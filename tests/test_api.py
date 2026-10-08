"""HTTP API: the surface the desktop application consumes.

Routes carry no logic -- it is tested in `test_service` -- so what is checked
here is the translation: parameters, status codes, and the fact that a paid
operation does not start from a plain HTTP call.

The token is set by the environment (`GAMESTUDIO_TOKEN`) rather than read from
disk: the tests never touch the installation's token, and the wall is checked
from both sides -- what passes with it, what fails without it.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from conftest import declare_recipe, figure_png
from fastapi.testclient import TestClient

from gamestudio import service
from gamestudio.api import auth
from gamestudio.api.app import app
from gamestudio.config import Settings
from gamestudio.domain.models import Character, CharacterSpec, Job, JobState, Pipeline

WIDTH, HEIGHT = 192, 288

# The tests' token, and the header carrying it. A fixed value is enough: it is
# never written to disk, since the environment wins (see `auth.expected`).
TOKEN = "test-token"
AUTHORIZATION = {"Authorization": f"Bearer {TOKEN}"}

# The studio's host: the wall only answers loopback, and `TestClient` presents
# itself as `testserver` by default -- which the wall rightly refuses.
HOST = "127.0.0.1:7788"

RECIPE = """\
version: 1
project: trial
style:
  id: trial-style
  name: Trial style
  prompt_prefix: "test"
defaults:
  archetype: biped
  pipelines: [mesh3d]
characters:
  - id: hero
    name: Hero
    subject: "a test hero"
"""


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A client without lifecycle: no worker starts, nothing runs.

    `TestClient` outside a context manager does not call the `lifespan`: the
    queue receives jobs and nobody consumes them, which is exactly what is
    wanted to observe what goes in.
    """
    monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
    auth.reset()
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    declare_recipe(settings, "trial", RECIPE)
    with service.using(service.build(settings)):
        yield local_client(AUTHORIZATION)


def local_client(headers: dict[str, str] | None = None) -> TestClient:
    """A client that talks to loopback, like the real front.

    `TestClient` presents itself as host `testserver` by default, which the
    wall refuses -- rightly, that is exactly what it must do with a name that
    is not loopback. The address and port reproduce the studio's, otherwise
    every test would check the host refusal instead of what it means to check.
    """
    return TestClient(app, base_url=f"http://{HOST}", headers=headers)


@pytest.fixture()
def character(client):
    """A character image in the store, like a generated concept."""
    studio = service.studio()
    path = studio.settings.data_dir / "character.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(figure_png(WIDTH, HEIGHT))
    trial = studio.space("trial")
    asset = trial.store.put_file(path, kind="image")
    trial.db.save_asset(asset)
    return asset.id


# ------------------------------------------------------------------------ read


def test_health_announces_the_workers(client):
    body = client.get("/api/health").json()
    assert "workers" in body and "queue" in body
    assert body["data_dir"].endswith("data")


def test_recipes_are_served(client):
    body = client.get("/api/recipes").json()
    assert [entry["project"] for entry in body] == ["trial"]


def test_an_unknown_character_answers_404(client):
    response = client.get("/api/projects/trial/characters/ghost")
    assert response.status_code == 404
    assert "ghost" in response.json()["detail"]


def test_an_unknown_asset_answers_404(client):
    assert client.get(f"/api/assets/{'0' * 32}").status_code == 404


def test_a_job_detail_carries_its_report(client):
    """What the window shows comes from here: fingerprints are already resolved."""
    studio = service.studio().space("trial")
    concept = studio.store.put_bytes(b"\x89PNG\r\n\x1a\n concept", ".png", kind="image")
    studio.db.save_asset(concept)
    studio.db.save_character("trial", Character(
        spec=CharacterSpec(id="hero", name="Hero", subject="a hero",
                           pipelines=[Pipeline.MESH_3D]),
        style_pack_id="", concept_asset_id=concept.id))
    studio.db.save_job(Job(id="j1", kind="render_sprites", project="trial",
                           state=JobState.DONE, result={"assets": [concept.id]},
                           cost_usd=0.03))

    body = client.get("/api/jobs/j1").json()

    assert [entry["folder"] for entry in body["report"]["files"]] == ["2d/hero"]
    assert body["report"]["cost_usd"] == 0.03
    # The raw result is still served: the report translates it, it does not
    # replace it.
    assert body["result"] == {"assets": [concept.id]}


