"""Library layout: one folder per project, nothing mixed.

The same index writes `.gamestudio/library/` and fills the application's
Library page: what these tests guarantee holds for both. They build a database
shared by several projects -- the hardest case: each project space holds a
single one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.pipeline.base import Context
from gamestudio.sheet import import_sheet
from gamestudio.store.assets import AssetStore
from gamestudio.store.db import Database
from gamestudio.store.library import Librarian

SHEET = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">
  <g id="square" transform="translate(4,4)"><rect width="16" height="16"/></g>
  <g id="circle" transform="translate(64,4)"><circle cx="8" cy="8" r="8"/></g>
</svg>
"""


@pytest.fixture()
def studio(tmp_path: Path):
    """One database, one store, one library -- shared by every project."""
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    settings.ensure_dirs()
    db = Database(settings.db_path)
    store = AssetStore(settings.assets_dir)
    librarian = Librarian(db, store, settings.library_dir)

    def context(project: str) -> Context:
        return Context(project=project, settings=settings, db=db, store=store)

    return settings, db, store, librarian, context


def test_two_projects_do_not_mix(studio, tmp_path: Path):
    """The library must never show everything, all projects together."""
    _, _, _, librarian, context = studio
    source = tmp_path / "pack.svg"
    source.write_text(SHEET, encoding="utf-8")
    other = tmp_path / "other.svg"
    other.write_text(SHEET.replace("square", "diamond"), encoding="utf-8")

    with context("game-a") as ctx:
        import_sheet(source, ctx, multi=True)
    with context("game-b") as ctx:
        import_sheet(other, ctx, multi=True)

    a = librarian.index_project("game-a")
    b = librarian.index_project("game-b")
    assert [folder.path for folder in a.folders] == ["icons/pack"]
    assert [folder.path for folder in b.folders] == ["icons/other"]
    assert not a.asset_ids() & b.asset_ids(), "no asset shared between projects"


def test_folders_are_browsed_level_by_level(studio, tmp_path: Path):
    """What the page needs to navigate: the direct subfolders."""
    _, _, _, librarian, context = studio
    source = tmp_path / "pack.svg"
    source.write_text(SHEET, encoding="utf-8")
    with context("game-a") as ctx:
        import_sheet(source, ctx, multi=True)

    index = librarian.index_project("game-a")
    assert index.subfolders("") == ["icons"]
    assert index.subfolders("icons") == ["icons/pack"]
    assert index.subfolders("icons/pack") == []

    folder = index.folder("icons/pack")
    assert folder is not None
    assert sorted(file.name for file in folder.files) == ["circle.svg", "square.svg"]
    assert "sheet.json" in folder.documents


def test_the_disk_is_the_written_index(studio, tmp_path: Path):
    """One folder per project on disk, exactly what the index describes."""
    settings, _, _, librarian, context = studio
    source = tmp_path / "pack.svg"
    source.write_text(SHEET, encoding="utf-8")
    with context("game-a") as ctx:
        import_sheet(source, ctx, multi=True)
    with context("game-b") as ctx:
        import_sheet(source, ctx, multi=True)

    librarian.sync_project("game-a")
    for project in ("game-a", "game-b"):
        index = librarian.index_project(project)
        expected = {f"{folder.path}/{file.name}"
                    for folder in index.folders for file in folder.files}
        base = settings.library_dir / project
        written = {path.relative_to(base).as_posix()
                   for path in base.rglob("*.svg")} if base.exists() else set()
        if project == "game-a":
            assert written == expected
        else:
            assert not written, "a project not synced writes nothing"

    # Both sheets are the same file: the store deduplicates, yet the library
    # files each in its own project.
    librarian.sync_project("game-b")
    assert (settings.library_dir / "game-b" / "icons" / "pack" / "square.svg").exists()


def test_what_nobody_files_stays_out_of_the_projects(studio):
    """Guides, intermediates: in the store, but in no project's layout."""
    _, db, store, librarian, _ = studio
    orphan = store.put_bytes(b"\x89PNG\r\n\x1a\n left", ".png", kind="image",
                             meta={"role": "openpose_guide"})
    db.save_asset(orphan)

    assert orphan.id not in librarian.index_project("game-a").asset_ids()


def test_known_projects_come_from_the_database(studio, tmp_path: Path):
    _, _, _, librarian, context = studio
    source = tmp_path / "pack.svg"
    source.write_text(SHEET, encoding="utf-8")
    with context("game-a") as ctx:
        import_sheet(source, ctx, multi=True)
    assert "game-a" in librarian.projects()


def test_each_icon_states_its_dimensions(studio, tmp_path: Path):
    """`sheet.json` carries the file's size, not only its frame.

    That is what placing the icon in an engine without opening it takes. A
    bitmap gives its measured pixels; an SVG, its user units.
    """
    from PIL import Image, ImageDraw

    _, _, _, librarian, context = studio
    vector = tmp_path / "pack.svg"
    vector.write_text(SHEET, encoding="utf-8")
    image = Image.new("RGBA", (120, 60), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle([10, 10, 39, 29], fill=(200, 40, 40, 255))
    bitmap = tmp_path / "pack.png"
    image.save(bitmap)

    with context("game") as ctx:
        import_sheet(vector, ctx, multi=True, sheet_name="vectors")
        import_sheet(bitmap, ctx, multi=True, sheet_name="bitmaps")

    index = librarian.index_project("game")
    square = index.folder("icons/vectors").documents["sheet.json"]["icons"][0]
    assert (square["width"], square["height"]) == (16.0, 16.0)

    icon = index.folder("icons/bitmaps").documents["sheet.json"]["icons"][0]
    assert (icon["width"], icon["height"]) == (30, 20), "the PNG's pixels"
