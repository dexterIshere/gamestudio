## Framing a UI component rendered alone, and showing its states without a mouse.
##
## Shared by `render_scene.gd` (one scene) and `showcase.gd` (a batch of props):
## they load it from beside them (`load(<script folder>/ui_frame.gd)`), because
## a script run with `--script` outside the project cannot `preload` a neighbor.
extends RefCounted

## The button states shown, in the order they are judged.
const STATES := ["normal", "hover", "pressed", "disabled"]


## The size of a lone component: at least what its visible children ask for,
## margins included (a child anchored across the full width, 12 px from the
## edges, asks for its minimum width plus 24). True if the size changed.
static func fit(control: Control) -> bool:
	var wanted := control.get_combined_minimum_size()
	for child in control.get_children():
		if not (child is Control) or not (child as Control).visible:
			continue
		var c := child as Control
		var minimum := c.get_combined_minimum_size()
		wanted.x = maxf(wanted.x, _span(minimum.x, c.anchor_left, c.anchor_right,
			c.offset_left, c.offset_right))
		wanted.y = maxf(wanted.y, _span(minimum.y, c.anchor_top, c.anchor_bottom,
			c.offset_top, c.offset_bottom))
	if wanted.x <= control.size.x and wanted.y <= control.size.y:
		return false
	control.size = control.size.max(wanted)
	return true


## The parent size for a child anchored between `from` and `to` to get `minimum`.
static func _span(minimum: float, from: float, to: float, start: float, end: float) -> float:
	if to - from > 0.0:
		return (minimum - (end - start)) / (to - from)
	return start + minimum


## What the framing must keep: the node and everything that overflows it.
static func bounds(node: Node, rect: Rect2) -> Rect2:
	if node is CanvasItem and not (node as CanvasItem).visible:
		return rect
	if node is Control:
		var own := (node as Control).get_global_rect()
		if own.size.x > 0.0 and own.size.y > 0.0:
			rect = own if rect.size.x <= 0.0 or rect.size.y <= 0.0 else rect.merge(own)
	for child in node.get_children():
		rect = bounds(child, rect)
	return rect


## A scene's buttons, the scene itself included.
static func buttons(node: Node) -> Array:
	var found := []
	_collect(node, found)
	return found


static func _collect(node: Node, found: Array) -> void:
	if node is BaseButton:
		found.append(node)
	for child in node.get_children():
		_collect(child, found)


## Show a state without a mouse: hover and pressed take the box and colors the
## theme gives that state; "disabled" really disables. What is seen is thus
## what the game would draw -- a state the theme does not distinguish comes out
## identical to "normal", and that is information.
static func show_state(node: Node, state: String) -> void:
	var suffix: String = {"hover": "hover", "pressed": "pressed"}.get(state, "")
	for button in buttons(node):
		var b := button as BaseButton
		if state == "disabled":
			b.disabled = true
		elif suffix != "" and b is Control:
			var c := b as Control
			if c.has_theme_stylebox(suffix):
				c.add_theme_stylebox_override("normal", c.get_theme_stylebox(suffix))
			for kind in ["font", "icon"]:
				var from: String = "%s_%s_color" % [kind, suffix]
				var to: String = "font_color" if kind == "font" else "icon_normal_color"
				if c.has_theme_color(from):
					c.add_theme_color_override(to, c.get_theme_color(from))
