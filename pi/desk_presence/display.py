"""A read-only, optional SSD1306 status screen. Hardware runs only in its worker.

Snapshots contain numbers and short status strings, never pictures, faceprints or
credentials. Importing this module does not import Pillow or any Pi libraries.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, replace

from .config import DisplaySettings
from .presence import State

log = logging.getLogger(__name__)
REFRESH_SECONDS = 0.25
RETRY_SECONDS = 30.0
STALE_SECONDS = 15.0
RESULT_SECONDS = 3.0


@dataclass(frozen=True)
class Snapshot:
    practice: bool = True
    phase: str = "starting"
    frame_time: float | None = None
    state: State | None = None
    pc_status: str | None = None
    people: int | None = None
    upper_points: int | None = None
    lux: float | None = None
    second_person: bool = False
    pause_until: float | None = None
    lock_seconds: float | None = None
    action: str = ""
    action_until: float = 0.0
    face: str = "FACE UNAVAILABLE"
    face_result: str = ""
    face_until: float = 0.0
    instruction: str = ""
    step_seconds: float | None = None


def observed(frame, obs, tracker, *, practice=True, **status) -> Snapshot:
    """Copy only display-safe measurements from an already interpreted frame."""
    bodies = tracker.bodies(frame)
    points = bodies[0].upper_body_points(tracker.p.min_keypoint_score) if bodies else 0
    return Snapshot(
        practice=practice, phase="watching", frame_time=frame.time, state=obs.state,
        people=len(bodies), upper_points=points, lux=frame.lux,
        second_person=obs.second_person, **status,
    )


def lines(snapshot: Snapshot, now: float) -> tuple[str, ...]:
    """Pure six-row layout: header, large title, then four small diagnostic rows."""
    s = snapshot
    pc = {"UNLOCKED": "OPEN", "LOCKED": "LOCKED", "LOCK_SCREEN": "LOCKED"}.get(s.pc_status, "?")
    header = f"{'PRACTICE' if s.practice else 'LIVE'} PC:{pc}"
    if s.phase == "camera_error":
        return header, "CAM ERROR", "CAMERA FAILED", "CHECK LOG AND CABLE", "", ""
    if s.phase == "starting":
        return header, "STARTING", "LOADING CAMERA", "FIRST BOOT MAY TAKE", "UP TO 150 SECONDS", ""
    if s.phase == "test":
        return "OLED TEST 128X64", "SSD1306", "BORDER ON ALL SIDES", "0123456789 0X3C/0X3D", "TEXT SHOULD BE CLEAR", "180 DEG VIA CONFIG"
    if s.frame_time is None or now - s.frame_time >= STALE_SECONDS:
        return header, "CAMERA WAIT", "NO RECENT RESULTS", "CHECK CAMERA AND LOG", "", ""

    title = {
        State.SEATED: "SEATED", State.STOOD_UP: "STANDING",
        State.AWAY: "NO PERSON", State.TOO_DARK: "TOO DARK",
    }.get(s.state, "CAMERA WAIT")
    lux = "?" if s.lux is None or not math.isfinite(s.lux) else str(min(99999, max(0, round(s.lux))))
    people = "?" if s.people is None else str(min(s.people, 99))
    detail = f"PEOPLE:{people} LUX:{lux}"
    points = f"HEAD/SHOULDERS:{s.upper_points if s.upper_points is not None else '?'}/7"
    paused = s.pause_until is not None and now < s.pause_until
    if paused:
        action = f"PAUSED {math.ceil((s.pause_until - now) / 60)} MIN"
    elif s.state is State.TOO_DARK:
        action = "LOW LIGHT: NO ACTION"
    elif s.second_person:
        action = "SECOND PERSON IN VIEW"
    elif s.instruction:
        action = s.instruction
    elif s.action and now < s.action_until:
        action = s.action
    elif s.lock_seconds is not None:
        action = f"{'WOULD LOCK' if s.practice else 'LOCK'} IN {max(0, s.lock_seconds):.1f}S"
    else:
        action = "WATCHING"
    if s.instruction:
        face = "" if s.step_seconds is None else f"STEP LEFT: {math.ceil(max(0, s.step_seconds))}S"
    elif paused:
        face = "FACE PAUSED"
    elif s.state is State.TOO_DARK:
        face = "FACE WAIT FOR LIGHT"
    elif s.second_person:
        face = "FACE WAIT ONE PERSON"
    else:
        face = s.face_result if s.face_result and now < s.face_until else s.face
    return header, title, detail, points, action, face


# A fixed 5x7 uppercase bitmap font keeps the same legible pixel layout on
# Bookworm, Trixie and desktop previews, without depending on installed fonts.
_FONT = {
    "A": "0E11111F111111", "B": "1E11111E11111E", "C": "0E11101010110E",
    "D": "1E11111111111E", "E": "1F10101E10101F", "F": "1F10101E101010",
    "G": "0E11101711110F", "H": "1111111F111111", "I": "0E04040404040E",
    "J": "0702020212120C", "K": "11121418141211", "L": "1010101010101F",
    "M": "111B1515111111", "N": "11191513111111", "O": "0E11111111110E",
    "P": "1E11111E101010", "Q": "0E11111115120D", "R": "1E11111E141211",
    "S": "0F10100E01011E", "T": "1F040404040404", "U": "1111111111110E",
    "V": "11111111110A04", "W": "11111115151B11", "X": "11110A040A1111",
    "Y": "11110A04040404", "Z": "1F01020408101F",
    "0": "0E11131519110E", "1": "040C040404040E", "2": "0E11010204081F",
    "3": "1E01010601011E", "4": "02060A121F0202", "5": "1F10101E01011E",
    "6": "0E10101E11110E", "7": "1F010204080808", "8": "0E11110E11110E",
    "9": "0E11110F01010E", " ": "00000000000000", ":": "00040400040400",
    "/": "01010204081010", ".": "00000000000C0C", "?": "0E110102040004",
    "-": "0000001F000000", "+": "0004041F040400",
}


def render(snapshot: Snapshot, now: float):
    """Return a new monochrome 128x64 Pillow image; no hardware side effects."""
    from PIL import Image, ImageDraw

    image = Image.new("1", (128, 64))
    draw = ImageDraw.Draw(image)
    for row, text in enumerate(lines(snapshot, now)):
        scale = 2 if row == 1 else 1
        advance = 5 * scale + 1
        text = text.upper()[:128 // advance]
        x0 = (128 - (len(text) * advance - 1)) // 2 if row == 1 else 0
        y0 = 12 if row == 1 else (0 if row == 0 else 32 + (row - 2) * 8)
        if snapshot.phase == "test" and row != 1:
            x0 = 2
            if row == 0:
                y0 = 1
        for col, char in enumerate(text):
            bitmap = bytes.fromhex(_FONT.get(char, _FONT["?"]))
            for y, bits in enumerate(bitmap):
                for x in range(5):
                    if bits & (1 << (4 - x)):
                        px, py = x0 + col * advance + x * scale, y0 + y * scale
                        draw.rectangle((px, py, px + scale - 1, py + scale - 1), fill=1)
    if snapshot.phase == "test":
        draw.rectangle((0, 0, 127, 63), outline=1)
    return image


class SSD1306Backend:
    def __init__(self, settings: DisplaySettings):
        from luma.core.interface.serial import i2c
        from luma.oled.device import ssd1306

        serial = i2c(port=settings.i2c_bus, address=settings.address)
        try:
            self.device = ssd1306(serial, width=128, height=64, rotate=settings.rotate // 90)
        except Exception:
            serial.cleanup()
            raise
        # We own cleanup in the worker; luma's later atexit hook should only
        # release resources, never write to a closed bus from the main thread.
        self.device.persist = True

    def show(self, image) -> None:
        self.device.display(image)

    def close(self, clear=True) -> None:
        # Clear explicitly on normal shutdown. Leave camera errors visible until
        # restart, while still releasing the I2C file handle.
        try:
            if clear:
                self.device.clear()
                self.device.hide()
        finally:
            self.device.cleanup()


class Display:
    """One latest-value mailbox; publish never performs rendering or I2C work."""

    def __init__(self, settings: DisplaySettings, practice=True, *, clock=time.monotonic,
                 backend=SSD1306Backend):
        self.settings = settings
        self.clock = clock
        self.backend_factory = backend
        self._latest = Snapshot(practice=practice)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._device = None
        self._next_tick = float("-inf")
        self._retry_at = float("-inf")
        self._warning_logged = False
        self._last_pixels = None
        self._clear = True

    def publish(self, snapshot: Snapshot) -> None:
        with self._lock:
            self._latest = snapshot

    def camera_error(self) -> None:
        with self._lock:
            self._latest = replace(self._latest, phase="camera_error")

    def start(self):
        if self.settings.enabled and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="oled-display", daemon=True)
            self._thread.start()
        return self

    def _tick(self, now: float) -> None:
        if not self.settings.enabled or now < self._next_tick:
            return
        self._next_tick = now + REFRESH_SECONDS
        if now < self._retry_at:
            return
        try:
            if self._device is None:
                self._device = self.backend_factory(self.settings)
                self._last_pixels = None
            with self._lock:
                snapshot = self._latest
            image = render(snapshot, now)
            pixels = image.tobytes()
            if pixels != self._last_pixels:
                self._device.show(image)
                self._last_pixels = pixels
            if self._warning_logged:
                log.info("OLED display connected again")
                self._warning_logged = False
        except Exception as e:
            if not self._warning_logged:
                log.warning("OLED unavailable (%s); camera continues; retrying every 30 seconds", e)
                self._warning_logged = True
            self._dispose(clear=False)
            self._retry_at = now + RETRY_SECONDS

    def _dispose(self, clear=True) -> None:
        device, self._device = self._device, None
        if device is not None:
            try:
                device.close(clear=clear)
            except Exception:
                pass  # unplugged displays must never break shutdown/retry

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._tick(self.clock())
                self._stop.wait(REFRESH_SECONDS)
        finally:
            if not self._clear and self._device is not None:
                try:
                    with self._lock:
                        snapshot = self._latest
                    self._device.show(render(snapshot, self.clock()))
                except Exception:
                    pass
            self._dispose(clear=self._clear)

    def close(self, clear=True) -> None:
        self._clear = clear
        self._stop.set()
        if self._thread is not None:
            # A wedged I2C driver cannot hold up the main process on shutdown.
            self._thread.join(timeout=1.0)


def oled_test(settings: DisplaySettings, *, sleep=time.sleep) -> int:
    """Explicit foreground test: failure is reported, without touching the camera."""
    device = None
    try:
        device = SSD1306Backend(settings)
        device.show(render(Snapshot(phase="test"), 0))
        print(f"OLED test on I2C {settings.i2c_bus}, address {settings.address:#04x}, rotation {settings.rotate}.")
        print("Check the border and text. Clearing in 10 seconds; Ctrl+C stops early.")
        sleep(10)
        return 0
    except Exception as e:
        print(f"OLED test failed: {e}. Check installation, I2C permissions, wiring and address.")
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if device is not None:
            try:
                device.close()
            except Exception:
                pass
