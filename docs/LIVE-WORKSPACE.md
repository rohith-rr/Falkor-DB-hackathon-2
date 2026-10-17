# Live Plane and Slack sandbox

The October 17 setup populated the user-designated **Falkor DB Test Data Project** in Plane and two public Slack demo channels. These are real API records with native IDs, timestamps, assignments, and source URLs. The current dataset describes the **Wayfinder project itself**, with staged discussions and task status based on recorded implementation checks and explicit outstanding work. See the [project story and backlog](WAYFINDER-PROJECT-DEMO.md).

| Surface | Populated records |
|---|---|
| One designated Plane project | 12 work items, 20 comments, 6 Project Pages |
| Runtime Slack channel | 3 discussion threads, 15 discussion messages, 1 channel disclosure |
| Allocation Slack channel | 3 discussion threads, 15 discussion messages, 1 channel disclosure |
| Separate FalkorDB graph | `wayfinder_company_live_demo` |

The Northstar `wayfinder_company_demo` graph was not reseeded or replaced. The dashboard serves whichever graph `--graph` names (Northstar by default); use `--graph wayfinder_company_live_demo` and open `/graph` for this one.

## Authorship and evidence boundary

Plane work items are assigned to the actual project member. Plane creation/comments retain the personal-access-token account as their author. Slack messages retain the actual bot identity. No six-person workforce, independent employee corroboration, or human Slack authorship is manufactured.

The manifest explicitly declares that sandbox member as owner of **Source Ingestion & Evidence** and **Agent Review Workflow**. This is a configured project responsibility, not discovered organizational truth. It maps the real Plane user ID to a canonical sandbox identity. The Slack bot remains a bot and is not mapped to a human owner.

Each Slack channel has one staged-discussion disclosure. Discussions use concise project language and relevant labeled source links; bookkeeping IDs are in message metadata and the local checkpoint rather than repeated visible footers. The twelve tasks distinguish four completed implementation checks, three items in progress, and five Todo validation/release tasks. Staged conversation is not independent employee corroboration, and a scripted check is not answer accuracy. Tickets and chat volume do not establish code expertise. Code activity comes from this repository's pull requests through the [live GitHub connector](#live-github-connector); expertise there reflects one main author, so use Northstar for the owner-versus-expert contrast.

Events use actual source-system timestamps. There is no backdated company history or fixture clock anchor in this graph.

## Local configuration and repeatable population

Credentials remain in the ignored `.env` file: `PLANE_API_KEY` and `SLACK_BOT_TOKEN`; a separately configured `SLACK_USER_TOKEN` can be used for reading if needed. The read connector loads these locally. Slack requires readable channel history and thread replies; the bot must be invited to the two explicitly scoped public channels.

Account-specific scope, source response snapshots, record IDs, and evidence transcripts are kept under ignored `data/live-workspace/`. Do not publish that directory or credential exports.

The populated setup has:

- `scope.json`: approved workspace, project, Slack team, and two channel IDs.
- `seed-plan.json`: reviewable source payloads and authorship contract.
- `seed-state.json`: confirmed native IDs and pending request journal.
- `manifest.json`: identity mapping and bindings built from returned real source IDs.
- `verification.json`: connector readback and actual scripted MCP session.
- `provider-transfer-review.json`: exact eight selected source texts/revisions and external request limits, ready for review before provider calls.

The population runner makes no LLM or embedding requests. Default mode performs read-only preflight and saves a local plan:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company.live_seed --scope data/live-workspace/scope.json
```

Applying the plan creates records only in that scope:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company.live_seed --scope data/live-workspace/scope.json --apply
```

Successful seed records are reused, without overwriting later source edits. Plane records carry stable external import IDs; Slack messages carry seed metadata, with support for the original visible markers during migration. A request is journaled before creation. An uncertain outcome must reconcile to the recorded native ID, external ID, or message metadata/legacy marker; no blind retry is made. There is no automatic cleanup/delete operation. Preserve the checkpoint and do not run competing seed processes.

### Revising the original content

The initial connector-test wording and the later infrastructure scenario were superseded by `fixtures/company/wayfinder-content.json`. It defines Wayfinder tasks, six matching documents, six discussion threads, states, priorities, dates, and a dependency graph. The twelve tasks cover connector ingestion, identity, freshness, retrieval, MCP, bounded live extraction, approvals, dashboard errors, evaluation, and submission. The rewrite updates the original 68 native records plus their two disclosure messages in place. It preserves IDs, authors, and reply timestamps and does not impersonate employee accounts. `live-content.json` remains the previous infrastructure revision, not the current population source.

