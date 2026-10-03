#!/bin/bash
set -euo pipefail
trap 'echo "Book Distiller setup: FAILED (line $LINENO). Resolve the error and rerun ./setup.sh." >&2' ERR
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Book Distiller setup: FAILED. macOS is required." >&2
  exit 1
fi
# Reuse an initialized project interpreter even when its original bin is not on PATH.
if [[ -x "$ROOT/.venv/bin/python3.12" ]]; then
  export PATH="$ROOT/.venv/bin:$PATH"
fi
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "Book Distiller setup: FAILED. python3.12 was not found." >&2
  echo "Install Python 3.12 (for example: brew install python@3.12), add it to PATH, and rerun ./setup.sh." >&2
  exit 1
fi
python3.12 -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'
if [[ ! -e .venv ]]; then
  python3.12 -m venv .venv
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "Book Distiller setup: FAILED. Existing .venv is invalid; inspect it manually. It was preserved." >&2
  exit 1
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12), "Existing .venv must use Python 3.12; preserved without replacement"'
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -e '.[dev]'
mkdir -p inbox library backups
for directory in inbox library backups; do
  if [[ ! -e "$directory/.gitkeep" ]]; then touch "$directory/.gitkeep"; fi
done
./book doctor
.venv/bin/python -m pytest
echo "Book Distiller setup: SUCCESS. Environment, doctor, and tests passed."
