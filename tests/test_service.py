"""Service layer: the base shared by the CLI, the HTTP API and the MCP server.

What is checked here fits in three rules: a caller's mistake comes back as a
typed `ServiceError` (never a stack trace), a paid operation does not start
without explicit consent, and none of it calls the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import declare_recipe, figure_png

from gamestudio import service
from gamestudio.config import Settings
from gamestudio.service import NotFound, PaymentRequired, ServiceError

WIDTH, HEIGHT = 192, 288

RECIPE = """\
version: 1
project: trial
style:
  id: trial-style
  name: Trial style
  prompt_prefix: "test"
defaults:
  archetype: biped
  pipelines: [mesh3d]
characters:
  - id: hero
    name: Hero
    subject: "a test hero"
"""


@pytest.fixture()
def studio(tmp_path: Path):
    """A complete studio on a temporary folder, swapped in for the test."""
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    declare_recipe(settings, "trial", RECIPE)
    with service.using(service.build(settings)) as current:
        yield current


@pytest.fixture()
def silhouette(studio):
    """A character image in the store, like a generated concept."""
    path = studio.settings.data_dir / "character.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(figure_png(WIDTH, HEIGHT))
    trial = studio.space("trial")
    asset = trial.store.put_file(path, kind="image")
    trial.db.save_asset(asset)
    return asset.id


# ------------------------------------------------------------------- catalogue


def test_health_describes_the_environment(studio):
    health = service.catalog.health()
    assert health["data_dir"] == str(studio.settings.data_dir)
    assert "queue" in health


def test_recipes_are_read_in_each_project_folder(studio):
    entries = service.catalog.list_recipes()
    assert [entry["project"] for entry in entries] == ["trial"]
    assert "error" not in entries[0], "the test recipe must be valid"
    assert entries[0]["characters"] == ["hero"]


def test_a_broken_recipe_does_not_prevent_reading_the_others(studio):
    declare_recipe(studio.settings, "broken", "project: [")
    entries = {entry["project"]: entry for entry in service.catalog.list_recipes()}
    assert "error" in entries["broken"]
    assert "error" not in entries["trial"]


def test_an_unknown_project_is_a_readable_error(studio):
    with pytest.raises(NotFound, match="missing"):
        service.catalog.find_recipe("missing")


def test_an_unknown_asset_is_a_readable_error(studio):
    with pytest.raises(NotFound):
        service.catalog.asset_info("0" * 32)


# ------------------------------------------------------------------ production


def test_resolve_recipe_accepts_a_name_or_a_path(studio):
    by_name = service.produce.resolve_recipe("trial")
    by_path = service.produce.resolve_recipe(
        str(studio.space("trial").paths.recipe))
    assert by_name is not None and by_path is not None
    assert by_name[0] == by_path[0]


def test_a_build_without_consent_is_refused(studio):
    with pytest.raises(PaymentRequired):
        service.produce.build("trial")
    assert service.jobs.status()["states"] == {}, "nothing may be queued"


def test_a_3d_entity_without_consent_is_refused(studio):
    with pytest.raises(PaymentRequired):
        service.produce.create_entity("a dragon")


# Each paid service operation, called without consent then with it: the
# refusal states the estimated amount, and nothing is queued while consent is
# missing.
PAID = {
    "image": lambda confirm: service.produce.generate_image(
        "a test", recipe="trial", count=2, confirm=confirm),
    "entity": lambda confirm: service.produce.create_entity(
        "a dragon", recipe="trial", confirm=confirm),
    "build": lambda confirm: service.produce.build("trial", confirm=confirm),
    "exploration": lambda confirm: service.produce.explore_style(
        "trial", "a blacksmith", 8, confirm),
    "training": lambda confirm: service.produce.train_style(
        "trial", [f"{index:032x}" for index in range(10)], confirm=confirm),
}


@pytest.mark.parametrize("operation", sorted(PAID))
def test_a_paid_operation_states_its_amount_and_waits_for_consent(studio, operation):
    with pytest.raises(PaymentRequired, match=r"~\$\d+\.\d+") as refusal:
        PAID[operation](False)
    assert refusal.value.status == 402
    assert "confirm=true" in str(refusal.value)
    assert service.jobs.status()["states"] == {}, "nothing may be queued"

    result = PAID[operation](True)
    assert result["queued"] and result["project"] == "trial"
    assert service.jobs.status()["states"] == {"pending": len(result["queued"])}


def test_an_image_batch_amount_follows_the_model_and_the_count(studio):
    with pytest.raises(PaymentRequired, match=r"~\$0\.024 for 4 image"):
        service.produce.generate_image("a test", count=4)
    with pytest.raises(PaymentRequired, match=r"~\$0\.005 for 4 image"):
        service.produce.generate_image("a test", count=4, model="runware:100@1")
    with pytest.raises(PaymentRequired, match="not in the catalogue"):
        service.produce.generate_image("a test", model="civitai:1@1")


def test_an_entity_amount_follows_its_mesh_model(studio):
    with pytest.raises(PaymentRequired, match=r"~\$0\.150"):
        service.produce.create_entity("a dragon", reference_asset_id="0" * 32,
                                      mesh_model="microsoft:trellis-2@4b")
    with pytest.raises(PaymentRequired, match=r"~\$0\.406"):
        service.produce.create_entity("a dragon")


def test_a_wrong_parameter_is_refused_before_asking_for_consent(studio):
    """Asking for consent only to refuse afterwards would waste the question."""
    with pytest.raises(ServiceError) as refusal:
        service.produce.generate_image("a test", count=40)
    assert not isinstance(refusal.value, PaymentRequired)
    with pytest.raises(ServiceError) as refusal:
        service.produce.explore_style("trial", "   ")
    assert not isinstance(refusal.value, PaymentRequired)


def test_a_build_godot_target_travels_to_the_job(studio, tmp_path):
    result = service.produce.build("trial", godot_project=str(tmp_path / "game"),
                                   confirm=True)
    job = service.jobs.detail(result["queued"][0])
    assert job["payload"]["godot_project"] == str(tmp_path / "game")


def test_a_training_requires_ten_images(studio):
    with pytest.raises(ServiceError, match="10 images"):
        service.produce.train_style("trial", ["a", "b"], confirm=True)


def test_an_entity_is_queued_with_consent(studio):
    result = service.produce.create_entity("a hero", name="hero", recipe="trial",
                                           confirm=True)
    assert len(result["queued"]) == 1
    assert result["project"] == "trial"
    assert service.jobs.status()["states"] == {"pending": 1}


def test_the_face_limit_travels_to_the_job(studio):
    result = service.produce.create_entity("a dragon", face_limit=12000,
                                           recipe="trial", confirm=True)
    job = service.jobs.detail(result["queued"][0])
    assert job["payload"]["face_limit"] == 12000


def test_an_out_of_bounds_face_limit_is_refused(studio):
    with pytest.raises(ServiceError, match="face_limit"):
        service.produce.create_entity("a dragon", face_limit=10, confirm=True)
    assert service.jobs.status()["states"] == {}, "nothing may be queued"


def test_a_character_unknown_to_the_recipe_is_refused(studio):
    with pytest.raises(ServiceError, match="unknown characters"):
        service.produce.build("trial", characters=["ghost"], confirm=True)


def test_an_empty_prompt_is_refused(studio):
    with pytest.raises(ServiceError, match="empty prompt"):
        service.produce.generate_image("   ")


def test_the_sprite_styles_are_described(studio):
    styles = {entry["name"]: entry for entry in service.produce.sprite_styles()}
    assert set(styles) == {"normal", "prerender", "pixel"}
    assert all(entry["label"] and entry["description"] for entry in styles.values())


def test_an_unknown_sprite_style_is_refused(studio, silhouette):
    asset_id = silhouette
    with pytest.raises(ServiceError, match="unknown style"):
        service.produce.render_sprites(asset_id, style="cartoon")


@pytest.mark.parametrize("argument,value", [
    ("directions", 0), ("directions", 99), ("size", 8), ("size", 4096),
    ("elevation", 120.0), ("elevation", -120.0),
])
def test_an_out_of_bounds_framing_is_refused(studio, silhouette, argument, value):
    """An absurd framing would run Blender for nothing: refuse first."""
    asset_id = silhouette
    with pytest.raises(ServiceError):
        service.produce.render_sprites(asset_id, **{argument: value})


def test_a_missing_mesh_is_a_readable_error(studio):
    with pytest.raises(NotFound, match="mesh"):
        service.produce.render_sprites("0" * 32)


def test_a_sprite_render_is_queued(studio, silhouette):
    """The render is local and free: no consent to spend is required."""
    asset_id = silhouette
    result = service.produce.render_sprites(asset_id, name="tower", recipe="trial",
                                            style="pixel", directions=4)
    assert len(result["queued"]) == 1
    assert result["project"] == "trial"
    job = service.jobs.detail(result["queued"][0])
    assert job["kind"] == "render_sprites"
    assert job["payload"]["style"] == "pixel"
    assert job["payload"]["directions"] == 4


# ----------------------------------------------------------------------- queue


def test_the_detail_of_an_unknown_job_is_a_readable_error(studio):
    with pytest.raises(NotFound):
        service.jobs.detail("missing")


def test_each_job_lives_in_its_project_queue(studio):
    """The studio has no queue of its own: reads without a project gather the
    projects' queues, and a job is found again in its own project's database."""
    from gamestudio.domain.models import Job, JobState

    declare_recipe(studio.settings, "other", RECIPE.replace("project: trial", "project: other"))
    studio.space("trial").queue.enqueue("generate_image", {}, project="trial")
    studio.space("other").db.save_job(Job(id="finished", kind="generate_image",
                                          project="other", state=JobState.DONE,
                                          cost_usd=0.25))

    state = service.jobs.status()
    assert state["states"] == {"pending": 1, "done": 1}
    assert state["cost_usd"] == pytest.approx(0.25)
    assert {job["project"] for job in state["recent"]} == {"trial", "other"}
    assert service.catalog.queue_stats() == {"pending": 1, "done": 1}
    assert [job["id"] for job in service.jobs.listing(state="done")] == ["finished"]
    assert service.jobs.detail("finished")["project"] == "other"
    found = studio.find_job("finished")
    assert found is not None and found[0].project == "other"
    assert not (studio.settings.data_dir / "studio.sqlite3").exists()


def test_the_stream_only_emits_on_change(studio):
    """Otherwise the interface would redraw every second for nothing."""
    changes = service.jobs.Changes()
    first = service.jobs.status()
    assert changes.emit(first), "the first state is always sent"
    assert not changes.emit(service.jobs.status()), "an unchanged queue does not emit"

    service.produce.generate_image("a test", recipe="trial", confirm=True)
    assert changes.emit(service.jobs.status()), "a moving queue emits"


# ----------------------------------------------------------------------- poses


def test_the_reference_poses_are_exposed(studio):
    names = {pose["name"] for pose in service.poses.pose_templates()}
    assert "a_pose" in names
    detail = service.poses.pose_template("a_pose", WIDTH, HEIGHT)
    assert len(detail["keypoints"]) == 18
    assert all(0 <= point["x"] <= WIDTH for point in detail["keypoints"])


def test_an_unknown_pose_is_a_readable_error(studio):
    with pytest.raises(NotFound):
        service.poses.pose_template("impossible_pose")


# --------------------------------------------------------------------- library


def test_the_library_can_be_browsed_and_filed(studio, silhouette):
    asset_id = silhouette
    service.library.sync("trial")
    tree = service.library.tree("trial")
    assert tree["project"] == "trial"
    assert Path(tree["root"]).exists()
    # The image placed by hand belongs to no character: nobody holds it, so
    # filing may touch it.
    assert service.library.holders(asset_id) == []


def test_a_document_outside_the_library_is_refused(studio):
    """A `..` in the path must not allow reading elsewhere on disk."""
    service.library.sync("trial")
    with pytest.raises(NotFound):
        service.library.read_document("trial", "../../..", "etc/passwd")


def test_deleting_without_a_target_is_refused(studio):
    with pytest.raises(ServiceError):
        service.library.delete([])


# -------------------------------------------------------------------- previews


def test_a_preview_is_resized(studio, silhouette):
    asset_id = silhouette
    png = service.images.preview_png(asset_id, 64)
    from PIL import Image

    with Image.open(__import__("io").BytesIO(png)) as image:
        assert max(image.size) == 64


def test_a_file_that_is_not_an_image_is_refused(studio):
    path = studio.settings.data_dir / "notes.txt"
    path.write_text("not an image", encoding="utf-8")
    asset = studio.space("trial").store.put_file(path, kind="data")
    studio.space("trial").db.save_asset(asset)
    with pytest.raises(ServiceError, match="image"):
        service.images.preview_png(asset.id)


# ---------------------------------------------------------------------- import


def test_a_sheet_is_inspected_without_writing_anything(studio):
    sheet = studio.settings.project_root / "pack.svg"
    sheet.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">'
        '<g id="square"><rect x="4" y="4" width="16" height="16"/></g>'
        '<g id="circle"><circle cx="72" cy="12" r="8"/></g></svg>',
        encoding="utf-8")
    before = len(service.catalog.list_assets(limit=100))
    summary = service.sheets.inspect_sheet(str(sheet))
    assert summary["count"] == 2
    assert len(service.catalog.list_assets(limit=100)) == before, "inspecting writes nothing"


def test_a_missing_file_is_a_readable_error(studio):
    with pytest.raises(NotFound):
        service.sheets.inspect_sheet("/path/that/does/not/exist.svg")


# ------------------------------------------------------------------------- CLI


def test_the_cli_goes_through_the_service_and_states_the_amount(studio):
    """`build`, `style explore` and `style train` refuse without `--confirm`: the
    terminal states the amount, and nothing is queued."""
    from typer.testing import CliRunner

    from gamestudio.cli import app

    runner = CliRunner()
    recipe = str(studio.space("trial").paths.recipe)
    images = [arg for index in range(10) for arg in ("-i", f"{index:032x}")]
    for command in (["build", recipe], ["style", "explore", recipe, "-s", "a blacksmith"],
                    ["style", "train", recipe, *images]):
        refused = runner.invoke(app, command)
        assert refused.exit_code == 1, refused.output
        assert "$" in refused.output and "--confirm" in refused.output
    assert service.jobs.status()["states"] == {}, "nothing may be queued"

    queued = runner.invoke(app, ["build", recipe, "--confirm", "--no-wait"])
    assert queued.exit_code == 0, queued.output
    assert service.jobs.status()["states"] == {"pending": 1}
    job = service.jobs.listing("trial")[0]
    assert job["kind"] == "build_character" and job["id"] in queued.output
