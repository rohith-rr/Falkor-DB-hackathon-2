"""Allowlisted Plane Cloud v2 ingestion. Never falls back to browser scraping."""
import os
import time
from urllib.parse import quote,urlparse,urljoin
import httpx
from pathlib import Path
from dotenv import dotenv_values
from .core import CompanyStore, html_text
from ..models.common import now_iso


class PlaneClient:
    def __init__(self,workspace,token=None,session=None):
        token=token or os.getenv('PLANE_API_KEY') or dotenv_values(Path(__file__).resolve().parents[2]/'.env').get('PLANE_API_KEY')
        if not token: raise ValueError('Set PLANE_API_KEY for Plane Cloud access')
        self.workspace=workspace
        self.root=f'https://api.plane.so/api/v2/workspaces/{quote(workspace,safe="")}/'
        self.session=session or httpx.Client(timeout=30,headers={'X-Api-Key':token})

    def request(self,method,path,**kwargs):
        for attempt in range(3):
            response=self.session.request(method,self.root+path,**kwargs)
            if response.status_code==429:
                delay=float(response.headers.get('Retry-After','1'))
                if delay>60 or attempt==2: raise RuntimeError('Plane rate limited; resume this scope later')
                time.sleep(max(0,delay)); continue
            if response.status_code in (401,402,403,404):
                raise RuntimeError(f'Plane capability unavailable: HTTP {response.status_code} at {path}')
            response.raise_for_status(); return response.json()
        raise RuntimeError('Plane request did not complete')

    def listing(self,path):
        params={'per_page':100,'fields':'all'}; seen=set(); rows=[]
        for _ in range(10000):
            payload=self.request('GET',path,params=params)
            if isinstance(payload,list): return rows+payload
            page=payload.get('data',payload.get('results'))
            if not isinstance(page,list): raise RuntimeError('Unrecognized Plane pagination envelope')
            rows.extend(page)
            style=payload.get('pagination',{}).get('style','offset')
            if style=='cursor':
                if not payload.get('has_more'): return rows
                cursor=payload.get('next_cursor'); marker=('cursor',cursor)
                params.update(paginate='cursor',cursor=cursor)
            else:
                offset=payload.get('next'); marker=('offset',offset)
                if offset is None: return rows
                if not isinstance(offset,int): raise RuntimeError('Invalid Plane offset')
                params['offset']=offset
            if marker in seen or marker[1] is None: raise RuntimeError('Plane pagination did not advance')
            seen.add(marker)
        raise RuntimeError('Plane pagination exceeded safety bound')

    def check(self,projects):
        result=[]
        for project in projects:
            for endpoint in ('','work-items/','pages/','members/','states/'):
                path=f'projects/{project}/{endpoint}'
                try:
                    self.request('GET',path,params={} if not endpoint else {'per_page':1})
                    result.append({'project':project,'resource':endpoint or 'project','status':'available'})
                except RuntimeError as exc:
                    result.append({'project':project,'resource':endpoint,'status':'unavailable','reason':str(exc)})
        return result

    def close(self): self.session.close()

    def attachment_text(self,asset,allowed_hosts):
        if asset.get('content_type') not in ('text/plain','text/markdown') or asset.get('size',0)>1048576:
            return None
        url=urljoin('https://api.plane.so',asset.get('asset_url',''))
        for _ in range(3):
            parsed=urlparse(url)
            if parsed.scheme!='https' or parsed.hostname not in allowed_hosts: raise ValueError('Attachment host is not allowlisted')
            headers={'X-Api-Key':self.session.headers.get('X-Api-Key','')} if parsed.hostname=='api.plane.so' else {}
            with httpx.stream('GET',url,headers=headers,timeout=30,follow_redirects=False) as response:
                if response.is_redirect:
                    url=urljoin(url,response.headers['Location']); continue
                response.raise_for_status(); content=bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content)>1048576: raise ValueError('Attachment exceeds 1 MiB')
                return content.decode('utf-8')
        raise ValueError('Too many attachment redirects')


