"""Agent tool calls are traced for the path view without changing results; the dashboard serves graph and trace."""
import asyncio
import json
import uuid
import pytest
from starlette.testclient import TestClient
from wayfinder import agent_trace
from wayfinder.agent_trace import Tracer,touched,read
from wayfinder.db import DatabaseManager
from wayfinder.company.fixture import ingest_fixture
from wayfinder.dashboard import create_app
from wayfinder.mcp_server import create_server


def test_touched_collects_entities_and_relationships_but_not_context():
    nodes,edges=touched({'items':[{'n':{'id':'plane:o:issue:1','labels':['Issue']},
        'r':{'id':'x','type':'REFERENCES','source_id':'slack:o:comment:C1:1','target_id':'plane:o:issue:1'}}],
        'evidence':['github:o:pr:a/b:4->HAS_COMMIT->github:o:commit:a/b:c1@4f2a','not-an-id','plain text'],
        'context':{'source_scope':[{'id':'coverage:o:plane:project:p'}]}})
    assert nodes==['plane:o:issue:1','slack:o:comment:C1:1','github:o:pr:a/b:4','github:o:commit:a/b:c1']
    assert edges==[['slack:o:comment:C1:1','REFERENCES','plane:o:issue:1'],['github:o:pr:a/b:4','HAS_COMMIT','github:o:commit:a/b:c1']]


def test_trace_sessions_and_disabled_tracer(tmp_path):
    first=Tracer('g',folder=tmp_path); second=Tracer('g',folder=tmp_path)
    first.record('get_entity',{'entity_id':'plane:o:issue:1'},{'n':{'id':'plane:o:issue:1','labels':['Issue']}})
    second.record('get_neighbors',{'entity_id':'plane:o:issue:2'},error='BrainError: too large')
    latest=read('g',folder=tmp_path)
    assert latest['session']==second.session and [e['tool'] for e in latest['entries']]==['get_neighbors']
    assert latest['entries'][0]['ok'] is False and latest['entries'][0]['focus']==['plane:o:issue:2']
    assert read('g',session=first.session,folder=tmp_path)['entries'][0]['nodes']==['plane:o:issue:1']
    assert read('g',session=first.session,after=1,folder=tmp_path)['entries']==[]
    assert Tracer('g',folder=tmp_path,enabled=False).record('x',{})is None
    assert len(latest['sessions'])==2


@pytest.fixture
def db():
    instance=DatabaseManager(graph_name='test_trace_'+uuid.uuid4().hex)
    if not instance.ping(): pytest.skip('FalkorDB unavailable')
    ingest_fixture(instance)
    yield instance
    instance.clear_graph()


def test_mcp_tools_record_calls_and_return_unchanged_results(db,tmp_path):
    server=create_server(db,trace_folder=tmp_path)
    content=asyncio.run(server.call_tool('get_entity',{'entity_id':'company:northstar:component:resource-allocation'}))
    with pytest.raises(Exception): asyncio.run(server.call_tool('get_entity',{'entity_id':'not-an-entity'}))
    entries=read(db.graph_name,folder=tmp_path)['entries']
    assert [e['tool'] for e in entries]==['get_entity','get_entity'] and [e['ok'] for e in entries]==[True,False]
    assert entries[0]['focus']==['company:northstar:component:resource-allocation']
    assert 'company:northstar:component:resource-allocation' in json.dumps(content,default=str)
    untraced=tmp_path/'off'
    asyncio.run(create_server(db,trace=False,trace_folder=untraced).call_tool('graph_stats',{}))
    assert not untraced.exists()


def test_dashboard_serves_graph_and_trace_with_entity_details(db,tmp_path,monkeypatch):
    monkeypatch.setattr(agent_trace,'FOLDER',tmp_path)
    tracer=Tracer(db.graph_name,folder=tmp_path)
    tracer.record('get_entity',{'entity_id':'company:northstar:component:resource-allocation'},{})
    with TestClient(create_app(db)) as client:
        assert client.get('/graph').status_code==200 and client.get('/assets/graph.js').status_code==200
        graph=client.get('/api/graph').json()
        ids={n['id'] for n in graph['nodes']}
        assert 'company:northstar:component:resource-allocation' in ids and graph['edges']
        assert all(e['source'] in ids and e['target'] in ids for e in graph['edges'])
        assert not any(n['label'] in ('SourceRevision','SourceCoverage','File') for n in graph['nodes'])
        trace=client.get('/api/trace').json()
        assert trace['session']==tracer.session and len(trace['entries'])==1
        assert trace['nodes']['company:northstar:component:resource-allocation']['label']=='Component'
        assert client.get('/api/trace',params={'after':'1'}).json()['entries']==[]
