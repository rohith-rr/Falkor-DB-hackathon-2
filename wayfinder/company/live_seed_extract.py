"""Bounded real extraction of selected records imported from the seeded workspaces."""
import argparse
import json
import time
from pathlib import Path
from openai import OpenAI
from .fixture import ROOT
from .core import load_manifest
from .live_seed import ORG,content
from .brief import draft_brief,validate_brief
from ..db import DatabaseManager
from ..models.common import now_iso
from ..extraction.llm_client import OpenAPIClient
from ..extraction.extractor import ExtractionPipeline
from ..derived.concerns import ConcernResolver


class CappedClient(OpenAPIClient):
    def __init__(self):
        super().__init__()
        if not self.is_live_llm_configured() or not self.is_live_embedding_configured():
            raise ValueError('Real configured generation and embedding credentials required')
        self.generations=0;self.embeddings=0;self.responses=[]
        self._llm_client=OpenAI(base_url=self.base_url,api_key=self.api_key,timeout=60,max_retries=0)
        self._embedding_client=OpenAI(base_url=self.embedding_base_url,api_key=self.embedding_api_key,timeout=60,max_retries=0)

    def chat_completion_json(self,**kwargs):
        if self.generations>=8: raise RuntimeError('Eight generation request cap reached')
        self.generations+=1;start=time.monotonic()
        value=super().chat_completion_json(**{**kwargs,'max_tokens':2000})
        self.responses.append({'origin':'live_provider','elapsed_seconds':round(time.monotonic()-start,3),'proposed':value})
        return value

    def get_embedding(self,text):
        if self.embeddings>=60: raise RuntimeError('Sixty embedding request cap reached')
        self.embeddings+=1;return super().get_embedding(text)


def selected_units(state):
    ids=state['created'];result=[]
    data=content();pages={p['key']:p['component'] for p in data['pages']}
    comments={c['key']:i['component'] for i in data['items'] for c in i['comments']}
    for key in ('adr-runtime','adr-allocation','rfc-retry'):
        component=pages[key]
        result.append((f"plane:{ORG}:page:{ids['page:'+key]['id']}:section:0",component))
    for key in ('pc1','pc13','pc7'):
        component=comments[key]
        result.append((f"plane:{ORG}:comment:{ids['comment:'+key]['id']}",component))
    for key,index in [('t0-0',0),('t2-0',2)]:
        component=data['channel_components'][data['threads'][index]['channel']]
        row=ids['slack:'+key];result.append((f"slack:{ORG}:comment:{row['channel']}:{row['id']}",component))
    return result


def run(folder,graph,enable_live=False):
    if not enable_live: raise ValueError('Explicit --enable-live required for model requests')
    if graph!='wayfinder_company_live_demo': raise ValueError('This runner only targets the separate live connector demo')
    folder=Path(folder);manifest=load_manifest(folder/'manifest.json')
    if manifest['organization_id']!=ORG: raise ValueError('Unexpected organization')
    state=json.loads((folder/'seed-state.json').read_text(encoding='utf-8'))
    transfer=json.loads((folder/'provider-transfer-review.json').read_text(encoding='utf-8'))
    if transfer['graph']!=graph or transfer['destination']!='https://openrouter.ai/api/v1':
        raise ValueError('Transfer review does not match this graph or configured destination')
    reviewed={u['id']:u for u in transfer['units']}
    db=DatabaseManager(graph_name=graph);client=CappedClient();pipeline=ExtractionPipeline(db,client)
    if client.base_url.rstrip('/')!=transfer['destination']: raise ValueError('Provider changed after transfer review')
    if client.embedding_base_url.rstrip('/')!=transfer.get('embedding_destination',transfer['destination']):
        raise ValueError('Embedding provider changed after transfer review')
    report={'at':now_iso(),'graph':graph,'mode':'live_connector_sources_real_model_extraction',
        'fixture_annotations_imported':False,'model':client.model,'embedding_model':client.embedding_model,
        'embedding_dim':client.embedding_dim,'units':[]}
    try:
        for ident,component in selected_units(state):
            cid=f'company:{ORG}:component:{component}'
            rows=db.graph.ro_query('MATCH (u {id:$id}) WHERE u.valid_to IS NULL RETURN labels(u)[0],u.text_content,u.created_at,u.source_url,u.source_revision_id',params={'id':ident}).result_set
            if not rows: raise ValueError('Selected source not imported: '+ident)
            label,text,at,url,revision=rows[0]
            expected=reviewed.get(ident,{})
            if text!=expected.get('text_to_send') or revision!=expected.get('source_revision_id'):
                raise ValueError('Source changed after transfer review; review the new content before export')
            authors=db.graph.ro_query('MATCH (p:Person)-[:AUTHORED]->(u {id:$id}) RETURN p.id LIMIT 1',params={'id':ident}).result_set
            concerns=db.graph.ro_query("MATCH (a:Argument {kind:'concern'})-[r:RAISES_CONCERN_ON]->(c:Component {id:$id}) WHERE a.valid_to IS NULL AND r.valid_to IS NULL RETURN a.id,a.summary",params={'id':cid}).result_set
            print(f'Extracting {len(report["units"])+1}/8: {ident}',flush=True)
            value=pipeline.process_text_unit(label,ident,text,author_id=authors[0][0] if authors else None,
                context_target_ids=[cid]+[r[0] for r in concerns],thread_title=f'{component}: staged Wayfinder project records; preserve the stated verification limits',
                prev_comments_context=json.dumps({'component_id':cid,'existing_concerns':concerns}),
                source_url=url,source_timestamp=at)
            report['units'].append({'id':ident,'component_id':cid,**value})
            if value.get('error'): raise RuntimeError('Extraction stopped on source-unit error; inspect the local report')
        report['concerns']=ConcernResolver(db).resolve_concern_statuses()
        report['briefs']={}
        for item in manifest['components']:
            component=item['id']
            brief=draft_brief(db,f'company:{ORG}:component:{component}')
            report['briefs'][component]={'brief':brief,'validation':validate_brief(db,brief)}
        report['completed']=True
    finally:
        report.update(generation_requests=client.generations,embedding_requests=client.embeddings,
            responses=client.responses,diagnostics=pipeline.stats.model_dump())
        (folder/'extraction-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    summary={k:report[k] for k in ('graph','generation_requests','embedding_requests','diagnostics','concerns')}
    summary['briefs']={k:{'decisions':len(v['brief']['decisions']),'concerns':len(v['brief']['concerns']),
        'valid':v['validation']['valid']} for k,v in report['briefs'].items()}
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',default=str(ROOT/'data/live-workspace'))
    parser.add_argument('--graph',default='wayfinder_company_live_demo');parser.add_argument('--enable-live',action='store_true')
    args=parser.parse_args();print(json.dumps(run(args.folder,args.graph,args.enable_live),indent=2))
