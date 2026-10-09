# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Views, under Interface** (`service/views.py`, MCP `game_views`): the
  game's main pages, the one it starts on first, then the screens and worlds
  its scripts open -- never a piece placed in another scene, nor a widget.
  Each is drawn by the game's engine once it has settled, kept until its
  scene changes, and named after the interface card describing it; it says
  where it is opened from.
- **The graphic style and the game type lead the art direction**: two
  sections at the head of the Universe, each written part by part -- the game
  type's gameplay style, setting and lore, the graphic style's look --, a card
  each in `design/direction` (from new templates), shown in its own tab and
  written in place, or asked of the agent. Each also has a board of
  influences (`service/influences.py`), a mood board: each influence -- a
  work, a game, a film, an artist, a movement, or a blend of them -- shows its
  images large, with what is kept from it and what is left; it is renamed and
  rewritten in place, and an image taken off it in a click. Each aspect's
  gallery holds every image generated for it, influence by influence or for
  the aspect as a whole. A part's card can be erased, to start again. MCP:
  `influence_board`, `influence_set`, `influence_propose`.
- **A discussion with an agent beside the board** (`service/direction_chat.py`):
  a messaging thread in the page, answered by Claude Code run headless by the
  studio in the game's folder, on the model chosen in the thread (Sonnet by
  default, Opus or Haiku), its answer streamed as it is written. Every message
  hands it both aspects' influences, with their images' paths, and the graphic
  style as written. It does what it is asked and only that: it writes the
  aspect's cards, never changes the board -- the influences are the user's --
  and proposes images only when asked, from what it sees in the influences'
  images; the graphic style it writes comes from those images too, the game's
  current look second. It never pays: an image proposal shows with its amount (adjustable
  model and count), and only the user's click pays it. A request the page
  suggests (a part's "Ask the agent", a first question) fills the composer,
  never goes on its own; while the agent answers, the send button stops it. A
  new discussion keeps the previous one.
- **Images generated from what the influences show** (`direction_chat.draft`):
  "Generate images", from the gallery or an influence, hands the chosen
  influences' images to a model in one short turn; it looks at them and
  writes the prompt from what it sees, and says what it kept. The prompt comes
  back as a proposal, editable, with its price -- paid or set aside.
- **The Universe is split by aspect**: the graphic style, the game type,
  Colors, Typography, Interface, Materials, Sky and Other shaders, each in its
  own section (the last one chosen is remembered). The colors section shows
  the share of each color in the game as a band, then the colors of each
  aspect -- interface, sky, elsewhere --, and beside them every place the
  chosen one is written (file, and the property, theme key, constant or
  shader setting carrying it); a transparent color shows over a checkerboard.
  A material's colors are shown in the Materials section, under its shaders.
- **The game's colors, by aspect and by place** (`service/colors.py`): every
  literal color of the scenes, scripts and resources, and now the
  `source_color` settings of the shaders. Its aspect follows what paints it:
  a Control, a theme or a canvas_item shader paints the interface, a 3D object
  or a spatial shader the materials, an environment or a sky shader the sky; a
  material takes its shader's aspect.
- **A discussion on a whole section of the Universe**: its colors, its
  typography or a family of shaders, not one element. The section's bubble
  writes a brief (`lookdev_aspect_brief`, MCP and API) with what the game
  holds for it and where each thing is written, then opens an agent on it.
- **The game's typography**, read without running anything and without a font
  library (`service/fonts.py`): each font file's own tables (family, style,
  weight, italic, glyph count), the Godot font resources (`FontVariation`,
  `SystemFont`: base, fallbacks, OpenType features as tags), the project's
  default font, the sizes its scenes, scripts and themes set, and its own
  texts as specimens. Each family is shown in its real faces, in an editable
  specimen text, with its type scale; a face nothing cites says so.

### Removed

- **The Universe's "Cards" button**: the graphic style's and the game type's
  cards are shown and written in their sections; the other art direction
  cards stay in `design/direction`, read by agents.

### Fixed

- **The Universe's background was blurry**: the game's sky was a JPEG stretched
  over the whole page height. It is now rendered losslessly (PNG, a new bench
  format) at the size of the first screen times the pixel density, and covers
  that screen only.
- **The page's font had no real weights**: one face was loaded, so every
  heading used a bold the browser made up. Every face of the game's default
  family is now loaded with its real weight and style.

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
  tunable, and seen on the whole screen of a phone, a tablet or a desktop --
  the shader's use placed there as the game's stretch settings place it,
  black bars included; "Save" (Ctrl+S)
  writes the tuned settings back into the game, in the shown material or as
  the shader's defaults.
- **Three interfaces over one service layer**: a Tauri desktop application, an
  MCP server for agents (Claude Code, Codex, Gemini…), and a CLI.
- **A bilingual interface**, English or French.
- **Every expense confirmed explicitly**: `confirm=true` for a tool or a route,
  a dialog stating the amount in the interface, `--confirm` on the CLI.
