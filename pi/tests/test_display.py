"""OLED behavior without a camera, GPIO, I2C, or real credentials."""

import io
import subprocess
import sys
import threading
import types
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

from desk_presence import check, config, display, protocol
from desk_presence.app import DeskPresence
from desk_presence.display import Display, Snapshot, lines, observed, render
from desk_presence.events import EventLog
from desk_presence.keyboard import PracticeKeyboard
from desk_presence.presence import PresenceTracker, State
from helpers import frame, person
from test_check import FakeCamera, you_do_it_all
from test_unlock import FakeHelper, LivePC, desk, play


class RecordingDisplay:
    def __init__(self):
        self.snapshots = []
        self.failed = False

    def publish(self, snapshot):
        self.snapshots.append(snapshot)

    def camera_error(self):
        self.failed = True


def watching(**changes):
    return replace(Snapshot(phase="watching", frame_time=10, state=State.SEATED,
                            people=1, upper_points=7, lux=100), **changes)


@pytest.mark.parametrize("state,title", [
    (State.SEATED, "SEATED"), (State.STOOD_UP, "STANDING"),
    (State.AWAY, "NO PERSON"), (State.TOO_DARK, "TOO DARK"),
])
def test_detection_labels_and_measurements(state, title):
    rows = lines(watching(state=state, lux=None), 10)
    assert rows[1] == title
    assert rows[2] == "PEOPLE:1 LUX:?"
    assert rows[3] == "HEAD/SHOULDERS:7/7"
    assert rows[0] == "PRACTICE PC:?"
    assert lines(watching(practice=False, pc_status=protocol.UNLOCKED), 10)[0] == "LIVE PC:OPEN"
    assert lines(watching(pc_status=protocol.LOCK_SCREEN), 10)[0].endswith("PC:LOCKED")


def test_priority_expiry_and_stale_results():
    s = watching(action="WOULD LOCK", action_until=13, lock_seconds=4,
                 face_result="WOULD UNLOCK", face_until=13, face="FACE PRACTICE READY")
    assert lines(s, 12)[4:] == ("WOULD LOCK", "WOULD UNLOCK")
    assert lines(s, 13)[4:] == ("WOULD LOCK IN 4.0S", "FACE PRACTICE READY")
    paused = replace(s, pause_until=12)
    assert lines(paused, 11)[4:] == ("PAUSED 1 MIN", "FACE PAUSED")
    assert lines(paused, 12)[4] == "WOULD LOCK"
    second = replace(s, second_person=True)
    assert lines(second, 11)[4:] == ("SECOND PERSON IN VIEW", "FACE WAIT ONE PERSON")
    dark = replace(second, state=State.TOO_DARK)
    assert lines(dark, 11)[4:] == ("LOW LIGHT: NO ACTION", "FACE WAIT FOR LIGHT")
    assert lines(s, 25)[1:] == ("CAMERA WAIT", "NO RECENT RESULTS", "CHECK CAMERA AND LOG", "", "")
    assert lines(Snapshot(), 1000)[1] == "STARTING"  # model upload isn't a stall
    assert lines(Snapshot(phase="camera_error"), 1000)[1] == "CAM ERROR"


def test_snapshot_is_immutable_and_excludes_sensitive_data():
    s = watching()
    with pytest.raises(FrozenInstanceError):
        s.people = 2
    names = {f.name for f in fields(s)}
    assert not names.intersection({"pin", "image", "faceprint", "face_engine", "check"})
    tracker = PresenceTracker(config.PresenceSettings(), config.SecondPersonSettings())
    f = frame(10, [person(), person()])  # duplicate detections count only once
    f.image = object()
    snapshot = observed(f, tracker.update(f), tracker)
    assert snapshot.people == 1 and snapshot.upper_points == 7
    assert all(value is not f.image for value in vars(snapshot).values())


