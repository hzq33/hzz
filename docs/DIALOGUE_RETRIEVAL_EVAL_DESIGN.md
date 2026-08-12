# 对话检索质量评估设计（Dialogue Retrieval Eval）

> 日期：2026-08-06 | 状态：Proposed（待拍板）| 范围：**先设计后落地**；实施按 §8 分层 L1→L4
> 相关：[ONLINE_EVAL_DESIGN.md](./ONLINE_EVAL_DESIGN.md)（旧在线评估设计，本设计取代其轨 A/B 的对话部分）· [NOVEL_RAG_DESIGN.md](./NOVEL_RAG_DESIGN.md) · [NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md](./NARRATIVE_CHILD_PARENT_AND_DIALOGUE_QUOTA_DESIGN.md)

## 1. 背景：为什么重写

- 旧 `tests/eval/` 全套（seed 30 条 / thresholds / 800 块夹具 / quality_gates / RAGAS·DeepEval 可选测试）已于 2026-08-05 由用户删除（9ee44c0），**不恢复**。
- `scripts/dev/analysis/eval_real_retrieval.py` 仍引用已删文件（`tests/eval/rag_eval_seed.json`、`thresholds.json`），当前**一跑即崩**。
- 旧 RAGAS/DeepEval 用法「不好用」，根因自查结论见 §2 —— 是**用法与数据问题**，不是工具本身不可用；用户拍板：**可用 RAGAS/DeepEval，但须对比验证，效果好的留**。
- 本次目标：重写一套**对话检索质量评估**，第一性指标 = **检索召回的内容与用户输入相关**（用户明确）。

## 2. 根因分析：旧 RAGAS/DeepEval 为什么「不好用」

| # | 根因 | 证据 | 新设计规避 |
|---|------|------|-----------|
| 1 | **synthetic 玩具 case**：旧测试用假人物假数据（"苏瑶和顾辰重逢"），judge 分数不反映生产检索 | `test_ragas_deepeval_optional.py` 硬编码 1 条玩具 case | 评估集全部来自**真实会话 query + 已入库语料 gold**（§4） |
| 2 | **DeepEval 后端未配置即 skip**：FaithfulnessMetric 默认要 OpenAI 后端，没配好就 `pytest.skip` → 从未真正跑过 | 旧测试 `except → pytest.skip` | 后端显式指向 DeepSeek（OpenAI 兼容），启动时自检，配不上就**报错而非静默跳过** |
| 3 | **RAGAS 用 `context[:500]` 冒充 answer**：混淆检索质量与生成质量，高估 faithfulness | `eval_real_retrieval.py` 反模式（旧文档自己承认） | 严格 Retrieval-only：judge 只吃 query + 检索块，**无 answer 字段**（或显式标注不参与 faithfulness） |
| 4 | **版本过老**：`requirements-eval.txt` 锁 ragas>=0.2.0（2024 API），新版 0.3+/0.4+ API 完全不同 | requirements-eval.txt | 装新版并适配新 API；**5 case 小样先验 API 可用性**再全量（§8 L3 决策点） |
| 5 | **dialogue 稀疏、seed 覆盖差**：全库 dialogue 块约 57 块，30 条 seed 中 dialogue case 少，方差大 | ONLINE_EVAL_DESIGN §1 | 评估集以 dialogue 为主（口吻模仿真实 query ~20 条），分通道切片统计 |
| 6 | **环境坑**：venv 被 Hermes 3.11 污染，`import lancedb` 直接跑挂（pydantic_core 缺失），须 `PYTHONPATH="./venv/Lib/site-packages"` 前缀 | 本次实测 | 运行命令统一带前缀；启动时自检导入 |

## 3. 评估对象（用户已拍板）

**核心：dialogue 通道检索，分两条路径独立评估**（用户确认两者不是一回事）：

| 路径 | 检索形态 | 代码位置 | 质量含义 |
|------|----------|----------|----------|
| A. RAG dialogue（全书宽检索） | IntentRouter 路由 → `store.search(channel="dialogue")` 全书范围 | `application/novel/retrieval.py` | 检索到的对话语境是否支撑用户 query |
| B. 口吻模仿（角色窄检索） | style probe query（角色名+短语）+ `filters={"characters":[...]}` + `_extract_character_style_turns` 说话人过滤 | `core/impersonation/retrieval.py` | 目标角色台词是否被召回、说话人过滤是否准 |

两条路径**共用 `store.search(channel="dialogue")` 底层**，但 B 多三层角色化约束（probe query / 角色 filters / 说话人抽取），检索难度和失败模式不同，必须分列统计。

**narrative 通道一并评估**（用户明确"肯定要评估"）。qa / character 通道本次不做。

## 4. 数据层：评估集构建（来源 = 真实会话 + 系统自动挖掘）

### 4.1 query 来源（真实）

