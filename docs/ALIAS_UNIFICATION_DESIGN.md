# 角色名统一设计（LLM 别名归并 + 入库归一）

> ✅ **2026-08-10 状态更新：已实施**（`src/domain/novel/alias_sync.py`：
> `detect_canonical_renames` / `apply_canonical_rename` / `sync_alias_roster_save`）。
> 原 "Proposed（验证已通过，待认可后实施）" 状态已过时，本文档作为设计背景保留。


> 状态：**Proposed**（验证已通过，待认可后实施）| 日期：2026-08-05
> 相关：[LLM_HARVEST_CHARACTER_NAMES_DESIGN.md](./LLM_HARVEST_CHARACTER_NAMES_DESIGN.md) · [CHARACTER_INVENTORY_DESIGN.md](./CHARACTER_INVENTORY_DESIGN.md)

---

## 1. 背景与问题

抽检（败犬 vol05，50 条 speaker 100% 正确）暴露唯一缺陷：**角色名不统一**。

| # | 问题 | 证据 |
|---|------|------|
| 1 | 入库 turn.speaker 用 LLM 原始变体（未归一） | #39「朝云同学」vs #42「朝云千早」同角色两名字 |
| 2 | inventory N 元聚类别名错并 | `温水佳树 aliases=[佳树, 温水, 温温]`——温水/温温是**温水和彦**（主角）的别称，被错归给妹妹 |
| 3 | alias.json 劣质 | 70 个变体全部独立未合并；含 OCR 错误「小抜」 |
| 4 | canonical 多为简称 | 朝云（应朝云千早）、小鞠（应小鞠知花）、天爱星（应马剃天爱星） |

**关键领域约束**（用户确认）：**共享姓 ≠ 同一人**——温水和彦 ≠ 温水佳树。别名归并必须按指代判断，不能按字符串相似度。

## 2. 验证结论（真实 LLM 输出，2026-08-05）

LLM 别名归并探针（`scripts/dev/analysis/alias_merge_probe.py`，变体+原文例句 → 归并决策）：

| 小说 | 结果 |
|------|------|
| 败犬女主太多了（20 变体） | 9 组全对：温水和彦←[温水,温温]、温水佳树←[佳树] 严格分开；烧盐柠檬/马剃天爱星 全名补全 |
| 史莱姆（25 变体） | 11 组全对：利姆路・坦派斯←[利姆,利姆姆]、艾拉多・格利姆瓦多←[格利姆瓦多]、盖德←[魔王盖德] |

跨书泛化成立（校园现代文 + 异世界日式名/物种名/称号/昵称/中点/误写）。**LLM 做对正则/聚类做不到的领域判断**。

## 3. 方案（三层）

### A. 入库归一（数据层，必须）

`dialogue_quota/window.py:filter_turns_for_index`：

```python
# 现状：kept.append(raw)  ← speaker 保留 LLM 原始输出
# 改为：
kept.append({**raw, "speaker": canon})  # canon = tracker.resolve() 结果
```

效果：入库 turn.speaker 全部为 canonical；同一角色只有一个名字；检索/扮演按角色过滤不再漏。

### B. alias 体系重建（canonical 全名化）

1. **LLM 归并**（每系列 1 次调用，输入：inventory candidates + alias.json 全部变体 + 原文例句）→ `groups: [{canonical, variants, reason}]`
2. **canonical 原文校验（防 LLM 截断）**：LLM 单次输出可能截断全名（实测：输出「利姆路・坦派斯」，正确应为「利姆路・坦派斯特」，原文两者都出现）
   - 规则 1：canonical 必须在原文中出现（缺失 → 回退组内最长变体）
   - 规则 2：组内/原文存在更长完整形式时，canonical 取**最长形式**（坦派斯特 > 坦派斯）
   - 规则 3：**用户复核兜底**——归并结果落盘前必须人工过目（用户已两次纠正领域事实：温水≠温水佳树、哥布莉娜是个体名）
3. **结果落盘** `data/rosters/{series}.alias.json` 重建：canonical=全名，aliases=变体清单
4. **一对一等价校验**（规则层）：任一变体只能出现在一个 group；canonical 不重复——防"温水"同时映射两人
5. **与 inventory candidates 联动**：candidates 的 name/aliases 用归并结果修正（重建 inventory 或 overlay）；volume_seed 用 canonical
6. **黑名单兼容**：物种名（哥布林等）归并可保留，`importance_blacklist` 仍拦截其升档；**个体角色名（哥布莉娜/哥布达）不得进黑名单**（2026-08-05 已修正：3 处移除哥布莉娜）

### C. harvest 归一（源头优化）

`harvest.py` 输出名单 → 查 alias 表映射到 canonical（"温水"→"温水和彦"）后再进候选；alias 表查不到的保留原样（LLM 归因时 volume_seed 兜底）。

## 4. 配置

```yaml
dialogue_attribution:
  alias_merge:            # 新增（B 层）
    enabled: true
    max_variants: 30      # 单次归并调用上限（超量分批）
    rebuild_roster: true  # 归并结果写回 data/rosters/*.alias.json
```

## 5. 实施分层

| 层 | 内容 | 验证 |
|----|------|------|
| L1 | A 入库归一（window.py speaker→canonical）+ 单测（同一角色归一、canonical 冲突） | pytest 全绿 |
| L2 | B alias 重建：探针脚本固化（分批/断点）、败犬+史莱姆归并落盘、一对一等价校验 + 单测 | 归并 JSON 通过校验；alias.json 重建后无重复变体 |
| L3 | C harvest 归一：harvest 输出映射 canonical + 单测 | pytest 全绿 |
| L4 | 真实重抽败犬 vol05：样本 speaker 唯一性检查（同角色同名）+ 抽检 | 50 条样本角色名唯一率 100% |

## 6. 验证标准

1. L4 重抽后样本：任一角色只有一种名字写法（"朝云同学"消失）
2. 温水和彦/温水佳树 严格分离（L2 校验强制）
3. 全库 21 卷 alias.json 重建：0 重复变体、canonical 全名化比例（目标：top20 角色 100% 全名）

## 7. 明确不做

- 不改 N 元聚类算法本身（inventory 重建依赖归并结果 overlay，不重跑聚类）
- 不手动维护 alias（LLM 归并 + 人工复核兜底）
- 不合并共享姓的不同角色（硬约束）

## 8. 过期资产

| 资产 | 处置 |
|------|------|
| 现有 data/rosters/*.alias.json（70 独立实体） | 重建覆盖（git 可回滚） |
| inventory candidates 的错 aliases（温水→温水佳树） | overlay 修正；原始数据保留在 git |

## 9. 文档索引

| 文档 | 角色 |
|------|------|
| 本文 | 名字统一唯一说明 |
| LLM_HARVEST_CHARACTER_NAMES_DESIGN | 收割 LLM 化（候选源，本文 B/C 与其衔接） |
| CHARACTER_INVENTORY_DESIGN | inventory/seed 生产路径 |
