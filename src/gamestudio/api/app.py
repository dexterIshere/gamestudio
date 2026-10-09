"""The studio's HTTP API: the surface the desktop application consumes.

Routes hold no business logic -- it lives in `gamestudio.service`, shared with
the MCP server and the CLI. This module only translates: request parameters to
arguments, service errors to HTTP codes (402 for a spending without
`confirm`), and event streams to SSE.

What is paid (Runware, Tripo) and what takes minutes (a mesh, a Blender sprite
render) is queued: the route returns with the job id. The rest runs within the
request, including local operations of a few seconds: rendering a scene or a
specimen through Godot, editing a screen on its branch, splitting a sheet,
extracting a video's frames, previewing or building an effect. Synchronous
routes run in FastAPI's thread pool: the event loop stays free meanwhile.

Served files (`_file`) carry a policy that forbids any script: the token
travels in their URL, and an SVG opened full page on the API's origin would
run its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..jobs.supervisor import WorkerPool
from ..service import (
    ServiceError,
    activity,
    briefing,
    cards,
    catalog,
    connections,
    direction_chat,
    doctor,
    documents,
    effects,
    entities,
    folders,
    forge,
    handoff,
    images,
    inbox,
    influences,
    jobs,
    library,
    lookdev,
    meshes,
    poses,
    preview_data,
    produce,
    prompts,
    screen_comments,
    screens,
    sheets,
    showcase,
    skills,
    trash,
    video,
    views,
    workspace,
    world,
)
from ..service.errors import NotFound
from ..terminal import routes as terminal_routes
from ..terminal.session import manager as terminals
from .auth import Guard

logger = logging.getLogger("gamestudio.api")

ROOT = Path(__file__).resolve().parents[3]
# Interface served in production: the built front.
FRONT_DIR = ROOT / "app" / "dist"

# Number of workers started with the server. 0 disables them, to run
# `gamestudio worker` elsewhere. Set by `gamestudio serve --no-worker`.
WORKER_COUNT = int(os.environ.get("GAMESTUDIO_WORKERS", "1"))

_pool = WorkerPool(WORKER_COUNT)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the workers with the server and stop them with it."""
    _pool.start()
    terminals.loop = asyncio.get_running_loop()
    try:
        yield
    finally:
        _pool.stop()
        # The Chats tabs are children of the server: they go with it. Their
        # screen is written to disk first, so that an interrupted tab can be
        # reread -- and resumed -- on the next start, instead of vanishing.
        terminals.shutdown()
        # The lookdev benches are offscreen Godot instances: they go too, and
        # so does an agent answering in the Universe.
        lookdev.stop_benches()
        direction_chat.stop_all()


app = FastAPI(title="gamestudio", version=__version__, lifespan=lifespan)
# The interface runs in a web view served from another port in development
# (Vite): without CORS, no call would go through. Opening wide here exposes
# nothing: the server only listens on loopback, and the token (`Guard`, below)
# is the door -- CORS only says who may read a response, not who may send a
# request. The token protects the API; the origin protects nothing.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    # The render time of a lookdev bench image, which the Universe page shows.
    expose_headers=["X-Render-Ms", "X-Bench-Ms"],
)
# Added after CORS, so it runs before it: a request without a token is refused
# before it reaches a route (see `auth.py`).
app.add_middleware(Guard)


@app.exception_handler(ServiceError)
async def _service_error(_request: Request, exc: ServiceError) -> JSONResponse:
    """A caller's fault becomes an HTTP code, not a stack trace."""
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


# What every served file carries: no script (an SVG opened alone would run its
# scripts on the API's origin, where the token is in the URL), and no type
# guessed by the browser. An image shown through `<img>` is not affected.
FILE_HEADERS = {
    "Content-Security-Policy": "script-src 'none'; sandbox",
    "X-Content-Type-Options": "nosniff",
}


def _file(path: str | Path, media_type: str | None = None) -> FileResponse:
    """A file from disk, served under the `FILE_HEADERS` policy."""
    return FileResponse(path, media_type=media_type, headers=FILE_HEADERS)


# The terminal tabs of the Chats window: their HTTP and WebSocket surface lives
# next door, in `terminal/routes.py`, behind the same door (the token) as the
# rest of the API -- a WebSocket carries it in its URL, since it cannot set a
# header.
app.include_router(terminal_routes.router)


# --------------------------------------------------------------------- models


class EntityRequest(BaseModel):
    prompt: str
    name: str = ""
    image_model: str | None = None
    mesh_model: str | None = None
    recipe: str | None = None
    reference_asset_id: str | None = None
    negative_prompt: str = ""
    face_limit: int = 8000
    confirm: bool = False


class ImageRequest(BaseModel):
    prompt: str
    model: str | None = None
    negative_prompt: str = ""
    recipe: str | None = None
    reference_asset_id: str | None = None
    strength: float = 0.6
    pose: str | None = None
    width: int = 768
    height: int = 1152
    count: int = 1
    transparent: bool = False
    seed: int | None = None
    confirm: bool = False


class BuildRequest(BaseModel):
    recipe: str
    characters: list[str] = Field(default_factory=list)
    force: bool = False
    confirm: bool = False


class SpritesRequest(BaseModel):
    """Render a 3D mesh into multi-direction sprite sheets."""

    mesh: str
    name: str = ""
    recipe: str | None = None
    # Empty: every animation of the mesh, in file order.
    animations: list[str] = Field(default_factory=list)
    directions: int = 8
    size: int = 128
    elevation: float = 30.0
    style: str = "normal"
    palette: int = 32
    max_frames: int = 0
    fps: int = 12
    loop: bool = True


class ExploreRequest(BaseModel):
    recipe: str
    subject: str
    count: int = 8
    confirm: bool = False


class TrainRequest(BaseModel):
    recipe: str
    images: list[str]
    steps: int = 1000
    confirm: bool = False


class HandoffRequest(BaseModel):
    """The agent to hand a brief to: a new chat, or an open tab.

    Defined before any route that uses it: with `from __future__ import
    annotations`, a class declared further down would make `request` a
    required query parameter, and the front's JSON would get a 422.
    """

    harness: str = "claude"
    effort: str = ""
    # Empty: a new chat. Otherwise the Chats tab to type the request into.
    session: str = ""
    # What the user writes to the agent, sent after the line pointing to the brief.
    message: str = ""


class CurationRequest(BaseModel):
    asset_ids: list[str] = Field(default_factory=list)
    project: str = ""
    sheet: str = ""
    name: str = ""


class SheetRequest(BaseModel):
    path: str
    multi: bool = True
    project: str = "imports"
    strategy: str = "auto"
    gap: float | None = None
    min_size: float | None = None
    rows: int = 0
    columns: int = 0
    keep: list[str] | None = None
    raster_size: int = 0
    matting: bool = True


class MeshImportRequest(BaseModel):
    """Import a mesh from elsewhere: the path is local, as for a sheet."""

    path: str
    project: str = "imports"
    name: str = ""
    subject: str = ""
    attach: bool = True
    replace: bool = False


class VideoRequest(BaseModel):
    """Extract the frames of a reference video (local ffmpeg, free)."""

    path: str
    name: str = ""
    project: str = "imports"
    fps: float = 4.0
    start: float = 0.0
    end: float | None = None
    max_frames: int = 120
    width: int = 480
    cell_width: int = 240
    contact: bool = True


# ------------------------------------------------------------------ inspection


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {**catalog.health(), "workers": _pool.count,
            "workers_running": _pool.running}


@app.get("/api/recipes")
def recipes() -> list[dict[str, Any]]:
    return catalog.list_recipes()


@app.get("/api/recipes/{project}/source")
def recipe_source(project: str) -> dict[str, Any]:
    return catalog.recipe_source(project)


class FolderRequest(BaseModel):
    path: str
    name: str = ""


