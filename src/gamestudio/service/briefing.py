"""The briefing: what an agent must know about the studio before working in it.

A discussion opened in the Chats window starts an agent at the studio root.
With nothing more, that agent knows neither what was produced, nor what awaits
review, nor which project is open: it asks again, or guesses. The briefing
fills that gap.

Two kinds of text, one rule:

- the **notes** (`context/identity.md`, `goals.md`, `preferences.md`) are
  written by hand: what the studio produces, where it is going, and the
  conventions that are not renegotiated each time;
- the **briefing** (`context/briefing.md`) is *regenerated* from the database
  and the library. It is never committed: a file describing today's state has
  no place in a history.

Nothing here calls the network, and the computation takes a few milliseconds on
a local database: that is what allows redoing it instead of letting it age. It
is rewritten after each finished production and each opened chat tab.

A discussion opened **outside the root** -- in a project's folder -- reads
neither `AGENTS.md`, nor `CLAUDE.md`, nor this briefing: they are elsewhere.
The studio then gives it the same knowledge on the command line
(`session_context`): where the studio is, which project is open, the notes,
that project's briefing, and where to read the rest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..store.folders import project_paths
from .context import space, studio
from .errors import NotFound, ServiceError

# The hand-kept notes. This list is the only way in: a name not on it is
# refused, which makes path traversal impossible by construction rather than by
# filtering.
NOTES = ("identity.md", "goals.md", "preferences.md")
BRIEFING = "briefing.md"
# The briefing reads like the notes, but is never written through the API.
READABLE = (*NOTES, BRIEFING)

# Reading limits: the briefing must stay short to be read in full.
MAX_PROJECTS = 12
MAX_CHARACTERS = 8
MAX_JOBS = 10

# Starting content, written only when the file is missing: a cloned repository
# has a usable `context/` right away, and a note already written is never
# overwritten.
SEEDS: dict[str, str] = {
    "identity.md": """\
# Studio identity

Fill in once, then keep up to date: agents read this file before proposing
anything.

## The game

<!-- Name, genre, platform, engine. One or two sentences are enough. -->

## What we produce

<!-- Animated 3D entities, sprites rendered from 3D, icons?
     For what use in the game: playable characters, enemies, scenery, interface? -->

## What we never do

<!-- The constraints that are not renegotiated in each discussion. -->
""",
    "goals.md": """\
# Goals

What matters now. A reached goal moves to "Done", it does not disappear: that
is how an agent knows what was delivered.

## In progress

<!-- One line per goal, with the expected deliverable. -->

## Next

<!-- What is decided but not started. -->

## Done

<!-- What is delivered, with the date. -->
""",
    "preferences.md": """\
# Working preferences

The studio conventions nobody should have to repeat. The procedure itself lives
in `CLAUDE.md` and in `.claude/skills/`: here, only the choices that change it.

## Language and naming

<!-- Language of comments, project names and assets. -->

## Spending

<!-- What requires explicit consent, and the cap per batch. -->

## Rendering and review

<!-- What is looked at before validating, and what is never validated without
     opening it. -->
""",
}


def _dir() -> Path:
    """The agent notes folder, anchored on the studio root."""
    return studio().settings.context_path


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _stamp(path: Path) -> str | None:
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def ensure() -> list[str]:
    """Create `context/` and the missing notes. Return what was created.

    Idempotent and never destructive: an existing note is left as is, even
    empty. Writing a template over a file someone just emptied would be the
    worst of surprises.
    """
    directory = _dir()
    directory.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for name, text in SEEDS.items():
        path = directory / name
        if not path.exists():
            path.write_text(text, encoding="utf-8")
            created.append(name)
    return created


def files() -> list[dict[str, Any]]:
    """The context files, present or not, with their state.

    A missing note is returned anyway: the interface creates it on first save,
    and the agent must know it is missing.
    """
    directory = _dir()
    entries: list[dict[str, Any]] = []
    for name in READABLE:
        path = directory / name
        entries.append({
            "name": name,
            "path": str(path),
            "generated": name == BRIEFING,
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else None,
            "modified_at": _stamp(path),
        })
    return entries


def _path(name: str) -> Path:
    if name not in READABLE:
        raise NotFound(f"unknown context file: {name} (known: {', '.join(READABLE)})")
    return _dir() / name


def read(name: str) -> dict[str, Any]:
    """A context file's text. The briefing can be read, never written."""
    path = _path(name)
    if not path.is_file():
        raise NotFound(f"{name} does not exist yet ({path})")
    return {
        "name": name, "path": str(path), "generated": name == BRIEFING,
        "text": path.read_text(encoding="utf-8"), "modified_at": _stamp(path),
    }


