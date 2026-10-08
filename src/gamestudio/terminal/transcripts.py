"""Reading, in an agent's native transcript, the moment it hands control back.

A tab that produces bytes is not a tab waiting for an answer: a full-screen
interface redraws its counter every second, so a PTY's `seq` does not tell
"it is working" from "it is waiting for you". The only reliable judge is what
the agent itself writes. Claude Code keeps one JSONL transcript per session,
where each assistant message carries a `stop_reason`: `end_turn` means "I am
done, your turn", `tool_use` means "I am going on".

The transcript is found through the working directory: Claude Code files its
own under `~/.claude/projects/<slugified-path>/`, and our tab's is the file
born there when it is launched. A harness whose transcript cannot be read --
codex, kimi -- says nothing rather than something false: the front then falls
back on its coarser criterion, which stays correct.

**What this module cannot do.** Two agents launched in the same directory
write two transcripts in the same folder; the most recently modified one is
chosen, which is right in every ordinary case and may be wrong if the other
agent writes faster than ours. Entries are filtered by timestamp to rule out
the history of a previous tab, which covers the common case -- a tab reopened
in a project already being worked on.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("gamestudio.terminal")

# How much is read at once, and kept from one read to the next. An agent's
# transcript grows fast: it is never reread whole, only what was added since
# the last poll.
CHUNK = 256 * 1024

# How much older than the tab a transcript may be. The file is born when the
# agent starts, but the file's clock and ours may differ by a few seconds, and
# the kernel does not always date a write at the moment the agent made it.
SLACK = 10.0

# Beyond this, stop looking. An agent that writes no transcript -- or not where
# it is expected -- will not write one by insisting, and searching on every poll
# would open a folder twelve times a second.
LOCATE_ATTEMPTS = 40

# The only state that means "your turn". Everything else -- `tool_use`,
# `max_tokens`, `stop_sequence` -- means the agent is not done, and a badge that
# lights up at the wrong time is no better than no badge.
HANDOVER = "end_turn"


def projects_root() -> Path:
    """Where Claude Code files its transcripts.

    `CLAUDE_CONFIG_DIR` moves the agent's whole configuration: the studio must
    follow, otherwise it would look for the transcripts of an installation
    nobody uses.
    """
    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    home = Path(configured) if configured else Path.home() / ".claude"
    return home / "projects"


def slug(path: Path) -> str:
    """The folder name Claude Code gives a working directory.

    Everything that is not alphanumeric becomes a hyphen: `/home/me/proj`
    gives `-home-me-proj`. Case is kept.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _stamp(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _reason(line: bytes, since: float) -> str | None:
    """A line's `stop_reason`, if it really is about our tab."""
    if not line.strip():
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        # A line truncated by a write in progress, or a format not known: it
        # says nothing, and that is no reason to stop.
        return None
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return None
    if entry.get("isSidechain"):
        # A subagent's turn: it tells of the work of one of the tab's children,
        # not of the tab's own state.
        return None
    if since:
        stamp = _stamp(entry.get("timestamp"))
        if stamp is None or stamp < since:
            return None
    message = entry.get("message")
    reason = message.get("stop_reason") if isinstance(message, dict) else None
    return reason if isinstance(reason, str) else None


class Transcript:
    """An agent's transcript, read as it grows.

    The file is written by another program and may be written partially: the
    rest of an incomplete line is thus kept from one read to the next, otherwise
    an `end_turn` split in two by a write would never be read.
    """

    def __init__(self, cwd: Path, since: float, root: Path | None = None,
                 conversation: str = "") -> None:
        self.cwd = cwd
        # The tab's conversation, when it named it: its transcript is then known
        # by name, and no other file of the folder -- that of a neighboring
        # session, written at the same moment -- can be mistaken for it.
        self.conversation = conversation
        self.since = since
        self.root = root if root is not None else projects_root()
        self.path: Path | None = None
        self.offset = 0
        self.remainder = b""
        self.attempts = 0
        self._first = True

    def locate(self) -> Path | None:
        """This tab's transcript, or None as long as it does not exist yet.

        An agent takes a few hundred milliseconds to open its own: the first
        poll thus does not find it, and that is expected.
        """
        if self.path is not None:
            return self.path
        folder = self.root / slug(self.cwd)
        if self.conversation:
            # A known name is searched without an attempt limit: a `stat` costs
            # nothing, and the agent only writes its transcript on the first message.
            named = folder / f"{self.conversation}.jsonl"
            return self._open(named) if named.is_file() else None
        if self.attempts >= LOCATE_ATTEMPTS:
            return None
        self.attempts += 1
        try:
            candidates = [entry for entry in folder.glob("*.jsonl")
                          if entry.stat().st_mtime >= self.since - SLACK]
        except OSError:
            # The folder does not exist yet: the agent never ran here.
            return None
        if not candidates:
            return None
        return self._open(max(candidates, key=lambda entry: entry.stat().st_mtime))

    def _open(self, newest: Path) -> Path:
        self.path = newest
        self._first = True
        # Start near the end, not at the start: the reasons of interest are the
        # latest ones, and an agent's transcript quickly exceeds one read --
        # starting from zero would reread a whole history, oldest first, before
        # reaching the present.
        try:
            self.offset = max(0, newest.stat().st_size - CHUNK)
        except OSError:
            self.offset = 0
        return newest

    def _scan(self, raw: bytes, filtered: bool) -> str | None:
        parts = (self.remainder + raw).split(b"\n")
        self.remainder = parts.pop()
        found = None
        for line in parts:
            reason = _reason(line, self.since if filtered else 0.0)
            if reason is not None:
                found = reason
        return found

    def poll(self) -> str | None:
        """The last `stop_reason` that appeared, or None if there is nothing new."""
        path = self.locate()
        if path is None:
            return None
        try:
            size = path.stat().st_size
            if size < self.offset:
                # The file was replaced or truncated: read it again from the
                # start, with the timestamp filter back on.
                self.offset = 0
                self.remainder = b""
                self._first = True
            if size == self.offset:
                return None
            with path.open("rb") as handle:
                handle.seek(self.offset)
                raw = handle.read(CHUNK)
                self.offset = handle.tell()
        except OSError as error:
            logger.debug("unreadable transcript (%s): %s", path, error)
            self.path = None
            return None
        # The timestamp filter only applies to the first read, the one of the
        # window preceding our arrival: it may hold the end of a previous tab's
        # turn, or of another agent in the same folder. What is written after
        # that necessarily belongs to this tab.
        found = self._scan(raw, self._first)
        self._first = False
        return found


def conversation_path(cwd: Path, conversation: str, root: Path | None = None) -> Path:
    """The transcript of a named Claude conversation, whether it exists or not."""
    return (root if root is not None else projects_root()) / slug(cwd) / f"{conversation}.jsonl"


def for_harness(harness_id: str, cwd: Path, since: float,
                conversation: str = "") -> Transcript | None:
    """A harness's transcript, or None when it cannot be read."""
    from .harnesses import journal_for

    if journal_for(harness_id) != "claude":
        return None
    return Transcript(cwd, since, conversation=conversation)


__all__ = ["HANDOVER", "Transcript", "conversation_path", "for_harness", "projects_root",
           "slug"]
