"""A single version number, and mirrors that agree.

The version is written in places that cannot import each other:
`gamestudio/__init__.py` holds it for Python, and the three files of the Tauri
application copy it -- npm and Cargo do not read Python. This test keeps them
in agreement.
"""

from __future__ import annotations

import json
from pathlib import Path

import gamestudio

ROOT = Path(__file__).resolve().parents[1]


def _tauri_field(name: str) -> str:
    content = (ROOT / "app" / "src-tauri" / "tauri.conf.json").read_text("utf-8")
    return json.loads(content)[name]


def test_pyproject_reads_the_version_instead_of_copying_it():
    """Two numbers to maintain is one too many: the package reads the code's."""
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]

    assert project["dynamic"] == ["version"]
    assert "version" not in project


def test_the_application_mirrors_state_the_same_version():
    package = json.loads((ROOT / "app" / "package.json").read_text("utf-8"))

    assert package["version"] == gamestudio.__version__
    assert _tauri_field("version") == gamestudio.__version__
    # Cargo writes `version = "x"` on the third line of its header.
    cargo = (ROOT / "app" / "src-tauri" / "Cargo.toml").read_text("utf-8")
    line = next(ln for ln in cargo.splitlines() if ln.startswith("version = "))
    assert line.split('"')[1] == gamestudio.__version__
