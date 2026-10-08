"""The workspace: every project summarised, with a readable report for each.

The library (`.gamestudio/library/`) mirrors the *assets*: the project's files,
filed. It answers "where is this file", not "where does this project stand".
The workspace answers the latter, in two forms:

- an **index**: one card per project -- category, pin, and the state of what
  was produced (characters, renders, sheets, batches, cost);
- a **report** per project: `report.html`, standalone (its styling is inlined,
  so it opens outside the studio), with one section per step -- an entity (its
  concept, 3D mesh and sprites), an icon sheet, a frame batch, a style
  exploration.

Nothing is written by hand: everything derives from the database and the
library index. A card describing anything other than what the studio holds
would be a polite lie, which is exactly what a report must not be.

The only hand-written part is a project's declaration (`workspace.json` in
`.gamestudio/workspace/`): a title, a description, a category, a pin. The rest
is recomputed -- after each production, and on demand.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain.models import JobState
from ..store.folders import check_name, project_paths
from .context import space, studio
from .errors import NotFound, ServiceError
from .jobs import active

# Files written to .gamestudio/workspace/.
DECLARATION = "workspace.json"
REPORT = "report.html"

# Card vocabulary. The category is inferred: a project with a recipe is a
# project; one made only of free generations and imports is a workbench. The
# declaration can override it.
CATEGORIES = ("project", "workbench", "research")
STATUSES = ("active", "dormant", "archived")

# A pipeline's label, as it reads on a card.
PIPELINE_LABELS = {"mesh3d": "3D", "sprites": "sprites"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _when(value: str | None) -> str:
    return (value or "")[:16].replace("T", " ")


def _dir(project: str) -> Path:
    return project_paths(studio().settings, project).workspace


def project_dir(project: str) -> Path:
    """A project's workspace folder. It is not created."""
    return _dir(project)


def report_path(project: str) -> Path:
    return _dir(project) / REPORT


# --------------------------------------------------------------- declaration


def _recipe(project: str) -> dict[str, Any] | None:
    """The project's recipe, if any. An unreadable recipe does not raise."""
    from . import catalog

    try:
        return catalog.find_recipe(project)
    except NotFound:
        return None


def read_meta(project: str) -> dict[str, Any]:
    """The hand-written declaration, or honest defaults."""
    path = _dir(project) / DECLARATION
    declared: dict[str, Any] = {}
    if path.is_file():
        try:
            declared = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ServiceError(f"{path} cannot be read: {exc}") from exc
    recipe = _recipe(project)
    return {
        "project": project,
        "title": declared.get("title") or project,
        "description": declared.get("description", ""),
        "category": declared.get("category") or ("project" if recipe else "workbench"),
        "status": declared.get("status") or "active",
        "pinned": bool(declared.get("pinned", False)),
        "recipe": recipe["path"] if recipe else None,
        "declared": bool(declared),
        "path": str(path),
        "logo": _logo_info(project),
    }


def set_meta(project: str, *, title: str | None = None,
             description: str | None = None, category: str | None = None,
             status: str | None = None, pinned: bool | None = None) -> dict[str, Any]:
    """Amend a project's declaration. Only the given fields change."""
    if category is not None and category not in CATEGORIES:
        raise ServiceError(f"unknown category: {category} (known: {', '.join(CATEGORIES)})")
    if status is not None and status not in STATUSES:
        raise ServiceError(f"unknown status: {status} (known: {', '.join(STATUSES)})")

    wanted = {key: value for key, value in
              (("title", title), ("description", description),
               ("category", category), ("status", status), ("pinned", pinned))
              if value is not None}
    current = read_meta(project)
    merged = {
        "title": wanted.get("title", current["title"] if current["declared"] else ""),
        "description": wanted.get("description",
                                  current["description"] if current["declared"] else ""),
        "category": wanted.get("category", current["category"]),
        "status": wanted.get("status", current["status"]),
        "pinned": wanted.get("pinned", current["pinned"]),
        "updated_at": _now_iso(),
    }
    target = _dir(project)
    target.mkdir(parents=True, exist_ok=True)
    (target / DECLARATION).write_text(
        json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return read_meta(project)


# ----------------------------------------------------------------------- logo
#
# A workspace's logo is chosen by hand, so it lives with what is kept -- the
# project's documents folder (`.gamestudio/documents/logo.png`) -- not in
# `.gamestudio/workspace/`, which is a cache.

LOGO_STEM = "logo"
LOGO_MAX_BYTES = 4 * 1024 * 1024
# The type is recognized by the first bytes, never by the claimed extension.
LOGO_KINDS = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF8", "gif"),
)


