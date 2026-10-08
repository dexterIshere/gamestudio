extends SceneTree

# Headless validation of an effect scene written by the studio.
#   godot --headless --path <project> --script res://validate_godot_effect.gd -- <scene.tscn> <frames>
#
# Checks what writing a .tscn cannot guarantee alone: that Godot loads the
# sheet, that the animation exists with the right number of frames, and that an
# emitter has its texture and its particle material.

func _initialize() -> void:
	var target := ""
	var expected := -1
	for arg in OS.get_cmdline_user_args():
		if arg.ends_with(".tscn"):
			target = arg
		elif arg.is_valid_int():
			expected = int(arg)
	var packed := load(target) as PackedScene
	if packed == null:
		printerr("FAILED: unreadable scene: %s" % target)
		quit(1)
		return
	var scene := packed.instantiate()
	var failures := 0
	var checked := 0
	for node in scene.find_children("*", "", true, false):
		if node is AnimatedSprite2D:
			checked += 1
			var frames: SpriteFrames = node.sprite_frames
			if frames == null or not frames.has_animation(node.animation):
				printerr("FAILED: missing animation: %s" % node.animation)
				failures += 1
				continue
			var count := frames.get_frame_count(node.animation)
			print("  flipbook: %s, %d frames" % [node.animation, count])
			if expected > 0 and count != expected:
				printerr("FAILED: %d frames instead of %d" % [count, expected])
				failures += 1
			if count > 0 and frames.get_frame_texture(node.animation, 0) == null:
				printerr("FAILED: unreadable frame texture")
				failures += 1
		elif node is GPUParticles2D:
			checked += 1
			print("  emitter: %d particles" % node.amount)
			if node.texture == null:
				printerr("FAILED: emitter without a texture")
				failures += 1
			if node.process_material == null:
				printerr("FAILED: emitter without a particle material")
				failures += 1
	if checked == 0:
		printerr("FAILED: neither flipbook nor emitter in %s" % target)
		failures += 1
	scene.free()
	print("RESULT: %s" % ("OK" if failures == 0 else "FAILED"))
	quit(0 if failures == 0 else 1)
