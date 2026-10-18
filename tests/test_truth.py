"""Truth maintenance tools: answer verification, catch-up, stale pages, bus factor and approval validity."""
import asyncio
import json
import uuid
import pytest
from wayfinder.db import DatabaseManager
from wayfinder.brain_graph import BrainGraph, BrainError
from wayfinder.company.core import digest
from wayfinder.mcp_server import create_server

T0, T1, T2, T3 = '2026-10-01T00:00:00Z', '2026-10-05T00:00:00Z', '2026-10-10T00:00:00Z', '2026-10-12T00:00:00Z'


@pytest.fixture
def db():
    instance = DatabaseManager(graph_name='test_truth_' + uuid.uuid4().hex)
    if not instance.ping(): pytest.skip('FalkorDB unavailable')
    node = lambda label, ident, **p: instance.upsert_node(label, {'id': ident, 'valid_to': None, **p})
    edge = lambda a, la, rel, b, lb, **p: instance.upsert_edge(la, a, lb, b, rel, {'id': f'{a}->{rel}->{b}', 'valid_from': T0, **p})
    rev = lambda unit, at, text: node('SourceRevision', f'rev:{unit}:{at}', source_unit_id=unit, observed_at=at, text_content=text)
    # People: two humans and a bot.
    node('Person', 'ana', display_name='Ana'); node('Person', 'bo', display_name='Bo'); node('Person', 'bot', display_name='Bot', is_bot=True)
    node('Component', 'ingest', name='Ingestion'); node('Component', 'solo', name='Solo')
    node('Directory', 'd-root', path='ingest'); node('Directory', 'd-sub', path='ingest/sub'); node('File', 'f1', path='ingest/sub/a.py')
    edge('ingest', 'Component', 'IMPLEMENTED_IN', 'd-root', 'Directory'); edge('d-sub', 'Directory', 'CHILD_OF', 'd-root', 'Directory')
    edge('d-sub', 'Directory', 'CONTAINS', 'f1', 'File')
    edge('ana', 'Person', 'OWNS_COMPONENT', 'ingest', 'Component'); edge('ana', 'Person', 'OWNS_COMPONENT', 'solo', 'Component')
    edge('ana', 'Person', 'EXPERT_IN', 'd-root', 'Directory'); edge('bo', 'Person', 'EXPERT_IN', 'd-root', 'Directory')
    edge('bot', 'Person', 'EXPERT_IN', 'd-sub', 'Directory'); edge('ana', 'Person', 'EXPERT_IN', 'd-sub', 'Directory')
    for n, who in ((1, 'bo'), (2, 'bo'), (3, 'bot')):
        node('PullRequest', f'pr{n}', title=f'PR {n}', state='MERGED', merged_at=T2)
        edge(f'pr{n}', 'PullRequest', 'CONCERNS_COMPONENT', 'ingest', 'Component'); edge(who, 'Person', 'AUTHORED', f'pr{n}', 'PullRequest')
    edge('pr1', 'PullRequest', 'TOUCHES', 'f1', 'File')
    node('Issue', 'task', key='ING-1', state='Todo', source_updated_at=T0, source_system='plane')
    edge('task', 'Issue', 'ASSIGNED_TO', 'ana', 'Person'); edge('task', 'Issue', 'CONCERNS_COMPONENT', 'ingest', 'Component')
    # A page about the component, last edited before PR 1 merged.
    node('DocPage', 'page', title='Ingestion design', source_updated_at=T1, source_system='plane',
         text_content='We import sources before extraction.')
    edge('page', 'DocPage', 'DESCRIBES', 'ingest', 'Component'); node('DocPage', 'fresh', title='Unrelated', source_updated_at=T3)
    rev('page', T0, 'old'); rev('page', T2, 'We import sources before extraction.')
    rev('task', T2, 'ING-1 body')
    # Claims: a current decision, a retired one with a different replacement, and a retired one re-extracted unchanged.
    node('Decision', 'dec', summary='Import sources before extraction', evidence_quote='We import sources before extraction.')
    edge('page', 'DocPage', 'ASSERTS', 'dec', 'Decision', authority='EXTRACTED'); edge('dec', 'Decision', 'ABOUT', 'ingest', 'Component')
    node('Comment', 'msg', text_content='Use batch mode. Actually, stream it.', source_system='slack', created_at=T0)
    node('Decision', 'old', summary='Use batch mode', valid_to=T2)
    edge('msg', 'Comment', 'ASSERTS', 'old', 'Decision', authority='EXTRACTED', valid_to=T2)
    node('Decision', 'new', summary='Stream it'); edge('msg', 'Comment', 'ASSERTS', 'new', 'Decision', authority='EXTRACTED', valid_from=T2)
    node('Question', 'q-old', summary='Who reviews?', valid_to=T2)
    edge('task', 'Issue', 'ASSERTS', 'q-old', 'Question', valid_to=T2)
    node('Question', 'q-new', summary='Who reviews?'); edge('task', 'Issue', 'ASSERTS', 'q-new', 'Question', valid_from=T2)
    node('Issue', 'deleted', key='ING-2', valid_to=T2, is_deleted=True, source_system='plane')
    # A conflicting decision about the same component from a different source.
    node('Comment', 'other', text_content='Extract first.'); node('Decision', 'rival', summary='Extract first')
    edge('other', 'Comment', 'ASSERTS', 'rival', 'Decision'); edge('rival', 'Decision', 'ABOUT', 'ingest', 'Component')
    # Link history: one link made, one broken, and one replaced in the same instant (not a change).
    edge('msg', 'Comment', 'REFERENCES', 'task', 'Issue', valid_from=T2)
    edge('page', 'DocPage', 'REFERENCES', 'deleted', 'Issue', valid_from=T0, valid_to=T2)
    instance.query("MATCH (a {id:'other'}), (b {id:'task'}) CREATE (a)-[:REFERENCES {id:'x@1', logical_id:'x', valid_from:$t0, valid_to:$t2}]->(b), "
                   "(a)-[:REFERENCES {id:'x@2', logical_id:'x', valid_from:$t2}]->(b)", {'t0': T0, 't2': T2})
    yield instance
    instance.clear_graph()


