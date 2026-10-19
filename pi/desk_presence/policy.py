"""Decides when to lock. All the safeguards against accidental locks live here.

- It only locks after it has seen you seated (so starting the Pi in an empty room does nothing).
- Standing up, stretching or turning around never locks; your body has to leave the
  picture and stay gone for `away_seconds`.
- A body has to be seen for `return_confirm_seconds` before it counts as back, so a
  one-frame flicker while you're away doesn't restart the countdown.
- Too dark to judge means do nothing, and the countdown starts over once it's light.
- One lock per absence. If the PC helper says the PC is already locked, it doesn't press anything.
- Paused means no actions at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import PresenceSettings, SecondPersonSettings
from .presence import Observation, State


@dataclass
class Decision:
    action: str  # "lock" or "warn_second_person"
    reason: str


class LockPolicy:
    def __init__(self, presence: PresenceSettings, second: SecondPersonSettings):
        self.p = presence
        self.s = second
        self.armed = False  # seen you seated since the last lock
        self.absent_since: float | None = None
        self.present_since: float | None = None
        self.second_since: float | None = None
        self.last_warning: float | None = None

    def step(self, obs: Observation, pc_locked: bool | None, paused: bool) -> list[Decision]:
        now = obs.time

        if obs.state is State.TOO_DARK:
            self.absent_since = None
            self.present_since = None
            self.second_since = None
            return []

        decisions: list[Decision] = []

        if obs.state is State.AWAY:
            self.present_since = None
            self.second_since = None
            if self.absent_since is None:
                self.absent_since = now
        else:
            if self.present_since is None:
                self.present_since = now
            if now - self.present_since >= self.p.return_confirm_seconds:
                self.absent_since = None
                if obs.state is State.SEATED and pc_locked is not True:
                    self.armed = True
            decisions += self._second_person(obs, now)

        if (
            obs.state is State.AWAY
            and self.armed
            and self.absent_since is not None
            and now - self.absent_since >= self.p.away_seconds
        ):
            self.armed = False  # one lock per absence
            if pc_locked is True:
                pass  # you locked it yourself
            elif not paused:
                away_for = now - self.absent_since
                decisions.append(Decision("lock", f"away for {away_for:.1f} seconds"))

        if paused:
            return []
        if pc_locked is True:
            return [d for d in decisions if d.action != "lock"]
        return decisions

    def _second_person(self, obs: Observation, now: float) -> list[Decision]:
        if not obs.second_person or self.s.action == "off":
            self.second_since = None
            return []
        if self.second_since is None:
            self.second_since = now
        if now - self.second_since < self.s.seconds:
            return []
        if self.last_warning is not None and now - self.last_warning < self.s.cooldown_seconds:
            return []
        self.last_warning = now
        out = [Decision("warn_second_person", "a second person is in view")]
        if self.s.action == "lock":
            self.armed = False
            out.append(Decision("lock", "a second person is in view"))
        return out
