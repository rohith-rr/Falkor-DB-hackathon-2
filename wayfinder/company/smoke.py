"""Read-only MCP investigation transcript against the prepared company demo."""
import asyncio
import json
import os
from pathlib import Path
import sys
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from .fixture import ROOT


async def check(output):
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'wayfinder/mcp_server.py')],
        env={**os.environ,'FALKORDB_GRAPH':'wayfinder_company_demo'})
    transcript=[]
    async with stdio_client(params) as (reader,writer):
        async with ClientSession(reader,writer) as session:
            await session.initialize()
            async def call(name,arguments):
                result=await session.call_tool(name,arguments)
                if result.isError: raise RuntimeError(f'{name} failed: {result.content}')
                transcript.append({'tool':name,'arguments':arguments,'result':result.structuredContent})
                return result.structuredContent
            await call('graph_schema',{})
            await call('knowledge_coverage',{})
            search=await call('search_entities',{'query':'Resource Allocation','labels':['Component'],'limit':2})
            cid=search['items'][0]['n']['id']
            context=await call('company_context',{'component':cid,'limit':20})
            expertise=await call('who_knows',{'topic_or_dir':cid,'limit':10})
            if not expertise['items'] or expertise['items'][0]['login']!='faye': raise RuntimeError('Unified code expertise missing')
            await call('semantic_search',{'query':'reservation retry starvation','limit':5})
            page=await call('get_neighbors',{'entity_id':cid,'limit':20})
            neighbors=list(page['items'])
            while page.get('next_cursor'):
                page=await call('get_neighbors',{'entity_id':cid,'limit':20,'cursor':page['next_cursor']})
                neighbors.extend(page['items'])
            issue=next(r['m']['id'] for r in neighbors if 'Issue' in r['m']['labels'])
            await call('get_thread',{'entity_id':issue,'limit':20})
            for item in context['related']:
                props=item['entity']['properties']
                if props.get('source_revision_id'):
                    await call('get_entity',{'entity_id':props['source_revision_id']})
                    break
            from ..db import DatabaseManager
            from .brief import draft_brief
            brief=draft_brief(DatabaseManager(graph_name='wayfinder_company_demo'),cid)
            ids=list(dict.fromkeys(i for c in brief['decisions']+brief['concerns']+brief['contributors'] for i in c['evidence_ids']))
            await call('get_evidence',{'evidence_ids':ids})
            validation=await call('validate_review_brief',{'brief':brief})
            if not validation['valid']: raise RuntimeError('Brief evidence validation failed')
    output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'mode':'scripted_navigation_not_an_agent_accuracy_evaluation',
        'at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        'brief':brief,'transcript':transcript},indent=2),encoding='utf-8')
    print(f'MCP investigation passed; transcript: {output}')


if __name__=='__main__':
    asyncio.run(check(sys.argv[1] if len(sys.argv)>1 else ROOT/'data/company-mcp-transcript.json'))
