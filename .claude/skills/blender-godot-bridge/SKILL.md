---
name: blender-godot-bridge
description: Round-trip a mesh between the studio and the user's Blender (retouch, then re-import), or take a studio export into Godot. Not needed for a single Blender or Godot action — call the tool directly.
---

# The Blender ↔ studio ↔ Godot bridge

Three toolsets, one agent. Next to the studio, the repository declares two
**companions**: `blender` (blender-mcp, which drives a Blender session
**opened** with its addon) and `godot` (godot-mcp, which opens the editor, runs
a project and reads its debug output). They are declared for each CLI through
the `scripts/mcp/blender` and `scripts/mcp/godot` launchers: `.mcp.json`
(Claude Code, Kimi), `.codex/config.toml` (Codex, once the repository is
trusted), `.gemini/settings.json` (Gemini). The studio never calls them itself:
the agent passes files from one to the other, by **absolute paths**.

The studio keeps in **headless** Blender what is deterministic: sprite
rendering and the inventory of a GLB. The live Blender is for looking, for
retouching, and for the agent that rigs and animates when the user wants to
watch the work (skill `animation`).

## No preliminary check

Do not check the connection before acting: call the tool you need. **Only if it
fails**, look at why:

- connection refused on the Blender side: no session has its addon connected
  (port 9877). Say so in one sentence — open Blender, "Connect to Claude" in
  the BlenderMCP tab (N key) — without launching Blender yourself;
- `blender` or `godot` tool missing from the list: the server is not approved,
  for the user to enable (`/mcp`).

## Studio → Blender → studio: retouching a mesh

1. **Find the file**: `library_tree(project, "3d", pattern=".glb")` returns the
   absolute path — `<entity>.glb` (rigged and animated, if it has been) and
   `<entity>-bare.glb` (the original mesh). Never the store
   (`.gamestudio/assets/…`), and never write into `.gamestudio/library/`: it is
   a mirror, pruned at each sync.
2. **Open it**: `execute_blender_code` with
   `bpy.ops.import_scene.gltf(filepath="<absolute path>")`, into a collection
   named `gamestudio`. The session is the user's: never clear the scene, or
   save or overwrite their `.blend`, without their agreement.
3. **Look**: `get_viewport_screenshot` from at least two angles, before and
   after the retouch. A retouch that returns has proved nothing.
4. **Retouch**: decimate, recompute normals, set the origin at the feet, fix
   the scale, clean up the materials. An already rigged mesh is retouched with
   its armature and actions: do not rename bones, do not lose an animation on
   export.
5. **Export outside `.gamestudio/`**: `bpy.ops.export_scene.gltf(filepath=
   "<scratchpad>/<entity>.glb", export_format="GLB", use_selection=True)`, with
   the `gamestudio` collection selected; an animated mesh adds
   `export_animation_mode="NLA_TRACKS"` and `export_anim_slide_to_zero=True`.
6. **Bring it back**: `import_mesh(path, project, name, replace=True)` to
   replace the entity's mesh (the original bare mesh is kept), otherwise a new
   name. The inventory it returns lists bones and animations: that is the first
   check. Then `render_sprites` if the deliverable is 2D (see
   `sprites-from-mesh`).

## Studio → Godot: open, run, read the errors

A studio project's Godot project is its folder: `project.godot` at the root,
each entity in `characters/<entity>/<entity>.glb`, each effect in `effects/`
or `vfx/`. **Never create a `project.godot` in a game's folder**: without one,
nothing is copied there, and declaring it is up to the game. A project hosted
by the studio (`data/projects/<project>/`, without a game folder) gets one.

- **The proof stays `gamestudio validate-godot <folder> --scene res://…`**
  (headless Godot). godot-mcp does not replace it: it complements it.
- `launch_editor(projectPath)` to show the scene to the user;
  `run_project(projectPath, scene)` then `get_debug_output` to read runtime
  errors; `stop_project` at the end, always.
- **Do not edit what the studio copies** (`characters/<entity>/<entity>.glb`,
  effect scenes): it is rewritten at every delivery. To compose, create your
  own scene (`create_scene`) that **instances** the studio's. An error in a GLB
  is fixed at the source — in Blender, then a new delivery.
- To another game project: start from the `.gamestudio/exports/<entity>.zip`
  bundle, which keeps the `res://` tree, unzip it at the game root, then
  `update_project_uids(projectPath)` and `run_project` to check.

## What the companions do not do here

blender-mcp can also generate models (Hyper3D Rodin, Hunyuan) and download from
Sketchfab or Poly Haven. **Generation there is excluded**: the studio's paid
paths are Runware and the direct Tripo API, both confirmed with their amount,
and the free path is `local-3d`. A Poly Haven or Sketchfab download happens
only at the user's request, with the licence noted in the project documents.

## Done when

- Retouch: the returned mesh is in `library_tree(project, "3d")`, its inventory
  (bones, animations) matches the one before the retouch, and two captures show
  the expected result.
- Godot: `validate-godot` passes, `run_project` runs with no error in
  `get_debug_output`, and the project is stopped.

A companion that hangs, or a Godot error that comes back after a fix, is
reported to the user instead of looping.

Example: "the 3D knight's feet are in the ground". `library_tree` → import into
Blender → front and side captures → origin reset to the feet → GLB export into
the scratchpad → `import_mesh(..., replace=True)` → identical inventory →
`render_sprites` → `view_asset` on the sheets.

Dependencies: the `scripts/mcp/blender` and `scripts/mcp/godot` launchers
(blender-mcp in `~/.local/share/blender-mcp`, godot-mcp in
`~/.local/share/godot-mcp`; override with `BLENDER_MCP_BIN`, `GODOT_MCP`) — two
third-party servers the user installs —, an open Blender session with the
addon connected, Godot 4. See `animation`, `local-3d`, `sprites-from-mesh`,
`validation`.
