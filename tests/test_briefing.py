"""Agent context: hand-kept notes, regenerated briefing.

These tests hold the feature's promise: a discussion opened after a production
must find that state in `context/briefing.md`, without any agent having to ask
again.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.domain.models import Character, CharacterSpec, JobState, Pipeline
from gamestudio.service import briefing, build, using
from gamestudio.service.context import Studio
from gamestudio.service.errors import NotFound, ServiceError


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    """The test's studio, on a folder of its own.

    The project root is redirected too: otherwise the repository's recipes
    would enter the briefing and the test would depend on what the person
    running it has at hand.
    """
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context")
    with using(build(settings)) as current:
        yield current


def _character(project: str = "demo") -> Character:
    spec = CharacterSpec(id="hero", name="Hero", subject="a knight",
                         pipelines=[Pipeline.MESH_3D])
    return Character(spec=spec, style_pack_id=f"{project}-free", state=JobState.DONE)


def test_notes_are_created_once_then_respected(isolated_studio: Studio) -> None:
    """`ensure` seeds the template, and never overwrites what someone wrote."""
    created = briefing.ensure()
    assert set(created) == set(briefing.NOTES)
    assert all(entry["exists"] for entry in briefing.files()
               if not entry["generated"])

    briefing.write("goals.md", "# Goals\n\nDeliver the roster.\n")
    assert briefing.ensure() == []  # second call: nothing to seed
    assert "Deliver the roster." in briefing.read("goals.md")["text"]


def test_the_briefing_cannot_be_written(isolated_studio: Studio) -> None:
    """It is regenerated: editing it would suggest a change that does not happen."""
    with pytest.raises(ServiceError):
        briefing.write(briefing.BRIEFING, "by hand")


def test_an_unknown_name_cannot_leave_the_folder(isolated_studio: Studio) -> None:
    """The allow-list is the guard: no path traversal to filter."""
    for name in ("../.env", "identity.md/../briefing.md", "other.md", ""):
        with pytest.raises(NotFound):
            briefing.read(name)


def test_the_briefing_tells_characters_and_jobs(isolated_studio: Studio) -> None:
    """What the agent comes for: who exists, and where production stands."""
    isolated_studio.space("demo").db.save_character("demo", _character())
    path = Path(briefing.write_briefing())

    assert path == isolated_studio.settings.context_path / briefing.BRIEFING
    text = path.read_text(encoding="utf-8")
    # The current project opens the briefing, the others follow in summary.
    assert "## Current project: `demo`" in text
    assert "`hero`" in text
    assert "mesh3d" in text
    assert "gamestudio context show --write" in text
    # The file is marked as regenerated, not as a hand-kept note.
    entry = next(e for e in briefing.files() if e["name"] == briefing.BRIEFING)
    assert entry["generated"] is True and entry["exists"] is True


def test_a_briefing_focused_on_a_project_keeps_the_other_counts(
        isolated_studio: Studio) -> None:
    isolated_studio.space("demo").db.save_character("demo", _character("demo"))
    isolated_studio.space("other").db.save_character("other", _character("other"))

    data = briefing.digest("demo")
    # The focus puts `demo` forward, but every project stays listed.
    assert data["focus"] == "demo"
    assert {entry["project"] for entry in data["projects"]} == {"demo", "other"}
    assert data["projects_total"] == 2

    text = briefing.render(data)
    assert "## Current project: `demo`" in text
    assert "`other`" in text  # not detailed, but counted
    assert "## Other projects" in text


def test_an_isolated_project_briefing_sees_only_itself(isolated_studio: Studio) -> None:
    """The context of a discussion opened in a project: the other games are not in it."""
    isolated_studio.space("demo").db.save_character("demo", _character("demo"))
    isolated_studio.space("rival").db.save_character("rival", _character("rival"))

    data = briefing.digest("demo", isolated=True)
    assert [entry["project"] for entry in data["projects"]] == ["demo"]
    assert data["counters"]["projects"] == 1
    text = briefing.render(data)
    assert "rival" not in text and "## Other projects" not in text

    path = Path(briefing.write_project_briefing("demo"))
    assert path == isolated_studio.space("demo").paths.context / briefing.BRIEFING


def test_a_read_writes_nothing(isolated_studio: Studio) -> None:
    """`write_briefing` writes: looking at the context does not freeze it."""
    path = isolated_studio.settings.context_path / briefing.BRIEFING
    briefing.briefing()
    assert not path.exists()

    briefing.write_briefing()
    assert path.is_file()


def test_the_briefing_names_the_hand_written_documents(isolated_studio: Studio) -> None:
    """What was decided must read in the context, not only what was produced."""
    from gamestudio.service import documents

    documents.create_document("demo", "Hero bible", "character")
    text = briefing.render(briefing.digest())
    assert "Hero bible" in text
    assert "documents: `Hero bible`" in text


def test_a_project_without_documents_has_no_documents_line(isolated_studio: Studio) -> None:
    isolated_studio.space("demo").db.save_character("demo", _character())
    text = briefing.render(briefing.digest())
    assert "documents:" not in text
