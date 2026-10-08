"""The mesh catalog: a single, checked price list.

The front shows the paid models from `app/src/lib/catalog.ts`; an agent reads
them from `service/meshes.py` (`mesh_providers`). Two lists, so two possible
prices -- and a wrong displayed price is worse than none: it invites a click.
This test keeps them equal.

It reads the TypeScript as text on purpose: a Node dependency in the Python
suite would cost more than the dozen lines below, and the list is data, not
code.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.service import build, meshes, using
from gamestudio.service.context import Studio

CATALOG = Path(__file__).resolve().parents[1] / "app" / "src" / "lib" / "catalog.ts"

# `{ air: "tripo:v3.1@0", label: "Tripo v3.1", sub: t("…"), usd: 0.4 },`
# The description goes through `t()`: it is translated, the model name is not.
# `usdMore` exists only for models whose detailed quality costs extra: the group
# is optional, and its absence means "same price".
LINE = re.compile(
    r'\{\s*air:\s*"(?P<air>[^"]+)"\s*,\s*label:\s*"(?P<label>[^"]+)"\s*,'
    r'\s*sub:\s*(?:t\()?"(?P<sub>[^"]+)"\)?\s*,\s*usd:\s*(?P<usd>[0-9.]+)'
    r'(?:\s*,\s*usdMore:\s*(?P<usdmore>[0-9.]+))?\s*\}')


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context",
                        inbox_dir=tmp_path / "inbox")
    with using(build(settings)):
        yield


def _front() -> dict[str, dict]:
    """The front's mesh models, as written."""
    text = CATALOG.read_text(encoding="utf-8")
    block = text.split("export const MESH_MODELS", 1)[1].split("];", 1)[0]
    return {match.group("air"): {"label": match.group("label"),
                                 "usd": float(match.group("usd")),
                                 "usd_more": (float(match.group("usdmore"))
                                              if match.group("usdmore") else None)}
            for match in LINE.finditer(block)}


def test_front_and_service_announce_the_same_prices(isolated_studio: Studio) -> None:
    """The point of the test: any gap must fail the check.

    The comparison covers **every** paid route: the front's list mixes two
    billings (Runware and direct Tripo), and a single forgotten one would show
    a wrong price.
    """
    front = _front()
    assert front, f"no mesh model read from {CATALOG}"

    served: dict[str, dict] = {}
    for provider in meshes.mesh_providers():
        if not provider["paid"]:
            continue
        for model in provider["models"]:
            served[model["id"]] = {
                "label": model["label"], "usd": float(model["cost_usd"]),
                "usd_more": (float(model["cost_detailed_usd"])
                             if model.get("cost_detailed_usd") else None)}
    assert served, "the service announces no paid 3D model"

    assert set(front) == set(served), (
        "the two lists do not offer the same models: "
        f"front {sorted(front)} / service {sorted(served)}")
    for air, entry in served.items():
        assert front[air]["usd"] == pytest.approx(entry["usd"]), (
            f"{air}: the front announces ${front[air]['usd']}, the service "
            f"${entry['usd']} — both must say the same price")
        if entry["usd_more"] is not None:
            assert front[air]["usd_more"] == pytest.approx(entry["usd_more"]), (
                f"{air}: detailed quality is ${entry['usd_more']} on the "
                f"service side, ${front[air]['usd_more']} on the front side")