def test_an_unknown_job_answers_404(client):
    assert client.get("/api/jobs/never-seen").status_code == 404


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="ffmpeg/ffprobe missing: frame extraction depends on them")
def test_video_frames_come_out_through_the_api(client, tmp_path: Path):
    """The route only translates: the video is made on the spot, offline."""
    source = tmp_path / "run.mp4"
    subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=25:duration=1",
         "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(source)],
        check=True, capture_output=True, timeout=120)

    body = client.post("/api/video/frames",
                       json={"path": str(source), "name": "run", "fps": 4}).json()

    assert Path(body["library"]).is_dir()
    assert Path(body["manifest"]).is_file()
    assert Path(body["contact_sheet"]).is_file()
    assert len(list(Path(body["frames_folder"]).glob("*.jpg")))


def test_a_missing_video_answers_404(client):
    response = client.post("/api/video/frames", json={"path": "/nowhere.mp4"})
    assert response.status_code == 404


def test_a_thumbnail_is_an_immutable_png(client, character):
    asset_id = character
    response = client.get(f"/api/assets/{asset_id}/preview", params={"size": 64})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    # An asset id is the hash of its content: a thumbnail can never become
    # stale, so it is cached without reserve.
    assert "immutable" in response.headers["cache-control"]


def test_the_raw_file_is_served_as_is(client, character):
    asset_id = character
    response = client.get(f"/api/assets/{asset_id}/file")
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_served_file_runs_no_script(client, character):
    """The token is in a file's URL: an SVG opened alone must run nothing."""
    response = client.get(f"/api/assets/{character}/file")
    assert response.headers["content-security-policy"] == "script-src 'none'; sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert client.put("/api/workspace/trial/logo",
                      files={"file": ("logo.svg", svg, "image/svg+xml")}).status_code == 200
    logo = client.get("/api/workspace/trial/logo")
    assert logo.headers["content-type"].startswith("image/svg+xml")
    assert "script-src 'none'" in logo.headers["content-security-policy"]


# ------------------------------------------------------------------ safeguards


def test_a_build_without_confirmation_answers_402(client):
    response = client.post("/api/produce/build", json={"recipe": "trial"})
    assert response.status_code == 402
    assert "confirm" in response.json()["detail"]
    assert client.get("/api/jobs").json() == [], "nothing may be queued"


def test_a_3d_entity_without_confirmation_answers_402(client):
    response = client.post("/api/produce/entity", json={"prompt": "a dragon"})
    assert response.status_code == 402


@pytest.mark.parametrize("route,body", [
    ("/api/produce/image", {"prompt": "a hero", "recipe": "trial"}),
    ("/api/produce/style/explore", {"recipe": "trial", "subject": "a blacksmith"}),
    ("/api/produce/style/train", {"recipe": "trial",
                                  "images": [f"{index:032x}" for index in range(10)]}),
])
def test_a_spending_without_confirmation_answers_402_with_its_amount(client, route, body):
    response = client.post(route, json=body)
    assert response.status_code == 402, response.text
    assert "$" in response.json()["detail"] and "confirm" in response.json()["detail"]
    assert client.get("/api/jobs").json() == [], "nothing may be queued"

    confirmed = client.post(route, json={**body, "confirm": True})
    assert confirmed.status_code == 200, confirmed.text
    assert len(client.get("/api/jobs").json()) == len(confirmed.json()["queued"])


def test_a_card_s_concepts_wait_for_confirmation(client):
    folder = client.post("/api/projects/trial/world",
                         json={"label": "Characters", "icon": "character"}).json()["folder"]
    client.post(f"/api/projects/trial/documents?folder={folder}",
                json={"title": "Arachne", "template": "card"})
    route = "/api/projects/trial/world/characters/arachne/concepts"
    refused = client.post(route, json={"count": 2})
    assert refused.status_code == 402, refused.text
    assert client.get("/api/jobs").json() == []
    accepted = client.post(route, json={"count": 2, "confirm": True})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["entity"] == "arachne"


