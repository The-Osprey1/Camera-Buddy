import json

from desk_presence import config, protocol
from desk_presence.app import DeskPresence
from desk_presence.config import UnlockSettings
from desk_presence.events import EventLog
from desk_presence.faces import FaceCheck, Faceprint
from desk_presence.keyboard import PracticeKeyboard, key_for
from desk_presence.presence import Observation, State
from desk_presence.unlock import UnlockPolicy, type_pin
from fake_faces import IMAGE, SOMEONE_ELSE, YOU, FakeEngine
from helpers import frame, person

import numpy as np
import pytest

SEATED, STOOD, AWAY, DARK = State.SEATED, State.STOOD_UP, State.AWAY, State.TOO_DARK
YOURS = FaceCheck(found=True, fits_body=True, similarity=0.8, real_score=3.0)
FAKE = FaceCheck(found=True, fits_body=True, similarity=0.8, real_score=-3.0)
STRANGER = FaceCheck(found=True, fits_body=True, similarity=0.1, real_score=3.0)


def run(policy, script, status=None, step=0.2, start=10.0):
    """script: list of (state, face check or None). Returns [(time, action)]."""
    out = []
    for i, (state, check) in enumerate(script):
        t = start + i * step
        second = state == "second"
        obs = Observation(t, SEATED if second else state, second)
        for d in policy.step(obs, check, status, paused=False):
            out.append((round(t, 1), d.action))
    return out


def practice():
    p = UnlockPolicy(UnlockSettings(), enrolled=True, live=False)
    p.note_lock(5.0)
    return p


def test_practice_would_unlock_after_steady_matches():
    actions = run(practice(), [(SEATED, YOURS)] * 10)
    # Five matches in a row, spread over 0.8 s: the fifth, at 10.8.
    assert actions == [(10.8, "would_unlock")]


def test_nothing_before_a_lock():
    p = UnlockPolicy(UnlockSettings(), enrolled=True, live=False)
    assert run(p, [(SEATED, YOURS)] * 20) == []


def test_nothing_without_an_enrolled_face_or_when_off():
    p = UnlockPolicy(UnlockSettings(), enrolled=False, live=False)
    p.note_lock(5.0)
    assert run(p, [(SEATED, YOURS)] * 20) == []
    p = UnlockPolicy(UnlockSettings(mode="off"), enrolled=True, live=False)
    p.note_lock(5.0)
    assert run(p, [(SEATED, YOURS)] * 20) == []


def test_only_once_per_lock():
    p = practice()
    actions = run(p, [(SEATED, YOURS)] * 10 + [(AWAY, None)] * 5 + [(SEATED, YOURS)] * 10)
    assert [a for _, a in actions] == ["would_unlock"]


def test_someone_else_never_unlocks_and_it_gives_up():
    actions = run(practice(), [(SEATED, STRANGER)] * 200)
    assert actions == [(40.0, "not_recognized")]  # 30 s after sitting down


def test_a_stranger_in_between_restarts_the_count():
    script = [(SEATED, YOURS)] * 4 + [(SEATED, STRANGER)] + [(SEATED, YOURS)] * 5
    assert run(practice(), script) == [(11.8, "would_unlock")]


def test_second_person_in_view_blocks_unlock():
    script = [(SEATED, YOURS)] * 3 + [("second", YOURS)] * 20
    assert run(practice(), script) == []


def test_standing_or_leaving_restarts_the_count():
    script = [(SEATED, YOURS)] * 4 + [(STOOD, None)] + [(SEATED, YOURS)] * 4
    assert run(practice(), script) == []
    script = [(SEATED, YOURS)] * 4 + [(AWAY, None)] + [(SEATED, YOURS)] * 4
    assert run(practice(), script) == []


def test_looking_away_for_a_moment_is_fine():
    script = [(SEATED, YOURS)] * 3 + [(SEATED, None)] * 2 + [(SEATED, YOURS)] * 2
    assert run(practice(), script) == [(11.2, "would_unlock")]


