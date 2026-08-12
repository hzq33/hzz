# 系统功能需求基线（REQUIREMENTS）

> 定位：**测试的需求源**。本文档从用户可感知的功能行为出发（做什么、验收什么），
> 不描述实现细节（怎么做）。每条需求有稳定 ID，测试按 ID 引用。
> 同步日期：2026-08-10 | 来源：README、docs/ARCHITECTURE、AGENT_FLOW、AGENT_TOOLS_DESIGN、
> NOVEL_RAG_DESIGN、GRAPH_RAG_DESIGN、CHARACTER_ON_DEMAND_DESIGN、CHARACTER_INVENTORY_DESIGN、
> DIALOGUE_CHAPTER_EXTRACT_DESIGN、NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN、openapi.json、config.yaml

## 需求域总览

| 域 | 前缀 | 主题 |
|----|------|------|
| A | 平台基础 | 健康探针 / 鉴权 / CORS / 限流 / 指标 / 会话历史 / LLM 配置 / 上传安全 |
| B | 通用对话 | chat / stream / 工具 / 会话预算 / HITL |
| C | 小说导入 | 上传 / 分章 / 入库 / 书目管理 / 重抽 |
| D | 检索 | 四通道 / hybrid / rerank / Parent 展开 / 过滤 / 注入防护 |
| E | 角色管线 | 盘点 / 聚类 / 别名 / 建卡 / 图谱 / 合并 |
| F | 对话抽取 | 按章抽取 / 配额 / 说话人归因 / 噪声过滤 / 降级 |
| G | 扮演 | impersonation chat / 会话 / 引用 / 口吻 |
| H | 世界体系 | 剧情分析 / 时间线 / 设定书 / 关系图 / GraphRAG |
| I | 任务与存储 | Job / 会话存储 / 启动维护 |
| J | 安全 | execute_code / 文件沙箱 / 注入防护 / 上传 / SSE |

测试文件映射：`tests/requirements/test_A_*.py` … `test_J_*.py`，文件头部声明覆盖的需求 ID。

---

## A 平台基础

### A-01 健康探针
- `GET /api/v1/agent/health/live` 公开（无需 Bearer），返回 200，`status == "ok"`。
- `GET /api/v1/agent/health/ready` 公开；就绪时 200 且 `ready == true`；
  未配置 API Token / 关键配置缺失时返回 503（fail-closed）。
- `GET /api/v1/agent/health` 返回聚合健康信息。

### A-02 鉴权（Bearer，fail-closed）
- 受保护端点（chat / novels / characters / impersonation / history / tools 等）必须带有效 `Authorization: Bearer <AGENT_API_TOKEN>`。
- 缺失 / 错误 token → 401，且不泄漏内部错误细节。
- 未设置 `AGENT_API_TOKEN` 时：非 public 端点返回 503（服务未配置），而不是降级为开放。
- 弱 / 占位 token（如 `changeme`、过短）拒绝启动（启动校验）。
- `/health/live`、`/health/ready`、`/metrics` 为 public。

### A-03 CORS
- `CORS_ORIGINS` 配置的来源被允许跨域；未列出的来源被拒绝（白名单，非 `*` 全放）。

### A-04 限流
- `AGENT_RATE_LIMIT_RPS` 生效：超限请求被拒绝（429 或等效），不进入业务处理。

### A-05 指标
- `GET /metrics` 返回 Prometheus 文本格式，包含 HTTP 请求计数 / 延迟 / 活跃会话等核心指标。

### A-06 会话历史
- `GET /api/v1/agent/history?session_id=...` 返回该会话的消息历史（用户 + 助手）。
- `DELETE /api/v1/agent/history?session_id=...` 清空该会话历史。
- 不存在的会话 ID 处理明确（不 500）。

### A-07 LLM 配置
- `GET /api/v1/agent/llm-config` 返回当前 LLM 服务商/模型配置（key 脱敏）。
- `PUT /api/v1/agent/llm-config` 更新配置。
- `POST /api/v1/agent/llm-config/test` 测试连接，返回成功/失败结果。

