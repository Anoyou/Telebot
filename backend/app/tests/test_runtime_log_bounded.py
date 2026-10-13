"""runtime_log_stream 有界写入不变量测试。

覆盖：
- helper 在 pipeline 可用时同管道执行 RPUSH + LTRIM
- pipeline 不可用 / 执行失败时退化为逐条命令，且 ltrim 仍被调用
- 三个生产者入口（worker/runtime、plugin loader、account_bot_runtime）
  经 mock redis 断言 ltrim 一定被调用，防回退成裸 RPUSH
- 静态不变量：仓库中除 helper 外不再出现裸的 ``RUNTIME_LOG_STREAM`` 写入
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import account_bot_runtime
from app.worker import runtime as runtime_mod
from app.worker.ipc import RUNTIME_LOG_STREAM, RuntimeLogPayload
from app.worker.plugins import loader as loader_mod
from app.worker.runtime_log import RUNTIME_LOG_MAX_LEN, push_runtime_log_bounded

PRODUCER_FILES = (
    "app/services/account_bot_runtime.py",
    "app/worker/plugins/loader.py",
    "app/worker/runtime.py",
)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class _RecordingRedis:
    """带 pipeline 的 redis stub：记录所有命令调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def pipeline(self) -> _RecordingPipeline:
        return _RecordingPipeline(self)

    async def rpush(self, key: str, value: bytes) -> None:
        self.calls.append(("rpush", (key, value)))

    async def ltrim(self, key: str, start: int, stop: int) -> None:
        self.calls.append(("ltrim", (key, start, stop)))


class _RecordingPipeline:
    def __init__(self, redis: _RecordingRedis) -> None:
        self._redis = redis
        self._calls: list[tuple[str, tuple]] = []

    def rpush(self, key: str, value: bytes) -> None:
        self._calls.append(("rpush", (key, value)))

    def ltrim(self, key: str, start: int, stop: int) -> None:
        self._calls.append(("ltrim", (key, start, stop)))

    async def execute(self) -> list:
        for name, args in self._calls:
            self._redis.calls.append((name, args))
        return []


class _NoPipelineRedis(_RecordingRedis):
    """无 pipeline 的 redis stub（例如轻量客户端或测试替身）。"""

    def pipeline(self):  # noqa: ANN201
        raise AssertionError("pipeline 不可用时应走逐条分支")


class _BrokenPipelineRedis(_RecordingRedis):
    def pipeline(self):  # noqa: ANN201
        raise RuntimeError("pipeline 不可用")


def _ltrim_calls(redis: _RecordingRedis) -> list[tuple[str, int, int]]:
    """收集所有 ltrim 调用，兼容 pipeline / 逐条两种记录形态。"""
    calls: list[tuple[str, int, int]] = []
    for name, args in redis.calls:
        if name != "ltrim" or len(args) != 3:
            continue
        key, start, stop = args
        calls.append((key, start, stop))
    return calls


@pytest.mark.asyncio
async def test_push_runtime_log_bounded_uses_pipeline_with_ltrim() -> None:
    redis = _RecordingRedis()
    await push_runtime_log_bounded(redis, '{"k":1}')

    assert [name for name, _ in redis.calls] == ["rpush", "ltrim"]
    assert redis.calls[0][1] == (RUNTIME_LOG_STREAM, '{"k":1}')
    assert redis.calls[1][1] == (RUNTIME_LOG_STREAM, -RUNTIME_LOG_MAX_LEN, -1)


@pytest.mark.asyncio
async def test_push_runtime_log_bounded_falls_back_without_pipeline() -> None:
    redis = _NoPipelineRedis()
    await push_runtime_log_bounded(redis, '{"k":2}')

    assert [name for name, _ in redis.calls] == ["rpush", "ltrim"]
    assert _ltrim_calls(redis) == [(RUNTIME_LOG_STREAM, -RUNTIME_LOG_MAX_LEN, -1)]


@pytest.mark.asyncio
async def test_push_runtime_log_bounded_retries_after_pipeline_failure() -> None:
    redis = _BrokenPipelineRedis()
    await push_runtime_log_bounded(redis, "payload")

    assert [name for name, _ in redis.calls] == ["rpush", "ltrim"]
    assert _ltrim_calls(redis) == [(RUNTIME_LOG_STREAM, -RUNTIME_LOG_MAX_LEN, -1)]


@pytest.mark.asyncio
async def test_producer_worker_runtime_always_trims() -> None:
    redis = _RecordingRedis()
    await runtime_mod._log(redis, 7, "info", "hello", source="system", trace_id="t")

    assert _ltrim_calls(redis) == [(RUNTIME_LOG_STREAM, -RUNTIME_LOG_MAX_LEN, -1)]
    assert redis.calls[0][0] == "rpush"


@pytest.mark.asyncio
async def test_producer_plugin_loader_always_trims() -> None:
    redis = _RecordingRedis()
    await loader_mod._log(redis, 7, "info", "plugin log")

    assert _ltrim_calls(redis) == [(RUNTIME_LOG_STREAM, -RUNTIME_LOG_MAX_LEN, -1)]


@pytest.mark.asyncio
async def test_producer_plugin_loader_trims_without_pipeline() -> None:
    redis = _NoPipelineRedis()
    await loader_mod._log(redis, None, "warning", "plugin log no pipeline")

    assert _ltrim_calls(redis) == [(RUNTIME_LOG_STREAM, -RUNTIME_LOG_MAX_LEN, -1)]


@pytest.mark.asyncio
async def test_producer_account_bot_runtime_always_trims(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _RecordingRedis()
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)
    incoming = SimpleNamespace(account_id=7, trace_id="trace-1", message_id=1)
    await account_bot_runtime._write_interaction_runtime_log(incoming, "info", "interaction log")

    assert _ltrim_calls(redis) == [(RUNTIME_LOG_STREAM, -RUNTIME_LOG_MAX_LEN, -1)]
    assert "trace-1" in redis.calls[0][1][1]


def _stream_write_calls(tree: ast.AST) -> list[int]:
    """返回源码里所有直接以 ``RUNTIME_LOG_STREAM`` 为参数的写入调用行号。"""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else None
        if attr not in {"rpush", "lpush", "rpushx", "lpushx"}:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id == "RUNTIME_LOG_STREAM":
                lines.append(node.lineno)
                break
    return lines


def test_no_raw_runtime_log_stream_writes_outside_helper() -> None:
    """静态不变量：除 helper 外，仓库里没有裸的 stream 写入。"""
    offenders: list[str] = []
    for path in _BACKEND_ROOT.rglob("*.py"):
        if "build" in path.parts or ".venv" in path.parts:
            continue
        relative = path.relative_to(_BACKEND_ROOT).as_posix()
        if relative == "app/worker/runtime_log.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - 解析失败不应掩盖
            continue
        lines = _stream_write_calls(tree)
        if lines:
            offenders.append(f"{relative}: {lines}")

    assert offenders == [], f"发现绕过 helper 的 runtime_log_stream 写入: {offenders}"


def test_no_raw_runtime_log_stream_writes_in_known_producers() -> None:
    """三个热点生产者文件里只允许出现 helper 调用。"""
    for relative in PRODUCER_FILES:
        source = (_BACKEND_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert _stream_write_calls(tree) == [], relative
        assert "push_runtime_log_bounded" in source, relative


def test_helper_payload_roundtrip_matches_runtime_log_payload() -> None:
    payload = RuntimeLogPayload(
        account_id=3,
        level="info",
        source="event",
        message="m",
        detail={"trace_id": "x"},
    )
    assert '"trace_id":"x"' in payload.encode()
