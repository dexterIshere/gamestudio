"""Poses: the one imposed on a concept, and the one read from an image.

A concept meant for 3D is drawn in an imposed pose (A-pose, T-pose, profile):
ControlNet receives that pose's OpenPose template (`pose_template`). Once the
image exists, `detect_pose` estimates its keypoints -- locally (RTMPose), for
free. It compares nothing: both return the same 18 COCO points, and whoever
reads the measurement -- a critic, before judging by eye -- decides whether the
image holds the pose.
"""

from __future__ import annotations

from typing import Any

from .context import studio
from .errors import NotFound


def detect_pose(asset_id: str) -> dict[str, Any]:
    """Estimate the 18 OpenPose keypoints of an image, locally with RTMPose.

    A measurement, not a verdict: comparing it to the imposed template is the
    caller's job.
    """
    from PIL import Image

    from ..vision.detect import detect_keypoints
    from ..vision.poses import COCO18

    path = studio().asset_file(asset_id)
    if path is None:
        raise NotFound(f"asset {asset_id} not found")
    with Image.open(path) as image:
        points, scores = detect_keypoints(image)
    return {
        "keypoints": [{"name": COCO18[i], "x": round(x, 1), "y": round(y, 1),
                       "score": round(scores[i], 3)}
                      for i, (x, y) in enumerate(points)],
    }


def pose_templates() -> list[dict[str, str]]:
    """The reference poses a generation can be held to (ControlNet OpenPose)."""
    from ..vision.poses import TEMPLATES

    return [{"name": t.name, "description": t.description} for t in TEMPLATES.values()]


def pose_template(name: str, width: int = 768, height: int = 1152) -> dict[str, Any]:
    """The keypoints of a reference pose, scaled to the wanted image size."""
    from ..vision.poses import COCO18, template

    try:
        pose = template(name)
    except KeyError as exc:
        raise NotFound(str(exc)) from exc
    return {
        "name": pose.name,
        "description": pose.description,
        "keypoints": [{"name": COCO18[i], "x": x, "y": y}
                      for i, (x, y) in enumerate(pose.scaled(width, height))],
    }
