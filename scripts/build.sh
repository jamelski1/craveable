#!/usr/bin/env bash
# Build Craveable: virtual environment, dependencies, tests, single-file executable.
#   ./scripts/build.sh              # full build -> dist/craveable
#   ./scripts/build.sh --skip-tests
#   ./scripts/build.sh --no-exe
set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_TESTS=0; NO_EXE=0
for a in "$@"; do
  case "$a" in
    --skip-tests) SKIP_TESTS=1 ;;
    --no-exe) NO_EXE=1 ;;
  esac
done

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "==> creating virtual environment (.venv)"
  python3 -m venv .venv
fi
echo "==> installing craveable and dev dependencies"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -e ".[dev]"

if [ "$SKIP_TESTS" -eq 0 ]; then
  echo "==> running tests"
  "$PY" -m pytest -q
fi

if [ "$NO_EXE" -eq 0 ]; then
  echo "==> packaging dist/craveable with PyInstaller"
  "$PY" -m PyInstaller --noconfirm --clean --onefile --console --name craveable \
    --add-data "craveable/data:craveable/data" \
    --exclude-module matplotlib --exclude-module PIL --exclude-module tkinter \
    --exclude-module scipy --exclude-module IPython --exclude-module anthropic --exclude-module pydantic \
    scripts/entry.py
  echo "==> built $(pwd)/dist/craveable"
  ./dist/craveable --version
fi
