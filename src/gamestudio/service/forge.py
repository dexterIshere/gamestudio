"""The icon forge: create an icon, a whole set, or redo one -- all the way into the game.

Three requests, one path, the same for the page and for an agent:

- `one`: a new icon in a family (a game folder);
- `set`: several icons at once, on a sheet in a strict grid, split afterwards;
- `redo`: a game icon, redrawn from itself.

The path: **request** (paid: Runware, confirmed with its amount) ->
**proposals** (the generation goes through the project queue) -> for a set,
**split** of the chosen sheet (local, free) -> **adoption**: local matting
(BiRefNet), scaling to the family, writing into the game folder. A replaced
icon goes to the project's trash (`service/trash.py`): nothing is lost.

What makes a new icon look like its neighbours, rather than like the model's
default drawing:

- **the reference**: a family icon is the starting image (img2img) -- it
  carries the rendering, the palette, the lighting; the prompt carries the
  subject. For a set, it is repeated in each grid cell: the sheet is born in a
  grid, each cell in the family's style;
- **the family's written style**, kept from one request to the next
  (`.gamestudio/documents/showcase/styles.json`, versioned with the project);
- **the scale**: the forge measures the family -- canvas size, margin around
  the drawing, or drawing size when the family is cropped -- and brings each
  adopted icon to it.

Everything lives in `.gamestudio/workspace/forge/<request>/` (`request.json`,
a sheet's pieces); the generated images are project assets.
"""

from __future__ import annotations

import io
import json
import math
import re
import statistics
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from ..runware import catalog as models
from ..store.folders import project_paths
from . import documents, images, produce, prompts, showcase, trash
from .context import space, studio
from .errors import NotFound, PaymentRequired, ServiceError

MODES = ("one", "set", "redo")
REFERENCES = ("family", "element", "none")
FOLDER = "forge"
STYLES_FILE = "styles.json"
# The price of a one-megapixel image, as the interface announces it
# (`app/src/lib/catalog.ts`): the forge tells the agent before it pays.
PRICES = {models.FLUX_DEV: 0.006, models.FLUX_SCHNELL: 0.0013, models.FLUX_KONTEXT: 0.04}
MAX_NAMES = 16
MAX_VARIANTS = 4
MAX_SHEETS = 2
# The background the prompts ask for, on which a reference is laid.
BACKGROUND = (217, 217, 217)
# A family with no measurable icon: a square canvas, a small margin.
DEFAULT_CANVAS = 256
DEFAULT_MARGIN = 0.06
# A family of SVGs is hard to measure without rasterizing them: aim for four
# times their size, which Godot scales down cleanly.
SVG_SCALE = 4
# A reference's drawing in its starting image: large enough to carry the
# rendering, small enough to leave the margin the prompts ask for.
REFERENCE_FILL = 0.72
RASTER = {".png", ".webp", ".jpg", ".jpeg"}
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# ------------------------------------------------------------------ helpers


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _work(project: str) -> Path:
    return project_paths(studio().settings, project).workspace / FOLDER


def _name(value: str) -> str:
    """An icon name as the game writes them: lowercase, digits, `_` and `-`."""
    folded = documents.slug(value.strip()).replace("-", "_")
    name = re.sub(r"_+", "_", folded).strip("_")
    if not _NAME.match(name):
        raise ServiceError(f"invalid icon name: “{value}”")
    return name


def _human(name: str) -> str:
    return name.replace("_", " ").replace("-", " ")


def estimate(model: str, count: int, width: int, height: int) -> float:
    """What a request costs, at the per-megapixel price."""
    price = PRICES.get(model)
    if price is None:
        raise ServiceError(f"model not offered by the forge: {model} (known: {', '.join(PRICES)})")
    return round(price * count * max(1.0, width * height / (1024 * 1024)), 4)


def _matte(image: Image.Image) -> Image.Image:
    """Matte a proposal locally (BiRefNet): the flat background goes, the icon stays."""
    from ..vision.detect import ToolUnavailable, remove_background

    try:
        return remove_background(image)
    except ToolUnavailable as exc:
        raise ServiceError(f"local cut-out unavailable: {exc}") from exc


