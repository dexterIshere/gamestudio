"""Open a folder of the machine as a studio project.

The gesture of any creative tool: pick a folder, it becomes the project. A
folder that already holds `.gamestudio/recipe.yaml` reopens as is (it may have
been moved, cloned, copied from another machine): everything the studio knows
about the project -- database, store, reports -- travels with it. A blank
folder gets a minimal recipe. The layout itself is described once, in
`store/folders.py`.

Closing a folder removes it from the studio without touching its content: what
is in the user's folder belongs to them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.models import Recipe, StylePack
from ..domain.recipe import load_recipe, write_recipe
from ..store.folders import (
    STUDIO_DIR,
    FolderRegistry,
    ensure_layout,
    project_paths,
    recipe_in,
    slug,
)
from .context import studio
from .errors import NotFound, ServiceError


def _registry() -> FolderRegistry:
    return FolderRegistry.for_settings(studio().settings)


def list_folders() -> list[dict[str, Any]]:
    """The open folders, most recent first, and whether they still exist."""
    return _registry().entries()


def open_folder(path: str, name: str = "") -> dict[str, Any]:
    """Open `path` as a project; return `{project, root, created}`.

    `name` only applies to a blank folder: it names the project to create (the
    folder's name otherwise). A folder that is already a project keeps the name
    in its recipe.
    """
    folder = Path(path).expanduser()
    if not folder.is_absolute():
        raise ServiceError(f"relative path refused: {path!r} — give an absolute path")
    if not folder.is_dir():
        raise NotFound(f"folder not found: {folder}")
    folder = folder.resolve()

    st = studio()
    home = st.settings.project_root
    if home is not None and (folder == home or home in folder.parents
                             or folder == st.settings.data_dir
                             or st.settings.data_dir in folder.parents):
        raise ServiceError("the studio folder cannot be a project: choose the game folder")

    registry = _registry()
    already = registry.project_at(folder)
    if already is not None:
        registry.link(already, folder)  # moves it to the top of the recent list
        return {"project": already, "root": str(folder), "created": False}

    recipe_path = recipe_in(folder)
    created = not recipe_path.is_file()
    if created:
        project = slug(name or folder.name)
    else:
        try:
            project = load_recipe(recipe_path).project
        except Exception as exc:
            raise ServiceError(f"unreadable recipe in {recipe_path}: {exc}") from exc

    # A name designates one project only. A project the studio already hosts
    # (`data/projects/<project>/`) has its home: linking a folder to it would
    # hide one behind the other.
    linked_elsewhere = registry.root(project)
    if linked_elsewhere is not None and linked_elsewhere.is_dir():
        raise ServiceError(f"project “{project}” is already open on {linked_elsewhere}: close it "
                           "first")
    hosted = project_paths(st.settings, project)
    if linked_elsewhere is None and (hosted.root / STUDIO_DIR).is_dir():
        raise ServiceError(f"a project “{project}” already exists in the studio: give another name")

    if created:
        write_recipe(Recipe(project=project,
                            style=StylePack(id=f"{project}-style", name=project)),
                     recipe_path)
    registry.link(project, folder)
    ensure_layout(project_paths(st.settings, project))
    return {"project": project, "root": str(folder), "created": created}


def close_folder(project: str) -> dict[str, Any]:
    """Remove a folder project from the studio. The folder is not touched.

    Everything the studio knew about the project is in its `.gamestudio/`:
    reopening the folder brings back the characters and their assets.
    """
    registry = _registry()
    root = registry.root(project)
    if root is None or not registry.unlink(project):
        raise NotFound(f"no folder open for project “{project}”")
    return {"project": project, "root": str(root)}


def forget(project: str) -> dict[str, Any]:
    """Remove a workspace from the studio's list. **No file is deleted.**

    A project open on a folder is closed (its folder stays intact, and reopening
    it brings it back); a project hosted by the studio is only hidden -- its
    recipe, documents and productions stay where they are.
    """
    from . import catalog

    registry = _registry()
    if registry.root(project) is not None:
        closed = close_folder(project)
        return {**closed, "closed": True, "hidden": False}
    if not any(entry["project"] == project for entry in catalog.list_recipes()):
        raise NotFound(f"unknown workspace: {project}")
    registry.hide(project)
    return {"project": project, "root": None, "closed": False, "hidden": True}


def unhide(project: str = "") -> list[str]:
    """Show a hidden workspace again, or all of them if `project` is empty."""
    registry = _registry()
    names = [project] if project else sorted(registry.hidden())
    for name in names:
        registry.show(name)
    return names
