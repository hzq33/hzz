# 名字收割 LLM 化设计（章级 Harvest）

> 状态：**L1-L3 已落地**（harvest 模块 + 主路径接入 + 真实重抽验证）| 日期：2026-08-05
> L4 待办：speaker 正确率抽检（B4 KPI ≥90%）；达标后决定全库重抽
> 相关：[DIALOGUE_CHAPTER_EXTRACT_DESIGN.md](./DIALOGUE_CHAPTER_EXTRACT_DESIGN.md) · [DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN.md](./DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN.md) · [CHARACTER_INVENTORY_DESIGN.md](./CHARACTER_INVENTORY_DESIGN.md)

---

## 1. 背景与问题

对话归因的候选名有两个来源：**卷级 volume_seed**（`character_inventory` 的 `build_llm_seed`：黑名单 → min_mentions → percentile → top_k，质量已生产化）与 **窗级局部收割**（`candidates_from_text`，正则）。

局部收割用于捕获"本章出现但不在 volume_seed"的角色（龙套/章节特有）。当前实现：

```python
re.compile(r"([\u4e00-\u9fff]{2,3})(?:同学|小姐|先生|桑)?(?=说|道|问|…)")
```

**实测缺陷**（2026-08-05，败犬/史莱姆语料）：

| # | 缺陷 | 案例 |
|---|------|------|
| 1 | 4 字名截断成 3 字 | 维鲁多拉 → `维鲁多` |
| 2 | 跨词碎片混入 | "利姆露又喊" → `姆露又` |
| 3 | 5 字名收不全 | 八奈见杏菜 → `见杏菜` |

**根因**：正则"名字=2-3 个汉字+动词前瞻"无法表达真实名字边界（长度不定、无词边界、敬称变体、翻译名/外来语名）。

**决策**：收割改由 **LLM 完成**（章级，每章 1 次，可 batch）。LLM 对名字识别天然泛化（任意长度/语言/风格），正则方案无法达到同等泛化性——这是正则的结构性局限，堆规则不可解。

## 2. 方案 A：章级 LLM 收割

### 2.1 调用位置变更（核心架构点）

```
现状（每窗）：assemble_prompt_candidates
  → _window_local_candidates → candidates_from_text（正则，每窗重复收割）

变更后（章级一次、窗级复用）：
  extract 章循环 → harvest_chapter_names(chapter, llm) → 章名单缓存
  每窗 assemble_prompt_candidates(chapter_harvest=缓存的章名单, …)
```

- 收割频率：**每章 1 次 LLM 调用**（或 2-3 章 batch 1 次，配置 `harvest_batch_chapters`）
- 窗级不再调 LLM 收割，只做本地过滤/合并 → **成本不随窗数放大**
- 卷级 volume_seed 不变（仍走 inventory seed）

### 2.2 数据流

```text
chapter.text
  → F1 harvest LLM：提取本章说话人名单（JSON）
  → F2 幻觉防御：逐名校验「出现在本章原文」+ is_noise_speaker + 长度≥2
  → F3 与 volume_seed 去重合并（volume_seed 优先序不变）
  → F4 窗级候选组装（替换 candidates_from_text 的步骤 2）
```

候选优先级（保留原序）：

1. `_high_confidence_seeds`（span 规则 hint——named_colon/postfix_said，**不动**，可靠且不截断）
2. **章级 harvest 名单 ∩ 本章文本**（新，替换正则收割）
3. volume_seed ∩ 本章文本
4. volume_seed 补齐

### 2.3 Prompt 设计

```
你是小说对话归因助手。从下面的章节文本中，提取【说话人】名字清单。

规则：
- 只提取实际说了话的人（出现在引号台词旁/叙事句中）
- 名字可以是任意长度（中文名、翻译名、敬称变体如"利姆露大人"→"利姆露"）
- 被呼叫方不算说话人（"维鲁多拉大人，快醒醒"的说话人是呼叫者，不是维鲁多拉）
- 忽略：他/她/众人/少年 等指代词、旁白、无名字的碎片
- 不确定的名字不要编造；最多 20 个

输出 JSON：{"names": ["八奈见杏菜", "烧盐", …]}
```

