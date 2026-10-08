"""Production operations: everything that costs money, everything that takes time.

None of these functions works in the caller's thread: they enqueue and return.
The worker -- embedded in the MCP server, the API, or run separately --
consumes. **Every paid operation requires `confirm=True`**: the service layer
refuses (`PaymentRequired`, 402 for the API) and states the estimated amount;
it never assumes consent. Everything that can be refused is refused first: a
request for consent followed by a refusal would waste the question.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.recipe import Recipe, load_recipe
from ..runware import catalog as air
from .catalog import find_recipe
from .context import space, studio
from .errors import NotFound, PaymentRequired, ServiceError

# The project that hosts free productions, outside any declared recipe.
FREE_PROJECT = "generation"

MIN_TRAIN_IMAGES = 10
MAX_IMAGES = 16

# What a style LoRA training costs at Runware (1000 steps).
TRAIN_USD = 1.45
# The images an operation pays for without stating them in its parameters: an
# entity's posed reference (`GenerateReferencePose`), and the variants of a
# style exploration (`ExploreStyle`).
REFERENCE_SIZE = (768, 1152)
EXPLORE_SIZE = 1024

# Styles for rendering a mesh into sprites. They differ by render engine as much
# as by lighting -- see `pipeline.steps.render.RenderSprites`.
SPRITE_STYLES = ("normal", "prerender", "pixel")

MAX_DIRECTIONS = 32
MAX_SPRITE_SIZE = 1024
# Bounds on a generated mesh's face count: below, a silhouette no longer reads;
# above, the mesh is no longer a game asset.
MIN_FACES = 500
MAX_FACES = 50000


def resolve_recipe(recipe: str | None) -> tuple[Path, Recipe] | None:
    """Accept either a project name or a recipe path.

    The interface handles names ("my-game"), the CLI and agents handle paths
    ("recipes/my-game.yaml"): both lead to the same file, and no caller has to
    convert first.
    """
    if not recipe:
        return None
    candidate = Path(recipe).expanduser()
    if candidate.suffix == ".yaml" or "/" in recipe:
        if not candidate.exists():
            raise ServiceError(f"recipe not found: {candidate}")
        return candidate, load_recipe(candidate)
    path = Path(find_recipe(recipe)["path"])
    return path, load_recipe(path)


def _style_of(resolved: tuple[Path, Recipe] | None,
              project: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """The style to apply, and the project to file the result in.

    `project` forces the project: a world card belongs to its workspace, with
    or without a recipe -- without a style, the image still goes there.
    """
    if resolved is None:
        return None, project or FREE_PROJECT
    _path, recipe = resolved
    return recipe.style.model_dump(mode="json"), project or recipe.project


# ------------------------------------------------------------------ amounts


def usd(value: float) -> str:
    """An amount as refusals state it: three decimals below one dollar."""
    return f"{value:.3f}" if value < 1 else f"{value:.2f}"


def image_cost(model: str | None, count: int, width: int, height: int) -> float | None:
    """What a batch of images costs at catalog price; `None` outside the catalog.

    One price grid for every image in the studio: the forge's (`forge.estimate`,
    price per megapixel, copied from `app/src/lib/catalog.ts`).
    """
    from . import forge

    try:
        return forge.estimate(model or air.FLUX_DEV, count, width, height)
    except ServiceError:
        return None


def mesh_cost(model: str | None) -> float | None:
    """A mesh's price, read from the 3D routes catalog; `None` outside the catalog."""
    from .meshes import mesh_providers

    wanted = model or air.TRIPO
    return next((float(entry["cost_usd"]) for provider in mesh_providers()
                 for entry in provider["models"] if entry["id"] == wanted), None)


