"""Steps that call a paid service (Runware, Tripo): style, LoRA, posed reference, matting, 3D."""

from __future__ import annotations

import io
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from PIL import Image

from ...domain.models import Asset, StylePack
from ...runware import catalog
from ...service.prompts import GUARDRAILS, GUARDRAILS_NEGATIVE
from ...tripo import catalog as tripo
from ...vision.poses import render_openpose, template
from ..base import Context, Step, StepResult

# Phrases added to every character prompt: they decide whether the following
# steps succeed far more than the image's looks do. They live with the
# workbench prompts (`service/prompts.py`): one definition, so that a generated
# reference and a style exploration keep asking for the same thing.
CHARACTER_PROMPT_GUARDS = GUARDRAILS
NEGATIVE_GUARDS = GUARDRAILS_NEGATIVE


def _download(ctx: Context, result: dict[str, Any], suffix: str, *, kind: str,
              meta: dict | None = None) -> list:
    """Fetch the files of a Runware result and put them in the store."""
    assets = []
    for index, entry in enumerate(ctx.runware.files(result)):
        temporary = ctx.work_dir / f"dl_{entry.get('uuid') or index}{suffix}"
        ctx.runware.download(entry["url"], temporary)
        asset = ctx.store.put_file(temporary, kind=kind, meta={**(meta or {}),
                                                               "source_url": entry["url"]},
                                   move=True)
        assets.append(asset)
    return assets


class ExploreStyle(Step):
    """Generate a grid of variants to choose an art direction.

    Deliberately cheap and uncached: it is an exploration, each call should
    give different results. The images the user keeps then feed the LoRA
    training.
    """

    name = "explore_style"
    cacheable = False

    def __init__(self, pack: StylePack, subject: str, *, count: int = 8,
                 size: int = 1024, model: str | None = None,
                 batch: str = "") -> None:
        self.pack = pack
        self.subject = subject
        self.count = count
        self.size = size
        self.model = model or catalog.FLUX_SCHNELL
        # Batch identifier (the job id): it groups the images of one
        # generation in a library folder.
        self.batch = batch

    def inputs(self) -> dict[str, Any]:
        # `created_at` is excluded: a StylePack rebuilt on each attempt would
        # change the fingerprint and pay again for a step already computed.
        return {"pack": self.pack.model_dump(mode="json", exclude={"created_at"}),
                "subject": self.subject,
                "count": self.count, "model": self.model}

    def run(self, ctx: Context) -> StepResult:
        prompt = f"{self.pack.compose_prompt(self.subject)}, {CHARACTER_PROMPT_GUARDS}"
        params: dict[str, Any] = {
            "model": self.model,
            "positivePrompt": prompt,
            "width": self.size,
            "height": self.size,
            "numberResults": self.count,
            "outputFormat": "PNG",
            "outputType": "URL",
            "includeCost": True,
        }
        params["negativePrompt"] = ", ".join(
            p for p in (self.pack.negative_prompt, NEGATIVE_GUARDS) if p)
        if self.pack.lora_config():
            params["lora"] = self.pack.lora_config()

        result = ctx.runware.run("imageInference", params)
        assets = _download(ctx, result, ".png", kind="image",
                           meta={"role": "style_exploration", "prompt": prompt,
                                 "project": ctx.project, "model": self.model,
                                 "batch": self.batch})
        return StepResult(
            data={"prompt": prompt, "count": len(assets)},
            assets=assets,
            cost_usd=float(result.get("cost", 0.0) or 0.0),
        )


