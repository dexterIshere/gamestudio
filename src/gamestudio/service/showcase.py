"""The icons and props showcase: each element of the game shown as it is, to be judged.

An icon or a button is not criticized from a description: one looks at it, next
to its neighbours, at the size the player sees it, on the game's background.
The showcase takes each element out of the game and shows it alone:

- an **icon** is its file, as is, filed with its family (its folder);
- a **prop** is drawn by Godot itself, alone, cropped, on a transparent
  background, and for a button in each of its states (normal, hover, pressed,
  disabled -- shown without a mouse, with the box and colors the theme gives
  them: a state the theme does not distinguish comes out identical to
  "normal", and the showcase says so); a StyleBox is laid on a panel; a
  button, arrow or frame texture is its file.

A whole batch of props is drawn in a single engine run (`godot/showcase.gd`),
in a few seconds; the images are kept in the workspace
(`.gamestudio/workspace/showcase/props/`) and redrawn when the scene or what it
uses changes.

The showcase holds no verdict: what is in the game is kept, what is no longer
wanted leaves it. What needs rework is discussed with an agent opened on the
element itself (`handoff.showcase_brief`). The showcase never modifies the
game.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..godot.tscn_edit import Scene
from ..store.folders import project_paths
from . import documents, lookdev, renders, survey, trash
from .context import studio
from .errors import NotFound, ServiceError

SCRIPT = Path(__file__).resolve().parent.parent / "godot" / "showcase.gd"
KINDS = ("icons", "props")
# A showcase's label, and the shelf of its cards.
LABELS = {"icons": "Icons", "props": "Props"}
SHELVES = {"icons": "design/icons", "props": "design/props"}
WORK_FOLDER = "showcase"
# A button's states, in the order they are judged (`ui_frame.gd`).
STATES = ("normal", "hover", "pressed", "disabled")
STYLEBOX_SIZE = (160, 56)
SCALE = 2.0
# Bumped when the way a prop is drawn changes: older images are redrawn.
RENDER_VERSION = 1
# A batch of props: one engine run, a few seconds per ten elements.
BATCH_TIMEOUT = 300.0

_LINE = re.compile(r"GAMESTUDIO_SHOWCASE: (\S+) (?:OK (\d+)x(\d+)|FAILED (.*))")
# Two drawings of the same batch must not step on each other.
_LOCK = threading.Lock()


# ----------------------------------------------------------------- the game


def _game(project: str) -> tuple[Path, dict[str, Any]]:
    paths = project_paths(studio().settings, project)
    if not paths.linked:
        raise ServiceError(f"project {project} has no game folder: nothing to show")
    return paths.root, survey.game_map(paths.root)


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise NotFound(f"unknown showcase: {kind} (known: {', '.join(KINDS)})")
    return kind


def element_id(relative: str) -> str:
    """An element's id: its path in the game, as one stable word."""
    return documents.slug(relative.replace(".", "-")) or "element"


def _family(folder: str) -> str:
    """A family's name: the last segment of its folder."""
    return folder.rsplit("/", 1)[-1] if folder and folder != "." else "root"


def _size(text: str) -> tuple[float | None, float | None]:
    if "x" not in text:
        return None, None
    width, height = text.split("x", 1)
    return float(width), float(height)


# -------------------------------------------------------------- icons


def _icons(project: str, game: dict[str, Any]) -> list[dict[str, Any]]:
    families: dict[str, list[dict[str, Any]]] = {}
    for icon in game["icons"]:
        element = element_id(icon["path"])
        width, height = _size(icon["size"])
        families.setdefault(icon["folder"], []).append({
            "id": element, "type": "image", "file": icon["path"],
            "title": Path(icon["path"]).stem, "format": icon["format"],
            "width": width, "height": height, "users": icon["users"],
            "states": []})
    return [{"id": element_id(folder), "label": _family(folder), "folder": folder,
             "items": items} for folder, items in sorted(families.items())]


# --------------------------------------------------------------- props


