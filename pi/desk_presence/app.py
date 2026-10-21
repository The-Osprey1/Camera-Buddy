"""Wires the camera, the decisions and the keyboard together."""

from __future__ import annotations

import logging
import time
from typing import Iterable

from . import faces, protocol
from .config import Settings
from .events import EventLog
from .helper_link import HelperLink
from .keyboard import PracticeKeyboard, UsbKeyboard
from .pause import PauseSwitch
from .policy import LockPolicy
from .pose import Frame
from .presence import Observation, PresenceTracker, State
from .unlock import UnlockPolicy, type_pin

log = logging.getLogger(__name__)

# At most one look at the picture per this many seconds, so the camera keeps up.
LOOK_EVERY = 0.15
# Ask the PC helper for your PIN at most this often while the Pi doesn't have it.
ASK_PIN_EVERY = 60.0


class DeskPresence:
    def __init__(
        self,
        settings: Settings,
        keyboard,
        helper,
        events: EventLog,
        clock=time.monotonic,
        face_engine=None,
        faceprint: faces.Faceprint | None = None,
        sleep=time.sleep,
    ):
        self.settings = settings
        self.keyboard = keyboard
        self.helper = helper
        self.events = events
        self.clock = clock
        self.sleep = sleep
        self.tracker = PresenceTracker(settings.presence, settings.second_person)
        self.policy = LockPolicy(settings.presence, settings.second_person)
        self.pause = PauseSwitch(settings.pause_seconds)
        self.state: State | None = None
        self.face_engine = face_engine
        self.faceprint = faceprint
        live = settings.unlock.mode == "on" and not settings.practice_mode
        self.unlock = UnlockPolicy(settings.unlock, face_engine is not None and faceprint is not None, live)
        self.last_obs: Observation | None = None
        self.last_look = float("-inf")
        self.last_pin_ask = float("-inf")
        self.face_error_logged = False

    @property
    def wants_images(self) -> bool:
        """Whether the camera should hand over its picture with the next frame."""
        obs = self.last_obs
        return obs is not None and self.unlock.wants_faces(obs, self.helper.status)

    def receive_pin(self, pin: str) -> None:
        """Your PIN, from the PC helper's prompt. Kept in memory only, and never logged."""
        if not self.unlock.needs_pin:
            return
        self.unlock.set_pin(pin)
        self.events.write("pin_received")
        self.helper.send(protocol.PIN_OK)

    def toggle_pause(self) -> None:
        paused = self.pause.toggle(self.clock())
        if paused:
            minutes = round(self.settings.pause_seconds / 60)
            self.events.write("paused", minutes=minutes)
            self.helper.send(protocol.PAUSED)
        else:
            self.events.write("resumed")
            self.helper.send(protocol.RESUMED)

    def handle(self, frame: Frame) -> None:
        obs = self.tracker.update(frame)
        if obs.state is not self.state:
            self.events.write("state", state=obs.state.value)
            log.info("Now: %s", obs.state.value)
            self.state = obs.state

        was_paused = self.pause.until is not None
        paused = self.pause.is_paused(frame.time)
        if was_paused and not paused:
            self.events.write("resumed", reason="pause ran out")
            self.helper.send(protocol.RESUMED)

        pc_locked = self.helper.pc_locked
        for decision in self.policy.step(obs, pc_locked, paused):
            if decision.action == "warn_second_person":
                self.events.write("second_person", reason=decision.reason)
                self.helper.send(protocol.WARN_SECOND_PERSON)
            elif decision.action == "lock":
                if self.settings.practice_mode:
                    self.events.write("would_lock", reason=decision.reason, pc_locked=pc_locked)
                    self.helper.send(protocol.WOULD_LOCK)
                    self.keyboard.lock_pc()
                    self.unlock.note_lock(frame.time)
                else:
                    self.helper.send(protocol.LOCKING)
                    try:
                        self.keyboard.lock_pc()
                    except OSError as e:
                        # Usually the cable isn't plugged into the PC.
                        self.events.write("lock_failed", reason=decision.reason, error=str(e))
                        log.warning("Could not press Windows+L: %s", e)
                    else:
                        self.events.write("locked", reason=decision.reason)
                        self.unlock.note_lock(frame.time)

        self.handle_unlock(frame, obs, paused)
        self.last_obs = obs

    def handle_unlock(self, frame: Frame, obs: Observation, paused: bool) -> None:
        status = self.helper.status
        check = None
        if (
            frame.image is not None
            and frame.time - self.last_look >= LOOK_EVERY
            and self.unlock.wants_faces(obs, status)
        ):
            self.last_look = frame.time
            bodies = self.tracker.bodies(frame)
            if bodies:
                score = self.settings.presence.min_keypoint_score
                try:
                    check, _ = faces.look(self.face_engine, frame.image, bodies[0], score, self.faceprint)
                except Exception as e:  # a face model choking on one picture: skip it
                    if not self.face_error_logged:
                        log.exception("Face check failed: %s", e)
                        self.face_error_logged = True

        for decision in self.unlock.step(obs, check, status, paused, self.helper.silent_for):
            if decision.action == "would_unlock":
                self.events.write("would_unlock", pc_status=status, **decision.details)
                self.helper.send(protocol.WOULD_UNLOCK)
            elif decision.action == "unlock":
                self.type_pin(decision.details)
            elif decision.action == "fake_face":
                self.events.write("fake_face", practice=not self.unlock.live, **decision.details)
                if self.unlock.live:
                    self.helper.send(protocol.FACE_REFUSED)
            elif decision.action == "not_recognized":
                self.events.write("not_recognized", **decision.details)

        if (
            self.unlock.needs_pin
            and status == protocol.UNLOCKED
            and frame.time - self.last_pin_ask >= ASK_PIN_EVERY
        ):
            self.last_pin_ask = frame.time
            self.helper.send(protocol.NEED_PIN)

    def type_pin(self, details: dict) -> None:
        pin = self.unlock.pin
        if pin is None:
            return
        self.helper.send(protocol.UNLOCKING)
        try:
            result = type_pin(self.keyboard, lambda: self.helper.status, pin, self.sleep, self.clock)
        except OSError as e:
            self.events.write("unlock_failed", error=str(e), **details)
            log.warning("Could not type the PIN: %s", e)
            return
        if result == "unlocked":
            self.events.write("unlocked", **details)
        elif result == "not_typed":
            self.events.write("unlock_skipped", reason="the lock screen went away", **details)
        else:
            self.unlock.unlock_failed()
            self.events.write("unlock_failed", reason="still locked after typing the PIN", **details)
            self.helper.send(protocol.UNLOCK_FAILED)

    def run(self, frames: Iterable[Frame]) -> None:
        unlock = self.settings.unlock.mode if self.unlock.active else "off"
        if self.unlock.active and self.settings.unlock.mode == "on" and self.settings.practice_mode:
            unlock = "practice (it types your PIN only once practice_mode is off too)"
        self.events.write("started", practice_mode=self.settings.practice_mode, face_unlock=unlock)
        for frame in frames:
            self.handle(frame)


