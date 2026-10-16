"""Concern-specific closure. Approvals, references and task state are not resolution."""
from ..db import db_manager
from ..models.common import now_iso
from ..extraction.verifier import QuoteVerifier
from .expertise_v2 import date
import re


def explicit_resolution(quote):
    if not quote or '?' in quote or re.search(r'\b(not|never|unresolved|pending|claim|claims|claimed)\b',quote,re.I):
        return False
    return bool(re.search(r'\b(concern|issue|problem)\b.{0,100}\b(resolved|fixed)\b|\b(resolved|fixed)\b.{0,100}\b(concern|issue|problem)\b',quote,re.I))


class ConcernResolver:
    def __init__(self,db=None):
        self.db=db or db_manager

    def resolve_concern_statuses(self):
        stats={'open':0,'claimed_addressed':0,'resolved':0}
        concerns=self.db.query("MATCH (a:Argument {kind:'concern'}) WHERE a.valid_to IS NULL "
            'RETURN a.id,a.status,coalesce(a.source_timestamp,a.valid_from)').result_set
        for ident,previous,at in concerns:
            raisers={r[0] for r in self.db.query('MATCH (p:Person)-[r:MADE]->(:Argument {id:$id}) '
                'WHERE r.valid_to IS NULL AND p.valid_to IS NULL RETURN p.id',{'id':ident}).result_set}
            owners={r[0] for r in self.db.query('MATCH (:Argument {id:$id})-[raised:RAISES_CONCERN_ON]->(target)'
                '<-[r:OWNS_COMPONENT]-(p:Person) WHERE r.valid_to IS NULL AND raised.valid_to IS NULL '
                'AND p.valid_to IS NULL RETURN p.id',{'id':ident}).result_set}
            resolutions=self.db.query('MATCH (resolution:Argument)-[r:RESOLVES]->(:Argument {id:$id}) '
                'MATCH (p:Person)-[m:MADE]->(resolution) MATCH (source)-[assertion:ASSERTS]->(resolution) '
                'WHERE r.valid_to IS NULL AND m.valid_to IS NULL AND assertion.valid_to IS NULL '
                "AND resolution.kind='resolution' AND resolution.valid_to IS NULL AND source.valid_to IS NULL AND p.valid_to IS NULL "
                'AND coalesce(source.is_deleted,false)=false AND coalesce(p.is_bot,false)=false '
                'RETURN p.id,coalesce(resolution.source_timestamp,resolution.valid_from),'
                'r.evidence_quote,coalesce(source.body_text,source.text_content),source.id,r.authority',{'id':ident}).result_set
            evidence=[r for r in resolutions if r[0] in raisers|owners and date(at) and date(r[1]) and
                date(r[1])>date(at) and QuoteVerifier.verify_quote(r[2],r[3]) and
                explicit_resolution(r[2])]
            addressed=self.db.query('MATCH (later:Argument)-[r:ADDRESSES]->(:Argument {id:$id}) '
                'WHERE r.valid_to IS NULL AND later.valid_to IS NULL RETURN count(r)',{'id':ident}).result_set[0][0]
            status='resolved' if evidence else 'claimed_addressed' if addressed else 'open'
            stamp=now_iso()
            if previous!=status:
                self.db.upsert_node('ConcernStatusRevision',{'id':f'{ident}:status:{stamp}',
                    'concern_id':ident,'previous_status':previous,'status':status,'observed_at':stamp,
                    'rule_version':'explicit-resolution-v2'})
            self.db.query('MATCH (a:Argument {id:$id}) SET a.status=$status,a.resolution_evidence_ids=$evidence,'
                'a.resolution_rule_version=\'explicit-resolution-v2\',a.resolution_authority=$authority',
                {'id':ident,'status':status,'evidence':[r[4] for r in evidence],
                 'authority':'EXTRACTED' if evidence else None})
            stats[status]+=1
        return stats
