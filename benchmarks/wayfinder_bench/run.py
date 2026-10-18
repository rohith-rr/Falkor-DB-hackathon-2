"""Run Wayfinder-Bench: graph tools vs vector search vs BM25 full-text on the same fixed questions.

- graph: a fixed plan per question type over the MCP tools (read_cypher to resolve the anchor,
  get_neighbors along typed relationships). It stands in for an agent's planner; no LLM is used.
- vector: every current work/discussion/code record's text is embedded with the configured model;
  the question's top 5 by cosine similarity are returned.
- bm25: the same records and text, ranked by BM25; top 5.

Scoring is set precision/recall/F1 against gold entity sets. An unanswerable question is correct
only when nothing is returned. Writes results.json and RESULTS.md next to this file (readable
references only), and caches embeddings under the ignored data/ folder.
"""
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wayfinder.db import DatabaseManager
from wayfinder.brain_graph import BrainGraph, BrainError
from wayfinder.config import settings
from wayfinder.company.core import load_manifest
from wayfinder.extraction.llm_client import OpenAPIClient

HERE = Path(__file__).parent
LIVE = ROOT / 'data/live-workspace'
CACHE = ROOT / 'data/bench-embeddings.json'
CORPUS_LABELS = ['Person', 'Component', 'Issue', 'DocPage', 'Comment', 'PullRequest', 'Branch', 'Commit']
TOP_K = 5


class Refs:
    """Map readable gold references to graph IDs and back."""
    def __init__(self, db, manifest):
        self.db = db; self.org = manifest['organization_id']; self.repo = manifest['github']['repository']
        self.seed = json.loads((LIVE / 'seed-state.json').read_text(encoding='utf-8'))['created']
        self.reverse = {}
        # Committed results must not carry account names quoted inside message text.
        self.names = [p['name'] for p in manifest.get('people', []) if p.get('name')]

    def to_id(self, ref):
        (kind, value), = ref.items()
        if kind == 'person': ident = f'company:{self.org}:person:{value}'
        elif kind == 'issue':
            rows = self.db.graph.ro_query("MATCH (i:Issue {key:$k}) WHERE i.source_system='plane' AND i.valid_to IS NULL RETURN i.id",
                params={'k': value}).result_set
            ident = rows[0][0] if rows else f'missing-issue:{value}'
        elif kind == 'page': ident = f"plane:{self.org}:page:{self.seed['page:' + value]['id']}"
        elif kind == 'slack':
            record = self.seed['slack:' + value]; ident = f"slack:{self.org}:comment:{record['channel']}:{record['id']}"
        elif kind == 'branch': ident = f'github:{self.org}:branch:{self.repo}:{value}'
        elif kind == 'commit': ident = f'github:{self.org}:commit:{self.repo}:{value}'
        elif kind == 'pr': ident = f'github:{self.org}:pr:{self.repo}:{value}'
        else: raise ValueError(kind)
        self.reverse[ident] = f'{kind}:{value}'
        return ident

    def readable(self, ident):
        if ident in self.reverse: return self.reverse[ident]
        row = self.db.graph.ro_query("MATCH (n {id:$id}) RETURN labels(n)[0], coalesce(n.key,n.number,n.name,n.short_sha,n.title,"
            "substring(coalesce(n.text_content,n.body_text,''),0,40))",
            params={'id': ident}).result_set
        if not row: return 'unknown'
        text = str(row[0][1])
        for name in self.names: text = text.replace(name, '<owner>')
        return f'{row[0][0]}:{text}'


# ---------------------------------------------------------------- graph strategy

