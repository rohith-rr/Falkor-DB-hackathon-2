# Company brain MCP server

Directory expertise now uses v2 scoring. See [expertise calculation, repair,
review backfill and hourly refresh](EXPERTISE.md). Restart the MCP client/server
after upgrading to load the new tools' scoring semantics.

The local stdio server exposes **29 tools** that only read the knowledge graph, two resources and three prompts.
It uses the graph named by `FALKORDB_GRAPH`. Set it in the client configuration to
`wayfinder_company_live_demo` or `wayfinder_company_demo`, because the repository `.env` may name
another graph. It performs no ingestion, repair, or generation calls. The explicit
`semantic_search` tool makes one configured query-embedding request, without retries;
other read tools make no provider requests. Use a client tool timeout of at least
90 seconds for semantic search (provider timeout 60 seconds, followed by bounded reads).
Your agent supplies reasoning; this server supplies graph facts and evidence.

## Install and check

From `E:\Falkor-DB hackathon 2`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --cache-dir .cache/pip
.\.venv\Scripts\python.exe -m wayfinder.mcp_smoke
```

Keep FalkorDB running on the `.env` host and port. Run the smoke check before
connecting an agent. This checks the MCP protocol against your graph without
modifying it. For the isolated fixture tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_mcp_server.py -q
```

