"""A terminal tab: a PTY, what it has shown, and who is listening.

The terminal is not a studio operation -- it produces no asset and carries no
domain state -- so it does not live in `service/`. It is mounted directly by
the API, the only surface where it makes sense: an MCP tool or a CLI verb that
opened an interactive terminal would make none.

The PTY belongs to the Python server, not to the Tauri shell. The server is
already tied to the window by `PR_SET_PDEATHSIG` (see `src-tauri/src/server.rs`):
the agent thus dies with it without one more line of Rust, and it survives
closing the Chats window alone -- exactly the intended behavior.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import re
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import settings
from ..service.errors import NotFound, ServiceError
from . import screen
from .harnesses import (
    DEFAULT,
    accepted_effort,
    command_for,
    names_conversation,
    search_path,
    studio_context_args,
    studio_mcp_args,
    takes_prompt,
)
from .transcripts import HANDOVER, conversation_path, for_harness

logger = logging.getLogger("gamestudio.terminal")

# Bounds of a resize. Below them, nothing fits on screen any more; above them, a
# lying client would make the kernel allocate a grid of several million cells
# for a single tab.
MIN_COLS, MAX_COLS = 20, 500
MIN_ROWS, MAX_ROWS = 5, 200

# Size of the replay buffer: enough to rebuild a TUI's screen and its recent
# history. Beyond it the buffer is pruned, and whoever reconnects is told.
BUFFER_BYTES = 256 * 1024
# Margin before pruning: without it, every read once the capacity is reached
# would copy the whole buffer.
BUFFER_SLACK = 16 * 1024

# Per subscriber: beyond this, the client is not keeping up. The oldest item is
# then dropped rather than making the PTY wait -- live beats exhaustive.
QUEUE_MAX = 512

# A tab is a thread and a process: the cap protects the machine from a client
# that would open them in a loop.
MAX_SESSIONS = 12

# Period of the watcher that polls the native transcripts. Short enough for the
# badge to light up as soon as the agent is done, long enough that twelve tabs
# do not open twelve files per round.
WATCH_EVERY = 0.75

# Typing a message into an agent: the time it takes to draw its prompt when it
# has just been launched, then the gap between the text and its submission.
STARTUP_DELAY = 4.0
SUBMIT_DELAY = 0.3

# What gives away a host agent. A `claude` launched from a Claude Code session
# that inherits these variables believes it is nested and refuses to start:
# exactly the case when the studio is launched from an agent's terminal.
SCRUBBED = frozenset({"CLAUDECODE"})
SCRUBBED_PREFIX = ("CLAUDE_CODE_",)

# The studio's secrets: `config` loads the `.env` into the server's
# environment, and a tab would otherwise inherit the paid keys and the API
# token -- visible to any third-party CLI launched there. The studio's MCP
# server that the agent starts does not need them: `GAMESTUDIO_HOME` is enough
# for it to read the studio's `.env` again (`config.settings`).
SECRETS = frozenset({"RUNWARE_API_KEY", "TRIPO_API_KEY", "GAMESTUDIO_TOKEN"})

# Marks, in a subscriber's stream, that the process has ended. Reads are
# `bytes`, so a sentinel stands out unambiguously.
END_OF_PROCESS = object()

# The title learned from a first message: long enough to say what it is about,
# short enough to fit in a tab.
TITLE_MAX = 48
# How much of the current typing is kept to learn that title. A paste can be
# tens of kilobytes: beyond this, it is no longer a title.
TITLE_SCAN = 512

# An escape sequence cut between two reads is short: the rest arrives in the
# next read. Beyond this size, it is no longer a sequence but text, and holding
# it back would lose the typing.
CARRY_MAX = 64

# Escape sequences: an arrow, a function key, a bracketed paste, and above all
# the mouse -- an agent that enables mouse tracking (DeepSeek does) receives a
# report on each move, `ESC[<35;62;14M` in SGR, three raw bytes after `ESC[M` in
# X10. None of them may end up in a title. CSI parameters cover all of
# 0x30-0x3F: SGR's `<` is one of them.
#
# All these forms are *complete*. A sequence cut at the end of a read is not
# cleaned here but carried over (`_cut_escape`): that is the only way to
# recognize its tail when it arrives, instead of taking it for text.
_ANSI = re.compile(
    rb"\x1b\[M[\s\S]{3}"                    # X10 mouse
    rb"|\x1b\[[0-?]*[ -/]*[@-~]"              # CSI, SGR mouse included
    rb"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"    # OSC
    rb"|\x1bO[\s\S]"                         # SS3 (keypad arrows)
    rb"|\x1b[@-Z\\-_]"                       # one-byte escape
)

# What remains of a sequence when a read stops in the middle: a CSI without its
# final byte, an OSC without its terminator, an X10 report missing bytes, or
# the escape alone.
_INCOMPLETE = re.compile(
    rb"\x1b\[M[\s\S]{0,2}$"
    rb"|\x1b\[[0-?]*[ -/]*$"
    rb"|\x1b\][^\x07\x1b]*$"
    rb"|\x1b$"
)


def _cut_escape(raw: bytes) -> tuple[bytes, bytes]:
    """Return the clean text, and the tail of an incomplete escape sequence.

    The PTY returns what the program writes in blocks: `ESC[<35;62;14M` may
    arrive as `ESC`, then `[<35;62;14M`. Cleaning read by read then lets the
    tail through, and a tab's title fills up with the agent's mouse reports
    (e.g. `1;13M [<35;62;14M [<35;63`). So whatever starts a sequence without
    finishing it is set aside, and presented again with the next read, where it
    will be recognized and removed.
    """
    start = raw.rfind(b"\x1b")
    if start != -1:
        tail = raw[start:]
        # The tail is judged *before* cleaning: `ESC[M` followed by two bytes is
        # a valid CSI for the cleaner, which would eat it and leave the rest of
        # the mouse report behind.
        if len(tail) <= CARRY_MAX and _INCOMPLETE.fullmatch(tail):
            return _ANSI.sub(b"", raw[:start]), tail
    # Neither a known sequence nor a cut one: it is text, and `_plain` will turn
    # the escape into a space.
    return _ANSI.sub(b"", raw), b""


def _plain(raw: bytes) -> str:
    """What was typed, as clean text: no ANSI and no control characters."""
    text = _ANSI.sub(b"", raw).decode("utf-8", errors="replace")
    cleaned = "".join(char if char.isprintable() else " " for char in text)
    return " ".join(cleaned.split())


def _clamped(cols: int, rows: int) -> tuple[int, int]:
    return (min(max(int(cols), MIN_COLS), MAX_COLS),
            min(max(int(rows), MIN_ROWS), MAX_ROWS))


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    """Set the terminal's grid.

    `struct winsize` wants rows, columns, then pixels -- left at zero, since
    they are unknown and nothing reads them.
    """
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def absorb(buffer: bytearray, chunk: bytes) -> bool:
    """Append a read to the buffer and prune it. True if history was lost."""
    buffer += chunk
    if len(buffer) <= BUFFER_BYTES + BUFFER_SLACK:
        return False
    del buffer[: len(buffer) - BUFFER_BYTES]
    return True


@dataclass(eq=False)
class Subscriber:
    """A client attached to a session, and its queue.

    The queue is what guarantees that a slow client never freezes the tab:
    writing straight into the websocket, a client that stops reading would fill
    the kernel's buffer, the child would block on write, and the whole tab with
    it.
    """

    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(QUEUE_MAX))


class TerminalSession:
    """A process in a PTY, what it has shown, and who is listening."""

    def __init__(self, *, loop: asyncio.AbstractEventLoop | None, title: str,
                 command: list[str], cwd: str, cols: int, rows: int,
                 harness: str = "", env_unset: tuple[str, ...] = (),
                 effort: str = "", identifier: str = "", conversation: str = "") -> None:
        # A resumed tab keeps its identifier: that is what gives it back its
        # place, title and tint in the window, and what makes a single tab exist
        # where the old one sleeps.
        self.id = identifier or uuid.uuid4().hex[:12]
        self.title = title
        self.command = command
        # The followed harness, or empty when the command was given explicitly
        # -- the tab then knows what it runs, which the title already shows.
        self.harness = harness
        # The effort level asked for at opening. It is already in `command`, but
        # kept apart: it is what is replayed when resuming the tab, and what the
        # interface shows.
        self.effort = effort
        # The identifier of the agent's conversation, when the studio could name
        # it (`Harness.named`): that one, and only that one, is reopened when
        # resuming the tab, and its transcript is the one followed.
        self.conversation = conversation
        # True as long as the title comes from the studio ("claude 1"). The
        # first typed message replaces it, and only once: a title chosen by hand
        # must never be overwritten by typing.
        self.auto_title = False
        # What was typed and not yet submitted, to learn the title, and the tail
        # of an escape sequence waiting for the next read.
        self._typed = bytearray()
        self._carry = b""
        self.env_unset = env_unset
        self.cwd = cwd
        self.cols, self.rows = _clamped(cols, rows)
        self.created_at = time.time()
        self.buffer = bytearray()
        self.truncated = False
        self.seq = 0
        self.state = "running"
        self.exit_code: int | None = None
        self.subscribers: set[Subscriber] = set()
        self.master: int | None = None
        self.pid = 0
        # The turn's state, as the agent's native transcript tells it: `working`
        # or `waiting`, and when it was seen. None means unknown -- an explicit
        # command, or a harness without a readable transcript.
        self.turn: dict[str, Any] | None = None
        self._tail = (for_harness(harness, Path(cwd), self.created_at, conversation)
                      if harness else None)
        # Where to save the screen, fixed at creation (see `screen.save`).
        self._screens = screen.screens_dir()
        self._deposited: bytes | None = None
        self._deposited_at = 0.0
        self._forgotten = False
        # Set when the server stops. Without it, the reader thread would see the
        # end of the process just killed and save "exited" with a victim's exit
        # code -- which reads as "it finished".
        self._interrupted = False
        self._loop = loop
        self._lock = threading.Lock()
        self._spawn()

    # -------------------------------------------------------------- lifecycle

    def _spawn(self) -> None:
        master, slave = pty.openpty()
        # The size must be set before exec: the program reads it as soon as it
        # starts, to know what grid to draw on.
        _set_winsize(master, self.cols, self.rows)
        try:
            self.proc = subprocess.Popen(
                self.command,
                stdin=slave, stdout=slave, stderr=slave,
                cwd=self.cwd, env=self._env(),
                # setsid: the group equals the pid, so `killpg` reaches everything
                # the program launched. This is what makes closing a tab complete
                # rather than partial.
                start_new_session=True,
                close_fds=True,
            )
        except OSError as error:
            os.close(master)
            os.close(slave)
            raise ServiceError(
                f"cannot start ({self.command[0]}): {error}") from error
        # The parent does not keep the slave: otherwise the child's end would
        # never show on the master, which the parent itself would hold open.
        os.close(slave)
        self.master = master
        self.pid = self.proc.pid
        threading.Thread(target=self._pump, name=f"pty-{self.id}", daemon=True).start()

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in list(env):
            if key in SCRUBBED or key in SECRETS or key.startswith(SCRUBBED_PREFIX):
                del env[key]
        # What the harness must not inherit -- the ambient endpoint, which would
        # silently turn an Anthropic tab into another provider's.
        for key in self.env_unset:
            env.pop(key, None)
        env.update({
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "LANG": env.get("LANG") or "C.UTF-8",
            # Launched from the desktop, the studio does not inherit the shell's
            # PATH: without this one, `claude-deepseek` would not find the
            # `claude` of its `exec`, and a `codex` installed through nvm would be
            # invisible.
            "PATH": search_path(env),
            # `GAMESTUDIO_HOME` names the studio, not the current directory: a
            # tab opened in a project folder passes that root on to the MCP
            # server it starts, which thus finds its data.
            "PWD": self.cwd,
            "GAMESTUDIO_HOME": str(settings().project_root or self.cwd),
        })
        return env

    def _pump(self) -> None:
        """Read the PTY until the child dies, then reap it.

        A single mechanism to read *and* reap, in its own thread. On a Linux PTY
        master, end of life is signaled by `EIO`, not by an empty read; an
        `add_reader` on that fd would require a `remove_reader` on `EPOLLHUP`
        (or the event loop would spin at 100% CPU) and would still leave the
        `waitpid` to be done elsewhere.
        """
        master = self.master
        assert master is not None
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            # Under the lock: `snapshot` and `_deposit` read the buffer from other
            # threads, and must see it with its matching `seq`.
            with self._lock:
                if absorb(self.buffer, chunk):
                    self.truncated = True
                self.seq += 1
            self._broadcast(chunk)
            self._deposit()
        try:
            os.close(master)
        except OSError:
            pass
        self.master = None
        try:
            self.exit_code = self.proc.wait()
        except Exception:
            # The closing thread may have reaped it first: it then already knows
            # the code, and the tab is over anyway.
            self.exit_code = None
        self.state = "exited"
        # The last screen, saved without waiting for the period: it is the one
        # that will be read again, and a finished tab will produce nothing more
        # to catch up.
        self._deposit(force=True)
        self._broadcast(END_OF_PROCESS)

    def close(self, grace: float = 4.0) -> None:
        """Close the tab: the process group first, then by force.

        The group is targeted, not just the child: a `claude` being talked to
        has started MCP servers, and leaving them behind would create exactly the
        orphans the rest of the studio takes care to avoid.
        """
        if self.state == "running":
            self._signal_group(signal.SIGHUP)
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline and self.proc.poll() is None:
                time.sleep(0.05)
            if self.proc.poll() is None:
                self._signal_group(signal.SIGKILL)
        try:
            self.proc.wait(timeout=grace)
        except Exception:
            # Already reaped by the reader thread, or it no longer answers: either
            # way there is nothing left to wait for here.
            pass

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(self.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    # ------------------------------------------------------------- dimensions

    def resize(self, cols: int, rows: int) -> tuple[int, int]:
        size = _clamped(cols, rows)
        changed = size != (self.cols, self.rows)
        self.cols, self.rows = size
        master = self.master
        if master is not None:
            try:
                _set_winsize(master, self.cols, self.rows)
                self._signal_group(signal.SIGWINCH)
            except OSError:
                pass
        if changed:
            # A screen that watches the tab without driving it -- the agents at
            # work page -- measures nothing: it follows the grid of whoever
            # drives, and must therefore learn that it changed.
            self._broadcast({"type": "size", "cols": self.cols, "rows": self.rows})
        return self.cols, self.rows

    def redraw(self) -> None:
        """Force the program to redraw its screen.

        `TIOCSWINSZ` only raises `SIGWINCH` if the size *changes*, and without a
        controlling terminal the kernel has no foreground process group to send
        it to: the signal thus never arrives by itself. Hence the dummy size,
        then the real one, then the explicit signal.
        """
        master = self.master
        if master is None or self.state != "running":
            return
        try:
            _set_winsize(master, 1, 1)
            _set_winsize(master, self.cols, self.rows)
            self._signal_group(signal.SIGWINCH)
        except OSError:
            pass

    # ------------------------------------------------------------ subscription

    def subscribe(self) -> Subscriber:
        """Attach a subscriber. To be called **before** reading the buffer.

        In this order, the worst case is replaying twice what arrived in between;
        in the reverse order, that same interval would be lost.
        """
        subscriber = Subscriber()
        self.subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self.subscribers.discard(subscriber)

    def snapshot(self) -> tuple[bytes, bool, int]:
        with self._lock:
            return bytes(self.buffer), self.truncated, self.seq

    def write(self, data: bytes) -> None:
        master = self.master
        if master is None:
            return
        # A keystroke hands control back to the agent: it is working again,
        # whatever its transcript said a second before. Without this the badge
        # would stay lit between the moment one answers and the agent's first
        # message.
        if self._tail is not None and data.strip():
            self._set_turn("working")
        self._learn_title(data)
        try:
            os.write(master, data)
        except OSError:
            pass

    # ------------------------------------------------------------------ title

    def _learn_title(self, data: bytes) -> None:
        """Title the tab after its first message.

        "claude 1" says nothing: after eight tabs, one no longer knows which one
        was about splitting the knight sheet. The first message does -- it is
        what was just asked there.

        Two precautions: a slash command (`/help`) names nothing, and a title
        chosen by hand is never overwritten -- the `auto_title` flag drops at
        the first learned title, and a user who renames drops it too.
        """
        if not self.auto_title:
            return
        # Escape sequences are removed from the accumulated buffer, not read by
        # read: the same sequence may be cut between two PTY reads, and its tail
        # would then pass for text. Whatever starts a sequence without finishing
        # it waits for the next read.
        body, self._carry = _cut_escape(self._carry + data)
        self._typed.extend(body)
        newline = next((index for index, byte in enumerate(self._typed)
                        if byte in (10, 13)), None)
        if newline is None:
            # Without a line end, it is not a message yet. What is kept is
            # bounded: a paste would grow this buffer without end.
            if len(self._typed) > TITLE_SCAN:
                del self._typed[:-TITLE_SCAN]
            return

        line, self._typed = bytes(self._typed[:newline]), bytearray()
        title = _plain(line)
        # A title without a single letter is no title: what is left is debris
        # of a sequence, not a message.
        if (len(title) < 3 or title.startswith("/")
                or not any(char.isalpha() for char in title)):
            return
        self.title = title[:TITLE_MAX]
        self.auto_title = False
        # The title goes to disk right away: a studio closed just after must not
        # bring back a nameless tab.
        self._deposit(force=True)

    # ------------------------------------------------------------------- turn

    def _set_turn(self, state: str) -> None:
        self.turn = {"state": state, "at": time.time()}

    def poll_turn(self) -> None:
        """Ask the native transcript whether the agent is working or waiting.

        The first read of a transcript covers its end, not its start: an agent
        may have finished its turn before the file was found, and waiting for a
        write that will never come would leave the tab "working" forever.
        """
        tail = self._tail
        if tail is None or self.state != "running":
            return
        reason = tail.poll()
        if reason is None:
            return
        self._set_turn("waiting" if reason == HANDOVER else "working")

    def has_journal(self) -> bool:
        return self._tail is not None

    # ------------------------------------------------------------------- disk

    def _record(self, state: str | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "argv": list(self.command),
            "harness": self.harness,
            "cwd": self.cwd,
            "cols": self.cols,
            "rows": self.rows,
            "created_at": self.created_at,
            "closed_at": time.time(),
            "state": state or ("interrupted" if self._interrupted else self.state),
            "exit_code": self.exit_code,
            "seq": self.seq,
            "truncated": self.truncated,
            "turn": self.turn,
            # The effort level and the title's origin survive closing: resuming a
            # tab relaunches it identically, and a learned title is not taken for
            # an automatic one.
            "effort": self.effort,
            "auto_title": self.auto_title,
            "conversation": self.conversation,
        }

    def _deposit(self, *, state: str | None = None, force: bool = False) -> None:
        """Save the screen, at most once per period.

        The buffer is copied before being compared: the reader thread keeps
        feeding it, and comparing then writing two different states of the same
        buffer would write a file that is already out of date.

        The period keeps a chatty command -- a build, a `find` -- from copying
        its buffer on every read. Nothing is lost: what is not saved is still in
        memory, and a finished tab, like the server stopping, saves without
        waiting.
        """
        if self._forgotten:
            # The tab was closed: the reader thread may still see the process end
            # right after, and would save again the screen just forgotten. The tab
            # would then come back at the next start.
            return
        with self._lock:
            snapshot = bytes(self.buffer)
        if not force and snapshot == self._deposited:
            return
        now = time.monotonic()
        if not force and now - self._deposited_at < screen.SAVE_EVERY:
            return
        self._deposited = snapshot
        self._deposited_at = now
        screen.save(self._record(state), snapshot, self._screens)

    def _broadcast(self, item: bytes | object) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for subscriber in list(self.subscribers):
            try:
                loop.call_soon_threadsafe(self._offer, subscriber, item)
            except RuntimeError:
                # The loop closed meanwhile: the server is stopping.
                return

    @staticmethod
    def _offer(subscriber: Subscriber, item: bytes | object) -> None:
        try:
            subscriber.queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                subscriber.queue.get_nowait()
                subscriber.queue.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    # ---------------------------------------------------------------- reading

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            # The program alone for the label, the whole argv to relaunch the tab
            # identically: one is shown, the other is replayed.
            "command": self.command[0],
            "argv": list(self.command),
            "harness": self.harness,
            "effort": self.effort,
            "conversation": self.conversation,
            "cwd": self.cwd,
            "state": self.state,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "seq": self.seq,
            "turn": self.turn,
            # The grid the screen was drawn for. The client needs it to replay
            # the buffer at that size: a TUI positions itself absolutely, and
            # reading it back in another grid only gives a string of pieces. It
            # then fits it to its own box.
            "cols": self.cols,
            "rows": self.rows,
            # Mirrors the saved tab: a live tab has nothing to resume, and the
            # front reads the same shape in both cases.
            "revivable": False,
        }


class ArchivedSession:
    """A tab of which only the screen was kept.

    Same surface as a live tab -- which lets the routes serve it without
    knowing: `snapshot` returns what was saved, and `_forward` sees an already
    finished tab, so it replays the screen then closes the stream. Nothing is
    writable: there is no process behind it any more.
    """

    # A state that is neither "running" nor "exited": the tab did not finish, it
    # was interrupted. The distinction matters -- a finished tab returned its
    # exit code, this one returned nothing at all.
    state = "interrupted"

    def __init__(self, body: dict[str, Any]) -> None:
        self.id = str(body.get("id") or "")
        self.title = str(body.get("title") or self.id)
        self.command = [str(part) for part in body.get("argv") or []]
        self.harness = str(body.get("harness") or "")
        # What launched the tab: resuming it must bring back the same agent, with
        # the same effort.
        self.effort = str(body.get("effort") or "")
        self.auto_title = bool(body.get("auto_title"))
        # Empty when the tab's conversation was not named: it is not reopened,
        # it starts a new conversation.
        self.conversation = str(body.get("conversation") or "")
        self.cwd = str(body.get("cwd") or "")
        self.cols = int(body.get("cols") or 80)
        self.rows = int(body.get("rows") or 24)
        self.created_at = float(body.get("created_at") or 0)
        self.exit_code = body.get("exit_code")
        self.seq = int(body.get("seq") or 0)
        self.truncated = bool(body.get("truncated"))
        self.turn = body.get("turn") if isinstance(body.get("turn"), dict) else None
        self.buffer = bytearray(body.get("screen") or b"")
        self.master = None
        self.pid = 0

    def subscribe(self) -> Subscriber:
        return Subscriber()

    def unsubscribe(self, subscriber: Subscriber) -> None:
        pass

    def snapshot(self) -> tuple[bytes, bool, int]:
        return bytes(self.buffer), self.truncated, self.seq

    def write(self, data: bytes) -> None:
        pass

    def resize(self, cols: int, rows: int) -> tuple[int, int]:
        """A saved tab's grid cannot be set.

        There is no process behind it any more, hence nobody to announce a size
        to. And above all: this grid *describes the buffer* -- it is the one the
        screen was drawn for. Letting the client's overwrite it would make the
        next `snapshot` lie, and the wide buffer would be read back in a narrow
        grid: exactly what breaks a screen into pieces. A saved tab is read back
        at its size, and the client then fits it to its window.
        """
        return self.cols, self.rows

    def redraw(self) -> None:
        pass

    def close(self, grace: float = 0.0) -> None:
        pass

    def poll_turn(self) -> None:
        pass

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "command": self.command[0] if self.command else "",
            "argv": list(self.command),
            "harness": self.harness,
            "effort": self.effort,
            "conversation": self.conversation,
            "cwd": self.cwd,
            "state": self.state,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "seq": self.seq,
            "turn": self.turn,
            # The recorded grid: it is what makes the replay faithful, even in a
            # window that no longer has the same width.
            "cols": self.cols,
            "rows": self.rows,
            # What the front needs to offer resuming the work rather than leaving
            # it read-only.
            "revivable": bool(self.command),
        }


class TerminalManager:
    """The tabs: those running, and those only kept.

    The latter come from disk and date from the previous run. They are there
    because the server dies with the window: without them, each launch would
    return an empty list, and the work shown in an agent just left would be
    lost without a trace.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._archived: dict[str, ArchivedSession] = {
            archived.id: archived for archived in map(ArchivedSession, screen.load_all())
        }
        self._watcher: threading.Thread | None = None
        self._lock = threading.Lock()
        # Called with a tab's id when its agent hands control back: what waits
        # for that moment (a queue of requests) is told, instead of polling.
        self._handovers: list[Callable[[str], None]] = []
        # The server's loop, for the tabs the studio opens by itself (outside a
        # request): their output still reaches the Chats window.
        self.loop: asyncio.AbstractEventLoop | None = None

    def on_handover(self, listener: Callable[[str], None]) -> None:
        """Call `listener(session_id)` each time an agent hands control back."""
        if listener not in self._handovers:
            self._handovers.append(listener)

    # ---------------------------------------------------------------- watcher

    def _ensure_watcher(self) -> None:
        """Start the watcher that polls the native transcripts.

        A single thread for all tabs, started only at the first tab that has a
        transcript: opening the studio to launch a `bash` is no reason to run a
        loop.
        """
        if self._watcher is not None:
            return
        self._watcher = threading.Thread(target=self._watch, name="terminal-watch",
                                         daemon=True)
        self._watcher.start()

    def _watch(self) -> None:
        while True:
            time.sleep(WATCH_EVERY)
            with self._lock:
                sessions = list(self._sessions.values())
            for session in sessions:
                before = (session.turn or {}).get("state")
                try:
                    session.poll_turn()
                except Exception:
                    # An unreadable transcript must not take the watcher down,
                    # let alone the server: the other tabs carry on.
                    logger.exception("unreadable turn (%s)", session.id)
                    continue
                if before != "waiting" and (session.turn or {}).get("state") == "waiting":
                    self._handed_over(session.id)

    def _handed_over(self, session_id: str) -> None:
        for listener in list(self._handovers):
            try:
                listener(session_id)
            except Exception:
                logger.exception("handover listener failed (%s)", session_id)

    def create(self, *, loop: asyncio.AbstractEventLoop | None,
               command: list[str] | None = None, harness: str = DEFAULT,
               title: str = "", cwd: str = "", cols: int = 80, rows: int = 24,
               session_id: str | None = None, effort: str = "",
               conversation: str = "", resume: bool = False,
               first_message: str = "") -> TerminalSession:
        followed = ""
        home = settings().project_root or Path.cwd()
        root = Path(cwd).expanduser() if cwd else home
        if not root.is_dir():
            raise ServiceError(f"folder not found: {root}")
        if command:
            # An explicit command overrides the harness: the escape hatch for
            # tests, which launch `bash` rather than an agent, and it keeps the
            # environment as is -- what one wants when debugging.
            env_unset: tuple[str, ...] = ()
            resolved = shutil.which(command[0], path=search_path())
            if resolved is None:
                raise ServiceError(
                    f"“{command[0]}” is not found on the PATH: nothing to start")
            command = [resolved, *command[1:]]
            effort = ""
            conversation = ""
        else:
            if names_conversation(harness):
                # A conversation is reopened only if it exists: a tab closed
                # before any message left no transcript, and `--resume` on an
                # unknown name would fail. It is then created under the same
                # name.
                conversation = conversation or str(uuid.uuid4())
                resume = resume and conversation_path(root, conversation).is_file()
            else:
                conversation, resume = "", False
            command, env_unset = command_for(harness, effort, conversation, resume, first_message)
            followed = harness
        # A tab opened in a project folder keeps the studio's tools and context:
        # both are passed on the command line, the context before the MCP
        # servers (`--mcp-config` reads to the end of the line).
        if followed and root.resolve() != home.resolve():
            command = [*command, *self._context_args(followed, root, home),
                        *studio_mcp_args(followed, home)]
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                raise ServiceError(
                    f"{MAX_SESSIONS} sessions are already open: close one")
            generated = not title
            session = TerminalSession(
                loop=loop or self.loop, title=title or self._next_title(command[0]),
                command=command, cwd=str(root), cols=cols, rows=rows,
                harness=followed, env_unset=env_unset, effort=effort,
                identifier=session_id or "", conversation=conversation)
            # A title given by hand is a wanted title; the one just made up
            # ("claude 1") waits for its first message.
            session.auto_title = generated
            self._sessions[session.id] = session
        if session.has_journal():
            self._ensure_watcher()
        if first_message and not (followed and takes_prompt(followed)):
            # The agent does not take its message as an argument: it is typed,
            # once the agent has had time to draw its prompt.
            self.type_in(session.id, first_message, delay=STARTUP_DELAY)
        return session

    @staticmethod
    def _context_args(harness: str, root: Path, home: Path) -> list[str]:
        """The studio's context for a tab outside the root, or nothing.

        A failure here must not keep the tab from opening: the agent keeps its
        tools, including `studio_briefing`, which gives the same knowledge.
        """
        from ..service import briefing

        try:
            return studio_context_args(harness, home, briefing.write_session_context(root))
        except Exception:
            logger.exception("context not written for the tab opened in %s", root)
            return []

    def type_in(self, session_id: str, text: str, delay: float = 0.0) -> dict[str, Any]:
        """Type a message into a live tab, then submit it.

        A single line: a newline would submit the start of the message. The
        submission is sent separately, a moment after the text -- received in
        the same read, a TUI takes it for a pasted newline.
        """
        session = self.get(session_id)
        if not isinstance(session, TerminalSession) or session.state != "running":
            raise ServiceError("this discussion is no longer active: resume it, or open a new one")
        line = " ".join(text.split())

        def keystrokes() -> None:
            session.write(line.encode("utf-8"))
            time.sleep(SUBMIT_DELAY)
            session.write(b"\r")
            # A submitted message puts the agent to work: its next hand-back is
            # a change of state, even when it answers in a single message.
            if session.has_journal():
                session._set_turn("working")

        timer = threading.Timer(delay, keystrokes)
        timer.daemon = True
        timer.start()
        return session.describe()

    def _next_title(self, program: str) -> str:
        name = Path(program).name
        return self._free(f"{name} 1", start=1)

    def _free(self, title: str, start: int = 1) -> str:
        """A title that does not duplicate another.

        Saved tabs count: resuming a tab whose title sleeps in the list would
        give two identical entries, between which one could no longer choose.
        """
        taken = {session.title for session in self._sessions.values()}
        taken.update(archived.title for archived in self._archived.values())
        if title not in taken:
            return title
        stem = title.rstrip(" 0123456789").strip() or title
        index = start
        while f"{stem} {index}" in taken:
            index += 1
        return f"{stem} {index}"

    def get(self, session_id: str) -> TerminalSession | ArchivedSession:
        with self._lock:
            session = self._sessions.get(session_id)
            archived = self._archived.get(session_id)
        if session is not None:
            return session
        if archived is not None:
            return archived
        raise NotFound(f"unknown session: {session_id}")

    def listing(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values()) + list(self._archived.values())
        sessions.sort(key=lambda session: session.created_at)
        return [session.describe() for session in sessions]

    def rename(self, session_id: str, title: str) -> dict[str, Any]:
        session = self.get(session_id)
        session.title = title.strip() or session.title
        # A title chosen by hand is a wanted title: the first message must no
        # longer replace it. Same rule as for the learned title.
        session.auto_title = False
        return session.describe()

    def revive(self, session_id: str, *, loop: asyncio.AbstractEventLoop | None
               ) -> TerminalSession:
        """Resume a saved tab: it is the same tab, with a new agent.

        The process does not come back. But the tab does: it takes back its
        identifier, hence its place, title and tint in the window -- and the
        saved tab leaves the list instead of leaving a numbered sibling next to
        it. The agent is relaunched through its *harness* (the program is
        resolved again in the PATH and the environment cleaned as on first
        launch) with what it takes to **reopen the conversation it held**:
        otherwise "Resume" would give a new agent that forgot everything it had
        just been asked.
        """
        with self._lock:
            archived = self._archived.pop(session_id, None)
        if archived is None:
            raise NotFound(f"unknown session: {session_id}")
        common = {
            "loop": loop,
            "title": archived.title,
            "cwd": archived.cwd,
            # The recorded grid: the new agent redraws its screen at the same
            # size as the one just left.
            "cols": archived.cols,
            "rows": archived.rows,
            "session_id": session_id,
        }
        try:
            if archived.harness:
                effort = accepted_effort(archived.harness, archived.effort)
                session = self.create(harness=archived.harness, effort=effort,
                                      conversation=archived.conversation,
                                      resume=True, **common)
            else:
                # An explicit command has no conversation to reopen: it restarts
                # as is.
                session = self.create(command=archived.command, harness="", **common)
        except Exception:
            # The tab could not come up: it stays what it was. Removing it from
            # the list before knowing whether the launch holds would make a tab
            # vanish that nobody asked to close.
            with self._lock:
                self._archived[session_id] = archived
            raise
        # A title already learned or chosen stays: it is not made automatic
        # again, or the next message would rename it a second time.
        session.auto_title = archived.auto_title
        return session

    def close(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        with self._lock:
            self._sessions.pop(session_id, None)
            self._archived.pop(session_id, None)
        if isinstance(session, TerminalSession):
            # A live tab must no longer save itself: the reader thread may still
            # see the process end right after, and would save again the screen
            # just erased.
            session._forgotten = True
        # The saved screen belongs to the session, whether it is still running
        # or comes from a previous launch: a tab found on disk *is* its file.
        # Erasing it only for live tabs would answer `closed` while leaving a
        # saved tab on disk, to come back at the next start with no way to get
        # rid of it for good.
        screen.forget(session_id)
        session.close()
        return {"id": session_id, "closed": True}

    def shutdown(self) -> None:
        """Close everything: called when the server stops.

        A signal to all first, a single wait next: closing twelve tabs one after
        the other would take twelve times the grace period, and the server's
        shutdown has no time to lose.
        """
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            if session.state == "running":
                # Save before killing, under the state that tells the truth: what
                # was running did not finish, it was interrupted. This is the only
                # window where the screen is still the one the user saw.
                session._interrupted = True
                session._deposit(force=True)
                session._signal_group(signal.SIGHUP)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and any(
                session.proc.poll() is None for session in sessions):
            time.sleep(0.05)
        for session in sessions:
            session.close(grace=0.5)


manager = TerminalManager()
