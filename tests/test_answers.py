"""Saved answers that invalidate themselves: save, re-check without writing, mark stale on refresh, notify."""
import asyncio
import uuid
import pytest
from starlette.testclient import TestClient
from wayfinder import answers
from wayfinder.db import DatabaseManager
from wayfinder.brain_graph import BrainGraph, BrainError
from wayfinder.company import freshness, slack
from wayfinder.mcp_server import create_server

T0, T2, T3 = '2026-10-01T00:00:00Z', '2026-10-10T00:00:00Z', '2026-10-12T00:00:00Z'
CURRENT = [{'text': 'They chose to "import sources before extraction".', 'evidence_ids': ['dec', 'page']}]
PAGE_ONLY = [{'text': 'The design page describes ingestion.', 'evidence_ids': ['page']}]
MANIFEST = {'organization_id': 'ans', 'synthetic': True, 'plane': {'workspace': 'w', 'project_ids': ['p']},
            'slack': {'channel_ids': ['C1']}, 'github': {}, 'people': [], 'identities': {}, 'teams': [], 'components': [], 'bindings': []}


@pytest.fixture
def db():
    instance = DatabaseManager(graph_name='test_answers_' + uuid.uuid4().hex)
    if not instance.ping(): pytest.skip('FalkorDB unavailable')
    node = lambda label, ident, **p: instance.upsert_node(label, {'id': ident, 'valid_to': None, **p})
    edge = lambda a, la, rel, b, lb, **p: instance.upsert_edge(la, a, lb, b, rel, {'id': f'{a}->{rel}->{b}', 'valid_from': T0, **p})
    node('DocPage', 'page', title='Ingestion design', text_content='We import sources before extraction.')
    node('Decision', 'dec', summary='Import sources before extraction', evidence_quote='We import sources before extraction.')
    edge('page', 'DocPage', 'ASSERTS', 'dec', 'Decision', authority='EXTRACTED')
    yield instance
    instance.clear_graph()


def edit_page(db):
    """The page changed: its claim retires and a different one replaces it."""
    db.query("MATCH (d {id:'dec'}) SET d.valid_to=$t", {'t': T2})
    db.query("MATCH (:DocPage {id:'page'})-[a:ASSERTS]->({id:'dec'}) SET a.valid_to=$t", {'t': T2})
    db.upsert_node('Decision', {'id': 'dec2', 'valid_to': None, 'summary': 'Extract while importing'})
    db.upsert_edge('DocPage', 'page', 'Decision', 'dec2', 'ASSERTS', {'id': 'page->ASSERTS->dec2', 'valid_from': T2})


def count(db):
    return db.query('MATCH (n) OPTIONAL MATCH (n)-[r]->() RETURN count(DISTINCT n), count(r)').result_set[0]


def test_save_answer_stores_question_statements_verdict_and_cites_edges(db):
    brain = BrainGraph(db)
    saved = answers.save_answer(db, brain.rows, 'How does ingestion order work?',
                                [CURRENT[0], {'text': 'It links an edge.', 'evidence_ids': ['page->ASSERTS->dec']}], now=T0)
    assert saved['verdict'] == 'VERIFIED' and saved['answer_id'].startswith('answer:')
    # Relationship IDs are kept as evidence but cannot be CITES targets.
    assert saved['cited'] == ['dec', 'page'] and saved['not_cited_as_nodes'] == ['page->ASSERTS->dec']
    row = db.query("MATCH (a:Answer)-[:CITES]->(n) RETURN a.question, a.verdict, a.saved_at, collect(n.id)").result_set[0]
    assert row[:3] == ['How does ingestion order work?', 'VERIFIED', T0] and sorted(row[3]) == ['dec', 'page']
    # Saving the same answer again updates it in place.
    again = answers.save_answer(db, brain.rows, 'How does ingestion order work?',
                                [CURRENT[0], {'text': 'It links an edge.', 'evidence_ids': ['page->ASSERTS->dec']}], now=T3)
    assert again['answer_id'] == saved['answer_id'] and db.query('MATCH (a:Answer) RETURN count(a), a.saved_at').result_set[0] == [1, T0]
    rival = answers.save_answer(db, brain.rows, 'Uncited?', [{'text': 'No evidence.', 'evidence_ids': []}])
    assert rival['verdict'] == 'UNSUPPORTED' and rival['reasons'] == ['Statement 1 is UNSUPPORTED: no evidence cited']
    with pytest.raises(answers.TruthError): answers.save_answer(db, brain.rows, '', CURRENT)