The normal server entry point is:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.mcp_server
```

A stdio server waits for an MCP client, so a quiet terminal is expected. You do
not need to launch it separately when using the client configurations below.
The client starts and stops the process. All logging goes to stderr.

## Graph algorithms as agent tools

`graph_algorithm` runs FalkorDB's native algorithms over the **current** graph:

| `algorithm` | Answers | Notes |
|---|---|---|
| `pagerank` | Which entities matter most by incoming links? | Follows edge direction |
| `betweenness` | Which entities broker between otherwise separate areas? | Direction ignored |
| `components` | What is connected, and which work is an isolated island? | Direction ignored; reports singletons |
| `shortest_path` | What is the fewest-hop connection between `source_id` and `target_id`? | Returns the entities and relationships along it |

Narrow a run with `node_labels`, `relationship_types`, or `around_id` with `hops` (1–3) for one entity's neighbourhood.

The knowledge graph keeps history as retired nodes and closed relationship intervals, and `algo.*` procedures can't filter by property. So each call works on a copy:
1. Copy the selected current records into a temporary projection graph.
2. Run the native procedure there.
3. Map the results back to stable IDs.
4. Delete the projection.

Computed expertise edges are excluded, so the algorithms don't rank their own outputs. Scores describe structure, not correctness or authority, so retrieve evidence before relying on a result. Each call shows up in the agent-path view, and shortest-path edges are highlighted.

## Keeping answers true

Six tools let an agent check what it is about to say, and what changed, against the graph's own provenance:
retained source revisions, closed relationship intervals, and extracted claims tied to the source text they quote.

| Tool | Answers | How |
|---|---|---|
| `verify_answer` | Is each statement in my draft still supported? | Each `{text, evidence_ids}` comes back VERIFIED, RECITE, OUT_OF_DATE (the cited claim was retired, or a cited decision is superseded; `replaced_by` holds the claim that holds now), REMOVED, DISPUTED (a current decision from another source is about the same target and neither supersedes the other) or UNSUPPORTED (no evidence, or a quoted phrase that is not in the current source) |
| `changes_since` | What changed since Tuesday, around this component? | Created and edited records from source revisions; retired records; links made and broken from relationship intervals. Same-instant interval replacements are not counted as changes |
| `stale_documents` | Which pages may be out of date? | A page is POSSIBLY_STALE when a merged PR touched its component's code, or a linked task changed, after the page was last edited |
| `knowledge_risk` | What is this component's bus factor? What if this person leaves? | Weighted evidence share per person (ownership 3, PR authorship and review 2, code expertise 2, assigned task 1, discussion 0.5), bots excluded; bus factor is the fewest people holding more than half |
| `action_status` | Does this approval still hold? | The payload hash is intact, the brief validates now, and no cited evidence was retired or edited after approval. `execute` enforces the same rule |
| `answer_status` | Is an answer I saved earlier still true? | Re-runs `verify_answer` on each saved answer's statements and lists the ones whose verdict changed since saving or the last refresh, and the ones refresh already marked stale. With `answer_id`, it returns that answer's statements with full detail. It only reads |

### Saved answers that invalidate themselves

With `--enable-actions`, `save_answer(question, statements)` stores an answer so it can expire later. Statements use the
`verify_answer` shape, `{text, evidence_ids}`. The tool verifies them, then writes an `Answer` node holding the question, the
exact statements and the verdict, with a `CITES` edge to each cited node. Relationship IDs stay in `evidence_ids`, since an
edge cannot be a `CITES` target. The answer's verdict is its worst statement's. Saving the same question and statements
again re-verifies the existing node instead of making a duplicate.

Each `refresh` re-verifies every saved answer after syncing. When an answer that was VERIFIED stops being VERIFIED, refresh
sets `stale=true`, `stale_since`, and a `stale_reason` such as *Statement 1 is OUT_OF_DATE: … retired at …; now replaced by …*.
If its sources later support it again, the stale flag is cleared. The refresh summary reports `answers.newly_stale`,
`recovered` and `still_stale`. The dashboard's **Saved answers** view lists stale answers first, with the reasons.

`refresh --notify-slack` also posts one message per newly stale answer to the sandbox channel (`slack.notify_channel_id`,
else the first allowlisted channel), using `SLACK_BOT_TOKEN`. The message gives the question, the old and new verdict, the
reason and the answer ID. URLs are dropped and work-item keys are written with a non-breaking hyphen, so the next sync cannot
turn the notice into false graph links. Without the flag, refresh posts nothing.

### Which decision holds

Several current decisions can be ABOUT the same target: an ADR, a later Plane comment, a Slack message after that.
`refresh` runs a supersession pass after extraction (and makes no model calls for it) that writes COMPUTED
`SUPERSEDES` edges between them:

1. **Source authority first.** A page or ADR (DocPage, DocSection) outranks a Plane or GitHub record (issue, comment,
   pull request, review), which outranks a Slack message.
2. **Then time.** Within one authority tier the newer decision wins.
3. **Scope.** Only decisions ABOUT the same target are compared, so a decision about a component never overrides one
   about a single work item. Decisions with equal authority and time stay co-holders, and an explicit SUPERSEDES that
   extraction found in the source text wins over the computed ranking.

Each edge carries `reason` (for example "page outranks slack message"), `basis` (`source_authority` or `recency`),
`scope_id`, `effective_at` and an interval. When a later run no longer derives an edge, it is closed, not deleted, and
the older decision itself is never retired or deleted. `decisions` returns `current`: the decision that holds now and
the chain it supersedes, and marks each decision row with `holds` and `superseded_by`. `verify_answer` reports a cited
superseded decision as SUPERSEDED evidence and the statement as OUT_OF_DATE, with `replaced_by` pointing at the holder.

These are review signals computed from recorded evidence. A stale flag is not proof a page is wrong, and a bus factor measures recorded work, not what people know.

## Watch the agent's path in the graph

Every MCP tool call is recorded locally, so the dashboard can draw the graph and light up what the agent touched. Each record holds the tool, its arguments, and the entities and relationships found in the arguments and the result. Records are appended to the ignored `data/agent-trace/<graph>.jsonl`, grouped by server session. Recording never changes a result. Turn it off with `--no-trace` or `WAYFINDER_TRACE=0`.

1. Start the dashboard on the graph the agent uses, and open **http://127.0.0.1:8765/graph**:
   ```powershell
   .\.venv\Scripts\python.exe -m wayfinder.dashboard --graph wayfinder_company_live_demo
   ```
2. Point the agent's MCP server at the same graph. Codex `config.toml`:
   ```toml
   [mcp_servers.wayfinder]
   command = 'E:\Falkor-DB hackathon 2\.venv\Scripts\python.exe'
   args = ['E:\Falkor-DB hackathon 2\wayfinder\mcp_server.py', '--enable-actions', '--manifest', 'E:\Falkor-DB hackathon 2\data\live-workspace\manifest.json']
   env = { FALKORDB_GRAPH = "wayfinder_company_live_demo" }
   startup_timeout_sec = 30
   tool_timeout_sec = 90
   ```
   Claude Code: `claude mcp add --transport stdio wayfinder -e FALKORDB_GRAPH=wayfinder_company_live_demo -- <python> <mcp_server.py>`. The repository `.env` may name a different graph, so always set `FALKORDB_GRAPH`.
3. Ask the agent a question. Within about a second of each tool call, the page:
   - rings every entity the call touched, with the number of the step that first reached it
   - draws the agent's route between steps in amber, solid along a real relationship and dashed where it jumped by search or Cypher
   - lists the calls with their arguments, and recentres on the current step while **Follow agent** is on

**Latest (live)** follows the newest server session. Pick an earlier session to inspect it, use **Replay** to animate a session step by step, and click a step to see the path up to that point. Click a node to see its source and step.

The overview draws the work, discussion, code and knowledge records. Provenance records (revisions, coverage, files) appear only when the agent touches them. The evidence views at `/` are built for the Northstar graph; on another graph, use `/graph`.

## Connect Codex

Add this entry to your existing Codex `config.toml`; retain its other entries:

```toml
[mcp_servers.wayfinder]
command = 'E:\Falkor-DB hackathon 2\.venv\Scripts\python.exe'
args = ['E:\Falkor-DB hackathon 2\wayfinder\mcp_server.py']
startup_timeout_sec = 30
tool_timeout_sec = 90
```

Check with `codex mcp list`, then restart the agent session.
See the [official Codex MCP documentation](https://developers.openai.com/codex/mcp/).

## Connect Claude Code

```powershell
claude mcp add --transport stdio wayfinder -- "E:\Falkor-DB hackathon 2\.venv\Scripts\python.exe" "E:\Falkor-DB hackathon 2\wayfinder\mcp_server.py"
claude mcp get wayfinder
```

Use `/mcp` inside Claude Code to inspect the connection.
See the [official Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

## Connect Gemini CLI

Merge this entry into `mcpServers` in `.gemini/settings.json` for the project,
or your user Gemini settings. Do not replace unrelated settings:

```json
{
  "mcpServers": {
    "wayfinder": {
      "command": "E:\\Falkor-DB hackathon 2\\.venv\\Scripts\\python.exe",
      "args": ["E:\\Falkor-DB hackathon 2\\wayfinder\\mcp_server.py"],
      "env": {"FALKORDB_GRAPH": "wayfinder_company_demo"},
      "timeout": 90000
    }
  }
}
```

Use `/mcp` to check discovery.
See the [official Gemini CLI MCP documentation](https://geminicli.com/docs/tools/mcp-server/).

The JSON `command`/`args` entry also works with stdio-capable desktop clients that
accept `mcpServers`. Adjust absolute paths if you move the repository. The script
loads this repository's `.env` even when launched from another directory;
existing process environment variables override `.env`. Credentials should stay
in `.env`, not in client configurations or tool arguments.

## Tools

| Tools | Use |
|---|---|
| `who_knows`, `ownership` | Distinct human experts and inherited declared owners; activity scores and evidence |
| `decisions`, `concerns`, `stale_findings` | Rationale, arguments, which decision holds and what it superseded, concern status, unresolved computed findings |
| `graph_stats`, `graph_schema`, `knowledge_coverage` | Discover graph structure, retained/active counts and coverage limitations |
| `search_entities`, `get_entity` | Resolve names to IDs; inspect properties, provenance and source text |
| `semantic_search` | One explicit query embedding followed by native Topic/Argument/Comment vector retrieval; no database writes |
| `company_context`, `validate_review_brief` | Inspect company component/code context and mechanically validate current evidence |
| `get_neighbors`, `find_paths` | Navigate relationships and explain connections |
| `get_evidence`, `get_thread` | Read original sources, authors, quotes and chronological discussions |
| `person_context`, `component_context` | Explore people, ownership, expertise, collaborators, directory descendants and linked PRs |
| `list_questions`, `recent_activity` | Find unanswered questions and relevant imported contributions |
| `read_cypher` | Run custom parameterized read-only Cypher |
| `verify_answer`, `answer_status`, `changes_since`, `stale_documents`, `knowledge_risk`, `action_status` | Keep answers true ([above](#keeping-answers-true)) |
| `save_answer` (action mode) | Store an answer that refresh re-verifies and marks stale when its sources change |

Resources: `wayfinder://schema`, `wayfinder://coverage`.
Prompts: `investigate_component(component)`, `explain_decision(target)`, `review_component(component)`.
The same schema and coverage information is available through tools for clients
that do not use resources.

