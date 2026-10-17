"""Staged LOCAL overlay with real, capped inference. Never edits Plane."""
import json
import re
import time
import uuid
from pathlib import Path
from openai import OpenAI
from .fixture import ROOT,MANIFEST
from .core import CompanyStore,load_manifest
from .plane import ingest_project
from .brief import draft_brief,validate_brief
from ..models.common import now_iso
from ..extraction.extractor import ExtractionPipeline
from ..extraction.llm_client import OpenAPIClient
from ..extraction.cache import ExtractionCache

COMPONENT='company:northstar:component:resource-allocation'
PAGE='plane:northstar:page:adr-allocation'
UNIT=PAGE+':section:0'
BEFORE='Decision: preserve existing reservations and retry resize immediately, at most twice.'
AFTER='Decision: preserve existing reservations, disable automatic resize retries, and require an operator-initiated retry.'


class BudgetClient(OpenAPIClient):
    def __init__(self,state):
        super().__init__(); self.state=state
        if not self.is_live_llm_configured(): raise ValueError('Configure LLM_API_KEY; no fixture substitution is allowed')
        self._llm_client=OpenAI(base_url=self.base_url,api_key=self.api_key,timeout=60,max_retries=0)
        self._embedding_client=OpenAI(base_url=self.embedding_base_url,api_key=self.embedding_api_key,timeout=60,max_retries=0)

    def chat_completion_json(self,**kwargs):
        if self.state['generation_requests']>=2: raise RuntimeError('Two generation request budget exhausted')
        self.state['generation_requests']+=1
        start=time.monotonic()
        result=super().chat_completion_json(**{**kwargs,'max_tokens':2000})
        self.state['model_responses'].append({'at':now_iso(),'model':self.model,'origin':'live_provider',
            'elapsed_seconds':round(time.monotonic()-start,3),'proposed':result})
        return result

    def get_embedding(self,text):
        if self.state['embedding_requests']>=12: raise RuntimeError('Twelve embedding request budget exhausted')
        self.state['embedding_requests']+=1
        return super().get_embedding(text)


