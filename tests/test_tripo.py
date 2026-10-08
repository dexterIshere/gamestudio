"""The Tripo API called directly: the v3 client, and what the provider does with it.

What these tests protect, without a cent and without network (the transport
is simulated):

- the image is uploaded first (`POST /files`), and its `file_token` goes into
  `input` -- the Tripo API does not accept a data URI;
- the task is followed until `success`, and its `credits_consumed` become
  dollars ($0.01 per credit);
- a business failure (non-zero code, `status: failed`, `banned`) is a refusal
  that is reported, never an empty mesh;
- a missing key refuses **before** any call, naming the variable to set;
- the face bounds and quads are those of the chosen model: P2 outputs a quad
  GLB, and an H model's quads -- which the studio cannot render to sprites --
  are refused rather than billed.

The real paid call is not tested here: it needs a key and the user's explicit
consent.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from gamestudio.config import Settings
from gamestudio.pipeline.base import Context
from gamestudio.pipeline.steps.generation import (
    GenerateMesh,
    TripoMesh,
    provider_for,
)
from gamestudio.service import build, using
from gamestudio.service.context import Studio
from gamestudio.tripo import TripoClient, TripoError
from gamestudio.tripo import catalog as tripo

GLB = b"fake glTF, but downloadable"


class FakeTripo:
    """An in-memory Tripo server: it counts the calls, and answers.

    `statuses` is the sequence of states the task returns: the last one repeats.
    """

    def __init__(self, *, statuses: list[str] | None = None,
                 credits: float = 120.0) -> None:
        self.statuses = list(statuses or ["success"])
        self.credits = credits
        self.calls: list[tuple[str, str]] = []
        self.headers: list[dict[str, str]] = []
        self.payload: dict[str, Any] = {}
        self.upload: bytes = b""

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        self.headers.append(dict(request.headers))
        if request.url.path == "/v3/files":
            self.upload = request.read()
            return httpx.Response(200, json={"code": 0, "data": {"file_token": "file_abc"}})
        if request.url.path == "/v3/account/balance":
            return httpx.Response(200, json={"code": 0,
                                             "data": {"balance": 10000.0, "frozen": 200.0}})
        if request.url.path == "/v3/generation/image-to-model":
            self.payload = json.loads(request.read())
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "task_abc"}})
        if request.url.path == "/v3/tasks/task_abc":
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            if status == "success":
                return httpx.Response(200, json={"code": 0, "data": {
                    "task_id": "task_abc", "status": "success", "progress": 100,
                    "output": {"model_url": "https://cdn.tripo3d.ai/output/model.glb"},
                    "credits_consumed": self.credits}})
            return httpx.Response(200, json={"code": 0, "data": {
                "task_id": "task_abc", "status": status, "progress": 40}})
        if request.url.host == "cdn.tripo3d.ai":
            return httpx.Response(200, content=GLB)
        raise AssertionError(f"unexpected call: {request.method} {request.url}")


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context", inbox_dir=tmp_path / "inbox")
    with using(build(settings)) as current:
        yield current


@pytest.fixture
def reference(tmp_path: Path) -> tuple[Path, str]:
    """A reference image in the store, and its asset id."""
    buffer = io.BytesIO()
    Image.new("RGBA", (64, 64), (10, 20, 30, 255)).save(buffer, "PNG")
    path = tmp_path / "reference.png"
    path.write_bytes(buffer.getvalue())
    return path, "asset-reference"


def _context(isolated_studio: Studio, transport: httpx.MockTransport) -> Context:
    game = isolated_studio.space("game")
    ctx = Context(project="game", settings=game.settings, db=game.db, store=game.store)
    ctx._tripo = TripoClient("test-key")
    ctx._tripo._http = httpx.Client(transport=transport,
                                    headers={"Authorization": "Bearer test-key"},
                                    follow_redirects=True)
    return ctx


def _generate(isolated_studio: Studio, image: Path,
              **options: Any) -> tuple[list, dict, FakeTripo]:
    """A full provider call, against a simulated Tripo server."""
    fake = FakeTripo(**{k: v for k, v in options.items()
                        if k in ("statuses", "credits")})
    ctx = _context(isolated_studio, fake.transport())
    try:
        assets, data = TripoMesh().generate(
            ctx, image,
            model=options.get("model", tripo.P2),
            face_limit=options.get("face_limit", 8000),
            quad=options.get("quad", False), pbr=True,
            detailed=options.get("detailed", False), seed=options.get("seed"))
    finally:
        ctx.close()
    return assets, data, fake


# ------------------------------------------------------------------ the client


def test_the_image_is_uploaded_before_the_task_and_credits_become_dollars(
        reference: tuple[Path, str]) -> None:
    image, _ = reference
    fake = FakeTripo(credits=120.0)
    with TripoClient("test-key") as client:
        client._http = httpx.Client(transport=fake.transport(),
                                    headers={"Authorization": "Bearer test-key"},
                                    follow_redirects=True)
        result = client.generation(image, {"model": tripo.P2, "face_limit": 8000})

    assert fake.calls == [("POST", "/v3/files"),
                          ("POST", "/v3/generation/image-to-model"),
                          ("GET", "/v3/tasks/task_abc")]
    assert fake.payload["input"] == "file_abc"
    assert fake.payload["model"] == tripo.P2
    assert result["credits"] == 120.0
    assert result["cost_usd"] == pytest.approx(1.2)
    assert result["task_id"] == "task_abc"
    assert result["url"].endswith("model.glb")


def test_a_missing_key_refuses_before_any_call() -> None:
    with pytest.raises(TripoError, match="TRIPO_API_KEY"):
        TripoClient("")


def test_a_non_zero_business_code_is_a_refusal(reference: tuple[Path, str]) -> None:
    """Tripo answers `{code, message}`: a non-zero code under HTTP 200 is still a refusal."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 1004, "message": "invalid parameter"})

    with TripoClient("test-key") as client:
        client._http = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(TripoError, match="invalid parameter") as exc:
            client.balance()
    assert exc.value.code == "1004"


