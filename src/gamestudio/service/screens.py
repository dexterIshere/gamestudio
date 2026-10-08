"""The screen editor: a game screen, its Controls, their properties, written on its branch.

An interface card describes a screen, a props card a component (a button, an
arrow, a frame); its editor changes it without opening Godot, the way a page is
retouched in a design tool: point at an element on the render, change its text,
color, margin or icon, and the engine redraws the screen.

What is changed is the game itself, on **one git branch per card**
(`studio/screen-<card>`, `studio/prop-<card>`), which the user merges when the
result suits them. The branch lives in its own checkout (`git worktree`, under
`.gamestudio/workspace/screens|props/<card>/game/`): the user's working copy --
and the Godot they have open on it -- does not move. Each edit is a commit;
"undo" removes the last one.

The editor's render stays in its workspace (`preview.png`, and the survey of
the Controls `nodes.json`, see `godot/render_scene.gd`): it shows the branch,
not the game as it is -- that remains the card's "Current render".
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..godot.tscn_edit import Scene, SceneEditError
from ..store.folders import project_paths
from . import cards, documents, renders, survey
from .context import studio
from .errors import NotFound, ServiceError

# The sections whose cards can be edited: a screen, or a prop (button, arrow,
# frame) retouched on its own. Each has its branches, its folder, its commit
# subject -- a screen and a prop with the same name do not mix.
KINDS: dict[str, dict[str, Any]] = {
    "design/interface": {"branch": "studio/screen-", "dir": "screens", "commit": "Screen "},
    "design/props": {"branch": "studio/prop-", "dir": "props", "commit": "Prop ",
                     # A prop is judged alone: cropped to it, on a transparent background.
                     "render": {"crop": True, "transparent": True}},
}
# An editor commit is recognized by its subject: only those can be undone.
COMMIT_PREFIXES = tuple(kind["commit"] for kind in KINDS.values())
STATE_NAME = "screen.json"
PREVIEW_NAME = "preview.png"
NODES_NAME = "nodes.json"
CHECKOUT_NAME = "game"
GIT_TIMEOUT = 60

# --------------------------------------------------------- properties

# Control families, by Godot class.
BUTTONS = frozenset({"Button", "CheckBox", "CheckButton", "MenuButton", "OptionButton",
                     "ColorPickerButton", "LinkButton"})
TEXTS = BUTTONS | {"Label", "RichTextLabel", "LineEdit"}
BOXES = frozenset({"BoxContainer", "HBoxContainer", "VBoxContainer", "FlowContainer",
                   "HFlowContainer", "VFlowContainer"})
ALIGN_H = [[0, "Left"], [1, "Center"], [2, "Right"], [3, "Justified"]]
ALIGN_V = [[0, "Top"], [1, "Center"], [2, "Bottom"], [3, "Fill"]]
STRETCH = [[0, "Stretch"], [1, "Tile"], [2, "Keep"], [3, "Keep, centered"],
           [4, "Keep aspect"], [5, "Keep aspect, centered"], [6, "Cover"]]
EXPAND = [[0, "Image size"], [1, "Ignore size"], [2, "Fit width"],
          [3, "Width, keep aspect"], [4, "Fit height"],
          [5, "Height, keep aspect"]]


def _prop(key: str, label: str, kind: str, group: str,
          options: list[list[Any]] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"key": key, "label": label, "kind": kind, "group": group}
    if options is not None:
        entry["options"] = options
    return entry


def properties_for(kind: str) -> list[dict[str, Any]]:
    """What the editor offers to change on a Control of this class.

    A closed list: what a screen changes most often, in the form the `.tscn`
    writes. The rest is done in Godot, or by an agent.
    """
    found: list[dict[str, Any]] = []
    if kind in TEXTS:
        found.append(_prop("text", "Text", "text", "Content"))
    if kind in {"LineEdit", "TextEdit"}:
        found.append(_prop("placeholder_text", "Placeholder", "text", "Content"))
    if kind in BUTTONS:
        found.append(_prop("icon", "Icon", "texture", "Content"))
    if kind in {"TextureRect", "NinePatchRect"}:
        found.append(_prop("texture", "Image", "texture", "Content"))
    if kind == "TextureRect":
        found += [_prop("stretch_mode", "Stretch mode", "enum", "Content", STRETCH),
                  _prop("expand_mode", "Size", "enum", "Content", EXPAND)]
    if kind == "ColorRect":
        found.append(_prop("color", "Color", "color", "Content"))
    if kind == "RichTextLabel":
        found += [_prop("theme_override_font_sizes/normal_font_size", "Font size",
                        "int", "Text"),
                  _prop("theme_override_colors/default_color", "Font color",
                        "color", "Text")]
    elif kind in TEXTS:
        found += [_prop("theme_override_font_sizes/font_size", "Font size", "int",
                        "Text"),
                  _prop("theme_override_colors/font_color", "Font color", "color",
                        "Text")]
    if kind == "Label":
        found += [_prop("horizontal_alignment", "Alignment", "enum", "Text", ALIGN_H),
                  _prop("vertical_alignment", "Vertical alignment", "enum", "Text",
                        ALIGN_V),
                  _prop("uppercase", "Uppercase", "bool", "Text")]
    if kind in BUTTONS:
        found += [_prop("alignment", "Alignment", "enum", "Text", ALIGN_H[:3]),
                  _prop("flat", "Flat", "bool", "Appearance")]
    if kind in BOXES:
        found.append(_prop("theme_override_constants/separation", "Separation", "int",
                           "Layout"))
    if kind == "GridContainer":
        found += [_prop("columns", "Columns", "int", "Layout"),
                  _prop("theme_override_constants/h_separation", "Horizontal separation",
                        "int", "Layout"),
                  _prop("theme_override_constants/v_separation", "Vertical separation",
                        "int", "Layout")]
    if kind == "MarginContainer":
        found += [_prop(f"theme_override_constants/margin_{side}", label, "int",
                        "Layout")
                  for side, label in (("left", "Left margin"), ("top", "Top margin"),
                                      ("right", "Right margin"), ("bottom", "Bottom margin"))]
    found += [
        _prop("custom_minimum_size", "Minimum size", "vector2", "Layout"),
        _prop("offset_left", "Left offset", "float", "Layout"),
        _prop("offset_top", "Top offset", "float", "Layout"),
        _prop("offset_right", "Right offset", "float", "Layout"),
        _prop("offset_bottom", "Bottom offset", "float", "Layout"),
        _prop("visible", "Visible", "bool", "Appearance"),
        _prop("modulate", "Modulate", "color", "Appearance"),
        _prop("self_modulate", "Self modulate", "color", "Appearance"),
    ]
    return found


# --------------------------------------------------------------- the repository


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    try:
        done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                              text=True, timeout=GIT_TIMEOUT)
    except FileNotFoundError as exc:
        raise ServiceError("git not found on the PATH: the screen editor works on a "
                           "branch") from exc
    if check and done.returncode != 0:
        raise ServiceError(f"git {' '.join(args[:2])} failed: "
                           f"{(done.stderr or done.stdout).strip()}")
    return done.stdout.strip() if done.returncode == 0 else ""


def _repo(root: Path) -> Path:
    """The root of the git repository holding the game."""
    top = _git(root, "rev-parse", "--show-toplevel", check=False)
    if not top:
        raise ServiceError(f"the game folder is not a git repository ({root}): the screen editor "
                           "writes on one branch per screen")
    return Path(top)


def _kind(folder: str) -> dict[str, Any]:
    kind = KINDS.get(folder)
    if kind is None:
        raise ServiceError(f"“{folder}” cannot be edited on the render: only {', '.join(KINDS)} "
                           "cards can")
    return kind


def branch_name(folder: str, name: str) -> str:
    return f"{_kind(folder)['branch']}{documents.slug(name)}"


class _Place:
    """Where a screen's editing lives: its folder, its checkout of the game, its state."""

    def __init__(self, project: str, folder: str, name: str) -> None:
        self.kind = _kind(folder)
        paths = project_paths(studio().settings, project)
        if not paths.linked:
            raise ServiceError(f"project {project} has no game folder: no screen to edit")
        self.project = project
        self.name = name
        self.game = paths.root
        self.dir = paths.workspace / self.kind["dir"] / documents.slug(name)
        self.checkout = self.dir / CHECKOUT_NAME
        self.state_file = self.dir / STATE_NAME
        self.preview = self.dir / PREVIEW_NAME
        self.nodes = self.dir / NODES_NAME

    def load(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {}
        try:
            found = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return found if isinstance(found, dict) else {}

    def save(self, state: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")

    def opened(self) -> bool:
        return (self.checkout / ".git").exists()

    def root_in_checkout(self, repo: Path) -> Path:
        """The game folder, inside the branch's checkout."""
        return self.checkout / self.game.resolve().relative_to(repo.resolve())


def _commits(place: _Place, base: str) -> list[dict[str, str]]:
    """The editor's commits on the branch, newest first."""
    if not place.opened() or not base:
        return []
    log = _git(place.checkout, "log", "--format=%H%x09%cI%x09%s", f"{base}..HEAD",
               check=False)
    found = []
    for line in log.splitlines():
        sha, when, subject = [*line.split("\t", 2), "", ""][:3]
        found.append({"sha": sha, "date": when, "subject": subject,
                      "studio": subject.startswith(COMMIT_PREFIXES)})
    return found


def _interface_scenes(project: str, folder: str) -> list[dict[str, str]]:
    """The game's interface scenes, excluding development ones; for a prop, the
    components (button, slider, gauge roots) first."""
    game = survey.game_map(project_paths(studio().settings, project).root)
    found = [{"file": scene["path"], "root": scene["root"]} for scene in game["scenes"]
             if scene["role"] == "interface" and not scene["dev"]]
    if folder == "design/props":
        found.sort(key=lambda scene: not survey.is_prop_type(scene["root"]))
    return found


# ---------------------------------------------------------------- state


def _card(project: str, folder: str, name: str) -> tuple[str, str]:
    # The section first: a mechanics card is not editable, whether it exists or not.
    _kind(folder.strip("/"))
    folder, card = cards._card(project, folder, name)
    return folder, card["name"]


def state(project: str, folder: str, name: str) -> dict[str, Any]:
    """Where a card's screen editing stands: its branch, its render, its Controls."""
    folder, name = _card(project, folder, name)
    place = _Place(project, folder, name)
    saved = place.load()
    preview = None
    if place.preview.is_file():
        preview = {"path": str(place.preview),
                   "rendered_at": str(saved.get("rendered_at") or ""),
                   "width": saved.get("width"), "height": saved.get("height")}
    nodes: list[dict[str, Any]] = []
    if place.nodes.is_file():
        try:
            nodes = json.loads(place.nodes.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            nodes = []
    scene = str(saved.get("scene") or "")
    if not scene:
        render = cards._latest_render(project, folder, name)
        scene = str(render.meta.get("scene") or "") if render is not None else ""
    return {
        "project": project, "folder": folder, "name": name,
        "branch": branch_name(folder, name),
        "opened": place.opened(),
        "scene": scene,
        "scenes": _interface_scenes(project, folder),
        "base": str(saved.get("base") or ""),
        "commits": _commits(place, str(saved.get("base") or "")),
        "preview": preview,
        "nodes": [_editable(node, scene) for node in nodes],
        "checkout": str(place.checkout) if place.opened() else "",
    }


def _editable(node: dict[str, Any], scene: str) -> dict[str, Any]:
    """A surveyed Control, and what the editor can do with it."""
    file = str(node.get("file") or "")
    return {**node, "editable": file.endswith(".tscn"),
            # A node declared by another scene: changing it also changes the
            # other screens that use it.
            "shared": bool(file) and file != scene}


# ------------------------------------------------------------- operations


def open_screen(project: str, folder: str, name: str, scene: str = "") -> dict[str, Any]:
    """Open a card's screen for editing: its branch, its checkout, a first render.

    `scene`: the screen's scene (`res://…` or from the game root); by default,
    the one of the card's current render. The branch starts from the game's
    current commit; if already open, it is reused as is.
    """
    folder, name = _card(project, folder, name)
    place = _Place(project, folder, name)
    repo = _repo(place.game)
    saved = place.load()
    if not scene:
        scene = str(saved.get("scene") or "") or state(project, folder, name)["scene"]
    if not scene:
        raise ServiceError("no scene given: choose the screen to edit")
    target = renders.resolve_scene(project, scene)
    branch = branch_name(folder, name)
    if not place.opened():
        _git(repo, "worktree", "prune", check=False)
        place.dir.mkdir(parents=True, exist_ok=True)
        exists = bool(_git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}",
                           check=False))
        if exists:
            _git(repo, "worktree", "add", str(place.checkout), branch)
        else:
            _git(repo, "worktree", "add", "-b", branch, str(place.checkout), "HEAD")
        if not saved.get("base"):
            saved["base"] = _git(place.checkout, "rev-parse", "HEAD")
    saved.update({"scene": target["res_path"], "godot": target["godot"]["path"],
                  "folder": folder, "branch": branch})
    _warm_cache(place, repo, target)
    place.save(saved)
    _render(project, folder, name, place)
    return state(project, folder, name)


def _warm_cache(place: _Place, repo: Path, target: dict[str, Any]) -> None:
    """Godot's import cache, copied from the game: the checkout does not reimport everything."""
    main = target["base"] / ".godot"
    copy = place.root_in_checkout(repo) / (target["godot"]["path"] or ".") / ".godot"
    if main.is_dir() and not copy.exists():
        shutil.copytree(main, copy, symlinks=True, ignore_dangling_symlinks=True)


def _render_settings(project: str, folder: str, name: str, scene: str) -> dict[str, Any]:
    """The card render's settings when it shows the same scene: same setup."""
    render = cards._latest_render(project, folder, name)
    if render is None or render.meta.get("scene") != scene:
        return dict(_kind(folder).get("render", {}))
    meta = render.meta
    return {"scale": float(meta.get("scale") or 2.0),
            "width": int(meta.get("width") or 0), "height": int(meta.get("height") or 0),
            "delay": float(meta.get("delay", 0.5)), "crop": bool(meta.get("crop")),
            "locale": str(meta.get("locale") or ""),
            "setup": str(meta.get("setup_code") or "")}


def _render(project: str, folder: str, name: str, place: _Place) -> None:
    saved = place.load()
    repo = _repo(place.game)
    target = renders.resolve_scene(project, str(saved["scene"]), str(saved.get("godot") or ""),
                                   root=place.root_in_checkout(repo))
    nodes = place.dir / f".{NODES_NAME}"
    output = place.dir / f".{PREVIEW_NAME}"
    drawn = renders.render_png(target, output, nodes=nodes,
                               **_render_settings(project, folder, name, saved["scene"]))
    output.replace(place.preview)
    nodes.replace(place.nodes)
    saved.update({"rendered_at": datetime.now(UTC).isoformat(timespec="seconds"),
                  "width": drawn["width"], "height": drawn["height"]})
    place.save(saved)


def render(project: str, folder: str, name: str) -> dict[str, Any]:
    """Redraw the screen from its branch. Free, local."""
    folder, name = _card(project, folder, name)
    place = _require(project, folder, name)
    _render(project, folder, name, place)
    return state(project, folder, name)


def _require(project: str, folder: str, name: str) -> _Place:
    place = _Place(project, folder, name)
    if not place.opened() or not place.load().get("scene"):
        raise ServiceError("the screen is not open for editing: open it first (screen_open)")
    return place


def _node(place: _Place, path: str) -> dict[str, Any]:
    if not place.nodes.is_file():
        raise ServiceError("no element survey: redraw the screen")
    nodes = json.loads(place.nodes.read_text(encoding="utf-8"))
    for node in nodes:
        if node.get("path") == path:
            return node
    raise NotFound(f"element not found on the screen: {path}")


def _scene_file(place: _Place, node: dict[str, Any]) -> Path:
    """The `.tscn` declaring a node, in the branch's checkout."""
    file = str(node.get("file") or "")
    if not file.endswith(".tscn"):
        raise ServiceError(f"“{node.get('path')}” is created by code: no scene declares it, it is "
                           "changed in its script")
    saved = place.load()
    repo = _repo(place.game)
    base = place.root_in_checkout(repo) / (saved.get("godot") or ".")
    path = (base / file.removeprefix("res://")).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise NotFound(f"scene not found on the branch: {file}")
    return path


def node_properties(project: str, folder: str, name: str, path: str) -> dict[str, Any]:
    """A screen element and its editable properties, with their written value.

    A missing value (`None`) is Godot's or the theme's default.
    """
    folder, name = _card(project, folder, name)
    place = _require(project, folder, name)
    node = _node(place, path)
    saved = place.load()
    entry = _editable(node, str(saved.get("scene") or ""))
    props = properties_for(str(node.get("type") or ""))
    if entry["editable"]:
        scene = Scene(_scene_file(place, node).read_text(encoding="utf-8"))
        local = str(node.get("local") or ".")
        try:
            scene.node(local)
        except SceneEditError:
            entry["editable"] = False
        else:
            for prop in props:
                prop["value"] = scene.read(local, prop["key"])
    return {**entry, "properties": props, "icons": _icons(project) if any(
        prop["kind"] == "texture" for prop in props) else []}


def _icons(project: str) -> list[dict[str, str]]:
    """The game's icons, by their `res://` path: what can go on a button."""
    paths = project_paths(studio().settings, project)
    game = survey.game_map(paths.root)
    bases = sorted((entry["path"] for entry in game["godot"]), key=len, reverse=True)
    found = []
    for icon in game["icons"]:
        for base in bases:
            prefix = f"{base}/" if base else ""
            if icon["path"].startswith(prefix):
                found.append({"res": "res://" + icon["path"].removeprefix(prefix),
                              "file": icon["path"], "size": icon["size"]})
                break
    return found


def edit(project: str, folder: str, name: str, path: str,
         changes: dict[str, Any]) -> dict[str, Any]:
    """Write an element's properties into its scene, on the branch, and redraw.

    `changes`: `{key: value}` among the element's properties
    (`node_properties`); `None` restores the default. An edit is a commit on
    the screen's branch, undone by `undo`.
    """
    folder, name = _card(project, folder, name)
    place = _require(project, folder, name)
    node = _node(place, path)
    if not changes:
        raise ServiceError("no change")
    allowed = {prop["key"]: prop for prop in properties_for(str(node.get("type") or ""))}
    unknown = sorted(set(changes) - set(allowed))
    if unknown:
        raise ServiceError(f"property not editable here: {', '.join(unknown)}")
    file = _scene_file(place, node)
    original = file.read_text(encoding="utf-8")
    scene = Scene(original)
    local = str(node.get("local") or ".")
    try:
        for key, value in changes.items():
            scene.write(local, key, value, allowed[key]["kind"])
    except SceneEditError as exc:
        raise ServiceError(str(exc)) from exc
    if scene.text() == original:
        return {**state(project, folder, name), "changed": False}
    file.write_text(scene.text(), encoding="utf-8")
    labels = ", ".join(allowed[key]["label"].lower() for key in changes)
    message = f"{place.kind['commit']}{name} : {node.get('name') or path} — {labels}"
    relative = file.relative_to(place.checkout).as_posix()
    # A repository with no configured author: the studio signs its commits itself.
    signer = [] if _git(place.checkout, "config", "user.email", check=False) else \
        ["-c", "user.name=gamestudio", "-c", "user.email=gamestudio@localhost"]
    _git(place.checkout, "add", "--", relative)
    _git(place.checkout, *signer, "commit", "--quiet", "-m", message, "--", relative)
    try:
        _render(project, folder, name, place)
    except ServiceError as exc:
        # The written scene no longer draws: put it back as it was.
        _undo_last(place)
        raise ServiceError(f"change undone, the screen no longer drew: {exc}") \
            from exc
    return {**state(project, folder, name), "changed": True}


def _undo_last(place: _Place) -> None:
    _git(place.checkout, "reset", "--hard", "--quiet", "HEAD~1")


def undo(project: str, folder: str, name: str) -> dict[str, Any]:
    """Remove the screen's last edit (the studio's last commit on its branch)."""
    folder, name = _card(project, folder, name)
    place = _require(project, folder, name)
    commits = _commits(place, str(place.load().get("base") or ""))
    if not commits:
        raise ServiceError("nothing to undo: the branch has no change")
    if not commits[0]["studio"]:
        raise ServiceError(f"the last commit of {branch_name(folder, name)} is not a studio edit "
                           f"(“{commits[0]['subject']}”): nothing is undone")
    _undo_last(place)
    _render(project, folder, name, place)
    return state(project, folder, name)


def preview_file(project: str, folder: str, name: str) -> Path:
    """The image of the screen as its branch draws it."""
    folder, name = _card(project, folder, name)
    place = _Place(project, folder, name)
    if not place.preview.is_file():
        raise NotFound("no render of the screen: open it for editing")
    return place.preview
