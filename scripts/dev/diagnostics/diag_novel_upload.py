"""全链路诊断脚本 — 定位小说上传问题。

逐阶段测试 ingest_novel 的每个环节，捕获异常、计时、报告。
使用 MockEmbedding 隔离 Qwen3 加载耗时，专注流程逻辑。
"""

import asyncio
import io
import sys
import time
import traceback
from pathlib import Path

# 项目根
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 强制不读 .env 里的 LLM（避免 localhost:9527 阻塞）
import os
os.environ.pop("DEEPSEEK_API_KEY", None)


def banner(t: str):
    print(f"\n{'='*70}\n {t}\n{'='*70}")


def step(t: str):
    print(f"\n--- {t} ---")


def ok(msg: str):
    print(f"  [OK]   {msg}")


def fail(msg: str, err: str = ""):
    print(f"  [FAIL] {msg}" + (f"\n         {err}" if err else ""))


def warn(msg: str):
    print(f"  [WARN] {msg}")


# ──────────────────────────────────────────────────────────────
# Stage A: 环境与依赖
# ──────────────────────────────────────────────────────────────
def check_env():
    banner("Stage A: 环境与依赖检查")
    issues = []

    # chardet
    try:
        import chardet
        ok(f"chardet {chardet.__version__} (编码检测可用)")
    except ImportError:
        warn("chardet 未安装 — Preprocessor Stage 0 编码检测降级为逐个尝试 fallback")
        issues.append("chardet 缺失（非致命，有 fallback）")

    # opencc
    try:
        import opencc
        ok("opencc 可用 (繁简转换可选)")
    except ImportError:
        warn("opencc 未安装 — 繁→简转换不可用（默认关闭，影响小）")
        issues.append("opencc 缺失（可选）")

    # lancedb
    try:
        import lancedb
        ok(f"lancedb {lancedb.__version__}")
    except ImportError as e:
        fail("lancedb 未安装", str(e))
        issues.append("lancedb 缺失（致命）")

    # sentence_transformers (Qwen3)
    try:
        import sentence_transformers
        ok("sentence_transformers 可用 (Qwen3 可加载)")
    except ImportError as e:
        fail("sentence_transformers 未安装", str(e))
        issues.append("sentence_transformers 缺失（Qwen3 不可用）")

    # Qwen3 模型文件
    model_dir = ROOT / "models" / "Qwen3-Embedding-0.6B"
    if model_dir.exists():
        safetensors = list(model_dir.glob("*.safetensors"))
        if safetensors:
            ok(f"Qwen3 模型文件存在: {safetensors[0].name} ({safetensors[0].stat().st_size/1e6:.0f}MB)")
        else:
            fail("Qwen3 模型目录存在但无 .safetensors")
            issues.append("Qwen3 模型权重缺失")
    else:
        fail("Qwen3 模型目录不存在")
        issues.append("Qwen3 模型目录缺失")

    # DEEPSEEK_API_KEY
    if os.getenv("DEEPSEEK_API_KEY"):
        ok("DEEPSEEK_API_KEY 已设置 (LLM 功能可用)")
    else:
        warn("DEEPSEEK_API_KEY 未设置 — QA生成/角色清洗/对话LLM fallback 将跳过")
        issues.append("LLM 不可用（非致命，QA/角色清洗跳过）")

    return issues


