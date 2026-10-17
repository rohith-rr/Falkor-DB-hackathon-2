"""Resolve explicit links only; names and proximity are not identity or causality."""
import re


def link_manifest(store):
    desired={}
    for binding in store.manifest.get('bindings',[]):
        source,target,rel=binding['source'],binding['target'],binding['relationship']
        if rel not in {'CONCERNS_COMPONENT','DESCRIBES','REFERENCES','ABOUT','DEPENDS_ON'}:
            raise ValueError('Unsupported explicit binding')
        rows=store.db.graph.ro_query('MATCH (a {id:$a}),(b {id:$b}) RETURN labels(a)[0],labels(b)[0],a.source_url',
            params={'a':source,'b':target}).result_set
        if not rows: continue  # Optional Slack source can be absent.
        sl,tl,url=rows[0]
        store.edge(sl,source,tl,target,rel,'manifest',url)
        if rel=='REFERENCES': desired.setdefault(source,set()).add(f'{source}->REFERENCES->{target}')
    records=store.db.graph.ro_query('MATCH (n) WHERE n.organization_id=$org AND n.valid_to IS NULL '
        'AND n.source_system IN [\'plane\',\'slack\'] RETURN n.id,labels(n)[0],n.text_content,n.source_url',
        params={'org':store.org}).result_set
    by_url={}
    for row in sorted(records,key=lambda row:row[1] not in ('DocPage','Issue')):
        if row[3]: by_url.setdefault(row[3],(row[0],row[1]))
    for ident,label,text,url in records:
        # Slack renders <URL|label>; the label is not part of the destination.
        targets=set(re.findall(r'https?://[^\s<>"\)|]+',text or ''))
        for destination in targets:
            if destination in by_url:
                tid,tl=by_url[destination]
                store.edge(label,ident,tl,tid,'REFERENCES','manifest',url)
                desired.setdefault(ident,set()).add(f'{ident}->REFERENCES->{tid}')
    links=store.db.graph.ro_query('MATCH (n:SourceLink) WHERE n.organization_id=$org AND n.valid_to IS NULL '
        'RETURN n.linked_from,n.source_url',params={'org':store.org}).result_set
    for source,url in links:
        if url in by_url:
            target,label=by_url[url]
            store.edge('Issue',source,label,target,'REFERENCES','manifest',url)
            desired.setdefault(source,set()).add(f'{source}->REFERENCES->{target}')
    # Source edits can remove a literal link. Retain explicit manifest bindings,
    # but retire reference intervals that no current record or binding supports.
    existing=store.db.graph.ro_query('MATCH (n)-[r:REFERENCES]->() WHERE n.organization_id=$org '
        "AND n.source_system IN ['plane','slack'] AND n.valid_to IS NULL AND r.valid_to IS NULL "
        'RETURN DISTINCT n.id',params={'org':store.org}).result_set
    for row in existing:
        store.reconcile_edges(row[0],['REFERENCES'],sorted(desired.get(row[0],set())))