def test_answer_status_reverifies_without_writing_and_lists_changed(db):
    brain = BrainGraph(db)
    first = answers.save_answer(db, brain.rows, 'Ingestion order?', CURRENT)['answer_id']
    answers.save_answer(db, brain.rows, 'Is there a design page?', PAGE_ONLY)
    edit_page(db); before = count(db)
    status = brain.invoke('answer_status')
    assert status['summary'] == {'checked': 2, 'changed': 1, 'verified_now': 1}
    changed = status['changed'][0]
    assert changed['answer_id'] == first and (changed['saved_verdict'], changed['verdict_now']) == ('VERIFIED', 'OUT_OF_DATE')
    assert 'now replaced by dec2' in changed['reasons'][0] and [s['answer_id'] for s in status['stale']] == [first]
    one = brain.invoke('answer_status', answer_id=first)
    assert one['verdict_now'] == 'OUT_OF_DATE' and [r['id'] for r in one['statements'][0]['replaced_by']] == ['dec2']
    # Read-only: nothing new in the graph and the stored verdict is untouched.
    assert count(db) == before
    assert db.query("MATCH (a:Answer {id:$id}) RETURN a.verdict, a.stale", {'id': first}).result_set[0] == ['VERIFIED', False]
    with pytest.raises(BrainError, match='INVALID_REQUEST'): brain.invoke('answer_status', answer_id='answer:missing')


def test_mark_stale_records_why_once_and_recovers(db):
    brain = BrainGraph(db)
    ident = answers.save_answer(db, brain.rows, 'Ingestion order?', CURRENT)['answer_id']
    assert answers.mark_stale(db, brain.rows)['newly_stale'] == []
    edit_page(db)
    marked = answers.mark_stale(db, brain.rows, now=T3)
    assert [(s['answer_id'], s['verdict_now']) for s in marked['newly_stale']] == [(ident, 'OUT_OF_DATE')]
    stored = db.query('MATCH (a:Answer) RETURN a.stale, a.stale_since, a.stale_reason, a.verdict').result_set[0]
    assert stored[:2] == [True, T3] and 'dec out of date at ' + T2 in stored[2] and stored[3] == 'OUT_OF_DATE'
    # Still stale on the next refresh, but not newly so.
    again = answers.mark_stale(db, brain.rows)
    assert again['newly_stale'] == [] and again['still_stale'] == 1
    listed = answers.stale_answers(brain.rows)
    assert listed['stale'] == 1 and listed['items'][0]['stale_reason'] == stored[2]
    # The claim comes back: the answer is VERIFIED again and no longer stale.
    db.query("MATCH (d {id:'dec'}) SET d.valid_to=NULL"); db.query("MATCH ()-[a:ASSERTS]->({id:'dec'}) SET a.valid_to=NULL")
    assert answers.mark_stale(db, brain.rows)['recovered'] == [ident]
    assert db.query('MATCH (a:Answer) RETURN a.stale, a.stale_reason').result_set[0] == [False, None]


