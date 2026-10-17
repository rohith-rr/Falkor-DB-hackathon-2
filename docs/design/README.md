# Wayfinder knowledge graph: design review pack

**Design snapshot:** 18 October 2026  
**Audience:** product reviewers, architects, engineering leads, and hackathon judges  
**Purpose:** explain the application sufficiently to review its design without reading implementation code.

Wayfinder connects people, responsibilities, engineering work, documents, discussions, and decisions in an evidence-backed graph. An external agent explores that graph through MCP and produces answers that distinguish declared facts, interpretations, computed signals, and missing evidence.

This pack describes the implementation present in the repository at the snapshot date. It is not a claim that a server is currently running, that live accounts have been synchronized, or that answer accuracy has been established. No credentials, implementation listings, or deployment commands are included.

## Reading order

| Document | Questions it answers |
|---|---|
| [1. Product and system architecture](01-system-design.md) | What problem does the application solve? What are its boundaries and major parts? |
| [2. Graph ontology and evidence model](02-graph-and-evidence-model.md) | What do nodes and relationships mean? How are identities, authority, revisions, and history represented? |
| [3. Population, freshness, and derived knowledge](03-data-lifecycle-and-quality.md) | How does information enter the graph, change, become invalid, and produce scores or findings? |
| [4. Agent navigation and approved actions](04-agent-and-action-design.md) | How does an agent investigate independently, cite evidence, and propose a controlled action? |
| [5. Review scenarios, evaluation, and limitations](05-review-and-evaluation.md) | What can be demonstrated? What is tested, uncertain, incomplete, or unsuitable for production? |
| [6. Graph selection, connections, and query access](06-graph-access-and-demo-boundaries.md) | Which graph does each interface use? How is an agent's path shown? What does connection status mean? |

For a short review, read document 1, the Resource Allocation walkthrough in document 4, and the limitations table in document 5. Read document 6 before switching datasets or investigating a connection failure. For a technical design review, read all six in order.

## Terms used throughout

| Term | Meaning |
|---|---|
| Source record | A work item, page, message, review, structured file, or other imported record |
| Source unit | A specific piece of text selected for interpretation, such as a comment or document section |
| Claim | An interpretation such as a concern, decision, rationale, or question |
| Revision | Retained content associated with a particular source-text version |
| Coverage | What an import successfully examined, including its scope, gaps, and time bounds |
| Freshness | How recently sources were observed or a derived result was recalculated |
| Declared ownership | An explicit responsibility declaration; different from ticket assignment and demonstrated expertise |
| Expertise score | A versioned heuristic over saved engineering contributions; not a probability or reviewer authorization |
| Review brief | A structured investigation with cited ownership, contributors, decisions, concerns, gaps, and a proposed next step |
| MCP | Model Context Protocol: the interface through which an external agent uses Wayfinder's tools |

## Separate datasets, one application

| Graph | Intended use | Evidence boundary |
|---|---|---|
| `wayfinder_company_live_demo` | The live Company Brain about building Wayfinder | Real Plane, Slack and GitHub API records (the Plane/Slack content is a staged scenario), owner-approved extracted claims, and an executed approved action |
| `wayfinder_company_demo` | Fictional Northstar demonstration | Synthetic Plane, Slack and GitHub-shaped records, code expertise, and authored baseline interpretation labels |

These named graphs are separate. Shared graph vocabulary and application services don't make their evidence interchangeable. See the [live workspace record](../LIVE-WORKSPACE.md) for connector verification, freshness, refresh and extraction results.

The dashboard serves the graph chosen with `--graph`; its `/graph` view draws that graph and highlights an MCP agent's path through it. FalkorDB Browser can select either graph, and each MCP process uses its configured graph. A stopped database container makes both unavailable through that instance; it doesn't mean either was deleted. See [graph access and demo boundaries](06-graph-access-and-demo-boundaries.md) for the full contract.

## Review conventions

- **Implemented** means a mechanism exists in the current application; it does not imply production validation.
- **Fixture demonstration** means deterministic fictional data illustrates behavior. It does not measure LLM extraction accuracy.
- **Evaluation target** means a proposed or configured acceptance criterion, not an achieved result.
- **Limitation** means the reviewer should not infer a capability from a nearby feature.

Existing [company operations](../COMPANY.md), [MCP setup](../MCP.md), and [expertise operations](../EXPERTISE.md) guides cover running the application separately.
