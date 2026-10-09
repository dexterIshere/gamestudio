"""MCP server: the studio, driven by an agent.

Exposes over stdio everything an agent needs: inspect the state (projects,
characters, assets, job queue, library), look at the produced images, queue
the long operations, and hand an entity to the agent that will rig and animate
it.

The work itself lives in `gamestudio.service`, shared with the HTTP API and
the CLI: this module only adds the MCP translation -- the descriptions meant
for an agent, the image format, and the conversion of service errors into
readable messages.

Two safeguards shape the API:

- Paid operations require `confirm=True` and state their order of cost in
  their description. An agent does not spend by accident.
- Long operations go through the SQLite queue, never through the MCP call
  itself. The server starts its own worker (can be disabled), so jobs move on
  even when the application is closed.

Start: `gamestudio mcp` -- or automatically by Claude Code through `.mcp.json`.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from mcp.server.mcpserver import Image as McpImage
from mcp.server.mcpserver import MCPServer

from . import __version__
from .jobs.supervisor import WorkerPool
from .service import (
    ServiceError,
    briefing,
    cards,
    catalog,
    connections,
    doctor,
    documents,
    effects,
    entities,
    folders,
    forge,
    handoff,
    images,
    influences,
    jobs,
    library,
    meshes,
    poses,
    produce,
    prompts,
    renders,
    screens,
    sheets,
    showcase,
    skills,
    video,
    workspace,
    world,
)

# The module is named `inbox`, and so is the MCP tool: the alias keeps the
# second from shadowing the first -- which would only show at runtime.
from .service import inbox as inbox_service
from .service import (
    lookdev as lookdev_service,
)
from .service import (
    preview_data as preview_data_service,
)
from .service import (
    screen_comments as screen_comments_service,
)
from .service import (
    trash as trash_service,
)

logger = logging.getLogger("gamestudio.mcp")

# Claude Code only reads the first 2048 characters of these instructions:
# anything beyond is silently lost (`test_survey` keeps them under the limit).
# Only intent -> tool goes here, and the rules no tool carries.
INSTRUCTIONS = """\
Game asset studio: from a world card to an animated 3D entity, exported to Godot.

- Start with `studio_briefing` (real state: projects, paths, skills). The
  project is the one whose folder is the current directory, otherwise the
  briefing's active project.
- "Update a section" (Interface, Icons, Props, Mechanics, Art direction, VFX,
  or a world section; in French « mettre à jour la rubrique … ») = bring it
  back in line with what the game already holds: `shelf_update_brief(project,
  shelf)`, then follow the brief. The game is read, never modified.
- Paid (Runware, Tripo): confirm=true, and only after the user's explicit
  consent. Free first: `mesh_providers` shows the local route.
- Nothing is proven until looked at: an image with `view_asset`, a Godot scene
  validated headless.
- The image of a game scene: `render_scene` (drawn by the engine, offscreen)
  -- prefer it to a screenshot.
- An entity's rig and animations: an agent's work in Blender
  (`entity_animation_brief`, skill `animation`), never a studio tool.
- Visual effect: `effect_reference`, `write_effect`, `preview_effect`,
  `build_effect` -- local and free (skill `vfx`).
- Project documents (notes, ideas, devlog, design, world): `list_documents`,
  `read_document`, `write_document`, `create_document`.
- Produced files: `library_tree` (absolute library paths). Long jobs:
  `queue_status`, `job_detail`.
- A lasting decision is written down (`studio_note_write`): the next chat
  already knows it.
- What no tool exposes (deletions) is the user's gesture: ask them.
"""

server = MCPServer(
    name="gamestudio",
    version=__version__,
    instructions=INSTRUCTIONS,
)

_MAX_PREVIEW = images.MAX_PREVIEW  # longest side of the images returned to the agent

F = TypeVar("F", bound=Callable[..., Any])


def tool(function: F) -> F:
    """Publish a function as an MCP tool, with service errors translated.

    An agent can do nothing with an exception hierarchy: its own mistake must
    come back as a message it can read and fix. Other exceptions remain bugs
    and propagate as they are.
    """

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except ServiceError as exc:
            raise ValueError(str(exc)) from exc

    return server.tool()(wrapper)  # type: ignore[return-value]


# ------------------------------------------------------------------ inspection


@tool
def studio_health() -> dict[str, Any]:
    """State of the environment: API key, Blender, local tools, queue."""
    return catalog.health()


@tool
def studio_briefing(project: str | None = None, refresh: bool = False) -> dict[str, Any]:
    """The studio's context: what is produced, what is waiting, where to look.

    **Call it first, before `studio_health`.** It is the briefing the agents
    launched in the Chats window read at startup (see `context/` at the root,
    and `AGENTS.md`). It holds: the projects and their characters with their
    state, the recent jobs, the declared recipes, the available skills, and
    the library paths.

    - `project`: restrict the detail to one project (the others are still
      counted).
    - `refresh`: rewrite `context/briefing.md`. Leave it false to only read: a
      read writes nothing.

    The hand-written notes are read and written with `studio_notes` and
    `studio_note_write`.
    """
    if refresh:
        briefing.write_briefing(project)
    return briefing.briefing(project)


@tool
def studio_notes() -> list[dict[str, Any]]:
    """The context files (`context/`): present, size, date.

    `identity.md`, `goals.md` and `preferences.md` are written by hand;
    `briefing.md` is regenerated from the database and is not edited.
    """
    return briefing.files()


@tool
def studio_note_read(name: str) -> dict[str, Any]:
    """The text of a context file: `identity.md`, `goals.md`,
    `preferences.md` or `briefing.md`."""
    return briefing.read(name)


@tool
def studio_note_write(name: str, text: str) -> dict[str, Any]:
    """Save a context note. `briefing.md` is refused: it is regenerated.

    This is how an agent keeps `context/` current after a decision: the
    context of the next chats changes because this file changed.
    """
    return briefing.write(name, text)


# ------------------------------------------------------------------- workspace


@tool
def project_folders() -> list[dict[str, Any]]:
    """Projects opened on a folder of the machine, most recent first.

    Such a project keeps everything the studio holds for it in its
    `.gamestudio/` -- recipe, documents, context, database, store, library,
    report, bundles -- and the Godot exports at the root
    (`res://characters/...`). `exists=false`: the folder disappeared or was
    moved -- reopen it at its new location.
    """
    return folders.list_folders()


@tool
def open_project_folder(path: str, name: str = "") -> dict[str, Any]:
    """Open a folder of the machine as a project (absolute path). Free.

    A folder that already holds `.gamestudio/recipe.yaml` is reopened under
    its name, with everything it contains (characters, assets, costs); a blank
    folder gets a minimal recipe, named `name` (or after the folder). Refuses a
    name already taken by a studio project.
    """
    return folders.open_folder(path, name)


@tool
def studio_workspace() -> dict[str, Any]:
    """The workspace: one card per project, with its steps and cost.

    This is the "where does each project stand" view -- the workspace index,
    not the file mirror (see `library_tree` for that). Each card holds its
    category, pin, counters (characters, files, to review, failures, cost) and
    its **steps**: a 2D character, a 3D mesh, an icon sheet, a frame batch, a
    style exploration.

    A project's readable report is written with `workspace_report`, and the
    card is corrected with `workspace_meta`.
    """
    return workspace.index()


@tool
def workspace_project(project: str) -> dict[str, Any]:
    """A project summary: declaration, counters and steps."""
    return workspace.summary(project)


@tool
def workspace_report(project: str) -> dict[str, Any]:
    """Regenerate a project's HTML report and return its path.

    The report is self-contained (styling included): it opens in a browser,
    can be sent, and only holds what the database and the library say. It is
    a document for a human -- an agent rather reads `studio_workspace` or the
    project's JSON file.
    """
    path = workspace.write_report(project)
    entry = workspace.summary(project)
    return {"path": path, "project": project, "counters": entry["counters"],
            "steps": [{"id": step["id"], "title": step["title"],
                       "kind": step["kind"]} for step in entry["steps"]]}


@tool
def workspace_meta(project: str, title: str | None = None,
                   description: str | None = None, category: str | None = None,
                   status: str | None = None, pinned: bool | None = None,
                   logo: str | None = None) -> dict[str, Any]:
    """Correct a project's card: title, description, category, status, pin.

    Only the given fields change. It is the only hand-written part of the
    workspace -- the rest is recomputed. `category`: project | workbench |
    research; `status`: active | dormant | archived. `logo`: absolute path of
    an image (PNG, JPEG, GIF, WebP, SVG) that becomes the logo shown at the top
    of the menu; an empty string removes it.
    """
    if logo is not None:
        if logo:
            workspace.set_logo_from_file(project, logo)
        else:
            workspace.clear_logo(project)
    return workspace.set_meta(project, title=title, description=description,
                              category=category, status=status, pinned=pinned)


@tool
def list_recipes(directory: str = "") -> list[dict[str, Any]]:
    """The projects' recipes, one per folder (`root`, `linked` for a folder
    opened by the user). `directory` reads any folder of recipes instead."""
    return catalog.list_recipes(directory)


@tool
def list_characters(project: str) -> list[dict[str, Any]]:
    """A project's characters, built (or in progress)."""
    return catalog.list_characters(project)


@tool
def character_detail(project: str, character_id: str) -> dict[str, Any]:
    """An entity's whole state: its mesh (bare or rigged), bones, animations,
    sprite sheets, exports."""
    return catalog.character_detail(project, character_id)


@tool
def list_assets(kind: str | None = None, limit: int = 60,
                project: str | None = None) -> list[dict[str, Any]]:
    """The produced files, newest first.

    `kind` filters: image | vector | mesh | spritesheet | lora | scene | data | archive.
    `project` keeps to one project; without it, each entry names its own.
    """
    return catalog.list_assets(kind, limit, project)


