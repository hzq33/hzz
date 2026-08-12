# 运行时稳定性优化方案（框架层）

> 同步：2026-08-04  
> 范围：**怎么跑稳定**（LangGraph / Job / Session / LLM / Probe / 前端取消），**不含**小说 RAG 业务语义改造  
> 原则：加深已有栈（LangGraph + SQLite Job/Session + SharedLLM），不换整套编排框架

---

## 1. 目标与非目标

### 目标（验收）

| ID | 指标 | 验收 |
|----|------|------|
| S1 | 进程重启后 Job 不假「running」 | lifespan 启动即 `mark_orphans_failed`；ready 可观察 orphan 数 |
| S2 | 优雅停机 | shutdown 取消/等待 in-flight asyncio Job；未完成标 failed 或 pending |
| S3 | 客户端取消有效 | SSE Abort / 离开页 → 后端取消图任务；poll 带 AbortSignal |
| S4 | LLM 抖动可恢复 | 重试 + jitter；熔断字段落地或删除；stream/非 stream 策略一致 |
| S5 | 就绪信号可信 | ready 含 job orphan / 可选 embed 权重 degraded；live 仍轻量 |
| S6 | 开发期预期明确 | `--reload` 文档化：长任务不可靠；生产关 reload |

### 非目标

- 不换成 CrewAI / 云 Assistants 作主编排
- 本轮不做多副本 Redis Job worker（列入 Phase C）
- 不改 story/ingest 业务算法
- 不为「状态好看」引入 SqliteSaver，除非出现 HITL interrupt/resume 刚需

---

## 2. 现状基线（摘要）

```text
Browser Abort ──► SSE/poll（前端）── partial
                      │
agent_server lifespan ──► telemetry + session prune
                      ✗ Job orphan / runner shutdown
                      │
SwarmAgent ── MemorySaver（进程内）+ 消息落 SQLite
AsyncJobRunner ── 进程内 create_task；orphan 仅在 start()/enqueue
SharedLLM ── retry + fallback；_circuit_open_until 未真正使用
/ready ── token/config/可写；✗ embed/worker/orphan
```

**`--reload` 一句话：** 杀进程 → MemorySaver / 内存会话 / 正在跑的 Job 全丢；Job 行易变 orphan；消息与 Job 元数据仍在盘上。

---

## 3. 分层设计

### 3.1 Job 生命周期（最高优先级）

**问题：** orphan 清理 lazy；shutdown 不取消任务；upload concurrency 参数可能被 singleton 忽略。

**方案：**

| 步骤 | 改动点 | 行为 |
|------|--------|------|
| J1 | `agent_server` lifespan startup | `get_job_store().mark_orphans_failed()` + metrics |
| J2 | lifespan shutdown | `runner.shutdown(timeout=…)`：cancel tasks → 未完成 job `failed`（或 `pending` 可配置） |
| J3 | `get_job_runner` | 尊重 `AGENT_JOB_CONCURRENCY`；分类型可选（upload=1, story=2） |
| J4 | Job 记录 | `error` 带稳定码：`orphan_after_restart` / `cancelled_on_shutdown` |
| J5 | 可选重试 | `upload` / `story_analysis` 支持 `POST .../retry`（仅 failed+可重试码）；本 Phase 可只做文案「请重试」 |

**状态机（稳定后）：**

```text
pending → running → done
                 ↘ failed (error_code)
running + restart → failed(orphan_after_restart)   # startup
running + shutdown → failed(cancelled_on_shutdown) # 或 pending 若策略=requeue
```

### 3.2 LangGraph / SSE 取消

**问题：** 图状态仅 MemorySaver；断线后 `ainvoke` 可能继续耗 LLM。

**方案（务实）：**

| 项 | 建议 |
|----|------|
| Checkpointer | **保持 MemorySaver**；权威状态 = SessionStore 消息。文档写清：不做跨重启 graph resume |
| SSE cancel | chat/impersonation stream：检测 `request.is_disconnected` 或绑定 Abort → `task.cancel()` |
| 会话恢复 | 已有：消息 + `rag_character` 重建 ImpersonationAgent；保持即可 |
| 未来 SqliteSaver | 仅当需要 HITL interrupt 跨进程 resume 时再开 Phase C |

