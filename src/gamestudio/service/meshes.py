"""3D meshes: where they come from, and how they enter the studio.

A mesh is bought -- `create_entity(target="3d")` calls Runware (Tripo v3.1,
Hunyuan 3D, TRELLIS) or the direct Tripo API (P2, P1, H3.1: `tripo/`) -- or
made elsewhere for free and brought in by `import_mesh`. That is also how the
agent that rigs and animates an entity hands back its GLB.

The local route is `img2threejs` (an Apache-2.0 skill vendored with the
studio): it builds a mesh procedurally from an image, on the machine, without
network, and outputs a `model.glb`. That file becomes a `mesh` asset here,
attached to a character, filed as `3d/<entity>/<entity>.glb` -- so it can be
rendered into 2D sprite sheets by `produce.render_sprites`.

Three routes, one catalog (`mesh_providers`): what is free and local first,
what costs next. An agent choosing 3D must see them all, and know which one is
paid.

**Adding another direct API** does not touch the pipeline:
`generation.MeshProvider` is the seam, `register_mesh_provider` the
registration (that is how `TripoMesh` came in), and the `generate_mesh` step
takes the provider as a parameter. What remains is to complete the catalog
below, so that the interface and agents see the new route and its price.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.models import Character, CharacterSpec, JobState, Pipeline, Rig
from ..pipeline.base import Context
from ..runware import catalog as air
from ..tripo import TripoClient
from ..tripo import catalog as tripo_catalog
from .context import space, studio
from .errors import NotFound, ServiceError

# What a mesh can be: the formats Blender opens natively -- hence the ones
# `render_sprites` and the inventory can read.
MESH_SUFFIXES = (".glb", ".gltf", ".fbx", ".obj")

# A size guard: a game mesh fits in a few megabytes, and a file of several
# hundred MB almost always means the wrong file.
MAX_MESH_BYTES = 256 * 1024 * 1024


def _slug(value: str) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:40] or "entity"


def mesh_providers() -> list[dict[str, Any]]:
    """The studio's 3D routes: the free local one, then the paid ones.

    The order matters. A mesh made on the machine costs nothing; the others are
    paid, each at the price its model carries below. Listing the paid route
    first would make spending the default, which the studio forbids everywhere
    else.

    The direct route's prices are *computed* from Tripo's price grid for the
    request the studio sends (`tripo/catalog.py`): the bill is in credits, and a
    wrong displayed price would invite a click. Runware's prices are written
    here and copied by `app/src/lib/catalog.ts`: a test keeps both lists equal
    (`tests/test_mesh_catalog.py`).
    """
    return [
        {
            "id": "local",
            "label": "Local — img2threejs",
            "paid": False,
            "detail": "Procedural mesh built from an image, on the machine: no key, no network "
                      "call. Output `model.glb`, to import with `import_mesh`.",
            "models": [
                {"id": "img2threejs", "label": "img2threejs (local forge)",
                 "note": "Vendored skill (.claude/skills/img2threejs). Staged pipeline with "
                         "quality gates; the resulting GLB is imported afterwards.",
                 "cost_usd": 0.0},
            ],
        },
        {
            "id": "runware",
            "label": "Runware",
            "paid": True,
            "detail": "A hosted 3D model turns the reference image into a textured mesh. Paid, "
                      "and requires confirm=True. The front shows the same identifiers and prices "
                      "(`app/src/lib/catalog.ts`): a test keeps them equal.",
            "models": [
                {"id": air.TRIPO,
                 "label": "Tripo v3.1",
                 "note": "Quad meshes, adjustable faceLimit: the best fit when topology matters.",
                 "cost_usd": 0.4},
                {"id": air.HUNYUAN_PRO,
                 "label": "Hunyuan 3D 3.1 Pro",
                 "note": "Best geometry on complex organic shapes.",
                 "cost_usd": 0.5},
                {"id": air.HUNYUAN_RAPID,
                 "label": "Hunyuan 3D 3.1 Rapid",
                 "note": "The same family, faster and cheaper.",
                 "cost_usd": 0.25},
                {"id": air.TRELLIS,
                 "label": "TRELLIS.2",
                 "note": "Billed by compute time: the cheapest to iterate.",
                 "cost_usd": 0.15},
            ],
        },
        {
            "id": "tripo",
            "label": "Tripo (direct API)",
            "paid": True,
            "detail": "The Tripo API itself, where Runware hosts only one of its models. Requires "
                      "`TRIPO_API_KEY` in `.env` and confirm=True; billing is in Tripo credits (1 "
                      "credit = $0.01) and the prices below are those of the request the studio "
                      "sends: `cost_usd` with standard texture, `cost_detailed_usd` in detailed "
                      "quality. P2's native quads come out as GLB here, where Runware forces FBX, "
                      "and cost $0.05 more.",
            "models": [
                {"id": model["id"], "label": model["label"], "note": model["note"],
                 "cost_usd": model["cost_usd"],
                 "cost_detailed_usd": model["cost_detailed_usd"],
                 "faces": model["faces"], "quad": model["quad"]}
                for model in tripo_catalog.models()
            ],
            # A route whose key is missing stays visible, so that one learns it
            # exists, but it says it is not ready.
            "ready": bool(studio().settings.tripo_api_key),
        },
    ]


def tripo_balance() -> dict[str, Any]:
    """The Tripo balance in credits, and what that makes in dollars.

    Read-only and free: `GET /account/balance` consumes nothing. It lets the
    studio say what is left before a generation, and refuse to start one when
    the account is dry -- rather than hitting the refusal in the middle of a
    chain.
    """
    # A bare client, not a `Context`: the balance belongs to the account, with
    # no project, database or store to open.
    with TripoClient(studio().settings.tripo_api_key) as client:
        balance = client.balance()
    return {**balance, "usd_per_credit": tripo_catalog.USD_PER_CREDIT}


def mesh_formats() -> list[str]:
    """The extensions accepted on import."""
    return list(MESH_SUFFIXES)


def _source(path: str) -> Path:
    source = Path(path).expanduser()
    if not source.is_file():
        raise NotFound(f"file not found: {source}")
    if source.suffix.lower() not in MESH_SUFFIXES:
        raise ServiceError(f"unrecognized format: {source.suffix or '(no extension)'} "
                           f"(expected {', '.join(MESH_SUFFIXES)})")
    size = source.stat().st_size
    if size == 0:
        raise ServiceError(f"{source.name} is empty")
    if size > MAX_MESH_BYTES:
        raise ServiceError(f"{source.name} weighs {size // (1024 * 1024)} MB: beyond "
                           f"{MAX_MESH_BYTES // (1024 * 1024)} MB, it is almost always a wrong "
                           "file")
    return source


def import_mesh(path: str, *, project: str = "imports", name: str = "",
                subject: str = "", attach: bool = True,
                replace: bool = False) -> dict[str, Any]:
    """Bring an external mesh into the store, and attach it to a character.

    - `path`: the `.glb` / `.gltf` / `.fbx` / `.obj` made elsewhere.
    - `name`: the entity the mesh belongs to. Default: the file name.
    - `attach`: write `3d/<entity>/<entity>.glb` and make the mesh visible in
      the library by attaching it to a character. An unattached mesh would stay
      invisible (see `store/library.py`).
    - `replace`: replace the mesh of a character that already has one. Without
      it, the import is refused: overwriting a valid mesh by mistake costs a
      full redo. This is how the agent delivers a rigged and animated entity:
      the original bare mesh stays attached, as `<entity>-bare.glb`.

    What the file carries is surveyed on entry -- bones, animations, share of
    canonically named bones -- and the mesh is copied into the folder's Godot
    project when it has one (`res://characters/<entity>/`).

    Free and local: no network call, no model involved.
    """
    source = _source(path)
    st = space(project)
    entity = _slug(name or source.stem)
    label = name.strip() or source.stem

    with Context(project=project, settings=st.settings, db=st.db, store=st.store) as ctx:
        asset = ctx.store.put_bytes(
            source.read_bytes(), source.suffix.lower(), kind="mesh",
            meta={"role": "imported_mesh", "source": source.name,
                  "format": source.suffix.lower().lstrip("."),
                  "project": project, "entity": entity,
                  "bytes": source.stat().st_size},
        )
        ctx.db.save_asset(asset)
        character = None
        if attach:
            character = _attach(ctx, project, entity, label, subject, asset.id,
                                source=source, replace=replace)
            _export_godot(ctx, st.paths.godot, character)

    st.librarian.sync_project(project)
    from . import briefing, workspace

    briefing.write_briefing(project)
    workspace.write_report(project)

    located = st.librarian.project_dir(project) / "3d" / entity
    return {
        "asset_id": asset.id,
        "project": project,
        "entity": entity,
        "path": str(st.store.path_for(asset.id)),
        "library": str(located) if attach else None,
        "attached": attach,
        "character": character.spec.id if character else None,
        "state": character.state.value if character else None,
        "bones": len(character.rig3d.bones) if character and character.rig3d else 0,
        "animations": list(character.rig3d.animations) if character and character.rig3d else [],
        "rig": character.rig3d.normalization_report if character and character.rig3d else "",
        "godot": character.exports.get("mesh") if character else None,
        "size_bytes": source.stat().st_size,
        "next": ("render_sprites(mesh_asset_id=...) to turn it into 2D sprites"
                 if attach else "the mesh is in the store, but attached to no "
                                "character: the library does not file it"),
    }


def _rig_facts(source: Path, archetype) -> tuple[list[str], list[str], str]:
    """What a mesh carries: its bones, its animations, and a summary of them.

    A measurement, not a verdict: a building animated by parts has no bones,
    and a creature outside the archetypes has no canonical names -- both are
    valid deliveries. The report says what is there; the review judges.
    """
    from ..domain.gltf import godot_animation, inspect_rig
    from ..domain.skeleton import normalize

    if source.suffix.lower() not in (".glb", ".gltf"):
        return [], [], f"format {source.suffix.lower().lstrip('.')}: bones and animations not read"
    try:
        info = inspect_rig(source)
    except Exception as exc:
        # The inventory informs; a file it cannot read still comes in, and the
        # review will tell whether it is worth anything.
        return [], [], f"file unreadable by the inventory: {exc}"
    animations = list(info.animation_names)
    # The names as Godot will show them: `walk_loop` becomes a looping `walk`.
    shown = []
    for raw in animations:
        name, loops = godot_animation(raw)
        shown.append(f"{name} (loop)" if loops else name)
    parts = [f"{len(animations)} animation(s)" + (f": {', '.join(shown)}"
                                                  if animations else "")]
    if info.is_rigged:
        report = normalize(info.bones, archetype)
        canonical = len(set(report.mapping.values()))
        parts.insert(0, f"{len(info.bones)} bones, {canonical} with a canonical name "
                        f"({archetype.value})")
    else:
        parts.insert(0, "no bones")
    return list(info.bones), animations, "; ".join(parts)


def _attach(ctx: Context, project: str, entity: str, label: str, subject: str,
            asset_id: str, *, source: Path, replace: bool) -> Character:
    """Attach the mesh to a character, creating it if needed.

    An imported mesh is not a *valid* mesh: nobody has looked at it. It is
    therefore left "needs review" rather than "done" -- the same rule as for a
    doubtful 2D split.
    """
    existing = ctx.db.get_character(project, entity)
    if existing is not None and existing.rig3d is not None \
            and existing.rig3d.mesh_asset_id and not replace:
        raise ServiceError(
            f"{entity} already has a mesh ({existing.rig3d.mesh_asset_id[:8]}): call again with "
            "replace=true to replace it")

    character = existing or Character(
        spec=CharacterSpec(id=entity, name=label, subject=subject or label,
                           pipelines=[Pipeline.MESH_3D]),
        style_pack_id=f"{project}-free",
        state=JobState.NEEDS_REVIEW,
    )
    previous = character.rig3d
    # The original bare mesh survives the delivery: an agent rigging again
    # starts from it, never from an already rigged mesh.
    origin = (previous.source_mesh_asset_id or previous.mesh_asset_id) if previous else None
    bones, animations, report = _rig_facts(source, character.spec.archetype)
    character.rig3d = Rig(
        archetype=character.spec.archetype, bones=bones,
        mesh_asset_id=asset_id, source_mesh_asset_id=origin or asset_id,
        animations=animations, normalization_report=report,
    )
    # A built character whose mesh is replaced simply goes back to review.
    character.state = JobState.NEEDS_REVIEW
    character.errors = []
    ctx.db.save_character(project, character)
    return character


def _export_godot(ctx: Context, godot: Path, character: Character) -> None:
    """Copy the mesh into the folder's Godot project, if there is one.

    Never create a `project.godot` here: a game folder whose Godot project lives
    elsewhere (a client in `client/`) must not get a second one at its root.
    """
    from ..pipeline.steps.export import ExportGodotMesh

    rig = character.rig3d
    if rig is None or not rig.mesh_asset_id or not (godot / "project.godot").is_file():
        return
    exported = ExportGodotMesh(character.spec.id, rig.mesh_asset_id,
                               godot_project=godot).execute(ctx, force=True)
    character.exports["mesh"] = exported.data["res_path"]
    ctx.db.save_character(ctx.project, character)
