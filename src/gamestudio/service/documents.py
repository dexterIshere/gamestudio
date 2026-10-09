"""A project's documents: texts that are written, reread and kept.

A project is not only a recipe and assets: there is what no tool can guess --
what the world looks like, what the character must never do, why the palette
changed at batch 3. These texts live in `<folder>/.gamestudio/documents/`, next
to the recipe, and are versioned with the game (the folder's `.gitignore` lets
them through).

The exact opposite of the workspace report (`.gamestudio/workspace/report.html`):
that one is *written* by the studio and disposable, these are *read* by the
studio and precious. The same split as `context/` and `briefing.md`: what is
computed is not committed, what is decided is kept.

The format is Markdown, for two reasons: it reads without a tool, and it
renders in the window (`app/src/components/Markdown.tsx`).

A document can live on a **shelf** -- a named subfolder: `notes/`, `ideas/`,
`devlog/`, or `world/<section>/` for the world sections the user declares
(`service/world.py`). The empty shelf is the root, the Documents page's; a
shelf never shows up inside another.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..store.folders import project_paths
from .context import studio
from .errors import NotFound, ServiceError

SUFFIX = ".md"
# A world card's workbench, stored next to it (see `service/entities.py`): it
# follows the card when renamed, and leaves with it when deleted.
WORKBENCH = ".workbench.json"

# A project document is a text, not an archive: beyond this, it is the wrong
# file or the wrong use.
MAX_BYTES = 512 * 1024

# A document name is an identifier, not a path: letters, digits, hyphens and
# underscores. Anything else is refused rather than silently cleaned -- a name
# that changes by itself would lose track of the file.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.IGNORECASE)

# The templates offered on creation. They are not meant to be filled in
# mechanically: they show what a document of this kind contains, so that the
# first draft is not a blank page.
TEMPLATES: dict[str, dict[str, str]] = {
    "blank": {
        "label": "Blank page",
        "body": "# {title}\n\n<!-- What this document must say, and for whom. -->\n",
    },
    "character": {
        "label": "Character bible",
        "body": """\
# {title}

## Who they are

<!-- One sentence: what the character wants, and what stands in the way. -->

## Appearance

- **Silhouette**:
- **Palette**:
- **Details that matter**:

## In the game

- **Role**:
- **Expected animations**:
- **Technical constraints**:

## Never do

<!-- What would break the character, and an agent cannot guess. -->
""",
    },
    "world": {
        "label": "World notes",
        "body": """\
# {title}

## The place

<!-- Where, when, and what happens there. -->

## What one sees

- **Visual landmarks**:
- **Palette and light**:

## Rules

<!-- What is possible here, and what is not. -->
""",
    },
    # The game design templates -- `direction`, `mechanic`, `interface` --
    # follow the card writing rules (skill `game-survey`, "Writing a card"):
    # one sentence first, one section per question, labelled bullets. The space
    # after a dash is written `\x20`: the renderer only recognizes an empty
    # bullet by it, and an editor strips a trailing space.
    "direction": {
        "label": "Art direction",
        "body": """\
# {title}

<!-- One sentence: the rule for this subject, and what it serves in the game's intent. -->

## Rules

- **…** —\x20

## In the game

- **…** — where to see it: scene, shader, file

## References

- **…** — what to keep from it

## To avoid

- **…** — why
""",
    },
    # The intent: the lead card of the art direction. What the game conveys
    # beyond its genre, and what it makes the player feel at each moment.
    "mood": {
        "label": "Intent and feel",
        "body": """\
# {title}

<!-- One sentence: what the game conveys, in three adjectives, and what they rule out. -->

## Style

- **Beyond the genre** —\x20
- **What sets it apart** —\x20

## Moments

<!-- For each: the intended feeling; what the game shows, what it makes heard. -->

- **Launch** —\x20
- **In play** —\x20
- **Victory** —\x20
- **Defeat** —\x20

## To avoid

- **…** — why
""",
    },
    # The graphic style and the game type lead the art direction: their cards
    # are shown and written in the Universe (`service/influences.py`).
    "style": {
        "label": "Graphic style",
        "body": """\
# {title}

