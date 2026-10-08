"""The terminal: one PTY per tab, its buffer, its subscribers.

Everything is local and offline -- the programs launched are `bash`, never the
agent, and no call goes to Runware. The harness catalog is tested on a lab
PATH, hence without depending on the tools installed on the machine running
the tests.
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from gamestudio.service.errors import ServiceError
from gamestudio.terminal import harnesses, routes, screen, transcripts
from gamestudio.terminal import session as terminal
from gamestudio.terminal.harnesses import resolve, search_path
from gamestudio.terminal.session import (
    BUFFER_BYTES,
    BUFFER_SLACK,
    MAX_COLS,
    MIN_ROWS,
    ArchivedSession,
    TerminalManager,
    absorb,
    manager,
)
from gamestudio.terminal.transcripts import Transcript


def _app() -> FastAPI:
    """The application reduced to the terminal.

    No workers, no database, no front: the terminal depends on none of them,
    and starting them here would only test noise. The error handler is taken
    from the API, though, because it is what turns a refusal into an HTTP code
    -- which the front reads.
    """
    app = FastAPI()
    app.include_router(routes.router)

    @app.exception_handler(ServiceError)
    async def _error(_request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    return app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """A client sharing a single event loop across its requests.

    The context manager is essential here: without it, each request would open
    its own loop, and the PTY's broadcast -- aimed at the loop where the session
    was born -- would no longer reach any subscriber.
    """
    with TestClient(_app()) as opened:
        yield opened


def _open(client: TestClient, *command: str) -> str:
    response = client.post("/api/terminal/sessions",
                           json={"command": list(command), "cols": 80, "rows": 24})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _until(websocket: Any, needle: bytes, limit: int = 400) -> bytes:
    """Read the stream until `needle` shows up in it.

    The terminal mixes frames: bytes for the program's output, JSON for the
    stream's events.
    """
    seen = bytearray()
    for _ in range(limit):
        message = websocket.receive()
        if message.get("type") == "websocket.close":
            break
        chunk = message.get("bytes")
        if chunk:
            seen += chunk
        if needle in seen:
            break
    return bytes(seen)


# ---------------------------------------------------------------------- buffer


def test_the_buffer_is_bounded_and_keeps_the_latest():
    buffer = bytearray()
    assert absorb(buffer, b"v" * (BUFFER_BYTES // 2)) is False
    assert absorb(buffer, b"v" * (BUFFER_BYTES // 2)) is False
    # Full, but not yet past the margin: nothing is lost.
    assert len(buffer) == BUFFER_BYTES

    assert absorb(buffer, b"n" * (BUFFER_SLACK + 1)) is True
    # Pruning brings it back to the exact capacity, and cuts from the start.
    assert len(buffer) == BUFFER_BYTES
    assert buffer.endswith(b"n")


# ------------------------------------------------------------------------- tab


def test_a_tab_returns_what_is_written_to_it(client: TestClient):
    session_id = _open(client, "bash", "-c", "read line; echo got:$line")
    try:
        with client.websocket_connect(
                f"/api/terminal/sessions/{session_id}/stream") as websocket:
            snapshot = websocket.receive_json()
            assert snapshot["type"] == "snapshot"
            assert snapshot["truncated"] is False

            websocket.send_json({"type": "input", "data": "hello\n"})
            assert b"got:hello" in _until(websocket, b"got:hello")
    finally:
        client.delete(f"/api/terminal/sessions/{session_id}")


def test_the_stream_ends_with_the_process(client: TestClient):
    session_id = _open(client, "bash", "-c", "echo done")
    try:
        with client.websocket_connect(
                f"/api/terminal/sessions/{session_id}/stream") as websocket:
            websocket.receive_json()  # the snapshot
            seen = bytearray()
            for _ in range(400):
                message = websocket.receive()
                if message.get("type") == "websocket.close":
                    break
                if message.get("bytes"):
                    seen += message["bytes"]
                elif message.get("text"):
                    assert '"exit"' in message["text"]
                    break
            assert b"done" in seen
    finally:
        client.delete(f"/api/terminal/sessions/{session_id}")


def test_an_already_finished_tab_still_reports_the_end(client: TestClient):
    """Reopening the window on a finished tab must say it is finished.

    The end sentinel was broadcast before the subscription: without the
    explicit server-side case, the stream would stay open on a queue that
    would yield nothing more -- and the interface would wait for an event that
    never comes.
    """
    session_id = _open(client, "bash", "-c", "echo done")
    try:
        session = manager.get(session_id)
        for _ in range(300):
            if session.state != "running":
                break
            time.sleep(0.01)
        assert session.state == "exited"

        with client.websocket_connect(
                f"/api/terminal/sessions/{session_id}/stream") as websocket:
            assert websocket.receive_json()["type"] == "snapshot"
            seen = bytearray()
            for _ in range(50):
                message = websocket.receive()
                if message.get("type") == "websocket.close":
                    break
                if message.get("bytes"):
                    seen += message["bytes"]
                elif message.get("text"):
                    assert '"exit"' in message["text"]
                    break
            assert b"done" in seen
    finally:
        client.delete(f"/api/terminal/sessions/{session_id}")


def test_resize_bounds_are_enforced(client: TestClient):
    session_id = _open(client, "bash", "-c", "sleep 30")
    try:
        session = manager.get(session_id)
        assert session.resize(10_000, 1) == (MAX_COLS, MIN_ROWS)
        assert session.resize(1, 10_000)[1] <= 200
    finally:
        client.delete(f"/api/terminal/sessions/{session_id}")


def test_renaming_a_tab(client: TestClient):
    session_id = _open(client, "bash", "-c", "sleep 30")
    try:
        assert manager.get(session_id).title.startswith("bash")
        response = client.patch(f"/api/terminal/sessions/{session_id}",
                                json={"title": "mine"})
        assert response.status_code == 200
        assert response.json()["title"] == "mine"
        # An empty title must not erase the one there was.
        client.patch(f"/api/terminal/sessions/{session_id}", json={"title": "  "})
        assert manager.get(session_id).title == "mine"
    finally:
        client.delete(f"/api/terminal/sessions/{session_id}")


# ---------------------------------------------------------------- clean close


def test_closing_a_tab_kills_the_child_without_a_zombie(client: TestClient):
    session_id = _open(client, "bash", "-c", "sleep 60")
    session = manager.get(session_id)
    pid = session.pid
    # The child is alive: it is indeed the one about to be killed.
    os.kill(pid, 0)

    assert client.delete(f"/api/terminal/sessions/{session_id}").status_code == 200

    # `poll` only returns a code after a successful `waitpid`: the child is dead
    # *and* reaped. A zombie would never be returned to anyone.
    assert session.proc.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert session_id not in [entry["id"] for entry in manager.listing()]


def test_an_unknown_session_is_refused(client: TestClient):
    assert client.patch("/api/terminal/sessions/missing",
                        json={"title": "x"}).status_code == 404
    assert client.delete("/api/terminal/sessions/missing").status_code == 404


def test_a_missing_program_is_clearly_refused(client: TestClient):
    response = client.post("/api/terminal/sessions",
                           json={"command": ["not-a-program-at-all"]})
    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


# ------------------------------------------------------------------------ PATH


def test_the_path_covers_the_tools_folders(tmp_path: Path):
    """Launched from the desktop, the studio does not inherit the shell's PATH.

    Without these three folders, the agents are not found when launched from a
    desktop launcher -- the tab would open for nothing.
    """
    (tmp_path / ".nvm/versions/node/v24.21.0/bin").mkdir(parents=True)
    parts = search_path({"HOME": str(tmp_path), "PATH": "/usr/bin"}).split(os.pathsep)
    assert str(tmp_path / ".local/bin") in parts
    assert str(tmp_path / ".kimi-code/bin") in parts
    assert str(tmp_path / ".nvm/versions/node/v24.21.0/bin") in parts
    # The ambient PATH is not lost for all that.
    assert "/usr/bin" in parts


def test_the_path_does_not_repeat_what_it_already_holds():
    """A folder already present must not appear twice.

    This guarantees that the first one found stays first: a PATH where
    `~/.local/bin` appeared twice would make `which` depend on `shutil`'s
    internal order, and one duplicate would be enough to hide the user's
    wrapper behind a system copy.
    """
    home = "/home/someone"
    parts = search_path({"HOME": home, "PATH": f"{home}/.local/bin:/usr/bin"}).split(os.pathsep)
    assert parts.count(f"{home}/.local/bin") == 1
    assert len(parts) == len(set(parts))


# --------------------------------------------------------------------- catalog


def test_a_missing_harness_is_reported_with_its_name():
    entry = resolve("claude", path="/no/folder/by/that/name")
    assert entry["available"] is False
    assert "claude" in entry["reason"]


def test_a_present_harness_is_available(tmp_path: Path):
    program = tmp_path / "claude"
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    entry = resolve("claude", path=str(tmp_path))
    assert entry["available"] is True
    assert entry["reason"] == ""


def test_an_unknown_harness_is_refused():
    with pytest.raises(ServiceError):
        resolve("not-a-harness-at-all")


def test_the_catalog_is_served_by_the_api(client: TestClient):
    entries = client.get("/api/terminal/harnesses").json()
    assert [entry["id"] for entry in entries] == [
        "claude", "deepseek", "mimo", "kimi", "codex"]
    for entry in entries:
        assert entry["label"] and entry["detail"]
        assert isinstance(entry["available"], bool)
        # An unavailable entry must say why: that is what the menu shows, and
        # what tells "not installed" from "nothing works".
        assert (entry["reason"] == "") is entry["available"]


# --------------------------------------------------------------------- harness


@pytest.fixture()
def fake_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                 isolated_data: Path) -> Path:
    """A lab harness: fake wrappers, in a PATH of our own.

    What is tested here is the wiring -- which command goes out, and with what
    environment -- never whether a tool is present on this machine. Data is
    isolated: a tab that stops saves its screen, which must not land in the
    machine's `data/run/terminals/`, where the Chats window would show it at
    the next launch.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "claude-deepseek"):
        script = bin_dir / name
        script.write_text("#!/bin/sh\nread line\n")
        script.chmod(0o755)
    monkeypatch.setattr(harnesses, "search_path", lambda env=None: str(bin_dir))
    monkeypatch.setattr(terminal, "search_path", lambda env=None: str(bin_dir))
    return bin_dir


