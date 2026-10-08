---
name: animation
description: Rig and animate an entity in Blender — a skeleton for a creature, parts for a building or a machine — then hand it back to the studio and prove every animation reads. The procedure for the agent that receives an `anim-<entity>.md` brief.
---

# Rigging and animating an entity

The studio rigs nothing and animates nothing: no rigging tool is coded in it.
An entity that went to 3D comes out with a **bare mesh**; its rig and
animations are an agent's work, in Blender, from its card. The studio takes
over at delivery: it takes the inventory of the GLB, keeps the bare mesh,
renders the sprites, copies into Godot.

The starting point is the brief (`.gamestudio/workspace/briefs/anim-<entity>.md`,
written by "Hand to an agent" or `entity_animation_brief`): it copies the card,
cites the bare mesh through the library, and gives the working folder. This
skill gives the order, the conventions and the finish line.

**Never animation by diffusion.** A generated animation drifts from one image
to the next and does not loop: keyframes, or procedural code (a script that
computes the keys), nothing else.

## 1. Read before touching

- The whole card: what the entity is, its role in the game, the animations it
  asks for. **The card is authoritative**; where it is silent, choose what
  serves its role, and say so in the report.
- The chosen concept (`view_asset`) and the mesh from at least two angles:
  `render_sprites(mesh_asset_id, directions=4)` renders a free turnaround.
- A redo starts from the **bare mesh** (`<entity>-bare.glb`), never from the
  previous rig.

## 2. Choose the structure

| The entity | The structure |
| --- | --- |
| A creature whose body fits (biped, quadruped, winged biped) | A skeleton with canonical names (`src/gamestudio/domain/skeleton.py`: `hips`, `upper_arm.L`…) |
| A creature outside the archetypes (snake, swarm, tentacles) | A skeleton with plainly named bones |
| A machine, a building, a vehicle | Parts: each moving part is an object, parented to a pivot placed on its axis (a wheel's axle, a door's hinge) |
| Both (a mill and its miller) | Two structures, kept apart |

A single-piece mesh is segmented in Blender: by islands, by materials, or by
hand. If it does not segment cleanly, **tell the user**: generating in parts
(Tripo `generateParts`) is paid, and comes out untextured.

Canonical names are a convention, not a requirement: the studio measures the
share of bones that follow it, it does not refuse a rig that departs from it.
But a canonical name means the same thing in Blender, in Godot and in the
studio's inventories — that is what lets an effect attach to `hand.R` without
opening the file.

## 3. Rig

- In the user's Blender (MCP server `blender`) if a session is open — they see
  the work; otherwise `blender --background --python <script>`. The `.blend`
  and the scripts stay in the brief's working folder, never in
  `.gamestudio/library/`.
- A skeleton: armature, weights, then **test the deformations at extreme
  poses** (shoulders, elbows, knees, neck) before animating. A pinching elbow
  cannot be fixed in animation.
- Parts: transforms applied, origins on the axes, no part passing through
  another at rest.

## 4. Animate

- One action per animation, named in English `snake_case`. A silent card gets
  the minimum for its role: a creature `idle_loop` and `walk_loop`, a machine
  its activity cycle, and `build` if it is built in game.
- **A cycle is named `<name>_loop`** and loops exactly: its last key repeats
  the first pose (a wheel from 0° to 360°, a walk that lands back on the same
  foot). Godot loops it on import and calls it `<name>`; sprite rendering drops
  that last frame, which would duplicate the first.
- A planted foot does not slide, a strike has its anticipation and its stop, a
  heavy part accelerates and brakes.
- **Note the events** as you go: the frame of a strike, a footfall, an impact.
  That is where effects and gameplay will hook in, and nobody will guess them
  afterwards.

## 5. Export and deliver

```python
bpy.ops.export_scene.gltf(filepath="<working folder>/<entity>.glb",
    export_format="GLB", export_yup=True,
    export_animation_mode="NLA_TRACKS",   # one NLA track per action, named after it
    export_anim_slide_to_zero=True)       # each animation starts at 0 s
```

Without `export_anim_slide_to_zero`, an animation keyed from frame 1 starts at
1/24 s: Godot holds the first pose one frame too long on every turn of a cycle.

Then hand it back to the studio, which replaces the entity's mesh and keeps
the bare one:

```
entity_attach_mesh(project, section, name, path=<glb>, replace=true)
gamestudio mesh import <glb> --project <p> --name <entity> --replace   # without MCP
```

The inventory it returns — bones, share of canonical names, animations and
which of them loop — is the first check: an animation missing from the
inventory is missing from the file.

## 6. Prove

Returning has proved nothing.

- `render_sprites(mesh_asset_id, max_frames=8)`: one sheet per animation and
  direction. **Look at all of them** (`view_asset`): each animation reads,
  nothing passes through, nothing tears, the character does not hop from one
  animation to the next.
- If the project folder has a `project.godot`, the GLB is copied there:
  `gamestudio validate-godot <folder> --scene res://characters/<entity>/<entity>.glb`
  must end with `RESULT: OK` — every track resolved, each cycle with
  `loop=true`.

## 7. The report

- In the card, a `## Rig and animations` section: the date; the structure
  (bones or parts, and why); each animation (name, frames, fps, loop); its
  events (frame and kind); the departures from the card; the paths of the
  proofs.
- A devlog entry asking for the user's approval — the "animated model" gate:
  `create_document(project, title="Rig and animations — <entity>",
  template="devlog", folder="devlog")`, then write it: the sheets to look at,
  what was checked, what remains doubtful.

## Done when

- `character_detail` shows the bones (or parts) and every expected animation;
  the bare mesh is still there.
- Every sheet has been looked at; every cycle loops without a jolt.
- Godot validation passes, if the project has a Godot project.
- The card and the devlog are written. Approval belongs to the user.

A criterion that fails twice is reported to the user instead of looping. No
paid operation without their agreement.

Example: a water mill. Parts — the wheel on its axle, the millstone, the rest
fixed. `spin_loop`: the wheel from 0° to 360° in 48 frames at 24 fps, constant
speed; `build`: the parts rise into place one by one, the wheel last. Event:
the millstone touches the grain at frame 12 of `spin_loop`. Delivered, rendered
in 4 directions, looked at, validated in Godot.

Dependencies: Blender on the PATH, Godot 4 for validation. See
`blender-godot-bridge` (the open Blender session), `sprites-from-mesh`,
`validation`.