def test_too_dark_or_unusable_faces_do_nothing():
    assert run(practice(), [(DARK, YOURS)] * 20) == []
    dim = FaceCheck(found=True, fits_body=True, too_dark=True, similarity=0.9)
    off_body = FaceCheck(found=True, fits_body=False, similarity=0.9)
    assert run(practice(), [(SEATED, dim)] * 20 + [(SEATED, off_body)] * 20) == []


def test_lock_too_long_ago_is_forgotten():
    p = practice()
    actions = run(p, [(SEATED, YOURS)] * 10, start=5.0 + 4 * 3600 + 1)
    assert actions == []


def test_photo_is_refused_and_live_unlock_turns_off_until_hand_unlock():
    p = UnlockPolicy(UnlockSettings(mode="on"), enrolled=True, live=True)
    p.set_pin("1234")
    run(p, [(SEATED, None)], status=protocol.UNLOCKED)
    p.note_lock(5.0)
    actions = run(p, [(SEATED, FAKE)] * 3 + [(SEATED, YOURS)] * 10, status=protocol.LOCK_SCREEN)
    assert [a for _, a in actions] == ["fake_face"]
    assert p.off_until_hand_unlock
    # You unlock by hand: it's back on for the next time.
    run(p, [(SEATED, None)], status=protocol.UNLOCKED, start=20)
    assert not p.off_until_hand_unlock
    p.note_lock(30.0)
    actions = run(p, [(SEATED, YOURS)] * 10, status=protocol.LOCK_SCREEN, start=40)
    assert [a for _, a in actions] == ["unlock"]


def test_one_odd_look_isnt_called_a_photo():
    script = [(SEATED, FAKE)] + [(SEATED, YOURS)] * 5
    assert [a for _, a in run(practice(), script)] == ["would_unlock"]


def live(pin="1234"):
    p = UnlockPolicy(UnlockSettings(mode="on"), enrolled=True, live=True)
    if pin:
        p.set_pin(pin)
    p.note_lock(5.0)
    return p


def test_live_only_at_the_real_lock_screen_and_with_a_pin():
    assert run(live(), [(SEATED, YOURS)] * 10, status=protocol.LOCK_SCREEN) == [(10.8, "unlock")]
    # An admin prompt, or the PC isn't locked, or no helper.
    for status in (protocol.LOCKED, protocol.UNLOCKED, None):
        assert run(live(), [(SEATED, YOURS)] * 10, status=status) == []
    assert run(live(pin=None), [(SEATED, YOURS)] * 10, status=protocol.LOCK_SCREEN) == []
    assert live(pin=None).needs_pin


def test_live_forgets_the_lock_when_the_helper_goes_quiet():
    p = live()
    obs = Observation(10.0, SEATED)
    assert p.step(obs, YOURS, protocol.LOCK_SCREEN, False, helper_silent_for=61) == []
    assert p.locked_at is None


def test_failed_pin_forgets_it():
    p = live()
    p.unlock_failed()
    assert p.pin is None and p.off_until_hand_unlock and p.needs_pin


class FakeKeyboard(PracticeKeyboard):
    def __init__(self, pc):
        super().__init__()
        self.pc = pc

    def type_text(self, text):
        self.would_have_pressed.append(f"typed {len(text)}")
        if self.pc.right_pin == text and text.isdigit():
            self.pc.status = protocol.UNLOCKED

    def enter(self):
        super().enter()
        if self.pc.right_pin is not None and not self.pc.right_pin.isdigit():
            self.pc.status = protocol.UNLOCKED


class FakePC:
    def __init__(self, right_pin="1234"):
        self.status = protocol.LOCK_SCREEN
        self.right_pin = right_pin
        self.now = 0.0

    def sleep(self, s):
        self.now += s

    def clock(self):
        return self.now


