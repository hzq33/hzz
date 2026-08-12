# Modular Agent Framework 代码质量与技术债评估报告

> 评估日期：2026-08-05
> 评估范围：全量扫描（src/ 122 文件 / tests/ 69 文件 / frontend / 工程配置）
> 评估方式：静态检查（ruff 0.16.1）+ 全量测试（pytest 8.x）+ 源码走读
> 严重级别：**P0** 必须修（正确性/安全） · **P1** 应当修（稳定性/维护性） · **P2** 建议修（风格/整洁）

---

## 1. 总体结论

项目**工程化水平在同规模项目中属于中上**：CI gate 静态检查全过、测试 466 通过、无裸 `except`、无 TODO/FIXME 残留、`.env` 已正确 ignore。但存在 **3 个测试失败**、**1 个架构级 LLM 调用隐患**、**若干大函数/大文件** 与 **API 层测试空白**，另有 1101 个风格级 lint 问题（833 个可自动修复）。

**健康度评分（本报告口径）：**

| 维度 | 评级 | 依据 |
|---|---|---|
| 测试健康度 | ⚠️ 良 | 466 passed / 3 failed / 4 skipped |
| 静态检查 | ✅ 优 | CI gate（E9/F63/F7/F82）全过 |
| 代码卫生 | ✅ 优 | 0 裸 except、0 TODO、`.env` 已 ignore |
| 架构清晰度 | ⚠️ 良 | 分层明确，但 ingest.py 1845 行 / ingest_novel 789 行 |
| 测试覆盖完整性 | ⚠️ 良 | 核心域覆盖好，但 API routers ~1300 行零直接测试 |
| 依赖管理 | ⚠️ 良 | requirements.txt 无 pin，lock 文件存在 |

---

## 2. 真实运行结果

### 2.1 pytest 全套（后台运行 326s）

```
466 passed, 3 failed, 4 skipped, 2 warnings in 326.22s (0:05:26)
```

跳过项（合理）：`test_ragas_deepeval_optional.py` ×3（需 `EVAL_LLM_JUDGE=1`）、`test_execute_code_cpu_limit_unix`（Windows 无 RLIMIT）。

### 2.2 ruff 静态检查

| 规则集 | 结果 |
|---|---|
| CI gate（E9/F63/F7/F82 — 运行时错误/未定义名） | ✅ All checks passed |
| 全面规则集（E/F/W/I/UP/B/SIM/C4） | ❌ 1101 errors（833 可自动修复） |

### 2.3 规模统计

- src/ 共 **33,292 行**；tests/ 共 **13,078 行**；frontend/src 共 **7,077 行**
- 最大文件：`ingest.py` **1845 行**、`story_analysis.py` 1372 行、`impersonation_agent.py` 951 行

---

## 3. 问题清单

### P0 — 必须修

#### P0-1：LLM 统一入口未注入 `thinking: disabled`，主对话路径有"空输出"隐患

**位置**：`src/shared/llm.py`（`chat`/`achat`/`achat_stream`/`achat_result` 均无默认 `extra_body`）；`src/core/agent.py:210,318`；`src/core/impersonation_agent.py:356`；`src/core/planner.py:183,190`；`src/domain/novel/character_builder.py:518,759`；`character_inventory.py:446`；`story_analysis.py:684,687`；`speaker_attributor.py:102` 等

**根因**：DeepSeek v4-flash 默认启用 thinking 模式，会吞掉 `max_tokens` 导致输出为空（项目已有此经验，见 ingest.py:299 注释 "thinking disabled"）。但该约束只靠 **6 个调用点各自显式传** `extra_body={"thinking": {"type": "disabled"}}`（ingest.py:337、character_on_demand.py:551,576、character_unified.py:122、dialogue_llm.py:205、qa_generator.py:129），**统一封装层没有注入**，其余 10+ 处裸调——包括最核心的 `Agent` 主回复路径、`Planner`、`ImpersonationAgent` 最终回复。

**影响**：主对话/角色扮演/剧情分析路径在 thinking 模式下可能返回空串或截断输出，且行为不一致（部分路径禁用、部分不禁用）。这是**架构级的一致性缺陷**：约束散落在调用点，新增调用极易漏。

