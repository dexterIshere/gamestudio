---
name: roster
description: Write or edit a project's recipe and build a batch of 3D entities — style, pipelines, archetype, face budget, sprite settings.
---

# Building a roster

A recipe ("what we want", not "how to make it") is the studio's unit of
re-execution: "about twenty villagers in this style" fits in
`.gamestudio/recipe.yaml`. For each entity it produces an A-pose reference and
a **bare mesh** copied into the Godot project. Rig and animations are not part
of it: they are an agent's work (skill `animation`).

## What is set where

- `style:` — the art direction. See `art-direction` before touching the
  roster: an unlocked style makes the batch inconsistent.
- `defaults:` — what each entity inherits: `archetype`, `pipelines`,
  `directions`, `sprite_size`, `sprite_style`, `sprite_palette`, `face_limit`.
- `characters:` — the roster: `id`, `name`, `subject` (the description that
  feeds the prompt), and per entity whatever departs from the defaults.

`pipelines` accepts `mesh3d` (reference → bare mesh → Godot copy) and `sprites`
(the entity will end up as sheets). `build` produces the same for both: the
reference and the bare mesh. The `directions` and `sprite_*` settings describe
the wanted sprites, but **no render reads them by itself**: once the entity is
animated, pass them to `render_sprites` (skill `sprites-from-mesh`). An unknown
value is ignored on read. `archetype` accepts `biped`, `quadruped`,
`winged_biped`, `serpentine`, `prop`, `custom`; it tells the rigging agent
which skeleton convention applies. All fields: `docs/recipe.md`. `face_limit`
(500 to 50,000) caps the mesh: low for a mobile game or a crowd, high for a
hero seen up close.

## Build

1. `read_recipe` for the current state; `studio_health` if the environment is
   in doubt.
2. Write or fix the recipe. Subjects are descriptive and concrete: "a young
   knight in worn leather, short red hair, a short sword".
3. `enqueue_build(recipe_path, characters=None, force=False, confirm=true)` —
   **paid, about $0.40 per entity** (one image, then the mesh). `characters`
   narrows the batch; without it, the whole roster goes.
4. Follow with `queue_status` and `job_detail`. Steps already computed come
   from the cache: relaunching does not pay again, unless `force=true`.
5. `character_detail(project, character_id)` tells what was produced;
   `view_asset` shows the reference, `render_sprites(mesh_asset_id,
   directions=4)` the mesh from four angles, for free.
6. Each entity that must move goes to an agent: from its card's workbench
   (`entity_animation_brief`) when it has one, otherwise by giving the agent
   the `animation` skill and the entity id — delivery goes through
   `import_mesh(path, project, name=<id>, replace=true)`.

`force=true` recomputes everything, including what was fine. Do not use it to
"retry": find the step that failed and fix it.

Done when: every entity in the batch has its reference and its mesh, looked
at, and the entities look alike. One successful entity does not make a
consistent roster — the batch is what gets judged.

Example: a village of ten NPCs. A locked style, `pipelines: [mesh3d, sprites]`,
`face_limit: 4000`, `directions: 8`. Build **two** entities first, look at their
references and meshes, fix the style or the subjects that drift, and only then
spend on the rest.

Dependencies: a valid recipe, a Runware key, the user's agreement. See
`art-direction`, `animation`, `sprites-from-mesh`.
