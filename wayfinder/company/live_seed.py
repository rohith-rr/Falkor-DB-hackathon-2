"""Populate explicitly scoped fictional workspaces; no graph writes or model calls.

Default is a reviewable plan. --apply creates only the planned seed records.
Existing seed records are reused without overwriting source edits. Uncertain
requests are reconciled by external IDs/message markers, never blindly retried.
"""
import argparse
import html
import json
import uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
import httpx
from dotenv import dotenv_values
from .fixture import ROOT
from .plane import PlaneClient
from .slack import SlackClient
from .core import digest

DATASET='wayfinder-live-v1'
ORG='northstar-live'
NOTICE='Fictional Wayfinder demo scenario. API authorship and timestamps are real; scenario statements and results are simulated.'


def load_scope(path):
    scope=json.loads(Path(path).read_text(encoding='utf-8'))
    if scope.get('synthetic') is not True: raise ValueError('Explicit fictional sandbox required')
    if not scope.get('workspace') or not scope.get('project_id') or not scope.get('slack_team_id'):
        raise ValueError('Workspace, project, and Slack team IDs required')
    channels=scope.get('channels',{})
    if set(channels)!={'runtime','allocation'} or any(not v.startswith('C') for v in channels.values()):
        raise ValueError('Two explicitly designated public demo channels required')
    return scope


def scope_hash(scope):
    """Identity of the Plane/Slack sandbox; the optional GitHub section does not change it."""
    return digest({k:v for k,v in scope.items() if k!='github'})


def legacy_records(owner):
    """Reuse source text, not fixture claims, dates, identities, or annotations."""
    source=json.loads((ROOT/'fixtures/company/sources.json').read_text(encoding='utf-8'))
    pages=[];items=[]
    for project in source['projects']:
        component='node-runtime' if project['id']=='runtime' else 'resource-allocation'
        for page in project['pages']:
            text=page['description_html']
            if page['id']=='ownership':
                text=f'<h1>Component ownership</h1><p>For this demo, {html.escape(owner)} owns Node Runtime and Resource Allocation. The same actual sandbox member is assigned the work; six fictional employee accounts have not been created.</p>'
            elif page['id']=='adr-allocation':
                text='<h1>ADR: reservation fairness</h1><p>Decision: preserve existing reservations and retry resize automatically at most twice. Unlimited retries are prohibited because they can starve existing reservations.</p>'
            elif page['id']=='adr-runtime':
                text='<h1>ADR: bounded cancellation</h1><p>Decision: use bounded cancellation with a five second shutdown deadline. Cancel outstanding work when the deadline expires; do not wait indefinitely.</p>'
            pages.append({'key':page['id'],'component':component,'name':'[WF Demo] '+page['name'],
                'description_html':text+f'<p>{NOTICE}</p>'})
        for item in project['work_items']:
            comments=[{'key':c['id'],'comment_html':c['comment_html']+f'<p>{NOTICE}</p>'} for c in item['comments']]
            if item['id']=='w1':
                comments[1]['comment_html']='<p>I confirm the shutdown deadline concern is resolved after the bounded cancellation test. In this simulated test, pending requests were cancelled within five seconds. This is scenario evidence, not a measurement of Wayfinder.</p>'+f'<p>{NOTICE}</p>'
            if item['id']=='w3': comments[0]['comment_html']='<p>The runtime rollback procedure must retain the cancellation deadline and operator checkpoint.</p>'+f'<p>{NOTICE}</p>'
            items.append({'key':item['id'],'component':component,'name':'[WF Demo] '+item['name'],
                'description_html':f'<h2>{"Node Runtime" if component=="node-runtime" else "Resource Allocation"}</h2>'+item['description_html']+f'<p>{NOTICE}</p>',
                'comments':comments})
    return pages,items


