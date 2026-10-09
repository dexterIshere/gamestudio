"""Handoff: entrust a piece of work to an agent, which carries it through to the end.

Three jobs are handed off this way, because no deterministic code does them
well: building a VFX concept in Godot; rigging then animating the mesh of a
world entity -- a character, a creature, a building whose parts move; and
bringing a studio section back in line with what the game already contains
(`shelf_brief`), when the project moved on without it. The studio makes none
of the three: it hands them to an agent (Claude Code, Codex, Kimi...), in a new
discussion or in one already open in the Chats window, and the agent builds
them.

A studio procedure (a skill) is handed off the same way (`skill_brief`): the
user says what they want, an agent writes it -- in the studio, not in a
project. So is a creation in a section (`create_brief`): the user's request,
which an agent turns into cards.

Some handoffs are discussions rather than jobs to finish alone: on a card
(`card_brief`), a showcase element (`showcase_brief`) or a lookdev shader
(`lookdev_brief`), where the user keeps moving forward on the subject. The
brief gives the agent the subject, its images and the game; the agent says
where the subject stands, then waits for the request.

What goes out is not a long message pasted into a terminal: it is a **brief**
written to disk (`.gamestudio/workspace/briefs/`), standalone -- the source
copied as it was when sent, the target, the procedure and the completion
criterion -- and a single line pointing to it. An agent opened in a project's
folder does not see the studio's skills: the brief cites them by absolute path.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..store.folders import ProjectPaths, project_paths
from ..terminal.harnesses import DEFAULT
from ..terminal.session import manager
from . import documents, world
from .context import space, studio
from .errors import NotFound, ServiceError

# The concepts' shelf, and where their builds go in Godot.
SHELF = "design/vfx"
TARGET = "vfx"

# The grid of a handed-off tab, until a window measures it.
WATCH_COLS = 120
WATCH_ROWS = 36


def _home() -> Path:
    return Path(studio().settings.project_root or Path.cwd())


def _skill(name: str) -> Path:
    """A studio skill, by absolute path: an agent opened in a game's folder does
    not see the studio's `.claude/skills/`."""
    return _home() / ".claude" / "skills" / name / "SKILL.md"


def _write(project: str, filename: str, text: str) -> Path:
    folder = project_paths(studio().settings, project).workspace / "briefs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_text(text, encoding="utf-8")
    return path


def _dispatch(project: str, prompt: str, title: str, *, harness: str, effort: str,
              session: str, loop: Any, message: str = "",
              cwd: Path | None = None) -> dict[str, Any]:
    """Type the line pointing to the brief into `session`, or into a new tab.

    `message` is what the user wrote in the handoff dialog: it follows the
    line, so the agent reads the brief with the user's request in hand.
    A new discussion opens in the project's folder (the studio root for a
    project without a folder), like those of the Chats window -- or in `cwd`,
    for work that belongs to no project.
    """
    if message.strip():
        prompt = f"{prompt}\n\nThe user's message: {message.strip()}"
    if session:
        return manager.type_in(session, prompt)
    root = cwd or project_paths(studio().settings, project).root
    # Nobody measures the tab yet: it starts at a comfortable grid, which the
    # Chats window adjusts when it opens it -- the agents-at-work page follows
    # it without changing it.
    return manager.create(loop=loop, harness=harness, effort=effort, cwd=str(root),
                          title=title, first_message=prompt, cols=WATCH_COLS,
                          rows=WATCH_ROWS).describe()


# ------------------------------------------------------------------------ VFX


def brief(project: str, name: str) -> dict[str, Any]:
    """Write a concept's brief and return it: path, text, and the line to send."""
    concept = documents.read_document(project, name, SHELF)
    paths = project_paths(studio().settings, project)
    godot = paths.godot
    target = f"res://{TARGET}/{concept['name']}"
    scene = f"{target}/{concept['name']}.tscn"
    home = _home()
    skills = home / ".claude" / "skills"
    gamestudio = home / ".venv" / "bin" / "gamestudio"
    ready = (godot / "project.godot").is_file()
    missing = "" if ready else ("  — **no `project.godot` here: create nothing, "
                                "report it and stop.**")

    text = f"""# Brief — build the VFX “{concept['title']}” in Godot

Project: `{project}` · sent {datetime.now().strftime('%Y-%m-%d %H:%M')}

## The mission

Build in the Godot project the effect described by the concept below, as
written: intent, sequence, material, palette, constraints. The concept is
authoritative; where it is silent, choose what serves the intent and say so in
the report.

## Where

- Godot project: `{godot}`{missing}
- Deliverable: `{scene}` — a standalone scene, instantiable as is.
- Everything the effect uses (shaders, textures, materials, scripts) goes in
  `{target}/`, and nowhere else in the project.
- Source concept: `{concept['path']}`

## How

1. Read the whole concept, and look at every image reference it cites.
2. Choose the technique (`GPUParticles2D/3D`, shader, flipbook, a combination)
   from the concept's Godot constraints. The methods per material (gas,
   liquid, plasma, magic, light…) are in `{skills / 'vfx' / 'SKILL.md'}`.
3. Build in Godot: the `godot` MCP server if it is connected, otherwise by
   writing the `.tscn` / `.gdshader` / `.tres` files directly.
4. Validate headless — a scene that does not load is not delivered:
   `{gamestudio} validate-godot {godot} --scene {scene}`
5. Watch the effect play and prove it with captures, following
   `{skills / 'visual-review' / 'SKILL.md'}`.
6. Add to the concept (`{concept['path']}`) an `## Implementation` section:
   date, files created, chosen technique, departures from the concept, capture.

## Rules

- No paid operation.
- A criterion that fails twice goes back to the user, instead of looping.
- Do not touch the project's other scenes.

## The concept (copy at sending time)

{concept['text']}
"""
    path = _write(project, f"vfx-{concept['name']}.md", text)
    prompt = (f"Build the VFX “{concept['title']}” of project {project} in Godot: "
              f"read the brief {path} and follow it to the end.")
    return {"project": project, "name": concept["name"], "title": concept["title"],
            "path": str(path), "text": text, "prompt": prompt, "scene": scene,
            "godot": str(godot), "godot_ready": ready}


def send(project: str, name: str, *, harness: str = DEFAULT, effort: str = "",
         session: str = "", message: str = "", loop: Any = None) -> dict[str, Any]:
    """Hand the concept to an agent: a new discussion, or `session`."""
    sent = brief(project, name)
    if not sent["godot_ready"]:
        raise ServiceError(f"no Godot project in {sent['godot']}: open the game folder as a "
                           "studio project, or create project.godot in it")
    tab = _dispatch(project, sent["prompt"], f"VFX · {sent['title']}", harness=harness,
                    effort=effort, session=session, message=message, loop=loop)
    return {**{key: value for key, value in sent.items() if key != "text"}, "session": tab}


# ------------------------------------------------------- rig and animation


def _in_library(library: Path, relative: str) -> str:
    """The library path if it exists: that is the path an agent is given."""
    path = library / relative
    return str(path) if path.exists() else "—"


def animation_brief(project: str, section: str, name: str) -> dict[str, Any]:
    """Write the brief handing a card's rig and animations to an agent.

    The agent receives the entity's mesh, rigs and animates it in Blender, and
    returns it to the studio; so the card must already be in 3D.
    """
    from . import entities

    bench = entities.workbench(project, section, name)
    rubric = world.section(project, section)
    card = documents.read_document(project, name, rubric["folder"])
    entity = bench["entity"]
    st = space(project)
    character = st.db.get_character(project, entity)
    rig3d = character.rig3d if character is not None else None
    if rig3d is None or not rig3d.mesh_asset_id:
        raise ServiceError("no mesh to rig: move the card to 3D first (the workbench's 3D step, "
                           "or entity_attach_mesh)")

    paths = st.paths
    godot = paths.godot
    godot_ready = (godot / "project.godot").is_file()
    library = st.librarian.project_dir(project)
    home = _home()
    skills = home / ".claude" / "skills"
    gamestudio = home / ".venv" / "bin" / "gamestudio"
    skeleton = home / "src" / "gamestudio" / "domain" / "skeleton.py"
    work = paths.workspace / "anim" / entity
    when = datetime.now().strftime('%Y-%m-%d %H:%M')
    kind = rubric["label"]

    mesh = _in_library(library, f"3d/{entity}/{entity}.glb")
    nude = bool(rig3d.source_mesh_asset_id) \
        and rig3d.source_mesh_asset_id != rig3d.mesh_asset_id
    origin = _in_library(library, f"3d/{entity}/{entity}-bare.glb") if nude else mesh
    if rig3d.bones or rig3d.animations:
        # A rework starts from the bare mesh; a mesh that arrived already rigged
        # has none, and saying so avoids looking for a file that does not exist.
        done_before = (f"already {len(rig3d.bones)} bones and {len(rig3d.animations)} "
                       f"animation(s) ({', '.join(rig3d.animations) or '—'}) — a rework: "
                       + ("start again from the bare mesh, never from the previous rig"
                          if nude else
                          "no bare mesh is known, start from this one as it is"))
    else:
        done_before = "no rig: the mesh is bare"
    exported = (f"`res://characters/{entity}/{entity}.glb` (copied automatically on delivery)"
                if godot_ready else "no `project.godot` at the folder's root: the mesh is "
                                    "not copied there — do not create one")
    source_id = rig3d.source_mesh_asset_id or rig3d.mesh_asset_id
    where = f"""- Mesh to rig: `{origin}` (asset `{source_id}`)
- Current state: {done_before}
- Chosen concept: asset `{bench['concept'] or '—'}` — look at it with `view_asset`
- Working folder (`.blend`, scripts): `{work}`
- Delivery: the rigged and animated GLB, returned to the studio (step 7); in
  Godot, {exported}"""
    how = f"""1. Read the whole card. Look at the chosen concept and at the mesh from at
   least two angles: `render_sprites(mesh_asset_id, directions=4)` renders a
   free turn around it, `view_asset` shows it.
2. Choose the structure from what the entity is:
   - a being (character, creature): a skeleton. If the body suits it (biped,
     quadruped, winged biped), name the bones after the studio convention
     (`{skeleton}`): the same names in Blender, in Godot and in the studio's
     readings;
   - a machine, a building: parts. A single-piece mesh is segmented in
     Blender (by islands, by materials, or by hand); if a clean segmentation
     is impossible, tell the user — a generation in parts (Tripo
     `generateParts`) is paid, and comes out untextured;
   - both when needed (a mill and its miller are two things).
3. Work in Blender: the `blender` MCP server if a session is open (the user
   sees the work), otherwise headless Blender
   (`blender --background --python <script>`). Keep the `.blend` and the
   scripts in the working folder.
4. Rig: armature and weights, or parented parts. Test the deformations at
   extreme poses (shoulders, elbows, knees, neck) before animating.
5. Animate every animation the card asks for (how the entity is used in the
   game, its expected animations); a silent card gets the minimum for its
   role (a being: `idle`, `walk`; a machine: its activity cycle, and `build`
   if it is built in game). One action per animation, named in English
   snake_case. A cycle loops exactly (first and last pose identical) and its
   name ends with `_loop` (`walk_loop`): Godot loops it on import and calls it
   `walk`, like the sprite sheets. A planted foot does not slide, a strike has
   its anticipation. **Never animation by diffusion**: keys set by hand, or
   written procedural motion.
6. Export the GLB: one NLA track per action, named after it
   (`export_animation_mode="NLA_TRACKS"`), each animation brought back to 0 s
   (`export_anim_slide_to_zero=True`: otherwise Godot holds the first pose one
   frame too long where a cycle joins), Y up.
7. Return it to the studio: `entity_attach_mesh(project="{project}", section="{section}",
   name="{name}", path=<glb>, replace=true)` — or
   `{gamestudio} mesh import <glb> --project {project} --name {entity} --replace`.
   The studio reads the bones and animations, keeps the original bare mesh,
   and copies the GLB into Godot when the folder has a `project.godot`.
8. Prove it — handing back proves nothing:
   - `render_sprites(mesh_asset_id, max_frames=8)`: one sheet per animation
     and per direction; look at all of them (`view_asset`). Each animation
     reads by eye, nothing passes through, nothing tears;
   - if the GLB is in Godot: `{gamestudio} validate-godot {godot} --scene
     res://characters/{entity}/{entity}.glb` — every track resolved."""

    text = f"""# Brief — rig and animate “{bench['title']}”

Project: `{project}` · section {kind} · entity `{entity}` · sent {when}

## The mission

Give this entity what it needs to live in the game: its rig and its
animations, after its card. The card is authoritative; where it is silent,
choose what serves its role and say so in the report. The full procedure is
the skill `{skills / 'animation' / 'SKILL.md'}`.

## Where

- Card: `{card['path']}`
{where}

## How

{how}

## The report

- Add to the card (`{card['path']}`) a `## Rig and animations` section: date;
  structure (bones or parts, and why); each animation (name, frames, fps,
  loop); its events (the frame of a strike, a footfall, an impact: that is
  where effects and gameplay will hook in); departures from the card; the
  paths of the proofs.
- Open a devlog entry asking for the user's validation (the "animated model"
  gate): `create_document(project="{project}",
  title="Rig and animations — {bench['title']}", template="devlog",
  folder="devlog")`, then write it — the sheets to look at, what was checked,
  what remains doubtful.

## Rules

- No paid operation without the user's explicit consent in the discussion.
- A criterion that fails twice goes back to the user, instead of looping.
- Touch no other entity, and no other scene of the project.

## The card (copy at sending time)

{card['text']}
"""
    path = _write(project, f"anim-{entity}.md", text)
    prompt = (f"Rig and animate “{bench['title']}” ({kind}) of project {project}: "
              f"read the brief {path} and follow it to the end.")
    return {"project": project, "section": section, "name": name, "entity": entity,
            "title": bench["title"], "path": str(path),
            "text": text, "prompt": prompt, "godot": str(godot),
            "godot_ready": godot_ready}


