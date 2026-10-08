"""The terminal's HTTP and WebSocket surface, mounted by `api/app.py`.

These routes carry no logic: they turn a message into a call on `session.py`,
and nothing more -- the same rule as for the rest of the API.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..service.errors import NotFound
from .harnesses import DEFAULT, HARNESSES, resolve
from .session import (
    END_OF_PROCESS,
    ArchivedSession,
    Subscriber,
    TerminalSession,
    manager,
)

logger = logging.getLogger("gamestudio.terminal")

router = APIRouter()


class SessionRequest(BaseModel):
    title: str = ""
    # Empty: follow the harness. Non-empty: override it -- the escape hatch for
    # tests, which launch `bash` rather than an agent. The interface only
    # offers the catalog.
    command: list[str] = Field(default_factory=list)
    harness: str = DEFAULT
    # Effort level asked of the agent. Empty: its own default. A harness that
    # does not accept one refuses to open, with the reason.
    effort: str = ""
    cwd: str = ""
    cols: int = 80
    rows: int = 24


class RenameRequest(BaseModel):
    title: str


@router.get("/api/terminal/harnesses")
def terminal_harnesses() -> list[dict[str, Any]]:
    """The agents on offer, and which ones can be launched on this machine.

    Availability is computed here, where the PATH is real; the front only shows
    it, and greys out an entry with the returned reason.
    """
    return [resolve(harness.id) for harness in HARNESSES]


@router.get("/api/terminal/sessions")
def terminal_sessions() -> list[dict[str, Any]]:
    return manager.listing()


@router.post("/api/terminal/sessions")
async def terminal_create(request: SessionRequest) -> dict[str, Any]:
    """Open a tab.

    Async because it needs the current event loop: that loop will carry the
    PTY's broadcast, whose reading lives in a thread. The launch itself is a
    `fork`/`exec` of a few milliseconds, for an action done once per tab.
    """
    _refresh_context()
    session = manager.create(
        loop=asyncio.get_running_loop(), title=request.title,
        command=request.command, harness=request.harness, cwd=request.cwd,
        cols=request.cols, rows=request.rows, effort=request.effort)
    return session.describe()


def _refresh_context() -> None:
    """Rewrite `context/briefing.md` right before opening a tab.

    This is where "the context is up to date" stops being an intention: the
    agent starting in this directory reads `AGENTS.md` then this file, and finds
    the studio's state as of the moment the tab opens -- not that of the last
    production. The computation is local and offline; if it fails, it must not
    keep a tab from opening.
    """
    from ..service import briefing

    try:
        briefing.write_briefing()
    except Exception:
        logger.exception("briefing not regenerated before opening a tab")


@router.patch("/api/terminal/sessions/{session_id}")
def terminal_rename(session_id: str, request: RenameRequest) -> dict[str, Any]:
    return manager.rename(session_id, request.title)


@router.delete("/api/terminal/sessions/{session_id}")
def terminal_close(session_id: str) -> dict[str, Any]:
    """Close a tab.

    Synchronous on purpose: closing waits a few seconds for the child before
    killing it, and FastAPI's thread pool must carry that wait, not the event
    loop.
    """
    return manager.close(session_id)


@router.post("/api/terminal/sessions/{session_id}/revive")
async def terminal_revive(session_id: str) -> dict[str, Any]:
    """Resume a saved tab: the same tab, with a new agent behind it.

    The new process reopens the conversation the old one held (see
    `TerminalManager.revive`), and the tab keeps its identifier: what the
    window gets back here replaces the saved tab, it is not added to it.

    A POST and not a GET: it starts a process, so it changes state, and a verb
    that does not promise to be harmless must not be cached or prefetched.
    """
    session = manager.revive(session_id, loop=asyncio.get_running_loop())
    return session.describe()


async def _forward(websocket: WebSocket, session: TerminalSession | ArchivedSession,
                   subscriber: Subscriber, buffer: bytes, truncated: bool,
                   seq: int) -> None:
    """Everything that goes to the client passes through here.

    A single coroutine writes to the websocket: two concurrent writers would
    interleave their frames. The buffer replay thus comes from here, and not
    from the connection handler.
    """
    # The grid travels with the screen: the client replays the buffer at that
    # size before fitting it to its box. Without it, a TUI drawn for a wide
    # window reads back in pieces in a narrow one.
    await websocket.send_json({"type": "snapshot", "truncated": truncated, "seq": seq,
                               "cols": session.cols, "rows": session.rows})
    if buffer:
        await websocket.send_bytes(buffer)
    # Replay is not enough: a full-screen interface must be prodded to redraw
    # itself entirely, and only `SIGWINCH` does that.
    session.redraw()
    if session.state != "running":
        # The process had already ended when the subscription was made: the end
        # sentinel was broadcast before, and waiting on the queue would yield
        # nothing more. This happens when reopening the window on a finished tab.
        await websocket.send_json({"type": "exit", "code": session.exit_code})
        await websocket.close()
        return
    while True:
        item = await subscriber.queue.get()
        if item is END_OF_PROCESS:
            # Close after saying so: the client learns of the process's end and
            # of the stream's closing in one go.
            await websocket.send_json({"type": "exit", "code": session.exit_code})
            await websocket.close()
            return
        if isinstance(item, dict):
            # A tab event (its grid changed), not screen output.
            await websocket.send_json(item)
            continue
        await websocket.send_bytes(item)


@router.websocket("/api/terminal/sessions/{session_id}/stream")
async def terminal_stream(websocket: WebSocket, session_id: str) -> None:
    try:
        session = manager.get(session_id)
    except NotFound as error:
        await websocket.close(code=1008, reason=str(error))
        return
    await websocket.accept()
    # Subscribe before reading the buffer: in the other order, whatever arrived
    # in between would be lost. In this one, the worst case is replaying it.
    subscriber = session.subscribe()
    buffer, truncated, seq = session.snapshot()
    forwarder = asyncio.create_task(
        _forward(websocket, session, subscriber, buffer, truncated, seq))
    try:
        while True:
            await _request(websocket, session)
    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError:
        # A client sending garbage must not bring the server down.
        pass
    finally:
        forwarder.cancel()
        session.unsubscribe(subscriber)


async def _request(websocket: WebSocket, session: TerminalSession) -> None:
    """Handle a client message: keystrokes, or a new size."""
    message = await websocket.receive_json()
    kind = message.get("type")
    if kind == "input":
        session.write(str(message.get("data", "")).encode())
    elif kind == "resize":
        session.resize(message.get("cols", 80), message.get("rows", 24))
