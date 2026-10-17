"""Measured FalkorDB capabilities; preparation is explicit, search is read-only."""
import json
import hashlib
import math
import time
from pathlib import Path
from openai import OpenAI
from ..config import settings
from ..db import NODE_LABELS_FOR_INDEX
from ..models.common import now_iso
from ..extraction.llm_client import OpenAPIClient

LABELS={'Topic':'name','Argument':'summary','Comment':'body_text'}


def indexes(db):
    result=db.graph.ro_query('CALL db.indexes()',timeout=5000)
    normalized=[]
    for values in result.result_set:
        row=dict(zip([h[1] for h in result.header],values)); row.pop('info',None)
        if isinstance(row['types'],dict):
            for prop,kinds in row['types'].items():
                normalized.append({**row,'properties':[prop],'types':kinds,'options':row['options'].get(prop,{})})
        else: normalized.append(row)
    return normalized


def prepare_schema(db):
    if db.graph_name not in ('wayfinder_company_demo','wayfinder_company_live_demo') and not db.graph_name.startswith('test_'):
        raise ValueError('Demo schema changes require company demo or isolated test graph')
    existing=indexes(db)
    def has(label,prop,kind):
        return any(x['label']==label and prop in x['properties'] and kind in x['types'] for x in existing)
    for label in [*NODE_LABELS_FOR_INDEX,'DemoDataset']:
        if not has(label,'id','RANGE'): db.query(f'CREATE INDEX FOR (n:{label}) ON (n.id)')
    for label,field in LABELS.items():
        if not has(label,field,'FULLTEXT'):
            db.query(f"CALL db.idx.fulltext.createNodeIndex('{label}', '{field}')")
        if not has(label,'embedding','VECTOR'):
            db.query(f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.embedding) OPTIONS {{dimension:{settings.embedding_dim}, similarityFunction:'cosine'}}")
    deadline=time.monotonic()+20
    while True:
        current=indexes(db)
        if all(str(x['status']).upper()=='OPERATIONAL' for x in current): break
        if time.monotonic()>deadline: raise RuntimeError('Indexes are not operational; inspect db.indexes()')
        time.sleep(.1)
    for label in LABELS:
        vector=next(x for x in current if x['label']==label and 'VECTOR' in x['types'])
        options=vector['options']
        if isinstance(options,str): options=json.loads(options)
        if int(options.get('dimension',0))!=settings.embedding_dim or str(options.get('similarityFunction','')).lower()!='cosine':
            raise RuntimeError('Existing vector index does not match configured dimension/cosine; no index was dropped')
    return {'at':now_iso(),'graph':db.graph_name,'indexes':current,'embedding_model':settings.embedding_model,'embedding_dim':settings.embedding_dim}


def embedding_client():
    client=OpenAPIClient()
    client._embedding_client=OpenAI(base_url=client.embedding_base_url,api_key=client.embedding_api_key,timeout=60,max_retries=0)
    return client


def query_embedding(text):
    return embedding_client().get_embedding(text)


def semantic_search(db,query,labels=None,limit=10):
    labels=list(dict.fromkeys(labels or LABELS))
    if not query.strip() or len(query)>4000: raise ValueError('Use a nonempty query up to 4000 characters')
    if not 1<=limit<=20 or not labels or set(labels)-set(LABELS): raise ValueError('Use Topic/Argument/Comment labels and limit 1..20')
    current=indexes(db)
    if any(not any(i['label']==label and 'VECTOR' in i['types'] and str(i['status']).upper()=='OPERATIONAL' for i in current) for label in labels):
        raise ValueError('Vector indexes are unavailable; run explicit company capability preparation')
    vector=query_embedding(query)  # One request, no automatic retries/caching or DB write.
    return vector_search(db,vector,labels,limit,query=query)


