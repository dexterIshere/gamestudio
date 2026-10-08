"""Writing Godot resources: the sprite sheet and its directions."""

from __future__ import annotations

import pytest

from gamestudio.godot.spriteframes import SheetSpec, build_spriteframes, direction_names


def test_spriteframes_names_animations_by_direction():
    sheets = [
        SheetSpec(clip="walk", direction=name,
                  texture_path=f"res://sheets/walk_{name}.png",
                  frame_count=8, frame_width=64, frame_height=64)
        for name in direction_names(8)
    ]
    text = build_spriteframes(sheets).render()
    assert text.startswith('[gd_resource type="SpriteFrames"')
    assert '&"walk_ne"' in text
    assert text.count('type="AtlasTexture"') == 64


def test_invalid_directions_are_refused():
    with pytest.raises(ValueError):
        direction_names(5)


def test_the_studio_creates_a_godot_project_only_in_its_own_folders(tmp_path):
    """A game whose Godot project lives in `client/` never gets a second project."""
    from gamestudio.pipeline.steps.export import godot_target
    from gamestudio.store.folders import ProjectPaths

    game = ProjectPaths(project="game", root=tmp_path / "game", linked=True)
    (game.root / "client").mkdir(parents=True)
    with pytest.raises(ValueError, match=r"project\.godot"):
        godot_target(game)
    assert not (game.root / "project.godot").exists()

    (game.root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    assert godot_target(game) == game.root

    hosted = ProjectPaths(project="trial", root=tmp_path / "projects" / "trial", linked=False)
    assert (godot_target(hosted) / "project.godot").is_file()
    # A path given by hand is not the hosted folder: nothing is created there.
    with pytest.raises(ValueError):
        godot_target(hosted, tmp_path / "elsewhere")
    assert not (tmp_path / "elsewhere" / "project.godot").exists()
