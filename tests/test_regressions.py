from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid
import pytest

from wayfinder.db import DatabaseManager
from wayfinder.derived.concerns import ConcernResolver
from wayfinder.derived.expertise import ExpertiseCalculator
from wayfinder.derived.graph_algos import GraphAlgorithms
from wayfinder.derived.stale_findings import StaleDetector
from wayfinder.extraction.cache import ExtractionCache
from wayfinder.extraction.extractor import ExtractionPipeline
from wayfinder.extraction.llm_client import OpenAPIClient
from wayfinder.extraction.verifier import QuoteVerifier
from wayfinder.github.activity import ActivityProcessor
from wayfinder.github.client import GitHubClient
from wayfinder.parsers.gate_parser import GateParser
from wayfinder.parsers.owners_parser import OwnersParser
from wayfinder.query import WayfinderBrainQuery
from wayfinder.sync.incremental import IncrementalSync, apply_prow_cancellations
from wayfinder.sync.pipeline import PopulationPipeline
from wayfinder.sync.seed_data import SAMPLE_GITHUB_ACTIVITY


def stamp(days=0):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.fixture
def db():
    instance = DatabaseManager(graph_name='wayfinder_regression_' + uuid.uuid4().hex)
    if not instance.ping():
        pytest.skip('FalkorDB is unavailable')
    yield instance
    instance.clear_graph()


def scalar(db, query, params=None):
    return db.query(query, params).result_set[0][0]


def node(db, label, ident, **props):
    db.upsert_node(label, dict(id=ident, **props))


def edge(db, sl, sid, tl, tid, rel, **props):
    at = props.pop('valid_from', stamp())
    db.upsert_edge(sl, sid, tl, tid, rel, dict(id=f'{sid}->{rel}->{tid}', valid_from=at, observed_at=at, **props))


def test_vector_values_and_relationship_history(db):
    db.query("CREATE VECTOR INDEX FOR (n:Argument) ON (n.embedding) OPTIONS {dimension: 3, similarityFunction: 'cosine'}")
    db.batch_upsert_nodes('Argument', [{'id': 'arg', 'embedding': [1.0, 0.0, 0.0]}])
    assert scalar(db, "CALL db.idx.vector.queryNodes('Argument', 'embedding', 3, vecf32([1.0,0.0,0.0])) YIELD node RETURN count(node)") == 1
    node(db, 'Person', 'p', login='p')
    node(db, 'Directory', 'd', path='pkg')
    edge(db, 'Person', 'p', 'Directory', 'd', 'EXPERT_IN', score=3, valid_from='2025-01-01T00:00:00+00:00')
    edge(db, 'Person', 'p', 'Directory', 'd', 'EXPERT_IN', score=5, valid_from='2025-02-01T00:00:00+00:00')
    assert WayfinderBrainQuery(db).query_who_knows('pkg', as_of='2025-01-15T00:00:00+00:00')[0]['score'] == 3
    assert WayfinderBrainQuery(db).query_who_knows('pkg')[0]['score'] == 5
    edge(db, 'Person', 'p', 'Directory', 'd', 'EXPERT_IN', score=5, valid_from='2025-03-01T00:00:00+00:00')
    assert scalar(db, 'MATCH ()-[r:EXPERT_IN]->() RETURN count(r)') == 2
    db.query('MATCH ()-[r:EXPERT_IN]->() WHERE r.valid_to IS NULL SET r.valid_to = $at', {'at': '2025-04-01T00:00:00+00:00'})
    edge(db, 'Person', 'p', 'Directory', 'd', 'EXPERT_IN', score=5, valid_from='2025-05-01T00:00:00+00:00')
    assert scalar(db, 'MATCH ()-[r:EXPERT_IN]->() WHERE r.valid_to IS NULL RETURN count(r)') == 1


def test_resolution_requires_later_actual_evidence(db):
    db.query("CREATE (p:Person {id:'p'}), (a:Argument {id:'a',kind:'concern',source_timestamp:'2025-01-02'}), "
             "(pr:PullRequest {id:'pr'}), (p)-[:MADE]->(a), (a)-[:RAISES_CONCERN_ON]->(pr)")
    resolver = ConcernResolver(db)
    assert resolver.resolve_concern_statuses()['open'] == 1
    db.query("MATCH (p:Person),(pr:PullRequest) CREATE (p)-[:APPROVED {at:'2025-01-01'}]->(pr)")
    assert resolver.resolve_concern_statuses()['open'] == 1
    db.query("MATCH (p:Person),(pr:PullRequest) CREATE (s:Argument {source_timestamp:'2025-01-03'}), (p)-[:MADE]->(s), (s)-[:SUPPORTS]->(pr)")
    assert resolver.resolve_concern_statuses()['open'] == 1


