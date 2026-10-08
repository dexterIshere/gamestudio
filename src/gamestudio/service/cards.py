"""A game design card's workbench: its render, its sketch, its generated images.

An Interface, Mechanics, Art direction or VFX card is not just a text: it is
where work on that subject goes on. Next to the text live the game's render as
it is (the latest `render_scene` carrying the card's name), the user's sketch
(an Excalidraw saved next to the card, exported as PNG so that agents can see
it), and the images generated for it.

Four links, of four kinds:

- the **render** is linked by its name and section, exactly as the library
  files it (`briefing/<section>/<name>.png`): nothing to write on the card,
  and a new render with the same name updates it. It keeps its settings,
  enough to redo it as is (`rerender`);
- the **sketch** lives next to the card, under its name
  (`<name>.sketch.excalidraw` and its export `<name>.sketch.png`): versioned
  with it, it follows the card when renamed and leaves with it
  (`documents.rename_document`, `documents.delete_document`);
- the **references** are the images the user drops for the card (a mood, a
  game, a sheet): they live next to it, in `<name>.references/`, versioned with
  it, and follow it like the sketch;
- a **generated image** carries the card in its metadata
  (`card = "<section>/<name>"`, and `reference_kind`: where its reference image
  came from), and its job the step `card:<section>/<name>`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..domain.models import Asset, Job, JobState
from ..store.folders import BRIEFING_FOLDER
from ..store.library import render_folder
from . import documents, library, produce, renders, world
from .context import space, studio
from .errors import NotFound, PaymentRequired, ServiceError

# The sketch lives next to the card, under its name: it follows the card when
# renamed, and leaves with it.
SKETCH_SUFFIX = ".sketch.excalidraw"
SKETCH_PNG_SUFFIX = ".sketch.png"
# The step of a card's generation jobs: `card:<section>/<name>`.
STEP_PREFIX = "card:"
REFERENCES = ("", "sketch", "render")
# A generation can start from one of the card's reference images:
# `reference="ref:<file>"`.
REF_PREFIX = "ref:"

# Reference images live next to the card, in a folder named after it.
REFERENCES_SUFFIX = ".references"
# A reference used as a generation's starting point enters the store.
REFERENCE_ROLE = "reference"
# A reference is an image to look at: a format the browser, PIL and an image
# model can all read.
REFERENCE_TYPES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
MAX_REFERENCE_BYTES = 25 * 1024 * 1024
# Beyond this, it is no longer a reference board, it is a library.
MAX_REFERENCES = 60

# A generation only takes its reference image by asset id: the sketch export
# therefore enters the store, under this role, when used as a reference.
SKETCH_ROLE = "sketch"
# The role of the images a generation produces (`GenerateImage`).
GENERATION_ROLE = "generation"

# An Excalidraw scene embeds the images pasted into it (a render to trace, a
# reference): a few MB. Beyond this, it is no longer a sketch.
MAX_SCENE_BYTES = 16 * 1024 * 1024
MAX_PNG_BYTES = 16 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# A generation for a card is a lead, not an exploration batch.
MAX_COUNT = 4
# How many images are scanned to find a card's render and generations.
SCAN_LIMIT = 2000
# How many jobs are scanned, per state, for a card's pending and failed ones.
JOB_SCAN = 200
# A failure shows for a day, and only the last three: it is a signal, not a
# log (the job log keeps everything).
FAILURE_WINDOW = timedelta(hours=24)
MAX_FAILURES = 3

# The prompt seed: enough to write the rest, not the whole card.
SEED_LENGTH = 300
SEED_ITEMS = 4


# ------------------------------------------------------------------ the card


def _folder(folder: str) -> str:
    """A card's section, validated like any shelf -- and never empty."""
    parts = documents.shelf(folder)
    if not parts:
        raise ServiceError("empty section: a card lives in a section (`design/interface`, "
                           "`world/<section>`)")
    return "/".join(parts)


def _card(project: str, folder: str, name: str) -> tuple[str, dict[str, Any]]:
    """The normalized section, and the card -- which must exist."""
    where = _folder(folder)
    return where, documents.read_document(project, name, where)


def section_label(project: str, folder: str) -> str:
    """The section as the rail names it: a fixed shelf, a world section, else
    its folder's name."""
    if folder in documents.SHELVES:
        return documents.SHELVES[folder]["label"]
    parts = folder.split("/")
    if len(parts) == 2 and parts[0] == world.SHELF:
        try:
            return str(world.section(project, parts[1])["label"])
        except ServiceError:
            pass
    return parts[-1]


