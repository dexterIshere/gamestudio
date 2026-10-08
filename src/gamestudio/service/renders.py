"""A game's renders: one of its scenes, drawn by its own engine, off screen.

An interface, art direction or world card reads better with the image of what
it describes. A screenshot needs the game open, at its window size, and shows
whatever was passing by; a **render** asks the engine to draw the wanted scene,
at the wanted resolution (twice the game's by default), without showing
anything. It is the route to prefer.

Godot draws the scene in a SubViewport (`godot/render_scene.gd`). So that no
window appears, it runs in a virtual display -- `gamescope` without output, or
`xvfb-run`; with neither, a window opens for a moment, and the render says so.
The game's user data (`user://`) is redirected to a temporary folder: a render
touches neither the player's settings nor their saves.

The image enters the project's store; the library files it under
`renders/<name>.png`, where the latest render of a name wins -- a card cites it
once, and a new render updates it.

A render illustrating a section's cards -- the case of every brief, which asks
for the image of each element it surveys -- is not filed with free images: it
lives under `briefing/<section>/`, and the Library page groups them under
"Briefing". The section is that of the cards citing it, and the render keeps it
(`briefing` in its metadata): a brief's render therefore finds its place by
itself, even when an agent filed it.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..store.folders import BRIEFING_FOLDER, project_paths
from ..store.library import Librarian, render_folder
from . import documents, survey
from .context import space, studio
from .errors import NotFound, ServiceError

SCRIPT = Path(__file__).resolve().parent.parent / "godot" / "render_scene.gd"
ROLE = "render"
FOLDER = "renders"

# The script stops itself when a scene does not render (45 s after its delay);
# this timeout is the last resort, when the engine no longer answers.
TIMEOUT = 90.0
IMPORT_TIMEOUT = 600.0
MAX_SCALE = 4.0
MAX_SIDE = 8192

_RESULT = re.compile(r"GAMESTUDIO_RENDER: (OK (\d+)x(\d+)|FAILED (.*))")
_STARTED = "GAMESTUDIO_RENDER: START"


def _godot_binary() -> str:
    binary = shutil.which("godot") or shutil.which("godot4")
    if binary is None:
        raise ServiceError("Godot not found on the PATH: rendering goes through it")
    return binary


def displays(width: int, height: int) -> list[tuple[str, list[str]]]:
    """The displays to run the engine on, from the most discreet to the most visible.

    `gamescope` without output and `xvfb-run` render without showing anything;
    otherwise the engine opens its window on the user's screen, for a moment.
    """
    found: list[tuple[str, list[str]]] = []
    if shutil.which("gamescope"):
        w, h = str(width), str(height)
        found.append(("gamescope", ["gamescope", "--backend", "headless",
                                    "-w", w, "-h", h, "-W", w, "-H", h, "--"]))
    if shutil.which("xvfb-run"):
        found.append(("xvfb", ["xvfb-run", "-a", "-s", f"-screen 0 {width}x{height}x24"]))
    found.append(("window", []))
    return found


def _godot_project(project: str, godot: str, scene: str,
                   root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """The game's Godot project to render in: the only one, or the one named.

    `root` replaces the game folder with another copy of it -- the branch of a
    screen being edited (`service/screens.py`).
    """
    root = root or project_paths(studio().settings, project).root
    found = survey.game_map(root)["godot"]
    if not found:
        raise ServiceError(f"no Godot project in {root}: nothing to render")
    if godot:
        wanted = godot.strip("/")
        for entry in found:
            if entry["path"] == wanted or (wanted == "." and not entry["path"]):
                return root, entry
        raise NotFound(f"Godot project not found: {godot} (known: "
                       f"{', '.join(e['path'] or '.' for e in found)})")
    if len(found) == 1:
        return root, found[0]
    # Several projects: the scene, given from the game root, says which one.
    for entry in sorted(found, key=lambda e: -len(e["path"])):
        if entry["path"] and scene.startswith(entry["path"] + "/"):
            return root, entry
    raise ServiceError("several Godot projects in the game: specify `godot` "
                       f"({', '.join(e['path'] or '.' for e in found)})")


def resolve_scene(project: str, scene: str = "", godot: str = "",
                  root: Path | None = None) -> dict[str, Any]:
    """The scene to render: `res://…`, or a path from the game root.

    Without a scene, the Godot project's main scene -- the game as it starts.
    """
    root, entry = _godot_project(project, godot, scene, root)
    base = root / entry["path"] if entry["path"] else root
    scene = scene.strip()
    if not scene:
        scene = entry["main_scene"]
        if not scene:
            raise ServiceError("no main scene declared: specify `scene`")
    if scene.startswith("res://"):
        res_path = scene
    else:
        relative = scene.removeprefix("./")
        prefix = f"{entry['path']}/" if entry["path"] else ""
        if prefix and not relative.startswith(prefix):
            raise NotFound(f"{scene} is not in the Godot project `{entry['path']}/`")
        res_path = "res://" + relative.removeprefix(prefix)
    file = (base / res_path.removeprefix("res://")).resolve()
    if not file.is_relative_to(base.resolve()) or not file.is_file():
        raise NotFound(f"scene not found: {res_path}")
    if file.suffix not in (".tscn", ".scn"):
        raise ServiceError(f"not a Godot scene: {res_path}")
    return {"root": root, "base": base, "godot": entry, "res_path": res_path,
            "local": file.relative_to(root.resolve()).as_posix()}


def setup_script(code: str) -> str:
    """The setup an agent writes, as the body of a `setup(scene)` function."""
    lines = [("\t" + line) if line.strip() else "" for line in code.splitlines()]
    body = "\n".join(lines) if any(lines) else "\tpass"
    return f"extends RefCounted\n\n\nfunc setup(scene: Node) -> void:\n{body}\n"


def _run(command: list[str], *, env: dict[str, str], timeout: float) -> str:
    """Run a command in its own process group: a timeout stops all of it,
    virtual display included."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, env=env, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        # The script had started: the scene does not render. Otherwise the
        # display is at fault, and the caller tries another one.
        if _STARTED in (output or ""):
            return (output or "") + "\nGAMESTUDIO_RENDER: FAILED the engine no longer answers"
        return output or ""
    return output or ""


