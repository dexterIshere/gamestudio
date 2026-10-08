"""The effects language: numpy expressions, evaluated over the whole image.

An effect is not picked from a list: it is written. Each layer of an effect
carries code -- a few assignments, then a final expression -- evaluated once
per frame over the whole pixel grid (or over the particle array), exactly like
a fragment shader:

    w = fbm(x * 3, y * 3 - t * 4, ty=4)        # scrolls one period: loops
    flame = fill(circle(x, y + w * 0.3, 0.5), 0.2)
    flame * smoothstep(-1, 0.2, y)

The code is **parsed, never executed**: the syntax tree is walked by this
evaluator, which only accepts numbers, known names, arithmetic, comparisons,
`a if c else b` and calls to the functions listed in `FUNCTIONS`. No
attribute, no subscript, no import, no loop: an effect spec coming from an
agent or a file can do nothing but compute an image.

The language reference (`reference()`) is drawn from this registry: it cannot
mention a function that does not exist.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import noise as nz

# Beyond this, effect code is no longer an effect: it is a mistake or an abuse.
MAX_SOURCE = 8000
MAX_NODES = 2500


class ExprError(ValueError):
    """Invalid effect code: syntax, unknown name, bad call."""


@dataclass
class Function:
    name: str
    signature: str
    doc: str
    category: str
    impl: Callable[..., Any]
    # Functions that need the evaluation context (seed, images).
    contextual: bool = False


FUNCTIONS: dict[str, Function] = {}

CATEGORIES = {
    "math": "Math",
    "shape": "Shapes (signed distances) and coverage",
    "domain": "Warping space",
    "noise": "Noises",
    "time": "Time and envelopes",
    "color": "Color and images",
}


def _register(category: str, signature: str, doc: str, *, contextual: bool = False):
    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        name = signature.split("(", 1)[0]
        FUNCTIONS[name] = Function(name, signature, doc, category, fn, contextual)
        return fn
    return wrap


def _f(value: Any) -> Any:
    if isinstance(value, (tuple, list)):
        return tuple(_f(v) for v in value)
    if isinstance(value, np.ndarray):
        return value.astype(np.float32, copy=False)
    return np.float32(value)


# ----------------------------------------------------------------------- math

def _unary(name: str, fn: Callable[[Any], Any], doc: str) -> None:
    _register("math", f"{name}(v)", doc)(lambda v: fn(v))


_unary("sin", np.sin, "Sine (radians).")
_unary("cos", np.cos, "Cosine (radians).")
_unary("tan", np.tan, "Tangent (radians).")
_unary("asin", lambda v: np.arcsin(np.clip(v, -1, 1)), "Arc sine, argument clamped to [-1, 1].")
_unary("acos", lambda v: np.arccos(np.clip(v, -1, 1)), "Arc cosine, argument clamped to [-1, 1].")
_unary("atan", np.arctan, "Arc tangent.")
_unary("sqrt", lambda v: np.sqrt(np.maximum(v, 0)), "Square root (0 below zero).")
_unary("exp", lambda v: np.exp(np.minimum(v, 60)), "Exponential.")
_unary("log", lambda v: np.log(np.maximum(v, 1e-12)), "Natural logarithm (bounded at 0).")
_unary("abs", np.abs, "Absolute value.")
_unary("sign", np.sign, "Sign: -1, 0 or 1.")
_unary("floor", np.floor, "Round down to an integer.")
_unary("ceil", np.ceil, "Round up to an integer.")
_unary("fract", lambda v: v - np.floor(v), "Fractional part, in [0, 1).")
_unary("saturate", lambda v: np.clip(v, 0, 1), "Clamp to [0, 1].")


@_register("math", "atan2(y, x)", "Angle of the vector (x, y), in radians.")
def _atan2(y, x):
    return np.arctan2(y, x)


@_register("math", "mod(a, b)", "Always-positive remainder of a / b.")
def _mod(a, b):
    return np.mod(a, b)


@_register("math", "pow(a, b)", "a to the power b; a negative a is brought to 0.")
def _pow(a, b):
    return np.power(np.maximum(a, 0), b)


@_register("math", "min(a, b, ...)", "Element-wise minimum (union of distances).")
def _min(*args):
    out = args[0]
    for arg in args[1:]:
        out = np.minimum(out, arg)
    return out


@_register("math", "max(a, b, ...)", "Element-wise maximum (intersection of distances).")
def _max(*args):
    out = args[0]
    for arg in args[1:]:
        out = np.maximum(out, arg)
    return out


@_register("math", "clamp(v, lo=0, hi=1)", "Clamp v between lo and hi.")
def _clamp(v, lo=0.0, hi=1.0):
    return np.clip(v, lo, hi)


@_register("math", "mix(a, b, k)", "Linear interpolation: a when k=0, b when k=1.")
def _mix(a, b, k):
    if isinstance(a, tuple):
        return tuple(_mix(x, y, k) for x, y in zip(a, b, strict=True))
    return a + (b - a) * k


@_register("math", "smoothstep(e0, e1, v)",
           "0 before e0, 1 after e1, smooth transition. e0 > e1 reverses the slope.")
def _smoothstep(e0, e1, v):
    span = np.where(np.abs(np.asarray(e1 - e0)) < 1e-9, 1e-9, e1 - e0)
    k = np.clip((v - e0) / span, 0, 1)
    return k * k * (3 - 2 * k)


@_register("math", "step(edge, v)", "0 below edge, 1 beyond: a hard threshold.")
def _step(edge, v):
    return (np.asarray(v) >= edge).astype(np.float32)


@_register("math", "remap(v, a, b, c=0, d=1)", "Map v from [a, b] to [c, d], without clamping.")
def _remap(v, a, b, c=0.0, d=1.0):
    return c + (v - a) / (b - a if b != a else 1e-9) * (d - c)


@_register("math", "where(cond, a, b)", "a where cond > 0.5, b elsewhere.")
def _where(cond, a, b):
    return np.where(np.asarray(cond) > 0.5, a, b)


@_register("math", "length(x, y)", "Length of the vector (x, y).")
def _length(x, y):
    return np.sqrt(x * x + y * y)


@_register("math", "smin(a, b, k=0.1)", "Smooth minimum: merges two shapes like two drops.")
def _smin(a, b, k=0.1):
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return b + (a - b) * h - k * h * (1 - h)


# -------------------------------------------------------- shapes, coverage

@_register("shape", "circle(x, y, r)", "Signed distance to a disk of radius r (negative inside).")
def _circle(x, y, r):
    return np.sqrt(x * x + y * y) - r


@_register("shape", "ring(x, y, r, w)", "Signed distance to a ring of radius r and thickness w.")
def _ring(x, y, r, w):
    return np.abs(np.sqrt(x * x + y * y) - r) - w * 0.5


@_register("shape", "box(x, y, w, h, round=0)", "Signed distance to a centered w x h rectangle, "
                                                "rounded corners.")
def _box(x, y, w, h, round=0.0):
    qx = np.abs(x) - w * 0.5 + round
    qy = np.abs(y) - h * 0.5 + round
    outside = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2)
    return outside + np.minimum(np.maximum(qx, qy), 0) - round


@_register("shape", "segment(x, y, ax, ay, bx, by)", "Distance to the segment [A, B]: strokes, "
                                                     "lightning, blades.")
def _segment(x, y, ax, ay, bx, by):
    pax, pay = x - ax, y - ay
    bax, bay = bx - ax, by - ay
    denom = bax * bax + bay * bay
    h = np.clip((pax * bax + pay * bay) / (denom if np.all(denom) else denom + 1e-9), 0, 1)
    dx, dy = pax - bax * h, pay - bay * h
    return np.sqrt(dx * dx + dy * dy)


@_register("shape", "arc(x, y, r, start, end, w)",
           "Distance to a circular arc between two angles (in turns, 0 = right, "
           "counterclockwise).")
def _arc(x, y, r, start, end, w):
    angle = np.mod(np.arctan2(y, x) / (2 * np.pi), 1.0)
    span = np.mod(end - start, 1.0) or 1.0
    rel = np.mod(angle - start, 1.0)
    inside = rel <= span
    radial = np.abs(np.sqrt(x * x + y * y) - r) - w * 0.5
    ends = []
    for edge in (start, start + span):
        ex, ey = r * np.cos(2 * np.pi * edge), r * np.sin(2 * np.pi * edge)
        ends.append(np.sqrt((x - ex) ** 2 + (y - ey) ** 2) - w * 0.5)
    return np.where(inside, radial, np.minimum(*ends))


@_register("shape", "polygon(x, y, n, r)", "Signed distance to a regular n-sided polygon.")
def _polygon(x, y, n, r):
    n = max(3, int(round(float(np.mean(n)))))
    sector = 2 * np.pi / n
    angle = np.arctan2(x, y)
    folded = np.mod(angle + sector / 2, sector) - sector / 2
    length = np.sqrt(x * x + y * y)
    return length * np.cos(folded) - r * np.cos(sector / 2)


@_register("shape", "star(x, y, n, r, inner=0.5)", "Approximate distance to an n-pointed star "
                                                   "(inner: relative inner radius).")
def _star(x, y, n, r, inner=0.5):
    n = max(2, int(round(float(np.mean(n)))))
    sector = 2 * np.pi / n
    angle = np.mod(np.arctan2(x, y), sector) - sector / 2
    length = np.sqrt(x * x + y * y)
    blend = np.abs(angle) / (sector / 2)
    edge = r * inner + (r - r * inner) * (1 - blend)
    return length - edge


@_register("shape", "fill(d, soft=0.01)", "Coverage of a shape: 1 inside (d < 0), 0 outside, "
                                          "edge softened by `soft`.")
def _fill(d, soft=0.01):
    return _smoothstep(soft, -soft, d)


@_register("shape", "stroke(d, w, soft=0.01)", "Coverage of a shape's outline, thickness w.")
def _stroke(d, w, soft=0.01):
    return _smoothstep(soft, -soft, np.abs(d) - w * 0.5)


@_register("shape", "glow(d, k=8)", "Halo around a shape: 1 at the edge, exponential "
                                    "falloff of steepness k.")
def _glow(d, k=8.0):
    return np.exp(-np.maximum(d, 0) * k)


# --------------------------------------------------------------------- domain

@_register("domain", "rotate(x, y, turns)", "Rotate the frame by `turns` turns. Returns (x, y).")
def _rotate(x, y, turns):
    c, s = np.cos(2 * np.pi * turns), np.sin(2 * np.pi * turns)
    return x * c - y * s, x * s + y * c


@_register("domain", "polar(x, y)", "Polar coordinates: returns (r, a), a in turns in [0, 1).")
def _polar(x, y):
    return np.sqrt(x * x + y * y), np.mod(np.arctan2(y, x) / (2 * np.pi), 1.0)


@_register("domain", "cart(r, a)", "Inverse of polar: returns (x, y), a in turns.")
def _cart(r, a):
    return r * np.cos(2 * np.pi * a), r * np.sin(2 * np.pi * a)


@_register("domain", "kaleido(x, y, n)", "Fold the plane into n symmetric sectors: rune "
                                          "circles, mandalas, crystals. Returns (x, y).")
def _kaleido(x, y, n):
    n = max(1, int(round(float(np.mean(n)))))
    r = np.sqrt(x * x + y * y)
    sector = 2 * np.pi / n
    a = np.mod(np.arctan2(y, x), sector)
    a = np.abs(a - sector / 2)
    return r * np.cos(a), r * np.sin(a)


@_register("domain", "repeat(v, size)", "Repeat the axis every `size`, centered on 0: rows, "
                                         "grids, patterns.")
def _repeat(v, size):
    return np.mod(v + size * 0.5, size) - size * 0.5


@_register("domain", "cellindex(v, size)", "The index of the repetition v falls in (combine "
                                            "with hash).")
def _cellindex(v, size):
    return np.floor((v + size * 0.5) / size)


# ---------------------------------------------------------------------- noise

def _noise_coords(x, y, z, w):
    coords = [x, y]
    if z is not None:
        coords.append(z)
    if w is not None:
        if z is None:
            coords.append(np.float32(0))
        coords.append(w)
    return tuple(coords)


def _periods(tx, ty, tz, tw):
    return (tx, ty, tz, tw)


def _seed(ctx, seed):
    return int(ctx.seed) + int(seed) * 7919


@_register("noise", "noise(x, y, z=None, w=None, tx=0, ty=0, tz=0, tw=0, seed=0)",
           "Smooth gradient noise in [-1, 1], in 2 to 4 dimensions. tx..tw: integer period "
           "per axis (tiling) -- scrolling a tiled axis by one period over the clip loops "
           "seamlessly.",
           contextual=True)
def _noise(ctx, x, y, z=None, w=None, tx=0, ty=0, tz=0, tw=0, seed=0):
    return nz.gradient(_noise_coords(x, y, z, w), periods=_periods(tx, ty, tz, tw),
                       seed=_seed(ctx, seed))


def _fractal(mode: str, ctx, x, y, z=None, w=None, octaves=5, lacunarity=2.0, gain=0.5,
             tx=0, ty=0, tz=0, tw=0, seed=0):
    return nz.fractal(_noise_coords(x, y, z, w), octaves=octaves, lacunarity=lacunarity,
                      gain=gain, mode=mode, periods=_periods(tx, ty, tz, tw),
                      seed=_seed(ctx, seed))


_FRACTAL_ARGS = ("x, y, z=None, w=None, octaves=5, lacunarity=2, gain=0.5, "
                 "tx=0, ty=0, tz=0, tw=0, seed=0")


@_register("noise", f"fbm({_FRACTAL_ARGS})",
           "Smooth fractal noise in [-1, 1]: clouds, smoke, masses, ripples.", contextual=True)
def _fbm(ctx, *args, **kwargs):
    return _fractal("fbm", ctx, *args, **kwargs)


@_register("noise", f"ridged({_FRACTAL_ARGS})",
           "Ridged fractal noise in [0, 1]: filaments, lightning, veins, "
           "cracks.", contextual=True)
def _ridged(ctx, *args, **kwargs):
    return _fractal("ridged", ctx, *args, **kwargs)


@_register("noise", f"turb({_FRACTAL_ARGS})",
           "Turbulence in [0, 1] (accumulated |noise|): flames, boiling, "
           "plasma.", contextual=True)
def _turb(ctx, *args, **kwargs):
    return _fractal("turbulence", ctx, *args, **kwargs)


def _cell(ctx, x, y, z, tx, ty, tz, jitter, seed):
    coords = (x, y) if z is None else (x, y, z)
    return nz.cellular(coords, periods=(tx, ty, tz), jitter=jitter, seed=_seed(ctx, seed))


_CELL_ARGS = "x, y, z=None, tx=0, ty=0, tz=0, jitter=1, seed=0"


@_register("noise", f"worley({_CELL_ARGS})",
           "Cellular noise: distance to the nearest seed point (~0..1). Bubbles, foam, scales, "
           "cells.",
           contextual=True)
def _worley(ctx, x, y, z=None, tx=0, ty=0, tz=0, jitter=1.0, seed=0):
    return _cell(ctx, x, y, z, tx, ty, tz, jitter, seed)[0]


@_register("noise", f"worley_edge({_CELL_ARGS})",
           "Edges between cells (f2 - f1, 0 on the edge): cracks, caustics, networks, "
           "ice.",
           contextual=True)
def _worley_edge(ctx, x, y, z=None, tx=0, ty=0, tz=0, jitter=1.0, seed=0):
    f1, f2, _ = _cell(ctx, x, y, z, tx, ty, tz, jitter, seed)
    return f2 - f1


@_register("noise", f"cellrand({_CELL_ARGS})",
           "One random value [0, 1] per cell: shards, facets, debris of a different "
           "hue.",
           contextual=True)
def _cellrand(ctx, x, y, z=None, tx=0, ty=0, tz=0, jitter=1.0, seed=0):
    return _cell(ctx, x, y, z, tx, ty, tz, jitter, seed)[2]


@_register("noise", "curl(x, y, z=None, octaves=2, tx=0, ty=0, tz=0, seed=0)",
           "Divergence-free vector field drawn from a noise: returns (vx, vy). Warping the "
           "domain by it gives swirls of smoke, liquid or energy that whirl without "
           "bunching up.",
           contextual=True)
def _curl(ctx, x, y, z=None, octaves=2, tx=0, ty=0, tz=0, seed=0):
    eps = 0.01

    def potential(px, py):
        return _fractal("fbm", ctx, px, py, z, None, octaves=octaves,
                        tx=tx, ty=ty, tz=tz, seed=seed)

    dx = (potential(x + eps, y) - potential(x - eps, y)) / (2 * eps)
    dy = (potential(x, y + eps) - potential(x, y - eps)) / (2 * eps)
    return dy, -dx


@_register("noise", "hash(a, b=0, seed=0)",
           "Stable random value [0, 1) for each pair of integers (a, b): one seed per "
           "cell, per row.",
           contextual=True)
def _hash(ctx, a, b=0, seed=0):
    a_arr, b_arr = np.broadcast_arrays(np.floor(np.asarray(a)), np.floor(np.asarray(b)))
    h = nz._hash([a_arr.astype(np.int64), b_arr.astype(np.int64)], _seed(ctx, seed))
    return (h.astype(np.float64) / 2**32).astype(np.float32)


@_register("noise", "grain(seed=0)",
           "Fixed white noise, one value [0, 1) per pixel (or per particle): twinkle, "
           "dust.",
           contextual=True)
def _grain(ctx, seed=0):
    shape = ctx.shape
    index = np.arange(int(np.prod(shape)), dtype=np.int64).reshape(shape)
    h = nz._hash([index, np.zeros(shape, dtype=np.int64)], _seed(ctx, seed) + 17)
    return (h.astype(np.float64) / 2**32).astype(np.float32)


# ----------------------------------------------------------------------- time

@_register("time", "env(v, attack, hold=0, release=None)",
           "Envelope 0 → 1 → 0: rises until `attack`, holds `hold`, falls during `release` "
           "(the rest of the time by default). v is usually t.")
def _env(v, attack, hold=0.0, release=None):
    attack = max(float(attack), 1e-6)
    if release is None:
        release = max(1.0 - attack - hold, 1e-6)
    rise = np.clip(v / attack, 0, 1)
    fall = 1 - np.clip((v - attack - hold) / max(float(release), 1e-6), 0, 1)
    return np.minimum(rise, fall)


@_register("time", "ease_in(v, p=2)", "Accelerate: v^p on [0, 1].")
def _ease_in(v, p=2.0):
    return np.clip(v, 0, 1) ** p


@_register("time", "ease_out(v, p=2)", "Decelerate: 1 - (1 - v)^p. The expansion of a wave, of "
                                        "an explosion.")
def _ease_out(v, p=2.0):
    return 1 - (1 - np.clip(v, 0, 1)) ** p


@_register("time", "ease_inout(v, p=2)", "Accelerate then decelerate.")
def _ease_inout(v, p=2.0):
    v = np.clip(v, 0, 1)
    return np.where(v < 0.5, 0.5 * (2 * v) ** p, 1 - 0.5 * (2 - 2 * v) ** p)


@_register("time", "spring(v, freq=3, damp=5)", "Damped overshoot toward 1: an impact that "
                                                 "bounces, a shield that opens.")
def _spring(v, freq=3.0, damp=5.0):
    v = np.maximum(v, 0)
    return 1 - np.exp(-damp * v) * np.cos(2 * np.pi * freq * v)


@_register("time", "pulse(v, center, width)", "Bell centered on `center`, of width `width`: "
                                               "a flash, a wavefront.")
def _pulse(v, center, width):
    k = (v - center) / max(float(np.mean(width)), 1e-6)
    return np.exp(-k * k * 4)


@_register("time", "tri(v)", "Periodic triangle 0 → 1 → 0 over each unit.")
def _tri(v):
    return 1 - np.abs(np.mod(v, 1.0) * 2 - 1)


@_register("time", "loop(t, k=1)", "Returns (cos, sin) of k full turns over t: a circular "
                                    "path that loops.")
def _loop(t, k=1.0):
    return np.cos(2 * np.pi * k * t), np.sin(2 * np.pi * k * t)


# ---------------------------------------------------------------------- color

def parse_color(text: str) -> tuple[float, float, float, float]:
    """`#rgb`, `#rrggbb` or `#rrggbbaa` -> (r, g, b, a) in [0, 1]."""
    value = text.strip().lstrip("#")
    if len(value) in (3, 4):
        value = "".join(ch * 2 for ch in value)
    if len(value) == 6:
        value += "ff"
    if len(value) != 8:
        raise ExprError(f"unreadable color: {text!r} (expected #rrggbb or #rrggbbaa)")
    try:
        channels = [int(value[i:i + 2], 16) / 255.0 for i in range(0, 8, 2)]
    except ValueError as exc:
        raise ExprError(f"unreadable color: {text!r}") from exc
    return channels[0], channels[1], channels[2], channels[3]


def ramp_stops(stops: list[tuple[float, str]] | list[str]) -> tuple[np.ndarray, np.ndarray]:
    """A ramp's stops: increasing positions and RGBA colors."""
    if not stops:
        raise ExprError("a ramp needs at least one color")
    if all(isinstance(stop, str) for stop in stops):
        count = len(stops)
        stops = [(i / max(count - 1, 1), stop) for i, stop in enumerate(stops)]
    positions, colors = [], []
    for stop in stops:
        if not isinstance(stop, (list, tuple)) or len(stop) != 2:
            raise ExprError(f"unreadable ramp stop: {stop!r} "
                            "(expected [position, \"#color\"])")
        positions.append(float(stop[0]))
        colors.append(parse_color(str(stop[1])))
    order = np.argsort(positions)
    return np.asarray(positions, np.float32)[order], np.asarray(colors, np.float32)[order]


