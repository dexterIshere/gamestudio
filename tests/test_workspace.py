"""The workspace: one card per project, a readable report for each.

What is checked here fits in one sentence: the card and the report say only
what the database and the library contain. A workspace that invented a step,
or kept a step that disappeared, would be worse than an empty page.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import declare_recipe

from gamestudio.config import Settings
from gamestudio.pipeline.base import Context
from gamestudio.service import build, using, workspace
from gamestudio.service.context import Studio
from gamestudio.service.errors import ServiceError
from gamestudio.sheet import import_sheet

SHEET = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">
  <g id="square" transform="translate(4,4)"><rect width="16" height="16"/></g>
  <g id="round" transform="translate(64,4)"><circle cx="8" cy="8" r="8"/></g>
</svg>
"""

RECIPE = """\
version: 1
project: game
style:
  id: game-style
  name: Game style
  prompt_prefix: "test"
defaults:
  archetype: biped
  pipelines: [mesh3d]
characters:
  - id: hero
    name: Hero
    subject: "a hero"
"""


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    """A studio on a temporary folder, with a recipe and a sheet."""
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context")
    declare_recipe(settings, "game", RECIPE)
    with using(build(settings)) as current:
        yield current


def _sheet(isolated_studio: Studio, project: str) -> None:
    """Produce a sheet in a project: enough to fill a card."""
    space = isolated_studio.space(project)
    source = isolated_studio.settings.data_dir.parent / f"pack-{project}.svg"
    # The geometry depends on the project: two identical sheets would produce
    # the same assets (the store is hash-addressed), and the second import
    # would overwrite the first. The group ids stay stable -- they name the
    # split files.
    width = 14 + len(project) % 7
    source.write_text(
        SHEET.replace('width="16" height="16"', f'width="{width}" height="16"'),
        encoding="utf-8")
    with Context(project=project, settings=space.settings, db=space.db,
                 store=space.store) as ctx:
        import_sheet(source, ctx, multi=True)


def test_the_category_follows_from_the_recipe(isolated_studio: Studio) -> None:
    """A declared project is a project; one with only a sheet is a workbench."""
    _sheet(isolated_studio, "game")
    _sheet(isolated_studio, "trials")

    data = workspace.index()
    by_project = {entry["project"]: entry for entry in data["projects"]}
    assert by_project["game"]["category"] == "project"
    assert by_project["game"]["recipe"] is not None
    assert by_project["trials"]["category"] == "workbench"
    assert by_project["trials"]["recipe"] is None
    assert data["counters"]["projects"] == 2


def test_a_pin_comes_first_and_is_kept(isolated_studio: Studio) -> None:
    """The reading order is chosen: what is pinned comes first."""
    _sheet(isolated_studio, "game")
    _sheet(isolated_studio, "trials")
    assert [entry["project"] for entry in workspace.index()["projects"]] != ["trials"]

    meta = workspace.set_meta("trials", pinned=True, title="Direction trials")
    assert meta["pinned"] is True and meta["title"] == "Direction trials"

    first = workspace.index()["projects"][0]
    assert first["project"] == "trials"
    assert first["title"] == "Direction trials"
    # The declaration is a workspace file, readable as is.
    assert (isolated_studio.space("trials").paths.workspace / "workspace.json").is_file()

    # The other fields do not move when only one is given.
    workspace.set_meta("trials", description="One sentence.")
    assert workspace.read_meta("trials")["title"] == "Direction trials"
    assert workspace.read_meta("trials")["pinned"] is True


def test_the_steps_follow_the_library(isolated_studio: Studio) -> None:
    """A produced sheet becomes a step; its files are named in it."""
    _sheet(isolated_studio, "game")
    steps = workspace.steps("game")
    sheets = [step for step in steps if step["kind"] == "sheet"]
    assert len(sheets) == 1
    assert sorted(file["name"] for file in sheets[0]["files"]) == ["round.svg", "square.svg"]
    assert sheets[0]["facts"]

    # The declared roster is a step of its own.
    roster = next(step for step in steps if step["kind"] == "roster")
    assert ("Declared", "1") in roster["facts"]


