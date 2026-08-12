# Mixin 再生成脚本

按固定行号从仍存在的源文件切出 mixin 模块，仅供偶尔重跑拆分时使用。日常开发不依赖本目录。

| 脚本 | 源文件 | 生成目标 |
|------|--------|----------|
| `split_impersonation.py` | `src/core/impersonation_agent.py` | `src/core/_imp_*.py` |
| `split_llm.py` | `src/shared/llm.py` | `src/shared/_llm_resilience.py` |
| `split_builtin_novel.py` | `src/tools/builtin_novel.py` | `src/tools/_novel_search_handlers.py` |

在仓库根目录执行，例如：

```bash
python scripts/dev/refactor/split_impersonation.py
```

行号与源文件不同步时脚本会写出错误切分结果；改完源文件后应改用 AST/函数名提取，或手工维护 mixin。
