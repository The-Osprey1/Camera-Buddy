#!/bin/bash
# Sets up desk presence by itself the first time the Pi starts, so a Pi with no
# screen or keyboard can be set up from the SD card alone (see "No screen or
# keyboard?" in the README). The pi/ folder goes on the card's boot partition as
# desk-presence/, and cloud-init runs this script once, with your username:
#   bash /boot/firmware/desk-presence/setup/firstboot.sh yourname
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

# Without a username, use the Pi's first user account.
USER_NAME="${1:-$(getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 {print $1; exit}')}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)" ||
    fail "no user account '$USER_NAME' (set one up in Raspberry Pi Imager)"

say "Waiting for the internet..."
for _ in $(seq 120); do
    getent hosts archive.raspberrypi.com >/dev/null && break
    sleep 10
done
getent hosts archive.raspberrypi.com >/dev/null ||
    fail "no internet after 20 minutes. Check the Wi-Fi name and password, or plug in a network cable."

# The desktop's own update checker can be using the package tools at the same
# time, so wait for it instead of failing.
APT_WAIT=/etc/apt/apt.conf.d/90desk-presence-firstboot
echo 'DPkg::Lock::Timeout "600";' >"$APT_WAIT"

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

rm -f "$APT_WAIT"
mkdir -p "$(dirname "$DONE")"
touch "$DONE"
say "All done. Restarting in one minute to switch on keyboard mode."
shutdown -r +1
