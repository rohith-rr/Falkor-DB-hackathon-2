# Company Brain sandbox

Wayfinder supports an evidence-backed engineering investigation across Plane Cloud
Project Pages/work items and optional Slack channels. This release targets fictional,
publishable sandbox data, not confidential production workspaces. The default company
graph is `wayfinder_company_demo`; the existing Kubernetes graph is preserved.

A separate [live workspace walkthrough](LIVE-WORKSPACE.md) documents the actual
Plane/Slack sandbox populated on October 17, including its separate
`wayfinder_company_live_demo` graph, native source IDs, actual authorship,
repeatable seed runner, and pending external-provider extraction approval.

## Run without accounts or paid requests

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company fixture
.\.venv\Scripts\python.exe -m wayfinder.company brief "Node Runtime" --output data/runtime-brief.json
.\.venv\Scripts\python.exe -m wayfinder.company evaluate
```

The fixture contains two teams, six people, two components, two projects, twelve work
items, twenty issue comments, six pages, and thirty chat messages in six threads.
It also contains eight synthetic GitHub PRs, three OWNERS files, a build bot,
and explicit IMPLEMENTED_IN mappings to code directories. Directory expertise v2
now works within this same company graph and contributes evidence to briefs.
Fifty hand-authored interpretation labels demonstrate provenance and rule behavior.
They are explicitly fixture annotations, not successful LLM extractions or independent
measurements of model accuracy. Fixture URLs identify simulated records and may not
open in a live workspace. SourceRevision text remains available through the graph.

Plane Project Pages are the free documentation solution: store ownership pages,
ADRs, RFCs and runbooks there. Attachments supplement documents. Workspace Wiki,
PDF/DOCX parsing, OCR, DMs, full enterprise access control and complete historical
reconstruction are outside this release.

## Configure live Plane

Copy `fixtures/company/manifest.json` to a local sandbox manifest. Replace workspace,
project IDs and sandbox_project_id with those in your Plane Cloud workspace. Keep
`synthetic: true` only for an actual fictional sandbox. Map actual source user IDs to
fictional people under `identities`; matching display names are never identity proof.
Update bindings to the native IDs of your sandbox records. Do not use demo fixture IDs
as if they were real Plane UUIDs. No workspace is created by normal sync.

Add `PLANE_API_KEY` to your local `.env` (never commit it), then:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company --manifest data/sandbox.json check-plane
.\.venv\Scripts\python.exe -m wayfinder.company --manifest data/sandbox.json sync
# Explicit paid extraction, capped at twenty source units:
.\.venv\Scripts\python.exe -m wayfinder.company --manifest data/sandbox.json extract --limit 20
```

The connector uses documented API v2 endpoints and reports unavailable capabilities.
All configured projects are exhaustively listed on each run; source hashes avoid
duplicate revisions. Full scans intentionally favor deletion reconciliation for the
small sandbox. Coverage watermarks are advanced only for successfully listed scopes;
they are not proof of company-wide activity coverage. A failed scope is retried next run.
Source ingestion is not a globally atomic graph snapshot.

Work-item attachments are linked metadata. Only UTF-8 Markdown/plain text up to 1 MiB
is eligible for content ingestion. Attachment redirects require explicitly allowlisted
`plane.attachment_hosts`; API credentials are never forwarded to storage hosts.

If Pages are unavailable through the account API, use an explicit JSON export:

```json
{"synthetic":true,"exported_at":"2026-10-15T10:00:00Z","projects":[
  {"id":"YOUR_PROJECT_ID","name":"Runtime","pages":[
    {"id":"ORIGINAL_PAGE_ID","name":"ADR","description_html":"<p>Decision text</p>","updated_at":"2026-10-15T10:00:00Z"}
  ]}
]}
```

Run `python -m wayfinder.company --manifest data/sandbox.json import-pages data/pages.json`.
Exports are partial snapshots, not live synchronization; they never prove deletion.

## Optional Slack

Set `SLACK_USER_TOKEN` or `SLACK_BOT_TOKEN` with appropriate channel-history and
thread-reply access; the latter must actually work for the selected token. Add only
fictional public sandbox channel IDs under `slack.channel_ids`. Use `sync --slack`.
History and replies are paginated separately. Polling revisits known threads and
uses a thirty-day history scope with five minutes of overlap. Retention, permissions,
deleted parent threads, and rate limiting are reported as gaps, not empty evidence.
No messages are posted and no Slack files are downloaded.

