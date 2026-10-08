# Getting started

This guide goes from a freshly cloned repository to a first rigged and animated
entity, through what saves time day to day: the context, projects, documents,
the inbox. It does not replace the [`README.md`](../README.md) (how it works,
requirements) nor `CLAUDE.md` (the agents' guide): it says **in what order to
use the studio**.

## 1. Install, then ask where things stand

```bash
git clone <the repository> && cd gamestudio
make install                 # venv + dependencies
make install-app             # front end (Node)
cp .env.example .env         # then set RUNWARE_API_KEY
make install-rigtools        # optional: local pose and background removal
make link                    # `gamestudio` in ~/.local/bin
```

The studio finds its root by walking up to the first `.env`. To run it from a
game folder, which has none, export the studio root once and for all (in
`~/.bashrc` or `~/.zshrc`):

```bash
export GAMESTUDIO_HOME=/path/to/gamestudio
```

Then, before anything else:

```bash
gamestudio doctor
```

Each line says **what is missing and what to do**, in order of priority. Three
states only: `ok`, `warning` (the studio runs, a capability is missing — 3D
without Blender, generation without a key), `failed` (the studio cannot work:
no project root, data not writable). For a script or an agent:

```bash
gamestudio doctor --check    # exits with an error on a failure
gamestudio doctor --json     # the raw report
```

## 2. Declare a project

A project is a folder on the machine — the game folder, root of the Godot
project. In the window: workspace selector, "Open a folder…". On the command
line, it starts with a recipe, the declarative document that sets the style and
the roster (fields: [`recipe.md`](recipe.md)):

```bash
cd ~/games/my-game
gamestudio init my-game           # creates .gamestudio/recipe.yaml
$EDITOR .gamestudio/recipe.yaml
```

Everything the studio produces or writes for this game goes into
`.gamestudio/`; the engine (code, tools, skills, key) stays in the studio.

Then settle the art direction **before** producing:

```bash
gamestudio style explore .gamestudio/recipe.yaml -s "a villager, front view" --confirm   # ~$0.01
```

Look at the images (`view_asset` for an agent, the Library page in the window),
keep about ten, train the LoRA (`gamestudio style train`), copy `lora_air` and
`trigger_word` into the recipe. Changing style after the roster means redoing
everything.

## 3. Watch the studio, not only the queue

Open the window: `make studio`. Three pages serve as a dashboard:

- **Control room** — what needs doing now (failures, a roster to build, outputs
  to review, missing tools), then the active project — its entities and their
  progress, its job log, its recipe and what it cost — then this machine:
  diagnostics, MCP connections and procedures, with their index and mirror. The
  project is chosen in the workspace selector at the top of the rail; a project
  declared by a recipe shows its roster **before** anything is produced.
- **Context** — what every agent reads on arrival. The briefing is recomputed
  after each production and whenever a chat tab opens; the three personal
  notes (`identity.md`, `goals.md`, `preferences.md`) are written by hand, and
  they are how the next conversation knows where you are.
- **Documents** — the texts you write for a project: character bible, world
  notes, art direction. Versioned with the recipe, in `.gamestudio/documents/`.

## 4. Produce a first entity

Nothing is generated without context: an entity starts from a **world card**.
In the window, "+" under *The world* declares a section (Characters,
Buildings…); the card says what the entity is, what it looks like, its role in
the game and the animations it needs. Its **workbench** then chains:

1. **Concepts** — batches drawn from the card (~$0.006 per image), looked at,
   then one concept chosen. This is the studio's only 2D.
2. **3D** — the mesh, paid and confirmed ($0.15 to $1.25 depending on the
   model: Runware, or the direct Tripo API), or a locally forged `.glb`. The
   studio starts from an image in **A-pose** on a transparent background: limbs
   clear of the body make a mesh that can be rigged. The mesh comes out
   **bare**.
3. **Animate** — "Hand to an agent" writes a brief and sends it to the agent
   of your choice, which rigs and animates the entity in Blender (a skeleton for
   a creature, parts for a mill or a factory) and returns a GLB carrying all
   its animations. Watch it work in *Team › At work*.
4. **Sprites, export** — one sheet per animation and per direction, free; the
   GLB is copied into the Godot project when the folder has a `project.godot`.
   The studio never creates one in a game folder.

A whole roster can also be built from the recipe (reference and bare mesh,
~$0.40 per entity). Like every expense, the build needs explicit confirmation:

```bash
gamestudio build .gamestudio/recipe.yaml -c villager --confirm
```

An entity built this way has no card: Control room › "Give it a card"
attaches it to a world section, and its workbench then hands it to an agent
("Animate"). The same handoff exists on the command line, once the card has
gone 3D: `gamestudio world brief <project> <section> <card>`.

The phrasing that makes an image usable lives in the studio, not in a notes
file:

```bash
gamestudio prompts ls
gamestudio prompts show apose --subject "an old villager"
```

The prompts cover the reference (`apose`, `tpose`, `profile`), the accessories
sheet to split (`accessories`), the angles (`multiview`, `turnaround`) and
icons (`icon`, `icon-set`). The full procedure is the `character-sheet` skill.

**Without spending anything**: if you have an image (your own, a scanned
sketch), `import_image` brings it in as is — it can become a card's chosen
concept (skill `external-image`) — and `render_sprites` makes sprites from an
existing mesh. The `local-3d` skill goes further: forging a 3D mesh on the
machine, for free, with `img2threejs`.

## 5. Where things land

| What you are looking for | Where it is |
| --- | --- |
| Everything the studio keeps for the game | `<folder>/.gamestudio/` |
| Produced files, organised and readable | `.gamestudio/library/` |
| The project's state and its report | `.gamestudio/workspace/` |
| What you wrote about the project | `.gamestudio/documents/` |
| What agents read on arrival | `context/` |
| What you dropped for an agent | `inbox/attachments/` |
| The procedures | `.claude/skills/` (index: `.claude/skills/README.md`) |
| The content-addressed store | `.gamestudio/assets/` |

To give a file to another agent, always cite the **library**
(`<folder>/.gamestudio/library/…`), never the store: the first path can be
read, the second cannot.

## 6. Working with an agent

The MCP server is declared in `.mcp.json`: an agent started in the studio has
the studio's tools directly. From another repository:

```json
{
  "mcpServers": {
    "gamestudio": {
      "command": "/absolute/path/to/.venv/bin/gamestudio",
      "args": ["mcp"],
      "env": { "GAMESTUDIO_HOME": "/absolute/path/to/the/studio" }
    }
  }
}
```

`GAMESTUDIO_HOME` is enough to find the data and the API key. The Chats window
opens a tab on the agent of your choice (Claude Code, Codex, Kimi Code), with an
effort level when the agent offers one; paste a screenshot or drop a file into
it, and **its path** is written into the conversation. The DeepSeek and MiMo
tabs run `claude-deepseek` and `claude-mimo`, wrappers the repository does not
provide: a script of your own, on the `PATH`, that sets the endpoint and the
token then runs `claude`.

## 7. Check without spending

```bash
make check                   # ruff + pytest + skills + code map + types + translations
gamestudio skills check      # the procedures' index and mirror, alone
python scripts/smoke_3d.py   # simulated agent delivery → sprites → headless Godot
```

None of these commands calls the network. A `.tscn` or a `.glb` counts as
loadable only once headless Godot has validated it: without Godot, the smoke
test writes the files and says so.

## Three rules that save time

1. **An operation that returns has proved nothing.** Look at the image,
   validate the scene. A `done` job says the API answered.
2. **A failing criterion goes back to the user** instead of paying in a loop.
   The budget of attempts is limited, and a repeated failure is a decision, not
   a reason to try again.
3. **What is decided is written down** — in `context/` if it holds for the
   studio, in `.gamestudio/documents/` otherwise. What is computed is redone.
