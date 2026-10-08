"""A project's visual effects: specs that are written, sheets that are rendered.

An effect -- spell, fire, smoke, lightning, splash, aura -- is described by a
YAML spec (`vfx/spec.py`) filed with the project's documents
(`<folder>/.gamestudio/documents/effects/<name>.yaml`): a decided, versioned
text, never a production. Rendering is local, free and deterministic:

- `preview_effect` renders at reduced scale, filing nothing -- to iterate;
- `build_effect` renders at full size, files the sheet, the frames, the
  contact sheet and the atlas in the library
  (`.gamestudio/library/effects/<name>/`), writes the Godot scenes
  (`res://effects/<name>/`) and has them validated by headless Godot. The game
  folder must already have its `project.godot`: the studio does not create one.

No material is coded here: what the effect *is* reads from its spec, and how to
write it from the `vfx` skill.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..godot.vfx_scene import write_effect
from ..pipeline.steps.export import create_godot_project, godot_target
from ..store.folders import project_paths
from ..vfx import expr as vfx_expr
from ..vfx import render as vfx_render
from ..vfx.expr import ExprError
from ..vfx.spec import NAME_RE, EffectSpec, SpecError, load
from .context import space, studio
from .errors import NotFound, ServiceError

SUFFIX = ".yaml"
MAX_BYTES = 64 * 1024

# The roles of a built effect's assets (see store/library.py).
SHEET_ROLE = "effect_sheet"
FRAME_ROLE = "effect_frame"
CONTACT_ROLE = "effect_contact"
ATLAS_ROLE = "effect_atlas"
SPEC_ROLE = "effect_spec"
ROLES = (SHEET_ROLE, FRAME_ROLE, CONTACT_ROLE, ATLAS_ROLE, SPEC_ROLE)

# A new effect's starting point: a canvas, not a material. A soft breathing
# disc, so that the first preview shows something and every key of the format
# is there, to be replaced.
BLANK = """\
description: ""
size: 192
frames: 24
fps: 24
loop: true
seed: 1
defs: |
  breathe = 0.85 + 0.15 * sin(tau * t)
layers:
  - name: shape
    type: field
    alpha: fill(circle(x, y, 0.5 * breathe), 0.2)
    color: "#ffffff"
    blend: normal
post: []
"""

VALIDATOR = Path(__file__).resolve().parents[3] / "scripts" / "validate_godot_effect.gd"


def _check_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise ServiceError(f"invalid effect name: {name!r} -- lowercase letters, digits, - and _ "
                           "(48 characters at most)")
    return name


def _folder(project: str) -> Path:
    if not project:
        raise ServiceError("a project is required")
    return project_paths(studio().settings, project).effects


def _path(project: str, name: str) -> Path:
    return _folder(project) / f"{_check_name(name)}{SUFFIX}"


def _parse(text: str, name: str) -> EffectSpec:
    try:
        return load(text, name)
    except SpecError as exc:
        raise ServiceError(f"invalid spec: {exc}") from exc


# ------------------------------------------------------------------- reading

def effects(project: str) -> list[dict[str, Any]]:
    """A project's effects, with the state of their latest build."""
    folder = _folder(project)
    builds = _builds(project)
    rows = []
    if folder.is_dir():
        for path in sorted(folder.glob(f"*{SUFFIX}")):
            name = path.stem
            if not NAME_RE.match(name):
                continue
            row: dict[str, Any] = {"name": name, "path": str(path), "description": "",
                                   "valid": True, "error": ""}
            try:
                spec = load(path.read_text(encoding="utf-8"), name)
                row.update(description=spec.description, frames=spec.frames, fps=spec.fps,
                           loop=spec.loop, size=[spec.width, spec.height],
                           layers=[f"{layer.name}:{layer.type}" for layer in spec.layers])
            except SpecError as exc:
                row.update(valid=False, error=str(exc))
            row["build"] = builds.get(name)
            rows.append(row)
    return rows


def read_effect(project: str, name: str) -> dict[str, Any]:
    path = _path(project, name)
    if not path.is_file():
        raise NotFound(f"effect not found: {name} (project {project})")
    text = path.read_text(encoding="utf-8")
    error = ""
    try:
        load(text, name)
    except SpecError as exc:
        error = str(exc)
    return {"project": project, "name": name, "path": str(path), "spec": text,
            "valid": not error, "error": error, "build": _builds(project).get(name)}


def reference() -> str:
    """The effects language reference (functions, variables), in Markdown."""
    return vfx_expr.reference() + _FORMAT


