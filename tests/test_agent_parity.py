"""What the interface can do, an agent can do too -- or the reason is stated.

A chat must lack none of the data the studio window shows. The promise does
not rest on discipline: a route added to the API without an MCP tool fails
`make check`, until it is a tool or an entry of `mcp_server.HUMAN_ONLY` with
its reason. And the reverse: an exclusion that became false (the operation
disappeared, or a tool now exposes it) is refused too, so the list stays true.

Same rule for the agents of the Chats window: each one receives, outside the
studio root, its tools **and** its context.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from gamestudio import mcp_server
from gamestudio.config import Settings
from gamestudio.service import briefing, build, using
from gamestudio.store.folders import FolderRegistry
from gamestudio.terminal import harnesses

SRC = Path(mcp_server.__file__).parent
SERVICE = {path.stem for path in (SRC / "service").glob("*.py")}


def _service_calls(path: Path) -> set[str]:
    """The service `module.function` calls a file makes (classes excluded).

    Import aliases (`from .service import inbox as inbox_service`) are mapped
    back to the module name.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    alias = {name: name for name in SERVICE}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("service"):
            for name in node.names:
                if name.name in SERVICE:
                    alias[name.asname or name.name] = name.name
    calls: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in alias and node.attr[:1].islower()):
            calls.add(f"{alias[node.value.id]}.{node.attr}")
    return calls


def test_every_interface_operation_is_a_tool_or_a_stated_exclusion() -> None:
    api = _service_calls(SRC / "api" / "app.py")
    mcp = _service_calls(SRC / "mcp_server.py")
    missing = sorted(api - mcp - set(mcp_server.HUMAN_ONLY))
    assert not missing, (
        "operations served to the interface and missing from the MCP server: "
        f"{missing} -- add a tool, or a HUMAN_ONLY entry with its reason")


def test_the_exclusions_stay_true() -> None:
    mcp = _service_calls(SRC / "mcp_server.py") - set(mcp_server.HUMAN_ONLY)
    for qualified, reason in mcp_server.HUMAN_ONLY.items():
        module, function = qualified.split(".")
        assert module in SERVICE, qualified
        source = (SRC / "service" / f"{module}.py").read_text(encoding="utf-8")
        assert f"def {function}(" in source, f"{qualified} no longer exists"
        assert reason.strip(), f"{qualified}: an exclusion states its reason"
    # An exclusion is only named in its own declaration.
    exposed = {name for name in mcp_server.HUMAN_ONLY if name in mcp}
    assert not exposed, f"exposed by a tool, remove from HUMAN_ONLY: {exposed}"


def test_every_chats_agent_gets_tools_and_context_outside_the_root(
        tmp_path: Path) -> None:
    home = tmp_path / "studio"
    home.mkdir()
    (home / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"gamestudio": {"command": ".venv/bin/gamestudio",
                                       "args": ["mcp"]}}}), encoding="utf-8")
    context = tmp_path / "context.md"
    context.write_text("# gamestudio context\n", encoding="utf-8")
    for harness in harnesses.HARNESSES:
        args = harnesses.studio_context_args(harness.id, home, context)
        assert args, f"{harness.label} gets no context outside the root"
        if harness.mcp:
            assert harnesses.studio_mcp_args(harness.id, home), harness.label


def test_claude_reads_the_context_as_a_file_and_codex_as_instructions(tmp_path: Path) -> None:
    context = tmp_path / "context.md"
    context.write_text("Project “trial”\n", encoding="utf-8")
    claude = harnesses.studio_context_args("claude", tmp_path, context)
    assert claude == ["--add-dir", str(tmp_path), "--append-system-prompt-file", str(context)]
    codex = harnesses.studio_context_args("codex", tmp_path, context)
    assert codex[0] == "-c" and codex[1].startswith("developer_instructions=")
    # The value is a valid TOML string: Codex reads it back as is.
    import tomllib
    assert tomllib.loads(codex[1])["developer_instructions"] == "Project “trial”\n"


@pytest.fixture
def studio_with_folder(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path / "studio",
                        context_dir=tmp_path / "studio" / "context")
    (tmp_path / "studio").mkdir()
    game = tmp_path / "game"
    (game / "scenes").mkdir(parents=True)
    with using(build(settings)) as current:
        FolderRegistry.for_settings(settings).link("trial", game)
        briefing.ensure()
        yield current, game


def test_a_project_folder_context_carries_notes_and_briefing(
        studio_with_folder) -> None:
    _, game = studio_with_folder
    text = briefing.session_context(game / "scenes")
    assert "project `trial`" in text
    assert 'studio_briefing(project="trial")' in text
    assert "## Note `context/preferences.md`" in text
    assert "## Briefing at opening" in text
    # Copied headings move down one level: a single level-1 heading.
    assert sum(1 for line in text.splitlines() if line.startswith("# ")) == 1
    path = briefing.write_session_context(game)
    assert path.read_text(encoding="utf-8").startswith("# gamestudio context")


def test_a_foreign_folder_says_so(studio_with_folder, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    text = briefing.session_context(elsewhere)
    assert "which is no studio project's folder" in text
    assert "open_project_folder" in text
