"""Live GitHub connector for the sandbox's own repository. Read-only GraphQL search.

Pull requests, reviews, review threads, comments and issues go through the same
ActivityProcessor as the Kubernetes pilot and the Northstar fixture, so code expertise
is computed by the unchanged v2 rules. Links are explicit only: a PR references a Plane or
Slack record when its title or body contains that record's URL (resolved by links.py, the
same path that links Slack/Plane text to PR URLs), and concerns a Component when it
changes files under a directory the manifest maps to that Component.
"""
from .core import CompanyStore
from .links import link_manifest
from .github_fixture import save_parsed
from ..github.client import GitHubClient
from ..github.activity import ActivityProcessor
from ..derived.expertise_v2 import ExpertiseRefresh
from ..models.common import now_iso


def sync_github(db,manifest,client=None):
    cfg=manifest.get('github') or {}
    repo=cfg.get('repository')
    if not repo: return {'status':'disabled','reason':'No github.repository in manifest'}
    store=CompanyStore(db,manifest); org=store.org; namespace=f'github:{org}'
    # The repository is real code; only the surrounding Plane/Slack scenario is fictional.
    common={'organization_id':org,'data_mode':'live','synthetic':False}
    def person(login): return store.person('github',login.lower())
    client=client or GitHubClient(); prs=[]; issues=0; started=now_iso()
    try:
        for node in client.iter_activity(repo=repo,label=None,since_date=cfg.get('since')):
            save_parsed(db,ActivityProcessor.process_github_node(node,repo,namespace=namespace,person_id_resolver=person),common)
            if node.get('__typename')=='PullRequest': prs.append(node)
            else: issues+=1
        history=client.repository_history(repo,since=cfg.get('since'))
    except Exception as exc:
        store.coverage('github',f'repo:{repo}','unavailable',gaps=[f'{type(exc).__name__}: {exc}'])
        return {'repository':repo,'status':'unavailable','reason':str(exc)}
    finally: client.close()
    url=f'https://github.com/{repo}'; rid=f'{namespace}:repo:{repo}'
    default=(history.get('defaultBranchRef') or {}).get('name')
    db.upsert_node('Repository',{'id':rid,'owner':repo.split('/')[0],'name':repo.split('/')[1],
        'default_branch':default,'source_system':'github','source_url':url,**common})
    git=ingest_git_history(store,repo,history,common,person,since=cfg.get('since'))
    roots={}
    for component,paths in cfg.get('components',{}).items():
        cid=f'company:{org}:component:{component}'
        for path in [paths] if isinstance(paths,str) else paths:
            did=f'{namespace}:dir:{repo}:{path}'
            db.upsert_node('Directory',{'id':did,'path':path,'repo':repo,'source_system':'github',
                'source_url':f'{url}/tree/HEAD/{path}',**common})
            store.edge('Component',cid,'Directory',did,'IMPLEMENTED_IN','manifest',url)
            store.edge('Repository',rid,'Directory',did,'CONTAINS','manifest',url)
            roots[path.rstrip('/')]=cid
    for node in prs:
        pid=f"{namespace}:pr:{repo}:{node['number']}"
        files=[f['path'] for f in (node.get('files') or {}).get('nodes',[])]
        for root,cid in roots.items():
            if any(f==root or f.startswith(root+'/') for f in files):
                store.edge('PullRequest',pid,'Component',cid,'CONCERNS_COMPONENT','github',node['url'])
    store.coverage('github',f'repo:{repo}','complete',since=cfg.get('since'),count=len(prs)+issues,
        gaps=['Pull requests and issues; branches and commits are covered separately.'])
    linked=link_manifest(store)
    references=sum(1 for node in prs for edge in linked.get(f"{namespace}:pr:{repo}:{node['number']}",())
        if edge.split('->REFERENCES->',1)[1].startswith(f'plane:{org}:issue:'))
    refresh=ExpertiseRefresh(db).refresh()
    return {'repository':repo,'observed_since':started,'pull_requests':len(prs),'issues':issues,**git,
            'plane_references':references,'expertise_generation':refresh.get('generation') if isinstance(refresh,dict) else None}


