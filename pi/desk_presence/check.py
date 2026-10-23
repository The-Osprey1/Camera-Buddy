"""A guided check of the whole setup, run by `desk-check`: the power, keyboard mode,
and how the camera sees you sit, stand up, walk away and come back.

It presses nothing. It says on screen what to do and beeps when it's time for the
next step, so you can follow along from across the room. At the end it gives OK or
PROBLEM for each part, with what to change, and saves the results in
~/desk-presence/checks/.
"""

from __future__ import annotations

import math
import os
import queue
import statistics
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, TextIO

from .config import Settings
from .policy import LockPolicy
from .pose import Frame
from .presence import Observation, PresenceTracker, State

OK, WARNING, PROBLEM, NOT_YET = "OK", "WARNING", "PROBLEM", "NOT YET"

# The first start after switching on uploads the pose model to the camera.
CAMERA_START_SECONDS = 150.0
# No results for this long means the camera has stopped.
CAMERA_STALL_SECONDS = 10.0

# Many PC ports can't power a Pi 4 with the camera, and a PC that cuts power to its
# USB ports can take your mouse and keyboard with it.
OWN_SUPPLY = (
    "Power the Pi from its own supply (5 V, 3 A), through a USB-C power and data splitter "
    "if it's plugged into the PC."
)

RESEAT_CABLE = (
    "Switch the Pi off (sudo shutdown now), unplug it, and press the camera's ribbon cable "
    "firmly into both connectors. On the Pi, the cable's shiny contacts face the HDMI sockets."
)

LABELS = {
    State.SEATED: "sitting",
    State.STOOD_UP: "standing",
    State.AWAY: "nobody in view",
    State.TOO_DARK: "too dark",
}

INTRO = """
DESK CHECK (about {minutes} minutes)

It watches you sit, stand up, walk away and come back, then says what works and
what to change. It presses nothing. Do what the words in CAPITALS say; a beep
means it's time for the next step. Ctrl+C stops it.

"""


@dataclass
class Step:
    key: str
    say: str  # what to do, starting with the action in capitals
    seconds: float  # how long it watches
    ready: float = 5.0  # countdown before it starts watching


def steps(settings: Settings) -> list[Step]:
    away = settings.presence.away_seconds
    return [
        Step("sit", "SIT AT YOUR DESK the way you normally do, facing the screen.", 15),
        Step("stand", "STAND UP beside your chair and stay at your desk. Stretch if you like.", max(15, away + 10)),
        Step("leave", "WALK AWAY out of the camera's view, and wait there for the beep.", max(20, away + 15)),
        # No countdown: you're out of view, so the beep is the signal.
        Step("back", "COME BACK AND SIT DOWN.", 15, ready=0),
    ]


@dataclass
class Sample:
    """What the camera made of one moment."""

    obs: Observation
    people: int  # people counted
    model_saw: int  # bodies the pose model reported, before merging copies
    middle: tuple[float, float] | None  # middle of your body in the picture
    top: float | None  # your highest body point (0 is the top of the picture)
    lux: float | None


@dataclass
class Result:
    status: str
    name: str
    detail: str


class CameraProblem(Exception):
    pass


class CameraFeed:
    """Starts the camera and reads its results in the background, so the check can
    keep counting down, and can tell when the camera stops."""

    def __init__(self, model_path: str, images: bool = False):
        # libcamera's progress messages would bury the instructions.
        os.environ.setdefault("LIBCAMERA_LOG_LEVELS", "*:ERROR")
        try:
            from picamera2 import Picamera2

            cameras = Picamera2.global_camera_info()
        except Exception as e:
            raise CameraProblem(f"the camera software didn't start ({e}).") from e
        if not any(c.get("Model") == "imx500" for c in cameras):
            raise CameraProblem("the Pi can't find the camera. " + RESEAT_CABLE)
        self._results: queue.Queue[Frame] = queue.Queue()
        self._error: Exception | None = None
        threading.Thread(target=self._run, args=(model_path, images), daemon=True).start()

    def _run(self, model_path: str, images: bool) -> None:
        try:
            from .camera import PoseCamera

            for frame in PoseCamera(model_path).frames(wants_images=lambda: images):
                self._results.put(frame)
        except Exception as e:
            self._error = e

    def frames(self) -> Iterator[Frame | None]:
        """Results as they arrive, or None after a moment without one."""
        while True:
            if self._error is not None:
                if "busy" in str(self._error).lower():
                    raise CameraProblem("another program is using it. Is camera-test running in another window?")
                raise CameraProblem(f"it stopped with an error ({self._error}). {RESEAT_CABLE}")
            try:
                yield self._results.get(timeout=0.2)
            except queue.Empty:
                yield None


