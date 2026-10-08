"""AIR identifiers of the models the studio uses.

Kept here so that switching models is a one-line edit, and to record *why*
each model was chosen.
"""

from __future__ import annotations

# --- image generation --------------------------------------------------------
# FLUX.1 [dev]: good value, accepts LoRA + ControlNet + layerDiffuse.
FLUX_DEV = "runware:101@1"
# FLUX.1 [schnell]: 4 steps, for style exploration where many images are generated.
FLUX_SCHNELL = "runware:100@1"
# FLUX.1 Kontext [dev]: instruction-guided editing, to touch up a reference
# image rather than draw a new one.
FLUX_KONTEXT = "runware:106@1"

# --- ControlNet --------------------------------------------------------------
# The FLUX.1 [dev] ControlNet that takes the OpenPose guide: it imposes the
# pose of a reference or a concept (`vision/poses.py`).
CONTROLNET_POSE = "runware:25@1"

# --- background removal ------------------------------------------------------
# BiRefNet Dis: a dedicated background-removal model, clean on character
# silhouettes (proven in production).
REMOVE_BG = "runware:112@3"

# --- 3D ----------------------------------------------------------------------
# Tripo v3.1: quad meshes, controllable faceLimit, generateParts. The best fit
# for a game pipeline where topology matters.
TRIPO = "tripo:v3.1@0"
# Hunyuan 3D 3.1 Pro: better geometry on complex organic shapes.
HUNYUAN_PRO = "tencent:hunyuan-3d@3.1-pro"
HUNYUAN_RAPID = "tencent:hunyuan-3d@3.1-rapid"
# TRELLIS.2: billed by compute time, the cheapest to iterate with.
TRELLIS = "microsoft:trellis-2@4b"

# --- style training ----------------------------------------------------------
TRAIN_FLUX_DEV = "runware:flux-1-dev@style-lora-training"