def test_directory_experts_include_descendants_with_path_boundaries_and_time_travel(db):
    node(db, 'Person', 'expert', login='expert')
    node(db, 'Directory', 'subdir', path='pkg/kubelet/prober')
    node(db, 'Directory', 'similar', path='pkg/kubelet-extra')
    edge(db, 'Person', 'expert', 'Directory', 'subdir', 'EXPERT_IN',
         score=2, evidence_ids=['review'], valid_from='2025-01-01T00:00:00+00:00')
    edge(db, 'Person', 'expert', 'Directory', 'similar', 'EXPERT_IN',
         score=100, evidence_ids=['other'])
    query = WayfinderBrainQuery(db)
    results = query.query_who_knows('pkg/kubelet')
    assert len(results) == 1
    assert results[0]['target_name'] == 'pkg/kubelet/prober'
    assert results[0]['evidence_ids'] == ['review']
    db.query("MATCH ()-[r:EXPERT_IN]->(:Directory {id:'subdir'}) SET r.valid_to = '2025-02-01T00:00:00+00:00'")
    assert query.query_who_knows('pkg/kubelet') == []
    assert len(query.query_who_knows('pkg/kubelet', as_of='2025-01-15T00:00:00+00:00')) == 1


def test_legacy_prow_bot_is_removed_from_expertise_and_unlisted_experts(db):
    node(db, 'Person', 'bot', login='kubernetes-prow', is_bot=False)
    node(db, 'Person', 'human', login='human', is_bot=False)
    node(db, 'Directory', 'dir', path='pkg/kubelet', repo='kubernetes/kubernetes')
    node(db, 'File', 'file', path='pkg/kubelet/test.go')
    node(db, 'PullRequest', 'pr')
    node(db, 'Thread', 'thread')
    edge(db, 'PullRequest', 'pr', 'File', 'file', 'TOUCHES')
    edge(db, 'Directory', 'dir', 'File', 'file', 'CONTAINS')
    edge(db, 'Thread', 'thread', 'PullRequest', 'pr', 'ON')
    node(db,'Person','author',login='author')
    edge(db,'Person','author','PullRequest','pr','AUTHORED')
    for person in ['bot', 'human']:
        node(db, 'Comment', person+'-comment', body_text='Memory reservations isolate workloads.', created_at=stamp(),
             kind='review_line',file_path='pkg/kubelet/test.go',pr_id='pr')
        edge(db, 'Person', person, 'Comment', person+'-comment', 'AUTHORED')
        edge(db, 'Thread', 'thread', 'Comment', person+'-comment', 'HAS_COMMENT')
    edge(db, 'Person', 'bot', 'Directory', 'dir', 'EXPERT_IN', score=100, evidence_ids=['old'])
    edge(db, 'Person', 'bot', 'Directory', 'dir', 'UNLISTED_EXPERT', score=100)
    calc = ExpertiseCalculator(db)
    assert calc.calculate_directory_expertise()['expert_edges_created'] == 1
    assert scalar(db, "MATCH (p:Person {id:'bot'}) RETURN p.is_bot") is True
    assert [r['login'] for r in WayfinderBrainQuery(db).query_who_knows('pkg/kubelet')] == ['human']
    calc.detect_unlisted_experts()
    assert scalar(db, "MATCH (:Person {id:'bot'})-[r:EXPERT_IN|UNLISTED_EXPERT]->() WHERE r.valid_to IS NULL RETURN count(r)") == 0
    assert [r['login'] for r in WayfinderBrainQuery(db).query_ownership('pkg/kubelet')['unlisted_experts']] == ['human']


class FakeLLM:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0
    def chat_completion_json(self, **kwargs):
        self.calls += 1
        return deepcopy(self.payload)
    def get_embedding(self, text):
        return [1.0, 0.0, 0.0]


def extractor(db, tmp_path, payload):
    llm = FakeLLM(payload)
    return ExtractionPipeline(db=db, llm_client=llm, cache=ExtractionCache(cache_dir=tmp_path)), llm


def test_answer_cannot_use_a_comment_as_question_target(db, tmp_path):
    text = 'The manager uses reservations to isolate memory.'
    node(db, 'Comment', 'current', body_text=text, source_url='https://example.test/current')
    node(db, 'Comment', 'previous', body_text='Earlier comment')
    pipeline, _ = extractor(db, tmp_path, {'answers': [{'question_id': 'previous', 'evidence_quote': text}]})
    result = pipeline.process_text_unit('Comment', 'current', text, context_target_ids=['previous'])
    assert result['accepted'] == 0 and result['rejected'] == 1
    assert pipeline.stats.invalid_targets == 1
    assert pipeline.stats.rejection_reasons == ['answers: invalid target or question ID']
    assert scalar(db, 'MATCH ()-[r:ANSWERS]->() RETURN count(r)') == 0


def test_pilot_budget_skips_old_and_completed_text_without_model_calls(db, tmp_path):
    from wayfinder.pilot import PilotExtraction
    llm = FakeLLM({})
    def pilot():
        instance = PilotExtraction(1, stamp(30))
        instance.db, instance.llm = db, llm
        instance.cache = ExtractionCache(cache_dir=tmp_path)
        return instance
    text = 'The manager isolates workloads using reservations.'
    for ident in ['old', 'recent', 'next']:
        node(db, 'Comment', ident, source_url=f'https://example.test/{ident}', body_text=text)
    first = pilot()
    assert first.process_text_unit('Comment', 'old', text, source_timestamp=stamp(40))['skipped']
    assert llm.calls == 0
    first.process_text_unit('Comment', 'recent', text, source_timestamp=stamp(1))
    assert llm.calls == 1
    assert first.process_text_unit('Comment', 'next', text, source_timestamp=stamp(1))['skipped']
    second = pilot()
    assert second.process_text_unit('Comment', 'recent', text, source_timestamp=stamp(1))['skipped']
    assert second.attempts == 0
    second.process_text_unit('Comment', 'next', text, source_timestamp=stamp(1))
    assert llm.calls == 2


