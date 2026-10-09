"""A game's lookdev: each element of its art direction, taken out of the game, shown alone and live.

The art direction of a game already under way is not described: it is shown.
Each game shader becomes a **specimen** -- laid on its template (a rectangle at
its size for an interface, a lit shape for a material, a sky around the
camera), or on the real object carrying it when it only lives there (a water
reading its planet's mesh) --, rendered by Godot itself, and tunable live. No
verdict: what is in the game is kept, what is no longer wanted leaves it. A
specimen needing rework is discussed with an agent opened on it
(`handoff.lookdev_brief`). The lookdev changes the game on one gesture only:
"Save" (`save`) writes the touched settings where the shown use takes them --
the `shader_parameter/` lines of its material, or the shader's defaults -- and
nothing else.

Rendering goes through a **bench** (`godot/lookdev_bench.gd`): a Godot that
stays open, off screen, and answers on a local socket -- an image in about ten
milliseconds (JPEG), where `render_scene` needs three seconds to start. One
bench per Godot project, started at the first image; its life is tied to its
connection with the studio, and it stops after ten minutes without a command.
The socket is local, but any process on the machine can connect to it, and the
bench runs the setup it is given: it only obeys whoever repeats the secret
drawn at its launch. A restarted bench has lost what was laid on it: it is laid
again, silently.

An image does not reread the game: the specimen is resolved once (`_target`),
when its room opens, then each image only costs the bench. Only the shader file
is checked at each image, so that a change in the game shows at once.

How a specimen is presented -- the setup building its real object, the template
shape, the use shown first -- lives in `.gamestudio/documents/lookdev/<id>.json`,
versioned with the project.
"""

from __future__ import annotations

import atexit
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..net import find_free_port
from ..store.folders import project_paths
from . import colors, documents, fonts, influences, renders, survey
from .context import space, studio
from .errors import NotFound, ServiceError

SCRIPT = Path(__file__).resolve().parent.parent / "godot" / "lookdev_bench.gd"
STATE_FOLDER = "lookdev"
SHAPES = ("sphere", "plane", "cube", "cylinder", "capsule", "torus")
# A material can also be laid on a model: `mesh:game:<file>` (the game's),
# `mesh:asset:<id>` (the studio's library), `mesh:import:<file>` (one the user
# dropped, kept in the workspace). glTF only: the bench reads it as is.
MESH = "mesh:"
MESH_TYPES = (".glb", ".gltf")
MESH_FOLDER = "lookdev-meshes"
MAX_MESH_BYTES = 200 * 1024 * 1024
# The screens a specimen is seen on, by their resolution upright: a phone, a
# tablet, a desktop monitor (always landscape). A game held in landscape turns
# the first two (`_devices`).
DEVICES = {"phone": (1080, 2400), "tablet": (1536, 2048), "desktop": (1920, 1080)}
# Godot's `window/handheld/orientation` values that hold the device upright.
_UPRIGHT = {"1", "3", "5"}
_SENSOR = "6"
# The bench announces it is listening; past this, the virtual display did not start.
START_TIMEOUT = 30.0
# An image, or building a real game object (a planet: ~1.5 s, plus the first
# compilation of its shaders).
ASK_TIMEOUT = 60.0
# The bench's image formats: `jpg` for the live image (5 ms at 640 px), `webp`
# for a thumbnail that keeps its transparency (45 ms, once), `png` for an image
# that must stay exact -- a sky as the page's background, where JPEG smears the
# stars (lossless, once per page).
FORMATS = {"jpg": "image/jpeg", "webp": "image/webp", "png": "image/png"}
# What a bench with nothing laid on it answers (it was restarted).
EMPTY = "nothing on the bench"
PORT_BASE = 47400
# The secret goes through the bench's environment, which other accounts on the
# machine cannot read -- unlike its command line.
SECRET_ENV = "GAMESTUDIO_BENCH_SECRET"
MAX_SETUP = 64 * 1024
# Bumped when the way a thumbnail is rendered changes: older ones are redone.
THUMB_VERSION = 4
# The game colors that dress the page: the most frequent, not the whole palette.
THEME_COLORS = 8
# The art direction's rules the user ticks in the Universe, and what each asks
# of whoever touches the game's look (the agents' briefs carry them).
RULES = {
    "procedural_materials": "Materials are procedural: no image texture -- matter, patterns "
                            "and wear are computed in the shader (noise, gradients, distance "
                            "fields).",
}
RULES_FILE = "rules.json"
# A shader sampler fed by the engine rather than an image: the screen, depth,
# normals behind the surface.
_ENGINE_SAMPLERS = ("screen_texture", "depth_texture", "normal_roughness_texture")

_READY = re.compile(r"GAMESTUDIO_BENCH: (READY (\d+)|FAILED (.*))")
_SHADER_PARAM = re.compile(r"^shader_parameter/(\w+)\s*=\s*(.+)$")
_CALL = re.compile(r"^(Vector2i?|Vector3i?|Vector4i?|Color)\((.*)\)$")
_EXT = re.compile(r'ExtResource\(\s*"([^"]+)"\s*\)')
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_TIME = re.compile(r"\bTIME\b")
# A uniform's declaration, up to its semicolon: scope, type, name, hint, default.
_DECL = r"(?m)^(?P<head>[ \t]*(?P<scope>(?:global|instance)\s+)?uniform\s+(?P<type>\w+)\s+{name}" \
    r"(?:\s*:\s*(?P<hint>[^=;]+?))?)\s*(?:=\s*(?P<default>[^;]+?)\s*)?;"
# The GLSL types of a setting that can be written back, and their component count.
_WRITABLE = {"bool": 1, "int": 1, "uint": 1, "float": 1, "vec2": 2, "vec3": 3, "vec4": 4,
             "ivec2": 2, "ivec3": 3, "ivec4": 4, "uvec2": 2, "uvec3": 3, "uvec4": 4}


# --------------------------------------------------------------------- the bench


class BenchLost(ServiceError):
    """The bench stopped during a command: what was laid on it is lost."""