@tool
def asset_info(asset_id: str) -> dict[str, Any]:
    """An asset's metadata and path on disk."""
    return catalog.asset_info(asset_id)


@tool
def view_asset(asset_id: str) -> McpImage:
    """Show a produced image (concept, layer, spritesheet, preview, SVG icon)."""
    try:
        return McpImage(data=images.preview_png(asset_id, _MAX_PREVIEW), format="png")
    except ServiceError as exc:
        raise ValueError(f"{exc}: use asset_info for its path") from exc


@tool
def library_tree(project: str, folder: str = "", depth: int = 1,
                 pattern: str = "", limit: int = 200) -> dict[str, Any]:
    """Browse a project's library like a folder, paths included.

    This is how to find a file to use elsewhere -- open it, copy it into a
    Godot project: each entry carries its **absolute path**, which really
    exists (the library is resynced on the way).

    - `folder`: folder to read, relative to the project (`icons/my-sheet`,
      `3d/hero/sprites`). Empty = the project root.
    - `depth`: 1 for the direct content, more to go deeper (6 at most).
    - `pattern`: keep only the files whose path contains this text.

    The layout is stable and documented: `2d/<entity>/`, `3d/<entity>/`,
    `generations/<batch>/`, `icons/<sheet>/`, `style/`. Each sheet or batch
    folder holds a JSON file saying where its files come from.
    """
    return library.tree(project, folder, depth, pattern, limit)


@tool
def library_sync(project: str) -> dict[str, Any]:
    """Resync the readable library from the database and the store."""
    return library.sync(project)


# ------------------------------------------------------------------- job queue


@tool
def queue_status(project: str | None = None) -> dict[str, Any]:
    """State of the queue: counters by state, total cost, latest jobs."""
    return jobs.status(project)


@tool
def job_detail(job_id: str) -> dict[str, Any]:
    """A job in detail: payload, result, error.

    `report` says what was produced, plainly: the cost, the files filed in the
    library with their **absolute path** (`report.files`), what failed, and
    what was produced without being filed (`report.absent`).
    """
    return jobs.detail(job_id)


@tool
def create_entity(prompt: str, name: str = "", image_model: str | None = None,
                  mesh_model: str | None = None, recipe_path: str | None = None,
                  reference_asset_id: str | None = None,
                  negative_prompt: str = "", face_limit: int = 8000,
                  confirm: bool = False) -> dict[str, Any]:
    """Create a 3D entity in one call: A-pose reference, then mesh. PAID.

    - A reference image in an imposed A-pose, transparent background, then a
      mesh through `mesh_model` (default tripo:v3.1@0; also
      tencent:hunyuan-3d@3.1-pro, microsoft:trellis-2@4b). ~$0.15-1.25 per mesh
      depending on the model, plus ~$0.006 for the reference: requires
      confirm=true; without it, the refusal states the amount. The direct-route
      ids (`P2-20260801`, `P1-20260311`, `tripo-v3.1`) go through the Tripo API
      and require `TRIPO_API_KEY`: `mesh_providers` says which are ready.
    - `reference_asset_id`: start from an existing image instead of generating
      (its background is removed if opaque: locally, otherwise by Runware,
      ~$0.0006).
    - `face_limit`: maximum number of faces of the mesh (500-50000).
    The mesh comes out bare: its rig and animations are handed to an agent.
    The entity shows up in list_characters and the library. When it matches a
    world card, go through `entity_concepts` then `entity_realize` instead:
    the card then holds the workbench.
    """
    result = produce.create_entity(
        prompt, name=name, image_model=image_model,
        mesh_model=mesh_model, recipe=recipe_path,
        reference_asset_id=reference_asset_id, negative_prompt=negative_prompt,
        face_limit=face_limit, confirm=confirm)
    return {**result, "follow_up": "job_detail, then character_detail / view_asset"}


@tool
def generate_image(prompt: str, model: str | None = None,
                   negative_prompt: str = "", recipe_path: str | None = None,
                   reference_asset_id: str | None = None, strength: float = 0.6,
                   pose: str | None = None, width: int = 768, height: int = 1152,
                   count: int = 1, transparent: bool = False,
                   seed: int | None = None, confirm: bool = False) -> dict[str, Any]:
    """Generate free images. PAID: requires confirm=true after consent.

    ~$0.006 per image with FLUX dev; without confirm, the refusal states the
    batch amount.
    - `model`: a Runware AIR (default runware:101@1 = FLUX.1 dev; runware:100@1
      = schnell, ~$0.0013; runware:106@1 = Kontext, ~$0.04, guided editing:
      with a reference only).
    - `recipe_path`: apply the project's style (prompt prefix + LoRA).
    - `reference_asset_id` + `strength` (0-1): image-to-image from an asset.
    - `pose`: impose a ControlNet pose (a_pose | t_pose | side_pose).
    Result through the queue: get the asset ids from job_detail, then
    view_asset to look.
    """
    result = produce.generate_image(
        prompt, model=model, negative_prompt=negative_prompt, recipe=recipe_path,
        reference_asset_id=reference_asset_id, strength=strength, pose=pose,
        width=width, height=height, count=count, transparent=transparent, seed=seed,
        confirm=confirm)
    return {**result, "follow_up": "job_detail then view_asset on the produced assets"}


@tool
def render_sprites(mesh_asset_id: str, name: str = "", animations: list[str] | None = None,
                   directions: int = 8, size: int = 128, elevation: float = 30.0,
                   style: str = "normal", palette: int = 32, max_frames: int = 0,
                   recipe_path: str | None = None, fps: int = 12) -> dict[str, Any]:
    """Render a 3D mesh into 2D sprite sheets, from N angles. FREE.

    The 3D-to-2D bridge outside any recipe: pick a library mesh
    (character_detail gives `rig3d.mesh_asset_id` and its `animations`) and
    get one sheet per animation and per direction, plus a JSON atlas.
    `animations` picks some; empty, all of them.

    - `style`: normal (flat light, exact texture colours -- the fastest) |
      prerender (three-point lighting, shadows and occlusion baked into the
      sprite) | pixel (no antialiasing, rendered at the target size, then a
      single palette of `palette` colours for all frames and all directions).
    - `elevation`: 0 for a side view (platformer), 30-45 for RPG-style
      isometric, 90 for a top view.
    - `directions`: 8 is the usual; 1 renders a single front view.
    - `max_frames`: subsamples a long animation. A mesh without animation
      gives one frame per direction anyway, under `name`.

    Everything is local (headless Blender): no network call, no cost.
    """
    return produce.render_sprites(
        mesh_asset_id, name=name, recipe=recipe_path, animations=animations,
        directions=directions, size=size, elevation=elevation, style=style,
        palette=palette, max_frames=max_frames, fps=fps)


@tool
def sprite_styles() -> list[dict[str, str]]:
    """The sprite render styles and what sets them apart."""
    return produce.sprite_styles()


# --------------------------------------------------------------------- skills


@tool
def list_skills() -> list[dict[str, Any]]:
    """The studio's procedures: their name, what they are for, and their state.

    A skill says **in what order, under which conditions, and how to tell it
    is done** -- not what a tool does, the tool descriptions cover that.
    Start with `production-routing`, which routes to the others.

    `vendored: true` marks third-party material (see `docs/THIRD_PARTY_NOTICES.md`):
    it is not maintained here.
    """
    return skills.skills()


@tool
def skills_check() -> dict[str, Any]:
    """Check the skills: frontmatter, current index, mirror for other agents.

    This is what `make check` runs. `ok: false` with `problems` says exactly
    what to fix (`gamestudio skills index`, `gamestudio skills sync`).
    """
    return skills.check()


@tool
def read_skill(name: str) -> dict[str, Any]:
    """A procedure's full text: read it before chaining several tools."""
    return skills.read(name)


@tool
def write_skill(name: str, description: str, body: str = "") -> dict[str, Any]:
    """Add a procedure to the studio: the file, then the index and the mirror.

    Only call it when the user asks: a procedure describes a way of working,
    and the user decides it. `name` gives the title and the folder
    (`.claude/skills/<name>/SKILL.md`); `description` says **when** to follow
    it, in one sentence, and cannot be shorter than 20 characters -- it is what
    shows in the index. An empty `body` writes the skeleton.

    Refuses a name already taken: a procedure is added, never overwritten.
    """
    return skills.create(name, description, body)


# ------------------------------------------------------------------ diagnosis


@tool
def studio_doctor() -> dict[str, Any]:
    """The environment diagnosis: what works, what is missing, what to do.

    Call it when something does not work -- or before promising a capability.
    Each line carries its **fix**, and `ok: false` means the studio cannot work
    here (no project root, data not writable). A warning is not a failure: "no
    Blender" only means "no 3D and no sprites on this machine".
    """
    return doctor.check()


# ---------------------------------------------------------------------- inbox


@tool
def inbox(limit: int = 50) -> dict[str, Any]:
    """What the user dropped for you: screenshots, dragged files.

    The inbox is a shared space at the studio root (`inbox/attachments/`).
    Each entry carries its **relative path**, which is what is read: when the
    user pastes a screenshot into the conversation, the path is what arrives.
    """
    data = inbox_service.summary()
    return {**data, "attachments": data["attachments"][:max(1, limit)]}


@tool
def inbox_add(path: str) -> dict[str, Any]:
    """Drop a file from disk into the inbox, to give it to the user.

    The file is copied, never moved, and its name carries the date and the
    fingerprint of its content: dropping the same file twice returns the same
    path. Useful to hand over a preview, an export or a report where it will
    be looked for.
    """
    return inbox_service.add_file(path)


