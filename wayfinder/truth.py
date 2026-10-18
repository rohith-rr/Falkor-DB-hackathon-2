"""Truth maintenance for agents: is an answer still true, what changed, what is stale,
who holds the knowledge, and does an approval still hold.

Everything is computed from the graph's own provenance: retained source revisions, closed
relationship intervals, extraction claims tied to their source unit, and dated activity.
All queries are read-only; results carry {id, labels} so the agent-path view can show them.
"""
import json
import re
from datetime import datetime

CLAIMS = ('Decision', 'Argument', 'Rationale', 'Question')
WORK = ('Issue', 'DocPage', 'DocSection', 'Comment', 'Thread', 'PullRequest', 'Review', 'Branch', 'Commit', 'Action') + CLAIMS
NAME = ("coalesce(n.key, n.title, n.name, n.summary, n.display_name, n.short_sha, n.path, "
        "substring(coalesce(n.text_content, n.body_text, ''), 0, 90), n.id)")
QUOTE = re.compile(r'[“"]([^”"]{12,400})[”"]')


class TruthError(ValueError):
    pass


def _iso(value, field):
    try:
        datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return str(value)
    except ValueError as exc:
        raise TruthError(f'{field} must be an ISO timestamp, e.g. 2026-10-17T00:00:00Z') from exc


def _norm(text):
    return ' '.join(re.sub(r'[^\w\s]', ' ', str(text or '').lower()).split())


def _record(rows, ident):
    found = rows(f'MATCH (n {{id:$id}}) RETURN n.id AS id, labels(n) AS labels, {NAME} AS name, '
                 'n.valid_to AS valid_to, coalesce(n.is_deleted, false) AS deleted, '
                 "coalesce(n.text_content, n.body_text, '') + ' ' + coalesce(n.title, '') + ' ' + coalesce(n.summary, '') + ' ' + coalesce(n.evidence_quote, '') AS text, "
                 'n.source_url AS url', {'id': ident})
    return found[0] if found else None


# ---------------------------------------------------------------- verify_answer

