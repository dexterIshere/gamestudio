---
name: character-sheet
description: Get a reference the 3D step can use — A-pose, isolated accessories, multiple views — with the studio's workbench prompts, then bring a sheet in by splitting it.
---

# From description to reference

An entity that will be meshed and then rigged is not obtained with "a knight".
What matters is not the prettiest image but the one **the next steps can
read**: known pose, full framing, flat background, no hidden limb. This goes
from description to reference, in this order.

The prompt texts live in the studio (`gamestudio prompts`, tools
`studio_prompts` and `render_prompt`), not in this file: they are code, written
once, shared with style exploration.

## 1. The reference: the pose, not the portrait

```
render_prompt("apose", "<subject>", style_prefix=<recipe prefix>)
```

Gives the prompt, the negative, the size (768×1152), the pose (`a_pose`) and
the transparent background. It is **exactly** what `create_entity` asks the
model for: if you go through it, there is nothing to do; if you generate
separately, keep the imposed pose (`generate_image(pose="a_pose")`). The pose
gives the mesh limbs clear of the body, and the rigging agent readable armpits
and crotch: arms against the torso are meshed fused.

`tpose` if a model asks for it, `profile` for a second view.
`detect_pose(asset_id)` checks, for free, that the image holds the pose before
its 3D is paid for.

**Done here**: the image is full-length, whole, on a transparent background,
and you have **looked at it** (`view_asset`). A reference cut at the ankles
cannot be fixed at the next step.

## 2. Accessories: one per cell

When the entity wears or holds an object that must live separately — a weapon
to attach to the hand, a bag, a hat that changes in game — the `accessories`
sheet isolates them, one per cell. Each split element becomes the reference of
its own 3D entity.

It is generated on a **flat background** (`transparent=false` in the prompt
catalogue): that is what splitting reads best, and inspection tells right away.

Then, in order and without spending:

```
inspect_sheet(path)                     → free preview: is the count right?
import_sheet(path, multi=true, project=…, keep=[…])
```

If the count is wrong: `gap` to rejoin or separate, `rows`/`columns` for a grid
whose cells touch. **A sheet with a wrong count is not split**: fix the
parameter, not the result.

## 3. Angles: what the front view does not say

`multiview` (four views in one image) or `turnaround` (twelve) show the back
and the profiles the front view hides. **They do not feed the mesh**: the
studio's 3D reads one view only, the chosen concept. They serve to judge the
entity from every angle before paying for its 3D, and as a reference for the
agent who retouches or rigs the mesh in Blender. They are generated **with the
reference as input** (`reference_asset_id` + low `strength`): without it, the
views are not the same entity.

For a fully local, free mesh, see the `local-3d` skill.

## Done when

- The reference is full-length, matted, in its pose, and **looked at**.
- Each sheet was **inspected** before import, and the element count is the
  expected one.
- The imported elements are in the project library (`icons/<sheet>/`), so
  another agent can find them.

## What cannot be fixed later

- A cropped entity, or two entities in one image: redo it.
- A sheet whose elements touch: splitting will make them one piece.
- A cast shadow attached to the silhouette: it is matted with the entity, and
  the mesh inherits it — a base welded under the feet.