def apply_ramp(value: np.ndarray, positions: np.ndarray, colors: np.ndarray
               ) -> tuple[np.ndarray, ...]:
    flat = np.asarray(value, np.float32)
    return tuple(np.interp(flat, positions, colors[:, c]).astype(np.float32) for c in range(4))


@_register("color", "rgb(\"#hex\")", "A constant color: returns (r, g, b, a).")
def _rgb(text):
    if not isinstance(text, str):
        raise ExprError("rgb() expects a string \"#rrggbb\"")
    return tuple(np.float32(c) for c in parse_color(text))


@_register("color", "ramp(v, \"#c0\", \"#c1\", ...)",
           "Gradient: v=0 gives the first color, v=1 the last, stops evenly spread. Returns (r, "
           "g, b, a).")
def _ramp(v, *colors):
    if not colors or not all(isinstance(c, str) for c in colors):
        raise ExprError("ramp(v, \"#c0\", \"#c1\", ...) expects colors as strings")
    positions, rgba = ramp_stops(list(colors))
    return apply_ramp(v, positions, rgba)


@_register("color", "hsv(h, s, v)", "Hue (in turns), saturation, value → (r, g, b).")
def _hsv(h, s, v):
    h = np.mod(h, 1.0) * 6
    i = np.floor(h)
    f = h - i
    p, q, tt = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    i = np.mod(i, 6)
    r = np.select([i == 0, i == 1, i == 2, i == 3, i == 4], [v, q, p, p, tt], v)
    g = np.select([i == 0, i == 1, i == 2, i == 3, i == 4], [tt, v, v, q, p], p)
    b = np.select([i == 0, i == 1, i == 2, i == 3, i == 4], [p, p, tt, v, v], q)
    return r, g, b


