# RAG 检索 Trace 在线评估

> 日期：2026-08-08 | 状态：Current
> 定位：真实对话检索质量的**事后复盘**评估（区别于 seed 离线评估）
> 相关：[NOVEL_RAG_DESIGN.md](./NOVEL_RAG_DESIGN.md) · [EVAL_LLM_JUDGE.md](./EVAL_LLM_JUDGE.md) · [ONLINE_EVAL_DESIGN.md](./ONLINE_EVAL_DESIGN.md)（已 superseded，本文件为落地形态）

## 1. 是什么

在前端**真实对话**中自动记录每次 novel 检索的关键信息（query / 路由 / scope / 命中原文预览），事后跑一个脚本复盘检索质量。

与 seed 离线评估的分工：

| | seed 离线评估（`eval_dialogue/`） | **Trace 在线评估（本文件）** |
|---|---|---|
| 数据 | 固定 55 条标注问题集 | 真实用户对话中的真实检索 |
| 回答 | "代码没变差"（可复现对比 baseline） | "真实检索好不好"（真分布） |
| 判定 | 代理指标（hit 判定） | LLM 自动打分 + 结构信号 + 人工确认 |
| 自动化 | 可进 CI | 手动跑报告 |

**一句话**：seed 管回归（不倒退），trace 管真实（知好坏）。

## 2. 三层分工（设计原则）

```
        评判者              擅长                       盲区
┌──────────────────────────────────────────────────────────────┐
│ LLM judge    内容相关度、规模化、可复现、可设阈值  系统正确性   │
│ (主评估)     同输入同输出（裁判一致性）            (范围/通道) │
├──────────────────────────────────────────────────────────────┤
│ 结构信号     零命中率/scope覆盖率/通道分布         语义相关性   │
│ (免费防线)   系统性错误一抓一个准                  (内容好不好) │
├──────────────────────────────────────────────────────────────┤
│ 人工         深度排查、发现"为什么"、校准评估集    规模化       │
│ (兜底)                                                   │
└──────────────────────────────────────────────────────────────┘
```

**为什么不是纯 LLM judge 或纯人工**：
- 人工无统一标准、不可复现、看不过来
- LLM judge 只评"内容相关度"，**发现不了系统性错误**（如 scope 串作品——单条看内容相关，LLM 会给高分，但它不知道命中了错误作品；只有 scope 覆盖率这类结构信号能抓）
- 人工只做两件事：确认低分项（防裁判误判）+ 校准评估集

## 3. 组件与数据流

```
前端对话（通用助手 / 扮演）
    │ 自动埋点（RAG_TRACE_ENABLED=1 默认开）
    │ 会话归属：请求上下文绑定 session_id → trace 自动附带
    ▼
data/traces/rag_trace.jsonl
    │ 两种记录
    │  kind=novel_retrieval  （NovelRetrieval.search_raw：完整语义）
    │  kind=store_search     （HierarchicalNovelStore.search：覆盖扮演等全路径）
    │ 字段：+ session_id（检索发生在会话请求内时）
    ▼
scripts/dev/rag_trace_review.py
    ├─ ① 结构信号（零命中率 / scope 覆盖率 / 通道分布 / 平均耗时）
    ├─ ② LLM judge（--llm-judge，复用 judge_self，DeepSeek 相关度 0-1 + 理由）
    └─ ③ 报告：分数分布 + 低分 top-N + 逐条详情（命中原文预览）
```

### 会话归属与评估口径（2026-08-08 决策）

- **归属机制**：`chat` / `impersonate` 路由（chat、chat_stream、regenerate）在请求上下文
  （`src/shared/request_context` 的 session_id ContextVar）绑定会话，检索时 `append_trace`
  自动附加 `session_id` 字段；非会话上下文（后台任务如建卡 deepen、脚本）不附加。
- **评估口径**：`/api/v1/agent/rag-eval` 只统计**现存会话**（角色扮演 + 通用助手，内存+磁盘）
  的 trace；无归属旧数据与已删除会话的数据一律不展示——评估反映当前系统真实状态，
  已删会话的坏数据视为过期（历史问题由 seed 离线评估与 `.old` 审计文件兜底）。
- **后台任务不纳入在线评估**：建卡 deepen 等检索是确定性输入、低频固定场景，
  由离线/脚本评估覆盖更合适；无归属 trace 仍写入文件（审计），但评估页过滤。

### 埋点位置

