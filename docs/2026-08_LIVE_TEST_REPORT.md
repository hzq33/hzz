# 全功能实测分析报告（2026-08-10）

> 范围：后端 API（FastAPI :8080）+ 前端（Vite React :3001）全功能实测
> 方式：真实 DeepSeek API 调用 + Playwright 浏览器自动化
> 环境：Python 3.13.5 / Node v24.19.0 / 分支 `phase3/wave-a-governance` @ `71227b2`

---

## 1. 测试环境与方法

| 项 | 说明 |
|---|---|
| 服务 | uvicorn `agent_server:app`（127.0.0.1:8080）+ vite dev（:3001，代理注入 Bearer token） |
| 鉴权 | `.env` 中真实 `AGENT_API_TOKEN`（32 字符，非占位符） |
| LLM | DeepSeek `deepseek-v4-flash`（真实调用，消耗 API 额度） |
| 数据 | 库内 4 部小说（Re:从零前日传、史莱姆 vol01、史莱姆短篇、败犬女主）+ 测试上传《雾山客栈》《京都怪谈》 |
| 工具 | curl / Python urllib（UTF-8 文件中转避免管道编码问题）/ Playwright chromium 1.49 |
| 覆盖 | 41 个 API 端点、8 个前端页面、角色扮演/对话/SSE/上传/构建/评估等完整链路 |

> 注意：Windows 终端 GBK 显示 UTF-8 中文会乱码，所有结论均以 UTF-8 文件 + Python json 解析为准，排除显示层干扰。

---

## 2. 功能验证结果总览

| 模块 | 结果 |
|---|---|
| 鉴权/安全（401、弱 token 拒启、key 脱敏 `sk-***c3f9`） | ✅ 全部正确 |
| 健康检查（live/ready/metrics） | ✅ 全绿（token/配置/目录/session/job/lance/embed/reranker） |
| 通用对话（chat / chat-stream SSE / novel_scope） | ✅ 正常，流式 phase→plan→reply→done，含 token/费用统计 |
| 角色扮演（chat / stream / regenerate / reset / 会话 CRUD） | ✅ 正常，引用（dialogue+narrative）与风格样本均命中 |
| 小说上传/索引（txt/md、分块、目录） | ✅ 正常文档可索引（narrative/dialogue 分块正确） |
| redialogue 重抽对话、删除书目/角色 | ✅ 正常 |
| 角色名录（roster 查询/更新、candidates、merge 校验、卡编辑） | ✅ 正常，别名持久化生效 |
| 剧情分析（build map/reduce、timeline、lorebook、关系图谱） | ✅ 正常，事件/证据/时间线质量良好 |
| RAG 评估（rag-eval 统计/judge、llm-config 7 服务商） | ⚠️ 可用，但零命中率高（见 §4.6） |
| 前端 8 页面（加载、导航、角色扮演交互、SSE 流式渲染） | ✅ 全部通过，零 console/pageerror |
| 工具注册（web_search/file_operation/novel_search/character_kb/story_analysis/novel_admin） | ✅ 6 工具正常注册 |

---

## 3. 严重问题（高影响）

### 3.1 运行中上传新小说后，检索完全失效（须重启服务才恢复）

**现象**
- 上传新文档《京都怪谈》后，立即对该书做角色扮演（无论是否指定 `doc_id`），引用检索 **0 命中**；
- 重启前进程：上传《雾山客栈》后立即执行角色建卡（`POST /characters/build`）同样失败（`no_evidence`，evidence 0/0），重启后成功。

**根因（后续深挖已修正）**
- **核心是 2 字角色名在叙事块的 all_person 标注中永远匹配失败**（见 §3.1a）：
  - keyword 索引 `_by_char['晴明']` 只含 dialogue 块（对话说话人字段），narrative 块 `all_person` 为空；
  - impersonate 检索按 `character='晴明' AND channel='narrative'` AND 过滤 → 0 命中；
  - 3 字名（八奈见杏菜等）走子串匹配不受影响 → 表现为"旧书正常、新书全挂"。
