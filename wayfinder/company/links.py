"""Resolve explicit links only; names and proximity are not identity or causality."""
import html
import re
from ..models.common import now_iso

# Slack renders <URL|label>; the label is not part of the destination.
URL=re.compile(r'https?://[^\s<>"\)|]+',re.I)
# GitHub numbers issues and PRs in one sequence per repository and redirects between
# /issues/n and /pull/n, so both shapes share one key. Tabs, fragments and queries
# (/files, /checks, #discussion_r…) all address the same record.
# Commit URLs, including a commit viewed inside a PR (/pull/n/commits/<sha>); full or abbreviated SHA.
COMMIT=re.compile(r'https://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/(?:commits?|pull/\d+/commits)/([0-9a-f]{7,40})(?:[/?#].*)?$',re.I)
# /tree/<branch>[/<path>]; branch names may contain '/', so they resolve against known branches.
TREE=re.compile(r'https://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/tree/(.+)$',re.I)
GITHUB=re.compile(r'https://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/(?:pull|issues)/(\d+)(?:[/?#].*)?$',re.I)
PLANE_ITEM=re.compile(r'(https://app\.plane\.so/[\w-]+/projects/[0-9a-f-]{36})/(?:issues|work-items)/([0-9a-f-]{36})$',re.I)
# Scanned for URLs: Plane/Slack text, and the GitHub conversation (PR, issue, review, comment).
SOURCES=("(n.source_system IN ['plane','slack'] OR (n.source_system='github' "
    'AND (n:PullRequest OR n:Issue OR n:Review OR n:Comment OR n:Commit OR n:Branch)))')
# Resolvable destinations; a review or comment URL resolves to its PR or issue.
TARGET_LABELS={'github':{'PullRequest','Issue','Commit','Branch'}}
# Lower ranks win when records share a URL: a Plane issue over its thread, and a Slack
# message over the thread whose URL is that root message's permalink.
RANK={'DocPage':0,'Issue':0,'PullRequest':0,'Commit':0,'Branch':0,'Thread':2}
# Plane work-item keys such as FALKORDBTE-12 in branch names, commit messages and chat.
KEY=r'(?<![A-Za-z0-9])({})-(\d+)(?![A-Za-z0-9])'


def normalize_url(url):
    """Canonical destination for matching; only rewrites shapes that address the same record."""
    url=re.sub(r'^http://','https://',url.rstrip('.,;:!?\'*_~').rstrip('/'),flags=re.I)
    match=COMMIT.match(url)
    if match: return f'https://github.com/{match.group(1).lower()}/{match.group(2).lower()}/commit/{match.group(3).lower()}'
    match=GITHUB.match(url)
    if match: return f'https://github.com/{match.group(1).lower()}/{match.group(2).lower()}/issues/{match.group(3)}'
    match=TREE.match(url)
    if match: return f'https://github.com/{match.group(1).lower()}/{match.group(2).lower()}/tree/{match.group(3)}'
    match=PLANE_ITEM.match(url)
    if match: return f'{match.group(1)}/work-items/{match.group(2)}'
    return url


def link_manifest(store):
    desired={}
    # Text that only quotes an identifier as an example (and can't be edited, e.g. a merged
    # commit message) is excluded explicitly; the existing link then retires like a removed URL.
    excluded={(e['source'],e['target']) for e in store.manifest.get('link_exclusions',[])}
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
        "RETURN n.id,labels(n)[0],CASE WHEN n:Branch THEN n.name WHEN n.source_system='github' THEN coalesce(n.title,'')+' '+coalesce(n.body_text,'') "
        'ELSE n.text_content END,n.source_url,n.source_system',params={'org':store.org}).result_set
    by_url={}
    for ident,label,_,url,system in sorted(records,key=lambda row:(RANK.get(row[1],1),row[0])):
        if url and label in TARGET_LABELS.get(system,{label}):
            by_url.setdefault(normalize_url(url),(ident,label,system))
    def resolve(destination):
        if destination in by_url: return by_url[destination]
        # An abbreviated SHA resolves only when exactly one known commit in that repository matches it.
        match=re.match(r'(https://github\.com/[^/]+/[^/]+/commit/)([0-9a-f]{7,39})$',destination)
        if match:
            found=[v for k,v in by_url.items() if k.startswith(match.group(1)+match.group(2))]
            return found[0] if len(found)==1 else None
        # /tree/<branch>/<path>: the longest known branch name that prefixes the remainder.
        match=re.match(r'(https://github\.com/[^/]+/[^/]+/tree/)(.+)$',destination)
        if match:
            names=[k for k in by_url if k.startswith(match.group(1)) and (match.group(2)==k[len(match.group(1)):]
                or match.group(2).startswith(k[len(match.group(1)):]+'/'))]
            return by_url[max(names,key=len)] if names else None
        return None
    keys={row[0].lower():row[1] for row in store.db.graph.ro_query('MATCH (i:Issue) WHERE i.organization_id=$org '
        "AND i.source_system='plane' AND i.valid_to IS NULL AND i.key IS NOT NULL RETURN i.key,i.id",
        params={'org':store.org}).result_set}
    prefixes=sorted({k.rsplit('-',1)[0] for k in keys})
    key_pattern=re.compile(KEY.format('|'.join(re.escape(p) for p in prefixes)),re.I) if prefixes else None
    for ident,label,text,url,system in records:
        text=html.unescape(text or '')
        found=[(resolve(normalize_url(u)),'EXACT_REFERENCE') for u in URL.findall(text)]
        if key_pattern:
            # Branch names are often lower-cased (feat/falkordbte-12-...); keys match case-insensitively.
            found+=[((keys[f'{p}-{n}'.lower()],'Issue','plane'),'EXACT_IDENTIFIER') for p,n in key_pattern.findall(text)
                if f'{p}-{n}'.lower() in keys]
        for target,method in dict.fromkeys(found):
            if not target: continue
            tid,tl,target_system=target
            # GitHub-to-GitHub references are the activity parser's; only cross-source links are resolved here.
            if tid==ident or system==target_system=='github' or (ident,tid) in excluded: continue
            store.edge(label,ident,tl,tid,'REFERENCES',system,url,link_method=method)
            desired.setdefault(ident,set()).add(f'{ident}->REFERENCES->{tid}')
    links=store.db.graph.ro_query('MATCH (n:SourceLink) WHERE n.organization_id=$org AND n.valid_to IS NULL '
        'RETURN n.linked_from,n.source_url',params={'org':store.org}).result_set
    for source,url in links:
        if url and normalize_url(url) in by_url and (source,by_url[normalize_url(url)][0]) not in excluded:
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
