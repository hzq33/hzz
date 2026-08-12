# 小说知识库世界体系构建 — 工作总结报告

> 日期：2026-08-09 | 项目：d:/tools/agent（ModularAgent）
> 目标：从小说文本构建完整世界体系（角色/时间线/设定书），驱动高质量角色扮演

---

## 一、任务背景与目标

本项目为轻小说角色扮演系统，已有 RAG 检索、角色卡、对话抽取等基础能力。本次工作的核心目标：

1. **测试并接入 NVIDIA Free Endpoint** 作为免费 LLM 服务商
2. **重构角色实体提取**（V4 实体本体），产出干净角色名单
3. **构建故事内时间线**（编年体 chronicle），支撑角色发展路线
4. **生成时间感知设定书**（Temporal Lorebook，对标酒馆 WI 但带时间维度）
5. **前端整体重做**（简洁模块化），展示世界体系

---

## 二、主要成果

### 1. NVIDIA Free Endpoint 接入与验证

| 项目 | 结果 |
|------|------|
| API Key 有效性 | ✅ 100 个免费模型可访问 |
| 对话调用 | ✅ `deepseek-ai/deepseek-v4-flash-0731` 可用 |
| Embedding | ❌ 账号无权限（404） |
| 关键限制 | 响应时间与 payload 强相关（16KB→83s，62KB→170s 接近断连）；`deepseek-v4-pro` 已 EOL（410） |

**适配优化**：
- 归一/粗扫 `max_tokens` 8192→4096（免费端点按 max_tokens 预留资源，过大排队断连）
- 归一 evidence 8条×120字 → 4条×80字（payload 62KB→16KB，响应稳定）
- 粗扫按章分批 60000→30000 字符 + 4 并发（实测 4 并发无 429，提速明显）
- 归一带 3 次重试（免费端点偶发 Connection error）
- 顺带修复：httpx 读 Windows 系统代理导致 DeepSeek 官方 API 连接失败（`trust_env=False` 直连）

### 2. 角色实体提取 V4（实体本体 + 属性挂接）

**核心设计**：以"能否作为关系三元组端点"为唯一实体判定标准。

- **核心实体（2 类）**：person（人物）、speaking_skill（会说话的技能）
- **属性支撑类（7 类）**：role/race/title/location/org/skill_attr/item（**全部挂接实体，绝不独立成节点**）

**改造内容**：
- 粗扫提示词重写：实体/属性两段式 JSON 输出，属性挂接实体
- 归一提示词职责收窄：合并/拆分/属性校验（不再处理删泛称——源头已消除）
- 代码层一致性兜底：同簇不得既在 characters 又在 dropped（修复 c2 一簇多用 + mention 虚增）
- InventoryCharacter/candidates 增加 `attributes` 字段

**验证效果**（史莱姆 vol01）：
- 属性挂接精准：凯金=矮人/武器锻造师、盖札=矮人王/英雄王、兰加=牙狼族首领/坐骑
- 名单干净：主篇 16 候选零噪声（败犬 5 女主全对）
- 角色名单从"属性类混入 37 个" → 源头消除

### 3. 编年体时间线（Chronicle Timeline，V5 P1）

**设计**：像史书编年体——一条按事件发生顺序排列的时间线（不按年份分桶）。

- `story_time` 结构化字段：year/period/label/relative/confidence（LLM 语义推断）
- 排序双键：year → 章节序兜底；转生前设负 year（-1）保证排最前
- reduce 跨卷归并 + 实体名归一（canonical/别名对齐）
- 产出 `data/timelines/{series}.json`：chronicle（事件序列）+ by_character（角色索引）+ by_era（时段分段）
- 弱实体过滤加强：后记/作者/插画师/网络版 等场景事件剔除

**验证**（短篇 14 事件、主篇 29 事件、败犬 33 事件）：
- 时间标注准确（period=建国后/转生前/魔王时期，17/29 有 year）
- 跨卷排序正确（转生前→转生后→魔王时期）

### 4. 时间感知 Lorebook（V5 P2）

**设计**：酒馆 World Info 的时间升级版——"关键词 + 时间窗口 → 该时段的设定文本"。

- 实体条目按时段分段（利姆露@转生后/建国后/大战后 各一条）
- 关系条目按事件生成（同事件只 1 条，避免重复）
- 每条带 keys（canonical+别名）/time_range（era）/priority/content
- 自动落盘 `data/lorebooks/{series}.json`（save_analysis 时同步生成）

### 5. 扮演注入（V5 P3）

- `_lorebook.py` 激活器：关键词命中 + 当前故事时间窗口 → 注入 system prompt
- `current_time` 推断：从用户消息+历史检测 era（长词优先，如"建国后"不被"建国"抢先）
- 注入格式带 era 标签（`[建国后] 利姆露：...`），LLM 能区分时间段
- 修复 `card.series_id` 为空导致 Lorebook 加载 0 条（从 doc_id 推断系列回写）