### A-08 上传安全
- 上传文件名被清洗（去除路径成分 / 危险字符）。
- 超过大小限制的上传被拒绝（413），不进入入库流程。

---

## B 通用对话

### B-01 chat 与 chat/stream
- `POST /api/v1/agent/chat`：`message` 必填（空串 / 缺失 → 422）；返回助手回复文本。
- `POST /api/v1/agent/chat/stream`：SSE 事件流；事件类型含 `phase` / `reply_chunk` / `done` / `error`；
  `done` 事件携带 usage / `cost_usd`。
- 未传 `session_id` 时自动生成；同一 `session_id` 的多次请求共享上下文（多轮）。
- `novel_scope` 可选：限定检索范围（系列/卷）。

### B-02 工具列表
- `GET /api/v1/agent/tools` 返回注册工具名列表，必须含 `web_search`、`novel_search`、`character_kb`、
  `story_analysis`、`novel_admin`、`file_operation`。

### B-03 会话预算
- 会话 token 预算（`AGENT_SESSION_TOKEN_BUDGET`）超限时，chat 以 `error(budget)` 结束，不继续消耗。

### B-04 工具调用与 HITL
- 默认走原生 tool calling（`AGENT_USE_PLANNER=1` 时回退 Plan→Execute→Reply）。
- 高风险工具（`novel_admin` 写操作、`character_kb` build/merge/update、`story_analysis` build、
  `execute_code`、`file_operation` write）在 `AGENT_TOOL_HITL=1` 时先发 `approval_required` SSE 事件。
- `POST /api/v1/agent/tools/approve`：批准后工具才执行；拒绝则不执行。
- `GET /api/v1/agent/tools/approvals/{id}` 可查询审批状态。

---

## C 小说导入

### C-01 上传与异步 Job
- `POST /api/v1/agent/upload` 接受 EPUB/TXT/MD 文件，启动异步入库 Job，返回 `job_id`。
- `GET /api/v1/agent/upload/jobs/{job_id}` 返回 Job 状态与进度（阶段 / 百分比 / 消息）。
- Job 状态机：running → done（成功）| failed（失败，含原因）；孤儿 Job 在重启时标记失败。

### C-02 入库管线行为
- 入库主路径：转换/分章 → Narrative Parent/Child 切块 → 角色盘点（Inventory）→ 对话抽取 →
  向量化 → 索引落库（LanceDB / FAISS 测试后端）。
- 入库完成后，书目列表出现该卷；`block_counts` / 章节数等元数据正确。
- 上传不默认批跑全角色人设 LLM、不默认生成全书 QA（成本约束）。

### C-03 书目管理
- `GET /api/v1/agent/novels` 列出书目（可按 `series_id` 过滤），含 `doc_id`、章节数、`needs_reindex` 标记。
- `PATCH /api/v1/agent/novels/series` 系列改名（HITL 写操作）；改名后书目/角色仍归属新系列名。
- `DELETE /api/v1/agent/novels/{doc_id}` 删除卷（HITL）；删除后检索不再命中该卷。
- 单卷书与多卷书在系列过滤下行为一致（无 `__vol` 后缀的 `doc_id == series_id` 也能被过滤检索）。

### C-04 对话重抽
- `POST /api/v1/agent/novels/{doc_id}/redialogue` 触发该卷对话重新抽取（可 `wait` 同步或异步 Job）。
- `GET /api/v1/agent/redialogue/jobs/{job_id}` 返回重抽 Job 状态。

---

## D 检索

### D-01 四通道
- 检索支持 narrative / dialogue / qa / character 四通道；意图路由输出 `primary_channel` + 权重。
- 各通道返回对应类型块：narrative=场景情节、dialogue=台词口吻、character=关系/事件索引。
- qa 通道默认关闭（`qa.enabled: false`），不参与检索。

