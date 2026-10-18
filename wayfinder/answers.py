"""Saved answers that invalidate themselves.

An Answer node keeps the question, the exact statements and the verdict verify_answer gave
them, with CITES edges to each cited record. Every refresh re-verifies the saved answers; one
that was VERIFIED and no longer is gets marked stale with the reason, so nobody keeps reusing
an answer whose sources moved on.
"""
import json
import re
from .models.common import now_iso
from .truth import TruthError, verify_answer

# Worst first: an answer is only as good as its weakest statement.
SEVERITY = ('UNSUPPORTED', 'REMOVED', 'OUT_OF_DATE', 'PARTLY_OUT_OF_DATE', 'DISPUTED', 'RECITE')
FIELDS = ('a.id AS id, a.question AS question, a.statements_json AS statements, a.verdict AS verdict, '
          'a.statement_statuses AS statuses, a.saved_at AS saved_at, a.checked_at AS checked_at, '
          'coalesce(a.stale, false) AS stale, a.stale_since AS stale_since, a.stale_reason AS stale_reason, '
          'a.evidence_ids AS evidence_ids')


def overall(result):
    statuses = [s['status'] for s in result['statements']]
    return next((s for s in SEVERITY if s in statuses), 'VERIFIED')


def reasons(result):
    """One line per statement that is not VERIFIED, naming what changed."""
    out = []
    for i, s in enumerate(result['statements'], 1):
        if s['status'] == 'VERIFIED': continue
        parts = []
        for e in s['evidence']:
            if e.get('status') in ('OUT_OF_DATE', 'REMOVED', 'RETIRED'):
                parts.append(f"{e['id']} {e['status'].lower().replace('_', ' ')}" + (f" at {e['retired_at']}" if e.get('retired_at') else ''))
            elif e.get('status') == 'NOT_FOUND': parts.append(f"{e['id']} not found")
        if s['replaced_by']: parts.append('now replaced by ' + ', '.join(r['id'] for r in s['replaced_by']))
        if s['quotes_not_in_current_sources']: parts.append(f"{len(s['quotes_not_in_current_sources'])} quote(s) no longer in the source")
        if s['conflicting_current_decisions']: parts.append('conflicts with ' + ', '.join(d['id'] for d in s['conflicting_current_decisions']))
        if not s['evidence']: parts.append('no evidence cited')
        out.append(f"Statement {i} is {s['status']}" + (': ' + '; '.join(parts) if parts else ''))
    return out


def _clean(question, statements):
    question = str(question or '').strip()
    if not 1 <= len(question) <= 1000: raise TruthError('question must be 1..1000 characters')
    if not isinstance(statements, list): raise TruthError('statements must be a list')
    return question, [{'text': str(s.get('text', '')), 'evidence_ids': [str(i) for i in s.get('evidence_ids', [])][:20]}
                      for s in statements if isinstance(s, dict)]


def save_answer(db, rows, question, statements, now=None):
    """Verify the statements, then persist them as an Answer citing its evidence. Saving the same answer again re-verifies it."""
    from .company.core import digest
    question, clean = _clean(question, statements)
    result = verify_answer(rows, clean)
    now = now or now_iso(); verdict = overall(result)
    ident = 'answer:' + digest({'question': question, 'statements': clean})[:16]
    evidence = sorted({i for s in clean for i in s['evidence_ids']})
    props = {'question': question, 'title': question[:120], 'statements_json': json.dumps(clean, ensure_ascii=False),
             'evidence_ids': evidence, 'verdict': verdict, 'statement_statuses': [s['status'] for s in result['statements']],
             'checked_at': now, 'stale': False, 'stale_since': None, 'stale_reason': None,
             'source_system': 'wayfinder', 'authority': 'COMPUTED'}
    db.query('MERGE (a:Answer {id:$id}) ON CREATE SET a.saved_at=$now SET a += $props', {'id': ident, 'now': now, 'props': props})
    # Relationship IDs and unknown IDs stay in evidence_ids; only nodes get a CITES edge.
    cited = [r[0] for r in db.query('UNWIND $ids AS e MATCH (a:Answer {id:$id}), (n {id:e}) WHERE NOT n:Answer '
                                    'MERGE (a)-[c:CITES]->(n) ON CREATE SET c.id=$id+"->CITES->"+e, c.valid_from=$now '
                                    'RETURN n.id', {'id': ident, 'ids': evidence, 'now': now}).result_set]
    return {'answer_id': ident, 'question': question, 'verdict': verdict, 'saved_at': now, 'cited': sorted(cited),
            'not_cited_as_nodes': [i for i in evidence if i not in cited], 'reasons': reasons(result),
            'statements': [{'text': s['text'], 'status': s['status']} for s in result['statements']],
            'meaning': 'Saved. Every refresh re-verifies this answer; if it stops being VERIFIED it is marked stale with the '
                       'reason. Check it later with answer_status.'}


