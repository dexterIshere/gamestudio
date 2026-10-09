"""A game's lookdev: its shaders as specimens, their uses, their render, their discussion.

What these tests protect: each game shader becomes a specimen, with the settings
the game puts on it, use by use, named by the node carrying it; an include is
not rendered alone; no verdict is held, the setup is written next to the project
without touching the game, and what is wrong is refused; "Save" writes into the
game only the touched settings, where the use takes them; a shader is discussed
with an agent, whose brief carries its uses, and so is a whole section -- its
colors with where each is written, its typography, a family of shaders; the
page only serves the fonts the game declares. The bench tests have a shader
rendered by the bench -- Godot off screen -- and only run where Godot and a
virtual display are installed.
"""

from __future__ import annotations

import io
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from gamestudio.config import Settings
from gamestudio.service import build, folders, handoff, lookdev, using
from gamestudio.service.errors import NotFound, ServiceError

SHADER = """shader_type canvas_item;

uniform vec2 node_size = vec2(64.0, 32.0);
uniform vec4 tint : source_color = vec4(0.2, 0.4, 0.9, 1.0);
uniform float glow : hint_range(0.0, 1.0) = 0.5;

void fragment() {
\tCOLOR = vec4(tint.rgb * (0.5 + glow), 1.0);
}
"""

SCENE = """[gd_scene load_steps=3 format=3]

[ext_resource type="Shader" path="res://shaders/button.gdshader" id="1_s"]

[sub_resource type="ShaderMaterial" id="mat_ok"]
shader = ExtResource("1_s")
shader_parameter/node_size = Vector2(120, 40)
shader_parameter/tint = Color(1, 0.8, 0.4, 1)
shader_parameter/glow = 0.25

[node name="Bar" type="Control"]

[node name="Confirm" type="Button" parent="."]

[node name="Background" type="ColorRect" parent="Confirm"]
material = SubResource("mat_ok")
"""

MATERIAL = """[gd_resource type="ShaderMaterial" load_steps=2 format=3]

[ext_resource type="Shader" path="res://shaders/button.gdshader" id="1"]

[resource]
shader = ExtResource("1")
shader_parameter/glow = 1.0
shader_parameter/active = true
"""


GREEN = "shader_type canvas_item;\n\nvoid fragment() {\n\tCOLOR = vec4(0.0, 1.0, 0.0, 1.0);\n}\n"

# Settings filed in groups (`group_uniforms`): the room shows them as sections.
GROUPS = """shader_type canvas_item;

group_uniforms color;
uniform vec4 hue : source_color = vec4(1.0, 1.0, 1.0, 1.0);
group_uniforms;
uniform float strength = 1.0;

void fragment() {
\tCOLOR = vec4(hue.rgb * strength, 1.0);
}
"""

BENCH = pytest.mark.skipif(
    not shutil.which("godot") or not (shutil.which("gamescope") or shutil.which("xvfb-run")),
    reason="the bench needs Godot and a virtual display")


def _game(root: Path) -> Path:
    def write(relative: str, text: str = "") -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write("client/project.godot", "\n".join([
        "config_version=5", "", "[application]", 'config/name="Trial"', "",
        "[rendering]", "environment/defaults/default_clear_color=Color(0.05, 0.09, 0.16, 1)",
        "", "[display]", "window/size/viewport_width=320", "window/size/viewport_height=240"]))
    write("client/shaders/button.gdshader", SHADER)
    write("client/shaders/green.gdshader", GREEN)
    write("client/shaders/groups.gdshader", GROUPS)
    write("client/shaders/common.gdshaderinc", "float soften(float x) { return x; }\n")
    write("client/ui/bar.tscn", SCENE)
    write("client/style/button_bright.tres", MATERIAL)
    write("client/fonts/Title.ttf", "")
    return root


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    settings = Settings(data_dir=isolated_data, project_root=studio_dir,
                        context_dir=tmp_path / "context")
    root = _game(tmp_path / "my-game")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        yield root
        lookdev.stop_benches()


