"""The committed live-connector snapshot rebuilds the graph offline and carries no account identifiers."""
import json
import re
import uuid
import pytest
from wayfinder.db import DatabaseManager
from wayfinder.company.replay import FIXTURE,DROP_KEYS,ReplayTransport,load
import httpx


def test_replay_rebuilds_live_connector_graph_without_credentials(monkeypatch):
    for name in ('PLANE_API_KEY','SLACK_BOT_TOKEN','SLACK_USER_TOKEN'): monkeypatch.delenv(name,raising=False)
    db=DatabaseManager(graph_name='test_replay_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    try:
        result=load(db)
        assert result['changes']['retired']==0
        q=lambda cypher:db.graph.ro_query(cypher).result_set
        counts={r[0]:r[1] for r in q('MATCH (n) WHERE n.valid_to IS NULL RETURN labels(n)[0],count(n)')}
        assert (counts['Issue'],counts['DocPage'],counts['Comment'],counts['Thread'],counts['Component'])==(12,6,52,20,2)
        assert q('MATCH (:Issue)-[a:ASSIGNED_TO]->(:Person) WHERE a.valid_to IS NULL RETURN count(a)')[0][0]==12
        assert q('MATCH (:Issue)-[r:DEPENDS_ON]->(:Issue) WHERE r.valid_to IS NULL RETURN count(r)')[0][0]==23
        assert q("MATCH (p:Person)-[r:AUTHORED]->(m:Comment) WHERE m.source_system='slack' RETURN p.is_bot,count(m)")==[[True,32]]
        assert q("MATCH (m:Comment)-[r:REFERENCES]->() WHERE m.source_system='slack' AND r.valid_to IS NULL RETURN count(r)")[0][0]==22
        # Coverage is labeled as a replay, never as a live observation.
        assert {r[0] for r in q('MATCH (c:SourceCoverage) RETURN c.mode')}=={'replay'}
        # Re-loading the same snapshot is idempotent.
        assert load(db)['changes']['created']==0
    finally:
        db.clear_graph()


def test_replay_refuses_unrecorded_requests():
    transport=ReplayTransport([])
    with httpx.Client(transport=transport) as client:
        assert client.get('https://api.plane.so/api/v2/workspaces/x/projects/').status_code==404
    assert transport.misses


def test_snapshot_contains_no_account_identifiers():
    text=(FIXTURE/'responses.json').read_text(encoding='utf-8')+(FIXTURE/'manifest.json').read_text(encoding='utf-8')
    assert not re.search(r'xox[abpe]-|plane_api_',text)
    assert set(re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',text))<={'owner@example.invalid'}
    assert set(re.findall(r'https://([a-z0-9-]+)\.slack\.com',text))<={'wayfinder-demo'}
    assert set(re.findall(r'app\.plane\.so/([a-z0-9-]+)/',text))<={'wayfinder-demo'}
    def keys(value):
        if isinstance(value,dict):
            for k,v in value.items(): yield k; yield from keys(v)
        elif isinstance(value,list):
            for v in value: yield from keys(v)
    assert not set(keys(json.loads((FIXTURE/'responses.json').read_text(encoding='utf-8'))))&DROP_KEYS