def test_refresh_marks_stale_answers_and_notifies_only_when_asked(db, monkeypatch):
    brain = BrainGraph(db)
    answers.save_answer(db, brain.rows, 'Ingestion order?', CURRENT)
    monkeypatch.setattr(freshness, 'sync_once', lambda *a, **k: {'plane': [], 'changes': {'summary': {}}})
    sent = []
    monkeypatch.setattr(slack, 'notify_stale_answers', lambda manifest, items: sent.append(items) or [{'status': 'posted'}])
    assert freshness.refresh(db, MANIFEST, slack=False, github=False)['answers']['newly_stale'] == []
    edit_page(db)
    summary = freshness.refresh(db, MANIFEST, slack=False, github=False)
    assert len(summary['answers']['newly_stale']) == 1 and 'notifications' not in summary['answers'] and sent == []
    # Reset it to VERIFIED: the next refresh sees it go stale again and, with notify_slack, posts once.
    db.query('MATCH (a:Answer) SET a.verdict="VERIFIED", a.stale=false')
    summary = freshness.refresh(db, MANIFEST, slack=False, github=False, notify_slack=True)
    assert len(sent) == 1 and len(sent[0]) == 1 and summary['answers']['notifications'] == [{'status': 'posted'}]


def test_slack_notice_posts_one_message_per_answer_without_links_or_keys(monkeypatch):
    item = {'answer_id': 'answer:abc', 'question': 'Who owns ENG-12? See https://app.plane.so/x', 'saved_verdict': 'VERIFIED',
            'verdict_now': 'REMOVED', 'reason': 'Statement 1 is REMOVED: plane:ans:issue:1 removed at ' + T2}
    text = answers.notice(item)
    assert 'ENG-12' not in text and 'ENG‑12' in text and 'https://' not in text and T2 in text

    class Session:
        def __init__(self): self.posts = []
        def post(self, method, json):
            self.posts.append((method, json))
            return type('R', (), {'raise_for_status': lambda s: None, 'json': lambda s: {'ok': True, 'ts': '1.2'}})()
    session = Session()
    out = slack.notify_stale_answers(MANIFEST, [item, {**item, 'answer_id': 'answer:def'}], session=session)
    assert [o['status'] for o in out] == ['posted', 'posted'] and [p[1]['channel'] for p in session.posts] == ['C1', 'C1']
    assert slack.notify_stale_answers({'slack': {}}, [item])[0]['status'] == 'skipped'
    monkeypatch.delenv('SLACK_BOT_TOKEN', raising=False)
    monkeypatch.setattr(slack, 'dotenv_values', lambda path: {})
    assert slack.notify_stale_answers(MANIFEST, [item])[0]['reason'].startswith('Set SLACK_BOT_TOKEN')


def test_save_answer_is_an_action_tool_and_answer_status_is_read_only(db):
    tools = {t.name: t for t in asyncio.run(create_server(db, trace=False).list_tools())}
    assert 'save_answer' not in tools and tools['answer_status'].annotations.readOnlyHint
    server = create_server(db, enable_actions=True, trace=False)
    acting = {t.name: t for t in asyncio.run(server.list_tools())}
    assert not acting['save_answer'].annotations.readOnlyHint
    asyncio.run(server.call_tool('save_answer', {'question': 'Ingestion order?', 'statements': CURRENT}))
    assert db.query('MATCH (a:Answer) RETURN a.verdict').result_set == [['VERIFIED']]


def test_dashboard_lists_stale_answers(db):
    from wayfinder.dashboard import create_app
    brain = BrainGraph(db)
    answers.save_answer(db, brain.rows, 'Ingestion order?', CURRENT); edit_page(db); answers.mark_stale(db, brain.rows)
    with TestClient(create_app(db)) as client:
        data = client.get('/api/answers').json()
        assert data['stale'] == 1 and data['items'][0]['question'] == 'Ingestion order?'
        token = client.get('/api/session').json()['token']
        checked = client.post('/api/tools/answer_status', json={}, headers={'X-Wayfinder-Session': token}).json()
        assert checked['summary']['checked'] == 1
        assert 'view-answers' in client.get('/').text