# Each route that hands a brief to an agent receives the agent choice as JSON.
# A request class declared after its route would make it a required URL
# parameter: the agent bubble would get a 422.
HANDOFFS = [
    ("/api/projects/trial/lookdev/water/handoff", "send_lookdev", {}),
    ("/api/projects/trial/showcase/icons/sword/handoff", "send_showcase", {}),
    ("/api/projects/trial/vfx/fire/handoff", "send", {}),
    ("/api/projects/trial/documents/bar/handoff?folder=design/interface",
     "send_card", {}),
    ("/api/projects/trial/world/characters/arachne/animation/handoff",
     "send_animation", {}),
    ("/api/projects/trial/document-handoff?folder=notes", "send_create",
     {"request": "a note"}),
    ("/api/skills/handoff", "send_skill", {"request": "a procedure"}),
]


@pytest.mark.parametrize("route,function,body", HANDOFFS)
def test_a_handoff_receives_the_agent_choice_as_json(
        client, monkeypatch: pytest.MonkeyPatch, route, function, body):
    received: list[dict] = []

    def hand_over(*args, **options):
        received.append(options)
        return {"session": {"id": "s1"}}

    monkeypatch.setattr(service.handoff, function, hand_over)
    response = client.post(route, json={**body, "harness": "codex", "effort": "high",
                                        "session": "tab-2"})
    assert response.status_code == 200, response.text
    assert received and received[0]["harness"] == "codex"
    assert received[0]["effort"] == "high" and received[0]["session"] == "tab-2"


def test_an_empty_prompt_answers_400(client):
    response = client.post("/api/produce/image", json={"prompt": "  "})
    assert response.status_code == 400


def test_a_confirmed_entity_is_queued(client):
    response = client.post("/api/produce/entity",
                           json={"prompt": "a hero", "name": "hero", "recipe": "trial",
                                 "confirm": True})
    assert response.status_code == 200
    assert len(response.json()["queued"]) == 1

    jobs = client.get("/api/jobs").json()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "create_entity"
    assert jobs[0]["state"] == "pending"


def test_the_sprite_styles_are_served(client):
    body = client.get("/api/sprites/styles").json()
    assert {entry["name"] for entry in body} == {"normal", "prerender", "pixel"}


def test_a_sprite_render_is_queued_without_confirmation(client, character):
    """Blender runs locally: nothing to confirm, unlike Runware."""
    asset_id = character
    response = client.post("/api/produce/sprites", json={
        "mesh": asset_id, "name": "tower", "style": "pixel", "directions": 4, "size": 64})
    assert response.status_code == 200
    jobs = client.get("/api/jobs").json()
    assert jobs[0]["kind"] == "render_sprites"


def test_an_unknown_sprite_style_answers_400(client, character):
    asset_id = character
    response = client.post("/api/produce/sprites",
                           json={"mesh": asset_id, "style": "cartoon"})
    assert response.status_code == 400


def test_a_missing_mesh_answers_404(client):
    response = client.post("/api/produce/sprites", json={"mesh": "0" * 32})
    assert response.status_code == 404


# ----------------------------------------------------------------------- poses


def test_the_reference_poses_are_served(client):
    names = {pose["name"] for pose in client.get("/api/poses").json()}
    assert "a_pose" in names
    detail = client.get("/api/poses/a_pose").json()
    assert len(detail["keypoints"]) == 18


def test_an_unknown_pose_answers_404(client):
    assert client.get("/api/poses/impossible_pose").status_code == 404


# --------------------------------------------------------------------- library


def test_the_library_can_be_browsed(client, character):
    body = client.get("/api/library/trial/tree").json()
    assert body["project"] == "trial"
    assert Path(body["root"]).exists()


def test_a_document_outside_the_library_answers_404(client):
    client.post("/api/library/trial/sync")
    response = client.get("/api/library/trial/document",
                          params={"folder": "../../..", "name": "etc/passwd"})
    assert response.status_code == 404


def test_a_sheet_is_inspected_without_writing_anything(client, tmp_path):
    sheet = tmp_path / "pack.svg"
    sheet.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">'
        '<g id="square"><rect x="4" y="4" width="16" height="16"/></g>'
        '<g id="circle"><circle cx="72" cy="12" r="8"/></g></svg>',
        encoding="utf-8")
    before = len(client.get("/api/assets").json())
    body = client.post("/api/sheet/inspect", json={"path": str(sheet)}).json()
    assert body["count"] == 2
    assert len(client.get("/api/assets").json()) == before


