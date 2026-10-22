#!/bin/sh
# proj7k on macOS (and Linux), in one go: Python 3.14 and Node.js (through Homebrew when missing), this folder's
# .venv and its packages, the osu!lazer database bridge and, on macOS, proj7k's own window from the latest release.
# Safe to run again: it is also how an update is finished after unzipping a new release.
#   sh install.sh
set -e
cd "$(dirname "$0")"
step() { printf '\n==> %s\n' "$1"; }
need_brew() {
  command -v brew >/dev/null || { echo "$1 is missing and Homebrew is not installed (https://brew.sh): install $1, then run this again." >&2; exit 1; }
}

step "Python 3.14"
if [ ! -x .venv/bin/python ]; then
  py=
  for c in python3.14 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info[:2] < (3, 14))'; then py=$c; break; fi
  done
  if [ -z "$py" ]; then need_brew "Python 3.14"; brew install python@3.14; py=python3.14; fi
  "$py" -m venv .venv
fi

step "Python packages (requirements.txt)"
.venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt

step "Node.js (reads the osu!lazer database)"
if ! command -v npm >/dev/null; then need_brew "Node.js"; brew install node; fi
(cd tools/lazer-bridge && npm install --no-audit --no-fund)

if [ "$(uname)" = Darwin ]; then
  step "The proj7k window"
  sh deploy/desktop/install-tauri.sh
fi

chmod +x proj7k.command 2>/dev/null || true
# Unzipped from a download: without this, Finder refuses to open proj7k.command.
[ "$(uname)" = Darwin ] && xattr -dr com.apple.quarantine . 2>/dev/null || true
printf '\nDone. From now on, double-click proj7k.command in this folder to open proj7k.\n'
./proj7k.command