def test_each_shader_becomes_a_specimen(game: Path) -> None:
    universe = lookdev.universe("game")
    by_id = {entry["id"]: entry for entry in universe["specimens"]}
    button = by_id["button"]
    assert (button["kind"], button["res_path"], button["godot"]) == \
        ("canvas_item", "res://shaders/button.gdshader", "client")
    assert button["renderable"] and button["users"] == ["client/ui/bar.tscn"]
    assert by_id["common"]["kind"] == "include" and not by_id["common"]["renderable"]
    assert len(universe["specimens"]) == 4
    assert universe["theme"]["background"] == "#0d1729"
    assert universe["theme"]["fonts"] == [{"file": "client/fonts/Title.ttf", "family": "Title"}]
    # The page is set in the game's family, with every face of it.
    assert universe["theme"]["font"] == {"family": "Title", "faces": [
        {"file": "client/fonts/Title.ttf", "weight": 400, "italic": False}]}


def test_the_art_direction_comes_by_aspect(game: Path) -> None:
    """Colors, typography and shaders: each aspect of the art direction, read from the game."""
    universe = lookdev.universe("game")
    assert set(universe) >= {"specimens", "palette", "typography", "theme"}
    by_hex = {color["hex"]: color for color in universe["palette"]["colors"]}
    assert by_hex["#ffcc66"]["uses"] == [{"file": "client/ui/bar.tscn", "aspect": "interface",
                                         "count": 1, "names": ["tint"]}], \
        "the tint the scene sets, on an interface shader"
    assert by_hex["#3366e6"]["uses"][0]["file"] == "client/shaders/button.gdshader", \
        "the shader's own color setting"
    assert universe["palette"]["total"] >= by_hex["#ffcc66"]["count"]
    [family] = universe["typography"]["families"]
    assert family["family"] == "Title" and family["faces"][0]["uses"] == 0


def test_uses_carry_the_game_settings(game: Path) -> None:
    root, found, entry = lookdev._find("game", "button")
    shader = next(s for s in found["shaders"] if s["path"] == entry["file"])
    uses = {p["label"]: p for p in lookdev._presets(root, found, shader, entry["res_path"])}
    scene = uses["bar.tscn — Confirm/Background"]
    assert scene["params"] == {"node_size": [120.0, 40.0], "tint": [1.0, 0.8, 0.4, 1.0],
                               "glow": 0.25}
    assert scene["nodes"] == ["Confirm/Background"]
    assert uses["button_bright.tres"]["params"] == {"glow": 1.0, "active": True}


def test_the_setup_is_written_next_to_the_project(game: Path) -> None:
    written = lookdev.set_state("game", "button", shape="cube")
    assert written["shape"] == "cube" and "verdict" not in written, "no verdict is held"
    file = game / ".gamestudio" / "documents" / "lookdev" / "button.json"
    assert file.is_file(), "versioned with the project, never in the game"
    assert "counts" not in lookdev.universe("game")
    # Only the given fields change.
    lookdev.set_state("game", "button", preset="default")
    assert lookdev._state("game", "button")["shape"] == "cube"

    for wrong, message in (({"setup": "extends Node"}, "func build"),
                           ({"shape": "torus"}, "unknown shape")):
        with pytest.raises(ServiceError, match=message):
            lookdev.set_state("game", "button", **wrong)
    with pytest.raises(NotFound):
        lookdev.set_state("game", "missing", shape="cube")
    assert not (game / "client" / "lookdev").exists()