class Bench:
    """A Godot open off screen on a project, rendering on demand."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.sock: socket.socket | None = None
        self.reader: Any = None
        self.file: Any = None
        self.log: list[str] = []
        self.engine = ""
        # What is laid on the bench -- a template, or a game object and the
        # shader being looked at: not laid again at each image.
        self.current: Any = None
        self.focus: Any = None
        self.loaded: dict[str, Any] = {}
        self.work = Path(tempfile.mkdtemp(prefix="gamestudio-bench-"))

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None and self.sock is not None

    def _drain(self, ready: threading.Event, found: dict[str, Any]) -> None:
        """Read the engine's output: its announcement, then its errors, to report them."""
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            self.log = [*self.log[-60:], line.rstrip()]
            match = _READY.search(line)
            if match and not ready.is_set():
                found["port"] = int(match.group(2)) if match.group(2) else None
                found["error"] = match.group(3)
                ready.set()
        ready.set()

    def start(self) -> None:
        binary = renders._godot_binary()
        env = {**os.environ, "XDG_DATA_HOME": str(self.work / "user")}
        renders._ensure_imported(binary, self.base, env)
        for engine, prefix in renders.displays(1024, 1024):
            port = find_free_port("127.0.0.1", PORT_BASE + os.getpid() % 500)
            if port is None:
                raise ServiceError("no free port for the lookdev bench")
            command = [*prefix, binary, "--path", str(self.base), "--resolution", "1024x1024",
                       "--script", str(SCRIPT), "--", f"--gs-port={port}"]
            secret = secrets.token_hex(16)
            self.process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env={**env, SECRET_ENV: secret}, start_new_session=True)
            ready, found = threading.Event(), {}
            self.reader = threading.Thread(target=self._drain, args=(ready, found), daemon=True)
            self.reader.start()
            ready.wait(START_TIMEOUT)
            if found.get("port"):
                self.sock = socket.create_connection(("127.0.0.1", found["port"]), timeout=10)
                self.sock.settimeout(ASK_TIMEOUT)
                self.file = self.sock.makefile("rwb")
                if not self._hello(secret):
                    self.stop()
                    raise ServiceError("the Godot bench refused the studio's connection")
                self.engine = engine
                self.current, self.focus, self.loaded = None, None, {}
                return
            self.stop()
            if found.get("error"):
                break
        tail = "\n".join(self.log[-8:])
        raise ServiceError(f"the Godot bench did not start\n{tail}")

    def _hello(self, secret: str) -> bool:
        """The first message: the launch secret, without which the bench hangs up."""
        try:
            self.file.write((json.dumps({"op": "hello", "secret": secret}) + "\n")
                            .encode("utf-8"))
            self.file.flush()
            return json.loads(self.file.readline() or b"null") == {"ok": True}
        except (OSError, ValueError):
            return False

    def stop(self) -> None:
        # The `makefile` file keeps the socket open while it lives: close both,
        # otherwise the bench does not see the studio leave.
        with contextlib.suppress(OSError):
            if self.file is not None:
                self.file.close()
        with contextlib.suppress(OSError):
            if self.sock is not None:
                self.sock.close()
        self.file, self.sock = None, None
        if self.process is not None and self.process.poll() is None:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
        self.process = None
        self.current, self.focus, self.loaded = None, None, {}

    def ask(self, command: dict[str, Any]) -> dict[str, Any]:
        """One command, one answer; a stopped bench restarts at the next command.

        A lost command is not resent as is: an image asked of a brand-new bench
        would have nothing to show. `BenchLost` tells the caller to lay the
        specimen again, then ask again.
        """
        with self.lock:
            if not self.alive():
                self.start()
            try:
                self.file.write((json.dumps(command) + "\n").encode("utf-8"))
                self.file.flush()
                line = self.file.readline()
                if not line:
                    raise OSError("bench closed")
                return json.loads(line)
            except (OSError, ValueError) as exc:
                self.stop()
                raise BenchLost("the Godot bench stopped: it is restarting") from exc


_BENCHES: dict[str, Bench] = {}
_BENCHES_LOCK = threading.Lock()


def _bench(base: Path) -> Bench:
    with _BENCHES_LOCK:
        bench = _BENCHES.get(str(base))
        if bench is None:
            bench = _BENCHES[str(base)] = Bench(base)
        return bench


@atexit.register
def stop_benches() -> None:
    """No bench outlives the studio; the next one starts from scratch."""
    with _BENCHES_LOCK:
        benches = list(_BENCHES.values())
        _BENCHES.clear()
    for bench in benches:
        bench.stop()
        shutil.rmtree(bench.work, ignore_errors=True)
    with _TARGETS_LOCK:
        _TARGETS.clear()


# ------------------------------------------------------------- specimens


def _godot_value(literal: str) -> Any:
    """A value written by Godot in a scene, as JSON; None if it cannot be read."""
    literal = literal.strip()
    if literal in ("true", "false"):
        return literal == "true"
    try:
        return int(literal) if re.fullmatch(r"-?\d+", literal) else float(literal)
    except ValueError:
        pass
    call = _CALL.match(literal)
    if call:
        try:
            return [float(part) for part in call.group(2).split(",")]
        except ValueError:
            return None
    return None


def _material_values(text: str, res_path: str) -> list[dict[str, Any]]:
    """The settings the game puts on this shader, material by material, in a file,
    and the nodes carrying each (`params`, `nodes`)."""
    ids = {}
    for line in text.splitlines():
        if line.startswith("[ext_resource"):
            attrs = survey._attrs(line)
            if "id" in attrs and "path" in attrs:
                ids[attrs["id"]] = attrs["path"]
    # The nodes carrying each material: they are how a use is recognized.
    holders: dict[str, list[str]] = {}
    node = ""
    for line in text.splitlines():
        if line.startswith("[node "):
            attrs = survey._attrs(line)
            parent = attrs.get("parent", "")
            name = attrs.get("name", "")
            # The node path under the root: "Universe/Bg" says which one, "Bg" does not.
            node = name if parent in ("", ".") else f"{parent}/{name}"
        elif line.startswith("["):
            node = ""
        elif node:
            sub = re.search(r'SubResource\(\s*"([^"]+)"\s*\)', line)
            if sub and line.split("=", 1)[0].strip() in ("material", "material_override"):
                held = holders.setdefault(sub.group(1), [])
                if node not in held:
                    held.append(node)
    found: list[dict[str, Any]] = []
    block: dict[str, Any] | None = None
    for line in [*text.splitlines(), "["]:
        if line.startswith("["):
            if block is not None and block["shader"] == res_path:
                # `block`: the material's id in its file, empty for the
                # `[resource]` of a `.tres` -- where a save writes.
                found.append({"params": block["params"], "nodes": holders.get(block["id"], []),
                              "block": block["id"]})
            attrs = survey._attrs(line)
            block = ({"shader": "", "params": {}, "id": attrs.get("id", "")}
                     if line.startswith("[resource]") or attrs.get("type") == "ShaderMaterial"
                     else None)
            continue
        if block is None:
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key.strip() == "shader":
            ext = _EXT.search(value)
            block["shader"] = ids.get(ext.group(1), "") if ext else ""
            continue
        param = _SHADER_PARAM.match(line.strip())
        if param:
            parsed = _godot_value(param.group(2))
            if parsed is not None:
                block["params"][param.group(1)] = parsed
    return found


