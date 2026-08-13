"""Novel catalog sidecar — ordered volumes/chapters without Lance schema migration.

True source for retrieval remains indexed blocks; this file is the bookkeeping
layer for series/volume selection, story analysis order, and re-ingest hints.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent")


def _default_catalog_dir() -> Path:
    """Default catalog directory (overridable via env or ``set_catalog_dir``)."""
    env_dir = os.getenv("AGENT_CATALOG_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    from src.domain.novel.series_paths import data_root

    return data_root() / "catalogs"


_CATALOG_DIR = _default_catalog_dir()


def set_catalog_dir(path: str | Path) -> None:
    """Override the catalog directory (primarily for tests / isolated data)."""
    global _CATALOG_DIR
    _CATALOG_DIR = Path(path)


@dataclass
class ChapterMeta:
    chapter_id: str = ""
    title: str = ""
    order: int = 0
    char_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChapterMeta:
        return cls(
            chapter_id=str(data.get("chapter_id") or ""),
            title=str(data.get("title") or ""),
            order=int(data.get("order") or 0),
            char_count=int(data.get("char_count") or 0),
        )


@dataclass
class VolumeEntry:
    doc_id: str
    series_id: str
    volume_no: int | None = None
    volume_title: str = ""
    title: str = ""
    source_format: str = ""
    indexed_at: str = ""
    content_fingerprint: str = ""
    block_counts: dict[str, int] = field(default_factory=dict)
    chapters: list[ChapterMeta] = field(default_factory=list)
    needs_reindex: bool = False
    reindex_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "series_id": self.series_id,
            "volume_no": self.volume_no,
            "volume_title": self.volume_title,
            "title": self.title,
            "source_format": self.source_format,
            "indexed_at": self.indexed_at,
            "content_fingerprint": self.content_fingerprint,
            "block_counts": dict(self.block_counts),
            "chapters": [c.to_dict() for c in self.chapters],
            "needs_reindex": self.needs_reindex,
            "reindex_reason": self.reindex_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VolumeEntry:
        return cls(
            doc_id=str(data.get("doc_id") or ""),
            series_id=str(data.get("series_id") or ""),
            volume_no=data.get("volume_no"),
            volume_title=str(data.get("volume_title") or ""),
            title=str(data.get("title") or ""),
            source_format=str(data.get("source_format") or ""),
            indexed_at=str(data.get("indexed_at") or ""),
            content_fingerprint=str(data.get("content_fingerprint") or ""),
            block_counts=dict(data.get("block_counts") or {}),
            chapters=[ChapterMeta.from_dict(c) for c in (data.get("chapters") or [])],
            needs_reindex=bool(data.get("needs_reindex")),
            reindex_reason=str(data.get("reindex_reason") or ""),
        )


@dataclass
class NovelCatalog:
    series_id: str
    volumes: list[VolumeEntry] = field(default_factory=list)
    updated_at: str = ""
    series_title: str = ""  # human-readable display name

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "series_title": self.series_title or self.series_id,
            "volumes": [v.to_dict() for v in self.volumes],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NovelCatalog:
        sid = str(data.get("series_id") or "")
        return cls(
            series_id=sid,
            series_title=str(data.get("series_title") or sid),
            volumes=[VolumeEntry.from_dict(v) for v in (data.get("volumes") or [])],
            updated_at=str(data.get("updated_at") or ""),
        )

    def upsert_volume(self, entry: VolumeEntry) -> None:
        for i, v in enumerate(self.volumes):
            if v.doc_id == entry.doc_id:
                self.volumes[i] = entry
                break
        else:
            self.volumes.append(entry)
        self.volumes.sort(
            key=lambda v: (
                v.volume_no is None,
                v.volume_no if v.volume_no is not None else 10**9,
                v.doc_id,
            )
        )

    def remove_volume(self, doc_id: str) -> bool:
        before = len(self.volumes)
        self.volumes = [v for v in self.volumes if v.doc_id != doc_id]
        return len(self.volumes) < before

    def find(self, doc_id: str) -> VolumeEntry | None:
        for v in self.volumes:
            if v.doc_id == doc_id:
                return v
        return None

    def ordered_chapters(self, doc_id: str | None = None) -> list[tuple[VolumeEntry, ChapterMeta]]:
        out: list[tuple[VolumeEntry, ChapterMeta]] = []
        for vol in self.volumes:
            if doc_id and vol.doc_id != doc_id:
                continue
            for ch in sorted(vol.chapters, key=lambda c: c.order):
                out.append((vol, ch))
        return out


def catalog_path(series_id: str) -> Path:
    safe = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (series_id or "").strip()) or "unknown"
    return _CATALOG_DIR / f"{safe}.json"


def content_fingerprint(raw: str | bytes) -> str:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def find_volume_by_fingerprint(fp: str) -> tuple[str, str] | None:
    """Global dedup lookup: find (series_id, doc_id) whose volume entry
    matches a content fingerprint (converted raw_md sha256).

    Scans every catalog sidecar under data/catalogs/. Returns None when the
    content has not been indexed before. Used by the upload pipeline to skip
    re-ingesting an identical book (saves LLM extraction + embedding).
    """
    if not fp:
        return None
    cat_dir = Path(catalog_path("").parent)
    if not cat_dir.exists():
        return None
    for path in sorted(cat_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for vol in data.get("volumes") or []:
            if str(vol.get("content_fingerprint") or "") == fp:
                return str(data.get("series_id") or ""), str(vol.get("doc_id") or "")
    return None


def load_catalog(series_id: str) -> NovelCatalog | None:
    path = catalog_path(series_id)
    if not path.exists():
        return None
    try:
        return NovelCatalog.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.warning("Failed to load catalog %s: %s", path, e)
        return None


def save_catalog(catalog: NovelCatalog) -> Path:
    path = catalog_path(catalog.series_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    catalog.updated_at = datetime.now(UTC).isoformat()
    payload = json.dumps(catalog.to_dict(), ensure_ascii=False, indent=2)
    # Atomic write
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        Path(tmp_name).replace(path)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    logger.info(
        "Saved catalog %s (%d volumes) → %s",
        catalog.series_id,
        len(catalog.volumes),
        path,
    )
    return path


def list_catalogs() -> list[NovelCatalog]:
    if not _CATALOG_DIR.exists():
        return []
    out: list[NovelCatalog] = []
    for p in sorted(_CATALOG_DIR.glob("*.json")):
        try:
            cat = NovelCatalog.from_dict(json.loads(p.read_text(encoding="utf-8")))
            out.append(cat)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return out


def find_orphan_doc_ids(store_doc_ids: list[str]) -> list[str]:
    """找出 lance 有数据但未被任何 catalog 收录的卷（孤儿卷）。

    孤儿卷在前端卷列表（来自 catalog）中不可见，用户无法通过 UI 删除，
    会永久残留（如史莱姆 vol09 案例）。返回按 doc_id 排序的列表。
    """
    known: set[str] = set()
    for catalog in list_catalogs():
        for vol in catalog.volumes:
            known.add(vol.doc_id)
    return sorted(d for d in (store_doc_ids or []) if d not in known)


def upsert_volume_entry(entry: VolumeEntry) -> NovelCatalog:
    catalog = load_catalog(entry.series_id) or NovelCatalog(series_id=entry.series_id)
    catalog.upsert_volume(entry)
    save_catalog(catalog)
    return catalog


def delete_volume_from_catalog(series_id: str, doc_id: str) -> NovelCatalog | None:
    catalog = load_catalog(series_id)
    if not catalog:
        return None
    if catalog.remove_volume(doc_id):
        if catalog.volumes:
            save_catalog(catalog)
        else:
            path = catalog_path(series_id)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
    return catalog


def rename_series(series_id: str, series_title: str) -> NovelCatalog | None:
    """Update display title for a series (series_id stays stable)."""
    catalog = load_catalog(series_id)
    if not catalog:
        return None
    title = (series_title or "").strip()
    if not title:
        return catalog
    catalog.series_title = title
    save_catalog(catalog)
    return catalog


_VOLUME_ONLY_TITLE_RE = re.compile(
    r"^第[一二三四五六七八九十百千零两\d]+\s*卷$"
)


def _is_volume_only_title(title: str) -> bool:
    return bool(_VOLUME_ONLY_TITLE_RE.match((title or "").strip()))


def ensure_series_title(catalog: NovelCatalog) -> NovelCatalog:
    """Fill series_title only when empty; never overwrite a real series name with「第N卷」.

    Note: series_title may equal series_id (user typed the same display/id string).
    That is valid and must not be treated as missing.
    """
    title = (catalog.series_title or "").strip()
    # Heal catalogs previously corrupted by treating title==id as missing
    if title and _is_volume_only_title(title) and not _is_volume_only_title(catalog.series_id):
        catalog.series_title = catalog.series_id
        save_catalog(catalog)
        return catalog
    if title:
        return catalog
    for vol in catalog.volumes:
        candidate = (vol.volume_title or vol.title or "").strip()
        if candidate and not _is_volume_only_title(candidate):
            catalog.series_title = candidate
            save_catalog(catalog)
            return catalog
    catalog.series_title = catalog.series_id
    if catalog.volumes:
        save_catalog(catalog)
    return catalog


def chapters_from_document(document) -> list[ChapterMeta]:
    chapters: list[ChapterMeta] = []
    for ch in getattr(document, "chapters", None) or []:
        chapters.append(
            ChapterMeta(
                chapter_id=getattr(ch, "chapter_id", "") or "",
                title=getattr(ch, "title", "") or "",
                order=int(getattr(ch, "order", 0) or 0),
                char_count=len(getattr(ch, "text", "") or ""),
            )
        )
    return chapters