def test_a_missing_file_answers_404(client):
    response = client.post("/api/sheet/inspect", json={"path": "/does/not/exist.svg"})
    assert response.status_code == 404


# -------------------------------------------------------------------- contract

def test_the_job_stream_emits_sse_then_stops(client):
    """The interface listens with `EventSource`: the contract is the format and the stop.

    The generator is exercised directly rather than through `TestClient`: the
    latter never signals a disconnection, so a stream designed to last would
    never end. What is checked here is precisely that the loop stops as soon as
    the client leaves -- otherwise each closed window would leave a stream
    running on the server.
    """
    import asyncio

    from gamestudio.api.app import jobs_stream

    class DisconnectingRequest:
        """Present on the first turn, gone on the second."""

        def __init__(self) -> None:
            self.calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls > 1

    async def collect() -> list[str]:
        response = await jobs_stream(DisconnectingRequest(), None, 0.01)
        assert response.media_type == "text/event-stream"
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(collect())
    assert len(chunks) == 1, "a client that left must receive nothing more"
    assert chunks[0].startswith("data: ")
    assert "states" in json.loads(chunks[0][6:])


# ----------------------------------------------------------------------- token


def test_without_a_token_the_whole_api_is_refused(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch):
    """The wall holds: a request without a token goes no further.

    This is the protection that matters -- the terminal routes launch an
    arbitrary program, and any process on the machine can reach them unless a
    token is required.
    """
    monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
    auth.reset()
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    with service.using(service.build(settings)):
        anonymous = local_client()
        assert anonymous.get("/api/health").status_code == 403
        assert anonymous.get("/api/assets").status_code == 403
        # A route that acts, not one that reads: that is the one that matters.
        assert anonymous.post("/api/terminal/sessions", json={}).status_code == 403


def test_a_wrong_token_is_refused(client: TestClient):
    # The right token passes: without this control, the refusal would prove nothing.
    assert client.get("/api/health").status_code == 200
    assert local_client({"Authorization": "Bearer not-the-right-one"}).get(
        "/api/health").status_code == 403
    # Without a header, the token as a URL parameter is all that is presented:
    # that is how image tags and streams, which set none, get through.
    assert local_client().get("/api/health?token=not-the-right-one").status_code == 403
    assert local_client().get(f"/api/health?token={TOKEN}").status_code == 200


def test_the_token_goes_through_the_header_or_the_url(client: TestClient):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health", headers={
        "X-Gamestudio-Token": TOKEN}).status_code == 200
    assert client.get(f"/api/health?token={TOKEN}").status_code == 200


def test_a_foreign_host_is_refused(client: TestClient):
    """Against DNS rebinding: a name resolving to 127.0.0.1 is not enough.

    Without this check, a remote page could pass itself off as loopback -- and
    the token would not stop it, since the browser would carry it for the page.
    """
    assert client.get("/api/health", headers={
        "Host": "example.test"}).status_code == 403
    assert client.get("/api/health", headers={
        "Host": "127.0.0.1:7788"}).status_code == 200
    assert client.get("/api/health", headers={
        "Host": "localhost"}).status_code == 200


def test_the_cors_preflight_does_not_require_a_token(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch):
    """An `OPTIONS` request triggers no action, and never carries a token.

    Requiring it would close everything in development, where the front comes
    from Vite and the API from elsewhere: the browser then sends a preflight
    before each call, without the token, which only goes with the real request.
    """
    monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
    auth.reset()
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    with service.using(service.build(settings)):
        anonymous = local_client()
        response = anonymous.options("/api/health", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        })
        assert response.status_code != 403


def test_the_static_front_stays_reachable_without_a_token(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch):
    """The page must load before it knows the token.

    `app/dist` does not exist in the tests: the static mount is absent and the
    route falls to 404. What matters is that the wall does not refuse it -- a
    403 would keep the front from loading, hence from asking for the token.
    """
    monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
    auth.reset()
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    with service.using(service.build(settings)):
        anonymous = local_client()
        assert anonymous.get("/").status_code != 403