def test_render_all_screens_fit_without_truncation():
    samples = [Snapshot(), Snapshot(phase="camera_error"), Snapshot(phase="test"),
               watching(frame_time=0)]
    samples += [watching(state=state) for state in State]
    samples += [watching(face=text) for text in (
        "FACE UNAVAILABLE", "FACE OFF", "FACE NEED PC HELPER", "FACE NEED PIN",
        "MANUAL UNLOCK NEEDED", "FACE TRY NEXT RETURN", "FACE PRACTICE READY",
        "TEST CHECKING", "FACE MATCH 4/5", "FACE NOT RECOGNIZED", "FACE REFUSED",
    )]
    samples += [watching(second_person=True), watching(pause_until=3610),
                watching(lock_seconds=4.2, practice=False),
                watching(instruction="READY: COME BACK/SIT", step_seconds=5)]
    for s in samples:
        for index, text in enumerate(lines(s, 16)):
            advance = 11 if index == 1 else 6
            assert len(text) * advance - 1 <= 128, text
        image = render(s, 16)
        assert image.size == (128, 64) and image.mode == "1"
        assert image.getbbox() is not None
        # The last small row must fit above y=63, with no pixels cut off.
        if s.phase != "test":
            assert image.crop((0, 63, 128, 64)).getbbox() is None
    test_image = render(Snapshot(phase="test"), 0)
    assert all(test_image.getpixel(p) for p in ((0, 0), (127, 0), (0, 63), (127, 63)))


def test_import_does_not_need_pi_libraries_or_pillow():
    pi = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-S", "-c", "import desk_presence.display, sys; "
         "assert 'PIL' not in sys.modules; assert 'luma' not in sys.modules"],
        cwd=pi, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


class FakeBackend:
    def __init__(self, settings):
        self.shown = []
        self.closed = []
        self.fail = False

    def show(self, image):
        if self.fail:
            raise OSError("disconnected")
        self.shown.append(image)

    def close(self, clear=True):
        self.closed.append(clear)


def test_worker_throttles_and_uses_latest_snapshot():
    device = FakeBackend(None)
    d = Display(config.DisplaySettings(), backend=lambda s: device)
    d._tick(0)
    assert len(device.shown) == 1
    d.publish(watching(state=State.AWAY, frame_time=0))
    d.publish(watching(state=State.STOOD_UP, frame_time=0))
    d._tick(0.1)
    assert len(device.shown) == 1
    d._tick(0.25)
    assert device.shown[-1].tobytes() == render(watching(state=State.STOOD_UP, frame_time=0), 0.25).tobytes()
    d._tick(0.5)
    assert len(device.shown) == 2  # unchanged screens aren't retransmitted
    d._tick(15)
    assert device.shown[-1].tobytes() == render(watching(frame_time=0), 15).tobytes()


@pytest.mark.parametrize("error", [OSError("absent"), ImportError("no luma"), PermissionError("I2C")])
def test_missing_hardware_or_dependency_retries_and_logs_once(error, caplog):
    calls = []
    device = FakeBackend(None)

    def backend(settings):
        calls.append(settings)
        if len(calls) < 3:
            raise error
        return device

    d = Display(config.DisplaySettings(), backend=backend)
    for now in (0, 1, 29, 30, 59):
        d._tick(now)
    assert len(calls) == 2
    assert sum("OLED unavailable" in r.message for r in caplog.records) == 1
    d._tick(60)
    assert len(calls) == 3 and len(device.shown) == 1
    assert not d._warning_logged


def test_unplug_releases_device_and_reconnects():
    first, second = FakeBackend(None), FakeBackend(None)
    devices = iter([first, second])
    d = Display(config.DisplaySettings(), backend=lambda s: next(devices))
    d._tick(0)
    first.fail = True
    d.publish(watching())
    d._tick(0.25)
    assert first.closed == [False] and d._device is None
    d._tick(30)
    assert second.shown == []
    d._tick(30.25)
    assert len(second.shown) == 1