class GenerateImage(Step):
    """Free generation: a prompt, a model, and the studio's useful options.

    This is the step behind a world card's "Concepts" stage and the MCP tool
    `generate_image`. Unlike `GenerateReferencePose`, nothing is imposed: the
    project style, the reference image and the pose guide are options.
    Uncached: generating twice must give two results.
    """

    name = "generate_image"
    cacheable = False

    def __init__(self, prompt: str, *, model: str | None = None,
                 negative_prompt: str = "", pack: StylePack | None = None,
                 reference_asset_id: str | None = None, strength: float = 0.6,
                 pose: str | None = None, controlnet_weight: float = 0.85,
                 width: int = 768, height: int = 1152, count: int = 1,
                 transparent: bool = False, seed: int | None = None,
                 batch: str = "", entity: str = "", card: str = "",
                 reference_kind: str = "", forge: str = "") -> None:
        if not prompt.strip():
            raise ValueError("the prompt is empty")
        self.prompt = prompt.strip()
        self.model = model or catalog.FLUX_DEV
        self.negative_prompt = negative_prompt.strip()
        self.pack = pack
        self.reference_asset_id = reference_asset_id
        self.strength = float(strength)
        self.pose = pose
        self.controlnet_weight = float(controlnet_weight)
        self.width = int(width)
        self.height = int(height)
        self.count = max(1, int(count))
        self.transparent = transparent
        self.seed = seed
        # Batch identifier (the job id): groups the images in a library folder.
        self.batch = batch
        # The world-card entity that asked for the image: this files an
        # entity's concepts with it rather than loose.
        self.entity = entity
        # The game-design card that asked for the image (`<section>/<name>`),
        # and where its reference comes from (empty, `sketch`, `render`): this
        # shows it in the card's workbench (`service/cards.py`).
        self.card = card
        self.reference_kind = reference_kind
        # The icon-forge request that ordered the image: its proposals are
        # found through it (`service/forge.py`).
        self.forge = forge

    def inputs(self) -> dict[str, Any]:
        return {"prompt": self.prompt, "model": self.model,
                "negative": self.negative_prompt,
                "pack": (self.pack.model_dump(mode="json", exclude={"created_at"})
                         if self.pack else None),
                "reference": self.reference_asset_id, "strength": self.strength,
                "pose": self.pose, "size": [self.width, self.height],
                "count": self.count, "transparent": self.transparent,
                "seed": self.seed}

    def build_params(self, *, reference_b64: str | None = None,
                     guide_b64: str | None = None) -> dict[str, Any]:
        """Build the `imageInference` request. Pure, so testable without network."""
        prompt = (self.pack.compose_prompt(self.prompt) if self.pack else self.prompt)
        params: dict[str, Any] = {
            "model": self.model,
            "positivePrompt": prompt,
            "width": self.width,
            "height": self.height,
            "numberResults": self.count,
            "outputFormat": "PNG",
            "outputType": "URL",
            "includeCost": True,
        }
        negative = ", ".join(p for p in (
            self.negative_prompt,
            self.pack.negative_prompt if self.pack else "",
        ) if p)
        if negative:
            params["negativePrompt"] = negative
        if self.seed is not None:
            params["seed"] = self.seed
        if self.pack and self.pack.lora_config():
            params["lora"] = self.pack.lora_config()
        if reference_b64:
            # Flat: the v1 API does not know the `inputs` wrapper (checked in
            # production).
            params["seedImage"] = f"data:image/png;base64,{reference_b64}"
            params["strength"] = self.strength
        if guide_b64:
            params["controlNet"] = [{
                "model": catalog.CONTROLNET_POSE,
                "guideImage": f"data:image/png;base64,{guide_b64}",
                "weight": self.controlnet_weight,
                "controlMode": "balanced",
                "startStepPercentage": 0,
                "endStepPercentage": 80,
            }]
        if self.transparent:
            params["layerDiffuse"] = True
        return params

    def run(self, ctx: Context) -> StepResult:
        reference_b64 = None
        if self.reference_asset_id:
            path = ctx.store.path_for(self.reference_asset_id)
            if path is None:
                raise FileNotFoundError(
                    f"reference image {self.reference_asset_id} not found")
            reference_b64 = _b64(path.read_bytes())

        guide_b64 = None
        guide_asset = None
        if self.pose:
            guide = render_openpose(template(self.pose), self.width, self.height)
            buffer = io.BytesIO()
            guide.save(buffer, "PNG")
            guide_b64 = _b64(buffer.getvalue())
            guide_asset = ctx.store.put_bytes(
                buffer.getvalue(), ".png", kind="image",
                meta={"role": "openpose_guide", "pose": self.pose})

        params = self.build_params(reference_b64=reference_b64, guide_b64=guide_b64)
        notes: list[str] = []
        try:
            result = ctx.runware.run("imageInference", params)
        except Exception as exc:
            if not self.transparent:
                raise
            notes.append(f"layerDiffuse unavailable ({exc}); opaque image")
            params.pop("layerDiffuse", None)
            result = ctx.runware.run("imageInference", params)

        assets = _download(ctx, result, ".png", kind="image",
                           meta={"role": "generation", "project": ctx.project,
                                 "prompt": params["positivePrompt"],
                                 "model": self.model, "batch": self.batch,
                                 "reference": self.reference_asset_id,
                                 "pose": self.pose,
                                 **({"entity": self.entity} if self.entity else {}),
                                 **({"card": self.card,
                                     "reference_kind": self.reference_kind}
                                    if self.card else {}),
                                 **({"forge": self.forge} if self.forge else {})})
        return StepResult(
            data={"prompt": params["positivePrompt"], "model": self.model,
                  "count": len(assets),
                  "guide_asset": guide_asset.id if guide_asset else None},
            assets=assets + ([guide_asset] if guide_asset else []),
            cost_usd=float(result.get("cost", 0.0) or 0.0),
            notes=notes,
        )