def _create(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/terminal/sessions", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_tab_follows_the_requested_harness(client: TestClient, fake_harness: Path):
    body = _create(client, harness="deepseek")
    try:
        assert body["harness"] == "deepseek"
        assert body["command"] == str(fake_harness / "claude-deepseek")
        # The title stays readable: it is the program's name, not its path.
        assert body["title"].startswith("claude-deepseek")
    finally:
        client.delete(f"/api/terminal/sessions/{body['id']}")


def test_the_ambient_endpoint_is_removed_from_the_tab(
        client: TestClient, fake_harness: Path, monkeypatch: pytest.MonkeyPatch):
    """Otherwise "Claude Code" would silently be another provider.

    The environment launching the studio readily carries another provider's
    endpoint; the tab must start from a clean slate.
    """
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid/anthropic")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "another-model")
    body = _create(client, harness="claude")
    try:
        env = manager.get(body["id"])._env()
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in env
        # The rest of the environment is not carried away for all that.
        assert env["HOME"] == os.environ["HOME"]
        assert env["GAMESTUDIO_HOME"] == body["cwd"]
    finally:
        client.delete(f"/api/terminal/sessions/{body['id']}")


def test_the_studio_keys_do_not_reach_the_tab(
        client: TestClient, fake_harness: Path, monkeypatch: pytest.MonkeyPatch):
    """The studio's `.env` is in its environment; a third-party agent sees none of it.

    The MCP server the agent starts reads the keys again from the studio's
    `.env`, through `GAMESTUDIO_HOME`: the tab only needs that path.
    """
    monkeypatch.setenv("RUNWARE_API_KEY", "rw-secret")
    monkeypatch.setenv("TRIPO_API_KEY", "tsk-secret")
    monkeypatch.setenv("GAMESTUDIO_TOKEN", "token-secret")
    body = _create(client, harness="claude")
    try:
        env = manager.get(body["id"])._env()
        assert not {"RUNWARE_API_KEY", "TRIPO_API_KEY", "GAMESTUDIO_TOKEN"} & set(env)
        assert env["GAMESTUDIO_HOME"]
    finally:
        client.delete(f"/api/terminal/sessions/{body['id']}")


def test_the_screen_snapshot_is_read_under_the_lock(tmp_path: Path):
    """The reader thread writes buffer and `seq` under the lock `snapshot` takes."""
    tabs = TerminalManager()
    session = tabs.create(loop=None, command=["cat"], cwd=str(tmp_path))
    try:
        with session._lock:
            seq = session.seq
            session.write(b"hello")
            time.sleep(0.5)
            # The reader thread waits for the lock: nothing moved under our eyes.
            assert session.seq == seq
        deadline = time.monotonic() + 5
        while session.seq == seq and time.monotonic() < deadline:
            time.sleep(0.05)
        assert session.seq > seq
    finally:
        tabs.close(session.id)


def test_an_explicit_command_overrides_the_harness(client: TestClient):
    """The escape hatch for tests: `command` wins, and is not cleaned."""
    body = _create(client, command=["bash", "-c", "sleep 30"], harness="deepseek")
    try:
        assert body["command"].endswith("bash")
        assert body["harness"] == ""
    finally:
        client.delete(f"/api/terminal/sessions/{body['id']}")


# ------------------------------------------------------------------ transcript


def _when(offset: float = 0.0) -> str:
    moment = datetime.now(UTC) + timedelta(seconds=offset)
    return moment.isoformat().replace("+00:00", "Z")


def _transcript(root: Path, cwd: Path, *lines: dict[str, Any]) -> Path:
    """A lab transcript, filed the way Claude Code files its own."""
    folder = root / transcripts.slug(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "session.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines),
                    encoding="utf-8")
    return path


def _turn(reason: str, **extra: Any) -> dict[str, Any]:
    line = {"type": "assistant", "timestamp": _when(),
            "message": {"stop_reason": reason}}
    line.update(extra)
    return line


def test_the_slug_follows_claude_codes_folder_name():
    """It is the only thing that ties a tab to its transcript."""
    assert transcripts.slug(Path("/home/me/games/gamestudio")) == \
        "-home-me-games-gamestudio"
    # A dot or an underscore becomes a hyphen like the rest: Claude Code keeps
    # only alphanumerics, and case.
    assert transcripts.slug(Path("/tmp/my_project.v2")) == "-tmp-my-project-v2"


def test_the_transcript_says_the_agent_is_waiting(tmp_path: Path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    _transcript(tmp_path / "transcripts", cwd, _turn("tool_use"), _turn("end_turn"))
    tail = Transcript(cwd, 0.0, root=tmp_path / "transcripts")
    assert tail.poll() == "end_turn"


def test_a_subagent_turn_says_nothing(tmp_path: Path):
    """A subagent has its own turn ends, which are not the tab's."""
    cwd = tmp_path / "project"
    cwd.mkdir()
    _transcript(tmp_path / "transcripts", cwd,
                _turn("tool_use"), _turn("end_turn", isSidechain=True))
    tail = Transcript(cwd, 0.0, root=tmp_path / "transcripts")
    assert tail.poll() == "tool_use"


def test_another_tabs_history_is_ruled_out(tmp_path: Path):
    """Yesterday's transcript is in the same folder: it must not speak.

    A tab reopened in a project already being worked on finds the previous
    session's transcript. Without the timestamp filter, its last turn end -- an
    hour old -- would light the badge at once.
    """
    cwd = tmp_path / "project"
    cwd.mkdir()
    old = {"type": "assistant", "timestamp": _when(-3600),
           "message": {"stop_reason": "end_turn"}}
    _transcript(tmp_path / "transcripts", cwd, old, _turn("tool_use"))
    # The tab was opened a minute ago: the hour-old line is ancient history, the
    # current one is its own.
    tail = Transcript(cwd, time.time() - 60, root=tmp_path / "transcripts")
    assert tail.poll() == "tool_use"


def test_a_cut_line_is_read_again_on_the_next_poll(tmp_path: Path):
    """An agent writes its transcript in several goes: the cut is normal.

    Losing the cut line would miss the turn end one time out of two, and a
    badge that lights up one time out of two does not light up.
    """
    cwd = tmp_path / "project"
    cwd.mkdir()
    path = _transcript(tmp_path / "transcripts", cwd, _turn("tool_use"))
    tail = Transcript(cwd, 0.0, root=tmp_path / "transcripts")
    assert tail.poll() == "tool_use"

    complete = json.dumps(_turn("end_turn")).encode()
    with path.open("ab") as handle:
        handle.write(complete[: len(complete) // 2])
    assert tail.poll() is None, "a line without an end says nothing"

    with path.open("ab") as handle:
        handle.write(complete[len(complete) // 2:] + b"\n")
    assert tail.poll() == "end_turn"


def test_a_harness_without_a_transcript_says_nothing():
    """Saying nothing beats saying something false: codex and kimi have no reader.

    The front then falls back on its coarser criterion, which stays correct.
    """
    assert transcripts.for_harness("codex", Path("/tmp"), 0.0) is None
    assert transcripts.for_harness("claude", Path("/tmp"), 0.0) is not None
    # The wrappers launch the same program: they write the same transcript.
    assert transcripts.for_harness("deepseek", Path("/tmp"), 0.0) is not None


def test_typing_hands_control_back_to_the_agent(client: TestClient, fake_harness: Path):
    """Answering the agent puts it back to work, whatever its transcript said."""
    body = _create(client, harness="claude")
    try:
        session = manager.get(body["id"])
        assert session.has_journal()
        session.poll_turn()
        session.write(b"hello\n")
        assert session.turn is not None
        assert session.turn["state"] == "working"
    finally:
        client.delete(f"/api/terminal/sessions/{body['id']}")


def test_a_tab_without_a_transcript_says_nothing_of_turns(client: TestClient):
    """A `bash` has no turns: the tab must not invent a state."""
    body = _create(client, command=["bash", "-c", "sleep 30"])
    try:
        assert manager.get(body["id"]).turn is None
        assert body["turn"] is None
    finally:
        client.delete(f"/api/terminal/sessions/{body['id']}")


# ---------------------------------------------------------------- saved screen


def _body(session_id: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": session_id, "title": "claude 1", "argv": ["claude"], "harness": "claude",
        "cwd": "/tmp", "cols": 80, "rows": 24, "created_at": 1.0, "closed_at": 2.0,
        "state": "interrupted", "exit_code": None, "seq": 3, "truncated": False,
        "turn": None,
    }
    body.update(extra)
    return body


def test_a_saved_screen_reads_back_identically(tmp_path: Path, isolated_data: Path):
    """What is saved is what will be read back, byte for byte.

    A terminal buffer is not text: the escape sequences that rebuild the screen
    matter as much as the letters, and an approximate round trip would give a
    screen different from the one left.
    """
    content = b"\x1b[2J\x1b[Hhello \xe2\x94\x80 world\xff"
    screen.save(_body("abc123"), content)
    saved = isolated_data / "run" / "terminals" / "abc123.json"
    assert saved.is_file()

    loaded = screen.load_all()
    assert [body["id"] for body in loaded] == ["abc123"]
    assert loaded[0]["screen"] == content


def test_closing_a_tab_forgets_its_screen(isolated_data: Path):
    screen.save(_body("to-forget"), b"hello")
    assert screen.load_all()
    screen.forget("to-forget")
    assert screen.load_all() == []


def test_deleting_a_saved_tab_forgets_its_screen(isolated_data: Path):
    """A tab found on disk is deleted like a live tab.

    This is the most common case: the tabs one really wants to erase are those
    of the previous launch, which have no process any more. Keeping their file
    would answer `closed` to the deletion while the tab came back at the next
    start -- with no way to get rid of it.
    """
    screen.save(_body("from-yesterday"), b"hello")
    tabs = TerminalManager()
    assert [body["id"] for body in tabs.listing()] == ["from-yesterday"]

    assert tabs.close("from-yesterday")["closed"] is True
    assert tabs.listing() == []
    assert screen.load_all() == []
    # And a restart does not bring it back: that is the whole point.
    assert TerminalManager().listing() == []


def test_an_unreadable_save_is_forgotten(isolated_data: Path):
    """One corrupt tab must not keep the others from coming back."""
    screen.save(_body("good"), b"hello")
    folder = isolated_data / "run" / "terminals"
    (folder / "broken.json").write_text("{ not json", encoding="utf-8")

    loaded = screen.load_all()
    assert [body["id"] for body in loaded] == ["good"]
    assert not (folder / "broken.json").exists()


def test_saves_keep_the_most_recent(isolated_data: Path):
    """Without a cap, each launch would leave its files forever."""
    for index in range(screen.KEEP + 5):
        screen.save(_body(f"s{index}", closed_at=float(index)), b"x")
    loaded = screen.load_all()
    assert len(loaded) == screen.KEEP
    # The most recent are kept, and returned most recent first.
    assert loaded[0]["id"] == f"s{screen.KEEP + 4}"


def test_the_previous_launchs_screens_are_listed(isolated_data: Path):
    """Otherwise each start would return an empty list.

    The server dies with the window: what an agent was showing when the studio
    was quit exists nowhere else.
    """
    screen.save(_body("from-yesterday", closed_at=2.0), b"hello")
    fresh = TerminalManager()
    listing = {body["id"]: body for body in fresh.listing()}
    assert listing["from-yesterday"]["state"] == "interrupted"
    assert fresh.get("from-yesterday").snapshot()[0] == b"hello"


# ---------------------------------------------------------------------- resume


@pytest.fixture()
def saved() -> Iterator[str]:
    """A saved tab, visible to the manager the API serves.

    It is injected rather than read from disk: the manager is a singleton built
    at import, and reading it again would require reimporting the module.
    """
    session_id = "saved-tab"
    manager._archived[session_id] = ArchivedSession(
        _body(session_id, cwd="/tmp", screen=None) | {"screen": b"hello world"})
    try:
        yield session_id
    finally:
        manager._archived.pop(session_id, None)


def test_a_saved_tab_replays_then_closes_the_stream(client: TestClient, saved: str):
    """The same path as a finished tab: the screen is replayed, then the end comes.

    This is what lets the window reopen a tab of the previous launch without
    knowing there is no process behind it any more.
    """
    with client.websocket_connect(
            f"/api/terminal/sessions/{saved}/stream") as websocket:
        assert websocket.receive_json()["type"] == "snapshot"
        seen = bytearray()
        for _ in range(50):
            message = websocket.receive()
            if message.get("type") == "websocket.close":
                break
            if message.get("bytes"):
                seen += message["bytes"]
            elif message.get("text"):
                assert '"exit"' in message["text"]
                break
        assert b"hello world" in seen


def test_a_saved_tab_closes_for_good(client: TestClient, saved: str):
    assert client.delete(f"/api/terminal/sessions/{saved}").status_code == 200
    assert client.delete(f"/api/terminal/sessions/{saved}").status_code == 404


def test_resuming_a_saved_tab_reopens_it_in_place(
        client: TestClient, saved: str, fake_harness: Path, tmp_path: Path):
    """"Resume" resumes that very tab, not a numbered sibling next to it.

    The tab keeps its identifier -- hence its place, title and tint in the
    window -- and the saved screen leaves the list: there is only one tab where
    there was one. The process, though, is new.
    """
    old = manager.get(saved)
    old.cwd = str(tmp_path)
    old.title = "in blender, make me a sphere"
    response = client.post(f"/api/terminal/sessions/{saved}/revive")
    assert response.status_code == 200, response.text
    fresh = response.json()
    try:
        assert fresh["id"] == saved
        assert fresh["title"] == old.title
        assert fresh["state"] == "running"
        assert fresh["cwd"] == str(tmp_path)
        assert manager.get(saved).pid > 0
        # A single tab carries this identifier: the saved one left the list.
        assert [entry["id"] for entry in manager.listing() if entry["id"] == saved] == [saved]
    finally:
        client.delete(f"/api/terminal/sessions/{saved}")


def test_resuming_a_harness_tab_goes_through_the_harness(
        client: TestClient, saved: str, fake_harness: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch):
    """Relaunching through the raw argv would lose the environment cleaning.

    A DeepSeek tab resumed as a plain command would keep the inherited
    `ANTHROPIC_BASE_URL`, and would become an Anthropic tab without saying so:
    exactly what the harness catalog exists to avoid.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    old = manager.get(saved)
    old.harness = "claude"
    old.cwd = str(tmp_path)
    old.conversation = "11111111-2222-3333-4444-555555555555"
    transcript = transcripts.conversation_path(tmp_path, old.conversation)
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps(_turn("end_turn")) + "\n", encoding="utf-8")
    response = client.post(f"/api/terminal/sessions/{saved}/revive")
    assert response.status_code == 200, response.text
    fresh = response.json()
    try:
        assert fresh["harness"] == "claude"
        assert fresh["command"] == str(fake_harness / "claude")
        # The agent reopens the tab's conversation, and only that one.
        command = manager.get(saved).command
        position = command.index("--resume")
        assert command[position + 1] == old.conversation
        assert "--continue" not in command
        assert fresh["conversation"] == old.conversation
    finally:
        client.delete(f"/api/terminal/sessions/{saved}")


def test_a_tab_without_a_known_conversation_does_not_resume_someone_elses(
        client: TestClient, saved: str, fake_harness: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch):
    """`--continue` would reopen the folder's latest conversation, anyone's.

    A tab whose conversation was not named does not know which one was its
    own. It does not guess -- the folder's most recent one is often someone
    else's (a Claude Desktop session, for one): it starts a new conversation.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    old = manager.get(saved)
    old.harness = "claude"
    old.cwd = str(tmp_path)
    # A neighbor's conversation, written in the same folder.
    neighbor = transcripts.conversation_path(tmp_path, "neighbor")
    neighbor.parent.mkdir(parents=True)
    neighbor.write_text(json.dumps(_turn("tool_use")) + "\n", encoding="utf-8")
    response = client.post(f"/api/terminal/sessions/{saved}/revive")
    assert response.status_code == 200, response.text
    try:
        command = manager.get(saved).command
        assert "--continue" not in command
        assert "--resume" not in command
        position = command.index("--session-id")
        assert command[position + 1] != "neighbor"
    finally:
        client.delete(f"/api/terminal/sessions/{saved}")


def test_resuming_an_explicit_command_adds_nothing(
        client: TestClient, saved: str):
    """A tab launched by a command has no conversation to reopen."""
    old = manager.get(saved)
    old.harness = ""
    old.command = ["bash", "-c", "sleep 60"]
    response = client.post(f"/api/terminal/sessions/{saved}/revive")
    assert response.status_code == 200, response.text
    try:
        assert manager.get(saved).command[-2:] == ["-c", "sleep 60"]
    finally:
        client.delete(f"/api/terminal/sessions/{saved}")


def test_a_tab_that_fails_to_come_back_stays_saved(
        client: TestClient, saved: str):
    """A failed launch must not take the tab away.

    The tab leaves the saved list before its agent is launched: if the program
    disappeared from the PATH meanwhile, putting it back is the only way not to
    lose a screen nobody asked to close.
    """
    old = manager.get(saved)
    old.harness = ""
    old.command = ["/no/program/by/that/name"]
    response = client.post(f"/api/terminal/sessions/{saved}/revive")
    assert response.status_code == 400, response.text
    assert manager.get(saved) is old
    assert old.state == "interrupted"


def test_resuming_an_unknown_tab_is_refused(client: TestClient):
    assert client.post("/api/terminal/sessions/missing/revive").status_code == 404


# -------------------------------------------------------------------- shutdown


def test_shutdown_saves_the_running_tabs(client: TestClient, isolated_data: Path):
    """What was running at shutdown did not finish: it was interrupted.

    Saving "exited" with the victim's exit code would read as "it finished",
    and the tab would reopen on a lie.
    """
    session_id = _open(client, "bash", "-c", "echo hello; sleep 60")
    session = manager.get(session_id)
    for _ in range(400):
        if b"hello" in session.snapshot()[0]:
            break
        time.sleep(0.01)

    manager.shutdown()

    saved = isolated_data / "run" / "terminals" / f"{session_id}.json"
    assert saved.is_file()
    body = json.loads(saved.read_text(encoding="utf-8"))
    assert body["state"] == "interrupted"
    assert b"hello" in base64.b64decode(body["screen"])


# ---------------------------------------------------------------------- effort


def test_effort_is_offered_only_if_it_exists(client: TestClient,
                                            monkeypatch: pytest.MonkeyPatch):
    """An agent whose flag is unknown offers none, and says so."""
    monkeypatch.setattr(harnesses, "codex_catalog", lambda: None)
    monkeypatch.delenv("CLAUDE_DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_MIMO_MODEL", raising=False)
    entries = {entry["id"]: entry for entry in client.get("/api/terminal/harnesses").json()}
    assert entries["claude"]["effort_levels"] == ["low", "medium", "high", "xhigh", "max"]
    assert entries["codex"]["effort_levels"] == ["low", "medium", "high", "xhigh"]
    assert entries["kimi"]["effort_levels"] == []
    assert "exposes no effort level" in entries["kimi"]["effort_reason"]


def test_effort_follows_the_launched_model(monkeypatch: pytest.MonkeyPatch):
    """Same program, another endpoint: the levels are the model's."""
    monkeypatch.delenv("CLAUDE_MIMO_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_DEEPSEEK_MODEL", raising=False)
    # MiMo refuses max and xhigh (400): they are not offered.
    assert harnesses.efforts_for(harnesses._require("mimo")) == (
        "mimo-v2.6-pro", ("low", "medium", "high"))
    with pytest.raises(ServiceError, match="unknown"):
        harnesses.effort_args("mimo", "max")
    # DeepSeek maps medium and xhigh to high: they would change nothing.
    assert harnesses.efforts_for(harnesses._require("deepseek"))[1] == ("low", "high", "max")
    # The wrapper reads its model from the environment; so does the studio.
    monkeypatch.setenv("CLAUDE_DEEPSEEK_MODEL", "deepseek-v4-pro")
    assert harnesses.efforts_for(harnesses._require("deepseek")) == (
        "deepseek-v4-pro", ("high", "max"))
    # Codex publishes its catalog: it is read rather than copied.
    monkeypatch.setattr(harnesses, "codex_catalog",
                        lambda: ("gpt-6-astra", ("low", "medium", "high", "xhigh", "max", "ultra")))
    assert harnesses.effort_args("codex", "ultra") == ["-c", "model_reasoning_effort=ultra"]
    with pytest.raises(ServiceError, match="unknown"):
        harnesses.effort_args("codex", "minimal")


def test_effort_turns_into_arguments(fake_harness: Path,
                                     monkeypatch: pytest.MonkeyPatch):
    """Each agent has its form: Claude a flag, Codex a config override."""
    monkeypatch.setattr(harnesses, "codex_catalog", lambda: None)
    command, _ = harnesses.command_for("claude", "xhigh")
    assert command[-2:] == ["--effort", "xhigh"]
    assert harnesses.effort_args("codex", "high") == [
        "-c", "model_reasoning_effort=high"]

    # The agent's default means passing nothing at all.
    assert harnesses.effort_args("claude", "") == []

    with pytest.raises(ServiceError, match="does not accept"):
        harnesses.effort_args("kimi", "high")
    with pytest.raises(ServiceError, match="unknown"):
        harnesses.effort_args("claude", "turbo")


def test_each_agent_says_how_to_reopen_its_conversation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Claude names its conversation; Codex and Kimi cannot, and do not guess.

    `--continue` or `resume --last` reopen "the folder's last conversation": a
    neighbor's just as well. They are therefore never passed.
    """
    for name in ("claude", "codex"):
        script = tmp_path / name
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
    monkeypatch.setattr(harnesses, "search_path", lambda env=None: str(tmp_path))
    monkeypatch.setattr(harnesses, "codex_catalog", lambda: None)

    new, _ = harnesses.command_for("claude", "", "abc")
    assert new[-2:] == ["--session-id", "abc"]
    resumed, _ = harnesses.command_for("claude", "", "abc", resume=True)
    assert resumed[-2:] == ["--resume", "abc"]

    codex, _ = harnesses.command_for("codex", "high", "abc", resume=True)
    assert codex[-2:] == ["-c", "model_reasoning_effort=high"]
    assert not harnesses.names_conversation("codex")
    assert not harnesses.names_conversation("kimi")
    assert harnesses.names_conversation("deepseek")


def test_the_named_transcript_ignores_the_neighbors(tmp_path: Path):
    """Two agents in the same folder: each reads only its own conversation."""
    cwd = tmp_path / "project"
    cwd.mkdir()
    root = tmp_path / "transcripts"
    neighbor = transcripts.conversation_path(cwd, "neighbor", root)
    neighbor.parent.mkdir(parents=True)
    neighbor.write_text(json.dumps(_turn("tool_use")) + "\n", encoding="utf-8")
    tail = Transcript(cwd, 0.0, root=root, conversation="me")
    assert tail.poll() is None, "the neighbor's transcript does not speak for the tab"

    transcripts.conversation_path(cwd, "me", root).write_text(
        json.dumps(_turn("end_turn")) + "\n", encoding="utf-8")
    assert tail.poll() == "end_turn"


def test_the_tab_learns_its_title_from_the_first_message(fake_harness: Path):
    """"claude 1" says nothing; the first message does."""
    tabs = TerminalManager()
    session = tabs.create(loop=None, harness="claude", cwd=str(fake_harness))
    assert session.auto_title is True
    assert session.title.startswith("claude")

    session._learn_title(b"Split the knight sheet\r")
    assert session.title == "Split the knight sheet"
    assert session.auto_title is False

    # The second message no longer renames: the title is learned once.
    session._learn_title(b"something else\r")
    assert session.title == "Split the knight sheet"


def test_a_command_does_not_name_the_conversation(fake_harness: Path):
    """`/help` does not say what it is about: the next message is awaited."""
    tabs = TerminalManager()
    session = tabs.create(loop=None, harness="claude", cwd=str(fake_harness))

    session._learn_title(b"/help\r")
    assert session.auto_title is True

    session._learn_title(b"Make the mesh lighter\r")
    assert session.title == "Make the mesh lighter"


def test_a_title_chosen_by_hand_is_not_overwritten(fake_harness: Path):
    """Renaming a tab is deciding its name -- for good."""
    tabs = TerminalManager()
    session = tabs.create(loop=None, harness="claude", cwd=str(fake_harness))
    session.auto_title = False  # what `rename` does
    session.title = "My tab"
    session._learn_title(b"a message that must not rename\r")
    assert session.title == "My tab"


def test_the_learned_title_is_cleaned(fake_harness: Path):
    """An arrow or a paste must not end up in a tab title."""
    tabs = TerminalManager()
    session = tabs.create(loop=None, harness="claude", cwd=str(fake_harness))
    # An arrow (ANSI sequence), multiple spaces, then the submission.
    session._learn_title(b"\x1b[A\x1b[D  A   title " + b" " * 40 + b"\r")
    assert session.title == "A title"


def test_the_mouse_does_not_name_the_tab(fake_harness: Path):
    """An agent that tracks the mouse receives a report on every move."""
    tabs = TerminalManager()
    session = tabs.create(loop=None, harness="claude", cwd=str(fake_harness))
    # Hundreds of SGR moves: the bounded buffer must keep nothing of them, not
    # even the tail of a cut sequence.
    for x in range(200):
        session._learn_title(b"\x1b[<35;%d;14M" % x)
    session._learn_title(b"\x1b[M #!")  # X10
    session._learn_title(b"\r")
    assert session.auto_title is True

    session._learn_title(b"\x1b[<0;10;5MMake the mesh\x1b[<0;10;5m lighter\r")
    assert session.title == "Make the mesh lighter"


def test_a_sequence_cut_between_two_reads_does_not_name_the_tab(
        fake_harness: Path):
    """The PTY returns blocks: `ESC[<35;62;14M` arrives in pieces.

    Without care, a tab title ends up as `1;13M [<35;62;14M [<35;63`. The tail
    of a cut sequence is not text, and the message that follows is.
    """
    tabs = TerminalManager()
    session = tabs.create(loop=None, harness="claude", cwd=str(fake_harness))

    # The escape alone, then the payload: the shape an SGR report takes.
    session._learn_title(b"\x1b")
    session._learn_title(b"[<35;62;14M")
    session._learn_title(b"\x1b")
    session._learn_title(b"[<35;63;15M")
    assert session.auto_title is True

    # A sequence cut right in the middle, then the rest of the report.
    session._learn_title(b"\x1b[<35;66;1")
    session._learn_title(b"6M")
    # An X10 report missing bytes, then an arrow.
    session._learn_title(b"\x1b[")
    session._learn_title(b"M #")
    session._learn_title(b"!\x1b[A\x1b[D  on the project ")
    assert session.auto_title is True

    session._learn_title(b"blender, make a sphere \xe2\x86\x92 smooth\r")
    assert session.title == "on the project blender, make a sphere → smooth"


def test_the_screen_grid_is_returned_with_the_tab(fake_harness: Path):
    """The client replays the buffer at the size it was drawn for.

    A TUI positions itself absolutely: reading its screen back in another grid
    only gives a string of pieces. The live tab and the saved tab must
    therefore both state their grid.
    """
    tabs = TerminalManager()
    session = tabs.create(loop=None, harness="claude", cwd=str(fake_harness),
                          cols=137, rows=41)
    assert (session.describe()["cols"], session.describe()["rows"]) == (137, 41)

    archived = ArchivedSession(_body("large", cols=477, rows=58))
    assert (archived.describe()["cols"], archived.describe()["rows"]) == (477, 58)


def test_a_saved_tabs_grid_cannot_be_set(fake_harness: Path):
    """The client does not choose the grid of a screen already drawn.

    It announces its own as soon as it has measured itself. Applying it to a
    saved tab would rewrite the size the buffer was drawn for, and the next
    replay would read a wide screen in a narrow grid -- in pieces. A saved
    tab's grid describes its buffer: it does not move.
    """
    archived = ArchivedSession(_body("large", cols=357, rows=67))
    assert archived.resize(86, 59) == (357, 67)
    assert (archived.describe()["cols"], archived.describe()["rows"]) == (357, 67)

    # The live tab does follow what the window announces: its PTY must learn
    # the size, and the program redraws itself.
    live = TerminalManager().create(loop=None, harness="claude",
                                    cwd=str(fake_harness), cols=357, rows=67)
    try:
        assert live.resize(86, 59) == (86, 59)
    finally:
        live.close(grace=0)


def test_effort_stays_with_the_tab(fake_harness: Path):
    """What launched the tab is kept: resuming it replays it."""
    tabs = TerminalManager()
    session = tabs.create(loop=None, harness="claude", effort="high",
                          cwd=str(fake_harness))
    assert session.describe()["effort"] == "high"
    assert "high" in session.command
    assert session._record()["auto_title"] is True


def test_a_new_grid_is_announced_to_whoever_watches(client: TestClient):
    """The agents at work page follows the grid without ever changing it."""
    session_id = _open(client, "bash", "-c", "sleep 30")
    try:
        with client.websocket_connect(f"/api/terminal/sessions/{session_id}/stream") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            ws.send_json({"type": "resize", "cols": 100, "rows": 30})
            announced = None
            for _ in range(50):
                message = ws.receive()
                if message.get("text"):
                    announced = json.loads(message["text"])
                    if announced.get("type") == "size":
                        break
            assert announced == {"type": "size", "cols": 100, "rows": 30}
    finally:
        client.delete(f"/api/terminal/sessions/{session_id}")
