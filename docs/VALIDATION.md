# Validation results — 2026-10-26

## Original onboarding validation (before owner publication)

| Check | Actual result |
| --- | --- |
| GitHub repository access | `main` fetched successfully; local and remote HEAD `26807191c23f3df3ab2d3fc04b053610d0776d98` |
| Dependency installation | `scripts/setup.sh` completed with pinned Python packages and local Codex CLI 0.159.3 |
| Configuration | MOWZIVA, Calgary, Codex provider, America/Edmonton, email disabled, zero email cap, approval required, LinkedIn disabled |
| Complete repository test suite | **2,423 passed, 26 skipped**, 24 deprecation warnings, 100.08 seconds; no failures/errors |
| MOWZIVA functional tests | **18 passed**; included in the complete suite |
| Real pinned Codex CLI isolation suite | **21 passed**, 26.79 seconds, with `HARVEY_CODEX_CLI_TESTS=1`; mock local model endpoint, no real credentials/inference |
| Application startup | `scripts/start.sh` started the loopback-only server; restarted successfully using the same instructions |
| Functional HTTP requests | Dashboard HTML 200; unauthenticated API 401; authenticated status/CSV 200; send endpoint 404 |
| Browser checks | Real Chromium: token unlock, populated controls, empty-state dashboard, desktop and mobile layouts, no JavaScript errors or page overflow |
| Research safety | Tests verify no unverified business counts as qualified, search location does not prove Calgary service, category needs page evidence, do-not-contact suppression, CASL review gate, CSV formula protection and no Claude fallback |
| Repository review | `git diff --check` passed; original README changed and new files remain untracked; no staging, commits, pushes or publications |

The 26 skipped tests are upstream opt-in or platform-specific checks; they are **not passes**. The real Codex CLI isolation suite was enabled separately. Live model calls were not run. Deprecation warnings concern upstream datetime usage and Starlette/httpx compatibility; the executed tests passed.

Initial runs exposed the invalid Canadian-sender setting in upstream Precleared, which only supports GB/US senders; the integration is now disabled, with the MOWZIVA human CASL gate used instead. Other initial failures came from upstream tests assuming its original documentation, sample sender and channel settings. Those tests now use preserved upstream fixtures/documents or a test-only sender; assertions were retained. The final full-suite result above comes from a new complete run after those corrections.

The browser screenshot is [mowziva-dashboard.png](mowziva-dashboard.png). No synthetic businesses were added to the real database. HTTP/application behavior tests use isolated temporary databases and mocked source/provider responses, so they establish application behavior rather than live data quality.

## Live prerequisites still unresolved

- **Discovery and company website network access:** read-only HTTPS checks to Overpass and `mowziva.netlify.app` received proxy **403 Forbidden** responses. Required discovery, MOWZIVA and OpenAI hostnames were saved in the environment configuration draft; saving that draft did not apply runtime access. Business website checks require access to the individual public source hosts and compatibility with upstream's protected public HTTP transport. No TLS, signature, checksum or public-address checks were disabled.
- **Live Codex subscription authentication:** the existing native login was present, but Harvey's supported CLI preflight returned exit **1** and reported that the login is expired or due for renewal. No credential values were printed or copied. The user must renew/authenticate through the supported CLI using ChatGPT sign-in; platform-managed authentication may require using an independently authenticated local machine. No API key or Claude fallback was configured.
- **Lead collection:** **0 of 50** evidence-qualified businesses have been collected. Discovery and source verification must be retried after access is available. No fabricated leads or inferred consent fill the gap. The free OSM source may not have enough coverage for 50 appropriate prospects; broader public-source research or optional Serper discovery can be needed.

## Saved configuration and review

The configuration draft contains `install_script`, `start_skill` and the network additions. Secret values were not requested or saved; optional Serper remains unconfigured. Runtime validation, a saved draft and a published environment are separate states. **Nothing has been published.** Review the code and settings first. Applying network changes and any later publication remain owner actions, as requested.

After access and login change, resume with actual discovery, per-business source verification and live Codex research review, then report the actual qualified count. Do not call the live workflow fully ready until those checks succeed. The local development/dashboard/CSV workflow is installed and tested now.


## Post-publication verification and Git submission — 2026-10-26

- The implementation, local dependencies, dashboard assets, tests and SQLite state survived publication. The current MOWZIVA functional suite passed again: **18 passed**, 1.86 seconds.
- `harvey.yaml` still selects Codex, disables email and LinkedIn, sets the email cap to zero and requires approval. The dedicated MOWZIVA app still has no sending endpoint.
- Secret review covered all **188** candidate repository files, including the dashboard screenshot. Token/private-key/JWT/credential-URL checks found only explicit fake test credentials and adversarial URL examples. No live credentials were found. `.env`, local databases, dashboard tokens, `.venv` and `.tools` are ignored and excluded from the commit.
- Network state changed after publication: the configured Overpass and MOWZIVA domains now accept proxy connections, and the MOWZIVA site responds HTTP 200 through the standard proxy client. A minimal read-only Overpass POST still returns **HTTP 406**, while the upstream protected website client returns **ConnectError**. These observations do not establish successful live discovery or website verification. Do not disable TLS or the public-address protections to work around them. Use an environment supporting the protected transport, or run the dashboard on a local machine if cloud access remains incompatible.
- Live Codex preflight still exits **1**, reporting the existing login expired or due for renewal. No inference was run and no credential values were printed or copied.
- GitHub Git reads work. The GitHub API (`api.github.com`), needed for automatic PR creation, is denied with a proxy **403**. That hostname was added to the network draft while preserving the published domain list; the addition requires applying the settings before it can work. A draft save is not a runtime update.
- The owner has explicitly authorized a feature-branch commit/push and PR creation, superseding the earlier hold in the initial review report. Main must remain unchanged; no merge or business contact is authorized. GitHub submission state and any remaining PR blocker are reported in the chat after actual operations.
