"""End-to-end freshness probe against the designated live Plane project and Slack demo channel.

It creates, edits and deletes its own clearly labeled probe records, then checks that
the graph follows each change: created -> visible, edited -> new text/state plus a
retained SourceRevision, deleted -> retired (valid_to set).

  --mode inline  the probe runs the connector sync itself after each change
                 (proves the connectors handle real API payloads).
  --mode watch   the probe never syncs; it waits for a separately running
                 `wayfinder.company ... watch` or the scheduled task to pick the change up
                 (proves the graph updates automatically, and measures the lag).

Default is a dry run. --apply performs the writes. Only probe-created records are edited
or deleted. No LLM or embedding requests are made.
"""
import argparse
import json
import sys
import time
import uuid
from datetime import datetime,timezone
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wayfinder.db import DatabaseManager
from wayfinder.company.core import load_manifest
from wayfinder.company.live_seed import load_scope
from wayfinder.company.plane import PlaneClient
from wayfinder.company.freshness import sync_once

GRAPH='wayfinder_company_live_demo'
FOLDER=ROOT/'data/live-workspace'
MARKER='wayfinder-freshness-probe'


def now(): return datetime.now(timezone.utc).isoformat()


class Probe:
    def __init__(self,scope,manifest,db,mode,timeout,interval):
        self.scope,self.manifest,self.db,self.mode,self.timeout,self.interval=scope,manifest,db,mode,timeout,interval
        self.org=manifest['organization_id']; self.run=uuid.uuid4().hex[:8]
        self.plane=PlaneClient(scope['workspace']); self.base=f"projects/{scope['project_id']}/"
        token=dotenv_values(ROOT/'.env').get('SLACK_BOT_TOKEN')
        if not token: raise SystemExit('SLACK_BOT_TOKEN is required to post the probe message')
        self.slack=httpx.Client(base_url='https://slack.com/api/',timeout=30,headers={'Authorization':'Bearer '+token})
        self.channel=scope['channels']['runtime']; self.created={}; self.steps=[]

    def close(self): self.plane.close(); self.slack.close()

    # --- source writes -------------------------------------------------
    def slack_call(self,method,**payload):
        data=self.slack.post(method,json=payload).json()
        if not data.get('ok'): raise RuntimeError(f"Slack {method}: {data.get('error')}")
        return data

    def plane_delete(self,path):
        response=self.plane.session.delete(self.plane.root+path)
        if response.status_code not in (200,204,404): response.raise_for_status()

    def create(self):
        states={s['group']:s for s in self.plane.listing(self.base+'states/')}
        self.states=states
        item=self.plane.request('POST',self.base+'work-items/',json={'name':f'[Freshness probe {self.run}] created',
            'description_html':f'<p>Freshness probe {self.run}: initial text.</p>','state_id':states['unstarted']['id'],
            'external_id':f'{MARKER}:{self.run}','external_source':MARKER})
        self.created['item']=item['id']
        comment=self.plane.request('POST',self.base+f"work-items/{item['id']}/comments/",
            json={'comment_html':f'<p>Probe {self.run} comment, initial.</p>','access':'INTERNAL'})
        self.created['comment']=comment['id']
        meta={'event_type':MARKER,'event_payload':{'run':self.run}}
        root=self.slack_call('chat.postMessage',channel=self.channel,text=f'Freshness probe {self.run}: root message (automated check, will be deleted).',metadata=meta)
        self.created['root']=root['ts']
        reply=self.slack_call('chat.postMessage',channel=self.channel,thread_ts=root['ts'],text=f'Freshness probe {self.run}: reply, initial.',metadata=meta)
        self.created['reply']=reply['ts']

    def edit(self):
        item=self.created['item']
        self.plane.request('PATCH',self.base+f'work-items/{item}/',json={'name':f'[Freshness probe {self.run}] edited',
            'description_html':f'<p>Freshness probe {self.run}: edited text.</p>','state_id':self.states['completed']['id']})
        self.plane.request('PATCH',self.base+f"work-items/{item}/comments/{self.created['comment']}/",
            json={'comment_html':f'<p>Probe {self.run} comment, edited.</p>'})
        self.slack_call('chat.update',channel=self.channel,ts=self.created['reply'],text=f'Freshness probe {self.run}: reply, edited.')

    def delete(self):
        item=self.created.get('item')
        if self.created.get('reply'): self.slack_call('chat.delete',channel=self.channel,ts=self.created['reply'])
        if self.created.get('root'): self.slack_call('chat.delete',channel=self.channel,ts=self.created['root'])
        if item and self.created.get('comment'): self.plane_delete(self.base+f"work-items/{item}/comments/{self.created['comment']}/")
        if item: self.plane_delete(self.base+f'work-items/{item}/')

    def cleanup(self):
        """Best-effort removal of probe records after a failure; never touches other records."""
        for step in (lambda:self.slack_call('chat.delete',channel=self.channel,ts=self.created['reply']),
                     lambda:self.slack_call('chat.delete',channel=self.channel,ts=self.created['root']),
                     lambda:self.plane_delete(self.base+f"work-items/{self.created['item']}/")):
            try: step()
            except Exception: pass

    # --- graph observation ---------------------------------------------
    def ids(self):
        c=self.created; org=self.org
        return {'item':f"plane:{org}:issue:{c['item']}",'comment':f"plane:{org}:comment:{c['comment']}",
                'root':f"slack:{org}:comment:{self.channel}:{c['root']}",'reply':f"slack:{org}:comment:{self.channel}:{c['reply']}",
                'thread':f"slack:{org}:thread:{self.channel}:{c['root']}"}

    def read(self,ident):
        rows=self.db.graph.ro_query('MATCH (n {id:$id}) RETURN n.valid_to IS NULL,n.text_content,n.title,n.state',params={'id':ident}).result_set
        return dict(zip(('current','text','title','state'),rows[0])) if rows else None

    def revisions(self,ident):
        return self.db.graph.ro_query('MATCH (r:SourceRevision {source_unit_id:$id}) RETURN count(r)',params={'id':ident}).result_set[0][0]

    def checks(self,stage):
        i=self.ids(); run=self.run
        if stage=='created': return {
            'plane item visible with title':lambda:(self.read(i['item']) or {}).get('title')==f'[Freshness probe {run}] created',
            'plane comment visible':lambda:(self.read(i['comment']) or {}).get('current') is True,
            'slack root visible':lambda:(self.read(i['root']) or {}).get('current') is True,
            'slack reply visible':lambda:(self.read(i['reply']) or {}).get('text')==f'Freshness probe {run}: reply, initial.'}
        if stage=='edited': return {
            'plane title updated':lambda:(self.read(i['item']) or {}).get('title')==f'[Freshness probe {run}] edited',
            'plane state updated':lambda:(self.read(i['item']) or {}).get('state')==self.states['completed']['name'],
            'plane description updated':lambda:(self.read(i['item']) or {}).get('text')==f'Freshness probe {run}: edited text.',
            'previous description retained as revision':lambda:self.revisions(i['item'])>=2,
            'plane comment updated':lambda:(self.read(i['comment']) or {}).get('text')==f'Probe {run} comment, edited.',
            'slack edit visible':lambda:(self.read(i['reply']) or {}).get('text')==f'Freshness probe {run}: reply, edited.'}
        return {
            'plane item retired':lambda:(self.read(i['item']) or {}).get('current') is False,
            'plane comment retired':lambda:(self.read(i['comment']) or {}).get('current') is False,
            'slack reply retired':lambda:(self.read(i['reply']) or {}).get('current') is False,
            'slack root retired':lambda:(self.read(i['root']) or {}).get('current') is False,
            'slack thread retired':lambda:(self.read(i['thread']) or {}).get('current') is False}

    def observe(self,stage,changed_at):
        checks=self.checks(stage); entry={'stage':stage,'source_changed_at':changed_at,'mode':self.mode}
        if self.mode=='inline':
            result=sync_once(self.db,self.manifest,slack=True)
            entry['sync_seconds']=result['duration_seconds']; entry['changes']=result['changes']['summary']
            entry['slack_unavailable']=[r for r in result.get('slack',[]) if r.get('status')=='unavailable']
            entry['checks']={name:bool(check()) for name,check in checks.items()}
        else:
            deadline=time.monotonic()+self.timeout; started=time.monotonic(); seen={}
            while time.monotonic()<deadline and len(seen)<len(checks):
                for name,check in checks.items():
                    if name not in seen and check(): seen[name]=round(time.monotonic()-started,1)
                if len(seen)<len(checks): time.sleep(self.interval)
            entry['checks']={name:name in seen for name in checks}
            entry['seconds_until_visible']=seen
        entry['passed']=all(entry['checks'].values())
        print(json.dumps({k:v for k,v in entry.items() if k!='changes'}),flush=True)
        self.steps.append(entry)
        return entry['passed']


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply',action='store_true',help='Perform the probe writes (default: dry run)')
    parser.add_argument('--mode',choices=('inline','watch'),default='inline')
    parser.add_argument('--timeout',type=int,default=420,help='watch mode: seconds to wait for each stage')
    parser.add_argument('--poll',type=int,default=10,help='watch mode: graph poll interval')
    parser.add_argument('--keep',action='store_true',help='Skip the delete stage (leaves probe records in the sources)')
    args=parser.parse_args()
    scope=load_scope(FOLDER/'scope.json'); manifest=load_manifest(FOLDER/'manifest.json')
    plan={'graph':GRAPH,'mode':args.mode,'plane_project':scope['project_id'],'slack_channel':scope['channels']['runtime'],
          'writes':['create 1 Plane work item + 1 comment','post 1 Slack root + 1 reply (bot)',
                    'edit item title/description/state, comment and reply','delete all four probe records'],
          'model_requests':0}
    if not args.apply:
        print(json.dumps({'dry_run':True,**plan},indent=2)); return
    db=DatabaseManager(graph_name=GRAPH); probe=Probe(scope,manifest,db,args.mode,args.timeout,args.poll)
    report={'run':probe.run,'started_at':now(),**plan}; ok=False
    try:
        if args.mode=='inline': sync_once(db,manifest,slack=True)  # Baseline so the diffs isolate the probe.
        probe.create(); ok=probe.observe('created',now())
        probe.edit(); ok=probe.observe('edited',now()) and ok
        if not args.keep: probe.delete(); ok=probe.observe('deleted',now()) and ok
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'; ok=False
        if not args.keep: probe.cleanup()
    finally: probe.close()
    report.update(finished_at=now(),passed=ok,steps=probe.steps)
    (FOLDER/f'change-probe-{probe.run}.json').write_text(json.dumps({**report,'native_ids':probe.created},indent=2),encoding='utf-8')
    # Publishable summary: no native IDs or workspace identifiers.
    public={k:v for k,v in report.items() if k not in ('plane_project','slack_channel')}
    out=ROOT/f'docs/evidence/live-change-probe-{args.mode}.json'
    out.write_text(json.dumps(public,indent=2),encoding='utf-8')
    print(json.dumps({'passed':ok,'evidence':str(out.relative_to(ROOT))}))
    if not ok: raise SystemExit(1)


if __name__=='__main__': main()