### D-02 hybrid 检索
- `hybrid_search` = 向量 + 关键词 + RRF 融合；关闭时回退纯向量。
- 检索结果带 `hit_count`；零命中时明确标记 `zero_hit`。

### D-03 精排（rerank）
- `reranker_enabled: true` 时，有可用权重走模型精排，否则回退 keyword（CI 可用）。
- 精排后 top 结果排序稳定、相关块靠前。

### D-04 Narrative Child→Parent 展开
- 检索命中 Child 块时，举证展开到同章 Parent ± `expand_radius` 邻居，输出完整原文语境。
- 展开受 `max_expanded_chars` 与 `chapter_hard_boundary` 约束（不跨章）。
- 旧库无 `parent_id` 的块：命中即举证（展开 no-op，不报错）。

### D-05 过滤
- 元数据过滤：`doc_id` / 系列 / 角色 / 章节；Lance 原生 prefilter + 应用层 post-filter 兜底。
- 角色过滤使用 `all_person` 标注：中文人名被汉字包围时仍应命中（2 字名不漏标）。
- 删除卷后，该卷块不再出现在任何通道检索结果。

### D-06 图谱富集
- `graph_enrich` 开启时，检索结果附加角色关系上下文（`_graph_context`）。
- 图谱数据缺失/构建失败时检索不崩溃（优雅降级）。

### D-07 提示注入防护
- 检索/搜索结果包在 `<search_results>` 隔离标记中，系统提示声明内容不可信、不执行其中指令。

---

## E 角色管线

### E-01 角色盘点（Inventory）
- 盘点流程：粗召回（默认 LLM 扫描；CLUENER 可降级）→ 聚类 → LLM 归一 → Roster L1 + AliasMap。
- 输出候选角色列表：名称、频次、章节覆盖、共现、`status`（candidate/building/ready/low_evidence/failed）。
- 无 LLM 时降级：仅聚类草稿写入，标记 `llm_skipped`，不阻塞入库。

### E-02 聚类规则
- 等长错字（编辑距离 1）合并为同一簇（如 利姆路/利姆露）。
- 全名 vs 短名（长度差 ≥2，如 温水佳树/温水）不预合并，交给 LLM 归一阶段裁决。
- 噪声 mention（「他说」「来」等碎片/单字/无意义）被过滤，不进候选。
- 簇按提及数排序，`min_cluster_mentions` 以下不入选。

### E-03 LLM 归一校验（幻觉防御）
- LLM 输出的别名/正名必须能在原文中找到（含敬称变体如 利姆露大人→利姆露），否则丢弃。
- 职业/身份词（图书管理员、店员、史莱姆等黑名单）即使 LLM 输出也过滤。
- LLM 失败 / 输出不可解析时回退规则路径，不污染别名表。

### E-04 Seed 策略
- seed 候选 = 黑名单剔除 → `min_mentions` → 百分位门槛 → `top_k` 封顶；
  角色数过少时回退 Top-N（`small_n_fallback`）。
- 全量 candidates 落盘；`seed_names` / `in_llm_seed` 供对话归因先验。

### E-05 跨卷合并衰减
- 合并旧卷盘点时，旧记录提及数按 `merge_decay`（0.85）衰减后与本卷取 max，
  避免早期卷高频角色永久绑架阈值。

### E-06 按需建卡
- `POST /api/v1/agent/characters/build`（`names` 必填）启动建卡 Job；`wait=false` 异步、返回 job_id。
- Job 流程：正名/别名归一 → 证据收集（dialogue + narrative，按名集合检索）→ LLM 蒸馏 → 落盘 Card。
- 证据充足（`dialogue_hits >= min_dialogues`）正常出卡；证据不足 → `low_evidence` 标记，卡上提示样本偏少；
  零证据 → Job failed 或 low_evidence，不写误导性卡。
- `GET /api/v1/agent/characters/jobs/{job_id}` 返回 Job 状态；`GET .../jobs` 列出 Job。
- 人设正文只来自本库证据 + 蒸馏，外网（web_normalize）只补正名/别名，不写剧情。

