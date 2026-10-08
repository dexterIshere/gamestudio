# gamestudio — instructions for agents

This repository is a game-asset studio: from a world card to a rigged and
animated 3D entity (rigged by an agent), ready for Godot; the only 2D is
concepts. The full guide — rules, architecture, conventions, how to verify — is
`CLAUDE.md`; it is written for Claude Code and holds for every agent. The
procedure for each deliverable is in `.claude/skills/` (mirrored in
`.agents/skills/`).

## Before working

1. **Read `context/briefing.md`.** It is regenerated after every finished
   production and whenever a tab opens in the Chats window, and holds the real
   state: projects, characters and their status, running jobs, total cost,
   declared recipes, available skills, library paths. If it is missing,
   `gamestudio context show --write` rebuilds it.
2. **Read the user's notes** in `context/`, if present — personal and
   untracked: `identity.md` (what they make), `goals.md` (where they are
   heading), `preferences.md` (how they work).
3. **Start from the code map**, `context/codemap.md` (one line per file), and
   open the files it points to rather than re-exploring the repository.
4. **Read `CLAUDE.md`** before chaining several tools.

## Keeping context current

When a lasting decision is made — a goal reached, a new constraint, an
art-direction choice — write it down instead of leaving it in the conversation:
in `context/` if it holds for the whole studio (tool `studio_note_write`), in
the project's `.gamestudio/documents/` if it holds for one project (tools
`create_document` / `write_document`, page **Documents**). The next
conversation knows it because these files changed. `context/briefing.md` is
never edited: it is computed.

"Updating a section" of the studio (interface, icons, props, mechanics, art
direction, VFX, a world section) means bringing it in line with what the game
already contains: `shelf_update_brief(project, shelf)` returns the game's map
and the procedure (skill `game-survey`). The game is read, not modified.

## Non-negotiable

- Every expense (image, concept, mesh, LoRA) needs the user's explicit
  agreement in the conversation, then `confirm=true`. Free first
  (`mesh_providers` shows the local path).
- No diffusion animation: the rigging agent sets keyframes or writes procedural
  animation in Blender; no rigging tool is coded into the studio.
- A Godot scene counts as loadable only once headless Godot has validated it.
- An image is looked at (`view_asset`) before it is kept.
- A failing criterion goes back to the user instead of paying in a loop.
- What no tool exposes (deleting a document, an asset, a section) is the
  user's gesture: ask them.
