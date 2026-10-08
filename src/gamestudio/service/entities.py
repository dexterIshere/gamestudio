"""A world card's workbench: from the card to concepts, then to 3D.

Nothing is generated on the fly. An entity starts as a card written in a world
section (`service/world.py`) -- what it is, what it is for, what it looks like
--, and that text seeds its concepts. One iterates on those images, chooses
one, and that one goes to 3D: the 3D Viewport is a step of a card, not a page
without context. Its rig and animations are then handed to an agent
(`service/handoff.py`).

The link between the card and what it produces is an **entity id**, stored next
to the card (`<card>.workbench.json`, versioned with it) along with the chosen
concept. It is the character's id in the database and the concepts' `entity`
key: renaming the card moves the workbench without losing anything.

The same workbench holds the card's **axis values** (`axes`): its filing in the
grids its section declares (`service/world.py`), so that a card is filed
without being renamed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..pipeline.graph import entity_slug
from . import documents, jobs, produce, world
from .context import space
from .errors import NotFound, ServiceError

# How many concepts are scanned to find an entity's.
SCAN_LIMIT = 2000

# The card headings that seed a concept's prompt: what it is, and what it looks
# like ("In the game" says how it is used, not how it looks). Users write cards
# in English or French, so both languages' headings are read.
SEED_SECTIONS = ("in short", "appearance", "en bref", "apparence")


def _sidecar(project: str, section: str, name: str) -> Path:
    return documents.directory(project, world.folder(section)) / f"{name}{documents.WORKBENCH}"


def _read_state(project: str, section: str, name: str) -> dict[str, Any]:
    path = _sidecar(project, section, name)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ServiceError(f"{path} cannot be read: {exc}") from exc
    return raw if isinstance(raw, dict) else {}


def _write_state(project: str, section: str, name: str, state: dict[str, Any]) -> None:
    path = _sidecar(project, section, name)
    path.write_text(json.dumps({"version": 1, **state}, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _cards(project: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every world card: (section, document)."""
    return [(section, entry)
            for section in world.sections(project)
            for entry in documents.documents(project, section["folder"])]


def _claimed(project: str, section: str, name: str) -> set[str]:
    """The entity ids already held by the project's other cards."""
    taken: set[str] = set()
    for other, entry in _cards(project):
        if other["id"] == section and entry["name"] == name:
            continue
        state = _read_state(project, other["id"], entry["name"])
        taken.add(str(state.get("entity") or entity_slug(entry["name"])))
    return taken


def _entity(project: str, section: str, name: str, *, claim: bool = False) -> str:
    """A card's entity id; `claim` fixes it on disk.

    By default it is the card's name. Two cards in different sections may share
    a name: the second to claim it then gets its section's name as a prefix, so
    their concepts never mix.
    """
    state = _read_state(project, section, name)
    if state.get("entity"):
        return str(state["entity"])
    entity = entity_slug(name)
    if not claim:
        return entity
    if entity in _claimed(project, section, name):
        entity = entity_slug(f"{section}-{name}")
    _write_state(project, section, name, {**state, "entity": entity})
    return entity