def test_extraction_schema_idempotence_topics_edits_and_provenance(db, tmp_path):
    text = 'The memory manager maintains isolation through reservations.'
    node(db, 'Comment', 'c', source_url='https://github.com/kubernetes/kubernetes/issues/1#issuecomment-1', source_ref='comment:1', created_at=stamp(45), body_text=text)
    node(db, 'Person', 'p', login='p')
    payload = {'arguments': [{'kind': 'explanation', 'summary': 'Reservations isolate memory', 'evidence_quote': text, 'topics': ['memory']},
                              {'kind': 'invented', 'summary': 'invalid', 'evidence_quote': text}],
               'questions': [{'summary': 'How does isolation work?', 'evidence_quote': text, 'topics': ['memory']}]}
    pipeline, llm = extractor(db, tmp_path, payload)
    result = pipeline.process_text_unit('Comment', 'c', text, author_id='p')
    assert result['accepted'] == 2 and result['rejected'] == 1
    assert pipeline.stats.schema_failures == 1
    assert pipeline.process_text_unit('Comment', 'c', text, author_id='p')['skipped']
    assert llm.calls == 1
    assert scalar(db, 'MATCH (a:Argument) WHERE a.valid_to IS NULL RETURN count(a)') == 1
    assert scalar(db, "MATCH (a:Argument)-[:ABOUT_TOPIC]->(:Topic) RETURN count(a)") == 1
    assert scalar(db, 'MATCH (q:Question) RETURN q.asked_at') == scalar(db, 'MATCH (c:Comment) RETURN c.created_at')
    assert scalar(db, 'MATCH (a:Argument) RETURN a.source_url').startswith('https://github.com/')
    assert ExpertiseCalculator(db).calculate_topic_expertise()['topic_expert_edges_created'] == 1
    assert StaleDetector(db).check_unanswered_questions() == 1
    llm.payload = {'arguments': [{'kind': 'explanation', 'summary': 'Updated claim', 'evidence_quote': text}]}
    node(db,'Comment','c',body_text=text+' Updated implementation.')
    result = pipeline.process_text_unit('Comment', 'c', text + ' Updated implementation.', author_id='p')
    assert result['rejected'] == 0
    assert scalar(db, 'MATCH (a:Argument) WHERE a.valid_to IS NULL RETURN count(a)') == 1
    assert scalar(db, 'MATCH (a:Argument) WHERE a.valid_to IS NOT NULL RETURN count(a)') == 1
    assert scalar(db, 'MATCH (c:Comment)-[r:ASSERTS]->() WHERE r.valid_to IS NULL RETURN count(r)') == 1
    # Returning to earlier text creates an active interpretation without reopening history.
    node(db,'Comment','c',body_text=text)
    result = pipeline.process_text_unit('Comment', 'c', text, author_id='p')
    assert result['accepted'] == 2
    assert scalar(db, 'MATCH (a:Argument) WHERE a.valid_to IS NULL RETURN count(a)') == 1


def test_secondary_targets_answers_and_rationales(db, tmp_path):
    text = 'This resolves the concern and answers the existing question.'
    for label, ident in [('Comment','c'), ('Argument','unallowed'), ('Question','q'), ('Decision','old'), ('KEP','k8s:kep:1'), ('KEP','other')]:
        node(db, label, ident, source_url='https://example.org/source', source_ref=ident, created_at=stamp(40))
    db.query("MATCH (d:Decision {id:'old'}) SET d.target_id = 'other'")
    db.query("MATCH (c:Comment {id:'c'}),(q:Question {id:'q'}) CREATE (t:Thread), (previous:Comment), "
             "(t)-[:HAS_COMMENT]->(c), (t)-[:HAS_COMMENT]->(previous), (previous)-[:ASSERTS]->(q)")
    payload = {'arguments': [{'kind':'support','summary':'support','evidence_quote':text,'addresses_target_id':'unallowed'}],
               'decisions':[{'scope':'KEP','summary':'new decision','evidence_quote':text,'target_id':'k8s:kep:1','supersedes_id':'old'}],
               'answers':[{'question_id':'q','evidence_quote':text}]}
    pipeline, _ = extractor(db, tmp_path, payload)
    result = pipeline.process_text_unit('Comment', 'c', text, context_target_ids=['q','old','k8s:kep:1'])
    assert result == dict(accepted=1, rejected=2, rejection_rate=2/3)
    assert scalar(db, 'MATCH ()-[r:ADDRESSES|SUPERSEDES]->() RETURN count(r)') == 0
    assert scalar(db, 'MATCH (:Comment)-[r:ANSWERS]->(:Question) RETURN count(r)') == 1
    node(db, 'KEPSection', 'section', source_url='https://example.org/README.md', source_ref='README#motivation')
    payload = {'rationales':[{'kind':'motivation','summary':'because','evidence_quote':text}]}
    pipeline, _ = extractor(db, tmp_path / 'rationale', payload)
    assert pipeline.process_text_unit('KEPSection','section',text,parent_kep_id='k8s:kep:1')['accepted'] == 1
    assert len(WayfinderBrainQuery(db).query_decisions_and_rationale('k8s:kep:1')['rationales']) == 1


