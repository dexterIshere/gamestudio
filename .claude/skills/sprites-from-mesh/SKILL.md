---
name: sprites-from-mesh
description: Render an animated 3D mesh into 2D sprite sheets — one per animation and direction — choosing the render style, the elevation and the palette.
---

# From 3D mesh to 2D sprites

This is the path for a 2D game (in the manner of Factorio or Project Zomboid):
the studio has no 2D rig, it renders the 3D. A rigged and animated mesh
becomes one sheet per animation and direction, plus a JSON atlas. Everything is
local (headless Blender): **free**, no network call.

## Choosing the render style

`sprite_styles()` gives the current list and what sets each apart. The three
uses:

- `normal` — flat light, exact texture colours. The fastest, and the right
  choice when the sprite must stay faithful to the mesh.
- `prerender` — three-point lighting, shadows and occlusion baked into the
  sprite. The prettiest, and the right choice for an RPG look.
- `pixel` — no anti-aliasing, a single palette of `palette` colours. For pixel
  art.

## Choosing the view and the animations

`elevation`: 0 for a side view (platformer), 30–45 for RPG-style isometric, 90
for top-down. `directions`: 8 is the usual, 1 renders a single front view.

`animations` picks which to render; left out, all of them, in file order
(`character_detail` → `rig3d.animations`). Sheets carry the name Godot gives
the animation: `walk_loop` becomes `walk`, marked as looping, and its last
frame — which repeats the first — is not rendered twice. `max_frames`
subsamples each animation evenly: the right tool to fit a sprite budget (60
captured frames × 8 directions make 480 images, 400 of which are invisible in
game).

A mesh without animation gives a turnaround of one frame per direction: what
you want for an object or a prop. A bare mesh that must move first gets its
rig and animations from an agent (skill `animation`).

## Running

`render_sprites(mesh_asset_id, animations=…, style=…, elevation=…,
directions=…, size=…, palette=…, max_frames=…, recipe_path=…, name=…)`. The
`mesh_asset_id` comes from `character_detail(project, character_id)` →
`rig3d.mesh_asset_id`.

The sheets are attached to the entity the mesh belongs to
(`character.spritesheets`, key `<animation>_<direction>`), and stored in
`3d/<entity>/sprites/`: the library only writes what a project claims.

## The two invariants

Cropping and palette are computed **once for all animations and all
directions**. A palette per frame would make colours jump from one angle to the
next, exactly as a crop per frame — or per animation — makes the character hop
when it goes from idle to walk. If colours change between two directions, the
bug is there — not in the render.

Done when: every sheet shows the same entity, at the same scale, with the same
colours; each animation stays centred without hopping, from frame to frame and
from animation to animation; a cycle loops without a jolt. This is judged by
eye: `view_asset` on the sheets, one by one.

Example: a 3D knight for an isometric RPG, delivered with `idle_loop`,
`walk_loop` and `attack`. `style: prerender`, `elevation: 40`,
`directions: 8`, `size: 128`, `max_frames: 8` — 24 sheets. Before delivering,
check that the eight views agree and that the attack does not shift the pivot.

Dependencies: a mesh in the library, Blender on the PATH. See `animation`,
`roster`.