def write(name: str, text: str) -> dict[str, Any]:
    """Save a note. The briefing is refused: it is regenerated."""
    if name == BRIEFING:
        raise ServiceError(
            f"{BRIEFING} is regenerated from the database: editing it would have no effect. Edit "
            "identity.md, goals.md or preferences.md.")
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"name": name, "path": str(path), "size_bytes": path.stat().st_size,
            "modified_at": _stamp(path)}


# --------------------------------------------------------------------- digest


def _skills() -> list[dict[str, str]]:
    """The studio's skills: their name and what they are for.

    An agent starting in the studio has them at hand but does not know which
    exist: naming them in the briefing makes them discoverable. They are read
    through `skills.skills()` -- the studio root, the YAML frontmatter -- so the
    briefing and `list_skills` say the same thing.
    """
    from . import skills

    return [{"name": entry["name"], "description": entry["description"]}
            for entry in skills.skills()]


def _project_entry(project: str) -> dict[str, Any]:
    """What is live in a project: its characters, its jobs, its cost."""
    from . import documents, jobs

    st = space(project)
    characters = st.db.list_characters(project)
    index = st.librarian.index_project(project)
    return {
        "project": project,
        "root": str(st.paths.root),
        "data": str(st.paths.data),
        "library": str(st.librarian.project_dir(project)),
        "files": sum(len(folder.files) for folder in index.folders),
        "characters": [
            {"id": c.spec.id, "name": c.spec.name, "state": c.state.value,
             "pipelines": [p.value for p in c.spec.pipelines],
             "errors": list(c.errors)}
            for c in characters[:MAX_CHARACTERS]
        ],
        "characters_total": len(characters),
        "documents": [entry["title"] for entry in documents.documents(project)],
        "jobs_active": jobs.active(st.queue.stats(project)),
        "cost_usd": st.db.total_cost(project),
    }


def _recipe_entry(project: str) -> dict[str, Any] | None:
    """A project's declared recipe, with its characters.

    It is what remains to build when the project is a recipe without
    production, and what to reread to understand an intent.
    """
    from . import catalog

    for entry in catalog.list_recipes():
        if entry["project"] == project:
            return entry
    return None


def _job_entry(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "project": job.project,
        "step": job.step,
        "state": job.state.value,
        "cost_usd": job.cost_usd,
        "error": job.error or None,
        "created_at": job.created_at.isoformat(),
    }


def digest(project: str | None = None, *, isolated: bool = False) -> dict[str, Any]:
    """The studio's state, as plain bounded data.

    Everything is read from the databases and the library indexes: nothing is
    written, nothing recomputed, and no call goes to the network.

    `isolated` sticks to the project: the context of a discussion opened in its
    folder, which has no business knowing about the other games.
    """
    from . import catalog, jobs, workspace

    st = studio()
    known = workspace.known_projects()
    # The requested project, else the first known one.
    focus = project or (known[0] if known else None)
    if isolated and focus:
        known = [focus] if focus in known else []
    projects = [_project_entry(name) for name in known[:MAX_PROJECTS]]
    focus_entry = next((e for e in projects if e["project"] == focus), None)
    focus_recipe = _recipe_entry(focus) if focus else None
    # The jobs: those of the current project, else the most recent across all
    # projects -- what one wants to see first.
    focus_jobs = jobs._jobs(focus if focus in known else None, None, MAX_JOBS)
    all_jobs = (focus_jobs if isolated
                else jobs._jobs(None, None, MAX_JOBS))
    paths = project_paths(st.settings, focus) if focus else None
    return {
        "generated_at": _now_iso(),
        "root": str(st.settings.project_root) if st.settings.project_root else None,
        "data_dir": str(paths.data) if isolated and paths else str(st.settings.data_dir),
        "library_dir": str(paths.library) if paths else None,
        "isolated": isolated,
        "focus": focus,
        "focus_entry": focus_entry,
        "focus_recipe": focus_recipe,
        "focus_jobs": [_job_entry(job) for job in focus_jobs],
        "projects": projects,
        "projects_total": len(known),
        "jobs": [_job_entry(job) for job in all_jobs],
        "recipes": [entry for entry in catalog.list_recipes()
                    if not isolated or entry["project"] == focus],
        "skills": _skills(),
        "notes": files(),
        "counters": {
            "projects": len(known),
            "characters": sum(len(space(name).db.list_characters(name)) for name in known),
            "jobs_active": sum(jobs.active(space(name).queue.stats(name))
                               for name in known),
            "cost_usd": round(sum(space(name).db.total_cost(name) for name in known), 4),
        },
    }