@_register("color", "lum(r, g, b)", "Perceived luminance of a color.")
def _lum(r, g, b):
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@_register("color", "tex(\"name\", u, v, wrap=1)",
           "Sample an input image (declared in `inputs`) at (u, v) ∈ [0, 1], v pointing "
           "up. "
           "wrap=1 repeats the image, wrap=0 borders it with transparency. Returns (r, g, b, a). "
           "This is the door for a painted or generated material: warp it, scroll it, "
           "dissolve it.",
           contextual=True)
def _tex(ctx, name, u, v, wrap=1):
    image = ctx.inputs.get(name) if isinstance(name, str) else None
    if image is None:
        known = ", ".join(sorted(ctx.inputs)) or "none"
        raise ExprError(f"unknown input image: {name!r} (declared: {known})")
    return sample(image, u, v, wrap=bool(wrap))


def sample(image: np.ndarray, u: Any, v: Any, *, wrap: bool = True) -> tuple[np.ndarray, ...]:
    """Bilinear sampling of an RGBA image (H, W, 4) at [0, 1]."""
    height, width = image.shape[:2]
    u_arr, v_arr = np.broadcast_arrays(np.asarray(u, np.float32), np.asarray(v, np.float32))
    fx = u_arr * width - 0.5
    fy = (1.0 - v_arr) * height - 0.5
    x0, y0 = np.floor(fx), np.floor(fy)
    ax, ay = fx - x0, fy - y0
    x0, y0 = x0.astype(np.int64), y0.astype(np.int64)
    out = np.zeros((*u_arr.shape, 4), np.float32)
    for dx, dy, weight in ((0, 0, (1 - ax) * (1 - ay)), (1, 0, ax * (1 - ay)),
                           (0, 1, (1 - ax) * ay), (1, 1, ax * ay)):
        xi, yi = x0 + dx, y0 + dy
        if wrap:
            texel = image[np.mod(yi, height), np.mod(xi, width)]
        else:
            valid = (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
            texel = image[np.clip(yi, 0, height - 1), np.clip(xi, 0, width - 1)]
            texel = texel * valid[..., None]
        out += texel * weight[..., None]
    return tuple(out[..., c] for c in range(4))


# ------------------------------------------------------------------ evaluator

@dataclass
class EvalContext:
    """What the contextual functions need."""

    seed: int = 0
    shape: tuple[int, ...] = ()
    inputs: dict[str, np.ndarray] = field(default_factory=dict)


def _divide(a, b):
    # A division by zero would give inf then NaN through the whole chain: it is
    # brought to the smallest denominator of the same sign.
    b = np.asarray(b, np.float32)
    safe = np.where(np.abs(b) < 1e-12, np.where(b < 0, -1e-12, 1e-12), b)
    return a / safe


def _power(a, b):
    # A fractional exponent of a negative base has no real value.
    if np.ndim(b) == 0 and float(b).is_integer():
        return np.power(a, b)
    return np.power(np.maximum(a, 0), b)


_BINOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: _divide,
    ast.Pow: _power,
    ast.Mod: lambda a, b: np.mod(a, b),
    ast.FloorDiv: lambda a, b: np.floor_divide(a, b),
}

_CMPOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Lt: np.less, ast.LtE: np.less_equal, ast.Gt: np.greater,
    ast.GtE: np.greater_equal, ast.Eq: np.equal, ast.NotEq: np.not_equal,
}

_ALLOWED = (ast.Module, ast.Assign, ast.Expr, ast.BinOp, ast.UnaryOp, ast.Compare,
            ast.Call, ast.Name, ast.Load, ast.Store, ast.Constant, ast.Tuple, ast.IfExp,
            ast.keyword, ast.USub, ast.UAdd, *_BINOPS, *_CMPOPS)


@dataclass
class Program:
    """Effect code parsed and validated, ready to be evaluated every frame."""

    source: str
    tree: ast.Module
    # Names read without being assigned first: what the environment must provide.
    free_names: set[str]

    # Names the code assigns: what a `defs` block exports.
    assigned: set[str] = field(default_factory=set)

    def scope(self, env: dict[str, Any], ctx: EvalContext) -> tuple[Any, dict[str, Any]]:
        """Evaluate the code; return the final value (or None) and the assigned names."""
        scope = dict(env)
        result: Any = None
        for statement in self.tree.body:
            if isinstance(statement, ast.Assign):
                value = _eval(statement.value, scope, ctx)
                for target in statement.targets:
                    _bind(target, value, scope)
                result = None
            else:
                result = _eval(statement.value, scope, ctx)
        return result, {name: scope[name] for name in self.assigned}

    def run(self, env: dict[str, Any], ctx: EvalContext) -> Any:
        result, _ = self.scope(env, ctx)
        if result is None:
            raise ExprError("the code must end with an expression (the returned value)")
        return result