# ──────────────────────────────────────────────────────────────
# Stage B: Preprocessor 单元测试（编码处理是关键）
# ──────────────────────────────────────────────────────────────
def test_preprocessor_encoding():
    banner("Stage B: Preprocessor 编码处理测试")
    from src.domain.novel import preprocessor as pp

    # B1: UTF-8 中文文本
    step("B1: UTF-8 编码的中文文本")
    utf8_bytes = "# 测试小说\n\n第一章 你好世界\n\n这是测试内容。\n".encode("utf-8")
    try:
        result = pp.run(utf8_bytes)
        if result.text and "你好世界" in result.text:
            ok(f"UTF-8 处理成功: {len(result.text)} chars, "
               f"encoding={result.metrics[0].encoding_detected}, "
               f"chapters={len(result.chapters)}")
        else:
            fail("UTF-8 处理后内容异常", f"text={result.text[:80]!r}")
    except Exception as e:
        fail("UTF-8 处理异常", f"{type(e).__name__}: {e}")

    # B2: GBK 编码的中文文本（关键测试点）
    step("B2: GBK 编码的中文文本（模拟 Windows 记事本保存的 .txt）")
    gbk_text = "# 测试小说\n\n第一章 你好世界\n\n这是GBK编码的测试内容，模拟Windows记事本。\n"
    gbk_bytes = gbk_text.encode("gbk")
    try:
        result = pp.run(gbk_bytes)
        if result.text and "你好世界" in result.text:
            enc = result.metrics[0].encoding_detected
            ok(f"GBK 处理成功: encoding_detected={enc}, chapters={len(result.chapters)}")
            if "fallback" in enc.lower():
                warn("编码检测走了 fallback 路径（chardet 缺失导致），但内容正确")
        else:
            fail("GBK 处理后内容丢失", f"text={result.text[:80]!r}")
    except Exception as e:
        fail("GBK 处理异常", f"{type(e).__name__}: {e}")

    # B3: GB18030 编码
    step("B3: GB18030 编码的中文文本")
    gb18030_bytes = "# 测试\n\n第一章 测试内容\n\n这是GB18030编码。\n".encode("gb18030")
    try:
        result = pp.run(gb18030_bytes)
        if "测试内容" in result.text:
            ok(f"GB18030 处理成功: encoding={result.metrics[0].encoding_detected}")
        else:
            fail("GB18030 内容丢失")
    except Exception as e:
        fail("GB18030 异常", f"{type(e).__name__}: {e}")

    # B4: 含 BOM 的 UTF-8
    step("B4: UTF-8 BOM 文本")
    bom_bytes = b"\xef\xbb\xbf" + "# 测试\n\n第一章 BOM测试\n\n内容。\n".encode("utf-8")
    try:
        result = pp.run(bom_bytes)
        if "BOM测试" in result.text and "\ufeff" not in result.text:
            ok("UTF-8 BOM 处理成功（BOM 已移除）")
        else:
            warn(f"BOM 文本: BOM残留={'\\ufeff' in result.text}")
    except Exception as e:
        fail("BOM 异常", f"{type(e).__name__}: {e}")


# ──────────────────────────────────────────────────────────────
# Stage C: ingest_novel Phase 1 — _convert_to_md 编码处理
# ──────────────────────────────────────────────────────────────
def test_convert_to_md_encoding():
    banner("Stage C: ingest Phase 1 (_convert_to_md) 编码处理")
    from src.application.novel.ingest import convert_to_md, SUPPORTED_MIMES

    # C1: UTF-8 txt
    step("C1: UTF-8 .txt → _convert_to_md")
    utf8_bytes = "第一章 测试\n\n这是UTF-8内容。\n".encode("utf-8")
    try:
        md = convert_to_md(utf8_bytes, "test.txt", "text/plain")[0]
        if "UTF-8内容" in md:
            ok("UTF-8 txt 转换成功")
        else:
            fail("UTF-8 txt 内容异常", repr(md[:80]))
    except Exception as e:
        fail("UTF-8 txt 转换异常", f"{type(e).__name__}: {e}")

    # C2: GBK txt —— 这是关键！
    step("C2: GBK .txt → _convert_to_md （关键：模拟 Windows 记事本）")
    gbk_bytes = "第一章 测试\n\n这是GBK编码内容。\n".encode("gbk")
    try:
        md = convert_to_md(gbk_bytes, "test.txt", "text/plain")[0]
        if "GBK编码内容" in md:
            ok("GBK txt 转换成功")
        else:
            fail("GBK txt 内容异常（乱码）", repr(md[:80]))
    except UnicodeDecodeError as e:
        fail("GBK txt 转换抛 UnicodeDecodeError",
             f"{e} — 这就是 .txt 上传失败的根因！")
    except Exception as e:
        fail("GBK txt 转换异常", f"{type(e).__name__}: {e}")

    # C3: GB18030 txt
    step("C3: GB18030 .txt → _convert_to_md")
    gb_bytes = "第一章 测试\n\nGB18030内容。\n".encode("gb18030")
    try:
        md = convert_to_md(gb_bytes, "test.txt", "text/plain")[0]
        if "GB18030内容" in md:
            ok("GB18030 txt 转换成功")
        else:
            fail("GB18030 内容异常", repr(md[:80]))
    except UnicodeDecodeError as e:
        fail("GB18030 txt 抛 UnicodeDecodeError", str(e))


