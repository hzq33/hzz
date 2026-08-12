# 密钥与 Secret 轮换清单（Wave A1）

> 状态：`Manual` — 必须在供应商控制台与本地环境完成，仓库无法代劳。  
> 关联：[评估计划 Phase 3 Wave A](./AGENT_PROJECT_EVALUATION_OPTIMIZATION_PLAN.md)

## 为何必须做

历史工作区曾出现真实 API key。即使 `.gitignore` 已忽略 `.env`，**曾暴露过的 key 仍应视为泄漏**，需作废并换新。

## 检查（仓库侧，可自动化）

```bash
# .env 必须被忽略且未跟踪
git check-ignore -v .env
git ls-files .env   # 应无输出

# 工作区不应暂存真实 secret
git status --short | findstr /i ".env"
```

当前约定：

| 文件 | 是否入库 |
|------|----------|
| `.env` | 否（`.gitignore`） |
| `.env.example` | 是（仅占位符） |
| CI Secrets / Compose env | 运行时注入，不提交 |

## 轮换步骤（人工）

1. **DeepSeek（或当前 LLM 供应商）控制台**  
   - 作废 / 删除旧 API key  
   - 创建新 key  
2. **本地**  
   - 更新 `d:\tools\agent\.env` 中 `DEEPSEEK_API_KEY` / `AGENT_API_TOKEN`  
   - 勿把新 key 写入可提交文件  
3. **CI / 部署**  
   - 更新 GitHub Actions secrets、Compose 宿主机 env、镜像运行环境  
4. **验收**  
   - 用旧 key 调 API → 应 401/鉴权失败  
   - 用新 key 本地 `GET /api/v1/agent/health/ready` → 200  
5. **记录**  
   - 在团队变更日志记下轮换日期（勿记录 key 本身）  
   - 完成后将评估计划 A1 标为 `Done`

## 相关变量（见 `.env.example`）

- `DEEPSEEK_API_KEY` — LLM  
- `AGENT_API_TOKEN` — API Bearer（fail-closed）  
- `LANGFUSE_*` — 可选可观测  
- 勿提交 `CORS_ORIGINS=*`（启动会拒绝）