def _when(iso: str) -> str:
    return iso[:19].replace("T", " ")


def _cost(value: float) -> str:
    return f"{value:.2f} $" if value else "0 $"


def _state_label(state: str) -> str:
    return {"needs_review": "to review"}.get(state, state)


def render(data: dict[str, Any]) -> str:
    """The briefing in Markdown: what an agent reads, in the order it needs it.

    First where the current project stands, then what to know of the other
    projects, then the jobs, then the pointers.
    """
    counters = data["counters"]
    focus = data.get("focus")
    focus_entry = data.get("focus_entry")
    focus_recipe = data.get("focus_recipe")
    focus_jobs = data.get("focus_jobs") or []
    lines = [
        "# gamestudio briefing",
        "",
        f"State at {_when(data['generated_at'])} — "
        f"{counters['projects']} project(s), {counters['characters']} character(s), "
        f"{counters['jobs_active']} job(s) running, "
        f"{counters['cost_usd']:.2f} $ spent.",
        "",
    ]

    # ------------------------------------------------------------------ focus
    if focus:
        lines += [f"## Current project: `{focus}`", ""]
    else:
        lines += ["## At a glance", ""]
        lines.append("No project is open: open a folder or pick a project in the "
                     "selector to see its detailed state.")
        lines.append("")

    if focus_entry:
        lines += _render_project_focus(focus_entry, focus_recipe)
    elif focus and focus_recipe:
        lines += _render_recipe_focus(focus_recipe)

    # --------------------------------------------------------- other projects
    others = [e for e in data["projects"] if e["project"] != focus]
    if others or data["projects_total"] > len(data["projects"]):
        lines += ["## Other projects", ""]
        if not others:
            lines.append("No other project.")
        for entry in others:
            lines += _render_project_summary(entry)
        if data["projects_total"] > len(data["projects"]):
            lines.append(f"({data['projects_total'] - len(data['projects'])} more "
                         "project(s), not detailed here.)")
        lines.append("")

    # ------------------------------------------------------------------- jobs
    if focus_jobs:
        lines += [f"## Recent jobs — `{focus or 'all projects'}`", ""]
        for job in focus_jobs:
            cost = f" — {_cost(job['cost_usd'])}" if job["cost_usd"] else ""
            error = f" — **{job['error']}**" if job.get("error") else ""
            lines.append(
                f"- `{job['kind']}` **{_state_label(job['state'])}**"
                f" ({job['project'] or 'no project'}, {job['step']})"
                f"{cost}{error} — {_when(job['created_at'])}")
        lines.append("")

    # ------------------------------------------------------------------ notes
    lines += ["## Read before working", ""]
    for name in NOTES:
        state = "present" if any(n["name"] == name and n["exists"]
                                 for n in data["notes"]) else "to create"
        # The notes are the studio's: a project briefing, read from its folder,
        # names them by their full path.
        where = str(_dir() / name) if data.get("isolated") else f"context/{name}"
        lines.append(f"- `{where}` ({state})")
    lines += [
        "",
        "The studio's procedure (skills, interfaces, verification) is in "
        "`CLAUDE.md`.",
        "",
    ]

    # --------------------------------------------------------------- pointers
    lines += [
        "## Pointers",
        "",
        f"- studio root: `{data['root'] or 'unknown'}`",
        f"- data: `{data['data_dir']}`",
        f"- readable library: `{data['library_dir'] or 'no project'}`",
        "- state of the environment: tool `studio_health`",
        "- browse the library: tool `library_tree`, or "
        "`gamestudio library ls <project> --paths`",
        "- review an output: tool `view_asset` (an image is looked at, not "
        "assumed)",
        "- regenerate this briefing: `gamestudio context show --write`",
        "",
    ]
    return "\n".join(lines)