def _card(project: str, section: str, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    declared = world.section(project, section)
    return declared, documents.read_document(project, name, declared["folder"])


def seed_prompt(text: str, title: str) -> str:
    """The prompt a card seeds: its title, "In short" and "Appearance".

    Template comments and formatting are stripped: an image model has no use
    for a `<!-- ... -->` or a `**`.
    """
    clean = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    kept: list[str] = []
    current = ""
    for line in clean.splitlines():
        heading = re.match(r"^#{2,6}\s+(.*)$", line)
        if heading:
            current = heading.group(1).strip().lower()
            continue
        if line.startswith("# "):
            continue
        if current in SEED_SECTIONS and line.strip():
            kept.append(re.sub(r"[*_`>#]+", "", line).strip(" -\t").rstrip("."))
    body = ", ".join(part for part in kept if part)
    return f"{title}, {body}" if body else title


def _all_concepts(project: str) -> dict[str, list[dict[str, Any]]]:
    """The project's concepts by entity, newest first, in a single scan."""
    found: dict[str, list[dict[str, Any]]] = {}
    for asset in space(project).db.list_assets(kind="image", limit=SCAN_LIMIT):
        entity = asset.meta.get("entity")
        if (not entity or asset.meta.get("role") != "generation"
                or asset.meta.get("project", project) != project):
            continue
        found.setdefault(str(entity), []).append({
            "id": asset.id, "batch": asset.meta.get("batch", ""),
            "prompt": asset.meta.get("prompt", ""), "model": asset.meta.get("model", ""),
            "reference": asset.meta.get("reference"),
            "created_at": asset.created_at.isoformat(timespec="seconds")})
    return found


def _concepts(project: str, entity: str) -> list[dict[str, Any]]:
    return _all_concepts(project).get(entity, [])


def _character(project: str, entity: str) -> dict[str, Any] | None:
    character = space(project).db.get_character(project, entity)
    if character is None:
        return None
    return {"id": character.spec.id, "state": character.state.value,
            "concept_asset": character.concept_asset_id,
            "has_rig3d": character.rig3d is not None,
            "exports": character.exports, "errors": character.errors}


def _pending(project: str, entity: str) -> list[dict[str, Any]]:
    steps = {f"concepts:{entity}", f"entity:{entity}"}
    return [job for state in ("pending", "running")
            for job in jobs.listing(project, state, limit=100)
            if job["step"] in steps]


def card_axes(project: str, section: str, name: str) -> dict[str, str]:
    """A card's filing: one value per axis of its section."""
    return dict(_read_state(project, section, name).get("axes") or {})


def workbench(project: str, section: str, name: str) -> dict[str, Any]:
    """A card's whole workbench: its text, its concepts, what was produced from it."""
    declared, card = _card(project, section, name)
    entity = _entity(project, section, name)
    state = _read_state(project, section, name)
    character = _character(project, entity)
    return {
        "project": project,
        "section": {"id": declared["id"], "label": declared["label"],
                    "icon": declared["icon"], "axes": declared.get("axes") or []},
        "name": card["name"],
        "title": card["title"],
        "path": card["path"],
        "entity": entity,
        "prompt": seed_prompt(card["text"], card["title"]),
        "concept": state.get("concept"),
        "axes": state.get("axes") or {},
        "concepts": _concepts(project, entity),
        "character": character,
        "pending": _pending(project, entity),
    }


def entities(project: str) -> list[dict[str, Any]]:
    """The world cards and each one's entity: what links the library or a
    character to the card it came from.

    `cover` is the image that stands for the card: the chosen concept, else the
    character's, else the latest drawn.
    """
    concepts = _all_concepts(project)
    rows = []
    for section, entry in _cards(project):
        state = _read_state(project, section["id"], entry["name"])
        entity = str(state.get("entity") or entity_slug(entry["name"]))
        character = _character(project, entity)
        drawn = concepts.get(entity, [])
        cover = (state.get("concept") or (character or {}).get("concept_asset")
                 or (drawn[0]["id"] if drawn else None))
        rows.append({"section": section["id"], "section_label": section["label"],
                     "icon": section["icon"], "name": entry["name"],
                     "title": entry["title"], "entity": entity,
                     "concept": state.get("concept"),
                     "axes": state.get("axes") or {},
                     "concepts": len(drawn),
                     "cover": cover, "character": character,
                     "modified_at": entry.get("modified_at")})
    return rows


def generate_concepts(project: str, section: str, name: str, *, prompt: str = "",
                      model: str | None = None, negative_prompt: str = "",
                      style: bool = True, reference_asset_id: str | None = None,
                      strength: float = 0.6, pose: str | None = None,
                      width: int = 768, height: int = 1152, count: int = 4,
                      transparent: bool = False, seed: int | None = None,
                      confirm: bool = False) -> dict[str, Any]:
    """Draw a batch of concepts for a card. PAID (~$0.006 per image with FLUX dev).

    Refused without `confirm`, stating the batch's amount, before writing
    anything: the entity id is only fixed once consent is given. Without
    `prompt`, the card speaks (`seed_prompt`). `reference_asset_id` --
    typically a concept already drawn -- varies one lead instead of opening a
    new one.
    """
    _declared, card = _card(project, section, name)
    produce.check_images(count, model=model, width=width, height=height, confirm=confirm)
    entity = _entity(project, section, name, claim=True)
    text = prompt.strip() or seed_prompt(card["text"], card["title"])
    recipe = project if style and _has_recipe(project) else None
    return {**produce.generate_image(
        text, model=model, negative_prompt=negative_prompt, recipe=recipe,
        reference_asset_id=reference_asset_id, strength=strength, pose=pose,
        width=width, height=height, count=count, transparent=transparent,
        seed=seed, project=project, entity=entity, confirm=confirm), "entity": entity}


def choose_concept(project: str, section: str, name: str,
                   asset_id: str | None) -> dict[str, Any]:
    """Choose a concept -- the one that will go to 3D --, or none."""
    _card(project, section, name)
    entity = _entity(project, section, name, claim=True)
    if asset_id:
        asset = space(project).db.get_asset(asset_id)
        if asset is None:
            raise NotFound(f"asset not found: {asset_id}")
        if asset.kind != "image":
            raise ServiceError(f"{asset_id} is not an image ({asset.kind})")
    state = _read_state(project, section, name)
    _write_state(project, section, name, {**state, "entity": entity, "concept": asset_id})
    return workbench(project, section, name)


# ---------------------------------------------------------- filing axes


def _match_value(values: dict[str, str], given: str) -> str | None:
    """A value's id, given by its id or by its label.

    Accepting the label is convenient ("Elf"), and the id keeps the filing
    stable: it is what gets written on the card.
    """
    if given in values:
        return given
    folded = given.strip().casefold()
    return next((value_id for value_id, label in values.items()
                 if label.casefold() == folded), None)


def resolve_axes(section: dict[str, Any],
                 values: dict[str, Any]) -> dict[str, str | None]:
    """A filing reduced to the grid's ids; `None` to detach.

    An axis the section does not declare, or a value outside its grid, is
    refused: a wrong filing only shows once the cards are lost.
    """
    declared = {axis["id"]: {value["id"]: value["label"] for value in axis["values"]}
                for axis in section.get("axes") or []}
    resolved: dict[str, str | None] = {}
    for axis_id, given in (values or {}).items():
        if axis_id not in declared:
            known = ", ".join(declared) or "none"
            raise ServiceError(f"section “{section['label']}” does not declare the axis "
                               f"“{axis_id}” (known: {known})")
        if given is None or str(given).strip() == "":
            resolved[axis_id] = None
            continue
        matched = _match_value(declared[axis_id], str(given))
        if matched is None:
            known = ", ".join(declared[axis_id]) or "none"
            raise ServiceError(f"“{given}” is not a value of “{axis_id}” (known: {known})")
        resolved[axis_id] = matched
    return resolved


def set_axes(project: str, section: str, name: str,
             values: dict[str, str]) -> dict[str, Any]:
    """File a card along its section's axes.

    `values` maps an axis to a value, by id or by label; an empty value
    detaches that axis. **Only the cited axes change**: setting the race does
    not undo the faction. An axis the section does not declare, or a value
    outside its grid, is refused (`resolve_axes`).
    """
    declared, _ = _card(project, section, name)
    state = _read_state(project, section, name)
    current = dict(state.get("axes") or {})
    for axis_id, value_id in resolve_axes(declared, values).items():
        if value_id is None:
            current.pop(axis_id, None)
        else:
            current[axis_id] = value_id
    _write_state(project, section, name, {**state, "axes": current})
    return workbench(project, section, name)


def clear_axes(project: str, section: str,
               lost: list[tuple[str, str | None]]) -> int:
    """Detach axis values from a section's cards; return how many moved.

    `lost` comes from `world.set_axes`: a removed axis (`None`) takes all its
    values, a removed value only itself. This keeps a card from staying filed
    under a value that no longer exists.
    """
    if not lost:
        return 0
    touched = 0
    for entry in documents.documents(project, world.folder(section)):
        state = _read_state(project, section, entry["name"])
        current = dict(state.get("axes") or {})
        if not current:
            continue
        before = dict(current)
        for axis_id, value_id in lost:
            if value_id is None or current.get(axis_id) == value_id:
                current.pop(axis_id, None)
        if current != before:
            _write_state(project, section, entry["name"], {**state, "axes": current})
            touched += 1
    return touched


def realize(project: str, section: str, name: str, *, mesh_model: str | None = None,
            face_limit: int = 8000, confirm: bool = False) -> dict[str, Any]:
    """Take the chosen concept to 3D: its bare mesh.

    PAID (~$0.15-1.25 depending on `mesh_model`), `confirm=true` after consent;
    the refusal states the amount (`produce.create_entity`). Rig and animations
    are then handed to an agent.
    """
    _declared, card = _card(project, section, name)
    entity = _entity(project, section, name, claim=True)
    concept = _read_state(project, section, name).get("concept")
    if not concept:
        raise ServiceError("no concept chosen: draw concepts, then choose one")
    recipe = project if _has_recipe(project) else None
    result = produce.create_entity(
        seed_prompt(card["text"], card["title"]), name=entity,
        mesh_model=mesh_model, recipe=recipe, reference_asset_id=concept,
        face_limit=face_limit, confirm=confirm, project=project)
    return {**result, "entity": entity}


def attach_mesh(project: str, section: str, name: str, path: str, *,
                replace: bool = False) -> dict[str, Any]:
    """Attach to the card a mesh made elsewhere (`img2threejs`, Blender) -- free."""
    from . import meshes

    _declared, card = _card(project, section, name)
    entity = _entity(project, section, name, claim=True)
    return meshes.import_mesh(path, project=project, name=entity,
                              subject=seed_prompt(card["text"], card["title"]),
                              attach=True, replace=replace)


def _has_recipe(project: str) -> bool:
    try:
        produce.resolve_recipe(project)
    except (NotFound, ServiceError):
        return False
    return True
