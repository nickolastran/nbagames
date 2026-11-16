#!/usr/bin/env bash
# API on :8000 and UI on :5173 together. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r api/requirements.txt; }
[ -d web/node_modules ] || (cd web && npm install)

trap 'kill 0' EXIT  # take the whole process group down with this script
.venv/bin/uvicorn api.main:app --reload --port 8000 &
(cd web && npm run dev) &
wait -n  # if either one dies, stop the other
