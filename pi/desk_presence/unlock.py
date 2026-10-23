"""Decides when to unlock, and types your PIN. All the unlock safeguards live here.

- Only after the Pi itself locked the PC because you walked away, at most `max_hours`
  ago. After the Pi restarts, or the PC sleeps or restarts, you unlock by hand.
- Only at the real Windows lock screen, as the PC helper reports it. Never at an admin
  ("allow changes?") prompt, and never without the helper.
- Only once you're seated, with nobody else in view, and only when your face matches
  on `match_frames` checks in a row over at least `match_seconds`.
- The face has to sit on your body at your normal distance (see faces.fits_body).
- A matching face that looks like a photo or a screen on `fake_frames` checks turns
  auto-unlock off until you unlock by hand once.
- One PIN attempt per return. If the PC is still locked afterwards (say you changed
  your PIN), it forgets the PIN and stops until you unlock by hand once.
- It looks for `give_up_seconds` after you sit down, then waits for you to leave and come back.
- Paused or too dark means nothing at all.
- The PIN is only ever in memory. It's never written to the log or to a file.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from . import protocol
from .config import UnlockSettings
from .faces import FaceCheck
from .presence import Observation, State

# The helper quiet for this long means the PC slept, restarted or was unplugged.
FORGET_AFTER_SILENT_SECONDS = 60.0


@dataclass
class UnlockDecision:
    action: str  # "unlock", "would_unlock", "not_recognized" or "fake_face"
    details: dict = field(default_factory=dict)


class UnlockPolicy:
    def __init__(self, settings: UnlockSettings, enrolled: bool, live: bool):
        self.s = settings
        # Live: types your PIN for real. Otherwise practice: writes down what it would do.
        self.live = live
        self.active = enrolled and settings.mode != "off"
        self.pin: str | None = None
        self.locked_at: float | None = None  # when the Pi locked the PC (or would have)
        self.off_until_hand_unlock = False
        self.last_status: str | None = None
        self._new_return()

    def _new_return(self) -> None:
        self.seated_since: float | None = None
        self.given_up = False
        self.streak: list[float] = []
        self.fakes = 0
        self.looks = 0
        self.best: float | None = None

    def note_lock(self, now: float) -> None:
        """The Pi locked the PC (or in practice, would have)."""
        if self.active:
            self.locked_at = now
            self._new_return()

    def forget_lock(self) -> None:
        self.locked_at = None
        self._new_return()

    def set_pin(self, pin: str) -> None:
        self.pin = pin

    @property
    def needs_pin(self) -> bool:
        return self.active and self.live and self.pin is None

    def _ready(self, status: str | None) -> bool:
        if not self.active or self.locked_at is None or self.off_until_hand_unlock:
            return False
        if self.live:
            return status == protocol.LOCK_SCREEN and self.pin is not None
        return True

    def wants_faces(self, obs: Observation, status: str | None) -> bool:
        """Whether this moment is worth a look at the picture."""
        return (
            self._ready(status)
            and obs.state is State.SEATED
            and not obs.second_person
            and not self.given_up
        )

    def step(
        self,
        obs: Observation,
        check: FaceCheck | None,
        status: str | None,
        paused: bool,
        helper_silent_for: float | None = 0.0,
    ) -> list[UnlockDecision]:
        now = obs.time
        came_unlocked = status == protocol.UNLOCKED and self.last_status in (protocol.LOCKED, protocol.LOCK_SCREEN)
        self.last_status = status if status is not None else self.last_status
        if came_unlocked:
            # Unlocked, by you or by the Pi: nothing left to unlock, and a hand unlock
            # turns auto-unlock back on.
            self.off_until_hand_unlock = False
            if self.live:
                self.forget_lock()
        if self.live and helper_silent_for is not None and helper_silent_for > FORGET_AFTER_SILENT_SECONDS:
            self.forget_lock()
        if self.locked_at is not None and now - self.locked_at > self.s.max_hours * 3600:
            self.forget_lock()

        if paused or obs.state is State.TOO_DARK or not self._ready(status):
            return []
        if obs.state is State.AWAY:
            self._new_return()
            return []
        if obs.state is State.STOOD_UP:
            self.streak = []
            return []

        if self.seated_since is None:
            self.seated_since = now
        if self.given_up:
            return []

        out: list[UnlockDecision] = []
        if obs.second_person:
            self.streak = []
        elif check is not None and check.usable:
            self.looks += 1
            self.best = check.similarity if self.best is None else max(self.best, check.similarity)
            if check.similarity >= self.s.match_threshold:
                if check.real_score is not None and check.real_score < self.s.real_face_threshold:
                    self.fakes += 1
                    self.streak = []
                    if self.fakes >= self.s.fake_frames:
                        self.given_up = True
                        if self.live:
                            self.off_until_hand_unlock = True
                        out.append(UnlockDecision("fake_face", self._details(now, check)))
                        return out
                else:
                    self.streak.append(now)
            else:
                self.streak = []

        if len(self.streak) >= self.s.match_frames and self.streak[-1] - self.streak[0] >= self.s.match_seconds:
            out.append(UnlockDecision("unlock" if self.live else "would_unlock", self._details(now, check)))
            self.locked_at = None
            self._new_return()
        elif now - self.seated_since >= self.s.give_up_seconds:
            self.given_up = True
            out.append(UnlockDecision("not_recognized", self._details(now, check)))
        return out

    def _details(self, now: float, check: FaceCheck | None) -> dict:
        d = {
            "seconds_after_sitting": round(now - (self.seated_since or now), 1),
            "looks": self.looks,
            "best_match": None if self.best is None else round(self.best, 3),
        }
        if check is not None and check.real_score is not None:
            d["real_face_score"] = round(check.real_score, 2)
        if self.fakes:
            d["fake_looks"] = self.fakes
        return d

    def unlock_failed(self) -> None:
        """The PIN didn't work: forget it, and stop until you unlock by hand."""
        self.pin = None
        self.off_until_hand_unlock = True


def type_pin(
    keyboard,
    status: Callable[[], str | None],
    pin: str,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Wakes the lock screen and types your PIN, once.

    Returns "unlocked", "not_typed" (the lock screen went away first, so nothing was
    typed) or "failed" (typed, and the PC is still locked).
    """
    keyboard.wake()
    sleep(1.0)
    # A second press in case the first only woke the screen.
    keyboard.wake()
    sleep(1.5)
    if status() != protocol.LOCK_SCREEN:
        return "not_typed"
    keyboard.type_text(pin)
    if not pin.isdigit():
        # Windows signs in by itself after the last digit of a digits-only PIN, so
        # Enter is only needed (and only pressed) for a PIN with letters.
        keyboard.enter()
    deadline = clock() + 6.0
    while clock() < deadline:
        sleep(0.25)
        if status() == protocol.UNLOCKED:
            return "unlocked"
    if status() == protocol.LOCK_SCREEN:
        keyboard.clear(len(pin))  # so you can type yours into an empty box
    return "failed"