def legacy_messages(owner):
    return [
        ('runtime',['Which decision sets the shutdown deadline?',
            'The bounded cancellation ADR records a five second shutdown deadline.',
            'The rollback runbook uses canary shutdown latency as its trigger.',
            f'The declared sandbox component owner is {owner}; this bot is not the owner.',
            'A test request is not a completed test result.']),
        ('runtime',['Does API approval also approve operational readiness?',
            'No. Approval covering API naming does not establish reliability approval.',
            'The shutdown concern and its explicit simulated resolution are in the same Plane work-item thread.',
            'That resolution is scenario data posted under the actual Plane account.',
            'Retain the exact quote and source revision in the review brief.']),
        ('allocation',['Why preserve existing reservations during resize?',
            'The allocation ADR permits automatic resize retry at most twice while preserving existing reservations.',
            'The reservation starvation concern remains open in this scenario.',
            'A proposed retry patch only claims to address the concern; no qualifying resolution is recorded.',
            'Do not infer that rollout completion closes the starvation concern.']),
        ('allocation',['Was immediate retry rejected?',
            'The RFC considers immediate retry; it rejects unlimited retry because reservations can starve.',
            'These are different alternatives and need different labels.',
            'The agent must inspect the exact document rather than inventing a rejection.',
            'If the ADR changes, the previous interpretation must retire before replacement extraction.']),
        ('allocation',['Who should review allocation rollback?',
            f'The manifest declares {owner} as the sandbox component owner.',
            'Task assignment is not code-review authorization.',
            'Slack message volume is not a directory expertise score.',
            'This live-connector dataset has no imported GitHub PRs; code expertise is unknown here.']),
        ('runtime',['How should an agent verify this demo?',
            'These records were created in real Plane and Slack workspaces through their APIs.',
            'The company scenario remains fictional and all Slack seed messages retain bot authorship.',
            'Check source coverage, current revisions, and exact evidence before drafting a brief.',
            'Creating a later follow-up task still requires the normal exact-payload human approval.'])]


def content():
    return json.loads((ROOT/'fixtures/company/wayfinder-content.json').read_text(encoding='utf-8'))


def records(owner):
    value=content()
    for page in value['pages']:
        page['description_html']=page['description_html'].replace('{owner}',html.escape(owner))
    return value['pages'],value['items']


def messages(owner):
    return [(t['channel'],[m.replace('{owner}',owner) for m in t['messages']]) for t in content()['threads']]


def metadata(key):
    return {'event_type':'wayfinder_demo_seed','event_payload':{'dataset':DATASET,'key':key,'content_revision':content()['revision']}}


def disclosure():
    return 'Wayfinder project demo: these staged conversations are posted by the Falkor DB app. Task status distinguishes recorded implementation checks from pending validation; this is not an independent team discussion or proof of production readiness.'


def message_text(owner,index,position,scope,state):
    thread=content()['threads'][index]
    text=thread['messages'][position].replace('{owner}',owner)
    pages,items=records(owner);named={('page:'+r['key']):r['name'] for r in pages}
    named.update({('item:'+r['key']):r['name'] for r in items})
    root=f"https://app.plane.so/{scope['workspace']}/projects/{scope['project_id']}"
    for key in thread.get('links',{}).get(str(position),[]):
        kind=key.split(':',1)[0];ident=state['created'][key]['id']
        url=root+('/pages/' if kind=='page' else '/work-items/')+ident
        text+='\n<'+url+'|'+named[key]+'>'
    return text


def item_description(row,scope,state):
    text=row['description_html']
    names={r['key']:r['name'] for r in content()['items']}
    links=[]
    for key in row.get('depends_on',[]):
        record=state['created'].get('item:'+key)
        if record:
            url=f"https://app.plane.so/{scope['workspace']}/projects/{scope['project_id']}/work-items/{record['id']}"
            links.append('<li><a href="'+url+'">'+html.escape(names[key])+'</a></li>')
        else: links.append('<li>'+html.escape(names[key])+'</li>')
    if links: text+='<p><strong>Depends on:</strong></p><ul>'+''.join(links)+'</ul>'
    return text


