"""Wires the camera, the decisions and the keyboard together."""

from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import replace
from typing import Iterable

from . import faces, protocol
from .config import Settings
from .display import RESULT_SECONDS, Snapshot, observed
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
# What the camera sees goes to the PC helper's log when it changes, and again this
# often so the helper can tell the Pi is still watching.
CAMERA_EVERY = 60.0


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
        async_faces: bool = False,
        display=None,
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
        self.camera_sent: State | None = None
        self.camera_sent_at = float("-inf")
        self.face_engine = face_engine
        self.faceprint = faceprint
        live = settings.unlock.mode == "on" and not settings.practice_mode
        self.unlock = UnlockPolicy(settings.unlock, face_engine is not None and faceprint is not None, live)
        self.last_obs: Observation | None = None
        self.last_look = float("-inf")
        self.last_pin_ask = float("-inf")
        self.face_error_logged = False
        self.async_faces = async_faces
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1) if async_faces else None
        self._pending_future: concurrent.futures.Future | None = None
        self.display = display
        self._display_snapshot: Snapshot | None = None
        self._display_action = ("", 0.0)
        self._face_result = ("", 0.0)
        self._face_hint = ("", 0.0)

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
        if self._display_snapshot is not None:
            self._publish_display(replace(self._display_snapshot, pause_until=self.pause.until))

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
        self.report_camera(obs, pc_locked)
        for decision in self.policy.step(obs, pc_locked, paused):
            if decision.action == "warn_second_person":
                self.events.write("second_person", reason=decision.reason)
                self.helper.send(protocol.WARN_SECOND_PERSON)
            elif decision.action == "lock":
                if self.settings.practice_mode:
                    self._display_action = ("WOULD LOCK", frame.time + RESULT_SECONDS)
                    self.events.write("would_lock", reason=decision.reason, pc_locked=pc_locked)
                    self.helper.send(protocol.WOULD_LOCK)
                    self.keyboard.lock_pc()
                    self.unlock.note_lock(frame.time)
                else:
                    self.helper.send(protocol.LOCKING)
                    try:
                        self.keyboard.lock_pc()
                    except OSError as e:
                        self._display_action = ("LOCK FAILED", frame.time + RESULT_SECONDS)
                        # Usually the cable isn't plugged into the PC.
                        self.events.write("lock_failed", reason=decision.reason, error=str(e))
                        log.warning("Could not press Windows+L: %s", e)
                    else:
                        self._display_action = ("LOCK SENT", frame.time + RESULT_SECONDS)
                        self.events.write("locked", reason=decision.reason)
                        self.unlock.note_lock(frame.time)

        # Publish before a PIN attempt, which can take several seconds. There is
        # no camera image or credential in this snapshot.
        self.report_display(frame, obs, paused)
        self.handle_unlock(frame, obs, paused)
        self.last_obs = obs
        self.report_display(frame, obs, paused)

    def handle_unlock(self, frame: Frame, obs: Observation, paused: bool) -> None:
        status = self.helper.status
        check = None
        if self._executor is not None:
            if self._pending_future is not None and self._pending_future.done():
                try:
                    check, _ = self._pending_future.result()
                except Exception as e:
                    self._face_hint = ("FACE UNAVAILABLE", frame.time + RESULT_SECONDS)
                    if not self.face_error_logged:
                        log.exception("Face check failed: %s", e)
                        self.face_error_logged = True
                self._pending_future = None

            if (
                self._pending_future is None
                and frame.image is not None
                and frame.time - self.last_look >= LOOK_EVERY
                and self.unlock.wants_faces(obs, status)
            ):
                self.last_look = frame.time
                bodies = self.tracker.bodies(frame)
                if bodies:
                    score = self.settings.presence.min_keypoint_score
                    img = frame.image.copy()
                    self._pending_future = self._executor.submit(
                        faces.look, self.face_engine, img, bodies[0], score, self.faceprint
                    )
        else:
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
                        self._face_hint = ("FACE UNAVAILABLE", frame.time + RESULT_SECONDS)
                        if not self.face_error_logged:
                            log.exception("Face check failed: %s", e)
                            self.face_error_logged = True

        if check is not None:
            if not check.found:
                hint = "FACE NOT FOUND"
            elif check.too_dark:
                hint = "FACE TOO DARK"
            elif not check.fits_body:
                hint = "FACE OFF BODY"
            elif not check.usable:
                hint = "FACE UNAVAILABLE"
            elif check.similarity < self.settings.unlock.match_threshold:
                hint = "FACE NO MATCH"
            elif check.real_score is not None and check.real_score < self.settings.unlock.real_face_threshold:
                hint = "FACE CHECK FAILED"
            else:
                hint = ""  # the policy's current match streak supplies progress
            self._face_hint = (hint, frame.time + RESULT_SECONDS)

        for decision in self.unlock.step(obs, check, status, paused, self.helper.silent_for):
            if decision.action == "would_unlock":
                self._face_result = ("WOULD UNLOCK", frame.time + RESULT_SECONDS)
                self.events.write("would_unlock", pc_status=status, **decision.details)
                self.helper.send(protocol.WOULD_UNLOCK)
            elif decision.action == "unlock":
                self.type_pin(decision.details)
            elif decision.action == "fake_face":
                self._face_result = ("FACE REFUSED", frame.time + RESULT_SECONDS)
                self.events.write("fake_face", practice=not self.unlock.live, **decision.details)
                if self.unlock.live:
                    self.helper.send(protocol.FACE_REFUSED)
            elif decision.action == "not_recognized":
                self._face_result = ("FACE NOT RECOGNIZED", frame.time + RESULT_SECONDS)
                self.events.write("not_recognized", **decision.details)
                self.helper.send(protocol.NOT_RECOGNIZED)

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
        if self._display_snapshot is not None:
            self._publish_display(replace(
                self._display_snapshot, face="UNLOCKING", action="UNLOCKING",
                action_until=self.clock() + 12.0, face_result="", face_until=0.0,
            ))
        try:
            result = type_pin(self.keyboard, lambda: self.helper.status, pin, self.sleep, self.clock)
        except OSError as e:
            if self.display is not None:
                self._face_result = ("UNLOCK FAILED", self.clock() + RESULT_SECONDS)
            self.events.write("unlock_failed", error=str(e), **details)
            log.warning("Could not type the PIN: %s", e)
            return
        if result == "unlocked":
            if self.display is not None:
                self._face_result = ("UNLOCKED", self.clock() + RESULT_SECONDS)
            self.events.write("unlocked", **details)
        elif result == "not_typed":
            if self.display is not None:
                self._face_result = ("UNLOCK SKIPPED", self.clock() + RESULT_SECONDS)
            self.events.write("unlock_skipped", reason="the lock screen went away", **details)
        else:
            if self.display is not None:
                self._face_result = ("MANUAL UNLOCK NEEDED", self.clock() + RESULT_SECONDS)
            self.unlock.unlock_failed()
            self.events.write("unlock_failed", reason="still locked after typing the PIN", **details)
            self.helper.send(protocol.UNLOCK_FAILED)

    def _publish_display(self, snapshot: Snapshot) -> None:
        self._display_snapshot = snapshot
        if self.display is not None:
            try:
                self.display.publish(snapshot)
            except Exception:
                # Even a broken display adapter must not interfere with policy.
                log.exception("OLED status publishing failed; disabling the display")
                self.display = None

    def face_status(self, obs: Observation, paused: bool) -> str:
        """Read policy progress without changing its decisions or retaining images."""
        if self.settings.unlock.mode == "off":
            return "FACE OFF"
        if not self.unlock.active:
            return "FACE UNAVAILABLE"
        if self.unlock.off_until_hand_unlock:
            return "MANUAL UNLOCK NEEDED"
        if paused:
            return "FACE PAUSED"
        if self.unlock.live and self.helper.status is None:
            return "FACE NEED PC HELPER"
        if self.unlock.needs_pin:
            return "FACE NEED PIN"
        if self.unlock.given_up:
            return "FACE TRY NEXT RETURN"
        if self.unlock.wants_faces(obs, self.helper.status):
            hint, until = self._face_hint
            if hint and obs.time < until:
                return hint
            prefix = "FACE" if self.unlock.live else "TEST"
            count = min(len(self.unlock.streak), self.settings.unlock.match_frames)
            if count:
                return f"{prefix} MATCH {count}/{self.settings.unlock.match_frames}"
            return f"{prefix} CHECKING"
        return "FACE READY" if self.unlock.live else "FACE PRACTICE READY"

    def report_display(self, frame: Frame, obs: Observation, paused: bool) -> None:
        if self.display is None:
            return
        remaining = None
        if (obs.state is State.AWAY and self.policy.armed
                and self.policy.absent_since is not None and not paused
                and self.helper.pc_locked is not True):
            remaining = max(0.0, self.settings.presence.away_seconds - (obs.time - self.policy.absent_since))
        action, until = self._display_action
        face_result, face_until = self._face_result
        self._publish_display(observed(
            frame, obs, self.tracker, practice=self.settings.practice_mode,
            pc_status=self.helper.status, pause_until=self.pause.until,
            lock_seconds=remaining, action=action, action_until=until,
            face=self.face_status(obs, paused),
            face_result=face_result, face_until=face_until,
        ))

    def report_camera(self, obs: Observation, pc_locked: bool | None) -> None:
        if pc_locked is None:
            # Nobody to tell: the helper isn't running, or the PC is asleep. Say it again once it's back.
            self.camera_sent = None
            return
        if obs.state is not self.camera_sent or obs.time - self.camera_sent_at >= CAMERA_EVERY:
            self.helper.send(protocol.CAMERA[obs.state.value])
            self.camera_sent, self.camera_sent_at = obs.state, obs.time

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


def build(settings: Settings, display=None) -> DeskPresence:
    keyboard = PracticeKeyboard() if settings.practice_mode else UsbKeyboard(settings.keyboard_device)
    events = EventLog(settings.event_log)
    engine, faceprint = load_faces(settings, events)
    holder: dict = {}
    helper = HelperLink(
        settings.helper_device,
        on_pause=lambda: holder["app"].toggle_pause(),
        on_pin=lambda pin: holder["app"].receive_pin(pin),
    )
    app = DeskPresence(
        settings, keyboard, helper, events, face_engine=engine, faceprint=faceprint, async_faces=True,
        display=display,
    )
    holder["app"] = app
    helper.start()
    if settings.pause_button_pin:
        from gpiozero import Button

        button = Button(settings.pause_button_pin, bounce_time=0.1)
        button.when_pressed = app.toggle_pause
        app._button = button  # keep it alive
    return app
