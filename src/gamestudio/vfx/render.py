"""Rendering an effect: composited layers, post-processing, a sheet.

Everything is computed in premultiplied float, and may exceed 1: an
overexposed layer (`intensity` > 1) is what feeds the bloom, as in an engine.
The move to 8 bits happens at the very end, straight alpha, for Godot.

Two studio rules apply here as they do to sprites (`sprites.py`): **cropping
and palette are computed once for all frames.** One box per frame would make
the effect jitter around its pivot; one palette per frame would make its
colors flicker.
"""

from __future__ import annotations

import io
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from .expr import EvalContext, ExprError, apply_ramp
from .sim import grid_env, particle_frames, simulate_fluid, splat, time_env
from .spec import ColorSpec, EffectSpec, Layer

Progress = Callable[[str, float], None]


@dataclass
class Rendered:
    """An effect's frames, ready to be assembled."""

    spec: EffectSpec
    frames: np.ndarray                  # (F, H, W, 4) uint8, straight alpha
    origin: tuple[float, float]         # where the effect's (0, 0) falls, in frame pixels
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def width(self) -> int:
        return int(self.frames.shape[2])

    @property
    def height(self) -> int:
        return int(self.frames.shape[1])


# ---------------------------------------------------------------------- color

def _color(color: ColorSpec, alpha: np.ndarray, env: dict[str, Any], ctx: EvalContext,
           where: str) -> tuple[np.ndarray, np.ndarray]:
    """A layer's color: return (rgb (..., 3), alpha multiplier)."""
    shape = alpha.shape
    if color.kind == "solid":
        r, g, b, a = color.rgba
        rgb = np.empty((*shape, 3), np.float32)
        rgb[..., 0], rgb[..., 1], rgb[..., 2] = r, g, b
        return rgb, np.float32(a)
    if color.kind == "ramp":
        driver = alpha if color.by is None else color.by.run(env, ctx)
        if isinstance(driver, tuple):
            raise ExprError(f"{where}.by: one value is expected")
        channels = apply_ramp(np.broadcast_to(driver, shape), color.positions, color.colors)
        return np.stack(channels[:3], axis=-1), channels[3]
    value = color.code.run(env, ctx)
    if not isinstance(value, tuple) or len(value) not in (3, 4):
        raise ExprError(f"{where}: color code returns (r, g, b) or (r, g, b, a)")
    channels = [np.broadcast_to(np.asarray(c, np.float32), shape) for c in value]
    return np.stack(channels[:3], axis=-1), (channels[3] if len(channels) == 4 else np.float32(1))


def _composite(canvas: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, blend: str) -> None:
    """Lay a premultiplied layer on the canvas, in place."""
    a = alpha[..., None]
    if blend == "normal":
        canvas[..., :3] = rgb + canvas[..., :3] * (1 - a)
        canvas[..., 3] = alpha + canvas[..., 3] * (1 - alpha)
    elif blend == "add":
        canvas[..., :3] += rgb
        canvas[..., 3] = np.clip(canvas[..., 3] + alpha, 0, 1)
    elif blend == "screen":
        base = canvas[..., :3]
        canvas[..., :3] = base + rgb - base * np.clip(rgb, 0, 1)
        canvas[..., 3] = alpha + canvas[..., 3] * (1 - alpha)
    elif blend == "multiply":
        straight = rgb / np.maximum(a, 1e-6)
        canvas[..., :3] = canvas[..., :3] * (1 - a + straight * a)
    elif blend == "mask":
        canvas *= a
    elif blend == "erase":
        canvas *= 1 - a


# -------------------------------------------------------------------- post

def _box_blur(image: np.ndarray, radius: int, axis: int) -> np.ndarray:
    if radius < 1:
        return image
    pad = [(0, 0)] * image.ndim
    pad[axis] = (radius + 1, radius)
    padded = np.pad(image, pad, mode="constant")
    summed = np.cumsum(padded, axis=axis, dtype=np.float32)
    size = image.shape[axis]
    upper = np.take(summed, np.arange(2 * radius + 1, 2 * radius + 1 + size), axis=axis)
    lower = np.take(summed, np.arange(0, size), axis=axis)
    return (upper - lower) / (2 * radius + 1)


def blur(image: np.ndarray, radius: float) -> np.ndarray:
    """Near-Gaussian blur: three box passes per axis."""
    if radius <= 0:
        return image
    box = max(1, int(round(radius / math.sqrt(3))))
    out = image
    for _ in range(3):
        out = _box_blur(_box_blur(out, box, out.ndim - 3), box, out.ndim - 2)
    return out


def _straight(frames: np.ndarray) -> np.ndarray:
    alpha = frames[..., 3:4]
    return np.where(alpha > 1e-6, frames[..., :3] / np.maximum(alpha, 1e-6), 0)