class Seeder:
    def __init__(self,scope,folder,plane=None,slack=None):
        self.scope=scope;self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'seed-state.json'
        self.state=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
            'dataset':DATASET,'scope_hash':scope_hash(scope),'created':{},'pending':{},'started_at':datetime.now(timezone.utc).isoformat()}
        if self.state['scope_hash']!=scope_hash(scope): raise ValueError('Checkpoint belongs to another scope')
        self.plane=plane or PlaneClient(scope['workspace'])
        self.slack=slack or SlackClient()
        env=dotenv_values(ROOT/'.env')
        self.bot_token=env.get('SLACK_BOT_TOKEN')
        self.http=httpx.Client(timeout=30)
        self.base=f"projects/{scope['project_id']}/"

    def save(self):
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(self.state,indent=2),encoding='utf-8');temp.replace(self.path)

    def close(self): self.plane.close();self.slack.close();self.http.close()

    def preflight(self):
        project=self.plane.request('GET',self.base)
        # users/me is outside the workspace root; use exactly the documented API endpoint.
        response=self.plane.session.get('https://api.plane.so/api/v2/users/me/')
        response.raise_for_status();me=response.json()
        members=self.plane.listing(self.base+'members/')
        if me['id'] not in [m.get('member_id') for m in members]: raise ValueError('Token owner is not a project member')
        auth=self.slack.call('auth.test')
        if auth['team_id']!=self.scope['slack_team_id']: raise ValueError('Slack token is for another workspace')
        for cid in self.scope['channels'].values():
            info=self.slack.call('conversations.info',channel=cid)['channel']
            if info.get('is_private') or not info.get('is_member'): raise ValueError('App must be a member of each public demo channel')
        if not self.bot_token: raise ValueError('Dedicated bot OAuth token required for seed posting')
        states=self.plane.listing(self.base+'states/')
        self.me=me;self.owner=me.get('display_name') or 'Sandbox owner';self.auth=auth
        self.states={s['group']:s['id'] for s in states}
        pages,items=records(self.owner)
        plan={'dataset':DATASET,'project':project['name'],'scope':self.scope,'pages':pages,'items':items,
            'threads':[{'channel':c,'messages':m} for c,m in messages(self.owner)],
            'channel_disclosure':disclosure(),
            'authorship':'Plane token owner and Slack bot; no fictional account impersonation',
            'model_calls':0,'overwrite_existing':False}
        (self.folder/'seed-plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
        return plan

    def ensure(self,key,find,create):
        if key in self.state['created']: return self.state['created'][key]
        found=find()
        if found:
            self.state['created'][key]=found;self.state['pending'].pop(key,None);self.save();return found
        if key in self.state['pending']: raise RuntimeError(f'Uncertain outcome for {key}; reconcile before any repeat write')
        self.state['pending'][key]=True;self.save()
        value=create()
        if not value.get('id'): raise RuntimeError('Creation returned no identity; reconcile pending checkpoint')
        self.state['created'][key]=value;self.state['pending'].pop(key,None);self.save()
        print('Created '+key,flush=True)
        return value

    def plane_record(self,kind,key,path,payload):
        external=DATASET+':'+key
        def find():
            found=[r for r in self.plane.listing(path) if r.get('external_id')==external and r.get('external_source')==DATASET]
            if len(found)>1: raise RuntimeError('Duplicate seed external IDs require manual review')
            return found[0] if found else None
        return self.ensure(kind+':'+key,find,lambda:self.plane.request('POST',path,
            params={'fields':'all'},json={**payload,'external_id':external,'external_source':DATASET}))

    def slack_record(self,key,channel,text,thread=None):
        marker=f'Wayfinder seed {DATASET}:{key}'
        def find():
            if thread: rows,_=self.slack.messages('conversations.replies',channel=channel,ts=thread,include_all_metadata=True)
            else: rows,_=self.slack.messages('conversations.history',channel=channel,include_all_metadata=True)
            matches=[r for r in rows if marker in r.get('text','') or
                (r.get('metadata',{}).get('event_payload',{}).get('dataset')==DATASET and
                 r.get('metadata',{}).get('event_payload',{}).get('key')==key)]
            if len(matches)>1: raise RuntimeError('Duplicate seed messages require manual review')
            return {'id':matches[0]['ts'],'channel':channel} if matches else None
        def create():
            payload={'channel':channel,'text':text,'unfurl_links':False,'unfurl_media':False,
                'metadata':metadata(key),'client_msg_id':str(uuid.uuid5(uuid.NAMESPACE_URL,scope_hash(self.scope)+key))}
            if thread: payload['thread_ts']=thread
            response=self.http.post('https://slack.com/api/chat.postMessage',headers={'Authorization':'Bearer '+self.bot_token},json=payload)
            response.raise_for_status();value=response.json()
            if not value.get('ok'): raise RuntimeError('Slack post rejected: '+value.get('error','unknown'))
            return {'id':value['ts'],'channel':channel}
        return self.ensure('slack:'+key,find,create)

    def apply(self,plan):
        today=datetime.now(timezone.utc).date()
        for page in plan['pages']:
            self.plane_record('page',page['key'],self.base+'pages/',{k:page[k] for k in ('name','description_html')})
        for i,item in enumerate(plan['items']):
            obj=self.plane_record('item',item['key'],self.base+'work-items/',{
                **{k:item[k] for k in ('name','description_html')},'assignee_ids':[self.me['id']],
                'state_id':self.states[item.get('status','unstarted')],
                'priority':item.get('priority','medium'),'start_date':str(today),
                'target_date':content().get('target_date',str(today+timedelta(days=7)))})
            for comment in item['comments']:
                self.plane_record('comment',comment['key'],self.base+f"work-items/{obj['id']}/comments/",{
                    'comment_html':comment['comment_html'],'access':'INTERNAL'})
        for i,(channel,thread) in enumerate(messages(self.owner)):
            cid=self.scope['channels'][channel]
            root=self.slack_record(f't{i}-0',cid,message_text(self.owner,i,0,self.scope,self.state))
            for j in range(1,len(thread)):
                self.slack_record(f't{i}-{j}',cid,message_text(self.owner,i,j,self.scope,self.state),root['id'])
        for channel,cid in self.scope['channels'].items():
            self.slack_record('notice-'+channel,cid,disclosure())
        manifest=self.manifest(plan)
        (self.folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        self.state['completed_at']=datetime.now(timezone.utc).isoformat();self.save()
        return {'created_or_reused':len(self.state['created']),'pages':len(plan['pages']),'work_items':len(plan['items']),
            'comments':sum(len(i['comments']) for i in plan['items']),'slack_messages':32,
            'manifest':str(self.folder/'manifest.json'),'graph':'wayfinder_company_live_demo','model_calls':0}

    def manifest(self,plan):
        root=f"https://app.plane.so/{self.scope['workspace']}/projects/{self.scope['project_id']}"
        bindings=[]
        for kind,rows,relationship in [('page',plan['pages'],'DESCRIBES'),('item',plan['items'],'CONCERNS_COMPONENT')]:
            for row in rows:
                ident=self.state['created'][kind+':'+row['key']]['id']
                bindings.append({'source':f"plane:{ORG}:{'page' if kind=='page' else 'issue'}:{ident}",
                    'target':f"company:{ORG}:component:{row['component']}",'relationship':relationship})
                for component in row.get('also_components',[]):
                    bindings.append({'source':f"plane:{ORG}:page:{ident}",'target':f'company:{ORG}:component:{component}','relationship':relationship})
                for dependency in row.get('depends_on',[]):
                    target=self.state['created']['item:'+dependency]['id']
                    bindings.append({'source':f'plane:{ORG}:issue:{ident}','target':f'plane:{ORG}:issue:{target}','relationship':'DEPENDS_ON'})
        for i,(channel,thread) in enumerate(messages(self.owner)):
            cid=self.scope['channels'][channel];ts=self.state['created'][f'slack:t{i}-0']['id']
            target=f"company:{ORG}:component:{content()['channel_components'][channel]}"
            bindings.append({'source':f'slack:{ORG}:thread:{cid}:{ts}','target':target,'relationship':'CONCERNS_COMPONENT'})
            for j in range(len(thread)):
                ts=self.state['created'][f'slack:t{i}-{j}']['id']
                bindings.append({'source':f'slack:{ORG}:comment:{cid}:{ts}','target':target,'relationship':'CONCERNS_COMPONENT'})
        return {'organization_id':ORG,'name':content()['organization_name'],'synthetic':True,'source_url':root,
            'content_revision':content()['revision'],
            'plane':{'workspace':self.scope['workspace'],'project_ids':[self.scope['project_id']],
                     'sandbox_project_id':self.scope['project_id']},'slack':{'channel_ids':list(self.scope['channels'].values())},
            'people':[{'id':'workspace-owner','name':self.owner}],
            # Optional GitHub scope: repository, since, component directories, and login -> identity.
            **({'github':{k:v for k,v in self.scope['github'].items() if k!='identities'}} if self.scope.get('github') else {}),
            'identities':{'plane:'+self.me['id']:'workspace-owner',
                **{'github:'+login.lower():who for login,who in self.scope.get('github',{}).get('identities',{}).items()}},'teams':[],
            'components':[{**c,'owners':['workspace-owner']} for c in content()['components']],'bindings':bindings}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--scope',required=True)
    parser.add_argument('--folder',default=str(ROOT/'data/live-workspace'));parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();seeder=Seeder(load_scope(args.scope),args.folder)
    try:
        plan=seeder.preflight()
        if args.apply: print(json.dumps(seeder.apply(plan),indent=2))
        else: print(json.dumps({'plan':str(seeder.folder/'seed-plan.json'),'pages':len(plan['pages']),
            'items':len(plan['items']),'slack_messages':32,'writes':0},indent=2))
    finally: seeder.close()


if __name__=='__main__': main()
