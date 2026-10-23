"""The few fixed messages the Pi and the PC helper send each other over the USB cable.

Anything not on these lists is ignored, so the cable can't be used to run anything.
One message per line, plain text.
"""

from __future__ import annotations

# PC helper -> Pi
FROM_PC = {"LOCKED", "UNLOCKED", "PAUSE"}

# Pi -> PC helper
PAUSED = "PAUSED"
RESUMED = "RESUMED"
WARN_SECOND_PERSON = "WARN SECOND_PERSON"
LOCKING = "LOCKING"
WOULD_LOCK = "WOULD_LOCK"
# What the camera sees (by presence.State value), for the helper's log.
CAMERA = {
    "seated": "CAMERA SEATED",
    "stood_up": "CAMERA STOOD_UP",
    "away": "CAMERA AWAY",
    "too_dark": "CAMERA TOO_DARK",
}
FROM_PI = {PAUSED, RESUMED, WARN_SECOND_PERSON, LOCKING, WOULD_LOCK, *CAMERA.values()}

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
