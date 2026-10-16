from datetime import datetime, timezone, timedelta
import json
import uuid
import pytest

from wayfinder.db import DatabaseManager
from wayfinder.derived.expertise_v2 import ExpertiseRefresh, get_rankings, read, STATE_ID
from wayfinder.parsers.prow_parser import ProwParser
from wayfinder.github.client import GitHubClient
from wayfinder.github.activity import ActivityProcessor
from wayfinder.query import WayfinderBrainQuery
from wayfinder.brain_graph import BrainGraph

NOW=datetime(2026,10,15,12,tzinfo=timezone.utc)


@pytest.fixture
def db():
    instance=DatabaseManager(graph_name='wayfinder_v2_test_'+uuid.uuid4().hex)
    if not instance.ping():
        pytest.skip('FalkorDB unavailable')
    instance.query("""CREATE (a:Person {id:'author',login:'author'}),(p:Person {id:'peer',login:'peer'}),
        (bot:Person {id:'bot',login:'kubernetes-prow',is_bot:true}),
        (d:Directory {id:'d',path:'pkg/kubelet',repo:'kubernetes/kubernetes'}),
        (s:Directory {id:'s',path:'pkg/kubelet/sub',repo:'kubernetes/kubernetes'}),
        (f:File {id:'f',path:'pkg/kubelet/a.go'}),(g:File {id:'g',path:'pkg/kubelet/sub/b.go'}),
        (pr:PullRequest {id:'pr',number:1,repo:'kubernetes/kubernetes',state:'MERGED',merged_at:'2026-10-15T12:00:00+00:00',source_url:'https://example.com/pr'}),
        (t:Thread {id:'t'}),
        (a)-[:AUTHORED {id:'auth'}]->(pr),(pr)-[:TOUCHES {id:'touch-f'}]->(f),(pr)-[:TOUCHES {id:'touch-g'}]->(g),
        (d)-[:CONTAINS {id:'contains-f'}]->(f),(s)-[:CONTAINS {id:'contains-g'}]->(g),
        (s)-[:CHILD_OF {id:'child'}]->(d),(t)-[:ON {id:'on'}]->(pr)""")
    yield instance
    instance.clear_graph()


def comment(db,ident,text,person='peer',path=None,at='2026-10-15T12:00:00+00:00'):
    db.upsert_node('Comment',{'id':ident,'body_text':text,'kind':'review_line' if path else 'review',
        'file_path':path,'pr_id':'pr','created_at':at,'source_url':'https://example.com/'+ident})
    db.query('MATCH (p:Person {id:$pid}),(c:Comment {id:$id}),(t:Thread) CREATE (p)-[:AUTHORED {id:$auth}]->(c),(t)-[:HAS_COMMENT {id:$has}]->(c)',
        {'pid':person,'id':ident,'auth':'auth-'+ident,'has':'has-'+ident})


@pytest.mark.parametrize('body', ['/test all','/cc @someone','/label api-review','/unhold','/triage accepted\n/priority important-longterm','/unknown-command x'])
def test_commands_classified_without_credit(body):
    assert ProwParser.is_command_only(body)
    assert ProwParser.classify(body)['discussion_text']==''


def test_quoted_and_fenced_commands_do_not_execute():
    assert ProwParser.parse_commands('> /approve\n```text\n/lgtm\n```')==[]
    assert not ProwParser.is_command_only('> /test all')
    assert ProwParser.classify('Rebased onto master')['category']=='administrative'
    assert ProwParser.classify('/test all\nThe lock must cover this update')['category']=='mixed'