**伪代码（chat stream）：**

```python
task = asyncio.create_task(swarm.run_stream(...))
async for event in ...:
    if await request.is_disconnected():
        task.cancel()
        break
```

### 3.3 Session 稳定

| 项 | 动作 |
|----|------|
| Startup prune | chat **与** imp 均 `cleanup_old_sessions(keep=AGENT_SESSION_KEEP)` |
| 文档 | 区分热 `max_sessions`（LRU）与冷 `AGENT_SESSION_KEEP` |
| 可选 | `AGENT_SESSION_TTL_HOURS` 按 `updated_at` 删；与 keep-N 组合（先 TTL 再 keep） |
| Budget | impersonation 与 chat 共用 `check_budget` / `record_usage`；可选写入 metadata 抗 reload |

### 3.4 SharedLLM 韧性

| 项 | 动作 |
|----|------|
| 熔断 | 实现 `_circuit_open_until`（开路 N 秒拒请求或强制 fallback），或删除死字段避免假安全感 |
| Retry | 指数退避 + **jitter**；429 读 `Retry-After` |
| 对称 | stream / non-stream 共用 `_on_failure` / `_try_revert` |
| 单测 | 扩展 `tests/test_shared_llm.py`：连续失败开路、cooldown 回切 |

### 3.5 Readiness / Metrics

| 探针 | 内容 |
|------|------|
| live | 不变：进程活着 |
| ready | 现有 + `job_orphan_count==0`（或仅 warning 字段）+ `jobs_in_flight` |
| degraded（可选 JSON 字段，仍 200） | embed/reranker 权重路径缺失 |
| /health | 增加 `job_backend`、`runner_started`、`concurrency` |
| metrics | `agent_jobs_in_flight`、`agent_job_orphans_total`、已有 circuit/failover |

### 3.6 前端取消与轮询

| 项 | 动作 |
|----|------|
| 统一 | upload / character build / story_analysis 的 `pollJob` **均传 AbortSignal** |
| unmount | Knowledge / Impersonation / Chat 卸载 abort |
| sleep | abortable delay（abort 时立刻结束，不空等 interval） |
| 超时 | 角色建卡对齐可配置预算（勿默认 96s）；剧情保持长超时 |
| 文案 | orphan/重启失败 →「服务已重启，请重新触发任务」 |

### 3.7 可观测（轻量）

| 项 | 动作 |
|----|------|
| 默认 | 生产关 `OTEL_CONSOLE_EXPORT`；OTLP 显式开 |
| Job span | enqueue → done/failed，attribute `job_id` / `job_type` |
| 关联 | LLM span 带 `session_id`（已有部分） |

---

## 4. 分阶段落地

### Phase A — 止血（1–2 天，P0）✅ 已落地

1. **JOB-1** lifespan startup orphan 清理 — `get_job_runner().start()` + metrics  
2. **JOB-2** shutdown 取消 in-flight Job — `shutdown_job_runner()`  
3. **FE-1/FE-3** 所有 poll + abortable sleep — `pollJob` / Knowledge unmount  
4. **FE-4** orphan 文案 — `errors.ts`  
5. **SES-1** imp 会话 prune — `prune_persisted_sessions`  
6. 文档：`--reload` 与生产启动建议 — `.env.example`  

**验收：** 杀进程后刷新，无长期 `running` Job；ready/metrics 可见 orphan 处理；离开 Knowledge 页轮询停止。

### Phase B — 韧性（3–5 天，P1）✅ 已落地

1. **LG-2** SSE disconnect → cancel graph — chat/imp `is_disconnected` + `run_stream` cancel  
2. **LLM-1/2/3** 熔断落地 + jitter + 单测 — `_circuit_open_until` / Retry-After / tests  
3. **RDY-1/2** ready 含 orphan / degraded embed — soft warning，不一律 503  
4. **LIM-2** impersonation budget — 与 chat 共用 `check_budget` / `record_usage_dict`  
5. **JOB-4** `AGENT_JOB_CONCURRENCY` 分型 — upload/story/character 分 Semaphore  

