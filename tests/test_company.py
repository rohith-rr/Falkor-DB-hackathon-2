import copy
import uuid
from types import SimpleNamespace
import httpx
import pytest
from wayfinder.db import DatabaseManager
from wayfinder.company.fixture import ingest_fixture,MANIFEST
from wayfinder.company.core import load_manifest,CompanyStore,html_text
from wayfinder.company.brief import draft_brief,validate_brief
from wayfinder.company.actions import PlaneActions
from wayfinder.company.plane import PlaneClient
from wayfinder.company.evaluation import evaluate
from wayfinder.derived.concerns import ConcernResolver
from wayfinder.extraction.extractor import ExtractionPipeline
from wayfinder.extraction.cache import ExtractionCache


@pytest.fixture
def db():
    instance=DatabaseManager(graph_name='test_company_'+uuid.uuid4().hex)
    if not instance.ping(): pytest.skip('FalkorDB unavailable')
    ingest_fixture(instance)
    yield instance
    instance.clear_graph()


def test_company_navigation_and_evidence(db):
    from wayfinder.brain_graph import BrainGraph
    brief=draft_brief(db,'Node Runtime')
    assert len(brief['ownership'])==2
    assert validate_brief(db,brief)['valid']
    result=BrainGraph(db).invoke('company_context',component='Node Runtime')
    assert result['related'] and result['context']['source_scope']
    review=evaluate(db)
    assert review['fixture_claims_valid']==50
    assert review['semantic_accuracy'] is None
    assert all(v['valid'] for v in review['brief_validations'].values())
    assert {r[1] for r in review['concerns']}=={'open','resolved'}


def test_stale_quotes_and_wrong_urls_rejected(db):
    brief=draft_brief(db,'Node Runtime')
    broken=copy.deepcopy(brief); broken['decisions'][0]['source_urls']=['https://wrong.example/']
    assert not validate_brief(db,broken)['valid']
    claim=brief['decisions'][0]
    db.query('MATCH (r:SourceRevision {id:$id}),(u {id:r.source_unit_id}) SET u.source_revision_id=\'changed\'',{'id':claim['source_revision_id']})
    assert not validate_brief(db,brief)['valid']


def test_scoped_approval_does_not_resolve(db):
    db.query("MATCH (p:Person {login:'ada'}),(c:Component {name:'Resource Allocation'}) MERGE (p)-[:APPROVED]->(c)")
    assert ConcernResolver(db).resolve_concern_statuses()['open']==1


def test_action_approval_and_idempotency(db):
    class Client:
        calls=0
        def request(self,*args,**kwargs): self.calls+=1; return {'id':'plane-created'}
    client=Client(); actions=PlaneActions(db,load_manifest(MANIFEST),client)
    proposal=actions.prepare('Review starvation','Check the still-open concern',draft_brief(db,'Resource Allocation'))
    with pytest.raises(ValueError,match='approval'): actions.execute(proposal['action_id'])
    with pytest.raises(ValueError,match='hash'): actions.approve(proposal['action_id'],'wrong','Human')
    actions.approve(proposal['action_id'],proposal['payload_hash'],'Human')
    assert actions.execute(proposal['action_id'])['external_id']=='plane-created'
    assert actions.execute(proposal['action_id'])['external_id']=='plane-created'
    assert client.calls==1


def test_uncertain_action_does_not_duplicate(db):
    class Client:
        calls=0
        def request(self,*a,**k): self.calls+=1; raise httpx.ReadTimeout('Unknown outcome')
        def listing(self,*a): return []
    client=Client(); actions=PlaneActions(db,load_manifest(MANIFEST),client)
    p=actions.prepare('Check rollback','Inspect evidence',draft_brief(db,'Node Runtime'))
    actions.approve(p['action_id'],p['payload_hash'],'Human')
    with pytest.raises(httpx.ReadTimeout): actions.execute(p['action_id'])
    with pytest.raises(ValueError,match='uncertain'): actions.execute(p['action_id'])
    assert client.calls==1


