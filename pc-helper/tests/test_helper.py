import logging
import os
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
    assert desk_helper.parse_from_pi(b"CAMERA AWAY\n") == "CAMERA AWAY"


def test_anything_else_is_ignored():
    assert desk_helper.parse_from_pi(b"cmd.exe /c del\n") is None
    assert desk_helper.parse_from_pi(b"\xff\xfe\n") is None
    assert desk_helper.parse_from_pi(b"PAUSED" * 10) is None


def test_messages_match_the_pi():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pi"))
    from desk_presence import protocol

    assert set(desk_helper.FROM_PI) | set(desk_helper.CAMERA) == protocol.FROM_PI
    assert set(desk_helper.CAMERA) == set(protocol.CAMERA.values())
    assert desk_helper.TO_PI == protocol.FROM_PC


def test_lengths_of_time():
    assert desk_helper.span(8.4) == "8 s"
    assert desk_helper.span(300) == "5 min"
    assert desk_helper.span(7500) == "2 h 5 min"
    assert desk_helper.last_used(None) == ""
    assert desk_helper.last_used(42) == "keyboard and mouse last used 42 s ago"
    assert desk_helper.aside("a", "", "b") == " (a; b)"
    assert desk_helper.aside("") == ""


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


def wait(helper, pc, seconds, camera=None, used=False):
    """Time passing in 10-second steps, as the helper checks the PC and the Pi says what its camera sees."""
    for _ in range(int(seconds // 10)):
        pc.later(10, used=used)
        helper.check_pc()
        if camera:
            helper.on_message(camera)


def test_would_lock_says_what_the_camera_saw(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="desk_helper")
    pc = FakePC(monkeypatch)
    helper = desk_helper.Helper(None, stop=threading.Event(), clock=lambda: pc.now)
    helper.check_pc()
    helper.on_message("CAMERA SEATED")
    wait(helper, pc, 170, "CAMERA SEATED", used=True)
    pc.later(5, used=True)
    helper.check_pc()
    helper.on_message("CAMERA AWAY")  # while you're typing
    pc.later(5, used=True)
    helper.check_pc()
    helper.on_message("WOULD_LOCK")
    assert caplog.messages == [
        "Practice mode: would have locked now (keyboard and mouse last used 0 s ago; "
        "camera in the last minute: sitting 55 s, nobody 5 s, 1 change)"
    ]


def test_long_spells_say_what_the_camera_saw(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="desk_helper")
    pc = FakePC(monkeypatch)
    helper = desk_helper.Helper(None, stop=threading.Event(), clock=lambda: pc.now)
    helper.check_pc()
    helper.on_message("CAMERA SEATED")
    wait(helper, pc, 600, "CAMERA SEATED")  # watching something
    pc.later(1, used=True)
    helper.check_pc()
    helper.on_message("CAMERA AWAY")
    wait(helper, pc, 150, "CAMERA AWAY")  # gone, and no lock, so it hadn't seen you sit down
    helper.on_message("CAMERA TOO_DARK")
    wait(helper, pc, 50, "CAMERA TOO_DARK")
    pc.later(1, used=True)
    helper.check_pc()
    said = caplog.messages
    assert said[0].startswith("Keyboard and mouse used again after 10 min unused (since ")
    assert said[0].endswith("; camera: sitting 100%)")
    assert said[1].endswith("; camera: nobody 75%, too dark 25%, 2 changes)")
    assert len(said) == 2


def test_a_camera_that_goes_quiet_is_written_down_once(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="desk_helper")
    pc = FakePC(monkeypatch)
    helper = desk_helper.Helper(None, stop=threading.Event(), clock=lambda: pc.now)
    helper.check_pc()
    wait(helper, pc, 600, used=True)  # a Pi that doesn't report its camera (not updated yet): nothing to say
    helper.on_message("CAMERA SEATED")
    wait(helper, pc, 300, used=True)
    helper.on_message("CAMERA SEATED")
    said = caplog.messages
    assert len(said) == 2
    assert said[0].startswith("The Pi's camera has said nothing since ")
    assert said[1] == "The Pi's camera is reporting again after 5 min"


def test_sleep_is_not_a_quiet_camera(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="desk_helper")
    pc = FakePC(monkeypatch)
    helper = desk_helper.Helper(None, stop=threading.Event(), clock=lambda: pc.now)
    helper.check_pc()
    helper.on_message("CAMERA SEATED")
    pc.later(8 * 3600, used=True)  # the PC was asleep, and so was the helper
    helper.check_pc()
    wait(helper, pc, 60, "CAMERA SEATED", used=True)
    assert caplog.messages == []


class FakeTimeout(Exception):
    pass


class FakePort:
    def __init__(self, lines=(), refuse=0, each_line=lambda: None, done=lambda: None):
        self.lines, self.refuse, self.each_line, self.done = list(lines), refuse, each_line, done
        self.written = []

    def write(self, data):
        if self.refuse:
            self.refuse -= 1
            raise FakeTimeout("Write timeout")
        self.written.append(data)

    def readline(self, size):
        if not self.lines:
            self.done()
            return b""
        self.each_line()
        return self.lines.pop(0)


def test_a_pi_that_isnt_reading_is_written_down_not_waited_for(caplog):
    caplog.set_level(logging.INFO, logger="desk_helper")
    helper = desk_helper.Helper(None, stop=threading.Event())
    helper.port = FakePort(refuse=2)
    for _ in range(3):
        helper.send_status("UNLOCKED", FakeTimeout)
    assert caplog.messages == [
        "The Pi isn't taking messages: is desk presence running on the Pi?",
        "The Pi is taking messages again",
    ]
    assert helper.port.written == [b"UNLOCKED\n"]


def test_talking_to_the_pi(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="desk_helper")
    pc = FakePC(monkeypatch)
    stop = threading.Event()
    helper = desk_helper.Helper(None, stop=stop, clock=lambda: pc.now)
    lines = [b"CAMERA SEATED\n", b"cmd.exe /c del\n", b"WOULD_LOCK\n"]
    port = FakePort(lines, each_line=lambda: pc.later(5), done=stop.set)
    helper.port = port
    helper.talk(port, FakeTimeout)
    assert port.written == [b"UNLOCKED\n"]
    assert caplog.messages == [
        "Practice mode: would have locked now (keyboard and mouse last used 15 s ago; "
        "camera in the last minute: sitting 10 s, no report 5 s)"
    ]


@windows_only
def test_windows_readings():
    assert isinstance(desk_helper.pc_is_locked(), bool)
    idle = desk_helper.seconds_since_input()
    assert idle is None or idle >= 0


@windows_only
def test_one_copy_runs_in_the_background_until_uninstalled(tmp_path):
    # What double-clicking start-helper.cmd does, with a pretend Startup folder and a signal of
    # its own, so a helper you're running on this PC keeps running.
    shutil.copy(HELPER, tmp_path)
    startup = tmp_path / "Startup"
    link = startup / desk_helper.SHORTCUT_NAME
    env = dict(os.environ, DESK_HELPER_SIGNAL=f"Local\\DeskPresenceHelperTest{os.getpid()}")
    yours = desk_helper.StopSignal()  # the real signal, shared with your helper if it's running

    def call(function):
        code = f"import sys, pathlib, desk_helper; sys.exit(desk_helper.{function}(startup=pathlib.Path(r'{startup}')))"
        done = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
        print(done.stdout, done.stderr)
        return done

    try:
        installed = call("install")
        assert installed.returncode == 0
        # Lost the Pi: your helper has the Pi's port. Connected: the Pi is plugged in and nothing else has it.
        assert any(said in installed.stdout for said in ("Waiting for the Pi", "Lost the Pi", "Connected to the Pi"))
        shortcut = f"(New-Object -ComObject WScript.Shell).CreateShortcut('{link}').TargetPath"
        target = subprocess.run(["powershell.exe", "-NoProfile", "-Command", shortcut], capture_output=True, text=True)
        assert Path(target.stdout.strip()).name.lower() == "pythonw.exe"

        second = subprocess.run([sys.executable, "desk_helper.py"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert second.returncode == 0 and "already running" in second.stdout

        again = call("install")
        assert again.returncode == 0 and "Stopped the copy that was already running" in again.stdout
    finally:
        removed = call("uninstall")
        left_alone = not yours.is_set()
        desk_helper.kernel32.CloseHandle(yours.handle)
    assert left_alone, "the helper running on this PC was asked to stop"
    assert removed.returncode == 0 and "Stopped it." in removed.stdout
    assert not link.exists()
    log = (tmp_path / "helper.log").read_text(encoding="utf-8")
    assert log.count("Helper started") == 2 and log.count("Stopped") == 2


class PinPort:
    def __init__(self):
        self.written = b""

    def write(self, data):
        self.written += data


def test_tells_the_pi_about_the_real_lock_screen(monkeypatch):
    pc = FakePC(monkeypatch)
    screen = {"locked session": False}
    monkeypatch.setattr(desk_helper, "lock_screen_showing", lambda: screen["locked session"])
    helper = desk_helper.Helper(None, stop=threading.Event(), clock=lambda: pc.now)
    assert helper.pc_status() == "UNLOCKED"
    pc.locked = True  # an admin prompt: the screen is taken, the session isn't locked
    assert helper.pc_status() == "LOCKED"
    screen["locked session"] = True
    assert helper.pc_status() == "LOCK_SCREEN"


def test_pin_box_hands_the_pin_to_the_pi_and_logs_no_pin(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="desk_helper")
    pc = FakePC(monkeypatch)
    asked = []

    def ask():
        asked.append(1)
        return "8642"

    helper = desk_helper.Helper(None, stop=threading.Event(), clock=lambda: pc.now, ask_pin=ask)
    helper.port = PinPort()
    helper.check_pc()
    helper.on_message("NEED_PIN")
    for t in threading.enumerate():
        if t.name == "pin-box":
            t.join(5)
    assert asked == [1]
    assert helper.port.written == b"PIN 8642\n"
    helper.on_message("PIN_OK")
    assert "8642" not in caplog.text
    assert caplog.messages == ["Gave your PIN to the Pi", "The Pi has your PIN now (kept in its memory only)"]


def test_pin_box_not_now_waits_and_never_shows_while_locked(monkeypatch):
    pc = FakePC(monkeypatch)
    asked = []

    def ask():
        asked.append(1)
        return None

    helper = desk_helper.Helper(None, stop=threading.Event(), clock=lambda: pc.now, ask_pin=ask)
    helper.port = PinPort()
    pc.locked = True
    helper.check_pc()
    helper.on_message("NEED_PIN")
    assert asked == []
    pc.locked = False
    helper.check_pc()
    helper.offer_pin_box()
    for t in threading.enumerate():
        if t.name == "pin-box":
            t.join(5)
    assert asked == [1] and helper.port.written == b""
    helper.offer_pin_box()
    assert asked == [1]  # "Not now" holds for a while
    pc.later(desk_helper.PIN_SNOOZE + 1)
    helper.offer_pin_box()
    for t in threading.enumerate():
        if t.name == "pin-box":
            t.join(5)
    assert asked == [1, 1]


def test_only_real_pins_are_sent():
    helper = desk_helper.Helper(None, stop=threading.Event())
    helper.port = PinPort()
    for bad in ("12", "12 34", "1234\nPAUSE", "x" * 21, "12;34"):
        helper.send_pin(bad)
    assert helper.port.written == b""


@windows_only
def test_windows_lock_screen_reading():
    assert desk_helper.lock_screen_showing() in (True, False)
