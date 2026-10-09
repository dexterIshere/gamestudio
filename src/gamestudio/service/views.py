"""The game's views: its main pages, found in its files, each drawn by its own engine.

A game is a handful of pages the player moves between -- a boot screen, the
world, a menu, a panel opened from a bar. They are found without running
anything (`survey.game_map`): the Godot project's main scene, then every scene
a script loads as a whole, rather than one a scene places inside itself as a
piece. A screen (a Control, a CanvasLayer) or a world (Node2D, Node3D) is a
view; a widget -- a button, a row, a slot -- is a piece of one, not a page.
No verdict: a view the game has is listed.

Each view is drawn by the engine (`renders.render_png`) when the page asks for
it, once, and kept in the workspace (`.gamestudio/workspace/views/`) until its
scene changes.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from ..store.folders import project_paths
from . import documents, renders, survey
from .context import studio
from .errors import NotFound, ServiceError

WORK_FOLDER = "views"
# A view's image: its longest side, in pixels -- a thumbnail that stays sharp.
IMAGE_SIDE = 960
# A page is drawn once it has settled: its arrival animation over, its data in.
IMAGE_DELAY = 3.0
# Bumped when the way a view is drawn changes: older images are redrawn.
IMAGE_VERSION = 2
# The words that name a piece of a page rather than a page.
PIECE_WORDS = frozenset({"row", "item", "cell", "tile", "slot", "entry", "button", "icon",
                         "badge", "chip", "tooltip", "widget", "line", "bar", "toast"})
INTERFACE_SHELF = "design/interface"

_WORDS = re.compile(r"[a-z]+")
# One render at a time per server: a view is a whole Godot run.
_RENDER_LOCK = threading.Lock()


def _root(project: str) -> Path:
    paths = project_paths(studio().settings, project)
    if not paths.linked:
        raise ServiceError(f"project {project} has no game folder: nothing to show")
    return paths.root


def _kind(root_type: str) -> str:
    if root_type.endswith("3D"):
        return "world-3d"
    if root_type.endswith("2D") or root_type in ("TileMap", "TileMapLayer"):
        return "world-2d"
    return "screen"


def _piece(scene: dict[str, Any]) -> bool:
    """A scene that is a piece of a page: a widget by its type or by its name."""
    words = set(_WORDS.findall(Path(scene["path"]).stem.lower()))
    return survey.is_prop_type(scene["root"]) or bool(words & PIECE_WORDS)


def _title(path: str) -> str:
    stem = Path(path).stem.replace("_", " ").replace("-", " ").strip()
    return stem[:1].upper() + stem[1:]


def _cards(project: str) -> list[dict[str, str]]:
    """The interface cards, with their text: a view is matched to the one citing it."""
    found = []
    for entry in documents.documents(project, INTERFACE_SHELF):
        try:
            text = Path(entry["path"]).read_text(encoding="utf-8")
        except OSError:
            continue
        found.append({"name": entry["name"], "title": entry["title"], "text": text})
    return found


def views(project: str) -> dict[str, Any]:
    """The game's main pages, the one it starts on first.

    Each: `id`, `title` (its card's when the card describes it alone or it is
    the start page, else its file's), `file` (from the game root),
    `res_path`, `root` (its
    node type), `kind` (`screen`, `world-2d`, `world-3d`), `entry` (the game
    starts on it), `opened_from` (the scripts that load it), `script` and
    `nodes`, and `card` -- the interface card citing it, if any.
    """
    root = _root(project)
    game = survey.game_map(root)
    entries = {}
    for entry in game["godot"]:
        main = str(entry.get("main_scene") or "")
        if main.startswith("res://"):
            prefix = f"{entry['path']}/" if entry["path"] else ""
            entries[prefix + main.removeprefix("res://")] = entry["path"]
    cards = _cards(project)
    found: list[dict[str, Any]] = []
    taken: set[str] = set()
    for scene in game["scenes"]:
        if scene["dev"] or not scene["root"]:
            continue
        users = [user for user in scene.get("users", []) if user != scene["path"]]
        placed = [user for user in users if user.endswith((".tscn", ".scn"))]
        entry = scene["path"] in entries
        if not entry and (placed or not users or _piece(scene)):
            continue
        base = documents.slug(Path(scene["path"]).stem) or "view"
        view_id, n = base, 2
        while view_id in taken:
            view_id, n = f"{base}-{n}", n + 1
        taken.add(view_id)
        godot = next((item["path"] for item in game["godot"]
                      if not item["path"] or scene["path"].startswith(item["path"] + "/")), "")
        res_path = "res://" + scene["path"].removeprefix(f"{godot}/" if godot else "")
        name = Path(scene["path"]).name
        card = next((item for item in cards if res_path in item["text"] or name in item["text"]),
                    None)
        found.append({
            # The card's title says what the page is; the file's name, at least where it is.
            "id": view_id, "title": card["title"] if card else _title(scene["path"]),
            "file": scene["path"],
            "res_path": res_path, "root": scene["root"], "kind": _kind(scene["root"]),
            "entry": entry, "opened_from": [user for user in users if user.endswith(".gd")],
            "script": scene["script"], "nodes": scene["nodes"],
            # Changes with the scene: the page asks for the image again.
            "version": f"{IMAGE_VERSION}-{(root / scene['path']).stat().st_mtime_ns}",
            "card": {"name": card["name"], "title": card["title"]} if card else None})
    # A card describing several views names only the start page among them; the
    # others keep their file's name.
    shared = [view["card"]["name"] for view in found if view["card"]]
    for view in found:
        if view["card"] and shared.count(view["card"]["name"]) > 1 and not view["entry"]:
            view["title"] = _title(view["file"])
    found.sort(key=lambda view: (not view["entry"], view["kind"] != "screen", view["title"]))
    return {"project": project, "views": found}


def _view(project: str, view_id: str) -> dict[str, Any]:
    for view in views(project)["views"]:
        if view["id"] == view_id:
            return view
    raise NotFound(f"view not found: {view_id}")


def image(project: str, view_id: str) -> Path:
    """A view's image, drawn by the game's engine the first time, then kept until
    its scene changes."""
    view = _view(project, view_id)
    root = _root(project)
    scene = root / view["file"]
    stamp = f"{IMAGE_VERSION}:{scene.stat().st_mtime_ns}:{scene.stat().st_size}"
    folder = project_paths(studio().settings, project).workspace / WORK_FOLDER
    output = folder / f"{view_id}.png"
    kept = folder / f"{view_id}.json"
    with _RENDER_LOCK:
        try:
            if output.is_file() and json.loads(kept.read_text(encoding="utf-8")) == stamp:
                return output
        except (OSError, json.JSONDecodeError):
            pass
        target = renders.resolve_scene(project, view["file"])
        display = target["godot"]["display"]
        width = int(display.get("viewport_width") or 1152)
        height = int(display.get("viewport_height") or 648)
        scale = min(1.0, IMAGE_SIDE / max(width, height))
        folder.mkdir(parents=True, exist_ok=True)
        renders.render_png(target, output, scale=scale, delay=IMAGE_DELAY)
        kept.write_text(json.dumps(stamp), encoding="utf-8")
    return output
