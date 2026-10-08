# A project's recipe

`<game folder>/.gamestudio/recipe.yaml` describes **what you want**, not how to
make it: the project's style and a roster of entities. `gamestudio init` writes
the first one; it is read and validated by `src/gamestudio/domain/recipe.py`
(models in `src/gamestudio/domain/models.py`). It is the unit of re-execution:
each step carries a fingerprint computed from its inputs, so changing one entity
and running again recomputes — and pays for — that entity only.

Animations are not in it: the agent that rigs and animates each entity sets
them, from the entity's card.

## Full example

```yaml
version: 1
project: my-game

# --- Art direction ---------------------------------------------------------------
style:
  id: my-game-style
  name: My game's style
  base_model: runware:101@1              # FLUX.1 [dev] on Runware
  prompt_prefix: "hand-painted game character, muted earth palette"
  prompt_suffix: "consistent art direction, readable silhouette"
  negative_prompt: "photorealistic, noisy background"
  palette: ["#3b4a3f", "#a68a64", "#d9c8a9"]   # informative
  # Once the LoRA is trained (gamestudio style train):
  # lora_air: gamestudio:my-game-style@1
  # lora_weight: 1.0
  # trigger_word: my-game_style

# --- Values every entity inherits ----------------------------------------------
defaults:
  archetype: biped
  pipelines: [mesh3d]
  face_limit: 8000

# --- The roster ------------------------------------------------------------------
characters:
  - id: ranger
    name: Forest ranger
    subject: "a forest ranger in a green hooded cloak, short bow"
    pipelines: [mesh3d, sprites]
    directions: 8
    sprite_size: 128
    sprite_style: pixel
    sprite_palette: 32
  - id: windmill
    name: Windmill
    subject: "a small wooden windmill with four sails"
    archetype: prop
    face_limit: 4000
    tags: [scenery]
    notes: "the sails turn: animate by parts"

export_targets: [godot]
output_dir: out
```

## The fields

### Top level

| Field | Value | Effect |
|---|---|---|
| `version` | `1` | format version |
| `project` | name | the studio project; defaults to the file name |
| `export_targets` | `[godot]` | read and written back, no other effect today |
| `output_dir` | path | read and written back, no other effect today |

### `style:`

| Field | Effect |
|---|---|
| `id`, `name` | identify the style pack (default: the project name) |
| `base_model` | the Runware AIR of the image model (default `runware:101@1`, FLUX.1 [dev]) |
| `prompt_prefix`, `prompt_suffix` | wrap each entity's `subject` |
| `negative_prompt` | what the model must avoid |
| `palette` | hexadecimal colours, informative |
| `lora_air`, `lora_weight`, `trigger_word` | the style LoRA; while `lora_air` is empty, only the prompts apply |
| `reference_images` | asset ids kept for the style (stored under `style/` in the library, protected) |

### `defaults:` and `characters:`

`defaults:` holds the fields every `characters:` entry inherits; an entry
overrides them for itself only.

| Field | Values | Effect |
|---|---|---|
| `id` | identifier | the entity in the database and its folders; defaults to a slug of `name` |
| `name` | text | displayed name; defaults to `id` |
| `subject` | text | the description that feeds the prompt — concrete and descriptive |
| `archetype` | `biped`, `quadruped`, `winged_biped`, `serpentine`, `prop`, `custom` | tells the rigging agent which skeleton convention applies |
| `pipelines` | `mesh3d`, `sprites` | what the entity needs; `build` produces the A-pose reference and the bare mesh for either. An unknown value is dropped on read |
| `face_limit` | 500 to 50,000 | caps the generated mesh |
| `directions`, `sprite_size`, `sprite_style` (`normal`, `prerender`, `pixel`), `sprite_palette` | integers, style | the sprite settings wanted for the entity. `build` does not render sprites: they are rendered once the entity is animated (the workbench's "Sprites" tool, `render_sprites`), which takes its own parameters — copy these into it |
| `tags`, `notes` | list, text | free |

## Building

```bash
export GAMESTUDIO_HOME=/path/to/gamestudio
cd /path/to/my-game
gamestudio build .gamestudio/recipe.yaml -c ranger --confirm   # one entity
gamestudio build .gamestudio/recipe.yaml --confirm             # the whole roster
gamestudio status .gamestudio/recipe.yaml
```

`build` is **paid**: a reference image (~$0.006) then a mesh (~$0.40 with the
default model, Tripo v3.1 on Runware) per entity. Like every expense in the
studio, it needs explicit confirmation. For an agent:
`enqueue_build(recipe_path, characters=[…], confirm=true)`, after the user has
agreed; the procedure is the `roster` skill.