def _fills_screen(root: Path, relative: str) -> bool:
    """A scene whose root covers the screen is a screen, not a prop."""
    try:
        scene = Scene((root / relative).read_text(encoding="utf-8"))
        anchors = (scene.read(".", "anchor_right"), scene.read(".", "anchor_bottom"))
        return scene.read(".", "anchors_preset") == 15 or anchors == (1.0, 1.0)
    except (OSError, ValueError):
        return False


def _prop_scenes(root: Path, game: dict[str, Any]) -> tuple[list[dict[str, Any]],
                                                            list[dict[str, Any]]]:
    """The components (button, slider… roots), and the scenes others instance
    without covering the screen (a dropdown, a row)."""
    ui = [s for s in game["scenes"] if s["role"] == "interface" and not s["dev"]]
    components = [s for s in ui if survey.is_prop_type(s["root"])]
    reused = [s for s in ui if s["users"] and s not in components
              and s["root"] not in ("CanvasLayer", "Window")
              and not _fills_screen(root, s["path"])]
    return components, reused


def _has_buttons(root: Path, relative: str) -> bool:
    """The scene has a button (itself or one of its nodes): its states are shown."""
    try:
        text = (root / relative).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r'\[node [^\]]*type="(\w*Button|CheckBox)"', text))


def _work(project: str) -> Path:
    return project_paths(studio().settings, project).workspace / WORK_FOLDER / "props"


