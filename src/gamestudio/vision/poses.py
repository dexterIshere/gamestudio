"""OpenPose reference poses: the ones imposed when generating a concept.

A concept's pose is not *detected*, it is *imposed*: the reference skeleton
guides ControlNet, so the image comes out in the pose the next steps expect --
a clean A-pose for a mesh an agent will rig, a profile for a side view. Each
joint's position is known in advance, and the estimation (`detect.py`)
measures the one the image holds: comparing the two is up to whoever reads
them.

Format: COCO-18, the convention OpenPose ControlNets expect.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

# Names of the 18 keypoints, in COCO order.
COCO18 = (
    "nose", "neck", "r_shoulder", "r_elbow", "r_wrist", "l_shoulder", "l_elbow",
    "l_wrist", "r_hip", "r_knee", "r_ankle", "l_hip", "l_knee", "l_ankle",
    "r_eye", "l_eye", "r_ear", "l_ear",
)

# The 17 drawn segments, and their colors. These values come from OpenPose's
# reference implementation: ControlNets were trained on them, and changing
# them clearly degrades pose following.
LIMB_SEQ: tuple[tuple[int, int], ...] = (
    (1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10),
    (1, 11), (11, 12), (12, 13), (1, 0), (0, 14), (14, 16), (0, 15), (15, 17),
)

LIMB_COLORS: tuple[tuple[int, int, int], ...] = (
    (255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0),
    (85, 255, 0), (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255),
    (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255),
    (255, 0, 255), (255, 0, 170),
)

POINT_COLORS: tuple[tuple[int, int, int], ...] = (*LIMB_COLORS, (255, 0, 85))


@dataclass(frozen=True)
class PoseTemplate:
    """A reference pose in normalized [0, 1] coordinates."""

    name: str
    points: tuple[tuple[float, float], ...]
    description: str = ""

    def scaled(self, width: int, height: int) -> list[tuple[float, float]]:
        return [(x * width, y * height) for x, y in self.points]


# A-pose: arms at 45 degrees. The studio's reference pose. Image-to-3D models
# rebuild it much better than a T-pose (less self-occlusion at the armpits),
# and it leaves the agent who will rig limbs free of the body.
A_POSE = PoseTemplate(
    name="a_pose",
    description="Arms at 45 degrees, legs slightly apart. The studio default.",
    points=(
        (0.500, 0.120),  # nose
        (0.500, 0.205),  # neck
        (0.425, 0.215),  # r_shoulder
        (0.360, 0.340),  # r_elbow
        (0.305, 0.465),  # r_wrist
        (0.575, 0.215),  # l_shoulder
        (0.640, 0.340),  # l_elbow
        (0.695, 0.465),  # l_wrist
        (0.455, 0.500),  # r_hip
        (0.448, 0.685),  # r_knee
        (0.442, 0.880),  # r_ankle
        (0.545, 0.500),  # l_hip
        (0.552, 0.685),  # l_knee
        (0.558, 0.880),  # l_ankle
        (0.478, 0.104),  # r_eye
        (0.522, 0.104),  # l_eye
        (0.455, 0.114),  # r_ear
        (0.545, 0.114),  # l_ear
    ),
)

# T-pose: horizontal arms. Better arm separation, but a wider framing and a less
# reliable 3D reconstruction at the shoulders.
T_POSE = PoseTemplate(
    name="t_pose",
    description="Horizontal arms. Maximum arm separation, for a model that requires it.",
    points=(
        (0.500, 0.120), (0.500, 0.205),
        (0.425, 0.212), (0.290, 0.212), (0.155, 0.212),
        (0.575, 0.212), (0.710, 0.212), (0.845, 0.212),
        (0.455, 0.500), (0.448, 0.685), (0.442, 0.880),
        (0.545, 0.500), (0.552, 0.685), (0.558, 0.880),
        (0.478, 0.104), (0.522, 0.104), (0.455, 0.114), (0.545, 0.114),
    ),
)

# Right profile: used for turnarounds and platformer sprites, where only the
# profile shows. The limbs of the far side are deliberately close to those of
# the visible side.
SIDE_POSE = PoseTemplate(
    name="side_pose",
    description="Side view facing right.",
    points=(
        (0.560, 0.120), (0.500, 0.205),
        (0.495, 0.215), (0.505, 0.345), (0.515, 0.470),
        (0.505, 0.215), (0.520, 0.345), (0.535, 0.470),
        (0.492, 0.500), (0.490, 0.685), (0.488, 0.880),
        (0.508, 0.500), (0.512, 0.685), (0.520, 0.880),
        (0.575, 0.104), (0.560, 0.104), (0.520, 0.114), (0.512, 0.114),
    ),
)

TEMPLATES: dict[str, PoseTemplate] = {t.name: t for t in (A_POSE, T_POSE, SIDE_POSE)}


def template(name: str) -> PoseTemplate:
    if name not in TEMPLATES:
        raise KeyError(f"unknown pose: {name} (available: {', '.join(TEMPLATES)})")
    return TEMPLATES[name]


def render_openpose(
    pose: PoseTemplate | list[tuple[float, float]],
    width: int,
    height: int,
    *,
    limb_width: int | None = None,
    point_radius: int | None = None,
) -> Image.Image:
    """Produce the ControlNet guide image: black background, colored limbs.

    Colors and proportions follow OpenPose's reference rendering.
    """
    points = pose.scaled(width, height) if isinstance(pose, PoseTemplate) else list(pose)
    scale = max(width, height) / 512.0
    limb_w = limb_width if limb_width is not None else max(4, int(8 * scale))
    radius = point_radius if point_radius is not None else max(3, int(4 * scale))

    canvas = Image.new("RGB", (width, height), (0, 0, 0))

    # Limbs are drawn at 60% opacity on a separate layer, as in the reference
    # implementation (overlaps stay readable).
    limbs = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(limbs)
    for index, (a, b) in enumerate(LIMB_SEQ):
        if a >= len(points) or b >= len(points):
            continue
        x1, y1 = points[a]
        x2, y2 = points[b]
        if (x1, y1) == (0.0, 0.0) or (x2, y2) == (0.0, 0.0):
            continue  # missing keypoint
        draw.line([(x1, y1), (x2, y2)], fill=LIMB_COLORS[index % len(LIMB_COLORS)],
                  width=limb_w, joint="curve")
    canvas = Image.blend(canvas, limbs, 0.6)

    draw = ImageDraw.Draw(canvas)
    for index, (x, y) in enumerate(points):
        if (x, y) == (0.0, 0.0):
            continue
        color = POINT_COLORS[index % len(POINT_COLORS)]
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)
    return canvas
