"""Inventory config, seed filtering, candidate persistence, quota merge.

Seed policy (production-style hybrid):
  blacklist → min_mentions → percentile/hybrid threshold → top_k
  small-N inventories fall back to Top-N (no unstable percentile).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.domain.novel.character_inventory.models import InventoryCharacter, InventoryResult

logger = logging.getLogger("agent")

_DEFAULT_SEED: dict[str, Any] = {
    "mode": "hybrid",  # fixed | percentile | hybrid (median legacy → percentile@50)
    "min_mentions": 2,
    "percentile": 70,
    "top_k": 30,
    "small_n_fallback": 8,
    "small_n_top": 8,
    "blacklist_from_quota": True,
    "min_degree": 0,  # 剪孤立噪声；需关系图已落盘 data/graphs/（默认关闭）
}

# Cross-volume merge: old volume mention counts decay by this factor per merge,
# so characters that only shined in early volumes stop hijacking the percentile
# threshold. 1.0 = legacy behavior (max without decay).
DEFAULT_MERGE_DECAY = 0.85

_INVENTORY_DIR = Path("data/inventories")


def inventory_config() -> dict:
    """公开入口：角色盘点配置（yaml + 默认值合并）。

    dev 脚本/工具读取盘点配置时使用，不直接 import 私有实现。
    """
    return _inventory_config()


def _inventory_config() -> dict:
    import yaml

    cfg: dict[str, Any] = {
        "enabled": True,
        "ner": "llm",  # llm（默认，一次扫全文）| cluener（本地模型，降级/可选）
        "llm_max_names": 60,
        "llm_max_tokens": 2048,
        "llm_max_chars": 120000,
        "ner_min_conf": 0.3,  # R1: NER softmax 置信度下限（实测 0.5 误杀低频真名如紫苑0.428，取 0.3 保守）
        "max_chars": 80000,
        "min_cluster_mentions": 2,
        "llm_batch_size": 30,
        "device": "cpu",
        "sync_on_ingest": True,
        "max_clusters_for_llm": 60,
        # legacy flat keys kept for env / old callers
        "seed_threshold_mode": "hybrid",
        "seed_min_mentions": 2,
        "merge_decay": DEFAULT_MERGE_DECAY,
        "seed": dict(_DEFAULT_SEED),
    }
    seed_from_yaml: dict[str, Any] | None = None
    cfg_path = Path(__file__).resolve().parents[4] / "config.yaml"
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        loaded = dict((raw.get("novel_rag") or {}).get("character_inventory") or {})
        if isinstance(loaded.get("seed"), dict):
            seed_from_yaml = dict(loaded["seed"])
            loaded = {k: v for k, v in loaded.items() if k != "seed"}
        cfg.update(loaded)
    except Exception as e:
        logger.debug("character_inventory config load failed: %s", e)

    seed = dict(_DEFAULT_SEED)
    # Legacy flat mode/threshold apply first, nested seed overrides
    legacy_mode = str(cfg.get("seed_threshold_mode") or "").strip().lower()
    if legacy_mode == "median":
        seed["mode"] = "percentile"
        seed["percentile"] = 50
    elif legacy_mode in ("fixed", "percentile", "hybrid"):
        seed["mode"] = legacy_mode
    if cfg.get("seed_min_mentions") is not None:
        seed["min_mentions"] = max(1, int(cfg["seed_min_mentions"]))
    if seed_from_yaml:
        seed.update(seed_from_yaml)
    cfg["seed"] = seed
    # Mirror nested → flat for older readers
    cfg["seed_threshold_mode"] = str(seed.get("mode") or "hybrid")
    cfg["seed_min_mentions"] = int(seed.get("min_mentions") or 2)

    env = os.getenv("NOVEL_CHAR_INVENTORY_ENABLED", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        cfg["enabled"] = True
    elif env in ("0", "false", "no", "off"):
        cfg["enabled"] = False
    env_mode = os.getenv("NOVEL_CHAR_SEED_THRESHOLD_MODE", "").strip().lower()
    if env_mode in ("median", "fixed", "percentile", "hybrid"):
        mapped = "percentile" if env_mode == "median" else env_mode
        cfg["seed"]["mode"] = mapped
        cfg["seed_threshold_mode"] = mapped
        if env_mode == "median":
            cfg["seed"]["percentile"] = 50
    env_thr = os.getenv("NOVEL_CHAR_SEED_MIN_MENTIONS", "").strip()
    if env_thr.isdigit():
        n = int(env_thr)
        cfg["seed"]["min_mentions"] = n
        cfg["seed_min_mentions"] = n
        cfg["seed"]["mode"] = "fixed"
        cfg["seed_threshold_mode"] = "fixed"
    return cfg


def _seed_settings(cfg: dict | None = None) -> dict[str, Any]:
    root = cfg or _inventory_config()
    seed = dict(_DEFAULT_SEED)
    if isinstance(root.get("seed"), dict):
        seed.update(root["seed"])
    mode = str(seed.get("mode") or "hybrid").strip().lower()
    if mode == "median":
        mode = "percentile"
        seed["percentile"] = int(seed.get("percentile") or 50)
    if mode not in ("fixed", "percentile", "hybrid"):
        mode = "hybrid"
    seed["mode"] = mode
    seed["min_mentions"] = max(1, int(seed.get("min_mentions") or 2))
    seed["percentile"] = min(99, max(1, int(seed.get("percentile") or 70)))
    seed["top_k"] = max(1, int(seed.get("top_k") or 30))
    seed["small_n_fallback"] = max(1, int(seed.get("small_n_fallback") or 8))
    seed["small_n_top"] = max(1, int(seed.get("small_n_top") or seed["top_k"]))
    seed["blacklist_from_quota"] = bool(seed.get("blacklist_from_quota", True))
    seed["min_degree"] = max(0, int(seed.get("min_degree") or 0))
    return seed


def _seed_blacklist(seed_cfg: dict[str, Any]) -> set[str]:
    if not seed_cfg.get("blacklist_from_quota", True):
        return set()
    try:
        from src.domain.novel.dialogue_quota import (
            DEFAULT_IMPORTANCE_BLACKLIST,
            load_importance_tier_settings,
        )

        settings = load_importance_tier_settings()
        bl = settings.get("importance_blacklist") or list(DEFAULT_IMPORTANCE_BLACKLIST)
        return {str(x).strip() for x in bl if str(x).strip()}
    except Exception:
        try:
            from src.domain.novel.dialogue_quota import DEFAULT_IMPORTANCE_BLACKLIST

            return set(DEFAULT_IMPORTANCE_BLACKLIST)
        except Exception:
            return set()


def inventory_path(series_id: str) -> Path:
    from src.domain.novel.series_paths import inventory_json_path

    return inventory_json_path(series_id)


def as_inventory_character(c: InventoryCharacter | dict) -> InventoryCharacter | None:
    """公开入口：dict → InventoryCharacter（同名私有函数的上游化封装）。

    分析脚本/工具需要把 inventory candidates dict 转成领域对象时使用，
    不直接 import 私有实现。
    """
    return _as_inventory_character(c)


def _as_inventory_character(c: InventoryCharacter | dict) -> InventoryCharacter | None:
    if isinstance(c, dict):
        name = str(c.get("canonical_name") or c.get("name") or "").strip()
        if not name:
            return None
        return InventoryCharacter(
            canonical_name=name,
            aliases=list(c.get("aliases") or []),
            importance=str(c.get("importance") or "supporting"),
            mention_count=int(c.get("mention_count") or 0),
            from_clusters=list(c.get("from_clusters") or []),
        )
    return c


def _mention_counts(characters: Sequence[InventoryCharacter] | Sequence[dict]) -> list[int]:
    counts: list[int] = []
    for c in characters or []:
        ic = _as_inventory_character(c)
        if ic is None:
            continue
        counts.append(max(0, int(ic.mention_count)))
    return counts


def median_mention_threshold(
    characters: Sequence[InventoryCharacter] | Sequence[dict],
    *,
    floor: int = 1,
) -> int:
    """Median of mention_count (整数中值); empty → floor. Kept for tests/compat."""
    return percentile_mention_threshold(characters, percentile=50, floor=floor)


def percentile_mention_threshold(
    characters: Sequence[InventoryCharacter] | Sequence[dict],
    *,
    percentile: int = 70,
    floor: int = 1,
) -> int:
    """Nearest-rank percentile of mention_count, ceiled; empty → floor."""
    floor = max(1, int(floor))
    counts = sorted(_mention_counts(characters))
    if not counts:
        return floor
    p = min(99, max(1, int(percentile)))
    # nearest-rank: index = ceil(p/100 * n) - 1
    idx = max(0, min(len(counts) - 1, int(math.ceil(p / 100.0 * len(counts))) - 1))
    thr = int(math.ceil(float(counts[idx])))
    return max(floor, thr)


def resolve_seed_min_mentions(
    characters: Sequence[InventoryCharacter] | Sequence[dict],
    *,
    seed_min_mentions: int | None = None,
    mode: str | None = None,
) -> int:
    """Resolve numeric threshold only (compat). Prefer ``build_llm_seed`` for full policy."""
    seed_cfg = _seed_settings()
    if seed_min_mentions is not None:
        return max(1, int(seed_min_mentions))
    use_mode = (mode or seed_cfg["mode"]).strip().lower()
    if use_mode == "median":
        use_mode = "percentile"
        pct = 50
    else:
        pct = int(seed_cfg["percentile"])
    floor = int(seed_cfg["min_mentions"])
    if use_mode == "fixed":
        return floor
    return percentile_mention_threshold(characters, percentile=pct, floor=floor)


@dataclass
class SeedBuildResult:
    characters: list[InventoryCharacter] = field(default_factory=list)
    threshold: int = 1
    mode: str = "hybrid"
    top_k: int = 30
    blacklisted: int = 0
    total_input: int = 0
    degree_dropped: int = 0

    @property
    def seed_names(self) -> list[str]:
        return [c.canonical_name for c in self.characters]


def _degree_filter(
    characters: list[InventoryCharacter],
    *,
    min_degree: int,
    degree_map: dict[str, int] | None,
) -> tuple[list[InventoryCharacter], int]:
    """Drop isolated-noise characters whose graph degree < min_degree.

    Characters absent from ``degree_map`` are kept (no evidence → no action),
    so this only trims nodes the graph actually marks as low-degree.
    Returns (kept, dropped).
    """
    if min_degree <= 0 or not degree_map:
        return characters, 0
    kept: list[InventoryCharacter] = []
    dropped = 0
    for c in characters:
        deg = int(degree_map.get(c.canonical_name, -1))
        if deg >= 0 and deg < min_degree:
            dropped += 1
            continue
        kept.append(c)
    return kept, dropped


def load_series_degree_map(series_id: str) -> dict[str, int]:
    """Aggregate per-role max degree across persisted graphs for a series.

    Reads ``data/graphs/<doc_id>.json`` files whose doc_id starts with the
    series id; a role counts as connected if it has degree ≥ 1 in ANY volume.
    Returns {} when no graph files exist yet (min_degree stays inert).
    """
    import glob

    out: dict[str, int] = {}
    safe = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (series_id or "").strip())
    pattern = str(_INVENTORY_DIR.parent / "graphs" / f"{safe}__*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            nodes = list(data.get("nodes") or [])
            # MultiGraph (CharacterGraph) persists edges as ``edges``;
            # plain graphs use ``links`` — accept both.
            edges = data.get("edges") or data.get("links") or []
            names = {str(n.get("id") or n.get("name") or "") for n in nodes if n.get("id") or n.get("name")}
            deg: dict[str, int] = {n: 0 for n in names}
            for e in edges:
                src = str(e.get("source") or "")
                tgt = str(e.get("target") or "")
                if src in deg:
                    deg[src] += 1
                if tgt in deg:
                    deg[tgt] += 1
            for name, d in deg.items():
                if name:
                    out[name] = max(out.get(name, 0), d)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return out


def build_llm_seed(
    characters: Sequence[InventoryCharacter] | Sequence[dict] | None,
    *,
    seed_min_mentions: int | None = None,
    config: dict | None = None,
    degree_map: dict[str, int] | None = None,
) -> SeedBuildResult:
    """Build LLM seed list: blacklist → floor → percentile → top_k (small-N → Top-N).

    ``degree_map`` (name → graph degree) enables optional isolated-noise pruning
    via ``seed.min_degree``; without a graph it is inert.
    """
    seed_cfg = _seed_settings(config)
    if seed_min_mentions is not None:
        seed_cfg = dict(seed_cfg)
        seed_cfg["min_mentions"] = max(1, int(seed_min_mentions))
        seed_cfg["mode"] = "fixed"

    blacklist = _seed_blacklist(seed_cfg)
    try:
        from src.domain.novel.dialogue_span import is_noise_speaker
    except Exception:

        def is_noise_speaker(name: str) -> bool:  # type: ignore[misc]
            return False

    cleaned: list[InventoryCharacter] = []
    blacklisted_n = 0
    for c in characters or []:
        ic = _as_inventory_character(c)
        if ic is None:
            continue
        name = normalize_character_name(ic.canonical_name)
        if not name or len(name) < 2:
            continue
        if name in blacklist or is_noise_speaker(name):
            blacklisted_n += 1
            continue
        ic.canonical_name = name
        cleaned.append(ic)

    total_input = len(cleaned)
    if not cleaned:
        return SeedBuildResult(
            characters=[],
            threshold=int(seed_cfg["min_mentions"]),
            mode=str(seed_cfg["mode"]),
            top_k=int(seed_cfg["top_k"]),
            blacklisted=blacklisted_n,
            total_input=0,
        )

    floor = int(seed_cfg["min_mentions"])
    top_k = int(seed_cfg["top_k"])
    mode = str(seed_cfg["mode"])

    # Optional isolated-noise pruning (needs a persisted relationship graph;
    # inert without one). Runs before threshold so low-degree noise does not
    # skew the percentile either.
    min_degree = int(seed_cfg.get("min_degree") or 0)
    degree_dropped = 0
    if min_degree > 0 and degree_map:
        cleaned, degree_dropped = _degree_filter(
            cleaned, min_degree=min_degree, degree_map=degree_map
        )

    # Small-N: skip unstable percentile
    if len(cleaned) < int(seed_cfg["small_n_fallback"]):
        picked = sorted(cleaned, key=lambda x: (-x.mention_count, x.canonical_name))
        picked = [c for c in picked if c.mention_count >= floor][: int(seed_cfg["small_n_top"])]
        return SeedBuildResult(
            characters=picked,
            threshold=floor,
            mode="small_n_top",
            top_k=int(seed_cfg["small_n_top"]),
            blacklisted=blacklisted_n,
            total_input=total_input,
            degree_dropped=degree_dropped,
        )

    if mode == "fixed":
        thr = floor
    else:
        # hybrid / percentile share percentile threshold with floor
        thr = percentile_mention_threshold(
            cleaned,
            percentile=int(seed_cfg["percentile"]),
            floor=floor,
        )

    pool = [c for c in cleaned if c.mention_count >= thr]
    pool.sort(key=lambda x: (-x.mention_count, x.canonical_name))
    pool = pool[:top_k]
    return SeedBuildResult(
        characters=pool,
        threshold=thr,
        mode=mode,
        top_k=top_k,
        blacklisted=blacklisted_n,
        total_input=total_input,
        degree_dropped=degree_dropped,
    )


def filter_seed_characters(
    characters: Sequence[InventoryCharacter] | Sequence[dict],
    *,
    seed_min_mentions: int | None = None,
) -> list[InventoryCharacter]:
    """Keep LLM-seed characters (hybrid policy)."""
    return build_llm_seed(characters, seed_min_mentions=seed_min_mentions).characters


def persist_relations(series_id: str, relations: list[dict]) -> Path:
    """落盘人物关系数据（跨卷合并）：``data/relations/{series_id}.json``。

    按 (source,target 排序对) 合并：evidence 累积去重、first_chapter 取最早、
    chapter_count 累加。供关系图谱/前端关系视图消费。
    """
    out_dir = _INVENTORY_DIR.parent / "relations"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{series_id}.json"

    prev: list[dict] = []
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8")).get("relations") or []
        except (OSError, json.JSONDecodeError):
            prev = []

    merged: dict[tuple[str, str], dict] = {}
    for r in [*prev, *relations]:
        src = str(r.get("source") or "").strip()
        tgt = str(r.get("target") or "").strip()
        if not src or not tgt or src == tgt:
            continue
        key = tuple(sorted((src, tgt)))
        item = merged.setdefault(
            key,
            {
                "source": src,
                "target": tgt,
                "relation": "",
                "evidence": [],
                "first_chapter": None,
                "chapter_count": 0,
            },
        )
        rel = str(r.get("relation") or "").strip()
        if rel and not item["relation"]:
            item["relation"] = rel
        for ev in r.get("evidence") or []:
            ev = str(ev).strip()
            if ev and ev not in item["evidence"]:
                item["evidence"].append(ev)
        fc = r.get("first_chapter")
        if fc is not None:
            try:
                fc = int(fc)
                item["first_chapter"] = (
                    fc if item["first_chapter"] is None else min(item["first_chapter"], fc)
                )
            except (TypeError, ValueError):
                pass
        item["chapter_count"] += int(r.get("chapter_count") or 1)

    from datetime import UTC, datetime

    data = {
        "series_id": series_id,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "relations": list(merged.values()),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def persist_inventory_candidates(
    *,
    series_id: str,
    doc_id: str,
    inventory: InventoryResult,
    seed_min_mentions: int | None = None,
    degree_map: dict[str, int] | None = None,
) -> dict:
    """Persist full candidate list + hybrid-filtered LLM seed names.

    ``degree_map`` (name → graph degree) optionally powers ``seed.min_degree``
    isolated-noise pruning; when omitted and ``min_degree`` is enabled, the
    series degree map is loaded from ``data/graphs/`` automatically (inert if
    the relationship graph has not been persisted yet).
    """
    cfg = _inventory_config()
    min_degree = int((cfg.get("seed") or {}).get("min_degree") or 0)
    if min_degree > 0 and degree_map is None:
        degree_map = load_series_degree_map(series_id)
    seed_res = build_llm_seed(
        inventory.characters,
        seed_min_mentions=seed_min_mentions,
        degree_map=degree_map,
    )
    seed_set = {c.canonical_name for c in seed_res.characters}
    seed_names = list(seed_res.seed_names)

    payload = {
        "series_id": series_id,
        "doc_ids": [doc_id] if doc_id else [],
        "updated_at": datetime.now(UTC).isoformat(),
        "seed_threshold_mode": seed_res.mode,
        "seed_min_mentions": seed_res.threshold,
        "seed_top_k": seed_res.top_k,
        "candidates": [
            {
                "name": c.canonical_name,
                "aliases": list(c.aliases),
                "mention_count": int(c.mention_count),
                "importance": c.importance,
                "from_clusters": list(c.from_clusters),
                "in_llm_seed": c.canonical_name in seed_set,
                "attributes": list(getattr(c, "attributes", None) or []),
            }
            for c in sorted(
                inventory.characters,
                key=lambda x: (-x.mention_count, x.canonical_name),
            )
        ],
        "seed_names": seed_names,
        "dropped": list(inventory.dropped or []),
        "meta": {
            **(inventory.meta or {}),
            "candidates_total": len(inventory.characters),
            "seed_count": len(seed_names),
            "seed_min_mentions": seed_res.threshold,
            "seed_threshold_mode": seed_res.mode,
            "seed_top_k": seed_res.top_k,
            "seed_blacklisted": seed_res.blacklisted,
            "degree_dropped": seed_res.degree_dropped,
            "llm_skipped": inventory.llm_skipped,
            "draft_clusters": inventory.draft_clusters,
        },
    }

    path = inventory_path(series_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            prev_docs = list(prev.get("doc_ids") or [])
            for d in payload["doc_ids"]:
                if d and d not in prev_docs:
                    prev_docs.append(d)
            payload["doc_ids"] = prev_docs
            # Decay old-volume mention counts so stale high-frequency characters
            # from earlier volumes stop hijacking the series-level percentile
            # threshold. Active characters (present this volume) are re-anchored
            # by their fresh count via max(); 1.0 disables decay.
            try:
                decay = float(cfg.get("merge_decay", DEFAULT_MERGE_DECAY))
            except (TypeError, ValueError):
                decay = DEFAULT_MERGE_DECAY
            decay = min(1.0, max(0.0, decay)) if decay >= 0 else DEFAULT_MERGE_DECAY
            by_name = {c["name"]: c for c in (prev.get("candidates") or []) if c.get("name")}
            fresh_names: set[str] = set()
            for c in payload["candidates"]:
                old = by_name.get(c["name"])
                if old:
                    old_count = int(old.get("mention_count") or 0)
                    if decay < 1.0:
                        old_count = int(round(old_count * decay))
                    c["mention_count"] = max(old_count, c["mention_count"])
                    c["aliases"] = sorted(set(old.get("aliases") or []) | set(c["aliases"]))
                by_name[c["name"]] = c
                fresh_names.add(c["name"])
            # Stale characters absent from this volume also decay, so early-volume
            # one-hit wonders cannot keep the percentile threshold hostage.
            if decay < 1.0:
                for name, old in by_name.items():
                    if name in fresh_names:
                        continue
                    old["mention_count"] = int(round((int(old.get("mention_count") or 0)) * decay))
            merged = sorted(
                by_name.values(),
                key=lambda x: (-int(x.get("mention_count") or 0), x["name"]),
            )
            merged_seed = build_llm_seed(
                merged,
                seed_min_mentions=seed_min_mentions,
                degree_map=degree_map,
            )
            seed_set = {c.canonical_name for c in merged_seed.characters}
            for c in merged:
                c["in_llm_seed"] = c.get("name") in seed_set
            payload["candidates"] = merged
            payload["seed_min_mentions"] = merged_seed.threshold
            payload["seed_threshold_mode"] = merged_seed.mode
            payload["seed_top_k"] = merged_seed.top_k
            payload["seed_names"] = list(merged_seed.seed_names)
            payload["meta"]["candidates_total"] = len(merged)
            payload["meta"]["seed_count"] = len(payload["seed_names"])
            payload["meta"]["seed_min_mentions"] = merged_seed.threshold
            payload["meta"]["seed_threshold_mode"] = merged_seed.mode
            payload["meta"]["seed_top_k"] = merged_seed.top_k
            payload["meta"]["seed_blacklisted"] = merged_seed.blacklisted
            payload["meta"]["merge_decay"] = decay
            payload["meta"]["merged_docs"] = len(prev_docs)
            payload["meta"]["degree_dropped"] = merged_seed.degree_dropped
            seed_res = merged_seed
        except (OSError, json.JSONDecodeError, TypeError) as e:
            logger.debug("inventory merge skipped: %s", e)

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "Saved inventory candidates series=%s total=%d seed=%d (mode=%s thr=%d top_k=%d) → %s",
        series_id,
        len(payload["candidates"]),
        len(payload["seed_names"]),
        payload.get("seed_threshold_mode"),
        seed_res.threshold,
        seed_res.top_k,
        path,
    )
    return payload


def load_inventory_candidates(series_id: str) -> dict | None:
    from src.domain.novel.series_paths import series_stem_aliases

    # Prefer project-root inventories; also try historical stems
    tried: set[Path] = set()
    for stem in series_stem_aliases(series_id):
        for base in (
            inventory_path(series_id).parent,
            _INVENTORY_DIR,
        ):
            path = (base / f"{stem}.json").resolve()
            if path in tried:
                continue
            tried.add(path)
            if not path.exists():
                continue
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError) as e:
                logger.warning("Failed to load inventory %s: %s", path, e)
                continue
    return None


def normalize_character_name(name: str) -> str:
    """Strip NER debris (trailing middle-dot etc.) from person names."""
    from src.domain.novel.dialogue_quota import normalize_character_name as _norm

    return _norm(name)


def merge_volume_and_series_for_quota(
    volume_characters: Sequence[Any] | None,
    series_candidates: Sequence[dict] | None,
) -> list[dict]:
    """Union this-volume inventory with series sidecar for dialogue quota TopN."""
    by: dict[str, dict] = {}

    def _upsert(name: str, aliases: Sequence[str], mentions: int, importance: str = "extra") -> None:
        from src.domain.novel.dialogue_quota import normalize_character_name as _norm

        name = _norm(name)
        if not name or len(name) < 2:
            return
        cleaned_aliases: list[str] = []
        for a in aliases or []:
            a = _norm(str(a))
            if a and a != name and a not in cleaned_aliases:
                cleaned_aliases.append(a)
        cur = by.get(name)
        if cur is None:
            by[name] = {
                "name": name,
                "aliases": cleaned_aliases,
                "mention_count": max(0, int(mentions)),
                "importance": importance if importance in ("main", "supporting", "extra") else "extra",
            }
            return
        cur["mention_count"] = max(int(cur.get("mention_count") or 0), max(0, int(mentions)))
        cur["aliases"] = sorted(set(cur.get("aliases") or []) | set(cleaned_aliases) - {name})

    for c in series_candidates or []:
        if not isinstance(c, dict):
            continue
        _upsert(
            str(c.get("name") or c.get("canonical_name") or ""),
            list(c.get("aliases") or []),
            int(c.get("mention_count") or 0),
            str(c.get("importance") or "extra"),
        )

    for c in volume_characters or []:
        if isinstance(c, dict):
            _upsert(
                str(c.get("canonical_name") or c.get("name") or ""),
                list(c.get("aliases") or []),
                int(c.get("mention_count") or 0),
                str(c.get("importance") or "extra"),
            )
        else:
            _upsert(
                str(getattr(c, "canonical_name", "") or getattr(c, "name", "") or ""),
                list(getattr(c, "aliases", None) or []),
                int(getattr(c, "mention_count", 0) or 0),
                str(getattr(c, "importance", None) or "extra"),
            )

    return sorted(by.values(), key=lambda x: (-int(x.get("mention_count") or 0), x["name"]))


def seed_names_from_inventory(
    inventory: InventoryResult,
    *,
    seed_min_mentions: int | None = None,
) -> list[str]:
    """Names (+aliases) for dialogue LLM reference, hybrid-filtered."""
    seed_chars = build_llm_seed(
        inventory.characters, seed_min_mentions=seed_min_mentions
    ).characters
    out: list[str] = []
    for c in seed_chars:
        for n in [c.canonical_name, *c.aliases]:
            n = (n or "").strip()
            if n and n not in out:
                out.append(n)
    return out