def statuses(result):
    return [s['status'] for s in result['statements']]


def test_verify_answer_grades_each_statement_against_current_sources(db):
    brain = BrainGraph(db)
    result = brain.invoke('verify_answer', statements=[
        {'text': 'They chose to "import sources before extraction".', 'evidence_ids': ['dec', 'page']},
        {'text': 'The page says "extraction always runs first".', 'evidence_ids': ['page']},
        {'text': 'The team uses batch mode.', 'evidence_ids': ['old']},
        {'text': 'Someone asked who reviews.', 'evidence_ids': ['q-old']},
        {'text': 'ING-2 tracks it.', 'evidence_ids': ['deleted']},
        {'text': 'Uncited.', 'evidence_ids': []},
        {'text': 'An ID that never existed.', 'evidence_ids': ['missing']},
    ])
    assert statuses(result) == ['DISPUTED', 'UNSUPPORTED', 'OUT_OF_DATE', 'RECITE', 'REMOVED', 'UNSUPPORTED', 'UNSUPPORTED']
    first, _, batch, question = result['statements'][:4]
    assert [d['id'] for d in first['conflicting_current_decisions']] == ['rival']
    assert [r['id'] for r in batch['replaced_by']] == ['new'] and batch['evidence'][0]['retired_at'] == T2
    assert [r['id'] for r in question['replaced_by']] == ['q-new']
    assert result['statements'][1]['quotes_not_in_current_sources'] == ['extraction always runs first']


def test_verify_answer_accepts_relationship_evidence_and_rejects_bad_input(db):
    brain = BrainGraph(db)
    result = brain.invoke('verify_answer', statements=[{'text': 'The page linked ING-2.', 'evidence_ids': ['page->REFERENCES->deleted']}])
    assert result['statements'][0]['evidence'][0]['status'] == 'RETIRED' and statuses(result) == ['REMOVED']
    with pytest.raises(BrainError, match='INVALID_REQUEST'): brain.invoke('verify_answer', statements=[])


