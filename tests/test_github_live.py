"""Live GitHub connector against an in-memory GraphQL API, through the real client and parsers."""
import json
import uuid
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from wayfinder.db import DatabaseManager
from wayfinder.company.core import CompanyStore
from wayfinder.company.github_live import sync_github
from wayfinder.github.client import GitHubClient

REPO='acme/wayfinder'; ITEM='11111111-2222-4333-8444-555555555555'
PLANE_URL=f'https://app.plane.so/acme-ws/projects/99999999-8888-4777-8666-555555555555/work-items/{ITEM}'
MANIFEST={'organization_id':'ghtest','synthetic':True,'plane':{'workspace':'acme-ws','project_ids':['p']},
    'identities':{'github:alice':'owner'},'people':[{'id':'owner','name':'Owner'}],'components':[],
    'github':{'repository':REPO,'components':{'source-ingestion':['wayfinder/company'],'agent-workflows':'wayfinder/extraction'}}}


def pr(number,files,body=None,review_comment='Deleted parents must retire their comments too.'):
    at=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    url=f'https://github.com/{REPO}/pull/{number}'
    empty={'pageInfo':{'hasNextPage':False},'nodes':[]}
    return {'__typename':'PullRequest','id':f'PR{number}','number':number,'title':'Add freshness watcher',
        'state':'MERGED','createdAt':at,'mergedAt':at,'updatedAt':at,'url':url,'additions':40,'deletions':2,
        'milestone':None,'author':{'login':'alice','__typename':'User'},'labels':{'nodes':[]},
        'body':f'Implements the Plane task {PLANE_URL}' if body is None else body,
        'files':{'pageInfo':{'hasNextPage':False},'nodes':[{'path':p,'additions':20,'deletions':1} for p in files]},
        'reviews':{'pageInfo':{'hasNextPage':False},'nodes':[{'id':f'R{number}','state':'APPROVED','submittedAt':at,
            'url':url+'#review','author':{'login':'bob','__typename':'User'},'body':'Checked the retirement cascade.'}]},
        'reviewThreads':{'pageInfo':{'hasNextPage':False},'nodes':[{'id':f'T{number}','path':files[0],'comments':{
            'pageInfo':{'hasNextPage':False},'nodes':[{'id':f'RC{number}','body':review_comment,
            'createdAt':at,'url':url+'#r1','path':files[0],'author':{'login':'bob','__typename':'User'},'pullRequestReview':{'id':f'R{number}'}}]}}]},
        'comments':empty}


def commit(sha,message,parents=(),login='alice'):
    return {'oid':sha,'abbreviatedOid':sha[:7],'url':f'https://github.com/{REPO}/commit/{sha}','messageHeadline':message.splitlines()[0],
        'message':message,'committedDate':'2026-10-17T10:00:00Z','authoredDate':'2026-10-17T10:00:00Z','additions':3,'deletions':1,
        'author':{'name':'Git Name','user':{'login':login} if login else None},'parents':{'nodes':[{'oid':p} for p in parents]}}


C1,C2,C3=('c1'+'0'*38,'c2'+'0'*38,'c3'+'0'*38)
FEATURE='feat/acme-3-watcher'


def history(heads=None,merge=None):
    """Repository refs and PR refs. Default: main at C1 and a feature branch at C2 with PR 7 open from it."""
    commits={C1:commit(C1,'Initial import'),C2:commit(C2,'Retire children of deleted items (ACME-3)',[C1]),
        C3:commit(C3,'Merge pull request #7',[C1,C2])}
    heads=heads or {'main':[C1],FEATURE:[C2,C1]}
    return {'defaultBranchRef':{'name':'main'},
        'refs':{'pageInfo':{'hasNextPage':False},'nodes':[{'name':name,'target':{'oid':log[0],
            'history':{'pageInfo':{'hasNextPage':False},'nodes':[commits[c] for c in log]}}} for name,log in heads.items()]},
        'pullRequests':{'pageInfo':{'hasNextPage':False},'nodes':[{'number':7,'headRefName':FEATURE,'baseRefName':'main',
            'headRefOid':C2,'mergeCommit':{'oid':merge} if merge else None,
            'commits':{'pageInfo':{'hasNextPage':False},'nodes':[{'commit':commits[C2]}]}}]}}


