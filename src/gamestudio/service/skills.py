"""The studio's skills: what they are, whether they hold up, and their index.

A skill is a procedure, not a tool: it says in what order, under what
conditions, and how to tell it is done. Skills live in
`.claude/skills/<name>/SKILL.md`, with their resources next to it.

Two problems are solved here, neither by copying files:

1. **The index.** A hand-written index drifts at the first addition, so it is
   *regenerated* from the files (`index`), and `check` refuses a stale one --
   the studio's rule everywhere: what can be computed is not written by hand.
2. **Agents that do not read `.claude/`.** Claude Code reads `.claude/skills`,
   the other agents the studio's tabs launch do not. A **mirror**
   `.agents/skills/<name>` makes the skills findable at a conventional path,
   and symlinks guarantee it cannot drift -- a full copy would double the
   repository's weight (vendored material included) and end up lying.

Nothing here calls the network: only file reads and names.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .context import studio
from .errors import NotFound, ServiceError

# The procedure file's name, the index's, and the mirror other agents read.
SKILL_FILE = "SKILL.md"
INDEX_FILE = "README.md"
MIRROR_DIR = ".agents/skills"
SOURCE_DIR = ".claude/skills"

# Marker of a third-party skill: it states its provenance.
VENDOR_MARKER = "VENDOR.md"

# Below this length, a description cannot say when to use the skill.
MIN_DESCRIPTION = 20

# A skill's name is its folder's name: lowercase letters, digits and hyphens.
# The folder is derived from the title, and the index points to a path that
# exists.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")

# The text of a new procedure: its shape, not its content. A skill says in what
# order, under what conditions, and how to tell it is done -- the skeleton asks
# these three questions and leaves the answers to the author.
SKILL_TEMPLATE = """\
## When to follow it

## In what order

1. 

## How to tell it is done

- 
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def root() -> Path:
    """The studio root: where `CLAUDE.md` and `.claude/` live."""
    settings = studio().settings
    return settings.project_root or Path.cwd()


def source_dir() -> Path:
    return root() / SOURCE_DIR


def mirror_dir() -> Path:
    return root() / MIRROR_DIR


def index_path() -> Path:
    return source_dir() / INDEX_FILE


