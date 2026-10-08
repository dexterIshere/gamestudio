"""A game design card's workbench: its render, its sketch, its generated images.

What these tests protect: a card only shows the render the library files with
it; its sketch is written, reread, removed, follows the card when renamed and
leaves with it; an export that is not a PNG does not get in; its references are
dropped without duplicates, follow it and leave with it; a generation does not
start without consent, and one that starts carries the card, its reference and
its step all the way to the produced images. No network, no engine: Runware and
Godot are replaced when needed.
"""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import Image as McpImage
from PIL import Image

from gamestudio import mcp_server
from gamestudio.api import auth
from gamestudio.api.app import app
from gamestudio.config import Settings
from gamestudio.domain.models import JobState
from gamestudio.jobs import worker
from gamestudio.pipeline.base import Context
from gamestudio.service import build, cards, documents, folders, handoff, renders, using
from gamestudio.service.context import space
from gamestudio.service.errors import NotFound, PaymentRequired, ServiceError

PROJECT_GODOT = """config_version=5

[application]

config/name="Trial"
run/main_scene="res://ui.tscn"

[display]

window/size/viewport_width=720
window/size/viewport_height=1280
"""

UI_SCENE = """[gd_scene format=3]

[node name="Screen" type="Control"]
"""

INTERFACE = "design/interface"
KEY = f"{INTERFACE}/top-bar"

CARD = """# Top bar

<!-- What the card must say. -->

## When it shows

Always, at the **top** of the [game screen](../screen.md). It never hides.

## What it shows

- The resources
"""

