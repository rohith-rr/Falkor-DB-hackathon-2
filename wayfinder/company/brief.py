"""Evidence collection and mechanical brief validation, not semantic proof."""
from ..extraction.verifier import QuoteVerifier
from .core import response_context


def component_context(db,component,limit=20):
    if not 1<=limit<=100: raise ValueError('Use limit 1..100')
    rows=db.graph.ro_query('MATCH (c:Component) WHERE c.id=$term OR toLower(c.name)=toLower($term) RETURN c',
        params={'term':component},timeout=5000).result_set
    if len(rows)!=1: raise ValueError('Resolve exactly one Component ID using search_entities')
    node=rows[0][0]; cid=node.properties['id']
    owners=db.graph.ro_query('MATCH (p:Person)-[r:OWNS_COMPONENT]->(c:Component {id:$id}) '
        'WHERE r.valid_to IS NULL AND p.valid_to IS NULL RETURN p,r ORDER BY p.id LIMIT $limit',
        params={'id':cid,'limit':limit},timeout=5000).result_set
    related=db.graph.ro_query('MATCH (c:Component {id:$id})<-[r]-(n) WHERE r.valid_to IS NULL AND n.valid_to IS NULL '
        'AND type(r) IN [\'CONCERNS_COMPONENT\',\'DESCRIBES\',\'ABOUT\',\'RAISES_CONCERN_ON\'] '
        'RETURN n,r ORDER BY n.id LIMIT $limit',params={'id':cid,'limit':limit+1},timeout=5000).result_set
    from ..brain_graph import serialize
    endpoint_ids={node.id:cid,**{p.id:p.properties['id'] for p,_ in owners},**{n.id:n.properties['id'] for n,_ in related}}
    directories=db.graph.ro_query('MATCH (:Component {id:$id})-[r:IMPLEMENTED_IN]->(d:Directory) '
        'WHERE r.valid_to IS NULL AND d.valid_to IS NULL RETURN d',params={'id':cid},timeout=5000).result_set
    return {'component':serialize(node),'code_directories':[serialize(r[0]) for r in directories],
        'owners':[{'person':serialize(p),'relationship':serialize(r,endpoint_ids)} for p,r in owners],
        'related':[{'entity':serialize(n),'relationship':serialize(r,endpoint_ids)} for n,r in related[:limit]],
        'truncated':len(related)>limit,'context':response_context(db),
        'reviewer_warning':'Declared component ownership does not confer code reviewer authorization. Inspect OWNERS separately.'}


def validate_brief(db,brief):
    errors=[]
    required=('component_id','ownership','contributors','decisions','concerns','missing_evidence','next_action','context')
    for key in required:
        if key not in brief: errors.append(f'Missing section: {key}')
    if not db.graph.ro_query('MATCH (c {id:$id}) WHERE (c:Component OR c:Directory) AND c.valid_to IS NULL RETURN c.id',
        params={'id':brief.get('component_id')},timeout=5000).result_set:
        errors.append('Component ID is missing, retired, or not a component/directory')
    if not isinstance(brief.get('context'),dict) or not brief.get('context',{}).get('warnings'):
        errors.append('Coverage warnings are required')
    claims=[]
    for key in ('ownership','contributors','decisions','concerns'):
        if not isinstance(brief.get(key,[]),list): errors.append(f'{key} must be a list'); continue
        claims.extend(brief.get(key,[]))
    for index,claim in enumerate(claims):
        if not isinstance(claim,dict): errors.append(f'Claim {index} is not an object'); continue
        ids=claim.get('evidence_ids',[]); urls=claim.get('source_urls',[])
        if not ids or not urls or claim.get('authority') not in ('CONFIRMED','EXTRACTED','COMPUTED'):
            errors.append(f'Claim {index} requires evidence, URLs, and authority'); continue
        found=[]
        for ident in ids:
            rows=db.graph.ro_query('MATCH (n {id:$id}) WHERE n.valid_to IS NULL AND coalesce(n.is_deleted,false)=false '
                'RETURN properties(n),labels(n)',params={'id':ident},timeout=5000).result_set
            rows=[({**r[0],'_labels':r[1]},) for r in rows]
            if not rows:
                rows=db.graph.ro_query('MATCH ()-[r]->() WHERE (r.id=$id OR r.logical_id=$id) AND r.valid_to IS NULL '
                    'RETURN properties(r)',params={'id':ident},timeout=5000).result_set
            if not rows: errors.append(f'Claim {index}: missing or retired evidence {ident}')
            found.extend(r[0] for r in rows)
        known_urls={r.get('source_url') for r in found}
        if not set(urls)<=known_urls: errors.append(f'Claim {index}: URL does not match its evidence')
        if claim.get('authority')=='CONFIRMED' and any(r.get('authority') in ('EXTRACTED','COMPUTED','CANDIDATE') or
            set(r.get('_labels',[])) & {'Argument','Decision','Rationale','Question'} for r in found):
            errors.append(f'Claim {index}: interpretation cannot be promoted to CONFIRMED')
        if 'status' in claim:
            concerns=[r for r in found if r.get('kind')=='concern']
            if len(concerns)!=1 or concerns[0].get('status')!=claim['status']:
                errors.append(f'Claim {index}: concern status is not the current computed status')
            elif claim['status']=='resolved' and (not concerns[0].get('resolution_evidence_ids') or
                not set(concerns[0]['resolution_evidence_ids'])<=set(ids)):
                errors.append(f'Claim {index}: resolved status requires its specific resolution source evidence')
        revision=claim.get('source_revision_id')
        quote=claim.get('evidence_quote')
        if claim.get('authority')=='EXTRACTED' and (not revision or not quote):
            errors.append(f'Claim {index}: extracted claims require a revision and quote')
        if revision:
            rows=db.graph.ro_query('MATCH (r:SourceRevision {id:$id}) RETURN r.text_content,r.source_unit_id',params={'id':revision},timeout=5000).result_set
            if not rows or not QuoteVerifier.verify_quote(quote,rows[0][0]): errors.append(f'Claim {index}: quote/revision mismatch')
            elif not db.graph.ro_query('MATCH (n {id:$id}) WHERE n.source_revision_id=$revision AND n.valid_to IS NULL RETURN n.id',
                params={'id':rows[0][1],'revision':revision},timeout=5000).result_set:
                errors.append(f'Claim {index}: cited source revision is stale')
    return {'valid':not errors,'errors':errors,'claims_checked':len(claims),
        'semantic_correctness':'Not mechanically proven; evaluate interpretation against the source.',
        'context':response_context(db)}


