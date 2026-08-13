"""Character inventory builder — document scan, cluster, LLM normalize.

Extracted from the former monolithic ``character_inventory.py``; logic unchanged.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from src.domain.novel.character_inventory.candidates import (
    _inventory_config,
    load_inventory_candidates,
)
from src.domain.novel.character_inventory.models import InventoryCharacter, InventoryResult

logger = logging.getLogger("agent")

_SYSTEM_V2 = """你是轻小说角色归一器。一次处理全部候选实体簇，输出【干净的角色名单】。

每簇包含：surfaces（名字表面形式）、count（出现次数）、evidence（原文上下文）、
types（粗扫类型标注，如 {"person": 3, "speaking_skill": 1}——仅供参考）、
attributes（属性挂接，如 [{"type":"race","value":"史莱姆"}]——粗扫先验，可修正）。

实体候选已由粗扫限定为 person / speaking_skill 两类（属性类名词不会出现在这里），
你的职责是【合并/拆分/属性校验】，不再需要删除泛称/种族/地名（它们已作为属性存在）。

规则：
1. 合并 — 同一实体的多个簇合并为一条：
   - 简称+全名（八奈见 + 八奈见杏菜 → canonical=八奈见杏菜）
   - 姓碎片+全名（绫野 + 绫野光希 → canonical=绫野光希）
   - canonical 选 surfaces/evidence 中最长最正式的形式；简称/变体进 aliases
2. 拆分 — 不同实体即使共享字或姓，不得合并：
   - 温水和彦 与 温水佳树 是两个人（兄妹）
   - 拿不准就拆成两条
3. 属性校验 — 检查 attributes 挂接是否合理：
   - person 可挂 role/race/title/skill_attr；speaking_skill 通常无属性
   - 属性值与 evidence 不符 → 删除该条属性
   - 若某候选名其实是属性值（如"矮人""勇者"被误列实体）→ 删除该实体（进 dropped），
     理由写"非实体，实为 race/role 属性"
4. 全名选择 — canonical_name 必须是 evidence/surfaces 中出现的最长纯人名：
   - 不要选包含叙述文字的长片段（如"发出声音的是八奈见杏"是错误的）
   - canonical 必须能从 evidence 或 surfaces 中验证
