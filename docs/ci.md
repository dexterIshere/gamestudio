# CI

The workflow lives in [`.github/workflows/checks.yml`](../.github/workflows/checks.yml).
It runs on `ubuntu-latest`, on every `push` and every `pull_request` targeting
`main` or `master`, and can be re-run by hand (`workflow_dispatch`). One run per
branch: a push that follows another cancels the previous run.

**Nothing in CI calls Runware or Tripo.** No API key is needed and no expense
is possible: the tests and the smoke test run offline, and what an agent would
deliver is built on the spot.

## What the job checks

| Step | What it checks | Local equivalent |
|---|---|---|
| Lint (ruff) | `ruff check src` — style and errors, with the rules in `pyproject.toml` | `ruff check src` |
| Tests (pytest) | the whole suite, including the server-message catalogue (`app/src/locales/fr-server.json`) | `pytest -q` |
| Skills index | `.claude/skills/README.md` is current, the `.agents/skills/` mirror intact | `gamestudio skills check` |
| Code map | `context/codemap.md` matches the files' headers | `gamestudio context check` |
| Front-end types | `npx tsc --noEmit` in `app/` | same |
| Front-end translations | no displayed text outside `t()`, no phrase without its French translation | `node scripts/translations.mjs` in `app/` |
| 3D smoke test | simulated agent delivery (skeleton, parts) → inventory → one sheet per animation and per direction → Godot resources → every animation played by the engine | `python scripts/smoke_3d.py` |

The first six steps are those of `make check`, in the same order and on the
same folders. The `Makefile` is the reference: a step added on one side is
added on the other.

One difference: `make check` fails if the front-end dependencies are missing
(`make install-app`), while CI installs them itself (`npm ci`, from
`app/package-lock.json`).

The 3D smoke test stands in for the agent that rigs and animates: a Blender
fixture delivers a character (canonical skeleton, an `idle_loop` cycle and a
`wave` gesture) and a boneless machine whose wheel turns. It checks that a
render produces one sheet per animation under a single framing, and that Godot
loops the cycles and only them.

## Engines, and what is skipped without them

The smoke test needs external engines, absent from the runner image. The
workflow installs them — Godot 4.4 and Blender 5.2.1 — with a cache, and
**tolerates a failed install**: a failed download must not pass a regression
off as a network problem.

What to know before reading a green result:

- **Without Godot**, the smoke test writes the resources but does not have the
  engine validate them, and says so at step 5/5. It is a partial success: the
  writing is checked, the loading is not.
- **Without Blender**, the smoke test cannot run at all: the step is
  **skipped** with an explicit `::warning::` saying it proved nothing. It never
  counts as a success.

In other words, a green job without Blender means "the Python code and the
front end hold; the 3D chain was not run" — not "all is well".

CI covers neither game renders (`render_scene`), nor showcases, nor the lookdev
bench: they need Godot with a display (`gamescope` or `xvfb-run`) and a real
game project.

## Doing the same on your machine

```bash
make check                 # ruff + pytest + skills + code map + types + translations
python scripts/smoke_3d.py # 3D, requires Blender; Godot validates if present
```

The repository's venv is `.venv/` (`make install` creates it with the `dev`
extras); the front-end dependencies come from `make install-app`. The optional
local tools (`rigtools`, `vector`) are needed neither by CI nor by the smoke
test.

The tested Python version is **3.11**, the floor declared by
`requires-python`. `ruff` already targets `py311` (`target-version`), so no
newer syntax can slip into the code unnoticed. Node is 22 (Vite 8 requires
`^20.19` or `>=22.12`).

A test that fails locally but passes in CI, or the reverse, almost always comes
down to versions: CI installs the latest Python versions `pyproject.toml`
allows and the exact versions of `app/package-lock.json`, not those of your
environment.