def test_a_terminal_stream_refuses_a_missing_token(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch):
    """The stream is the most sensitive door: it is guarded like the rest.

    The refusal comes before the handshake is accepted (code 1008), so the
    client sees a clear failure rather than a stream that cut off by itself.
    """
    from fastapi import WebSocketDisconnect

    monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
    auth.reset()
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    with service.using(service.build(settings)):
        anonymous = local_client()
        with pytest.raises(WebSocketDisconnect):
            with anonymous.websocket_connect("/api/terminal/sessions/unknown/stream"):
                pytest.fail("a stream without a token must never open")


def test_the_token_is_written_readable_by_its_owner_only(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch):
    """Without a forced token, one is drawn and written with mode 0600 in `<data>/run`."""
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    auth.reset()
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    with service.using(service.build(settings)):
        value = auth.publish()
        path = auth.token_path()
        assert len(value) >= 32
        assert path.read_text(encoding="utf-8").strip() == value
        # A permissive `umask` must not make the token readable by others.
        assert path.stat().st_mode & 0o077 == 0
        # Publishing twice returns the same: a restart disconnects nobody.
        assert auth.publish() == value
    auth.reset()


def test_a_terminal_stream_passes_the_wall_with_the_token(client: TestClient):
    """The wall must not break what it protects.

    A refusal without a token proves nothing if the token does not let through:
    a wall that closes every door reads as a success in the previous test. This
    one really opens a tab and reads its stream, token as a parameter -- the
    only way to carry one on a WebSocket.
    """
    created = client.post("/api/terminal/sessions",
                          json={"command": ["bash"], "cols": 80, "rows": 24})
    assert created.status_code == 200, created.text
    session_id = created.json()["id"]
    try:
        # The host is set explicitly: `TestClient` hard-codes `testserver` on a
        # WebSocket handshake, ignoring the client's `base_url` -- unlike HTTP
        # requests. A real browser does send the host it targets.
        with client.websocket_connect(
                f"/api/terminal/sessions/{session_id}/stream?token={TOKEN}",
                headers={"host": HOST}) as ws:
            assert ws.receive_json()["type"] == "snapshot"
    finally:
        client.delete(f"/api/terminal/sessions/{session_id}")


# --------------------------------------------------------------- agent context


def test_the_context_reads_writes_and_regenerates(client: TestClient):
    """The four context routes: read, regenerate, list, save.

    What matters here is not the briefing's shape -- `test_briefing` tests it
    -- but that the route that *rewrites* it exists: the interface calls it
    before saying an agent will see the current state.
    """
    notes = client.get("/api/context/notes")
    assert notes.status_code == 200
    assert {entry["name"] for entry in notes.json()} == {
        "identity.md", "goals.md", "preferences.md", "briefing.md"}

    written = client.put("/api/context/notes/goals.md",
                         json={"text": "# Goals\n\nKeep the context current.\n"})
    assert written.status_code == 200
    assert client.get("/api/context/notes/goals.md").json()["text"].startswith("# Goals")

    refreshed = client.post("/api/context/refresh")
    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert body["markdown"].startswith("# gamestudio briefing")
    assert Path(body["path"]).is_file()
    assert body["digest"]["counters"]["projects"] >= 0

    assert client.get("/api/context").json()["markdown"] == body["markdown"]


def test_the_briefing_is_not_edited_through_the_api(client: TestClient):
    """It is regenerated: the route refuses it, with a message that says what to do."""
    refused = client.put("/api/context/notes/briefing.md", json={"text": "by hand"})
    assert refused.status_code == 400
    assert "regenerated" in refused.json()["detail"]

    unknown = client.get("/api/context/notes/../../.env")
    assert unknown.status_code == 404


# ------------------------------------------------------------------- workspace