def test_the_report_is_a_standalone_document(isolated_studio: Studio) -> None:
    """It opens outside the studio: its styling is inlined, and it is dated."""
    _sheet(isolated_studio, "game")
    path = Path(workspace.write_report("game"))
    assert path == isolated_studio.space("game").paths.workspace / workspace.REPORT

    html = path.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "<style>" in html and ":root{" in html  # standalone, no external sheet
    assert "game" in html and "square.svg" in html
    assert "do not edit it" in html
    # A declaration is written so the card can be amended.
    assert (path.parent / workspace.DECLARATION).is_file()


def test_the_report_follows_what_is_produced(isolated_studio: Studio) -> None:
    """Regenerating it after a production must show the production."""
    workspace.write_report("game")
    before = Path(workspace.write_report("game")).read_text(encoding="utf-8")
    _sheet(isolated_studio, "game")
    after = Path(workspace.write_report("game")).read_text(encoding="utf-8")

    assert "square.svg" not in before
    assert "square.svg" in after


def test_an_unknown_category_or_status_is_refused(isolated_studio: Studio) -> None:
    """The card vocabulary is closed: a mistake is reported, not guessed."""
    with pytest.raises(ServiceError):
        workspace.set_meta("game", category="whatever")
    with pytest.raises(ServiceError):
        workspace.set_meta("game", status="maybe")


def test_deleting_a_project_erases_everything_that_makes_it_exist(
        isolated_studio: Studio) -> None:
    """Recipe, database, store, library, workspace: the project leaves the list,
    and its neighbour keeps its files."""
    _sheet(isolated_studio, "game")
    _sheet(isolated_studio, "trials")
    game, trials = isolated_studio.space("game"), isolated_studio.space("trials")
    game.librarian.sync_project("game")
    workspace.write_report("game")
    neighbours = trials.librarian.index_project("trials").asset_ids()
    ids = game.librarian.index_project("game").asset_ids()
    assert ids
    root = game.paths.root

    done = workspace.delete_project("game")

    assert done["erased"] and done["assets"] >= len(ids)
    assert "game" not in workspace.known_projects()
    assert not root.exists(), "the hosted folder goes entirely"
    assert trials.librarian.index_project("trials").asset_ids() == neighbours
    # The studio forgot the space: a project recreated under this name starts empty.
    fresh = isolated_studio.space("game")
    assert fresh is not game
    assert fresh.db.list_assets(limit=10) == []
    assert isolated_studio.space("trials") is trials, "the neighbour keeps its space"


def test_deleting_refuses_an_unknown_project_or_a_path(isolated_studio: Studio) -> None:
    with pytest.raises(ServiceError):
        workspace.delete_project("../game")
    with pytest.raises(ServiceError):
        workspace.delete_project("ghost")


def test_the_total_cost_is_the_same_everywhere_and_covers_the_whole_database(
        isolated_studio: Studio) -> None:
    """Card, queue, briefing and manifest state the same spending, every job included."""
    from datetime import UTC, datetime

    from gamestudio.domain.models import Job, JobState
    from gamestudio.service import briefing, jobs

    _sheet(isolated_studio, "game")
    game = isolated_studio.space("game")
    conn = game.db.connect()
    conn.execute("BEGIN")
    game.db.save_job(Job(id="oldest", kind="build_character", project="game",
                         state=JobState.DONE, cost_usd=1.0,
                         created_at=datetime(2026, 1, 1, tzinfo=UTC)))
    for index in range(600):
        game.db.save_job(Job(id=f"t{index:03d}", kind="generate_image", project="game",
                             state=JobState.DONE, cost_usd=0.01))
    conn.execute("COMMIT")
    game.queue.enqueue("generate_image", {}, project="game")

    expected = 7.0
    card = workspace.summary("game")["counters"]
    assert card["cost_usd"] == pytest.approx(expected)
    assert card["jobs"] == 602 and card["jobs_active"] == 1
    assert jobs.status("game")["cost_usd"] == pytest.approx(expected)
    assert briefing.digest("game")["focus_entry"]["cost_usd"] == pytest.approx(expected)
    assert game.librarian.sync_project("game")["cost_usd_total"] == pytest.approx(expected)

    # A pending job, even among hundreds of others, protects the project.
    with pytest.raises(ServiceError):
        workspace.delete_project("game")
