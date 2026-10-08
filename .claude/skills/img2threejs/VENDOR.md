# Vendored — provenance and upkeep

This folder is **third-party** material, kept as is. It is not maintained
here: fixing it in place would make the studio diverge from upstream, and an
update would become impossible to review.

- **Upstream**: `https://github.com/witnesstodark/mr-mak-workspace`,
  `.agents/skills/img2threejs`
- **Commit**: `de0f88b082309ed1c46461fbebb33c9d0c819908`
- **Licence**: Apache 2.0 (see `LICENSE`)
- **Removed when vendoring**: the `.github/`, `assets/` and `forge/tests/`
  folders and the `.gitignore`, `CHANGELOG.md`, `CLAUDE.md`,
  `CONTRIBUTING.md`, `LAB-FINDINGS.md`, `ROADMAP.md` files (see
  `THIRD_PARTY_NOTICES.md` at the studio root)

## Updating

The studio modifies no file in this folder. To take an upstream version: fetch
it separately, remove the three folders and six files above, replace the
content while keeping this `VENDOR.md`, and update the commit here and in
`THIRD_PARTY_NOTICES.md`.

## What the studio expects from this folder

- `forge/next.py` — the workbench entry point (state + next step);
- `forge/stage3_build/generate_threejs_factory.py` — model generation;
- the GLB export plugin, which writes `model.glb` and its provenance;
- `SKILL.md` — the procedure, read by the agent.

The studio calls nothing else directly: the rest is knowledge the agent
consults. The expected output is single: a `.glb`, imported by `import_mesh`.
