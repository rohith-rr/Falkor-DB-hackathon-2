"""Record the live Plane/Slack API responses once, scrub them, and replay them offline.

`record` runs the normal connector sync against the real accounts through a recording
HTTP transport (into a throwaway graph), then rewrites every account-identifying value:
emails, people's names, Plane workspace slug and UUIDs, Slack team/user/bot/channel IDs and
workspace domain. Replacements are consistent, so links between records survive.

`load` serves those recorded responses to the same connector code, with no credentials
or network, so reviewers can rebuild the live-connector graph from a clean checkout.
"""
import argparse
import base64
import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime,timezone
from pathlib import Path
import httpx
from .fixture import ROOT
from .core import load_manifest

FIXTURE=ROOT/'fixtures/live-replay'
LIVE=ROOT/'data/live-workspace'
VOLATILE={'oldest'}  # Slack's 30-day window start depends on when the sync runs.
DROP_KEYS={'email','avatar','avatar_url','image_24','image_32','image_48','image_72','image_192','image_512',
    'icons','first_name','last_name','user_profile'}
UUID=re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b')
SLACK_ID=re.compile(r'\b[TCUBWAGEF](?=[A-Z0-9]*\d)[A-Z0-9]{8,12}\b')
EMAIL=re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
WORKSPACE='wayfinder-demo'; SLACK_DOMAIN='wayfinder-demo'; OWNER='Sandbox Owner'


def key(method,url,params):
    params={k:v for k,v in sorted(params.items()) if k not in VOLATILE}
    return f"{method} {url.host}{url.path}?{json.dumps(params,sort_keys=True)}"


class RecordingTransport(httpx.BaseTransport):
    def __init__(self,log):
        self.inner=httpx.HTTPTransport(); self.log=log

    def handle_request(self,request):
        response=self.inner.handle_request(request); content=response.read()
        if request.method!='GET': raise RuntimeError('Recording is read-only; refusing '+request.method)
        try: body=json.loads(content)
        except ValueError: body=None
        self.log.append({'method':request.method,'host':request.url.host,'path':request.url.path,
            'params':dict(request.url.params),'status':response.status_code,'body':body})
        headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-encoding','content-length','transfer-encoding')}
        return httpx.Response(response.status_code,headers=headers,content=content,request=request)


class ReplayTransport(httpx.BaseTransport):
    def __init__(self,entries):
        self.index={}; self.misses=[]
        for e in entries:
            url=httpx.URL(f"https://{e['host']}{e['path']}")
            self.index[key(e['method'],url,e['params'])]=e

    def handle_request(self,request):
        entry=self.index.get(key(request.method,request.url,dict(request.url.params)))
        if entry is None:
            self.misses.append(f'{request.method} {request.url}')
            if request.url.host=='slack.com': return httpx.Response(200,json={'ok':False,'error':'not_recorded'})
            return httpx.Response(404,json={'error':'not recorded'})
        return httpx.Response(entry['status'],json=entry['body'])


def patch_clients(transport,token=None):
    """Route both connectors through one transport; returns a restore function."""
    from . import plane,slack
    real_plane,real_slack=plane.PlaneClient,slack.SlackClient
    def make_plane(workspace):
        client=real_plane(workspace,token=token)
        headers=dict(client.session.headers); client.session.close()
        client.session=httpx.Client(timeout=30,headers=headers,transport=transport); return client
    def make_slack():
        client=real_slack(token=token)
        headers=dict(client.session.headers); client.session.close()
        client.session=httpx.Client(base_url='https://slack.com/api/',timeout=30,headers=headers,transport=transport); return client
    plane.PlaneClient,slack.SlackClient=make_plane,make_slack
    def restore(): plane.PlaneClient,slack.SlackClient=real_plane,real_slack
    return restore


class Scrubber:
    def __init__(self,salt,workspace,slack_domain,people):
        self.salt=salt.encode(); self.workspace=workspace; self.slack_domain=slack_domain
        self.people=sorted({p for p in people if p and len(p)>2},key=len,reverse=True)

    def mac(self,value): return hmac.new(self.salt,value.encode(),hashlib.sha256).digest()

    def fake_uuid(self,match): return str(uuid.UUID(bytes=self.mac(match.group(0))[:16],version=4))

    def fake_slack(self,match):
        value=match.group(0)
        return value[0]+base64.b32encode(self.mac(value)).decode()[:len(value)-1]

    def text(self,value):
        value=EMAIL.sub('owner@example.invalid',value)
        value=value.replace(f'{self.slack_domain}.slack.com',f'{SLACK_DOMAIN}.slack.com')
        value=re.sub(rf'(?<![\w-]){re.escape(self.workspace)}(?![\w-])',WORKSPACE,value)
        for person in self.people: value=value.replace(person,OWNER)
        return SLACK_ID.sub(self.fake_slack,UUID.sub(self.fake_uuid,value))

    def value(self,data):
        if isinstance(data,dict): return {self.text(k):self.value(v) for k,v in data.items() if k not in DROP_KEYS}
        if isinstance(data,list): return [self.value(v) for v in data]
        if isinstance(data,str): return self.text(data)
        return data

    def leaks(self,serialized,originals):
        found=[o for o in originals if o and o in serialized]
        found+=[e for e in EMAIL.findall(serialized) if not e.endswith('example.invalid')]
        return found


