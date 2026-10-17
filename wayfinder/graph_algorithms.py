"""FalkorDB native graph algorithms over the current (non-retired) graph, for agents.

The knowledge graph keeps history as retired nodes and closed relationship intervals, and
algo.* procedures cannot filter by property. Running them directly would rank stale links.
So each call copies the selected current nodes and relationships into a temporary named
projection graph, runs the native algorithm there, maps results back to stable IDs, and
deletes the projection. The knowledge graph itself is only read.
"""
import uuid

ALGORITHMS = ('pagerank', 'betweenness', 'components', 'shortest_path')
# Work, discussion, code and knowledge records; provenance and calculation records are excluded.
DEFAULT_LABELS = ['Component', 'Person', 'Team', 'Project', 'Issue', 'DocPage', 'DocSection', 'Thread', 'Comment', 'PullRequest',
    'Review', 'Branch', 'Commit', 'Repository', 'Directory', 'Decision', 'Question', 'Argument', 'Rationale', 'Action', 'Topic']
# Computed signals would make algorithms rank their own outputs.
DERIVED_TYPES = ['EXPERT_IN', 'UNLISTED_EXPERT', 'BASED_ON']
MAX_NODES = 3000
MAX_EDGES = 20000
DISPLAY = ("coalesce(n.key,n.title,n.name,n.display_name,n.login,n.short_sha,n.summary,n.path,"
    "substring(coalesce(n.text_content,n.body_text,''),0,70),n.id)")
CURRENT = "{v}.valid_to IS NULL AND coalesce({v}.is_deleted,false)=false"


class AlgorithmError(ValueError):
    pass


def _scope_ids(db, around_id, hops):
    rows = db.graph.ro_query(f"MATCH (c {{id:$id}}) WHERE {CURRENT.format(v='c')} "
        f"MATCH p=(c)-[*0..{int(hops)}]-(n) WHERE all(r IN relationships(p) WHERE r.valid_to IS NULL) "
        f"AND all(x IN nodes(p) WHERE {CURRENT.format(v='x')}) RETURN DISTINCT n.id LIMIT {MAX_NODES + 1}",
        params={'id': around_id}, timeout=10000).result_set
    if not rows: raise AlgorithmError(f'around_id {around_id!r} is not a current entity')
    return [r[0] for r in rows]


def project(db, labels, relationship_types, around_id=None, hops=2):
    """Current nodes and relationships selected for an algorithm run."""
    labels = labels or DEFAULT_LABELS
    scope = _scope_ids(db, around_id, hops) if around_id else None
    nodes = db.graph.ro_query(f"MATCH (n) WHERE labels(n)[0] IN $labels AND {CURRENT.format(v='n')} AND n.id IS NOT NULL "
        f"AND ($scope IS NULL OR n.id IN $scope) RETURN n.id,labels(n)[0],{DISPLAY} LIMIT {MAX_NODES + 1}",
        params={'labels': labels, 'scope': scope}, timeout=10000).result_set
    if len(nodes) > MAX_NODES: raise AlgorithmError(f'More than {MAX_NODES} entities; narrow node_labels or use around_id')
    ids = {r[0] for r in nodes}
    edges = db.graph.ro_query("MATCH (a)-[r]->(b) WHERE labels(a)[0] IN $labels AND labels(b)[0] IN $labels "
        f"AND r.valid_to IS NULL AND {CURRENT.format(v='a')} AND {CURRENT.format(v='b')} "
        "AND (size($types)=0 OR type(r) IN $types) AND NOT type(r) IN $derived "
        f"RETURN DISTINCT a.id,type(r),b.id LIMIT {MAX_EDGES + 1}",
        params={'labels': labels, 'types': relationship_types or [], 'derived': DERIVED_TYPES}, timeout=10000).result_set
    if len(edges) > MAX_EDGES: raise AlgorithmError(f'More than {MAX_EDGES} relationships; narrow relationship_types')
    edges = [e for e in edges if e[0] in ids and e[2] in ids and e[0] != e[2]]
    # The repository root directory has an empty path.
    return {r[0]: {'id': r[0], 'labels': [r[1]], 'name': r[2] or ('(repository root)' if r[1] == 'Directory' else r[0])} for r in nodes}, edges


