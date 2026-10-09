"""A conversation on the graphic style or the game type, with an agent the studio drives.

The Universe's Graphic style and Game type sections hold a messaging thread:
the user writes, an agent answers. The agent is Claude Code run headless by
the studio (`claude --print`, its JSON stream read as it comes), in the game's
folder. It reads the game and the art direction's cards, and writes the aspect's
cards -- each part in its own. The board of influences (`influences`) is the
user's: the agent reads it, never changes it. It proposes images only when
asked, from what it sees in the influences' images, and never pays: a proposal
shows in the thread with its amount, and the user pays it there.

An image prompt can also be asked for alone (`draft`): one short turn of a
model that may only look at the influences' images it is given, and writes the
prompt from what it sees there -- the cheap way to the generation the user
wants.

What it may do is fixed on its command line, without asking: read the game
(`Read`, `Glob`, `Grep`), and a short list of the studio's tools -- nothing
that runs code, writes into the game, changes the board or spends. The model
is chosen in the thread (`MODELS`, the lightest that writes well by default).
The thread lives beside the Universe's state (`lookdev/<aspect>.chat.json`);
the agent's own memory of it is Claude Code's transcript (`--session-id`, then
`--resume`). One turn at a time per thread; a turn can be stopped.
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..store.folders import project_paths
from ..terminal import harnesses
from . import documents, influences, lookdev
from .context import studio
from .errors import NotFound, ServiceError

HARNESS = "claude"
MAX_MESSAGE = 4000
# A thread keeps its latest messages; the agent's transcript keeps the rest.
MAX_MESSAGES = 400
# The thread is written at most this often while the agent writes.
FLUSH_SECONDS = 0.3
MAX_STEPS = 60
MAX_ERROR = 600
STOP_SECONDS = 3.0
# Everything the agent may do without asking: read the game; read and write
# the art direction's cards; work the board; look at images and at the game.
TOOLS = ("Read", "Glob", "Grep")
STUDIO_TOOLS = ("influence_board", "influence_propose", "list_documents", "read_document",
                "write_document", "document_templates", "view_asset", "lookdev",
                "render_scene")
STUDIO_PREFIX = "mcp__gamestudio__"
LANGUAGES = {"fr": "French", "en": "English"}
# The models a thread can run on, as Claude Code names them; the first by default.
MODELS = ("sonnet", "opus", "haiku")
# A prompt drafted from images: the model that looks, how long it may take,
# and how many images it is shown.
DRAFT_MODEL = "sonnet"
DRAFT_SECONDS = 180.0
MAX_DRAFT_IMAGES = 12
MAX_DRAFT_REQUEST = 1000
# The written graphic style an agent is handed at each message: beyond this, it
# reads the card itself.
MAX_STYLE_CONTEXT = 3000


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _file(project: str, aspect: str) -> Path:
    influences._aspect(aspect)
    return documents.directory(project) / influences.STATE_FOLDER / f"{aspect}.chat.json"


def _load(path: Path) -> dict[str, Any]:
    held: dict[str, Any] = {"session": "", "messages": []}
    if path.is_file():
        try:
            held = {**held, **json.loads(path.read_text(encoding="utf-8"))}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError(f"unreadable thread: {path} ({exc})") from exc
    return held


def _save(path: Path, held: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    held["messages"] = held["messages"][-MAX_MESSAGES:]
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(held, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class _Turn:
    """A turn under way: the agent's process, and the thread it writes into."""

    def __init__(self, path: Path, held: dict[str, Any], root: Path) -> None:
        self.path = path
        self.held = held
        self.root = root
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.stopped = False
        self.error = ""
        self.finished = False
        self.streamed = False
        self.flushed = 0.0
        self.stderr: collections.deque[str] = collections.deque(maxlen=12)

    @property
    def answer(self) -> dict[str, Any]:
        return self.held["messages"][-1]


_TURNS: dict[str, _Turn] = {}
_TURNS_LOCK = threading.Lock()


def thread(project: str, aspect: str) -> dict[str, Any]:
    """The thread: its messages, and whether the agent is answering."""
    path = _file(project, aspect)
    with _TURNS_LOCK:
        turn = _TURNS.get(str(path))
    if turn is not None:
        with turn.lock:
            held = json.loads(json.dumps(turn.held))
    else:
        held = _load(path)
    return {"project": project, "aspect": aspect, "running": turn is not None,
            "session": held["session"], "messages": held["messages"]}


