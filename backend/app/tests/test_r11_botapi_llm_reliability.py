"""第 11 轮：Bot API 限流、LLM 调用可靠性、System Agent 流式合并与插件流式编辑。"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import (
    account_bot_runtime,
    account_bot_service,
    bot_api_limits,
    llm_http_pool,
    llm_reliability,
)
from app.services import llm_runtime as _rt
from app.services.llm_client import LLMCallFailed, LLMError, LLMResult
from app.services.llm_dto import LLMProviderDTO
from app.services.llm_runtime import FallbackChain
from app.services.system_agent.stream_flow import RunEventSignals, coalesce_stream_deltas
from app.services.telegram_stream import ThrottledTextPusher
from app.worker.plugins.stream_edit import PluginStreamEditor, stream_edit

# ── Bot API：429 / retry_after 只重试单次 HTTP 调用 ──────────────────────────


class _Resp:
    def __init__(self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self._body = body
        self.content = b"x"
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._body


class _ScriptedClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.posts: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _Resp:
        self.posts.append({"url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _rate_limited(retry_after: float) -> _Resp:
    return _Resp(
        429,
        {
            "ok": False,
            "error_code": 429,
            "description": f"Too Many Requests: retry after {retry_after}",
            "parameters": {"retry_after": retry_after},
        },
    )


_OK = _Resp(200, {"ok": True, "result": {"message_id": 7}})


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(account_bot_service.asyncio, "sleep", fake_sleep)
    return recorded


@pytest.mark.asyncio
async def test_bot_api_429_retries_only_that_http_call(monkeypatch, sleeps) -> None:
    client = _ScriptedClient([_rate_limited(3), _OK])
    monkeypatch.setattr(account_bot_service, "get_bot_api_client", AsyncMock(return_value=client))

    result = await account_bot_service.call_bot_api("111:tok", "sendMessage", {"chat_id": 5, "text": "hi"})

    assert result == {"message_id": 7}
    assert len(client.posts) == 2  # 同一请求重发一次
    assert client.posts[0]["json"] == client.posts[1]["json"]
    assert 3 in sleeps  # 按 Telegram 要求等满 retry_after


@pytest.mark.asyncio
async def test_bot_api_429_over_budget_raises_typed_error(monkeypatch, sleeps) -> None:
    client = _ScriptedClient([_rate_limited(30)])
    monkeypatch.setattr(account_bot_service, "get_bot_api_client", AsyncMock(return_value=client))

    with pytest.raises(account_bot_service.BotApiRateLimitedError) as exc_info:
        await account_bot_service.call_bot_api("111:tok", "editMessageText", {"chat_id": 5, "message_id": 1, "text": "x"})

    assert exc_info.value.retry_after == 30
    assert isinstance(exc_info.value, RuntimeError)
    assert "Too Many Requests" in str(exc_info.value)
    assert len(client.posts) == 1


@pytest.mark.asyncio
async def test_bot_api_draft_never_waits_and_respects_local_block(monkeypatch, sleeps) -> None:
    client = _ScriptedClient([_rate_limited(2)])
    monkeypatch.setattr(account_bot_service, "get_bot_api_client", AsyncMock(return_value=client))

    with pytest.raises(account_bot_service.BotApiRateLimitedError):
        await account_bot_service.send_message_draft("111:tok", 9, 1, "partial")
    # 封锁期内的草稿直接本地拒绝，不再撞 Telegram
    with pytest.raises(account_bot_service.BotApiRateLimitedError):
        await account_bot_service.send_message_draft("111:tok", 9, 1, "partial 2")

    assert len(client.posts) == 1
    assert 2 not in sleeps


@pytest.mark.asyncio
async def test_bot_api_connect_error_resent_once_but_read_timeout_is_not(monkeypatch, sleeps) -> None:
    client = _ScriptedClient([httpx.ConnectError("refused"), _OK])
    monkeypatch.setattr(account_bot_service, "get_bot_api_client", AsyncMock(return_value=client))
    assert await account_bot_service.call_bot_api("111:tok", "sendMessage", {"chat_id": 5, "text": "a"}) == {"message_id": 7}
    assert len(client.posts) == 2

    client2 = _ScriptedClient([httpx.ReadTimeout("slow")])
    monkeypatch.setattr(account_bot_service, "get_bot_api_client", AsyncMock(return_value=client2))
    with pytest.raises(httpx.ReadTimeout):
        await account_bot_service.call_bot_api("111:tok", "sendMessage", {"chat_id": 5, "text": "b"})
    assert len(client2.posts) == 1  # 可能已送达，绝不重发


@pytest.mark.asyncio
async def test_get_updates_uses_dedicated_poll_pool(monkeypatch) -> None:
    send_client = _ScriptedClient([])
    poll_client = _ScriptedClient([_Resp(200, {"ok": True, "result": []})])
    monkeypatch.setattr(account_bot_service, "get_bot_api_client", AsyncMock(return_value=send_client))
    monkeypatch.setattr(account_bot_service, "get_bot_api_poll_client", AsyncMock(return_value=poll_client))

    await account_bot_service.call_bot_api("111:tok", "getUpdates", {"offset": 0, "timeout": 25})

    assert len(poll_client.posts) == 1
    assert send_client.posts == []


@pytest.mark.asyncio
async def test_real_clients_are_separate_pools() -> None:
    try:
        send = await account_bot_service.get_bot_api_client()
        poll = await account_bot_service.get_bot_api_poll_client()
        assert send is not poll
    finally:
        await account_bot_service.close_bot_api_client()


@pytest.mark.asyncio
async def test_chat_bucket_spaces_out_sends_to_one_chat() -> None:
    now = [100.0]
    waits: list[float] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)
        now[0] += delay

    limiter = bot_api_limits.BotApiRateLimiter(clock=lambda: now[0], sleep=fake_sleep)
    for _ in range(int(bot_api_limits.PRIVATE_CHAT_BURST)):
        assert await limiter.acquire("bot", 42, budget=10) == 0
    # 突发用完后，同一私聊约 1 条/秒
    waited = await limiter.acquire("bot", 42, budget=10)
    assert 0.9 <= waited <= 1.01
    # 另一会话不受影响
    assert await limiter.acquire("bot", 43, budget=10) == 0
    # 编辑不消耗会话令牌，但尊重 429 封锁期
    limiter.penalize("bot", 44, 2.5)
    assert await limiter.acquire("bot", 44, budget=10, take_chat_token=False) == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_polling_does_not_rerun_handler_after_bot_api_error(monkeypatch) -> None:
    calls = {"n": 0}

    async def handler(aid, token, update):  # noqa: ANN001
        calls["n"] += 1
        # 模拟：已发出「🤔」并调用 LLM 后，编辑时被 429（单请求层已按预算重试过）
        raise account_bot_service.BotApiRateLimitedError(
            "Too Many Requests: retry after 3", method="editMessageText", error_code=429, retry_after=3
        )

    monkeypatch.setattr(account_bot_runtime, "_write_polling_update_failure_log", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "_record_polling_dead_letter", AsyncMock())
    sleep = AsyncMock()
    monkeypatch.setattr(account_bot_runtime.asyncio, "sleep", sleep)

    await account_bot_runtime._handle_polling_update_with_retries(
        aid=1,
        token="111:tok",
        update={"update_id": 9},
        loop_name="interaction bot polling",
        loop="interaction",
        token_role="interaction",
        handler=handler,
    )

    assert calls["n"] == 1  # 副作用只发生一次
    sleep.assert_not_awaited()
    account_bot_runtime._record_polling_dead_letter.assert_awaited_once()


@pytest.mark.asyncio
async def test_polling_still_retries_errors_before_telegram(monkeypatch) -> None:
    calls = {"n": 0}

    async def handler(aid, token, update):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database temporarily unavailable")

    monkeypatch.setattr(account_bot_runtime.asyncio, "sleep", AsyncMock())
    await account_bot_runtime._handle_polling_update_with_retries(
        aid=1,
        token="111:tok",
        update={"update_id": 10},
        loop_name="interaction bot polling",
        loop="interaction",
        token_role="interaction",
        handler=handler,
    )
    assert calls["n"] == 2


# ── LLM：HTTP 客户端复用 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_http_client_is_reused_with_per_request_timeout(monkeypatch) -> None:
    seen: list[Any] = []

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        seen.append((id(self), kwargs.get("timeout")))
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    await llm_http_pool.close_llm_http_clients()
    before = dict(llm_http_pool.stats)
    try:
        async with llm_http_pool.lease_llm_http_client(timeout=httpx.Timeout(5.0), trust_env=False) as cli:
            await cli.post("https://example.invalid/v1")
        async with llm_http_pool.lease_llm_http_client(timeout=httpx.Timeout(9.0), trust_env=False) as cli:
            await cli.post("https://example.invalid/v1")
    finally:
        await llm_http_pool.close_llm_http_clients()

    assert seen[0][0] == seen[1][0]  # 同一个底层客户端
    assert seen[0][1] == httpx.Timeout(5.0)
    assert seen[1][1] == httpx.Timeout(9.0)
    assert llm_http_pool.stats["created"] - before["created"] == 1
    assert llm_http_pool.stats["reused"] - before["reused"] == 1


# ── LLM：总时限、快速切备用、熔断 ──────────────────────────────────────────


def _chain() -> FallbackChain:
    primary = LLMProviderDTO(id=11, name="primary", provider="openai", default_model="m", api_key_enc="sk")
    fallback = LLMProviderDTO(id=12, name="fallback", provider="openai", default_model="m", api_key_enc="sk")
    return FallbackChain(primary=primary, fallbacks=[fallback])


@pytest.mark.asyncio
async def test_deadline_reserves_time_so_hung_primary_reaches_fallback() -> None:
    order: list[tuple[str, Any]] = []

    class _Hung:
        async def complete(self, system, user, max_tokens=512, images=None, timeout_seconds=None):  # noqa: ANN001
            order.append(("primary", timeout_seconds))
            await asyncio.sleep(30)

    class _Ok:
        async def complete(self, system, user, max_tokens=512, images=None, timeout_seconds=None):  # noqa: ANN001
            order.append(("fallback", timeout_seconds))
            return LLMResult(text="fallback-ok", model="m", input_tokens=1, output_tokens=1)

    async def build(dto, **_kwargs):  # noqa: ANN001
        return _Hung() if dto.id == 11 else _Ok()

    started = time.monotonic()
    with patch("app.services.llm_runtime.build_client_from_dto", build):
        result, provider, used_fb = await _rt.call_with_fallback(
            _chain(), "s", "u", max_tokens=10, timeout_seconds=4, deadline_seconds=4
        )
    elapsed = time.monotonic() - started

    assert result.text == "fallback-ok"
    assert provider.id == 12 and used_fb is True
    assert order[0][0] == "primary" and order[0][1] < 4  # 主 Provider 的单次超时被收紧，给备用留时间
    assert elapsed < 4
    assert [name for name, _ in order] == ["primary", "fallback"]


@pytest.mark.asyncio
async def test_backoff_never_exceeds_deadline_on_single_provider(monkeypatch) -> None:
    calls = {"n": 0}

    class _Flaky:
        async def complete(self, system, user, max_tokens=512, images=None):  # noqa: ANN001
            calls["n"] += 1
            raise LLMError("upstream 503", retryable=True, status_code=503)

    async def build(dto, **_kwargs):  # noqa: ANN001
        return _Flaky()

    monkeypatch.setattr(_rt, "_compute_retry_delay", lambda attempt: 2.0)
    chain = FallbackChain(primary=_chain().primary)
    started = time.monotonic()
    with patch("app.services.llm_runtime.build_client_from_dto", build), pytest.raises(LLMCallFailed):
        await _rt.call_with_fallback(chain, "s", "u", max_tokens=10, deadline_seconds=4)
    # 4 s 预算只放得下一次退避重试（2 s 退避后剩余 < 3 s 最小尝试时间就停）
    assert calls["n"] <= 2
    assert time.monotonic() - started < 4.5


@pytest.mark.asyncio
async def test_circuit_breaker_skips_provider_after_consecutive_unavailability() -> None:
    order: list[str] = []

    class _Down:
        async def complete(self, system, user, max_tokens=512, images=None):  # noqa: ANN001
            order.append("primary")
            raise LLMError("connect timeout", retryable=True, category="timeout")

    class _Ok:
        async def complete(self, system, user, max_tokens=512, images=None):  # noqa: ANN001
            order.append("fallback")
            return LLMResult(text="ok", model="m", input_tokens=1, output_tokens=1)

    async def build(dto, **_kwargs):  # noqa: ANN001
        return _Down() if dto.id == 11 else _Ok()

    with patch("app.services.llm_runtime.build_client_from_dto", build):
        for _ in range(llm_reliability.CIRCUIT_FAILURE_THRESHOLD):
            await _rt.call_with_fallback(_chain(), "s", "u", max_tokens=10)
        assert llm_reliability.get_circuit_breaker().is_open(11)
        order.clear()
        result, provider, _ = await _rt.call_with_fallback(_chain(), "s", "u", max_tokens=10)

    assert provider.id == 12 and result.text == "ok"
    assert order == ["fallback"]  # 熔断期间不再打主 Provider


def test_circuit_breaker_half_open_probe_and_recovery() -> None:
    now = [0.0]
    breaker = llm_reliability.ProviderCircuitBreaker(threshold=2, open_seconds=10, clock=lambda: now[0])
    breaker.record_failure(1, "timeout")
    breaker.record_failure(1, "upstream_error")
    assert breaker.allow(1) is False
    breaker.record_failure(2, "auth_failed")  # 凭据错误不计入熔断
    breaker.record_failure(2, "auth_failed")
    assert breaker.allow(2) is True
    now[0] = 11.0
    assert breaker.allow(1) is True  # 半开：放行一次试探
    assert breaker.allow(1) is False  # 试探结果出来前其余请求继续跳过
    breaker.record_success(1)
    assert breaker.allow(1) is True


def test_attempt_timeout_reserves_fallback_share() -> None:
    with llm_reliability.deadline_scope(45):
        primary = llm_reliability.attempt_timeout(45, has_fallback=True)
        last = llm_reliability.attempt_timeout(45, has_fallback=False)
    assert 25 <= primary <= 30  # 45 s 里给备用留约 15 s
    assert last >= 44
    assert llm_reliability.attempt_timeout(30, has_fallback=True) == 30  # 没有 deadline 时原样


# ── System Agent：delta 合并 + 订阅通知 ─────────────────────────────────────


async def _gen(events: list[Any]):
    for item in events:
        if isinstance(item, float):
            await asyncio.sleep(item)
            continue
        yield item


@pytest.mark.asyncio
async def test_coalesce_first_delta_immediate_then_merged_and_ordered() -> None:
    events = [
        {"type": "run_started"},
        {"type": "assistant_delta", "delta": "你"},
        {"type": "assistant_delta", "delta": "好"},
        {"type": "assistant_delta", "delta": "，"},
        {"type": "assistant_delta_reset"},
        {"type": "assistant_delta", "delta": "世"},
        {"type": "assistant_delta", "delta": "界"},
        {"type": "tool_started", "tool_name": "x"},
        {"type": "assistant_message", "content": "你好，世界"},
    ]
    stats: dict[str, int] = {}
    out = [e async for e in coalesce_stream_deltas(_gen(events), interval=10.0, stats=stats)]
    assert [e["type"] for e in out] == [
        "run_started",
        "assistant_delta",
        "assistant_delta",
        "assistant_delta_reset",
        "assistant_delta",
        "tool_started",
        "assistant_message",
    ]
    deltas = [e["delta"] for e in out if e["type"] == "assistant_delta"]
    assert deltas == ["你", "好，", "世界"]
    assert stats["in"] == len(events) and stats["out"] == len(out)


@pytest.mark.asyncio
async def test_coalesce_flushes_on_timer_while_upstream_is_quiet() -> None:
    events = [
        {"type": "assistant_delta", "delta": "a"},
        {"type": "assistant_delta", "delta": "b"},
        {"type": "assistant_delta", "delta": "c"},
        0.3,  # 上游停顿（例如准备工具调用）
        {"type": "done"},
    ]
    stamps: list[tuple[float, dict[str, Any]]] = []
    started = time.monotonic()
    async for event in coalesce_stream_deltas(_gen(events), interval=0.05):
        stamps.append((time.monotonic() - started, event))
    merged = [(t, e) for t, e in stamps if e.get("delta") == "bc"]
    assert merged and merged[0][0] < 0.25  # 停顿期间按定时器冲出，不等下一条事件
    assert stamps[-1][1]["type"] == "done"


@pytest.mark.asyncio
async def test_run_event_signals_wake_subscriber_without_polling() -> None:
    signals = RunEventSignals()
    version = signals.subscribe("r1")

    async def notify_later() -> None:
        await asyncio.sleep(0.01)
        signals.notify("r1")

    task = asyncio.create_task(notify_later())
    started = time.monotonic()
    assert await signals.wait("r1", since_version=version, timeout=5.0) is True
    assert time.monotonic() - started < 1.0
    await task
    # 查询与等待之间到达的通知不会丢：版本号已变化，立即返回
    assert await signals.wait("r1", since_version=version, timeout=5.0) is True
    signals.unsubscribe("r1")


# ── 节流推送器 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_throttled_pusher_sends_first_immediately_then_latest_only() -> None:
    pushed: list[str] = []
    renders = {"n": 0}

    async def push(value: str) -> None:
        pushed.append(value)

    def render(value: str) -> str:
        renders["n"] += 1
        return f"<b>{value}</b>"

    pusher = ThrottledTextPusher(push, min_interval=0.2, render=render)
    text = ""
    for ch in "abcdefghij":
        text += ch
        await pusher.offer(text)
    await asyncio.sleep(0.3)
    await pusher.aclose()

    assert pushed[0] == "<b>a</b>"  # 首个快照当场发出
    assert pushed[-1] == "<b>abcdefghij</b>"  # 到点只发最新快照
    assert len(pushed) == 2
    assert renders["n"] == 2  # 只渲染真正发出的快照，不是每个 delta 全文重渲染


@pytest.mark.asyncio
async def test_throttled_pusher_backs_off_on_429() -> None:
    attempts: list[str] = []

    async def push(value: str) -> None:
        attempts.append(value)
        if len(attempts) == 1:
            raise bot_api_limits.BotApiRateLimitedError("Too Many Requests", error_code=429, retry_after=0.1)

    pusher = ThrottledTextPusher(push, min_interval=0.0)
    await pusher.offer("x")
    assert pusher.stats["rate_limited"] == 1
    await asyncio.sleep(0.2)
    await pusher.aclose()
    assert attempts == ["x", "x"]  # 封锁到期后用最新快照重发


# ── 插件流式编辑 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plugin_stream_editor_throttles_intermediate_and_finishes_via_apply(monkeypatch) -> None:
    bot_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def fake_call(token, method, payload=None, **kwargs):  # noqa: ANN001
        bot_calls.append((method, dict(payload or {}), kwargs))
        return {"message_id": 5}

    monkeypatch.setattr(account_bot_service, "call_bot_api", fake_call)
    applied: list[list[dict[str, Any]]] = []

    async def apply(actions):  # noqa: ANN001
        applied.append(actions)
        return {"ok": True, "executed": 1}

    async def token() -> str:
        return "111:tok"

    editor = PluginStreamEditor(apply=apply, token_provider=token, chat_id=-100, message_id=5, min_interval=0.5)

    async def chunks():
        for part in ["你好", "，", "这是", "流式", "回答"]:
            yield part

    result = await stream_edit(editor, chunks(), markdown="**你好，这是流式回答**")

    assert result["ok"] is True and result["text"] == "你好，这是流式回答"
    assert [m for m, _, _ in bot_calls] == ["editMessageText"]  # 0.5 s 内只有首条中间编辑
    assert bot_calls[0][1] == {"text": "你好", "disable_web_page_preview": True, "chat_id": -100, "message_id": 5}
    assert bot_calls[0][2]["rate_limit_wait_budget"] == 0.0  # 中间编辑不等限流
    final = applied[-1][0]
    assert final["type"] == "edit_message" and final["message_id"] == 5
    assert final["rich_message"] == {"markdown": "**你好，这是流式回答**"}
    assert final["send_via"] == "interaction_bot"
    assert result["stream"]["pushes"] == 1


@pytest.mark.asyncio
async def test_plugin_stream_editor_draft_mode_for_private_chat(monkeypatch) -> None:
    drafts: list[tuple[int, str]] = []

    async def fake_draft(token, chat_id, draft_id, text="", **kwargs):  # noqa: ANN001
        drafts.append((chat_id, text))
        return {}

    monkeypatch.setattr(account_bot_service, "send_message_draft", fake_draft)
    applied: list[dict[str, Any]] = []

    async def apply(actions):  # noqa: ANN001
        applied.extend(actions)
        return {"ok": True}

    async def token() -> str:
        return "111:tok"

    editor = PluginStreamEditor(apply=apply, token_provider=token, chat_id=777)
    assert editor.mode == "draft"
    await editor.append("草稿")
    await editor.finish()
    assert drafts == [(777, "草稿"), (777, "")]  # finish 先清空草稿
    assert applied[-1]["type"] == "send_message" and applied[-1]["text"] == "草稿"


@pytest.mark.asyncio
async def test_plugin_stream_editor_without_token_skips_intermediate(monkeypatch) -> None:
    call = AsyncMock()
    monkeypatch.setattr(account_bot_service, "call_bot_api", call)
    applied: list[dict[str, Any]] = []

    async def apply(actions):  # noqa: ANN001
        applied.extend(actions)
        return {"ok": True}

    async def no_token() -> None:
        return None

    editor = PluginStreamEditor(apply=apply, token_provider=no_token, inline_message_id="AAA")
    await editor.append("片段")
    result = await editor.finish()
    call.assert_not_awaited()
    assert result["stream"]["disabled"] is True
    assert applied[-1]["inline_message_id"] == "AAA" and applied[-1]["text"] == "片段"


def test_plugin_stream_editor_validates_target() -> None:
    async def apply(actions):  # noqa: ANN001
        return {}

    async def token() -> str:
        return "t"

    with pytest.raises(ValueError):
        PluginStreamEditor(apply=apply, token_provider=token, chat_id=-100)  # 群聊没有占位
    with pytest.raises(ValueError):
        PluginStreamEditor(apply=apply, token_provider=token, message_id=1)  # 缺 chat_id