| 来源 | 内容 | 数量 |
|------|------|------|
| `data/sessions/imp/sessions.db` | 利姆露口吻模仿真实会话（"你对库洛艾了解多少""你和日向的关系后来怎么样了"…） | ~20 条（含寒暄类） |
| `data/sessions/sessions.db`（chat） | 通用问答真实 query（"维鲁多拉和伊芙利特的关系"…） | ~4 条 |
| **新增角色会话（建议用户建 2-3 个角色）** | 主角（维鲁多拉）+ 配角（日向/库洛艾）+ 可选异作品角色，每角色 5-10 条真实 query | 15-30 条 |

### 4.2 gold 构造（系统自动挖掘 + 人工核对）

已验证事实：**会话存储不保留引用块 ID**（assistant 消息仅 role+content，无 citations）→ 无法从旧回复自动挖"系统自认相关块"，gold 走以下来源：

| gold 来源 | 内容 | 方式 |
|-----------|------|------|
| 角色卡 `data/characters/*.json` 的 `dialogues` | speaker/content/context（利姆露卡 8 条） | **直接作为口吻模仿 gold**（已人工核对过的高质量） |
| 已入库 LanceDB 按角色过滤 | 该角色全部台词块 | 口吻模仿 gold 池（量大，供 speaker_recall 分母） |
| 真实 query 实体 → 已入库块反查 | 事实类 gold 内容 | 构建脚本产出候选 → **人工核对 2-3 条后冻结** |
| `data/dialogue_meta/` | 各卷对话抽取 | 构造补充 case（标注 `source=synthetic`，单列统计） |

- **口吻模仿 case**：会话角色（metadata 已确认 = 利姆露）→ gold = 角色卡台词 + 库里角色台词池。寒暄类 query 标 `intent=chitchat`，仅评相关性不评事实命中。
- **事实类 case**：query 实体 → 已入库块反查 gold，人工核对后进 seed。
- **narrative case**：真实 query 中情节/关系类 → gold = 对应 narrative 块内容。

### 4.3 评估集文件

