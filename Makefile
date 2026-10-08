.PHONY: install install-app install-rigtools link unlink \
        studio update-check update-build studio-dev studio-dev-fg desktop-entry \
        mcp serve api worker check

install:
	python -m venv .venv && .venv/bin/pip install -e '.[dev]' && touch $(VENV_STAMP)

# The venv follows pyproject.toml: a dependency added to the studio is installed
# at the next `make studio` -- so at the next start of the application. The
# Python code is installed editable: restarting the server is enough.
VENV_STAMP := .venv/.installed
$(VENV_STAMP): pyproject.toml
	.venv/bin/pip install -q -e '.[dev]' && touch $@

install-rigtools:
	.venv/bin/pip install -e '.[rigtools]'

# Makes `gamestudio` callable without a prefix. The venv launcher has an
# absolute shebang: a symbolic link is enough, no need to activate the venv.
link:
	@mkdir -p $(HOME)/.local/bin
	@ln -sf $(CURDIR)/.venv/bin/gamestudio $(HOME)/.local/bin/gamestudio
	@echo "linked: $(HOME)/.local/bin/gamestudio -> $(CURDIR)/.venv/bin/gamestudio"
	@command -v gamestudio >/dev/null || echo "WARNING: $(HOME)/.local/bin is not on the PATH"

unlink:
	@rm -f $(HOME)/.local/bin/gamestudio && echo "link removed"

# Front-end dependencies (Node); the Rust toolchain comes from rustup.
install-app:
	cd app && npm install

APP := app/src-tauri/target/release/gamestudio

# The application: Tauri shell + React front end. It starts the Python server
# itself on a free port and stops it when it quits -- no terminal to keep open,
# no process outliving the window. Detached from the shell, which returns at
# once; logs go to data/studio.log.
studio: $(APP) $(VENV_STAMP)
	@mkdir -p data
	@setsid $(APP) </dev/null >>data/studio.log 2>&1 &
	@echo "studio started in the background (logs: data/studio.log)"

