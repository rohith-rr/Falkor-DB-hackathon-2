# Wayfinder-Bench results

**Run:** 2026-10-18 07:30 UTC on `wayfinder_company_live_demo` · 45 questions (37 answerable, 8 unanswerable) · 134 records in the text baselines' corpus · 90 graph tool calls · no LLM generation requests

The questions and gold answers were committed before any strategy was run (`questions.json`, commit `1baef33`). Gold comes from the sources, not the graph: the authored content file, the GitHub REST API, and the Plane API.

## Overall

| Strategy | Answerable F1 | Answerable recall | Exact answers | Unanswerable correctly empty | All questions correct |
|---|---|---|---|---|---|
| **Graph tools** (MCP `read_cypher` + `get_neighbors`) | **0.973** | 0.973 | 36/37 | **8/8** | 0.978 |
| Vector search (`perplexity/pplx-embed-v1-0.6b`, top 5) | 0.106 | 0.234 | 0/37 | 0/8 | 0.000 |
| BM25 full-text (top 5) | 0.084 | 0.197 | 0/37 | 0/8 | 0.000 |

## F1 by question type (answerable questions)

| Type | Questions | Graph | Vector | BM25 |
|---|---|---|---|---|
| code | 8 | 1.000 | 0.000 | 0.042 |
| component | 2 | 1.000 | 0.091 | 0.091 |
| cross_source | 10 | 1.000 | 0.100 | 0.150 |
| decisions | 2 | 0.500 | 0.333 | 0.333 |
| dependencies | 6 | 1.000 | 0.235 | 0.033 |
| documents | 2 | 1.000 | 0.333 | 0.111 |
| multi_hop | 2 | 1.000 | 0.000 | 0.000 |
| ownership | 5 | 1.000 | 0.000 | 0.000 |

## What each strategy is

- **Graph tools:** a fixed plan per question type over Wayfinder's own MCP tools. Each plan finds the named entity (a work-item key, a PR number, a component name, or a Slack message phrase through FalkorDB full-text), then follows typed relationships with `get_neighbors`. It stands in for an agent's planner; no LLM is involved. It receives the question's anchor and intent, which is what an agent's first step would extract.
- **Vector / BM25:** the question text alone, over the text of every current person, component, work item, page, Plane/Slack comment, pull request, branch and commit. Each returns its top 5. Neither can say "nothing found", so unanswerable questions always fail for them.

## Misses and changes, reported as they happened

- **Run 1: graph answerable F1 0.946.** q11 (the dependencies of FALKORDBTE-12) failed. `get_neighbors` returns full records, and one page of five work items exceeded the MCP response bound (`RESULT_TOO_LARGE`), so the planner returned nothing. The planner now follows the tool's documented contract: page with `next_cursor`, and halve the page size when the response is too large. Run 2 is the result above. The questions and gold were not changed.
- **Remaining miss: q35, the source of the "import sources before semantic extraction" decision.** The ADR-001 page states this decision, but the extraction run did not produce a Decision for it, so the graph has nothing to point from. This is the known low-recall extraction gap, left visible instead of re-extracting to fix the score.

## Limits

- 45 questions over one small, staged project, with gold annotated by the project authors (with AI assistance). Treat it as a reproducible check, not a leaderboard result.
- Some gold relations follow the same rule the linker uses (a PR mentioning a work-item key or URL). Those questions check that ingestion and linking work on real API data, not independent semantics.
- The graph strategy uses a templated planner, and the baselines get only the question text. That difference is the point being measured (structure versus similarity), but it isn't an LLM-agent comparison like HERB's. An agent-level run (same model with graph tools, with vector search, or with everything in one prompt) is the next step.

Re-run: `python benchmarks/wayfinder_bench/run.py` (needs the local live graph and its ignored `data/live-workspace` mapping; embeddings are cached under `data/`).