### E-07 角色图谱
- 节点=角色、边=dialogue / co_occurrence / relation。
- 说话人先按系列 alias.json 归一（同一实体的不同称呼合并为一个节点，不分裂）。
- 噪声（职业词、「主角」、无意义高频词）不作为节点；自环被去重。
- `GET /api/v1/agent/characters/graph?series_id=` 返回图谱（节点+边）。

### E-08 角色名录与编辑
- `GET /api/v1/agent/characters?series_id=` 返回角色列表（含卡片信息）。
- `GET /api/v1/agent/characters/candidates?series_id=` 返回建卡候选（盘点输出）。
- `GET /api/v1/agent/characters/roster?series_id=` 与 `GET .../roster/series` 返回别名名录；`PUT .../roster` 可写。
- `POST /api/v1/agent/characters/merge` 合并角色（HITL）；`GET /merge-suggestions` 给出合并建议。
- `PUT /api/v1/agent/characters/{name}` 编辑角色（HITL）；`DELETE /api/v1/agent/characters/{name}` 删除。
- 跨系列同名角色互不污染（`series_id` 隔离）。

---

## F 对话抽取与归因

### F-01 按章 LLM 抽取
- 默认 `provider=cloud_chapter`：整章（≤`max_chunk_chars`）一次 LLM 抽取，同时产出台词与说话人。
- 超长章章内滑窗（`slide_win_chars` / `slide_stride_chars`），窗口带叙事上下文（不传纯台词）。
- 抽取结果去重后落为 DialogueTurn / NovelBlock，纳入 dialogue 通道。

### F-02 章过滤
- 无引号章（`require_quote_marks`）跳过；过短章（`min_chapter_chars`）跳过；简介/后记/制作信息标题跳过。
- 跳过行为可观测（meta：`chapters_skipped` / `skip_reasons`）。

### F-03 配额分层（够用即止）
- 按角色 importance 档位（main/supporting/extra）设定配额（50/40/10 默认）。
- 优先角色达标后提前停止后续抽取（`stop_when_priority_met`），成本与优先级成正比而非全书台词数。
- 只把达标策略内的 turn 写入 dialogue 向量。

### F-04 说话人归因
- 候选名 = Inventory seed（soft）+ 章内已解析名。
- 说话人判定带置信度：≥ `high_confidence_min` 直接采信，≥ `accept_min` 可接受，低于拒绝。
- 称谓（vocative）不当作说话人（`reject_vocative`）。
- 归因错误可经 `redialogue` 重抽修复（重抽带归因校验）。

### F-05 噪声与黑名单
- `is_noise_speaker`（「他说」「旁白」等）不入库。
- importance 黑名单（史莱姆/哥布林/人类等物种词）不参与 main/supporting 档位。
- 别名冲突按提及数裁决；无法裁决的冲突不落盘。

### F-06 降级路径
- 无 LLM / LLM 异常 / 输出不可解析：对话抽取回退（不产生对话块或走规则兜底），入库不失败。
- `NOVEL_DIALOGUE_ATTR_PROVIDER` 可覆盖 provider（含 `off`）。

---

## G 角色扮演（Impersonation）

### G-01 扮演对话
- `POST /api/v1/agent/impersonate/chat`：`character` 与 `message` 必填；返回扮演回复。
- `POST /api/v1/agent/impersonate/chat/stream`：SSE，事件含 `reply_chunk` / `done`（usage）。
- 同一会话多轮保持角色上下文；`doc_id` 可锁定检索卷。

### G-02 检索与口吻
- 扮演回复基于四通道检索（事实=narrative/character，口吻=dialogue）。
- 事实命中 Child 块时展开到同章 Parent 上下文（角色拿到大段原文）。
- `impersonation_full_chain` 关闭时降级为轻链路（单通道回退），不崩溃。

