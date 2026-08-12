"""统一 store 广播 — 上传新书后向所有持 store 的工具注入同一实例。

背景：此前只有 ``builtin_novel``（novel_search）在 server 重建 store 时被
``inject_store``；character_kb / story_analysis / novel_admin 各自懒加载
独立 store 实例，持有旧连接/旧索引，与 novel_search 行为不一致（新书检索
不到、block 统计对不上）。

本模块集中持有工具实例的注册表，server 在重建共享 store 后调用
``broadcast_store()`` 一次，让所有工具切换到同一实例。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("agent")

# 持有 store 的工具实例注册表（注册后由 broadcast 统一刷新）
_holdings: list = []


def register_store_holder(holder) -> None:
    """注册一个支持 ``inject_store`` 的工具实例。"""
    if holder is None:
        return
    if not hasattr(holder, "inject_store"):
        logger.warning("register_store_holder: %r has no inject_store", holder)
        return
    if holder not in _holdings:
        _holdings.append(holder)
        logger.debug("Store holder registered: %s", type(holder).__name__)


def broadcast_store(store) -> None:
    """向所有已注册工具注入同一 store 实例。"""
    injected = 0
    for holder in list(_holdings):
        try:
            holder.inject_store(store)
            injected += 1
        except Exception as exc:  # noqa: BLE001 - 单工具失败不阻断整体
            logger.warning("Store broadcast failed for %s: %s", type(holder).__name__, exc)
    if injected:
        logger.info("Broadcast store to %d tool(s)", injected)


def clear_holdings() -> None:
    """清空注册表（测试隔离用）。"""
    _holdings.clear()
