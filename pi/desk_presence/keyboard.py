"""Pressing keys on the PC by pretending to be a USB keyboard.

The Pi's USB-C port is set up as a keyboard by setup/usb-gadget.sh, which creates
/dev/hidg0. Writing an 8-byte "report" to it is the same as a key going down;
writing 8 zero bytes lets go of every key.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

LEFT_GUI = 0x08  # the Windows key
KEY_L = 0x0F


def report(modifiers: int, key: int) -> bytes:
    return bytes([modifiers, 0, key, 0, 0, 0, 0, 0])


RELEASE = bytes(8)


class UsbKeyboard:
    def __init__(self, device: str):
        self.device = device

    def press(self, modifiers: int, key: int) -> None:
        # Non-blocking, so an unplugged cable raises an error instead of freezing the program.
        fd = os.open(self.device, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, report(modifiers, key))
            os.write(fd, RELEASE)
        finally:
            os.close(fd)

    def lock_pc(self) -> None:
        self.press(LEFT_GUI, KEY_L)


class PracticeKeyboard:
    """Practice mode: writes down what it would press, presses nothing."""

    def __init__(self):
        self.would_have_pressed: list[str] = []

    def lock_pc(self) -> None:
        self.would_have_pressed.append("Windows+L")
        log.info("Practice mode: would have pressed Windows+L")
