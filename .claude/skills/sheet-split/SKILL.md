---
name: sheet-split
description: Split a multi-element sheet (SVG icon sheet, PNG pack, sprite sheet) into one file per element, with a free preview before import.
---

# Splitting a sheet

A sheet is a file that holds several. The studio turns it into one standalone
asset per element, stored in `<folder>/.gamestudio/library/icons/<sheet>/`.

Free and local — no network call, no cost. Iterate as much as needed.

## The loop

1. `inspect_sheet(path, strategy=…, gap=…, rows=…, columns=…)` — preview of
   what would be extracted: name and box of each element, plus the strategy
   used. **Writes nothing.**
2. Fix the count if it is wrong. It is the only setting that matters:
   - `gap` — maximum distance between two pieces of the same element. Raising
     it rejoins a drawing that fell apart (an icon with detached strokes);
     lowering it separates two drawings that touch.
   - `rows` / `columns` — force a grid when cells touch.
3. `import_sheet(path, project=…, multi=true, keep=…, raster_size=…)`.

`strategy` defaults to `auto`. For SVG: `symbols | groups | clusters | single`.
For a bitmap: `grid | blobs | single`. Only touch it if `auto` gets it wrong,
and even then check first whether `gap` is enough.

`keep` retains only the element names announced by `inspect_sheet` — the right
tool to extract thirty icons from a sheet of forty, without importing them all
and deleting the rest.

## Matting

An opaque PNG sheet is matted by the local model (BiRefNet, a few tens of
seconds, free). `matting=false` falls back to background-colour keying:
instant, but rough on a textured background. On a flat background it is the
right choice; on a photographic one, keep the model.

## SVG or PNG

A frame grid is never cropped cell by cell: the pivot must stay fixed from
frame to frame, or the character hops. `raster_size` also produces one PNG per
SVG icon; use it only if the target engine cannot read SVG — SVG is lighter and
goes into Godot as is.

Done when: the element count matches what the sheet shows, and no element is
cropped. The folder holds a `sheet.json` giving each element's origin and size
(`width`/`height`: pixels for a bitmap, user units for SVG) — enough to place
it in an engine without opening the file.

Example: a pack of 40 SVG icons. `inspect_sheet` announces 46 — six detached
strokes count as icons of their own. Raise `gap`, get back to 40, import.

Dependencies: `pip install -e .[vector]`, only for `raster_size`. See
`library` to find the produced files.