<!-- One sentence: how the game draws, and what it gives off. -->

## Rendering

<!-- Technique: 2D or 3D; painted, cel-shaded, low poly, pixel art, toy-like, realistic… -->

- **Technique** —\x20
- **Shapes and proportions** —\x20
- **Outline** —\x20
- **Light and shadow** —\x20
- **Matter and detail** —\x20
- **Camera** —\x20

## In the game

- **…** — where to see it: scene, shader, file

## To avoid

- **…** — why
""",
    },
    "gameplay": {
        "label": "Gameplay style",
        "body": """\
# {title}

<!-- One sentence: what the player does, and what brings them back. -->

## The game

<!-- Genre: strategy, MMO, roguelike, management, puzzle, action, narrative… -->

- **Genre** —\x20
- **The loop** — what the player does, minute by minute
- **Pace and sessions** — how long a session lasts, how often one comes back
- **Alone or together** — solo, cooperative, competitive, massively multiplayer
- **Platform and controls** —\x20
- **What sets it apart** —\x20

## In the game

- **…** — where to see it: scene, script, doc

## To avoid

- **…** — why
""",
    },
    "setting": {
        "label": "Setting",
        "body": """\
# {title}

<!-- One sentence: the world the game takes place in, and what twists it. -->

## The world

- **Place and time** —\x20
- **Scale** — a room, a city, a planet, a galaxy
- **Tone** — light or dark, humour, stakes
- **Who lives there** —\x20
- **Its laws** — technology, magic: what is possible

## In the game

- **…** — where to see it

## To avoid

- **…** — why
""",
    },
    "lore": {
        "label": "Lore",
        "body": """\
# {title}

<!-- One sentence: the story the world carries before the player arrives. -->

## Before the player

- **Origins** —\x20
- **What happened** — the events that shaped the world

## Who is who

- **…** — a people, a faction, a figure: what they want

## Mysteries

- **…** — what the world does not say yet

## In the game

- **…** — where the player meets it: a text, a place, a name
""",
    },
    "influences": {
        "label": "Influences",
        "body": """\
# {title}

<!-- One sentence: what the game draws on, and what it makes its own. -->

## Influences

- **…** — what to keep from it, what to leave
""",
    },
    "note": {
        "label": "Note",
        "body": "# {title}\n\n",
    },
    "idea": {
        "label": "Idea",
        "body": """\
# {title}

## The idea

<!-- In one sentence: what the player would experience. -->

## Why it is worth it

## What it costs
""",
    },
    "devlog": {
        "label": "Devlog entry",
        "body": """\
# {title}

*{date}*

## Done

-

## Learned

-

## Next

-
""",
    },
    "mechanic": {
        "label": "Mechanic",
        "body": """\
# {title}

<!-- One sentence: what the player does, and why they do it again. -->

## Loop

- **The player** —\x20
- **The game answers** —\x20

## Rules

- **…** —\x20

## Parameters

- **…** — value, unit

## Requirements

- **Animations** —\x20
- **Effects** —\x20
- **Interface** —\x20
- **Icons** —\x20
- **Props** —\x20
""",
    },
    "interface": {
        "label": "Interface screen",
        "body": """\
# {title}

<!-- One sentence: what this screen is, and what it does for the player. -->

## Access

-\x20

## Content

- **…** —\x20

## Actions

- **…** →\x20

## States

- **…** —\x20
""",
    },
    "icon": {
        "label": "Icon set",
        "body": """\
# {title}

<!-- One sentence: what these icons stand for, and where the player sees them. -->

## Usage

- **Where** —\x20
- **What they say** —\x20

## Shape

- **Size** — px on screen, source px
- **Format** — SVG, PNG, atlas
- **Stroke** —\x20
- **Palette** —\x20
- **States** — normal, hover, disabled

## Set

- **…** —\x20
""",
    },
    "prop": {
        "label": "Interface prop",
        "body": """\
# {title}

<!-- One sentence: what this prop is (button, arrow, frame…), and where it is touched. -->

## Usage

- **Where** —\x20
- **Role** —\x20

## Shape

- **Size** — px on screen, source px
- **Construction** — texture, 9-slice (margins), StyleBox, scene
- **Material** —\x20
- **Palette** —\x20

