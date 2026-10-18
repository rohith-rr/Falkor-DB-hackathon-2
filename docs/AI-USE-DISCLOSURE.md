# AI assistant and model use disclosure

Wayfinder was built with AI coding agents, and the application itself calls hosted models for semantic extraction and embeddings. This page records both. The project owner directed the work, reviewed and approved changes, ran or approved every operation against the live accounts, and is responsible for the submission.

## AI coding agents used to build the project

| Agent | Used for |
|---|---|
| **OpenAI Codex** | Architecture discussion, implementation, documentation, debugging, tests and review for the core system: the graph model, Northstar fixture, MCP server, expertise scoring, extraction pipeline with provenance, evidence dashboard, the live Plane/Slack connectors, and the seeding tooling and staged story for the live sandbox. |
| **Anthropic Claude Code** (Claude Opus 5.5), October 17–18 | See the list below. |

Claude Code was used for:

- **Project review:** a judge-style review of the project.
- **Freshness:** sync change reports, the `watch` command and the scheduler fix, plus live create/edit/delete probes on the sandbox.
- **Fixes and replay:** four ingestion fixes, and the scrubbed offline replay of the live API responses.
- **Embeddings:** the embedding-model migration.
- **GitHub:** the live connector (PRs, reviews, branches, commits), plus URL and Plane-key linking across GitHub, Plane and Slack.
- **Plane action:** preparing and executing the human-approved Plane action. The owner approved it in a terminal; the agent cannot approve.
- **Agent path view:** the graph view and its MCP tool-call tracing.
- **Refresh:** the `refresh` command with budgeted extraction, and model provenance on extracted claims.
- **Agent tools:** the `graph_algorithm` tool, and the truth-maintenance tools (`verify_answer`, `changes_since`, `stale_documents`, `knowledge_risk`, `action_status`), which were modelled on strengths of other hackathon entries.
- **Benchmark and site:** Wayfinder-Bench and the static showcase site.
- **Documentation**, including this page.

Commits from that work carry a `Co-Authored-By: Claude` trailer. One linking change was made in a separate Claude Code session and merged through the same pull requests.

## Content in the live sandbox written with AI assistance

- **Plane and Slack seed content.** The 12 tasks, 6 pages, 20 comments and 32 Slack messages describing the Wayfinder project were drafted with Codex and posted through the real APIs under the owner's Plane account and the Falkor DB Slack bot. They are a staged scenario, not an independent team discussion.
- **Sync-test changes,** written by Claude Code with the owner's request:
  - FALKORDBTE-8 and FALKORDBTE-6 moved to Done, with one comment and one appended line.
  - Work item FALKORDBTE-15 created.
  - One Slack thread posted.
  - Freshness-probe records created and then deleted.
- **FALKORDBTE-16,** created by the approved agent action. Its text was drafted by Claude Code, and the owner approved that exact payload.
- **GitHub.** The repository's code, commits and pull requests were written with the agents above. The connector imports them as real records (`synthetic: false`).

## Models the application calls

All model calls go through OpenRouter and are explicit: `extract`, `refresh --extract N`, `live_proof --enable-live`, `capabilities --enable-live`, `--migrate-embeddings`, and `semantic_search` (one query embedding). Sync, `refresh` without `--extract`, `watch`, actions and the other MCP read tools make no model calls.

| Purpose | Model | Where it was used |
|---|---|---|
| Generation | `mistralai/mistral-nemo` | Recorded local proof on Northstar; the eight-unit live extraction ([evidence](evidence/live-connector-extraction.json)) |
| Generation | `deepseek/deepseek-v4-flash` | Later live extraction: a three-unit run on Plane comments (1 accepted Question) and the product-brief page (5 accepted claims); the current configuration |
| Embeddings | `nvidia/nemotron-3-embed-1b:free`, 2048 dims | Original Northstar vectors and the recorded local proof |
| Embeddings | `perplexity/pplx-embed-v1-0.6b`, 1024 dims | Current. Both company graphs were re-embedded and their indexes rebuilt on October 17; the [capability evidence](evidence/falkordb-capabilities.json) was re-recorded |

Since October 18, each extracted claim records `llm_model` and `llm_base_url`. Claims extracted earlier don't carry that property; the table and the evidence files identify their models. These records describe the configuration used, not future provider availability, price or quota.

## Data and interpretation origins

- **Northstar** (`wayfinder_company_demo`): people, teams, components, work items, pages, chats, repository, PRs and URLs are synthetic. Fifty baseline interpretations are fixture annotations labelled `FIXTURE_ANNOTATION`, not model output. The recorded live proof contains genuine `LLM_EXTRACTED` before and after interpretations on a local source overlay; no Plane page was edited.
- **Live sandbox** (`wayfinder_company_live_demo`): real API records with native IDs, timestamps and actual authorship (owner account, bot, GitHub users), over the staged content described above. No fixture annotations were imported. Extracted claims there are `LLM_EXTRACTED`.
- **Test doubles and controlled stubs** are labelled as simulations, and are not evidence of live inference or task creation.

## Validation and limitations

- Schema, target, revision and quote checks are deterministic safeguards. They don't prove an interpretation is correct, a recommendation is appropriate, or a concern is fixed.
- Live extraction recall was low (for example, the ADR-001 decision was missed), so treat missing claims as unknown.
- Scripted MCP sessions test the protocol and navigation, not agent answer accuracy. Held-out answer accuracy has not been evaluated.
- Credentials stay in the ignored `.env`. Account identifiers stay in ignored `data/` folders, and committed evidence is scrubbed of native IDs and personal names.
- The presentation should keep synthetic versus live origin, coverage gaps, snapshot age and model identity visible.
