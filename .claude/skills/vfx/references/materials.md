# Materials: how to make each one

Each material is recognised by **its shape, its motion and its light** — in
that order. For each one, this says what makes it readable, the primitives
that build it, how it moves, how it is coloured, and the traps. The snippets
are pieces of effect code (see `effect_reference`); complete specs are in
[the examples](examples.md).

## Techniques used everywhere

- **Mask × noise, then threshold.** Almost every material is a simple shape
  (disc, column, ring, segment) whose **edge** is eaten by a noise, then
  thresholded with `smoothstep`. The noise makes the material, the shape makes
  the reading. `smoothstep(0.4, 0.6, shape * 0.7 + n * 0.5)`: the width of the
  transition sets the edge softness (narrow = dense, cartoon material; wide =
  vapour).
- **Domain warping.** Offsetting the coordinates by a noise before drawing
  (`fbm(x + 0.3 * fbm(x, y, seed=1), y …)`) gives organic swirls; by
  `curl(...)`, eddies that do not settle (gas, liquid, energy). This is the
  move that separates "noise" from "a material".
- **Scrolling.** Slide the noise in the direction of motion (upward for heat,
  outward for a wave); the shape stays in place. Scrolling by a whole period
  (`ty=4`, offset `4 * t`) makes it loop.
- **Polar coordinates.** `rr, aa = polar(x, y)`: a noise in
  `(aa * n, rr * k)` with `tx=n` wraps around without a seam — rays, crowns,
  arcs, vortices, concentric waves. `kaleido(x, y, n)` folds the plane into n
  sectors for anything symmetric (runes, crystals, snowflakes).
- **Ramp by a physical quantity.** Colour does not follow alpha but a quantity
  that means something: heat (fire), depth (water), density (smoke), age
  (`life` of particles), distance to the core (energy). A ramp goes from **dark
  and saturated** to **light and desaturated**: the hottest or most energetic
  part is almost white, never the reverse.
- **Dissolve.** To make a shape disappear without a flat fade: a noise
  threshold that rises with time
  (`shape * step(t * 1.2, fbm(x*4, y*4) * 0.5 + 0.5)`), with a bright rim just
  at the threshold's edge (`pulse(n, t, 0.05)`).
- **Value before colour.** Tune in white (`color: "#ffffff"`), on a dark
  background then on the checkerboard, before any ramp.

## Fire and flames

- **Reading**: a wide, dense base, a thin ragged tip that breaks off in
  tongues; the light core at the bottom, the dark edge at the top.
- **Construction**: a column that narrows (its width decreasing with height),
  its edge eaten by a rising `turb(…)` (turbulence has the ridges that make the
  tongues). **Heat** = shape × (1 − height) − noise × height: it collapses at
  the top, and it is what cuts the tongues.
- **Motion**: upward, faster than the shape moves (`- t * 3` on y); a slight
  sideways sway taken from a slow noise over height. For a volumetric flame
  (campfire, blaze), a `fluid` layer with strong `buoyancy`, `heat` injected at
  the base and a `curl` `force`.
- **Colour**: ramp by heat, from dark red to pale yellow, almost white at the
  core. Blend `add`. Bloom makes the felt heat; without it, a flame looks
  painted.
- **Variants**: magic fire = same method, another ramp (green, purple), more
  turbulence. Embers = a `particles` layer rising with `turbulence`. Smoke
  above = a `normal` layer *under* the flame, born from its tip.
- **Traps**: a core saturated to flat white (ramp pushed too far); a base cut
  sharply by the image edge (`smoothstep` on the height); a flame that "slides"
  because the shape scrolls with the noise.

## Gas: smoke, steam, mist, cloud, poison

- **Reading**: masses that swell, curl on themselves and lighten as they
  disperse. No sharp edge.
- **Construction**: preferably `fluid` — the only method that gives real
  curls. `source` (density injected per second, in a small shape), `heat` to
  make it rise (`buoyancy`), `vorticity` for the eddies, `dissipation` so it
  fades. **A perfectly symmetric source stays laminar** (a smooth column):
  modulate it with an evolving noise, and/or add a `curl` `force`. For still
  mist or a stylised cloud, a `field` of warped `fbm` is enough.
