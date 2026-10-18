"""Decision supersession: computed SUPERSEDES by source authority then time, reported by decisions and verify_answer."""
import uuid
import pytest
from wayfinder.db import DatabaseManager
from wayfinder.brain_graph import BrainGraph, MEANINGS
from wayfinder.company import freshness
from wayfinder.derived.supersession import compute_supersession

T0, T1, T2, T3, T4 = ('2026-10-01T00:00:00Z', '2026-10-05T00:00:00Z', '2026-10-10T00:00:00Z', '2026-10-12T00:00:00Z',
                      '2026-10-14T00:00:00Z')


@pytest.fixture
def db():
    instance = DatabaseManager(graph_name='test_supersession_' + uuid.uuid4().hex)
    if not instance.ping(): pytest.skip('FalkorDB unavailable')
    node = lambda label, ident, **p: instance.upsert_node(label, {'id': ident, 'valid_to': None, **p})
    edge = lambda a, la, rel, b, lb, **p: instance.upsert_edge(la, a, lb, b, rel, {'id': f'{a}->{rel}->{b}', 'valid_from': T0, **p})

    def decision(ident, summary, unit, unit_label, source, at, target, target_label='Component'):
        node(unit_label, unit, source_system=source, created_at=at, text_content=summary + '.')
        node('Decision', ident, summary=summary, evidence_quote=summary + '.', source_timestamp=at)
        edge(unit, unit_label, 'ASSERTS', ident, 'Decision', authority='EXTRACTED')
        edge(ident, 'Decision', 'ABOUT', target, target_label, authority='EXTRACTED')

    node('Component', 'ingest', name='Ingestion'); node('Component', 'solo', name='Solo'); node('Component', 'x', name='X')
    node('Issue', 'task', key='ING-1', source_system='plane')
    # Four sources about one component: the ADR is oldest but outranks the rest; the Slack message is newest but lowest.
    decision('page-dec', 'Import sources before extraction', 'adr', 'DocPage', 'plane', T0, 'ingest')
    decision('plane-old', 'Batch nightly', 'plane-c1', 'Comment', 'plane', T1, 'ingest')
    decision('plane-new', 'Batch hourly', 'plane-i1', 'Issue', 'plane', T3, 'ingest')
    decision('slack-dec', 'Stream it', 'slack-m1', 'Comment', 'slack', T4, 'ingest')
    # Scope: a Slack decision about one work item is not compared with decisions about the component.
    decision('task-dec', 'Ship the task behind a flag', 'slack-m2', 'Comment', 'slack', T4, 'task', 'Issue')
    # Equal authority and time: neither holds over the other.
    decision('tie-a', 'Use Postgres', 'plane-c2', 'Comment', 'plane', T2, 'solo')
    decision('tie-b', 'Use SQLite', 'plane-c3', 'Comment', 'plane', T2, 'solo')
    # The newer Slack message explicitly replaces the Plane decision; that extracted edge wins over authority.
    decision('x-plane', 'Retry twice', 'plane-c4', 'Comment', 'plane', T1, 'x')
    decision('x-slack', 'Retry three times', 'slack-m3', 'Comment', 'slack', T2, 'x')
    edge('x-slack', 'Decision', 'SUPERSEDES', 'x-plane', 'Decision', authority='EXTRACTED', evidence_quote='Retry three times.')
    yield instance
    instance.clear_graph()


def computed(db):
    rows = db.query("MATCH (a)-[r:SUPERSEDES]->(b) WHERE r.authority='COMPUTED' RETURN a.id, b.id, r.basis, r.reason, "
                    'r.scope_id, r.valid_from, r.valid_to ORDER BY a.id, b.id').result_set
    return {(a, b): dict(basis=basis, reason=reason, scope=scope, valid_from=start, valid_to=end)
            for a, b, basis, reason, scope, start, end in rows}


def test_supersession_ranks_by_source_authority_then_time_within_one_target(db):
    stats = compute_supersession(db, now=T4)
    assert stats == {'edges': 3, 'created': 3, 'closed': 0, 'unchanged': 0}
    edges = computed(db)
    assert set(edges) == {('page-dec', 'plane-new'), ('plane-new', 'plane-old'), ('plane-old', 'slack-dec')}
    assert edges[('page-dec', 'plane-new')]['basis'] == 'source_authority'
    assert edges[('page-dec', 'plane-new')]['reason'] == 'page outranks plane record'
    assert edges[('plane-new', 'plane-old')]['basis'] == 'recency' and T3 in edges[('plane-new', 'plane-old')]['reason']
    assert edges[('plane-old', 'slack-dec')]['reason'] == 'plane record outranks slack message'
    assert all(e['scope'] == 'ingest' and e['valid_from'] == T4 and e['valid_to'] is None for e in edges.values())
    # Scope, ties and explicit extracted supersession produce no computed edges.
    touched = {i for pair in edges for i in pair}
    assert not touched & {'task-dec', 'tie-a', 'tie-b', 'x-plane', 'x-slack'}
    # Superseded decisions are kept, still current and still asserted.
    assert db.query('MATCH (d:Decision) WHERE d.valid_to IS NULL RETURN count(d)').result_set[0][0] == 9
    assert compute_supersession(db, now=T4) == {'edges': 3, 'created': 0, 'closed': 0, 'unchanged': 3}


