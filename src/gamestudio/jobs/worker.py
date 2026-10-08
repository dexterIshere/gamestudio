"""Worker: consumes the queue and runs the long jobs.

A mesh generation takes minutes, a LoRA training tens of minutes. None of it
fits in an HTTP request: the interface queues, the worker runs, and the
interface follows progress. While a handler runs, the worker extends its job's
lease (`JobQueue.hold`): no other worker picks it up, and nothing paid is paid
twice (see `jobs/queue.py`).
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..domain.models import Job, StylePack
from ..domain.recipe import load_recipe
from ..pipeline.base import Context
from ..pipeline.graph import build_character, create_entity
from ..pipeline.steps.generation import ExploreStyle, GenerateImage, TrainStylePack
from ..pipeline.steps.render import RenderSprites
from ..store.folders import project_paths

logger = logging.getLogger("gamestudio.worker")


def _install_signal_handlers(stop: threading.Event) -> None:
    """Clean stop on SIGINT/SIGTERM.

    `signal.signal` only works from the main thread: when the worker runs in
    the background of a server, the host receives the signals and sets the
    stop event.
    """
    if threading.current_thread() is not threading.main_thread():
        return

    def handler(signum: int, _frame: Any) -> None:
        logger.info("signal %s received, stopping after the current job", signum)
        stop.set()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, handler)


# --------------------------------------------------------------------- handlers


def handle_build_character(job: Job, ctx: Context) -> dict[str, Any]:
    payload = job.payload
    recipe = load_recipe(Path(payload["recipe"]))
    spec = next((c for c in recipe.characters if c.id == payload["character"]), None)
    if spec is None:
        raise ValueError(f"character {payload['character']} missing from the recipe")

    godot_project = Path(payload.get("godot_project") or
                         project_paths(ctx.settings, recipe.project).godot)
    character, report = build_character(
        ctx, spec, recipe.style,
        godot_project=godot_project,
        force=bool(payload.get("force")),
    )
    _sync_library(ctx, recipe.project)
    return {
        "character": character.spec.id,
        "state": character.state.value,
        "exports": character.exports,
        "cost_usd": report.cost_usd,
        "needs_review": report.needs_review,
        "notes": report.notes,
        "errors": report.errors,
        "steps": [name for name, _ in report.steps],
    }


def _owning_character(ctx: Context, project: str, mesh_asset_id: str) -> Any:
    """The character this mesh comes from: its current mesh, or its bare mesh."""
    for character in ctx.db.list_characters(project):
        rig = character.rig3d
        if rig is None:
            continue
        if mesh_asset_id in (rig.mesh_asset_id, rig.source_mesh_asset_id):
            return character
    return None


def handle_render_sprites(job: Job, ctx: Context) -> dict[str, Any]:
    """Render a 3D mesh into sprite sheets, from N angles.

    Free and local: headless Blender, no network call. It is the 3D-to-2D
    bridge outside any recipe -- pick a mesh from the library and a render
    style.

    The sheets are attached to the character the mesh comes from: that is
    what files them under `3d/<entity>/sprites/`. Without it they would sit in
    the store without showing up anywhere -- the library only writes what a
    project claims.
    """
    payload = job.payload
    result = RenderSprites(
        payload["mesh"],
        payload.get("name") or "sprites",
        animations=list(payload.get("animations") or []),
        directions=int(payload.get("directions", 8)),
        size=int(payload.get("size", 128)),
        elevation=float(payload.get("elevation", 30.0)),
        style=payload.get("style", "normal"),
        palette=int(payload.get("palette", 32)),
        max_frames=int(payload.get("max_frames", 0)),
        fps=int(payload.get("fps", 12)),
        loop=bool(payload.get("loop", True)),
    ).execute(ctx)

    sheets = [a for a in result.assets if a.kind == "spritesheet"]
    owner = _owning_character(ctx, job.project, payload["mesh"])
    if owner is not None:
        # A new render replaces all the character's sheets: from 8 to 4
        # directions, index 1 no longer means the same angle, and an animation
        # missing from the new render must not survive from the old one.
        owner.spritesheets = {f"{asset.meta['clip']}_{asset.meta['direction']}": asset.id
                              for asset in sheets}
        ctx.db.save_character(job.project, owner)
    _sync_library(ctx, job.project)

    return {
        "assets": result.asset_ids(),
        "sheets": len(sheets),
        "library": f"3d/{owner.spec.id}/sprites" if owner is not None else None,
        "note": None if owner is not None else
                ("no character claims this mesh: the sheets stay in the store without being filed "
                 "in the library"),
        "clips": result.data.get("clips"),
        "directions": result.data.get("directions"),
        "frames": result.data.get("frames"),
        "frame_size": result.data.get("frame_size"),
        "pivot": result.data.get("pivot"),
        "style": result.data.get("style"),
        "palette": result.data.get("palette"),
        "animated": result.data.get("animated"),
        "engine": result.data.get("engine"),
        "cost_usd": 0.0,
    }


def _sync_library(ctx: Context, project: str) -> None:
    """Keep the readable mirror up to date after each production. Must never
    fail the job that just succeeded."""
    if not project:
        return
    try:
        from ..service.renders import librarian

        # The project space's filing: without it, each production would move
        # brief images -- a card's render -- from `briefing/<section>/` back
        # into `renders/`.
        librarian(ctx.db, ctx.store, ctx.settings).sync_project(project)
    except Exception:
        logger.exception("library sync failed (%s)", project)
    # The briefing and the report follow production: a chat opened after a
    # batch must see that batch, and the project summary must be current. Same
    # rule as the sync -- no reason to fail a job that just succeeded.
    try:
        from ..service import briefing, workspace

        briefing.write_briefing(project)
        workspace.write_report(project)
    except Exception:
        logger.exception("context not regenerated after production (%s)", project)


def handle_explore_style(job: Job, ctx: Context) -> dict[str, Any]:
    payload = job.payload
    pack = StylePack.model_validate(payload["style"])
    result = ExploreStyle(pack, payload["subject"],
                          count=int(payload.get("count", 8)),
                          batch=job.id[:8]).execute(ctx)
    _sync_library(ctx, job.project)
    return {"assets": result.asset_ids(), "cost_usd": result.cost_usd,
            "prompt": result.data.get("prompt")}


def handle_create_entity(job: Job, ctx: Context) -> dict[str, Any]:
    """Simplified path: a 3D entity, from a prompt or an image."""
    payload = job.payload
    style = (StylePack.model_validate(payload["style"])
             if payload.get("style") else None)
    character, report = create_entity(
        ctx,
        prompt=payload["prompt"],
        name=payload.get("name", ""),
        image_model=payload.get("image_model"),
        mesh_model=payload.get("mesh_model"),
        style=style,
        reference_asset_id=payload.get("reference"),
        negative_prompt=payload.get("negative_prompt", ""),
        face_limit=int(payload.get("face_limit", 8000)),
    )
    return {
        "character": character.spec.id,
        "state": character.state.value,
        "exports": character.exports,
        "cost_usd": report.cost_usd,
        "needs_review": report.needs_review or bool(report.outputs.get("notes")),
        "notes": [*report.notes, *report.outputs.get("notes", [])],
        "errors": report.errors,
    }


def handle_generate_image(job: Job, ctx: Context) -> dict[str, Any]:
    """Free generation: prompt + model + options (reference, pose, style)."""
    payload = job.payload
    pack = (StylePack.model_validate(payload["style"])
            if payload.get("style") else None)
    result = GenerateImage(
        payload["prompt"],
        model=payload.get("model"),
        negative_prompt=payload.get("negative_prompt", ""),
        pack=pack,
        reference_asset_id=payload.get("reference"),
        strength=float(payload.get("strength", 0.6)),
        pose=payload.get("pose"),
        width=int(payload.get("width", 768)),
        height=int(payload.get("height", 1152)),
        count=int(payload.get("count", 1)),
        transparent=bool(payload.get("transparent")),
        seed=payload.get("seed"),
        batch=job.id[:8],
        entity=payload.get("entity") or "",
        card=payload.get("card") or "",
        reference_kind=payload.get("reference_kind") or "",
        forge=payload.get("forge") or "",
    ).execute(ctx)
    _sync_library(ctx, job.project)
    return {"assets": result.asset_ids(), "cost_usd": result.cost_usd,
            "prompt": result.data.get("prompt"), "notes": result.notes}


def handle_train_style(job: Job, ctx: Context) -> dict[str, Any]:
    payload = job.payload
    pack = StylePack.model_validate(payload["style"])
    result = TrainStylePack(pack, payload["images"],
                            steps=int(payload.get("steps", 1000))).execute(ctx)
    return {**result.data, "cost_usd": result.cost_usd}


def handle_ping(job: Job, ctx: Context) -> dict[str, Any]:
    """A no-op job, to check that a worker does consume the queue.

    Costs nothing and calls no service: it tells "no worker is running" apart
    from "the job failed"."""
    import platform
    import threading

    return {
        "pong": True,
        "worker": threading.current_thread().name,
        "host": platform.node(),
        "project_root": str(ctx.settings.project_root or ""),
        "echo": job.payload.get("echo", ""),
    }


HANDLERS: dict[str, Callable[[Job, Context], dict[str, Any]]] = {
    "ping": handle_ping,
    "build_character": handle_build_character,
    "explore_style": handle_explore_style,
    "generate_image": handle_generate_image,
    "create_entity": handle_create_entity,
    "train_style": handle_train_style,
    "render_sprites": handle_render_sprites,
}


def run_worker(*, poll_interval: float = 2.0, kinds: list[str] | None = None,
               max_jobs: int = 0, stop: threading.Event | None = None,
               name: str = "worker") -> int:
    """Main loop. Returns the number of jobs processed.

    `stop` lets a host -- the web server -- request a stop. Without it, the
    worker installs its own signal handlers.
    """
    stop = stop or threading.Event()
    _install_signal_handlers(stop)

    # Each project has its queue, in its database: the worker serves them all.
    # The project list is reread on each turn -- a folder opened elsewhere must
    # be served at once.
    from ..service.context import studio

    processed = 0
    logger.info("%s started (jobs: %s)", name, ", ".join(kinds or HANDLERS))
    while not stop.is_set():
        claimed = None
        for space in studio().spaces():
            job = space.queue.claim(kinds=kinds)
            if job is not None:
                claimed = (space, job)
                break
        if claimed is None:
            # `wait` rather than `sleep`: a stop is honoured at once, without
            # waiting for the interval to end.
            stop.wait(poll_interval)
            continue
        space, job = claimed

        handler = HANDLERS.get(job.kind)
        if handler is None:
            space.queue.fail(job, f"unknown job kind: {job.kind}")
            continue

        logger.info("[%s] job %s (%s) started", name, job.id[:8], job.kind)
        started = time.monotonic()
        try:
            with space.queue.hold(job), Context(project=job.project, settings=space.settings,
                                                db=space.db, store=space.store) as ctx:
                result = handler(job, ctx)
            space.queue.complete(job, result, cost=float(result.get("cost_usd", 0.0)),
                                 needs_review=bool(result.get("needs_review")))
            logger.info("[%s] job %s finished in %.1fs", name, job.id[:8],
                        time.monotonic() - started)
        except Exception as exc:
            logger.exception("[%s] job %s failed", name, job.id[:8])
            # What a step already paid before failing, when the error carries it.
            spent = float(getattr(exc, "cost_usd", 0.0) or 0.0)
            space.queue.fail(job, f"{type(exc).__name__}: {exc}", cost=spent)

        processed += 1
        if max_jobs and processed >= max_jobs:
            break

    logger.info("%s stopped after %d job(s)", name, processed)
    return processed
