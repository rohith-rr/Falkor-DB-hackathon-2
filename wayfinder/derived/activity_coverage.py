"""Coverage of successful, uncapped SIG Node activity imports."""
from datetime import datetime, timezone, timedelta

from ..models.common import now_iso

COVERAGE_ID = 'github:sig-node:coverage'
QUERY_VERSION = 'partitioned-issue-pr-v2'


def _date(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def record_activity_coverage(db, since, through):
    since = since or (datetime.now(timezone.utc) - timedelta(days=365)).date().isoformat()
    previous = db.query('MATCH (s:ActivityCoverage {id: $id}) RETURN s.covered_since, s.covered_through, s.query_version',
                        {'id': COVERAGE_ID})
    if previous.result_set:
        start, end, version = previous.result_set[0]
        if version == QUERY_VERSION and start and end and _date(since) <= _date(end):
            since = min(_date(start), _date(since)).isoformat()
            through = max(_date(end), _date(through)).isoformat()
    db.upsert_node('ActivityCoverage', {'id': COVERAGE_ID, 'covered_since': since,
                               'covered_through': through, 'query_version': QUERY_VERSION,
                               'observed_at': now_iso()})


def has_activity_coverage(db, cutoff):
    result = db.query('MATCH (s:ActivityCoverage {id: $id}) RETURN s.covered_since, s.covered_through, s.query_version',
                      {'id': COVERAGE_ID})
    if not result.result_set:
        return False
    start, end, version = result.result_set[0]
    if version != QUERY_VERSION or not start or not end:
        return False
    # A stale or partial import cannot prove absence of recent activity.
    return (_date(start) <= _date(cutoff) and
            _date(end) >= datetime.now(timezone.utc) - timedelta(days=1))
