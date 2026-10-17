import argparse
import json
from pathlib import Path
from ..db import DatabaseManager
from .core import load_manifest
from .fixture import MANIFEST,DATA,ingest_fixture


def main():
    parser=argparse.ArgumentParser(description='Fictional company sandbox; live source access is explicit')
    parser.add_argument('--graph',default='wayfinder_company_demo')
    parser.add_argument('--manifest',default=str(MANIFEST))
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('fixture')
    sub.add_parser('check-plane')
    sync=sub.add_parser('sync'); sync.add_argument('--slack',action='store_true'); sync.add_argument('--extractions',type=int,default=0)
    extract=sub.add_parser('extract'); extract.add_argument('--limit',type=int,default=20); extract.add_argument('--unit-id')
    brief=sub.add_parser('brief'); brief.add_argument('component'); brief.add_argument('--output')
    prepare=sub.add_parser('prepare'); prepare.add_argument('--brief',required=True); prepare.add_argument('--title',required=True); prepare.add_argument('--description',required=True)
    approve=sub.add_parser('approve'); approve.add_argument('action_id'); approve.add_argument('--hash',required=True); approve.add_argument('--by',required=True); approve.add_argument('--yes',action='store_true')
    execute=sub.add_parser('execute'); execute.add_argument('action_id')
    repair=sub.add_parser('repair-knowledge'); repair.add_argument('--apply',action='store_true'); repair.add_argument('--backup')
    export=sub.add_parser('import-pages'); export.add_argument('path')
    evaluation=sub.add_parser('evaluate'); evaluation.add_argument('--answers')
    args=parser.parse_args(); db=DatabaseManager(graph_name=args.graph); manifest=load_manifest(args.manifest)
    if args.command=='fixture': result=ingest_fixture(db,args.manifest)
    elif args.command=='check-plane':
        from .plane import PlaneClient
        client=PlaneClient(manifest['plane']['workspace'])
        try: result=client.check(manifest['plane']['project_ids'])
        finally: client.close()
    elif args.command=='sync':
        from .plane import sync_plane
        from .links import link_manifest
        from .core import CompanyStore
        result={'plane':sync_plane(db,manifest)}
        if args.slack:
            from .slack import sync_slack
            result['slack']=sync_slack(db,manifest)
        link_manifest(CompanyStore(db,manifest))
        if args.extractions:
            from .extract import extract_units
            result['extraction']=extract_units(db,manifest['organization_id'],args.extractions)
        from ..derived.concerns import ConcernResolver
        result['concerns']=ConcernResolver(db).resolve_concern_statuses()
    elif args.command=='extract':
        from .extract import extract_units
        result=extract_units(db,manifest['organization_id'],args.limit,args.unit_id)
        from ..derived.concerns import ConcernResolver
        result['concerns']=ConcernResolver(db).resolve_concern_statuses()
    elif args.command=='brief':
        from .brief import draft_brief,validate_brief
        result=draft_brief(db,args.component)
        validation=validate_brief(db,result)
        if not validation['valid']: raise SystemExit(json.dumps(validation))
        if args.output: Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    elif args.command in ('prepare','approve','execute'):
        from .actions import PlaneActions
        actions=PlaneActions(db,manifest)
        if args.command=='prepare': result=actions.prepare(args.title,args.description,json.loads(Path(args.brief).read_text(encoding='utf-8')))
        elif args.command=='execute': result=actions.execute(args.action_id)
        else:
            action=actions.get(args.action_id)
            print(json.dumps(json.loads(action['payload_json']),indent=2))
            if not args.yes and input('Approve this exact payload? Type APPROVE: ')!='APPROVE': raise SystemExit('Not approved')
            result=actions.approve(args.action_id,args.hash,args.by)
    elif args.command=='repair-knowledge':
        from .repair import repair_knowledge
        result=repair_knowledge(db,args.apply,args.backup)
    elif args.command=='import-pages':
        from .core import CompanyStore
        from .plane import ingest_project
        data=json.loads(Path(args.path).read_text(encoding='utf-8'))
        if not data.get('synthetic'): raise ValueError('Export must be explicitly synthetic')
        store=CompanyStore(db,manifest); store.bootstrap()
        for project in data['projects']:
            if project['id'] not in manifest['plane']['project_ids']: raise ValueError('Project not allowlisted')
            project.update(work_items=[],items_complete=False,pages_complete=False)
            ingest_project(store,project,mode='export')
            store.coverage('plane',f"project:{project['id']}:pages",'partial',mode='export',
                count=len(project['pages']),gaps=[f"Export snapshot at {data.get('exported_at','unknown time')}; live coverage unproven"])
        result={'imported':'page export','live':False}
    else:
        from .evaluation import evaluate
        result=evaluate(db,args.answers)
    print(json.dumps(result,indent=2,default=str))


if __name__=='__main__': main()