def verify_answer(rows, statements):
    """Check each answer statement against the current graph, like a reviewer would."""
    if not isinstance(statements, list) or not 1 <= len(statements) <= 20:
        raise TruthError('Provide 1..20 statements, each {"text": ..., "evidence_ids": [...]}')
    out = []
    for item in statements:
        text = str(item.get('text', '')); ids = [str(i) for i in item.get('evidence_ids', [])][:20]
        evidence, replacements, retired, current_text = [], [], [], []
        for ident in ids:
            rec = _record(rows, ident)
            if rec is None:
                edge = rows('MATCH (a)-[r]->(b) WHERE r.id=$id OR r.logical_id=$id RETURN type(r) AS type, r.valid_to AS valid_to, '
                            'a.id AS source, b.id AS target LIMIT 1', {'id': ident})
                if not edge:
                    evidence.append({'id': ident, 'status': 'NOT_FOUND'}); continue
                e = edge[0]; status = 'CURRENT' if e['valid_to'] is None else 'RETIRED'
                evidence.append({'id': ident, 'relationship': e['type'], 'status': status, 'retired_at': e['valid_to'],
                                 'source_id': e['source'], 'target_id': e['target']})
                if status == 'RETIRED': retired.append(ident)
                continue
            entry = {'id': rec['id'], 'labels': rec['labels'], 'name': rec['name'], 'url': rec['url']}
            label = rec['labels'][0]
            if label in CLAIMS:
                link = rows('MATCH (u)-[a:ASSERTS]->(c {id:$id}) RETURN u.id AS unit, a.valid_to AS retired_at, '
                            'u.valid_to AS unit_retired ORDER BY a.valid_from DESC LIMIT 1', {'id': rec['id']})
                retired_at = rec['valid_to'] or (link[0]['retired_at'] if link else None)
                if retired_at:
                    entry.update(status='OUT_OF_DATE', retired_at=retired_at)
                    if link:
                        newer = rows('MATCH (u {id:$unit})-[a:ASSERTS]->(c) WHERE a.valid_to IS NULL AND c.valid_to IS NULL '
                                     f'AND labels(c)[0]=$label RETURN c.id AS id, labels(c) AS labels, c.summary AS name, '
                                     'c.evidence_quote AS quote LIMIT 5', {'unit': link[0]['unit'], 'label': label})
                        for n in newer: n['same_meaning'] = _norm(n['name']) == _norm(rec['name'])
                        replacements += newer
                    retired.append(rec['id'])
                else:
                    entry['status'] = 'CURRENT'; current_text.append(rec['text'])
            elif rec['valid_to'] or rec['deleted']:
                entry.update(status='REMOVED', retired_at=rec['valid_to']); retired.append(rec['id'])
            else:
                entry['status'] = 'CURRENT'; current_text.append(rec['text'])
            evidence.append(entry)
        quotes = QUOTE.findall(text); haystack = _norm(' '.join(current_text))
        missing = [q for q in quotes if _norm(q) not in haystack]
        # Two current decisions about the same target from different sources is a conflict, not a choice.
        disputes = []
        for e in evidence:
            if e.get('status') == 'CURRENT' and e.get('labels', [''])[0] == 'Decision':
                disputes += rows('MATCH (d {id:$id})-[a:ABOUT]->(t)<-[b:ABOUT]-(o:Decision) WHERE a.valid_to IS NULL AND b.valid_to IS NULL '
                                 'AND o.valid_to IS NULL AND o.id<>d.id MATCH (u1)-[:ASSERTS]->(d), (u2)-[x:ASSERTS]->(o) '
                                 'WHERE x.valid_to IS NULL AND u1.id<>u2.id RETURN DISTINCT o.id AS id, labels(o) AS labels, o.summary AS name, '
                                 't.id AS about LIMIT 5', {'id': e['id']})
        valid = [e for e in evidence if e.get('status') == 'CURRENT']
        if not ids or not any(e.get('status') != 'NOT_FOUND' for e in evidence): status = 'UNSUPPORTED'
        elif retired and replacements and all(r['same_meaning'] for r in replacements) and not missing: status = 'RECITE'
        elif retired and replacements: status = 'OUT_OF_DATE'
        elif retired and not valid: status = 'REMOVED'
        elif missing: status = 'UNSUPPORTED'
        elif disputes: status = 'DISPUTED'
        elif retired: status = 'PARTLY_OUT_OF_DATE'
        else: status = 'VERIFIED'
        out.append({'text': text, 'status': status, 'evidence': evidence, 'replaced_by': replacements,
                    'quotes_not_in_current_sources': missing, 'conflicting_current_decisions': disputes})
    counts = {}
    for s in out: counts[s['status']] = counts.get(s['status'], 0) + 1
    return {'statements': out, 'summary': counts,
            'meaning': 'VERIFIED: every cited record is current and every quote appears in it. OUT_OF_DATE: a cited claim was retired '
                       'because its source changed; replaced_by holds the current claim. RECITE: the source changed but the re-extracted '
                       'claim says the same; cite replaced_by instead. REMOVED: the cited record was deleted. '
                       'DISPUTED: another current decision about the same target comes from a different source. '
                       'UNSUPPORTED: no evidence, or a quote that is not in the current source.'}


# ---------------------------------------------------------------- changes_since

def _scope(rows, component_id, hops=2):
    if not component_id: return None
    found = rows(f'MATCH (c {{id:$id}}) MATCH p=(c)-[*0..{int(hops)}]-(n) WHERE all(r IN relationships(p) WHERE r.valid_to IS NULL) '
                 # A person links everything they touch; walking through one would widen the scope to all their work.
                 "AND none(x IN nodes(p)[1..size(nodes(p))-1] WHERE 'Person' IN labels(x) OR 'Answer' IN labels(x)) "
                 'RETURN collect(DISTINCT n.id) AS ids', {'id': component_id})
    if not found or not found[0]['ids']: raise TruthError('component_id is not a current entity')
    return found[0]['ids']


