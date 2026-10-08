"""Project folders: a project is a folder on the machine.

Four promises: the folder holds in its `.gamestudio/` everything the studio
keeps for the game (recipe, documents, database, store, library, reports), no
project sees what another produced, every path goes through the same
resolver, and the studio never erases anything in the folder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import declare_recipe

from gamestudio import service
from gamestudio.config import Settings
from gamestudio.service import NotFound, ServiceError
from gamestudio.store.folders import GITIGNORE, project_paths
from gamestudio.terminal.harnesses import studio_mcp_args

RECIPE = """\
version: 1
project: trial
style:
  id: trial-style
  name: Trial style
"""


@pytest.fixture()
def studio(tmp_path: Path):
    home = tmp_path / "studio"
    home.mkdir()
    settings = Settings(data_dir=home / "data", project_root=home)
    declare_recipe(settings, "trial", RECIPE)
    with service.using(service.build(settings)) as current:
        yield current


@pytest.fixture()
def game(tmp_path: Path) -> Path:
    folder = tmp_path / "My Game"
    folder.mkdir()
    return folder


def test_a_blank_folder_becomes_a_project(studio, game: Path) -> None:
    opened = service.folders.open_folder(str(game))
    assert opened == {"project": "my-game", "root": str(game.resolve()), "created": True}
    assert (game / ".gamestudio" / "recipe.yaml").is_file()
    # Only texts are versioned with the game.
    assert (game / ".gamestudio" / ".gitignore").read_text(encoding="utf-8") == GITIGNORE

    entry = next(e for e in service.catalog.list_recipes() if e["project"] == "my-game")
    assert entry["root"] == str(game.resolve()) and entry["linked"] is True
    assert service.catalog.find_recipe("my-game")["path"] == str(
        game / ".gamestudio" / "recipe.yaml")
    # A project hosted by the studio has the same layout, in `data/projects/`.
    trial = next(e for e in service.catalog.list_recipes() if e["project"] == "trial")
    assert trial["linked"] is False
    assert trial["root"] == str(studio.settings.data_dir / "projects" / "trial")


def test_every_project_path_is_in_the_folder(studio, game: Path) -> None:
    service.folders.open_folder(str(game))
    paths = project_paths(studio.settings, "my-game")
    root = game.resolve()
    inner = root / ".gamestudio"
    assert paths.godot == root
    assert paths.documents == inner / "documents"
    assert service.documents.directory("my-game") == paths.documents

    space = studio.space("my-game")
    assert space.settings.db_path == inner / "gamestudio.sqlite3"
    assert space.settings.assets_dir == inner / "assets"
    assert space.librarian.project_dir("my-game") == inner / "library"
    assert service.workspace.project_dir("my-game") == inner / "workspace"

    space.librarian.sync_project("my-game")
    service.workspace.write_report("my-game")
    service.briefing.write_briefing("my-game")
    assert (inner / "library" / "manifest.json").is_file()
    assert (inner / "workspace" / "report.html").is_file()
    assert (inner / "context" / "briefing.md").is_file()
    # The studio keeps nothing of the project on its side.
    assert not (studio.settings.data_dir / "library").exists()
    assert not (studio.settings.data_dir / "assets").exists() or not any(
        (studio.settings.data_dir / "assets").iterdir())


def test_a_project_does_not_see_what_another_produced(studio, game: Path) -> None:
    service.folders.open_folder(str(game))
    image = game / "square.png"
    from PIL import Image

    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)
    service.library.add_files([str(image)], "my-game", "pack")

    mine = service.catalog.list_assets(project="my-game")
    assert len(mine) == 1 and mine[0]["project"] == "my-game"
    assert service.catalog.list_assets(project="trial") == []
    assert service.catalog.asset_info(mine[0]["id"])["project"] == "my-game"
    # The file lives in the folder's store, not in the studio's.
    assert service.catalog.asset_path(mine[0]["id"]).is_relative_to(game.resolve())
    # A project does not file another's elements.
    with pytest.raises(ServiceError, match="outside project"):
        service.library.move([mine[0]["id"]], "trial", "stolen")


def test_a_project_briefing_only_talks_about_itself(studio, game: Path) -> None:
    service.folders.open_folder(str(game))
    text = (Path(service.briefing.write_project_briefing("my-game"))
            .read_text(encoding="utf-8"))
    assert "my-game" in text
    assert "trial" not in text
    assert "Other projects" not in text




def test_reopening_a_folder_keeps_its_name(studio, game: Path) -> None:
    service.folders.open_folder(str(game), name="Valley")
    assert service.folders.open_folder(str(game))["project"] == "valley"

    # Closed then reopened: the folder's recipe gives back the same project.
    service.folders.close_folder("valley")
    again = service.folders.open_folder(str(game))
    assert again == {"project": "valley", "root": str(game.resolve()), "created": False}


def test_a_taken_name_is_refused(studio, game: Path) -> None:
    with pytest.raises(ServiceError, match="already exists"):
        service.folders.open_folder(str(game), name="trial")
    assert not (game / ".gamestudio").exists()


def test_the_studio_itself_is_refused(studio) -> None:
    with pytest.raises(ServiceError, match="studio folder"):
        service.folders.open_folder(str(studio.settings.data_dir))
    with pytest.raises(NotFound):
        service.folders.open_folder(str(studio.settings.data_dir / "absent"))
    with pytest.raises(ServiceError, match="relative path"):
        service.folders.open_folder("games/my-game")


def test_closing_or_deleting_does_not_touch_the_folder(studio, game: Path) -> None:
    service.folders.open_folder(str(game))
    service.documents.create_document("my-game", "Bible")
    studio.space("my-game").librarian.sync_project("my-game")
    before = sorted(path.relative_to(game) for path in game.rglob("*"))

    service.workspace.delete_project("my-game")
    assert sorted(path.relative_to(game) for path in game.rglob("*")) == before
    assert service.folders.list_folders() == []
    assert all(e["project"] != "my-game" for e in service.catalog.list_recipes())


def test_a_tab_outside_the_studio_gets_its_mcp_servers(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "gamestudio": {"command": ".venv/bin/gamestudio", "args": ["mcp"]},
        "godot": {"command": "node", "args": ["x.js"]},
    }}), encoding="utf-8")

    claude = studio_mcp_args("claude", tmp_path)
    assert claude[0] == "--mcp-config"
    servers = json.loads(claude[1])["mcpServers"]
    # The relative launcher is anchored on the root; a program from the PATH stays as is.
    assert servers["gamestudio"]["command"] == str(tmp_path / ".venv/bin/gamestudio")
    assert servers["godot"]["command"] == "node"
    assert servers["gamestudio"]["env"]["GAMESTUDIO_HOME"] == str(tmp_path)

    codex = studio_mcp_args("codex", tmp_path)
    assert f'mcp_servers.gamestudio.command="{tmp_path}/.venv/bin/gamestudio"' in codex
    # An agent whose configuration cannot be passed gets none.
    assert studio_mcp_args("kimi", tmp_path) == []


def test_removing_a_workspace_erases_nothing(studio, game: Path) -> None:
    service.folders.open_folder(str(game))
    recipe = project_paths(studio.settings, "trial").recipe

    # A studio project is hidden: its recipe stays, and so does the entry.
    assert service.folders.forget("trial")["hidden"] is True
    assert recipe.is_file()
    trial = next(e for e in service.catalog.list_recipes() if e["project"] == "trial")
    assert trial["hidden"] is True

    # A folder project is closed: the folder stays whole.
    assert service.folders.forget("my-game")["closed"] is True
    assert (game / ".gamestudio" / "recipe.yaml").is_file()

    assert service.folders.unhide() == ["trial"]
    assert not any(e["hidden"] for e in service.catalog.list_recipes())
    with pytest.raises(NotFound):
        service.folders.forget("unknown")


@pytest.mark.parametrize("name", ["../../../../tmp/evil", "..", ".", "", "a/b", "a\\b", "a\0b"])
def test_a_name_leaving_the_studio_is_refused(studio, tmp_path: Path, name: str) -> None:
    """A hosted project lives under `data/projects/<project>/`: its name stays inside."""
    with pytest.raises(ServiceError, match="invalid project name"):
        project_paths(studio.settings, name)
    if name:
        # A project's space is never built outside the studio.
        with pytest.raises(ServiceError, match="invalid project name"):
            studio.space(name)


def test_a_recipe_named_outside_the_studio_does_not_open(studio, game: Path) -> None:
    (game / ".gamestudio").mkdir()
    (game / ".gamestudio" / "recipe.yaml").write_text(
        RECIPE.replace("project: trial", "project: ../../evil"), encoding="utf-8")
    with pytest.raises(ServiceError, match="invalid project name"):
        service.folders.open_folder(str(game))
    assert "../../evil" not in studio.registry.all()


def test_a_registry_written_by_several_at_once_loses_nothing(studio, tmp_path: Path) -> None:
    """The API and each MCP server open folders: no opening is lost."""
    from concurrent.futures import ThreadPoolExecutor

    from gamestudio.store.folders import FolderRegistry

    names = [f"game-{index}" for index in range(40)]

    def open_one(name: str) -> None:
        # One registry per call, like a separate process: nothing is shared
        # but the file.
        FolderRegistry.for_settings(studio.settings).link(name, tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(open_one, names))
    assert set(names) <= set(studio.registry.all())


def test_an_invalid_registry_name_is_ignored(studio, tmp_path: Path) -> None:
    registry = studio.settings.home / "folders.json"
    registry.write_text(json.dumps({"version": 1, "folders": {
        "../../evil": {"root": str(tmp_path)}, "sound": {"root": str(tmp_path)}}}))
    assert set(studio.registry.all()) == {"sound"}
