#!/usr/bin/env bash
# Picurate launcher — run from project directory.
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
if [ -f ".venv/bin/python3" ]; then
    exec .venv/bin/python3 main.py "$@"
else
    exec python3 main.py "$@"
fi