def _ensure_imported(binary: str, base: Path, env: dict[str, str]) -> bool:
    """A project never opened in the editor has not imported its resources, and
    the game cannot load them without that: import once, headless."""
    if (base / ".godot").is_dir():
        return False
    _run([binary, "--headless", "--path", str(base), "--import"], env=env,
         timeout=IMPORT_TIMEOUT)
    return True


# ------------------------------------------------------- brief images

def run_engine(base: Path, script: Path, args: list[str], width: int, height: int, *,
               notes: list[str] | None = None,
               timeout: float = TIMEOUT) -> tuple[str, str, re.Match[str] | None]:
    """Run a studio script in the game's engine, off screen.

    The most discreet display first (`displays`); a virtual display that did
    not start is retried once, then the next one is tried -- a script failure
    is not replayed. The player's data (`user://`) is redirected. Return the
    display used, the log, and the last `GAMESTUDIO_RENDER` line (or None).
    `notes` receives what deserves to be said.
    """
    binary = _godot_binary()
    notes = notes if notes is not None else []
    with tempfile.TemporaryDirectory(prefix="gamestudio-engine-") as work:
        env = {**os.environ, "XDG_DATA_HOME": str(Path(work) / "user")}
        if _ensure_imported(binary, base, env):
            notes.append("resources imported a first time (the project's .godot/ cache)")
        engine, log, result = "", "", None
        attempts = [(name, prefix) for name, prefix in displays(width, height)
                    for _ in range(1 if name == "window" else 2)]
        for candidate, prefix in attempts:
            engine = candidate
            command = [*prefix, binary, "--path", str(base),
                       "--resolution", f"{width}x{height}", "--script", str(script),
                       "--", *args]
            log = _run(command, env=env, timeout=timeout)
            matches = list(_RESULT.finditer(log))
            result = matches[-1] if matches else None
            if result is not None:
                break
    if engine == "window":
        notes.append("no virtual display (gamescope, xvfb-run): a window opened for a "
                     "moment")
    return engine, log, result


def render_png(target: dict[str, Any], output: Path, *, scale: float = 2.0,
               width: int = 0, height: int = 0, delay: float = 0.5, crop: bool = False,
               transparent: bool = False, locale: str = "", setup: str = "",
               nodes: Path | None = None) -> dict[str, Any]:
    """Draw a resolved scene (`resolve_scene`) into `output`, filing nothing.

    The raw render: no store, no library. `render_scene` files it; the screen
    editor keeps it in its workspace, with the survey of the Controls (`nodes`)
    that lets one point at them on the image.
    """
    entry = target["godot"]
    base_w = width or int(entry["display"].get("viewport_width") or 1152)
    base_h = height or int(entry["display"].get("viewport_height") or 648)
    if max(base_w, base_h) * scale > MAX_SIDE:
        raise ServiceError(f"image too large: {base_w * scale:.0f} x {base_h * scale:.0f} px (at "
                           f"most {MAX_SIDE} per side)")
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="gamestudio-render-") as work:
        temp = Path(work)
        args = [f"--gs-scene={target['res_path']}", f"--gs-out={output}",
                f"--gs-scale={scale}", f"--gs-delay={delay}"]
        if width and height:
            args += [f"--gs-width={width}", f"--gs-height={height}"]
        if crop:
            args.append("--gs-crop")
        if transparent:
            args.append("--gs-transparent")
        if locale:
            args.append(f"--gs-locale={locale}")
        if nodes is not None:
            args.append(f"--gs-nodes={nodes}")
        if setup.strip():
            helper = temp / "setup.gd"
            helper.write_text(setup_script(setup), encoding="utf-8")
            args.append(f"--gs-setup={helper}")
        engine, log, result = run_engine(target["base"], SCRIPT, args, base_w, base_h,
                                         notes=notes)
        if result is None or result.group(4) is not None or not output.is_file():
            reason = result.group(4) if result is not None and result.group(4) else \
                "the engine rendered nothing"
            tail = "\n".join(line for line in log.splitlines()[-12:] if line.strip())
            raise ServiceError(f"cannot render {target['res_path']}: {reason}\n{tail}")
    return {"engine": engine, "width": int(result.group(2)),
            "height": int(result.group(3)), "notes": notes}


