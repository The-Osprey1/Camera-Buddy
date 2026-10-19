"""What the camera's pose model reports, and simple measurements on it.

Coordinates are fractions of the picture: x from 0 (left) to 1 (right),
y from 0 (top) to 1 (bottom).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Order of the 17 body points in the standard (COCO) pose models.
NOSE, LEFT_EYE, RIGHT_EYE, LEFT_EAR, RIGHT_EAR = 0, 1, 2, 3, 4
LEFT_SHOULDER, RIGHT_SHOULDER = 5, 6
LEFT_HIP, RIGHT_HIP = 11, 12

UPPER_BODY = (NOSE, LEFT_EYE, RIGHT_EYE, LEFT_EAR, RIGHT_EAR, LEFT_SHOULDER, RIGHT_SHOULDER)


@dataclass
class Person:
    # 17 entries of (x, y, score).
    keypoints: list[tuple[float, float, float]]

    def visible(self, index: int, min_score: float) -> bool:
        return self.keypoints[index][2] >= min_score

    def upper_body_points(self, min_score: float) -> int:
        return sum(1 for i in UPPER_BODY if self.visible(i, min_score))

    def area(self, min_score: float) -> float:
        """Size of the box around the confident points."""
        pts = [(x, y) for x, y, s in self.keypoints if s >= min_score]
        if len(pts) < 2:
            return 0.0
        xs, ys = zip(*pts)
        return (max(xs) - min(xs)) * (max(ys) - min(ys))

    def shoulder_y(self, min_score: float) -> float | None:
        ys = [self.keypoints[i][1] for i in (LEFT_SHOULDER, RIGHT_SHOULDER) if self.visible(i, min_score)]
        return sum(ys) / len(ys) if ys else None


@dataclass
class Frame:
    time: float
    people: list[Person] = field(default_factory=list)
    # Scene brightness estimated by the camera, if it reports one.
    lux: float | None = None