def vector_search(db,vector,labels,limit,query=None):
    if len(vector)!=settings.embedding_dim or not all(math.isfinite(v) for v in vector): raise ValueError('Query vector does not match configured dimension')
    from ..brain_graph import serialize
    items=[]; k=min(100,limit*5)
    for label in labels:
        if label not in LABELS: raise ValueError('Unsupported semantic label')
        rows=db.graph.ro_query(f"CALL db.idx.vector.queryNodes('{label}', 'embedding', $k, vecf32($vector)) YIELD node,score "
            'WHERE node.valid_to IS NULL AND coalesce(node.is_deleted,false)=false '
            'AND node.embedding_model=$model AND node.embedding_dim=$dim '
            'RETURN node,score ORDER BY score ASC LIMIT $limit',params={'k':k,'vector':vector,'limit':limit,
                'model':settings.embedding_model,'dim':settings.embedding_dim},timeout=5000).result_set
        # Pinned FalkorDB returns cosine DISTANCE (self-query ~=0), not similarity.
        items.extend({'entity':serialize(n),'native_distance':distance,'score':max(-1.,min(1.,1.-float(distance)))} for n,distance in rows)
    items.sort(key=lambda x:(-x['score'],x['entity']['id']))
    return {'graph':db.graph_name,'query':query,'items':items[:limit],'embedding_model':settings.embedding_model,
        'embedding_dim':settings.embedding_dim,'retrieval':'native_falkordb_cosine_vector_index',
        'score_semantics':'similarity=1-native cosine distance; higher is closer; verified by self-query on pinned server',
        'warnings':['Similarity is not confidence or factual support. Inspect sources.',
            'Current/model filtering follows bounded ANN retrieval; results are not exhaustive.'],
        'query_embedding_requests':1 if query is not None else 0}


def prepare_vectors(db):
    client=embedding_client(); records=[]; requests=0
    preferred={'Topic':'company:northstar:topic:resource-allocation','Argument':'company:northstar:claim:pc7','Comment':'plane:northstar:comment:pc7'}
    for label,field in LABELS.items():
        rows=db.graph.ro_query(f'MATCH (n:{label}) WHERE n.valid_to IS NULL AND n.{field} IS NOT NULL '
            f'RETURN n.id,n.{field},n.embedding_model,n.embedding_dim,n.embedding_text_hash '
            'ORDER BY CASE WHEN n.id=$preferred THEN 0 ELSE 1 END,n.id LIMIT 1',
            params={'preferred':preferred.get(label,'')},timeout=5000).result_set
        if not rows: raise ValueError(f'No current {label} text for showcase')
        ident,text,model,dim,old_hash=rows[0]; text=text[:16000]; fingerprint=hashlib.sha256(text.encode()).hexdigest()
        cached=model==settings.embedding_model and dim==settings.embedding_dim and old_hash==fingerprint
        if not cached:
            vector=client.get_embedding(text);requests+=1
            db.upsert_node(label,{'id':ident,'embedding':vector,'embedding_model':settings.embedding_model,
                'embedding_dim':settings.embedding_dim,'embedding_generated_at':now_iso(),'embedding_text_hash':fingerprint})
        records.append({'id':ident,'label':label,'origin':'cached_live_provider' if cached else 'live_provider','text':text})
    return {'embedding_requests':requests,'records':records}


def migrate_embeddings(db,enable_live=False):
    """Move one graph to the configured embedding model and dimension.

    New vectors are requested first. Only after every request succeeds are mismatched
    vector indexes dropped, vectors from other models cleared, indexes recreated at the
    configured dimension, and the new vectors written. A provider failure changes nothing.
    """
    if db.graph_name not in ('wayfinder_company_demo','wayfinder_company_live_demo') and not db.graph_name.startswith('test_'):
        raise ValueError('Embedding migration requires a company demo or isolated test graph')
    stale=db.graph.ro_query('MATCH (n) WHERE n.embedding IS NOT NULL AND (n.embedding_model IS NULL '
        'OR n.embedding_model<>$model OR n.embedding_dim IS NULL OR n.embedding_dim<>$dim) '
        'RETURN labels(n)[0],n.id,n.embedding_model,n.embedding_dim ORDER BY n.id',
        params={'model':settings.embedding_model,'dim':settings.embedding_dim}).result_set
    unsupported=[r[1] for r in stale if r[0] not in LABELS]
    if unsupported: raise ValueError(f'No re-embedding text field for {unsupported[:3]}; nothing changed')
    if stale and not enable_live: raise ValueError(f'{len(stale)} vectors need re-embedding; pass --enable-live')
    client=embedding_client() if stale else None; prepared=[]
    for label,ident,old_model,old_dim in stale:
        text=db.graph.ro_query(f'MATCH (n:{label} {{id:$id}}) RETURN n.{LABELS[label]}',params={'id':ident}).result_set[0][0]
        if not text: raise ValueError(f'{ident} has no {LABELS[label]} text; nothing changed')
        text=text[:16000]
        prepared.append((label,ident,old_model,old_dim,text,client.get_embedding(text)))
    dropped=[]
    for index in indexes(db):
        if 'VECTOR' not in index['types']: continue
        options=index['options']; options=json.loads(options) if isinstance(options,str) else options
        if int(options.get('dimension',0))!=settings.embedding_dim:
            db.query(f"DROP VECTOR INDEX FOR (n:{index['label']}) ON (n.{index['properties'][0]})")
            dropped.append({'label':index['label'],'dimension':int(options.get('dimension',0))})
    # Drops on populated labels finish asynchronously; recreating too early would be skipped.
    deadline=time.monotonic()+20
    while any(i['label'] in {d['label'] for d in dropped} and 'VECTOR' in i['types'] for i in indexes(db)):
        if time.monotonic()>deadline: raise RuntimeError('Dropped vector indexes are still present; inspect db.indexes()')
        time.sleep(.1)
    db.query('MATCH (n) WHERE n.embedding IS NOT NULL AND (n.embedding_model IS NULL OR n.embedding_model<>$model '
        'OR n.embedding_dim IS NULL OR n.embedding_dim<>$dim) SET n.embedding=null,n.embedding_model=null,'
        'n.embedding_dim=null,n.embedding_text_hash=null',{'model':settings.embedding_model,'dim':settings.embedding_dim})
    schema=prepare_schema(db)
    for label,ident,_,_,text,vector in prepared:
        db.upsert_node(label,{'id':ident,'embedding':vector,'embedding_model':settings.embedding_model,
            'embedding_dim':settings.embedding_dim,'embedding_generated_at':now_iso(),
            'embedding_text_hash':hashlib.sha256(text.encode()).hexdigest()})
    return {'at':now_iso(),'graph':db.graph_name,'embedding_model':settings.embedding_model,'embedding_dim':settings.embedding_dim,
        'dropped_indexes':dropped,'recreated_vector_indexes':[l for l in LABELS],'embedding_requests':len(prepared),
        'reembedded':[{'label':l,'id':i,'previous_model':m,'previous_dim':d} for l,i,m,d,_,_ in prepared],
        'indexes_operational':all(str(x['status']).upper()=='OPERATIONAL' for x in schema['indexes'])}


