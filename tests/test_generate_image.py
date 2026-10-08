"""Building free-generation requests (no network call)."""

from __future__ import annotations

import pytest

from gamestudio.domain.models import StylePack
from gamestudio.pipeline.steps.generation import GenerateImage
from gamestudio.runware import catalog


def test_build_params_minimal() -> None:
    step = GenerateImage("a knight", count=4, width=1024, height=1024)
    params = step.build_params()
    assert params["model"] == catalog.FLUX_DEV
    assert params["positivePrompt"] == "a knight"
    assert params["numberResults"] == 4
    assert (params["width"], params["height"]) == (1024, 1024)
    assert "inputs" not in params and "controlNet" not in params
    assert "lora" not in params and "layerDiffuse" not in params


def test_build_params_full_options() -> None:
    pack = StylePack(id="p", name="p", prompt_prefix="painted style",
                     lora_air="gamestudio:p@1", trigger_word="p_style",
                     negative_prompt="photo")
    step = GenerateImage("a knight", model=catalog.FLUX_SCHNELL, pack=pack,
                         negative_prompt="blurry", reference_asset_id="abc",
                         strength=0.4, pose="a_pose", transparent=True, seed=7)
    params = step.build_params(reference_b64="QUJD", guide_b64="R0lE")

    assert params["model"] == catalog.FLUX_SCHNELL
    # The project style composes the prompt: trigger word + prefix + subject.
    assert params["positivePrompt"].startswith("p_style")
    assert "a knight" in params["positivePrompt"]
    assert "blurry" in params["negativePrompt"] and "photo" in params["negativePrompt"]
    assert params["lora"] == [{"model": "gamestudio:p@1", "weight": 1.0}]
    assert params["seedImage"].endswith("QUJD")
    assert params["strength"] == 0.4
    assert params["controlNet"][0]["guideImage"].endswith("R0lE")
    assert params["layerDiffuse"] is True
    assert params["seed"] == 7


def test_empty_prompt_rejected() -> None:
    with pytest.raises(ValueError):
        GenerateImage("   ")
