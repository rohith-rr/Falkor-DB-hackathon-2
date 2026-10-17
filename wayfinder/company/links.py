"""Resolve explicit links only; names and proximity are not identity or causality."""
import re

# Slack renders <URL|label>; the label is not part of the destination.
URL=re.compile(r'https?://[^\s<>"\)|]+')
GITHUB_PR=re.compile(r'(https://github\.com/[\w.-]+/[\w.-]+/pull/\d+)(?:/(?:files|commits|changes)\b.*)?$')
PLANE_ITEM=re.compile(r'(https://app\.plane\.so/[\w-]+/projects/[0-9a-f-]{36})/(?:issues|work-items)/([0-9a-f-]{36})$')
# Plane/Slack records keep their literal text; a PullRequest is scanned by title and body.
SOURCES=("(n.source_system IN ['plane','slack'] OR (n:PullRequest AND n.source_system='github'))")


def normalize_url(url):
    """Canonical destination for matching; only rewrites shapes that address the same record."""
    url=url.rstrip('.,;:!?\'').rstrip('/')
    if url.startswith('https://github.com/'):
        # Review-comment, files and commits links all address the pull request itself.
        base=url.split('#',1)[0].split('?',1)[0].rstrip('/')
        match=GITHUB_PR.match(base)
        if match: return match.group(1)
    match=PLANE_ITEM.match(url)
    if match: return f'{match.group(1)}/work-items/{match.group(2)}'
    return url


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
    records=store.db.graph.ro_query(f'MATCH (n) WHERE n.organization_id=$org AND n.valid_to IS NULL AND {SOURCES} '
        "RETURN n.id,labels(n)[0],CASE WHEN n:PullRequest THEN coalesce(n.title,'')+' '+coalesce(n.body_text,'') "
        'ELSE n.text_content END,n.source_url',params={'org':store.org}).result_set
    by_url={}
    for row in sorted(records,key=lambda row:row[1] not in ('DocPage','Issue')):
        if row[3]: by_url.setdefault(normalize_url(row[3]),(row[0],row[1]))
    for ident,label,text,url in records:
        targets={normalize_url(found) for found in URL.findall(text or '')}
        for destination in targets:
            if destination in by_url and by_url[destination][0]!=ident:
                tid,tl=by_url[destination]
                # Marked on PR sources only, so existing Plane/Slack intervals keep their signature.
                basis={'link_basis':'literal_url'} if label=='PullRequest' else {}
                store.edge(label,ident,tl,tid,'REFERENCES','manifest',url,**basis)
                desired.setdefault(ident,set()).add(f'{ident}->REFERENCES->{tid}')
    links=store.db.graph.ro_query('MATCH (n:SourceLink) WHERE n.organization_id=$org AND n.valid_to IS NULL '
        'RETURN n.linked_from,n.source_url',params={'org':store.org}).result_set
    for source,url in links:
        if url and normalize_url(url) in by_url:
            target,label=by_url[normalize_url(url)]
            store.edge('Issue',source,label,target,'REFERENCES','manifest',url)
            desired.setdefault(source,set()).add(f'{source}->REFERENCES->{target}')
    # Source edits can remove a literal link. Retain explicit manifest bindings,
    # but retire reference intervals that no current record or binding supports.
    # A PullRequest also carries parser and fixture references; only literal URLs are ours.
    kept=store.db.graph.ro_query('MATCH (n:PullRequest)-[r:REFERENCES]->() WHERE n.organization_id=$org '
        "AND r.valid_to IS NULL AND coalesce(r.link_basis,'')<>'literal_url' "
        'RETURN n.id,coalesce(r.logical_id,r.id)',params={'org':store.org}).result_set
    retained={ident:set(ids) for ident,ids in desired.items()}
    for ident,edge in kept: retained.setdefault(ident,set()).add(edge)
    existing=store.db.graph.ro_query('MATCH (n)-[r:REFERENCES]->() WHERE n.organization_id=$org '
        f'AND {SOURCES} AND n.valid_to IS NULL AND r.valid_to IS NULL '
        'RETURN DISTINCT n.id',params={'org':store.org}).result_set
    for row in existing:
        store.reconcile_edges(row[0],['REFERENCES'],sorted(retained.get(row[0],set())))
    return desired
