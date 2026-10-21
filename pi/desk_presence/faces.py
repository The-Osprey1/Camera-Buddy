"""Recognizing your face, on the Pi, from the camera's ordinary picture.

The pose model keeps running on the camera's chip. Only while it matters (the PC is
locked and someone has sat down) does the Pi look at the picture itself, with three
small models from setup/get-models.sh:
- a face finder (YuNet), which also marks the eyes, nose and mouth;
- a face describer (SFace), which turns a face into 128 numbers, so two pictures of
  the same person give nearly the same numbers;
- a fake-face check (MiniFAS, from face-antispoof-onnx), which looks for paper texture,
  screen glare and the patterns screens leave.

Enrolling keeps only those numbers in ~/desk-presence/face.npz, never a picture.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .pose import LEFT_EAR, LEFT_EYE, LEFT_SHOULDER, NOSE, RIGHT_EAR, RIGHT_EYE, RIGHT_SHOULDER, Person

# The files setup/get-models.sh downloads (and checks) into the models folder.
DETECTOR = "face_detection_yunet_2023mar.onnx"
RECOGNIZER = "face_recognition_sface_2021dec.onnx"
REAL_FACE = "face_antispoof_minifas.onnx"
MODEL_FILES = (DETECTOR, RECOGNIZER, REAL_FACE)

# The face finder model needs OpenCV 4.8 or newer.
MIN_OPENCV = (4, 8)
# Faces smaller than this many pixels across are too small to recognize.
MIN_FACE_PIXELS = 40
# A face darker than this (0 to 255) is too dark to judge.
MIN_FACE_BRIGHTNESS = 40.0
# Your face is about this share of your shoulder width; outside it, it isn't sitting on your body.
FACE_TO_SHOULDERS = (0.2, 0.9)
# And about the size it was when you enrolled, give or take leaning in or back.
FACE_TO_USUAL = (0.6, 1.7)


class FaceModelsMissing(Exception):
    pass


@dataclass
class Face:
    # Left, top, width and height in pixels.
    box: tuple[float, float, float, float]
    score: float
    # The face finder's whole result (box, eyes, nose, mouth corners, score), for lining it up.
    row: np.ndarray


@dataclass
class FaceCheck:
    """What one look at the picture found about the person in the chair."""

    found: bool  # a face sits where the pose model put your head
    fits_body: bool = False
    too_dark: bool = False
    similarity: float | None = None  # 0 to 1, how alike it is to your enrolled face
    real_score: float | None = None  # above the threshold: a real face; below: a photo or screen
    width: float | None = None  # face width as a share of the picture width
    brightness: float | None = None  # of the face, 0 to 255

    @property
    def usable(self) -> bool:
        return self.found and self.fits_body and not self.too_dark and self.similarity is not None


@dataclass
class Faceprint:
    """Your enrolled face: one row of 128 numbers per look, and how wide your face was."""

    vectors: np.ndarray  # (n, 128), each row of length 1
    widths: np.ndarray  # (n,) face width as a share of the picture width

    @property
    def usual_width(self) -> float:
        return float(np.median(self.widths))


def normalized(vector: np.ndarray) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def similarity(vector: np.ndarray, faceprint: Faceprint) -> float:
    """How alike a face is to yours: the average of its three closest enrolled looks."""
    scores = faceprint.vectors @ normalized(vector)
    best = np.sort(scores)[-3:]
    return float(np.mean(best))


def save_faceprint(path: str | Path, faceprint: Faceprint) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.npz")
    np.savez(tmp, vectors=faceprint.vectors.astype(np.float32), widths=faceprint.widths.astype(np.float32))
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def load_faceprint(path: str | Path) -> Faceprint | None:
    p = Path(path).expanduser()
    if not p.exists():
        return None
    with np.load(p, allow_pickle=False) as data:
        vectors, widths = data["vectors"], data["widths"]
    if len(vectors) == 0:
        return None
    return Faceprint(np.stack([normalized(v) for v in vectors]), widths)


def head_point(body: Person, min_score: float) -> tuple[float, float] | None:
    """Where the pose model puts your face: your nose, or else the middle of your eyes and ears."""
    if body.visible(NOSE, min_score):
        return body.keypoints[NOSE][:2]
    pts = [body.keypoints[i][:2] for i in (LEFT_EYE, RIGHT_EYE, LEFT_EAR, RIGHT_EAR) if body.visible(i, min_score)]
    if not pts:
        return None
    return sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)


def on_head(box: tuple[float, float, float, float], point: tuple[float, float], margin: float = 0.25) -> bool:
    """Whether a face box (as shares of the picture) covers the head point, with some slack."""
    left, top, width, height = box
    x, y = point
    return (
        left - margin * width <= x <= left + (1 + margin) * width
        and top - margin * height <= y <= top + (1 + margin) * height
    )


def fits_body(box: tuple[float, float, float, float], body: Person, min_score: float, usual_width: float | None) -> bool:
    """The face has to sit on a seated body at your normal distance from the camera.
    A photo held up close is too big for the shoulders, and one held away is too small."""
    point = head_point(body, min_score)
    if point is None or not on_head(box, point):
        return False
    if not (body.visible(LEFT_SHOULDER, min_score) and body.visible(RIGHT_SHOULDER, min_score)):
        return False
    shoulders = abs(body.keypoints[LEFT_SHOULDER][0] - body.keypoints[RIGHT_SHOULDER][0])
    width = box[2]
    if shoulders <= 0 or not FACE_TO_SHOULDERS[0] <= width / shoulders <= FACE_TO_SHOULDERS[1]:
        return False
    if usual_width:
        return FACE_TO_USUAL[0] <= width / usual_width <= FACE_TO_USUAL[1]
    return True


def square_crop(image: np.ndarray, box: tuple[float, float, float, float], scale: float) -> np.ndarray:
    """A square around the face, `scale` times its longer side, mirrored at the picture's edges."""
    import cv2

    x, y, w, h = box
    size = max(int(max(w, h) * scale), 1)
    left = int(x + w / 2 - size / 2)
    top = int(y + h / 2 - size / 2)
    ih, iw = image.shape[:2]
    pad = [max(0, -top), max(0, top + size - ih), max(0, -left), max(0, left + size - iw)]
    part = image[max(0, top) : min(ih, top + size), max(0, left) : min(iw, left + size)]
    if part.size == 0:
        return np.zeros((size, size, 3), dtype=image.dtype)
    out = cv2.copyMakeBorder(part, *pad, cv2.BORDER_REFLECT_101)
    return out[:size, :size]


