# 开发用手动脚本

从 `tests/` 迁出的一次性诊断 / 验证 / 分析脚本。**不是** pytest 门禁套件（依赖本机数据与模型，CI 不跑）。

## 分类

| 子目录 | 用途 | 脚本 |
|--------|------|------|
| `diagnostics/` | 数据探查与格式诊断（小说原文、epub 结构、符号问题） | `diag_*.py`、`inspect_*.py`、`dump_*.py`、`locate_hr_in_epub.py`、`show_symbol_context.py`、`probe_real_corpus.py` |
| `verify/` | 行为验证 / 冒烟（本地 LLM、hybrid pipeline、epub 转换、thinking 注入） | `verify_*.py`、`smoke_character_graph.py`、`preview_fixture.py` |
| `analysis/` | 评测对比 / 采样分析（seed 策略对比、风格采样、真实检索） | `compare_*.py`、`eval_*.py`、`analysis_extract_vol07.py`、`sample_real_corpus.py` |
| `refactor/` | 模块拆分脚本（历史工具，源文件仍在、可偶尔重跑） | `split_*.py`，见其 `README.md` |

## 用法

```bash
# 示例（需本机数据与模型）
python scripts/dev/verify/verify_channels.py
python scripts/dev/diagnostics/diag_novel_upload.py
```

## 正式测试（离线、无 LLM/网络）

```bash
# Hermes 环境（会注入自己的 venv 到 PYTHONPATH，需覆写）：
PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe -m pytest tests -q
```

## 静态检查

```bash
./venv/Scripts/ruff.exe check src tests --select E9,F63,F7,F82   # CI gate
./venv/Scripts/ruff.exe check src tests                           # 全量
```
