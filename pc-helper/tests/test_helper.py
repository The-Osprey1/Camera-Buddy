import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import desk_helper  # noqa: E402


def test_known_messages_from_pi():
    assert desk_helper.parse_from_pi(b"WARN SECOND_PERSON\n") == "WARN SECOND_PERSON"
    assert desk_helper.parse_from_pi(b"PAUSED\r\n") == "PAUSED"


def test_anything_else_is_ignored():
    assert desk_helper.parse_from_pi(b"cmd.exe /c del\n") is None
    assert desk_helper.parse_from_pi(b"\xff\xfe\n") is None
    assert desk_helper.parse_from_pi(b"PAUSED" * 10) is None


def test_messages_match_the_pi():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pi"))
    from desk_presence import protocol

    assert set(desk_helper.FROM_PI) == protocol.FROM_PI
    assert desk_helper.TO_PI == protocol.FROM_PC