def check_images(count: int, *, model: str | None, width: int, height: int,
                 confirm: bool) -> None:
    """What a batch of images must satisfy before it leaves: a bounded count, then consent.

    The refusal states the batch's amount: that is what the user agrees to. A
    card's concepts (`entities.generate_concepts`) stop here before writing
    anything.
    """
    if not 1 <= count <= MAX_IMAGES:
        raise ServiceError(f"count must be between 1 and {MAX_IMAGES}")
    if confirm:
        return
    cost = image_cost(model, count, width, height)
    if cost is None:
        raise PaymentRequired(f"paid generation ({count} image(s), model {model} not in the "
                              "catalogue: amount unknown): call again with confirm=true after the "
                              "user agrees")
    raise PaymentRequired(f"paid generation (~${usd(cost)} for {count} image(s)): call again with "
                          "confirm=true after the user agrees")


def create_entity(prompt: str, *, name: str = "", image_model: str | None = None,
                  mesh_model: str | None = None, recipe: str | None = None,
                  reference_asset_id: str | None = None,
                  negative_prompt: str = "", face_limit: int = 8000,
                  confirm: bool = False, project: str | None = None) -> dict[str, Any]:
    """Create a 3D entity: reference image, then bare mesh.

    PAID: the mesh (~$0.15-1.25 depending on `mesh_model`, see
    `mesh_providers`), plus the reference image when none is given (~$0.006).
    Explicit consent is required. `face_limit` bounds the face count. Rig and
    animations are not made here: they are handed to an agent.
    """
    if not prompt.strip() and not reference_asset_id:
        raise ServiceError("a prompt or a reference image is needed")
    if not MIN_FACES <= face_limit <= MAX_FACES:
        raise ServiceError(f"face_limit must be between {MIN_FACES} and {MAX_FACES}")
    resolved = resolve_recipe(recipe)
    if not confirm:
        mesh = mesh_cost(mesh_model)
        image = 0.0 if reference_asset_id else image_cost(image_model, 1, *REFERENCE_SIZE)
        if mesh is None or image is None:
            unknown = mesh_model if mesh is None else image_model
            raise PaymentRequired(f"paid 3D entity (model {unknown} not in the catalogue: amount "
                                  "unknown): call again with confirm=true after the user agrees")
        raise PaymentRequired(f"paid 3D entity (~${usd(mesh + image)}): call again with "
                              "confirm=true after the user agrees")

    style, project = _style_of(resolved, project)
    job = space(project).queue.enqueue("create_entity", {
        "prompt": prompt, "name": name,
        "image_model": image_model, "mesh_model": mesh_model, "style": style,
        "reference": reference_asset_id, "negative_prompt": negative_prompt,
        "face_limit": face_limit,
    }, project=project, step=f"entity:{name or prompt[:24]}")
    return {"queued": [job.id], "project": project}


def generate_image(prompt: str, *, model: str | None = None,
                   negative_prompt: str = "", recipe: str | None = None,
                   reference_asset_id: str | None = None, strength: float = 0.6,
                   pose: str | None = None, width: int = 768, height: int = 1152,
                   count: int = 1, transparent: bool = False,
                   seed: int | None = None, project: str | None = None,
                   entity: str = "", card: str = "", reference: str = "",
                   forge: str = "", confirm: bool = False) -> dict[str, Any]:
    """Generate images freely. PAID (~$0.006 per image with FLUX dev).

    Refused without `confirm`, stating the batch's amount (`check_images`).

    `entity` (the entity of a world card) attaches the batch to the card: the
    images are then its concepts, filed with it (see `service/entities.py`).

    `card` (`<section>/<name>`, a game design card) attaches the batch to that
    card, and `reference` says where the reference image came from: empty,
    `sketch` or `render` (see `service/cards.py`).

    `forge` (the id of an icon forge request) attaches the batch to that
    request: the images are its proposals (see `service/forge.py`).
    """
    if not prompt.strip():
        raise ServiceError("empty prompt")
    resolved = resolve_recipe(recipe)
    check_images(count, model=model, width=width, height=height, confirm=confirm)

    style, project = _style_of(resolved, project)
    if entity:
        step = f"concepts:{entity}"
    elif card:
        from .cards import STEP_PREFIX

        step = f"{STEP_PREFIX}{card}"
    elif forge:
        step = f"forge:{forge}"
    else:
        step = "generate"
    job = space(project).queue.enqueue("generate_image", {
        "prompt": prompt, "model": model, "negative_prompt": negative_prompt,
        "style": style, "reference": reference_asset_id, "strength": strength,
        "pose": pose, "width": width, "height": height, "count": count,
        "transparent": transparent, "seed": seed, "entity": entity,
        # `reference` already holds the image id: its origin gets its own key.
        "card": card, "reference_kind": reference, "forge": forge,
    }, project=project, step=step)
    return {"queued": [job.id], "project": project}