def _held(project: str) -> str:
    """What the art direction holds, handed to the agent at each message: every
    aspect's influences and the graphic style as written, so that a card or an
    image prompt builds on them."""
    held = influences.context(project)
    lines: list[str] = []
    for name, spec in influences.ASPECTS.items():
        lines.append(f"  Influences of {spec['subject']} (`aspect=\"{name}\"`):")
        for entry in held[name]["influences"]:
            line = f"  - **{entry['name']}** ({entry['kind']}, id `{entry['id']}`"
            line += f", blend of {', '.join(entry['of'])})" if entry.get("of") else ")"
            if entry.get("keep"):
                line += f" -- keep: {entry['keep']}"
            if entry.get("avoid"):
                line += f" -- leave: {entry['avoid']}"
            lines.append(line)
            lines += [f"    - image: `{path}`" for path in entry["images"]]
            if not entry["images"]:
                lines.append("    - no image yet")
        if not held[name]["influences"]:
            lines.append("  - none yet")
    ruled = lookdev.rule_lines(project)
    if ruled:
        lines += ["  Rules the user ticked:", *(f"  {line}" for line in ruled)]
    look = held["style"]["parts"]["look"]
    if look["written"]:
        text = look["text"].strip()
        if len(text) > MAX_STYLE_CONTEXT:
            text = text[:MAX_STYLE_CONTEXT].rsplit("\n", 1)[0] + "\n[…] (the rest is in the card)"
        lines += ["  The graphic style as written (`graphic-style`):", "",
                  *(f"    {line}" for line in text.splitlines()), ""]
    else:
        lines.append("  The graphic style is not written yet.")
    return "\n".join(lines)


def _system(project: str, aspect: str, lang: str) -> str:
    """What the agent knows before the first word: its role, its subject, its tools, its limits."""
    spec = influences.ASPECTS[aspect]
    language = LANGUAGES.get(lang, "the user's language")
    shelf = documents.directory(project, influences.FOLDER)
    folder = influences.FOLDER
    call = f'project="{project}", aspect="{aspect}"'
    parts = "\n".join(f"  - `{part['card']}` (template `{part['template']}`) -- {part['about']}"
                      for part in spec["parts"])
    board = spec["board"]["card"]
    return f"""You are the user's partner on {spec['subject']} of the game of project \
`{project}`, in a chat inside the studio's Universe page. Answer in {language}. The user \
reads you in chat bubbles: short paragraphs, light Markdown (bold, short lists), no headings, \
no tables.

- Your working directory is the game's folder: read it (Read, Glob, Grep). Its docs are \
authoritative: cite them, never copy them. You never modify the game.
- This aspect's cards, in the art direction's shelf `{shelf}`, each shown in its own section \
of the page:
{parts}
  - `{board}` (template `{influences.BOARD_TEMPLATE}`) -- the influences in writing: each one, \
what is kept and what is left.
  Read one with `read_document(project="{project}", name=…, folder="{folder}")`; one that does \
not exist yet starts from its template (`document_templates`). When the user settles something, \
write the card it belongs to with `write_document(project="{project}", name=…, \
folder="{folder}", text=…)` entirely in their language: the templates are written in English \
for the studio, never for the user, so translate their headings too -- same sections, same \
order. Say so in one line. Each card holds its own \
subject only. The other cards of the shelf \
(`list_documents(project="{project}", folder="{folder}")`) are the rest of the art direction.
- Beside this chat is the board of influences, and it is the user's: they add, rename, \
change and remove influences and their images -- never you, even when the board is empty or \
a card cites works it lacks; at most, suggest one in a sentence. `influence_board({call})` \
reads it.
- What the art direction holds today -- have it in mind before writing a card or proposing an \
image:
{_held(project)}
  Everything you write builds on these influences: keep what they keep, leave what they leave.
- The graphic style, written by you, comes from the influences' images when there are any: \
look at every one of them first (Read each path listed above), and describe what you see in \
them -- technique, shapes and proportions, outline, light and shadow, matter and detail, \
camera -- with what each keeps and leaves. The game as it is comes second, to say where it \
already matches and where it departs. With no image on the board, say so in one line, then \
describe from the game.
- Propose images only when the user asks for images. First look at the images of the \
influences the request draws on (Read each path listed above), then write the prompt from what \
you see in them -- shapes, matter, light, palette, composition, rendering -- with what each \
keeps and leaves; never from their names alone. If none of them has an image, say so and ask \
the user to drop some: do not propose. `influence_propose({call}, influence=…, prompt=…, \
count=…, model=…, why=…)`; `influence=""` for images of the aspect as a whole, which go to its \
gallery. The user sees the proposal in this chat with its price and pays it, or not: you \
never generate or pay yourself, and you do not ask them to confirm in words. FLUX schnell \
(`runware:100@1`, ~$0.0013 an image) to explore, FLUX dev (`runware:101@1`, ~$0.006) for \
the image that sums an influence up, Kontext (`runware:106@1`, ~$0.04) only to touch up an \
image the user dropped (`reference="ref:<file>"`). Write prompts in English, concrete: \
subject, composition, matter, light, palette; describe what you saw rather than naming \
protected works or characters. Propose little at a time and say why in one line.
- Images on the board come with their path: look at them (`view_asset`, or Read) before \
talking about them.
- The game as it is: `lookdev(project="{project}")` gives its palette, typography and shaders; \
`render_scene` draws one of its scenes with its own engine (free).
- Do what the user asks, and only that. Read the game and the cards as far as the request \
needs, no further: every file read is paid for in time and tokens."""


