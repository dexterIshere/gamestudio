"""Workbench prompts: what the model is asked for to get a reference.

An entity that will be meshed then rigged does not come from "a knight". It
needs an image in a **known pose**, framed head to feet, on a flat background,
with no hidden limb -- a production constraint, not a matter of taste. These
phrases are therefore code: written once, named, and reused from any
interface.

Four families, in the order they are used:

- **reference**: `apose`, `tpose`, `profile` -- the image that feeds the 3D
  mesh. This is what `create_entity` asks the model for.
- **sheet**: `accessories` -- what the entity wears, one element per cell on a
  flat background, to split with `inspect_sheet` / `import_sheet`: each element
  becomes the reference of its own entity.
- **volumes**: `multiview`, `turnaround` -- several angles of the same
  character, for the 3D mesh (a single view says nothing of the back) or for a
  full turn.
- **icons**: `icon`, `icon-set` -- a single icon, or a whole set on a sheet to
  split; the forge (`service/forge.py`) mattes them and scales them to their
  family.

The guardrails (`GUARDRAILS`) prevent the classic failures: a character cut at
the ankles, two characters in one image, a cast shadow stuck to the
silhouette. The whole studio shares them: the same requirement holds for a
generated reference and for a style exploration -- one definition, or the two
drift apart.

The `character-sheet-pipeline` prompts of mr-mak-workspace follow the same
intent for another workshop (its vendored skill is `.claude/skills/img2threejs`,
for local 3D); those here are written for this studio's chain: pose imposed by
ControlNet, transparent background, 3D mesh.
"""

from __future__ import annotations

from typing import Any

from .errors import NotFound

# Prompts shared by both generators (posed reference, exploration). What breaks
# a mesh: framing too tight, a ground shadow (a base welded to the feet), a
# backdrop behind the character, dramatic lighting.
GUARDRAILS = (
    "full body, entire character visible from head to feet, "
    "centered, symmetrical, neutral lighting, no shadow on the ground, "
    "plain background, no props behind the character"
)

# What is explicitly refused: failures actually seen, not a list of principles.
GUARDRAILS_NEGATIVE = (
    "cropped, cut off, out of frame, multiple characters, text, watermark, "
    "signature, dramatic lighting, motion blur, depth of field"
)

# A sheet gets split: one element per cell, never two touching.
GUARDRAILS_SHEET = (
    "flat solid background, each element fully inside its own area, "
    "elements not touching each other, no labels, no numbers, no text, "
    "even spacing, same scale for every element, straight-on view"
)

# An icon reads at 24 px: one object, a bold silhouette, nothing around it. The
# flat background is removed locally; a frame or a cast shadow would remain.
GUARDRAILS_ICON = (
    "one single object, centered, filling most of the image, bold readable "
    "silhouette, flat solid light gray background, no frame, no border, no "
    "text, no letters, no ground shadow, straight-on view"
)
GUARDRAILS_ICON_NEGATIVE = (
    "multiple objects, scene, landscape, frame, border, text, watermark, "
    "signature, cropped, cut off, blurry"
)

# Several angles: the back and the profiles must really differ.
GUARDRAILS_ANGLES = (
    "the same character, identical proportions, identical colors and details "
    "on every view, flat solid background, full body, no perspective distortion"
)

# Recommended formats. Portrait is the studio default: a standing character
# fits in 2:3, and square is for sheets and objects.
PORTRAIT = (768, 1152)
SQUARE = (1024, 1024)
LANDSCAPE = (1152, 768)


def _prompt(identifier: str, label: str, what: str, *, positive: str,
            negative: str = GUARDRAILS_NEGATIVE, guards: str = GUARDRAILS,
            size: tuple[int, int] = PORTRAIT, pose: str = "",
            transparent: bool = True, uses: str = "") -> dict[str, Any]:
    return {
        "id": identifier,
        "label": label,
        "what": what,
        "positive": positive,
        "negative": negative,
        "guards": guards,
        "width": size[0],
        "height": size[1],
        "pose": pose,
        "transparent": transparent,
        "uses": uses,
    }


