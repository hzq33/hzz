# 会话级上下文压缩（完整会话摘要）实现说明

> 日期：2026-08-11 · 关联报告：[2026-08_OOC_HALLUCINATION_TEST_REPORT.md](2026-08_OOC_HALLUCINATION_TEST_REPORT.md)

## 背景

OOC/幻觉测试发现：角色扮演**跨轮事实不一致**（艾琳"母亲死因"两题自相矛盾）、长对话遗忘早期事实。根因是 `ConversationMemory._truncate()` 超限时**直接丢弃最旧消息**，无摘要机制；`config.yaml` 中 `enable_summarization` 配置项预留但从未实现。

## 实现内容

### 1. 后端：上下文压缩（核心）

**`src/core/memory.py`**
- `ConversationMemory` 新增：`set_summary()/get_summary()`（压缩摘要，不随截断丢失）、`add_summarized_turns()/get_summarized_turns()`（累计压缩轮数）、`drop_oldest(keep)`（移除最旧非 system 消息并返回）、`restore_dropped()`（摘要失败时回滚，防静默丢上下文）
- 新增 `truncate_enabled` 参数：摘要开启时禁用 `add_message` 里的自动截断，由压缩器统一管理

**`src/core/impersonation/summarizer.py`**（新增）
- `summarize_dialogue()`：调 LLM（temperature 0.2）把早期对话压缩为结构化 JSON（narrative 脉络 / facts 已确认事实 / open_questions 待办）
- 输入：角色名 + 待压缩消息 + 已有摘要（合并去重）；失败返回空串由调用方回滚重试

**`src/core/impersonation/chat.py`**
- `maybe_compact()`：每轮 chat/流式结束后异步检查——token 估算超 `max_tokens * threshold` 时，保留最近 `keep_turns` 轮完整对话，更早的折叠进摘要
- 硬性兜底：摘要连续失败且消息超 `max_tokens * 4` 时退回丢弃策略，防内存膨胀
- `_build_messages` 把摘要作为 **system 块注入**："更早的对话摘要（已确认事实，回答时不得与之矛盾）"，角色每轮可见，从根源防跨轮矛盾

**`src/core/impersonation_agent.py`**
- `create_impersonation_agent` 从 `config.yaml → memory` 读取并传入：`max_history_tokens` / `enable_summarization` / `summarize_keep_turns` / `summarize_threshold`

**`src/application/impersonation_sessions.py`**
- 持久化：`metadata` 增加 `memory_summary` / `memory_summarized_turns`
- 恢复：会话重建时 `set_summary()` + `add_summarized_turns()` 还原

### 2. API

**`src/api/routers/memory_config.py`**（新增，注册于 agent_server.py）
- `GET /api/v1/agent/memory-config`：读 config.yaml → memory 段快照
- `PUT /api/v1/agent/memory-config`：白名单字段校验后写回 config.yaml（新会话生效）

**`src/api/routers/impersonation.py`**
- `/chat` 响应与流式 `done` 事件增加 `memory_stats`：`{max_tokens, tokens_est, summarized_turns, summary_excerpt}`

### 3. 前端

**设置页 `SettingsPage.tsx`**：「记忆与上下文」卡片
- 上下文上限（tokens）/ 保留最近完整轮数 / 压缩阈值 / 摘要开关，保存 → PUT memory-config

**扮演页 `ImpersonationPage.tsx` + `components/chat/ContextMonitor.tsx`**（新增）
- 上下文监控条：tokens 用量进度条（≥80% 黄 / ≥95% 红）、已压缩轮数徽标、"查看摘要"展开已确认事实
- 数据来自流式 `done` 事件的 `memory_stats`，经 `store.setMemoryStats` / `streamReducers` 传递

## 配置

```yaml
memory:
  max_history_tokens: 8000      # 上下文窗口上限
  enable_summarization: true    # 启用上下文压缩
  summarize_keep_turns: 8       # 压缩后保留最近完整轮数
  summarize_threshold: 0.8      # 触发压缩的 token 阈值比例
```

## 验证结果

| 项目 | 结果 |
|------|------|
| 单元测试（tests/） | 162 passed，无回归 |
| 压缩流程（stub LLM） | 20 轮 → 保留 8 轮 + 摘要，restore_dropped 正常 |
| 真实 LLM 压缩 | 12 轮对话触发 2 次压缩，4 轮入摘要；摘要正确提取"父亲马库斯/灰脊山一战/维克托副官"等事实 |
| 持久化 | memory_summary（700 字）+ summarized_turns（4）正确存入会话库并恢复 |
| 前端 tsc | 通过 |
| 配置热更新 | GET/PUT memory-config 读写正常，新会话按新配置创建 |

## 边界说明

