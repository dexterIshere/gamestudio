"""A job's report: what it produced, in plain terms.

The report exists for one reason: a raw `result` cites store fingerprints,
which cannot be read nor handed to anyone. What is checked here is that these
fingerprints become library paths, and that what has none is reported --
keeping quiet would pass an invisible production off as filed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gamestudio import service
from gamestudio.config import Settings
from gamestudio.domain.models import Character, CharacterSpec, Job, JobState, Pipeline
from gamestudio.service import NotFound

CONCEPT = b"\x89PNG\r\n\x1a\n concept"
ORPHAN = b"\x89PNG\r\n\x1a\n orphan"


@pytest.fixture()
def studio(tmp_path: Path):
    """The space of the `trial` project, in an isolated studio."""
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    with service.using(service.build(settings)) as current:
        yield current.space("trial")


def _owned(ctx: Any, data: bytes, **kwargs: Any) -> str:
    """An asset in the store and in the database: what every production does."""
    asset = ctx.store.put_bytes(data, ".png", kind="image", **kwargs)
    ctx.db.save_asset(asset)
    return asset.id


def _character(ctx: Any, concept_id: str) -> None:
    """A character whose concept is claimed by a project folder."""
    spec = CharacterSpec(id="hero", name="Hero", subject="a hero",
                         pipelines=[Pipeline.MESH_3D])
    ctx.db.save_character("trial", Character(spec=spec, style_pack_id="",
                                             concept_asset_id=concept_id))


def _job(ctx: Any, result: dict[str, Any], *, cost: float = 0.0) -> str:
    job = Job(id="j1", kind="render_sprites", project="trial", state=JobState.DONE,
              result=result, cost_usd=cost)
    ctx.db.save_job(job)
    return job.id


def test_fingerprints_become_paths(studio):
    concept = _owned(studio, CONCEPT)
    _character(studio, concept)
    service.library.sync("trial")
    _job(studio, {"assets": [concept], "sheets": 4, "style": "pixel"}, cost=0.12)

    report = service.jobs.detail("j1")["report"]

    expected = studio.paths.library / "2d" / "hero" / "concept.png"
    assert [f["path"] for f in report["files"]] == [str(expected)]
    # The path is not a promise: it points to a file that is there.
    assert Path(report["files"][0]["path"]).is_file()
    assert report["files"][0]["folder"] == "2d/hero"
    assert report["files"][0]["kind"] == "image"
    assert report["absent"] == []
    assert report["cost_usd"] == 0.12
    # The fingerprint is not repeated in the facts: the file states it.
    assert {f["key"] for f in report["facts"]} == {"sheets", "style"}


def test_an_asset_outside_the_library_is_reported(studio):
    """Produced but claimed by nobody: it must be seen, not guessed."""
    orphan = _owned(studio, ORPHAN)
    _job(studio, {"assets": [orphan]})

    report = service.jobs.detail("j1")["report"]

    assert report["files"] == []
    assert report["absent"] == [{"asset_id": orphan, "kind": "image"}]


def test_refusals_and_errors_are_set_apart(studio):
    _job(studio, {"note": "two views only", "errors": ["bone not found"],
                  "refused": {"gnome": "unknown archetype"},
                  "directions": 8, "animated": False, "frame_size": [80, 84]})

    report = service.jobs.detail("j1")["report"]

    assert report["note"] == "two views only"
    assert report["errors"] == ["bone not found"]
    assert report["refused"] == [{"what": "gnome", "why": "unknown archetype"}]
    facts = {f["key"]: f["value"] for f in report["facts"]}
    assert facts == {"directions": "8", "animated": "no", "frame_size": "80, 84"}


def test_a_failed_job_says_why(studio):
    job = Job(id="j2", kind="create_entity", project="trial", state=JobState.FAILED,
              error="Runware refused the request (401)")
    studio.db.save_job(job)

    report = service.jobs.detail("j2")["report"]

    assert report["state"] == "failed"
    assert report["error"] == "Runware refused the request (401)"
    assert report["cost_usd"] == 0.0


def test_an_unknown_job_is_a_readable_error(studio):
    with pytest.raises(NotFound):
        service.jobs.detail("never-seen")
