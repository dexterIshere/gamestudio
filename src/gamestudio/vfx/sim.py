"""The two simulations of an effect: a fluid on a grid, free particles.

A `field` layer is computed frame by frame, without memory. What has memory --
a smoke that rises and curls, sparks that fall back -- needs a simulation:

- **fluid** (`simulate_fluid`): Stam's "stable fluids", in numpy. A density and
  a heat carried by a velocity, pushed by buoyancy, sustained by vorticity
  confinement, made divergence-free by a pressure projection. Gas, smoke,
  volumetric flame, ink in water, fog: the same equation, other settings. A
  loop closes by cross-fading over one full period, after a warm-up
  (`warmup`): a fluid has no exactly periodic state.
- **particles** (`particle_frames`): each particle is independent and its path
  depends only on its age, so it is integrated once, then each frame reads it
  at the wanted age. This is what makes a loop *exact*: a particle born near
  the end of the clip reappears at the start with the age it would have had.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np

from . import noise as nz
from .expr import FUNCTIONS, EvalContext, ExprError, Program
from .spec import EffectSpec, Layer

RAND_STREAMS = 6


# ---------------------------------------------------------------------- tools

def _advect(field: np.ndarray, vx: np.ndarray, vy: np.ndarray, dt_cells: float) -> np.ndarray:
    """Semi-Lagrangian transport: each cell fetches its value upstream.

    The fluid grid is in math orientation: row 0 is at the bottom, y grows with
    the row index. It is only flipped on output.
    """
    rows, cols = field.shape
    jj, ii = np.meshgrid(np.arange(cols, dtype=np.float32), np.arange(rows, dtype=np.float32))
    src_x = np.clip(jj - vx * dt_cells, 0, cols - 1.001)
    src_y = np.clip(ii - vy * dt_cells, 0, rows - 1.001)
    x0, y0 = np.floor(src_x).astype(np.int64), np.floor(src_y).astype(np.int64)
    ax, ay = src_x - x0, src_y - y0
    return ((field[y0, x0] * (1 - ax) + field[y0, x0 + 1] * ax) * (1 - ay)
            + (field[y0 + 1, x0] * (1 - ax) + field[y0 + 1, x0 + 1] * ax) * ay)


def _shift(field: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """The value of cell (i + dy, j + dx), with the edge repeated: open boundary."""
    padded = np.pad(field, 1, mode="edge")
    rows, cols = field.shape
    return padded[1 + dy:1 + dy + rows, 1 + dx:1 + dx + cols]


def _ddx(field: np.ndarray) -> np.ndarray:
    return 0.5 * (_shift(field, 0, 1) - _shift(field, 0, -1))


def _ddy(field: np.ndarray) -> np.ndarray:
    return 0.5 * (_shift(field, 1, 0) - _shift(field, -1, 0))


def _project(vx: np.ndarray, vy: np.ndarray, iterations: int) -> tuple[np.ndarray, np.ndarray]:
    """Remove the divergence: a gas does not compress, it curls."""
    div = _ddx(vx) + _ddy(vy)
    pressure = np.zeros_like(div)
    for _ in range(iterations):
        pressure = (_shift(pressure, 0, 1) + _shift(pressure, 0, -1)
                    + _shift(pressure, 1, 0) + _shift(pressure, -1, 0) - div) * 0.25
    return vx - _ddx(pressure), vy - _ddy(pressure)


def _vorticity(vx: np.ndarray, vy: np.ndarray, strength: float, dt: float
               ) -> tuple[np.ndarray, np.ndarray]:
    """Vorticity confinement: restores the swirls the grid erases.

    f = ε h (N x ω); ω measured in cell differences is already h·ω, hence no
    cell size here.
    """
    if strength == 0:
        return vx, vy
    curl = _ddx(vy) - _ddy(vx)
    magnitude = np.abs(curl)
    gx, gy = _ddx(magnitude), _ddy(magnitude)
    norm = np.sqrt(gx * gx + gy * gy) + 1e-6
    nx, ny = gx / norm, gy / norm
    return vx + strength * ny * curl * dt, vy - strength * nx * curl * dt


def grid_env(width: int, height: int) -> dict[str, np.ndarray]:
    """A grid's coordinates: x, y, u, v, r, a and the size of a pixel."""
    short = min(width, height)
    xs = (np.arange(width, dtype=np.float32) + 0.5 - width / 2) / (short / 2)
    ys = (height / 2 - np.arange(height, dtype=np.float32) - 0.5) / (short / 2)
    x, y = np.meshgrid(xs, ys)
    u = (np.arange(width, dtype=np.float32) + 0.5) / width
    v = 1 - (np.arange(height, dtype=np.float32) + 0.5) / height
    u, v = np.meshgrid(u, v)
    return {"x": x, "y": y, "u": u, "v": v, "r": np.sqrt(x * x + y * y),
            "a": np.mod(np.arctan2(y, x) / (2 * np.pi), 1.0).astype(np.float32),
            "px": np.float32(2.0 / short)}


