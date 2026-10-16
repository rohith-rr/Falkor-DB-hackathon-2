"""Publish a complete interpretation in one atomic FalkorDB statement."""
import re


def publish(db, unit_id, unit_type, text, revision_id, nodes, edges, *, expected,
            version, generation, now, source_url):
    params = dict(unit=unit_id, expected=expected, version=version, generation=generation,
                  now=now, text=text, revision=revision_id, url=source_url)
    created={p['id'] for _,p in nodes}|{unit_id}
    params['targets']=sorted({x for _,sid,_,tid,_,_ in edges for x in (sid,tid)}-created)
    dependencies=[]
    for sl,sid,tl,tid,_,_ in edges:
        for label,ident in ((sl,sid),(tl,tid)):
            if ident not in created and (label,ident) not in dependencies: dependencies.append((label,ident))
    preflight=''
    for i,(label,ident) in enumerate(dependencies):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',label): raise ValueError('Invalid endpoint label')
        params[f'dep{i}']=ident
        preflight+=f'MATCH (dependency{i}:{label} {{id:$dep{i}}}) WHERE dependency{i}.valid_to IS NULL '
    query = ["MATCH (u {id:$unit}) WHERE coalesce(u.extraction_version,'')=coalesce($expected,'') "
             "AND (coalesce(u.body_text,u.text_content) IS NULL OR coalesce(u.body_text,u.text_content)=$text) "
             "WITH u OPTIONAL MATCH (target) WHERE target.id IN $targets AND target.valid_to IS NULL "
             "WITH u,count(DISTINCT target.id) AS found WHERE found=size($targets) "+preflight+
             "WITH u OPTIONAL MATCH (u)-[a:ASSERTS]->(old) WHERE a.authority='EXTRACTED' AND a.valid_to IS NULL "
             "WITH u,collect(old) AS old_nodes,collect(a) AS assertions "
             "FOREACH (r IN assertions | SET r.valid_to=$now) "
             "FOREACH (n IN old_nodes | SET n.valid_to=$now) "
             "WITH u,old_nodes OPTIONAL MATCH (old)-[r]-() WHERE old IN old_nodes AND r.valid_to IS NULL "
             "WITH u,collect(r) AS retired FOREACH (r IN retired | SET r.valid_to=$now) "
             "WITH u OPTIONAL MATCH (u)-[tag:ABOUT_TOPIC|ANSWERS]->() WHERE tag.authority='EXTRACTED' AND tag.valid_to IS NULL "
             "WITH u,collect(tag) AS tags FOREACH (r IN tags | SET r.valid_to=$now) "
             "MERGE (revision:SourceRevision {id:$revision}) ON CREATE SET revision.text_content=$text,revision.source_url=$url,"
             "revision.source_unit_id=$unit,revision.observed_at=$now "
             "SET u.extraction_version=$version,u.extraction_generation=$generation,u.semantic_stale=false,u.source_revision_id=$revision "]
    for i, (label, original) in enumerate(nodes):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', label):
            raise ValueError('Invalid node label')
        props = dict(original, valid_to=None)
        embedding = props.pop('embedding', None)
        params[f'n{i}'] = props
        query.append(f'MERGE (n{i}:{label} {{id:$n{i}.id}}) SET n{i}+=$n{i} ')
        if embedding is not None:
            params[f'v{i}'] = embedding
            query.append(f'SET n{i}.embedding=vecf32($v{i}) ')
    for i, (sl, sid, tl, tid, rel, props) in enumerate(edges):
        if not all(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', x) for x in (sl,tl,rel)):
            raise ValueError('Invalid relationship')
        params[f's{i}'],params[f't{i}']=sid,tid
        params[f'e{i}']=dict(props,valid_to=None)
        # Targets were verified before staging. MATCH failure is treated as a conflict.
        query.append(f'WITH * MATCH (s{i}:{sl} {{id:$s{i}}}),(t{i}:{tl} {{id:$t{i}}}) '
                     f'MERGE (s{i})-[e{i}:{rel} {{id:$e{i}.id}}]->(t{i}) SET e{i}+=$e{i} ')
    query.append('RETURN u.id')
    result=db.query(''.join(query),params)
    if not result.result_set:
        raise RuntimeError('Extraction publication conflict: source or generation changed; retry')
