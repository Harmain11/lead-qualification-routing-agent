#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON_BIN:-python3}"

echo "[build] installing backend package"
"$python_bin" -m pip install --no-cache-dir -e backend

echo "[build] installing and building frontend"
npm ci --prefix frontend
npm run build --prefix frontend

test -s frontend/dist/index.html
echo "[build] complete"
