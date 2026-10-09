## The lookdev bench: Godot stays open, off screen, and renders a specimen on demand.
##
## Run by the studio (`service/lookdev.py`):
##   godot --path <project> --script <this file> -- --gs-port=<port>
##
## It listens on 127.0.0.1:<port>, one JSON command per line, and answers with
## one JSON line. Any process on the machine can reach this port, and `stage`
## runs the script it is given: the first message of a connection must
## therefore be `{"op": "hello", "secret": "…"}`, with the secret the studio
## put in `GAMESTUDIO_BENCH_SECRET` at launch. Any other first message, or a
## wrong secret, and the connection is closed without an answer. The commands
## after that:
##
## - `{"op": "load", "shader": "res://…", "shape": "sphere", "size": [w, h]}`:
##   put the shader on its template -- a rectangle of its size for a
##   `canvas_item`, a lit shape for a `spatial`, a sky around the camera for a
##   `sky`. Answers the shader's settings as Godot reads them (`uniforms`:
##   name, type, hint, default, group).
## - `{"op": "stage", "setup": "/path/setup.gd"}`: put the game's real object
##   -- the one the script's `build()` returns (a planet, a building) -- lit
##   and framed. Built once, it serves all its shaders.
## - `{"op": "focus", "shader": "res://…"}`: on the staged object, select the
##   materials that use this shader; they are the ones receiving the settings.
## - `{"op": "screen", "shader": "res://…", "scene": "res://…", "nodes": [...],
##   "size": [w, h]}`: a device screen, for an interface shader. The scene of
##   a use is instantiated in the game's area of that screen, its anchors
##   placing it as in the game; the materials of `nodes` (paths from its root)
##   receive the settings. Without a scene, the template rectangle sits in the
##   middle. Black bars fill what the game leaves of the screen.
## - `{"op": "frame", "params": {...}, "yaw": 30, "pitch": 15, "zoom": 1,
##   "scale": 1, "format": "jpg", "background": "#0b0d12"}`: apply the
##   settings and render an image, in base64 (`data`). `jpg` is fast and
##   opaque, on the given background; `webp` keeps an interface's
##   transparency, for a thumbnail; `png` is exact, for a sky behind a page.
##   An image's settings do not survive the
##   next one: what it does not give again returns to its starting value.
##   With `"screen": {"size": [w, h], "logical": [w, h], "content": [x, y, w, h]}`
##   it renders a device: `size` pixels, the game's units over the whole
##   screen, and the game's area in them; for a material or a sky, only
##   `size` (the camera's format).
## - `{"op": "ping"}`, `{"op": "quit"}`.
##
## A shader is reread from its file on each load: changed in the game, it shows
## as it has become.
##
## Its life hangs on its connection: the studio keeps it open, and when it
## closes -- the studio stops, even killed without warning -- the bench stops.
## Also without a recognized studio thirty seconds after start, or without a
## command for ten minutes. Between two images, it draws nothing.
##
## The bench touches no file of the project: it only loads what it is given,
## and only answers on the socket. It prints `GAMESTUDIO_BENCH: READY <port>`
## when it listens.
extends SceneTree

const PREFIX := "--gs-"
const SECRET_ENV := "GAMESTUDIO_BENCH_SECRET"
# Without a command for this long, the bench stops: the studio restarts it.
const IDLE_QUIT := 600.0
# The studio connects as soon as READY is printed; beyond this, nobody will come.
const CONNECT_TIMEOUT := 30.0
const MAX_LINE := 1 << 20
const JPG_QUALITY := 0.88
const WEBP_QUALITY := 0.9
# Commands arrive through the socket: the bench checks it every millisecond
# without drawing anything, rather than spinning idle.
const POLL_USEC := 1000

var _server := TCPServer.new()
var _peer: StreamPeerTCP = null
var _secret := ""
# True once the peer gave the secret: it is then the studio.
var _trusted := false
var _buffer := PackedByteArray()
var _idle := 0.0
var _alone := 0.0
var _busy := false

