"""在同一个 Redis Lua 调用中延续旧键并执行额度校验，迁移失败时拒绝消费。"""

from __future__ import annotations

from typing import Any

_MIGRATE_KEYS_SCRIPT = """
for _, key in ipairs(KEYS) do
  if string.sub(key, 1, 10) ~= 'telepilot:' then
    return redis.error_reply('legacy migration requires telepilot namespace')
  end
  local legacy_key = string.sub(key, 11)
  if redis.call('EXISTS', key) == 0 and redis.call('EXISTS', legacy_key) == 1 then
    local payload = redis.call('DUMP', legacy_key)
    local ttl = redis.call('PTTL', legacy_key)
    if payload then
      redis.call('RESTORE', key, math.max(ttl, 0), payload)
    end
  end
end
"""


async def eval_with_legacy_keys(redis: Any, script: str, numkeys: int, *arguments: Any) -> Any:
    """原子复制本次需要的旧键（含全部 Redis 类型与 TTL），随后校验并扣账。

    不扫描全库、不提前登记完成；异常向调用方传播，由既有 fail-closed 路径拒绝。
    新键已有累计时不会被覆盖；旧键不再更新，到期后自然清理，不能用于无损回滚。
    """
    return await redis.eval(_MIGRATE_KEYS_SCRIPT + script, numkeys, *arguments)