**改进建议**：
1. 在 `SharedLLMClient` 的 `_build_client` 或统一方法内，对 `deepseek*` 模型默认附加 `extra_body={"thinking": {"type": "disabled"}}`，调用点可显式覆盖；
2. 或在 `config.yaml` 增加 `llm.thinking: disabled` 全局开关，统一入口读取；
3. 删除调用点重复的 `extra_body`，收敛为单一职责。

#### P0-2：3 个测试失败，含 1 个"测试隔离失效"根因

##### 失败 A：`test_list_known_series_ids_includes_store_docs`（tests/test_api_helpers.py:48）

```
AssertionError: assert 'from_roster' in ['e2e_classroom', 'from_store',
'关于我转生变成史莱姆这档事', ...]
```

**根因**：`src/domain/novel/catalog.py:21` 的 `_CATALOG_DIR` 是**模块级硬编码路径**（`Path(__file__).resolve().parents[3] / "data" / "catalogs"`），测试 monkeypatch 了 `src.api.helpers._PROJECT_ROOT`，但 `list_known_series_ids` 内部走 `catalog.list_catalogs()` 时用的是 catalog 自己的硬编码目录 → **读到真实项目数据**（e2e_classroom / 史莱姆 等真实书目）。测试隔离失效 + 目录不可注入。

**影响**：该测试永远读真实数据而非 tmp 数据；`catalog.py` 无法在隔离环境测试；CI 结果依赖本机 `data/catalogs` 内容。

**改进建议**：`catalog.py` 增加可注入目录（如 `catalog.set_catalog_dir()` 或环境变量 `AGENT_CATALOG_DIR`），helpers 与测试统一走注入点；测试用 `monkeypatch` 注入 tmp 目录。

##### 失败 B：`test_lancedb_search_converts_distance_to_score`（tests/test_novel_store.py:239）

```
>       assert results
E       assert []
```

**根因**：`NovelVectorStore(backend="lancedb", lance_path=tmp)` 索引 3 个 sample_blocks 后，搜索 `"苏瑶坐在窗边"` 返回空。需进一步确认是 lancedb 0.34.0 的搜索行为变化（如 `metric` 默认 L2 与 embedding 维度不匹配、或新表 schema 迁移问题），还是 mock embedding 与查询向量维度不一致。**注意**：首次跑时带 `--timeout=120` 直接报 pytest-timeout 未安装，实际耗时集中在 LanceDB 索引/搜索。

**影响**：LanceDB 后端是生产默认（`backend: lancedb`），此测试失败意味着**默认存储路径的检索正确性没有绿灯保护**。

**改进建议**：单独复现（已确认可稳定复现）；核对 lancedb 0.34.0 changelog 与 `distance_to_similarity` 转换；检查 mock embedding 维度是否与 `_VEC_DIM=1024` 一致；修复后建议补一条"LanceDB 全链路索引→搜索"的集成用例。

##### 失败 C：`test_seed_file_has_at_least_50_cases`（tests/eval/test_rag_eval_harness.py:146）

```
AssertionError: assert 30 >= 80
```

**根因**：`tests/eval/rag_eval_seed.json` 实际只有 **30 条**，而测试断言 `>= 80`（门禁与数据不同步）。评估文档声称 seed=80，数据缩水到 30。

**影响**：RAG 评估门禁（offline quality gate）实际可用用例只有声明的 1/3，检索质量回归的检出能力下降。

**改进建议**：二选一——(a) 扩充 seed 到 ≥80（推荐，RAG 评估置信度更高）；(b) 若 30 条已是真实语料上限，把断言降到与实际数据一致并同步更新评估文档，避免"文档承诺 > 实际能力"。

### P1 — 应当修

#### P1-1：`ingest_novel()` 单函数 789 行（`src/application/novel/ingest.py:621-1410`）

**根因**：入库主流程把 预处理→结构解析→分块→角色盘点→对话抽取→QA 生成→索引 全部线性写在一个函数里（含 1 个内部函数 `_progress`），文件整体 1845 行。属于"逐渐追加"形成的**上帝函数**。

