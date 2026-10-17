"""Explicit bounded extraction of company source units."""
from ..extraction.extractor import ExtractionPipeline


def extract_units(db,org,limit=20,unit_id=None):
    if not 0<=limit<=100: raise ValueError('Extraction limit must be 0..100')
    pipeline=ExtractionPipeline(db=db); attempts=0; results=[]
    rows=db.graph.ro_query("MATCH (u) WHERE u.organization_id=$org AND u.valid_to IS NULL "
        "AND ($unit IS NULL OR u.id=$unit) AND (u:Comment OR u:DocSection) AND size(u.text_content)>=20 "
        "RETURN labels(u)[0],u.id,u.text_content,u.source_url,u.created_at "
        "ORDER BY u.created_at,u.id",params={'org':org,'unit':unit_id}).result_set
    if unit_id and not rows: raise ValueError('No eligible current source unit in this organization')
    for label,ident,text,url,at in rows:
        if attempts>=limit: break
        context=db.graph.ro_query('MATCH path=(u {id:$id})-[*1..3]-(n) '
            'WHERE all(r IN relationships(path) WHERE r.valid_to IS NULL) AND n.valid_to IS NULL '
            'AND (n:Issue OR n:DocPage OR n:Component OR n:Project OR n:Argument OR n:Question OR n:Decision) '
            'RETURN DISTINCT n.id,n.name,n.title,n.summary ORDER BY n.id LIMIT 50',params={'id':ident}).result_set
        author=db.graph.ro_query('MATCH (p:Person)-[:AUTHORED]->(u {id:$id}) RETURN p.id LIMIT 1',params={'id':ident}).result_set
        result=pipeline.process_text_unit(label,ident,text,source_url=url,source_timestamp=at,
            author_id=author[0][0] if author else None,context_target_ids=[r[0] for r in context],
            prev_comments_context='\n'.join(str(r) for r in context))
        if not result.get('skipped'): attempts+=1
        results.append({'unit':ident,**result})
        if result.get('error'): break
    return {'attempts':attempts,'results':results,'diagnostics':pipeline.stats.model_dump()}
