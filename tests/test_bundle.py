"""Reading a recipe, packing a character.

Two operations the 3D space and the workbench call. Neither touches the network
nor launches Blender.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from conftest import declare_recipe

from gamestudio import service
from gamestudio.config import Settings
from gamestudio.domain.models import Character, CharacterSpec, JobState, Rig
from gamestudio.domain.skeleton import Archetype
from gamestudio.service import NotFound

RECIPE = """\
version: 1
project: trial
style:
  id: trial-style
  name: Trial style
characters:
  - id: hero
    name: Hero
    subject: "a test hero"
"""


@pytest.fixture()
def studio(tmp_path: Path):
    """The `trial` project's space: its database, its store, its paths."""
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    declare_recipe(settings, "trial", RECIPE)
    with service.using(service.build(settings)) as current:
        yield current.space("trial")


def _mesh_character(studio) -> tuple[Character, str]:
    """A 3D character from the entity path: a bare mesh, without bones."""
    mesh = studio.store.put_bytes(b"glTF-dummy", ".glb", kind="mesh", meta={"role": "raw_mesh"})
    studio.db.save_asset(mesh)
    character = Character(
        spec=CharacterSpec(id="golem", name="Golem", subject="a golem"),
        style_pack_id="trial-free", state=JobState.DONE,
        rig3d=Rig(archetype=Archetype.BIPED, bones=[],
                  mesh_asset_id=mesh.id, source_mesh_asset_id=mesh.id),
    )
    studio.db.save_character("trial", character)
    return character, mesh.id


def test_a_recipe_source_reads(studio):
    source = service.catalog.recipe_source("trial")
    assert source["text"].startswith("version: 1")
    assert source["error"] is None
    with pytest.raises(NotFound):
        service.catalog.recipe_source("missing")


def test_the_bundle_takes_the_mesh_and_the_godot_folder(studio):
    character, _raw = _mesh_character(studio)
    scene_dir = studio.paths.godot / "characters" / "golem"
    (scene_dir / "sheets").mkdir(parents=True)
    (scene_dir / "golem.glb").write_bytes(b"glTF-dummy")
    (scene_dir / "golem.glb.import").write_text("Godot cache", encoding="utf-8")
    (scene_dir / "sheets" / "idle_s.png").write_bytes(b"png")
    character.exports["mesh"] = "res://characters/golem/golem.glb"
    studio.db.save_character("trial", character)

    bundle = service.catalog.character_bundle("trial", "golem")

    archive = Path(bundle["path"])
    assert archive.parent == studio.paths.exports
    with zipfile.ZipFile(archive) as opened:
        names = set(opened.namelist())
    assert "golem/3d/golem.glb" in names
    assert "golem/godot/characters/golem/golem.glb" in names
    assert "golem/godot/characters/golem/sheets/idle_s.png" in names
    assert "golem/manifest.json" in names
    # Godot's import cache is regenerated: it has no place in the bundle.
    assert not any(name.endswith(".import") for name in names)


def test_a_character_with_nothing_is_not_exported(studio):
    studio.db.save_character("trial", Character(
        spec=CharacterSpec(id="empty", name="Empty", subject="x"), style_pack_id="trial-free"))
    with pytest.raises(NotFound, match="has not produced anything"):
        service.catalog.character_bundle("trial", "empty")