def show(out: TextIO, text: str) -> None:
    """Rewrites the current line, for countdowns."""
    out.write("\r     " + text.ljust(52))
    out.flush()


def sample(tracker: PresenceTracker, frame: Frame, obs: Observation) -> Sample:
    score = tracker.p.min_keypoint_score
    bodies = tracker.bodies(frame)
    you = bodies[0] if bodies else None
    box = you.box(score) if you else None
    return Sample(
        obs,
        len(bodies),
        len(frame.people),
        you.middle(score) if you else None,
        box[1] if box else None,
        frame.lux,
    )


def run_steps(
    frames: Iterator[Frame | None],
    tracker: PresenceTracker,
    plan: list[Step],
    clock: Callable[[], float],
    out: TextIO,
) -> dict[str, list[Sample]]:
    """Walks you through the steps and keeps what the camera saw during each one."""
    seen: dict[str, list[Sample]] = {}
    last_result = clock()

    def next_frame() -> Frame | None:
        nonlocal last_result
        frame = next(frames)
        if frame is not None:
            last_result = clock()
        elif clock() - last_result > CAMERA_STALL_SECONDS:
            raise CameraProblem("it stopped sending results partway through. " + RESEAT_CABLE)
        return frame

    for n, step in enumerate(plan, 1):
        out.write(f"\n{n}/{len(plan)}  {step.say}\n")
        start = clock()
        while (left := step.ready - (clock() - start)) > 0:
            frame = next_frame()
            if frame is not None:
                tracker.update(frame)  # keeps learning how you sit
            show(out, f"Starting in {math.ceil(left)}...")
        out.write("\a")  # beep: go
        samples: list[Sample] = []
        start = clock()
        while (left := step.seconds - (clock() - start)) > 0:
            frame = next_frame()
            if frame is None:
                continue
            obs = tracker.update(frame)
            samples.append(sample(tracker, frame, obs))
            show(out, f"Watching... {math.ceil(left)} s left   (now: {LABELS[obs.state]})")
        show(out, "Done.")
        out.write("\n")
        seen[step.key] = samples
    return seen


def share(samples: list[Sample], *states: State) -> float:
    if not samples:
        return 0.0
    return sum(s.obs.state in states for s in samples) / len(samples)


def percent(x: float) -> str:
    return "under 1%" if 0 < x < 0.005 else f"{round(100 * x)}%"


def breakdown(samples: list[Sample]) -> str:
    """Like "standing 70%, sitting 20%, nobody in view 10%"."""
    parts = sorted(((share(samples, state), label) for state, label in LABELS.items()), reverse=True)
    return ", ".join(f"{label} {percent(x)}" for x, label in parts if x > 0)


def brightness(samples: list[Sample]) -> str:
    lux = [s.lux for s in samples if s.lux is not None]
    return str(round(statistics.median(lux))) if lux else "unknown"


def lock_after(samples: list[Sample], settings: Settings) -> float | None:
    """Seconds from the first sample until the lock rules would have locked, as if
    you'd been sitting there just before. None if they wouldn't have."""
    policy = LockPolicy(settings.presence, settings.second_person)
    policy.armed = True
    for s in samples:
        if any(d.action == "lock" for d in policy.step(s.obs, pc_locked=False, paused=False)):
            return s.obs.time - samples[0].obs.time
    return None


