"""The diagnosis: what works here, what is missing, and what to do.

A studio that refuses to produce without saying why wastes time. This module
answers one question -- "why does it not work on this machine?" -- in three
states, never more:

- **ok**: it is in place;
- **warning**: the studio runs, a capability is missing (3D without Blender,
  generation without an API key). Not a breakdown: a limit;
- **failed**: the studio cannot work (no project root, data not writable).

Each line carries **the fix**, not just the finding: a Blender version becomes
a command, a missing key the URL where to get it.

`gamestudio doctor --check` exits with an error as soon as a **failure** is
present: that is what a script, a `Makefile` or an agent can query. Warnings do
not fail the check -- a studio without Godot is perfectly usable, it just
validates less.
"""

from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .context import studio

OK = "ok"
WARN = "warning"
FAIL = "failed"

# Below this, little can be kept: a mesh, a few sheets and a LoRA take a few
# gigabytes.
MIN_FREE_GB = 2.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _entry(identifier: str, label: str, status: str, detail: str, *,
           fix: str = "", needed: bool = False) -> dict[str, Any]:
    return {"id": identifier, "label": label, "status": status, "detail": detail,
            "fix": fix, "needed": needed}


def _python() -> dict[str, Any]:
    """The interpreter running the studio.

    Nothing to check here: the package requires Python 3.11 and does not import
    below it, so an older interpreter would never reach this line. This is
    information -- which Python runs -- not a criterion.
    """
    version = ".".join(str(part) for part in sys.version_info[:3])
    return _entry("python", "Python", OK, f"{version} ({sys.executable})")


def _root(root: Path | None) -> dict[str, Any]:
    if root is None:
        return _entry("project", "Studio root", FAIL,
                      "no `.env` here or above",
                      fix="run from the studio, or set GAMESTUDIO_HOME=<root>", needed=True)
    return _entry("project", "Studio root", OK, str(root))


def _data(directory: Path) -> dict[str, Any]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return _entry("data", "Data folder", FAIL,
                      f"{directory} — {exc}",
                      fix="fix GAMESTUDIO_DATA_DIR, or the folder permissions",
                      needed=True)
    free = shutil.disk_usage(directory).free / (1024 ** 3)
    if free < MIN_FREE_GB:
        return _entry("data", "Data folder", WARN,
                      f"{directory} — {free:.1f} GB free",
                      fix="free some space: meshes and sheets quickly weigh several gigabytes")
    return _entry("data", "Data folder", OK,
                  f"{directory} — {free:.1f} GB free")


def _key(key: str) -> dict[str, Any]:
    if key:
        return _entry("runware-key", "Runware key", OK, f"…{key[-6:]}")
    return _entry("runware-key", "Runware key", WARN, "missing",
                  fix="set RUNWARE_API_KEY in `.env` (https://my.runware.ai/keys) — without it, "
                      "no image or mesh generation")


def _tripo_key(key: str) -> dict[str, Any]:
    """The direct path's key: its absence blocks nothing else.

    It reads "missing", not "broken": the whole studio produces without it,
    only P2 and the H family stay out of reach.
    """
    if key:
        return _entry("tripo-key", "Tripo key (direct path)", OK, f"…{key[-6:]}")
    return _entry("tripo-key", "Tripo key (direct path)", WARN, "missing",
                  fix="set TRIPO_API_KEY in `.env` (https://platform.tripo3d.ai) — without it, 3D "
                      "goes through Runware alone, which hosts a single Tripo model (v3.1): no "
                      "P2, no native quads, no H family")


def _blender(binary: str) -> dict[str, Any]:
    from ..blender.run import blender_version

    version = blender_version(binary)
    if version:
        return _entry("blender", "Blender", OK, version)
    return _entry("blender", "Blender", WARN, f"not found: {binary}",
                  fix="install Blender 4.2+ and put it on the PATH, or set BLENDER_BIN in `.env` "
                      "— without it, no 3D and no sprites")


def _godot() -> dict[str, Any]:
    found = shutil.which("godot") or shutil.which("godot4")
    if found:
        return _entry("godot", "Godot", OK, found)
    return _entry("godot", "Godot", WARN, "not found",
                  fix="install Godot 4: it validates the exported scenes (`gamestudio "
                      "validate-godot`) and draws the game renders (`render_scene`) — without it, "
                      "nothing proves them")


def _offscreen() -> dict[str, Any]:
    """Where the engine draws a render without opening a window (`service/renders.py`)."""
    for binary, label in (("gamescope", "headless gamescope"), ("xvfb-run", "xvfb-run")):
        found = shutil.which(binary)
        if found:
            return _entry("offscreen", "Off-screen rendering", OK, f"{label} — {found}")
    return _entry("offscreen", "Off-screen rendering", WARN, "no virtual display",
                  fix="install xorg-server-xvfb (or gamescope): without them, each render of a "
                      "game scene opens a window for a moment")


