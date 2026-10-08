# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-10-08

First public release.

### Added

- **From a world card to Godot.** The user declares the sections of their game
  world and writes a card for each entity; the card's workbench chains
  concepts, 3D, animation, sprites and export.
- **Concepts in an imposed pose**, generated through Runware (FLUX, optional
  style LoRA, OpenPose ControlNet, background removal); the pose can be checked
  locally with RTMPose before any 3D is paid for.
- **3D meshes** through Runware (Tripo, Hunyuan 3D, TRELLIS), the direct Tripo
  API (optional), or forged locally and for free with `img2threejs`.
- **Rig and animation by an agent** in Blender, from a self-contained brief:
  a skeleton for a creature, parts for a building or a machine, delivered as one
  GLB carrying all its animations.
- **Sprite sheets rendered from the 3D**: one sheet per animation and per
  direction, one framing and one palette for every frame.
- **Godot export** validated by headless Godot; sheet splitting (SVG, PNG),
  a readable per-project library, visual effects, game renders, icon and prop
  showcases, and a screen editor working on a git branch per screen.
- **The Universe**: each game shader as a live specimen rendered by Godot,
  tunable and previewed at a device's scale (720p to 1440p); "Save" (Ctrl+S)
  writes the tuned settings back into the game, in the shown material or as
  the shader's defaults.
- **Three interfaces over one service layer**: a Tauri desktop application, an
  MCP server for agents (Claude Code, Codex, Gemini…), and a CLI.
- **A bilingual interface**, English or French.
- **Every expense confirmed explicitly**: `confirm=true` for a tool or a route,
  a dialog stating the amount in the interface, `--confirm` on the CLI.
