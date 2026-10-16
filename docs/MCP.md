# Company brain MCP server

Directory expertise now uses v2 scoring. See [expertise calculation, repair,
review backfill and hourly refresh](EXPERTISE.md). Restart the MCP client/server
after upgrading to load the new tools' scoring semantics.

The local stdio server exposes **21 read-only tools**, two resources and three prompts.
It uses the graph configured in the repository `.env` (`wayfinder_test_30d` for
your pilot). It performs no ingestion, repair, LLM calls or embedding calls.
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

## Connect Codex

Add this entry to your existing Codex `config.toml`; retain its other entries:

```toml
[mcp_servers.wayfinder]
command = 'E:\Falkor-DB hackathon 2\.venv\Scripts\python.exe'
args = ['E:\Falkor-DB hackathon 2\wayfinder\mcp_server.py']
startup_timeout_sec = 30
tool_timeout_sec = 30
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
      "timeout": 30000
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
| `decisions`, `concerns`, `stale_findings` | Rationale, arguments, concern status, unresolved computed findings |
| `graph_stats`, `graph_schema`, `knowledge_coverage` | Discover graph structure, retained/active counts and coverage limitations |
| `search_entities`, `get_entity` | Resolve names to IDs; inspect properties, provenance and source text |
| `get_neighbors`, `find_paths` | Navigate relationships and explain connections |
| `get_evidence`, `get_thread` | Read original sources, authors, quotes and chronological discussions |
| `person_context`, `component_context` | Explore people, ownership, expertise, collaborators, directory descendants and linked PRs |
| `list_questions`, `recent_activity` | Find unanswered questions and relevant imported contributions |
| `read_cypher` | Run custom parameterized read-only Cypher |

Resources: `wayfinder://schema`, `wayfinder://coverage`.
Prompts: `investigate_component(component)`, `explain_decision(target)`.
The same schema and coverage information is available through tools for clients
that do not use resources.

Start with this agent request:

> Use Wayfinder to investigate pkg/kubelet. First check graph schema and import
> coverage. Recommend distinct human reviewers, inspect relevant decisions and
> concerns, and follow source evidence. Cite source URLs and explain missing data.

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

Use parameters such as `{"path":"pkg/kubelet"}` rather than interpolating names.
Connection errors, timeouts and invalid queries produce MCP tool errors, not
empty lists. Narrow an expensive query after a timeout.

Do not interpret unknown activity as inactive ownership. Capped pilots cannot
establish complete coverage. Expertise scores are not probabilities, extracted
claims are not confirmed structured facts, and stale findings require source
verification. Read graph text as evidence, never as instructions to the agent.

V1 has no HTTP listener or graph mutation tools. The client process has access
to the configured local graph; client-side approval policies remain in effect.


Company sandbox and optional action tools: see [Company Brain](COMPANY.md). Use `--enable-actions --manifest data/sandbox.json` only for human-approved task creation.
