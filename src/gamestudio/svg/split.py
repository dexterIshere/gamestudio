"""Splitting an SVG sheet into one file per icon.

An icon sheet is a single SVG holding N icons. Depending on the tool that
produced it, the separation is already written in the document -- the
`<symbol>`s of a sprite sheet, one `<g>` per icon from Figma or Illustrator --
or it is gone, leaving only a soup of flat `<path>`s after an optimizer pass.

All three cases follow a single rule: *each drawable child of the sheet is an
indivisible unit, and two units that touch belong to the same icon.* A group
thus stays whole, scattered strokes join back together, and a sheet's grid
separates by itself as soon as the spacing between icons exceeds the gaps
inside them.

This is exact geometry, not estimation: the same sheet always gives the same
split, and the grouping threshold (`gap`) is the only setting, adjustable by
hand when a sheet is out of the ordinary.

Each extracted icon is a standalone SVG: viewBox fitted to its content,
ancestor transforms flattened into a wrapping group, inherited styles copied,
and only the definitions (`<defs>`, gradients, masks) it actually references.
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element

from ..sheet.layout import (
    Piece,
    SheetError,
    SheetSplit,
    group_with_auto_gap,
    is_generic_name,
    name_pieces,
    reading_order,
    slug,
)
from .geometry import (
    DEFAULT_STROKE,
    IDENTITY,
    SVG_NS,
    XLINK_NS,
    BBox,
    Matrix,
    StrokeState,
    element_bbox,
    element_matrix,
    is_drawable,
    is_identity,
    local_name,
    matrix_attribute,
    multiply,
    parse_length,
    parse_viewbox,
    stroke_state,
    union_all,
)

# Without this registration, ElementTree prefixes everything with `ns0:`.
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

STRATEGIES = ("auto", "symbols", "groups", "clusters", "single")

# Attributes that describe the document or the placement, and so have no
# business on the wrapping group of an extracted icon.
_NON_INHERITED = frozenset({
    "transform", "id", "viewBox", "width", "height", "x", "y", "version",
    "preserveAspectRatio", "xmlns", "data-name", "aria-label", "role",
    # A sprite sheet hides itself (`<svg style="display:none">`): its extracted
    # icons must show.
    "display", "visibility",
})

_URL_REF_RE = re.compile(r"url\(\s*['\"]?#([^)'\"\s]+)")
_ID_REF_RE = re.compile(r"^\s*#(.+?)\s*$")


# An unreadable SVG sheet and an unreadable PNG sheet are the same error.
SvgError = SheetError


class SvgIcon(Piece):
    """An extracted icon: the same piece as for a bitmap sheet, but whose
    standalone file is XML text."""

    @property
    def svg(self) -> str:
        return self.data.decode("utf-8")


# -------------------------------------------------------------------- document


class SvgDocument:
    """The loaded sheet: XML tree, `id` index, boxes in root space."""

    def __init__(self, root: Element, source: str = "") -> None:
        if local_name(root) != "svg":
            raise SvgError(f"root <{local_name(root) or '?'}>: this is not an SVG")
        self.root = root
        self.source = source
        self._parents: dict[int, Element] = {}
        for parent in root.iter():
            for child in parent:
                self._parents[id(child)] = parent
        self._by_id: dict[str, Element] = {}
        for element in root.iter():
            identifier = element.get("id")
            if identifier and identifier not in self._by_id:
                self._by_id[identifier] = element
        self._bbox_cache: dict[int, BBox | None] = {}

    # --- reading

    @property
    def viewbox(self) -> tuple[float, float, float, float]:
        box = parse_viewbox(self.root.get("viewBox"))
        if box is not None and box[2] > 0 and box[3] > 0:
            return box
        width = parse_length(self.root.get("width"), 0.0)
        height = parse_length(self.root.get("height"), 0.0)
        if width > 0 and height > 0:
            return (0.0, 0.0, width, height)
        content = self.bbox(self.root)
        if content is None:
            return (0.0, 0.0, 0.0, 0.0)
        return (content.x0, content.y0, content.width, content.height)

    def parent(self, element: Element) -> Element | None:
        return self._parents.get(id(element))

    def ancestors(self, element: Element) -> list[Element]:
        """From the root to the direct parent."""
        chain: list[Element] = []
        current = self.parent(element)
        while current is not None:
            chain.append(current)
            current = self.parent(current)
        return list(reversed(chain))

    def resolve(self, reference: str) -> Element | None:
        match = _ID_REF_RE.match(reference or "")
        return self._by_id.get(match.group(1)) if match else None

    def ancestor_matrix(self, element: Element) -> Matrix:
        """Cumulative transform of the ancestors, excluding the element's own.

        The root does not count: boxes are expressed in its user space, that of
        its viewBox.
        """
        matrix = IDENTITY
        for ancestor in self.ancestors(element):
            if ancestor is self.root:
                continue
            matrix = multiply(matrix, element_matrix(ancestor))
        return matrix

    def ancestor_stroke(self, element: Element) -> StrokeState:
        """Inherited stroke state: a `stroke` set on the sheet applies here."""
        state = DEFAULT_STROKE
        for ancestor in self.ancestors(element):
            state = stroke_state(ancestor, state)
        return state

    def bbox(self, element: Element) -> BBox | None:
        """An element's box in the root's user space."""
        key = id(element)
        if key not in self._bbox_cache:
            self._bbox_cache[key] = element_bbox(
                element, self.ancestor_matrix(element), self.resolve,
                self.ancestor_stroke(element))
        return self._bbox_cache[key]


def parse_document(source: str | Path) -> SvgDocument:
    """Load a sheet from a path or from the document's text."""
    text, name = _read_source(source)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SvgError(f"unreadable XML: {exc}") from exc
    return SvgDocument(root, name)


