# Cypher the product depends on

Target `wayfinder_company_demo` unless stated otherwise. These queries describe real retrieval and validation responsibilities. Ingestion/index setup and the two-ID repair are writes; the MCP read surface uses database-enforced read-only execution with a five-second timeout. Parameters are supplied separately by the application. Every answer must retain source scope and freshness.

## Component-to-code mapping

```cypher
MATCH (c:Component {id:$component})-[m:IMPLEMENTED_IN]->(d:Directory)
WHERE c.valid_to IS NULL AND m.valid_to IS NULL AND d.valid_to IS NULL
RETURN c.id,d.id,d.repo,d.path
```

`IMPLEMENTED_IN` is a declared mapping, not an inferred dependency. The demo maps Resource Allocation to `services/allocation` and Node Runtime to `services/runtime` in the synthetic `northstar-demo/engineering` repository.

## Component expertise: deduplicate before aggregating

```cypher
MATCH (state:ExpertiseState {id:'expertise:directory:published'})
MATCH (component:Component {id:$component})-[mapping:IMPLEMENTED_IN]->(root:Directory)
WHERE component.valid_to IS NULL AND mapping.valid_to IS NULL AND root.valid_to IS NULL
WITH state,collect(DISTINCT {id:root.id,path:root.path,repo:root.repo}) AS roots
MATCH (c:ExpertiseContribution {generation:state.generation})
MATCH (p:Person {id:c.person_id})
WHERE p.valid_to IS NULL AND coalesce(p.is_deleted,false)=false AND coalesce(p.is_bot,false)=false
AND any(root IN roots WHERE c.repo=root.repo AND
  (c.directory_id=root.id OR c.path=root.path OR root.path='' OR c.path STARTS WITH (root.path+'/')))
RETURN DISTINCT c.record_json AS contribution,p.id AS person_id,p.login AS login
```

The application decodes the contribution records, excludes the zero-credit involvement channel, sums implementation and peer-review allocations, and counts distinct supporting PRs. The `any` predicate and DISTINCT prevent overlapping roots from multiplying credit. Records are allocated to direct directories; ancestors are retrieval scopes, not extra score copies. `who_knows` exposes this behavior for stable Component IDs.

## Declared ownership and assignments

```cypher
MATCH (p:Person)-[r:OWNS_COMPONENT]->(c:Component {id:$component})
WHERE p.valid_to IS NULL AND r.valid_to IS NULL
RETURN p,r,c
```

Code authorization follows `APPROVER_IN`/`REVIEWER_IN` through `OwnersFile → COVERS → Directory`, with inherited-parent rules and `no_parent_owners`. Plane `Issue → ASSIGNED_TO → Person` means task assignment. Neither assignment nor a high expertise score grants code-review authority.

## Multi-hop evidence and bounded paths

```cypher
MATCH path=(c:Component {id:$component})-[*1..5]-(p:Person {id:$person})
WHERE all(r IN relationships(path) WHERE r.valid_to IS NULL)
AND all(n IN nodes(path) WHERE n.valid_to IS NULL AND coalesce(n.is_deleted,false)=false)
RETURN path ORDER BY length(path) LIMIT 10
```

The `find_paths` product tool also permits relationship filters. This returns shortest among bounded matching paths, not proof of causality or a guaranteed global shortest connection beyond five hops.

## Source claim and revision inspection

```cypher
MATCH (source)-[assertion:ASSERTS]->(claim {id:$claim})
WHERE assertion.valid_to IS NULL AND claim.valid_to IS NULL
MATCH (revision:SourceRevision {id:claim.source_revision_id})
RETURN source.id,source.source_url,claim,revision.text_content,
       source.source_revision_id=claim.source_revision_id AS current_revision
```

Fixture interpretations carry `FIXTURE_ANNOTATION`; real inference carries `LLM_EXTRACTED`. Quote verification proves text presence under normalization, not semantic entailment.

## Concern status and qualifying closure

```cypher
MATCH (concern:Argument {kind:'concern'})-[raised:RAISES_CONCERN_ON]->(target {id:$component})
WHERE concern.valid_to IS NULL AND raised.valid_to IS NULL
RETURN concern.id,concern.summary,concern.status,
       concern.resolution_evidence_ids,concern.resolution_rule_version
```

The resolver obtains candidate closure evidence with:

