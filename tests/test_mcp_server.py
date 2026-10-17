import asyncio
import os
import json
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace

import pytest
from wayfinder.brain_graph import BrainGraph, BrainError, MAX_BYTES
from wayfinder.db import DatabaseManager
from wayfinder.mcp_server import create_server


@pytest.fixture
def brain():
    db = DatabaseManager(graph_name="wayfinder_mcp_test_" + uuid.uuid4().hex)
    if not db.ping():
        pytest.skip("FalkorDB unavailable")
    db.query("""CREATE (p:Person {id:'p',login:'alice'}), (bot:Person {id:'bot',login:'robot',is_bot:true}),
        (d:Directory {id:'d',path:'pkg/kubelet',repo:'kubernetes/kubernetes'}),
        (sub:Directory {id:'sub',path:'pkg/kubelet/sub',repo:'kubernetes/kubernetes'}),
        (pr:PullRequest {id:'pr',title:'Resize',repo:'kubernetes/kubernetes',created_at:'2026-01-02T00:00:00+00:00'}),
        (t:Thread {id:'thread'}), (c:Comment {id:'comment',body_text:'Why resize?',created_at:'2026-01-02T00:00:00+00:00',source_url:'https://example.com/pr#comment'}),
        (q:Question {id:'question',summary:'Why resize?',evidence_quote:'Why resize?'}),
        (k:KEP {id:'kep',number:'1287',title:'Resize'}), (r:Rationale {id:'rationale',summary:'Resize without restart',evidence_quote:'Resize without restart',source_url:'https://example.com/kep'}),
        (o:OwnersFile {id:'owners',path:'pkg/OWNERS'}), (root:Directory {id:'root',path:'pkg',repo:'kubernetes/kubernetes'}),
        (orphan:Person {id:'orphan',login:'alice'}),
        (p)-[:EXPERT_IN {id:'expert',score:2,evidence_ids:['comment']}]->(d),
        (p)-[:EXPERT_IN {id:'expert-sub',score:1,evidence_ids:['comment']}]->(sub),
        (bot)-[:EXPERT_IN {id:'bot-expert',score:100}]->(d),
        (sub)-[:CHILD_OF {id:'child'}]->(d), (d)-[:CHILD_OF {id:'parent'}]->(root),
        (p)-[:AUTHORED {id:'author'}]->(c), (t)-[:ON {id:'on'}]->(pr),
        (t)-[:HAS_COMMENT {id:'comment-rel'}]->(c), (c)-[:ASSERTS {id:'assert',logical_id:'assert-logical',evidence_quote:'Why resize?',authority:'EXTRACTED'}]->(q),
        (k)-[:HAS_RATIONALE {id:'rationale-rel'}]->(r),
        (o)-[:COVERS {id:'covers'}]->(root), (p)-[:APPROVER_IN {id:'approver'}]->(o)""")
    yield BrainGraph(db)
    db.clear_graph()


def test_navigation_evidence_and_company_tools(brain):
    assert brain.search_entities("kubelet", labels=["Directory"])["count"] == 2
    entity = brain.get_entity("comment")
    assert entity["entity"]["properties"]["source_url"].endswith("#comment")
    assert brain.get_neighbors("comment", relationship_types=["ASSERTS"])["items"][0]["m"]["id"] == "question"
    path = brain.find_paths("p", "question", ["AUTHORED", "ASSERTS"])["items"][0]["p"]
    assert [n["id"] for n in path["nodes"]] == ["p", "comment", "question"]
    assert path["relationships"][0]["source_id"] == "p"
    assert brain.find_paths("orphan", "question")["count"] == 0
    evidence = brain.get_evidence(["assert-logical", "comment"])
    assert evidence["count"] == 2
    claim = brain.get_evidence(["question"])["items"][0]
    assert claim["source"]["id"] == "comment" and claim["author"] == "alice"
    assert brain.get_evidence(["missing"])["items"][0]["missing"] is True
    assert brain.get_thread("pr")["count"] == 1
    assert brain.list_questions(target_id="pr", answered=False)["count"] == 1
    assert brain.list_questions(target_id="kep")["count"] == 0
    assert brain.component_context("pkg/kubelet")["count"] > 0
    assert brain.person_context("p")["count"] > 0
    with pytest.raises(BrainError):
        brain.person_context("alice")
    assert brain.decisions("1287")["count"] == 1
    assert brain.ownership("pkg/kubelet")["items"][0]["activity"].startswith("Unknown")
    experts = brain.who_knows("pkg/kubelet")
    assert experts["count"] == 1 and len(experts["items"][0]["matches"]) == 2
    assert brain.recent_activity("2026-01-01", "2026-01-03")["count"] == 2
    assert brain.recent_activity("2026-01-01", "2026-01-03", entity_id="p")["count"] == 1
    assert brain.graph_schema()["labels"]["count"] > 0
    assert brain.graph_stats()["totals"][0]["retained_nodes"] > 0
    assert brain.knowledge_coverage()["limitations"]
    assert brain.stale_findings()["count"] == 0
    assert brain.concerns("pr")["count"] == 0


