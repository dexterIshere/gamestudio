"""Orchestration: from an entity's description to its Godot files.

The graph is deliberately explicit rather than generic. An asset pipeline does
not need a DAG engine: it needs the exact order of operations, and the
conditions that trigger them, to be readable in a single file.

Each step goes through its fingerprint cache: re-running a recipe after
changing a single character only recomputes that one.

The graph stops where the work stops being deterministic: at the bare mesh.
Rig and animations, for any entity -- character, creature, building in
parts -- are an agent's work, handed off from a card's workbench
(`service/handoff.py`). The studio's 2D is its concepts; a 2D game receives
sprites rendered from 3D (`render_sprites`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.models import Character, CharacterSpec, JobState, Pipeline, Rig, StylePack
from ..store.folders import project_paths
from .base import Context, StepResult
from .steps.export import ExportGodotMesh, godot_target
from .steps.generation import (
    GenerateMesh,
    GenerateReferencePose,
    is_opaque,
    remove_background_asset,
)

logger = logging.getLogger("gamestudio.graph")

# What a newborn mesh says: bare, waiting for its agent.
BARE = "bare mesh: rig and animations are handed off to an agent"


@dataclass
class BuildReport:
    """Trace of a character build."""

    character_id: str
    steps: list[tuple[str, StepResult]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        return round(sum(result.cost_usd for _, result in self.steps), 4)

    @property
    def needs_review(self) -> bool:
        return any(result.needs_review for _, result in self.steps)

    @property
    def notes(self) -> list[str]:
        return [f"{name}: {note}" for name, result in self.steps for note in result.notes]

    def record(self, name: str, result: StepResult) -> StepResult:
        self.steps.append((name, result))
        return result


def build_character(
    ctx: Context,
    spec: CharacterSpec,
    pack: StylePack,
    *,
    godot_project: Path,
    force: bool = False,
) -> tuple[Character, BuildReport]:
    """Produce an entity of a recipe: its reference in an imposed pose, its bare mesh.

    Rig and animations are not its job: they are handed off to an agent from
    the card's workbench, once the mesh is made.

    `godot_project` must hold a `project.godot`, unless it is the folder of a
    project the studio hosts (see `godot_target`): the refusal comes before
    any spending.
    """
    report = BuildReport(character_id=spec.id)
    character = Character(spec=spec, style_pack_id=pack.id, state=JobState.RUNNING)
    if not ({Pipeline.MESH_3D, Pipeline.SPRITES} & set(spec.pipelines)):
        # Nothing is paid for an entity that asks for no pipeline (an old
        # `cutout2d` recipe ends up here).
        report.errors.append("no pipeline to build: declare mesh3d or sprites")
        character.errors = list(report.errors)
        character.state = JobState.FAILED
        ctx.db.save_character(ctx.project, character)
        return character, report
    godot_project = godot_target(project_paths(ctx.settings, ctx.project), godot_project)

    # --- 1. Reference image in an imposed pose: what the 3D model expects.
    reference = report.record(
        "reference_pose",
        GenerateReferencePose(pack, spec.subject, pose="a_pose").execute(ctx, force=force),
    )
    images = [asset for asset in reference.assets
              if asset.meta.get("role") == "reference_pose"]
    if not images:
        raise RuntimeError("no reference image produced")
    character.concept_asset_id = images[0].id

    # --- 2. The bare mesh, and its copy in the Godot project.
    try:
        character, report = _build_3d(
            ctx, spec, character, report, image_asset_id=character.concept_asset_id,
            godot_project=godot_project, force=force,
        )
    except Exception as exc:
        logger.exception("3D pipeline failed for %s", spec.id)
        report.errors.append(f"3D: {exc}")

    character.errors = list(report.errors)
    if report.errors and not character.exports:
        character.state = JobState.FAILED
    elif report.needs_review:
        character.state = JobState.NEEDS_REVIEW
    else:
        character.state = JobState.DONE

    ctx.db.save_character(ctx.project, character)
    return character, report


def _build_3d(ctx, spec, character, report, *, image_asset_id, godot_project, force):
    mesh = report.record("generate_mesh", GenerateMesh(
        image_asset_id, face_limit=spec.face_limit).execute(ctx, force=force))
    mesh_id = mesh.assets[0].id
    character.rig3d = Rig(archetype=spec.archetype, bones=[], mesh_asset_id=mesh_id,
                          source_mesh_asset_id=mesh_id, normalization_report=BARE)
    exported = report.record("export_godot_mesh", ExportGodotMesh(
        spec.id, mesh_id, godot_project=godot_project).execute(ctx, force=force))
    character.exports["mesh"] = exported.data["res_path"]
    return character, report


# -------------------------------------------------------------------- entities


def entity_slug(value: str) -> str:
    """An entity's identifier: that of its character in the database and of its folders."""
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:40] or "entity"


