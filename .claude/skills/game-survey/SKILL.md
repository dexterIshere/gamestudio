---
name: game-survey
description: Bring a studio section — interface, icons, props, mechanics, art direction, VFX, a world section — up to date with what the game already contains, when the project started before the studio or moved on in its code without it. This is what "update the … section" means.
---

# Bringing a section back in line with the game

"Update the interface section in gamestudio" asks neither to reformat the
section nor to add ideas to it: it asks to **catch it up with the game**. The
project moved on without it — it existed before the studio, or the work
happened directly in its code — and the section must say again what the game
really does, without losing what the user wrote in it.

The starting point is `shelf_update_brief(project, shelf)` (CLI:
`gamestudio doc brief <project> <shelf>`). It resolves the section from what
you call it ("interface", "UI", "Mechanics", "Buildings"), surveys the game
folder without running anything, and returns the map, the existing cards and
the procedure. The project is the one whose folder is the current directory,
otherwise the briefing's active project.

## What is authoritative

| For… | The source | What to do with it |
| --- | --- | --- |
| What the user wants | The section's cards | Keep them: nothing is erased |
| What the game says about itself | Its docs (GDD, README, `docs/`) | Cite them by path, do not copy them |
| What is done | The code: scenes **and** scripts | Survey it, file by file |

A document that contradicts the code is not an error to fix silently: it is a
question for the user.

## The procedure

1. **Read the section**: every card, in full.
2. **Read the game docs** the map lists, in place.
3. **Survey the game**, starting from the map. Interface: each scene with an
   interface root and its script, what opens what, themes and fonts; an
   interface built in code is read in its scripts. Icons: one card per icon
   set (a consistent family, often a folder), its source size against its
   on-screen size, its stroke, its palette, what references it. Props: one
   card per component (a button, an arrow, a slider, a frame — its variants in
   the same card), how it is built (texture and 9-slice margins, StyleBox,
   scene), its states as read in the scene or the theme. Mechanics: autoloads,
   scripts by folder, code outside Godot (a server), data. Art direction: the
   section below. A world section: the entities the game contains.
4. **Compare**, element by element:
   - done and described → check, complete;
   - done, not described → a new card;
   - described, not done → keep it, write "planned — not in the game yet";
   - contradiction → write both versions and the question, report it.
5. **Write** one card per element (a screen, a system, an entity), with the
   section's template and following "Writing a card" below, closed by
   `## Game survey — <date>`. What the code does not say is written "to be
   specified".
6. **Report** in a devlog entry "Survey — <section>": cards created and
   changed, contradictions, what was set aside and why.

## Art direction

Art direction is not the game's genre: it is its style, what it gives off. The
`design/direction` section holds one card per topic:

| Card | Template | What to survey |
| --- | --- | --- |
| **Intent** | `mood` | The style beyond the genre; the feeling at launch, in play, at victory, at defeat — the screen of each moment (render) and what is heard there |
| **Palette** | `direction` | The colours of themes, scenes and scripts, and the **meaning** of each (action, danger, resource, rarity) |
| **Typography** | `direction` | Fonts, their weights, where each is used |
| **Light and atmosphere** | `direction` | Environment (background, sky, fog, glow, tonemap), lights, in scenes or built in code |
| **Shaders** | `direction` | Each shader: what it draws, its settings that matter, who uses it |
| **Characters**, **Buildings** | `direction` | The shared rules: silhouette, proportions, detail, readability at the game camera — individual entities stay in the world sections |
| **Places and levels** | `direction` | Composition, landmarks, scale, density; the playable layout stays with mechanics |
| **Signature** | `direction` | What you only see in this game |
| **References**, **To avoid** | `direction` | What is kept from elsewhere, what is refused, and why |

A card with no material in the game or the docs is not created: it goes to the
devlog as "to write". Colours are cited in hexadecimal, in `.mono`, with the
file that holds them; a colour with no recognisable role is a deviation, not a
rule.

**The intended feeling is the user's intent**: the code does not say it. The
agent writes what the game **gives off today**, from its renders, colours and
sounds, under an "Observed" bullet; the intended feeling is written "to be
specified", and the question goes to the devlog. A game doc that states it is
authoritative, cited by its path.

### Universe: shaders are shown, not described

Each shader in the game is already a specimen of the *Art direction ›
Universe* page (`lookdev`), rendered by Godot on the lookdev bench. The survey
does not make one card per shader: the **Shaders** card states the shared
rules, and the Universe shows each one. What falls to the agent:

