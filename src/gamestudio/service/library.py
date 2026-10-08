"""Readable library: browsing, syncing, filing.

The library is the plain mirror of the hash-addressed store. It is browsed like
a folder -- each entry carries its absolute path, which really exists since the
project is resynced on the way -- and filed through curation, the only path
allowed to rename, move or delete.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..store.curation import Outcome
from .context import space, studio
from .errors import NotFound, ServiceError

# The deepest: a card's concepts, `2d/<entity>/concepts/<batch>/<image>`.
MAX_DEPTH = 6


def sync(project: str) -> dict[str, Any]:
    """Resync the readable library from the database and the store.

    The agent briefing and the project report are regenerated along with it:
    the same information, read by other audiences, and files that contradicted
    each other would be worse than no file at all.
    """
    manifest = space(project).librarian.sync_project(project)
    from . import briefing, workspace

    briefing.write_briefing(project)
    workspace.write_report(project)
    return manifest


def projects() -> list[dict[str, Any]]:
    """The projects in the library, with their size."""
    entries: list[dict[str, Any]] = []
    for st in studio().spaces():
        project = st.project
        index = st.librarian.index_project(project)
        entries.append({
            "project": project,
            "path": str(st.librarian.project_dir(project)),
            "files": sum(len(f.files) for f in index.folders),
            "folders": len(index.subfolders()),
        })
    return entries


def tree(project: str, folder: str = "", depth: int = 1, pattern: str = "",
         limit: int = 200) -> dict[str, Any]:
    """Browse a project's library like a folder, paths included.

    - `folder`: folder to read, relative to the project. Empty = the root.
    - `depth`: 1 for the direct content, more to go down (6 at most).
    - `pattern`: keep only the files whose path contains this text.
    """
    st = space(project)
    st.librarian.sync_project(project)
    index = st.librarian.index_project(project)
    base = st.librarian.project_dir(project)
    folder = folder.strip("/")
    depth = max(1, min(int(depth), MAX_DEPTH))
    needle = pattern.lower()

    prefix = f"{folder}/" if folder else ""
    reachable = [f for f in index.folders
                 if not folder or f.path == folder or f.path.startswith(prefix)]
    if folder and not reachable:
        raise NotFound(f"folder not found: {folder} (available: "
                       f"{', '.join(index.subfolders()) or 'none'})")

    def below(path: str) -> int:
        return len(path[len(prefix):].split("/")) if path != folder else 0

    files: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    for entry in reachable:
        if below(entry.path) >= depth:
            continue
        target = base / entry.path if entry.path else base
        for file in entry.files:
            path = target / file.name
            if needle and needle not in f"{entry.path}/{file.name}".lower():
                continue
            asset = st.db.get_asset(file.asset_id)
            files.append({
                "name": file.name,
                "folder": entry.path,
                "path": str(path),
                "asset_id": file.asset_id,
                "kind": asset.kind if asset else None,
                "mime": asset.mime if asset else None,
                "size_bytes": path.stat().st_size if path.exists() else None,
            })
        for name in entry.documents:
            documents.append({"name": name, "folder": entry.path,
                              "path": str(target / name)})

    folders = [
        {"path": path, "name": path.rsplit("/", 1)[-1],
         "path_on_disk": str(base / path),
         "files": sum(len(f.files) for f in index.folders
                      if f.path == path or f.path.startswith(f"{path}/"))}
        for path in index.subfolders(folder)
    ]
    result: dict[str, Any] = {
        "project": project,
        "root": str(base),
        "folder": folder,
        "folders": folders,
        "files": files[:limit],
        "documents": documents,
        "truncated": len(files) > limit,
    }
    if not folder:
        manifest_path = base / "manifest.json"
        result["manifest"] = (json.loads(manifest_path.read_text(encoding="utf-8"))
                              if manifest_path.exists() else None)
    return result


def locate(project: str, asset_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Where these assets live in the project's library, if they are there.

    Return `{asset_id: {name, folder, path, kind}}` for those a project folder
    claims. An asset missing from the answer is not an error: it is outside the
    library, and the caller must say so -- keeping quiet would suggest that
    everything produced is filed.

    No `sync_project` here: the index is built from the database, so the
    returned paths are those the next sync will write. A read does not write to
    disk.
    """
    st = space(project)
    wanted = set(asset_ids)
    if not wanted:
        return {}
    index = st.librarian.index_project(project)
    base = st.librarian.project_dir(project)
    found: dict[str, dict[str, Any]] = {}
    for entry in index.folders:
        for file in entry.files:
            if file.asset_id not in wanted:
                continue
            asset = st.db.get_asset(file.asset_id)
            found[file.asset_id] = {
                "name": file.name,
                "folder": entry.path,
                "path": str((base / entry.path if entry.path else base) / file.name),
                "kind": asset.kind if asset else None,
            }
    return found


