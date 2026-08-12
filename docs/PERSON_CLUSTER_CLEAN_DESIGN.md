# 角色名识别链路重构设计（Person Cluster Clean）

> 状态：**R1-R3 已落地，败犬 R4 完成**（用户复核 28 组落盘）| 日期：2026-08-06
> 待办：史莱姆全量归并 → 复核 → 落盘；inventory overlay；L4 真实重抽验证
> 验收标准（用户定义）：**角色名识别准确、没有噪声**
> 相关：[ALIAS_UNIFICATION_DESIGN.md](./ALIAS_UNIFICATION_DESIGN.md) · [CHARACTER_INVENTORY_DESIGN.md](./CHARACTER_INVENTORY_DESIGN.md) · [LLM_HARVEST_CHARACTER_NAMES_DESIGN.md](./LLM_HARVEST_CHARACTER_NAMES_DESIGN.md)

---

## 1. 现状链路与脏簇根因（调研实证）

```
原文 → CLUENER NER（argmax 丢弃置信度）
     → 子串 union-find 聚类（a in b or b in a 无条件合并）
     → _llm_normalize_batch（_SYSTEM：删噪声/补全名/合并簇）
     → InventoryCharacter → build_llm_seed → volume_seed
```

| 脏簇类型 | 实例（败犬 52 组实证） | 根因层 |
|---|---|---|
| 碎片簇 | 来水管/了离车/向十二/微认真/想个好/鞠频频 | NER 无置信度过滤，动词短语/跨词误标 |
| 占位簇 | 女生A/女生B/主角/店员/部长/孩子们 | NER 普通名词误标 |
| 错并簇（最严重） | **温水→温水佳树** | 聚类子串无条件合并，LLM 归一无拆解机制 |
| 截断 canonical | 凯金→凯金正 | LLM 选名截断 |

## 2. 重构设计（三层职责重划）

**核心原则：规则层只做"候选分组"，拆并决策全部交给 LLM；LLM 输出必须过一对一等价校验。**

### R1. NER 层 — 保留置信度（character_ner.py）

```python
@dataclass
class Mention:
    text: str
    start: int
    end: int
    source: str = "cluener"
    score: float = 0.0          # ← 新增：softmax 概率

# extract_person_mentions：
#   pred = logits.argmax(-1)  →  softmax 取 PERSON 标签概率
#   min_conf 过滤（默认 0.5，实测校准）
```

效果：低置信 mention（来水管/女生A 类）源头拦截；`is_noise_speaker` + 长度过滤保留为第二道。

### R2. 聚类层 — 只分组、不合并（character_ner.py）

**去掉 `a in b or b in a` 无条件 union**。改为：

```python
# 只做「表面形式归组」：剥敬称后精确匹配 + 编辑距离≤1 的近邻
# 全名↔简称（温水佳树 vs 温水）**不预合并**——交给 LLM 判断
```

效果："温水"和"温水佳树"进**不同簇**，LLM 归一时依据证据决定拆/并——错并根因消除。

风险：聚类粒度变细 → 簇数变多 → LLM 归一输入变大。缓解：`max_clusters_for_llm` 上限保持；同簇 surface 排序（count 降序）保证高频名进 prompt。

### R3. LLM 簇归一 v2 — _SYSTEM 增强（builder.py）

_SYSTEM 增加三条硬规则：

```
4a. 拆开：共享姓但不同人必须拆（温水和彦 ≠ 温水佳树；"温水"是温水和彦的简称，
    "佳树"是温水佳树的简称）——不得合并为一条
4b. 碎片过滤：动词短语/跨词/占位符（来水管/女生A/店员/部长）→ dropped
4c. canonical 约束：证据或 surface 中有更长正式姓名用全名；无证据不编造
    （"坦派斯特" > "坦派斯"）；不确定 → 保持 surface 最长者
```

输出 JSON 格式不变（canonical_name/aliases/importance/from_clusters/action）。

**LLM 归一后处理**（复用 `alias_merge.validate_merge_groups`）：
- 一对一等价校验（E1 变体冲突/E2 canonical 重复/E3 空名）→ 冲突时该批回退规则 fallback

### R4. 存量修正（已设计落地中）

变体级 LLM 归并（`alias_merge_probe` + `rebuild_alias`）重建 alias.json——修正已入库的脏数据。与 R1-R3 互补：R1-R3 治源头（下次 ingest 干净），R4 修存量。

### R5. 入库归一（已完成 L1）

`filter_turns_for_index` speaker→canonical——数据层统一。

## 3. 链路对照（重构后）

```
原文 → NER（+置信度过滤）→ 候选分组（不预合并）→ LLM 簇归一 v2（拆并+碎片过滤+全名）
     → 等价校验 → InventoryCharacter → build_llm_seed
     → 归因（harvest + volume_seed + 入库归一 speaker→canonical）
     → 存量 alias.json 由变体级归并重建
```

## 4. 验收标准（用户定义：准确、零噪声）

| 指标 | 目标 |
|---|---|
| 碎片/占位名 | **0**（来水管/女生A/主角 类不出现在 inventory） |
| 错并 | **0**（温水≠温水佳树；共享姓角色全部独立） |
| canonical 全名率 | top20 角色 100% 全名（凯金正/利姆路・坦派斯特） |
| 等价校验 | E1/E2/E3 errors = 0 |
| 50 条抽检 speaker 正确率 | ≥90%（当前 100%） |

## 5. 实施分层

| 层 | 内容 | 验证 |
|----|------|------|
| R1 | NER 置信度：Mention.score + softmax + min_conf 过滤 | 单测（分数/过滤）+ 真实 mention 分布对比 |
| R2 | 聚类去无条件合并：精确+编辑距离分组 | 单测（温水/温水佳树 分簇）+ 回归 |
| R3 | _SYSTEM v2 + 归一后处理校验 | 单测（拆并/碎片/全名/校验回退）+ 真实一卷 ingest 对比 |
| R4 | 存量 alias 重建（败犬+史莱姆全量归并→复核→落盘） | 校验 errors=0 + 用户复核 |
| R5 | （已完成）入库归一 | 已通过 |

每层独立验证合入，一层稳了再进下一层（L1→L4 渐进原则）。

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 聚类变细 → LLM 输入簇数↑ | max_clusters_for_llm 上限 + surface count 排序 |
| NER min_conf 误杀真名 | 阈值实测校准（0.5 起步）；白名单补召回（inventory 已知名） |
| LLM 拆并判断偶错 | 等价校验拦截 + 用户复核（R4 落盘前） |
| LLM 归一批失败 | 回退规则 fallback（_fallback_from_clusters 保留） |

## 7. 明确不做

- 不换 NER 模型（CLUENER 保留，置信度已能解决大头；LLM NER 成本高，留作后续选项）
- 不做在线 coreference（跨句指代解析——超范围）
- 不删 `_fallback_from_clusters`（LLM 失败兜底）

## 8. 过期资产

| 资产 | 处置 |
|---|---|
| 子串无条件合并逻辑 | R2 替换为候选分组（git 可回滚） |
| 旧 `_SYSTEM` | R3 升级 v2 |
| 旧 alias.json（70 独立实体） | R4 重建覆盖 |
