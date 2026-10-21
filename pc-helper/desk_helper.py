"""Desk presence PC helper for Windows.

Runs quietly on the PC and talks to the Pi over the same USB cable:
- every 2 seconds (and the moment it changes) it tells the Pi whether the PC is locked,
  so the Pi never presses keys when the PC is already locked;
- Ctrl+Alt+P pauses the Pi for an hour, or resumes it if paused;
- it beeps for messages from the Pi (paused, resumed, someone behind you, locking, unlocking);
- for face unlock, it tells the Pi when the real Windows lock screen is showing (as opposed
  to an admin prompt, where the Pi must never type), and when the Pi asks, it pops up a box
  for your Windows PIN and hands it to the Pi. The PIN isn't saved or logged anywhere: the
  Pi keeps it in memory only.

It writes what happens to helper.log next to this file: the Pi's messages, when the PC was
locked and unlocked, and how long before each lock the keyboard and mouse were last used.
So in practice mode you can tell a right "would have locked" (you had been gone a while)
from a wrong one (you were typing a second before).

It only sends and accepts a few fixed messages, so the cable can't be used to run anything.

Needs Python 3.10+ and pyserial. Double-click start-helper.cmd once: it installs pyserial,
adds the helper to your Startup apps (so it runs without a window each time you log in)
and starts it. Double-click it again after an update to restart the helper.
    py desk_helper.py              runs it in this console instead
    py desk_helper.py --uninstall  stops it and takes it out of your Startup apps
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import re
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# The Pi shows up with these USB ids (see pi/setup/usb-gadget.sh).
PI_VENDOR_ID = 0x1D6B
PI_PRODUCT_ID = 0x0104

STATUS_EVERY = 2.0
MAX_LINE = 32
# Keyboard and mouse unused for this long, with the PC unlocked, gets a line in the log
# when you're back, so a time you left and nothing locked shows up too.
LONG_IDLE = 120.0

LOG_NAME = "helper.log"
SHORTCUT_NAME = "Desk presence helper.lnk"
# Marks a running helper, and lets a newer copy ask it to stop.
SIGNAL_NAME = "Local\\DeskPresenceHelper"

TO_PI = {"LOCKED", "LOCK_SCREEN", "UNLOCKED", "PAUSE"}
FROM_PI = {
    "PAUSED": ("Desk presence paused", (880, 120), (660, 120)),
    "RESUMED": ("Desk presence resumed", (660, 120), (880, 120)),
    "WARN SECOND_PERSON": ("Someone is standing behind you", (1200, 150), (1200, 150), (1200, 150)),
    "LOCKING": ("Locking: you walked away", (500, 80)),
    "WOULD_LOCK": ("Practice mode: would have locked now", (500, 60)),
    "UNLOCKING": ("Unlocking: it recognized you", (700, 60), (1000, 60)),
    "WOULD_UNLOCK": ("Practice mode: would have unlocked now (it recognized you)", (700, 50), (1000, 50)),
    "UNLOCK_FAILED": (
        "Face unlock typed your PIN but the PC stayed locked, so the Pi forgot the PIN. "
        "Unlock by hand once, and it asks for your PIN again",
        (400, 200), (300, 300),
    ),
    "FACE_REFUSED": (
        "Face unlock refused a face that looked like a photo or a screen. "
        "It's off until you unlock by hand once",
        (1200, 150), (400, 300),
    ),
    # No beep: you may simply not be looking at the screen yet.
    "NOT_RECOGNIZED": ("Face unlock didn't recognize you after you sat down",),
    "NEED_PIN": ("The Pi asked for your PIN, for face unlock",),
    "PIN_OK": ("The Pi has your PIN now (kept in its memory only)",),
}
PIN_PATTERN = re.compile(r"[A-Za-z0-9]{4,20}")
# "Not now" on the PIN box: don't ask again for this long.
PIN_SNOOZE = 8 * 3600.0
PIN_PROMPT = (
    "Face unlock: so the Pi can unlock this PC when it recognizes you, type your Windows PIN "
    "(the one you type at the lock screen).\n\n"
    "The Pi keeps it in its memory only, never on its card, and forgets it when it's unplugged "
    "or restarts. This PC doesn't keep it either."
)

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


def valid_pin(pin: str) -> bool:
    return bool(PIN_PATTERN.fullmatch(pin))


def span(seconds: float) -> str:
    """A length of time for the log: '8 s', '5 min', '2 h 5 min'."""
    if seconds < 120:
        return f"{seconds:.0f} s"
    minutes = round(seconds / 60)
    if minutes < 120:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def last_used(idle: float | None) -> str:
    """' (keyboard and mouse last used 8 s ago)', or nothing if Windows won't say."""
    return "" if idle is None else f" (keyboard and mouse last used {span(idle)} ago)"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.SwitchDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    DESKTOP_SWITCHDESKTOP = 0x0100

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    kernel32.GetTickCount.restype = wintypes.DWORD
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ERROR_ALREADY_EXISTS = 183
    EVENT_MODIFY_STATE = 0x0002
    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0

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

    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

    class WTSINFOEX_LEVEL1_W(ctypes.Structure):
        _fields_ = [
            ("SessionId", wintypes.ULONG),
            ("SessionState", ctypes.c_int),
            ("SessionFlags", wintypes.LONG),
            ("WinStationName", wintypes.WCHAR * 33),
            ("UserName", wintypes.WCHAR * 21),
            ("DomainName", wintypes.WCHAR * 18),
            ("LogonTime", wintypes.LARGE_INTEGER),
            ("ConnectTime", wintypes.LARGE_INTEGER),
            ("DisconnectTime", wintypes.LARGE_INTEGER),
            ("LastInputTime", wintypes.LARGE_INTEGER),
            ("CurrentTime", wintypes.LARGE_INTEGER),
            ("IncomingBytes", wintypes.DWORD),
            ("OutgoingBytes", wintypes.DWORD),
            ("IncomingFrames", wintypes.DWORD),
            ("OutgoingFrames", wintypes.DWORD),
            ("IncomingCompressedBytes", wintypes.DWORD),
            ("OutgoingCompressedBytes", wintypes.DWORD),
        ]

    class WTSINFOEXW(ctypes.Structure):
        _fields_ = [("Level", wintypes.DWORD), ("Data", WTSINFOEX_LEVEL1_W)]

    wtsapi32.WTSQuerySessionInformationW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
    ]
    wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    WTS_CURRENT_SESSION = 0xFFFFFFFF
    WTS_SESSION_INFO_EX = 25
    WTS_SESSIONSTATE_LOCK = 0

    def lock_screen_showing() -> bool:
        """Whether Windows itself says this session is locked. An admin prompt also takes
        the screen, but leaves the session unlocked, so the Pi never types into one."""
        buffer, size = ctypes.c_void_p(), wintypes.DWORD()
        if not wtsapi32.WTSQuerySessionInformationW(
            None, WTS_CURRENT_SESSION, WTS_SESSION_INFO_EX, ctypes.byref(buffer), ctypes.byref(size)
        ):
            return False
        try:
            if size.value < ctypes.sizeof(WTSINFOEXW):
                return False
            info = ctypes.cast(buffer, ctypes.POINTER(WTSINFOEXW)).contents
            return info.Level == 1 and info.Data.SessionFlags == WTS_SESSIONSTATE_LOCK
        finally:
            wtsapi32.WTSFreeMemory(buffer)

    def seconds_since_input() -> float | None:
        """How long ago the keyboard or mouse was last used, or None if Windows won't say."""
        info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        # Both count milliseconds since Windows started, and wrap around every 49 days.
        return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000

    class StopSignal:
        """Marks this helper as running, and lets a newer copy ask it to stop."""

        def __init__(self):
            ctypes.set_last_error(0)
            self.handle = kernel32.CreateEventW(None, False, False, SIGNAL_NAME)
            self.other_copy_running = bool(self.handle) and ctypes.get_last_error() == ERROR_ALREADY_EXISTS
            self.stopped = False

        def wait(self, seconds: float) -> bool:
            """Waits up to `seconds`; True as soon as a stop has been asked for."""
            if not self.stopped:
                if self.handle:
                    waited = kernel32.WaitForSingleObject(self.handle, int(seconds * 1000))
                    self.stopped = waited == WAIT_OBJECT_0
                else:
                    time.sleep(seconds)
            return self.stopped

        def is_set(self) -> bool:
            return self.wait(0)

    def stop_running_helper(timeout: float = 10.0) -> bool | None:
        """Asks a running helper to stop and waits until it has.

        None: none was running. True: it stopped. False: it didn't stop in time.
        """
        handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, SIGNAL_NAME)
        if not handle:
            return None
        kernel32.SetEvent(handle)
        kernel32.CloseHandle(handle)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.2)
            # The signal goes away once the helper has closed, and let go of the COM port.
            handle = kernel32.OpenEventW(SYNCHRONIZE, False, SIGNAL_NAME)
            if not handle:
                return True
            kernel32.CloseHandle(handle)
        return False

    def beep(*tones: tuple[int, int]) -> None:
        import winsound

        try:
            for freq, ms in tones:
                winsound.Beep(freq, ms)
        except RuntimeError:  # no sound device
            pass

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

    def startup_folder() -> Path:
        """The folder of programs Windows starts when you log in."""
        import winreg

        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return Path(os.path.expandvars(winreg.QueryValueEx(k, "Startup")[0]))
        except OSError:
            return Path(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup")

    def make_shortcut(link: Path, target: Path, arguments: str, folder: Path) -> None:
        # PowerShell makes the shortcut. The paths go in through the environment, so
        # nothing in them needs quoting.
        command = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:DP_LINK); "
            "$s.TargetPath = $env:DP_TARGET; $s.Arguments = $env:DP_ARGUMENTS; "
            "$s.WorkingDirectory = $env:DP_FOLDER; $s.Description = 'Desk presence helper'; $s.Save()"
        )
        env = dict(os.environ, DP_LINK=str(link), DP_TARGET=str(target), DP_ARGUMENTS=arguments, DP_FOLDER=str(folder))
        link.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            env=env, check=True, capture_output=True, timeout=60,
        )
        if not link.exists():
            raise OSError("Windows didn't make the shortcut")

    def start_in_background(program: Path, script: Path) -> None:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        options = dict(
            cwd=script.parent, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            # Outside this window's group of programs, so closing the window leaves it running.
            subprocess.Popen([str(program), str(script)], creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
        except OSError:
            subprocess.Popen([str(program), str(script)], creationflags=flags, **options)

else:  # lets the file be imported for tests on other systems

    def pc_is_locked() -> bool:
        return False

    def lock_screen_showing() -> bool:
        return False

    def seconds_since_input() -> float | None:
        return None

    class StopSignal:
        other_copy_running = False

        def __init__(self):
            self.event = threading.Event()

        def wait(self, seconds: float) -> bool:
            return self.event.wait(seconds)

        def is_set(self) -> bool:
            return self.event.is_set()

    def beep(*tones) -> None:
        pass

    def watch_hotkey(on_press) -> None:
        pass


def ask_for_pin() -> str | None:
    """A small box for your Windows PIN. Returns it, or None for "Not now"."""
    import tkinter as tk

    result: dict[str, str] = {}
    root = tk.Tk()
    root.title("Desk presence")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    tk.Label(root, text=PIN_PROMPT, justify="left", wraplength=380).pack(padx=16, pady=(16, 8))
    entry = tk.Entry(root, show="*", width=24)
    entry.pack(padx=16)
    problem = tk.Label(root, text="", fg="#b00020")
    problem.pack(padx=16)

    def give(_event=None):
        pin = entry.get().strip()
        if not valid_pin(pin):
            problem.config(text="That isn't a PIN: it's 4 to 20 letters and digits.")
            return
        result["pin"] = pin
        root.destroy()

    buttons = tk.Frame(root)
    buttons.pack(padx=16, pady=(8, 16), anchor="e")
    tk.Button(buttons, text="Not now", command=root.destroy).pack(side="right", padx=(8, 0))
    tk.Button(buttons, text="Give it to the Pi", command=give, default="active").pack(side="right")
    root.bind("<Return>", give)
    root.bind("<Escape>", lambda _event: root.destroy())
    root.after(200, entry.focus_force)
    root.mainloop()
    return result.get("pin")


def find_pi_port() -> str | None:
    from serial.tools import list_ports

    for port in list_ports.comports():
        if port.vid == PI_VENDOR_ID and port.pid == PI_PRODUCT_ID:
            return port.device
    return None


class Helper:
    def __init__(self, port_name: str | None, stop: StopSignal | None = None, clock=time.time, ask_pin=ask_for_pin):
        self.port_name = port_name
        self.stop = stop or StopSignal()
        self.clock = clock
        self.port = None
        self.write_lock = threading.Lock()
        self.locked: bool | None = None
        self.locked_at = 0.0
        # When you last used the keyboard or mouse, while they've been unused for LONG_IDLE.
        self.idle_from: float | None = None
        self.ask_pin = ask_pin
        self.pin_box_open = False
        self.pin_snoozed_until = 0.0

    def send(self, message: str) -> None:
        assert message in TO_PI
        with self.write_lock:
            if self.port is not None:
                self.port.write((message + "\n").encode("ascii"))

    def send_pin(self, pin: str) -> None:
        if not valid_pin(pin):
            return
        with self.write_lock:
            if self.port is not None:
                self.port.write(f"PIN {pin}\n".encode("ascii"))

    def offer_pin_box(self) -> None:
        """Shows the PIN box, unless one is open, you said "Not now" lately, or the PC is locked."""
        if self.pin_box_open or self.locked is not False or self.clock() < self.pin_snoozed_until:
            return
        self.pin_box_open = True

        def run():
            try:
                pin = self.ask_pin()
                if pin is None:
                    self.pin_snoozed_until = self.clock() + PIN_SNOOZE
                    log.info("PIN box: not now (it asks again in %s)", span(PIN_SNOOZE))
                    return
                try:
                    self.send_pin(pin)
                except Exception as e:  # cable pulled mid-write
                    log.warning("Could not reach the Pi: %s", e)
                else:
                    log.info("Gave your PIN to the Pi")
            except Exception:
                log.exception("The PIN box didn't work")
                self.pin_snoozed_until = self.clock() + PIN_SNOOZE
            finally:
                self.pin_box_open = False

        threading.Thread(target=run, name="pin-box", daemon=True).start()

    def request_pause(self) -> None:
        log.info("Pause switch pressed")
        try:
            self.send("PAUSE")
        except Exception as e:  # cable pulled mid-write
            log.warning("Could not reach the Pi: %s", e)

    def on_message(self, message: str) -> None:
        text, *tones = FROM_PI[message]
        if message == "NEED_PIN":
            self.offer_pin_box()
            return
        if message in ("WOULD_LOCK", "LOCKING"):
            text += last_used(seconds_since_input())
            if self.locked:
                # The Pi never locks a PC it knows is locked.
                text += ", but the PC was already locked: is the Pi hearing the helper?"
        log.info(text)
        beep(*tones)

    def pc_status(self) -> str:
        """LOCK_SCREEN, LOCKED (an admin prompt, say) or UNLOCKED, for the Pi."""
        if not self.check_pc():
            return "UNLOCKED"
        return "LOCK_SCREEN" if lock_screen_showing() else "LOCKED"

    def check_pc(self) -> bool:
        """Whether the PC is locked. Writes down locks, unlocks and long spells of not using it."""
        locked = pc_is_locked()
        idle = seconds_since_input()
        now = self.clock()
        if locked != self.locked:
            if locked:
                log.info("PC locked%s", last_used(idle))
            elif self.locked:
                log.info("PC unlocked after %s", span(now - self.locked_at))
            self.locked, self.locked_at = locked, now
        if locked or idle is None:
            self.idle_from = None
        elif idle >= LONG_IDLE:
            if self.idle_from is None:
                self.idle_from = now - idle
        elif self.idle_from is not None:
            since = time.strftime("%H:%M:%S", time.localtime(self.idle_from))
            log.info("Keyboard and mouse used again after %s unused (since %s)", span(now - idle - self.idle_from), since)
            self.idle_from = None
        return locked

    def run_connected(self, name: str) -> None:
        import serial

        with serial.Serial(name, timeout=0.2) as port:
            self.port = port
            log.info("Connected to the Pi on %s", name)
            last_sent, last_state = 0.0, None
            try:
                while not self.stop.is_set():
                    status = self.pc_status()
                    now = time.monotonic()
                    if status != last_state or now - last_sent >= STATUS_EVERY:
                        self.send(status)
                        last_sent, last_state = now, status
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
        last_problem = None
        while not self.stop.is_set():
            name = self.port_name or find_pi_port()
            if name is None:
                if not waiting_logged:
                    log.info("Waiting for the Pi to be plugged in...")
                    waiting_logged, last_problem = True, None
                self.stop.wait(3)
                continue
            waiting_logged = False
            try:
                self.run_connected(name)
            except (OSError, serial.SerialException) as e:
                if str(e) != last_problem:  # once, not every 3 seconds
                    log.info("Lost the Pi (%s); will reconnect", e)
                    last_problem = str(e)
                self.stop.wait(3)
            except Exception as e:
                if repr(e) != last_problem:
                    log.exception("Something went wrong; starting over")
                    last_problem = repr(e)
                self.stop.wait(3)
        log.info("Stopped")


def log_file() -> Path:
    """helper.log next to this file, or in %LOCALAPPDATA%\\DeskPresence if this folder is read-only."""
    here = Path(__file__).resolve().with_name(LOG_NAME)
    try:
        with open(here, "a", encoding="utf-8"):
            return here
    except OSError:
        folder = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DeskPresence"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / LOG_NAME


def windowless_python() -> Path:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return pythonw if pythonw.exists() else Path(sys.executable)


def follow(path: Path, start: int, timeout: float) -> str:
    """Shows what a helper that just started writes to the log, until it says how it's doing."""
    outcomes = ("Connected to the Pi", "Waiting for the Pi", "Lost the Pi", "stopped because of an error")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            with open(path, "rb") as f:
                f.seek(start)
                chunk = f.read()
        except OSError:
            continue
        chunk = chunk[: chunk.rfind(b"\n") + 1]  # whole lines only
        start += len(chunk)
        for line in chunk.decode("utf-8", "replace").splitlines():
            print("    " + line)
            for outcome in outcomes:
                if outcome in line:
                    return outcome
    return ""


def install(startup: Path | None = None) -> int:
    """Adds the helper to Startup apps and starts it in the background, in place of a running copy."""
    if sys.platform != "win32":
        print("The helper only runs on Windows.")
        return 1
    script = Path(__file__).resolve()
    program = windowless_python()
    try:
        make_shortcut((startup or startup_folder()) / SHORTCUT_NAME, program, f'"{script}"', script.parent)
        print("Added the helper to your Startup apps, so it starts by itself (without a window) when you log in.")
    except (OSError, subprocess.SubprocessError) as e:
        print(f"Couldn't add the helper to your Startup apps ({e}).")
        print("After you next log in, double-click start-helper.cmd again.")
    stopped = stop_running_helper()
    if stopped is False:
        print("The helper that was already running didn't stop. Restart the PC, and it starts by itself.")
        return 1
    if stopped:
        print("Stopped the copy that was already running.")
    path = log_file()
    start = path.stat().st_size
    start_in_background(program, script)
    print("Started it. It says:")
    said = follow(path, start, timeout=20)
    if said == "Connected to the Pi":
        print("All set: it hears the Pi. You can close this window.")
        print("It beeps when the Pi would have locked the PC, and keeps a log in helper.log in this folder.")
    elif said == "Waiting for the Pi":
        print("It's running, and it connects by itself once the Pi is plugged in. You can close this window.")
    elif said == "Lost the Pi":
        print("It's running but can't talk to the Pi yet (see the line above). It keeps trying.")
    else:
        print("It didn't start properly, so here it is in this window instead, to show why:")
        return 1
    return 0


def uninstall(startup: Path | None = None) -> int:
    """Stops the helper and takes it out of Startup apps."""
    if sys.platform != "win32":
        print("The helper only runs on Windows.")
        return 1
    link = (startup or startup_folder()) / SHORTCUT_NAME
    stopped = stop_running_helper()
    if link.exists():
        link.unlink()
        print("Took the helper out of your Startup apps.")
    if stopped is False:
        print("It didn't stop. Restarting the PC will stop it.")
        return 1
    print("Stopped it." if stopped else "It wasn't running.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Desk presence PC helper")
    parser.add_argument("--port", help="COM port of the Pi, for example COM5 (found automatically if left out)")
    parser.add_argument("--install", action="store_true", help="start it in the background now and at every login")
    parser.add_argument("--uninstall", action="store_true", help="stop it, and stop it starting at login")
    args = parser.parse_args()
    if args.install:
        return install()
    if args.uninstall:
        return uninstall()

    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stdout is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)

    stop = StopSignal()
    if stop.other_copy_running:
        log.info("The helper is already running, so this copy is closing")
        return 0
    log.info("Helper started (Python %s at %s)", platform.python_version(), sys.executable)
    try:
        Helper(args.port, stop).run()
    except KeyboardInterrupt:
        log.info("Stopped")
    except Exception:
        log.exception("The helper stopped because of an error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