def load_faces(settings: Settings, events: EventLog):
    """The face models and your enrolled face, or (None, None) if face unlock is off."""
    if settings.unlock.mode == "off":
        return None, None
    faceprint = faces.load_faceprint(settings.unlock.face_file)
    if faceprint is None:
        log.info("Face unlock: no face enrolled yet (run desk-face)")
        return None, None
    try:
        engine = faces.FaceEngine(settings.unlock.models_dir)
    except faces.FaceModelsMissing as e:
        events.write("face_unlock_off", reason=str(e))
        log.warning("Face unlock is off: %s", e)
        return None, None
    return engine, faceprint


def build(settings: Settings) -> DeskPresence:
    keyboard = PracticeKeyboard() if settings.practice_mode else UsbKeyboard(settings.keyboard_device)
    events = EventLog(settings.event_log)
    engine, faceprint = load_faces(settings, events)
    holder: dict = {}
    helper = HelperLink(
        settings.helper_device,
        on_pause=lambda: holder["app"].toggle_pause(),
        on_pin=lambda pin: holder["app"].receive_pin(pin),
    )
    app = DeskPresence(settings, keyboard, helper, events, face_engine=engine, faceprint=faceprint)
    holder["app"] = app
    helper.start()
    if settings.pause_button_pin:
        from gpiozero import Button

        button = Button(settings.pause_button_pin, bounce_time=0.1)
        button.when_pressed = app.toggle_pause
        app._button = button  # keep it alive
    return app
