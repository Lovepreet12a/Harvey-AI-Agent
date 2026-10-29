# Batch commercial lead research

MOWZIVA can now run a bounded discovery → official-site research → qualification → Codex opinion → unsent draft batch. Research automation does not grant permission to contact anyone. The existing email/LinkedIn configuration remains disabled, with zero email sends and no sending endpoints in this application.

## Architecture and changes

The original dashboard (`harvey/mowziva.py`, FastAPI) and Harvey's company/prospect SQLite schema remain. The upgrade adds:

- `harvey/research.py`: a sequential background job runner and bounded source-research client. API requests start a job and return HTTP 202 rather than holding the browser open through an entire batch.
- Additive SQLite tables: `research_jobs`, `research_items`, `research_leads`, `research_cache`, and `research_schedule`. Existing companies, reviews, evidence, notes, consent records and drafts are not dropped. Research status is separate from the human sales-pipeline stage.
- Persisted progress, counters, source excerpts, published contacts, errors and cancellation outcomes. A single OS lock protects each database from a second service instance. Use one application process, not multiple uvicorn workers.
- A scheduler disabled by default. It runs only inside this application process; it cannot wake a sleeping computer or start WSL itself.
- Authenticated research APIs behind the existing token, loopback-host, same-origin and JSON guards. Untrusted site connections still use Harvey's IP-pinned public-address/TLS/redirect boundary. No relaxed transport or proxy bypass was added.

Implemented phases: (1) durable job lifecycle/progress/cancellation; (2) bounded discovery and source enrichment; (3) automatic eligibility checks, subscription-backed Codex opinions and unsent drafts; (4) opt-in scheduling, regression tests and WSL2 operating instructions. Broader licensed-directory connectors remain a future extension, not an untested promise.

## Sources and costs

