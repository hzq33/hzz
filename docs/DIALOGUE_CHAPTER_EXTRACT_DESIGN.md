# 按章优先 + 超长滑动的对话抽取

> 状态：**Current（默认主路径）** | 同步 2026-07-28  
> 日期：2026-07-27  
> 替代默认路径：`archive/SPEAKER_ATTR_INGEST_DESIGN.md` 的「正则 span + 短窗分类」  
> 旧路径保留为 `provider=legacy_window` / `cloud`（兼容别名）

---

## 1. 决策

| 决策 | 结论 |
|------|------|
| 主路径 | **按章整段 LLM**：同时抽取台词 + 定说话人 |
| 超长章 | **仅在该章内**按字数滑动（`win` / `stride`） |
| 无对话章 | 标题规则 + 无引号 → 跳过（简介/后记/制作信息） |
| 候选名 | `character_inventory` seed + 章内已解析名 |
| Haruhi | 不作为主路径 |
| 旧短窗 | `legacy_window` 保留，默认关闭 |

原则：优先保留叙事上下文；接受轻度漏抽，换说话人正确。

---

## 2. 流程

```text
document.chapters[]
  → F0 章过滤（简介/后记/无引号）
  → F1 切块：len≤max_chunk → 整章；否则章内滑窗
  → F2 Cloud LLM 抽取+归因
  → F3 重叠去重 → DialogueTurn → NovelBlock
```

---

## 3. 配置（`novel_rag.dialogue_attribution`）

| 键 | 默认 | 说明 |
|----|------|------|
| `provider` | `cloud_chapter` | 新默认；`legacy_window`/`cloud`/`haruhi_window`/`off` |
| `max_chunk_chars` | `6000` | ≤ 则整章一次 |
| `slide_win_chars` | `3500` | 滑窗 |
| `slide_stride_chars` | `2000` | 步长 |
| `max_output_tokens` | `4096` | 单窗 completion |
| `require_quote_marks` | `true` | 无「」『』跳过 |
| `min_chapter_chars` | `80` | 过短且无引号跳过 |
| `skip_title_patterns` | 见实现 | 标题黑名单 |

环境变量：`NOVEL_DIALOGUE_ATTR_PROVIDER` 可覆盖 provider。

---

## 4. 观测 meta

`chapters_total` / `chapters_skipped` / `skip_reasons` / `windows` /  
`llm_calls` / `turns` / `unknown` / `slide_chapters` / `dedupe_dropped` / `conflicts`

欠采样与说话人错绑的修复策略见 [DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN.md](./DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN.md)（先诊断 meta，再提量+纠偏；重抽须带归因校验）。

---

## 5. 非目标

- 不修 EPUB 章节标题质量  
- 不改 narrative 分块  
- 不以 Haruhi 为主路径  
