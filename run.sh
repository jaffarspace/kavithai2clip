#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
cloud_python=/workspace/.cloud-setup/kavithai2clip/venv/bin/python
if [ -x "$cloud_python" ]; then
  exec "$cloud_python" -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
fi
exec uv run --frozen uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
