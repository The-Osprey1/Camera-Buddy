import logging
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import desk_helper  # noqa: E402

HELPER = Path(__file__).resolve().parents[1] / "desk_helper.py"
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")


def test_known_messages_from_pi():
    assert desk_helper.parse_from_pi(b"WARN SECOND_PERSON\n") == "WARN SECOND_PERSON"
    assert desk_helper.parse_from_pi(b"PAUSED\r\n") == "PAUSED"


def test_anything_else_is_ignored():
    assert desk_helper.parse_from_pi(b"cmd.exe /c del\n") is None
    assert desk_helper.parse_from_pi(b"\xff\xfe\n") is None
    assert desk_helper.parse_from_pi(b"PAUSED" * 10) is None


def test_messages_match_the_pi():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pi"))
    from desk_presence import protocol

    assert set(desk_helper.FROM_PI) == protocol.FROM_PI
    assert desk_helper.TO_PI == protocol.FROM_PC


def test_lengths_of_time():
    assert desk_helper.span(8.4) == "8 s"
    assert desk_helper.span(300) == "5 min"
    assert desk_helper.span(7500) == "2 h 5 min"
    assert desk_helper.last_used(None) == ""
    assert desk_helper.last_used(42) == " (keyboard and mouse last used 42 s ago)"


class FakePC:
    """The PC as the helper sees it: locked or not, and how long since a key or the mouse was used."""

    def __init__(self, monkeypatch):
        self.now = 1_000_000.0
        self.locked = False
        self.idle = 0.0
        monkeypatch.setattr(desk_helper, "pc_is_locked", lambda: self.locked)
        monkeypatch.setattr(desk_helper, "seconds_since_input", lambda: self.idle)

    def later(self, seconds, used=False):
        self.now += seconds
        self.idle = 0.0 if used else self.idle + seconds


def test_log_shows_what_you_were_doing(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="desk_helper")
    pc = FakePC(monkeypatch)
    helper = desk_helper.Helper(None, stop=threading.Event(), clock=lambda: pc.now)
    helper.check_pc()
    pc.later(8)
    helper.on_message("WOULD_LOCK")
    pc.later(300)
    helper.check_pc()
    pc.later(600, used=True)
    helper.check_pc()
    pc.locked = True
    pc.later(3)
    helper.check_pc()
    helper.on_message("WOULD_LOCK")
    pc.locked = False
    pc.later(720, used=True)
    helper.check_pc()

    said = caplog.messages
    assert said[0] == "Practice mode: would have locked now (keyboard and mouse last used 8 s ago)"
    assert said[1].startswith("Keyboard and mouse used again after 15 min unused (since ")
    assert said[2] == "PC locked (keyboard and mouse last used 3 s ago)"
    assert said[3].endswith("but the PC was already locked: is the Pi hearing the helper?")
    assert said[4] == "PC unlocked after 12 min"
    assert len(said) == 5


def test_short_breaks_and_locked_time_are_not_idle_spells(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="desk_helper")
    pc = FakePC(monkeypatch)
    helper = desk_helper.Helper(None, stop=threading.Event(), clock=lambda: pc.now)
    helper.check_pc()
    pc.later(60)
    helper.check_pc()
    pc.later(1, used=True)
    helper.check_pc()
    pc.locked = True
    pc.later(1)
    helper.check_pc()
    pc.later(3600)
    helper.check_pc()
    pc.locked = False
    pc.later(1, used=True)
    helper.check_pc()
    assert caplog.messages == ["PC locked (keyboard and mouse last used 1 s ago)", "PC unlocked after 60 min"]


@windows_only
def test_windows_readings():
    assert isinstance(desk_helper.pc_is_locked(), bool)
    idle = desk_helper.seconds_since_input()
    assert idle is None or idle >= 0


@windows_only
def test_one_copy_runs_in_the_background_until_uninstalled(tmp_path):
    # What double-clicking start-helper.cmd does, with a pretend Startup folder.
    shutil.copy(HELPER, tmp_path)
    startup = tmp_path / "Startup"
    link = startup / desk_helper.SHORTCUT_NAME

    def call(function):
        code = f"import sys, pathlib, desk_helper; sys.exit(desk_helper.{function}(startup=pathlib.Path(r'{startup}')))"
        done = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=90)
        print(done.stdout, done.stderr)
        return done

    try:
        installed = call("install")
        assert installed.returncode == 0
        assert "Waiting for the Pi" in installed.stdout
        shortcut = f"(New-Object -ComObject WScript.Shell).CreateShortcut('{link}').TargetPath"
        target = subprocess.run(["powershell.exe", "-NoProfile", "-Command", shortcut], capture_output=True, text=True)
        assert Path(target.stdout.strip()).name.lower() == "pythonw.exe"

        second = subprocess.run([sys.executable, "desk_helper.py"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert second.returncode == 0 and "already running" in second.stdout

        again = call("install")
        assert again.returncode == 0 and "Stopped the copy that was already running" in again.stdout
    finally:
        removed = call("uninstall")
    assert removed.returncode == 0 and "Stopped it." in removed.stdout
    assert not link.exists()
    log = (tmp_path / "helper.log").read_text(encoding="utf-8")
    assert log.count("Helper started") == 2 and log.count("Stopped") == 2