- **A shader bound to its object** (water that reads its planet's mesh, a halo
  that reads its height map) comes out wrong on the generic shape. Write its
  setup: `lookdev_set(project, specimen, setup=…)`, a script whose
  `func build() -> Node3D` builds the object **the way the game does** (read
  the script that builds it, reuse its call), then look at it
  (`lookdev_look`). An object that cannot be built outside the game is
  reported, not faked.
- **No verdict**: what is in the game is kept; what is no longer wanted leaves
  it — at the user's request. A shader to rework is discussed
  (`lookdev_brief`); so is a whole section — its colors, its typography, a
  family of shaders (`lookdev_aspect_brief`).

## Writing a card

A card reads at a glance, by the user and by the agent who picks it up. These
rules apply to every section card, whether caught up with the game or
discussed with the user (`card_brief`):

- **The first line says it all**: one sentence, no heading — what the element
  is, and what it does for the player.
- **One section = one question**: Access, Content, Actions, States for a
  screen; Loop, Rules, Parameters, Requirements for a mechanic. The section's
  template gives them.
- **Bullets, one idea each**: the label in **bold**, an em dash, a few words.
- **Short**: no paragraph over two lines, no repetition from one section to
  another; an empty section disappears.
- **The image is not written into the text**: the render carries the card's
  name (`render_scene(name=<card>, folder=<section>)`), and the page shows it
  next to the text. What the image does not show (a missing server, a
  language) fits in one bullet of the survey.
- **Links between cards**: `[top bar](top-bar.md)`.
- **`## Game survey — <date>` closes the card**, for agents: **Files**,
  **Game docs**, **State** (done, partial, planned), **Gaps**, in short
  bullets. The page folds it.

```markdown
# Top bar

The player's resources and access to the Universe, at the top of the planet view.

## Access

- **Planet view** — always visible, at the top of the screen.

## Content

- **Counters** — ore, energy, food, population.
- **Universe** — a button, no chevron.

## Actions

- **Tap a counter** → opens its [dropdown menu](resources-dropdown.md).
- **Tap Universe** → opens the [Universe page](universe-page.md).

## States

- **Warehouse full** — the ore counter switches to alert.

## Game survey — 2026-10-05

- **Files** — `client/scenes/hud/top_bar.tscn`, `client/scripts/main.gd`.
- **Game docs** — `docs/GDD.md`, "Resources".
- **State** — done.
- **Gaps** — the GDD announces three counters, the game has four: to settle.
```

## Images

A card reads better with the image of what it describes. It comes from the
**engine**, not from a screenshot: `render_scene(project, scene, name=<card>,
folder=<section>)` has Godot draw the scene off screen (nothing is displayed),
at twice the game's resolution. `crop=true` crops to a panel or a bar; `setup`
(GDScript run on the scene) opens a page or fills a list; `locale` picks the
language. The card's page shows the render that carries its name next to the
text on its own: nothing to paste. The `markdown` line the tool returns is for
citing the image elsewhere — a devlog, another card.

`name` is the **card's name** and `folder` its section: the image is a brief
image, and the library stores it with its cards (`briefing/<section>/`), where
the Library page groups them and shows them large. A render without `folder`
stays a free image. Rendering again under the same name replaces the image,
and a card that cites the old path still finds it.

Look at the image (`view_asset`). A screen that waits for its server comes out
without its data: say so in a bullet of the survey, or offer the user to start
the server. A screenshot is only for a state that no setup reaches.

## What is not done

- Touching the game: it is read-only — no writes, no builds, no tests. Only
  `render_scene` runs the engine there, off screen, writing nothing.
- Describing development scenes (`dev/`, `test/`) as game.
- Generating anything: a world section gets cards, not concepts or meshes; the
  workbench stays with the user.
- Summarising the whole section in a single document: one card per element.

## Ongoing

An agent that changes the game — a screen, a rule, an entity — updates the card
concerned right away: its `Game survey` section, and its render. The survey
catches what escaped that rule; it does not replace it.

## Done when

- Each element of the map that belongs to the section has its card, or appears
  in the devlog as set aside, with its reason.
- Each card of a visible element has its render, under its name, looked at.
- Each card follows "Writing a card": one sentence first, bullets, the survey
  last.
- No sentence of the user's has disappeared.
- The devlog entry is written, and the contradictions in it await an answer.

Example: "update the interface section" on a mobile game whose Godot client is
in `client/`. The map lists the start screen, the Empire, Social and Tech
views, the HUD pieces and a settings panel built in code. The section had a
single card, "Main screen", which announced a galaxy map the game does not
show yet: it stays, marked "planned". Eight new cards, one contradiction (the
GDD puts the settings in the HUD, the code opens them full screen), one devlog
entry.

Dependencies: a project opened on the game folder (`open_project_folder`). No
spending. See `production-routing`.
