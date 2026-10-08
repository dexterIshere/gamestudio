"""MCP connections: read, located, and never modified.

What these tests protect: the panel must tell the truth about what is plugged
in, including when a server does not start, and it must write **nothing** --
neither in the repository nor in an agent's global configuration.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.service import build, connections, using
from gamestudio.service.context import Studio


@pytest.fixture
def isolated_studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Studio]:
    # The agents' global configuration belongs to the machine: a test never
    # reads it, or its verdict would change from one machine to another. A test
    # that wants one writes it itself.
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context")
    with using(build(settings)) as current:
        yield current


def _source(data: dict, source_id: str) -> dict:
    return next(entry for entry in data["sources"] if entry["id"] == source_id)


def test_a_project_connection_resolves_from_the_repository(isolated_studio: Studio,
                                                           tmp_path: Path) -> None:
    """A relative path resolves against the repository: that is what the agent does."""
    binary = tmp_path / ".venv" / "bin" / "gamestudio"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "gamestudio": {"command": ".venv/bin/gamestudio", "args": ["mcp"]}}}))

    data = connections.connections()
    project = _source(data, "project")
    assert project["exists"] is True
    assert len(project["servers"]) == 1
    server = project["servers"][0]
    assert server["name"] == "gamestudio"
    assert server["transport"] == "stdio"
    assert server["available"] is True
    assert server["studio"] is True
    assert data["declared_here"] is True
    assert data["counts"] == {"project": 1, "global": 0, "available": 1, "unavailable": 0}


def test_a_missing_server_is_reported_missing(isolated_studio: Studio,
                                              tmp_path: Path) -> None:
    """The real question: why does this one not answer."""
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "ghost": {"command": ".venv/bin/not-there", "args": []}}}))

    data = connections.connections()
    server = _source(data, "project")["servers"][0]
    assert server["available"] is False
    assert "not found" in server["reason"]
    assert data["counts"]["unavailable"] == 1
    assert data["declared_here"] is False


def test_a_missing_or_unreadable_file_breaks_nothing(isolated_studio: Studio,
                                                     tmp_path: Path) -> None:
    """A machine without a global connection is a normal state, not a failure."""
    data = connections.connections()
    project = _source(data, "project")
    assert project["exists"] is False
    assert project["servers"] == []
    assert project["error"] == ""
    assert _source(data, "claude")["error"] == ""

    (tmp_path / ".mcp.json").write_text("{ this is not json")
    broken = _source(connections.connections(), "project")
    assert "unreadable" in broken["error"]
    assert broken["servers"] == []


def test_a_remote_connection_has_nothing_to_resolve(isolated_studio: Studio,
                                                    tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "remote": {"url": "https://example.test/mcp"}}}))

    server = _source(connections.connections(), "project")["servers"][0]
    assert server["transport"] == "remote"
    assert server["available"] is True
    assert server["url"].startswith("https://")


def test_the_panel_changes_nothing(isolated_studio: Studio, tmp_path: Path) -> None:
    """Read-only: neither the repository nor an agent's configuration."""
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
    before = (tmp_path / ".mcp.json").read_bytes()
    listing = sorted(path.name for path in tmp_path.iterdir())

    connections.connections()

    assert (tmp_path / ".mcp.json").read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == listing


def test_variables_expand_as_the_agent_does(isolated_studio: Studio,
                                            tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """`${HOME}` in `.mcp.json`: the declaration stays portable across machines."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MISSING_SERVER", raising=False)
    script = tmp_path / "godot-mcp" / "index.js"
    script.parent.mkdir()
    script.write_text("")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "present": {"command": "sh", "args": ["${HOME}/godot-mcp/index.js"]},
        "absent": {"command": "sh",
                   "args": ["${MISSING_SERVER:-/nowhere}/index.js"]}}}))

    servers = {server["name"]: server
               for server in _source(connections.connections(), "project")["servers"]}
    # Expanded to know whether the server starts, the declaration is returned as
    # written.
    assert servers["present"]["args"] == ["${HOME}/godot-mcp/index.js"]
    assert servers["present"]["available"] is True
    # `sh` exists: the missing script is what says the server is not installed.
    assert servers["absent"]["available"] is False
    assert "${MISSING_SERVER:-/nowhere}/index.js" in servers["absent"]["reason"]


def test_a_key_cited_by_a_declaration_does_not_leak(isolated_studio: Studio,
                                                    tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """`${VAR}` often cites a key: the panel, the API and the diagnosis never see it."""
    monkeypatch.setenv("SECRET_KEY", "sk-do-not-show")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "remote": {"url": "https://example.test/mcp?key=${SECRET_KEY}"},
        "local": {"command": "sh", "args": ["--token", "${SECRET_KEY}"],
                  "env": {"TOKEN": "${SECRET_KEY}"}}}}))

    data = connections.connections()
    assert "sk-do-not-show" not in json.dumps(data)
    servers = {server["name"]: server for server in _source(data, "project")["servers"]}
    assert servers["remote"]["url"] == "https://example.test/mcp?key=${SECRET_KEY}"
    assert servers["local"]["args"] == ["--token", "${SECRET_KEY}"]
    assert servers["local"]["available"] is True


def test_blender_is_ready_only_when_the_addon_listens(isolated_studio: Studio,
                                                     tmp_path: Path) -> None:
    """Without an open Blender session, the server starts but its tools fail."""
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "blender": {"command": "sh", "env": {"BLENDER_HOST": "127.0.0.1",
                                              "BLENDER_PORT": str(port)}}}}))
    try:
        server = _source(connections.connections(), "project")["servers"][0]
    finally:
        listener.close()
    assert server["available"] is True
    assert server["companion"]["role"] == "blender"
    assert server["companion"]["ready"] is True

    server = _source(connections.connections(), "project")["servers"][0]
    assert server["available"] is True
    assert server["companion"]["ready"] is False
    assert str(port) in server["companion"]["detail"]


def test_godot_is_located_by_its_binary(isolated_studio: Studio, tmp_path: Path) -> None:
    binary = tmp_path / "godot"
    binary.write_text("")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "godot": {"command": "sh", "env": {"GODOT_PATH": str(binary)}},
        "godot-broken": {"command": "sh", "env": {"GODOT_PATH": str(tmp_path / "nothing")}},
        "other": {"command": "sh"}}}))

    servers = {server["name"]: server
               for server in _source(connections.connections(), "project")["servers"]}
    assert servers["godot"]["companion"]["ready"] is True
    assert servers["godot-broken"]["companion"]["ready"] is False
    assert servers["other"]["companion"] is None


def test_codex_ignores_an_untrusted_repository(isolated_studio: Studio, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Declaring for Codex is not enough: it must trust the repository."""
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        '[mcp_servers.godot]\ncommand = "sh"\n')

    data = connections.connections()
    codex = _source(data, "codex-project")
    assert [server["name"] for server in codex["servers"]] == ["godot"]
    assert "not trusted" in codex["warning"]
    # A repository declaration for another CLI is not a global connection.
    assert data["counts"]["global"] == 0

    (home / ".codex" / "config.toml").write_text(
        f'[projects."{tmp_path}"]\ntrust_level = "trusted"\n')
    assert _source(connections.connections(), "codex-project")["warning"] == ""
