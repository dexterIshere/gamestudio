"""Reading the studio's state: environment, projects, characters, assets."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from ..domain.recipe import load_recipe
from ..store.folders import project_paths
from .context import space, studio
from .errors import NotFound


def health() -> dict[str, Any]:
    """State of the environment: API key, Blender, local tools, queue."""
    from ..blender.run import blender_version
    from ..vision.detect import available as rigtools_available

    st = studio()
    settings = st.settings
    return {
        "project_root": str(settings.project_root) if settings.project_root else None,
        "data_dir": str(settings.data_dir),
        "runware_key": bool(settings.runware_api_key),
        "blender": blender_version(settings.blender_bin),
        "local_rigtools": rigtools_available(),
        "queue": queue_stats(),
    }


def queue_stats() -> dict[str, int]:
    """Counts per state, across the queues of every project."""
    totals: dict[str, int] = {}
    for found in studio().spaces():
        for state, count in found.queue.stats().items():
            totals[state] = totals.get(state, 0) + count
    return totals


def list_recipes(directory: str | None = None) -> list[dict[str, Any]]:
    """The projects' recipes: one per project folder, in its `.gamestudio/`.

    `directory` reads any folder of recipes instead. An unreadable recipe hides
    no other: it is returned with its error, so the interface can show it struck
    through rather than have it vanish without explanation. Each entry carries
    `root`, the project folder, and `linked`, true for a folder the user opened.
    """
    if directory:
        root = Path(directory).expanduser()
        return [_recipe_entry(path, path.stem, None)
                for path in (sorted(root.glob("*.yaml")) if root.exists() else [])]
    st = studio()
    # `hidden`: removed from display by the user, nothing deleted. The entry
    # stays listed -- an agent or a command still finds it.
    hidden = st.registry.hidden()
    entries: list[dict[str, Any]] = []
    for project in st.projects():
        paths = project_paths(st.settings, project)
        if not paths.recipe.is_file():
            continue
        entry = _recipe_entry(paths.recipe, project, paths.root)
        entry["linked"] = paths.linked
        entry["hidden"] = project in hidden
        entries.append(entry)
    return entries


def _recipe_entry(path: Path, fallback: str, folder: Path | None) -> dict[str, Any]:
    base: dict[str, Any] = {"path": str(path), "root": str(folder) if folder else None}
    try:
        recipe = load_recipe(path)
    except Exception as exc:
        return {**base, "project": fallback, "error": str(exc)}
    # A project is named after its folder: the recipe says the same unless it
    # was edited by hand -- and the folder is what files things.
    return {
        **base, "project": fallback if folder else recipe.project,
        "style": recipe.style.name, "lora": recipe.style.lora_air,
        "characters": [c.id for c in recipe.characters],
    }


def recipe_source(project: str) -> dict[str, Any]:
    """A recipe's text, as it is on disk.

    An invalid recipe can still be read: it is precisely when it no longer
    loads that one needs to see what it contains.
    """
    for entry in list_recipes():
        if entry["project"] == project:
            path = Path(entry["path"])
            return {"project": project, "path": str(path),
                    "text": path.read_text(encoding="utf-8"),
                    "error": entry.get("error")}
    raise NotFound(f"project {project} not found (no recipe in its folder)")


def find_recipe(project: str) -> dict[str, Any]:
    """A project's recipe, found by its name rather than its path.

    The interface handles project names; production operations want a file
    path. This is where the translation happens, once.
    """
    for entry in list_recipes():
        if entry["project"] == project and "error" not in entry:
            return entry
    raise NotFound(f"project {project} not found (no recipe in its folder)")


def list_characters(project: str) -> list[dict[str, Any]]:
    """A project's built (or in-progress) characters.

    `has_rig3d` says the entity has a mesh, rigged or not: the front reads that
    field as is.
    """
    return [
        {"id": c.spec.id, "name": c.spec.name, "state": c.state.value,
         "pipelines": [p.value for p in c.spec.pipelines],
         "concept_asset": c.concept_asset_id,
         "has_rig3d": c.rig3d is not None,
         "exports": c.exports, "errors": c.errors}
        for c in space(project).db.list_characters(project)
    ]


def character_detail(project: str, character_id: str) -> dict[str, Any]:
    """A character's whole state: concept, mesh and what it carries, sprites, exports."""
    character = space(project).db.get_character(project, character_id)
    if character is None:
        raise NotFound(f"character {character_id} not found in {project}")
    return character.model_dump(mode="json")