def ingest_project(store,project,*,mode='live'):
    """Same normalized records are used by API and published offline fixtures."""
    org=store.org; native=str(project['id']); scope=f'project:{native}'
    root=f"https://app.plane.so/{store.manifest['plane']['workspace']}/projects/{native}"
    pid=f'plane:{org}:project:{native}'
    store.node('Project',pid,source='plane',url=root,name=project['name'],native=native,scope=scope)
    for member in project.get('members',[]):
        mid=str(member.get('member_id') or member.get('id'))
        person=store.person('plane',mid)
        store.node('Person',person,source='plane',url=root,login=person.rsplit(':',1)[-1],
                   display_name=member.get('display_name'),is_bot=False)
        store.edge('Person',person,'Project',pid,'MEMBER_OF','plane',root)
    for item in project.get('work_items',[]):
        ident=f"plane:{org}:issue:{item['id']}"; url=f"{root}/work-items/{item['id']}"
        store.node('Issue',ident,source='plane',url=url,native=item['id'],scope=f'{scope}:items',
            title=item['name'],number=str(item.get('sequence_id',item['id'])),
            state=item.get('state_name',item.get('state_id','unknown')),created_at=item.get('created_at'),
            updated=item.get('updated_at'),text=html_text(item.get('description_html','')),raw_html=item.get('description_html',''))
        store.edge('Project',pid,'Issue',ident,'HAS_WORK_ITEM','plane',url)
        desired=[]
        for assignee in item.get('assignee_ids',[]):
            person=store.person('plane',str(assignee))
            store.node('Person',person,source='plane',url=url,login=person.rsplit(':',1)[-1],is_bot=False)
            store.edge('Issue',ident,'Person',person,'ASSIGNED_TO','plane',url)
            desired.append(f'{ident}->ASSIGNED_TO->{person}')
        store.reconcile_edges(ident,['ASSIGNED_TO'],desired)
        for asset in item.get('attachments',[]):
            aid=f"plane:{org}:attachment:{asset['id']}"
            store.node('Attachment',aid,source='plane',url=url,native=asset['id'],scope=f'{ident}:attachments',
                name=asset.get('name','Attachment'),content_type=asset.get('content_type'),text=asset.get('text_content'))
            store.edge('Issue',ident,'Attachment',aid,'HAS_ATTACHMENT','plane',url)
        if item.get('attachments_complete'):
            store.reconcile('plane',f'{ident}:attachments',[f"plane:{org}:attachment:{a['id']}" for a in item.get('attachments',[])])
        for link in item.get('links',[]):
            # URLs remain source references; they become graph links only when a target resolves.
            store.node('SourceLink',f"plane:{org}:link:{link['id']}",source='plane',url=link.get('url',url),
                linked_from=ident,title=link.get('title'),scope=f'{ident}:links')
        author=item.get('created_by_id')
        if author:
            person=store.person('plane',str(author))
            store.node('Person',person,source='plane',url=url,login=person.rsplit(':',1)[-1],is_bot=False)
            store.edge('Person',person,'Issue',ident,'AUTHORED','plane',url)
        tid=f'{ident}:thread'
        store.node('Thread',tid,source='plane',url=url,root_ref=ident,kind='issue')
        store.edge('Thread',tid,'Issue',ident,'ON','plane',url)
        comments=[]
        for comment in item.get('comments',[]):
            cid=f"plane:{org}:comment:{comment['id']}"; comments.append(cid)
            store.node('Comment',cid,source='plane',url=url+f"#comment-{comment['id']}",
                text=html_text(comment.get('comment_html',comment.get('body',''))),native=comment['id'],
                raw_html=comment.get('comment_html',comment.get('body','')),
                scope=f'{ident}:comments',created_at=comment.get('created_at'),updated=comment.get('updated_at'),kind='issue')
            store.edge('Thread',tid,'Comment',cid,'HAS_COMMENT','plane',url)
            actor=comment.get('actor_id',comment.get('created_by_id'))
            if actor:
                person=store.person('plane',str(actor))
                store.node('Person',person,source='plane',url=url,login=person.rsplit(':',1)[-1],is_bot=False)
                store.edge('Person',person,'Comment',cid,'AUTHORED','plane',url)
        if item.get('comments_complete',True):
            store.reconcile('plane',f'{ident}:comments',comments)
            store.coverage('plane',f'{ident}:comments','complete',count=len(comments),mode=mode)
    for page in project.get('pages',[]):
        ident=f"plane:{org}:page:{page['id']}"; url=f"{root}/pages/{page['id']}"
        text=html_text(page.get('description_html',''))
        store.node('DocPage',ident,source='plane',url=url,text=text,native=page['id'],
            updated=page.get('updated_at'),scope=f'{scope}:pages',title=page['name'],path=page['id'],raw_html=page.get('description_html',''))
        store.edge('Project',pid,'DocPage',ident,'HAS_DOCUMENT','plane',url)
        sections=[]
        for index,part in enumerate(text.split('\n#')):
            sid=f'{ident}:section:{index}'; sections.append(sid)
            store.node('DocSection',sid,source='plane',url=url,text=part,native=page['id'],
                updated=page.get('updated_at'),scope=f'{ident}:sections',heading=part.splitlines()[0] if part else page['name'])
            store.edge('DocPage',ident,'DocSection',sid,'HAS_SECTION','plane',url,order=index)
        store.reconcile('plane',f'{ident}:sections',sections)
    if project.get('items_complete',True):
        store.reconcile('plane',f'{scope}:items',[f"plane:{org}:issue:{i['id']}" for i in project.get('work_items',[])])
        store.coverage('plane',f'{scope}:items','complete',count=len(project.get('work_items',[])),mode=mode)
    if project.get('pages_complete',True):
        store.reconcile('plane',f'{scope}:pages',[f"plane:{org}:page:{p['id']}" for p in project.get('pages',[])])
        store.coverage('plane',f'{scope}:pages','complete',count=len(project.get('pages',[])),mode=mode)


