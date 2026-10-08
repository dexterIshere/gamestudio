"""Job queue: state, detail, and a stream of changes.

Each project has its queue, in the database of its `.gamestudio/`, and every
job lives in its project's queue. The reads below gather the queues of every
project, or stick to one project when asked.

The queue lives in SQLite and the workers are threads: rather than add an event
broadcast the CLI and the MCP server would not use, the stream rereads the
queue and emits only when it changed. An interface plugged into it sees jobs
progress without polling itself.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from ..domain.models import Job, JobState
from . import library
from .context import space, studio
from .errors import NotFound

RECENT_LIMIT = 15
HEARTBEAT = 20.0   # seconds without change before a keep-alive emission
FINGERPRINT = 32   # truncated sha256 of an asset, see store/assets.py
HEX = frozenset("0123456789abcdef")

# What the report handles separately: the cost, what failed, what was refused,
# and an operation's note. Everything else goes, flattened, into the facts.
RESERVED = ("cost_usd", "errors", "refused", "note")


def active(states: dict[str, int]) -> int:
    """The jobs a worker holds or will take, from `JobQueue.stats`."""
    return states.get(JobState.PENDING.value, 0) + states.get(JobState.RUNNING.value, 0)


def summary(job: Job) -> dict[str, Any]:
    return {"id": job.id, "kind": job.kind, "project": job.project, "step": job.step,
            "state": job.state.value, "error": job.error or None,
            "cost_usd": job.cost_usd, "updated_at": job.updated_at.isoformat()}


def _jobs(project: str | None, state: JobState | None, limit: int) -> list[Job]:
    """The jobs of one project, or of every project, newest first."""
    if project:
        return space(project).db.list_jobs(project=project, state=state, limit=limit)
    found = [job for held in studio().spaces()
             for job in held.db.list_jobs(state=state, limit=limit)]
    found.sort(key=lambda job: job.updated_at, reverse=True)
    return found[:limit]


def status(project: str | None = None) -> dict[str, Any]:
    """State of the queue: counts per state, total cost, latest jobs.

    Counts and cost cover every job, not just the latest ones read.
    """
    if project:
        st = space(project)
        states = st.queue.stats(project)
        cost = st.db.total_cost(project)
    else:
        states = {}
        cost = 0.0
        for found in studio().spaces():
            for name, count in found.queue.stats().items():
                states[name] = states.get(name, 0) + count
            cost += found.db.total_cost()
    return {
        "states": states,
        "cost_usd": round(cost, 4),
        "recent": [summary(j) for j in _jobs(project, None, RECENT_LIMIT)],
    }


def listing(project: str | None = None, state: str | None = None,
            limit: int = 50) -> list[dict[str, Any]]:
    """The jobs, filtered by project and state."""
    parsed = JobState(state) if state else None
    return [summary(j) for j in _jobs(project, parsed, min(limit, 300))]


def _readable(value: Any) -> str:
    """A scalar as it reads in a report."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _leaves(value: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Flatten a result into lines: `{"a": {"b": 1}}` gives `a.b = 1`.

    A list of scalars stays one line -- `frame_size = 80, 84` reads better than
    two numbered entries.
    """
    key = prefix.rstrip(".")
    if isinstance(value, dict):
        for name, item in value.items():
            yield from _leaves(item, f"{prefix}{name}.")
    elif isinstance(value, list) and value and all(
            not isinstance(item, (dict, list)) for item in value):
        yield key, ", ".join(_readable(item) for item in value)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{prefix}{index}.")
    else:
        yield key, value


def _fingerprints(value: Any, project: str) -> dict[str, str]:
    """The store fingerprints a result cites, with their kind.

    None says what it holds, which is why they must be resolved before being
    shown. The kind is read here, in the same pass -- a fingerprint the
    database does not know is not one.
    """
    # A job's fingerprints belong to its project: no other project holds them.
    if not project:
        return {}
    db = space(project).db
    found: dict[str, str] = {}

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for entry in item.values():
                walk(entry)
        elif isinstance(item, list):
            for entry in item:
                walk(entry)
        elif (isinstance(item, str) and len(item) == FINGERPRINT
                and all(char in HEX for char in item) and item not in found):
            asset = db.get_asset(item)
            if asset is not None:
                found[item] = asset.kind

    walk(value)
    return found


def report(job: Job) -> dict[str, Any]:
    """What a job produced, in plain terms.

    A `result` is a dictionary whose keys change with each operation. The
    report draws what reads everywhere: the cost, the produced files --
    resolved to library paths, never store fingerprints --, what failed, what
    was refused, and the rest flattened. A file outside the library is reported
    as such: keeping quiet would pass an invisible production off as filed.
    """
    leaves = list(_leaves(job.result))
    fingerprints = _fingerprints(job.result, job.project)
    placed = (library.locate(job.project, list(fingerprints))
              if job.project and fingerprints else {})

    files = [{"asset_id": asset_id, "kind": kind, **placed[asset_id]}
             for asset_id, kind in fingerprints.items() if asset_id in placed]
    absent = [{"asset_id": asset_id, "kind": kind}
              for asset_id, kind in fingerprints.items() if asset_id not in placed]

    facts = [{"key": key, "value": _readable(value)} for key, value in leaves
             if key.split(".")[0] not in RESERVED
             and not (isinstance(value, str)
                      and any(f in value for f in fingerprints))]
    errors = [_readable(value) for key, value in leaves
              if key == "errors" or key.startswith("errors.")]
    refused = [{"what": key.split(".", 1)[1], "why": _readable(value)}
               for key, value in leaves if key.startswith("refused.")]
    note = next((value for key, value in leaves if key == "note" and value), None)

    return {
        "state": job.state.value,
        "error": job.error or None,
        "cost_usd": job.cost_usd,
        "note": note,
        "errors": errors,
        "refused": refused,
        "files": files,
        "absent": absent,
        "facts": facts,
    }


def detail(job_id: str) -> dict[str, Any]:
    """A job in detail: payload, result, error, and its report."""
    found = studio().find_job(job_id)
    if found is None:
        raise NotFound(f"task {job_id} not found")
    job = found[1]
    return {**summary(job), "payload": job.payload, "result": job.result,
            "report": report(job), "created_at": job.created_at.isoformat()}


class Changes:
    """Detect that a queue moved, so as to emit only on a real change.

    The first call always emits: an observer plugging in must start from a full
    state. After that only a different queue triggers an emission, and a
    periodic heartbeat keeps the connection open through proxies that cut
    silent streams.

    The logic lives here because two observers share it: the API's SSE stream,
    asynchronous, and `watch` below, synchronous.
    """

    def __init__(self, *, heartbeat: float = HEARTBEAT) -> None:
        self.heartbeat = heartbeat
        self._previous: str | None = None
        self._last_sent = 0.0

    def emit(self, snapshot: dict[str, Any]) -> bool:
        fingerprint = repr(snapshot)
        now = time.monotonic()
        if fingerprint == self._previous and now - self._last_sent < self.heartbeat:
            return False
        self._previous = fingerprint
        self._last_sent = now
        return True


def watch(project: str | None = None, *, interval: float = 1.0,
          heartbeat: float = HEARTBEAT) -> Iterator[dict[str, Any]]:
    """Emit the queue's state on every change, forever."""
    changes = Changes(heartbeat=heartbeat)
    while True:
        current = status(project)
        if changes.emit(current):
            yield current
        time.sleep(interval)