def record_showcase(db,output,enable_live=False):
    schema=prepare_schema(db)
    if not enable_live: return schema
    vectors=prepare_vectors(db)
    text='reservation retry starvation'; vector=query_embedding(text)
    fulltext="CALL db.idx.fulltext.queryNodes('Argument', $query) YIELD node,score WHERE node.valid_to IS NULL RETURN node,score ORDER BY score DESC LIMIT 10"
    vector_query="CALL db.idx.vector.queryNodes('Argument', 'embedding', 10, vecf32($vector)) YIELD node,score WHERE node.valid_to IS NULL RETURN node,score LIMIT 10"
    from ..brain_graph import BrainGraph
    result={'at':now_iso(),'graph':db.graph_name,'schema':schema,'vector_preparation':vectors,
        'query_embedding_requests':1,'total_embedding_requests':vectors['embedding_requests']+1,
        'fulltext':{'query':fulltext,'parameters':{'query':'reservation'},
            'result':BrainGraph(db).read_cypher(fulltext,{'query':'reservation'}),
            'explain':str(db.graph.explain(fulltext,params={'query':'reservation'}))},
        'vector':{'query':vector_query,'query_text':text,'embedding_model':settings.embedding_model,
            'result':vector_search(db,vector,list(LABELS),10),
            'explain':str(db.graph.explain(vector_query,params={'vector':vector}))},
        'path':BrainGraph(db).find_paths('company:northstar:component:resource-allocation','company:northstar:person:faye',
            ['IMPLEMENTED_IN','EXPERT_IN'],max_hops=5)}
    if not result['fulltext']['result']['items'] or not result['vector']['result']['items']:
        raise RuntimeError('Full-text/vector demonstration returned no current results')
    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return {'output':str(path),'embedding_requests':vectors['embedding_requests']+1,'fulltext_hits':len(result['fulltext']['result']['items']),
        'vector_hits':len(result['vector']['result']['items'])}


if __name__=='__main__':
    import argparse
    from ..db import DatabaseManager
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--enable-live',action='store_true')
    parser.add_argument('--graph',default='wayfinder_company_demo')
    parser.add_argument('--migrate-embeddings',action='store_true',help='Re-embed and rebuild vector indexes for the configured model/dimension')
    parser.add_argument('--output',default='docs/evidence/falkordb-capabilities.json');args=parser.parse_args()
    db=DatabaseManager(graph_name=args.graph)
    if args.migrate_embeddings: print(json.dumps(migrate_embeddings(db,args.enable_live),indent=2))
    else: print(json.dumps(record_showcase(db,args.output,args.enable_live),indent=2))
