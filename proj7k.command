#!/bin/sh
# proj7k desktop app on macOS / Linux: double-click in Finder (or run it). It reuses the running dashboard or starts
# one in the background, then opens proj7k's window. Extra arguments pass through (e.g. --window browser).
cd "$(dirname "$0")"
PYTHONPATH="$PWD/src" PYTHONUTF8=1 exec "${PYTHON:-.venv/bin/python}" -m proj7k.desktop "$@"
