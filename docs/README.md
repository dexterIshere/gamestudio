# Documentation

This folder does not repeat what exists elsewhere: it points to the source.

| Document | Content |
|---|---|
| [`../README.md`](../README.md) | The studio: what it does and does not do, requirements, installation, usage, known limitations, costs, licence. [`../README.fr.md`](../README.fr.md) is its French translation. |
| [`getting-started.md`](getting-started.md) | Getting started: in what order to use the studio, and where things land. |
| [`recipe.md`](recipe.md) | A project's recipe (`.gamestudio/recipe.yaml`): a full example and every field. |
| [`ci.md`](ci.md) | What CI checks, and how to run the same on your machine. |
| [`../CLAUDE.md`](../CLAUDE.md) | Working in the repository: rules, architecture, driving through the MCP server, code and front-end conventions. |
| [`../AGENTS.md`](../AGENTS.md) | The same entry point for agents other than Claude Code. |
| [`../context/codemap.md`](../context/codemap.md) | The code map: one file per line, with what it does. Generated. |
| [`../.claude/skills/`](../.claude/skills/) | The studio's procedures, one per deliverable. The entry point is `production-routing`. |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Contributing: installation, checks, conventions, translation, licence. |
| [`../SECURITY.md`](../SECURITY.md) | What the server exposes, where keys live, how to report a vulnerability. |
| [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) | Third-party material and its licence. |
| [`../CHANGELOG.md`](../CHANGELOG.md) | Release history. |

To check something **without spending anything**:

```bash
make check                   # ruff + pytest + skills + code map + types + translations
python scripts/smoke_3d.py   # simulated agent delivery → sprites → headless Godot
```

Neither calls Runware or Tripo.
