# Verified examples

One spec per method, rendered and looked at before being written here; the
test `tests/test_effects.py` reloads them all, so they stay valid. They show
the **form** of a spec — layers, blend, timing, loop — not a catalogue: a
project's effect is written for its meaning and its art direction (see
[materials](materials.md)).

## Fire — `field`, rising turbulence, heat that cuts the tongues

`flame`

```yaml
description: torch flame, looping
size: [128, 192]
frames: 24
fps: 24
loop: true
seed: 3
defs: |
  # the noise rises by a whole period over the clip (ty=3, offset 3*t): exact loop
  n = turb(x * 2.2, y * 2.2 - t * 3, ty=3, octaves=5)
  h = clamp(y * 0.5 + 0.5, 0, 1)                    # 0 at the bottom, 1 at the top
  width = 0.55 * (1 - h) ** 0.8 + 0.02
  body = smoothstep(width, width * 0.3, abs(x + (n - 0.3) * 0.25 * h))
  base = smoothstep(0.0, 0.18, h)                    # rounded foot, not cut
  heat = body * base * (1 - h) ** 0.8 - n * 0.6 * h - 0.1
layers:
  - name: flame
    alpha: smoothstep(0.0, 0.25, heat)
    color:
      ramp: [[0, "#5a0e00"], [0.3, "#d63a00"], [0.6, "#ff8a1c"], [0.85, "#ffd36a"], [1, "#fff2c4"]]
      by: heat * 1.15
    blend: add
post:
  - bloom: {threshold: 0.7, radius: 6, strength: 0.5}
```

## Gas — `fluid`, perturbed source, buoyancy and eddies

`smoke`

```yaml
size: 160
frames: 32
fps: 24
loop: true
layers:
  - name: smoke
    type: fluid
    grid: 96
    source: 5 * fill(circle(x, y + 0.75, 0.14), 0.06) * (0.6 + 0.8 * noise(x * 6, y * 6, t * 4, tz=4))
    heat: 3 * fill(circle(x, y + 0.75, 0.14), 0.06)
    force: |
      cx, cy = curl(x * 2.5, y * 2.5, t * 2, tz=2)
      (cx * 1.2, cy * 1.2)
    buoyancy: 2.2
    vorticity: 4
    dissipation: 0.965
    alpha: smoothstep(0.0, 0.7, density)
    color:
      ramp: ["#2a2a30", "#7c7c84", "#d0d0d4"]
      by: density * 0.8 + heat * 0.3
```

## Sparks — `particles` as streaks, gravity, exact loop

`sparks`

```yaml
size: 160
frames: 24
fps: 24
loop: true
layers:
  - name: sparks
    type: particles
    count: 120
    lifetime: 0.4 + rand1 * 0.5
    position: (rand2 * 0.2 - 0.1, -0.6)
    velocity: ((rand3 - 0.5) * 2.5, 1.5 + rand4 * 1.5)
    gravity: [0, -4]
    drag: 0.4
    size: 0.025 * (1 - life * 0.5)
    shape: streak
    stretch: 0.04
    alpha: 1 - life
    color: {ramp: ["#ffffff", "#ffd27a", "#ff6a10", "#80200000"]}
    blend: add
    intensity: 1.5
```

## Liquid — two `particles` layers: drops and highlights

`water`

```yaml
size: 160
frames: 20
fps: 24
loop: false
layers:
  - name: drops
    type: particles
    seed: 1
    count: 90
    born: rand0 * 0.15
    lifetime: 0.5 + rand1 * 0.3
    position: ((rand2 - 0.5) * 0.3, -0.5)
    velocity: cart(1.8 + 1.6 * rand4, 0.25 + (rand3 - 0.5) * 0.4)   # a fan: angle in turns
    gravity: [0, -8]
    size: 0.03 + 0.04 * rand5
    softness: 0.2
    alpha: 1 - ease_in(life, 3)
    color: {ramp: ["#e8fbff", "#58c0ff", "#1f6ed0"], by: rand5}
  - name: highlight
    type: particles
    seed: 1
    count: 90
    born: rand0 * 0.15
    lifetime: 0.5 + rand1 * 0.3
    position: ((rand2 - 0.5) * 0.3 - 0.01, -0.49)
    velocity: cart(1.8 + 1.6 * rand4, 0.25 + (rand3 - 0.5) * 0.4)   # a fan: angle in turns
    gravity: [0, -8]
    size: (0.03 + 0.04 * rand5) * 0.35
    alpha: 0.9 * (1 - life)
    color: "#ffffff"
```

## Solid — cracks (`worley_edge`, polar rays), then shards

`ice-shards`

```yaml
description: impact on ice -- cracks, then shards
size: 192
frames: 20
fps: 24
loop: false
seed: 8
defs: |
  rr, aa = polar(x, y)
  front = ease_out(t / 0.3, 3) * 0.8
  cracks = 1 - smoothstep(0.0, 0.025, worley_edge(x * 5, y * 5, jitter=0.9))
  rays = 1 - smoothstep(0.0, 0.03, abs(fract(aa * 9 + noise(rr * 3, 0) * 0.15) - 0.5) * rr)
layers:
  - name: frost
    alpha: smoothstep(front, front - 0.15, rr) * env(t, 0.15, 0.3) * 0.35
    color: "#bfe8ff"
  - name: cracks
    alpha: max(cracks, rays) * smoothstep(front, front - 0.05, rr) * env(t, 0.1, 0.35)
    color: "#f2fbff"
    intensity: 1.2
    blend: add
  - name: shards
    type: particles
    count: 50
    born: 0.12 + rand0 * 0.08
    lifetime: 0.35 + rand1 * 0.35
    position: (cos(tau * rand2) * 0.1, sin(tau * rand2) * 0.1)
    velocity: (cos(tau * rand2) * (1 + 1.5 * rand3), sin(tau * rand2) * (1 + 1.5 * rand3) + 0.8)
    gravity: [0, -6]
    size: 0.015 + 0.025 * rand4
    shape: square
    softness: 0.15
    alpha: 1 - ease_in(life, 2)
    color: {ramp: ["#ffffff", "#9fd8ff", "#4f8fc8"], by: rand5}
```

