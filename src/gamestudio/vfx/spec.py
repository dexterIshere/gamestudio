"""An effect's spec: a YAML document that says everything to render.

An effect is a stack of composited layers, rendered over N frames:

    size: 256                  # or [width, height]
    frames: 32
    fps: 24
    loop: true                 # seamless loop, or one-shot effect
    seed: 7
    inputs:                    # input images, read by tex("name", u, v)
      glyph: library/.../glyph.png
    defs: |                    # shared code, evaluated once per frame
      w = fbm(x * 2, y * 2 - t * 2, ty=2)
    layers:
      - name: core
        type: field            # field | fluid | particles
        alpha: |               # the layer's 0..1 coverage
          fill(circle(x, y + w * 0.2, 0.4), 0.15)
        color:                 # "#hex", code returning (r, g, b[, a]), or a ramp
          ramp: ["#300", "#f60", "#ffd"]
          by: core             # what walks the ramp (default: alpha)
        blend: add             # normal | add | screen | multiply | mask | erase
        intensity: 1.5         # > 1: overexposes, feeds the bloom
    post:
      - bloom: {threshold: 0.7, radius: 6, strength: 0.8}
    godot:
      blend: add               # the Godot scene's material
      particles: {amount: 12, lifetime: 1.2}   # optional

No material is planned in advance: a fire, a water, a lightning bolt or an aura
are only different ways of filling these fields. Per-material methods live in
the skill `vfx`, not in the code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import yaml

from .expr import ExprError, Program, compile_code, parse_color, ramp_stops

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

LAYER_TYPES = ("field", "fluid", "particles")
BLENDS = ("normal", "add", "screen", "multiply", "mask", "erase")
POST_OPS = ("bloom", "blur", "pixelate", "quantize", "palette", "alpha_steps", "tonemap",
            "exposure")

# Bounds: beyond them, a local render becomes a wait of several minutes.
MAX_SIZE = 1024
MAX_FRAMES = 128
MAX_PARTICLES = 6000
MAX_GRID = 256
MAX_PIXELS = 16_000_000


class SpecError(ValueError):
    """An incomplete or inconsistent effect spec."""


@dataclass
class ColorSpec:
    kind: str                          # solid | ramp | code
    rgba: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    positions: np.ndarray | None = None
    colors: np.ndarray | None = None
    by: Program | None = None
    code: Program | None = None


@dataclass
class Layer:
    name: str
    type: str
    blend: str = "normal"
    intensity: float = 1.0
    alpha: Program | None = None
    color: ColorSpec = field(default_factory=lambda: ColorSpec("solid"))
    # Type-specific settings (fluid, particles): numbers or programs.
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class EffectSpec:
    name: str
    width: int
    height: int
    frames: int
    fps: float
    loop: bool
    seed: int
    layers: list[Layer]
    defs: Program | None = None
    post: list[tuple[str, Any]] = field(default_factory=list)
    inputs: dict[str, str] = field(default_factory=dict)
    trim: bool = False
    columns: int = 0
    godot: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    @property
    def duration(self) -> float:
        return self.frames / self.fps

    @property
    def blend(self) -> str:
        """The render's blend mode in the engine: declared, otherwise derived."""
        declared = str(self.godot.get("blend", "")).lower()
        if declared in ("add", "mix", "normal", "screen"):
            return "mix" if declared == "normal" else declared
        visible = [layer for layer in self.layers if layer.blend not in ("mask", "erase")]
        additive = visible and all(layer.blend in ("add", "screen") for layer in visible)
        return "add" if additive else "mix"


# ------------------------------------------------------------------- reading