def test_changes_since_separates_new_edited_retired_and_real_link_changes(db):
    result = BrainGraph(db).invoke('changes_since', since=T1, until=T3)
    assert [r['id'] for r in result['edited']] == ['page'] and [r['id'] for r in result['created']] == ['task']
    assert {r['id'] for r in result['retired']} == {'old', 'q-old', 'deleted'}
    made = {(r['source_id'], r['target_id']) for r in result['links_made']}
    broken = {(r['source_id'], r['target_id']) for r in result['links_broken']}
    # The same-instant replacement of other->task is neither made nor broken.
    assert ('msg', 'task') in made and ('other', 'task') not in made
    # Retired claims show up as broken ASSERTS links too.
    assert broken == {('page', 'deleted'), ('msg', 'old'), ('task', 'q-old')}
    scoped = BrainGraph(db).invoke('changes_since', since=T1, until=T3, component_id='solo')
    assert scoped['summary']['created'] == 0 and scoped['summary']['edited'] == 0
    with pytest.raises(BrainError, match='ISO'): BrainGraph(db).invoke('changes_since', since='last week')


def test_stale_documents_flags_pages_whose_component_code_changed_after_the_edit(db):
    result = BrainGraph(db).invoke('stale_documents')
    by_id = {i['page']['id']: i for i in result['items']}
    assert by_id['page']['status'] == 'POSSIBLY_STALE'
    assert [p['id'] for p in by_id['page']['code_merged_since']] == ['pr1']
    assert by_id['fresh']['status'] == 'CURRENT' and result['items'][0]['page']['id'] == 'page'


def test_knowledge_risk_reports_bus_factor_without_bots(db):
    brain = BrainGraph(db)
    risk = brain.invoke('knowledge_risk', target_id='ingest')
    assert [h['id'] for h in risk['holders']] == ['ana', 'bo'] and 'bot' not in json.dumps(risk['holders'])
    # Ana: owner 3 + task 1 + root expertise 2 = 6; Bo: two PRs 4 + root expertise 2 = 6. Neither alone holds over half.
    assert [h['share'] for h in risk['holders']] == [0.5, 0.5] and risk['bus_factor'] == 2 and risk['risk'] == 'MEDIUM'
    db.query("MATCH (p {id:'pr2'})-[r:CONCERNS_COMPONENT]->() SET r.valid_to=$t", {'t': T3})
    assert brain.invoke('knowledge_risk', target_id='ingest')['bus_factor'] == 1
    leaving = brain.invoke('knowledge_risk', target_id='ana')['if_they_leave']
    assert {c['id'] for c in leaving['components_without_owner']} == {'ingest', 'solo'}
    assert [t['id'] for t in leaving['open_tasks_without_assignee']] == ['task']
    # The bot does not count as a second expert for the subdirectory; Bo covers the root.
    assert [d['id'] for d in leaving['directories_without_expert']] == ['d-sub']
    with pytest.raises(BrainError, match='Component or Person'): brain.invoke('knowledge_risk', target_id='page')


def test_action_status_voids_an_approval_when_cited_evidence_changes(db, monkeypatch):
    monkeypatch.setattr('wayfinder.company.brief.validate_brief', lambda database, brief: {'valid': True, 'errors': []})
    payload = {'title': 'Follow up', 'brief': {}}
    db.upsert_node('Action', {'id': 'act', 'status': 'approved', 'payload_json': json.dumps(payload), 'payload_hash': digest(payload),
                              'evidence_ids': ['dec', 'task'], 'approved_at': T1, 'approved_by': 'A person'})
    brain = BrainGraph(db)
    stale = brain.invoke('action_status', action_id='act')
    # task gained a revision after approval.
    assert stale['verdict'] == 'APPROVAL_STALE' and [(c['id'], c['change']) for c in stale['evidence_changed']] == [('task', 'edited')]
    db.query("MATCH (a:Action {id:'act'}) SET a.approved_at=$t", {'t': T3})
    assert brain.invoke('action_status', action_id='act')['verdict'] == 'APPROVAL_HOLDS'
    db.query("MATCH (a:Action {id:'act'}) SET a.payload_hash='tampered'")
    assert brain.invoke('action_status', action_id='act')['verdict'] == 'APPROVAL_STALE'


def test_truth_tools_are_read_only_mcp_tools(db):
    server = create_server(db, trace=False)
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    for name in ('verify_answer', 'changes_since', 'stale_documents', 'knowledge_risk', 'action_status', 'fix_queue'):
        assert tools[name].annotations.readOnlyHint
    before = db.query('MATCH (n) RETURN count(n)').result_set[0][0]
    asyncio.run(server.call_tool('knowledge_risk', {'target_id': 'ingest'}))
    asyncio.run(server.call_tool('fix_queue', {'limit': 5}))
    assert db.query('MATCH (n) RETURN count(n)').result_set[0][0] == before