- **Motion**: slow, except at birth. The smoke of an explosion leaves fast then
  slows (`drag`); steam rises and widens; a heavy gas (poison, fog) has
  `weight` > `buoyancy` and creeps along the ground.
- **Colour**: blend `normal`, alpha capped (0.6 to 0.85): smoke is never fully
  opaque. Ramp by density, lighter where it is thick (lit from above); `heat`
  can tint the bottom orange above a fire. Poison: desaturated yellow-green,
  denser at ground level.
- **Traps**: smoke in `add` (it becomes light); sharp edges; a grid too coarse
  (`grid` < 64) that gives squares; a fluid without `warmup` whose loop starts
  empty.

## Liquids: water, blood, acid, lava, bubbles

- **Reading**: the **mass** (round drops, a sheet), the **tension** (drops
  round off, edges are sharp) and the **highlight** (a light point offset
  toward the light). The highlight is what says "liquid".
- **Construction**:
  - splash, jet, rain: `particles` with strong `gravity` (−5 to −8), low
    `softness` (sharp edges), varied sizes; a second layer of smaller white
    particles, slightly offset = the highlights. Same layer `seed`, same birth
    and velocity expressions in both layers: each highlight follows its drop.
  - sheet, puddle, surface: `field`; a shape whose edge ripples (slow
    `noise`), caustics from a scrolling `worley_edge`, a light rim at the edge.
  - ripple on the surface: rings that spread with `ease_out` and thin out.
  - forming drop, viscous liquid: shapes merged with `smin`.
- **Motion**: ballistic (rise, apex, fall); nothing floats. A thick liquid
  (blood, lava) bounces less, has more `drag`.
- **Colour**: `normal` for water and blood (they mask), with a dark value at
  the core and a light one at the edge. Lava and acid are **emissive**: a dark
  `normal` layer (crust) and an `add` layer (glowing cracks from `worley_edge`
  or `ridged`) on top.
- **Traps**: blurry drops (it reads as smoke); a symmetric splash; no
  highlight; water in `add`.

## Solids: debris, rock, shards, ice, crystal, dust

- **Reading**: **edges** and **facets**, a clean fall. A solid does not
  deform: it cracks, breaks, falls back.
- **Construction**:
  - debris, shards: `particles` with `shape: square` (or a short `streak` for
    fast fragments), low `softness`, `gravity`, colour by `rand` (each shard
    its own tint, like a facet lit differently).
  - cracks, ice splitting: `worley_edge` (a cell network), polar rays
    (`fract(aa * n)`), revealed by a spreading front (`ease_out`).
  - crystal, facets: `cellrand` gives one value per cell → one brightness per
    facet; `kaleido` for the symmetry of a snowflake.
  - dust, sand, earth: many small soft particles, high `drag`, a layer of brown
    smoke in `normal` that settles (`weight`).
- **Motion**: a burst of speed at the start, then gravity; dust stays in
  suspension. A crack front is almost instant (2 to 4 frames), the fallout
  long.
- **Colour**: `normal`, strong value contrast between facets; ice adds a very
  light `add` rim on the edges.
- **Traps**: round, soft shards (it reads as drops); everything leaving at the
  same speed (`rand` in the velocity); dust rising like a gas.

## Plasma and electricity: lightning, arc, orb, discharge

- **Reading**: a **very thin, very bright** line inside a wide, saturated halo;
  path changes that are **instant**, not sliding.
- **Construction**:
  - lightning, arc: distance to a line displaced by noise at two scales
    (`fbm(y * 3, k)` for the large waviness, `fbm(y * 12, k)` for the jagged
    detail), where `k = floor(t * n)` changes only n times: the path *jumps*.
    Core: `fill(d - thickness)`; glow: `glow(d, k)`. Branches: a second,
    shorter line, born from a point of the first.
  - orb, plasma ball: polar, `ridged(aa * n, rr * k - t, …, tx=n)` for
    rotating filaments, core in `glow`, wide halo.
  - discharge, short circuit: very fast, very brief `particles` `streak`.
- **Motion**: in jolts (quantised time) for electricity; continuous rotation
  for contained plasma. A `pulse(t, …)` flash on appearance.
- **Colour**: `add`, ramp from saturated blue (halo) to white (core); purple,
  green or red for unnatural energies. Bloom is essential.
