# Modular Agent Framework（模块化 AI Agent）

> 融合 **Novel RAG 检索**、**角色扮演** 与 **GraphRAG 全局问答** 的模块化 AI Agent 系统。

## 项目简介

一个模块化的 AI Agent 框架，提供三种使用模式：

- **通用助手** — 原生 tool calling（可选 Planner），SSE 流式对话
- **角色扮演** — 基于小说语料的四通道检索 + 多轮角色扮演（自动携带原文证据）
- **世界体系** — 剧情分析（时间线 / 设定书 / 关系图谱）+ GraphRAG 全局问答（社区发现 + LLM 社区摘要 → 主线 / 整体关系网问答）

后端为 FastAPI 服务，前端为 React + Vite 单页应用，两者独立启动、通过 HTTP + SSE 通信。

## 技术栈

| 层 | 技术 |
|----|------|
| LLM | DeepSeek（primary / fallback），调用点可在设置页独立配置 |
| Embedding | Qwen3-Embedding-0.6B（1024 维） |
| 向量库 | LanceDB（默认）/ FAISS（测试） |
| 编排 | LangGraph StateGraph |
| API | FastAPI + SSE，Bearer 鉴权，HITL 人工审批 |
| 前端 | React 18 + Vite + Zustand + Tailwind |
| 测试 | pytest（离线单测）+ Playwright（冒烟） |

## 环境要求

- Python **3.13**（`requirements.txt` 以 3.13 为准）
- Node.js + npm（前端构建）
- 可访问 DeepSeek API（或通过设置页配置其他 OpenAI 兼容端点）

## 安装

```bash
# 1) 进入项目根目录，创建虚拟环境并安装依赖
python -m venv venv
venv\Scripts\activate            # Windows；macOS/Linux 用 source venv/bin/activate
pip install -r requirements.txt  # 唯一依赖文件（清华 PyPI 镜像，见文件头注释）

# 2) 安装前端依赖
cd frontend
npm install
cd ..
```

> 依赖文件已统一为 `requirements.txt`（原 lock/ci/eval 多文件已合并删除）。

### 模型文件

`models/` 目录不在仓库中，按 `config.yaml` 中配置的路径放置：

- `models/Qwen3-Embedding-0.6B/` — 检索向量化模型
- `models/bge-reranker-v2-m3/` — 可选 reranker（缺失时自动回退 keyword 精排）

## 配置

```bash
cp .env.example .env
```

编辑 `.env`，必填项：

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥（必填，服务启动失败即退出） |
| `AGENT_API_TOKEN` | API 访问令牌，用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成（必填，前端请求与 API 均需携带） |
| `CORS_ORIGINS` | 允许的浏览器来源，默认 `http://localhost:3000,http://localhost:3001` |

其余为可选：会话预算 / 速率限制 / 遥测（OTEL、Langfuse）/ 本地 LLM 等，见文件内注释。

### LLM 配置

**方式一：`.env` 环境变量（默认 DeepSeek）**

```ini
DEEPSEEK_API_KEY=你的DeepSeek密钥      # 必填
DEEPSEEK_MODEL=deepseek-v4-flash       # 可选，默认即可
DEEPSEEK_FALLBACK_MODEL=deepseek-v4-pro
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

`config.yaml` 中 `agent.api_key: ${DEEPSEEK_API_KEY}` 自动读取环境变量，无需改 config。

**方式二：前端「设置」页动态配置（支持多家服务商）**

启动服务后打开前端 → **设置页 → LLM 调用点**：选服务商、填 API Key、选模型，可点「测试连接」，保存即生效（写入 `llm_config` 持久化，不用改 .env / config.yaml）。每个调用点（对话 / 扮演 / 对话抽取 / QA 生成等）可独立配置模型。

| Provider | base_url | 说明 |
|----------|----------|------|
| `deepseek` | api.deepseek.com | 默认（flash + pro） |
| `openai` | api.openai.com/v1 | gpt-4o 系列 |
| `moonshot` | api.moonshot.cn/v1 | Kimi |
| `glm` | open.bigmodel.cn | 智谱清言 |
| `ollama` | localhost:11434/v1 | 本地模型（qwen3:8b 等，无需 API key） |
| `siliconflow` | api.siliconflow.cn/v1 | 硅基流动 |
| `custom` | 自定义 | 任意 OpenAI 兼容端点 |

> 提示：
> 1. `AGENT_API_TOKEN` 无论如何都要填写——前端请求与 API 均要求 Bearer 鉴权，缺失/弱口令服务拒绝启动
> 2. 文本生成 ≠ 检索：小说 RAG 还需本地 embedding 模型 `models/Qwen3-Embedding-0.6B/`（不在仓库中，需自行下载放置；reranker 同理可选）
> 3. 换用非 DeepSeek 服务商：通过设置页覆盖端点，或改 `config.yaml` 的 `agent.model / base_url / api_key`
> 4. 使用 Ollama 本地模型无需云端 key，生成质量取决于本地模型

系统行为参数（模型、检索通道权重、对话抽取、上下文压缩等）在 `config.yaml` 中调整，如：

- `memory.enable_summarization` — 上下文压缩开关（默认 `true`，超阈值自动折叠早期轮次为摘要）
- `tools.builtin` — 启用的内置工具列表
- `novel_rag.*` — 检索 / 入库 / 角色盘点参数

## 启动

直接双击 `start.bat`（Windows），或手动执行：

```bash
# 后端（端口 8080）
venv\Scripts\python.exe -m uvicorn agent_server:app --host 0.0.0.0 --port 8080

