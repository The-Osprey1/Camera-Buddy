from desk_presence.config import PresenceSettings, SecondPersonSettings
from desk_presence.pose import (
    LEFT_EAR,
    LEFT_EYE,
    LEFT_SHOULDER,
    NOSE,
    RIGHT_EAR,
    RIGHT_EYE,
    RIGHT_SHOULDER,
    Person,
)
from desk_presence.presence import PresenceTracker, State
from helpers import frame, person


def tracker():
    return PresenceTracker(PresenceSettings(), SecondPersonSettings())


def only(body, points):
    """The same body with only these points found."""
    return Person([kp if i in points else (0.0, 0.0, 0.0) for i, kp in enumerate(body.keypoints)])


def test_empty_picture_is_away():
    assert tracker().update(frame(0)).state is State.AWAY


def test_seated_then_stood_up():
    t = tracker()
    assert t.update(frame(0, [person(0.6)])).state is State.SEATED
    assert t.update(frame(1, [person(0.62)])).state is State.SEATED
    assert t.update(frame(2, [person(0.35)])).state is State.STOOD_UP


def test_too_dark_means_no_judgement():
    assert tracker().update(frame(0, [person()], lux=1.0)).state is State.TOO_DARK


def test_unknown_brightness_is_not_dark():
    assert tracker().update(frame(0, [person()], lux=None)).state is State.SEATED


def test_low_confidence_points_do_not_count():
    assert tracker().update(frame(0, [person(score=0.1)])).state is State.AWAY


def test_small_person_far_behind_is_ignored():
    obs = tracker().update(frame(0, [person(), person(x=0.9, scale=0.3)]))
    assert not obs.second_person


def test_person_close_behind_counts():
    obs = tracker().update(frame(0, [person(), person(x=0.8, scale=0.8)]))
    assert obs.second_person


def test_person_leaning_in_beside_you_counts():
    obs = tracker().update(frame(0, [person(), person(x=0.72, shoulder_y=0.55)]))
    assert obs.second_person


def test_you_reported_twice_count_once():
    # The pose model sometimes reports the same body twice, almost on top of itself.
    t = tracker()
    seen = frame(0, [person(), person(x=0.51, shoulder_y=0.61)])
    assert len(t.bodies(seen)) == 1
    assert not t.update(seen).second_person


def test_you_split_in_two_count_once():
    you = person()
    left = only(you, {LEFT_EYE, LEFT_EAR, LEFT_SHOULDER})
    right = only(you, {NOSE, RIGHT_EYE, RIGHT_EAR, RIGHT_SHOULDER})
    t = tracker()
    seen = frame(0, [left, right])
    assert len(t.bodies(seen)) == 1
    assert not t.update(seen).second_person