def draft_brief(db,component):
    context=component_context(db,component,100)
    brief={'component_id':context['component']['id'],'ownership':[],'contributors':[],
        'decisions':[],'concerns':[],'missing_evidence':['No validated company-wide expertise ranking; contributors are unknown.',
            'Code reviewer authorization requires code ownership evidence.'],
        'next_action':'Review unresolved concerns and prepare a scoped follow-up task for human approval.',
        'context':context['context']}
    for item in context['owners']:
        p=item['person']['properties']; r=item['relationship']['properties']
        brief['ownership'].append({'summary':f"{p['login']} is a declared component owner",
            'evidence_ids':[r['id']],'source_urls':[r['source_url']],'authority':'CONFIRMED'})
    for item in context['related']:
        n=item['entity']; p=n['properties']
        if not ('Decision' in n['labels'] or p.get('kind')=='concern'): continue
        claim={'summary':p.get('summary','Unknown'),'evidence_ids':[p['id']],
            'source_urls':[p['source_url']],'authority':'EXTRACTED',
            'source_revision_id':p.get('source_revision_id'),'evidence_quote':p.get('evidence_quote')}
        if p.get('kind')=='concern':
            claim['status']=p.get('status','open');claim['status_authority']='COMPUTED'
            for evidence in p.get('resolution_evidence_ids',[]):
                rows=db.graph.ro_query('MATCH (n {id:$id}) RETURN n.source_url',params={'id':evidence}).result_set
                if rows:
                    claim['evidence_ids'].append(evidence)
                    if rows[0][0] not in claim['source_urls']: claim['source_urls'].append(rows[0][0])
            brief['concerns'].append(claim)
        else: brief['decisions'].append(claim)
    if context['code_directories']:
        from ..derived.expertise_v2 import get_rankings
        report=get_rankings(db,brief['component_id'])
        brief['context']['expertise']={k:v for k,v in report.items() if k not in ('items','involvement_only')}
        brief['missing_evidence'][0]='Code expertise measures synthetic imported implementation/file-review evidence, not company-wide competence.'
        for person in report['items'][:5]:
            brief['contributors'].append({'summary':f"{person['login']}: code activity score {person['score']}, supported by {person['distinct_pr_count']} PRs",
                'evidence_ids':person['evidence_ids'],'source_urls':person['source_urls'],'authority':'COMPUTED',
                'person_id':person['person_id'],'score':person['score'],'score_breakdown':person['score_breakdown'],
                'distinct_pr_count':person['distinct_pr_count'],'evidence_strength':person['evidence_strength'],
                'generation':report['generation'],'calculated_at':report.get('calculated_at')})
    return brief
