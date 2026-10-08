---
name: local-3d
description: Produce a 3D mesh without paying — build the GLB with img2threejs from a reference image, import it into the studio, then derive 2D sprites or an export from it.
---

# 3D without spending

The studio can buy a mesh (Tripo, Hunyuan 3D, TRELLIS through Runware, or
Tripo P1, P2 and H through the direct Tripo API: $0.15 to $1.25). It can also
**build one locally**, for free, with `img2threejs` — a workbench vendored in
`.claude/skills/img2threejs/`, under Apache 2.0 (see `THIRD_PARTY_NOTICES.md`).

This skill gives the order and the finish line. The img2threejs procedure
itself is `.claude/skills/img2threejs/SKILL.md`: it is authoritative, and long.
Here, only the connection to the studio.

## When to take this path

- The budget matters, or the user said "free": **it is the only free path to a
  mesh**.
- The subject is geometric and readable: an object, a creature with simple
  volumes, architecture. img2threejs builds volumes; it does not sculpt fine
  organic detail.
- You want to iterate: each attempt costs machine time, not money.

Take a paid path when the topology must be clean on the first try (Tripo), the
subject is organic and complex (Hunyuan Pro), or time is short — with the
user's agreement on the amount. `mesh_providers` lists the choices and their
prices.

## The chain

1. **A clean reference image.** Transparent background, whole subject, a single
   view. Same requirement as for 2D: an opaque image gets matted
   (`import_image(matting=true)`), a blurry one cannot be saved.
2. **Build.** Follow `.claude/skills/img2threejs/SKILL.md` from
   `.claude/skills/img2threejs/`. The expected output is a `model.glb` (export
   plugin, `--target glb`) — and **nothing else matters here**: the rest
   (showing the model, comparing it to the reference) serves img2threejs, not
   the studio.
3. **Import it**: `import_mesh(path=…, project=…, name=<entity>)` — free,
   local. The mesh becomes `3d/<entity>/<entity>.glb` in the project library,
   and the character is created if it does not exist.
4. **Look at it, really.** `view_asset` does not show a mesh: open
   `3d/<entity>/<entity>.glb` (the window's 3D viewport), or use
   `render_sprites` to judge its silhouette from several angles. A mesh nobody
   looked at is not a delivered mesh.
5. **Derive something from it**: `render_sprites(mesh_asset_id=…)` for 2D
   sheets (`normal`, `prerender`, `pixel`); the GLB is copied into the Godot
   project when the folder has a `project.godot`.

## What is done, and what is not

Done: the mesh is in the library, it has been **looked at**, and its silhouette
holds from at least two angles.

Not done, and that is expected: `import_mesh` leaves the entity **awaiting
review**. The studio records what the file carries (bones, animations), but
nobody has looked at it yet. A bare mesh that must *move* gets its rig and
animations from an agent (skill `animation`); an object or a prop rendered as
static sprites does fine without.

## The trap

img2threejs is verbose and produces quality reports. **A green img2threejs
report does not prove the mesh is good**: it is another criterion, on another
object. The mesh is judged by looking at it, like everything else here.

Dependencies: `.claude/skills/img2threejs/` (vendored, Apache 2.0). No network
access, no key: this path cannot spend.
