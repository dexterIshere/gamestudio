"""The studio's data model.

An entity (`Character`) is described by its `CharacterSpec` and carries its
mesh in a `Rig`: bare at first, then rigged and animated by an agent, who
delivers a GLB carrying all its animations. Its sprite sheets are rendered
from it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .skeleton import Archetype


def _now() -> datetime:
    return datetime.now(UTC)


class Pipeline(StrEnum):
    """What an entity asks for: its mesh, and the sprites rendered from it.

    A recipe or database row that still declares the retired rigged 2D
    (`cutout2d`) still loads; the value is dropped.
    """

    MESH_3D = "mesh3d"      # image -> mesh -> glTF (rig and animations: an agent)
    SPRITES = "sprites"     # animated 3D mesh -> ortho render, N directions -> spritesheet


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"  # output produced, quality to be checked by a human


# --------------------------------------------------------------------------- style


class StylePack(BaseModel):
    """The project's art direction, frozen and reusable.

    While `lora_air` is empty, the pack is exploratory: generations use the
    prompt only. Once the LoRA is trained, everything goes through it, which is
    what keeps a roster of 20 characters consistent.
    """

    id: str
    name: str
    base_model: str = "runware:101@1"  # FLUX.1 [dev]
    prompt_prefix: str = ""
    prompt_suffix: str = ""
    negative_prompt: str = ""
    palette: list[str] = Field(default_factory=list)  # hex, informative + post-process
    lora_air: str | None = None
    lora_weight: float = 1.0
    trigger_word: str | None = None
    reference_images: list[str] = Field(default_factory=list)  # approved asset ids
    created_at: datetime = Field(default_factory=_now)

    def compose_prompt(self, subject: str) -> str:
        parts = [self.prompt_prefix, subject, self.prompt_suffix]
        if self.trigger_word and self.lora_air:
            parts.insert(0, self.trigger_word)
        return ", ".join(p.strip() for p in parts if p and p.strip())

    def lora_config(self) -> list[dict[str, Any]]:
        if not self.lora_air:
            return []
        return [{"model": self.lora_air, "weight": self.lora_weight}]


# ------------------------------------------------------------------------ character


class CharacterSpec(BaseModel):
    """What the user describes: a wanted character, not produced yet."""

    id: str
    name: str
    subject: str                      # natural-language description
    archetype: Archetype = Archetype.BIPED
    pipelines: list[Pipeline] = Field(default_factory=lambda: [Pipeline.MESH_3D])
    directions: int = 8               # number of views for the sprite render
    sprite_size: int = 128
    # normal: exact texture colours | prerender: lighting baked into the
    # sprite | pixel: no antialiasing, a reduced and shared palette.
    sprite_style: Literal["normal", "prerender", "pixel"] = "normal"
    sprite_palette: int = 32          # colours kept in pixel style
    face_limit: int = 8000
    tags: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("pipelines", mode="before")
    @classmethod
    def _known_pipelines(cls, value: Any) -> Any:
        # A pipeline retired from the studio must not make what named it unreadable.
        known = {member.value for member in Pipeline}
        if isinstance(value, list):
            return [entry for entry in value if str(entry) in known]
        return value


class Rig(BaseModel):
    """An entity's mesh and what it carries: bare, or rigged and animated by the agent.

    `Character.rig3d` holds it as soon as the entity has a mesh, rigged or
    not. An older database row may carry retired keys (`dimension`): pydantic
    ignores them on read.
    """

    archetype: Archetype
    bones: list[str]
    # The entity's .glb -- bare, or rigged and animated by the agent.
    mesh_asset_id: str | None = None
    # The original bare mesh. The rigging agent starts from it, never from a
    # mesh already rigged by an earlier attempt.
    source_mesh_asset_id: str | None = None
    # The animations the .glb carries, in file order.
    animations: list[str] = Field(default_factory=list)
    # The survey of the file when it came in (`service/meshes.py`): bone count,
    # share of canonical names, animations. A measure, not a normalisation --
    # the field keeps its name because the front and the databases read it.
    normalization_report: str = ""


class Character(BaseModel):
    """The produced state of a `CharacterSpec`, enriched at each pipeline step."""

    spec: CharacterSpec
    style_pack_id: str
    concept_asset_id: str | None = None      # approved reference image
    # Multi-view images of a retired pipeline: nothing writes them any more,
    # but an existing database may hold some, and curation still protects them.
    turnaround_asset_ids: list[str] = Field(default_factory=list)
    # The entity's mesh as soon as it has one -- bare or rigged (see `Rig`).
    rig3d: Rig | None = None
    spritesheets: dict[str, str] = Field(default_factory=dict)  # anim_direction -> asset
    exports: dict[str, str] = Field(default_factory=dict)       # target -> path
    state: JobState = JobState.PENDING
    errors: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------- recipe


class Recipe(BaseModel):
    """A project's declarative document: style + roster + exports.

    It is the unit of re-execution. Changing a line and running again only
    recomputes what depends on it, thanks to the per-step hashing of the
    pipeline graph.
    """

    version: int = 1
    project: str
    style: StylePack
    characters: list[CharacterSpec] = Field(default_factory=list)
    export_targets: list[str] = Field(default_factory=lambda: ["godot"])
    output_dir: Path = Path("out")


# ----------------------------------------------------------------------------- jobs


class Job(BaseModel):
    id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    state: JobState = JobState.PENDING
    project: str = ""
    step: str = ""
    error: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Asset(BaseModel):
    """A produced file, addressed by the hash of its content."""

    id: str                # truncated sha256
    kind: str              # image | mesh | spritesheet | data | lora | scene
    path: Path
    mime: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