def test_a_shader_is_discussed_with_an_agent(game: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """The brief carries the shader, its uses with their settings, and how to look at it."""
    thumbnail = game / "thumbnail.webp"
    monkeypatch.setattr(lookdev, "thumbnail", lambda project, specimen: thumbnail)
    brief = handoff.lookdev_brief("game", "button")
    text = Path(brief["path"]).read_text(encoding="utf-8")
    assert Path(brief["path"]).name == "lookdev-button.md"
    assert "bar.tscn — Confirm/Background" in text and "glow=0.25" in text
    assert f"`{thumbnail}`" in text
    assert 'lookdev_look(project="game", specimen="button"' in text
    assert "studio/lookdev-button" in text, "the game is only modified on a branch"
    assert "\n" not in brief["prompt"] and brief["path"] in brief["prompt"]
    with pytest.raises(NotFound):
        handoff.lookdev_brief("game", "missing")


def test_a_section_is_discussed_as_a_whole(game: Path) -> None:
    """Colors, typography or a family of shaders: the brief carries the section and where."""
    brief = handoff.lookdev_aspect_brief("game", "colors")
    text = Path(brief["path"]).read_text(encoding="utf-8")
    assert Path(brief["path"]).name == "lookdev-aspect-colors.md"
    assert "### Interface — " in text
    assert "- `#ffcc66` x1 — `client/ui/bar.tscn` (tint)" in text, "a color and where it is"
    assert "studio/lookdev-colors" in text, "the game is only modified on a branch"
    assert "\n" not in brief["prompt"] and brief["path"] in brief["prompt"]

    text = Path(handoff.lookdev_aspect_brief("game", "typography")["path"]).read_text(
        encoding="utf-8")
    assert "**Title**" in text and "Title.ttf` — cited nowhere" in text

    text = Path(handoff.lookdev_aspect_brief("game", "interface")["path"]).read_text(
        encoding="utf-8")
    assert 'lookdev_brief(project="game", specimen="button")' in text
    assert "**green**" in text and "### Their colors" in text and "`#ffcc66`" in text
    with pytest.raises(NotFound):
        handoff.lookdev_aspect_brief("game", "sound")


def test_an_include_is_not_rendered_alone_and_fonts_are_the_game_ones(
        game: Path) -> None:
    with pytest.raises(ServiceError, match="include"):
        lookdev.frame("game", "common")
    assert lookdev.font_file("game", "client/fonts/Title.ttf").is_file()
    with pytest.raises(NotFound):
        lookdev.font_file("game", "client/project.godot")



def test_save_writes_the_settings_into_the_material_of_the_use(game: Path) -> None:
    """Only the lines of the touched settings change; a setting not there is added."""
    scene = game / "client" / "ui" / "bar.tscn"
    before = scene.read_text(encoding="utf-8")
    done = lookdev.save("game", "button", preset="bar-confirm-background",
                        params={"glow": 0.75, "tint": [0.5, 0.25, 1.0], "node_size": [120, 40]})
    assert (done["file"], done["written"], done["unchanged"]) == \
        ("client/ui/bar.tscn", ["glow", "tint"], ["node_size"])
    after = scene.read_text(encoding="utf-8")
    assert after == before.replace("shader_parameter/glow = 0.25", "shader_parameter/glow = 0.75") \
        .replace("Color(1, 0.8, 0.4, 1)", "Color(0.5, 0.25, 1, 1)")
    # The use is read again as it now is.
    root, found, entry = lookdev._find("game", "button")
    shader = next(s for s in found["shaders"] if s["path"] == entry["file"])
    use = lookdev._presets(root, found, shader, entry["res_path"])[0]
    assert use["params"]["glow"] == 0.75 and use["params"]["tint"] == [0.5, 0.25, 1.0, 1.0]

    # The `[resource]` of a `.tres`: the setting it did not carry is appended.
    lookdev.save("game", "button", preset="button-bright-0", params={"node_size": [10, 20.5]})
    material = (game / "client" / "style" / "button_bright.tres").read_text(encoding="utf-8")
    assert material.endswith("shader_parameter/active = true\n"
                             "shader_parameter/node_size = Vector2(10, 20.5)\n")


def test_saving_the_shader_values_writes_its_defaults(game: Path) -> None:
    source = game / "client" / "shaders" / "button.gdshader"
    done = lookdev.save("game", "button", preset="default",
                        params={"glow": 1, "tint": [0.1, 0.2, 0.3, 0.5], "node_size": [64, 30]})
    assert done["written"] == ["glow", "tint", "node_size"] and done["label"] == "Shader values"
    text = source.read_text(encoding="utf-8")
    assert "uniform float glow : hint_range(0.0, 1.0) = 1.0;" in text, "always a float"
    assert "uniform vec4 tint : source_color = vec4(0.1, 0.2, 0.3, 0.5);" in text
    assert "uniform vec2 node_size = vec2(64.0, 30.0);" in text
    assert text.endswith(SHADER[SHADER.index("void fragment"):]), "the rest does not move"


def test_save_refuses_what_cannot_be_written(game: Path) -> None:
    scene = game / "client" / "ui" / "bar.tscn"
    before = scene.read_text(encoding="utf-8")
    for params, message in (({"missing": 1.0}, "not a setting"),
                            ({"glow": "strong"}, "a number"),
                            ({"glow": float("nan")}, "not finite"),
                            ({"node_size": [1, 2, 3]}, "2 components"),
                            ({}, "no setting")):
        with pytest.raises(ServiceError, match=message):
            lookdev.save("game", "button", preset="bar-confirm-background", params=params)
    with pytest.raises(NotFound):
        lookdev.save("game", "button", preset="unknown", params={"glow": 0.1})
    assert scene.read_text(encoding="utf-8") == before, "a refusal writes nothing"


def test_the_game_screen_tells_how_a_device_shows_it(game: Path) -> None:
    screen = lookdev._target("game", "button", fresh=True)["screen"]
    assert {key: screen[key] for key in ("width", "height", "stretch", "aspect")} == \
        {"width": 320, "height": 240, "stretch": "disabled", "aspect": "keep"}
    # No orientation declared: the handhelds are held like the base size, wide.
    assert [(d["id"], d["width"], d["height"]) for d in screen["devices"]] == \
        [("phone", 2400, 1080), ("tablet", 2048, 1536), ("desktop", 1920, 1080)]
    with pytest.raises(ServiceError, match="unknown device"):
        lookdev.frame("game", "button", device="watch")


def test_a_device_screen_follows_the_game_stretch_settings() -> None:
    upright = {"width": 720, "height": 1280, "stretch": "canvas_items", "aspect": "keep"}
    phone = lookdev._layout(upright, 1080, 2400)
    assert phone["size"] == [1080, 2400] and phone["logical"] == pytest.approx([720, 1600])
    assert phone["content"] == pytest.approx([0, 160, 720, 1280]), "bars above and below"
    desktop = lookdev._layout(upright, 1920, 1080)
    assert desktop["content"][0] == pytest.approx((1920 / (1080 / 1280) - 720) / 2), \
        "bars on both sides"
    assert lookdev._layout({**upright, "aspect": "expand"}, 1920, 1080)["content"] == \
        pytest.approx([0, 0, 1280 * 1920 / 1080, 1280]), "the game widens to the screen"
    assert lookdev._layout({**upright, "aspect": "keep_width"}, 1080, 2400)["content"] == \
        pytest.approx([0, 0, 720, 1600]), "the game grows taller"
    assert lookdev._layout({**upright, "stretch": "viewport"}, 1080, 2400)["size"] == [720, 1600], \
        "drawn at the base size, enlarged by the screen"
    assert lookdev._layout({**upright, "stretch": "disabled"}, 1080, 2400)["content"] == \
        [0, 0, 1080, 2400], "no scaling: the screen's pixels are the game's units"
    assert [d["width"] for d in lookdev._devices(upright, "1")] == [1080, 1536, 1920]
    assert [d["width"] for d in lookdev._devices(upright, "0")] == [2400, 2048, 1920], \
        "a game held in landscape turns the handhelds"
    assert lookdev._devices(upright, "")[0]["height"] == 2400, "upright like its base size"

@BENCH
def test_the_bench_renders_the_shader_with_the_game_settings(game: Path) -> None:
    detail = lookdev.specimen("game", "button")
    assert detail["bench"]["ok"], detail["bench"]
    names = {u["name"]: u for u in detail["uniform_list"]}
    assert names["glow"]["default"] == pytest.approx(0.5), "the settings come from Godot"
    assert detail["size"] == [120, 40], "the size of the game's use"

    shot = lookdev.frame("game", "button", scale=1)
    with Image.open(io.BytesIO(shot["image"])) as image:
        assert image.size == (120, 40)
        r, g, b, _ = image.convert("RGBA").getpixel((60, 20))
    # tint (1, 0.8, 0.4) x (0.5 + 0.25): the game's use, not the shader's default.
    assert (r, g, b) == pytest.approx((191, 153, 77), abs=4)
    red = lookdev.frame("game", "button", params={"tint": [1, 0, 0, 1], "glow": 0.5})
    with Image.open(io.BytesIO(red["image"])) as image:
        assert image.convert("RGB").getpixel((60, 20)) == pytest.approx((255, 0, 0), abs=4)

    # The grid asks for its thumbnails together: two specimens do not cross on
    # the bench (each is laid then rendered as one unit).
    from concurrent.futures import ThreadPoolExecutor

    asked = ["button", "green"] * 4
    with ThreadPoolExecutor(max_workers=8) as pool:
        shots = list(pool.map(lambda name: lookdev.frame("game", name, params={
            "tint": [1, 0, 0, 1], "glow": 0.5} if name == "button" else None), asked))
    for name, shot in zip(asked, shots, strict=True):
        with Image.open(io.BytesIO(shot["image"])) as image:
            r, g, _ = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
        assert (r > 200 and g < 50) if name == "button" else (g > 200 and r < 50), name


@BENCH
def test_an_agent_looks_at_a_specimen_and_presents_it(game: Path) -> None:
    from mcp.server.mcpserver import Image as McpImage

    from gamestudio import mcp_server

    text, image = mcp_server.lookdev_look("game", "button", params={"glow": 1.0})
    assert text["specimen"] == "button" and isinstance(image, McpImage)
    laid = mcp_server.lookdev_set("game", "button", shape="plane")
    assert laid["shape"] == "plane"
    assert [entry["id"] for entry in mcp_server.lookdev("game")["specimens"]].count("button") == 1


# A strip along the bottom of the game, as a HUD bar is anchored.
HUD = """[gd_scene load_steps=3 format=3]

[ext_resource type="Shader" path="res://shaders/button.gdshader" id="1_s"]

[sub_resource type="ShaderMaterial" id="mat_hud"]
shader = ExtResource("1_s")
shader_parameter/tint = Color(0, 1, 0, 1)
shader_parameter/glow = 0.5

[node name="Hud" type="Control"]
layout_mode = 3
anchors_preset = 12
anchor_top = 1.0
anchor_right = 1.0
anchor_bottom = 1.0
offset_top = -40.0
grow_horizontal = 2
grow_vertical = 0

[node name="Strip" type="ColorRect" parent="."]
material = SubResource("mat_hud")
layout_mode = 1
anchors_preset = 15
anchor_right = 1.0
anchor_bottom = 1.0
grow_horizontal = 2
grow_vertical = 2
"""


@BENCH
def test_a_device_shows_the_use_where_the_game_places_it(game: Path) -> None:
    (game / "client" / "ui" / "hud.tscn").write_text(HUD, encoding="utf-8")
    project = game / "client" / "project.godot"
    project.write_text(project.read_text(encoding="utf-8")
                       + '\nwindow/stretch/mode="canvas_items"\nwindow/handheld/orientation=1\n',
                       encoding="utf-8")
    lookdev._target("game", "button", fresh=True)
    green, red, black = (0, 255, 0), (255, 0, 0), (0, 0, 0)

    def look(device: str, **kwargs) -> Image.Image:
        shot = lookdev.frame("game", "button", preset="hud-strip", device=device,
                             background="#102030", **kwargs)
        return Image.open(io.BytesIO(shot["image"])).convert("RGB")

    # 320 x 240 on an upright phone: scale 3.375, the game's area between two bars.
    with look("phone") as phone:
        assert phone.size == (1080, 2400)
        assert phone.getpixel((540, 100)) == pytest.approx(black, abs=6), "bar above"
        assert phone.getpixel((540, 1000)) == pytest.approx((16, 32, 48), abs=6), "the game's area"
        assert phone.getpixel((540, 1540)) == pytest.approx(green, abs=6), "the strip, at the bottom"
    with look("phone", params={"tint": [1, 0, 0, 1]}) as tuned:
        assert tuned.getpixel((540, 1540)) == pytest.approx(red, abs=6), "the settings apply"
    # On a desktop monitor the same game keeps its proportions: bars on the sides.
    with look("desktop") as desktop:
        assert desktop.size == (1920, 1080)
        assert desktop.getpixel((100, 540)) == pytest.approx(black, abs=6)
        assert desktop.getpixel((960, 1040)) == pytest.approx(green, abs=6)
    # Back on the template, the studio view is the same as before.
    with Image.open(io.BytesIO(lookdev.frame("game", "button", scale=1)["image"])) as image:
        assert image.size == (120, 40)


def _pixel(shot: dict) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(shot["image"])) as image:
        return image.convert("RGB").getpixel((image.width // 2, image.height // 2))


@BENCH
def test_the_bench_lives_as_long_as_the_studio_holds_it(game: Path) -> None:
    """The bench lives as long as its connection with the studio is open.

    Godot's `OS.is_process_running` only sees processes Godot launched itself,
    so the bench cannot watch the studio's process: a bench that relied on it
    would stop after a second and be restarted on every image.
    """
    import time

    lookdev.frame("game", "button")
    [bench] = lookdev._BENCHES.values()
    process = bench.process
    time.sleep(2.5)
    lookdev.frame("game", "button")
    assert bench.process is process and process.poll() is None, "the bench did not restart"


@BENCH
def test_a_restarted_bench_lays_its_specimen_again_silently(game: Path) -> None:
    import os
    import signal

    lookdev.frame("game", "button", params={"tint": [1, 0, 0, 1], "glow": 0.5})
    [bench] = lookdev._BENCHES.values()
    os.killpg(bench.process.pid, signal.SIGKILL)
    bench.process.wait(timeout=10)
    # The bench fell: the next image restarts it, lays the specimen again and
    # renders -- never "nothing on the bench".
    red = lookdev.frame("game", "button", params={"tint": [1, 0, 0, 1], "glow": 0.5})
    assert _pixel(red) == pytest.approx((255, 0, 0), abs=6)


@BENCH
def test_a_bench_whose_studio_leaves_stops(game: Path) -> None:
    lookdev.frame("game", "button")
    [bench] = lookdev._BENCHES.values()
    process = bench.process
    # The studio leaves without a word (killed): its connection closes, and
    # nobody stops the bench.
    bench.file.close()
    bench.sock.close()
    process.wait(timeout=10)


@BENCH
def test_an_image_does_not_reread_the_game(game: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gamestudio.service import survey

    lookdev.specimen("game", "button")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("an image reread the whole game")

    monkeypatch.setattr(survey, "game_map", forbidden)
    for yaw in (0, 30, 60):
        lookdev.frame("game", "button", yaw=yaw)


@BENCH
def test_what_an_image_does_not_repeat_goes_back_to_the_game(game: Path) -> None:
    original = _pixel(lookdev.frame("game", "button"))
    assert original == pytest.approx((191, 153, 77), abs=6)
    assert _pixel(lookdev.frame("game", "button", params={"tint": [0, 0, 1, 1]})) \
        == pytest.approx((0, 0, 191), abs=6)
    # "Back to the game": no setting, the use's color comes back.
    assert _pixel(lookdev.frame("game", "button")) == pytest.approx(original, abs=6)


@BENCH
def test_a_shader_changed_in_the_game_shows_at_once(game: Path) -> None:
    import os

    assert _pixel(lookdev.frame("game", "green")) == pytest.approx((0, 255, 0), abs=6)
    source = game / "client" / "shaders" / "green.gdshader"
    source.write_text(GREEN.replace("vec4(0.0, 1.0, 0.0, 1.0)", "vec4(1.0, 0.0, 1.0, 1.0)"),
                      encoding="utf-8")
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert _pixel(lookdev.frame("game", "green")) == pytest.approx((255, 0, 255), abs=6)


@BENCH
def test_formats_and_groups(game: Path) -> None:
    live = lookdev.frame("game", "button", background="#102030")
    assert live["image"][:3] == b"\xff\xd8\xff" and live["media_type"] == "image/jpeg"
    thumbnail = lookdev.frame("game", "button", format="webp")
    assert thumbnail["image"][8:12] == b"WEBP"
    # Exact, for a sky behind the page: no compression smears it.
    exact = lookdev.frame("game", "button", format="png")
    assert exact["image"][:8] == b"\x89PNG\r\n\x1a\n" and exact["media_type"] == "image/png"
    with pytest.raises(ServiceError, match="unknown format"):
        lookdev.frame("game", "button", format="gif")
    with pytest.raises(ServiceError, match="unreadable background"):
        lookdev.frame("game", "button", background="blue")

    groups = {u["name"]: u["group"] for u in lookdev.specimen("game", "groups")["uniform_list"]}
    assert groups == {"hue": "color", "strength": ""}


def _hangs_up(connection) -> bool:
    """True if the bench closes the connection without answering."""
    connection.settimeout(10)
    try:
        return connection.recv(4096) == b""
    except ConnectionResetError:
        return True


@BENCH
def test_the_bench_only_obeys_whoever_gives_its_secret(game: Path, tmp_path: Path) -> None:
    """Any process on the machine can join the socket; `stage` would run its script.

    The bench is launched by hand so that an intruder connects before the
    studio: it hangs up, without answering, on whoever does not give the launch
    secret, and keeps waiting for the studio.
    """
    import json
    import os
    import signal
    import socket
    import subprocess
    import threading

    from gamestudio.net import find_free_port
    from gamestudio.service import renders

    base = game / "client"
    binary = renders._godot_binary()
    env = {**os.environ, "XDG_DATA_HOME": str(tmp_path / "user")}
    renders._ensure_imported(binary, base, env)
    _, prefix = renders.displays(256, 256)[0]
    port = find_free_port("127.0.0.1", lookdev.PORT_BASE + 600)
    bench = subprocess.Popen(
        [*prefix, binary, "--path", str(base), "--script", str(lookdev.SCRIPT), "--",
         f"--gs-port={port}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**env, lookdev.SECRET_ENV: "the-right-secret"}, start_new_session=True)
    try:
        for line in bench.stdout:
            if "GAMESTUDIO_BENCH:" in line:
                assert "READY" in line, line
                break
        threading.Thread(target=bench.stdout.read, daemon=True).start()

        trace = tmp_path / "executed"
        trap = tmp_path / "trap.gd"
        trap.write_text("extends RefCounted\nfunc build():\n"
                        f"\tFileAccess.open({str(trace)!r}, FileAccess.WRITE).store_string('x')\n"
                        "\treturn Node3D.new()\n")
        with socket.create_connection(("127.0.0.1", port)) as intruder:
            intruder.sendall(json.dumps({"op": "stage", "setup": str(trap)}).encode() + b"\n")
            assert _hangs_up(intruder)
        assert not trace.exists(), "the bench ran a stranger's script"
        with socket.create_connection(("127.0.0.1", port)) as forger:
            forger.sendall(b'{"op": "hello", "secret": "another-one"}\n')
            assert _hangs_up(forger)

        with socket.create_connection(("127.0.0.1", port), timeout=10) as studio:
            channel = studio.makefile("rwb")
            channel.write(b'{"op": "hello", "secret": "the-right-secret"}\n')
            channel.flush()
            assert json.loads(channel.readline()) == {"ok": True}
            channel.write(b'{"op": "ping"}\n')
            channel.flush()
            assert json.loads(channel.readline())["ok"] is True
            channel.close()
        # With the studio gone, the bench stops: nobody else will drive it.
        bench.wait(timeout=15)
    finally:
        if bench.poll() is None:
            os.killpg(bench.pid, signal.SIGKILL)
            bench.wait(timeout=10)