def test_pagination_history_and_limits(brain):
    first = brain.search_entities("kubelet", limit=1)
    second = brain.search_entities("kubelet", limit=1, cursor=first["next_cursor"])
    assert first["items"][0]["n"]["id"] != second["items"][0]["n"]["id"]
    assert second["next_cursor"] is None
    with pytest.raises(BrainError, match="INVALID_CURSOR"):
        brain.search_entities("different", cursor=first["next_cursor"])
    brain.db.query("MATCH ()-[r {id:'assert'}]->() SET r.valid_to='2026-01-03'")
    assert brain.get_neighbors("comment", relationship_types=["ASSERTS"])["count"] == 0
    assert brain.get_neighbors("comment", relationship_types=["ASSERTS"], include_history=True)["count"] == 1
    with pytest.raises(BrainError):
        brain.find_paths("p", "d", max_hops=6)
    with pytest.raises(BrainError):
        brain.search_entities("x", limit=101)
    with pytest.raises(BrainError, match="ENTITY_NOT_FOUND"):
        brain.get_entity("absent")
    brain.db.query("MATCH (c:Comment) SET c.body_text=$text", {"text": "é" * 20000})
    segment = brain.get_entity("comment", text_field="body_text", text_offset=8000)
    assert segment["text_segment"]["next_offset"] == 12000
    assert len(json.dumps(brain.invoke("get_entity", entity_id="comment")).encode()) < MAX_BYTES


def test_thread_combines_reviews_and_comments_with_global_pagination(brain):
    brain.db.query("MATCH (pr:PullRequest),(p:Person {id:'p'}) CREATE (r:Review {id:'review',body_text:'Approved',submitted_at:'2026-01-01T00:00:00+00:00'}), (r)-[:REVIEWS {id:'rv'}]->(pr), (p)-[:SUBMITTED {id:'submitted'}]->(r)")
    first = brain.get_thread('pr', limit=1)
    assert first['items'][0]['n']['id'] == 'review'
    second = brain.get_thread('pr', limit=1, cursor=first['next_cursor'])
    assert second['items'][0]['n']['id'] == 'comment' and second['next_cursor'] is None


def test_owner_inheritance_barrier_and_answer_state(brain):
    brain.db.query("MATCH (d:Directory {id:'d'}),(p:Person {id:'p'}) CREATE (o:OwnersFile {id:'local-owner',path:'pkg/kubelet/OWNERS',no_parent_owners:true}), (o)-[:COVERS {id:'local-covers'}]->(d), (p)-[:REVIEWER_IN {id:'local-reviewer'}]->(o)")
    owners = brain.ownership('pkg/kubelet/sub')['items']
    assert len(owners) == 1 and owners[0]['owners_file'] == 'pkg/kubelet/OWNERS'
    brain.db.query("MATCH (q:Question),(c:Comment) CREATE (c)-[:ANSWERS {id:'answer'}]->(q)")
    assert brain.list_questions(answered=True)['count'] == 1
    assert brain.list_questions(answered=False)['count'] == 0
    brain.db.query("MATCH ()-[r {id:'answer'}]->() SET r.valid_to='2026-01-03'")
    assert brain.list_questions(answered=False)['count'] == 1


def test_byte_budget_preserves_continuation(brain):
    brain.db.query("UNWIND range(1,5) AS i CREATE (:DocPage {id:'doc-'+toString(i),title:'large',a:$text,b:$text,c:$text})", {'text': 'é'*3000})
    first = brain.search_entities('large', limit=5)
    assert first['count'] < 5 and first['next_cursor']
    assert len(json.dumps(first).encode()) < MAX_BYTES
    second = brain.search_entities('large', limit=5, cursor=first['next_cursor'])
    assert first['items'][0]['n']['id'] != second['items'][0]['n']['id']


@pytest.mark.parametrize("query", ["CREATE (:Danger)", "MATCH (n) SET n.bad=true", "MATCH (n) DELETE n", "MERGE (:Danger {id:'bad'})", "CALL db.idx.fulltext.createNodeIndex('Person','login')"])
def test_database_enforces_read_only(brain, query):
    before = brain.read_cypher("MATCH (n) RETURN count(n) AS count")
    with pytest.raises(BrainError):
        brain.read_cypher(query)
    assert brain.read_cypher("MATCH (n) RETURN count(n) AS count") == before


def test_timeout_connection_errors_and_transport_schema():
    class Broken:
        def ro_query(self, *args, **kwargs):
            assert kwargs["timeout"] == 5000
            raise RuntimeError("Query timed out secret source")
    brain = BrainGraph(SimpleNamespace(graph=Broken()))
    with pytest.raises(BrainError, match="QUERY_TIMEOUT"):
        brain.read_cypher("RETURN 1")
    tools = asyncio.run(create_server().list_tools())
    assert len(tools) == 23
    search = next(t for t in tools if t.name == "search_entities")
    assert search.inputSchema["properties"]["limit"]["type"] == "integer"
    assert all(t.annotations.readOnlyHint for t in tools)
    class Disconnected:
        def ro_query(self, *args, **kwargs):
            raise RuntimeError('Connection refused')
    with pytest.raises(BrainError, match='DATABASE_UNAVAILABLE'):
        BrainGraph(SimpleNamespace(graph=Disconnected())).read_cypher('RETURN 1')


@pytest.mark.asyncio
async def test_stdio_protocol_from_other_directory(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(command=sys.executable, args=[str(root / "wayfinder" / "mcp_server.py")], cwd=str(tmp_path),
                                    env={**os.environ, "WAYFINDER_TRACE": "0"})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            response = await session.initialize()
            assert "coverage" in response.instructions
            assert len((await session.list_tools()).tools) == 23
            assert len((await session.list_resources()).resources) == 2
            assert len((await session.list_prompts()).prompts) == 3
            result = await session.call_tool("read_cypher", {"query": "RETURN $value AS value", "parameters": {"value": 42}})
            assert not result.isError
            assert result.structuredContent["items"] == [{"value": 42}]
            bad = await session.call_tool("get_entity", {"entity_id": "not-an-entity"})
            assert bad.isError