def ingest_git_history(store,repo,history,common,person,since=None):
    """Branches, commits and PR refs. Commits are immutable and keyed by SHA; branches move."""
    db=store.db; namespace=f'github:{store.org}'; url=f'https://github.com/{repo}'; rid=f'{namespace}:repo:{repo}'
    default=(history.get('defaultBranchRef') or {}).get('name')
    def commit_id(sha): return f'{namespace}:commit:{repo}:{sha}'
    def branch_id(name): return f'{namespace}:branch:{repo}:{name}'
    commits={}; truncated=[]
    refs=history.get('refs') or {}
    for ref in refs.get('nodes',[]):
        log=(ref.get('target') or {}).get('history') or {}
        if (log.get('pageInfo') or {}).get('hasNextPage'): truncated.append(ref['name'])
        for commit in log.get('nodes',[]): commits[commit['oid']]=commit
    pulls=(history.get('pullRequests') or {}).get('nodes',[])
    for pr in pulls:
        for row in ((pr.get('commits') or {}).get('nodes') or []): commits[row['commit']['oid']]=row['commit']
    for sha,commit in commits.items():
        cid=commit_id(sha)
        login=((commit.get('author') or {}).get('user') or {}).get('login')
        db.upsert_node('Commit',{'id':cid,'sha':sha,'short_sha':commit.get('abbreviatedOid',sha[:7]),'repo':repo,
            'title':commit.get('messageHeadline'),'body_text':commit.get('message') or '',
            'committed_at':commit.get('committedDate'),'authored_at':commit.get('authoredDate'),
            'additions':commit.get('additions',0),'deletions':commit.get('deletions',0),
            # A git author without a linked GitHub account keeps only the commit's display name.
            'author_login':login,'author_name':None if login else (commit.get('author') or {}).get('name'),
            'parent_shas':[p['oid'] for p in (commit.get('parents') or {}).get('nodes',[])],
            'source_system':'github','source_url':commit.get('url') or f'{url}/commit/{sha}','source_ref':sha,**common})
        if login:
            author=person(login)
            if not author.startswith('company:'):
                db.upsert_node('Person',{'id':author,'login':login.lower(),'source_system':'github',**common})
            store.edge('Person',author,'Commit',cid,'AUTHORED','github',commit.get('url') or url)
    for sha,commit in commits.items():
        for parent in (commit.get('parents') or {}).get('nodes',[]):
            if parent['oid'] in commits:
                store.edge('Commit',commit_id(sha),'Commit',commit_id(parent['oid']),'HAS_PARENT','github',url)
    branches=[]
    for ref in refs.get('nodes',[]):
        name=ref['name']; bid=branch_id(name); head=(ref.get('target') or {}).get('oid'); branches.append(bid)
        db.upsert_node('Branch',{'id':bid,'name':name,'repo':repo,'head_sha':head,'is_default':name==default,
            'source_system':'github','source_url':f'{url}/tree/{name}','source_ref':name,
            'sync_scope':f'repo:{repo}:branches','valid_to':None,'is_deleted':False,**common})
        store.edge('Repository',rid,'Branch',bid,'HAS_BRANCH','github',url)
        if head in commits: store.edge('Branch',bid,'Commit',commit_id(head),'HEAD_AT','github',url)
        # A moved head retires the previous HEAD_AT interval.
        store.reconcile_edges(bid,['HEAD_AT'],[f'{bid}->HEAD_AT->{commit_id(head)}'] if head in commits else [])
        for commit in ((ref.get('target') or {}).get('history') or {}).get('nodes',[]):
            store.edge('Branch',bid,'Commit',commit_id(commit['oid']),'HAS_COMMIT','github',url)
    refs_complete=not (refs.get('pageInfo') or {}).get('hasNextPage')
    if refs_complete: store.reconcile('github',f'repo:{repo}:branches',branches)
    current=set(branches)
    for pr in pulls:
        pid=f"{namespace}:pr:{repo}:{pr['number']}"
        merge=(pr.get('mergeCommit') or {}).get('oid')
        if not db.graph.ro_query('MATCH (p:PullRequest {id:$id}) RETURN p.id',params={'id':pid}).result_set: continue
        db.upsert_node('PullRequest',{'id':pid,'head_ref':pr.get('headRefName'),'base_ref':pr.get('baseRefName'),
            'head_sha':pr.get('headRefOid'),'merge_sha':merge})
        for rel,name in (('FROM_BRANCH',pr.get('headRefName')),('INTO_BRANCH',pr.get('baseRefName'))):
            # A deleted head branch keeps its name on the PR; there is no current Branch to point at.
            if name and branch_id(name) in current:
                store.edge('PullRequest',pid,'Branch',branch_id(name),rel,'github',url)
        for row in ((pr.get('commits') or {}).get('nodes') or []):
            store.edge('PullRequest',pid,'Commit',commit_id(row['commit']['oid']),'HAS_COMMIT','github',url)
        if merge in commits: store.edge('PullRequest',pid,'Commit',commit_id(merge),'MERGED_AS','github',url)
    store.coverage('github',f'repo:{repo}:branches','complete' if refs_complete else 'partial',count=len(branches),
        gaps=[] if refs_complete else ['More than 100 branches; the rest were not read.'])
    gaps=['Commits reachable from current branch heads and pull requests within the window.']
    if truncated: gaps.append('History truncated at 100 commits on: '+', '.join(truncated))
    store.coverage('github',f'repo:{repo}:commits','partial',since=since,count=len(commits),gaps=gaps)
    return {'branches':len(branches),'commits':len(commits),'default_branch':default}