def test_an_invalid_key_is_reported_with_the_api_suggestion() -> None:
    """The real shape seen on the API: `{code, status, message, suggestion}`.

    An unauthenticated call answers exactly this (checked by hand, without
    spending a credit): the refusal must name the key and keep the suggestion.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={
            "code": 2, "status": "error", "message": "Invalid API key",
            "suggestion": "Check if your credentials is valid"})

    with TripoClient("invalid-key") as client:
        client._http = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(TripoError) as exc:
            client.balance()
    assert exc.value.code == "2"
    assert "Invalid API key" in str(exc.value)
    assert "credentials is valid" in str(exc.value)


def test_a_failed_or_banned_task_is_reported_instead_of_returning_a_mesh(
        reference: tuple[Path, str]) -> None:
    image, _ = reference
    for status, word in (("failed", "failed"), ("banned", "content policy")):
        fake = FakeTripo(statuses=[status])
        with TripoClient("test-key") as client:
            client._http = httpx.Client(transport=fake.transport())
            client.upload(image)
            client.create("/generation/image-to-model", {"input": "file_abc"})
            with pytest.raises(TripoError, match=word):
                client.wait("task_abc", interval=0.0, timeout=5.0)


def test_the_balance_reads_in_credits_and_dollars() -> None:
    fake = FakeTripo()
    with TripoClient("test-key") as client:
        client._http = httpx.Client(transport=fake.transport())
        balance = client.balance()
    assert balance == {"credits": 10000.0, "frozen": 200.0, "usd": 100.0}


# ---------------------------------------------------------------- the provider


def test_the_provider_files_a_glb_and_returns_the_task(
        reference: tuple[Path, str], isolated_studio: Studio) -> None:
    image, _ = reference
    assets, data, _ = _generate(isolated_studio, image, model=tripo.P2)

    assert len(assets) == 1
    asset = assets[0]
    assert asset.kind == "mesh"
    assert asset.meta["provider"] == "tripo"
    assert asset.meta["model"] == tripo.P2
    assert asset.meta["task_id"] == "task_abc"
    assert asset.meta["credits"] == 120.0
    assert data["cost_usd"] == pytest.approx(1.2)
    assert data["format"] == "glb"
    # The mesh did enter the store, and the temporary work is gone.
    game = isolated_studio.space("game")
    assert game.store.path_for(asset.id).read_bytes() == GLB
    assert not list(game.settings.work_for("game").glob("tripo-*.glb"))


def test_p2_asks_for_quads_and_always_outputs_glb(
        reference: tuple[Path, str], isolated_studio: Studio) -> None:
    """P2's point: native quads in a GLB, where Runware forces an FBX."""
    image, _ = reference
    _, data, fake = _generate(isolated_studio, image, model=tripo.P2, face_limit=4000,
                              quad=True, detailed=True, seed=7)

    assert fake.payload["quad"] is True
    assert fake.payload["face_limit"] == 4000
    assert fake.payload["model_seed"] == 7
    # P2 does not know `geometry_quality`: sending it would get the task refused.
    assert "geometry_quality" not in fake.payload
    assert fake.payload["texture_quality"] == "detailed"
    assert data["quad"] is True
    assert data["format"] == "glb"


def test_the_face_budget_is_clamped_to_the_model_bounds(
        reference: tuple[Path, str], isolated_studio: Studio) -> None:
    """P1 does not go up to 33,000 faces: the budget is clamped, and that is said."""
    image, _ = reference
    _, data, fake = _generate(isolated_studio, image, model=tripo.P1, face_limit=33_000)

    assert fake.payload["face_limit"] == 20_000
    assert "20000" in data["notes"]


