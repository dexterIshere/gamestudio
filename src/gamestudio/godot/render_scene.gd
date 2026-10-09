## Render a scene of the game to PNG, by the engine itself, off screen.
##
## Run by the studio (`service/renders.py`):
##   godot --path <project> --script <this file> -- --gs-scene=res://... \
##     --gs-out=/path.png [--gs-scale=2] [--gs-width=720 --gs-height=1280] \
##     [--gs-delay=0.5] [--gs-crop] [--gs-transparent] [--gs-locale=fr] \
##     [--gs-prepare=/path/prepare.gd] [--gs-setup=/path/setup.gd]
##     [--gs-nodes=/path/nodes.json]
##
## The scene is instantiated in a SubViewport at the game's size, rendered at
## `scale` times its resolution: a 720 x 1280 interface comes out sharp at
## 1440 x 2560. The project's autoloads are there, as in the game. The script
## touches no file of the project: it only writes the requested image.
##
## `--gs-prepare` runs before the scene exists (its `scene` is null): the
## project's preview data points the game's autoloads at a fake server there,
## before the screen asks it anything (`service/preview_data.py`). `--gs-setup`
## runs once the scene is in the tree.
##
## With `--gs-nodes`, it also lists the visible Controls: their path, their
## type, their rectangle in image pixels, and the scene that declares them --
## enough to point at them on the image and find where to write their
## properties (`service/screens.py`).
##
## It prints `GAMESTUDIO_RENDER: START` as soon as it runs, and always ends with
## `GAMESTUDIO_RENDER: OK <w>x<h>` or `GAMESTUDIO_RENDER: FAILED <reason>`: these
## are the lines the studio reads. Without START, the display failed, not the
## scene.
extends SceneTree

const PREFIX := "--gs-"
# Beyond this, the scene is waiting for something that will not come (a
# server, an input): say so, instead of letting the studio wait.
const WATCHDOG := 45.0

var _done := false
# Framing of a lone component, shared with the showcase (`ui_frame.gd`).
var _frame = load(get_script().resource_path.get_base_dir().path_join("ui_frame.gd"))


func _initialize() -> void:
	_run()


func _args() -> Dictionary:
	var found := {}
	for raw in OS.get_cmdline_user_args():
		if not raw.begins_with(PREFIX):
			continue
		var parts := raw.substr(PREFIX.length()).split("=", true, 1)
		found[parts[0]] = parts[1] if parts.size() > 1 else "true"
	return found


func _fail(reason: String) -> void:
	if _done:
		return
	_done = true
	print("GAMESTUDIO_RENDER: FAILED ", reason)
	quit(1)


## Run a setup script's `setup(scene)`; false (and the render failed) if it
## cannot run.
func _helper(path: String, scene: Node) -> bool:
	var helper_script = load(path)
	if helper_script == null:
		_fail("unreadable setup: " + path)
		return false
	# A setup written for another version of the game (a function that changed
	# signature, an autoload that moved) does not compile.
	if not helper_script.can_instantiate():
		_fail("the render setup does not compile against this version of the game")
		return false
	await helper_script.new().setup(scene)
	return true


