"""Read-only access check for the explicitly designated fictional demo workspace."""
import json
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
# Account-specific IDs live only in the ignored local scope file.
SCOPE=json.loads((ROOT/'data/live-workspace/scope.json').read_text(encoding='utf-8'))
WORKSPACE=SCOPE['workspace']
PROJECT=SCOPE['project_id']
CHANNELS=list(SCOPE['channels'].values())


def main():
    env=dotenv_values(ROOT/'.env'); out={'plane':{},'slack':{}}
    plane=env.get('PLANE_API_KEY')
    slack=env.get('SLACK_USER_TOKEN') or env.get('SLACK_BOT_TOKEN')
    with httpx.Client(timeout=30) as client:
        if plane:
            for name,path in [('me','users/me/'),('project',f'workspaces/{WORKSPACE}/projects/{PROJECT}/'),
                *[(r,f'workspaces/{WORKSPACE}/projects/{PROJECT}/{r}/') for r in ('work-items','pages','members','states')]]:
                try:
                    response=client.get('https://api.plane.so/api/v2/'+path,headers={'X-Api-Key':plane},params={'per_page':100})
                    out['plane'][name]={'http':response.status_code}
                    if response.is_success: out['plane'][name]['data']=response.json()
                except Exception as exc: out['plane'][name]={'error':type(exc).__name__}
        else: out['plane']['error']='PLANE_API_KEY missing'
        if slack:
            for name,method,params in [('auth','auth.test',{}),*[(cid,'conversations.info',{'channel':cid}) for cid in CHANNELS],
                *[(cid+':history','conversations.history',{'channel':cid,'limit':5}) for cid in CHANNELS]]:
                try:
                    response=client.get('https://slack.com/api/'+method,headers={'Authorization':'Bearer '+slack},params=params)
                    row={'http':response.status_code,'scopes':response.headers.get('x-oauth-scopes')}
                    if response.is_success: row['data']=response.json()
                    out['slack'][name]=row
                except Exception as exc: out['slack'][name]={'error':type(exc).__name__}
        else: out['slack']['error']='Slack OAuth token missing'
    folder=ROOT/'data/live-workspace';folder.mkdir(parents=True,exist_ok=True)
    (folder/'access.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    summary={}
    for source,rows in out.items():
        summary[source]={}
        for name,row in rows.items():
            if isinstance(row,str): summary[source][name]=row;continue
            data=row.get('data',{})
            summary[source][name]={k:v for k,v in row.items() if k!='data'}
            if isinstance(data,dict):
                summary[source][name].update({k:data[k] for k in ('ok','error','team_id','user_id','bot_id','name') if k in data})
                if 'channel' in data: summary[source][name]['is_member']=data['channel'].get('is_member')
    print(json.dumps(summary,indent=2))


if __name__=='__main__': main()