var _view: SubViewport
var _stage: Node
var _object: Node3D
var _env: Environment
var _material: ShaderMaterial
# The materials that receive the settings: the template's, or those of the
# real object that use the selected shader.
var _materials: Array[ShaderMaterial] = []
# Their starting values, and the settings the last image applied.
var _baseline := {}
var _applied := {}
var _kind := ""
var _pivot: Node3D
var _camera: Camera3D
var _rect: ColorRect
var _base := Vector2i(512, 512)
var _frame_distance := 2.6
# A device screen (`screen`): the game's area in it, its background, and the
# geometry of the last image, whose change lets the layout settle first.
var _screen_area: Control
var _screen_fill: ColorRect
var _screen_geometry := ""


func _initialize() -> void:
	var args := {}
	for raw in OS.get_cmdline_user_args():
		if raw.begins_with(PREFIX):
			var parts := raw.substr(PREFIX.length()).split("=", true, 1)
			args[parts[0]] = parts[1] if parts.size() > 1 else "true"
	_secret = OS.get_environment(SECRET_ENV)
	if _secret.is_empty():
		print("GAMESTUDIO_BENCH: FAILED missing secret (", SECRET_ENV, ")")
		quit(1)
		return
	var port := int(args.get("port", "0"))
	if port <= 0 or _server.listen(port, "127.0.0.1") != OK:
		print("GAMESTUDIO_BENCH: FAILED port ", port)
		quit(1)
		return
	OS.low_processor_usage_mode = true
	OS.low_processor_usage_mode_sleep_usec = POLL_USEC
	_view = SubViewport.new()
	_view.size = _base
	_view.transparent_bg = true
	# Nothing is drawn between two images: `_frame` asks for its own.
	_view.render_target_update_mode = SubViewport.UPDATE_DISABLED
	_view.own_world_3d = true
	root.add_child(_view)
	print("GAMESTUDIO_BENCH: READY ", port)


func _process(delta: float) -> bool:
	_idle += delta
	if _idle > IDLE_QUIT:
		quit(0)
		return true
	if not _trusted:
		_alone += delta
		if _alone > CONNECT_TIMEOUT:
			quit(0)
			return true
	if _peer == null:
		if not _server.is_connection_available():
			return false
		_peer = _server.take_connection()
		_buffer.clear()
	_peer.poll()
	if _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		# The studio closed its connection, or is gone: the bench has nobody left
		# to serve. When a stranger leaves, it keeps waiting for the studio.
		if _trusted:
			quit(0)
			return true
		_hang_up()
		return false
	var available := _peer.get_available_bytes()
	if available > 0:
		var chunk := _peer.get_data(available)
		if chunk[0] == OK:
			_buffer.append_array(chunk[1])
	var newline := _buffer.find(10)
	if newline >= 0 and not _busy:
		var line := _buffer.slice(0, newline).get_string_from_utf8()
		_buffer = _buffer.slice(newline + 1)
		if not _trusted:
			_greet(line)
			return false
		_idle = 0.0
		_handle(line)
	elif _buffer.size() > MAX_LINE:
		if not _trusted:
			_hang_up()
			return false
		_reply({"ok": false, "error": "command too long"})
		_buffer.clear()
	return false


## The first message of a connection: the launch secret, and nothing else.
func _greet(line: String) -> void:
	var hello = JSON.parse_string(line)
	if typeof(hello) == TYPE_DICTIONARY and str(hello.get("op", "")) == "hello" \
			and str(hello.get("secret", "")) == _secret:
		_trusted = true
		_reply({"ok": true})
	else:
		_hang_up()


## Close a connection that did not give the secret, without answering it.
func _hang_up() -> void:
	_peer.disconnect_from_host()
	_peer = null
	_buffer.clear()


func _reply(data: Dictionary) -> void:
	if _peer != null:
		_peer.put_data((JSON.stringify(data) + "\n").to_utf8_buffer())


func _handle(line: String) -> void:
	# One command at a time: the one that waits (building an object) holds the
	# next one in the buffer.
	_busy = true
	await _dispatch(line)
	_busy = false