# 前端（另开终端，端口 3001，以启动输出为准）
cd frontend && npm run dev
```

启动后：

- **前端界面**：http://localhost:3001
- **API 服务**：http://localhost:8080（文档 /docs，健康检查 /api/v1/agent/health/live）

> 生产环境请勿使用 `--reload`（长任务如上传入库会被重启打断，见 .env.example 注释）。

## 使用说明

### 通用对话
打开前端界面直接提问即可；可让 Agent 调用内置工具（联网搜索、文件操作、小说检索等），高风险操作会弹出**人工审批**（HITL）等待确认。

### 上传小说 / 建立检索
1. 在前端「知识库 / 上传」上传小说文件（txt / epub 等）
2. 系统自动完成分章、对话抽取、角色盘点与向量入库（异步任务，可在任务列表查看进度）
3. 入库后可进行小说检索问答、角色扮演、剧情分析

### 角色扮演
选择已入库的小说与角色，进入扮演会话；回答基于检索证据生成并附带原文引用，支持多轮上下文（含自动压缩摘要）。

### 世界体系
对已入库系列运行剧情分析（构建时间线 / 设定书 / 关系图谱），随后可进行 **GraphRAG 全局问答**（跨章节主线、整体关系网提问）。

### 命令行 CLI（可选）
```bash
python main.py --config config.yaml --query "你的问题"
python main.py --config config.yaml --interactive
```

## 项目结构

```
├── agent_server.py      # FastAPI 服务入口
├── main.py              # CLI 入口（单问 / REPL）
├── config.yaml          # 系统配置
├── requirements.txt     # 唯一依赖文件
├── src/
│   ├── api/             # HTTP 路由 / 请求响应模型
│   ├── core/            # Agent / Swarm / 角色扮演 / 上下文压缩
│   ├── application/     # 会话、小说入库 / 检索 / 对话抽取服务
│   ├── domain/          # 小说块、对话、角色盘点、分块器
│   ├── infrastructure/  # LanceDB / FAISS、embedding、reranker
│   ├── tools/           # 内置工具
│   └── shared/          # LLM、会话、预算、HITL、限流、遥测
├── frontend/            # React 前端
├── tests/               # 离线单元测试
├── scripts/dev/         # 手动诊断 / 验证脚本（非 CI）
├── deploy/              # prometheus 监控告警配置
└── data/                # 运行时数据（向量库、会话、任务、上传、工作区）
```

## 内置工具

注册于 `config.yaml` → `tools.builtin`：

| 工具 | 说明 |
|------|------|
| `web_search` | 公网检索（DuckDuckGo） |
| `novel_search` | 四通道小说 RAG、书目 / 章节目录、单次模仿、GraphRAG 全局问答 |
| `novel_admin` | 书目管理（重命名 / 删卷 / 清理 / 对话重抽取）；**写操作需 HITL** |
| `character_kb` | 角色名录、按需建卡、合并、编辑；**build/merge/update 需 HITL** |
| `story_analysis` | 剧情脉络（时间线 / 伏笔 / 关系图谱）；**build 需 HITL** |
| `graph_rag` | 读取 GraphRAG 全局层（社区摘要 / 全局问答） |
| `character_graph` | 读取角色关系图谱 |
| `roster` | 读取系列角色规范名与别名 |
| `file_operation` | 工作区沙箱文件操作；**write 需 HITL** |
| `execute_code` | 默认禁用（`EXECUTE_CODE_ENABLED` 启用），子进程沙箱 + HITL |

## 测试

```bash
venv\Scripts\python.exe -m pytest          # 离线单元测试
# 前端（frontend/ 下）
npm run typecheck && npm run lint
```