def _saved(rows, answer_id=None, limit=200):
    found = rows(f'MATCH (a:Answer) WHERE $id IS NULL OR a.id=$id RETURN {FIELDS} ORDER BY a.saved_at DESC LIMIT $limit',
                 {'id': answer_id, 'limit': limit})
    if answer_id and not found: raise TruthError('Answer not found')
    return found


def _recheck(rows, a):
    result = verify_answer(rows, json.loads(a['statements']))
    now = overall(result)
    return {'answer_id': a['id'], 'question': a['question'], 'saved_verdict': a['verdict'], 'verdict_now': now,
            'changed': now != a['verdict'], 'stale': a['stale'], 'stale_since': a['stale_since'],
            'saved_at': a['saved_at'], 'last_checked_at': a['checked_at'], 'reasons': reasons(result),
            'statuses_now': [s['status'] for s in result['statements']], 'evidence_ids': a['evidence_ids'] or [],
            '_result': result}


def answer_status(rows, answer_id=None):
    """Re-verify saved answers against the current graph without writing. Lists those whose verdict changed."""
    checks = [_recheck(rows, a) for a in _saved(rows, answer_id)]
    if answer_id:
        c = checks[0]; result = c.pop('_result')
        return {**c, 'statements': result['statements'], 'meaning': result['meaning']}
    for c in checks: c.pop('_result')
    changed = [c for c in checks if c['changed']]
    return {'checked': len(checks), 'changed': changed,
            'stale': [{k: c[k] for k in ('answer_id', 'question', 'verdict_now', 'stale_since')} for c in checks if c['stale'] or
                      (c['saved_verdict'] == 'VERIFIED' and c['verdict_now'] != 'VERIFIED')],
            'summary': {'checked': len(checks), 'changed': len(changed),
                        'verified_now': sum(c['verdict_now'] == 'VERIFIED' for c in checks)},
            'meaning': 'changed lists saved answers whose verdict differs from the one recorded at save or at the last refresh. '
                       'Refresh marks an answer stale when it stops being VERIFIED; this tool only reads.'}


def mark_stale(db, rows, now=None):
    """Re-verify every saved answer and record the result. Returns the answers that just went stale."""
    now = now or now_iso(); newly, recovered, still = [], [], 0
    for a in _saved(rows, limit=1000):
        c = _recheck(rows, a); c.pop('_result')
        props = {'verdict': c['verdict_now'], 'statement_statuses': c['statuses_now'], 'checked_at': now}
        if c['verdict_now'] == 'VERIFIED':
            props.update(stale=False, stale_since=None, stale_reason=None)
            if a['stale']: recovered.append(c['answer_id'])
        elif a['verdict'] == 'VERIFIED' or a['stale']:
            reason = '; '.join(c['reasons'])
            props.update(stale=True, stale_reason=reason, stale_since=a['stale_since'] or now)
            if a['stale']: still += 1
            else: newly.append({**{k: c[k] for k in ('answer_id', 'question', 'saved_verdict', 'verdict_now')}, 'reason': reason})
        db.query('MATCH (a:Answer {id:$id}) SET a += $props', {'id': a['id'], 'props': props})
    return {'checked_at': now, 'newly_stale': newly, 'recovered': recovered, 'still_stale': still}


def stale_answers(rows, limit=50):
    """Saved answers as the last refresh left them, stale first."""
    found = rows(f'MATCH (a:Answer) RETURN {FIELDS} ORDER BY coalesce(a.stale,false) DESC, a.stale_since DESC, a.saved_at DESC LIMIT $limit',
                 {'limit': limit})
    for a in found: a.pop('statements')
    return {'items': found, 'stale': sum(a['stale'] for a in found), 'saved': len(found)}


# Plane keys and URLs in a chat message become graph links on the next sync; keep them out.
KEYLIKE = re.compile(r'\b([A-Za-z][A-Za-z0-9]*)-(\d+)\b')


def notice(item):
    text = (f"Saved answer went stale ({item['saved_verdict']} -> {item['verdict_now']}).\n"
            f"Question: {item['question']}\nWhy: {item['reason']}\nAnswer ID: {item['answer_id']}")
    text = re.sub(r'https?://\S+', '[link removed]', text)
    return KEYLIKE.sub(lambda m: f'{m.group(1)}‑{m.group(2)}', text)