def test_pagination_and_html():
    pages=[{'data':[{'id':'one'}],'next':1},{'data':[{'id':'two'}],'next':None}]
    client=PlaneClient('demo',token='test',session=SimpleNamespace())
    client.request=lambda *a,**k: pages.pop(0)
    assert [r['id'] for r in client.listing('projects/')]==['one','two']
    client.request=lambda *a,**k: {'data':[],'next':1}
    with pytest.raises(RuntimeError,match='advance'): client.listing('projects/')
    assert html_text('<script>secret()</script><h1>ADR</h1><p>Use bounds.</p>')=='# ADR\nUse bounds.'


class FakeLLM:
    model='fake-one'
    fail=False
    def chat_completion_json(self,**kwargs):
        return {'arguments':[{'kind':'explanation','summary':'A bounded deadline is necessary.',
            'evidence_quote':'A bounded deadline is necessary.'}],'decisions':[],'rationales':[],
            'questions':[],'answers':[],'topics':[]}
    def get_embedding(self,text):
        if self.fail: raise RuntimeError('embedding failure')
        return [1.0,0.0,0.0]


def test_atomic_publication_and_invalidation(db,tmp_path):
    text='A bounded deadline is necessary.'
    db.upsert_node('Comment',{'id':'publication-unit','body_text':text,'source_url':'https://example.org/source'})
    llm=FakeLLM(); pipeline=ExtractionPipeline(db,llm,ExtractionCache(tmp_path))
    assert pipeline.process_text_unit('Comment','publication-unit',text)['accepted']==1
    first=db.query("MATCH (c {id:'publication-unit'}) RETURN c.extraction_version").result_set[0][0]
    assert pipeline.process_text_unit('Comment','publication-unit',text)['skipped']
    llm.model='fake-two'; llm.fail=True
    with pytest.raises(RuntimeError,match='embedding'): pipeline.process_text_unit('Comment','publication-unit',text)
    assert db.query("MATCH (c {id:'publication-unit'}) RETURN c.extraction_version").result_set[0][0]==first
    llm.fail=False
    pipeline.process_text_unit('Comment','publication-unit',text,prev_comments_context='new context')
    second=db.query("MATCH (c {id:'publication-unit'}) RETURN c.extraction_version").result_set[0][0]
    assert second!=first
    assert db.query("MATCH (:Comment {id:'publication-unit'})-[r:ASSERTS]->() WHERE r.valid_to IS NULL RETURN count(r)").result_set[0][0]==1


def test_partial_source_does_not_prove_deletion(db):
    store=CompanyStore(db,load_manifest(MANIFEST))
    store.coverage('plane','project:runtime:items','unavailable',gaps=['access denied'])
    assert db.query('MATCH (i:Issue) WHERE i.valid_to IS NULL RETURN count(i)').result_set[0][0]==12


def test_confirmed_source_deletion_retires_interpretation(db):
    store=CompanyStore(db,load_manifest(MANIFEST))
    store.reconcile('plane','plane:northstar:issue:w1:comments',[])
    assert db.query("MATCH (a:Argument {id:'company:northstar:claim:pc1'}) RETURN a.valid_to").result_set[0][0]
    assert ConcernResolver(db).resolve_concern_statuses()['resolved']==0


def test_publication_conflict_does_not_publish(db,tmp_path):
    text='A bounded deadline is necessary.'
    db.upsert_node('Comment',{'id':'cas-unit','body_text':text,'source_url':'https://example.org/cas'})
    llm=FakeLLM(); pipeline=ExtractionPipeline(db,llm,ExtractionCache(tmp_path))
    pipeline.process_text_unit('Comment','cas-unit',text)
    previous=db.query("MATCH (u {id:'cas-unit'}) RETURN u.extraction_version").result_set[0][0]
    llm.model='changed-model'
    def mutate(text):
        db.query("MATCH (u {id:'cas-unit'}) SET u.body_text='Concurrent edit'")
        return [1.0,0.0,0.0]
    llm.get_embedding=mutate
    with pytest.raises(RuntimeError,match='conflict'): pipeline.process_text_unit('Comment','cas-unit',text)
    assert db.query("MATCH (u {id:'cas-unit'}) RETURN u.extraction_version").result_set[0][0]==previous
    assert db.query("MATCH (:Comment {id:'cas-unit'})-[r:ASSERTS]->() WHERE r.valid_to IS NULL RETURN count(r)").result_set[0][0]==1


