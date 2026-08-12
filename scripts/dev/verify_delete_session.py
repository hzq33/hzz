"""端到端验证角色扮演"删除会话"链路（临时目录，不碰真实数据）。

模拟前端调用链：
SessionDrawer → store.deleteSession → DELETE /sessions/{id} → service.delete_session
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from src.application.impersonation_sessions import ImpersonationSessionService
from src.shared.session_factory import create_session_store
from src.shared.session_store import is_safe_session_id


def make_store(dir: Path, backend: str = "sqlite"):
    return create_session_store(
        namespace="imp",
        base_dir=dir / "sessions",
        backend=backend,
    )


async def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
        if not cond:
            failures += 1

    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(Path(tmp))
        svc = ImpersonationSessionService(
            max_sessions=5,
            session_dir=Path(tmp) / "sessions",
            session_store=store,
        )

        # 1. 模拟两个会话：一个"活跃（在内存）"，一个"仅落盘"
        live_id = svc.new_session_id()
        cold_id = svc.new_session_id()
        check("生成合法 session_id", is_safe_session_id(live_id) and is_safe_session_id(cold_id))

        # 活跃会话：塞入内存（带 agent 桩）
        fake_agent = type(
            "FakeAgent",
            (),
            {
                "memory": type(
                    "FakeMemory",
                    (),
                    {
                        "get_messages": lambda self: [
                            {"role": "user", "content": "hi"},
                            {"role": "assistant", "content": "hello"},
                        ]
                    },
                )()
            },
        )()
        svc._sessions[live_id] = {"agent": fake_agent, "character": "测试角色", "title": "活跃存档"}
        svc._store.save(live_id, [{"role": "user", "content": "hi"}], metadata={"character": "测试角色", "title": "活跃存档"})

        # 冷会话：只落盘
        svc._store.save(cold_id, [{"role": "user", "content": "cold"}], metadata={"character": "测试角色", "title": "冷存档"})

        summaries = svc.list_summaries()
        check("列表包含两个会话", len(summaries) == 2, f"实际 {len(summaries)}")

        # 2. 删除活跃会话
        ok_live = svc.delete_session(live_id)
        check("删除活跃会话返回 True", ok_live is True)
        check("内存已移除", live_id not in svc._sessions)
        check("锁已移除", live_id not in svc._locks)
        check("磁盘已删除", store.load(live_id) is None)

        # 3. 删除冷会话
        check("冷会话已落盘", store.load(cold_id) is not None)
        ok_cold = svc.delete_session(cold_id)
        check("删除冷会话返回 True", ok_cold is True)
        check("磁盘已删除", store.load(cold_id) is None)

        # 4. 重复删除 → False（前端会收到 404 → ApiError "删除存档失败"）
        again = svc.delete_session(cold_id)
        check("重复删除返回 False（404 语义）", again is False)

        # 5. 列表已清空
        check("删除后列表为空", svc.list_summaries() == [])

        # 6. 删除会话后历史读不到
        hist = svc.load_history(cold_id)
        check("删除后 load_history 为 None", hist is None)

        # 7. 模拟 HTTP 层（URL 编码 + sanitize）
        weird_id = "imp_deadbeef.1-2_3"
        svc._store.save(weird_id, [{"role": "user", "content": "x"}], metadata={"character": "c", "title": "t"})
        ok_weird = svc.delete_session(weird_id)
        check("带 . - _ 的 id 可删除", ok_weird is True)

    print("\n" + ("✅ 全部通过" if failures == 0 else f"❌ {failures} 项失败"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