# What the binary depends on: without these lists, `make studio` would keep
# launching the binary of the first build.
#
# Two stages, because they do not change together: the front end (`app/dist`,
# built by Vite) and the Rust shell, which embeds it (`frontendDist:
# ../dist`). A changed page rebuilds both -- it would not reach the window
# otherwise; a changed shell does not rebuild the front end.
FRONT_SRC := $(wildcard app/*.html app/package.json app/tsconfig.json app/vite.config.ts) \
             $(shell find app/src -type f 2>/dev/null)
SHELL_SRC := $(wildcard app/src-tauri/*.rs app/src-tauri/src/*.rs \
                          app/src-tauri/*.toml app/src-tauri/Cargo.lock \
                          app/src-tauri/*.json app/src-tauri/capabilities/*.json)

# The built front end. Vite empties `dist/` when it starts, so an interrupted
# build leaves no stamp and the next one starts over.
DIST := app/dist/.built

# Is the application up to date with its code? Answers `up-to-date`, or
# `rebuild` followed by what is stale (`venv`, `front`, `app`), without building
# anything. `front` always comes with `app`, which embeds it; `app` alone means
# only the shell changed. The shell asks this at every start (and then opens its
# update window instead of the other two), and the rail's "Update" button asks
# it again later: the rule is written only here.
update-check:
	@s=""; \
	$(MAKE) -s -q $(VENV_STAMP) || s="$$s venv"; \
	$(MAKE) -s -q $(DIST) || s="$$s front"; \
	$(MAKE) -s -q $(APP) || s="$$s app"; \
	if [ -z "$$s" ]; then echo up-to-date; else echo "rebuild$$s"; fi

# Rebuilds what is stale, without launching anything: the update window follows
# its output to move its gauge, and restarts once it is done.
update-build: $(VENV_STAMP) $(APP)

# The front end: `tsc` then Vite (`npm run build`), only if a page changed.
$(DIST): $(FRONT_SRC)
	@echo "building the interface..."
	cd app && npm run --silent build
	@touch $@

# Builds the application if it is missing or a source changed. The first build
# is long (Tauri and its dependencies); later ones only recompile what changed.
# The front end is already built by `$(DIST)`: `tauri build` does not redo it
# (empty `beforeBuildCommand`).
$(APP): $(SHELL_SRC) $(DIST)
	@echo "building the application..."
	cd app && npx --no-install tauri build --no-bundle --config '{"build":{"beforeBuildCommand":""}}'

# Development: Vite with hot reload, the window follows every edit. Detached
# like `studio`, so closing the terminal does not close the window. Build
# errors and Vite's logs go to data/studio-dev.log -- follow them live with
# `tail -f data/studio-dev.log`, or use `studio-dev-fg`.
studio-dev:
	@mkdir -p data
	@setsid $(MAKE) -s studio-dev-fg </dev/null >data/studio-dev.log 2>&1 &
	@echo "development mode started in the background (logs: data/studio-dev.log)"

# The same, attached: build errors scroll in the terminal and Ctrl+C stops
# everything. The form to prefer when debugging the shell.
studio-dev-fg:
	cd app && npx --no-install tauri dev

# Applications-menu entry: the studio starts like any other program, without a
# terminal -- including from the desktop launcher, which reads this folder.
#
# `Exec` targets the binary, not `make studio`: a launcher that started
# recompiling would make you wait minutes with nothing on screen. Hence a crash
# at startup is silent, since `eprintln!` goes nowhere; to see the logs, go
# through `make studio` (data/studio.log).
desktop-entry: $(APP)
	@mkdir -p $(HOME)/.local/share/applications \
	           $(HOME)/.local/share/icons/hicolor/512x512/apps
	@cp app/src-tauri/icons/icon.png \
	    $(HOME)/.local/share/icons/hicolor/512x512/apps/gamestudio.png
	@printf '%s\n' \
	  '[Desktop Entry]' \
	  'Type=Application' \
	  'Name=gamestudio' \
	  'Comment=Game asset studio' \
	  'Exec=$(CURDIR)/$(APP)' \
	  'Path=$(CURDIR)' \
	  'Icon=gamestudio' \
	  'Terminal=false' \
	  'Categories=Graphics;2DGraphics;3DGraphics;' \
	  'Keywords=game;asset;godot;rig;animation;character;studio;' \
	  'StartupWMClass=gamestudio' \
	  > $(HOME)/.local/share/applications/gamestudio.desktop
	@update-desktop-database $(HOME)/.local/share/applications 2>/dev/null || true
	@gtk-update-icon-cache -f -t $(HOME)/.local/share/icons/hicolor 2>/dev/null || true
	@echo "entry created: $(HOME)/.local/share/applications/gamestudio.desktop"

# MCP server over stdio (for an agent started by hand; Claude Code goes through
# .mcp.json on its own).
mcp:
	.venv/bin/gamestudio mcp

# The server alone, without a window: it is what the application talks to.
serve:
	.venv/bin/gamestudio serve

# The server alone, reloaded on every edit (the worker runs separately).
api:
	.venv/bin/gamestudio serve --no-worker --reload

# The worker alone, for a machine dedicated to rendering.
worker:
	.venv/bin/gamestudio worker

check:
	.venv/bin/ruff check src && .venv/bin/pytest -q
	# Skills: index current and mirror intact. Same rule as the lint -- a
	# drifting index would give an agent a wrong list.
	.venv/bin/gamestudio skills check
	# The code map, imported by CLAUDE.md: same rule as the skills index.
	.venv/bin/gamestudio context check
	# The front end: types, then no displayed text without its translation.
	# Without its dependencies the check fails -- it is never skipped silently.
	@if [ ! -d app/node_modules ]; then \
	  echo "front: dependencies missing -- run 'make install-app', then 'make check'" >&2; \
	  exit 1; \
	fi
	cd app && npx tsc --noEmit && echo "front: types ok" && node scripts/translations.mjs
