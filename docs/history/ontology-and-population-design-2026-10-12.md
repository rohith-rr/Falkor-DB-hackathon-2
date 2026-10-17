# Wayfinder: Graph Ontology and Population Design

For the current code-free application design, start with the [design review pack](../design/README.md), dated 17 October 2026. The original proposal below is retained for historical context; the review pack describes implemented behavior, evidence boundaries, and known limitations.

Oct 12, 2026 · @Bhargav

## Implementation status (October 15, 2026)

The three layers remain the design. Implemented extensions use Plane Cloud Project
Pages and work items plus optional allowlisted Slack channels, restricted to a
fictional publishable company sandbox in a separate graph. This supersedes the
original no-Slack design boundary; that boundary was a design note, not a verified
current event rule. See `docs/COMPANY.md` for setup and limitations.

Implemented: explicit company teams/projects/components/ownership; source revisions;
per-scope coverage; considered alternatives; concern-specific resolution; versioned
extraction fingerprints and one-statement publication; validated investigation briefs;
opt-in hash-bound human-approved Plane task creation.

Existing Kubernetes code ownership and v2 directory expertise remain supported.
Company ticket assignments and chat volume do not imply ownership or expertise.
Company source sync is per scope, not globally atomic. Plane Pages API capabilities
require a read-only check. Slack retention limits remain visible.

Not demonstrated: independent held-out semantic accuracy, live Plane task creation
without configured credentials, confidential multiuser access control, complete
historical node snapshots, semantic topic merging, or real organizational communities.
Algorithms and scores are heuristics. Quote presence does not prove interpretation.
Alternatives default to considered. Generic approval does not close concerns.

The tables below retain original design context. Aspirational rows are not claims
that every feature is implemented. Current behavior is documented in
`docs/COMPANY.md` and `docs/EXPERTISE.md`.

## Overview

Wayfinder uses FalkorDB for scoped Kubernetes SIG Node data and a separate fictional company sandbox. Actual import windows and completeness depend on each source run. The graph has three layers, and each layer is filled by a different method with a different level of trust.

