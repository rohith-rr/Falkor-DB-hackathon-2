"""Optional channel-allowlisted Slack reader; no DMs or file downloads.

The only write is notify_stale_answers, which posts to the sandbox channel when refresh is run with --notify-slack.
"""
import os
import time
from pathlib import Path
from datetime import datetime,timezone,timedelta
import httpx
from dotenv import dotenv_values
from .core import CompanyStore


# Channel housekeeping events are not discussion and have no human author.
SYSTEM_SUBTYPES={'channel_join','channel_leave','channel_name','channel_topic','channel_purpose','channel_archive',
    'channel_unarchive','channel_convert_to_private','channel_convert_to_public','pinned_item','unpinned_item',
    'bot_add','bot_remove','group_join','group_leave'}


class SlackClient:
    def __init__(self,token=None,session=None):
        local=dotenv_values(Path(__file__).resolve().parents[2]/'.env')
        token=token or os.getenv('SLACK_USER_TOKEN') or os.getenv('SLACK_BOT_TOKEN') or local.get('SLACK_USER_TOKEN') or local.get('SLACK_BOT_TOKEN')
        if not token: raise ValueError('Set a Slack token with channel history and replies access')
        self.session=session or httpx.Client(base_url='https://slack.com/api/',timeout=30,
            headers={'Authorization':f'Bearer {token}'})

    def call(self,method,**params):
        for attempt in range(3):
            response=self.session.get(method,params=params)
            if response.status_code==429:
                delay=float(response.headers.get('Retry-After','1'))
                if delay>60 or attempt==2: raise RuntimeError('Slack rate limited; resume later')
                time.sleep(max(0,delay)); continue
            response.raise_for_status(); data=response.json()
            if not data.get('ok'): raise RuntimeError(f"Slack {method}: {data.get('error','unknown_error')}")
            return data
        raise RuntimeError('Slack request failed')

    def messages(self,method,**params):
        rows=[]; seen=set(); limited=False
        while True:
            data=self.call(method,limit=100,**params); rows.extend(data.get('messages',[]))
            limited=limited or bool(data.get('is_limited'))
            cursor=data.get('response_metadata',{}).get('next_cursor')
            if not cursor:
                if data.get('has_more'): raise RuntimeError('Slack missing continuation cursor')
                return rows,limited
            if cursor in seen: raise RuntimeError('Slack cursor did not advance')
            seen.add(cursor); params['cursor']=cursor

    def close(self): self.session.close()


def iso(ts):
    return datetime.fromtimestamp(float(ts),timezone.utc).isoformat()


def ingest_channel(store,channel,*,mode='live'):
    cid=channel['id']; scope=f'channel:{cid}'
    for thread in channel.get('threads',[]):
        root=thread['ts']; tid=f'slack:{store.org}:thread:{cid}:{root}'
        if thread.get('deleted'):
            continue
        url=thread.get('url',f"https://app.slack.com/archives/{cid}/p{root.replace('.','')}")
        store.node('Thread',tid,source='slack',url=url,kind='chat',root_ref=tid,
            name=channel.get('name',cid),channel_id=cid,scope=scope)
        ids=[]
        for message in thread['messages']:
            ts=message['ts']; ident=f'slack:{store.org}:comment:{cid}:{ts}'; ids.append(ident)
            permalink=message.get('url',url)
            store.node('Comment',ident,source='slack',url=permalink,text=message.get('text',''),
                native=f'{cid}:{ts}',scope=f'{tid}:messages',kind='chat',created_at=iso(ts),
                updated=iso(message.get('edited',{}).get('ts',ts)))
            store.edge('Thread',tid,'Comment',ident,'HAS_COMMENT','slack',permalink)
            actor=message.get('user') or message.get('bot_id')
            if actor:
                person=store.person('slack',actor)
                store.node('Person',person,source='slack',url=permalink,login=person.rsplit(':',1)[-1],
                    is_bot=bool(message.get('bot_id') or message.get('subtype')=='bot_message'))
                store.edge('Person',person,'Comment',ident,'AUTHORED','slack',permalink)
        if thread.get('complete',False): store.reconcile('slack',f'{tid}:messages',ids)
    if channel.get('complete'):
        # Every saved root was re-read above, so a root Slack no longer returns is a confirmed deletion.
        deleted=[f"slack:{store.org}:thread:{cid}:{t['ts']}" for t in channel.get('threads',[]) if t.get('deleted')]
        for tid in deleted: store.reconcile('slack',f'{tid}:messages',[])
        store.reconcile('slack',scope,[f"slack:{store.org}:thread:{cid}:{t['ts']}" for t in channel.get('threads',[]) if not t.get('deleted')])
    store.coverage('slack',scope,'partial',count=sum(len(t['messages']) for t in channel.get('threads',[])),
        mode=mode,gaps=channel.get('gaps',['Channel history is bounded; absence does not prove deletion or inactivity.']))