func _dispatch(line: String) -> void:
	var command = JSON.parse_string(line)
	if typeof(command) != TYPE_DICTIONARY:
		_reply({"ok": false, "error": "unreadable command"})
		return
	match str(command.get("op", "")):
		"ping":
			_reply({"ok": true, "version": Engine.get_version_info()["string"]})
		"load":
			_reply(_load(command))
		"stage":
			_reply(await _stage_object(command))
		"focus":
			_reply(_focus(command))
		"screen":
			_reply(_screen(command))
		"frame":
			_reply(await _frame(command))
		"quit":
			_reply({"ok": true})
			quit(0)
		_:
			_reply({"ok": false, "error": "unknown command"})


# ------------------------------------------------------------------ template


func _clear() -> void:
	# A game object's materials are its resources, shared and kept in memory
	# by Godot: they go back as the game gave them.
	_restore()
	if _stage != null:
		_stage.queue_free()
		_stage = null
	_object = null
	_env = null
	_material = null
	_materials.clear()
	_baseline.clear()
	_applied.clear()
	_pivot = null
	_camera = null
	_rect = null
	_screen_area = null
	_screen_fill = null
	_screen_geometry = ""
	_view.size_2d_override = Vector2i.ZERO
	_view.size_2d_override_stretch = false


## The shader, reread from its file: the one already in memory is updated in
## place, and every material that uses it along with it.
func _shader(path: String) -> Shader:
	if not ResourceLoader.exists(path):
		return null
	var found = ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_REPLACE)
	return found if found is Shader else null


func _load(command: Dictionary) -> Dictionary:
	var path := str(command.get("shader", ""))
	var shader := _shader(path)
	if shader == null:
		return {"ok": false, "error": "shader not found: " + path}
	_clear()
	_material = ShaderMaterial.new()
	_material.shader = shader
	_kind = ["spatial", "canvas_item", "particles", "sky", "fog"][shader.get_mode()]
	var size = command.get("size", [])
	match _kind:
		"canvas_item":
			_base = _template_size(shader, size)
			var w := _base.x
			var h := _base.y
			_stage = Control.new()
			_rect = ColorRect.new()
			_rect.size = Vector2(w, h)
			_rect.material = _material
			_stage.add_child(_rect)
		"spatial":
			_base = Vector2i(512, 512)
			_stage = _scene_3d(str(command.get("shape", "sphere")))
		"sky":
			_base = Vector2i(768, 432)
			_stage = _sky_3d()
		_:
			return {"ok": false, "error": "no template written yet for a shader of type " + _kind}
	_view.size = _base
	_view.add_child(_stage)
	_materials.append(_material)
	_capture()
	return {"ok": true, "kind": _kind, "size": [_base.x, _base.y], "uniforms": _uniforms(shader)}


func _scene_3d(shape: String) -> Node3D:
	var stage := Node3D.new()
	var mesh := MeshInstance3D.new()
	match shape:
		"plane":
			var plane := PlaneMesh.new()
			plane.size = Vector2(2.4, 2.4)
			plane.subdivide_width = 128
			plane.subdivide_depth = 128
			mesh.mesh = plane
		"cube":
			mesh.mesh = BoxMesh.new()
		_:
			var sphere := SphereMesh.new()
			sphere.radial_segments = 128
			sphere.rings = 64
			mesh.mesh = sphere
	mesh.material_override = _material
	stage.add_child(mesh)
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-40, -35, 0)
	stage.add_child(sun)
	var env := WorldEnvironment.new()
	env.environment = Environment.new()
	env.environment.background_mode = Environment.BG_COLOR
	env.environment.background_color = Color(0.04, 0.045, 0.06)
	env.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.environment.ambient_light_color = Color(0.35, 0.37, 0.42)
	_env = env.environment
	stage.add_child(env)
	_pivot = Node3D.new()
	stage.add_child(_pivot)
	_camera = Camera3D.new()
	_camera.position = Vector3(0, 0, 2.6)
	_camera.fov = 40
	_pivot.add_child(_camera)
	return stage


