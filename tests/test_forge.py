"""The icon forge and the trash: create, redo, remove, without losing anything.

What these tests protect: a paid request is refused without consent, and states
its amount; a new icon starts from its family (reference, style, scale); a
sheet is split into icons named in order; adopting writes into the game folder
at the family's scale, and a redone icon leaves the old one in the trash;
removing an element or a family goes through the trash, from which it comes
back. Runware and matting are simulated.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from gamestudio.config import Settings
from gamestudio.service import build, folders, forge, produce, showcase, trash, using
from gamestudio.service.context import space
from gamestudio.service.errors import NotFound, PaymentRequired, ServiceError

GRAY = (217, 217, 217)


def _icon(path: Path, color: str, size: int = 128, margin: int = 24) -> None:
    """A family icon: a disc, with a transparent margin around it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((margin, margin, size - margin, size - margin), fill=color)
    image.save(path)


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    root = tmp_path / "game"
    (root / "project.godot").parent.mkdir(parents=True)
    (root / "project.godot").write_text('config_version=5\n\n[application]\n\n'
                                        'config/name="Forge"\n', encoding="utf-8")
    for name, color in (("coin", "gold"), ("gem", "purple"), ("heart", "red")):
        _icon(root / "icons" / f"{name}.png", color)
    (root / "icons" / "coin.png.import").write_text("[remap]\n", encoding="utf-8")
    _icon(root / "icons" / "small" / "dot.png", "blue", size=32, margin=4)
    settings = Settings(data_dir=isolated_data, project_root=tmp_path / "studio",
                        context_dir=tmp_path / "context")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        yield root


