"""The art direction's leading aspects: graphic style and game type, their cards and influences.

Two aspects lead the art direction, before its colors, type and shaders: the
graphic style (how the game draws) and the game type (what game it is). Each
is made of parts, one card each in the art direction's shelf -- the graphic
style's `graphic-style`; the game type's `gameplay`, `setting` and `lore` --
and of a board of influences beside the Universe's state
(`lookdev/<aspect>.board.json`), summed up in writing in its own card
(`style-influences`, `game-influences`). An influence -- a work, a game, a
film, an artist, a movement, or a blend of others -- says what is kept from
it and what is left, with its images.

An image is a reference the user dropped (filed with the board's card, see
`cards.add_reference`) or one generated for that card. Generating is paid: an
agent proposes (`propose`: a prompt, a model, a count -- free), the user pays
the proposal in the Universe (`pay`, refused without consent and stating its
amount). An agent never pays a proposal. Once its job is done, a proposal's
images join its influence -- or, proposed for the aspect as a whole, only
its gallery, which holds every image generated for the aspect.

The board is written by two processes -- the studio's server, and the MCP
server of the agent working on it --: every change goes through a file lock.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..runware import catalog as air
from . import cards, documents, produce
from .context import space
from .errors import NotFound, PaymentRequired, ServiceError

try:
    import fcntl
except ImportError:  # pragma: no cover - the studio runs on Linux and macOS
    fcntl = None  # type: ignore[assignment]

FOLDER = "design/direction"
STATE_FOLDER = "lookdev"
# Each aspect: what it is about, its parts -- a card each, the template it
# starts from, its title until the user's words replace it --, and the card
# that sums its board up in writing (where its images are filed).
ASPECTS: dict[str, dict[str, Any]] = {
    "style": {
        "subject": "the game's graphic style",
        "parts": (
            {"id": "look", "card": "graphic-style", "template": "style", "title": "Graphic style",
             "about": "how the game draws: technique, shapes and proportions, outline, light, "
                      "matter and detail, camera"},
        ),
        "board": {"card": "style-influences", "title": "Graphic style influences"},
    },
    "game": {
        "subject": "the game type",
        "parts": (
            {"id": "gameplay", "card": "gameplay", "template": "gameplay",
             "title": "Gameplay style",
             "about": "the gameplay style: genre, loop, pace and sessions, alone or together, "
                      "platform, what sets it apart"},
            {"id": "setting", "card": "setting", "template": "setting", "title": "Setting",
             "about": "the world the game takes place in: place and time, scale, tone, who "
                      "lives there, its laws"},
            {"id": "lore", "card": "lore", "template": "lore", "title": "Lore",
             "about": "its story: origins, what happened, who is who, mysteries"},
        ),
        "board": {"card": "game-influences", "title": "Game influences"},
    },
}
BOARD_TEMPLATE = "influences"
KINDS = ("work", "game", "film", "book", "artist", "movement", "place", "blend", "other")
# What an image model is asked for to explore, and what it may be: the catalog
# the interface offers (`app/src/lib/catalog.ts`). Kontext only touches up an image.
DEFAULT_MODEL = air.FLUX_SCHNELL
MODELS = (air.FLUX_SCHNELL, air.FLUX_DEV, air.FLUX_KONTEXT)
DEFAULT_SIZE = (1024, 768)
SIZES = range(256, 2049)
MAX_INFLUENCES = 24
MAX_IMAGES = 24
MAX_PROPOSALS = 80
MAX_NAME = 60
MAX_TEXT = 600
MAX_PROMPT = 1500

# An image of the board: a generated one by its asset, a dropped one by its file.
ASSET = "asset:"
REF = cards.REF_PREFIX


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _aspect(aspect: str) -> dict[str, Any]:
    if aspect not in ASPECTS:
        raise NotFound(f"unknown aspect: {aspect} (known: {', '.join(ASPECTS)})")
    return ASPECTS[aspect]


def _file(project: str, aspect: str) -> Path:
    return documents.directory(project) / STATE_FOLDER / f"{aspect}.board.json"


@contextlib.contextmanager
def _board(project: str, aspect: str, *, write: bool = True) -> Iterator[dict[str, Any]]:
    """The board, held under the lock; written back when `write` and it changed."""
    _aspect(aspect)
    path = _file(project, aspect)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        board: dict[str, Any] = {"influences": [], "proposals": []}
        if path.is_file():
            try:
                board = {**board, **json.loads(path.read_text(encoding="utf-8"))}
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ServiceError(f"unreadable board: {path} ({exc})") from exc
        before = json.dumps(board, sort_keys=True)
        yield board
        if write and json.dumps(board, sort_keys=True) != before:
            board["proposals"] = board["proposals"][-MAX_PROPOSALS:]
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(board, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
            os.replace(temporary, path)


def _template(template: str, title: str) -> str:
    return documents.TEMPLATES[template]["body"].format(
        title=title, date=datetime.now().strftime("%Y-%m-%d"))


def _read(project: str, name: str) -> dict[str, Any] | None:
    try:
        return documents.read_document(project, name, FOLDER)
    except NotFound:
        return None


def ensure_card(project: str, aspect: str) -> dict[str, Any]:
    """The card of the aspect's board, created from its template the first time."""
    spec = _aspect(aspect)["board"]
    return _read(project, spec["card"]) or documents.write_document(
        project, spec["card"], _template(BOARD_TEMPLATE, spec["title"]), folder=FOLDER)


