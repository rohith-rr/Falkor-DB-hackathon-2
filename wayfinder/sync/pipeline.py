import logging
from typing import Any, Dict, List, Optional

from ..models.common import now_iso
from ..db import DatabaseManager, db_manager
from ..derived.concerns import ConcernResolver
from ..derived.expertise import ExpertiseCalculator
from ..derived.graph_algos import GraphAlgorithms
from ..derived.stale_findings import StaleDetector
from ..extraction.extractor import ExtractionPipeline
from ..github.activity import ActivityProcessor
from ..github.client import GitHubClient
from ..parsers.doc_parser import DocParser
from ..parsers.gate_parser import GateParser
from ..parsers.kep_parser import KepParser
from ..parsers.owners_parser import OwnersParser
from ..parsers.sigs_parser import SigsParser
from .seed_data import (
    DOC_PAGE_RESIZE_SAMPLE,
    KEP_1287_README_SAMPLE,
    KEP_1287_YAML_SAMPLE,
    KUBELET_FEATURES_GO_SAMPLE,
    OWNERS_ALIASES_SAMPLE,
    OWNERS_KUBELET_SAMPLE,
    SAMPLE_GITHUB_ACTIVITY,
    SIGS_YAML_SAMPLE,
)

logger = logging.getLogger("wayfinder.sync.pipeline")

