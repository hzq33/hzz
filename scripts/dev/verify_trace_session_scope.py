"""验证：trace 会话归属 + 评估只显示现存会话数据（已删会话自动消失）。

覆盖：
1. append_trace 在请求上下文（ContextVar）下自动附加 session_id
2. filter_traces_for_api 的 active_session_ids 过滤（无归属旧数据 / 已删会话排除）
3. 删除会话 → 评估结果不再包含该会话的 trace
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.shared.rag_trace import (
    append_trace,
    build_case_list,
    filter_traces_for_api,
    load_traces,
    summarize_traces,
)
from src.shared.request_context import (
    get_session_id,
    reset_session_id,
    set_session_id,
)

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures += 1


# ── 1. ContextVar 传播 ──
check("默认无 session_id", get_session_id() is None)
tok = set_session_id("imp_test123")
check("set 后可取", get_session_id() == "imp_test123")
reset_session_id(tok)
check("reset 后恢复 None", get_session_id() is None)

# ── 2. append_trace 附加 session_id（临时目录）──
import os

with tempfile.TemporaryDirectory() as tmp:
    os.environ["RAG_TRACE_DIR"] = tmp
    os.environ["RAG_TRACE_ENABLED"] = "1"

    # 无上下文：不附加 session_id
    append_trace({"kind": "store_search", "query": "q_legacy", "hits": [], "zero_hit": True})
    # 有上下文：附加 session_id
    tok = set_session_id("imp_alive")
    append_trace({"kind": "store_search", "query": "q_alive", "hits": [{"x": 1}], "zero_hit": False})
    append_trace({"kind": "store_search", "query": "q_alive2", "hits": [], "zero_hit": True})
    reset_session_id(tok)
    # 另一个会话
    tok2 = set_session_id("imp_doomed")
    append_trace({"kind": "store_search", "query": "q_doomed", "hits": [], "zero_hit": True})
    reset_session_id(tok2)

    traces = load_traces()
    check("共 4 条 trace", len(traces) == 4, str(len(traces)))
    by_q = {t["query"]: t for t in traces}
    check("无上下文旧数据不带 session_id", "session_id" not in by_q["q_legacy"])
    check("活跃会话 trace 带 session_id", by_q["q_alive"].get("session_id") == "imp_alive")
    check("第二个会话带 session_id", by_q["q_doomed"].get("session_id") == "imp_doomed")

    # ── 3. 过滤：只保留现存会话 ──
    # 场景 A：所有会话都存在（imp_alive + imp_doomed）→ 无归属旧数据被排除
    all_active = filter_traces_for_api(
        traces, limit=100, active_session_ids={"imp_alive", "imp_doomed"}
    )
    check("只保留现存会话（3 条）", len(all_active) == 3, f"实际 {len(all_active)}")
    check("旧数据（无归属）被排除",
          all(q != "q_legacy" for q in [t["query"] for t in all_active]))

    # 场景 B：imp_doomed 已删除 → 其 trace 消失
    after_delete = filter_traces_for_api(
        traces, limit=100, active_session_ids={"imp_alive"}
    )
    check("已删会话 trace 消失（2 条）", len(after_delete) == 2, f"实际 {len(after_delete)}")
    check("q_doomed 不再出现",
          all(q != "q_doomed" for q in [t["query"] for t in after_delete]))

    # 场景 C：无 active_session_ids（旧行为，脚本复用）→ 不过滤
    no_filter = filter_traces_for_api(traces, limit=100)
    check("不传过滤参数 = 旧行为（4 条）", len(no_filter) == 4, str(len(no_filter)))

    # ── 4. 概览统计一致 ──
    summ = summarize_traces(after_delete)
    check("概览 total=2", summ["total"] == 2, str(summ["total"]))
    check("概览 zero_hit=1（q_alive2 零命中）", summ["zero_hit"] == 1, str(summ["zero_hit"]))

    # ── 5. build_case_list 输出包含 session_id 吗？前端暂不需要，仅确认不崩 ──
    cases = build_case_list(after_delete)
    check("build_case_list 正常", len(cases) == 2)

print("\n" + ("✅ 全部通过" if failures == 0 else f"❌ {failures} 项失败"))
raise SystemExit(1 if failures else 0)
