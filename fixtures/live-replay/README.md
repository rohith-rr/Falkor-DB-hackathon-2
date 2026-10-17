# Live-connector replay snapshot

`responses.json` holds the 104 Plane and Slack API responses that one real connector sync of the [live sandbox](../../docs/LIVE-WORKSPACE.md) read on October 17, 2026. `manifest.json` is the matching sandbox manifest. Replaying them through the same connector code rebuilds the live-connector graph with no credentials and no network access:

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company.replay load --reset
```

This writes the `wayfinder_company_live_replay` graph. It refuses to overwrite `wayfinder_company_live_demo`. The result has 12 Issues, 6 DocPages, 52 Comments, 20 Threads, 12 assignments, 23 task dependencies and 22 Slack-to-Plane references, which match the live graph's current records. Coverage nodes are marked `mode: replay` with the recording time, so nothing claims a live observation. Point the MCP server at this graph with `FALKORDB_GRAPH=wayfinder_company_live_replay`.

## What was scrubbed

The snapshot was scrubbed before writing, and the recorder refuses to write if anything identifying remains:

- Emails were replaced with `owner@example.invalid`, and people's names with `Sandbox Owner`.
- Avatar, icon and profile fields were dropped.
- The Plane workspace slug and every UUID were replaced, as were Slack team, user, bot, app and channel IDs and the Slack domain. Replacements are consistent (keyed HMAC), so links between records still resolve.

Message text, timestamps, states, priorities and dates are unchanged. The scenario content is fictional ([project story](../../docs/WAYFINDER-PROJECT-DEMO.md)).

## Limits

- The snapshot covers the current source state only. It does not include the live graph's earlier SourceRevisions from content rewrites and freshness probes.
- It does not include semantic extraction, which is pending provider-transfer approval.
- Replay proves ingestion and graph construction, not live freshness. For freshness evidence, see the [change probe](../../docs/evidence/live-change-probe-watch.json).

## Re-recording

Re-recording requires the local `.env` credentials and the ignored `data/live-workspace/` scope. The recording is read-only: non-GET requests are refused.

```powershell
.\.venv\Scripts\python.exe -m wayfinder.company.replay record
```
