"""Procedural noises: the raw material of every effect.

A fire, a smoke, a water, a plasma or an aura do not differ by the noise they
use, but by what is done with it: scale, speed, domain warping, threshold,
color ramp. This module therefore only provides neutral primitives, vectorized
with numpy, without any dependency:

- `gradient`: gradient noise (Perlin family) in 2, 3 or 4 dimensions;
- `fractal`: a sum of octaves (fbm), smooth, ridged or turbulent;
- `cellular`: cellular noise (Worley) -- distance to the nearest seed point,
  to the second nearest, and a random value per cell.

**Tiling is what makes an effect loop.** Each axis can take an integer period
(in lattice cells): the noise repeats exactly after it. Scrolling a tiled axis
by one full period over the clip's duration, or rotating time on a tiled axis,
gives a seamless loop -- the only way to loop a material that *evolves*,
instead of cross-fading it.

Returned values are normalized: `gradient` and `fractal` in [-1, 1] (roughly,
the actual amplitude stays a bit inside), `cellular` in cell distances.
"""

from __future__ import annotations

from itertools import product

import numpy as np

# Hashing constants: large odd numbers, one per axis.
_PRIMES = (0x8DA6B343, 0xD8163841, 0xCB1AB31F, 0x9E3779B1)
# The raw gradient is not unit-length: this factor brings the observed
# amplitude back to [-1, 1] (measured once, per dimension).
_SCALE = {2: 1.9, 3: 1.7, 4: 1.45}


def _hash(cells: list[np.ndarray], seed: int) -> np.ndarray:
    """One pseudo-random integer per lattice cell (uint32)."""
    h = np.full(cells[0].shape, (seed * 0x27D4EB2D + 0x165667B1) & 0xFFFFFFFF, dtype=np.uint32)
    for axis, cell in enumerate(cells):
        h ^= (cell.astype(np.int64) * _PRIMES[axis] & 0xFFFFFFFF).astype(np.uint32)
        # xorshift-multiply mixing: each axis must disturb every bit.
        h ^= h >> np.uint32(15)
        h *= np.uint32(0x2C1B3C6D)
        h ^= h >> np.uint32(12)
    h *= np.uint32(0x297A2D39)
    h ^= h >> np.uint32(15)
    return h


def _wrap(cell: np.ndarray, period: int) -> np.ndarray:
    return np.mod(cell, period) if period > 0 else cell


def _fade(t: np.ndarray) -> np.ndarray:
    # Quintic: zero first and second derivatives at the nodes, no visible edge.
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _coords(coords: tuple[np.ndarray | float, ...]) -> list[np.ndarray]:
    arrays = [np.asarray(c, dtype=np.float32) for c in coords]
    shape = np.broadcast_shapes(*(a.shape for a in arrays))
    return [np.broadcast_to(a, shape) for a in arrays]


def gradient(coords: tuple[np.ndarray | float, ...], *, periods: tuple[int, ...] = (),
             seed: int = 0) -> np.ndarray:
    """Gradient noise in N dimensions (2 to 4), in [-1, 1].

    `periods`: one integer period per axis (0 = no tiling).
    """
    points = _coords(coords)
    dims = len(points)
    periods = tuple(int(round(p)) for p in periods) + (0,) * (dims - len(periods))
    base = [np.floor(p) for p in points]
    frac = [p - b for p, b in zip(points, base, strict=True)]
    base_i = [b.astype(np.int64) for b in base]
    fades = [_fade(f) for f in frac]

    total = np.zeros(points[0].shape, dtype=np.float32)
    for corner in product((0, 1), repeat=dims):
        cells = [_wrap(b + c, periods[axis])
                 for axis, (b, c) in enumerate(zip(base_i, corner, strict=True))]
        h = _hash(cells, seed)
        # One gradient per vertex: one component per byte of the hash, in [-1, 1].
        dot = np.zeros_like(total)
        for axis in range(dims):
            byte = (h >> np.uint32(8 * axis)) & np.uint32(255)
            component = byte.astype(np.float32) / 127.5 - 1.0
            dot += component * (frac[axis] - corner[axis])
        weight = np.ones_like(total)
        for axis in range(dims):
            weight *= fades[axis] if corner[axis] else 1.0 - fades[axis]
        total += weight * dot
    return np.clip(total * _SCALE.get(dims, 1.0), -1.0, 1.0)