def _logo_kind(data: bytes) -> str:
    for magic, ext in LOGO_KINDS:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    head = data[:512].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048]):
        return "svg"
    raise ServiceError("logo refused: PNG, JPEG, GIF, WebP or SVG only")


def logo_path(project: str) -> Path | None:
    """The logo file, if there is one."""
    folder = project_paths(studio().settings, project).documents
    for ext in ("png", "jpg", "gif", "webp", "svg"):
        candidate = folder / f"{LOGO_STEM}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def _logo_info(project: str) -> dict[str, Any] | None:
    path = logo_path(project)
    if path is None:
        return None
    # The modification time serves as version: the interface appends it to the
    # URL, so a cached old logo does not hide a new one.
    return {"path": str(path), "version": int(path.stat().st_mtime_ns // 1_000_000)}


def set_logo(project: str, data: bytes) -> dict[str, Any]:
    """Set a workspace's logo, replacing the old one whatever its type."""
    if not data:
        raise ServiceError("empty logo")
    if len(data) > LOGO_MAX_BYTES:
        raise ServiceError(f"logo too large: {len(data) // 1024} KB (at most "
                           f"{LOGO_MAX_BYTES // 1024 // 1024} MB)")
    ext = _logo_kind(data)
    clear_logo(project)
    folder = project_paths(studio().settings, project).documents
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{LOGO_STEM}.{ext}").write_bytes(data)
    return read_meta(project)


def set_logo_from_file(project: str, path: str) -> dict[str, Any]:
    source = Path(path).expanduser()
    if not source.is_file():
        raise NotFound(f"file not found: {source}")
    return set_logo(project, source.read_bytes())


def clear_logo(project: str) -> dict[str, Any]:
    """Remove the logo. The workspace goes back to its default mark."""
    while (path := logo_path(project)) is not None:
        path.unlink()
    return read_meta(project)


# ---------------------------------------------------------------------- steps


def _character_steps(project: str, index: Any) -> list[dict[str, Any]]:
    """One step per character, in the order the studio produced them."""
    steps = []
    for character in space(project).db.list_characters(project):
        slug = character.spec.id
        facts = [
            ("State", character.state.value),
            ("Pipelines", ", ".join(
                PIPELINE_LABELS.get(p.value, p.value) for p in character.spec.pipelines)
             or "none"),
            ("Subject", character.spec.subject or "—"),
        ]
        files = []
        for section, label in (("2d", "2D"), ("3d", "3D")):
            folder = index.folder(f"{section}/{slug}")
            if folder is None:
                continue
            files.extend({"name": file.name, "folder": folder.path}
                         for file in folder.files)
            facts.append((f"{label} files", str(len(folder.files))))
        if character.rig3d is not None:
            facts.append(("Skeleton bones", str(len(character.rig3d.bones))))
            if character.rig3d.animations:
                facts.append(("Animations", ", ".join(character.rig3d.animations)))
            if character.rig3d.normalization_report:
                # What the rig says about itself: the line to reread when it
                # misbehaves.
                facts.append(("Rig", character.rig3d.normalization_report))
        if character.spritesheets:
            facts.append(("Sprite sheets",
                          ", ".join(sorted(character.spritesheets))))
        if character.concept_asset_id:
            facts.append(("Concept", "in the library"))
        for name, path in sorted(character.exports.items()):
            facts.append((f"Export {name}", path))
        if character.errors:
            facts.append(("To review", "; ".join(character.errors)))
        if not files:
            continue
        steps.append({
            "id": f"character-{slug}",
            "kind": "character",
            "title": character.spec.name or slug,
            "subtitle": slug,
            "facts": facts,
            "files": files,
        })
    return steps


def _section_steps(index: Any) -> list[dict[str, Any]]:
    """Batches and sheets: generations, icons, video, style."""
    steps = []
    for section, kind, label in (("generations", "generation", "generation batch"),
                                 ("icons", "sheet", "element sheet"),
                                 ("video", "video", "reference video")):
        for name in index.batches(section):
            folder = index.folder(f"{section}/{name}")
            if folder is None:
                continue
            document = folder.documents.get(
                {"generations": "batch.json", "icons": "sheet.json",
                 "video": "frames.json"}[section], {})
            facts = [("Folder", folder.path)]
            for key in ("prompt", "model", "source", "strategy", "fps", "date"):
                if document.get(key):
                    facts.append((key.capitalize(), str(document[key])))
            facts.append(("Files", str(len(folder.files))))
            steps.append({
                "id": f"{section}-{name}",
                "kind": kind,
                "title": name,
                "subtitle": label,
                "facts": facts,
                "files": [{"name": file.name, "folder": folder.path}
                          for file in folder.files],
            })
    style = index.folder("style")
    if style is not None and style.files:
        steps.append({
            "id": "style",
            "kind": "style",
            "title": "Art direction",
            "subtitle": "reference images",
            "facts": [("Files", str(len(style.files)))],
            "files": [{"name": file.name, "folder": "style"} for file in style.files],
        })
    return steps


def _document_steps(project: str) -> list[dict[str, Any]]:
    """The hand-written documents: they are part of what a project is.

    A card showing only what the machine produced would leave out the one part
    that cannot be regenerated.
    """
    from . import documents

    return [{
        "id": f"document-{entry['name']}",
        "kind": "document",
        "title": entry["title"],
        "subtitle": entry["file"],
        "facts": [
            ("File", entry["path"]),
            ("Size", f"{entry['size_bytes']} bytes"),
            ("Lines", str(entry["lines"])),
            ("Modified", (entry["modified_at"] or "")[:16].replace("T", " ")),
        ],
        "files": [],
    } for entry in documents.documents(project)]


def steps(project: str, index: Any | None = None) -> list[dict[str, Any]]:
    """A project's steps: what was produced, and where to look at it."""
    index = index if index is not None else space(project).librarian.index_project(project)
    steps = _character_steps(project, index) + _section_steps(index)
    recipe = _recipe(project)
    if recipe:
        built = {entry["subtitle"] for entry in steps if entry["kind"] == "character"}
        missing = [name for name in recipe["characters"] if name not in built]
        steps.insert(0, {
            "id": "roster",
            "kind": "roster",
            "title": f"Roster — {recipe['project']}",
            "subtitle": f"style {recipe['style']}",
            "facts": [
                ("Declared", str(len(recipe["characters"]))),
                ("Built", str(len(built))),
                ("Remaining", ", ".join(missing) if missing else "none"),
                ("Recipe", recipe["path"]),
            ],
            "files": [],
        })
    # Documents come last: what was written for the project reads after what
    # it produced.
    steps.extend(_document_steps(project))
    return steps


# ----------------------------------------------------------------------- card


def summary(project: str) -> dict[str, Any]:
    """A project summary: its declaration, its state, its steps.

    Job counts and cost cover the project's whole database, never just its
    latest jobs.
    """
    st = space(project)
    meta = read_meta(project)
    index = st.librarian.index_project(project)
    states = st.queue.stats(project)
    characters = st.db.list_characters(project)
    return {
        **meta,
        "library": str(st.librarian.project_dir(project)),
        "root": str(st.paths.root),
        "linked": st.paths.linked,
        "workspace": str(_dir(project)),
        "report": str(report_path(project)),
        "generated_at": _now_iso(),
        "counters": {
            "files": sum(len(folder.files) for folder in index.folders),
            "characters": len(characters),
            "to_review": sum(1 for c in characters if c.errors
                             or c.state.value == "needs_review"),
            "jobs": sum(states.values()),
            "jobs_active": active(states),
            "jobs_failed": states.get(JobState.FAILED.value, 0),
            "cost_usd": st.db.total_cost(project),
        },
        "steps": steps(project, index),
    }


def known_projects() -> list[str]:
    """The workspace's projects: one per folder, opened or hosted by the studio.

    Each project carries everything that makes it exist in its `.gamestudio/`
    -- recipe, documents, database, library: nothing to gather from elsewhere.
    """
    return studio().projects()


def index() -> dict[str, Any]:
    """Every card: pinned projects first, then the most recent.

    The order is the reading order, not the database's: what is pinned, then
    what moved most recently.
    """
    summaries = [summary(project) for project in known_projects()]
    summaries.sort(key=lambda entry: (not entry["pinned"],
                                  -_last_activity(entry["project"])))
    return {
        "generated_at": _now_iso(),
        "counters": {
            "projects": len(summaries),
            "pinned": sum(1 for entry in summaries if entry["pinned"]),
            "to_review": sum(entry["counters"]["to_review"] for entry in summaries),
            "jobs_active": sum(entry["counters"]["jobs_active"] for entry in summaries),
            "cost_usd": round(sum(entry["counters"]["cost_usd"] for entry in summaries), 4),
        },
        "projects": summaries,
    }


def _last_activity(project: str) -> float:
    """The time of a project's last event, to sort the cards.

    The date of the last job when there is one -- the usual case -- otherwise
    that of the project's library, enough to tell a fresh card from a forgotten
    one.
    """
    st = space(project)
    jobs = st.db.list_jobs(project=project, limit=1)
    if jobs:
        return jobs[0].created_at.timestamp()
    library = st.librarian.project_dir(project)
    return library.stat().st_mtime if library.exists() else 0.0


# --------------------------------------------------------------------- report


def _facts_html(facts: list[tuple[str, str]]) -> str:
    rows = "".join(
        f"<div><dt>{html.escape(str(label))}</dt><dd>{html.escape(str(value))}</dd></div>"
        for label, value in facts)
    return f'<dl class="facts">{rows}</dl>'


def _files_html(files: list[dict[str, str]], *, limit: int = 60) -> str:
    if not files:
        return ""
    shown = files[:limit]
    items = "".join(
        f'<li><span class="name">{html.escape(file["name"])}</span><span class="where mono">'
        f'{html.escape(file["folder"])}</span></li>'
        for file in shown)
    more = (f'<p class="more">and {len(files) - len(shown)} more file(s).</p>'
            if len(files) > len(shown) else "")
    return f'<ul class="files">{items}</ul>{more}'


# The report's styling, inlined: it opens outside the studio, with no network
# and no font to download. The colors are the studio console's
# (app/src/styles.css), copied as literals: the report cannot read its variables.
REPORT_CSS = """\
:root{--bg:#0f0d0b;--surface:#1a1715;--fg:#fafafa;--fg-2:#e8e3de;--muted:#a8a29e;
--accent:#ff7a45;--gold:#ffd260;--warn:#ffb347;--danger:#ff5533;--success:#10b981;
--border:rgba(255,160,80,.15);--border-soft:rgba(255,160,80,.09)}
*{box-sizing:border-box}
body{margin:0;padding:32px;background:var(--bg);color:var(--fg-2);
font:400 14px/1.6 "Prompt",system-ui,sans-serif}
main{max-width:900px;margin:0 auto}
.mono{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-variant-numeric:tabular-nums}
header.top{border-bottom:1px solid var(--border);padding-bottom:16px;margin-bottom:24px}
h1{font-size:22px;margin:0 0 4px;color:var(--fg)}
.where{color:var(--muted);font-size:12px;margin:0}
.counters{display:flex;gap:24px;flex-wrap:wrap;margin-top:16px}
.counters div{min-width:90px}
.counters b{display:block;font-size:20px;color:var(--gold);
font-family:ui-monospace,monospace;font-variant-numeric:tabular-nums}
.counters span{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
nav.toc{margin:0 0 32px;padding:12px 16px;background:var(--surface);
border:1px solid var(--border-soft);border-radius:4px}
nav.toc a{color:var(--accent);text-decoration:none;font-size:12px}
nav.toc li{margin:2px 0}
section.step{margin:0 0 32px;padding:20px;background:var(--surface);
border:1px solid var(--border-soft);border-radius:4px}
section.step h2{margin:0;font-size:16px;color:var(--fg)}
section.step .kind{font-size:11px;color:var(--muted);text-transform:uppercase;
letter-spacing:.08em}
dl.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
gap:8px 24px;margin:16px 0 0}
dl.facts div{display:flex;gap:8px;font-size:12px;
border-bottom:1px dotted var(--border-soft);padding-bottom:4px}
dl.facts dt{color:var(--muted);min-width:110px}
dl.facts dd{margin:0;color:var(--fg-2);word-break:break-word}
ul.files{list-style:none;margin:16px 0 0;padding:0;max-height:280px;overflow:auto}
ul.files li{display:flex;justify-content:space-between;gap:16px;font-size:12px;
padding:3px 0;border-bottom:1px dotted var(--border-soft)}
ul.files .where{color:var(--muted)}
.more{font-size:11px;color:var(--muted);margin:8px 0 0}
.empty{color:var(--muted)}
footer{margin-top:32px;padding-top:16px;border-top:1px solid var(--border);
font-size:11px;color:var(--muted)}
"""


def render(project: str) -> str:
    """A project's report, as standalone and deterministic HTML."""
    entry = summary(project)
    counters = entry["counters"]
    toc = "".join(
        f'<li><a href="#{html.escape(step["id"])}">{html.escape(step["title"])}</a></li>'
        for step in entry["steps"])
    sections = []
    for step in entry["steps"]:
        sections.append(
            f'<section class="step" id="{html.escape(step["id"])}"><p class="kind">'
            f'{html.escape(step["kind"])}'
            f'{" · " + html.escape(step["subtitle"]) if step["subtitle"] else ""}</p><h2>'
            f'{html.escape(step["title"])}</h2>'
            f'{_facts_html(step["facts"])}{_files_html(step["files"])}</section>')
    body = "".join(sections) or (
        "<p class=\"empty\">Nothing has been produced in this project yet: generations,"
        " splits and renders will appear here.</p>")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(entry['title'])} — gamestudio report</title>
<style>{REPORT_CSS}</style>
</head>
<body>
<main>
<header class="top">
  <h1>{html.escape(entry['title'])}</h1>
  <p class="where">{html.escape(project)} · {html.escape(entry['category'])}
  · report of <span class="mono">{_when(entry['generated_at'])}</span></p>
  {f'<p class="where">{html.escape(entry["description"])}</p>'
   if entry['description'] else ''}
  <div class="counters">
    <div><b>{counters['characters']}</b><span>characters</span></div>
    <div><b>{counters['files']}</b><span>files</span></div>
    <div><b>{counters['to_review']}</b><span>to review</span></div>
    <div><b>{counters['jobs_active']}</b><span>running</span></div>
    <div><b>{counters['jobs_failed']}</b><span>failed</span></div>
    <div><b>{counters['cost_usd']:.2f} $</b><span>spent</span></div>
  </div>
</header>
{f'<nav class="toc"><ul>{toc}</ul></nav>' if toc else ''}
{body}
<footer>
  Generated by gamestudio from the database and the library —
  library: <span class="mono">{html.escape(entry['library'])}</span>.
  This file is rewritten after each production: do not edit it.
</footer>
</main>
</body>
</html>
"""


def write_report(project: str) -> str:
    """Write a project's `report.html` and return its path.

    The declaration is written along with it if missing: the card becomes
    editable from the interface without guessing the file format.
    """
    target = _dir(project)
    target.mkdir(parents=True, exist_ok=True)
    path = target / REPORT
    path.write_text(render(project), encoding="utf-8")

    declaration = target / DECLARATION
    if not declaration.is_file():
        meta = read_meta(project)
        declaration.write_text(
            json.dumps({key: meta[key] for key in
                        ("title", "description", "category", "status", "pinned")},
                       indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def write_all() -> list[str]:
    """Write the report of every known project."""
    return [write_report(project) for project in known_projects()]


# ------------------------------------------------------------------- deletion


def delete_project(project: str) -> dict[str, Any]:
    """Delete a project, and everything that makes it exist.

    Everything that makes a project lives in its folder. For a project open on
    a user folder, that folder belongs to the user: the studio deletes nothing
    in it, it forgets it (reopening it brings everything back). For a project
    the studio hosts (`data/projects/<project>/`), the whole folder goes --
    recipe, documents, database, store, library, report, bundles.

    A running job refuses the deletion: the worker would write into a project
    that no longer exists.
    """
    import shutil

    check_name(project)
    if project not in known_projects():
        raise NotFound(f"unknown project: {project}")

    st = space(project)
    running = active(st.queue.stats(project))
    if running:
        raise ServiceError(f"{running} task(s) running on “{project}”: wait for them to finish "
                           "before deleting the project")

    if st.paths.linked:
        studio().registry.unlink(project)
        return {"project": project, "root": str(st.paths.root), "erased": False,
                "folders": []}

    assets = len(st.db.list_assets(limit=1_000_000))
    # Forget the space before erasing: a project recreated under the same name
    # must not get back the database of the erased one.
    studio().forget(project)
    shutil.rmtree(st.paths.root, ignore_errors=True)
    return {"project": project, "root": str(st.paths.root), "erased": True,
            "assets": assets, "folders": [str(st.paths.root)]}