def send_animation(project: str, section: str, name: str, *, harness: str = DEFAULT,
                   effort: str = "", session: str = "",
                   message: str = "", loop: Any = None) -> dict[str, Any]:
    """Hand a card's rig and animations to an agent: a new tab, or `session`."""
    sent = animation_brief(project, section, name)
    tab = _dispatch(project, sent["prompt"], f"Anim · {sent['title']}", harness=harness,
                    effort=effort, session=session, message=message, loop=loop)
    return {**{key: value for key, value in sent.items() if key != "text"}, "session": tab}


# --------------------------------------------------- a section and the game

# The shelves caught up on the game: what goes in them, and with which
# template. Notes, ideas and the devlog are not among them -- they are the
# user's writing, not a survey. World sections join them, declared by the user.
CATCHUP: dict[str, dict[str, str]] = {
    "design/interface": {
        "label": "Interface", "kind": "interface", "template": "interface",
        "unit": "screen, HUD panel or pop-up window",
        "where": "the scenes with an interface root (Control, containers, CanvasLayer) "
                 "and their scripts; what opens what (`change_scene`, signals, "
                 "`visible`); themes, fonts, inputs. An interface built in code is read "
                 "in its scripts. Icons and props (buttons, arrows, sliders, frames) "
                 "have their own section: a screen card cites those it uses, without "
                 "describing them.",
    },
    "design/icons": {
        "label": "Icons", "kind": "icons", "template": "icon",
        "unit": "icon set (a consistent family: resources, actions, statuses, "
                "navigation…)",
        "where": "the icon folders and what cites them (scenes, scripts, themes, "
                 "AtlasTexture); their format and source size against their on-screen "
                 "size; their stroke, palette, states. An icon nothing cites may be "
                 "loaded through a composed path: search for its name in the scripts "
                 "before calling it unused.",
    },
    "design/props": {
        "label": "Props", "kind": "props", "template": "prop",
        "unit": "interface prop (a button, an arrow, a slider, a checkbox, a tab, a "
                "frame, a gauge — and its variants)",
        "where": "the component scenes the screens instantiate, the scripts that extend "
                 "a button, the StyleBoxes (themes and `.tres`), the textures of "
                 "buttons, arrows and frames (and their 9-slice margins). A prop is "
                 "described by its states (normal, hover, pressed, disabled): read them "
                 "in the theme or the scene, do not assume them.",
    },
    "design/mechanics": {
        "label": "Mechanics", "kind": "mechanics", "template": "mechanic",
        "unit": "game system (what the player does, and its rules)",
        "where": "the autoloads (the global services), the scripts by folder, the code "
                 "outside Godot (a server, a simulation), the balancing data. A rule is "
                 "read in the code that applies it, not in a comment.",
    },
    "design/direction": {
        "label": "Art direction", "kind": "direction", "template": "direction",
        "unit": "art direction subject (intent, palette, typography, lighting, shaders, "
                "characters, buildings, places and levels, signature)",
        "where": "the intent first, with the `mood` template (style, feeling at launch, "
                 "in play, at victory, at defeat). Then what the game already uses: the "
                 "home screen and the screens of key moments (rendered by the engine), "
                 "the colors of themes, scenes and scripts (the meaning each one "
                 "carries), each shader (what it draws, its settings, who uses it), the "
                 "environment and lights, the levels, music and sounds, models and "
                 "images. The intended feeling is not in the code: write what the game "
                 "gives off today under an “Observed” bullet, and ask the question.",
    },
    "design/vfx": {
        "label": "VFX", "kind": "vfx", "template": "vfx",
        "unit": "effect the game already plays (its “Implementation” section filled in "
                "from its files)",
        "where": "the particle scenes, the shaders, the effect textures. An effect is "
                 "described by what triggers it (the script) and what it shows (the "
                 "scene).",
    },
}
# What a user may call a section, in English or in French.
ALIASES = {
    "interface": "design/interface", "ui": "design/interface", "hud": "design/interface",
    "menu": "design/interface", "menus": "design/interface", "screen": "design/interface",
    "screens": "design/interface", "ecran": "design/interface", "ecrans": "design/interface",
    "icons": "design/icons", "icon": "design/icons", "pictograms": "design/icons",
    "glyphs": "design/icons", "icones": "design/icons", "icone": "design/icons",
    "pictos": "design/icons", "pictogrammes": "design/icons", "glyphes": "design/icons",
    "props": "design/props", "prop": "design/props", "buttons": "design/props",
    "button": "design/props", "components": "design/props", "widgets": "design/props",
    "arrows": "design/props", "controls": "design/props", "boutons": "design/props",
    "bouton": "design/props", "composants": "design/props", "fleches": "design/props",
    "controles": "design/props",
    "mechanics": "design/mechanics", "mechanic": "design/mechanics",
    "gameplay": "design/mechanics", "rules": "design/mechanics",
    "systems": "design/mechanics", "mecaniques": "design/mechanics",
    "mecanique": "design/mechanics", "regles": "design/mechanics",
    "systemes": "design/mechanics",
    "direction": "design/direction", "art-direction": "design/direction",
    "direction-artistique": "design/direction", "da": "design/direction",
    "vfx": "design/vfx", "fx": "design/vfx", "effects": "design/vfx",
    "visual-effects": "design/vfx", "effets": "design/vfx", "effets-visuels": "design/vfx",
}
WRITTEN = ("notes", "ideas", "devlog")
WORLD_UNIT = "game entity (card: what it is, what it looks like, its role in the game)"
WORLD_WHERE = ("the object and entity scenes, the data that describes them, their models "
               "and their images. Generate nothing: a card's workbench (concepts, 3D) "
               "stays with the user.")

# The rules for writing a card live once, in the `game-survey` skill
# ("Writing a card"); a brief recalls them in one line.
WRITING = ("one sentence first, without a heading; one section per question; short "
           "bullets (**label** — a few words); no repetition, no empty section; no "
           "image in the text; “Game survey — <date>” at the end")


def _writing(skill: Path) -> str:
    """The reminder of the writing rules, pointing to the skill where they live."""
    return f"The writing rules are in `{skill}` (“Writing a card”): {WRITING}."


def catchup_target(project: str, shelf: str) -> dict[str, str]:
    """The section designated by its folder, its name or what the user calls it ("UI")."""
    wanted = documents.slug(shelf.removeprefix("design/"))
    if wanted in WRITTEN:
        raise ServiceError(f"“{shelf}” is the user's writing, not a survey of the game: nothing "
                           "to catch up on")
    folder = ALIASES.get(wanted)
    if folder:
        return {"folder": folder, **CATCHUP[folder]}
    sections = world.sections(project)
    for section in sections:
        names = {section["id"], documents.slug(section["label"]),
                 documents.slug(section["folder"])}
        if wanted in names:
            return {"folder": section["folder"], "label": section["label"],
                    "kind": "world", "template": "card", "unit": WORLD_UNIT,
                    "where": WORLD_WHERE}
    known = [entry["label"] for entry in CATCHUP.values()]
    known += [section["label"] for section in sections]
    raise ServiceError(f"unknown section: “{shelf}” (known: {', '.join(known)})")


