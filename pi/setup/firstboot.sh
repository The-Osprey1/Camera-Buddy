#!/bin/bash
# Sets up desk presence by itself the first time the Pi starts, so a Pi with no
# screen or keyboard can be set up from the SD card alone (see "No screen or
# keyboard?" in the README). The pi/ folder goes on the card's boot partition as
# desk-presence/, and cloud-init runs this script once.
# Everything it does goes into desk-presence-setup.log on the boot partition,
# so you can read it on any computer.
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BOOT="$(dirname "$HERE")"
LOG="$BOOT/desk-presence-setup.log"
DONE=/var/lib/desk-presence/firstboot-done

exec >>"$LOG" 2>&1
say() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
fail() {
    say "FAILED: $*"
    exit 1
}

say "Desk presence setup starting"
if [ -f "$DONE" ]; then
    say "Already set up on an earlier start, nothing to do."
    exit 0
fi

USER_NAME="$(getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 {print $1; exit}')"
[ -n "$USER_NAME" ] || fail "no user account found (set one up in Raspberry Pi Imager)"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"

say "Waiting for the internet..."
for _ in $(seq 120); do
    getent hosts archive.raspberrypi.com >/dev/null && break
    sleep 10
done
getent hosts archive.raspberrypi.com >/dev/null ||
    fail "no internet after 20 minutes. Check the Wi-Fi name and password in Raspberry Pi Imager."

# The Pi has no clock battery. Until the time is set, the package lists look
# like they come from the future and get refused.
say "Waiting for the clock to be set..."
for _ in $(seq 36); do
    [ "$(timedatectl show -p NTPSynchronized --value)" = yes ] && break
    sleep 5
done

export DEBIAN_FRONTEND=noninteractive
say "Updating Raspberry Pi OS (the slow part)..."
apt-get update || fail "could not download the package lists"
apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade ||
    fail "could not update Raspberry Pi OS"

say "Copying the program to $USER_HOME/Camera-Buddy/pi..."
mkdir -p "$USER_HOME/Camera-Buddy/pi"
cp -r "$HERE/." "$USER_HOME/Camera-Buddy/pi/"
chown -R "$USER_NAME:" "$USER_HOME/Camera-Buddy"

say "Installing desk presence for $USER_NAME..."
cd "$USER_HOME/Camera-Buddy/pi" || fail "could not open $USER_HOME/Camera-Buddy/pi"
SUDO_USER="$USER_NAME" bash setup/install.sh || fail "install.sh stopped with an error (see above)"

mkdir -p "$(dirname "$DONE")"
touch "$DONE"
say "All done. Restarting in one minute to switch on keyboard mode."
shutdown -r +1