def longest_gone(samples: list[Sample]) -> float:
    """The longest stretch, in seconds, with nobody in view."""
    longest, since = 0.0, None
    for s in samples:
        if s.obs.state is State.AWAY:
            since = s.obs.time if since is None else since
            longest = max(longest, s.obs.time - since)
        else:
            since = None
    return longest


def back_after(samples: list[Sample], hold: float = 1.0) -> float | None:
    """Seconds from the first sample until you were in view for `hold` seconds running."""
    since = None
    for s in samples:
        if s.obs.state in (State.SEATED, State.STOOD_UP):
            since = s.obs.time if since is None else since
            if s.obs.time - since >= hold:
                return since - samples[0].obs.time
        else:
            since = None
    return None


def aim_hint(samples: list[Sample]) -> str:
    """Advice on pointing the camera, from where you were in the picture."""
    xs = [s.middle[0] for s in samples if s.middle]
    tops = [s.top for s in samples if s.top is not None]
    if not xs:
        return ""
    if not 0.2 <= statistics.median(xs) <= 0.8:
        return "You're near the edge of the picture: turn the camera to face your chair."
    if statistics.median(tops) < 0.1:
        return "Your head is at the top edge of the picture: tilt the camera up a little."
    return ""


def too_dark(name: str, samples: list[Sample]) -> Result | None:
    if share(samples, State.TOO_DARK) <= 0.5:
        return None
    return Result(
        PROBLEM, name, f"too dark for the camera to judge (brightness {brightness(samples)}). Turn a light on."
    )


def judge_sitting(samples: list[Sample], settings: Settings) -> Result:
    name = "Sitting"
    if dark := too_dark(name, samples):
        return dark
    seen = share(samples, State.SEATED, State.STOOD_UP)
    hint = aim_hint(samples)
    if seen < 0.6 or lock_after(samples, settings) is not None:
        return Result(PROBLEM, name, f"it only saw you {percent(seen)} of the time. {hint or 'Point the camera at your chair.'}")
    if seen < 0.9:
        return Result(WARNING, name, f"it saw you {percent(seen)} of the time. {hint or 'Point the camera straight at your chair.'}")
    return Result(OK, name, f"it saw you {percent(seen)} of the time. {hint}".strip())


def judge_standing(samples: list[Sample], settings: Settings) -> Result:
    name = "Standing up"
    away = settings.presence.away_seconds
    split = breakdown(samples)
    locked = lock_after(samples, settings)
    if locked is not None:
        return Result(
            PROBLEM,
            name,
            f"it lost sight of you and would have locked after {locked:.0f} s ({split}). "
            "Tilt the camera up a little so it can still see your shoulders when you stand.",
        )
    gone = longest_gone(samples)
    if gone >= away / 2:
        return Result(
            WARNING,
            name,
            f"no lock, but it lost sight of you for {gone:.0f} s and it locks at {away:.0f} s ({split}). "
            "Tilting the camera up a little would make it safer.",
        )
    return Result(OK, name, f"no lock ({split}).")


def judge_leaving(samples: list[Sample], settings: Settings) -> Result:
    name = "Walking away"
    if dark := too_dark(name, samples):
        return dark
    locked = lock_after(samples, settings)
    if locked is not None:
        return Result(OK, name, f"it would have locked {locked:.0f} s after you set off.")
    gone = share(samples, State.AWAY)
    if gone < 0.3:
        return Result(
            PROBLEM,
            name,
            "it kept seeing a person after you left, so it wouldn't lock. Is there something in view "
            "that looks like one, like a coat on the chair, a poster or a mirror?",
        )
    return Result(
        PROBLEM, name, f"it wouldn't have locked, because it kept glimpsing someone ({breakdown(samples)})."
    )


