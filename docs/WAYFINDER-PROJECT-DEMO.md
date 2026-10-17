# Wayfinder project story in the live workspace

The live Plane and Slack dataset describes building and validating **Wayfinder itself**. Its product problem is scattered engineering knowledge: a useful agent must join tasks, documents, conversations, ownership, and decisions, preserve evidence, and obtain human approval before creating work.

The discussion is staged and bot-authored. Implementation status reflects recorded checks, not an invented multi-person company, independent employee approval, or a production readiness claim. There is one actual mapped Plane member in this sandbox.

## Workstreams and backlog

| Workstream | Task | Current state and evidence boundary |
|---|---|---|
| Source Ingestion & Evidence | Import Plane tasks, comments, and Project Pages | Done for the designated account/project; native records and bodies were read back |
| Source Ingestion & Evidence | Import Slack messages and complete threads | Done for the two channels; history, replies, permalinks, bot attribution, and join-event handling checked |
| Source Ingestion & Evidence | Invalidate interpretations after source edits | Done as an implemented/tested mechanism; repeating the semantic proof on the live sources is a separate pending task |
| Source Ingestion & Evidence | Validate identity mappings | In progress; one human/bot case works, additional-person validation is missing |
| Source Ingestion & Evidence | Publish coverage and access limitations | Todo; coverage records exist, final presentation/production-gap review is pending |
| Source Ingestion & Evidence | Verify live full-text/vector retrieval | In progress; indexes exist, approved live extraction/embeddings and their retrieval proof are pending |
| Agent Review Workflow | Navigate sources through MCP and validate a structural brief | Done as an actual scripted protocol session; not an answer-accuracy evaluation |
| Agent Review Workflow | Run the approved eight-unit live extraction pilot | Todo; explicit source-text transfer approval is still pending |
| Agent Review Workflow | Verify one real exact-payload-approved Plane follow-up | Todo; controlled checks exist, real hash-approved execution remains unverified |
| Agent Review Workflow | Fix dashboard connection/error reporting | Todo; the observed Connecting/non-JSON error defect is documented and not fixed |
| Agent Review Workflow | Independently review twenty answers | Todo; no semantic accuracy score has been established |
| Agent Review Workflow | Record the demo and submit | In progress; setup, design docs, disclosure, and a script exist, recording/upload/submission remain |

This yields **four Done, three In Progress, and five Todo** tasks. Twenty-three explicit dependencies connect the backlog. Native Plane task descriptions contain linked prerequisite tasks; the graph also represents these as DEPENDS_ON. Remaining dates align with the October 18 submission target. These counts describe this revision, not future project state.

## Documents support the same work

1. **Product brief and responsibilities:** defines Wayfinder, the two workstreams, the actual owner, and release limits.
2. **ADR-001: Import sources before semantic extraction:** explains provider-independent source ingestion, revisions, invalidation, and coverage.
3. **Connector refresh runbook:** covers access checks, readback, bot identity, source changes, unavailable scopes, and retries.
4. **ADR-002: Require approval for an exact Plane payload:** distinguishes proposal, approval, guarded execution, and uncertain outcome reconciliation.
5. **Evidence-backed review checklist:** requires scope, current sources, role distinctions, concern status, and approval before action.
6. **RFC: Bounded live extraction pilot:** separates considered/rejected options from the proposed eight-unit run and its pending approval.

## Slack discussions follow project work

The six threads address Plane/Slack readback, source-edit invalidation, the live extraction blocker, human-approved action execution, the dashboard defect, and identity/coverage/retrieval limits. Every task is linked from at least one relevant thread; all six documents have relevant references. Links point to actual native Plane records.

No conversation claims that the pending extraction, real approved action, dashboard fix, or independent evaluation is complete. Source ingestion, provider inference, protocol interoperability, and answer correctness have separate acceptance evidence.

The bot lacks channel-management scopes. The channel IDs and message URLs are preserved, so their existing names may still be `wayfinder-runtime` and `wayfinder-allocation`. Suggested UI names are **wayfinder-connectors** and **wayfinder-agents**, respectively; an authorized workspace member can rename them without changing IDs or source bindings.

## Graph identity and investigation

Use `wayfinder_company_live_demo`. Its current Components are:

- `company:northstar-live:component:source-ingestion`
- `company:northstar-live:component:agent-workflows`

The older infrastructure Components are retired with history retained; they were not renamed to mean a different product. Source record UUIDs, authors, and message timestamps remain stable. `northstar-live` in the stored namespace is a legacy identifier, not the displayed project name. The original `wayfinder_company_demo` and Kubernetes graph are separate and preserved. The dashboard still targets the original fixture graph.

Example MCP question:

> Investigate Wayfinder's Agent Review Workflow. Which checks are complete, what is blocking the live extraction and approved Plane action, and what evidence is still missing? Follow linked tasks and documents, read the original comments, distinguish recorded implementation checks from pending validation, and propose the next reviewable step.

Before extraction, the agent must read the current source records directly and report missing semantic materialization rather than pretend extracted Decisions or concerns already exist. [Live setup and verification](LIVE-WORKSPACE.md) describes the commands and request boundary. The authored current dataset is [wayfinder-content.json](../fixtures/company/wayfinder-content.json).