def changes_since(rows, since, until=None, component_id=None, limit=25):
    """What changed in the sources since a time: new and edited text, retired records, links made and broken."""
    since = _iso(since, 'since'); until = _iso(until, 'until') if until else None
    if not 1 <= limit <= 100: raise TruthError('Use limit 1..100')
    scope = _scope(rows, component_id)
    window = 'x >= $since AND ($until IS NULL OR x < $until)'
    params = {'since': since, 'until': until, 'scope': scope, 'work': list(WORK), 'limit': limit}
    revisions = rows('MATCH (r:SourceRevision) WITH r, r.observed_at AS x WHERE ' + window +
        ' AND ($scope IS NULL OR r.source_unit_id IN $scope) WITH r.source_unit_id AS unit, max(r.observed_at) AS at '
        'MATCH (n {id:unit}) WHERE labels(n)[0] IN $work '
        'MATCH (every:SourceRevision {source_unit_id:unit}) WITH n, at, min(every.observed_at) AS first, count(every) AS revisions '
        f'RETURN n.id AS id, labels(n) AS labels, {NAME} AS name, n.source_system AS source, at, first < $since AS edited, revisions, '
        'n.valid_to IS NULL AS current ORDER BY at DESC', params)
    created = [dict(r, change='created') for r in revisions if not r['edited']]
    edited = [dict(r, change='edited') for r in revisions if r['edited']]
    retired = rows('MATCH (n) WITH n, n.valid_to AS x WHERE x IS NOT NULL AND ' + window +
        ' AND labels(n)[0] IN $work AND ($scope IS NULL OR n.id IN $scope) '
        f'RETURN n.id AS id, labels(n) AS labels, {NAME} AS name, n.source_system AS source, n.valid_to AS at ORDER BY at DESC LIMIT $limit', params)
    links = ['REFERENCES', 'ASSIGNED_TO', 'DEPENDS_ON', 'CONCERNS_COMPONENT', 'FROM_BRANCH', 'INTO_BRANCH', 'MERGED_AS', 'HEAD_AT', 'OWNS_COMPONENT', 'ASSERTS']
    made = rows('MATCH (a)-[r]->(b) WITH a, r, b, r.valid_from AS x WHERE x IS NOT NULL AND ' + window +
        ' AND type(r) IN $links AND ($scope IS NULL OR a.id IN $scope OR b.id IN $scope) '
        # An interval that replaces one closed at the same instant is a property update, not a new link.
        'OPTIONAL MATCH (a)-[prev]->(b) WHERE type(prev) = type(r) AND prev.logical_id = r.logical_id AND prev.valid_to = r.valid_from '
        'WITH a, r, b, count(prev) AS continued WHERE continued = 0 '
        'RETURN DISTINCT a.id AS source_id, type(r) AS type, b.id AS target_id, max(r.valid_from) AS at, collect(r.link_method)[0] AS method '
        'ORDER BY at DESC LIMIT $limit', {**params, 'links': links})
    broken = rows('MATCH (a)-[r]->(b) WITH a, r, b, r.valid_to AS x WHERE x IS NOT NULL AND ' + window +
        ' AND type(r) IN $links AND ($scope IS NULL OR a.id IN $scope OR b.id IN $scope) '
        'OPTIONAL MATCH (a)-[next]->(b) WHERE type(next) = type(r) AND next.logical_id = r.logical_id AND next.valid_from = r.valid_to '
        'WITH a, r, b, count(next) AS continued WHERE continued = 0 '
        'RETURN a.id AS source_id, type(r) AS type, b.id AS target_id, max(r.valid_to) AS at ORDER BY at DESC LIMIT $limit', {**params, 'links': links})
    retired = [dict(r, change='retired') for r in retired]
    by_source = {}
    for r in created + edited + retired:
        key = r.get('source') or 'derived'; by_source[key] = by_source.get(key, 0) + 1
    return {'since': since, 'until': until, 'component_id': component_id,
        'summary': {'created': len(created), 'edited': len(edited), 'retired': len(retired), 'links_made': len(made),
                    'links_broken': len(broken), 'records_by_source': by_source},
        'created': created[:limit], 'edited': edited[:limit], 'retired': retired, 'links_made': made, 'links_broken': broken,
        'note': 'Times are when Wayfinder observed each change. Task state changes are not versioned, so they are not listed.'}


