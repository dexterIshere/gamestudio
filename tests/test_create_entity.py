"""Simplified entity path: a reference image, then a bare mesh, without network.

What these tests protect: the given image becomes the entity's concept, the mesh
comes out bare and says so, and nothing starts without a description or an
image.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import figure_png

from gamestudio.config import Settings
from gamestudio.pipeline import graph
from gamestudio.pipeline.base import Context, StepResult
from gamestudio.pipeline.graph import create_entity
from gamestudio.store.assets import AssetStore
from gamestudio.store.db import Database


@pytest.fixture()
def ctx(tmp_path: Path) -> Context:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    settings.ensure_dirs()
    return Context(project="entities", settings=settings,
                   db=Database(settings.db_path),
                   store=AssetStore(settings.assets_dir))


class _FakeMesh:
    """Stands in for Tripo: a dummy GLB, without network call."""

    def __init__(self, image_asset_id: str, **_options) -> None:
        self.image = image_asset_id

    def execute(self, ctx: Context, force: bool = False) -> StepResult:
        asset = ctx.store.put_bytes(f"glTF:{self.image}".encode(), ".glb", kind="mesh",
                                    meta={"role": "raw_mesh"})
        ctx.db.save_asset(asset)
        return StepResult(assets=[asset])


def test_a_given_image_yields_a_bare_mesh(ctx: Context, monkeypatch) -> None:
    monkeypatch.setattr(graph, "GenerateMesh", _FakeMesh)
    asset = ctx.store.put_bytes(figure_png(), ".png", kind="image",
                                meta={"role": "imported"})
    ctx.db.save_asset(asset)

    character, report = create_entity(ctx, prompt="a knight", name="Test Knight",
                                      reference_asset_id=asset.id)

    assert character.spec.id == "test-knight"
    # The original cut-out image is used as is: it is the entity's concept.
    assert character.concept_asset_id == asset.id
    assert character.rig3d is not None and character.rig3d.mesh_asset_id
    assert character.rig3d.source_mesh_asset_id == character.rig3d.mesh_asset_id
    assert character.rig3d.bones == [] and character.rig3d.animations == []
    assert "agent" in character.rig3d.normalization_report
    assert not report.errors
    stored = ctx.db.get_character("entities", "test-knight")
    assert stored is not None and stored.rig3d.mesh_asset_id == character.rig3d.mesh_asset_id
    # And the readable library followed, in the 3D world.
    library = ctx.settings.library_dir / "entities" / "3d" / "test-knight"
    assert (library / "test-knight.glb").exists()


def test_an_entity_needs_a_description_or_an_image(ctx: Context) -> None:
    with pytest.raises(ValueError):
        create_entity(ctx, prompt="   ")


def test_an_old_2d_entity_still_reads(tmp_path: Path) -> None:
    """Rigged 2D no longer exists: what named it still reads, without it."""
    from gamestudio.domain.models import CharacterSpec, Pipeline
    from gamestudio.domain.recipe import load_recipe

    spec = CharacterSpec.model_validate({"id": "hero", "name": "Hero", "subject": "x",
                                         "pipelines": ["cutout2d", "mesh3d"]})
    assert spec.pipelines == [Pipeline.MESH_3D]

    recipe = tmp_path / "recipe.yaml"
    recipe.write_text("version: 1\nproject: old\nclips: [{name: walk, source: x}]\n"
                      "characters:\n  - id: hero\n    subject: x\n"
                      "    pipelines: [cutout2d]\n    clips: [walk]\n", encoding="utf-8")
    loaded = load_recipe(recipe)
    assert loaded.characters[0].pipelines == []


# ------------------------------------------------------------------- matting


def _png(alpha: int) -> bytes:
    """A 32 px image: opaque (alpha 255), or on a transparent background."""
    import io

    from PIL import Image

    image = Image.new("RGBA", (32, 32), (200, 40, 40, alpha))
    if alpha < 255:
        image.paste((200, 40, 40, 255), (8, 8, 24, 24))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class _FakeRunware:
    """Stands in for Runware: each task returns a file, nothing leaves."""

    def __init__(self) -> None:
        self.tasks: list[tuple[str, dict]] = []
        self.files_by_url: dict[str, bytes] = {}

    def run(self, task: str, params: dict, **_options) -> dict:
        self.tasks.append((task, params))
        # Generation comes out opaque; matting returns a transparent background.
        url = f"https://fake/{len(self.tasks)}"
        self.files_by_url[url] = _png(255 if task == "imageInference" else 0)
        return {"cost": 0.01, "url": url}

    def files(self, result: dict) -> list[dict]:
        return [{"url": result["url"], "uuid": result["url"].rsplit("/", 1)[1]}]

    def download(self, url: str, target: Path) -> None:
        target.write_bytes(self.files_by_url[url])

    def close(self) -> None:
        pass


def _without_rembg(_image):
    from gamestudio.vision.detect import ToolUnavailable

    raise ToolUnavailable("rembg missing")


def test_an_opaque_reference_keeps_its_original_and_its_negative(ctx: Context,
                                                                monkeypatch) -> None:
    """Matting has a single fallback (Runware), and the paid original stays in the database."""
    from gamestudio.domain.models import StylePack
    from gamestudio.pipeline.steps.generation import GenerateReferencePose
    from gamestudio.vision import detect

    monkeypatch.setattr(detect, "remove_background", _without_rembg)
    fake = _FakeRunware()
    ctx._runware = fake
    result = GenerateReferencePose(StylePack(id="s", name="s"), "a golem",
                                   negative_prompt="text, logo").execute(ctx)

    generation = fake.tasks[0][1]
    assert generation["negativePrompt"].startswith("text, logo")
    assert [task for task, _ in fake.tasks] == ["imageInference", "imageBackgroundRemoval"]
    roles = {asset.meta["role"]: asset for asset in result.assets}
    cut, original = roles["reference_pose"], roles["reference_opaque"]
    assert cut.meta["matting"] == "birefnet" and cut.meta["source"] == original.id
    assert result.cost_usd == pytest.approx(0.02)
    # Both images are in the database: no store file is orphaned.
    assert ctx.db.get_asset(original.id) is not None
    assert ctx.db.get_asset(cut.id) is not None


def test_an_empty_negative_does_not_change_the_fingerprint() -> None:
    """A reference already computed is not paid again because the step gained a setting."""
    from gamestudio.domain.models import StylePack
    from gamestudio.pipeline.steps.generation import GenerateReferencePose

    pack = StylePack(id="s", name="s")
    assert "negative" not in GenerateReferencePose(pack, "a golem").inputs()
    assert GenerateReferencePose(pack, "a golem", negative_prompt="logo").inputs() != \
        GenerateReferencePose(pack, "a golem").inputs()


def test_create_entity_passes_its_negative(ctx: Context, monkeypatch) -> None:
    received: dict = {}

    class _FakeReference:
        def __init__(self, pack, subject, **options) -> None:
            received.update(options)

        def execute(self, ctx: Context, force: bool = False) -> StepResult:
            asset = ctx.store.put_bytes(figure_png(), ".png", kind="image",
                                        meta={"role": "reference_pose"})
            return StepResult(assets=[asset])

    monkeypatch.setattr(graph, "GenerateReferencePose", _FakeReference)
    monkeypatch.setattr(graph, "GenerateMesh", _FakeMesh)
    create_entity(ctx, prompt="a golem", negative_prompt="text")
    assert received["negative_prompt"] == "text"


def test_an_opaque_given_image_is_matted_and_its_cost_counted(ctx: Context,
                                                              monkeypatch) -> None:
    from gamestudio.vision import detect

    monkeypatch.setattr(graph, "GenerateMesh", _FakeMesh)
    monkeypatch.setattr(detect, "remove_background", _without_rembg)
    ctx._runware = _FakeRunware()
    opaque = ctx.store.put_bytes(_png(255), ".png", kind="image", meta={"role": "imported"})
    ctx.db.save_asset(opaque)

    character, report = create_entity(ctx, prompt="a golem", reference_asset_id=opaque.id)

    concept = ctx.db.get_asset(character.concept_asset_id)
    assert concept.meta["source"] == opaque.id and concept.meta["matting"] == "birefnet"
    assert report.cost_usd == pytest.approx(0.01)
    # The report notes the matting, naming the method used.
    assert any("birefnet" in note for note in report.outputs["notes"])


def test_a_roster_does_not_create_a_godot_project_in_a_game(tmp_path: Path) -> None:
    """The refusal comes before the first expense, and the game is not touched."""
    from gamestudio.config import settings as load_settings
    from gamestudio.domain.models import CharacterSpec, Pipeline, StylePack
    from gamestudio.pipeline.graph import build_character
    from gamestudio.store.folders import FolderRegistry, project_paths

    root = tmp_path / "my-game"
    (root / "client").mkdir(parents=True)
    FolderRegistry.for_settings(load_settings()).link("game", root)
    fake = _FakeRunware()
    with Context(project="game") as game:
        game._runware = fake
        spec = CharacterSpec(id="golem", name="Golem", subject="a golem",
                             pipelines=[Pipeline.MESH_3D])
        with pytest.raises(ValueError, match=r"project\.godot"):
            build_character(game, spec, StylePack(id="s", name="s"),
                            godot_project=project_paths(game.settings, "game").godot)
    assert fake.tasks == []
    assert not (root / "project.godot").exists()


def test_a_failed_mesh_keeps_the_reference_cost(ctx: Context, monkeypatch) -> None:
    """The paid reference does not vanish from the bill when the mesh fails after it."""

    class _PaidReference:
        def __init__(self, pack, subject, **_options) -> None:
            pass

        def execute(self, ctx: Context, force: bool = False) -> StepResult:
            asset = ctx.store.put_bytes(figure_png(), ".png", kind="image",
                                        meta={"role": "reference_pose"})
            return StepResult(assets=[asset], cost_usd=0.006)

    class _BrokenMesh:
        def __init__(self, image_asset_id: str, **_options) -> None:
            pass

        def execute(self, ctx: Context, force: bool = False) -> StepResult:
            raise RuntimeError("Tripo unavailable")

    monkeypatch.setattr(graph, "GenerateReferencePose", _PaidReference)
    monkeypatch.setattr(graph, "GenerateMesh", _BrokenMesh)
    with pytest.raises(RuntimeError) as failure:
        create_entity(ctx, prompt="a golem")
    assert failure.value.cost_usd == pytest.approx(0.006)
