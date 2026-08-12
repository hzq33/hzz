# 角色名归一方案 I：全书角色表一次生成（深挖设计）

> ✅ **2026-08-10 状态更新：分批归一已实施**（`character_inventory` 的
> `llm_batch_size` / `llm_batch_chars` 分批逻辑，见 `builder.py`）。
> 原 "Proposed" 状态已过时，本文档作为设计背景保留。


> 日期：2026-08-06 | 状态：Proposed | 替代 `_llm_normalize_batch` 的分批归一

## 1. 旧方案的致命缺陷（为什么必须改）

```
NER碎片(31簇) → 分批归一(每批15簇,每簇2条evidence)
  ├─ LLM看不到"太宰治"和"慎太郎"两个簇同时 → 无法判断是一人还是两人
  ├─ LLM看不到"绫野"和"绫野光希"同时 → 无法发现该合并
  └─ canonical选择只靠单簇内部,看不到全书角色全貌
```

**根本矛盾**：LLM 归一需要全局视野（跨簇对比），但旧方案把输入切成孤立的 batch。

## 2. 方案 I 全貌

```
NER(31簇) → 聚类(不动) → │ 一次 LLM 调用 │ → 完整角色表 → 校验 → 落盘
               ↓          │   ↓            │
           全部簇+evedence │  角色表JSON     │
              (25KB输入)   │ canonical+alias │
                           │ merge/drop决策  │
```

**LLM 输入**：31 个簇的完整信息（每簇 6-8 条 evidence，多样性抽样）

**LLM 输出**：一份完整的角色表 JSON
```json
{
  "characters": [
    {
      "canonical_name": "温水和彦",           // 必须有全名
      "aliases": ["温水", "温温", "温水君"],  // 简称/称呼变体
      "importance": "main",
      "from_clusters": ["c8","c17"],          // 合并了哪些簇
      "evidence_for_canonical": "c8 evidence片段中包含'温水和彦'" // 为什么选这个全名
    },
    ...
  ],
  "dropped": [
    {"from_clusters": ["c3"], "reason": "太宰治是作家名,非角色"},
    {"from_clusters": ["c13"], "reason": "夏目漱石是文中引用的作家"}
  ]
}
```

## 3. 与旧方案的关键差异

| 维度 | 旧方案（分批归一） | 新方案（一次全局） |
|------|-------------------|-------------------|
| LLM 调用次数 | 2-3 次（分 batch） | **1 次** |
| 跨簇对比 | ❌ 不可见 | ✅ 全部可见 |
| canonical 归属 | 单簇 surfaces 中选 | 全表 cross-reference 确认 |
| 合并决策 | 靠 LLM 猜（无全局信息） | LLM 看到全貌后判断 |
| 输出校验 | 无（事后 alias 回退） | **内建校验**（canonical 必须在 evidence 里） |
| R4 alias 归并 | 需要独立跑一轮 | **降为校验层**（读 inventory 即可，不再重跑 LLM 归并） |

## 4. Evidence 抽样策略

旧方案每簇 2 条、窗口 40 字符——实在太少。新方案：

- **每簇 6-8 条 evidence**（31 簇 × 8 条 × 100 字符 ≈ 25KB，deepseek 128K context 轻松容纳）
- **窗口扩到 100 字符**（surrounding 50 + mention 名本身 + trailing 50）
- **多样性抽样**：同一名字在不同章节/场景的 evidence 各取一条（用 pos 间隔 > 5000 字符判断），避免"全是同场景重复"
- **最大输入硬上限**：120KB（约 200 簇 × 6 条 × 100 字）——全书导入绰绰有余

## 5. LLM Prompt 设计

### System Prompt（在旧版 _SYSTEM 基础上强化）

```
你是轻小说角色归一器。根据提供的所有候选人名簇，生成一份完整、无重复的角色表。

规则：
1. **去噪**：删除以下类型 →
   - 文中引用的作家/名人（夏目漱石/太宰治/川端康成）
   - 职业/身份词（店员/部长/老师/学姐——除非是唯一指代）
   - 作者/编辑/图源等元信息名
   - 碎片（单字、数字、标点）
   
2. **合并**：同一角色的多个簇合并为一条 → 依据：
   - 简称+全名（八奈见 + 八奈见杏菜 → canonical=八奈见杏菜）
   - 共享姓且可确认是同一人（需 evidence 支持）
   
3. **拆分**（关键）：共享姓或字 ≠ 同一人 →
   - 温水和彦 与 温水佳树 不得合并（兄妹，不同人）
   - 月之木古都 与 玉木慎太郎 不得合并（玉木≠月之木）
   - 拿不准就拆成两条

4. **全名选择**（关键）：canonical_name 必须是最大完整形式 →
   - 如果 evidence/surfaces 中有"温水和彦"和"温水和"，选"温水和彦"
   - 如果 evidence/surfaces 中有"袴田草介"和"田草介"，选"袴田草介"
   - 选最长、最正式的形式，简称进 aliases
   - canonical 必须能从 evidence 中验证（引用 evidence_for_canonical 字段）

5. **禁止幻觉**：没有证据不要编造全名。拿不准的可保留为 extra importance

6. **只输出 JSON**，格式见 user prompt
```

### User Prompt