class GraphStrategy:
    def __init__(self, db):
        self.brain = BrainGraph(db); self.calls = 0

    def tool(self, name, **kw):
        self.calls += 1
        return self.brain.invoke(name, **kw)

    def one(self, query, **params):
        rows = self.tool('read_cypher', query=query, parameters=params, row_limit=5)['items']
        return next(iter(rows[0].values())) if rows else None

    def resolve(self, anchor, kind):
        if kind == 'issue':
            return self.one("MATCH (i:Issue {key:$k}) WHERE i.source_system='plane' AND i.valid_to IS NULL RETURN i.id LIMIT 1", k=anchor)
        if kind == 'component':
            return self.one('MATCH (c:Component) WHERE toLower(c.name)=toLower($n) AND c.valid_to IS NULL RETURN c.id LIMIT 1', n=anchor)
        if kind == 'pr':
            return self.one('MATCH (p:PullRequest {number:$n}) WHERE p.valid_to IS NULL RETURN p.id LIMIT 1', n=int(anchor.lstrip('#')))
        if kind == 'message':
            # Full-text requires every remaining word, so a phrase that was never said resolves to nothing.
            words = ' '.join(w for w in re.findall(r'[A-Za-z0-9]+', anchor) if len(w) > 2)
            return self.one("CALL db.idx.fulltext.queryNodes('Comment', $q) YIELD node, score "
                "WHERE node.valid_to IS NULL AND node.source_system='slack' RETURN node.id ORDER BY score DESC LIMIT 1", q=words)
        if kind == 'decision_scope':
            # A decision question names its component; anything else has no component to look under.
            return self.resolve(anchor, 'component')
        raise ValueError(kind)

    def hop(self, ident, direction, types, labels=None, source=None):
        # Follow next_cursor in small pages, as the tool contract asks; one large page can exceed the response bound.
        # On RESULT_TOO_LARGE, halve the page size, as the error message instructs.
        found, cursor, limit = [], None, 10
        while True:
            try:
                page = self.tool('get_neighbors', entity_id=ident, direction=direction, relationship_types=types,
                                 labels=labels or [], limit=limit, cursor=cursor)
            except BrainError as exc:
                if 'RESULT_TOO_LARGE' not in str(exc) or limit == 1: raise
                limit = max(1, limit // 2); continue
            found += [x['m']['id'] for x in page['items'] if source is None or x['m']['properties'].get('source_system') == source]
            cursor = page.get('next_cursor')
            if not cursor: return found

    def answer(self, q):
        intent, anchor = q['intent'], q['anchor']
        plans = {
            'owner_of_component': ('component', lambda c: self.hop(c, 'in', ['OWNS_COMPONENT'], ['Person'])),
            'assignee_of_task': ('issue', lambda i: self.hop(i, 'out', ['ASSIGNED_TO'], ['Person'])),
            'depends_on': ('issue', lambda i: self.hop(i, 'out', ['DEPENDS_ON'], ['Issue'])),
            'tasks_in_component': ('component', lambda c: self.hop(c, 'in', ['CONCERNS_COMPONENT'], ['Issue'])),
            'pages_for_component': ('component', lambda c: self.hop(c, 'in', ['DESCRIBES'], ['DocPage'])),
            'links_in_message': ('message', lambda m: self.hop(m, 'out', ['REFERENCES'], ['Issue', 'DocPage'])),
            'messages_about_task': ('issue', lambda i: self.hop(i, 'in', ['REFERENCES'], ['Comment'], source='slack')),
            'branch_of_pr': ('pr', lambda p: self.hop(p, 'out', ['FROM_BRANCH'], ['Branch'])),
            'base_of_pr': ('pr', lambda p: self.hop(p, 'out', ['INTO_BRANCH'], ['Branch'])),
            'commits_of_pr': ('pr', lambda p: self.hop(p, 'out', ['HAS_COMMIT'], ['Commit'])),
            'prs_for_task': ('issue', lambda i: self.hop(i, 'both', ['REFERENCES'], ['PullRequest'])),
            'decision_source_for_component': ('decision_scope', self.decision_pages),
            'owner_of_task_component': ('issue', lambda i: sorted({p for c in self.hop(i, 'out', ['CONCERNS_COMPONENT'], ['Component'])
                                                                   for p in self.hop(c, 'in', ['OWNS_COMPONENT'], ['Person'])})),
            'branch_of_pr_linked_from_message': ('message', self.branch_via_message),
        }
        kind, plan = plans[intent]
        start = self.resolve(anchor, kind)
        return sorted(set(plan(start))) if start else []

    def decision_pages(self, component):
        # Component <-ABOUT- Decision <-ASSERTS- DocSection <-HAS_SECTION- DocPage
        pages = set()
        for decision in self.hop(component, 'in', ['ABOUT'], ['Decision']):
            for section in self.hop(decision, 'in', ['ASSERTS'], ['DocSection', 'DocPage']):
                pages.update(self.hop(section, 'in', ['HAS_SECTION'], ['DocPage']) if ':section:' in section else [section])
        return pages

    def branch_via_message(self, message):
        prs = self.hop(message, 'out', ['REFERENCES'], ['PullRequest'])
        if not prs:
            prs = [p for i in self.hop(message, 'out', ['REFERENCES'], ['Issue']) for p in self.hop(i, 'both', ['REFERENCES'], ['PullRequest'])]
        return [b for p in prs for b in self.hop(p, 'out', ['FROM_BRANCH'], ['Branch'])]


# ---------------------------------------------------------------- text baselines

def corpus(db):
    rows = db.graph.ro_query("MATCH (n) WHERE labels(n)[0] IN $labels AND n.valid_to IS NULL AND n.id IS NOT NULL "
        "AND (labels(n)[0] <> 'Comment' OR n.source_system IN ['plane','slack']) "
        "RETURN n.id, labels(n)[0], coalesce(n.key,''), coalesce(toString(n.number),''), coalesce(n.name,''), "
        "coalesce(n.display_name,''), coalesce(n.title,''), coalesce(n.text_content, n.body_text, '')",
        params={'labels': CORPUS_LABELS}).result_set
    docs = []
    for ident, label, key, number, name, display, title, text in rows:
        prefix = f'Pull request #{number}' if label == 'PullRequest' else label
        docs.append({'id': ident, 'text': ' '.join(p for p in (prefix, key, name, display, title, text[:1500]) if p)})
    return docs


TOKEN = re.compile(r'[a-z0-9]+')


class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.docs = docs; self.k1 = k1; self.b = b
        self.tokens = [TOKEN.findall(d['text'].lower()) for d in docs]
        self.avg = sum(map(len, self.tokens)) / len(self.tokens)
        df = {}
        for toks in self.tokens:
            for t in set(toks): df[t] = df.get(t, 0) + 1
        n = len(docs); self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def top(self, query, k=TOP_K):
        terms = TOKEN.findall(query.lower()); scores = []
        for d, toks in zip(self.docs, self.tokens):
            tf = {}
            for t in toks: tf[t] = tf.get(t, 0) + 1
            s = sum(self.idf.get(t, 0) * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * len(toks) / self.avg))
                    for t in terms if t in tf)
            scores.append((s, d['id']))
        return [i for s, i in sorted(scores, key=lambda x: (-x[0], x[1]))[:k] if s > 0]