def _command(project: str, aspect: str, session: str, resume: bool, system: str,
             model: str) -> tuple[list[str], tuple[str, ...]]:
    home = Path(studio().settings.project_root or Path.cwd())
    servers = {name: server for name, server in harnesses.studio_servers(home).items()
               if name == "gamestudio"}
    if not servers:
        raise ServiceError("the studio's MCP server is not declared (`.mcp.json`): the agent "
                           "would have no tools")
    command, unset = harnesses.command_for(HARNESS, conversation=session, resume=resume)
    allowed = [*TOOLS, *(f"{STUDIO_PREFIX}{tool}" for tool in STUDIO_TOOLS)]
    # The message goes through stdin: `--allowedTools` and `--mcp-config` take
    # every word that follows them.
    return [*command, "--print", "--model", model, "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--setting-sources", "",
            "--tools", ",".join(TOOLS), "--permission-mode", "dontAsk",
            "--append-system-prompt", system, "--allowedTools", *allowed,
            "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": servers})], unset


def send(project: str, aspect: str, text: str, *, lang: str = "",
         model: str = "") -> dict[str, Any]:
    """Write to the agent; it answers in the thread as it goes. `model`: one of `MODELS`."""
    path = _file(project, aspect)
    model = model or MODELS[0]
    if model not in MODELS:
        raise ServiceError(f"unknown model: {model} (offered: {', '.join(MODELS)})")
    message = text.strip()
    if not message:
        raise ServiceError("empty message: say what you want")
    if len(message) > MAX_MESSAGE:
        raise ServiceError(f"message too long: {len(message)} characters (at most {MAX_MESSAGE})")
    root = project_paths(studio().settings, project).root
    with _TURNS_LOCK:
        if str(path) in _TURNS:
            raise ServiceError("the agent is still answering: wait for it, or stop it")
        held = _load(path)
        session, resume = held["session"] or str(uuid.uuid4()), bool(held["session"])
        command, unset = _command(project, aspect, session, resume,
                                  _system(project, aspect, lang), model)
        held["session"] = session
        held["messages"] += [
            {"id": uuid.uuid4().hex[:12], "role": "user", "text": message, "at": _now()},
            {"id": uuid.uuid4().hex[:12], "role": "assistant", "text": "", "steps": [],
             "state": "running", "error": "", "model": model, "at": _now()}]
        _save(path, held)
        turn = _Turn(path, held, root)
        env = {key: value for key, value in os.environ.items() if key not in unset}
        env["PATH"] = harnesses.search_path()
        try:
            turn.process = subprocess.Popen(
                command, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                bufsize=1)
        except OSError as exc:
            turn.answer.update(state="failed", error=str(exc)[:MAX_ERROR])
            _save(path, held)
            raise ServiceError(f"the agent did not start: {exc}") from exc
        _TURNS[str(path)] = turn
    threading.Thread(target=_run, args=(turn, message), daemon=True,
                     name=f"direction-chat-{aspect}").start()
    return thread(project, aspect)


def _relative(value: str, root: Path) -> str:
    try:
        return Path(value).relative_to(root).as_posix()
    except ValueError:
        return value


def _detail(tool: str, given: dict[str, Any], root: Path) -> str:
    """The one line that says what a tool call is about."""
    if tool == "Read":
        return _relative(str(given.get("file_path") or ""), root)
    if tool in ("Glob", "Grep"):
        return str(given.get("pattern") or "")
    for key in ("name", "influence", "scene", "asset_id", "folder"):
        if given.get(key):
            return str(given[key])
    return ""


