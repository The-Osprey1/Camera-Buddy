"""What the camera's pose model reports, and simple measurements on it.

Coordinates are fractions of the picture: x from 0 (left) to 1 (right),
y from 0 (top) to 1 (bottom).
"""

from __future__ import annotations

import math
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

    def box(self, min_score: float) -> tuple[float, float, float, float] | None:
        """(left, top, right, bottom) of the box around the confident points."""
        pts = [(x, y) for x, y, s in self.keypoints if s >= min_score]
        if len(pts) < 2:
            return None
        xs, ys = zip(*pts)
        return min(xs), min(ys), max(xs), max(ys)

    def area(self, min_score: float) -> float:
        """Size of the box around the confident points."""
        box = self.box(min_score)
        if box is None:
            return 0.0
        left, top, right, bottom = box
        return (right - left) * (bottom - top)

    def middle(self, min_score: float) -> tuple[float, float] | None:
        box = self.box(min_score)
        if box is None:
            return None
        left, top, right, bottom = box
        return (left + right) / 2, (top + bottom) / 2

    def shoulder_y(self, min_score: float) -> float | None:
        ys = [self.keypoints[i][1] for i in (LEFT_SHOULDER, RIGHT_SHOULDER) if self.visible(i, min_score)]
        return sum(ys) / len(ys) if ys else None


def same_person(a: Person, b: Person, min_score: float) -> bool:
    """The pose model sometimes reports one person twice, almost on top of each
    other, or splits them into two bodies (say a left and a right half)."""
    box_a, box_b = a.box(min_score), b.box(min_score)
    if box_a is None or box_b is None:
        return False
    size = max(box_a[2] - box_a[0], box_a[3] - box_a[1], box_b[2] - box_b[0], box_b[3] - box_b[1])
    shared = [i for i in range(len(a.keypoints)) if a.visible(i, min_score) and b.visible(i, min_score)]
    if len(shared) >= 2:
        # Copies put the same points in nearly the same places. Two real people
        # have their heads and shoulders apart.
        gaps = sorted(math.dist(a.keypoints[i][:2], b.keypoints[i][:2]) for i in shared)
        return gaps[len(gaps) // 2] <= 0.15 * size
    # Little in common to compare: pieces of one body overlap or nearly touch.
    gap_x = max(box_a[0], box_b[0]) - min(box_a[2], box_b[2])
    gap_y = max(box_a[1], box_b[1]) - min(box_a[3], box_b[3])
    return max(gap_x, gap_y) <= 0.25 * size


@dataclass
class Frame:
    time: float
    people: list[Person] = field(default_factory=list)
    # Scene brightness estimated by the camera, if it reports one.
    lux: float | None = None
    # The camera's picture (480 x 640, BGR), only when face unlock asked for it. Never saved.
    image: object = None
