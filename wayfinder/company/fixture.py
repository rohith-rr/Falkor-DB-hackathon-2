"""Deterministic public fictional source fixture; does not call any LLM."""
import json
from pathlib import Path
from .core import CompanyStore,load_manifest
from .plane import ingest_project
from .slack import ingest_channel
from .links import link_manifest
from ..derived.concerns import ConcernResolver
from ..models.common import now_iso

ROOT=Path(__file__).resolve().parents[2]
MANIFEST=ROOT/'fixtures/company/manifest.json'
DATA=ROOT/'fixtures/company/sources.json'


def ingest_fixture(db,manifest_path=MANIFEST,data_path=DATA):
    manifest=load_manifest(manifest_path)
    if db.graph_name!='wayfinder_company_demo' and not db.graph_name.startswith('test_'):
        raise ValueError('Fixture ingestion requires wayfinder_company_demo or an isolated test_ graph')
    data=json.loads(Path(data_path).read_text(encoding='utf-8'))
    store=CompanyStore(db,manifest); store.bootstrap()
    for project in data['projects']: ingest_project(store,project,mode='fixture')
    for channel in data['channels']: ingest_channel(store,channel,mode='fixture')
    link_manifest(store)
    # Gold fixture claims are hand-authored demonstrations, not measured model output.
    for claim in data['claims']:
        source=claim['source']; cid=claim['id']; label=claim['label']
        rows=db.graph.ro_query('MATCH (n {id:$id}) RETURN n.source_url,n.source_revision_id,n.body_text',params={'id':source}).result_set
        if not rows or claim['quote'] not in rows[0][2]: raise ValueError('Fixture claim has no exact source quote')
        url,revision,_=rows[0]
        store.node(label,cid,source='fixture-annotation',url=url,summary=claim['summary'],
            evidence_quote=claim['quote'],source_revision_id=revision,source_timestamp=claim['at'],
            kind=claim.get('kind'),target_id=claim.get('target'),annotation_origin='hand_authored_demo')
        store.edge('Comment' if ':comment:' in source else 'DocPage',source,label,cid,'ASSERTS',
            'fixture-annotation',url,evidence_quote=claim['quote'],authority_override='EXTRACTED')
        # Explicitly label fixture interpretation links rather than claiming an API asserted them.
        db.query('MATCH ()-[r:ASSERTS]->(n {id:$id}) SET r.authority=\'EXTRACTED\',r.link_method=\'FIXTURE_ANNOTATION\',r.annotation_origin=\'hand_authored_demo\'',{'id':cid})
        if claim.get('author'):
            store.edge('Person',f"company:{store.org}:person:{claim['author']}",label,cid,'MADE','fixture-annotation',url)
        if claim.get('target'):
            target=claim['target']; found=db.graph.ro_query('MATCH (n {id:$id}) RETURN labels(n)[0]',params={'id':target}).result_set
            if found:
                rel=claim.get('relationship','ABOUT')
                store.edge(label,cid,found[0][0],target,rel,'fixture-annotation',url,evidence_quote=claim['quote'])
                db.query('MATCH (n {id:$id})-[r]->() WHERE type(r)=$rel SET r.authority=\'EXTRACTED\'',{'id':cid,'rel':rel})
    db.query("MATCH (n) WHERE n.source_system='fixture-annotation' AND n.organization_id=$org "
        'AND n.valid_to IS NULL AND NOT n.id IN $ids SET n.valid_to=$now WITH n '
        'OPTIONAL MATCH (n)-[r]-() WHERE r.valid_to IS NULL SET r.valid_to=$now',
        {'org':store.org,'ids':[c['id'] for c in data['claims']],'now':now_iso()})
    return {'mode':'fixture','synthetic':True,'projects':len(data['projects']),
        'issues':sum(len(p['work_items']) for p in data['projects']),
        'pages':sum(len(p['pages']) for p in data['projects']),
        'chat_threads':sum(len(c['threads']) for c in data['channels']),
        'manual_claims':len(data['claims']),'concerns':ConcernResolver(db).resolve_concern_statuses()}