def test_the_workspace_shows_declared_projects_and_serves_the_report(client: TestClient):
    """A recipe declares a project before its first production: the card exists.

    This is what sets the workspace apart from the library: it shows what is
    left to do (the roster to build), not only what is done.
    """
    index = client.get("/api/workspace")
    assert index.status_code == 200
    body = index.json()
    assert [entry["project"] for entry in body["projects"]] == ["trial"]
    assert body["projects"][0]["category"] == "project"
    assert body["projects"][0]["steps"][0]["kind"] == "roster"

    pinned = client.patch("/api/workspace/trial",
                          json={"pinned": True, "description": "Demo project."})
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True
    assert client.get("/api/workspace").json()["counters"]["pinned"] == 1

    written = client.post("/api/workspace/trial/report")
    assert written.status_code == 200, written.text
    assert Path(written.json()["path"]).is_file()

    served = client.get("/api/workspace/trial/report.html")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/html")
    assert "Demo project." in served.text


def test_a_card_refuses_an_unknown_vocabulary(client: TestClient):
    refused = client.patch("/api/workspace/trial", json={"category": "something-else"})
    assert refused.status_code == 400
    assert "unknown category" in refused.json()["detail"]


# ----------------------------------------------------------------------- world


def test_the_world_axes_file_the_cards(client: TestClient):
    """The axis routes: declare a grid, file a card, detach it."""
    created = client.post("/api/projects/trial/world",
                          json={"label": "Characters", "icon": "character"})
    assert created.status_code == 200, created.text
    assert created.json()["axes"] == []

    saved = client.put("/api/projects/trial/world/characters/axes", json={"axes": [
        {"label": "Race", "values": [{"label": "Human"}, {"label": "Elf"}]},
    ]})
    assert saved.status_code == 200, saved.text
    axes = saved.json()["axes"]
    assert [axis["id"] for axis in axes] == ["race"]
    assert [value["id"] for value in axes[0]["values"]] == ["human", "elf"]

    card = client.post("/api/projects/trial/documents?folder=world/characters",
                        json={"title": "Arachne", "template": "card"})
    assert card.status_code == 200, card.text

    filed = client.put("/api/projects/trial/world/characters/arachne/axes",
                       json={"values": {"race": "elf"}})
    assert filed.status_code == 200, filed.text
    assert filed.json()["axes"] == {"race": "elf"}
    assert client.get("/api/projects/trial/entities").json()[0]["axes"] == {"race": "elf"}

    # A value outside the grid is refused, naming the known grid.
    refused = client.put("/api/projects/trial/world/characters/arachne/axes",
                         json={"values": {"race": "Orc"}})
    assert refused.status_code == 400
    assert "is not a value" in refused.json()["detail"]

    # Renaming the value detaches nobody: the id is stable.
    saved = client.put("/api/projects/trial/world/characters/axes", json={"axes": [
        {"id": "race", "label": "People", "values": [
            {"id": "human", "label": "Human"}, {"id": "elf", "label": "Dark elf"}]},
    ]})
    assert saved.json()["detached"] == 0
    assert client.get("/api/projects/trial/entities").json()[0]["axes"] == {"race": "elf"}

    # ...but removing the value detaches the card that carried it.
    saved = client.put("/api/projects/trial/world/characters/axes", json={"axes": [
        {"id": "race", "label": "People", "values": [{"id": "human", "label": "Human"}]},
    ]})
    assert saved.json()["detached"] == 1
    assert client.get("/api/projects/trial/entities").json()[0]["axes"] == {}


def test_removing_a_full_section_requires_force(client: TestClient):
    """Without `force`, a full section is refused; with it, it takes its cards along."""
    client.post("/api/projects/trial/world", json={"label": "Places", "icon": "place"})
    client.post("/api/projects/trial/documents?folder=world/places",
                json={"title": "The Tower", "template": "card"})

    refused = client.delete("/api/projects/trial/world/places")
    assert refused.status_code == 400
    assert "still holds" in refused.json()["detail"]

    gone = client.delete("/api/projects/trial/world/places?force=true")
    assert gone.status_code == 200, gone.text
    assert gone.json()["cards"] == ["the-tower"]
    assert client.get("/api/projects/trial/world").json() == []


# ---------------------------------------------------------------------- meshes