@app.get("/api/folders")
def folder_list() -> list[dict[str, Any]]:
    return folders.list_folders()


@app.post("/api/folders")
def folder_open(request: FolderRequest) -> dict[str, Any]:
    return folders.open_folder(request.path, request.name)


@app.delete("/api/folders/{project}")
def folder_close(project: str) -> dict[str, Any]:
    return folders.close_folder(project)


@app.post("/api/workspaces/{project}/forget")
def workspace_forget(project: str) -> dict[str, Any]:
    return folders.forget(project)


@app.post("/api/workspaces/unhide")
def workspace_unhide() -> list[str]:
    return folders.unhide()


@app.get("/api/projects")
def projects() -> list[dict[str, Any]]:
    return library.projects()


@app.get("/api/projects/{project}/characters")
def characters(project: str) -> list[dict[str, Any]]:
    return catalog.list_characters(project)


@app.post("/api/projects/{project}/characters/{character_id}/bundle")
def character_bundle(project: str, character_id: str) -> dict[str, Any]:
    return catalog.character_bundle(project, character_id)


@app.get("/api/projects/{project}/characters/{character_id}")
def character(project: str, character_id: str) -> dict[str, Any]:
    return catalog.character_detail(project, character_id)


# --------------------------------------------------------------------- context


class NoteRequest(BaseModel):
    """The text of a context note, as saved."""

    text: str


@app.get("/api/context")
def context(project: str = "") -> dict[str, Any]:
    """The briefing: projects, jobs, skills, landmarks, and the context notes."""
    return briefing.briefing(project or None)


@app.post("/api/context/refresh")
def context_refresh(project: str = "") -> dict[str, Any]:
    """Regenerate `context/briefing.md` on disk, then return it.

    The write is what matters: an agent launched afterwards in the studio reads
    this file when its session starts, not this request's response.
    """
    path = briefing.write_briefing(project or None)
    return {"path": path, **briefing.briefing(project or None)}


@app.get("/api/context/notes")
def context_notes() -> list[dict[str, Any]]:
    return briefing.files()


@app.get("/api/context/notes/{name}")
def context_note(name: str) -> dict[str, Any]:
    return briefing.read(name)


@app.put("/api/context/notes/{name}")
def context_note_write(name: str, request: NoteRequest) -> dict[str, Any]:
    return briefing.write(name, request.text)


# ------------------------------------------------------------------- workspace


class WorkspaceRequest(BaseModel):
    """A project's declaration. None = leave it untouched."""

    title: str | None = None
    description: str | None = None
    category: str | None = None
    status: str | None = None
    pinned: bool | None = None


@app.get("/api/workspace")
def workspace_index() -> dict[str, Any]:
    """Projects: category, pin, counters, steps."""
    return workspace.index()


@app.get("/api/workspace/{project}")
def workspace_project(project: str) -> dict[str, Any]:
    return workspace.summary(project)


@app.post("/api/workspace/{project}/report")
def workspace_report(project: str) -> dict[str, Any]:
    """Regenerate a project's report and return the updated card.

    The HTML is not in the response: it is served separately, at
    `/api/workspace/{project}/report.html`, so that its links and anchors work
    in a browser.
    """
    return {"path": workspace.write_report(project), **workspace.summary(project)}


@app.get("/api/workspace/{project}/report.html")
def workspace_report_file(project: str) -> FileResponse:
    """The report, written on the way: what is served is what is on disk."""
    path = Path(workspace.write_report(project))
    return _file(path, "text/html")


@app.patch("/api/workspace/{project}")
def workspace_meta(project: str, request: WorkspaceRequest) -> dict[str, Any]:
    """Correct a card: title, description, category, status, pin."""
    return workspace.set_meta(project, **request.model_dump())


@app.get("/api/workspace/{project}/meta")
def workspace_declaration(project: str) -> dict[str, Any]:
    """The declaration alone -- title, logo -- without recomputing the card."""
    return workspace.read_meta(project)


@app.get("/api/workspace/{project}/logo")
def workspace_logo(project: str) -> FileResponse:
    """The workspace logo, as it is on disk."""
    path = workspace.logo_path(project)
    if path is None:
        raise NotFound(f"no logo for {project}")
    # An SVG is served as an image: an `<img>` runs none of its scripts.
    return _file(path, "image/svg+xml" if path.suffix == ".svg" else None)


