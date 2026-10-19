"""The Pi's end of the data line to the PC helper (a USB serial port on the same cable)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from . import protocol

log = logging.getLogger(__name__)

# The helper sends the lock status every 2 seconds. If it goes quiet for this
# long, the Pi treats the lock status as unknown.
STALE_SECONDS = 10.0


class HelperLink:
    def __init__(self, device: str, on_pause: Callable[[], None], clock=time.monotonic):
        self.device = device
        self.on_pause = on_pause
        self.clock = clock
        self._locked: bool | None = None
        self._heard_at: float | None = None
        self._port = None
        self._lock = threading.Lock()

    @property
    def pc_locked(self) -> bool | None:
        """True or False from the helper, or None if the helper isn't running."""
        with self._lock:
            if self._heard_at is None or self.clock() - self._heard_at > STALE_SECONDS:
                return None
            return self._locked

    def handle(self, line: bytes | str) -> None:
        message = protocol.parse(line, protocol.FROM_PC)
        if message is None:
            return
        if message == "PAUSE":
            self.on_pause()
            return
        with self._lock:
            self._locked = message == "LOCKED"
            self._heard_at = self.clock()

    def send(self, message: str) -> None:
        assert message in protocol.FROM_PI
        port = self._port
        if port is None:
            return
        try:
            port.write((message + "\n").encode("ascii"))
        except OSError as e:
            log.debug("Could not send to PC helper: %s", e)

    def start(self) -> None:
        threading.Thread(target=self._run, name="helper-link", daemon=True).start()

    def _run(self) -> None:
        import serial  # pyserial; only needed on the Pi

        while True:
            try:
                with serial.Serial(self.device, timeout=1) as port:
                    self._port = port
                    log.info("Listening for the PC helper on %s", self.device)
                    while True:
                        line = port.readline(protocol.MAX_LINE + 2)
                        if line:
                            self.handle(line)
            except (OSError, serial.SerialException) as e:
                self._port = None
                log.debug("PC helper link not available (%s); retrying", e)
                time.sleep(5)
