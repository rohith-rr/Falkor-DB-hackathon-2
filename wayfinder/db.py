import logging
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple
from falkordb import FalkorDB, Graph
from .config import settings

logger = logging.getLogger("wayfinder.db")

NODE_LABELS_FOR_INDEX = [
    "Organization", "Team", "Project", "Component", "SourceRevision", "RawSourceRevision", "SourceCoverage", "Attachment", "SourceLink", "ConcernStatusRevision",
    "Person",
    "SIG",
    "Subproject",
    "Repository",
    "Directory",
    "File",
    "OwnersFile",
    "KEP",
    "KEPSection",
    "FeatureGate",
    "Release",
    "Issue",
    "PullRequest",
    "Review",
    "Comment",
    "Thread",
    "DocPage",
    "DocSection",
    "Topic",
    "Argument",
    "Decision",
    "Rationale",
    "Question",
    "Community",
    "StaleFinding",
    "Action",
    "SyncState",
    "SyncSource",
    "ActivityCoverage",
    "ExpertiseState",
    "ExpertiseSnapshot",
    "ExpertiseContribution",
    "ReviewThreadCoverage",
]

class DatabaseManager:
    """Manages FalkorDB connection, schema initialization, and Cypher executions."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        graph_name: Optional[str] = None,
    ):
        self.host = host or settings.falkordb_host
        self.port = port or settings.falkordb_port
        self.graph_name = graph_name or settings.falkordb_graph
        self._client: Optional[FalkorDB] = None
        self._graph: Optional[Graph] = None

    @property
    def client(self) -> FalkorDB:
        if self._client is None:
            self._client = FalkorDB(host=self.host, port=self.port)
        return self._client

    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = self.client.select_graph(self.graph_name)
        return self._graph

    def ping(self) -> bool:
        """Ping the database to verify connectivity."""
        try:
            return bool(self.client.connection.ping())
        except Exception as e:
            logger.error(f"Failed to ping FalkorDB: {e}")
            return False

    def query(self, cypher: str, params: Optional[Dict[str, Any]] = None):
        """Execute a Cypher query with optional parameters."""
        return self.graph.query(cypher, params=params)

    def init_schema(self, enable_vectors: bool = True):
        """Initialize all schema indexes (range/exact, full-text, vector)."""
        logger.info(f"Initializing schema for graph '{self.graph_name}'...")
        
        # 1. Range/Exact index on `id` for all node labels
        for label in NODE_LABELS_FOR_INDEX:
            try:
                self.query(f"CREATE INDEX FOR (n:{label}) ON (n.id)")
                logger.debug(f"Created index on :{label}(id)")
            except Exception as e:
                # If index already exists or error, ignore
                if "already exists" not in str(e).lower() and "indexed" not in str(e).lower():
                    logger.warning(f"Index creation note for :{label}(id): {e}")

        # 2. Vector indexes for semantic retrieval
        if enable_vectors:
            vector_labels = [
                ("Topic", "embedding"),
                ("Argument", "embedding"),
                ("Comment", "embedding"),
            ]
            for label, prop in vector_labels:
                try:
                    q = (
                        f"CREATE VECTOR INDEX FOR (m:{label}) ON (m.{prop}) "
                        f"OPTIONS {{dimension: {settings.embedding_dim}, similarityFunction: 'cosine'}}"
                    )
                    self.query(q)
                    logger.info(f"Created vector index for :{label}({prop})")
                except Exception as e:
                    if "already exists" not in str(e).lower():
                        logger.warning(f"Vector index note for :{label}({prop}): {e}")

        # 3. Fulltext indexes for text search
        fulltext_targets = [
            ("Comment", "body_text"),
            ("KEP", "title"),
            ("Issue", "title"),
            ("PullRequest", "title"),
        ]
        for label, prop in fulltext_targets:
            try:
                self.query(f"CALL db.idx.fulltext.createNodeIndex('{label}', '{prop}')")
                logger.info(f"Created fulltext index for :{label}({prop})")
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.warning(f"Fulltext index note for :{label}({prop}): {e}")

        logger.info("Schema initialization complete.")

    def upsert_node(self, label: str, props: Dict[str, Any]):
        """Upsert a single node by its stable id."""
        node_id = props.get("id")
        if not node_id:
            raise ValueError("Node properties must contain 'id'")
        source_text=props.get('body_text',props.get('text_content'))
        if source_text is not None and props.get('source_system')!='llm-extraction' and label!='SourceRevision':
            # Invalidate claims at the source-edit boundary, independently of LLM availability.
            from .models.common import now_iso
            props=dict(props)
            revision=props.get('source_revision_id') or f"source-revision:{node_id}:{hashlib.sha256(source_text.encode()).hexdigest()}"
            self.query('MERGE (revision:SourceRevision {id:$id}) ON CREATE SET revision += $props',
                {'id':revision,'props':{'source_unit_id':node_id,'text_content':source_text,
                    'source_url':props.get('source_url'),'source_ref':props.get('source_ref'),
                    'source_updated_at':props.get('source_updated_at'),'observed_at':now_iso()}})
            props['source_revision_id']=revision
            self.query(f'MATCH (u:{label} {{id:$id}}) WHERE coalesce(u.body_text,u.text_content) IS NOT NULL '
                'AND coalesce(u.body_text,u.text_content)<>$text '
                "OPTIONAL MATCH (u)-[r:ASSERTS]->(claim) WHERE r.authority='EXTRACTED' AND r.valid_to IS NULL "
                'SET u.semantic_stale=true,u.extraction_version=null,r.valid_to=$now,claim.valid_to=$now '
                'WITH claim OPTIONAL MATCH (claim)-[e]-() WHERE e.valid_to IS NULL SET e.valid_to=$now',
                {'id':node_id,'text':source_text,'now':now_iso()})
        
        props = dict(props)
        embedding = props.pop("embedding", None)
        vector_set = "SET n.embedding = vecf32($embedding)" if embedding is not None else ""
        cypher = f"""
        MERGE (n:{label} {{id: $node_id}})
        ON CREATE SET n = $props
        ON MATCH SET n += $props
        {vector_set}
        RETURN n.id
        """
        return self.query(cypher, {"node_id": node_id, "props": props, "embedding": embedding})

    def upsert_edge(
        self,
        source_label: str,
        source_id: str,
        target_label: str,
        target_id: str,
        rel_type: str,
        props: Dict[str, Any],
    ):
        """Upsert a directed relationship between two nodes by their ids."""
        props = dict(props)
        edge_id = props.get("id")
        if not edge_id:
            edge_id = f"{source_id}->{rel_type}->{target_id}"
            props["id"] = edge_id

        # The logical ID identifies a relationship, not one of its intervals.
        # Compare meaningful properties so repeated ingestion preserves valid_from.
        ignored = {"id", "observed_at", "valid_from", "valid_to", "paper_owner"}
        signature = hashlib.sha256(json.dumps(
            {k: v for k, v in props.items() if k not in ignored}, sort_keys=True
        ).encode()).hexdigest()
        existing = self.query(
            f"MATCH (s:{source_label} {{id: $sid}})-[r:{rel_type}]->(t:{target_label} {{id: $tid}}) "
            "WHERE r.logical_id = $id OR r.id = $id RETURN r",
            {"sid": source_id, "tid": target_id, "id": edge_id},
        )
        for row in existing.result_set:
            old = row[0].properties
            if old.get("valid_to") is None and old.get("signature") == signature:
                return existing
            # Replayed source events must not reopen a withdrawn interval.
            if old.get("signature") == signature and old.get("valid_from") == props.get("valid_from"):
                return existing
        start = props.get("valid_from") or props.get("observed_at")
        # Source events (approvals) keep event time. Changes to an existing
        # structural relationship take effect when the change was observed.
        if existing.result_set and rel_type not in {"APPROVED", "LGTM"}:
            start = props.get("observed_at") or start
        if not start:
            from .models.common import now_iso
            start = now_iso()
        self.query(
            f"MATCH ()-[r:{rel_type}]->() WHERE (r.logical_id = $id OR r.id = $id) "
            "AND r.valid_to IS NULL AND r.valid_from <= $start SET r.valid_to = $start",
            {"id": edge_id, "start": start},
        )
        props.update(logical_id=edge_id, signature=signature, valid_from=start)
        edge_id = edge_id + "@" + hashlib.sha256((start + signature).encode()).hexdigest()[:20]
        props["id"] = edge_id
        cypher = f"""
        MATCH (s:{source_label} {{id: $source_id}})
        MATCH (t:{target_label} {{id: $target_id}})
        MERGE (s)-[r:{rel_type} {{id: $edge_id}}]->(t)
        ON CREATE SET r = $props
        ON MATCH SET r += $props
        RETURN r.id
        """
        return self.query(
            cypher,
            {
                "source_id": source_id,
                "target_id": target_id,
                "edge_id": edge_id,
                "props": props,
            },
        )

    def batch_upsert_nodes(self, label: str, nodes_props: List[Dict[str, Any]]):
        """Batch upsert multiple nodes of the same label."""
        if not nodes_props:
            return
        if any("embedding" in props for props in nodes_props):
            for props in nodes_props:
                self.upsert_node(label, props)
            return
        cypher = f"""
        UNWIND $batch AS item
        MERGE (n:{label} {{id: item.id}})
        ON CREATE SET n = item
        ON MATCH SET n += item
        """
        self.query(cypher, {"batch": nodes_props})

    def get_stats(self) -> Dict[str, Any]:
        """Retrieve total counts of nodes by label and edges by relationship type."""
        node_counts = {}
        for label in NODE_LABELS_FOR_INDEX:
            try:
                res = self.query(f"MATCH (n:{label}) RETURN count(n) AS cnt")
                cnt = res.result_set[0][0] if res.result_set else 0
                if cnt > 0:
                    node_counts[label] = cnt
            except Exception:
                pass

        edge_counts = {}
        try:
            res = self.query("MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS cnt")
            for row in res.result_set:
                edge_counts[row[0]] = row[1]
        except Exception:
            pass

        return {
            "graph": self.graph_name,
            "node_counts": node_counts,
            "edge_counts": edge_counts,
            "total_nodes": sum(node_counts.values()),
            "total_edges": sum(edge_counts.values()),
        }

    def clear_graph(self):
        """Clear all nodes and relationships in the graph (useful for test runs)."""
        try:
            self.graph.delete()
            self._graph = None
            logger.info(f"Graph '{self.graph_name}' deleted.")
        except Exception as e:
            logger.warning(f"Could not delete graph '{self.graph_name}': {e}")

db_manager = DatabaseManager()
