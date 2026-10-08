"""Game asset generation studio."""

# The studio version, and its single source: `pyproject.toml` reads it here, so
# do the API and the MCP server. The three surfaces that cannot import Python --
# `app/package.json`, `app/src-tauri/tauri.conf.json` and
# `app/src-tauri/Cargo.toml` -- copy it, and a test checks that they agree.
__version__ = "0.3.0"
