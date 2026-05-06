"""风控模块内部异常。

外部调用方一般只需要捕获 ``AccountPaused``（账号被风控暂停时不应继续重试）。
``FloodWaitTriggered`` 是装饰器在向上抛 Telethon ``FloodWaitError`` 之前包了一层
便于日志区分；调用方若不关心可让其继续向上抛即可，engine 内部已经写过 override 与事件。
"""

from __future__ import annotations


class RateLimitError(Exception):
    """风控基类。所有由本模块抛出的异常都继承自它。"""


class AccountPaused(RateLimitError):
    """账号已被风控暂停（``policy=pause`` 触发或外部强制暂停）。

    收到该异常表示当前 worker 不应再发起任何主动 TG 调用，应停止 / 退出循环。
    """


class FloodWaitTriggered(RateLimitError):
    """Telegram 抛了 ``FloodWaitError``，已被 engine 处理（写入 override + 落事件）。

    保留 ``seconds`` 与 ``action`` 字段供上层做额外业务处理（例如把当前任务挂起）。
    """

    def __init__(self, seconds: int, action: str) -> None:
        super().__init__(f"FloodWait {seconds}s on {action}")
        self.seconds = int(seconds)
        self.action = action
