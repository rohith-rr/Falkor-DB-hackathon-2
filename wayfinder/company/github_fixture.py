"""Synthetic GitHub shapes through the real parsers; never calls GitHub."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from .core import CompanyStore
from ..github.activity import ActivityProcessor
from ..parsers.owners_parser import OwnersParser
from ..derived.expertise_v2 import ExpertiseRefresh
from ..models.common import now_iso


def ingest_github(db,manifest,clock=None):
    data=json.loads((Path(__file__).resolve().parents[2]/'fixtures/company/github.json').read_text(encoding='utf-8'))
    if not data.get('synthetic'): raise ValueError('Only synthetic GitHub fixture records are supported')
    store=CompanyStore(db,manifest); org=store.org; namespace=f'github:{org}'; repo=data['repository']
    dataset=f'company:{org}:github-fixture:v1'
    rows=db.graph.ro_query('MATCH (d:DemoDataset {id:$id}) RETURN d.anchor_at',params={'id':dataset}).result_set
    anchor=datetime.fromisoformat(rows[0][0]) if rows else clock or datetime.now(timezone.utc)
    db.upsert_node('DemoDataset',{'id':dataset,'anchor_at':anchor.isoformat(),'synthetic':True,
        'organization_id':org,'clock_policy':'events=fixed offsets before persisted anchor; decay=snapshot calculated_at; staleness=actual UTC now'})
    common={'synthetic':True,'organization_id':org,'data_mode':'fixture','fixture_dataset':dataset}
    def person(login): return store.person('github',login.lower())
    labels={'people':'Person','issues':'Issue','prs':'PullRequest','reviews':'Review','comments':'Comment',
        'threads':'Thread','files':'File','directories':'Directory'}
    def save(parsed,owners=False):
        if owners:
            batches={'OwnersFile':[parsed['owners_node']],'Directory':parsed['directories'],'Person':parsed['people']}
        else: batches={labels[k]:v for k,v in parsed['nodes'].items()}
        for label,nodes in batches.items():
            for node in nodes:
                props=node.to_cypher_props(); props.update(common)
                if label=='Person' and props['id'].startswith('company:'):
                    # Source accounts enrich canonical identities without overwriting display names.
                    props.pop('display_name',None)
                db.upsert_node(label,props)
        for edge in parsed['edges']:
            db.upsert_edge(edge['source_label'],edge['source_id'],edge['target_label'],edge['target_id'],edge['rel_type'],
                {**edge['props'],**common})
    for source in data['owners']:
        save(OwnersParser.parse_owners_file(source['content'],repo,source['path'],namespace=namespace,
            person_id_resolver=person),owners=True)
    rid=f'{namespace}:repo:{repo}'
    db.upsert_node('Repository',{'id':rid,'owner':repo.split('/')[0],'name':repo.split('/')[1],
        'default_branch':'main','source_system':'github','source_url':f'https://github.com/{repo}',**common})
    for item in data['pull_requests']:
        n=item['number']; at=(anchor-timedelta(days=item['age_days'])).isoformat(); review_at=(anchor-timedelta(days=item['age_days'],hours=1)).isoformat()
        url=f'https://github.com/{repo}/pull/{n}'; component=f"company:{org}:component:{item['component']}"
        ticket=f"plane:{org}:issue:{item['ticket']}"
        actor=lambda name:{'login':name,'__typename':'Bot' if name=='northstar-build-bot' else 'User'}
        comments=[{'id':f'IC{n}cmd','body':'/test all\n/cc @dee','author':actor('cy'),'createdAt':review_at,'url':url+f'#issuecomment-{n}01'},
            {'id':f'IC{n}approval','body':'/approve\nfor API bits only','author':actor('eli'),'createdAt':review_at,'url':url+f'#issuecomment-{n}02'}]
        entries=[(item['peer'],item['review']),(item['author'],'The implementation now retains the reservation token.'),
            ('northstar-build-bot','The lock must remain held until the update finishes.')]
        threads=[]
        for i,(login,body) in enumerate(entries):
            threads.append({'id':f'T{n}-{i}','path':item['files'][0],'comments':{'nodes':[{
                'id':f'RC{n}-{i}','body':body,'author':actor(login),'createdAt':review_at,
                'path':item['files'][0],'url':url+f'#discussion_r{n}{i}','pullRequestReview':{'id':f'R{n}'}}]}})
        raw={'__typename':'PullRequest','number':n,'title':item['title'],'state':item['state'],'url':url,
            'body':f"Synthetic change for {item['component']}. Tracks {ticket}.",
            'createdAt':(anchor-timedelta(days=item['age_days']+1)).isoformat(),'updatedAt':at,
            'mergedAt':at if item['state']=='MERGED' else None,'author':actor(item['author']),
            'files':{'nodes':[{'path':p,'additions':12,'deletions':3} for p in item['files']]},
            'comments':{'nodes':comments},'reviews':{'nodes':[{'id':f'R{n}','state':'COMMENTED',
                'body':'Please inspect the file-specific comments.','author':actor(item['peer']),
                'submittedAt':review_at,'url':url+f'#pullrequestreview-{n}'}]},'reviewThreads':{'nodes':threads}}
        save(ActivityProcessor.process_github_node(raw,repo,namespace=namespace,person_id_resolver=person))
        pid=f'{namespace}:pr:{repo}:{n}'
        store.edge('PullRequest',pid,'Issue',ticket,'REFERENCES','manifest',url)
        store.edge('PullRequest',pid,'Component',component,'CONCERNS_COMPONENT','manifest',url)
    for component,path in data['components'].items():
        cid=f'company:{org}:component:{component}'; did=f'{namespace}:dir:{repo}:{path}'
        store.edge('Component',cid,'Directory',did,'IMPLEMENTED_IN','manifest',manifest['source_url'])
        store.edge('Repository',rid,'Directory',did,'CONTAINS','manifest',manifest['source_url'])
        topic=f'company:{org}:topic:{component}'
        store.node('Topic',topic,source='manifest',url=manifest['source_url'],name=component.replace('-',' ').title(),seed=True)
        store.edge('Component',cid,'Topic',topic,'ABOUT_TOPIC','manifest',manifest['source_url'])
    store.coverage('github',f'fixture:{repo}','partial',count=8,mode='fixture',
        gaps=['Eight synthetic PRs, not live or complete repository activity.'])
    return {'repository':repo,'pull_requests':8,'anchor_at':anchor.isoformat(),
        'expertise':ExpertiseRefresh(db,clock=clock).refresh()}