class Vector:
    def __init__(self, docs):
        self.client = OpenAPIClient().embedding_client; self.model = settings.embedding_model
        cache = json.loads(CACHE.read_text(encoding='utf-8')) if CACHE.exists() else {}
        cache = cache if cache.get('model') == self.model else {'model': self.model, 'vectors': {}}
        self.cache = cache; self.requests = 0
        missing = [d['text'] for d in docs if self.key(d['text']) not in cache['vectors']]
        self.embed(missing)
        self.docs = [(d['id'], self.unit(cache['vectors'][self.key(d['text'])])) for d in docs]

    key = staticmethod(lambda text: hashlib.sha256(text.encode()).hexdigest())

    def embed(self, texts):
        for start in range(0, len(texts), 32):
            batch = texts[start:start + 32]
            result = self.client.embeddings.create(model=self.model, input=batch); self.requests += 1
            for text, item in zip(batch, result.data): self.cache['vectors'][self.key(text)] = item.embedding
        CACHE.parent.mkdir(parents=True, exist_ok=True); CACHE.write_text(json.dumps(self.cache), encoding='utf-8')

    @staticmethod
    def unit(v):
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def top(self, query, k=TOP_K):
        if self.key(query) not in self.cache['vectors']: self.embed([query])
        q = self.unit(self.cache['vectors'][self.key(query)])
        scored = sorted(((sum(a * b for a, b in zip(q, v)), i) for i, v in self.docs), key=lambda x: (-x[0], x[1]))
        return [i for _, i in scored[:k]]