def _part(project: str, part: dict[str, str]) -> dict[str, Any]:
    """A part as the page shows it: its card, or the template it would start from."""
    card = _read(project, part["card"])
    template = _template(part["template"], part["title"])
    return {"id": part["id"], "name": part["card"], "folder": FOLDER,
            "title": card["title"] if card else part["title"],
            "path": card["path"] if card else "", "text": card["text"] if card else "",
            "template": template,
            "written": card is not None and card["text"].strip() != template.strip()}


def _text(value: str, limit: int, what: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        raise ServiceError(f"{what} too long: {len(text)} characters (at most {limit})")
    return text


def _influence(board: dict[str, Any], influence_id: str) -> dict[str, Any]:
    for entry in board["influences"]:
        if entry["id"] == influence_id:
            return entry
    known = ", ".join(entry["id"] for entry in board["influences"]) or "none yet"
    raise NotFound(f"influence not found: {influence_id} (known: {known})")


def _proposal(board: dict[str, Any], proposal_id: str) -> dict[str, Any]:
    for entry in board["proposals"]:
        if entry["id"] == proposal_id:
            return entry
    raise NotFound(f"proposal not found: {proposal_id}")


def _image(project: str, aspect: str, key: str) -> dict[str, Any] | None:
    """What the page and an agent know of an image of the board; None once it is gone."""
    if key.startswith(ASSET):
        asset_id = key.removeprefix(ASSET)
        st = space(project)
        path = st.store.path_for(asset_id)
        if path is None or not path.is_file():
            return None
        asset = st.db.get_asset(asset_id)
        meta = asset.meta if asset else {}
        return {"key": key, "kind": "generated", "asset_id": asset_id, "path": str(path),
                "prompt": str(meta.get("prompt") or ""), "model": str(meta.get("model") or "")}
    if key.startswith(REF):
        file = key.removeprefix(REF)
        path = cards.references_dir(project, FOLDER, _aspect(aspect)["board"]["card"]) / file
        if not path.is_file():
            return None
        return {"key": key, "kind": "reference", "file": file, "path": str(path)}
    return None


def _check_images(project: str, aspect: str, keys: list[str]) -> list[str]:
    found: list[str] = []
    for key in keys:
        if key in found:
            continue
        if _image(project, aspect, key) is None:
            raise NotFound(f"image not found: {key} (an `asset:<id>` generated for the card, "
                           "or a `ref:<file>` dropped with it)")
        found.append(key)
    if len(found) > MAX_IMAGES:
        raise ServiceError(f"{MAX_IMAGES} images at most per influence")
    return found


def _refresh(project: str, board: dict[str, Any]) -> None:
    """Paid proposals follow their job: once done, their images join their influence."""
    st = space(project)
    for proposal in board["proposals"]:
        if proposal["status"] != "queued" or not proposal.get("job"):
            continue
        job = st.db.get_job(proposal["job"])
        if job is None:
            continue
        if job.state.value == "failed":
            proposal.update(status="failed", error=job.error)
        elif job.state.value in ("done", "needs_review"):
            made = [f"{ASSET}{asset_id}" for asset_id in job.result.get("assets", [])]
            proposal.update(status="done", images=made)
            for entry in board["influences"]:
                if proposal["influence"] and entry["id"] == proposal["influence"]:
                    entry["images"] = [*entry["images"],
                                       *(key for key in made if key not in entry["images"])]
                    entry["images"] = entry["images"][-MAX_IMAGES:]


def _cost(proposal: dict[str, Any]) -> float | None:
    return produce.image_cost(proposal["model"], proposal["count"], proposal["width"],
                              proposal["height"])


def board(project: str, aspect: str) -> dict[str, Any]:
    """The aspect: its parts (their cards), its influences with their images, its proposals.

    Each image has its `path`, to look at it; each proposal its `cost_usd`;
    `card` is the board's own card, where its images are filed.
    """
    spec = _aspect(aspect)
    with _board(project, aspect) as held:
        _refresh(project, held)
        influences = [dict(entry) for entry in held["influences"]]
        proposals = [dict(entry) for entry in held["proposals"]]
    for entry in influences:
        entry["images"] = [image for image in (_image(project, aspect, key)
                                               for key in entry["images"]) if image]
    for proposal in proposals:
        proposal["cost_usd"] = _cost(proposal)
        proposal["images"] = [image for image in (_image(project, aspect, key)
                                                  for key in proposal.get("images", [])) if image]
    card = _read(project, spec["board"]["card"])
    # Every image generated for the aspect, the newest first, with what asked for it.
    gallery = [{**image, "proposal": proposal["id"], "influence": proposal["influence"]}
               for proposal in reversed(proposals) for image in proposal["images"]]
    return {"project": project, "aspect": aspect, "subject": spec["subject"],
            "parts": [_part(project, part) for part in spec["parts"]],
            "gallery": gallery,
            "card": {"name": spec["board"]["card"], "folder": FOLDER,
                     "title": card["title"] if card else spec["board"]["title"],
                     "path": card["path"] if card else "",
                     "text": card["text"] if card else ""},
            "influences": influences, "proposals": proposals}


def context(project: str) -> dict[str, Any]:
    """What an agent has in mind before it writes or proposes: every aspect's
    influences with the paths of their images, and its parts as written -- read
    without touching the board."""
    found: dict[str, Any] = {}
    for aspect, spec in ASPECTS.items():
        path = _file(project, aspect)
        try:
            held = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            held = {}
        found[aspect] = {
            "influences": [{**{key: entry.get(key) for key in ("id", "name", "kind", "keep",
                                                             "avoid", "of")},
                            "images": [image["path"] for image in (
                                _image(project, aspect, key) for key in entry.get("images", []))
                                if image]}
                           for entry in held.get("influences", [])],
            "parts": {part["id"]: _part(project, part) for part in spec["parts"]}}
    return found


def summary(project: str) -> dict[str, dict[str, Any]]:
    """Each aspect at a glance: its influences, and how many of its parts are written."""
    found: dict[str, dict[str, Any]] = {}
    for aspect, spec in ASPECTS.items():
        path = _file(project, aspect)
        try:
            held = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            held = {}
        found[aspect] = {"influences": len(held.get("influences", [])),
                         "written": sum(1 for part in spec["parts"]
                                        if _part(project, part)["written"]),
                         "parts": len(spec["parts"])}
    return found


def set_influence(project: str, aspect: str, *, name: str, influence: str = "",
                  kind: str = "", keep: str | None = None, avoid: str | None = None,
                  of: list[str] | None = None,
                  images: list[str] | None = None) -> dict[str, Any]:
    """Name an influence, or change one (`influence`: its id). Free.

    `keep` and `avoid` say what is kept from it and what is left; a blend
    (`kind="blend"`) crosses the influences `of` names; `images` replaces its
    images (`asset:<id>`, `ref:<file>`). What is not given is kept.
    """
    ensure_card(project, aspect)
    label = _text(name, MAX_NAME, "name")
    if not label:
        raise ServiceError("empty name: say which work, artist or movement it is")
    if kind and kind not in KINDS:
        raise ServiceError(f"unknown kind: {kind} (known: {', '.join(KINDS)})")
    checked = _check_images(project, aspect, images) if images is not None else None
    with _board(project, aspect) as held:
        if influence:
            entry = _influence(held, influence)
        else:
            base = documents.slug(label)[:40].strip("-") or "influence"
            taken = {item["id"] for item in held["influences"]}
            if len(taken) >= MAX_INFLUENCES:
                raise ServiceError(f"{MAX_INFLUENCES} influences at most: blend or remove some")
            wanted, n = base, 2
            while wanted in taken:
                wanted, n = f"{base}-{n}", n + 1
            entry = {"id": wanted, "name": label, "kind": kind or "other", "keep": "",
                     "avoid": "", "of": [], "images": [], "created_at": _now()}
            held["influences"].append(entry)
        if of is not None:
            missing = [item for item in of if item == entry["id"]
                       or item not in {other["id"] for other in held["influences"]}]
            if missing:
                raise NotFound(f"influence not found: {', '.join(missing)}")
            entry["of"] = list(dict.fromkeys(of))
        entry["name"] = label
        if kind:
            entry["kind"] = kind
        if keep is not None:
            entry["keep"] = _text(keep, MAX_TEXT, "keep")
        if avoid is not None:
            entry["avoid"] = _text(avoid, MAX_TEXT, "avoid")
        if checked is not None:
            entry["images"] = checked
        entry["updated_at"] = _now()
        influence_id = entry["id"]
    return next(item for item in board(project, aspect)["influences"]
                if item["id"] == influence_id)


def remove_influence(project: str, aspect: str, influence: str) -> dict[str, Any]:
    """Take an influence off the board. Its images stay with the card; blends forget it."""
    with _board(project, aspect) as held:
        _influence(held, influence)
        held["influences"] = [entry for entry in held["influences"] if entry["id"] != influence]
        for entry in held["influences"]:
            entry["of"] = [item for item in entry.get("of", []) if item != influence]
    return board(project, aspect)


def remove_image(project: str, aspect: str, influence: str, key: str) -> dict[str, Any]:
    """Take an image off an influence.

    A dropped image no other influence shows leaves the card's references
    too; a generated one stays in the library -- it was paid for, and the
    card's workbench still shows it.
    """
    with _board(project, aspect) as held:
        entry = _influence(held, influence)
        if key not in entry["images"]:
            raise NotFound(f"image not found: {key}")
        entry["images"] = [image for image in entry["images"] if image != key]
        orphan = key.startswith(REF) and not any(key in other["images"]
                                                  for other in held["influences"])
    if orphan and _image(project, aspect, key) is not None:
        cards.remove_reference(project, FOLDER, _aspect(aspect)["board"]["card"],
                               key.removeprefix(REF))
    return board(project, aspect)


def add_reference(project: str, aspect: str, influence: str, data: bytes,
                  filename: str = "") -> dict[str, Any]:
    """Drop an image for an influence: filed with the aspect's card, shown with the influence."""
    card = ensure_card(project, aspect)
    with _board(project, aspect, write=False) as held:
        _influence(held, influence)
    added = cards.add_reference(project, FOLDER, card["name"], data, filename)
    key = f"{REF}{added['file']}"
    with _board(project, aspect) as held:
        entry = _influence(held, influence)
        if key not in entry["images"]:
            if len(entry["images"]) >= MAX_IMAGES:
                raise ServiceError(f"{MAX_IMAGES} images at most per influence")
            entry["images"].append(key)
    return board(project, aspect)


def add_reference_file(project: str, aspect: str, influence: str, path: str) -> dict[str, Any]:
    """Drop an image from disk for an influence (what the shell gives); it stays in place."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise NotFound(f"file not found: {source}")
    return add_reference(project, aspect, influence, source.read_bytes(), source.name)


def _settings(model: str, count: int, width: int, height: int, reference: str,
              strength: float, project: str, aspect: str) -> None:
    if model not in MODELS:
        raise ServiceError(f"unknown model: {model} (offered: {', '.join(MODELS)})")
    if not 1 <= count <= cards.MAX_COUNT:
        raise ServiceError(f"count must be between 1 and {cards.MAX_COUNT}")
    if width not in SIZES or height not in SIZES:
        raise ServiceError(f"size must be between {SIZES.start} and {SIZES.stop - 1} pixels")
    if reference and not reference.startswith(REF):
        raise ServiceError("a reference is a dropped image: “ref:<file>”")
    if reference and _image(project, aspect, reference) is None:
        raise NotFound(f"image not found: {reference}")
    if model == air.FLUX_KONTEXT and not reference:
        raise ServiceError("Kontext touches up an image: give it a reference (“ref:<file>”)")
    if not 0.0 <= strength <= 1.0:
        raise ServiceError("strength must be between 0 and 1")


def propose(project: str, aspect: str, *, influence: str = "", prompt: str, count: int = 4,
            model: str = DEFAULT_MODEL, width: int = DEFAULT_SIZE[0],
            height: int = DEFAULT_SIZE[1], reference: str = "", strength: float = 0.6,
            why: str = "") -> dict[str, Any]:
    """Propose images to generate for an influence, or for the aspect as a whole
    (`influence=""`). Free: nothing leaves.

    The user sees the proposal with its amount, and pays it -- or not (`pay`).
    """
    text = prompt.strip()
    if not text:
        raise ServiceError("empty prompt: say what the image must show")
    if len(text) > MAX_PROMPT:
        raise ServiceError(f"prompt too long: {len(text)} characters (at most {MAX_PROMPT})")
    _settings(model, count, width, height, reference, strength, project, aspect)
    ensure_card(project, aspect)
    with _board(project, aspect) as held:
        if influence:
            _influence(held, influence)
        entry = {"id": f"p-{uuid.uuid4().hex[:8]}", "influence": influence, "prompt": text,
                 "model": model, "count": count, "width": width, "height": height,
                 "reference": reference, "strength": strength,
                 "why": _text(why, MAX_TEXT, "why"), "status": "proposed", "job": "",
                 "error": "", "images": [], "created_at": _now()}
        held["proposals"].append(entry)
    return {**entry, "cost_usd": _cost(entry)}


def pay(project: str, aspect: str, proposal: str, *, prompt: str | None = None,
        model: str | None = None, count: int | None = None,
        confirm: bool = False) -> dict[str, Any]:
    """PAID: generate a proposal's images, as proposed or adjusted.

    Refused without `confirm`, stating the amount; a proposal is paid once.
    """
    card = ensure_card(project, aspect)
    with _board(project, aspect) as held:
        entry = _proposal(held, proposal)
        if entry["status"] != "proposed":
            raise ServiceError(f"proposal already {entry['status']}: {proposal}")
        wanted = {**entry, "prompt": (prompt if prompt is not None else entry["prompt"]).strip(),
                  "model": model or entry["model"],
                  "count": count if count is not None else entry["count"]}
        if not wanted["prompt"]:
            raise ServiceError("empty prompt: say what the image must show")
        _settings(wanted["model"], wanted["count"], wanted["width"], wanted["height"],
                  wanted["reference"], wanted["strength"], project, aspect)
        if not confirm:
            cost = _cost(wanted)
            raise PaymentRequired(
                f"paid generation (~${produce.usd(cost or 0)} for {wanted['count']} image(s)): "
                "the user pays the proposal in the Universe")
        queued = cards.generate(
            project, FOLDER, card["name"], prompt=wanted["prompt"], model=wanted["model"],
            reference=wanted["reference"], strength=wanted["strength"],
            width=wanted["width"], height=wanted["height"], count=wanted["count"],
            style=False, confirm=True)
        entry.update(prompt=wanted["prompt"], model=wanted["model"], count=wanted["count"],
                     status="queued", job=queued["queued"][0], paid_at=_now())
        paid = dict(entry)
    return {**paid, "cost_usd": _cost(paid)}


def dismiss(project: str, aspect: str, proposal: str) -> dict[str, Any]:
    """Set a proposal aside: it stays in the thread, unpaid."""
    with _board(project, aspect) as held:
        entry = _proposal(held, proposal)
        if entry["status"] != "proposed":
            raise ServiceError(f"proposal already {entry['status']}: {proposal}")
        entry["status"] = "dismissed"
        dismissed = dict(entry)
    return {**dismissed, "cost_usd": _cost(dismissed)}
