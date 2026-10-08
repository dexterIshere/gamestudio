"""Global configuration, read from the environment (optional .env)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up the tree to the folder that holds a `.env`.

    Without it, `gamestudio` launched from a subfolder -- or from anywhere
    through a link in the PATH -- would find neither the API key nor the
    project registry, and would create a `data/` wherever it stands.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".env").is_file():
            return candidate
    return None


def _load_dotenv(path: Path) -> None:
    """Load a minimal .env without any external dependency."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class Settings(BaseModel):
    # Folder holding the .env: the anchor of every relative path.
    project_root: Path | None = None
    runware_api_key: str = ""
    # The key of the direct Tripo route. Optional: without it the whole studio
    # works, and only 3D generation through the Tripo API (P2 and its native
    # quads, the H family) is refused -- naming the variable.
    tripo_api_key: str = ""
    # The data these settings point to. For the studio, `data/`: the folder
    # registry, the API token, the sessions. For a project, its
    # `<folder>/.gamestudio/`: its database -- jobs included --, its store, its
    # library, its report (see `store/folders.py`).
    data_dir: Path = Path("./data")
    # The studio's `data/` when these settings are a project's: the folder
    # registry lives there, and every project must be able to read it.
    home_dir: Path | None = None
    # The project these settings are the space of, empty for the studio itself.
    space: str = ""
    # Folder of the agent notes (`context/`). None means "anchored on the
    # repository root", the normal case: agents launched in the studio read
    # this folder at startup. The environment variable exists for the tests,
    # which must not write into the repository of whoever runs them.
    context_dir: Path | None = None
    # Folder of the inbox (`inbox/`), at the root: the agent reads relative
    # paths there, since that is where its working directory puts it.
    inbox_dir: Path | None = None
    blender_bin: str = "blender"
    api_host: str = "127.0.0.1"
    api_port: int = 7788

    @property
    def home(self) -> Path:
        """The studio's `data/`, whether these settings are its own or a project's."""
        return self.home_dir or self.data_dir

    @property
    def db_path(self) -> Path:
        # A project has its database in its `.gamestudio/`: the only one the
        # studio opens. On the studio's own settings, this path only serves a
        # `Context` built by hand on a throwaway folder (tests, 3D smoke test).
        if self.space:
            return self.data_dir / "gamestudio.sqlite3"
        return self.data_dir / "studio.sqlite3"

    @property
    def assets_dir(self) -> Path:
        return self.data_dir / "assets"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def library_dir(self) -> Path:
        # Readable mirror of the store (see store/library.py).
        return self.data_dir / "library"

    def work_for(self, project: str, kind: str = "work") -> Path:
        """Intermediate files of a computation (Blender, splitting, renders):
        a cache, never a deliverable. `kind` is `work` or `out`."""
        if self.space:
            return self.data_dir / kind
        return self.projects_dir / project / kind

    @property
    def run_dir(self) -> Path:
        # What does not outlive the machine: the local API token (see
        # api/auth.py). It is not an asset, so it does not live in the store.
        return self.home / "run"

    @property
    def context_path(self) -> Path:
        # The agent briefing. It lives in the repository, not under `data/`:
        # it is a text agents read, not a produced asset. The default follows
        # the studio root, where `AGENTS.md` and `CLAUDE.md` cite it.
        if self.context_dir is not None:
            return self.context_dir
        return (self.project_root or Path.cwd()) / "context"

    @property
    def inbox_path(self) -> Path:
        # What is dropped for an agent (screenshots, dragged files). At the
        # root for the same reason as `context/`: a relative path there makes
        # sense to the agent, which works in that directory.
        if self.inbox_dir is not None:
            return self.inbox_dir
        return (self.project_root or Path.cwd()) / "inbox"

    def ensure_dirs(self) -> None:
        """Create the folders these settings point to: a project's store, or
        what the studio keeps -- the projects it hosts and the API token.

        The studio keeps no asset: each lives in its project's space
        (`store/folders.py`), with its library and its report.
        """
        if self.space:
            directories = (self.data_dir, self.assets_dir)
        else:
            directories = (self.data_dir, self.projects_dir, self.run_dir)
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def settings() -> Settings:
    # `GAMESTUDIO_HOME` points to the studio installation when it is called
    # from elsewhere: the MCP server started from another repository (a game),
    # where the current directory says nothing about the studio. One variable
    # is then enough: the `.env` at that root provides the rest, API key
    # included.
    home = os.environ.get("GAMESTUDIO_HOME", "").strip()
    root = Path(home).expanduser().resolve() if home else find_project_root()
    _load_dotenv((root or Path.cwd()) / ".env")

    # A relative path resolves against the studio root, not the current
    # directory: otherwise `gamestudio` launched from a subfolder would look
    # for the project registry elsewhere, and find none.
    raw_data_dir = Path(os.environ.get("GAMESTUDIO_DATA_DIR", "./data"))
    if not raw_data_dir.is_absolute():
        raw_data_dir = (root or Path.cwd()) / raw_data_dir

    raw_context_dir = os.environ.get("GAMESTUDIO_CONTEXT_DIR", "").strip()
    raw_inbox_dir = os.environ.get("GAMESTUDIO_INBOX_DIR", "").strip()
    return Settings(
        project_root=root,
        runware_api_key=os.environ.get("RUNWARE_API_KEY", ""),
        tripo_api_key=os.environ.get("TRIPO_API_KEY", ""),
        data_dir=raw_data_dir.resolve(),
        context_dir=(Path(raw_context_dir).expanduser().resolve()
                     if raw_context_dir else None),
        inbox_dir=(Path(raw_inbox_dir).expanduser().resolve()
                   if raw_inbox_dir else None),
        blender_bin=os.environ.get("BLENDER_BIN", "blender"),
        api_host=os.environ.get("GAMESTUDIO_API_HOST", "127.0.0.1"),
        api_port=int(os.environ.get("GAMESTUDIO_API_PORT", "7788")),
    )
