# gamestudio

*English · [Français](README.fr.md)*

A game asset studio: from a world card written in plain language to a rigged
and animated 3D entity, ready to open in Godot — and rendered into sprite sheets
when the game is 2D.

```
card ──► concepts (imposed pose) ──► 3D mesh ──────────► rig + animations ──► .glb ──► Godot
          Runware                     Runware, Tripo      an agent, Blender    └──► sprites, N dir.
                                      or local (free)
```

The repository is in English; the desktop interface speaks English or French.
[`README.fr.md`](README.fr.md) is the French translation of this file.

---

## What the system does, and what it does not

**Two paid paths provide the look.** Runware: image generation, a style LoRA
trained on your own references, ControlNet, background removal, and image-to-3D
(Tripo, Hunyuan 3D, TRELLIS). The Tripo API called directly, optional: the
models Runware does not host (P2 and its native quads, P1, the H family). A free
path sits beside them: forging the mesh locally (`img2threejs`).

**An agent does the rig and the animations.** No model rigs well, diffusion
animation drifts from one frame to the next, and no rigging tool coded into the
studio would match the software built for it. The 3D step of a card therefore
hands its entity to an agent (Claude Code, Codex…), which rigs and animates it
in Blender — a skeleton for a creature, parts for a building or a machine — and
returns a GLB that carries all its animations.

**The studio's code does what must be exact**: the inventory of the delivered
GLB, sprite rendering (one framing and one palette for every frame), the Godot
export and its headless validation.

**Every expense requires explicit confirmation**: `confirm=true` for an MCP
tool or an API route, a dialog that states the amount in the interface, a
confirmation in the CLI.

---

## The central idea: impose the pose rather than suffer it

Generation does not produce an arbitrary image to be analysed afterwards. It
produces a concept in a **reference pose known in advance**, guided by an
OpenPose skeleton passed through ControlNet. 3D generators rebuild a clean A-pose
far better than an arbitrary pose, and the agent that will rig the mesh finds
limbs clear of the body. Pose estimation (RTMPose, local) checks that a concept
holds the pose before you pay for its 3D.

---

## The rig: an agent, a convention

The pivot is the **bone naming convention** (`hips`, `upper_arm.L`, `shin.R`…,
`domain/skeleton.py`). The agent that rigs a body suited to it (biped,
quadruped, winged biped) uses it; elsewhere, it names its bones or parts in
plain words. On delivery, the studio measures the share of canonical names — a
measurement, not a condition. A cycle is named `<name>_loop`: Godot loops it on
import and calls it `<name>`, as the sprite sheets do.

---

## Requirements

The studio runs **on Linux**: the shell uses `prctl` and `setsid`, and updates go
through `make`. macOS and Windows are not supported.

| Tool | Version | Used for | |
|---|---|---|---|
| Python | ≥ 3.11 | the server, the CLI, the MCP server | required |
| Node.js | ≥ 20.19 or ≥ 22.12 (Vite 8) | building the front end | required for the app |
| Rust (`rustup`) | ≥ 1.77 | the Tauri shell | required for the app |
| webkit2gtk-4.1, libayatana-appindicator | — | the window, the system tray icon; with Tauri's other [Linux prerequisites](https://v2.tauri.app/start/prerequisites/#linux) | required for the app |
| Blender | ≥ 4.2 (CI: 5.2.1) | sprites, GLB inventory, smoke test; the rigging agent works in it | required for 3D |
| Godot | 4 (CI: 4.4) | headless validation, game renders, showcases, art-direction bench, screen editor | required for Godot |
| gamescope or xvfb-run | — | drawing Godot off-screen (without them, a window flashes open for each render) | recommended |
| git | — | the screen editor (one branch per screen) | required for the editor |
| ffmpeg | — | extracting frames from a reference video | optional |
| `rigtools` extra | `make install-rigtools` | local RTMPose pose and background removal (rtmlib, onnxruntime, rembg) | optional |
| `vector` extra | `.venv/bin/pip install -e '.[vector]'` | rasterising an SVG icon to PNG (cairosvg) | optional |

Keys go in `.env`: `RUNWARE_API_KEY` for any paid generation, `TRIPO_API_KEY`
(optional) for the direct Tripo API. Without a key, everything local works.

---

## Installation