# ------------------------------------------------------------ MCP connections


@tool
def mcp_connections() -> dict[str, Any]:
    """Which MCP connections are plugged in, where, and whether they start here.

    Three sources, read without changing anything: the repository's
    `.mcp.json` (where the studio declares its own server), Claude Code's
    global configuration, Codex's. Answers "what tools do I have, and why does
    that one not answer".
    """
    return connections.connections()


# ------------------------------------------------------------------ documents


@tool
def list_documents(project: str, folder: str = "") -> list[dict[str, Any]]:
    """A project's documents: character bible, world notes, direction.

    These are the **hand-written** texts (in `.gamestudio/documents/`, next to
    the recipe, so versioned). The generated report is read with
    `studio_workspace`: what is decided is kept, what is computed is redone.

    `folder` picks the shelf -- what each entry of the studio menu shows: empty
    for the Documents page, `notes`, `ideas`, `devlog`, `design/mechanics`,
    `design/interface`, `design/icons`, `design/props`, `design/direction`, or
    `world/<section>` for a world section (`world_sections`).
    """
    return documents.documents(project, folder)


@tool
def read_document(project: str, name: str, folder: str = "") -> dict[str, Any]:
    """The text of a project document (`name`: its id, without `.md`;
    `folder`: its shelf, see `list_documents`)."""
    return documents.read_document(project, name, folder)


@tool
def write_document(project: str, name: str, text: str, folder: str = "") -> dict[str, Any]:
    """Save a project document, created if it does not exist.

    This is where to write what no tool can guess: what the character must
    never do, why the palette changed, what was rejected and why. The next
    chat will reread it -- and so will you, in six months. A devlog entry goes
    in `folder="devlog"`, an idea in `ideas`.
    """
    return documents.write_document(project, name, text, folder=folder)


@tool
def create_document(project: str, title: str, template: str = "blank",
                    folder: str = "") -> dict[str, Any]:
    """Create a document from a template (`document_templates`) in a shelf.

    The title gives the file name. Refuses to overwrite an existing document.
    A world card: `template="card"`, `folder="world/<section>"`.
    """
    return documents.create_document(project, title, template, folder)


@tool
def document_brief(project: str, request: str, folder: str = "",
                   images: list[str] | None = None,
                   names: list[str] | None = None,
                   axes: dict[str, str] | None = None) -> dict[str, Any]:
    """The brief that hands an agent what to create in a section.

    It is what the agent receives when the user says, from a section's "New",
    what they want to see there: the request, the section's template, the
    documents it already has, the game to read, the steps up to
    `create_document` / `write_document`. `folder`: the section
    (`design/interface`, `world/<section>`, `notes`...). Also written under
    `.gamestudio/workspace/briefs/`. `images`: the reference images dropped
    with the request (inbox or absolute paths), which the agent files with the
    card they illustrate; `names`, the names they had. `axes` (a world
    section): the group where the request is made (`{"race": "humans"}`),
    where each created card is filed. Free. Read it, then follow it.
    """
    return handoff.create_brief(project, folder, request, images, names, axes)


@tool
def vfx_brief(project: str, name: str) -> dict[str, Any]:
    """The production brief of a VFX concept (`design/vfx/<name>.md`).

    It is what the agent receives when the user hands it a concept from the
    VFX page: the target `res://vfx/<name>/`, the procedure, the done
    criterion, and the copied concept. Read and follow it when it is handed to
    you.
    """
    return handoff.brief(project, name)


@tool
def skill_brief(request: str) -> dict[str, Any]:
    """The brief that hands over the writing of a studio procedure (a skill).

    It is what the agent receives when the user asks, from the Control room,
    for a new procedure: the request, the shape of a skill, the procedures
    that already exist, and the steps up to `write_skill`. Also written under
    `data/briefs/`. Free. Read it, then follow it to the end.
    """
    return handoff.skill_brief(request)


@tool
def shelf_update_brief(project: str, shelf: str) -> dict[str, Any]:
    """Update a studio section from what the game already holds.

    "Update the interface section in gamestudio" (in French « mets à jour la
    rubrique interface ») means: bring the section back in line with the game
    -- a project started before the studio, or one whose code moved on without
    it. `shelf`: `interface`, `mechanics`, `direction`, `vfx`, or a world
    section (`world_sections`), by id or by label (“UI”, “Mechanics”,
    “Buildings”).

    Returns the brief, also written under `.gamestudio/workspace/briefs/`: the
    map of the game folder (Godot projects, screens, scripts, themes, the
    game's docs), the cards the section already has, the procedure and the
    expected report. Free and local: the game is read, nothing in it is run.
    Read it, then follow it to the end.
    """
    return handoff.shelf_brief(project, shelf)


@tool
def card_brief(project: str, folder: str, name: str) -> dict[str, Any]:
    """The brief of a chat about a card, written and returned.

    It is what the agent opened from a card's bubble receives (Interface,
    Icons, Props, Mechanics, Art direction, VFX): the card, cited by its path;
    its images by absolute path -- the game render, the user's sketch, the
    generated images; the game scene and its root script; the writing rules. A
    world card (`world/<section>`) adds its filing, its neighbours in the same
    group, its concepts and its 3D. `folder`: the section (`design/interface`,
    `world/civs`), `name`: the card. Also written under
    `.gamestudio/workspace/briefs/`: enough to resume the chat about a card.
    Free. Read it, look at the images, say where the card stands, then wait for
    the user's request.
    """
    return handoff.card_brief(project, folder, name)


@tool
def render_scene(project: str, scene: str = "", name: str = "", folder: str = "",
                 scale: float = 2.0, width: int = 0, height: int = 0, delay: float = 0.5,
                 crop: bool = False, transparent: bool = False, locale: str = "",
                 setup: str = "", godot: str = "") -> dict[str, Any]:
    """Render a game scene to an image, through its engine, offscreen. FREE, local.

    Prefer it to a screenshot: the resolution is chosen (`scale` 2 = twice the
    game's), nothing shows on screen, and the scene is the one named. It is
    the image of an interface, art direction or world card.

    - `scene`: `res://...`, or a path from the game root
      (`client/scenes/hud/top_bar.tscn`); empty = the main scene.
    - `name`: the render's name, and that of the card it illustrates: a new
      render with the same name replaces the previous one.
    - `folder`: the section of the card it illustrates (`design/interface`):
      the image is filed with its cards, under `briefing/<section>/` in the
      library, and the card's page shows it by itself next to the text --
      nothing to paste. The returned `markdown` line cites it elsewhere (a
      devlog, another card). Without `folder`, it is a free image
      (`renders/`).
    - `crop`: crop to the scene root (a panel, a bar).
    - `setup`: GDScript run on the scene before rendering (the body of
      `func setup(scene)`), to open a page or fill a list.
    - `locale`: the render language (`fr`).

    Look at the image (`view_asset`). A scene waiting for a server or for input
    may come out empty or partial: say so in one bullet of the card's game
    survey.
    """
    return renders.render_scene(project, scene, name=name, folder=folder, scale=scale,
                                width=width, height=height, delay=delay, crop=crop,
                                transparent=transparent, locale=locale, setup=setup,
                                godot=godot)


@tool
def card_media(project: str, folder: str, name: str,
                look: bool = False):  # no annotation: a text, and the images if `look`
    """A game design card's workbench: the game as it is, the sketch, the images.

    `folder`: the section (`design/interface`, `design/icons`, `design/props`,
    `design/mechanics`, `design/direction`, `design/vfx`, `world/<section>`),
    `name`: the card. Returns the game's current render (the latest
    `render_scene` named after the card), the sketch the user drew
    (`sketch.png`: its export), the reference images they dropped
    (`references`), the images generated for the card, those still awaited
    (`pending`), the recent failures, and a prompt starter drawn from the card.

    `look=true` also shows the images -- the render, the sketch, then the
    references: that is how to see what the user means without opening a
    file. Free.
    """
    result = cards.media(project, folder, name)
    if not look:
        return result
    shown: list[Any] = [result]
    if result["render"] is not None:
        shown += ["Current game render:",
                  McpImage(data=images.preview_png(result["render"]["asset_id"], _MAX_PREVIEW),
                           format="png")]
    if result["sketch"] is not None and result["sketch"]["png"]:
        shown += ["The user's sketch:",
                  McpImage(data=images.render_png(Path(result["sketch"]["png"]), _MAX_PREVIEW),
                           format="png")]
    for entry in result["references"][:_MAX_LOOK_REFERENCES]:
        shown += [f"The user's reference `{entry['file']}`:",
                  McpImage(data=images.render_png(Path(entry["path"]), _MAX_PREVIEW),
                           format="png")]
    if len(result["references"]) > _MAX_LOOK_REFERENCES:
        shown.append(f"… and {len(result['references']) - _MAX_LOOK_REFERENCES} more "
                     "reference(s): `view_asset` on their path, or read them directly.")
    if len(shown) == 1:
        shown.append("No render, no sketch, no reference: nothing to show.")
    return shown


@tool
def lookdev(project: str) -> dict[str, Any]:
    """The game's universe: each shader as a specimen.

    Returns the specimens (`id`, type, file, uses) and the game's look
    (background, colours, fonts, sky). No verdict: what is in the game is kept,
    what is no longer wanted leaves it -- at the user's request. Free.
    """
    return lookdev_service.universe(project)


@tool
def lookdev_specimen(project: str, specimen: str) -> dict[str, Any]:
    """A specimen in detail: its settings as Godot reads them (`uniform_list`),
    its uses in the game with their values (`presets`), its setup. Starts the
    lookdev bench if needed (offscreen Godot). Free."""
    return lookdev_service.specimen(project, specimen)


