"""Redis Lua 多窗口令牌桶（秒/分/时/天 + 同会话桶）。

所有窗口共用一段原子 Lua 脚本，避免在 Python 端做"先 ZCARD 再 ZADD"的非原子组合
带来的并发漏洞。脚本内部用 ``redis.call('TIME')`` 取秒级时间戳，避免主备时间漂移
导致排队判定不一致。

key 命名约定：
    rl:{account_id}:{action}:s          # 1 秒窗口 zset
    rl:{account_id}:{action}:m          # 60 秒窗口 zset
    rl:{account_id}:{action}:h          # 3600 秒窗口 zset
    rl:{account_id}:{action}:d          # 86400 秒窗口 zset
    rl:{account_id}:{action}:peer:{pid}:m   # 同会话 60 秒窗口 zset

每个窗口阈值若 <=0 表示该窗口不参与（继承层把空字段当作 None → 0）。
脚本返回 [allowed, retry_after_seconds, hit_window_idx]。
"""

from __future__ import annotations

# ── Lua 脚本：原子地检查 N 个窗口并按需消费 ──────────────────────
# 关键设计：
#   1) 用 KEYS 列表传入 N 个 zset key（顺序固定 = ARGV 阈值顺序）
#   2) score=now 时间戳，每次 ZADD 一个唯一 member（now-rand），ZCARD 数当前条数
#   3) 任一窗口超阈值即返回 (0, retry_after, idx)，retry_after = 该窗口最早一条 + span - now
#   4) 全通过且 consume=1 时，统一对各窗口 ZADD + EXPIRE（TTL = span * 1.2 兜底回收）
#   5) consume=0 表示仅查询不扣减，用于先做"双扣检查"（per-action + api_total）的预演
_LUA = """
-- KEYS[1..N]: 各窗口 zset key（按 ARGV[1] 给出的窗口数顺序）
-- ARGV[1]: 窗口数 N
-- ARGV[2..N+1]: 各窗口长度（秒）
-- ARGV[N+2..2N+1]: 各窗口阈值（>0 表示生效，<=0 表示该窗口跳过）
-- ARGV[2N+2]: 是否真正消费一个令牌（1）还是仅查询（0）
-- 返回：{allowed, retry_after_seconds, hit_window_idx_1based}
local now = tonumber(redis.call('TIME')[1])
local n = tonumber(ARGV[1])
local consume = tonumber(ARGV[2 * n + 2])

-- 唯一 member，避免同一秒内并发 ZADD 互相覆盖
local member = string.format('%d-%d', now, math.random(1, 1000000000))

-- ① 先做一次纯检查：任一窗口超限立即返回
for i = 1, n do
  local span = tonumber(ARGV[1 + i])
  local limit = tonumber(ARGV[1 + n + i])
  if limit and limit > 0 then
    local k = KEYS[i]
    redis.call('ZREMRANGEBYSCORE', k, '-inf', now - span)
    local cur = tonumber(redis.call('ZCARD', k))
    if cur >= limit then
      local oldest = redis.call('ZRANGE', k, 0, 0, 'WITHSCORES')
      local retry = 0
      if oldest and oldest[2] then
        retry = span - (now - tonumber(oldest[2]))
      end
      if retry < 0 then retry = 0 end
      return {0, retry, i}
    end
  end
end

-- ② 全部窗口通过 → 真正消费令牌
if consume == 1 then
  for i = 1, n do
    local span = tonumber(ARGV[1 + i])
    local limit = tonumber(ARGV[1 + n + i])
    if limit and limit > 0 then
      local k = KEYS[i]
      redis.call('ZADD', k, now, member)
      redis.call('EXPIRE', k, math.ceil(span * 1.2))
    end
  end
end
return {1, 0, 0}
"""


# 五个窗口的固定顺序（KEYS / ARGV 必须和这个顺序一致）
_WINDOWS = ("second", "minute", "hour", "day", "same_peer_minute")
_SPANS = (1, 60, 3600, 86400, 60)  # 单位：秒


