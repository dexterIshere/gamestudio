"""The inbox: what is dropped for an agent, and the path it is given.

What these tests protect: a drop must never overwrite a file already there, two
drops of the same content must not make two files, and a name must not allow
leaving the folder.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from gamestudio.config import Settings
from gamestudio.service import build, inbox, using
from gamestudio.service.context import Studio
from gamestudio.service.errors import NotFound, ServiceError


def capture(width: int = 64, height: int = 48, colour: tuple[int, int, int, int]
            = (255, 0, 0, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (width, height), colour).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context",
                        inbox_dir=tmp_path / "inbox")
    with using(build(settings)) as current:
        yield current


def test_a_capture_is_dropped_with_its_dimensions(isolated_studio: Studio) -> None:
    """The studio notes what it knows of the file: an agent does not."""
    result = inbox.add_bytes(capture(120, 80), "capture.png")
    assert result["kind"] == "image"
    assert (result["width"], result["height"]) == (120, 80)
    assert result["name"].endswith(".png")
    assert Path(result["path"]).is_file()
    # The relative path is the one given to the agent, which works here.
    assert result["relative"].endswith(result["name"])
    assert result["duplicate"] is False
    assert len(result["sha256"]) == 64


def test_the_same_content_twice_does_not_make_two_files(isolated_studio: Studio) -> None:
    """The name carries the fingerprint: dropping the same capture twice has no effect."""
    first = inbox.add_bytes(capture(), "capture.png")
    again = inbox.add_bytes(capture(), "capture.png")
    assert again["duplicate"] is True
    assert again["path"] == first["path"]
    assert len(inbox.attachments()) == 1

    # A different content does make a second file.
    inbox.add_bytes(capture(colour=(0, 0, 255, 255)), "capture.png")
    assert len(inbox.attachments()) == 2


def test_a_drop_cannot_overwrite_another(isolated_studio: Studio) -> None:
    """The date and fingerprint in the name suffice: nothing is silently replaced."""
    names = {inbox.add_bytes(capture(colour=(n, 0, 0, 255)), "capture.png")["name"]
             for n in range(1, 6)}
    assert len(names) == 5
    assert len(inbox.attachments()) == 5


def test_what_is_refused_is_refused_out_loud(isolated_studio: Studio) -> None:
    """An empty file, a too large one, or a fake image: saying so beats keeping it."""
    with pytest.raises(ServiceError, match="empty"):
        inbox.add_bytes(b"")

    with pytest.raises(ServiceError, match="too large"):
        inbox.add_bytes(b"x" * (inbox.MAX_BYTES + 1), "notes.txt")

    # An image extension on bytes that are not one: the studio refuses rather
    # than keep a file nobody will be able to open.
    with pytest.raises(ServiceError, match="unreadable"):
        inbox.add_bytes(b"this is not a png", "capture.png")

    # Any other file goes through: the inbox is not reserved for images.
    other = inbox.add_bytes(b"some notes", "notes.txt")
    assert other["kind"] == "file"
    assert other["width"] is None


def test_a_file_name_is_an_identifier(isolated_studio: Studio) -> None:
    inbox.add_bytes(capture(), "capture.png")
    for bad in ("../secret", "a/b.png", "", "..", "/etc/passwd"):
        with pytest.raises(ServiceError):
            inbox.read_attachment(bad)
        with pytest.raises(ServiceError):
            inbox.delete_attachment(bad)
    with pytest.raises(NotFound):
        inbox.read_attachment("never-dropped.png")


def test_the_list_goes_from_newest_to_oldest(isolated_studio: Studio) -> None:
    import os

    base = 1_700_000_000.0
    for position, colour in enumerate(((1, 0, 0, 255), (2, 0, 0, 255), (3, 0, 0, 255))):
        written = inbox.add_bytes(capture(colour=colour), "capture.png")
        os.utime(written["path"], (base + position, base + position))
    last = inbox.add_bytes(capture(colour=(9, 0, 0, 255)), "capture.png")
    os.utime(last["path"], (base + 10, base + 10))

    listed = inbox.attachments()
    assert listed[0]["name"] == last["name"]
    assert [entry["modified_at"] for entry in listed] == sorted(
        (entry["modified_at"] for entry in listed), reverse=True)


def test_the_summary_says_where_and_how_much(isolated_studio: Studio) -> None:
    inbox.add_bytes(capture(), "capture.png")
    inbox.add_bytes(b"some notes", "notes.txt")
    data = inbox.summary()
    assert data["count"] == 2
    assert data["bytes"] > 0
    assert data["dir"].endswith("attachments")
    assert {entry["name"] for entry in data["attachments"]} == {
        entry["name"] for entry in inbox.attachments()}


def test_a_file_from_disk_can_be_dropped_without_moving_it(
        isolated_studio: Studio, tmp_path: Path) -> None:
    """The original file stays where it is: the inbox keeps a copy."""
    source = tmp_path / "export.glb"
    source.write_bytes(b"binary glTF, test version")
    result = inbox.add_file(str(source))

    assert source.is_file()
    assert Path(result["path"]).is_file()
    assert Path(result["path"]) != source
    assert result["name"].endswith(".glb")

    with pytest.raises(NotFound):
        inbox.add_file(str(tmp_path / "absent.glb"))


def test_a_file_can_be_removed_from_the_inbox(isolated_studio: Studio) -> None:
    written = inbox.add_bytes(capture(), "capture.png")
    removed = inbox.delete_attachment(written["name"])
    assert removed["deleted"] is True
    assert inbox.attachments() == []
    assert not Path(written["path"]).exists()