```json
{
  "series": "败犬女主太多了",
  "total_clusters": 31,
  "clusters": [
    {
      "id": "c1",
      "surfaces": ["八奈见", "八奈见杏菜", "八奈", "加奈"],
      "total_mentions": 184,
      "evidence": [
        "…八奈见杏菜叹了口气，「温水君，你这个人啊…」…",
        "…「八奈见同学？」她回过头来…",
        "…杏菜每次来都会点这个…(vol02 ch03)",
        "…八奈见杏菜坐在窗边的位置上…(vol01 ch05)"
      ]
    },
    ...
  ],
  "instruction": "请输出角色表 JSON（characters + dropped）。canonical_name 必须是 evidence/surfaces 中出现的最完整人名形式。"
}
```

## 6. 输出校验层（LLM 输出后自动执行）

LLM 归一的输出不再全信——**加一道程序化校验**：

| 校验 | 规则 | 动作 |
|------|------|------|
| **C1 完整性** | canonical_name ∈ (surfaces ∪ evidence) 中任一完整名字 | 不通过 → 自动替换为 evidence 中最长匹配形式 |
| **C2 别名冲突** | 同一 alias 不能属于两个 canonical | 冲突 → 拆分，选 mentions 多的保留，少的另立 |
| **C3 共享姓保护** | 若有"温水/佳树""月之木/玉木"同时存在，警告 | 标记需人工复核，不自动合并 |
| **C4 全名覆盖** | 若 evidence 中出现比 canonical 更长的完整人名，且不是称号+本名 | 自动提升 canonical（如"温水和"→"温水和彦"） |
| **C5 无声人** | canonical 为空或单字 | 剔除 |

**输出分两级**：`validated`（通过所有校验的） + `review`（C3 警告、需人工确认的）。

## 7. 与 R4 alias 归并的关系

旧链路：R3 inventory → R4 alias 归并（LLM 再次调用） → alias.json

新链路：**R3 一次生成完整角色表 → R4 降为"校验 + 落盘"**（不再跑 LLM）

- R4 从 inventory 读 R3 输出，直接生成 alias.json 初稿
- 用户复核时只看 C3 警告项（共享姓保护标记），不必复核全部实体
- alias.json 的落盘格式不变（兼容现有对话抽取/口吻模仿的上游消费）

## 8. 成本对比

| | 旧方案 | 新方案 |
|---|---|---|
| LLM 调用 | 2-3 次（分 batch） | **1 次** |
| 每调用 token | ~3K input × 2-3 = 6-9K | ~30K input（31 簇 × 6 evidence × 100 字） |
| 总 token | 约 10K-15K | 约 30K + ~3K output |
| R4 alias 归并 | 需另跑 LLM（再加 token） | 无需（降为校验） |
| **总成本** | 较高 | **更低或持平** |

## 9. 验证方案（败犬 2 卷实测）

用现有败犬 vol01-02 的数据，跑新链路 → 对照脏点清单逐项验证：

| 脏点 | 旧链路输出 | 新链路预期 |
|------|-----------|-----------|
| canonical "温水和" | ❌ 截断 | ✅ "温水和彦" |
| canonical "田草介" | ❌ 截断 | ✅ "袴田草介" |
| "太宰治 can=作家" | ❌ 保留 | ✅ dropped |
| 绫野 + 绫野光希 拆分 | ❌ 2 条 | ✅ 合并为"绫野光希" |
| 月之木 aliases 含"玉木" | ❌ 污染 | ✅ 拆分为"月之木古都"+"玉木慎太郎" |
| 佳树 aliases 含"春树" | ❌ 错字 | ✅ 不出现"春树" |
| "小千"吞"小鞠" | ❌ 错合并 | ✅ 拆分为"小鞠知花"+"小拔小夜(或drop)" |
| 甘夏 + 古奈美 拆分 | ❌ 2 条 | ✅ 合并为"甘夏古奈美" |

## 10. 分层落地

| 层 | 内容 | 文件 | 验证标准 |
|----|------|------|----------|
| **L0** | LLM prompt 验证（抽 15 簇手动跑一次 LLM，看输出质量） | 临时脚本 | canonical 正确率 ≥ 80%（对照 gold） |
| **L1** | 实现 `build_character_inventory_v2`（替换 builder.py 的 R3 段 + 加校验层） | `src/domain/novel/character_inventory/` | 34-45→all_dirty 全部清理 |
| **L2** | R4 alias 归并适配（从 inventory 读而非重跑 LLM） | `scripts/dev/analysis/rebuild_alias.py` | alias.json 干净落盘 |
| **L3** | 全量回归（导入观察日记全卷 + 败犬全卷，跑全链路） | — | 脏点数清零或 C3 人工可处理 |

## 11. 拍板点

1. **Evidence 抽样**：每簇 6-8 条、窗口 100 字符——够吗？（全书 31 簇 × 8 × 100 = 25KB，富余）
2. **校验层 C1/C4**：自动替换 canonical 的逻辑——如果 LLM 选错了但 evidence 里有对的，自动修正。同意这个"不信任 LLM 输出"的设计？
3. **R4 降为校验**：不再跑 LLM alias 归并——同意？（省一轮 token）
4. **L0 先验证 prompt**：我抽 15 簇写 prompt，手跑一次确认 LLM 输出质量——可以吗？
