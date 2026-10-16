from collections import defaultdict
from datetime import datetime, timezone, timedelta
import uuid
from ..config import settings
import logging
from typing import Any, Dict, List, Optional
from ..db import DatabaseManager, db_manager
from ..models.common import Authority, LinkMethod, now_iso
from ..models.layer3 import Community

logger = logging.getLogger("wayfinder.derived.graph_algos")

class GraphAlgorithms:
    """Computes Layer 3 graph projections, FalkorDB algorithms (PageRank, betweenness, WCC/CDLP)."""

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or db_manager

    def compute_working_relationships(self) -> int:
        """
        Computes (Person)-[:WORKS_WITH {weight}]->(Person) based on count of threads
        where both people commented or reviewed in the last 180 days.
        """
        now = now_iso()
        logger.info("Computing WORKS_WITH relationships between collaborators...")

        cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.expertise_window_days)).isoformat()
        participants = self.db.query("MATCH (p:Person)-[:AUTHORED]->(c:Comment)<-[:HAS_COMMENT]-(t:Thread) "
            "WHERE coalesce(p.is_bot, false) = false AND c.created_at >= $cutoff RETURN p.id AS person_id, t.root_ref AS root_id "
            "UNION MATCH (p:Person)-[:SUBMITTED]->(r:Review)-[:REVIEWS]->(pr:PullRequest) "
            "WHERE coalesce(p.is_bot, false) = false AND r.submitted_at >= $cutoff RETURN p.id AS person_id, pr.id AS root_id", {'cutoff': cutoff})
        groups = defaultdict(set)
        for person, thread in participants.result_set:
            groups[thread].add(person)
        weights = defaultdict(int)
        for people in groups.values():
            ordered = sorted(people)
            for i, first in enumerate(ordered):
                for second in ordered[i + 1:]:
                    weights[(first, second)] += 1
        count = 0
        try:
            for (p1_id, p2_id), weight in weights.items():
                if weight > 0:
                    edge_id = f"{p1_id}->WORKS_WITH->{p2_id}"
                    self.db.upsert_edge(
                        source_label="Person",
                        source_id=p1_id,
                        target_label="Person",
                        target_id=p2_id,
                        rel_type="WORKS_WITH",
                        props={
                            "id": edge_id,
                            "weight": weight,
                            "link_method": LinkMethod.COMPUTED.value,
                            "authority": Authority.COMPUTED.value,
                            "observed_at": now,
                            "valid_from": now,
                        },
                    )
                    count += 1
        except Exception as e:
            logger.warning(f"Error computing WORKS_WITH: {e}")

        self.db.query('MATCH ()-[r:WORKS_WITH]->() WHERE r.valid_to IS NULL AND NOT coalesce(r.logical_id, r.id) IN $ids SET r.valid_to = $now',
                      {'ids': [f'{a}->WORKS_WITH->{b}' for a, b in weights], 'now': now})
        logger.info(f"Created {count} WORKS_WITH edges.")
        return count

    def compute_communities(self) -> int:
        """
        Executes FalkorDB algo.wcc or connected components on WORKS_WITH projection
        to group people into real working groups (:Community).
        """
        now = now_iso()
        logger.info("Running community detection on WORKS_WITH...")

        communities: Dict[int, List[str]] = defaultdict(list)
        try:
            res = self._run_projection_algorithm("WORKS_WITH", "CALL algo.wcc({nodeLabels: ['Person'], relationshipTypes: ['WORKS_WITH']})")
            for row in res.result_set:
                node_obj, comp_id = row[0], row[1]
                # node_obj has properties or id
                p_id = getattr(node_obj, "properties", {}).get("id") or str(getattr(node_obj, "id", ""))
                if p_id and "person:" in p_id:
                    communities[int(comp_id)].append(p_id)
        except Exception as e:
            logger.warning(f"algo.wcc call notice: {e}. Falling back to Cypher component grouping.")
            # Fallback Cypher grouping
            res = self.db.query("""
            MATCH (p1:Person)-[r:WORKS_WITH]->(p2:Person)
            WHERE r.valid_to IS NULL
            RETURN p1.id, p2.id
            """)
            comp_idx = 0
            visited = set()
            adj = defaultdict(set)
            for row in res.result_set:
                adj[row[0]].add(row[1])
                adj[row[1]].add(row[0])
            for node in adj:
                if node not in visited:
                    comp_idx += 1
                    queue = [node]
                    visited.add(node)
                    while queue:
                        curr = queue.pop(0)
                        communities[comp_idx].append(curr)
                        for neighbor in adj[curr]:
                            if neighbor not in visited:
                                visited.add(neighbor)
                                queue.append(neighbor)

        self.db.query('MATCH ()-[r:IN_COMMUNITY]->() WHERE r.valid_to IS NULL SET r.valid_to = $now', {'now': now})
        # Write Community nodes and IN_COMMUNITY edges
        created_count = 0
        for comp_id, members in communities.items():
            if len(members) < 2:
                continue
            comm_node_id = f"k8s:community:{comp_id}"
            comm_node = Community(
                id=comm_node_id,
                community_id=comp_id,
                size=len(members),
                source_system="derived",
                source_url="derived:community",
                source_ref=f"community:{comp_id}",
                observed_at=now,
                valid_from=now,
            )
            self.db.upsert_node("Community", comm_node.to_cypher_props())

            for m_id in members:
                self.db.upsert_edge(
                    source_label="Person",
                    source_id=m_id,
                    target_label="Community",
                    target_id=comm_node_id,
                    rel_type="IN_COMMUNITY",
                    props={
                        "id": f"{m_id}->IN_COMMUNITY->{comm_node_id}",
                        "link_method": LinkMethod.COMPUTED.value,
                        "authority": Authority.COMPUTED.value,
                        "observed_at": now,
                        "valid_from": now,
                    },
                )
            created_count += 1

        logger.info(f"Identified {created_count} working group communities.")
        return created_count

    def compute_pagerank(self) -> int:
        """
        Runs FalkorDB algo.pageRank over REFERENCES and DOCUMENTS edges
        to compute importance scores on KEP and DocPage nodes.
        """
        logger.info("Computing PageRank for KEP and DocPage nodes...")
        updated = 0
        try:
            res = self.db.query("CALL algo.pageRank('KEP', 'REFERENCES')")
            for row in res.result_set:
                node_obj, score = row[0], float(row[1])
                node_id = getattr(node_obj, "properties", {}).get("id")
                if node_id:
                    self.db.query(
                        "MATCH (k:KEP {id: $id}) SET k.importance = $score",
                        {"id": node_id, "score": round(score, 6)},
                    )
                    updated += 1
        except Exception as e:
            logger.warning(f"algo.pageRank for KEP notice: {e}")

        try:
            res = self.db.query("CALL algo.pageRank('DocPage', 'DOCUMENTS')")
            for row in res.result_set:
                node_obj, score = row[0], float(row[1])
                node_id = getattr(node_obj, "properties", {}).get("id")
                if node_id:
                    self.db.query(
                        "MATCH (d:DocPage {id: $id}) SET d.importance = $score",
                        {"id": node_id, "score": round(score, 6)},
                    )
                    updated += 1
        except Exception as e:
            logger.warning(f"algo.pageRank for DocPage notice: {e}")

        return updated

    def compute_review_centrality(self) -> int:
        """
        Runs betweenness centrality on reviewer-to-author network to find review bottlenecks.
        Sets review_centrality on Person nodes.
        """
        logger.info("Computing review bottleneck betweenness centrality...")
        updated = 0
        now = now_iso()
        reviews = self.db.query('MATCH (reviewer:Person)-[:SUBMITTED]->(:Review)-[:REVIEWS]->(pr:PullRequest)<-[:AUTHORED]-(author:Person) '
                                'WHERE reviewer.id <> author.id RETURN DISTINCT reviewer.id, author.id')
        active = []
        for reviewer, author in reviews.result_set:
            eid = f'{reviewer}->REVIEWS_FOR->{author}'
            active.append(eid)
            self.db.upsert_edge('Person', reviewer, 'Person', author, 'REVIEWS_FOR',
                                {'id': eid, 'valid_from': now, 'observed_at': now, 'authority': 'COMPUTED', 'link_method': 'COMPUTED'})
        self.db.query('MATCH ()-[r:REVIEWS_FOR]->() WHERE r.valid_to IS NULL AND NOT coalesce(r.logical_id, r.id) IN $ids SET r.valid_to = $now', {'ids': active, 'now': now})
        self.db.query('MATCH (p:Person) SET p.review_centrality = 0')
        try:
            res = self._run_projection_algorithm('REVIEWS_FOR', "CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['REVIEWS_FOR']})")
            for row in res.result_set:
                node_obj, score = row[0], float(row[1])
                node_id = getattr(node_obj, "properties", {}).get("id")
                if node_id and "person:" in node_id:
                    self.db.query(
                        "MATCH (p:Person {id: $id}) SET p.review_centrality = $score",
                        {"id": node_id, "score": round(score, 6)},
                    )
                    updated += 1
        except Exception as e:
            logger.warning(f"algo.betweenness notice: {e}")

        return updated

    def _run_projection_algorithm(self, relationship, procedure):
        # Procedures filter by type but cannot filter valid_to. A disposable
        # snapshot keeps historical intervals out of the algorithm input.
        edges = self.db.query(f'MATCH (a:Person)-[r:{relationship}]->(b:Person) WHERE r.valid_to IS NULL RETURN DISTINCT a.id, b.id')
        projection = DatabaseManager(host=self.db.host, port=self.db.port,
                                     graph_name=f'{self.db.graph_name}_projection_{uuid.uuid4().hex}')
        try:
            for a, b in edges.result_set:
                projection.query(f'MERGE (a:Person {{id: $a}}) MERGE (b:Person {{id: $b}}) MERGE (a)-[:{relationship}]->(b)', {'a': a, 'b': b})
            return projection.query(procedure)
        finally:
            projection.clear_graph()
