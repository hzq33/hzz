# 扮演检索增强 — 全链路设计、验证与问题汇总

> 日期：2026-08-10 · 范围：world_knowledge 结构化检索 + 100 次扮演评估 + 检索增强迭代 + 漏洞修复
> 关联文档：WORLD_KNOWLEDGE_TOOL_DESIGN.md（工具设计）、E2E_100_IMPERSONATION_REPORT*.md（两轮评估）

---

## 一、背景与目标

用户指出：项目已演进出角色关系、时间线、设定书等结构化世界数据，但扮演检索仍停留在
"叙事+对话"向量通道旧范式。目标：
1. 结构化数据（关系/事件/时间线/设定书）建表，写工具让 LLM 按需查（agentic RAG，上下文零污染）
2. LLM 后处理判断召回相关性与工具价值，**判断结果回收为在线评估指标**
3. 事实依据只能是检索原文；结构化数据只做定位索引（用户原则）

## 二、架构（当前状态）

### 2.1 world_knowledge 建表 + 工具
- **存储**：`data/world_kb.sqlite`，5 表（relations/events/timeline/lorebook/character_events），懒构建（首次查询该系列时从 JSON 源构建），`scripts/dev/build_world_kb.py` 全量构建
- **数据规模**：8 系列入库；北境守望者 95 关系 / 79 事件 / 79 时间线 / 103 设定书
- **工具**：`world_knowledge`（builtin_world_knowledge.py）——5 种 query_type 参数化，返回行级摘要 + evidence block_id 指针（不展开原文）
- **别名扩展**：查询词经 alias 表扩展到 canonical+变体（利姆露→利姆鲁/利姆路）

### 2.2 判断回收（在线评估）
3 组 Prometheus 指标（src/shared/metrics.py）：
```
retrieval_relevance_total{verdict}   — 检索片段相关性（LLM 判断）
tool_value_total{query_type,verdict} — 工具查询价值（确定性信号）
answer_coverage_total{verdict}       — 可答性
```
双通道回收：
- **LLM verdict**（通用 chat）：主 LLM 回复后附 `<<VERDICT>>{...}<<END>>`，解析剥离上报
- **确定性信号**（扮演兜底）：工具/检索执行处按命中数回收（扮演 LLM persona 会忽略格式指令，实测无效）

### 2.3 检索链路（search_raw）
- 向量四通道（narrative/dialogue/qa/character）+ CharacterGraph 富集 + GraphRAG 全局层
- 查询改写：**3 变体**（实体补全/意图定向/事件发展 + 原查询保底，单次 LLM 调用输出）
- 跨变体 RRF 融合（search_raw 已有）

### 2.4 LLM 后处理节点（当前实现，src/core/impersonation/chat.py）
检索后、主回复前，`_llm_postprocess_facts`：
1. 主动调 world_knowledge（当前角色 relations + character_events，代码层执行）
2. LLM 结合工具结果 + 检索原文，输出结构化 JSON（relation_context / event_timeline / kept_snippets / answerable）
3. 注入 final_messages，主回复模型基于它回答（主提示词不变）

## 三、100 次扮演评估（5 万字合成书《北境守望者》）

### 3.1 评估配置
- 数据：88 章 / 14 角色 / 2028 句对话 / 621 块（551 narrative/52 dialogue/18 character）
- 8 核心角色建卡（亚瑟/莉娜/维克托/艾琳/雷恩/小卡/玛拉/老首领），79 事件/95 关系/79 时间线
- 96 问 = 8 角色 × 12，方向覆盖：daily 26 / relation 29 / temporal 18 / event 23
- 脚本：scripts/dev/e2e_100_impersonate.py（结果 data/eval/impersonation_100_results.jsonl）、e2e_100_analyze.py

### 3.2 第一轮（修复前：无 doc_id、单变体查询、无强制注入）
| 指标 | 值 |
|---|---|
| 成功率 | 100%（96/96） |
| 带引用率 | 99.0% |
| world_knowledge 调用 | 0 次（P3：工具利用率 0） |
| 平均耗时 | 6s |

发现的问题：
- P1：temporal 类答对靠角色卡背景+向量命中，非时间线结构化召回
- P2：1 次无依据回答（小卡"黑旗军来袭"0 引用，凭人设演绎）
- P3：world_knowledge 扮演场景利用率 0（扮演 LLM 人设优先，从不主动调工具）
- P4：引用数固定 5 条

