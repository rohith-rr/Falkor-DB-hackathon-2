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


def transport(searches,body=None,**extra):
    def respond(request):
        query=json.loads(request.content)['variables'].get('queryStr','')
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
        assert q("MATCH (c:SourceCoverage {source_system:'github'}) RETURN c.status,c.record_count")==[['complete',1]]
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
