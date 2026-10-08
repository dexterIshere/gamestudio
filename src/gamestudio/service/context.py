"""Root of the studio's data, shared by every interface.

The studio is an engine: it has no game data of its own. Each project has its
**space** -- database, store, queue, library, curation -- in its
`<folder>/.gamestudio/` (see `store/folders.py`); the studio opens no database
of its own and keeps in `data/` only the folder registry, the token and the
sessions. Every job lives in its project's queue. No query therefore crosses
two projects: what is read from a project comes from its folder alone.

One set of objects for the CLI, the HTTP API and the MCP server: opening two
`Database` objects on the same file would work -- SQLite in WAL mode allows it
-- but would duplicate caches and connections for nothing. Spaces are built
once and kept; connections are already thread-local (`Database.connect`), so
this container is shared across threads.

The current studio can be substituted (`using`): that is what lets tests run the
service layer on a temporary folder, and lets a second studio open in the same
process without either overwriting the other's data.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..config import Settings
from ..config import settings as load_settings
from ..domain.models import Asset, Job
from ..jobs.queue import JobQueue
from ..store.assets import AssetStore
from ..store.curation import Curator
from ..store.db import Database
from ..store.folders import (
    STUDIO_DIR,
    FolderRegistry,
    ProjectPaths,
    ensure_layout,
    project_paths,
    space_settings,
    valid_name,
)
from ..store.library import Librarian


@dataclass(frozen=True)
class Space:
    """Everything the studio keeps for a project, in its `.gamestudio/`."""

    project: str
    paths: ProjectPaths
    settings: Settings
    db: Database
    store: AssetStore
    queue: JobQueue
    librarian: Librarian
    curator: Curator


def open_space(settings: Settings, project: str) -> Space:
    """Build a project's space on the studio's settings."""
    local = space_settings(settings, project)
    paths = project_paths(local, project)
    ensure_layout(paths)
    local.ensure_dirs()
    db = Database(local.db_path)
    store = AssetStore(local.assets_dir)
    # Filing a brief image under `briefing/<section>/` needs the section's cards:
    # the store does not read documents, the renders service does.
    from .renders import librarian as librarian_for

    librarian = librarian_for(db, store, local)
    return Space(project=project, paths=paths, settings=local, db=db, store=store,
                 queue=JobQueue(db), librarian=librarian,
                 curator=Curator(db, store, librarian, documents=paths.documents))


@dataclass(frozen=True)
class Studio:
    """The engine: its settings, and the projects' spaces."""

    settings: Settings
    _spaces: dict[tuple[str, Path], Space] = field(default_factory=dict, repr=False,
                                                   compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False,
                                  compare=False)

    @property
    def registry(self) -> FolderRegistry:
        return FolderRegistry.for_settings(self.settings)

    def space(self, project: str) -> Space:
        """A project's space, built on first request.

        The key includes the folder: a project reopened elsewhere gets another
        space, never the one of its former folder.
        """
        if not project:
            raise ValueError("a space is requested for a named project")
        root = project_paths(self.settings, project).data
        key = (project, root)
        with self._lock:
            found = self._spaces.get(key)
            if found is None:
                found = self._spaces[key] = open_space(self.settings, project)
            return found

    def projects(self) -> list[str]:
        """The projects: the open folders, then those the studio hosts."""
        names = set()
        for name, root in self.registry.all().items():
            if root.is_dir():
                names.add(name)
        hosted = self.settings.home / "projects"
        if hosted.is_dir():
            # A hand-made folder with an invalid name must not stop the worker:
            # `space()` would refuse it.
            names |= {path.name for path in hosted.iterdir()
                      if (path / STUDIO_DIR).is_dir() and valid_name(path.name)}
        return sorted(names)

    def spaces(self) -> list[Space]:
        return [self.space(project) for project in self.projects()]

    def find_asset(self, asset_id: str) -> tuple[Space, Asset] | None:
        """The project holding an asset. An asset exists in one project only."""
        for found in self.spaces():
            asset = found.db.get_asset(asset_id)
            if asset is not None:
                return found, asset
        return None

    def asset_file(self, asset_id: str) -> Path | None:
        """An asset's file, in the store of the project holding it."""
        held = self.find_asset(asset_id)
        return held[0].store.path_for(asset_id) if held else None

    def forget(self, project: str) -> None:
        """Forget a project's space, whatever folder held it.

        Otherwise a project deleted and recreated under the same name would get
        back the database and store of the deleted one. Nothing is deleted
        here: the next `space()` rebuilds the space from disk.
        """
        with self._lock:
            for key in [key for key in self._spaces if key[0] == project]:
                del self._spaces[key]

    def find_job(self, job_id: str) -> tuple[Space, Job] | None:
        """A job, and the project whose queue holds it."""
        for found in self.spaces():
            job = found.db.get_job(job_id)
            if job is not None:
                return found, job
        return None


def build(settings: Settings) -> Studio:
    """Build a studio on the given settings."""
    settings.ensure_dirs()
    return Studio(settings=settings)


@lru_cache(maxsize=1)
def _default() -> Studio:
    return build(load_settings())


_current: Studio | None = None


def studio() -> Studio:
    """The current studio: the substituted one, else the one from the environment."""
    return _current if _current is not None else _default()


def space(project: str) -> Space:
    """A project's space in the current studio."""
    return studio().space(project)


@contextmanager
def using(studio: Studio) -> Iterator[Studio]:
    """Substitute the current studio for the duration of a block."""
    global _current
    previous = _current
    _current = studio
    try:
        yield studio
    finally:
        _current = previous
