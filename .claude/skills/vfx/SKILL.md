---
name: vfx
description: Create a game visual effect — spell, fire, smoke, water, lightning, plasma, magic, impact, explosion — by writing it in the effects language, judging it frame by frame, rendering it to a sheet and delivering it to Godot; then, if needed, hooking it to an entity animation.
---

# Visual effects

A visual effect is an **animated texture**: a sheet of frames (flipbook) that
an `AnimatedSprite2D` plays, or that the particles of a `GPUParticles2D` carry.
The studio computes it locally, for free, from a **YAML spec** written in the
effects language. Nothing is preset: fire, water or a spell are just different
ways of writing layers. This skill says how to write them; the exact function
list is in `effect_reference` (`gamestudio fx ref`), generated from the code.

## 1. Decide what the effect must say

Before any code, one line each:

- **Meaning in game**: what must the player understand? ("this spell will hit
  there", "this enemy is burning", "the shield took the hit").
- **Life cycle**: *loop* (a lasting state: fire, aura, poison) or *one-shot*
  (an event: impact, explosion, cast). `loop: true` or `false`.
- **Material**: what the effect is made of — gas, liquid, solid, plasma,
  energy, light. It picks the method (see [materials](references/materials.md)).
- **Reading**: silhouette, then value (light/dark), then colour. An effect that
  only reads through colour does not read. A single accent hue, taken from the
  project's art direction (`.gamestudio/documents/`).
- **Size and rate**: the on-screen size in game sets `size` (no need to render
  512 px for an effect that fills 64); 12 to 24 fps, 8 to 32 frames.

The chosen direction and its revisions go into the effect's **concept**
(`.gamestudio/documents/design/vfx/<effect>.md`, VFX page, `read_document` /
`write_document` with `folder="design/vfx"`), not into the conversation. The
YAML spec lives next to it, in `.gamestudio/documents/effects/<effect>.yaml`
(`write_effect`).

## 2. Write the spec in layers

A readable effect is almost always **three to five layers**, back to front: a
mass (the body of the material), a detail (filaments, embers, drops), a light
(overexposed core, halo, flash). Each layer has its blend: `add` for what
emits light (fire, plasma, magic, sparks), `normal` for what blocks it (smoke,
dust, opaque liquid, debris).

Three layer types, three ways of moving matter:

| Type | What it computes | For |
| --- | --- | --- |
| `field` | one image per frame, without memory, like a shader | shapes, waves, runes, stylised flames, plasma, anything described as a function of (x, y, t) |
| `fluid` | a density carried by a velocity (stable fluids) | smoke, steam, mist, volumetric flames, ink, settling dust |
| `particles` | independent points with velocity, gravity, life | sparks, embers, drops, debris, shards, dust, bubbles |

Fixed conventions: `x, y` are centred, the short side runs from -1 to 1, **y
points up**; `t` runs from 0 to 1 over the duration; a layer named `fire` can
then be read as the variable `fire` (its alpha) — that is how smoke is born
from fire, or a glow follows the shape of a lightning bolt.

Start with the silhouette in white (`color: "#ffffff"`), tune it, and only then
set the colour ramp.

## 3. Make it loop, make it snap

- **Exact loop**: a noise that scrolls by a **whole period** of its tiling over
  the duration comes back to its start (`fbm(x*3, y*3 - t*4, ty=4)`). A noise
  that must *evolve* in a loop gets time on a tiled axis
  (`noise(x, y, t*2, tz=2)`). A rotation loops if it makes a full turn, or a
  whole number of sectors of a symmetry (`kaleido`). Particles loop on their
  own; a fluid loops by crossfade once it has settled.
- **One-shot**: timing makes the effect. A brief anticipation, the **hit** (the
  peak, 1 to 3 frames), then a long dissipation. `env(t, attack, hold,
  release)` draws that curve; `ease_out` slows an expansion, `pulse` makes a
  flash. The last frames of a one-shot effect end transparent: nothing vanishes
  at once.
- **Two impacts differ by motion first**, colour second: a light hit that is
  only a paler heavy hit remains a heavy hit. A hit received is not a hit dealt
  played backwards: rings moving inward, debris leaving the body.

## 4. Judge, tune, repeat — within a budget

`preview_effect(project, name)` (or `spec=` to try a text without saving it)
renders at half size and **shows every frame** on a dark background (additive
effect) or a checkerboard (opaque effect). Look at it, every time: an
operation that returns has proved nothing. What to look for:

1. does the silhouette read at the real game size?
2. does the last frame chain into the first (loop)? is the last one empty
   (one-shot)?
3. is an area burnt to flat white? → lower `intensity`, the ramp
   (`by: heat * 1.1` rather than `* 1.6`) or the bloom;
4. is the motion even where it should snap?

A note saying that all frames are transparent means no layer covers anything:
a zero alpha, a threshold too high, a shape out of frame.

Five to eight round trips are enough for an effect; beyond that, the direction
is missing, not a setting — go back to the user with the contact sheet instead
of going round in circles.

## 5. Render and deliver

`build_effect(project, name)` renders at full size and stores:

- **library** `<folder>/.gamestudio/library/effects/<name>/`: `<name>.png`
  (sheet), `<name>.json` (frames, rate, loop, columns, **pivot** and blend),
  `contact.png`, `spec.yaml`, `frames/0001.png…`;
- **Godot** `res://effects/<name>/<name>.tscn`: an `AnimatedSprite2D` whose
  pivot is the effect's (0, 0) point, with an additive material if the effect
  is additive; and `<name>_particles.tscn` if the spec declares
  `godot.particles` (each particle plays the sheet over its life: embers, smoke
  puffs).

**Two Godot folders, two paths.** `res://effects/<name>/` receives what the
local effects engine renders (this skill, `build_effect`): a sheet and its
scenes, rewritten at every render. `res://vfx/<name>/` receives an effect built
by hand by an agent from a VFX page concept ("Create in Godot", `vfx_brief`):
shaders, particles, scenes written in Godot. An agent following a
`vfx-<name>.md` brief works in `res://vfx/<name>/`; it may use a sheet rendered
here by instancing the scene from `res://effects/`, never by copying it.

The scenes are **validated by headless Godot** in a throwaway project:
`godot.validation` reports `ok`, a failure (with the output), or that Godot is
missing. A failure is reported, not worked around.

`trim: true` crops every frame to **one** shared box (the pivot does not jump);
a crop per frame would make the effect hop. `quantize` computes **one** palette
for all frames, for the same reason.

## 6. Hook it into the game

Read [the integration contract](references/implementation.md) before wiring the
effect. In short: the generated scene is rewritten at every render, so the game
**instances** it and never edits it; what triggers the effect, what owns it and
what frees it (end of animation, death, cancellation) live in the game's code.
A one-shot effect frees itself on `animation_finished`.

For an effect carried by an entity, the anchor is a **bone** — canonical when
the body fits (`hand.R`, `foot.L`, `chest`…) — or a **part**, never a
coordinate: in 3D, a child of a `BoneAttachment3D`. The moment is not guessed:
the frame where a strike stops, a foot lands, a millstone touches the grain is
written by the agent who animated the entity, in the "Rig and animations"
section of its card (`read_document`). A card silent on the moment is
reported, not estimated. An effect is built around the hit, not the swing.

The final proof is a native Godot capture, before/after (`visual-review`).
Approved concept, Godot-validated scene and user acceptance are **three
distinct statuses**.

## Resources

- [Materials](references/materials.md): how to make a gas, a liquid, a solid, a
  plasma, magic, light — the primitives, the motion, the colour, the traps.
- [Verified examples](references/examples.md): complete specs, one per method,
  that render as is. Read them for the form, do not copy them.
- [The integration contract](references/implementation.md).

Everything is local and free. A **painted material** (a flame texture, a
glyph) can come from `generate_image` (paid, explicit agreement) and enter
through `inputs`: `tex("glyph", u, v)` distorts it, scrolls it, dissolves it.
Motion is always computed: never animation by diffusion. Adapted from
`game-vfx-workflow` (mr-mak-workspace, MIT — see `docs/THIRD_PARTY_NOTICES.md`). See
`sheet-split` (importing an existing flipbook:
`import_sheet(rows=…, columns=…)`), `visual-review`, `animation`.
