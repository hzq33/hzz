# 扮演口吻参考：角色台词池选型设计

> 日期：2026-08-04 | 状态：**Implemented (Phase S1)** · S2/S3 未做  
> 前置：[IMPERSONATION_CITATION_FACT_STYLE_SPLIT_DESIGN.md](./IMPERSONATION_CITATION_FACT_STYLE_SPLIT_DESIGN.md)（出处拆分已落地）  
> 相关：[ADR-004](./ADR-004-impersonation-agent.md) · [CHARACTER_ON_DEMAND_DESIGN.md](./CHARACTER_ON_DEMAND_DESIGN.md) · [`scripts/dev/eval_style_sample_strategies.py`](../scripts/dev/eval_style_sample_strategies.py)

## 1. 问题

扮演「口吻参考」当前每轮对 **全库 dialogue 通道** 做向量检索（`query = "{角色} {用户话}"`），再事后过滤「块内出现角色名」。结果：

- 注入的是 **多人对话块**，角色本人台词占比极低；
- 用 **事实/情节问句** 当 style query，样本跟话题跑，不跟说话方式跑；
- UI 已拆分 fact/style 后，style 仍常为空或质量差，口吻实际只靠 card 系统样本。

目标：口吻样本应来自 **已提取且归因到该角色的台词**，与事实检索解耦。

## 2. 评估（外传库 · 利姆露）

脚本：`python scripts/dev/eval_style_sample_strategies.py`  
指标：**speaker_purity** = 注入行中 speaker 为该角色的比例。

| 策略 | 做法 | 均值 speaker_purity | 备注 |
|------|------|---------------------|------|
| **A 现状** | dialogue 检索 + 块级 mention 过滤，展开块内全部 turn | **~0.06** | 每查询展开 28–73 行，首行常为维尔德拉/伊夫利特等 |
| **B 过滤+本人** | `filters.characters` + **只保留角色 turn** | **1.0** | 立即可行；query 仍偏话题 |
| **C Card** | `CharacterCard.sample_dialogues`（建卡策展） | **1.0** | 稳定；已在 system prompt |
| **D 台词池策展** | `gather_evidence` 扫库 → `_curate_dialogue_samples` | **1.0** | 本库利姆露 pool≈**80** 句，策展 8 条 |

代表性坏例（A · 问「日向」）：命中块开场是维鲁多拉/伊芙利特旁观对战，角色本人句淹没在多人台词里。

结论：

1. **现状 A 不适合做口吻**（纯度崩）。  
2. **C 已够做默认口吻锚**；动态补充应用 B/D 的「本人台词」约束。  
3. 语料侧已有可用台词池（`gather_evidence`），不必再靠「话题相似对话块」。

## 3. 目标与非目标

### 目标

1. Style 注入行 **speaker_purity ≥ 0.95**（别名算本人）。  
2. 事实问句 **默认不再用用户原文做 style 检索**（避免情节污染）。  
3. Style citations 只含角色本人 turn（或明确标 `role=style` 的单句），不再塞整段多人块。  
4. 与 card 样本去重，避免 prompt 重复灌同一句。

### 非目标

- 不做完整「情绪分类器 / style embedding」一期工程。  
- 不改变 narrative 事实检索主路径（指代补全另案）。  
- 不要求 style 与问题主题相关。

## 4. 方案设计

### 4.1 默认策略（推荐）

```text
口吻 = Card 策展样本（system，已有）
     + 可选动态补充（仅非事实闲聊 / 样本不足时）
动态补充 ∈ 角色台词池（speaker ∈ {canonical}∪aliases）
```

| 模式 | 条件 | 行为 |
|------|------|------|
| **Card-only** | 事实问句，或 `style_sample_turns` 已过，或 card≥N | 不跑 dialogue 检索；style citations 可空或回显 card 锚点（可选） |
| **Pool-retrieve** | 闲聊且需动态样 | 从角色台词检索/抽样，只注入本人 turn |
| **Fallback** | pool 空 | 仅 card；打日志 |

### 4.2 动态补充实现（Phase 1）

改 `ImpersonationAgent._retrieve_style_samples`：

1. **门控**：`looks_like_fact_question(user)` → **直接 return ""**（事实轮不抢口吻带宽）。  
2. **检索**：
   ```python
   hits = await store.search(
       style_query,  # 见下
       channel="dialogue",
       top_k=style_fetch_k,  # e.g. 8
       doc_id=self.doc_id,
       filters={"characters": name_list},  # canonical + aliases
   )
   ```
