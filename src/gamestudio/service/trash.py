"""A project's trash: what the studio removes from the game, kept so it can be put back.

Deleting an icon, a prop or a whole family from the showcase removes its files
from the game folder: what is in the game is kept, what is no longer wanted
leaves it. Nothing is gone for good, though: each deletion is a **batch** moved
under `.gamestudio/trash/<batch>/`, at its original path, with its Godot
companions (`<file>.import`). A batch is restored as is as long as nothing has
taken its place; emptying the trash is the user's gesture, outside the studio.

An icon redone by the forge also leaves its old version here: replacing erases
nothing.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..store.folders import project_paths
from . import documents
from .context import studio
from .errors import NotFound, ServiceError

FOLDER = "trash"
MANIFEST = "batch.json"
# What Godot keeps next to an imported resource: it leaves and comes back with it.
COMPANIONS = (".import",)


def _root(project: str) -> Path:
    paths = project_paths(studio().settings, project)
    if not paths.linked:
        raise ServiceError(f"project {project} has no game folder")
    return paths.root


def _bin(project: str) -> Path:
    return project_paths(studio().settings, project).data / FOLDER


def _inside(root: Path, relative: str) -> Path:
    """A game file, by its path from the root: never outside, nor in the studio's
    `.gamestudio/`."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or ".gamestudio" in path.parts:
        raise ServiceError(f"path outside the game: {relative}")
    return path


def discard(project: str, files: list[str], label: str) -> dict[str, Any]:
    """Move game files into a trash batch, and return the batch.

    `files`: paths from the game root. Their Godot companions follow them. A
    folder left empty by the removal is removed too.
    """
    root = _root(project)
    moved: list[str] = []
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    batch = f"{stamp}-{documents.slug(label)[:40] or 'batch'}"
    target = _bin(project) / batch
    folders: set[Path] = set()
    for relative in files:
        source = _inside(root, relative)
        if not source.is_file():
            raise NotFound(f"file not found: {relative}")
        for path in [source, *(source.with_name(source.name + suffix)
                               for suffix in COMPANIONS)]:
            if not path.is_file():
                continue
            rel = path.relative_to(root.resolve()).as_posix()
            destination = target / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), destination)
            moved.append(rel)
        folders.add(source.parent)
    for folder in sorted(folders, key=lambda path: -len(path.parts)):
        if folder != root.resolve() and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    manifest = {"id": batch, "label": label, "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "files": moved}
    (target / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return manifest


def batches(project: str) -> list[dict[str, Any]]:
    """The trash batches, newest first."""
    folder = _bin(project)
    found = []
    if folder.is_dir():
        for path in sorted(folder.iterdir(), reverse=True):
            manifest = path / MANIFEST
            if not manifest.is_file():
                continue
            try:
                found.append(json.loads(manifest.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    return found


def restore(project: str, batch: str) -> dict[str, Any]:
    """Put a batch back in the game, at its original paths. Refused if one is taken."""
    root = _root(project)
    source = _bin(project) / batch
    manifest_path = source / MANIFEST
    if not batch or "/" in batch or not manifest_path.is_file():
        raise NotFound(f"batch not found in the trash: {batch}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    taken = [rel for rel in manifest["files"] if _inside(root, rel).exists()]
    if taken:
        raise ServiceError(f"these paths have been taken since: {', '.join(taken)}"
                           " — nothing is restored")
    for rel in manifest["files"]:
        destination = _inside(root, rel)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source / rel), destination)
    shutil.rmtree(source)
    return {"restored": manifest["files"], "id": batch, "label": manifest.get("label", "")}