def _owner(game: dict[str, Any], relative: str) -> dict[str, Any]:
    """The Godot project containing a file, and its `res://` path."""
    best = None
    for entry in game["godot"]:
        base = entry["path"]
        if base == "" or relative.startswith(base + "/"):
            if best is None or len(base) > len(best["path"]):
                best = entry
    if best is None:
        raise ServiceError(f"{relative} is in no Godot project")
    inner = relative[len(best["path"]) + 1:] if best["path"] else relative
    return {"godot": best, "res_path": f"res://{inner}"}


def _id(relative: str, taken: set[str]) -> str:
    stem = documents.slug(Path(relative).stem) or "shader"
    found, n = stem, 2
    while found in taken:
        found, n = f"{stem}-{n}", n + 1
    taken.add(found)
    return found


def _game(project: str) -> tuple[Path, dict[str, Any]]:
    paths = project_paths(studio().settings, project)
    if not paths.linked:
        raise ServiceError(f"project {project} has no game folder: nothing to show")
    return paths.root, survey.game_map(paths.root)


def _presets(root: Path, game: dict[str, Any], shader: dict[str, Any],
             res_path: str) -> list[dict[str, Any]]:
    """The shader's uses in the game, with the settings each one sets."""
    owner = _owner(game, shader["path"])["godot"]["path"]
    folder = root / owner if owner else root
    candidates = set(shader.get("users", []))
    for path in folder.rglob("*.tres"):
        if any(part.startswith(".") or part == "addons" for part in path.parts):
            continue
        candidates.add(path.relative_to(root).as_posix())
    found: list[dict[str, Any]] = []
    # Scenes first: a use carried by a node says where the shader shows, and it
    # is the one shown by default.
    for relative in sorted(candidates, key=lambda path: (not path.endswith(".tscn"), path)):
        path = root / relative
        if path.suffix not in (".tscn", ".tres") or not path.is_file():
            continue
        if path.stat().st_size > survey.MAX_SCENE_BYTES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if res_path not in text:
            continue
        for index, material in enumerate(_material_values(text, res_path)):
            nodes = material["nodes"]
            label = f"{Path(relative).name} — {', '.join(nodes)}" if nodes else (
                Path(relative).name if index == 0 else f"{Path(relative).name} ({index + 1})")
            stem = documents.slug(f"{Path(relative).stem}-{'-'.join(nodes) or index}")[:60]
            preset_id, n = stem, 2
            while any(entry["id"] == preset_id for entry in found):
                preset_id, n = f"{stem}-{n}", n + 1
            found.append({"id": preset_id, "label": label, "file": relative,
                          "nodes": nodes, "params": material["params"],
                          "block": material["block"]})
    return found


def _state_dir(project: str) -> Path:
    return documents.directory(project) / STATE_FOLDER


def _state(project: str, specimen: str) -> dict[str, Any]:
    path = _state_dir(project) / f"{specimen}.json"
    empty = {"setup": "", "shape": "", "preset": "", "updated_at": None}
    if not path.is_file():
        return empty
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(f"unreadable state: {path} ({exc})") from exc
    return {**empty, **{key: value for key, value in stored.items() if key in empty}}


def _specimens(project: str) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    root, game = _game(project)
    taken: set[str] = set()
    found = []
    for shader in game["shaders"]:
        owner = _owner(game, shader["path"])
        specimen = _id(shader["path"], taken)
        state = _state(project, specimen)
        found.append({
            "id": specimen, "category": "shader", "title": Path(shader["path"]).stem,
            "file": shader["path"], "res_path": owner["res_path"],
            "godot": owner["godot"]["path"], "kind": shader["type"] or "?",
            "renderable": shader["type"] in ("canvas_item", "spatial", "sky")
            or bool(state["setup"]),
            "uniforms": len(shader["uniforms"]), "users": shader.get("users", []),
            "staged": bool(state["setup"]), "updated_at": state["updated_at"],
            "textures": _samples_images(shader["uniforms"]),
        })
    return root, game, found


def _samples_images(uniforms: list[str]) -> bool:
    """Whether a shader reads an image: a sampler the engine does not feed itself.

    The survey writes each setting as `name (type, hint) = default`.
    """
    for uniform in uniforms:
        kind, _, hint = uniform.partition("(")[2].partition(")")[0].partition(",")
        if kind.strip().startswith("sampler") and not any(
                engine in hint for engine in _ENGINE_SAMPLERS):
            return True
    return False


def rules(project: str) -> dict[str, bool]:
    """The art direction's rules, ticked or not."""
    path = _state_dir(project) / RULES_FILE
    try:
        held = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        held = {}
    return {rule: bool(held.get(rule)) for rule in RULES}


def set_rule(project: str, rule: str, value: bool) -> dict[str, bool]:
    """Tick or untick a rule of the art direction. A user's decision."""
    if rule not in RULES:
        raise NotFound(f"unknown rule: {rule} (known: {', '.join(RULES)})")
    held = {**rules(project), rule: bool(value)}
    path = _state_dir(project) / RULES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(held, indent=1) + "\n", encoding="utf-8")
    return held


