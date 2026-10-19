"""Command line: `python3 -m desk_presence run|camera-test|log`."""

from __future__ import annotations

import argparse
import logging
import time

from . import config, events


def main() -> None:
    parser = argparse.ArgumentParser(prog="desk_presence")
    parser.add_argument("--config", help="path to your settings file (TOML)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="watch the desk and lock the PC when you leave")
    sub.add_parser("camera-test", help="print what the camera sees, press nothing")
    sub.add_parser("log", help="summarize the event log")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings = config.load(args.config)

    if args.command == "log":
        print(events.summarize(settings.event_log))
        return

    from .camera import PoseCamera

    camera = PoseCamera(settings.camera_model)
    if args.command == "camera-test":
        from .presence import PresenceTracker

        tracker = PresenceTracker(settings.presence, settings.second_person)
        last = 0.0
        for frame in camera.frames():
            obs = tracker.update(frame)
            if frame.time - last >= 1:
                last = frame.time
                score = settings.presence.min_keypoint_score
                sizes = [round(p.area(score), 3) for p in frame.people]
                print(
                    f"{time.strftime('%H:%M:%S')}  {obs.state.value:<9} people={len(frame.people)} "
                    f"sizes={sizes} brightness={frame.lux} second_person={obs.second_person}"
                )
        return

    from .app import build

    app = build(settings)
    if settings.practice_mode:
        logging.info("Practice mode: no keys will be pressed. See the log with `log`.")
    app.run(camera.frames())


if __name__ == "__main__":
    main()