class TrainStylePack(Step):
    """Train the style LoRA from the approved images.

    This is the step that locks the art direction. Once the LoRA exists, every
    character of the roster goes through it: it is the only way to get twenty
    sprites that look alike instead of twenty styles.
    """

    name = "train_style_pack"

    def __init__(self, pack: StylePack, image_asset_ids: list[str], *,
                 steps: int = 1000, trigger_word: str | None = None,
                 base: str | None = None, captions: dict[str, str] | None = None) -> None:
        self.pack = pack
        self.image_asset_ids = sorted(image_asset_ids)
        self.steps = steps
        self.trigger_word = trigger_word or pack.trigger_word or f"{pack.id}_style"
        self.base = base or catalog.TRAIN_FLUX_DEV
        self.captions = captions or {}

    def inputs(self) -> dict[str, Any]:
        return {"images": self.image_asset_ids, "steps": self.steps,
                "trigger": self.trigger_word, "base": self.base,
                "captions": self.captions}

    def _build_dataset(self, ctx: Context) -> Path:
        """Assemble the ZIP Runware expects: images plus optional captions."""
        archive = ctx.work_dir / f"dataset_{self.pack.id}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for index, asset_id in enumerate(self.image_asset_ids):
                path = ctx.store.path_for(asset_id)
                if path is None:
                    continue
                name = f"{index:03d}{path.suffix}"
                bundle.write(path, name)
                caption = self.captions.get(asset_id)
                if caption:
                    bundle.writestr(f"{index:03d}.txt", caption)
        return archive

    def run(self, ctx: Context) -> StepResult:
        if len(self.image_asset_ids) < 10:
            raise ValueError(
                f"training needs at least 10 images, {len(self.image_asset_ids)} provided"
            )

        archive = self._build_dataset(ctx)
        dataset_uuid = ctx.runware.upload_file(archive)

        air = f"gamestudio:{self.pack.id}@1"
        result = ctx.runware.run(
            "training",
            {
                "model": self.base,
                "inputs": {"dataset": dataset_uuid},
                "importModel": {
                    "air": air,
                    "name": f"{self.pack.name} style",
                    "private": True,
                    "shortDescription": f"Style pack of project {ctx.project}",
                },
                "trainingSteps": self.steps,
                "triggerWord": self.trigger_word,
                "includeCost": True,
            },
            poll_timeout=7200.0,
        )

        trained_air = result.get("air") or air
        assets = _download(ctx, result, ".safetensors", kind="lora",
                           meta={"air": trained_air, "trigger": self.trigger_word})
        return StepResult(
            data={"air": trained_air, "trigger_word": self.trigger_word,
                  "steps": self.steps, "images": len(self.image_asset_ids)},
            assets=assets,
            cost_usd=float(result.get("cost", 0.0) or 0.0),
        )