- **Traps**: a path that ripples gently (it reads as a rope); a core as thick
  as the halo; too many different frames (the eye cannot follow: 3 to 6 paths
  per second are enough).

## Energy and magic: aura, runes, spell, portal, shield, heal

- **Reading**: **geometry** (circles, symmetries, glyphs) animated by
  **energy** (pulsing, rotation, particles converging or rising). Geometry is
  what says "magic" rather than "natural".
- **Construction**:
  - rune circle: `stroke(ring(...))` rings, a star or polygon in a `kaleido`
    frame, marks spread by `hash(floor(aa * n))`, the whole rotated
    (`rotate(x, y, t / n)` loops with an order-n symmetry).
  - aura: a `glow` around a silhouette or a disc, edge eaten by a rising
    `fbm`, particles that rise and fade.
  - portal, vortex: polar with the angle twisted by the radius
    (`aa + rr * 2 - t`), spiral noise, a dark centre in `normal` under a bright
    edge in `add`.
  - projectile: `glow` core, a trail of `particles` born at the core (the
    projectile moves in the game, not in the sheet).
  - shield: a ring or a bubble (`ring`, a very transparent `fill(circle)`),
    hexagons from `polygon` in a `repeat` grid, and a `spring` impact flash
    when it takes a hit.
  - heal, blessing: soft rising particles, warm or green tints; curse, shadow:
    dark smoke in `normal` + a purple `add` rim.
- **Motion**: regular pulsing (`sin(tau * t * n)`, n whole), slow rotation,
  convergence toward the centre on cast, expansion on release.
- **Colour**: `add`, one hue of the school of magic, lighter at the core; the
  bloom unifies. A dark `normal` layer under the effect separates it from a
  light background.
- **Traps**: geometry too busy at game size (check at 64 px); rotations that
  do not loop (a turn that is not whole); everything equally bright.

## Light: flash, beam, halo, glint

- **Reading**: no material, only value. A flash lasts 1 to 3 frames; a beam has
  a hard core and a soft halo.
- **Construction**: `glow(d, k)` on any shape (disc, segment, thin star);
  `pulse(t, center, width)` for time; a very thin 4- or 6-pointed star
  (`star(..., inner=0.05)`) for the glint of a reflection.
- **Colour**: `add`, almost white, tinted by the source. `intensity` > 1 and
  bloom; this is where overexposure is wanted.
- **Traps**: a flash that lasts (it reads as a lamp); a halo without a core.

## Pure motion: slash, trail, shockwave, wind

- **Slash, weapon swing**: a thick `arc` revealed from one end to the other
  (the end angle follows `ease_out(t / 0.3)`), thinned and faded from the tail
  toward the head; sharp inner edge, `glow` outer edge. 6 to 10 frames.
- **Shockwave**: `stroke(ring(x, y, R(t), 0), w(t))` with `R = ease_out` and a
  shrinking `w`; on the ground (side view), squash it in y
  (`ring(x, y * 3, …)`) and add dust leaving to the sides.
- **Wind, gust**: thin `segment` lines displaced by a slow noise, scrolling,
  low alpha, light `normal`.
- **Traps**: a wave of constant thickness (no energy dissipates); a trail that
  appears all at once.

## Composites: explosion, impact, cast, death

A rich effect is a **sequence of materials** over time, each layer with its
`env`:

- **Explosion**: flash (0 → 2 frames) → shockwave (0 → 8) → fireball
  (1 → 12, `add`) → debris (2 → 15, `particles`) → smoke (4 → end, `normal`).
- **Impact**: a brief hit stop, a star of light, a few shards, a ring; the
  motion tells the force (see SKILL.md §3).
- **Spell cast**: convergence (particles toward the centre, attracting `force`
  `(-x * k, -y * k)`), a circle lighting up, release (flash + wave).
- **Death, dissipation**: the shape dissolves by a rising noise threshold, with
  a bright rim, and particles flying off from the edge.

## Pixel art and small sizes

Render at the final size (`size: 48`, `64`) rather than scaling down after: the
shapes are tuned for that size. `pixelate: n` enlarges pixels without changing
the size; `quantize: {colors: 6}` (one palette **for all frames**) or
`palette: ["#…"]` (the project's); `alpha_steps: 2` for all-or-nothing
transparency. No bloom under quantisation: it creates gradients that the
palette breaks into bands.