- 摘要用独立 LLM 调用（~500 tokens），每轮仅在超阈值时触发一次，失败静默降级下轮重试
- 配置变更对新会话生效；已在内存中的会话保持旧配置直至重建
- 摘要注入为 system 块，优先级高于检索结果，冲突时以摘要（已确认事实）为准

---

# 通用助手问题修复（追加：2026-08-11）

对通用助手（Agent / SwarmAgent / ConversationService）静态审查发现的 7 项问题全部修复：

## P1-1 通用助手接入上下文压缩
- `Agent.__init__`：`ConversationMemory(truncate_enabled=not enable_summarization)`，读取
  `max_history_tokens / enable_summarization / summarize_keep_turns / summarize_threshold`
- `Agent.maybe_compact()`：与扮演链路同款逻辑（超阈值→保留最近 N 轮→LLM 摘要→硬兜底）
- `_run_with_native_tools` / `_run_with_planner` / `_direct_reply` 均注入摘要 system 块 + 轮末触发压缩

## P1-2 扮演模式 memory 隔离
- `swarm_agent._rag_chat_node` / `_exit_role_node` 不再写 `agent.memory`
- 扮演对话只存 ImpersonationAgent 自己的 memory，避免双 memory 消息不平衡、扮演内容污染普通历史

## P1-3 扮演历史恢复
- `conversation_service._persist`：metadata 增加 `rag_messages / rag_summary / rag_summarized_turns`
- `get_or_create` 恢复时：`_enter_impersonation` 后回填扮演 memory + 摘要 + 压缩计数

## P2-4 恢复后 system prompt 统一刷新
- `get_or_create` 恢复末尾 `agent.refresh_system_prompt_for_tools()`，避免工具清单过期

## P2-5 预算持久化（SQLite）
- `session_budget` 从进程内 dict 改为 `data/budget.db`（WAL + 事务，多 worker 共享）
- 接口兼容：`add_session_tokens / get_session_tokens / reset_session / check_budget / record_usage_dict` 不变
- 新增 `set_budget_db_path()`（测试隔离用）、`clear_all()`；测试 conftest 已隔离临时库

## P2-6 CLI 对齐
- CLI（main.py）走 `agent.run()` 已自动获得上下文压缩（Agent 构造读配置）；SSE/scope/HITL 为 API 层能力，CLI 保持轻量调试通道

## P3-7 死代码清理
- 删除 `src/conversation/manager.py`（ConversationManager 无任何引用）

## 验证
- 162 单测全过；预算持久化/重启保留验证通过；Agent 压缩（21 条→7 条，7 轮入摘要）通过；
  扮演历史恢复（消息/摘要/压缩计数）通过；真实服务端到端（普通→扮演→退出→普通）隔离验证通过

---

# 通用助手配套工具（追加：2026-08-11）

按"拆开写"原则，新增 3 个独立工具（不合成大工具）：

| 工具 | 文件 | 功能 | 读写 |
|------|------|------|------|
| `graph_rag` | `src/tools/builtin_graph_rag.py` | GraphRAG 全局问答：overview（全局概览+社区列表）/ ask（全局问答）/ status（构建状态） | 读 |
| `character_graph` | `src/tools/builtin_character_graph.py` | 角色关系图谱：节点/边/统计，支持 doc_id 锁卷、min_confidence/min_weight 过滤 | 读 |
| `roster` | `src/tools/builtin_roster.py` | 角色盘点：get（规范名+别名+重要度+提及数）/ list（已有盘点系列） | 读 |

- 注册：`src/tools/bootstrap.py` 工厂表 + `config.yaml → tools.builtin`（与现有 7 工具并列）
- 三者均走 service 层（graph_rag_service / story_analysis_service / alias_roster），不持有 store，无需广播注册
- 错误处理遵循统一约定：读工具 fail 降级，不打断工具循环
- 同时完成的现有工具优化（同一批）：
  1. store 统一：新增 `src/application/tool_store_broadcast.py`，character_kb/story_analysis/novel_admin 支持 `inject_store`，server 重建 store 后统一广播
  2. 补实现 `novel_search.global`（GraphRAG 全局问答，对齐 AGENT_TOOLS_DESIGN 文档承诺）
  3. 错误处理统一：读工具 fail 降级；写工具（novel_admin）保持 raise
  4. schema 补齐：character_kb 字段 description、novel_admin 风险提示
  5. 结果格式统一（核对通过）
  6. redialogue 并入 novel_admin（写操作，HITL 门控已加）
  7. import 加固：拒绝绝对路径 + symlink 逃逸检查
- 验证：162 单测全过；三个新工具真实数据验证（graph_rag=8 社区、character_graph=23 节点/46 边、roster=14 实体）