class GenerateReferencePose(Step):
    """Generate the character in a known reference pose.

    A key point of the studio: the pose is imposed rather than suffered,
    through an OpenPose ControlNet guide. Each joint's position in the produced
    image is known in advance, which gives image-to-3D models a clean A-pose --
    and the agent who will rig the mesh, a body with free limbs.

    `layerDiffuse` directly produces a PNG with a transparent background; an
    image that comes out opaque goes through `remove_background_asset`.

    `negative_prompt` adds to the style's and to the studio's guardrails.
    """

    name = "reference_pose"

    def __init__(self, pack: StylePack, subject: str, *, pose: str = "a_pose",
                 negative_prompt: str = "",
                 width: int = 768, height: int = 1152, seed: int | None = None,
                 controlnet_weight: float = 0.85,
                 controlnet_model: str = catalog.CONTROLNET_POSE,
                 transparent: bool = True, steps: int = 28) -> None:
        self.pack = pack
        self.subject = subject
        self.pose = pose
        self.negative_prompt = negative_prompt.strip()
        self.width = width
        self.height = height
        self.seed = seed
        self.controlnet_weight = controlnet_weight
        self.controlnet_model = controlnet_model
        self.transparent = transparent
        self.steps = steps

    def inputs(self) -> dict[str, Any]:
        return {"pack": self.pack.model_dump(mode="json", exclude={"created_at"}),
                "subject": self.subject,
                "pose": self.pose, "size": [self.width, self.height],
                "seed": self.seed, "weight": self.controlnet_weight,
                "cn": self.controlnet_model, "transparent": self.transparent,
                "steps": self.steps,
                # Absent when empty: a fingerprint that changed for every
                # reference already computed would make them paid again.
                **({"negative": self.negative_prompt} if self.negative_prompt else {})}

    def run(self, ctx: Context) -> StepResult:
        pose_template = template(self.pose)
        guide = render_openpose(pose_template, self.width, self.height)
        buffer = io.BytesIO()
        guide.save(buffer, "PNG")
        guide_asset = ctx.store.put_bytes(buffer.getvalue(), ".png", kind="image",
                                          meta={"role": "openpose_guide", "pose": self.pose})

        prompt = f"{self.pack.compose_prompt(self.subject)}, {CHARACTER_PROMPT_GUARDS}"
        params: dict[str, Any] = {
            "model": self.pack.base_model or catalog.FLUX_DEV,
            "positivePrompt": prompt,
            "negativePrompt": ", ".join(
                p for p in (self.negative_prompt, self.pack.negative_prompt, NEGATIVE_GUARDS)
                if p),
            "width": self.width,
            "height": self.height,
            "steps": self.steps,
            "numberResults": 1,
            "outputFormat": "PNG",
            "outputType": "URL",
            "includeCost": True,
            "controlNet": [{
                "model": self.controlnet_model,
                "guideImage": f"data:image/png;base64,{_b64(buffer.getvalue())}",
                "weight": self.controlnet_weight,
                "controlMode": "balanced",
                "startStepPercentage": 0,
                "endStepPercentage": 80,
            }],
        }
        if self.seed is not None:
            params["seed"] = self.seed
        if self.pack.lora_config():
            params["lora"] = self.pack.lora_config()
        if self.transparent:
            params["layerDiffuse"] = True

        notes: list[str] = []
        cost = 0.0
        try:
            result = ctx.runware.run("imageInference", params)
        except Exception as exc:
            if not self.transparent:
                raise
            # Not every model supports layerDiffuse: retry without it, then
            # cut out separately.
            notes.append(f"layerDiffuse unavailable ({exc}); falling back to matting")
            params.pop("layerDiffuse", None)
            result = ctx.runware.run("imageInference", params)
        cost += float(result.get("cost", 0.0) or 0.0)

        assets = _download(ctx, result, ".png", kind="image",
                           meta={"role": "reference_pose", "pose": self.pose,
                                 "prompt": prompt, "guide": guide_asset.id})

        # The image must come out cut out; but a failed matting must never
        # lose the generation just paid for: the opaque image is kept and
        # flagged for review.
        needs_review = False
        if assets and is_opaque(ctx.store.path_for(assets[0].id)):
            original = assets[0]
            try:
                cutout, extra = remove_background_asset(
                    ctx, original.id, meta={"role": "reference_pose", "pose": self.pose})
                cost += extra
                # The paid original stays in the database under its own role:
                # the cut-out reference cites it (`source`), and the store keeps
                # no file the database does not know.
                original.meta["role"] = "reference_opaque"
                assets = [cutout, original]
                notes.append(f"opaque image: matting applied ({cutout.meta['matting']})")
            except Exception as exc:
                notes.append(f"matting failed ({exc}): opaque image to review")
                needs_review = True

        return StepResult(
            data={"pose": self.pose, "guide_asset": guide_asset.id, "prompt": prompt,
                  "keypoints": pose_template.scaled(self.width, self.height),
                  "size": [self.width, self.height]},
            assets=[*assets, guide_asset],
            cost_usd=cost,
            needs_review=needs_review,
            notes=notes,
        )


