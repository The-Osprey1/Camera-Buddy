"""Enrolling your face and testing face unlock, run by `desk-face`.

Like desk-check, it says on screen what to do and beeps when it's time for the next
step. It presses nothing.

    desk-face             enrolls you if you aren't yet, then tests that it knows you
    desk-face add         adds more looks at you, say in evening light, then tests
    desk-face enroll      starts your enrolled face over
    desk-face test        only the test
    desk-face photo-test  hold a photo of yourself up: it should refuse it
    desk-face forget      deletes your enrolled face, which turns face unlock off

Enrolling keeps 128 numbers per look at you in ~/desk-presence/face.npz, never a picture.
"""

from __future__ import annotations

import math
import statistics
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, TextIO

import numpy as np

from . import faces
from .check import OK, PROBLEM, WARNING, CameraFeed, CameraProblem, Result, show
from .config import Settings
from .pose import Frame
from .presence import Observation, PresenceTracker, State
from .unlock import UnlockPolicy

CAMERA_START_SECONDS = 150.0
# One look per this many seconds while enrolling, so the looks differ a little.
ENROLL_EVERY = 0.25
ENOUGH_LOOKS = 20
MOST_LOOKS = 400


@dataclass
class Step:
    say: str
    seconds: float
    ready: float = 4.0


ENROLL_STEPS = [
    Step("SIT AT YOUR DESK AND LOOK AT YOUR SCREEN the way you normally do.", 8),
    Step("LOOK STRAIGHT AT THE CAMERA.", 6),
    Step("TURN YOUR HEAD SLOWLY a little to the left, then a little to the right.", 8),
    Step("LEAN BACK in your chair, still facing the screen.", 6),
    Step("LEAN IN towards the screen.", 6),
]
TEST_STEP = Step("SIT NORMALLY AND LOOK AT YOUR SCREEN, as if you'd just come back.", 15)
PHOTO_STEP = Step(
    "SIT DOWN AND HOLD A PHOTO OF YOUR FACE in front of your own face, on your phone or printed. "
    "Keep it there until the beep.",
    15,
    ready=8,
)


@dataclass
class Tally:
    """What happened on each look at the picture."""

    looks: int = 0
    nobody: int = 0
    someone_else_too: int = 0
    no_face: int = 0
    not_on_body: int = 0
    too_dark: int = 0
    checks: list[faces.FaceCheck] = field(default_factory=list)  # the usable ones
    vectors: list[np.ndarray] = field(default_factory=list)
    unlock_after: float | None = None  # practice unlock rules: seconds until it would have unlocked
    refused_as_fake: bool = False

    def main_trouble(self) -> str:
        """The likeliest reason it didn't get good looks at you."""
        troubles = [
            (self.nobody, "it couldn't see anyone in the chair. Point the camera at your chair."),
            (self.someone_else_too, "someone else was in view. Try again on your own."),
            (self.no_face, "it saw your body but not your face. Face the screen, and check your face isn't at the edge of the picture."),
            (self.not_on_body, "the face it found didn't line up with your body. Sit square to the camera."),
            (self.too_dark, "your face was too dark. Turn on a lamp or the room light."),
        ]
        count, text = max(troubles, key=lambda t: t[0])
        return text if count else "it didn't get enough good looks at you."


def watch(
    frames: Iterator[Frame | None],
    tracker: PresenceTracker,
    engine,
    faceprint: faces.Faceprint | None,
    settings: Settings,
    step: Step,
    clock: Callable[[], float],
    out: TextIO,
    label: str,
    every: float = 0.15,
) -> Tally:
    """Counts down, beeps, then looks at the picture for the step's length."""
    out.write(f"\n{label}  {step.say}\n")
    start = clock()
    while (left := step.ready - (clock() - start)) > 0:
        frame = next(frames)
        if frame is not None:
            tracker.update(frame)
        show(out, f"Starting in {math.ceil(left)}...")
    out.write("\a")
    tally = Tally()
    policy = UnlockPolicy(settings.unlock, enrolled=faceprint is not None, live=False)
    policy.note_lock(0.0)
    score = settings.presence.min_keypoint_score
    start = clock()
    last = float("-inf")
    while (left := step.seconds - (clock() - start)) > 0:
        frame = next(frames)
        if frame is None or frame.image is None:
            continue
        obs = tracker.update(frame)
        check = None
        if frame.time - last >= every:
            last = frame.time
            check = look(engine, frame, obs, tracker, score, faceprint, tally)
        for decision in policy.step(Observation(obs.time, obs.state, obs.second_person), check, None, False):
            if decision.action == "would_unlock" and tally.unlock_after is None:
                tally.unlock_after = clock() - start
            elif decision.action == "fake_face":
                tally.refused_as_fake = True
        show(out, f"Watching... {math.ceil(left)} s left   (good looks so far: {len(tally.checks)})")
    show(out, "Done.")
    out.write("\n")
    return tally