@tool
def lookdev_look(project: str, specimen: str, params: dict[str, Any] | None = None,
                 preset: str = "", shape: str = "", yaw: float = 25.0, pitch: float = 12.0,
                 zoom: float = 1.0, device: str = ""):  # no annotation: a text, then the image
    """Look at a specimen, rendered by Godot with these settings. FREE, local.

    `preset`: a use in the game (`lookdev_specimen` lists them), `default` for
    the shader alone; `params`: extra settings ({"tint": [1, 0.8, 0.4, 1]}).
    `yaw`/`pitch`/`zoom` turn the camera around a material, an object, a sky.
    `device` (`phone`, `tablet`, `desktop`): the whole screen of that device --
    an interface shader in the scene of its use, where the game places it.
    """
    shot = lookdev_service.frame(project, specimen, params=params, preset=preset, shape=shape,
                         yaw=yaw, pitch=pitch, zoom=zoom, scale=1.0, format="webp",
                         device=device)
    png = images.webp_to_png(shot["image"], _MAX_PREVIEW)
    return [{"specimen": specimen, "width": shot["width"], "height": shot["height"],
             "ms": shot["ms"]}, McpImage(data=png, format="png")]


@tool
def lookdev_set(project: str, specimen: str, setup: str | None = None,
                shape: str | None = None, preset: str | None = None) -> dict[str, Any]:
    """Write how to present a specimen. The game is never modified.

    - `setup`: for a shader that only lives on its object (water that reads
      its planet's mesh), a GDScript whose `func build() -> Node3D` builds that
      object the way the game does; the bench places, lights and frames it.
      "" goes back to the shape. Check with `lookdev_look` afterwards.
    - `shape` (`sphere`, `plane`, `cube`) and `preset`: the shape and the use
      shown by default.
    """
    return lookdev_service.set_state(project, specimen, setup=setup, shape=shape, preset=preset)


@tool
def lookdev_save(project: str, specimen: str, params: dict[str, Any],
                 preset: str = "") -> dict[str, Any]:
    """Write a shader's settings INTO THE GAME, where the use takes them. Free.

    `preset`: a use (`lookdev_specimen` lists them) -- only its
    `shader_parameter/` lines change; `default` writes the shader's defaults.
    Call it only at the user's request, after looking at the result
    (`lookdev_look` with these `params`).
    """
    return lookdev_service.save(project, specimen, params=params, preset=preset)


@tool
def lookdev_brief(project: str, specimen: str) -> dict[str, Any]:
    """The brief of a chat about a universe shader, written and returned.

    It is what the agent opened from a specimen's bubble receives: the shader
    by its path, its uses in the game with their settings, its thumbnail, and
    how to look at it (`lookdev_look`). Read it, look, say what you see, then
    wait for the request. Free.
    """
    return handoff.lookdev_brief(project, specimen)


@tool
def lookdev_aspect_brief(project: str, aspect: str) -> dict[str, Any]:
    """The brief of a chat about a whole section of the universe, written and returned.

    `aspect`: `colors`, `typography`, or a family of shaders (`interface`,
    `materials`, `sky`, `other`). It is what the agent opened from a section's
    bubble receives: what the game holds for it, with where each thing is
    written (a color's files and names, a font's files and uses), and the
    written art direction by path. Read it, say what you see, then wait for
    the request. Free.
    """
    return handoff.lookdev_aspect_brief(project, aspect)


@tool
def influence_board(project: str, aspect: str) -> dict[str, Any]:
    """The graphic style (`aspect="style"`) or the game type (`aspect="game"`): its
    parts' cards (`parts`: the graphic style's `graphic-style`; the game type's
    `gameplay`, `setting`, `lore`), each influence with what is kept and left and its
    images (each with its `path`, to look at it), the image proposals and their
    status. Free.
    """
    return influences.board(project, aspect)


@tool
def influence_set(project: str, aspect: str, name: str, influence: str = "", kind: str = "",
                  keep: str | None = None, avoid: str | None = None,
                  of: list[str] | None = None,
                  images: list[str] | None = None) -> dict[str, Any]:
    """Name an influence of the graphic style or the game type, or change one. Free.

    `influence`: the id of the one to change, empty to add one. `kind`: work,
    game, film, book, artist, movement, place, blend (`of`: the ids it
    crosses) or other. `keep` / `avoid`: what is kept from it, what is left.
    `images` replaces its images: `asset:<id>` (generated for the aspect's
    card), `ref:<file>` (dropped with it). What is not given is kept.
    """
    return influences.set_influence(project, aspect, name=name, influence=influence, kind=kind,
                                    keep=keep, avoid=avoid, of=of, images=images)


@tool
def influence_propose(project: str, aspect: str, prompt: str, influence: str = "", count: int = 4,
                      model: str = influences.DEFAULT_MODEL, width: int = 1024,
                      height: int = 768, reference: str = "", strength: float = 0.6,
                      why: str = "") -> dict[str, Any]:
    """Propose images that make an influence visible -- or the aspect as a whole
    (`influence=""`: they go to its gallery). FREE: nothing is generated.

    The user sees the proposal in the Universe with its amount and pays it, or
    not; an agent never pays it. `model`: runware:100@1 (FLUX schnell, ~$0.0013
    an image, to explore), runware:101@1 (FLUX dev, ~$0.006), runware:106@1
    (Kontext, ~$0.04, only with `reference="ref:<file>"`, a dropped image).
    `count`: 1 to 4. `why`: one line, shown with it.
    """
    return influences.propose(project, aspect, influence=influence, prompt=prompt, count=count,
                              model=model, width=width, height=height, reference=reference,
                              strength=strength, why=why)


# ----------------------------------------------------- the icon and prop showcase


@tool
def showcase_list(project: str, kind: str) -> dict[str, Any]:
    """The game's icon (`kind="icons"`) or prop (`kind="props"`) showcase.

    Each element by family: an icon is its file (source size, format, uses); a
    prop -- a component, a scene others instance, a StyleBox, a button or
    frame texture -- is drawn by Godot alone, cropped, and a button in each of
    its states (`states`: normal, hover, pressed, disabled; `same`: identical
    to normal, the theme does not tell it apart). No verdict: what is in the
    game is kept, what is no longer wanted leaves it -- at the user's request.
    A `stale` prop is not drawn yet: `showcase_render`. Free.
    """
    return showcase.showcase(project, kind)


@tool
def showcase_render(project: str, force: bool = False) -> dict[str, Any]:
    """Draw the showcase props whose image is missing or stale (all of them with
    `force`). A single offscreen Godot for the whole batch, a few seconds. FREE."""
    return showcase.render(project, force=force)


@tool
def showcase_look(project: str, kind: str, element: str):  # a text, then the images
    """Look at a showcase element: its detail, then its images (each state of a
    button). Free."""
    found = showcase.element(project, kind, element)
    shown: list[Any] = [found]
    for entry in found["images"]:
        try:
            data = images.render_png(Path(entry["path"]), _MAX_PREVIEW)
        except ServiceError as exc:
            shown.append(f"{entry['state'] or found['title']}: {exc}")
            continue
        shown += [f"State “{entry['state']}”:" if entry["state"] else found["title"],
                  McpImage(data=data, format="png")]
    return shown


# -------------------------------------------------------------- the icon forge


@tool
def forge_families(project: str) -> list[dict[str, Any]]:
    """The game's icon families (their folders), and what a new icon borrows from them.

    `profile`: `mode` (`framed` -- a common canvas and a margin -- or
    `cropped`), size, margin, `exemplar` (the icon used as reference) and
    `style` (the family's written style, kept from the previous request).
    Free.
    """
    return forge.families(project)


@tool
def forge_request(project: str, mode: str, folder: str = "", names: list[str] | None = None,
                  description: str = "", style: str | None = None, element: str = "",
                  model: str | None = None, count: int | None = None,
                  reference: str | None = None, strength: float | None = None,
                  confirm: bool = False) -> dict[str, Any]:
    """Order icons from Runware. PAID (~$0.006 per image with FLUX dev): confirm=true
    only after the user's explicit consent, amount stated.

    - `mode="one"`: a new icon; `folder` (the family, from the game root:
      `forge_families`), `names=["name"]`, `description` (what it shows,
      preferably in English), `count` variants (1 to 4, 2 by default).
    - `mode="set"`: several icons at once; `names` (2 to 16, in the sheet's
      reading order), a shared `description`; a sheet in a grid, to split
      afterwards (`forge_split`).
    - `mode="redo"`: redraw an icon of the game; `element` (its id in
      `showcase_list(kind="icons")`), `description` (what changes).

    The reference (`family` by default, `element` for `redo`, or `none`)
    gives the family's rendering; `style` writes it down (taken from the
    previous request if absent). Without confirm, the answer gives the amount.
    Then follow with `forge_list` (status `running`, then `ready`).
    """
    return forge.request(project, mode=mode, folder=folder, names=names,
                         description=description, style=style, element=element, model=model,
                         count=count, reference=reference, strength=strength, confirm=confirm)


@tool
def forge_list(project: str, closed: bool = False) -> list[dict[str, Any]]:
    """The forge's requests: status (`running`, `ready`, `split`, `adopted`,
    `failed`, `empty`), proposals (`candidates`: asset_id and path), pieces of a
    split sheet (`split.pieces`), icons already written into the game
    (`adopted`). Free."""
    return forge.requests(project, closed=closed)