def is_opaque(path: Path) -> bool:
    """True when fewer than 2% of the pixels are transparent: no silhouette.

    An image-to-3D model needs a transparent background to separate the entity
    from its surroundings. The threshold tolerates an accidentally transparent
    edge or corner.
    """
    with Image.open(path) as image:
        alpha = image.convert("RGBA").getchannel("A")
    transparent = sum(alpha.histogram()[:250])
    return transparent < alpha.width * alpha.height * 0.02


def remove_background_asset(ctx: Context, asset_id: str, *,
                            meta: dict[str, Any]) -> tuple[Asset, float]:
    """Cut out an image of the store: local rembg if installed (free), else BiRefNet on Runware.

    BiRefNet costs about $0.0006. This is the pipeline's only matting -- a
    generated reference and a provided image both go through it -- and its only
    fallback. The cut-out image is saved in the database with `meta`, the
    method used (`matting`) and the original image (`source`). A matting that
    returns nothing raises: the original is never returned in its place.
    """
    from ...vision.detect import ToolUnavailable, remove_background

    path = ctx.store.path_for(asset_id)
    if path is None:
        raise FileNotFoundError(f"image {asset_id} not found")
    try:
        with Image.open(path) as image:
            cut = remove_background(image)
    except ToolUnavailable:
        cut = None

    if cut is not None:
        buffer = io.BytesIO()
        cut.save(buffer, "PNG", optimize=True)
        produced = ctx.store.put_bytes(buffer.getvalue(), ".png", kind="image",
                                       meta={**meta, "matting": "rembg", "source": asset_id})
        cost = 0.0
    else:
        result = ctx.runware.run("imageBackgroundRemoval", {
            "model": catalog.REMOVE_BG,
            "inputImage": f"data:image/png;base64,{_b64(path.read_bytes())}",
            "outputFormat": "PNG",
            "outputType": "URL",
            "includeCost": True,
        })
        found = _download(ctx, result, ".png", kind="image",
                          meta={**meta, "matting": "birefnet", "source": asset_id})
        if not found:
            raise RuntimeError("the cut-out returned no image")
        produced = found[0]
        cost = float(result.get("cost", 0.0) or 0.0)
    ctx.db.save_asset(produced)
    return produced, cost


class MeshProvider(ABC):
    """Where a 3D mesh comes from.

    Two routes exist: Runware (`RunwareMesh`) and the direct Tripo API
    (`TripoMesh`). Another one is added without touching the step: a class,
    then `register_mesh_provider`. `GenerateMesh` only knows this protocol, and
    the rest of the studio only knows `generate_mesh`.

    `id` names the provider everywhere (CLI, MCP, interface); `paid` says
    whether the user's explicit consent is needed before calling it.
    """

    id: str = ""
    label: str = ""
    paid: bool = True

    @abstractmethod
    def generate(self, ctx: Context, image: Path, *, model: str, face_limit: int,
                 quad: bool, pbr: bool, detailed: bool,
                 seed: int | None) -> tuple[list[Any], dict[str, Any]]:
        """Return the produced assets and what needs to be known about them."""


