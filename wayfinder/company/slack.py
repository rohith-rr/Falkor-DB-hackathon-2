"""Optional channel-allowlisted Slack reader; no posting, DMs, or file downloads."""
import os
import time
from datetime import datetime,timezone,timedelta
import httpx
from .core import CompanyStore


class SlackClient:
    def __init__(self,token=None,session=None):
        token=token or os.getenv('SLACK_USER_TOKEN') or os.getenv('SLACK_BOT_TOKEN')
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
                roots={m.get('thread_ts',m['ts']) for m in messages}
                saved=db.graph.ro_query('MATCH (t:Thread) WHERE t.source_system=\'slack\' AND t.channel_id=$channel '
                    'AND t.valid_to IS NULL RETURN t.source_ref',params={'channel':cid}).result_set
                roots.update(str(r[0]).rsplit(':',1)[-1] for r in saved)
                threads=[]
                for root in sorted(roots):
                    replies,thread_limited=client.messages('conversations.replies',channel=cid,ts=root)
                    for m in replies:
                        m['url']=client.call('chat.getPermalink',channel=cid,message_ts=m['ts'])['permalink']
                    threads.append({'ts':root,'messages':replies,'complete':not (limited or thread_limited),
                        'url':replies[0]['url'] if replies else f'https://app.slack.com/archives/{cid}'})
                ingest_channel(store,{'id':cid,'threads':threads,'gaps':[
                    '30-day polling scope; retention and unavailable deleted parent threads may hide records.']+
                    (['Slack reports limited history.'] if limited else [])})
                report.append({'channel':cid,'threads':len(threads)})
            except Exception as exc:
                store.coverage('slack',f'channel:{cid}','unavailable',gaps=[str(exc)])
                report.append({'channel':cid,'status':'unavailable','reason':str(exc)})
        return report
    finally: client.close()