def _image_size(path: Path | None) -> tuple[int | None, int | None]:
    """An image's size, read from its header; None if it cannot be read."""
    if path is None or not path.is_file():
        return None, None
    from PIL import Image

    try:
        with Image.open(path) as image:
            return image.width, image.height
    except (OSError, ValueError):
        return None, None


# ------------------------------------------------------------------- the render


def _godot(meta: dict[str, Any]) -> str:
    """A render's Godot project, as `renders.resolve_scene` names it.

    The game root is written `.`: an empty name would leave the studio to
    guess, and it refuses to guess when the game holds several Godot projects.
    """
    if "godot" not in meta:
        return ""
    return str(meta.get("godot") or ".")


def _scene_file(project: str, meta: dict[str, Any]) -> str:
    """A render's scene from the game root, or "" if it no longer resolves."""
    scene = str(meta.get("scene") or "")
    if not scene:
        return ""
    try:
        return str(renders.resolve_scene(project, scene, _godot(meta))["local"])
    except ServiceError:
        return ""


def _latest_render(project: str, folder: str, name: str,
                   assets: list[Asset] | None = None) -> Asset | None:
    """The card's render: the latest with its name, if the library files it with the card.

    This is the library's rule, not another (`Librarian._render_folders`, then
    `store.library.render_folder`): the card shows the image the mirror files
    under `briefing/<section>/<name>.png`. A render with the same name filed
    elsewhere -- another section, or the free renders -- is not its own.
    """
    wanted = documents.slug(name)
    if assets is None:
        assets = space(project).db.list_assets(kind="image", limit=SCAN_LIMIT)
    latest: Asset | None = None
    for asset in assets:
        meta = asset.meta
        if meta.get("role") != renders.ROLE or meta.get("project") != project:
            continue
        label = str(meta.get("name") or "")
        if not label or documents.slug(label) != wanted:
            continue
        if latest is None or str(meta.get("rendered_at", "")) > \
                str(latest.meta.get("rendered_at", "")):
            latest = asset
    if latest is None:
        return None
    if render_folder(latest, renders.briefings(project)) != f"{BRIEFING_FOLDER}/{folder}":
        return None
    return latest


def _render_path(project: str, folder: str, asset: Asset) -> Path:
    """Where the library files a card's render: `briefing/<section>/<name>.png`."""
    mirror = space(project).librarian.project_dir(project)
    label = documents.slug(str(asset.meta.get("name") or ""))
    return mirror / BRIEFING_FOLDER / folder / f"{label}.png"


def _render_entry(project: str, asset: Asset, path: Path) -> dict[str, Any]:
    """What the workbench knows of a render (`CardRender`)."""
    st = space(project)
    stored = st.store.path_for(asset.id)
    if not path.is_file() and stored is not None:
        # Rendered by another process, mirror not written yet: the returned
        # path must exist.
        st.librarian.sync_project(project)
    meta = asset.meta
    scene = str(meta.get("scene") or "")
    file = _scene_file(project, meta)
    width, height = _image_size(path if path.is_file() else stored)
    # A setup can be replayed if its code was kept; older renders only
    # recorded that there was one.
    kept = not meta.get("setup") or bool(meta.get("setup_code"))
    return {"asset_id": asset.id, "path": str(path), "scene": scene, "file": file,
            "rendered_at": str(meta.get("rendered_at") or ""),
            "width": width, "height": height,
            "rerender": bool(scene and file) and kept}


# ------------------------------------------------------------------ the sketch


def _sketch_files(project: str, folder: str, name: str) -> tuple[Path, Path]:
    """The scene and its export, next to the card and under its name."""
    where = documents.directory(project, folder)
    return where / f"{name}{SKETCH_SUFFIX}", where / f"{name}{SKETCH_PNG_SUFFIX}"


def _sketch_entry(scene: Path, png: Path) -> dict[str, Any]:
    """What the workbench knows of a sketch (`CardSketch`), whether it exists or not."""
    exported = png.is_file()
    return {"path": str(scene), "png": str(png) if exported else None,
            # The name is enough: the export is next to the card, and
            # `documents.image_file` resolves an image from the card.
            "src": png.name if exported else None,
            "mtime": scene.stat().st_mtime if scene.is_file() else 0.0}