**验收：** 刷掉 SSE 后 LLM 调用明显中止（日志/metrics）；LLM 连续失败行为可测；ready 在无权重时 degraded。

### Phase C — 规模化（可选）✅ MVP 已落地（无 Redis）

1. **外置 Job worker** — `AGENT_JOB_WORKER_MODE=external` + `python -m src.jobs.worker`（SQLite claim/lease）  
2. **Handler 注册表** — `src/application/jobs/`（upload / story / character 共用）  
3. **Job OTel span** — `job.run`（inprocess / external）  
4. **FastAPI OTel 自动埋点** — `instrument_fastapi`（缺包则 fail-open）  

**明确延后：** Redis 多副本 session/budget；LangGraph SqliteSaver（当前 HITL 非 graph interrupt）；Celery/RQ。

**用法：**

```bash
# 终端 A — API 只入队
set AGENT_JOB_WORKER_MODE=external
uvicorn agent_server:app --port 8080

# 终端 B — 同库 worker
python -m src.jobs.worker
```

---

## 5. 配置契约（拟增）

```yaml
# config.yaml / env（示意）
# AGENT_JOB_CONCURRENCY=2
# AGENT_JOB_UPLOAD_CONCURRENCY=1
# AGENT_JOB_SHUTDOWN_GRACE_SEC=15
# AGENT_SESSION_KEEP=50          # chat+imp
# AGENT_SESSION_TTL_HOURS=0      # 0=关闭
# AGENT_LLM_CIRCUIT_FAILURES=3
# AGENT_LLM_CIRCUIT_OPEN_SEC=30
# AGENT_LLM_RETRY_JITTER=1
```

`.env.example` 同步注释；生产 compose：**不要** `--reload`。

---

## 6. 风险与回滚

| 风险 | 缓解 |
|------|------|
| shutdown 标 failed 导致用户以为需重导 | 文案区分 orphan vs 业务失败；保留 result 进度快照 |
| cancel SSE 误杀慢生成 | 仅 disconnect 时 cancel；完成事件仍正常 |
| ready 过严导致编排摘流 | orphan 用 warning 字段或独立 gauge，不一律 503 |
| 熔断过猛 | 有 fallback 时切备用；无 fallback 短开路 |

回滚：feature flag `AGENT_JOB_LIFECYCLE_V2=0` 跳过 shutdown cancel；前端可临时去掉 signal。

---

## 7. 与「换框架」的关系

本方案 **加深** 现有 LangGraph / SQLite Job / SharedLLM，不引入第二套编排。  
「别人封装好的接口」仅继续用于 **LLM HTTP**；稳定性靠 **生命周期与取消语义**，不靠换 RAG SDK。

---

## 8. 实施任务清单（可直接开干）

| 优先级 | 任务 | 主文件 |
|--------|------|--------|
| A1 | lifespan orphan + metrics | `agent_server.py`, `async_jobs.py` |
| A2 | runner.shutdown | `async_jobs.py`, `agent_server.py` |
| A3 | poll AbortSignal + abortable sleep | `pollJob.ts`, `KnowledgePage.tsx`, `api.ts` |
| A4 | imp session prune | `agent_server.py`, `session_factory` |
| B1 | SSE disconnect cancel | `chat` / `impersonation` routers, `swarm_agent` |
| B2 | LLM circuit + jitter | `llm.py` + tests |
| B3 | ready orphan / degraded | `readiness.py`, `ops.py` |
| B4 | job concurrency env | `async_jobs.py`, `.env.example` |

---

## 9. 成功画像

开发者：`--reload` 后 Job 立刻变 failed 并提示重试，不会无限转圈。  
用户：关掉扮演页 / 点停止后，后端不再空烧 token。  
运维：`/ready` 与 `/metrics` 能看出 Job 积压与 LLM 熔断；生产无 reload、concurrency 可配。
