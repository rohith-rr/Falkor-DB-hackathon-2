"""Resolve explicit links only; names and proximity are not identity or causality."""
import html
import re
from ..models.common import now_iso

# Slack renders <URL|label>; the label is not part of the destination.
URL=re.compile(r'https?://[^\s<>"\)|]+',re.I)
# GitHub numbers issues and PRs in one sequence per repository and redirects between
# /issues/n and /pull/n, so both shapes share one key. Tabs, fragments and queries
# (/files, /checks, #discussion_r…) all address the same record.
GITHUB=re.compile(r'https://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/(?:pull|issues)/(\d+)(?:[/?#].*)?$',re.I)
PLANE_ITEM=re.compile(r'(https://app\.plane\.so/[\w-]+/projects/[0-9a-f-]{36})/(?:issues|work-items)/([0-9a-f-]{36})$',re.I)
# Scanned for URLs: Plane/Slack text, and the GitHub conversation (PR, issue, review, comment).
SOURCES=("(n.source_system IN ['plane','slack'] OR (n.source_system='github' "
    'AND (n:PullRequest OR n:Issue OR n:Review OR n:Comment)))')
# Resolvable destinations; a review or comment URL resolves to its PR or issue.
TARGET_LABELS={'github':{'PullRequest','Issue'}}
# Lower ranks win when records share a URL: a Plane issue over its thread, and a Slack
# message over the thread whose URL is that root message's permalink.
RANK={'DocPage':0,'Issue':0,'PullRequest':0,'Thread':2}


def normalize_url(url):
    """Canonical destination for matching; only rewrites shapes that address the same record."""
    url=re.sub(r'^http://','https://',url.rstrip('.,;:!?\'*_~').rstrip('/'),flags=re.I)
    match=GITHUB.match(url)
    if match: return f'https://github.com/{match.group(1).lower()}/{match.group(2).lower()}/issues/{match.group(3)}'
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
        "RETURN n.id,labels(n)[0],CASE WHEN n.source_system='github' THEN coalesce(n.title,'')+' '+coalesce(n.body_text,'') "
        'ELSE n.text_content END,n.source_url,n.source_system',params={'org':store.org}).result_set
    by_url={}
    for ident,label,_,url,system in sorted(records,key=lambda row:(RANK.get(row[1],1),row[0])):
        if url and label in TARGET_LABELS.get(system,{label}):
            by_url.setdefault(normalize_url(url),(ident,label,system))
    for ident,label,text,url,system in records:
        targets={normalize_url(found) for found in URL.findall(html.unescape(text or ''))}
        for destination in targets:
            if destination not in by_url: continue
            tid,tl,target_system=by_url[destination]
            # GitHub-to-GitHub references are the activity parser's; only cross-source links are resolved here.
            if tid==ident or system==target_system=='github': continue
            store.edge(label,ident,tl,tid,'REFERENCES',system,url,link_method='EXACT_REFERENCE')
            desired.setdefault(ident,set()).add(f'{ident}->REFERENCES->{tid}')
    links=store.db.graph.ro_query('MATCH (n:SourceLink) WHERE n.organization_id=$org AND n.valid_to IS NULL '
        'RETURN n.linked_from,n.source_url',params={'org':store.org}).result_set
    for source,url in links:
        if url and normalize_url(url) in by_url:
            target,label,_=by_url[normalize_url(url)]
            store.edge('Issue',source,label,target,'REFERENCES','plane',url)
            desired.setdefault(source,set()).add(f'{source}->REFERENCES->{target}')
    # Source edits can remove a literal link. Retain explicit manifest bindings,
    # but retire reference intervals that no current record or binding supports.
    existing=store.db.graph.ro_query('MATCH (n)-[r:REFERENCES]->() WHERE n.organization_id=$org '
        "AND n.source_system IN ['plane','slack'] AND n.valid_to IS NULL AND r.valid_to IS NULL "
        'RETURN DISTINCT n.id',params={'org':store.org}).result_set
    for row in existing:
        store.reconcile_edges(row[0],['REFERENCES'],sorted(desired.get(row[0],set())))
    # GitHub records also carry parser references to other GitHub records; only their
    # cross-source references (not fixture bindings) are reconciled against the text.
    crossing=store.db.graph.ro_query('MATCH (n)-[r:REFERENCES]->(t) WHERE n.organization_id=$org '
        "AND n.source_system='github' AND r.valid_to IS NULL AND t.source_system IN ['plane','slack'] "
        "AND coalesce(r.link_method,'')<>'STRUCTURED_FILE' RETURN n.id,coalesce(r.logical_id,r.id)",
        params={'org':store.org}).result_set
    stale=sorted({edge for ident,edge in crossing if edge not in desired.get(ident,set())})
    if stale:
        store.db.query('MATCH ()-[r:REFERENCES]->() WHERE coalesce(r.logical_id,r.id) IN $ids AND r.valid_to IS NULL '
            'SET r.valid_to=$now',{'ids':stale,'now':now_iso()})
    return desired
