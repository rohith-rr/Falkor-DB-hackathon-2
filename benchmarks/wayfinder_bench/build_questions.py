"""Build Wayfinder-Bench: fixed questions with gold answers taken from the sources, not the graph.

Gold comes from three places the graph is built from but does not define:
- fixtures/company/wayfinder-content.json: the authored story (components, task dependencies,
  which page/task each Slack message links, which component each page/task belongs to)
- the GitHub REST API: each pull request's head/base branch, commits, title and description
- the Plane API: each work item's description and "Links" entries

Gold is written as readable references (FALKORDBTE-15, PR #4, a branch name, a page key), never
native account IDs, so questions.json can be committed. The runner maps them to graph IDs locally.
Run once, commit questions.json, then run the benchmark.
"""
import json
import re
import sys
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wayfinder.company.live_seed import load_scope, content
from wayfinder.company.plane import PlaneClient

OUT = Path(__file__).with_name('questions.json')
KEY = 'FALKORDBTE'


def github(scope):
    repo = scope['github']['repository']; token = dotenv_values(ROOT / '.env')['GITHUB_TOKEN']
    client = httpx.Client(base_url=f'https://api.github.com/repos/{repo}', timeout=30,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
    pulls = {}
    for pr in client.get('/pulls', params={'state': 'all', 'per_page': 100}).json():
        commits = [c['sha'] for c in client.get(f"/pulls/{pr['number']}/commits", params={'per_page': 100}).json()]
        pulls[pr['number']] = {'head': pr['head']['ref'], 'base': pr['base']['ref'], 'commits': commits,
            'text': (pr['title'] or '') + '\n' + (pr['body'] or '')}
    return pulls


def plane_items(scope, keys):
    """Description and Links of the work items asked about, read from Plane itself."""
    client = PlaneClient(scope['workspace']); base = f"projects/{scope['project_id']}/"
    try:
        items = {f"{KEY}-{i['sequence_id']}": i for i in client.listing(base + 'work-items/')}
        out = {}
        for key in keys:
            item = client.request('GET', base + f"work-items/{items[key]['id']}/", params={'fields': 'all'})
            links = client.listing(base + f"work-items/{items[key]['id']}/links/")
            out[key] = (item.get('description_html') or '') + ' ' + ' '.join(l.get('url', '') for l in links)
        return out
    finally: client.close()


def prs_mentioning(pulls, plane_text, key, repo):
    """PRs whose title/body name the key, plus PRs the work item itself links to."""
    pattern = re.compile(rf'(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])', re.I)
    found = {n for n, pr in pulls.items() if pattern.search(pr['text'])}
    found |= {int(n) for n in re.findall(rf'github\.com/{re.escape(repo)}/pull/(\d+)', plane_text, re.I)}
    return sorted(found)


def main():
    scope = load_scope(ROOT / 'data/live-workspace/scope.json'); data = content(); repo = scope['github']['repository']
    pulls = github(scope)
    plane = plane_items(scope, ['FALKORDBTE-15', 'FALKORDBTE-16'])
    item = {i['key']: i for i in data['items']}
    key_of = lambda w: f"{KEY}-{w[1:]}"  # Items were seeded in order: w1 is FALKORDBTE-1.
    owner = {'person': 'workspace-owner'}
    comp_name = {c['id']: c['name'] for c in data['components']}
    questions = []

    def add(qtype, question, anchor, intent, gold, answerable=True):
        questions.append({'id': f'q{len(questions) + 1:02d}', 'type': qtype, 'question': question, 'anchor': anchor,
                          'intent': intent, 'answerable': answerable, 'gold': gold if answerable else []})

    # Ownership and assignment (manifest declarations; seeding assigned every task to the owner).
    for slug in ('agent-workflows', 'source-ingestion'):
        add('ownership', f'Who owns the {comp_name[slug]} component?', comp_name[slug], 'owner_of_component', [owner])
    for w in ('w1', 'w8', 'w12'):
        add('ownership', f'Who is assigned {key_of(w)}?', key_of(w), 'assignee_of_task', [owner])
    # Task dependencies (authored story).
    for w in ('w3', 'w6', 'w8', 'w9', 'w11', 'w12'):
        add('dependencies', f'Which tasks must finish before {key_of(w)}?', key_of(w), 'depends_on',
            [{'issue': key_of(d)} for d in item[w]['depends_on']])
    # Component scope.
    for slug in ('source-ingestion', 'agent-workflows'):
        add('component', f'Which work items belong to {comp_name[slug]}?', comp_name[slug], 'tasks_in_component',
            [{'issue': key_of(i['key'])} for i in data['items'] if i['component'] == slug])
        add('documents', f'Which pages describe {comp_name[slug]}?', comp_name[slug], 'pages_for_component',
            [{'page': p['key']} for p in data['pages'] if p['component'] == slug or slug in (p.get('also_components') or [])])
    # Slack -> Plane links (authored per message).
    def link_refs(keys):
        return [{'issue': key_of(k.split(':', 1)[1])} if k.startswith('item:') else {'page': k.split(':', 1)[1]} for k in keys]
    for t, m in ((0, 0), (1, 3), (2, 1), (3, 0), (4, 4), (5, 2)):
        text = data['threads'][t]['messages'][m]
        phrase = ' '.join(re.sub(r'\{owner\}', '', text).split()[:9])
        add('cross_source', f'Which Plane tasks or pages does the Slack message "{phrase}" point to?', phrase,
            'links_in_message', link_refs(data['threads'][t]['links'][str(m)]))
    # Plane -> Slack: which messages discuss a task (inverse of the authored links).
    for w in ('w8', 'w12'):
        hits = [{'slack': f't{t}-{m}'} for t, thread in enumerate(data['threads']) for m, keys in thread.get('links', {}).items()
                if f'item:{w}' in keys]
        add('cross_source', f'Which Slack messages discuss {key_of(w)}?', key_of(w), 'messages_about_task', hits)
    # GitHub (REST API facts).
    for n in (1, 2, 4, 6):
        add('code', f'Which branch did pull request #{n} come from?', f'#{n}', 'branch_of_pr', [{'branch': pulls[n]['head']}])
    for n in (2, 4):
        add('code', f'Which branch did pull request #{n} merge into?', f'#{n}', 'base_of_pr', [{'branch': pulls[n]['base']}])
    for n in (4, 6):
        add('code', f'Which commits are in pull request #{n}?', f'#{n}', 'commits_of_pr', [{'commit': s} for s in pulls[n]['commits']])
    for key in ('FALKORDBTE-15', 'FALKORDBTE-16'):
        add('cross_source', f'Which pull requests mention or are linked from {key}?', key, 'prs_for_task',
            [{'pr': n} for n in prs_mentioning(pulls, plane[key], key, repo)])
    # Decisions: the source page that states each component's decision.
    add('decisions', 'Which document records the decision that agent-created Plane tasks need approval?', comp_name['agent-workflows'],
        'decision_source_for_component', [{'page': 'adr-allocation'}])
    add('decisions', 'Which document records the decision to import sources before semantic extraction?', comp_name['source-ingestion'],
        'decision_source_for_component', [{'page': 'adr-runtime'}])
    # Multi-hop.
    add('multi_hop', 'Who owns the component that FALKORDBTE-9 belongs to?', 'FALKORDBTE-9', 'owner_of_task_component', [owner])
    add('multi_hop', 'Which branch carried the work announced in the Slack message "Branch and commit linking is merged in"?',
        'Branch and commit linking is merged in', 'branch_of_pr_linked_from_message', [{'branch': pulls[4]['head']}])
    # Unanswerable: the right answer is that nothing in the sources supports one.
    for qtype, question, anchor, intent in (
        ('ownership', 'Who owns the Billing Service component?', 'Billing Service', 'owner_of_component'),
        ('ownership', 'Who is assigned FALKORDBTE-99?', 'FALKORDBTE-99', 'assignee_of_task'),
        ('dependencies', 'Which tasks must finish before FALKORDBTE-77?', 'FALKORDBTE-77', 'depends_on'),
        ('code', 'Which branch did pull request #99 come from?', '#99', 'branch_of_pr'),
        ('code', 'Which commits are in pull request #98?', '#98', 'commits_of_pr'),
        ('documents', 'Which pages describe the Payments Gateway component?', 'Payments Gateway', 'pages_for_component'),
        ('cross_source', 'Which Plane tasks does the Slack message "we migrated the payroll database to Postgres" point to?',
         'we migrated the payroll database to Postgres', 'links_in_message'),
        ('decisions', 'Which document records the decision to adopt Kafka for the event bus?', 'Kafka event bus', 'decision_source_for_component')):
        add(qtype, question, anchor, intent, [], answerable=False)

    OUT.write_text(json.dumps({'benchmark': 'Wayfinder-Bench', 'version': 1, 'graph': 'wayfinder_company_live_demo',
        'gold_sources': ['fixtures/company/wayfinder-content.json', 'GitHub REST API (pull requests, commits)',
                         'Plane API (work-item descriptions and links)'],
        'questions': questions}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    counts = {}
    for q in questions: counts[q['type']] = counts.get(q['type'], 0) + 1
    print(json.dumps({'questions': len(questions), 'answerable': sum(q['answerable'] for q in questions), 'by_type': counts}, indent=2))


if __name__ == '__main__': main()
