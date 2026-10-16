"""Repair deterministic findings from saved data, without API or LLM calls.

Run: python -m wayfinder.repair
"""
import logging
import argparse
from collections import defaultdict

from .db import db_manager
from .models.common import now_iso
from .parsers.kep_parser import KepParser
from .derived.expertise import ExpertiseCalculator
from .derived.stale_findings import StaleDetector


def repair_saved_graph(db=None):
    db = db or db_manager
    sections = db.query('MATCH (k:KEP)-[has:HAS_SECTION]->(s:KEPSection) '
        'WHERE has.valid_to IS NULL AND s.valid_to IS NULL '
        'RETURN DISTINCT k.number, s.heading, s.text_content, s.source_url, s.source_ref, s.order '
        'ORDER BY k.number, s.order')
    grouped = defaultdict(list)
    for number, heading, text, url, ref, order in sections.result_set:
        grouped[str(number)].append((heading, text, url, ref))
    updated = retired = 0
    for number, saved in grouped.items():
        # Preserve comments spanning section boundaries from the old parser.
        content = '\n\n'.join(f'## {heading}\n{text or ""}' for heading, text, _, _ in saved)
        _, _, url, ref = saved[0]
        parsed = KepParser.parse_kep_readme(content, number,
            source_url=(url or '').split('#')[0], source_ref=(ref or '').split('#')[0])
        active = [r.id for r in parsed['rationales']]
        result = db.query('MATCH (n:Rationale) WHERE n.id STARTS WITH $prefix '
            'AND n.valid_to IS NULL AND NOT n.id IN $ids '
            'SET n.valid_to = $now WITH n OPTIONAL MATCH (n)-[r]-() '
            'WHERE r.valid_to IS NULL SET r.valid_to = $now RETURN count(DISTINCT n)',
            {'prefix': f'k8s:rationale:{number}:', 'ids': active, 'now': now_iso()})
        retired += result.result_set[0][0] if result.result_set else 0
        for rationale in parsed['rationales']:
            ident = rationale.id
            props = rationale.to_cypher_props()
            props['valid_to'] = None
            db.upsert_node('Rationale', props)
            for edge in parsed['edges']:
                if edge['target_id'] == ident:
                    db.upsert_edge(edge['source_label'], edge['source_id'], edge['target_label'],
                                   edge['target_id'], edge['rel_type'], edge['props'])
            updated += 1
    owners = ExpertiseCalculator(db).detect_paper_owners()
    stale = StaleDetector(db).check_inactive_owners()
    return {'rationales_updated': updated, 'rationales_retired': retired,
            'paper_owner_entries': len(owners), 'owner_inactive_findings': stale}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--derived-only', action='store_true', help='Recompute derived metrics and stale checks only')
    parser.add_argument('--expertise-only', action='store_true', help='Repair saved comments and refresh v2 directory expertise only; no API calls')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.expertise_only:
        from .derived.expertise_v2 import ExpertiseRefresh
        print(ExpertiseRefresh(db_manager).refresh(), flush=True)
    elif args.derived_only:
        from .sync.pipeline import PopulationPipeline
        pipeline = PopulationPipeline()
        print(pipeline.step6_compute_derived(), flush=True)
        print(pipeline.step7_run_stale_checks(), flush=True)
    else:
        print(repair_saved_graph())
