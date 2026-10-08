"""A project's world: sections the user declares themselves.

Characters, buildings, factions, creatures -- the studio presumes none of them.
A project starts with an empty world; each section is created by hand, with a
label and an icon, and becomes a shelf of documents
(`.gamestudio/documents/world/<section>/`, see `service/documents.py`): one
card per element of the world.

**Axes** are filing grids with closed values ("Race" -> Human, Elf, Orc). They
belong to **the project**, not to a section: a section cites the ones it uses,
and an axis can be corrected from any of them -- the change applies wherever it
is used. A new section therefore reuses an already declared axis instead of
rewriting it. A card carries one value per axis of its section (in its
workbench, `<card>.workbench.json`, see `service/entities.py`): it is filed
without ever being renamed.

An axis and a value carry an **id** fixed at creation and a **label** that can
be corrected: renaming "Elf" to "Dark elf" detaches no card. Only a value's
removal detaches the cards that carried it, in every section using the axis; a
section that drops an axis detaches only its own cards, and the axis stays in
the project.

The declaration is written next to the cards (`world/sections.json`): it is
versioned with the documents, and it gives the order, the icons and the grids
-- a folder says none of that.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import documents
from .errors import NotFound, ServiceError

# The sections' shelf, and the declaration that lists them.
SHELF = "world"
DECLARATION = "sections.json"
# Declaration format. v2 declared axes inside each section (still read: see
# `_load`); v3 holds axes at project level, each section citing the ones it uses.
VERSION = 3

# The icons a section can carry. The interface draws them; here only their names
# are kept, to refuse an icon nobody could draw.
ICONS = ("character", "building", "place", "map", "item", "creature",
         "faction", "book", "weapon", "vehicle", "plant", "star")
DEFAULT_ICON = "book"

# Bounds: an axis is a grid taken in at a glance, not a database. Beyond them,
# what is wanted is a free field, not an axis.
MAX_AXES = 8
MAX_VALUES = 100


def _file(project: str) -> Path:
    return documents.directory(project, SHELF) / DECLARATION


def _axes(raw: Any) -> list[dict[str, Any]]:
    """Declared axes, normalized: an id, a label, values."""
    found: list[dict[str, Any]] = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        values: list[dict[str, str]] = []
        for value in row.get("values") or []:
            if not isinstance(value, dict) or not value.get("id"):
                continue
            values.append({"id": str(value["id"]),
                           "label": str(value.get("label") or value["id"])})
        found.append({"id": str(row["id"]),
                      "label": str(row.get("label") or row["id"]),
                      "values": values})
    return found


def _merge(catalog: list[dict[str, Any]], axis: dict[str, Any]) -> None:
    """File an axis in the project; if it is already there, add the missing values.

    This reads a v2 declaration without losing anything: two sections that each
    declared their own "Race" share a single grid carrying the values of both.
    """
    known = next((entry for entry in catalog if entry["id"] == axis["id"]), None)
    if known is None:
        catalog.append({**axis, "values": list(axis["values"])})
        return
    have = {value["id"] for value in known["values"]}
    known["values"] += [value for value in axis["values"] if value["id"] not in have]


def _load(project: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The project's axes, and the sections -- each with the ids of its axes."""
    path = _file(project)
    if not path.is_file():
        return [], []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ServiceError(f"{path} cannot be read: {exc}") from exc
    raw = raw if isinstance(raw, dict) else {}
    catalog = _axes(raw.get("axes"))
    rows: list[dict[str, Any]] = []
    for row in raw.get("sections") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        refs: list[str] = []
        for entry in row.get("axes") or []:
            if isinstance(entry, str):
                refs.append(entry)
            elif isinstance(entry, dict) and entry.get("id"):
                # A v2 declaration: the axis moves up to the project.
                _merge(catalog, _axes([entry])[0])
                refs.append(str(entry["id"]))
        known = {axis["id"] for axis in catalog}
        rows.append({**row, "axes": [ref for ref in dict.fromkeys(refs) if ref in known]})
    return catalog, rows