## Electricity — a path that jumps (`floor(t * n)`), core and glow

`lightning`

```yaml
size: [128, 256]
frames: 12
fps: 24
loop: false
defs: |
  k = floor(t * 6)     # the bolt changes path 6 times
  dx = fbm(y * 3, k * 13.1, octaves=4) * 0.35 + fbm(y * 12, k * 7.3, octaves=2) * 0.08
  d = abs(x - dx)
  flash = env(t, 0.1, 0.2)
layers:
  - name: halo
    alpha: glow(d - 0.02, 12) * flash * 0.6
    color: "#5aa0ff"
    blend: add
  - name: core
    alpha: fill(d - 0.012 * flash, 0.01) * flash
    color: "#f0f6ff"
    blend: add
    intensity: 1.5
```

## Plasma — tiled polar coordinates, rotating `ridged` filaments

`orb`

```yaml
description: plasma orb, looping
size: 160
frames: 32
fps: 24
loop: true
seed: 5
defs: |
  rr, aa = polar(x, y)
  # the angle is tiled over 6 cells: the pattern wraps around without a seam
  arcs = ridged(aa * 6, rr * 4 - t * 2, t * 2, tx=6, ty=2, tz=2, octaves=3)
  core = glow(rr - 0.18, 9)
  filaments = smoothstep(0.75, 0.97, arcs) * smoothstep(0.62, 0.2, rr)
layers:
  - name: halo
    alpha: glow(rr - 0.3, 5) * 0.4
    color: "#2a6bff"
    blend: add
  - name: filaments
    alpha: filaments
    color: "#8fd0ff"
    blend: add
    intensity: 1.3
  - name: core
    alpha: core * (0.85 + 0.15 * sin(tau * t * 3))
    color: {ramp: ["#3a7bff", "#d8f2ff", "#ffffff"]}
    blend: add
post:
  - bloom: {threshold: 0.6, radius: 5, strength: 0.7}
```

## Magic — `kaleido` geometry, a rotation that loops through symmetry

`rune-circle`

```yaml
size: 192
frames: 32
fps: 24
loop: true
defs: |
  rx, ry = rotate(x, y, t / 6)
  kx, ky = kaleido(rx, ry, 6)
  rings = stroke(ring(x, y, 0.8, 0.0), 0.02) + stroke(ring(x, y, 0.62, 0.0), 0.012)
  glyphs = stroke(star(kx, ky, 6, 0.6, 0.5), 0.015)
  marks = fill(box(kx - 0.71, ky, 0.05, 0.03), 0.01) * step(0.5, hash(floor(a * 36)))
  lines = max(rings, glyphs, marks)
  beat = 0.6 + 0.4 * sin(tau * t * 2)
layers:
  - name: halo
    alpha: glow(abs(r - 0.7) - 0.12, 10) * 0.35 * beat
    color: "#6a3cff"
    blend: add
  - name: trace
    alpha: clamp(lines, 0, 1) * (0.7 + 0.3 * beat)
    color: {ramp: ["#7b4dff", "#d9c8ff"], by: lines}
    blend: add
    intensity: 1.4
post:
  - bloom: {threshold: 0.5, radius: 5, strength: 0.9}
```

## Composite — flash, shockwave, fireball, debris, smoke

`explosion`

```yaml
description: one-shot explosion -- flash, shockwave, fireball, smoke, debris
size: 192
frames: 24
fps: 24
loop: false
seed: 11
defs: |
  e = ease_out(t / 0.5, 3)                      # an expansion that slows down
  n = fbm(x * 3, y * 3, t * 2, octaves=4)
  rr = r + n * 0.12
layers:
  - name: smoke
    alpha: smoothstep(0.75 * e + 0.15, 0.4 * e, rr) * env(t, 0.25, 0.1) * 0.85
    color: {ramp: ["#20181400", "#3a302a", "#6a5e56"], by: 1 - rr}
  - name: fireball
    alpha: smoothstep(0.55 * e + 0.05, 0.2 * e, rr) * env(t, 0.08, 0.05, 0.4)
    color: {ramp: ["#7a1800", "#ff6a10", "#ffd060", "#fffbe8"], by: 1 - rr / (0.6 * e + 0.05)}
    blend: add
    intensity: 1.4
  - name: shockwave
    alpha: stroke(ring(x, y, 0.95 * ease_out(t / 0.35, 2), 0), 0.04 * (1 - t), 0.02) * env(t, 0.03, 0, 0.32)
    color: "#ffe2b0"
    blend: add
  - name: flash
    alpha: glow(r - 0.05, 3) * pulse(t, 0.02, 0.06)
    color: "#ffffff"
    blend: add
  - name: debris
    type: particles
    count: 40
    born: 0.02
    lifetime: 0.4 + rand1 * 0.5
    position: (0, 0)
    velocity: (cos(tau * rand2) * (1.5 + 2 * rand3), sin(tau * rand2) * (1.5 + 2 * rand3))
    gravity: [0, -3]
    drag: 1.5
    size: 0.012 + 0.015 * rand4
    shape: streak
    stretch: 0.03
    color: {ramp: ["#fff0b0", "#ff7a20", "#60200000"]}
    blend: add
post:
  - bloom: {threshold: 0.75, radius: 6, strength: 0.6}
```