func _run() -> void:
	print("GAMESTUDIO_RENDER: START")
	var args := _args()
	var scene_path: String = args.get("scene", "")
	var output: String = args.get("out", "")
	if scene_path.is_empty() or output.is_empty():
		_fail("missing scene or output")
		return
	if not ResourceLoader.exists(scene_path):
		_fail("scene not found: " + scene_path)
		return

	# The game's size, unless another is requested: it is what the interface's
	# anchors see.
	var base := Vector2i(int(args.get("width", "0")), int(args.get("height", "0")))
	if base.x <= 0 or base.y <= 0:
		base = Vector2i(
			int(ProjectSettings.get_setting("display/window/size/viewport_width", 1152)),
			int(ProjectSettings.get_setting("display/window/size/viewport_height", 648)))
	var scale := maxf(float(args.get("scale", "2")), 0.25)
	var delay := float(args.get("delay", "0.5"))
	create_timer(delay + WATCHDOG).timeout.connect(
		_fail.bind("the scene did not render within %d s: it is probably waiting for a server "
			% int(delay + WATCHDOG) + "or an input"))
	if args.has("locale"):
		TranslationServer.set_locale(args["locale"])

	var view := SubViewport.new()
	view.size = Vector2i(roundi(base.x * scale), roundi(base.y * scale))
	view.size_2d_override = base
	view.size_2d_override_stretch = true
	view.transparent_bg = args.has("transparent")
	view.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	root.add_child(view)

	var packed = load(scene_path)
	if not packed is PackedScene:
		_fail("not a scene: " + scene_path)
		return
	if args.has("prepare") and not await _helper(args["prepare"], null):
		return
	var scene: Node = packed.instantiate()
	view.add_child(scene)

	# The setup the agent wrote: open a page, fill a list.
	if args.has("setup") and not await _helper(args["setup"], scene):
		return

	# Time for the interface to settle and its intro animations to finish.
	if delay > 0.0:
		await create_timer(delay).timeout
	for i in 3:
		await process_frame
	# A component (a button, a list row) is meant to be sized by its container:
	# alone, it may have no width. It gets the one its children ask for -- once
	# their minimum sizes are computed -- otherwise the crop would show nothing.
	if args.has("crop") and scene is Control and _frame.fit(scene as Control):
		for i in 2:
			await process_frame
	# Drawing is forced rather than awaited: a virtual display may never present
	# the window, and the end of a frame would never come.
	RenderingServer.force_draw(false)
	if _done:
		return

	var image := view.get_texture().get_image()
	if image == null or image.is_empty():
		_fail("empty image: the engine rendered nothing")
		return
	var offset := Vector2.ZERO
	if args.has("crop") and scene is Control:
		var rect: Rect2 = _frame.bounds(scene, (scene as Control).get_global_rect())
		var region := Rect2i(Vector2i((rect.position * scale).floor()),
			Vector2i((rect.size * scale).ceil()))
		region = region.intersection(Rect2i(Vector2i.ZERO, image.get_size()))
		if region.size.x > 0 and region.size.y > 0:
			image = image.get_region(region)
			offset = Vector2(region.position)
	if image.save_png(output) != OK:
		_fail("cannot write: " + output)
		return
	if args.has("nodes"):
		var found := []
		_walk(scene, scene, scale, offset, found)
		var file := FileAccess.open(args["nodes"], FileAccess.WRITE)
		if file == null:
			_fail("cannot write: " + str(args["nodes"]))
			return
		file.store_string(JSON.stringify(found))
		file.close()
	_done = true
	print("GAMESTUDIO_RENDER: OK %dx%d" % [image.get_width(), image.get_height()])
	quit(0)


## The visible Controls, in tree order (a child after its parent: the last one
## containing a point is the one on top).
##
## `file` and `local` say where to write: the scene that declares the node, and
## its path in that scene. A node created by code is declared nowhere (empty
## `file`); a node of an instanced scene is declared in its own.
func _walk(node: Node, scene: Node, scale: float, offset: Vector2, out: Array) -> void:
	if node is CanvasItem and not (node as CanvasItem).visible:
		return
	if node is Control:
		var control := node as Control
		var rect := control.get_global_rect()
		var file := ""
		var local := ""
		if control == scene:
			file = scene.scene_file_path
			local = "."
		elif control.owner != null:
			file = control.owner.scene_file_path
			local = str(control.owner.get_path_to(control))
		var text = control.get("text")
		out.append({
			"path": "." if control == scene else str(scene.get_path_to(control)),
			"name": str(control.name),
			"type": control.get_class(),
			"rect": [rect.position.x * scale - offset.x, rect.position.y * scale - offset.y,
				rect.size.x * scale, rect.size.y * scale],
			"file": file,
			"local": local,
			"instance": control != scene and not control.scene_file_path.is_empty(),
			"text": text if text is String else "",
		})
	for child in node.get_children():
		_walk(child, scene, scale, offset, out)

