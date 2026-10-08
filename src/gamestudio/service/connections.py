"""MCP connections: what is plugged in, where it is declared, and whether it can start.

An agent working here has two sets of tools: those of its own MCP servers, and
the studio's. The latter is declared by the repository (`.mcp.json`); the
former comes from **the agent's** configuration, which is not the studio's and
so is not changed here.

This operation only reads, on purpose: writing into an agent's global
configuration from a studio interface would be an unpleasant surprise. What it
offers is an answer to the real question -- "what is plugged in, from where, and
does it start on this machine?" -- instead of a search through several files.

Two formats, because agents do not read the same files: JSON (`.mcp.json`,
`~/.claude.json`) and TOML (`~/.codex/config.toml`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .context import studio

# The studio server's name, as it must be declared.
STUDIO_SERVER = "gamestudio"

# Companions: third-party servers the studio does not drive, but that an agent
# chains with the studio's (skill `blender-godot-bridge`). The studio can tell
# their state: Blender is useless unless its addon listens in an open session,
# Godot needs its binary. Recognized by the start of their name.
COMPANIONS: dict[str, str] = {
    "blender": "Open Blender, driven by its MCP addon",
    "godot": "Godot editor: open, run, read the debug output",
}
# The port `scripts/mcp/blender` gives blender-mcp when `BLENDER_PORT` is unset:
# 9877 rather than blender-mcp's default 9876; the addon is set the same way.
BLENDER_DEFAULT_PORT = 9877

# `${VAR}` and `${VAR:-default}`: the syntax Claude Code expands in `.mcp.json`,
# which lets it cite `${HOME}` without pinning a machine. The studio expands it
# only to know whether a server starts; it returns the declaration as written,
# since `${VAR}` often cites a key there.
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _root() -> Path:
    settings = studio().settings
    return settings.project_root or Path.cwd()


def _sources() -> list[dict[str, Any]]:
    """The places where a connection can be declared.

    None is mandatory: a machine without a global connection is a normal state,
    and the panel must say so rather than look broken.
    """
    home = Path.home()
    return [
        {
            "id": "project",
            "label": "This repository",
            "path": _root() / ".mcp.json",
            "format": "json",
            "key": "mcpServers",
            "relative_to_root": True,
            "detail": "Read by the agent started in this folder. This is where the studio "
                      "declares its own server.",
        },
        {
            "id": "claude",
            "label": "Claude Code (global)",
            "path": home / ".claude.json",
            "format": "json",
            "key": "mcpServers",
            "relative_to_root": False,
            "detail": "The agent's own configuration: it does not belong to the studio, and this "
                      "panel does not change it.",
        },
        {
            "id": "codex-project",
            "label": "Codex (this repository)",
            "path": _root() / ".codex" / "config.toml",
            "format": "toml",
            "key": "mcp_servers",
            "relative_to_root": True,
            "detail": "The same servers as `.mcp.json`, for Codex. It only reads this file if the "
                      "repository is trusted.",
        },
        {
            "id": "gemini-project",
            "label": "Gemini (this repository)",
            "path": _root() / ".gemini" / "settings.json",
            "format": "json",
            "key": "mcpServers",
            "relative_to_root": True,
            "detail": "The same servers as `.mcp.json`, for Gemini CLI.",
        },
        {
            "id": "codex",
            "label": "Codex (global)",
            "path": home / ".codex" / "config.toml",
            "format": "toml",
            "key": "mcp_servers",
            "relative_to_root": False,
            "detail": "Codex CLI configuration (`mcp_servers`). Accounts and keys stay in its "
                      "secure storage.",
        },
        {
            "id": "gemini",
            "label": "Gemini (global)",
            "path": home / ".gemini" / "settings.json",
            "format": "json",
            "key": "mcpServers",
            "relative_to_root": False,
            "detail": "Gemini CLI configuration.",
        },
        {
            "id": "kimi",
            "label": "Kimi (global)",
            "path": home / ".kimi-code" / "mcp.json",
            "format": "json",
            "key": "mcpServers",
            "relative_to_root": False,
            "detail": "Kimi Code configuration. Kimi also reads the repository's `.mcp.json`, but "
                      "does not expand `${VAR}` in it.",
        },
    ]


def _codex_trusts_root() -> bool:
    """Codex ignores the configuration of a repository it does not trust.

    Trust is written to `~/.codex/config.toml` (`[projects."<path>"]`,
    `trust_level = "trusted"`) when granted on first start.
    """
    path = Path.home() / ".codex" / "config.toml"
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    projects = document.get("projects") or {}
    root = _root().resolve()
    for key, entry in projects.items():
        if not isinstance(entry, dict) or entry.get("trust_level") != "trusted":
            continue
        # A trusted parent repository covers this one too.
        if root == Path(key).expanduser().resolve() or \
                Path(key).expanduser().resolve() in root.parents:
            return True
    return False


def _load(source: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """A source's server table, and the error if any."""
    path: Path = source["path"]
    if not path.is_file():
        return {}, ""
    try:
        raw = path.read_bytes()
        if source["format"] == "toml":
            document = tomllib.loads(raw.decode("utf-8"))
        else:
            document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        return {}, f"unreadable: {exc}"
    servers = document.get(source["key"]) or {}
    if not isinstance(servers, dict):
        return {}, f"`{source['key']}` is not a table of servers"
    return servers, ""


