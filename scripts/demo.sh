#!/usr/bin/env bash
# Run the Craveable demonstration: reference scenario, then an infeasible
# variant that forces re-planning and constraint relaxation.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
mkdir -p out
DELAY="${1:-0.04}"

if [ -x dist/craveable ]; then RUN=(./dist/craveable); else RUN=(python -m craveable); fi

SCENARIO='Find a Turkish restaurant in Downtown Baltimore, MD for two people to have dinner under $65 on Thursday night at 7:30 pm with a table for two near a window with a view of the garden or the street.'
INFEASIBLE="${SCENARIO/\$65/\$45}"

echo; echo "===== 1/3  version ====="
"${RUN[@]}" --version
echo; echo "===== 2/3  reference scenario ====="
"${RUN[@]}" "$SCENARIO" --delay "$DELAY" --json out/report.json --log-json out/events.json
echo; echo "===== 3/3  infeasible variant (budget \$45) -> re-planning + relaxation ====="
"${RUN[@]}" "$INFEASIBLE" --delay "$DELAY" --json out/report_relaxed.json
if [ "${RUN[0]}" = "python" ]; then
  python -m craveable "$SCENARIO" --quiet --chart out/utility.png >/dev/null && echo "chart: out/utility.png"
fi