def read_document(project: str, folder: str, name: str) -> dict[str, Any]:
    """A folder's descriptive JSON (`sheet.json`, `batch.json`).

    The path is rebuilt from the project root and checked: a `..` in `folder`
    must not allow reading outside the library.
    """
    base = space(project).librarian.project_dir(project).resolve()
    path = (base / folder.strip("/") / name).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise NotFound(f"document not found: {folder}/{name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- filing


def _outcome(outcome: Outcome) -> dict[str, Any]:
    """Turn curation's outcome, refusals included, into plain data.

    A refusal is not an error: the interface must be able to say which asset
    was kept and why (a character or a style pack holds it).
    """
    return {"ok": outcome.ok, "summary": outcome.summary(),
            "done": list(outcome.done), "refused": dict(outcome.refused)}


def _owner(asset_id: str):
    """The space of the project holding an asset: curation happens there."""
    held = studio().find_asset(asset_id)
    if held is None:
        raise NotFound(f"asset {asset_id} not found")
    return held[0]


def holders(asset_id: str) -> list[str]:
    """What holds an asset: a held asset can be neither deleted nor moved."""
    return _owner(asset_id).curator.holders(asset_id)


def rename(asset_id: str, name: str) -> dict[str, Any]:
    """Rename a sheet element in the library."""
    return _outcome(_owner(asset_id).curator.rename(asset_id, name))


def move(asset_ids: list[str], project: str, sheet: str) -> dict[str, Any]:
    """File elements into another sheet of the same project.

    A project only files what it holds: giving an element to another project
    means importing it there (`add_files`), not moving it.
    """
    if not asset_ids:
        raise ServiceError("no asset to move")
    st = space(project)
    foreign = [asset_id for asset_id in asset_ids if st.db.get_asset(asset_id) is None]
    if foreign:
        raise ServiceError(f"{len(foreign)} element(s) outside project {project}: a project only "
                           "files its own elements")
    return _outcome(st.curator.move(asset_ids, project=project, sheet=sheet))


def delete(asset_ids: list[str]) -> dict[str, Any]:
    """Delete sheet elements, store included. Irreversible."""
    if not asset_ids:
        raise ServiceError("no asset to delete")
    groups: dict[str, list[str]] = {}
    spaces = {}
    for asset_id in asset_ids:
        held = studio().find_asset(asset_id)
        owner = held[0].project if held else ""
        groups.setdefault(owner, []).append(asset_id)
        if held:
            spaces[owner] = held[0]
    outcome = Outcome()
    for owner, ids in groups.items():
        if not owner:
            outcome.refused.update({asset_id: "not found in the database" for asset_id in ids})
            continue
        part = spaces[owner].curator.delete(ids)
        outcome.done += part.done
        outcome.refused.update(part.refused)
    return _outcome(outcome)


def add_files(paths: list[str], project: str, sheet: str) -> dict[str, Any]:
    """Add files from disk to a sheet of the library."""
    sources = [Path(p).expanduser() for p in paths]
    missing = [str(p) for p in sources if not p.exists()]
    if missing:
        raise NotFound(f"files not found: {', '.join(missing)}")
    return _outcome(space(project).curator.add_files(sources, project=project, sheet=sheet))
