import logging
from ..db import db_manager
from ..github.client import GitHubClient
from ..github.activity import ActivityProcessor
from ..extraction.extractor import ExtractionPipeline
from ..models.common import now_iso
from .pipeline import PopulationPipeline, write_activity_edge
from .structured import StructuredSourceSync
from ..derived.activity_coverage import record_activity_coverage

logger = logging.getLogger('wayfinder.sync.incremental')


def apply_prow_cancellations(db):
    count = 0
    for command, relationship in [('approve cancel', 'APPROVED'), ('lgtm cancel', 'LGTM')]:
        result = db.query(f'MATCH (p:Person)-[:AUTHORED]->(c:Comment)<-[:HAS_COMMENT]-(:Thread)-[:ON]->(pr:PullRequest) '
            f'MATCH (p)-[r:{relationship}]->(pr) WHERE $command IN c.commands AND r.valid_from < c.created_at '
            'AND (r.valid_to IS NULL OR r.valid_to > c.created_at) SET r.valid_to = c.created_at RETURN count(r)', {'command': command})
        count += result.result_set[0][0] if result.result_set else 0
    return count


class IncrementalSync:
    def __init__(self, db=None, github_client=None, extractor=None):
        self.db = db or db_manager
        self.github = github_client or GitHubClient()
        self.extractor = extractor or ExtractionPipeline(db=self.db)

    def sync_once(self, since_date=None):
        started = now_iso()
        cursor_id = 'github:sig-node:activity'
        if not since_date:
            cursor = self.db.query('MATCH (s:SyncState {id: $id}) RETURN s.watermark', {'id': cursor_id})
            since_date = cursor.result_set[0][0] if cursor.result_set else None
        stats = {'updated_nodes': 0, 'closed_edges': 0, 'recomputed_concerns': 0, 'recomputed_expertise': 0}
        if self.github.token:
            # A failure must propagate: advancing the watermark would lose updates.
            stats['structure'] = StructuredSourceSync(self.db, self.github).refresh()
            batch = [ActivityProcessor.process_github_node(raw) for raw in self.github.iter_activity(since_date=since_date)]
            for processed in batch:
                for nodes in processed['nodes'].values():
                    for node in nodes:
                        self.db.upsert_node(node.__class__.__name__, node.to_cypher_props())
                        stats['updated_nodes'] += 1
            for processed in batch:
                for edge in processed['edges']:
                    write_activity_edge(self.db, edge)
            # GitHub search exposes at most 1,000 results; do not claim full
            # coverage when the search may have reached that ceiling.
            # iter_activity partitions search windows and raises unless every window is exhausted.
            record_activity_coverage(self.db, since_date, started)
        stats['closed_edges'] = apply_prow_cancellations(self.db)
        pipeline = PopulationPipeline(db=self.db, github_client=self.github, extraction_pipeline=self.extractor)
        # Extraction markers skip unchanged materialized units; edits retire the
        # old interpretation even if they now contain only commands/short text.
        self.db.query("MATCH (u)-[r:ASSERTS]->(s) WHERE r.authority = 'EXTRACTED' AND r.valid_to IS NULL "
                      "AND (u.is_command = true OR size(coalesce(u.body_text, u.text_content, '')) < 20) "
                      'SET r.valid_to = $now, s.valid_to = $now, u.extraction_version = null WITH s MATCH (s)-[e]-() '
                      'WHERE e.valid_to IS NULL SET e.valid_to = $now', {'now': started})
        self.db.query("MATCH (u)-[r:ABOUT_TOPIC|ANSWERS]->() WHERE r.authority = 'EXTRACTED' AND r.valid_to IS NULL "
                      "AND (u.is_command = true OR size(coalesce(u.body_text, u.text_content, '')) < 20) "
                      'SET r.valid_to = $now, u.extraction_version = null', {'now': started})
        stats['extraction'] = pipeline.step4_run_extraction()
        stats['derived'] = pipeline.step6_compute_derived()
        stats['recomputed_concerns'] = sum(stats['derived']['concerns'].values())
        stats['recomputed_expertise'] = 1
        stats['stale'] = pipeline.step7_run_stale_checks()
        if self.github.token:
            self.db.upsert_node('SyncState', {'id': cursor_id, 'watermark': started, 'observed_at': now_iso()})
        return stats
