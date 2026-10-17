"""Local record of which graph entities each MCP tool call touched, for the live agent-path view.

One JSON line per tool call, grouped by server session, in the ignored data/agent-trace/<graph>.jsonl.
Recording never changes a tool's result and never raises into the tool call.
"""
import json
import logging
import re
import threading
import uuid
from pathlib import Path
from .models.common import now_iso

FOLDER=Path(__file__).resolve().parents[1]/'data'/'agent-trace'
# Stable graph IDs carry a source prefix; coverage and clock metadata are not navigation.
ID=re.compile(r'^(?:company|plane|slack|github|k8s|action|source-revision):\S+$')
SKIP_KEYS={'context','properties'}
MAX_NODES=300; MAX_EDGES=300
logger=logging.getLogger('wayfinder.agent_trace')


def clean(ident):
    # Relationship interval IDs append '@<hash>' to the logical ID.
    return ident.split('@',1)[0]


def touched(value):
    """Ordered node IDs and (source, type, target) edges mentioned anywhere in arguments or results."""
    nodes={}; edges={}
    def add_node(ident):
        if ident and len(nodes)<MAX_NODES: nodes.setdefault(clean(ident),None)
    def add_edge(source,kind,target):
        if source and target and len(edges)<MAX_EDGES:
            edges.setdefault((clean(source),kind,clean(target)),None); add_node(source); add_node(target)
    def walk(item):
        if isinstance(item,dict):
            if isinstance(item.get('id'),str) and 'labels' in item: add_node(item['id'])
            if item.get('source_id') and item.get('target_id') and item.get('type'):
                add_edge(item['source_id'],item['type'],item['target_id'])
            for key,child in item.items():
                if key not in SKIP_KEYS: walk(child)
        elif isinstance(item,(list,tuple)):
            for child in item: walk(child)
        elif isinstance(item,str) and ID.match(item):
            if '->' in item:
                parts=clean(item).split('->')
                if len(parts)==3: add_edge(*parts)
            else: add_node(item)
    walk(value)
    return list(nodes),[list(e) for e in edges]


def summarize(arguments):
    text=json.dumps(arguments,default=str,ensure_ascii=False)
    return json.loads(text) if len(text)<=2000 else {'truncated':text[:2000]}


class Tracer:
    def __init__(self,graph,folder=None,enabled=True):
        self.graph=graph; self.enabled=enabled; self.session=uuid.uuid4().hex[:8]; self.seq=0
        self.path=Path(folder or FOLDER)/f'{graph}.jsonl'; self.lock=threading.Lock()

    def record(self,tool,arguments,result=None,error=None):
        if not self.enabled: return None
        try:
            focus,_=touched(arguments)
            nodes,edges=touched(result) if result is not None else ([],[])
            ordered=list(dict.fromkeys(focus+nodes))
            with self.lock:
                self.seq+=1
                entry={'session':self.session,'seq':self.seq,'at':now_iso(),'graph':self.graph,'tool':tool,
                    'arguments':summarize(arguments),'focus':focus,'nodes':ordered,'edges':edges,
                    'ok':error is None,'error':error}
                self.path.parent.mkdir(parents=True,exist_ok=True)
                with self.path.open('a',encoding='utf-8') as handle: handle.write(json.dumps(entry,ensure_ascii=False)+'\n')
            return entry
        except Exception:
            logger.exception('Agent trace write failed; the tool result is unaffected')
            return None


def read(graph,session=None,after=0,folder=None):
    """Entries for one session (default: the most recent) after a sequence number, plus the session list."""
    path=Path(folder or FOLDER)/f'{graph}.jsonl'
    if not path.exists(): return {'sessions':[],'session':None,'entries':[]}
    sessions={}; rows=[]
    for order,line in enumerate(path.read_text(encoding='utf-8').splitlines()):
        try: row=json.loads(line)
        except ValueError: continue
        info=sessions.setdefault(row['session'],{'session':row['session'],'started':row['at'],'calls':0,'last':row['at']})
        info['calls']+=1; info['last']=row['at']; info['order']=order; rows.append(row)
    # Clock ticks can be coarse (two calls, one timestamp); the file's append order breaks ties.
    ordered=sorted(sessions.values(),key=lambda s:(s['last'],s['order']),reverse=True)
    for info in ordered: info.pop('order')
    chosen=session if session in sessions else (ordered[0]['session'] if ordered else None)
    return {'sessions':ordered,'session':chosen,
            'entries':[r for r in rows if r['session']==chosen and r['seq']>after]}