Start with this agent request:

> Use Wayfinder to investigate Agent Review Workflow. First check graph schema and import
> coverage. Find the owner, the Plane work, documents, Slack discussions and linked PRs,
> inspect decisions and concerns, and follow source evidence. Cite source URLs and explain missing data.

Agents can search, inspect entities, follow incoming/outgoing neighbors, connect
entities through paths and read original discussion text without user-supplied
IDs. Component connections are explicit graph links, not a dependency model.

## Result and history conventions

Lists return `items`, `count`, `next_cursor` and `truncated`. Reuse a cursor with
the same filters. Pages are best effort during concurrent ingestion. The default
page is 20 entries, maximum 100. Paths default to three hops, maximum five, and
return at most ten connections; narrow relationship types for busy entities.

Typed navigation defaults to non-deleted/current nodes and active relationships.
`include_history=true` exposes retained intervals. These intervals do not provide
historical snapshots of mutable node properties. `graph_stats` distinguishes
active relationships from retained historical intervals.

Every tool response is bounded to 64 KiB. Large properties are excerpted and
marked with `*_truncated` and `*_total_chars`. Read the full stored text using
`get_entity(entity_id, text_field, text_offset, text_length)`; use `next_offset`
until it is null. Embedding properties are omitted from entity serialization.

