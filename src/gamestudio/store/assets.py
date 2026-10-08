"""Storage of produced files, addressed by the hash of their content.

Two identical generations (same seed, prompt and model) produce the same file
and so take a single entry. It is also how the pipeline graph knows that a
step does not need to run again.
"""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
from pathlib import Path
from typing import Any

from ..domain.models import Asset

_EXTENSION_KINDS = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".glb": "mesh", ".gltf": "mesh", ".fbx": "mesh", ".obj": "mesh",
    ".svg": "vector",
    ".safetensors": "lora",
    ".tscn": "scene", ".tres": "scene", ".json": "data", ".zip": "archive",
}


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _target(self, asset_id: str, suffix: str) -> Path:
        # Two levels of subfolders, to avoid directories of 100k entries.
        return self.root / asset_id[:2] / asset_id[2:4] / f"{asset_id}{suffix}"

    def put_file(self, source: Path, *, kind: str = "", meta: dict[str, Any] | None = None,
                 move: bool = False) -> Asset:
        digest = sha256_file(source)
        asset_id = digest[:32]
        suffix = source.suffix.lower()
        target = self._target(asset_id, suffix)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if move:
                shutil.move(str(source), target)
            else:
                shutil.copy2(source, target)
        elif move:
            source.unlink(missing_ok=True)
        return Asset(
            id=asset_id,
            kind=kind or _EXTENSION_KINDS.get(suffix, "blob"),
            path=target,
            mime=mimetypes.guess_type(target.name)[0] or "",
            meta=meta or {},
        )

    def put_bytes(self, data: bytes, suffix: str, *, kind: str = "",
                  meta: dict[str, Any] | None = None) -> Asset:
        asset_id = sha256_bytes(data)[:32]
        target = self._target(asset_id, suffix)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return Asset(
            id=asset_id,
            kind=kind or _EXTENSION_KINDS.get(suffix, "blob"),
            path=target,
            mime=mimetypes.guess_type(f"x{suffix}")[0] or "",
            meta=meta or {},
        )

    def path_for(self, asset_id: str) -> Path | None:
        prefix = self.root / asset_id[:2] / asset_id[2:4]
        if not prefix.is_dir():
            return None
        for candidate in prefix.glob(f"{asset_id}.*"):
            return candidate
        return None

    def remove(self, asset_id: str) -> bool:
        """Remove the file from the store. Content is addressed by its hash:
        the same id names nothing else, so nothing else is lost."""
        path = self.path_for(asset_id)
        if path is None:
            return False
        path.unlink(missing_ok=True)
        return True

    def read(self, asset_id: str) -> bytes:
        path = self.path_for(asset_id)
        if path is None:
            raise FileNotFoundError(f"asset {asset_id} not found")
        return path.read_bytes()
