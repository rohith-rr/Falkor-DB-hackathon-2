# Wayfinder - Company Brain on FalkorDB

Wayfinder connects people, component ownership, project work, documents, conversations, code changes, and decisions in one evidence-backed graph. An MCP agent can investigate a component, inspect who owns it versus who has relevant implementation/review activity, trace decisions to source revisions, and prepare a human-approved Plane follow-up.

**Tracks:** Company Brain; Agents That Act on Connected Data.

Wayfinder runs on two named graphs:

- **`wayfinder_company_live_demo`: the live graph.** It is built from real API records in a Plane project and two Slack channels describing [the Wayfinder project itself](docs/WAYFINDER-PROJECT-DEMO.md), plus this repository's own GitHub pull requests, branches and commits. Records keep their real authorship and timestamps. One `refresh` command keeps it current, and an MCP agent's path through it can be watched live at `/graph`. See [the live workspace](docs/LIVE-WORKSPACE.md).
- **`wayfinder_company_demo`: Northstar, a fictional company.** Its Plane, Slack and GitHub-shaped records are synthetic. It shows owner-versus-expert code expertise and a recorded proof that an edited decision retires its old interpretation.

The Plane/Slack content in the live graph is a staged scenario. Extraction there is bounded and owner-approved, and its recall is low ([evidence](docs/evidence/live-connector-extraction.json)). Models used are listed in the [AI use disclosure](docs/AI-USE-DISCLOSURE.md).

## Start here

- [Design review pack](docs/design/README.md): architecture, ontology, provenance, lifecycle, agent/action design, evaluation, limitations; no implementation listings.
- [Cypher the product depends on](docs/QUERIES.md): IMPLEMENTED_IN aggregation/deduplication, concern status, source invalidation, full-text/vector queries, paths, action guards.
- [Recording script and submission checklist](docs/DEMO.md).
- [AI assistant and model-use disclosure](docs/AI-USE-DISCLOSURE.md).
- [Verified implementation status and what remains](docs/IMPLEMENTATION-STATUS.md).
- [Live workspace: connectors, refresh, freshness and replay](docs/LIVE-WORKSPACE.md).
- [MCP setup and the live agent-path view](docs/MCP.md).
- [Original October 12 ontology proposal](docs/history/ontology-and-population-design-2026-10-12.md), kept for history.

## Local setup

Requires Python 3.11+, Docker/FalkorDB, and a terminal in this repository. There is no JavaScript build step and no public hosting requirement. The reviewed database image is `falkordb/falkordb:v4.20.7`.

If FalkorDB is not already serving localhost:6379, start it with persistent storage on E: (adjust the absolute path for another checkout):

```powershell
New-Item -ItemType Directory -Force 'E:\Falkor-DB hackathon 2\data\falkordb'
docker run -d --name wayfinder-db -p 127.0.0.1:6379:6379 -p 127.0.0.1:3000:3000 -v 'E:\Falkor-DB hackathon 2\data\falkordb:/data' falkordb/falkordb:v4.20.7
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Configure local `.env` (excluded from Git). Preserve the models/dimension already configured if using the existing workspace:

```dotenv
FALKORDB_HOST=localhost
FALKORDB_PORT=6379
FALKORDB_GRAPH=wayfinder_company_demo
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=YOUR_LOCAL_KEY
LLM_MODEL=deepseek/deepseek-v4-flash
EMBEDDING_BASE_URL=https://openrouter.ai/api/v1
EMBEDDING_API_KEY=YOUR_LOCAL_KEY
EMBEDDING_MODEL=perplexity/pplx-embed-v1-0.6b
EMBEDDING_DIM=1024
EXPERTISE_WINDOW_DAYS=30
EXPERTISE_HALF_LIFE_DAYS=15
```

Provider availability and quota are account-dependent. Vector index dimension and stored vectors must match the configured embedding model. The application rejects dimension mismatches; it does not replace failed provider vectors with fake ones.

### Offline baseline - no GitHub, Plane, Slack, or model requests

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company fixture
.\.venv\Scripts\python.exe -m wayfinder.company.capabilities
.\.venv\Scripts\python.exe -m wayfinder.company brief "Resource Allocation" --output data/allocation-brief.json
.\.venv\Scripts\python.exe -m wayfinder.dashboard
```