def shelf_brief(project: str, shelf: str) -> dict[str, Any]:
    """Write the brief that catches a section up with what the game already contains.

    A project starts before the studio, or moves on in its code without its
    sections following: "update the interface section" means bringing it back
    in line with the game. The brief carries the map of the game folder
    (`survey.game_map`), what the section already says, and the procedure.
    """
    from . import survey

    target = catchup_target(project, shelf)
    paths = project_paths(studio().settings, project)
    if not paths.linked:
        raise ServiceError(f"project {project} has no game folder: nothing to survey (open the "
                           "game folder as a project: open_project_folder)")
    game = survey.game_map(paths.root)
    present = documents.documents(project, target["folder"])
    shelf_dir = documents.directory(project, target["folder"])
    template = documents.TEMPLATES[target["template"]]["label"]
    skill = _skill("game-survey")
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    day = when.split(" ")[0]
    label = target["label"]

    listed = "\n".join(f"  - `{entry['file']}` — “{entry['title']}”"
                       for entry in sorted(present, key=lambda e: e["name"]))
    there = (f"{len(present)} card(s) today:\n{listed}" if present
             else "empty for now")
    text = f"""# Brief — update the “{label}” section from the game

Project: `{project}` · section `{target['folder']}` · written {when}

## The mission

Bring the section back in line with what the game already contains: the
project moved on without it — started before the studio, or worked on directly
in its code. Each {target['unit']} the game shows has its card; what the user
wrote and the game does not do yet stays, marked as planned. The game is read;
it is not modified.

## Where

- The game: `{paths.root}` — read-only.
- The section: `{shelf_dir}` — {there}
- A card is written with the “{template}” template: `create_document(project="{project}",
  title=<title>, template="{target['template']}", folder="{target['folder']}")`, then
  `write_document(project="{project}", name=<name>, text=<text>,
  folder="{target['folder']}")` — or directly in the section's folder.
- The procedure, in detail: `{skill}`.

## How

1. Read each card of the section: it is the user's intent, it must not be
   lost.
2. Read in place the game docs listed in the map: they are authoritative for
   what they cover. Cite them by path, do not copy them.
3. Survey what the game really does, starting from the map: {target['where']}
   The code is authoritative for what is done: read the scene **and** its
   script. Show what can be seen: each screen, entity or effect gets its image,
   drawn off-screen by the engine — `render_scene(project="{project}",
   scene=<scene>, name=<card name>, folder="{target['folder']}")`
   (`crop=true` for a panel or a bar, `setup` to open a page, `locale` for the
   language). `name` is the card's name: the image is a brief image, filed with
   its cards (`briefing/{target['folder']}/`), where the library groups them,
   and the card's page shows it next to the text by itself — nothing to paste.
   Look at it (`view_asset`). The render comes before any screenshot: a
   screenshot is only for a state no setup reaches.
4. Compare, element by element:
   - done and described: check that the card tells the truth, and complete it;
   - done but not described: a new card;
   - described but not done: keep it, writing “planned — not in the game yet”;
   - contradiction: do not decide alone. Write both versions and the question
     in the card, and raise it with the user.
5. Write one card per element, with the template. {_writing(skill)}
   Each one ends with `## Game survey — {day}`: **Files** (paths from the game
   root), **Game docs**, **State** (done, partial, planned), **Gaps**. Invent
   nothing: what the code does not say is written “to be specified”. A screen
   waiting for its server comes out without its data: say so in one bullet of
   the survey.
6. The report, a devlog entry: `create_document(project="{project}",
   title="Survey — {label}", template="devlog", folder="devlog")`, then write
   it — cards created, cards changed, contradictions to settle, what was left
   out and why.

## Rules

- The game is read-only: none of its files is modified, no command runs in it
  (no build, no tests, no editor) — except `render_scene`, which has the
  engine draw a scene off-screen without writing anything there.
- No paid operation.
- Touch only this section, and the devlog.
- Development scenes (`dev/`, `test/`…) are left out, unless the player sees
  them.
- An element that cannot be classified is raised, not guessed.

## How to tell it is done

- Each element of the map that belongs to the section has its card, or is in
  the devlog as left out, with its reason.
- Each card of a visible element has its render, under its name, looked at —
  or its survey says why it has none.
- No sentence of the user has disappeared.
- The devlog entry is written.

## The game map (surveyed {when}, without running anything)

{survey.outline(game, target['kind'])}
"""
    slug = documents.slug(target["folder"].removeprefix("design/"))
    path = _write(project, f"section-{slug}.md", text)
    prompt = (f"Update the “{label}” section of project {project} from what the game "
              f"already contains: read the brief {path} and follow it to the end.")
    return {"project": project, "folder": target["folder"], "label": label,
            "kind": target["kind"], "path": str(path), "prompt": prompt, "text": text,
            "game": str(paths.root), "documents": [entry["name"] for entry in present]}


# ----------------------------------------------------- a card, in discussion

# Where an image generated for a card comes from, as the brief says it.
GENERATED_FROM = {"": "text only", "sketch": "from the sketch",
                  "render": "from the render"}
# Beyond this, the list no longer reads: `card_media` returns them all.
MAX_GENERATIONS = 6
# A prompt is recognized by its start.
PROMPT_CHARS = 90


def _moment(value: Any) -> str:
    """A studio ISO date, in local time: `2026-10-05 14:02`."""
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value or "—")


def _generated(entry: dict[str, Any]) -> str:
    """An image generated for the card, as one bullet: where to see it, where it comes from."""
    where = (f"`{entry['path']}`" if entry.get("path") else
             f"asset `{entry.get('asset_id', '')}` (not in the library yet: "
             "`view_asset`)")
    prompt = " ".join(str(entry.get("prompt") or "").split())
    if len(prompt) > PROMPT_CHARS:
        prompt = prompt[:PROMPT_CHARS - 1].rstrip() + "…"
    said = f" — “{prompt}”" if prompt else ""
    reference = str(entry.get("reference") or "")
    source = GENERATED_FROM.get(reference, f"from {reference}")
    return f"  - {where}{said}, {source}, {_moment(entry.get('created_at'))}"


def _card_game(paths: ProjectPaths, render: dict[str, Any] | None
                ) -> tuple[Path, list[str]]:
    """A card's game: its Godot project, the scene of its render and the script
    of that scene's root -- when they are known; nothing is guessed.

    The game map (`survey.game_map`) reads them without running anything, and
    cites a scene's root and script the way the survey does: from the game
    root. Return the Godot project folder, and the brief's bullets.
    """
    from . import survey

    root = paths.root
    if not paths.linked:
        return paths.godot, ["- **Folder** — none: the project is open on no game "
                             "folder; the card is discussed without it."]
    game = survey.game_map(root)
    projects = {project["path"]: project for project in game["godot"]}
    lines = [f"- **Folder** — `{root}`: the card's paths start here."]
    file = str((render or {}).get("file") or "")
    res_path = str((render or {}).get("scene") or "")
    # The scene's Godot project: what precedes its `res://` path in its path
    # from the game root (`client/` + `scenes/hud/top_bar.tscn`).
    inner = res_path.removeprefix("res://")
    base = file[:-len(inner)].rstrip("/") if inner and file.endswith(inner) else None
    if base not in projects:
        base = next(iter(projects)) if len(projects) == 1 else None
    godot = root
    if base is not None:
        project = projects[base]
        godot = root / base if base else root
        name = f" “{project['name']}”" if project["name"] else ""
        main = f", main scene `{project['main_scene']}`" if project["main_scene"] else ""
        lines.append(f"- **Godot project** — `{base or '.'}/`{name}{main}")
    elif projects:
        lines.append("- **Godot projects** — " + ", ".join(f"`{path or '.'}/`"
                                                          for path in projects))
    else:
        lines.append("- **Godot project** — no `project.godot`: the card is discussed "
                     "without a game.")

    if not render:
        lines.append("- **Card scene** — none known: the card has no render yet.")
        return godot, lines
    cited = f"`{res_path}`" + (f" (`{file}`)" if file else "")
    scene = next((entry for entry in game["scenes"] if entry["path"] == file), None)
    if scene is None:
        lines.append(f"- **Card scene** — {cited}: not found in the game today (renamed, "
                     "or moved).")
        return godot, lines
    lines.append(f"- **Card scene** — {cited}, root `{scene['root'] or '?'}`")
    lines.append(f"- **Script of its root** — `{scene['script']}`" if scene["script"]
                 else "- **Script of its root** — none: the scene carries none.")
    return godot, lines


# What a world card's brief shows of its neighbours and of its concepts.
MAX_NEIGHBOURS = 12
MAX_CONCEPTS = 6


def _asset_paths(project: str, ids: list[str]) -> dict[str, str]:
    """The path to cite for each asset: the library, else the store."""
    from . import library

    st = space(project)
    found = {asset_id: entry["path"] for asset_id, entry in library.locate(project, ids).items()
             if Path(entry["path"]).is_file()}
    for asset_id in ids:
        if asset_id not in found:
            stored = st.store.path_for(asset_id)
            if stored is not None:
                found[asset_id] = str(stored)
    return found