def _expand(text: str, env: dict[str, str] | None = None) -> str:
    """Expand `${VAR}` / `${VAR:-default}` as the agent does."""
    scope = {**os.environ, **(env or {})}
    return _VAR.sub(lambda match: scope.get(match.group(1)) or (match.group(2) or ""), text)


def _script_missing(first_arg: str) -> bool:
    """Is the script an interpreter must run (`node /…/index.js`) missing?

    `node` almost always exists: the script is what tells whether the server is
    installed. Only an absolute first argument (once expanded) with a script
    extension is checked -- the rest belongs to the server.
    """
    first = Path(first_arg).expanduser()
    return first.is_absolute() and first.suffix in {".js", ".mjs", ".cjs", ".py"} \
        and not first.exists()


def _companion(name: str, env: dict[str, str]) -> dict[str, Any] | None:
    """A companion server's state: ready to serve, or why not.

    Blender: does the addon listen on its port? Without an open session, the
    MCP server starts but each of its tools fails -- the breakdown to see
    before an agent stumbles on it. Godot: the binary the server will use.
    """
    role = next((key for key in COMPANIONS if name.lower().startswith(key)), "")
    if not role:
        return None
    if role == "blender":
        host = env.get("BLENDER_HOST") or "localhost"
        try:
            port = int(env.get("BLENDER_PORT") or BLENDER_DEFAULT_PORT)
        except ValueError:
            return {"role": role, "label": COMPANIONS[role], "ready": False,
                    "detail": f"unreadable BLENDER_PORT: {env.get('BLENDER_PORT')}"}
        try:
            with socket.create_connection((host, port), timeout=0.3):
                ready = True
        except OSError:
            ready = False
        detail = (f"addon listening on {host}:{port}" if ready else
                  (f"nothing listening on {host}:{port}: open Blender, then “Connect to Claude” in "
                   "the BlenderMCP tab (N)"))
        return {"role": role, "label": COMPANIONS[role], "ready": ready, "detail": detail}
    binary = env.get("GODOT_PATH") or shutil.which("godot") or ""
    ready = bool(binary) and Path(binary).exists()
    detail = (f"Godot: {binary}" if ready else
              "Godot binary not found: set GODOT_PATH")
    return {"role": role, "label": COMPANIONS[role], "ready": ready, "detail": detail}