- 次要因素：keyword 索引为进程内全局共享（`_KEYWORD_CACHE`），ingest 增量更新在部分场景未生效（雾山1 建卡失败即此场景），重启后重建恢复。

**复现步骤**
1. 启动服务（此时 keyword 索引按启动时库内数据构建）；
2. `POST /upload` 上传新书（job 显示 done）；
3. 立即 `POST /impersonate/chat` 或 `/characters/build` 针对新书角色；
4. 检索引用 0 命中 / 建卡报 `no_evidence`；
5. 重启服务 → 一切正常。

**根因（代码级定位）**
- 上传任务在 job runner 中执行，通过 `src/application/jobs/handlers.py::_novel_store()` **每次新建独立 store 实例**写入 LanceDB；
- API 常驻 store（`HierarchicalNovelStore`）的内存 **keyword 索引仅在启动/创建时构建一次**：`src/infrastructure/hierarchical_store.py::ensure_keyword_index()` 中 `if self._keywords.stats().get("total_ids", 0) > 0: return`——索引非空即跳过重建；
- ingest 流程（`src/application/novel/ingest/indexer.py`）只触发**文件级向量索引重建**（`ensure_vector_indices`，日志 `Vector index rebuilt after ingest`），**不重建/不通知常驻实例的内存 keyword 索引**；
- 日志佐证：
  - 启动时仅一次 `Keyword index rebuilt from store: 2825 blocks`；
  - 上传后仅 `Vector index rebuilt after ingest: {'vec_narrative': True, ...}`（keyword 无动作）；
  - 上传《雾山客栈2》时 `Dedup hit ... skipping` 表明 ingest 走的是独立实例。

**影响**
- 2 字名角色（中文人名常态）在新/旧书中叙事检索全部失效，角色扮演引用缺失；
- 属高频主路径缺陷（"上传即用"是知识库产品的核心预期）。

### 3.1a 根因：narrative all_person 2 字名匹配 bug

**位置**：`src/domain/novel/chunker.py::_match_known_persons`

```python
for n in short_names:  # 2-char names
    if any(n in long for long in long_names):
        continue
    for m in re.finditer(re.escape(n), text):
        before = text[m.start() - 1] if m.start() > 0 else ""
        after = text[m.end()] if m.end() < len(text) else ""
        if not (_is_cjk_char(before) or _is_cjk_char(after)):
            found.append(n)
            break
```

**问题**：要求 2 字名前后均非 CJK 字符才匹配。中文人名在句中几乎必然被汉字包围（如"青年阴阳师晴明提着灯笼"），导致 2 字名角色（晴明/狐儿/翠娘）的叙事块 all_person 永远为空。

**验证**：
- `_by_char['晴明']` 块类型 = `{'dialogue'}`（仅对话块）；
- `channel=narrative character=晴明` → 0 hits；`channel=dialogue` → 3 hits；去掉 character 过滤 → 5 hits；
- inventory（角色盘点）本身正确识别了晴明/狐儿（candidates=2）。

**建议修复（供参考，需确认后实施）**
1. 上传完成后对**常驻 store** 显式刷新：`ensure_keyword_index(force=True)` 或增量 `index_batch` 新块；
2. 或让 upload job 复用 `request.app.state.get_imp_store()` 的常驻实例写入；
3. LanceDB 向量索引重建后需重新 `open_table` 使常驻连接生效；
4. 至少应在 upload job 完成时打印/暴露"索引未同步，需重启"的提示。

---

## 4. 中等问题

### 4.1 重复内容上传被静默去重跳过，API 仍报成功

**现象**：上传与库内内容相同的文档（《雾山客栈2》=《雾山客栈》），API 返回 200 + job done + blocks 统计，但日志显示 `Dedup hit: content of 'test2.md' already indexed as 雾山客栈; skipping`，**块实际未写入**。

**影响**：前端书目出现该书（catalog 有条目），但检索不到任何块；用户完全无感知，误以为导入成功。

**建议**：去重命中时 job 返回明确状态（如 `state=skipped_duplicate` + 关联 doc_id），前端展示"内容与《雾山客栈》重复，已跳过"。

### 4.2 空文件上传被接受（200）后异步失败

