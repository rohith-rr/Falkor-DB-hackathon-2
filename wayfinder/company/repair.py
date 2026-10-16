"""Deterministic offline repair. Backup is mandatory before applying."""
import json
from pathlib import Path
from ..models.common import now_iso
from ..derived.concerns import ConcernResolver


def repair_knowledge(db,apply=False,backup=None):
    alternatives=db.graph.ro_query("MATCH (r:Rationale) WHERE r.kind='alternative_rejected' "
        "AND r.source_system='k8s-enhancements' RETURN r.id").result_set
    statuses=db.graph.ro_query("MATCH (d:Decision) WHERE d.id STARTS WITH 'k8s:decision:kep:' "
        "MATCH (d)-[:ABOUT]->(k:KEP) RETURN d.id,k.number,k.status,k.stage").result_set
    report={'alternatives_to_reclassify':len(alternatives),'metadata_descriptions':len(statuses),'applied':False}
    if not apply: return report
    if not backup: raise ValueError('--backup is mandatory with --apply')
    path=Path(backup).resolve(); root=Path(__file__).resolve().parents[2]
    if not path.is_relative_to(root): raise ValueError('Backup must be inside the workspace')
    if path.exists(): raise ValueError('Backup path already exists; choose a new file')
    # Raw properties are retained, including vectors, for a useful backup.
    nodes=db.graph.ro_query('MATCH (n) RETURN labels(n),properties(n)').result_set
    edges=db.graph.ro_query('MATCH (a)-[r]->(b) RETURN a.id,labels(a),type(r),b.id,labels(b),properties(r)').result_set
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'graph':db.graph_name,'created_at':now_iso(),'nodes':nodes,'edges':edges},default=str),encoding='utf-8')
    for ident, in alternatives:
        db.query("MATCH (r:Rationale {id:$id}) SET r.kind='alternative_considered',r.parser_version='rationale-v3'",{'id':ident})
    for ident,number,status,stage in statuses:
        db.query('MATCH (d:Decision {id:$id}) SET d.summary=$summary,d.observation_kind=\'metadata_status\',d.decided_at=null',
            {'id':ident,'summary':f"Observed KEP-{number} metadata: status '{status}', stage '{stage}' (transition time and decision maker unknown)"})
    report.update(applied=True,backup=str(path),concerns=ConcernResolver(db).resolve_concern_statuses())
    return report