def test_blocked_i2c_never_blocks_publishing_and_cleanup_stays_on_worker():
    entered, release = threading.Event(), threading.Event()
    threads = []

    class BlockingBackend(FakeBackend):
        def __init__(self, settings):
            super().__init__(settings)
            threads.append(threading.current_thread().name)

        def show(self, image):
            threads.append(threading.current_thread().name)
            entered.set()
            assert release.wait(5)
            super().show(image)

        def close(self, clear=True):
            threads.append(threading.current_thread().name)
            super().close(clear)

    d = Display(config.DisplaySettings(), backend=BlockingBackend).start()
    try:
        assert entered.wait(2)
        published = threading.Event()

        def publish():
            d.publish(watching())
            published.set()

        caller = threading.Thread(target=publish)
        caller.start()
        assert published.wait(1), "publish waited for I2C"
        caller.join()
    finally:
        release.set()
        d.close()
    assert threads and set(threads) == {"oled-display"}
    assert not d._thread.is_alive()


def test_disabled_display_never_initializes_hardware():
    def forbidden(settings):
        pytest.fail("disabled OLED opened I2C")

    d = Display(config.DisplaySettings(enabled=False), backend=forbidden).start()
    d.publish(watching())
    d._tick(0)
    d.close()
    assert d._thread is None


def test_shutdown_clears_or_preserves_final_error():
    for clear in (True, False):
        shown = threading.Event()
        device = FakeBackend(None)
        original_show = device.show

        def show(image):
            original_show(image)
            shown.set()

        device.show = show
        d = Display(config.DisplaySettings(), backend=lambda s: device).start()
        assert shown.wait(2)
        d.camera_error()
        d.close(clear=clear)
        assert device.closed == [clear]
        if not clear:
            assert device.shown[-1].tobytes() == render(Snapshot(phase="camera_error"), 0).tobytes()