def look(engine, frame, obs, tracker, score, faceprint, tally: Tally) -> faces.FaceCheck | None:
    tally.looks += 1
    bodies = tracker.bodies(frame)
    if not bodies or obs.state is State.AWAY:
        tally.nobody += 1
        return None
    if obs.second_person:
        tally.someone_else_too += 1
        return None
    check, vector = faces.look(engine, frame.image, bodies[0], score, faceprint)
    if not check.found:
        tally.no_face += 1
    elif not check.fits_body:
        tally.not_on_body += 1
    elif check.too_dark:
        tally.too_dark += 1
    elif vector is not None:
        tally.vectors.append(vector)
        if faceprint is None or check.similarity is not None:
            tally.checks.append(check)
    return check if check.found else None


def percent(x: float) -> str:
    return f"{round(100 * x)}%"


def judge_enroll(tallies: list[Tally]) -> Result:
    got = sum(len(t.vectors) for t in tallies)
    if got >= ENOUGH_LOOKS:
        return Result(OK, "Enrolling", f"kept {got} looks at you.")
    worst = max(tallies, key=lambda t: t.looks - len(t.vectors))
    return Result(PROBLEM, "Enrolling", f"it only got {got} good looks at you, because {worst.main_trouble()}")


def judge_recognition(tally: Tally, settings: Settings) -> Result:
    name = "Recognizing you"
    sims = [c.similarity for c in tally.checks if c.similarity is not None]
    if not sims:
        return Result(PROBLEM, name, f"no good looks at you, because {tally.main_trouble()}")
    threshold = settings.unlock.match_threshold
    matched = sum(s >= threshold for s in sims) / len(sims)
    middle = statistics.median(sims)
    detail = f"matched on {percent(matched)} of {len(sims)} looks (typical match {middle:.2f}, needs {threshold:.2f})"
    if tally.unlock_after is not None:
        return Result(OK, name, f"{detail}; it would have unlocked {tally.unlock_after:.1f} s after the beep.")
    if tally.refused_as_fake:
        return Result(PROBLEM, name, f"{detail}, but the fake-face check took you for a photo. See below.")
    return Result(
        PROBLEM,
        name,
        f"{detail}, so it wouldn't have unlocked. Type desk-face add to give it more looks at you, "
        "in the light you have now.",
    )


def judge_real(tally: Tally, settings: Settings) -> Result:
    name = "Fake-face check"
    threshold = settings.unlock.match_threshold
    scores = [c.real_score for c in tally.checks if c.real_score is not None and (c.similarity or 0) >= threshold]
    if not scores:
        return Result(WARNING, name, "no matching looks to judge.")
    real = sum(s >= settings.unlock.real_face_threshold for s in scores) / len(scores)
    detail = f"took you for a real face on {percent(real)} of looks (typical score {statistics.median(scores):.1f})"
    if real >= 0.9:
        return Result(OK, name, detail + ".")
    advice = (
        "More light on your face, from the front, helps most. "
        "If that doesn't do it, lower real_face_threshold in config.toml a little."
    )
    status = WARNING if real >= 0.6 else PROBLEM
    return Result(status, name, f"{detail}. {advice}")


def judge_photo(tally: Tally) -> Result:
    name = "Photo test"
    if tally.unlock_after is not None:
        return Result(
            PROBLEM,
            name,
            "the photo fooled it: it would have unlocked. Raise real_face_threshold in config.toml "
            "(try 1.0), then type desk-face photo-test again.",
        )
    if tally.refused_as_fake:
        return Result(OK, name, "it spotted the photo as fake and would have turned auto-unlock off.")
    if not tally.checks:
        return Result(OK, name, f"it wouldn't have unlocked: {tally.main_trouble()}")
    return Result(OK, name, "it wouldn't have unlocked: the photo never matched well enough.")


def judge_light(tally: Tally) -> Result | None:
    levels = [c.brightness for c in tally.checks if c.brightness is not None]
    if not levels:
        return None
    middle = statistics.median(levels)
    if middle < 70:
        return Result(WARNING, "Light", f"your face is dim ({middle:.0f} of 255). A lamp facing you will make it surer.")
    return Result(OK, "Light", f"your face is lit well enough ({middle:.0f} of 255).")


def combine(old: faces.Faceprint | None, vectors: list[np.ndarray], widths: list[float]) -> faces.Faceprint:
    new_v = np.stack(vectors)
    new_w = np.asarray(widths, dtype=np.float32)
    if old is not None:
        new_v = np.concatenate([old.vectors, new_v])[-MOST_LOOKS:]
        new_w = np.concatenate([old.widths, new_w])[-MOST_LOOKS:]
    return faces.Faceprint(new_v, new_w)


