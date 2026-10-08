"""Readable mirror of the store: `<folder>/.gamestudio/library/`.

The store addresses files by hash (`assets/8f/fa/8ffa...png`), which is ideal
for caching and deduplication, and unreadable for a human -- or for an agent
that wants to inspect what the studio produced. The library is an organised
mirror, made of hard links to the store: zero copy, always current, and safe
to delete.

The layout is described once, by `index_project`: a list of folders, each with
its files (readable name -> asset) and its JSON documents. The disk is its
written form (`sync_project`), the desktop application its read form. The two
cannot diverge -- and above all, *nothing is mixed between projects*: a project
is a folder, 2D and 3D are separate worlds in it, and each generation batch or
icon sheet has its own folder.

Resynced by the worker after each production, on demand through
`gamestudio library sync`, and exposed to the MCP server.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..domain.models import Asset, Character
from .assets import AssetStore
from .db import Database
from .folders import BRIEFING_FOLDER

LIBRARY_README = """\
# gamestudio library

Readable mirror of the content-addressed store (`.gamestudio/assets/`). Each
file is a hard link to the store: deleting it here destroys nothing, and it is
recreated on the next sync (`gamestudio library sync <project>`).

Layout -- 2D and 3D never mixed:

    library/
      manifest.json               roster state, costs, errors
      generations/                free generations, one folder per batch
        <date>_<prompt>_<batch>/
          <id>.png                the batch's images
          batch.json              prompt, model, date
      2d/<entity>/
        manifest.json             state, exports, review notes
        concept.png               reference image in an imposed pose
        concepts/<date>_<batch>/  concepts drawn from the world card
      3d/<entity>/
        manifest.json
        concept.png
        <entity>.glb              the mesh: bare, or rigged and animated by the agent
        <entity>-bare.glb         the original bare mesh, once the entity is rigged
        sprites/<clip>_<dir>.png  sheets rendered from the 3D, one per animation
      icons/<sheet>/              a split sheet, one file per element
        <name>.svg                vector icon, viewBox fitted to the content
        <name>.png                bitmap icon or frame, split from the sheet
        sheet.json                source, strategy, frames and dimensions
      effects/<effect>/           the latest render of a visual effect (see service/effects.py)
        <effect>.png              the frame sheet
        <effect>.json             atlas: frames, rate, loop, pivot, blending
        contact.png               every frame on a dark background
        spec.yaml                 the spec that rendered it
        frames/<index>.png        each frame on its own
      renders/                    game scenes rendered by its engine
        <name>.png                the latest render under this name: a card cites it
        renders.json              scene, scale and date of each render
      briefing/<section>/         a brief's images, filed with their cards
        <name>.png                one render per card, under the card's name
        renders.json              what each image shows, and when
      video/<batch>/              frames taken from a reference video
        frames/<index>.jpg        a frame, timestamped in frames.json
        frames.json               source, rate, range, timestamp of each frame
        contact.png               the whole batch's grid, in one image
      style/                      art direction reference images

