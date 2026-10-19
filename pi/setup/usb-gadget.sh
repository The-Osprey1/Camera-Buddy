#!/bin/bash
# Makes the Pi's USB-C port show up on the PC as two things on one cable:
#   1. an ordinary keyboard (/dev/hidg0 on the Pi), used to press Windows+L
#   2. a simple serial data line (/dev/ttyGS0 on the Pi, a COM port on Windows),
#      used by the PC helper to say whether the PC is locked.
# Run as root at every boot (desk-presence-gadget.service does this).
set -euo pipefail

G=/sys/kernel/config/usb_gadget/deskpresence
modprobe libcomposite

if [ -d "$G" ]; then
    echo "USB gadget already set up"
    exit 0
fi

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

# Keyboard: standard 8-byte boot keyboard.
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 8 > functions/hid.usb0/report_length
echo -ne '\x05\x01\x09\x06\xa1\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0' \
    > functions/hid.usb0/report_desc

# Serial data line for the PC helper.
mkdir -p functions/acm.usb0

ln -s functions/hid.usb0 configs/c.1/
ln -s functions/acm.usb0 configs/c.1/

ls /sys/class/udc > UDC
echo "USB gadget ready: keyboard and helper link"