def time_env(spec: EffectSpec, frame: float) -> dict[str, Any]:
    t = (frame / spec.frames) % 1.0 if spec.loop else frame / spec.frames
    return {"t": np.float32(t), "time": np.float32(frame / spec.fps), "frame": np.float32(frame),
            "frames": np.float32(spec.frames), "fps": np.float32(spec.fps),
            "duration": np.float32(spec.duration), "pi": np.float32(math.pi),
            "tau": np.float32(2 * math.pi)}


def _scalar_field(program: Program | None, env: dict[str, Any], ctx: EvalContext,
                  shape: tuple[int, int], where: str) -> np.ndarray:
    if program is None:
        return np.zeros(shape, np.float32)
    value = program.run(env, ctx)
    if isinstance(value, tuple):
        raise ExprError(f"{where}: one value is expected, not {len(value)}")
    return np.broadcast_to(np.asarray(value, np.float32), shape).astype(np.float32)


def _vector(program: Program | None, env: dict[str, Any], ctx: EvalContext,
            shape: tuple[int, ...], where: str) -> tuple[np.ndarray, np.ndarray]:
    if program is None:
        zero = np.zeros(shape, np.float32)
        return zero, zero
    value = program.run(env, ctx)
    if not isinstance(value, tuple) or len(value) != 2:
        raise ExprError(f"{where}: a pair (x, y) is expected")
    return tuple(np.broadcast_to(np.asarray(c, np.float32), shape).astype(np.float32)
                 for c in value)  # type: ignore[return-value]


# ---------------------------------------------------------------------- fluid

