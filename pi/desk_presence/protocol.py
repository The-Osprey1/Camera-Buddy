"""The few fixed messages the Pi and the PC helper send each other over the USB cable.

Anything not on these lists is ignored, so the cable can't be used to run anything.
One message per line, plain text. The only message that carries anything is the PIN,
and it can only be letters and digits.
"""

from __future__ import annotations

import re

# PC helper -> Pi. LOCK_SCREEN means the Windows lock screen itself is showing.
# LOCKED means the PC can't be used right now for another reason too, such as an
# admin ("allow changes?") prompt, where a PIN must never be typed.
LOCKED = "LOCKED"
LOCK_SCREEN = "LOCK_SCREEN"
UNLOCKED = "UNLOCKED"
PAUSE = "PAUSE"
FROM_PC = {LOCKED, LOCK_SCREEN, UNLOCKED, PAUSE}
# "PIN 1234": your Windows PIN, typed into the helper's prompt. The Pi keeps it in memory only.
PIN_PREFIX = "PIN "
PIN_PATTERN = re.compile(r"[A-Za-z0-9]{4,20}")

# Pi -> PC helper
PAUSED = "PAUSED"
RESUMED = "RESUMED"
WARN_SECOND_PERSON = "WARN SECOND_PERSON"
LOCKING = "LOCKING"
WOULD_LOCK = "WOULD_LOCK"
UNLOCKING = "UNLOCKING"
WOULD_UNLOCK = "WOULD_UNLOCK"
UNLOCK_FAILED = "UNLOCK_FAILED"
FACE_REFUSED = "FACE_REFUSED"
NEED_PIN = "NEED_PIN"
PIN_OK = "PIN_OK"
FROM_PI = {
    PAUSED, RESUMED, WARN_SECOND_PERSON, LOCKING, WOULD_LOCK,
    UNLOCKING, WOULD_UNLOCK, UNLOCK_FAILED, FACE_REFUSED, NEED_PIN, PIN_OK,
}

MAX_LINE = 32


def parse(line: bytes | str, allowed: set[str]) -> str | None:
    if isinstance(line, bytes):
        if len(line) > MAX_LINE + 2:
            return None
        try:
            line = line.decode("ascii")
        except UnicodeDecodeError:
            return None
    message = line.strip()
    return message if message in allowed else None


def parse_pin(line: bytes | str) -> str | None:
    """The PIN from a "PIN 1234" line, or None if the line isn't one."""
    if isinstance(line, bytes):
        if len(line) > MAX_LINE + 2:
            return None
        try:
            line = line.decode("ascii")
        except UnicodeDecodeError:
            return None
    line = line.strip()
    if not line.startswith(PIN_PREFIX):
        return None
    pin = line[len(PIN_PREFIX):]
    return pin if PIN_PATTERN.fullmatch(pin) else None