## States

- **Normal** —\x20
- **Hover** —\x20
- **Pressed** —\x20
- **Disabled** —\x20

## Variants

- **…** —\x20
""",
    },
    "vfx": {
        "label": "VFX concept",
        "body": """\
# {title}

## Intent

<!-- What the player must feel or understand, in one sentence. -->

## Trigger

- **When**: <!-- a spell cast, an impact, a permanent ambience -->
- **On what**: <!-- a character, a weapon, the scenery, the screen -->
- **Sync**: <!-- an animation frame, a game event -->

## Timeline

- **Anticipation**:
- **Peak**:
- **Dissipation**:
- **Duration**: <!-- in seconds -->
- **Loop**: <!-- yes / no -->

## Material and motion

<!-- Fire, smoke, plasma, water, shards, light… and how it moves. -->

## Palette and light

## References

<!-- Image paths (library, inbox), links, reference games. -->

## Godot constraints

- **Dimension**: <!-- 2D / 3D -->
- **Preferred technique**: <!-- particles, shader, flipbook, free -->
- **Size on screen**:
- **Budget**: <!-- particle count, target platform -->

## Success criterion

<!-- How one can tell, watching it play, that it is the right effect. -->
""",
    },
    "card": {
        "label": "World card",
        "body": """\
# {title}

## In short

<!-- One sentence: what it is, and what sets it apart. -->

## Appearance

## In the game
""",
    },
    "todo": {
        "label": "To do",
        "body": """\
# {title}

## In progress

- [ ]

## Next

- [ ]

## Done

