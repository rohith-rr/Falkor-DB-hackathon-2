# Wayfinder: Company Brain on FalkorDB

Wayfinder is an evidence-backed engineering knowledge graph on **FalkorDB**. It supports scoped Kubernetes SIG Node imports and a separate fictional company sandbox using Plane Cloud Project Pages/work items and optional Slack. Import windows and coverage depend on each run; complete organizational activity is not guaranteed.

See [Company Brain setup, repairs, evaluation, and approved Plane actions](docs/COMPANY.md).

The graph follows the three-layer architecture described in `Wayfinder Graph Ontology and Population Design.md`:

| Layer | What it holds | Populated by | Trust level |
| --- | --- | --- | --- |
| **1. Structure** | People, SIGs, ownership, KEPs, feature gates, issues, PRs, reviews, comments, docs | Deterministic parsers over structured files + GitHub API | Confirmed (deterministic) |
| **2. Meaning** | Topics, arguments, concerns, decisions, rationale, questions & answers | OpenAPI / OpenRouter LLM extraction with verbatim quote verification | Extracted (text proven, reading is the model's) |
| **3. Derived** | Expertise, real working groups, review load, unresolved concerns, stale findings | Cypher rules + FalkorDB graph algorithms (PageRank, betweenness, WCC/CDLP) | Computed (reproducible from layers 1 and 2) |

---

## 🛠️ Environment & Configuration

All dependencies, virtual environments, caches, and raw data are strictly located within `E:\Falkor-DB hackathon 2\` to keep the primary C: drive clean.

- **Virtual Environment**: `E:\Falkor-DB hackathon 2\.venv\`
- **Pip Cache**: `E:\Falkor-DB hackathon 2\.cache\pip\`
- **Extraction Cache**: `E:\Falkor-DB hackathon 2\.cache\extractions\`
- **FalkorDB Instance**: Docker container running on `localhost:6379` (`falkordb/falkordb:v4.20.7`)

### Environment Variables (`.env`)

Configure your settings in `.env`:

```env
# FalkorDB Docker Instance
FALKORDB_HOST=localhost
FALKORDB_PORT=6379
FALKORDB_GRAPH=wayfinder_sig_node

# GitHub PAT (Personal Access Token)
GITHUB_TOKEN=your_personal_access_token_here

# OpenAPI / OpenAI-compatible LLM Spec (OpenRouter, OpenAI, LocalAI, vLLM, Ollama)
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=your_openrouter_or_llm_api_key
LLM_MODEL=openai/gpt-4o-mini

# OpenAPI / OpenAI-compatible Embedding Spec
EMBEDDING_BASE_URL=https://openrouter.ai/api/v1
EMBEDDING_API_KEY=your_embedding_api_key
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536

# Local storage configuration (strictly on Drive E)
CACHE_DIR=E:/Falkor-DB hackathon 2/.cache
DATA_DIR=E:/Falkor-DB hackathon 2/data

# Extraction & Derived parameters
EXTRACTOR_VERSION=v1.0.0
REJECTION_THRESHOLD=0.30
EXPERTISE_WINDOW_DAYS=180
EXPERTISE_HALF_LIFE_DAYS=90
```

---

## 🚀 Quickstart & CLI Commands

Activate the environment:
```powershell
.\.venv\Scripts\Activate.ps1
```

### 1. Initialize Schema & Indexes
Creates range indexes on all stable IDs, FalkorDB vector indexes on embeddings, and full-text indexes on titles & bodies:
```powershell
python -m wayfinder init
```

### 2. Run 7-Step Population Pipeline
Executes the initial population order:
1. Structured files (`sigs.yaml`, `OWNERS`, `kep.yaml`, `README.md`, feature gates, docs).
2. GitHub activity (Issues, PRs, files, reviews, comments, Prow commands).
3. Threads and cross-references (`#123`, `Fixes #123`, `KEP-1287`, URLs).
4. Layer 2 extraction with deterministic quote verification and OpenAPI embeddings.
5. Index verification.
6. Layer 3 derived rules (expertise, paper owners, unlisted experts, PageRank, betweenness, working groups).
7. Stale checks (`DOC_STAGE_MISMATCH`, `OWNER_INACTIVE`, etc.).

```powershell
# Using bundled offline/seed SIG Node dataset:
python -m wayfinder populate

# Or using live GitHub API via your PAT token:
python -m wayfinder populate --live --limit 50
```

### 3. Inspect Graph Statistics
```powershell
python -m wayfinder stats
```

### 4. Query the Brain

#### Who knows about a topic or directory?
```powershell
python -m wayfinder who-knows pkg/kubelet
python -m wayfinder who-knows "in-place-resize"
```

#### Who owns a directory (Declared vs Behavioral)?
Shows declared owners in `OWNERS`, flags **Paper Owners** (inactive in the last 180 days), and lists **Unlisted Experts** (top activity without an OWNERS entry):
```powershell
python -m wayfinder ownership pkg/kubelet
```

#### Why was a decision made?
Returns KEP rationales (Motivation, Goals, Non-Goals, Alternatives Rejected), decisions made, and debate arguments with **verbatim supporting quotes**:
```powershell
python -m wayfinder decisions 1287
```

#### What concerns were raised and are they resolved?
Tracks open, claimed-addressed, and resolved concerns:
```powershell
python -m wayfinder concerns 118234
```

#### What documentation or ownership is stale?
Detects documentation stage mismatches, inactive owners, and unanswered questions:
```powershell
python -m wayfinder stale
```

### 5. Incremental Sync (10-minute cycles)
Syncs newly updated activity, closes edges with `valid_to` upon cancellations or edits (preserving time-travel capability), and recomputes derived metrics:
```powershell
python -m wayfinder sync
```

---

## 🧪 Automated Test Suite

Run all unit and integration tests (including live tests against the local FalkorDB Docker container):

```powershell
python -m pytest -v
```

Tests include:
- `test_models.py`: Provenance fields, content hashing, Layer 1, 2, and 3 schema validation.
- `test_parsers.py`: Deterministic parsers for `sigs.yaml`, `OWNERS`, `kep.yaml`, feature gates Go code, doc pages, and Prow commands.
- `test_verifier.py`: Deterministic verbatim quote verification and target ID constraints.
- `test_llm_client.py`: OpenAPI / OpenRouter client configuration, deterministic fallback embeddings, and content-hash caching.
- `test_layer3_derived.py`: 180-day window, 90-day decay half-life, and derived rules.
- `test_falkordb_live.py`: Full end-to-end integration test against the local running FalkorDB Docker instance, validating population, Cypher queries, and "as of" time-travel queries.

Live population reads scoped OWNERS (including ancestors and descendants), aliases,
SIG Node KEP metadata and READMEs, gate definitions at releases in the last year,
and scoped documentation from GitHub. Configure `OWNERS_SCOPE` and `DOCS_SCOPE`
as JSON lists of repository paths/prefixes. Live source failures propagate; fixture
population remains available without `--live`. Activity defaults to all paginated
results updated within the last year; `--limit` explicitly caps a pilot load.

Incremental sync stores its successful activity watermark in the graph, refreshes
structured sources, and extracts new or edited eligible text. Unchanged units skip
semantic writes. Missing LLM credentials skip extraction without caching an empty
model result. Changed relationships retain their previous validity intervals;
withdrawn approvals and retired semantic evidence are excluded from current rules.

`tests/test_regressions.py` exercises vector retrieval, relationship time travel,
extraction validation and edits, source provenance, answers and rationale links,
ownership inheritance, stale-finding reconciliation, collaboration projections,
GitHub pagination, live-source refresh, and incremental cursor failure handling.

Ownership activity is reported as unknown when the import does not cover the
configured expertise window. Uncapped live imports record coverage separately
from the successful sync watermark; limited loads and searches reaching GitHub's
1,000-result ceiling do not establish coverage. Inactivity findings describe
absence of qualifying activity in the imported SIG Node scope, not all GitHub
activity. Activity coverage must reach within the last day to support those
findings. Positive activity evidence can establish an active owner even when
coverage is incomplete.

To repair first-line rationale excerpts and HTML comment artifacts already
stored by older versions, stop any ongoing sync and run:

```powershell
python -m wayfinder.repair
```

This uses saved KEP sections, preserves model-extracted rationales, and
recomputes ownership inactivity flags and findings. It makes no GitHub, LLM,
or embedding API calls. Reference-source refreshes also use the revised parser.

For a bounded activity test on a graph with reference data already populated:

```powershell
python -m wayfinder.pilot --days 30 --items 10 --extractions 20
```

The pilot reuses existing reference data, imports at most 10 activity items,
and attempts extraction of at most 20 comments/reviews, skipping KEP and
documentation sections. Use `--extractions 0` for an activity-only test without
model calls. SDK retries and embedding calls are additional API requests.
The item cap does not establish complete coverage for inactivity findings.
Pilot extraction considers only comments/reviews dated inside the requested
window and skips units already completed with the current pilot prompt revision.
The pilot prompt cache is versioned separately from full reference extraction.
Its diagnostics distinguish schema, quote, and target validation failures.
Extraction sends the JSON schema as a strict API response format. On OpenRouter,
provider routing requires support for that parameter; an unsupported endpoint
fails explicitly rather than silently ignoring it. Answer IDs are constrained
to actual Question nodes, and answers are disabled when no Question is present
in the supplied context. Local quote and target verification still applies.
Directory expertise queries include descendants (for example, expertise in
`pkg/kubelet/prober` is returned when querying `pkg/kubelet`).
Known bots, including `kubernetes-prow`, are excluded from human directory/topic
expertise and unlisted-expert results. Recompute saved derived results without
API calls using `python -m wayfinder.repair --derived-only`.
Graph statistics count retained historical nodes and relationship intervals;
`stale` shows only unresolved findings.
GitHub activity search uses separate `is:pr` and `is:issue` queries because
some token types return empty results for mixed GraphQL searches.
# Company brain MCP server

See [expertise scoring v2](docs/EXPERTISE.md) for repairing activity scores,
backfilling file-specific reviews and configuring hourly offline refreshes.

Wayfinder exposes a local read-only MCP server with 21 tools for knowledge
queries, graph navigation, evidence retrieval and custom read-only Cypher.
See [MCP setup and tool guide](docs/MCP.md) for Codex, Claude and Gemini setup.

```powershell
.\.venv\Scripts\python.exe -m wayfinder.mcp_smoke
```