def judge_back(samples: list[Sample]) -> Result:
    name = "Coming back"
    after = back_after(samples)
    if after is None:
        return Result(PROBLEM, name, "it didn't see you come back and sit down. Point the camera at your chair.")
    return Result(OK, name, f"it saw you again {after:.0f} s after the beep.")


def judge_second_person(samples: list[Sample]) -> Result:
    name = "Second person"
    if not samples or not any(s.obs.second_person for s in samples):
        return Result(OK, name, "none seen.")
    x = sum(s.obs.second_person for s in samples) / len(samples)
    return Result(
        WARNING,
        name,
        f"it thought someone else was in view {percent(x)} of the time. "
        "If you were on your own, something in view looks like a person to it.",
    )


def judge_camera(seen: dict[str, list[Sample]], plan: list[Step]) -> Result:
    samples = [s for v in seen.values() for s in v]
    rate = len(samples) / sum(step.seconds for step in plan)
    return Result(OK, "Camera", f"working ({rate:.0f} results a second, brightness {brightness(samples)}).")


# Bits of `vcgencmd get_throttled`.
UNDER_VOLTAGE_NOW = 0x1
UNDER_VOLTAGE_SINCE_START = 0x10000
# Slowed down (now, or since start), or too hot (now, or since start).
TOO_HOT = 0x4 | 0x8 | 0x40000 | 0x80000


def parse_throttled(text: str) -> int | None:
    """Reads output like "throttled=0x50005"."""
    try:
        return int(text.strip().split("=", 1)[1], 16)
    except (IndexError, ValueError):
        return None


def read_throttled() -> int | None:
    try:
        run = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_throttled(run.stdout)


def judge_power(flags: int | None) -> Result:
    name = "Power"
    if flags is None:
        return Result(WARNING, name, "couldn't read the Pi's power status.")
    if flags & UNDER_VOLTAGE_NOW:
        return Result(PROBLEM, name, "the voltage is too low right now. " + OWN_SUPPLY)
    if flags & UNDER_VOLTAGE_SINCE_START:
        return Result(
            WARNING,
            name,
            "the voltage dipped too low at some point since the Pi was switched on. " + OWN_SUPPLY,
        )
    if flags & TOO_HOT:
        return Result(WARNING, name, "the Pi got hot and slowed itself down. Give it some air.")
    return Result(OK, name, "steady.")


def read_usb(settings: Settings) -> tuple[str | None, bool]:
    """How the PC sees the Pi's USB-C port, and whether the keyboard and data line exist."""
    states = sorted(Path("/sys/class/udc").glob("*/state"))
    state = states[0].read_text().strip() if states else None
    devices = Path(settings.keyboard_device).exists() and Path(settings.helper_device).exists()
    return state, devices


def judge_keyboard(state: str | None, devices: bool) -> Result:
    name = "Keyboard mode"
    if state is None or not devices:
        return Result(PROBLEM, name, "it isn't switched on. Restart the Pi (sudo reboot), then type desk-check again.")
    if state == "configured":
        return Result(OK, name, "your PC has accepted the Pi as a keyboard.")
    if state == "not attached":
        return Result(
            NOT_YET,
            name,
            "ready, but no computer is connected to the Pi's USB-C port. Connect it through a USB-C "
            "power and data splitter: power from the Pi's own supply, data to the PC.",
        )
    if state == "suspended":
        return Result(WARNING, name, "your PC has put the Pi's USB connection to sleep. Is the PC asleep?")
    return Result(WARNING, name, f"your PC is still setting it up ({state}). Type desk-check again in a minute.")


def next_step(results: list[Result]) -> str:
    problem = next((r for r in results if r.status == PROBLEM), None)
    if problem:
        return f"Next: sort out the {problem.name.lower()} problem, then type desk-check again."
    if any(r.status == NOT_YET for r in results):
        return "Next: connect the Pi to your PC through a USB-C power and data splitter, then type desk-check again."
    if any(r.status == WARNING for r in results):
        return "It works. The warnings above are worth a look."
    return "Everything works."