def fractal(coords: tuple[np.ndarray | float, ...], *, octaves: int = 5,
            lacunarity: float = 2.0, gain: float = 0.5, mode: str = "fbm",
            periods: tuple[int, ...] = (), seed: int = 0) -> np.ndarray:
    """A sum of `gradient` octaves.

    - `fbm`: smooth, in [-1, 1];
    - `ridged`: thin ridges (1 - |n|, squared), in [0, 1] -- lightning,
      veins, cracks, filaments;
    - `turbulence`: accumulated |n|, in [0, 1] -- flames, boiling swirls.

    With tiling, lacunarity is rounded to an integer: an octave whose period
    was not an integer would break the loop.
    """
    octaves = max(1, min(int(octaves), 10))
    points = _coords(coords)
    if periods:
        lacunarity = float(max(1, round(lacunarity)))
    total = np.zeros(points[0].shape, dtype=np.float32)
    norm = 0.0
    amplitude, frequency = 1.0, 1.0
    for octave in range(octaves):
        scaled = tuple(p * frequency for p in points)
        octave_periods = tuple(int(round(p * frequency)) for p in periods)
        n = gradient(scaled, periods=octave_periods, seed=seed + octave * 101)
        if mode == "ridged":
            n = (1.0 - np.abs(n)) ** 2
        elif mode == "turbulence":
            n = np.abs(n)
        total += amplitude * n
        norm += amplitude
        amplitude *= gain
        frequency *= lacunarity
    return total / norm


def cellular(coords: tuple[np.ndarray | float, ...], *, periods: tuple[int, ...] = (),
             jitter: float = 1.0, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cellular noise in 2 or 3 dimensions.

    Returns `(f1, f2, cell)`: distance to the nearest seed point, to the second
    nearest, and a random value in [0, 1] specific to the nearest cell. `f2 -
    f1` draws the edges between cells (cracks, scales, caustics).
    """
    points = _coords(coords)
    dims = len(points)
    if dims not in (2, 3):
        raise ValueError("cellular noise takes 2 or 3 coordinates")
    periods = tuple(int(round(p)) for p in periods) + (0,) * (dims - len(periods))
    base = [np.floor(p).astype(np.int64) for p in points]
    frac = [p - np.floor(p) for p in points]
    f1 = np.full(points[0].shape, 9.0, dtype=np.float32)
    f2 = np.full_like(f1, 9.0)
    cell_value = np.zeros_like(f1)
    for offset in product((-1, 0, 1), repeat=dims):
        cells = [_wrap(b + o, periods[axis])
                 for axis, (b, o) in enumerate(zip(base, offset, strict=True))]
        h = _hash(cells, seed)
        distance = np.zeros_like(f1)
        for axis in range(dims):
            feature = ((h >> np.uint32(8 * axis)) & np.uint32(255)).astype(np.float32) / 255.0
            delta = offset[axis] + 0.5 + (feature - 0.5) * jitter - frac[axis]
            distance += delta * delta
        distance = np.sqrt(distance)
        value = ((h >> np.uint32(24)) & np.uint32(255)).astype(np.float32) / 255.0
        closer = distance < f1
        f2 = np.where(closer, f1, np.minimum(f2, distance))
        cell_value = np.where(closer, value, cell_value)
        f1 = np.where(closer, distance, f1)
    return f1, f2, cell_value


def random_values(count: int, seed: int, stream: int) -> np.ndarray:
    """`count` values in [0, 1), reproducible by (seed, stream)."""
    index = np.arange(count, dtype=np.int64)
    h = _hash([index, np.full(count, stream, dtype=np.int64)], seed)
    return (h.astype(np.float64) / 2**32).astype(np.float32)
