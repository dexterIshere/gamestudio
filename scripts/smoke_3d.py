"""End-to-end test of the 3D pipeline, without calling Runware or an agent.

A rigged and animated entity made by Blender stands in for what the rigging
and animating agent delivers: a character (canonical skeleton, an `idle_loop`
cycle and a `wave` gesture) and a machine (no bone, a wheel spinning in a
loop). The rest is the real path: GLB inventory, sprite rendering -- one sheet
per animation and per direction, a single framing for all, under the names
Godot gives them, looping stated per sheet in the atlas -- copying the GLBs
into a throwaway Godot project, headless validation of every animation,
looping cycles.

    python scripts/smoke_3d.py [output_folder]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gamestudio.blender.run import BlenderError, run_script
from gamestudio.config import Settings
from gamestudio.domain.gltf import inspect_rig
from gamestudio.domain.skeleton import Archetype, normalize
from gamestudio.pipeline.base import Context
from gamestudio.pipeline.steps.export import ExportGodotMesh, create_godot_project
from gamestudio.pipeline.steps.render import RenderSprites
from gamestudio.store.assets import AssetStore
from gamestudio.store.db import Database

# What each fixture carries (the names in the file), and what Godot makes of
# them: the `_loop` suffix drops, and the animation loops.
EXPECTED = {"hero": ["idle_loop", "wave"], "mill": ["spin_loop"]}
IN_GODOT = {"hero": {"idle": True, "wave": False}, "mill": {"spin": True}}


def main(argv: list[str]) -> int:
    positional = [a for a in argv[1:] if not a.startswith("--")]
    root = Path(positional[0]) if positional else Path(tempfile.mkdtemp(prefix="gamestudio-3d-"))
    work = root / "fixtures"
    work.mkdir(parents=True, exist_ok=True)
    godot_project = root / "godot"

    settings = Settings(data_dir=root / "data")
    settings.ensure_dirs()
    ctx = Context(project="smoke3d", settings=settings,
                  db=Database(settings.db_path), store=AssetStore(settings.assets_dir))
    print(f"work folder: {root}")

    meshes: dict[str, str] = {}
    try:
        # 1. What the agent would deliver: a rigged being, a machine animated by parts.
        for name, kind in (("hero", "humanoid"), ("mill", "machine")):
            fixture = work / f"{name}.glb"
            result = run_script("bl_make_fixture.py", {"output": str(fixture), "kind": kind},
                                blender_bin=settings.blender_bin, timeout=600)
            asset = ctx.store.put_file(fixture, kind="mesh", meta={"role": "imported_mesh"})
            ctx.db.save_asset(asset)
            meshes[name] = asset.id
            print(f"1/5  delivery   {name:<12} {len(result['bones'])} bones, "
                  f"{result['vertices']} vertices, {', '.join(result['animations'])}")

        # 2. The inventory the import makes: without Blender, from the GLB's JSON.
        for name, asset_id in meshes.items():
            info = inspect_rig(ctx.store.path_for(asset_id))
            canonical = len(set(normalize(info.bones, Archetype.BIPED).mapping.values()))
            if info.animation_names != EXPECTED[name]:
                print(f"2/5  inventory  {name:<12} FAILED: {info.animation_names}")
                return 1
            print(f"2/5  inventory  {name:<12} {len(info.bones)} bones ({canonical} canonical), "
                  f"animations {', '.join(info.animation_names)}")

        # 3. The render: every animation, a single framing for all.
        rendered = RenderSprites(meshes["hero"], "hero", directions=8, size=96,
                                 max_frames=6, fps=12).execute(ctx)
        sheets = [asset for asset in rendered.assets if asset.kind == "spritesheet"]
        sizes = {(a.meta["frame_width"], a.meta["frame_height"]) for a in sheets}
        clips = rendered.data["clips"]
        if clips != list(IN_GODOT["hero"]) or len(sheets) != 16 or len(sizes) != 1:
            print(f"3/5  render                 FAILED: {clips}, {len(sheets)} sheets, "
                  f"{len(sizes)} framing(s)")
            return 1
        # The atlas states each sheet's looping, as Godot reads it: idle loops,
        # the gesture does not.
        atlas = next(asset for asset in rendered.assets if asset.meta.get("role") == "atlas")
        loops = {sheet["clip"]: sheet["loop"] for sheet in
                 json.loads(ctx.store.path_for(atlas.id).read_text(encoding="utf-8"))["sheets"]}
        if loops != IN_GODOT["hero"]:
            print(f"3/5  render                 FAILED: atlas loops {loops}")
            return 1
        # A cycle keyed 1 -> 25 (the wheel returns to 0 degrees) renders over 24
        # frames: the 25th would repeat the first where the loop wraps.
        machine = RenderSprites(meshes["mill"], "mill", directions=1, size=96,
                                fps=12).execute(ctx)
        if machine.data["frames"] != {"spin_loop": 24}:
            print(f"3/5  render                 FAILED: mill cycle {machine.data['frames']}")
            return 1
        print(f"3/5  render                 hero: {len(sheets)} sheets "
              f"({', '.join(clips)} x 8 directions), frame {rendered.data['frame_size']}; "
              f"mill: {machine.data['clips']} over 24 frames, "
              f"engine {rendered.data['engine']}, loops {loops}")

        # 4. Godot export: the GLBs, as the workbench and the import copy them.
        create_godot_project(godot_project, "smoke3d")
        exported = {name: ExportGodotMesh(name, asset_id, godot_project=godot_project)
                    .execute(ctx).data["res_path"] for name, asset_id in meshes.items()}
        print(f"4/5  Godot export           {', '.join(exported.values())}")
    except BlenderError as exc:
        print(f"Blender FAILED: {exc}")
        print(exc.details(20))
        return 1
    finally:
        ctx.close()

    binary = shutil.which("godot") or shutil.which("godot4")
    if binary is None:
        print("5/5  Godot validation       skipped (binary not on the PATH)")
        return 0

    check = subprocess.run(
        [binary, "--headless", "--path", str(godot_project), "--import"],
        capture_output=True, text=True, timeout=900,
    )
    errors = [line for line in (check.stdout + check.stderr).splitlines()
              if "ERROR" in line and "shader" not in line.lower()]
    if errors:
        print("5/5  Godot validation       FAILED on import")
        for line in errors[:5]:
            print(f"       {line}")
        return 1

    validator = Path(__file__).parent / "validate_godot_scene.gd"
    shutil.copyfile(validator, godot_project / validator.name)
    failures = 0
    for name, res_path in exported.items():
        result = subprocess.run(
            [binary, "--headless", "--path", str(godot_project),
             "--script", f"res://{validator.name}", "--", res_path],
            capture_output=True, text=True, timeout=600,
        )
        output = result.stdout + result.stderr
        played = {}
        for line in output.splitlines():
            if any(marker in line for marker in ("MeshInstance3D", "animation ", "FAILED")):
                print(f"       {line.strip()}")
            if line.strip().startswith("animation "):
                played[line.split()[1].rstrip(":")] = "loop=true" in line
        if "RESULT: OK" not in output or played != IN_GODOT[name]:
            failures += 1
            print(f"       ! {name}: expected {IN_GODOT[name]}, played {played}")
    if failures:
        print("5/5  Godot validation       FAILED")
        return 1
    print("5/5  Godot validation       resources imported, every animation played")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
