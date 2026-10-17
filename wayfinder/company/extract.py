"""Explicit bounded extraction of company source units."""
from ..extraction.extractor import ExtractionPipeline

# Documents carry decisions and rationale; chat is the noisiest. Lower rank is extracted first.
PRIORITY="CASE WHEN u:DocSection THEN 0 WHEN u.source_system='plane' THEN 1 ELSE 2 END"
UNIT=("u.organization_id=$org AND u.valid_to IS NULL AND (u:Comment OR u:DocSection) "
      "AND u.source_system IN ['plane','slack'] AND size(coalesce(u.text_content,''))>=20")


def pending_units(db,org):
    """Current units without an extraction for their current text: never extracted, or edited since.

    A source edit clears extraction_version, so edited units come back here automatically.
    """
    rows=db.graph.ro_query(f'MATCH (u) WHERE {UNIT} AND u.extraction_version IS NULL '
        f'RETURN labels(u)[0],u.id,u.source_system,coalesce(u.heading,substring(u.text_content,0,60)),{PRIORITY} AS rank '
        'ORDER BY rank,coalesce(u.source_updated_at,u.created_at) DESC,u.id',params={'org':org}).result_set
    return [{'label':r[0],'id':r[1],'source':r[2],'preview':r[3]} for r in rows]


def _extract(db,pipeline,label,ident,text,url,at):
    context=db.graph.ro_query('MATCH path=(u {id:$id})-[*1..3]-(n) '
        'WHERE all(r IN relationships(path) WHERE r.valid_to IS NULL) AND n.valid_to IS NULL '
        'AND (n:Issue OR n:DocPage OR n:Component OR n:Project OR n:Argument OR n:Question OR n:Decision) '
        'RETURN DISTINCT n.id,n.name,n.title,n.summary ORDER BY n.id LIMIT 50',params={'id':ident}).result_set
    author=db.graph.ro_query('MATCH (p:Person)-[:AUTHORED]->(u {id:$id}) RETURN p.id LIMIT 1',params={'id':ident}).result_set
    return pipeline.process_text_unit(label,ident,text,source_url=url,source_timestamp=at,
        author_id=author[0][0] if author else None,context_target_ids=[r[0] for r in context],
        prev_comments_context='\n'.join(str(r) for r in context))


def extract_units(db,org,limit=20,unit_id=None,units=None):
    """Extract up to `limit` units: one unit, an explicit ordered list, or the oldest eligible units."""
    if not 0<=limit<=100: raise ValueError('Extraction limit must be 0..100')
    pipeline=ExtractionPipeline(db=db); attempts=0; results=[]
    if units is not None:
        rows=[]
        for ident in units:
            found=db.graph.ro_query(f'MATCH (u {{id:$id}}) WHERE {UNIT} RETURN labels(u)[0],u.id,u.text_content,u.source_url,u.created_at',
                params={'org':org,'id':ident}).result_set
            rows.extend(found)
    else:
        rows=db.graph.ro_query("MATCH (u) WHERE u.organization_id=$org AND u.valid_to IS NULL "
            "AND ($unit IS NULL OR u.id=$unit) AND (u:Comment OR u:DocSection) AND size(u.text_content)>=20 "
            "RETURN labels(u)[0],u.id,u.text_content,u.source_url,u.created_at "
            "ORDER BY u.created_at,u.id",params={'org':org,'unit':unit_id}).result_set
    if unit_id and not rows: raise ValueError('No eligible current source unit in this organization')
    for label,ident,text,url,at in rows:
        if attempts>=limit: break
        result=_extract(db,pipeline,label,ident,text,url,at)
        if not result.get('skipped'): attempts+=1
        results.append({'unit':ident,**result})
        if result.get('error'): break
    return {'attempts':attempts,'results':results,'diagnostics':pipeline.stats.model_dump(),
            'model':getattr(pipeline.llm,'model',None)}
