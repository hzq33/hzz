"""NovelSearchTool handlers mixin — search / impersonate / import / list actions.

Extracted from the former monolithic ``builtin_novel.py``; logic unchanged.
Mixin methods share instance state (``self._store`` / ``self.llm`` / ``self.name``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.application.novel.query_parse import (
    is_toc_intent,
    list_ordered_chapter_titles,
    parse_section_hint,
    parse_volume_hint,
    resolve_chapter_by_ordinal,
    resolve_doc_id_for_volume,
)
from src.tools.base import ToolResult


def _observe_search_relevance(hits) -> None:
    """确定性回收：检索命中数 → 相关性指标（不依赖 LLM verdict，扮演场景兜底）。"""
    try:
        from src.shared.metrics import observe_retrieval_relevance

        n = len(list(hits or []))
        if n > 0:
            observe_retrieval_relevance(verdict="relevant")
        else:
            observe_retrieval_relevance(verdict="irrelevant")
    except Exception:  # noqa: BLE001 - 回收失败不影响检索
        pass

logger = logging.getLogger("agent")


class NovelSearchHandlersMixin:
    """Action handlers for NovelSearchTool."""


    def _resolve_series_from_query(self, query: str) -> str | None:
        """从 query 解析系列名：书名号《》优先，其次系列名包含匹配。

        例："在《败犬女主太多了》里" / "Re：从零开始的异世界生活 中维鲁多拉…"
        命中后返回 series_id，检索严格限定该系列（防跨作品污染）。
        """
        try:
            from src.application.novel.services.catalog_service import list_catalogs
            catalogs = list_catalogs()
        except Exception:
            return None
        if not catalogs:
            return None
        import re

        # 书名号优先（用户显式引用作品名）
        m = re.search(r"[《「]([^》」]+)[》」]", query or "")
        if m:
            title = m.group(1).strip()
            for c in catalogs:
                sid = c.series_id or ""
                st = c.series_title or sid
                if title in sid or sid in title or title in st or st in title:
                    return sid
        # 系列名包含匹配（长名优先，避免短名误伤）
        for c in sorted(
            catalogs,
            key=lambda c: len(c.series_title or c.series_id or ""),
            reverse=True,
        ):
            name = c.series_title or c.series_id or ""
            if name and name in (query or ""):
                return c.series_id
        return None

    def _resolve_search_scope(
        self, query: str, doc_id: str | None, series: str | None = None
    ) -> tuple[str | None, dict[str, Any], str | None, list[str]]:
        """Infer doc_id / series / chapter filters from query; return notes for the LLM.

        返回 (doc_id, filters, channel_hint, notes)。
        filters 可能含 ``series``（系列级隔离）与 ``chapter`` 等。
        """
        notes: list[str] = []
        filters: dict[str, Any] = {}
        resolved = (doc_id or "").strip() or None

        # 系列解析：显式参数优先，其次 query 书名号/系列名
        resolved_series = (series or "").strip() or None
        if not resolved_series:
            resolved_series = self._resolve_series_from_query(query)
            if resolved_series:
                notes.append(f"已从查询解析系列 → series={resolved_series}")
        if resolved_series:
            filters["series"] = resolved_series

        volume_hint = parse_volume_hint(query)
        if volume_hint and not resolved:
            try:
                ids = list(self._store.doc_ids() or [])
            except Exception:
                ids = []
            resolved = resolve_doc_id_for_volume(
                ids, volume_hint, series_hint=resolved_series or ""
            )
            if resolved:
                notes.append(f"已从查询解析卷号 → doc_id={resolved}")
            else:
                notes.append(
                    f"查询含第{volume_hint}卷，但知识库中未找到对应 __vol{volume_hint:02d}"
                )

        section = parse_section_hint(query)
        channel_hint: str | None = None
        if section:
            unit, n = section
            channel_hint = "narrative"
            if not resolved:
                notes.append(
                    f"查询含第{n}{unit}，但未锁定卷；请提供 doc_id 或在 query 写「第N卷」"
                )
            else:
                title, titles = resolve_chapter_by_ordinal(resolved, n)
                if title:
                    filters["chapter"] = title
                    # Prefer semantic query on the real title, not「第N节」
                    notes.append(
                        f"第{n}{unit}（卷内第{n}章）→「{title}」"
                    )
                else:
                    preview = "、".join(
                        f"{i + 1}.{t}" for i, t in enumerate(titles[:12])
                    )
                    if titles:
                        notes.append(
                            f"本卷共 {len(titles)} 章，没有第{n}{unit}。"
                            f"目录：{preview}"
                            + ("…" if len(titles) > 12 else "")
                        )
                    else:
                        # Fallback: title substring keys (rare books that literally use 第N节)
                        from src.application.novel.query_parse import chapter_match_keys

                        filters["chapter_contains_any"] = chapter_match_keys(unit, n)
                        notes.append(
                            f"未找到卷目录，回退标题包含匹配：{filters['chapter_contains_any'][:4]}"
                        )

        return resolved, filters, channel_hint, notes

    async def _handle_search(self, kwargs: dict) -> ToolResult:
        query = kwargs.get("query", "")
        if not query:
            return ToolResult.fail("query is required for 'search' action")

        top_k = min(kwargs.get("top_k", 5), 10)
        channel = kwargs.get("channel")  # "narrative" | "dialogue" | "qa" | None
        doc_id, filters, channel_hint, notes = self._resolve_search_scope(
            query, kwargs.get("doc_id"), series=kwargs.get("series")
        )
        # 「第3卷目录」→ catalog，不做向量检索
        if is_toc_intent(query):
            return await self._handle_list_chapters(
                {"doc_id": doc_id, "query": query}
            )
        if channel_hint and not channel:
            channel = channel_hint
        # Use resolved chapter title in the embedding query for better recall
        chapter_title = filters.get("chapter")
        if isinstance(chapter_title, str) and chapter_title.strip():
            query = f"{chapter_title.strip()} {query}"

        # ── Explicit channel: bypass IntentRouter, search that channel directly ──
        if channel:
            from src.domain.novel.models import (
                BLOCK_CHARACTER,
                BLOCK_DIALOGUE,
                BLOCK_NARRATIVE,
                BLOCK_QA,
            )
            valid = {BLOCK_NARRATIVE, BLOCK_DIALOGUE, BLOCK_QA, BLOCK_CHARACTER}
            if channel not in valid:
                return ToolResult.fail(
                    f"Invalid channel '{channel}'. Valid: {', '.join(sorted(valid))}"
                )
            hits = await self._store.search(
                query,
                channel=channel,
                doc_id=doc_id,
                top_k=top_k,
                filters=filters or None,
            )
            _observe_search_relevance(hits)
            context = self._format_channel_results(query, channel, hits)
            if notes:
                context = "\n".join(notes) + "\n" + context
            if not hits:
                context += (
                    "\n提示：小说内容仅在向量库中，请勿用 file_operation 读本地路径。"
                    "可先 action=list 确认 doc_id，或放宽 query / 去掉章节过滤重试。"
                )
            return ToolResult.ok(context)

        # ── Auto-route via IntentRouter（top_k 作为参数传递，不再修改共享
        #    NovelRetrieval 实例属性 —— 并发请求会互相覆盖 top_k 的竞态）──
        if filters:
            intent = await self._router.aclassify(query)
            intent_filters = dict(intent.filters or {})
            intent_filters.update(filters)
            if len(intent.channel_weights) == 1:
                ch = list(intent.channel_weights.keys())[0]
                hits = await self._store.search(
                    query,
                    channel=ch,
                    doc_id=doc_id,
                    top_k=top_k,
                    filters=intent_filters or None,
                )
            else:
                hits = await self._store.search_multi(
                    query,
                    intent.channel_weights,
                    doc_id=doc_id,
                    top_k=top_k,
                    filters=intent_filters or None,
                )
            _observe_search_relevance(hits)
            context = self._format_channel_results(
                query, intent.primary_channel, hits
            )
            header = f"路由: {intent.primary_channel} (confidence={intent.confidence:.2f})"
            context = header + "\n" + context
        else:
            context = await self._retrieval.search(
                query,
                doc_id=doc_id,
                series_id=filters.get("series"),
                top_k=top_k,
            )
        if notes:
            context = "\n".join(notes) + "\n" + context
        if "未找到" in context or "未找到相关知识" in context:
            context += (
                "\n提示：请勿用 file_operation 访问 data/novels；"
                "用 novel_search action=list 查看可用 doc_id。"
            )
        return ToolResult.ok(context)

    def _format_channel_results(self, query: str, channel: str, hits: list) -> str:
        """Format search hits for a specific channel (bypassing IntentRouter)."""
        from src.application.novel.retrieval import (
            _CHARACTER_PERSONALITY_CHARS,
            _CHARACTER_SPEECH_CHARS,
            _DIALOGUE_SCENE_CHARS,
            _NARRATIVE_CHARS,
            _QA_ANSWER_CHARS,
            _RELATED_NARRATIVE_CHARS,
            _clip,
        )
        from src.domain.novel.models import (
            BLOCK_CHARACTER,
            BLOCK_DIALOGUE,
            BLOCK_NARRATIVE,
            BLOCK_QA,
        )

        if not hits:
            return (
                f"查询「{query}」在通道 [{channel}] 中未找到相关知识。\n"
                f"可尝试切换到其他通道：narrative（原文）、dialogue（对话）、"
                f"qa（问答）、character（关系/事件）。"
            )

        lines = [f"查询: {query}", f"通道: {channel}（手动指定）", "─" * 40]

        for i, hit in enumerate(hits, 1):
            block = hit.block
            lines.append(f"\n结果 {i} [相关性:{hit.score:.2f}]")

            if block.block_type == BLOCK_NARRATIVE:
                lines.append(f"  来源: {block.source}")
                lines.append(f"  原文: {_clip(block.narrative_text, _NARRATIVE_CHARS)}")
            elif block.block_type == BLOCK_DIALOGUE:
                lines.append(f"  场景: {block.scene}")
                lines.append(f"  角色: {', '.join(block.characters)}")
                lines.append(f"  风格: {', '.join(block.style_tags)}")
                lines.append(
                    f"  场景描写: {_clip(block.scene_detail, _DIALOGUE_SCENE_CHARS)}"
                )
                for t in block.dialogues[:5]:
                    lines.append(f"    [{t.speaker}] {t.content}")
            elif block.block_type == BLOCK_QA:
                lines.append(f"  Q: {block.question}")
                lines.append(f"  A: {_clip(block.answer, _QA_ANSWER_CHARS)}")
                lines.append(f"  标签: {', '.join(block.qa_tags)}")
            elif block.block_type == BLOCK_CHARACTER:
                from src.application.novel.character_channel_index import (
                    format_relation_event_clue,
                    is_relation_event_block,
                )

                if is_relation_event_block(block):
                    lines.append("  类型: 关系/事件索引")
                    lines.append(f"  线索: {format_relation_event_clue(block, clip=400)}")
                    for ref_id in (block.ref_chunk_ids or [])[:3]:
                        if not ref_id:
                            continue
                        try:
                            narr = self._store.get_block(ref_id)
                        except Exception:
                            narr = None
                        if narr is None or not getattr(narr, "narrative_text", None):
                            continue
                        src = narr.source or narr.chapter_title or ref_id
                        lines.append(
                            f"  证据原文[{src}]: {_clip(narr.narrative_text, _RELATED_NARRATIVE_CHARS)}"
                        )
                else:
                    name = getattr(block, "character_name", "") or (
                        block.characters[0] if block.characters else ""
                    )
                    lines.append(f"  角色: {name}")
                    if getattr(block, "personality", ""):
                        lines.append(
                            f"  性格: {_clip(block.personality, _CHARACTER_PERSONALITY_CHARS)}"
                        )
                    if getattr(block, "speech_style", ""):
                        lines.append(
                            f"  说话风格: {_clip(block.speech_style, _CHARACTER_SPEECH_CHARS)}"
                        )

        return "\n".join(lines)

    async def _handle_impersonate(self, kwargs: dict) -> ToolResult:
        query = kwargs.get("query", "")
        character = kwargs.get("character", "")

        # Try to extract character from query if not explicitly provided
        if not character and query:
            known = self._router._KNOWN_CHARACTERS
            for name in known:
                if name in query:
                    character = name
                    break

        if not character:
            return ToolResult.fail(
                "character is required for 'impersonate' action. "
                f"Known characters: {', '.join(self._router._KNOWN_CHARACTERS)}"
            )

        style = kwargs.get("style", "")
        doc_id = kwargs.get("doc_id")

        result = await self._impersonator.impersonate(
            character=character,
            user_request=query,
            style=style,
            doc_id=doc_id,
        )

        output_parts = [
            result["generated_text"],
            "",
            "---",
            f"风格标签: {result['style']}",
            f"句式分析: {result['speech_patterns'].get('style_summary', 'N/A')}",
            f"平均句长: {result['speech_patterns'].get('avg_sentence_length', 'N/A')}字",
            f"引用来源: {', '.join(result.get('sources', []))}",
        ]
        return ToolResult.ok("\n".join(output_parts))

    async def _handle_import(self, kwargs: dict) -> ToolResult:
        """Import a novel MD file into the store.

        统一走 ingest_novel 管线（转换→分章→narrative/dialogue/qa/character
        块→索引→catalog→图谱），与 upload 入口行为一致；不再手工重排旧组件。
        """
        file_path = kwargs.get("query") or kwargs.get("file_path", "")
        doc_id = kwargs.get("doc_id", "")

        if not file_path:
            return ToolResult.fail("file path is required for 'import' action")

        try:
            path = self._resolve_import_path(file_path)
        except ValueError as e:
            return ToolResult.fail(str(e))

        file_bytes = path.read_bytes()

        from src.application.novel.ingest import ingest_novel

        result = await ingest_novel(
            file_bytes,
            path.name,
            store=self._store,
            doc_id=doc_id or None,
        )
        if not result.success:
            return ToolResult.fail(f"导入失败: {result.error or 'unknown error'}")

        stats = await self._store.stats()
        return ToolResult.ok(
            f"导入完成: 《{result.doc_id}》\n"
            f"  叙事块: {result.narrative_blocks}\n"
            f"  对话块: {result.dialogue_blocks}\n"
            f"  QA对: {result.qa_blocks}\n"
            f"  角色块: {result.character_blocks}\n"
            f"  总计索引: {stats['total_blocks']} 条\n"
            f"  覆盖通道: {', '.join(f'{k}={v}' for k, v in stats['channels'].items())}"
        )

    def _resolve_import_path(self, file_path: str) -> Path:
        """Resolve a novel import path under the configured upload directory."""
        base = self._import_dir
        # 拒绝绝对路径直接访问（统一走 base 内相对路径）
        raw = Path(file_path)
        if raw.is_absolute():
            raise ValueError("Import path must be relative to the upload directory.")
        path = (base / raw).resolve()
        try:
            path.relative_to(base)
        except ValueError as exc:
            raise ValueError(
                f"Import path '{file_path}' is outside the allowed upload directory."
            ) from exc
        # 拒绝符号链接逃逸（resolve 后可能指向 base 外）
        try:
            if path.is_symlink():
                target = path.resolve()
                target.relative_to(base)
        except ValueError as exc:
            raise ValueError(
                f"Import path '{file_path}' resolves outside the upload directory."
            ) from exc

        if not path.exists():
            raise ValueError(f"File not found: {file_path}")
        if not path.is_file():
            raise ValueError(f"Import path is not a file: {file_path}")
        if path.suffix.lower() not in self._ALLOWED_IMPORT_SUFFIXES:
            allowed = ", ".join(sorted(self._ALLOWED_IMPORT_SUFFIXES))
            raise ValueError(f"Unsupported import file type: {path.suffix}. Allowed: {allowed}")
        if path.stat().st_size > self._MAX_IMPORT_BYTES:
            raise ValueError(
                f"Import file is too large: {path.stat().st_size} bytes "
                f"(max {self._MAX_IMPORT_BYTES} bytes)"
            )
        return path

    def _resolve_doc_id(self, query: str, doc_id: str | None) -> tuple[str | None, list[str]]:
        """Resolve volume doc_id from explicit arg or「第N卷」hint."""
        notes: list[str] = []
        resolved = (doc_id or "").strip() or None
        volume_hint = parse_volume_hint(query or "")
        if volume_hint and not resolved:
            try:
                ids = list(self._store.doc_ids() or [])
            except Exception:
                ids = []
            resolved = resolve_doc_id_for_volume(ids, volume_hint)
            if resolved:
                notes.append(f"已从查询解析卷号 → doc_id={resolved}")
            else:
                notes.append(
                    f"查询含第{volume_hint}卷，但知识库中未找到对应 __vol{volume_hint:02d}"
                )
        return resolved, notes

    async def _handle_list_chapters(self, kwargs: dict) -> ToolResult:
        """Return ordered chapter titles from Novel Catalog (not vector search)."""
        query = kwargs.get("query") or ""
        resolved, notes = self._resolve_doc_id(query, kwargs.get("doc_id"))
        if not resolved:
            return ToolResult.fail(
                "list_chapters 需要 doc_id 或在 query 写「第N卷」。"
                "可先 action=list 查看可用卷。"
            )

        from src.application.novel.query_parse import series_id_from_doc_id
        from src.application.novel.services.catalog_service import load_catalog

        sid = series_id_from_doc_id(resolved)
        catalog = load_catalog(sid) if sid else None
        volume = catalog.find(resolved) if catalog else None
        if volume is None and catalog:
            import re

            m = re.search(r"__vol0*(\d+)$", resolved, flags=re.IGNORECASE)
            if m:
                vol_no = int(m.group(1))
                volume = next(
                    (v for v in catalog.volumes if v.volume_no == vol_no),
                    None,
                )

        titles = list_ordered_chapter_titles(resolved)
        if not titles and volume:
            titles = [
                c.title.strip()
                for c in sorted(volume.chapters or [], key=lambda c: (c.order, c.title))
                if (c.title or "").strip()
            ]

        lines: list[str] = []
        if notes:
            lines.extend(notes)
        vol_label = ""
        if volume and volume.volume_no is not None:
            vol_label = f"第{volume.volume_no}卷"
        elif volume and volume.volume_title:
            vol_label = volume.volume_title
        lines.append(f"## 章节目录 — {vol_label or resolved}")
        lines.append(f"doc_id={resolved}")
        if volume:
            bc = volume.block_counts or {}
            bc_str = ", ".join(f"{k}={v}" for k, v in sorted(bc.items())) or "无"
            lines.append(
                f"章节数={len(titles)}；block_counts=[{bc_str}]；"
                f"needs_reindex={volume.needs_reindex}"
                + (f"（{volume.reindex_reason}）" if volume.reindex_reason else "")
            )
        lines.append("")
        if not titles:
            lines.append(
                "本卷 catalog 无章节元数据。可尝试重新导入，或用 search 按章名检索。"
            )
            return ToolResult.ok("\n".join(lines))

        for i, title in enumerate(titles, start=1):
            char_n = ""
            if volume:
                ch = next(
                    (
                        c
                        for c in (volume.chapters or [])
                        if (c.title or "").strip() == title
                    ),
                    None,
                )
                if ch and ch.char_count:
                    char_n = f" ({ch.char_count}字)"
            lines.append(f"{i}. {title}{char_n}")
        lines.append("")
        lines.append(
            "提示：查某章正文请 search + query「第N节」或真实章名，"
            "channel=narrative；勿用 file_operation。"
        )
        return ToolResult.ok("\n".join(lines))

    async def _handle_global(self, kwargs: dict) -> ToolResult:
        """GraphRAG 全局问答：跨章节主线/整体关系（需已构建）。"""
        query = kwargs.get("query") or ""
        series_id = (kwargs.get("series") or "").strip()
        if not series_id:
            # 从 query 尝试解析系列名（书名号/系列名包含匹配）
            series_id = self._resolve_series_from_query(query) or ""
        if not series_id:
            return ToolResult.fail(
                "global 需要 series（系列名，如「败犬女主太多了」）。"
                "可先 action=list 查看可用系列。"
            )
        try:
            from src.application.novel.services.graph_rag_service import (
                format_global_context,
                is_stale,
                load_graph_rag,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"GraphRAG service unavailable: {exc}")
        payload = load_graph_rag(series_id)
        if payload is None:
            return ToolResult.ok(
                f"系列「{series_id}」尚未构建 GraphRAG。"
                "可运行 story_analysis build（会联动构建），"
                "或调用 API POST /api/v1/agent/rag-global/build。"
            )
        lines = [
            f"## GraphRAG 全局层 — {series_id}",
            f"stale={is_stale(series_id)} updated_at={payload.get('updated_at', '')}",
            "",
            f"全局概览: {payload.get('global_overview', '')}",
        ]
        communities = payload.get("communities") or []
        lines.append(f"社区数: {len(communities)}")
        if query.strip():
            try:
                ctx = format_global_context(series_id, query.strip())
                lines.append("")
                lines.append(f"## 全局问答上下文（问题: {query.strip()}）")
                lines.append(ctx or "（无匹配社区摘要）")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"全局问答失败: {exc}")
        return ToolResult.ok("\n".join(lines))

    async def _handle_list(self, kwargs: dict | None = None) -> ToolResult:
        """List series/volumes from Novel Catalog with block_counts / reindex flags."""
        kwargs = kwargs or {}
        from src.application.novel.services.catalog_service import ensure_series_title, list_catalogs

        catalogs = [ensure_series_title(c) for c in list_catalogs()]
        store_ids: set[str] = set()
        try:
            store_ids = set(self._store.doc_ids() or [])
        except Exception:
            store_ids = set()

        if not catalogs and not store_ids:
            return ToolResult.ok("知识库为空。请先导入小说（使用 import 或工作台上传）。")

        lines = ["## 小说书目（Catalog）", ""]
        listed_ids: set[str] = set()
        for catalog in catalogs:
            title = catalog.series_title or catalog.series_id
            lines.append(f"### 《{title}》")
            lines.append(f"series_id={catalog.series_id}")
            if not catalog.volumes:
                lines.append("  （无卷）")
                lines.append("")
                continue
            for vol in catalog.volumes:
                listed_ids.add(vol.doc_id)
                vol_no = (
                    f"第{vol.volume_no}卷"
                    if vol.volume_no is not None
                    else (vol.volume_title or "未编号卷")
                )
                n_ch = len(vol.chapters or [])
                bc = vol.block_counts or {}
                bc_str = ", ".join(f"{k}={v}" for k, v in sorted(bc.items())) or "—"
                in_store = "已索引" if vol.doc_id in store_ids else "仅书目"
                reindex = ""
                if vol.needs_reindex:
                    reindex = "；needs_reindex=true"
                    if vol.reindex_reason:
                        reindex += f"（{vol.reindex_reason}）"
                lines.append(
                    f"- {vol_no} | doc_id={vol.doc_id} | 章节={n_ch} | "
                    f"blocks=[{bc_str}] | {in_store}{reindex}"
                )
            lines.append("")

        orphan = sorted(store_ids - listed_ids)
        if orphan:
            lines.append("## 仅在向量库中的卷（无 catalog）")
            for did in orphan:
                lines.append(f"- doc_id={did}")
            lines.append("")

        try:
            stats = await self._store.stats()
            channels = stats.get("channels", {})
            lines.append("## 通道统计（全库）")
            for ch_key, label in (
                ("narrative", "叙事"),
                ("dialogue", "对话"),
                ("qa", "QA"),
                ("character", "角色"),
            ):
                lines.append(f"  - {label}({ch_key}): {channels.get(ch_key, 0)}")
            lines.append(f"总计知识块: {stats.get('total_blocks', 0)}")
        except Exception:
            pass

        lines.append("")
        lines.append(
            "提示: 列某卷章节用 action=list_chapters + doc_id；"
            "查正文用 action=search。"
        )
        return ToolResult.ok("\n".join(lines))