class LocalProof:
    def __init__(self,db,run_id=None):
        if db.graph_name!='wayfinder_company_demo' and not db.graph_name.startswith('test_'):
            raise ValueError('Local overlays require the company demo or an isolated test graph')
        self.db=db; self.run_id=run_id or uuid.uuid4().hex
        if not re.fullmatch('[0-9a-f]{32}',self.run_id): raise ValueError('Invalid run ID')
        self.path=ROOT/'data/proof-runs'/f'{self.run_id}.json'
        self.state=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
            'run_id':self.run_id,'graph':db.graph_name,'mode':'local_source_overlay','external_plane_edit':False,
            'created_at':now_iso(),'stage':'created','generation_requests':0,'embedding_requests':0,
            'model_responses':[],'steps':[],'successful':False}
        if self.state['graph']!=db.graph_name: raise ValueError('Run belongs to a different graph')

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp'); temp.write_text(json.dumps(self.state,indent=2),encoding='utf-8');temp.replace(self.path)

    def overlay(self,text):
        manifest=load_manifest(MANIFEST); store=CompanyStore(self.db,manifest)
        project={'id':'allocation','name':'Allocation','work_items':[],'items_complete':False,'pages_complete':False,
            'pages':[{'id':'adr-allocation','name':'ADR: reservation fairness','updated_at':now_iso(),
                'description_html':'<h1>ADR: reservation fairness</h1><p>Resource Allocation retry policy.</p><p>'+text+'</p>'}]}
        ingest_project(store,project,mode='local-overlay')
        self.db.query('MATCH (p:DocPage {id:$id}) SET p.local_overlay_run=$run',{'id':PAGE,'run':self.run_id})

    def claims(self):
        return [row[0] for row in self.db.graph.ro_query('MATCH (u {id:$id})-[a:ASSERTS]->(d:Decision) '
            'WHERE a.valid_to IS NULL AND d.valid_to IS NULL RETURN properties(d)',params={'id':UNIT}).result_set]

    def extract(self,llm=None):
        client=llm or BudgetClient(self.state)
        self.state['inference_mode']='live_provider' if isinstance(client,BudgetClient) else 'test_double'
        self.state['model']=client.model
        self.state['embedding_model']=getattr(client,'embedding_model',None)
        self.state['embedding_dim']=getattr(client,'embedding_dim',None)
        text=self.db.graph.ro_query('MATCH (u {id:$id}) RETURN u.text_content',params={'id':UNIT}).result_set[0][0]
        pipeline=ExtractionPipeline(self.db,client,ExtractionCache(self.path.parent/self.run_id),
            extractor_version='live-proof-'+self.run_id)
        result=pipeline.process_text_unit('DocSection',UNIT,text,context_target_ids=[COMPONENT],
            thread_title='Resource Allocation ADR; record the explicit component decision, not a goal',source_timestamp=now_iso())
        if result.get('error'): raise RuntimeError('Provider extraction failed; inspect local diagnostics and create a new run')
        return {**result,'diagnostics':pipeline.stats.model_dump(),'decisions':self.claims()}

    def advance(self,stage,*,enable_live=False,llm=None):
        if not enable_live and llm is None: raise ValueError('Explicit --enable-live is required for paid inference')
        previous={'baseline':'created','edit':'baseline','extract':'pending'}
        if stage not in previous or self.state['stage']!=previous[stage]:
            raise ValueError(f"Cannot run {stage} after {self.state['stage']}; create a new run to retry")
        # Runs cannot accidentally continue over a fixture reset or another run's edit.
        if stage!='baseline':
            owner=self.db.graph.ro_query('MATCH (p:DocPage {id:$id}) RETURN p.local_overlay_run',params={'id':PAGE}).result_set
            if not owner or owner[0][0]!=self.run_id: raise ValueError('Another run owns this source overlay')
            pointer=self.db.graph.ro_query('MATCH (u {id:$id}) RETURN u.source_revision_id',params={'id':UNIT}).result_set
            expected=self.state['baseline_revision'] if stage=='edit' else self.state['new_revision']
            if not pointer or pointer[0][0]!=expected: raise ValueError('Source changed outside this run; start a new proof')
        start=time.monotonic()
        try:
            if stage=='baseline':
                self.overlay(BEFORE)
                result=self.extract(llm)
                decisions=result['decisions']
                if not decisions or not any('twice' in d['evidence_quote'].lower() and d.get('target_id')==COMPONENT for d in decisions):
                    raise RuntimeError('Model did not publish the explicit baseline Decision; no substitute was inserted')
                brief=draft_brief(self.db,COMPONENT); validation=validate_brief(self.db,brief)
                if not validation['valid']: raise RuntimeError('Baseline brief failed validation')
                self.state.update(stage='baseline',baseline=result,old_brief=brief,baseline_validation=validation,
                    baseline_revision=decisions[0]['source_revision_id'])
            elif stage=='edit':
                self.overlay(AFTER)
                revision=self.db.graph.ro_query('MATCH (u {id:$id}) RETURN u.source_revision_id,u.semantic_stale,u.extraction_version',params={'id':UNIT}).result_set[0]
                validation=validate_brief(self.db,self.state['old_brief'])
                retired=self.db.graph.ro_query('MATCH (d:Decision) WHERE d.id IN $ids RETURN d.id,d.valid_to',
                    params={'ids':[d['id'] for d in self.state['baseline']['decisions']]}).result_set
                if validation['valid'] or self.claims() or any(not row[1] for row in retired):
                    raise RuntimeError('Old Decision/brief was not invalidated')
                self.state.update(stage='pending',new_revision=revision[0],pending={'semantic_stale':revision[1],
                    'extraction_version':revision[2],'retired_decisions':retired},old_brief_validation_after_edit=validation)
            else:
                result=self.extract(llm); decisions=result['decisions']
                if not any(d.get('target_id')==COMPONENT and 'operator' in d['evidence_quote'].lower()
                    and 'disable' in d['evidence_quote'].lower() for d in decisions):
                    raise RuntimeError('Model did not publish the changed operator-only Decision')
                brief=draft_brief(self.db,COMPONENT); validation=validate_brief(self.db,brief)
                if not validation['valid']: raise RuntimeError('Replacement brief failed validation')
                self.state.update(stage='complete',replacement=result,new_brief=brief,replacement_validation=validation,
                    successful=self.state['inference_mode']=='live_provider',completed_at=now_iso())
            self.state['steps'].append({'stage':stage,'at':now_iso(),'elapsed_seconds':round(time.monotonic()-start,3),'ok':True})
        except Exception as exc:
            self.state.update(stage='failed',error_type=type(exc).__name__)
            # Provider exceptions can contain network details: persist type, not arbitrary exception text.
            self.state['steps'].append({'stage':stage,'at':now_iso(),'ok':False,'error_type':type(exc).__name__})
            self.save(); raise
        self.save(); return self.state


def main():
    import argparse
    from ..db import DatabaseManager
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id');parser.add_argument('--stage',choices=['baseline','edit','extract','all'],default='all')
    parser.add_argument('--enable-live',action='store_true');parser.add_argument('--output')
    args=parser.parse_args(); proof=LocalProof(DatabaseManager(graph_name='wayfinder_company_demo'),args.run_id)
    for stage in ['baseline','edit','extract'] if args.stage=='all' else [args.stage]:
        state=proof.advance(stage,enable_live=args.enable_live)
        print(json.dumps({'run_id':proof.run_id,'stage':state['stage'],'generation_requests':state['generation_requests'],
            'embedding_requests':state['embedding_requests']}),flush=True)
    if args.output:
        if not state['successful']: raise ValueError('Only a successful real run can be exported as proof')
        Path(args.output).write_text(json.dumps(state,indent=2),encoding='utf-8')


if __name__=='__main__': main()