func _sky_3d() -> Node3D:
	var stage := Node3D.new()
	var env := WorldEnvironment.new()
	env.environment = Environment.new()
	env.environment.background_mode = Environment.BG_SKY
	env.environment.sky = Sky.new()
	env.environment.sky.sky_material = _material
	stage.add_child(env)
	_pivot = Node3D.new()
	stage.add_child(_pivot)
	_camera = Camera3D.new()
	_camera.fov = 70
	_pivot.add_child(_camera)
	return stage


## The template rectangle of an interface shader: the given size, else the
## `node_size` the shader declares, else a square.
func _template_size(shader: Shader, size) -> Vector2i:
	if typeof(size) != TYPE_ARRAY or size.size() != 2:
		# An interface shader that knows its size often declares it.
		var own = RenderingServer.shader_get_parameter_default(shader.get_rid(), "node_size")
		size = [own.x, own.y] if own is Vector2 else []
	if typeof(size) == TYPE_ARRAY and size.size() == 2:
		return Vector2i(clampi(int(size[0]), 8, 2048), clampi(int(size[1]), 8, 2048))
	return Vector2i(256, 256)


# ------------------------------------------------------------ device screen


func _screen(command: Dictionary) -> Dictionary:
	var path := str(command.get("shader", ""))
	var shader := _shader(path)
	if shader == null:
		return {"ok": false, "error": "shader not found: " + path}
	_clear()
	_kind = "screen"
	var root := Control.new()
	var bars := ColorRect.new()
	bars.color = Color.BLACK
	bars.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.add_child(bars)
	_screen_area = Control.new()
	_screen_area.clip_contents = true
	root.add_child(_screen_area)
	_screen_fill = ColorRect.new()
	_screen_fill.set_anchors_preset(Control.PRESET_FULL_RECT)
	_screen_area.add_child(_screen_fill)
	# In the tree before the scene: its scripts start as they do in the game.
	_stage = root
	_view.add_child(root)
	var found := []
	var scene_path := str(command.get("scene", ""))
	if scene_path.is_empty():
		_material = ShaderMaterial.new()
		_material.shader = shader
		_base = _template_size(shader, command.get("size", []))
		_rect = ColorRect.new()
		_rect.size = Vector2(_base)
		_rect.material = _material
		_screen_area.add_child(_rect)
		found.append(_material)
	else:
		if not ResourceLoader.exists(scene_path):
			return {"ok": false, "error": "scene not found: " + scene_path}
		# Reread with its resources: a material saved into the scene shows as saved.
		var packed = ResourceLoader.load(scene_path, "", ResourceLoader.CACHE_MODE_REPLACE_DEEP)
		if not packed is PackedScene:
			return {"ok": false, "error": "not a scene: " + scene_path}
		var instance: Node = packed.instantiate()
		_screen_area.add_child(instance)
		for held in command.get("nodes", []):
			var node: Node = instance if str(held) == instance.name \
				else instance.get_node_or_null(NodePath(str(held)))
			if node is CanvasItem and node.material is ShaderMaterial \
					and node.material.shader != null \
					and node.material.shader.resource_path == shader.resource_path \
					and not found.has(node.material):
				found.append(node.material)
		if found.is_empty():
			_collect(instance, shader, found)
		if found.is_empty():
			return {"ok": false, "error": "the scene does not use this shader: " + scene_path}
	for material in found:
		_materials.append(material)
	_material = _materials[0]
	_capture()
	return {"ok": true, "kind": "screen", "size": [_base.x, _base.y], "materials": found.size(),
		"uniforms": _uniforms(shader)}


