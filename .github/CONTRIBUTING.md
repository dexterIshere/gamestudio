# Contributing

## Installing from scratch

The requirements (Linux, Python ≥ 3.11, Node ≥ 20.19, Rust, Blender, Godot…)
are listed in the [README](../README.md#requirements).

```bash
git clone <the repository> && cd gamestudio
make install           # .venv + Python dependencies (dev extras)
make install-app       # front-end dependencies (npm)
cp .env.example .env   # no key is needed for the tests
make check
```

`make check` is what CI runs: ruff, pytest, the skills index, the code map, the
front-end types and its translations. It calls no service and spends nothing.
A change to the Godot export, the Blender scripts, sprite rendering or mesh
import also goes through `python scripts/smoke_3d.py` (Blender required, Godot
for the validation).

## Conventions

They are in [`CLAUDE.md`](../CLAUDE.md). The essentials:

- **An operation is defined once, in `src/gamestudio/service/`**; the API, the
  CLI and the MCP server only translate it. A route added to the API gets its
  MCP tool, or a justified entry in `mcp_server.HUMAN_ONLY`
  (`tests/test_agent_parity.py` checks it).
- Everything is in English — code, comments, messages, docs. Comments say what
  and why, concisely. `ruff` and `pytest` pass.
- Each file opens with one sentence saying what it is for; an added or moved
  file needs `gamestudio context codemap`. A changed skill needs
  `gamestudio skills index`. `make check` rejects a stale code map or index.
- Every paid operation requires explicit confirmation. Tests never call the
  network.
- A script that imports `bpy` (`src/gamestudio/blender/bl_*.py`) is
  GPL-3.0-or-later and opens with `# SPDX-License-Identifier: GPL-3.0-or-later`.
- No machine path, no key, no private game name in a tracked file.
- The front-end design system lives in `app/src/styles.css`, the components in
  `app/src/components/ui.tsx`; no hard-coded colour, no explanatory text on
  screen.

## Translation

The interface speaks English and French. Every displayed phrase goes through
`t("…")` (`tn()` for a plural, `tc()` with a context) and has its French in
`app/src/locales/fr.ts`; a text from the server goes through `tr()`, with its
template in `app/src/locales/fr-server.json`. `make check` rejects hard-coded
text and a phrase without its translation. `README.md` and its French
translation `docs/README.fr.md` change together.

## Licence of contributions

A contribution is published under the repository's licence: PolyForm Shield
1.0.0 (`LICENSE`), or GPL 3.0 or later for the scripts run inside Blender.
Added third-party material keeps its licence and is listed in
`docs/THIRD_PARTY_NOTICES.md`. A contributor agreement may be requested before a
large pull request is accepted.
