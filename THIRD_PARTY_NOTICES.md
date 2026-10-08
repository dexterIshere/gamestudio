# Third-party notices

The studio is under **PolyForm Shield 1.0.0** (`LICENSE`), except what is
listed here: each entry keeps its own licence, which prevails over the
studio's for those files. Each entry says where the material comes from, under
which licence, and what was removed along the way. The full licences are in
the folder concerned, or below.

The dependencies installed by `pip` and `npm` (see `pyproject.toml` and
`app/package.json`) are not in the repository: they keep their licence, and the
README lists the models some of them download at runtime.

## img2threejs — `.claude/skills/img2threejs/`

- **Author**: hoainho (`https://github.com/hoainho/img2threejs`),
  "Copyright 2026 hoainho".
- **Origin**: `https://github.com/witnesstodark/mr-mak-workspace`,
  folder `.agents/skills/img2threejs`, commit
  `de0f88b082309ed1c46461fbebb33c9d0c819908` (branch `main`).
- **Licence**: Apache License 2.0 — full text in
  `.claude/skills/img2threejs/LICENSE`.
- **What it is**: a procedural modelling workbench. From a reference image, it
  leads an agent step by step (survey, specification, construction, review,
  rig) to a mesh — emitted as `model.glb` by its export plugin. **No network
  call, no key**: it is the studio's local, free 3D path.
- **What was removed**: the folders `.github/` (upstream CI and contribution
  templates), `assets/` (logo and sponsor logotypes) and `forge/tests/` (the
  upstream tests, which exercise the workbench itself rather than its use
  here); the files `.gitignore`, `CHANGELOG.md`, `CONTRIBUTING.md`,
  `LAB-FINDINGS.md` and `ROADMAP.md` (the upstream repository's life); and
  `CLAUDE.md` (instructions meant for the upstream repository, which would
  describe this folder as its canonical source — untrue here — and which agents
  read automatically). Nothing that `SKILL.md`, `forge/next.py` or the scripts
  depend on was removed.
- **Added**: `VENDOR.md` (provenance and upkeep), the studio's own.
- **Modifications**: none to the kept files. No file was edited, only removed;
  the upstream code therefore stays recognisable.
- **How the studio uses it**: see `.claude/skills/local-3d/SKILL.md`. The
  output (`model.glb`) enters the studio through `import_mesh`, and can then be
  stored in the library and rendered into 2D sprite sheets.

## Effects and visual review — `.claude/skills/vfx/`, `.claude/skills/visual-review/`

- **Origin**: `https://github.com/witnesstodark/mr-mak-workspace`, skills
  `game-vfx-workflow` (with `references/implementation.md`) and
  `gameplay-visual-review`, commit `d5ebf2d2d694bd39ae00965f3eb21349b54ba3ff`
  (branch `main`).
- **Licence**: MIT, reproduced below.
- **Rules taken from img2threejs**: a few rules from img2threejs's
  `docs/standard-prompts/vfx.md` prompt (event timing, hitstop, impact
  vocabulary) are also used there, reworded. That prompt is under the
  **Apache License 2.0** ("Copyright 2026 hoainho", full text in
  `.claude/skills/img2threejs/LICENSE`), which covers those passages.
- **What changed**: this is not vendored material but an **adaptation**,
  rewritten for the studio: anchoring on a bone of the canonical skeleton or on
  a part, timing read from the entity's card (written by the agent that
  animated it), Godot integration without touching the files the studio copies,
  native capture through Godot's Movie Maker then `video_frames`. These two
  skills are therefore maintained here.

```text
MIT License

Copyright (c) 2026 Mr. Mak Workspace contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Agent logos — `app/src/chat/logos.tsx`

- **Origin**: the SVG paths come from LobeHub lobe-icons
  (`https://github.com/lobehub/lobe-icons`).
- **Licence**: MIT, "Copyright (c) 2023 LobeHub", reproduced below.
- **Trademarks**: the logos (Claude, Codex, Kimi, DeepSeek, MiMo) are
  trademarks of their respective owners. The MIT licence covers the SVG files,
  not the trademarks: the studio uses them only to designate the agent a tab
  starts, and claims no affiliation with their owners.

```text
MIT License

Copyright (c) 2023 LobeHub

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Scripts run inside Blender — `src/gamestudio/blender/bl_*.py`

- **Licence**: GNU GPL 3.0 or later — full text in
  `src/gamestudio/blender/LICENSE`, and a `SPDX-License-Identifier` at the top
  of each file.
- **Why**: these scripts run under Blender's interpreter and import `bpy`.
  Blender is under the GPL, and the Blender Foundation considers code that uses
  its Python API a derivative work: it must be distributed under a
  GPL-compatible licence, which PolyForm Shield is not. The rest of the studio
  only starts Blender as a separate process (`blender/run.py`) and stays under
  the studio's licence.

## Outfit and Prompt fonts — `app/src/fonts/`

- **Outfit**: "Copyright 2021 The Outfit Project Authors"
  (`https://github.com/Outfitio/Outfit-Fonts`).
- **Prompt**: "Copyright (c) 2015, Cadson Demak"
  (`https://github.com/cadsondemak/prompt`).
- **Licence**: SIL Open Font License 1.1 — full text in
  `app/src/fonts/OFL.txt`, which accompanies the redistributed `.woff2` files.

## Optional MCP companions — not bundled

The repository declares two third-party MCP servers next to the studio
(`.mcp.json`, `.codex/config.toml`, `.gemini/settings.json`), through the
launchers `scripts/mcp/blender` and `scripts/mcp/godot`. **Their code is not in
the repository**: users install them themselves, if they want them, and they
keep their licence. The studio works without them.

- **blender-mcp** — `https://github.com/ahujasid/blender-mcp`, MIT,
  "Copyright (c) 2025 Siddharth Ahuja".
- **godot-mcp** — `https://github.com/Coding-Solo/godot-mcp`, MIT,
  "Copyright (c) 2025 Solomon Elias".

The rest of the repository is original and covered by the studio's licence (see
`LICENSE`).
