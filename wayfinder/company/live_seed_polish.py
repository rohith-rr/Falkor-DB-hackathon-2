"""Revise only checkpointed demo records; retain native IDs, authors, and threads.

Default prepares a local before/after plan and backup. --apply updates source
content in place, then adds one disclosure message per channel. No model calls.
Unrelated records or seed records edited outside this migration are not overwritten.
"""
import argparse
import html
import json
import re
from pathlib import Path
from .live_seed import (Seeder,ROOT,DATASET,load_scope,legacy_records,legacy_messages,
    records,messages,message_text,metadata,disclosure,content,item_description)
from .core import html_text,digest
from ..models.common import now_iso


def plain_slack(text):
    return html.unescape(re.sub(r'<(https?://[^>|]+)(?:\|[^>]+)?>',r'\1',text)).strip()


def legacy_slack_text(owner,index,position,scope,state):
    channel,thread=legacy_messages(owner)[index];text=thread[position]
    if position==0:
        component='node-runtime' if channel=='runtime' else 'resource-allocation'
        pages,_=legacy_records(owner)
        root=f"https://app.plane.so/{scope['workspace']}/projects/{scope['project_id']}"
        item=state['created']['item:'+('w1' if channel=='runtime' else 'w7')]['id']
        text+='\nPlane work item: '+root+'/work-items/'+item
        text+='\n'+'\n'.join(root+'/pages/'+state['created']['page:'+p['key']]['id'] for p in pages
            if p['component']==component and p['key']!='ownership')
    from .live_seed import NOTICE
    return text+'\n\n'+NOTICE+f'\nWayfinder seed {DATASET}:t{index}-{position}'


def assert_owned_plane(value,key):
    if value.get('external_id')!=DATASET+':'+key or value.get('external_source')!=DATASET:
        raise ValueError('Not a verified seed record: '+key)


def check_source_edit(current,original,replacement,key,normalize):
    originals=original if isinstance(original,list) else [original]
    if normalize(current) not in [normalize(v) for v in [*originals,replacement]]:
        raise ValueError('Source edited outside this migration; preserve and review: '+key)


