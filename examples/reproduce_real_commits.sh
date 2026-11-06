#!/usr/bin/env bash
# Rebuild the real-commit results table from nothing but the network:
# clone the four libraries, make a Python 3.12 environment for their tests,
# run every case with --jobs 1 and --jobs 4, and print the table and the hunks.
#
#   bash examples/reproduce_real_commits.sh DATA_DIR
#
# DATA_DIR receives the clones and the test environment (about 36 MB).
# Results go to results/jobs1 and results/jobs4 in this repository.
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "usage: bash examples/reproduce_real_commits.sh DATA_DIR" >&2
    exit 1
fi
data=$(mkdir -p "$1" && cd "$1" && pwd)
here=$(cd "$(dirname "$0")/.." && pwd)
cd "$here"

venv="$data/venv312"
if [ ! -x "$venv/bin/python" ]; then
    uv venv -q --python 3.12 "$venv"
fi
# The versions the README table was made with.
uv pip install -q --python "$venv/bin/python" \
    pytest==9.2.0 hypothesis==6.170.15 pympler==1.1 cloudpickle==3.1.2

for jobs in 1 4; do
    uv run python examples/real_commits.py --data "$data" --python "$venv/bin/python" \
        --jobs "$jobs" --out "results/jobs$jobs" > /dev/null
done
uv run python examples/real_commits.py --summary results/jobs1 results/jobs4
uv run python examples/real_commits.py --hunks results/jobs4
