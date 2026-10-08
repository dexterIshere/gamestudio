"""Job queue backed by SQLite.

Generation takes minutes (a 3D mesh, a LoRA training): nothing can happen
within an HTTP request. Jobs are queued and picked up by a worker -- and
several workers serve the same queue: the API's, and one per MCP server, so
one per agent tab.

The claim is atomic through `UPDATE ... WHERE state='pending'` with a lease
(`lease_until`), which the worker extends while the job runs (`hold`). An
expired lease therefore means a dead worker, never a long job: without this
heartbeat, a two-hour training would be picked up by another worker when the
lease ran out, and paid twice.

A failing criterion goes back to the user instead of looping while paying: a
paid job is never replayed by the queue -- its failure, or its worker's death,
moves it to `failed`. Only free jobs (`FREE_KINDS`) go back to pending, within
their attempt limit.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from ..domain.models import Job, JobState
from ..store.db import Database

# The lease only covers the interval between two heartbeats, with a margin: a
# SQLite write can wait up to 30 s for its lock (`busy_timeout`).
DEFAULT_LEASE = timedelta(minutes=5)
HEARTBEAT = 30.0
MAX_ATTEMPTS = 3

# Jobs that cost nothing and can be restarted safely. Any other -- including a
# kind added later -- is treated as paid: forgetting an entry here costs an
# attempt, never a bill.
FREE_KINDS = frozenset({"ping", "render_sprites"})

# The failure of a job whose worker died: paid, or free and out of attempts.
INTERRUPTED = "interrupted: the worker running it stopped; it is not restarted"


def _now() -> datetime:
    return datetime.now(UTC)


class JobQueue:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(self, kind: str, payload: dict[str, Any], *, project: str = "",
                step: str = "") -> Job:
        job = Job(id=str(uuid.uuid4()), kind=kind, payload=payload, project=project,
                  step=step)
        return self.db.save_job(job)

    def claim(self, *, lease: timedelta = DEFAULT_LEASE,
              kinds: list[str] | None = None) -> Job | None:
        """Claim a pending job. None if the queue is empty."""
        conn = self.db.connect()
        sql = "SELECT id FROM jobs WHERE state = 'pending'"
        params: list[Any] = []
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " ORDER BY created_at LIMIT 1"

        conn.execute("BEGIN IMMEDIATE")
        try:
            now = _now()
            self._release_expired(conn, now)
            row = conn.execute(sql, params).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            job_id = row["id"]
            conn.execute(
                "UPDATE jobs SET state = 'running', attempts = attempts + 1,"
                " lease_until = ?, updated_at = ? WHERE id = ? AND state = 'pending'",
                ((now + lease).isoformat(), now.isoformat(), job_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self.db.get_job(job_id)

    @staticmethod
    def _release_expired(conn: Any, now: datetime) -> None:
        """Jobs whose worker died: replayed if free, failed otherwise.

        A killed worker must not freeze the queue; but an interrupted paid job
        may have been billed, and restarting it would pay it twice.
        """
        expired = "state = 'running' AND lease_until IS NOT NULL AND lease_until < ?"
        free = ",".join("?" * len(FREE_KINDS))
        conn.execute(
            f"UPDATE jobs SET state = 'pending', lease_until = NULL, updated_at = ?"
            f" WHERE {expired} AND kind IN ({free}) AND attempts < ?",
            (now.isoformat(), now.isoformat(), *sorted(FREE_KINDS), MAX_ATTEMPTS),
        )
        conn.execute(
            f"UPDATE jobs SET state = 'failed', lease_until = NULL, error = ?,"
            f" updated_at = ? WHERE {expired}",
            (INTERRUPTED, now.isoformat(), now.isoformat()),
        )

    def heartbeat(self, job: Job, *, lease: timedelta = DEFAULT_LEASE) -> None:
        """Extend the lease of a running job -- and only a running one: a job
        already finished or failed never goes back to `running`."""
        self.db.connect().execute(
            "UPDATE jobs SET lease_until = ? WHERE id = ? AND state = 'running'",
            ((_now() + lease).isoformat(), job.id),
        )

    @contextmanager
    def hold(self, job: Job, *, lease: timedelta = DEFAULT_LEASE,
             every: float = HEARTBEAT) -> Iterator[None]:
        """Keep a job's lease alive while the block runs.

        A separate thread: the handler waits on Blender or the network for
        hours, and would never yield to extend it itself.
        """
        done = threading.Event()

        def beat() -> None:
            while not done.wait(every):
                try:
                    self.heartbeat(job, lease=lease)
                except Exception:
                    # A missed heartbeat is not fatal: the next one catches up,
                    # the lease has a margin of several heartbeats.
                    pass

        thread = threading.Thread(target=beat, name=f"lease-{job.id[:8]}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            done.set()
            thread.join()

    def complete(self, job: Job, result: dict[str, Any], *, cost: float = 0.0,
                 needs_review: bool = False) -> None:
        job.state = JobState.NEEDS_REVIEW if needs_review else JobState.DONE
        job.result = result
        job.cost_usd = cost
        self.db.save_job(job)
        self.db.connect().execute("UPDATE jobs SET lease_until = NULL WHERE id = ?", (job.id,))

    def fail(self, job: Job, error: str, *, cost: float = 0.0) -> None:
        """Record a failure, with what it already cost when known.

        A free job goes back to pending while it has attempts left; a paid job
        fails for good on its first attempt.
        """
        row = self.db.connect().execute(
            "SELECT attempts FROM jobs WHERE id = ?", (job.id,)
        ).fetchone()
        attempts = row["attempts"] if row else MAX_ATTEMPTS

        if job.kind in FREE_KINDS and attempts < MAX_ATTEMPTS:
            self.db.connect().execute(
                "UPDATE jobs SET state = 'pending', error = ?, lease_until = NULL,"
                " updated_at = ? WHERE id = ?",
                (error[:2000], _now().isoformat(), job.id),
            )
            return
        job.state = JobState.FAILED
        job.error = error[:2000]
        job.cost_usd = cost
        self.db.save_job(job)
        self.db.connect().execute(
            "UPDATE jobs SET lease_until = NULL WHERE id = ?", (job.id,)
        )

    def stats(self, project: str | None = None) -> dict[str, int]:
        sql = "SELECT state, COUNT(*) AS n FROM jobs"
        params: list[Any] = []
        if project:
            sql += " WHERE project = ?"
            params.append(project)
        sql += " GROUP BY state"
        return {r["state"]: r["n"] for r in self.db.connect().execute(sql, params).fetchall()}
