"""Retire only the two superseded demo Components after the Wayfinder source sync.

No source/API writes, deletion, model requests, or changes to either older graph.
Source IDs remain stable; obsolete Component meanings retain closed history.
"""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from wayfinder.company.core import CompanyStore,load_manifest
from wayfinder.company.live_seed import ORG,content
from wayfinder.db import DatabaseManager
from wayfinder.models.common import now_iso


def main():
    folder=ROOT/'data/live-workspace';manifest=load_manifest(folder/'manifest.json')
    if manifest.get('content_revision')!='v3-wayfinder' or manifest['organization_id']!=ORG:
        raise ValueError('Expected the reviewed Wayfinder live-demo manifest')
    db=DatabaseManager(graph_name='wayfinder_company_live_demo')
    for component in content()['components']:
        ident=f"company:{ORG}:component:{component['id']}"
        if not db.graph.ro_query('MATCH (c:Component {id:$id}) WHERE c.valid_to IS NULL RETURN c.id',params={'id':ident}).result_set:
            raise ValueError('Sync the new source context before retiring legacy Components')
    old=[f'company:{ORG}:component:'+key for key in ('node-runtime','resource-allocation')]
    before={'at':now_iso(),'graph':db.graph_name,
        'nodes':db.graph.ro_query('MATCH (c:Component) WHERE c.id IN $ids RETURN properties(c)',params={'ids':old}).result_set,
        'edges':db.graph.ro_query('MATCH (c:Component)-[r]-(n) WHERE c.id IN $ids RETURN c.id,type(r),properties(r),n.id',params={'ids':old}).result_set}
    backup=folder/'legacy-components-before-wayfinder.json'
    if not backup.exists():backup.write_text(json.dumps(before,indent=2),encoding='utf-8')
    db.query("MATCH (c:Component) WHERE c.id IN $ids AND c.source_system='manifest' AND c.valid_to IS NULL "
        'SET c.valid_to=$at,c.retired_reason=$reason WITH c OPTIONAL MATCH (c)-[r]-() '
        'WHERE r.valid_to IS NULL SET r.valid_to=$at',
        {'ids':old,'at':now_iso(),'reason':'Superseded fictional infrastructure scenario; current catalog describes the Wayfinder project'})
    CompanyStore(db,manifest).bootstrap()
    rows=db.graph.ro_query('MATCH (c:Component) WHERE c.valid_to IS NULL RETURN c.id,c.name ORDER BY c.id').result_set
    print(json.dumps({'graph':db.graph_name,'current_components':rows,'legacy_components_retired':old,'deleted_records':0},indent=2))


if __name__=='__main__':main()