## Size the device screen: its pixels, the game's units over it, the game's
## area in them. A new geometry lets the layout settle (containers sort,
## scripts follow their size) before the image is drawn.
func _fit_screen(screen: Dictionary, background: Color) -> bool:
	var size = screen.get("size", [])
	var logical = screen.get("logical", size)
	var area = screen.get("content", [0, 0] + Array(logical))
	if typeof(size) != TYPE_ARRAY or size.size() != 2 or typeof(logical) != TYPE_ARRAY \
			or logical.size() != 2 or typeof(area) != TYPE_ARRAY or area.size() != 4:
		return false
	_view.size = Vector2i(clampi(int(size[0]), 16, 4096), clampi(int(size[1]), 16, 4096))
	if _kind != "screen":
		return true
	_view.size_2d_override = Vector2i(maxi(1, roundi(logical[0])), maxi(1, roundi(logical[1])))
	_view.size_2d_override_stretch = true
	(_stage as Control).size = Vector2(logical[0], logical[1])
	_screen_area.position = Vector2(area[0], area[1])
	_screen_area.size = Vector2(area[2], area[3])
	_screen_fill.color = background
	if _rect != null:
		_rect.position = ((_screen_area.size - _rect.size) / 2.0).round()
	var geometry := JSON.stringify([size, logical, area])
	if geometry != _screen_geometry:
		_screen_geometry = geometry
		for i in 2:
			await process_frame
	return true


# ------------------------------------------------------------- real object


func _stage_object(command: Dictionary) -> Dictionary:
	var helper_script = load(str(command.get("setup", "")))
	if helper_script == null:
		return {"ok": false, "error": "unreadable setup"}
	_clear()
	_kind = "object"
	_base = Vector2i(640, 640)
	_view.size = _base
	var helper = helper_script.new()
	var built = await helper.build()
	if not built is Node3D:
		return {"ok": false, "error": "build() must return a Node3D"}
	var stage := Node3D.new()
	_view.add_child(stage)
	_stage = stage
	_object = built
	stage.add_child(built)
	# Let the object finish building (geometry stitched in tasks).
	for i in 4:
		await process_frame
	if _first_of(built, "WorldEnvironment") == null:
		var sun := DirectionalLight3D.new()
		sun.rotation_degrees = Vector3(-25, 35, 0)
		sun.light_energy = 1.45
		stage.add_child(sun)
		var env := WorldEnvironment.new()
		env.environment = Environment.new()
		env.environment.background_mode = Environment.BG_COLOR
		env.environment.background_color = Color(0.02, 0.03, 0.06)
		env.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
		env.environment.ambient_light_color = Color(0.72, 0.72, 0.76)
		env.environment.ambient_light_energy = 0.35
		env.environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
		_env = env.environment
		stage.add_child(env)
	var box := _bounds(built)
	# The largest side, not the diagonal: a sphere fits its frame without the
	# margin of a box corner it does not fill.
	var radius := maxf(maxf(box.size.x, maxf(box.size.y, box.size.z)) * 0.5, 0.01)
	_pivot = Node3D.new()
	_pivot.position = box.get_center()
	stage.add_child(_pivot)
	_camera = Camera3D.new()
	_camera.fov = 40
	_camera.position = Vector3(0, 0, radius / tan(deg_to_rad(20.0)) * 1.15)
	_camera.far = radius * 40.0
	_pivot.add_child(_camera)
	_frame_distance = _camera.position.z
	return {"ok": true, "kind": "object", "size": [_base.x, _base.y], "radius": radius}


func _focus(command: Dictionary) -> Dictionary:
	if _object == null:
		return {"ok": false, "error": "no object on the bench"}
	var path := str(command.get("shader", ""))
	var shader := _shader(path)
	if shader == null:
		return {"ok": false, "error": "shader not found: " + path}
	_restore()
	var found := []
	_collect(_object, shader, found)
	_materials.clear()
	for material in found:
		_materials.append(material)
	_material = _materials[0] if _materials else null
	_capture()
	return {"ok": true, "kind": "object", "size": [_base.x, _base.y], "materials": found.size(),
		"uniforms": _uniforms(shader)}


func _first_of(node: Node, type: String) -> Node:
	if node.is_class(type):
		return node
	for child in node.get_children():
		var hit := _first_of(child, type)
		if hit != null:
			return hit
	return null


func _bounds(node: Node) -> AABB:
	var box := AABB()
	var first := true
	for visual in node.find_children("*", "VisualInstance3D", true, false):
		var local: AABB = visual.get_aabb()
		var world: AABB = visual.global_transform * local
		box = world if first else box.merge(world)
		first = false
	if node is VisualInstance3D:
		var own: AABB = node.global_transform * node.get_aabb()
		box = own if first else box.merge(own)
	return box