@pytest.fixture
def runware(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """What the forge orders from Runware, without sending it."""
    orders: list[dict[str, Any]] = []

    def generate(prompt: str, **options: Any) -> dict[str, Any]:
        orders.append({"prompt": prompt, **options})
        return {"queued": [f"job{len(orders)}"], "project": options.get("project")}

    monkeypatch.setattr(produce, "generate_image", generate)
    # The local model's matting: here, the prompts' gray becomes transparent.
    monkeypatch.setattr(forge, "_matte", lambda image: _without_gray(image))
    monkeypatch.setattr(forge, "MATTING", False)
    return orders


def _without_gray(image: Image.Image) -> Image.Image:
    pixels = np.array(image.convert("RGBA"))
    pixels[(pixels[..., :3] == GRAY).all(axis=-1), 3] = 0
    return Image.fromarray(pixels)


def _proposal(request: str, drawing: Image.Image) -> str:
    """An image Runware would have returned for the request."""
    import io

    buffer = io.BytesIO()
    drawing.convert("RGB").save(buffer, "PNG")
    st = space("game")
    asset = st.store.put_bytes(buffer.getvalue(), ".png", kind="image",
                               meta={"role": "generation", "project": "game", "forge": request})
    st.db.save_asset(asset)
    return asset.id


def test_a_family_says_how_to_look_like_it(game: Path) -> None:
    profile = forge.profile("game", "icons")
    assert (profile["mode"], profile["width"], profile["height"]) == ("framed", 128, 128)
    assert profile["margin"] == pytest.approx(0.19, abs=0.02), "the family's measured margin"
    assert profile["exemplar"].startswith("icons/")
    assert {f["folder"] for f in forge.families("game")} == {"icons", "icons/small"}


def test_a_paid_request_waits_for_consent(game: Path, runware: list) -> None:
    with pytest.raises(PaymentRequired, match=r"\$0\.012"):
        forge.request("game", mode="one", folder="icons", names=["shield"])
    assert runware == [], "nothing is ordered without consent"
    with pytest.raises(ServiceError, match="already in icons: coin"):
        forge.request("game", mode="one", folder="icons", names=["coin"], confirm=True)
    with pytest.raises(ServiceError, match="2 to 16"):
        forge.request("game", mode="set", folder="icons", names=["alone"], confirm=True)
    with pytest.raises(ServiceError, match="outside the game"):
        forge.request("game", mode="one", folder="../elsewhere", names=["x"], confirm=True)


def test_a_new_icon_starts_from_its_family(game: Path, runware: list) -> None:
    request = forge.request("game", mode="one", folder="icons", names=["Shield"],
                            description="a round wooden shield",
                            style="glossy cartoon, thick dark outline", confirm=True)
    [order] = runware
    assert order["forge"] == request["id"] and order["count"] == 2
    assert order["reference_asset_id"], "a family icon is the starting point"
    assert order["prompt"].startswith(
        "glossy cartoon, thick dark outline, a round wooden shield")
    assert "flat solid light gray background" in order["prompt"]
    assert request["names"] == ["shield"] and request["status"] in ("running", "empty", "ready")
    styles = game / ".gamestudio" / "documents" / "showcase" / "styles.json"
    assert json.loads(styles.read_text(encoding="utf-8"))["icons"].startswith("glossy")

    drawing = Image.new("RGB", (1024, 1024), GRAY)
    ImageDraw.Draw(drawing).rectangle((100, 300, 900, 700), fill="brown")
    proposal = _proposal(request["id"], drawing)
    view = forge.detail("game", request["id"])
    assert [c["asset_id"] for c in view["candidates"]] == [proposal]

    adopted = forge.adopt("game", request["id"], [{"source": f"asset:{proposal}",
                                                   "name": "shield"}])
    assert adopted["written"] == ["icons/shield.png"]
    with Image.open(game / "icons/shield.png") as icon:
        assert icon.size == (128, 128), "the family's scale"
        box = icon.getchannel("A").getbbox()
    assert box[2] - box[0] == pytest.approx(128 * (1 - 2 * 0.19), abs=3)
    assert "shield" in {i["title"] for f in showcase.showcase("game", "icons")["families"]
                        for i in f["items"]}
    with pytest.raises(ServiceError, match="already in the game"):
        forge.adopt("game", request["id"], [{"source": f"asset:{proposal}", "name": "shield"}])


def test_an_icon_set_is_split_in_order(game: Path, runware: list) -> None:
    request = forge.request("game", mode="set", folder="icons/small",
                            names=["fire", "water", "earth", "air"], confirm=True)
    assert request["grid"] == [2, 2]
    assert "1. fire; 2. water; 3. earth; 4. air" in runware[0]["prompt"]
    sheet = Image.new("RGB", (1024, 1024), GRAY)
    drawing = ImageDraw.Draw(sheet)
    for index, color in enumerate(("red", "blue", "green", "white")):
        x, y = (index % 2) * 512, (index // 2) * 512
        drawing.ellipse((x + 150, y + 150, x + 362, y + 362), fill=color)
    proposal = _proposal(request["id"], sheet)

    split = forge.split("game", request["id"], proposal)
    pieces = split["split"]["pieces"]
    assert [p["name"] for p in pieces] == ["fire", "water", "earth", "air"]
    adopted = forge.adopt("game", request["id"], [{"source": "piece:0", "name": "fire"},
                                                  {"source": "piece:3", "name": "air"}])
    assert adopted["written"] == ["icons/small/fire.png", "icons/small/air.png"]
    with Image.open(game / "icons/small/fire.png") as icon:
        assert icon.size == (32, 32)
        assert icon.getpixel((16, 16))[:3] == (255, 0, 0)


def test_redoing_leaves_the_old_icon_in_the_trash(game: Path, runware: list) -> None:
    gem = next(i for f in showcase.showcase("game", "icons")["families"]
               for i in f["items"] if i["file"] == "icons/gem.png")
    request = forge.request("game", mode="redo", element=gem["id"],
                            description="sharper facets", confirm=True)
    assert runware[0]["strength"] == 0.55 and request["names"] == ["gem"]
    drawing = Image.new("RGB", (1024, 1024), GRAY)
    ImageDraw.Draw(drawing).polygon([(512, 100), (900, 512), (512, 900), (100, 512)], fill="cyan")
    proposal = _proposal(request["id"], drawing)
    adopted = forge.adopt("game", request["id"], [{"source": f"asset:{proposal}",
                                                   "name": "gem"}])
    assert adopted["written"] == ["icons/gem.png"] and adopted["batch"]
    with Image.open(game / "icons/gem.png") as icon:
        assert icon.getpixel((64, 64))[:3] == (0, 255, 255)
    [batch] = trash.batches("game")
    assert batch["files"] == ["icons/gem.png"], "the old one is kept"
    with pytest.raises(ServiceError, match="taken since"):
        trash.restore("game", batch["id"])


def test_removing_goes_through_the_trash(game: Path) -> None:
    families = {f["folder"]: f for f in showcase.showcase("game", "icons")["families"]}
    coin = next(i for i in families["icons"]["items"] if i["title"] == "coin")
    removed = showcase.delete("game", "icons", coin["id"])
    assert not (game / "icons/coin.png").exists()
    assert removed["batch"]["files"] == ["icons/coin.png", "icons/coin.png.import"], \
        "the Godot companion follows"
    trash.restore("game", removed["batch"]["id"])
    assert (game / "icons/coin.png").is_file() and (game / "icons/coin.png.import").is_file()

    family = showcase.delete_family("game", "icons", families["icons"]["id"])
    assert family["count"] == 3
    assert not list((game / "icons").glob("*.png"))
    assert (game / "icons/small/dot.png").is_file(), "a sub-family stays"
    with pytest.raises(NotFound):
        trash.restore("game", "missing")
