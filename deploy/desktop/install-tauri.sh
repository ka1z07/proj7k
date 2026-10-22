#!/bin/sh
# proj7k desktop app on macOS: install proj7k's own window (desktop/tauri, ADR-0037) from the `desktop-latest`
# release into ~/Applications. `python -m proj7k.desktop` then opens it. Run it again to update.
#   sh deploy/desktop/install-tauri.sh
set -e
command -v gh >/dev/null || { echo "gh (GitHub CLI) is needed: brew install gh && gh auth login" >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
gh release download desktop-latest --repo ka1z07/proj7k --pattern 'proj7k-macos.zip' --dir "$tmp"
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/proj7k.app"
ditto -x -k "$tmp/proj7k-macos.zip" "$HOME/Applications"
# Not signed: clear the quarantine flag so Gatekeeper lets it open.
xattr -dr com.apple.quarantine "$HOME/Applications/proj7k.app" 2>/dev/null || true
echo "installed: $HOME/Applications/proj7k.app"