def sync_slack(db,manifest,days=30):
    channels=manifest.get('slack',{}).get('channel_ids',[])
    if not channels: return {'status':'disabled','reason':'No allowlisted channels; use fixture'}
    store=CompanyStore(db,manifest); client=SlackClient(); report=[]
    try:
        client.call('auth.test')
        for cid in channels:
            if not cid.startswith('C'): raise ValueError('Only allowlisted public sandbox channels are supported')
            try:
                # Replies are fetched separately; history access alone is not enough.
                oldest=(datetime.now(timezone.utc)-timedelta(days=days,minutes=5)).timestamp()
                messages,limited=client.messages('conversations.history',channel=cid,oldest=str(oldest))
                # Membership events cannot have replies; treating them as thread
                # roots makes an otherwise readable channel fail thread_not_found.
                roots={m.get('thread_ts',m['ts']) for m in messages if m.get('subtype') not in SYSTEM_SUBTYPES}
                saved=db.graph.ro_query('MATCH (t:Thread) WHERE t.source_system=\'slack\' AND t.channel_id=$channel '
                    'AND t.valid_to IS NULL RETURN t.source_ref',params={'channel':cid}).result_set
                roots.update(str(r[0]).rsplit(':',1)[-1] for r in saved)
                threads=[]
                for root in sorted(roots):
                    try: replies,thread_limited=client.messages('conversations.replies',channel=cid,ts=root)
                    except RuntimeError as exc:
                        # A previously seen root that Slack no longer has was deleted;
                        # one missing thread must not make the whole channel unavailable.
                        if 'thread_not_found' not in str(exc): raise
                        threads.append({'ts':root,'messages':[],'complete':True,'deleted':True}); continue
                    replies=[m for m in replies if m.get('subtype') not in SYSTEM_SUBTYPES]
                    if not replies:
                        # A saved "thread" that is only a system event is retired like a deleted one.
                        threads.append({'ts':root,'messages':[],'complete':True,'deleted':True}); continue
                    for m in replies:
                        m['url']=client.call('chat.getPermalink',channel=cid,message_ts=m['ts'])['permalink']
                    threads.append({'ts':root,'messages':replies,'complete':not (limited or thread_limited),
                        'url':replies[0]['url'] if replies else f'https://app.slack.com/archives/{cid}'})
                ingest_channel(store,{'id':cid,'threads':threads,'complete':not limited,'gaps':[
                    '30-day polling scope; retention and unavailable deleted parent threads may hide records.']+
                    (['Slack reports limited history.'] if limited else [])})
                report.append({'channel':cid,'threads':len(threads)})
            except Exception as exc:
                store.coverage('slack',f'channel:{cid}','unavailable',gaps=[str(exc)])
                report.append({'channel':cid,'status':'unavailable','reason':str(exc)})
        return report
    finally: client.close()


def notify_stale_answers(manifest,items,session=None):
    """Post one message per newly stale answer to the configured sandbox channel. Failures are reported, not raised."""
    from ..answers import notice
    if not items: return []
    slack=manifest.get('slack',{}); channel=slack.get('notify_channel_id') or (slack.get('channel_ids') or [None])[0]
    if not channel or not channel.startswith('C'): return [{'status':'skipped','reason':'No allowlisted public sandbox channel'}]
    local=dotenv_values(Path(__file__).resolve().parents[2]/'.env')
    token=os.getenv('SLACK_BOT_TOKEN') or local.get('SLACK_BOT_TOKEN')
    if not token and session is None: return [{'status':'skipped','reason':'Set SLACK_BOT_TOKEN to post notifications'}]
    http=session or httpx.Client(base_url='https://slack.com/api/',timeout=30,headers={'Authorization':f'Bearer {token}'})
    out=[]
    try:
        for item in items:
            try:
                response=http.post('chat.postMessage',json={'channel':channel,'text':notice(item),'unfurl_links':False,'unfurl_media':False})
                response.raise_for_status(); data=response.json()
                if not data.get('ok'): raise RuntimeError(data.get('error','unknown_error'))
                out.append({'answer_id':item['answer_id'],'status':'posted','channel':channel,'ts':data.get('ts')})
            except Exception as exc:
                out.append({'answer_id':item['answer_id'],'status':'failed','reason':f'{type(exc).__name__}: {exc}'})
    finally:
        if session is None: http.close()
    return out
