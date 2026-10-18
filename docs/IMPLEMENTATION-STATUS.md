# Submission implementation status

Snapshot: October 18, 2026. This report separates implementation checks from verification on the live accounts.

## Northstar demo graph (`wayfinder_company_demo`, synthetic)

| Capability | State | Evidence |
|---|---|---|
| Decision kinds and validation | Implemented; the two stored demo IDs corrected | Fixture taxonomy regression |
| Synthetic GitHub, Component expertise, briefs | Implemented | Eight PRs, explicit identities and IMPLEMENTED_IN; Faye four PRs, Dee three; overlap regression |
| Local live extraction proof | Successful real provider run | docs/evidence/live-extraction-run.json: two generation and three embedding requests; the old brief is rejected |
| Native full-text, vector, EXPLAIN | Verified with the current embedding model | docs/evidence/falkordb-capabilities.json |
| Agent vector tool | `semantic_search` through MCP | Real stdio transcript; native filtering and metric regression |
| Evidence dashboard | HTTP/session tests; checked in a browser on October 18 | Three evidence views at `/` |
| Action flow regression | Controlled stub verified | docs/evidence/plane-action-regression.json (no real task) |
| Scripted MCP investigation | Protocol session succeeded end to end | docs/evidence/company-mcp-transcript.json; scripted, not an accuracy evaluation |

## Live connector graph (`wayfinder_company_live_demo`, real API records)

| Capability | State | Evidence |
|---|---|---|
| Plane/Slack sandbox population and readback | Verified on the designated project and channels | 12 seeded tasks, 20 comments, 6 pages, 30 bot messages plus 2 disclosures; `scripts/verify_live_workspace.py` passes |
| Live GitHub connector (this repository) | PRs, reviews, branches, commits and PR refs read live; URL and Plane-key links across GitHub, Plane and Slack | Latest read: 6 PRs, 7 branches, 21 commits; mocked-API tests cover merges, deleted branches, moved heads, ambiguous SHAs |
| Freshness | Create, edit and delete in Plane and Slack reach the graph, with direct sync or only the watcher running | docs/evidence/live-change-probe-*.json: 15/15 checks per mode, 62–85 s lag at a 90 s interval |
| One-command refresh | `refresh` syncs every configured source; `--extract N` extracts pending units, pages first | Live run: three sources in about 92 s, no model calls without `--extract` |
| Semantic extraction | Owner-approved bounded runs; low recall | docs/evidence/live-connector-extraction.json (eight units, mistral-nemo); later runs on three Plane comments and the product-brief page with deepseek-v4-flash. 10 current claims: 1 Decision, 3 Rationales, 2 Arguments, 4 Questions |
| Human-approved Plane action | Verified live: validated brief, owner approval against the payload hash, one task created, the repeat returned the same task | docs/evidence/plane-action-live.json (FALKORDBTE-16), then ingested and linked by the next sync |
| Graph algorithms as agent tools | `graph_algorithm`: native PageRank, betweenness, connected components and shortest path over a temporary projection of current records | tests/test_graph_algorithms.py (retired history excluded, no leftover projection, traced path edges); live run: Slack message → PR #4 → `git-history` branch |
| Wayfinder-Bench | 45 questions committed before running, with gold from the sources; graph tools vs vector vs BM25 | benchmarks/wayfinder_bench/RESULTS.md: graph F1 0.973 (36/37 exact, 8/8 unanswerable correctly empty); vector 0.106; BM25 0.084 |
| Agent path view | `/graph` draws the graph and highlights each MCP call's entities and route, live | Browser check with a 7-call scripted session: 13 entities highlighted in order |
| Embedding migration | Both company graphs on perplexity/pplx-embed-v1-0.6b at 1024 dimensions | `capabilities --migrate-embeddings`; pre-migration graph copies kept locally |
| Offline rebuild | A scrubbed recording of 104 real Plane/Slack responses replays through the connectors without credentials | fixtures/live-replay; tests/test_live_replay.py includes a leak scan |

## Tests

The full suite passes 140 tests, with fake inference keys and a local FalkorDB. All twelve read-only queries in the query catalog run against the demo graph; the action-write example is intentionally not executed.

## Remaining before submission

- **Record the video.** See [DEMO.md](DEMO.md) for the side-by-side agent and graph setup.
- **Make the repository readable to judges.** It is currently private.
- **Optional:** extract the two remaining pages (the review checklist and runbook) with `refresh --extract 2`.
- **Partly done:** Wayfinder-Bench measures retrieval with a templated planner. An LLM-agent run (the same model with graph tools, with vector search, or with everything in one prompt) and independent review of answers (FALKORDBTE-11) remain.
