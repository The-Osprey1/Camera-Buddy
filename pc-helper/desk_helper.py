"""Desk presence PC helper for Windows.

Runs quietly on the PC and talks to the Pi over the same USB cable:
- every 2 seconds (and the moment it changes) it tells the Pi whether the PC is locked,
  so the Pi never presses keys when the PC is already locked;
- Ctrl+Alt+P pauses the Pi for an hour, or resumes it if paused;
- it beeps for messages from the Pi (paused, resumed, someone behind you, locking)
  and writes them to %LOCALAPPDATA%\\DeskPresence\\helper.log.

It only sends and accepts a few fixed messages, so the cable can't be used to run anything.

Needs Python 3.10+ and pyserial:  py -m pip install pyserial
Run without a console window:      pyw desk_helper.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

# The Pi shows up with these USB ids (see pi/setup/usb-gadget.sh).
PI_VENDOR_ID = 0x1D6B
PI_PRODUCT_ID = 0x0104

STATUS_EVERY = 2.0
MAX_LINE = 32

TO_PI = {"LOCKED", "UNLOCKED", "PAUSE"}
FROM_PI = {
    "PAUSED": ("Desk presence paused", (880, 120), (660, 120)),
    "RESUMED": ("Desk presence resumed", (660, 120), (880, 120)),
    "WARN SECOND_PERSON": ("Someone is standing behind you", (1200, 150), (1200, 150), (1200, 150)),
    "LOCKING": ("Locking: you walked away", (500, 80)),
    "WOULD_LOCK": ("Practice mode: would have locked now", (500, 60)),
}

log = logging.getLogger("desk_helper")


def parse_from_pi(line: bytes) -> str | None:
    """Return the message if it's one we know, otherwise None."""
    if len(line) > MAX_LINE + 2:
        return None
    try:
        message = line.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    return message if message in FROM_PI else None


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.SwitchDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    DESKTOP_SWITCHDESKTOP = 0x0100

    def pc_is_locked() -> bool:
        # While the lock screen is up, this program can't switch to the input desktop.
        # Note: an admin ("allow changes?") prompt looks the same, which is fine for
        # locking (the Pi just won't press anything) but matters for unlock later.
        desktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not desktop:
            return True
        try:
            return not user32.SwitchDesktop(desktop)
        finally:
            user32.CloseDesktop(desktop)

    def beep(*tones: tuple[int, int]) -> None:
        import winsound

        for freq, ms in tones:
            winsound.Beep(freq, ms)

    def watch_hotkey(on_press) -> None:
        """Ctrl+Alt+P anywhere in Windows calls on_press."""
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
        WM_HOTKEY = 0x0312

        def loop():
            if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("P")):
                log.warning("Ctrl+Alt+P is already used by another program; pause hotkey is off")
                return
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    on_press()

        threading.Thread(target=loop, name="hotkey", daemon=True).start()

else:  # lets the file be imported for tests on other systems

    def pc_is_locked() -> bool:
        return False

    def beep(*tones) -> None:
        pass

    def watch_hotkey(on_press) -> None:
        pass


def find_pi_port() -> str | None:
    from serial.tools import list_ports

    for port in list_ports.comports():
        if port.vid == PI_VENDOR_ID and port.pid == PI_PRODUCT_ID:
            return port.device
    return None


class Helper:
    def __init__(self, port_name: str | None):
        self.port_name = port_name
        self.port = None
        self.write_lock = threading.Lock()

    def send(self, message: str) -> None:
        assert message in TO_PI
        with self.write_lock:
            if self.port is not None:
                self.port.write((message + "\n").encode("ascii"))

    def request_pause(self) -> None:
        log.info("Pause switch pressed")
        try:
            self.send("PAUSE")
        except Exception as e:  # cable pulled mid-write
            log.warning("Could not reach the Pi: %s", e)

    def on_message(self, message: str) -> None:
        text, *tones = FROM_PI[message]
        log.info(text)
        beep(*tones)

    def run_connected(self, name: str) -> None:
        import serial

        with serial.Serial(name, timeout=0.2) as port:
            self.port = port
            log.info("Connected to the Pi on %s", name)
            last_sent, last_state = 0.0, None
            try:
                while True:
                    locked = pc_is_locked()
                    now = time.monotonic()
                    if locked != last_state or now - last_sent >= STATUS_EVERY:
                        self.send("LOCKED" if locked else "UNLOCKED")
                        last_sent, last_state = now, locked
                    line = port.readline(MAX_LINE + 2)
                    if line:
                        message = parse_from_pi(line)
                        if message:
                            self.on_message(message)
            finally:
                self.port = None

    def run(self) -> None:
        import serial

        watch_hotkey(self.request_pause)
        waiting_logged = False
        while True:
            name = self.port_name or find_pi_port()
            if name is None:
                if not waiting_logged:
                    log.info("Waiting for the Pi to be plugged in...")
                    waiting_logged = True
                time.sleep(3)
                continue
            waiting_logged = False
            try:
                self.run_connected(name)
            except (OSError, serial.SerialException) as e:
                log.info("Lost the Pi (%s); will reconnect", e)
                time.sleep(3)


def main() -> None:
    parser = argparse.ArgumentParser(description="Desk presence PC helper")
    parser.add_argument("--port", help="COM port of the Pi, for example COM5 (found automatically if left out)")
    args = parser.parse_args()

    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DeskPresence"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(log_dir / "helper.log", encoding="utf-8")]
    if sys.stdout is not None:  # pyw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=handlers)

    Helper(args.port).run()


if __name__ == "__main__":
    main()
