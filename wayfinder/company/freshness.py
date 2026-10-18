"""Observable source freshness: what a sync changed, and a polling watcher.

A sync is idempotent, so "did the graph follow the source?" needs a before/after
comparison of current source records rather than a count of upserts.
"""
import json
import time
from pathlib import Path
from ..models.common import now_iso

LABELS=('Issue','Comment','DocPage','DocSection','Thread','Attachment','PullRequest','Review','Branch','Commit')
FIELDS=('content_hash','title','name','state','priority','start_date','target_date','source_updated_at','head_sha')
EDGES=('ASSIGNED_TO','HAS_COMMENT','REFERENCES','HAS_WORK_ITEM','HAS_DOCUMENT','CONCERNS_COMPONENT','TOUCHES',
    'HEAD_AT','HAS_BRANCH','FROM_BRANCH','INTO_BRANCH','MERGED_AS')


def snapshot(db,org):
    """Current (valid_to IS NULL) source records and confirmed edges for one organization."""
    try:
        nodes=db.graph.ro_query('MATCH (n) WHERE n.organization_id=$org AND n.source_system IN [\'plane\',\'slack\',\'github\'] '
            'AND n.valid_to IS NULL AND labels(n)[0] IN $labels '
            'RETURN n.id,labels(n)[0],'+','.join('n.'+f for f in FIELDS),
            params={'org':org,'labels':list(LABELS)}).result_set
        edges=db.graph.ro_query('MATCH (a)-[r]->(b) WHERE a.organization_id=$org AND type(r) IN $types '
            'AND r.valid_to IS NULL RETURN DISTINCT a.id+\'->\'+type(r)+\'->\'+b.id',
            params={'org':org,'types':list(EDGES)}).result_set
    except Exception as exc:
        if 'Invalid graph' in str(exc) or 'empty key' in str(exc): return {'nodes':{},'edges':set()}
        raise
    return {'nodes':{r[0]:{'label':r[1],**dict(zip(FIELDS,r[2:]))} for r in nodes},
            'edges':{r[0] for r in edges}}


def diff(before,after):
    old,new=before['nodes'],after['nodes']
    created=[{'id':k,'label':v['label'],'title':v.get('title') or v.get('name')} for k,v in new.items() if k not in old]
    retired=[{'id':k,'label':v['label'],'title':v.get('title') or v.get('name')} for k,v in old.items() if k not in new]
    updated=[]
    for key in old.keys()&new.keys():
        # source_updated_at alone moves on Plane reads of unchanged records; report it only with a real change.
        fields={f:[old[key].get(f),new[key].get(f)] for f in FIELDS if old[key].get(f)!=new[key].get(f)}
        if set(fields)-{'source_updated_at'}:
            if 'content_hash' in fields: fields['content_hash']=['changed','changed']
            updated.append({'id':key,'label':new[key]['label'],'fields':fields})
    added=sorted(after['edges']-before['edges']); removed=sorted(before['edges']-after['edges'])
    return {'created':created,'updated':updated,'retired':retired,'edges_added':added,'edges_retired':removed,
        'summary':{'created':len(created),'updated':len(updated),'retired':len(retired),
                   'edges_added':len(added),'edges_retired':len(removed)}}


def sync_once(db,manifest,*,slack=False,github=False,extractions=0):
    """The single sync path used by the CLI, the scheduler and the watcher."""
    from .plane import sync_plane
    from .links import link_manifest
    from .core import CompanyStore
    from ..derived.concerns import ConcernResolver
    before=snapshot(db,manifest['organization_id']); started=now_iso(); clock=time.monotonic()
    result={'started_at':started,'plane':sync_plane(db,manifest)}
    if slack:
        from .slack import sync_slack
        result['slack']=sync_slack(db,manifest)
    if github:
        from .github_live import sync_github
        result['github']=sync_github(db,manifest)
    link_manifest(CompanyStore(db,manifest))
    if extractions:
        from .extract import extract_units
        result['extraction']=extract_units(db,manifest['organization_id'],extractions)
    result['concerns']=ConcernResolver(db).resolve_concern_statuses()
    result['changes']=diff(before,snapshot(db,manifest['organization_id']))
    result['finished_at']=now_iso(); result['duration_seconds']=round(time.monotonic()-clock,2)
    return result


