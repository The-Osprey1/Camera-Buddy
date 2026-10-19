"""Turns each camera frame into a simple observation: seated, stood up, away or too dark."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import PresenceSettings, SecondPersonSettings
from .pose import Frame, Person


class State(str, Enum):
    SEATED = "seated"
    STOOD_UP = "stood_up"
    AWAY = "away"
    TOO_DARK = "too_dark"


@dataclass
class Observation:
    time: float
    state: State
    # Another person of meaningful size is in view, besides you.
    second_person: bool = False


class PresenceTracker:
    def __init__(self, presence: PresenceSettings, second: SecondPersonSettings):
        self.p = presence
        self.s = second
        # Your usual seated shoulder height, learned while you sit.
        self.seated_shoulder_y: float | None = None

    def _bodies(self, frame: Frame) -> list[Person]:
        score = self.p.min_keypoint_score
        bodies = [b for b in frame.people if b.upper_body_points(score) >= self.p.min_upper_body_points]
        return sorted(bodies, key=lambda b: b.area(score), reverse=True)

    def update(self, frame: Frame) -> Observation:
        if self.p.min_lux > 0 and frame.lux is not None and frame.lux < self.p.min_lux:
            return Observation(frame.time, State.TOO_DARK)

        bodies = self._bodies(frame)
        if not bodies:
            return Observation(frame.time, State.AWAY)

        score = self.p.min_keypoint_score
        you = bodies[0]
        your_area = you.area(score)
        second = any(
            your_area > 0 and b.area(score) / your_area >= self.s.min_size_ratio for b in bodies[1:]
        )

        state = State.SEATED
        shoulder = you.shoulder_y(score)
        if shoulder is not None:
            if self.seated_shoulder_y is None:
                self.seated_shoulder_y = shoulder
            elif self.seated_shoulder_y - shoulder > self.p.stand_rise:
                state = State.STOOD_UP
            else:
                # Follow slow changes in how you sit (or where the camera points).
                self.seated_shoulder_y = 0.98 * self.seated_shoulder_y + 0.02 * shoulder
        return Observation(frame.time, state, second)