**影响**：任何阶段出问题都要在 800 行内定位；无法单独测试各阶段；`_progress` 回调贯穿全程，参数传递脆弱。

**改进建议**：按流水线阶段拆为独立函数/类（`parse → chunk → inventory → dialogue → qa → index`），每个阶段返回中间结果（现有代码已产出这些中间值，拆出即可）；文件按阶段拆 2-3 个模块（如 `ingest_parse.py` / `ingest_index.py`）；`_progress` 改为事件回调参数而非函数内闭包。

#### P1-2：API 层 ~1300 行零直接测试

**位置**：`src/api/routers/`（novels.py 391 + characters.py 381 + impersonation.py 324 + chat.py 164 + ops.py 85 + approvals.py 38）——`grep -rln "routers\." tests/` **零命中**（仅 test_api_helpers.py 覆盖 helpers.py）。

**根因**：API 层以 agent_server.py 的 TestClient 冒烟为主（test_agent_server_auth.py），路由内部逻辑缺少单元/集成测试。

**影响**：鉴权、参数校验、错误映射、SSE 事件契约这些"最容易回归"的胶水层没有防护；前端契约（StreamEvent 类型）变更无测试拦截。

**改进建议**：优先为 `chat.py`（SSE 契约）与 `novels.py`/`characters.py`（写操作 + HITL 门禁）补集成测试；用依赖注入替换 `_get_imp_store()` 等全局获取器，便于 mock。

#### P1-3：`zip()` 无 `strict=` 遍布向量/对话代码（12 处 B905）

**位置**：`vector_store.py:113,224,247`、`lance_backend.py:178`、`novel_store.py:246,401`、`speaker_window.py:106`、`speaker_attributor.py:158`、`dialogue_quota.py:131`、`character_ner.py:114` 等

**根因**：`zip(a, b)` 在长度不等时**静默截断**。向量存储处（vector↔id↔metadata 配对）若两侧长度不一致，会静默丢数据且无任何告警。

**影响**：索引/检索数据错位时表现为"查不到"或"结果张冠李戴"，极难排查；且当前已有测试失败正是检索空结果，两者可能相关。

**改进建议**：涉及配对的关键路径改 `zip(a, b, strict=True)`（Python 3.10+），让长度不匹配立即抛错；或断言两侧长度相等。

#### P1-4：`character_ner.py` B023 闭包捕获循环变量

**位置**：`src/domain/novel/character_ner.py:109-112`（`flush()` 闭包引用 `piece`/`off`）

**根因**：内层 `flush()` 在 `for piece, off in ...` 循环内定义并**延迟到循环体后续调用**，捕获的是循环变量。当前代码 flush 在循环内立即调用（暂未出问题），但这是典型的"改了调用时机就爆炸"的脆弱写法。

**影响**：若未来把 flush 移出循环/异步化，所有 mention 的 offset 都会指向最后一轮的 `piece`/`off`，产生错位归因。

**改进建议**：`flush(piece=piece, off=off)` 显式传参，或用 `functools.partial` 绑定当前迭代值。

#### P1-5：异常链丢失（B904 ×3）

**位置**：`ingest.py:1706`、`dialogue_local_llm.py:237`、`builtin_file.py:70`

**影响**：`except` 块内裸 `raise` 覆盖原始异常上下文，日志排查时丢失根因堆栈。

**改进建议**：改 `raise ... from err` 或 `from None`。

### P2 — 建议修

#### P2-1：类型注解现代化（UP006/UP035/UP045 占大头）

- `typing.List`/`typing.Dict`/`Optional[X]` 旧式注解遍布 `tools/registry.py`、`utils/config.py`、`utils/auth.py`、`builtin_search.py` 等（1101 个 lint 问题中大部分是此类）。
- **改进**：`ruff check src --fix` 即可自动修复 833 个，建议在 CI 中逐步放开 `UP` 规则。

#### P2-2：`setattr` 常量属性（dialogue_quota.py:248,257,264 B010）

