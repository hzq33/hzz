"""Dialogue attribution config.

Extracted from the former monolithic ``dialogue_pipeline.py``; logic unchanged.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("agent")


def _attr_config() -> dict:
    """Load novel_rag.dialogue_attribution (+ env overrides)."""
    from pathlib import Path

    import yaml

    cfg: dict = {
        "enabled": True,
        "provider": "cloud_chapter",
        "mode": "quota",  # quota | full
        "high_confidence_min": 0.85,
        "accept_min": 0.7,
        "accept_min_strict": 0.7,
        "reject_vocative": True,
        "roster_min": 0.5,
        "batch_size": 5,
        "context_sentences": 2,
        "max_candidates": 12,
        "max_prompt_candidates": 10,
        "prefer_local_over_volume_seed": True,
        "max_calls_per_doc": 80,
        "max_turns_indexed_per_doc": 1200,
        "stop_when_priority_met": True,
        "index_unknown": False,
        "quotas": {"main": 50, "supporting": 40, "extra": 10},
        "min_chapter_coverage_main": 0.3,
        "supporting_top_n": 20,
        "main_top_n": 5,
        "promote_importance_by_mentions": True,
        "merge_alias_collisions": True,
        "merge_near_duplicates": True,
        "near_duplicate_max_distance": 2,
        "near_duplicate_min_len": 4,
        "importance_blacklist": [
            "史莱姆",
            "哥布林",
            # 注：哥布莉娜/哥布达 是个体角色名（非种族），不进黑名单
            "人类",
            "兽人",
            "魔人",
            "魔物",
            "精灵",
            "矮人",
            "恶魔",
            "天使",
            "世界之声",
        ],
        "max_windows_per_chapter": 3,
        "deepen_on_build": True,
        "deepen_max_calls": 8,
        "cold_start_open_extract": True,
        "sync_on_ingest": True,
        "turns_per_block": 40,
        "llm_correct_speakers": True,
        "vec_text_max_chars": 1200,
        "concurrency": 1,
        # chapter-first
        "max_chunk_chars": 6000,
        "slide_win_chars": 3500,
        "slide_stride_chars": 2000,
        "max_output_tokens": 6144,
        "require_quote_marks": True,
        "min_chapter_chars": 80,
        "skip_title_patterns": "",
        "quote_patterns": "",
        # LLM chapter name harvest (design: LLM_HARVEST_CHARACTER_NAMES_DESIGN.md)
        "harvest": {
            "enabled": True,      # False = 完全走原正则收割路径
            "batch_chapters": 1,  # L1 单章 1 次；batch>1 为后续优化
            "max_names": 20,
            "max_tokens": 512,
            "concurrency": 1,     # 免费模型串行（受全局 concurrency 约束）
        },
    }
    # config.py → dialogue_pipeline → novel → application → src → repo root
    cfg_path = Path(__file__).resolve().parents[4] / "config.yaml"
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        loaded = dict((raw.get("novel_rag") or {}).get("dialogue_attribution") or {})
        cfg.update(loaded)
    except Exception as e:
        logger.debug("dialogue_attribution config load failed: %s", e)

    env_provider = os.getenv("NOVEL_DIALOGUE_ATTR_PROVIDER", "").strip().lower()
    if env_provider:
        cfg["provider"] = env_provider
    env_enabled = os.getenv("NOVEL_DIALOGUE_ATTR_ENABLED", "").strip().lower()
    if env_enabled in ("1", "true", "yes", "on"):
        cfg["enabled"] = True
    elif env_enabled in ("0", "false", "no", "off"):
        cfg["enabled"] = False
    return cfg