**现象**：空文件上传返回 200 `{"state":"pending","progress":{"stage":"received","message":"已接收文件"}}`，随后 job 失败 `Conversion produced empty content`。

**影响**：用户上传空文件先看到"已接收"，后悄悄失败，体验差且无同步反馈。

**建议**：接收阶段校验文件非空（长度 > 0）直接 4xx 拒绝。

### 4.3 `/llm-config/test` 对不存在的 endpoint 返回 200 成功

**现象**：`POST /llm-config/test {"endpoint": "no_such"}` 返回 `200 {"ok":true,...}`，与真实 endpoint 结果无异。

**根因**：`src/shared/llm_config.py::get_endpoint_config` 对未知 key 仅 `logger.warning("Unknown llm endpoint ...; returning defaults")`，静默回退默认配置（deepseek-v4-flash）并测试"成功"。

**影响**：用户在设置页拼错调用点名称会看到"测试成功"的假象，误以为已配置到目标服务。

**建议**：未知 endpoint 应返回 404/400 与明确错误信息（fail-fast），或在前端用下拉框约束可选值。

---

## 5. 观察项（非阻塞）

### 5.1 未知角色扮演不报错
`POST /impersonate/chat {"character":"不存在的角色xyz"}` 返回 200 并生成"不确定你在跟谁打招呼"式回复（宽松降级）。若产品希望"仅扮演库内角色"，建议后端校验 roster/卡片存在，前端限制下拉选择。

### 5.2 RAG 检索零命中率偏高（~50%）
- 71 条评估 trace 中 36 条零命中（50.7%）：31 条为**全库查询**（doc_id 为空），多为反事实问题（如"利姆露回到原世界"——原文不存在该情节）或无关问题（"请自我介绍"），**部分属查询与内容天然不匹配**，非纯检索 bug；
- 但 **qa 通道 blocks 全为 0**：`config.yaml` 中 `qa.enabled: true` 与实际状态矛盾（代码注释"qa 数据未提取，暂时屏蔽"，权重 `qa: 0.35` 被注释），所有 qa 通道查询必然零命中——配置与实现不一致，建议明确关闭 qa 通道或补齐 qa 数据提取；
- character 通道 7 条零命中值得后续抽样复核。

### 5.3 前端使用 hash 路由（`#/`）
所有导航为 `href="#/..."`（HashRouter）。功能正常，但 URL 不含语义路径，不利于分享/SEO；如无子路径部署需求可考虑 BrowserRouter。

### 5.4 ingest 后向量索引仅重建 narrative 通道
日志 `Vector index rebuilt after ingest: {'vec_narrative': True, 'vec_dialogue': False, ...}`——对话/QA/角色向量通道索引未随上传重建。虽对话块少时可全表扫描兜底，但 dialogue 向量检索质量存疑（与 §3.1 同源的索引同步问题）。

---

## 6. 测试期间产生的数据与状态（待处理）

- 上传：《雾山客栈》《京都怪谈》（另《雾山客栈2》被去重跳过）；
- 建卡：翠娘、沈从云（雾山客栈）、雾山客栈2 翠娘（重启后重建成功）；
- 会话：多个 impersonate 会话（含测试消息）；
- 服务：后端/前端仍运行中；`server.log`、`server2.log` 保留可查。

---

## 7. 修复优先级建议

| 优先级 | 问题 | 影响面 |
|---|---|---|
| P0 | §3.1 上传后检索失效（索引不同步） | 核心主路径，上传即用场景全挂 |
| P1 | §4.1 去重静默跳过无提示 | 数据一致性/用户误导 |
| P1 | §4.2 空文件未同步拒绝 | 体验/资源浪费 |
| P2 | §4.3 llm-config 未知 endpoint 假成功 | 配置误导 |
| P2 | §5.2 qa 配置与实现不一致 | 评估指标失真 |
| P3 | §5.1/§5.3/§5.4 观察项 | 体验/一致性打磨 |

## 8. 修复记录（2026-08-10 第二轮）

### 已实施并验证的修复