### 2.4 幻觉防御（F2，必做）

| 检查 | 规则 |
|------|------|
| 原文包含 | 名字必须出现在本章文本中（否则丢弃）——**防编造名的第一道闸** |
| 噪声过滤 | `is_noise_speaker` 复用（单字/代词/虚词碎片） |
| 长度 | ≥2 字 |
| 与 seed 合并 | 已有名不重复；harvest 名不覆盖 volume_seed 顺序 |

### 2.5 容错

- JSON 解析：三层兜底（code fence → 纯 JSON → 首尾 `{}` 截取），复用 `hermes-python-workarounds` 技能的 `parse_llm_json` 模式
- LLM 调用失败/超时/无 API key → **静默降级**到原正则路径（`candidates_from_text` 保留作 fallback）
- 空名单 → 跳过收割，直接用 volume_seed

## 3. 配置（`novel_rag.dialogue_attribution`）

```yaml
harvest:
  enabled: true          # false = 完全走原正则路径
  batch_chapters: 2      # 每 N 章 1 次 LLM 收割（2 = 成本减半）
  max_names: 20          # 单章名单上限
  min_confidence: 0      # 预留（当前不用置信过滤，用原文校验）
  cache_dir: "data/dialogue_harvest/"   # 卷级缓存，重抽不重复调用
```

## 4. 实施分层

| 层 | 内容 | 验证 |
|----|------|------|
| L1 | 新增 `dialogue_pipeline/harvest.py`（prompt + JSON 解析 + 幻觉防御 + 降级）；config 加 `harvest` 段；单测（FakeLLM：正常/编造名/空/坏 JSON/失败降级） | pytest 全绿；ruff 全绿 |
| L2 | `extract.py` 章循环接入 harvest；`assemble_prompt_candidates` 增加 `chapter_harvest` 参数（替换正则收割步骤 2）；`_window_local_candidates`/`candidates_from_text` 保留为降级路径 | 存量测试不破（正则路径仍在） |
| L3 | 真实数据验证：重抽败犬 vol05，对比收割名单（无"维鲁多"截断/无"姆露又"碎片/长名完整）与 meta | 一卷重抽 meta + 名单对比表 |
| L4 | 抽样 speaker 正确率（B4 KPI ≥90%）；如达标 → 决定全库重抽 | 抽检表 |

## 5. 验证标准

1. L1 单测覆盖 4 个容错场景（正常/幻觉/坏 JSON/降级）
2. L3 重抽 vol05：harvest 名单包含"八奈见杏菜"等长名，**零** `维鲁多`/`姆露又` 类碎片
3. 成本：vol05 收割调用 ≤ ceil(14 章 / batch 2) = 7 次，归因 24 次不变
4. L4 抽检 speaker 正确率 ≥ 90%

## 6. 明确不做

- 不用 LLM 收割替代 `build_llm_seed`（inventory 已生产化，两套机制职责不同）
- 不改 `_high_confidence_seeds`（span 规则 hint 可靠，非本次问题）
- 不在窗级调 LLM 收割（成本随窗数放大）
- 不用 LLM Judge 做归因修复（沿既有红线）

## 7. 过期资产

| 资产 | 处置 |
|------|------|
| `candidates_from_text` 正则 | **保留**为降级路径（LLM 失败时），不删除；测试注释中"已知缺陷"记录待实施后更新 |
| `_window_local_candidates` | 保留为降级路径；主路径改走 harvest |
| 测试中固化的截断行为断言 | L3 后更新（harvest 上线后正则不再走主路径） |

## 8. 文档索引

| 文档 | 角色 |
|------|------|
| 本文 | 收割 LLM 化唯一说明 |
| DIALOGUE_CHAPTER_EXTRACT_DESIGN | 按章抽取主路径（收割是其中的候选步骤） |
| DIALOGUE_UNDERSAMPLE_ATTR_FIX_DESIGN | 量/质修复总纲（Phase B 归因质量） |
| CHARACTER_INVENTORY_DESIGN | volume_seed 生产路径 |
