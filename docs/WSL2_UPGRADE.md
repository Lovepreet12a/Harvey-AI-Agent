# Update the existing Ubuntu WSL2 installation

Run these commands inside Ubuntu WSL2, in your existing repository. Replace the example directory with the actual clone path. Keep the clone in the Linux filesystem when practical. Python 3.11+ is required (validated with 3.12), plus Node.js/npm. Do not uninstall your existing database or copy credentials into Git.

## 1. Stop the old server and back up SQLite

Press **Ctrl+C** in the terminal running MOWZIVA. Then use a second Ubuntu terminal:

```bash
cd ~/Harvey-AI-Agent
python3 --version
node --version
python3 - <<'PY'
import os, sqlite3
from pathlib import Path
from datetime import datetime, timezone
os.umask(0o077)
source_db = Path('data/harvey.db')
if source_db.exists():
    backup_dir = Path('data/backups')
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / ('harvey-before-batch-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.db')
    with sqlite3.connect(source_db.resolve().as_uri()+'?mode=ro', uri=True) as original:
        with sqlite3.connect(backup_path) as backup_db:
            original.backup(backup_db)
            assert backup_db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    print('Created private, integrity-checked backup:', backup_path)
else:
    print('No existing data/harvey.db; nothing to back up.')
PY
```

This backs up SQLite consistently, including data that a plain copy could miss in a WAL journal. It does not print leads, tokens or credentials. Future backups can use `python3 scripts/backup-data.py` after installing this upgrade. Preserve your ignored `.env` and `harvey.local.yaml` files; do not paste their contents into chat or commit them.

## 2. Update the branch and dependencies

```bash
git status --short
```

If this lists local changes, preserve/reconcile them before pulling. Do not use `git reset --hard`, delete `data/`, or overwrite your local configuration. Once the working tree is clean:

```bash
git fetch origin
git switch feature/mowziva-harvey
git pull --ff-only origin feature/mowziva-harvey
./scripts/setup.sh
```

If a clone contains uncommitted development changes from an earlier session, do not discard them; reconcile them with the branch update first. Setup keeps the existing database and ignored configuration. Additive research tables are created on server startup. No migration deletes companies, review notes, source evidence, consent records or drafts.

## 3. Configure optional discovery and Codex

For broader discovery, edit your ignored `.env` privately:

```bash
umask 077
nano .env
```

Add `SERPER_API_KEY=` with your real Serper key after the equals sign. Never put the value in chat, a shell command, a screenshot or a Git commit. Check the provider's current credits/pricing. Serper is independent of ChatGPT and may require paid credits. OSM and existing-lead research do not require a Serper key unless you explicitly enable missing-website resolution.

For subscription-backed AI opinions, if needed:

```bash
.tools/node_modules/.bin/codex login
PATH="$PWD/.tools/node_modules/.bin:$PATH" .venv/bin/harvey status --check
```

Choose **Sign in with ChatGPT**. Do not use an OpenAI API key or another AI provider. If authentication, quota, CLI version or model checks fail, resolve the displayed reason. You can still run source research and factual drafts with **Request Codex opinions** unchecked. Keep secrets out of `harvey.yaml` and do not enable email/LinkedIn channels.

## 4. Validate and start

```bash
.venv/bin/pytest tests/test_mowziva.py tests/test_research.py -q
./scripts/start.sh
```

Keep the Ubuntu terminal and computer running. Open the local dashboard on port **8765** from the Windows browser. Open `data/mowziva-token` privately in an editor and paste it into the **Unlock** field. If Windows loopback forwarding is unavailable, fix the WSL networking configuration; do not bind the authenticated service publicly as a shortcut. If the port is occupied, stop the old server first or choose another port (`./scripts/start.sh --port 8766`). Only one service may own a given database.

## 5. Launch the first automated batch

1. In **Research Leads**, choose **Serper** for broader coverage, or **Existing leads** for an initial no-discovery-credit trial. Choose **property management** and **condominium management**, and limit the first batch to **10 leads**.
2. Keep Airdrie unchecked. Enable Serper missing-site resolution only if desired and within your explicit request/cost caps. Set the maximum Codex reviews to 10 or uncheck Codex while renewing authentication.
3. Click **Research Leads**. Watch progress and per-lead reasons. You may leave the page, but the service must continue running. The browser polls local progress; closing it does not cancel a running job.
4. Review **Draft Ready**, **Needs Human Review**, and **Research Incomplete** records. Verify source URLs, published contact/role context, portfolio/service relevance and actual property locations. A score is research priority, not proof of need or consent.
5. Review the unsent drafts and complete CASL checks before any future manual-contact approval. Existing sales stages are intentionally preserved, so batch results need not all have the sales stage `qualified`. Use **All stages**, inspect research status, and export the desired reviewed records using the existing search/score/stage filters. CSV now includes research status/reason and source/contact evidence.

Leave scheduling disabled for the first batch. When comfortable with results and usage, explicitly enable it using the desired current batch settings. It runs only while WSL and the service are awake/running, not as an always-on hosted agent. Cancel a current batch and disable a future schedule separately.