def _resolve(command: str, *, relative_to_root: bool) -> bool:
    """Can a connection's program start on this machine?

    A relative path (`.venv/bin/gamestudio`) resolves against the repository
    for the project source -- as the agent does -- and against the PATH
    otherwise.
    """
    if not command:
        return False
    if "/" in command:
        candidate = Path(command).expanduser()
        if relative_to_root and not candidate.is_absolute():
            candidate = _root() / candidate
        return candidate.exists()
    return shutil.which(command) is not None


def _server(name: str, entry: Any, *, relative_to_root: bool) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {"name": name, "transport": "?", "command": "", "args": [],
                "available": False, "reason": "unreadable declaration", "studio": False,
                "companion": None}
    raw_env = entry.get("env") or {}
    env = {str(key): _expand(str(value)) for key, value in raw_env.items()} \
        if isinstance(raw_env, dict) else {}
    command = str(entry.get("command", "") or "")
    args = [str(arg) for arg in (entry.get("args") or [])]
    url = str(entry.get("url", "") or "")
    if url:
        return {"name": name, "transport": "remote", "command": "", "args": [],
                "url": url, "available": True,
                "reason": "remote connection: nothing to resolve locally",
                "studio": name == STUDIO_SERVER, "companion": None}
    found = _resolve(_expand(command, env), relative_to_root=relative_to_root)
    reason = "" if found else f"“{command}” not found"
    if found and args and _script_missing(_expand(args[0], env)):
        found, reason = False, f"“{args[0]}” not found"
    return {
        "name": name,
        "transport": "stdio",
        "command": command,
        "args": args,
        "url": "",
        "available": found,
        "reason": reason,
        "studio": name == STUDIO_SERVER,
        "companion": _companion(name, env),
    }


def connections() -> dict[str, Any]:
    """Every known MCP connection, by source, with its state.

    Read-only: nothing is written, here or in an agent's configuration.
    """
    sources: list[dict[str, Any]] = []
    for source in _sources():
        servers, error = _load(source)
        warning = ""
        if source["id"] == "codex-project" and servers and not _codex_trusts_root():
            warning = ("repository not trusted by Codex: this file is ignored until “trust” is "
                        "accepted when starting `codex` here")
        sources.append({
            "id": source["id"],
            "warning": warning,
            "label": source["label"],
            "path": str(source["path"]),
            "exists": source["path"].is_file(),
            "detail": source["detail"],
            "error": error,
            "servers": [
                _server(name, entry, relative_to_root=source["relative_to_root"])
                for name, entry in sorted(servers.items())
            ],
        })

    project = next(entry for entry in sources if entry["id"] == "project")
    # The repository's declarations for other CLIs repeat `.mcp.json`: they are
    # not global connections.
    global_servers = [server for source in sources
                      if source["id"] not in {"project", "codex-project", "gemini-project"}
                      for server in source["servers"]]
    return {
        "generated_at": _now_iso(),
        "root": str(_root()),
        "sources": sources,
        "counts": {
            "project": len(project["servers"]),
            "global": len(global_servers),
            "available": sum(1 for source in sources for server in source["servers"]
                             if server["available"]),
            "unavailable": sum(1 for source in sources for server in source["servers"]
                               if not server["available"]),
        },
        "declared_here": any(server["studio"] for server in project["servers"]),
        "notes": [
            f"The studio server is declared in the repository's `.mcp.json`: `{STUDIO_SERVER}` "
            "runs `gamestudio mcp`.",
            "From another repository, plugging it in takes the absolute path of the binary and "
            "`GAMESTUDIO_HOME=<studio root>`: that variable is enough to find the data and the "
            "API key.",
            "`blender` and `godot` are companions: declared for each CLI (`.mcp.json` for Claude "
            "Code and Kimi, `.codex/config.toml`, `.gemini/settings.json`) through the "
            "`scripts/mcp/` launchers, so that an agent can chain them with the studio (skill "
            "`blender-godot-bridge`). Blender is ready only when a session is open with its addon "
            "connected.",
        ],
    }