def _world_card_brief(project: str, section: str, name: str) -> dict[str, Any]:
    """The brief of a discussion on a world card: the entity, where it stands.

    A world card has neither render nor sketch: it has a filing (its section's
    axes), neighbours (the cards of the same group), concepts, a chosen
    concept, and maybe already its 3D. That is what the agent receives to talk
    about the subject without rediscovering it.
    """
    from . import entities

    bench = entities.workbench(project, section, name)
    rubric = world.section(project, section)
    folder = rubric["folder"]
    name = bench["name"]
    title = bench["title"]
    entity = bench["entity"]
    st = space(project)
    paths = st.paths
    library = st.librarian.project_dir(project)
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    call = f'project="{project}", section="{section}", name="{name}"'

    template = documents.TEMPLATES["card"]
    sections = [line[3:].strip() for line in template["body"].splitlines()
                if line.startswith("## ")]
    seeds = " and ".join(f"“{heading}”" for heading in sections
                         if heading.lower() in entities.SEED_SECTIONS)
    seeded = f" {seeds} seed the prompt of its concepts." if seeds else ""

    filed = bench["axes"]
    ranks = []
    for axis in rubric["axes"]:
        value = next((entry for entry in axis["values"] if entry["id"] == filed.get(axis["id"])),
                     None)
        ranks.append(f"{axis['label']}: {value['label'] if value else '—'}")
    rank = (f"- **Filing** — {' · '.join(ranks)}. It changes through "
            f"`entity_axes({call}, values={{<axis>: <value>}})`, in the section's "
            "grid." if ranks else "- **Filing** — the section declares no axis.")
    primary = rubric["axes"][0] if rubric["axes"] else None
    group = filed.get(primary["id"]) if primary else None
    others = [row for row in entities.entities(project)
              if row["section"] == section and row["name"] != name
              and (not primary or row["axes"].get(primary["id"]) == group)]
    if primary:
        label = next((entry["label"] for entry in primary["values"] if entry["id"] == group),
                     f"no {primary['label'].lower()}")
        where_from = f"in the group {primary['label']}: {label}"
    else:
        where_from = "in the section"
    if others:
        shown = "\n".join(f"  - `{documents.directory(project, folder) / (row['name'] + '.md')}`"
                           f" — “{row['title']}”" for row in others[:MAX_NEIGHBOURS])
        if len(others) > MAX_NEIGHBOURS:
            shown += f"\n  - … and {len(others) - MAX_NEIGHBOURS} more (`world_entities`)"
        near = (f"- **Neighbours** — {len(others)} card(s) {where_from}, to read to stay "
                f"consistent; they are not changed without a request:\n{shown}")
    else:
        near = f"- **Neighbours** — no card {where_from}."

    concepts = bench["concepts"]
    retained = bench["concept"]
    cited = _asset_paths(project, [*([retained] if retained else []),
                                   *(entry["id"] for entry in concepts[:MAX_CONCEPTS])])
    if retained:
        chosen = (f"- **Chosen concept** — `{cited.get(retained, '—')}` (asset `{retained}`): "
                  "the one that goes to 3D.")
    else:
        chosen = "- **Chosen concept** — none."
    if concepts:
        listed = "\n".join(
            f"  - `{cited.get(entry['id'], '—')}` (asset `{entry['id']}`, "
            f"{_moment(entry['created_at'])})" for entry in concepts[:MAX_CONCEPTS])
        if len(concepts) > MAX_CONCEPTS:
            listed += (f"\n  - … and {len(concepts) - MAX_CONCEPTS} more, which "
                       f"`entity_workbench({call})` returns in full")
        drawn = f"- **Drawn concepts** — {len(concepts)}, most recent first:\n{listed}"
    else:
        drawn = "- **Drawn concepts** — none."
    look = ("\n\nA CLI that does not read image files sees them through `view_asset(<asset>)`."
            if concepts or retained else "")

    character = st.db.get_character(project, entity)
    rig3d = character.rig3d if character is not None else None
    if rig3d is not None and rig3d.mesh_asset_id:
        mesh = _in_library(library, f"3d/{entity}/{entity}.glb")
        moves = ", ".join(rig3d.animations) or "none"
        three = (f"- **Mesh** — `{mesh}` (asset `{rig3d.mesh_asset_id}`): "
                 f"{len(rig3d.bones)} bones, animations: {moves}.")
    else:
        three = "- **Mesh** — not yet: the card has not gone to 3D."
    pending = len(bench["pending"])
    if pending:
        three += f"\n- **In progress** — {pending} job(s) (`queue_status`)."
    game = (f"`{paths.root}` — read-only: read it when talking about what the game already "
            "does with this entity." if paths.linked
            else "No game folder for this project.")

    text = f"""# Brief — discussion on “{title}” ({rubric['label']})

Project: `{project}` · section `{folder}` · entity `{entity}` · written {when}

A working discussion, not a mission to finish alone: the user is moving
forward on this subject with you. This brief is reread up to date — one more
concept, a 3D — through `card_brief(project="{project}", folder="{folder}", name="{name}")`.

## The subject

- **Card** — `{bench['path']}`
  It is alive: reread it when it comes up, never copy it.
- **Template** — “{template['label']}”: {', '.join(sections)}.{seeded}
{rank}
{near}

## Its images

{chosen}
{drawn}{look}

## Its 3D

{three}

## The game

{game}

## What you do

1. Read the card and look at its images, then say in two or three lines where
   the subject stands: what the card holds, what it lacks, and its stage
   (card → concepts → 3D → rig and animations). Then wait for the request.
2. The card changes when the user asks, keeping its template.
   {_writing(_skill('game-survey'))}
3. The next stages have their tools: `entity_concepts({call})` draws concepts
   from the card, `entity_choose_concept` picks one, `entity_realize` takes it
   to 3D, `entity_animation_brief` hands the rig and animations to an agent.
   `entity_workbench({call})` tells the whole workbench.

## Rules

- Nothing paid without the user's explicit consent in the discussion: a
  concept costs money (~$0.006 per image), a 3D much more ($0.15 to $1.25) —
  `confirm=true` only after their yes.
- Touch only this card: the neighbours are read, they are not changed without
  a request. An axis or a value is only created on request (`world_axes`).
- The game is only changed if the user asks.
"""
    path = _write(project, f"card-{'-'.join([*documents.shelf(folder), name])}.md", text)
    prompt = (f"We are working on “{title}” ({rubric['label']}) of project {project}: read "
              f"the brief {path}, look at the images it cites, tell me in two lines where "
              "this card stands, then wait for my request.")
    return {"project": project, "folder": folder, "name": name, "title": title,
            "section_label": rubric["label"], "path": str(path), "text": text, "prompt": prompt,
            "godot": str(paths.root), "godot_ready": (paths.godot / "project.godot").is_file()}


def card_brief(project: str, folder: str, name: str) -> dict[str, Any]:
    """Write the brief of a discussion on a card, and return it.

    Not a mission to finish alone: the user keeps moving forward on the
    subject with the agent. The brief cites the card by path -- it is alive, it
    is not copied --, its images by absolute path (the game render, the user's
    sketch, the generated images: `cards.media`), the game it describes, and
    what the agent does: look, say where the subject stands, wait for the
    request. No Godot project is required: a mechanic is discussed without a
    game. A world card (`world/<section>`) gets its entity's brief
    (`_world_card_brief`).
    """
    from . import cards

    parts = documents.shelf(folder)
    if len(parts) == 2 and parts[0] == world.SHELF:
        return _world_card_brief(project, parts[1], name)

    card = documents.read_document(project, name, folder)
    name = card["name"]
    media = cards.media(project, folder, name)
    paths = project_paths(studio().settings, project)
    title = media.get("title") or card["title"]
    section_label = media.get("section_label") or folder
    render = media.get("render")
    skill = _skill("game-survey")
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    call = f'project="{project}", folder="{folder}", name="{name}"'

    try:
        template = documents.TEMPLATES[catchup_target(project, folder)["template"]]
    except ServiceError:
        template = None
    sections = [line[3:].strip() for line in (template or {}).get("body", "").splitlines()
                if line.startswith("## ")]
    shape = (f"\n- **Template** — “{template['label']}”: {', '.join(sections)}."
             if template else "")

    if render:
        size = (f", {render['width']} x {render['height']} px"
                if render.get("width") and render.get("height") else "")
        seen = (f"- **Game render** — `{render['path']}`: scene `{render['scene']}`, "
                f"rendered {_moment(render.get('rendered_at'))}{size}. It is the game as "
                "it is.")
    else:
        seen = "- **Game render** — none."
    sketch = media.get("sketch")
    if sketch and sketch.get("png"):
        drawn = (f"- **User's sketch** — `{sketch['png']}`. Look at it: it is what the "
                 "user is talking about; it is re-exported with every stroke, reread it "
                 "when it comes up.")
    else:
        drawn = ("- **User's sketch** — none"
                 + (": the sketch is empty." if sketch else "."))
    references = list(media.get("references") or [])
    if references:
        kept = "\n".join(f"  - `{entry['path']}`" for entry in references)
        refs = (f"- **User's references** — {len(references)} image(s) dropped for this "
                f"card. Look at them: they say what the user wants, better than words; the "
                f"card's “References” section says what is kept from each one, by its file "
                f"name.\n{kept}")
    else:
        refs = "- **User's references** — none."
    generations = list(media.get("generations") or [])
    pending = int(media.get("pending") or 0)
    waiting = f"; {pending} in progress" if pending else ""
    if generations:
        shown = [_generated(entry) for entry in generations[:MAX_GENERATIONS]]
        if len(generations) > MAX_GENERATIONS:
            shown.append(f"  - … and {len(generations) - MAX_GENERATIONS} more, "
                         "which `card_media` returns in full")
        order = ", most recent first" if len(generations) > 1 else ""
        generated = (f"- **Generated images** — {len(generations)}{order}{waiting}:\n"
                     + "\n".join(shown))
    else:
        generated = f"- **Generated images** — none{waiting}."
    failures = list(media.get("failures") or [])
    if failures:
        # Otherwise a failed generation leaves no trace in the brief: the wait
        # disappears and no image comes.
        last = failures[0]
        reason = " ".join(str(last.get("error") or "").split())[:PROMPT_CHARS]
        generated += (f"\n  - {len(failures)} recent failure(s), the last one "
                      f"{_moment(last.get('at'))}: “{reason or 'no message'}” "
                      f"(job `{last.get('job', '')}`, `job_detail`)")
    look = (f"\n\nA CLI that does not read image files sees them through "
            f"`card_media({call}, look=true)`."
            if render or generations or references or (sketch and sketch.get("png"))
            else "")

    godot, game = _card_game(paths, render)
    ready = (godot / "project.godot").is_file()
    scene = render["scene"] if render else "<scene>"
    redo = "redo the render" if render else "render the scene that shows it"
    again = (f"\n   `card_rerender({call})` redoes the last one as is, with the same "
             "settings."
             if render and render.get("rerender") else "")
    lines = "\n".join(game)

    text = f"""# Brief — discussion on “{title}” ({section_label})

Project: `{project}` · section `{folder}` · written {when}

A working discussion, not a mission to finish alone: the user is moving
forward on this subject with you. This brief is reread up to date — one more
image, a new render — through `card_brief({call})`.

## The subject

- **Card** — `{card['path']}`
  It is alive: reread it when it comes up, never copy it.{shape}

## The images

{seen}
{drawn}
{refs}
{generated}{look}

## The game

{lines}

## What you do

1. Read the card and look at its images, then say in two or three lines where
   the subject stands. Then wait for the request.
2. The card changes when the user asks, keeping its template.
   {_writing(skill)}
3. The game is only changed if the user asks. Afterwards, {redo} —
   `render_scene(project="{project}", scene="{scene}", name="{name}", folder="{folder}")`
   —, look at it (`view_asset`), and update the “Game survey” section.{again}
4. A sketch is an intent, not a measurement: when it is ambiguous, ask.

## Rules

- Nothing paid without the user's explicit consent in the discussion: a
  generated image costs money (`confirm=true` only after their yes).
- A contradiction between the card, the sketch and the game is raised as a
  question to the user; it is not settled alone.
"""
    # `card-design-interface-top-bar.md`: the section and the name, already valid.
    path = _write(project, f"card-{'-'.join([*documents.shelf(folder), name])}.md", text)
    prompt = (f"We are working on “{title}” ({section_label}) of project {project}: read "
              f"the brief {path}, look at the images it cites, tell me in two lines where "
              "this card stands, then wait for my request.")
    return {"project": project, "folder": folder, "name": name, "title": title,
            "section_label": section_label, "path": str(path), "text": text, "prompt": prompt,
            "godot": str(godot), "godot_ready": ready}


