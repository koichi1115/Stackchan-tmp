#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

python3 -m compileall -q src tests
python3 -m unittest discover -s tests -v
./scripts/test-integration.sh
./scripts/secret-scan.sh