def compile_code(source: str | float | int, *, where: str = "", result: bool = True) -> Program:
    """Parse effect code; refuse anything that is not computation.

    `result=False`: a block of assignments only (the shared `defs`).
    """
    label = f" ({where})" if where else ""
    if isinstance(source, (int, float)) and not isinstance(source, bool):
        source = repr(float(source))
    if not isinstance(source, str) or not source.strip():
        raise ExprError(f"empty code{label}")
    if len(source) > MAX_SOURCE:
        raise ExprError(f"code too long{label}: {len(source)} characters (max {MAX_SOURCE})")
    try:
        tree = ast.parse(source.strip(), mode="exec")
    except SyntaxError as exc:
        raise ExprError(f"syntax{label}, line {exc.lineno}: {exc.msg}") from exc

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise ExprError(f"code too complex{label}")
    for node in nodes:
        if not isinstance(node, _ALLOWED):
            raise ExprError(f"construct refused{label}, line {getattr(node, 'lineno', '?')}: "
                            f"{type(node).__name__} -- only computation, assignments and the "
                            "language's functions are allowed")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, str)):
            raise ExprError(f"constant refused{label}: {node.value!r}")
        if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
            raise ExprError(f"call refused{label}: only the language's functions can be called")
        if isinstance(node, ast.Call) and node.func.id not in FUNCTIONS:
            raise ExprError(f"unknown function{label}: {node.func.id}()")
    body = tree.body
    if not body:
        raise ExprError(f"empty code{label}")
    for statement in body[:-1]:
        if not isinstance(statement, ast.Assign):
            raise ExprError(f"line {statement.lineno}{label}: "
                            "only the last line is an expression")
    if result and not isinstance(body[-1], ast.Expr):
        raise ExprError(f"the code{label} must end with an expression (the returned value)")
    for statement in body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                for node in ast.walk(target):
                    if isinstance(node, ast.Name) and node.id in FUNCTIONS:
                        raise ExprError(f"“{node.id}” is a function of the language{label}")

    assigned: set[str] = set()
    free: set[str] = set()
    for statement in body:
        value = statement.value
        for node in ast.walk(value):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                    and node.id not in assigned and not _is_callee(value, node):
                free.add(node.id)
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                for node in ast.walk(target):
                    if isinstance(node, ast.Name):
                        assigned.add(node.id)
    return Program(source=source, tree=tree, free_names=free, assigned=assigned)


