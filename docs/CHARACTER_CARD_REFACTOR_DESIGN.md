# 角色卡生成重构设计

> ✅ **2026-08-10 状态更新：已实施**。角色卡已含 v2.0 结构化字段
> （`PersonalityProfile`：traits / speech_patterns / structured_catchphrases），
> `to_prompt()` 使用结构化数据。原 "Proposed" 状态已过时，本文档作为设计背景保留。


> 日期：2026-08-06 | 状态：Proposed

## 1. 现有流程与问题

```
对话块 + 叙事块
  → _discover_characters()   ← 说话人干净了（alias 映射 ✓）
  → LLM PersonalityProfile    ← 口吻特征靠 prompt，质量不稳定
  → catchphrases              ← 简单 text 字段，不适配检索
  → 落盘 character_card.json
```

**核心问题**：角色卡设计的初衷是"角色介绍"，不是"口吻模仿素材"。现有字段缺少结构化的口吻特征。

## 2. 重构目标

角色卡输出两个层面的数据：

| 层 | 用途 | 内容 |
|----|------|------|
| **① 口吻素材层** | 直接被口吻模仿检索器消费 | 每个角色的代表性对话样本（精选 8-15 条） + 口吻标签 |
| **② 角色画像层** | 角色人格描述（现有功能增强） | 性格/说话风格/口头禅/情绪模式（结构化） |

## 3. 口吻素材层设计（新增）

### 3.1 对话样本精选

不再把全部 `dialogue_contents` 丢给 LLM，而是**精选代表性样本**：

```python
def _curate_style_samples(name, dialogues, max_samples=12):
    """从该角色对话中精选多样化样本"""
    # 1. 去重（相似度 > 0.85 的只留一条）
    # 2. 按长度分组（短/中/长 各取 3-4 条）
    # 3. 按 mood 分组（愤怒/开心/悲伤/平淡 各取 1-3 条）
    # 4. 优先取含特征词（口癖词/称谓词）的样本
    return curated
```

落盘到角色卡的 `style_samples` 字段，口吻模仿检索器直接消耗（不再需要每次去 LanceDB 查）。

### 3.2 口吻标签

每条样本自动打标（从 mood + LLM 分析）：

```json
{
  "style_samples": [
    {
      "content": "草介这个骗子！明明说要娶我的！",
      "mood": "愤怒",
      "style_tags": ["娇嗔", "青梅竹马", "称谓直呼"],
      "length": "short",
      "source_chapter": "vol01 ch01"
    }
  ]
}
```

### 3.3 口吻特征向量

从精选样本生成角色的**口吻特征描述**（给 LLM 一段话描述，用于 prompt 里的口吻指令）：

```yaml
speech_profile:
  vocabulary: "常用词：骗子、笨蛋、可恶。自称'我'，称呼青梅竹马'草介'直呼其名"
  sentence_pattern: "短句为主（8-12字），感叹句多，反问句少"
  catchphrases: ["这个骗子", "明明说好"]
  emotional_expression: "愤怒时直接爆发（短感叹句），伤心时隔一句沉默再说话"
  rhythm: "语速快，停顿少，连续输出"
```

## 4. 实现改动

### 4.1 新增 `StyleSampleCurator`

```python
# src/domain/novel/style_sample_curator.py
class StyleSampleCurator:
    """从对话数据中精选口吻模仿样本"""

    def curate(self, name, dialogues, max_samples=12) -> list[StyleSample]:
        # 去重 → 分组 → 排序 → 截取
        ...
```

### 4.2 修改 `_build_character_block`

- 调用 `StyleSampleCurator` 生成精选样本
- 样本作为新的 `style_samples` 字段写入角色卡

### 4.3 增强 PersonalityProfile prompt

- 让 LLM 同时输出结构化的 `SpeechProfile`（词汇/句式/口头禅/情绪节奏）
- 替换现有的 `_profile_to_legacy_speech_style` 文字描述

### 4.4 口吻模仿检索对接

- 检索器从角色卡 `style_samples` 读（不再查 LanceDB 全表）
- 速度更快（本地 JSON vs 向量检索），质量更高（已筛选）

## 5. 分层实施

| 层 | 内容 | 文件 |
|----|------|------|
| L0 | `StyleSampleCurator` 实现（去重/分组/排序逻辑） | 新增 |
| L1 | `_build_character_block` 改造 + PersonalityProfile prompt 增强 | 修改 |
| L2 | 口吻模仿检索器对接（改从角色卡读样本） | 修改 |
| L3 | 全量验证（败犬/Re0 角色卡重建，检查样本质量） | 测试 |
