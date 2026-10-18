"""Which decision holds: computed SUPERSEDES edges between current decisions about the same target.

Two current decisions ABOUT the same target from different sources used to be reported as a
dispute with no answer. This pass ranks them by the authority of the source that asserts them
(a page or ADR outranks a Plane issue or comment, which outranks a Slack message), then by time,
and writes SUPERSEDES from each decision to the ones directly below it. Edges are COMPUTED, carry
a reason and an interval, and are closed (never deleted) when the ranking no longer derives them.
Decisions themselves are never retired or deleted here; superseded history stays readable.

Scope: decisions are only compared with decisions ABOUT the same target, so a decision about a
component never overrides one about a single work item under it. Decisions with equal authority
and time stay co-holders, and an explicit extracted SUPERSEDES between two decisions wins over
the computed ranking.
"""
from ..models.common import now_iso

RULE = 'source-authority-then-time-v1'
TIERS = {3: 'page', 2: 'plane record', 1: 'slack message', 0: 'other source'}


def authority(label, source):
    """Source authority tier: pages and ADRs, then Plane and GitHub records, then chat."""
    if label in ('DocPage', 'DocSection', 'KEP', 'KEPSection'): return 3
    if source == 'slack': return 1
    if label in ('Issue', 'Comment', 'Thread', 'PullRequest', 'Review'): return 2
    return 0


def _rank(db):
    rows = db.query(
        'MATCH (d:Decision)-[a:ABOUT]->(t) WHERE d.valid_to IS NULL AND a.valid_to IS NULL AND t.valid_to IS NULL '
        'AND coalesce(d.is_deleted, false) = false '
        'MATCH (u)-[s:ASSERTS]->(d) WHERE s.valid_to IS NULL AND u.valid_to IS NULL '
        'RETURN t.id, labels(t)[0], d.id, d.summary, u.id, labels(u)[0], u.source_system, '
        'coalesce(d.source_timestamp, u.source_updated_at, u.created_at, s.valid_from, d.observed_at)').result_set
    groups = {}
    for target, target_label, ident, summary, unit, label, source, at in rows:
        tier = authority(label, source)
        group = groups.setdefault(target, {'label': target_label, 'decisions': {}})
        best = group['decisions'].get(ident)
        # A decision asserted by several units takes its strongest source.
        if best is None or (tier, str(at or '')) > (best['tier'], best['at']):
            group['decisions'][ident] = {'id': ident, 'summary': summary, 'unit': unit, 'unit_label': label,
                                         'source': source, 'tier': tier, 'at': str(at or '')}
    return groups


def _reason(winner, loser):
    if winner['tier'] != loser['tier']:
        return 'source_authority', f"{TIERS[winner['tier']]} outranks {TIERS[loser['tier']]}"
    return 'recency', f"same authority ({TIERS[winner['tier']]}); newer: {winner['at']} after {loser['at']}"


def compute_supersession(db, now=None):
    """Write the current SUPERSEDES ranking and close computed edges it no longer derives."""
    now = now or now_iso()
    explicit = {(a, b) for a, b in db.query(
        "MATCH (a:Decision)-[r:SUPERSEDES]->(b:Decision) WHERE r.valid_to IS NULL AND coalesce(r.authority, '') <> 'COMPUTED' "
        'RETURN a.id, b.id').result_set}
    wanted = {}
    for target, group in _rank(db).items():
        ranked = sorted(group['decisions'].values(), key=lambda d: (d['tier'], d['at'], d['id']), reverse=True)
        tiers = []
        for d in ranked:
            if tiers and (tiers[-1][0]['tier'], tiers[-1][0]['at']) == (d['tier'], d['at']): tiers[-1].append(d)
            else: tiers.append([d])
        # Each rank supersedes the rank directly below it, so the chain can be walked from the holder.
        for upper, lower in zip(tiers, tiers[1:]):
            for w in upper:
                for l in lower:
                    if (l['id'], w['id']) in explicit or (w['id'], l['id']) in explicit: continue
                    basis, reason = _reason(w, l)
                    wanted[(w['id'], l['id'])] = {'scope_id': target, 'scope_label': group['label'], 'basis': basis,
                        'reason': reason, 'effective_at': w['at'], 'winner_unit': w['unit'], 'loser_unit': l['unit']}
    current = {(a, b): (ident, scope, reason) for a, b, ident, scope, reason in db.query(
        "MATCH (a:Decision)-[r:SUPERSEDES]->(b:Decision) WHERE r.valid_to IS NULL AND r.authority = 'COMPUTED' "
        'RETURN a.id, b.id, r.id, r.scope_id, r.reason').result_set}
    stats = {'edges': len(wanted), 'created': 0, 'closed': 0, 'unchanged': 0}
    kept = set()
    for pair, (ident, scope, reason) in current.items():
        want = wanted.get(pair)
        if want and (want['scope_id'], want['reason']) == (scope, reason):
            kept.add(pair); stats['unchanged'] += 1; continue
        db.query('MATCH ()-[r:SUPERSEDES {id:$id}]->() SET r.valid_to = $now', {'id': ident, 'now': now})
        stats['closed'] += 1
    for (a, b), props in wanted.items():
        if (a, b) in kept: continue
        logical = f'{a}->SUPERSEDES->{b}'
        db.query('MATCH (a:Decision {id:$a}), (b:Decision {id:$b}) CREATE (a)-[r:SUPERSEDES]->(b) SET r = $props',
                 {'a': a, 'b': b, 'props': {**props, 'id': f'{logical}@{now}', 'logical_id': logical, 'authority': 'COMPUTED',
                                            'rule': RULE, 'valid_from': now, 'valid_to': None, 'computed_at': now,
                                            'source_system': 'derived'}})
        stats['created'] += 1
    return stats
