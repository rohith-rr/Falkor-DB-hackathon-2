# Recording script and submission checklist

**Primary track:** Company Brain. **Additional track:** Agents That Act on Connected Data.  
**Deadline:** October 18, 2026, 11:59 PM IST. **Feature cutoff:** noon October 18.

The video is recorded side by side: an MCP agent (Codex or Claude Code) on one half of the screen, and the graph view at **http://127.0.0.1:8765/graph** on the other. The graph view highlights every entity the agent touches and the route between its steps.

## Before recording

1. **Check the live graph:** `.\.venv\Scripts\python.exe scripts/verify_live_workspace.py`. It makes no model calls and fails loudly if anything is off.
2. **Bring the graph up to date:** `.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_company_live_demo --manifest data/live-workspace/manifest.json refresh`.
3. **Start the graph view:** `.\.venv\Scripts\python.exe -m wayfinder.dashboard --graph wayfinder_company_live_demo`, then open `/graph`.
4. **Register the MCP server on the same graph,** with actions enabled ([setup](MCP.md#watch-the-agents-path-in-the-graph)). The repository `.env` may point at another graph, so set `FALKORDB_GRAPH` explicitly. Ask the agent to call `graph_stats` and confirm the graph name.
5. **Start the watcher in its own terminal** for the freshness segment:
   `.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_company_live_demo --manifest data/live-workspace/manifest.json watch --slack --github --interval 60`
6. **Start a fresh agent session for each take,** so **Latest (live)** in the graph view shows only that question.
7. **Optionally,** start a second dashboard on Northstar (`--graph wayfinder_company_demo --port 8766`) for the expertise and edit-proof segment.

## Five-minute demonstration

| Time | Show | Narration and evidence |
|---|---|---|
| 0:00–0:30 | Problem, then the graph view | "Context lives in Plane, Slack and GitHub. Wayfinder connects it in one FalkorDB graph, with evidence for every claim." |
| 0:30–1:15 | Agent: "What happened with linking branches and commits to Plane? Show the task, the Slack discussion, the PR and its commits." | The graph view lights up Slack → FALKORDBTE-15 → PR #4 → branch and commits. "Real records, linked only by URLs and Plane keys found in the text." |
| 1:15–1:30 | Agent: "Which work items and PRs connect the most other work? What is the shortest connection from the Slack announcement to the `git-history` branch?" | `graph_algorithm` runs native betweenness and shortest path on the current graph. The path view highlights Slack message → PR #4 → branch. "Graph algorithms are agent tools, not offline reports." |
| 1:30–2:15 | Agent: "Why must a person approve agent-created tasks? Quote the source." Then: "What don't you know?" | The extracted Decision, with its exact ADR-002 quote and source revision. The agent states low extraction recall and partial commit coverage. |
| 2:15–3:00 | Live freshness: change a task's state in Plane, or post in Slack | Cut to the watcher log line (`updated: 1`, old → new) and ask again. Pre-record this or use a jump cut, since the update takes 60–90 s. Evidence: [watch-mode probe](evidence/live-change-probe-watch.json), 15/15 checks. |
| 3:00–4:00 | Agent: "Draft a follow-up task for the recall gap." | `prepare_plane_work_item` appears in the graph view and the agent shows the hash. You approve in the terminal, the agent executes, and the task appears in Plane. A repeat execute returns the same ID. |
| 4:00–4:40 | Northstar: Expertise view, or `who_knows` | "Dee and Eli own Resource Allocation; Faye isn't an owner but has the most file-review evidence." Optionally show the edit proof: the ADR changes, the old Decision retires, and the old brief fails validation. |
| 4:40–5:00 | FalkorDB capabilities view, then close | Full-text, vector and query-plan evidence. "One graph, real sources, cited answers, safe actions." |

After each agent answer, press **Replay** in the graph view to retrace the route slowly for narration.

**Say plainly on camera:**
- The Plane/Slack content is a staged scenario. The API records, authorship, timestamps and the GitHub repository are real.
- Northstar is fictional.
- Extraction recall was low.
- Cited answers are not an accuracy evaluation.

## Agent prompts

Live graph:

> Confirm the graph is wayfinder_company_live_demo. Investigate Agent Review Workflow (company:northstar-live:component:agent-workflows): owners, Plane work, documents, Slack discussions, linked PRs, branches and commits. Retrieve source evidence and revisions for every claim, state coverage gaps and missing extraction, and prepare a Plane task only when asked. Never grant approval yourself.

Northstar:

> Confirm the graph is wayfinder_company_demo. Investigate company:northstar:component:resource-allocation. Follow owners, Plane work, documents, Slack discussions and IMPLEMENTED_IN code directories. Call who_knows with the Component ID and semantic_search for reservation starvation. Distinguish declared owners from contributors and reviewer authorization. Produce a cited review brief and call validate_review_brief.

## Live source-proof commands (Northstar)

From the repository root, with the configured model and embedding keys:

```powershell
# Stage 1 returns RUN_ID. Start this before changing the source.
.\.venv\Scripts\python.exe -m wayfinder.company.live_proof --enable-live --stage baseline
# Use that same RUN_ID; inspect the pending state before extracting.
.\.venv\Scripts\python.exe -m wayfinder.company.live_proof --enable-live --run-id RUN_ID --stage edit
.\.venv\Scripts\python.exe -m wayfinder.company.live_proof --enable-live --run-id RUN_ID --stage extract --output docs/evidence/another-live-run.json
```

Each run is capped at two generation and twelve embedding requests, with no automatic retries. A failed run can't resume as if it succeeded; start a new run. Re-importing the Northstar fixture resets the local overlay, so don't do it between stages.

## Approved Plane action

A human-approved task was executed live on October 18 against `wayfinder_company_live_demo` (FALKORDBTE-16, [evidence](evidence/plane-action-live.json)). For the video, show that run or approve a fresh one on camera. From the CLI:

```powershell
$g='--graph','wayfinder_company_live_demo','--manifest','data/live-workspace/manifest.json'
.\.venv\Scripts\python.exe -m wayfinder.company @g brief company:northstar-live:component:agent-workflows --output data/live-workspace/brief.json
.\.venv\Scripts\python.exe -m wayfinder.company @g prepare --brief data/live-workspace/brief.json --title "..." --description "..."
# Inspect the exact payload and hash. Approval is by a human, outside MCP:
.\.venv\Scripts\python.exe -m wayfinder.company @g approve ACTION_ID --hash PAYLOAD_HASH --by "Your name"
.\.venv\Scripts\python.exe -m wayfinder.company @g execute ACTION_ID
```

For an uncertain outcome, reconcile the existing action instead of preparing a duplicate. `python -m wayfinder.company.action_check` runs the controlled regression on a disposable graph with a stub client (no real task).

## Final submission checks

- Setup instructions, graph model and [operational Cypher](QUERIES.md).
- [AI use disclosure](AI-USE-DISCLOSURE.md), with synthetic versus live origins and limitations.
- Evidence:
  - [extraction](evidence/live-connector-extraction.json)
  - [local proof](evidence/live-extraction-run.json)
  - [capabilities](evidence/falkordb-capabilities.json)
  - [MCP transcript](evidence/company-mcp-transcript.json)
  - [Plane action](evidence/plane-action-live.json)
  - freshness: [inline](evidence/live-change-probe-inline.json), [watch](evidence/live-change-probe-watch.json)
- Rebuild the [offline replay](../fixtures/live-replay/README.md) once from a clean clone with no `.env`.
- Make the repository and video links accessible to judges. Commit no credentials, sandbox config or backups.
- Check the video plays end to end, and complete the track selections and submission fields before 11:59 PM IST.
- No new feature work after the cutoff. Don't claim unfinished features in the README or video.
