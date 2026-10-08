---
name: validation
description: Check the studio without spending a cent — lint, tests, and the 3D chain end to end (simulated agent delivery, sprites, headless Godot).
---

# Checking without network

Everything here is free and offline. Run it before any structural change to
the Godot export, the Blender scripts, sprite rendering or mesh import.

```bash
make check                   # ruff + pytest + skills index + code map + front types and translations
python scripts/smoke_3d.py   # simulated agent delivery -> sprites -> headless Godot
```

`make check` covers lint and unit tests, and refuses a stale skills index or
code map. The 3D smoke test goes further: it builds in Blender what an agent
would deliver (a character on the canonical skeleton with a cycle and a
gesture, a mill made of parts with a turning wheel), takes its inventory,
renders the sheets of every animation under one framing, exports, then has
Godot import and play each animation.

## The rule that matters

**A `.tscn` or a `.glb` is only known to load once headless Godot has
validated it.** An export written without error proves nothing: the scene may
carry a broken path, an animation track that resolves no node, or a cycle
imported without looping, and Godot only complains when it opens it.
`gamestudio validate-godot <folder> --scene res://…`
(`scripts/validate_godot_scene.gd`) is the only judge — it reads an animated
GLB as well as an effect scene.

## What validation does not tell

It checks structure, never the picture. A scene that loads can hold an
unreadable sprite, an arm through the torso, or a cycle that jumps at the seam.
That is for the eye — `view_asset` on the sheets — and it cannot be delegated
to a test. "The tests pass" and "the result is good" are two different claims:
never present the first as the second.

Done when: `make check` is green, the 3D smoke test runs without error, and the
images have actually been looked at.

Example: after touching sprite rendering, run both commands. If the smoke test
passes but the character hops when it goes from idle to walk, dig into the
framing shared by the animations, not into the test.

Dependencies: an installed venv (`make install`), Blender and Godot on the PATH
for the smoke test. See `production-routing` for the cross-cutting rules.