# ──────────────────────────────────────────────────────────────
# Stage D: 端到端 ingest_novel（MockEmbedding，隔离 Qwen3）
# ──────────────────────────────────────────────────────────────
async def test_ingest_e2e():
    banner("Stage D: 端到端 ingest_novel（MockEmbedding 隔离 Qwen3）")
    from src.application.novel.ingest import ingest_novel
    from src.infrastructure.embedding import MockEmbeddingProvider
    from src.infrastructure.novel_store import NovelVectorStore

    # 用临时 lance 路径，不污染 data/novel_lance
    tmp_lance = str(ROOT / "data" / "_diag_lance")
    import shutil
    shutil.rmtree(tmp_lance, ignore_errors=True)

    store = NovelVectorStore(
        embedding=MockEmbeddingProvider(dimensions=1024),
        backend="lancedb",
        lance_path=tmp_lance,
        dimensions=1024,
    )

    # 读取真实测试小说
    test_novel_path = ROOT / "data" / "测试小说.md"
    test_bytes = test_novel_path.read_bytes()

    # D1: UTF-8 .md（基准）
    step("D1: UTF-8 .md 端到端（基准）")
    t0 = time.time()
    try:
        result = await ingest_novel(
            test_bytes, "测试小说.md", store=store, generate_qa=False,
        )
        dt = time.time() - t0
        if result.success:
            ok(f"成功 {dt:.2f}s: ch={result.total_chapters} "
               f"narr={result.narrative_blocks} dial={result.dialogue_blocks} "
               f"char={result.character_blocks} chars={result.characters[:5]}")
        else:
            fail(f"失败 {dt:.2f}s", result.error)
    except Exception as e:
        fail("异常", f"{type(e).__name__}: {e}\n{traceback.format_exc()[:500]}")

    # D2: GBK .txt（关键验证）
    step("D2: GBK .txt 端到端（验证编码修复前后的行为）")
    gbk_bytes = test_bytes.decode("utf-8").encode("gbk")  # 把测试小说转 GBK
    t0 = time.time()
    try:
        result = await ingest_novel(
            gbk_bytes, "测试小说gbk.txt", store=store, generate_qa=False,
        )
        dt = time.time() - t0
        if result.success:
            ok(f"GBK txt 成功 {dt:.2f}s: narr={result.narrative_blocks}")
        else:
            fail(f"GBK txt 失败 {dt:.2f}s", result.error)
    except Exception as e:
        fail("GBK txt 异常", f"{type(e).__name__}: {e}")

    # D3: 空文件
    step("D3: 空文件")
    try:
        result = await ingest_novel(b"", "empty.txt", store=store, generate_qa=False)
        if not result.success:
            ok(f"空文件正确拒绝: {result.error}")
        else:
            fail("空文件不应成功")
    except Exception as e:
        fail("空文件异常", f"{type(e).__name__}: {e}")

    # D4: 二进制垃圾
    step("D4: 二进制垃圾 .txt")
    garbage = bytes(range(256)) * 10
    try:
        result = await ingest_novel(garbage, "garbage.txt", store=store, generate_qa=False)
        if not result.success:
            ok(f"垃圾文件正确拒绝: {result.error[:60]}")
        else:
            warn(f"垃圾文件被处理了（可能产生无效blocks）: narr={result.narrative_blocks}")
    except Exception as e:
        ok(f"垃圾文件异常拒绝: {type(e).__name__}")

    # D5: 不支持的格式 .pdf
    step("D5: 不支持的 .pdf 格式")
    try:
        result = await ingest_novel(b"%PDF-1.4 fake", "test.pdf", store=store, generate_qa=False)
        if not result.success:
            ok(f"PDF 正确拒绝: {result.error[:60]}")
        else:
            fail("PDF 不应被当作文本处理")
    except Exception as e:
        ok(f"PDF 异常拒绝: {type(e).__name__}")

    # 清理临时 lance
    shutil.rmtree(tmp_lance, ignore_errors=True)