def prepare(seeder):
    prior_path=seeder.folder/'content-update-plan.json'
    previous=json.loads(prior_path.read_text(encoding='utf-8')) if prior_path.exists() else {}
    if previous.get('scope_hash') not in (None,digest(seeder.scope)): raise ValueError('Previous plan belongs to another scope')
    if previous:
        archive=seeder.folder/f"content-update-plan-{previous['revision']}.json"
        if not archive.exists():archive.write_text(json.dumps(previous,indent=2),encoding='utf-8')
    prior={c['key']:c['payload'] for c in previous.get('changes',[])}
    revision=content()['revision']
    plan=seeder.preflight();changes=[];snapshots={}
    created=seeder.state['created']
    pages,items=records(seeder.owner)
    for kind,rows in [('page',pages),('item',items)]:
        for row in rows:
            key=kind+':'+row['key'];record=created[key]
            path=seeder.base+('pages/' if kind=='page' else 'work-items/')+record['id']+'/'
            current=seeder.plane.request('GET',path,params={'fields':'all'})
            assert_owned_plane(current,row['key']);snapshots[key]=current
            payload={k:row[k] for k in ('name','description_html')}
            if kind=='item':
                payload.update(description_html=item_description(row,seeder.scope,seeder.state),
                    state_id=seeder.states[row['status']],priority=row['priority'],target_date=content()['target_date'])
            check_source_edit(current.get('description_html',''),[record.get('description_html',''),prior.get(key,{}).get('description_html','')],payload['description_html'],key,html_text)
            check_source_edit(current['name'],[record['name'],prior.get(key,{}).get('name','')],payload['name'],key,str)
            changes.append({'kind':'plane','key':key,'path':path,'payload':payload,
                'changed':any(current.get(k)!=v for k,v in payload.items())})
            if kind=='item':
                for comment in row['comments']:
                    ck='comment:'+comment['key'];old=created[ck]
                    cp=seeder.base+f"work-items/{record['id']}/comments/{old['id']}/"
                    current=seeder.plane.request('GET',cp,params={'fields':'all'})
                    assert_owned_plane(current,comment['key']);snapshots[ck]=current
                    check_source_edit(current.get('comment_html',''),[old.get('comment_html',''),prior.get(ck,{}).get('comment_html','')],comment['comment_html'],ck,html_text)
                    changes.append({'kind':'plane','key':ck,'path':cp,'payload':{'comment_html':comment['comment_html']},
                        'changed':current.get('comment_html')!=comment['comment_html']})
    response=seeder.http.get('https://slack.com/api/auth.test',headers={'Authorization':'Bearer '+seeder.bot_token})
    response.raise_for_status();auth=response.json()
    if not auth.get('ok') or auth.get('team_id')!=seeder.scope['slack_team_id'] or not auth.get('bot_id'):
        raise ValueError('Writer must be the bot in the designated Slack workspace')
    for i,(channel,thread) in enumerate(messages(seeder.owner)):
        cid=seeder.scope['channels'][channel]
        root=created[f'slack:t{i}-0']['id']
        actual,_=seeder.slack.messages('conversations.replies',channel=cid,ts=root,include_all_metadata=True)
        by_ts={m['ts']:m for m in actual}
        for j in range(len(thread)):
            key=f'slack:t{i}-{j}';record=created[key]
            if record['channel']!=cid: raise ValueError('Checkpoint channel mismatch')
            current=by_ts[record['id']];snapshots[key]=current
            if current.get('user')!=auth['user_id'] or current.get('bot_id')!=auth['bot_id']:
                raise ValueError('Refusing to edit another author: '+key)
            text=message_text(seeder.owner,i,j,seeder.scope,seeder.state)
            check_source_edit(current['text'],[legacy_slack_text(seeder.owner,i,j,seeder.scope,seeder.state),prior.get(key,{}).get('text','')],text,key,plain_slack)
            changes.append({'kind':'slack','key':key,'payload':{'channel':cid,'ts':record['id'],'text':text,
                'metadata':metadata(f't{i}-{j}'),'parse':'none','attachments':[]},
                'changed':plain_slack(current['text'])!=plain_slack(text)})
    old_disclosure='Demo workspace: this is a fictional engineering scenario. Scripted discussions are posted by the Falkor DB app; scenario decisions and test results are simulated.'
    for channel,cid in seeder.scope['channels'].items():
        key='slack:notice-'+channel
        if key not in created:continue
        record=created[key]
        if record['channel']!=cid:raise ValueError('Disclosure checkpoint channel mismatch')
        rows,_=seeder.slack.messages('conversations.replies',channel=cid,ts=record['id'],include_all_metadata=True)
        current=next(m for m in rows if m['ts']==record['id']);snapshots[key]=current
        if current.get('user')!=auth['user_id'] or current.get('bot_id')!=auth['bot_id']:raise ValueError('Disclosure belongs to another author')
        text=disclosure();check_source_edit(current['text'],[old_disclosure,prior.get(key,{}).get('text','')],text,key,plain_slack)
        changes.append({'kind':'slack','key':key,'payload':{'channel':cid,'ts':record['id'],'text':text,
            'metadata':metadata('notice-'+channel),'parse':'none','attachments':[]},'changed':plain_slack(current['text'])!=plain_slack(text)})
    backup=seeder.folder/f'content-before-{revision}.json'
    if not backup.exists(): backup.write_text(json.dumps({'at':now_iso(),'records':snapshots},indent=2),encoding='utf-8')
    result={'at':now_iso(),'revision':revision,'scope_hash':digest(seeder.scope),'changes':changes,
        'new_disclosures':{c:disclosure() for c in seeder.scope['channels']},'backup':str(backup),'model_calls':0}
    (seeder.folder/'content-update-plan.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result,plan


def apply(seeder,revision,plan):
    if revision['scope_hash']!=digest(seeder.scope): raise ValueError('Update plan belongs to another scope')
    count=0
    for change in revision['changes']:
        if not change['changed']: continue
        if change['kind']=='plane':
            seeder.plane.request('PATCH',change['path'],json=change['payload'])
        else:
            response=seeder.http.post('https://slack.com/api/chat.update',
                headers={'Authorization':'Bearer '+seeder.bot_token},json=change['payload'])
            response.raise_for_status();value=response.json()
            if not value.get('ok'): raise RuntimeError('Slack update rejected: '+value.get('error','unknown'))
        seeder.state.setdefault('content_updates',{})[change['key']]={'revision':revision['revision'],'at':now_iso(),'payload_hash':digest(change['payload'])}
        seeder.save();count+=1;print('Updated '+change['key'],flush=True)
    for channel,cid in seeder.scope['channels'].items():
        seeder.slack_record('notice-'+channel,cid,disclosure())
    (seeder.folder/'manifest.json').write_text(json.dumps(seeder.manifest(plan),indent=2),encoding='utf-8')
    report={'at':now_iso(),'revision':revision['revision'],'updated_records':count,'channel_disclosures':2,
        'ids_preserved':True,'model_requests':0}
    (seeder.folder/'content-update-result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--scope',required=True)
    parser.add_argument('--folder',default=str(ROOT/'data/live-workspace'));parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();seeder=Seeder(load_scope(args.scope),args.folder)
    try:
        revision,plan=prepare(seeder)
        if args.apply: print(json.dumps(apply(seeder,revision,plan),indent=2))
        else: print(json.dumps({'plan':str(seeder.folder/'content-update-plan.json'),
            'changed_records':sum(c['changed'] for c in revision['changes']),'new_disclosures':2,'writes':0},indent=2))
    finally: seeder.close()


if __name__=='__main__':main()
