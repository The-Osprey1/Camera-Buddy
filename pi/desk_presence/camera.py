"""Runs the body pose model on the AI Camera's own chip and hands back Frames.

Only the model's results reach this code, plus the picture itself while face unlock
is looking for you. No pictures are saved or sent anywhere.
Needs the camera software on the Pi: `sudo apt install imx500-all python3-picamera2`.
"""

from __future__ import annotations

import time
from typing import Callable, Iterator

import numpy as np

from .pose import Frame, Person

# The size the pose model's results are reported in (height, width).
MODEL_SIZE_H_W = (480, 640)


class PoseCamera:
    def __init__(self, model_path: str, detection_threshold: float = 0.3):
        from picamera2 import Picamera2
        from picamera2.devices.imx500 import IMX500, NetworkIntrinsics
        from picamera2.devices.imx500.postprocess_highernet import postprocess_higherhrnet

        self._postprocess = postprocess_higherhrnet
        self.threshold = detection_threshold
        self.imx500 = IMX500(model_path)
        intrinsics = self.imx500.network_intrinsics or NetworkIntrinsics()
        intrinsics.task = "pose estimation"
        intrinsics.update_with_defaults()
        self.picam2 = Picamera2(self.imx500.camera_num)
        # BGR in OpenCV's terms (picamera2 calls it RGB888), at the size the pose results use.
        config = self.picam2.create_preview_configuration(
            main={"format": "RGB888", "size": (MODEL_SIZE_H_W[1], MODEL_SIZE_H_W[0])},
            controls={"FrameRate": intrinsics.inference_rate},
            buffer_count=12,
        )
        # The first start uploads the model to the camera, which can take a minute.
        self.picam2.start(config, show_preview=False)
        self.imx500.set_auto_aspect_ratio()

    def _people(self, metadata: dict) -> list[Person] | None:
        outputs = self.imx500.get_outputs(metadata=metadata, add_batch=True)
        if outputs is None:
            return None  # the model hasn't produced a result for this frame
        keypoints, scores, _boxes = self._postprocess(
            outputs=outputs,
            img_size=MODEL_SIZE_H_W,
            img_w_pad=(0, 0),
            img_h_pad=(0, 0),
            detection_threshold=self.threshold,
            network_postprocess=True,
        )
        if scores is None or len(scores) == 0:
            return []
        h, w = MODEL_SIZE_H_W
        people = []
        for person in keypoints:
            rows = np.asarray(person, dtype=float).reshape(17, 3)
            people.append(Person([(x / w, y / h, s) for x, y, s in rows.tolist()]))
        return people

    def frames(self, wants_images: Callable[[], bool] = lambda: False) -> Iterator[Frame]:
        while True:
            image = None
            if wants_images():
                request = self.picam2.capture_request()
                try:
                    metadata = request.get_metadata()
                    image = request.make_array("main")
                finally:
                    request.release()
            else:
                metadata = self.picam2.capture_metadata()
            people = self._people(metadata)
            if people is None:
                continue
            lux = metadata.get("Lux")
            yield Frame(time.monotonic(), people, float(lux) if lux is not None else None, image)