# ──────────────────────────────────────────────────────────────
# Stage E: Qwen3 模型加载（真实 embedding）
# ──────────────────────────────────────────────────────────────
async def test_qwen3_load():
    banner("Stage E: Qwen3 模型加载验证")
    try:
        from src.infrastructure.embedding import Qwen3EmbeddingProvider
    except ImportError as e:
        fail("无法导入 Qwen3EmbeddingProvider", str(e))
        return

    step("E1: 加载 Qwen3-Embedding-0.6B")
    t0 = time.time()
    try:
        provider = Qwen3EmbeddingProvider(
            model_path=str(ROOT / "models" / "Qwen3-Embedding-0.6B"),
            device="cpu",
        )
        dt = time.time() - t0
        ok(f"模型加载成功 {dt:.1f}s, dim={provider._dim}")

        step("E2: 单次嵌入测试")
        t0 = time.time()
        result = await provider.embed_texts(["你好世界，这是一个测试"])
        dt = time.time() - t0
        if result.embeddings and len(result.embeddings[0]) == 1024:
            ok(f"嵌入成功 {dt:.2f}s, dim={result.dimensions}")
        else:
            fail(f"嵌入维度异常: {result.dimensions}")
    except Exception as e:
        fail("Qwen3 加载失败", f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}")


# ──────────────────────────────────────────────────────────────
# Stage F: 真实端到端（Qwen3 + LanceDB，可选 LLM）
# ──────────────────────────────────────────────────────────────
async def test_real_e2e():
    banner("Stage F: 真实端到端 (Qwen3 + LanceDB)")
    from src.application.novel.ingest import ingest_novel
    from src.application.novel.factory import create_novel_store

    tmp_lance = str(ROOT / "data" / "_diag_real_lance")
    import shutil
    shutil.rmtree(tmp_lance, ignore_errors=True)

    step("F1: 创建 Qwen3 store")
    try:
        store = create_novel_store(backend="lancedb", lance_path=tmp_lance)
        ok("Qwen3 store 创建成功")
    except Exception as e:
        fail("store 创建失败", f"{type(e).__name__}: {e}")
        return

    test_novel_path = ROOT / "data" / "测试小说.md"
    test_bytes = test_novel_path.read_bytes()

    step("F2: 真实 ingest（UTF-8 .md）")
    t0 = time.time()
    try:
        result = await ingest_novel(
            test_bytes, "测试小说.md", store=store, generate_qa=False,
        )
        dt = time.time() - t0
        if result.success:
            ok(f"成功 {dt:.1f}s: ch={result.total_chapters} "
               f"narr={result.narrative_blocks} dial={result.dialogue_blocks} "
               f"char={result.character_blocks}")
            ok(f"角色: {result.characters}")
        else:
            fail(f"失败 {dt:.1f}s", result.error)
    except Exception as e:
        fail("异常", f"{type(e).__name__}: {e}\n{traceback.format_exc()[:500]}")

    shutil.rmtree(tmp_lance, ignore_errors=True)


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────
async def main():
    print(f"诊断开始 — 项目根: {ROOT}")
    print(f"Python: {sys.version.split()[0]}")

    env_issues = check_env()
    test_preprocessor_encoding()
    test_convert_to_md_encoding()
    await test_ingest_e2e()
    await test_qwen3_load()
    await test_real_e2e()

    banner("诊断总结")
    if env_issues:
        print("环境问题:")
        for i in env_issues:
            print(f"  - {i}")
    print("\n（详见上方各 Stage 的 [FAIL]/[WARN] 行）")


if __name__ == "__main__":
    asyncio.run(main())
