#!/bin/bash
# Installs desk presence on Raspberry Pi OS (Bookworm or newer).
# Run from the pi/ folder:  sudo ./setup/install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run with sudo: sudo ./setup/install.sh"
    exit 1
fi
# The account the program runs as: whoever ran sudo, or else the Pi's first
# user account (setup/firstboot.sh runs this without sudo).
USER_NAME="${SUDO_USER:-$(getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 {print $1; exit}')}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)" || {
    echo "Could not find the user account '$USER_NAME'."
    exit 1
}
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "Installing camera and helper software..."
apt-get update
# python3-opencv and python3-munkres are needed by picamera2's decoder for the pose model's results.
apt-get install -y imx500-all python3-picamera2 python3-opencv python3-munkres \
    python3-serial python3-gpiozero python3-numpy

echo "Copying the program to /opt/desk-presence..."
mkdir -p /opt/desk-presence
cp -r "$HERE/desk_presence" "$HERE/setup" /opt/desk-presence/
chmod +x /opt/desk-presence/setup/usb-gadget.sh

echo "Turning on USB keyboard mode on the USB-C port..."
BOOT_CONFIG=/boot/firmware/config.txt
[ -f "$BOOT_CONFIG" ] || BOOT_CONFIG=/boot/config.txt
# Match this exact line: newer config.txt files already have a different dwc2 line
# for the Compute Module 5. The [all] stops it landing inside a [cm4] or [cm5] section.
if ! grep -q '^dtoverlay=dwc2,dr_mode=peripheral' "$BOOT_CONFIG"; then
    printf '\n[all]\n# Desk presence: USB keyboard mode on the USB-C port\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$BOOT_CONFIG"
fi
printf 'dwc2\nlibcomposite\n' > /etc/modules-load.d/desk-presence.conf
cp "$HERE/setup/99-desk-presence.rules" /etc/udev/rules.d/
usermod -aG plugdev,dialout,video "$USER_NAME"

echo "Setting up your settings file..."
mkdir -p "$USER_HOME/desk-presence"
if [ ! -f "$USER_HOME/desk-presence/config.toml" ]; then
    cp "$HERE/config.example.toml" "$USER_HOME/desk-presence/config.toml"
fi
chown -R "$USER_NAME:" "$USER_HOME/desk-presence"

echo "Installing start-at-boot services..."
cp "$HERE/setup/desk-presence-gadget.service" /etc/systemd/system/
sed -e "s|__USER__|$USER_NAME|" -e "s|__HOME__|$USER_HOME|" \
    "$HERE/setup/desk-presence.service" > /etc/systemd/system/desk-presence.service
systemctl daemon-reload
systemctl enable desk-presence-gadget.service desk-presence.service

echo
echo "Done. Restart the Pi (sudo reboot) to switch on keyboard mode."
echo "It starts in practice mode: it only writes down what it would do."
echo "Settings: $USER_HOME/desk-presence/config.toml"
