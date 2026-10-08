"""Loading and writing recipes (project YAML documents).

A recipe describes what is wanted, not how to make it: the style, the roster,
the export targets. It is the studio's unit of re-execution -- "twenty
characters in this style" fits in this file. Animations are not in it: the
agent keys them on each entity, from its card.

A recipe that still declares `clips:` loads without error: the key is ignored
and disappears on the next write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import CharacterSpec, Pipeline, Recipe, StylePack
from .skeleton import Archetype


def load_recipe(path: Path) -> Recipe:
    """Read a YAML recipe and validate it."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    style_data = raw.get("style") or {}
    style_data.setdefault("id", raw.get("project", "default"))
    style_data.setdefault("name", style_data["id"])
    style = StylePack.model_validate(style_data)

    characters: list[CharacterSpec] = []
    defaults = raw.get("defaults") or {}
    for entry in raw.get("characters", []):
        merged: dict[str, Any] = {**defaults, **entry}
        merged.pop("clips", None)
        merged.setdefault("id", _slugify(merged.get("name", "character")))
        merged.setdefault("name", merged["id"])
        if isinstance(merged.get("archetype"), str):
            merged["archetype"] = Archetype(merged["archetype"])
        # An unknown pipeline (the retired `cutout2d`) is dropped: the recipe
        # stays readable.
        known = {member.value for member in Pipeline}
        merged["pipelines"] = [
            p if isinstance(p, Pipeline) else Pipeline(p)
            for p in merged.get("pipelines", ["mesh3d"]) if str(p) in known
        ]
        characters.append(CharacterSpec.model_validate(merged))

    return Recipe(
        version=int(raw.get("version", 1)),
        project=raw.get("project", path.stem),
        style=style,
        characters=characters,
        export_targets=raw.get("export_targets", ["godot"]),
        output_dir=Path(raw.get("output_dir", "out")),
    )


def write_recipe(recipe: Recipe, path: Path) -> Path:
    payload = {
        "version": recipe.version,
        "project": recipe.project,
        "style": recipe.style.model_dump(mode="json", exclude_none=True,
                                         exclude={"created_at"}),
        "characters": [
            character.model_dump(mode="json", exclude_none=True)
            for character in recipe.characters
        ],
        "export_targets": recipe.export_targets,
        "output_dir": str(recipe.output_dir),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path


def _slugify(value: str) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "character"


TEMPLATE = """\
version: 1
project: {project}

# --- Art direction ------------------------------------------------------------
# While `lora_air` is empty, only the prompts apply. Once the LoRA is trained
# (gamestudio style train), fill in `lora_air` and `trigger_word`: that is what
# makes the whole roster consistent.
style:
  id: {project}-style
  name: {project} style
  base_model: runware:101@1
  prompt_prefix: "game character sprite, clean flat colors, soft cel shading"
  prompt_suffix: "consistent art direction, readable silhouette"
  negative_prompt: "photorealistic, 3d render, noisy background"
  # lora_air: gamestudio:{project}-style@1
  # trigger_word: {project}_style

# --- Roster defaults ----------------------------------------------------------
defaults:
  archetype: biped
  pipelines: [mesh3d]
  directions: 8
  sprite_size: 128
  face_limit: 8000

# --- The roster ---------------------------------------------------------------
characters:
  - id: hero
    name: Heroine
    subject: "a young knight in worn leather armor, short red hair, carrying a short sword"
  - id: villager
    name: Villager
    subject: "an old villager in a brown tunic, bald, holding a lantern"

export_targets: [godot]
output_dir: out
"""
