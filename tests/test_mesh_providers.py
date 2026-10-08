"""The mesh provider seam.

What these tests protect: adding a direct 3D API (Hunyuan, Tripo) must be
**one class and one registration** -- not a change to the pipeline step. The day
that stops being true, these tests fail.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
from PIL import Image

from gamestudio.config import Settings
from gamestudio.pipeline.base import Context, StepResult
from gamestudio.pipeline.steps.generation import (
    MESH_PROVIDERS,
    GenerateMesh,
    MeshProvider,
    RunwareMesh,
    mesh_provider,
    register_mesh_provider,
)
from gamestudio.service import build, using
from gamestudio.service.context import Studio


class FakeProvider(MeshProvider):
    """A local provider: it writes a file and talks to nobody."""

    id = "fake"
    label = "Test provider"
    paid = False
    calls: ClassVar[list[dict]] = []

    def generate(self, ctx, image, *, model, face_limit, quad, pbr, detailed, seed):
        FakeProvider.calls.append({"model": model, "face_limit": face_limit,
                                   "quad": quad, "seed": seed,
                                   "image": image.name})
        asset = ctx.store.put_bytes(b"glTF fake", ".glb", kind="mesh",
                                    meta={"role": "raw_mesh", "provider": self.id})
        return [asset], {"model": model, "format": "glb", "cost_usd": 0.0}


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context",
                        inbox_dir=tmp_path / "inbox")
    with using(build(settings)) as current:
        yield current


@pytest.fixture
def reference(isolated_studio: Studio) -> str:
    buffer = io.BytesIO()
    Image.new("RGBA", (64, 64), (10, 20, 30, 255)).save(buffer, "PNG")
    game = isolated_studio.space("game")
    asset = game.store.put_bytes(buffer.getvalue(), ".png", kind="image",
                                 meta={"role": "reference_pose"})
    game.db.save_asset(asset)
    return asset.id


def test_runware_is_the_default_provider(isolated_studio: Studio) -> None:
    """The catalog is closed: an invented identifier is reported."""
    assert MESH_PROVIDERS["runware"] is not None
    assert mesh_provider("runware").paid is True
    with pytest.raises(ValueError, match="unknown"):
        mesh_provider("an-api-that-does-not-exist")


def test_a_direct_api_is_added_without_touching_the_step(isolated_studio: Studio,
                                                         reference: str) -> None:
    """The point of the seam: registering is enough, the step does not change."""
    register_mesh_provider(FakeProvider())
    try:
        reference_path = isolated_studio.space("game").store.path_for(reference)
        game = isolated_studio.space("game")
        with Context(project="game", settings=game.settings, db=game.db,
                     store=game.store) as ctx:
            result = GenerateMesh(reference, model="some-model", face_limit=1200,
                                  seed=7, provider="fake").run(ctx)

        assert isinstance(result, StepResult)
        assert result.data["provider"] == "fake"
        assert result.data["model"] == "some-model"
        assert result.cost_usd == 0.0
        assert len(result.assets) == 1
        # The provider received everything it needs, including the image's path
        # -- not an asset id it would have to resolve itself.
        assert FakeProvider.calls[-1] == {
            "model": "some-model", "face_limit": 1200, "quad": False, "seed": 7,
            "image": reference_path.name}
    finally:
        MESH_PROVIDERS.pop("fake", None)


def test_the_step_fingerprint_names_the_provider(isolated_studio: Studio,
                                                 reference: str) -> None:
    """Two providers on the same image are not the same computation.

    Otherwise the step cache would return the first provider's mesh for the
    second -- and the studio would believe it had tried both.
    """
    runware = GenerateMesh(reference).inputs()
    other = GenerateMesh(reference, provider="fake").inputs()
    assert runware["provider"] == RunwareMesh.id
    assert other["provider"] == "fake"
    assert runware != other