def list_assets(kind: str | None = None, limit: int = 60,
                project: str | None = None) -> list[dict[str, Any]]:
    """The produced files, newest first.

    `kind` filters: image | mesh | spritesheet | data | lora | scene.
    `project` sticks to one project; without it, every project, each entry
    naming its own.
    """
    limit = min(limit, 300)
    spaces = [space(project)] if project else studio().spaces()
    found = [(s.project, a) for s in spaces for a in s.db.list_assets(kind=kind, limit=limit)]
    found.sort(key=lambda pair: pair[1].created_at, reverse=True)
    return [
        {"id": a.id, "kind": a.kind, "mime": a.mime, "meta": a.meta,
         "project": owner, "created_at": a.created_at.isoformat()}
        for owner, a in found[:limit]
    ]


def asset_info(asset_id: str) -> dict[str, Any]:
    """An asset's metadata, project and path on disk."""
    held = studio().find_asset(asset_id)
    if held is None:
        raise NotFound(f"asset {asset_id} not found")
    owner, asset = held
    path = owner.store.path_for(asset_id)
    return {
        "id": asset_id,
        "project": owner.project,
        "kind": asset.kind,
        "mime": asset.mime,
        "meta": asset.meta,
        "path": str(path) if path else None,
        "size_bytes": path.stat().st_size if path else None,
    }


def asset_path(asset_id: str) -> Path:
    """An asset's path on disk, or an error if no project holds it."""
    held = studio().find_asset(asset_id)
    path = held[0].store.path_for(asset_id) if held else None
    if path is None:
        raise NotFound(f"asset {asset_id} not found")
    return path


def character_bundle(project: str, character_id: str) -> dict[str, Any]:
    """Pack everything a character produced into an archive.

    The mesh and its animations, the sprite sheets, the concept image, and the
    exported Godot folders (the GLB, the SpriteFrames) with their `res://` tree
    -- enough to drop the character into another project without the studio.
    The archive lives under `.gamestudio/exports/`, outside the library; it is
    rewritten on every call.
    """
    st = space(project)
    character = st.db.get_character(project, character_id)
    if character is None:
        raise NotFound(f"character {character_id} not found in {project}")

    entries: dict[str, Path] = {}

    def add(asset_id: str | None, name: str) -> None:
        path = st.store.path_for(asset_id) if asset_id else None
        if path is not None and path.exists():
            entries[f"{name}{path.suffix.lower()}"] = path

    add(character.concept_asset_id, "concept")
    rig = character.rig3d
    if rig is not None:
        add(rig.mesh_asset_id, f"3d/{character_id}")
    for key, asset_id in character.spritesheets.items():
        add(asset_id, f"sprites/{key}")

    # Godot exports: each `res://` path names a file of the studio's Godot
    # project; the whole folder containing it goes into the archive.
    godot_root = st.paths.godot
    for res_path in character.exports.values():
        if not res_path.startswith("res://"):
            continue
        folder = (godot_root / res_path.removeprefix("res://")).parent
        if not folder.is_dir() or folder == godot_root:
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and not path.name.endswith(".import"):
                entries[f"godot/{path.relative_to(godot_root).as_posix()}"] = path

    if not entries:
        raise NotFound(f"{character_id} has not produced anything to export yet")

    target = st.paths.exports / f"{character_id}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".zip.part")
    manifest = {"project": project, "character": character_id,
                "files": sorted(entries), "exports": character.exports}
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(entries.items()):
            archive.write(path, f"{character_id}/{name}")
        archive.writestr(f"{character_id}/manifest.json",
                         json.dumps(manifest, indent=2, ensure_ascii=False))
    partial.replace(target)
    return {"path": str(target), "files": sorted(entries),
            "size_bytes": target.stat().st_size}
