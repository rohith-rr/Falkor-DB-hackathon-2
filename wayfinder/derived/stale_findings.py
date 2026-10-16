import hashlib
from datetime import datetime, timezone, timedelta
from ..db import db_manager
from ..models.common import now_iso


class StaleDetector:
    def __init__(self, db=None):
        self.db = db or db_manager

    def run_all_checks(self):
        return {'DOC_STAGE_MISMATCH': self.check_doc_stage_mismatch(),
                'OWNER_INACTIVE': self.check_inactive_owners(),
                'KEP_STATUS_MISMATCH': self.check_kep_status_mismatch(),
                'UNANSWERED_QUESTION': self.check_unanswered_questions()}

    def _reconcile(self, kind, rows):
        now, active = now_iso(), []
        for label, subject, evidence, details in rows:
            digest = hashlib.sha256(f'{kind}:{subject}:{sorted(evidence)}'.encode()).hexdigest()[:24]
            ident = f'k8s:stale:{digest}'
            active.append(ident)
            self.db.query('MERGE (f:StaleFinding {id: $id}) ON CREATE SET f.detected_at = $now, f.valid_from = $now '
                          'SET f.kind = $kind, f.subject_id = $subject, f.evidence_ids = $evidence, f.details = $details, '
                          'f.observed_at = $now, f.resolved_at = null, f.source_system = \'derived\', '
                          'f.source_url = \'derived:stale_check\', f.source_ref = $id',
                          {'id': ident, 'now': now, 'kind': kind, 'subject': subject, 'evidence': evidence, 'details': details})
            props = {'id': f'{ident}->CONCERNS->{subject}', 'valid_from': now, 'observed_at': now,
                     'authority': 'COMPUTED', 'link_method': 'COMPUTED'}
            self.db.upsert_edge('StaleFinding', ident, label, subject, 'CONCERNS', props)
            for eid in evidence:
                found = self.db.query('MATCH (n {id: $id}) RETURN labels(n)[0]', {'id': eid})
                if found.result_set:
                    self.db.upsert_edge('StaleFinding', ident, found.result_set[0][0], eid, 'EVIDENCE',
                                        dict(props, id=f'{ident}->EVIDENCE->{eid}'))
        self.db.query('MATCH (f:StaleFinding {kind: $kind}) WHERE f.resolved_at IS NULL '
                      'AND NOT f.id IN $ids SET f.resolved_at = $now', {'kind': kind, 'ids': active, 'now': now})
        return len(active)

    def check_doc_stage_mismatch(self):
        result = self.db.query('MATCH (d:DocPage)-[doc:DOCUMENTS]->(fg:FeatureGate) '
            'WHERE doc.valid_to IS NULL AND doc.stated_stage IS NOT NULL '
            'MATCH (fg)-[st:STAGE_IN]->(release:Release) WHERE st.valid_to IS NULL '
            'RETURN d.id, fg.id, doc.stated_stage, st.stage, release.version')
        latest = {}
        for did, gid, doc, stage, version in result.result_set:
            key = (did, gid)
            number = tuple(int(part) for part in version.lstrip('v').split('.')[:2])
            if key not in latest or number > latest[key][0]:
                latest[key] = (number, doc, stage)
        rows = [('DocPage', did, [gid], f"Documentation states '{doc}', latest release states '{stage}'.")
                for (did, gid), (_, doc, stage) in latest.items() if doc.lower() != stage.lower()]
        return self._reconcile('DOC_STAGE_MISMATCH', rows)

    def check_inactive_owners(self):
        result = self.db.query('MATCH (p:Person)-[r:APPROVER_IN|REVIEWER_IN]->(o:OwnersFile) '
                               'WHERE r.valid_to IS NULL AND r.paper_owner = true RETURN DISTINCT p.id, o.id, p.login, o.path')
        return self._reconcile('OWNER_INACTIVE', [('OwnersFile', oid, [pid], f"Owner '{login}' has no qualifying activity in '{path}'.")
                                                 for pid, oid, login, path in result.result_set])

    def check_kep_status_mismatch(self):
        result = self.db.query("MATCH (k:KEP)-[r:DEFINES_GATE]->(fg:FeatureGate) WHERE r.valid_to IS NULL "
                               "AND k.status = 'implementable' AND fg.current_stage = 'GA' RETURN k.id, fg.id")
        return self._reconcile('KEP_STATUS_MISMATCH', [('KEP', kid, [gid], 'Implementable KEP defines a GA gate.')
                                                      for kid, gid in result.result_set])

    def check_unanswered_questions(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        result = self.db.query('MATCH (q:Question) WHERE q.valid_to IS NULL AND q.asked_at <= $cutoff '
                               'OPTIONAL MATCH ()-[a:ANSWERS]->(q) WHERE a.valid_to IS NULL '
                               'WITH q, count(a) AS answers WHERE answers = 0 RETURN q.id, q.summary', {'cutoff': cutoff})
        return self._reconcile('UNANSWERED_QUESTION', [('Question', qid, [qid], f"Technical question '{summary}' remains unanswered after 30 days.")
                                                       for qid, summary in result.result_set])
