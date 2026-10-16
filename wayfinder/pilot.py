"""A bounded activity pilot using reference data already saved in the graph."""
import argparse
import logging
from datetime import datetime, timedelta, timezone

from .config import settings
from .github.client import GitHubClient
from .extraction.extractor import ExtractionPipeline
from .sync.pipeline import PopulationPipeline
from .models.common import compute_content_hash


class PilotGitHub(GitHubClient):
    def __init__(self, since, item_limit):
        super().__init__()
        self.since, self.item_limit = since, item_limit

    def iter_activity(self, since_date=None, page_size=10, limit=None):
        return super().iter_activity(since_date=since_date or self.since,
            page_size=min(page_size, self.item_limit),
            limit=min(limit, self.item_limit) if limit is not None else self.item_limit)


class PilotExtraction(ExtractionPipeline):
    def __init__(self, attempt_limit, since=None):
        # New prompt revision must not replay the old invalid-answer cache.
        # Keep this pilot-specific so reference sections are not re-extracted.
        super().__init__(extractor_version=f'{settings.extractor_version}-pilot-v3')
        self.attempt_limit, self.attempts = attempt_limit, 0
        self.since = datetime.fromisoformat(since.replace('Z', '+00:00')) if since else None

    def process_text_unit(self, unit_type, unit_id, unit_text, **kwargs):
        if unit_type not in ('Comment', 'Review') or self.attempts >= self.attempt_limit:
            return {'accepted': 0, 'rejected': 0, 'skipped': True}
        if self.since:
            timestamp = kwargs.get('source_timestamp')
            if not timestamp:
                return {'accepted': 0, 'rejected': 0, 'skipped': True}
            at = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if at < self.since:
                return {'accepted': 0, 'rejected': 0, 'skipped': True}
        text = (unit_text or '').strip()
        if len(text) < 20:
            return {'accepted': 0, 'rejected': 0, 'skipped': True}
        return super().process_text_unit(unit_type, unit_id, unit_text, **kwargs)

    def _before_extraction(self, unit_id):
        self.attempts += 1
        print(f'Extraction attempt {self.attempts}/{self.attempt_limit}: {unit_id}', flush=True)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--items', type=int, default=10)
    parser.add_argument('--extractions', type=int, default=20)
    args = parser.parse_args()
    if args.days <= 0 or args.items <= 0 or args.extractions < 0:
        parser.error('days and items must be positive; extractions must be nonnegative')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat(timespec='seconds')
    github = PilotGitHub(since, args.items)
    try:
        if not github.token:
            raise SystemExit('Set GITHUB_TOKEN in .env first.')
        pipeline = PopulationPipeline(github_client=github,
                                      extraction_pipeline=PilotExtraction(args.extractions, since))
        print(f'Graph: {settings.falkordb_graph}; activity since: {since}', flush=True)
        print(f'Importing up to {args.items} issues/PRs...', flush=True)
        result = pipeline.step2_ingest_activity(use_live_github=True, max_items=args.items)
        print(result, flush=True)
        if not result['processed_items']:
            raise SystemExit('No matching activity returned; check the date window.')
        if args.extractions:
            print(f'LLM: {settings.llm_model}; extraction attempt cap: {args.extractions}', flush=True)
            print(pipeline.step4_run_extraction(), flush=True)
            print({'extraction_diagnostics': pipeline.extractor.stats.model_dump()}, flush=True)
        print(pipeline.step6_compute_derived(), flush=True)
        print(pipeline.step7_run_stale_checks(), flush=True)
    finally:
        github.close()


if __name__ == '__main__':
    main()