@tool
def forge_look(project: str, demand: str):  # a text, then the images
    """Look at a request's proposals, then at the pieces of its split sheet:
    that is how to choose before adopting. Free."""
    found = forge.detail(project, demand)
    shown: list[Any] = [found]
    for entry in found["candidates"]:
        shown += [f"Proposal `{entry['asset_id']}`:",
                  McpImage(data=images.render_png(Path(entry["path"]), _MAX_PREVIEW),
                           format="png")]
    for piece in (found.get("split") or {}).get("pieces", []):
        shown += [f"Piece {piece['index']} “{piece['name']}”:",
                  McpImage(data=images.render_png(Path(piece["path"]), _MAX_PREVIEW),
                           format="png")]
    return shown


@tool
def forge_split(project: str, demand: str, asset_id: str) -> dict[str, Any]:
    """Split the chosen sheet of an icon set, one icon per cell, named in
    reading order. FREE, local (BiRefNet background removal). Look at the
    pieces (`forge_look`) before adopting."""
    return forge.split(project, demand, asset_id)


@tool
def forge_adopt(project: str, demand: str, picks: list[dict[str, Any]]) -> dict[str, Any]:
    """Write the chosen proposals into the game. FREE, local.

    `picks`: `[{"source": "asset:<id>" | "piece:<n>", "name": "<name>"}]`. Each
    icon has its background removed, is scaled to its family and written as
    PNG into the family's folder; for `redo`, the old one goes to the
    project's trash. Only what the user chose, or accepted. Check in the
    showcase afterwards (`showcase_look`).
    """
    return forge.adopt(project, demand, picks)


@tool
def forge_close(project: str, demand: str) -> dict[str, Any]:
    """Close a finished request: it leaves the page. Free."""
    return forge.close(project, demand)


@tool
def trash(project: str) -> list[dict[str, Any]]:
    """The project's trash: what the showcase removed from the game (or the forge
    replaced), batch by batch, with the original paths. Free."""
    return trash_service.batches(project)


@tool
def trash_restore(project: str, batch: str) -> dict[str, Any]:
    """Put a trash batch back into the game, at its original paths. Refused if
    one of them has been taken since. Free."""
    return trash_service.restore(project, batch)


@tool
def showcase_brief(project: str, kind: str, element: str) -> dict[str, Any]:
    """The brief of a chat about a showcase element, written and returned.

    It is what the agent opened from an icon's or a prop's bubble receives:
    the element and its images by absolute path (each state of a button), its
    family, what uses it, the section's cards. Read it, look, say what you
    see, then wait for the request. Free.
    """
    return handoff.showcase_brief(project, kind, element)


# The references shown at once by `card_media(look=true)`: beyond that, the
# message overflows, and the agent reads them by their path.
_MAX_LOOK_REFERENCES = 8


@tool
def card_reference_add(project: str, folder: str, name: str, path: str,
                        filename: str = "") -> dict[str, Any]:
    """File a reference image with a card. FREE.

    The image is copied next to the card (`<name>.references/`), versioned
    with it; it follows the card when renamed. `path`: absolute, or relative
    to the studio root (the one `inbox` returns). `filename`: the name to give,
    when the path does not say it (the inbox renames what is dropped in it).
    The same content dropped twice is not duplicated. Then say, in the card's
    "References" section, what is kept from it -- by its file name.
    """
    return cards.add_reference_file(project, folder, name, path, filename)


@tool
def card_rerender(project: str, folder: str, name: str) -> dict[str, Any]:
    """Render a card's game image again, with the settings of the last one. FREE, local.

    Same scene, same setup, same scale: the image shows the game as it has
    become. Without a previous render, go through `render_scene` (`name`: the
    card, `folder`: its section). Look at the image afterwards
    (`card_media(look=true)`).
    """
    return cards.rerender(project, folder, name)


# ----------------------------------------------------------- game screen editor


def _screen_view(result: dict[str, Any], look: bool):
    """A screen's state, and its image if it is to be seen."""
    if not look or result.get("preview") is None:
        return result
    return [result, "The screen as its branch draws it:",
            McpImage(data=images.render_png(Path(result["preview"]["path"]), _MAX_PREVIEW),
                     format="png")]


@tool
def screen_state(project: str, folder: str, name: str, look: bool = False):
    """The editor of an interface or props card: its branch, commits, elements.

    `folder`: `design/interface` (a screen) or `design/props` (a button, an
    arrow, a frame, rendered alone and cropped). The scene is edited on a git
    branch of its own (`studio/screen-<card>`, `studio/prop-<card>`), in a copy
    of the game under `.gamestudio/workspace/`, made by the first edit: the
    user's copy does not move, and they merge the branch when the screen suits
    them. `opened`: whether that branch exists yet. `nodes`: the visible
    Controls, their path, type, rectangle in preview pixels, and whether they
    are declared by this scene (`editable`) or another (`shared`: editing them
    also changes the other screens). `look=true` shows the preview. Free.
    """
    return _screen_view(screens.state(project, folder, name), look)


@tool
def screen_open(project: str, folder: str, name: str, scene: str = "",
                look: bool = False):
    """Show a card's screen, ready to edit: its render and its elements. FREE.

    `scene`: `res://…` or a path from the game root; by default the one shown,
    else the card's current render's (`screen_state` lists the game's
    screens). Creates nothing: the first `screen_edit` makes the branch (and
    is refused while the game has uncommitted changes). Refused outside a git
    repository.
    """
    return _screen_view(screens.open_screen(project, folder, name, scene), look)


@tool
def screen_render(project: str, folder: str, name: str, look: bool = False):
    """Redraw the screen: from its branch, or from the game before the first edit. FREE."""
    return _screen_view(screens.render(project, folder, name), look)


@tool
def screen_node(project: str, folder: str, name: str, path: str) -> dict[str, Any]:
    """A screen element and its editable properties, with their written value.

    `path`: the element's path (`screen_state`, `.` for the root). A `null`
    value is the default one (Godot's or the theme's). `icons`: the game's
    icons (`res://`) that can be set on an image property. Free.
    """
    return screens.node_properties(project, folder, name, path)


@tool
def screen_edit(project: str, folder: str, name: str, path: str,
                changes: dict[str, Any], look: bool = False):
    """Write an element's properties into its scene, on the branch, and redraw. FREE.

    `changes`: `{key: value}` among those of `screen_node` -- text, number,
    boolean, colour `#rrggbb[aa]`, vector `[x, y]`, image `res://…`; `null`
    restores the default value. Each edit is a commit on the branch (the first
    one creates it); a scene
    that no longer draws is returned as it was. Look at the screen afterwards
    (`look=true`).
    """
    return _screen_view(screens.edit(project, folder, name, path, changes), look)


@tool
def screen_comments(project: str, folder: str, name: str) -> dict[str, Any]:
    """The user's comments on a screen's elements: what they want of each one. Free.

    Each: `id`, the element (`path`, `name`, `type`, `file`), `text`, and
    `state` -- `saved` (kept), `queued` (waiting for the screen's agent),
    `sent` (the agent is on it), `done`. `session`: the screen agent's tab.
    """
    return screen_comments_service.comments(project, folder, name)


@tool
def screen_comment_add(project: str, folder: str, name: str, path: str,
                       text: str) -> dict[str, Any]:
    """Save a comment on a screen element (`path`, from `screen_state`). Free.

    Saved, not sent: the user sends comments to the screen's agent from the
    editor.
    """
    return screen_comments_service.add(project, folder, name, path, text)


@tool
def preview_data(project: str) -> dict[str, Any]:
    """A networked game's preview data: fake server answers for the studio's renders. Free.

    `enabled`, the `routes` written, the `setup` that points the game at the
    fake server, the `misses` of the last render, and the files' `paths`.
    """
    return preview_data_service.state(project)


@tool
def preview_data_brief(project: str) -> dict[str, Any]:
    """Write the brief to fill a game's preview data, and return it. Free.

    For a game whose screens need a server: the agent writes fake answers from
    the game's code and docs, then renders to check.
    """
    return handoff.preview_brief(project)


@tool
def preview_data_enable(project: str, enabled: bool) -> dict[str, Any]:
    """Turn the preview data on or off (off: renders reach the real server). Free."""
    return preview_data_service.set_enabled(project, enabled)


@tool
def screen_undo(project: str, folder: str, name: str, look: bool = False):
    """Undo the screen's last edit (only a studio commit). FREE."""
    return _screen_view(screens.undo(project, folder, name), look)


@tool
def card_generate(project: str, folder: str, name: str, prompt: str,
                   model: str | None = None, reference: str = "", strength: float = 0.6,
                   width: int = 768, height: int = 1344, count: int = 1,
                   negative_prompt: str = "", style: bool = True,
                   confirm: bool = False) -> dict[str, Any]:
    """Generate images for a game design card. PAID.

    ~$0.006 per image with FLUX dev (`model`: a Runware AIR): requires
    confirm=true after the user's consent. `count`: 1 to 4.
    - `reference`: "" (text only), "sketch" (from the user's sketch), "render"
      (from the game's current render) or "ref:<file>" (from one of their
      references, `card_media`); `strength` (0-1) says how far the image
      strays from it.
    - `style`: apply the project's style (prompt prefix, LoRA) if it has one.
    The images are attached to the card (`card_media`); look at them before
    talking about them.
    """
    result = cards.generate(project, folder, name, prompt=prompt, model=model,
                             reference=reference, strength=strength, width=width,
                             height=height, count=count, negative_prompt=negative_prompt,
                             style=style, confirm=confirm)
    return {**result, "follow_up": "job_detail, then card_media(look=true) / view_asset"}


