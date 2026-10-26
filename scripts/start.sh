#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.tools/node_modules/.bin:$PATH"
exec .venv/bin/python -m harvey.mowziva "$@"