5. 禁止幻觉：没有 evidence 不要编造全名。拿不准 → importance=extra。
6. 每个簇只能出现在 characters 或 dropped 中的一处；同一簇不得拆给多个角色。
7. 只输出 JSON：
{"characters":[{"canonical_name":"...","aliases":["..."],"importance":"main|supporting|extra","from_clusters":["c1","c27"],"attributes":[{"type":"race","value":"..."}]}],"dropped":[{"from_clusters":["c9"],"reason":"..."}]}
"""


def _document_text(document: Any, max_chars: int) -> str:
    parts: list[str] = []
    for ch in getattr(document, "chapters", None) or []:
        t = (getattr(ch, "text", None) or "").strip()
        if t:
            title = getattr(ch, "title", "") or ""
            parts.append(f"【{title}】\n{t}" if title else t)
    text = "\n\n".join(parts)
    if max_chars > 0 and len(text) > max_chars:
        # head + mid + tail sample
        third = max_chars // 3
        mid_start = max(0, (len(text) - third) // 2)
        text = (
            text[:third]
            + "\n…\n"
            + text[mid_start : mid_start + third]
            + "\n…\n"
            + text[-third:]
        )
    return text


def _parse_llm_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {}
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return {}


async def _llm_normalize_global(
    llm_client: Any,
    clusters: Sequence[Any],
    *,
    series_hint: str = "",
    name_types: dict[str, str] | None = None,
    name_attributes: dict[str, list[dict]] | None = None,
) -> tuple[list[InventoryCharacter], list[dict]]:
    """方案 I：一次 LLM 调用看到全部簇，全局归一（消灭跨簇盲区）。

    evidence 增强到 6-8 条/簇（旧方案每簇仅 2 条），LLM 同时做合并/拆分/去噪/选全名。
    ``name_types``：提取阶段（llm_ner）的类型标注（name→角色/技能/…），
    按簇内 surfaces 聚合后随 payload 传入，归一按 type 驱动裁决。
    ``name_attributes``：V4 实体本体的属性挂接（name→[{type,value}]），已由
    cluster_mentions 聚合到簇，随 payload 传入供归一校验挂接合理性。
    """
    from collections import Counter

    from src.domain.novel.character_ner import DraftCluster

    def _cluster_type_counts(c: DraftCluster) -> dict[str, int]:
        """簇内 surfaces 的类型标注累计（多数裁决依据，无"角色"偏置）。"""
        if not name_types:
            return {}
        cnt: Counter = Counter()
        for s in c.surfaces:
            ts = name_types.get(s)
            if not ts:
                continue
            if isinstance(ts, dict):
                for t, n in ts.items():
                    cnt[t] += n
            else:
                cnt[ts] += 1
        return dict(cnt)

    payload_clusters = []
    for c in clusters:
        assert isinstance(c, DraftCluster)
        # evidence：每簇最多 4 条、每条截 80 字符（NVIDIA 免费端点响应时间与
        # payload 强相关——62KB 需 ~170s 接近断连极限；减半后稳定在 60-90s）
        evidence: list[str] = []
        for ev in c.evidence:
            short = ev.strip()[:80]
            if short:
                evidence.append(short)
            if len(evidence) >= 4:
                break
        payload_clusters.append(
            {
                "id": c.cluster_id,
                "surfaces": c.surfaces[:8],
                "count": c.count,
                "evidence": evidence,
                "types": _cluster_type_counts(c),
                "attributes": list(getattr(c, "attributes", None) or [])[:12],
            }
        )
    user = json.dumps(
        {"series_hint": series_hint, "clusters": payload_clusters},
        ensure_ascii=False,
    )
    raw = await llm_client.achat(
        [
            {"role": "system", "content": _SYSTEM_V2},
            {"role": "user", "content": user + "\n\n请输出 JSON。"},
        ],
        temperature=0.0,
        max_tokens=4096,
    )
    data = _parse_llm_json(raw or "")
    by_id = {c.cluster_id: c for c in clusters}

    kept: list[InventoryCharacter] = []
    for item in data.get("characters") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("canonical_name") or "").strip()
        if not name or len(name) < 2:
            continue
        aliases = [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()]
        aliases = sorted(set(a for a in aliases if a != name))
        from_ids = [str(x) for x in (item.get("from_clusters") or [])]
        mention = 0
        for cid in from_ids:
            if cid in by_id:
                mention += by_id[cid].count
        # V4 属性挂接：归一可增删修正；兜底继承簇属性（归一未输出时）
        attrs = [
            {"type": str(a.get("type") or "").strip(), "value": str(a.get("value") or "").strip()}
            for a in (item.get("attributes") or [])
            if isinstance(a, dict) and (a.get("type") or "").strip() and (a.get("value") or "").strip()
        ]
        if not attrs:
            seen_attr: set[tuple[str, str]] = set()
            for cid in from_ids:
                if cid in by_id:
                    for a in getattr(by_id[cid], "attributes", None) or []:
                        key = (str(a.get("type") or ""), str(a.get("value") or ""))
                        if key[0] and key[1] and key not in seen_attr:
                            seen_attr.add(key)
                            attrs.append({"type": key[0], "value": key[1]})
        kept.append(
            InventoryCharacter(
                canonical_name=name,
                aliases=aliases[:16],
                importance=str(item.get("importance") or "supporting"),
                mention_count=mention or 1,
                from_clusters=from_ids,
                attributes=attrs[:16],
            )
        )

    dropped = [dict(d) for d in (data.get("dropped") or []) if isinstance(d, dict)]
    # 代码层一致性校验：同一簇不得既在 characters 又在 dropped（归一输出冲突兜底）
    # 冲突时以 dropped 为准（宁可删不可污染）；重复归因（一簇给多角色）保留 mention 最高者。
    dropped_ids: set[str] = set()
    for dr in dropped:
        for cid in dr.get("from_clusters") or []:
            dropped_ids.add(str(cid))
    if dropped_ids:
        filtered: list[InventoryCharacter] = []
        for c in kept:
            clash = [cid for cid in c.from_clusters if cid in dropped_ids]
            if clash:
                logger.warning(
                    "Normalize conflict: %s refs dropped clusters %s — removing entity",
                    c.canonical_name, clash,
                )
                continue
            filtered.append(c)
        kept = filtered
    return kept, dropped


def _persist_alias_json(
    series_id: str,
    characters: list[InventoryCharacter],
    llm_skipped: bool = False,
) -> None:
    """方案 I：LLM 归一 + 校验后的 characters 直接落 alias.json。

    不再需要 R4 归并——LLM 全局调用已经给了完整的 canonical+aliases。
    """
    from datetime import datetime, timezone

    from src.api.routers.alias_roster import write_alias

    entities = []
    for c in characters:
        entities.append({
            "canonical_name": c.canonical_name,
            "aliases": list(c.aliases or []),
            "importance": c.importance or "extra",
            "mention_count": c.mention_count or 0,
        })

    # 去重 canonical
    seen = set()
    deduped = []
    for e in entities:
        if e["canonical_name"] not in seen:
            seen.add(e["canonical_name"])
            deduped.append(e)

    write_alias(
        series_id,
        {
            "entities": deduped,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "meta": {
                "total": len(deduped),
                "llm_skipped": llm_skipped,
            },
        },
    )
    logger.debug("alias.json written for %s (%d entities)", series_id, len(deduped))


def _fallback_from_clusters(clusters: Sequence[Any]) -> list[InventoryCharacter]:
    from src.domain.novel.dialogue_span import is_noise_speaker

    out: list[InventoryCharacter] = []
    for c in clusters:
        name = c.primary()
        if not name or is_noise_speaker(name):
            continue
        aliases = [s for s in c.surfaces if s != name][:8]
        out.append(
            InventoryCharacter(
                canonical_name=name,
                aliases=aliases,
                importance="extra",
                mention_count=c.count,
                from_clusters=[c.cluster_id],
            )
        )
    return out


async def build_character_inventory(
    document: Any,
    *,
    series_id: str = "",
    llm_client: Any = None,
    normalize_llm_client: Any = None,
    config: dict | None = None,
) -> InventoryResult:
    """Full inventory pipeline for one document.

    ``llm_client``：粗召回（分批提取 + type 标注）用——推荐快模型（glm 免费）。
    ``normalize_llm_client``：全局归一（合并/去噪/拆分的复杂裁决）用——推荐强模型
    （DeepSeek）；未传则回退 ``llm_client``。
    """
    from src.domain.novel.character_ner import cluster_mentions, extract_person_mentions

    cfg = dict(config or _inventory_config())
    if not cfg.get("enabled", True):
        return InventoryResult(meta={"enabled": False})

    max_chars = int(cfg.get("max_chars", 80000))
    min_m = int(cfg.get("min_cluster_mentions", 2))
    device = str(cfg.get("device") or "cpu")
    max_for_llm = int(cfg.get("max_clusters_for_llm", 60))

    # 粗召回 backend：llm（默认）走更大文本上限（一次扫全文能力），
    # cluener（本地模型）保持 8 万采样（NER 推理成本约束）。
    ner_backend = str(cfg.get("ner", "llm") or "llm").strip().lower()
    batch_chars = int(cfg.get("llm_batch_chars", 60000))
    if ner_backend == "llm":
        # llm 分批盘点：全文不采样（mentions 全文定位需要完整文本）
        max_chars = 0

    text = _document_text(document, max_chars)
    if not text.strip():
        return InventoryResult(meta={"error": "empty_text"})

    # ── 粗召回：llm（默认，一次扫全文 / 超长按章分批）| cluener（本地模型，降级/可选）──
    mentions = []
    relations: list[dict] = []
    if ner_backend == "llm":
        try:
            from src.domain.novel.character_inventory.llm_ner import (
                extract_names_by_chapter_batches,
                extract_names_llm,
                mentions_from_names,
            )

            chapters = list(getattr(document, "chapters", None) or [])
            if chapters and len(text) > batch_chars:
                # 按章分批（每批 ≤llm_batch_chars，章边界零切半）联合抽取；
                # 批间并发 llm_concurrency（NVIDIA 免费端点 4 并发实测无 429）
                res = await extract_names_by_chapter_batches(
                    chapters,
                    llm_client,
                    batch_chars=batch_chars,
                    max_names=int(cfg.get("llm_max_names", 60)),
                    max_tokens=int(cfg.get("llm_max_tokens", 4096)),
                    concurrency=int(cfg.get("llm_concurrency", 1)),
                )
            else:
                single = await extract_names_llm(
                    text,
                    llm_client,
                    max_names=int(cfg.get("llm_max_names", 60)),
                    max_tokens=int(cfg.get("llm_max_tokens", 4096)),
                )
                res = {
                    "names": [
                        {
                            "name": d["name"],
                            "types": [d["type"]],
                            "attributes": d.get("attributes") or [],
                        }
                        for d in single.get("names") or []
                        if (d.get("name") or "").strip()
                    ],
                    "relations": single.get("relations") or [],
                }
            names = res.get("names") or []
            relations = res.get("relations") or []
            # 名字列表（去 type）用于原文定位；type 标注（含跨批多类型）与属性
            # 挂接（name → [{type,value}]）传给归一阶段裁决。
            name_texts = [
                d["name"] for d in names if (d.get("name") or "").strip()
            ]

            name_types: dict[str, dict[str, int]] = {}
            name_attributes: dict[str, list[dict]] = {}
            for d in names:
                n = (d.get("name") or "").strip()
                if not n:
                    continue
                types = d.get("types") if isinstance(d.get("types"), list) else None
                if types:
                    name_types.setdefault(n, {}).update({t: 1 for t in types})
                elif d.get("type"):
                    name_types.setdefault(n, {}).update({d["type"]: 1})
                # V4 属性挂接：跨批去重累积（同 type+value 只留一次）
                for a in d.get("attributes") or []:
                    if not isinstance(a, dict):
                        continue
                    at = str(a.get("type") or "").strip()
                    av = str(a.get("value") or "").strip()
                    if at and av:
                        cur = name_attributes.setdefault(n, [])
                        if {"type": at, "value": av} not in cur:
                            cur.append({"type": at, "value": av})
            if name_texts:
                mentions = mentions_from_names(text, name_texts)
            logger.info(
                "Inventory LLM scan: names=%d mentions=%d relations=%d (batches=%s)",
                len(name_texts),
                len(mentions),
                len(relations),
                "yes" if (chapters and len(text) > batch_chars) else "no",
            )
            if not name_types:
                name_types = None
            if not name_attributes:
                name_attributes = None
        except Exception as e:
            logger.warning("Inventory LLM full-scan failed: %s", e)
        if not mentions:
            logger.warning(
                "Inventory LLM full-scan empty/failed — falling back to CLUENER"
            )
    if not mentions:
        try:
            mentions = extract_person_mentions(
                text, device=device, min_conf=float(cfg.get("ner_min_conf", 0.3))
            )
        except Exception as e:
            logger.warning("CLUENER inventory failed: %s", e)
            return InventoryResult(meta={"error": f"ner_failed:{e}"})

    clusters = cluster_mentions(
        mentions,
        min_mentions=min_m if len(text) > 3000 else 1,
        text=text,
        name_attributes=name_attributes,
    )
    # Cap for LLM cost
    clusters_for_llm = clusters[:max_for_llm]

    characters: list[InventoryCharacter] = []
    dropped: list[dict] = []
    llm_calls = 0
    llm_skipped = False

    normalize_client = normalize_llm_client or llm_client
    if normalize_client is None:
        llm_skipped = True
        characters = _fallback_from_clusters(clusters_for_llm)
    else:
        import asyncio as _asyncio

        # NVIDIA 免费端点偶发断连：归一失败自动重试（最多 3 次，退避 5s/15s）
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                kept, drop = await _llm_normalize_global(
                    normalize_client, clusters_for_llm, series_hint=series_id,
                    name_types=name_types,
                    name_attributes=name_attributes,
                )
                llm_calls += 1
                characters.extend(kept)
                dropped.extend(drop)
                break
            except Exception as e:  # noqa: BLE001
                last_exc = e
                logger.warning(
                    "Inventory LLM global failed (attempt %d/3): %s", attempt + 1, e
                )
                if attempt < 2:
                    await _asyncio.sleep(5 * (attempt + 1))
        else:
            logger.warning("Inventory LLM global failed after 3 attempts: %s", last_exc)
            characters = _fallback_from_clusters(clusters_for_llm)

    # Dedupe by canonical
    merged: dict[str, InventoryCharacter] = {}
    for c in characters:
        key = c.canonical_name
        if key in merged:
            prev = merged[key]
            prev.aliases = sorted(set(prev.aliases) | set(c.aliases) - {key})
            prev.mention_count = max(prev.mention_count, c.mention_count)
            prev.from_clusters = sorted(set(prev.from_clusters) | set(c.from_clusters))
        else:
            merged[key] = c
    characters = sorted(merged.values(), key=lambda x: (-x.mention_count, x.canonical_name))

    # LLM sometimes drops everyone (over-aggressive noise filter). Keep NER clusters
    # so dialogue quotas still get mention-ranked main/supporting targets.
    used_cluster_fallback = False
    if not characters and clusters_for_llm:
        logger.warning(
            "Inventory LLM kept 0/%d clusters; falling back to NER cluster names",
            len(clusters_for_llm),
        )
        characters = _fallback_from_clusters(clusters_for_llm)
        used_cluster_fallback = True

    # Overlay / inject series sidecar so cluster_fallback still has 利姆露 + 史莱姆 alias.
    if series_id:
        try:
            prev = load_inventory_candidates(series_id) or {}
            by_prev = {
                str(c.get("name") or "").strip(): c
                for c in (prev.get("candidates") or [])
                if str(c.get("name") or "").strip()
            }
            existing = {c.canonical_name: c for c in characters}
            for name, old in by_prev.items():
                if name in existing:
                    c = existing[name]
                    extra = [
                        str(a).strip()
                        for a in (old.get("aliases") or [])
                        if str(a).strip() and str(a).strip() != c.canonical_name
                    ]
                    if extra:
                        c.aliases = sorted(set(c.aliases) | set(extra))
                    c.mention_count = max(
                        int(c.mention_count or 0), int(old.get("mention_count") or 0)
                    )
                else:
                    # Inject stable series canonicals missing from this-volume NER
                    characters.append(
                        InventoryCharacter(
                            canonical_name=name,
                            aliases=[
                                str(a).strip()
                                for a in (old.get("aliases") or [])
                                if str(a).strip() and str(a).strip() != name
                            ][:12],
                            importance=str(old.get("importance") or "extra"),
                            mention_count=int(old.get("mention_count") or 0),
                            from_clusters=list(old.get("from_clusters") or []),
                        )
                    )
        except Exception as e:
            logger.debug("inventory series union skipped: %s", e)

    # Ensure dialogue quotas see main/supporting even when LLM marks everyone extra.
    # R1/R2/R3: alias collision merge + blacklist + mention TopN (shared with dialogue_quota).
    from src.domain.novel.dialogue_quota import (
        assign_importance_by_mentions,
        load_importance_tier_settings,
        merge_alias_collisions,
        merge_near_duplicates,
    )

    tier = load_importance_tier_settings()
    merged_log: list[dict] = []
    near_log: list[dict] = []
    if tier.get("merge_alias_collisions", True) and characters:
        characters, merged_log = merge_alias_collisions(characters)
    if tier.get("merge_near_duplicates", True) and characters:
        characters, near_log = merge_near_duplicates(
            characters,
            max_distance=int(tier.get("near_duplicate_max_distance", 2)),
            min_len=int(tier.get("near_duplicate_min_len", 4)),
        )
    if tier.get("promote_importance_by_mentions", True):
        assign_importance_by_mentions(
            characters,
            main_top_n=int(tier.get("main_top_n", 5)),
            supporting_top_n=int(tier.get("supporting_top_n", 20)),
            blacklist=list(tier.get("importance_blacklist") or []),
        )

    result = InventoryResult(
        characters=characters,
        dropped=dropped,
        draft_clusters=len(clusters),
        llm_calls=llm_calls,
        llm_skipped=llm_skipped,
        relations=relations,
        meta={
            "mentions": len(mentions),
            "clusters": len(clusters),
            "kept": len(characters),
            "dropped": len(dropped),
            "text_chars": len(text),
            "device": device,
            "cluster_fallback": used_cluster_fallback,
            "merged_alias_collisions": len(merged_log),
            "merged_near_duplicates": len(near_log),
            "importance_blacklisted": sum(
                1
                for c in characters
                if c.canonical_name in set(tier.get("importance_blacklist") or [])
            ),
        },
    )
    logger.info(
        "Character inventory: mentions=%d clusters=%d kept=%d dropped=%d llm_calls=%d skipped=%s",
        len(mentions),
        len(clusters),
        len(characters),
        len(dropped),
        llm_calls,
        llm_skipped,
    )

    # ── 方案 I 校验层：确定性裁决（零 LLM 成本）──
    # 规则违规（别名冲突/作家名/身份词）用确定性逻辑修正；仍无法自动解决时
    # 拒绝落盘（回退 NER 簇 + 告警），避免把污染表写入 alias.json。
    if characters and llm_client is not None and not llm_skipped:
        from src.domain.novel.character_inventory.validate import resolve_violations

        # 转换为 dict 格式
        char_dicts = [
            {
                "canonical_name": c.canonical_name,
                "aliases": list(c.aliases or []),
                "importance": c.importance,
                "mention_count": c.mention_count,
                "from_clusters": c.from_clusters,
            }
            for c in characters
        ]
        fixed_chars, fixed_dropped, remaining = resolve_violations(
            char_dicts, dropped, series_hint=series_id
        )
        if remaining:
            logger.warning(
                "Inventory validation left %d unresolved violations — falling back "
                "to NER clusters (avoid persisting polluted table): %s",
                len(remaining),
                remaining[:3],
            )
            characters = _fallback_from_clusters(clusters_for_llm)
            dropped = []
        else:
            # 转换回 InventoryCharacter
            characters = [
                InventoryCharacter(
                    canonical_name=c["canonical_name"],
                    aliases=c.get("aliases", []),
                    importance=c.get("importance", "extra"),
                    mention_count=c.get("mention_count", 0),
                    from_clusters=c.get("from_clusters", []),
                )
                for c in fixed_chars
                if c.get("canonical_name", "").strip()
            ]
            dropped = fixed_dropped

    # ── 方案 I：LLM 全局归一 + 校验后直接落 alias.json ──
    # 不需要额外的 R4 归并——LLM 那次调用已经给出了完整的 canonical+aliases。
    if characters and series_id:
        _persist_alias_json(series_id, characters, llm_skipped)
        logger.info("alias.json persisted for '%s' (%d characters)", series_id, len(characters))

    return result


