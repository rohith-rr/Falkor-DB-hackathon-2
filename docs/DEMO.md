# Recording script and submission checklist

**Primary track:** Company Brain. **Additional track:** Agents That Act on Connected Data.  
**Deadline:** October 18, 2026, 11:59 PM IST. **Feature cutoff:** noon October 18.  
**Plane fallback decision:** 6 PM October 18. **Protected final submission window:** at least 7:59–11:59 PM.

## Before recording

1. Confirm graph `wayfinder_company_demo`, service readiness, clocks, and the recorded provider evidence. Keep the Kubernetes graph separate.
2. Run the real MCP-client investigation, including Component who_knows, semantic_search, source evidence, and brief validation. It makes one explicit query-embedding request. Save the transcript.
3. Verify prepare/approve/execute with the contributor-bearing brief against the controlled stub. Verify one actual approved task against the fictional Plane sandbox once credentials/IDs are configured; retain the external task ID and repeat-execution result.
4. Confirm source/brief state before recording. Reimporting the fixture resets the local overlay; it must not happen between the baseline/edit/re-extraction steps.
5. Choose recorded proof replay or an explicitly enabled new live run. State which is shown. A recorded real-provider result is not a new on-camera request.
6. Check browser display manually. Automated dashboard HTTP/session smoke tests pass, but no controllable browser was available during implementation for visual QA.
7. Start the live freshness watcher in a separate terminal and leave it running for the whole recording. It needs credentials; see [the freshness section](LIVE-WORKSPACE.md#freshness-does-the-graph-follow-plane-and-slack-edits):
   `.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_company_live_demo --manifest data/live-workspace/manifest.json watch --slack --interval 60`

## Five-minute demonstration

| Time | Show | Narration / evidence |
|---|---|---|
| 0:00–0:50 | MCP client investigates Resource Allocation | “The answer crosses people, work, documents, discussions, and code in one FalkorDB graph.” Confirm graph and coverage, resolve the stable Component, inspect context and code directories. |
| 0:50–1:35 | Expertise view or MCP who_knows | “Dee and Eli are declared owners. Faye is not an owner, but has the strongest imported file-review evidence.” Show four supporting peer-review PRs for Faye, three merged authored PRs for Dee, per-channel scores and clocks. Do not call Faye an authorized approver. |
| 1:35–2:30 | Live proof: baseline → edit → pending | Show the actual source text. Before: automatic retry at most twice. After: automatic retries disabled, operator retry required. Show changed SourceRevision and retired old Decision before invoking extraction. Label LOCAL OVERLAY explicitly. |
| 2:30–3:05 | Old brief validation failure, then replacement | Show the validator rejecting retired evidence/stale revision. Re-extract with the configured model or inspect the recorded real run. Show the different operator-only Decision, quote, target, LLM_EXTRACTED origin, and a valid replacement brief. |
| 3:05–3:40 | FalkorDB capabilities | Inspect one full-text result, semantic_search result, source evidence, and EXPLAIN output. Copy an IMPLEMENTED_IN query into Browser. Similarity is one minus the pinned server's native cosine distance; it is not confidence. |
| 3:40–4:30 | Approved Plane task | Prepare a narrow follow-up from the current brief. Display payload/hash, human approval, execute, then show the task's returned ID. A repeated execution must reuse the result. If using the stub, show a persistent **SIMULATED — no live Plane task created** label. |
| 4:30–5:00 | Live freshness: Plane edit reaches the graph | Make the edit during the 3:40 segment: change one task's state in the live Plane project. Watch lag is about 60–90 s, so by 4:30 the watcher has synced. Show its log line (`updated: 1`, with the old and new state) and the Issue's new state with a Browser query on `wayfinder_company_live_demo`. “No re-import: the watcher polled Plane.” A description edit would also retain the previous text as a SourceRevision. Cite the [watch-mode probe](evidence/live-change-probe-watch.json): 15/15 checks, 62–85 s lag. |

The Kubernetes appendix (`wayfinder_test_30d`, pkg/kubelet v2 expertise and KEP 1287 decisions) is now optional. Show it only if an earlier segment runs short. Narration: “The same engine supports scoped public engineering knowledge; Kubernetes data was not copied into Northstar.”

## Side-by-side recording: agent and graph

Put Codex (or Claude Code) on one half of the screen and **http://127.0.0.1:8765/graph** on the other, both on `wayfinder_company_live_demo`. Setup is in [MCP.md](MCP.md#watch-the-agents-path-in-the-graph). Start a fresh agent session for each take, so **Latest (live)** shows only that question. Good questions:

- "What happened with linking branches and commits to Plane? Show the task, the Slack discussion, the PR and its commits." The path runs Slack → FALKORDBTE-15 → PR #4 → branch and commits.
- "Why must a person approve agent-created tasks? Quote the source." The path runs from the component to the extracted Decision and the ADR-002 page.
- "Draft a follow-up for the recall gap." The `prepare_plane_work_item` step appears in the list; approval stays in your terminal.

After the answer, press **Replay** to retrace the route slowly for the narration.

## Suggested MCP agent prompt

> Confirm the graph is wayfinder_company_demo. Investigate company:northstar:component:resource-allocation. Check schema and coverage; follow owners, Plane work, documents, Slack discussions, and IMPLEMENTED_IN code directories. Call who_knows with the Component ID and semantic_search for reservation starvation. Inspect the supporting PRs, file-review evidence, original sources, and source revisions. Distinguish declared owners from contributors and reviewer authorization. Produce a cited review brief and call validate_review_brief. State clocks, import gaps, and what remains unresolved. Prepare a Plane task only when asked; never grant approval yourself.

## Live source-proof commands

From the repository root, with the configured model/embedding keys:

```powershell
# Stage 1 returns RUN_ID. Start this before changing the source.
.\.venv\Scripts\python.exe -m wayfinder.company.live_proof --enable-live --stage baseline
# Use that same RUN_ID; inspect pending state before extracting.
.\.venv\Scripts\python.exe -m wayfinder.company.live_proof --enable-live --run-id RUN_ID --stage edit
.\.venv\Scripts\python.exe -m wayfinder.company.live_proof --enable-live --run-id RUN_ID --stage extract --output docs/evidence/another-live-run.json
```

There are no automatic retries. Each run is capped at two generation/twelve embedding requests. A failed run cannot resume as if successful; investigate and start a new explicitly requested run. Baseline/current revisions and a run ownership marker protect against resuming over another overlay. Live controls are separate from ordinary graph reads.

## Plane action prerequisite and fallback

The controlled regression is runnable with `python -m wayfinder.company.action_check`.
It uses an isolated disposable graph and a stub client; its saved evidence explicitly
states that no live task was created. It does not approve a real task on the human's behalf.

Use a synthetic sandbox manifest with the real workspace slug, actual allowed project IDs, and sandbox_project_id. Put PLANE_API_KEY in local `.env` or the process environment, never Git or a chat message. Plane credentials and the live sandbox IDs are now configured locally in the ignored `data/live-workspace/`. A human-approved task was executed live on October 18 against `wayfinder_company_live_demo` (FALKORDBTE-16, [evidence](evidence/plane-action-live.json)). The 6 PM simulated fallback is no longer needed. For the video, either show this recorded run or prepare and approve a fresh action on camera.

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company --manifest data/sandbox.json check-plane
.\.venv\Scripts\python.exe -m wayfinder.company brief "Resource Allocation" --output data/allocation-brief.json
.\.venv\Scripts\python.exe -m wayfinder.company --manifest data/sandbox.json prepare --brief data/allocation-brief.json --title "Verify allocation retry safety" --description "Review the operator-only retry decision and unresolved reservation starvation evidence before rollout."
# Inspect the exact returned payload/hash. Approval is by the human, outside MCP:
.\.venv\Scripts\python.exe -m wayfinder.company --manifest data/sandbox.json approve ACTION_ID --hash PAYLOAD_HASH --by "Your name"
.\.venv\Scripts\python.exe -m wayfinder.company --manifest data/sandbox.json execute ACTION_ID
```

For an uncertain request outcome, reconcile the existing action rather than preparing a duplicate. If a live check is not passing by **6 PM IST October 18**, use the controlled-stub result with the explicit SIMULATED video label. This is a disclosed fallback, not live execution evidence.

## Final submission checks

- Complete local setup instructions, graph model, and [operational Cypher](QUERIES.md).
- [AI assistant use disclosure](AI-USE-DISCLOSURE.md), synthetic/fixture/live distinctions, and limitations.
- Committed extraction evidence, Kubernetes compatibility comparison, native query plans, and MCP transcript.
- Real approved Plane task evidence: [plane-action-live.json](evidence/plane-action-live.json).
- Live freshness evidence ([inline](evidence/live-change-probe-inline.json), [watch](evidence/live-change-probe-watch.json)). Rebuild the [offline replay](../fixtures/live-replay/README.md) once from a clean clone with no `.env`.
- Repository and video links accessible to judges; no credential files, API keys, private sandbox config, or sensitive backups committed.
- Video playable from beginning to end; final track selections and submission fields complete before 11:59 PM IST.
- No new feature work after noon October 18. Unfinished features are not claimed in the README or video.