def mode_note(settings: Settings) -> str:
    mode = settings.unlock.mode
    if mode == "off":
        return "Face unlock is off (unlock.mode in config.toml)."
    if mode == "on" and not settings.practice_mode:
        return "Face unlock is on: it types your PIN when it recognizes you."
    return (
        "Face unlock is in practice: when you come back after it would have locked, it beeps "
        "and writes down whether it would have unlocked. It types nothing."
    )


def report(results: list[Result], settings: Settings) -> str:
    width = len(PROBLEM)
    lines = ["", "RESULTS", ""]
    for r in results:
        lines += textwrap.wrap(
            f"{r.status:<{width}}  {r.name}: {r.detail}", width=78, subsequent_indent=" " * (width + 2)
        )
    lines.append("")
    lines += textwrap.wrap(mode_note(settings), width=78)
    problem = next((r for r in results if r.status == PROBLEM), None)
    if problem:
        lines += textwrap.wrap(f"Next: sort out the {problem.name.lower()} problem, then type desk-face again.", width=78)
    return "\n".join(lines) + "\n"


def main(
    settings: Settings,
    action: str = "auto",
    out: TextIO = sys.stdout,
    camera: Callable[..., CameraFeed] = CameraFeed,
    engine_factory: Callable[[str], object] = faces.FaceEngine,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    face_file = Path(settings.unlock.face_file).expanduser()
    if action == "forget":
        if face_file.exists():
            face_file.unlink()
            out.write("Deleted your enrolled face. Face unlock is off until you type desk-face again.\n")
        else:
            out.write("No face was enrolled.\n")
        return 0

    faceprint = faces.load_faceprint(face_file)
    if action in ("test", "photo-test") and faceprint is None:
        out.write("No face enrolled yet. Type desk-face to enroll.\n")
        return 1
    enrolling = action in ("enroll", "add") or (action == "auto" and faceprint is None)
    testing = action in ("auto", "add", "enroll", "test")

    out.write("\nDESK FACE\n\nDo what the words in CAPITALS say; a beep means it's time for the next step.\n")
    out.write("It presses nothing, and keeps no pictures. Ctrl+C stops it.\n\n")
    try:
        engine = engine_factory(settings.unlock.models_dir)
    except faces.FaceModelsMissing as e:
        out.write(report([Result(PROBLEM, "Face models", str(e))], settings))
        return 1

    results: list[Result] = []
    try:
        out.write("Starting the camera. The first start after switching on can take a minute...\n")
        frames = camera(settings.camera_model, images=True).frames()
        start = clock()
        while next(frames) is None:
            if clock() - start > CAMERA_START_SECONDS:
                raise CameraProblem("it didn't start.")
        out.write("Camera ready.\n")
        tracker = PresenceTracker(settings.presence, settings.second_person)

        if enrolling:
            tallies = []
            steps = len(ENROLL_STEPS) + (1 if testing else 0)
            for n, step in enumerate(ENROLL_STEPS, 1):
                tallies.append(
                    watch(frames, tracker, engine, None, settings, step, clock, out, f"{n}/{steps}", ENROLL_EVERY)
                )
            result = judge_enroll(tallies)
            results.append(result)
            if result.status == OK:
                vectors = [v for t in tallies for v in t.vectors]
                widths = [c.width for t in tallies for c in t.checks]
                faceprint = combine(faceprint if action == "add" else None, vectors, widths)
                faces.save_faceprint(face_file, faceprint)
                out.write(f"Saved your face ({len(faceprint.vectors)} looks, numbers only) to {face_file}\n")
            else:
                testing = False
            label = f"{steps}/{steps}"
        else:
            label = "1/1"

        if testing and faceprint is not None:
            tally = watch(frames, tracker, engine, faceprint, settings, TEST_STEP, clock, out, label)
            results.append(judge_recognition(tally, settings))
            results.append(judge_real(tally, settings))
            if light := judge_light(tally):
                results.append(light)
        if action == "photo-test":
            tally = watch(frames, tracker, engine, faceprint, settings, PHOTO_STEP, clock, out, "1/1")
            results.append(judge_photo(tally))
    except CameraProblem as e:
        results.append(Result(PROBLEM, "Camera", str(e)))
    except KeyboardInterrupt:
        out.write("\nStopped.\n")
        return 130

    text = report(results, settings)
    folder = Path(settings.event_log).expanduser().parent / "checks"
    folder.mkdir(parents=True, exist_ok=True)
    saved = folder / time.strftime("face_%Y-%m-%d_%H%M%S.txt")
    saved.write_text(text, encoding="utf-8")
    out.write(text + f"Saved to {saved}\n")
    if any(r.status == PROBLEM for r in results):
        return 1
    if faceprint is not None and enrolling:
        out.write("The background program picks up your face when desk-face finishes.\n")
    return 0
