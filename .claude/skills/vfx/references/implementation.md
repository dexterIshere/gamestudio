# Integration and review in Godot

## What the studio writes

`build_effect` drops into the Godot project (`res://effects/<name>/`):

- `<name>.png`, the sheet, and `<name>.tres`, a `SpriteFrames` with one
  animation `<name>` (rate and loop from the spec);
- `<name>.tscn`: a root `Node2D` and an `AnimatedSprite2D` named "Flipbook",
  `autoplay` on the animation, `offset` set so that **the scene origin is the
  effect's (0, 0) point** (the atlas pivot), and an additive
  `CanvasItemMaterial` if the effect is additive;
- `<name>_particles.tscn` if the spec declares `godot.particles`: a
  `GPUParticles2D` whose material plays the sheet over each particle's life
  (`particles_animation`, `anim_speed` = 1).

These files are **rewritten at every render**. The game instances them
(`preload("res://effects/<name>/<name>.tscn").instantiate()`) and puts its own
settings on the instance, never in the file. A one-shot effect frees itself on
the `Flipbook`'s `animation_finished` signal; a looping effect is freed by its
owner (end of the state, death, unload). An effect that follows a bone is added
as a child of a `BoneAttachment3D` (in a billboard `Sprite3D`, or a
`SubViewport`); a machine part carries it directly.

## The contract of each layer

For each layer, note: a stable identifier, the owner of the trigger or state,
the target, the space (bone-local or global), the start / hold / end
conditions, the scale base (a fraction of the character's height), the
visibility rule and the resource owner. An unknown tuning value stays marked
unknown: do not fill it in from a demo.

Check the effect against what really happens:

| Case | Expected relation |
| --- | --- |
| Anticipation cancelled | No projectile released, no confirmed-hit burst |
| Release, the target moves | The visual path follows the existing game's projectile |
| Confirmed hit | One impact, on the real target, at the real contact point |
| Continuous state refreshed | The existing effect updates; no unplanned stacking |
| Owner dead or gone | Local emission stops; resources finish or are freed |
| Teleport or pool reuse | The old trail does not join the old and new positions |
| Surface emitter changes site | The new site starts a new life; the old smoke stays consistent |

Look on light ground and on dark ground, under the intended lighting, with
moving targets, selected units and a plausible crowd. Readability comes from
silhouette, value, timing and placement **before** the bloom goes up. Keep
distinct visual priorities for idle, movement, anticipation and brief contact.

Measure the system touched: active emitters, particles, material instances,
transparent overdraw, allocations, cost per frame (`Performance` monitors). A
passing event counter proves the life-cycle wiring, not visual quality nor an
acceptable cost. Note the capture setup and the scope actually measured.

Three explicit evidence labels: **generated concept**, **code-drawn study**,
**native Godot capture**. The licence of a source or provider follows the
assets actually delivered.
