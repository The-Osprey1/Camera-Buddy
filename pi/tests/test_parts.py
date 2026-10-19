import json

import pytest

from desk_presence import config, protocol
from desk_presence.app import DeskPresence
from desk_presence.events import EventLog, summarize
from desk_presence.helper_link import HelperLink
from desk_presence.keyboard import LEFT_GUI, KEY_L, PracticeKeyboard, UsbKeyboard
from desk_presence.pause import PauseSwitch
from helpers import frame, person


def test_windows_l_report(tmp_path):
    dev = tmp_path / "hidg0"
    dev.write_bytes(b"")
    UsbKeyboard(str(dev)).lock_pc()
    assert dev.read_bytes() == bytes([LEFT_GUI, 0, KEY_L, 0, 0, 0, 0, 0]) + bytes(8)


def test_protocol_ignores_anything_unexpected():
    assert protocol.parse(b"LOCKED\n", protocol.FROM_PC) == "LOCKED"
    assert protocol.parse(b"rm -rf /\n", protocol.FROM_PC) is None
    assert protocol.parse(b"\xff\xfe", protocol.FROM_PC) is None
    assert protocol.parse(b"LOCKED" * 20, protocol.FROM_PC) is None


def test_helper_status_goes_stale():
    now = [0.0]
    link = HelperLink("unused", on_pause=lambda: None, clock=lambda: now[0])
    assert link.pc_locked is None
    link.handle(b"LOCKED\n")
    assert link.pc_locked is True
    link.handle(b"UNLOCKED\n")
    assert link.pc_locked is False
    now[0] = 11
    assert link.pc_locked is None


def test_pause_toggles_and_runs_out():
    p = PauseSwitch(3600)
    assert p.toggle(0) is True
    assert p.is_paused(100)
    assert p.toggle(200) is False
    assert not p.is_paused(201)
    p.toggle(300)
    assert not p.is_paused(3900)


def test_config_loads_and_rejects_typos(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("practice_mode = false\n[presence]\naway_seconds = 8\n")
    s = config.load(f)
    assert s.practice_mode is False and s.presence.away_seconds == 8.0
    f.write_text("[presence]\naway_secs = 8\n")
    with pytest.raises(ValueError):
        config.load(f)


def test_example_config_is_valid():
    from pathlib import Path

    config.load(Path(__file__).resolve().parents[1] / "config.example.toml")


class FakeHelper:
    def __init__(self):
        self.pc_locked = None
        self.sent = []

    def send(self, m):
        self.sent.append(m)


def test_practice_mode_presses_nothing_and_logs(tmp_path):
    settings = config.Settings(event_log=str(tmp_path / "events.jsonl"))
    kb, helper = PracticeKeyboard(), FakeHelper()
    app = DeskPresence(settings, kb, helper, EventLog(settings.event_log), clock=lambda: 0)
    for i in range(4):
        app.handle(frame(i * 0.5, [person()]))
    for i in range(4, 20):
        app.handle(frame(i * 0.5))
    events = [json.loads(l)["event"] for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert events.count("would_lock") == 1 and "locked" not in events
    assert kb.would_have_pressed == ["Windows+L"]
    assert helper.sent == [protocol.WOULD_LOCK]
    assert "would_lock" in summarize(settings.event_log)


def test_pause_from_helper_stops_locking(tmp_path):
    settings = config.Settings(event_log=str(tmp_path / "events.jsonl"))
    kb, helper = PracticeKeyboard(), FakeHelper()
    app = DeskPresence(settings, kb, helper, EventLog(settings.event_log), clock=lambda: 0)
    app.toggle_pause()
    for i in range(4):
        app.handle(frame(i * 0.5, [person()]))
    for i in range(4, 20):
        app.handle(frame(i * 0.5))
    assert kb.would_have_pressed == []
    assert helper.sent == [protocol.PAUSED]
