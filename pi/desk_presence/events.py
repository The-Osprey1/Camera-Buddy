"""The event log: one line of JSON per event, so you can read back what happened."""

from __future__ import annotations

import json
import time
from pathlib import Path


class EventLog:
    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **details) -> None:
        entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **details}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


SHOWN = {
    "would_lock", "locked", "second_person", "paused", "resumed", "started",
    "would_unlock", "unlocked", "unlock_failed", "unlock_skipped", "not_recognized",
    "fake_face", "pin_received", "face_unlock_off",
}


def summarize(path: str) -> str:
    """A readable summary of the log, for checking practice mode before going live."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"No log yet at {p}"
    lines = []
    counts: dict[str, int] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue
        counts[e.get("event", "?")] = counts.get(e.get("event", "?"), 0) + 1
        if e.get("event") in SHOWN:
            extra = {k: v for k, v in e.items() if k not in ("at", "event")}
            lines.append(f"{e['at']}  {e['event']:<14} {extra if extra else ''}".rstrip())
    total = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    return "\n".join(lines + ["", f"Totals: {total}"])