def _apply(turn: _Turn, event: dict[str, Any]) -> None:
    """One event of the agent's stream, written into the thread."""
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init" and event.get("session_id"):
        turn.held["session"] = event["session_id"]
    elif kind == "stream_event" and not event.get("parent_tool_use_id"):
        inner = event.get("event") or {}
        if inner.get("type") == "message_start":
            turn.streamed = False
        elif inner.get("type") == "content_block_start" and \
                (inner.get("content_block") or {}).get("type") == "text":
            text = turn.answer["text"]
            if text and not text.endswith("\n\n"):
                turn.answer["text"] = text.rstrip("\n") + "\n\n"
        elif inner.get("type") == "content_block_delta" and \
                (inner.get("delta") or {}).get("type") == "text_delta":
            turn.streamed = True
            turn.answer["text"] += inner["delta"].get("text", "")
    elif kind == "assistant" and not event.get("parent_tool_use_id"):
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use" and len(turn.answer["steps"]) < MAX_STEPS:
                # What the agent said before acting is a step of its work, not
                # its answer: the bubble keeps what it says once it is done.
                said = turn.answer["text"].strip()
                if said:
                    turn.answer["steps"].append({"note": said, "at": _now()})
                    turn.answer["text"] = ""
                name = str(block.get("name") or "")
                tool = name.removeprefix(STUDIO_PREFIX)
                turn.answer["steps"].append(
                    {"tool": tool, "detail": _detail(tool, block.get("input") or {}, turn.root),
                     "at": _now()})
            elif block.get("type") == "text" and not turn.streamed:
                text = turn.answer["text"]
                turn.answer["text"] = (text.rstrip("\n") + "\n\n" if text else "") + \
                    block.get("text", "")
    elif kind == "result":
        turn.finished = True
        if event.get("is_error") or event.get("subtype") not in (None, "success"):
            turn.error = str(event.get("result") or event.get("subtype") or "error")[:MAX_ERROR]


def _drain(turn: _Turn) -> None:
    process = turn.process
    if process is None or process.stderr is None:
        return
    for line in process.stderr:
        if line.strip():
            turn.stderr.append(line.rstrip())


def _run(turn: _Turn, message: str) -> None:
    process = turn.process
    assert process is not None and process.stdin is not None and process.stdout is not None
    threading.Thread(target=_drain, args=(turn,), daemon=True).start()
    try:
        process.stdin.write(message)
        process.stdin.close()
    except OSError:
        pass
    for line in process.stdout:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        with turn.lock:
            _apply(turn, event)
            if time.monotonic() - turn.flushed >= FLUSH_SECONDS:
                _save(turn.path, turn.held)
                turn.flushed = time.monotonic()
    code = process.wait()
    with turn.lock:
        answer = turn.answer
        if turn.stopped:
            answer["state"] = "stopped"
        elif turn.error or (code != 0 and not turn.finished):
            answer["state"] = "failed"
            answer["error"] = turn.error or "\n".join(turn.stderr)[-MAX_ERROR:] or \
                f"the agent stopped (code {code})"
        else:
            answer["state"] = "done"
        answer["text"] = answer["text"].strip()
        _save(turn.path, turn.held)
    with _TURNS_LOCK:
        _TURNS.pop(str(turn.path), None)


def stop(project: str, aspect: str) -> dict[str, Any]:
    """Stop the agent's answer where it is."""
    path = _file(project, aspect)
    with _TURNS_LOCK:
        turn = _TURNS.get(str(path))
    if turn is not None and turn.process is not None:
        turn.stopped = True
        turn.process.terminate()
        try:
            turn.process.wait(timeout=STOP_SECONDS)
        except subprocess.TimeoutExpired:
            turn.process.kill()
    return thread(project, aspect)


def reset(project: str, aspect: str) -> dict[str, Any]:
    """Start a new conversation. The previous thread is kept beside it, dated."""
    path = _file(project, aspect)
    with _TURNS_LOCK:
        if str(path) in _TURNS:
            raise ServiceError("the agent is still answering: wait for it, or stop it")
        if path.is_file():
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            path.rename(path.with_name(f"{aspect}.chat.{stamp}.json"))
    return thread(project, aspect)


def stop_all() -> None:
    """Server shutdown: no agent outlives the studio."""
    with _TURNS_LOCK:
        running = list(_TURNS.values())
    for turn in running:
        if turn.process is not None and turn.process.poll() is None:
            turn.stopped = True
            turn.process.terminate()


# ------------------------------------------------- a prompt drafted from images


