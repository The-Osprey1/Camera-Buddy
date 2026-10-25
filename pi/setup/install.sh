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

# python3-opencv and python3-munkres are needed by picamera2's decoder for the pose model's results.
PACKAGES=(imx500-all python3-picamera2 python3-opencv python3-munkres python3-serial python3-gpiozero python3-numpy python3-luma.oled python3-pil i2c-tools)
MISSING=no
for p in "${PACKAGES[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || MISSING=yes
done
# Skipped when everything is already there, so updates (setup/update.sh) are quick.
if [ "$MISSING" = yes ]; then
    echo "Installing camera and helper software..."
    apt-get update
    apt-get install -y "${PACKAGES[@]}"
fi

echo "Copying the program to /opt/desk-presence..."
mkdir -p /opt/desk-presence
rm -rf /opt/desk-presence/desk_presence /opt/desk-presence/setup
cp -r "$HERE/desk_presence" "$HERE/setup" /opt/desk-presence/
chmod +x /opt/desk-presence/setup/*.sh /opt/desk-presence/setup/desk-check /opt/desk-presence/setup/desk-face
# desk-check or desk-face may be the one running this (through update.sh), so swap
# them in one step instead of writing over them.
for tool in desk-check desk-face; do
    cp "/opt/desk-presence/setup/$tool" "/usr/local/bin/$tool.new"
    mv "/usr/local/bin/$tool.new" "/usr/local/bin/$tool"
done

echo "Downloading the face unlock models..."
bash /opt/desk-presence/setup/get-models.sh || echo "Face unlock will work once they're downloaded: type desk-face when the Pi is online."

echo "Turning on USB keyboard mode on the USB-C port..."
BOOT_CONFIG=/boot/firmware/config.txt
[ -f "$BOOT_CONFIG" ] || BOOT_CONFIG=/boot/config.txt
# Match this exact line: newer config.txt files already have a different dwc2 line
# for the Compute Module 5. The [all] stops it landing inside a [cm4] or [cm5] section.
RESTART_NEEDED=no
if ! grep -q '^dtoverlay=dwc2,dr_mode=peripheral' "$BOOT_CONFIG"; then
    printf '\n[all]\n# Desk presence: USB keyboard mode on the USB-C port\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$BOOT_CONFIG"
    RESTART_NEEDED=yes
fi
printf 'dwc2\nlibcomposite\n' > /etc/modules-load.d/desk-presence.conf
echo "Enabling I2C for the optional OLED display..."
# Always enable the header bus, including for older configs with no [display]
# section. raspi-config handles the active boot-file sections idempotently.
raspi-config nonint do_i2c 0
printf 'i2c-dev\n' > /etc/modules-load.d/desk-presence-i2c.conf
modprobe i2c-dev || true
[ -e /dev/i2c-1 ] || RESTART_NEEDED=yes
cp "$HERE/setup/99-desk-presence.rules" /etc/udev/rules.d/
usermod -aG plugdev,dialout,video,i2c "$USER_NAME"

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
# If it's already running, switch it over to the new copy.
systemctl try-restart desk-presence.service

echo
if [ "$RESTART_NEEDED" = yes ]; then
    echo "Done. Restart the Pi (sudo reboot) to switch on keyboard mode and I2C."
    echo "It starts in practice mode: it only writes down what it would do."
else
    echo "Done."
fi
echo "Settings: $USER_HOME/desk-presence/config.toml"
echo "To check everything works, type: desk-check"