def test_cancellation_reapproval_and_expiring_expertise(db):
    db.query("CREATE (p:Person {id:'p',login:'p'}), (pr:PullRequest {id:'pr'}), (t:Thread), (t)-[:ON]->(pr), "
             "(c:Comment {created_at:'2025-02-01',is_command:true,commands:['approve cancel']}), (p)-[:AUTHORED]->(c), (t)-[:HAS_COMMENT]->(c)")
    edge(db,'Person','p','PullRequest','pr','APPROVED', at='2025-01-01', valid_from='2025-01-01')
    edge(db,'Person','p','PullRequest','pr','APPROVED', at='2025-03-01', valid_from='2025-03-01')
    apply_prow_cancellations(db)
    rows = db.query('MATCH ()-[r:APPROVED]->() RETURN r.at, r.valid_to ORDER BY r.at').result_set
    assert rows == [['2025-01-01','2025-02-01'], ['2025-03-01',None]]
    assert apply_prow_cancellations(db) == 0
    db.query("MATCH (pr:PullRequest) CREATE (d:Directory {id:'d',path:'pkg',repo:'repo'}), (f:File), (pr)-[:TOUCHES]->(f), (d)-[:CONTAINS]->(f)")
    edge(db,'Person','p','PullRequest','pr','APPROVED',at=stamp(1),valid_from=stamp(1))
    calc = ExpertiseCalculator(db)
    assert calc.calculate_directory_expertise()['expert_edges_created'] == 0
    from wayfinder.derived.expertise_v2 import get_rankings
    assert get_rankings(db,'pkg')['involvement_only']
    db.query('MATCH ()-[r:APPROVED]->() WHERE r.valid_to IS NULL SET r.valid_to = $at', {'at':stamp()})
    assert calc.calculate_directory_expertise()['expert_edges_created'] == 0
    assert WayfinderBrainQuery(db).query_who_knows('pkg') == []


def test_stale_lifecycle_latest_stage_and_question_age(db):
    db.query("CREATE (d:DocPage {id:'doc'}), (g:FeatureGate {id:'gate'}), "
             "(d)-[:DOCUMENTS {stated_stage:'Beta'}]->(g), "
             "(r1:Release {version:'v1.9'}), (r2:Release {version:'v1.10'}), "
             "(g)-[:STAGE_IN {stage:'Alpha'}]->(r1), (g)-[:STAGE_IN {stage:'Beta'}]->(r2)")
    detector = StaleDetector(db)
    assert detector.check_doc_stage_mismatch() == 0
    db.query("MATCH ()-[r:DOCUMENTS]->() SET r.stated_stage = 'Alpha'")
    assert detector.check_doc_stage_mismatch() == detector.check_doc_stage_mismatch() == 1
    assert scalar(db, 'MATCH (f:StaleFinding) RETURN count(f)') == 1
    db.query("MATCH ()-[r:DOCUMENTS]->() SET r.stated_stage = 'Beta'")
    assert detector.check_doc_stage_mismatch() == 0
    assert scalar(db, 'MATCH (f:StaleFinding) WHERE f.resolved_at IS NOT NULL RETURN count(f)') == 1
    assert WayfinderBrainQuery(db).query_stale_findings() == []


def test_inherited_owners_and_paper_owner_reset(db):
    parsed = OwnersParser.parse_owners_file('approvers: [alice]', 'kubernetes/kubernetes', 'pkg/OWNERS')
    for group in ['directories','people']:
        for n in parsed[group]:
            db.upsert_node(n.__class__.__name__, n.to_cypher_props())
    db.upsert_node('OwnersFile', parsed['owners_node'].to_cypher_props())
    for e in parsed['edges']:
        db.upsert_edge(e['source_label'],e['source_id'],e['target_label'],e['target_id'],e['rel_type'],e['props'])
    node(db,'Directory','child',path='pkg/child',repo='kubernetes/kubernetes')
    edge(db,'Directory','child','Directory','k8s:dir:kubernetes/kubernetes:pkg','CHILD_OF')
    query = WayfinderBrainQuery(db)
    assert query.query_ownership('pkg/child')['declared_owners'][0]['login'] == 'alice'
    calc = ExpertiseCalculator(db)
    assert calc.detect_paper_owners() == []
    assert query.query_ownership('pkg/child')['declared_owners'][0]['paper_owner'] is None
    from wayfinder.derived.activity_coverage import record_activity_coverage
    record_activity_coverage(db, stamp(calc.window_days + 1), stamp())
    assert len(calc.detect_paper_owners()) == 1
    db.query("MATCH (p:Person),(d:Directory {id:'child'}) CREATE (pr:PullRequest), (f:File), (pr)-[:TOUCHES]->(f), "
             "(d)-[:CONTAINS]->(f), (p)-[:APPROVED {at:$at}]->(pr)", {'at': stamp()})
    assert calc.detect_paper_owners() == []
    node(db,'OwnersFile','block',no_parent_owners=True)
    edge(db,'OwnersFile','block','Directory','child','COVERS')
    assert query.query_ownership('pkg/child')['declared_owners'] == []


