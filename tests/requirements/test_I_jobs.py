"""需求域 I — 任务与存储：Job 生命周期 / 清理 / 孤儿恢复 / 会话并发锁。

覆盖 docs/REQUIREMENTS.md I-01 ~ I-03。
黑盒：走公开 SqliteJobStore 与 ConversationService 接口。
"""

from __future__ import annotations

import asyncio

from src.shared.sqlite_job_store import SqliteJobStore


def _store(tmp_path) -> SqliteJobStore:
    return SqliteJobStore(db_path=tmp_path / "jobs.db")


# ── I-01 Job 存储与生命周期 ───────────────────────────────────


def test_job_create_get_roundtrip(tmp_path):
    """I-01：Job 创建 → 读回（字段完整）。"""
    store = _store(tmp_path)
    job = store.create("test_job", payload={"k": "v"})
    got = store.get(job.job_id, job_type="test_job")
    assert got is not None
    assert got.job_id == job.job_id
    assert got.state == "pending"
    assert got.payload == {"k": "v"}


def test_job_state_transitions(tmp_path):
    """I-01：Job 状态 running → done 可持久化读回。"""
    store = _store(tmp_path)
    job = store.create("test_job")
    job.state = "running"
    store.save(job)
    job.state = "done"
    job.result = {"ok": True}
    store.save(job)
    got = store.get(job.job_id, job_type="test_job")
    assert got.state == "done"
    assert got.result == {"ok": True}


def test_job_unknown_id_returns_none(tmp_path):
    """I-01：查询不存在 Job → None（调用方转 404，不 500）。"""
    store = _store(tmp_path)
    assert store.get("no-such-job", job_type="test_job") is None


def test_job_ttl_cleanup(tmp_path):
    """I-01：过期 Job（AGENT_JOB_TTL_HOURS 语义）可被清理。"""
    from datetime import datetime, timedelta, timezone

    store = _store(tmp_path)
    job = store.create("test_job")
    job.state = "done"
    store.save(job)
    assert store.list(job_type="test_job")
    # 环境准备：把 DB 中的 updated_at 回拨 3 小时（save() 会刷新时间戳）
    conn = store._connect()
    conn.execute(
        "UPDATE jobs SET updated_at = ? WHERE job_id = ?",
        ((datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(), job.job_id),
    )
    conn.commit()
    conn.close()
    removed = store.cleanup_older_than(max_age_hours=1)
    assert removed >= 1
    assert not store.list(job_type="test_job")


def test_orphan_running_jobs_marked_failed(tmp_path):
    """I-01：重启时 running 孤儿标记 failed（orphan_after_restart）。"""
    store = _store(tmp_path)
    job = store.create("test_job")
    job.state = "running"
    store.save(job)
    marked = store.mark_orphans_failed()
    assert marked >= 1
    got = store.get(job.job_id, job_type="test_job")
    assert got.state == "failed"


def test_job_list_and_progress(tmp_path):
    """I-01：Job 列表 + 进度更新（轮询 UX）。"""
    store = _store(tmp_path)
    job = store.create("test_job")
    store.update_progress(job.job_id, {"pct": 50, "stage": "ingest"}, job_type="test_job")
    got = store.get(job.job_id, job_type="test_job")
    assert got.progress.get("pct") == 50


# ── I-03 会话并发 ─────────────────────────────────────────────


def test_session_lock_serializes_concurrent_turns(tmp_path):
    """I-03：同一会话并发请求被 per-session lock 串行化。"""
    from src.application.conversation_service import ConversationService

    service = ConversationService(max_sessions=5, session_dir=tmp_path / "chat")
    order: list[str] = []
    entered = asyncio.Event()

    async def turn(name: str):
        config_factory = lambda: None  # noqa: E731 - 仅测锁，不建真实会话
        async with service.locked_session("shared-session", config_factory):
            order.append(f"{name}-enter")
            if name == "first":
                entered.set()
                await asyncio.sleep(0.1)  # 占用锁
            else:
                # 若锁未串行，first 未释放时 second 就会进入
                pass
            order.append(f"{name}-exit")

    async def run():
        t1 = asyncio.create_task(turn("first"))
        await entered.wait()
        t2 = asyncio.create_task(turn("second"))
        await asyncio.gather(t1, t2)

    asyncio.run(run())
    # 锁保证 first 完整退出后 second 才进入
    first_exit = order.index("first-exit")
    second_enter = order.index("second-enter")
    assert first_exit < second_enter, "并发 turn 应被串行化"


# ── I 补充：事件总线 ──────────────────────────────────────────


def test_event_bus_publish_dispatch():
    """I：事件总线发布触发订阅者（会话事件解耦）。"""
    from src.application.event_bus import EventBus
    from src.domain.events import SessionCreated, SessionCleared

    bus = EventBus()
    got: list[str] = []
    bus.subscribe(SessionCreated, lambda e: got.append(f"created:{e.session_id}"))
    bus.subscribe(SessionCleared, lambda e: got.append(f"cleared:{e.session_id}"))
    bus.publish(SessionCreated(session_id="s1"))
    bus.publish(SessionCleared(session_id="s1"))
    assert got == ["created:s1", "cleared:s1"]
    bus.clear()
    bus.publish(SessionCreated(session_id="s2"))
    assert got == ["created:s1", "cleared:s1"], "clear 后不再派发"