def people_strings(log):
    """Names and emails returned for Plane members and Slack users/bots."""
    names=set()
    for entry in log:
        stack=[entry['body']]
        while stack:
            item=stack.pop()
            if isinstance(item,dict):
                for k,v in item.items():
                    if isinstance(v,(dict,list)): stack.append(v)
                    elif isinstance(v,str) and k in ('display_name','first_name','last_name','email','real_name',
                            'member__display_name','member__first_name','member__last_name','member__email'):
                        names.add(v); names.add(v.split('@')[0])
            elif isinstance(item,list): stack.extend(item)
    return names


def record(out=FIXTURE):
    from ..db import DatabaseManager
    from .freshness import sync_once
    from .live_seed import load_scope
    scope=load_scope(LIVE/'scope.json'); manifest=load_manifest(LIVE/'manifest.json')
    salt_file=LIVE/'replay-salt.txt'
    if not salt_file.exists(): salt_file.write_text(secrets.token_hex(32),encoding='utf-8')
    log=[]; restore=patch_clients(RecordingTransport(log))
    db=DatabaseManager(graph_name='wayfinder_replay_recording_'+uuid.uuid4().hex[:8])
    try:
        result=sync_once(db,manifest,slack=True)
        domain=next(e['body'].get('url','') for e in log if e['path'].endswith('auth.test'))
        domain=re.sub(r'^https://|\.slack\.com/?$','',domain)
    finally:
        restore(); db.clear_graph()
    unavailable=[r for r in result.get('slack',[]) if r.get('status')=='unavailable']
    if unavailable: raise RuntimeError(f'Recording incomplete: {unavailable}')
    def rows(body): return body if isinstance(body,list) else (body or {}).get('results',(body or {}).get('data',[])) or []
    attachments=[e for e in log if e['path'].endswith('/attachments/') and rows(e['body'])]
    if attachments: raise RuntimeError('Attachment downloads are not recorded; this project has attachments')
    people=people_strings(log)|{p['name'] for p in manifest.get('people',[])}
    scrub=Scrubber(salt_file.read_text(encoding='utf-8').strip(),scope['workspace'],domain,people)
    recorded_at=datetime.now(timezone.utc).isoformat()
    responses={'recorded_at':recorded_at,'synthetic':True,'requests':len(log),'entries':scrub.value(log)}
    replay_manifest=scrub.value(manifest)
    replay_manifest['replay']={'recorded_at':recorded_at,'source':'scrubbed recorded Plane/Slack API responses'}
    originals=[scope['workspace'],scope['project_id'],scope['slack_team_id'],*scope['channels'].values(),domain,*people]
    serialized=json.dumps([responses,replay_manifest])
    leaks=scrub.leaks(serialized,originals)
    if leaks: raise RuntimeError(f'Scrub incomplete; refusing to write ({len(leaks)} identifying values remain)')
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    (out/'responses.json').write_text(json.dumps(responses,indent=1,ensure_ascii=False),encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps(replay_manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    return {'requests':len(log),'recorded_at':recorded_at,'written':str(out.relative_to(ROOT)),
            'live_changes_observed':result['changes']['summary']}


def load(db,folder=FIXTURE):
    """Rebuild the live-connector graph from the recorded responses; no credentials or network."""
    from .freshness import sync_once
    folder=Path(folder)
    data=json.loads((folder/'responses.json').read_text(encoding='utf-8'))
    manifest=load_manifest(folder/'manifest.json')
    transport=ReplayTransport(data['entries']); restore=patch_clients(transport,token='replay-no-network')
    try: result=sync_once(db,manifest,slack=True)
    finally: restore()
    if transport.misses: raise RuntimeError(f'Replay requested unrecorded responses: {transport.misses[:3]}')
    # Coverage must not claim a live observation.
    db.query('MATCH (c:SourceCoverage) WHERE c.organization_id=$org SET c.mode=\'replay\',c.recorded_at=$at,'
        'c.gaps=coalesce(c.gaps,[])+[$gap]',{'org':manifest['organization_id'],'at':data['recorded_at'],
        'gap':f"Replayed from responses recorded at {data['recorded_at']}; not a live observation."})
    return {'graph':db.graph_name,'recorded_at':data['recorded_at'],'changes':result['changes']['summary']}


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('record',help='Read live Plane/Slack (credentials required) and write the scrubbed fixture')
    loader=sub.add_parser('load',help='Rebuild a graph offline from the committed fixture')
    loader.add_argument('--graph',default='wayfinder_company_live_replay')
    loader.add_argument('--reset',action='store_true',help='Clear the target graph first')
    args=parser.parse_args()
    if args.command=='record': result=record()
    else:
        from ..db import DatabaseManager
        if args.graph=='wayfinder_company_live_demo': raise SystemExit('Refusing to overwrite the live connector graph; choose another name')
        db=DatabaseManager(graph_name=args.graph)
        if args.reset:
            try: db.clear_graph()
            except Exception: pass
        result=load(db)
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