def test_saved_graph_repair_preserves_semantics_and_resolves_false_inactivity(db):
    from wayfinder.parsers.kep_parser import KepParser
    from wayfinder.repair import repair_saved_graph
    text = ('## Motivation\nResources may need to change\nwithout restarting containers.\n\n'
            '## Alternatives\n<!-- Describe rejected alternatives here. -->')
    parsed = KepParser.parse_kep_readme(text, '1')
    node(db, 'KEP', 'k8s:kep:1', number='1')
    for section in parsed['sections']:
        db.upsert_node('KEPSection', section.to_cypher_props())
    for relation in parsed['edges']:
        if relation['rel_type'] == 'HAS_SECTION':
            db.upsert_edge(relation['source_label'], relation['source_id'], relation['target_label'],
                           relation['target_id'], relation['rel_type'], relation['props'])
    node(db, 'Rationale', 'k8s:rationale:1:motivation', kind='motivation',
         summary='Resources may need to change', evidence_quote='Resources may need to change')
    node(db, 'Rationale', 'k8s:rationale:1:alternatives', kind='alternative_rejected',
         summary='<!', evidence_quote='<!--')
    edge(db, 'KEP', 'k8s:kep:1', 'Rationale', 'k8s:rationale:1:alternatives', 'HAS_RATIONALE')
    node(db, 'Rationale', 'k8s:rationale:semantic', kind='motivation', summary='LLM evidence')
    node(db, 'Person', 'owner', login='alice')
    node(db, 'OwnersFile', 'owners', path='pkg/OWNERS')
    node(db, 'Directory', 'directory', repo='kubernetes/kubernetes', path='pkg')
    edge(db, 'OwnersFile', 'owners', 'Directory', 'directory', 'COVERS')
    edge(db, 'Person', 'owner', 'OwnersFile', 'owners', 'APPROVER_IN', paper_owner=True)
    assert StaleDetector(db).check_inactive_owners() == 1
    result = repair_saved_graph(db)
    assert result['rationales_retired'] == 1
    assert result['owner_inactive_findings'] == 0
    rationales = WayfinderBrainQuery(db).query_decisions_and_rationale('1')['rationales']
    assert len(rationales) == 1
    assert rationales[0]['quote'] == 'Resources may need to change\nwithout restarting containers.'
    assert scalar(db, "MATCH (r:Rationale {id:'k8s:rationale:semantic'}) RETURN r.valid_to") is None
    assert WayfinderBrainQuery(db).query_ownership('pkg')['declared_owners'][0]['paper_owner'] is None
    assert WayfinderBrainQuery(db).query_stale_findings() == []
    repair_saved_graph(db)
    assert len(WayfinderBrainQuery(db).query_decisions_and_rationale('1')['rationales']) == 1


def test_graph_projections_exclude_shared_ontology_and_old_conversations(db):
    db.query("CREATE (a:Person {id:'k8s:person:a',is_bot:false}), (b:Person {id:'k8s:person:b',is_bot:false}), "
             "(c:Person {id:'k8s:person:c',is_bot:false}), (s:SIG), (a)-[:MEMBER_OF]->(s), (c)-[:MEMBER_OF]->(s), "
             "(t:Thread {root_ref:'pr'}), (ca:Comment {created_at:$old}), (cb:Comment {created_at:$old}), "
             "(a)-[:AUTHORED]->(ca), (b)-[:AUTHORED]->(cb), (t)-[:HAS_COMMENT]->(ca), (t)-[:HAS_COMMENT]->(cb)", {'old': stamp(200)})
    algos = GraphAlgorithms(db)
    assert algos.compute_working_relationships() == 0
    db.query("MATCH (a:Person {id:'k8s:person:a'}),(b:Person {id:'k8s:person:b'}) "
             "CREATE (pr:PullRequest {id:'pr'}), (ra:Review {submitted_at:$at}), (rb:Review {submitted_at:$at}), "
             "(a)-[:SUBMITTED]->(ra), (b)-[:SUBMITTED]->(rb), (ra)-[:REVIEWS]->(pr), (rb)-[:REVIEWS]->(pr), (a)-[:AUTHORED]->(pr)", {'at':stamp()})
    assert algos.compute_working_relationships() == 1
    assert algos.compute_communities() == 1
    assert scalar(db, "MATCH (c:Person {id:'k8s:person:c'})-[:IN_COMMUNITY]->() RETURN count(c)") == 0
    assert algos.compute_review_centrality() >= 2
    assert scalar(db, 'MATCH ()-[r:REVIEWS_FOR]->() WHERE r.valid_to IS NULL RETURN count(r)') == 1


def test_activity_references_parent_chains_and_explicit_replies():
    raw = deepcopy(SAMPLE_GITHUB_ACTIVITY[0])
    raw['body'] = 'Fixes https://github.com/other/repo/issues/42'
    raw['comments'] = {'nodes':[{'id':'1','body':'A standalone message','author':{'login':'alice'},'createdAt':stamp(2)},
                               {'id':'2','body':'See https://github.com/other/repo/pull/77 and KEP-1234','author':{'login':'bob'},'createdAt':stamp(1)}]}
    result = ActivityProcessor.process_github_node(raw)
    edges = result['edges']
    assert any(e['rel_type']=='FIXES' and e['target_id']=='k8s:issue:other/repo:42' for e in edges)
    assert any(e['source_label']=='Comment' and e['target_id']=='k8s:pr:other/repo:77' for e in edges)
    assert not any(e['rel_type']=='REPLIES_TO' for e in edges)
    assert any(e['rel_type']=='CHILD_OF' for e in edges)
    raw['comments']['nodes'][1]['body'] = '@alice yes, this is related'
    assert any(e['rel_type']=='REPLIES_TO' for e in ActivityProcessor.process_github_node(raw)['edges'])


