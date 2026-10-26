# Changes awaiting review

The owner authorized committing and pushing this implementation on `feature/mowziva-harvey` for a pull request against `main`. The cloud environment was published by the owner. Merging and contacting businesses remain unauthorized.

- Imported Harvey's MIT-licensed source, upstream tests and reference assets from `8023a9495eb46d644f03da8c8f4d2b9e41e5836e`; retained license and upstream documentation.
- Added `harvey/mowziva.py`: a separate authenticated, loopback-only FastAPI application using Harvey's SQLite company store, Codex brain, discovery providers, dashboard guard and protected public HTTP client.
- Added `harvey/web/mowziva.html`: MOWZIVA branding, Calgary segment discovery, search/filterable pipeline, source evidence, research scores, CASL review, follow-up dates, review-only factual drafts and filtered CSV export.
- Configured `harvey.yaml` for MOWZIVA services, Calgary geography, America/Edmonton and Codex. Email and LinkedIn disabled; zero send cap; approval required. No mailbox credentials requested or configured.
- Added focused functional tests for evidence qualification, deduplication, authentication/origin protection, CASL gates, suppression, safe CSV export, discovery wiring and Codex failure behavior.
- Added reproducible dependency pins and setup/start scripts. Codex CLI is installed locally at upstream's supported version without replacing the host executable.

## Design choices

The initial customer list is business-level: public websites, business phone numbers and public contact/vendor pages. It does not collect personal profiles or guess emails. The MOWZIVA app has no outbound transport endpoints or background agent loop. Existing upstream sales integrations are retained but unused and disabled in configuration.

Rules score research priority; retrieved source evidence and human judgment establish fit. A Codex opinion cannot auto-approve a lead or change consent. Drafts use a conservative factual template so untrusted websites or model output cannot add unsupported MOWZIVA claims. A human may edit a draft outside the application before separately approved manual contact.

The initial target of 50 qualified prospects is tracked, not simulated. No seed business is passed off as a verified prospect. Live discovery and source verification depend on network access. AI reviews depend on a supported file-backed ChatGPT login, not merely on this chat's access to Codex.

## Review commands

```bash
git diff --stat main...feature/mowziva-harvey
git diff main...feature/mowziva-harvey -- harvey/mowziva.py harvey/web/mowziva.html harvey.yaml
```

Do not merge the pull request, enable outreach or contact a business without the owner's explicit approval. See [FIRST_TEN_LEADS.md](FIRST_TEN_LEADS.md) for the operational steps and remaining prerequisites.

Upstream tests that depend on the original sample configuration now use the preserved `tests/fixtures/upstream.harvey.yaml`; upstream documentation assertions point to preserved upstream documents. Warmup tests use a test-only `.test` sender so the real MOWZIVA configuration can leave its sender blank. Assertions remain active. MOWZIVA tests separately verify the actual product configuration and disabled channels.
