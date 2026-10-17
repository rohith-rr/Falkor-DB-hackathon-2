# 5. Review scenarios, evaluation, and limitations

[Design pack](README.md) · [Previous: agent and action design](04-agent-and-action-design.md)

## What this review can establish

The design can be reviewed for coherence, traceability, source boundaries, invalidation behavior, ranking semantics, agent navigation, and action controls without reading implementation code. A working demonstration can establish that these paths operate on the demo data. It cannot alone establish general company understanding or production reliability.

This document separates implemented mechanisms, repeatable demonstrations, evaluation targets, and outstanding limitations. Dataset counts below describe the committed fixture definition; they are not fresh database measurements or promises about a currently running server.

## Company Brain requirements

| Principle | Current design support | Remaining gap |
|---|---|---|
| Docs, tickets, and chats in one graph | Plane Project Pages/work items/comments plus optional Slack share identities and explicit component links; the designated live sandbox was populated and read back on October 17 | Live-source semantic extraction is pending explicit transfer approval; access on this account does not prove compatibility with every account |
| Who owns what | Manifest component owners, Plane assignments, and Kubernetes OWNERS are separate relationships | No automatic authoritative company service catalog or HR synchronization |
| Who knows what | Versioned directory expertise for Kubernetes and Northstar synthetic code, connected by IMPLEMENTED_IN | No validated general company expertise model over ticket/chat activity; topic scoring remains legacy |
| Trace decisions to sources | Quotes, source records, revisions, semantic links, and evidence navigation | Quote validation cannot prove a statement was actually a decision or caused an outcome |
| Keep knowledge current | Scoped polling, revision invalidation, reconciliation, coverage, and score refresh mechanisms | No globally atomic sync, universal deletion detection, automatic scheduler installation, or freshness SLA |
| Answers with clear sources | MCP evidence tools and structured brief validation | Free-form agent citation correctness and semantic accuracy require evaluation |
| Get work done | Hash-bound human approval and one sandbox Plane task action | Not an autonomous multi-step business workflow engine or production authorization system |

## Fixture inventory

| Data class | Committed quantity | Purpose |
|---|---:|---|
| Organization | 1 | Northstar fictional company boundary |
| Teams | 2 | Platform and Capacity |
| People | 6 humans plus a build bot | Ada Chen, Ben Ortiz, Cy Rao, Dee Park, Eli Moss, Faye Lin |
| Components | 2 | Node Runtime and Resource Allocation |
| Plane-shaped projects | 2 | Runtime and Allocation |
| Work items | 12 | A small linked engineering backlog |
| Work-item comments | 20 | Concern, response, and discussion evidence |
| Pages | 6 | Ownership, ADRs, RFC, and runbooks |
| Chat threads / messages | 6 / 30 | Cross-source discussion context |
| Synthetic GitHub PRs / OWNERS files | 8 / 3 | Code ownership versus implementation/file-review expertise |
| Hand-authored semantic annotations | 50 | Demonstrate source tracing and rule behavior |
| Development / held-out questions | 10 / 20 | Separate diagnostic prompts from answer-review prompts |

The fixture deliberately includes one runtime concern with an explicit recorded resolution and one allocation concern that remains open. It distinguishes immediate retry as considered from unlimited retry as rejected. It does not use Kubernetes records to populate the company demo.

Counts of total graph nodes and relationships will exceed this source inventory because sections, revisions, evidence links, coverage, status history, and retained records are also stored. Reimporting and historical retention can affect totals.

## Review scenarios

| Scenario | Evidence reviewer should inspect | Acceptance condition |
|---|---|---|
| Component ownership | OWNS_COMPONENT relationship, manifest origin, person identity | Answer attributes the declaration and does not equate it with task assignment or code-review authority |
| Resource Allocation investigation | Component, Allocation project, six work items, ADR/RFC/runbook, discussion | Agent follows explicit links and produces a cited decision/concern brief |
| Alternatives | Exact immediate-retry and unlimited-retry quotes | Considered and rejected remain distinct; no invented causal support for an ADR |
| Concern closure | Runtime concern, later specific resolution, actor, chronology, source text | Runtime closure cites qualifying evidence; general approval does not close allocation concern |
| Source edit | Previous revision, current revision, retired claims, pending/replacement interpretation | Old meaning is not presented as current after source text changes |
| Missing source capability | Unavailable or partial coverage record | Answer reports a gap rather than “zero activity” or source deletion |
| Expertise ranking | Published generation, contribution records, direct directories, PR counts, exclusion metadata | Commands and PR-wide approval do not inflate strong expertise; no claim of calibrated confidence |
| Historical question | Relationship intervals and selected score snapshot | Answer states bounded history limits instead of claiming a complete past company state |
| Approved action | Exact payload/hash, human approval, current citations, external identity | Mutation is rejected without approval; uncertain outcome does not cause blind duplicate creation |
| Wrong graph | Graph identity and entity namespace | Agent does not answer a company question using unrelated live Plane tools or Kubernetes evidence |