def test_versioned_gates_empty_quotes_and_explicit_credentials():
    parsed = GateParser.parse_feature_gates_go('''Foo: {
        {Version: version.MustParse("1.30"), Default: false, PreRelease: featuregate.Alpha},
        {Version: version.MustParse("1.31"), Default: true, PreRelease: featuregate.Beta},
    },''', release_version='v1.30')
    assert parsed['gates'][0].current_stage == 'Alpha'
    assert not QuoteVerifier.verify_quote('   ', 'unrelated text')
    assert not QuoteVerifier.verify_quote('**__**', 'unrelated text')
    client = OpenAPIClient(api_key='explicit-real-key', embedding_api_key='explicit-embedding-key')
    assert client.is_live_llm_configured() and client.is_live_embedding_configured()


def test_github_nested_and_search_pagination(monkeypatch):
    client = GitHubClient(token='test')
    calls = []
    def graphql(query, variables):
        calls.append((query, variables))
        if 'search(' in query:
            return {'search': {'nodes':[{'id':'PR1','__typename':'PullRequest',
                'comments':{'nodes':[{'id':'c1'}], 'pageInfo':{'hasNextPage':True,'endCursor':'cc'}},
                'reviews':{'nodes':[], 'pageInfo':{'hasNextPage':True,'endCursor':'rr'}},
                'files':{'nodes':[], 'pageInfo':{'hasNextPage':True,'endCursor':'ff'}}}],
                'pageInfo':{'hasNextPage':False}}}
        connection = next(c for c in ['comments','reviews','files'] if c + '(first:' in query)
        return {'node':{connection:{'nodes':[{'id':'later-'+connection}], 'pageInfo':{'hasNextPage':False}}}}
    monkeypatch.setattr(client, 'execute_graphql', graphql)
    page = client.search_sig_node_activity()
    item = page['search']['nodes'][0]
    assert len(item['comments']['nodes']) == 2
    assert item['reviews']['nodes'][0]['id'] == 'later-reviews'
    assert item['files']['nodes'][0]['id'] == 'later-files'
    assert 'sort:updated-asc' in calls[0][1]['queryStr'] and 'updated:>=' in calls[0][1]['queryStr']
    assert 'is:pr' in calls[0][1]['queryStr']
    seen = []
    def pages(**kwargs):
        seen.append((kwargs['kind'], kwargs.get('after_cursor')))
        kind = kwargs['kind']
        if kwargs.get('after_cursor') is None:
            return {'search':{'nodes':[{'id':kind+'-first'}],'pageInfo':{'hasNextPage':True,'endCursor':'next'}}}
        return {'search':{'nodes':[{'id':kind+'-second'}],'pageInfo':{'hasNextPage':False}}}
    monkeypatch.setattr(client,'search_sig_node_activity',pages)
    assert [n['id'] for n in client.iter_activity()] == ['pr-first','pr-second','issue-first','issue-second']
    assert seen == [('pr',None),('pr','next'),('issue',None),('issue','next')]
    seen.clear()
    assert [n['id'] for n in client.iter_activity(limit=1)] == ['pr-first']
    assert seen == [('pr',None)]
    seen.clear()
    assert list(client.iter_activity(limit=0)) == []
    assert seen == []
    client.close()


def test_eligible_units_and_previous_semantic_context(db):
    db.query("CREATE (p:Person {id:'p',is_bot:false}), (bot:Person {id:'bot',is_bot:true}), "
             "(pr:PullRequest {id:'pr',title:'Thread title'}), (t:Thread {root_ref:'pr'}), (t)-[:ON]->(pr), "
             "(first:Comment {id:'first',body_text:'An earlier technical question about scheduling.',created_at:'2025-01-01'}), "
             "(last:Comment {id:'last',body_text:'A later technical answer about scheduling.',created_at:'2025-01-02'}), "
             "(auto:Comment {id:'auto',body_text:'An automated message with enough characters.',created_at:'2025-01-03'}), "
             "(p)-[:AUTHORED]->(first), (p)-[:AUTHORED]->(last), (bot)-[:AUTHORED]->(auto), "
             "(t)-[:HAS_COMMENT]->(first), (t)-[:HAS_COMMENT]->(last), (t)-[:HAS_COMMENT]->(auto), "
             "(q:Question {id:'question',summary:'Earlier question'}), (first)-[:ASSERTS]->(q), "
             "(r:Review {id:'review',body_text:'Review discusses the scheduler design.',submitted_at:'2025-01-04'}), "
             "(p)-[:SUBMITTED]->(r), (r)-[:REVIEWS]->(pr), "
             "(kep:KEP {id:'kep'}), (ks:KEPSection {id:'kepsection',text_content:'The motivation explains the technical design.'}), (kep)-[:HAS_SECTION]->(ks), "
             "(doc:DocPage {id:'doc'}), (ds:DocSection {id:'docsection',text_content:'The documentation explains this technical design.'}), (doc)-[:HAS_SECTION]->(ds)")
    captured = []
    class Recorder:
        stats = SimpleNamespace(rejection_rate=0)
        def process_text_unit(self, *args, **kwargs):
            captured.append((args, kwargs))
            return {'accepted':0,'rejected':0}
    report = PopulationPipeline(db=db, extraction_pipeline=Recorder()).step4_run_extraction()
    assert report['units_examined'] == 5
    assert {args[0] for args, _ in captured} == {'Comment','Review','KEPSection','DocSection'}
    args, context = next(entry for entry in captured if entry[0][1] == 'last')
    assert context['thread_title'] == 'Thread title'
    assert 'first' in context['context_target_ids'] and 'question' in context['context_target_ids']
    assert 'Earlier question' in context['prev_comments_context']


