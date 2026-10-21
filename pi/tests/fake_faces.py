"""A pretend face engine, so the face unlock logic can be tested without OpenCV or a camera."""

import numpy as np

from desk_presence.faces import Face

W, H = 640, 480
YOU = np.eye(128, dtype=np.float32)[0]
SOMEONE_ELSE = np.eye(128, dtype=np.float32)[1]


def face_box(x=0.5, nose_y=0.4, width=0.12):
    """A face (as shares of the picture) centred on the nose of helpers.person()."""
    return (x - width / 2, nose_y - width * 0.6, width, width * 1.3)


class FakeEngine:
    """Shows whatever the test puts in `faces`: a list of (box as shares, vector, real score)."""

    def __init__(self):
        self.faces = [(face_box(), YOU, 3.0)]
        self.bright = 120.0
        self.looks = 0

    def _entry(self, face):
        return self.faces[int(face.row[0])]

    def find(self, image):
        self.looks += 1
        out = []
        for i, (box, _, _) in enumerate(self.faces):
            x, y, w, h = box
            out.append(Face((x * W, y * H, w * W, h * H), 0.95, np.array([i], dtype=np.float32)))
        return out

    def describe(self, image, face):
        return self._entry(face)[1]

    def real_score(self, image, face):
        return self._entry(face)[2]

    def brightness(self, image, face):
        return self.bright


IMAGE = np.zeros((H, W, 3), dtype=np.uint8)