def _frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """The `---` block of a SKILL.md, and the rest.

    A file without frontmatter is not a read error: it is a malformed skill,
    and `check` reports it. An empty block is returned.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}, text
    _, _, rest = text.partition("---")
    block, _, body = rest.partition("\n---")
    try:
        parsed = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return {}, body
    return (parsed if isinstance(parsed, dict) else {}), body


def skills() -> list[dict[str, Any]]:
    """The installed skills, with what it takes to judge them.

    `vendored` is read from disk: a skill carrying a `VENDOR.md` comes from
    elsewhere and says so. That separates the studio's procedures from
    third-party material without a hand-kept list.
    """
    directory = source_dir()
    if not directory.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        skill_file = entry / SKILL_FILE
        meta, _body = _frontmatter(skill_file) if skill_file.is_file() else ({}, "")
        files = [path for path in entry.rglob("*") if path.is_file()]
        found.append({
            "name": entry.name,
            "declared_name": str(meta.get("name", "") or ""),
            "description": str(meta.get("description", "") or "").strip(),
            "path": str(entry),
            "skill_file": str(skill_file),
            "files": len(files),
            "bytes": sum(path.stat().st_size for path in files),
            "vendored": (entry / VENDOR_MARKER).is_file(),
            "problems": _problems(entry, meta, skill_file),
        })
    return found


def _problems(entry: Path, meta: dict[str, Any], skill_file: Path) -> list[str]:
    """What prevents a skill from being usable as is."""
    problems: list[str] = []
    if not skill_file.is_file():
        problems.append(f"{SKILL_FILE} missing")
        return problems
    if not meta:
        problems.append("frontmatter missing or unreadable (--- name / description ---)")
        return problems
    declared = str(meta.get("name", "") or "")
    if not declared:
        problems.append("`name` field missing")
    elif declared != entry.name:
        problems.append(f"`name: {declared}` does not match the folder “{entry.name}”")
    description = str(meta.get("description", "") or "").strip()
    if not description:
        problems.append("`description` field missing")
    elif len(description) < MIN_DESCRIPTION:
        problems.append("description too short to tell when to use it")
    return problems


def _index_text(entries: list[dict[str, Any]]) -> str:
    """The skills index, in Markdown, regenerated from the files."""
    ours = [entry for entry in entries if not entry["vendored"]]
    theirs = [entry for entry in entries if entry["vendored"]]
    lines = [
        "# Studio skills",
        "",
        "<!-- Regenerated by `gamestudio skills index`: do not edit by hand. -->",
        "",
        "One procedure per deliverable. A skill does not say what a tool does -- the",
        "MCP tool descriptions do that -- it says **in what order, under what",
        "conditions, and how to tell it is done**. Start with `production-routing`,",
        "the index of the procedures.",
        "",
        "Agents launched in the studio read `.claude/skills/`. A symlink mirror",
        f"`{MIRROR_DIR}/` makes them findable at a conventional path for agents that",
        "do not read `.claude/`: `gamestudio skills sync` repairs it, `gamestudio",
        "skills check` verifies it has not drifted.",
        "",
    ]
    for title, group in (("Studio procedures", ours),
                         ("Vendored third-party material", theirs)):
        if not group:
            continue
        lines += [f"## {title}", "", "| Skill | What it is for |", "| --- | --- |"]
        for entry in group:
            name = entry["name"]
            link = f"[`{name}`]({name}/{SKILL_FILE})"
            note = entry["description"].replace("|", "\\|")
            if entry["vendored"]:
                note += " *(third party — see `THIRD_PARTY_NOTICES.md`)*"
            lines.append(f"| {link} | {note} |")
        lines.append("")
    return "\n".join(lines)


def index() -> dict[str, Any]:
    """The skills index: the text it should have, and its path."""
    entries = skills()
    return {
        "generated_at": _now_iso(),
        "path": str(index_path()),
        "count": len(entries),
        "vendored": sum(1 for entry in entries if entry["vendored"]),
        "markdown": _index_text(entries),
    }


def write_index() -> dict[str, Any]:
    """Rewrite `.claude/skills/README.md` from the files."""
    data = index()
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data["markdown"], encoding="utf-8")
    return {**data, "written": True}


def mirror() -> dict[str, Any]:
    """The state of the `.agents/skills` mirror: what is missing, what points elsewhere."""
    source, target = source_dir(), mirror_dir()
    wanted = {entry["name"] for entry in skills()}
    links: list[dict[str, Any]] = []
    for name in sorted(wanted):
        link = target / name
        resolved = link.resolve() if link.is_symlink() else None
        links.append({
            "name": name,
            "path": str(link),
            "linked": link.is_symlink(),
            "ok": bool(resolved and resolved == (source / name).resolve()),
        })
    return {"dir": str(target), "links": links,
            "ok": all(entry["ok"] for entry in links) and bool(links)}


def sync(*, prune: bool = True) -> dict[str, Any]:
    """Create or repair the mirror, and remove dead links.

    Symlinks, never copies: the mirror cannot drift, and vendored material is
    not duplicated. `prune` only removes links that pointed **into**
    `.claude/skills` -- a skill added by hand to `.agents/` is left alone.
    """
    source, target = source_dir(), mirror_dir()
    target.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    removed: list[str] = []

    for entry in skills():
        link = target / entry["name"]
        if link.is_symlink() and link.resolve() == (source / entry["name"]).resolve():
            continue
        if link.is_symlink() or link.exists():
            link.unlink()
        # Relative path: the repository stays movable from one folder to another.
        link.symlink_to(Path("..") / ".." / SOURCE_DIR / entry["name"])
        created.append(entry["name"])

    if prune:
        for link in sorted(target.iterdir()) if target.is_dir() else []:
            if not link.is_symlink():
                continue
            resolved = link.resolve()
            if resolved.exists():
                continue
            if SOURCE_DIR in str(link.readlink()):
                link.unlink()
                removed.append(link.name)
    return {"created": created, "removed": removed, **mirror()}


def check() -> dict[str, Any]:
    """The verdict: are the skills usable, and is the index up to date?

    This is what `make check` runs. A stale index fails the check, exactly like
    a lint: otherwise it drifts at the first added skill, and the agent reading
    it starts from a wrong list.
    """
    entries = skills()
    problems: list[dict[str, str]] = []
    for entry in entries:
        for problem in entry["problems"]:
            problems.append({"skill": entry["name"], "problem": problem})

    expected = _index_text(entries)
    path = index_path()
    if not path.is_file():
        problems.append({"skill": INDEX_FILE, "problem": "index missing (`gamestudio skills "
                                                         "index`)"})
    elif path.read_text(encoding="utf-8") != expected:
        problems.append({"skill": INDEX_FILE, "problem": "index out of date (`gamestudio skills "
                                                         "index`)"})

    state = mirror()
    for entry in state["links"]:
        if not entry["ok"]:
            problems.append({"skill": entry["name"],
                             "problem": "mirror missing or broken (`gamestudio skills sync`) — "
                                        f"{entry['path']}"})

    return {
        "generated_at": _now_iso(),
        "ok": not problems and bool(entries),
        "skills": len(entries),
        "vendored": sum(1 for entry in entries if entry["vendored"]),
        "files": sum(entry["files"] for entry in entries),
        "bytes": sum(entry["bytes"] for entry in entries),
        "mirror": state["dir"],
        "index": str(path),
        "problems": problems,
        "missing": [] if entries else ["no skill in .claude/skills/"],
    }


def read(name: str) -> dict[str, Any]:
    """A skill's text: its whole procedure."""
    entry = next((item for item in skills() if item["name"] == name), None)
    if entry is None:
        raise NotFound(f"unknown skill: {name} (known: "
                       f"{', '.join(item['name'] for item in skills())})")
    path = Path(entry["skill_file"])
    if not path.is_file():
        raise ServiceError(f"{name} has no {SKILL_FILE}")
    return {**entry, "text": path.read_text(encoding="utf-8")}


