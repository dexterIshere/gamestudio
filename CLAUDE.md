# gamestudio — guide for agents

A game-asset studio: from a world card to a rigged, animated 3D entity, ready
for Godot. Two paid paths provide the look (Runware; the direct Tripo API,
optional); rig and animation are an agent's work in Blender; inventory, sprite
rendering and export are local code. The only 2D the studio makes is concepts;
a 2D game gets sprites rendered from the 3D.

## Before working

A session starts informed: the `SessionStart` hook (`.claude/settings.json` →
`gamestudio context session`) injects the briefing, and the files below are
imported. **Do not re-explore the repository**: start from the code map and
open the files it points to.

@context/identity.md
@context/goals.md
@context/preferences.md
@context/codemap.md

`context/identity.md`, `goals.md` and `preferences.md` are the user's personal
notes: local, untracked, seeded by the studio from a template and written by
hand (what they make, where they are heading, how they work). They may be
absent. `context/briefing.md` (projects, jobs, costs, paths) and
`context/codemap.md` (each file's opening sentence) are **generated**
(`gamestudio context show --write`, `gamestudio context codemap`); `make check`
rejects a stale code map. A lasting decision is written down
(`studio_note_write`) so the next conversation knows it.

## The rules

1. **Every expense needs explicit confirmation**: `confirm=true` for a tool or
   a route, a dialog that states the amount in the UI, `--confirm` on the CLI.
   An agent confirms only after the user agreed in the conversation. Free
   first (local, Blender, `img2threejs`). Orders of magnitude: image ~$0.006,
   8-image exploration ~$0.01, mesh $0.15–1.25 (`mesh_providers` gives each
   model's price, `app/src/lib/catalog.ts` shows it), LoRA ~$1.45.
2. **An operation that returns has proved nothing.** Look at an image
   (`view_asset`), validate a Godot scene headless (`gamestudio
   validate-godot`), look at a mesh from two angles.
3. **A failing criterion goes back to the user** instead of paying in a loop.
   A paid job is never retried on its own.
4. **Rig and animation are an agent's work**, never a studio tool's nor a
   diffusion model's: keyframes or procedural in Blender, delivered as one GLB
   that carries all its animations (skill `animation`).
5. **The user's game is read; its docs are authoritative** and are cited, not
   copied. The studio writes into it only on an explicit gesture: the Godot
   export of an entity, adopting a forge icon, the trash (delete, restore), the
   screen editor (one branch per screen, which the user merges), "Save" in the
   Universe (only the `shader_parameter/` lines of the shown use, or the
   shader's defaults, written in place). Never create a `project.godot` in a
   game folder.
6. **No verdict on what is in the game**: present means kept; what needs rework
   is discussed (an element's chat button opens an agent on it).
7. **Destructive gestures belong to the user**: what no tool exposes is listed,
   with its reason, in `mcp_server.HUMAN_ONLY`.
8. **The world has no default sections**: an agent creates one only when the
   user asks.
9. **The repository is public** (PolyForm Shield, `LICENSE`): no machine path,
   no key, no private game name in a tracked file. A script that imports `bpy`
   (`blender/bl_*.py`) is GPL-3.0-or-later and opens with
   `# SPDX-License-Identifier: GPL-3.0-or-later`; third-party material keeps its
   licence (`docs/THIRD_PARTY_NOTICES.md`, `.claude/skills/img2threejs/VENDOR.md`).

## The studio on one page

- **An operation is defined once**, in `src/gamestudio/service/`, and served by
  three interfaces: the HTTP API (`api/app.py`, used by the window), the MCP
  server (`mcp_server.py`), the CLI (`cli.py`). A new capability goes into the
  service, then is exposed — never reimplemented in a route or a tool.
  `tests/test_agent_parity.py` rejects a route that is neither an MCP tool nor
  in `HUMAN_ONLY`.
- **A project is a folder on the machine.** Everything the studio keeps for a
  game lives in `<folder>/.gamestudio/` (recipe, documents, database, store,
  library, working files); the folder is the Godot root where exports land. A
  project without a folder is hosted under `data/projects/<project>/.gamestudio/`.
  Every project path goes through `store/folders.py` (`project_paths`); each
  project has its own space (`service.context.space`) and no read crosses two
  projects. The engine (code, skills, keys, logs) is never copied into a
  project.
- **The library** (`<folder>/.gamestudio/library/`) is the readable mirror of
  the store, regenerated after every production: cite it to another agent,
  never the store (`.gamestudio/assets/`, addressed by hash). Its layout is
  described once (`Librarian.index_project`); do not write into it. Moving or
  renaming goes through `store/curation.py`, which refuses to touch what a
  production holds.
- **Driving through MCP**: `studio_briefing` first. From another repository,
  connect the server with the binary's absolute path and
  `GAMESTUDIO_HOME=<studio root>`. Claude Code truncates MCP server
  instructions at 2048 characters (`tests/test_survey.py` keeps them under):
  they hold only intent → tool, and the rules no tool carries.
- **Handing off work**: `service/handoff.py` writes a self-contained brief under
  `.gamestudio/workspace/briefs/` (rig and animation, VFX, card, showcase item,
  shader, procedure). The agent that receives a brief follows it to the end.
- **Seeing the game**: `render_scene` draws it with the game's own engine,
  off-screen — prefer it to any screenshot.
- **Adding a 3D path**: a `MeshProvider` (`id`, `paid`, `generate`) registered
  with `register_mesh_provider`; `provider_for` routes a model to it.
- **Skills** (`.claude/skills/`) give the procedure for a deliverable: in what
  order, under which conditions, how to tell it is done. The index is
  `.claude/skills/README.md` (generated, `gamestudio skills index`); the entry
  point is `production-routing`. Read a skill when the task is the procedure it
  describes, never as a precaution. A new procedure is requested
  (`skill_brief`) and written by an agent (`write_skill`).
- **The application** (`make studio`) is a Tauri shell that starts the
  repository's Python server and follows the code
  (`app/src-tauri/src/update.rs` rebuilds it when a source changed). It runs
  only from the repository: there is no standalone package.

## Verify (free, offline)

```bash
make check                   # ruff, pytest, skills, code map, front-end types and translations
python scripts/smoke_3d.py   # simulated agent delivery → sprites → headless Godot
```

Run the second before changing the Godot export, the Blender scripts, sprite
rendering or mesh import. CI (`.github/workflows/checks.yml`) runs both.

## Code conventions

- **Everything is in English**: identifiers, comments, docstrings, server
  messages, CLI, MCP tools, skills, briefs, docs. Comments say what the code
  does and why, concisely — never the story of how it was written. Each file
  opens with one sentence saying what it is for (the code map reads it).
- **The UI speaks English or French.** Every displayed phrase goes through
  `t("…")` (English is the key; `tn()` for a plural, `tc()` with a context),
  with its French in `app/src/locales/fr.ts`; a text from the server goes
  through `tr()`, and every message the server raises has its template (`{0}`,
  `{1}`…) in `app/src/locales/fr-server.json` (`tests/test_translations.py`).
  `make check` rejects hard-coded text and a phrase without its translation.
  `docs/README.fr.md` is the French translation of `README.md`: change both.
- Canonical skeleton: Blender bone names (`hips`, `upper_arm.L`…) in
  `domain/skeleton.py`, used when the body suits it; a cycle is named
  `<name>_loop` (Godot loops it and calls it `<name>`).
- What is computed for an entity is computed **once for all its frames and
  directions** (crop, pixel-style palette); a frame grid is never cropped cell
  by cell. The boxes of an SVG sheet are upper bounds: an icon is never cropped.

## Front-end conventions

- The design system lives **entirely** in `app/src/styles.css` and its
  variables: no hard-coded colour. The component vocabulary is
  `app/src/components/ui.tsx` (`Panel`, `Setting`, `Field`, `Select`, `Seg`,
  `Facts`, `Dialog`, `State`…): a page does not reinvent a settings row or use
  a native `<select>` (white under WebKitGTK).
- **Colour carries one meaning only**: orange → gold for action, selection and
  focus; emerald for what is finished or connected; amber for what costs money
  or awaits review; red for what is destroyed or failed. The light `--board`
  surface is only for judging an image.
- **An icon is 16, 20 or 24 px**, and an icon button's glyph is a path
  (`viewBox="0 0 24 24"`, `stroke-width: 1.7`), never a character.
- **No explanatory text on screen**, except what prevents a mistake (an input
  constraint), what commits an expense or a destruction, and what reports a
  state or a refusal. A paid action is confirmed in a `Dialog` whose button
  states the amount ("Pay $0.25 and start").
- Layout holds at the real window sizes (studio 1360×860, minimum 1040×640;
  Chats 640×900, minimum 520×360): what does not fit folds or grows, never a
  fixed height. Measurements use `.mono` or `.num`.
- Nothing is generated without context: generation starts from the workbench
  of a world card (Card → Concepts → 3D). The models offered and their prices
  live once, in `app/src/lib/catalog.ts`.