def test_the_3d_routes_and_importing_a_mesh(client: TestClient, tmp_path: Path):
    """The 3D route: the catalogue of routes, then importing a local GLB."""
    providers = client.get("/api/mesh/providers")
    assert providers.status_code == 200
    body = providers.json()
    assert body[0]["id"] == "local" and body[0]["paid"] is False
    assert body[1]["id"] == "runware" and body[1]["paid"] is True

    # A minimal GLB: the import does not read the geometry, Blender will.
    document = json.dumps({"asset": {"version": "2.0"}, "scenes": [{"nodes": []}],
                           "nodes": []}).encode()
    document += b" " * ((4 - len(document) % 4) % 4)
    glb = tmp_path / "model.glb"
    glb.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(document))
                    + struct.pack("<II", len(document), 0x4E4F534A) + document)

    imported = client.post("/api/import/mesh", json={
        "path": str(glb), "project": "trial", "name": "Arachne"})
    assert imported.status_code == 200, imported.text
    result = imported.json()
    assert result["entity"] == "arachne"
    assert result["state"] == "needs_review"
    assert Path(result["path"]).is_file()
    folder = Path(result["library"])
    assert folder.name == "arachne" and folder.parent.name == "3d"
    assert (folder / "arachne.glb").is_file()

    # The mesh is now in the project's library, so visible.
    tree = client.get("/api/library/trial/tree?folder=3d/arachne")
    assert tree.status_code == 200
    assert [file["name"] for file in tree.json()["files"]] == ["arachne.glb"]

    # Overwriting an existing mesh takes an explicit gesture.
    again = client.post("/api/import/mesh", json={
        "path": str(glb), "project": "trial", "name": "Arachne"})
    assert again.status_code == 400
    assert "replace=true" in again.json()["detail"]


# ------------------------------------------------------------------- documents


def test_a_project_s_documents_are_read_and_written(client: TestClient):
    """The document routes: list, create, read, write, delete."""
    assert client.get("/api/projects/trial/documents").json() == []

    templates = client.get("/api/documents/templates").json()
    assert [entry["id"] for entry in templates][:2] == ["blank", "character"]

    created = client.post("/api/projects/trial/documents",
                          json={"title": "Hero bible", "template": "character"})
    assert created.status_code == 200, created.text
    assert created.json()["name"] == "hero-bible"
    assert "## Appearance" in created.json()["text"]

    # The same title twice must not overwrite the first.
    again = client.post("/api/projects/trial/documents",
                        json={"title": "Hero bible", "template": "blank"})
    assert again.status_code == 400

    written = client.put("/api/projects/trial/documents/hero-bible",
                         json={"text": "# Hero bible\n\nHe wants to go home.\n"})
    assert written.status_code == 200
    read = client.get("/api/projects/trial/documents/hero-bible").json()
    assert "He wants to go home." in read["text"]

    listed = client.get("/api/projects/trial/documents").json()
    assert [(entry["name"], entry["title"]) for entry in listed] == [
        ("hero-bible", "Hero bible")]

    # A name that is not an id cannot leave the folder.
    assert client.get("/api/projects/trial/documents/..%2F..%2F.env").status_code in (400, 404)

    removed = client.delete("/api/projects/trial/documents/hero-bible")
    assert removed.status_code == 200
    assert client.get("/api/projects/trial/documents").json() == []


def test_the_world_and_its_cards_go_through_the_routes(client: TestClient):
    """A section is declared, its cards live on its shelf, not at the root."""
    assert client.get("/api/projects/trial/world").json() == []
    created = client.post("/api/projects/trial/world",
                          json={"label": "Characters", "icon": "character"})
    assert created.status_code == 200, created.text
    folder = created.json()["folder"]

    card = client.post(f"/api/projects/trial/documents?folder={folder}",
                        json={"title": "Arachne", "template": "card"})
    assert card.status_code == 200, card.text
    assert client.get("/api/projects/trial/documents").json() == []
    shelf = client.get(f"/api/projects/trial/documents?folder={folder}").json()
    assert [entry["name"] for entry in shelf] == ["arachne"]

    # Full, the section resists; empty, it goes.
    assert client.delete("/api/projects/trial/world/characters").status_code == 400
    client.delete(f"/api/projects/trial/documents/arachne?folder={folder}")
    assert client.delete("/api/projects/trial/world/characters").status_code == 200