@tool
def world_sections(project: str, create: str = "", icon: str = "") -> list[dict[str, Any]]:
    """A project's world sections (characters, buildings, factions...).

    None exists by default: the user declares them. `create` adds one --
    **only at the user's request** -- with an icon among: character, building,
    place, map, item, creature, faction, book, weapon, vehicle, plant, star. A
    section's cards live in its `folder` (`world/<id>`).
    """
    if create:
        world.create_section(project, create, icon or None)
    return world.sections(project)


# -------------------------------------------------------- a world card's workbench


@tool
def world_entities(project: str) -> list[dict[str, Any]]:
    """The world cards and the entity each one gave rise to.

    An entity (character, building, object...) is born from a card written in
    a world section: the card gives the generation its context. Each line
    gives the section, the card, the entity id (the character's id in the
    database), the chosen concept and the character's state if it exists.
    """
    return entities.entities(project)


@tool
def entity_workbench(project: str, section: str, name: str) -> dict[str, Any]:
    """A world card's workbench (`section`: the section, `name`: the card).

    Returns the prompt the card starts (title + "In short" + "Appearance"),
    the concepts already drawn, the chosen concept, the produced character
    (mesh, bones, animations) and the running jobs. This is the studio's
    order: card, concepts, then 3D -- never a generation without a card.
    `section.axes` gives the section's filing grids, `axes` the card's filing
    (`entity_axes`).
    """
    return entities.workbench(project, section, name)


@tool
def entity_axes(project: str, section: str, name: str,
                values: dict[str, str]) -> dict[str, Any]:
    """File a card in its section's axes (`values`: axis -> value).

    The value is written by id or by label (“Elf”); an empty value detaches
    that axis. **Only the named axes change**: setting the race does not undo
    the faction. An axis the section does not declare, or a value outside its
    grid, is refused -- `world_axes(project, section)` gives the grid.
    """
    return entities.set_axes(project, section, name, values)


@tool
def entity_concepts(project: str, section: str, name: str, prompt: str = "",
                    count: int = 4, model: str | None = None,
                    negative_prompt: str = "", style: bool = True,
                    reference_asset_id: str | None = None, strength: float = 0.6,
                    pose: str | None = None, width: int = 768, height: int = 1152,
                    transparent: bool = False, seed: int | None = None,
                    confirm: bool = False) -> dict[str, Any]:
    """Draw a batch of concepts for a world card. PAID.

    ~$0.006 per image with FLUX dev: requires confirm=true after the user's
    consent; without it, the refusal states the batch amount. Without
    `prompt`, the card speaks. `reference_asset_id` (a concept already drawn)
    + `strength` vary a lead. `style` applies the project's style if it has
    one. The images are filed with the entity (`2d/<entity>/concepts/`); look
    at them with `view_asset`, then choose one with `entity_choose_concept`.
    """
    result = entities.generate_concepts(
        project, section, name, prompt=prompt, model=model,
        negative_prompt=negative_prompt, style=style,
        reference_asset_id=reference_asset_id, strength=strength, pose=pose,
        width=width, height=height, count=count, transparent=transparent, seed=seed,
        confirm=confirm)
    return {**result, "follow_up": "job_detail, view_asset, then entity_choose_concept"}


@tool
def entity_choose_concept(project: str, section: str, name: str,
                          asset_id: str | None = None) -> dict[str, Any]:
    """Choose a card's concept -- the one that will go to 3D.

    Empty `asset_id`: no chosen concept any more. Look at it (`view_asset`)
    before choosing it, and only with the user's consent when they follow the
    work.
    """
    return entities.choose_concept(project, section, name, asset_id)


@tool
def entity_realize(project: str, section: str, name: str,
                   mesh_model: str | None = None, face_limit: int = 8000,
                   confirm: bool = False) -> dict[str, Any]:
    """Take a card's chosen concept to 3D: its mesh.

    Mesh through `mesh_model` (default tripo). PAID ~$0.15-1.25 depending on
    the model: requires confirm=true after consent; without it, the refusal
    states the amount. The direct-route ids (`P2-20260801` for native quads,
    `P1-20260311`, `tripo-v3.1`) go through the Tripo API and require
    `TRIPO_API_KEY`; `mesh_providers` says what is ready and at what price.
    The mesh comes out bare: its rig and animations are then handed to an
    agent (`entity_animation_brief`). For free 3D, forge the GLB locally
    (`local-3d`) and `entity_attach_mesh`.
    """
    result = entities.realize(project, section, name, mesh_model=mesh_model,
                              face_limit=face_limit, confirm=confirm)
    return {**result, "follow_up": "job_detail, then entity_workbench"}


@tool
def entity_attach_mesh(project: str, section: str, name: str, path: str,
                       replace: bool = False) -> dict[str, Any]:
    """Attach a mesh forged elsewhere to a world card (free, local).

    The `.glb` from `img2threejs` or from Blender becomes the entity's mesh;
    `replace=true` replaces an existing mesh -- this is how the rigging and
    animating agent delivers its GLB: bones and animations are surveyed, the
    original bare mesh stays attached (`<entity>-bare.glb`), and the GLB is
    copied into Godot when the project folder has a `project.godot`.
    """
    return entities.attach_mesh(project, section, name, path, replace=replace)


@tool
def entity_animation_brief(project: str, section: str, name: str) -> dict[str, Any]:
    """The brief that hands an agent the rig and animations of a world card.

    It is what the agent receives when the user hands it an entity from its
    workbench: the mesh to rig and animate in Blender, the procedure, the
    expected report, and the copied card. Read it and follow it to the end,
    when it is handed to you.
    """
    return handoff.animation_brief(project, section, name)


@tool
def document_templates() -> list[dict[str, str]]:
    """The document templates on offer, with their label."""
    return documents.templates()


# --------------------------------------------------------- workbench prompts


@tool
def studio_prompts() -> list[dict[str, Any]]:
    """The studio's workbench prompts: reference, accessories, angles.

    A concept ready for 3D does not come from "a knight". It takes an image in
    a known pose, framed head to toe, with no hidden limb -- and on a sheet,
    one element per cell, flat background. These phrases are code, not prose:
    written once and reused.

    Take `apose` for the reference -- the pose `create_entity` asks for --,
    `accessories` for a sheet to split, `multiview` or `turnaround` for a 3D
    mesh that must show its back.
    """
    return prompts.catalogue()


@tool
def render_prompt(prompt_id: str, subject: str, style_prefix: str = "",
                  style_negative: str = "") -> dict[str, Any]:
    """Compose a workbench prompt for a subject: positive, negative, size, pose.

    The result goes as is to `generate_image` (`prompt`, `negative_prompt`,
    `width`, `height`, `pose`) or serves as a reference for `create_entity`.
    Passing the project's style prefix brings the art direction into the
    phrase.
    """
    return prompts.render(prompt_id, subject, style_prefix=style_prefix,
                          style_negative=style_negative)


# --------------------------------------------------------------------- meshes


@tool
def mesh_providers() -> list[dict[str, Any]]:
    """The studio's 3D routes, the free one first, then the paid ones.

    - **local** (`img2threejs`): a procedural mesh built from an image, on the
      machine, with no network and no key. The skill is in
      `.claude/skills/img2threejs`; it outputs a `model.glb`, to bring in with
      `import_mesh`. Cost: zero.
    - **runware** (Tripo v3.1, Hunyuan 3D, TRELLIS): a hosted model turns the
      reference image into a textured mesh. Paid, `confirm=True` required.
    - **tripo** (direct API, `TRIPO_API_KEY`): the models Runware does not
      host -- P2 and its native quads, P1, the H family. Paid, billed in Tripo
      credits; `ready: false` says the key is missing.

    An agent that wants 3D looks here **before** spending: the local route is
    slower, but free, and its result is imported the same way.
    """
    return meshes.mesh_providers()


@tool
def tripo_balance() -> dict[str, Any]:
    """The Tripo balance in credits and dollars -- read-only, free.

    Look at it before starting a generation on the direct route: Tripo freezes
    the credits as soon as the task is created, and refuses if the account is
    empty.
    """
    return meshes.tripo_balance()


@tool
def import_mesh(path: str, project: str = "imports", name: str = "",
                subject: str = "", attach: bool = True,
                replace: bool = False) -> dict[str, Any]:
    """Bring an external mesh (`.glb`, `.gltf`, `.fbx`, `.obj`) into the studio.

    Free and local: no 3D model is called. It is the way in for any mesh
    produced elsewhere -- notably by `img2threejs`, which writes `model.glb`
    without network.

    With `attach=true` (the default), the mesh becomes
    `3d/<entity>/<entity>.glb` in the project's library, and `render_sprites`
    can draw 2D sprite sheets from it. Unattached, it would stay invisible:
    the library only writes what a character claims.

    The character created this way is **to review**: nobody has looked at it.
    Its bones, animations and share of canonical names are surveyed on entry
    (`rig`, `animations` in the answer). `replace=true` is required to replace
    an existing mesh.
    """
    return meshes.import_mesh(path, project=project, name=name, subject=subject,
                              attach=attach, replace=replace)


@tool
def enqueue_build(recipe_path: str, characters: list[str] | None = None,
                  force: bool = False, confirm: bool = False) -> dict[str, Any]:
    """Build a recipe's entities: A-pose reference, bare mesh, Godot export.

    PAID: ~$0.41 per entity (FLUX dev reference, then Tripo v3.1 mesh). The rig
    and animations are not part of it: they are handed to an agent. Requires
    confirm=true after the user's consent; without it, the refusal states the
    amount. Steps already computed are taken from the cache and cost nothing
    (unless force=true).
    """
    result = produce.build(recipe_path, characters=characters, force=force,
                           confirm=confirm)
    return {**result, "follow_up": "queue_status / job_detail"}