| Source | Cost/access | How this version uses it | Limits and guidance |
| --- | --- | --- | --- |
| Existing sourced leads | No discovery API credits | Researches the recorded official websites; can resolve missing sites only with an explicitly selected Serper-resolution option | Good for a first batch without purchasing search credits; website access still has to work |
| [OpenStreetMap/Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API) | Free volunteer infrastructure; OSM database licensing/attribution applies | One bounded public query for supported categories; stable map IDs and recorded official website tags | Sparse coverage of property managers/landlords. It is not a commercial ownership or consent directory. Repeated runs are cached. No mirror rotation to evade HTTP 429 |
| [Serper](https://serper.dev/) | Third-party API key and provider search credits; any trial allowance depends on current provider terms | Documented search API, multiple selected Calgary categories, and optional exact-business-name official-site lookup | Opt-in. Up to 10 results per search; defaults cap 6 requests and $0.01 estimated cost per batch, including retries/resolution. Check current provider pricing; the estimate uses $0.001/request and is not a guarantee of the provider's invoice |
| [DataForSEO](https://docs.dataforseo.com/) | Paid APIs with separate credentials/billing | Evaluated existing upstream listings/Maps/SERP adapters; not enabled in the new batch UI | Useful potential licensed source, but requires separate provider/budget validation. No silent fallback or unexpected charges |
| [City of Calgary Open Data](https://data.calgary.ca/) | Dataset-specific open-data licence and published API limits | Evaluated as a possible municipal business-licence seed source; not integrated | Licensing data is not proof of a manager's portfolio, maintenance responsibility, current website or decision-maker identity. A specific dataset/schema/licence needs validation before automation |
| Official business sites / sourced entries | No search-provider credit for direct reads; normal connectivity costs | Up to three public HTML pages on the official domain: the initial page and up to two linked contact/about/team/vendor pages | Robots rules and access denials are respected. No contact forms, portal registrations, LinkedIn profiles, protected directories or search-result webpages are submitted/scraped |

Serper is a discovery dependency, **not** the AI model provider. There are no new Python dependencies or separately billed OpenAI API calls in this upgrade. Secret values belong only in an ignored local `.env` or secure environment settings, never in Git or chat. `SERPER_API_KEY` is optional unless selecting Serper discovery or missing-site resolution.

## Run a batch

1. Start the application using `./scripts/start.sh`, then unlock the local dashboard using the private `data/mowziva-token` file. See [WSL2_UPGRADE.md](WSL2_UPGRADE.md) for safe upgrade/backup commands.
2. In **Research Leads**, choose **Existing leads**, **Serper**, or **OpenStreetMap**. Existing leads do not run discovery searches. OSM can return few or no appropriate businesses; Serper is the practical broader search option.
3. Select up to five target categories and 1–50 leads. Prioritize property managers, condo/townhouse managers, multi-property landlords, smaller commercial managers and realistically serviceable office/retail premises. Search results are candidates, not verified claims.
4. **Find missing websites using Serper credits** is off in the dashboard by default. Enable it only with a configured key and an explicit request/cost cap. Candidate URLs must come from actual search results, pass directory filtering and corroborate the business identity on the retrieved site; no hostname is guessed.
5. **Request Codex opinions** uses your existing ChatGPT subscription. Default maximum: 10 review operations per batch, adjustable 0–50. Additional eligible leads still get factual drafts but are marked **Needs Human Review** when the cap is reached. Existing usable opinions can be reused when the source text is unchanged.
6. Airdrie is excluded by default and must be explicitly enabled as a separate research area. An Airdrie-only office is not counted toward the Calgary-qualified goal. Search geography alone does not establish a service location.
7. Click **Research Leads**. The dashboard polls local job state every three seconds, with per-lead explanations and found/queued, verified, qualified, new/retained draft, failed/retryable, Serper-request and Codex-review counters. You can browse recent jobs while another job is running.
8. **Cancel active batch** cancels in-flight work, retains committed evidence, and releases the job lock. It does not disable an independently enabled future schedule. Disable that schedule separately if desired.

Statuses are **Discovered**, **Researching**, **Research Incomplete**, **Verified**, **Qualified**, **Draft Ready**, **Needs Human Review** and **Do Not Contact**. Human sales stages such as follow-up, won/lost and approved-for-manual-contact remain separate and are preserved. Existing drafts are retained by batches; an explicit manual draft action can regenerate a draft for review.

## Verification, extraction and qualification

- Direct public pages must corroborate a distinctive business-name token; generic words such as “property management” alone do not verify identity. This is a conservative research aid, not an independent audit. Alternate trading names or cross-domain redirects can require human confirmation.
- Contacts come only from published page text, `mailto:`/`tel:` links or explicit Person/jobTitle structured data. Role-context mentions are labelled as source context, not inferred hiring authority. Each entry keeps its source URL and observation timestamp. No email permutations, guessed names or invented roles are generated.
- Business source URLs and excerpts are stored with their original retrieval timestamps, including cache hits. The website check does not infer portfolio size, maintenance needs, budget or buying intent. Missing information remains unknown.
- Deterministic scoring remains inspectable. Adequate official-site category and service-area evidence is required before automatic Codex review. Insufficient evidence is returned as **Research Incomplete** with a useful reason, not a bare HTTP 422. Invalid request fields still correctly return structured validation errors.
- Codex opinions never change consent, pipeline approval or deterministic scores. Model-supplied quotations are retained as evidence only when they occur verbatim in the saved source text. Published-contact extraction is source-based, not model-based.
- Drafts use factual business-name, website, supported-category and published vendor-page context. They retain MOWZIVA's capacity-assessment caveat and sender-detail placeholders. They never assert insurance, WCB, equipment, commercial capacity, credentials or past work. All are **DRAFT ONLY — NOT SENT** and require human/CASL review.
- Deduplication uses normalized official domains and stable OSM IDs. Duplicate search results are processed once. If resolving a legacy record collides with another official domain, the collision is labelled for review; neither original record is silently deleted or merged.

## Errors, limits and recovery

Public pages and search/map responses are cached for up to 24 hours; robots checks for one hour. Site reads are paced by host (two seconds minimum plus applicable crawl delay). Fetching is limited to 1 MB per response and three attempts, with bounded exponential waits and `Retry-After` support. Long retry windows stop the task as retryable and preserve a host cooldown, including across restarts. Access-denied responses, robots disallows and anti-bot challenges stop automated retrieval; no challenges are solved and no identity/mirror rotation is used to evade limits.

Paid search timeouts are not blindly replayed because a provider may already have charged for an ambiguous response. Every attempted paid search, including status-based retries, consumes the batch request/estimated-cost cap. Optional official-site resolution shares that same budget.

A 429 when opening an OSM map link in an external browser remains the map service’s response; this application cannot override it. The batch researches the recorded official business site instead of scraping the OSM map page.

Each lead has a bounded source-research timeout; errors do not stop unrelated leads. If the main website is verified but an enrichment/contact page fails, the retained draft is marked Needs Human Review and the failure/retryable status remains visible. A provider authentication/setup outage pauses further Codex attempts for that batch while source research and factual drafts continue. The provider's exact error remains visible. No Claude or OpenAI API fallback occurs.

An unclean service restart marks unfinished jobs **interrupted** and active lead research incomplete, preserving earlier results. Start another batch explicitly; paid requests/model calls are never silently resumed after a crash. Prior jobs and their summaries remain in SQLite; the UI displays the latest 20.

The protected public HTTP client deliberately does not use environment proxies to resolve untrusted destinations. Some restricted cloud environments cannot support this route even when a domain is allowlisted. Use compatible public-network access or the local WSL2 installation; do not disable IP pinning, certificate verification or SSRF protections.

## Codex subscription and scheduling

Setup retains the supported local **Codex CLI 0.159.3** and the subscription-only adapter. Renew a file-backed ChatGPT login using the local CLI's interactive `codex login`, then check `harvey status --check` with the local CLI on PATH. The cloud chat's authentication is not necessarily a usable/renewable Harvey login. A subscription still has usage/rate limits; authentication expiry, provider quota and model availability can halt opinions. Model calls are sequential and tool-less. The review cap counts review operations, not a guaranteed count of underlying CLI retry turns.

Scheduling must be explicitly enabled in the dashboard and saves the current batch settings, provider budgets and Codex cap. Minimum interval is 60 minutes. The service must remain running and the computer/WSL instance awake. It is not cron, a cloud daemon or a Windows wake-up task. A missed interval does not trigger a backlog storm; an overdue enabled schedule starts at most one batch when the service is running again. Disabling a schedule stops future scheduled launches, not an already running batch.

## Review and tests

Run `./scripts/setup.sh` if dependencies need refreshing, then:

```bash
.venv/bin/pytest tests/test_mowziva.py tests/test_research.py -q
.venv/bin/pytest -q
```

Tests use isolated databases and mocked provider pages/responses; they do not contact businesses or consume live Serper/Codex credits. See [RESEARCH_VALIDATION.md](RESEARCH_VALIDATION.md) for actual outcomes and the browser demonstration. The screenshot uses explicitly labelled fictional test data, never mixed into the real lead database.
