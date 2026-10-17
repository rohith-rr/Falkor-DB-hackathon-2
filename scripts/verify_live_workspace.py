"""Read back actual seed records through the connector graph and a real MCP client.

No external writes, no LLM calls, and no query embeddings.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from wayfinder.db import DatabaseManager
from wayfinder.company.core import load_manifest
from wayfinder.company.core import html_text
from wayfinder.company.live_seed_polish import plain_slack
from wayfinder.company.live_seed import content
from wayfinder.company.brief import draft_brief,validate_brief
from wayfinder.models.common import now_iso

GRAPH='wayfinder_company_live_demo'
FOLDER=ROOT/'data/live-workspace'


def extraction_state():
    path=FOLDER/'extraction-report.json'
    if not path.exists(): return 'pending_explicit_external_provider_transfer_approval'
    report=json.loads(path.read_text(encoding='utf-8'))
    return {'completed':bool(report.get('completed')),'at':report.get('at'),'model':report.get('model'),
        'embedding_model':report.get('embedding_model'),'generation_requests':report.get('generation_requests'),
        'embedding_requests':report.get('embedding_requests'),'diagnostics':report.get('diagnostics')}


async def run():
    db=DatabaseManager(graph_name=GRAPH);manifest=load_manifest(FOLDER/'manifest.json')
    seed=json.loads((FOLDER/'seed-state.json').read_text(encoding='utf-8'))
    notices=sum(k.startswith('slack:notice-') for k in seed['created'])
    # Plane/Slack seed counts; GitHub PRs, review threads and comments are counted separately below.
    counts={r[0]:r[1] for r in db.graph.ro_query("MATCH (n) WHERE n.valid_to IS NULL AND coalesce(n.source_system,'')<>'github' "
        'RETURN labels(n)[0],count(n)').result_set}
    github={r[0]:r[1] for r in db.graph.ro_query("MATCH (n) WHERE n.valid_to IS NULL AND n.source_system='github' "
        'RETURN labels(n)[0],count(n)').result_set}
    assert counts['Issue']==12 and counts['DocPage']==6 and counts['Comment']==50+notices and counts['Thread']==18+notices
    assert counts['Component']==len(manifest['components'])
    assignments=db.graph.ro_query('MATCH (:Issue)-[a:ASSIGNED_TO]->(:Person) WHERE a.valid_to IS NULL RETURN count(a)').result_set[0][0]
    assert assignments==12
    coverage=db.graph.ro_query('MATCH (c:SourceCoverage) RETURN c.source_system,c.status,c.mode,c.record_count,c.gaps').result_set
    assert not any(r[1]=='unavailable' for r in coverage),coverage
    bot_authors=db.graph.ro_query("MATCH (p:Person)-[r:AUTHORED]->(m:Comment) WHERE m.source_system='slack' AND r.valid_to IS NULL RETURN p.is_bot,count(m)").result_set
    assert bot_authors==[[True,30+notices]],bot_authors
    polish=None
    if (FOLDER/'content-update-result.json').exists():
        revision=json.loads((FOLDER/'content-update-plan.json').read_text(encoding='utf-8'))
        for change in revision['changes']:
            kind,key=change['key'].split(':',1);record=seed['created'][change['key']]
            if kind=='slack': ident=f"slack:northstar-live:comment:{record['channel']}:{record['id']}"
            else: ident=f"plane:northstar-live:{'issue' if kind=='item' else kind}:{record['id']}"
            current=db.graph.ro_query('MATCH (n {id:$id}) WHERE n.valid_to IS NULL RETURN n.text_content,n.title',params={'id':ident}).result_set
            assert current,change['key']
            payload=change['payload']
            if kind=='slack':
                assert plain_slack(current[0][0])==plain_slack(payload['text']),change['key']
                assert 'Wayfinder seed' not in current[0][0] and 'API authorship' not in current[0][0]
            else:
                expected=html_text(payload.get('description_html',payload.get('comment_html','')))
                assert current[0][0]==expected,change['key']
                if 'name' in payload:assert current[0][1]==payload['name'],change['key']
        references=db.graph.ro_query("MATCH (m:Comment)-[r:REFERENCES]->(n) WHERE m.source_system='slack' AND m.valid_to IS NULL AND r.valid_to IS NULL RETURN count(r)").result_set[0][0]
        expected_refs=sum(len(v) for t in content()['threads'] for v in t.get('links',{}).values())
        assert references==expected_refs,references
        polish={'verified_current_source_records':len(revision['changes']),'labeled_slack_source_links':references,
                'visible_seed_footers':0,'disclosures':notices}
    items=content()['items'];states={}
    for item in items:
        ident='plane:northstar-live:issue:'+seed['created']['item:'+item['key']]['id']
        row=db.graph.ro_query('MATCH (i:Issue {id:$id}) RETURN i.state,i.priority,i.target_date',params={'id':ident}).result_set[0]
        expected={'completed':'Done','started':'In Progress','unstarted':'Todo'}[item['status']]
        assert row==[expected,item['priority'],content()['target_date']],(item['key'],row)
        states[expected]=states.get(expected,0)+1
    dependencies=db.graph.ro_query('MATCH (:Issue)-[r:DEPENDS_ON]->(:Issue) WHERE r.valid_to IS NULL RETURN count(r)').result_set[0][0]
    assert dependencies==sum(len(i['depends_on']) for i in items)
    calls=[]
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'wayfinder/mcp_server.py')],
        env={**os.environ,'FALKORDB_GRAPH':GRAPH})
    async with stdio_client(params) as (reader,writer):
        async with ClientSession(reader,writer) as session:
            await session.initialize()
            async def call(name,args):
                result=await session.call_tool(name,args)
                if result.isError: raise RuntimeError(name+' failed: '+str(result.content))
                calls.append({'tool':name,'arguments':args,'result':result.structuredContent})
                return result.structuredContent
            stats=await call('graph_stats',{})
            assert stats['graph']==GRAPH
            await call('graph_schema',{})
            await call('knowledge_coverage',{})
            cid='company:northstar-live:component:'+content()['mcp_component']
            context=await call('company_context',{'component':cid,'limit':6})
            assert len(context['owners'])==1
            neighbors=await call('get_neighbors',{'entity_id':cid,'limit':8})
            item=next(r['m']['id'] for r in neighbors['items'] if 'Issue' in r['m']['labels'])
            await call('get_thread',{'entity_id':item,'limit':20})
            evidence=await call('get_evidence',{'evidence_ids':[item]})
            assert evidence['items']
            brief=draft_brief(db,cid)
            validation=await call('validate_review_brief',{'brief':brief})
            assert validation['valid']
    report={'at':now_iso(),'graph':GRAPH,'mode':'real_connector_readback_and_scripted_mcp_navigation',
        'counts':counts,'github_counts':github,'assignments':assignments,'task_states':states,'task_dependencies':dependencies,
        'slack_bot_authors':bot_authors,'coverage':coverage,
        'content_revision_verification':polish,'model_requests':0,'extraction_state':extraction_state(),
        'semantic_accuracy':'not_evaluated','transcript':calls}
    (FOLDER/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('transcript','coverage')},indent=2))


if __name__=='__main__':asyncio.run(run())
