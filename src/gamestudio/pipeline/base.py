"""Foundation of the pipeline graph: shared context, steps, fingerprints.

Each step declares a deterministic fingerprint computed from its inputs. Two
runs with the same inputs do not recompute -- which matters when a step costs
$0.40 (a 3D mesh) or ten minutes (a LoRA training). This is what makes
re-running a recipe incremental: changing a character's description only
regenerates that character.
"""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from ..config import settings as load_settings
from ..domain.models import Asset
from ..runware import RunwareClient
from ..store.assets import AssetStore
from ..store.db import Database
from ..tripo import TripoClient

logger = logging.getLogger("gamestudio.pipeline")


@dataclass
class StepResult:
    """Output of a step: serializable data and the assets it produced."""

    data: dict[str, Any] = field(default_factory=dict)
    assets: list[Asset] = field(default_factory=list)
    cost_usd: float = 0.0
    cached: bool = False
    # True when the output deserves human review before going on (a reference
    # left opaque because matting failed): it stays out of the cache, so that a
    # new attempt redoes it.
    needs_review: bool = False
    notes: list[str] = field(default_factory=list)

    def asset_ids(self) -> list[str]:
        return [asset.id for asset in self.assets]


class Context:
    """Resources shared by every step of a run."""

    def __init__(
        self,
        *,
        project: str,
        settings: Settings | None = None,
        db: Database | None = None,
        store: AssetStore | None = None,
    ) -> None:
        self.project = project
        # Without explicit settings, those of the project's space: its database,
        # store and library, in its `.gamestudio/`.
        if settings is None:
            from ..store.folders import ensure_layout, project_paths, space_settings

            settings = space_settings(load_settings(), project)
            ensure_layout(project_paths(settings, project))
        self.settings = settings
        self.settings.ensure_dirs()
        self.db = db or Database(self.settings.db_path)
        self.store = store or AssetStore(self.settings.assets_dir)
        self._runware: RunwareClient | None = None
        self._tripo: TripoClient | None = None

    @property
    def runware(self) -> RunwareClient:
        if self._runware is None:
            self._runware = RunwareClient(self.settings.runware_api_key)
        return self._runware

    @property
    def tripo(self) -> TripoClient:
        """The direct Tripo API, set up only when a step calls it.

        A context that only does 2D must not require a Tripo key: the client is
        built on first access, and refuses then -- naming the variable to set --
        if the key is missing.
        """
        if self._tripo is None:
            self._tripo = TripoClient(self.settings.tripo_api_key)
        return self._tripo

    @property
    def work_dir(self) -> Path:
        path = self.settings.work_for(self.project)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def out_dir(self) -> Path:
        path = self.settings.work_for(self.project, "out")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def close(self) -> None:
        if self._runware is not None:
            self._runware.close()
            self._runware = None
        if self._tripo is not None:
            self._tripo.close()
            self._tripo = None

    def __enter__(self) -> Context:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def fingerprint(step_name: str, payload: dict[str, Any]) -> str:
    """Stable fingerprint of a step and its inputs.

    `sort_keys` and `default=str` make the hash independent of key order and
    tolerant of non-serializable types (Path, Enum, datetime).
    """
    blob = json.dumps({"step": step_name, "input": payload}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]


class Step(ABC):
    """A pipeline step."""

    name: str = "step"
    # A non-cacheable step runs every time (random exploration).
    cacheable: bool = True

    @abstractmethod
    def inputs(self) -> dict[str, Any]:
        """Everything that influences the result, for the fingerprint."""

    @abstractmethod
    def run(self, ctx: Context) -> StepResult:
        """Run the step."""

    def execute(self, ctx: Context, *, force: bool = False) -> StepResult:
        """Run through the fingerprint cache."""
        key = fingerprint(self.name, self.inputs())
        if self.cacheable and not force:
            cached = ctx.db.cached_step(key)
            if cached is not None:
                logger.info("%s: reusing the cache (%s)", self.name, key[:8])
                assets = [a for a in (ctx.db.get_asset(i) for i in cached.get("assets", []))
                          if a is not None]
                return StepResult(data=cached.get("data", {}), assets=assets,
                                  cost_usd=0.0, cached=True,
                                  notes=cached.get("notes", []))

        logger.info("%s: running (%s)", self.name, key[:8])
        result = self.run(ctx)

        for asset in result.assets:
            ctx.db.save_asset(asset)
        if self.cacheable and not result.needs_review:
            ctx.db.cache_step(key, self.name, {
                "data": result.data,
                "assets": result.asset_ids(),
                "notes": result.notes,
            })
        return result