**真实扮演验证**（利姆露 4 轮对话）：
- "建国后都忙什么" → 仅注入建国后条目 → 回复私塾/朱菜细节 ✅
- "刚转生时的事" → 仅注入转生后 → 回复被刺瞬间 ✅
- 内容与注入时段一致，时间感知生效

### 6. 对话归因质量修复

- 发现 Qwen3-8B（硅基流动）归因能力不足：败犬口语对话 speaker 崩溃（"未知"690、"怎么"112）
- `dialogue_extract` 切换 DeepSeek flash → **unknown=0、speaker 全对**（4 女主各 50）
- redialogue 脚本加 `--write-back` 参数（一步重抽+写回 LanceDB）

### 7. epub 解析优化

- 章节标题提取：支持正文首段标题启发式（败犬"~一败目~xxx"无 title/h1 标记）
- 元数据章过滤：制作信息/简介/彩页/角色介绍等剔除（`_is_metadata_chapter`）
- 章节结构 LLM 超时 25s→120s + 失败重试

### 8. 前端整体重做

**WorldPage**（新页面 `/world`）：
- **时间线视图**：事件按故事时间排列、era 时段过滤、★关键事件标记、角色 chips、点角色看时间线轨迹弹层
- **设定书视图**：实体/关系条目卡片、kind 筛选 + 关键词搜索、keys/时间窗/优先级展示
- 系列选择器 + 双 Tab，简洁模块化（单文件内聚，方便后续改）

后端新增：`GET /api/v1/agent/timeline`、`GET /api/v1/agent/lorebook`

---

## 三、最终数据产出

| 系列 | 角色候选 | 时间线事件 | Lorebook 条目 | 状态 |
|------|---------|-----------|--------------|------|
| 史莱姆 主篇 | 16 | 29 | 30（10 实体+20 关系）| ✅ 完整 |
| 史莱姆 短篇 | 17 | 14 | 26（14 实体+12 关系）| ✅ 完整 |
| 败犬女主 vol01 | 16 | 33 | 36（8 实体+28 关系）| ✅ 完整（含角色卡+扮演验证）|

---

## 四、遗留问题与建议

| 问题 | 影响 | 建议 |
|------|------|------|
| NVIDIA 免费端点响应不稳定 | 粗扫/归一偶发超时需重试 | 生产建议用 DeepSeek 官方；免费端点用于验证 |
| 败犬主角等短名未对齐 canonical | by_character 用"主角/华恋"短名 | story_analysis 实体名与 inventory 别名映射加强 |
| 属性未按时段细分 | Lorebook 实体条目属性是全集 | P3 世界体系：按 year 分桶 attributes |
| 章节结构解析仍可能错乱 | 特殊 epub 标题 | 已加启发式，复杂场景可再迭代 |
| dialogue_meta 不自动写回 LanceDB | --apply 后需手动 write_back | 已加 `--write-back` 参数解决 |

---

## 五、关键文件清单

**新增**：
- `src/domain/novel/story_analysis/timeline.py`（编年体时间线）
- `src/domain/novel/story_analysis/lorebook.py`（时间感知设定书）
- `src/core/impersonation/_lorebook.py`（扮演注入激活器）
- `frontend/src/pages/WorldPage.tsx`（世界体系页面）
- `data/timelines/*.json`、`data/lorebooks/*.json`（数据产出）

**主要修改**：
- `src/domain/novel/character_inventory/llm_ner.py`（粗扫提示词 V4）
- `src/domain/novel/character_inventory/builder.py`（归一 V4 + 重试 + 冲突兜底）
- `src/domain/novel/character_ner.py`（DraftCluster.attributes）
- `src/domain/novel/character_inventory/models.py`（attributes 字段）
- `src/domain/novel/character_inventory/candidates.py`（落盘 attributes）
- `src/domain/novel/story_analysis/{models,config,map_reduce,reduce}.py`（story_time）
- `src/application/novel/ingest/convert.py`（epub 标题+元数据过滤）
- `src/application/novel/ingest/structure.py`（超时+重试）
- `src/application/novel/ingest/blocks.py`（超时、max_tokens、concurrency）
- `src/shared/llm.py`（代理直连 trust_env=False）
- `src/api/routers/novels.py`（timeline/lorebook API）
- `src/core/impersonation_agent.py`（series_id 回写 + Lorebook 缓存）
- `src/core/impersonation/chat.py`（Lorebook 注入）
- `frontend/`（WorldPage、Sidebar、App 路由、types、api、constants）

**配置**：`config.yaml`（llm_batch_chars=30000、llm_concurrency=4、entity_filter 加强）、`data/llm_config.json`（character_inventory/dialogue_extract→DeepSeek、normalize→DeepSeek pro）
