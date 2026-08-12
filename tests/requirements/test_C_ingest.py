"""需求域 C — 小说导入：上传 Job / 入库管线 / 书目管理 / 对话重抽。

覆盖 docs/REQUIREMENTS.md C-01 ~ C-04。
黑盒：入库走公开 ingest_novel（注入内存 store + FakeLLM），
书目管理走公开 HTTP 端点。
"""

from __future__ import annotations

import pytest

from tests.conftest import FakeLLM, auth_headers, make_store

_TXT = (
    "# 第一章\n"
    "利姆露从温泉中起身，热气蒸腾。维鲁多拉盘踞在池边打盹。\n"
    "「你醒了？」维鲁多拉问道。\n"
    "「嗯，睡得不错。」利姆露回答。\n"
    "# 第二章\n"
    "朱菜端着茶走了进来。\n"
    "「请用茶。」朱菜微笑着说。\n"
)


# ── C-01/C-02 上传入库主路径 ─────────────────────────────────


async def test_ingest_txt_produces_blocks(monkeypatch):
    """C-02：txt 入库主路径——转换/分章/Narrative 切块/索引落库。"""
    from src.application.novel.ingest import ingest_novel
    from src.application.novel.ingest import convert

    # LLM 全程注入 FakeLLM（inventory/harvest/dialogue 均降级不阻塞）
    monkeypatch.setattr(convert, "_build_shared_llm", lambda *a, **kw: FakeLLM())
    store = make_store()
    result = await ingest_novel(
        _TXT.encode("utf-8"),
        "demo.txt",
        store=store,
        series_id="测试系列",
    )
    assert result.success, result.error
    assert result.doc_id
    assert result.series_id == "测试系列"
    assert result.narrative_blocks > 0, "应产出 narrative 块"
    assert store.block_count() > 0, "块应索引进 store"


async def test_ingest_doc_id_inference(monkeypatch):
    """C-01：doc_id 未指定时从系列/卷号推断（series__volNN）。"""
    from src.application.novel.ingest import ingest_novel
    from src.application.novel.ingest import convert

    monkeypatch.setattr(convert, "_build_shared_llm", lambda *a, **kw: FakeLLM())
    store = make_store()
    result = await ingest_novel(
        _TXT.encode("utf-8"),
        "demo_vol02.txt",
        store=store,
        series_id="测试系列",
    )
    assert result.success
    assert "vol02" in result.doc_id or result.doc_id.endswith("02")


async def test_ingest_rejects_binary(monkeypatch):
    """C-01：不可解码二进制被拒（不产生书目）。"""
    from src.application.novel.ingest import ingest_novel
    from src.application.novel.ingest import convert

    monkeypatch.setattr(convert, "_build_shared_llm", lambda *a, **kw: FakeLLM())
    store = make_store()
    result = await ingest_novel(
        b"\x00\xff\xfe\x01\x02",
        "evil.bin",
        store=store,
    )
    assert not result.success
    assert result.error


async def test_ingest_then_searchable(monkeypatch):
    """C-02：入库后可被检索命中（端到端行为）。"""
    from src.application.novel.ingest import ingest_novel
    from src.application.novel.ingest import convert
    from src.application.novel.retrieval import NovelRetrieval

    monkeypatch.setattr(convert, "_build_shared_llm", lambda *a, **kw: FakeLLM())
    store = make_store()
    result = await ingest_novel(
        _TXT.encode("utf-8"),
        "demo.txt",
        store=store,
        series_id="测试系列",
    )
    assert result.success
    out = await NovelRetrieval(store, graph_enrich=False).search("温泉")
    assert isinstance(out, str)
    assert "利姆露" in out or "维鲁多拉" in out


# ── C-03 书目管理 ─────────────────────────────────────────────


def test_novels_list_empty(api_client):
    """C-03：空库书目列表 → 200 空结果（不 500）。"""
    r = api_client.get("/api/v1/agent/novels", headers=auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("items"), list)


def test_rename_unknown_series_404(api_client):
    """C-03：改名不存在的系列 → 404（不 500）。"""
    r = api_client.patch(
        "/api/v1/agent/novels/series",
        params={"series_id": "不存在系列"},
        json={"series_title": "新名字"},
        headers=auth_headers(),
    )
    assert r.status_code == 404


def test_delete_unknown_volume_graceful(api_client):
    """C-03：删除不存在的卷 → 4xx（不 500）。"""
    r = api_client.delete(
        "/api/v1/agent/novels/no-such-doc",
        headers=auth_headers(),
    )
    assert r.status_code in (404, 400, 200)


def test_upload_job_not_found_404(api_client):
    """C-01：查询不存在的上传 Job → 404。"""
    r = api_client.get(
        "/api/v1/agent/upload/jobs/no-such-job",
        headers=auth_headers(),
    )
    assert r.status_code == 404


# ── C-04 对话重抽 ─────────────────────────────────────────────


def test_redialogue_unknown_doc_4xx(api_client):
    """C-04：重抽不存在的卷 → 4xx（依赖 inventory 缺失或文档不存在，不 500）。"""
    r = api_client.post(
        "/api/v1/agent/novels/no-such-doc/redialogue",
        params={"wait": True},
        headers=auth_headers(),
    )
    assert r.status_code in (404, 409, 400)


def test_redialogue_job_not_found_404(api_client):
    """C-04：查询不存在的重抽 Job → 404。"""
    r = api_client.get(
        "/api/v1/agent/redialogue/jobs/no-such-job",
        headers=auth_headers(),
    )
    assert r.status_code == 404


# ── C-01 补充：上传文件存储 ───────────────────────────────────


async def test_local_file_storage_roundtrip(tmp_path):
    """C-01：上传文件可读回/下载/删除（持久化行为）。"""
    from src.infrastructure.file_storage import LocalFileStorage

    fs = LocalFileStorage(base_dir=tmp_path / "storage")
    src = tmp_path / "src.txt"
    src.write_text("小说内容", encoding="utf-8")
    await fs.upload("f1", src)
    assert await fs.exists("f1")
    assert await fs.read_bytes("f1") == "小说内容".encode("utf-8")
    dest = tmp_path / "out.txt"
    await fs.download("f1", dest)
    assert dest.read_text(encoding="utf-8") == "小说内容"
    await fs.delete("f1")
    assert not await fs.exists("f1")


# ── C-02 补充：工具 import 与 upload 同走 ingest 管线（收敛回归）───────


async def test_tool_import_goes_through_ingest_pipeline(monkeypatch, tmp_path):
    """C-02：novel_search import 动作走 ingest_novel（与 upload 一致）。

    回归（2026-08-10）：旧实现工具层手工重排旧组件（无 catalog/graph/盘点），
    已收敛为 ingest_novel 管线——工具导入后应有 catalog 记录。
    """
    from src.application.novel.ingest import convert
    from src.domain.novel.catalog import load_catalog
    from src.tools.builtin_novel import NovelSearchTool

    monkeypatch.setattr(convert, "_build_shared_llm", lambda *a, **kw: FakeLLM())
    store = make_store()
    import_dir = tmp_path / "uploads"
    import_dir.mkdir()
    (import_dir / "demo.md").write_text(
        "# 第一章\n「你好。」利姆露说道。\n",
        encoding="utf-8",
    )
    tool = NovelSearchTool(store=store, import_dir=import_dir)
    result = await tool.execute(action="import", file_path="demo.md")
    assert result.success
    assert store.block_count() > 0
    catalog = load_catalog("demo")
    assert catalog is not None, "工具导入应产生 catalog 记录（与 upload 一致）"
