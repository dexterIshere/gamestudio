"""Workbench prompts: the phrases that make an image usable.

What these tests protect: the guardrails are **shared** with the pipeline (a
single definition), and a workbench prompt is not composed without a subject --
a generic phrase produces an image the 3D mesh cannot read.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.pipeline.steps.generation import (
    CHARACTER_PROMPT_GUARDS,
    NEGATIVE_GUARDS,
)
from gamestudio.service import build, prompts, using
from gamestudio.service.context import Studio
from gamestudio.service.errors import NotFound


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context",
                        inbox_dir=tmp_path / "inbox")
    with using(build(settings)):
        yield


def test_the_catalogue_covers_the_families_in_order_of_use(isolated_studio: Studio) -> None:
    """Reference, accessory sheet, angles: in this order of use."""
    entries = prompts.catalogue()
    identifiers = [entry["id"] for entry in entries]
    assert identifiers[:3] == ["apose", "tpose", "profile"]
    assert "accessories" in identifiers
    assert identifiers[-2:] == ["multiview", "turnaround"]
    for entry in entries:
        assert entry["label"] and entry["what"] and entry["positive"]
        assert entry["negative"] and entry["guards"]
        assert entry["width"] > 0 and entry["height"] > 0
        assert entry["uses"]


def test_the_guardrails_are_the_pipeline_ones(isolated_studio: Studio) -> None:
    """Two lists that diverged would make two different requirements."""
    assert CHARACTER_PROMPT_GUARDS == prompts.GUARDRAILS
    assert NEGATIVE_GUARDS == prompts.GUARDRAILS_NEGATIVE
    # And a workbench prompt carries both.
    entry = next(item for item in prompts.catalogue() if item["id"] == "apose")
    assert entry["guards"] == prompts.GUARDRAILS


def test_a_prompt_is_composed_in_the_studio_order(isolated_studio: Studio) -> None:
    """Style prefix, subject, workbench phrase, guardrails: the same order as
    `StylePack.compose_prompt`, so nothing changes downstream."""
    rendered = prompts.render("apose", "an old knight", style_prefix="ink and watercolor",
                              style_negative="photo")
    assert rendered["positive"].startswith("ink and watercolor, an old knight, A-pose")
    assert rendered["positive"].endswith(prompts.GUARDRAILS)
    assert rendered["negative"].startswith("photo, ")
    assert (rendered["width"], rendered["height"]) == (768, 1152)
    assert rendered["pose"] == "a_pose"
    assert rendered["transparent"] is True


def test_sheets_are_on_a_flat_background(isolated_studio: Studio) -> None:
    """A matted sheet would be split along holes: the flat background is wanted."""
    for identifier in ("accessories",):
        rendered = prompts.render(identifier, "a knight")
        assert rendered["transparent"] is False
        assert rendered["pose"] == ""
        # The invariant is not each phrase's wording -- each says "one per cell"
        # in its own way -- but the sheet guardrail: each element in its cell,
        # without contact.
        assert rendered["positive"].endswith(prompts.GUARDRAILS_SHEET)


def test_a_prompt_without_subject_or_unknown_is_refused(isolated_studio: Studio) -> None:
    """The catalogue is closed: an invented identifier is reported, not guessed."""
    with pytest.raises(NotFound, match="unknown"):
        prompts.render("a-home-made-prompt", "a knight")
    with pytest.raises(NotFound, match="empty subject"):
        prompts.render("apose", "   ")


def test_the_returned_catalogue_does_not_share_its_dictionaries(isolated_studio: Studio) -> None:
    """The caller may change what it receives: the catalogue stays intact."""
    first = prompts.catalogue()
    first[0]["label"] = "changed"
    assert prompts.catalogue()[0]["label"] != "changed"
