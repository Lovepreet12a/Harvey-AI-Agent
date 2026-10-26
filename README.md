# MOWZIVA Calgary lead workspace

A customized [Harvey](https://github.com/ethanplusai/harvey) workspace for MOWZIVA's Calgary lawn care, snow removal and seasonal property maintenance business. Uses **OpenAI Codex with a ChatGPT subscription**, not Claude or separately billed OpenAI APIs.

**No outbound messaging runs.** The MOWZIVA application has no send endpoint, scheduler, mailbox integration or background sales loop. Email and LinkedIn are disabled in `harvey.yaml`; the email cap is zero. Discovery and research happen only when requested. No business has been contacted during development.

## Run locally

Requires Linux/macOS, Python 3.11+ (validated with 3.12), Node.js/npm, and Internet access for installation.

```bash
./scripts/setup.sh
./scripts/start.sh          # optional: --port 8766
```

The server binds to loopback port 8765. Open the dashboard locally and unlock it with the contents of the owner-only `data/mowziva-token` file. The token stays in tab memory. Do not expose this server publicly or share its token. Cloud onboarding supports local request validation; it does not provide a dashboard preview link.

Each cloud task already has an isolated checkout. Use the existing repository; do not create a Git worktree. Processes must restart in each task; installed files and SQLite state are retained in a published environment snapshot.

## Workflow

1. **Discover** real Calgary businesses via free OpenStreetMap, or optionally Serper. Prioritize property managers, condo/townhouse managers and commercial property managers, then small plazas, offices, clinics, landlords, daycare centres and warehouses. OSM coverage is limited and does not reliably identify landlords. Condo/commercial OSM searches find general property-management offices; they do not prove a portfolio type.
2. **Research** the business website. The website check retrieves the public page, checks identity text and records source text, contact/vendor page links and a timestamp. It is an evidence aid, not an independent audit. Check addresses, decision-maker roles, portfolio size and maintenance responsibilities yourself. Search geography alone is never evidence that a business operates in Calgary.
3. **Qualify** using transparent priority scores, and optionally ask Codex for a research opinion. Automated evidence qualification requires score ≥60, a retrieved business website, Calgary in its source text and matching category evidence. This does not establish buying intent, service capacity or consent. Missing information stays unknown. Codex opinions do not change scores, stages or CASL evidence.
4. **Review** individual leads and pipeline stages. Record a public contact page, vendor registration link, research notes and a follow-up date. Follow-ups are reminders only; no messages are scheduled.
5. **Prepare a draft**, tailored with the business name and website, for human review. Drafts use a factual template rather than allowing generated claims about MOWZIVA's credentials. Commercial work is always subject to property, equipment and capacity assessment.
6. **Export CSV** using the current search, stage and score filters. Do-not-contact records are excluded and spreadsheet formulas are escaped. The CSV is a research artifact, not permission to upload a list into an outreach sequencer.

Initial goal: **50 evidence-qualified commercial prospects**. The dashboard shows progress honestly. An empty new installation contains no demo or fabricated leads. Public-source businesses can also be entered manually, with a source URL required and domain/source deduplication.

Priority areas: SE/NE Calgary, including Cranston, Auburn Bay, McKenzie Towne, Seton, Mahogany, Taradale and Saddleridge; expand to the rest of Calgary as appropriate. An office location does not establish the location of the properties it manages.

## Codex subscription setup

Harvey's isolated Codex adapter requires CLI **0.159.3 exactly**. Setup installs it inside ignored `.tools/`, preserving the host CLI. `scripts/start.sh` adds it to PATH. Model is blank in `harvey.yaml`, so the adapter selects an available subscription model from its catalog. There is no fallback to Claude or an API provider.

Use your existing file-backed ChatGPT login when valid. For a missing/expired login, run the locally installed CLI interactively:

```bash
.tools/node_modules/.bin/codex login   # choose Sign in with ChatGPT
PATH="$PWD/.tools/node_modules/.bin:$PATH" .venv/bin/harvey status --check
```

Do not paste credentials into chat, commit authentication files, or copy login files into this repository. Harvey reads the native login without renewing or modifying it and refuses API-key logins. Platform-managed cloud authentication may not support the subscription login workflow; if so, run subscription-backed reviews on your own authenticated machine. Deterministic research scoring, drafts, dashboard and CSV do not require Codex authentication.

## Discovery and network settings

Free OSM discovery uses `overpass-api.de`, `overpass.kumi.systems`, `overpass.private.coffee` and source links at `www.openstreetmap.org`. OSM sources require OpenStreetMap attribution; see [ODbL terms](https://www.openstreetmap.org/copyright) when redistributing database extracts.

For optional broader Google discovery, configure `SERPER_API_KEY` securely in environment settings (destination `google.serper.dev`) or an ignored local `.env`. Never commit its value. Each run is capped at an estimated $0.01, but provider pricing/credits are controlled by Serper. No key is needed for OSM or manually entered sources.

Restricted cloud networking must allow the discovery hosts, `mowziva.netlify.app`, and Codex's `chatgpt.com`, `auth.openai.com`, `api.openai.com`. Business website verification also requires network access to each source business's public hostname; add these as needed in environment settings. The upstream public HTTP transport rejects private addresses and validates redirects/TLS; restricted cloud networking can prevent it from reaching a business website. Failed verification is shown explicitly and never converted into a qualified lead.

## CASL and human approval

Publicly available email addresses and contact pages are **not blanket consent**. This workspace does not certify legal compliance. Before any separately approved manual contact, review a lawful CASL basis, evidence/source/date, recipient relevance, valid sender identity and mailing address, business contact details, and a working unsubscribe mechanism. The `approved_for_manual_contact` stage requires a verified website, draft, documented consent basis and evidence; it records your decision and still sends nothing. A do-not-contact selection blocks drafts and removes the lead from CSV exports.

Do not claim commercial insurance, WCB, equipment, capacity, credentials or prior work without verified business evidence. The draft has explicit sender-detail placeholders and must not be sent unchanged. Upstream Precleared does not support Canadian senders, so that integration is disabled; the MOWZIVA CASL review gate remains active.

## Development and review

```bash
.venv/bin/pytest -q
PATH="$PWD/.tools/node_modules/.bin:$PATH" HARVEY_CODEX_CLI_TESTS=1 \
  .venv/bin/pytest tests/test_codex_cli_surface.py -q
```

The first command includes the existing upstream tests and MOWZIVA's functional tests. Optional live-model, cross-platform and packaging tests have their own opt-in prerequisites. Mocked tests validate integration behavior without spending subscription usage or contacting businesses; they do not prove live provider availability.

No commits, pushes, deployments or publications are performed by setup. Review `docs/REVIEW.md` and `docs/VALIDATION.md` for the implementation scope and actual validation results. Local database, tokens, tool downloads and `.venv` are ignored.

## Upstream provenance

Based on Ethan Plus AI's Harvey at commit `8023a9495eb46d644f03da8c8f4d2b9e41e5836e` (retrieved 2026-10-26). The upstream Python package, tests, prompts, sales reference skills and dashboard assets are retained under the MIT license in `LICENSE`. See `docs/UPSTREAM_README.md` for original documentation. Original outreach and Claude features remain in the source for provenance, but are outside this phase; use **`scripts/start.sh`**, not `harvey start`, `harvey setup`, `harvey train` or the upstream dashboard, which expose broader configuration and sales controls.
