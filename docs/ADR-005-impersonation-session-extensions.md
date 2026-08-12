# ADR-005: Impersonation Session Extensions (Deferred)

> 日期：2026-07-27 | 状态：Deferred

## 背景

沉浸式 RP 用户需要分支存档、角色记忆面板、用户人设、多角色同台等能力。这些需求涉及会话模型与 UI 的大改，不在「溯源 + 导入进度 + 书库 MVP」迭代范围内。

## 已交付（Phase D 轻量）

- 重新生成最后一条 bot 回复（`POST /api/v1/agent/impersonate/regenerate`）
- 多行输入（Shift+Enter 换行）
- 角色快捷切换（头部下拉，切换时清空会话）
- 会话记忆上限提示（SSE `done.max_history_tokens`）

## 延后

| 能力 | 原因 |
|------|------|
| 分支存档 / 读档 | 需持久化会话树与 checkpoint 模型 |
| 角色记忆面板 | 需结构化 memory store + UI |
| 用户人设 | 需 system prompt 注入与身份切换状态 |
| 多角色同台 | 需多 agent 编排与轮流发言协议 |

## 后续建议

1. 会话持久化：`data/sessions/{session_id}.json` + parent_id 字段实现分支
2. 记忆面板：暴露 `ConversationMemory` 摘要与 RAG 命中历史
3. 多角色：LangGraph 子图或 round-robin orchestrator