Open **http://127.0.0.1:8765**. The evidence views are Live proof/source history, Expertise and FalkorDB capabilities, and `/graph` draws the graph with live agent-path highlighting. Page loads make no provider requests. Live controls require `--enable-live`. An archived proof is labelled when it no longer matches the current source. Committed evidence logs document past real runs, not a fresh run on each checkout.

Fixture import reestablishes baseline source records and recalculates v2 code expertise. It resets a local ADR overlay, so do not rerun it between proof stages. A fresh offline import has no provider-generated vectors until explicit preparation.

### Everyday refresh of the live graph

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_company_live_demo --manifest data/live-workspace/manifest.json refresh --extract 3
```

This syncs Plane, Slack and GitHub, then extracts up to three pending units, pages first. Without `--extract` it makes no model calls. See [one-command refresh](docs/LIVE-WORKSPACE.md#one-command-refresh).

### Offline rebuild of the live Plane/Slack graph - no credentials

The live sandbox graph can be rebuilt from a committed, scrubbed recording of its real Plane and Slack API responses. The recording is replayed through the same connector code:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company.replay load --reset
```

This writes `wayfinder_company_live_replay`, with coverage marked as a replay. See [what was recorded and scrubbed](fixtures/live-replay/README.md).

### Explicit real inference and capability preparation

```powershell
# Real local-overlay proof, maximum 2 generation / 12 embedding requests, no retries:
.\.venv\Scripts\python.exe -u -m wayfinder.company.live_proof --enable-live --output docs/evidence/another-live-run.json
# Native retrieval showcase; at most 3 content embeddings plus 1 query embedding:
.\.venv\Scripts\python.exe -u -m wayfinder.company.capabilities --enable-live
# Real MCP protocol investigation, including semantic search (1 query embedding):
.\.venv\Scripts\python.exe -u -m wayfinder.company.smoke docs/evidence/another-mcp-transcript.json
# Enable explicit paid proof/search controls in the local dashboard:
.\.venv\Scripts\python.exe -m wayfinder.dashboard --enable-live
```

The live ADR changes from preserving reservations with at most two immediate retries to **disabling automatic retries and requiring an operator retry**. The runner exposes baseline -> edit/pending -> replacement. It records retired claims, the old brief's validation failure, and a valid replacement brief. No external Plane page is edited. [Staged recording commands](docs/DEMO.md#live-source-proof-commands-northstar) let the presenter pause at pending.

The committed [successful proof](docs/evidence/live-extraction-run.json) used the configured real model, two generation requests and three embeddings. [Native query evidence](docs/evidence/falkordb-capabilities.json) records actual full-text/vector results and EXPLAIN plans. Additional capability/client requests are separate from that proof's budget.

## Graph data model

| Layer | Main records | Meaning |
|---|---|---|
| Structure | Person, Team, Component, Project, Issue, DocPage/DocSection, Comment/Thread, Repository, Directory/File, OwnersFile, PullRequest, Review | Source records, explicit responsibilities, code/change hierarchy, assignments, discussion and exact links |
| Meaning | Argument (including concerns/resolutions), Decision, Rationale, Question, Topic | Quoted interpretations; baseline FIXTURE_ANNOTATION and real LLM_EXTRACTED remain distinguishable |
| Derived/operational | ExpertiseContribution, ExpertiseSnapshot/State, SourceRevision/RawSourceRevision, SourceCoverage, ConcernStatusRevision, StaleFinding, Action | Versioned calculations, retained evidence, coverage, status changes, approved proposals |

Core paths:

- Person -> OWNS_COMPONENT -> Component -> IMPLEMENTED_IN -> Directory.
- Person -> APPROVER_IN / REVIEWER_IN -> OwnersFile -> COVERS -> Directory.
- Project -> HAS_WORK_ITEM -> Issue -> CONCERNS_COMPONENT -> Component.
- Project -> HAS_DOCUMENT -> DocPage -> DESCRIBES -> Component.
- Thread -> HAS_COMMENT -> Comment -> ASSERTS -> Argument/Decision/Question.
- PullRequest -> REFERENCES -> Plane Issue; PullRequest -> TOUCHES -> File.
- Person -> EXPERT_IN -> Directory, with immutable per-generation contribution evidence.
- Resolution Argument -> RESOLVES -> specific concern; Action -> BASED_ON -> evidence.

Source revisions retain evidence text; changed source text retires prior meaning before re-extraction. CONFIRMED declarations, EXTRACTED interpretation, and COMPUTED signals are different authority categories. Exact quote presence does not prove semantic entailment. Relationship intervals support bounded history; mutable records are not complete historical snapshots.