class RunwareMesh(MeshProvider):
    """The studio's provider: a hosted 3D model, billed per call."""

    id = "runware"
    label = "Runware"
    paid = True

    def generate(self, ctx: Context, image: Path, *, model: str, face_limit: int,
                 quad: bool, pbr: bool, detailed: bool,
                 seed: int | None) -> tuple[list[Any], dict[str, Any]]:
        params: dict[str, Any] = {
            "model": model,
            "inputs": {"images": [f"data:image/png;base64,{_b64(image.read_bytes())}"]},
            # On Runware a quad mesh only comes out as FBX; GLB stays the studio's
            # exchange format because Blender and Godot import it natively.
            "outputFormat": "FBX" if quad else "GLB",
            "settings": {
                "faceLimit": face_limit,
                "pbr": pbr,
                "texture": True,
                "quad": quad,
                "imageAutoFix": True,
                "orientation": "align_image",
                "geometryQuality": "detailed" if detailed else "standard",
                "textureQuality": "detailed" if detailed else "standard",
            },
            "includeCost": True,
        }
        if seed is not None:
            params["seed"] = seed

        result = ctx.runware.run("3dInference", params, poll_timeout=1800.0)
        suffix = ".fbx" if quad else ".glb"
        assets = _download(ctx, result, suffix, kind="mesh",
                           meta={"role": "raw_mesh", "model": model,
                                 "face_limit": face_limit})
        if not assets:
            raise RuntimeError("the 3D generator returned no file")
        return assets, {"model": model, "format": suffix.lstrip("."),
                        "cost_usd": float(result.get("cost", 0.0) or 0.0)}


class TripoMesh(MeshProvider):
    """The direct Tripo API: the second paid route.

    It exists because Runware hosts only one Tripo model. Here P2 (native
    quads, 48-50,000 faces) and the H family are reachable, with a GLB where
    Runware forces an FBX as soon as quads are requested.

    What this provider deliberately does not do: Tripo's auto-rig and
    animation retargeting. The studio does not rig through diffusion -- the
    rig is the work of the agent who receives the brief (`service/handoff.py`).

    `model` is an identifier of the Tripo catalog (`tripo/catalog.py`), not an
    AIR: `P2-20260801`, `P1-20260311`, `tripo-v3.1`.
    """

    id = "tripo"
    label = "Tripo (direct API)"
    paid = True

    def generate(self, ctx: Context, image: Path, *, model: str, face_limit: int,
                 quad: bool, pbr: bool, detailed: bool,
                 seed: int | None) -> tuple[list[Any], dict[str, Any]]:
        # Refusing beats ignoring: a triangle mesh billed for requested quads
        # would be a lie in the catalog. H's quads force an FBX, P2's come out
        # as GLB; the studio only renders GLB to sprites, so quads are only
        # requested from P2.
        supported = tripo.supports(model, "quad")
        if quad and not supported:
            raise ValueError(
                f"{model} does not produce quads (native quads come from {tripo.P2})")
        if quad and supported == "fbx":
            raise ValueError(
                f"{model} outputs quads as FBX, which the studio cannot render to sprites; use "
                f"{tripo.P2} for a quad mesh")

        # P2's quads have their own bounds: beyond them, the API would refuse.
        limits = tripo.face_limits(model, quad=bool(quad))
        limit = tripo.clamp_face_limit(model, face_limit, quad=bool(quad))
        quality = "detailed" if detailed else "standard"
        payload: dict[str, Any] = {
            "model": model,
            "face_limit": limit,
            "texture": True,
            "pbr": pbr,
            "texture_quality": quality,
        }
        if tripo.supports(model, "geometry_quality"):
            payload["geometry_quality"] = quality
        if quad:
            payload["quad"] = True
        if seed is not None:
            payload["model_seed"] = seed

        result = ctx.tripo.generation(image, payload, timeout=1800.0)
        temporary = ctx.work_dir / f"tripo-{result['task_id']}.glb"
        ctx.tripo.download(result["url"], temporary)
        try:
            asset = ctx.store.put_bytes(
                temporary.read_bytes(), ".glb", kind="mesh",
                meta={"role": "raw_mesh", "provider": self.id, "model": model,
                      "task_id": result["task_id"], "face_limit": limit,
                      "quad": quad, "credits": result["credits"]})
            ctx.db.save_asset(asset)
        finally:
            temporary.unlink(missing_ok=True)

        cost = float(result["cost_usd"]) or tripo.usd_for(
            model, texture_quality=quality,
            geometry_quality=quality if tripo.supports(model, "geometry_quality")
            else "standard", quad=quad)
        return [asset], {
            "model": model, "format": "glb", "cost_usd": cost,
            "provider": self.id, "task_id": result["task_id"],
            "credits": result["credits"], "face_limit": limit, "quad": quad,
            "notes": (f"face budget lowered from {face_limit} to {limit}"
                      if limit != face_limit else ""),
            "limits": list(limits),
        }