def rule_lines(project: str) -> list[str]:
    """The ticked rules, as a brief states them."""
    held = rules(project)
    return [f"- **{RULES[rule].split(':')[0]}** -- {RULES[rule].split(':', 1)[1].strip()}"
            for rule in RULES if held[rule]]


def _find(project: str, specimen: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root, game, found = _specimens(project)
    for entry in found:
        if entry["id"] == specimen:
            return root, game, entry
    raise NotFound(f"specimen not found: {specimen}")


def theme(project: str, game: dict[str, Any] | None = None,
          found: list[dict[str, Any]] | None = None,
          typography: dict[str, Any] | None = None) -> dict[str, Any]:
    """The game's theme: its background, colors, fonts, sky.

    `font` is the family the page is set in -- the game's default font, else
    its largest family -- with every face, so a heading in semibold uses the
    game's semibold file instead of a weight the browser makes up.
    """
    if game is None or found is None:
        root, game, found = _specimens(project)
        typography = fonts.typography(root, game)
    typography = typography or {"families": []}
    clear = next((entry["clear_color"] for entry in game["godot"] if entry["clear_color"]), "")
    tints = [color for color in game["colors"] if len(color) == 7][:THEME_COLORS]
    sky = next((entry["id"] for entry in found if entry["kind"] == "sky"), "")
    family = next((item for item in typography["families"]
                   if any(face["default"] for face in item["faces"])), None) \
        or max(typography["families"], key=lambda item: len(item["faces"]), default=None)
    return {"background": clear or "#0b0d12", "colors": tints,
            "fonts": [{"file": font, "family": Path(font).stem} for font in game["fonts"]],
            "font": {"family": family["family"], "faces": [
                {"file": face["file"], "weight": face["weight"], "italic": face["italic"]}
                for face in family["faces"]]} if family else None,
            "sky": sky}


def universe(project: str) -> dict[str, Any]:
    """The game's art direction, by aspect: its colors, its typography, its shaders.

    `theme` dresses the page as the game; `palette` (`colors.palette`: each
    color with where it is written, by aspect) and `typography`
    (`fonts.typography`) are read from the game's files, the specimens are its
    shaders.
    """
    root, game, found = _specimens(project)
    typography = fonts.typography(root, game)
    return {"project": project, "specimens": found, "palette": colors.palette(root, game),
            "direction": influences.summary(project), "rules": rules(project),
            "typography": typography, "theme": theme(project, game, found, typography)}


def specimen(project: str, specimen_id: str) -> dict[str, Any]:
    """A specimen in detail: its settings as Godot reads them, and the game's uses.

    The settings come from the bench (`uniform_list`: name, type, hint,
    default, group); without a bench, the list is empty and `bench` says why.
    Opening a specimen rereads it from the game: its images then reuse that,
    without rereading the game each time.
    """
    target = _target(project, specimen_id, fresh=True)
    entry, state = target["entry"], target["state"]
    detail = {**entry, **state, "presets": target["presets"], "uniform_list": [],
              "bench": None, "animated": target["animated"], "screen": target["screen"],
              "meshes": meshes(project) if entry["kind"] == "spatial" else []}
    if entry["renderable"]:
        bench = _bench_for(project, entry)
        try:
            loaded = _posed(bench, target, state["preset"], "")
            detail["uniform_list"] = loaded.get("uniforms", [])
            detail["size"] = loaded.get("size")
            detail["materials"] = loaded.get("materials")
            detail["bench"] = {"ok": True, "engine": bench.engine}
        except ServiceError as exc:
            detail["bench"] = {"ok": False, "error": str(exc)}
    return detail


# --------------------------------------------------------------- images

# What a specimen's image reuses without rereading the game: its entry, its
# uses, what was decided about it. Reread when its room opens (`specimen`) and
# after each decision (`set_state`).
_TARGETS: dict[tuple[str, str, str], dict[str, Any]] = {}
_TARGETS_LOCK = threading.Lock()


def _target_key(project: str, specimen_id: str) -> tuple[str, str, str]:
    # The game folder is part of the key: a project name reopened on another
    # folder does not reuse the previous one's files.
    return (str(project_paths(studio().settings, project).root), project, specimen_id)


def _target(project: str, specimen_id: str, *, fresh: bool = False) -> dict[str, Any]:
    key = _target_key(project, specimen_id)
    if not fresh:
        with _TARGETS_LOCK:
            cached = _TARGETS.get(key)
        if cached is not None:
            return cached
    root, game, entry = _find(project, specimen_id)
    shader = next(s for s in game["shaders"] if s["path"] == entry["file"])
    source = root / entry["file"]
    state = _state(project, specimen_id)
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    target = {"entry": entry, "state": state, "source": source, "root": root, "project": project,
              "presets": _presets(root, game, shader, entry["res_path"]),
              "screen": _screen(game, entry),
              # A shader reading the time is animated; a game object may carry
              # other shaders that do.
              "animated": bool(_TIME.search(text)) or bool(state["setup"])}
    with _TARGETS_LOCK:
        _TARGETS[key] = target
    return target


def _forget(project: str, specimen_id: str) -> None:
    key = _target_key(project, specimen_id)
    with _TARGETS_LOCK:
        _TARGETS.pop(key, None)


def _screen(game: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """The screen the game is drawn for: its base size, stretch mode and aspect.

    They decide how a device shows the game (`_layout`): with `canvas_items`
    and `keep`, a 720 x 1280 game fills a 1080 x 2400 phone at scale 1.5,
    with black bars above and below. Godot's defaults, 1152 x 648, `disabled`
    and `keep`, apply when the project says nothing. `devices`: the device
    screens, turned the way the game is held.
    """
    godot = next((g for g in game["godot"] if g["path"] == entry["godot"]), None)
    display = (godot or {}).get("display", {})

    def size(key: str, default: int) -> int:
        try:
            return int(float(display.get(key, default)))
        except ValueError:
            return default

    screen = {"width": size("viewport_width", 1152), "height": size("viewport_height", 648),
              "stretch": display.get("mode", "disabled") or "disabled",
              "aspect": display.get("aspect", "keep") or "keep"}
    return {**screen, "devices": _devices(screen, display.get("orientation", ""))}


def _devices(screen: dict[str, Any], orientation: str) -> list[dict[str, Any]]:
    """The device screens, turned the way the game holds them.

    A phone or a tablet is upright when the game asks for it, landscape when it
    asks for that, and like the game's base size when it says nothing or
    follows the sensor. A desktop monitor stays landscape.
    """
    if orientation and orientation != _SENSOR:
        upright = orientation in _UPRIGHT
    else:
        upright = screen["height"] > screen["width"]
    found = []
    for name, (width, height) in DEVICES.items():
        if name != "desktop" and not upright:
            width, height = height, width
        found.append({"id": name, "width": width, "height": height})
    return found


def _layout(screen: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """How the game fills a device screen, as Godot's stretch settings decide.

    `logical`: the whole screen in the game's units; `content`: the game's own
    area inside it (x, y, w, h), with black bars around it when the aspect is
    kept; `size`: the pixels drawn. `canvas_items` draws at the screen's
    resolution, `viewport` at the game's base size (the screen enlarges it),
    `disabled` does not scale: the game gets the screen's pixels as its units.
    """
    base_w, base_h = screen["width"], screen["height"]
    if screen["stretch"] == "disabled":
        return {"size": [width, height], "logical": [width, height],
                "content": [0, 0, width, height]}
    aspect = screen["aspect"]
    if aspect == "ignore":
        # Stretched to the screen whatever its proportions.
        logical = [base_w, base_h]
        content = [0.0, 0.0, base_w, base_h]
    else:
        content_w, content_h = float(base_w), float(base_h)
        wider = width / height > base_w / base_h
        if wider and aspect in ("expand", "keep_height"):
            content_w = base_h * width / height
        if not wider and aspect in ("expand", "keep_width"):
            content_h = base_w * height / width
        scale = min(width / content_w, height / content_h)
        logical = [width / scale, height / scale]
        content = [(logical[0] - content_w) / 2, (logical[1] - content_h) / 2,
                   content_w, content_h]
    size = [width, height]
    if screen["stretch"] == "viewport":
        size = [max(1, round(logical[0])), max(1, round(logical[1]))]
    return {"size": size, "logical": logical, "content": content}


def _use(presets: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    """The chosen game use, `None` for the shader's own values."""
    if wanted == "default" or not presets:
        return None
    return next((p for p in presets if p["id"] == wanted), presets[0]) if wanted else presets[0]


def _scene_of(target: dict[str, Any], use: dict[str, Any] | None) -> str:
    """The scene that holds a use's material, as `res://`; empty for a `.tres` or none."""
    if use is None or not use["file"].endswith(".tscn"):
        return ""
    base = target["entry"]["godot"]
    inner = use["file"][len(base) + 1:] if base else use["file"]
    return f"res://{inner}"


def _bench_for(project: str, entry: dict[str, Any]) -> Bench:
    root = project_paths(studio().settings, project).root
    base = root / entry["godot"] if entry["godot"] else root
    return _bench(base)


def _preset(presets: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
    if not wanted:
        return presets[0]["params"] if presets else {}
    for preset in presets:
        if preset["id"] == wanted:
            return preset["params"]
    if wanted == "default":
        return {}
    raise NotFound(f"use not found: {wanted}")


def _refused(bench: Bench, what: str, answer: dict[str, Any]) -> ServiceError:
    tail = "\n".join(line for line in bench.log[-6:] if "ERROR" in line)
    return ServiceError(f"the bench refuses {what}: {answer.get('error')}"
                        + (f"\n{tail}" if tail else ""))


def _pose(bench: Bench, target: dict[str, Any], preset: str, shape: str,
          device: bool = False) -> dict[str, Any]:
    """Lay the specimen on the bench unless it is already there; return what the bench says.

    A game object is built once for all its shaders (`stage`), then the one
    being looked at is picked (`focus`). On a device, an interface shader is
    laid on a screen (`screen`): the scene of the chosen use, placed as the
    game places it, or its template when the use is no scene. The shader file
    and the scene are part of the key: changed in the game, they reload.
    Called under `bench.lock`.
    """
    entry, state = target["entry"], target["state"]
    try:
        stamp = target["source"].stat().st_mtime_ns
    except OSError:
        stamp = 0
    setup = state["setup"]
    if device and not setup and entry["kind"] == "canvas_item":
        use = _use(target["presets"], preset or state["preset"])
        scene = _scene_of(target, use)
        nodes = use["nodes"] if scene and use else []
        size = None
        if not scene:
            params = use["params"] if use else {}
            size = params.get("node_size") if isinstance(params.get("node_size"), list) else None
        try:
            scene_stamp = (target["root"] / use["file"]).stat().st_mtime_ns if scene and use else 0
        except OSError:
            scene_stamp = 0
        key = ("screen", entry["res_path"], scene, json.dumps(nodes), json.dumps(size), stamp,
               scene_stamp)
        if bench.current != key:
            loaded = bench.ask({"op": "screen", "shader": entry["res_path"], "scene": scene,
                                "nodes": nodes, "size": size or []})
            if not loaded.get("ok"):
                bench.current = None
                raise _refused(bench, scene or entry["res_path"], loaded)
            bench.current, bench.focus, bench.loaded = key, None, loaded
        return bench.loaded
    if setup:
        stage = ("stage", hashlib.sha1(setup.encode()).hexdigest())
        if bench.current != stage:
            script = bench.work / f"setup-{stage[1][:12]}.gd"
            script.write_text(setup, encoding="utf-8")
            built = bench.ask({"op": "stage", "setup": str(script)})
            if not built.get("ok"):
                bench.current = None
                raise _refused(bench, "the setup", built)
            bench.current, bench.focus = stage, None
        focus = (entry["res_path"], stamp)
        if bench.focus != focus:
            loaded = bench.ask({"op": "focus", "shader": entry["res_path"]})
            if not loaded.get("ok"):
                bench.focus = None
                raise _refused(bench, entry["res_path"], loaded)
            bench.focus, bench.loaded = focus, loaded
        return bench.loaded
    params = _preset(target["presets"], preset)
    shape = shape or state["shape"] or ("plane" if "plane" in entry["title"] else "sphere")
    size = params.get("node_size") if isinstance(params.get("node_size"), list) else None
    command: dict[str, Any] = {"op": "load", "shader": entry["res_path"], "shape": shape,
                               "size": size or []}
    if shape.startswith(MESH):
        # A model laid on the bench: read from its file, as it is now.
        mesh = _mesh_path(target["project"], shape)
        command.update(shape="mesh", mesh=str(mesh))
        stamp = (stamp, mesh.stat().st_mtime_ns)
    key = ("load", entry["res_path"], shape, json.dumps(size), stamp)
    if bench.current != key:
        loaded = bench.ask(command)
        if not loaded.get("ok"):
            bench.current = None
            raise _refused(bench, entry["res_path"], loaded)
        bench.current, bench.focus, bench.loaded = key, None, loaded
    return bench.loaded


def _posed(bench: Bench, target: dict[str, Any], preset: str, shape: str,
           then: dict[str, Any] | None = None, device: bool = False) -> dict[str, Any]:
    """Lay the specimen, then send `then` if given, as one unit.

    Two specimens asked at the same time (the grid loads its thumbnails
    together) do not cross on the bench. A bench that stops on the way is
    restarted, the specimen laid again, and the command asked again -- once.
    """
    for attempt in range(2):
        try:
            with bench.lock:
                loaded = _pose(bench, target, preset, shape, device)
                if then is None:
                    return loaded
                answer = bench.ask(then)
                if answer.get("error") == EMPTY and attempt == 0:
                    # The bench was restarted without the studio noticing: lay again.
                    bench.current, bench.focus = None, None
                    continue
                return answer
        except BenchLost:
            if attempt:
                raise
    raise ServiceError("the Godot bench does not keep the specimen")  # pragma: no cover


def _imported(project: str) -> Path:
    return project_paths(studio().settings, project).workspace / MESH_FOLDER


def meshes(project: str) -> list[dict[str, str]]:
    """The models a material can be laid on: the game's, the library's, the dropped ones."""
    root = project_paths(studio().settings, project).root
    found: list[dict[str, str]] = []
    if project_paths(studio().settings, project).linked:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if (path.suffix.lower() in MESH_TYPES and path.is_file()
                    and not any(part.startswith(".") or part in survey.SKIPPED
                                for part in relative.parts)
                    and not survey._is_dev(relative.as_posix())):
                found.append({"shape": f"{MESH}game:{relative.as_posix()}", "label": path.stem,
                              "source": "game"})
    st = space(project)
    for asset in st.db.list_assets(kind="mesh", limit=200):
        path = st.store.path_for(asset.id)
        if path is not None and path.suffix.lower() in MESH_TYPES:
            label = str(asset.meta.get("name") or asset.meta.get("entity") or asset.id[:8])
            found.append({"shape": f"{MESH}asset:{asset.id}", "label": label,
                          "source": "library"})
    folder = _imported(project)
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in MESH_TYPES:
                found.append({"shape": f"{MESH}import:{path.name}", "label": path.stem,
                              "source": "import"})
    return found


def _mesh_path(project: str, shape: str) -> Path:
    """The file a `mesh:` shape names, which must be one `meshes` offers."""
    if not any(entry["shape"] == shape for entry in meshes(project)):
        raise NotFound(f"mesh not found: {shape.removeprefix(MESH)}")
    source, _, name = shape.removeprefix(MESH).partition(":")
    if source == "game":
        return project_paths(studio().settings, project).root / name
    if source == "asset":
        path = space(project).store.path_for(name)
        if path is None:
            raise NotFound(f"mesh not found: {name}")
        return path
    return _imported(project) / name


def _check_shape(project: str, shape: str) -> None:
    if shape.startswith(MESH):
        _mesh_path(project, shape)
    elif shape and shape not in SHAPES:
        raise ServiceError(f"unknown shape: {shape} (expected: {', '.join(SHAPES)}, or a "
                           "mesh)")


def import_mesh(project: str, data: bytes, filename: str) -> dict[str, str]:
    """Keep a dropped model for the project, to lay materials on. The game is not touched."""
    suffix = Path(filename).suffix.lower()
    if suffix not in MESH_TYPES:
        raise ServiceError(f"format refused: {suffix or '?'} (expected: "
                           f"{', '.join(MESH_TYPES)})")
    if not data:
        raise ServiceError("empty file: nothing to drop")
    if len(data) > MAX_MESH_BYTES:
        raise ServiceError(f"model too heavy: {len(data) // (1024 * 1024)} MB (at most "
                           f"{MAX_MESH_BYTES // (1024 * 1024)} MB)")
    folder = _imported(project)
    folder.mkdir(parents=True, exist_ok=True)
    stem = documents.slug(Path(filename).stem)[:48] or "mesh"
    target, n = folder / f"{stem}{suffix}", 2
    while target.exists() and target.read_bytes() != data:
        target, n = folder / f"{stem}-{n}{suffix}", n + 1
    target.write_bytes(data)
    return {"shape": f"{MESH}import:{target.name}", "label": target.stem, "source": "import"}


def frame(project: str, specimen_id: str, *, params: dict[str, Any] | None = None,
          preset: str = "", shape: str = "", yaw: float = 0.0, pitch: float = 0.0,
          zoom: float = 1.0, scale: float = 1.0, format: str = "jpg",
          background: str = "", device: str = "") -> dict[str, Any]:
    """An image of the specimen, rendered by Godot with these settings.

    `preset`: a game use (its settings are the starting point), `default` for
    the shader's own values; `params` are added on top, and what an image does
    not repeat goes back to its starting value. `yaw`, `pitch` and `zoom` turn
    the camera around a material, an object or a sky. `format`: `jpg` (fast,
    opaque, on `background`) or `webp` (an interface keeps its transparency).
    `device` (`phone`, `tablet`, `desktop`): the whole screen of that device,
    at its resolution -- an interface shader in the scene of its use, placed
    and stretched as the game's settings place it, with the black bars a kept
    aspect leaves; a material or a sky through a camera of that format.
    `scale` then does not apply. Free, local.
    """
    _check_shape(project, shape)
    if not 0.25 <= scale <= 4:
        raise ServiceError("scale must be between 0.25 and 4")
    if format not in FORMATS:
        raise ServiceError(f"unknown format: {format} (expected: {', '.join(FORMATS)})")
    if background and not _HEX.match(background):
        raise ServiceError(f"unreadable background: {background} (expected: #rrggbb)")
    target = _target(project, specimen_id)
    entry, state = target["entry"], target["state"]
    if not entry["renderable"]:
        raise ServiceError(f"{entry['file']} ({entry['kind']}) is not rendered alone: an include "
                           "shows in the shaders that use it")
    chosen = preset or state["preset"]
    screen = None
    if device:
        sizes = {known["id"]: known for known in target["screen"]["devices"]}
        if device not in sizes:
            raise ServiceError(f"unknown device: {device} (expected: {', '.join(sizes)})")
        width, height = sizes[device]["width"], sizes[device]["height"]
        flat = entry["kind"] == "canvas_item" and not state["setup"]
        screen = _layout(target["screen"], width, height) if flat else {"size": [width, height]}
    in_scene = screen is not None and "logical" in screen and bool(
        _scene_of(target, _use(target["presets"], chosen)))
    # A use's scene already holds its settings, and its scripts set some of them
    # (a size that follows the screen): only the changes are sent.
    merged = {} if in_scene else {
        key: value for key, value in _preset(target["presets"], chosen).items()
        if not isinstance(value, str)}
    merged.update(params or {})
    started = time.monotonic()
    bench = _bench_for(project, entry)
    command: dict[str, Any] = {
        "op": "frame", "params": merged, "yaw": yaw, "pitch": pitch, "zoom": zoom,
        "scale": scale, "format": format, "background": background}
    if screen is not None:
        command["screen"] = screen
    answer = _posed(bench, target, chosen, shape, command, device=bool(device))
    if not answer.get("ok"):
        raise ServiceError(f"image refused by the bench: {answer.get('error')}")
    kind = answer.get("format", format)
    return {"image": base64.b64decode(answer["data"]), "format": kind,
            "media_type": FORMATS.get(kind, "image/webp"), "width": answer["width"],
            "height": answer["height"], "ms": round((time.monotonic() - started) * 1000),
            "bench_ms": answer.get("ms", {})}


def thumbnail(project: str, specimen_id: str) -> Path:
    """A specimen's thumbnail, rendered once and kept while nothing changes."""
    target = _target(project, specimen_id)
    entry, state = target["entry"], target["state"]
    # The shown use's settings are part of the key: once saved, the thumbnail is redone.
    shown = _preset(target["presets"], state["preset"]) if state["preset"] != "default" else {}
    stamp = hashlib.sha1(json.dumps(
        [THUMB_VERSION, target["source"].stat().st_mtime_ns, state["setup"], state["shape"],
         state["preset"], shown], sort_keys=True, default=str).encode()).hexdigest()[:12]
    cache = project_paths(studio().settings, project).workspace / "lookdev"
    path = cache / f"{specimen_id}-{stamp}.webp"
    if path.is_file():
        return path
    # An interface renders at double size, on a transparent background: the
    # thumbnail stays sharp without upscaling, and sits on the card as it is.
    flat = entry["kind"] == "canvas_item" and not state["setup"]
    shot = frame(project, specimen_id, yaw=25, pitch=12, scale=2.0 if flat else 1.0,
                 format="webp")
    cache.mkdir(parents=True, exist_ok=True)
    for old in cache.glob(f"{specimen_id}-*.webp"):
        old.unlink(missing_ok=True)
    path.write_bytes(shot["image"])
    return path


# ------------------------------------------------------------- decisions


def set_state(project: str, specimen_id: str, *, setup: str | None = None,
              shape: str | None = None, preset: str | None = None) -> dict[str, Any]:
    """How to present a specimen: its setup, its template, its use.

    `setup`: a GDScript whose `build()` returns the real game object carrying
    the shader (empty: the template). Only the given fields change. The game is
    not touched.
    """
    _, _, entry = _find(project, specimen_id)
    state = _state(project, specimen_id)
    if setup is not None:
        code = setup.strip()
        if len(code) > MAX_SETUP:
            raise ServiceError("setup too long")
        if code and "func build" not in code:
            raise ServiceError("the setup defines `func build() -> Node3D`, which returns the "
                               "game object to place on the bench")
        state["setup"] = code + "\n" if code else ""
    if shape is not None:
        _check_shape(project, shape)
        state["shape"] = shape
    if preset is not None:
        state["preset"] = preset
    state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    _forget(project, specimen_id)
    folder = _state_dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{specimen_id}.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {**entry, **state, "staged": bool(state["setup"])}


def font_file(project: str, file: str) -> Path:
    """A game font, to dress the page: only those the game map surveys."""
    root, game = _game(project)
    if file not in game["fonts"]:
        raise NotFound(f"unknown font: {file}")
    return root / file


# ------------------------------------------------------------- saving


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ServiceError(f"{name}: a number is expected")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ServiceError(f"{name}: value is not finite")
    return number


def _components(gltype: str, color: bool, value: Any, name: str) -> list[float]:
    """The components of a vector setting, checked against its type."""
    count = _WRITABLE[gltype]
    if not isinstance(value, list):
        raise ServiceError(f"{name}: a list of {count} numbers is expected")
    numbers = [_number(part, name) for part in value]
    if color and gltype == "vec4" and len(numbers) == 3:
        numbers.append(1.0)
    if color and gltype == "vec3" and len(numbers) == 4:
        numbers = numbers[:3]
    if len(numbers) != count:
        raise ServiceError(f"{name}: {count} components expected, {len(numbers)} received")
    if gltype.startswith(("i", "u")):
        return [float(round(part)) for part in numbers]
    return numbers


def _short(number: float) -> str:
    """A component the way Godot writes it in a scene: `720`, `0.62`."""
    return str(int(number)) if number.is_integer() else repr(number)


def _glsl_float(number: float) -> str:
    """A float in GLSL: always a decimal point, never an exponent (`95.0`, `0.0001`)."""
    text = repr(number)
    if "e" in text or "E" in text:
        text = f"{number:.12f}".rstrip("0")
    if "." not in text:
        text += ".0"
    return text + "0" if text.endswith(".") else text


def _literals(gltype: str, hint: str, value: Any, name: str) -> tuple[str, str]:
    """A setting written twice: as Godot stores it in a material, and in GLSL."""
    if gltype not in _WRITABLE:
        raise ServiceError(f"{name} ({gltype}) cannot be saved from the Universe")
    if gltype == "bool":
        if not isinstance(value, bool):
            raise ServiceError(f"{name}: true or false is expected")
        text = "true" if value else "false"
        return text, text
    if gltype in ("int", "uint"):
        number = round(_number(value, name))
        if gltype == "uint" and number < 0:
            raise ServiceError(f"{name}: a non-negative integer is expected")
        return str(number), f"{number}u" if gltype == "uint" else str(number)
    if gltype == "float":
        number = _number(value, name)
        return repr(number), _glsl_float(number)
    color = "source_color" in hint
    parts = _components(gltype, color, value, name)
    if color:
        rgba = [*parts, 1.0][:4]
        material = f"Color({', '.join(_short(part) for part in rgba)})"
    else:
        suffix = "i" if gltype[0] in "iu" else ""
        material = f"Vector{gltype[-1]}{suffix}({', '.join(_short(part) for part in parts)})"
    glsl = ", ".join(_short(part) if gltype[0] in "iu" else _glsl_float(part) for part in parts)
    return material, f"{gltype}({glsl})"


def _declaration(text: str, name: str) -> re.Match[str]:
    found = re.search(_DECL.format(name=re.escape(name)), text)
    if found is None:
        raise ServiceError(f"{name} is not a setting of this shader")
    if (found.group("scope") or "").strip() == "global":
        raise ServiceError(f"{name} is a project-wide global setting: it cannot be saved "
                           "from a shader")
    return found


def _write(path: Path, text: str) -> None:
    """Replace a game file in one move: never half written."""
    temporary = path.with_name(f".{path.name}.gamestudio")
    temporary.write_text(text, encoding="utf-8", newline="")
    with contextlib.suppress(OSError):
        shutil.copymode(path, temporary)
    os.replace(temporary, path)


def _save_defaults(source: Path, literals: dict[str, str]) -> list[str]:
    """Settings written as the shader's defaults: `uniform float x = 95.0;`."""
    text = source.read_text(encoding="utf-8")
    changed = []
    for name, value in literals.items():
        found = _declaration(text, name)
        if (found.group("default") or "").strip() == value:
            continue
        text = f"{text[:found.start()]}{found.group('head')} = {value};{text[found.end():]}"
        changed.append(name)
    if changed:
        _write(source, text)
    return changed


def _save_material(path: Path, block: str, literals: dict[str, str]) -> list[str]:
    """Settings written into a material: its `shader_parameter/` lines, and only those."""
    text = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if block == "" and line.strip() == "[resource]":
            start = index
        elif block and line.startswith("[sub_resource"):
            attrs = survey._attrs(line)
            if attrs.get("id") == block and attrs.get("type") == "ShaderMaterial":
                start = index
        if start is not None:
            break
    if start is None:
        raise ServiceError(f"material {block or '[resource]'} is no longer in {path.name}")
    end = next((index for index in range(start + 1, len(lines))
                if lines[index].startswith("[")), len(lines))
    changed = []
    for name, value in literals.items():
        line = f"shader_parameter/{name} = {value}"
        held = next((index for index in range(start + 1, end)
                     if re.match(rf"shader_parameter/{re.escape(name)}\s*=", lines[index])), None)
        if held is not None:
            if lines[held].strip() == line:
                continue
            lines[held] = line
        else:
            # After the material's last setting, else after its shader: Godot
            # restores its own order the next time it saves the file.
            anchor = max((index for index in range(start + 1, end)
                          if lines[index].startswith(("shader_parameter/", "shader ="))),
                         default=start)
            lines.insert(anchor + 1, line)
            end += 1
        changed.append(name)
    if changed:
        _write(path, newline.join(lines) + (newline if text.endswith(("\n", "\r")) else ""))
    return changed


def save(project: str, specimen_id: str, *, params: dict[str, Any],
         preset: str = "") -> dict[str, Any]:
    """Write settings into the game, where the shown use takes them.

    `preset`: a use in the game (`lookdev_specimen` lists them) -- its
    `shader_parameter/` lines are rewritten or added in its material, and
    nothing else in the file moves; `default` writes the shader's default
    values (`uniform float x = 95.0;`). Without `preset`, the use shown by
    default. A setting already at that value is not rewritten. The game folder
    changes: its git shows it, and a Godot editor open on that file must
    reload it before saving there. Free, local.
    """
    if not params:
        raise ServiceError("no setting to save")
    target = _target(project, specimen_id, fresh=True)
    entry = target["entry"]
    shader = target["source"].read_text(encoding="utf-8", errors="replace")
    literals: dict[str, tuple[str, str]] = {}
    for name, value in params.items():
        found = _declaration(shader, name)
        literals[name] = _literals(found.group("type"), found.group("hint") or "", value, name)
    chosen = preset or target["state"]["preset"]
    presets = target["presets"]
    use = (None if chosen == "default" or not presets
           else next((p for p in presets if p["id"] == chosen), None) if chosen
           else presets[0])
    if chosen and chosen != "default" and use is None:
        raise NotFound(f"use not found: {chosen}")
    root = project_paths(studio().settings, project).root
    if use is None:
        relative = entry["file"]
        written = _save_defaults(target["source"],
                                 {name: glsl for name, (_, glsl) in literals.items()})
    else:
        relative = use["file"]
        written = _save_material(root / relative, use["block"],
                                 {name: material for name, (material, _) in literals.items()})
    # What was read from the game no longer holds, for this specimen as for
    # those sharing its file.
    with _TARGETS_LOCK:
        for key in [key for key in _TARGETS if key[1] == project]:
            _TARGETS.pop(key, None)
    return {"specimen": specimen_id, "file": relative,
            "preset": use["id"] if use else "default",
            "label": use["label"] if use else "Shader values",
            "written": written, "unchanged": [name for name in params if name not in written]}
