# QA 通道修复 + 说话人校正 + 数据管线冒烟验证总结

> 日期：2026-08-12 · 关联：[2026-08_CONTEXT_COMPACTION_IMPLEMENTATION.md](2026-08_CONTEXT_COMPACTION_IMPLEMENTATION.md)
> 验证数据：《败犬女主太多了！01》epub（雨森たきび）

## 背景

此前评估报告（2026-08_OOC_HALLUCINATION_TEST_REPORT）发现的 **P1 问题——qa 通道零命中**（4/4 全零）一直未修。数据清空重建后，本次以《败犬女主太多了！01》为真实样本，走完整数据管线冒烟测试，期间连带修复 4 个阻塞 bug，并引入说话人 LLM 校正环节治理碎片角色名。

## 一、QA 通道修复

| 项 | 内容 |
|----|------|
| 根因 | `config.yaml` 中 `novel_rag.qa.enabled: false`（QA 生成被配置关闭）+ 上传 API/job 默认 `generate_qa=False` |
| 修复 | ① config 开启 `qa.enabled: true` ② `src/api/routers/novels.py` ③ `src/application/jobs/handlers.py` 默认值改 True |
| 验证 | 败犬数据产出 **qa=60 块**（此前 qa=0） |

## 二、说话人 LLM 校正（碎片角色名治理）

### 问题
对话抽取（dialogue_attribution）对生僻名/日语原名归因偶发产出截断碎片：`微微一`/`佳树皮`/`奈见别`/`会被人`/`么会知`/`时朝我`/`树不知`/`女生A` 等，污染 roster → 建卡 → 扮演检索全链路。

### 方案对比（真实数据验证）
| 维度 | 规则清洗（is_noise_speaker 增强） | **LLM 校正（选定）** |
|------|----------------------------------|---------------------|
| 碎片识别 | 只认精确匹配权威名单 | 语义判断（"温水"→温水和彦） |
| 别名归一 | ❌ 误删合法别名（温水/志喜屋/月之木学姐） | ✅ 5 个别名正确归一 |
| 召回 | 8/15（53%） | 15/15（100%） |
| 维护 | 规则越堆越多 | 一次调用，随数据自适应 |

### 实现
- 新增 `src/application/novel/dialogue_pipeline/speaker_correct.py`：
  - `correct_speakers()`：LLM 批量输出 raw→target 映射（权威名 / "noise" / 原样）
  - `apply_speaker_mapping()`：应用映射；noise 标记为"未知"由上层噪声过滤
- 接入 `extract.py` `_extract_chapter_first` 返回前（blocks 生成后）：
  - 权威名单 = volume_seed（inventory）→ 兜底对话内高频说话人
  - 配置开关 `dialogue_attribution.llm_correct_speakers`（默认 true），失败静默降级

### 验证
181 处说话人修正、72 处标记噪声；校正后对话块说话人**零碎片**。

## 三、数据管线冒烟（完整链路）

《败犬女主太多了！01》epub 全流程：上传 → 章节解析 → 对话抽取 → 说话人校正 → QA 生成 → 角色盘点 → 向量索引 → roster → catalog

**结果**：998 块（narrative 915 / dialogue 11 / qa 60 / character 12），15 个干净角色，doc_id 正确（`败犬女主太多了__vol01`）。

## 四、连带修复的 4 个 Bug

| # | Bug | 根因 | 修复 |
|---|-----|------|------|
| 1 | **doc_id 脏名**（`败犬女主太多了__01__雨森たきひ____Z-Library__1`） | `_sanitize_upload_filename` 把全角 `！` 替换为 `_`，导致卷号推断正则失效、系列名清理失败 | ① sanitize 保留 `！` ② `_infer_volume_no_impl` 正则兼容 `_` 分隔 ③ `_clean_series_id_impl` 增加 `__NN__` 形态清理（`src/application/novel/ingest/convert.py` + `novels.py`） |
| 2 | **force_reindex 失效**（重传仍 dedup 跳过） | **FastAPI 0.139 框架 bug**：multipart 请求中 Query 声明的字段收不到（str/bool 均 None），bool 恒为默认值 | `upload_novel` 改为 str 接收 + `request.query_params`/`request.form()` 手动合并取值，兼容 Query 与 multipart 双路径 |
| 3 | **job handler 漏传 force_reindex** | payload 有该字段但 ingest 调用漏参 | `src/application/jobs/handlers.py` 补传 |
| 4 | **roster 碎片残留**（30 条含 15 碎片） | `persist_inventory_roster` 的"保留旧条目"逻辑原样保留 NER 失败时 fallback 进来的碎片 | `src/domain/novel/character_inventory/roster.py`：孤儿清理（不在新名单且非别名→移除，有卡保护）+ 别名条目收敛合并 |

## 五、最终数据状态

```
series: 败犬女主太多了 | doc_id: 败犬女主太多了__vol01
blocks: narrative=915 dialogue=11 qa=60 character=12 total=998
roster: 15 条（全部正确，零碎片）
角色: 八奈见杏菜/小鞠知花/烧盐柠檬/温水和彦/温水佳树/月之木古都/袴田草介/
      绫野光希/甘夏古奈美/姬宫华恋/志喜屋梦子/玉木慎太郎/朝云千早/小抜小夜/放虎原云雀
inventory aliases: 温水→温水和彦、志喜屋→志喜屋梦子、月之木学姐→月之木古都 等正确
```

## 六、验证

- **162 单测全过**（A 平台上传测试 23 个含新增兼容均通过）
- 真实服务端到端：上传（force_reindex 传递正确）→ 管线 → 数据正确入库
- 文档：本总结 + [2026-08_CONTEXT_COMPACTION_IMPLEMENTATION.md](2026-08_CONTEXT_COMPACTION_IMPLEMENTATION.md)

## 七、遗留/后续

1. **Docker 构建验证**：未做（下一步建议项）
2. **数据管线生产验证**：仅验证了《败犬女主太多了！01》单卷；多卷/其他格式（md/txt）建议各跑一本
3. **角色卡管线**：roster 已干净，可继续建卡（`POST /characters/build`），观察扮演检索质量
4. **FastAPI 0.139 multipart bug**：已在 upload 接口绕过；若项目其他端点也有 multipart+Query bool 组合需排查（当前无已知实例）