# The known providers, by identifier. A direct API is added here.
MESH_PROVIDERS: dict[str, MeshProvider] = {RunwareMesh.id: RunwareMesh(),
                                           TripoMesh.id: TripoMesh()}

# Which model belongs to which route. A list, not a guess: an unknown
# identifier stays with Runware, which will refuse it naming what it knows.
# This lets the interface send nothing but an identifier.
PROVIDER_MODELS: dict[str, set[str]] = {
    TripoMesh.id: {tripo.P1, tripo.P2, tripo.H31, tripo.H30, tripo.H25},
}


def provider_for(model: str, default: str = RunwareMesh.id) -> str:
    """The route that serves this model, from its identifier."""
    for identifier, known in PROVIDER_MODELS.items():
        if model in known:
            return identifier
    return default


def mesh_provider(identifier: str) -> MeshProvider:
    """The requested provider, or a refusal that says which ones exist."""
    provider = MESH_PROVIDERS.get(identifier)
    if provider is None:
        raise ValueError(f"unknown mesh provider: “{identifier}” (known: "
                         f"{', '.join(MESH_PROVIDERS)})")
    return provider


def register_mesh_provider(provider: MeshProvider) -> None:
    """Register a provider: that is all adding an API takes."""
    MESH_PROVIDERS[provider.id] = provider


class GenerateMesh(Step):
    """Go from the reference image to the textured 3D mesh.

    `faceLimit` is imposed: a game mesh must hold a triangle budget, and
    generators readily produce 200,000-face meshes.

    The provider is a parameter, not a dependency: `provider="runware"` for
    Runware, `provider="tripo"` for the direct API, the same step for both.
    Without `provider`, the model names it: Tripo catalog identifiers cannot be
    mistaken for Runware AIRs, and a caller holding only an identifier (the
    interface, an agent) need not know which of the two serves it.
    """

    name = "generate_mesh"

    def __init__(self, image_asset_id: str, *, model: str | None = None,
                 face_limit: int = 8000, quad: bool = False, pbr: bool = True,
                 detailed: bool = False, seed: int | None = None,
                 provider: str | None = None) -> None:
        self.image_asset_id = image_asset_id
        self.model = model or catalog.TRIPO
        self.provider = provider or provider_for(self.model)
        self.face_limit = face_limit
        self.quad = quad
        self.pbr = pbr
        self.detailed = detailed
        self.seed = seed

    def inputs(self) -> dict[str, Any]:
        return {"image": self.image_asset_id, "provider": self.provider,
                "model": self.model,
                "face_limit": self.face_limit, "quad": self.quad, "pbr": self.pbr,
                "detailed": self.detailed, "seed": self.seed}

    def run(self, ctx: Context) -> StepResult:
        path = ctx.store.path_for(self.image_asset_id)
        if path is None:
            raise FileNotFoundError(f"image {self.image_asset_id} not found")

        assets, data = mesh_provider(self.provider).generate(
            ctx, path, model=self.model, face_limit=self.face_limit,
            quad=self.quad, pbr=self.pbr, detailed=self.detailed, seed=self.seed)
        return StepResult(data={**data, "provider": self.provider}, assets=assets,
                          cost_usd=float(data.get("cost_usd", 0.0) or 0.0))


def _b64(payload: bytes) -> str:
    import base64
    return base64.b64encode(payload).decode("ascii")