def _read_source(source: str | Path) -> tuple[str, str]:
    """A path, or the document's text directly (it starts with `<`)."""
    if isinstance(source, str) and source.lstrip().startswith("<"):
        return source, "<memory>"
    path = Path(source)
    try:
        return path.read_text(encoding="utf-8"), path.name
    except OSError as exc:
        raise SvgError(f"cannot read: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SvgError(f"{path.name} is not text: compressed SVGZ?") from exc


# ------------------------------------------------------------------- detection


@dataclass
class _Unit:
    """An indivisible unit of the sheet, with its box in root space."""

    elements: tuple[Element, ...]
    bbox: BBox

    @property
    def first(self) -> Element:
        return self.elements[0]


def _drawable_children(element: Element) -> list[Element]:
    return [child for child in element if is_drawable(child)]


def _is_background(sheet: SvgDocument, element: Element, sheet_area: float) -> bool:
    """A filled shape covering the sheet is a background, not an icon."""
    if sheet_area <= 0 or local_name(element) not in ("rect", "path"):
        return False
    box = sheet.bbox(element)
    return box is not None and box.area >= 0.9 * sheet_area


def _candidates(sheet: SvgDocument, *, drop_background: bool) -> tuple[list[Element], list[str]]:
    """The sheet's units, once its wrappers are crossed.

    A tool export often wraps everything in a `<g id="Layer_1">`, sometimes on
    top of a background rectangle. As long as a single visible group remains,
    it is not an icon: it is a wrapper, and the search goes inside it.
    """
    _, _, width, height = sheet.viewbox
    area = width * height
    notes: list[str] = []
    node = sheet.root
    while True:
        children = _drawable_children(node)
        if drop_background and len(children) > 1:
            visible = [c for c in children if not _is_background(sheet, c, area)]
            if visible and len(visible) < len(children):
                notes.append(f"{len(children) - len(visible)} background(s) covering the "
                             "sheet dropped")
                children = visible
        if len(children) == 1 and local_name(children[0]) in ("g", "a", "switch"):
            node = children[0]
            continue
        return children, notes


def _units(sheet: SvgDocument, elements: list[Element]) -> list[_Unit]:
    units: list[_Unit] = []
    for element in elements:
        box = sheet.bbox(element)
        if box is not None:
            units.append(_Unit((element,), box))
    return units


def _cluster(units: list[_Unit], gap: float | None) -> tuple[list[_Unit], float]:
    """Group the units that touch, by the rule shared by all sheets."""
    groups, effective = group_with_auto_gap([unit.bbox for unit in units], gap)
    merged: list[_Unit] = []
    for group in groups:
        box = union_all(units[index].bbox for index in group)
        if box is None:
            continue
        merged.append(_Unit(tuple(e for index in group for e in units[index].elements), box))
    return merged, effective


# ---------------------------------------------------------------------- naming


def _unit_name(unit: _Unit) -> str:
    """The name the icon carries in the sheet, or empty if it carries none."""
    if len(unit.elements) != 1:
        return ""
    element = unit.first
    for attribute in ("id", "data-name", "aria-label",
                      "{http://www.inkscape.org/namespaces/inkscape}label"):
        candidate = slug(element.get(attribute, ""))
        if candidate and not is_generic_name(candidate):
            return candidate
    for child in element:
        if local_name(child) == "title" and child.text:
            candidate = slug(child.text)
            if candidate:
                return candidate
    return ""


# ----------------------------------------------------------------- construction


def _collect_references(sheet: SvgDocument, elements: tuple[Element, ...]) -> list[Element]:
    """Definitions the icon actually references, resolved transitively.

    Copying the sheet's whole `<defs>` into each icon would bloat every file
    with all the sheet's gradients; copying nothing would break the fills. So
    `url(#...)` and `href="#..."` are followed, in a loop.
    """
    found: dict[str, Element] = {}
    frontier: list[Element] = [e for element in elements for e in element.iter()]
    while frontier:
        element = frontier.pop()
        for name, value in element.attrib.items():
            references: list[str] = _URL_REF_RE.findall(value)
            if local_name_of_attribute(name) == "href":
                match = _ID_REF_RE.match(value)
                if match:
                    references.append(match.group(1))
            for reference in references:
                if reference in found:
                    continue
                target = sheet.resolve(f"#{reference}")
                if target is None:
                    continue
                found[reference] = target
                frontier.extend(target.iter())
    return list(found.values())


def local_name_of_attribute(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _inherited_attributes(sheet: SvgDocument, element: Element) -> dict[str, str]:
    """Presentation attributes inherited from the ancestors, root included.

    Without them, a `fill` set once on the sheet's group would vanish from
    every extracted icon.
    """
    attributes: dict[str, str] = {}
    styles: list[str] = []
    for ancestor in sheet.ancestors(element):
        for name, value in ancestor.attrib.items():
            if name in _NON_INHERITED or name.startswith("{"):
                continue
            if name == "style":
                styles.append(_visible_style(value))
            else:
                attributes[name] = value
    style = ";".join(part for part in styles if part)
    if style:
        attributes["style"] = style
    return attributes


def _visible_style(style: str) -> str:
    """An inherited `style`, stripped of what would hide the extracted icon."""
    declarations = [d.strip() for d in style.split(";") if d.strip()]
    return ";".join(d for d in declarations
                    if d.split(":", 1)[0].strip() not in _NON_INHERITED)


def _icon_element(element: Element) -> Element:
    """A deep copy ready to be drawn.

    A `<symbol>` is never rendered where it is defined: it becomes an ordinary
    `<g>`, and its viewBox -- already taken into account in the box -- goes away.
    """
    clone = copy.deepcopy(element)
    if local_name(clone) == "symbol":
        clone.tag = f"{{{SVG_NS}}}g"
        for attribute in ("viewBox", "preserveAspectRatio", "width", "height", "x", "y"):
            clone.attrib.pop(attribute, None)
    return clone


def _build_svg(sheet: SvgDocument, unit: _Unit, *, padding: float) -> str:
    """Assemble an icon's standalone SVG."""
    frame = unit.bbox.expand(padding) if padding else unit.bbox
    root = Element(f"{{{SVG_NS}}}svg")
    root.set("viewBox", " ".join(f"{v:.6g}" for v in
                                 (frame.x0, frame.y0, frame.width, frame.height)))
    root.set("width", f"{frame.width:.6g}")
    root.set("height", f"{frame.height:.6g}")

    definitions = _collect_references(sheet, unit.elements)
    styles = [style for style in sheet.root.iter()
              if local_name(style) == "style" and style.text]
    if definitions or styles:
        defs = Element(f"{{{SVG_NS}}}defs")
        for style in styles:
            defs.append(copy.deepcopy(style))
        for definition in definitions:
            defs.append(copy.deepcopy(definition))
        root.append(defs)

    # An icon's elements are siblings: a single wrapping group carries the
    # transform and styles of their ancestor chain.
    for group in _group_by_parent(sheet, unit.elements):
        reference = group[0]
        wrapper = Element(f"{{{SVG_NS}}}g")
        matrix = sheet.ancestor_matrix(reference)
        if not is_identity(matrix):
            wrapper.set("transform", matrix_attribute(matrix))
        for name, value in _inherited_attributes(sheet, reference).items():
            wrapper.set(name, value)
        for element in group:
            wrapper.append(_icon_element(element))
        # A group without transform or inherited style adds nothing: the icon
        # may as well be the content itself.
        if wrapper.attrib:
            root.append(wrapper)
        else:
            root.extend(list(wrapper))

    if not any(local_name(node) == "text" for node in root.iter()):
        # Readable indentation, unless text is present: whitespace matters there.
        ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def _group_by_parent(sheet: SvgDocument, elements: tuple[Element, ...]) -> list[list[Element]]:
    """An icon's elements, grouped by parent (hence by ancestor chain)."""
    groups: dict[int, list[Element]] = {}
    for element in elements:
        parent = sheet.parent(element)
        groups.setdefault(id(parent) if parent is not None else 0, []).append(element)
    return list(groups.values())


# ----------------------------------------------------------------------- split


def split_svg(
    source: str | Path,
    *,
    strategy: str = "auto",
    gap: float | None = None,
    min_size: float = 1.0,
    padding: float = 0.0,
    drop_background: bool = True,
    max_units: int = 3000,
) -> SheetSplit:
    """Split an SVG sheet into standalone icons.

    - `strategy`: `auto` (symbols if there are any, otherwise proximity),
      `symbols`, `groups` (one child = one icon, no grouping), `clusters`
      (geometric proximity), `single` (the whole sheet as one icon).
    - `gap`: maximum distance, in user units, between two pieces of the same
      icon. `None` derives it from the units' median size.
    - `min_size`: drop the units none of whose sides reaches this size.
    - `padding`: margin added around each icon.
    - `drop_background`: drop a background covering the whole sheet.

    No network call, no rasterization: the same sheet always gives exactly the
    same split.
    """
    if strategy not in STRATEGIES:
        raise SvgError(f"unknown strategy: {strategy} (among {', '.join(STRATEGIES)})")

    sheet = parse_document(source)
    _, _, sheet_width, sheet_height = sheet.viewbox
    warnings: list[str] = []

    symbols = [element for element in sheet.root.iter() if local_name(element) == "symbol"]
    used = strategy
    if strategy == "auto":
        used = "symbols" if symbols else "clusters"

    if used == "single":
        elements = tuple(_drawable_children(sheet.root))
        box = union_all(sheet.bbox(element) for element in elements)
        units = [_Unit(elements, box)] if elements and box is not None else []
        effective_gap = 0.0
    else:
        if used == "symbols":
            if not symbols:
                raise SvgError("no <symbol> in this sheet: try strategy='clusters'")
            units = _units(sheet, symbols)
        else:
            elements, notes = _candidates(sheet, drop_background=drop_background)
            warnings.extend(notes)
            units = _units(sheet, elements)

        if len(units) > max_units:
            warnings.append(f"{len(units)} units: grouping limited to the first "
                            f"{max_units} (unusual sheet)")
            units = units[:max_units]

        units, dropped = _filter_units(units, min_size=min_size)
        warnings.extend(dropped)

        effective_gap = 0.0
        if used == "clusters":
            units, effective_gap = _cluster(units, gap)

    units = [units[index] for index in reading_order([unit.bbox for unit in units])]
    names = name_pieces([_unit_name(unit) for unit in units], prefix="icon")
    icons = [
        SvgIcon(
            name=name,
            index=index,
            x=unit.bbox.x0, y=unit.bbox.y0,
            width=unit.bbox.width, height=unit.bbox.height,
            data=_build_svg(sheet, unit, padding=padding).encode("utf-8"),
            suffix=".svg",
            elements=len(unit.elements),
            source_id=unit.first.get("id", "") if len(unit.elements) == 1 else "",
        )
        for index, (unit, name) in enumerate(zip(units, names, strict=True))
    ]
    if not icons:
        warnings.append("no drawable shape found in the sheet")
    elif len(icons) == 1 and used != "single":
        warnings.append("a single icon detected: the sheet may hold only one, "
                        "or lowering `gap` would split it")

    return SheetSplit(source=sheet.source, strategy=used, width=sheet_width,
                      height=sheet_height, pieces=icons, warnings=warnings,
                      gap=effective_gap)


def _filter_units(units: list[_Unit], *, min_size: float) -> tuple[list[_Unit], list[str]]:
    """Drop the specks: registration marks, isolated dots, void shapes."""
    kept = [u for u in units if max(u.bbox.width, u.bbox.height) >= min_size]
    if len(kept) == len(units):
        return kept, []
    return kept, [f"{len(units) - len(kept)} shape(s) under {min_size:g} unit(s) "
                  "dropped"]