Do not edit these files in place: they are regenerated. To rename, file or
delete an element, use the studio's Library page or the MCP tools
(`library_rename`, `library_move`).
"""

# Asset roles treated as free generations, filed by batch.
GENERATION_ROLES = ("generation", "style_exploration")

# Asset roles filed by source sheet (see sheet/importer.py): a split element, a
# whole sheet, and the former role of vector icons.
ICON_ROLES = ("icon", "sheet", "vector")

# Asset roles filed by source frame batch (see video/importer.py): a frame,
# the batch's contact sheet, and its manifest.
VIDEO_FRAME_ROLE = "video_frame"
VIDEO_CONTACT_ROLE = "video_contact"
VIDEO_MANIFEST_ROLE = "video_manifest"

# Asset roles of a rendered effect (see service/effects.py).
EFFECT_ROLES = ("effect_sheet", "effect_frame", "effect_contact", "effect_atlas", "effect_spec")

# Role of a game scene rendered by its engine (see service/renders.py).
RENDER_ROLE = "render"
# The folder of renders that illustrate no card. Brief images live under
# `BRIEFING_FOLDER/<section>/`: see `render_folder`.
FOLDER = "renders"

# Number of assets scanned to rebuild the library folders: a frame batch weighs
# a few hundred, and several batches add up.
SCAN_LIMIT = 2000


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _slug(value: str, *, max_length: int = 28) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:max_length].strip("-") or "untitled"


def _icon_size(asset: Asset) -> dict[str, float]:
    """Dimensions of the file itself, for whoever places it in an engine.

    `bbox` says where the element was taken in its sheet; that is not the size
    of the produced file, which a bitmap carries measured (`pixels`). An SVG
    has no pixels: its dimensions are those of its viewBox, hence of its frame
    -- which is what the fallback returns.
    """
    pixels = asset.meta.get("pixels")
    if isinstance(pixels, (list, tuple)) and len(pixels) == 2:
        return {"width": pixels[0], "height": pixels[1]}
    bbox = asset.meta.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        return {"width": bbox[2], "height": bbox[3]}
    return {}


def render_folder(asset: Asset, briefings: dict[str, list[str]]) -> str:
    """A render's library folder, under `renders/` or under `briefing/`.

    `briefings`: the sections that have cards, and the name of each
    (`service/renders.py:briefings`). A brief render carries its section's tag
    in its metadata; an untagged render is attached to the section where a
    card bears its name -- the image produced for `folder="design/interface"`
    is named after its card. A render nothing claims stays a free image, in
    `renders/`.
    """
    tag = str(asset.meta.get("briefing") or "")
    section = tag[len(BRIEFING_FOLDER) + 1:] if tag.startswith(f"{BRIEFING_FOLDER}/") else ""
    if section not in briefings:
        stem = Path(str(asset.meta.get("name") or asset.id[:12])).stem.lower()
        matching = [name for name, files in briefings.items() if stem in files]
        section = matching[0] if len(matching) == 1 else ""
    return f"{BRIEFING_FOLDER}/{section}" if section else FOLDER


def _hardlink(source: Path, target: Path) -> None:
    """Hard link when possible (same file system), copy otherwise."""
    if target.exists():
        if target.stat().st_ino == source.stat().st_ino:
            return
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.hardlink_to(source)
    except OSError:
        shutil.copy2(source, target)


# ------------------------------------------------------------------ description


@dataclass(frozen=True)
class LibraryFile:
    """A library file: its readable name and the asset behind it."""

    name: str
    asset_id: str


@dataclass
class LibraryFolder:
    """A project folder: its files and its JSON documents."""

    path: str  # relative to the project, POSIX; "" is the project root
    files: list[LibraryFile] = field(default_factory=list)
    documents: dict[str, Any] = field(default_factory=dict)  # name -> JSON content

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1] if self.path else ""

    @property
    def parent(self) -> str:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""

    def is_empty(self) -> bool:
        return not self.files and not self.documents


@dataclass
class ProjectIndex:
    """A project's complete layout, before anything is written to disk."""

    project: str
    folders: list[LibraryFolder] = field(default_factory=list)
    characters: list[dict[str, Any]] = field(default_factory=list)

    def folder(self, path: str) -> LibraryFolder | None:
        return next((f for f in self.folders if f.path == path), None)

    def subfolders(self, path: str = "") -> list[str]:
        """Paths of the direct subfolders of `path`, deduplicated and sorted.

        An intermediate folder (`2d`, `icons`) has no entry of its own: it only
        exists through its children, so it is inferred from their paths.
        """
        prefix = f"{path}/" if path else ""
        depth = len(prefix.split("/")) - 1 if prefix else 0
        names: set[str] = set()
        for folder in self.folders:
            if not folder.path.startswith(prefix) or folder.path == path:
                continue
            parts = folder.path.split("/")
            if len(parts) > depth:
                names.add("/".join(parts[:depth + 1]))
        return sorted(names)

    def asset_ids(self) -> set[str]:
        return {file.asset_id for folder in self.folders for file in folder.files}

    def batches(self, section: str) -> list[str]:
        """Names of the folders filed directly under `section` (generations, icons)."""
        return [path.split("/")[-1] for path in self.subfolders(section)]