def _is_callee(root: ast.AST, name: ast.Name) -> bool:
    return any(isinstance(node, ast.Call) and node.func is name for node in ast.walk(root))


def _bind(target: ast.AST, value: Any, scope: dict[str, Any]) -> None:
    if isinstance(target, ast.Name):
        scope[target.id] = value
        return
    if isinstance(target, ast.Tuple):
        if not isinstance(value, tuple) or len(value) != len(target.elts):
            count = len(value) if isinstance(value, tuple) else 1
            raise ExprError(f"line {target.lineno}: {len(target.elts)} names "
                            f"for {count} value(s)")
        for element, item in zip(target.elts, value, strict=True):
            _bind(element, item, scope)
        return
    raise ExprError(f"line {target.lineno}: assignment refused")


def _eval(node: ast.AST, scope: dict[str, Any], ctx: EvalContext) -> Any:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else np.float32(node.value)
    if isinstance(node, ast.Name):
        if node.id in scope:
            return scope[node.id]
        raise ExprError(f"line {node.lineno}: unknown name “{node.id}”")
    if isinstance(node, ast.Tuple):
        return tuple(_eval(element, scope, ctx) for element in node.elts)
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, scope, ctx)
        return _broadcast_tuple(lambda v: -v if isinstance(node.op, ast.USub) else v, operand)
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, scope, ctx), _eval(node.right, scope, ctx)
        op = _BINOPS[type(node.op)]
        return _binary(op, left, right, node)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, scope, ctx)
        result: Any = None
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator, scope, ctx)
            step = _CMPOPS[type(op)](left, right)
            result = step if result is None else np.logical_and(result, step)
            left = right
        return np.asarray(result, np.float32)
    if isinstance(node, ast.IfExp):
        cond = _eval(node.test, scope, ctx)
        a, b = _eval(node.body, scope, ctx), _eval(node.orelse, scope, ctx)
        if isinstance(a, tuple) or isinstance(b, tuple):
            pairs = zip(_tup(a, b), _tup(b, a), strict=True)
            return tuple(np.where(np.asarray(cond) > 0.5, x, y) for x, y in pairs)
        return np.where(np.asarray(cond) > 0.5, a, b)
    if isinstance(node, ast.Call):
        function = FUNCTIONS[node.func.id]
        args = [_eval(arg, scope, ctx) for arg in node.args]
        kwargs = {kw.arg: _eval(kw.value, scope, ctx) for kw in node.keywords if kw.arg}
        try:
            with np.errstate(all="ignore"):
                if function.contextual:
                    return _f(function.impl(ctx, *args, **kwargs))
                return _f(function.impl(*args, **kwargs))
        except ExprError:
            raise
        except TypeError as exc:
            raise ExprError(f"line {node.lineno}: {function.signature} -- {exc}") from exc
        except ValueError as exc:
            raise ExprError(f"line {node.lineno}: {function.name}() -- {exc}") from exc
    raise ExprError(f"construct cannot be evaluated: {type(node).__name__}")


