"""What the user can do with the filing: file, rename, delete.

The library describes where things go (`library.py`); this module says what
may be changed. The distinction matters: an asset's place depends on where it
comes from.

- A **sheet piece** (a split icon, an imported sheet) carries its place in its
  metadata: project and sheet. Moving, renaming or deleting it harms nobody.
- Everything else has its place decided by what produced it: it is neither
  moved nor renamed. It can be deleted as long as nothing holds it -- a
  character (its concept, mesh, sprites), a style (its reference images), a
  world card (its chosen concept), an effect (its latest render). Otherwise
  the operation is refused, naming what holds it.

Nothing is deleted silently: each refusal comes back with its reason, for the
interface to show.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.models import Asset
from ..sheet.layout import slug
from .assets import AssetStore
from .db import Database
from .library import EFFECT_ROLES, ICON_ROLES, SCAN_LIMIT, Librarian

# Roles whose place lies in their metadata, so they can be changed freely.
MOVABLE_ROLES = frozenset(ICON_ROLES)
# A world card's workbench, stored next to it: the same suffix as
# `service/documents.WORKBENCH` (a test keeps them equal). It holds the card's
# chosen concept (`concept`), which the workbench takes to 3D.
WORKBENCH = ".workbench.json"


@dataclass
class Outcome:
    """The outcome of an operation: what was done, what was refused."""

    done: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)   # asset id -> reason
    projects: set[str] = field(default_factory=set)         # projects to resync

    @property
    def ok(self) -> bool:
        return bool(self.done) and not self.refused

    def summary(self) -> str:
        parts = []
        if self.done:
            parts.append(f"{len(self.done)} element(s)")
        if self.refused:
            parts.append(f"{len(self.refused)} refused")
        return ", ".join(parts) or "nothing to do"


class Curator:
    """The editing operations on a project's filing.

    `documents` is the project's documents folder: the cards' workbenches are
    read there. Without it, a concept chosen by a card is not protected.
    """

    def __init__(self, db: Database, store: AssetStore, librarian: Librarian,
                 documents: Path | None = None) -> None:
        self.db = db
        self.store = store
        self.librarian = librarian
        self.documents = documents

    # --------------------------------------------------------------------- read

    def holders(self, asset_id: str) -> list[str]:
        """What holds this asset: characters, styles, cards, effects.

        An asset held by a production is not a free file: deleting it would
        leave a character without a mesh, a style without a reference, a card
        without a concept, an effect with an incomplete render.
        """
        held: list[str] = []
        for project in self.librarian.projects():
            for character in self.db.list_characters(project):
                if asset_id in self._character_assets(character):
                    held.append(f"character “{character.spec.id}” ({project})")
            for pack in self.db.list_style_packs(project):
                if asset_id in pack.reference_images:
                    held.append(f"style “{pack.name}” ({project})")
        held += [f"card “{card}” (chosen concept)" for card in self._cards(asset_id)]
        effect = self._effect(asset_id)
        if effect:
            held.append(effect)
        return held

    def _cards(self, asset_id: str) -> list[str]:
        """The world cards whose workbench holds this asset as their concept."""
        if self.documents is None or not self.documents.is_dir():
            return []
        found = []
        for sidecar in sorted(self.documents.rglob(f"*{WORKBENCH}")):
            try:
                state = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(state, dict) and state.get("concept") == asset_id:
                found.append(sidecar.relative_to(self.documents).as_posix()[:-len(WORKBENCH)])
        return found

    def _effect(self, asset_id: str) -> str:
        """The effect whose latest render includes this asset, if any.

        An effect render is a whole (sheet, frames, atlas, spec) the library
        shows together; only earlier renders are free.
        """
        asset = self.db.get_asset(asset_id)
        if asset is None or asset.meta.get("role") not in EFFECT_ROLES:
            return ""
        project, name = asset.meta.get("project"), asset.meta.get("effect")
        latest = max((str(other.meta.get("build", ""))
                      for other in self.db.list_assets(limit=SCAN_LIMIT)
                      if other.meta.get("role") in EFFECT_ROLES
                      and other.meta.get("project") == project
                      and other.meta.get("effect") == name), default="")
        build = str(asset.meta.get("build", ""))
        return f"effect “{name}” (render {build})" if build == latest else ""

    @staticmethod
    def _character_assets(character) -> set[str]:
        ids: set[str] = set()
        if character.concept_asset_id:
            ids.add(character.concept_asset_id)
        ids.update(character.turnaround_asset_ids)
        ids.update(character.spritesheets.values())
        rig = character.rig3d
        if rig is not None:
            ids.update(asset for asset in (rig.mesh_asset_id, rig.source_mesh_asset_id)
                       if asset)
        return ids

    def movable(self, asset: Asset) -> bool:
        return asset.meta.get("role") in MOVABLE_ROLES

    # -------------------------------------------------------------------- write

    def delete(self, asset_ids: Iterable[str]) -> Outcome:
        """Delete assets nothing holds: the row, the store file, the mirror."""
        outcome = Outcome()
        for asset_id in asset_ids:
            asset = self.db.get_asset(asset_id)
            if asset is None:
                outcome.refused[asset_id] = "not found in the database"
                continue
            held = self.holders(asset_id)
            if held:
                outcome.refused[asset_id] = "held by " + ", ".join(held)
                continue
            project = str(asset.meta.get("project", ""))
            self.db.delete_asset(asset_id)
            self.store.remove(asset_id)
            outcome.done.append(asset_id)
            if project:
                outcome.projects.add(project)
        self._resync(outcome)
        return outcome

    def move(self, asset_ids: Iterable[str], *, project: str, sheet: str) -> Outcome:
        """File pieces of `project` into another of its sheets.

        A piece does not change project: each project has its own store and
        mirror, and moving it there means importing it there (`add_files`).
        """
        outcome = Outcome()
        target_sheet = slug(sheet, max_length=48)
        for asset_id in asset_ids:
            asset = self.db.get_asset(asset_id)
            if asset is None:
                outcome.refused[asset_id] = "not found in the database"
                continue
            if not self.movable(asset):
                outcome.refused[asset_id] = (
                    "its place is decided by what produced it "
                    f"(role “{asset.meta.get('role', '?')}”)")
                continue
            if asset.meta.get("project") != project:
                outcome.refused[asset_id] = (
                    f"outside project {project}: a project only files its own elements")
                continue
            asset.meta["sheet"] = target_sheet
            self.db.save_asset(asset)
            outcome.done.append(asset_id)
            outcome.projects.add(project)
        self._resync(outcome)
        return outcome

    def rename(self, asset_id: str, name: str) -> Outcome:
        """Rename a piece. The file name in the library follows."""
        outcome = Outcome()
        asset = self.db.get_asset(asset_id)
        if asset is None:
            outcome.refused[asset_id] = "not found in the database"
            return outcome
        if not self.movable(asset):
            outcome.refused[asset_id] = (
                "its name is the one given by what produced it")
            return outcome
        cleaned = slug(name, max_length=48)
        if not cleaned:
            outcome.refused[asset_id] = "empty name"
            return outcome
        project = str(asset.meta.get("project", ""))
        sheet = str(asset.meta.get("sheet", ""))
        taken = {
            str(other.meta.get("name", "")) for other in self.db.list_assets(limit=1000)
            if other.id != asset_id
            and other.meta.get("project") == project
            and other.meta.get("sheet") == sheet
        }
        if cleaned in taken:
            outcome.refused[asset_id] = f"“{cleaned}” already exists in this sheet"
            return outcome

        asset.meta["name"] = cleaned
        self.db.save_asset(asset)
        outcome.done.append(asset_id)
        if project:
            outcome.projects.add(project)
        self._resync(outcome)
        return outcome

    def add_files(self, paths: Sequence[Path], *, project: str, sheet: str) -> Outcome:
        """Bring already split files in as pieces of a sheet.

        This is the "paste" gesture: a pasted file is one more element, not a
        sheet to split again -- the sheet import does that.
        """
        from ..sheet import RASTER_SUFFIXES, VECTOR_SUFFIXES

        outcome = Outcome()
        target = slug(sheet, max_length=48)
        for path in paths:
            suffix = path.suffix.lower()
            if suffix not in (RASTER_SUFFIXES | VECTOR_SUFFIXES):
                outcome.refused[path.name] = f"unsupported format ({suffix or 'no extension'})"
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                outcome.refused[path.name] = str(exc)
                continue
            asset = self._put_piece(data, suffix, project=project, sheet=target,
                                    name=path.stem, source=path.name)
            outcome.done.append(asset.id)
        outcome.projects.add(project)
        self._resync(outcome)
        return outcome

    # ----------------------------------------------------------------- internal

    def _put_piece(self, data: bytes, suffix: str, *, project: str, sheet: str,
                   name: str, source: str) -> Asset:
        cleaned = slug(name, max_length=48) or "element"
        existing = {
            str(a.meta.get("name", "")) for a in self.db.list_assets(limit=1000)
            if a.meta.get("project") == project and a.meta.get("sheet") == sheet
        }
        unique = cleaned
        suffix_index = 2
        while unique in existing:
            unique = f"{cleaned}-{suffix_index}"
            suffix_index += 1
        asset = self.store.put_bytes(data, suffix, meta={
            "role": "icon", "project": project, "sheet": sheet, "name": unique,
            "source": source, "index": len(existing),
        })
        self.db.save_asset(asset)
        return asset

    def _resync(self, outcome: Outcome) -> None:
        for project in sorted(p for p in outcome.projects if p):
            self.librarian.sync_project(project)