def template() -> str:
    """The skeleton written under a new procedure when no body is given."""
    return SKILL_TEMPLATE


def slug(value: str) -> str:
    """Turn a title into a stable folder name.

    Accents are folded -- an accented folder name travels badly -- and anything
    that is not a letter, a digit or a hyphen becomes a hyphen. A title that
    yields nothing (`!!!`) yields no name at all: creation refuses it rather
    than invent `skill-1`.
    """
    folded = (value.strip().lower()
              .replace("é", "e").replace("è", "e").replace("ê", "e")
              .replace("à", "a").replace("â", "a").replace("î", "i")
              .replace("ô", "o").replace("û", "u").replace("ù", "u")
              .replace("ç", "c"))
    cleaned = "".join(c if c.isascii() and c.isalnum() else "-" for c in folded)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:48].strip("-")


def create(name: str, description: str, body: str = "") -> dict[str, Any]:
    """Add a procedure, then bring the index and the mirror back in line.

    The three steps are one: a skill written without its index makes
    `make check` fail, and a skill without its link stays invisible to agents
    that do not read `.claude/`. The file's title comes from the name -- never
    from the body, which is only the procedure.
    """
    label = name.strip()
    slug_name = slug(label)
    if not NAME_RE.match(slug_name):
        raise ServiceError(f"invalid name: “{name}” (letters, digits and hyphens, for example "
                           "“ui-overhaul”)")
    note = description.strip()
    if len(note) < MIN_DESCRIPTION:
        raise ServiceError(
            f"description too short ({len(note)} character(s), at least {MIN_DESCRIPTION}): it is "
            "what says when to follow the procedure")

    folder = source_dir() / slug_name
    if folder.exists():
        raise ServiceError(f"“{slug_name}” already exists: open the procedure, or choose another "
                           "name")

    text = (body.strip("\n") or SKILL_TEMPLATE).rstrip("\n")
    front = yaml.safe_dump({"name": slug_name, "description": note},
                           allow_unicode=True, sort_keys=False, width=1000)
    folder.mkdir(parents=True)
    skill_file = folder / SKILL_FILE
    skill_file.write_text(f"---\n{front}---\n\n# {label}\n\n{text}\n", encoding="utf-8")

    written = write_index()
    linked = sync()
    return {
        **read(slug_name),
        "index": written["path"],
        "mirror": linked["dir"],
        "linked": bool(linked["ok"]),
    }
