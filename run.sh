#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONUTF8=1
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python app.py
elif [[ -x .venv/Scripts/python.exe ]]; then
  exec .venv/Scripts/python.exe app.py
else
  exec python app.py
fi
