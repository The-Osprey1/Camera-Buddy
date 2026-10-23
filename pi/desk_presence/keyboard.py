"""Pressing keys on the PC by pretending to be a USB keyboard.

The Pi's USB-C port is set up as a keyboard by setup/usb-gadget.sh, which creates
/dev/hidg0. Writing an 8-byte "report" to it is the same as a key going down;
writing 8 zero bytes lets go of every key.

Typing uses the main keys (not the number pad, which depends on Num Lock), and the
codes are for key positions, so letters assume an English (US or UK) keyboard layout.
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

LEFT_CTRL = 0x01
LEFT_SHIFT = 0x02
LEFT_GUI = 0x08  # the Windows key
KEY_L = 0x0F
KEY_ENTER = 0x28
KEY_BACKSPACE = 0x2A
NO_KEY = 0x00


def key_for(char: str) -> tuple[int, int]:
    """(modifiers, key) that type one letter or digit."""
    if "a" <= char <= "z":
        return 0, 0x04 + ord(char) - ord("a")
    if "A" <= char <= "Z":
        return LEFT_SHIFT, 0x04 + ord(char) - ord("A")
    if "1" <= char <= "9":
        return 0, 0x1E + ord(char) - ord("1")
    if char == "0":
        return 0, 0x27
    raise ValueError("only letters and digits can be typed")


def report(modifiers: int, key: int) -> bytes:
    return bytes([modifiers, 0, key, 0, 0, 0, 0, 0])


RELEASE = bytes(8)


class UsbKeyboard:
    def __init__(self, device: str):
        self.device = device

    def press(self, modifiers: int, key: int, hold_seconds: float = 0.02) -> None:
        # Non-blocking, so an unplugged cable raises an error instead of freezing the program.
        flags = os.O_WRONLY | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(self.device, flags)
        try:
            self._send(fd, report(modifiers, key))
            if hold_seconds > 0:
                time.sleep(hold_seconds)
            self._send(fd, RELEASE)
        finally:
            os.close(fd)

    @staticmethod
    def _send(fd: int, data: bytes, timeout: float = 0.5) -> None:
        # The PC collects one report at a time, every few milliseconds. Until it has
        # taken the last one, writing says "try again".
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.write(fd, data)
                return
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise OSError("the PC isn't reading the keyboard (is the cable plugged in?)")
                time.sleep(0.005)

    def lock_pc(self) -> None:
        self.press(LEFT_GUI, KEY_L)

    def wake(self) -> None:
        """Ctrl on its own: wakes the screen and opens the lock screen, and types nothing."""
        self.press(LEFT_CTRL, NO_KEY)

    def type_text(self, text: str, inter_key_delay: float = 0.035) -> None:
        keys = [key_for(c) for c in text]  # check every character before pressing any
        for i, (modifiers, key) in enumerate(keys):
            if i > 0 and inter_key_delay > 0:
                time.sleep(inter_key_delay)
            self.press(modifiers, key)

    def enter(self) -> None:
        self.press(0, KEY_ENTER)

    def clear(self, count: int, inter_key_delay: float = 0.02) -> None:
        for i in range(count):
            if i > 0 and inter_key_delay > 0:
                time.sleep(inter_key_delay)
            self.press(0, KEY_BACKSPACE)


class PracticeKeyboard:
    """Practice mode: writes down what it would press, presses nothing."""

    def __init__(self):
        self.would_have_pressed: list[str] = []

    def lock_pc(self) -> None:
        self.would_have_pressed.append("Windows+L")
        log.info("Practice mode: would have pressed Windows+L")

    def wake(self) -> None:
        self.would_have_pressed.append("Ctrl")

    def type_text(self, text: str) -> None:
        # Never the PIN itself, not even in memory for the log.
        self.would_have_pressed.append("(your PIN)")

    def enter(self) -> None:
        self.would_have_pressed.append("Enter")

    def clear(self, count: int) -> None:
        self.would_have_pressed.append("Backspace")
