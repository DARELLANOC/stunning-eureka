#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./run_parallel_linux.sh 8
#   ./run_parallel_linux.sh 16
#
# Optional environment overrides:
#   OPENFAST_EXE, TURBSIM_EXE, OPENFAST_CASE_DIR, TURBSIM_TEMPLATE,
#   INFLOW_FILE, OUTPUT_DIR, TURBSIM_DIR

WORKERS="${1:-$(nproc)}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found in PATH" >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python run_parallel.py --workers "${WORKERS}"