# ---------------------------------------------------------------- stale_documents

def stale_documents(rows, component_id=None, limit=20):
    """Pages that may be out of date: the things they describe changed after the page was last edited."""
    if not 1 <= limit <= 50: raise TruthError('Use limit 1..50')
    pages = rows('MATCH (p:DocPage) WHERE p.valid_to IS NULL AND ($cid IS NULL OR (p)-[:DESCRIBES]->(:Component {id:$cid})) '
                 'RETURN p.id AS id, labels(p) AS labels, p.title AS name, coalesce(p.source_updated_at, p.observed_at) AS edited_at',
                 {'cid': component_id})
    out = []
    for page in pages:
        p = {'id': page['id'], 'edited_at': page['edited_at']}
        tasks = rows('MATCH (pg {id:$id})-[r:REFERENCES]-(i:Issue) WHERE r.valid_to IS NULL AND i.valid_to IS NULL '
                     'AND coalesce(i.source_updated_at, i.observed_at) > $edited_at '
                     'RETURN DISTINCT i.id AS id, labels(i) AS labels, i.key AS name, i.state AS state, i.source_updated_at AS at', p)
        code = rows('MATCH (pg {id:$id})-[d:DESCRIBES]->(c:Component)-[m:IMPLEMENTED_IN]->(root:Directory) '
                    'WHERE d.valid_to IS NULL AND m.valid_to IS NULL '
                    'MATCH (dir:Directory)-[:CHILD_OF*0..8]->(root) MATCH (dir)-[:CONTAINS]->(:File)<-[t:TOUCHES]-(pr:PullRequest) '
                    "WHERE t.valid_to IS NULL AND pr.state='MERGED' AND pr.merged_at > $edited_at "
                    'RETURN DISTINCT pr.id AS id, labels(pr) AS labels, pr.title AS name, pr.merged_at AS at ORDER BY at DESC', p)
        talk = rows('MATCH (pg {id:$id})<-[r:REFERENCES]-(m:Comment) WHERE r.valid_to IS NULL AND m.valid_to IS NULL AND m.created_at > $edited_at '
                    f'RETURN m.id AS id, labels(m) AS labels, substring(m.text_content, 0, 90) AS name, m.created_at AS at', p)
        newer = len(tasks) + len(code) + len(talk)
        out.append({'page': {k: page[k] for k in ('id', 'labels', 'name')}, 'last_edited': page['edited_at'],
            'status': 'POSSIBLY_STALE' if code or tasks else 'CURRENT' if not talk else 'DISCUSSED_SINCE',
            'newer_changes': newer, 'tasks_updated_since': tasks[:10], 'code_merged_since': code[:10], 'discussion_since': talk[:10]})
    out.sort(key=lambda x: (-x['newer_changes'], x['page']['name'] or ''))
    return {'items': out[:limit], 'checked_pages': len(out),
        'meaning': "POSSIBLY_STALE: a pull request changed the described component's code, or a linked task changed, after the "
                   'page was last edited. It is a review signal, not proof the page is wrong.'}


# ---------------------------------------------------------------- knowledge_risk

WEIGHTS = {'owner': 3.0, 'assignee': 1.0, 'pr_author': 2.0, 'reviewer': 2.0, 'code_expert': 2.0, 'discussion': 0.5}


def knowledge_risk(rows, target_id):
    """For a Component: who holds its knowledge and its bus factor. For a Person: what only they hold."""
    kind = rows('MATCH (n {id:$id}) WHERE n.valid_to IS NULL RETURN labels(n)[0] AS label', {'id': target_id})
    if not kind: raise TruthError('target_id is not a current entity')
    if kind[0]['label'] == 'Component': return _component_risk(rows, target_id)
    if kind[0]['label'] == 'Person': return _person_risk(rows, target_id)
    raise TruthError('Use a Component or Person ID')