def _tup(a: Any, b: Any) -> tuple:
    if isinstance(a, tuple):
        return a
    return tuple(a for _ in b)


def _broadcast_tuple(fn: Callable[[Any], Any], value: Any) -> Any:
    if isinstance(value, tuple):
        return tuple(fn(v) for v in value)
    return fn(value)


def _binary(op: Callable[[Any, Any], Any], left: Any, right: Any, node: ast.AST) -> Any:
    if isinstance(left, str) or isinstance(right, str):
        raise ExprError(f"line {node.lineno}: a string cannot be computed")
    if isinstance(left, tuple) or isinstance(right, tuple):
        if isinstance(left, tuple) and isinstance(right, tuple) and len(left) != len(right):
            raise ExprError(f"line {node.lineno}: tuples of different sizes")
        pairs = zip(_tup(left, right), _tup(right, left), strict=True)
        return tuple(_binary(op, a, b, node) for a, b in pairs)
    with np.errstate(all="ignore"):
        return op(left, right)


# ------------------------------------------------------------------ reference

VARIABLES: dict[str, str] = {
    "x, y": "Pixel position. The image center is (0, 0), the short side goes from -1 to 1, y "
            "pointing up.",
    "u, v": "Position in [0, 1], v pointing up.",
    "r, a": "Radius from the center, and angle in turns in [0, 1) (0 = right, "
            "counterclockwise).",
    "t": "Normalized time in [0, 1) over the effect's duration. A loop passes through 0 "
         "seamlessly.",
    "time, duration": "Time and duration in seconds.",
    "frame, frames, fps": "Frame index, number of frames, frame rate.",
    "px": "Size of a pixel in x units: the minimum softness of a sharp edge.",
    "pi, tau": "π and 2π.",
}


def reference() -> str:
    """The language reference, in Markdown, drawn from the function registry."""
    lines = ["# The effects language", "",
             "Each code is a sequence of assignments (`name = expression`, or "
             "`a, b = function(...)`) ending with an expression: the returned value. "
             "Operators: `+ - * / ** % //`, comparisons (return 0 or 1), "
             "`a if condition else b`. Everything is computed over the whole grid.", "",
             "## Variables", ""]
    lines += [f"- `{name}` — {doc}" for name, doc in VARIABLES.items()]
    for category, title in CATEGORIES.items():
        lines += ["", f"## {title}", ""]
        for function in FUNCTIONS.values():
            if function.category == category:
                lines.append(f"- `{function.signature}` — {function.doc}")
    return "\n".join(lines) + "\n"


def constants() -> dict[str, Any]:
    return {"pi": np.float32(math.pi), "tau": np.float32(2 * math.pi)}