def test_live_structured_sources_and_changed_ownership(db, monkeypatch):
    from wayfinder.sync.structured import StructuredSourceSync
    from wayfinder.sync import seed_data
    from wayfinder.config import settings
    monkeypatch.setattr(settings, 'owners_scope', ['pkg/kubelet/OWNERS'])
    monkeypatch.setattr(settings, 'docs_scope', ['content/en/docs/test'])
    files = {
        ('kubernetes/community','sigs.yaml'):seed_data.SIGS_YAML_SAMPLE,
        ('kubernetes/kubernetes','OWNERS_ALIASES'):seed_data.OWNERS_ALIASES_SAMPLE,
        ('kubernetes/kubernetes','pkg/kubelet/OWNERS'):'approvers: [live-owner]',
        ('kubernetes/enhancements','keps/sig-node/1287-resize/kep.yaml'):seed_data.KEP_1287_YAML_SAMPLE.replace('authors:', 'authors:'),
        ('kubernetes/enhancements','keps/sig-node/1287-resize/README.md'):seed_data.KEP_1287_README_SAMPLE,
        ('kubernetes/kubernetes','pkg/features/kubelet_features.go'):seed_data.KUBELET_FEATURES_GO_SAMPLE,
        ('kubernetes/website','content/en/docs/test.md'):seed_data.DOC_PAGE_RESIZE_SAMPLE,
    }
    class SourceClient:
        def __init__(self): self.fetched = []
        def repository_tree(self, repo): return 'commit', [path for r,path in files if r == repo]
        def fetch_raw_file(self, repo, path, ref='master'):
            self.fetched.append((repo,path,ref))
            return files[(repo,path)]
        def release_refs(self, repo): return ['v1.30.0']
    source = SourceClient()
    sync = StructuredSourceSync(db,source)
    report = sync.refresh()
    assert report['sources'] == 6
    assert scalar(db, "MATCH (p:Person {login:'live-owner'}) RETURN count(p)") == 1
    assert scalar(db, 'MATCH ()-[r:GOVERNED_BY]->() RETURN count(r)') >= 1
    assert scalar(db, 'MATCH (:Person)-[r:KEP_AUTHOR]->(:KEP) RETURN count(r)') >= 1
    assert sync.refresh()['changed_sources'] == 0
    files[('kubernetes/kubernetes','pkg/kubelet/OWNERS')] = 'approvers: [new-owner]'
    assert sync.refresh()['changed_sources'] == 1
    assert scalar(db, "MATCH (:Person {login:'live-owner'})-[r:APPROVER_IN]->() WHERE r.valid_to IS NULL RETURN count(r)") == 0
    assert scalar(db, "MATCH (:Person {login:'new-owner'})-[r:APPROVER_IN]->() WHERE r.valid_to IS NULL RETURN count(r)") == 1


def test_incremental_extracts_updates_and_commits_watermark_only_on_success(db, tmp_path, monkeypatch):
    from wayfinder.sync.structured import StructuredSourceSync
    monkeypatch.setattr(StructuredSourceSync,'refresh',lambda self: {'changed_sources':0})
    raw = {'__typename':'Issue','number':123,'title':'A thread','url':'https://github.com/kubernetes/kubernetes/issues/123',
           'createdAt':stamp(50),'updatedAt':stamp(),'author':{'login':'alice'},
           'comments':{'nodes':[{'id':'c1','body':'How does the scheduler isolate memory?', 'createdAt':stamp(40),
                                 'url':'https://github.com/kubernetes/kubernetes/issues/123#issuecomment-1', 'author':{'login':'alice'}}]}}
    class ActivityClient:
        token = 'explicit-token'
        def __init__(self): self.since = []
        def iter_activity(self,since_date=None):
            self.since.append(since_date)
            yield raw
    client = ActivityClient()
    pipeline, llm = extractor(db,tmp_path,{'questions':[{'summary':'Memory isolation?', 'evidence_quote':raw['comments']['nodes'][0]['body']}]})
    engine = IncrementalSync(db,client,pipeline)
    engine.sync_once()
    assert scalar(db,'MATCH (q:Question) WHERE q.valid_to IS NULL RETURN count(q)') == 1
    watermark = scalar(db,'MATCH (s:SyncState) RETURN s.watermark')
    engine.sync_once()
    assert client.since[-1] == watermark and llm.calls == 1
    raw['comments']['nodes'][0]['body'] = 'Updated: how does the scheduler reserve memory?'
    llm.payload = {'questions':[{'summary':'Reservations?', 'evidence_quote':raw['comments']['nodes'][0]['body']}]}
    engine.sync_once()
    assert scalar(db,'MATCH (q:Question) WHERE q.valid_to IS NULL RETURN count(q)') == 1
    assert scalar(db,'MATCH (q:Question) WHERE q.valid_to IS NOT NULL RETURN count(q)') == 1
    restored = raw['comments']['nodes'][0]['body']
    raw['comments']['nodes'][0]['body'] = '/approve'
    engine.sync_once()
    assert scalar(db,'MATCH (q:Question) WHERE q.valid_to IS NULL RETURN count(q)') == 0
    raw['comments']['nodes'][0]['body'] = restored
    engine.sync_once()
    assert scalar(db,'MATCH (q:Question) WHERE q.valid_to IS NULL RETURN count(q)') == 1
    before = scalar(db,'MATCH (s:SyncState) RETURN s.watermark')
    def fail(self): raise RuntimeError('source fetch failed')
    monkeypatch.setattr(StructuredSourceSync,'refresh',fail)
    with pytest.raises(RuntimeError,match='source fetch failed'):
        engine.sync_once()
    assert scalar(db,'MATCH (s:SyncState) RETURN s.watermark') == before