def test_allocations_peer_review_and_weak_signals(db,monkeypatch):
    monkeypatch.setattr('wayfinder.derived.expertise_v2.settings.expertise_half_life_days',15)
    comment(db,'test','/test all')
    comment(db,'rebased','Rebased onto master',person='author')
    comment(db,'approval','/approve\nfor API bits')
    db.query("MATCH (p:Person {id:'peer'}),(pr:PullRequest) CREATE (p)-[:APPROVED {id:'approval-rel',via_comment:'approval',at:'2026-10-15T12:00:00+00:00'}]->(pr)")
    comment(db,'line','The lock must protect this read',path='pkg/kubelet/sub/b.go')
    comment(db,'line2','Please validate the pointer',path='pkg/kubelet/sub/b.go')
    comment(db,'self','A substantive implementation note',person='author',path='pkg/kubelet/a.go')
    comment(db,'bot-line','The lock is wrong',person='bot',path='pkg/kubelet/a.go')
    report=ExpertiseRefresh(db,NOW).refresh()
    ranked=get_rankings(db,'pkg/kubelet',clock=NOW)
    assert report['scoring_version']=='v2'
    author=next(x for x in ranked['items'] if x['login']=='author')
    peer=next(x for x in ranked['items'] if x['login']=='peer')
    assert author['score']==2 and [m['score'] for m in author['matches']]==[1,1]
    assert peer['score']==2 and len(peer['matches'])==1
    assert peer['score_breakdown']=={'implementation':0,'peer_review':2}
    assert peer['involvement_count']==1 and peer['involvement'][0]['category']=='pr_wide_approval'
    assert peer['distinct_pr_count']==1 and peer['evidence_strength']=='limited'
    assert get_rankings(db,'pkg/kubelet/sub',clock=NOW)['items'][0]['login']=='peer'
    assert get_rankings(db,'d',clock=NOW)['items']==ranked['items']
    assert [x['login'] for x in WayfinderBrainQuery(db).query_who_knows('pkg/kubelet')]==[x['login'] for x in BrainGraph(db).who_knows('pkg/kubelet')['items']]


def test_command_only_person_excluded_and_snapshot_empty_generation(db):
    comment(db,'test','/test all')
    report=ExpertiseRefresh(db,NOW).refresh()
    assert [x['login'] for x in get_rankings(db,'pkg/kubelet')['items']]==['author']
    db.query("MATCH (pr:PullRequest) SET pr.merged_at=null,pr.state='OPEN'")
    ExpertiseRefresh(db,NOW+timedelta(hours=1)).refresh()
    assert get_rankings(db,'pkg/kubelet')['items']==[]


def test_retired_membership_and_pending_review_are_not_scored(db):
    comment(db,'line','Please validate the pointer',path='pkg/kubelet/sub/b.go')
    db.query("MATCH (c:Comment {id:'line'}) CREATE (r:Review {id:'pending-review',state:'PENDING'}),(c)-[:IN_REVIEW {id:'in-review'}]->(r)")
    db.query("MATCH ()-[r:TOUCHES {id:'touch-g'}]->() SET r.valid_to='2026-10-14'")
    report=ExpertiseRefresh(db,NOW).refresh()
    people=get_rankings(db,'pkg/kubelet')['items']
    assert [p['login'] for p in people]==['author']
    assert people[0]['score']==2 and len(people[0]['matches'])==1
    assert report['excluded_counts']['unpublished_or_retired_review']==1


def test_smooth_decay_invalid_dates_and_history(db):
    db.query("MATCH (pr:PullRequest) SET pr.merged_at='2026-09-15T12:00:00+00:00'")
    before=ExpertiseRefresh(db,NOW-timedelta(seconds=1)).refresh()
    score1=get_rankings(db,'pkg/kubelet')['items'][0]['score']
    after=ExpertiseRefresh(db,NOW+timedelta(seconds=1)).refresh()
    score2=get_rankings(db,'pkg/kubelet')['items'][0]['score']
    assert 0<score2<=score1 and abs(score1-score2)<0.00001
    assert get_rankings(db,'pkg/kubelet',as_of=before['calculated_at'])['generation']==before['generation']
    assert get_rankings(db,'pkg/kubelet',clock=NOW+timedelta(hours=3))['stale']
    db.query("MATCH (pr:PullRequest) SET pr.merged_at='invalid'")
    report=ExpertiseRefresh(db,NOW+timedelta(hours=4)).refresh()
    assert report['excluded_counts']['invalid_timestamp']==1
    assert get_rankings(db,'pkg/kubelet')['items']==[]


def test_failed_generation_preserves_published_snapshot(db,monkeypatch):
    published=ExpertiseRefresh(db,NOW).refresh()
    original=db.batch_upsert_nodes
    def fail(*args,**kwargs):
        raise RuntimeError('simulated interruption')
    monkeypatch.setattr(db,'batch_upsert_nodes',fail)
    with pytest.raises(RuntimeError):
        ExpertiseRefresh(db,NOW+timedelta(hours=1)).refresh()
    assert get_rankings(db,'pkg/kubelet')['generation']==published['generation']
    monkeypatch.setattr(db,'batch_upsert_nodes',original)
    ExpertiseRefresh(db,NOW+timedelta(hours=1)).refresh()
    assert len(read(db,'MATCH ()-[r:EXPERT_IN]->() WHERE r.valid_to IS NULL RETURN r'))==2