def build(recipe: str, *, characters: list[str] | None = None, force: bool = False,
          godot_project: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Build characters: posed reference, bare mesh, Godot export.

    PAID: ~$0.41 per character (FLUX dev reference + Tripo v3.1 mesh). Steps
    already computed come from the cache and cost nothing, unless `force`: the
    announced amount is a ceiling. Rig and animations are not part of it: they
    are handed to an agent. `godot_project` replaces the folder's Godot project
    as the export target.
    """
    resolved = resolve_recipe(recipe)
    if resolved is None:
        raise ServiceError("no project given")
    path, parsed = resolved

    targets = characters or [c.id for c in parsed.characters]
    known = {c.id for c in parsed.characters}
    unknown = [t for t in targets if t not in known]
    if unknown:
        raise ServiceError(f"unknown characters: {', '.join(unknown)}")
    if not targets:
        raise ServiceError("no character to build in the recipe")
    if not confirm:
        # The models the build uses: FLUX dev for the reference (the style can
        # change its price), Tripo v3.1 for the mesh.
        each = (image_cost(None, 1, *REFERENCE_SIZE) or 0.0) + (mesh_cost(None) or 0.0)
        raise PaymentRequired(f"paid build (at most ~${usd(each * len(targets))} for "
                              f"{len(targets)} entity(ies)): call again with confirm=true after "
                              "the user agrees")

    queue = space(parsed.project).queue
    jobs = [
        queue.enqueue("build_character",
                      {"recipe": str(path), "character": target, "force": force,
                       **({"godot_project": godot_project} if godot_project else {})},
                      project=parsed.project, step=target)
        for target in targets
    ]
    return {"queued": [j.id for j in jobs], "project": parsed.project}


def explore_style(recipe: str, subject: str, count: int = 8,
                  confirm: bool = False) -> dict[str, Any]:
    """Generate variants to explore the art direction.

    PAID (~$0.0013 per variant with FLUX schnell, ~$0.010 for 8): refused
    without `confirm`, stating the amount.
    """
    if not subject.strip():
        raise ServiceError("empty subject: say what the variants show")
    if not 1 <= count <= MAX_IMAGES:
        raise ServiceError(f"count must be between 1 and {MAX_IMAGES}")
    resolved = resolve_recipe(recipe)
    if resolved is None:
        raise ServiceError("no project given")
    _path, parsed = resolved
    if not confirm:
        cost = image_cost(air.FLUX_SCHNELL, count, EXPLORE_SIZE, EXPLORE_SIZE) or 0.0
        raise PaymentRequired(f"paid exploration (~${usd(cost)} for {count} variant(s)): call "
                              "again with confirm=true after the user agrees")
    job = space(parsed.project).queue.enqueue("explore_style", {
        "style": parsed.style.model_dump(mode="json"),
        "subject": subject, "count": count,
    }, project=parsed.project, step="explore")
    return {"queued": [job.id], "project": parsed.project}


def train_style(recipe: str, image_ids: list[str], steps: int = 1000,
                confirm: bool = False) -> dict[str, Any]:
    """Train the style LoRA on approved images (PAID, ~$1.45 per 1000 steps)."""
    if len(image_ids) < MIN_TRAIN_IMAGES:
        raise ServiceError(f"training needs at least {MIN_TRAIN_IMAGES} images")
    resolved = resolve_recipe(recipe)
    if resolved is None:
        raise ServiceError("no project given")
    _path, parsed = resolved
    if not confirm:
        raise PaymentRequired(f"paid training (~${usd(TRAIN_USD)} per 1000 steps, {steps} "
                              "requested): call again with confirm=true after the user agrees")
    job = space(parsed.project).queue.enqueue("train_style", {
        "style": parsed.style.model_dump(mode="json"),
        "images": image_ids, "steps": steps,
    }, project=parsed.project, step="train")
    return {"queued": [job.id], "project": parsed.project}


def render_sprites(mesh_asset_id: str, *, name: str = "", recipe: str | None = None,
                   animations: list[str] | None = None,
                   directions: int = 8, size: int = 128, elevation: float = 30.0,
                   style: str = "normal", palette: int = 32, max_frames: int = 0,
                   fps: int = 12, loop: bool = True) -> dict[str, Any]:
    """Render a 3D mesh into sprite sheets, from N angles.

    Free: everything is local (headless Blender), no call to Runware. An
    animated mesh gives one sheet per animation and per direction --
    `animations` picks some, empty means all; a static mesh gives a turnaround
    of one frame per direction, named `name`.

    - `style`: normal (exact texture colors) | prerender (lighting baked into
      the sprite) | pixel (no anti-aliasing, shared palette).
    - `elevation`: 0 for a side view, 30-45 for isometric, 90 for top-down.
    """
    if style not in SPRITE_STYLES:
        raise ServiceError(f"unknown style: {style} (expected {', '.join(SPRITE_STYLES)})")
    if not 1 <= directions <= MAX_DIRECTIONS:
        raise ServiceError(f"directions must be between 1 and {MAX_DIRECTIONS}")
    if not 16 <= size <= MAX_SPRITE_SIZE:
        raise ServiceError(f"size must be between 16 and {MAX_SPRITE_SIZE} px")
    if not -90.0 <= elevation <= 90.0:
        raise ServiceError("elevation must be between -90 and 90 degrees")

    # The render goes to the project that holds the mesh: its sheets attach to
    # its character, in its database -- never in another project's.
    held = studio().find_asset(mesh_asset_id)
    if held is None:
        raise NotFound(f"mesh {mesh_asset_id} not found")
    resolved = resolve_recipe(recipe)
    if resolved is not None and resolved[1].project != held[0].project:
        raise ServiceError(f"mesh {mesh_asset_id} belongs to project {held[0].project}, not to "
                           f"{resolved[1].project}")
    project = held[0].project
    label = name or f"sprites-{mesh_asset_id[:8]}"

    job = space(project).queue.enqueue("render_sprites", {
        "mesh": mesh_asset_id, "name": label, "animations": list(animations or []),
        "directions": directions,
        "size": size, "elevation": elevation, "style": style,
        "palette": palette, "max_frames": max_frames, "fps": fps, "loop": loop,
    }, project=project, step=f"sprites:{label}")
    return {"queued": [job.id], "project": project}


def sprite_styles() -> list[dict[str, str]]:
    """The render styles and what sets them apart, for an interface."""
    return [
        {"name": "normal",
         "label": "Normal render",
         "description": "Flat lighting, exact texture colors. The fastest, and the right choice "
                        "when the style already comes from the 2D image."},
        {"name": "prerender",
         "label": "Pre-rendered",
         "description": "Three-point lighting, shadows and occlusion. Rendered at twice the size "
                        "then reduced, for clean edges. The lighting is baked into the sprite."},
        {"name": "pixel",
         "label": "Pixel art",
         "description": "No anti-aliasing, rendered at the target size, then a single palette for "
                        "every frame and direction."},
    ]
