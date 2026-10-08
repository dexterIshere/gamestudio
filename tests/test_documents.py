"""Project documents: written by hand, kept, reread.

What these tests protect: a project document is a precious text, so it must not
get lost, overwrite itself, or allow reading outside its folder.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.service import build, documents, using, workspace
from gamestudio.service.context import Studio
from gamestudio.service.errors import NotFound, ServiceError


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context")
    with using(build(settings)) as current:
        yield current


def test_a_document_is_created_from_a_template_and_reread(isolated_studio: Studio) -> None:
    """The template gives a page usable right away, not a blank page."""
    created = documents.create_document("game", "Arachne's bible", "character")

    assert created["name"] == "arachne-s-bible"
    assert created["file"] == "arachne-s-bible.md"
    assert created["title"] == "Arachne's bible"
    assert "## Appearance" in created["text"]
    assert created["path"].endswith(".gamestudio/documents/arachne-s-bible.md")

    # The folder lives in the project's `.gamestudio/`, versioned with the game.
    assert Path(created["path"]).is_file()
    assert Path(created["path"]).parent == isolated_studio.space("game").paths.documents


def test_an_accented_title_gives_a_plain_file_name() -> None:
    """Users title their documents in their own language: accents are folded."""
    assert documents.slug("Bâtiments à l'étage") == "batiments-a-l-etage"


def test_the_offered_templates_are_the_service_ones(isolated_studio: Studio) -> None:
    ids = [entry["id"] for entry in documents.templates()]
    assert ids == ["blank", "character", "world", "direction", "mood", "note", "idea",
                   "devlog", "mechanic", "interface", "icon", "prop", "vfx", "card", "todo"]
    assert all(entry["label"] for entry in documents.templates())


def test_writing_does_not_lose_an_existing_document_unasked(
        isolated_studio: Studio) -> None:
    """A taken name refuses creation; writing updates."""
    documents.create_document("game", "Notes", "world")
    with pytest.raises(ServiceError, match="already exists"):
        documents.create_document("game", "Notes", "blank")

    updated = documents.write_document("game", "notes", "# Notes\n\nThe valley.\n")
    assert "The valley." in updated["text"]
    assert len(documents.documents("game")) == 1


def test_writing_creates_the_given_document(isolated_studio: Studio) -> None:
    written = documents.write_document("game", "ideas", "# Ideas\n\n- one\n")
    assert written["title"] == "Ideas"
    assert written["size_bytes"] == len("# Ideas\n\n- one\n")
    # A document written by an agent is no different from one written by hand:
    # same folder, same versioning.
    assert documents.read_document("game", "ideas")["text"].endswith("- one\n")


def test_the_list_goes_newest_first(isolated_studio: Studio) -> None:
    """The timestamps are set by the test: disk granularity must not decide the
    outcome of what is tested."""
    base = 1_700_000_000.0
    for position, name in enumerate(("one", "two", "three")):
        written = documents.write_document("game", name, f"# {name}\n")
        os.utime(written["path"], (base + position, base + position))
    # The last written is the newest, even if its name comes first.
    last = documents.write_document("game", "one", "# One\n\nModified afterwards.\n")
    os.utime(last["path"], (base + 10, base + 10))

    listed = documents.documents("game")
    assert [entry["name"] for entry in listed] == ["one", "three", "two"]
    assert listed[0]["words"] > 2


def test_a_document_name_is_an_identifier_not_a_path(isolated_studio: Studio) -> None:
    """Refuse rather than clean: a name that changes silently gets lost."""
    for bad in ("../secret", "a/b", "", "..", "spaces forbidden", "/etc/passwd",
                "x" * 80, ".cache"):
        with pytest.raises(ServiceError):
            documents.write_document("game", bad, "# Whatever\n")
        with pytest.raises(ServiceError):
            documents.read_document("game", bad)


def test_a_missing_document_says_so(isolated_studio: Studio) -> None:
    assert documents.documents("game") == []
    with pytest.raises(NotFound):
        documents.read_document("game", "ghost")
    with pytest.raises(NotFound):
        documents.delete_document("game", "ghost")


def test_the_document_ends_with_a_newline(isolated_studio: Studio) -> None:
    """Without it, the next git diff would report a change that is not one."""
    written = documents.write_document("game", "no-end", "# Title")
    assert written["text"] == "# Title\n"


def test_an_empty_or_oversized_document_is_refused(isolated_studio: Studio) -> None:
    with pytest.raises(ServiceError, match="empty"):
        documents.write_document("game", "empty", "   \n")
    with pytest.raises(ServiceError, match="too large"):
        documents.write_document("game", "huge", "a" * (documents.MAX_BYTES + 1))


def test_deleting_and_renaming_are_explicit(isolated_studio: Studio) -> None:
    documents.create_document("game", "Notes", "blank")
    renamed = documents.rename_document("game", "notes", "World notes")
    assert renamed["name"] == "world-notes"
    assert documents.documents("game")[0]["name"] == "world-notes"

    removed = documents.delete_document("game", "world-notes")
    assert removed["deleted"] is True
    assert documents.documents("game") == []


def test_documents_show_in_the_project_card(isolated_studio: Studio) -> None:
    """A document is a workspace step: it is part of what the project is."""
    documents.create_document("game", "Art direction", "direction")
    steps = workspace.steps("game")
    document_steps = [step for step in steps if step["kind"] == "document"]
    assert len(document_steps) == 1
    assert document_steps[0]["title"] == "Art direction"
    assert any(str(value).endswith("art-direction.md")
               for _, value in document_steps[0]["facts"])

    html = Path(workspace.write_report("game")).read_text(encoding="utf-8")
    assert "Art direction" in html
    assert "art-direction.md" in html