@tool
def enqueue_style_explore(recipe_path: str, subject: str, count: int = 8,
                          confirm: bool = False) -> dict[str, Any]:
    """Generate variants to explore the art direction. PAID.

    ~$0.0013 per variant with FLUX schnell (~$0.010 for 8): requires
    confirm=true after the user's consent; without it, the refusal states the
    amount. 1 to 16.
    """
    return produce.explore_style(recipe_path, subject, count, confirm)


@tool
def enqueue_style_train(recipe_path: str, image_ids: list[str], steps: int = 1000,
                        confirm: bool = False) -> dict[str, Any]:
    """Train the style LoRA on approved images.

    PAID: ~$1.45 for 1000 steps. Requires confirm=true after the user's
    consent. 10 images at least, each looked at with view_asset.
    """
    return produce.train_style(recipe_path, image_ids, steps, confirm)


# ----------------------------------------------------------------- local tools


@tool
def detect_pose(asset_id: str) -> dict[str, Any]:
    """Estimate an image's pose (18 OpenPose keypoints), locally with RTMPose.

    Checks that a concept holds the imposed pose (A-pose, T-pose, profile)
    before paying for its 3D -- a measure, before looking. Also reads the pose
    of an imported image. Needs the local tools (pip install -e .[rigtools]).
    """
    return poses.detect_pose(asset_id)


@tool
def import_image(path: str, matting: bool = False,
                 project: str = "imports") -> dict[str, Any]:
    """Import an external image into the store (background removed locally if matting=true).

    The created asset is then treated like a concept drawn by the studio: its
    pose can be read (detect_pose), and in a card's project it can be chosen
    as the card's concept (entity_choose_concept) before going to 3D. An asset
    is only seen from its project: import into the card's project.
    """
    return sheets.import_image(path, matting, project)


@tool
def inspect_sheet(path: str, strategy: str = "auto", gap: float | None = None,
                  min_size: float | None = None, rows: int = 0,
                  columns: int = 0, matting: bool = True) -> dict[str, Any]:
    """Free preview of how a multi-element sheet would be split, writing nothing.

    A sheet is a file that holds several: an SVG of icons, or a PNG (an icon
    sheet, or a frame grid). Returns what `import_sheet` would extract: the
    name and frame of each element, plus the chosen strategy.

    If the count is wrong, `gap` (the largest distance between two pieces of
    one element) fixes it: raising it rejoins a scattered drawing, lowering it
    separates two touching drawings.

    An opaque PNG sheet has its background removed by the local model (free, a
    few seconds); matting=false falls back to background-colour removal,
    instant but rough on a textured background.

    strategy: SVG -> auto | symbols | groups | clusters | single;
    bitmap -> auto | grid | blobs | single (rows/columns impose the grid when
    the cells touch).
    """
    return sheets.inspect_sheet(path, strategy, gap, min_size, rows, columns, matting)


@tool
def import_sheet(path: str, multi: bool = True, project: str = "imports",
                 strategy: str = "auto", gap: float | None = None,
                 min_size: float | None = None, rows: int = 0, columns: int = 0,
                 keep: list[str] | None = None, raster_size: int = 0,
                 matting: bool = True) -> dict[str, Any]:
    """Import a sheet: one file per element if multi=true.

    The path for a file that holds several drawings -- an SVG icon sheet, a
    PNG icon pack, or a spritesheet. Each element becomes a standalone asset,
    filed in `.gamestudio/library/icons/<sheet>/`.

    - multi=false: the file comes in whole, unsplit.
    - keep: keep only these element names (those of `inspect_sheet`).
    - raster_size: for an SVG sheet, also produce one PNG per icon (longest
      side in pixels; needs `pip install -e .[vector]`).
    - matting: background removal of an opaque sheet by the local model (the
      default); false falls back to background-colour removal.

    Free: everything is local, no call to Runware.
    """
    return sheets.import_sheet(path, multi, project, strategy, gap, min_size,
                               rows, columns, keep, raster_size, matting)


@tool
def pose_templates() -> list[dict[str, str]]:
    """The reference poses that can be imposed on generation (ControlNet OpenPose)."""
    return poses.pose_templates()


# -------------------------------------------------------------------- effects


@tool
def effect_reference() -> str:
    """The reference of the effects language: variables, functions (noises,
    shapes, distortions, envelopes, colour) and the spec format. Read it before
    writing a spec; the method per material (fire, gas, liquid, plasma,
    magic...) is the `vfx` skill."""
    return effects.reference()


@tool
def list_effects(project: str) -> list[dict[str, Any]]:
    """A project's visual effects: spec, validity, latest filed render."""
    return effects.effects(project)


@tool
def read_effect(project: str, name: str) -> dict[str, Any]:
    """An effect's YAML spec, its path, and its latest render."""
    return effects.read_effect(project, name)


@tool
def write_effect(project: str, name: str, spec: str) -> dict[str, Any]:
    """Write (or create) an effect's YAML spec. FREE.

    The spec is fully validated before it is written: a broken spec is refused
    with the reason (unknown key, unknown function, faulty line) and the
    previous file stays intact. Filed in `.gamestudio/documents/effects/`.
    """
    return effects.write_effect_spec(project, name, spec)


@tool
def preview_effect(project: str, name: str, spec: str | None = None,
                   scale: float = 0.5):  # no annotation: a text AND an image, unstructured
    """Render a scaled-down preview of the effect and show its contact sheet. FREE, local.

    Nothing is filed: this is the iteration loop. `spec` tries a text without
    saving it. The returned image is ALL the frames, on a dark background
    (additive effect) or a checkerboard (opaque effect): look at it before
    concluding -- a render that returns has proven nothing.
    """
    result = effects.preview_effect(project, name, spec=spec, scale=scale)
    result.pop("sheet", None)
    # A contact sheet carries every frame: larger than a preview.
    contact = images.render_png(Path(result["contact_sheet"]), 2 * _MAX_PREVIEW)
    return [result, McpImage(data=contact, format="png")]


@tool
def build_effect(project: str, name: str, validate: bool = True) -> dict[str, Any]:
    """Render the effect at full size, file it and export it to Godot. FREE, local.

    Library: `.gamestudio/library/effects/<name>/` (sheet, JSON atlas with
    pivot and blend mode, contact sheet, frames, spec). Godot:
    `res://effects/<name>/<name>.tscn` (AnimatedSprite2D) and, if the spec
    declares `godot.particles`, `<name>_particles.tscn` (GPUParticles2D).
    `validate` has headless Godot load the scenes, in a throwaway project.
    """
    return effects.build_effect(project, name, validate=validate)


# ---------------------------------------------------------------------- video


@tool
def video_frames(path: str, name: str = "", project: str = "imports",
                 fps: float = 4.0, start: float = 0.0, end: float | None = None,
                 max_frames: int = 120, width: int = 480,
                 cell_width: int = 240, contact: bool = True) -> dict[str, Any]:
    """Extract frames from a reference video (a run capture, a filmed walk).

    FREE and local: ffmpeg and ffprobe, no call to Runware. The batch is filed
    in `.gamestudio/library/video/<batch>/`: the frames under `frames/`, plus
    `frames.json` (where each frame comes from, timestamp included, and where
    the video comes from) and `contact.png`, the whole batch's grid in a
    single image -- enough to judge an extraction at a glance with view_asset.

    - `fps`: requested rate, in frames per second of video. A cycle capture
      needs 12 or 24; an overview makes do with 2 to 4.
    - `start` / `end`: range in seconds (`end` omitted: to the end).
    - `max_frames`: the batch's cap. If it bites, the rate drops and the range
      stays whole: a ten-minute capture capped at 120 frames gives 120 frames
      spread over the ten minutes, never the first four seconds.
    - `width`: frame width, never upscaled.

    An unreadable file is refused before anything is extracted.
    """
    return video.extract_frames(
        path, name=name, project=project, fps=fps, start=start, end=end,
        max_frames=max_frames, width=width, cell_width=cell_width, contact=contact)


# ------------------------------------------------------ what the interface sees


@tool
def read_recipe(project: str) -> dict[str, Any]:
    """A recipe's text, as it is on disk, and its path.

    The recipe gives the style, the declared characters, their pipelines and
    their directions: it is the project's plan, to read before changing it
    (skill `roster`).
    """
    return catalog.recipe_source(project)


@tool
def job_history(project: str | None = None, state: str | None = None,
                limit: int = 50) -> list[dict[str, Any]]:
    """The job history, filtered by project and by state.

    `state`: pending | running | done | failed | needs_review. What the
    interface's job log shows; `job_detail` gives one in full.
    """
    return jobs.listing(project, state, limit)


@tool
def export_bundle(project: str, character_id: str) -> dict[str, Any]:
    """Gather everything a character produced into an archive (free, local).

    The mesh and its animations, sprite sheets, concept and Godot folders with
    their `res://` tree, plus a `manifest.json`, under
    `.gamestudio/exports/<entity>.zip` -- rewritten on every call.
    """
    return catalog.character_bundle(project, character_id)


@tool
def library_projects() -> list[dict[str, Any]]:
    """The projects present in the library, with their volume."""
    return library.projects()


@tool
def library_manifest(project: str, folder: str, name: str) -> dict[str, Any]:
    """The descriptive JSON of a library folder.

    `sheet.json` (the dimensions of each element), `batch.json` (a batch's
    prompt, model, seed) -- what says where a folder's content comes from,
    without opening its files.
    """
    return library.read_document(project, folder, name)


@tool
def asset_holders(asset_id: str) -> list[str]:
    """What holds an asset: a character, a style pack, a card, an effect.

    A held asset is neither moved nor deleted; it is the reason for a refusal
    from `library_rename` or `library_move`.
    """
    return library.holders(asset_id)


