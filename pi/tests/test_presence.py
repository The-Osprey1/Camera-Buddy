from desk_presence.config import PresenceSettings, SecondPersonSettings
from desk_presence.presence import PresenceTracker, State
from helpers import frame, person


def tracker():
    return PresenceTracker(PresenceSettings(), SecondPersonSettings())


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
