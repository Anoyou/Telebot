"""主进程 ↔ worker 之间的 IPC 协议。

通信通道命名约定（Redis pub/sub）：
- ``worker_cmd:{account_id}``    主进程 → worker  下发指令
- ``worker_event:{account_id}``  worker → 主进程  上报事件 / 日志 / 限速事件
- ``worker_global``              广播指令（全员适用，例如 kill switch 切换）

消息使用 JSON，统一字段：
    { "type": "...", "ts": <epoch_ms>, "payload": { ... } }
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


# ── Channel 模板 ──────────────────────────────────────────────────
def cmd_channel(account_id: int) -> str:
    return f"worker_cmd:{account_id}"


def event_channel(account_id: int) -> str:
    return f"worker_event:{account_id}"


GLOBAL_CHANNEL = "worker_global"
RUNTIME_LOG_STREAM = "runtime_log_stream"          # 主进程消费此 list 落库
RATELIMIT_EVENT_STREAM = "ratelimit_event_stream"  # 主进程消费此 list 落库


# ── 指令类型（主→worker） ────────────────────────────────────────
CMD_PAUSE = "pause"
CMD_RESUME = "resume"
CMD_STOP = "stop"
CMD_RELOAD_CONFIG = "reload_config"        # 拉新风控/拟人化配置
CMD_RELOAD_PLUGIN = "reload_plugin"        # payload: {plugin_key}
# 自定义命令模板 / LLM provider 变化后通知 worker 热加载（无 payload）
CMD_RELOAD_COMMANDS = "reload_commands"
CMD_RUN_TG_COMMAND = "run_tg_command"      # 用于 Web 触发 TG 命令（可选）
CMD_PING = "ping"
# 让 worker 把当前账号头像下载到本地磁盘缓存（payload: {"path": "<绝对路径>"}）
# 主进程的 ensure_avatar 用 fire-and-forget 方式发送，worker 写盘后由下次请求读到
CMD_FETCH_AVATAR = "fetch_avatar"
# 通知 worker 重新拉取忽略名单（账号忽略 peer 增删后下发；payload 为空）
CMD_RELOAD_IGNORED = "reload_ignored"
# RPC：拉 worker 内存中的最近活跃 peer（payload: {"reply_to": <一次性应答频道>}）
CMD_GET_RECENT_PEERS = "get_recent_peers"

# ── 事件类型（worker→主） ──────────────────────────────────────
EVT_STATUS = "status"                      # payload: {status: active|paused|...}
EVT_LOG = "log"                            # payload: {level, source, message, detail}
EVT_RATELIMIT = "ratelimit"                # payload: {action, outcome, detail}
EVT_PLUGIN_STATE = "plugin_state"          # payload: {feature_key, state, last_error?}
EVT_LOGIN_REQUIRED = "login_required"      # session 失效
EVT_PONG = "pong"


# ── 全局指令 ────────────────────────────────────────────────
GCMD_KILL_SWITCH = "kill_switch"           # payload: {enabled: bool}
GCMD_RELOAD_GLOBAL = "reload_global"


@dataclass
class IPCMessage:
    """统一的 IPC 消息结构。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def decode(cls, raw: str | bytes) -> IPCMessage:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        d = json.loads(raw)
        return cls(type=d["type"], payload=d.get("payload") or {}, ts=d.get("ts") or 0)


# ── 便捷构造函数 ──────────────────────────────────────────────
def make_cmd(type_: str, **payload: Any) -> str:
    return IPCMessage(type=type_, payload=payload).encode()


def make_event(type_: str, **payload: Any) -> str:
    return IPCMessage(type=type_, payload=payload).encode()


# Worker -> 主进程：限速事件结构（也用于直接写 RATELIMIT_EVENT_STREAM）
@dataclass
class RateLimitEventPayload:
    account_id: int
    action: str
    outcome: str
    detail: dict[str, Any] | None = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False)


# Worker -> 主进程：运行日志结构
@dataclass
class RuntimeLogPayload:
    account_id: int | None
    level: Literal["debug", "info", "warn", "error"]
    source: str | None
    message: str
    detail: dict[str, Any] | None = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False)