_FORMAT = """
## The spec

- `size` (integer or [w, h]), `frames`, `fps`, `loop`, `seed`, `description`.
- `inputs`: `name: path` -- images read by `tex("name", u, v)` (absolute path, relative to
  the project's library, or asset id).
- `defs`: shared code; its names can be read in every layer.
- `layers`: composited in order. Each layer: `name` (readable afterwards as a variable:
  its alpha), `type`, `alpha`, `color`, `blend` (normal, add, screen, multiply, mask, erase),
  `intensity` (> 1 overexposes).
  - `color`: `"#rrggbb[aa]"`, code returning `(r, g, b[, a])`, or `{ramp: [...], by: code}`
    (evenly spread `"#c"` stops, or `[position, "#c"]`; `by` defaults to the alpha, to life
    for particles).
  - `field`: `alpha` is required.
  - `fluid`: `source` (density injected per second), `heat`, `force` → (fx, fy), `grid`,
    `substeps`, `warmup`, `buoyancy`, `weight`, `vorticity`, `dissipation`, `cooling`,
    `drag`, `iterations`. Extra variables: `density`, `heat`, `vx`, `vy`, `speed`.
  - `particles`: `count`, `seed` (two layers with the same seed share their draws),
    `born` (birth time in [0, 1)), `lifetime` (s),
    `position` → (x, y), `velocity` → (vx, vy), `force` → (fx, fy), `gravity`, `drag`,
    `turbulence`, `turbulence_scale`, `size`, `shape` (dot, streak, ring, square),
    `softness`, `stretch`. Variables: `rand0`…`rand5` (fixed per particle), `x`, `y`,
    `vx`, `vy`, `speed`, `age`, `life` (0 → 1), `lifetime`, `born`.
- `post`: `bloom: {threshold, radius, strength}`, `blur: r`, `pixelate: n`,
  `quantize: {colors: n}` (one palette for every frame), `palette: ["#…"]`,
  `alpha_steps: n`, `exposure: k`, `tonemap: white`.
- `trim`: crops every frame to the same box. `columns`: the sheet's columns.
- `godot`: `blend` (add, mix), `particles: {amount, lifetime, one_shot, explosiveness,
  direction, spread, velocity, gravity, scale}` -- in Godot conventions (pixels, y down).
"""


# ------------------------------------------------------------------ writing