def send_card(project: str, folder: str, name: str, *, harness: str = DEFAULT,
               effort: str = "", session: str = "",
               message: str = "", loop: Any = None) -> dict[str, Any]:
    """Open an agent discussion on a card: a new tab, or `session`.

    Nothing requires a Godot project: a mechanic is discussed without a game.
    The tab is named after the subject, "<section> · <title>".
    """
    sent = card_brief(project, folder, name)
    tab = _dispatch(project, sent["prompt"], f"{sent['section_label']} · {sent['title']}",
                    harness=harness, effort=effort, session=session, message=message, loop=loop)
    return {**{key: value for key, value in sent.items() if key != "text"}, "session": tab}


# -------------------------------------------- a showcase element, discussed

# The family neighbours shown in the brief: enough to judge consistency.
MAX_SIBLINGS = 12
KIND_NAMES = {"image": "game image", "scene": "scene", "stylebox": "StyleBox"}
STATE_NAMES = {"normal": "Normal", "hover": "Hover", "pressed": "Pressed",
               "disabled": "Disabled"}


def showcase_brief(project: str, kind: str, element: str) -> dict[str, Any]:
    """Write the brief of a discussion on a showcase element (icon or prop).

    The showcase holds no verdict: what is in the game is kept. An element to
    rework -- or to remove -- is discussed: the brief cites the element and its
    images by absolute path (each state of a button), its family to judge
    consistency, what uses it, the section's cards, and what the agent does:
    look, say what it sees, wait for the request.
    """
    from . import showcase

    item = showcase.element(project, kind, element)
    root = Path(item["root_dir"])
    label = showcase.LABELS[kind]
    title = item["title"]
    call = f'project="{project}", kind="{kind}", element="{element}"'
    when = datetime.now().strftime("%Y-%m-%d %H:%M")

    nature = ("icon" if kind == "icons" else
              f"{KIND_NAMES[item['type']]}" + (f" with root `{item['root']}`"
                                               if item.get("root") else ""))
    facts = [f"- **File** — `{root / item['file']}`"
             + (f" (`{item['res_path']}` in Godot)" if item.get("res_path") else ""),
             f"- **Nature** — {nature}"]
    if item.get("width") and item.get("height"):
        facts.append(f"- **Source size** — {item['width']:g} x {item['height']:g}")
    users = item["users"]
    facts.append("- **Used by** — " + (", ".join(f"`{user}`" for user in users)
                                       if users else "nothing the game map can see "
                                       "(search for its name in the scripts: a composed "
                                       "path does not show up there)"))

    images = []
    for entry in item["images"]:
        state = entry["state"]
        drawn = next((s for s in item.get("states", []) if s["state"] == state), None)
        same = " — same as Normal: the theme does not distinguish this state" \
            if drawn and drawn.get("same") else ""
        images.append(f"- {STATE_NAMES.get(state, title)} — `{entry['path']}`{same}")
    if not images:
        images.append("- none: the prop is not drawn yet "
                      f"(`showcase_render(project=\"{project}\")`)")

    family = item.get("siblings", [])
    siblings = []
    for other in family[:MAX_SIBLINGS]:
        found = showcase.element(project, kind, other)
        shown = found["images"][0]["path"] if found["images"] else str(root / found["file"])
        siblings.append(f"- {found['title']} — `{shown}`")
    if len(family) > MAX_SIBLINGS:
        siblings.append(f"- … and {len(family) - MAX_SIBLINGS} more: `showcase_list("
                        f"project=\"{project}\", kind=\"{kind}\")`")
    if not siblings:
        siblings.append("- none: the element is alone in its family.")

    shelf = showcase.SHELVES[kind]
    notes = [f"- `{entry['path']}` — {entry['title']}"
             for entry in documents.documents(project, shelf)]
    skill = _skill("game-survey")
    branch = f"studio/{kind}-{element}"
    if kind == "icons":
        redo = f"""2. **Redo the icon** (redraw it with Runware) — the forge does it all the way
   into the game, in this order:
   1. propose to the user what you will ask for: what changes (in one
      sentence, in English for the model), the family's style
      (`forge_families` returns it, written or to write from what you see),
      the model and the number of variants, **and the amount** (~$0.006 per
      image with FLUX dev, 2 variants by default); wait for their yes;
   2. `forge_request(project="{project}", mode="redo", element="{element}",
      description=…, style=…, count=…, confirm=true)` — the current icon serves
      as the starting image;
   3. follow the request (`forge_list`: `running`, then `ready`), then look at
      the proposals (`forge_look`) and show them to the user;
   4. `forge_adopt(project="{project}", demand=…, picks=[{{"source": "asset:<id>",
      "name": "{Path(item['file']).stem}"}}])` with the one they choose: it is
      matted, scaled to the family and takes the place of the old one (which
      goes to the trash);
   5. look at it in the showcase (`showcase_look`) next to its neighbours.
   A new icon for the family follows the same path (`mode="one"`,
   `folder="{Path(item['file']).parent.as_posix()}"`), and so do several at once
   (`mode="set"`, then `forge_split` on the chosen sheet). A criterion that
   fails (background badly removed, style drifting) goes back to the user
   instead of paying again in a loop.
"""
    else:
        redo = ("2. A prop is reworked in its scene: on the branch (point 3), or through "
                "the Edit tab of a props card.\n")
    redraw = (f"`showcase_render(project=\"{project}\")` redraws the prop; "
              if kind == "props" else "")

    text = f"""# Brief — discussion on “{title}” ({label})

Project: `{project}` · showcase `{kind}` · family “{item['family_label']}” · written {when}

A working discussion, not a mission to finish alone: the user is looking at
this element in the showcase and wants to talk about it — rework it, compare
it with its family, or remove it from the game. The showcase holds no
verdict: what is in the game is kept, what is no longer wanted leaves it.
This brief is reread up to date through `showcase_brief({call})`.

## The element

{chr(10).join(facts)}

## Its images

{chr(10).join(images)}

A CLI that does not read image files sees them through `showcase_look({call})`.

## Its family “{item['family_label']}”

{chr(10).join(siblings)}

## The cards of the {label} section

{chr(10).join(notes) if notes else "- none."}

## What you do

1. Look at the element, then at its family, and say in two or three lines
   what you see: legibility at small sizes, consistency of stroke, palette and
   proportions with its neighbours; for a button, what its states distinguish
   or not. Then wait for the request.
{redo}
3. Any other change to the game is made only at the user's request, on a
   separate branch (`{branch}`) that they will merge. Afterwards, {redraw}look
   at the result (`showcase_look`) before saying it is done.
4. Removing the element from the game is the showcase's “Delete” button: a
   user gesture (the file goes to the project's trash, `trash_restore` brings
   it back). First tell them what uses it; if they ask, rework those uses on
   the branch, then validate the scenes concerned (headless Godot).
5. A card of the section that describes the element is updated when the
   element changes or leaves. {_writing(skill)}

## Rules

- Nothing paid without the user's explicit consent in the discussion: a
  generated image costs money (`confirm=true` only after their yes).
- A contradiction between the element, its family and a card is raised as a
  question to the user; it is not settled alone.
"""
    path = _write(project, f"showcase-{kind}-{element}.md", text)
    prompt = (f"We are looking at “{title}” ({label}) of project {project}: read the brief "
              f"{path}, look at the element and its family, tell me in two lines what you "
              "see, then wait for my request.")
    return {"project": project, "kind": kind, "element": element, "title": title,
            "section_label": label, "family": item["family_label"], "path": str(path),
            "text": text, "prompt": prompt, "godot": str(root)}


def send_showcase(project: str, kind: str, element: str, *, harness: str = DEFAULT,
                  effort: str = "", session: str = "",
                  message: str = "", loop: Any = None) -> dict[str, Any]:
    """Open an agent discussion on a showcase element: a new one, or `session`."""
    sent = showcase_brief(project, kind, element)
    tab = _dispatch(project, sent["prompt"], f"{sent['section_label']} · {sent['title']}",
                    harness=harness, effort=effort, session=session, message=message, loop=loop)
    return {**{key: value for key, value in sent.items() if key != "text"}, "session": tab}


# ---------------------------------------------- a lookdev shader, discussed

# The uses of a shader cited in the brief: beyond this, `lookdev_specimen`.
MAX_PRESETS = 8
SHADER_KINDS = {"canvas_item": "interface (canvas_item)", "spatial": "material (spatial)",
                "sky": "sky (sky)", "particles": "particles", "fog": "fog",
                "include": "library (include)"}


