"""生成 tests/fixtures/epub/sample.epub — 合成但真实结构的 EPUB3 文件。

5 章 + OPF(spine) + EPUB3 nav + EPUB2 ncx，供 epub 集成测试使用。
不依赖任何外部文件（CI/本地/无网络都能跑）。

用法：
    ./venv/Scripts/python.exe scripts/dev/gen_epub_fixture.py
"""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# 复用测试文件的构造工具
from test_epub_convert import _chap_html, _make_epub, _make_nav3, _make_ncx, _make_opf, _LONG_PARA, _LONG_PARA_2

OUT = ROOT / "tests" / "fixtures" / "epub" / "sample.epub"

CHAPTERS = [
    ("第一章 觉醒", _LONG_PARA + _LONG_PARA_2),
    ("第二章 暴风龙", _LONG_PARA_2 + _LONG_PARA),
    ("第三章 迷宫", _LONG_PARA),
    ("第四章 祭典", _LONG_PARA_2),
    ("第五章 契约", _LONG_PARA + _LONG_PARA_2),
]

def build() -> bytes:
    titles = [t for t, _ in CHAPTERS]
    manifest = [(f"c{i}", f"c{i}.xhtml") for i in range(1, 6)]
    spine = [f"c{i}" for i in range(1, 6)]
    entries = {
        "content.opf": _make_opf(manifest, spine),
        "nav.xhtml": _make_nav3(titles),
        "toc.ncx": _make_ncx(titles),
    }
    for i, (title, body) in enumerate(CHAPTERS, 1):
        entries[f"c{i}.xhtml"] = _chap_html(title, body)
    return _make_epub(entries)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    OUT.write_bytes(data)
    # 验证可被转换器解析
    from src.application.novel.ingest import convert_epub
    raw_md, toc = convert_epub(data, OUT.name)
    h1 = len([l for l in raw_md.splitlines() if l.startswith("# ")])
    print(f"写入 {OUT} ({len(data)} bytes)")
    print(f"验证: md={len(raw_md)}字, H1={h1}, TOC={toc}")
    assert h1 == 5 and toc and len(toc) == 5, "fixture 结构异常"


if __name__ == "__main__":
    main()
