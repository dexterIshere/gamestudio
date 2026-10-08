"""3D meshes: the production routes, and importing an external mesh.

What these tests protect: a mesh made elsewhere -- by `img2threejs` on the
machine, or by any tool -- enters the studio for free, becomes visible in the
library, and is never presented as valid while nobody has looked at it.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.service import build, meshes, using, workspace
from gamestudio.service.context import Studio
from gamestudio.service.errors import NotFound, ServiceError


def glb(extra: dict | None = None) -> bytes:
    """A minimal but valid GLB: a header, then a JSON chunk.

    The import does not read the geometry -- Blender will -- so an empty glTF
    document is enough to exercise the path end to end.
    """
    document = {"asset": {"version": "2.0"}, "scenes": [{"nodes": []}],
                "nodes": [], **(extra or {})}
    payload = json.dumps(document).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(payload))
    return header + struct.pack("<II", len(payload), 0x4E4F534A) + payload


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context")
    with using(build(settings)) as current:
        yield current


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "model.glb"
    path.write_bytes(glb())
    return path


def test_the_three_routes_are_listed_free_first(isolated_studio: Studio) -> None:
    """An agent choosing 3D must see the price before spending."""
    providers = meshes.mesh_providers()
    assert [provider["id"] for provider in providers] == ["local", "runware", "tripo"]
    assert providers[0]["paid"] is False
    assert providers[0]["models"][0]["cost_usd"] == 0.0
    assert providers[1]["paid"] is True
    assert all(model["cost_usd"] > 0 for model in providers[1]["models"])
    # Runware's 3D models are those of the AIR catalog, not copies.
    from gamestudio.runware import catalog as air

    assert {model["id"] for model in providers[1]["models"]} == {
        air.TRIPO, air.HUNYUAN_PRO, air.HUNYUAN_RAPID, air.TRELLIS}
    # The direct route carries the models Runware does not host, and says
    # whether its key is set: a route not yet usable is still shown.
    from gamestudio.tripo import catalog as tripo

    assert providers[2]["paid"] is True
    assert {model["id"] for model in providers[2]["models"]} == {
        tripo.P1, tripo.P2, tripo.H31}
    assert providers[2]["ready"] == bool(isolated_studio.settings.tripo_api_key)


def test_an_imported_mesh_becomes_visible_in_the_library(
        isolated_studio: Studio, source: Path) -> None:
    """Without attachment the asset would stay invisible: that is the whole point."""
    result = meshes.import_mesh(str(source), project="game", name="Arachne")

    assert result["entity"] == "arachne"
    assert result["attached"] is True
    assert result["state"] == "needs_review"
    folder = isolated_studio.space("game").paths.library / "3d" / "arachne"
    assert (folder / "arachne.glb").is_file()

    character = isolated_studio.space("game").db.get_character("game", "arachne")
    assert character is not None
    assert character.rig3d is not None
    assert character.rig3d.mesh_asset_id == result["asset_id"]
    # An empty document has neither bones nor animation: the delivery says so,
    # without judging.
    assert character.rig3d.normalization_report.startswith("no bones")
    # The mesh is a step of the workspace card.
    steps = workspace.steps("game")
    assert any(step["kind"] == "character" for step in steps)
    assert result["asset_id"] != ""


def test_the_imported_mesh_is_in_the_report(isolated_studio: Studio, source: Path) -> None:
    """The project summary must say what just came in."""
    meshes.import_mesh(str(source), project="game", name="Arachne")
    html = Path(workspace.write_report("game")).read_text(encoding="utf-8")
    assert "arachne" in html
    assert "arachne.glb" in html
    assert "no bones" in html


def test_replacing_an_existing_mesh_is_explicit(
        isolated_studio: Studio, tmp_path: Path, source: Path) -> None:
    """Overwriting a valid mesh by mistake would cost a full redo."""
    meshes.import_mesh(str(source), project="game", name="Arachne")

    with pytest.raises(ServiceError, match="replace=true"):
        meshes.import_mesh(str(source), project="game", name="Arachne")

    other = tmp_path / "other.glb"
    other.write_bytes(glb({"extras": {"version": 2}}))
    first = isolated_studio.space("game").db.get_character("game", "arachne").rig3d.mesh_asset_id
    result = meshes.import_mesh(str(other), project="game", name="Arachne", replace=True)
    character = isolated_studio.space("game").db.get_character("game", "arachne")
    assert character.rig3d.mesh_asset_id == result["asset_id"]
    # The original bare mesh survives the delivery: the agent starts again from it.
    assert character.rig3d.source_mesh_asset_id == first
    folder = isolated_studio.space("game").paths.library / "3d" / "arachne"
    assert (folder / "arachne-bare.glb").is_file()


def rigged_glb() -> bytes:
    """What the agent delivers: bones under a skin, and two named animations."""
    return glb({
        "nodes": [{"name": "hips", "children": [1]}, {"name": "spine", "children": [2]},
                  {"name": "Bone.003"}],
        "skins": [{"joints": [0, 1, 2]}],
        "animations": [{"name": "idle", "channels": [], "samplers": []},
                       {"name": "attack", "channels": [], "samplers": []}],
    })


def test_an_agent_delivery_is_inventoried(isolated_studio: Studio,
                                          tmp_path: Path) -> None:
    """Bones, animations and canonical names are measured on entry, without Blender."""
    delivery = tmp_path / "golem.glb"
    delivery.write_bytes(rigged_glb())
    result = meshes.import_mesh(str(delivery), project="game", name="Golem")

    assert result["bones"] == 3
    assert result["animations"] == ["idle", "attack"]
    character = isolated_studio.space("game").db.get_character("game", "golem")
    assert character.rig3d.animations == ["idle", "attack"]
    assert character.rig3d.bones == ["hips", "spine", "Bone.003"]
    # Two bones out of three carry a canonical name: a measurement, not a refusal.
    assert "3 bones, 2 with a canonical name" in character.rig3d.normalization_report
    assert character.state.value == "needs_review"


def test_the_mesh_goes_to_godot_only_if_the_folder_has_a_project(
        isolated_studio: Studio, tmp_path: Path) -> None:
    """No project.godot is ever created: a game whose Godot lives elsewhere gets none."""
    delivery = tmp_path / "golem.glb"
    delivery.write_bytes(rigged_glb())
    godot = isolated_studio.space("game").paths.godot

    without = meshes.import_mesh(str(delivery), project="game", name="Golem")
    assert without["godot"] is None
    assert not (godot / "project.godot").exists()
    assert not (godot / "characters").exists()

    (godot / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    with_project = meshes.import_mesh(str(delivery), project="game", name="Golem",
                                      replace=True)
    assert with_project["godot"] == "res://characters/golem/golem.glb"
    assert (godot / "characters" / "golem" / "golem.glb").read_bytes() == rigged_glb()


def test_an_unattached_mesh_files_nothing(isolated_studio: Studio,
                                          source: Path) -> None:
    """`attach=false` is a deposit: the asset exists, the library does not claim it."""
    result = meshes.import_mesh(str(source), project="game", name="Arachne", attach=False)
    assert result["attached"] is False
    assert result["library"] is None
    assert isolated_studio.space("game").db.get_character("game", "arachne") is None
    assert not (isolated_studio.space("game").paths.library / "3d").exists()
    assert isolated_studio.space("game").store.path_for(result["asset_id"]) is not None


def test_formats_and_guards(isolated_studio: Studio, tmp_path: Path) -> None:
    """An unknown format, an empty or missing file are refused with a reason."""
    assert ".glb" in meshes.mesh_formats()

    text = tmp_path / "notes.txt"
    text.write_text("not a mesh")
    with pytest.raises(ServiceError, match="unrecognized format"):
        meshes.import_mesh(str(text))

    empty = tmp_path / "empty.glb"
    empty.write_bytes(b"")
    with pytest.raises(ServiceError, match="is empty"):
        meshes.import_mesh(str(empty))

    with pytest.raises(NotFound):
        meshes.import_mesh(str(tmp_path / "missing.glb"))
