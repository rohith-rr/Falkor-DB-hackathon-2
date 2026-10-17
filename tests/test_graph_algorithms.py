"""Native graph algorithms for agents: current graph only, correct results, no leftover projection."""
import asyncio
import uuid
import pytest
from falkordb import FalkorDB
from wayfinder.db import DatabaseManager
from wayfinder.brain_graph import BrainGraph, BrainError
from wayfinder.agent_trace import read
from wayfinder.mcp_server import create_server


@pytest.fixture
def db():
    instance=DatabaseManager(graph_name='test_algo_'+uuid.uuid4().hex)
    if not instance.ping(): pytest.skip('FalkorDB unavailable')
    node=lambda label,ident,**p: instance.upsert_node(label,{'id':ident,'title':ident,**p})
    for i in range(1,5): node('Issue',f'i{i}')
    node('PullRequest','pr'); node('Comment','c'); node('Issue','lonely'); node('Issue','gone',valid_to='2026-01-01')
    edge=lambda a,la,rel,b,lb,**p: instance.upsert_edge(la,a,lb,b,rel,{'id':f'{a}->{rel}->{b}',**p})
    # i2 is the hub: three issues depend on it; the PR implements it and a comment references the PR.
    for i in ('i1','i3','i4'): edge(i,'Issue','DEPENDS_ON','i2','Issue')
    edge('pr','PullRequest','REFERENCES','i2','Issue'); edge('c','Comment','REFERENCES','pr','PullRequest')
    # History that must not count: a retired edge and an edge to a retired node.
    edge('lonely','Issue','REFERENCES','i1','Issue',valid_to='2026-01-01'); edge('gone','Issue','DEPENDS_ON','i1','Issue')
    yield instance
    instance.clear_graph()


def leftovers(db):
    return [g for g in FalkorDB(host=db.host,port=db.port).list_graphs() if g.startswith(db.graph_name+'__algo_')]


def test_pagerank_and_betweenness_rank_the_hub_on_current_records(db):
    brain=BrainGraph(db)
    ranked=brain.invoke('graph_algorithm',algorithm='pagerank',limit=3)
    assert ranked['items'][0]['id']=='i2' and ranked['projection']['entities']==7 and ranked['projection']['relationships']==5
    brokers=brain.invoke('graph_algorithm',algorithm='betweenness')
    assert [i['id'] for i in brokers['items'][:2]]==['i2','pr']
    assert 'gone' not in {i['id'] for i in ranked['items']}
    assert leftovers(db)==[]


def test_components_reveal_isolated_work_and_ignore_retired_links(db):
    result=BrainGraph(db).invoke('graph_algorithm',algorithm='components')
    assert result['component_count']==2 and result['singletons']==1
    assert sorted(m['id'] for m in result['items'][0]['members'])==['c','i1','i2','i3','i4','pr']


def test_shortest_path_returns_entities_and_directed_relationships(db):
    result=BrainGraph(db).invoke('graph_algorithm',algorithm='shortest_path',source_id='c',target_id='i3')
    assert [n['id'] for n in result['items']]==['c','pr','i2','i3'] and result['hops']==3
    assert result['relationships'][-1]=={'type':'DEPENDS_ON','source_id':'i3','target_id':'i2'}
    none=BrainGraph(db).invoke('graph_algorithm',algorithm='shortest_path',source_id='c',target_id='lonely')
    assert none['items']==[] and 'No path' in none['note']


def test_scoping_and_invalid_requests(db):
    brain=BrainGraph(db)
    near=brain.invoke('graph_algorithm',algorithm='pagerank',around_id='c',hops=1)
    assert {i['id'] for i in near['items']}=={'c','pr'}
    only_deps=brain.invoke('graph_algorithm',algorithm='components',relationship_types=['DEPENDS_ON'])
    assert sorted(m['id'] for m in only_deps['items'][0]['members'])==['i1','i2','i3','i4']
    for bad in ({'algorithm':'louvain'},{'algorithm':'shortest_path','source_id':'c'},{'algorithm':'pagerank','hops':9},
                {'algorithm':'shortest_path','source_id':'c','target_id':'gone'},{'algorithm':'pagerank','around_id':'nope'}):
        with pytest.raises(BrainError,match='INVALID_ALGORITHM_REQUEST'): brain.invoke('graph_algorithm',**bad)
    assert leftovers(db)==[]


def test_mcp_tool_is_traced_with_the_path_edges(db,tmp_path):
    server=create_server(db,trace_folder=tmp_path)
    asyncio.run(server.call_tool('graph_algorithm',{'algorithm':'shortest_path','source_id':'c','target_id':'i3'}))
    entry=read(db.graph_name,folder=tmp_path)['entries'][0]
    assert entry['tool']=='graph_algorithm' and {'c','pr','i2','i3'}<=set(entry['nodes'])
    assert ['i3','DEPENDS_ON','i2'] in entry['edges']