def test_typing_a_digits_pin_presses_no_enter():
    pc = FakePC()
    kb = FakeKeyboard(pc)
    assert type_pin(kb, lambda: pc.status, "1234", pc.sleep, pc.clock) == "unlocked"
    assert kb.would_have_pressed == ["Ctrl", "Ctrl", "typed 4"]


def test_typing_a_pin_with_letters_presses_enter():
    pc = FakePC(right_pin="abc12")
    kb = FakeKeyboard(pc)
    assert type_pin(kb, lambda: pc.status, "abc12", pc.sleep, pc.clock) == "unlocked"
    assert kb.would_have_pressed == ["Ctrl", "Ctrl", "typed 5", "Enter"]


def test_wrong_pin_is_typed_once_then_cleared():
    pc = FakePC(right_pin="9999")
    kb = FakeKeyboard(pc)
    assert type_pin(kb, lambda: pc.status, "1234", pc.sleep, pc.clock) == "failed"
    assert kb.would_have_pressed == ["Ctrl", "Ctrl", "typed 4", "Backspace"]


def test_nothing_typed_if_the_lock_screen_goes_away():
    pc = FakePC()
    pc.status = protocol.LOCKED  # say an admin prompt came up
    kb = FakeKeyboard(pc)
    assert type_pin(kb, lambda: pc.status, "1234", pc.sleep, pc.clock) == "not_typed"
    assert kb.would_have_pressed == ["Ctrl", "Ctrl"]


def test_keys_for_letters_and_digits():
    assert key_for("a") == (0, 0x04)
    assert key_for("Z") == (0x02, 0x1D)
    assert key_for("1") == (0, 0x1E)
    assert key_for("0") == (0, 0x27)
    with pytest.raises(ValueError):
        key_for("!")


def test_pin_messages():
    assert protocol.parse_pin(b"PIN 1234\n") == "1234"
    assert protocol.parse_pin("PIN abcD1234") == "abcD1234"
    assert protocol.parse_pin(b"PIN 12\n") is None  # too short
    assert protocol.parse_pin(b"PIN 12 34\n") is None
    assert protocol.parse_pin(b"PIN 12;rm\n") is None
    assert protocol.parse_pin(b"PIN " + b"1" * 40) is None
    assert protocol.parse(b"PIN 1234\n", protocol.FROM_PC) is None


def test_helper_link_hands_over_the_pin_and_lock_screen():
    from desk_presence.helper_link import HelperLink

    got = []
    link = HelperLink("unused", on_pause=lambda: None, clock=lambda: 0.0, on_pin=got.append)
    link.handle(b"PIN 4321\n")
    assert got == ["4321"] and link.status is None
    link.handle(b"LOCK_SCREEN\n")
    assert link.status == protocol.LOCK_SCREEN and link.pc_locked is True


# The whole program, with pretend camera pictures.


class FakeHelper:
    def __init__(self):
        self.status = protocol.UNLOCKED
        self.silent_for = 0.0
        self.sent = []

    @property
    def pc_locked(self):
        return None if self.status is None else self.status != protocol.UNLOCKED

    def send(self, m):
        if m not in protocol.CAMERA.values():  # tested in test_parts
            self.sent.append(m)