def transport(searches,body=None,repository=None,**extra):
    def respond(request):
        variables=json.loads(request.content)['variables']
        if 'owner' in variables:
            return httpx.Response(200,json={'data':{'repository':repository or history()}})
        query=variables.get('queryStr','')
        searches.append(query)
        nodes=[pr(7,['wayfinder/company/plane.py','wayfinder/company/slack.py'],body,**extra)] if 'is:pr' in query else []
        return httpx.Response(200,json={'data':{'search':{'issueCount':len(nodes),'pageInfo':{'hasNextPage':False},'nodes':nodes}}})
    return httpx.MockTransport(respond)


def test_live_github_connector_links_code_work_and_expertise():
    db=DatabaseManager(graph_name='test_github_live_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    try:
        store=CompanyStore(db,MANIFEST); store.bootstrap()
        for component in ('source-ingestion','agent-workflows'):
            store.node('Component',f'company:ghtest:component:{component}',source='manifest',url='https://example.org',name=component)
        store.node('Issue',f'plane:ghtest:issue:{ITEM}',source='plane',url=PLANE_URL,title='Freshness watcher',text='Build it')
        searches=[]
        client=GitHubClient(token='test',session=httpx.Client(transport=transport(searches)))
        result=sync_github(db,MANIFEST,client=client)
        assert result['pull_requests']==1 and result['plane_references']==1
        # Whole-repository search: no Kubernetes label filter.
        assert searches and all(q.startswith(f'repo:{REPO} is:') and 'label:' not in q for q in searches)
        q=lambda c:db.graph.ro_query(c).result_set
        pid=f'github:ghtest:pr:{REPO}:7'
        assert q(f"MATCH (:PullRequest {{id:'{pid}'}})-[:REFERENCES]->(i:Issue) RETURN i.id")==[[f'plane:ghtest:issue:{ITEM}']]
        assert q(f"MATCH (:PullRequest {{id:'{pid}'}})-[:CONCERNS_COMPONENT]->(c) RETURN c.id")==[['company:ghtest:component:source-ingestion']]
        assert q("MATCH (:Component {id:'company:ghtest:component:agent-workflows'})-[:IMPLEMENTED_IN]->(d:Directory) RETURN d.path")==[['wayfinder/extraction']]
        # The PR author maps to the manifest identity; the reviewer stays a separate GitHub person.
        assert q(f"MATCH (p:Person)-[:AUTHORED]->(:PullRequest {{id:'{pid}'}}) RETURN p.id")==[['company:ghtest:person:owner']]
        assert q("MATCH (p:Person) WHERE p.id='github:ghtest:person:bob' RETURN count(p)")==[[1]]
        assert q(f"MATCH (c:SourceCoverage {{source_system:'github',scope:'repo:{REPO}'}}) RETURN c.status,c.record_count")==[['complete',1]]
        assert q("MATCH (p:PullRequest) RETURN p.data_mode,p.synthetic")==[['live',False]]
        # File-level review by a non-author peer counts as review expertise on the touched code.
        experts=q("MATCH (p:Person)-[r:EXPERT_IN]->(d:Directory) WHERE r.valid_to IS NULL RETURN DISTINCT p.id")
        assert ['github:ghtest:person:bob'] in experts,experts
    finally:
        db.clear_graph()


def test_pr_urls_link_both_ways_and_retire_when_removed():
    db=DatabaseManager(graph_name='test_github_links_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    try:
        store=CompanyStore(db,MANIFEST); store.bootstrap()
        store.node('Issue',f'plane:ghtest:issue:{ITEM}',source='plane',url=PLANE_URL,title='Freshness watcher',text='Build it')
        permalink='https://acme.slack.com/archives/C1/p1700000000000100'
        pid=f'github:ghtest:pr:{REPO}:7'; pr_url=f'https://github.com/{REPO}/pull/7'
        def slack(text):
            store.node('Comment','slack:ghtest:message:C1:1',source='slack',url=permalink,text=text)
        def sync(body):
            client=GitHubClient(token='test',session=httpx.Client(transport=transport([],body)))
            return sync_github(db,MANIFEST,client=client)
        slack(f'Review thread on <{pr_url}#discussion_r42|the watcher PR> before merge.')
        result=sync(f'Context in Slack: {permalink}\nTracks {PLANE_URL}')
        assert result['plane_references']==1
        q=lambda c:db.graph.ro_query(c).result_set
        current=lambda src:sorted(r[0] for r in q(f"MATCH ({{id:'{src}'}})-[r:REFERENCES]->(t) WHERE r.valid_to IS NULL RETURN t.id"))
        assert current('slack:ghtest:message:C1:1')==[pid]
        assert current(pid)==sorted([f'plane:ghtest:issue:{ITEM}','slack:ghtest:message:C1:1'])
        # Removing the URLs from either side retires only those reference intervals.
        slack('Merged, thanks.'); sync(f'Tracks {PLANE_URL}')
        assert current('slack:ghtest:message:C1:1')==[]
        assert current(pid)==[f'plane:ghtest:issue:{ITEM}']
        assert q(f"MATCH ({{id:'{pid}'}})-[r:REFERENCES]->({{id:'slack:ghtest:message:C1:1'}}) RETURN r.valid_to IS NOT NULL")==[[True]]
    finally:
        db.clear_graph()


def test_cross_source_links_cover_issues_conversation_plane_links_and_provenance():
    db=DatabaseManager(graph_name='test_github_xlinks_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    try:
        store=CompanyStore(db,MANIFEST); store.bootstrap()
        issue=f'plane:ghtest:issue:{ITEM}'; pid=f'github:ghtest:pr:{REPO}:7'; pr_url=f'https://github.com/{REPO}/pull/7'
        store.node('Issue',issue,source='plane',url=PLANE_URL,title='Freshness watcher',text='Build it')
        stale=f'plane:ghtest:issue:{ITEM[:-1]}6'; fixture=f'plane:ghtest:issue:{ITEM[:-1]}7'
        for other in (stale,fixture):
            store.node('Issue',other,source='plane',url=PLANE_URL[:-1]+other[-1],title='Other',text='Other')
        permalink='https://acme.slack.com/archives/C1/p1700000000000100'
        store.node('Comment','slack:ghtest:message:C1:1',source='slack',url=permalink,
            text=f'Bug report: https://github.com/{REPO}/issues/12 and http://www.github.com/ACME/Wayfinder/pull/7/checks')
        db.upsert_node('Issue',{'id':f'github:ghtest:issue:{REPO}:12','number':12,'title':'Watcher drops edits',
            'source_system':'github','source_url':f'https://github.com/{REPO}/issues/12','organization_id':'ghtest'})
        # A Plane work item's "Links" field pointing at the PR's files tab.
        store.node('SourceLink','plane:ghtest:link:1',source='plane',url=pr_url+'/files',linked_from=issue)
        def sync(**extra):
            client=GitHubClient(token='test',session=httpx.Client(transport=transport([],'No links here.',**extra)))
            return sync_github(db,MANIFEST,client=client)
        sync()
        # Written by the previous connector version (no link_method marker) and by the fixture binding.
        store.edge('PullRequest',pid,'Issue',stale,'REFERENCES','github',pr_url)
        store.edge('PullRequest',pid,'Issue',fixture,'REFERENCES','manifest',pr_url)
        sync(review_comment=f'Discussed in Slack: <{permalink}>')
        q=lambda c:db.graph.ro_query(c).result_set
        current=lambda src:sorted(r[0] for r in q(f"MATCH ({{id:'{src}'}})-[r:REFERENCES]->(t) WHERE r.valid_to IS NULL RETURN t.id"))
        assert current('slack:ghtest:message:C1:1')==sorted([f'github:ghtest:issue:{REPO}:12',pid])
        assert current(issue)==[pid]
        review_comment=q(f"MATCH (c:Comment {{source_url:'{pr_url}#r1'}}) RETURN c.id")[0][0]
        assert current(review_comment)==['slack:ghtest:message:C1:1']
        # The legacy URL edge is gone with its URL; the fixture binding stays.
        assert current(pid)==[fixture]
        provenance=q("MATCH (s)-[r:REFERENCES]->() WHERE r.valid_to IS NULL AND r.link_method='EXACT_REFERENCE' "
            "AND s.organization_id='ghtest' RETURN DISTINCT s.source_system,r.source_system ORDER BY s.source_system")
        assert provenance==[['github','github'],['slack','slack']]
        assert q(f"MATCH ({{id:'{issue}'}})-[r:REFERENCES]->() WHERE r.valid_to IS NULL RETURN r.source_system,r.link_method")==[['plane','API']]
    finally:
        db.clear_graph()


def test_github_failure_is_recorded_not_raised():
    db=DatabaseManager(graph_name='test_github_fail_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    try:
        CompanyStore(db,MANIFEST).bootstrap()
        client=GitHubClient(token='test',session=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'errors':[{'message':'Could not resolve to a Repository'}]}))))
        result=sync_github(db,MANIFEST,client=client)
        assert result['status']=='unavailable'
        assert db.graph.ro_query("MATCH (c:SourceCoverage {source_system:'github'}) RETURN c.status").result_set==[['unavailable']]
    finally:
        db.clear_graph()