def _ffmpeg() -> dict[str, Any]:
    found = shutil.which("ffmpeg")
    if found:
        return _entry("ffmpeg", "ffmpeg", OK, found)
    return _entry("ffmpeg", "ffmpeg", WARN, "not found",
                  fix="install ffmpeg to extract frames from a reference video (`video_frames`)")


def _rigtools() -> dict[str, Any]:
    from ..vision.detect import available

    tools = available()
    missing = [name for name, ready in tools.items() if not ready]
    if not missing:
        return _entry("local-tools", "Local tools (pose, cut-out)", OK,
                      ", ".join(sorted(tools)) or "no tool declared")
    return _entry("local-tools", "Local tools (pose, cut-out)", WARN,
                  f"missing: {', '.join(sorted(missing))}",
                  fix="install the local extras (`make install-rigtools`) — without them, a "
                      "concept's pose is not checked and cut-out falls back to the background "
                      "color")


def _skills() -> dict[str, Any]:
    from . import skills as service

    state = service.check()
    if state["ok"]:
        return _entry("skills", "Skills", OK,
                      f"{state['skills']} procedures, index up to date, mirror intact")
    problems = "; ".join(f"{entry['skill']}: {entry['problem']}"
                         for entry in state["problems"][:3])
    return _entry("skills", "Skills", WARN, problems or "no skill",
                  fix="`gamestudio skills index` and `gamestudio skills sync`")


def _context() -> dict[str, Any]:
    from . import briefing

    entries = briefing.files()
    missing = [entry["name"] for entry in entries
               if not entry["generated"] and not entry["exists"]]
    if missing:
        return _entry("context", "Agent context", WARN,
                      f"missing notes: {', '.join(missing)}",
                      fix="`gamestudio context path` lists them — agents read them at the start "
                          "of each session")
    return _entry("context", "Agent context", OK,
                  f"{len(entries)} files, including the regenerated briefing")


def _connections() -> dict[str, Any]:
    from . import connections as service

    data = service.connections()
    if data["declared_here"]:
        return _entry("mcp", "Studio MCP server", OK,
                      f"declared in {data['sources'][0]['path']}")
    return _entry("mcp", "Studio MCP server", WARN,
                  "not declared in `.mcp.json`",
                  fix="add the command `.venv/bin/gamestudio mcp` to it (see `gamestudio "
                      "connections`) — that is what gives an agent the studio's tools")


def _queue() -> dict[str, Any]:
    # `stats` returns a count per state ("pending", "running", "failed"...).
    from .catalog import queue_stats

    states = queue_stats()
    running = states.get("running", 0)
    pending = states.get("pending", 0)
    failed = states.get("failed", 0)
    detail = f"{pending} pending, {running} running, {failed} failed"
    if failed:
        return _entry("queue", "Task queue", WARN, detail,
                      fix="`gamestudio status` shows what failed; a failed task does not replay "
                          "by itself")
    return _entry("queue", "Task queue", OK, detail)


def check() -> dict[str, Any]:
    """The state of the environment, line by line, with the fix."""
    st = studio()
    settings = st.settings

    checks = [
        _python(),
        _root(settings.project_root),
        _data(settings.data_dir),
        _key(settings.runware_api_key),
        _tripo_key(settings.tripo_api_key),
        _blender(settings.blender_bin),
        _godot(),
        _offscreen(),
        _ffmpeg(),
        _rigtools(),
        _skills(),
        _context(),
        _connections(),
        _queue(),
    ]
    failures = [entry for entry in checks if entry["status"] == FAIL]
    warnings = [entry for entry in checks if entry["status"] == WARN]
    return {
        "generated_at": _now_iso(),
        "ok": not failures,
        "version": _version(),
        "counts": {"checks": len(checks), "ok": len(checks) - len(failures) - len(warnings),
                   "warnings": len(warnings), "failures": len(failures)},
        "checks": checks,
        # What prevents working comes first: it is the only thing a user in a
        # hurry must read.
        "blocking": [entry["label"] for entry in failures],
        "next": [entry["fix"] for entry in (*failures, *warnings) if entry["fix"]][:5],
    }


def _version() -> str:
    from .. import __version__

    return __version__


def refuse(failures: int) -> str:
    """The message of a failing `--check`: short, and it says what to do."""
    if failures == 1:
        return "1 failure: the studio cannot work here."
    return f"{failures} failures: the studio cannot work here."
