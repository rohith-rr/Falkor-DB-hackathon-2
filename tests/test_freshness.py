"""Source changes reach the graph through the real connector code, against in-memory Plane/Slack APIs."""
import copy
import uuid
import httpx
import pytest
from wayfinder.db import DatabaseManager
from wayfinder.company import plane,slack
from wayfinder.company.freshness import sync_once,watch

PROJECT='P1'; CHANNEL='CFRESH'
MANIFEST={'organization_id':'fresh','synthetic':True,'plane':{'workspace':'ws','project_ids':[PROJECT]},
    'slack':{'channel_ids':[CHANNEL]},'people':[],'identities':{},'teams':[],'components':[],'bindings':[]}


def item(ident,name,body,state='s-todo'):
    return {'id':ident,'name':name,'description_html':f'<p>{body}</p>','state_id':state,'priority':'medium',
            'sequence_id':ident,'assignee_ids':['u1'],'created_by_id':'u1','updated_at':'2026-10-17T00:00:00Z'}


def initial():
    return {'plane':{
        'items':{'i1':item('i1','Ingest Plane','Read every work item.'),'i2':item('i2','Old task','Will be deleted.')},
        'comments':{'i1':[{'id':'c1','comment_html':'<p>First</p>','actor_id':'u1'},
                          {'id':'c2','comment_html':'<p>Second</p>','actor_id':'u1'}],
                    'i2':[{'id':'c3','comment_html':'<p>On the deleted task</p>','actor_id':'u1'}]},
        'pages':{'pg1':{'id':'pg1','name':'ADR','description_html':'<h1>ADR</h1><p>Retry twice.</p>'}}},
     'slack':{'1760000000.000100':[{'ts':'1760000000.000100','text':'Thread A root','user':'U1'},
                                   {'ts':'1760000001.000100','thread_ts':'1760000000.000100','text':'Reply A1','user':'U1'}],
              '1760000100.000100':[{'ts':'1760000100.000100','text':'Thread B root','user':'U1'}]}}


def plane_transport(state):
    def respond(request):
        path=request.url.path.split(f'/workspaces/ws/projects/{PROJECT}/',1)[1].strip('/')
        parts=path.split('/') if path else []
        data=state['plane']
        if not parts: return httpx.Response(200,json={'id':PROJECT,'name':'Freshness'})
        if parts==['members']: return httpx.Response(200,json=[{'member_id':'u1','display_name':'Owner'}])
        if parts==['states']: return httpx.Response(200,json=[{'id':'s-todo','name':'Todo'},{'id':'s-done','name':'Done'}])
        if parts==['work-items']: return httpx.Response(200,json=list(data['items'].values()))
        if parts==['pages']: return httpx.Response(200,json=list(data['pages'].values()))
        if parts[0]=='work-items' and len(parts)==2: return httpx.Response(200,json=data['items'][parts[1]])
        if parts[0]=='pages' and len(parts)==2: return httpx.Response(200,json=data['pages'][parts[1]])
        if parts[0]=='work-items' and parts[2]=='comments': return httpx.Response(200,json=data['comments'].get(parts[1],[]))
        if parts[0]=='work-items' and parts[2] in ('attachments','links'): return httpx.Response(200,json=[])
        return httpx.Response(404)
    return httpx.MockTransport(respond)


def slack_transport(state):
    def respond(request):
        method=request.url.path.rsplit('/',1)[-1]; params=dict(request.url.params)
        threads=state['slack']
        if method=='auth.test': return httpx.Response(200,json={'ok':True})
        if method=='chat.getPermalink':
            return httpx.Response(200,json={'ok':True,'permalink':f"https://demo.slack.com/archives/{CHANNEL}/p{params['message_ts'].replace('.','')}"})
        if method=='conversations.history':
            return httpx.Response(200,json={'ok':True,'messages':[{**m[0],'reply_count':len(m)-1} for m in threads.values()]})
        if method=='conversations.replies':
            if params['ts'] not in threads: return httpx.Response(200,json={'ok':False,'error':'thread_not_found'})
            return httpx.Response(200,json={'ok':True,'messages':threads[params['ts']]})
        return httpx.Response(404)
    return httpx.MockTransport(respond)


