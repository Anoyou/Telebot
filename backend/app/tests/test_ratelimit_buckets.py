"""TokenBuckets 测试：用纯 Python fake redis 复现 Lua 脚本语义，验证 ``check_and_consume``。

由于 CI 环境没有 fakeredis 也没有 Redis 实例，这里直接用一个最小内存替身重放脚本逻辑。
重点验证：
  - 阈值未到 → allowed
  - 阈值到顶 → not allowed + retry_after > 0
  - 多窗口任一超限即挡
  - same_peer 桶按 peer_id 隔离
  - consume=False 不扣减
"""

from __future__ import annotations

import time

import pytest

from app.worker.ratelimit.buckets import _SPANS, TokenBuckets


# ─────────────────────────────────────────────────────
# 最小内存 fake redis：只实现 TokenBuckets 用到的方法
# ─────────────────────────────────────────────────────
class _FakeRedis:
    """模拟 redis-py 异步客户端，复现 Lua 脚本的滑动窗口语义。"""

    def __init__(self) -> None:
        # zsets: key -> list[(score, member)]
        self._zsets: dict[str, list[tuple[float, str]]] = {}

    async def script_load(self, script: str) -> str:
        return "fake-sha"

    async def evalsha(self, sha: str, numkeys: int, *args):
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        n = int(argv[0])
        spans = [int(argv[1 + i]) for i in range(n)]
        limits = [int(argv[1 + n + i]) for i in range(n)]
        consume = int(argv[2 * n + 1])
        now = int(time.time())
        # 检查
        for i in range(n):
            limit = limits[i]
            if limit <= 0:
                continue
            k = keys[i]
            self._cleanup(k, now - spans[i])
            cur = len(self._zsets.get(k, []))
            if cur >= limit:
                oldest = self._zsets[k][0][0]
                retry = spans[i] - (now - oldest)
                return [0, max(0, retry), i + 1]
        if consume == 1:
            for i in range(n):
                limit = limits[i]
                if limit <= 0:
                    continue
                k = keys[i]
                self._zsets.setdefault(k, []).append((now, f"{now}-{i}"))
                self._zsets[k].sort()
        return [1, 0, 0]

    async def zremrangebyscore(self, key: str, mn, mx) -> int:
        self._cleanup(key, mx)
        return 0

    async def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, []))

    def _cleanup(self, key: str, before: float) -> None:
        if key not in self._zsets:
            return
        self._zsets[key] = [(s, m) for s, m in self._zsets[key] if s > before]


# ─────────────────────────────────────────────────────
# 用例
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_per_minute_limit_blocks_after_quota() -> None:
    """阈值 5/分钟：连扣 5 次 OK，第 6 次被挡。"""
    buckets = TokenBuckets(_FakeRedis())
    for _ in range(5):
        allowed, retry, idx = await buckets.check_and_consume(
            account_id=1,
            action="send_message_group",
            per_second=None,
            per_minute=5,
            per_hour=None,
            per_day=None,
            same_peer_per_minute=None,
        )
        assert allowed
        assert retry == 0
    allowed, retry, idx = await buckets.check_and_consume(
        account_id=1,
        action="send_message_group",
        per_second=None,
        per_minute=5,
        per_hour=None,
        per_day=None,
        same_peer_per_minute=None,
    )
    assert not allowed
    assert idx == 2  # minute 窗口（顺序：s/m/h/d/peer）
    assert retry > 0


@pytest.mark.asyncio
async def test_per_second_takes_precedence_over_per_minute() -> None:
    """阈值 1/秒、5/分钟：第二次扣即被秒级窗口挡（idx=1）。"""
    buckets = TokenBuckets(_FakeRedis())
    a, _, _ = await buckets.check_and_consume(1, "x", 1, 5, None, None, None)
    assert a
    a, _, idx = await buckets.check_and_consume(1, "x", 1, 5, None, None, None)
    assert not a
    assert idx == 1  # 秒窗口先撞


@pytest.mark.asyncio
async def test_same_peer_isolates_per_peer() -> None:
    """same_peer_per_minute=2：peer A 扣 2 次后被挡，peer B 不受影响。"""
    buckets = TokenBuckets(_FakeRedis())
    for _ in range(2):
        a, _, _ = await buckets.check_and_consume(
            1, "y", None, 100, None, None, 2, peer_id=10
        )
        assert a
    a, _, idx = await buckets.check_and_consume(
        1, "y", None, 100, None, None, 2, peer_id=10
    )
    assert not a
    assert idx == 5  # peer 桶在第 5 个窗口
    a2, _, _ = await buckets.check_and_consume(
        1, "y", None, 100, None, None, 2, peer_id=20
    )
    assert a2  # 不同 peer 互不影响


@pytest.mark.asyncio
async def test_consume_false_does_not_decrement() -> None:
    """consume=False 仅查询不消费：连查 100 次都不会撞阈值 5。"""
    buckets = TokenBuckets(_FakeRedis())
    for _ in range(100):
        a, _, _ = await buckets.check_and_consume(
            1, "z", None, 5, None, None, None, consume=False
        )
        assert a


@pytest.mark.asyncio
async def test_zero_limits_skip_window() -> None:
    """阈值全 0 → 不限制，永远 allowed。"""
    buckets = TokenBuckets(_FakeRedis())
    for _ in range(50):
        a, retry, idx = await buckets.check_and_consume(
            1, "free", 0, 0, 0, 0, 0
        )
        assert a
        assert retry == 0
        assert idx == 0


@pytest.mark.asyncio
async def test_peer_id_none_disables_same_peer_bucket() -> None:
    """peer_id=None 时同会话桶强制视作 0：即便配了阈值也不挡。"""
    buckets = TokenBuckets(_FakeRedis())
    for _ in range(10):
        a, _, _ = await buckets.check_and_consume(
            1, "p", None, None, None, None, 1, peer_id=None
        )
        assert a


def test_keys_naming() -> None:
    """Key 命名：含 account_id、action、peer 段，便于运维排错。"""
    keys = TokenBuckets._keys(42, "send_message_group", peer_id=99)
    assert keys[0] == "rl:42:send_message_group:s"
    assert keys[1] == "rl:42:send_message_group:m"
    assert keys[2] == "rl:42:send_message_group:h"
    assert keys[3] == "rl:42:send_message_group:d"
    assert keys[4] == "rl:42:send_message_group:peer:99:m"

    keys2 = TokenBuckets._keys(42, "x", peer_id=None)
    assert keys2[4] == "rl:42:x:peer:none:m"


def test_spans_correct() -> None:
    """窗口长度严格按 1s/60s/3600s/86400s/60s 排列。"""
    assert _SPANS == (1, 60, 3600, 86400, 60)
