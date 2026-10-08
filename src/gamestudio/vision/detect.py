"""What is read from an image, locally: its pose, and its cut-out silhouette.

A concept's pose is *imposed* at generation; estimating it gives the keypoints
it actually holds -- to compare with the template before paying for its 3D --
and those of an image from elsewhere. Matting gives a concept, a sheet or an
imported image the transparent background that 3D and the game expect.

- Pose: RTMPose/DWPose through `rtmlib`, the open-source state of the art in 2D
  estimation. Runs locally (onnxruntime); the models are downloaded on first
  use, then cached.
- Matting: `rembg` with the BiRefNet model (MIT), locally as well.

Both dependencies are optional (`pip install -e .[rigtools]`): without them,
each function raises `ToolUnavailable` with the command to run, and the rest
of the studio works normally.
"""

from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image

# Reordering COCO-17 (rtmlib) -> COCO-18 (OpenPose, the studio's convention).
# COCO-17: 0 nose, 1 eye.L, 2 eye.R, 3 ear.L, 4 ear.R, 5 shoulder.L,
# 6 shoulder.R, 7 elbow.L, 8 elbow.R, 9 wrist.L, 10 wrist.R, 11 hip.L,
# 12 hip.R, 13 knee.L, 14 knee.R, 15 ankle.L, 16 ankle.R.
# COCO-18 inserts the neck (missing from COCO-17) at position 1: it is interpolated.
_COCO17_TO_18 = (0, None, 6, 8, 10, 5, 7, 9, 12, 14, 16, 11, 13, 15, 2, 1, 4, 3)


class ToolUnavailable(RuntimeError):
    """An optional local tool is not installed."""


def available() -> dict[str, bool]:
    """Which local tools are installed, without initializing them."""
    import importlib.util

    return {
        "rtmlib": importlib.util.find_spec("rtmlib") is not None,
        "rembg": importlib.util.find_spec("rembg") is not None,
    }


@lru_cache(maxsize=1)
def _body_estimator():
    try:
        from rtmlib import Body
    except ImportError as exc:
        raise ToolUnavailable(
            "rtmlib missing: pip install -e .[rigtools] (local RTMPose/DWPose pose estimation)"
        ) from exc
    # `balanced`: RTMPose-m, the reference trade-off between accuracy and time.
    # CPU: a single image, not a video stream -- no need to require CUDA.
    return Body(mode="balanced", backend="onnxruntime", device="cpu")


def detect_keypoints(image: Image.Image) -> tuple[list[tuple[float, float]], list[float]]:
    """Estimate the 18 OpenPose keypoints of a character in an image.

    Returns (points, scores) in the studio's COCO-18 convention, in pixels. An
    undetected keypoint is (0, 0), OpenPose's convention for absence.
    """
    import numpy as np

    estimator = _body_estimator()
    rgb = np.array(image.convert("RGB"))
    keypoints, scores = estimator(rgb[:, :, ::-1])  # rtmlib expects BGR
    if len(keypoints) == 0:
        raise RuntimeError("no character detected in the image")

    # Several people detected: take the largest (a game asset image holds one
    # main subject, the rest is noise).
    def extent(points) -> float:
        xs, ys = points[:, 0], points[:, 1]
        return float((xs.max() - xs.min()) * (ys.max() - ys.min()))

    best = max(range(len(keypoints)), key=lambda i: extent(keypoints[i]))
    points17 = keypoints[best]
    scores17 = scores[best]

    points18: list[tuple[float, float]] = []
    scores18: list[float] = []
    for index in _COCO17_TO_18:
        if index is None:  # the neck: midpoint of the shoulders
            neck = ((points17[5][0] + points17[6][0]) / 2.0,
                    (points17[5][1] + points17[6][1]) / 2.0)
            points18.append(neck)
            scores18.append(float(min(scores17[5], scores17[6])))
        else:
            score = float(scores17[index])
            if score < 0.3:
                points18.append((0.0, 0.0))
            else:
                points18.append((float(points17[index][0]), float(points17[index][1])))
            scores18.append(score)
    return points18, scores18


@lru_cache(maxsize=1)
def _matting_session():
    try:
        from rembg import new_session
    except ImportError as exc:
        raise ToolUnavailable(
            "rembg missing: pip install -e .[rigtools] (local cut-out) -- or use the pipeline's "
            "Runware cut-out"
        ) from exc
    # `birefnet-general`: the same model as the pipeline's Runware cut-out
    # (runware:112@x), hence identical silhouettes locally and remotely. rembg's
    # default (u2net, 2020) cuts out noticeably worse.
    return new_session("birefnet-general")


def remove_background(image: Image.Image) -> Image.Image:
    """Cut out an image locally: return an RGBA with a transparent background."""
    session = _matting_session()
    from rembg import remove

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    result = remove(buffer.getvalue(), session=session)
    return Image.open(io.BytesIO(result)).convert("RGBA")