| # | 修复 | 文件 | 验证 |
|---|---|---|---|
| 1 | 2 字角色名 narrative all_person 匹配 bug（去掉"前后非 CJK"限制） | `src/domain/novel/chunker.py` | 白狼/晴明/阿茶等 2 字名角色检索正常；`tests/test_narrative_all_person.py` 同步更新 |
| 2 | CharacterGraph 支持空 character_blocks（节点回退自 dialogue 说话人 + narrative all_person） | `src/infrastructure/character_graph.py` | 图谱生成成功（雪山 3 节点 8 边、京都 2 节点 4 边） |
| 3 | `build_graph` import 路径错误（`src.domain.novel.query_parse` → `src.application.novel.query_parse`） | `src/application/novel/ingest/blocks.py` | 图谱构建不再报 ModuleNotFoundError |
| 4 | qa 开关统一（`qa.enabled: false` 与全库 0 块现状一致） | `config.yaml` | 配置一致性 |
| 5 | **扮演事实检索补 parent 上下文展开**（命中 child → 同章 parent ±邻居，角色能拿大段原文） | `src/core/impersonation/retrieval.py` | 败犬命中 child(140c) → 展开 2391c（3 parent 邻居） |
| 6 | **keyword 索引运行时强制重建**（`ensure_keyword_index(force=True)`；upload 完成后自动执行） | `src/infrastructure/hierarchical_store.py` + `src/application/jobs/handlers.py` | 上传《茶屋物语》后**立即**检索 5 命中，无需重启 |

### 认知修正（第三轮核实）

- **hierarchy 实际已生效**（败犬 155 parent+781 child，parent 平均 716c / child 142c）；此前"granularity 全空"系查询了不存在的列（granularity 存于 style_tags_json 内）
- **parent 也有向量**（441/441），并非"只有子向量"；但 `index_parents: false` 使 parent 的 `vec_text_narrative` 为空
- **问题 B（LanceDB IVF_PQ 连接）实际不存在**：上传后立即检索 narrative 命中，证明 vector 路运行时正常；此前"运行时 0 命中"系 keyword 路（character/series 过滤依赖内存 kw）所致，已被修复 6 解决
- **扮演链路的真正缺口是"不展开"**：此前 `search_raw` 直接返回命中块碎片（~200c），现已在注入文本中展开到 parent 上下文

### 遗留问题（未修/需决策）

- 30 个既有单测失败（epub 依赖缺失 / 数据目录污染 / LLM mock 环境 / `test_retrieval_scope` 既有断言问题）——改动前后一致，非本次引入
- `test_retrieval_scope.py::test_resolve_injects_doc_ids_via_derivation` 需单独排查
- qa 通道/character 人设块仍为 0（generate_qa / generate_character_llm 默认关，属产品开关决策）
- 测试期间产生的数据：《竹林居》《溪边居》《青石居》《茶屋物语》《海港逸事》《雪山谜案》《京都怪谈》《雾山客栈》+ 多角色卡 + 会话

### 第三轮补充修复（2026-08-10 能力验证中发现的深层问题）

| # | 问题 | 根因 | 修复 | 验证 |
|---|---|---|---|---|
| 7 | **series 过滤对无卷后缀书失效**（vector 路恒零召回） | `lance_filters.py` SQL 层 + `novel_store._block_matches_filters` post-filter 层均写死 `doc_id LIKE '{series}__vol%'`，doc_id==series_id 的单卷书永不匹配 | 两处兼容 `doc_id = '{series}' OR LIKE '{series}__vol%'` | 竹林居/京都/溪边居重启后检索恢复正常；败犬（有 `__vol`）不受影响 |
| 8 | **运行时上传后新书检索失效**（kw 强制重建仍 0） | 非 kw 问题（日志证实 kw 实例同 id、total 正确）；根因是 **LanceDB IVF_PQ 重建后旧连接（Table 句柄）读不到新行**——get_blocks/向量搜索用旧索引 | **dirty 标志根治**：upload 完成 → `api_state.store_dirty=True` → 下次检索重建 store（新 LanceDB 连接 + 已重建的共享 kw） | 青石居上传后**立即**检索 5 命中（无需重启） |

---

*报告生成：2026-08-10 · 全功能实测（真实 LLM 调用）*