# ------------------------------------------------------------- the family


def _styles_path(project: str) -> Path:
    return documents.directory(project) / "showcase" / STYLES_FILE


def _styles(project: str) -> dict[str, str]:
    path = _styles_path(project)
    if not path.is_file():
        return {}
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(key): str(value) for key, value in found.items()} if isinstance(found, dict) \
        else {}


def _keep_style(project: str, folder: str, style: str) -> None:
    styles = _styles(project)
    if styles.get(folder) == style:
        return
    styles[folder] = style
    path = _styles_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(styles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _content_ratio(image: Image.Image) -> float | None:
    """The share of the canvas the drawing takes (its longest side), from the alpha."""
    if image.mode != "RGBA":
        return None
    box = image.getchannel("A").point(lambda value: 255 if value > 8 else 0).getbbox()
    if box is None:
        return None
    return max(box[2] - box[0], box[3] - box[1]) / max(image.size)


def profile(project: str, folder: str, game: dict[str, Any] | None = None,
            root: Path | None = None) -> dict[str, Any]:
    """How a family's icons are made, so that the next one looks like them.

    `mode`: `framed` (a common canvas, a margin around the drawing) or `cropped`
    (each icon cropped to its drawing, at a common size). `exemplar`: the most
    typical icon, used as the reference. `style`: the family's written style,
    kept from the previous request.
    """
    if game is None or root is None:
        root, game = showcase._game(project)
    members = [icon for icon in game["icons"] if icon["folder"] == folder]
    measured = []
    for icon in members:
        if Path(icon["path"]).suffix.lower() not in RASTER:
            continue
        try:
            with Image.open(root / icon["path"]) as opened:
                image = opened.convert("RGBA")
        except OSError:
            continue
        measured.append((icon, image.size, _content_ratio(image)))

    found: dict[str, Any] = {"folder": folder, "count": len(members), "format": "png",
                             "style": _styles(project).get(folder, "")}
    if measured:
        sizes = Counter(size for _, size, _ in measured)
        (common, seen) = sizes.most_common(1)[0]
        ratios = [ratio for _, _, ratio in measured if ratio]
        typical = statistics.median(ratios) if ratios else 1 - 2 * DEFAULT_MARGIN
        if common[0] == common[1] and seen >= 0.6 * len(measured):
            found.update(mode="framed", width=common[0], height=common[1],
                         margin=round(min(0.3, max(0.0, (1 - typical) / 2)), 3))
        else:
            side = int(statistics.median(max(size) for _, size, _ in measured))
            found.update(mode="cropped", width=side, height=side, margin=0.0)
        exemplar = min(measured, key=lambda entry: abs((entry[2] or typical) - typical))
        found["exemplar"] = exemplar[0]["path"]
    elif members:
        width, height = (int(float(v)) for v in (members[0]["size"] or "64x64").split("x"))
        found.update(mode="framed", width=max(DEFAULT_CANVAS, width * SVG_SCALE),
                     height=max(DEFAULT_CANVAS, height * SVG_SCALE), margin=DEFAULT_MARGIN,
                     exemplar=members[0]["path"])
    else:
        found.update(mode="framed", width=DEFAULT_CANVAS, height=DEFAULT_CANVAS,
                     margin=DEFAULT_MARGIN, exemplar="")
    return found


def fit(image: Image.Image, shape: dict[str, Any]) -> Image.Image:
    """Bring a matted icon to its family's scale (`profile`)."""
    image = image.convert("RGBA")
    box = image.getchannel("A").point(lambda value: 255 if value > 8 else 0).getbbox()
    if box is None:
        raise ServiceError("the cut-out image is empty: nothing to keep")
    drawn = image.crop(box)
    if shape["mode"] == "cropped":
        scale = shape["width"] / max(drawn.size)
        return drawn.resize((max(1, round(drawn.width * scale)),
                             max(1, round(drawn.height * scale))), Image.LANCZOS)
    width, height = shape["width"], shape["height"]
    inner = (1 - 2 * float(shape["margin"])) * min(width, height)
    scale = inner / max(drawn.size)
    drawn = drawn.resize((max(1, round(drawn.width * scale)),
                          max(1, round(drawn.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(drawn, ((width - drawn.width) // 2, (height - drawn.height) // 2))
    return canvas


# -------------------------------------------------------------- the request


def _grid(count: int) -> tuple[int, int]:
    columns = math.ceil(math.sqrt(count))
    return columns, math.ceil(count / columns)


def _load_icon(path: Path) -> Image.Image | None:
    """A game icon as an image: a raster as is, an SVG rasterized when possible."""
    try:
        if path.suffix.lower() == ".svg":
            return Image.open(io.BytesIO(images.render_png(path, 512))).convert("RGBA")
        with Image.open(path) as opened:
            return opened.convert("RGBA")
    except (OSError, ServiceError):
        return None


def _seed(icon: Image.Image, width: int, height: int, cells: tuple[int, int]) -> bytes:
    """The starting image: the reference icon on the prompts' background, one per cell."""
    canvas = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
    columns, rows = cells
    cell_w, cell_h = width // columns, height // rows
    drawn = icon.crop(icon.getbbox() or (0, 0, *icon.size))
    scale = REFERENCE_FILL * min(cell_w, cell_h) / max(drawn.size)
    drawn = drawn.resize((max(1, round(drawn.width * scale)),
                          max(1, round(drawn.height * scale))), Image.LANCZOS)
    for row in range(rows):
        for column in range(columns):
            canvas.alpha_composite(drawn, (column * cell_w + (cell_w - drawn.width) // 2,
                                           row * cell_h + (cell_h - drawn.height) // 2))
    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


def _path(project: str, request_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", request_id or ""):
        raise NotFound(f"request not found: {request_id}")
    return _work(project) / request_id / "request.json"


def _load(project: str, request_id: str) -> dict[str, Any]:
    path = _path(project, request_id)
    if not path.is_file():
        raise NotFound(f"request not found: {request_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(project: str, demand: dict[str, Any]) -> None:
    path = _path(project, demand["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(demand, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request(project: str, *, mode: str, folder: str = "", names: list[str] | None = None,
            description: str = "", style: str | None = None, element: str = "",
            model: str | None = None, count: int | None = None,
            reference: str | None = None, strength: float | None = None,
            confirm: bool = False) -> dict[str, Any]:
    """Order one or more icons from Runware. PAID: refused without `confirm`.

    - `one`: `folder` (the family, from the game root), `names` (one name),
      `description` (what the icon shows), `count` variants (1 to 4);
    - `set`: `folder`, `names` (2 to 16, in the sheet's reading order),
      `count` sheets (1 or 2);
    - `redo`: `element` (the showcase icon), `description` (what changes),
      `count` variants.

    `reference`: `family` (a family icon as the starting image, the default for
    a new icon), `element` (the icon itself, the default for `redo`), or
    `none`. `strength`: how far the generation departs from the reference
    (0.85 from the family, 0.55 to redo). `style`: the family's written style;
    without it, the previous request's.
    """
    if mode not in MODES:
        raise ServiceError(f"unknown request: {mode} (expected: {', '.join(MODES)})")
    root, game = showcase._game(project)
    item: dict[str, Any] | None = None
    if mode == "redo":
        item = showcase.element(project, "icons", element)
        folder = Path(item["file"]).parent.as_posix()
        names = [Path(item["file"]).stem]
    folder = folder.strip().strip("/")
    if not folder:
        raise ServiceError("missing family: the game folder where the icon goes")
    base = (root / folder).resolve()
    if not base.is_relative_to(root.resolve()) or ".gamestudio" in base.parts:
        raise ServiceError(f"folder outside the game: {folder}")
    if not any(entry["path"] == "" or folder == entry["path"]
               or folder.startswith(entry["path"] + "/") for entry in game["godot"]):
        raise ServiceError(f"{folder} is in none of the game's Godot projects")

    wanted = [_name(name) for name in (names or []) if name.strip()]
    if len(set(wanted)) < len(wanted):
        raise ServiceError("two icons have the same name")
    if mode == "set" and not 2 <= len(wanted) <= MAX_NAMES:
        raise ServiceError(f"an icon set has 2 to {MAX_NAMES} icons")
    if mode != "set" and len(wanted) != 1:
        raise ServiceError("one icon, one name")
    if mode != "redo":
        taken = [name for name in wanted
                 if base.is_dir() and any(path.stem == name for path in base.iterdir()
                                          if path.is_file())]
        if taken:
            raise ServiceError(f"already in {folder}: {', '.join(taken)} (to redraw it: “redo”)")

    shape = profile(project, folder, game, root)
    model = model or models.FLUX_DEV
    if mode == "set":
        cells = _grid(len(wanted))
        count = count or 1
        if not 1 <= count <= MAX_SHEETS:
            raise ServiceError(f"1 to {MAX_SHEETS} sheets")
        side = 1024 if len(wanted) <= 9 else 1536
    else:
        cells = (1, 1)
        count = count or 2
        if not 1 <= count <= MAX_VARIANTS:
            raise ServiceError(f"1 to {MAX_VARIANTS} variants")
        side = 1024
    cost = estimate(model, count, side, side)
    if not confirm:
        raise PaymentRequired(f"paid operation (~${cost:.3f}): call again with confirm=true after "
                              "the user agrees")

    style = shape["style"] if style is None else style.strip()
    reference = reference or ("element" if mode == "redo" else "family")
    if reference not in REFERENCES:
        raise ServiceError(f"unknown reference: {reference} (expected: {', '.join(REFERENCES)})")
    notes: list[str] = []
    seed_id = None
    source = ""
    if reference == "element" and item is not None:
        source = item["file"]
    elif reference == "family":
        source = shape.get("exemplar", "")
    if source:
        icon = _load_icon(root / source)
        if icon is None:
            notes.append(f"unreadable reference ({source}): generated without it")
        else:
            asset = space(project).store.put_bytes(
                _seed(icon, side, side, cells), ".png", kind="image",
                meta={"role": "forge-reference", "project": project, "source": source})
            space(project).db.save_asset(asset)
            seed_id = asset.id
    if strength is None:
        strength = 0.55 if mode == "redo" else 0.85

    if mode == "set":
        columns, rows = cells
        listed = "; ".join(f"{index + 1}. {_human(name)}" for index, name in enumerate(wanted))
        subject = (f"{columns} columns and {rows} rows, icons in reading order: {listed}"
                   + (f", {description.strip()}" if description.strip() else ""))
        template = "icon-set"
    else:
        subject = description.strip() or _human(wanted[0])
        if mode == "redo" and description.strip():
            subject = f"{_human(wanted[0])}, {description.strip()}"
        template = "icon"
    composed = prompts.render(template, subject, style_prefix=style)

    demand_id = uuid.uuid4().hex[:12]
    queued = produce.generate_image(
        composed["positive"], model=model, negative_prompt=composed["negative"],
        reference_asset_id=seed_id, strength=float(strength), width=side, height=side,
        count=count, project=project, forge=demand_id,
        # Consent was given above, on this request's amount.
        confirm=True)
    demand = {
        "id": demand_id, "mode": mode, "folder": folder, "names": wanted,
        "description": description.strip(), "style": style, "element": element or "",
        "element_file": item["file"] if item else "", "model": model, "count": count,
        "width": side, "height": side, "grid": list(cells), "reference": reference,
        "reference_source": source if seed_id else "", "strength": float(strength),
        "prompt": composed["positive"], "negative": composed["negative"],
        "job": queued["queued"][0], "estimate_usd": cost, "profile": shape,
        "notes": notes, "split": None, "adopted": [], "closed": False, "created_at": _now(),
    }
    _save(project, demand)
    if style:
        _keep_style(project, folder, style)
    return _view(project, demand)


# --------------------------------------------------------- proposals


def _candidates(project: str, demand_id: str) -> list[dict[str, Any]]:
    st = space(project)
    found = []
    for asset in st.db.list_assets(kind="image", limit=500):
        if asset.meta.get("forge") != demand_id:
            continue
        path = st.store.path_for(asset.id)
        if path is None:
            continue
        found.append({"asset_id": asset.id, "path": str(path)})
    return sorted(found, key=lambda entry: entry["asset_id"])


def _view(project: str, demand: dict[str, Any]) -> dict[str, Any]:
    job = space(project).db.get_job(demand["job"]) if demand.get("job") else None
    candidates = _candidates(project, demand["id"])
    state = job.state.value if job else "done"
    status = ("failed" if state == "failed" else "running" if state in ("pending", "running")
              else "adopted" if demand["adopted"] else "split" if demand["split"]
              else "ready" if candidates else "empty")
    return {**{key: value for key, value in demand.items() if key != "profile"},
            "profile": demand["profile"], "status": status,
            "error": job.error if job and state == "failed" else "",
            "candidates": candidates}


def requests(project: str, *, closed: bool = False) -> list[dict[str, Any]]:
    """The forge's requests, newest first; closed ones too with `closed`."""
    folder = _work(project)
    found = []
    if folder.is_dir():
        for path in folder.glob("*/request.json"):
            try:
                demand = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if demand.get("closed") and not closed:
                continue
            found.append(_view(project, demand))
    return sorted(found, key=lambda entry: entry["created_at"], reverse=True)


def detail(project: str, request_id: str) -> dict[str, Any]:
    return _view(project, _load(project, request_id))


def close(project: str, request_id: str) -> dict[str, Any]:
    """Close a request: it leaves the page, its images stay in the project."""
    demand = _load(project, request_id)
    demand["closed"] = True
    _save(project, demand)
    return _view(project, demand)


def _candidate(project: str, demand: dict[str, Any], asset_id: str) -> Path:
    for entry in _candidates(project, demand["id"]):
        if entry["asset_id"] == asset_id:
            return Path(entry["path"])
    raise NotFound(f"proposal not found in the request: {asset_id}")


def split(project: str, request_id: str, asset_id: str) -> dict[str, Any]:
    """Split the chosen sheet of an icon set, one icon per cell. Free, local.

    The request's grid comes first; if it does not yield as many icons as
    names, splitting by shapes replaces it when that count is right. Each piece
    takes its cell's name, in reading order.
    """
    from ..sheet.bitmap import split_bitmap

    demand = _load(project, request_id)
    if demand["mode"] != "set":
        raise ServiceError("only a sheet can be split")
    sheet = _candidate(project, demand, asset_id)
    columns, rows = demand["grid"]
    names = demand["names"]
    result = split_bitmap(sheet, strategy="grid", rows=rows, columns=columns, trim=True,
                          matting=MATTING)
    if len(result.pieces) != len(names):
        loose = split_bitmap(sheet, strategy="blobs", trim=True, matting=MATTING)
        if len(loose.pieces) == len(names):
            result = loose
    target = _work(project) / request_id / "split" / asset_id[:12]
    target.mkdir(parents=True, exist_ok=True)
    pieces = []
    for index, piece in enumerate(result.pieces):
        path = target / f"{index:02d}.png"
        path.write_bytes(piece.data)
        pieces.append({"index": index, "name": names[index] if index < len(names)
                       else f"icon_{index + 1}", "path": str(path),
                       "width": round(piece.width), "height": round(piece.height)})
    # What the split says about itself (background removed, grid recognized)
    # goes without saying here; only an empty cell or a wrong count needs a look.
    warnings = [warning for warning in result.warnings if "empty" in warning]
    if len(pieces) != len(names):
        warnings.append(f"{len(pieces)} piece(s) for {len(names)} name(s): check the names "
                        "before adopting")
    demand["split"] = {"asset_id": asset_id, "pieces": pieces, "warnings": warnings}
    _save(project, demand)
    return _view(project, demand)


# Local matting during the split: a flat background comes off by color, and the
# model catches the edges color alone would leave.
MATTING = True


# ----------------------------------------------------------------- adoption


def adopt(project: str, request_id: str, picks: list[dict[str, Any]]) -> dict[str, Any]:
    """Write the chosen proposals into the game folder. Free, local.

    `picks`: `[{"source": "asset:<id>" | "piece:<n>", "name": "<name>"}]`. A
    proposal is matted locally, a sheet piece already is; then each is scaled
    to its family and written as PNG (`<family>/<name>.png`). For `redo`, the
    old icon goes to the trash and the new one takes its place -- except an
    SVG: the new one is written next to it as PNG, and what cited the SVG needs
    updating.
    """
    demand = _load(project, request_id)
    if not picks:
        raise ServiceError("no proposal chosen")
    root, game = showcase._game(project)
    shape = profile(project, demand["folder"], game, root)
    base = root / demand["folder"]
    prepared: list[tuple[Path, Image.Image, str]] = []
    notes: list[str] = []
    for pick in picks:
        source = str(pick.get("source", ""))
        if source.startswith("asset:"):
            with Image.open(_candidate(project, demand, source[6:])) as opened:
                image = _matte(opened.convert("RGB"))
        elif source.startswith("piece:") and demand["split"]:
            index = int(source[6:])
            piece = next((entry for entry in demand["split"]["pieces"]
                          if entry["index"] == index), None)
            if piece is None:
                raise NotFound(f"piece not found: {index}")
            with Image.open(piece["path"]) as opened:
                image = opened.convert("RGBA")
        else:
            raise ServiceError(f"unknown proposal: {source}")
        name = _name(str(pick.get("name") or ""))
        if demand["mode"] == "redo":
            old = root / demand["element_file"]
            target = old if old.suffix.lower() in (".png", ".webp") else old.with_suffix(".png")
            if target != old:
                notes.append(f"{demand['element_file']} is an SVG: the new icon is written "
                             f"next to it ({target.relative_to(root).as_posix()}); what "
                             "cites the SVG needs updating")
        else:
            target = base / f"{name}.png"
            if target.exists():
                raise ServiceError(f"already in the game: {target.relative_to(root).as_posix()}")
        prepared.append((target, fit(image, shape), name))
    if len({target for target, _, _ in prepared}) < len(prepared):
        raise ServiceError("two icons would go to the same file")

    batch = None
    replaced = [target.relative_to(root).as_posix() for target, _, _ in prepared
                if target.exists()]
    if replaced:
        batch = trash.discard(project, replaced, f"replaced by the forge ({demand['id']})")
    written = []
    for target, image, name in prepared:
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, "WEBP" if target.suffix.lower() == ".webp" else "PNG")
        relative = target.relative_to(root).as_posix()
        written.append(relative)
        demand["adopted"].append({"name": name, "file": relative, "at": _now()})
    _save(project, demand)
    if written:
        notes.append("Godot imports the new files the next time the project is opened")
    return {**_view(project, demand), "written": written, "notes": notes,
            "batch": batch["id"] if batch else None}


def image_file(project: str, request_id: str, *, asset: str = "", piece: int | None = None) -> Path:
    """The image of a proposal or a sheet piece, for the page."""
    demand = _load(project, request_id)
    if asset:
        return _candidate(project, demand, asset)
    if piece is not None and demand["split"]:
        for entry in demand["split"]["pieces"]:
            if entry["index"] == piece:
                return Path(entry["path"])
    raise NotFound("image not found in the request")


def families(project: str) -> list[dict[str, Any]]:
    """The game's icon families, with what a new icon must borrow from them."""
    root, game = showcase._game(project)
    folders = sorted({icon["folder"] for icon in game["icons"]})
    return [{"folder": folder, "label": showcase._family(folder),
             "profile": profile(project, folder, game, root)} for folder in folders]
