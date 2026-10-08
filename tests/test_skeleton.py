"""Skeleton conventions: name matching, the measure taken on a delivery."""

from __future__ import annotations

import pytest

from gamestudio.domain.skeleton import Archetype, canonical_bone, deform_bones, normalize

MIXAMO = [
    "mixamorig:Hips", "mixamorig:Spine", "mixamorig:Spine1", "mixamorig:Neck",
    "mixamorig:Head", "mixamorig:LeftShoulder", "mixamorig:LeftArm",
    "mixamorig:LeftForeArm", "mixamorig:LeftHand", "mixamorig:RightShoulder",
    "mixamorig:RightArm", "mixamorig:RightForeArm", "mixamorig:RightHand",
    "mixamorig:LeftUpLeg", "mixamorig:LeftLeg", "mixamorig:LeftFoot",
    "mixamorig:RightUpLeg", "mixamorig:RightLeg", "mixamorig:RightFoot",
]

UNREAL = [
    "pelvis", "spine_01", "spine_03", "neck_01", "head", "clavicle_l", "upperarm_l",
    "lowerarm_l", "hand_l", "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
    "thigh_l", "calf_l", "foot_l", "thigh_r", "calf_r", "foot_r",
]


@pytest.mark.parametrize("bones,label", [(MIXAMO, "mixamo"), (UNREAL, "unreal")])
def test_known_conventions_are_fully_mapped(bones, label):
    report = normalize(bones, Archetype.BIPED)
    assert set(report.mapping.values()) == set(deform_bones(Archetype.BIPED)), \
        f"{label}: {sorted(set(deform_bones(Archetype.BIPED)) - set(report.mapping.values()))}"


def test_an_anonymous_rig_matches_nothing():
    """A rig without a convention measures as such: no canonical name recognised."""
    report = normalize([f"Bone.{i:03d}" for i in range(19)], Archetype.BIPED)
    assert report.mapping == {}


def test_canonical_bone_tolerates_variants():
    assert canonical_bone("mixamorig:LeftForeArm") == "lower_arm.L"
    assert canonical_bone("upperarm_r") == "upper_arm.R"
    assert canonical_bone("UPPER_ARM.L") == "upper_arm.L"
    assert canonical_bone("spine_01") == "spine"
    assert canonical_bone("foo_bar") is None