def test_branches_commits_and_plane_keys_link_and_follow_changes():
    db=DatabaseManager(graph_name='test_github_git_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    try:
        store=CompanyStore(db,MANIFEST); store.bootstrap()
        issue=f'plane:ghtest:issue:{ITEM}'
        store.node('Issue',issue,source='plane',url=PLANE_URL,title='Freshness watcher',text='Build it',key='ACME-3')
        message='slack:ghtest:message:C1:1'
        store.node('Comment',message,source='slack',url='https://acme.slack.com/archives/C1/p1',
            text=(f'Fix is on <https://github.com/{REPO}/tree/{FEATURE}/wayfinder/company|the branch>, '
                  f'commit https://github.com/{REPO}/commit/{C2[:7]} - tracks acme-3.'))
        def sync(repository=None):
            client=GitHubClient(token='test',session=httpx.Client(transport=transport([],'No links here.',repository)))
            return sync_github(db,MANIFEST,client=client)
        result=sync()
        assert (result['branches'],result['commits'],result['default_branch'])==(2,2,'main')
        q=lambda c:db.graph.ro_query(c).result_set
        bid=lambda name:f'github:ghtest:branch:{REPO}:{name}'; cid=lambda sha:f'github:ghtest:commit:{REPO}:{sha}'
        pid=f'github:ghtest:pr:{REPO}:7'
        current=lambda src,rel:sorted(r[0] for r in q(f"MATCH ({{id:'{src}'}})-[r:{rel}]->(t) WHERE r.valid_to IS NULL RETURN t.id"))
        assert current(bid('main'),'HEAD_AT')==[cid(C1)] and current(bid(FEATURE),'HEAD_AT')==[cid(C2)]
        assert current(pid,'FROM_BRANCH')==[bid(FEATURE)] and current(pid,'INTO_BRANCH')==[bid('main')]
        assert current(pid,'HAS_COMMIT')==[cid(C2)] and current(cid(C2),'HAS_PARENT')==[cid(C1)]
        assert q(f"MATCH (p:Person)-[:AUTHORED]->(:Commit {{id:'{cid(C2)}'}}) RETURN p.id")==[['company:ghtest:person:owner']]
        assert q(f"MATCH (b:Branch {{id:'{bid('main')}'}}) RETURN b.is_default,b.source_url")==[[True,f'https://github.com/{REPO}/tree/main']]
        # Slack: a /tree URL with a path resolves to the branch, a short SHA to its commit, and the key to the work item.
        assert current(message,'REFERENCES')==sorted([bid(FEATURE),cid(C2),issue])
        methods=q(f"MATCH ({{id:'{message}'}})-[r:REFERENCES]->(t) WHERE r.valid_to IS NULL RETURN t.id,r.link_method")
        assert dict(methods)[issue]=='EXACT_IDENTIFIER' and dict(methods)[cid(C2)]=='EXACT_REFERENCE'
        # The key in a lower-case branch name and in a commit message links to the Plane work item.
        assert current(bid(FEATURE),'REFERENCES')==[issue] and current(cid(C2),'REFERENCES')==[issue]
        assert q(f"MATCH (c:SourceCoverage {{scope:'repo:{REPO}:commits'}}) RETURN c.status,c.record_count")==[['partial',2]]

        # Merge: the feature branch is deleted and main moves to the merge commit.
        sync(history(heads={'main':[C3,C2,C1]},merge=C3))
        assert q(f"MATCH (b:Branch {{id:'{bid(FEATURE)}'}}) RETURN b.valid_to IS NOT NULL,b.is_deleted")==[[True,True]]
        assert current(bid('main'),'HEAD_AT')==[cid(C3)]
        assert q(f"MATCH ({{id:'{bid('main')}'}})-[r:HEAD_AT]->({{id:'{cid(C1)}'}}) RETURN r.valid_to IS NOT NULL")==[[True]]
        assert current(pid,'MERGED_AS')==[cid(C3)] and current(pid,'FROM_BRANCH')==[]
        assert q(f"MATCH (p:PullRequest {{id:'{pid}'}}) RETURN p.head_ref,p.merge_sha")==[[FEATURE,C3]]
        # The link to the deleted branch retires; links to the immutable commit and the work item stay.
        assert current(message,'REFERENCES')==sorted([cid(C2),issue])
    finally:
        db.clear_graph()


def test_ambiguous_short_sha_and_unknown_keys_do_not_link():
    db=DatabaseManager(graph_name='test_github_ambig_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    try:
        store=CompanyStore(db,MANIFEST); store.bootstrap()
        store.node('Issue',f'plane:ghtest:issue:{ITEM}',source='plane',url=PLANE_URL,title='Watcher',text='x',key='ACME-3')
        message='slack:ghtest:message:C1:2'
        store.node('Comment',message,source='slack',url='https://acme.slack.com/archives/C1/p2',
            text=f'See https://github.com/{REPO}/commit/c000000 and ACME-30, XACME-3, ACME-3x9.')
        twins={C1:'c0000001'+'0'*32,C2:'c0000002'+'0'*32}
        repository=history()
        for ref in repository['refs']['nodes']:
            for node in ref['target']['history']['nodes']:
                node['oid']=twins.get(node['oid'],node['oid']); node['parents']['nodes']=[]
            ref['target']['oid']=twins.get(ref['target']['oid'],ref['target']['oid'])
        client=GitHubClient(token='test',session=httpx.Client(transport=transport([],'No links here.',repository)))
        sync_github(db,MANIFEST,client=client)
        rows=db.graph.ro_query(f"MATCH ({{id:'{message}'}})-[r:REFERENCES]->(t) WHERE r.valid_to IS NULL RETURN t.id").result_set
        assert rows==[],rows
    finally:
        db.clear_graph()