def test_config_backwards_compatibility_and_validation(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("practice_mode = true\n")
    assert config.load(path).display == config.DisplaySettings()
    path.write_text("[display]\nenabled = false\naddress = 0x3d\nrotate = 180\ni2c_bus = 1\n")
    s = config.load(path).display
    assert not s.enabled and s.address == 0x3D and s.rotate == 180
    for bad in ("address = 0x40", "rotate = 90", "i2c_bus = -1", "enabled = 1", "rotation = 180"):
        path.write_text("[display]\n" + bad)
        with pytest.raises(ValueError):
            config.load(path)


def test_countdown_uses_policy_and_pause_does_not_change_it(tmp_path):
    settings = config.Settings(event_log=str(tmp_path / "events.jsonl"), pause_seconds=2)
    helper, screen = FakeHelper(), RecordingDisplay()
    now = [0]
    app = DeskPresence(settings, PracticeKeyboard(), helper, EventLog(settings.event_log),
                       clock=lambda: now[0], display=screen)
    app.handle(frame(0, [person()]))
    app.handle(frame(0.5, [person()]))
    app.handle(frame(1))
    assert screen.snapshots[-1].lock_seconds == 5
    app.handle(frame(2))
    assert screen.snapshots[-1].lock_seconds == 4
    now[0] = 2
    app.toggle_pause()
    assert lines(screen.snapshots[-1], 2)[4].startswith("PAUSED")
    app.handle(frame(3))
    assert screen.snapshots[-1].lock_seconds is None
    app.handle(frame(4))
    assert screen.snapshots[-1].pause_until is None
    assert screen.snapshots[-1].lock_seconds == 2
    helper.status = protocol.LOCK_SCREEN
    app.handle(frame(4.5))
    assert screen.snapshots[-1].lock_seconds is None
    helper.status = protocol.UNLOCKED
    app.handle(frame(5, lux=0))
    assert screen.snapshots[-1].lock_seconds is None


@pytest.mark.parametrize("broken", [False, True])
def test_display_cannot_change_lock_unlock_actions_or_leak_pin(tmp_path, broken):
    results = []
    screens = []
    for with_display in (False, True):
        folder = tmp_path / str(with_display)
        folder.mkdir()
        s, engine, fp = desk(folder, practice_mode=False, unlock=config.UnlockSettings(mode="on"))
        pc, screen = LivePC("2468"), RecordingDisplay()
        if broken:
            def fail(snapshot):
                raise OSError("broken display")
            screen.publish = fail
        app = DeskPresence(s, pc, pc, EventLog(s.event_log), clock=lambda: 10,
                           face_engine=engine, faceprint=fp, sleep=lambda _: None,
                           display=screen if with_display else None)
        t = play(app, 0, 2, [person()])
        app.receive_pin("2468")
        t = play(app, t, 8, [])
        play(app, t, 5, [person()])
        results.append((pc.pressed, pc.sent))
        screens.append(screen)
    assert results[0] == results[1]
    assert results[1][0] == ["Windows+L", "Ctrl", "Ctrl", "PIN"]
    assert "2468" not in repr(screens[1].snapshots)
    if not broken:
        snapshots = screens[1].snapshots
        assert any(s.face == "UNLOCKING" for s in snapshots)
        assert any(s.face_result == "UNLOCKED" for s in snapshots)
        assert any(s.face.startswith("FACE MATCH ") for s in snapshots)


@pytest.mark.parametrize("kind,result", [
    ("match", "WOULD UNLOCK"), ("stranger", "FACE NOT RECOGNIZED"),
    ("photo", "FACE REFUSED"), ("error", "FACE UNAVAILABLE"),
])
def test_face_feedback_from_real_policy_results(tmp_path, kind, result):
    from fake_faces import SOMEONE_ELSE

    s, engine, fp = desk(tmp_path)
    if kind == "stranger":
        engine.faces = [(engine.faces[0][0], SOMEONE_ELSE, 3)]
    elif kind == "photo":
        engine.faces = [(engine.faces[0][0], engine.faces[0][1], -3)]
    elif kind == "error":
        def fail(image):
            raise RuntimeError("fake model error")
        engine.find = fail
    screen = RecordingDisplay()
    app = DeskPresence(s, PracticeKeyboard(), FakeHelper(), EventLog(s.event_log),
                       face_engine=engine, faceprint=fp, display=screen)
    t = play(app, 0, 2, [person()])
    t = play(app, t, 8, [])
    play(app, t, 35, [person()])
    assert any(result in lines(snap, snap.frame_time)[5] for snap in screen.snapshots)


def test_guided_check_has_steps_countdowns_and_no_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(check, "read_throttled", lambda: 0)
    monkeypatch.setattr(check, "read_usb", lambda settings: ("configured", True))
    cam, screen = FakeCamera(you_do_it_all), RecordingDisplay()
    s = config.Settings(event_log=str(tmp_path / "events.jsonl"))
    assert check.main(s, io.StringIO(), camera=lambda model: cam, clock=cam.clock, display=screen) == 0
    instructions = {snap.instruction for snap in screen.snapshots}
    assert {"SIT AT DESK", "STAND UP", "WALK AWAY", "COME BACK/SIT"} <= instructions
    assert any(i.startswith("READY:") for i in instructions)
    assert all(snap.step_seconds is not None and snap.lock_seconds is None for snap in screen.snapshots)
    assert not Path(s.event_log).exists()


def test_guided_check_camera_failure_reaches_oled(monkeypatch, tmp_path):
    monkeypatch.setattr(check, "read_throttled", lambda: 0)
    monkeypatch.setattr(check, "read_usb", lambda settings: (None, False))
    screen = RecordingDisplay()

    def missing(model):
        raise check.CameraProblem("missing camera")

    s = config.Settings(event_log=str(tmp_path / "events.jsonl"))
    check.main(s, io.StringIO(), camera=missing, display=screen)
    assert screen.failed


def test_oled_test_needs_no_camera_and_reports_device_failure(monkeypatch):
    device = FakeBackend(None)
    monkeypatch.setattr(display, "SSD1306Backend", lambda settings: device)
    assert display.oled_test(config.DisplaySettings(), sleep=lambda _: None) == 0
    assert device.closed == [True] and len(device.shown) == 1

    class DriverError(Exception):
        pass

    def absent(settings):
        raise DriverError("no display")

    monkeypatch.setattr(display, "SSD1306Backend", absent)
    assert display.oled_test(config.DisplaySettings()) == 1


def test_backend_address_rotation_and_resource_cleanup(monkeypatch):
    calls = []

    class Serial:
        def __init__(self, **kwargs):
            calls.append(("i2c", kwargs))

        def cleanup(self):
            calls.append("serial closed")

    class Device:
        def __init__(self, serial, **kwargs):
            calls.append(("device", kwargs))
            self.serial = serial

        def clear(self):
            calls.append("cleared")

        def hide(self):
            calls.append("off")

        def cleanup(self):
            assert self.persist
            self.serial.cleanup()

    for name in ("luma", "luma.core", "luma.core.interface", "luma.core.interface.serial",
                 "luma.oled", "luma.oled.device"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["luma.core.interface.serial"].i2c = Serial
    sys.modules["luma.oled.device"].ssd1306 = Device
    backend = display.SSD1306Backend(config.DisplaySettings(i2c_bus=1, address=0x3D, rotate=180))
    assert calls == [("i2c", {"port": 1, "address": 0x3D}),
                     ("device", {"width": 128, "height": 64, "rotate": 2})]
    backend.close(clear=False)
    assert calls[-1] == "serial closed" and "cleared" not in calls
    backend.close(clear=True)
    assert calls[-3:] == ["cleared", "off", "serial closed"]

    def fail(serial, **kwargs):
        raise OSError("initialization failed")

    sys.modules["luma.oled.device"].ssd1306 = fail
    with pytest.raises(OSError):
        display.SSD1306Backend(config.DisplaySettings())
    assert calls[-1] == "serial closed"


@pytest.mark.parametrize("outcome", ["normal", "camera_error", "sigterm"])
def test_cli_starts_display_before_camera_and_cleans_up(monkeypatch, outcome):
    from desk_presence import __main__ as cli

    calls = []
    screen = RecordingDisplay()

    def start():
        calls.append("display started")
        return screen

    screen.start = start
    screen.close = lambda clear: calls.append(("closed", clear))
    monkeypatch.setattr(display, "Display", lambda *args: screen)
    monkeypatch.setattr(sys, "argv", ["desk_presence", "camera-test"])
    handlers = {}
    monkeypatch.setattr(cli.signal, "signal", lambda sig, fn: handlers.update({sig: fn}))
    camera_module = types.ModuleType("desk_presence.camera")

    class Camera:
        def __init__(self, model):
            assert calls == ["display started"]
            calls.append("camera started")
            if outcome == "camera_error":
                raise RuntimeError("camera disconnected")
            if outcome == "sigterm":
                handlers[cli.signal.SIGTERM](None, None)

        def frames(self):
            yield frame(1, [person()])

    camera_module.PoseCamera = Camera
    monkeypatch.setitem(sys.modules, "desk_presence.camera", camera_module)
    if outcome == "camera_error":
        with pytest.raises(RuntimeError, match="camera disconnected"):
            cli.main()
        assert screen.failed
    elif outcome == "sigterm":
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
    else:
        cli.main()
        assert screen.snapshots[-1].state is State.SEATED
        assert screen.snapshots[-1].instruction == "CAMERA TEST: NO KEYS"
    assert calls[-1] == ("closed", outcome != "camera_error")


def test_oled_test_cli_bypasses_camera_and_disabled_setting(monkeypatch):
    from desk_presence import __main__ as cli

    monkeypatch.setattr(sys, "argv", ["desk_presence", "oled-test"])
    monkeypatch.setattr(config, "load", lambda path: config.Settings(display=config.DisplaySettings(enabled=False)))

    def forbidden(*args):
        pytest.fail("OLED test attempted to start the camera/service worker")

    monkeypatch.setattr(cli, "run_camera", forbidden)
    monkeypatch.setattr(display, "Display", forbidden)
    device = FakeBackend(None)
    monkeypatch.setattr(display, "SSD1306Backend", lambda settings: device)
    original = display.oled_test
    monkeypatch.setattr(display, "oled_test", lambda settings: original(settings, sleep=lambda _: None))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert device.closed == [True]
