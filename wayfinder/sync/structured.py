"""Fetch and reconcile scoped structured sources without substituting fixtures."""
import re
import yaml
import json
from ..models.common import now_iso, compute_content_hash
from ..parsers.sigs_parser import SigsParser
from ..parsers.owners_parser import OwnersParser
from ..parsers.kep_parser import KepParser
from ..parsers.gate_parser import GateParser
from ..parsers.doc_parser import DocParser


class StructuredSourceSync:
    def __init__(self, db, github):
        self.db, self.github = db, github

    def refresh(self):
        from ..config import settings
        now, records = now_iso(), []
        trees = {}
        def tree(repo):
            if repo not in trees:
                trees[repo] = self.github.repository_tree(repo)
            return trees[repo]
        def fetch(repo, path, ref=None):
            return self.github.fetch_raw_file(repo, path, ref or tree(repo)[0])
        def record(repo, path, content, parsed, ref=None):
            records.append((f'{repo}:{path}' + (f'@{ref}' if ref else ''), content, parsed))
        community = 'kubernetes/community'
        content = fetch(community, 'sigs.yaml')
        sigs = SigsParser.parse_sigs_yaml(content)
        # Keep SIG Node and its own subprojects, rather than all Kubernetes governance.
        sig_ids = {n.id for n in sigs['sigs'] if n.name.lower() in ('node', 'sig-node')}
        sub_ids = {e['target_id'] for e in sigs['edges'] if e['rel_type'] == 'HAS_SUBPROJECT' and e['source_id'] in sig_ids}
        selected = [e for e in sigs['edges'] if e['source_id'] in sig_ids | sub_ids or e['target_id'] in sig_ids]
        people_ids = {e['source_id'] for e in selected if e['source_label'] == 'Person'}
        sigs.update(sigs=[n for n in sigs['sigs'] if n.id in sig_ids],
                    subprojects=[n for n in sigs['subprojects'] if n.id in sub_ids],
                    people=[n for n in sigs['people'] if n.id in people_ids], edges=selected)
        record(community, 'sigs.yaml', content, sigs)
        owner_paths = {('kubernetes/kubernetes', path) for path in settings.owners_scope}
        for edge in selected:
            if edge['rel_type'] == 'GOVERNED_BY':
                repo, path = edge['target_id'].removeprefix('k8s:owners:').split(':', 1)
                owner_paths.add((repo, path))
        # Include all scoped descendants and their ancestors for inheritance.
        for repo, path in list(owner_paths):
            prefix = path.rsplit('/', 1)[0] if '/' in path else ''
            _, paths = tree(repo)
            owner_paths.update((repo, p) for p in paths if p.endswith('/OWNERS') and p.startswith(prefix + '/'))
            parts = prefix.split('/') if prefix else []
            owner_paths.update((repo, '/'.join(parts[:i] + ['OWNERS'])) for i in range(len(parts) + 1)
                               if '/'.join(parts[:i] + ['OWNERS']) in paths)
        aliases = {}
        for repo in {repo for repo, _ in owner_paths}:
            _, paths = tree(repo)
            aliases[repo] = OwnersParser.parse_owners_aliases(fetch(repo, 'OWNERS_ALIASES')) if 'OWNERS_ALIASES' in paths else {}
        for repo, path in sorted(owner_paths):
            if path not in tree(repo)[1]:
                continue
            content = fetch(repo, path)
            record(repo, path, content, OwnersParser.parse_owners_file(content, repo, path, aliases[repo], commit_sha=tree(repo)[0]))
        enhancements = 'kubernetes/enhancements'
        for path in tree(enhancements)[1]:
            if path.startswith('keps/sig-node/') and path.endswith('/kep.yaml'):
                content = fetch(enhancements, path)
                url = f'https://github.com/{enhancements}/blob/{tree(enhancements)[0]}/{path}'
                metadata = yaml.safe_load(content) or {}
                metadata.setdefault('kep-number', path.split('/')[-2].split('-', 1)[0])
                parsed = KepParser.parse_kep_yaml(yaml.safe_dump(metadata), source_url=url, source_ref=f'{enhancements}:{path}')
                record(enhancements, path, content, parsed)
                readme = path.rsplit('/', 1)[0] + '/README.md'
                if readme in tree(enhancements)[1]:
                    content = fetch(enhancements, readme)
                    parsed_readme = KepParser.parse_kep_readme(content, parsed['kep'].number,
                        source_url=url.rsplit('/', 1)[0] + '/README.md', source_ref=f'{enhancements}:{readme}')
                    record(enhancements, readme, content, parsed_readme)
        k8s = 'kubernetes/kubernetes'
        gate_paths = [p for p in tree(k8s)[1] if p.startswith('pkg/features/') and p.endswith('.go') and not p.endswith('_test.go')]
        for ref in self.github.release_refs(k8s):
            for path in gate_paths:
                # Files absent from older release trees have no stage in that release.
                try:
                    content = fetch(k8s, path, ref)
                except Exception as exc:
                    import httpx
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                        continue
                    raise
                record(k8s, path, content, GateParser.parse_feature_gates_go(content, k8s, path, ref,
                       source_url=f'https://github.com/{k8s}/blob/{ref}/{path}'), ref)
        gates = {g.name for _, _, parsed in records for g in parsed.get('gates', [])}
        website = 'kubernetes/website'
        for path in tree(website)[1]:
            if path.endswith('.md') and any(path.startswith(prefix) for prefix in settings.docs_scope):
                content = fetch(website, path)
                record(website, path, content, DocParser.parse_markdown_doc(content, path, known_gates=gates,
                    source_url=f'https://github.com/{website}/blob/{tree(website)[0]}/{path}', source_ref=f'{website}:{path}'))
        # Fetch all sources before mutation. Nodes precede every reference edge.
        changed, all_keys = [], [key for key, _, _ in records]
        for key, content, parsed in records:
            digest = compute_content_hash(content)
            if ':keps/' in key and key.endswith('/README.md'):
                digest = compute_content_hash('kep-readme-parser-v3\n' + content)
            previous = self.db.query('MATCH (s:SyncSource {id: $id}) RETURN s.content_hash', {'id': key})
            # Aliases can change the interpretation without changing OWNERS text.
            if ':OWNERS' in key:
                digest = compute_content_hash(content + json.dumps(aliases.get(key.split(':', 1)[0], {}), sort_keys=True))
            if previous.result_set and previous.result_set[0][0] == digest:
                continue
            changed.append((key, digest, parsed))
            for group, values in parsed.items():
                if group == 'edges':
                    continue
                for node in values if isinstance(values, list) else [values]:
                    if hasattr(node, 'to_cypher_props'):
                        props = node.to_cypher_props()
                        if node.__class__.__name__ in {'KEPSection', 'DocSection'}:
                            props['structured_source'] = key
                            props['valid_to'] = None
                        self.db.upsert_node(node.__class__.__name__, props)
        # Release definitions remain authoritative when metadata alone changes.
        for key, _, parsed in records:
            if '@v' in key:
                for gate in parsed.get('gates', []):
                    self.db.upsert_node('FeatureGate', gate.to_cypher_props())
        for key, digest, parsed in changed:
            rationale_ids = [n.id for n in parsed.get('rationales', [])]
            self.db.query('MATCH (:KEPSection)-[r:ASSERTS]->(n:Rationale) '
                          "WHERE r.structured_source = $source AND r.authority = 'CONFIRMED' "
                          'AND n.valid_to IS NULL AND NOT n.id IN $ids '
                          'SET n.valid_to = $now WITH n MATCH (n)-[e]-() '
                          'WHERE e.valid_to IS NULL SET e.valid_to = $now',
                          {'source': key, 'ids': rationale_ids, 'now': now})
            present_sections = [n.id for group, values in parsed.items() if group != 'edges'
                                for n in (values if isinstance(values, list) else [values])
                                if n.__class__.__name__ in {'KEPSection', 'DocSection'}]
            self.db.query('MATCH (n) WHERE n.structured_source = $source AND NOT n.id IN $ids '
                          'AND n.valid_to IS NULL SET n.valid_to = $now WITH n MATCH (n)-[r:ASSERTS]->(s) '
                          'WHERE r.valid_to IS NULL SET r.valid_to = $now, s.valid_to = $now '
                          'WITH s MATCH (s)-[e]-() WHERE e.valid_to IS NULL SET e.valid_to = $now',
                          {'source': key, 'ids': present_sections, 'now': now})
            active = [e['props']['id'] for e in parsed['edges']]
            self.db.query('MATCH ()-[r]->() WHERE r.structured_source = $source AND r.valid_to IS NULL '
                          'AND NOT coalesce(r.logical_id, r.id) IN $ids SET r.valid_to = $now',
                          {'source': key, 'ids': active, 'now': now})
            for edge in parsed['edges']:
                props = dict(edge['props'], structured_source=key)
                self.db.upsert_edge(edge['source_label'], edge['source_id'], edge['target_label'], edge['target_id'], edge['rel_type'], props)
            self.db.upsert_node('SyncSource', {'id': key, 'content_hash': digest, 'observed_at': now, 'valid_to': None})
        self.db.query('MATCH (n) WHERE n.structured_source IS NOT NULL AND NOT n.structured_source IN $ids '
                      'AND n.valid_to IS NULL SET n.valid_to = $now WITH n MATCH (n)-[r:ASSERTS]->(s) '
                      'WHERE r.valid_to IS NULL SET r.valid_to = $now, s.valid_to = $now '
                      'WITH s MATCH (s)-[e]-() WHERE e.valid_to IS NULL SET e.valid_to = $now', {'ids': all_keys, 'now': now})
        self.db.query('MATCH (s:SyncSource) WHERE NOT s.id IN $ids SET s.valid_to = $now', {'ids': all_keys, 'now': now})
        self.db.query('MATCH ()-[r]->() WHERE r.structured_source IS NOT NULL AND NOT r.structured_source IN $ids '
                      'AND r.valid_to IS NULL SET r.valid_to = $now', {'ids': all_keys, 'now': now})
        return {'status': 'success', 'sources': len(records), 'changed_sources': len(changed), 'keps': sum('kep' in p for _, _, p in records)}