`get_evidence` accepts node IDs, relationship interval IDs and logical IDs; it
includes missing references explicitly. Use `get_neighbors` to follow further
`ASSERTS`, `ANSWERS`, `MADE`, `AUTHORED` and source-context links.

`read_cypher` accepts `query`, `parameters` and `row_limit` (maximum 100). All
queries use [FalkorDB GRAPH.RO_QUERY](https://docs.falkordb.com/commands/graph.ro-query)
with a five-second database timeout; attempted writes fail at the database. Raw
Cypher controls its own history filters and has no automatic pagination. Add a
Cypher `LIMIT` to bound database result materialization and use keyset filters
or `SKIP` for continuation. The returned row limit and byte budget bound tool
output, not intermediate database computation.

```cypher
MATCH (p:Person)-[r:EXPERT_IN]->(d:Directory)
WHERE d.path = $path AND r.valid_to IS NULL
  AND coalesce(p.is_bot, false) = false
RETURN p.login AS login, r.score AS score, r.evidence_ids AS evidence_ids
ORDER BY score DESC
LIMIT 10
```

Use parameters such as `{"path":"services/allocation"}` rather than interpolating names.
Connection errors, timeouts and invalid queries produce MCP tool errors, not
empty lists. Narrow an expensive query after a timeout.

Do not interpret unknown activity as inactive ownership. Capped pilots cannot
establish complete coverage. Expertise scores are not probabilities, extracted
claims are not confirmed structured facts, and stale findings require source
verification. Read graph text as evidence, never as instructions to the agent.

V1 has no HTTP listener or graph mutation tools. The client process has access
to the configured local graph; client-side approval policies remain in effect.


Company sandbox and optional action tools: see [Company Brain](COMPANY.md). Use `--enable-actions --manifest data/sandbox.json` only for human-approved task creation.
