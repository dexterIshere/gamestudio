"""Visual effects: a safe language, exact loops, a filed build.

What these tests protect: an effect spec can only compute an image; a loop
closes without a jump; a build goes into the library and into Godot, and a
wrong spec is never written.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from gamestudio.config import Settings
from gamestudio.service import build, effects, using
from gamestudio.service.context import Studio
from gamestudio.service.errors import ServiceError
from gamestudio.vfx import expr, noise, render, sim
from gamestudio.vfx.spec import SpecError, load


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context")
    with using(build(settings)) as current:
        yield current


# ------------------------------------------------------------------ language

@pytest.mark.parametrize("code", [
    '__import__("os")', "x.real", "[1, 2]", "lambda: 1", "open('f')",
    "x[0]", "fbm = 1\nfbm", "import os\n1",
])
def test_the_language_refuses_anything_but_computation(code: str) -> None:
    with pytest.raises(expr.ExprError):
        expr.compile_code(code)


def test_the_language_computes_over_the_whole_grid() -> None:
    program = expr.compile_code("px, py = rotate(x, y, 0.25)\nfill(circle(px, py, 0.5), 0.01)")
    grid = sim.grid_env(32, 32)
    value = program.run({**grid, "t": np.float32(0)}, expr.EvalContext(shape=(32, 32)))
    assert value.shape == (32, 32)
    assert value[16, 16] == pytest.approx(1.0)
    assert value[0, 0] == pytest.approx(0.0)


def test_the_reference_only_cites_existing_functions() -> None:
    text = effects.reference()
    for name in ("fbm", "worley_edge", "curl", "ramp", "tex", "env", "kaleido"):
        assert f"`{name}(" in text
    assert set(expr.FUNCTIONS) >= {"noise", "ridged", "turb", "fill", "glow"}


def test_a_tiled_noise_repeats_exactly() -> None:
    y, x = np.mgrid[0:64, 0:64] / 16.0
    base = noise.fractal((x, y, x * 0), periods=(4, 4, 2))
    shifted = noise.fractal((x + 4, y, x * 0 + 2), periods=(4, 4, 2))
    assert np.abs(base - shifted).max() < 1e-5


# --------------------------------------------------------------------- spec

def test_a_wrong_spec_says_where_it_is_wrong() -> None:
    with pytest.raises(SpecError, match=r"layers\.fire\.blend"):
        load("layers:\n  - name: fire\n    alpha: '1'\n    blend: glowing\n", "fire")
    # Each refusal names what is wrong: the unknown function, the unknown key.
    with pytest.raises(SpecError, match="burn"):
        load("layers:\n  - name: fire\n    alpha: burn(x)\n", "fire")
    with pytest.raises(SpecError, match="height_px"):
        load("height_px: 3\nlayers:\n  - name: a\n    alpha: '1'\n", "fire")


# -------------------------------------------------------------------- render

SPEC = """\
size: 48
frames: 8
fps: 12
loop: true
seed: 2
defs: |
  n = fbm(x * 2, y * 2 - t * 2, ty=2)
layers:
  - name: core
    alpha: fill(circle(x, y + n * 0.2, 0.5), 0.1)
    color: {ramp: ["#300", "#f80", "#fff"]}
    blend: add
  - name: embers
    type: particles
    count: 30
    position: (rand2 - 0.5, -0.5)
    velocity: (0, 1)
    size: 0.05
    color: "#ffcc66"
    blend: add
post:
  - bloom: {threshold: 0.6, radius: 3, strength: 0.5}
"""


def test_an_effect_renders_to_a_sheet_with_its_pivot() -> None:
    spec = load(SPEC, "fire")
    rendered = render.render(spec)
    assert rendered.frames.shape == (8, 48, 48, 4)
    assert rendered.frames[..., 3].max() > 0
    sheet = render.assemble(rendered)
    assert sheet.meta["columns"] * sheet.meta["rows"] >= 8
    assert sheet.meta["origin"] == [24.0, 24.0]
    assert sheet.meta["blend"] == "add"
    assert len(sheet.frames) == 8


def test_particles_loop_seamlessly() -> None:
    """A particle born at the end of the clip is already there, at the right age, at frame 0."""
    spec = load(SPEC, "fire")
    layer = spec.layers[1]
    frames = sim.particle_frames(layer, spec, expr.EvalContext(seed=spec.seed))
    # In a loop, the number of living particles is stationary: frame 0 is not
    # empty, it looks like the others.
    counts = [len(state.get("x", [])) for state in frames]
    assert counts[0] > 0
    assert max(counts) - min(counts) <= max(3, int(0.35 * max(counts)))


def test_a_fluid_rises_and_stays_finite() -> None:
    spec = load("""\
