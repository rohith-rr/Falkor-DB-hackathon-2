# AI assistant and model use disclosure

Wayfinder was developed with assistance from OpenAI Codex for architecture discussion, code implementation, documentation, debugging, test construction, and review. The project owner remains responsible for understanding the design, reviewing changes, validating behavior, and the final submission.

Anthropic's Claude Code (Claude Opus 5.5) was used on October 17 for a judge-style project review and the work that followed:

- The source-freshness layer: the sync change report, the `watch` command, and the live create/edit/delete change probe.
- The scrubbed offline replay of the live Plane/Slack responses.
- Fixes for four ingestion defects found by those tests.
- This cleanup pass.

Commits containing that work carry a `Co-Authored-By: Claude` trailer. The live probe and the recording read and wrote only the designated fictional sandbox. They made no LLM or embedding requests.

The application uses the locally configured OpenRouter model for semantic extraction. The recorded live demonstration used **mistralai/mistral-nemo**. Its embedding model was **nvidia/nemotron-3-embed-1b:free**, dimension **2048**. On October 17 the project moved to **perplexity/pplx-embed-v1-0.6b**, dimension **1024**. Both company graphs were re-embedded and their vector indexes rebuilt (`capabilities --migrate-embeddings`), and the [capability evidence](evidence/falkordb-capabilities.json) was re-recorded with the new model. The earlier extraction proof record keeps its original configuration. These identify the recorded configuration, not guarantees about future provider availability, price, or account quota.

## Data and interpretation origins

- In the original Northstar fixture, people, teams, components, work items, pages, chats, repository, PRs, and URLs are synthetic demonstration data. They are not records from a real company or live GitHub repository.
- The separate live connector sandbox contains API-created Plane work/pages/comments and Slack bot messages with real native IDs, source URLs, timestamps, and actual authorship. Codex assisted with writing the staged project story and population tooling. The current revision describes building Wayfinder itself, distinguishes recorded completed checks from outstanding work, and replaces the earlier fictional infrastructure story. This is real integration on staged content, not an actual six-person company or independent employee discussion. No fixture annotations were imported into that graph, After the owner approved the external-provider transfer, bounded semantic extraction of eight selected units completed on October 17 with mistralai/mistral-nemo and perplexity/pplx-embed-v1-0.6b (1024 dimensions): 8 generation and 20 embedding requests, 5 accepted and 1 rejected proposals, recall low ([evidence](evidence/live-connector-extraction.json)).
- Fifty baseline interpretation records are deterministically authored fixture annotations. They are labeled FIXTURE_ANNOTATION and are not live model outputs or independent human accuracy measurements. Fixture creation and development also involved AI assistance.
- The live-proof record contains genuine provider-generated before/after interpretations, labeled LLM_EXTRACTED, with source revisions and validation results. Local source overlays were used; no Plane page was edited externally.
- Kubernetes records come from the project's scoped public-source imports and remain in a separate graph. Activity coverage is incomplete, and scores are heuristic evidence signals.
- Automated test doubles and controlled Plane stubs are identified as simulations. They are not evidence of successful live inference or a real external task creation.

## Validation and limitations

The recorded successful extraction proof used two generation and three embedding requests. It demonstrates retirement of the old Decision, rejection of its saved brief, and publication of a different Decision. A preliminary restricted-network attempt failed and is not represented as a successful inference. Additional explicit embedding requests were used for capability and MCP checks; the successful extraction record is not a total billing report.

Schema, target, revision, and quote checks are deterministic safeguards. They do not prove that every interpretation is correct, that a reviewer recommendation is appropriate, or that an identified software concern is actually fixed. The scripted MCP transcript is a real protocol/client interoperability exercise, not an independent agent accuracy evaluation. Held-out semantic accuracy remains unestablished until independent answer review is completed.

The local credentials file is excluded from Git. Secrets are not included in committed proof logs. Synthetic status, partial coverage, snapshot age, model identity, and simulation/live origin must remain visible in the presentation.
