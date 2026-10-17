import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from ..models.common import now_iso


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.hidden=0; self.links=[]

    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'): self.hidden+=1
        if tag in ('p','div','li','br') or re.fullmatch('h[1-6]',tag): self.parts.append('\n')
        if re.fullmatch('h[1-6]',tag): self.parts.append('#'*int(tag[1])+' ')
        if tag=='a': self.links.append(dict(attrs).get('href',''))

    def handle_endtag(self,tag):
        if tag in ('script','style'): self.hidden=max(0,self.hidden-1)
        if tag in ('p','div','li') or re.fullmatch('h[1-6]',tag): self.parts.append('\n')
        if tag=='a' and self.links:
            href=self.links.pop()
            if href.startswith(('https://','http://')): self.parts.append(' <'+href+'>')

    def handle_data(self,data):
        if not self.hidden: self.parts.append(data)


def html_text(value):
    parser=PageText(); parser.feed(value or '')
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def load_manifest(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if not data.get('synthetic'):
        raise ValueError('This release accepts only explicitly synthetic sandbox manifests')
    if not data.get('organization_id') or not data.get('plane',{}).get('project_ids'):
        raise ValueError('Manifest requires organization_id and explicit Plane project_ids')
    return data


class CompanyStore:
    def __init__(self,db,manifest):
        self.db,self.manifest=db,manifest
        self.org=manifest['organization_id']

    def person(self,source,native):
        # Explicit cross-source mapping only; unmatched native identities remain separate.
        key=self.manifest.get('identities',{}).get(f'{source}:{native}')
        ident=f'company:{self.org}:person:{key}' if key else f'{source}:{self.org}:person:{native}'
        return ident

    def node(self,label,ident,*,source,url,text=None,native=None,updated=None,scope=None,**props):
        stamp=now_iso()
        old=self.db.graph.ro_query('MATCH (n {id:$id}) RETURN n.content_hash',params={'id':ident}).result_set
        revision=None
        if props.get('raw_html') is not None:
            raw=props['raw_html']; raw_id=f'raw-revision:{ident}:{hashlib.sha256(raw.encode()).hexdigest()}'
            self.db.query('MERGE (r:RawSourceRevision {id:$id}) ON CREATE SET r += $props',
                {'id':raw_id,'props':{'raw_content':raw,'source_unit_id':ident,'source_url':url,
                    'source_ref':native or ident,'source_updated_at':updated,'observed_at':stamp}})
            props['raw_revision_id']=raw_id
        if text is not None:
            revision=f'source-revision:{ident}:{hashlib.sha256(text.encode()).hexdigest()}'
            self.db.query('MERGE (r:SourceRevision {id:$id}) ON CREATE SET r += $props',
                {'id':revision,'props':{'text_content':text,'source_unit_id':ident,'source_url':url,
                 'source_ref':native or ident,'source_updated_at':updated,'observed_at':stamp,
                 'synthetic':True,'organization_id':self.org}})
            if old and old[0][0] and old[0][0]!=digest(text):
                # Changed sources invalidate current meaning even before an LLM is available.
                self.db.query("MATCH (u {id:$id})-[r:ASSERTS]->(n) WHERE r.authority='EXTRACTED' AND r.valid_to IS NULL "
                    'SET r.valid_to=$now,n.valid_to=$now,u.semantic_stale=true',{'id':ident,'now':stamp})
        values=dict(id=ident,source_system=source,source_url=url,source_ref=native or ident,
            source_updated_at=updated,observed_at=stamp,organization_id=self.org,synthetic=True,
            sync_scope=scope,valid_to=None,is_deleted=False,**props)
        if text is not None: values.update(text_content=text,body_text=text,content_hash=digest(text),source_revision_id=revision)
        self.db.upsert_node(label,values)
        return ident

    def edge(self,sl,sid,tl,tid,rel,source,url,**props):
        self.db.upsert_edge(sl,sid,tl,tid,rel,dict(id=f'{sid}->{rel}->{tid}',source_system=source,
            source_url=url,source_ref=sid,observed_at=now_iso(),authority='CONFIRMED',
            link_method='API' if source!='manifest' else 'STRUCTURED_FILE',**props))

    def reconcile_edges(self,source_id,types,desired):
        self.db.query('MATCH (n {id:$id})-[r]->() WHERE type(r) IN $types AND r.valid_to IS NULL '
            'AND NOT coalesce(r.logical_id,r.id) IN $ids SET r.valid_to=$now',
            {'id':source_id,'types':types,'ids':desired,'now':now_iso()})

    def coverage(self,source,scope,status,*,since=None,through=None,count=0,gaps=None,mode='live'):
        ident=f'coverage:{self.org}:{source}:{scope}'
        props={'id':ident,'source_system':source,'scope':scope,'organization_id':self.org,
            'status':status,'synthetic':True,'mode':mode,'observed_at':now_iso(),'record_count':count,
            'requested_since':since,'gaps':gaps or [],'pagination_complete':status=='complete'}
        if status=='complete': props.update(covered_through=through or now_iso(),watermark=through or now_iso())
        if status=='complete' or (status=='partial' and count>0): props['successful_observation_at']=now_iso()
        self.db.upsert_node('SourceCoverage',props)

    def reconcile(self,source,scope,ids):
        # Called only after an exhaustive authorized listing.
        params={'source':source,'scope':scope,'org':self.org,'ids':ids,'now':now_iso()}
        self.db.query('MATCH (n) WHERE n.source_system=$source AND n.sync_scope=$scope '
            'AND n.organization_id=$org AND n.valid_to IS NULL AND NOT n.id IN $ids '
            "MATCH (n)-[assertion:ASSERTS]->(claim) WHERE assertion.authority='EXTRACTED' AND assertion.valid_to IS NULL "
            'SET assertion.valid_to=$now,claim.valid_to=$now WITH claim MATCH (claim)-[r]-() '
            'WHERE r.valid_to IS NULL SET r.valid_to=$now',params)
        self.db.query('MATCH (n) WHERE n.source_system=$source AND n.sync_scope=$scope '
            'AND n.organization_id=$org AND n.valid_to IS NULL AND NOT n.id IN $ids '
            'SET n.is_deleted=true,n.valid_to=$now WITH n OPTIONAL MATCH (n)-[r]-() '
            'WHERE r.valid_to IS NULL SET r.valid_to=$now',
            params)

    def bootstrap(self):
        m=self.manifest; url=m.get('source_url','https://example.org/wayfinder-synthetic')
        oid=f'company:{self.org}'
        self.db.upsert_node('Organization',{'id':oid})  # Establish a new named graph before read-only lookups.
        self.node('Organization',oid,source='manifest',url=url,name=m.get('name',self.org))
        for person in m.get('people',[]):
            self.node('Person',f"company:{self.org}:person:{person['id']}",source='manifest',url=url,
                      login=person['id'],display_name=person['name'],is_bot=False)
        for team in m.get('teams',[]):
            tid=f"company:{self.org}:team:{team['id']}"
            self.node('Team',tid,source='manifest',url=url,name=team['name'])
            for person in team['members']:
                self.edge('Person',f'company:{self.org}:person:{person}','Team',tid,'MEMBER_OF','manifest',url)
        for component in m.get('components',[]):
            cid=f"company:{self.org}:component:{component['id']}"
            self.node('Component',cid,source='manifest',url=url,name=component['name'])
            for owner in component['owners']:
                self.edge('Person',f'company:{self.org}:person:{owner}','Component',cid,'OWNS_COMPONENT','manifest',url)
            self.db.query('MATCH (p:Person)-[r:OWNS_COMPONENT]->(c:Component {id:$id}) '
                'WHERE r.valid_to IS NULL AND NOT p.id IN $owners SET r.valid_to=$now',
                {'id':cid,'owners':[f'company:{self.org}:person:{p}' for p in component['owners']],'now':now_iso()})


def response_context(db,operational=False):
    rows=db.graph.ro_query('MATCH (c:SourceCoverage) RETURN properties(c) LIMIT 50',timeout=5000).result_set
    coverage=[r[0] for r in rows]
    now=datetime.now(timezone.utc)
    for c in coverage:
        try:
            observed=datetime.fromisoformat(c.get('successful_observation_at',c.get('covered_through','')).replace('Z','+00:00'))
            c['age_seconds']=max(0,(now-observed).total_seconds())
        except (KeyError,ValueError): c['age_seconds']=None
        c['stale']=c['age_seconds'] is None or c['age_seconds']>1200
    github=db.graph.ro_query('MATCH (c:ActivityCoverage) RETURN properties(c) LIMIT 1',timeout=5000).result_set
    clocks=db.graph.ro_query('MATCH (d:DemoDataset) RETURN properties(d) LIMIT 5',timeout=5000).result_set
    prs=db.graph.ro_query('MATCH (p:PullRequest) RETURN count(p)',timeout=5000).result_set
    pending=db.graph.ro_query("MATCH (n) WHERE n.organization_id IS NOT NULL AND n.valid_to IS NULL "
        "AND (n:Comment OR n:DocSection) AND n.extraction_version IS NULL RETURN count(n)",timeout=5000).result_set
    return {'source_scope':[{'source':c['source_system'],'scope':c['scope'],'status':c['status'],
        'mode':c.get('mode'),'synthetic':c.get('synthetic'),'observed_at':c.get('observed_at'),
        'age_seconds':c['age_seconds'],'stale':c['stale'],
        'covered_through':c.get('covered_through'),'gaps':c.get('gaps',[]),'record_count':c.get('record_count',0)} for c in coverage],
        'freshness':'not_applicable' if operational else 'source_observation_times_only',
        'clock':{'actual_now_utc':now.isoformat(),'datasets':[r[0] for r in clocks],
            'event_time':'fixed offsets before persisted dataset anchor' if clocks else 'source-system record timestamps',
            'decay_time':'expertise snapshot calculated_at',
            'staleness_time':'actual current UTC minus calculated_at'},
        'imported_pr_count':prs[0][0] if prs else 0,
        'supporting_pr_count':None,
        'units_without_live_extraction':pending[0][0] if pending else 0,
        'github_activity_coverage':[r[0] for r in github],
        'missing_evidence':['No proven complete GitHub activity coverage'] if not github else [],
        'warnings':['Coverage describes configured scopes, not the whole company.',
                    'Fixture observations are not live synchronization.'] if any(c.get('mode')=='fixture' for c in coverage) else
                   ['Coverage describes configured scopes, not the whole company.']}
