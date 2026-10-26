#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.lock
.venv/bin/python -m pip install --disable-pip-version-check --no-deps --no-build-isolation -e .
npm install --cache "$PWD/.tools/npm-cache" --prefix "$PWD/.tools" --no-audit --no-fund @openai/codex@0.159.3
.venv/bin/python -c 'from harvey.config import load_config; c=load_config(); assert c.brain.provider == "codex"; assert not c.channels.email.enabled; assert not c.channels.linkedin.enabled; print("MOWZIVA config validated; sending disabled")'
