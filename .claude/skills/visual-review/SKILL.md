---
name: visual-review
description: Check a visual change in Godot — effect, animation, scene — with comparable native captures (before/after, OFF/ON), targeted behaviour checks and a readable record.
---

# Native visual review

Read the requested change and its accepted reference. Choose a short scenario
that can **reveal** the difference. Preserve the current scene and whatever is
not concerned: look at the editor's state before taking it over, and do not
restart an active tool just for a capture.

## A fair comparison

The game's camera, lighting and materials, at normal speed. Same subjects, same
action, same framing, same seed for OFF/ON or before/after; note any
unavoidable difference. A slow-motion close-up is diagnostic evidence; it does
not replace the normal game zoom.

The review bench isolates the feature **while keeping its real code path**:
never "fix" a comparison with a demo implementation. It lives in its own scene,
which instances the studio's — the studio's `.tscn` files are not edited
(`blender-godot-bridge`). Simulated input stays separate from real devices, and
nothing transient is saved into the scene.

Native capture is Godot's Movie Maker, deterministic at a fixed frame rate:

```bash
godot --path <project> --write-movie <scratchpad>/on.avi --fixed-fps 30 --quit-after 90 <scene>.tscn
```

It needs a real renderer: **not `--headless`**, which draws nothing. The same
call with the effect off gives `off.avi`. `video_frames` then extracts the
frames and a contact sheet, to look at with `view_asset`.

## Appearance and behaviour, separately

Animation: contacts, seams, interrupted transitions, responsiveness. Effect:
contrast, overlap, attachment, cleanup. Interface: readability, overflow, input
blocking, real state. Scenery: scale, grounding, traversal, game camera.

A counter, a hash or a recorded value prove wiring or unchanged inputs — **not
visual quality**. A still capture does not prove a timed transition; a browser
study does not prove a native render cost. `gamestudio validate-godot` says a
scene loads, not that it looks good. Measure only the system concerned.

## Leaving a useful record

Note the source revision, the configuration, the exact scenario, the capture
type, the observations and what remains. Label clearly: generated art,
code-drawn study, isolated Godot preview, native game. A successful agent check
**is not** acceptance by the user. The record goes into the project documents
(`write_document`); the current specification is updated when behaviour
changes.

Short loops or videos for motion, stills for detail. Keep the original capture
and a light review copy, with their provenance. Do not recompress what is
already compressed.

## Done when

A visible comparison under the same conditions, the behaviour cases checked
(cancellation and cleanup for an effect), the record written, and aesthetic
acceptance **explicitly left** to the user. A capture that does not show the
expected difference is reported instead of being redone in a loop.

Example: a shield impact OFF/ON on dark ground and pale ground, same action,
same camera. Deliver the comparison, plus the cancellation and cleanup results;
acceptance stays with the user.

Dependencies: Godot 4 with a renderer (not headless), ffmpeg for
`video_frames`. Adapted from `gameplay-visual-review` (mr-mak-workspace, MIT —
see `THIRD_PARTY_NOTICES.md`). See `vfx`, `animation`, `blender-godot-bridge`,
`validation`.