def test_the_workspace_logo_is_set_and_served(client: TestClient):
    assert client.get("/api/workspace/trial/logo").status_code == 404
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    posed = client.put("/api/workspace/trial/logo",
                       files={"file": ("logo.png", png, "image/png")})
    assert posed.status_code == 200, posed.text
    assert posed.json()["logo"]["version"] > 0
    served = client.get("/api/workspace/trial/logo")
    assert served.status_code == 200
    assert served.content == png

    refused = client.put("/api/workspace/trial/logo",
                         files={"file": ("x.png", b"not an image", "image/png")})
    assert refused.status_code == 400
    assert client.delete("/api/workspace/trial/logo").json()["logo"] is None


# ------------------------------------------------------ skills and connections


def test_the_skill_and_connection_routes(client: TestClient, tmp_path: Path):
    """The connections panel serves what it read, and the index is regenerated."""
    connections = client.get("/api/connections")
    assert connections.status_code == 200
    body = connections.json()
    assert [source["id"] for source in body["sources"]] == [
        "project", "claude", "codex-project", "gemini-project", "codex", "gemini", "kimi"]
    assert body["counts"]["project"] >= 0
    assert body["notes"]

    # The test repository has no skills: the check says so instead of lying.
    check = client.get("/api/skills/check")
    assert check.status_code == 200
    assert check.json()["ok"] is False
    assert check.json()["skills"] == 0

    assert client.get("/api/skills").json() == []
    assert client.get("/api/skills/missing").status_code == 404


def test_a_procedure_is_requested_from_an_agent(client: TestClient):
    """The user says what they want; the brief carries the request and a skill's shape."""

    too_short = client.post("/api/skills/brief", json={"request": "a skill"})
    assert too_short.status_code == 400

    request = "Take a badly split sheet back, element by element."
    brief = client.post("/api/skills/brief", json={"request": request})
    assert brief.status_code == 200, brief.text
    content = brief.json()
    assert Path(content["path"]).is_file()
    assert Path(content["path"]).parent.name == "briefs"
    assert f"> {request}" in content["text"]
    assert "## When to follow it" in content["text"]  # the expected shape
    assert "write_skill" in content["text"]
    assert content["path"] in content["prompt"]


def test_adding_a_procedure_through_the_api(client: TestClient):
    """Creation writes the index and the mirror, a mistake is a 400."""

    created = client.post("/api/skills", json={
        "name": "Colour review",
        "description": "Check a palette before delivering a sheet.",
    })
    assert created.status_code == 200, created.text
    assert created.json()["name"] == "colour-review"
    assert created.json()["linked"] is True

    assert [entry["name"] for entry in client.get("/api/skills").json()] == [
        "colour-review"]
    assert client.get("/api/skills/check").json()["ok"] is True
    assert client.get("/api/skills/colour-review").json()["text"].startswith("---")

    # A taken name, a too-short description: the caller's mistake becomes a
    # code, not a stack trace.
    assert client.post("/api/skills", json={
        "name": "Colour review",
        "description": "Check a palette, a second time.",
    }).status_code == 400
    assert client.post("/api/skills", json={
        "name": "forge", "description": "Forge.",
    }).status_code == 400


# ----------------------------------------------------------- workbench prompts


def test_the_workbench_prompts_are_served(client: TestClient):
    """The catalogue, then a prompt composed for a subject."""
    entries = client.get("/api/prompts").json()
    assert [entry["id"] for entry in entries][:3] == ["apose", "tpose", "profile"]

    rendered = client.get("/api/prompts/apose",
                          params={"subject": "an old knight",
                                  "style_prefix": "ink"}).json()
    assert rendered["positive"].startswith("ink, an old knight, A-pose")
    assert rendered["pose"] == "a_pose"
    assert (rendered["width"], rendered["height"]) == (768, 1152)

    assert client.get("/api/prompts/unknown", params={"subject": "x"}).status_code == 404


# ------------------------------------------------------------------- diagnosis


def test_the_diagnosis_is_served_and_writes_nothing(client: TestClient):
    """The report says what is missing, with the fix -- and does not change the studio."""
    report = client.get("/api/doctor").json()
    assert report["version"]
    assert report["counts"]["checks"] == len(report["checks"])
    assert isinstance(report["ok"], bool)
    for entry in report["checks"]:
        assert entry["status"] in ("ok", "warning", "failed")
        assert entry["label"] and entry["detail"]
        if entry["status"] != "ok":
            assert entry["fix"], f"{entry['id']} reports a gap without a fix"