| Layer | What it holds | Populated by | Trust level |
| --- | --- | --- | --- |
| 1. Structure | People, SIGs, ownership, KEPs, feature gates, issues, PRs, reviews, comments, docs | Parsers over structured files + GitHub API | Confirmed (deterministic) |
| 2. Meaning | Topics, arguments, concerns, decisions, rationale, questions and answers | LLM extraction, every edge checked against a verbatim quote | Extracted (text proven, reading is the model's) |
| 3. Derived | Expertise, real working groups, review load, unresolved concerns, stale findings | Cypher rules + FalkorDB algorithms, recomputed on each sync | Computed (reproducible from layers 1 and 2) |

Design principles:

- **Nothing without provenance.** Every node and edge carries its source URL, source record and observation time.
- **Models extract, the graph decides.** The LLM reads text and proposes typed items. Code verifies the quote. Cypher computes every verdict (who owns, what is stale, what is unresolved).
- **Behavior over declaration.** Declared ownership (OWNERS, sigs.yaml) is treated as a claim and checked against recent activity.
- **Candidates stay visible but separate.** Inferred links are labeled and never counted as fact.
- **Change closes, never deletes.** Edges get a `valid_to` when they stop being true, so the graph can answer "as of" questions.
- **Data boundary (revised).** Public Kubernetes data and explicitly fictional, publishable Plane/Slack sandbox data only. No email ingestion. Event eligibility must be checked against actual organizer rules; design notes do not prove compliance.

&#91;embedded content: Ontology at a glance · 3 layers, main node types\]

Layer 2 is highlighted because it is what separates Wayfinder from a metadata catalog: it reads the text, but only keeps what it can quote.

## Common properties

Every node and edge carries the same identity, provenance and time fields, filled by the ingester that writes it. Application-owned IDs are used everywhere, never FalkorDB internal IDs.

**On every node**

| Property | Meaning | How it is filled |
| --- | --- | --- |
| `id` | Stable namespaced ID, e.g. `k8s:pr:kubernetes/kubernetes:12345`, `k8s:person:<login>`, `k8s:kep:2221` | Built by the ingester from source system + native ID |
| `source_system` | `github`, `k8s-community`, `k8s-enhancements`, `k8s-website`, `discourse`, `llm-extraction`, `derived` | Set by the ingester that wrote it |
| `source_url` | Link a human can open | API `url` field, or repo + path + commit SHA for files |
| `source_ref` | Exact record: API node ID, or `repo@sha:path` | From the API response or git |
| `source_updated_at` | When the source last changed it | API `updatedAt`, or commit time for files; null if unknown (never faked from observation time) |
| `observed_at` | When we read it | Ingester clock |
| `valid_from`, `valid_to` | Period during which it was true | Creation time; `valid_to` set when a later sync shows it gone or changed |
| `content_hash` | Hash of the text body | SHA-256 of normalized text; drives re-extraction |
| `is_deleted` | Tombstone | Set when the source record disappears |

**On every edge**

| Property | Meaning | How it is filled |
| --- | --- | --- |
| `id`, `source_url`, `source_ref`, `observed_at`, `valid_from`, `valid_to` | Same as nodes | Same as nodes |
| `link_method` | `STRUCTURED_FILE`, `API`, `EXACT_REFERENCE`, `LLM_EXTRACTED`, `COMPUTED` | Set by the code path that created the edge |
| `authority` | `CONFIRMED`, `EXTRACTED`, `CANDIDATE`, `COMPUTED` | Structured/API/exact links = CONFIRMED; quote-verified LLM items = EXTRACTED; unverified inferences = CANDIDATE; layer 3 = COMPUTED |
| `evidence_quote` | Verbatim text supporting the edge (layer 2 only) | Copied from LLM output, kept only if found verbatim in the source |
| `extractor_version` | Prompt + model + schema version (layer 2) or rule version (layer 3) | Pipeline config |

Answers may cite CONFIRMED, EXTRACTED and COMPUTED edges. CANDIDATE edges appear only in a separate "possible links" section of an answer.

## Layer 1: structural nodes

Layer 1 is filled entirely by deterministic parsers and the GitHub GraphQL API; no LLM touches it. Scope filter: items labeled `sig/node`, files under directories whose OWNERS belong to SIG Node, and KEPs under `keps/sig-node/`.

| Node | Key properties | Source | How populated |
| --- | --- | --- | --- |
| `Person` | `login`, `display_name`, `is_bot`, `first_seen_at`, `last_active_at` | GitHub authors, reviewers, commenters; OWNERS; sigs.yaml; kep.yaml | Upsert by GitHub login. `is_bot` from GitHub actor type plus a known-bot list (e.g. `k8s-ci-robot`). `last_active_at` = latest timestamp across the person's authored, reviewed and commented edges. No emails stored |
| `SIG` | `name`, `dir`, `charter_url`, `mission` | `sigs.yaml` in kubernetes/community | Parse YAML; one node per SIG (SIG Node plus SIGs it collaborates with) |
| `Subproject` | `name`, `description` | `sigs.yaml` subprojects list | Parse YAML; owners file URLs become edges to `OwnersFile` |
| `Repository` | `owner`, `name`, `default_branch` | GitHub API | One node per repo in scope (kubernetes, enhancements, website, community) |
| `Directory` | `path`, `repo` | Paths of changed files and OWNERS locations | Created for every directory on a path that has an OWNERS file or a touched file; parent chain via `CHILD_OF` |
| `File` | `path`, `repo`, `last_changed_at` | PR files API | One node per file touched by an in-scope PR |
| `OwnersFile` | `path`, `commit_sha`, `no_parent_owners`, `labels` | OWNERS files plus `OWNERS_ALIASES` | Parse YAML at HEAD; aliases expanded to people. Git history of each OWNERS file gives `valid_from`/`valid_to` for every approver and reviewer entry |
| `KEP` | `number`, `title`, `status`, `stage`, `owning_sig`, `latest_milestone`, `creation_date`, `last_updated` | `kep.yaml` + `README.md` in kubernetes/enhancements | Parse YAML for metadata. Split README by headings; Summary, Motivation, Goals, Non-Goals, Proposal and Alternatives stored as `KEPSection` nodes with text references |
| `KEPSection` | `kep_number`, `heading`, `text_ref`, `content_hash` | KEP README | One node per top-level section; input to layer 2 |
| `FeatureGate` | `name`, `current_stage`, `default_enabled`, `locked_to_default` | `kep.yaml` feature-gates list; feature gate definitions in `pkg/features` | Name from kep.yaml. Stage per release parsed from the versioned gate specs in code at each release tag (format to verify on day 1) |
| `Release` | `version`, `released_at` | GitHub releases and tags | One node per minor release in the window |
| `Issue` | `number`, `repo`, `title`, `state`, `labels`, `created_at`, `closed_at`, `body_ref` | GitHub GraphQL search `label:sig/node` | Paged by `updatedAt`; body text stored by reference for layer 2 |
| `PullRequest` | `number`, `repo`, `title`, `state`, `merged_at`, `labels`, `milestone`, `body_ref` | GitHub GraphQL | Same query pattern as issues; changed files fetched per PR |
| `Review` | `id`, `state` (APPROVED, CHANGES\_REQUESTED, COMMENTED), `submitted_at`, `body_ref` | GitHub GraphQL reviews | One node per submitted review |
| `Comment` | `id`, `kind` (issue, review, review\_line, forum), `created_at`, `body_ref`, `is_command`, `commands` | GitHub GraphQL; forum API if used | One node per comment. Prow commands (`/lgtm`, `/approve`, `/hold`, `/assign`) parsed by regex into `commands`; command-only comments skip layer 2 |
| `Thread` | `kind`, `root_ref`, `comment_count`, `last_activity_at` | Grouping of comments | One per issue or PR conversation, per line-review thread, per forum topic |
| `DocPage` | `path`, `title`, `last_edited_at`, `front_matter` | kubernetes/website content for SIG Node topics and feature gate reference pages | Parse Markdown and front matter; split into `DocSection` nodes. Git log gives editors and edit times |
| `DocSection` | `page_path`, `heading`, `text_ref`, `content_hash` | Doc pages | One per heading; input to layer 2 |
| `ForumTopic` (optional) | `id`, `title`, `category`, `created_at` | discuss.kubernetes.io | Only if readable without login (Rule 6); otherwise review threads are the chat source |

## Layer 1: structural edges

Every structural edge is CONFIRMED and comes from a structured file, an API field, or an exact text reference matched by a fixed pattern.

**People, teams and ownership**

| Edge | Properties | How populated |
| --- | --- | --- |
| `(Person)-[:MEMBER_OF]->(SIG)` | `role` (chair, tech\_lead) | `sigs.yaml` leadership lists |
| `(SIG)-[:HAS_SUBPROJECT]->(Subproject)` |  | `sigs.yaml` |
| `(Subproject)-[:GOVERNED_BY]->(OwnersFile)` |  | Owners file URLs in `sigs.yaml` |
| `(Person)-[:APPROVER_IN]->(OwnersFile)` | `valid_from`, `valid_to` | OWNERS `approvers` (aliases expanded); interval from git history of that file |
| `(Person)-[:REVIEWER_IN]->(OwnersFile)` | `valid_from`, `valid_to` | OWNERS `reviewers`, same method |
| `(OwnersFile)-[:COVERS]->(Directory)` |  | Directory containing the OWNERS file; inheritance resolved by walking `CHILD_OF` unless `no_parent_owners` |
| `(Directory)-[:CHILD_OF]->(Directory)`, `(Directory)-[:CONTAINS]->(File)` |  | Split from file paths |

**Work and conversation**

| Edge | Properties | How populated |
| --- | --- | --- |
| `(Person)-[:AUTHORED]->(PullRequest \| Issue \| Comment)` | `at` | API `author` field |
| `(Person)-[:SUBMITTED]->(Review)-[:REVIEWS]->(PullRequest)` | `state`, `at` | API reviews |
| `(Person)-[:APPROVED]->(PullRequest)`, `(Person)-[:LGTM]->(PullRequest)` | `via_comment`, `at` | Prow `/approve` and `/lgtm` commands parsed from comments (withdrawals like `/lgtm cancel` close the edge) |
| `(PullRequest)-[:TOUCHES]->(File)` | `additions`, `deletions` | PR files API |
| `(PullRequest)-[:FIXES]->(Issue)` |  | Closing keywords (`Fixes #123`) or API `closingIssuesReferences` |
| `(PullRequest \| Issue \| Comment)-[:REFERENCES]->(Issue \| PullRequest \| KEP)` | `pattern` | Regex over text: `#123`, full GitHub URLs, `KEP-1234`, links into kubernetes/enhancements |
| `(Thread)-[:ON]->(PullRequest \| Issue \| KEP \| ForumTopic)`, `(Thread)-[:HAS_COMMENT]->(Comment)` | `position` | Grouping during ingestion |
| `(Comment)-[:REPLIES_TO]->(Comment)` |  | API reply chain for line-review threads; for flat threads, an explicit quote or @mention of the previous author |
| `(PullRequest)-[:IN_RELEASE]->(Release)` |  | Milestone field; fallback: merge time between release cut dates |

**Decisions, gates and docs**

| Edge | Properties | How populated |
| --- | --- | --- |
| `(KEP)-[:OWNED_BY]->(SIG)`, `(KEP)-[:PARTICIPATES]->(SIG)` |  | `owning-sig`, `participating-sigs` in kep.yaml |
| `(Person)-[:KEP_AUTHOR \| KEP_REVIEWER \| KEP_APPROVER]->(KEP)` |  | kep.yaml `authors`, `reviewers`, `approvers` |
| `(KEP)-[:TRACKED_BY]->(Issue)` |  | Enhancement tracking issue (number matches the KEP number in kubernetes/enhancements) |
| `(KEP)-[:HAS_SECTION]->(KEPSection)` | `order` | README split |
| `(KEP)-[:DEFINES_GATE]->(FeatureGate)` |  | kep.yaml `feature-gates` |
| `(FeatureGate)-[:DECLARED_IN]->(File)` |  | File where the gate is defined in code |
| `(FeatureGate)-[:STAGE_IN]->(Release)` | `stage`, `default_enabled` | Gate spec at each release tag |
| `(DocPage)-[:HAS_SECTION]->(DocSection)` | `order` | Markdown split |
| `(DocPage \| DocSection)-[:DOCUMENTS]->(FeatureGate \| KEP)` | `stated_stage` | Exact gate name or KEP link in the text; `stated_stage` from the gate reference page front matter |
| `(Person)-[:EDITED]->(DocPage)` | `at`, `commit_sha` | Git log of the doc file |

## Layer 2: meaning (semantic nodes and edges)

Layer 2 is where Wayfinder reads. An LLM turns each text unit (comment, review body, KEP section, doc section) into typed items, and every item is kept only if its supporting quote appears verbatim in the source.

**Nodes**

| Node | Key properties | How populated |
| --- | --- | --- |
| `Topic` | `name`, `aliases`, `embedding`, `seed` (true if from the seed list) | Seeded from Subproject names, directory names, KEP titles and feature gate names. The LLM tags each unit with seed topics or proposes new ones; new names merge into an existing Topic when embedding similarity is above a threshold, otherwise create a new node |
| `Argument` | `kind` (proposal, support, objection, concern, explanation), `summary`, `concern_type` (performance, compatibility, security, reliability, complexity, operability), `evidence_quote`, `embedding` | One per extracted claim |
| `Decision` | `summary`, `decided_at`, `scope` (KEP, PR, gate), `evidence_quote` | Extracted from phrases that record an outcome ("we agreed to go with option B", "SIG decided to defer to 1.33"); KEP status changes in kep.yaml also create a Decision with authority CONFIRMED |
| `Rationale` | `kind` (motivation, goal, non\_goal, alternative\_rejected), `summary`, `evidence_quote` | Extracted from KEP Motivation, Goals, Non-Goals and Alternatives sections |
| `Question` | `summary`, `asked_at`, `evidence_quote` | Extracted from comments that ask something technical |

**Edges**

| Edge | How populated |
| --- | --- |
| `(Comment \| KEPSection \| DocSection)-[:ASSERTS]->(Argument \| Decision \| Rationale \| Question)` | Link from the source unit to each item extracted from it |
| `(Person)-[:MADE]->(Argument \| Decision \| Question)` | Author of the source comment (copied from layer 1, not inferred) |
| `(Argument)-[:SUPPORTS \| OPPOSES]->(KEP \| PullRequest \| Argument \| Decision)` | LLM picks the target from a closed list of IDs given in its context window |
| `(Argument {kind:'concern'})-[:RAISES_CONCERN_ON]->(KEP \| PullRequest \| FeatureGate)` | Same; `concern_type` from a fixed enum |
| `(Argument)-[:ADDRESSES]->(Argument)` | LLM proposes that a later comment answers an earlier concern; resolution rule in layer 3 decides whether it counts |
| `(Comment)-[:ANSWERS]->(Question)` | LLM link within the same thread |
| `(Decision)-[:ABOUT]->(KEP \| PullRequest \| FeatureGate)`, `(Decision)-[:SUPERSEDES]->(Decision)` | LLM proposes; SUPERSEDES needs both decisions to share a target |
| `(KEP)-[:HAS_RATIONALE]->(Rationale)` | From the KEPSection the rationale came from |
| `(any text unit \| Argument)-[:ABOUT_TOPIC]->(Topic)` | Topic tags from extraction, after merge |

**Extraction pipeline**

1. **Select units.** Every non-bot comment, review body, KEPSection and DocSection in scope. Skip command-only comments and units under 20 characters.
2. **Build context.** Thread title, the target object's ID and title, the previous two comments with their IDs, and the unit text. The context lists every ID the model may point at.
3. **Extract.** A small, cheap model returns JSON against a fixed schema: item type, target ID, stance, concern type, topics, summary and a verbatim quote. Types and enums are closed; anything outside them is dropped.
4. **Verify (deterministic).** The quote must appear in the unit text after normalizing whitespace and Markdown; the target ID must be in the allowed list. Failures are rejected and counted.
5. **Write.** Accepted items get `link_method = LLM_EXTRACTED`, `authority = EXTRACTED`, `evidence_quote`, `extractor_version`.
6. **Embed.** Topic names, Argument summaries and comment text get embeddings for the vector index; titles and bodies get a full-text index. These are the entry points for fuzzy questions.
7. **Cache.** Results are keyed by `content_hash + extractor_version`, so unchanged text is never re-extracted.

Quality gates reported in the README: rejection rate from step 4, and precision on a hand-checked sample of 50 extracted items.

## Layer 3: derived nodes and edges

Layer 3 is computed from layers 1 and 2 by Cypher rules and FalkorDB algorithms, and recomputed for affected nodes on every sync. Every derived value stores the IDs of the evidence it was computed from, so an answer can show why.

| Derived item | Shape | How computed |
| --- | --- | --- |
| **Expertise** | `(Person)-[:EXPERT_IN {score, evidence_ids, window}]->(Topic \| Directory)` | Weighted count of evidence in the last 180 days: approval 3, review with comments 2, merged PR authored 2, Argument of kind explanation 2, ANSWERS a Question 2, other comment 1. Each item decays with a 90-day half-life. Weights are visible config, not hidden in code |
| **Paper owner** | `paper_owner = true` on `APPROVER_IN` / `REVIEWER_IN` | Listed in OWNERS but no review, approval or comment in that directory in the last 180 days |
| **Unlisted expert** | `(Person)-[:UNLISTED_EXPERT]->(Directory)` | Top-5 `EXPERT_IN` score for a directory but no OWNERS entry covering it |
| **Working relationship** | `(Person)-[:WORKS_WITH {weight}]->(Person)` | Count of threads where both commented or reviewed, last 180 days; the projection used by community detection |
| **Real working group** | `(:Community {id, size})<-[:IN_COMMUNITY]-(Person)` | FalkorDB community detection (CDLP) on `WORKS_WITH`; each community gets an overlap score against SIG Node subprojects and OWNERS groups |
| **Review bottleneck** | `review_centrality` on `Person` | Betweenness centrality on a `REVIEWS_FOR` projection (reviewer to PR author); used to avoid routing every review to the same people |
| **Foundational docs and KEPs** | `importance` on `KEP` and `DocPage` | PageRank over `REFERENCES` and `DOCUMENTS` edges |
| **Concern status** | `status` on concern Arguments: open, claimed\_addressed, resolved | Resolved only if the concern's author later SUPPORTS or approves the target, or a merged PR explicitly REFERENCES the concern comment. An ADDRESSES edge alone makes it claimed\_addressed. No ADDRESSES edge = open |
| **Stale finding** | `(:StaleFinding {kind, detected_at, resolved_at})-[:CONCERNS]->(subject)` plus `-[:EVIDENCE]->` edges | Kinds: `DOC_STAGE_MISMATCH` (doc's stated stage differs from the gate's stage in the latest release), `OWNER_INACTIVE` (paper owner), `KEP_STATUS_MISMATCH` (kep.yaml status disagrees with merged gate graduation), `UNANSWERED_QUESTION` (Question with no ANSWERS after 30 days) |
| **Agent action** | `(:Action {kind, status, created_at, approved_by})-[:BASED_ON]->(evidence)` | Written when the agent files an issue in the sandbox repo after approval, so later answers can cite it |

Algorithm names, parameters and subgraph filtering are confirmed on day 1 against the pinned FalkorDB version; any algorithm whose semantics do not fit is dropped rather than forced.

## Population order and keeping it current

The initial load runs in seven steps, each depending only on the ones before it:

1. **Structure files.** `sigs.yaml`, OWNERS and `OWNERS_ALIASES`, kep.yaml and KEP READMEs, feature gate definitions at each release tag, doc pages. Git history of OWNERS and doc files gives validity intervals and editors.
2. **Activity.** GitHub GraphQL for issues and PRs labeled `sig/node` in the 12-month window, with reviews, comments and changed files. Prow commands parsed here.
3. **Threads and references.** Group comments into threads; run the reference patterns (closing keywords, `#123`, URLs, `KEP-1234`, gate names in doc text).
4. **Extraction.** Layer 2 pipeline over every eligible text unit. Run a 100-unit pilot first to measure cost and rejection rate before the full pass.
5. **Indexes.** Range indexes on every `id` and lookup key; full-text index on titles and bodies; vector index on Topic, Argument and Comment embeddings.
6. **Derived layer.** Expertise, paper owners, working relationships, communities, centrality, PageRank, concern status.
7. **Stale checks.** Write StaleFinding nodes.

**Incremental sync (every 10 minutes during the demo)**

| Change seen | What happens |
| --- | --- |
| New or updated issue, PR, review or comment (GraphQL `updatedAt` after the last cursor) | Upsert nodes and edges; new text goes through extraction; edited text with a new `content_hash` is re-extracted and the old extracted edges get `valid_to` |
| New commit touching an OWNERS file, kep.yaml, a gate definition or a doc page | Re-parse that file; close edges that no longer hold (`valid_to`), open new ones |
| Prow `/lgtm cancel` or `/approve cancel` | Close the matching `LGTM` or `APPROVED` edge |
| New Argument that ADDRESSES a concern, or the concern's author approves | Recompute that concern's status |
| Any change to a person's activity | Recompute their `EXPERT_IN` scores and `paper_owner` flags |
| A doc edit or gate stage change | Re-run stale checks for that gate; a fixed finding gets `resolved_at` instead of being deleted |
| Nightly | Re-run community detection, centrality and PageRank on the full projection |

Relationship intervals and retained source revisions support bounded history. Mutable nodes are not complete historical snapshots; arbitrary historical answers are not guaranteed.

## Company equivalents and the ablation

Every Kubernetes source has a direct counterpart in a normal company, so the same ontology applies with different adapters.

| Kubernetes source | Company equivalent | Ontology nodes it fills |
| --- | --- | --- |
| `sigs.yaml` | HR directory, identity-provider groups, team pages | `Person`, `SIG` (Team), `MEMBER_OF` |
| OWNERS files | CODEOWNERS, service catalog owners | `OwnersFile`, `APPROVER_IN`, `REVIEWER_IN` |
| KEPs | ADRs, RFCs, design docs in Notion or Confluence | `KEP` (Decision record), `KEPSection`, `Rationale` |
| Feature gates | Feature flags (LaunchDarkly, Unleash, config) | `FeatureGate`, `STAGE_IN` |
| GitHub issues | Jira or Plane tickets | `Issue` |
| PR review threads, forum | PR reviews, Slack or Teams threads | `Comment`, `Thread`, all of layer 2 |
| Website docs | Confluence, Notion, internal wiki, runbooks | `DocPage`, `DocSection` |

**Ablation: what survives messy data.** Run the evaluation twice: once on the full graph, once with `sigs.yaml`, OWNERS, kep.yaml metadata and labels removed, leaving only raw issues, PRs, comments, docs and git. Report per capability how much it degrades.

| Capability | Depends on structured files? | Expected effect of ablation |
| --- | --- | --- |
| Who knows what (expertise) | No: reviews, authorship and extracted explanations | Small |
| Why it works this way | Partly: KEP rationale helps, debate threads carry most of it | Moderate |
| Who owns what (declared) | Yes | Lost; only behavior-based ownership remains |
| Real working groups | No: computed from co-activity | Small |
| Stale docs | Partly: gate stages from code still available | Moderate |

The expected effects are hypotheses; the ablation run replaces them with measured numbers.

## Open questions and day-1 checks

- [ ] Get written confirmation from the organizers on early-start permission; otherwise this document stays as notes and all code starts October 15 (Rule 8).
- [ ] Confirm discuss.kubernetes.io (or another chat-like source) is readable without login; drop it if not (Rule 6).
- [ ] Confirm the current format of feature gate definitions in code and of the website's feature gate reference pages, so stage parsing is exact.
- [ ] Confirm FalkorDB algorithm procedure names, parameters and label/relationship filtering for CDLP, betweenness and PageRank on the pinned version.
- [ ] Confirm FalkorDB vector and full-text index syntax on the pinned version.
- [ ] Measure GitHub GraphQL volume for `sig/node` over 12 months; shrink to 6 months if the pull takes more than a few hours.
- [ ] Run the 100-unit extraction pilot: cost per unit, rejection rate, and a quick precision check before the full pass.
- [ ] Pick the evaluation window after the model's training cutoff, and freeze the evaluation questions before tuning any rule or weight.
- [ ] Decide the expertise weights and the 180-day window once, record them, and do not tune them against the evaluation set.