Optional scheduled polling:

```powershell
.\scripts\schedule_company.ps1 -Install -Manifest data/sandbox.json
# Add -Slack only when Slack is configured. Remove with -Remove.
```

Nothing registers this task automatically. It runs under the logged-in user every ten
minutes, without overlapping instances. Check Task Scheduler for errors.

## Agent investigation through MCP

Set `FALKORDB_GRAPH=wayfinder_company_demo` in the MCP client's environment and restart
its server. The default server has 22 database-read-only tools and three prompts. Use
`review_component("Node Runtime")` or ask:

> Investigate Resource Allocation. Navigate from owners to Plane work items, ADRs,
> discussions and unresolved concerns. Retrieve source revisions and produce a cited
> review brief. Distinguish proposals, decisions and verified resolution. State gaps.

The agent must call schema/coverage, resolve IDs, follow neighbors, read evidence,
and validate the brief. `company_context` is an entry point, not permission to invent
links. `validate_review_brief` checks structure, IDs, URLs, current revisions and quote
presence; it does not prove a summary follows from its quote. Component ownership is
not code reviewer authorization. Company ticket/chat volume is not v2 code expertise.

`semantic_search` explicitly makes one query-embedding request. Prepare demo indexes
using `python -m wayfinder.company.capabilities`; use `--enable-live` to prepare real
vectors and record native full-text/vector/EXPLAIN evidence. The company MCP smoke
now includes semantic search and therefore makes one explicit embedding request:
`python -m wayfinder.company.smoke`. It is not part of the zero-request offline path.

See [local source overlay and recording instructions](DEMO.md), the
[three-view dashboard setup](../README.md), and the [operational Cypher](QUERIES.md).

## Proposed Plane task and human approval

Action MCP tools exist only with `--enable-actions --manifest data/sandbox.json`.
There is no MCP tool that grants approval. Proposal writes affect the local graph;
execution writes one work item in the configured sandbox project. The ordinary read
tools remain database-enforced read-only. Local CLI/shell access is a trusted boundary,
not an enterprise authorization system.

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company --manifest data/sandbox.json prepare --brief data/runtime-brief.json --title "Review rollback evidence" --description "Inspect the linked sources before rollout."
# Inspect the exact returned payload and hash, then approve interactively:
.\.venv\Scripts\python.exe -m wayfinder.company --manifest data/sandbox.json approve ACTION_ID --hash PAYLOAD_HASH --by "Your name"
.\.venv\Scripts\python.exe -m wayfinder.company --manifest data/sandbox.json execute ACTION_ID
```

Changed evidence invalidates execution. Repeated successful execution returns the
existing Plane ID. A timeout produces an uncertain state: subsequent calls reconcile
by external action ID and never blindly post again. If not found, inspect Plane before
proposing a different task. No live Plane task is created during automated tests.

## Offline repair and evaluation

```powershell
# Use the actual graph explicitly. Dry-run is default:
.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_test_30d repair-knowledge
# A fresh backup path inside this repository is mandatory for apply:
.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_test_30d repair-knowledge --apply --backup data/backups/knowledge-before.json
```

Repair relabels parser alternatives as considered, removes invented status transition
wording, and recomputes concern status from explicit concern-specific resolution.
It makes no LLM requests. Backups preserve node/edge properties, including vectors;
they are sensitive artifacts and are not automatically committed or restored.

`fixtures/company/evaluation.json` freezes ten development and twenty distinct held-out
questions. Preserve the agent tool transcript and answers, then independently score
each held-out answer against its source and limitations. Release target: at least
18/20 semantically correct answers, all factual claims traceable, and zero false concern
closures or reviewer-authorization claims. Mechanical `evaluate` reports semantic
accuracy as pending; it must never manufacture a passing human-review score.

To score an independently reviewed run, use `evaluate --answers data/answers-review.json`.
The JSON must include a `reviewer` name and an `answers` array containing exactly the
twenty held-out IDs. Each entry includes `id`, the structured `brief`, a human-reviewed
`semantic_pass` boolean, `false_concern_closure: false`, and
`invented_reviewer_authorization: false`. Missing safety reviews fail the gate.
The evaluator checks current evidence independently of the supplied semantic score.