def librarian(db: Any, store: Any, settings: Any) -> Librarian:
    """A project's librarian, which files brief images with their cards.

    Every mirror of a project syncs through it -- the project space, the worker,
    a character build: a librarian unaware of sections would move a card's
    render from `briefing/<section>/` back to `renders/` on its first sync.
    """
    built = Librarian.for_settings(db, store, settings)
    built.sections = briefings
    return built


def briefings(project: str) -> dict[str, list[str]]:
    """The sections that have cards, and the name of each card.

    This is how a brief's render is recognized: a render carries a card's name
    (`planet-visit.png` for `planet-visit.md`), and its shelf says which
    section it belongs to.
    """
    root = documents.directory(project)
    found: dict[str, list[str]] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*.md")):
        folder = path.parent.relative_to(root).as_posix()
        if not _section(folder):
            continue
        found.setdefault(folder, []).append(path.stem)
    return found


def _section(value: str) -> str:
    """A fileable section: its segments validated one by one, otherwise "".

    A card's shelf may be fileable nowhere (a folder made by hand, a name
    outside the convention): no guessing, such a render stays in `renders/`.
    """
    try:
        shelf = documents.shelf(value)
    except ServiceError:
        return ""
    return value.strip("/") if shelf else ""


def briefing_tag(folder: str) -> str:
    """The tag stored in a render's metadata: `briefing/<section>`."""
    section = _section(folder)
    return f"{BRIEFING_FOLDER}/{section}" if section else ""


def briefing_folder(asset: Any, briefings: dict[str, list[str]]) -> str:
    """A render's library folder, from its brief tag."""
    tag = str(asset.meta.get("briefing") or "")
    section = tag[len(BRIEFING_FOLDER) + 1:] if tag.startswith(f"{BRIEFING_FOLDER}/") else ""
    return f"{BRIEFING_FOLDER}/{section}" if section in briefings else ""


def render_scene(project: str, scene: str = "", *, name: str = "", folder: str = "",
                 scale: float = 2.0, width: int = 0, height: int = 0, delay: float = 0.5,
                 crop: bool = False, transparent: bool = False, locale: str = "",
                 setup: str = "", godot: str = "") -> dict[str, Any]:
    """Render a game scene to PNG with its engine, off screen, and file it.

    `name` and `folder`: the card the image illustrates, and its section
    (`design/interface`). Such a render is a brief image: it is filed under
    `briefing/<section>/` in the library (see `store.library.render_folder`),
    and the card's page shows it by itself ("Current render", see
    `service/cards.py`) -- the card's text need not cite it. The result's
    `markdown` line is for citing it elsewhere: a devlog, another card.

    The call's settings are kept in the metadata, enough to redo the render
    identically (`cards.rerender`).
    """
    if not 0.25 <= scale <= MAX_SCALE:
        raise ServiceError(f"scale out of bounds: {scale} (0.25 to {MAX_SCALE})")
    target = resolve_scene(project, scene, godot)
    entry = target["godot"]
    label = documents.slug(name or Path(target["res_path"]).stem)

    with tempfile.TemporaryDirectory(prefix="gamestudio-render-") as work:
        output = Path(work) / f"{label}.png"
        drawn = render_png(target, output, scale=scale, width=width, height=height,
                           delay=delay, crop=crop, transparent=transparent,
                           locale=locale, setup=setup)
        notes = drawn["notes"]

        st = space(project)
        when = datetime.now(UTC).isoformat(timespec="seconds")
        # The call's settings are kept as given -- requested width and height
        # (0: the game's), delay, background, setup code.
        asset = st.store.put_file(output, kind="image", meta={
            "role": ROLE, "project": project, "name": label,
            "scene": target["res_path"], "godot": entry["path"], "scale": scale,
            "crop": crop, "setup": bool(setup.strip()), "locale": locale,
            "setup_code": setup if setup.strip() else "",
            "width": width, "height": height, "delay": delay,
            "transparent": transparent,
            "rendered_at": when, "briefing": briefing_tag(folder)})
        st.db.save_asset(asset)

    st.librarian.sync_project(project)
    # The image lives under its cards' section when it has one: the returned
    # path is the library's, not a recomposed one.
    mirror = st.librarian.project_dir(project)
    library = mirror / render_folder(asset, briefings(project)) / f"{label}.png"
    found = {"project": project, "asset_id": asset.id, "name": label,
             "scene": target["res_path"], "file": target["local"],
             "width": drawn["width"], "height": drawn["height"],
             "scale": scale, "engine": drawn["engine"], "path": str(library),
             "library": library.relative_to(mirror).as_posix(), "notes": notes}
    if folder:
        shelf = documents.directory(project, folder)
        relative = os.path.relpath(library, shelf)
        found["markdown"] = f"![{label}]({Path(relative).as_posix()})"
    return found