# ---------------------------------------------------------------- scoring

def score(predicted, gold, answerable):
    p, g = set(predicted), set(gold)
    if not answerable:
        return {'correct': not p, 'precision': None, 'recall': None, 'f1': None}
    hit = len(p & g)
    precision = hit / len(p) if p else 0.0; recall = hit / len(g) if g else 0.0
    f1 = 2 * precision * recall / (precision + recall) if hit else 0.0
    return {'correct': p == g, 'precision': precision, 'recall': recall, 'f1': f1}


def summarize(rows):
    answerable = [r for r in rows if r['answerable']]; unanswerable = [r for r in rows if not r['answerable']]
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    by_type = {}
    for r in answerable: by_type.setdefault(r['type'], []).append(r['f1'])
    return {'answerable_f1': mean([r['f1'] for r in answerable]), 'answerable_recall': mean([r['recall'] for r in answerable]),
            'answerable_exact': mean([1.0 if r['correct'] else 0.0 for r in answerable]),
            'unanswerable_correct': mean([1.0 if r['correct'] else 0.0 for r in unanswerable]),
            'overall_correct': mean([1.0 if r['correct'] else 0.0 for r in rows]),
            'f1_by_type': {t: mean(v) for t, v in sorted(by_type.items())}}


def main():
    spec = json.loads((HERE / 'questions.json').read_text(encoding='utf-8'))
    manifest = load_manifest(LIVE / 'manifest.json'); db = DatabaseManager(graph_name=spec['graph'])
    refs = Refs(db, manifest); docs = corpus(db)
    graph, bm25 = GraphStrategy(db), BM25(docs)
    clock = time.monotonic(); vector = Vector(docs); embed_seconds = round(time.monotonic() - clock, 1)
    strategies = {'graph': graph.answer, 'vector': lambda q: vector.top(q['question']), 'bm25': lambda q: bm25.top(q['question'])}
    results = {name: [] for name in strategies}; timing = {name: 0.0 for name in strategies}
    for q in spec['questions']:
        gold = [refs.to_id(r) for r in q['gold']]
        for name, run in strategies.items():
            clock = time.monotonic()
            try: predicted = run(q); error = None
            except BrainError as exc: predicted = []; error = str(exc)
            timing[name] += time.monotonic() - clock
            row = {'id': q['id'], 'type': q['type'], 'answerable': q['answerable'], **score(predicted, gold, q['answerable']),
                   'predicted': [refs.readable(i) for i in predicted], 'gold': [refs.readable(i) for i in gold]}
            if error: row['error'] = error
            results[name].append(row)
    report = {'benchmark': spec['benchmark'], 'version': spec['version'], 'graph': spec['graph'],
        'run_at': datetime.now(timezone.utc).isoformat(), 'questions': len(spec['questions']),
        'answerable': sum(q['answerable'] for q in spec['questions']), 'top_k_for_text_baselines': TOP_K,
        'corpus_records': len(docs), 'embedding_model': settings.embedding_model, 'embedding_requests': vector.requests,
        'graph_tool_calls': graph.calls, 'llm_generation_requests': 0,
        'summary': {name: summarize(rows) for name, rows in results.items()},
        'seconds': {name: round(t, 2) for name, t in timing.items()}, 'embedding_seconds': embed_seconds, 'results': results}
    (HERE / 'results.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('questions', 'answerable', 'corpus_records', 'embedding_requests', 'graph_tool_calls', 'summary', 'seconds')}, indent=2))


if __name__ == '__main__': main()