def test_retiring_the_holder_closes_its_edges_and_promotes_the_next(db):
    compute_supersession(db, now=T3)
    db.query("MATCH (d:Decision {id:'page-dec'})-[r:ABOUT]->() SET d.valid_to=$t, r.valid_to=$t", {'t': T4})
    assert compute_supersession(db, now=T4) == {'edges': 2, 'created': 0, 'closed': 1, 'unchanged': 2}
    # The closed edge is retained as history, not deleted.
    retained = db.query("MATCH (:Decision {id:'page-dec'})-[r:SUPERSEDES]->(:Decision {id:'plane-new'}) RETURN r.valid_to").result_set
    assert retained == [[T4]]
    current = BrainGraph(db).invoke('decisions', target_id='ingest')['current']
    assert current['status'] == 'HOLDS' and current['holding'][0]['id'] == 'plane-new'


def test_decisions_reports_the_holder_and_its_chain(db):
    compute_supersession(db, now=T4)
    result = BrainGraph(db).invoke('decisions', target_id='ingest')
    current = result['current']
    assert current['status'] == 'HOLDS' and [h['id'] for h in current['holding']] == ['page-dec']
    chain = current['holding'][0]['supersedes']
    assert [(c['id'], c['depth']) for c in chain] == [('plane-new', 1), ('plane-old', 2), ('slack-dec', 3)]
    assert chain[0]['authority'] == 'COMPUTED' and chain[0]['basis'] == 'source_authority'
    assert sorted(current['superseded']) == ['plane-new', 'plane-old', 'slack-dec']
    rows = {i['n']['id']: i for i in result['items']}
    assert rows['page-dec']['holds'] is True and rows['page-dec']['superseded_by'] == []
    assert rows['slack-dec']['holds'] is False and rows['slack-dec']['superseded_by'] == ['plane-old']
    tie = BrainGraph(db).invoke('decisions', target_id='solo')['current']
    assert tie['status'] == 'DISPUTED' and {h['id'] for h in tie['holding']} == {'tie-a', 'tie-b'}
    explicit = BrainGraph(db).invoke('decisions', target_id='x')['current']
    assert [h['id'] for h in explicit['holding']] == ['x-slack'] and explicit['holding'][0]['supersedes'][0]['authority'] == 'EXTRACTED'


def test_verify_answer_marks_a_superseded_citation_out_of_date_not_disputed(db):
    brain = BrainGraph(db)
    claims = [{'text': 'They stream it.', 'evidence_ids': ['slack-dec']},
              {'text': 'They "import sources before extraction".', 'evidence_ids': ['page-dec']},
              {'text': 'They use Postgres.', 'evidence_ids': ['tie-a']}]
    # Before the pass runs, decisions from different sources about one target are only a dispute.
    assert [s['status'] for s in brain.invoke('verify_answer', statements=claims)['statements']] == ['DISPUTED'] * 3
    compute_supersession(db, now=T4)
    result = brain.invoke('verify_answer', statements=claims)
    stream, adr, tie = result['statements']
    assert [s['status'] for s in result['statements']] == ['OUT_OF_DATE', 'VERIFIED', 'DISPUTED']
    assert [r['id'] for r in stream['replaced_by']] == ['page-dec'] and stream['conflicting_current_decisions'] == []
    assert stream['evidence'][0]['status'] == 'SUPERSEDED' and stream['evidence'][0]['holding'] == ['page-dec']
    assert [s['id'] for s in stream['evidence'][0]['superseded_by']] == ['plane-old']
    assert adr['conflicting_current_decisions'] == []
    assert [d['id'] for d in tie['conflicting_current_decisions']] == ['tie-b']


def test_refresh_runs_the_supersession_pass_and_meanings_document_the_edge(db, monkeypatch):
    monkeypatch.setattr(freshness, 'sync_once', lambda *a, **k: {'plane': [], 'changes': {'summary': {}}})
    manifest = {'organization_id': 'none', 'plane': {}, 'slack': {}, 'github': {}}
    summary = freshness.refresh(db, manifest, slack=False, github=False)
    assert summary['supersession']['created'] == 3 and len(computed(db)) == 3
    assert 'COMPUTED' in MEANINGS['SUPERSEDES'] and 'history' in MEANINGS['SUPERSEDES']