class TokenBuckets:
    """对 ``RateLimitRule.{per_second/minute/hour/day/same_peer_per_minute}`` 五窗口的封装。

    单实例持有 evalsha 的 SHA 缓存；遇到 NOSCRIPT 自动重新 ``script_load``。
    """

    def __init__(self, redis) -> None:
        self.redis = redis
        # Lua 脚本上传后的 SHA（lazy 初始化）
        self._sha: str | None = None

    # ─────────────────────────────────────────────────────
    # 内部工具
    # ─────────────────────────────────────────────────────
    async def _ensure_loaded(self) -> str:
        """首次调用前把 Lua 脚本上传到 Redis，缓存 SHA 复用。"""
        if self._sha is None:
            self._sha = await self.redis.script_load(_LUA)
        return self._sha

    @staticmethod
    def _keys(account_id: int, action: str, peer_id: int | None) -> list[str]:
        """构造五个窗口对应的 Redis key（顺序与 ``_WINDOWS`` 严格一致）。"""
        base = f"rl:{account_id}:{action}"
        # 同会话桶：peer_id 为空时给一个占位 key，调用方传 same_peer_per_minute=0 让其失效
        peer_part = f"peer:{peer_id}:m" if peer_id is not None else "peer:none:m"
        return [
            f"{base}:s",
            f"{base}:m",
            f"{base}:h",
            f"{base}:d",
            f"{base}:{peer_part}",
        ]

    # ─────────────────────────────────────────────────────
    # 主接口
    # ─────────────────────────────────────────────────────
    async def check_and_consume(
        self,
        account_id: int,
        action: str,
        per_second: int | None,
        per_minute: int | None,
        per_hour: int | None,
        per_day: int | None,
        same_peer_per_minute: int | None,
        peer_id: int | None = None,
        consume: bool = True,
    ) -> tuple[bool, float, int]:
        """检查五窗口并按需扣减。

        返回 ``(allowed, retry_after_seconds, hit_window_idx)``：
          - ``allowed=True`` 表示通过；``retry_after`` 总是 0。
          - ``allowed=False`` 表示被某个窗口挡住，``hit_window_idx`` 为 1..5（顺序见 ``_WINDOWS``）。
          - ``retry_after`` 是浮点秒，调用方应当至少 sleep 这么久再重试。
        """
        sha = await self._ensure_loaded()
        keys = self._keys(account_id, action, peer_id)
        # 同会话窗口：peer_id 为空或显式禁用时强制 0
        sp_limit = (same_peer_per_minute or 0) if peer_id is not None else 0
        argv = [
            "5",
            *[str(s) for s in _SPANS],
            str(per_second or 0),
            str(per_minute or 0),
            str(per_hour or 0),
            str(per_day or 0),
            str(sp_limit),
            "1" if consume else "0",
        ]
        try:
            res = await self.redis.evalsha(sha, len(keys), *keys, *argv)
        except Exception as exc:  # 兼容 NOSCRIPT / 连接重置等
            # NOSCRIPT 重新 load 后再试一次
            msg = str(exc).upper()
            if "NOSCRIPT" not in msg:
                # 其它错误：清掉 sha 触发下次重新加载，并向上抛
                self._sha = None
                raise
            self._sha = None
            sha = await self._ensure_loaded()
            res = await self.redis.evalsha(sha, len(keys), *keys, *argv)
        # redis-py decode_responses=True 时返回 list[str|int]，这里统一转成基本类型
        allowed = int(res[0]) == 1
        retry = float(res[1])
        idx = int(res[2])
        return allowed, retry, idx

    async def usage(self, account_id: int, action: str, window: str = "minute") -> int:
        """实时查询单个窗口的当前已用量（用于风控仪表盘）。

        参数 ``window`` 取值见 ``_WINDOWS``。先 ZREMRANGEBYSCORE 清掉过期再 ZCARD，
        和 Lua 脚本的语义保持一致。
        """
        if window not in _WINDOWS:
            raise ValueError(f"未知窗口：{window}")
        idx = _WINDOWS.index(window)
        keys = self._keys(account_id, action, None)
        from time import time as _now

        now = int(_now())
        await self.redis.zremrangebyscore(keys[idx], "-inf", now - _SPANS[idx])
        return int(await self.redis.zcard(keys[idx]))

    async def usage_all_windows(self, account_id: int, action: str) -> dict[str, int]:
        """一次返回某动作四个主窗口（不含同会话）的当前用量，便于仪表盘渲染。"""
        out: dict[str, int] = {}
        for w in ("second", "minute", "hour", "day"):
            out[w] = await self.usage(account_id, action, w)
        return out