@app.put("/api/workspace/{project}/logo")
async def workspace_logo_set(project: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """Set the workspace logo. The previous one is replaced."""
    data = await file.read()
    return await run_in_threadpool(workspace.set_logo, project, data)


@app.delete("/api/workspace/{project}/logo")
def workspace_logo_clear(project: str) -> dict[str, Any]:
    return workspace.clear_logo(project)


@app.delete("/api/workspace/{project}")
def workspace_delete(project: str) -> dict[str, Any]:
    """Delete a project and everything that makes it exist. A human gesture:
    no MCP tool exposes it."""
    return workspace.delete_project(project)


# ---------------------------------------------------------------- diagnostic


@app.get("/api/doctor")
def doctor_check() -> dict[str, Any]:
    """The state of the environment, with the fix for each gap."""
    return doctor.check()


# -------------------------------------------------------------------- prompts


@app.get("/api/prompts")
def prompts_catalogue() -> list[dict[str, Any]]:
    """The workbench prompts: reference, element sheets, angles."""
    return prompts.catalogue()


@app.get("/api/prompts/{prompt_id}")
def prompt_render(prompt_id: str, subject: str, style_prefix: str = "",
                  style_negative: str = "") -> dict[str, Any]:
    """A prompt composed for a subject, ready for the generator."""
    return prompts.render(prompt_id, subject, style_prefix=style_prefix,
                          style_negative=style_negative)


# ---------------------------------------------------------------------- inbox


class InboxRequest(BaseModel):
    """Drop a file already on disk: the interface has the path."""

    path: str


@app.get("/api/inbox")
def inbox_list(limit: int = 50) -> dict[str, Any]:
    """What the inbox holds: relative path, size, dimensions for an image."""
    data = inbox.summary()
    return {**data, "attachments": data["attachments"][:max(1, limit)]}


@app.post("/api/inbox/attachment")
async def inbox_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """Drop bytes: the path of a screenshot pasted into the window.

    The interface has bytes, not a path -- a clipboard image exists nowhere on
    disk. It is written here, and its path goes back into the conversation.
    """
    data = await file.read()
    return await run_in_threadpool(inbox.add_bytes, data, file.filename or "capture.png")


@app.post("/api/inbox/add")
def inbox_add(request: InboxRequest) -> dict[str, Any]:
    """Drop a file from disk, without moving it."""
    return inbox.add_file(request.path)


@app.get("/api/inbox/{name}")
def inbox_file(name: str) -> FileResponse:
    """The file itself, for the preview in the window."""
    return _file(inbox.read_attachment(name)["path"])


@app.delete("/api/inbox/{name}")
def inbox_delete(name: str) -> dict[str, Any]:
    return inbox.delete_attachment(name)


# ------------------------------------------------------ skills and connections


class NewSkillRequest(BaseModel):
    """A procedure to add: its title, when to follow it, and its text."""

    name: str
    description: str
    body: str = ""


@app.get("/api/skills")
def skills_list() -> list[dict[str, Any]]:
    """The studio's procedures: name, description, state."""
    return skills.skills()


@app.get("/api/skills/check")
def skills_check() -> dict[str, Any]:
    """The verdict on the skills, the index and the mirror (what `make check` runs)."""
    return skills.check()


class SkillRequest(BaseModel):
    request: str


class SkillHandoffRequest(SkillRequest, HandoffRequest):
    """A procedure request, and the agent that will write it."""


@app.post("/api/skills/brief")
def skill_brief(request: SkillRequest) -> dict[str, Any]:
    """The brief handing the writing of a procedure to an agent, written and returned."""
    return handoff.skill_brief(request.request)


@app.post("/api/skills/handoff")
async def skill_handoff(request: SkillHandoffRequest) -> dict[str, Any]:
    """Hand the writing of a procedure to an agent, at the studio root."""
    return handoff.send_skill(request.request, harness=request.harness,
                              effort=request.effort, session=request.session,
                              message=request.message,
                              loop=asyncio.get_running_loop())


@app.post("/api/skills")
def skills_create(request: NewSkillRequest) -> dict[str, Any]:
    """Add a procedure: the file, then the regenerated index and the repaired mirror."""
    return skills.create(request.name, request.description, request.body)


@app.post("/api/skills/index")
def skills_index() -> dict[str, Any]:
    """Regenerate `.claude/skills/README.md` from the files."""
    return skills.write_index()


@app.post("/api/skills/sync")
def skills_sync() -> dict[str, Any]:
    """Create or repair the `.agents/skills` mirror for other agents."""
    return skills.sync()


@app.get("/api/skills/{name}")
def skill_detail(name: str) -> dict[str, Any]:
    """A procedure, in full. Declared after `/check`: the literal path wins."""
    return skills.read(name)


@app.get("/api/connections")
def mcp_connections() -> dict[str, Any]:
    """The MCP connections, by source, with their state. Read-only."""
    return connections.connections()


# ------------------------------------------------------------------ documents


class DocumentRequest(BaseModel):
    """The text of a project document, as saved."""

    text: str


class NewDocumentRequest(BaseModel):
    title: str
    template: str = "blank"


@app.get("/api/documents/templates")
def document_templates() -> list[dict[str, str]]:
    return documents.templates()


# `folder` picks the shelf: empty for the root (the Documents page), `notes`,
# `ideas`, `devlog`, `design/...`, or `world/<section>`.


@app.get("/api/projects/{project}/documents")
def document_list(project: str, folder: str = "") -> list[dict[str, Any]]:
    """A project's documents: what is written, and when."""
    return documents.documents(project, folder)


@app.get("/api/projects/{project}/document-image")
def document_image(project: str, src: str, folder: str = "") -> FileResponse:
    """An image a document cites: the Documents page shows it in the render."""
    path = documents.image_file(project, folder, src)
    return _file(path, documents.IMAGE_TYPES[path.suffix.lower()])


@app.get("/api/projects/{project}/documents/{name}")
def document_read(project: str, name: str, folder: str = "") -> dict[str, Any]:
    return documents.read_document(project, name, folder)


@app.put("/api/projects/{project}/documents/{name}")
def document_write(project: str, name: str, request: DocumentRequest,
                   folder: str = "") -> dict[str, Any]:
    """Save a document. It is created if it does not exist."""
    return documents.write_document(project, name, request.text, folder=folder)


@app.post("/api/projects/{project}/documents")
def document_create(project: str, request: NewDocumentRequest,
                    folder: str = "") -> dict[str, Any]:
    """Create a document from a template, without overwriting an existing one."""
    return documents.create_document(project, request.title, request.template, folder)


class CreateRequest(BaseModel):
    request: str
    # The images dropped with the request: inbox paths (or absolute), and the
    # name they had (the inbox renames them).
    images: list[str] = []
    names: list[str] = []
    # The group of the world section where the request is made: axis -> value.
    axes: dict[str, str] = {}


class CreateHandoffRequest(CreateRequest, HandoffRequest):
    """The request made in a section, and the agent that will write it."""


@app.post("/api/projects/{project}/document-brief")
def document_brief(project: str, request: CreateRequest, folder: str = "") -> dict[str, Any]:
    """The brief handing an agent what to create in the section, written and returned."""
    return handoff.create_brief(project, folder, request.request, request.images,
                                request.names, request.axes)


@app.post("/api/projects/{project}/document-handoff")
async def document_handoff(project: str, request: CreateHandoffRequest,
                           folder: str = "") -> dict[str, Any]:
    """Hand the creation to an agent: a new chat, or an open tab."""
    return handoff.send_create(project, folder, request.request, images=request.images,
                               names=request.names, axes=request.axes,
                               harness=request.harness,
                               effort=request.effort, session=request.session,
                               message=request.message,
                               loop=asyncio.get_running_loop())


@app.delete("/api/projects/{project}/documents/{name}")
def document_delete(project: str, name: str, folder: str = "") -> dict[str, Any]:
    """Delete a document. A human gesture: no MCP tool exposes it."""
    return documents.delete_document(project, name, folder)


# ------------------------------------------- lookdev: the art direction shown


class LookdevStateRequest(BaseModel):
    """How to present a specimen; `null` leaves a field as is."""

    setup: str | None = None
    shape: str | None = None
    preset: str | None = None


@app.get("/api/projects/{project}/lookdev")
def lookdev_universe(project: str) -> dict[str, Any]:
    """The game's specimens (its shaders), and the game's look."""
    return lookdev.universe(project)


@app.get("/api/projects/{project}/lookdev-font")
def lookdev_font(project: str, file: str) -> FileResponse:
    """A game font: the page dresses itself with it."""
    return _file(lookdev.font_file(project, file))


@app.get("/api/projects/{project}/lookdev/{specimen}")
def lookdev_specimen(project: str, specimen: str) -> dict[str, Any]:
    """A specimen: its settings as Godot reads them, its uses in the game."""
    return lookdev.specimen(project, specimen)


@app.get("/api/projects/{project}/lookdev/{specimen}/frame")
def lookdev_frame(project: str, specimen: str, params: str = "", preset: str = "",
                  shape: str = "", yaw: float = 0.0, pitch: float = 0.0, zoom: float = 1.0,
                  scale: float = 1.0, format: str = "jpg", background: str = "",
                  device: str = "") -> Response:
    """An image of the specimen, rendered by Godot with these settings.

    `params`: the settings, as JSON. The interface asks for the next one as
    soon as the previous has arrived: that is what makes the specimen live.
    `jpg` on the page background (`background`) for the live image, `webp` to
    keep transparency. `device`: the whole screen of a phone, a tablet or a
    desktop, the game placed in it as its stretch settings say.
    """
    try:
        values = json.loads(params) if params else {}
    except json.JSONDecodeError as exc:
        raise ServiceError(f"unreadable settings: {exc}") from exc
    if not isinstance(values, dict):
        raise ServiceError("unreadable settings: an object is expected")
    shot = lookdev.frame(project, specimen, params=values, preset=preset, shape=shape,
                         yaw=yaw, pitch=pitch, zoom=zoom, scale=scale, format=format,
                         background=background, device=device)
    timings = shot["bench_ms"]
    return Response(shot["image"], media_type=shot["media_type"], headers={
        "Cache-Control": "no-store", "X-Render-Ms": str(shot["ms"]),
        "X-Bench-Ms": ",".join(f"{key}={timings[key]:.1f}" for key in sorted(timings))})


@app.get("/api/projects/{project}/lookdev/{specimen}/thumb")
def lookdev_thumb(project: str, specimen: str) -> FileResponse:
    """A specimen's thumbnail, rendered once as long as nothing changes."""
    return _file(lookdev.thumbnail(project, specimen), "image/webp")


@app.put("/api/projects/{project}/lookdev/{specimen}")
def lookdev_state(project: str, specimen: str, request: LookdevStateRequest) -> dict[str, Any]:
    """A specimen's setup, shape and shown use."""
    return lookdev.set_state(project, specimen, **request.model_dump())


class LookdevSaveRequest(BaseModel):
    """The settings to write into the game, and the use that takes them (`default`: the shader)."""

    params: dict[str, Any]
    preset: str = ""


@app.post("/api/projects/{project}/lookdev/{specimen}/save")
def lookdev_save(project: str, specimen: str, request: LookdevSaveRequest) -> dict[str, Any]:
    """Write settings into the game: the use's material, or the shader's defaults."""
    return lookdev.save(project, specimen, params=request.params, preset=request.preset)


@app.get("/api/projects/{project}/lookdev/{specimen}/brief")
def lookdev_brief(project: str, specimen: str) -> dict[str, Any]:
    """The brief an agent would get to discuss this shader, written to disk."""
    return handoff.lookdev_brief(project, specimen)


@app.post("/api/projects/{project}/lookdev/{specimen}/handoff")
async def lookdev_handoff(project: str, specimen: str,
                          request: HandoffRequest) -> dict[str, Any]:
    """Open an agent chat about the shader: new, or an open tab."""
    return handoff.send_lookdev(project, specimen, harness=request.harness,
                                effort=request.effort, session=request.session,
                                message=request.message,
                                loop=asyncio.get_running_loop())


@app.get("/api/projects/{project}/lookdev-aspects/{aspect}/brief")
def lookdev_aspect_brief(project: str, aspect: str) -> dict[str, Any]:
    """The brief an agent would get to discuss a whole section of the universe."""
    return handoff.lookdev_aspect_brief(project, aspect)


@app.post("/api/projects/{project}/lookdev-aspects/{aspect}/handoff")
async def lookdev_aspect_handoff(project: str, aspect: str,
                                 request: HandoffRequest) -> dict[str, Any]:
    """Open an agent chat about a whole section of the universe: new, or an open tab."""
    return handoff.send_lookdev_aspect(project, aspect, harness=request.harness,
                                       effort=request.effort, session=request.session,
                                       message=request.message,
                                       loop=asyncio.get_running_loop())


# ------------------------- the graphic style and the universe: influences, thread


@app.get("/api/projects/{project}/direction/{aspect}")
def direction_board(project: str, aspect: str) -> dict[str, Any]:
    """The aspect's card, its influences with their images, its image proposals."""
    return influences.board(project, aspect)


class InfluenceRequest(BaseModel):
    """An influence to add (no `influence`) or to change; what is not given is kept."""

    name: str
    influence: str = ""
    kind: str = ""
    keep: str | None = None
    avoid: str | None = None
    of: list[str] | None = None


@app.post("/api/projects/{project}/direction/{aspect}/influences")
def direction_influence(project: str, aspect: str, request: InfluenceRequest) -> dict[str, Any]:
    """Name an influence, or change one."""
    return influences.set_influence(project, aspect, **request.model_dump())


@app.delete("/api/projects/{project}/direction/{aspect}/influences/{influence}")
def direction_influence_remove(project: str, aspect: str, influence: str) -> dict[str, Any]:
    """Take an influence off the board. A human gesture: no MCP tool exposes it."""
    return influences.remove_influence(project, aspect, influence)


@app.post("/api/projects/{project}/direction/{aspect}/influences/{influence}/references")
async def direction_reference(project: str, aspect: str, influence: str,
                              file: UploadFile = File(...)) -> dict[str, Any]:
    """Drop an image for an influence: filed with the aspect's card."""
    data = await file.read()
    return await run_in_threadpool(influences.add_reference, project, aspect, influence, data,
                                   file.filename or "reference.png")


@app.delete("/api/projects/{project}/direction/{aspect}/influences/{influence}/images")
def direction_image_remove(project: str, aspect: str, influence: str, key: str) -> dict[str, Any]:
    """Take an image off an influence. A human gesture: no MCP tool exposes it."""
    return influences.remove_image(project, aspect, influence, key)


class InfluencePathRequest(BaseModel):
    # A path on disk: what the shell gives for a dragged file. Declared before
    # its route, or `request` would become a query parameter.
    path: str


@app.post("/api/projects/{project}/direction/{aspect}/influences/{influence}/references/path")
def direction_reference_path(project: str, aspect: str, influence: str,
                             request: InfluencePathRequest) -> dict[str, Any]:
    """File an image from disk for an influence, without moving it."""
    return influences.add_reference_file(project, aspect, influence, request.path)


class ProposalPayRequest(BaseModel):
    """A proposal paid as proposed, or adjusted; refused without `confirm`."""

    prompt: str | None = None
    model: str | None = None
    count: int | None = None
    confirm: bool = False


@app.post("/api/projects/{project}/direction/{aspect}/proposals/{proposal}/pay")
def direction_pay(project: str, aspect: str, proposal: str,
                  request: ProposalPayRequest) -> dict[str, Any]:
    """PAID: generate a proposal's images. The user's gesture: an agent only proposes."""
    return influences.pay(project, aspect, proposal, **request.model_dump())


@app.post("/api/projects/{project}/direction/{aspect}/proposals/{proposal}/dismiss")
def direction_dismiss(project: str, aspect: str, proposal: str) -> dict[str, Any]:
    """Set a proposal aside, unpaid."""
    return influences.dismiss(project, aspect, proposal)


@app.get("/api/projects/{project}/direction/{aspect}/chat")
def direction_thread(project: str, aspect: str) -> dict[str, Any]:
    """The thread with the agent: its messages, and whether it is answering."""
    return direction_chat.thread(project, aspect)


class DirectionMessage(BaseModel):
    """A message to the agent, the interface's language for its answer, and its model."""

    text: str
    lang: str = ""
    model: str = ""


@app.post("/api/projects/{project}/direction/{aspect}/chat")
def direction_send(project: str, aspect: str, request: DirectionMessage) -> dict[str, Any]:
    """Write to the agent: it answers in the thread as it goes."""
    return direction_chat.send(project, aspect, request.text, lang=request.lang,
                               model=request.model)


class DraftRequest(BaseModel):
    """Images wanted: what to see, the influences to draw on (all with images when empty)."""

    # Before `influences`, which names the field below, not the module, past it.
    model: str = influences.DEFAULT_MODEL
    request: str = ""
    influences: list[str] | None = None
    count: int = 4
    lang: str = ""


@app.post("/api/projects/{project}/direction/{aspect}/draft")
def direction_draft(project: str, aspect: str, request: DraftRequest) -> dict[str, Any]:
    """A model looks at the influences' images and writes the prompt: a proposal, unpaid."""
    return direction_chat.draft(project, aspect, request=request.request,
                                sources=request.influences, count=request.count,
                                model=request.model, lang=request.lang)


@app.post("/api/projects/{project}/direction/{aspect}/chat/stop")
def direction_stop(project: str, aspect: str) -> dict[str, Any]:
    """Stop the agent's answer where it is."""
    return direction_chat.stop(project, aspect)


@app.post("/api/projects/{project}/direction/{aspect}/chat/reset")
def direction_reset(project: str, aspect: str) -> dict[str, Any]:
    """Start a new conversation; the previous one is kept, dated."""
    return direction_chat.reset(project, aspect)


# ------------------------------------------------------- the game's main pages


@app.get("/api/projects/{project}/views")
def game_views(project: str) -> dict[str, Any]:
    """The game's views: its main pages, the one it starts on first."""
    return views.views(project)


@app.get("/api/projects/{project}/views/{view}/image")
def game_view_image(project: str, view: str) -> FileResponse:
    """A view drawn by the game's engine; drawn once, kept until its scene changes."""
    return _file(views.image(project, view), "image/png")


# ------------------------------------------------- a game design card's workbench

# `folder` is the card's section (`design/interface`): never empty.


class CardSketchRequest(BaseModel):
    """A card's sketch: the Excalidraw scene, and its export as a data URL."""

    scene: dict[str, Any] | None = None
    png: str | None = None


class CardGenerateRequest(BaseModel):
    prompt: str
    model: str | None = None
    # Empty: text only. Otherwise `sketch` or `render`, the starting image.
    reference: str = ""
    strength: float = 0.6
    width: int = 768
    height: int = 1344
    count: int = 1
    negative_prompt: str = ""
    style: bool = True
    confirm: bool = False


@app.get("/api/projects/{project}/documents/{name}/media")
def card_media(project: str, name: str, folder: str) -> dict[str, Any]:
    """A card's game render, sketch and generated images."""
    return cards.media(project, folder, name)


@app.get("/api/projects/{project}/documents/{name}/sketch")
def card_sketch(project: str, name: str, folder: str) -> dict[str, Any]:
    """The card's Excalidraw scene, as saved."""
    return cards.read_sketch(project, folder, name)


@app.put("/api/projects/{project}/documents/{name}/sketch")
def card_sketch_save(project: str, name: str, request: CardSketchRequest,
                      folder: str) -> dict[str, Any]:
    """Save the sketch and its PNG export; `null` removes them."""
    return cards.save_sketch(project, folder, name, request.scene, request.png)


@app.post("/api/projects/{project}/documents/{name}/references")
async def card_reference_add(project: str, name: str, folder: str,
                              file: UploadFile = File(...)) -> dict[str, Any]:
    """Drop a reference image for the card (dragged, pasted or picked)."""
    data = await file.read()
    return await run_in_threadpool(cards.add_reference, project, folder, name, data,
                                   file.filename or "reference.png")


class ReferencePathRequest(BaseModel):
    # A path on disk: what Tauri gives for a dragged file.
    path: str


@app.post("/api/projects/{project}/documents/{name}/references/path")
def card_reference_add_path(project: str, name: str, request: ReferencePathRequest,
                             folder: str) -> dict[str, Any]:
    """File an image from disk with the card, without moving it."""
    return cards.add_reference_file(project, folder, name, request.path)


@app.delete("/api/projects/{project}/documents/{name}/references/{file}")
def card_reference_delete(project: str, name: str, file: str,
                           folder: str) -> dict[str, Any]:
    """Remove a reference from the card. A human gesture: no MCP tool exposes it."""
    return cards.remove_reference(project, folder, name, file)


@app.post("/api/projects/{project}/documents/{name}/render")
def card_rerender(project: str, name: str, folder: str) -> dict[str, Any]:
    """Render the card's scene again, through the game engine. Free."""
    return cards.rerender(project, folder, name)


@app.post("/api/projects/{project}/documents/{name}/generate")
def card_generate(project: str, name: str, request: CardGenerateRequest,
                   folder: str) -> dict[str, Any]:
    """Images for the card. PAID: refused (402) without `confirm`."""
    return cards.generate(
        project, folder, name, prompt=request.prompt, model=request.model,
        reference=request.reference, strength=request.strength, width=request.width,
        height=request.height, count=request.count,
        negative_prompt=request.negative_prompt, style=request.style,
        confirm=request.confirm)


# ------------------------------------------------ the icon and prop showcase

# `kind`: `icons` or `props` (`service/showcase.py`).


@app.get("/api/projects/{project}/showcase/{kind}")
def showcase_list(project: str, kind: str) -> dict[str, Any]:
    """The showcase elements, by family, and the game's look."""
    return showcase.showcase(project, kind)


@app.post("/api/projects/{project}/showcase/props/render")
def showcase_render(project: str, force: bool = False) -> dict[str, Any]:
    """Draw the props whose image is missing or stale. Free, local."""
    return showcase.render(project, force=force)


@app.get("/api/projects/{project}/showcase/{kind}/{element}")
def showcase_element(project: str, kind: str, element: str) -> dict[str, Any]:
    """An element in detail: its family, its images, its uses."""
    return showcase.element(project, kind, element)


@app.get("/api/projects/{project}/showcase/{kind}/{element}/image")
def showcase_image(project: str, kind: str, element: str, state: str = "") -> FileResponse:
    """An element's image: the game file, or a prop's drawing in a state."""
    path = showcase.image_file(project, kind, element, state)
    return _file(path, documents.IMAGE_TYPES.get(path.suffix.lower()))


@app.delete("/api/projects/{project}/showcase/{kind}/family/{family}")
def showcase_delete_family(project: str, kind: str, family: str) -> dict[str, Any]:
    """Remove a whole family from the game, into the project's trash. A human gesture."""
    return showcase.delete_family(project, kind, family)


@app.delete("/api/projects/{project}/showcase/{kind}/{element}")
def showcase_delete(project: str, kind: str, element: str) -> dict[str, Any]:
    """Remove an element from the game, into the project's trash. A human gesture."""
    return showcase.delete(project, kind, element)


@app.get("/api/projects/{project}/trash")
def trash_list(project: str) -> list[dict[str, Any]]:
    """What the studio removed from the game, batch by batch, with what restores it."""
    return trash.batches(project)


@app.post("/api/projects/{project}/trash/{batch}/restore")
def trash_restore(project: str, batch: str) -> dict[str, Any]:
    """Put a trash batch back into the game, at its original paths."""
    return trash.restore(project, batch)


# ------------------------------------------------------------- the icon forge


class ForgeRequest(BaseModel):
    mode: str
    folder: str = ""
    names: list[str] = Field(default_factory=list)
    description: str = ""
    style: str | None = None
    element: str = ""
    model: str | None = None
    count: int | None = None
    reference: str | None = None
    strength: float | None = None
    confirm: bool = False


class ForgeSplitRequest(BaseModel):
    asset_id: str


class ForgeAdoptRequest(BaseModel):
    # `[{"source": "asset:<id>" | "piece:<n>", "name": "<name>"}]`
    picks: list[dict[str, Any]]


@app.get("/api/projects/{project}/forge")
def forge_list(project: str, closed: bool = False) -> list[dict[str, Any]]:
    """The forge's requests, their proposals and their state."""
    return forge.requests(project, closed=closed)


@app.get("/api/projects/{project}/forge-families")
def forge_families(project: str) -> list[dict[str, Any]]:
    """The icon families, with their scale, reference and style."""
    return forge.families(project)


@app.post("/api/projects/{project}/forge")
def forge_request(project: str, request: ForgeRequest) -> dict[str, Any]:
    """Order icons from Runware. PAID: refused (402) without `confirm`."""
    return forge.request(project, **request.model_dump())


@app.post("/api/projects/{project}/forge/{demand}/split")
def forge_split(project: str, demand: str, request: ForgeSplitRequest) -> dict[str, Any]:
    """Split the chosen sheet of an icon set. Free, local."""
    return forge.split(project, demand, request.asset_id)


@app.post("/api/projects/{project}/forge/{demand}/adopt")
def forge_adopt(project: str, demand: str, request: ForgeAdoptRequest) -> dict[str, Any]:
    """Write the chosen proposals into the game folder."""
    return forge.adopt(project, demand, request.picks)


@app.post("/api/projects/{project}/forge/{demand}/close")
def forge_close(project: str, demand: str) -> dict[str, Any]:
    """Close a request: it leaves the page."""
    return forge.close(project, demand)


@app.get("/api/projects/{project}/forge/{demand}/image")
def forge_image(project: str, demand: str, asset: str = "",
                piece: int | None = None) -> FileResponse:
    """The image of a proposal, or of a sheet piece."""
    return _file(forge.image_file(project, demand, asset=asset, piece=piece), "image/png")


@app.get("/api/projects/{project}/showcase/{kind}/{element}/brief")
def showcase_brief(project: str, kind: str, element: str) -> dict[str, Any]:
    """The brief an agent would get to discuss this element, written to disk."""
    return handoff.showcase_brief(project, kind, element)


@app.post("/api/projects/{project}/showcase/{kind}/{element}/handoff")
async def showcase_handoff(project: str, kind: str, element: str,
                           request: HandoffRequest) -> dict[str, Any]:
    """Open an agent chat about the element: new, or an open tab."""
    return handoff.send_showcase(project, kind, element, harness=request.harness,
                                 effort=request.effort, session=request.session,
                                 message=request.message,
                                 loop=asyncio.get_running_loop())


# --------------------------------------------------------- game screen editor

# The screen of an interface card, edited on its branch (`service/screens.py`).


class ScreenOpenRequest(BaseModel):
    # Empty: the scene of the card's current render.
    scene: str = ""


class ScreenEditRequest(BaseModel):
    # The element's path in the screen (`nodes.json`), `.` for its root.
    path: str
    # `{key: value}`; `null` restores the default value.
    changes: dict[str, Any]


@app.get("/api/projects/{project}/documents/{name}/screen")
def screen_state(project: str, name: str, folder: str) -> dict[str, Any]:
    """Where the screen edit stands: branch, commits, render, elements."""
    return screens.state(project, folder, name)


@app.post("/api/projects/{project}/documents/{name}/screen/open")
def screen_open(project: str, name: str, request: ScreenOpenRequest,
                folder: str) -> dict[str, Any]:
    """Show the screen, ready to edit: its render and its elements."""
    return screens.open_screen(project, folder, name, request.scene)


@app.post("/api/projects/{project}/documents/{name}/screen/render")
def screen_render(project: str, name: str, folder: str) -> dict[str, Any]:
    """Redraw the screen: from its branch, or from the game before the first edit."""
    return screens.render(project, folder, name)


@app.get("/api/projects/{project}/documents/{name}/screen/node")
def screen_node(project: str, name: str, folder: str, path: str) -> dict[str, Any]:
    """A screen element and its editable properties."""
    return screens.node_properties(project, folder, name, path)


@app.post("/api/projects/{project}/documents/{name}/screen/edit")
def screen_edit(project: str, name: str, request: ScreenEditRequest,
                folder: str) -> dict[str, Any]:
    """Write an element's properties on the screen's branch, and redraw."""
    return screens.edit(project, folder, name, request.path, request.changes)


@app.post("/api/projects/{project}/documents/{name}/screen/undo")
def screen_undo(project: str, name: str, folder: str) -> dict[str, Any]:
    """Undo the screen's last edit."""
    return screens.undo(project, folder, name)


class ScreenCommentRequest(BaseModel):
    # The element's path in the screen (`nodes.json`), `.` for its root.
    path: str
    text: str
    # True: hand it to the screen's agent at once (queued if it is working).
    send: bool = False
    harness: str = "claude"


class ScreenCommentsRequest(BaseModel):
    ids: list[int]
    harness: str = "claude"


@app.get("/api/projects/{project}/documents/{name}/screen/comments")
def screen_comments_list(project: str, name: str, folder: str) -> dict[str, Any]:
    """The comments on the screen's elements, and its agent's tab."""
    return screen_comments.comments(project, folder, name)


@app.post("/api/projects/{project}/documents/{name}/screen/comments")
async def screen_comment_add(project: str, name: str, request: ScreenCommentRequest,
                             folder: str) -> dict[str, Any]:
    """Save a comment on an element; with `send`, hand it to the agent too."""
    added = screen_comments.add(project, folder, name, request.path, request.text)
    if not request.send:
        return added
    loop = asyncio.get_running_loop()
    sent = await asyncio.to_thread(screen_comments.send, project, folder, name,
                                   [added["comment"]["id"]], harness=request.harness,
                                   loop=loop)
    return {**sent, "comment": added["comment"]}


@app.post("/api/projects/{project}/documents/{name}/screen/comments/send")
async def screen_comments_send(project: str, name: str, request: ScreenCommentsRequest,
                               folder: str) -> dict[str, Any]:
    """Hand comments to the screen's agent, in order: one now, the rest queued."""
    loop = asyncio.get_running_loop()
    return await asyncio.to_thread(screen_comments.send, project, folder, name, request.ids,
                                   harness=request.harness, loop=loop)


@app.post("/api/projects/{project}/documents/{name}/screen/comments/remove")
def screen_comments_remove(project: str, name: str, request: ScreenCommentsRequest,
                           folder: str) -> dict[str, Any]:
    """Remove comments that are not with the agent."""
    return screen_comments.remove(project, folder, name, request.ids)


@app.get("/api/activity")
def activity_now(project: str = "") -> dict[str, Any]:
    """What the studio is doing: renders under way, the data agent at work."""
    return activity.now(project)


class PreviewDataRequest(BaseModel):
    enabled: bool


@app.get("/api/projects/{project}/preview-data")
def preview_data_state(project: str) -> dict[str, Any]:
    """The game's preview data: fake server answers for the studio's renders."""
    return preview_data.state(project)


@app.post("/api/projects/{project}/preview-data")
def preview_data_enable(project: str, request: PreviewDataRequest) -> dict[str, Any]:
    """Turn the preview data on or off."""
    return preview_data.set_enabled(project, request.enabled)


@app.get("/api/projects/{project}/preview-data/brief")
def preview_data_brief(project: str) -> dict[str, Any]:
    """The brief of the agent that writes the preview data, written to disk."""
    return handoff.preview_brief(project)


@app.post("/api/projects/{project}/preview-data/handoff")
async def preview_data_handoff(project: str, request: HandoffRequest) -> dict[str, Any]:
    """Hand the preview data to an agent: a new chat, or an open tab."""
    return handoff.send_preview(project, harness=request.harness, effort=request.effort,
                                session=request.session, message=request.message,
                                loop=asyncio.get_running_loop())


@app.post("/api/projects/{project}/screens/warm")
def screens_warm(project: str, folder: str) -> dict[str, Any]:
    """Draw the section's screens not drawn yet, in the background."""
    return screens.warm(project, folder)


@app.get("/api/projects/{project}/documents/{name}/screen/preview")
def screen_preview(project: str, name: str, folder: str) -> FileResponse:
    """The screen as its branch draws it."""
    return _file(screens.preview_file(project, folder, name), "image/png")


# --------------------------------------------------------------- VFX handoff


@app.get("/api/projects/{project}/vfx/{name}/brief")
def vfx_brief(project: str, name: str) -> dict[str, Any]:
    """The brief an agent would get for this concept, written to disk."""
    return handoff.brief(project, name)


@app.post("/api/projects/{project}/vfx/{name}/handoff")
async def vfx_handoff(project: str, name: str, request: HandoffRequest) -> dict[str, Any]:
    """Hand the concept to an agent, who makes it in Godot."""
    return handoff.send(project, name, harness=request.harness, effort=request.effort,
                        session=request.session, message=request.message,
                        loop=asyncio.get_running_loop())


# ------------------------------------------------------- a chat about a card

# `folder` is the card's section (`design/interface`): never empty.


@app.get("/api/projects/{project}/documents/{name}/brief")
def card_brief(project: str, name: str, folder: str) -> dict[str, Any]:
    """The brief an agent would get to discuss this card, written to disk."""
    return handoff.card_brief(project, folder, name)


@app.post("/api/projects/{project}/documents/{name}/handoff")
async def card_handoff(project: str, name: str, request: HandoffRequest,
                        folder: str) -> dict[str, Any]:
    """Open an agent chat about the card: new, or an open tab."""
    return handoff.send_card(project, folder, name, harness=request.harness,
                              effort=request.effort, session=request.session,
                              message=request.message,
                              loop=asyncio.get_running_loop())


# ---------------------------------------------------------------------- world


class WorldSectionRequest(BaseModel):
    label: str | None = None
    icon: str | None = None


@app.get("/api/projects/{project}/world")
def world_sections(project: str) -> list[dict[str, Any]]:
    """The world sections, declared by the user."""
    return world.sections(project)


@app.get("/api/projects/{project}/world-axes")
def world_project_axes(project: str) -> list[dict[str, Any]]:
    """The project's axes, and the sections using each."""
    return world.axes(project)


@app.delete("/api/projects/{project}/world-axes/{axis}")
def world_forget_axis(project: str, axis: str) -> dict[str, Any]:
    """Forget an axis no section uses any more."""
    return world.forget_axis(project, axis)


@app.post("/api/projects/{project}/world")
def world_create(project: str, request: WorldSectionRequest) -> dict[str, Any]:
    return world.create_section(project, request.label or "", request.icon)


@app.patch("/api/projects/{project}/world/{section}")
def world_update(project: str, section: str, request: WorldSectionRequest) -> dict[str, Any]:
    return world.update_section(project, section, label=request.label, icon=request.icon)


@app.delete("/api/projects/{project}/world/{section}")
def world_delete(project: str, section: str, force: bool = False) -> dict[str, Any]:
    """Remove a section.

    A section holding cards is refused; `force=true` takes them with it. What
    they produced -- concepts, meshes -- stays in the library.
    """
    return world.delete_section(project, section, force=force)


class WorldAxesRequest(BaseModel):
    """A section's filing axes, in display order: a project axis id, or an
    object {id, label, values}."""

    axes: list[dict[str, Any] | str] = Field(default_factory=list)


@app.put("/api/projects/{project}/world/{section}/axes")
def world_axes(project: str, section: str,
               request: WorldAxesRequest) -> dict[str, Any]:
    """The axes a section uses, corrected project-wide: renaming detaches no
    card, removing a value removes it wherever the axis is used."""
    return world.set_axes(project, section, request.axes)


# ------------------------------------------------------ a world card's workbench


class ConceptsRequest(BaseModel):
    prompt: str = ""
    model: str | None = None
    negative_prompt: str = ""
    style: bool = True
    reference_asset_id: str | None = None
    strength: float = 0.6
    pose: str | None = None
    width: int = 768
    height: int = 1152
    count: int = 4
    transparent: bool = False
    seed: int | None = None
    confirm: bool = False


class ChooseConceptRequest(BaseModel):
    asset_id: str | None = None


class RealizeRequest(BaseModel):
    mesh_model: str | None = None
    face_limit: int = 8000
    confirm: bool = False


class AttachMeshRequest(BaseModel):
    path: str
    replace: bool = False


@app.get("/api/projects/{project}/entities")
def world_entities(project: str) -> list[dict[str, Any]]:
    """The world cards and the entity each one gave rise to."""
    return entities.entities(project)


@app.get("/api/projects/{project}/world/{section}/{name}/workbench")
def entity_workbench(project: str, section: str, name: str) -> dict[str, Any]:
    return entities.workbench(project, section, name)


@app.post("/api/projects/{project}/world/{section}/{name}/concepts")
def entity_concepts(project: str, section: str, name: str,
                    request: ConceptsRequest) -> dict[str, Any]:
    """A batch of concepts for the card. PAID: refused (402) without `confirm`."""
    return entities.generate_concepts(
        project, section, name, prompt=request.prompt, model=request.model,
        negative_prompt=request.negative_prompt, style=request.style,
        reference_asset_id=request.reference_asset_id, strength=request.strength,
        pose=request.pose, width=request.width, height=request.height,
        count=request.count, transparent=request.transparent, seed=request.seed,
        confirm=request.confirm)


@app.put("/api/projects/{project}/world/{section}/{name}/concept")
def entity_choose(project: str, section: str, name: str,
                  request: ChooseConceptRequest) -> dict[str, Any]:
    return entities.choose_concept(project, section, name, request.asset_id)


class EntityAxesRequest(BaseModel):
    """A card's filing: an axis, a value (by id or label)."""

    values: dict[str, str] = Field(default_factory=dict)


@app.put("/api/projects/{project}/world/{section}/{name}/axes")
def entity_axes(project: str, section: str, name: str,
                request: EntityAxesRequest) -> dict[str, Any]:
    """File a card in its section's axes."""
    return entities.set_axes(project, section, name, request.values)


@app.post("/api/projects/{project}/world/{section}/{name}/realize")
def entity_realize(project: str, section: str, name: str,
                   request: RealizeRequest) -> dict[str, Any]:
    return entities.realize(project, section, name, mesh_model=request.mesh_model,
                            face_limit=request.face_limit, confirm=request.confirm)


@app.post("/api/projects/{project}/world/{section}/{name}/mesh")
def entity_mesh(project: str, section: str, name: str,
                request: AttachMeshRequest) -> dict[str, Any]:
    return entities.attach_mesh(project, section, name, request.path, replace=request.replace)


@app.get("/api/projects/{project}/world/{section}/{name}/animation/brief")
def entity_animation_brief(project: str, section: str, name: str) -> dict[str, Any]:
    """The brief an agent would get to rig and animate this entity."""
    return handoff.animation_brief(project, section, name)


@app.post("/api/projects/{project}/world/{section}/{name}/animation/handoff")
async def entity_animation_handoff(project: str, section: str, name: str,
                                   request: HandoffRequest) -> dict[str, Any]:
    """Hand the entity's rig and animations to an agent."""
    return handoff.send_animation(project, section, name, harness=request.harness,
                                  effort=request.effort, session=request.session,
                                  message=request.message,
                                  loop=asyncio.get_running_loop())


# --------------------------------------------------------------------- assets


@app.get("/api/assets")
def assets(kind: str | None = None, limit: int = 60,
           project: str | None = None) -> list[dict[str, Any]]:
    return catalog.list_assets(kind, limit, project)


@app.get("/api/assets/{asset_id}")
def asset(asset_id: str) -> dict[str, Any]:
    return catalog.asset_info(asset_id)


@app.get("/api/assets/{asset_id}/file")
def asset_file(asset_id: str) -> FileResponse:
    """The raw file: how the interface loads a GLB or an SVG."""
    return _file(catalog.asset_path(asset_id))


@app.get("/api/assets/{asset_id}/preview")
def asset_preview(asset_id: str, size: int = 320) -> Response:
    """A PNG thumbnail, SVG included (rasterised on the fly).

    Caching is aggressive because an asset is immutable: its id is the hash of
    its content, so a thumbnail can never become stale.
    """
    return Response(
        content=images.preview_png(asset_id, size),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# -------------------------------------------------------------------- library


@app.get("/api/library/{project}/tree")
def library_tree(project: str, folder: str = "", depth: int = 1,
                 pattern: str = "", limit: int = 200) -> dict[str, Any]:
    return library.tree(project, folder, depth, pattern, limit)


@app.post("/api/library/{project}/sync")
def library_sync(project: str) -> dict[str, Any]:
    return library.sync(project)


@app.get("/api/library/{project}/document")
def library_document(project: str, folder: str = "", name: str = "") -> dict[str, Any]:
    return library.read_document(project, folder, name)


@app.get("/api/library/holders/{asset_id}")
def library_holders(asset_id: str) -> dict[str, Any]:
    return {"asset_id": asset_id, "holders": library.holders(asset_id)}


@app.post("/api/library/rename")
def library_rename(request: CurationRequest) -> dict[str, Any]:
    if not request.asset_ids:
        raise ServiceError("no asset to rename")
    return library.rename(request.asset_ids[0], request.name)


@app.post("/api/library/move")
def library_move(request: CurationRequest) -> dict[str, Any]:
    return library.move(request.asset_ids, request.project, request.sheet)


@app.post("/api/library/delete")
def library_delete(request: CurationRequest) -> dict[str, Any]:
    return library.delete(request.asset_ids)


# ------------------------------------------------------------------ job queue


@app.get("/api/jobs")
def jobs_list(project: str | None = None, state: str | None = None,
              limit: int = 50) -> list[dict[str, Any]]:
    return jobs.listing(project, state, limit)


@app.get("/api/jobs/stats")
def jobs_stats(project: str | None = None) -> dict[str, Any]:
    return jobs.status(project)


@app.get("/api/jobs/stream")
async def jobs_stream(request: Request, project: str | None = None,
                      interval: float = Query(1.0, ge=0.2, le=10.0)
                      ) -> StreamingResponse:
    """SSE stream of the queue state: the interface follows without polling.

    Reading the queue is synchronous (SQLite): it goes to the thread pool so as
    not to block the event loop, and the wait is asynchronous so that the
    client disconnecting really ends the stream.
    """
    async def events():
        changes = jobs.Changes()
        while not await request.is_disconnected():
            snapshot = await run_in_threadpool(jobs.status, project)
            if changes.emit(snapshot):
                yield f"data: {json.dumps(snapshot)}\n\n"
            await asyncio.sleep(interval)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict[str, Any]:
    return jobs.detail(job_id)


# ----------------------------------------------------------------- production


@app.post("/api/produce/entity")
def produce_entity(request: EntityRequest) -> dict[str, Any]:
    return produce.create_entity(
        request.prompt, name=request.name, image_model=request.image_model,
        mesh_model=request.mesh_model, recipe=request.recipe,
        reference_asset_id=request.reference_asset_id,
        negative_prompt=request.negative_prompt, face_limit=request.face_limit,
        confirm=request.confirm)


@app.post("/api/produce/image")
def produce_image(request: ImageRequest) -> dict[str, Any]:
    """Free images. PAID: refused (402) without `confirm`."""
    return produce.generate_image(
        request.prompt, model=request.model, negative_prompt=request.negative_prompt,
        recipe=request.recipe, reference_asset_id=request.reference_asset_id,
        strength=request.strength, pose=request.pose, width=request.width,
        height=request.height, count=request.count, transparent=request.transparent,
        seed=request.seed, confirm=request.confirm)


@app.post("/api/produce/build")
def produce_build(request: BuildRequest) -> dict[str, Any]:
    return produce.build(request.recipe, characters=request.characters or None,
                         force=request.force, confirm=request.confirm)


@app.get("/api/sprites/styles")
def sprite_styles() -> list[dict[str, str]]:
    return produce.sprite_styles()


@app.post("/api/produce/sprites")
def produce_sprites(request: SpritesRequest) -> dict[str, Any]:
    """Free: local headless Blender, no network call."""
    return produce.render_sprites(
        request.mesh, name=request.name, recipe=request.recipe,
        animations=request.animations or None,
        directions=request.directions, size=request.size,
        elevation=request.elevation, style=request.style, palette=request.palette,
        max_frames=request.max_frames, fps=request.fps, loop=request.loop)


@app.post("/api/produce/style/explore")
def produce_explore(request: ExploreRequest) -> dict[str, Any]:
    """Variants of the art direction. PAID: refused (402) without `confirm`."""
    return produce.explore_style(request.recipe, request.subject, request.count,
                                 request.confirm)


@app.post("/api/produce/style/train")
def produce_train(request: TrainRequest) -> dict[str, Any]:
    return produce.train_style(request.recipe, request.images, request.steps,
                               request.confirm)


# ---------------------------------------------------------------------- poses


@app.get("/api/poses")
def pose_list() -> list[dict[str, str]]:
    """The poses that can be imposed when generating a concept."""
    return poses.pose_templates()


@app.get("/api/poses/{name}")
def pose_get(name: str, width: int = 768, height: int = 1152) -> dict[str, Any]:
    return poses.pose_template(name, width, height)


# -------------------------------------------------------------------- effects


class EffectRequest(BaseModel):
    """An effect's YAML spec."""

    spec: str


class EffectPreviewRequest(BaseModel):
    # An unsaved text to try; absent, the effect's file.
    spec: str | None = None
    scale: float = 0.5


@app.get("/api/effects/reference")
def effect_reference() -> dict[str, str]:
    return {"markdown": effects.reference()}


@app.get("/api/projects/{project}/effects")
def effect_list(project: str) -> list[dict[str, Any]]:
    return effects.effects(project)


@app.get("/api/projects/{project}/effects/{name}")
def effect_read(project: str, name: str) -> dict[str, Any]:
    return effects.read_effect(project, name)


@app.put("/api/projects/{project}/effects/{name}")
def effect_write(project: str, name: str, request: EffectRequest) -> dict[str, Any]:
    """Save a spec; a broken spec is refused (400) and the old one stays."""
    return effects.write_effect_spec(project, name, request.spec)


@app.post("/api/projects/{project}/effects/{name}")
def effect_create(project: str, name: str) -> dict[str, Any]:
    """Create an effect from the neutral canvas, without overwriting an existing one."""
    return effects.create_effect(project, name)


@app.delete("/api/projects/{project}/effects/{name}")
def effect_delete(project: str, name: str) -> dict[str, Any]:
    return effects.delete_effect(project, name)


@app.post("/api/projects/{project}/effects/{name}/preview")
def effect_preview(project: str, name: str, request: EffectPreviewRequest) -> dict[str, Any]:
    """Scaled-down preview: the sheet comes back encoded, nothing is filed."""
    return effects.preview_effect(project, name, spec=request.spec, scale=request.scale)


@app.post("/api/projects/{project}/effects/{name}/build")
def effect_build(project: str, name: str) -> dict[str, Any]:
    """Full render, library, validated Godot scenes."""
    return effects.build_effect(project, name)


# ---------------------------------------------------------------------- import


@app.post("/api/import/image")
async def import_image(file: UploadFile = File(...), matting: bool = Form(False),
                       project: str = Form("imports")) -> dict[str, Any]:
    """Import an uploaded image: the web interface has no local path."""
    data = await file.read()
    return await run_in_threadpool(sheets.import_bytes, data,
                                   file.filename or "image.png", matting, project)


@app.post("/api/import/sheet")
def import_sheet(request: SheetRequest) -> dict[str, Any]:
    return sheets.import_sheet(
        request.path, request.multi, request.project, request.strategy,
        request.gap, request.min_size, request.rows, request.columns,
        request.keep, request.raster_size, request.matting)


@app.post("/api/sheet/inspect")
def inspect_sheet(request: SheetRequest) -> dict[str, Any]:
    return sheets.inspect_sheet(request.path, request.strategy, request.gap,
                                request.min_size, request.rows, request.columns,
                                request.matting)


# ------------------------------------------------------------------------- 3D


@app.get("/api/mesh/providers")
def mesh_providers() -> list[dict[str, Any]]:
    """The 3D routes: the local, free one first, then the paid ones."""
    return meshes.mesh_providers()


@app.post("/api/import/mesh")
def import_mesh(request: MeshImportRequest) -> dict[str, Any]:
    """Bring in an external mesh (`.glb`, `.gltf`, `.fbx`, `.obj`).

    Local and free: the way in for 3D produced on the machine, notably by
    `img2threejs`.
    """
    return meshes.import_mesh(
        request.path, project=request.project, name=request.name,
        subject=request.subject, attach=request.attach, replace=request.replace)


# ---------------------------------------------------------------------- video

# A reference video is not uploaded: it is already on the machine, and
# carrying several hundred MB in a request would gain nothing. The path is
# enough, as for a sheet or a mesh.

@app.post("/api/video/frames")
def video_frames(request: VideoRequest) -> dict[str, Any]:
    """Extract the frames of a reference video: local ffmpeg, free.

    The batch goes to `.gamestudio/library/video/<batch>/`, with its manifest
    (`frames.json`) and its contact sheet (`contact.png`).
    """
    return video.extract_frames(
        request.path, name=request.name, project=request.project, fps=request.fps,
        start=request.start, end=request.end, max_frames=request.max_frames,
        width=request.width, cell_width=request.cell_width, contact=request.contact)


# ----------------------------------------------------------- static interface

# In production the built front is served by the same server: the desktop
# application then has a single origin to reach. In development Vite serves
# the interface on its own port and nothing is mounted here.
if (FRONT_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(FRONT_DIR), html=True), name="front")