@pytest.fixture
def world(monkeypatch):
    db=DatabaseManager(graph_name='test_freshness_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    state=initial()
    monkeypatch.setattr(plane,'PlaneClient',lambda workspace:PlaneReal(workspace,token='test',session=httpx.Client(transport=plane_transport(state))))
    monkeypatch.setattr(slack,'SlackClient',lambda:SlackReal(token='test',
        session=httpx.Client(base_url='https://slack.com/api/',transport=slack_transport(state))))
    yield db,state
    db.clear_graph()


PlaneReal=plane.PlaneClient; SlackReal=slack.SlackClient


def current(db,ident):
    rows=db.graph.ro_query('MATCH (n {id:$id}) RETURN n.valid_to IS NULL,n.text_content,n.state,n.title',params={'id':ident}).result_set
    return rows[0] if rows else None


def test_source_edits_creations_and_deletions_reach_the_graph(world):
    db,state=world
    first=sync_once(db,MANIFEST,slack=True)
    assert first['changes']['summary']['created']>=10
    assert current(db,'plane:fresh:issue:i1')[:1]==[True]

    # Re-reading unchanged sources must not report phantom changes.
    quiet=sync_once(db,MANIFEST,slack=True)['changes']['summary']
    assert quiet=={'created':0,'updated':0,'retired':0,'edges_added':0,'edges_retired':0},quiet

    p=state['plane']; threads=state['slack']
    p['items']['i1'].update(name='Ingest Plane and Slack',state_id='s-done',
        description_html='<p>Read every work item and thread.</p>',updated_at='2026-10-17T01:00:00Z')
    p['comments']['i1']=[p['comments']['i1'][0],{'id':'c4','comment_html':'<p>New comment</p>','actor_id':'u1'}]
    del p['items']['i2']
    p['items']['i3']=item('i3','New task','Created after the first sync.')
    p['pages']['pg1']['description_html']='<h1>ADR</h1><p>Disable automatic retry.</p>'
    a='1760000000.000100'
    threads[a][1]={**threads[a][1],'text':'Reply A1 (edited)','edited':{'ts':'1760000500.000000'}}
    threads[a].append({'ts':'1760000600.000100','thread_ts':a,'text':'Reply A2','user':'U1'})
    del threads['1760000100.000100']

    changes=sync_once(db,MANIFEST,slack=True)['changes']
    created={c['id'] for c in changes['created']}; retired={c['id'] for c in changes['retired']}
    updated={u['id']:u['fields'] for u in changes['updated']}

    assert {'plane:fresh:issue:i3','plane:fresh:comment:c4',f'slack:fresh:comment:{CHANNEL}:1760000600.000100'}<=created
    assert {'plane:fresh:issue:i2','plane:fresh:comment:c2',f'slack:fresh:thread:{CHANNEL}:1760000100.000100',
            f'slack:fresh:comment:{CHANNEL}:1760000100.000100'}<=retired
    # Children of a deleted work item are not left behind as current evidence.
    assert {'plane:fresh:comment:c3','plane:fresh:issue:i2:thread'}<=retired
    assert updated['plane:fresh:issue:i1']['state']==['Todo','Done']
    assert updated['plane:fresh:issue:i1']['title']==['Ingest Plane','Ingest Plane and Slack']
    assert 'content_hash' in updated['plane:fresh:issue:i1']
    assert 'content_hash' in updated['plane:fresh:page:pg1']
    assert 'content_hash' in updated[f'slack:fresh:comment:{CHANNEL}:1760000001.000100']

    assert current(db,'plane:fresh:issue:i1')==[True,'Read every work item and thread.','Done','Ingest Plane and Slack']
    assert current(db,'plane:fresh:issue:i2')[0] is False
    # The previous wording stays retrievable as a source revision.
    revisions=db.graph.ro_query("MATCH (r:SourceRevision {source_unit_id:'plane:fresh:issue:i1'}) RETURN r.text_content").result_set
    assert {r[0] for r in revisions}=={'Read every work item.','Read every work item and thread.'}
    coverage=db.graph.ro_query("MATCH (c:SourceCoverage) WHERE c.status='unavailable' RETURN c.scope").result_set
    assert coverage==[],'A deleted Slack thread must not make the channel unavailable'


def test_watch_logs_each_change_set(world,tmp_path):
    db,state=world; entries=[]
    log=tmp_path/'sync.jsonl'
    watch(db,MANIFEST,interval=0,slack=False,iterations=1,log=log,out=entries.append)
    state['plane']['items']['i1']['state_id']='s-done'
    watch(db,MANIFEST,interval=0,slack=False,iterations=1,log=log,out=entries.append)
    assert entries[0]['summary']['created']>0
    assert entries[1]['summary']['updated']==1 and entries[1]['changes']['updated'][0]['fields']['state']==['Todo','Done']
    assert len(log.read_text(encoding='utf-8').splitlines())==2


def test_channel_system_events_are_not_discussion(world):
    db,state=world
    rename='1760000900.000100'
    state['slack'][rename]=[{'ts':rename,'subtype':'channel_name','text':'has renamed the channel','user':'U1'}]
    sync_once(db,MANIFEST,slack=True)
    assert current(db,f'slack:fresh:comment:{CHANNEL}:{rename}') is None
    assert current(db,f'slack:fresh:thread:{CHANNEL}:{rename}') is None
