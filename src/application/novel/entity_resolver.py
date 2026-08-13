"""EntityResolver — 查询理解前置层，将称谓/别名解析为规范实体并贯穿检索管线。

在 IntentRouter / QueryRewriter / NovelStore.search 之前运行，产出
``QueryContext`` 作为单一事实源，解决各组件孤立猜测角色、系列、doc_id 的问题。

核心流程：
    user query
      → AliasMap.resolve_in_query（确定性字符串匹配，含 titles/aliases）
      → series_id 推导（从命中实体反查）
      → doc_ids 派生（从 source_doc_ids 取交集/前缀）
      → augmented_query（原词 + 追加规范名，不替换）
      → QueryContext 向后传递

设计决策：
    - 追加而非替换：保留"会长"原词 + 追加"月之木古都"，避免误展开
    - 确定性优先：regex + AliasMap 字符串匹配，不调 LLM（LLM 留给 Router 做意图分类）
    - 复用 AliasMap.resolve_in_query：不重写别名解析逻辑
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.domain.novel.alias_map import AliasEntity, AliasMap, load_alias_map

logger = logging.getLogger("agent.entity_resolver")

from src.domain.novel.series_paths import data_root

_ROSTER_DIR = data_root() / "rosters"
_CHARACTER_DIR = data_root() / "characters"


@dataclass
class ResolvedEntity:
    """query 中解析出的单个角色实体。"""

    canonical_name: str           # 月之木古都
    matched_surface: str          # 会长（query 中实际出现的词）
    surface_type: str             # "title" | "alias" | "canonical"
    aliases: list[str]            # [会长, 月之木, 月之木学姐, ...]（含 titles）
    source_doc_ids: list[str]     # 该角色出现的 doc_id 列表

    @property
    def filter_names(self) -> list[str]:
        """用于 metadata 过滤的名字（canonical + aliases，不含 titles）。

        titles 不参与 metadata 过滤，因为对话块的 characters_json 存的是
        角色名而非称谓——"会长"不会出现在 characters_json 中。
        """
        names = [self.canonical_name]
        names.extend(a for a in self.aliases if a and a not in names)
        return names


@dataclass
class QueryContext:
    """管线单一事实源，由 EntityResolver 产出，向后传递给所有组件。

    各消费方按需读取字段：
    - LLMIntentRouter: alias_hints（注入 prompt）+ resolved_entities（候选角色）
    - QueryRewriter:   alias_hints + augmented_query（多变体生成）
    - NovelRetrieval:  doc_ids（硬隔离）+ resolved_entities（filters）
    - NovelStore:      filter_names（characters 过滤）
    """

    original_query: str
    augmented_query: str                  # 原词 + 追加规范名（不替换）
    resolved_entities: list[ResolvedEntity] = field(default_factory=list)
    series_id: str | None = None
    doc_ids: list[str] = field(default_factory=list)
    alias_hints: str = ""                 # 给 LLM prompt 用：月之木古都(会长)、志喜屋梦子(副会长)
    is_cross_series: bool = False         # 显式跨系列查询标志（多系列命中时 True）

    @property
    def primary_entity(self) -> ResolvedEntity | None:
        """主要解析实体（第一个），用于单角色查询场景。"""
        return self.resolved_entities[0] if self.resolved_entities else None

    @property
    def primary_doc_id(self) -> str | None:
        """派生的主 doc_id（取第一个），用于硬隔离。None 表示无法派生。"""
        return self.doc_ids[0] if self.doc_ids else None

    @property
    def all_filter_names(self) -> list[str]:
        """所有解析实体的过滤名字汇总。"""
        names: list[str] = []
        for e in self.resolved_entities:
            for n in e.filter_names:
                if n and n not in names:
                    names.append(n)
        return names


class EntityResolver:
    """管线入口的确定性实体解析层。

    扫描 query 中的角色称谓/别名，产出 QueryContext 供下游消费。
    维护 AliasMap 缓存（按 series_id），避免每次查询都读磁盘。
    """

    def __init__(self, *, alias_cache_ttl: int = 300, roster_dir: Path | str | None = None,
                 character_dir: Path | str | None = None):
        self._roster_dir = Path(roster_dir) if roster_dir else _ROSTER_DIR
        self._character_dir = (
            Path(character_dir) if character_dir else _CHARACTER_DIR
        )
        # series_id → AliasMap，None 键存"全系列扫描"用的所有 map
        self._alias_maps: dict[str, AliasMap] = {}
        self._all_maps: list[AliasMap] | None = None
        self._alias_cache_ttl = alias_cache_ttl
        # canonical_name → source_doc_ids（角色卡回填缓存，避免每次查询读盘）
        self._char_docs_cache: dict[str, list[str]] = {}

    def _load_character_docs(
        self, canonical_name: str, series_id: str | None
    ) -> list[str]:
        """从角色卡回填 source_doc_ids（{series}__{name}.json / {name}.json）。

        角色卡（data/characters/）的 profile.source_doc_ids 记录了该角色出现的
        doc_id 列表；回填后 EntityResolver 派生的 doc_id 才能自动锁定作品，
        避免称谓查询（如"会长"）跨作品检索。
        """
        if not canonical_name:
            return []
        key = f"{series_id or ''}|{canonical_name}"
        cached = self._char_docs_cache.get(key)
        if cached is not None:
            return cached
        docs: list[str] = []
        try:
            import json

            candidates: list[Path] = []
            if series_id:
                candidates.append(
                    self._character_dir / f"{series_id}__{canonical_name}.json"
                )
            candidates.append(self._character_dir / f"{canonical_name}.json")
            for path in candidates:
                if not path.is_file():
                    continue
                raw = json.loads(path.read_text(encoding="utf-8"))
                profile = raw.get("profile") or {}
                docs = [
                    str(d) for d in (profile.get("source_doc_ids") or [])
                    if str(d).strip()
                ]
                break
            # 兜底：glob 匹配 {anything}__{name}.json（系列名可能含变体写法）
            if not docs and self._character_dir.is_dir():
                import re

                pat = re.compile(rf"^.*__{re.escape(canonical_name)}\.json$")
                for path in sorted(self._character_dir.glob("*.json")):
                    if pat.match(path.name):
                        raw = json.loads(path.read_text(encoding="utf-8"))
                        profile = raw.get("profile") or {}
                        docs = [
                            str(d) for d in (profile.get("source_doc_ids") or [])
                            if str(d).strip()
                        ]
                        if docs:
                            break
        except (OSError, ValueError, TypeError) as exc:
            logger.debug(
                "Character card docs load failed for %s: %s", canonical_name, exc
            )
        self._char_docs_cache[key] = docs
        return docs

    def resolve(
        self,
        query: str,
        *,
        hint_series: str | None = None,
        hint_doc_ids: list[str] | None = None,
    ) -> QueryContext:
        """解析 query 中的实体，产出 QueryContext。

        Args:
            query: 用户原始 query
            hint_series: 提示系列 ID（如已知当前会话角色所属系列），优先扫描该系列
            hint_doc_ids: 提示 doc_id 列表（如角色卡的 source_doc_ids），用于派生隔离

        Returns:
            QueryContext，向后传递给 Router/Rewriter/Store
        """
        query = (query or "").strip()
        if not query:
            return QueryContext(original_query=query, augmented_query=query)

        # 1. 加载 AliasMap（优先 hint_series）
        maps_to_scan = self._load_maps_for_scan(hint_series)

        # 2. 扫描 query 中出现的所有实体
        resolved: list[ResolvedEntity] = []
        seen_canonical: set[str] = set()
        for amap in maps_to_scan:
            for entity in amap.resolve_in_query(query):
                if entity.canonical_name in seen_canonical:
                    continue
                seen_canonical.add(entity.canonical_name)
                resolved.append(
                    self._build_resolved_entity(
                        query, entity, series_id=amap.series_id
                    )
                )

        # 3. 推导 series_id
        series_id = self._derive_series_id(resolved, hint_series)

        # 4. 派生 doc_ids
        doc_ids = self._derive_doc_ids(resolved, hint_doc_ids)

        # 5. 构建 augmented_query（原词 + 追加规范名）
        augmented = self._build_augmented_query(query, resolved)

        # 6. 构建 alias_hints 串
        alias_hints = self._build_alias_hints(resolved)

        # 7. 判断是否跨系列
        is_cross_series = self._check_cross_series(resolved)

        ctx = QueryContext(
            original_query=query,
            augmented_query=augmented,
            resolved_entities=resolved,
            series_id=series_id,
            doc_ids=doc_ids,
            alias_hints=alias_hints,
            is_cross_series=is_cross_series,
        )

        logger.debug(
            "EntityResolver: query=%r → entities=%s, series=%s, doc_ids=%s, cross=%s",
            query[:60],
            [e.canonical_name for e in resolved],
            series_id,
            doc_ids[:2],
            is_cross_series,
        )
        return ctx

    # ── 内部方法 ──────────────────────────────────────────

    def _load_maps_for_scan(self, hint_series: str | None) -> list[AliasMap]:
        """加载用于扫描的 AliasMap 列表。

        优先扫描 hint_series 的 map；若无 hint，扫描所有已落盘的 map。
        """
        maps: list[AliasMap] = []
        if hint_series:
            amap = self._get_or_load_map(hint_series)
            if amap and amap.entities:
                maps.append(amap)
        if not maps:
            # 全系列扫描
            for m in self._load_all_maps():
                if m.entities:
                    maps.append(m)
        return maps

    def _get_or_load_map(self, series_id: str) -> AliasMap | None:
        """按 series_id 加载 AliasMap（带缓存）。"""
        if series_id in self._alias_maps:
            return self._alias_maps[series_id]
        amap = load_alias_map(series_id)
        if amap:
            self._alias_maps[series_id] = amap
        return amap

    def _load_all_maps(self) -> list[AliasMap]:
        """扫描 roster 目录，加载所有 .alias.json 文件。"""
        if self._all_maps is not None:
            return self._all_maps
        maps: list[AliasMap] = []
        if not self._roster_dir.is_dir():
            self._all_maps = maps
            return maps
        import json

        for path in sorted(self._roster_dir.glob("*.alias.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                # 兼容两种格式：AliasMap（有 series_id）和 roster（无 series_id）
                entities_data = raw.get("entities") or []
                if not entities_data:
                    continue
                series_id = raw.get("series_id") or path.stem.replace(".alias", "")
                amap = AliasMap(
                    series_id=str(series_id),
                    entities=[AliasEntity.from_dict(e) for e in entities_data],
                    updated_at=str(raw.get("updated_at") or ""),
                )
                maps.append(amap)
            except (OSError, ValueError, TypeError) as e:
                logger.warning("Failed to load alias map %s: %s", path, e)
        self._all_maps = maps
        return maps

    def _build_resolved_entity(
        self, query: str, entity: AliasEntity, series_id: str | None = None
    ) -> ResolvedEntity:
        """从 AliasEntity 构建 ResolvedEntity，识别 matched_surface 和 surface_type。

        source_doc_ids 从角色卡回填（见 _load_character_docs），使称谓解析
        自动携带作品归属，派生 doc_id 实现硬隔离。
        """
        matched_surface = ""
        surface_type = "canonical"
        # 优先匹配 titles（用户最常用称谓）
        for title in entity.titles:
            if title and title in query:
                matched_surface = title
                surface_type = "title"
                break
        if not matched_surface:
            # 匹配 aliases（按长度降序，长名优先）
            for alias in sorted(entity.aliases, key=len, reverse=True):
                if alias and alias in query:
                    matched_surface = alias
                    surface_type = "alias"
                    break
        if not matched_surface and entity.canonical_name in query:
            matched_surface = entity.canonical_name
            surface_type = "canonical"
        if not matched_surface:
            matched_surface = entity.canonical_name
            surface_type = "canonical"

        return ResolvedEntity(
            canonical_name=entity.canonical_name,
            matched_surface=matched_surface,
            surface_type=surface_type,
            aliases=list(entity.aliases) + list(entity.titles),
            source_doc_ids=self._load_character_docs(
                entity.canonical_name, series_id
            ),
        )

    def _derive_series_id(
        self, resolved: list[ResolvedEntity], hint_series: str | None
    ) -> str | None:
        """从解析实体反推 series_id。

        策略：hint_series 优先；否则从 AliasMap 的 series_id 取（单系列命中时）。
        多系列命中时返回 None（交由调用方处理跨系列）。
        """
        if hint_series:
            return hint_series
        if not resolved:
            return None
        # 从已加载的 maps 反查每个实体所属系列
        series_set: set[str] = set()
        for amap in self._load_all_maps():
            for entity in resolved:
                if any(
                    entity.canonical_name == e.canonical_name
                    for e in amap.entities
                ):
                    if amap.series_id:
                        series_set.add(amap.series_id)
        if len(series_set) == 1:
            return series_set.pop()
        return None

    def _derive_doc_ids(
        self, resolved: list[ResolvedEntity], hint_doc_ids: list[str] | None
    ) -> list[str]:
        """派生 doc_id 列表用于硬隔离。

        策略：
        1. 若 resolved 实体的 source_doc_ids 非空，取交集
        2. 否则用 hint_doc_ids
        3. 否则从 series_id 派生前缀（series_id → doc_id 前缀）
        """
        # 1. 实体 source_doc_ids 交集
        if resolved:
            doc_sets = [
                set(e.source_doc_ids) for e in resolved if e.source_doc_ids
            ]
            if doc_sets:
                common = doc_sets[0]
                for ds in doc_sets[1:]:
                    common &= ds
                if common:
                    return sorted(common)
                # 无交集时取并集（多角色可能在不同卷）
                all_ids: set[str] = set()
                for ds in doc_sets:
                    all_ids |= ds
                if all_ids:
                    return sorted(all_ids)

        # 2. hint_doc_ids
        if hint_doc_ids:
            return [d for d in hint_doc_ids if d]

        # 3. series_id → 前缀（由 NovelRetrieval 调用时传入 series_id 派生）
        return []

    def _build_augmented_query(
        self, query: str, resolved: list[ResolvedEntity]
    ) -> str:
        """构建增强 query：原词 + 追加规范名（不替换原词）。

        例: "对会长的看法" → "对会长的看法 月之木古都"
        只追加 title/alias 类型的实体（canonical 类型已在 query 中）。
        """
        if not resolved:
            return query
        additions: list[str] = []
        for entity in resolved:
            if entity.surface_type in ("title", "alias"):
                if entity.canonical_name not in query:
                    additions.append(entity.canonical_name)
        if not additions:
            return query
        return f"{query} {' '.join(additions)}"

    def _build_alias_hints(self, resolved: list[ResolvedEntity]) -> str:
        """构建 LLM prompt 注入用的别名提示串。

        例: "月之木古都(会长)、志喜屋梦子(副会长)"
        只包含 title/alias 类型（canonical 类型无需提示）。
        """
        if not resolved:
            return ""
        hints: list[str] = []
        for entity in resolved:
            if entity.surface_type in ("title", "alias"):
                hints.append(f"{entity.canonical_name}({entity.matched_surface})")
        return "、".join(hints)

    def _check_cross_series(self, resolved: list[ResolvedEntity]) -> bool:
        """判断是否跨系列查询（多系列实体命中时 True）。"""
        if len(resolved) <= 1:
            return False
        series_set: set[str] = set()
        for amap in self._load_all_maps():
            for entity in resolved:
                if any(
                    entity.canonical_name == e.canonical_name
                    for e in amap.entities
                ):
                    if amap.series_id:
                        series_set.add(amap.series_id)
        return len(series_set) > 1


# ── 模块级便捷实例 ────────────────────────────────────────

_default_resolver: EntityResolver | None = None


def get_default_resolver() -> EntityResolver:
    """获取默认 EntityResolver 单例（懒加载）。"""
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = EntityResolver()
    return _default_resolver
