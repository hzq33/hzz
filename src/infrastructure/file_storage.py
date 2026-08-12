"""File storage abstraction — unified file I/O with pluggable backends.

Domain code depends on this ABC, not on local filesystem or MinIO.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


@dataclass
class StoredFile:
    """Metadata for a stored file."""

    file_id: str
    filename: str
    size_bytes: int = 0
    content_type: str = ""


class FileStorage(ABC):
    """Abstract base for file storage backends."""

    name: ClassVar[str] = "base"

    @abstractmethod
    async def upload(self, file_id: str, source_path: Path) -> str:
        """Upload a file. Returns the storage key/path."""
        ...

    @abstractmethod
    async def download(self, file_id: str, dest_path: Path) -> Path:
        """Download a file to dest_path. Returns dest_path."""
        ...

    @abstractmethod
    async def delete(self, file_id: str) -> bool:
        """Delete a file. Returns True if deleted."""
        ...

    @abstractmethod
    async def exists(self, file_id: str) -> bool:
        """Check if a file exists."""
        ...

    async def read_bytes(self, file_id: str) -> bytes:
        """Read file contents as bytes. Default: download to temp."""
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmp = Path(f.name)
        try:
            await self.download(file_id, tmp)
            return tmp.read_bytes()
        finally:
            tmp.unlink(missing_ok=True)


class LocalFileStorage(FileStorage):
    """Local filesystem storage (for development / single-machine)."""

    name: ClassVar[str] = "local"

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir or "./data/storage").resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, file_id: str) -> Path:
        # Prevent path traversal
        safe_id = file_id.replace("\\", "/").split("/")[-1]
        return self.base_dir / safe_id

    async def upload(self, file_id: str, source_path: Path) -> str:
        dest = self._path(file_id)
        shutil.copy2(source_path, dest)
        return str(dest)

    async def download(self, file_id: str, dest_path: Path) -> Path:
        src = self._path(file_id)
        if not src.exists():
            raise FileNotFoundError(f"File not found: {file_id}")
        shutil.copy2(src, dest_path)
        return dest_path

    async def delete(self, file_id: str) -> bool:
        p = self._path(file_id)
        if p.exists():
            p.unlink()
            return True
        return False

    async def exists(self, file_id: str) -> bool:
        return self._path(file_id).exists()
