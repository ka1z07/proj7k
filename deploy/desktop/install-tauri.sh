#!/bin/sh
# proj7k desktop app on macOS: install proj7k's own window (desktop/tauri, ADR-0037) from the latest release into
# ~/Applications (`desktop-latest`, the build of main, with `main` as the argument or before the first release).
# `python -m proj7k.desktop` then opens it. Run it again to update.
#   sh deploy/desktop/install-tauri.sh [release|main]
set -e
base=https://github.com/ka1z07/proj7k/releases
urls="$base/download/desktop-latest/proj7k-macos.zip"
[ "${1:-release}" = main ] || urls="$base/latest/download/proj7k-macos.zip $urls"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
got=
for url in $urls; do
  echo "downloading $url"
  if curl -fL --retry 2 -o "$tmp/proj7k-macos.zip" "$url"; then got=1; break; fi
done
[ -n "$got" ] || { echo "Could not download the proj7k window. Check the network, then run this again." >&2; exit 1; }
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/proj7k.app"
ditto -x -k "$tmp/proj7k-macos.zip" "$HOME/Applications"
# Not signed: clear the quarantine flag so Gatekeeper lets it open.
xattr -dr com.apple.quarantine "$HOME/Applications/proj7k.app" 2>/dev/null || true
echo "installed: $HOME/Applications/proj7k.app"