## Verification evidence and its limits

The repository includes automated checks for model/parsing rules, provenance, quote and target verification, current/history behavior, ranking attribution, publication guards, company navigation, source reconciliation, MCP read boundaries, brief checks, and action replay/approval behavior.

Connector behavior is also exercised with controlled responses for pagination, unavailable capabilities, and incomplete access. Such tests establish behavior under the modeled conditions. They do not establish that a specific real Plane or Slack account currently exposes every endpoint or that provider behavior will remain unchanged.

The company MCP smoke workflow opens an actual MCP session and performs a scripted investigation, including evidence retrieval and brief validation. It demonstrates tool interoperability and a scripted navigation path. It is not an independent autonomous-agent accuracy evaluation.

The retained Kubernetes compatibility artifact records unchanged ranking output before and after the shared processing changes at a fixed evaluation clock and published generation. It is a preservation regression, not a new Kubernetes sync, refreshed snapshot, full database backup, or claim that every possible query is unchanged. The company demonstration contains synthetic GitHub evidence rather than copied Kubernetes PRs. See the [compatibility record](../evidence/kubernetes-ranking-compatibility.json) and [final verification record](../evidence/final-verification.json).

The October 17 implementation run passed 103 automated tests against local FalkorDB and completed a real capped OpenRouter edit/invalidation proof plus native retrieval and an actual MCP protocol session. These checks do not establish independent answer accuracy. No actual Plane task creation has been verified without the required sandbox credentials and human approval.

A later October 17 setup created actual source records in the designated fictional Plane project and Slack channels, read them through the real connectors into a third graph, and verified a real MCP protocol session without provider requests. It checked twelve assignments and bot attribution for all thirty Slack messages. These are source-population operations, not the separate hash-approved agent action workflow. Semantic extraction of that newly imported dataset remains pending explicit OpenRouter transfer approval; the earlier successful local-overlay proof must not be presented as proof of extraction from these live sources. See the [live workspace record](../LIVE-WORKSPACE.md).

## Answer evaluation design

Evaluation has three distinct levels.

| Level | Measurement | What a pass means |
|---|---|---|
| Mechanical fixture checks | Fifty labeled quotes/revisions and generated brief evidence checks | The fixture evidence is connected and inspectable |
| Agent navigation exercise | Tool calls, scope checks, thread retrieval, source revision retrieval, structured brief | An agent or scripted client can navigate the required evidence path |
| Independent answer review | Twenty held-out answers reviewed by a named human | Answer meaning, citations, role distinctions, and concern status meet the chosen rubric |

The configured semantic gate requires at least 90% human-marked semantic passes across the twenty held-out answers, valid evidence briefs for all, no false concern closure, and no invented reviewer authorization. This is an evaluation target, not a result already established by the fixture.

A credible review record should retain each question, final answer, graph/snapshot context, evidence IDs, source revisions, reviewer judgment, and reason for failures. Freeze questions before tuning prompts or weights. Keep development questions separate. A reviewer should check meaning against complete relevant source context, not only a short quote chosen by the answering agent.

Useful failure categories include unsupported ownership, inflated reviewer authority, goal/decision confusion, considered/rejected confusion, false concern closure, irrelevant citation, stale revision, omitted coverage, nonexistent source, and unjustified confidence.

## Current limitations and downsides

