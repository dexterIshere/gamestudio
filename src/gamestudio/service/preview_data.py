"""A game's preview data: fake server answers, so a screen that needs a server renders full.

A networked game draws empty screens off line ("Server unreachable"): the
studio's renders -- a card's current render, the screen editor -- would show
nothing worth judging. The project can carry **preview data**: the answers
its server would give, as JSON, and the few setup lines that point the game
at them. Each render then starts a small local server that answers from them
(`serving`), for the time of the render only.

Everything lives in the project's studio folder, never in the game:

- `.gamestudio/preview/server.json` -- `{"enabled": true, "routes": [...]}`;
  a route is `{"method": "GET", "path": "/api/planet/*", "status": 200,
  "body": {...}}`. `*` matches one path segment, `**` the rest; the query is
  ignored; the first route that matches answers.
- `.gamestudio/preview/setup.gd` -- the body of a `setup(scene)` function run
  before the scene settles (see `godot/render_scene.gd`), where `{{server}}`
  is the fake server's address: `Api.BASE_URL = "{{server}}"`.
- `.gamestudio/preview/misses.json` -- what the last render asked and found no
  answer for: the list to complete.

The data is written by an agent (`handoff.preview_brief`), which reads the
game's network code, its server if it is in the repository, and its docs.
**The studio calls it by itself** (`after_render`): when the game's code talks
to a server (`networked`) and a render had no data for it -- none at all, or
requests left unanswered (`misses.json`) -- the agent is launched, or the open
one asked to complete. Never twice for the same gaps, never while it works,
never once the user turned the data off (`"auto": false`), never from a
server started with `GAMESTUDIO_NO_AGENTS` (a test server beside the real one).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..store.folders import STUDIO_DIR, project_paths
from ..terminal.session import TerminalSession, manager
from .context import studio
from .errors import NotFound, ServiceError

logger = logging.getLogger("gamestudio.preview")

DIR_NAME = "preview"
SERVER_NAME = "server.json"
SETUP_NAME = "setup.gd"
MISSES_NAME = "misses.json"
PLACEHOLDER = "{{server}}"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
AGENT_NAME = "agent.json"

# What a game's code uses to talk to a server: Godot's network classes.
NETWORK = re.compile(r"\b(HTTPRequest|HTTPClient|WebSocketPeer|WebSocketMultiplayerPeer|"
                     r"ENetMultiplayerPeer|StreamPeerTCP|PacketPeerUDP)\b")
# Folders that are not the game's own code.
SKIPPED = {".godot", ".git", STUDIO_DIR, "addons", "node_modules"}
SCAN_MAX = 2000


def _dir(project: str) -> Path:
    return project_paths(studio().settings, project).data / DIR_NAME


def _read_server(project: str) -> dict[str, Any]:
    file = _dir(project) / SERVER_NAME
    if not file.is_file():
        return {"enabled": False, "routes": []}
    try:
        found = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ServiceError(f"preview data unreadable ({file}): {exc}") from exc
    if not isinstance(found, dict) or not isinstance(found.get("routes", []), list):
        raise ServiceError(f"preview data malformed ({file}): expected "
                           '{"enabled": true, "routes": [...]}')
    found.setdefault("enabled", True)
    found.setdefault("routes", [])
    return found


def _matches(pattern: str, path: str) -> bool:
    want = [part for part in pattern.split("/") if part]
    got = [part for part in path.split("/") if part]
    for index, part in enumerate(want):
        if part == "**":
            return True
        if index >= len(got) or (part != "*" and part != got[index]):
            return False
    return len(want) == len(got)


def networked(project: str) -> list[str]:
    """The game's scripts that talk to a server, from the game root; [] if none."""
    root = project_paths(studio().settings, project).root
    found: list[str] = []
    scanned = 0
    for path in sorted(root.rglob("*.gd")):
        relative = path.relative_to(root)
        # Hidden folders are tools' copies of the game (worktrees), not the game.
        if any(part in SKIPPED or part.startswith(".") for part in relative.parts[:-1]):
            continue
        scanned += 1
        if scanned > SCAN_MAX:
            break
        try:
            if NETWORK.search(path.read_text(encoding="utf-8", errors="replace")):
                found.append(relative.as_posix())
        except OSError:
            continue
    return found


def _agent_record(project: str) -> dict[str, Any]:
    file = _dir(project) / AGENT_NAME
    try:
        found = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
    except (OSError, json.JSONDecodeError):
        found = {}
    return found if isinstance(found, dict) else {}


def _agent(project: str) -> TerminalSession | None:
    """The agent tab writing this project's data, while it is open."""
    session_id = str(_agent_record(project).get("session") or "")
    if not session_id:
        return None
    try:
        found = manager.get(session_id)
    except (NotFound, ServiceError):
        return None
    return found if isinstance(found, TerminalSession) and found.state == "running" else None


# ------------------------------------------------------------------ reading


