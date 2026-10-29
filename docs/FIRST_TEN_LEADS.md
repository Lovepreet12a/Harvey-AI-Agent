# First 10 verified Calgary business leads

For the 2026-10-29 batch research upgrade, see [AUTONOMOUS_RESEARCH.md](AUTONOMOUS_RESEARCH.md), [RESEARCH_VALIDATION.md](RESEARCH_VALIDATION.md), and [WSL2_UPGRADE.md](WSL2_UPGRADE.md). Earlier manual-workflow instructions remain available below.

No step below sends an email, submits a contact form, registers with a vendor portal, or initiates LinkedIn outreach. Source research is distinct from contacting a business.

## 1. Start the review branch

In the existing cloud checkout:

```bash
cd /workspace/Harvey-AI-Agent
git switch feature/mowziva-harvey
./scripts/start.sh
```

If working from a fresh local clone instead, check out `feature/mowziva-harvey` and run `./scripts/setup.sh` once before `./scripts/start.sh`. Keep the server terminal running. The server binds to loopback port **8765**; if occupied, use `./scripts/start.sh --port 8766`.

On your own computer, open the loopback address at that port in your browser. A cloud-onboarding dashboard has no supported browser preview; do not expose the service publicly or expect the computer's loopback address to reach the cloud container. Use a local clone for the browser workflow if your cloud interface has no supported private access route. An agent in the cloud can operate the same authenticated API internally.

Open `data/mowziva-token` privately in your editor, paste its contents into the dashboard's Unlock field, and close the file. Do not paste the token into chat, put it in a URL or commit it. The token is not an OpenAI API key.

## 2. Resolve network access before collecting

The published environment already includes these allowed hosts:

- `overpass-api.de`, `overpass.kumi.systems`, `overpass.private.coffee` — free OSM discovery.
- `www.openstreetmap.org` — map source links.
- `mowziva.netlify.app` — MOWZIVA's site.
- `google.serper.dev` — optional broader discovery.
- `chatgpt.com`, `auth.openai.com`, `api.openai.com` — Codex login/model access.

Individual business websites and redirected source hosts also need access. Add their specific public hostnames in environment settings, save/apply the changes and retry verification. Do not broaden access indiscriminately or disable certificate, private-address or redirect checks.

Post-publication probes show that MOWZIVA's site is now reachable through the proxy, but Overpass's actual query endpoint returns HTTP 406, and upstream's protected website transport cannot connect. Allowlisting alone has not completed the live workflow. If cloud networking cannot support these operations, run the same branch locally on an Internet-connected machine. OSM can be unavailable or sparse; a zero-result or error response is never proof that no Calgary prospects exist.

GitHub PR automation separately needs `api.github.com`; it has been added to the configuration draft. Apply that addition in environment settings if you want this agent to create the PR automatically.

## 3. Renew Codex only for AI research

Codex is optional for deterministic qualification, factual drafts, the dashboard and CSV export. It is required for **Review with Codex**.

From the repository terminal:

```bash
.tools/node_modules/.bin/codex login
```

Choose **Sign in with ChatGPT**, using your subscription. Then run:

```bash
PATH="$PWD/.tools/node_modules/.bin:$PATH" .venv/bin/harvey status --check
```

Continue with AI reviews only when this exits successfully and reports Codex ready. Never supply a separately billed OpenAI API key. If the cloud's platform-managed login is read-only or cannot be renewed interactively, authenticate on your own computer and run the local clone there. Do not copy or edit credential JSON files. A valid login to this chat does not necessarily provide a renewable login for Harvey's isolated CLI.

## 4. Build a research queue of 20–30 businesses

1. In **Discover Calgary businesses**, choose **OpenStreetMap**, **property management**, and **25** results. Click **Discover businesses** once and inspect the report for errors, stopped reasons and the new-company count.
2. If it succeeds, inspect the returned business names and source links. OSM property-manager listings do not prove a residential/commercial portfolio or authority to hire maintenance contractors.
3. Avoid repeatedly searching condo/commercial management with OSM expecting distinct results: those categories map to the same property-management tag and duplicates are suppressed.
4. If OSM fails or coverage is insufficient, use **Add a business from a public source**. Search publicly for Calgary property management, condo/townhouse management, commercial property management and small retail plazas. Enter the business's actual name, official website, publicly documented location, category, phone if published, and a source URL. Do not guess contacts or addresses.
5. Optional: configure `SERPER_API_KEY` securely in environment settings for destination `google.serper.dev` (or an ignored local `.env`), then select **Serper** and search the priority segments. Serper is a discovery service, not the AI model provider; its billing/free-tier terms are separate from your ChatGPT subscription. Each app run has a $0.01 estimated cap. No key is needed for OSM or manual source entry.

Prefer managers responsible for multiple rentals, condos/townhouses or small commercial properties, then plazas, offices/clinics, multi-property landlords, daycares and small warehouses. Prioritize SE/NE Calgary where supported by source evidence: Cranston, Auburn Bay, McKenzie Towne, Seton, Mahogany, Taradale and Saddleridge. A manager's office location does not establish where its properties are located.

## 5. Verify and qualify 10 distinct businesses

For each candidate:

1. Open the lead and follow its **Website** and **Source** links. Confirm the site represents the same business, operates in Calgary and supports the selected business category.
2. Click **Verify website**. Successful retrieval stores source text and a timestamp and may find contact/vendor links. A failure must be investigated; do not mark it verified by assumption. The automated identity/category checks are research aids, so inspect the evidence yourself.
3. Record the actual public **contact page**, **vendor registration page**, and business phone if available. Reading a registration page is allowed; submitting a registration or enquiry needs owner approval.
4. In **Research notes**, record the source and date for maintenance relevance, portfolio type/count if actually published, SE/NE service-property evidence if available, and unknowns such as contractor selection, capacity and vendor requirements. Do not invent names, consent, insurance, WCB, equipment or experience.
5. If Codex is ready, click **Review with Codex** for a source-based research opinion. This does not approve contact or change consent.
6. The evidence-qualified count requires score **60+**, a successful website check, Calgary in the retrieved evidence, and matching category evidence. Once supported, select **qualified** in Pipeline stage and click **Save review**. Leave **CASL basis** unconfirmed unless you actually have documented lawful evidence. Qualification is not consent.
7. Add a follow-up date for further research where useful. It is a reminder, not a scheduled message.

Repeat until you have **10 distinct, human-reviewed, evidence-qualified businesses**. The dashboard's overall goal remains 50. A high score alone, a search snippet or an unverified record does not count as one of the 10. If an otherwise real candidate lacks sufficient page evidence, keep it researching rather than forcing it through the gate.

## 6. Export the first reviewed batch

Set the pipeline filter to **qualified** and minimum score to **60+**, then click **Export filtered CSV**. Check that the download contains your 10 reviewed businesses with sources, scores, stages and timestamps. If it contains fewer than 10, continue research. Do-not-contact records are excluded.

**Prepare factual draft** is optional and changes a lead's stage to `draft_ready`; if you prepare drafts before exporting, adjust the stage filter accordingly. Drafts are never sent. Review all sender placeholders and Canadian CASL requirements before any later, separately approved manual contact. Do not enable the upstream sales loop or run `harvey start`, `harvey setup`, `harvey train`, or the original dashboard for this phase.