def load(text: str, name: str) -> EffectSpec:
    """Read a YAML spec and validate it fully: no render starts on a wrong spec."""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise SpecError(f"unreadable YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SpecError("an effect spec is a YAML dictionary")
    return parse(raw, name)


def parse(raw: dict[str, Any], name: str) -> EffectSpec:
    if not NAME_RE.match(name):
        raise SpecError(f"invalid effect name: {name!r} (lowercase, digits, - and _)")
    known = {"size", "frames", "fps", "loop", "seed", "layers", "defs", "post", "inputs",
             "trim", "columns", "godot", "description"}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise SpecError(f"unknown key(s): {', '.join(unknown)} "
                        f"(known: {', '.join(sorted(known))})")

    width, height = _size(raw.get("size", 256))
    frames = _int(raw.get("frames", 24), "frames", 1, MAX_FRAMES)
    fps = _num(raw.get("fps", 24), "fps", 1, 120)
    defs = None
    if raw.get("defs") not in (None, ""):
        try:
            defs = compile_code(raw["defs"], where="defs", result=False)
        except ExprError as exc:
            raise SpecError(str(exc)) from exc
    if frames * width * height > MAX_PIXELS:
        raise SpecError(f"{frames} frames of {width}x{height}: too many pixels for a local "
                        f"render (max {MAX_PIXELS // 1_000_000} M) -- reduce `size` or `frames`")

    layers_raw = raw.get("layers")
    if not isinstance(layers_raw, list) or not layers_raw:
        raise SpecError("`layers`: at least one layer is expected")
    layers: list[Layer] = []
    names: set[str] = set()
    for index, entry in enumerate(layers_raw):
        layer = _layer(entry, index)
        if layer.name in names:
            raise SpecError(f"two layers are named “{layer.name}”")
        names.add(layer.name)
        layers.append(layer)

    inputs = raw.get("inputs") or {}
    if not isinstance(inputs, dict) or not all(isinstance(v, str) for v in inputs.values()):
        raise SpecError("`inputs`: a dictionary name → image path")

    godot = raw.get("godot") or {}
    if not isinstance(godot, dict):
        raise SpecError("`godot`: a dictionary")

    return EffectSpec(
        name=name, width=width, height=height, frames=frames, fps=fps,
        loop=bool(raw.get("loop", True)), seed=int(raw.get("seed", 0)),
        layers=layers, defs=defs, post=_post(raw.get("post") or []),
        inputs={str(k): v for k, v in inputs.items()},
        trim=bool(raw.get("trim", False)),
        columns=_int(raw.get("columns", 0), "columns", 0, MAX_FRAMES),
        godot=godot, description=str(raw.get("description") or ""),
    )


def _size(value: Any) -> tuple[int, int]:
    if isinstance(value, (int, float)):
        value = [value, value]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SpecError("`size`: an integer, or [width, height]")
    return (_int(value[0], "size", 8, MAX_SIZE), _int(value[1], "size", 8, MAX_SIZE))


def _int(value: Any, key: str, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SpecError(f"`{key}`: an integer is expected, not {value!r}") from exc
    if not lo <= number <= hi:
        raise SpecError(f"`{key}`: {number} outside [{lo}, {hi}]")
    return number


def _num(value: Any, key: str, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SpecError(f"`{key}`: a number is expected, not {value!r}") from exc
    if not lo <= number <= hi:
        raise SpecError(f"`{key}`: {number} outside [{lo}, {hi}]")
    return number


def _code(value: Any, where: str) -> Program:
    try:
        return compile_code(value, where=where)
    except ExprError as exc:
        raise SpecError(str(exc)) from exc


def _color(value: Any, where: str) -> ColorSpec:
    if value is None:
        return ColorSpec("solid")
    try:
        if isinstance(value, str) and value.strip().startswith("#"):
            return ColorSpec("solid", rgba=parse_color(value))
        if isinstance(value, dict):
            if "ramp" not in value:
                raise SpecError(f"{where}: a color given as a dictionary carries `ramp`")
            positions, colors = ramp_stops(value["ramp"])
            by = _code(value["by"], f"{where}.by") if value.get("by") not in (None, "") else None
            return ColorSpec("ramp", positions=positions, colors=colors, by=by)
        if isinstance(value, list):
            positions, colors = ramp_stops(value)
            return ColorSpec("ramp", positions=positions, colors=colors)
        return ColorSpec("code", code=_code(value, where))
    except ExprError as exc:
        raise SpecError(f"{where}: {exc}") from exc


# Each type's own settings: name -> (kind, default). A "code" kind is a
# program; "num" a number; "vec" a pair of numbers.
FLUID_PARAMS: dict[str, tuple[str, Any]] = {
    "grid": ("int", 128),
    "substeps": ("int", 1),
    "warmup": ("int", None),
    "source": ("code", None),
    "heat": ("code", None),
    "force": ("code", None),
    "buoyancy": ("num", 1.0),
    "weight": ("num", 0.0),
    "vorticity": ("num", 1.5),
    "dissipation": ("num", 0.985),
    "cooling": ("num", 0.94),
    "drag": ("num", 0.0),
    "iterations": ("int", 30),
}

PARTICLE_PARAMS: dict[str, tuple[str, Any]] = {
    "count": ("int", 200),
    # Two layers with the same `seed` draw the same rand0..rand5: a drop and
    # its reflection, an ember and its glow. Absent, the seed comes from the name.
    "seed": ("int", None),
    "born": ("code", "rand0"),
    "lifetime": ("code", "0.6 + rand1 * 0.6"),
    "position": ("code", "(0, 0)"),
    "velocity": ("code", "(0, 1)"),
    "force": ("code", None),
    "gravity": ("vec", (0.0, 0.0)),
    "drag": ("num", 0.0),
    "turbulence": ("num", 0.0),
    "turbulence_scale": ("num", 2.0),
    "size": ("code", "0.03"),
    "shape": ("str", "dot"),
    "softness": ("num", 0.6),
    "stretch": ("num", 0.05),
}

SHAPES = ("dot", "streak", "ring", "square")


def _params(entry: dict[str, Any], table: dict[str, tuple[str, Any]], where: str
            ) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, (kind, default) in table.items():
        value = entry.get(key, default)
        if kind == "code":
            params[key] = _code(value, f"{where}.{key}") if value not in (None, "") else None
        elif kind == "int" and value is None:
            params[key] = None
        elif kind == "int":
            params[key] = _int(value, f"{where}.{key}", 0,
                               {"grid": MAX_GRID, "count": MAX_PARTICLES,
                                "seed": 1_000_000}.get(key, 500))
        elif kind == "num":
            params[key] = _num(value, f"{where}.{key}", -1e4, 1e4)
        elif kind == "vec":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise SpecError(f"`{where}.{key}`: [x, y] is expected")
            params[key] = (float(value[0]), float(value[1]))
        else:
            params[key] = str(value)
    return params


def _layer(entry: Any, index: int) -> Layer:
    if not isinstance(entry, dict):
        raise SpecError(f"layer {index}: a dictionary is expected")
    name = str(entry.get("name") or f"layer{index}")
    if not re.match(r"^[a-z_][a-z0-9_]*$", name):
        raise SpecError(f"layer “{name}”: a layer name is an identifier (a-z, 0-9, _), "
                        "read as a variable in the following layers")
    where = f"layers.{name}"
    kind = str(entry.get("type", "field"))
    if kind not in LAYER_TYPES:
        raise SpecError(f"{where}.type: {kind!r} (expected: {', '.join(LAYER_TYPES)})")
    blend = str(entry.get("blend", "normal"))
    if blend not in BLENDS:
        raise SpecError(f"{where}.blend: {blend!r} (expected: {', '.join(BLENDS)})")

    base = {"name", "type", "blend", "intensity", "alpha", "color"}
    table = {"fluid": FLUID_PARAMS, "particles": PARTICLE_PARAMS}.get(kind, {})
    unknown = sorted(set(entry) - base - set(table))
    if unknown:
        allowed = ", ".join(sorted(base | set(table)))
        raise SpecError(f"{where}: unknown key(s) {', '.join(unknown)} "
                        f"(for {kind}: {allowed})")

    alpha_default = {"field": None, "fluid": "density", "particles": "1 - life"}[kind]
    alpha_value = entry.get("alpha", alpha_default)
    if kind == "field" and alpha_value in (None, ""):
        raise SpecError(f"{where}.alpha: a field layer states its coverage (code)")
    layer = Layer(
        name=name, type=kind, blend=blend,
        intensity=_num(entry.get("intensity", 1.0), f"{where}.intensity", 0, 100),
        alpha=_code(alpha_value, f"{where}.alpha"),
        color=_color(entry.get("color"), f"{where}.color"),
        params=_params(entry, table, where) if table else {},
    )
    if kind == "fluid" and layer.params["source"] is None:
        raise SpecError(f"{where}.source: a fluid needs a source (code)")
    if kind == "particles" and layer.params["shape"] not in SHAPES:
        raise SpecError(f"{where}.shape: {layer.params['shape']!r} "
                        f"(expected: {', '.join(SHAPES)})")
    return layer


def _post(raw: Any) -> list[tuple[str, Any]]:
    if not isinstance(raw, list):
        raise SpecError("`post`: a list of operations, each `- name: settings`")
    ops: list[tuple[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise SpecError(f"`post`: {entry!r} -- one operation per item (`- bloom: {{...}}`)")
        op, value = next(iter(entry.items()))
        if op not in POST_OPS:
            raise SpecError(f"`post`: unknown operation {op!r} (known: {', '.join(POST_OPS)})")
        if op == "palette":
            if not isinstance(value, list) or not value:
                raise SpecError("`post.palette`: a list of colors")
            try:
                value = [parse_color(str(c)) for c in value]
            except ExprError as exc:
                raise SpecError(f"`post.palette`: {exc}") from exc
        ops.append((str(op), value))
    return ops


def dump(raw: dict[str, Any]) -> str:
    """Write a spec as readable YAML: multiline code stays a block."""
    class _Dumper(yaml.SafeDumper):
        pass

    def _str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
        style = "|" if "\n" in data else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)

    _Dumper.add_representer(str, _str)
    return yaml.dump(raw, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=100)