def lookdev_brief(project: str, specimen: str) -> dict[str, Any]:
    """Write the brief of a discussion on a lookdev shader (the game's art direction).

    The Universe holds no verdict: what is in the game is kept. A shader to
    rework -- or to remove -- is discussed: the brief cites the shader by path,
    its uses in the game with their settings, its thumbnail when the bench
    renders it, and how to look at it live (`lookdev_look`).
    """
    from . import lookdev

    root, game, entry = lookdev._find(project, specimen)
    state = lookdev._state(project, specimen)
    shader = next(item for item in game["shaders"] if item["path"] == entry["file"])
    presets = lookdev._presets(root, game, shader, entry["res_path"])
    title = entry["title"]
    call = f'project="{project}", specimen="{specimen}"'
    when = datetime.now().strftime("%Y-%m-%d %H:%M")

    facts = [f"- **File** — `{root / entry['file']}` (`{entry['res_path']}` in Godot)",
             f"- **Nature** — {SHADER_KINDS.get(entry['kind'], entry['kind'])}, "
             f"{entry['uniforms']} setting(s)",
             "- **Used by** — " + (", ".join(f"`{user}`" for user in entry["users"])
                                   if entry["users"] else "nothing the game map can see")]
    if state["setup"]:
        facts.append("- **Shown on** — the game's real object, built by the Universe's "
                     "setup (`lookdev_specimen` returns it)")
    uses = [f"- {preset['label']} — `{preset['file']}`"
            + (f": {', '.join(f'{key}={value}' for key, value in preset['params'].items())}"
               if preset["params"] else "") for preset in presets[:MAX_PRESETS]]
    if len(presets) > MAX_PRESETS:
        uses.append(f"- … and {len(presets) - MAX_PRESETS} more: `lookdev_specimen({call})`")
    if not uses:
        uses.append("- no use found: the shader is shown with its default values.")
    seen = "- none: the lookdev bench has not rendered it."
    if entry["renderable"]:
        try:
            seen = f"- Thumbnail — `{lookdev.thumbnail(project, specimen)}`"
        except ServiceError as exc:
            seen = f"- none: {exc}"
    branch = f"studio/lookdev-{specimen}"
    # The art direction's ticked rules: they bind a material's rework.
    ruled = "\n".join(lookdev.rule_lines(project)) if entry["kind"] == "spatial" else ""
    if ruled and entry.get("textures"):
        ruled += "\n- This shader reads an image today: say so before reworking it."

    text = f"""# Brief — discussion on the shader “{title}” (art direction)

Project: `{project}` · Universe (lookdev) · written {when}

A working discussion, not a mission to finish alone: the user is looking at
this shader in the game's Universe and wants to talk about it — rework it,
tune it, compare it, or remove it. The Universe holds no verdict: what is in
the game is kept, what is no longer wanted leaves it. This brief is reread up
to date through `lookdev_brief({call})`.

## The shader

{chr(10).join(facts)}

## Its uses in the game

{chr(10).join(uses)}

## Seeing it

{seen}

Live, with other settings, on another use or from another angle:
`lookdev_look({call}, preset=…, params={{…}})` — rendered by Godot itself, in
a few tens of milliseconds.

## What you do

1. Look at the shader (its thumbnail, then `lookdev_look` on its uses), read
   its file, and say in two or three lines what you see: what it draws, what
   its settings change, how it holds up with the rest of the game. Then wait
   for the request.
2. The game is only changed at the user's request. Setting values to keep are
   written with `lookdev_save({call}, preset=…, params={{…}})`, like the
   room's "Save" button: only the use's `shader_parameter/` lines (or the
   shader's defaults) change. The shader's code is reworked on a separate
   branch (`{branch}`) that they will merge. After the change, look at the
   result (`lookdev_look`) before saying it is done.
3. Removing the shader from the game is the decision "we do not want it":
   only at their request, after telling them what uses it. A material left
   pointing to a removed shader breaks the game: rework it too, then validate
   the scenes concerned (headless Godot).

## Rules

- Nothing paid without the user's explicit consent in the discussion.
- A contradiction between the shader, the written art direction and the game
  is raised as a question to the user; it is not settled alone.
{ruled}"""
    path = _write(project, f"lookdev-{specimen}.md", text)
    prompt = (f"We are looking at the shader “{title}” in the Universe of project {project}: "
              f"read the brief {path}, look at it, tell me in two lines what you see, then "
              "wait for my request.")
    return {"project": project, "specimen": specimen, "title": title,
            "section_label": "Art direction", "path": str(path), "text": text,
            "prompt": prompt, "godot": str(root)}


def send_lookdev(project: str, specimen: str, *, harness: str = DEFAULT, effort: str = "",
                 session: str = "", message: str = "", loop: Any = None) -> dict[str, Any]:
    """Open an agent discussion on a lookdev shader: a new one, or `session`."""
    sent = lookdev_brief(project, specimen)
    tab = _dispatch(project, sent["prompt"], f"Universe · {sent['title']}",
                    harness=harness, effort=effort, session=session, message=message, loop=loop)
    return {**{key: value for key, value in sent.items() if key != "text"}, "session": tab}


# ------------------------------------- an aspect of the art direction, discussed

# The Universe's sections a discussion can be on as a whole: its title, what it is about.
LOOKDEV_ASPECTS = {"colors": ("Colors", "the game's colors"),
                   "typography": ("Typography", "the game's typography"),
                   "interface": ("Interface shaders", "the interface shaders"),
                   "materials": ("Material shaders", "the material shaders"),
                   "sky": ("Sky shaders", "the sky shaders"),
                   "other": ("Other shaders", "the other shaders")}
COLOR_ASPECTS = {"interface": "Interface", "materials": "Materials and 3D world",
                 "sky": "Sky and environment", "other": "Elsewhere (logic, 2D)"}
# The colors the colors section holds: a material's colors are discussed with
# the material shaders, as the Universe shows them.
PALETTE_ASPECTS = ("interface", "sky", "other")
# What a brief cites: beyond this, `lookdev` gives the whole of it.
MAX_BRIEF_COLORS = 24
MAX_BRIEF_USES = 4
MAX_BRIEF_SHADERS = 24


def _color_lines(palette: dict[str, Any], only: str = "") -> list[str]:
    """The palette in a brief, aspect by aspect: each color with where it is written."""
    lines: list[str] = []
    for aspect in (only,) if only else PALETTE_ASPECTS:
        held = sorted((color for color in palette["colors"] if color["aspects"].get(aspect)),
                      key=lambda color: -color["aspects"][aspect])
        if not held:
            continue
        if not only:
            lines += [f"### {COLOR_ASPECTS[aspect]} — {len(held)} color(s)", ""]
        for color in held[:MAX_BRIEF_COLORS]:
            uses = [use for use in color["uses"] if use["aspect"] == aspect]
            where = ", ".join(
                f"`{use['file']}`" + (f" ({', '.join(use['names'])})" if use["names"] else "")
                + (f" x{use['count']}" if use["count"] > 1 else "")
                for use in uses[:MAX_BRIEF_USES])
            if len(uses) > MAX_BRIEF_USES:
                where += f", and {len(uses) - MAX_BRIEF_USES} more file(s)"
            lines.append(f"- `{color['hex']}` x{color['aspects'][aspect]} — {where}")
        if len(held) > MAX_BRIEF_COLORS:
            lines.append(f"- … and {len(held) - MAX_BRIEF_COLORS} more (`lookdev` → `palette`)")
        lines.append("")
    return lines or ["- none written literally: the game computes them, or has none.", ""]


def _type_lines(typography: dict[str, Any], root: Path) -> list[str]:
    """The typography in a brief: families and faces, default font, resources, sizes, texts."""
    lines = ["### Families", ""]
    for family in typography["families"]:
        lines.append(f"- **{family['family']}**")
        for face in family["faces"]:
            role = "default font" if face["default"] else (
                "cited nowhere" if not face["uses"] else f"{face['uses']} use(s)")
            lines.append(f"  - {face['style']} {face['weight']}"
                         + (" italic" if face["italic"] else "")
                         + f" — `{root / face['file']}` — {role}")
    if not typography["families"]:
        lines.append("- no font file: the game uses Godot's default font.")
    default = typography["default"]
    lines += ["", "### Default font", "",
              f"- `{default['resource'] or default['base']}`"
              + (f" — built on `{default['base']}`" if default["resource"] and default["base"]
                 else "") + (f", size {default['size']}" if default["size"] else "")
              if default["resource"] or default["base"] else
              "- none declared (`[gui] theme/custom_font` or a theme's `default_font`)."]
    if typography["resources"]:
        lines += ["", "### Font resources", ""]
        for resource in typography["resources"]:
            facts = [resource["type"]]
            if resource["base"]:
                facts.append(f"base `{resource['base']}`")
            if resource["fallbacks"] or resource["system"]:
                facts.append("fallbacks " + " → ".join(resource["system"] + resource["fallbacks"]))
            if resource["features"]:
                facts.append("OpenType " + ", ".join(f"{feature['tag']}={feature['value']}"
                                                     for feature in resource["features"]))
            facts += [f"{key}={value}" for key, value in resource["settings"].items()]
            lines.append(f"- `{resource['file']}` — {'; '.join(facts)}")
    if typography["sizes"]:
        lines += ["", "### Sizes set by the game", "",
                  "- " + ", ".join(f"{size['size']} px x{size['count']}"
                                   for size in typography["sizes"])]
    if typography["samples"]:
        lines += ["", "### Texts the game shows", "",
                  "- " + " · ".join(f"“{sample}”" for sample in typography["samples"][:12])]
    return [*lines, ""]