def test_fix_queue_turns_findings_into_suggested_tasks_with_evidence(db):
    result = BrainGraph(db).invoke('fix_queue')
    items = {i['id']: i for i in result['items']}
    assert [i['id'] for i in result['items']] == ['fix:disputed:dec:rival', 'fix:stale:page', 'fix:bus-factor:solo']
    assert result['summary'] == {'DISPUTED_DECISION': 1, 'STALE_DOCUMENT': 1, 'SINGLE_HOLDER': 1}
    stale = items['fix:stale:page']
    assert stale['title'] == 'Update Ingestion design: PR 1 changed its code since' and stale['component_id'] == 'ingest'
    assert stale['evidence_ids'] == ['page', 'pr1'] and [e['id'] for e in stale['evidence']] == ['page', 'pr1']
    disputed = items['fix:disputed:dec:rival']
    assert set(disputed['evidence_ids']) == {'dec', 'rival', 'ingest', 'page', 'other'} and disputed['component_id'] == 'ingest'
    assert 'Import sources before extraction' in disputed['title'] and 'Extract first' in disputed['title']
    # Ingestion has two holders; Solo has only its owner.
    holder = items['fix:bus-factor:solo']
    assert holder['title'] == 'Add a second owner or reviewer for Solo' and holder['evidence_ids'] == ['solo', 'ana']
    assert all(i['actions'] == [] for i in result['items'])
    assert BrainGraph(db).invoke('fix_queue', limit=1)['total'] == 3
    with pytest.raises(BrainError, match='limit'): BrainGraph(db).invoke('fix_queue', limit=0)


def test_prepare_fix_uses_the_hash_approval_and_execute_rules(db):
    from wayfinder.company.actions import PlaneActions
    from wayfinder.company.core import load_manifest
    from wayfinder.company.fixture import MANIFEST

    class Client:
        calls = []
        def request(self, method, path, json=None): self.calls.append(json); return {'id': f'plane-{len(self.calls)}'}

    db.query("MATCH (n) WHERE n.id IN ['page', 'pr1'] SET n.source_url = 'https://example.test/' + n.id")
    client = Client(); actions = PlaneActions(db, load_manifest(MANIFEST), client)
    proposal = actions.prepare_fix('fix:stale:page')
    assert proposal['approval_required'] and proposal['finding_id'] == 'fix:stale:page'
    payload = proposal['payload']
    assert payload['title'] == 'Update Ingestion design: PR 1 changed its code since'
    assert payload['brief']['finding']['id'] == 'fix:stale:page'
    assert [c['evidence_ids'] for c in payload['brief']['concerns']] == [['page'], ['pr1']]
    assert actions.get(proposal['action_id'])['evidence_ids'] == ['page', 'pr1']
    # Preparing does not approve: execute refuses until a person approves this exact hash.
    with pytest.raises(ValueError, match='approval'): actions.execute(proposal['action_id'])
    with pytest.raises(ValueError, match='hash'): actions.approve(proposal['action_id'], 'wrong', 'A person')
    queued = BrainGraph(db).invoke('fix_queue')['items']
    assert [a['status'] for a in next(i for i in queued if i['id'] == 'fix:stale:page')['actions']] == ['proposed']
    actions.approve(proposal['action_id'], proposal['payload_hash'], 'A person')
    assert actions.execute(proposal['action_id'])['external_id'] == 'plane-1'
    assert 'https://example.test/pr1' in client.calls[0]['description_html']
    # A second proposal is voided when its evidence is edited after approval.
    again = actions.prepare_fix('fix:stale:page')
    actions.approve(again['action_id'], again['payload_hash'], 'A person')
    db.upsert_node('SourceRevision', {'id': 'rev:page:later', 'source_unit_id': 'page', 'observed_at': '2999-01-01T00:00:00Z'})
    with pytest.raises(ValueError, match='edited after approval'): actions.execute(again['action_id'])
    assert len(client.calls) == 1
    # Findings without citable sources, or that no longer hold, are refused.
    with pytest.raises(ValueError, match='source URL'): actions.prepare_fix('fix:bus-factor:solo')
    db.query("MATCH (p {id:'page'}) SET p.source_updated_at = $t", {'t': '2026-10-15T00:00:00Z'})
    with pytest.raises(ValueError, match='no longer|not in the current fix queue'): actions.prepare_fix('fix:stale:page')
