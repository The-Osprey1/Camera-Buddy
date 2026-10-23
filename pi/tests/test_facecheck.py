import io

from desk_presence import facecheck, faces
from desk_presence.config import Settings, UnlockSettings
from desk_presence.pose import Frame
from fake_faces import IMAGE, SOMEONE_ELSE, YOU, FakeEngine, face_box
from helpers import person


class FakeCamera:
    """You, sitting, at 10 pictures a second on a pretend clock."""

    def __init__(self, people=lambda t: [person()]):
        self.people = people
        self.now = 0.0

    def __call__(self, model_path, images=False):
        assert images
        return self

    def clock(self):
        return self.now

    def frames(self):
        while True:
            self.now = round(self.now + 0.1, 3)
            yield Frame(self.now, self.people(self.now), 100.0, IMAGE)


def settings(tmp_path):
    return Settings(
        event_log=str(tmp_path / "events.jsonl"),
        unlock=UnlockSettings(face_file=str(tmp_path / "face.npz")),
    )


def run(s, action, engine, camera=None):
    camera = camera or FakeCamera()
    out = io.StringIO()
    code = facecheck.main(s, action, out, camera, lambda _: engine, camera.clock)
    return code, out.getvalue()


def test_enroll_then_test(tmp_path):
    s = settings(tmp_path)
    code, text = run(s, "auto", FakeEngine())
    assert code == 0, text
    assert "OK       Enrolling: kept" in text
    assert "OK       Recognizing you" in text and "would have unlocked" in text
    assert "OK       Fake-face check" in text
    fp = faces.load_faceprint(tmp_path / "face.npz")
    assert len(fp.vectors) >= facecheck.ENOUGH_LOOKS
    assert list((tmp_path / "checks").glob("face_*.txt"))


def test_add_keeps_the_old_looks(tmp_path):
    s = settings(tmp_path)
    run(s, "enroll", FakeEngine())
    before = len(faces.load_faceprint(tmp_path / "face.npz").vectors)
    run(s, "add", FakeEngine())
    assert len(faces.load_faceprint(tmp_path / "face.npz").vectors) == 2 * before


def test_enroll_in_the_dark_says_why(tmp_path):
    engine = FakeEngine()
    engine.bright = 10.0
    code, text = run(settings(tmp_path), "auto", engine)
    assert code == 1 and "PROBLEM  Enrolling" in text and "face was too\n" in text
    assert not (tmp_path / "face.npz").exists()


def test_someone_else_is_not_recognized(tmp_path):
    s = settings(tmp_path)
    run(s, "enroll", FakeEngine())
    stranger = FakeEngine()
    stranger.faces = [(face_box(), SOMEONE_ELSE, 3.0)]
    code, text = run(s, "test", stranger)
    assert code == 1 and "PROBLEM  Recognizing you" in text and "wouldn't have unlocked" in text


def test_photo_test(tmp_path):
    s = settings(tmp_path)
    run(s, "enroll", FakeEngine())
    photo = FakeEngine()
    photo.faces = [(face_box(), YOU, -4.0)]
    code, text = run(s, "photo-test", photo)
    assert code == 0 and "OK       Photo test: it spotted the photo" in text
    fooled = FakeEngine()
    code, text = run(s, "photo-test", fooled)
    assert code == 1 and "PROBLEM  Photo test: the photo fooled it" in text


def test_missing_models_are_explained(tmp_path):
    def no_models(_):
        raise faces.FaceModelsMissing("the face models aren't downloaded yet")

    out = io.StringIO()
    code = facecheck.main(settings(tmp_path), "auto", out, FakeCamera(), no_models)
    assert code == 1 and "aren't downloaded yet" in out.getvalue()


def test_forget(tmp_path):
    s = settings(tmp_path)
    run(s, "enroll", FakeEngine())
    code, text = run(s, "forget", FakeEngine())
    assert code == 0 and not (tmp_path / "face.npz").exists()
