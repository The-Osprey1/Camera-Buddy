import io

import pytest

from desk_presence import check
from desk_presence.check import NOT_YET, OK, PROBLEM, WARNING
from desk_presence.config import Settings
from desk_presence.pose import Frame
from desk_presence.presence import PresenceTracker
from helpers import person

# When each step watches, with the default settings: sit 5-20 s, stand 25-40 s,
# walk away 45-65 s, come back 65-80 s.
SITTING, STANDING = person(shoulder_y=0.6), person(shoulder_y=0.38)


def you_do_it_all(t):
    if t < 26:
        return [SITTING]
    if t < 48:
        return [STANDING]
    if t < 68:
        return []
    return [SITTING]


def standing_looks_like_leaving(t):
    return [] if 26 <= t < 40 else you_do_it_all(t)


class FakeCamera:
    """Plays a scene at 10 results a second, on a pretend clock."""

    def __init__(self, scene, stop_at=None):
        self.scene = scene
        self.stop_at = stop_at
        self.now = 0.0

    def clock(self):
        return self.now

    def frames(self):
        while True:
            self.now = round(self.now + 0.1, 3)
            if self.stop_at is not None and self.now >= self.stop_at:
                yield None
            else:
                yield Frame(self.now, self.scene(self.now), 100.0)


def watch(scene, settings=None):
    settings = settings or Settings()
    cam = FakeCamera(scene)
    tracker = PresenceTracker(settings.presence, settings.second_person)
    return check.run_steps(cam.frames(), tracker, check.steps(settings), cam.clock, io.StringIO())


def test_everything_done_right_passes():
    seen = watch(you_do_it_all)
    s = Settings()
    assert check.judge_sitting(seen["sit"], s).status == OK
    standing = check.judge_standing(seen["stand"], s)
    assert standing.status == OK and standing.detail.startswith("no lock (standing 9")
    leaving = check.judge_leaving(seen["leave"], s)
    assert leaving.status == OK and "locked 8 s after you set off" in leaving.detail
    assert check.judge_back(seen["back"]).detail == "it saw you again 3 s after the beep."
    assert check.judge_second_person(seen["sit"] + seen["stand"]).status == OK


def test_standing_that_would_lock_is_a_problem():
    seen = watch(standing_looks_like_leaving)
    result = check.judge_standing(seen["stand"], Settings())
    assert result.status == PROBLEM and "Tilt the camera up" in result.detail


def test_brief_loss_while_standing_is_a_warning():
    seen = watch(lambda t: [] if 30 <= t < 33 else you_do_it_all(t))
    assert check.judge_standing(seen["stand"], Settings()).status == WARNING


def test_camera_that_cant_see_your_chair():
    seen = watch(lambda t: [person(x=0.9)] if t < 26 else you_do_it_all(t))
    result = check.judge_sitting(seen["sit"], Settings())
    assert result.status == OK and "edge of the picture" in result.detail
    seen = watch(lambda t: [] if t < 26 else you_do_it_all(t))
    assert check.judge_sitting(seen["sit"], Settings()).status == PROBLEM


def test_something_that_looks_like_a_person_stops_the_lock():
    seen = watch(lambda t: [STANDING] if 45 <= t < 65 else you_do_it_all(t))
    result = check.judge_leaving(seen["leave"], Settings())
    assert result.status == PROBLEM and "coat on the chair" in result.detail


def test_camera_that_stops_is_reported():
    settings = Settings()
    cam = FakeCamera(you_do_it_all, stop_at=30)
    tracker = PresenceTracker(settings.presence, settings.second_person)
    with pytest.raises(check.CameraProblem):
        check.run_steps(cam.frames(), tracker, check.steps(settings), cam.clock, io.StringIO())


def test_power_readings():
    assert check.parse_throttled("throttled=0x50005\n") == 0x50005
    assert check.parse_throttled("error") is None
    assert check.judge_power(0).status == OK
    assert check.judge_power(0x50005).status == PROBLEM
    assert check.judge_power(0x50000).status == WARNING
    assert check.judge_power(0x80000).status == WARNING
    assert check.judge_power(None).status == WARNING


def test_keyboard_readings():
    assert check.judge_keyboard("configured", True).status == OK
    assert check.judge_keyboard("not attached", True).status == NOT_YET
    assert check.judge_keyboard(None, False).status == PROBLEM
    assert check.judge_keyboard("configured", False).status == PROBLEM
    assert check.judge_keyboard("suspended", True).status == WARNING


def test_next_step_points_at_the_first_problem():
    results = [
        check.Result(OK, "Power", "steady."),
        check.Result(NOT_YET, "Keyboard mode", "ready"),
        check.Result(PROBLEM, "Standing up", "it lost sight of you"),
    ]
    assert check.next_step(results) == "Next: sort out the standing up problem, then type desk-check again."
    assert check.next_step(results[:2]).startswith("Next: connect the Pi to your PC through a USB-C power")
    assert check.next_step(results[:1]) == "Everything works."


def run_check(monkeypatch, tmp_path, scene, usb=("configured", True)):
    monkeypatch.setattr(check, "read_throttled", lambda: 0)
    monkeypatch.setattr(check, "read_usb", lambda settings: usb)
    settings = Settings(event_log=str(tmp_path / "events.jsonl"))
    cam = FakeCamera(scene)
    out = io.StringIO()
    assert check.main(settings, out, camera=lambda model: cam, clock=cam.clock) == 0
    return out.getvalue(), list((tmp_path / "checks").iterdir())


def test_whole_check(monkeypatch, tmp_path):
    text, saved = run_check(monkeypatch, tmp_path, you_do_it_all)
    assert "1/4  SIT AT YOUR DESK" in text and "4/4  COME BACK AND SIT DOWN." in text
    assert "OK       Walking away: it would have locked 8 s after you set off." in text
    assert "Everything works." in text
    assert "Practice mode is on" in text
    assert len(saved) == 1 and "TIMELINE" in saved[0].read_text()


def test_whole_check_with_a_problem(monkeypatch, tmp_path):
    text, _ = run_check(monkeypatch, tmp_path, standing_looks_like_leaving, usb=("not attached", True))
    assert "PROBLEM  Standing up: it lost sight of you" in text
    assert "NOT YET  Keyboard mode" in text
    assert "Next: sort out the standing up problem" in text


def test_camera_missing_still_reports_power_and_keyboard(monkeypatch, tmp_path):
    monkeypatch.setattr(check, "read_throttled", lambda: 0)
    monkeypatch.setattr(check, "read_usb", lambda settings: ("configured", True))

    def no_camera(model):
        raise check.CameraProblem("the Pi can't find the camera.")

    out = io.StringIO()
    settings = Settings(event_log=str(tmp_path / "events.jsonl"))
    assert check.main(settings, out, camera=no_camera) == 0
    text = out.getvalue()
    assert "OK       Power: steady." in text
    assert "PROBLEM  Camera: the Pi can't find the camera." in text
    assert "Next: sort out the camera problem" in text
    assert text.index("Camera:") < text.index("Keyboard mode:")