def lookdev_aspect_brief(project: str, aspect: str) -> dict[str, Any]:
    """Write the brief of a discussion on a whole section of the Universe.

    `aspect`: `colors`, `typography`, or a family of shaders (`interface`,
    `materials`, `sky`, `other`). Not one element: the section as a whole --
    a palette's harmony, a type system, how the interface shaders agree. The
    brief cites what the game holds for it, read from its files, with where
    each thing is written; the written art direction is cited by path.
    """
    from . import colors, fonts, lookdev

    if aspect not in LOOKDEV_ASPECTS:
        raise NotFound(f"unknown aspect: {aspect} (known: {', '.join(LOOKDEV_ASPECTS)})")
    root, game, found = lookdev._specimens(project)
    title, subject = LOOKDEV_ASPECTS[aspect]
    call = f'project="{project}", aspect="{aspect}"'
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    cards = [f"- `{entry['path']}` — {entry['title']}"
             for entry in documents.documents(project, "design/direction")]
    branch = f"studio/lookdev-{aspect}"
    # The art direction's ticked rules bind the materials.
    ruled = "\n".join(lookdev.rule_lines(project)) if aspect == "materials" else ""

    if aspect == "colors":
        palette = colors.palette(root, game)
        held = [color for color in palette["colors"]
                if any(color["aspects"].get(aspect) for aspect in PALETTE_ASPECTS)]
        body = (f"{len(held)} color(s) for the interface, the sky and the rest of the game, the "
                "most used first; a material's colors are discussed with the material shaders. "
                "Each color says where it is written: the file, then the names carrying it (a "
                "property, a theme key, a constant, a shader setting).\n\n"
                + "\n".join(_color_lines(palette)))
        seeing = ("- The whole palette, every use included: `lookdev` → `palette`.\n"
                  "- A screen or a level with its colors: `render_scene`, drawn by the "
                  "game's engine.")
        change = ("A color changed is changed everywhere it is written (its uses list them), "
                  "or turned into one named constant or theme key at the user's request.")
    elif aspect == "typography":
        body = "\n".join(_type_lines(fonts.typography(root, game), root))
        seeing = ("- The faces, sizes and resources: `lookdev` → `typography`.\n"
                  "- A screen set in its type: `render_scene`, drawn by the game's engine.")
        change = ("A font is added to the game as a file with its licence; the default font "
                  "is changed where the project declares it, never node by node.")
    else:
        shown = [entry for entry in found if colors.shader_aspect(entry["kind"]) == aspect]
        listed = [f"- **{entry['title']}** — `{root / entry['file']}` "
                  f"({SHADER_KINDS.get(entry['kind'], entry['kind'])}, "
                  f"{entry['uniforms']} setting(s)) — used by "
                  + (", ".join(f"`{user}`" for user in entry["users"][:MAX_BRIEF_USES])
                     + (f" and {len(entry['users']) - MAX_BRIEF_USES} more"
                        if len(entry["users"]) > MAX_BRIEF_USES else "")
                     if entry["users"] else "nothing the game map can see")
                  + (" — reads an image" if entry.get("textures") else "")
                  + f" — `lookdev_brief(project=\"{project}\", specimen=\"{entry['id']}\")`"
                  for entry in shown[:MAX_BRIEF_SHADERS]]
        if len(shown) > MAX_BRIEF_SHADERS:
            listed.append(f"- … and {len(shown) - MAX_BRIEF_SHADERS} more (`lookdev`)")
        body = ("\n".join(listed or ["- none in the game."])
                + "\n\n### Their colors\n\n"
                + "\n".join(_color_lines(colors.palette(root, game), aspect)))
        seeing = ("- Each shader live, on its uses, from any angle: `lookdev_look(project=…, "
                  "specimen=…, preset=…)` — rendered by Godot itself.\n"
                  "- Each shader in detail (settings, uses): `lookdev_specimen`.")
        change = ("Setting values to keep are written with `lookdev_save`, like the Universe's "
                  "\"Save\"; a shader's code is reworked like the rest.")

    text = f"""# Brief — discussion on {subject} (art direction)

Project: `{project}` · Universe (lookdev) · written {when}

A working discussion on a whole section of the art direction, not on one
element and not a mission to finish alone: the user is looking at {subject}
in the game's Universe and wants to talk about the section as a whole —
what it says, whether it holds together, what to change. The Universe holds no
verdict: what is in the game is kept, what is no longer wanted leaves it.
This brief is reread up to date through `lookdev_aspect_brief({call})`.

## What the game holds

Game folder: `{root}`

{body}
## The written art direction

{chr(10).join(cards) if cards else "- no card yet in `design/direction`."}

The game's own docs are authoritative: cite them, never copy them.

## Seeing it

{seeing}

## What you do

1. Read what is above, open the files that matter, and say in three or four
   lines what you see: what holds the section together, what stands out,
   what contradicts the written art direction. Then wait for the request.
2. The game is only changed at the user's request, on a separate branch
   (`{branch}`) that they will merge. {change} After the change, look at
   the result before saying it is done, and validate the scenes concerned
   (headless Godot).

## Rules

- Nothing paid without the user's explicit consent in the discussion.
- A contradiction between the game and the written art direction is raised
  as a question to the user; it is not settled alone.
{ruled}"""
    path = _write(project, f"lookdev-aspect-{aspect}.md", text)
    prompt = (f"We are looking at {subject} in the Universe of project {project}: read the "
              f"brief {path}, tell me in a few lines what you see, then wait for my request.")
    return {"project": project, "aspect": aspect, "title": title,
            "section_label": "Art direction", "path": str(path), "text": text,
            "prompt": prompt, "godot": str(root)}


def send_lookdev_aspect(project: str, aspect: str, *, harness: str = DEFAULT, effort: str = "",
                        session: str = "", message: str = "",
                        loop: Any = None) -> dict[str, Any]:
    """Open an agent discussion on a whole section of the Universe: new, or `session`."""
    sent = lookdev_aspect_brief(project, aspect)
    tab = _dispatch(project, sent["prompt"], f"Universe · {sent['title']}",
                    harness=harness, effort=effort, session=session, message=message, loop=loop)
    return {**{key: value for key, value in sent.items() if key != "text"}, "session": tab}


# ------------------------------------------ creating in a section, on request

# The request words that name its brief, and the length it must have.
CREATE_BRIEF_WORDS = 6
MIN_CREATE_REQUEST = 12


def shelf_template(folder: str) -> str:
    """A section's template: its shelf's, `card` for the world, `blank` at the
    documents root."""
    if folder in documents.SHELVES:
        return documents.SHELVES[folder]["template"]
    if folder.split("/")[0] == world.SHELF:
        return "card"
    return "blank"


def _dropped(images: list[str]) -> list[Path]:
    """The images dropped with a request: absolute paths, or relative to the
    studio root (those the inbox returns). A missing image is refused before
    the brief is written: the agent would not look for it."""
    from . import cards

    root = studio().settings.project_root or Path.cwd()
    found: list[Path] = []
    for image in images:
        path = Path(image).expanduser()
        path = path if path.is_absolute() else root / path
        if not path.is_file():
            raise NotFound(f"image not found: {image}")
        if path.suffix.lower() not in cards.REFERENCE_TYPES:
            raise ServiceError(f"not a reference image: {image} (expected: "
                               f"{', '.join(sorted(cards.REFERENCE_TYPES))})")
        found.append(path.resolve())
    return found


def _filing(project: str, where: str,
            axes: dict[str, str] | None) -> tuple[dict[str, str], list[dict[str, str]]]:
    """The filing that a creation in a world section's group imposes.

    Return the values to set (`entity_axes`) and their labels. Refuse a filing
    outside a world section, or outside its grid: the request would go to a
    group that does not exist.
    """
    from . import entities

    wanted = {key: value for key, value in (axes or {}).items() if str(value or "").strip()}
    if not wanted:
        return {}, []
    parts = where.split("/")
    if len(parts) != 2 or parts[0] != world.SHELF:
        raise ServiceError(f"“{where or '(root)'}” is not a world section: no axis to file it "
                           "under")
    rubric = world.section(project, parts[1])
    resolved = {key: value for key, value in entities.resolve_axes(rubric, wanted).items()
                if value}
    shown: list[dict[str, str]] = []
    for axis in rubric["axes"]:
        if axis["id"] in resolved:
            value = next(entry for entry in axis["values"]
                         if entry["id"] == resolved[axis["id"]])
            shown.append({"axis": axis["id"], "axis_label": axis["label"],
                          "value": value["id"], "value_label": value["label"]})
    return resolved, shown


def create_brief(project: str, folder: str, request: str, images: list[str] | None = None,
                 names: list[str] | None = None,
                 axes: dict[str, str] | None = None) -> dict[str, Any]:
    """Write the brief handing to an agent what must be created in a section.

    The user says what they want ("the inventory screen: a grid..."); the agent
    makes a card of it -- or several, if the request names several -- with the
    section's template, next to those that already exist. The game, when the
    project has one, is read to anchor the card; it is not modified.

    `axes` (a world section): the group where the request is made ("Race:
    Humans") -- each card created is filed there.
    """
    from . import cards, entities

    wanted = request.strip()
    if len(wanted) < MIN_CREATE_REQUEST:
        raise ServiceError(f"request too short ({len(wanted)} character(s), at least "
                           f"{MIN_CREATE_REQUEST}): say what must be created")
    where = "/".join(documents.shelf(folder))
    label = cards.section_label(project, where) if where else "Documents"
    template_id = shelf_template(where)
    template = documents.TEMPLATES[template_id]
    sections = [line[3:].strip() for line in template["body"].splitlines()
                if line.startswith("## ")]
    shape = f": {', '.join(sections)}" if sections else ""
    present = documents.documents(project, where)
    shelf_dir = documents.directory(project, where)
    paths = project_paths(studio().settings, project)
    when = datetime.now()
    dropped = _dropped(list(images or []))
    filing, filed = _filing(project, where, axes)
    # The name the image had when dropped: the inbox renames it.
    labels = [str(label).strip() for label in (names or [])]

    listed = "\n".join(f"  - `{entry['file']}` — “{entry['title']}”"
                       for entry in sorted(present, key=lambda e: e["name"]))
    there = (f"{len(present)} document(s) today:\n{listed}" if present
             else "empty for now")
    attach = ""
    if dropped:
        shown = "\n".join(
            f"- `{path}`" + (f" — dropped as `{labels[i]}`"
                             if i < len(labels) and labels[i] and labels[i] != path.name
                             else "")
            for i, path in enumerate(dropped))
        call = f'project="{project}", folder="{where}", name=<name>'
        attach = f"""
## The reference images

The user dropped {len(dropped)} image(s) with the request. Look at them before
writing: they say what the user wants to see.

{shown}

Each one is filed with the card it illustrates — `card_reference_add({call},
path=<path>, filename=<dropped name, if any>)` —, and that card's
“References” section says, by file name, what is kept from it. An image that
concerns no card is reported, not filed at random.
"""
    # A card (game design, world) follows the skill's writing rules; a note, an
    # idea, a devlog entry is the user's writing.
    card = where.startswith("design/") or where.split("/")[0] == world.SHELF
    writing = (_writing(_skill("game-survey")) if card
               else "Write briefly, in the request's words; add nothing it does not say.")
    game = (f"- The game: `{paths.root}` — read-only. Read it when the request "
            "touches what it already does (a scene, a script, a piece of data), so that "
            "the card tells the truth; what it does not do yet is written “planned”."
            if paths.linked else "- The game: no game folder for this project.")
    folder_arg = f', folder="{where}"' if where else ""
    quoted = "\n".join(f"> {line}" if line else ">" for line in wanted.splitlines())
    group = " · ".join(f"{entry['axis_label']}: {entry['value_label']}" for entry in filed)
    ranged = ""
    file_step = ""
    if filing:
        section_id = where.split("/")[1]
        held = [entry for entry in sorted(present, key=lambda e: e["name"])
                if all(entities.card_axes(project, section_id, entry["name"]).get(key) == value
                       for key, value in filing.items())]
        listed_group = ("\n".join(f"  - `{entry['file']}` — “{entry['title']}”"
                                   for entry in held) if held else "  - none yet")
        values_arg = json.dumps(filing, ensure_ascii=False)
        ranged = f"""
## The group

The request is made in the group **{group}** of the section: everything
created here is filed there. The cards already filed there — those the request
most likely overlaps:
{listed_group}
"""
        file_step = (f"\n   Then file each card in the group: "
                     f"`entity_axes(project=\"{project}\", section=\"{section_id}\", "
                     f"name=<name>, values={values_arg})`.")

    text = f"""# Brief — create in “{label}”{f" · {group}" if group else ""}

Project: `{project}` · section `{where or '(root)'}` · written {when:%Y-%m-%d %H:%M}

## The request

{quoted}

## Where

- The section: `{shelf_dir}` — {there}
{game}
- The template: “{template['label']}”{shape}.
{ranged}{attach}
## What you do

1. Read the request, then the section's documents it overlaps. If it describes
   a subject that already has its document, say so and ask: completing it
   beats a duplicate. If a decision the request does not settle is missing,
   ask the question; otherwise, ask nothing.
2. One document per subject the request names: `create_document(project="{project}",
   title=<title>, template="{template_id}"{folder_arg})`, then
   `write_document(project="{project}", name=<name>, text=<text>{folder_arg})`.
   The title is the subject's name, short.{file_step}
3. {writing}
4. Return the path of each document created, and say in two lines what it
   holds.

## Rules

- Touch only this section: no other document is changed without a request.
- The game is not modified.
- Nothing paid: no image is generated without the user's explicit consent in
  the discussion.
- What the request does not say is written “to be specified”; nothing is
  invented.
"""
    words = documents.slug(" ".join(wanted.split()[:CREATE_BRIEF_WORDS])) or "request"
    stem = "-".join(documents.shelf(where)) or "documents"
    path = _write(project, f"create-{stem}-{when:%Y%m%d-%H%M%S}-{words}.md", text)
    inside = f" ({group})" if group else ""
    prompt = (f"Create in the “{label}” section{inside} of project {project} what the brief "
              f"{path} asks for: read it, ask me a question only if a decision is missing, "
              "then write.")
    return {"project": project, "folder": where, "section_label": label,
            "template": template_id, "request": wanted, "path": str(path),
            "images": [str(image) for image in dropped], "axes": filed,
            "text": text, "prompt": prompt, "godot": str(paths.root)}