def create_entity(
    ctx: Context,
    *,
    prompt: str,
    name: str = "",
    image_model: str | None = None,
    mesh_model: str | None = None,
    style: StylePack | None = None,
    reference_asset_id: str | None = None,
    negative_prompt: str = "",
    face_limit: int = 8000,
) -> tuple[Character, BuildReport]:
    """The short path: an entity in 3D, from a description or an image.

    This is the orchestration behind the workbench's 3D step and the MCP tool
    `create_entity`. It ensures what is needed:

    - a clean reference image (A-pose, transparent background): generated in
      an imposed pose, or provided -- and cut out if it is opaque
      (`remove_background_asset`);
    - then the mesh through the chosen 3D model. It comes out bare: its rig
      and animations are handed off to an agent.
    """
    from ..runware import catalog
    from ..service.renders import librarian  # files brief images with their cards

    if not prompt.strip() and not reference_asset_id:
        raise ValueError("describe the entity, or provide a reference image")

    entity_id = entity_slug(name or prompt)
    report = BuildReport(character_id=entity_id)
    notes: list[str] = []

    pack = style or StylePack(id=f"{ctx.project}-free", name="Free style")
    if image_model:
        pack = pack.model_copy(update={"base_model": image_model})

    spec = CharacterSpec(id=entity_id, name=name or entity_id, subject=prompt,
                         pipelines=[Pipeline.MESH_3D])
    character = Character(spec=spec, style_pack_id=pack.id, state=JobState.RUNNING)
    # Redoing an entity's mesh keeps what it exported: the new mesh replaces
    # the old one, it does not erase the rest.
    previous = ctx.db.get_character(ctx.project, entity_id)
    if previous is not None:
        character.exports = dict(previous.exports)

    # --- 1. The reference image: generated in an imposed pose, or provided.
    if reference_asset_id:
        concept_id = _prepare_reference(ctx, report, notes, reference_asset_id)
    else:
        reference = report.record("reference_pose", GenerateReferencePose(
            pack, prompt, pose="a_pose", negative_prompt=negative_prompt).execute(ctx))
        images = [a for a in reference.assets
                  if a.meta.get("role") == "reference_pose"]
        if not images:
            raise RuntimeError("no reference image produced")
        concept_id = images[0].id
    character.concept_asset_id = concept_id

    # --- 2. The bare mesh. If it fails, the reference is already paid for:
    # the error carries that cost, which the worker records on the job instead
    # of losing it.
    try:
        mesh = report.record("generate_mesh", GenerateMesh(
            concept_id, model=mesh_model or catalog.TRIPO, face_limit=face_limit).execute(ctx))
    except Exception as exc:
        exc.cost_usd = report.cost_usd  # type: ignore[attr-defined]
        raise
    character.rig3d = Rig(archetype=spec.archetype, bones=[],
                          mesh_asset_id=mesh.assets[0].id,
                          source_mesh_asset_id=mesh.assets[0].id,
                          normalization_report=BARE)

    # Notes prefixed with "!" are blocking (e.g. matting failed), the others
    # inform.
    character.errors = [n for n in notes if n.startswith("!")]
    report.errors.extend(character.errors)
    if report.needs_review or notes:
        character.state = JobState.NEEDS_REVIEW
    else:
        character.state = JobState.DONE

    ctx.db.save_character(ctx.project, character)
    try:
        librarian(ctx.db, ctx.store, ctx.settings).sync_project(ctx.project)
    except Exception:
        logger.exception("library sync failed")
    # Non-blocking notes join the report for the interface.
    report.outputs["notes"] = notes
    return character, report


def _prepare_reference(ctx: Context, report: BuildReport, notes: list[str],
                       asset_id: str) -> str:
    """Make a provided image usable: cut out if it is opaque.

    Matting is the same as for any reference (`remove_background_asset`), and
    its cost, if any, goes into the report. Returns the concept to turn into 3D.
    """
    path = ctx.store.path_for(asset_id)
    if path is None:
        raise FileNotFoundError(f"reference image {asset_id} not found")
    if not is_opaque(path):
        return asset_id
    try:
        cut, cost = remove_background_asset(ctx, asset_id, meta={"role": "reference_pose"})
    except Exception as exc:
        notes.append(f"! opaque image, matting failed ({exc}): "
                     "provide a cut-out PNG")
        return asset_id
    report.record("remove_background", StepResult(assets=[cut], cost_usd=cost))
    notes.append(f"opaque image: cut out ({cut.meta['matting']})")
    return cut.id
