"""Data isolation between tests.

The studio keeps everything under `data_dir`: the hash-addressed store, the
database, the library, and the screens the terminal tabs displayed. Without
this redirection, running the tests would write into the installation of
whoever runs them, and a tab saved by a test would come back in their
interface on the next start. A test that dirties the machine running it is not
a test.

`GAMESTUDIO_DATA_DIR` is set by the repository's `.env`: it is overridden here,
and `settings` must forget what it computed. The `yield` hands the data folder
to the tests that want to inspect what was written.

The agent notes folder (`context/`) is redirected the same way, for an even
stronger reason: it lives in the repository, not under `data/`. Otherwise any
test that produces something would rewrite the briefing of whoever runs the
tests. Project documents are in the same case: versioned, so never written by
a test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import settings


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data = tmp_path / "data"
    monkeypatch.setenv("GAMESTUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("GAMESTUDIO_CONTEXT_DIR", str(tmp_path / "context"))
    monkeypatch.setenv("GAMESTUDIO_INBOX_DIR", str(tmp_path / "inbox"))
    settings.cache_clear()
    try:
        yield data
    finally:
        settings.cache_clear()


def declare_recipe(settings, project: str, text: str) -> Path:
    """Write a project's recipe where it lives: in its `.gamestudio/`.

    Without an opened folder, the project is hosted by the studio
    (`data/projects/<project>/`) -- the same layout as a game folder.
    """
    from gamestudio.store.folders import project_paths

    path = project_paths(settings, project).recipe
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def figure_png(width: int = 256, height: int = 384) -> bytes:
    """An A-pose silhouette on a transparent background: it stands in for a concept.

    One volume per limb, each with its own hue: the image passes for a
    generated concept for everything that reads it (store, library, previews,
    import), without a model.
    """
    import colorsys
    import io

    from PIL import Image, ImageDraw

    from gamestudio.vision.poses import A_POSE

    points = A_POSE.scaled(width, height)
    limbs = ((1, 0), (1, 2), (2, 3), (3, 4), (1, 5), (5, 6), (6, 7),
             (1, 8), (8, 9), (9, 10), (1, 11), (11, 12), (12, 13))
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    thickness = max(4, width // 16)
    for index, (start, end) in enumerate(limbs):
        red, green, blue = colorsys.hsv_to_rgb((index * 0.13) % 1.0, 0.5, 0.92)
        draw.line([points[start], points[end]], width=thickness,
                  fill=(int(red * 255), int(green * 255), int(blue * 255), 255))
    head = points[0]
    draw.ellipse([head[0] - thickness, head[1] - thickness * 1.5,
                  head[0] + thickness, head[1] + thickness * 0.5], fill=(230, 200, 180, 255))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()
