"""Live GitHub connector for the sandbox's own repository. Read-only GraphQL search.

Pull requests, reviews, review threads, comments and issues go through the same
ActivityProcessor as the Kubernetes pilot and the Northstar fixture, so code expertise
is computed by the unchanged v2 rules. Links are explicit only: a PR references a Plane
work item when its title or body contains that item's URL, and concerns a Component when
it changes files under a directory the manifest maps to that Component.
"""
import re
from .core import CompanyStore
from .github_fixture import save_parsed
from ..github.client import GitHubClient
from ..github.activity import ActivityProcessor
from ..derived.expertise_v2 import ExpertiseRefresh
from ..models.common import now_iso

PLANE_ITEM=re.compile(r'https://app\.plane\.so/[\w-]+/projects/[0-9a-f-]{36}/(?:work-items|issues)/([0-9a-f-]{36})')


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
    except Exception as exc:
        store.coverage('github',f'repo:{repo}','unavailable',gaps=[f'{type(exc).__name__}: {exc}'])
        return {'repository':repo,'status':'unavailable','reason':str(exc)}
    finally: client.close()
    url=f'https://github.com/{repo}'; rid=f'{namespace}:repo:{repo}'
    db.upsert_node('Repository',{'id':rid,'owner':repo.split('/')[0],'name':repo.split('/')[1],
        'source_system':'github','source_url':url,**common})
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
    references=0
    for node in prs:
        pid=f"{namespace}:pr:{repo}:{node['number']}"
        for item in set(PLANE_ITEM.findall((node.get('title') or '')+'\n'+(node.get('body') or ''))):
            target=f'plane:{org}:issue:{item}'
            if db.graph.ro_query('MATCH (i:Issue {id:$id}) RETURN i.id',params={'id':target}).result_set:
                store.edge('PullRequest',pid,'Issue',target,'REFERENCES','github',node['url']); references+=1
        files=[f['path'] for f in (node.get('files') or {}).get('nodes',[])]
        for root,cid in roots.items():
            if any(f==root or f.startswith(root+'/') for f in files):
                store.edge('PullRequest',pid,'Component',cid,'CONCERNS_COMPONENT','github',node['url'])
    store.coverage('github',f'repo:{repo}','complete',since=cfg.get('since'),count=len(prs)+issues,
        gaps=['Pull requests and issues only; commits pushed without a pull request are not imported.'])
    refresh=ExpertiseRefresh(db).refresh()
    return {'repository':repo,'observed_since':started,'pull_requests':len(prs),'issues':issues,
            'plane_references':references,'expertise_generation':refresh.get('generation') if isinstance(refresh,dict) else None}