The source revision command checks external seed IDs and bot ownership, saves native source backups and a before/after plan, and refuses to overwrite text changed outside this migration. Normal seed reruns do not overwrite source content; this explicit revision path is separate:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company.live_seed_polish --scope data/live-workspace/scope.json
# Inspect data/live-workspace/content-update-plan.json before applying:
.\.venv\Scripts\python.exe -m wayfinder.company.live_seed_polish --scope data/live-workspace/scope.json --apply
```

Repeat the connector sync after source changes. Historical SourceRevisions can retain earlier stories; current source text should contain the Wayfinder revision. When migrating the earlier graph context, `scripts/update_live_graph_story.py` retires only its two old infrastructure Components, retaining their closed history and rebuilding the explicit current owner context. It does not delete records or affect the other graphs. A prior provider-transfer review is invalid when its selected source revisions change. Review the new text before enabling external inference; a content rewrite does not authorize model calls.

## Live GitHub connector

The live graph can also read this project's own repository. GitHub's GraphQL search returns pull requests, reviews, file-level review threads, comments and issues. These go through the same ActivityProcessor and v2 expertise rules as the Northstar fixture.

Links between code and work are explicit only:

- A PR references a Plane work item when its title or body contains that item's URL.
- A PR concerns a Component when it changes files under a directory that `scope.json` maps to that Component (`github.components`, copied into the manifest).
- The `rohith-rr` login maps to the same sandbox owner as the Plane account. Reviewers stay separate GitHub identities.

PR records are marked `data_mode: live` and `synthetic: false`, because the code is real even though the Plane/Slack scenario is staged.

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_company_live_demo --manifest data/live-workspace/manifest.json sync --slack --github
# or keep it current alongside Plane/Slack:
.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_company_live_demo --manifest data/live-workspace/manifest.json watch --slack --github --interval 90
```

The same sync also reads git history with one bounded GraphQL request:

- **Branch** nodes (current branches, with `is_default` and tree URL) hang off the Repository via `HAS_BRANCH`. A branch moves through `HEAD_AT`; earlier heads stay as closed intervals. A deleted branch is retired.
- **Commit** nodes are keyed by SHA: headline, message, dates, size, parent SHAs, and the GitHub author mapped through `identities`. They connect through `HAS_PARENT` and `HAS_COMMIT` (from the branch's recent history and from each PR).
- **Pull requests** gain `head_ref`, `base_ref`, `head_sha` and `merge_sha`, plus `FROM_BRANCH`, `INTO_BRANCH` and `MERGED_AS` edges.

Cross-source links extend to git:

- `/tree/<branch>[/<path>]` resolves to the longest matching known branch.
- `/commit/<sha>` and `/pull/n/commits/<sha>` resolve to the commit. An abbreviated SHA resolves only if exactly one known commit matches it.
- A Plane work-item key such as `FALKORDBTE-12` in a branch name, commit message, PR, Slack message or Plane text links to that work item (`link_method: EXACT_IDENTIFIER`). Plane supplies the key; `FALKORDBTE-120` or `XFALKORDBTE-12` do not match. When text only quotes a key as an example and can't be edited, such as a merged commit message, add a `link_exclusions` entry (`source`, `target`, `reason`) to `scope.json`. The manifest carries it, and the next sync retires that link.

The latest sync (October 18) read 6 PRs, 7 branches and 21 commits from this repository.

Prerequisites and limits:

- `GITHUB_TOKEN` must be able to read the repository. For a fine-grained token, grant Metadata, Contents and Pull requests read access on this repository. Without it, the sync records GitHub coverage as `unavailable` and continues.
- Commits are imported from current branch heads and PRs within the configured window (at most 100 per branch). Their coverage is `partial`. Commits never count toward code expertise; only reviewed PRs do.
- A force-push can leave an old `HAS_COMMIT` edge from the branch; `HEAD_AT` always reflects the current head.

## Offline replay for reviewers

Reviewers without these credentials can rebuild the same current graph from [`fixtures/live-replay`](../fixtures/live-replay/README.md). It is a scrubbed recording of one real connector read, replayed through the connectors. Run `python -m wayfinder.company.replay load --reset` to write `wayfinder_company_live_replay`. Account identifiers are replaced consistently. Re-record with `replay record` after source changes.

## Real connector ingestion

The source records were read through the implemented Plane and Slack connectors, rather than inserted directly as graph fixtures:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_company_live_demo --manifest data/live-workspace/manifest.json sync --slack
```

Sync does not run semantic extraction unless explicitly requested. Native source IDs determine graph IDs and explicit bindings. Slack membership events are excluded from thread retrieval. Coverage records distinguish exhaustive Plane scopes from bounded Slack history; successful ingestion does not establish complete company history.

The initial readback verified 12 assigned Issues, 6 DocPages, 50 Comments, 18 Threads, two Components, and bot authorship for all thirty discussion messages. The content revision adds two disclosure messages/threads, bringing current expected counts to 52 Comments and 20 Threads. Native indexes were prepared without provider requests. These are measurements of the recorded setup runs, not guaranteed counts after later source edits or imports.

## One-command refresh

`refresh` is the everyday command. It syncs every source the manifest configures: Plane always, Slack when channels are listed, GitHub when a repository is set. Then it reports or extracts what is still unextracted.

```powershell
# Sync only; no model calls. Lists pending units, pages first:
.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_company_live_demo --manifest data/live-workspace/manifest.json refresh
# Sync, then extract up to 3 pending units (one generation call each, plus embeddings):
.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_company_live_demo --manifest data/live-workspace/manifest.json refresh --extract 3
```

- **Pending** means a Plane/Slack unit with no extraction for its current text: never extracted, or edited since, because a source edit clears the old extraction. The order is document sections, then the newest Plane comments, then Slack messages.
- **Budget:** `--extract N` caps the units, each one generation call. The default 0 never calls a model.
- **Output:** a compact summary of sources, changes, extraction results with the model used, and what remains. The full change set is appended to `data/sync-log-<graph>.jsonl`.
- **Provenance:** each extracted claim records `llm_model`, so claims from different models stay distinguishable.

`sync`, `watch` and `extract --unit-id` remain for finer control.

## Freshness: does the graph follow Plane and Slack edits?

Every sync now reports what it changed (`changes`: created, updated with old/new state/title, retired, and edge changes), so freshness is observable rather than assumed. A re-sync of unchanged sources reports zero changes.

Keep the graph updated automatically with the polling watcher (each pass appends its change set to the ignored `data/sync-log-<graph>.jsonl`):

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company --graph wayfinder_company_live_demo --manifest data/live-workspace/manifest.json watch --slack --interval 90
```

Or install the Windows scheduled task, which now requires an explicit graph so live records cannot land in `wayfinder_company_demo`:

```powershell
.\scripts\schedule_company.ps1 -Install -Manifest data/live-workspace/manifest.json -Graph wayfinder_company_live_demo -Slack -IntervalMinutes 2
```

`scripts/live_change_probe.py` proves the loop end to end on the real accounts. It creates one labeled Plane work item and comment and one Slack root and reply, then edits the title, description, state, comment and reply, then deletes all four. After each stage it checks the graph. It touches only its own records and makes no model requests. Without `--apply` it is a dry run.

```powershell
# Probe runs the connector sync itself (connector correctness on real payloads):
.\.venv\Scripts\python.exe scripts/live_change_probe.py --apply --mode inline
# Probe never syncs; a running watcher/scheduled task must pick the changes up (automatic update + lag):
.\.venv\Scripts\python.exe scripts/live_change_probe.py --apply --mode watch
```

October 17 results ([inline](evidence/live-change-probe-inline.json), [watch](evidence/live-change-probe-watch.json)): all 15 checks passed in both modes. With a 90-second watcher, creations were visible after 62-72 s, edits after 80-85 s and deletions after 65-70 s. The previous description was retained as a SourceRevision.

Limits: this is polling, not push. One full pass is about 60 Plane requests and takes 40-80 s under Plane's per-minute rate limit, so lag below about a minute needs incremental fetches or Plane webhooks / the Slack Events API (both need a public endpoint). Plane task dependencies come from the manifest, so a relation added in the Plane UI is not ingested. Slack coverage stays bounded to 30 days of history plus previously seen threads.

## Semantic extraction

**Eight-unit run, October 17.** The owner approved sending the eight selected source units to OpenRouter. Before the run, both company graphs were moved to the new embedding model, and the reviewed source texts were rechecked against the graph.

- **Configuration:** mistralai/mistral-nemo for generation; perplexity/pplx-embed-v1-0.6b at 1024 dimensions for embeddings.
- **Requests:** 8 generation and 20 embedding requests.
- **Proposals:** 6 proposed. 5 were accepted, all quote-verified, and 1 was rejected for an invalid target.
- **What was extracted:** one Decision ("require human approval of agent-created work", Agent Review Workflow) and four Questions. Both component briefs validate.

Recall was low. The source-ingestion ADR yielded topics but no Decision, and the interpretation-accuracy concern was proposed as a Question, not a concern. No concern status changed. This is real model output and is not an accuracy evaluation. The publishable summary, without native IDs, is [live-connector-extraction.json](evidence/live-connector-extraction.json).

The prepared bounded runner selects eight imported source units: the source-ingestion ADR, exact-payload-approval ADR, extraction-pilot RFC, Plane body-readback discussion and resolution comments, interpretation-accuracy concern comment, and one Slack thread root for each current workstream. It uses the configured generation and embedding models; maximum eight generation requests and sixty embedding requests, SDK retries disabled, maximum two thousand generation output tokens per request. Existing valid extraction materializations can be skipped. These are request/token bounds, not a quoted dollar cost.

Re-running requires the same explicit approval and an unchanged transfer review:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company.live_seed_extract --enable-live
```

The runner saves actual proposed model results, validation diagnostics, request counts, and brief validation in `data/live-workspace/extraction-report.json`. It imports no fixture annotations. Empty or rejected output is retained as a result; it does not fabricate a successful semantic demonstration. Existing source-text ingestion remains useful while inference is pending, but Decision/concern answers must report missing extraction.

**Later extraction.** Further units are extracted with `refresh --extract N` (pending units, pages first) or `extract --unit-id`, under the model configured at the time:

- A three-unit run on Plane comments, before the model change, accepted 1 Question.
- The product-brief page, with `deepseek/deepseek-v4-flash`, gave 5 accepted claims: 2 ownership Arguments and 3 Rationales (problem, goal, non-goal).

As of October 18, the graph holds 10 current extracted claims (1 Decision, 3 Rationales, 2 Arguments, 4 Questions) from 10 units. The review checklist and runbook pages are still pending. Since October 18, claims record `llm_model`; earlier claims don't, and the [AI use disclosure](AI-USE-DISCLOSURE.md) lists which model produced them.

The setup does not edit the live ADR after seeding, run the original local-overlay proof on this graph, or bypass the exact-payload approval required for a later agent-created Plane action.

## MCP testing

Use the existing MCP entry point with this graph override, then restart the agent connection:

```json
"env": {
  "FALKORDB_GRAPH": "wayfinder_company_live_demo"
}
```

Ask the agent to verify graph_stats, inspect coverage, and investigate **Agent Review Workflow** using stable ID `company:northstar-live:component:agent-workflows`, or **Source Ingestion & Evidence** using `company:northstar-live:component:source-ingestion`. The namespace is retained for stable source identity; the displayed organization is Wayfinder Project. Use modest context/page limits and follow pagination; including too many complete source records can exceed the MCP response bound.

Example questions:

> Confirm the graph and inspect Wayfinder's Agent Review Workflow: owner, assigned Plane work items, pages, Slack discussions, and linked PRs, branches and commits. Which tasks are done and which remain? Cite source evidence and state missing extraction and coverage.

> Explain why Wayfinder separates source ingestion from inference and requires approval of an exact Plane payload. Cite the source revisions, distinguish the pilot proposal from completed validation, and say what remains unextracted.

To watch the agent's path through this graph while it answers, see [MCP.md](MCP.md#watch-the-agents-path-in-the-graph).

The read-only protocol check makes no provider requests:

```powershell
.\.venv\Scripts\python.exe scripts/verify_live_workspace.py
```

It verifies the seeded records by native ID (counts, assignments, bot attribution, text, and forward-only task states), coverage, references and dependencies, and runs an actual MCP navigation and brief-validation session. It reports GitHub counts and the extraction state. Records added later, such as the sync-test scenario and approved actions, don't make it fail. It is a scripted integration check, not an answer-accuracy evaluation.