def _nearest(colors: np.ndarray, palette: np.ndarray) -> np.ndarray:
    """The nearest palette color for each color, in chunks."""
    flat = colors.reshape(-1, 3)
    out = np.empty_like(flat)
    for start in range(0, flat.shape[0], 262144):
        chunk = flat[start:start + 262144]
        distance = ((chunk[:, None, :] - palette[None, :, :]) ** 2).sum(axis=2)
        out[start:start + 262144] = palette[np.argmin(distance, axis=1)]
    return out.reshape(colors.shape)


def _shared_palette(frames: np.ndarray, count: int) -> np.ndarray:
    """One palette for all frames, drawn from the visible pixels of all of them."""
    straight = np.clip(_straight(frames), 0, 1)
    visible = straight[frames[..., 3] > 0.05]
    if visible.size == 0:
        return np.zeros((1, 3), np.float32)
    rng = np.random.default_rng(0)
    if visible.shape[0] > 200_000:
        visible = visible[rng.choice(visible.shape[0], 200_000, replace=False)]
    strip = Image.fromarray((visible[None, :, :] * 255).astype(np.uint8), "RGB")
    quantized = strip.quantize(colors=max(2, min(int(count), 256)), method=Image.Quantize.MEDIANCUT)
    entries = quantized.getpalette()[: 3 * max(2, int(count))]
    palette = np.asarray(entries, np.float32).reshape(-1, 3)
    return palette / 255.0


def _post(frames: np.ndarray, spec: EffectSpec, scale: float, notes: list[str]) -> np.ndarray:
    for op, value in spec.post:
        options = value if isinstance(value, dict) else {}
        if op == "exposure":
            frames[..., :3] *= float(value)
        elif op == "tonemap":
            white = float(value or 1.0)
            rgb = frames[..., :3]
            frames[..., :3] = rgb * (1 + rgb / (white * white)) / (1 + rgb)
        elif op == "blur":
            frames = blur(frames, float(value) * scale)
        elif op == "bloom":
            threshold = float(options.get("threshold", 0.8))
            radius = float(options.get("radius", 8)) * scale
            strength = float(options.get("strength", 0.8))
            bright = np.maximum(frames[..., :3] - threshold * np.maximum(frames[..., 3:4], 1e-6), 0)
            halo = blur(bright, radius) * strength
            frames[..., :3] += halo
            lum = halo.max(axis=-1)
            frames[..., 3] = np.clip(frames[..., 3] + lum * (1 - frames[..., 3]), 0, 1)
        elif op == "pixelate":
            step = max(1, int(round(float(value) * scale)))
            f, h, w, c = frames.shape
            hh, ww = h // step, w // step
            if hh and ww:
                cropped = frames[:, :hh * step, :ww * step]
                small = cropped.reshape(f, hh, step, ww, step, c).mean(axis=(2, 4))
                big = np.repeat(np.repeat(small, step, axis=1), step, axis=2)
                frames = np.zeros_like(frames)
                frames[:, :hh * step, :ww * step] = big
        elif op in ("quantize", "palette"):
            if op == "palette":
                palette = np.asarray([c[:3] for c in value], np.float32)
            else:
                count = int(options.get("colors", value if not isinstance(value, dict) else 8))
                palette = _shared_palette(frames, count)
            alpha = np.clip(frames[..., 3:4], 0, 1)
            mapped = _nearest(np.clip(_straight(frames), 0, 1), palette)
            frames = np.concatenate([mapped * alpha, alpha], axis=-1)
            notes.append(f"palette of {len(palette)} colors, shared by all frames")
        elif op == "alpha_steps":
            steps = max(2, int(value))
            alpha = np.clip(frames[..., 3:4], 0, 1)
            stepped = np.round(alpha * (steps - 1)) / (steps - 1)
            straight = _straight(frames)
            frames = np.concatenate([straight * stepped, stepped], axis=-1)
    return frames


# --------------------------------------------------------------------- render