# The catalogue follows the order of use: the reference first, then the sheets,
# the angles last.
CATALOGUE: tuple[dict[str, Any], ...] = (
    _prompt(
        "apose",
        "A-pose reference",
        "The studio's reference image: it feeds the 3D mesh. Pose imposed by "
        "ControlNet, transparent background.",
        positive="A-pose with arms at 45 degrees, arms and legs clearly "
                 "separated from the body, facing the viewer, feet flat on the "
                 "ground",
        pose="a_pose",
        uses="create_entity, generate_image(pose=\"a_pose\")",
    ),
    _prompt(
        "tpose",
        "T-pose reference",
        "The same, arms horizontal: useful when a 3D model explicitly asks for "
        "it.",
        positive="T-pose with arms straight out horizontally, feet together, "
                 "facing the viewer",
        pose="t_pose",
        uses="create_entity, generate_image(pose=\"t_pose\")",
    ),
    _prompt(
        "profile",
        "Side reference",
        "The character seen from the side: what the front view does not tell "
        "(belly, muzzle, thickness of the back). Serves as a 3D model's second "
        "view.",
        positive="seen from the side, exact profile view, arms at 45 degrees, "
                 "both feet visible, facing right",
        pose="side_pose",
        size=PORTRAIT,
        uses="generate_image(pose=\"side_pose\"), secondary reference of the 3D mesh",
    ),
    _prompt(
        "accessories",
        "Accessory sheet",
        "What the character wears or holds: weapon, cape, bag, hat. One "
        "accessory per cell, same viewpoint as the reference; each one, once "
        "split, becomes the reference of its own 3D entity.",
        positive="character equipment and accessories, one item per cell, "
                 "shown from the same angle as the reference: weapon, cape, "
                 "bag, hat, belt",
        guards=GUARDRAILS_SHEET,
        size=SQUARE,
        transparent=False,
        uses="inspect_sheet -> import_sheet, then create_entity per element",
    ),
    _prompt(
        "icon",
        "Game icon",
        "A single icon, on a flat light background: the forge mattes it locally "
        "and scales it to its family. With an icon of the family as reference, "
        "it takes on that rendering.",
        positive="game icon, polished game UI asset",
        negative=GUARDRAILS_ICON_NEGATIVE,
        guards=GUARDRAILS_ICON,
        size=SQUARE,
        transparent=False,
        uses="forge_request(mode=\"one\"|\"redo\")",
    ),
    _prompt(
        "icon-set",
        "Icon set",
        "Several icons of one family on a sheet, one per cell of a strict grid: "
        "the forge splits it (imposed grid, local matting) and names each icon "
        "in reading order.",
        positive="game icon set sheet, a strict grid of separate game icons, one "
                 "icon per cell, every icon in the same rendering style",
        negative=GUARDRAILS_ICON_NEGATIVE.replace("multiple objects, ", ""),
        guards=GUARDRAILS_SHEET,
        size=SQUARE,
        transparent=False,
        uses="forge_request(mode=\"set\") -> forge_split -> forge_adopt",
    ),
    _prompt(
        "multiview",
        "Multi-view sheet",
        "The same character from several angles, in a single image: what an "
        "image-to-3D model expects to infer the back and the thickness.",
        positive="character turnaround sheet, four views of the same character "
                 "side by side in this order: front, three-quarter, side, back",
        guards=GUARDRAILS_ANGLES,
        size=LANDSCAPE,
        transparent=False,
        uses="generate_image with a reference + strength, then 3D mesh",
    ),
    _prompt(
        "turnaround",
        "Turnaround",
        "Twelve views spread around the character, for a full turn or to check "
        "a silhouette from every angle.",
        positive="character turnaround, twelve evenly spaced views around the "
                 "character in a single row: front, then rotating clockwise "
                 "back to front",
        guards=GUARDRAILS_ANGLES,
        size=LANDSCAPE,
        transparent=False,
        uses="generate_image, or a reference video (video_frames)",
    ),
)

_BY_ID = {entry["id"]: entry for entry in CATALOGUE}


def catalogue() -> list[dict[str, Any]]:
    """The workbench prompts, with what they are for and where they are used."""
    return [dict(entry) for entry in CATALOGUE]


def exists(identifier: str) -> bool:
    return identifier in _BY_ID


def render(identifier: str, subject: str, *, style_prefix: str = "",
           style_negative: str = "") -> dict[str, Any]:
    """Compose a workbench prompt for a given subject.

    The final prompt is: the project's style prefix, the subject, the workbench
    phrase, then the guardrails. The same order as `StylePack.compose_prompt`,
    so a workbench prompt behaves like any other in the chain.
    """
    entry = _BY_ID.get(identifier)
    if entry is None:
        raise NotFound(f"unknown workbench prompt: {identifier} (known: {', '.join(_BY_ID)})")
    if not subject.strip():
        raise NotFound("empty subject: a workbench prompt describes someone")

    parts = [style_prefix, subject, entry["positive"], entry["guards"]]
    negative = ", ".join(
        part for part in (style_negative, entry["negative"]) if part)
    return {
        "id": entry["id"],
        "label": entry["label"],
        "subject": subject.strip(),
        "positive": ", ".join(part.strip() for part in parts if part and part.strip()),
        "negative": negative,
        "width": entry["width"],
        "height": entry["height"],
        "pose": entry["pose"],
        "transparent": entry["transparent"],
        "uses": entry["uses"],
        "what": entry["what"],
    }