# A scene as the editor saves it (`serializeAsJSON`), reduced.
SCENE = {"type": "excalidraw", "version": 2, "source": "gamestudio",
         "elements": [{"id": "stroke", "type": "freedraw", "x": 10, "y": 20,
                       "points": [[0, 0], [12.5, 3]]}],
         "appState": {"viewBackgroundColor": "#ffffff"}, "files": {}}


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    """A project opened on a game folder, with its Godot client in `client/`."""
    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    settings = Settings(data_dir=isolated_data, project_root=studio_dir,
                        context_dir=tmp_path / "context")
    root = tmp_path / "my-game"
    (root / "client").mkdir(parents=True)
    (root / "client" / "project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
    (root / "client" / "ui.tscn").write_text(UI_SCENE, encoding="utf-8")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        yield root


def _card(name: str = "top-bar", section: str = INTERFACE, text: str = CARD) -> None:
    documents.write_document("game", name, text, folder=section)


def _png(color: Any = "red", size: tuple[int, int] = (8, 6)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def _data_url(data: bytes, media: str = "image/png") -> str:
    return f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"


def _render(game: Path, name: str, *, section: str = INTERFACE, color: Any = "red",
            when: str = "2026-10-05T10:00:00+00:00", **settings: Any) -> str:
    """A render as `render_scene` saves it, without an engine.

    One color per render: the store is content-addressed, and two identical
    images would make a single asset. A setting set to `None` is left out of the
    metadata, as in older renders that did not keep it.
    """
    st = space("game")
    image = game.parent / "images" / f"{name}-{color}-{when[:10]}.png"
    image.parent.mkdir(exist_ok=True)
    image.write_bytes(_png(color))
    meta = {"role": renders.ROLE, "project": "game", "name": name, "scene": "res://ui.tscn",
            "godot": "client", "scale": 2.0, "crop": False, "setup": False, "locale": "",
            "setup_code": "", "width": 0, "height": 0, "delay": 0.5, "transparent": False,
            "rendered_at": when, "briefing": renders.briefing_tag(section)}
    asset = st.store.put_file(image, kind="image", meta={
        key: value for key, value in {**meta, **settings}.items() if value is not None})
    st.db.save_asset(asset)
    return asset.id




# ------------------------------------------------------------------- the render


def test_the_render_is_linked_by_its_name_and_section(game: Path) -> None:
    _card()
    _card("inventory")
    _card("inventory", "design/mechanics")
    _card("map")
    _card("map", "design/direction")
    _render(game, "top-bar", color="red", when="2026-10-01T10:00:00+00:00")
    latest = _render(game, "top-bar", color="blue")
    combat = _render(game, "inventory", section="design/mechanics", color="green")
    # No section, and two cards with that name: the library does not guess.
    _render(game, "map", section="", color="white")
    # A free image, more recent, takes no card's place.
    _render(game, "free-screen", section="", color="black",
            when="2026-10-06T10:00:00+00:00")

    render = cards.media("game", INTERFACE, "top-bar")["render"]
    assert render is not None and render["asset_id"] == latest, "the latest with its name"
    filed = space("game").librarian.project_dir("game") / "briefing" / INTERFACE / \
        "top-bar.png"
    assert render["path"] == str(filed) and filed.is_file(), \
        "the returned path is the library's, synced when needed"
    assert (render["scene"], render["file"]) == ("res://ui.tscn", "client/ui.tscn")
    assert (render["width"], render["height"]) == (8, 6)
    assert render["rendered_at"] == "2026-10-05T10:00:00+00:00"
    assert render["rerender"] is True

    assert cards.media("game", INTERFACE, "inventory")["render"] is None, \
        "another section's render is not its own"
    assert cards.media("game", "design/mechanics", "inventory")["render"]["asset_id"] == combat
    assert cards.media("game", INTERFACE, "map")["render"] is None
    assert cards.media("game", "design/direction", "map")["render"] is None


def test_a_render_is_redone_only_if_everything_needed_was_kept(game: Path) -> None:
    _card()
    _card("list")
    _card("old")
    # A setup of which only the boolean was kept (older renders).
    _render(game, "top-bar", setup=True, setup_code=None)
    render = cards.media("game", INTERFACE, "top-bar")["render"]
    assert render["rerender"] is False
    with pytest.raises(ServiceError, match="setup"):
        cards.rerender("game", INTERFACE, "top-bar")

    # The setup was kept: the render can be redone.
    _render(game, "list", color="blue", setup=True, setup_code="scene.open_page(1)")
    assert cards.media("game", INTERFACE, "list")["render"]["rerender"] is True

    # A scene gone from the game no longer resolves, and is not redone.
    _render(game, "old", color="green", scene="res://gone.tscn")
    old = cards.media("game", INTERFACE, "old")["render"]
    assert (old["file"], old["rerender"]) == ("", False)


def test_rendering_again_replays_the_last_render_settings(
        game: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _card()
    with pytest.raises(NotFound, match="no render"):
        cards.rerender("game", INTERFACE, "top-bar")
    _render(game, "top-bar", scale=1.5, width=360, height=640, delay=1.0,
            crop=True, transparent=True, locale="fr", setup=True,
            setup_code='scene.open_page("empire")')

    calls: list[dict[str, Any]] = []

    def engine(project: str, scene: str = "", **settings: Any) -> dict[str, Any]:
        calls.append({"project": project, "scene": scene, **settings})
        new = _render(game, "top-bar", color="blue", when="2026-10-06T10:00:00+00:00")
        path = space("game").librarian.project_dir("game") / "briefing" / INTERFACE / \
            "top-bar.png"
        return {"asset_id": new, "path": str(path)}

    monkeypatch.setattr(cards.renders, "render_scene", engine)
    render = cards.rerender("game", INTERFACE, "top-bar")

    assert calls == [{"project": "game", "scene": "res://ui.tscn", "name": "top-bar",
                      "folder": INTERFACE, "scale": 1.5, "width": 360, "height": 640,
                      "delay": 1.0, "crop": True, "transparent": True, "locale": "fr",
                      "setup": 'scene.open_page("empire")', "godot": "client"}]
    assert render["rendered_at"] == "2026-10-06T10:00:00+00:00"
    assert Path(render["path"]).is_file()


# ------------------------------------------------------------------ the sketch


def test_the_sketch_is_written_reread_and_removed(game: Path) -> None:
    _card()
    empty = cards.read_sketch("game", INTERFACE, "top-bar")
    assert (empty["scene"], empty["png"], empty["src"], empty["mtime"]) == (None, None, None, 0.0)
    assert cards.media("game", INTERFACE, "top-bar")["sketch"] is None

    written = cards.save_sketch("game", INTERFACE, "top-bar", SCENE, _data_url(_png()))
    shelf = documents.directory("game", INTERFACE)
    assert written["path"] == str(shelf / "top-bar.sketch.excalidraw")
    assert written["png"] == str(shelf / "top-bar.sketch.png")
    assert written["src"] == "top-bar.sketch.png" and written["mtime"] > 0
    assert Path(written["png"]).read_bytes() == _png()
    assert cards.read_sketch("game", INTERFACE, "top-bar")["scene"] == SCENE
    assert cards.media("game", INTERFACE, "top-bar")["sketch"] == written
    # Written in one go: no temporary file stays next to the card.
    assert sorted(path.name for path in shelf.iterdir()) == [
        "top-bar.md", "top-bar.sketch.excalidraw", "top-bar.sketch.png"]

    # An empty sketch has no export any more; the scene stays.
    no_export = cards.save_sketch("game", INTERFACE, "top-bar", SCENE, None)
    assert (no_export["png"], no_export["src"]) == (None, None)
    assert not (shelf / "top-bar.sketch.png").exists()
    assert cards.read_sketch("game", INTERFACE, "top-bar")["scene"] == SCENE

    # Without a scene, both go.
    cards.save_sketch("game", INTERFACE, "top-bar", SCENE, _data_url(_png()))
    removed = cards.save_sketch("game", INTERFACE, "top-bar", None, _data_url(_png()))
    assert (removed["png"], removed["mtime"]) == (None, 0.0)
    assert sorted(path.name for path in shelf.iterdir()) == ["top-bar.md"]
    assert cards.media("game", INTERFACE, "top-bar")["sketch"] is None


def test_an_export_that_is_not_a_png_is_refused(game: Path) -> None:
    _card()
    cards.save_sketch("game", INTERFACE, "top-bar", SCENE, _data_url(_png()))
    gif = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(gif, "GIF")
    for refused in (_data_url(gif.getvalue()),                    # a GIF signature
                    _data_url(_png(), "image/jpeg"),              # announces a JPEG
                    base64.b64encode(_png()).decode("ascii"),     # not a data URL
                    "data:image/png;base64,@@not base64@@",
                    _data_url(_png()[:40]),                       # truncated PNG
                    "data:image/png," + _png().decode("latin-1")):  # not base64
        with pytest.raises(ServiceError, match="sketch export"):
            cards.save_sketch("game", INTERFACE, "top-bar",
                              {**SCENE, "elements": []}, refused)
    # A refusal writes nothing: the previous sketch is intact.
    assert cards.read_sketch("game", INTERFACE, "top-bar")["scene"] == SCENE
    for scene in ([1, 2], {"elements": "stroke"}):
        with pytest.raises(ServiceError, match="invalid sketch"):
            cards.save_sketch("game", INTERFACE, "top-bar", scene, None)  # type: ignore[arg-type]


def test_an_unreadable_sketch_is_refused_rather_than_replaced(game: Path) -> None:
    _card()
    file = documents.directory("game", INTERFACE) / "top-bar.sketch.excalidraw"
    file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ServiceError, match="unreadable sketch"):
        cards.read_sketch("game", INTERFACE, "top-bar")


def test_the_sketch_export_is_served_as_a_card_image(game: Path) -> None:
    _card()
    sketch = cards.save_sketch("game", INTERFACE, "top-bar", SCENE, _data_url(_png()))
    served = documents.image_file("game", INTERFACE, sketch["src"])
    assert served == Path(sketch["png"]).resolve()


def test_renaming_or_deleting_the_card_takes_its_sketch(game: Path) -> None:
    _card()
    cards.save_sketch("game", INTERFACE, "top-bar", SCENE, _data_url(_png()))
    shelf = documents.directory("game", INTERFACE)

    renamed = documents.rename_document("game", "top-bar", "Resource bar", INTERFACE)
    assert renamed["name"] == "resource-bar"
    assert sorted(path.name for path in shelf.iterdir()) == [
        "resource-bar.md", "resource-bar.sketch.excalidraw", "resource-bar.sketch.png"]
    assert cards.read_sketch("game", INTERFACE, "resource-bar")["scene"] == SCENE

    documents.delete_document("game", "resource-bar", INTERFACE)
    assert list(shelf.iterdir()) == []


DIRECTION = "design/direction"


def test_references_are_dropped_without_duplicates_and_follow_the_card(game: Path) -> None:
    _card("intent", DIRECTION, "# Intent\n")
    red = cards.add_reference("game", DIRECTION, "intent", _png("red"), "Pink nebula.PNG")
    assert (red["file"], red["duplicate"]) == ("pink-nebula.png", False)
    assert (red["width"], red["height"]) == (8, 6)
    assert Path(red["path"]).parent.name == "intent.references"
    # The same content makes no copy; another one with the same name does not
    # overwrite it.
    assert cards.add_reference("game", DIRECTION, "intent", _png("red"), "x.png")["duplicate"]
    blue = cards.add_reference("game", DIRECTION, "intent", _png("blue"), "Pink nebula.png")
    assert blue["file"] == "pink-nebula-2.png"
    jpeg = io.BytesIO()
    Image.new("RGB", (4, 4), "green").save(jpeg, "JPEG")
    assert cards.add_reference("game", DIRECTION, "intent", jpeg.getvalue(),
                               "forest.png")["file"] == "forest.jpg", "the format decides"
    for wrong, message in ((b"", "empty"), (b"not an image", "unreadable")):
        with pytest.raises(ServiceError, match=message):
            cards.add_reference("game", DIRECTION, "intent", wrong, "wrong.png")
    media = cards.media("game", DIRECTION, "intent")
    assert [entry["file"] for entry in media["references"]] == \
        ["pink-nebula.png", "pink-nebula-2.png", "forest.jpg"]
    # A document cites it by its path, like any card image.
    assert documents.image_file("game", DIRECTION, "intent.references/forest.jpg").is_file()

    with pytest.raises(ServiceError, match="invalid"):
        cards.remove_reference("game", DIRECTION, "intent", "../intent.md")
    cards.remove_reference("game", DIRECTION, "intent", "forest.jpg")
    assert len(cards.media("game", DIRECTION, "intent")["references"]) == 2

    documents.rename_document("game", "intent", "What the game conveys", DIRECTION)
    followed = cards.media("game", DIRECTION, "what-the-game-conveys")["references"]
    assert [entry["file"] for entry in followed] == ["pink-nebula.png", "pink-nebula-2.png"]
    folder = Path(followed[0]["path"]).parent
    documents.delete_document("game", "what-the-game-conveys", DIRECTION)
    assert not folder.exists(), "the references leave with the card"


def test_an_agent_files_an_inbox_image_with_the_card(game: Path) -> None:
    _card("intent", DIRECTION, "# Intent\n")
    from gamestudio.service import inbox

    dropped = inbox.add_bytes(_png("purple"), "mood.png")
    filed = mcp_server.card_reference_add("game", DIRECTION, "intent", dropped["relative"])
    assert filed["file"].endswith(".png") and Path(filed["path"]).is_file()
    assert Path(dropped["path"]).is_file(), "the inbox keeps its file"
    with pytest.raises(ValueError, match="not found"):
        mcp_server.card_reference_add("game", DIRECTION, "intent", "inbox/missing.png")
    look = mcp_server.card_media("game", DIRECTION, "intent", look=True)
    assert [part for part in look if isinstance(part, McpImage)], "the agent sees it"

    brief = handoff.card_brief("game", DIRECTION, "intent")["text"]
    assert filed["path"] in brief

    started = cards.generate("game", DIRECTION, "intent", prompt="the mood",
                             reference=f"ref:{filed['file']}", confirm=True)
    entered = space("game").db.get_asset(started["reference_asset_id"])
    assert entered is not None and entered.meta["role"] == "reference"
    job = space("game").db.get_job(started["queued"][0])
    assert job.payload["reference_kind"] == "reference"
    with pytest.raises(NotFound):
        cards.generate("game", DIRECTION, "intent", prompt="x", reference="ref:missing.png",
                       confirm=True)


def test_a_missing_card_or_an_empty_section_is_refused(game: Path) -> None:
    _card()
    with pytest.raises(NotFound):
        cards.media("game", INTERFACE, "ghost")
    with pytest.raises(ServiceError, match="empty section"):
        cards.media("game", "", "top-bar")
    with pytest.raises(ServiceError, match="invalid shelf"):
        cards.save_sketch("game", "../..", "top-bar", SCENE, None)
    with pytest.raises(ServiceError, match="invalid document name"):
        cards.read_sketch("game", INTERFACE, "../top-bar")


# ------------------------------------------------------------------ generating


def test_generating_asks_for_consent_then_enqueues(game: Path) -> None:
    _card()
    st = space("game")
    # What can be refused is refused before consent: the question is not wasted.
    for wrong, message in (({"prompt": "  "}, "empty prompt"),
                           ({"count": 0}, "count"), ({"count": 5}, "count"),
                           ({"reference": "photo"}, "unknown reference"),
                           ({"reference": "sketch", "strength": 1.5}, "strength")):
        with pytest.raises(ServiceError, match=message) as refusal:
            cards.generate("game", INTERFACE, "top-bar", **{"prompt": "the bar", **wrong})
        assert not isinstance(refusal.value, PaymentRequired)
    with pytest.raises(NotFound, match="no render"):
        cards.generate("game", INTERFACE, "top-bar", prompt="the bar",
                       reference="render", confirm=True)
    with pytest.raises(NotFound, match="has no export"):
        cards.generate("game", INTERFACE, "top-bar", prompt="the bar",
                       reference="sketch", confirm=True)
    with pytest.raises(PaymentRequired, match="confirm"):
        cards.generate("game", INTERFACE, "top-bar", prompt="the bar")
    assert st.db.list_jobs(project="game") == [], "nothing starts without consent"

    text = cards.generate("game", INTERFACE, "top-bar", prompt=" the bar ",
                          count=2, confirm=True)
    assert text["project"] == "game" and text["reference_asset_id"] is None
    job = st.db.get_job(text["queued"][0])
    assert job is not None and job.step == f"card:{KEY}"
    assert (job.payload["card"], job.payload["reference_kind"]) == (KEY, "")
    assert (job.payload["prompt"], job.payload["count"]) == ("the bar", 2)
    assert job.payload["reference"] is None
    # The opened folder received a recipe: its style applies, unless refused.
    assert job.payload["style"]["id"] == "game-style"
    no_style = cards.generate("game", INTERFACE, "top-bar", prompt="the bar",
                              style=False, confirm=True)
    assert st.db.get_job(no_style["queued"][0]).payload["style"] is None

    cards.save_sketch("game", INTERFACE, "top-bar", SCENE, _data_url(_png("navy")))
    sketch = cards.generate("game", INTERFACE, "top-bar", prompt="the bar",
                            reference="sketch", strength=0.4, confirm=True)
    entered = st.db.get_asset(sketch["reference_asset_id"])
    assert entered is not None and entered.meta == {"role": "sketch", "project": "game",
                                                    "card": KEY}
    job = st.db.get_job(sketch["queued"][0])
    assert job.payload["reference"] == entered.id and job.payload["strength"] == 0.4
    assert job.payload["reference_kind"] == "sketch"
    # The same sketch makes a single asset.
    again = cards.generate("game", INTERFACE, "top-bar", prompt="the bar",
                           reference="sketch", confirm=True)
    assert again["reference_asset_id"] == entered.id

    render = _render(game, "top-bar")
    from_the_game = cards.generate("game", INTERFACE, "top-bar", prompt="the bar",
                                   reference="render", confirm=True)
    assert from_the_game["reference_asset_id"] == render
    job = st.db.get_job(from_the_game["queued"][0])
    assert (job.payload["reference"], job.payload["reference_kind"]) == (render, "render")


def test_waiting_counts_images_and_failures_show_for_a_day(game: Path) -> None:
    _card()
    st = space("game")
    cards.generate("game", INTERFACE, "top-bar", prompt="the bar", count=3, confirm=True)
    cards.generate("game", INTERFACE, "top-bar", prompt="at night", count=2, confirm=True)
    # Another card adds nothing to this one's waiting.
    _card("inventory")
    cards.generate("game", INTERFACE, "inventory", prompt="the bag", confirm=True)
    assert cards.media("game", INTERFACE, "top-bar")["pending"] == 5

    # Claimed by a worker, a job still awaits its images.
    claimed = st.queue.claim()
    assert claimed is not None and claimed.payload["count"] == 3
    assert cards.media("game", INTERFACE, "top-bar")["pending"] == 5

    # Failed for good, it leaves the waiting and shows.
    claimed.state = JobState.FAILED
    claimed.error = "RunwareError: out of credit"
    st.db.save_job(claimed)
    media = cards.media("game", INTERFACE, "top-bar")
    assert media["pending"] == 2
    assert [(failure["job"], failure["error"]) for failure in media["failures"]] == \
        [(claimed.id, "RunwareError: out of credit")]
    assert datetime.fromisoformat(media["failures"][0]["at"]) > \
        datetime.now(UTC) - timedelta(minutes=5)

    # Three at most, newest first; older than a day, nothing.
    now = datetime.now(UTC)
    failures = []
    for hours in (30, 4, 2, 3, 1):
        job = st.queue.enqueue("generate_image", {"count": 1}, project="game",
                               step=f"card:{KEY}")
        job.state, job.error = JobState.FAILED, f"failed {hours} h ago"
        st.db.save_job(job)
        st.db.connect().execute("UPDATE jobs SET updated_at = ? WHERE id = ?",
                                ((now - timedelta(hours=hours)).isoformat(), job.id))
        failures.append(job.id)
    seen = cards.media("game", INTERFACE, "top-bar")["failures"]
    assert [failure["job"] for failure in seen] == [claimed.id, failures[4], failures[2]]
    assert [failure["error"] for failure in seen][1:] == ["failed 1 h ago", "failed 2 h ago"]


class FakeRunware:
    """Runware without network: each request returns its images, one color each."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def run(self, _task: str, params: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(params)
        return {"cost": 0.0, "images": [{"url": f"fake://{index}", "uuid": f"u{index}"}
                                        for index in range(params["numberResults"])]}

    def files(self, result: dict[str, Any]) -> list[dict[str, str]]:
        return result["images"]

    def download(self, url: str, dest: Path) -> Path:
        index = int(url.rsplit("/", 1)[1])
        Image.new("RGB", (16, 24), (60 * index, 90, 160)).save(dest)
        return dest

    def close(self) -> None:
        pass


def test_produced_images_carry_their_card_to_the_workbench(game: Path) -> None:
    _card()
    st = space("game")
    _render(game, "top-bar")
    cards.save_sketch("game", INTERFACE, "top-bar", SCENE, _data_url(_png("navy")))
    queued = cards.generate("game", INTERFACE, "top-bar", prompt="the bar, cleaned up",
                            reference="sketch", strength=0.5, count=2, confirm=True)
    job = st.queue.claim()
    assert job is not None and job.id == queued["queued"][0]
    assert cards.media("game", INTERFACE, "top-bar")["pending"] == 2

    runware = FakeRunware()
    with Context(project="game", settings=st.settings, db=st.db, store=st.store) as ctx:
        ctx._runware = runware  # type: ignore[assignment]
        result = worker.handle_generate_image(job, ctx)
    st.queue.complete(job, result)

    assert runware.requests[0]["seedImage"].startswith("data:image/png;base64,")
    assert runware.requests[0]["strength"] == 0.5
    media = cards.media("game", INTERFACE, "top-bar")
    assert media["pending"] == 0
    assert sorted(image["asset_id"] for image in media["generations"]) == \
        sorted(result["assets"])
    for image in media["generations"]:
        assert image["reference"] == "sketch"
        assert image["model"] == "runware:101@1"
        assert "the bar, cleaned up" in image["prompt"]
        assert (image["width"], image["height"]) == (16, 24)
        assert image["path"] is not None and Path(image["path"]).is_file()
        meta = st.db.get_asset(image["asset_id"]).meta
        assert (meta["card"], meta["reference"]) == (KEY, queued["reference_asset_id"])
    # The post-production sync leaves the card's render in its place.
    filed = st.librarian.project_dir("game") / "briefing" / INTERFACE / "top-bar.png"
    assert filed.is_file()


# --------------------------------------------------------- the prompt seed


def test_the_prompt_seed_says_what_the_card_says_first(game: Path) -> None:
    template = documents.TEMPLATES["mood"]["body"].format(title="Intent", date="")
    assert cards.prompt_seed(template, "Intent") == "Intent", \
        "a labelled bullet without a value says nothing"
    _card()
    assert cards.media("game", INTERFACE, "top-bar")["prompt_seed"] == \
        "Top bar. Always, at the top of the game screen."

    bullets = """# Inventory

![Inventory](../../../library/renders/inventory.png)

- **Resources**: `metal`, crystal
- **When**: <!-- to fill in -->
- [ ] Population
- Credits.
- Energy
- Fifth
"""
    assert cards.prompt_seed(bullets, "Inventory") == \
        "Inventory. Resources: metal, crystal; Population; Credits; Energy"

    announcing = "# End\n\nIt shows:\n\n- the score\n- the time\n\n## Next\n\nNothing.\n"
    assert cards.prompt_seed(announcing, "End") == "End. It shows: the score; the time"

    code = ("# Glow\n\n```gdscript\nshader_type canvas_item;\n```\n\n"
            "The `top_bar` node pulses, in *snake_case* or not. Then.\n")
    assert cards.prompt_seed(code, "Glow") == \
        "Glow. The top_bar node pulses, in snake_case or not."

    template = ("# Fireball\n\n## Intent\n\n<!-- What the player feels. -->\n\n"
                "## Trigger\n\n- **When**: <!-- a spell -->\n- **On what**:\n\n"
                "---\n\n| a | b |\n|---|---|\n")
    assert cards.prompt_seed(template, "Fireball") == "Fireball"

    long = "# Long\n\n" + " ".join(["word"] * 200) + "\n"
    seed = cards.prompt_seed(long, "Long")
    assert len(seed) <= cards.SEED_LENGTH and seed.endswith("…")
    assert seed.startswith("Long. word word")


def test_the_section_is_named_like_the_rail(game: Path) -> None:
    """The label is shown as is -- a discussion tab is called "Mechanics ·
    Combat": the rail's label (`app/src/App.tsx`)."""
    _card()
    _card("combat", "design/mechanics")
    _card("an-idea", "ideas")
    _card("arachne", "world/characters")
    _card("draft", "scratch/drafts")
    assert cards.media("game", INTERFACE, "top-bar")["section_label"] == "Interface"
    assert cards.media("game", "design/mechanics", "combat")["section_label"] == "Mechanics"
    assert cards.media("game", "ideas", "an-idea")["section_label"] == "Ideas"
    from gamestudio.service import world

    world.create_section("game", "Characters", "character")
    assert cards.media("game", "world/characters", "arachne")["section_label"] == "Characters"
    assert cards.media("game", "scratch/drafts", "draft")["section_label"] == "drafts"


# ------------------------------------------------------- interfaces: HTTP, MCP


def test_the_workbench_routes(game: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(auth.TOKEN_ENV, "test-token")
    auth.reset()
    client = TestClient(app, base_url="http://127.0.0.1:7788",
                        headers={"Authorization": "Bearer test-token"})
    _card()
    base = "/api/projects/game/documents/top-bar"
    section = {"folder": INTERFACE}

    media = client.get(f"{base}/media", params=section).json()
    assert set(media) == {"project", "folder", "name", "title", "section_label", "render",
                          "sketch", "references", "generations", "pending", "failures",
                          "prompt_seed"}
    assert media["sketch"] is None and media["failures"] == []

    written = client.put(f"{base}/sketch", params=section,
                         json={"scene": SCENE, "png": _data_url(_png())})
    assert written.status_code == 200 and written.json()["src"] == "top-bar.sketch.png"
    assert client.get(f"{base}/sketch", params=section).json()["scene"] == SCENE
    image = client.get("/api/projects/game/document-image",
                       params={**section, "src": written.json()["src"]})
    assert image.status_code == 200 and image.content == _png()
    refused = client.put(f"{base}/sketch", params=section,
                         json={"scene": SCENE, "png": "data:image/png;base64,AAAA"})
    assert refused.status_code == 400

    paid = {"prompt": "the bar", "model": "runware:101@1", "reference": "sketch",
            "strength": 0.5, "width": 768, "height": 1344, "count": 1}
    assert client.post(f"{base}/generate", params=section,
                       json={**paid, "confirm": False}).status_code == 402
    assert space("game").db.list_jobs(project="game") == [], "nothing starts without consent"
    accepted = client.post(f"{base}/generate", params=section, json={**paid, "confirm": True})
    assert accepted.status_code == 200 and accepted.json()["reference_asset_id"]

    assert client.post(f"{base}/render", params=section, json={}).status_code == 404
    assert client.get(f"{base}/media", params={"folder": ""}).status_code == 400
    assert client.get(f"{base}/media").status_code == 422, "the section is required"
    dropped = client.post(f"{base}/references", params=section,
                          files={"file": ("mood.png", _png("teal"), "image/png")})
    assert dropped.status_code == 200 and dropped.json()["file"] == "mood.png"
    assert client.get(f"{base}/media", params=section).json()["references"][0]["file"] == \
        "mood.png"
    assert client.post(f"{base}/references", params=section,
                       files={"file": ("x.png", b"nothing", "image/png")}).status_code == 400
    removed = client.delete(f"{base}/references/mood.png", params=section)
    assert removed.status_code == 200 and removed.json()["deleted"] is True
    assert client.delete(f"{base}/references/mood.png", params=section).status_code == 404
    cleared = client.put(f"{base}/sketch", params=section, json={"scene": None, "png": None})
    assert cleared.status_code == 200 and cleared.json()["png"] is None


def test_an_agent_looks_at_the_render_and_the_sketch(game: Path) -> None:
    _card()
    # Nothing to show: a single line says so, and no image.
    [_, nothing] = mcp_server.card_media("game", INTERFACE, "top-bar", look=True)
    assert isinstance(nothing, str) and nothing
    _render(game, "top-bar")
    cards.save_sketch("game", INTERFACE, "top-bar", SCENE, _data_url(_png("navy", (40, 30))))

    without_look = mcp_server.card_media("game", INTERFACE, "top-bar")
    assert isinstance(without_look, dict) and without_look["sketch"]["png"]
    look = mcp_server.card_media("game", INTERFACE, "top-bar", look=True)
    views = [part for part in look if isinstance(part, McpImage)]
    assert len(views) == 2, "the render, then the sketch"
    with Image.open(io.BytesIO(views[1].data)) as sketch:
        assert sketch.size == (40, 30)
    assert json.loads(json.dumps(look[0]))["name"] == "top-bar"