def _key(root: Path, entry: dict[str, Any]) -> str:
    """What makes a prop's image: its file, and the files it uses."""
    digest = hashlib.sha1(f"v{RENDER_VERSION}".encode())
    for relative in [entry["file"], *entry.get("refs", [])]:
        path = root / relative
        if path.is_file():
            stat = path.stat()
            digest.update(f"{relative}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:16]


def _rendered(project: str, element: str) -> dict[str, Any]:
    path = _work(project) / element / "render.json"
    if not path.is_file():
        return {}
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return found if isinstance(found, dict) else {}


def _drawn(project: str, root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """The element, with its drawn images and whether they are fresh."""
    done = _rendered(project, entry["id"])
    fresh = done.get("key") == _key(root, entry)
    states = done.get("states", []) if fresh else []
    return {**entry, "states": states, "stale": not fresh,
            "error": done.get("error", "") if fresh else "",
            "rendered_at": done.get("rendered_at", "") if fresh else ""}


def _props(project: str, root: Path, game: dict[str, Any]) -> list[dict[str, Any]]:
    components, reused = _prop_scenes(root, game)
    families = []
    for family, label, scenes in (("components", "Components", components),
                                  ("reused", "Reused scenes", reused)):
        items = []
        for scene in scenes:
            element = element_id(scene["path"])
            owner = lookdev._owner(game, scene["path"])
            items.append(_drawn(project, root, {
                "id": element, "type": "scene", "file": scene["path"],
                "res_path": owner["res_path"], "godot": owner["godot"]["path"],
                "title": Path(scene["path"]).stem, "root": scene["root"],
                "script": scene["script"], "users": scene["users"],
                "refs": [ref for ref in scene["refs"] if not ref.startswith("res://")],
                "wanted": list(STATES) if _has_buttons(root, scene["path"]) else ["normal"]}))
        if items:
            families.append({"id": family, "label": label, "folder": "", "items": items})
    boxes = []
    for box in game["styleboxes"]:
        element = element_id(box["path"])
        owner = lookdev._owner(game, box["path"])
        boxes.append(_drawn(project, root, {
            "id": element, "type": "stylebox", "file": box["path"],
            "res_path": owner["res_path"], "godot": owner["godot"]["path"],
            "title": Path(box["path"]).stem, "root": box["type"], "script": "",
            "users": box["users"], "refs": [], "wanted": ["normal"]}))
    if boxes:
        families.append({"id": "stylebox", "label": "StyleBox", "folder": "", "items": boxes})
    textures: dict[str, list[dict[str, Any]]] = {}
    for image in game["props"]:
        element = element_id(image["path"])
        width, height = _size(image["size"])
        textures.setdefault(image["folder"], []).append({
            "id": element, "type": "image", "file": image["path"],
            "title": Path(image["path"]).stem, "format": image["format"],
            "width": width, "height": height, "users": image["users"], "states": [],
            "stale": False})
    for folder, items in sorted(textures.items()):
        families.append({"id": element_id(folder), "label": f"Textures · {_family(folder)}",
                         "folder": folder, "items": items})
    return families


# ------------------------------------------------------------- the showcase


def showcase(project: str, kind: str) -> dict[str, Any]:
    """A showcase's elements, by family, and the game's theme.

    A prop whose image is missing or outdated carries `stale`: `render` draws it.
    """
    _kind(kind)
    root, game = _game(project)
    families = _icons(project, game) if kind == "icons" else _props(project, root, game)
    total = 0
    for family in families:
        for item in family["items"]:
            item.pop("refs", None)
            total += 1
    return {"project": project, "kind": kind, "families": families, "total": total,
            "stale": sum(1 for family in families
                         for item in family["items"] if item.get("stale")),
            "theme": lookdev.theme(project, game, [])}


def _find(project: str, kind: str, element: str) -> tuple[Path, dict[str, Any]]:
    root, game = _game(project)
    families = _icons(project, game) if kind == "icons" else _props(project, root, game)
    for family in families:
        for item in family["items"]:
            if item["id"] == element:
                return root, {**item, "family": family["id"], "family_label": family["label"],
                              "siblings": [other["id"] for other in family["items"]
                                           if other["id"] != element]}
    raise NotFound(f"element not found in the {kind} showcase: {element}")


def render(project: str, *, force: bool = False,
           only: list[str] | None = None) -> dict[str, Any]:
    """Draw the props whose image is missing or outdated (all with `force`). Free, local.

    One engine run for the whole batch. A prop that does not draw keeps its
    reason (`error`); the others do not suffer from it.
    """
    root, game = _game(project)
    wanted = [item for family in _props(project, root, game) for item in family["items"]
              if item["type"] in ("scene", "stylebox")
              and (force or item["stale"]) and (not only or item["id"] in only)]
    if not wanted:
        return {**showcase(project, "props"), "drawn": 0, "engine": ""}
    with _LOCK, tempfile.TemporaryDirectory(prefix="gamestudio-showcase-") as temp:
        jobs = []
        by_godot: dict[str, list[dict[str, Any]]] = {}
        for item in wanted:
            by_godot.setdefault(item["godot"], []).append(item)
        engine = ""
        for godot, items in by_godot.items():
            base = root / godot if godot else root
            entry = next(g for g in game["godot"] if g["path"] == godot)
            width = int(entry["display"].get("viewport_width") or 1152)
            height = int(entry["display"].get("viewport_height") or 648)
            batch = []
            for item in items:
                out = _work(project) / item["id"]
                out.mkdir(parents=True, exist_ok=True)
                for state in item["wanted"]:
                    job: dict[str, Any] = {"key": f"{item['id']}/{state}",
                                           "out": str(out / f"{state}.png")}
                    if item["type"] == "stylebox":
                        job.update({"stylebox": item["res_path"], "size": STYLEBOX_SIZE})
                    else:
                        job.update({"scene": item["res_path"], "state": state})
                    batch.append(job)
            listing = Path(temp) / f"batch-{len(jobs)}.json"
            listing.write_text(json.dumps(batch), encoding="utf-8")
            jobs.append(batch)
            engine, log, result = renders.run_engine(
                base, SCRIPT, [f"--gs-list={listing}", f"--gs-scale={SCALE}"], width, height,
                timeout=BATCH_TIMEOUT)
            _record(project, root, items, log, result)
    return {**showcase(project, "props"), "drawn": len(wanted), "engine": engine}


def _record(project: str, root: Path, items: list[dict[str, Any]], log: str,
            result: Any) -> None:
    """Keep, for each prop, its images and what tells its states apart."""
    answers: dict[str, dict[str, Any]] = {}
    for match in _LINE.finditer(log):
        element, state = match.group(1).split("/", 1)
        answers.setdefault(element, {})[state] = (
            {"width": int(match.group(2)), "height": int(match.group(3))}
            if match.group(4) is None else {"error": match.group(4)})
    failed = "" if result is not None and result.group(4) is None else (
        result.group(4) if result is not None else "the engine rendered nothing")
    for item in items:
        found = answers.get(item["id"], {})
        folder = _work(project) / item["id"]
        states = []
        errors = []
        normal = _pixels(folder / "normal.png")
        for state in item["wanted"]:
            answer = found.get(state)
            if answer is None or "error" in answer:
                errors.append(f"{state}: {answer['error'] if answer else failed or 'missing'}")
                continue
            same = state != "normal" and normal is not None \
                and _pixels(folder / f"{state}.png") == normal
            states.append({"state": state, "width": answer["width"],
                           "height": answer["height"], "same": same})
        record = {"key": _key(root, item), "states": states, "error": "; ".join(errors),
                  "rendered_at": datetime.now(UTC).isoformat(timespec="seconds")}
        (folder / "render.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                           encoding="utf-8")


def _pixels(path: Path) -> bytes | None:
    """An image's pixels, to tell whether two states are identical."""
    if not path.is_file():
        return None
    from PIL import Image

    with Image.open(path) as image:
        return image.convert("RGBA").tobytes() + f"{image.size}".encode()


def image_file(project: str, kind: str, element: str, state: str = "") -> Path:
    """An element's image: the game file, or a prop's drawing in one state."""
    _kind(kind)
    root, item = _find(project, kind, element)
    if item["type"] == "image":
        path = (root / item["file"]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise NotFound(f"file not found: {item['file']}")
        return path
    wanted = state or "normal"
    if wanted not in STATES:
        raise ServiceError(f"unknown state: {state} (expected: {', '.join(STATES)})")
    path = _work(project) / element / f"{wanted}.png"
    if not path.is_file() or not any(entry["state"] == wanted for entry in item["states"]):
        raise NotFound(f"{item['title']} is not drawn in the “{wanted}” state: redraw the showcase")
    return path


def element(project: str, kind: str, element_id: str) -> dict[str, Any]:
    """An element in detail: its family, its images (each state), its users."""
    _kind(kind)
    root, item = _find(project, kind, element_id)
    item.pop("refs", None)
    paths = []
    if item["type"] == "image":
        paths.append({"state": "", "path": str(root / item["file"])})
    else:
        paths += [{"state": entry["state"],
                   "path": str(_work(project) / element_id / f"{entry['state']}.png")}
                  for entry in item["states"]]
    return {**item, "kind": kind, "root_dir": str(root), "images": paths}


# -------------------------------------------------------------- removing from the game


def delete(project: str, kind: str, element_id: str) -> dict[str, Any]:
    """Remove an element from the game: its file goes to the project's trash.

    What used it is not changed: the answer names it (`users`). The batch is
    restored by `trash.restore`.
    """
    _kind(kind)
    _, item = _find(project, kind, element_id)
    batch = trash.discard(project, [item["file"]], item["title"])
    return {"batch": batch, "users": item["users"], "title": item["title"]}


def delete_family(project: str, kind: str, family_id: str) -> dict[str, Any]:
    """Remove a whole family from the game, in a single trash batch.

    Only the family's elements leave: a subfolder forming its own family stays.
    The folder, left empty, is removed too.
    """
    _kind(kind)
    root, game = _game(project)
    families = _icons(project, game) if kind == "icons" else _props(project, root, game)
    family = next((entry for entry in families if entry["id"] == family_id), None)
    if family is None:
        raise NotFound(f"family not found in the {kind} showcase: {family_id}")
    files = [item["file"] for item in family["items"]]
    users = sorted({user for item in family["items"] for user in item["users"]
                    if user not in files})
    batch = trash.discard(project, files, f"{LABELS[kind]} · {family['label']}")
    return {"batch": batch, "users": users, "count": len(files), "label": family["label"]}
