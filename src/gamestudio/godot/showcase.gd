## The props showcase: a batch of the game's components, each drawn alone, in each of its states.
##
## Run by the studio (`service/showcase.py`):
##   godot --path <project> --script <this file> -- --gs-list=/path/batch.json \
##     [--gs-scale=2] [--gs-width=720 --gs-height=1280]
##
## `batch.json` is a list of items:
##   {"key": "...", "scene": "res://…", "state": "hover", "out": "/….png"}
##   {"key": "...", "stylebox": "res://…", "size": [160, 56], "out": "/….png"}
##
## A component is instantiated alone, at the size its children ask for if it
## has none (`ui_frame.fit`), in the requested state (`ui_frame.show_state`),
## then cropped to what it draws, on a transparent background. A StyleBox is
## put on a panel of the given size. One engine for the whole batch: starting
## Godot costs more than all the drawings together.
##
## Each item reports `GAMESTUDIO_SHOWCASE: <key> OK <w>x<h>` or
## `GAMESTUDIO_SHOWCASE: <key> FAILED <reason>`; the batch ends with
## `GAMESTUDIO_RENDER: OK 0x0`. Nothing is written into the project.
extends SceneTree

const PREFIX := "--gs-"
# Per item: beyond this, the scene is waiting for something that will not come.
const WATCHDOG_PER_ITEM := 6.0

var _frame = load(get_script().resource_path.get_base_dir().path_join("ui_frame.gd"))
var _done := false


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


func _finish(line: String, code: int) -> void:
	if _done:
		return
	_done = true
	print(line)
	quit(code)


func _run() -> void:
	print("GAMESTUDIO_RENDER: START")
	var args := _args()
	var text := FileAccess.get_file_as_string(str(args.get("list", "")))
	var items = JSON.parse_string(text)
	if not items is Array:
		_finish("GAMESTUDIO_RENDER: FAILED unreadable batch", 1)
		return
	var base := Vector2i(int(args.get("width", "0")), int(args.get("height", "0")))
	if base.x <= 0 or base.y <= 0:
		base = Vector2i(
			int(ProjectSettings.get_setting("display/window/size/viewport_width", 1152)),
			int(ProjectSettings.get_setting("display/window/size/viewport_height", 648)))
	var scale := maxf(float(args.get("scale", "2")), 0.25)
	create_timer(5.0 + WATCHDOG_PER_ITEM * items.size()).timeout.connect(
		_finish.bind("GAMESTUDIO_RENDER: FAILED the batch did not render in time", 1))

	for item in items:
		if _done:
			return
		var reason: String = await _draw(item, base, scale)
		var key := str(item.get("key", ""))
		if reason.begins_with("OK"):
			print("GAMESTUDIO_SHOWCASE: ", key, " ", reason)
		else:
			print("GAMESTUDIO_SHOWCASE: ", key, " FAILED ", reason)
	_finish("GAMESTUDIO_RENDER: OK 0x0", 0)


## Draw one item; return `OK <w>x<h>`, or the reason it failed.
func _draw(item: Dictionary, base: Vector2i, scale: float) -> String:
	var view := SubViewport.new()
	view.size = Vector2i(roundi(base.x * scale), roundi(base.y * scale))
	view.size_2d_override = base
	view.size_2d_override_stretch = true
	view.transparent_bg = true
	view.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	root.add_child(view)

	var node: Control
	if item.has("stylebox"):
		var box = load(str(item["stylebox"]))
		if not box is StyleBox:
			view.queue_free()
			return "not a StyleBox: " + str(item["stylebox"])
		var panel := Panel.new()
		panel.add_theme_stylebox_override("panel", box)
		var size: Array = item.get("size", [160, 56])
		panel.position = Vector2(8, 8)
		panel.size = Vector2(float(size[0]), float(size[1]))
		node = panel
		view.add_child(panel)
	else:
		var packed = load(str(item.get("scene", "")))
		if not packed is PackedScene:
			view.queue_free()
			return "not a scene: " + str(item.get("scene", ""))
		var scene: Node = packed.instantiate()
		if not scene is Control:
			scene.free()
			view.queue_free()
			return "the root is not a Control"
		node = scene as Control
		view.add_child(node)
		_frame.show_state(node, str(item.get("state", "normal")))

	for i in 3:
		await process_frame
	if _frame.fit(node):
		for i in 2:
			await process_frame
	RenderingServer.force_draw(false)
	var image := view.get_texture().get_image()
	var answer := ""
	if image == null or image.is_empty():
		answer = "empty image"
	else:
		var rect: Rect2 = _frame.bounds(node, node.get_global_rect())
		var region := Rect2i(Vector2i((rect.position * scale).floor()),
			Vector2i((rect.size * scale).ceil()))
		region = region.intersection(Rect2i(Vector2i.ZERO, image.get_size()))
		if region.size.x <= 0 or region.size.y <= 0:
			answer = "nothing drawn"
		else:
			image = image.get_region(region)
			if image.save_png(str(item.get("out", ""))) != OK:
				answer = "cannot write the image"
			else:
				answer = "OK %dx%d" % [image.get_width(), image.get_height()]
	view.queue_free()
	await process_frame
	return answer
