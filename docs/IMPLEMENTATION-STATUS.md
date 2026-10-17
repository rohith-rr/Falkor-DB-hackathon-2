# Submission implementation status

Snapshot: October 17, 2026. This report distinguishes implementation checks from live-account verification.

| Approved priority | State | Evidence |
|---|---|---|
| Decision kind fix and validation | Implemented and applied to the two stored demo IDs | Fixture taxonomy regression; pre-fix two-record export retained locally |
| Unified synthetic GitHub, Component expertise, briefs | Implemented | Eight PRs, explicit identities and IMPLEMENTED_IN; Faye four PRs/Dee three; overlap regression |
| Kubernetes compatibility | Verified at fixed clock and published generation | docs/evidence/kubernetes-ranking-compatibility.json |
| Local live extraction | Successful real provider run | docs/evidence/live-extraction-run.json; two generation/three embedding requests; old brief rejected |
| Indexes/full-text/vector/EXPLAIN | Operational native queries verified | docs/evidence/falkordb-capabilities.json; measured cosine-distance self-query |
| Agent vector tool | semantic_search exposed through MCP | Real stdio client transcript and native filtering/metric regression |
| Three-view local dashboard | HTTP/session smoke verified; visual review unavailable | No controllable browser surfaced in the environment |
| New brief/action compatibility | Controlled stub regression verified | docs/evidence/plane-action-regression.json; no real task created |
| Real approved Plane creation | Pending sandbox credential/IDs and exact human approval | Do not represent the stub as live; fallback decision at 6 PM IST October 18 |
| Real MCP client investigation | Protocol session succeeded end to end | docs/evidence/company-mcp-transcript.json; scripted navigation, not independent agent accuracy |
| Actual Plane/Slack sandbox population and readback | Verified on the designated account/project and channels | 12 assigned work items, 20 comments, 6 pages, 30 bot discussion messages plus two channel disclosures; ignored local data/live-workspace/verification.json |
| Live sandbox Wayfinder project revision | All 70 existing records verified against coherent Wayfinder tasks, documents, and staged discussions | Four Done/three In Progress/five Todo; 23 task dependencies; 22 current Slack-to-Plane links; IDs and authors retained; no model calls |
| Live connector MCP navigation | Actual stdio session verified without provider requests | Separate wayfinder_company_live_demo; local verification transcript |
| Live source change propagation | Create/edit/delete in real Plane and Slack verified in the graph, both with direct sync and with only the background watcher running | docs/evidence/live-change-probe-inline.json and live-change-probe-watch.json; 15/15 checks per mode; 62-85 s lag at a 90 s watch interval |
| Offline rebuild of the live graph | Scrubbed recording of 104 real Plane/Slack responses replays through the connectors without credentials | fixtures/live-replay; current records match the live graph; tests/test_live_replay.py includes an identifier leak scan |
| Live connector semantic extraction | Completed with owner approval; low recall | docs/evidence/live-connector-extraction.json; 8 generation/20 embedding requests; 5 accepted (1 Decision, 4 Questions), 1 rejected; both briefs valid |
| Live GitHub connector (own repository) | PRs, file reviews, branches, commits and PR refs, read live from the repository; URL and Plane-key links across GitHub, Plane and Slack | Live read: 3 PRs, 4 branches, 13 commits; tests against a mocked GraphQL API cover merges, deleted branches, moved heads and ambiguous SHAs |
| Embedding model migration | Both company graphs moved to perplexity/pplx-embed-v1-0.6b at 1024 dimensions; vectors re-embedded, indexes rebuilt | capabilities --migrate-embeddings; re-recorded docs/evidence/falkordb-capabilities.json; pre-migration graph copies kept locally |
| Video/script/submission assets | Script, disclosure, setup, model, and query catalog provided | Owner must record/check/upload video and complete submission |

The full suite passes 132 tests (October 17, after the git-history and Plane-key linking work), with fake inference keys and a local FalkorDB. All twelve read-only queries in the query catalog were executed against the demo graph. The action-write example was intentionally not executed.

The feature cutoff is noon October 18 IST. At least the final four hours are protected for submission. No new PageRank/WCC/betweenness demonstration or interactive graph explorer is included. The later October 17 sandbox setup verified the existing live Plane/Slack ingestion paths and added repeatable source population. A live ADR-edit proof and a hash-approved agent-created Plane task are still separate from that seed setup.
