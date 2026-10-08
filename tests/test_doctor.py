"""The diagnosis: say what is missing, and what to do to get it.

What these tests protect: a **failure** must mean "the studio cannot work",
and a **warning** must not become one. Confusing the two would make `--check`
unusable: it would refuse to run on a perfectly working machine.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.service import briefing, build, doctor, using
from gamestudio.service.context import Studio


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[tuple[Studio, Path]]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context",
                        inbox_dir=tmp_path / "inbox")
    with using(build(settings)) as current:
        yield current, tmp_path


def _by_id(report: dict) -> dict[str, dict]:
    return {entry["id"]: entry for entry in report["checks"]}


def test_a_bare_studio_stays_usable(isolated_studio) -> None:
    """No key, no skills, no notes: warnings, not failures.

    The environment of the machine running the tests is not what is under
    test: Blender may be installed there, and the diagnosis must then say
    "ok". What matters is that none of these gaps is a failure.
    """
    report = doctor.check()
    assert report["ok"] is True
    assert report["counts"]["failures"] == 0
    assert report["counts"]["warnings"] > 0
    checks = _by_id(report)
    assert checks["project"]["status"] == doctor.OK
    assert checks["data"]["status"] == doctor.OK
    # A missing key and missing skills are warnings: the studio inspects, files
    # and exports without them.
    assert checks["runware-key"]["status"] == doctor.WARN
    assert checks["skills"]["status"] == doctor.WARN

    # Each warning carries its fix: a finding with no way out is useless.
    assert all(entry["fix"] for entry in report["checks"]
               if entry["status"] != doctor.OK)
    assert report["next"]
    assert report["blocking"] == []


def test_unwritable_data_is_a_failure(isolated_studio) -> None:
    """The only real blocker: the studio cannot file anything."""
    studio, tmp_path = isolated_studio
    # A file where the data folder should be: nothing can be created in it.
    blocked = tmp_path / "blocked"
    blocked.write_text("not a folder", encoding="utf-8")
    studio.settings.data_dir = blocked

    report = doctor.check()
    assert report["ok"] is False
    assert report["counts"]["failures"] == 1
    entry = _by_id(report)["data"]
    assert entry["status"] == doctor.FAIL
    assert entry["needed"] is True
    assert "GAMESTUDIO_DATA_DIR" in entry["fix"]
    assert report["blocking"] == ["Data folder"]
    assert doctor.refuse(report["counts"]["failures"]).startswith("1 failure")


def test_a_missing_root_is_a_failure(isolated_studio) -> None:
    """Without a root, no repository path makes sense: context/, documents/, recipes/."""
    studio, _ = isolated_studio
    studio.settings.project_root = None
    report = doctor.check()
    assert report["ok"] is False
    assert _by_id(report)["project"]["status"] == doctor.FAIL
    assert "GAMESTUDIO_HOME" in _by_id(report)["project"]["fix"]


def test_the_diagnosis_also_says_what_works(isolated_studio) -> None:
    """A report listing only the gaps does not say whether the studio runs."""
    report = doctor.check()
    checks = _by_id(report)
    assert checks["python"]["status"] == doctor.OK
    # A new studio does not have its notes yet: the diagnosis says so without
    # creating them -- a diagnosis that writes is no longer a diagnosis.
    assert checks["context"]["status"] == doctor.WARN
    briefing.ensure()
    assert _by_id(doctor.check())["context"]["status"] == doctor.OK
    assert checks["queue"]["status"] == doctor.OK
    assert report["version"]
    assert report["counts"]["checks"] == len(report["checks"])