def _component_evidence(rows, cid):
    human = 'coalesce(p.is_bot, false) = false AND p.valid_to IS NULL'
    q = {
        'owner': f'MATCH (p:Person)-[r:OWNS_COMPONENT]->(:Component {{id:$id}}) WHERE r.valid_to IS NULL AND {human} RETURN p.id AS person, $id AS via',
        'assignee': f'MATCH (i:Issue)-[c:CONCERNS_COMPONENT]->(:Component {{id:$id}}), (i)-[a:ASSIGNED_TO]->(p:Person) '
                    f'WHERE c.valid_to IS NULL AND a.valid_to IS NULL AND i.valid_to IS NULL AND {human} RETURN p.id AS person, i.id AS via',
        'pr_author': f'MATCH (pr:PullRequest)-[c:CONCERNS_COMPONENT]->(:Component {{id:$id}}), (p:Person)-[a:AUTHORED]->(pr) '
                     f'WHERE c.valid_to IS NULL AND {human} RETURN p.id AS person, pr.id AS via',
        'reviewer': f'MATCH (pr:PullRequest)-[c:CONCERNS_COMPONENT]->(:Component {{id:$id}}), (rv:Review)-[:REVIEWS]->(pr), (p:Person)-[:SUBMITTED]->(rv) '
                    f'WHERE c.valid_to IS NULL AND {human} RETURN p.id AS person, rv.id AS via',
        'code_expert': f'MATCH (:Component {{id:$id}})-[m:IMPLEMENTED_IN]->(d:Directory)<-[e:EXPERT_IN]-(p:Person) '
                       f'WHERE m.valid_to IS NULL AND e.valid_to IS NULL AND {human} RETURN p.id AS person, d.id AS via',
        'discussion': f'MATCH (x)-[c:CONCERNS_COMPONENT]->(:Component {{id:$id}}), (p:Person)-[a:AUTHORED]->(x) '
                      f"WHERE (x:Comment OR x:Thread) AND c.valid_to IS NULL AND {human} RETURN p.id AS person, x.id AS via",
    }
    people = {}
    for role, query in q.items():
        for row in rows(query, {'id': cid}):
            entry = people.setdefault(row['person'], {'roles': {}, 'evidence': set()})
            if row['via'] not in entry['evidence']:
                entry['evidence'].add(row['via']); entry['roles'][role] = entry['roles'].get(role, 0) + 1
    return people


def _component_risk(rows, cid):
    people = _component_evidence(rows, cid)
    weight = {p: sum(WEIGHTS[r] * n for r, n in e['roles'].items()) for p, e in people.items()}
    total = sum(weight.values()) or 1.0
    ranked = sorted(weight.items(), key=lambda x: -x[1]); cumulative = 0.0; bus = 0
    for _, w in ranked:
        bus += 1; cumulative += w
        if cumulative / total > 0.5: break
    holders = []
    for person, w in ranked:
        info = rows(f'MATCH (n {{id:$id}}) RETURN labels(n) AS labels, {NAME} AS name', {'id': person})[0]
        holders.append({'id': person, 'labels': info['labels'], 'name': info['name'], 'share': round(w / total, 3),
                        'roles': people[person]['roles']})
    return {'component_id': cid, 'bus_factor': bus if ranked else 0, 'people_with_evidence': len(ranked),
        'risk': 'HIGH' if bus <= 1 else 'MEDIUM' if bus == 2 else 'LOW', 'holders': holders, 'weights': WEIGHTS,
        'meaning': 'Bus factor: the fewest people who together hold more than half of the weighted evidence (ownership, assigned '
                   'tasks, authored and reviewed PRs, code expertise, discussion). Bots are excluded. It measures recorded '
                   'evidence, not actual knowledge.'}


