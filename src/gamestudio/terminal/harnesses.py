"""The catalog of agents a tab can launch.

It lives here, on the server side, and not in the front: availability is
computed with `shutil.which`, hence where the PATH is real -- the server's --
and the list of agents is a studio operation like any other, defined once then
served by the API.

**No tab inherits the ambient endpoint.** A development machine's environment
readily carries `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` and the
`ANTHROPIC_DEFAULT_*_MODEL` aliases, set by whatever launched the studio.
Letting them through would turn a "Claude Code" tab into another provider's
tab without anything saying so. Each harness therefore removes them before
launching: that is `ANTHROPIC_AMBIENT`, and it is the only change this module
makes to the environment.

The `claude-deepseek` and `claude-mimo` wrappers are user scripts that set
their own endpoint and token: the studio thus has no key to manage, and a tab
costs only a `fork`.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..service.errors import ServiceError

# What a developer's shell may carry unintentionally: the endpoint, the token,
# and the model aliases. Removing them makes each entry deterministic --
# "Claude Code" stays Anthropic, "DeepSeek" stays DeepSeek.
ANTHROPIC_AMBIENT = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
)


@dataclass(frozen=True)
class Harness:
    """An agent that can be launched in a tab.

    `command` is written as in a shell: the first word is the program --
    looked up in the PATH, then replaced by its absolute path -- the following
    ones are its arguments. `unset` is what it must not inherit.

    `journal` names the native transcript format whose end of turn can be read
    (see `transcripts.py`). Empty means "unknown": the tab will then never say
    it is waiting for an answer, rather than saying so wrongly.

    `effort_levels` and `effort_args` say whether the agent accepts an effort
    level, and in what form. A harness that accepts none offers none: the list
    of levels is **empty** rather than guessed, and the interface greys out
    the setting with the reason. Inventing a flag would make a tab fail to
    launch, which is worse than not having the option.

    **Levels belong to the model, not to the program.** `claude-mimo` and
    `claude-deepseek` are the same `claude`, but their endpoints do not accept
    the same efforts. `default_model` is the model the tab launches --
    overridden by the `model_env` variable when the wrapper reads it -- and
    `model_efforts` gives, per model, the levels that actually change
    something there. A model missing from the table falls back on
    `effort_levels`.
    """

    id: str
    label: str
    command: tuple[str, ...]
    detail: str
    unset: tuple[str, ...] = ANTHROPIC_AMBIENT
    journal: str = ""
    effort_levels: tuple[str, ...] = ()
    # The argument template: `{level}` is replaced by the chosen level.
    effort_args: tuple[str, ...] = ()
    default_model: str = ""
    model_env: str = ""
    model_efforts: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # An agent that publishes its own model catalog: its levels are read there
    # instead of being copied here (see `codex_catalog`).
    catalog: str = ""
    # How to pass it the studio's MCP servers on the command line, when the tab
    # opens outside the studio root (see `studio_mcp_args`). Empty: unknown, and
    # the tab only gets what its folder declares.
    mcp: str = ""
    # How to give it the studio's context (`briefing.session_context`) in that
    # same case: `AGENTS.md` and `CLAUDE.md` are at the root, and the agent does
    # not read them from a project folder. Empty: it only has its tools.
    context: str = ""
    # A tab's conversation is named by the studio: `new_args` creates it under a
    # chosen identifier, `resume_args` reopens exactly that one (`{id}` is
    # replaced). This is the only honest resume. "The folder's last
    # conversation" (`--continue`, `resume --last`) takes anyone else's working
    # in the same place -- a Claude Desktop session open on the repository,
    # another tab -- and continues it in its stead: two agents then write the
    # same transcript. Empty when the agent does not let the identifier be
    # chosen at creation: the resumed tab starts a new conversation, rather than
    # a guessed one.
    new_args: tuple[str, ...] = ()
    resume_args: tuple[str, ...] = ()
    # True when the interactive agent accepts its first message as an argument
    # (`claude "..."`, `codex "..."`). Otherwise the message is typed into the
    # tab once the agent has started (see `TerminalManager.type_in`).
    prompt_arg: bool = False

    @property
    def named(self) -> bool:
        """True when the tab can name -- hence find again -- its conversation."""
        return bool(self.new_args and self.resume_args)

    def model(self) -> str:
        """The model the tab launches, as the wrapper will choose it."""
        if self.model_env:
            return os.environ.get(self.model_env) or self.default_model
        return self.default_model

    def levels(self) -> tuple[str, ...]:
        """The effort levels valid for the launched model."""
        return dict(self.model_efforts).get(self.model(), self.effort_levels)


# `claude --help` lists `--effort <level> (low, medium, high, xhigh, max)`; the
# Anthropic tab launches the model of Claude Code's settings (`opus`), which
# accepts all of them.
CLAUDE_EFFORT = ("low", "medium", "high", "xhigh", "max")
# DeepSeek receives `output_config.effort` through its Anthropic-compatible API
# and maps medium and xhigh to high: only low, high and max change anything.
# V4-Pro does not go below high (checked against its `support_efforts` catalog).
DEEPSEEK_EFFORT = (
    ("deepseek-flash", ("low", "high", "max")),
    ("deepseek-v4-flash", ("low", "high", "max")),
    ("deepseek-v4-pro", ("high", "max")),
)
# The MiMo endpoint (Token Plan AMS) only accepts low, medium, high: max and
# minimal answer 400.
MIMO_EFFORT = (
    ("mimo-v2.6-pro", ("low", "medium", "high")),
    ("mimo-v2.6-flash", ("low", "medium", "high")),
)
# Codex has no dedicated flag: it goes through a configuration override,
# `model_reasoning_effort`. Its levels change from one model to another and
# from one CLI version to the next: they are read from its own catalog
# (`codex_catalog`), and this list is only the fallback when it does not
# answer -- the levels shared by all its listed models.
CODEX_EFFORT = ("low", "medium", "high", "xhigh")

# `claude --session-id <uuid>` creates the conversation under this identifier,
# and `claude --resume <uuid>` reopens that one, and no other.
CLAUDE_NEW = ("--session-id", "{id}")
CLAUDE_RESUME = ("--resume", "{id}")

# The first three are the same program: `claude-deepseek` and `claude-mimo` are
# wrappers that set their endpoint then `exec claude`. They thus write the same
# transcript, in the same place, and are read the same way.
HARNESSES = (
    Harness("claude", "Claude Code", ("claude",), "Anthropic's models",
            journal="claude", effort_levels=CLAUDE_EFFORT,
            effort_args=("--effort", "{level}"), default_model="opus", mcp="claude",
            context="claude", new_args=CLAUDE_NEW, resume_args=CLAUDE_RESUME, prompt_arg=True),
    Harness("deepseek", "DeepSeek", ("claude-deepseek",),
            "Claude Code on the DeepSeek endpoint", journal="claude",
            effort_levels=("low", "high", "max"), effort_args=("--effort", "{level}"),
            default_model="deepseek-flash", model_env="CLAUDE_DEEPSEEK_MODEL",
            model_efforts=DEEPSEEK_EFFORT, mcp="claude", context="claude",
            new_args=CLAUDE_NEW, resume_args=CLAUDE_RESUME, prompt_arg=True),
    Harness("mimo", "MiMo", ("claude-mimo",),
            "Claude Code on the MiMo models", journal="claude",
            effort_levels=("low", "medium", "high"), effort_args=("--effort", "{level}"),
            default_model="mimo-v2.6-pro", model_env="CLAUDE_MIMO_MODEL",
            model_efforts=MIMO_EFFORT, mcp="claude", context="claude",
            new_args=CLAUDE_NEW, resume_args=CLAUDE_RESUME, prompt_arg=True),
    # Kimi Code has no effort flag: it reads it from its config.toml. It only
    # takes its MCP servers from its folder's `.mcp.json`, and no flag gives it
    # instructions: outside the root, it only gets read access to the studio
    # (`--add-dir`).
    # Neither Kimi nor Codex lets the identifier of a conversation be chosen at
    # creation: a resumed tab starts a new conversation there.
    Harness("kimi", "Kimi Code", ("kimi",), "Moonshot's Kimi Code CLI", context="kimi"),
    Harness("codex", "Codex", ("codex",), "OpenAI's Codex CLI",
            effort_levels=CODEX_EFFORT,
            effort_args=("-c", "model_reasoning_effort={level}"),
            catalog="codex", mcp="codex", context="codex", prompt_arg=True),
)

DEFAULT = "claude"

_BY_ID = {harness.id: harness for harness in HARNESSES}


def search_path(env: dict[str, str] | None = None) -> str:
    """The process's PATH, extended with the folders where these tools really live.

    Launched from a desktop launcher, the studio does not inherit the shell's
    PATH: it may hold neither `~/.local/bin` nor nvm's bin, and the agents
    would then not be found -- the feature dead on the very path that motivates
    it. The user's own folders therefore come first, deduplicated, followed by
    the ambient PATH.

    This PATH is used twice: to resolve the program, and to hand it to the
    child. Both are needed -- `claude-deepseek` runs `exec claude` and thus
    needs `~/.local/bin` **in its own environment**.
    """
    env = os.environ if env is None else env
    home = Path(env.get("HOME") or Path.home())
    extra = [home / ".local/bin", home / ".kimi-code/bin"]
    nvm = home / ".nvm/versions/node"
    if nvm.is_dir():
        # Several Node versions may coexist; the newest first, so that the choice
        # does not depend on the disk's order.
        versions = sorted((entry for entry in nvm.iterdir() if entry.is_dir()),
                          key=lambda entry: entry.name, reverse=True)
        extra.extend(version / "bin" for version in versions)

    seen: set[str] = set()
    ordered: list[str] = []
    for part in [str(path) for path in extra] + (env.get("PATH") or "").split(os.pathsep):
        if part and part not in seen:
            seen.add(part)
            ordered.append(part)
    return os.pathsep.join(ordered)


@functools.cache
def codex_catalog() -> tuple[str, tuple[str, ...]] | None:
    """The model Codex will launch, and the effort levels it accepts.

    Read from `codex debug models` -- the catalog the CLI ships, with each
    model's levels -- rather than copied: it changes with every CLI version.
    The model is the one in `~/.codex/config.toml` if it names one, otherwise
    the first model listed by priority, which is the CLI's default. `None` when
    Codex does not answer: the caller falls back on its fallback list. Once per
    process: it is a subprocess, and the catalog only changes with the CLI.
    """
    program = shutil.which("codex", path=search_path())
    if program is None:
        return None
    try:
        output = subprocess.run([program, "debug", "models"], capture_output=True,
                                text=True, timeout=15, check=True).stdout
        models = json.loads(output).get("models") or []
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None

    wanted = ""
    config = Path.home() / ".codex/config.toml"
    try:
        wanted = str(tomllib.loads(config.read_text(encoding="utf-8")).get("model") or "")
    except (OSError, tomllib.TOMLDecodeError):
        pass
    listed = sorted((m for m in models if m.get("visibility") == "list"),
                    key=lambda m: m.get("priority") or 0)
    chosen = next((m for m in models if m.get("slug") == wanted), None) or (
        listed[0] if listed else None)
    if chosen is None:
        return None
    levels = tuple(str(entry.get("effort")) for entry in
                   chosen.get("supported_reasoning_levels") or [] if entry.get("effort"))
    return str(chosen.get("slug") or ""), levels


def efforts_for(harness: Harness) -> tuple[str, tuple[str, ...]]:
    """The model a tab will launch, and the effort levels it accepts."""
    if harness.catalog == "codex":
        found = codex_catalog()
        if found is not None and found[1]:
            return found
    return harness.model(), harness.levels()


def _require(harness_id: str) -> Harness:
    harness = _BY_ID.get(harness_id)
    if harness is None:
        known = ", ".join(entry.id for entry in HARNESSES)
        raise ServiceError(f"unknown harness: “{harness_id}” (known: {known})")
    return harness


def resolve(harness_id: str, path: str | None = None) -> dict[str, Any]:
    """A harness's state: where it is, and whether it can be launched.

    `path` makes the check injectable: the catalog is tested without depending
    on the binaries of the machine running the tests.
    """
    harness = _require(harness_id)
    program = harness.command[0]
    found = shutil.which(program, path=search_path() if path is None else path)
    model, levels = efforts_for(harness) if found else (harness.model(), harness.levels())
    return {
        "id": harness.id,
        "label": harness.label,
        "detail": harness.detail,
        "available": found is not None,
        "reason": "" if found else f"“{program}” is not found on the PATH",
        # The effort levels this agent accepts, and why it offers none: the
        # interface greys out the setting instead of hiding it. The levels are
        # those of the model the tab will launch: it is named, so that the
        # choice is an informed one.
        "effort_model": model,
        "effort_levels": list(levels),
        "effort_reason": "" if levels else (
            f"{harness.label} exposes no effort level: the studio does not "
            "guess its flags"),
    }


def effort_args(harness_id: str, level: str) -> list[str]:
    """The arguments carrying the effort level, or a clear refusal.

    An unknown level is refused rather than passed on: an invented flag would
    make the tab fail to launch, and the user would see a dead terminal without
    knowing why.
    """
    if not level:
        return []
    harness = _require(harness_id)
    model, levels = efforts_for(harness)
    if not levels:
        raise ServiceError(f"{harness.label} does not accept an effort level")
    if level not in levels:
        raise ServiceError(
            f"unknown effort level for {harness.label} ({model or 'default model'}): "
            f"“{level}” (known: {', '.join(levels)})")
    return [part.replace("{level}", level) for part in harness.effort_args]


def accepted_effort(harness_id: str, level: str) -> str:
    """The level if it is still valid for the agent's model, otherwise its default.

    For resuming an archived tab: the catalog may have changed since (`minimal`
    left Codex), and an outdated level must not keep the conversation from
    reopening -- it restarts on the agent's default.
    """
    harness = _BY_ID.get(harness_id)
    if harness is None or not level:
        return ""
    return level if level in efforts_for(harness)[1] else ""


def studio_servers(home: Path) -> dict[str, dict[str, Any]]:
    """The MCP servers the studio declares (`.mcp.json`), with absolute paths.

    The repository's `.mcp.json` writes its launchers as relative paths
    (`scripts/mcp/godot`, `.venv/bin/gamestudio`): they only work when launched
    from the root. Here they are anchored to it, and each receives
    `GAMESTUDIO_HOME`, which is enough for the studio's server to find its data
    and its key.
    """
    try:
        raw = json.loads((home / ".mcp.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers: dict[str, dict[str, Any]] = {}
    for name, entry in (raw.get("mcpServers") or {}).items():
        command = str(entry.get("command") or "")
        if not command:
            continue
        if "/" in command and not Path(command).is_absolute():
            command = str(home / command)
        servers[name] = {"command": command, "args": list(entry.get("args") or []),
                         "env": {**(entry.get("env") or {}), "GAMESTUDIO_HOME": str(home)}}
    return servers


def studio_mcp_args(harness_id: str, home: Path) -> list[str]:
    """The arguments that give the agent the studio's MCP servers.

    For a tab opened in a project folder, outside the studio root: the agent
    reads that folder's configuration there, not the studio's, and would
    otherwise lose all its tools. Nothing is written into the folder -- the
    configuration goes through the command line, and only applies to the tab.
    """
    harness = _BY_ID.get(harness_id)
    servers = studio_servers(home)
    if harness is None or not servers:
        return []
    if harness.mcp == "claude":
        return ["--mcp-config", json.dumps({"mcpServers": servers})]
    if harness.mcp == "codex":
        args: list[str] = []
        for name, server in servers.items():
            env = ", ".join(f"{key} = {json.dumps(value)}"
                            for key, value in server["env"].items())
            args += ["-c", f"mcp_servers.{name}.command={json.dumps(server['command'])}",
                     "-c", f"mcp_servers.{name}.args={json.dumps(server['args'])}",
                     "-c", f"mcp_servers.{name}.env={{{env}}}"]
        return args
    return []


def studio_context_args(harness_id: str, home: Path, context: Path) -> list[str]:
    """The arguments that give the agent the studio's context.

    The counterpart of `studio_mcp_args`: tools without instructions leave the
    agent guessing the studio's state. `context` is the file written by
    `briefing.write_session_context`. Claude Code reads it at the end of its
    system prompt; Codex receives it as developer instructions (it has no file
    variant); Kimi only gets access to the studio folder.
    """
    harness = _BY_ID.get(harness_id)
    if harness is None:
        return []
    if harness.context == "claude":
        return ["--add-dir", str(home), "--append-system-prompt-file", str(context)]
    if harness.context == "codex":
        text = context.read_text(encoding="utf-8")
        return ["-c", f"developer_instructions={json.dumps(text)}"]
    if harness.context == "kimi":
        return ["--add-dir", str(home)]
    return []


def command_for(harness_id: str, effort: str = "", conversation: str = "",
                resume: bool = False, first_message: str = "") -> tuple[list[str], tuple[str, ...]]:
    """The command to launch and the variables to remove, or a clear refusal.

    `conversation` is the identifier the studio gives the tab's conversation:
    `resume` reopens it, otherwise it is created under that name. Ignored for an
    agent that cannot name its own (`Harness.named`). These arguments come after
    the effort's. `first_message` is the first message, passed as the last
    argument when the agent accepts it (`Harness.prompt_arg`) -- before the MCP
    servers, which `--mcp-config` reads to the end of the line.
    """
    harness = _require(harness_id)
    program = harness.command[0]
    found = shutil.which(program, path=search_path())
    if found is None:
        raise ServiceError(
            f"“{program}” is not found on the PATH: {harness.label} is not installed")
    extra = effort_args(harness_id, effort)
    tail: tuple[str, ...] = ()
    if conversation and harness.named:
        template = harness.resume_args if resume else harness.new_args
        tail = tuple(part.replace("{id}", conversation) for part in template)
    if first_message and harness.prompt_arg:
        tail = (*tail, first_message)
    return [found, *harness.command[1:], *extra, *tail], harness.unset

def takes_prompt(harness_id: str) -> bool:
    """True when the harness takes its first message as an argument."""
    harness = _BY_ID.get(harness_id)
    return harness is not None and harness.prompt_arg


def names_conversation(harness_id: str) -> bool:
    """True when the harness lets the studio name its conversation."""
    harness = _BY_ID.get(harness_id)
    return harness is not None and harness.named


def journal_for(harness_id: str) -> str:
    """A harness's transcript format, empty when it cannot be read.

    Does not raise on an unknown identifier: it is a question, not an order,
    and a tab launched with an explicit command has no harness at all.
    """
    harness = _BY_ID.get(harness_id)
    return harness.journal if harness is not None else ""