- [x]
""",
    },
}


# A project's fixed shelves, with the template used there by default. World
# sections are declared by the user instead (`service/world.py`). The label is
# the rail's, shown as is (a card's workbench, its discussion title).
SHELVES: dict[str, dict[str, str]] = {
    "notes": {"label": "Notes", "template": "note"},
    "ideas": {"label": "Ideas", "template": "idea"},
    "devlog": {"label": "Devlog", "template": "devlog"},
    "design/mechanics": {"label": "Mechanics", "template": "mechanic"},
    "design/interface": {"label": "Interface", "template": "interface"},
    "design/icons": {"label": "Icons", "template": "icon"},
    "design/props": {"label": "Props", "template": "prop"},
    "design/direction": {"label": "Art direction", "template": "direction"},
    "design/vfx": {"label": "VFX", "template": "vfx"},
}


# What a document can show: an image, nothing else.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _when(path: Path) -> str | None:
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(timespec="seconds")


def directory(project: str, folder: str = "") -> Path:
    """The documents folder of a project, or of one of its shelves.

    It is not created.
    """
    root = project_paths(studio().settings, project).documents
    return root.joinpath(*shelf(folder)) if folder else root


def shelf(folder: str) -> list[str]:
    """A shelf's segments, validated one by one.

    As for a document name: refuse rather than clean. Two levels at most
    (`world/characters`) -- a shelf is not a tree.
    """
    parts = [part for part in folder.strip("/").split("/") if part]
    if len(parts) > 2 or any(not NAME_RE.match(part) for part in parts):
        raise ServiceError(f"invalid shelf: “{folder}” (two segments at most, letters, digits, "
                           "hyphens and underscores)")
    return parts


def image_file(project: str, folder: str, src: str) -> Path:
    """An image cited by a document: its path starts from the document.

    `![Top bar](../../../library/renders/top-bar.png)`, in a `design/interface`
    card, points to a render in the library. The path must stay inside the
    project folder and point to an image: a document is not a door to the rest
    of the machine.

    An image filed elsewhere since -- a brief's render moved under
    `briefing/<section>/` -- is still found by its name in the library: the
    card does not need rewriting because the filing changed.
    """
    if not src or "://" in src or src.startswith(("data:", "//")):
        raise NotFound(f"image not found: {src}")
    root = project_paths(studio().settings, project).root.resolve()
    candidate = Path(src)
    path = (candidate if candidate.is_absolute()
            else directory(project, folder) / candidate).resolve()
    if not _inside_image(path, root):
        moved = _find_in_library(project, candidate.name, root)
        if moved is None:
            raise NotFound(f"image not found: {src}")
        path = moved
    return path


def _inside_image(path: Path, root: Path) -> bool:
    """The path is inside the project and is an existing image."""
    return (path.is_relative_to(root) and path.suffix.lower() in IMAGE_TYPES
            and path.is_file())


def _find_in_library(project: str, name: str, root: Path) -> Path | None:
    """Find a moved image by its name, in the project's library.

    The name is enough because the library files a render under a unique name
    (`renders/<name>.png`); two files with the same name in two sections cannot
    both be cited by the same card, and the first found wins.
    """
    if Path(name).suffix.lower() not in IMAGE_TYPES:
        return None
    library = project_paths(studio().settings, project).library
    if not library.is_dir():
        return None
    for found in sorted(library.rglob(name)):
        if _inside_image(found.resolve(), root):
            return found.resolve()
    return None


def templates() -> list[dict[str, str]]:
    """The templates offered when creating a document."""
    return [{"id": key, "label": value["label"]} for key, value in TEMPLATES.items()]


def projects() -> list[str]:
    """The projects that have documents, each in its `.gamestudio/documents/`."""
    found = []
    for project in studio().projects():
        root = project_paths(studio().settings, project).documents
        if root.is_dir() and any(root.glob(f"*{SUFFIX}")):
            found.append(project)
    return found


def slug(value: str) -> str:
    """Turn a title into a stable file name.

    Accents are folded (an accented file name travels badly), and anything not
    alphanumeric becomes a hyphen.
    """
    folded = (value.strip().lower()
              .replace("é", "e").replace("è", "e").replace("ê", "e")
              .replace("à", "a").replace("â", "a").replace("î", "i")
              .replace("ô", "o").replace("û", "u").replace("ù", "u")
              .replace("ç", "c"))
    cleaned = "".join(c if c.isalnum() else "-" for c in folded)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:64] or "document"


def _path(project: str, name: str, folder: str = "") -> Path:
    """A document's path, valid by construction.

    Path traversal is not filtered: any name that is not an identifier is
    refused, so a `..` cannot exist, and the suffix is imposed.
    """
    candidate = name[:-len(SUFFIX)] if name.endswith(SUFFIX) else name
    if not NAME_RE.match(candidate):
        raise ServiceError(
            f"invalid document name: “{name}” (letters, digits, hyphens and underscores, 64 "
            "characters at most)")
    return directory(project, folder) / f"{candidate}{SUFFIX}"


def _title(text: str, fallback: str) -> str:
    """A document's title: its first level-1 heading, otherwise its name."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
        if stripped and not stripped.startswith("<!--"):
            break
    return fallback