**改进**：直接赋值属性即可（`self.x = v`），`setattr(self, "x", v)` 无额外安全性。

#### P2-3：集合字面量重复项（B033，name_resolver.py / character_builder.py）

**改进**：删重复元素即可，可自动修复。

#### P2-4：行超长 E501（builtin_search.py、registry.py、config.py 等）

**改进**：`ruff format` 或手动换行；可自动修复。

#### P2-5：`_switch_to_fallback` / `_on_failure` 中递归调用链

**位置**：`llm.py:399`（`chat` 内失败后 `return self.chat(...)`）、`llm.py:497`（`achat_result` 同理）

**根因**：失败切换 fallback 时用**递归重入**而非循环。若 fallback 也持续失败且 `_on_failure` 返回 True（如 fallback 失败→回到 primary→再失败→再切 fallback），可能形成**无限递归**（虽然目前 `_using_fallback` 状态位限制了大部分场景）。

**影响**：极端网络故障下可能栈溢出而非优雅报错；`max_retries` 不约束 fallback 递归深度。

**改进建议**：将失败重试改为 `for` 循环 + fallback 状态机（最多 primary→fallback→primary 一次），避免递归。

#### P2-6：前端测试稀疏（仅 2 个单测文件，无 API 层测试）

**位置**：`frontend/src/lib/__tests__/`（errors.test.ts、lib-utils.test.ts）

**改进**：SSE 解析（`sse.ts`/`streamReducers.ts`）是前端核心且纯函数化程度高，补单测收益最大；`useSSE.ts` 的 AbortController 逻辑值得一条测试。

---

## 4. 亮点（保持）

- **CI gate 策略务实**：先保"运行时错误/未定义名"零容忍（E9/F63/F7/F82），风格类规则渐进放开，而不是一次性全量——比大多数项目聪明。
- **测试规模与质量**：466 通过用例，覆盖 planner/executor/熔断/沙箱/HITL/SSE 取消/会话锁/配额等**非平凡逻辑**，且大量 `pytest.mark.asyncio` 异步用例。
- **安全基线已落地**：`.env` 已 ignore、Bearer fail-closed、路径沙箱、HITL 审批、execute_code 默认禁用——初评的 P0 安全项全部闭环。
- **工程配套完整**：Docker 多阶段 + 非 root、requirements.lock、ruff + Dependabot、Prometheus 探针、文档 30+ 份且有 ADR 记录架构决策。
- **代码卫生**：0 裸 `except`、0 TODO/FIXME、`type: ignore` 仅 23 处且都有具体原因码。

---

## 5. 修复优先级路线图

| 阶段 | 内容 | 预计收益 |
|---|---|---|
| **A（立即可做，1-2h）** | P0-1 thinking 统一注入（改 llm.py 一处+删调用点重复）；P0-2C seed 断言对齐；P2-1 `ruff --fix` 批量清理 | 消除架构级隐患 + 恢复测试绿灯 + 1101 lint 降到 ~270 |
| **B（本周）** | P0-2A catalog 目录可注入；P0-2B LanceDB 复现定位；P1-5 B904；P2-3/P2-4 自动修复 | 3 个失败测试全部修复，测试 469/469 |
| **C（双周）** | P1-1 ingest_novel 拆分；P1-2 API 层补测试（chat/nodes 路由优先）；P1-3 zip strict；P2-5 递归改循环 | 维护性显著提升，API 契约有防护 |
| **D（远期）** | P1-4 闭包改显式传参；P2-2 setattr；P2-6 前端 SSE 单测；CI 逐步放开 UP/B 规则 | 与现有 Wave D/E 路线合并 |

---

## 6. 评估方法说明

- 真实运行：pytest 全量 326s（466 passed/3 failed/4 skipped）、ruff 双规则集、`git ls-files` 校验 `.env` 未入库。
- 源码走读：核心链路（agent.py → swarm_agent.py → impersonation_agent.py → llm.py）逐段阅读；大文件用 `grep -n "^def"` 拆结构分析。
- 未做：LLM 真实调用（无 API key 场景）、前端 build/playwright（未启动依赖）、性能压测。以上如需可另行安排。