| Area | Limitation | Consequence for review |
|---|---|---|
| Demo semantics | Fifty interpretations are hand-authored fixture labels | Do not report them as fifty successful model extractions or a model precision measurement |
| Corrected Decision taxonomy | The two fixture ADR Decisions no longer carry `kind: goal`; label-specific validation rejects recurrence | Applied repair preserves their stable IDs, evidence, and component scope |
| Independent support | Fictional page/chat statements were deliberately authored together | Repetition is not independent corroboration |
| Fixture URLs | Plane/Slack URLs identify simulated records and may not open | Use retained revision text; do not claim the records exist in a live account |
| Live capability validation | Plane Pages/work items/comments and Slack history/replies were verified on the designated sandbox account | This does not validate every account, plan, permission combination, or future API change |
| Source scope | Bounded sources and incomplete activity coverage | Unknown is often the correct answer; absence is not proof of inactivity |
| Identity mapping | Explicit configuration can be missing or wrong | Work can be split across identities or incorrectly joined by a bad mapping |
| Ownership authority | Manifest declarations can become stale | Declared ownership should be periodically reviewed by responsible humans |
| Semantic correctness | Valid quote plus existing target can still support a misleading summary | Mechanical validation is necessary but insufficient |
| Resolution language | Conservative English wording and narrow authority checks | Valid closures can be missed; ambiguous statements can still challenge the rule |
| Expertise calibration | Heuristic weights, sparse PR evidence, no quality assessment | Rankings can shift significantly and should not determine staffing or performance judgments |
| Topic scoring | Legacy rules differ from directory v2 | Report the returned scoring version and avoid combining incomparable scores |
| Historical completeness | Mutable source fields are not full snapshots | Arbitrary past-state reconstruction is unsupported |
| Multi-source consistency | A sync is not one atomic snapshot | Answers can see sources observed at different times |
| Source deletion | Reconciliation is connector- and scope-specific | No guarantee of detecting every removed message, lost label, or inaccessible record |
| Coverage summaries | Response context is bounded; existence of a coverage record alone does not mean completeness | Inspect status, scope, mode, and gaps rather than a single present/absent flag |
| Generic graph queries | Read-only queries can still be expensive or broad | Timeout and output bounds do not replace resource governance or source-level access control |
| Algorithms | Communities, PageRank, and centrality depend on small chosen projections | Do not call them real teams, importance, or workload without independent validation |
| Storage | Revisions, claims, snapshots, and backups accumulate | Retention, compaction, and capacity planning remain operational work |
| Initialization | Index creation can warn and continue | Inspect index readiness before judging retrieval performance |
| Scheduling | Polling and refresh require explicit registration and healthy services | A stopped scheduler leaves stale results; starting MCP does not refresh them |
| Dashboard connection reporting | The current client can leave its initial Connecting label visible after a state request fails and surface a JSON parsing error for a plain-text HTTP error | The label is not a health check; inspect the dashboard response and database availability before drawing conclusions about graph contents |
| Approved action | Local human-name/hash approval, limited evidence revalidation | Not an authenticated production control or a guarantee that business context is unchanged |

## Security and privacy boundary

The current company release is for fictional, publishable sandbox data. Source scope allowlists reduce accidental ingestion, and attachment-host restrictions reduce credential forwarding exposure. Read tools are database-enforced read-only, while action tools require explicit enablement and an approved sandbox destination.

These safeguards do not provide source-user permission inheritance, row-level access control, tenant isolation, SSO approval, protected audit infrastructure, retention enforcement, or sensitive-data erasure. Anyone granted graph access may be able to inspect imported text beyond their original source permissions. Retained revisions and backups can preserve text after a source edit or deletion.

When live interpretation is enabled, selected source text and related context are sent to the configured generation provider; text for embeddings is sent to the configured embedding provider. Provider retention, residency, and contractual controls are outside the application's current guarantees.

Imported text is untrusted content. The MCP instructions tell agents to treat it as data, not instructions, and structured targets/quotes reduce some extraction errors. They do not constitute a complete prompt-injection defense. An external agent with independent shell or connector permissions can exceed Wayfinder's read-only boundary through those other capabilities.

## Performance and operational expectations

There is no established production throughput, latency, cost, availability, or recovery-time target. Runtime depends on PR fan-out, page/message counts, graph size, provider response time, rate limits, retry behavior, and derived calculations. An item or extraction cap is not a fixed cost or duration guarantee.

For review, measure source retrieval separately from model generation, embeddings, semantic publication, derived refresh, and query latency. Report distinct source records as well as write counts, actual requests rather than attempts alone, pending extraction backlog, stale snapshots, and unavailable scopes. Keep cost/freshness measurements tied to a specific dataset and run.

## Recommended acceptance sequence

1. Verify graph selection, fixture identity, and source scope before evaluating answers.
2. Demonstrate a complete Resource Allocation investigation with full citations and stated gaps.
3. Exercise negative cases: approval without closure, considered without rejection, missing coverage, stale citation, and unauthenticated action attempt.
4. Inspect directory v2 evidence on a separate Kubernetes dataset, including exclusions and per-person PR counts.
5. Review held-out answers independently and retain failures, not only successful demonstrations.
6. Validate live source capabilities in an explicitly allowed fictional workspace before making live integration claims.
7. Assess confidential-data authorization and operational controls as a separate production-readiness effort.

## Outstanding design decisions

| Decision | Why it remains open |
|---|---|
| Authoritative ownership source and adjudication | Production declarations need an owner and a process for conflicting or stale records |
| Company-wide expertise model | Ticket/chat volume must not be promoted into competence without evaluation and clear attribution |
| Broader concern-resolution language | Improving recall must preserve protection against false closure |
| Provenance for every helper record | Some operational nodes do not carry the full ordinary source contract |
| Evidence entailment checking | Current validation checks presence/currentness, not whether the evidence proves the summary |
| Retention and access propagation | Production use requires policy for history, deleted sources, permissions, and backups |
| Snapshot consistency across sources | A global investigation snapshot may be needed for stronger temporal claims |
| Retrieval relevance and scale | Larger graphs require measured query plans, better relevance, and resource controls |
| Action identity and approval | Production mutation requires authenticated approval and auditable authorization beyond local CLI trust |

These gaps do not prevent a useful evidence-backed sandbox demonstration. They bound what the current application can credibly claim.
