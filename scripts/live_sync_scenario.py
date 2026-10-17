"""Staged, journaled source changes in the designated Plane project and Slack channel for testing sync.

Stages (run in order, syncing in between to see what each one changes):
  changes  move FALKORDBTE-8 and FALKORDBTE-6 to Done, append a line to FALKORDBTE-6, comment on
           FALKORDBTE-8, create one work item with a Plane link to a PR, and post a Slack thread
           (root + reply) that mentions the new item's key and the PR.
  edits    edit the Slack reply and the new work item's description, and delete the scenario comment.
  cleanup  delete every record this scenario created (the Slack thread and the new work item).
           The two state changes and the appended line on FALKORDBTE-6 stay; they record real progress.

Default is a dry run. --apply performs the writes. Only scenario-created records are edited or
deleted. Native IDs and the previous values are journaled in data/live-workspace/scenario-state.json.
No LLM or embedding requests are made.
"""
import argparse
import json
import sys
from datetime import datetime,timezone
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wayfinder.db import DatabaseManager
from wayfinder.company.live_seed import load_scope
from wayfinder.company.plane import PlaneClient

FOLDER=ROOT/'data/live-workspace'; STATE=FOLDER/'scenario-state.json'
GRAPH='wayfinder_company_live_demo'; MARKER='wayfinder-sync-scenario'


def now(): return datetime.now(timezone.utc).isoformat()


