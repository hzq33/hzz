"""Character relationship graph for novel RAG — build + enrich search results.

Builds a MultiGraph from character/dialogue/narrative blocks during ingestion.
At query time, injects relational context (interactions, shared chapters, paths)
into search results for richer LLM reasoning.

Usage:
    graph = CharacterGraph()
    graph.build(character_blocks, dialogue_blocks, narrative_blocks)
    enriched = graph.enrich_results(search_results)
    # Each result now carries direct_relations, shared_chapters, etc.

Zero extra LLM calls — purely graph-traversal computation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from src.domain.novel.models import NovelBlock
from src.infrastructure.novel_store import SearchResultWithBlock

logger = logging.getLogger("agent")


def _json_default(obj):
    """JSON encoder fallback: frozenset/set→list."""
    if isinstance(obj, (frozenset, set)):
        return list(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ── Public types ─────────────────────────────────────────


@dataclass
class Relation:
    """A directed relationship between two characters."""
    character: str
    interaction_count: int = 0
    chapters: list[str] = field(default_factory=list)
    edge_types: list[str] = field(default_factory=list)  # "dialogue" | "co_occurrence" | "relation"
    # relation 边（事实源 story_analysis 派生）的语义信息：
    relation_type: str = ""
    polarity: str = ""
    confidence: float = 0.0
    change_id: str = ""


@dataclass
class EnrichedResult:
    """Search result with injected graph context."""
    global_id: str
    block_type: str
    score: float
    channel: str
    text: str                          # primary content (narrative_text or dialogue summary)
    characters_in_block: list[str] = field(default_factory=list)
    # Graph context
    direct_relations: list[Relation] = field(default_factory=list)
    shared_chapters: list[str] = field(default_factory=list)
    shortest_path: list[str] = field(default_factory=list)


# ── CharacterGraph ───────────────────────────────────────


class CharacterGraph:
    """Character relationship graph for enhanced novel RAG.

    Builds from character/dialogue/narrative blocks once during ingestion.
    Enriches search results with relational context at query time.

    Uses networkx.MultiGraph — multiple edges between same pair
    for different interaction types (dialogue vs co-occurrence).
    """

    __slots__ = ("graph", "_node_count")

    def __init__(self):
        self.graph = nx.MultiGraph()
        self._node_count = 0

    # ── Build ────────────────────────────────────────────

    @staticmethod
    def _is_noise_name(name: str, extra_noise: set[str] | None = None) -> bool:
        """占位/噪声名过滤（主角/种族名/代词碎片等）。"""
        if not name or name in (extra_noise or set()):
            return True
        if len(name) < 2:
            return True
        try:
            from src.domain.novel.dialogue_span import is_noise_speaker

            if is_noise_speaker(name):
                return True
        except Exception:  # noqa: BLE001 - best-effort noise filter
            pass
        return False

    def build(
        self,
        character_blocks: list[NovelBlock],
        dialogue_blocks: list[NovelBlock],
        narrative_blocks: list[NovelBlock],
        *,
        alias_map: dict[str, list[str]] | None = None,
        noise_names: set[str] | None = None,
        relations: list | None = None,
    ) -> CharacterGraph:
        """Build the graph from novel blocks. Called once during ingestion.

        ``alias_map`` (canonical → aliases, from the series roster/alias.json)
        resolves short forms found in dialogue/narrative blocks ("八奈见" →
        "八奈见杏菜") so one character never splits into multiple nodes.

        ``relations`` (可选，story_analysis 的 RelationChange 列表) 是关系事实源：
        P2 后图谱的关系边（type="relation"）从它构建（带类型/极性/证据），
        不再只依赖对话共现的弱信号。
        """
        # alias 归一表：alias → canonical（含去敬称后缀的宽松匹配）
        alias_to_canon: dict[str, str] = {}
        canon_to_aliases: dict[str, set[str]] = {}
        for canon, aliases in (alias_map or {}).items():
            canon = (canon or "").strip()
            if not canon:
                continue
            canon_to_aliases.setdefault(canon, set()).update(aliases or [])
            for a in aliases or []:
                a = (a or "").strip()
                if a:
                    alias_to_canon[a] = canon

        _HONOR_SUFFIXES = ("同学", "老师", "前辈", "小姐", "先生", "大人", "酱", "君", "桑")

        def resolve_canonical(name: str) -> str:
            """简称 → canonical；未命中保留原名。"""
            n = (name or "").strip()
            if not n:
                return ""
            if n in alias_to_canon:
                return alias_to_canon[n]
            if n in canon_to_aliases:
                return n
            # 去敬称后缀再匹配（"白玉同学" → "白玉" → "白玉莉子"）
            for suf in _HONOR_SUFFIXES:
                if n.endswith(suf) and len(n) > len(suf) + 1:
                    base = n[: -len(suf)]
                    return alias_to_canon.get(base, canon_to_aliases.get(base, n))
            return n

        # 1. Character nodes — use canonical_name from CharacterIdentity (v2.0)
        #    Falls back to character_name for backward compat
        for cb in character_blocks:
            # v2.0: prefer CharacterIdentity.canonical_name
            if cb.character_identity:
                node_name = cb.character_identity.canonical_name
                aliases = set(cb.character_identity.aliases or [])
                gender = cb.character_identity.gender.value if cb.character_identity.gender else None
            else:
                node_name = cb.character_name
                aliases = set()
                gender = None

            node_name = resolve_canonical(node_name)
            if not node_name or self._is_noise_name(node_name, noise_names):
                continue

            self.graph.add_node(node_name,
                type="character",
                personality=getattr(cb, "personality", ""),
                first_appearance=getattr(cb, "chapter_title", ""),
                aliases=sorted(aliases),
                gender=gender,
            )
            self._node_count += 1

        # Build alias→canonical lookup from character blocks + series alias_map
        name_lookup: dict[str, str] = {}
        for cb in character_blocks:
            if cb.character_identity:
                canonical = resolve_canonical(cb.character_identity.canonical_name)
                name_lookup[canonical] = canonical
                for alias in cb.character_identity.aliases:
                    name_lookup[alias] = canonical
            elif cb.character_name:
                canonical = resolve_canonical(cb.character_name)
                name_lookup[canonical] = canonical
                name_lookup[cb.character_name] = canonical
        # series 级 alias 映射（覆盖短名/变体）
        for canon, aliases in canon_to_aliases.items():
            resolved = resolve_canonical(canon)
            name_lookup.setdefault(resolved, resolved)
            for a in aliases:
                name_lookup.setdefault(a, resolved)
                name_lookup.setdefault(resolve_canonical(a), resolved)

        # 1b. Node fallback — seed nodes from dialogue speakers + narrative
        # all_person when no character blocks exist (generate_character_llm
        # is off by default, so character_blocks is usually empty). Without
        # this the dialogue/co-occurrence edges below can never connect and
        # the persisted graph is empty → retrieval graph_enrich is a no-op.
        for db in dialogue_blocks:
            for sp in (db.characters or []):
                canon = resolve_canonical(sp)
                if not canon or self._is_noise_name(canon, noise_names):
                    continue
                if canon not in self.graph:
                    self.graph.add_node(
                        canon,
                        type="character",
                        personality="",
                        first_appearance=getattr(db, "chapter_title", ""),
                        aliases=[],
                        gender=None,
                    )
                    self._node_count += 1
        for nb in narrative_blocks:
            for p in (getattr(nb, "all_person", None) or []):
                canon = resolve_canonical(p)
                if not canon or self._is_noise_name(canon, noise_names):
                    continue
                if canon not in self.graph:
                    self.graph.add_node(
                        canon,
                        type="character",
                        personality="",
                        first_appearance=getattr(nb, "chapter_title", ""),
                        aliases=[],
                        gender=None,
                    )
                    self._node_count += 1

        # 2. Dialogue edges (characters who speak in the same scene)
        for db in dialogue_blocks:
            speakers = db.characters or []
            # Normalize speaker names to canonical via lookup
            canonical_speakers = []
            for s in speakers:
                canonical = resolve_canonical(s)
                if canonical and canonical in self.graph:
                    canonical_speakers.append(canonical)
            for i, s1 in enumerate(canonical_speakers):
                for s2 in canonical_speakers[i + 1:]:
                    if s1 == s2:
                        continue  # 归一后同一角色（短名/变体）→ 跳过自环
                    key = f"{s1}|{s2}|{db.chapter_title or '?'}"
                    if not self.graph.has_edge(s1, s2, key=key):
                        self.graph.add_edge(s1, s2, key=key,
                            type="dialogue",
                            chapter=getattr(db, "chapter_title", ""),
                            weight=1,
                        )
                    else:
                        self.graph[s1][s2][key]["weight"] += 1

        # 3. Co-occurrence edges (characters mentioned in the same narrative)
        for nb in narrative_blocks:
            raw_persons = [p for p in (getattr(nb, "all_person", []) or []) if p]
            persons = list(dict.fromkeys(
                resolve_canonical(p) for p in raw_persons if resolve_canonical(p) in self.graph
            ))
            for i, p1 in enumerate(persons):
                for p2 in persons[i + 1:]:
                    if self.graph.has_edge(p1, p2):
                        for k in self.graph[p1][p2]:
                            if self.graph[p1][p2][k].get("type") == "co_occurrence":
                                self.graph[p1][p2][k]["weight"] += 1
                                break
                        else:
                            self.graph.add_edge(p1, p2,
                                type="co_occurrence", weight=1)
                    else:
                        self.graph.add_edge(p1, p2,
                            type="co_occurrence", weight=1)

        # 4. Fact-source relation edges (story_analysis 关系事实源)
        self._add_relation_edges(relations, resolve_canonical)

        logger.info("Graph built: %d nodes, %d edges",
            self.graph.number_of_nodes(), self.graph.number_of_edges())
        return self

    def _add_relation_edges(self, relations: list, resolve_canonical) -> int:
        """Add fact-source relation edges (type='relation') from story_analysis.

        关系是独立事实源：即使角色无 character block，也建节点建边
        （带 relation_type / polarity / confidence / change_id）。
        """
        if not relations:
            return 0
        added = 0
        for rel in relations:
            src = (getattr(rel, "source", "") or "").strip()
            tgt = (getattr(rel, "target", "") or "").strip()
            if not src or not tgt or src == tgt:
                continue
            src_c = resolve_canonical(src)
            tgt_c = resolve_canonical(tgt)
            if not src_c or not tgt_c or src_c == tgt_c:
                continue
            if self._is_noise_name(src_c) or self._is_noise_name(tgt_c):
                continue
            # 节点不存在也建（关系事实源优先）
            if src_c not in self.graph:
                self.graph.add_node(src_c, type="character", first_appearance="")
                self._node_count += 1
            if tgt_c not in self.graph:
                self.graph.add_node(tgt_c, type="character", first_appearance="")
                self._node_count += 1
            if self.graph.has_edge(src_c, tgt_c):
                # 同 pair 已有 relation 边 → 保留置信度最高的一条
                best_key, best_conf = None, -1.0
                for k, data in self.graph[src_c][tgt_c].items():
                    if data.get("type") == "relation":
                        conf = float(data.get("confidence", 0) or 0)
                        if conf > best_conf:
                            best_key, best_conf = k, conf
                if best_key is not None and best_conf >= float(rel.confidence or 0):
                    continue
                if best_key is not None:
                    self.graph.remove_edge(src_c, tgt_c, key=best_key)
            self.graph.add_edge(
                src_c,
                tgt_c,
                type="relation",
                weight=1,
                relation_type=(rel.relation_type or "").strip(),
                polarity=(rel.polarity or "neutral").strip(),
                confidence=float(rel.confidence or 0),
                change_id=(rel.change_id or "").strip(),
                chapter=getattr(rel, "chapter_title", "") or "",
            )
            added += 1
        if added:
            logger.info("Graph relation edges added: %d", added)
        return added

    # ── Query ────────────────────────────────────────────

    def get_relations(self, character: str) -> list[Relation]:
        """Get all relationships for a character, sorted by strength.

        relation 边（事实源）优先带出语义信息（类型/极性/置信度）；
        共现/对话边仅贡献互动计数。
        """
        if character not in self.graph:
            return []

        agg: dict[str, Relation] = {}
        for neighbor in self.graph.neighbors(character):
            chapters = set()
            total = 0
            types = set()
            best_rel = {"relation_type": "", "polarity": "", "confidence": 0.0, "change_id": ""}
            for data in self.graph[character][neighbor].values():
                total += data.get("weight", 1)
                if data.get("chapter"):
                    chapters.add(data["chapter"])
                etype = data.get("type", "")
                types.add(etype)
                if etype == "relation":
                    conf = float(data.get("confidence", 0) or 0)
                    if conf >= best_rel["confidence"]:
                        best_rel = {
                            "relation_type": (data.get("relation_type") or "").strip(),
                            "polarity": (data.get("polarity") or "").strip(),
                            "confidence": conf,
                            "change_id": (data.get("change_id") or "").strip(),
                        }

            agg[neighbor] = Relation(
                character=neighbor,
                interaction_count=total,
                chapters=sorted(chapters),
                edge_types=sorted(types),
                relation_type=best_rel["relation_type"],
                polarity=best_rel["polarity"],
                confidence=best_rel["confidence"],
                change_id=best_rel["change_id"],
            )

        return sorted(agg.values(), key=lambda r: -r.interaction_count)

    def shared_chapters(self, c1: str, c2: str) -> list[str]:
        """Chapters where both characters appear."""
        if c1 not in self.graph or c2 not in self.graph:
            return []

        c1_ch = set()
        for _, _, d in self.graph.edges(c1, data=True):
            if d.get("chapter"):
                c1_ch.add(d["chapter"])

        c2_ch = set()
        for _, _, d in self.graph.edges(c2, data=True):
            if d.get("chapter"):
                c2_ch.add(d["chapter"])

        return sorted(c1_ch & c2_ch)

    def shortest_path(self, c1: str, c2: str) -> list[str]:
        """Shortest path between two characters in the graph."""
        try:
            return nx.shortest_path(self.graph, c1, c2)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def characters(self) -> list[str]:
        """List all characters in the graph."""
        return sorted(self.graph.nodes())

    # ── Enrich ───────────────────────────────────────────

    def enrich_results(
        self,
        results: list[SearchResultWithBlock],
        *,
        focus_character: str | None = None,
    ) -> list[EnrichedResult]:
        """Inject graph context into search results.

        Args:
            results: Raw vector search results.
            focus_character: If provided, build context centered on this character.

        Returns:
            Enriched results with direct_relations, shared_chapters, etc.
        """
        enriched = []
        for r in results:
            block = r.block
            chars = (getattr(block, "all_person", []) or []) or getattr(block, "characters", []) or []

            primary = focus_character or (chars[0] if chars else "")

            ctx_relations = []
            ctx_chapters = []
            ctx_path = []

            if primary and primary in self.graph:
                ctx_relations = self.get_relations(primary)

                # Shared chapters with top relation
                if ctx_relations and chars:
                    for other in chars:
                        if other != primary and other in self.graph:
                            ctx_chapters = self.shared_chapters(primary, other)
                            ctx_path = self.shortest_path(primary, other)
                            break

            enriched.append(EnrichedResult(
                global_id=block.global_id,
                block_type=block.block_type,
                score=r.score,
                channel=r.channel,
                text=getattr(block, "narrative_text", "") or " ".join(
                    d.content for d in getattr(block, "dialogues", [])
                ),
                characters_in_block=chars,
                direct_relations=ctx_relations,
                shared_chapters=ctx_chapters,
                shortest_path=ctx_path,
            ))

        return enriched

    def to_context_string(self, results: list[SearchResultWithBlock],
                          limit: int = 3) -> str:
        """Build a compact graph-context string for LLM prompt injection.

        Example output:
          ## 角色关系
          - 林晚晴 ←→ 顾清寒 (12次互动, 共处章节: 第一章,第三章,第五章)
          - 林晚晴 ←→ 林震天 (5次互动, 共处章节: 第四章,第六章)
        """
        enriched = self.enrich_results(results[:limit])
        lines = []

        seen = set()
        for er in enriched:
            # 优先 relation 边（事实源语义：类型/极性），再补共现/对话边
            rel_edges = [r for r in er.direct_relations if r.edge_types and "relation" in r.edge_types]
            others = [r for r in er.direct_relations if r not in rel_edges]
            ordered = rel_edges + others
            for rel in ordered[:3]:
                pair = tuple(sorted([er.characters_in_block[0] if er.characters_in_block else "?", rel.character]))
                if pair in seen:
                    continue
                seen.add(pair)
                if rel.relation_type:
                    pol = f"（{rel.polarity}）" if rel.polarity and rel.polarity != "neutral" else ""
                    conf = f"，置信{rel.confidence:.0%}" if rel.confidence else ""
                    lines.append(f"- {pair[0]} ←→ {pair[1]}：{rel.relation_type}{pol}{conf}")
                else:
                    ch_str = f", 共处章节: {','.join(rel.chapters[:3])}" if rel.chapters else ""
                    lines.append(f"- {pair[0]} ←→ {pair[1]} ({rel.interaction_count}次互动{ch_str})")

        if lines:
            return "## 角色关系\n" + "\n".join(lines)
        return ""

    # ── Persist ──────────────────────────────────────────

    def save(self, path: str) -> None:
        """Persist graph to JSON (handles frozenset→list for v2.0 CharacterIdentity aliases)."""
        data = nx.node_link_data(self.graph)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # 显式 UTF-8：Windows 默认 locale 编码（GBK）会让 JSON 文件不可移植
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str) -> CharacterGraph:
        """Load graph from JSON."""
        g = cls()
        g.graph = nx.node_link_graph(
            json.loads(Path(path).read_text(encoding="utf-8"))
        )
        g._node_count = g.graph.number_of_nodes()
        return g
