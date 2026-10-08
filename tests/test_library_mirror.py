"""Readable library: the store's mirror, entity by entity.

Everything is local and synthetic: no network call. An entity lives in `2d/`
through its concept while it has no mesh, then in `3d/` -- never in both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import figure_png

from gamestudio.config import Settings
from gamestudio.domain.models import Character, CharacterSpec, JobState, Pipeline, Rig
from gamestudio.pipeline.base import Context
from gamestudio.store.assets import AssetStore
from gamestudio.store.db import Database
from gamestudio.store.library import Librarian


@pytest.fixture()
def ctx(tmp_path: Path) -> Context:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    settings.ensure_dirs()
    return Context(project="testlib", settings=settings,
                   db=Database(settings.db_path),
                   store=AssetStore(settings.assets_dir))


def _concept(ctx: Context) -> str:
    asset = ctx.store.put_bytes(figure_png(), ".png", kind="image",
                                meta={"role": "reference_pose"})
    ctx.db.save_asset(asset)
    return asset.id


def test_an_entity_without_mesh_lives_through_its_concept(ctx: Context) -> None:
    concept = _concept(ctx)
    ctx.db.save_character("testlib", Character(
        spec=CharacterSpec(id="hero", name="Hero", subject="test"),
        style_pack_id="testlib-style", concept_asset_id=concept, state=JobState.DONE))
    librarian = Librarian(ctx.db, ctx.store, ctx.settings.library_dir)
    manifest = librarian.sync_project("testlib")

    base = librarian.project_dir("testlib") / "2d" / "hero"
    assert (base / "concept.png").exists()
    assert (base / "manifest.json").exists()
    assert not (librarian.project_dir("testlib") / "3d" / "hero").exists()
    assert manifest["characters"][0] == {"id": "hero", "state": "done",
                                         "dimensions": ["2d"]}

    # Files are hard links: no copy of the content.
    source = ctx.store.path_for(concept)
    assert (base / "concept.png").stat().st_ino == source.stat().st_ino

    # Resyncing is idempotent.
    librarian.sync_project("testlib")
    assert (base / "concept.png").exists()


def test_generations_are_filed_by_batch(ctx: Context) -> None:
    """Free generations are filed by batch, with their batch.json."""
    for index, batch in enumerate(("batch1", "batch1", "batch2")):
        asset = ctx.store.put_bytes(figure_png()[:-index - 1] + b"\0" * (index + 1),
                                    ".png", kind="image",
                                    meta={"role": "generation", "project": "testlib",
                                          "prompt": "a knight", "model": "runware:101@1",
                                          "batch": batch})
        ctx.db.save_asset(asset)

    librarian = Librarian(ctx.db, ctx.store, ctx.settings.library_dir)
    manifest = librarian.sync_project("testlib")

    generations = librarian.project_dir("testlib") / "generations"
    folders = sorted(p.name for p in generations.iterdir() if p.is_dir())
    assert len(folders) == 2, "one folder per batch"
    assert all("a-knight" in name for name in folders)
    batch1 = next(p for p in generations.iterdir() if p.name.endswith("batch1"))
    assert len(list(batch1.glob("*.png"))) == 2
    batch_doc = json.loads((batch1 / "batch.json").read_text(encoding="utf-8"))
    assert batch_doc["prompt"] == "a knight" and batch_doc["images"] == 2
    assert len(manifest["generation_batches"]) == 2


def test_a_rigged_entity_lives_in_3d_with_its_bare_mesh(ctx: Context) -> None:
    """The GLB delivered by the agent, and the bare mesh it started from."""
    bare = ctx.store.put_bytes(b"glTF-bare", ".glb", kind="mesh")
    rigged = ctx.store.put_bytes(b"glTF-rigged", ".glb", kind="mesh")
    for asset in (bare, rigged):
        ctx.db.save_asset(asset)
    spec = CharacterSpec(id="golem", name="Golem", subject="a stone golem",
                         pipelines=[Pipeline.MESH_3D])
    ctx.db.save_character("testlib", Character(
        spec=spec, style_pack_id="s", concept_asset_id=_concept(ctx),
        state=JobState.DONE,
        rig3d=Rig(archetype=spec.archetype, bones=["hips"], mesh_asset_id=rigged.id,
                  source_mesh_asset_id=bare.id, animations=["idle_loop"])))

    librarian = Librarian(ctx.db, ctx.store, ctx.settings.library_dir)
    librarian.sync_project("testlib")

    base = librarian.project_dir("testlib") / "3d" / "golem"
    assert (base / "golem.glb").read_bytes() == b"glTF-rigged"
    assert (base / "golem-bare.glb").read_bytes() == b"glTF-bare"
    assert (base / "concept.png").exists()
    assert not (librarian.project_dir("testlib") / "2d" / "golem").exists()
