"""Project folders: a project is a folder on the machine.

Open a folder and it becomes the project, the way one opens a folder in an
editor or a project in Godot. Everything the studio produces or writes *for
this game* lives in its `.gamestudio/`; the folder itself is the root of the
Godot project where exports are written.

    my-game/
    ├── project.godot          exports: res://characters/<entity>/...
    └── .gamestudio/           hidden: Godot ignores dot folders
        ├── .gitignore         only texts are versioned
        ├── recipe.yaml
        ├── documents/         bible, world, mechanics, concepts, effects/*.yaml
        ├── context/           the project briefing, read by an agent opened here
        ├── gamestudio.sqlite3 characters, rigs, style packs, jobs, costs
        ├── assets/            the hash-addressed store
        ├── library/           its readable mirror, regenerated
        ├── workspace/         the report, the handoff briefs
        ├── exports/           the character bundles
        └── work/              intermediate files of computations

The engine stays in the studio and is never copied into a project: code,
tools, skills, prompts, the studio's `context/` notes, the Runware key, logs.
The studio's `data/` only keeps the registry of opened folders
(`folders.json`), the API token and the sessions.

A project no folder carries -- a trial, a test -- gets a folder inside the
studio (`data/projects/<project>/`), with the same layout: there is a single
model.

**Every project path resolution goes through `project_paths`**: it is the
only place that knows where everything is.

The registry is a JSON file under `data/`, reread on every call: the MCP
server, the API and the CLI are separate processes, and a folder opened in one
must exist at once for the others. For the same reason, each write rereads
and rewrites it under a file lock: two interleaved writes would lose one.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Settings

# The subfolder the studio occupies in a project folder. Hidden: Godot imports
# nothing from a dot folder, and file managers do not show it.
STUDIO_DIR = ".gamestudio"
RECIPE_NAME = "recipe.yaml"
REGISTRY_NAME = "folders.json"

# The library folder where a brief's images are filed, one section per
# subfolder (`briefing/design/interface/`). The renders service writes it in
# the metadata, the librarian reads it to file the mirror: the convention is
# named once, here.
BRIEFING_FOLDER = "briefing"

# What is versioned with the game: the texts one writes and rereads. The rest
# -- database, store, mirror, reports, bundles -- is heavy, binary or
# regenerated.
GITIGNORE = """\
# Written by gamestudio: only the project's texts are versioned.
/*
!/.gitignore
!/recipe.yaml
!/documents/
!/context/
"""


def slug(value: str) -> str:
    """Turn a folder name into a project name: lowercase, dashes."""
    folded = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return folded or "project"


def valid_name(project: str) -> bool:
    """Whether a path can be built on this name without leaving its folder.

    A hosted project lives under `data/projects/<project>/`: a separator or
    `..` would take it out of the studio, and create a database and a store
    there.
    """
    return bool(project) and project not in (".", "..") and not any(
        char in project for char in "/\\\0")


def check_name(project: str) -> None:
    """Clearly refuse a project name that cannot be a folder."""
    if not valid_name(project):
        # The service imports this module: its error is loaded at call time.
        from ..service.errors import ServiceError

        raise ServiceError(f"invalid project name: {project!r}")


@dataclass(frozen=True)
class ProjectPaths:
    """Where each part of a project lives."""

    project: str
    # The project folder: the one that was opened, otherwise the one the
    # studio gives it (`data/projects/<project>/`).
    root: Path
    # True when the folder was opened by the user.
    linked: bool

    @property
    def data(self) -> Path:
        """`.gamestudio/`: everything the studio keeps for this project."""
        return self.root / STUDIO_DIR

    @property
    def recipe(self) -> Path:
        return self.data / RECIPE_NAME

    @property
    def documents(self) -> Path:
        return self.data / "documents"

    @property
    def context(self) -> Path:
        return self.data / "context"

    @property
    def library(self) -> Path:
        return self.data / "library"

    @property
    def workspace(self) -> Path:
        return self.data / "workspace"

    @property
    def exports(self) -> Path:
        return self.data / "exports"

    @property
    def godot(self) -> Path:
        return self.root

    @property
    def effects(self) -> Path:
        # Effect specs are written, versioned texts, like the documents: they
        # live with them, never under `data/`.
        return self.documents / "effects"


class FolderRegistry:
    """Projects opened on a folder: project name -> absolute path."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_settings(cls, settings: Settings) -> FolderRegistry:
        return cls(settings.home / REGISTRY_NAME)

    # --------------------------------------------------------------------- read

    def _raw(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _read(self) -> dict[str, dict[str, Any]]:
        folders = self._raw().get("folders")
        if not isinstance(folders, dict):
            return {}
        # A name that cannot be a folder (a hand-written registry) is ignored:
        # serving it would leave the studio.
        return {name: entry for name, entry in folders.items() if valid_name(name)}

    def hidden(self) -> set[str]:
        """Projects hidden from display. Nothing is erased: they exist."""
        hidden = self._raw().get("hidden")
        return {str(name) for name in hidden} if isinstance(hidden, list) else set()

    def all(self) -> dict[str, Path]:
        return {name: Path(entry["root"]) for name, entry in self._read().items()
                if isinstance(entry, dict) and entry.get("root")}

    def entries(self) -> list[dict[str, Any]]:
        """Known folders, most recently opened first."""
        rows = []
        for name, entry in self._read().items():
            root = Path(entry.get("root", ""))
            rows.append({"project": name, "root": str(root),
                         "opened_at": entry.get("opened_at"),
                         "exists": root.is_dir()})
        return sorted(rows, key=lambda row: row["opened_at"] or "", reverse=True)

    def root(self, project: str) -> Path | None:
        return self.all().get(project)

    def project_at(self, folder: Path) -> str | None:
        """The project already opened on this folder, if any."""
        target = folder.resolve()
        for name, root in self.all().items():
            if root == target:
                return name
        return None

    # -------------------------------------------------------------------- write

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """One writer at a time, across all processes.

        The API and each MCP server reread then rewrite the registry: without a
        lock, two simultaneous openings would lose one. The lock is the
        kernel's (`flock`), released with the file, even by a killed process.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_name(f".{self.path.name}.lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _write(self, folders: dict[str, dict[str, Any]] | None = None,
               hidden: set[str] | None = None) -> None:
        # Atomic write: a concurrent reader (the other process) must never read
        # a truncated JSON, which would make it forget every folder.
        payload = {"version": 1,
                   "folders": self._read() if folders is None else folders,
                   "hidden": sorted(self.hidden() if hidden is None else hidden)}
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".folders-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temp, self.path)

    def hide(self, project: str) -> None:
        with self._locked():
            self._write(hidden=self.hidden() | {project})

    def show(self, project: str) -> None:
        with self._locked():
            self._write(hidden=self.hidden() - {project})

    def link(self, project: str, root: Path) -> None:
        check_name(project)
        with self._locked():
            folders = self._read()
            folders[project] = {"root": str(root.resolve()),
                                "opened_at": datetime.now(UTC).isoformat(timespec="seconds")}
            # Reopening a folder shows it again, if it was hidden.
            self._write(folders, self.hidden() - {project})

    def unlink(self, project: str) -> bool:
        with self._locked():
            folders = self._read()
            if folders.pop(project, None) is None:
                return False
            self._write(folders)
            return True


def studio_dir(root: Path) -> Path:
    return root / STUDIO_DIR


def recipe_in(root: Path) -> Path:
    return studio_dir(root) / RECIPE_NAME



def ensure_layout(paths: ProjectPaths) -> None:
    """Make sure a project's `.gamestudio/` exists, with its `.gitignore`."""
    paths.data.mkdir(parents=True, exist_ok=True)
    ignore = paths.data / ".gitignore"
    if not ignore.exists():
        ignore.write_text(GITIGNORE, encoding="utf-8")


def project_paths(settings: Settings, project: str) -> ProjectPaths:
    """Where each part of a project lives. The only place that knows.

    Every project name goes through here before becoming a path: this is
    where a name that would leave its folder is refused.
    """
    check_name(project)
    if settings.space == project:
        # A project's settings: its folder is the parent of `.gamestudio/`.
        root = settings.data_dir.parent
        registered = FolderRegistry.for_settings(settings).root(project)
        return ProjectPaths(project=project, root=root, linked=registered == root)
    root = FolderRegistry.for_settings(settings).root(project)
    if root is not None:
        return ProjectPaths(project=project, root=root, linked=True)
    return ProjectPaths(project=project, root=settings.home / "projects" / project,
                        linked=False)


def space_settings(settings: Settings, project: str) -> Settings:
    """A project's settings: the studio's, anchored on its `.gamestudio/`.

    The engine (key, Blender) is the studio's; only the data moves.
    """
    paths = project_paths(settings, project)
    return settings.model_copy(update={"data_dir": paths.data, "home_dir": settings.home,
                                       "space": project})