@pytest.mark.parametrize("pbr", [True, False])
def test_an_h_model_s_quads_are_refused_rather_than_billed(
        reference: tuple[Path, str], isolated_studio: Studio, pbr: bool) -> None:
    """H's quads come out as FBX, which the studio cannot render to sprites -- PBR or not."""
    image, _ = reference
    fake = FakeTripo()
    ctx = _context(isolated_studio, fake.transport())
    try:
        with pytest.raises(ValueError, match="FBX"):
            TripoMesh().generate(ctx, image, model=tripo.H31, face_limit=8000,
                                 quad=True, pbr=pbr, detailed=False, seed=None)
    finally:
        ctx.close()
    assert fake.calls == [], "a refusal must cost no call"


# --------------------------------------------------------------------- routing


def test_the_model_picks_the_route_without_changing_the_step(
        reference: tuple[Path, str], isolated_studio: Studio) -> None:
    """A Tripo id routes to Tripo; a Runware AIR stays with Runware."""
    _, asset_id = reference
    assert provider_for(tripo.P2) == "tripo"
    assert provider_for(tripo.P1) == "tripo"
    assert provider_for("tripo:v3.1@0") == "runware"
    assert GenerateMesh(asset_id, model=tripo.P2).provider == "tripo"
    assert GenerateMesh(asset_id).provider == "runware"
    # The step's fingerprint names the route: two routes, two computations.
    assert GenerateMesh(asset_id, model=tripo.P2).inputs() != GenerateMesh(asset_id).inputs()


@pytest.mark.parametrize(("model", "options", "credits"), [
    # Tripo's grid, image -> 3D (developers.tripo3d.com/en/pricing,
    # /en/models/p1, /en/models/v3-1, /en/docs/changelog): untextured base,
    # standard texture +10, detailed +20; detailed geometry +20 (H family).
    (tripo.P2, {}, 110),
    (tripo.P2, {"texture_quality": "detailed"}, 120),
    (tripo.P1, {}, 50),
    (tripo.P1, {"texture_quality": "detailed"}, 60),
    (tripo.H31, {}, 30),
    (tripo.H31, {"texture_quality": "detailed"}, 40),
    (tripo.H31, {"texture_quality": "detailed", "geometry_quality": "detailed"}, 60),
    # P1 and P2 do not know detailed geometry: it is not paid for.
    (tripo.P2, {"geometry_quality": "detailed"}, 110),
    # Quads are paid where they exist: only P2 outputs them as GLB.
    (tripo.P2, {"quad": True}, 115),
    (tripo.P2, {"texture_quality": "detailed", "quad": True}, 125),
    (tripo.P1, {"quad": True}, 50),
])
def test_the_price_follows_the_tripo_grid(model: str, options: dict, credits: float) -> None:
    """A wrong displayed price would get clicks: it is computed, never guessed."""
    assert tripo.credits_for(model, **options) == pytest.approx(credits)
    assert tripo.usd_for(model, **options) == pytest.approx(credits / 100)


def test_a_model_off_the_grid_is_never_announced_cheaper() -> None:
    """Underestimating would get a spending accepted that nobody saw."""
    assert tripo.credits_for("tripo-unknown") == pytest.approx(
        max(tripo.IMAGE_BASE_CREDITS.values()) + 10)


def test_the_catalogue_announces_the_studio_request() -> None:
    """`cost_usd` with standard texture, `cost_detailed_usd` in detailed quality."""
    catalogue = {model["id"]: model for model in tripo.models()}
    assert list(catalogue) == [tripo.P2, tripo.P1, tripo.H31]
    assert catalogue[tripo.P2]["cost_usd"] == pytest.approx(1.10)
    assert catalogue[tripo.P2]["cost_detailed_usd"] == pytest.approx(1.20)
    assert catalogue[tripo.P1]["cost_usd"] == pytest.approx(0.50)
    assert catalogue[tripo.P1]["cost_detailed_usd"] == pytest.approx(0.60)
    # H3.1's detailed quality also covers geometry: `TripoMesh` sends it.
    assert catalogue[tripo.H31]["cost_usd"] == pytest.approx(0.30)
    assert catalogue[tripo.H31]["cost_detailed_usd"] == pytest.approx(0.60)


def test_the_face_budget_follows_the_documented_bounds() -> None:
    assert tripo.clamp_face_limit(tripo.P1, 50_000) == 20_000
    assert tripo.clamp_face_limit(tripo.P2, 10) == 48
    assert tripo.clamp_face_limit(tripo.P2, 12_000) == 12_000
    assert tripo.clamp_face_limit(tripo.P2, 40_000) == 40_000
    # In quad mode, P2 counts quads: half as many.
    assert tripo.clamp_face_limit(tripo.P2, 40_000, quad=True) == 25_000
    assert tripo.face_limits(tripo.P1, quad=True) == (48, 20_000)
    assert tripo.face_limits(tripo.H31) == (48, 2_000_000)
