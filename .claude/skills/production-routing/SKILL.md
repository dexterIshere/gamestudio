---
name: production-routing
description: Pick the studio procedure that fits the deliverable — art direction, roster, reference and sheets, rig and animations, sheet splitting, sprites from a mesh, external image, survey of a section, effects, visual review, library, validation.
---

# Routing production

Start from the deliverable, not the tool. The studio does not produce "an
image": it produces a rigged and animated 3D entity, its sprites, or split
icons. State in one sentence what must exist at the end, then take the row.

| Deliverable | Skill |
| --- | --- |
| A locked art direction, reusable across a whole project | `art-direction` |
| A batch of entities described in a recipe | `roster` |
| A posed reference, its accessories, its multiple views | `character-sheet` |
| An entity's rig and animations — skeleton or parts (the `anim-<entity>.md` brief) | `animation` |
| One file per element from an SVG or PNG sheet | `sheet-split` |
| A game icon created or redone, or a whole icon set, all the way into the game folder | the forge: `forge_families` → `forge_request` (paid) → `forge_split` (a set) → `forge_adopt`; the procedure is in the `showcase_brief` brief |
| 2D sprites rendered from a 3D mesh | `sprites-from-mesh` |
| A 3D mesh built from an image without spending anything | `local-3d` |
| An image made elsewhere, treated as a concept | `external-image` |
| A studio section (interface, icons, props, mechanics, art direction, VFX, world) brought back in line with what the game already contains — "update the … section" | `game-survey` |
| A mesh retouched in Blender, or the Godot project opened and run | `blender-godot-bridge` |
| A visual effect: spell, fire, smoke, water, lightning, plasma, impact, explosion (animated texture → Godot) | `vfx` |
| Proof in Godot that a visual change shows and behaves | `visual-review` |
| Find, rename or move a produced asset (deleting is the user's) | `library` |
| Check that nothing is broken, without spending a cent | `validation` |

Three rules apply everywhere:

**Spending.** Every operation that calls Runware or Tripo is paid and requires
`confirm=true`. It never runs without the user's explicit agreement in the
conversation, and not a cent beyond what was agreed. Rig and animation
(Blender), sprite rendering, mesh inventory, export, sheet splitting,
validation and **the whole local 3D path** (`local-3d`, `img2threejs`) are
local and free: iterate on them freely. Before paying for a mesh, check
`mesh_providers`: the free path exists.

**Proof.** An operation that returns has proved nothing. Look at the image with
`view_asset`, or validate the scene with headless Godot. A `done` job means the
API answered, not that the result is usable. "The tests pass" and "the result
is good" are two different claims: never present the first as the second.

**Retry budget.** A failing criterion is retried within the task's budget. A
repeated failure is a decision for the user, not a reason to keep paying in a
loop.

Examples: "twenty villagers in this style" needs `art-direction`, then
`roster`. "This mill must turn" needs `animation`. "I have an SVG icon pack"
needs `sheet-split`. "The mesh is too heavy for the game" is fixed in Blender
(`blender-godot-bridge`: decimate, then re-import), or by regenerating it with
a lower `face_limit` — which pays for the mesh again.

Dependencies: none. Reading this file connects to nothing and pays for nothing.