def where_head(samples: list[Sample]) -> str:
    tops = [s.top for s in samples if s.top is not None]
    xs = [s.middle[0] for s in samples if s.middle]
    if len(tops) < len(samples) / 2:
        return "mostly out of view"
    return f"{percent(statistics.median(tops))} down, {percent(statistics.median(xs))} across"


def report(results: list[Result], seen: dict[str, list[Sample]], practice_mode: bool) -> str:
    width = len(PROBLEM)
    lines = ["", "RESULTS", ""]
    for r in results:
        lines += textwrap.wrap(
            f"{r.status:<{width}}  {r.name}: {r.detail}", width=78, subsequent_indent=" " * (width + 2)
        )
    lines.append("")
    if seen:
        lines += textwrap.wrap(
            f"Your head in the picture: sitting {where_head(seen['sit'])}; "
            f"standing {where_head(seen['stand'])}.",
            width=78,
        )
    if practice_mode:
        lines.append("Practice mode is on: it writes down what it would do and presses nothing.")
    else:
        lines.append("Practice mode is off: it locks the PC for real.")
    lines += textwrap.wrap(next_step(results), width=78)
    return "\n".join(lines) + "\n"


def timeline(seen: dict[str, list[Sample]]) -> str:
    """Every half second of the check, for looking into problems later."""
    lines = ["", "TIMELINE (seconds into each step)"]
    for key, samples in seen.items():
        shown = None
        for s in samples:
            t = s.obs.time - samples[0].obs.time
            if shown is not None and t - shown < 0.5:
                continue
            shown = t
            middle = "-" if s.middle is None else f"{s.middle[0]:.2f},{s.middle[1]:.2f}"
            top = "-" if s.top is None else f"{s.top:.2f}"
            lines.append(
                f"{key:<6}{t:5.1f}  {s.obs.state.value:<9} people={s.people} model_saw={s.model_saw} "
                f"middle={middle} top={top} brightness={s.lux if s.lux is None else round(s.lux)} "
                f"second_person={s.obs.second_person}"
            )
    return "\n".join(lines) + "\n"


def main(
    settings: Settings,
    out: TextIO = sys.stdout,
    camera: Callable[[str], CameraFeed] = CameraFeed,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    plan = steps(settings)
    out.write(INTRO.format(minutes=math.ceil(sum(s.ready + s.seconds for s in plan) / 60)))
    seen: dict[str, list[Sample]] = {}
    try:
        out.write("Starting the camera. The first start after switching on can take a minute...\n")
        frames = camera(settings.camera_model).frames()
        start = clock()
        while next(frames) is None:
            if clock() - start > CAMERA_START_SECONDS:
                raise CameraProblem("it didn't start. " + RESEAT_CABLE)
        out.write("Camera ready.\n")
        tracker = PresenceTracker(settings.presence, settings.second_person)
        seen = run_steps(frames, tracker, plan, clock, out)
        camera_result = judge_camera(seen, plan)
    except CameraProblem as e:
        seen = {}
        camera_result = Result(PROBLEM, "Camera", str(e))
    except KeyboardInterrupt:
        out.write("\nStopped.\n")
        return 130

    # In the order to sort things out: "Next" points at the first problem.
    results = [judge_power(read_throttled()), camera_result]
    if seen:
        everything = [s for v in seen.values() for s in v]
        results += [
            judge_sitting(seen["sit"], settings),
            judge_standing(seen["stand"], settings),
            judge_leaving(seen["leave"], settings),
            judge_back(seen["back"]),
            judge_second_person(everything),
        ]
    results.append(judge_keyboard(*read_usb(settings)))
    text = report(results, seen, settings.practice_mode)
    folder = Path(settings.event_log).expanduser().parent / "checks"
    folder.mkdir(parents=True, exist_ok=True)
    saved = folder / time.strftime("%Y-%m-%d_%H%M%S.txt")
    saved.write_text(text + timeline(seen), encoding="utf-8")
    out.write(text + f"Saved to {saved}\n")
    return 0