def _write(project: str, catalog: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    path = _file(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": VERSION, "axes": catalog, "sections": rows},
                               indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def folder(section: str) -> str:
    """A section's document shelf."""
    return f"{SHELF}/{section}"


def _icon(icon: str | None) -> str:
    if icon is None or icon == "":
        return DEFAULT_ICON
    if icon not in ICONS:
        raise ServiceError(f"unknown icon: {icon} (known: {', '.join(ICONS)})")
    return icon


def _entry(project: str, row: dict[str, Any],
           catalog: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {axis["id"]: axis for axis in catalog}
    return {
        "id": row["id"],
        "label": row.get("label") or row["id"],
        "icon": row.get("icon") or DEFAULT_ICON,
        "folder": folder(row["id"]),
        "documents": len(documents.documents(project, folder(row["id"]))),
        "axes": [by_id[ref] for ref in row.get("axes") or [] if ref in by_id],
    }


def sections(project: str) -> list[dict[str, Any]]:
    """The world's sections, in creation order."""
    catalog, rows = _load(project)
    return [_entry(project, row, catalog) for row in rows]


def section(project: str, section_id: str) -> dict[str, Any]:
    catalog, rows = _load(project)
    for row in rows:
        if row["id"] == section_id:
            return _entry(project, row, catalog)
    raise NotFound(f"section not found: {project}/{section_id}")


def axes(project: str) -> list[dict[str, Any]]:
    """The project's axes, and the sections using each.

    This is what a new section is offered: an already declared axis is reused,
    not rewritten. Empty `sections`: the axis is no longer used; it stays in the
    project until forgotten (`forget_axis`).
    """
    catalog, rows = _load(project)
    return [{**axis, "sections": [{"id": row["id"], "label": row.get("label") or row["id"]}
                                   for row in rows if axis["id"] in row.get("axes", [])]}
            for axis in catalog]


def create_section(project: str, label: str, icon: str | None = None) -> dict[str, Any]:
    """Declare a section. Its id comes from the label, and never changes."""
    text = label.strip()
    if not text:
        raise ServiceError("empty label: a section without a name cannot be found again")
    section_id = documents.slug(text)
    catalog, rows = _load(project)
    if any(row["id"] == section_id for row in rows):
        raise ServiceError(f"section “{section_id}” already exists")
    # Validate the shelf here, before writing anything.
    documents.directory(project, folder(section_id))
    rows.append({"id": section_id, "label": text, "icon": _icon(icon), "axes": []})
    _write(project, catalog, rows)
    return section(project, section_id)


def update_section(project: str, section_id: str, *, label: str | None = None,
                   icon: str | None = None) -> dict[str, Any]:
    """Change the label or the icon. The folder keeps its name."""
    catalog, rows = _load(project)
    for row in rows:
        if row["id"] != section_id:
            continue
        if label is not None:
            if not label.strip():
                raise ServiceError("empty label")
            row["label"] = label.strip()
        if icon is not None:
            row["icon"] = _icon(icon)
        _write(project, catalog, rows)
        return section(project, section_id)
    raise NotFound(f"section not found: {project}/{section_id}")


# ------------------------------------------------------------------- axes


def _clean_values(label: str, raw: Any) -> list[dict[str, str]]:
    """An axis's values: derived ids, non-empty labels, no duplicates."""
    values: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw or []:
        if not isinstance(entry, dict):
            raise ServiceError(f"a value of “{label}” is an object {{id, label}}")
        value_label = str(entry.get("label") or "").strip()
        value_id = str(entry.get("id") or "").strip() or documents.slug(value_label)
        if not value_label or not value_id:
            raise ServiceError(f"a value of “{label}” has no label")
        if value_id in seen:
            raise ServiceError(f"“{value_label}” is declared twice in “{label}”")
        seen.add(value_id)
        values.append({"id": value_id, "label": value_label})
    if len(values) > MAX_VALUES:
        raise ServiceError(f"“{label}”: {len(values)} values (maximum {MAX_VALUES})")
    return values


def _clean_axes(axes: Any, catalog: list[dict[str, Any]],
                own: list[str]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Read a section's declaration: the axes it cites, and what they become.

    Each entry has one of three forms:

    - an id (`"race"`): the project's axis, reused as is;
    - an object with `id`: the project's axis, **corrected** -- its label and
      grid become these, wherever it is used;
    - an object without `id`: the id is derived from the label. An axis the
      project already has and the section did not use is **reused**, and only
      receives the values it lacks: naming "Race" never takes away from another
      section the values it uses. An axis the section already used is
      redeclared as given.

    Return the cited ids, in order, and the axes that change.
    """
    known = {axis["id"]: axis for axis in catalog}
    refs: list[str] = []
    changed: dict[str, dict[str, Any]] = {}
    for raw in axes if isinstance(axes, list) else []:
        if isinstance(raw, str):
            axis_id = raw.strip()
            if axis_id not in known:
                names = ", ".join(known) or "none"
                raise ServiceError(f"axis “{axis_id}” does not exist in the project (known: "
                                   f"{names})")
        elif isinstance(raw, dict):
            label = str(raw.get("label") or "").strip()
            given = str(raw.get("id") or "").strip()
            axis_id = given or documents.slug(label)
            if not label or not axis_id:
                raise ServiceError("an axis without a label cannot be found again")
            values = _clean_values(label, raw.get("values"))
            if axis_id in known and not given and axis_id not in own:
                have = {value["id"] for value in known[axis_id]["values"]}
                values = known[axis_id]["values"] + [value for value in values
                                                      if value["id"] not in have]
                label = known[axis_id]["label"]
            if len(values) > MAX_VALUES:
                raise ServiceError(f"“{label}”: {len(values)} values (maximum {MAX_VALUES})")
            changed[axis_id] = {"id": axis_id, "label": label, "values": values}
        else:
            raise ServiceError("an axis is an identifier, or an object {id, label, values}")
        if axis_id in refs:
            raise ServiceError(f"axis “{axis_id}” is declared twice")
        refs.append(axis_id)
    if len(refs) > MAX_AXES:
        raise ServiceError(f"too many axes: {len(refs)} (maximum {MAX_AXES})")
    return refs, changed


def _lost_values(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """The values a correction removes. A label never removes anything: ids are
    stable, that is the point."""
    kept = {value["id"] for value in new["values"]}
    return [value["id"] for value in old["values"] if value["id"] not in kept]


def set_axes(project: str, section_id: str, axes: Any) -> dict[str, Any]:
    """Set the axes a section uses, and what they become in the project.

    Nothing is renumbered: an axis or a value that keeps its id keeps its
    filing, even if its label changes. What detaches cards:

    - a value removed from an axis -- in **every** section using it, since the
      axis is the same everywhere;
    - an axis the section no longer uses -- in **this** section only: the axis
      stays in the project, and the other sections keep it.

    `detached` says how many cards moved, `detached_in` where.
    """
    from . import entities

    catalog, rows = _load(project)
    row = next((entry for entry in rows if entry["id"] == section_id), None)
    if row is None:
        raise NotFound(f"section not found: {project}/{section_id}")
    before = list(row.get("axes") or [])
    refs, changed = _clean_axes(axes, catalog, before)

    known = {axis["id"]: axis for axis in catalog}
    lost: dict[str, list[tuple[str, str | None]]] = {}
    for axis_id, axis in changed.items():
        if axis_id in known:
            gone = _lost_values(known[axis_id], axis)
            users = [entry["id"] for entry in rows
                     if axis_id in entry.get("axes", []) or entry["id"] == section_id]
            for user in users:
                lost.setdefault(user, []).extend((axis_id, value) for value in gone)
            known[axis_id].update(axis)
        else:
            catalog.append(axis)
    for axis_id in before:
        if axis_id not in refs:
            lost.setdefault(section_id, []).append((axis_id, None))
    row["axes"] = refs
    _write(project, catalog, rows)

    detached_in = {user: count for user, dropped in lost.items() if dropped
                   for count in [entities.clear_axes(project, user, dropped)] if count}
    return {**section(project, section_id), "detached": sum(detached_in.values()),
            "detached_in": detached_in}


def forget_axis(project: str, axis_id: str) -> dict[str, Any]:
    """Remove an axis from the project. Refused while a section uses it: then no
    card carries its value, and nothing but its grid is lost."""
    catalog, rows = _load(project)
    axis = next((entry for entry in catalog if entry["id"] == axis_id), None)
    if axis is None:
        raise NotFound(f"axis not found: {project}/{axis_id}")
    users = [row.get("label") or row["id"] for row in rows if axis_id in row.get("axes", [])]
    if users:
        raise ServiceError(f"axis “{axis['label']}” is still in use: {', '.join(users)}")
    _write(project, [entry for entry in catalog if entry["id"] != axis_id], rows)
    return {**axis, "forgotten": True}


def delete_section(project: str, section_id: str, *, force: bool = False) -> dict[str, Any]:
    """Remove a section.

    A section holding cards is refused: cards are precious texts. `force` takes
    them along -- their workbenches too -- but **never what they produced**:
    concepts and meshes stay in the library, where they do not depend on the
    card.
    """
    current = section(project, section_id)
    if current["documents"] and not force:
        raise ServiceError(f"section “{current['label']}” still holds {current['documents']} "
                           "card(s): delete them first, or force the removal")
    removed: list[str] = []
    if force:
        for entry in documents.documents(project, current["folder"]):
            documents.delete_document(project, entry["name"], current["folder"])
            removed.append(entry["name"])
    catalog, rows = _load(project)
    _write(project, catalog, [row for row in rows if row["id"] != section_id])
    where = documents.directory(project, current["folder"])
    if where.is_dir() and not any(where.iterdir()):
        where.rmdir()
    return {**current, "deleted": True, "cards": removed}