def sync_plane(db,manifest):
    store=CompanyStore(db,manifest); store.bootstrap()
    client=PlaneClient(manifest['plane']['workspace']); report=[]
    try:
        for pid in manifest['plane']['project_ids']:
            start=now_iso(); path=f'projects/{pid}/'
            project=client.request('GET',path)
            for resource,key in [('members/','members'),('states/','states'),('work-items/','work_items'),('pages/','pages')]:
                try:
                    rows=client.listing(path+resource)
                    if key in ('work_items','pages'):
                        rows=[client.request('GET',path+resource+str(row['id'])+'/',params={'fields':'all'}) for row in rows]
                    project[key]=rows
                except Exception as exc:
                    if key=='members' or key=='states': project[key]=[]
                    else: project[key]=[]; project['items_complete' if key=='work_items' else 'pages_complete']=False
                    store.coverage('plane',f'project:{pid}:{key}','unavailable',gaps=[str(exc)])
            states={s['id']:s['name'] for s in project.get('states',[])}
            for item in project.get('work_items',[]):
                item['state_name']=states.get(item.get('state_id'),'unknown')
                try: item['comments']=client.listing(path+f"work-items/{item['id']}/comments/")
                except Exception as exc:
                    item['comments']=[]; item['comments_complete']=False
                    store.coverage('plane',f"plane:{store.org}:issue:{item['id']}:comments",'unavailable',gaps=[str(exc)])
                for resource in ('attachments','links'):
                    try:
                        item[resource]=client.listing(path+f"work-items/{item['id']}/{resource}/")
                        item[resource+'_complete']=True
                        if resource=='attachments':
                            for asset in item[resource]:
                                try:
                                    asset['text_content']=client.attachment_text(asset,manifest['plane'].get('attachment_hosts',['api.plane.so']))
                                except Exception as exc:
                                    store.coverage('plane',f"attachment:{asset['id']}",'partial',gaps=['Attachment content unavailable: '+type(exc).__name__])
                    except Exception as exc:
                        store.coverage('plane',f"plane:{store.org}:issue:{item['id']}:{resource}",'unavailable',gaps=[str(exc)])
            ingest_project(store,project)
            report.append({'project':pid,'observed_since':start,'items':len(project.get('work_items',[])),
                           'pages':len(project.get('pages',[]))})
        return report
    finally: client.close()