### 3.3 第二轮（强制注入 + doc_id 锁定后）
| 指标 | 值 |
|---|---|
| 成功率 | 100%（96/96） |
| 带引用率 | 84.4%（↓，回答部分靠摘要） |
| world_knowledge 调用 | 100%（每轮强制） |
| 平均耗时 | 14s（↑ 工具查询开销） |
| retrieval_relevance | relevant=99 irrelevant=7（93.4%） |
| tool_value | relations valuable=141 useless=6；character_events valuable=163 useless=5 |

发现的问题：
- P1（严重）：检索盲区——莉娜「你救过老赵吗」答"没有叫老赵的人"（原文支线一有）。次要角色未召回 → 角色"诚实地答错"
- P2（中等）：摘要驱动回答缺引用 + 轻微 OOC（艾琳"我只是个商队首领"与原文矛盾）
- P3（低）：角色间引用率不均（艾琳 42%/莉娜 58%）
- P4（观察）：耗时翻倍

## 四、漏洞修复（doc_id 同类漏洞排查）

### 4.1 幽灵卡漏洞（高危）
- 根因：`CharacterCard.build` 无系列参数 → `store.doc_ids()[0]` 首卷 fallback → 错误系列卡；
  `_save_cache` 写裸名卡；`load(character)` 索引第一个匹配优先 → 幽灵卡劫持正确卡
- 实证：96 次评估期间生成 9 张幽灵卡（`Re：从零开始__艾琳_塔利斯.json` 等，内容北境但系列标签错）
- 修复：
  1. `CharacterCard.build` 增加 `series_id` 参数（有则系列卡缓存，不写裸名卡）
  2. `create_impersonation_agent` doc_id 推断 series_id 为权威来源，始终覆盖（不只空时）
  3. 会话复用 doc_id 变化时卡系列对齐（impersonation_sessions.py）
- 待办：9 张幽灵卡清理（用户暂缓，先修代码）

### 4.2 其他排查（未修复项）
- upload series_id 可选 → fallback 文件名（低危，有兜底）
- 会话恢复 doc_id 不重建卡（已修复，见上）

## 五、当前验证（10 次问题样本，后处理节点）

脚本：scripts/dev/e2e_postprocess_verify.py · 结果：data/eval/postprocess_verify.json

| 项 | 结果 |
|---|---|
| 平均耗时 | 12.1s（基线 6s / 强制注入 14s） |
| P1 老赵盲区 | ✅ 修复（莉娜答"北坡那个猎户…用了止血草"） |
| P2 艾琳 OOC | ⚠️ 未完全修复（仍答"哨站不是我的终点"） |
| 引用率 | ⚠️ 新问题：0/10（后处理绕过 citation 生成） |
| 工具调用 | ✅ 正常（relations 11/character_events 12 valuable） |

## 六、当前问题与待决

1. **后处理引用机制**：`kept_snippets` 应带 block_id → 生成 citation（当前 0 引用）
2. **P2 语义**：艾琳"决定留下"（支线二十四）需进后处理的事件发展维度
3. **幽灵卡清理**：9 张错误系列卡待确认后删除
4. **全量回归**：162 测试绿，当前改动未提交

## 七、改动文件（未提交）

- `src/core/impersonation/chat.py`：LLM 后处理节点（_llm_postprocess_facts + _POSTPROCESS_PROMPT）
- `src/application/novel/query_rewriter.py`：3 变体查询改写（单变体→3+原查询）
- `src/domain/character_card.py`：build 支持 series_id
- `src/core/impersonation_agent.py`：doc_id 权威系列来源
- `src/application/impersonation_sessions.py`：会话卡系列对齐
- `scripts/dev/e2e_postprocess_verify.py`：10 次问题样本验证
- 测试：162 passed（含 world_knowledge +7）

## 八、结论

1. world_knowledge 结构化检索链路成立：工具利用率 0→100%，tool_value 量化证明查询精准
2. 判断回收机制有效：retrieval_relevance 93.4%、answer_coverage 38/1——每个维度有数据可改进
3. 知识边界设计有固有张力：检索覆盖率是知识边界正确性的前提（检索不到=角色"不知道"=答错）
4. 后处理节点方向正确（P1 修复实证），但引用机制需补（kept_snippets→block_id→citation）
