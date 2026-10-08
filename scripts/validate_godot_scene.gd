extends SceneTree

# Headless validation of a scene: an entity's mesh (.glb), a scene that
# animates it, a visual effect -- anything an export or an agent delivers to
# Godot.
#   godot --headless --path <project> --script res://validate_godot_scene.gd -- <scene>
#
# Checks what writing a file cannot guarantee alone: that Godot loads and
# instantiates the scene, that every animation track points to an existing
# node, and, when the scene has one, that 2D skinning resolves its bones.

var _target: String = ""
var _scene: Node = null
var _frames: int = 0

func _initialize() -> void:
	for arg in OS.get_cmdline_user_args():
		if arg.ends_with(".tscn") or arg.ends_with(".scn") or arg.ends_with(".glb") \
				or arg.ends_with(".gltf"):
			_target = arg
	if _target.is_empty():
		printerr("FAILED: no scene given")
		quit(1)
		return

	var packed := load(_target) as PackedScene
	if packed == null:
		printerr("FAILED: unreadable scene: %s" % _target)
		quit(1)
		return
	_scene = packed.instantiate()
	root.add_child(_scene)

func _process(_delta: float) -> bool:
	# Some nodes (Skeleton2D, particle systems) build themselves after entering
	# the tree: let one frame go by.
	_frames += 1
	if _frames < 2:
		return false
	_report()
	return true

func _find_all(node: Node, type_name: String, found: Array) -> void:
	if node.is_class(type_name):
		found.append(node)
	for child in node.get_children():
		_find_all(child, type_name, found)

func _count(type_name: String) -> int:
	var found: Array = []
	_find_all(_scene, type_name, found)
	return found.size()

func _report() -> void:
	var failures := 0
	print("scene: %s" % _target)

	# What the scene holds: a survey, not a requirement.
	var skeletons: Array = []
	_find_all(_scene, "Skeleton3D", skeletons)
	var bones := 0
	for skeleton in skeletons:
		bones += (skeleton as Skeleton3D).get_bone_count()
	print("  MeshInstance3D: %d, Skeleton3D: %d (%d bones), 2D sprites/particles: %d" % [
		_count("MeshInstance3D"), skeletons.size(), bones,
		_count("Sprite2D") + _count("AnimatedSprite2D") + _count("GPUParticles2D")
			+ _count("CPUParticles2D")])

	# 2D skinning: every named bone must exist, every weight array cover the polygon.
	var polygons: Array = []
	_find_all(_scene, "Polygon2D", polygons)
	for item in polygons:
		var poly := item as Polygon2D
		if poly.skeleton.is_empty():
			continue
		if poly.get_node_or_null(poly.skeleton) == null:
			printerr("  FAILED %s: invalid skeleton NodePath" % poly.name)
			failures += 1
			continue
		for i in range(poly.get_bone_count()):
			if poly.get_node_or_null(poly.get_bone_path(i)) == null:
				printerr("  FAILED %s: bone not found %s" % [poly.name, poly.get_bone_path(i)])
				failures += 1
			if poly.get_bone_weights(i).size() != poly.polygon.size():
				printerr("  FAILED %s: weights and vertices differ in count" % poly.name)
				failures += 1

	# Each animation: playable, all its tracks resolved from its AnimationPlayer's
	# root.
	var players: Array = []
	_find_all(_scene, "AnimationPlayer", players)
	if players.is_empty():
		print("  (no AnimationPlayer)")
	for item in players:
		var player := item as AnimationPlayer
		var base := player.get_node_or_null(player.root_node)
		if base == null:
			printerr("  FAILED: animation root not found %s" % player.root_node)
			failures += 1
			continue
		for name in player.get_animation_list():
			var anim := player.get_animation(name)
			var broken := 0
			for t in range(anim.get_track_count()):
				var node_path := String(anim.track_get_path(t)).get_slice(":", 0)
				if base.get_node_or_null(NodePath(node_path)) == null:
					broken += 1
			if broken > 0:
				printerr("  FAILED %s: %d tracks point at nothing" % [name, broken])
				failures += 1
			print("  animation %s: %.3fs, %d tracks, loop=%s" % [name, anim.length, anim.get_track_count(), anim.loop_mode != 0])
			player.play(name)
			player.seek(anim.length * 0.5, true)

	if failures == 0:
		print("RESULT: OK")
		quit(0)
	else:
		printerr("RESULT: %d failure(s)" % failures)
		quit(1)