class Scenario:
    def __init__(self,scope,apply):
        self.scope=scope; self.apply=apply; self.base=f"projects/{scope['project_id']}/"
        repo=scope.get('github',{}).get('repository')
        if not repo: raise SystemExit('scope.json needs github.repository for the PR and commit links')
        self.gh=f'https://github.com/{repo}'
        self.plane=PlaneClient(scope['workspace'])
        token=dotenv_values(ROOT/'.env').get('SLACK_BOT_TOKEN')
        if not token: raise SystemExit('SLACK_BOT_TOKEN is required')
        self.slack=httpx.Client(base_url='https://slack.com/api/',timeout=30,headers={'Authorization':'Bearer '+token})
        self.channel=scope['channels']['runtime']
        self.state=json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'created':{},'previous':{},'stages':[]}
        self.log=[]

    def close(self): self.plane.close(); self.slack.close()

    def save(self):
        if self.apply: STATE.write_text(json.dumps(self.state,indent=2),encoding='utf-8')

    def step(self,description,action):
        self.log.append(description)
        if self.apply:
            result=action(); self.save(); print('done:',description,flush=True); return result
        print('plan:',description,flush=True)

    def slack_call(self,method,**payload):
        data=self.slack.post(method,json=payload).json()
        if not data.get('ok'): raise RuntimeError(f"Slack {method}: {data.get('error')}")
        return data

    def plane_delete(self,path):
        response=self.plane.session.delete(self.plane.root+path)
        if response.status_code not in (200,204,404): response.raise_for_status()

    def item(self,key):
        rows=DatabaseManager(graph_name=GRAPH).graph.ro_query("MATCH (i:Issue {key:$key}) WHERE i.source_system='plane' "
            'AND i.valid_to IS NULL RETURN i.source_ref',params={'key':key}).result_set
        if not rows: raise SystemExit(f'{key} is not in {GRAPH}; run a sync first')
        return rows[0][0]

    def changes(self):
        if 'item' in self.state['created']: raise SystemExit('Stage "changes" already applied; run edits or cleanup')
        states={s['group']:s['id'] for s in self.plane.listing(self.base+'states/')}
        pilot,retrieval=self.item('FALKORDBTE-8'),self.item('FALKORDBTE-6')
        def move(native,key):
            current=self.plane.request('GET',self.base+f'work-items/{native}/')
            self.state['previous'][key]={'state_id':current['state_id']}
            self.plane.request('PATCH',self.base+f'work-items/{native}/',json={'state_id':states['completed']})
        self.step('FALKORDBTE-8: Todo -> Done',lambda:move(pilot,'FALKORDBTE-8'))
        def comment():
            value=self.plane.request('POST',self.base+f'work-items/{pilot}/comments/',json={'access':'INTERNAL','comment_html':
                f'<p>Ran with owner approval: 8 generation and 20 embedding requests, 5 proposals accepted, recall low. '
                f'Merged in <a href="{self.gh}/pull/1">PR #1</a>.</p>'})
            self.state['created']['comment']={'item':pilot,'id':value['id']}
        self.step('FALKORDBTE-8: comment linking PR #1',comment)
        def retrieval_done():
            current=self.plane.request('GET',self.base+f'work-items/{retrieval}/',params={'fields':'all'})
            self.state['previous']['FALKORDBTE-6']={'state_id':current['state_id'],'description_html':current.get('description_html','')}
            self.plane.request('PATCH',self.base+f'work-items/{retrieval}/',json={'state_id':states['completed'],
                'description_html':current.get('description_html','')+f'<p>Re-verified after moving embeddings to '
                f'pplx-embed-v1-0.6b at 1024 dimensions in <a href="{self.gh}/commit/ff7e423">ff7e423</a>.</p>'})
        self.step('FALKORDBTE-6: In Progress -> Done, append a line linking commit ff7e423',retrieval_done)
        def create():
            value=self.plane.request('POST',self.base+'work-items/',params={'fields':'all'},json={
                'name':'Link branches, commits and Plane keys across sources','state_id':states['completed'],
                'priority':'medium','assignee_ids':[a for a in [self.owner()] if a],
                'description_html':f'<p>Branches and commits are imported with PR head/base refs, and Plane keys in branch '
                    f'names, commit messages and chat link to work items. Merged in <a href="{self.gh}/pull/4">PR #4</a>.</p>',
                'external_id':f'{MARKER}:item','external_source':MARKER})
            self.state['created']['item']={'id':value['id'],'key':f"FALKORDBTE-{value.get('sequence_id')}"}
            try:
                link=self.plane.request('POST',self.base+f"work-items/{value['id']}/links/",
                    json={'url':f'{self.gh}/pull/4/files','title':'PR #4 changes'})
                self.state['created']['link']=link.get('id')
            except Exception as exc: self.state['created']['link_error']=str(exc)
        self.step('Create work item "Link branches, commits and Plane keys across sources" (Done) with a Plane link to PR #4',create)
        def thread():
            key=self.state['created'].get('item',{}).get('key','FALKORDBTE-?')
            meta={'event_type':MARKER,'event_payload':{'stage':'changes'}}
            root=self.slack_call('chat.postMessage',channel=self.channel,metadata=meta,unfurl_links=False,
                text=f'Branch and commit linking is merged in <{self.gh}/pull/4|PR #4>, tracked as {key}.')
            reply=self.slack_call('chat.postMessage',channel=self.channel,thread_ts=root['ts'],metadata=meta,unfurl_links=False,
                text='FALKORDBTE-9 is still open: the approved Plane action has not run against the live sandbox.')
            self.state['created']['slack']={'root':root['ts'],'reply':reply['ts']}
        self.step('Slack: post a thread in the runtime channel mentioning the new key, PR #4 and FALKORDBTE-9',thread)
        self.state['stages'].append({'stage':'changes','at':now()})

    def owner(self):
        response=self.plane.session.get('https://api.plane.so/api/v2/users/me/')
        return response.json().get('id') if response.is_success else None

    def edits(self):
        created=self.state['created']
        if 'item' not in created: raise SystemExit('Run stage "changes" first')
        if created.get('slack'):
            self.step('Slack: edit the reply (FALKORDBTE-9 -> FALKORDBTE-10)',lambda:self.slack_call('chat.update',channel=self.channel,
                ts=created['slack']['reply'],text='Correction: FALKORDBTE-10 (dashboard errors) is the open item; FALKORDBTE-9 is next.'))
        self.step('New work item: edit its description',lambda:self.plane.request('PATCH',self.base+f"work-items/{created['item']['id']}/",
            json={'description_html':'<p>Branches, commits and Plane keys now link across GitHub, Plane and Slack. '
                f'Merged in <a href="{self.gh}/pull/4">PR #4</a>; follow-up linking fixes in <a href="{self.gh}/pull/3">PR #3</a>.</p>'}))
        if created.get('comment'):
            def drop():
                self.plane_delete(self.base+f"work-items/{created['comment']['item']}/comments/{created['comment']['id']}/")
                created.pop('comment')
            self.step('FALKORDBTE-8: delete the scenario comment',drop)
        self.state['stages'].append({'stage':'edits','at':now()})

    def cleanup(self):
        created=self.state['created']
        if created.get('slack'):
            def drop_slack():
                for ts in (created['slack']['reply'],created['slack']['root']):
                    try: self.slack_call('chat.delete',channel=self.channel,ts=ts)
                    except RuntimeError as exc:
                        if 'message_not_found' not in str(exc): raise
                created.pop('slack')
            self.step('Slack: delete the scenario thread',drop_slack)
        if created.get('comment'):
            def drop_comment():
                self.plane_delete(self.base+f"work-items/{created['comment']['item']}/comments/{created['comment']['id']}/")
                created.pop('comment')
            self.step('FALKORDBTE-8: delete the scenario comment',drop_comment)
        if created.get('item'):
            def drop_item():
                self.plane_delete(self.base+f"work-items/{created['item']['id']}/"); created.pop('item',None); created.pop('link',None)
            self.step('Delete the scenario work item',drop_item)
        self.state['stages'].append({'stage':'cleanup','at':now()})


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('stage',choices=('changes','edits','cleanup'))
    parser.add_argument('--apply',action='store_true',help='Perform the writes (default: dry run)')
    args=parser.parse_args()
    scenario=Scenario(load_scope(FOLDER/'scope.json'),args.apply)
    try: getattr(scenario,args.stage)()
    finally: scenario.close()
    print(json.dumps({'stage':args.stage,'applied':args.apply,'steps':scenario.log,
        'created':{k:v for k,v in scenario.state['created'].items() if k in ('item','link','link_error')}},indent=2))


if __name__=='__main__': main()