class PopulationPipeline:
    """7-step Population Pipeline for Wayfinder Company Brain on FalkorDB."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        github_client: Optional[GitHubClient] = None,
        extraction_pipeline: Optional[ExtractionPipeline] = None,
    ):
        self.db = db or db_manager
        self.github = github_client or GitHubClient()
        self.extractor = extraction_pipeline or ExtractionPipeline(db=self.db)

    def run_full_pipeline(self, use_live_github: bool = False, max_search_items: Optional[int] = None) -> Dict[str, Any]:
        """Run all 7 population steps in dependency order."""
        logger.info("==================================================")
        logger.info("STARTING WAYFINDER 7-STEP POPULATION PIPELINE")
        logger.info("==================================================")

        started = now_iso()
        results = {}

        # Step 5 (Early): Ensure indexes are initialized
        logger.info("[Step 5 Early] Initializing FalkorDB schema and indexes...")
        self.db.init_schema()

        # Step 1: Structure files
        logger.info("[Step 1] Parsing structured files (sigs.yaml, OWNERS, KEPs, gates, docs)...")
        step1_res = self.step1_ingest_structure_files(use_live_github=use_live_github)
        results["step1"] = step1_res

        # Step 2: Activity
        logger.info("[Step 2] Ingesting GitHub activity (Issues, PRs, reviews, comments)...")
        step2_res = self.step2_ingest_activity(use_live_github=use_live_github, max_items=max_search_items)
        results["step2"] = step2_res
        if use_live_github and max_search_items is None:
            from ..derived.activity_coverage import record_activity_coverage
            record_activity_coverage(self.db, None, started)

        # Step 3: Threads and references
        logger.info("[Step 3] Threads and references populated during step 2.")
        results["step3"] = {"status": "completed"}

        # Step 4: Extraction (Layer 2 Meaning)
        logger.info("[Step 4] Running Layer 2 semantic extraction and verification...")
        step4_res = self.step4_run_extraction()
        results["step4"] = step4_res

        # Step 6: Derived layer
        logger.info("[Step 6] Computing Layer 3 derived rules, expertise, and algorithms...")
        step6_res = self.step6_compute_derived()
        results["step6"] = step6_res

        # Step 7: Stale checks
        logger.info("[Step 7] Running stale checks and creating StaleFinding nodes...")
        step7_res = self.step7_run_stale_checks()
        results["step7"] = step7_res

        stats = self.db.get_stats()
        results["stats"] = stats

        logger.info("==================================================")
        logger.info(f"POPULATION PIPELINE COMPLETED! Total nodes: {stats['total_nodes']}, Total edges: {stats['total_edges']}")
        logger.info("==================================================")
        if use_live_github and max_search_items is None:
            self.db.upsert_node('SyncState', {'id': 'github:sig-node:activity', 'watermark': started, 'observed_at': now_iso()})
        return results

    def step1_ingest_structure_files(self, use_live_github: bool = False) -> Dict[str, Any]:
        """Step 1: Parse sigs.yaml, OWNERS, kep.yaml, feature gates, doc pages."""
        if use_live_github:
            from .structured import StructuredSourceSync
            return StructuredSourceSync(self.db, self.github).refresh()
        repo_k8s = "kubernetes/kubernetes"
        repo_enhancements = "kubernetes/enhancements"
        repo_community = "kubernetes/community"
        repo_website = "kubernetes/website"

        # 1. sigs.yaml
        sigs_yaml_content = SIGS_YAML_SAMPLE
        sigs_res = SigsParser.parse_sigs_yaml(sigs_yaml_content)
        for sig in sigs_res["sigs"]:
            self.db.upsert_node("SIG", sig.to_cypher_props())
        for person in sigs_res["people"]:
            self.db.upsert_node("Person", person.to_cypher_props())
        for sp in sigs_res["subprojects"]:
            self.db.upsert_node("Subproject", sp.to_cypher_props())
        for edge in sigs_res["edges"]:
            self.db.upsert_edge(
                edge["source_label"], edge["source_id"],
                edge["target_label"], edge["target_id"],
                edge["rel_type"], edge["props"]
            )

        # 2. OWNERS & OWNERS_ALIASES
        aliases_map = OwnersParser.parse_owners_aliases(OWNERS_ALIASES_SAMPLE)
        owners_res = OwnersParser.parse_owners_file(
            content=OWNERS_KUBELET_SAMPLE,
            repo=repo_k8s,
            path="pkg/kubelet/OWNERS",
            aliases_map=aliases_map,
        )
        self.db.upsert_node("OwnersFile", owners_res["owners_node"].to_cypher_props())
        for d in owners_res["directories"]:
            self.db.upsert_node("Directory", d.to_cypher_props())
        for p in owners_res["people"]:
            self.db.upsert_node("Person", p.to_cypher_props())
        for edge in owners_res["edges"]:
            self.db.upsert_edge(
                edge["source_label"], edge["source_id"],
                edge["target_label"], edge["target_id"],
                edge["rel_type"], edge["props"]
            )

        # 3. KEP-1287 metadata & README
        kep_res = KepParser.parse_kep_yaml(KEP_1287_YAML_SAMPLE)
        self.db.upsert_node("KEP", kep_res["kep"].to_cypher_props())
        for person in kep_res.get("people", []):
            self.db.upsert_node("Person", person.to_cypher_props())
        for gate in kep_res["gates"]:
            self.db.upsert_node("FeatureGate", gate.to_cypher_props())
        for dec in kep_res.get("decisions", []):
            self.db.upsert_node("Decision", dec.to_cypher_props())
        for edge in kep_res["edges"]:
            self.db.upsert_edge(
                edge["source_label"], edge["source_id"],
                edge["target_label"], edge["target_id"],
                edge["rel_type"], edge["props"]
            )

        kep_readme_res = KepParser.parse_kep_readme(KEP_1287_README_SAMPLE, "1287")
        for sec in kep_readme_res["sections"]:
            self.db.upsert_node("KEPSection", sec.to_cypher_props())
        for rat in kep_readme_res.get("rationales", []):
            self.db.upsert_node("Rationale", rat.to_cypher_props())
        for edge in kep_readme_res["edges"]:
            self.db.upsert_edge(
                edge["source_label"], edge["source_id"],
                edge["target_label"], edge["target_id"],
                edge["rel_type"], edge["props"]
            )

        # 4. Feature Gate definitions
        gate_res = GateParser.parse_feature_gates_go(
            content=KUBELET_FEATURES_GO_SAMPLE,
            repo=repo_k8s,
            file_path="pkg/features/kubelet_features.go",
            release_version="v1.30",
        )
        self.db.upsert_node("File", gate_res["file"].to_cypher_props())
        for r in gate_res["releases"]:
            self.db.upsert_node("Release", r.to_cypher_props())
        for g in gate_res["gates"]:
            self.db.upsert_node("FeatureGate", g.to_cypher_props())
        for edge in gate_res["edges"]:
            self.db.upsert_edge(
                edge["source_label"], edge["source_id"],
                edge["target_label"], edge["target_id"],
                edge["rel_type"], edge["props"]
            )

        # 5. Doc Page
        doc_res = DocParser.parse_markdown_doc(
            content=DOC_PAGE_RESIZE_SAMPLE,
            path="tasks/configure-pod-container/resize-container-resources.md",
            known_gates={"InPlacePodVerticalScaling", "MemoryQoS"},
        )
        self.db.upsert_node("DocPage", doc_res["doc_page"].to_cypher_props())
        for s in doc_res["sections"]:
            self.db.upsert_node("DocSection", s.to_cypher_props())
        for edge in doc_res["edges"]:
            self.db.upsert_edge(
                edge["source_label"], edge["source_id"],
                edge["target_label"], edge["target_id"],
                edge["rel_type"], edge["props"]
            )

        # Governance endpoints are available after OWNERS ingestion.
        for edge in sigs_res['edges']:
            if edge['rel_type'] == 'GOVERNED_BY':
                self.db.upsert_edge(edge['source_label'], edge['source_id'], edge['target_label'], edge['target_id'], edge['rel_type'], edge['props'])
        return {"status": "success", "sigs": len(sigs_res["sigs"]), "keps": 1}

    def step2_ingest_activity(self, use_live_github: bool = False, max_items: Optional[int] = None) -> Dict[str, Any]:
        """Step 2 & 3: GitHub issues, PRs, comments, reviews, Prow commands, references."""
        raw_items = list(self.github.iter_activity(limit=max_items)) if use_live_github else SAMPLE_GITHUB_ACTIVITY
        processed_batch = [ActivityProcessor.process_github_node(raw) for raw in raw_items]
        total_nodes = total_edges = 0
        for processed in processed_batch:
            for nodelist in processed['nodes'].values():
                for node in nodelist:
                    self.db.upsert_node(node.__class__.__name__, node.to_cypher_props())
                    total_nodes += 1
        for processed in processed_batch:
            for edge in processed['edges']:
                write_activity_edge(self.db, edge)
                total_edges += 1
        from .incremental import apply_prow_cancellations
        apply_prow_cancellations(self.db)
        for raw in raw_items:
            if raw.get('__typename')=='PullRequest' and 'reviewThreads' in raw:
                from ..derived.expertise_backfill import record_review_coverage
                repo='/'.join(raw['url'].split('/')[3:5])
                record_review_coverage(self.db,repo,raw['number'],raw['reviewThreads'])
            if raw.get('__typename')=='PullRequest' and raw.get('files',{}).get('pageInfo',{}).get('hasNextPage') is False:
                repo='/'.join(raw['url'].split('/')[3:5])
                pid=f"k8s:pr:{repo}:{raw['number']}"
                file_ids=[f"k8s:file:{repo}:{f['path']}" for f in raw['files'].get('nodes',[])]
                self.db.query('MATCH (pr:PullRequest {id:$pr})-[r:TOUCHES]->(f:File) '
                    'WHERE r.valid_to IS NULL AND NOT f.id IN $files SET r.valid_to=$now',
                    {'pr':pid,'files':file_ids,'now':now_iso()})
        return {'processed_items': len(raw_items), 'nodes_upserted': total_nodes, 'edges_upserted': total_edges}

    def step4_run_extraction(self) -> Dict[str, Any]:
        """Step 4: Layer 2 semantic extraction over non-bot comments and KEP sections."""
        units = []
        for label, field, author_rel, root_pattern in [
            ('Comment', 'body_text', 'AUTHORED', 'OPTIONAL MATCH (t:Thread)-[:HAS_COMMENT]->(u) OPTIONAL MATCH (t)-[:ON]->(root)'),
            ('Review', 'body_text', 'SUBMITTED', 'OPTIONAL MATCH (u)-[:REVIEWS]->(root)'),
            ('KEPSection', 'text_content', None, 'OPTIONAL MATCH (root:KEP)-[:HAS_SECTION]->(u)'),
            ('DocSection', 'text_content', None, 'OPTIONAL MATCH (root:DocPage)-[:HAS_SECTION]->(u)'),
        ]:
            author = f'OPTIONAL MATCH (p:Person)-[:{author_rel}]->(u)' if author_rel else ''
            bot = 'AND coalesce(p.is_bot, false) = false' if author_rel else ''
            command = 'AND coalesce(u.is_command, false) = false' if label == 'Comment' else ''
            result = self.db.query(f'MATCH (u:{label}) {author} {root_pattern} '
                f'WITH u, root' + (', p ' if author_rel else ' ') +
                f'WHERE u.valid_to IS NULL AND size(u.{field}) >= 20 {bot} {command} '
                f'RETURN DISTINCT u.id, u.{field}, ' + ('p.id' if author_rel else 'null') +
                ', root.id, root.title, u.source_url, u.source_ref, coalesce(u.created_at, u.submitted_at, u.source_updated_at, u.valid_from) '
                'ORDER BY coalesce(u.created_at, u.submitted_at, u.valid_from), u.id')
            units.extend((label, row) for row in result.result_set)
        accepted = rejected = 0
        for label, row in units:
            ident, text, author, root, title, url, ref, timestamp = row
            ids = [root] if root else []
            context = []
            if label in ('Comment', 'Review') and root:
                previous = self.db.query('MATCH (t:Thread)-[:ON]->(root {id: $root}) '
                    'MATCH (t)-[:HAS_COMMENT]->(c:Comment) WHERE c.created_at < $at '
                    'RETURN c.id, c.body_text ORDER BY c.created_at DESC LIMIT 2', {'root': root, 'at': timestamp})
                for cid, body in reversed(previous.result_set):
                    ids.append(cid)
                    context.append(f'{cid}: {body}')
                semantics = self.db.query('MATCH (t:Thread)-[:ON]->(root {id: $root}) '
                    'MATCH (t)-[:HAS_COMMENT]->(c:Comment)-[r:ASSERTS]->(s) '
                    'WHERE c.created_at < $at AND r.valid_to IS NULL AND s.valid_to IS NULL '
                    'RETURN s.id, s.summary, s.target_id', {'root': root, 'at': timestamp})
                for sid, summary, target in semantics.result_set:
                    ids.append(sid)
                    if target:
                        ids.append(target)
                    context.append(f'{sid} target={target}: {summary}')
            result = self.extractor.process_text_unit(label, ident, text, author_id=author,
                context_target_ids=ids, thread_title=title, prev_comments_context='\n'.join(context),
                source_url=url, source_ref=ref, source_timestamp=timestamp,
                parent_kep_id=root if label == 'KEPSection' else None)
            if result.get('error'):
                raise RuntimeError(f'Extraction failed for {ident}: {result["error"]}')
            accepted += result.get('accepted', 0)
            rejected += result.get('rejected', 0)
        return {'units_examined': len(units), 'items_accepted': accepted, 'items_rejected': rejected,
                'rejection_rate': self.extractor.stats.rejection_rate}

    def step6_compute_derived(self) -> Dict[str, Any]:
        """Step 6: Compute Layer 3 expertise, paper owners, unlisted experts, PageRank, communities."""
        exp_calc = ExpertiseCalculator(db=self.db)
        dir_exp = exp_calc.calculate_directory_expertise()
        topic_exp = exp_calc.calculate_topic_expertise()
        paper_owners = exp_calc.detect_paper_owners()
        unlisted_experts = exp_calc.detect_unlisted_experts()

        algos = GraphAlgorithms(db=self.db)
        working_rels = algos.compute_working_relationships()
        communities = algos.compute_communities()
        pagerank = algos.compute_pagerank()
        centrality = algos.compute_review_centrality()

        resolver = ConcernResolver(db=self.db)
        concern_statuses = resolver.resolve_concern_statuses()

        return {
            "directory_expertise": dir_exp,
            "topic_expertise": topic_exp,
            "paper_owners": len(paper_owners),
            "unlisted_experts": len(unlisted_experts),
            "working_relationships": working_rels,
            "communities": communities,
            "pagerank_updated": pagerank,
            "centrality_updated": centrality,
            "concerns": concern_statuses,
        }

    def step7_run_stale_checks(self) -> Dict[str, int]:
        """Step 7: Detect stale docs, inactive owners, KEP status mismatch, unanswered questions."""
        detector = StaleDetector(db=self.db)
        return detector.run_all_checks()


def write_activity_edge(db, edge):
    target = edge['target_id']
    props = dict(edge['props'])
    if edge.get('target_url'):
        found = db.query('MATCH (n:Comment {source_url: $url}) RETURN n.id', {'url': edge['target_url']})
        if not found.result_set:
            return
        target = found.result_set[0][0]
        props['id'] = f"{edge['source_id']}->{edge['rel_type']}->{target}"
    db.upsert_edge(edge['source_label'], edge['source_id'], edge['target_label'], target, edge['rel_type'], props)