def test_compare_publish_rejects_overlapping_refresh(db,monkeypatch):
    published=ExpertiseRefresh(db,NOW).refresh()
    original=db.query
    def race(query,params=None):
        if query.startswith('MATCH (s:ExpertiseState'):
            original('MATCH (s:ExpertiseState {id:$id}) SET s.generation=$new',{'id':STATE_ID,'new':'newer-generation'})
        return original(query,params)
    monkeypatch.setattr(db,'query',race)
    with pytest.raises(RuntimeError,match='published first'):
        ExpertiseRefresh(db,NOW+timedelta(hours=1)).refresh()
    # The publication guard does not retire the prior current edges on failure.
    assert read(db,'MATCH ()-[r:EXPERT_IN]->() WHERE r.valid_to IS NULL RETURN DISTINCT r.generation AS generation')[0]['generation']==published['generation']


def test_bounded_backfill_only_existing_prs_and_coverage(db):
    from wayfinder.derived.expertise_backfill import backfill
    calls=[]
    class Source:
        def fetch_review_threads(self,repo,number):
            calls.append((repo,number))
            return {'pageInfo':{'hasNextPage':False},'nodes':[{'id':'review-thread','path':'pkg/kubelet/a.go',
                'comments':{'pageInfo':{'hasNextPage':False},'nodes':[{'id':'review-comment','body':'Check this pointer',
                'createdAt':NOW.isoformat(),'url':'https://example.com/review','path':'pkg/kubelet/a.go','author':{'login':'peer'},'pullRequestReview':None}]}}]}
    report=backfill(db,Source(),limit=1)
    assert report['review_threads_backfilled_prs']==1 and len(calls)==1
    assert read(db,'MATCH (pr:PullRequest) RETURN pr.state AS state')[0]['state']=='MERGED'
    assert read(db,'MATCH (c:ReviewThreadCoverage) RETURN c.complete AS complete')[0]['complete']
    assert not read(db,'MATCH (c:ActivityCoverage) RETURN c')
    assert backfill(db,Source(),limit=1)['review_threads_backfilled_prs']==0


def test_review_coverage_requires_all_comment_pages(db):
    from wayfinder.derived.expertise_backfill import record_review_coverage
    record_review_coverage(db,'kubernetes/kubernetes',1,{'pageInfo':{'hasNextPage':False},'nodes':[
        {'comments':{'pageInfo':{'hasNextPage':True},'nodes':[]}}]})
    assert read(db,'MATCH (c:ReviewThreadCoverage) RETURN c.complete AS complete')[0]['complete'] is False


def test_review_thread_and_nested_comment_pagination(monkeypatch):
    client=GitHubClient(token='fake')
    thread={'id':'thread1','path':'pkg/kubelet/a.go','comments':{'nodes':[{'id':'one'}],'pageInfo':{'hasNextPage':True,'endCursor':'c1'}}}
    calls=[]
    def query(gql,variables):
        calls.append(variables)
        if variables.get('id')=='pr-id':
            return {'node':{'reviewThreads':{'nodes':[{'id':'thread2','path':'x','comments':{'nodes':[],'pageInfo':{'hasNextPage':False}}}],'pageInfo':{'hasNextPage':False}}}}
        return {'node':{'comments':{'nodes':[{'id':'two'}],'pageInfo':{'hasNextPage':False}}}}
    monkeypatch.setattr(client,'execute_graphql',query)
    node={'__typename':'PullRequest','id':'pr-id','reviewThreads':{'nodes':[thread],'pageInfo':{'hasNextPage':True,'endCursor':'t1'}}}
    client._paginate_connections(node)
    assert len(node['reviewThreads']['nodes'])==2
    assert [x['id'] for x in thread['comments']['nodes']]==['one','two']
    assert len(calls)==2
    client.close()


def test_activity_processor_keeps_review_file_anchor():
    raw={'__typename':'PullRequest','number':1,'url':'https://github.com/kubernetes/kubernetes/pull/1',
         'reviewThreads':{'nodes':[{'id':'t1','path':'pkg/kubelet/a.go','comments':{'nodes':[
             {'id':'rc1','body':'Please validate this pointer','createdAt':NOW.isoformat(),'url':'https://example.com/review',
              'author':{'login':'peer'},'pullRequestReview':{'id':'review1'}}]}}]}}
    parsed=ActivityProcessor.process_github_node(raw)
    c=parsed['nodes']['comments'][0]
    assert c.kind=='review_line' and c.file_path=='pkg/kubelet/a.go'
    assert c.attribution=='file_specific'
    assert any(x['rel_type']=='ON_FILE' for x in parsed['edges'])