def _render_project_focus(entry: dict[str, Any],
                          recipe: dict[str, Any] | None) -> list[str]:
    """The current project in detail: what is done, what remains, where to look."""
    lines: list[str] = []
    if entry.get("root"):
        lines.append(f"- folder: `{entry['root']}` (everything the studio keeps for "
                     "this project under `.gamestudio/`, Godot exports at the root)")
    lines.append(f"- library: `{entry['library']}` ({entry['files']} file(s))")
    lines.append(f"- total cost: {_cost(entry['cost_usd'])}")
    if entry["jobs_active"]:
        lines.append(f"- **{entry['jobs_active']} job(s) running**")

    if recipe and "error" not in recipe:
        lines.append(f"- recipe: style {recipe['style']}, "
                     f"{len(recipe['characters'])} character(s) planned — "
                     f"`{recipe['path']}`")
    elif recipe and "error" in recipe:
        lines.append(f"- recipe **unreadable**: {recipe['error']}")

    if entry["characters"]:
        lines.append(f"- characters ({entry['characters_total']}):")
        for c in entry["characters"]:
            marker = " **[to review]**" if c["errors"] else ""
            lines.append(
                f"  - `{c['id']}` — {c['name'] or c['id']} — "
                f"**{_state_label(c['state'])}** "
                f"({', '.join(c['pipelines']) or 'no pipeline'}){marker}")
    else:
        lines.append("- characters: none")

    if entry["documents"]:
        lines.append("- documents: " + ", ".join(
            f"`{title}`" for title in entry["documents"]))
    lines.append("")
    return lines


def _render_recipe_focus(recipe: dict[str, Any]) -> list[str]:
    """A project that exists through its recipe alone: the roster is still to build."""
    if "error" in recipe:
        return [f"- recipe **unreadable**: {recipe['error']}", ""]
    lines = [
        f"- recipe: style {recipe['style']}, "
        f"{len(recipe['characters'])} character(s) planned — `{recipe['path']}`",
        "- library: nothing produced yet",
    ]
    if recipe["characters"]:
        lines.append("- roster to build: "
                     + ", ".join(f"`{c}`" for c in recipe["characters"]))
    lines.append("")
    return lines


def _render_project_summary(entry: dict[str, Any]) -> list[str]:
    """The short line of a secondary project: name, state, cost."""
    review = [c["id"] for c in entry["characters"] if c["errors"]]
    parts = [
        f"**`{entry['project']}`**",
        f"{len(entry['characters'])}/{entry['characters_total']} character(s)",
        _cost(entry["cost_usd"]),
    ]
    if entry["jobs_active"]:
        parts.append(f"{entry['jobs_active']} job(s) running")
    if review:
        parts.append(f"**to review: {', '.join(review)}**")
    line = " — ".join(parts)
    if entry["documents"]:
        line += f" — documents: {', '.join(f'`{t}`' for t in entry['documents'])}"
    return [f"- {line}"]


def briefing(project: str | None = None) -> dict[str, Any]:
    """The full briefing: the data, the Markdown, and where it is written.

    Nothing is written here: a read must change nothing. `write_briefing`
    writes, and the caller decides when.
    """
    data = digest(project)
    return {
        "generated_at": _now_iso(),
        "path": str(_dir() / BRIEFING),
        "digest": data,
        "markdown": render(data),
    }


def write_briefing(project: str | None = None) -> str:
    """Regenerate `context/briefing.md` and return its path.

    Called after each production and each opened chat tab, so a discussion
    starts on the real state, not yesterday's. The studio briefing sees every
    project -- that is where the engine is worked on; a project's briefing
    (`.gamestudio/context/briefing.md`) sees only that project, and travels with
    its folder.
    """
    ensure()
    path = _dir() / BRIEFING
    path.write_text(render(digest(project)), encoding="utf-8")
    if project:
        write_project_briefing(project)
    return str(path)


def write_project_briefing(project: str) -> str:
    """Regenerate a project's briefing, in its folder, and return its path."""
    target = space(project).paths.context / BRIEFING
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(digest(project, isolated=True)), encoding="utf-8")
    return str(target)


