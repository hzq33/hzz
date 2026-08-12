# 扮演检索出处：口吻/事实拆分与 UI 可信展示

> 日期：2026-08-04 | 状态：**Implemented (Phase A+B)**  
> 后续口吻选型：[IMPERSONATION_STYLE_SAMPLE_POOL_DESIGN.md](./IMPERSONATION_STYLE_SAMPLE_POOL_DESIGN.md)  
> 触发：扮演问「库洛艾」时 UI 出处全是无关 `dialogue · 62–65%`，而 Lance 实测 narrative 能召回含库洛艾的块。  
> 相关：[ADR-004](./ADR-004-impersonation-agent.md) · [NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md](./NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md) · [NOVEL_RAG_DESIGN.md](./NOVEL_RAG_DESIGN.md)

## 1. 问题陈述

用户感知：**「召回质量不高」**。

复现特征（外传库 + 扮演利姆露）：

1. 问「你对库洛艾了解多少」→ 回复含「银发」等原文没有的细节。  
2. 气泡展示 `原文出处 (6)`，可见徽章却是三条 **`dialogue · ~65%`**，内容与库洛艾无关。  
3. 独立探针：对同一查询 **narrative 能稳定命中含「库洛艾」的块**（面具、道别、寄宿等）。

矛盾点：事实通道其实有相关 narrative，但 UI/心智模型被 **无关 dialogue 出处** 主导。

## 2. 现状数据流（事实）

```text
iter_chat_events / _build_messages
  ├─ (turn ≤ 3) _retrieve_style_samples
  │     search(channel=dialogue, top_k=3, query="{character} {user}")
  │     → prompt: 「## 风格参考（原著对话检索，事实锚）」
  │     → _append_citations(dialogue×3)          ← 先写入
  │
  └─ _retrieve_fact_context
        looks_like_fact_question? → QA→narrative 或 narrative top_k=3
        → prompt: 「## 原著参考 / 事实参考」
        → _append_citations(narrative/qa…)       ← 后写入

SSE citations 事件 = _last_citations 全量（通常 3 dialogue + 3 narrative = 6）

前端 BotBubble
  ├─ 「原文出处 (N)」打开 EvidenceDrawer → 可看全部 N 条
  └─ 徽章：items.slice(0, 3) → 永远是列表前 3 条 = dialogue
```

关键代码：

| 位置 | 行为 |
|------|------|
| [`impersonation_agent.py`](../src/core/impersonation_agent.py) `_STYLE_SAMPLE_TURNS=3` | 前 3 轮强制 dialogue 风格检索 |
| 同文件 `_retrieve_style_samples` | dialogue 命中全部进 citations，prompt 称「事实锚」 |
| 同文件 `_append_citations` | 保序去重，style 在前 |
| [`ImpersonationPage.tsx`](../frontend/src/pages/ImpersonationPage.tsx) `slice(0,3)` | 气泡只展示前 3 条 |
| [`EvidenceDrawer.tsx`](../frontend/src/components/knowledge/EvidenceDrawer.tsx) | 抽屉可看全量，但不分「口吻/事实」 |

## 3. 分层根因

### R1. UI 截断（直接导致「看得见的都是无关 dialogue」）

徽章 `slice(0, 3)` + citations 保序（style 先写）→ 用户默认只看到 dialogue。  
即使用户点开抽屉能看到 narrative，**首屏心智已被错误锚定**。

### R2. Dialogue 风格检索与主题无关仍展示

外传库中含「库洛艾」的 dialogue 极少（探针约 1 块）。  
`search(利姆露 + 了解多少, channel=dialogue)` 的 top3 常为无关对白，但：

- 无 `min_score` 截断  
- 无「snippet/角色必须覆盖查询实体」过滤  
- 仍写入 citations 与 prompt  

故徽章上的 62–65% **不是「库洛艾相关度」**，只是该 dialogue 块相对查询向量/RRF 的融合分。

### R3. Prompt 语义污染

Style 段标题为「风格参考（…**事实锚**）」，把口吻样本抬成事实依据；模型易把弱相关对话 + 先验混进设定回答（如银发）。

### R4. 语料内容上限（次要，但真实）

外传含库洛艾的 narrative（约 8 块）写的是面具/道别/寄宿，**全文无「黑发/银发」**。  
即使 UI/检索全修好，发色仍只能答「原文未写」或依赖正文语料——这是内容边界，不是本方案主修对象，但须在产品文案上区分。

### R5. 百分比语义不清（加剧「质量差」观感）

混合检索下 score 可能是 RRF/距离变换，前端仍 `×100` 显示为「%」。用户以为是「原文支持度」。

## 4. 目标与非目标

### 目标

1. 气泡首屏出处优先反映 **事实依据**（narrative/QA），不被 style dialogue 抢占。  
2. 口吻样本与事实证据 **分角色展示 / 分字段下发**，避免混称「原文出处」。  
3. Style dialogue 弱相关时不进事实 citations；prompt 不再称其为「事实锚」。  
4. 事实不足时 UI/prompt 明确「可能含推理」。