The studio runs **from its repository**: there is no package to install.

```bash
git clone <the repository> && cd gamestudio
make install                 # .venv + Python dependencies
make install-app             # front-end dependencies (npm)
make install-rigtools        # optional: local RTMPose pose and background removal
cp .env.example .env         # put RUNWARE_API_KEY (and TRIPO_API_KEY) in it
make link                    # `gamestudio` in ~/.local/bin
gamestudio doctor            # what works here, what is missing, and what to do
```

The first `make studio` compiles the shell — a few minutes; later ones start at
once. `gamestudio doctor --check` exits with an error on a real blocker,
`--json` prints the raw report. Without `make link`, the command is
`.venv/bin/gamestudio`.

`gamestudio` finds the studio root by walking up to the first `.env`, the way
`git` finds its repository. **From a game folder**, which has none, you have to
tell it where the studio is — once and for all, in your shell profile:

```bash
export GAMESTUDIO_HOME=/path/to/gamestudio
```

Blender is found on the `PATH`, or through `BLENDER_BIN` in `.env`.
`gamestudio mesh providers` tells which 3D paths are ready and at what price,
`gamestudio mesh balance` what is left on the Tripo account (read-only, free).

**Getting started**: [`docs/getting-started.md`](docs/getting-started.md) gives
the order of operations and says where everything lands.

The interface speaks English or French: the language is chosen at first launch,
then in Control room › This machine.

---

## Usage

### In the application

```bash
make studio           # the window opens, the terminal is released
```

1. Workspace selector (top of the rail) › **Open a folder…**: the game folder
   becomes the project. Everything the studio keeps for that game will go into
   its `.gamestudio/`.
2. **The world** › "+": declare a section (Characters, Buildings…), then write
   an entity's card in it — what it is, what it is for, what it looks like.
3. The card's **workbench** chains **Concepts** (batches seeded by the card;
   you keep one), **3D** (the mesh, paid and confirmed, or a locally forged
   `.glb`), **Animate** (the brief handed to an agent, which rigs and animates
   in Blender), then **Sprites** and **Export**.

`make studio` detaches the application: closing the terminal does not close
the studio. The window starts the Python server itself on a free port and stops
it when leaving — no process outlives it, even when it is killed by a signal.
Logs go to `data/studio.log`. `make desktop-entry` adds it to the applications
menu.

The application follows the code: at every start it asks the `Makefile` whether
it is up to date (`make update-check`) and, if not, rebuilds itself in an update
window before opening.

### From the command line

```bash
export GAMESTUDIO_HOME=/path/to/gamestudio
cd ~/games/my-game && gamestudio init my-game  # creates .gamestudio/recipe.yaml

# 1. Find the art direction (paid)
gamestudio style explore .gamestudio/recipe.yaml -s "a knight in leather armour" --confirm
# keep 10 to 20 images, then:
gamestudio style train .gamestudio/recipe.yaml -i <id> -i <id> ... --confirm
# copy lora_air and trigger_word into the recipe: the style is frozen

# 2. Build the roster: A-pose references, bare meshes (paid)
gamestudio build .gamestudio/recipe.yaml --confirm
gamestudio status .gamestudio/recipe.yaml
```

The rig and the animations go through the entity's card. An entity built by
`build` has none yet: Control room › "Give it a card" attaches it to a world
section. Once the card has gone 3D, `gamestudio world brief <project> <section>
<card>` writes the brief the agent follows (which is what "Animate" does in the
workbench). The delivered GLB is validated by Godot:

```bash
gamestudio validate-godot . --scene res://characters/<entity>/<entity>.glb
```

Other entry points:

```bash
make studio-dev               # development: Vite with hot reload
gamestudio worker             # worker alone (another machine, or dedicated to rendering)
gamestudio mcp                # MCP server for an agent (built-in worker)
gamestudio serve              # the server alone, without a window
```

Why a thread and not a second process: the worker computes almost nothing
itself, it waits on Blender (`subprocess`) and the network (`httpx`), which both
release the GIL. The interface stays responsive during a render. Separating
them only matters to truly isolate the two — a dedicated machine, or a restart
that does not interrupt a training run.

### The recipe

A YAML file, `.gamestudio/recipe.yaml`, describes **what you want**, not how to
make it:

```yaml
version: 1
project: my-game
style:
  prompt_prefix: "hand-painted game character, muted earth palette"
  lora_air: gamestudio:my-game-style@1     # once trained
characters:
  - id: ranger
    subject: "a forest ranger in a green hooded cloak, short bow"
    pipelines: [mesh3d]
```

It is the unit of re-execution. Each step carries a fingerprint computed from
its inputs: change one entity and run again, and only that one is recomputed.
That matters when a step costs $0.40 or ten minutes. Every field:
[`docs/recipe.md`](docs/recipe.md).

---

## The desktop application

A **Tauri** shell (under 2,000 lines of Rust) and a **React** front end. The
Rust picks a free port, starts the Python server on it and stops it when
leaving; it opens the windows (the studio, the Chats, the update), holds the
system tray icon and the rebuild. All the business logic stays in Python, in
`service/`, shared word for word with the MCP server and the CLI — an operation
is defined there once, and the three interfaces serve it.

The interface is dense and dark, and colour carries one meaning and one only: a
single vivid family, from orange to gold, for action, selection and focus;
emerald for what is finished or connected; amber for what costs money or awaits
review; red for what is destroyed or has failed. The only light surface is
reserved for artwork: it is there to judge an image.

The rail groups the pages by intent:

- **Discover** — **Control room**: what needs doing now (failures, roster to
  build, outputs to review, missing tools), the active project (entities, job
  log, recipe, cost) and this machine (diagnosis, MCP connections, procedures,
  language). **Context**: what every agent reads on arrival.
- **Team › At work** — the agents at work, each in its terminal, live: you watch
  them without driving them.
- **The world** — empty by default: the sections the user declares. Each card
  opens its **workbench** (Card → Concepts → 3D). The 3D step is a viewport:
  Blender navigation (`1/3/7` front/side/top, `F` to frame), skeleton overlay,
  the GLB's animations, turntable.
- **Game design** — **Mechanics**; **Interface**, where each screen card shows
  the game's render by Godot and is edited on a git branch of its own, with
  **Icons** and **Props**, showcases of the game's own elements; **VFX**, the
  concept of an effect, handed to an agent that builds it in Godot; **Art
  direction**, each of the game's shaders as a live specimen.
