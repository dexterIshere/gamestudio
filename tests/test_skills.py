"""Skills: the regenerated index, the mirror, and what must fail a check.

What these tests protect: a drifting index would have an agent read a wrong
list, and a broken mirror would hide the procedures from agents that do not
read `.claude/`. Both are therefore checked, not just written.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.service import build, skills, using
from gamestudio.service.context import Studio
from gamestudio.service.errors import NotFound, ServiceError


def _skill(root: Path, name: str, *, description: str = "A test procedure, nothing more.",
           declared: str | None = None, vendored: bool = False) -> Path:
    folder = root / ".claude" / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    declared_name = name if declared is None else declared
    (folder / skills.SKILL_FILE).write_text(
        f"---\nname: {declared_name}\ndescription: {description}\n---\n\n# {name}\n\nBody.\n",
        encoding="utf-8")
    if vendored:
        (folder / skills.VENDOR_MARKER).write_text("# Vendored\n", encoding="utf-8")
    return folder


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context")
    with using(build(settings)) as current:
        yield current


def test_a_valid_skill_reads_with_its_description(isolated_studio: Studio,
                                                  tmp_path: Path) -> None:
    _skill(tmp_path, "animation", description="Rig and animate an entity, part by part.")
    found = skills.skills()
    assert [entry["name"] for entry in found] == ["animation"]
    assert found[0]["description"].endswith("part by part.")
    assert found[0]["problems"] == []
    assert found[0]["vendored"] is False
    assert found[0]["files"] == 1


def test_third_party_material_is_recognized_by_its_provenance(isolated_studio: Studio,
                                                              tmp_path: Path) -> None:
    """`VENDOR.md` is enough: no hand-kept list that would end up lying."""
    _skill(tmp_path, "forge")
    _skill(tmp_path, "img2threejs", vendored=True)
    entries = {entry["name"]: entry for entry in skills.skills()}
    assert entries["forge"]["vendored"] is False
    assert entries["img2threejs"]["vendored"] is True


def test_malformed_skills_are_reported(isolated_studio: Studio, tmp_path: Path) -> None:
    """A too short description is refused: "TODO" does not say when to use it."""
    _skill(tmp_path, "good")
    _skill(tmp_path, "terse", description="Too short.")
    _skill(tmp_path, "other-name", declared="another-name")
    _skill(tmp_path, "no-description", description="")
    (tmp_path / ".claude" / "skills" / "empty").mkdir(parents=True)

    problems = {entry["name"]: entry["problems"] for entry in skills.skills()}
    assert problems["good"] == []
    assert any("does not match" in problem for problem in problems["other-name"])
    assert any("description" in problem for problem in problems["no-description"])
    assert any("too short" in problem for problem in problems["terse"])
    assert problems["empty"] == [f"{skills.SKILL_FILE} missing"]

    state = skills.check()
    assert state["ok"] is False
    assert {problem["skill"] for problem in state["problems"]} >= {
        "other-name", "no-description", "empty"}


def test_the_index_is_regenerated_and_checked(isolated_studio: Studio, tmp_path: Path) -> None:
    """A stale index must fail the check, not go unnoticed."""
    _skill(tmp_path, "roster", description="Build a batch of characters.")
    _skill(tmp_path, "forge", description="Forge a local mesh, without network.",
           vendored=True)

    assert skills.check()["ok"] is False  # index missing
    written = skills.write_index()
    assert written["count"] == 2
    assert Path(written["path"]).is_file()
    text = Path(written["path"]).read_text(encoding="utf-8")
    assert "Studio procedures" in text and "Vendored third-party material" in text
    assert "[`roster`](roster/SKILL.md)" in text
    assert "docs/THIRD_PARTY_NOTICES.md" in text

    # The mirror is still missing: the check says so, and `sync` repairs it.
    assert any("mirror" in problem["problem"] for problem in skills.check()["problems"])
    result = skills.sync()
    assert result["created"] == ["forge", "roster"]
    assert result["ok"] is True
    assert skills.check()["ok"] is True

    # Adding a skill makes the index stale: exactly what must show.
    _skill(tmp_path, "animation", description="Pick a clip and check the loop.")
    state = skills.check()
    assert state["ok"] is False
    assert any(problem["problem"].startswith("index out of date")
               for problem in state["problems"])


def test_the_mirror_is_made_of_links_and_repairs_itself(isolated_studio: Studio,
                                                        tmp_path: Path) -> None:
    """Links, never copies: the mirror cannot drift."""
    _skill(tmp_path, "sheet-split")
    skills.write_index()
    skills.sync()

    link = tmp_path / skills.MIRROR_DIR / "sheet-split"
    assert link.is_symlink()
    assert link.resolve() == (tmp_path / ".claude" / "skills" / "sheet-split").resolve()
    # The content follows without copying: that is the whole point.
    assert (link / skills.SKILL_FILE).is_file()

    # A broken link is reported, then removed by the sync.
    link.unlink()
    (tmp_path / skills.MIRROR_DIR / "ghost").symlink_to("../../.claude/skills/ghost")
    assert skills.check()["ok"] is False
    assert skills.sync()["removed"] == ["ghost"]
    assert skills.mirror()["ok"] is True


def test_reading_a_skill_and_refusing_an_unknown_one(isolated_studio: Studio,
                                                     tmp_path: Path) -> None:
    _skill(tmp_path, "validation", description="Check without spending a cent.")
    entry = skills.read("validation")
    assert "Body." in entry["text"]
    with pytest.raises(NotFound):
        skills.read("nonexistent")


def test_creating_a_procedure_writes_the_file_the_index_and_the_mirror(
        isolated_studio: Studio, tmp_path: Path) -> None:
    """The three steps are one: otherwise creating would make the repository fail its check."""
    entry = skills.create("Rework of a split's joints",
                          "Fix a 2D split, joint by joint, without paying anything.")

    assert entry["name"] == "rework-of-a-split-s-joints"
    assert Path(entry["skill_file"]) == (
        tmp_path / ".claude" / "skills" / "rework-of-a-split-s-joints" / skills.SKILL_FILE)
    assert entry["text"].startswith("---\nname: rework-of-a-split-s-joints")
    assert "# Rework of a split's joints" in entry["text"]
    assert skills.template().strip() in entry["text"]  # the skeleton, for lack of a body
    assert entry["problems"] == []
    assert entry["linked"] is True
    # The index and the mirror follow in the same step: the check is green.
    assert skills.check()["ok"] is True
    assert (tmp_path / skills.MIRROR_DIR / entry["name"]).is_symlink()


def test_a_folder_name_folds_accents(isolated_studio: Studio) -> None:
    """A title may be typed in French: its folder name stays plain ASCII."""
    assert skills.slug("Reprise d'une découpe") == "reprise-d-une-decoupe"


def test_creating_refuses_a_taken_name_and_a_silent_description(
        isolated_studio: Studio, tmp_path: Path) -> None:
    _skill(tmp_path, "sheet-split", description="Split a sheet into elements.")
    with pytest.raises(ServiceError, match="already exists"):
        skills.create("Sheet split", "Split a sheet of several elements.")
    # A too short description would pass the frontmatter but fail the check: it
    # is refused when written.
    with pytest.raises(ServiceError, match="too short"):
        skills.create("forge", "Forge.")
    with pytest.raises(ServiceError, match="invalid name"):
        skills.create("!!!", "A procedure without a usable name.")
    assert [entry["name"] for entry in skills.skills()] == ["sheet-split"]


def test_creating_writes_the_given_body(isolated_studio: Studio, tmp_path: Path) -> None:
    entry = skills.create("Color check", "Check a palette before delivering.",
                          body="## The loop\n\n1. Read the sheet.\n")
    assert "## The loop" in entry["text"]
    assert skills.template().strip() not in entry["text"]