## The object's ShaderMaterials that use this shader, wherever they are set.
func _collect(node: Node, shader: Shader, found: Array) -> void:
	var candidates := []
	if node is GeometryInstance3D:
		candidates.append(node.material_override)
		candidates.append(node.material_overlay)
	if node is MeshInstance3D and node.mesh != null:
		for i in node.mesh.get_surface_count():
			candidates.append(node.get_surface_override_material(i))
			candidates.append(node.mesh.surface_get_material(i))
	if node is CanvasItem:
		candidates.append(node.material)
	for material in candidates:
		if material is ShaderMaterial and material.shader != null \
				and material.shader.resource_path == shader.resource_path \
				and not found.has(material):
			found.append(material)
	for child in node.get_children():
		_collect(child, shader, found)


## The shader's settings, in file order, each with its group
## (`group_uniforms`): 107 settings read better in nine sections.
func _uniforms(shader: Shader) -> Array:
	var found := []
	var group := ""
	var subgroup := ""
	for entry in shader.get_shader_uniform_list(true):
		var name := str(entry["name"])
		var usage := int(entry["usage"])
		if usage & PROPERTY_USAGE_GROUP:
			group = name
			subgroup = ""
			continue
		if usage & PROPERTY_USAGE_SUBGROUP:
			subgroup = name
			continue
		var value = RenderingServer.shader_get_parameter_default(shader.get_rid(), name)
		found.append({"name": name, "type": type_string(int(entry["type"])),
			"hint": int(entry["hint"]), "hint_string": str(entry["hint_string"]),
			"default": _to_json(value),
			"group": group + ("/" + subgroup if subgroup else "")})
	return found


# ------------------------------------------------------------------- an image


## The starting values of the materials that receive the settings: those the
## game (or the shader) gives them, before any setting from the bench.
func _capture() -> void:
	_baseline.clear()
	_applied.clear()
	for material in _materials:
		var values := {}
		for entry in material.shader.get_shader_uniform_list():
			values[entry["name"]] = material.get_shader_parameter(entry["name"])
		_baseline[material.get_instance_id()] = values


## Put the materials back as they were before the bench.
func _restore() -> void:
	for material in _materials:
		var values: Dictionary = _baseline.get(material.get_instance_id(), {})
		for name in _applied:
			material.set_shader_parameter(name, values.get(name))
	_applied.clear()


func _apply(params: Dictionary) -> void:
	for material in _materials:
		var values: Dictionary = _baseline.get(material.get_instance_id(), {})
		# What the previous image set and this one does not give again returns
		# to its starting value: "back to the game" really means it.
		for name in _applied:
			if not params.has(name):
				material.set_shader_parameter(name, values.get(name))
		for name in params:
			var current = material.get_shader_parameter(name)
			if current == null:
				current = RenderingServer.shader_get_parameter_default(material.shader.get_rid(), name)
			material.set_shader_parameter(name, _from_json(params[name], current))
	_applied.clear()
	for name in params:
		_applied[name] = true