def simulate_fluid(layer: Layer, spec: EffectSpec, ctx: EvalContext,
                   shared: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                   *, scale: float = 1.0) -> list[dict[str, np.ndarray]]:
    """Simulate a layer's fluid: one state (density, heat, velocity) per frame."""
    p = layer.params
    short = max(8, int(round(p["grid"] * min(1.0, max(scale, 0.25)))))
    aspect = spec.width / spec.height
    if aspect >= 1:
        gw, gh = int(round(short * aspect)), short
    else:
        gw, gh = short, int(round(short / aspect))
    # Math orientation during the simulation (see `_advect`).
    grid = {key: np.flipud(value) if np.ndim(value) == 2 else value
            for key, value in grid_env(gw, gh).items()}
    shape = (gh, gw)
    cell = 2.0 / short                       # size of a cell, in x units
    substeps = max(1, p["substeps"])
    dt = 1.0 / spec.fps / substeps
    dt_cells = dt / cell
    warmup = p["warmup"] if p["warmup"] is not None else (48 if spec.loop else 0)
    total = warmup + spec.frames * (2 if spec.loop else 1)
    frame_rate = dt * spec.fps               # fraction of a frame per substep

    density = np.zeros(shape, np.float32)
    heat = np.zeros(shape, np.float32)
    vx = np.zeros(shape, np.float32)
    vy = np.zeros(shape, np.float32)
    local = EvalContext(seed=ctx.seed, shape=shape, inputs=ctx.inputs)
    states: list[dict[str, np.ndarray]] = []

    for step in range(total):
        for sub in range(substeps):
            frame = step - warmup + sub / substeps
            env = {**grid, **time_env(spec, frame if spec.loop else max(frame, 0)),
                   "density": density, "heat": heat, "vx": vx, "vy": vy}
            if shared is not None:
                env.update(shared(env))
            source = _scalar_field(p["source"], env, local, shape, f"{layer.name}.source")
            density = density + source * dt
            if p["heat"] is not None:
                heat = heat + _scalar_field(p["heat"], env, local, shape, f"{layer.name}.heat") * dt
            fx, fy = _vector(p["force"], env, local, shape, f"{layer.name}.force")
            lift = heat if p["heat"] is not None else density
            vx = vx + fx * dt
            vy = vy + (fy + p["buoyancy"] * lift - p["weight"] * density) * dt
            vx, vy = _vorticity(vx, vy, p["vorticity"], dt)
            if p["drag"]:
                keep = (1 - min(max(p["drag"], 0.0), 1.0)) ** frame_rate
                vx, vy = vx * keep, vy * keep
            vx, vy = _project(vx, vy, p["iterations"])
            vx, vy = _advect(vx, vx, vy, dt_cells), _advect(vy, vx, vy, dt_cells)
            vx, vy = _project(vx, vy, max(4, p["iterations"] // 3))
            density = _advect(density, vx, vy, dt_cells) * p["dissipation"] ** frame_rate
            heat = _advect(heat, vx, vy, dt_cells) * p["cooling"] ** frame_rate
        if step >= warmup:
            states.append({"density": density.copy(), "heat": heat.copy(),
                           "vx": vx.copy(), "vy": vy.copy()})

    if spec.loop:
        # Cross-fade over one period: frame i blends state i+F (which precedes
        # state 0 in the loop's time) with state i. The last frame thus joins
        # the first without a jump.
        count = spec.frames
        looped = []
        for i in range(count):
            k = i / count
            looped.append({key: states[count + i][key] * (1 - k) + states[i][key] * k
                           for key in states[i]})
        states = looped
    out = []
    for state in states:
        state = {key: np.ascontiguousarray(np.flipud(value)) for key, value in state.items()}
        state["speed"] = np.sqrt(state["vx"] ** 2 + state["vy"] ** 2)
        out.append(state)
    return out


# ------------------------------------------------------------------ particles

def particle_frames(layer: Layer, spec: EffectSpec, ctx: EvalContext
                    ) -> list[dict[str, np.ndarray]]:
    """The particles visible on each frame, with their state (position, age...)."""
    p = layer.params
    count = p["count"]
    salt = p["seed"] if p["seed"] is not None else \
        sum(ord(ch) * (i + 1) for i, ch in enumerate(layer.name))
    rand = {f"rand{k}": nz.random_values(count, ctx.seed + salt, k) for k in range(RAND_STREAMS)}
    shape = (count,)
    base = {**rand, **time_env(spec, 0)}
    base.pop("t")
    local = EvalContext(seed=ctx.seed, shape=shape, inputs=ctx.inputs)

    def scalar(program: Program, env: dict[str, Any], where: str) -> np.ndarray:
        return _scalar_field(program, env, local, shape, f"{layer.name}.{where}")

    born = scalar(p["born"], {**base, "t": np.float32(0)}, "born")
    lifetime = np.maximum(scalar(p["lifetime"], {**base, "t": np.float32(0)}, "lifetime"),
                          1.0 / spec.fps)
    x0, y0 = _vector(p["position"], {**base, "t": np.float32(0)}, local, shape,
                     f"{layer.name}.position")
    vx0, vy0 = _vector(p["velocity"], {**base, "t": np.float32(0)}, local, shape,
                       f"{layer.name}.velocity")

    steps = int(math.ceil(float(lifetime.max()) * spec.fps)) + 2
    dt = 1.0 / spec.fps
    pos = np.zeros((steps, count, 2), np.float32)
    vel = np.zeros((steps, count, 2), np.float32)
    pos[0, :, 0], pos[0, :, 1] = x0, y0
    vel[0, :, 0], vel[0, :, 1] = vx0, vy0
    gx, gy = p["gravity"]
    curl = FUNCTIONS["curl"].impl
    for step in range(1, steps):
        px, py = pos[step - 1, :, 0], pos[step - 1, :, 1]
        vx, vy = vel[step - 1, :, 0], vel[step - 1, :, 1]
        age = np.float32((step - 1) * dt)
        ax = np.full(shape, gx, np.float32) - p["drag"] * vx
        ay = np.full(shape, gy, np.float32) - p["drag"] * vy
        if p["force"] is not None:
            env = {**base, "x": px, "y": py, "vx": vx, "vy": vy, "age": age,
                   "life": np.clip(age / lifetime, 0, 1), "lifetime": lifetime,
                   "t": np.mod(born + age / spec.duration, 1.0)}
            fx, fy = _vector(p["force"], env, local, shape, f"{layer.name}.force")
            ax, ay = ax + fx, ay + fy
        if p["turbulence"]:
            # A fixed field per particle (z drawn from its seed): turbulence does
            # not depend on absolute time, otherwise the loop would not close.
            cx, cy = curl(local, px * p["turbulence_scale"], py * p["turbulence_scale"],
                          rand["rand5"] * 7.0)
            ax, ay = ax + p["turbulence"] * cx, ay + p["turbulence"] * cy
        vel[step, :, 0], vel[step, :, 1] = vx + ax * dt, vy + ay * dt
        pos[step] = pos[step - 1] + vel[step] * dt

    life_frames = lifetime * spec.fps
    born_frames = born * spec.frames
    wraps = int(math.ceil(float(life_frames.max()) / spec.frames)) + 1 if spec.loop else 1
    frames: list[dict[str, np.ndarray]] = []
    for frame in range(spec.frames):
        picks: list[tuple[np.ndarray, np.ndarray]] = []
        for k in range(wraps):
            age_frames = frame - born_frames + k * spec.frames
            alive = (age_frames >= 0) & (age_frames < life_frames)
            if alive.any():
                picks.append((np.nonzero(alive)[0], age_frames[alive]))
        if not picks:
            frames.append({})
            continue
        index = np.concatenate([pick[0] for pick in picks])
        age_f = np.concatenate([pick[1] for pick in picks]).astype(np.float32)
        lo = np.floor(age_f).astype(np.int64)
        frac = (age_f - lo)[:, None]
        hi = np.minimum(lo + 1, steps - 1)
        position = pos[lo, index] * (1 - frac) + pos[hi, index] * frac
        velocity = vel[lo, index] * (1 - frac) + vel[hi, index] * frac
        age = age_f / spec.fps
        state = {key: values[index] for key, values in rand.items()}
        state.update({
            "x": position[:, 0], "y": position[:, 1],
            "vx": velocity[:, 0], "vy": velocity[:, 1],
            "speed": np.sqrt((velocity ** 2).sum(axis=1)),
            "age": age, "lifetime": lifetime[index],
            "life": np.clip(age / lifetime[index], 0, 1),
            "born": born[index],
        })
        frames.append(state)
    return frames


def splat(state: dict[str, np.ndarray], size: np.ndarray, alpha: np.ndarray,
          color: tuple[np.ndarray, np.ndarray, np.ndarray], layer: Layer,
          width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Draw the particles: return the layer's (premultiplied rgb, alpha)."""
    rgb = np.zeros((height, width, 3), np.float32)
    acc = np.zeros((height, width), np.float32)
    if not state:
        return rgb, acc
    p = layer.params
    short = min(width, height)
    unit = short / 2
    cx = width / 2 + state["x"] * unit
    cy = height / 2 - state["y"] * unit
    radius = np.clip(np.abs(size) * unit, 0.5, 64.0)
    weight = np.clip(alpha, 0, None)

    points_x, points_y, points_r = [cx], [cy], [radius]
    points_w, points_i = [weight], [np.arange(cx.size)]
    if p["shape"] == "streak":
        # A streak: points along the velocity, paler and paler toward the tail,
        # close enough to overlap.
        tail_x = state["vx"] * p["stretch"] * unit
        tail_y = -state["vy"] * p["stretch"] * unit
        length = np.sqrt(tail_x ** 2 + tail_y ** 2)
        samples = int(np.clip(np.ceil(float(np.max(length / np.maximum(radius, 0.5)) * 2)), 1, 24))
        points_x, points_y, points_r, points_w, points_i = [], [], [], [], []
        for k in range(samples + 1):
            f = k / max(samples, 1)
            points_x.append(cx - tail_x * f)
            points_y.append(cy - tail_y * f)
            points_r.append(radius * (1 - 0.5 * f))
            spacing = np.maximum(length / max(samples, 1), 1e-3)
            density = np.minimum(1.0, np.maximum(radius, 0.5) / spacing * 0.5)
            points_w.append(weight * (1 - f) * np.minimum(density, 1.0))
            points_i.append(np.arange(cx.size))

    px_all = np.concatenate(points_x)
    py_all = np.concatenate(points_y)
    r_all = np.concatenate(points_r)
    w_all = np.concatenate(points_w)
    i_all = np.concatenate(points_i)
    reach = int(np.ceil(float(r_all.max()))) + 1
    offsets = np.arange(-reach, reach + 1)
    oy, ox = np.meshgrid(offsets, offsets, indexing="ij")
    oy, ox = oy.ravel(), ox.ravel()
    hardness = float(np.clip(1 - p["softness"], 0, 0.999))
    chunk = max(1, 4_000_000 // oy.size)
    red, green, blue = (np.broadcast_to(np.asarray(c, np.float32), cx.shape) for c in color)

    for start in range(0, px_all.size, chunk):
        sl = slice(start, start + chunk)
        bx = np.floor(px_all[sl]).astype(np.int64)[:, None] + ox[None, :]
        by = np.floor(py_all[sl]).astype(np.int64)[:, None] + oy[None, :]
        dx = bx + 0.5 - px_all[sl][:, None]
        dy = by + 0.5 - py_all[sl][:, None]
        r = r_all[sl][:, None]
        if p["shape"] == "square":
            dist = np.maximum(np.abs(dx), np.abs(dy)) / r
        else:
            dist = np.sqrt(dx * dx + dy * dy) / r
        if p["shape"] == "ring":
            dist = np.abs(dist - 0.75) * 4
        k = np.clip((1 - dist) / max(1 - hardness, 1e-3), 0, 1)
        cover = k * k * (3 - 2 * k) * w_all[sl][:, None]
        inside = (bx >= 0) & (bx < width) & (by >= 0) & (by < height) & (cover > 1e-4)
        if not inside.any():
            continue
        rows, cols = by[inside], bx[inside]
        values = cover[inside]
        owner = np.broadcast_to(i_all[sl][:, None], cover.shape)[inside]
        np.add.at(acc, (rows, cols), values)
        for channel, source in enumerate((red, green, blue)):
            np.add.at(rgb[..., channel], (rows, cols), values * source[owner])
    # Accumulated coverage: overlapping particles thicken without exceeding 1;
    # the color stays the weighted mean.
    alpha_out = 1 - np.exp(-acc * 1.6)
    mean = rgb / np.maximum(acc, 1e-6)[..., None]
    return mean * alpha_out[..., None], alpha_out