# --------------------------------------------------------- outside the root

# What an agent opened outside the root receives on top of its MCP tools.
SESSION_DIR = "sessions"


def _project_for(folder: Path) -> str | None:
    """The project whose folder is `folder` or contains it."""
    target = folder.resolve()
    for name, root in studio().registry.all().items():
        if target == root or root in target.parents:
            return name
    return None


def session_context(cwd: Path) -> str:
    """The context of a discussion opened in `cwd`, outside the studio root.

    What `AGENTS.md`, `CLAUDE.md` and the start hook give a discussion opened at
    the root, gathered into one text: the agent must have nothing to ask again.
    The notes and the briefing are **copied in**, not just cited -- an agent
    that does not follow a link still has the state.
    """
    st = studio()
    home = Path(st.settings.project_root or Path.cwd())
    project = _project_for(cwd)
    skills = home / ".claude" / "skills"
    lines = [
        "# gamestudio context",
        "",
        "Injected by the studio when this discussion opened.",
        "",
    ]
    if project:
        paths = project_paths(st.settings, project)
        lines += [
            f"This discussion opens in the folder of the gamestudio project "
            f"`{project}`: `{cwd}`. The studio itself lives at `{home}`.",
            "",
            "## This project",
            "",
            f"- folder (root of the Godot project, where exports go): `{paths.godot}`",
            f"- `.gamestudio/` (everything the studio keeps for this game, and only it): "
            f"`{paths.data}`",
            f"- project briefing, regenerated after each production: "
            f"`{paths.context / BRIEFING}`",
            f"- recipe: `{paths.recipe}`",
            f"- documents (bible, world, direction, concepts): `{paths.documents}` — "
            "tools `list_documents` / `read_document` / `write_document`",
            f"- library (everything produced): `{paths.library}` — "
            "tool `library_tree`",
            f"- effects: `{paths.effects}`",
            "",
        ]
    else:
        lines += [
            f"This discussion opens in `{cwd}`, which is no studio project's folder. "
            f"The studio lives at `{home}`; `open_project_folder` turns a folder into "
            "a project.",
            "",
        ]
    focus = f'project="{project}"' if project else ""
    lines += [
        "## The studio is connected",
        "",
        "The MCP servers `gamestudio`, `blender` and `godot` are passed to this "
        "discussion. Whatever the studio interface can show, a tool returns:",
        "",
        f"- `studio_briefing({focus})` — the up-to-date state (the copy below dates "
        "from the opening);",
        "- `studio_workspace`, `character_detail`, `job_history`, `library_tree`, "
        "`view_asset` — what is produced, and where;",
        "- `list_skills` / `read_skill` — the procedure of each deliverable;",
        "- `studio_note_write` — a lasting decision is written down, it does not "
        "stay in the conversation.",
        "",
        "Full instructions: "
        f"`{home / 'AGENTS.md'}`, `{home / 'CLAUDE.md'}`. "
        f"Procedures: `{skills}/<name>/SKILL.md` (index: `{skills / 'README.md'}`).",
        "",
        "If a `gamestudio` tool is missing or fails to start, tell the user: do "
        "not work blind on guessed files.",
        "",
    ]
    for name in NOTES:
        path = _dir() / name
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            lines += [f"## Note `context/{name}`", "", _demote(text), ""]
    lines += ["## Briefing at opening", "",
              _demote(render(digest(project, isolated=bool(project))))]
    return "\n".join(lines).rstrip() + "\n"


def _demote(markdown: str) -> str:
    """Lower headings by one level: a copied note stays under its section."""
    return "\n".join(f"#{line}" if line.startswith("#") else line
                     for line in markdown.splitlines())


def write_session_context(cwd: Path) -> Path:
    """Write a discussion's context and return its path.

    A file, because `claude --append-system-prompt-file` reads it and a text of
    several kilobytes has no place in the process list. One file per folder:
    two tabs opened in the same place share the same context, and the second
    refreshes it.
    """
    from ..store.folders import slug

    folder = studio().settings.data_dir / SESSION_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slug(str(cwd.resolve()))}.md"
    path.write_text(session_context(cwd), encoding="utf-8")
    return path
