"""Local three-view evidence dashboard. No ingestion or paid calls on page load."""
import argparse
import json
import secrets
import threading
from pathlib import Path
from starlette.applications import Starlette
from starlette.responses import JSONResponse,FileResponse
from starlette.routing import Route,Mount
from starlette.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.concurrency import run_in_threadpool
from .db import DatabaseManager
from .brain_graph import BrainGraph
from .company.fixture import ROOT
from .company.live_proof import LocalProof
from .company.capabilities import indexes

ASSETS=Path(__file__).parent/'web'
READ_TOOLS={'who_knows','ownership','decisions','concerns','stale_findings','graph_stats','graph_schema',
    'search_entities','get_entity','get_neighbors','find_paths','get_evidence','get_thread','person_context',
    'component_context','list_questions','recent_activity','knowledge_coverage','read_cypher','company_context',
    'validate_review_brief','semantic_search'}


def create_app(db=None,enable_live=False):
    db=db or DatabaseManager(graph_name='wayfinder_company_demo'); brain=BrainGraph(db)
    token=secrets.token_urlsafe(32); proof_lock=threading.Lock()
    def artifact(name):
        path=ROOT/'docs/evidence'/name
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    def latest_proof():
        files=sorted((ROOT/'data/proof-runs').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        for path in files:
            value=json.loads(path.read_text(encoding='utf-8'))
            if value['graph']==db.graph_name: return value
        return artifact('live-extraction-run.json') if db.graph_name=='wayfinder_company_demo' else None
    async def home(request): return FileResponse(ASSETS/'index.html')
    async def session(request): return JSONResponse({'token':token,'enable_live':enable_live})
    async def state(request):
        component=request.query_params.get('component','company:northstar:component:resource-allocation')
        def read():
            proof=latest_proof()
            if proof:
                from .company.live_proof import UNIT
                rows=db.graph.ro_query('MATCH (u {id:$id}) RETURN u.source_revision_id',params={'id':UNIT},timeout=5000).result_set
                proof['matches_current_source']=bool(rows) and rows[0][0]==proof.get('new_revision',proof.get('baseline_revision'))
            return {'context':brain.invoke('company_context',component=component,limit=12),
                'expertise':brain.invoke('who_knows',topic_or_dir=component,limit=10),
                'indexes':indexes(db),'proof':proof,'capabilities':artifact('falkordb-capabilities.json'),
                'graph':db.graph_name,'enable_live':enable_live}
        return JSONResponse(await run_in_threadpool(read))
    async def body(request):
        if not secrets.compare_digest(request.headers.get('X-Wayfinder-Session',''),token):
            raise ValueError('Session token required')
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'): raise ValueError('Same-origin request required')
        content=await request.body()
        if len(content)>65536: raise ValueError('Request exceeds 64 KiB')
        data=json.loads(content)
        if not isinstance(data,dict): raise ValueError('Expected JSON object')
        return data
    async def tool(request):
        try:
            data=await body(request); name=request.path_params['name']
            if name not in READ_TOOLS: raise ValueError('Read tool is not allowlisted')
            if name=='semantic_search' and not enable_live: raise ValueError('Enable live requests explicitly before semantic search')
            return JSONResponse(await run_in_threadpool(lambda:brain.invoke(name,**data)))
        except (ValueError,TypeError): return JSONResponse({'error':'Invalid tool request, session, origin, or disabled live requests'},status_code=400)
    async def create_proof(request):
        try:
            await body(request)
            if not enable_live: raise ValueError('Live controls disabled')
            proof=LocalProof(db);proof.save();return JSONResponse(proof.state)
        except ValueError: return JSONResponse({'error':'Live controls disabled or invalid session'},status_code=400)
    async def advance_proof(request):
        try:
            data=await body(request)
            if not enable_live: raise ValueError('Live controls disabled')
            if not proof_lock.acquire(blocking=False): return JSONResponse({'error':'Another proof step is running'},status_code=409)
            try:
                proof=LocalProof(db,request.path_params['run_id'])
                value=await run_in_threadpool(lambda:proof.advance(data['stage'],enable_live=True))
                return JSONResponse(value)
            finally: proof_lock.release()
        except Exception as exc:
            return JSONResponse({'error':type(exc).__name__,'message':'Proof step failed; inspect its saved run log. No substitute result was inserted.'},status_code=400)
    app=Starlette(routes=[Route('/',home),Route('/api/session',session),Route('/api/state',state),
        Route('/api/tools/{name}',tool,methods=['POST']),Route('/api/proofs',create_proof,methods=['POST']),
        Route('/api/proofs/{run_id}/advance',advance_proof,methods=['POST']),Mount('/assets',StaticFiles(directory=ASSETS))])
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
        return response
    app.add_middleware(BaseHTTPMiddleware,dispatch=headers)
    return app


def main():
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--enable-live',action='store_true');args=parser.parse_args()
    uvicorn.run(create_app(enable_live=args.enable_live),host='127.0.0.1',port=args.port)


if __name__=='__main__': main()
