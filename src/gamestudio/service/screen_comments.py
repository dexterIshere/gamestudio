"""Comments on a screen's elements: what the user asks of each one, sent to an agent in turn.

In the screen editor, an element is picked and a comment written on it ("this
button is too small", "align it with the title"). A comment is **saved** to be
sent later, or **sent** now. Sent comments go to the card's own agent -- one
Chats tab per screen, opened on the first one, in the screen branch's checkout
-- one at a time: while the agent works, the next ones wait in a **queue**, and
each hand-back (`TerminalManager.on_handover`) delivers the next.

A comment's states: `saved` (kept, not asked), `queued` (asked, waiting for
the agent), `sent` (typed into the tab, the agent is on it), `done` (the agent
handed back after it). They live next to the screen's other state, in
`comments.json` of its workspace (`screens._Place`).

The agent works on the screen's branch, never in the user's copy: the first
comment sent makes the branch, like a first edit (`screens._open_branch`).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..terminal.harnesses import DEFAULT
from ..terminal.session import TerminalSession, manager
from . import documents, screens
from .errors import NotFound, ServiceError

COMMENTS_NAME = "comments.json"
STATES = ("saved", "queued", "sent", "done")
TEXT_MAX = 2000

# The grid of the card's tab, until the Chats window measures it.
TAB_COLS = 120
TAB_ROWS = 36

# One writer at a time: the API's threads and the terminal watcher's.
_lock = threading.RLock()
# The tab of each card's agent: session id -> (project, folder, name).
_tabs: dict[str, tuple[str, str, str]] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _file(place: screens._Place) -> Path:
    return place.dir / COMMENTS_NAME


def _load(place: screens._Place) -> dict[str, Any]:
    file = _file(place)
    if not file.is_file():
        return {"next": 1, "session": "", "comments": []}
    try:
        found = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"next": 1, "session": "", "comments": []}
    found.setdefault("next", 1)
    found.setdefault("session", "")
    found.setdefault("comments", [])
    return found


def _save(place: screens._Place, data: dict[str, Any]) -> None:
    place.dir.mkdir(parents=True, exist_ok=True)
    _file(place).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")


def _live(session_id: str) -> TerminalSession | None:
    if not session_id:
        return None
    try:
        found = manager.get(session_id)
    except (NotFound, ServiceError):
        return None
    return found if isinstance(found, TerminalSession) and found.state == "running" else None


def _view(place: screens._Place, data: dict[str, Any]) -> dict[str, Any]:
    tab = _live(str(data.get("session") or ""))
    return {"comments": data["comments"],
            "session": tab.describe() if tab is not None else None}


def _place(project: str, folder: str, name: str) -> tuple[str, str, screens._Place]:
    folder, name = screens._card(project, folder, name)
    return folder, name, screens._Place(project, folder, name)


# ---------------------------------------------------------------- reading


def comments(project: str, folder: str, name: str) -> dict[str, Any]:
    """A screen's comments, oldest first, and its agent's tab if it is open."""
    _, _, place = _place(project, folder, name)
    with _lock:
        return _view(place, _load(place))


# ---------------------------------------------------------------- writing


def add(project: str, folder: str, name: str, path: str, text: str) -> dict[str, Any]:
    """Save a comment on an element of the screen (`path`, from its survey).

    Saved, not sent: `send` hands it to the agent.
    """
    folder, name, place = _place(project, folder, name)
    text = text.strip()
    if not text:
        raise ServiceError("an empty comment says nothing")
    if len(text) > TEXT_MAX:
        raise ServiceError(f"comment too long: {len(text)} characters (at most {TEXT_MAX})")
    node = screens._node(place, path)
    with _lock:
        data = _load(place)
        comment = {"id": int(data["next"]), "path": path,
                   "name": str(node.get("name") or path), "type": str(node.get("type") or ""),
                   "file": str(node.get("file") or ""), "text": text, "state": "saved",
                   "created_at": _now(), "sent_at": ""}
        data["next"] = comment["id"] + 1
        data["comments"].append(comment)
        _save(place, data)
        return {**_view(place, data), "comment": comment}


def remove(project: str, folder: str, name: str, ids: list[int]) -> dict[str, Any]:
    """Remove comments that are not with the agent (saved, or done)."""
    _, _, place = _place(project, folder, name)
    with _lock:
        data = _load(place)
        busy = [c["id"] for c in data["comments"]
                if c["id"] in ids and c["state"] in ("queued", "sent")]
        if busy:
            raise ServiceError(f"comment(s) {', '.join(map(str, busy))} are with the agent: "
                               "they are not removed")
        data["comments"] = [c for c in data["comments"] if c["id"] not in ids]
        _save(place, data)
        return _view(place, data)


def send(project: str, folder: str, name: str, ids: list[int], *, harness: str = DEFAULT,
         loop: Any = None) -> dict[str, Any]:
    """Hand comments to the screen's agent, in the given order.

    The first one goes now if the agent is free; the others -- and all of them
    if it is working -- wait in the queue, each delivered when the agent hands
    back. With no tab open, one is opened on the screen's branch.
    """
    folder, name, place = _place(project, folder, name)
    if not ids:
        raise ServiceError("no comment to send")
    with _lock:
        data = _load(place)
        known = {c["id"]: c for c in data["comments"]}
        missing = [str(i) for i in ids if i not in known]
        if missing:
            raise NotFound(f"comment(s) not found: {', '.join(missing)}")
        # The queue keeps the order of the request, after what already waits.
        for comment_id in ids:
            comment = known[comment_id]
            if comment["state"] in ("saved", "done"):
                comment["state"] = "queued"
                comment["sent_at"] = ""
                comment["rank"] = int(data.get("rank", 0)) + 1
                data["rank"] = comment["rank"]
        _save(place, data)
        _pump(project, folder, name, place, harness=harness, loop=loop)
        return _view(place, _load(place))


# ------------------------------------------------------------------ queue


def _pump(project: str, folder: str, name: str, place: screens._Place, *,
          harness: str = DEFAULT, loop: Any = None) -> None:
    """Deliver the next queued comment if the agent is free."""
    data = _load(place)
    session_id = str(data.get("session") or "")
    tab = _live(session_id)
    working = [c for c in data["comments"] if c["state"] == "sent"]
    if tab is None:
        # The tab is gone: what was typed into it was delivered, the rest waits
        # for a new one.
        for comment in working:
            comment["state"] = "done"
        working = []
    queued = sorted((c for c in data["comments"] if c["state"] == "queued"),
                    key=lambda c: int(c.get("rank", 0)))
    if working or not queued:
        _save(place, data)
        return
    comment = queued[0]
    line = _request(name, comment)
    if tab is None:
        if not place.opened():
            screens._open_branch(place, folder, name)
        brief = _brief(project, folder, name, place)
        repo = screens._repo(place.game)
        cwd = place.root_in_checkout(repo)
        tab = manager.create(loop=loop, harness=harness, cwd=str(cwd),
                             title=f"{screens._kind(folder)['commit'].strip()} {name}",
                             first_message=f"Read {brief} and follow it. {line}",
                             cols=TAB_COLS, rows=TAB_ROWS)
        data["session"] = tab.id
    else:
        manager.type_in(tab.id, line)
    _tabs[tab.id] = (project, folder, name)
    comment["state"] = "sent"
    comment["sent_at"] = _now()
    _save(place, data)


def _handed_back(session_id: str) -> None:
    """The agent of a screen handed back: its comment is done, the next one goes."""
    card = _tabs.get(session_id)
    if card is None:
        return
    project, folder, name = card
    with _lock:
        place = screens._Place(project, folder, name)
        data = _load(place)
        if data.get("session") != session_id:
            return
        for comment in data["comments"]:
            if comment["state"] == "sent":
                comment["state"] = "done"
        _save(place, data)
        _pump(project, folder, name, place)


manager.on_handover(_handed_back)


def _request(name: str, comment: dict[str, Any]) -> str:
    """The line typed for a comment: the element, then what the user wants."""
    where = f" ({comment['type']}" + (f", declared in {comment['file']}" if comment["file"]
                                      else ", created by code") + ")"
    return (f"Comment #{comment['id']} on `{comment['path']}`{where} of the screen {name}: "
            f"{comment['text']}")


def _brief(project: str, folder: str, name: str, place: screens._Place) -> Path:
    """The brief of the screen's agent: where it works, how it answers a comment."""
    saved = place.load()
    repo = screens._repo(place.game)
    root = place.root_in_checkout(repo)
    godot = root / str(saved.get("godot") or ".")
    card = documents.read_document(project, name, folder)
    call = f'project="{project}", folder="{folder}", name="{name}"'
    branch = screens.branch_name(folder, name)
    subject = screens._kind(folder)["commit"].strip()
    text = f"""# Comments on the screen “{card['title']}”

The user comments on elements of a game screen, in the studio's screen
editor. You receive the comments one at a time; each one names an element
(its path in the scene tree, its type, the scene that declares it) and says
what the user wants of it.

## The screen

- **Card** — `{card['path']}`: what the screen is for. Read it.
- **Scene** — `{saved.get('scene')}`, in the Godot project `{godot}`.
- **Its render and elements** — `screen_state({call}, look=true)`.

## Where you work

On the branch `{branch}`, in its own checkout: `{place.checkout}` (the game
is `{root}`). Your working directory is there. **Never touch the user's copy**
(`{place.game}`), never merge: the user merges the branch.

## For each comment

1. Find the element (`screen_state` gives its path and its scene). If the
   comment is unclear or needs a decision, ask -- in one question -- and wait.
2. Make the change. A property the editor writes (text, colour, size,
   margin, icon -- `screen_node({call}, path=…)` lists them) goes through
   `screen_edit({call}, path=…, changes=…)`: it commits and redraws. Anything
   else is edited in the checkout, then committed there with the subject
   `{subject} {name} : <element> — <what changed>`, so the studio's “Undo”
   can take it back.
3. Redraw and look: `screen_render({call}, look=true)`. A change that is not
   looked at has proved nothing.
4. Answer in one or two sentences, then stop: the next comment comes when
   you hand back.
"""
    from .handoff import _write

    return _write(project, f"screen-comments-{documents.slug(name)}.md", text)
