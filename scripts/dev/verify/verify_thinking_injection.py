"""A1 验证脚本：确认 SharedLLMClient 所有入口都注入 thinking disabled。

运行方式（在 D:\\tools\\agent 下）：
    PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/verify_thinking_injection.py
"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, ".")


class EmptyStream:
    """Async 空流：立即 StopAsyncIteration（模拟无内容 stream）。"""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _standard_response():
    return MagicMock(
        choices=[MagicMock(message=MagicMock(content="ok", tool_calls=[]), finish_reason="stop")],
        usage=MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model="deepseek-v4-flash",
    )


# 异步入口 mock：stream=True → 空流；否则 → 标准响应
ASYNC_CREATE = AsyncMock()


async def _async_fake_create(**kwargs):
    if kwargs.get("stream"):
        return EmptyStream()
    return _standard_response()


ASYNC_CREATE.side_effect = _async_fake_create

# 同步入口 mock：直接返回标准响应
SYNC_CREATE = MagicMock(return_value=_standard_response())


def make_client(**kw):
    from src.shared.llm import SharedLLMClient

    c = SharedLLMClient(
        primary={"base_url": "https://api.deepseek.com", "api_key": "k", "model": "deepseek-v4-flash"},
        fallback={"base_url": "https://api.deepseek.com", "api_key": "k", "model": "deepseek-v4-pro"},
        **kw,
    )
    c._async_client = MagicMock()
    c._async_client.chat.completions.create = ASYNC_CREATE
    c._sync_client = MagicMock()
    c._sync_client.chat.completions.create = SYNC_CREATE
    return c


async def main():
    from src.shared.llm import SharedLLMClient

    results = []
    # 关键：禁止 _build_client 重建真实 OpenAI 客户端（fallback 切换时会重建，
    # 覆盖我们挂的 mock）。client 实例由 make_client 手工挂 mock。
    with patch.object(SharedLLMClient, "_build_client", lambda self: None):
        return await _run_checks(SharedLLMClient, results)


async def _run_checks(SharedLLMClient, results):

    # 1. achat（异步）—— 应注入
    c = make_client()
    ASYNC_CREATE.reset_mock()
    await c.achat([{"role": "user", "content": "hi"}])
    kw = ASYNC_CREATE.call_args.kwargs
    results.append(("achat 注入", kw.get("extra_body") == {"thinking": {"type": "disabled"}}, kw.get("extra_body")))

    # 2. achat 调用点显式 extra_body 优先（合并，显式 key 不被覆盖）
    c = make_client()
    ASYNC_CREATE.reset_mock()
    await c.achat([{"role": "user", "content": "hi"}], extra_body={"custom": 1})
    kw = ASYNC_CREATE.call_args.kwargs
    eb = kw.get("extra_body")
    results.append(("achat 显式合并", eb == {"custom": 1, "thinking": {"type": "disabled"}}, eb))

    # 3. chat（同步）—— 应注入
    c = make_client()
    SYNC_CREATE.reset_mock()
    c.chat([{"role": "user", "content": "hi"}])
    kw = SYNC_CREATE.call_args.kwargs
    results.append(("chat 注入", kw.get("extra_body") == {"thinking": {"type": "disabled"}}, kw.get("extra_body")))

    # 4. achat_stream —— 空流回退 achat 也应注入
    c = make_client()
    ASYNC_CREATE.reset_mock()
    async for _ in c.achat_stream([{"role": "user", "content": "hi"}]):
        pass
    last_kw = None
    for call in ASYNC_CREATE.call_args_list:
        if not call.kwargs.get("stream"):
            last_kw = call.kwargs
    results.append((
        "achat_stream 回退注入",
        last_kw is not None and last_kw.get("extra_body") == {"thinking": {"type": "disabled"}},
        last_kw.get("extra_body") if last_kw else None,
    ))

    # 5. thinking_disabled=False 显式关闭 → 不注入
    c = make_client(thinking_disabled=False)
    ASYNC_CREATE.reset_mock()
    await c.achat([{"role": "user", "content": "hi"}])
    kw = ASYNC_CREATE.call_args.kwargs
    results.append(("关闭后不注入", kw.get("extra_body") is None, kw.get("extra_body")))

    # 6. 非 DeepSeek base_url → 不注入（auto 探测）
    c = SharedLLMClient(primary={"base_url": "https://api.openai.com/v1", "api_key": "k", "model": "gpt-4o"})
    c._async_client = MagicMock()
    c._async_client.chat.completions.create = ASYNC_CREATE
    ASYNC_CREATE.reset_mock()
    await c.achat([{"role": "user", "content": "hi"}])
    kw = ASYNC_CREATE.call_args.kwargs
    results.append(("非 DeepSeek 不注入", kw.get("extra_body") is None, kw.get("extra_body")))

    # 7. fallback 生效后（_using_fallback=True）仍注入
    c = make_client()
    c._using_fallback = True
    c._fallback_since = 0.0  # 让 _try_revert 走 probe（mock 掉）
    with patch.object(c, "_try_revert", lambda: None):
        ASYNC_CREATE.reset_mock()
        await c.achat([{"role": "user", "content": "hi"}])
    kw = ASYNC_CREATE.call_args.kwargs
    results.append(("fallback 下注入", kw.get("extra_body") == {"thinking": {"type": "disabled"}}, kw.get("extra_body")))

    ok = True
    for name, passed, detail in results:
        mark = "PASS" if passed else "FAIL"
        ok = ok and passed
        print(f"[{mark}] {name}: {detail}")
    print("=" * 50)
    print("ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
