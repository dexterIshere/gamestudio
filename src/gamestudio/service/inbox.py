"""The inbox: what one drops into the studio to hand to an agent.

Three gestures, one place. Paste a screenshot, drag a file, point at an export:
the object lands in `inbox/attachments/`, and **its path** is what goes into the
conversation. An agent reads files; pasting an image into a terminal means
nothing to it.

The inbox sits at the root, not under `data/`: the agent works in this
directory, so a relative path (`inbox/attachments/…`) makes sense to it. It is a
shared workspace, not a studio asset -- no project claims anything in it, and
putting it in the store would show it in the library as a production, which it
is not.

A file's name carries the date and the fingerprint of its content: dropping the
same capture twice does not create two files, and a later drop cannot
overwrite a name.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .context import studio
from .errors import NotFound, ServiceError

# What the inbox accepts to keep. Beyond that, it is a slip of the hand (a whole
# disk dragged by mistake) more often than a real need.
MAX_BYTES = 64 * 1024 * 1024

# Images get their dimensions noted: that is what one checks before handing them
# to an agent ("is it really 1024x1024?").
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg")

# An inbox file name is an identifier, like a document's: letters, digits,
# dashes, dots and underscores. No traversal possible.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _dir() -> Path:
    return studio().settings.inbox_path / "attachments"


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _when(path: Path) -> str:
    return _stamp(datetime.fromtimestamp(path.stat().st_mtime, tz=UTC))


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if not suffix or not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return ""
    return suffix


def _measure(data: bytes, suffix: str) -> dict[str, Any]:
    """What is known of the file: an image (and its size) or any other file.

    An unreadable image is refused: keeping it would leave a file nobody can
    open, and the agent would find out too late.
    """
    if suffix not in IMAGE_SUFFIXES:
        return {"kind": "file", "width": None, "height": None}
    if suffix == ".svg":
        # An SVG has no pixels: its size is in its viewBox, and reading it would
        # require parsing. It is left as is.
        return {"kind": "image", "width": None, "height": None}
    import io

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ServiceError(f"unreadable image ({exc}): the file may be truncated, or the "
                           "extension does not match") from exc
    return {"kind": "image", "width": width, "height": height}


def add_bytes(data: bytes, filename: str = "") -> dict[str, Any]:
    """Drop bytes into the inbox and return where they are.

    The name carries the date and the fingerprint: dropping the same content
    twice returns the **same** path (`duplicate: true`) rather than two
    identical files.
    """
    if not data:
        raise ServiceError("empty file: nothing to drop")
    if len(data) > MAX_BYTES:
        raise ServiceError(f"file too large: {len(data) // (1024 * 1024)} MB (beyond "
                           f"{MAX_BYTES // (1024 * 1024)} MB, put it elsewhere and give its path)")

    suffix = _safe_suffix(filename)
    if not suffix and data[:8] == b"\x89PNG\r\n\x1a\n":
        suffix = ".png"
    facts = _measure(data, suffix)
    digest = hashlib.sha256(data).hexdigest()

    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    existing = next((path for path in folder.glob(f"*_{digest[:8]}{suffix}")
                     if path.is_file()), None)
    if existing is not None:
        return {**_describe(existing, facts), "duplicate": True,
                "relative": _relative(existing)}

    name = f"{_now().strftime('%Y-%m-%d_%H%M%S')}_{digest[:8]}{suffix}"
    path = folder / name
    path.write_bytes(data)
    return {**_describe(path, facts), "duplicate": False, "relative": _relative(path),
            "sha256": digest}


def _relative(path: Path) -> str:
    """The path as given to an agent: relative to the studio root."""
    root = studio().settings.project_root
    if root is not None:
        try:
            return str(path.relative_to(root))
        except ValueError:
            pass
    return str(path)


def _describe(path: Path, facts: dict[str, Any] | None = None) -> dict[str, Any]:
    """What is known of an inbox file.

    Dimensions are measured on demand when not already known: `Image.open`
    reads only the header, so browsing the inbox stays cheap, and the interface
    can show the real size without opening the images one by one.
    """
    known = facts or {}
    kind = known.get("kind") or ("image" if path.suffix.lower() in IMAGE_SUFFIXES
                                 else "file")
    width, height = known.get("width"), known.get("height")
    if not known and kind == "image" and path.suffix.lower() != ".svg":
        try:
            import io as _io

            from PIL import Image

            with Image.open(_io.BytesIO(path.read_bytes())) as image:
                width, height = image.size
        except Exception:
            # An unreadable file stays listable: refusal happens when it is
            # dropped, not when the inbox is reread.
            width = height = None
    return {
        "name": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "modified_at": _when(path),
        "kind": kind,
        "width": width,
        "height": height,
    }


def _path(name: str) -> Path:
    """The path of an inbox file, valid by construction.

    As for documents: rather than filter out a traversal, any name that is not
    an identifier is refused. What enters the inbox is named by `add_bytes`;
    here, only what is already there is read back.
    """
    if not NAME_RE.match(name or ""):
        raise ServiceError(f"invalid file name: “{name}”")
    return _dir() / name


def attachments(limit: int = 50) -> list[dict[str, Any]]:
    """What the inbox holds, newest first."""
    folder = _dir()
    if not folder.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in folder.iterdir():
        if path.is_file():
            found.append({**_describe(path), "relative": _relative(path)})
    found.sort(key=lambda entry: entry["modified_at"], reverse=True)
    return found[:max(1, limit)]


def read_attachment(name: str) -> dict[str, Any]:
    path = _path(name)
    if not path.is_file():
        raise NotFound(f"file missing from the inbox: {name}")
    return {**_describe(path), "relative": _relative(path)}


def delete_attachment(name: str) -> dict[str, Any]:
    """Remove a file from the inbox. The studio keeps no copy."""
    path = _path(name)
    if not path.is_file():
        raise NotFound(f"file missing from the inbox: {name}")
    size = path.stat().st_size
    path.unlink()
    return {"name": name, "deleted": True, "size_bytes": size, "path": str(path)}


def add_file(path: str) -> dict[str, Any]:
    """Drop a file from disk into the inbox, without moving it."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise NotFound(f"file not found: {source}")
    return add_bytes(source.read_bytes(), source.name)


def summary() -> dict[str, Any]:
    """The state of the inbox: how many files, how heavy, where."""
    found = attachments(limit=10_000)
    return {
        "dir": str(_dir()),
        "relative": _relative(_dir()),
        "count": len(found),
        "bytes": sum(entry["size_bytes"] for entry in found),
        "attachments": found[:50],
    }