def events_in(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def desk(tmp_path, **settings):
    s = config.Settings(event_log=str(tmp_path / "events.jsonl"), **settings)
    fp = Faceprint(np.stack([YOU]), np.array([0.12], dtype=np.float32))
    return s, FakeEngine(), fp


def play(app, start, seconds, people, step=0.1):
    t = start
    while t < start + seconds:
        f = frame(t, people)
        if app.wants_images:
            f.image = IMAGE
        app.handle(f)
        t = round(t + step, 3)
    return t


def test_practice_week_writes_down_would_unlock(tmp_path):
    s, engine, fp = desk(tmp_path)
    kb, helper = PracticeKeyboard(), FakeHelper()
    app = DeskPresence(s, kb, helper, EventLog(s.event_log), face_engine=engine, faceprint=fp)
    t = play(app, 0, 2, [person()])
    t = play(app, t, 8, [])  # away: would lock
    play(app, t, 5, [person()])  # back
    names = [e["event"] for e in events_in(tmp_path / "events.jsonl")]
    assert names.count("would_lock") == 1 and names.count("would_unlock") == 1
    assert helper.sent == [protocol.WOULD_LOCK, protocol.WOULD_UNLOCK]
    assert kb.would_have_pressed == ["Windows+L"]
    # It only looked at pictures after you came back.
    assert 0 < engine.looks < 20


def test_practice_stranger_is_not_recognized(tmp_path):
    s, engine, fp = desk(tmp_path)
    engine.faces = [(engine.faces[0][0], SOMEONE_ELSE, 3.0)]
    helper = FakeHelper()
    app = DeskPresence(s, PracticeKeyboard(), helper, EventLog(s.event_log), face_engine=engine, faceprint=fp)
    t = play(app, 0, 2, [person()])
    t = play(app, t, 8, [])
    play(app, t, 35, [person()])
    names = [e["event"] for e in events_in(tmp_path / "events.jsonl")]
    assert "would_unlock" not in names and names.count("not_recognized") == 1
    assert helper.sent == [protocol.WOULD_LOCK, protocol.NOT_RECOGNIZED]


class LivePC(FakeHelper):
    """Locks when the Pi presses Windows+L and unlocks when it types the right PIN."""

    def __init__(self, pin):
        super().__init__()
        self.pin = pin
        self.pressed = []

    def lock_pc(self):
        self.pressed.append("Windows+L")
        self.status = protocol.LOCK_SCREEN

    def wake(self):
        self.pressed.append("Ctrl")

    def type_text(self, text):
        self.pressed.append("PIN")
        if text == self.pin:
            self.status = protocol.UNLOCKED

    def enter(self):
        self.pressed.append("Enter")

    def clear(self, count):
        self.pressed.append("Backspace")


def test_live_asks_for_the_pin_then_unlocks_you(tmp_path):
    s, engine, fp = desk(tmp_path, practice_mode=False, unlock=config.UnlockSettings(mode="on"))
    pc = LivePC("2468")
    app = DeskPresence(s, pc, pc, EventLog(s.event_log), face_engine=engine, faceprint=fp, sleep=lambda _: None)
    t = play(app, 0, 2, [person()])
    assert pc.sent == [protocol.NEED_PIN]
    app.receive_pin("2468")
    assert pc.sent[-1] == protocol.PIN_OK
    t = play(app, t, 8, [])
    assert pc.status == protocol.LOCK_SCREEN
    play(app, t, 5, [person()])
    assert pc.status == protocol.UNLOCKED
    assert pc.pressed == ["Windows+L", "Ctrl", "Ctrl", "PIN"]
    log = (tmp_path / "events.jsonl").read_text()
    assert "2468" not in log
    names = [e["event"] for e in events_in(tmp_path / "events.jsonl")]
    assert names.count("locked") == 1 and names.count("unlocked") == 1


def test_live_wrong_pin_stops_after_one_try(tmp_path):
    s, engine, fp = desk(tmp_path, practice_mode=False, unlock=config.UnlockSettings(mode="on"))
    pc = LivePC("2468")
    ticks = iter(range(10**6))
    app = DeskPresence(
        s, pc, pc, EventLog(s.event_log), clock=lambda: next(ticks) * 0.25,
        face_engine=engine, faceprint=fp, sleep=lambda _: None,
    )
    t = play(app, 0, 2, [person()])
    app.receive_pin("1111")
    t = play(app, t, 8, [])
    t = play(app, t, 5, [person()])
    t = play(app, t, 8, [])  # leave again while still locked
    play(app, t, 5, [person()])
    assert pc.pressed.count("PIN") == 1
    assert protocol.UNLOCK_FAILED in pc.sent
    assert app.unlock.pin is None
