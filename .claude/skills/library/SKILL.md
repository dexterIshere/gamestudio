---
name: library
description: Find the path of a produced asset, or rename it and move it elsewhere — within the protections. Deleting is the user's job.
---

# The library

`<folder>/.gamestudio/library/` is the readable mirror of everything produced:
one project per folder, projects never mixed, 2D and 3D never mixed. The worker
regenerates it after each production — **never write in it by hand.**

## Giving a path to someone else

Cite the library, never the store.
`<folder>/.gamestudio/library/icons/<sheet>/<name>.svg` is readable; the hash
`.gamestudio/assets/7c/e4/7ce4….svg` is not.

`library_tree(project, folder=…, depth=…, pattern=…)` walks the library like a
folder and returns the **absolute path** of each file. It resyncs the library
on the way, so these paths really exist. It is the tool to find a file to open
elsewhere, or to copy into a Godot project.

## Layout

`2d/<entity>/` (`concept.png`, `concepts/<batch>/`), `3d/<entity>/`
(`concept.png`, `<entity>.glb`, `<entity>-bare.glb` once an agent has rigged
it, `sprites/`), `generations/<date>_<prompt>_<batch>/` (images +
`batch.json`), `icons/<sheet>/` (split sheet + `sheet.json`),
`effects/<effect>/`, `renders/`, `briefing/<section>/`, `video/<batch>/`,
`style/`. An entity lives in `2d/` through its concept until it has a mesh,
then in `3d/`; its generated concepts stay under `2d/<entity>/concepts/`.
Everything that comes from the mesh is stored under 3D, sprites included.

Each sheet or batch folder carries a JSON saying where its content comes from.
Read it directly: `sheet.json` and `batch.json` answer "where does this file
come from" without opening the database. Paths are stable as long as the
element is neither renamed nor moved.

## Changing

`library_rename(asset_id, name)` and `library_move(asset_ids, project, sheet)`
go through `store/curation.py`. A piece of a sheet can be renamed and moved.
**An asset held by a character or a style pack is protected**: the operation
is refused, with the reason.

Never work around that refusal. It protects a production that could not be
redone identically — an asset detached then regenerated will not be the same.

**Deleting is the user's job**: no agent tool does it (irreversible, store
included). If a file must go, tell the user, with its path; they delete it from
the Library page, where a held asset is refused the same way.

Done when: after a rename or a move, `library_tree` shows the new location and
the neighbouring JSON is consistent.

Example: "where is the knight's sprite?" →
`library_tree(project, folder="3d/knight", depth=2)` gives the absolute path of
the `.glb` and of the sheets, to copy into the Godot project.

Dependencies: none. See `roster`, `sheet-split`, `sprites-from-mesh`.