def watch(db,manifest,*,interval=120,slack=False,github=False,iterations=None,log=None,out=None):
    """Poll the allowlisted sources and append each observed change set to a JSONL log."""
    log=Path(log) if log else None
    if log: log.parent.mkdir(parents=True,exist_ok=True)
    count=0
    while iterations is None or count<iterations:
        count+=1; started=time.monotonic()
        try:
            result=sync_once(db,manifest,slack=slack,github=github)
            entry={'at':result['finished_at'],'graph':db.graph_name,'duration_seconds':result['duration_seconds'],
                   'summary':result['changes']['summary'],'changes':result['changes'],
                   'unavailable':[r for r in result.get('slack',[]) if isinstance(r,dict) and r.get('status')=='unavailable']}
        except Exception as exc:
            entry={'at':now_iso(),'graph':db.graph_name,'error':f'{type(exc).__name__}: {exc}'}
        if log:
            with log.open('a',encoding='utf-8') as handle: handle.write(json.dumps(entry,default=str)+'\n')
        if out: out(entry)
        else: print(json.dumps({k:v for k,v in entry.items() if k!='changes'},default=str),flush=True)
        if iterations is not None and count>=iterations: break
        time.sleep(max(0,interval-(time.monotonic()-started)))


def counts(units):
    out={}
    for u in units:
        key='page' if u['label']=='DocSection' else f"{u['source']} message"
        out[key]=out.get(key,0)+1
    return out


def refresh(db,manifest,*,extract=0,slack=None,github=None,log=None,notify_slack=False):
    """One pass: sync every configured source, extract up to `extract` pending units (pages first),
    recompute which decision holds for each target, then re-verify saved answers and mark the ones
    that stopped being VERIFIED as stale.

    extract=0 makes no model calls; it reports what is pending. notify_slack posts one message
    per newly stale answer to the sandbox channel.
    """
    from .extract import pending_units,extract_units
    from ..derived.concerns import ConcernResolver
    clock=time.monotonic(); org=manifest['organization_id']
    slack=bool(manifest.get('slack',{}).get('channel_ids')) if slack is None else slack
    github=bool((manifest.get('github') or {}).get('repository')) if github is None else github
    synced=sync_once(db,manifest,slack=slack,github=github)
    before=pending_units(db,org)
    summary={'graph':db.graph_name,'sources':{'plane':[{k:p.get(k) for k in ('items','pages')} for p in synced['plane']]},
        'changes':synced['changes']['summary'],'pending_before':counts(before)}
    if slack: summary['sources']['slack']=[{k:r[k] for k in ('channel','threads','status','reason') if r.get(k) is not None} for r in synced.get('slack',[])]
    if github:
        g=synced.get('github') or {}
        summary['sources']['github']={k:g.get(k) for k in ('pull_requests','branches','commits','status','reason') if g.get(k) is not None}
    if extract:
        run=extract_units(db,org,extract,units=[u['id'] for u in before])
        resolved=ConcernResolver(db).resolve_concern_statuses()
        summary['extraction']={'model':run['model'],'units':len(run['results']),'generation_calls':run['attempts'],
            'accepted':run['diagnostics']['accepted'],'rejected':run['diagnostics']['rejected'],
            'rejection_reasons':run['diagnostics']['rejection_reasons'],'concerns':resolved,
            'results':[{k:r.get(k) for k in ('unit','accepted','rejected','skipped','error') if r.get(k) is not None} for r in run['results']]}
    # Ranking is recomputed after extraction so new decisions take their place in each chain.
    from ..derived.supersession import compute_supersession
    summary['supersession']=compute_supersession(db)
    after=pending_units(db,org) if extract else before
    summary['pending_after']=counts(after)
    summary['next_pending']=[f"{u['label']} {u['source']}: {u['preview']}" for u in after[:5]]
    from ..answers import mark_stale
    from ..brain_graph import BrainGraph
    summary['answers']=mark_stale(db,BrainGraph(db).rows)
    if notify_slack:
        from .slack import notify_stale_answers
        summary['answers']['notifications']=notify_stale_answers(manifest,summary['answers']['newly_stale'])
    summary['duration_seconds']=round(time.monotonic()-clock,2)
    if log:
        log=Path(log); log.parent.mkdir(parents=True,exist_ok=True)
        with log.open('a',encoding='utf-8') as handle:
            handle.write(json.dumps({'at':now_iso(),'kind':'refresh',**summary,'changes_detail':synced['changes']},default=str)+'\n')
    return summary
