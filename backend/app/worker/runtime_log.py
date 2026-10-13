"""runtime_log_stream 的有界写入入口。

所有 ``RUNTIME_LOG_STREAM`` 生产者都必须经过本模块的
``push_runtime_log_bounded``：语义固定为 ``RPUSH`` + ``LTRIM -5000 -1``，
pipeline 可用时在同一个管道内执行，不可用时退化为逐条调用且 ltrim 尽力而为。

统一入口的原因：消费者是主进程的可靠消费者，写入方分布在 worker 子进程与
``account_bot_runtime`` 等多个进程模块里；任何一处漏掉 ltrim 都会让
``noeviction`` Redis 键无限增长，拖垮主进程。
"""

from __future__ import annotations

import logging
from typing import Any

from .ipc import RUNTIME_LOG_STREAM

log = logging.getLogger(__name__)

# 有界队列长度上限：保留最新 N 条，防止消费者停滞时无限增长。
RUNTIME_LOG_MAX_LEN = 5000


async def push_runtime_log_bounded(redis: Any, payload: str) -> None:
    """把单条 ``RuntimeLogPayload`` 编码结果写入有界 stream。

    ``payload`` 按调用方给出的形态原样转发（``RuntimeLogPayload.encode()`` 返回
    JSON 字符串），不在这里做二次编码，保持与既有 redis 客户端调用一致的语义。

    - pipeline 可用：``RPUSH`` + ``LTRIM`` 同管道执行，消费者看到的就是已截断状态。
    - pipeline 不可用或执行失败：退化为逐条 ``RPUSH`` + ``LTRIM``（ltrim 尽力而为）。
    """
    pipe = getattr(redis, "pipeline", None)
    if callable(pipe):
        try:
            p = redis.pipeline()
            p.rpush(RUNTIME_LOG_STREAM, payload)
            p.ltrim(RUNTIME_LOG_STREAM, -RUNTIME_LOG_MAX_LEN, -1)
            await p.execute()
            return
        except Exception:  # noqa: BLE001
            # pipeline 失败不放弃写入，退回逐条命令由调用方原有语义继续。
            log.debug("runtime log pipeline 失败，退化为逐条写入", exc_info=True)

    await redis.rpush(RUNTIME_LOG_STREAM, payload)
    try:
        await redis.ltrim(RUNTIME_LOG_STREAM, -RUNTIME_LOG_MAX_LEN, -1)
    except Exception:  # noqa: BLE001
        log.debug("runtime log ltrim 失败，仅保留 rpush 语义", exc_info=True)