3. **Turn 级抽取**：只保留 `speaker` 匹配 name_set 的句子；截断至 `style_top_k` 句（默认 3）。  
4. **去重**：与 `card.sample_dialogues` 正文规范化去重。  
5. **Prompt**：仍用「口吻参考（仅说话方式，勿当设定事实）」；每行 ` [角色] 台词 `，**不附整块多人对话**。  
6. **Citations**：每条 style evidence 对应 **单句**（snippet=该 turn），`role=style`。

**style_query 选择（Phase 1 简单版）**：

- 不用用户事实原文；  
- 使用固定探针：`f"{character} 对话 语气"` 或 card 中 1 条口头禅；  
- 或对闲聊短句：仅用用户句（无实体长问）——若 `len(user)<20` 且非事实启发。

### 4.3 台词池加强（Phase 2，可选）

复用 `gather_evidence` + `_curate_dialogue_samples`：

- 会话开始时（或建 agent 时）缓存 `style_pool: list[dict]`（≤200 turn）；  
- 动态轮：从 pool **随机分层 / 轮转** 抽 3 条（比向量检索更稳、无话题漂移）；  
- 建卡路径统一走同一 gather，减少 card 与 runtime 两套逻辑。

### 4.4 配置

```yaml
impersonation:
  style_sample_turns: 3          # 保留；事实问句仍跳过动态 style
  style_top_k: 3                 # 注入本人句条数
  style_mode: pool_turn          # off | legacy_block | pool_turn | card_rotate
  style_skip_on_fact_question: true
  style_require_speaker_match: true
```

`legacy_block` 仅作回滚。

### 4.5 与出处拆分的关系

- fact / style SSE 字段保持不变。  
- Style 变「少而纯」后，抽屉「口吻参考」应可读；徽章仍只展 fact。  
- 可选：card 样本不进 citations（避免与 system 重复展示）；动态句才进 style 列表。

## 5. 实施阶段

### Phase S0 — 验证已完成

- [x] `eval_style_sample_strategies.py`：A≈0.06 vs B/C/D=1.0  
- [x] 确认 `gather_evidence` 可抽出 ~80 条利姆露台词  

### Phase S1 — 纯度修复（优先实现）

1. [x] `_retrieve_style_samples`：事实问跳过；filters + turn 级 speaker 过滤；单句注入。  
2. [x] 别名：从 card.aliases 取 name_set。  
3. [x] 单测：mock hits 多人块 → 只留下角色句；事实问 → 无 style 检索调用。  
4. [ ] 复跑 eval：策略 A'（改造后）speaker_purity ≥ 0.95（可用 `pool_turn` 路径对照 B）。  

### Phase S2 — Card/Pool 统一

1. Agent 初始化缓存 style_pool（gather 或 card 扩展）。  
2. `style_mode=card_rotate`：前 N 轮轮换 card 样本进「口吻参考」区块（可选 UI），检索可关。  
3. 建卡 `_curate` 增加句式桶（疑问/感叹/命令/吐槽）——增强默认口吻覆盖。  

### Phase S3 — 意图分桶（可延后）

情绪/意图粗分类后再抽池；需标注或启发式，不阻塞 S1。

## 6. 验收

| # | 操作 | 期望 |
|---|------|------|
| S1-1 | 闲聊「你好呀」 | style 行全部为扮演角色说话；纯度 1.0 |
| S1-2 | 「你对库洛艾了解多少」 | **无**动态 style 注入（或仅 card）；fact 仍有 narrative |
| S1-3 | 抽屉口吻节 | 无维尔德拉/路人抢镜；snippet 为单句 |
| S1-4 | eval 脚本 | 改造后 A' 均值 purity ≥ 0.95 |
| R | 回归 | card system 样本仍在；非事实闲聊可有 0–3 条动态 style |

## 7. 风险与回滚

| 风险 | 缓解 |
|------|------|
| filters.characters LIKE 漏召 | turn 级二次过滤；必要时放大 fetch_k |
| 别名未进 name_set（利姆路/利姆露） | 建卡/normalize 别名写入 card |
| 动态 style 过少 | 回退 card_rotate；勿回退 legacy 多人块 |
| 回滚 | `style_mode: legacy_block` |

## 8. 决策摘要

| 决策 | 选择 |
|------|------|
| 评估结论 | 现状块级检索口吻 **不可用**（purity≈6%） |
| 默认口吻 | **Card 策展为主** |
| 动态补充 | **角色本人 turn 池**（filters + speaker），禁止整块多人对话 |
| 事实问句 | **跳过**动态 style 检索 |
| 一期不做 | 完整情绪分类 / 专用 style 向量索引 |