- **Development** — **Ideas**, **Notes**, **Devlog**, **Documents** (the
  project's texts), **Library** (everything the project has produced) and
  **Compare** (two images or two meshes under the same eye).
- **Chats** — a separate window: one tab per agent (Claude Code, Codex, Kimi
  Code…), started in the project folder with the studio's tools.

### From 3D mesh to 2D sprites

From the 3D step of a card, the "Sprites" tool renders a mesh from N
directions and makes one sheet per animation and per direction, plus a JSON
atlas. It is **free**: headless Blender, locally, no network call. The result is
stored in `3d/<entity>/sprites/`.

Three styles, which differ by engine as much as by light:

| style | what it does | when to choose it |
|---|---|---|
| **normal** | Workbench with flat light, the texture's exact colours | the art direction already comes from the 2D image; also the fastest |
| **prerender** | EEVEE, three-point lighting, shadows and occlusion, rendered at double size then reduced | you want volume baked into the sprite — Diablo-style pre-rendering |
| **pixel art** | no anti-aliasing, rendered at target size, reduced palette | the sprite must stay crisp under a magnifier |

Two rules carry the quality of the result, and follow from the same idea: what
defines the character is computed **once for all its frames and all its
directions**, never image by image.

- The **crop** is shared: cropping each frame to its own content would make the
  character hop, since its pivot would move.
- The pixel style's **palette** is shared: a palette per frame would make the
  colours jump from one angle to the next.

The elevation picks the genre: `0°` side view (platformer), `30-45°` isometric
(RPG, tactics), `90°` top-down. A mesh without animation gives a turnaround —
one frame per direction, which is what you want from an object or a prop.

### Driving the studio with an agent

The repository's `.mcp.json` connects Claude Code to the studio's MCP server
(`gamestudio mcp`); `.codex/config.toml` and `.gemini/settings.json` do the same
for Codex and Gemini. The agent can inspect the state, look at the produced
images, queue builds, split a sheet, and follow the brief that hands it the rig
and animations of an entity — paid operations require explicit confirmation.
The server embeds its own worker: nothing else to start. See `CLAUDE.md`.

The same files declare two optional companions, `blender` (blender-mcp) and
`godot` (godot-mcp), through the `scripts/mcp/` launchers. They are third-party
projects, which you install yourself if you want them; the studio works without
them (skill `blender-godot-bridge`).

#### From another project — a game repository, for instance

This is the useful case: the agent working on the game wants to pick from the
studio's library. All it takes is a `.mcp.json` in that repository, with the
absolute path of the binary and **a single variable**, `GAMESTUDIO_HOME`:

```json
{
  "mcpServers": {
    "gamestudio": {
      "command": "/path/to/gamestudio/.venv/bin/gamestudio",
      "args": ["mcp", "--no-worker"],
      "env": { "GAMESTUDIO_HOME": "/path/to/gamestudio" }
    }
  }
}
```

`GAMESTUDIO_HOME` points at the studio installation: the `.env` found there
supplies the rest — data folder, keys, Blender. Without it, the server would
look for a `.env` above the current directory, so above the game repository,
and would find neither the assets nor the key.

`--no-worker` is the right setting from a game repository: the agent reads and
queues, but the studio produces. Remove it to let it produce too.

### Storage you can inspect

**A project is a folder**: everything the studio keeps for a game lives in its
`.gamestudio/` — recipe, documents, context, database, store, library, report,
bundles — and nothing is ever mixed between projects. The studio itself never
copies its engine there (code, tools, skills, keys). The store addresses files
by hash (`.gamestudio/assets/`); the **library** (`.gamestudio/library/`) is its
readable mirror, in hard links. A `.gitignore` lets only the texts through. The
layout does not mix dimensions either:

- `generations/<date>_<prompt>_<batch>/` — each free generation has its folder,
  with its images and a `batch.json` (prompt, model, date);
- `2d/<entity>/` — `concept.png` and `concepts/<batch>/`, as long as the entity
  has no mesh;
- `3d/<entity>/` — `<entity>.glb` (bare, or rigged and animated by the agent),
  `<entity>-bare.glb` (the original mesh), `sprites/`, `concept.png`;
- `icons/<sheet>/` — a split sheet, one file per item (SVG or PNG icons, frames
  of a sprite sheet), plus a `sheet.json` (source, split strategy, frames);
- `effects/<effect>/` — the latest render of a visual effect;
- `renders/` and `briefing/<section>/` — the game's scenes drawn by Godot;
- `style/` — the art-direction references.

Resynchronised by the worker after each production (generations included), or by
hand: `gamestudio library sync <project>`.

**This tree is what you hand to another tool or agent**, never the store: a
library path can be read and stays stable, a hash does not.

```bash
gamestudio library ls my-game                          # the project's folders
gamestudio library ls my-game icons/my-pack            # the content of a sheet
gamestudio library ls my-game icons/my-pack --paths    # one absolute path per line
gamestudio library path my-game icons                  # the folder's raw path
```

An agent connected to the MCP server has the equivalent in one call:
`library_tree(project, folder, depth, pattern)` returns subfolders and files with
their **absolute path**, their asset and their size — and resynchronises on the
way, so the returned paths exist. Each sheet or batch folder also carries a JSON
(`sheet.json`, `batch.json`) saying where its content comes from: an agent thus
knows *what it opens*, not only where it is.

This layout is described once, by `Librarian.index_project`: the disk is its
writing, the **Library** page its reading. The page shows the project's files
filtered by medium (2D, 3D) and by kind (concepts, references, meshes, sprites,
free images, sheets…), with a search by name; a mesh can be viewed in 3D, and
each piece leads to the workbench step where it is reworked, or to the comparer.
Right-click offers "Copy the path", "Show in the folder" and "Delete"; a
piece's panel, "Rename or delete". Not everything can be changed, on purpose:

| | Rename, move elsewhere | Delete |
|---|---|---|
| Piece of a sheet (icon, frame) | yes | yes — database, store and mirror |
| Free image (`generations/`) | no | yes |
| What an entity or a style holds (concept, mesh, sprite, reference) | refused, naming what holds it | refused |

An agent renames and moves (`library_rename`, `library_move`); deleting remains
the user's gesture. Since the mirror is the exact writing of the index, an item
deleted, renamed or moved leaves nothing behind on disk.

### Splitting a multi-item sheet (SVG or PNG)

A game consumes its assets one by one; an icon pack or a sprite sheet arrives as
a single file. The Library therefore has an **"Import a sheet…"** button: the
file (SVG, PNG, JPEG, WebP), a strategy (`auto` by default), a gap (`gap`,
automatic by default) and local background removal. **Inspect** announces,
without writing anything, how many items will be extracted and under which
names; **Import** writes one standalone file per item under `icons/<sheet>/`. An
agent does the same with `inspect_sheet` then `import_sheet`, which also take
`rows` and `columns` (a tight grid), `keep` (keep only some items) and
`raster_size` (one PNG per SVG icon) — skill `sheet-split`.

**A single rule separates the items**, whatever the medium: *two pieces that
touch belong to the same drawing.* What remains is knowing from which gap two
pieces stop touching — and the sheet tells that gap itself: the empty spaces
inside a drawing and those between two drawings form two distinct populations,
separated by a clear jump. The threshold is therefore read from that
distribution (an Otsu cut on the gaps of the minimum spanning tree), not fixed in
advance. A constant would have been wrong at every change of scale.

It remains adjustable by hand (`gap`) when a sheet is out of the ordinary:
raising it glues a scattered drawing back together, lowering it separates two
drawings stuck together.

What changes from one medium to the other is only *how the sheet is read*:

| | SVG | PNG, JPEG, WebP |
|---|---|---|
| What is read | the document structure | the pixels |
| Split | `<symbol>`, one `<g>` per icon, or an optimiser's flat `<path>`s | connected components of the foreground |
| Background | not applicable | alpha, otherwise local background removal (BiRefNet) or the dominant colour of the edges |
| Output | standalone SVG: adjusted viewBox, flattened transforms, inherited styles copied, only the referenced definitions | cropped RGBA PNG |

Bitmap sheets also have two regimes, chosen automatically. A **frame grid** is
recognised by the regularity of its gutters: all its cells then receive **one
and the same frame**, the union of their contents — so they keep the same size,
without dragging the sheet's margins along. Cropping each frame to its own
content would make the character's pivot jump from one image to the next: that
is the trap `sprites.py` already avoids when assembling. A **loose pack** of
icons, on the other hand, is cropped item by item. If the cells touch, no gutter
gives them away: you then impose `rows` and `columns`.

One bitmap-specific trap is worth knowing: on a dark background, many sheets
carry a **halo** around each drawing — too close to the background to be a
drawing, too far to be removed with it. It then links neighbours and the sheet
becomes a single piece. The symptom is clear (one piece covers a large part of
the sheet), so the correction is automatic: the background tolerance widens
until the drawings come apart, and the split report says so.

Each file's name comes from the sheet when it carries one (`id`, `data-name`,
an SVG's `<title>`); tool identifiers (`path12`, `Layer_1`) are ignored in
favour of the position in the grid, like the `icon-01…` and `frame-01…` of a
bitmap sheet. No network call, no cost.

### An image from elsewhere

A drawing, a scan or an artist's concept is handled like a studio concept: it
comes in through `import_image` (optional local background removal), its pose is
measured with `detect_pose` (`rigtools` extra) before paying for anything, then
it becomes the chosen concept of a card and goes 3D like the others. The
procedure is the `external-image` skill.

---

## Architecture

The code map — one file per line, with what it does — is
[`context/codemap.md`](context/codemap.md), regenerated from each file's header
and checked by `make check`. It is the starting point for a change; the agents'
procedures are in `.claude/skills/`.

**Why a `service/` layer.** Without it, the CLI, the API and the MCP server
would each do the same work, with three chances to diverge. An operation is
defined once and rendered in its own language by each interface: an HTTP code
for the API, a readable message for an agent. It is also what makes the cost
guards truly uniform — a paid operation refuses everywhere, or nowhere.

**Why Tauri and not Electron.** The window uses the system's web engine
(WebKitGTK) instead of embedding a Chromium: the application weighs a few
megabytes instead of a hundred and fifty. And the Rust stays a shell — the day
the Python server changes, nothing to recompile.

**Why Blender as a backend and not as the interface.** A Blender add-on would
lock everything into its UI and make batch processing painful. Headless Blender
behind an API, on the other hand, is exactly the right use for what must be
exact and repeatable: sprite rendering, the inventory of a GLB. The open Blender
remains the rigging agent's, and yours.

**Why SQLite and not Postgres + Redis.** The studio runs on a workstation, one
worker is enough, and WAL mode handles one producer and a few readers very well.
One less dependency to run.

---

## Verification

```bash
make check                  # ruff + pytest + skills + code map + front-end types and translations
python scripts/smoke_3d.py  # simulated agent delivery, requires Blender; Godot validates if present
```

Neither one calls the network. `make check` fails if the front-end
dependencies are missing (`make install-app`).

The smoke test stands in for the agent: Blender builds a rigged character (an
`idle_loop` cycle, a `wave` gesture) and a boneless machine whose wheel turns.
The studio takes stock of bones and animations, renders one sheet per animation
and per direction under a single framing, writes the Godot resources, then
**runs Godot headless to validate them**: every animation played, every track
resolved, the cycles looping and only them. It is the only way to guarantee that
a file written outside the editor really loads.

The same checks run in CI on every push and every pull request (see
[`docs/ci.md`](docs/ci.md)) — without an API key and with no possible
expense.

---

## Known limitations

- **Linux only.** The shell and the update rely on Linux-specific calls; there
  is no package, the studio runs from its repository.
- **The rig is as good as the agent that makes it.** A critic must look at it
  (sprite sheets, captures) before it is approved: a rig that loads is not a rig
  that deforms well.
- **Generated meshes have irregular topology.** Acceptable for prototypes and
  for rendering to sprites; to be retopologised for demanding real-time 3D
  (Tripo P2 called directly outputs native quads).
- **3D reads a single view.** The `multiview` and `turnaround` prompts produce the
  other angles, but the mesh is generated from the chosen concept only.
- **A split into parts comes out untextured** when it comes from Tripo
  (`generateParts`, incompatible with texturing): a building to animate by parts
  is better split in Blender, by the agent.

---

## Indicative costs

| Step | Order of magnitude |
|---|---|
| Reference image (FLUX.1 [dev], 768×1152) | ~$0.006 |
| Style exploration (8 images, FLUX.1 schnell) | ~$0.01 |
| LoRA training (1000 steps, FLUX.1 [dev]) | ~$1.45 |
| 3D mesh through Runware (TRELLIS.2, Hunyuan 3D 3.1, Tripo v3.1 by default at $0.40) | $0.15 to $0.50 |
| 3D mesh through the direct Tripo API (H3.1, P1, P2) | $0.30 to $1.25 depending on model and texture |
| Rig and animations (an agent, in Blender) | no generation cost — the agent's quota |
| Locally forged mesh, sprites, inventory, export | $0 |

The exact price of each model is in `gamestudio mesh providers` and in the
dialog that confirms each expense. A roster of twenty 3D entities with the
default model costs about $8, plus training the style.

---

## Licence

The code is public without being open source in the OSI sense: the studio is
under **PolyForm Shield 1.0.0** (`LICENSE`).

- **Allowed**: using it, studying it, modifying it and redistributing it,
  including to produce the assets of a game that is sold.
- **Forbidden**: making a product out of it that competes with the studio
  (software, service, plugin), whether free or paid.
- **Third-party material** keeps its own licence (`THIRD_PARTY_NOTICES.md`).
  img2threejs is under Apache 2.0, the scripts run inside Blender under GPL 3.0
  or later, the Outfit and Prompt fonts under OFL 1.1, the agent logos (paths
  from LobeHub lobe-icons) under MIT — the trademarks remain their owners'.
- **Models downloaded at runtime** are not in the repository. The optional local
  tools fetch on first use BiRefNet (background removal, through rembg: MIT) and
  RTMPose with YOLOX (pose, through rtmlib: Apache 2.0).
- **What the studio produces**: generated images and meshes fall under the
  terms of Runware, Tripo and the models used, not under this licence. In
  particular, a LoRA trained by `style train` derives from FLUX.1 [dev] and falls
  under its non-commercial licence (FLUX.1 [dev] Non-Commercial License): read
  it before any commercial use of the LoRA.

For a use the licence does not allow, ask the author for a licence.
Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md). Reporting a
vulnerability: [`SECURITY.md`](SECURITY.md).
