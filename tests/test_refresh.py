"""One-command refresh: pending selection (pages first), a call budget, no model calls by default, model provenance."""
import uuid
import pytest
from wayfinder.db import DatabaseManager
from wayfinder.company import extract as extract_module
from wayfinder.company import freshness
from wayfinder.company.core import CompanyStore
from wayfinder.company.extract import pending_units
from wayfinder.extraction.extractor import ExtractionPipeline
from wayfinder.extraction.cache import ExtractionCache

MANIFEST={'organization_id':'rf','synthetic':True,'plane':{'workspace':'w','project_ids':['p']},
    'slack':{'channel_ids':['C1']},'github':{'repository':'a/b'},'people':[],'identities':{},'teams':[],'components':[],'bindings':[]}
TEXT='This unit has enough words to be worth extracting.'


@pytest.fixture
def db():
    instance=DatabaseManager(graph_name='test_refresh_'+uuid.uuid4().hex)
    if not instance.ping(): pytest.skip('FalkorDB unavailable')
    store=CompanyStore(instance,MANIFEST); store.bootstrap()
    store.node('Comment','slack:rf:comment:C1:1',source='slack',url='https://x.slack.com/1',text=TEXT,updated='2026-10-17T03:00:00Z')
    store.node('Comment','plane:rf:comment:old',source='plane',url='https://app.plane.so/1',text=TEXT,updated='2026-10-17T01:00:00Z')
    store.node('Comment','plane:rf:comment:new',source='plane',url='https://app.plane.so/2',text=TEXT,updated='2026-10-17T02:00:00Z')
    store.node('DocSection','plane:rf:page:1:section:0',source='plane',url='https://app.plane.so/3',text=TEXT,heading='# Runbook')
    store.node('Comment','plane:rf:comment:done',source='plane',url='https://app.plane.so/4',text=TEXT)
    store.node('Comment','plane:rf:comment:short',source='plane',url='https://app.plane.so/5',text='ok')
    instance.upsert_node('Comment',{'id':'github:rf:comment:1','body_text':TEXT,'source_system':'github','organization_id':'rf'})
    instance.query("MATCH (c {id:'plane:rf:comment:done'}) SET c.extraction_version='v1:x'")
    yield instance
    instance.clear_graph()


def test_pending_units_order_pages_then_newest_plane_then_slack(db):
    ids=[u['id'] for u in pending_units(db,'rf')]
    assert ids==['plane:rf:page:1:section:0','plane:rf:comment:new','plane:rf:comment:old','slack:rf:comment:C1:1']
    # Editing an extracted unit clears its extraction, so it is pending again.
    CompanyStore(db,MANIFEST).node('Comment','plane:rf:comment:done',source='plane',url='https://app.plane.so/4',text=TEXT+' Edited.')
    assert 'plane:rf:comment:done' in [u['id'] for u in pending_units(db,'rf')]


def test_refresh_syncs_configured_sources_and_makes_no_model_calls_by_default(db,monkeypatch):
    calls={}
    def fake_sync(database,manifest,**flags):
        calls.update(flags)
        return {'plane':[{'items':3,'pages':1}],'slack':[{'channel':'C1','threads':1}],'github':{'pull_requests':2,'branches':1,'commits':4},
            'changes':{'summary':{'created':0,'updated':0,'retired':0,'edges_added':0,'edges_retired':0}}}
    monkeypatch.setattr(freshness,'sync_once',fake_sync)
    monkeypatch.setattr(extract_module,'extract_units',lambda *a,**k:pytest.fail('refresh must not extract without --extract'))
    summary=freshness.refresh(db,MANIFEST)
    assert calls=={'slack':True,'github':True}
    assert summary['pending_before']=={'page':1,'plane message':2,'slack message':1}=={**summary['pending_after']}
    assert 'extraction' not in summary and summary['next_pending'][0].startswith('DocSection plane: # Runbook')
    assert summary['sources']['github']=={'pull_requests':2,'branches':1,'commits':4}
    freshness.refresh(db,{**MANIFEST,'slack':{},'github':{}})
    assert calls=={'slack':False,'github':False}


def test_refresh_extracts_pending_units_in_priority_order_within_budget(db,monkeypatch):
    monkeypatch.setattr(freshness,'sync_once',lambda *a,**k:{'plane':[],'changes':{'summary':{}}})
    seen={}
    def fake_extract(database,org,limit,units=None):
        seen.update(limit=limit,units=units)
        return {'attempts':2,'model':'fake','results':[{'unit':units[0],'accepted':1,'rejected':0}],
            'diagnostics':{'accepted':1,'rejected':0,'rejection_reasons':[]}}
    monkeypatch.setattr(extract_module,'extract_units',fake_extract)
    summary=freshness.refresh(db,MANIFEST,extract=2,slack=False,github=False)
    assert seen['limit']==2 and seen['units'][:2]==['plane:rf:page:1:section:0','plane:rf:comment:new']
    assert summary['extraction']['model']=='fake' and summary['extraction']['generation_calls']==2


class FakeLLM:
    model='fake-model'; base_url='https://llm.example'
    def chat_completion_json(self,**kwargs):
        return {'arguments':[{'kind':'explanation','summary':'Use bounded retries.','evidence_quote':'Use bounded retries.'}],
            'decisions':[],'rationales':[],'questions':[],'answers':[],'topics':[]}
    def get_embedding(self,text): return [1.0,0.0,0.0]


def test_extracted_claims_record_the_model(db,tmp_path):
    text='Decision: Use bounded retries. Unlimited retries starve reservations.'
    db.upsert_node('Comment',{'id':'unit-model','body_text':text,'source_url':'https://example.org/s'})
    ExtractionPipeline(db,FakeLLM(),ExtractionCache(tmp_path)).process_text_unit('Comment','unit-model',text)
    rows=db.graph.ro_query("MATCH ({id:'unit-model'})-[r:ASSERTS]->(c) RETURN c.llm_model,r.llm_model,c.llm_base_url").result_set
    assert rows==[['fake-model','fake-model','https://llm.example']]