def test_unauthorized_resolution_is_not_closure(db):
    db.query("MATCH (p:Person)-[m:MADE]->(r:Argument {kind:'resolution'}) DELETE m")
    db.query("MATCH (p:Person {login:'cy'}),(r:Argument {kind:'resolution'}) CREATE (p)-[:MADE]->(r)")
    result=ConcernResolver(db).resolve_concern_statuses()
    assert result['resolved']==0 and result['claimed_addressed']==1


def test_embedding_dimension_failure_never_falls_back():
    from wayfinder.extraction.llm_client import OpenAPIClient
    client=OpenAPIClient(api_key='test',embedding_api_key='test',embedding_dim=3)
    client._embedding_client=SimpleNamespace(embeddings=SimpleNamespace(create=lambda **k:SimpleNamespace(data=[SimpleNamespace(embedding=[1.0,0.0])])))
    with pytest.raises(RuntimeError,match='no semantic fallback'): client.get_embedding('text')


def test_plane_access_failure_is_capability_error():
    session=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(403,json={'error':'denied'})))
    client=PlaneClient('sandbox',token='test',session=session)
    with pytest.raises(RuntimeError,match='HTTP 403'): client.listing('projects/runtime/pages/')
    client.close()


def test_slack_pagination_and_thread_access():
    from wayfinder.company.slack import SlackClient
    calls=[]
    def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith('conversations.replies'):
            return httpx.Response(200,json={'ok':False,'error':'missing_scope'})
        cursor=request.url.params.get('cursor')
        return httpx.Response(200,json={'ok':True,'messages':[{'ts':'2' if cursor else '1'}],
            'is_limited':True,'response_metadata':{'next_cursor':'' if cursor else 'next'}})
    session=httpx.Client(base_url='https://slack.com/api/',transport=httpx.MockTransport(respond))
    client=SlackClient(token='test',session=session)
    rows,limited=client.messages('conversations.history',channel='CDEMO')
    assert len(rows)==2 and limited
    with pytest.raises(RuntimeError,match='missing_scope'): client.messages('conversations.replies',channel='CDEMO',ts='1')
    client.close()


def test_actions_are_opt_in_and_no_approval_tool(db):
    import asyncio
    from wayfinder.mcp_server import create_server
    regular=asyncio.run(create_server(db).list_tools())
    enabled=asyncio.run(create_server(db,enable_actions=True).list_tools())
    assert len(regular)==21 and all(t.annotations.readOnlyHint for t in regular)
    assert len(enabled)==23 and not any(t.name.startswith('approve') for t in enabled)


def test_approval_cannot_cover_changed_payload(db):
    import json
    actions=PlaneActions(db,load_manifest(MANIFEST))
    p=actions.prepare('Review sources','Verify before rollout',draft_brief(db,'Node Runtime'))
    actions.approve(p['action_id'],p['payload_hash'],'Human')
    changed=actions.get(p['action_id']); payload=json.loads(changed['payload_json']);payload['title']='Different task'
    db.query('MATCH (a:Action {id:$id}) SET a.payload_json=$payload',{'id':p['action_id'],'payload':json.dumps(payload)})
    with pytest.raises(ValueError,match='approval'): actions.execute(p['action_id'])


def test_github_search_partitioning(monkeypatch):
    from datetime import datetime,timezone,timedelta
    from wayfinder.github.client import GitHubClient
    client=GitHubClient(token='fake'); windows=[]
    def search(**kw):
        start=datetime.fromisoformat(kw['since_date']); end=datetime.fromisoformat(kw['until_date'])
        windows.append((start,end))
        count=1000 if (end-start).total_seconds()>86400 else 1
        return {'search':{'issueCount':count,'nodes':[{'id':kw['kind']+kw['since_date']}],
            'pageInfo':{'hasNextPage':False}}}
    monkeypatch.setattr(client,'search_sig_node_activity',search)
    records=list(client.iter_activity(since_date=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()))
    assert len(records)>=6 and len(windows)>2
    client.close()


@pytest.mark.parametrize('quote',['The concern is not resolved.','Is the concern resolved?',
    'The author claims the concern is fixed.','/approve','The unresolved issue remains.'])
def test_ambiguous_or_negated_resolution_is_not_closure(quote):
    from wayfinder.derived.concerns import explicit_resolution
    assert not explicit_resolution(quote)
