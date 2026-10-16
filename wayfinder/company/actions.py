"""Human-approved, hash-bound Plane creation. No approval-granting MCP tool."""
import html
import json
from .core import digest,now_iso
from .brief import validate_brief
from .plane import PlaneClient


class PlaneActions:
    def __init__(self,db,manifest,client=None):
        self.db,self.manifest,self.client=db,manifest,client
        if not manifest.get('synthetic'): raise ValueError('Actions require a fictional sandbox manifest')

    def prepare(self,title,description,brief):
        result=validate_brief(self.db,brief)
        if not result['valid']: raise ValueError('Invalid brief: '+'; '.join(result['errors']))
        if not title.strip() or len(title)>255 or not description.strip() or len(description)>10000:
            raise ValueError('Supply title <=255 characters and description <=10000 characters')
        if len(json.dumps(brief).encode())>32768: raise ValueError('Brief exceeds 32 KiB; narrow the investigation')
        payload={'project_id':self.manifest['plane']['sandbox_project_id'],
            'workspace':self.manifest['plane']['workspace'],'title':title,'description':description,
            'brief':brief}
        if payload['project_id'] not in self.manifest['plane']['project_ids']: raise ValueError('Sandbox project is not allowlisted')
        hashed=digest(payload); ident='action:plane:'+hashed
        evidence=sorted({i for key in ('ownership','contributors','decisions','concerns') for c in brief.get(key,[]) for i in c['evidence_ids']})
        self.db.query('MERGE (a:Action {id:$id}) ON CREATE SET a.status=\'proposed\',a.payload_json=$payload,'
            'a.payload_hash=$hash,a.created_at=$now,a.observed_at=$now,a.valid_from=$now,a.source_ref=$id,'
            'a.source_url=\'derived:plane-proposal\',a.evidence_ids=$evidence,a.kind=\'create_plane_work_item\',a.source_system=\'derived\' '
            'RETURN a.status',{'id':ident,'payload':json.dumps(payload,sort_keys=True),'hash':hashed,'now':now_iso(),'evidence':evidence})
        for claim in sum([brief.get(k,[]) for k in ('ownership','contributors','decisions','concerns')],[]):
            for evidence in claim.get('evidence_ids',[]):
                self.db.query('MATCH (a:Action {id:$id}),(n {id:$evidence}) MERGE (a)-[r:BASED_ON {id:$edge}]->(n) '
                    'ON CREATE SET r.source_ref=$evidence,r.source_url=n.source_url,r.observed_at=$now,'
                    'r.valid_from=$now,r.authority=\'COMPUTED\',r.link_method=\'COMPUTED\'',
                    {'id':ident,'evidence':evidence,'edge':f'{ident}->BASED_ON->{evidence}','now':now_iso()})
        return {'action_id':ident,'payload_hash':hashed,'payload':payload,'approval_required':True}

    def get(self,ident):
        rows=self.db.graph.ro_query('MATCH (a:Action {id:$id}) RETURN properties(a)',params={'id':ident}).result_set
        if not rows: raise ValueError('Action not found')
        return rows[0][0]

    def approve(self,ident,expected_hash,approved_by):
        action=self.get(ident)
        if action['payload_hash']!=expected_hash or digest(json.loads(action['payload_json']))!=expected_hash:
            raise ValueError('Approval hash does not match the displayed payload')
        if not approved_by.strip(): raise ValueError('Name the approving human')
        result=self.db.query('MATCH (a:Action {id:$id}) WHERE a.status=\'proposed\' AND a.payload_hash=$hash '
            'SET a.status=\'approved\',a.approved_hash=$hash,a.approved_by=$who,a.approved_at=$now RETURN a.id',
            {'id':ident,'hash':expected_hash,'who':approved_by,'now':now_iso()})
        if not result.result_set: raise ValueError('Only a proposed action can be approved')
        return {'action_id':ident,'status':'approved'}

    def execute(self,ident):
        action=self.get(ident)
        if action['status']=='executed': return {'action_id':ident,'status':'executed','external_id':action['external_id']}
        payload=json.loads(action['payload_json'])
        if digest(payload)!=action.get('approved_hash') or action.get('approved_hash')!=action['payload_hash']:
            raise ValueError('Human approval of this exact payload is required')
        if payload['workspace']!=self.manifest['plane']['workspace'] or payload['project_id']!=self.manifest['plane']['sandbox_project_id']:
            raise ValueError('Action is outside the current sandbox')
        validation=validate_brief(self.db,payload['brief'])
        if not validation['valid']: raise ValueError('Evidence changed; prepare and approve a new action')
        client=self.client or PlaneClient(payload['workspace'])
        path=f"projects/{payload['project_id']}/work-items/"
        try:
            if action['status'] in ('executing','uncertain'):
                matches=[r for r in client.listing(path) if r.get('external_id')==ident and r.get('external_source')=='wayfinder']
                if not matches: raise ValueError('Previous outcome is uncertain; no blind retry. Reconcile in Plane before preparing another task.')
                created=matches[0]
            else:
                claimed=self.db.query('MATCH (a:Action {id:$id}) WHERE a.status=\'approved\' '
                    'SET a.status=\'executing\' RETURN a.id',{'id':ident})
                if not claimed.result_set: raise ValueError('Action is not approved or is already executing')
                citations='\n'.join(sorted({url for k in ('ownership','contributors','decisions','concerns')
                    for c in payload['brief'].get(k,[]) for url in c.get('source_urls',[])}))
                body=payload['description']+'\n\nEvidence:\n'+citations+'\n\nWayfinder action: '+ident
                try:
                    created=client.request('POST',path,json={'name':payload['title'],
                        'description_html':'<pre>'+html.escape(body)+'</pre>',
                        'external_id':ident,'external_source':'wayfinder'})
                except Exception:
                    self.db.query('MATCH (a:Action {id:$id}) SET a.status=\'uncertain\'',{'id':ident})
                    raise
            if not created.get('id'): raise RuntimeError('Plane response has no work-item ID; reconcile action')
            self.db.query('MATCH (a:Action {id:$id}) SET a.status=\'executed\',a.external_id=$external,a.executed_at=$now',
                {'id':ident,'external':str(created['id']),'now':now_iso()})
            return {'action_id':ident,'status':'executed','external_id':str(created['id'])}
        finally:
            if self.client is None: client.close()