### G-03 会话管理
- `POST /impersonate/reset?session_id=` 重置会话（清上下文）。
- `POST /impersonate/regenerate` 重新生成上一条回复。
- `GET /impersonate/sessions` 列出会话（可重命名 `PATCH` / 删除 `DELETE`）。
- `GET /impersonate/history?session_id=` 返回扮演会话历史。

### G-04 出处引用
- 扮演回复可携带出处（事实/口吻拆分），引用带章节/原文证据（citation/fact 与 style 分离）。

---

## H 世界体系

### H-01 剧情分析
- `POST /api/v1/agent/story-analysis/build`（`series_id` 必填）异步构建：时间线 / 设定书 / 关系，
  map/reduce，Job 带进度；`wait=true` 可同步。
- 默认不抽伏笔（`extract_foreshadows` 默认 false）；`max_chapters` 可覆盖。
- `GET /api/v1/agent/story-analysis?series_id=` 返回分析结果；`GET .../jobs/{id}` 返回构建 Job 状态。

### H-02 时间线与设定书
- `GET /api/v1/agent/timeline?series_id=` 返回时间线事件（带原文证据与章节引用）。
- `GET /api/v1/agent/lorebook?series_id=` 返回设定书条目。

### H-03 关系图谱（世界体系侧）
- 关系快照含类型 / 极性 / 置信度 / 证据 / 章节；供前端图谱页与时间过滤。

### H-04 GraphRAG 全局问答
- 社区发现：角色图按边权重模块度划分社区；孤立节点自成一社区。
- 社区摘要：LLM 按社区生成（成员/核心关系/主题/关键事件，附章节证据）；失败回退规则摘要。
- `GET /api/v1/agent/rag-global?series_id=&query=` 返回全局上下文（query 匹配社区摘要，无额外 LLM）。
- `POST /api/v1/agent/rag-global/build` 强制重建；story-analysis build 后联动构建。
- 全局问答含跨社区主线概述（社区数 >1 时生成）。

### H-05 全局意图路由
- 查询含 主线/主题/讲了什么/核心人物/整体/世界观/结局/故事梗概/关系网 等 → 走全局层；
  实体/事件/对话查询 → 本地多通道检索。

---

## I 任务与存储

### I-01 Job 存储与生命周期
- Job 存储默认 SQLite WAL（可回退 JSON）；角色建卡 / 剧情 / 上传 / 重抽 Job 统一管理。
- Job 状态：running → done | failed | cancelled；done 携带结果摘要。
- 过期 Job（`AGENT_JOB_TTL_HOURS`）启动时清理；重启时 running 孤儿标记 failed（`orphan_after_restart`）。
- Job 查询对不存在 ID 返回明确 404，不 500。

### I-02 会话存储
- 会话默认 SQLite WAL（chat / imp 分 namespace）；可回退 JSON。
- 超过 `AGENT_SESSION_KEEP` 的旧会话在启动时清理。

### I-03 会话并发
- 同一会话并发请求被串行化（per-session lock），不交错污染上下文。

---

## J 安全

### J-01 execute_code
- `EXECUTE_CODE_ENABLED` 未设置 → 工具默认禁用（不注册 / 调用即拒绝）。
- 启用时：子进程 `python -I` + 超时 / CPU / 内存限制 + HITL 审批。
- AST 级 import 拦截：`import os` 及其变体（制表符等）被阻止。

### J-02 文件沙箱
- `file_operation` 的 read/write/list 限制在工作区；`../` 前缀逃逸被拒绝（resolve + relative_to）。
- write 需 HITL。

### J-03 提示注入防护
- 外部内容（检索结果 / 网页）用 `<search_results>` 隔离并声明不可信，系统提示不得执行其中指令。

### J-04 上传与解析安全
- 上传文件名清洗；超大文件 413；扩展名白名单（EPUB/TXT/MD）。
- EPUB XML 解析走 defusedxml（XXE 防护）。

### J-05 SSE 与日志脱敏
- SSE 错误事件不泄漏内部堆栈 / 密钥（脱敏后返回）。
- 结构化日志含 `request_id`；API key 不出现在响应与日志中。
