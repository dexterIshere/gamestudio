"""The queue: a live job is picked up by nobody, a paid job is never replayed.

Several workers serve the same queue -- the API's, one per MCP server. What is
checked here is what keeps any of them from paying twice: a long job's lease
held by its heartbeat, a paid job's failure reported as is, and a worker's
death only restarting free jobs.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from gamestudio import service
from gamestudio.config import Settings
from gamestudio.domain.models import Character, CharacterSpec, JobState, Pipeline, Rig
from gamestudio.jobs import worker
from gamestudio.jobs.queue import FREE_KINDS, INTERRUPTED, MAX_ATTEMPTS, JobQueue
from gamestudio.pipeline.base import Context, StepResult
from gamestudio.store.db import Database


@pytest.fixture()
def base(tmp_path: Path) -> Path:
    return tmp_path / "queue.sqlite3"


def _attempts(queue: JobQueue, job_id: str) -> int:
    row = queue.db.connect().execute("SELECT attempts FROM jobs WHERE id = ?",
                                     (job_id,)).fetchone()
    return row["attempts"]


def test_a_held_job_is_picked_up_by_no_other_worker(base: Path) -> None:
    """A training outlasts its lease: the heartbeat keeps it with its worker."""
    mine, other = JobQueue(Database(base)), JobQueue(Database(base))
    mine.enqueue("train_style", {}, project="trial")
    lease = timedelta(seconds=0.4)
    job = mine.claim(lease=lease)
    assert job is not None

    with mine.hold(job, lease=lease, every=0.05):
        time.sleep(1.2)  # three leases
        assert other.claim() is None, "another worker picked up a live job"
        assert other.db.get_job(job.id).state == JobState.RUNNING

    # The heartbeat stopped without the job finishing: its worker is dead.
    time.sleep(0.6)
    assert other.claim() is None
    found = other.db.get_job(job.id)
    assert (found.state, found.error) == (JobState.FAILED, INTERRUPTED)
    assert _attempts(other, job.id) == 1


def test_a_heartbeat_does_not_revive_a_finished_job(base: Path) -> None:
    queue = JobQueue(Database(base))
    queue.enqueue("ping", {}, project="trial")
    job = queue.claim()
    queue.complete(job, {"pong": True})
    queue.heartbeat(job)
    assert queue.db.get_job(job.id).state == JobState.DONE
    assert queue.db.connect().execute("SELECT lease_until FROM jobs WHERE id = ?",
                                      (job.id,)).fetchone()["lease_until"] is None


def test_a_failed_paid_job_is_not_replayed(base: Path) -> None:
    """A `RunwareTimeout` may have been billed: it is reported, not restarted."""
    queue = JobQueue(Database(base))
    queue.enqueue("generate_image", {"count": 4}, project="trial")
    job = queue.claim()
    queue.fail(job, "RunwareTimeout: task not finished after 1800s", cost=0.04)

    found = queue.db.get_job(job.id)
    assert found.state == JobState.FAILED
    assert found.cost_usd == pytest.approx(0.04), "the committed cost is kept"
    assert found.error.startswith("RunwareTimeout")
    assert queue.claim() is None
    assert _attempts(queue, job.id) == 1


def test_a_free_job_is_replayed_within_its_attempts(base: Path) -> None:
    queue = JobQueue(Database(base))
    queue.enqueue("render_sprites", {}, project="trial")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        job = queue.claim()
        assert job is not None and _attempts(queue, job.id) == attempt
        queue.fail(job, "Blender failed")
    assert queue.db.get_job(job.id).state == JobState.FAILED
    assert queue.claim() is None


def test_a_dead_worker_only_restarts_free_jobs(base: Path) -> None:
    dead, alive = JobQueue(Database(base)), JobQueue(Database(base))
    paid = dead.enqueue("create_entity", {}, project="trial")
    free = dead.enqueue("render_sprites", {}, project="trial")
    # Two jobs taken by a worker that dies at once: their leases expire.
    expired = timedelta(seconds=-1)
    assert dead.claim(lease=expired).id == paid.id
    assert dead.claim(lease=expired).id == free.id

    resumed = alive.claim()
    assert resumed is not None and resumed.id == free.id
    found = alive.db.get_job(paid.id)
    assert (found.state, found.error) == (JobState.FAILED, INTERRUPTED)
    assert alive.claim() is None


def test_every_worker_job_kind_is_classified() -> None:
    """Adding a job kind means deciding whether it costs: by default, it does."""
    assert set(FREE_KINDS) <= set(worker.HANDLERS)
    assert set(worker.HANDLERS) - FREE_KINDS == {
        "build_character", "explore_style", "generate_image", "create_entity",
        "train_style"}


# ------------------------------------------------------------------ the worker


@pytest.fixture()
def space(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    with service.using(service.build(settings)) as current:
        yield current.space("trial")


class BilledFailure(RuntimeError):
    """An error that knows what it already cost."""

    cost_usd = 0.25


def test_the_worker_keeps_the_cost_of_a_paid_failure(space, monkeypatch) -> None:
    def fail(_job: Any, _ctx: Any) -> dict[str, Any]:
        raise BilledFailure("mesh refused after the reference")

    monkeypatch.setitem(worker.HANDLERS, "create_entity", fail)
    job = space.queue.enqueue("create_entity", {"prompt": "a golem"}, project="trial")

    assert worker.run_worker(max_jobs=1, poll_interval=0.01, stop=threading.Event()) == 1

    found = space.db.get_job(job.id)
    assert found.state == JobState.FAILED
    assert found.cost_usd == pytest.approx(0.25)
    assert "mesh refused" in found.error
    assert space.queue.claim() is None


class FakeRender:
    """`RenderSprites` without Blender: one sheet per animation and per direction."""

    def __init__(self, mesh: str, _name: str, *, animations: list[str],
                 directions: int, **_: Any) -> None:
        self.clips, self.directions = animations or ["idle"], directions

    def execute(self, ctx: Any) -> StepResult:
        assets = []
        for clip in self.clips:
            for direction in range(self.directions):
                asset = ctx.store.put_bytes(f"{clip}-{direction}/{self.directions}".encode(),
                                            ".png", kind="spritesheet",
                                            meta={"clip": clip, "direction": direction})
                ctx.db.save_asset(asset)
                assets.append(asset)
        return StepResult(data={"directions": self.directions}, assets=assets)


def test_a_new_render_replaces_all_the_sheets(space, monkeypatch) -> None:
    """From 8 to 4 directions, index 1 changes angle: nothing of the old render stays."""
    monkeypatch.setattr(worker, "RenderSprites", FakeRender)
    mesh = space.store.put_bytes(b"glTF mesh", ".glb", kind="mesh")
    space.db.save_asset(mesh)
    spec = CharacterSpec(id="golem", name="Golem", subject="a golem",
                         pipelines=[Pipeline.MESH_3D])
    space.db.save_character("trial", Character(
        spec=spec, style_pack_id="",
        rig3d=Rig(archetype=spec.archetype, bones=[], mesh_asset_id=mesh.id)))

    def render(directions: int, animations: list[str]) -> dict[str, str]:
        job = space.queue.enqueue("render_sprites", {
            "mesh": mesh.id, "directions": directions, "animations": animations},
            project="trial")
        with Context(project="trial", settings=space.settings, db=space.db,
                     store=space.store) as ctx:
            worker.handle_render_sprites(space.db.get_job(job.id), ctx)
        return space.db.get_character("trial", "golem").spritesheets

    assert len(render(8, ["walk_loop", "attack"])) == 16
    sheets = render(4, ["walk_loop"])
    assert sorted(sheets) == [f"walk_loop_{direction}" for direction in range(4)]
