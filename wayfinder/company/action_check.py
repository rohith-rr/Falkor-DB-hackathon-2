"""Controlled Plane stub regression. Never creates a real Plane work item."""
import json
import uuid
from pathlib import Path
from .fixture import ingest_fixture,MANIFEST
from .core import load_manifest
from .brief import draft_brief
from .actions import PlaneActions
from ..db import DatabaseManager
from ..models.common import now_iso


def check(output='docs/evidence/plane-action-regression.json'):
    db=DatabaseManager(graph_name='test_action_regression_'+uuid.uuid4().hex)
    class Stub:
        calls=0
        def request(self,method,path,**kwargs):
            assert method=='POST';self.calls+=1
            return {'id':'SIMULATED-PLANE-WORK-ITEM'}
    try:
        ingest_fixture(db); brief=draft_brief(db,'Resource Allocation')
        assert brief['contributors'] and brief['ownership']
        stub=Stub(); actions=PlaneActions(db,load_manifest(MANIFEST),stub)
        proposal=actions.prepare('SIMULATED regression: verify allocation retry safety','Controlled stub; not a real task.',brief)
        try: actions.execute(proposal['action_id'])
        except ValueError: unapproved_rejected=True
        else: raise AssertionError('Unapproved execution was accepted')
        actions.approve(proposal['action_id'],proposal['payload_hash'],'AUTOMATED TEST ONLY - SIMULATED APPROVAL')
        first=actions.execute(proposal['action_id']); repeat=actions.execute(proposal['action_id'])
        assert first==repeat and stub.calls==1
        stale=actions.prepare('SIMULATED stale evidence regression','Must not be posted.',brief)
        actions.approve(stale['action_id'],stale['payload_hash'],'AUTOMATED TEST ONLY - SIMULATED APPROVAL')
        ident=brief['decisions'][0]['evidence_ids'][0]
        db.query('MATCH (d:Decision {id:$id}) SET d.valid_to=$now',{'id':ident,'now':now_iso()})
        try: actions.execute(stale['action_id'])
        except ValueError: stale_rejected=True
        else: raise AssertionError('Stale evidence was accepted')
        assert stub.calls==1
        result={'at':now_iso(),'mode':'controlled_stub_regression_NOT_live_Plane','live_task_created':False,
            'contributor_count':len(brief['contributors']),'payload_hash':proposal['payload_hash'],
            'unapproved_rejected':unapproved_rejected,'stale_evidence_rejected':stale_rejected,
            'first':first,'repeat':repeat,'stub_post_calls':stub.calls,'passed':True}
        path=Path(output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2),encoding='utf-8')
        return result
    finally: db.clear_graph()


if __name__=='__main__': print(json.dumps(check(),indent=2))