def _prune(base: Path, written: set[Path]) -> None:
    """Erase from the mirror what the index no longer describes.

    Otherwise an element deleted, renamed or filed elsewhere would leave its
    old file behind, and the folder would stop reflecting exactly what the
    studio holds. The store keeps the content as long as an asset references
    it.
    """
    if not base.exists():
        return
    for path in sorted(base.rglob("*"), reverse=True):
        if path.is_file() and path not in written:
            path.unlink(missing_ok=True)
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


class Librarian:
    """Describe a project's layout, and write it to disk."""

    def __init__(self, db: Database, store: AssetStore, root: Path,
                 space: str = "") -> None:
        self.db = db
        self.store = store
        self.root = root
        # A project's space (`.gamestudio/`) holds a single project: its mirror
        # is `root` itself. Without a space -- a database shared by several
        # projects, which only tests build --, one folder per project under
        # `root`.
        self.space = space
        # The sections that have cards, and the name of each: this is what
        # files a brief image under `briefing/<section>/` rather than with the
        # free renders. The renders service can read them; the store cannot --
        # it only knows assets and paths.
        self.sections: Callable[[str], dict[str, list[str]]] | None = None

    def briefings(self, project: str) -> dict[str, list[str]]:
        """The project's sections, or nothing if no one can read them."""
        return self.sections(project) if self.sections is not None else {}

    @classmethod
    def for_settings(cls, db: Database, store: AssetStore, settings: Settings) -> Librarian:
        return cls(db, store, settings.library_dir, settings.space)

    def project_dir(self, project: str) -> Path:
        return self.root if self.space else self.root / project

    def projects(self) -> list[str]:
        """Known projects: the space, or those of the database and those already filed."""
        if self.space:
            return [self.space]
        names = set(self.db.list_projects())
        if self.root.exists():
            names |= {path.name for path in self.root.iterdir() if path.is_dir()}
        return sorted(names)

    # --------------------------------------------------------------------- read

    def index_project(self, project: str) -> ProjectIndex:
        """What a project holds, folder by folder. Writes nothing."""
        index = ProjectIndex(project=project)
        for character in self.db.list_characters(project):
            folders, summary = self._character_folders(character)
            index.folders.extend(folders)
            index.characters.append(summary)
        index.folders.extend(self._generation_folders(project))
        index.folders.extend(self._icon_folders(project))
        index.folders.extend(self._video_folders(project))
        index.folders.extend(self._effect_folders(project))
        index.folders.extend(self._render_folders(project, self.briefings(project)))
        style = self._style_folder(project)
        if style is not None:
            index.folders.append(style)
        index.folders = [folder for folder in index.folders if not folder.is_empty()]
        index.folders.sort(key=lambda folder: folder.path)
        return index

    # -------------------------------------------------------------------- write

    def sync_project(self, project: str) -> dict[str, Any]:
        """(Re)write the project's mirror from its index. Returns the manifest."""
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "README.md").write_text(LIBRARY_README, encoding="utf-8")

        index = self.index_project(project)
        base = self.project_dir(project)
        written: set[Path] = {base / "manifest.json", self.root / "README.md"}
        for folder in index.folders:
            target = base / folder.path if folder.path else base
            for file in folder.files:
                source = self.store.path_for(file.asset_id)
                if source is not None:
                    _hardlink(source, target / file.name)
                    written.add(target / file.name)
            for name, document in folder.documents.items():
                target.mkdir(parents=True, exist_ok=True)
                (target / name).write_text(
                    json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
                written.add(target / name)
        _prune(base, written)

        manifest = {
            "project": project,
            "synced_at": _now_iso(),
            "characters": index.characters,
            "generation_batches": index.batches("generations"),
            "icon_sheets": index.batches("icons"),
            "video_batches": index.batches("video"),
            "effects": index.batches("effects"),
            "cost_usd_total": self.db.total_cost(project),
        }
        base.mkdir(parents=True, exist_ok=True)
        (base / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return manifest

    # --------------------------------------------------------------------- style

    def _style_folder(self, project: str) -> LibraryFolder | None:
        folder = LibraryFolder(path="style")
        for pack in self.db.list_style_packs(project):
            for index, asset_id in enumerate(pack.reference_images):
                folder.files.append(
                    LibraryFile(f"ref_{index:02d}_{asset_id[:8]}.png", asset_id))
        return folder

    # --------------------------------------------------------------- generations

    def _generation_folders(self, project: str) -> list[LibraryFolder]:
        """Free generations: one folder per batch, never loose.

        A batch is identified by the `batch` (job id) the worker sets; assets
        without one fall back on date + prompt. A batch requested by a world
        card (`entity`) is not free: those are the entity's concepts, filed
        with it under `2d/<entity>/`.
        """
        assets = [a for a in self.db.list_assets(kind="image", limit=500)
                  if a.meta.get("role") in GENERATION_ROLES
                  and a.meta.get("project", project) == project]

        groups: dict[str, list[Asset]] = {}
        for asset in assets:
            batch = asset.meta.get("batch", "")
            key = batch or f"{asset.created_at:%Y-%m-%d}|{asset.meta.get('prompt', '')}"
            groups.setdefault(key, []).append(asset)

        folders: list[LibraryFolder] = []
        for group in groups.values():
            group.sort(key=lambda a: a.created_at)
            first = group[0]
            prompt = str(first.meta.get("prompt", ""))
            batch = str(first.meta.get("batch", ""))
            entity = str(first.meta.get("entity") or "")
            name = f"{first.created_at:%Y-%m-%d}_{_slug(prompt)}"
            if batch:
                name += f"_{batch}"
            if entity:
                name = f"{first.created_at:%Y-%m-%d}_{batch or _slug(prompt)}"
            folders.append(LibraryFolder(
                path=f"2d/{_slug(entity, max_length=64)}/concepts/{name}" if entity
                else f"generations/{name}",
                files=[LibraryFile(f"{a.id[:12]}.png", a.id) for a in group],
                documents={"batch.json": {
                    "prompt": prompt,
                    "model": first.meta.get("model", ""),
                    "role": first.meta.get("role", ""),
                    "images": len(group),
                    **({"entity": entity} if entity else {}),
                    "created_at": first.created_at.isoformat(timespec="seconds"),
                }},
            ))
        return folders

    # --------------------------------------------------------------------- icons

    def _icon_folders(self, project: str) -> list[LibraryFolder]:
        """Icons split from a sheet: one folder per sheet.

        Files keep their icon name -- the whole point of splitting the sheet --
        and `sheet.json` records where they come from and how they were
        separated.
        """
        assets = [a for a in self.db.list_assets(limit=1000)
                  if a.meta.get("role") in ICON_ROLES
                  and a.meta.get("project", project) == project]

        groups: dict[str, list[Asset]] = {}
        for asset in assets:
            groups.setdefault(str(asset.meta.get("sheet") or "no-sheet"), []).append(asset)

        folders: list[LibraryFolder] = []
        for sheet, group in groups.items():
            group.sort(key=lambda a: (a.meta.get("index", 0), a.id))
            first = group[0]
            files = [
                LibraryFile(
                    f"{_slug(str(a.meta.get('name') or a.id[:12]), max_length=48)}"
                    f"{a.path.suffix}", a.id)
                for a in group
            ]
            folders.append(LibraryFolder(
                path=f"icons/{_slug(sheet, max_length=48)}",
                files=files,
                documents={"sheet.json": {
                    "sheet": sheet,
                    "source": first.meta.get("source", ""),
                    "strategy": first.meta.get("strategy", "single"),
                    "icons": [
                        {"name": a.meta.get("name", ""), "asset_id": a.id,
                         "bbox": a.meta.get("bbox"), "format": a.path.suffix.lstrip("."),
                         **_icon_size(a)}
                        for a in group
                    ],
                    "created_at": first.created_at.isoformat(timespec="seconds"),
                }},
            ))
        return folders

    # ------------------------------------------------------------------- video

    def _video_folders(self, project: str) -> list[LibraryFolder]:
        """Frames taken from a video: one folder per batch.

        Frames are many and numbered (`0001.jpg`): the manifest carries the
        timestamps and provenance, and the contact sheet shows the batch in one
        image. A batch is identified by the key set at extraction
        (`video/importer.py`), which includes the parameters: two extractions
        of the same video therefore do not mix.
        """
        images = [a for a in self.db.list_assets(kind="image", limit=SCAN_LIMIT)
                  if a.meta.get("role") in (VIDEO_FRAME_ROLE, VIDEO_CONTACT_ROLE)
                  and a.meta.get("project", project) == project]
        documents = [a for a in self.db.list_assets(kind="data", limit=SCAN_LIMIT)
                     if a.meta.get("role") == VIDEO_MANIFEST_ROLE
                     and a.meta.get("project", project) == project]

        groups: dict[str, list[Asset]] = {}
        for asset in images + documents:
            groups.setdefault(str(asset.meta.get("video") or "no-batch"), []).append(asset)

        folders: list[LibraryFolder] = []
        for batch, group in groups.items():
            base = f"video/{_slug(batch, max_length=64)}"
            frames = sorted((a for a in group if a.meta.get("role") == VIDEO_FRAME_ROLE),
                            key=lambda a: (a.meta.get("index", 0), a.id))
            contact = next((a for a in group if a.meta.get("role") == VIDEO_CONTACT_ROLE),
                           None)
            manifest = next((a for a in group if a.meta.get("role") == VIDEO_MANIFEST_ROLE),
                            None)
            folder = LibraryFolder(path=base)
            if manifest is not None:
                folder.files.append(LibraryFile("frames.json", manifest.id))
            if contact is not None:
                folder.files.append(LibraryFile("contact.png", contact.id))
            folders.append(folder)
            if frames:
                folders.append(LibraryFolder(
                    path=f"{base}/frames",
                    files=[LibraryFile(f"{int(a.meta.get('index', 0)):04d}.jpg", a.id)
                           for a in frames],
                ))
        return folders

    # ------------------------------------------------------------------- effects

    def _effect_folders(self, project: str) -> list[LibraryFolder]:
        """Rendered effects: one folder per effect, its latest render only.

        An effect is rendered as often as it is tuned; earlier renders stay in
        the store, but the library only shows the latest -- the one the Godot
        scene uses.
        """
        assets = [a for a in self.db.list_assets(limit=SCAN_LIMIT * 2)
                  if a.meta.get("role") in EFFECT_ROLES and a.meta.get("project") == project]
        latest: dict[str, str] = {}
        for asset in assets:
            name = str(asset.meta.get("effect", ""))
            build = str(asset.meta.get("build", ""))
            if name and build > latest.get(name, ""):
                latest[name] = build

        folders: list[LibraryFolder] = []
        for name, build in sorted(latest.items()):
            group = [a for a in assets if a.meta.get("effect") == name
                     and str(a.meta.get("build", "")) == build]
            base = f"effects/{_slug(name, max_length=48)}"
            folder = LibraryFolder(path=base)
            frames = LibraryFolder(path=f"{base}/frames")
            names = {"effect_sheet": f"{name}.png", "effect_atlas": f"{name}.json",
                     "effect_contact": "contact.png", "effect_spec": "spec.yaml"}
            for asset in group:
                role = asset.meta.get("role")
                if role == "effect_frame":
                    index = int(asset.meta.get("index", 0))
                    frames.files.append(LibraryFile(f"{index + 1:04d}.png", asset.id))
                elif role in names:
                    folder.files.append(LibraryFile(names[role], asset.id))
            frames.files.sort(key=lambda file: file.name)
            folders += [folder, frames]
        return folders

    def _render_folders(self, project: str,
                        briefings: dict[str, list[str]]) -> list[LibraryFolder]:
        """Game renders: one file per name, the latest only.

        A card cites its image by this path: rendering again under the same
        name updates it without touching the card. Earlier renders stay in the
        store.

        Two folders, because these are two things: `renders/` for a scene's
        free image, `briefing/<section>/` for one a brief asked for -- the
        illustration of a section's cards, filed with them.
        """
        latest: dict[str, Asset] = {}
        for asset in self.db.list_assets(limit=SCAN_LIMIT):
            meta = asset.meta
            if meta.get("role") != RENDER_ROLE or meta.get("project") != project:
                continue
            name = _slug(str(meta.get("name") or asset.id[:12]), max_length=64)
            held = latest.get(name)
            if held is None or str(meta.get("rendered_at", "")) > \
                    str(held.meta.get("rendered_at", "")):
                latest[name] = asset

        by_folder: dict[str, dict[str, Asset]] = {}
        for name, asset in latest.items():
            by_folder.setdefault(render_folder(asset, briefings), {})[name] = asset
        return [LibraryFolder(path=path, documents=self._renders_document(group),
                              files=[LibraryFile(f"{name}.png", asset.id)
                                     for name, asset in sorted(group.items())])
                for path, group in sorted(by_folder.items())]

    def _renders_document(self, group: dict[str, Asset]) -> dict[str, Any]:
        """What `renders.json` says: where each image of the folder comes from."""
        return {"renders.json": {
            "source": "game scenes rendered by its engine (render_scene)",
            "renders": [{"file": f"{name}.png", "asset_id": asset.id,
                        "scene": asset.meta.get("scene", ""),
                        "scale": asset.meta.get("scale"),
                        "rendered_at": asset.meta.get("rendered_at", "")}
                       for name, asset in sorted(group.items())]}}

    # ---------------------------------------------------------------- characters

    def _character_folders(
        self, character: Character
    ) -> tuple[list[LibraryFolder], dict[str, Any]]:
        """An entity lives in `3d/` once it has a mesh; before, in `2d/` through its concept."""
        manifest = {
            "id": character.spec.id,
            "name": character.spec.name,
            "subject": character.spec.subject,
            "state": character.state.value,
            "pipelines": [p.value for p in character.spec.pipelines],
            "exports": character.exports,
            "errors": character.errors,
            "updated_at": character.updated_at.isoformat(timespec="seconds"),
            "synced_at": _now_iso(),
        }
        entity = character.spec.id
        folders: list[LibraryFolder] = []
        dimensions: list[str] = []

        # --- 2D world: the concept, while the entity has no mesh
        if character.rig3d is None:
            base = LibraryFolder(path=f"2d/{entity}", documents={"manifest.json": manifest})
            if character.concept_asset_id:
                base.files.append(LibraryFile("concept.png", character.concept_asset_id))
            folders.append(base)
            dimensions.append("2d")

        # --- 3D world: the mesh (animated or not), its bare origin, its sprites
        rig3d = character.rig3d
        if rig3d is not None:
            base = LibraryFolder(path=f"3d/{entity}", documents={"manifest.json": manifest})
            if character.concept_asset_id:
                base.files.append(LibraryFile("concept.png", character.concept_asset_id))
            if rig3d.mesh_asset_id:
                base.files.append(LibraryFile(f"{entity}.glb", rig3d.mesh_asset_id))
            # The rigging agent starts from the bare mesh: it must be citable
            # through the library, like everything given to an agent.
            if rig3d.source_mesh_asset_id and rig3d.source_mesh_asset_id != rig3d.mesh_asset_id:
                base.files.append(LibraryFile(f"{entity}-bare.glb", rig3d.source_mesh_asset_id))
            folders.append(base)
            folders.append(LibraryFolder(
                path=f"3d/{entity}/sprites",
                files=[LibraryFile(f"{key}.png", asset_id)
                       for key, asset_id in character.spritesheets.items()],
            ))
            dimensions.append("3d")

        return folders, {"id": entity, "state": character.state.value,
                         "dimensions": dimensions}

    # -------------------------------------------------------------- exploration

    def tree(self, project: str, *, max_entries: int = 400) -> list[str]:
        """Relative list of the files in the project's library."""
        base = self.project_dir(project)
        if not base.exists():
            return []
        entries = sorted(
            path.relative_to(base).as_posix()
            for path in base.rglob("*") if path.is_file()
        )
        return entries[:max_entries]
