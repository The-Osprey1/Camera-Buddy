import sys

import numpy as np

from desk_presence import faces
from desk_presence.faces import Faceprint
from fake_faces import IMAGE, SOMEONE_ELSE, YOU, FakeEngine, face_box
from helpers import person


def faceprint(width=0.12):
    return Faceprint(np.stack([YOU, YOU, YOU]), np.array([width] * 3, dtype=np.float32))


def test_similarity_is_one_for_you_and_low_for_someone_else():
    fp = faceprint()
    assert faces.similarity(YOU, fp) > 0.99
    assert faces.similarity(SOMEONE_ELSE, fp) < 0.01
    assert faces.similarity(YOU * 5, fp) > 0.99  # length doesn't matter


def test_faceprint_keeps_numbers_only_and_round_trips(tmp_path):
    path = tmp_path / "face.npz"
    faces.save_faceprint(path, faceprint())
    if sys.platform != "win32":
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    with np.load(path, allow_pickle=False) as data:
        assert sorted(data.files) == ["vectors", "widths"]
    back = faces.load_faceprint(path)
    assert back.vectors.shape == (3, 128) and abs(back.usual_width - 0.12) < 1e-6
    assert faces.load_faceprint(tmp_path / "missing.npz") is None


def test_face_must_sit_on_the_body():
    body = person()  # nose at (0.5, 0.4), shoulders 0.3 apart
    assert faces.fits_body(face_box(), body, 0.3, None)
    assert not faces.fits_body(face_box(x=0.8), body, 0.3, None)  # beside the head


def test_face_must_be_the_right_size():
    body = person()
    assert not faces.fits_body(face_box(width=0.35), body, 0.3, None)  # photo held up close
    assert not faces.fits_body(face_box(width=0.04), body, 0.3, None)  # tiny, far away
    # Fine for the shoulders, but much bigger than when you enrolled.
    assert faces.fits_body(face_box(width=0.2), body, 0.3, None)
    assert not faces.fits_body(face_box(width=0.2), body, 0.3, usual_width=0.1)


def test_face_needs_both_shoulders():
    body = person()
    body.keypoints[5] = (0.0, 0.0, 0.0)
    assert not faces.fits_body(face_box(), body, 0.3, None)


def test_look_finds_you():
    engine = FakeEngine()
    check, vector = faces.look(engine, IMAGE, person(), 0.3, faceprint())
    assert check.usable and check.similarity > 0.99 and check.real_score == 3.0
    assert vector is not None


def test_look_ignores_a_face_off_your_body():
    engine = FakeEngine()
    engine.faces = [(face_box(x=0.85), YOU, 3.0)]
    check, vector = faces.look(engine, IMAGE, person(), 0.3, faceprint())
    assert not check.found and vector is None


def test_look_in_the_dark_judges_nothing():
    engine = FakeEngine()
    engine.bright = 10.0
    check, vector = faces.look(engine, IMAGE, person(), 0.3, faceprint())
    assert check.found and check.too_dark and not check.usable and vector is None