def write_effect_spec(project: str, name: str, spec: str) -> dict[str, Any]:
    """Write an effect's spec. A wrong spec is refused, with the reason."""
    path = _path(project, name)
    if len(spec.encode("utf-8")) > MAX_BYTES:
        raise ServiceError(f"spec too long (max {MAX_BYTES // 1024} KB)")
    _parse(spec, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(spec if spec.endswith("\n") else spec + "\n", encoding="utf-8")
    return read_effect(project, name)


def create_effect(project: str, name: str, spec: str = "") -> dict[str, Any]:
    """Create an effect; without a spec, it starts from a neutral canvas."""
    path = _path(project, name)
    if path.exists():
        raise ServiceError(f"effect {name} already exists")
    return write_effect_spec(project, name, spec or BLANK)


def delete_effect(project: str, name: str) -> dict[str, Any]:
    """Delete the spec. Builds already filed stay in the store."""
    path = _path(project, name)
    if not path.is_file():
        raise NotFound(f"effect not found: {name}")
    path.unlink()
    return {"deleted": name, "path": str(path)}


# --------------------------------------------------------------------- render

def _spec_for(project: str, name: str, spec: str | None) -> EffectSpec:
    if spec is not None:
        return _parse(spec, _check_name(name or "preview"))
    return _parse(read_effect(project, name)["spec"], name)


def _inputs(project: str, spec: EffectSpec) -> dict[str, Any]:
    """Load the input images the spec declares."""
    if not spec.inputs:
        return {}
    st = space(project)
    paths = st.paths
    images = {}
    for key, value in spec.inputs.items():
        candidates: list[Path] = []
        if re.fullmatch(r"[0-9a-f]{32}", value):
            stored = st.store.path_for(value)
            if stored is not None:
                candidates.append(stored)
        raw = Path(value).expanduser()
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates += [paths.library / raw, paths.root / raw]
            if st.settings.project_root is not None:
                candidates.append(st.settings.project_root / raw)
        found = next((c for c in candidates if c.is_file()), None)
        if found is None:
            raise ServiceError(f"inputs.{key}: image not found ({value})")
        images[key] = vfx_render.load_input(found.read_bytes())
    return images


def _render(project: str, spec: EffectSpec, scale: float) -> vfx_render.Rendered:
    try:
        return vfx_render.render(spec, _inputs(project, spec), scale=scale)
    except ExprError as exc:
        raise ServiceError(f"render failed: {exc}") from exc


def preview_effect(project: str, name: str, spec: str | None = None,
                   scale: float = 0.5) -> dict[str, Any]:
    """Free preview: render the effect (scaled down), write the contact sheet, file nothing.

    `spec`: an unsaved text to try instead of the file. Returns the contact
    sheet's path -- that is what one looks at.
    """
    effect = _spec_for(project, name, spec)
    rendered = _render(project, effect, scale)
    sheet = vfx_render.assemble(rendered)
    work = space(project).settings.work_for(project) / "effects" / effect.name
    work.mkdir(parents=True, exist_ok=True)
    (work / "preview.png").write_bytes(sheet.contact)
    (work / "sheet.png").write_bytes(sheet.png)
    return {
        "project": project, "name": effect.name, "scale": scale,
        "contact_sheet": str(work / "preview.png"),
        "sheet_path": str(work / "sheet.png"),
        "sheet": "data:image/png;base64," + base64.b64encode(sheet.png).decode("ascii"),
        "meta": sheet.meta, "notes": rendered.notes,
        "seconds": round(rendered.seconds, 2),
    }


def build_effect(project: str, name: str, validate: bool = True) -> dict[str, Any]:
    """Render the effect at full size, file it and export it to Godot (free, local).

    Refused before any render if the game folder has no `project.godot`.
    """
    st = space(project)
    text = read_effect(project, name)["spec"]
    effect = _parse(text, name)
    try:
        godot = godot_target(st.paths)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    rendered = _render(project, effect, 1.0)
    sheet = vfx_render.assemble(rendered)
    build = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    base = {"project": project, "effect": name, "build": build}

    def put(data: bytes, suffix: str, role: str, kind: str = "image", **extra: Any):
        asset = st.store.put_bytes(data, suffix, kind=kind, meta={**base, "role": role, **extra})
        st.db.save_asset(asset)
        return asset

    sheet_asset = put(sheet.png, ".png", SHEET_ROLE,
                      pixels=[sheet.meta["columns"] * sheet.meta["frame_width"],
                              sheet.meta["rows"] * sheet.meta["frame_height"]])
    put(sheet.contact, ".png", CONTACT_ROLE)
    put(json.dumps(sheet.meta, indent=2, ensure_ascii=False).encode("utf-8"), ".json",
        ATLAS_ROLE, kind="data")
    # The header makes the file unique to the effect: two effects with the same
    # spec do not share an asset (the store is content-addressed).
    put(f"# effect {name}, build {build}\n{text}".encode(), ".yaml", SPEC_ROLE, kind="data")
    for index, frame in enumerate(sheet.frames):
        put(frame, ".png", FRAME_ROLE, index=index)

    folder = godot / "effects" / name
    exported = write_effect(godot, folder, name, sheet.png, sheet.meta, effect.godot)
    check = _validate(godot, exported, sheet.meta["frames"]) if validate else \
        {"status": "not checked", "output": ""}

    st.librarian.sync_project(project)
    library = st.librarian.project_dir(project) / "effects" / name
    return {
        "project": project, "name": name, "build": build,
        "library": str(library),
        "sheet": str(library / f"{name}.png"),
        "contact_sheet": str(library / "contact.png"),
        "atlas": str(library / f"{name}.json"),
        "sheet_asset_id": sheet_asset.id,
        "godot": {"scene": str(exported.scene), "res_scene": exported.res_scene,
                  "particles": str(exported.particles) if exported.particles else "",
                  "res_particles": exported.res_particles,
                  "validation": check["status"], "output": check["output"]},
        "meta": sheet.meta, "notes": rendered.notes,
        "seconds": round(rendered.seconds, 2),
    }


def _validate(project_root: Path, exported, frames: int) -> dict[str, str]:
    """Have headless Godot load the scenes, in a throwaway project.

    Nothing is written to the user's project: the effect folder is copied into
    a temporary project, imported, then each scene is instantiated there by
    `scripts/validate_godot_effect.gd`.
    """
    binary = shutil.which("godot") or shutil.which("godot4")
    if binary is None:
        return {"status": "godot missing", "output": ""}
    if not VALIDATOR.is_file():
        return {"status": "validator missing", "output": str(VALIDATOR)}
    folder = exported.scene.parent
    relative = folder.relative_to(project_root)
    with tempfile.TemporaryDirectory(prefix="gs-effect-") as temp:
        root = Path(temp)
        create_godot_project(root, "validation")
        shutil.copytree(folder, root / relative)
        shutil.copyfile(VALIDATOR, root / VALIDATOR.name)
        try:
            subprocess.run([binary, "--headless", "--path", str(root), "--import"],
                           capture_output=True, text=True, timeout=300)
            lines: list[str] = []
            ok = True
            for res in filter(None, (exported.res_scene, exported.res_particles)):
                result = subprocess.run(
                    [binary, "--headless", "--path", str(root), "--script",
                     f"res://{VALIDATOR.name}", "--", res, str(frames)],
                    capture_output=True, text=True, timeout=300)
                output = result.stdout + result.stderr
                ok = ok and "RESULT: OK" in output
                lines += [f"{res}: {line.strip()}" for line in output.splitlines()
                          if any(m in line for m in ("flipbook", "emitter", "FAILED", "RESULT"))]
        except subprocess.TimeoutExpired:
            return {"status": "failed", "output": "Godot did not answer"}
    return {"status": "ok" if ok else "failed", "output": "\n".join(lines)}


def _builds(project: str) -> dict[str, dict[str, Any]]:
    """The latest filed build of each effect in the project."""
    st = space(project)
    latest: dict[str, dict[str, Any]] = {}
    library = st.librarian.project_dir(project) / "effects"
    for asset in st.db.list_assets(limit=4000):
        meta = asset.meta
        if meta.get("role") != SHEET_ROLE or meta.get("project") != project:
            continue
        name = str(meta.get("effect", ""))
        if name not in latest or str(meta.get("build", "")) > latest[name]["build"]:
            latest[name] = {"build": str(meta.get("build", "")), "sheet_asset_id": asset.id,
                            "library": str(library / name)}
    return latest