@tool
def library_rename(asset_id: str, name: str) -> dict[str, Any]:
    """Rename a sheet element in the library. Its path changes."""
    return library.rename(asset_id, name)


@tool
def library_move(asset_ids: list[str], project: str, sheet: str) -> dict[str, Any]:
    """File sheet elements into another sheet of the same project.

    The paths change: then cite those `library_tree` returns.
    """
    return library.move(asset_ids, project, sheet)


@tool
def rename_document(project: str, name: str, title: str, folder: str = "") -> dict[str, Any]:
    """Rename a document after a new title, without overwriting an existing document."""
    return documents.rename_document(project, name, title, folder)


@tool
def world_section_update(project: str, section_id: str, label: str | None = None,
                         icon: str | None = None) -> dict[str, Any]:
    """Change a world section's label or icon. Its folder keeps its name."""
    return world.update_section(project, section_id, label=label, icon=icon)


@tool
def world_axes(project: str, section: str = "",
               axes: list[dict[str, Any] | str] | None = None,
               forget: str = "") -> dict[str, Any] | list[dict[str, Any]]:
    """The world's filing axes: the project's, or a section's.

    An axis is a grid of closed values -- “Race”: Human, Elf, Orc -- that
    files cards without ever renaming them; a card carries one value per axis
    of its section (`entity_axes`). **An axis belongs to the project**: a
    section cites those it uses, and correcting it from one corrects it
    everywhere.

    Without `section`: the project's axes, each with the sections using it --
    to reuse before declaring a new one. `forget` forgets an axis no section
    uses any more.

    With `section`: the section; `axes` replaces its list of axes (leave
    `None` to only read). Each entry is the id of a project axis (`"race"`,
    reused as is), or `{"id": ..., "label": ..., "values": [{"id": ...,
    "label": ...}]}` -- with `id`, the project axis is corrected everywhere;
    without `id`, it is inferred from the label, and an axis the project
    already has is reused with only the values it lacks. **Renaming detaches
    no card**; removing a value detaches the cards carrying it in every
    section, dropping an axis only detaches those of the section -- `detached`
    says how many, `detached_in` where.
    """
    if forget:
        return world.forget_axis(project, forget)
    if not section:
        return world.axes(project)
    if axes is not None:
        return world.set_axes(project, section, axes)
    return world.section(project, section)


@tool
def close_project_folder(project: str) -> dict[str, Any]:
    """Remove a folder project from the studio. **Nothing in the folder is erased.**

    Only at the user's request; `open_project_folder` reopens it.
    """
    return folders.close_folder(project)


@tool
def unhide_workspace(project: str = "") -> list[str]:
    """Show again a workspace hidden in the selector, or all if `project` is empty."""
    return folders.unhide(project)


@tool
def pose_template(name: str, width: int = 768, height: int = 1152) -> dict[str, Any]:
    """The keypoints of a reference pose (`pose_templates` lists them)."""
    return poses.pose_template(name, width, height)


@tool
def skills_sync() -> dict[str, Any]:
    """Regenerate the skills index and repair the `.agents/skills` mirror.

    What `skills_check` asks for when it returns `ok: false`: after adding or
    changing a skill, so that other agents find it.
    """
    return {"index": skills.write_index(), "mirror": skills.sync()}


# ------------------------------------------------------------ what stays human

# The service operations the API serves to the interface and that no tool
# exposes, with the reason. `tests/test_agent_parity.py` refuses any API
# operation that is neither a tool nor listed here: a capability added to the
# interface reaches the agent, or says why it does not.
HUMAN_ONLY: dict[str, str] = {
    "documents.delete_document": "deleting a hand-written text is a human gesture",
    "documents.image_file": "shows a document's image in the page: an agent reads the "
                            "file itself, or `view_asset`",
    "effects.delete_effect": "deleting a spec is a human gesture",
    "library.delete": "irreversible, store included: a human gesture",
    "workspace.delete_project": "deletes a whole project: a human gesture",
    "world.delete_section": "removing a section is the user's choice",
    "folders.forget": "the selector's cross: the user's display choice",
    "inbox.delete_attachment": "the inbox is the user's; they empty it",
    "handoff.send": "opens a tab in the Chats window; the agent reads `vfx_brief`",
    "handoff.send_animation": "opens a tab in the Chats window; the agent reads "
                              "`entity_animation_brief`",
    "handoff.send_card": "opens a tab in the Chats window; the agent reads `card_brief`",
    "cards.read_sketch": "the Excalidraw scene is reread in the card's editor; an agent "
                         "looks at its export through `card_media(look=true)`",
    "lookdev.stop_benches": "server shutdown: the benches go with it (and on the exit of "
                            "any process)",
    "lookdev.thumbnail": "the grid's thumbnail; an agent looks through `lookdev_look`",
    "lookdev.font_file": "dresses the page; `lookdev` gives the game's fonts",
    "cards.add_reference": "browser bytes (a dragged or pasted image); the agent has "
                           "`card_reference_add(path)`",
    "cards.remove_reference": "removing a reference the user dropped is their gesture",
    "showcase.delete": "removing an element from the game is the user's gesture (the "
                       "showcase's button); it goes to the trash, `trash_restore` puts it back",
    "showcase.delete_family": "removing a whole family is the user's gesture",
    "forge.image_file": "serves the images to the page; an agent looks through `forge_look`",
    "handoff.send_lookdev": "opens a tab in the Chats window; the agent reads "
                            "`lookdev_brief`",
    "handoff.send_lookdev_aspect": "opens a tab in the Chats window; the agent reads "
                                   "`lookdev_aspect_brief`",
    "influences.remove_influence": "taking an influence off the board is the user's gesture",
    "influences.remove_image": "taking an image off an influence deletes a dropped one: the "
                               "user's gesture; an agent rearranges with `influence_set(images=…)`",
    "influences.add_reference": "dropping a file is the user's gesture; an agent files an "
                                "image with `card_reference_add`, then `influence_set(images=…)`",
    "influences.add_reference_file": "dropping a file is the user's gesture (the shell gives "
                                     "its path); an agent uses `card_reference_add`",
    "influences.pay": "the user pays a proposal in the Universe; an agent proposes "
                      "(`influence_propose`) and never pays",
    "influences.dismiss": "the user's answer to a proposal",
    "direction_chat.stop_all": "server shutdown: no agent answering in the Universe outlives it",
    "direction_chat.draft": "the user asks a model to look at the influences' images and write "
                            "a prompt; an agent looks itself, then `influence_propose`",
    "direction_chat.thread": "the user's own conversation in the Universe; an agent works on "
                             "the board through `influence_board`",
    "direction_chat.send": "the user's own conversation, with the agent the studio drives",
    "direction_chat.stop": "the user's own conversation",
    "direction_chat.reset": "the user's own conversation",
    "handoff.send_showcase": "opens a tab in the Chats window; the agent reads "
                             "`showcase_brief`",
    "showcase.image_file": "serves the image to the page; an agent looks through "
                           "`showcase_look`",
    "screens.warm": "the window draws a section's screens ahead of the user; an agent "
                    "draws the one it works on with `screen_open`",
    "activity.now": "the window's progress bar; an agent knows what it runs itself",
    "handoff.send_preview": "opens a tab in the Chats window; the agent reads "
                            "`preview_data_brief`",
    "screen_comments.send": "opens or types into a tab of the Chats window; the agent "
                            "reads the comments through `screen_comments`",
    "screen_comments.remove": "removing the user's comments is their gesture",
    "screens.preview_file": "serves the preview to the page; an agent looks through "
                            "`screen_state(look=true)`",
    "cards.save_sketch": "the sketch is drawn by hand in the card's editor; an agent "
                         "looks at it through `card_media(look=true)`",
    "effects.create_effect": "`write_effect` creates the spec if it does not exist",
    "inbox.add_bytes": "a browser upload; the agent has `inbox_add(path)`",
    "inbox.read_attachment": "the agent reads the file through the path `inbox` returns",
    "sheets.import_bytes": "a browser upload; the agent has `import_sheet(path)`",
    "workspace.set_logo": "browser bytes; the agent has `workspace_meta(logo=)`",
    "workspace.logo_path": "serves the image to the interface; `workspace_project` carries "
                           "the logo",
    "workspace.read_meta": "carried by `workspace_project`",
    "catalog.asset_path": "serves the file to the interface; `asset_info` returns the path",
    "handoff.send_create": "opens a tab in the Chats window; the agent reads "
                           "`document_brief`",
    "handoff.send_skill": "opens a tab in the Chats window; the agent reads `skill_brief`",
}


# ------------------------------------------------------------------ resources

# The same texts, as resources: a client that offers them for mention
# (`@gamestudio:...`) gives them without a tool call.


@server.resource("gamestudio://briefing", name="briefing", mime_type="text/markdown",
                 description="The studio's state: projects, characters, jobs, skills.")
def briefing_resource() -> str:
    return briefing.briefing()["markdown"]


@server.resource("gamestudio://notes/{name}", name="note", mime_type="text/markdown",
                 description="A context note: identity.md, goals.md, preferences.md.")
def note_resource(name: str) -> str:
    return str(briefing.read(name)["text"])


@server.resource("gamestudio://skills/{name}", name="skill", mime_type="text/markdown",
                 description="The full procedure of a deliverable.")
def skill_resource(name: str) -> str:
    return str(skills.read(name)["text"])


# ---------------------------------------------------------------------- start


def run(workers: int = 1) -> None:
    """Start the server over stdio, with its background workers."""
    logging.basicConfig(level=logging.INFO)
    pool = WorkerPool(workers)
    pool.start()
    try:
        server.run("stdio")
    finally:
        pool.stop()