`scripts/dev/eval_dialogue/data/eval_seed.json`（全新位置，**不重建 tests/eval/**）：

```json
{
  "version": 1,
  "built_from": {"imp_sessions": ["imp_bb61deae7dbd", "imp_b87155162d41"], "chat_sessions": ["953ba6e7"]},
  "cases": [
    {
      "id": "imp_01",
      "query": "你对库洛艾了解多少",
      "character": "利姆露",
      "channel": "dialogue",
      "intent": "fact",
      "source": "real_session",
      "gold_speaker": "利姆露",
      "gold_keywords": ["库洛艾"],
      "gold_block_ids": ["..."]
    }
  ]
}
```

## 5. 指标设计：三层裁判 + 对比验证

> 用户第一性要求：**检索召回内容与用户输入有关联**（相关性）。所有指标最终回答这一个问题。

### 5.1 L1 代理指标（零 LLM 成本、可 diff、每次必跑）

| 指标 | 定义 | 对应视角 |
|------|------|----------|
| `hit_at_k` | top-k 检索块文本命中 `gold_keywords`（k=5） | A+B |
| `speaker_recall` | top-k 中目标角色（含 alias）台词条数 / 该角色金标台词数 | B 口吻模仿 |
| `channel_precision` | top-k 中 `block_type ∈ {dialogue, narrative}` 与 case.channel 匹配比例 | 通道纯度 |
| `rel_proxy` | 检索 top-1 文本与 query 的关键词/实体重叠率 | 相关性近似 |

### 5.2 L2/L3 LLM judge（`EVAL_LLM_JUDGE=1` 开启，DeepSeek 后端，烧 token 默认关）

| 裁判 | 指标 | 依赖 | 说明 |
|------|------|------|------|
| L2 RAGAS（新版 0.4+） | `context_precision`、`faithfulness`(仅事实 case) | ragas + langchain-openai | Retrieval-only：只喂 question + contexts，无 answer 冒充 |
| L3 DeepEval | `ContextualPrecisionMetric`、`FaithfulnessMetric` | deepeval | 后端显式 DeepSeek（base_url 指 DeepSeek v1）；启动自检失败即报错 |
| L4 自写 judge | 相关性 0-1 + 理由（prompt：判"检索上下文是否支撑/回应 query"） | 仅 openai SDK | 第一性指标；无框架依赖，作为对照基准 |

### 5.3 对比验证（用户拍板：效果好的留）

同一 case 集上跑 L1/L2/L3/L4，报告输出：

1. 四层指标汇总表（L1 全量；L2/L3/L4 有 key 才跑）
2. **一致性分析**：L2 vs L4、L3 vs L4 同向率（同判相关/不相关比例）+ 分歧 case 清单
3. 结论：以 L4（自写相关性 judge）为语义基准，验证 RAGAS/DeepEval 是否同向；**同向 → 留框架指标作主指标；分歧大 → 弃框架、自写为准**（决策规则写入 §8 L3）

## 6. 报告契约（可 diff、可审计）

落盘：`docs/analysis/dialogue_eval/YYYY-MM-DD_<git-sha>.md` + `.json`，另存 `baseline/dialogue_eval_latest.json` 供下次 diff。

报告最小字段：

1. 环境指纹：`git_sha` / embedding 类名 / reranker 类名 / top_k / lance 块数与分型计数 / seed version / timestamp
2. 汇总表：各层指标均分 + 阈值段（advisory，不挡任何东西）
3. 逐 case 详情：query、intent、channel、top-3 检索块（global_id、block_type、说话人、台词/文本片段）、各指标得分
4. 失败 case：rel 相关 judge 分 < 0.5 或 hit=0 的 case + 初步失败归因（命中说话人但内容错位 / 完全跑偏 / 通道错误）
5. 分通道切片：dialogue vs narrative 分别汇总（dialogue 稀疏、方差大，必须分列）
6. 与上次基线 diff：逐指标涨跌，跌超 5pp 显式标红

## 7. 文件与架构

```
scripts/dev/eval_dialogue/
  build_seed.py        # L1：从 sessions.db + 已入库语料构建 eval_seed.json
  run_eval.py          # L2：生产管道检索 + L1 代理指标 + 报告骨架
  judge_ragas.py       # L3：RAGAS 新版 API 适配（Retrieval-only）
  judge_deepeval.py    # L3：DeepEval 适配（DeepSeek 后端 + 自检）
  judge_self.py        # L3：自写 DeepSeek 相关性 judge
  report.py            # L4：JSON + Markdown 报告生成 + baseline diff
  data/eval_seed.json  # 评估集（build_seed.py 产物，git 提交）
```

运行命令（统一 PYTHONPATH 前缀，规避 venv 污染）：
```bash
PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/run_eval.py
EVAL_LLM_JUDGE=1 PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/run_eval.py
```

## 8. 分层落地（L1→L4，每层独立验证独立合入）

| 层 | 内容 | 验证标准（真实输出） |
|----|------|---------------------|
| **L1** | `build_seed.py` → 生成 eval_seed.json | seed ≥ 20 case；来源字段全真实；gold 可命中（抽样人工过目） |
| **L2** | `run_eval.py` 代理指标跑通全量 Lance | 四指标可算；逐 case 详情正确；运行时长可接受（CPU 5-15 分钟） |
| **L3** | 三 judge 适配 + **5 case 小样验证**（决策点） | RAGAS/DeepEval 新版 API 能跑、不静默 skip；小样四层对照表；**决策：同向留框架 / 分歧弃框架** |
| **L4** | 报告生成 + baseline 固化 + 清理过期资产 | 报告落盘可 diff；清理清单核对（§10） |

每层结束先 commit 固化，再进下一层（版本管理原则）。

## 9. 约束与假设（用户授权自定）

- 可用 RAGAS/DeepEval（新版），但必须过 L3 对比验证；否则弃用
- LLM judge 默认关，`EVAL_LLM_JUDGE=1` 开（烧 DeepSeek token）
- 本机 CPU 推理 Qwen3，无 GPU 加速；评估脚本不挡 PR、不建 pytest 门禁（与 ONLINE_EVAL_DESIGN 的 advisory 定位一致）
- 评估只读生产数据（LanceDB / sessions / dialogue_meta），不写任何生产状态
- 寒暄类 query 单独统计，不与事实类混算

## 10. 过期资产清理（本次改动产生）

| 资产 | 处置 | 理由 |
|------|------|------|
| `scripts/dev/analysis/eval_real_retrieval.py` | **删除**（被 eval_dialogue 取代） | 引用已删文件，不可运行 |
| `requirements-eval.txt` | 改写：ragas/deepeval 升级到新版版本号 | 旧版本号 = 根因 #4 |
| `docs/NOVEL_RAG_DESIGN.md` / `ONLINE_EVAL_DESIGN.md` | 更新"离线门禁"与"在线评估"引用为新套件 | 文档过期引用 |
| `tests/eval/` 目录残留引用（git 历史） | **不恢复**，文档改为指向 eval_dialogue | 用户明确不要 |
| `data/sessions_test_ops/sessions.db` | 保留不动（非本次范围） | — |

## 11. 明确不做（边界）

- 不恢复/不重建旧 `tests/eval/` 任何文件
- 不建 PR 阻断门禁、不建 nightly workflow（本期只做本机可复现评估）
- 不评 qa / character 通道
- 不评测 Agent 最终回答质量（E2E，留给后续；本次只评检索）

## 12. 拍板点（需用户确认）

1. 评估集放 `scripts/dev/eval_dialogue/data/eval_seed.json`（全新位置），同意？
2. 报告落盘 `docs/analysis/dialogue_eval/`，同意？
3. L3 决策规则（§8）：5 case 小样后，RAGAS/DeepEval 与自写 judge 同向 → 留框架；分歧大 → 弃框架、自写为准。同意？
4. 旧 `eval_real_retrieval.py` 直接删除（git 历史可找回），同意？