func _frame(command: Dictionary) -> Dictionary:
	if _stage == null:
		return {"ok": false, "error": "nothing on the bench"}
	var format := str(command.get("format", "webp"))
	var background := Color.html(str(command.get("background", "#0b0d12"))) \
		if Color.html_is_valid(str(command.get("background", ""))) else Color(0.04, 0.05, 0.07)
	# An interface thumbnail keeps its transparency; a live image is opaque,
	# on the background of the page that shows it: nothing is cut out.
	_view.transparent_bg = _kind == "canvas_item" and format != "jpg"
	RenderingServer.set_default_clear_color(background)
	if _env != null and _env.background_mode == Environment.BG_COLOR:
		_env.background_color = background
	var screen = command.get("screen", null)
	if typeof(screen) == TYPE_DICTIONARY:
		if not await _fit_screen(screen, background):
			return {"ok": false, "error": "unreadable screen"}
	elif _kind == "screen":
		return {"ok": false, "error": "a device screen needs its size"}
	else:
		var scale := clampf(float(command.get("scale", 1.0)), 0.25, 4.0)
		_view.size = Vector2i(roundi(_base.x * scale), roundi(_base.y * scale))
		if _rect != null:
			_view.size_2d_override = _base
			_view.size_2d_override_stretch = true
	# After the layout: what a scene's scripts set as it settles does not cover
	# the settings of this image.
	var params = command.get("params", {})
	_apply(params if typeof(params) == TYPE_DICTIONARY else {})
	if _pivot != null:
		_pivot.rotation_degrees = Vector3(-float(command.get("pitch", 0.0)),
			float(command.get("yaw", 0.0)), 0)
		if _kind == "spatial" or _kind == "object":
			var distance := 2.6 if _kind == "spatial" else _frame_distance
			_camera.position = Vector3(0, 0, distance / maxf(float(command.get("zoom", 1.0)), 0.2))
	var started := Time.get_ticks_usec()
	_view.render_target_update_mode = SubViewport.UPDATE_ONCE
	RenderingServer.force_draw(false)
	var drawn := Time.get_ticks_usec()
	var image := _view.get_texture().get_image()
	var read := Time.get_ticks_usec()
	if image == null or image.is_empty():
		return {"ok": false, "error": "empty image"}
	var data: PackedByteArray
	if format == "jpg":
		data = image.save_jpg_to_buffer(JPG_QUALITY)
	elif format == "png":
		# Lossless: a sky behind a page, where JPEG would smear every star.
		data = image.save_png_to_buffer()
	else:
		format = "webp"
		data = image.save_webp_to_buffer(true, WEBP_QUALITY)
	var encoded := Time.get_ticks_usec()
	return {"ok": true, "width": image.get_width(), "height": image.get_height(),
		"format": format, "data": Marshalls.raw_to_base64(data),
		"ms": {"draw": (drawn - started) / 1000.0, "read": (read - drawn) / 1000.0,
			"encode": (encoded - read) / 1000.0}}


## A Godot value as JSON: numbers, booleans, vectors and colors as lists.
func _to_json(value) -> Variant:
	match typeof(value):
		TYPE_VECTOR2, TYPE_VECTOR2I:
			return [value.x, value.y]
		TYPE_VECTOR3, TYPE_VECTOR3I:
			return [value.x, value.y, value.z]
		TYPE_VECTOR4, TYPE_VECTOR4I:
			return [value.x, value.y, value.z, value.w]
		TYPE_COLOR:
			return [value.r, value.g, value.b, value.a]
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_FLOAT:
			return value
		TYPE_OBJECT:
			return value.resource_path if value is Resource else null
	return str(value)


## A JSON value converted to the type the setting expects (that of its current value).
func _from_json(value, current) -> Variant:
	if typeof(value) == TYPE_STRING and str(value).begins_with("res://"):
		return load(value) if ResourceLoader.exists(value) else current
	if typeof(value) != TYPE_ARRAY:
		match typeof(current):
			TYPE_INT:
				return int(value)
			TYPE_FLOAT:
				return float(value)
			TYPE_BOOL:
				return bool(value)
		return value
	var v: Array = value
	match typeof(current):
		TYPE_VECTOR2:
			return Vector2(v[0], v[1])
		TYPE_VECTOR2I:
			return Vector2i(int(v[0]), int(v[1]))
		TYPE_VECTOR3:
			return Vector3(v[0], v[1], v[2])
		TYPE_VECTOR3I:
			return Vector3i(int(v[0]), int(v[1]), int(v[2]))
		TYPE_VECTOR4:
			return Vector4(v[0], v[1], v[2], v[3])
		TYPE_COLOR:
			return Color(v[0], v[1], v[2], v[3] if v.size() > 3 else 1.0)
	if v.size() == 3:
		return Vector3(v[0], v[1], v[2])
	if v.size() == 4:
		return Color(v[0], v[1], v[2], v[3])
	return Vector2(v[0], v[1]) if v.size() == 2 else current