def test_offline_extraction_is_not_cached(db,tmp_path):
    client = OpenAPIClient(api_key='sk-dummy-key')
    pipeline = ExtractionPipeline(db,client,ExtractionCache(cache_dir=tmp_path))
    assert pipeline.process_text_unit('Comment','c','An eligible technical statement')['reason'] == 'offline'
    assert list(tmp_path.iterdir()) == []


def test_top_five_rank_precedes_owner_exclusion_and_expertise_expires(db):
    node(db,'Directory','dir',path='pkg',repo='repo')
    node(db,'OwnersFile','owners',no_parent_owners=False)
    edge(db,'OwnersFile','owners','Directory','dir','COVERS')
    for i in range(6):
        pid = f'p{i}'
        node(db,'Person',pid,login=pid)
        edge(db,'Person',pid,'Directory','dir','EXPERT_IN',score=10-i,evidence_ids=[])
        if i < 5:
            edge(db,'Person',pid,'OwnersFile','owners','APPROVER_IN')
    calc = ExpertiseCalculator(db)
    assert calc.detect_unlisted_experts() == []
    db.query("MATCH (p:Person {id:'p5'}),(d:Directory {id:'dir'}) CREATE (pr:PullRequest {id:'merged-pr',state:'MERGED',merged_at:$at}), "
             "(f:File), (p)-[:AUTHORED]->(pr), (pr)-[:TOUCHES]->(f), (d)-[:CONTAINS]->(f)", {'at':stamp(1)})
    assert calc.calculate_directory_expertise()['expert_edges_created'] == 1
    db.query('MATCH (pr:PullRequest) SET pr.merged_at = $at', {'at':stamp(200)})
    assert calc.calculate_directory_expertise()['expert_edges_created'] == 1
    assert 0 < WayfinderBrainQuery(db).query_who_knows('pkg')[0]['score'] < 1


def test_batch_references_and_comment_resolution(db):
    from wayfinder.sync.pipeline import write_activity_edge
    raw = deepcopy(SAMPLE_GITHUB_ACTIVITY[0])
    raw['body'] = 'This resolves https://github.com/kubernetes/kubernetes/issues/8#issuecomment-9'
    raw['state'] = 'MERGED'
    raw['mergedAt'] = '2025-03-01'
    batch = ActivityProcessor.process_github_node(raw)
    for nodes in batch['nodes'].values():
        for n in nodes:
            db.upsert_node(n.__class__.__name__,n.to_cypher_props())
    db.query("CREATE (source:Comment {id:'source',created_at:'2025-01-01',source_url:'https://github.com/kubernetes/kubernetes/issues/8#issuecomment-9'}), "
             "(concern:Argument {id:'concern',kind:'concern'}), (source)-[:ASSERTS]->(concern)")
    for e in batch['edges']:
        write_activity_edge(db,e)
    assert scalar(db, "MATCH (:PullRequest)-[r:REFERENCES]->(:Comment {id:'source'}) RETURN count(r)") == 1
    assert ConcernResolver(db).resolve_concern_statuses()['open'] == 1
    pipeline = PopulationPipeline(db)
    pipeline.step2_ingest_activity()
    assert scalar(db,'MATCH (:PullRequest)-[r:FIXES]->(:Issue) RETURN count(r)') >= 1


@pytest.mark.parametrize('limit', [None, 1])
def test_initial_watermark_requires_a_complete_activity_load(limit):
    writes = []
    db = SimpleNamespace(init_schema=lambda: None, query=lambda *a,**k: SimpleNamespace(result_set=[]),
                         get_stats=lambda: {'total_nodes':0,'total_edges':0},
                         upsert_node=lambda label, props: writes.append((label,props)))
    pipeline = PopulationPipeline(db=db,github_client=object(),extraction_pipeline=object())
    for method in ('step1_ingest_structure_files','step2_ingest_activity','step4_run_extraction',
                   'step6_compute_derived','step7_run_stale_checks'):
        setattr(pipeline,method,lambda **kwargs: {})
    pipeline.run_full_pipeline(use_live_github=True,max_search_items=limit)
    assert bool(writes) == (limit is None)
