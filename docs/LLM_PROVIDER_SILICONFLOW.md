# 硅基流动 (SiliconFlow) LLM 接入预留说明

> 状态：**已预留，未启用**（key 已就绪于 `.env`，启用方式见下文）
> 日期：2026-08-09 | 关联：`src/shared/llm_config.py`（调用点配置服务）、`data/llm_config.json`（运行时配置）

---

## 1. 已验证模型（2026-08-09 实测连通）

| 模型 ID | 上下文 | 实测 | 备注 |
|---------|--------|------|------|
| `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` | 128K（YaRN 32K→128K） | ✅ 可用 | **推理模型**：回复含 reasoning_tokens，`max_tokens` 需预留推理配额 |
| `Qwen/Qwen3-8B` | 128K | ✅ 可用 | **冷启动 ~43s**（首请求慢，后续快）；免费模型 |

已写入 `.env`：
```
SILICONFLOW_API_KEY=sk-...   # 见 .env（gitignore，不入库）
```

硅基流动 API：`https://api.siliconflow.cn/v1`（OpenAI 兼容）

---

## 2. 启用方式（两种，任选）

### 方式 A：改 `data/llm_config.json`（推荐，前端设置页可操作）

```json
{
  "character_inventory": {
    "provider": "custom",
    "base_url": "https://api.siliconflow.cn/v1",
    "model": "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
    "temperature": 0,
    "max_tokens": 4096,
    "enabled": true,
    "thinking": "off",
    "api_key": "在这里填硅基流动 key"
  },
  "dialogue_extract": {
    "provider": "custom",
    "base_url": "https://api.siliconflow.cn/v1",
    "model": "Qwen/Qwen3-8B",
    "temperature": 0,
    "max_tokens": 6144,
    "enabled": true,
    "api_key": "在这里填硅基流动 key"
  }
}
```

⚠️ `llm_config.json` 的 `api_key` 目前是**明文存储**（与现有 glm 条目一致，单租户环境可接受）。若需环境变量引用（`${SILICONFLOW_API_KEY}`），需小改 `_effective_api_key`（见第 4 节）。

### 方式 B：改 `config.yaml`

```yaml
novel_rag:
  character_inventory:
    ...
  # 需要 config.yaml 结构支持 base_url 覆盖时使用（当前按调用点配置更灵活）
```

---

## 3. 模型分配建议

| 调用点 | 推荐模型 | 理由 |
|--------|---------|------|
| `character_inventory`（角色提取/盘点） | `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` | 128K 上下文，12 万字符盘点装得下 |
| `dialogue_extract`（对话抽取/归因） | `Qwen/Qwen3-8B` | 轻量免费，归因 prompt 窗口小 |
| `query_rewriter` / `intent_router` | `Qwen/Qwen3-8B` | 短 prompt，免费模型足够 |
| `impersonation_chat`（角色扮演） | 建议保留 DeepSeek 主模型 | 生成质量优先 |

---

## 4. 已知注意事项

1. **R1 推理模型配额**：`DeepSeek-R1-0528-Qwen3-8B` 是推理模型，实测 10 max_tokens 实际消费 156 completion（含 reasoning）。盘点用 `max_tokens` 建议 ≥4096，否则推理可能被截断。
2. **Qwen3-8B 冷启动**：首次请求 ~43s，任务调度注意超时设置（如 inventory 的 90s 超时足够）。
3. **免费额度**：硅基流动有免费模型（Qwen3-8B 系），可进一步省钱；付费模型按量计费。
4. **环境变量展开**（可选改进）：`src/shared/llm_config.py::_effective_api_key` 目前只读 entry.api_key 明文或 `DEEPSEEK_API_KEY`。如需 `${SILICONFLOW_API_KEY}` 引用，改该函数支持 `${ENV}` 前缀展开即可（约 5 行），未实施（预留）。
5. **llm_config.json 是运行时数据**（gitignore 的 `data/` 下），改动不影响仓库。

---

## 5. 预留清单

- [x] `.env` 写入 `SILICONFLOW_API_KEY`
- [x] 两个模型连通性实测（R1-0528-Qwen3-8B / Qwen3-8B）
- [x] 本文档（启用模板 + 注意事项）
- [ ] 实际切换调用点（**未执行**，等待确认）
- [ ] （可选）`_effective_api_key` 支持环境变量引用