## One coherent fictional dataset

Northstar contains two teams, six humans plus a synthetic build bot, two components, two Plane projects, twelve work items, twenty work-item comments, six pages, thirty chat messages in six threads, eight synthetic GitHub PRs, three OWNERS files, and fifty baseline interpretation annotations.

Four allocation PRs are merged and one is open; two runtime PRs are merged and one is open. Dee authors three merged allocation PRs. **Faye is not an allocation owner but leads file-review expertise across four PRs.** Commands, PR-wide approvals, bots, self-review, and open-PR authorship do not inflate strong directory expertise. More than three supporting PRs means multiple observations, not calibrated confidence.

Event dates are fixed offsets before a persisted dataset anchor. Decay uses the published snapshot's calculation time and configured half-life. Staleness uses actual UTC now; snapshots older than two hours are flagged. The UI displays all these clocks. Reimporting does not silently redate events.

## Agent/MCP access

**See the agent work:** `python -m wayfinder.dashboard --graph wayfinder_company_live_demo`, then open `/graph`. The page draws the graph and highlights the path an MCP agent takes, live, call by call ([setup](docs/MCP.md#watch-the-agents-path-in-the-graph)).

Configure the client to launch `.venv\Scripts\python.exe` with the absolute path to `wayfinder\mcp_server.py`, and set its environment `FALKORDB_GRAPH=wayfinder_company_demo`. See [client setup](docs/MCP.md).

The default server exposes **23 tools** that only read the knowledge graph. These include `graph_algorithm`: FalkorDB's native PageRank, betweenness, connected components and shortest path over the current graph, run on a temporary projection that is deleted afterwards. `semantic_search` makes one explicitly requested embedding-provider call; other tools make no model requests. Optional action mode adds 2 tools (25 in total) for proposal preparation and execution, but no approval-granting tool.

Ask:

> Investigate Resource Allocation. Confirm the graph and coverage, find declared owners, follow IMPLEMENTED_IN, rank code contributors, retrieve supporting PR/file-review evidence, inspect the ADR and unresolved starvation concern, use semantic_search, and validate a cited review brief. Explain owner versus expert and state missing evidence.

Agent navigation uses stable IDs, bounded neighbors/paths, thread inspection, source revision/evidence retrieval, and custom database-enforced read-only Cypher. Calls have database timeouts, pagination, and output bounds. The saved [MCP transcript](docs/evidence/company-mcp-transcript.json) is a real protocol session using a scripted client, not independent agent accuracy evaluation.

## Human-approved Plane action

The existing action flow prepares an exact payload, records its hash, requires a human's approval outside MCP, revalidates cited evidence, then creates one task in an allowlisted fictional Plane sandbox. Repeated success returns the existing ID; uncertain outcomes reconcile instead of blindly posting again.

This ran live on October 18 against the sandbox project ([evidence](docs/evidence/plane-action-live.json)):

- A brief for Agent Review Workflow, built from the live graph, cited the extracted "require human approval" Decision.
- The project owner approved the exact payload hash in a terminal.
- `execute` created FALKORDBTE-16. A repeat returned the same task, and Plane lists exactly one.
- The next sync ingested the task and linked it to ADR-002 and FALKORDBTE-11.

Put PLANE_API_KEY only in local config. See [setup and commands](docs/DEMO.md#approved-plane-action).

## Verification

Tests cover expertise inflation and exclusions, overlapping Component mappings, source-edit invalidation and stale brief rejection, connectors against mocked Plane/Slack/GitHub APIs (edits, deletions, merges, moved branch heads), cross-source URL and key links, refresh and extraction budgets, the offline replay, native vector metric and filtering, MCP navigation and read-only boundaries, agent-path tracing, action approval and idempotency, and dashboard endpoints. The suite has 140 tests. Normal tests use fake inference keys; integration tests require local FalkorDB and isolate their graph names.

```powershell
$env:LLM_API_KEY='sk-dummy-key'
$env:EMBEDDING_API_KEY='sk-dummy-key'
$env:EMBEDDING_DIM='1536'
$env:EXPERTISE_WINDOW_DAYS='180'
$env:EXPERTISE_HALF_LIFE_DAYS='90'
.\.venv\Scripts\python.exe -m pytest -q
```

These overrides apply to this shell; use a fresh shell for the configured live demo. Never use an empty key as an offline-test override because dotenv can reload the live value.
