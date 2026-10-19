"""The pause switch: pauses everything for a while, or until pressed again."""

from __future__ import annotations


class PauseSwitch:
    def __init__(self, duration: float):
        self.duration = duration
        self.until: float | None = None

    def toggle(self, now: float) -> bool:
        """Flip the switch. Returns True if now paused."""
        if self.is_paused(now):
            self.until = None
            return False
        self.until = now + self.duration
        return True

    def is_paused(self, now: float) -> bool:
        if self.until is not None and now >= self.until:
            self.until = None
        return self.until is not None
