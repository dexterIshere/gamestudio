"""Handing work to an agent: a VFX concept, an entity's rig.

What these tests protect: the brief is standalone (target, procedure, copied
source), nothing goes to what cannot receive it, and the request does reach the
agent -- as an argument of a new discussion, or typed into an open one.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio import terminal
from gamestudio.config import Settings
from gamestudio.service import build, documents, folders, handoff, using
from gamestudio.service.context import Studio
from gamestudio.service.errors import ServiceError
from gamestudio.terminal import harnesses
from gamestudio.terminal.session import manager


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path, monkeypatch: pytest.MonkeyPatch
         ) -> Iterator[tuple[Studio, Path]]:
    """A project open on a Godot folder, and fake agents."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "kimi"):
        script = bin_dir / name
        script.write_text("#!/bin/sh\nread line\nsleep 5\n")
        script.chmod(0o755)
    monkeypatch.setattr(harnesses, "search_path", lambda env=None: str(bin_dir))
    monkeypatch.setattr(terminal.session, "search_path", lambda env=None: str(bin_dir))
    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    settings = Settings(data_dir=isolated_data, project_root=studio_dir,
                        context_dir=tmp_path / "context")
    root = tmp_path / "my-game"
    root.mkdir()
    with using(build(settings)) as current:
        folders.open_folder(str(root), "game")
        documents.create_document("game", "Fireball", "vfx", handoff.SHELF)
        yield current, root


def test_the_brief_is_standalone(game: tuple[Studio, Path]) -> None:
    _, root = game
    (root / "project.godot").write_text("[application]\n", encoding="utf-8")
    sent = handoff.brief("game", "fireball")

    assert sent["scene"] == "res://vfx/fireball/fireball.tscn"
    assert sent["godot_ready"] is True
    text = Path(sent["path"]).read_text(encoding="utf-8")
    concept = documents.read_document("game", "fireball", handoff.SHELF)["text"]
    assert concept in text, "the concept is copied into the brief"
    assert "validate-godot" in text
    assert str(root) in text
    # A single line goes to the terminal: it points to the brief.
    assert "\n" not in sent["prompt"]
    assert sent["path"] in sent["prompt"]


def test_nothing_goes_without_a_godot_project(game: tuple[Studio, Path]) -> None:
    with pytest.raises(ServiceError, match="no Godot project"):
        handoff.send("game", "fireball")


def test_a_new_discussion_gets_the_request_as_an_argument(game: tuple[Studio, Path]) -> None:
    _, root = game
    (root / "project.godot").write_text("[application]\n", encoding="utf-8")
    sent = handoff.send("game", "fireball", harness="claude")
    try:
        tab = sent["session"]
        assert tab["cwd"] == str(root), "the discussion opens in the game folder"
        argv = tab["argv"]
        # The message comes before `--mcp-config`, which would swallow what follows it.
        assert sent["prompt"] in argv
        if "--mcp-config" in argv:
            assert argv.index(sent["prompt"]) < argv.index("--mcp-config")
        assert tab["title"] == "VFX · Fireball"
    finally:
        manager.close(sent["session"]["id"])


def test_an_open_discussion_gets_the_request_typed(
        game: tuple[Studio, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _, root = game
    (root / "project.godot").write_text("[application]\n", encoding="utf-8")
    tab = manager.create(loop=None, harness="kimi", cwd=str(root))
    received: list[bytes] = []
    monkeypatch.setattr(tab, "write", received.append)
    try:
        sent = handoff.send("game", "fireball", session=tab.id)
        for _ in range(50):
            if len(received) == 2:
                break
            time.sleep(0.05)
        assert received == [sent["prompt"].encode("utf-8"), b"\r"]
    finally:
        manager.close(tab.id)


# ---------------------------------------------------------- rig and animation

CARD = """\
# Golem

## In short

A stone guardian.

## In the game

- **Expected animations**: idle, walk, slam
"""


def _glb(tmp: Path) -> Path:
    """A minimal GLB: the import reads only its JSON."""
    import json
    import struct

    payload = json.dumps({"asset": {"version": "2.0"}, "nodes": []}).encode()
    payload += b" " * ((4 - len(payload) % 4) % 4)
    path = tmp / "golem.glb"
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 20 + len(payload))
                     + struct.pack("<II", len(payload), 0x4E4F534A) + payload)
    return path


@pytest.fixture
def golem(game: tuple[Studio, Path]) -> tuple[Studio, Path, str]:
    """A world card, with nothing produced yet."""
    from gamestudio.service import world

    current, root = game
    section = world.create_section("game", "Creatures", "creature")
    documents.create_document("game", "Golem", "card", section["folder"])
    documents.write_document("game", "golem", CARD, folder=section["folder"])
    return current, root, section["id"]


def test_nothing_to_animate_without_a_mesh(golem: tuple[Studio, Path, str]) -> None:
    _, _, section = golem
    with pytest.raises(ServiceError, match="no mesh to rig"):
        handoff.animation_brief("game", section, "golem")


def test_the_3d_animation_brief_is_standalone(golem: tuple[Studio, Path, str],
                                              tmp_path: Path) -> None:
    from gamestudio.service import meshes

    _, _, section = golem
    meshes.import_mesh(str(_glb(tmp_path)), project="game", name="golem")
    sent = handoff.animation_brief("game", section, "golem")

    text = Path(sent["path"]).read_text(encoding="utf-8")
    assert "slam" in text, "the card is copied into the brief"
    assert "3d/golem/golem.glb" in text, "the mesh is cited through the library"
    assert "entity_attach_mesh" in text and "replace=true" in text
    assert "NLA_TRACKS" in text
    assert "render_sprites" in text
    assert "Never animation by" in text
    assert "## Rig and animations" in text, "the report goes into the card"
    assert 'folder="devlog"' in text, "the animated model gate goes through the devlog"
    assert "\n" not in sent["prompt"] and sent["path"] in sent["prompt"]
    # A mesh that arrives already rigged has no bare mesh: the brief says so.
    assert "no rig: the mesh is bare" in text


def test_the_entity_goes_to_a_tab_in_the_game_folder(
        golem: tuple[Studio, Path, str], tmp_path: Path) -> None:
    from gamestudio.service import meshes

    _, root, section = golem
    meshes.import_mesh(str(_glb(tmp_path)), project="game", name="golem")
    sent = handoff.send_animation("game", section, "golem", harness="claude")
    try:
        tab = sent["session"]
        assert tab["cwd"] == str(root)
        assert tab["title"] == "Anim · Golem"
        assert sent["prompt"] in tab["argv"]
        # Nobody measures the tab yet: it starts at a comfortable grid.
        assert (tab["cols"], tab["rows"]) == (handoff.WATCH_COLS, handoff.WATCH_ROWS)
    finally:
        manager.close(sent["session"]["id"])