class FaceEngine:
    """The three face models. Pictures are BGR (OpenCV's usual colour order)."""

    def __init__(self, models_dir: str):
        try:
            import cv2
        except ImportError as e:
            raise FaceModelsMissing("OpenCV isn't installed (sudo apt install python3-opencv).") from e
        version = tuple(int(p) for p in cv2.__version__.split(".")[:2])
        if version < MIN_OPENCV or not hasattr(cv2, "FaceDetectorYN"):
            raise FaceModelsMissing(
                f"this Pi's OpenCV ({cv2.__version__}) is too old for the face models. "
                "Face unlock needs Raspberry Pi OS Trixie or newer."
            )
        folder = Path(models_dir)
        missing = [name for name in MODEL_FILES if not (folder / name).exists()]
        if missing:
            raise FaceModelsMissing(
                f"the face models aren't downloaded yet ({', '.join(missing)}). "
                "Connect the Pi to the internet and run desk-face again."
            )
        self.cv2 = cv2
        self.detector = cv2.FaceDetectorYN.create(str(folder / DETECTOR), "", (640, 480), 0.8, 0.3, 20)
        self.recognizer = cv2.FaceRecognizerSF.create(str(folder / RECOGNIZER), "")
        self.real_net = cv2.dnn.readNetFromONNX(str(folder / REAL_FACE))

    def find(self, image: np.ndarray) -> list[Face]:
        h, w = image.shape[:2]
        self.detector.setInputSize((w, h))
        _, rows = self.detector.detect(image)
        if rows is None:
            return []
        faces = []
        for row in rows:
            x, y, fw, fh = (float(v) for v in row[:4])
            if min(fw, fh) >= MIN_FACE_PIXELS:
                faces.append(Face((x, y, fw, fh), float(row[14]), row))
        return faces

    def describe(self, image: np.ndarray, face: Face) -> np.ndarray:
        aligned = self.recognizer.alignCrop(image, face.row)
        return normalized(self.recognizer.feature(aligned))

    def real_score(self, image: np.ndarray, face: Face) -> float:
        """Above 0: looks like a real face. Below 0: looks like a photo or a screen."""
        cv2 = self.cv2
        crop = square_crop(image, face.box, 1.5)
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        crop = cv2.resize(crop, (128, 128), interpolation=cv2.INTER_AREA if crop.shape[0] > 128 else cv2.INTER_LINEAR)
        blob = crop.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        self.real_net.setInput(blob)
        real, fake = self.real_net.forward()[0][:2]
        return float(real - fake)

    def brightness(self, image: np.ndarray, face: Face) -> float:
        x, y, w, h = (int(v) for v in face.box)
        part = image[max(0, y) : y + h, max(0, x) : x + w]
        return float(part.mean()) if part.size else 0.0


def look(
    engine: FaceEngine,
    image: np.ndarray,
    body: Person,
    min_score: float,
    faceprint: Faceprint | None,
    describe_only_if_fits: bool = True,
) -> tuple[FaceCheck, np.ndarray | None]:
    """Finds the face on the main body, and says how alike it is to yours and whether it looks real.
    Also returns its 128 numbers, for enrolling."""
    h, w = image.shape[:2]
    point = head_point(body, min_score)
    if point is None:
        return FaceCheck(found=False), None
    candidates = []
    for face in engine.find(image):
        x, y, fw, fh = face.box
        box = (x / w, y / h, fw / w, fh / h)
        if on_head(box, point):
            candidates.append((fw * fh, face, box))
    if not candidates:
        return FaceCheck(found=False), None
    _, face, box = max(candidates, key=lambda c: c[0])
    usual = faceprint.usual_width if faceprint is not None else None
    check = FaceCheck(found=True, fits_body=fits_body(box, body, min_score, usual), width=box[2])
    check.brightness = engine.brightness(image, face)
    check.too_dark = check.brightness < MIN_FACE_BRIGHTNESS
    if describe_only_if_fits and (not check.fits_body or check.too_dark):
        return check, None
    vector = engine.describe(image, face)
    if faceprint is not None:
        check.similarity = similarity(vector, faceprint)
    check.real_score = engine.real_score(image, face)
    return check, vector