def render(spec: EffectSpec, inputs: dict[str, np.ndarray] | None = None, *,
           scale: float = 1.0, progress: Progress | None = None) -> Rendered:
    """Render every frame of an effect. `scale` < 1: reduced preview, same timing."""
    started = time.monotonic()
    scale = float(min(max(scale, 0.1), 1.0))
    width = max(8, int(round(spec.width * scale)))
    height = max(8, int(round(spec.height * scale)))
    shape = (height, width)
    ctx = EvalContext(seed=spec.seed, shape=shape, inputs=dict(inputs or {}))
    grid = grid_env(width, height)
    notes: list[str] = []

    def shared(env: dict[str, Any]) -> dict[str, Any]:
        if spec.defs is None:
            return {}
        local = EvalContext(seed=ctx.seed, shape=np.shape(env["x"]), inputs=ctx.inputs)
        _, names = spec.defs.scope(env, local)
        return names

    # Simulations run first, each over its whole duration.
    fluids: dict[str, list[dict[str, np.ndarray]]] = {}
    particles: dict[str, list[dict[str, np.ndarray]]] = {}
    for layer in spec.layers:
        if progress:
            progress(f"simulation {layer.name}", 0.0)
        if layer.type == "fluid":
            fluids[layer.name] = simulate_fluid(layer, spec, ctx, shared, scale=scale)
        elif layer.type == "particles":
            particles[layer.name] = particle_frames(layer, spec, ctx)

    frames = np.zeros((spec.frames, height, width, 4), np.float32)
    for index in range(spec.frames):
        env: dict[str, Any] = {**grid, **time_env(spec, index)}
        env.update(shared(env))
        canvas = frames[index]
        for layer in spec.layers:
            where = f"layers.{layer.name}"
            if layer.type == "particles":
                rgb, alpha = _particle_layer(layer, particles[layer.name][index], env, ctx,
                                             width, height, scale, where)
            else:
                layer_env = env
                if layer.type == "fluid":
                    layer_env = {**env, **_upsample(fluids[layer.name][index], shape)}
                alpha = layer.alpha.run(layer_env, ctx)
                if isinstance(alpha, tuple):
                    raise ExprError(f"{where}.alpha: one value is expected, not a tuple")
                alpha = np.clip(np.broadcast_to(np.asarray(alpha, np.float32), shape), 0, 1)
                color, alpha_mul = _color(layer.color, alpha, layer_env, ctx, f"{where}.color")
                alpha = np.clip(alpha * alpha_mul, 0, 1)
                rgb = color * alpha[..., None]
            env[layer.name] = alpha
            _composite(canvas, rgb * layer.intensity, alpha, layer.blend)
        if progress:
            progress("render", (index + 1) / spec.frames)

    frames = np.nan_to_num(frames, nan=0.0, posinf=1.0, neginf=0.0)
    frames = _post(frames, spec, scale, notes)
    frames[..., 3] = np.clip(frames[..., 3], 0, 1)
    straight = np.clip(_straight(frames), 0, 1)
    out = np.concatenate([straight, frames[..., 3:4]], axis=-1)
    pixels = np.round(out * 255).astype(np.uint8)
    origin = (width / 2, height / 2)
    if not pixels[..., 3].any():
        notes.append("every frame is transparent: no layer covers anything")
    return Rendered(spec=spec, frames=pixels, origin=origin,
                    seconds=time.monotonic() - started, notes=notes)


def _upsample(state: dict[str, np.ndarray], shape: tuple[int, int]) -> dict[str, np.ndarray]:
    """Bring a fluid's fields from the grid to the image (bilinear)."""
    out = {}
    for key, values in state.items():
        image = Image.fromarray(values.astype(np.float32), "F")
        out[key] = np.asarray(image.resize((shape[1], shape[0]), Image.Resampling.BILINEAR),
                              np.float32)
    return out


def _particle_layer(layer: Layer, state: dict[str, np.ndarray], env: dict[str, Any],
                    ctx: EvalContext, width: int, height: int, scale: float, where: str
                    ) -> tuple[np.ndarray, np.ndarray]:
    if not state:
        return np.zeros((height, width, 3), np.float32), np.zeros((height, width), np.float32)
    count = state["x"].shape[0]
    local = EvalContext(seed=ctx.seed, shape=(count,), inputs=ctx.inputs)
    penv = {key: value for key, value in env.items() if np.ndim(value) == 0}
    penv.update(state)
    alpha = layer.alpha.run(penv, local)
    size = layer.params["size"].run(penv, local)
    if isinstance(alpha, tuple) or isinstance(size, tuple):
        raise ExprError(f"{where}: alpha and size return one value per particle")
    alpha = np.clip(np.broadcast_to(np.asarray(alpha, np.float32), (count,)), 0, 1)
    size = np.broadcast_to(np.asarray(size, np.float32), (count,))
    if layer.color.kind == "ramp" and layer.color.by is None:
        channels = apply_ramp(state["life"], layer.color.positions, layer.color.colors)
        color, alpha_mul = np.stack(channels[:3], axis=-1), channels[3]
    else:
        color, alpha_mul = _color(layer.color, alpha, penv, local, f"{where}.color")
    alpha = alpha * alpha_mul
    return splat(state, size, alpha, (color[..., 0], color[..., 1], color[..., 2]),
                 layer, width, height)


# ---------------------------------------------------------------------- sheet

@dataclass
class Sheet:
    """An effect's sheet and what is needed to place it in an engine."""

    png: bytes
    meta: dict[str, Any]
    frames: list[bytes]
    contact: bytes


