#!/bin/bash
# Installs the newest desk presence program from GitHub, if the Pi is online.
# desk-check runs this first, so fixes arrive by themselves while you set up.
#   sudo /opt/desk-presence/setup/update.sh [branch]
# It follows the branch it was last given (main if none), and goes back to main
# once that branch has been merged and deleted.
set -uo pipefail

REPO=The-Osprey1/Camera-Buddy
DEST=/opt/desk-presence
if [ "$(id -u)" -ne 0 ]; then
    echo "Please run with sudo."
    exit 1
fi
BRANCH="${1:-$(cat "$DEST/branch" 2>/dev/null || echo main)}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

download() {
    curl -fsSL --max-time 60 -o "$TMP/code.tar.gz" "https://github.com/$REPO/archive/refs/heads/$1.tar.gz"
}
if ! download "$BRANCH"; then
    if [ "$BRANCH" = main ] || ! download main; then
        echo "Couldn't reach GitHub, so using the desk presence version already installed."
        exit 0
    fi
    BRANCH=main
fi
if ! tar xzf "$TMP/code.tar.gz" -C "$TMP"; then
    echo "The download was damaged, so using the desk presence version already installed."
    exit 0
fi
NEW="$(echo "$TMP"/*/pi)"
# GitHub's download names the commit it came from.
VERSION="$(python3 -I -c 'import sys, tarfile; print(tarfile.open(sys.argv[1]).pax_headers.get("comment", "")[:7])' \
    "$TMP/code.tar.gz" 2>/dev/null)"

if diff -rq -x __pycache__ "$NEW/desk_presence" "$DEST/desk_presence" >/dev/null 2>&1 &&
    diff -rq "$NEW/setup" "$DEST/setup" >/dev/null 2>&1; then
    echo "Desk presence is up to date (version ${VERSION:-unknown})."
else
    echo "Installing the newest desk presence (version ${VERSION:-unknown})..."
    if ! bash "$NEW/setup/install.sh" >"$TMP/install.log" 2>&1; then
        cat "$TMP/install.log"
        echo "The update stopped with an error (see above)."
        exit 1
    fi
    grep "Restart the Pi" "$TMP/install.log"
    echo "Installed."
fi
echo "$BRANCH" >"$DEST/branch"