### 非目标（本轮）

- 重做整套 hybrid 检索算法  
- 必须导入正文才能答发色（语料问题另册）  
- ADR-005 分支存档等会话大改  

## 5. 方案设计

### 5.1 数据模型：拆分 citations

SSE / `StoryEvidence` 增加可选字段（向后兼容）：

```ts
interface StoryEvidence {
  // 既有字段…
  role?: 'fact' | 'style';   // 缺省：按 channel 推断（narrative/qa→fact，dialogue→style）
}
```

或事件级拆分（更清晰，推荐）：

```json
{
  "type": "citations",
  "fact": [ /* narrative/qa */ ],
  "style": [ /* dialogue style samples */ ]
}
```

前端合并展示时：徽章只取 `fact`；抽屉分两节「事实依据 / 口吻参考」。

**推荐采用事件级拆分**，避免前端靠启发式猜。

### 5.2 后端：组装与检索策略

| ID | 改动 | 说明 |
|----|------|------|
| B1 | Style 检索结果 **默认不进入 fact citations** | 仅进入 `style` 列表；可选 `include_style_in_citations=false` |
| B2 | Prompt 改文案 | `## 口吻参考（仅说话方式，勿当设定事实）` |
| B3 | Style 过滤 | 命中需满足：speaker/内容含 `character`，或 score≥`style_min_score`（建议 0.45）；否则丢弃 |
| B4 | Fact 检索强化 | 事实问句：query 用用户原文实体为主（少强制前缀角色名干扰）；narrative 优先写入 fact citations |
| B5 | 冲突指令 | system/user 追加：设定冲突时以「原著参考」为准；参考未写明则说不确定，禁止编造外貌 |

`looks_like_fact_question`：保持「多少」等提示；可增补「了解/知道/什么人」类，减少漏判（可选）。

### 5.3 前端：展示策略

| ID | 改动 |
|----|------|
| F1 | 气泡徽章：只展示 `fact` 前 3 条（按 score 降序）；无 fact 时显示「无事实锚点」而非塞 style |
| F2 | 计数文案：`事实出处 (n)`；若有 style，另显 `口吻参考 (m)` 或仅在抽屉出现 |
| F3 | EvidenceDrawer：两栏/两节；style 节小字说明「不保证与问题主题相关」 |
| F4 | 低置信：对 **fact** 列表判定；全部 fact\<0.35 或 fact 为空 → 提示推理 |

### 5.4 配置（可选）

```yaml
impersonation:
  style_sample_turns: 3
  style_top_k: 3
  style_min_score: 0.45
  style_require_character_mention: true
  fact_top_k: 3
  citations_split: true
```

## 6. 实施阶段

### Phase A — 展示可信（优先，改动小）

1. SSE 拆分 `fact` / `style`（或给 evidence 打 `role`）。  
2. 前端徽章只展示 fact；抽屉分节。  
3. Prompt 去掉「事实锚」误导文案。  

验收：库洛艾问题气泡首屏若有 narrative 命中，徽章应为 `narrative · …`，不再是无关 dialogue。

### Phase B — 检索质量

1. Style 过滤（角色提及 + min_score）。  
2. Fact 不足时显式降级文案注入模型。  
3. （可选）事实问句跳过 style 注入，或 style 仅保留 card 内样本。

验收：dialogue top 无关块不再进入 style 列表（或显著减少）；回复在无发色原文时倾向「不确定」而非编造。

### Phase C — 分数语义（可延后）

1. UI 对 RRF 分标注「相关分」而非默认「% 相似度」。  
2. 或后端同时下发 `raw_score` + `display_label`。

## 7. 验收用例

| # | 操作 | 期望 |
|---|------|------|
| A1 | 扮演利姆露，首轮问库洛艾 | 徽章以 narrative 为主；出处抽屉可见面具/道别相关 snippet |
| A2 | 打开抽屉 | 分「事实 / 口吻」；口吻块可存在但标注用途 |
| A3 | 问发色 | 若库中无黑发/银发句，不坚称银发（允许不确定） |
| B1 | style 检索全无关 | style 列表空或过滤后为空，不影响 fact 展示 |
| R | 回归 | 非事实闲聊仍可有口吻参考；无 fact 时提示清晰 |

## 8. 风险与回滚

- 拆分 SSE 字段：旧前端忽略新字段时可回退为扁平 `items`（兼容期双写 `items=fact+style`）。  
- Style 过滤过严：口吻多样性下降 → 调低 `style_min_score` 或仅事实问句启用过滤。  
- 回滚：feature flag `citations_split=false` 恢复旧行为。

## 9. 决策摘要

| 决策 | 选择 |
|------|------|
| 主因 | UI 前 3 条截断 + style 先写入 citations，而非 narrative「召不回」 |
| 主修 | 出处角色拆分 + 徽章只展事实；其次 style 过滤与 prompt 文案 |
| 语料 | 外传无发色是内容上限，与本 UI/检索错配问题分开表述 |