def _sketch(project: str, folder: str, name: str) -> dict[str, Any] | None:
    scene, png = _sketch_files(project, folder, name)
    return _sketch_entry(scene, png) if scene.is_file() else None


def _scene_text(scene: Any) -> str:
    """The scene as written: a valid, bounded JSON object."""
    if not isinstance(scene, dict):
        raise ServiceError("invalid sketch: the scene is a JSON object (the one from "
                           "`serializeAsJSON`)")
    if not isinstance(scene.get("elements", []), list):
        raise ServiceError("invalid sketch: `elements` must be a list")
    try:
        # `allow_nan=False`: the editor reads the file back with `JSON.parse`,
        # which does not know NaN.
        text = json.dumps(scene, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise ServiceError(f"invalid sketch: {exc}") from exc
    size = len(text.encode("utf-8"))
    if size > MAX_SCENE_BYTES:
        raise ServiceError(f"sketch too large: {size // 1024} KB (at most "
                           f"{MAX_SCENE_BYTES // (1024 * 1024)} MB)")
    return text


def _png_bytes(data_url: str) -> bytes:
    """The decoded sketch export: a base64 PNG data URL, and nothing else.

    What arrives is checked, not trusted: the signature first, then the
    structure (`verify` rereads each chunk) -- a truncated export would pass
    the signature, and the agent looking at it would see nothing.
    """
    header, comma, payload = data_url.strip().partition(",")
    params = [part.strip().lower() for part in header.removeprefix("data:").split(";")]
    if not comma or not header.startswith("data:"):
        raise ServiceError("invalid sketch export: a data URL is expected "
                           "(data:image/png;base64,…)")
    if params[0] != "image/png" or "base64" not in params[1:]:
        raise ServiceError("sketch export refused: only a base64 PNG is accepted")
    if len(payload) > (MAX_PNG_BYTES // 3 + 1) * 4:
        raise ServiceError(f"sketch export too large (at most {MAX_PNG_BYTES // (1024 * 1024)} MB)")
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError("unreadable sketch export: invalid base64") from exc
    if not data.startswith(PNG_SIGNATURE):
        raise ServiceError("sketch export refused: it is not a PNG")
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as exc:  # PIL raises whatever the format inspires
        raise ServiceError(f"unreadable sketch export: {exc}") from exc
    return data


def _write_atomic(path: Path, data: bytes) -> None:
    """Write a file in one go: a reader never sees half a sketch.

    The temporary file is next to the target (a `rename` does not cross file
    systems), and created with ordinary permissions: the sketch is versioned
    with the card.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# ---------------------------------------------------------- references


def references_dir(project: str, folder: str, name: str) -> Path:
    """A card's references folder: next to it, under its name."""
    return documents.directory(project, folder) / f"{name}{REFERENCES_SUFFIX}"


def _reference_entry(path: Path) -> dict[str, Any]:
    """What the workbench knows of a reference (`CardReferenceImage`)."""
    width, height = _image_size(path)
    stat = path.stat()
    return {"file": path.name, "path": str(path), "width": width, "height": height,
            "size_bytes": stat.st_size,
            "added_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            .isoformat(timespec="seconds")}


def _reference_files(where: Path) -> list[Path]:
    """The folder's images, in the order they were dropped."""
    if not where.is_dir():
        return []
    found = [path for path in where.iterdir()
             if path.is_file() and path.suffix.lower() in REFERENCE_TYPES]
    return sorted(found, key=lambda path: (path.stat().st_mtime, path.name))


def _references(project: str, folder: str, name: str) -> list[dict[str, Any]]:
    return [_reference_entry(path)
            for path in _reference_files(references_dir(project, folder, name))]


def _reference_name(filename: str, suffix: str, taken: set[str]) -> str:
    """A safe file name: the dropped one, cleaned, and never one already taken."""
    stem = documents.slug(Path(filename or "").stem)[:48].strip("-._") or "reference"
    candidate, n = f"{stem}{suffix}", 2
    while candidate.lower() in taken:
        candidate, n = f"{stem}-{n}{suffix}", n + 1
    return candidate


def add_reference(project: str, folder: str, name: str, data: bytes,
                  filename: str = "") -> dict[str, Any]:
    """Drop a reference image for the card, and return it.

    The same content dropped twice returns the file already there
    (`duplicate`) rather than a copy. An unreadable image is refused: keeping it
    would make a reference nobody can look at.
    """
    folder, card = _card(project, folder, name)
    if not data:
        raise ServiceError("empty file: nothing to drop")
    if len(data) > MAX_REFERENCE_BYTES:
        raise ServiceError(f"image too heavy: {len(data) // (1024 * 1024)} MB (at most "
                           f"{MAX_REFERENCE_BYTES // (1024 * 1024)} MB)")
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            kind = (image.format or "").lower()
    except (UnidentifiedImageError, OSError) as exc:
        raise ServiceError(f"unreadable image: {filename or 'file'} ({exc})") from exc
    suffix = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}.get(kind)
    if suffix is None:
        raise ServiceError(f"format refused: {kind or '?'} "
                           f"(expected: {', '.join(sorted(REFERENCE_TYPES))})")
    where = references_dir(project, folder, card["name"])
    present = _reference_files(where)
    digest = hashlib.sha256(data).digest()
    for path in present:
        if path.stat().st_size == len(data) and hashlib.sha256(
                path.read_bytes()).digest() == digest:
            return {**_reference_entry(path), "duplicate": True}
    if len(present) >= MAX_REFERENCES:
        raise ServiceError(f"{MAX_REFERENCES} references at most per card: remove some before "
                           "dropping others")
    where.mkdir(parents=True, exist_ok=True)
    taken = {path.name.lower() for path in where.iterdir()}
    target = where / _reference_name(filename, suffix, taken)
    _write_atomic(target, data)
    return {**_reference_entry(target), "duplicate": False}


def add_reference_file(project: str, folder: str, name: str, path: str,
                       filename: str = "") -> dict[str, Any]:
    """Drop an image from disk for the card: absolute path, or relative to the
    studio root (as the inbox returns it). The source file stays in place.

    `filename` names the reference when the path does not: the inbox renames
    what is dropped in it (date, hash), the brief keeps the original name.
    """
    source = Path(path).expanduser()
    if not source.is_absolute():
        root = studio().settings.project_root or Path.cwd()
        source = root / source
    if not source.is_file():
        raise NotFound(f"file not found: {source}")
    return add_reference(project, folder, name, source.read_bytes(),
                         filename.strip() or source.name)


def remove_reference(project: str, folder: str, name: str, file: str) -> dict[str, Any]:
    """Remove a reference from the card. A user gesture: nothing brings it back."""
    folder, card = _card(project, folder, name)
    path = _reference_path(project, folder, card["name"], file)
    path.unlink()
    where = path.parent
    if where.is_dir() and not any(where.iterdir()):
        where.rmdir()
    return {"file": path.name, "deleted": True}


def _reference_path(project: str, folder: str, name: str, file: str) -> Path:
    """A card reference, by its file name -- never another path."""
    where = references_dir(project, folder, name)
    if not file or "/" in file or "\\" in file or file.startswith("."):
        raise ServiceError(f"invalid reference name: {file!r}")
    path = where / file
    if path.suffix.lower() not in REFERENCE_TYPES or not path.is_file():
        raise NotFound(f"reference not found: {file}")
    return path


def move_references(source: Path, target: Path) -> None:
    """A renamed card's references follow it (`documents.rename_document`)."""
    before = source.with_name(f"{source.stem}{REFERENCES_SUFFIX}")
    after = target.with_name(f"{target.stem}{REFERENCES_SUFFIX}")
    if before.is_dir() and before != after and not after.exists():
        before.rename(after)


def drop_references(path: Path) -> None:
    """A deleted card's references leave with it."""
    where = path.with_name(f"{path.stem}{REFERENCES_SUFFIX}")
    if where.is_dir():
        shutil.rmtree(where)


# ---------------------------------------------------------- generations


def _generations(project: str, key: str, assets: list[Asset]) -> list[dict[str, Any]]:
    """The images generated for the card, newest first."""
    st = space(project)
    mine = [asset for asset in assets
            if asset.meta.get("role") == GENERATION_ROLE
            and asset.meta.get("card") == key
            and asset.meta.get("project", project) == project]
    ranged = library.locate(project, [asset.id for asset in mine]) if mine else {}
    rows: list[dict[str, Any]] = []
    for asset in mine:
        found = ranged.get(asset.id)
        path = Path(found["path"]) if found else None
        kind = str(asset.meta.get("reference_kind") or "")
        width, height = _image_size(st.store.path_for(asset.id))
        rows.append({
            "asset_id": asset.id,
            "path": str(path) if path is not None and path.is_file() else None,
            "prompt": str(asset.meta.get("prompt") or ""),
            "model": str(asset.meta.get("model") or ""),
            "reference": kind if kind in (*REFERENCES, REFERENCE_ROLE) else "",
            "created_at": asset.created_at.isoformat(timespec="seconds"),
            "width": width, "height": height,
        })
    return rows


def _jobs(project: str, key: str, state: JobState) -> list[Job]:
    """The card's generation jobs in a given state."""
    step = f"{STEP_PREFIX}{key}"
    return [job for job in space(project).db.list_jobs(project=project, state=state,
                                                       limit=JOB_SCAN)
            if job.step == step]


def _pending(project: str, key: str) -> int:
    """The awaited images: the sum of `count` over queued or running generations.

    An image, not a job: the workbench shows one waiting slot per image. A job
    that failed and will be retried is still queued -- it counts.
    """
    return sum(max(1, int(job.payload.get("count") or 1))
               for state in (JobState.PENDING, JobState.RUNNING)
               for job in _jobs(project, key, state))


def _failures(project: str, key: str) -> list[dict[str, Any]]:
    """The card's recently failed generations, newest first.

    Without them, a failing generation would vanish from the workbench without
    a trace: the waiting slot goes, and no image comes.
    """
    since = datetime.now(UTC) - FAILURE_WINDOW
    found = []
    for job in _jobs(project, key, JobState.FAILED):
        at = job.updated_at if job.updated_at.tzinfo else job.updated_at.replace(tzinfo=UTC)
        if at >= since:
            found.append((at, job))
    found.sort(key=lambda entry: entry[0], reverse=True)
    return [{"job": job.id, "error": job.error, "at": at.isoformat(timespec="seconds")}
            for at, job in found[:MAX_FAILURES]]


# ------------------------------------------------------- the prompt seed

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_CODE = re.compile(r"`+([^`]*)`+")
_STARS = re.compile(r"(\*\*|\*)(?=\S)(.+?)(?<=\S)\1")
# An underscore is emphasis only at a word boundary: `snake_case` is not.
_UNDERSCORES = re.compile(r"(?<!\w)(__|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_HEADING = re.compile(r"^#{1,6}(\s|$)")
_RULE = re.compile(r"^([-*_])(\s*\1){2,}$")
_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])(?:\s+(?:\[[ xX]\]\s*)?(.*))?$")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def _plain(value: str) -> str:
    """A Markdown line without its formatting: what it says, in plain text."""
    value = _LINK.sub(r"\1", _IMAGE.sub("", value))
    value = _CODE.sub(r"\1", value)
    for _ in range(2):  # `***bold and italic***`: two nested emphases
        value = _UNDERSCORES.sub(r"\2", _STARS.sub(r"\2", value))
    return re.sub(r"\s+", " ", _TAG.sub("", value)).strip()


def _opening(lines: list[str]) -> str:
    """What the card says first: its first sentence, or its first bullets.

    Headings, code blocks, tables and rules tell an image model nothing;
    neither does a bullet without a value ("**When**:" of a template not filled
    in yet). A paragraph announcing a list ("It shows:") is read with it.
    """
    paragraph: list[str] = []
    items: list[str] = []
    fenced = False
    for raw in lines:
        line = raw.strip()
        if line.startswith(("```", "~~~")):
            if paragraph or items:
                break
            fenced = not fenced
            continue
        if fenced:
            continue
        announcing = bool(paragraph) and paragraph[-1].endswith(":")
        if not line:
            if items or (paragraph and not announcing):
                break
            continue
        if _HEADING.match(line) or _RULE.match(line) or line.startswith("|"):
            if paragraph or items:
                break
            continue
        item = _ITEM.match(line)
        if item:
            if paragraph and not announcing:
                break
            value = _plain(item.group(1) or "").rstrip(" .;,")
            # "**When**:", "**Beyond the genre** —", "**…** — why": a template
            # not filled in yet.
            blank = value.endswith((":", "—", "\u2013")) or value.startswith("…")
            if value and not blank:
                items.append(value)
                if len(items) >= SEED_ITEMS:
                    break
            continue
        if items:
            break
        value = _plain(line.lstrip("> "))
        if value:
            paragraph.append(value)
    text = " ".join(paragraph)
    if items:
        listed = "; ".join(items)
        return f"{text} {listed}" if text else listed
    return _SENTENCE.split(text, maxsplit=1)[0] if text else ""


def _bounded(value: str, limit: int = SEED_LENGTH) -> str:
    """At most `limit` characters, cut between two words."""
    if len(value) <= limit:
        return value
    cut = value[:limit - 1]
    space_at = cut.rfind(" ")
    if space_at > limit // 2:
        cut = cut[:space_at]
    return cut.rstrip(" ,;:.") + "…"


def prompt_seed(text: str, title: str) -> str:
    """A card's prompt seed: its title, and what it says first.

    Formatting is stripped -- links, bold, code, images, template comments: an
    image model has no use for a `<!-- ... -->` or a `**`. It is a seed the
    user completes: 300 characters at most.
    """
    head = _plain(title) or title.strip()
    body = _opening(_COMMENT.sub("", text).splitlines())
    if not body:
        return _bounded(head)
    if not head:
        return _bounded(body)
    joint = " " if head.endswith((".", "!", "?", "…", ":")) else ". "
    return _bounded(f"{head}{joint}{body}")


# ------------------------------------------------------------------ operations


def media(project: str, folder: str, name: str) -> dict[str, Any]:
    """A card's render, sketch and generated images (see `CardMedia`)."""
    folder, card = _card(project, folder, name)
    name = card["name"]
    key = f"{folder}/{name}"
    assets = space(project).db.list_assets(kind="image", limit=SCAN_LIMIT)
    rendered = _latest_render(project, folder, name, assets)
    return {
        "project": project,
        "folder": folder,
        "name": name,
        "title": card["title"],
        "section_label": section_label(project, folder),
        "render": (_render_entry(project, rendered, _render_path(project, folder, rendered))
                   if rendered is not None else None),
        "sketch": _sketch(project, folder, name),
        "references": _references(project, folder, name),
        "generations": _generations(project, key, assets),
        "pending": _pending(project, key),
        "failures": _failures(project, key),
        "prompt_seed": prompt_seed(card["text"], card["title"]),
    }


def read_sketch(project: str, folder: str, name: str) -> dict[str, Any]:
    """A card's Excalidraw scene, as saved, or None.

    An unreadable file is refused, not replaced by a blank page: a sketch saved
    over it would erase what history can still give back.
    """
    folder, card = _card(project, folder, name)
    scene_path, png_path = _sketch_files(project, folder, card["name"])
    scene: Any = None
    if scene_path.is_file():
        try:
            scene = json.loads(scene_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError(f"unreadable sketch: {scene_path} ({exc})") from exc
        if not isinstance(scene, dict):
            raise ServiceError(f"unreadable sketch: {scene_path} is not a scene")
    return {**_sketch_entry(scene_path, png_path), "scene": scene}


def save_sketch(project: str, folder: str, name: str, scene: dict[str, Any] | None,
                png: str | None) -> dict[str, Any]:
    """Save the scene and its PNG export (data URL); `None` removes them.

    `png=None` removes the export only (an empty sketch has nothing to show);
    `scene=None` removes both. Everything is checked before anything is
    written: a refused export leaves the previous sketch intact.
    """
    folder, card = _card(project, folder, name)
    scene_path, png_path = _sketch_files(project, folder, card["name"])
    if scene is None:
        scene_path.unlink(missing_ok=True)
        png_path.unlink(missing_ok=True)
        return _sketch_entry(scene_path, png_path)
    text = _scene_text(scene)
    image = _png_bytes(png) if png and png.strip() else None
    _write_atomic(scene_path, text.encode("utf-8"))
    if image is None:
        png_path.unlink(missing_ok=True)
    else:
        _write_atomic(png_path, image)
    return _sketch_entry(scene_path, png_path)


def rerender(project: str, folder: str, name: str) -> dict[str, Any]:
    """Redo the render of the card's scene, with the last one's settings.

    Same scene, setup, scale, framing and language: the image shows the game as
    it has become. Free and local (`renders.render_scene`).
    """
    folder, card = _card(project, folder, name)
    name = card["name"]
    previous = _latest_render(project, folder, name)
    if previous is None:
        raise NotFound(f"no render for “{card['title']}”: make one first (render_scene, "
                       f"name=\"{name}\", folder=\"{folder}\")")
    meta = previous.meta
    if not meta.get("scene"):
        raise ServiceError("the last render does not say which scene it shows: redo it with "
                           "render_scene")
    if meta.get("setup") and not meta.get("setup_code"):
        raise ServiceError("the last render had a setup the studio did not keep: redo it with "
                           "render_scene and its `setup`")
    result = renders.render_scene(
        project, str(meta["scene"]), name=name, folder=folder,
        scale=float(meta.get("scale") or 2.0),
        width=int(meta.get("width") or 0), height=int(meta.get("height") or 0),
        delay=float(meta.get("delay", 0.5)), crop=bool(meta.get("crop")),
        transparent=bool(meta.get("transparent")), locale=str(meta.get("locale") or ""),
        setup=str(meta.get("setup_code") or ""), godot=_godot(meta))
    asset = space(project).db.get_asset(result["asset_id"])
    if asset is None:  # pragma: no cover - render_scene just saved it
        raise ServiceError(f"render not found afterwards: {result['asset_id']}")
    return _render_entry(project, asset, Path(result["path"]))


def _stored(project: str, key: str, image: Path, role: str) -> Asset:
    """A card image (the sketch export, a reference), entered into the store to
    be a generation's starting point."""
    st = space(project)
    asset = st.store.put_file(image, kind="image",
                              meta={"role": role, "project": project, "card": key})
    held = st.db.get_asset(asset.id)
    if held is not None:
        # The same sketch, already in: the store is content-addressed, and
        # rewriting the row would erase what else it said.
        return held
    return st.db.save_asset(asset)


def _has_recipe(project: str) -> bool:
    """Whether the project has a recipe, hence a style to apply."""
    try:
        produce.resolve_recipe(project)
    except ServiceError:
        return False
    return True


def generate(project: str, folder: str, name: str, *, prompt: str, model: str | None = None,
             reference: str = "", strength: float = 0.6, width: int = 768,
             height: int = 1344, count: int = 1, negative_prompt: str = "",
             style: bool = True, confirm: bool = False) -> dict[str, Any]:
    """PAID: generate images for the card, from a text or a reference.

    `reference`: "" (text only), "sketch" (the sketch export), "render" (the
    game's current render) or "ref:<file>" (one of the card's references);
    `strength` says how far the image departs from it. `style` applies the
    project's when it has a recipe.

    Everything that can be refused is refused before consent: a request for
    consent followed by a refusal would waste the question. Nothing is written
    without it.
    """
    folder, card = _card(project, folder, name)
    name = card["name"]
    text = prompt.strip()
    if not text:
        raise ServiceError("empty prompt: say what the image must show")
    if not 1 <= count <= MAX_COUNT:
        raise ServiceError(f"count must be between 1 and {MAX_COUNT}")
    picked = None
    if reference.startswith(REF_PREFIX):
        picked = _reference_path(project, folder, name, reference.removeprefix(REF_PREFIX))
    elif reference not in REFERENCES:
        raise ServiceError(f"unknown reference: {reference} (expected: empty, “sketch”, “render” "
                           "or “ref:<file>”)")
    if reference and not 0.0 <= strength <= 1.0:
        raise ServiceError("strength must be between 0 and 1")
    rendered = None
    _, sketch_png = _sketch_files(project, folder, name)
    if reference == "render":
        rendered = _latest_render(project, folder, name)
        if rendered is None:
            raise NotFound(f"no render for “{card['title']}”: make one first")
    elif reference == "sketch" and not sketch_png.is_file():
        raise NotFound(f"the sketch of “{card['title']}” has no export: save it first")
    if not confirm:
        raise PaymentRequired("paid generation (~$0.006 per image with FLUX dev): call again with "
                              "confirm=true after the user agrees")

    key = f"{folder}/{name}"
    if picked is not None:
        source = _stored(project, key, picked, REFERENCE_ROLE)
    elif rendered is not None:
        source = rendered
    else:
        source = _stored(project, key, sketch_png, SKETCH_ROLE) if reference == "sketch" else None
    reference_id = source.id if source is not None else None
    recipe = project if style and _has_recipe(project) else None
    queued = produce.generate_image(
        text, model=model or None, negative_prompt=negative_prompt, recipe=recipe,
        reference_asset_id=reference_id, strength=strength, width=width, height=height,
        count=count, project=project, card=key,
        reference=REFERENCE_ROLE if picked is not None else reference,
        # Consent was given above, on this generation's amount.
        confirm=True)
    return {**queued, "reference_asset_id": reference_id}