def _trim_box(frames: np.ndarray, pad: int = 2) -> tuple[int, int, int, int]:
    """The box that holds the effect over all its frames at once."""
    alpha = frames[..., 3].max(axis=0)
    rows, cols = np.nonzero(alpha > 0)
    height, width = alpha.shape
    if rows.size == 0:
        return 0, 0, width, height
    x0, x1 = max(0, cols.min() - pad), min(width, cols.max() + 1 + pad)
    y0, y1 = max(0, rows.min() - pad), min(height, rows.max() + 1 + pad)
    return int(x0), int(y0), int(x1), int(y1)


def _png(array: np.ndarray, label: str = "") -> bytes:
    """Encode a frame. `label` stamps it (effect, index): the store addresses by
    content, and two identical empty frames -- of one effect or of two effects --
    would otherwise become a single asset, filed in a single place."""
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    if label:
        info.add_text("gamestudio", label)
    buffer = io.BytesIO()
    Image.fromarray(array, "RGBA").save(buffer, format="PNG", optimize=True, pnginfo=info)
    return buffer.getvalue()


def assemble(rendered: Rendered, *, contact_cell: int = 160) -> Sheet:
    """Sheet, separate frames, contact sheet and metadata."""
    spec = rendered.spec
    frames = rendered.frames
    ox, oy = rendered.origin
    if spec.trim:
        x0, y0, x1, y1 = _trim_box(frames)
        frames = frames[:, y0:y1, x0:x1]
        ox, oy = ox - x0, oy - y0
    count, height, width = frames.shape[:3]
    columns = spec.columns or int(math.ceil(math.sqrt(count)))
    columns = max(1, min(columns, count))
    rows = int(math.ceil(count / columns))
    sheet = np.zeros((rows * height, columns * width, 4), np.uint8)
    for index in range(count):
        row, col = divmod(index, columns)
        sheet[row * height:(row + 1) * height, col * width:(col + 1) * width] = frames[index]

    meta = {
        "effect": spec.name,
        "description": spec.description,
        "frames": count,
        "fps": spec.fps,
        "duration": round(spec.duration, 4),
        "loop": spec.loop,
        "frame_width": width,
        "frame_height": height,
        "columns": columns,
        "rows": rows,
        "blend": spec.blend,
        # The effect's (0, 0) point in a frame: the pivot to put on the anchor.
        "origin": [round(ox, 2), round(oy, 2)],
        "seed": spec.seed,
        "render_seconds": round(rendered.seconds, 2),
    }
    return Sheet(png=_png(sheet, f"{spec.name}:sheet"), meta=meta,
                 frames=[_png(frame, f"{spec.name}:{index}") for index, frame in enumerate(frames)],
                 contact=contact_sheet(frames, columns=columns, cell=contact_cell,
                                       additive=spec.blend == "add",
                                       label=f"{spec.name}:contact"))


def contact_sheet(frames: np.ndarray, *, columns: int, cell: int = 160,
                  additive: bool = False, label: str = "") -> bytes:
    """Every frame on a dark background: what is looked at to judge the effect.

    An additive effect can only be judged on dark; an opaque effect is judged
    on a checkerboard, which shows what is really transparent.
    """
    count, height, width = frames.shape[:3]
    fit = min(1.0, cell / max(width, height))
    w, h = max(1, int(width * fit)), max(1, int(height * fit))
    rows = int(math.ceil(count / columns))
    gap = 4
    board = Image.new("RGB", (columns * (w + gap) + gap, rows * (h + gap) + gap), (12, 11, 10))
    yy, xx = np.mgrid[0:h, 0:w]
    checker = np.where(((xx // 8) + (yy // 8)) % 2 == 0, 38, 26).astype(np.uint8)
    for index in range(count):
        frame = Image.fromarray(frames[index], "RGBA").resize((w, h), Image.Resampling.LANCZOS)
        rgba = np.asarray(frame, np.float32) / 255.0
        if additive:
            base = np.full((h, w, 3), 10 / 255.0, np.float32)
            out = base + rgba[..., :3] * rgba[..., 3:4]
        else:
            base = np.repeat(checker[..., None], 3, axis=2).astype(np.float32) / 255.0
            out = base * (1 - rgba[..., 3:4]) + rgba[..., :3] * rgba[..., 3:4]
        tile = Image.fromarray(np.clip(out * 255, 0, 255).astype(np.uint8), "RGB")
        row, col = divmod(index, columns)
        board.paste(tile, (gap + col * (w + gap), gap + row * (h + gap)))
    return _png(np.asarray(board.convert("RGBA")), label)


def load_input(data: bytes) -> np.ndarray:
    """An input image as float RGBA in [0, 1]."""
    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("RGBA"), np.float32) / 255.0