def run(db, algorithm, node_labels=None, relationship_types=None, around_id=None, hops=2,
        source_id=None, target_id=None, max_hops=6, limit=10):
    if algorithm not in ALGORITHMS: raise AlgorithmError(f'Use one of {", ".join(ALGORITHMS)}')
    if not 1 <= limit <= 50: raise AlgorithmError('Use limit 1..50')
    if not 1 <= hops <= 3: raise AlgorithmError('Use hops 1..3')
    if not 1 <= max_hops <= 10: raise AlgorithmError('Use max_hops 1..10')
    if algorithm == 'shortest_path' and not (source_id and target_id):
        raise AlgorithmError('shortest_path needs source_id and target_id')
    nodes, edges = project(db, node_labels, relationship_types, around_id, hops)
    if algorithm == 'shortest_path':
        missing = [i for i in (source_id, target_id) if i not in nodes]
        if missing: raise AlgorithmError(f'Not in the projection (retired, or excluded by node_labels/around_id): {missing}')
    projection = db.client.select_graph(f'{db.graph_name}__algo_{uuid.uuid4().hex[:10]}')
    try:
        projection.query('UNWIND $ids AS id CREATE (:N {id:id})', {'ids': list(nodes)})
        projection.query('CREATE INDEX FOR (n:N) ON (n.id)')
        for start in range(0, len(edges), 2000):
            # R keeps direction (PageRank, components); U holds both directions, because
            # FalkorDB shortestPath is directed-only and brokerage should not depend on edge direction.
            projection.query('UNWIND $edges AS e MATCH (a:N {id:e[0]}),(b:N {id:e[2]}) '
                'CREATE (a)-[:R {type:e[1]}]->(b),(a)-[:U {type:e[1],forward:true}]->(b),(b)-[:U {type:e[1],forward:false}]->(a)',
                {'edges': [list(e) for e in edges[start:start + 2000]]})
        result = {'algorithm': algorithm, 'graph': db.graph_name,
            'projection': {'entities': len(nodes), 'relationships': len(edges), 'node_labels': node_labels or 'default',
                'relationship_types': relationship_types or 'all current (computed expertise excluded)',
                'around_id': around_id, 'hops': hops if around_id else None},
            'method': 'FalkorDB native algo.* / shortestPath over a temporary projection of current records; the knowledge graph is only read'}
        if algorithm == 'pagerank':
            rows = projection.ro_query('CALL algo.pageRank(\'N\',\'R\') YIELD node,score RETURN node.id,score '
                'ORDER BY score DESC, node.id LIMIT $limit', {'limit': limit}).result_set
            result['items'] = [{**nodes[i], 'score': round(s, 6)} for i, s in rows]
            result['meaning'] = 'Importance from incoming relationships, following edge direction; not authority or correctness.'
        elif algorithm == 'betweenness':
            rows = projection.ro_query("CALL algo.betweenness({nodeLabels:['N'],relationshipTypes:['U']}) YIELD node,score "
                'RETURN node.id,score ORDER BY score DESC, node.id LIMIT $limit', {'limit': limit}).result_set
            result['items'] = [{**nodes[i], 'score': round(s, 3)} for i, s in rows if s > 0]
            result['meaning'] = 'How often an entity lies on shortest paths between others (a broker); direction ignored.'
        elif algorithm == 'components':
            rows = projection.ro_query("CALL algo.WCC({nodeLabels:['N'],relationshipTypes:['R']}) YIELD node,componentId "
                'RETURN componentId, collect(node.id) AS members ORDER BY size(members) DESC').result_set
            result['component_count'] = len(rows)
            result['singletons'] = sum(1 for _, m in rows if len(m) == 1)
            result['items'] = [{'size': len(m), 'members': [nodes[i] for i in sorted(m)[:10]],
                'members_truncated': len(m) > 10} for _, m in rows[:limit] if len(m) > 1]
            result['meaning'] = 'Groups connected by any current relationship, ignoring direction; isolated islands reveal unlinked work.'
        else:
            rows = projection.ro_query(f'MATCH (a:N {{id:$s}}),(b:N {{id:$t}}) WITH shortestPath((a)-[:U*..{int(max_hops)}]->(b)) AS p '
                'RETURN [n IN nodes(p) | n.id], [r IN relationships(p) | [r.type, r.forward]]',
                {'s': source_id, 't': target_id}).result_set
            if not rows or rows[0][0] is None:
                result['items'] = []; result['note'] = f'No path within {max_hops} hops over current relationships.'
            else:
                ids, steps = rows[0]
                relationships = []
                for (kind, forward), a, b in zip(steps, ids, ids[1:]):
                    source, target = (a, b) if forward else (b, a)
                    relationships.append({'type': kind, 'source_id': source, 'target_id': target})
                result['items'] = [nodes[i] for i in ids]
                result['relationships'] = relationships
                result['hops'] = len(relationships)
                result['meaning'] = 'Fewest current relationships connecting the two entities; connectivity, not causality.'
        return result
    finally:
        projection.delete()