def documents(project: str, folder: str = "") -> list[dict[str, Any]]:
    """A project's documents, most recently modified first.

    A missing folder is not an error: a project may have no documents, which
    is the normal starting state.
    """
    where = directory(project, folder)
    if not where.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(where.glob(f"*{SUFFIX}")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
        found.append({
            "project": project,
            "folder": folder,
            "name": path.stem,
            "file": path.name,
            "path": str(path),
            "title": _title(text, path.stem),
            "size_bytes": stat.st_size,
            "lines": text.count("\n") + 1,
            "words": len(text.split()),
            "modified_at": _when(path),
            # The raw timestamp is for sorting: the shown date is rounded to
            # the second, which would leave two documents written in the same
            # second in arbitrary order.
            "mtime": stat.st_mtime,
        })
    found.sort(key=lambda entry: (-entry["mtime"], entry["name"]))
    return found


def _where(project: str, folder: str, file: str) -> str:
    return "/".join(part for part in (project, folder.strip("/"), file) if part)


def read_document(project: str, name: str, folder: str = "") -> dict[str, Any]:
    """A document's text, with its path."""
    path = _path(project, name, folder)
    if not path.is_file():
        raise NotFound(f"document not found: {_where(project, folder, path.name)}")
    text = path.read_text(encoding="utf-8")
    return {
        "project": project, "folder": folder, "name": path.stem, "file": path.name,
        "path": str(path),
        "title": _title(text, path.stem), "text": text,
        "size_bytes": path.stat().st_size, "modified_at": _when(path),
    }


def write_document(project: str, name: str, text: str, *,
                   create: bool = True, folder: str = "") -> dict[str, Any]:
    """Save a document. It is created if missing and `create` is true.

    The name is fixed once the file exists: only `rename_document` moves it,
    on request, rather than silently losing a path others cite.
    """
    if not text.strip():
        raise ServiceError("empty document: nothing to save")
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        raise ServiceError(f"document too large: {size // 1024} KB (beyond {MAX_BYTES // 1024} "
                           "KB, it is no longer a note)")

    path = _path(project, name, folder)
    if not path.is_file() and not create:
        raise NotFound(f"document not found: {_where(project, folder, path.name)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Always end with a newline: without it, the next git diff reports a change
    # that is not one.
    if not text.endswith("\n"):
        text = f"{text}\n"
    path.write_text(text, encoding="utf-8")
    return read_document(project, path.stem, folder)


def create_document(project: str, title: str, template: str = "blank",
                    folder: str = "") -> dict[str, Any]:
    """Create a document from a template. Refuses to overwrite an existing one."""
    if template not in TEMPLATES:
        raise ServiceError(f"unknown template: {template} (known: {', '.join(TEMPLATES)})")
    label = title.strip()
    if not label:
        raise ServiceError("empty title: a document without a title cannot be found again")
    name = slug(label)
    path = _path(project, name, folder)
    if path.exists():
        raise ServiceError(f"“{name}” already exists: open the document, or choose another title")
    body = TEMPLATES[template]["body"].format(
        title=label, date=datetime.now().strftime("%Y-%m-%d"))
    return write_document(project, name, body, folder=folder)


def _sketch_files(path: Path) -> list[Path]:
    """A card's sketch and its export, next to it and under its name.

    Like the workbench, they follow the card when renamed and leave with it
    (see `service/cards.py`).
    """
    from .cards import SKETCH_PNG_SUFFIX, SKETCH_SUFFIX

    return [path.with_name(f"{path.stem}{suffix}")
            for suffix in (SKETCH_SUFFIX, SKETCH_PNG_SUFFIX)]


def delete_document(project: str, name: str, folder: str = "") -> dict[str, Any]:
    """Delete a document, and what lives next to it (workbench, sketch,
    references). A human gesture: no MCP tool exposes it."""
    path = _path(project, name, folder)
    if not path.is_file():
        raise NotFound(f"document not found: {_where(project, folder, path.name)}")
    size = path.stat().st_size
    path.unlink()
    path.with_name(f"{path.stem}{WORKBENCH}").unlink(missing_ok=True)
    for companion in _sketch_files(path):
        companion.unlink(missing_ok=True)
    from .cards import drop_references

    drop_references(path)
    return {"project": project, "folder": folder, "name": path.stem, "deleted": True,
            "size_bytes": size, "path": str(path)}


def rename_document(project: str, name: str, title: str,
                    folder: str = "") -> dict[str, Any]:
    """Rename a document after a new title, without overwriting anyone.

    The only case where the file name moves -- because someone asked, never
    silently.
    """
    source = _path(project, name, folder)
    if not source.is_file():
        raise NotFound(f"document not found: {_where(project, folder, source.name)}")
    target = _path(project, slug(title), folder)
    if target.exists() and target != source:
        raise ServiceError(f"“{target.stem}” already exists")
    source.rename(target)
    workbench = source.with_name(f"{source.stem}{WORKBENCH}")
    if workbench.is_file() and target != source:
        # The entity keeps its id: its concepts and character stay its own
        # under the card's new name.
        from ..pipeline.graph import entity_slug

        state = json.loads(workbench.read_text(encoding="utf-8"))
        state.setdefault("entity", entity_slug(source.stem))
        target.with_name(f"{target.stem}{WORKBENCH}").write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        workbench.unlink()
    if target != source:
        for before, after in zip(_sketch_files(source), _sketch_files(target), strict=True):
            if before.is_file():
                before.rename(after)
        from .cards import move_references

        move_references(source, target)
    return read_document(project, target.stem, folder)
