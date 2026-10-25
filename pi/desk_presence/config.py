"""Settings, loaded from a TOML file. Every setting has a safe default."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class PresenceSettings:
    # A pose keypoint counts only if the model is at least this sure of it (0 to 1).
    min_keypoint_score: float = 0.3
    # How many head and shoulder points must be visible for a body to count.
    min_upper_body_points: int = 3
    # Your body must be gone for this long before the PC locks.
    away_seconds: float = 5.0
    # A body must be seen this long before it counts as "back" (ignores one-frame flickers).
    return_confirm_seconds: float = 0.5
    # Shoulders this far above your usual seated height (as a share of picture height) means "stood up".
    stand_rise: float = 0.15
    # Below this brightness the Pi does nothing at all, because it can't judge. 0 turns the check off.
    min_lux: float = 5.0


@dataclass
class SecondPersonSettings:
    # A second body this big compared with yours counts (filters out people far behind you).
    min_size_ratio: float = 0.25
    # It has to stay in view this long.
    seconds: float = 2.0
    # "warn" tells the PC helper; "lock" also locks the PC; "off" ignores it.
    action: str = "warn"
    # At most one warning per this many seconds.
    cooldown_seconds: float = 60.0


@dataclass
class UnlockSettings:
    # "practice" writes down when it would have unlocked and types nothing; "on" types
    # your PIN for real (only once practice_mode is off too); "off" never looks for faces.
    mode: str = "practice"
    # Your enrolled face (numbers only, no photos). desk-face makes it.
    face_file: str = "~/desk-presence/face.npz"
    models_dir: str = "/opt/desk-presence/models"
    # How alike a face must be to yours (0 to 1). Higher is stricter.
    match_threshold: float = 0.45
    # Your face must match on this many checks in a row, spread over at least match_seconds.
    match_frames: int = 5
    match_seconds: float = 0.8
    # Stop looking this long after you sit down, until you next leave and come back.
    give_up_seconds: float = 30.0
    # Only unlock if the Pi itself locked the PC, at most this many hours ago.
    max_hours: float = 4.0
    # The fake-face check: below this score a face counts as a photo or a screen.
    # 0 is the model's own even line; raise it to be stricter. A matching face that
    # looks fake on fake_frames checks turns auto-unlock off until you unlock by hand.
    real_face_threshold: float = 0.0
    fake_frames: int = 3


@dataclass
class DisplaySettings:
    enabled: bool = True
    i2c_bus: int = 1
    address: int = 0x3C
    # Degrees, rather than luma's quarter-turn numbering.
    rotate: int = 0


@dataclass
class Settings:
    # Practice mode: write down what it would do, press no keys. Leave on for the first week.
    practice_mode: bool = True
    keyboard_device: str = "/dev/hidg0"
    helper_device: str = "/dev/ttyGS0"
    event_log: str = "~/desk-presence/events.jsonl"
    camera_model: str = "/usr/share/imx500-models/imx500_network_higherhrnet_coco.rpk"
    # How long the pause switch pauses for.
    pause_seconds: float = 3600.0
    # Raspberry Pi pin number (BCM numbering) for an optional pause button. 0 means no button.
    pause_button_pin: int = 0
    presence: PresenceSettings = field(default_factory=PresenceSettings)
    second_person: SecondPersonSettings = field(default_factory=SecondPersonSettings)
    unlock: UnlockSettings = field(default_factory=UnlockSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)


def _apply(target, values: dict, where: str) -> None:
    known = {f.name: f for f in fields(target)}
    for key, value in values.items():
        if key not in known:
            raise ValueError(f"Unknown setting '{where}{key}'")
        current = getattr(target, key)
        if isinstance(value, dict):
            _apply(current, value, f"{where}{key}.")
        else:
            if isinstance(current, float) and isinstance(value, int):
                value = float(value)
            if type(value) is not type(current):
                raise ValueError(
                    f"Setting '{where}{key}' should be {type(current).__name__}, got {value!r}"
                )
            setattr(target, key, value)


def load(path: str | Path | None) -> Settings:
    settings = Settings()
    if path is not None:
        with open(path, "rb") as f:
            _apply(settings, tomllib.load(f), "")
    if settings.second_person.action not in ("warn", "lock", "off"):
        raise ValueError("second_person.action must be 'warn', 'lock' or 'off'")
    if settings.unlock.mode not in ("practice", "on", "off"):
        raise ValueError("unlock.mode must be 'practice', 'on' or 'off'")
    if settings.display.i2c_bus < 0:
        raise ValueError("display.i2c_bus must be nonnegative")
    if settings.display.address not in (0x3C, 0x3D):
        raise ValueError("display.address must be 0x3c or 0x3d")
    if settings.display.rotate not in (0, 180):
        raise ValueError("display.rotate must be 0 or 180 degrees")
    return settings
