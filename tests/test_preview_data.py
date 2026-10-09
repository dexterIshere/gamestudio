"""Preview data: the fake server a networked game's renders get, and what it lacks."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.service import build, folders, handoff, preview_data, using
from gamestudio.service.errors import ServiceError


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    root = tmp_path / "game"
    root.mkdir()
    (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    settings = Settings(data_dir=isolated_data, project_root=tmp_path / "studio",
                        context_dir=tmp_path / "context")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        yield root


def _write(game: Path, routes: list[dict], setup: str = "", enabled: bool = True) -> None:
    folder = game / ".gamestudio" / "preview"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "server.json").write_text(json.dumps({"enabled": enabled, "routes": routes}),
                                        encoding="utf-8")
    if setup:
        (folder / "setup.gd").write_text(setup, encoding="utf-8")


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as answer:
            return answer.status, json.loads(answer.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_a_game_without_data_renders_as_before(game: Path) -> None:
    with preview_data.serving("game") as preview:
        assert preview is None
    assert preview_data.state("game")["enabled"] is False


def test_the_fake_server_answers_from_the_routes(game: Path) -> None:
    _write(game, [
        {"path": "/api/universe", "body": {"galaxies": 3}},
        {"path": "/api/planet/*", "body": {"name": "Tellus"}},
        {"method": "POST", "path": "/api/**", "status": 201, "body": {"ok": True}},
    ], setup='Api.BASE_URL = "{{server}}"')

    with preview_data.serving("game") as preview:
        assert preview is not None
        assert preview.setup == f'Api.BASE_URL = "{preview.url}"', "the address is filled in"
        assert _get(f"{preview.url}/api/universe?x=1") == (200, {"galaxies": 3})
        assert _get(f"{preview.url}/api/planet/185") == (200, {"name": "Tellus"})
        status, _ = _get(f"{preview.url}/api/planet/185/scan")
        assert status == 404, "`*` is one segment"

    state = preview_data.state("game")
    assert state["enabled"] and state["misses"] == ["GET /api/planet/185/scan"]
    assert "GET /api/planet/*" in state["routes"]


def test_turned_off_the_data_is_kept_but_not_served(game: Path) -> None:
    _write(game, [{"path": "/api/universe", "body": {}}])
    preview_data.set_enabled("game", False)
    with preview_data.serving("game") as preview:
        assert preview is None
    assert preview_data.state("game")["routes"] == ["GET /api/universe"]
    _write(game, [])
    with pytest.raises(ServiceError, match="no preview data yet"):
        preview_data.set_enabled("game", True)


def test_the_brief_names_the_files_and_the_gaps(game: Path) -> None:
    _write(game, [{"path": "/api/universe", "body": {}}])
    (game / ".gamestudio" / "preview" / "misses.json").write_text('["GET /api/ranking"]',
                                                                  encoding="utf-8")
    brief = handoff.preview_brief("game")
    assert "server.json" in brief["text"] and "GET /api/ranking" in brief["text"]
    assert brief["routes"] == 1 and Path(brief["path"]).is_file()


def test_the_studio_calls_the_agent_when_a_networked_game_lacks_data(
        game: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def send(project: str, *, session: str = "", gaps: list[str] | None = None,
             **_: object) -> dict:
        calls.append(session)
        preview_data.remember_agent(project, "tab-1", gaps or [])
        return {"session": {"id": "tab-1"}}

    monkeypatch.setattr(handoff, "send_preview", send)
    preview_data.after_render("game", None)
    assert calls == [], "a game without network code needs no server"

    (game / "api.gd").write_text("extends Node\nvar http := HTTPRequest.new()\n",
                                 encoding="utf-8")
    preview_data.after_render("game", None)
    assert calls == [""], "no data: a new agent"
    assert preview_data.state("game")["networked"] == ["api.gd"]

    _write(game, [{"path": "/api/universe", "body": {}}])
    preview_data.after_render("game", preview_data.Serving(url="", setup=""))
    assert len(calls) == 1, "nothing missing: nothing to ask"

    preview_data.set_enabled("game", False)
    preview_data.after_render("game", None)
    assert len(calls) == 1, "turned off by the user: the studio stops"
