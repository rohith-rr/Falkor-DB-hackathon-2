# Expertise scoring v2

Directory scores now measure **saved implementation and file-specific peer-review
evidence**. PR-wide approvals, review summaries and coordination remain available
as involvement, but do not increase the expertise score. Names in this ranking
are contributors, not automatically authorized reviewers; consult OWNERS separately.

## Recompute and query

Commands use the graph named by `FALKORDB_GRAPH`.

```powershell
$env:FALKORDB_GRAPH='wayfinder_company_demo'
.\.venv\Scripts\python.exe -m wayfinder.repair --expertise-only
.\.venv\Scripts\python.exe -m wayfinder who-knows services/allocation
.\.venv\Scripts\python.exe -m wayfinder.mcp_smoke
```

This reclassifies stored comments and publishes a new v2 snapshot without GitHub,
LLM or embedding requests. Original text and old score intervals remain stored.
MCP clients must restart their server process to load the new code.

## Add file-specific review evidence

The old importer saved PR conversation comments and review summaries, but not
review-thread comments anchored to file paths. Those old records cannot safely
be promoted to file-specific reviews. New live imports paginate review threads
and their comments automatically. For existing PRs, explicitly run a bounded
GitHub-only backfill:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.backfill_reviews --limit 10
.\.venv\Scripts\python.exe -m wayfinder.repair --expertise-only
```

The backfill selects up to ten already imported PRs without complete review-thread
coverage. It does not import new PRs or change complete activity coverage. Repeating
it progresses to the remaining PRs. Each selected PR is fully paginated; the cap
is on PR count, not comment count or API calls. GITHUB_TOKEN is required. No paid
AI calls occur. Deleted or inaccessible PRs fail without falsely completing coverage.
Coverage is an observation, not an assurance of future freshness.

## Calculation

Each event contributes `allocated_weight × 0.5^(age_days / half_life_days)`.
The configured half-life remains `EXPERTISE_HALF_LIFE_DAYS` (15 in the pilot).
There is no hard scoring cutoff. `EXPERTISE_WINDOW_DAYS` (30 in the pilot) now
controls the recent-activity counts for directory v2, not exclusion of older saved
evidence. Ingestion remains bounded by the pilot `--days` argument. Older discussion
records already saved can contribute after decay; no history is downloaded by refresh.
Topic scoring retains its existing legacy rules.

| Channel | Credit | Attribution |
|---|---:|---|
| Merged PR authorship | 2 per PR | Split equally across distinct directly changed directories |
| File-specific peer review | 2 per reviewer/PR/directory | One contribution per group, dated at its latest qualifying comment |
| PR-wide review/approval/discussion | 0 | Separate involvement, with original scope quotes |
| Commands, administrative updates, bots, self-review | 0 | Excluded from expertise |

Command classification ignores quoted text and fenced code when recognizing
executable slash commands. Unknown command lines also receive no expertise credit.
Mixed comments retain prose. Administrative classification is conservative and
deterministic; it is not an AI judgment of technical correctness. File-specific
prose is review evidence, not proof of review quality. Invalid/missing event dates
are excluded. An open PR's author earns no merged-implementation credit.

Directory subtree scores sum allocated contributions; they do not take the maximum
subdirectory score or duplicate ancestor copies. For stable directory IDs, ranking
resolves the repository/path before aggregating descendants. Use stable IDs to
avoid ambiguous paths across repositories.

Results include `score_breakdown`, `matches`, `distinct_pr_count`, `recent_pr_count`,
`evidence_ids`, `source_urls`, `evidence_strength`, `calculated_at`, `scoring_version`,
snapshot freshness and coverage warnings. `limited` means fewer than three
distinct supporting PRs; `multiple_prs` describes quantity, not calibrated confidence.
There may be fewer than five qualifying contributors.

Involvement records are capped in ranking output and marked `involvement_truncated`.
Full immutable `ExpertiseContribution` records remain inspectable with `read_cypher`
and `get_entity`. To find a person's records, use the published `generation` and
their `person_id`, then inspect the returned node IDs:

```cypher
MATCH (c:ExpertiseContribution)
WHERE c.generation=$generation AND c.person_id=$person_id
RETURN c ORDER BY c.id LIMIT 20
```

## Hourly snapshot refresh

The MCP remains read-only. To register a Windows task under the current user:

```powershell
.\scripts\schedule_expertise.ps1 -Install
```

It runs every hour while the user is logged in, using the repository's absolute
Python/script paths. Overlapping task instances are ignored. FalkorDB must be running.
Check the task status in Task Scheduler; errors leave the previous published
snapshot available. To remove it:

```powershell
.\scripts\schedule_expertise.ps1 -Remove
```

Without `-Install` or `-Remove`, the helper only prints instructions. Registration
is not performed automatically by installation or repair. No background Python
process is left running. For other schedulers, invoke the absolute path to
`wayfinder/expertise_refresh.py` using the repository virtual environment.

Snapshots older than two hours are marked stale. A refresh stages contribution
records and expertise/unlisted relationships, then publishes the generation with
one database statement and a compare-and-publish guard. Partial or superseded
generations never replace the previous complete ranking. Original source text
and historical snapshots are retained; frequent refreshes therefore grow storage.

Historical CLI `--as-of` queries select the most recent published snapshot at or
before the requested time. Earlier v1 intervals remain labeled `legacy-v1` with
a warning; their scores are not reinterpreted as v2. Mutable source properties
are still not complete historical snapshots.
