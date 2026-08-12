# 对话提取（名单候选池驱动）设计

> 日期：2026-08-09 | 状态：已实现，覆盖/归因待完善
> 关联：`src/application/novel/dialogue_pipeline/extract.py`、`config.yaml → novel_rag.dialogue_attribution`、`scripts/dev/verify/dialogue_with_inventory.py`

---

## 1. 方案：名单候选池 + 模型归因

```
角色名单（inventory 75 个，V3 管线产出）
  → 名单 ∩ 本章文本定位 = 候选说话人（candidate_source: "inventory"）
  → 每窗口 LLM 归因："这句台词谁说"（窗口文本即 evidence）
  → 输出 dialogue blocks（台词 + 说话人）
```

**与"harvest"旧路径的区别**：

| | harvest（旧） | inventory（新） |
|--|-------------|----------------|
| 候选池来源 | 每章 1 次 LLM 收割（harvest_chapter_names） | 名单 ∩ 本章定位（**零 LLM**） |
| LLM 调用 | 每章 1 次 harvest + 每窗 1 次归因 | 仅每窗 1 次归因 |
| 成本 | 高 | **省掉全部 harvest 调用** |

配置：`config.yaml → dialogue_attribution.candidate_source: "inventory"`（可切回 "harvest"）。

## 2. 模型选择实测（glm vs Qwen3-8B）

| 项 | glm-4.7-flash（串行） | Qwen/Qwen3-8B（硅基流动，并行） |
|----|----------------------|-------------------------------|
| 耗时 | 284s（17 窗） | 121s（37 窗） |
| 429 限流 | ⚠️ 频发（并发=1，串行仍偶发 RPM 限制）→ circuit breaker 熔断跳过窗口 | ✅ 无 |
| 归因准确率 | ✅ 抽样全对 | ⚠️ 魔法筒误归（把雷昂/凯尼希台词归给技能"魔法筒"） |
| 成本 | 免费 | 免费 |

**结论**：
- Qwen3-8B 稳定（无 429）适合批量，但归因质量不如 glm（技能候选混入导致误归）
- glm 归因准，但限流需处理（限速/重试）

## 3. 当前问题与待办

| 问题 | 根因 | 对策 |
|------|------|------|
| 覆盖不足（37 窗口/46 turns） | `plan_document_windows` 窗口计划少（`require_quote_marks` 过滤、35 万字仅 37 窗） | 排查窗口计划参数 |
| 归因误归（魔法筒抢人物台词） | 会说话的技能（魔法筒等）在候选池中成为说话人候选 | 归因候选池**排除技能**（只留真实人物） |

已调参数（config.yaml）：
```yaml
dialogue_attribution:
  candidate_source: "inventory"
  concurrency: 8            # Qwen3-8B 无并发限制；glm 需改回 1 串行
  max_calls_per_doc: 200
  max_windows_per_chapter: 5
  max_turns_indexed_per_doc: 3000
```

## 4. 工具

- `scripts/dev/verify/dialogue_with_inventory.py`：名单驱动对话提取 + 分析（meta/speaker 分布/抽样）
- `scripts/dev/verify/redialogue_doc.py`：按 doc 重跑 dialogue 通道（A4 设计）

## 5. 参考

- 说话人候选池机制：`src/application/novel/dialogue_pipeline/extract.py`（candidate_source 分支）
- 名单来源：docs/CHARACTER_EXTRACTION_V3_DESIGN.md