| 文件 | 埋点 | kind |
|------|------|------|
| `src/application/novel/retrieval.py` | `search_raw` 返回前 | `novel_retrieval`（完整：改写后查询数/权重/实体/scope/hits；2026-08-09 起查询改写为单次完整改写，`query_variants` 恒为 1） |
| `src/infrastructure/hierarchical_store.py` | `search` 返回前 | `store_search`（覆盖扮演 `_retrieve_*`、工具等所有 store 检索） |

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `RAG_TRACE_ENABLED` | `1`（开） | 关掉则完全不记录（测试环境 conftest 自动关） |
| `RAG_TRACE_DIR` | `data/traces` | trace 文件目录 |
| `RAG_TRACE_MAX_ENTRIES` | `20000` | 超过轮转到 `rag_trace.jsonl.old` |

## 4. 使用方式

```bash
# 0) 启动服务（trace 默认开），前端对话几轮（问小说相关问题）

# 1) 完整评估：结构信号 + LLM 自动打分（需要 DEEPSEEK_API_KEY）
python scripts/dev/rag_trace_review.py --llm-judge

# 2) 只看结构性失败（免费，无 token）
python scripts/dev/rag_trace_review.py --zero-only

# 3) 过滤与导出
python scripts/dev/rag_trace_review.py --q 利姆露            # 按 query
python scripts/dev/rag_trace_review.py --kind store_search   # 只看扮演路径
python scripts/dev/rag_trace_review.py --channel character   # 按通道
python scripts/dev/rag_trace_review.py --limit 20 --out review.md
python scripts/dev/rag_trace_review.py --json > review.json

# 4) 省 token：只评前 N 条
python scripts/dev/rag_trace_review.py --llm-judge --judge-limit 20

# 5) 人工标注闭环（备用，不依赖 LLM）
python scripts/dev/rag_trace_review.py --annotate > annotate.csv
#   ↑ 编辑 CSV 填 relevant 列（y/n）
python scripts/dev/rag_trace_review.py --summarize annotate.csv
```

### 参数速查

| 参数 | 说明 |
|------|------|
| `--llm-judge` | DeepSeek 对每条非零命中检索打分 0-1 + 理由 |
| `--judge-limit N` | judge 最多评 N 条（省 token） |
| `--judge-concurrency N` | judge 并发（默认 4） |
| `--kind` / `--channel` / `--q` / `--zero-only` | 过滤 |
| `--annotate` / `--summarize` | 人工标注闭环 |

## 5. 报告解读

```
### 概览
- 零命中: 2（67%）            ← 结构性失败信号（越高越危险）
- 检索范围锁定: 3（100%）      ← scope 隔离生效（本应 100%）
- 平均命中数 / 平均耗时 / 变体数

### LLM Judge 评分（--llm-judge）
- 平均分: 0.100
- ⚠️ 低分项（<0.5，建议人工确认）
  - 0.10 `雷姆的性格特点` [narrative]
    - 理由：上下文仅提及雷姆的名字和对话，但未涉及她的性格特点
    - 命中：爱蜜莉雅: 可是,在那之前要先把玛德琳……
```

**判定指南**：
- 零命中率高 → 查系统性原因（通道空 / scope 冲突 / 数据缺失），不是 LLM 能答的
- LLM 低分项 → 人工看原文确认是"检索质量问题"还是"裁判误判"
- scope 覆盖率 < 100% → 有跨作品污染风险

## 6. 实测案例（2026-08-08）

Trace 复盘 + LLM judge 立即发现 3 个真实问题：

| 问题 | 信号 | 根因 |
|------|------|------|
| 关系查询零命中 | 零命中率 67% | character 通道 0 块（story_analysis 从未索引）→ 已修复 |
| 全通道零召回 | 零命中率 100% | scope 冲突 bug（doc_id + series AND 矛盾）→ 已修复 |
| persona 查询命中差 | LLM judge 0.10 分 | "雷姆的性格特点"命中他人对话，通道权重噪声 → 待优化 |

**结论**：结构性错误由免费信号抓住，内容质量由 LLM 打分暴露——两者缺一不可。

## 7. 已知限制与后续

| 限制 | 说明 |
|------|------|
| 评估口径为现存会话 | 无归属/已删除会话的 trace 不展示（决策见 §3）；如需全量诊断用复盘脚本 `--trace` 指向原文件 |
| 旧 trace 无 session_id | 2026-08-08 前记录无会话归属，评估页自动排除；新数据正常归属 |
| 旧索引无 chapter_order | 关系/事件块时间过滤对新索引生效；旧数据需重跑 `index_story_analysis` |
| LLM 裁判噪声 | 低分项需人工确认；judge 阈值仅参考，不设硬门槛 |
| 不替代 seed 门禁 | trace 不可复现，无法挡回归；建议恢复离线 seed CI 门禁作层 1 |
