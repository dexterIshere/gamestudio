"""SQLite persistence: jobs, assets, characters, style packs.

SQLite rather than Postgres+Redis: the studio runs on a development machine, a
single worker is enough, and WAL mode handles one writer and a few readers
well. The job queue uses the same database (see jobs/queue.py).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain.models import Asset, Character, Job, JobState, StylePack

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    project     TEXT NOT NULL DEFAULT '',
    step        TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    result      TEXT NOT NULL DEFAULT '{}',
    error       TEXT NOT NULL DEFAULT '',
    cost_usd    REAL NOT NULL DEFAULT 0,
    attempts    INTEGER NOT NULL DEFAULT 0,
    lease_until TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project);

CREATE TABLE IF NOT EXISTS assets (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    path       TEXT NOT NULL,
    mime       TEXT NOT NULL DEFAULT '',
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);

CREATE TABLE IF NOT EXISTS characters (
    id         TEXT NOT NULL,
    project    TEXT NOT NULL,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project, id)
);

CREATE TABLE IF NOT EXISTS style_packs (
    id         TEXT PRIMARY KEY,
    project    TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Pipeline graph cache: a step identified by the hash of its inputs is never
-- recomputed. That is what makes re-running a recipe incremental.
CREATE TABLE IF NOT EXISTS step_cache (
    fingerprint TEXT PRIMARY KEY,
    step        TEXT NOT NULL,
    result      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._local = threading.local()
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    # ------------------------------------------------------------------- assets

    def save_asset(self, asset: Asset) -> Asset:
        self.connect().execute(
            "INSERT OR REPLACE INTO assets (id, kind, path, mime, meta, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (asset.id, asset.kind, str(asset.path), asset.mime,
             json.dumps(asset.meta), asset.created_at.isoformat()),
        )
        return asset

    def get_asset(self, asset_id: str) -> Asset | None:
        row = self.connect().execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            return None
        return Asset(
            id=row["id"], kind=row["kind"], path=Path(row["path"]), mime=row["mime"],
            meta=json.loads(row["meta"]), created_at=datetime.fromisoformat(row["created_at"]),
        )

    def delete_asset(self, asset_id: str) -> bool:
        cursor = self.connect().execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        return cursor.rowcount > 0

    def list_assets(self, kind: str | None = None, limit: int = 200) -> list[Asset]:
        sql = "SELECT * FROM assets"
        params: list[Any] = []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.connect().execute(sql, params).fetchall()
        return [
            Asset(id=r["id"], kind=r["kind"], path=Path(r["path"]), mime=r["mime"],
                  meta=json.loads(r["meta"]), created_at=datetime.fromisoformat(r["created_at"]))
            for r in rows
        ]

    # ---------------------------------------------------------------- characters

    def save_character(self, project: str, character: Character) -> None:
        self.connect().execute(
            "INSERT OR REPLACE INTO characters (id, project, data, updated_at) VALUES (?, ?, ?, ?)",
            (character.spec.id, project, character.model_dump_json(), _now_iso()),
        )

    def get_character(self, project: str, character_id: str) -> Character | None:
        row = self.connect().execute(
            "SELECT data FROM characters WHERE project = ? AND id = ?", (project, character_id)
        ).fetchone()
        return Character.model_validate_json(row["data"]) if row else None

    def list_projects(self) -> list[str]:
        """Every project the database mentions: roster, jobs, style, assets.

        A project is not a table: it is carried by what makes it up. Gathering
        them gives the list of folders the library must hold.
        """
        rows = self.connect().execute(
            "SELECT project FROM characters"
            " UNION SELECT project FROM jobs"
            " UNION SELECT project FROM style_packs"
            " UNION SELECT json_extract(meta, '$.project') FROM assets"
        ).fetchall()
        return sorted({row[0] for row in rows if row[0]})

    def delete_project(self, project: str, asset_ids: set[str]) -> None:
        """Erase a project from the database: characters, style, jobs, assets.

        All or nothing, in one transaction: a half-erased project would show up
        again in the list through what is left of it.
        """
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM characters WHERE project = ?", (project,))
            conn.execute("DELETE FROM style_packs WHERE project = ?", (project,))
            conn.execute("DELETE FROM jobs WHERE project = ?", (project,))
            conn.executemany("DELETE FROM assets WHERE id = ?",
                             [(asset_id,) for asset_id in asset_ids])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def list_characters(self, project: str) -> list[Character]:
        rows = self.connect().execute(
            "SELECT data FROM characters WHERE project = ? ORDER BY id", (project,)
        ).fetchall()
        return [Character.model_validate_json(r["data"]) for r in rows]

    # -------------------------------------------------------------- style packs

    def save_style_pack(self, project: str, pack: StylePack) -> None:
        self.connect().execute(
            "INSERT OR REPLACE INTO style_packs (id, project, data, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (pack.id, project, pack.model_dump_json(), _now_iso()),
        )

    def list_style_packs(self, project: str | None = None) -> list[StylePack]:
        if project:
            rows = self.connect().execute(
                "SELECT data FROM style_packs WHERE project = ? ORDER BY updated_at DESC",
                (project,),
            ).fetchall()
        else:
            rows = self.connect().execute(
                "SELECT data FROM style_packs ORDER BY updated_at DESC"
            ).fetchall()
        return [StylePack.model_validate_json(r["data"]) for r in rows]

    # ---------------------------------------------------------------- step cache

    def cached_step(self, fingerprint: str) -> dict[str, Any] | None:
        row = self.connect().execute(
            "SELECT result FROM step_cache WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return json.loads(row["result"]) if row else None

    def cache_step(self, fingerprint: str, step: str, result: dict[str, Any]) -> None:
        self.connect().execute(
            "INSERT OR REPLACE INTO step_cache (fingerprint, step, result, created_at)"
            " VALUES (?, ?, ?, ?)",
            (fingerprint, step, json.dumps(result, default=str), _now_iso()),
        )

    # --------------------------------------------------------------------- jobs

    def save_job(self, job: Job) -> Job:
        self.connect().execute(
            "INSERT OR REPLACE INTO jobs"
            " (id, kind, project, step, state, payload, result, error, cost_usd,"
            "  attempts, lease_until, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
            "  COALESCE((SELECT attempts FROM jobs WHERE id = ?), 0),"
            "  (SELECT lease_until FROM jobs WHERE id = ?), ?, ?)",
            (job.id, job.kind, job.project, job.step, job.state.value,
             json.dumps(job.payload, default=str), json.dumps(job.result, default=str),
             job.error, job.cost_usd, job.id, job.id,
             job.created_at.isoformat(), _now_iso()),
        )
        return job

    def get_job(self, job_id: str) -> Job | None:
        row = self.connect().execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self, project: str | None = None, state: JobState | None = None,
                  limit: int = 100) -> list[Job]:
        sql = "SELECT * FROM jobs"
        clauses: list[str] = []
        params: list[Any] = []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if state:
            clauses.append("state = ?")
            params.append(state.value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_job(r) for r in self.connect().execute(sql, params).fetchall()]

    def total_cost(self, project: str | None = None) -> float:
        """What all jobs have cost, since the first one.

        A total is computed over the whole database, never over the last jobs
        read: limited to a window, it would forget the oldest ones, and each
        interface would report a different figure depending on its own window.
        """
        sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM jobs"
        params: list[Any] = []
        if project:
            sql += " WHERE project = ?"
            params.append(project)
        return round(float(self.connect().execute(sql, params).fetchone()[0]), 4)

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"], kind=row["kind"], project=row["project"], step=row["step"],
            state=JobState(row["state"]),
            payload=json.loads(row["payload"]), result=json.loads(row["result"]),
            error=row["error"], cost_usd=row["cost_usd"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