size: 32
frames: 4
loop: false
layers:
  - name: gas
    type: fluid
    grid: 24
    source: 8 * fill(circle(x, y + 0.6, 0.2), 0.05)
    buoyancy: 3
""", "gas")
    states = sim.simulate_fluid(spec.layers[0], spec, expr.EvalContext())
    last = states[-1]
    assert np.isfinite(last["vy"]).all()
    assert last["vy"].max() > 0          # buoyancy pushes upwards
    rows = np.nonzero(last["density"].sum(axis=1) > 1e-3)[0]
    assert rows.min() < 24 * 0.8         # the density left its source, at the bottom


# ------------------------------------------------------------------ service

def test_an_effect_is_created_previewed_and_filed(isolated_studio: Studio) -> None:
    created = effects.create_effect("game", "aura")
    assert Path(created["path"]).parent.name == "effects"
    assert created["valid"]

    with pytest.raises(ServiceError, match="invalid spec"):
        effects.write_effect_spec("game", "aura", "layers: []\n")
    # The refused spec overwrote nothing.
    assert "breathe" in effects.read_effect("game", "aura")["spec"]

    preview = effects.preview_effect("game", "aura", scale=0.25)
    assert Path(preview["contact_sheet"]).is_file()
    assert preview["sheet"].startswith("data:image/png;base64,")

    effects.write_effect_spec("game", "aura", SPEC)
    built = effects.build_effect("game", "aura", validate=False)
    library = Path(built["library"])
    assert (library / "aura.png").is_file()
    assert (library / "contact.png").is_file()
    assert json.loads((library / "aura.json").read_text())["frames"] == 8
    assert len(list((library / "frames").glob("*.png"))) == 8
    scene = Path(built["godot"]["scene"])
    assert scene.is_file() and "AnimatedSprite2D" in scene.read_text()
    assert "blend_mode = 1" in scene.read_text()

    listed = effects.effects("game")
    assert listed[0]["name"] == "aura" and listed[0]["build"]["build"] == built["build"]


def test_two_one_shot_effects_each_keep_their_empty_frames(isolated_studio: Studio) -> None:
    """The store is content-addressed: two identical empty frames must not get
    mixed up from one effect to the other."""
    blank = "size: 16\nframes: 3\nloop: false\nlayers:\n  - name: a\n    alpha: '0'\n"
    for name in ("empty-a", "empty-b"):
        effects.create_effect("game", name, blank)
        built = effects.build_effect("game", name, validate=False)
        assert len(list((Path(built["library"]) / "frames").glob("*.png"))) == 3
    first = Path(effects.effects("game")[0]["build"]["library"])
    assert len(list((first / "frames").glob("*.png"))) == 3


def test_an_effect_does_not_create_a_godot_project_in_a_game(tmp_path: Path) -> None:
    """A game folder without `project.godot` is refused before any render."""
    from gamestudio.service import folders

    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    root = tmp_path / "my-game"
    root.mkdir()
    settings = Settings(data_dir=tmp_path / "data", project_root=studio_dir,
                        context_dir=tmp_path / "context")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        effects.create_effect("game", "aura", SPEC)

        with pytest.raises(ServiceError, match=r"project\.godot"):
            effects.build_effect("game", "aura", validate=False)
        assert not (root / "project.godot").exists()
        assert effects.effects("game")[0]["build"] is None

        (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
        built = effects.build_effect("game", "aura", validate=False)
        assert Path(built["godot"]["scene"]).is_relative_to(root / "effects" / "aura")


@pytest.mark.skipif(shutil.which("godot") is None and shutil.which("godot4") is None,
                    reason="Godot missing")
def test_godot_loads_an_effect_scene(isolated_studio: Studio) -> None:
    spec = SPEC.replace("post:", "godot:\n  particles: {amount: 4}\npost:")
    effects.create_effect("game", "spark", spec)
    built = effects.build_effect("game", "spark")
    assert built["godot"]["validation"] == "ok", built["godot"]["output"]
    assert built["godot"]["res_particles"].endswith("spark_particles.tscn")


EXAMPLES = Path(__file__).resolve().parents[1] / ".claude/skills/vfx/references/examples.md"


def _examples() -> list[tuple[str, str]]:
    import re

    text = EXAMPLES.read_text(encoding="utf-8")
    return re.findall(r"`([a-z0-9_-]+)`\n\n```yaml\n(.*?)```", text, re.S)


def test_the_skill_examples_render_as_written() -> None:
    """A documentation example that no longer renders lies to the agent reading it."""
    examples = _examples()
    assert len(examples) >= 8
    for name, body in examples:
        rendered = render.render(load(body, name), scale=0.25)
        assert rendered.frames[..., 3].max() > 0, name
