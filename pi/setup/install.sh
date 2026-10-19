#!/bin/bash
# Installs desk presence on Raspberry Pi OS (Bookworm or newer).
# Run from the pi/ folder:  sudo ./setup/install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run with sudo: sudo ./setup/install.sh"
    exit 1
fi
USER_NAME="${SUDO_USER:-pi}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "Installing camera and helper software..."
apt-get update
apt-get install -y imx500-all python3-picamera2 python3-serial python3-gpiozero python3-numpy

echo "Copying the program to /opt/desk-presence..."
mkdir -p /opt/desk-presence
cp -r "$HERE/desk_presence" "$HERE/setup" /opt/desk-presence/
chmod +x /opt/desk-presence/setup/usb-gadget.sh

echo "Turning on USB keyboard mode on the USB-C port..."
BOOT_CONFIG=/boot/firmware/config.txt
[ -f "$BOOT_CONFIG" ] || BOOT_CONFIG=/boot/config.txt
grep -q '^dtoverlay=dwc2' "$BOOT_CONFIG" || echo 'dtoverlay=dwc2,dr_mode=peripheral' >> "$BOOT_CONFIG"
grep -q '^dwc2' /etc/modules || echo 'dwc2' >> /etc/modules
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