def state(project: str) -> dict[str, Any]:
    """Where a project's preview data stands: on or off, its routes, its gaps."""
    folder = _dir(project)
    server = _read_server(project)
    setup = folder / SETUP_NAME
    misses: list[str] = []
    if (folder / MISSES_NAME).is_file():
        try:
            misses = list(json.loads((folder / MISSES_NAME).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            misses = []
    routes = server["routes"]
    agent = _agent(project)
    return {
        "project": project,
        "networked": networked(project)[:5],
        "auto": server.get("auto", True) is not False,
        "agent": ({"id": agent.id, "title": agent.title,
                   "working": (agent.turn or {}).get("state") != "waiting"}
                  if agent is not None else None),
        "enabled": bool(server["enabled"]) and bool(routes),
        "exists": bool(routes) or setup.is_file(),
        "routes": [f"{str(r.get('method') or 'GET').upper()} {r.get('path', '')}"
                   for r in routes if isinstance(r, dict)],
        "setup": setup.read_text(encoding="utf-8") if setup.is_file() else "",
        "misses": misses,
        "paths": {"folder": str(folder), "server": str(folder / SERVER_NAME),
                  "setup": str(setup), "misses": str(folder / MISSES_NAME)},
    }


# ------------------------------------------------------------------ writing


def set_enabled(project: str, enabled: bool) -> dict[str, Any]:
    """Turn the preview data on or off: off, renders reach the real server."""
    server = _read_server(project)
    if enabled and not server["routes"]:
        raise ServiceError("no preview data yet: have an agent write it first")
    server["enabled"] = enabled
    # Off is the user's choice of the real server: the studio stops completing.
    server["auto"] = enabled
    folder = _dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SERVER_NAME).write_text(json.dumps(server, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    return state(project)


# ------------------------------------------------------------------ serving


@dataclass
class Serving:
    """The fake server of one render: its address, its setup, what it was asked."""

    url: str
    setup: str
    served: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)


def _handler(routes: list[dict[str, Any]], serving: Serving) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            path = urlsplit(self.path).path
            asked = f"{self.command} {path}"
            for route in routes:
                method = str(route.get("method") or "GET").upper()
                if method == self.command and _matches(str(route.get("path", "")), path):
                    serving.served.append(asked)
                    self._send(int(route.get("status") or 200), route.get("body", {}))
                    return
            if asked not in serving.misses:
                serving.misses.append(asked)
            self._send(404, {"error": f"no preview data for {asked}"})

        def _send(self, status: int, body: Any) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            return

    for method in METHODS:
        setattr(Handler, f"do_{method}", Handler._answer)
    return Handler


@contextmanager
def serving(project: str) -> Iterator[Serving | None]:
    """The project's fake server for the time of a render, or None when it has none.

    What went unanswered is written to `misses.json` when the render ends.
    """
    server = _read_server(project) if project else {"enabled": False, "routes": []}
    routes = [route for route in server["routes"] if isinstance(route, dict)]
    if not (server["enabled"] and routes):
        yield None
        return
    setup_file = _dir(project) / SETUP_NAME
    setup = setup_file.read_text(encoding="utf-8") if setup_file.is_file() else ""
    serving_ = Serving(url="", setup="")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(routes, serving_))
    httpd.daemon_threads = True
    serving_.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    serving_.setup = setup.replace(PLACEHOLDER, serving_.url)
    thread = threading.Thread(target=httpd.serve_forever, name="preview-data", daemon=True)
    thread.start()
    try:
        yield serving_
    finally:
        httpd.shutdown()
        httpd.server_close()
        try:
            (_dir(project) / MISSES_NAME).write_text(
                json.dumps(serving_.misses, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        except OSError:
            pass


def changed_at(project: str) -> float:
    """When the data last changed (epoch s), 0 without data or turned off."""
    server = _read_server(project)
    if not (server["enabled"] and server["routes"]):
        return 0.0
    files = [_dir(project) / SERVER_NAME, _dir(project) / SETUP_NAME]
    return max((f.stat().st_mtime for f in files if f.is_file()), default=0.0)


def agent_activity(project: str) -> dict[str, Any] | None:
    """The data's agent while it works: its tab and since when (epoch s); else None.

    Light enough to be polled: no scan of the game.
    """
    if not project:
        return None
    tab = _agent(project)
    if tab is None or (tab.turn or {}).get("state") == "waiting":
        return None
    since = float(_agent_record(project).get("since") or 0) or tab.created_at
    return {"project": project, "id": tab.id, "title": tab.title, "started_at": since}


# ------------------------------------------------------------- automation


def after_render(project: str, preview: Serving | None) -> None:
    """After a render: call the data's agent if the game needs a server and lacked data.

    Never raises: a render is not failed by its follow-up.
    """
    try:
        _follow_up(project, preview)
    except Exception:
        logger.exception("preview data not followed up (%s)", project)


def _follow_up(project: str, preview: Serving | None) -> None:
    if os.environ.get("GAMESTUDIO_NO_AGENTS"):
        return
    if not project or not project_paths(studio().settings, project).linked:
        return
    server = _read_server(project)
    if server.get("auto", True) is False:
        return
    if preview is None and server["routes"]:
        return
    gaps = sorted(preview.misses) if preview is not None else ["(no data)"]
    if not gaps or not networked(project):
        return
    record = _agent_record(project)
    tab = _agent(project)
    if tab is not None and (tab.turn or {}).get("state") != "waiting":
        return
    if tab is not None and record.get("gaps") == gaps:
        return
    from . import handoff

    handoff.send_preview(project, session=tab.id if tab is not None else "", gaps=gaps)


# The data agents' tabs: session id -> project, for their hand-back.
_agents: dict[str, str] = {}


def _handed_back(session_id: str) -> None:
    """The data's agent handed back: every screen is drawn again with its data."""
    project = _agents.get(session_id)
    if project is None:
        return
    from . import screens

    try:
        screens.warm_all(project, force=True)
    except Exception:
        logger.exception("screens not redrawn after the preview data (%s)", project)


manager.on_handover(_handed_back)


def remember_agent(project: str, session_id: str, gaps: list[str]) -> None:
    """Note the tab writing the data, and the gaps it was asked about."""
    _agents[session_id] = project
    folder = _dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / AGENT_NAME).write_text(
        json.dumps({"session": session_id, "gaps": gaps, "since": time.time()},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
