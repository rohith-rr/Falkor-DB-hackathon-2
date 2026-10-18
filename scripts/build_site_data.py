"""Export scrubbed static data for the public site (site/data/*.json). Run: python scripts/build_site_data.py

Reads the local live graph and committed evidence; writes only readable aliases (FALKORDBTE-15, PR #4,
branch names, short SHAs, page slugs). No native Plane/Slack IDs, private workspace URLs or account
names leave this script: only github.com links are kept, and people's names become roles.
Re-run after a refresh; makes no model calls.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wayfinder.db import DatabaseManager
from wayfinder.company.core import load_manifest
from wayfinder.brain_graph import BrainGraph
from wayfinder.graph_algorithms import project, run as run_algorithm, DEFAULT_LABELS

GRAPH = 'wayfinder_company_live_demo'
LIVE = ROOT / 'data/live-workspace'
OUT = ROOT / 'site/data'
WORK = ['Issue', 'PullRequest', 'Commit', 'Branch', 'Comment', 'DocPage', 'Thread']
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
PRIVATE_URL = re.compile(r'https?://(?:app\.plane\.so|[\w-]+\.slack\.com|app\.slack\.com)/\S*')


class Scrubber:
    def __init__(self, db, manifest):
        self.db = db; self.org = manifest['organization_id']
        self.names = sorted({p['name'] for p in manifest.get('people', []) if p.get('name')}, key=len, reverse=True)
        seed = json.loads((LIVE / 'seed-state.json').read_text(encoding='utf-8'))['created']
        self.pages = {v['id']: k.split(':', 1)[1] for k, v in seed.items() if k.startswith('page:')}
        self.aliases = {}; self.counters = {}

    def text(self, value, limit=None):
        value = str(value or '')
        for name in self.names: value = value.replace(name, 'the owner')
        value = PRIVATE_URL.sub('[workspace link]', value)
        value = re.sub(r'<\[workspace link\]\|([^>]+)>', r'\1', value)
        value = re.sub(r'\bU[0-9A-Z]{8,12}\b', 'a Slack user', UUID.sub('…', value))
        # Slack channel/team/bot IDs (uppercase, with digits) identify the workspace.
        value = re.sub(r'\b[CTBWG](?=[0-9A-Z]*\d)[0-9A-Z]{8,12}\b', 'Slack channel', value)
        value = ' '.join(value.split())
        return value[:limit] + ('…' if limit and len(value) > limit else '') if limit else value

    def next(self, kind):
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return f'{kind}-{self.counters[kind]}'

    def alias(self, ident, label, props):
        if ident in self.aliases: return self.aliases[ident]
        tail = ident.rsplit(':', 1)[-1]
        if label == 'Issue' and props.get('key'): a = props['key']
        elif label == 'PullRequest': a = f"PR #{props.get('number')}"
        elif label == 'Branch': a = f"branch {props.get('name')}"
        elif label == 'Commit': a = f"commit {props.get('short_sha') or tail[:7]}"
        elif label == 'Component': a = tail
        elif label == 'Person':
            a = 'owner' if ident.startswith(f'company:{self.org}:person:') else (
                f"github {props.get('login')}" if ident.startswith('github:') else self.next('slack-person'))
        elif label == 'DocPage': a = f"page {self.pages.get(tail, tail[:6])}"
        elif label == 'DocSection':
            page = ident.split(':section:')[0].rsplit(':', 1)[-1]; a = f"page {self.pages.get(page, page[:6])} §{ident.rsplit(':', 1)[-1]}"
        elif label == 'Repository': a = 'repository'
        elif label == 'Directory': a = f"dir {props.get('path') or '/'}"
        elif label == 'Comment': a = self.next('slack-msg' if ident.startswith('slack:') else 'github-comment' if ident.startswith('github:') else 'plane-comment')
        elif label == 'Thread': a = self.next('slack-thread' if ident.startswith('slack:') else 'thread')
        else: a = self.next(label.lower())
        self.aliases[ident] = a
        return a

    def node(self, ident):
        row = self.db.graph.ro_query('MATCH (n {id:$id}) RETURN labels(n)[0], properties(n)', params={'id': ident}).result_set
        if not row: return None
        label, props = row[0]
        props = {k: v for k, v in props.items() if 'embedding' not in k}
        name = props.get('title') or props.get('name') or props.get('summary') or props.get('display_name') or props.get('path') or ''
        if label == 'Person': name = {'owner': 'Project owner'}.get(self.alias(ident, label, props), 'Slack bot' if props.get('is_bot') else name)
        if label == 'Thread': name = 'Slack thread' if ident.startswith('slack:') else 'Comment thread'
        url = props.get('source_url') or ''
        return {'id': self.alias(ident, label, props), 'label': label, 'name': self.text(name, 90) or self.alias(ident, label, props),
                'source': props.get('source_system'), 'snippet': self.text(props.get('text_content') or props.get('body_text'), 320),
                'url': url if url.startswith('https://github.com/') else None,
                **({'state': props['state']} if props.get('state') else {}), **({'quote': self.text(props['evidence_quote'], 240)} if props.get('evidence_quote') else {})}


def truth_data(db, s, facts):
    """Real outputs of the truth-maintenance tools, with aliases in place of IDs."""
    from datetime import datetime, timedelta, timezone
    brain = BrainGraph(db)
    def ref(ident):
        n = s.node(ident)
        return {'id': n['id'], 'label': n['label'], 'name': n['name']} if n else None
    decision = facts['action']['decision']; quote = decision.get('evidence_quote') or ''
    found = db.graph.ro_query('MATCH (d:Decision {summary:$s}) WHERE d.valid_to IS NULL RETURN d.id LIMIT 1', params={'s': decision['summary']}).result_set
    decision = dict(decision, evidence_ids=[found[0][0]] if found else [])
    phrase = ' '.join(quote.split()[1:9]) if quote.lower().startswith('decision') else ' '.join(quote.split()[:8])
    retired = db.graph.ro_query('MATCH (u)-[a:ASSERTS]->(c) WHERE a.valid_to IS NOT NULL RETURN c.id, c.summary LIMIT 1').result_set
    statements = [{'text': f'The team requires a person to approve: "{phrase}".', 'evidence_ids': decision['evidence_ids']},
                  {'text': 'The decision says "agents may create tasks without review".', 'evidence_ids': decision['evidence_ids']}]
    if retired: statements.append({'text': f'An open question: {retired[0][1]}', 'evidence_ids': [retired[0][0]]})
    statements.append({'text': 'The extraction pilot finished last month.', 'evidence_ids': []})
    verified = brain.verify_answer(statements)['statements']
    verify = [{'text': s.text(v['text'], 200), 'status': v['status'], 'evidence': [ref(e['id']) for e in v['evidence'] if ref(e['id'])],
               'replaced_by': [ref(r['id']) for r in v['replaced_by'] if ref(r['id'])], 'missing_quotes': v['quotes_not_in_current_sources']}
              for v in verified]
    components = db.graph.ro_query('MATCH (c:Component) WHERE c.valid_to IS NULL RETURN c.id ORDER BY c.id').result_set
    risk = []
    for (cid,) in components:
        r = brain.knowledge_risk(cid)
        risk.append({'component': ref(cid), 'bus_factor': r['bus_factor'], 'risk': r['risk'],
                     'holders': [{'name': ref(h['id'])['name'], 'share': h['share'], 'roles': h['roles']} for h in r['holders']]})
    stale = [{'page': ref(i['page']['id']), 'status': i['status'], 'last_edited': i['last_edited'],
              'since': [ref(x['id']) for x in (i['code_merged_since'] + i['tasks_updated_since'])[:4]]}
             for i in brain.stale_documents(limit=10)['items']]
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    ch = brain.changes_since(since, limit=6)
    changes = {'since': since, 'summary': ch['summary'],
               'examples': [dict(ref(x['id']), change=x['change']) for x in (ch['edited'] + ch['created'] + ch['retired'])[:8] if ref(x['id'])]}
    action = db.graph.ro_query('MATCH (a:Action) RETURN a.id LIMIT 1').result_set
    status = brain.action_status(action[0][0]) if action else None
    return {'verify': verify, 'risk': risk, 'stale': stale, 'changes': changes,
            'action': {'verdict': status['verdict'], 'evidence_changed': len(status['evidence_changed'])} if status else None}


def main():
    db = DatabaseManager(graph_name=GRAPH); manifest = load_manifest(LIVE / 'manifest.json'); s = Scrubber(db, manifest)
    OUT.mkdir(exist_ok=True)

    # Graph: current records only, the same projection the algorithms use.
    nodes, edges = project(db, DEFAULT_LABELS, None)
    info = {i: s.node(i) for i in nodes}
    graph = {'nodes': [n for n in info.values() if n],
             'edges': [{'s': info[a]['id'], 't': info[b]['id'], 'type': t} for a, t, b in edges if info.get(a) and info.get(b)]}

    # Native algorithms over current records.
    def ids(items): return [dict(info[i['id']], score=i.get('score')) for i in items if i['id'] in info]
    pagerank = run_algorithm(db, 'pagerank', limit=10)
    brokers = run_algorithm(db, 'betweenness', node_labels=WORK, limit=10)
    components = run_algorithm(db, 'components', limit=6)
    lookup = lambda q, **p: (db.graph.ro_query(q, params=p).result_set or [[None]])[0][0]
    announce = lookup("CALL db.idx.fulltext.queryNodes('Comment','Branch commit linking merged') YIELD node RETURN node.id LIMIT 1")
    paths = []
    for title, src, dst, labels in (
        ('Slack announcement to the branch that carried the work', announce,
         f"github:{manifest['organization_id']}:branch:{manifest['github']['repository']}:git-history", WORK),
        ('Agent Review Workflow to the task the approved agent action created', f"company:{manifest['organization_id']}:component:agent-workflows",
         lookup("MATCH (i:Issue {key:'FALKORDBTE-16'}) WHERE i.valid_to IS NULL RETURN i.id"), None),
        ('FALKORDBTE-15 to the commit that implemented it', lookup("MATCH (i:Issue {key:'FALKORDBTE-15'}) WHERE i.valid_to IS NULL RETURN i.id"),
         lookup("MATCH (c:Commit) WHERE c.sha STARTS WITH '55085d4' RETURN c.id"), WORK)):
        if not (src and dst): continue
        result = run_algorithm(db, 'shortest_path', node_labels=labels, source_id=src, target_id=dst, max_hops=6)
        if result['items']:
            paths.append({'title': title, 'nodes': [info[i['id']]['id'] for i in result['items']],
                          'edges': [{'s': info[r['source_id']]['id'], 't': info[r['target_id']]['id'], 'type': r['type']} for r in result['relationships']]})
    algorithms = {'pagerank': ids(pagerank['items']), 'betweenness': ids(brokers['items']), 'paths': paths,
        'components': {'count': components['component_count'], 'singletons': components['singletons'],
                       'groups': [{'size': g['size'], 'members': [info[m['id']]['id'] for m in g['members'] if m['id'] in info]} for g in components['items']]},
        'projection': pagerank['projection']}

    # Benchmark: re-run the graph strategy with each tool call recorded, so the site can draw its path.
    sys.path.insert(0, str(ROOT / 'benchmarks/wayfinder_bench'))
    import run as bench
    spec = json.loads((ROOT / 'benchmarks/wayfinder_bench/questions.json').read_text(encoding='utf-8'))
    results = json.loads((ROOT / 'benchmarks/wayfinder_bench/results.json').read_text(encoding='utf-8'))
    strategy = bench.GraphStrategy(db); refs = bench.Refs(db, manifest)
    def node_alias(ident):
        if ident not in info: info[ident] = s.node(ident)
        return info[ident]['id'] if info.get(ident) else '?'
    walks = []
    for q in spec['questions']:
        calls = []; original = strategy.tool
        def tool(name, **kw):
            result = original(name, **kw)
            step = {'tool': name}
            if name == 'read_cypher':
                step['args'] = {'query': kw['query'], 'parameters': kw.get('parameters')}
                step['found'] = [node_alias(v) for row in result['items'] for v in row.values() if isinstance(v, str) and ':' in v]
            else:
                step['args'] = {'from': node_alias(kw['entity_id']), 'direction': kw.get('direction'), 'types': kw.get('relationship_types')}
                step['edges'] = [{'s': node_alias(x['r']['source_id']), 't': node_alias(x['r']['target_id']), 'type': x['r']['type']} for x in result['items']]
            calls.append(step); return result
        strategy.tool = tool
        answer = strategy.answer(q); strategy.tool = original
        gold = [node_alias(refs.to_id(r)) for r in q['gold']]
        verdict = {name: next(r for r in rows if r['id'] == q['id']) for name, rows in results['results'].items()}
        walks.append({'id': q['id'], 'type': q['type'], 'question': s.text(q['question']), 'answerable': q['answerable'],
            'graph_answer': [node_alias(i) for i in answer], 'gold': gold, 'calls': calls,
            'scores': {name: {'f1': v['f1'], 'correct': v['correct'], 'predicted': [s.text(p, 70) for p in v['predicted']]} for name, v in verdict.items()}})
    bench_out = {'summary': results['summary'], 'questions': results['questions'], 'answerable': results['answerable'],
        'corpus_records': results['corpus_records'], 'embedding_model': results['embedding_model'], 'run_at': results['run_at'], 'walks': walks,
        # Every node a walk touches, so the page can name nodes outside the overview graph.
        'nodes': {n['id']: n for n in info.values() if n}}

    # Facts from the committed evidence and the graph.
    ev = lambda name: json.loads((ROOT / 'docs/evidence' / name).read_text(encoding='utf-8'))
    probe = ev('live-change-probe-watch.json'); action = ev('plane-action-live.json')
    count = lambda q: db.graph.ro_query(q).result_set[0][0]
    facts = {
        'sources': {'plane_items': count("MATCH (n:Issue) WHERE n.source_system='plane' AND n.valid_to IS NULL RETURN count(n)"),
            'plane_pages': count("MATCH (n:DocPage) WHERE n.valid_to IS NULL RETURN count(n)"),
            'plane_comments': count("MATCH (n:Comment) WHERE n.source_system='plane' AND n.valid_to IS NULL RETURN count(n)"),
            'slack_messages': count("MATCH (n:Comment) WHERE n.source_system='slack' AND n.valid_to IS NULL RETURN count(n)"),
            'pull_requests': count("MATCH (n:PullRequest) WHERE n.valid_to IS NULL RETURN count(n)"),
            'branches': count("MATCH (n:Branch) WHERE n.valid_to IS NULL RETURN count(n)"),
            'commits': count("MATCH (n:Commit) WHERE n.valid_to IS NULL RETURN count(n)")},
        'links': {m: count(f"MATCH ()-[r:REFERENCES]->() WHERE r.valid_to IS NULL AND r.link_method='{m}' RETURN count(r)") for m in ('EXACT_REFERENCE', 'EXACT_IDENTIFIER')},
        'history': {'source_revisions': count('MATCH (r:SourceRevision) RETURN count(r)'),
            'retired_relationships': count('MATCH ()-[r]->() WHERE r.valid_to IS NOT NULL RETURN count(r)'),
            'current_relationships': count('MATCH ()-[r]->() WHERE r.valid_to IS NULL RETURN count(r)')},
        'claims': {r[0]: r[1] for r in db.graph.ro_query("MATCH ()-[a:ASSERTS]->(c) WHERE a.valid_to IS NULL AND a.authority='EXTRACTED' RETURN labels(c)[0], count(c)").result_set},
        'freshness': {'checks': sum(len(st['checks']) for st in probe['steps']), 'passed': sum(sum(st['checks'].values()) for st in probe['steps']),
            'stages': [{'stage': st['stage'], 'seconds': sorted(st['seconds_until_visible'].values())} for st in probe['steps']]},
        'action': {'task': action['task']['plane_key'], 'title': action['task']['title'], 'hash': action['action']['payload_hash'],
            'status': action['action']['status'], 'idempotency': action['idempotency'], 'readback': action['readback'],
            'decision': action['brief']['decisions'][0]},
    }
    truth = truth_data(db, s, facts)
    for name, value in (('graph', graph), ('algorithms', algorithms), ('bench', bench_out), ('facts', facts), ('truth', truth)):
        (OUT / f'{name}.json').write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(json.dumps({'nodes': len(graph['nodes']), 'edges': len(graph['edges']), 'paths': len(paths), 'walks': len(walks), 'facts': facts['sources']}, indent=1))


if __name__ == '__main__': main()
