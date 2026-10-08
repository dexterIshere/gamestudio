"""The code map: one line per file, and a stale map shows."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.service import build, codemap, using
from gamestudio.service.context import Studio


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[Studio]:
    """A tiny repository: a Python module, a page, a Rust file."""
    package = tmp_path / "src" / "gamestudio"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "rig.py").write_text('"""The rig: bones and joints.\n\nDetail."""\n')
    pages = tmp_path / "app" / "src" / "pages"
    pages.mkdir(parents=True)
    (pages / "Home.tsx").write_text("/**\n * Home: what is waiting.\n *\n * More.\n */\n")
    shell = tmp_path / "app" / "src-tauri" / "src"
    shell.mkdir(parents=True)
    (shell / "lib.rs").write_text("//! Shell: two windows.\n//!\n//! More.\nuse x;\n")
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context")
    with using(build(settings)) as current:
        yield current


def test_the_map_takes_the_opening_sentence(repository: Studio) -> None:
    text = codemap.codemap()["markdown"]
    assert "`src/gamestudio/rig.py` — The rig: bones and joints." in text
    assert "`app/src/pages/Home.tsx` — Home: what is waiting." in text
    assert "`app/src-tauri/src/lib.rs` — Shell: two windows." in text
    # An empty __init__ teaches nothing: it stays off the map.
    assert "__init__.py" not in text


def test_a_stale_map_is_refused(repository: Studio) -> None:
    assert codemap.check()["problem"] == "map missing"
    codemap.write()
    assert codemap.check()["ok"]
    root = repository.settings.project_root
    assert root is not None
    (root / "src" / "gamestudio" / "fresh.py").write_text('"""A fresh module."""\n')
    assert codemap.check()["problem"] == "map out of date"
