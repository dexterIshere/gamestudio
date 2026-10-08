---
name: external-image
description: Bring in an image made elsewhere (drawing, scan, an artist's concept) and treat it as a studio concept — matting, pose measurement, chosen concept of a card, move to 3D.
---

# An image made elsewhere

A studio concept is normally generated in an imposed pose, known in advance. An
imported image has no such guarantee: **measure** it before paying for its 3D.

## The chain

1. `import_image(path, matting=…, project=<the card's project>)` — the image
   enters **the project's** store: an asset is only visible to its project.
   `matting=true` removes the background locally (BiRefNet, a few tens of
   seconds, free). Leave it `false` if the image already has a transparent
   background.
2. `view_asset` — look at it: whole, a single entity, no cast shadow.
3. `detect_pose(asset_id)` — 18 OpenPose keypoints estimated locally (RTMPose,
   free). **Needs `pip install -e .[rigtools]`**; without these tools, tell the
   user rather than judging the pose by eye.
4. `entity_choose_concept(project, section, name, asset_id)` — it becomes the
   chosen concept of its card, like a concept the studio generated.
5. `entity_realize(project, section, name, confirm=true)` — the 3D, **paid**
   (~$0.40), with the user's agreement. An opaque image is matted
   automatically. The mesh comes out bare; its rig and animations then go to an
   agent (skill `animation`).

For a free mesh from the same image, see `local-3d`.

## What degrades

An estimated pose is less reliable than an imposed one: hand and foot
keypoints are often wrong, and strong foreshortening makes the estimate
useless. Before the 3D, the measurement must answer: are the arms clear of the
torso, the legs clear of each other? Touching limbs are meshed fused, and the
rigging agent will have to separate them by hand — or will not manage to. Say
so before paying, not after.

Done when: the image is in the card's project, looked at, its pose measured,
and what the measurement says has been written to the user before any
spending.

Example: a scanned drawing of a guard, arms along the body.
`import_image(matting=true, project=…)`, `detect_pose`: the wrists touch the
hips. Say so, and offer either to generate A-pose variations from this drawing
(`entity_concepts(project, section, name, reference_asset_id=…,
pose="a_pose")`, paid), or to pay for the mesh knowing the arms will have to be
detached in Blender.

Dependencies: the local tools (`pip install -e .[rigtools]`) for pose and
matting. See `character-sheet`, `local-3d`, `sheet-split`.
