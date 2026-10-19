"""Wires the camera, the decisions and the keyboard together."""

from __future__ import annotations

import logging
import time
from typing import Iterable

from . import protocol
from .config import Settings
from .events import EventLog
from .helper_link import HelperLink
from .keyboard import PracticeKeyboard, UsbKeyboard
from .pause import PauseSwitch
from .policy import LockPolicy
from .pose import Frame
from .presence import PresenceTracker, State

log = logging.getLogger(__name__)


class DeskPresence:
    def __init__(self, settings: Settings, keyboard, helper, events: EventLog, clock=time.monotonic):
        self.settings = settings
        self.keyboard = keyboard
        self.helper = helper
        self.events = events
        self.clock = clock
        self.tracker = PresenceTracker(settings.presence, settings.second_person)
        self.policy = LockPolicy(settings.presence, settings.second_person)
        self.pause = PauseSwitch(settings.pause_seconds)
        self.state: State | None = None

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

    def run(self, frames: Iterable[Frame]) -> None:
        self.events.write("started", practice_mode=self.settings.practice_mode)
        for frame in frames:
            self.handle(frame)


def build(settings: Settings) -> DeskPresence:
    keyboard = PracticeKeyboard() if settings.practice_mode else UsbKeyboard(settings.keyboard_device)
    events = EventLog(settings.event_log)
    holder: dict = {}
    helper = HelperLink(settings.helper_device, on_pause=lambda: holder["app"].toggle_pause())
    app = DeskPresence(settings, keyboard, helper, events)
    holder["app"] = app
    helper.start()
    if settings.pause_button_pin:
        from gpiozero import Button

        button = Button(settings.pause_button_pin, bounce_time=0.1)
        button.when_pressed = app.toggle_pause
        app._button = button  # keep it alive
    return app