def _draft_message(spec: dict[str, Any], chosen: list[dict[str, Any]], request: str,
                   style: str, language: str) -> str:
    shown = []
    for entry in chosen:
        shown.append(f"- {entry['name']} ({entry['kind']})"
                     + (f" -- keep: {entry['keep']}" if entry.get("keep") else "")
                     + (f" -- leave: {entry['avoid']}" if entry.get("avoid") else ""))
        shown += [f"  - `{path}`" for path in entry["images"]]
    return f"""You write one prompt for an image generator (FLUX), for {spec['subject']} of a \
game. Look at each image below with Read before writing anything: the prompt comes from what \
you see in them, not from their names.

The influences and their images:
{chr(10).join(shown)}

What the user wants to see: {request or "an image that sums these influences up for the game"}

{style}

Write the prompt in English, at most 120 words, concrete: subject, composition, shapes, \
matter, light, palette, rendering technique -- what you saw, with what each influence keeps and \
leaves. Never name a work, a brand or a character. Answer with this JSON object only:
{{"seen": "<two sentences in {language}: what you saw in the images that the prompt keeps>", \
"prompt": "<the prompt>"}}"""


def _parse_draft(output: str) -> dict[str, str]:
    """The prompt and what was seen, out of Claude Code's JSON result."""
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ServiceError(f"unreadable answer from the model ({exc})") from exc
    if result.get("is_error"):
        raise ServiceError(f"the model failed: {str(result.get('result') or '')[:MAX_ERROR]}")
    text = str(result.get("result") or "")
    start, end = text.find("{"), text.rfind("}")
    try:
        drafted = json.loads(text[start:end + 1]) if start >= 0 else {}
    except json.JSONDecodeError:
        drafted = {}
    prompt = " ".join(str(drafted.get("prompt") or "").split())
    if not prompt:
        raise ServiceError(f"the model wrote no prompt: {text[:MAX_ERROR]}")
    return {"prompt": prompt[:influences.MAX_PROMPT],
            "seen": " ".join(str(drafted.get("seen") or "").split())[:influences.MAX_TEXT]}


def draft(project: str, aspect: str, *, request: str = "", sources: list[str] | None = None,
          count: int = 4, model: str = influences.DEFAULT_MODEL,
          lang: str = "") -> dict[str, Any]:
    """Write an image prompt from what the influences' images show, and propose it.

    One short turn of Claude Code (the user's subscription), which may only read
    the images it is handed; nothing is generated -- the user pays the
    proposal, or not (`influences.pay`). `sources`: the influences to draw on,
    by default every one with images.
    """
    spec = influences._aspect(aspect)
    wanted = " ".join(request.split())
    if len(wanted) > MAX_DRAFT_REQUEST:
        raise ServiceError(f"request too long: {len(wanted)} characters "
                           f"(at most {MAX_DRAFT_REQUEST})")
    held = influences.context(project)[aspect]["influences"]
    known = {entry["id"] for entry in held}
    missing = [item for item in sources or [] if item not in known]
    if missing:
        raise NotFound(f"influence not found: {', '.join(missing)}")
    chosen = [entry for entry in held
              if entry["images"] and (not sources or entry["id"] in sources)]
    if not chosen:
        raise ServiceError("no influence with images to look at: drop images on an influence "
                           "first")
    budget = MAX_DRAFT_IMAGES
    for entry in chosen:
        entry["images"] = entry["images"][:max(1, budget // len(chosen))]
    look = influences.context(project)["style"]["parts"]["look"]
    style = (f"The game's graphic style as written:\n{look['text'].strip()[:MAX_STYLE_CONTEXT]}"
             if look["written"] else "")
    message = _draft_message(spec, chosen, wanted, style, LANGUAGES.get(lang, "English"))

    command, unset = harnesses.command_for(HARNESS)
    folders = sorted({str(Path(path).parent) for entry in chosen for path in entry["images"]})
    command += ["--print", "--model", DRAFT_MODEL, "--output-format", "json",
                "--setting-sources", "", "--no-session-persistence", "--strict-mcp-config",
                "--tools", "Read", "--permission-mode", "dontAsk",
                "--add-dir", *folders, "--allowedTools", "Read"]
    env = {key: value for key, value in os.environ.items() if key not in unset}
    env["PATH"] = harnesses.search_path()
    root = project_paths(studio().settings, project).root
    try:
        done = subprocess.run(command, input=message, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=root, env=env,
                              timeout=DRAFT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise ServiceError("the model took too long to look at the images") from exc
    except OSError as exc:
        raise ServiceError(f"the agent did not start: {exc}") from exc
    if done.returncode != 0 and not done.stdout.strip():
        raise ServiceError(f"the model failed: {done.stderr.strip()[-MAX_ERROR:]}")
    drafted = _parse_draft(done.stdout)
    return influences.propose(project, aspect, influence=chosen[0]["id"] if len(chosen) == 1
                              else "", prompt=drafted["prompt"], count=count, model=model,
                              why=drafted["seen"])

