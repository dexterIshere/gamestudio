"""Skeleton convention: the bone names the studio, Blender and Godot share.

The agent rigging a mesh whose body fits (biped, quadruped, winged biped)
names its bones this way: one name means the same thing in Blender, in Godot
and in the studio's surveys. `normalize` tells how many bones of a delivered
rig carry a canonical name -- a measure reported at delivery, not a condition
for accepting it.

Names follow the Blender convention (.L / .R) because that is what Rigify and
the glTF exporter expect. Godot keeps these names as they are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Archetype(StrEnum):
    """Supported skeleton families. Selects the mapping table used."""

    BIPED = "biped"
    QUADRUPED = "quadruped"
    WINGED_BIPED = "winged_biped"
    SERPENTINE = "serpentine"
    PROP = "prop"  # inanimate object: a single root bone, no deforming rig
    CUSTOM = "custom"


@dataclass(frozen=True)
class BoneSpec:
    """A bone of the convention. `parent` is None for the root."""

    name: str
    parent: str | None
    # Bones marked `deform=False` are controllers or markers and take no part
    # in skinning.
    deform: bool = True


BIPED_BONES: tuple[BoneSpec, ...] = (
    BoneSpec("root", None, deform=False),
    BoneSpec("hips", "root"),
    BoneSpec("spine", "hips"),
    BoneSpec("chest", "spine"),
    BoneSpec("neck", "chest"),
    BoneSpec("head", "neck"),
    BoneSpec("shoulder.L", "chest"),
    BoneSpec("upper_arm.L", "shoulder.L"),
    BoneSpec("lower_arm.L", "upper_arm.L"),
    BoneSpec("hand.L", "lower_arm.L"),
    BoneSpec("shoulder.R", "chest"),
    BoneSpec("upper_arm.R", "shoulder.R"),
    BoneSpec("lower_arm.R", "upper_arm.R"),
    BoneSpec("hand.R", "lower_arm.R"),
    BoneSpec("thigh.L", "hips"),
    BoneSpec("shin.L", "thigh.L"),
    BoneSpec("foot.L", "shin.L"),
    BoneSpec("thigh.R", "hips"),
    BoneSpec("shin.R", "thigh.R"),
    BoneSpec("foot.R", "shin.R"),
)

QUADRUPED_BONES: tuple[BoneSpec, ...] = (
    BoneSpec("root", None, deform=False),
    BoneSpec("hips", "root"),
    BoneSpec("spine", "hips"),
    BoneSpec("chest", "spine"),
    BoneSpec("neck", "chest"),
    BoneSpec("head", "neck"),
    BoneSpec("tail.001", "hips"),
    BoneSpec("tail.002", "tail.001"),
    BoneSpec("upper_arm.L", "chest"),
    BoneSpec("lower_arm.L", "upper_arm.L"),
    BoneSpec("front_foot.L", "lower_arm.L"),
    BoneSpec("upper_arm.R", "chest"),
    BoneSpec("lower_arm.R", "upper_arm.R"),
    BoneSpec("front_foot.R", "lower_arm.R"),
    BoneSpec("thigh.L", "hips"),
    BoneSpec("shin.L", "thigh.L"),
    BoneSpec("back_foot.L", "shin.L"),
    BoneSpec("thigh.R", "hips"),
    BoneSpec("shin.R", "thigh.R"),
    BoneSpec("back_foot.R", "shin.R"),
)

WINGED_BIPED_BONES: tuple[BoneSpec, ...] = (
    *BIPED_BONES,
    BoneSpec("wing_base.L", "chest"),
    BoneSpec("wing_mid.L", "wing_base.L"),
    BoneSpec("wing_tip.L", "wing_mid.L"),
    BoneSpec("wing_base.R", "chest"),
    BoneSpec("wing_mid.R", "wing_base.R"),
    BoneSpec("wing_tip.R", "wing_mid.R"),
)

SERPENTINE_BONES: tuple[BoneSpec, ...] = (
    BoneSpec("root", None, deform=False),
    BoneSpec("head", "root"),
    *tuple(
        BoneSpec(f"body.{i:03d}", "head" if i == 1 else f"body.{i - 1:03d}")
        for i in range(1, 13)
    ),
)

PROP_BONES: tuple[BoneSpec, ...] = (BoneSpec("root", None, deform=False),)


SKELETONS: dict[Archetype, tuple[BoneSpec, ...]] = {
    Archetype.BIPED: BIPED_BONES,
    Archetype.QUADRUPED: QUADRUPED_BONES,
    Archetype.WINGED_BIPED: WINGED_BIPED_BONES,
    Archetype.SERPENTINE: SERPENTINE_BONES,
    Archetype.PROP: PROP_BONES,
    Archetype.CUSTOM: (),
}


def deform_bones(archetype: Archetype) -> list[str]:
    return [b.name for b in SKELETONS[archetype] if b.deform]


# --- Synonyms found in delivered rigs (Mixamo, Unreal, Tripo, Meshy...).
# Matching is deliberately permissive: a rig from elsewhere never follows a
# convention. What is not recognised is not counted.

_SYNONYMS: dict[str, str] = {}


def _register(canonical: str, *aliases: str) -> None:
    for alias in aliases:
        _SYNONYMS[alias.lower()] = canonical


_register("hips", "pelvis", "mixamorig:hips", "hips", "bip01_pelvis", "hip", "root_hips",
          "j_bip_c_hips")
_register("spine", "spine", "bip01_spine", "abdomen", "j_bip_c_spine")
# Mixamo convention: Hips > Spine > Spine1 > Spine2. Spine1 is therefore the chest.
_register("chest", "chest", "spine1", "spine2", "spine3", "torso", "upperchest", "bip01_spine1")
_register("spine", "spine_01")
_register("chest", "spine_02", "spine_03")
_register("neck", "neck", "neck_01", "neck_02", "bip01_neck", "j_bip_c_neck")
_register("head", "head", "bip01_head", "skull", "j_bip_c_head")

for side, tag in (("L", "left"), ("R", "right")):
    low = side.lower()
    _register(f"shoulder.{side}", f"{tag}shoulder", f"clavicle_{low}", f"{tag}_clavicle",
              f"shoulder_{low}", f"clavicle.{side.lower()}")
    _register(f"upper_arm.{side}", f"{tag}arm", f"upperarm_{low}", f"{tag}_upperarm",
              f"arm_{low}", f"upper_arm_{low}", f"upperarm.{low}")
    _register(f"lower_arm.{side}", f"{tag}forearm", f"lowerarm_{low}", f"{tag}_forearm",
              f"forearm_{low}", f"forearm.{low}", f"elbow_{low}")
    _register(f"hand.{side}", f"{tag}hand", f"hand_{low}", f"{tag}_hand", f"wrist_{low}")
    _register(f"thigh.{side}", f"{tag}upleg", f"thigh_{low}", f"{tag}_thigh",
              f"upperleg_{low}", f"upleg_{low}", f"leg_{low}")
    _register(f"shin.{side}", f"{tag}leg", f"calf_{low}", f"{tag}_calf",
              f"lowerleg_{low}", f"shin_{low}", f"knee_{low}")
    _register(f"foot.{side}", f"{tag}foot", f"foot_{low}", f"{tag}_foot", f"ankle_{low}")


def canonical_bone(name: str) -> str | None:
    """The canonical name of a bone, or None if unknown.

    The comparison ignores case, separators and common namespace prefixes
    (`mixamorig:`, `Armature|`, `DEF-`).
    """
    cleaned = name.strip()
    for prefix in ("armature|", "mixamorig:", "mixamorig", "def-", "org-", "mch-"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix):]
    lowered = cleaned.lower()
    if lowered in _SYNONYMS:
        return _SYNONYMS[lowered]
    # Already canonical?
    for names in SKELETONS.values():
        for spec in names:
            if spec.name.lower() == lowered:
                return spec.name
    # Separator variants: "upper_arm_l", "upperArm-L", "upper arm.l"
    squashed = lowered.replace("-", "_").replace(" ", "_").replace(".", "_")
    if squashed in _SYNONYMS:
        return _SYNONYMS[squashed]
    for names in SKELETONS.values():
        for spec in names:
            if spec.name.lower().replace(".", "_") == squashed:
                return spec.name
    # Last resort: a numbered bone (spine_01, thigh_001...). Strip the suffix
    # and try again, except for chains where numbering is meaningful (tail,
    # serpentine body), which are already canonical at this point.
    stripped = re.sub(r"[._-]?\d+$", "", squashed)
    if stripped and stripped != squashed and not stripped.startswith(("body", "tail")):
        return canonical_bone(stripped)
    return None


@dataclass
class NormalizationReport:
    """How much a delivered rig follows the convention: a measure, not a verdict.

    `mapping` lists the recognised bones. A rig with free names (a machine, a
    creature outside the archetypes) is still accepted: the share of canonical
    names is reported at delivery, nothing more.
    """

    archetype: Archetype
    mapping: dict[str, str] = field(default_factory=dict)  # source name -> canonical name


def normalize(source_bones: list[str], archetype: Archetype) -> NormalizationReport:
    """Recognise, among raw bones, those that deform in the `archetype` convention.

    Each canonical bone counts once: the first source bone carrying it wins.
    """
    report = NormalizationReport(archetype=archetype)
    expected = set(deform_bones(archetype))
    for raw in source_bones:
        canon = canonical_bone(raw)
        if canon and canon in expected and canon not in report.mapping.values():
            report.mapping[raw] = canon
    return report