```cypher
MATCH (resolution:Argument {kind:'resolution'})-[r:RESOLVES]->(concern:Argument {id:$concern})
MATCH (person:Person)-[made:MADE]->(resolution)
MATCH (source)-[assertion:ASSERTS]->(resolution)
WHERE r.valid_to IS NULL AND made.valid_to IS NULL AND assertion.valid_to IS NULL
AND resolution.valid_to IS NULL AND source.valid_to IS NULL AND person.valid_to IS NULL
AND coalesce(source.is_deleted,false)=false AND coalesce(person.is_bot,false)=false
RETURN person.id,resolution.source_timestamp,r.evidence_quote,
       coalesce(source.body_text,source.text_content),source.id
```

Application checks then require a later timestamp, valid quote, specific resolution wording, and a concern raiser or declared component owner. Generic approval, merging, task completion, and test commands do not close concerns. ADDRESSES alone yields claimed-addressed. Status revisions retain the rule version.

## Full-text retrieval

```cypher
CALL db.idx.fulltext.queryNodes('Argument', $query) YIELD node,score
WHERE node.valid_to IS NULL
RETURN node,score ORDER BY score DESC LIMIT 10
```

The explicit capability preparation creates full-text indexes for Topic.name, Argument.summary, and Comment.body_text, plus stable-ID and vector indexes. Lexical `search_entities` remains separate from native full-text showcase retrieval.

## Agent-accessible semantic retrieval

```cypher
CALL db.idx.vector.queryNodes('Argument','embedding',$k,vecf32($vector)) YIELD node,score
WHERE node.valid_to IS NULL AND coalesce(node.is_deleted,false)=false
AND node.embedding_model=$model AND node.embedding_dim=$dim
RETURN node,score ORDER BY score ASC LIMIT $limit
```

`semantic_search` generates one real query embedding per explicit call, with no automatic retries. It queries selected Topic/Argument/Comment indexes, oversampling up to 100 candidates per label, filters current records with matching model/dimension, and returns bounded results. Filtering after ANN retrieval means results are not exhaustive.

**Pinned-server metric:** a self-query returns native score approximately zero: cosine distance, lower is closer. The product reports similarity = 1 − native distance, higher is closer. Similarity is not confidence. A measured self-query and real provider result are retained in [capability evidence](evidence/falkordb-capabilities.json).

For a copyable Browser-only example, use a stored provider-generated vector without making another API request:

```cypher
MATCH (seed:Argument {id:'company:northstar:claim:pc7'})
WITH seed.embedding AS vector
CALL db.idx.vector.queryNodes('Argument','embedding',10,vector) YIELD node,score
WHERE node.valid_to IS NULL
RETURN node,score ORDER BY score ASC LIMIT 10
```

This is nearest-neighbor retrieval from an existing record, not a fresh text-query embedding.

## Index readiness and execution plans

```cypher
CALL db.indexes()
```

Inspect OPERATIONAL status, indexed fields, dimension, and cosine configuration. The application normalizes the pinned server's per-property index metadata. `GRAPH.EXPLAIN` records the plan without executing a write. The saved capability evidence includes actual procedure-call plans; the presence of an index definition alone is insufficient.

## Source-edit invalidation and stale brief rejection

The ingestion boundary compares old and new source text, retains the new SourceRevision, clears stale source embeddings/extraction markers, and closes previous extracted assertions/claims and their current incident links. This is a write operation, not an MCP read tool.

To inspect the result:

```cypher
MATCH (source {id:$unit})
OPTIONAL MATCH (source)-[assertion:ASSERTS]->(claim)
RETURN source.source_revision_id,source.semantic_stale,source.extraction_version,
       claim.id,claim.valid_to,assertion.valid_to
```

`validate_review_brief` rejects missing/retired evidence, mismatched URLs, stale source revisions, invalid quotes, and invalid concern-status assertions. Per-unit publication uses a source-text/expected-generation guard and publishes the validated replacement set in one statement. It does not make the whole sync atomic.

## Approved action guards

Preparation binds the exact workspace, project, title, description, and brief to a payload hash. Human approval records that hash outside MCP. Execution revalidates evidence and claims execution with:

```cypher
MATCH (a:Action {id:$id}) WHERE a.status='approved'
SET a.status='executing' RETURN a.id
```

This is a write used only by the optional action service. Successful repeats return the external task ID. Uncertain outcomes reconcile external action identity instead of blindly posting again. The local approval interface is a trusted boundary, not enterprise authentication.
