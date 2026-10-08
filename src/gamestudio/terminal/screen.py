"""A tab's screen, saved to disk.

The replay buffer lives in the server's memory. But the shell ties the server's
lifetime to its own (`PR_SET_PDEATHSIG`, see `src-tauri/src/server.rs`):
closing the window kills the server, and with it everything the agents were
showing. Without this file, reopening the application would show empty tabs --
not what one expects of a work session just left, especially one that cost
hours of agent time.

What is saved is the replay buffer itself, which already holds the escape
sequences needed to rebuild the screen (the equivalent of xterm's
`serialize`). This is what lets a tab be read again without having kept its
process.

**What is not done.** The process is not resumed: its descendants died with
it, and nothing brings them back. What is resumed is the tab --
`TerminalManager.revive` reopens it in place, same identifier, same title, and
asks the agent to reopen the conversation it held (`Harness.resume_args`).
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from ..config import settings

logger = logging.getLogger("gamestudio.terminal")

# How much of a screen is kept. A tab may have produced megabytes; what matters
# is what it was showing, and a TUI easily fits in this size. The rest is log
# that nobody will reread in a dead tab.
SCREEN_BYTES = 128 * 1024

# Rare enough not to copy a buffer over and over, frequent enough that an abrupt
# shutdown only loses a moment of work.
SAVE_EVERY = 5.0

# How many saved tabs are kept. Without a cap, each launch would leave its files
# and the folder would grow forever, for content nobody reopens after a few
# days.
KEEP = 40


def screens_dir() -> Path:
    """The screens folder. Temporary data, hence in `run/`, not the library.

    The library only holds what a project claims; a terminal screen is not an
    asset and has no business there.
    """
    return settings().run_dir / "terminals"


def _path(session_id: str, folder: Path | None = None) -> Path:
    return (folder or screens_dir()) / f"{session_id}.json"


def save(record: dict[str, Any], screen: bytes, folder: Path | None = None) -> None:
    """Save a screen. The bytes go through base64: a terminal buffer is not text,
    and a JSON that is not valid UTF-8 could not be read back.

    `folder` is the folder the tab chose when it was created: the last save
    happens from the reader thread, sometimes after the settings changed -- a
    finished test must not have its screen land in the machine's real data."""
    path = _path(str(record["id"]), folder)
    body = dict(record)
    body["screen"] = base64.b64encode(screen[-SCREEN_BYTES:]).decode("ascii")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling file then rename: a stop halfway would otherwise
        # leave a truncated JSON, hence an unreadable tab at the next start --
        # exactly what this is meant to avoid.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        # A full or read-only disk must not stop the studio: the resume is lost,
        # not the running session.
        logger.warning("screen not saved (%s): %s", path, error)


def forget(session_id: str) -> None:
    """Forget a screen: the tab was closed for good."""
    try:
        _path(session_id).unlink()
    except OSError:
        pass


def load_all() -> list[dict[str, Any]]:
    """The saved tabs, most recent first."""
    try:
        files = list(screens_dir().glob("*.json"))
    except OSError:
        return []
    found: list[dict[str, Any]] = []
    for path in files:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            body["screen"] = base64.b64decode(body.get("screen") or "")
        except (OSError, ValueError):
            # An unreadable file is forgotten: one corrupt tab must not keep the
            # others from coming back.
            logger.warning("unreadable screen, forgotten: %s", path)
            forget(path.stem)
            continue
        found.append(body)
    found.sort(key=lambda body: float(body.get("closed_at") or 0), reverse=True)
    for stale in found[KEEP:]:
        forget(str(stale.get("id") or ""))
    return found[:KEEP]