def send_create(project: str, folder: str, request: str, *, images: list[str] | None = None,
                names: list[str] | None = None, axes: dict[str, str] | None = None,
                harness: str = DEFAULT, effort: str = "",
                session: str = "", message: str = "", loop: Any = None) -> dict[str, Any]:
    """Hand a creation in a section to an agent: a new tab, or `session`."""
    sent = create_brief(project, folder, request, images, names, axes)
    group = " · ".join(entry["value_label"] for entry in sent["axes"])
    tab = _dispatch(project, sent["prompt"],
                    f"{sent['section_label']}{f' · {group}' if group else ''} · new",
                    harness=harness, effort=effort, session=session, message=message, loop=loop)
    return {**{key: value for key, value in sent.items() if key != "text"}, "session": tab}


# --------------------------------------------------------------------- skills

# The request words that name its brief: enough to recognize it.
SKILL_BRIEF_WORDS = 6


def skill_brief(request: str) -> dict[str, Any]:
    """Write the brief handing the writing of a skill to an agent, and return it.

    The user says what they want; the agent writes the procedure. A skill
    belongs to the studio, not to a project: the brief goes under
    `data/briefs/`, and the discussion opens at the studio root, where the
    agent sees `.claude/skills/`.
    """
    from . import skills

    wanted = request.strip()
    if len(wanted) < skills.MIN_DESCRIPTION:
        raise ServiceError(f"request too short ({len(wanted)} character(s), at least "
                           f"{skills.MIN_DESCRIPTION}): say what the procedure must do, and when "
                           "to follow it")
    home = _home()
    known = sorted(skills.skills(), key=lambda entry: entry["name"])
    listed = "\n".join(f"- `{entry['name']}` — {entry['description']}"
                       for entry in known if not entry["vendored"])
    when = datetime.now()
    quoted = "\n".join(f"> {line}" if line else ">" for line in wanted.splitlines())
    text = f"""# Brief — write a studio procedure

Written {when:%Y-%m-%d %H:%M}

## The request

{quoted}

## What a procedure is here

A studio skill does not state what a tool does — the MCP tool descriptions do
that. It says **in what order, under what conditions, and how to tell it is
done**. It lives in `{skills.source_dir()}/<name>/SKILL.md`.

- **name**: lowercase letters, digits, hyphens (`split-rework`); the plain
  title becomes the file's `# Title`.
- **description**: one sentence, at least {skills.MIN_DESCRIPTION} characters,
  that says **when** to follow it — it is what appears in the index and what
  decides whether an agent opens it.
- **The text**, in English, shaped like the existing procedures:

```markdown
{skills.template().rstrip()}
```

## The existing procedures

{listed or "none"}

If the request overlaps one of them, say so before writing: completing it
beats a duplicate.

## What you do

1. Read the request and the neighbouring procedure(s). If a decision the
   request does not settle is missing (the deliverable, the completion
   criterion, what costs money), ask the question; otherwise, ask nothing.
2. Write the procedure: `write_skill(name=<title>, description=<when>,
   body=<text>)` — or `gamestudio skills new <title> -d <when> -b <text>`.
   Both regenerate the index and repair the `.agents/skills/` mirror.
3. `skills_check` (or `gamestudio skills check`) must pass.
4. Return the file's path and summarize in three lines what it prescribes.

## Rules

- Tools are cited by name; how they work is not copied.
- No ritual: no "read first", no check "in passing" of a connection or a
  tool — act, and diagnose on failure.
- In the procedure, a paid operation requires the user's explicit consent
  (`confirm=true`), and an operation that hands back has proved nothing.
- Touch only this procedure: no other one is changed without a request.
"""
    words = documents.slug(" ".join(wanted.split()[:SKILL_BRIEF_WORDS])) or "procedure"
    folder = studio().settings.data_dir / "briefs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"skill-{when:%Y%m%d-%H%M%S}-{words}.md"
    path.write_text(text, encoding="utf-8")
    prompt = (f"Write a new studio procedure: read the brief {path}, ask me a question "
              "only if a decision is missing, then write it.")
    return {"request": wanted, "path": str(path), "text": text, "prompt": prompt,
            "root": str(home)}


def send_skill(request: str, *, harness: str = DEFAULT, effort: str = "",
               session: str = "", message: str = "", loop: Any = None) -> dict[str, Any]:
    """Hand the writing of a skill to an agent: a new tab, or `session`.

    The new tab opens at the studio root: that is where the skills live.
    """
    sent = skill_brief(request)
    tab = _dispatch("", sent["prompt"], "Procedure", harness=harness, effort=effort,
                    session=session, message=message, loop=loop, cwd=Path(sent["root"]))
    return {**{key: value for key, value in sent.items() if key != "text"}, "session": tab}


# ------------------------------------------------- preview data of a game


def preview_brief(project: str) -> dict[str, Any]:
    """Write the brief of the agent that fills a game's preview data.

    A networked game renders empty screens off line; the studio serves fake
    answers during its renders (`service/preview_data.py`). The agent writes
    them from the game's own code and docs, then renders to check.
    """
    from . import preview_data

    paths = project_paths(studio().settings, project)
    if not paths.linked:
        raise ServiceError(f"project {project} has no game folder: no preview data to write")
    current = preview_data.state(project)
    files = current["paths"]
    root = paths.root
    misses = current["misses"]
    gaps = ("\n".join(f"  - `{miss}`" for miss in misses[:20]) if misses
            else "  - none recorded yet")
    have = (f"{len(current['routes'])} route(s) already written: complete them, do not start "
            "over." if current["routes"] else "Nothing written yet.")
    text = f"""# Preview data of the game `{project}`

The studio draws the game's screens off line, with its own engine (`render_scene`,
the screen editor, the cards' renders). This game needs a server for its
content: without one, its screens say “server unreachable” and show nothing.
You write the **fake answers of its server**, which the studio serves during
each render, and nothing else. {have}

The studio called you by itself: the game's code talks to a server
({", ".join(f"`{f}`" for f in current["networked"]) or "its network classes"}), and a render
lacked data. It calls you again when a later render lacks other data.

## The files (the studio's, never the game's)

- `{files['server']}` -- `{{"enabled": true, "routes": [...]}}`; a route is
  `{{"method": "GET", "path": "/api/planet/*", "status": 200, "body": {{...}}}}`.
  `*` matches one path segment, `**` the rest; the query is ignored; the first
  route that matches answers.
- `{files['setup']}` -- GDScript, the body of a `setup(scene)` function run
  **before the scene exists** (`scene` is null: only autoloads are there), then
  again after the card's own setup. `{preview_data.PLACEHOLDER}` is the fake server's
  address. Typically: the client's base URL, a session token, the player's ids
  -- whatever makes the screens ask for their content. It must compile against
  the game's last commit too (the screen editor renders that branch).
- `{files['misses']}` -- what the last render asked without an answer.
  Last recorded:
{gaps}

## Procedure

1. Read how the game talks to its server: the autoloads of `project.godot`,
   its HTTP client (base URL, headers, session), each request and what the
   screens read from the answer. If the server is in the repository
   (`{root}`), its routes and types are the reference for the shapes; the
   game's docs and the studio's cards (`list_documents`) say what the world
   contains. They are read, never copied whole.
2. Write `setup.gd`, then the routes the main screens need. The data is
   plausible and consistent with the game: real names of the world, numbers
   in their ranges, enough items to fill a list -- not placeholders.
3. Render and look: `render_scene(project="{project}", scene=…)` for each
   screen that needs data; its notes say how many answers were served and
   which requests had none (`misses.json`). Complete, render again, until the
   screens are full. A render not looked at has proved nothing.
4. Say which screens are now full and which still lack data, then stop.

## Rules

- Nothing is written into the game, and no real server is started.
- Free and local: no paid call.
- If the game's screens do not need its server after all (the network code
  serves something else: an update check, analytics), write
  `{{"enabled": false, "auto": false, "routes": []}}` in `server.json`, say why,
  and stop: the studio will not call you again.
"""
    path = _write(project, "preview-data.md", text)
    prompt = (f"Fill the preview data of project {project}: read the brief {path} and "
              "follow it to the end.")
    return {"project": project, "path": str(path), "text": text, "prompt": prompt,
            "godot": str(root), "routes": len(current["routes"]), "misses": misses}


def send_preview(project: str, *, harness: str = DEFAULT, effort: str = "",
                 session: str = "", message: str = "", loop: Any = None,
                 gaps: list[str] | None = None) -> dict[str, Any]:
    """Hand the preview data of a game to an agent: a new tab, or `session`.

    `gaps`: what the render lacked, when the studio calls the agent by itself.
    """
    from . import preview_data

    sent = preview_brief(project)
    tab = _dispatch(project, sent["prompt"], f"Preview data · {project}", harness=harness,
                    effort=effort, session=session, message=message, loop=loop)
    preview_data.remember_agent(project, tab["id"], gaps if gaps is not None
                                else sorted(sent["misses"]))
    return {**{key: value for key, value in sent.items() if key != "text"}, "session": tab}