def _person_risk(rows, pid):
    only_owner = rows('MATCH (p {id:$id})-[r:OWNS_COMPONENT]->(c:Component) WHERE r.valid_to IS NULL '
                      'OPTIONAL MATCH (o:Person)-[r2:OWNS_COMPONENT]->(c) WHERE r2.valid_to IS NULL AND o.id <> $id '
                      'WITH c, count(o) AS others WHERE others = 0 RETURN c.id AS id, labels(c) AS labels, c.name AS name', {'id': pid})
    open_tasks = rows("MATCH (i:Issue)-[a:ASSIGNED_TO]->(p {id:$id}) WHERE a.valid_to IS NULL AND i.valid_to IS NULL AND coalesce(i.state,'') <> 'Done' "
                      'OPTIONAL MATCH (i)-[a2:ASSIGNED_TO]->(o:Person) WHERE a2.valid_to IS NULL AND o.id <> $id '
                      'WITH i, count(o) AS others WHERE others = 0 RETURN i.id AS id, labels(i) AS labels, i.key AS name, i.state AS state', {'id': pid})
    only_expert = rows('MATCH (p {id:$id})-[e:EXPERT_IN]->(d:Directory) WHERE e.valid_to IS NULL '
                       'OPTIONAL MATCH (o:Person)-[e2:EXPERT_IN]->(d) WHERE e2.valid_to IS NULL AND o.id <> $id AND coalesce(o.is_bot,false)=false '
                       "WITH d, count(o) AS others WHERE others = 0 RETURN d.id AS id, labels(d) AS labels, coalesce(d.path, '/') AS name", {'id': pid})
    majority = []
    for c in rows('MATCH (c:Component) WHERE c.valid_to IS NULL RETURN c.id AS id, labels(c) AS labels, c.name AS name'):
        risk = _component_risk(rows, c['id'])
        share = next((h['share'] for h in risk['holders'] if h['id'] == pid), 0)
        if share > 0.5: majority.append(dict(c, share=share, bus_factor=risk['bus_factor']))
    return {'person_id': pid, 'if_they_leave': {'components_without_owner': only_owner, 'open_tasks_without_assignee': open_tasks,
        'directories_without_expert': only_expert, 'components_where_they_hold_majority': majority},
        'summary': {'components_without_owner': len(only_owner), 'open_tasks_without_assignee': len(open_tasks),
                    'directories_without_expert': len(only_expert), 'majority_components': len(majority)}}


# ---------------------------------------------------------------- action_status

def action_status(rows, db, action_id):
    """Does an action's approval still hold? Approval binds a payload whose evidence must still be current."""
    from .company.core import digest
    from .company.brief import validate_brief
    found = rows('MATCH (a:Action {id:$id}) RETURN properties(a) AS a', {'id': action_id})
    if not found: raise TruthError('Action not found')
    a = found[0]['a']; payload = json.loads(a['payload_json'])
    intact = digest(payload) == a['payload_hash']
    validation = validate_brief(db, payload['brief'])
    changed = []
    for ident in a.get('evidence_ids') or []:
        rec = _record(rows, ident)
        if rec is not None and (rec['valid_to'] or rec['deleted']):
            changed.append({'id': ident, 'labels': rec['labels'], 'name': rec['name'], 'change': 'retired', 'at': rec['valid_to']})
        elif rec is not None and a.get('approved_at'):
            edit = rows('MATCH (r:SourceRevision {source_unit_id:$id}) WHERE r.observed_at > $at RETURN min(r.observed_at) AS at',
                        {'id': ident, 'at': a['approved_at']})
            if edit and edit[0]['at']:
                changed.append({'id': ident, 'labels': rec['labels'], 'name': rec['name'], 'change': 'edited', 'at': edit[0]['at']})
    holds = intact and validation['valid'] and not changed
    status = a['status']
    if status == 'approved': verdict = 'APPROVAL_HOLDS' if holds else 'APPROVAL_STALE'
    elif status == 'executed': verdict = 'EXECUTED_EVIDENCE_CURRENT' if holds else 'EXECUTED_EVIDENCE_CHANGED'
    else: verdict = status.upper()
    return {'action_id': action_id, 'status': status, 'verdict': verdict, 'payload_intact': intact,
        'brief_valid_now': validation['valid'], 'brief_errors': validation.get('errors', [])[:10], 'evidence_changed': changed,
        'approved_by': a.get('approved_by'), 'approved_at': a.get('approved_at'), 'executed_at': a.get('executed_at'),
        'external_id': a.get('external_id'),
        'meaning': 'An approval covers one exact payload and the evidence it cites. If any cited evidence is retired or edited, '
                   'the approval no longer holds and execute refuses; prepare and approve a new action.'}
