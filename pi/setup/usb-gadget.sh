#!/bin/bash
# Makes the Pi's USB-C port show up on the PC as two things on one cable:
#   1. an ordinary keyboard (/dev/hidg0 on the Pi), used to press Windows+L
#   2. a simple serial data line (/dev/ttyGS0 on the Pi, a COM port on Windows),
#      used by the PC helper to say whether the PC is locked.
# Run as root at every boot (desk-presence-gadget.service does this).
#
# Safe to run again at any time: it finishes a setup that stopped half way and
# reconnects to the PC if the link was dropped, without touching a working link.
set -euo pipefail

# The three DESK_ settings exist only so the tests can run this on a fake folder.
CONFIGFS="${DESK_CONFIGFS:-/sys/kernel/config}"
UDC_CLASS="${DESK_UDC_CLASS:-/sys/class/udc}"
G="$CONFIGFS/usb_gadget/deskpresence"
if [ -z "${DESK_CONFIGFS:-}" ]; then
    modprobe libcomposite
    modprobe dwc2 2>/dev/null || true
    mountpoint -q "$CONFIGFS" || mount -t configfs none "$CONFIGFS"
fi

# The USB-C controller can appear a few seconds after boot. Writing to the
# gadget before it exists used to fail once and leave the PC with no keyboard
# until the next restart.
controller() { ls "$UDC_CLASS" 2>/dev/null | head -n 1; }
for _ in $(seq "${DESK_UDC_WAIT:-30}"); do
    [ -n "$(controller)" ] && break
    sleep 1
done
UDC_NAME="$(controller)"
if [ -z "$UDC_NAME" ]; then
    echo "No USB-C controller found. Is dtoverlay=dwc2,dr_mode=peripheral in config.txt?" >&2
    exit 1
fi

if [ -n "$(cat "$G/UDC" 2>/dev/null)" ]; then
    echo "USB gadget already set up and connected"
    exit 0
fi

# Every step below checks first, so a half-made gadget gets finished.
mkdir -p "$G"
cd "$G"
echo 0x1d6b > idVendor    # Linux Foundation
echo 0x0104 > idProduct   # Multifunction composite device
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB
# Lets Windows use its built-in drivers for a keyboard plus serial port on one cable.
echo 0xEF > bDeviceClass
echo 0x02 > bDeviceSubClass
echo 0x01 > bDeviceProtocol

mkdir -p strings/0x409
echo "deskpresence0001" > strings/0x409/serialnumber
echo "Raspberry Pi"     > strings/0x409/manufacturer
echo "Desk Presence"    > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "Keyboard and helper link" > configs/c.1/strings/0x409/configuration
echo 100 > configs/c.1/MaxPower

# Keyboard: standard 8-byte boot keyboard. Its settings can't change once it is
# linked into the configuration, so only write them the first time.
if [ ! -d functions/hid.usb0 ]; then
    mkdir -p functions/hid.usb0
    echo 1 > functions/hid.usb0/protocol
    echo 1 > functions/hid.usb0/subclass
    echo 8 > functions/hid.usb0/report_length
    echo -ne '\x05\x01\x09\x06\xa1\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0' \
        > functions/hid.usb0/report_desc
fi

# Serial data line for the PC helper.
mkdir -p functions/acm.usb0

[ -L configs/c.1/hid.usb0 ] || ln -s functions/hid.usb0 configs/c.1/
[ -L configs/c.1/acm.usb0 ] || ln -s functions/acm.usb0 configs/c.1/

echo "$UDC_NAME" > UDC
echo "USB gadget ready: keyboard and helper link"
