# 契约层参考（仅供子 Agent 阅读，不要修改）

> 本文件由主 Claude 在并行开工前生成，用于约束 6 个并行子 Agent 的接口边界。
> 所有 Agent **只读** 此文件以及下面列出的契约文件，**不得修改**。

## 已就绪的契约文件（不要改动）

| 文件 | 内容 |
|---|---|
| `backend/pyproject.toml` | Python 依赖清单 |
| `backend/app/settings.py` | `settings` 全局配置单例 |
| `backend/app/crypto.py` | `encrypt_str/decrypt_str/encrypt_bytes/decrypt_bytes` |
| `backend/app/redis_client.py` | `get_redis()` 异步 Redis 客户端 |
| `backend/app/deps.py` | `CurrentUser`、`DBSession` 依赖 |
| `backend/app/db/base.py` | `Base`、`engine`、`AsyncSessionLocal` |
| `backend/app/db/session.py` | `get_db()` |
| `backend/app/db/models/*.py` | 全部 14 张表模型 |
| `backend/app/schemas/*.py` | API 出入参 |
| `backend/app/worker/ipc.py` | IPC 协议常量与编解码 |
| `backend/alembic/env.py` | 已配置 metadata，autogenerate 直接可用 |

## 通用约定

### 注释 / 文档字符串

- 全部用**中文**
- 模块顶部一句话说明用途
- 复杂函数加 docstring

### 错误返回格式

```json
{ "error": { "code": "...", "message": "..." } }
```

`HTTPException(status_code=4xx, detail={"code":"X","message":"Y"})` + 全局异常处理器统一转换。

### 加密字段写法

- 写库：`obj.api_hash_enc = encrypt_str(plain)`
- 读库：`plain = decrypt_str(obj.api_hash_enc)`

### IPC 通道（worker ↔ 主进程）

- `worker_cmd:{account_id}` 主→worker
- `worker_event:{account_id}` worker→主
- `worker_global` 全局广播
- `runtime_log_stream` worker 写入 list，主进程批量消费落库
- `ratelimit_event_stream` worker 写入 list，主进程批量消费落库

构造消息：`make_cmd(CMD_PAUSE)`、`make_event(EVT_LOG, level="info", message="x")`。

### 限速接口（C 提供，B/D 调用）

C Agent 必须暴露：

```python
# backend/app/worker/ratelimit/engine.py
class RateLimitEngine:
    async def acquire(self, account_id: int, action: str, peer_id: int | None = None) -> RateLimitDecision: ...

@dataclass
class RateLimitDecision:
    allowed: bool
    wait_seconds: float
    outcome: str   # ok | drop | queued | backoff | pause
    reason: str | None = None

# 装饰器形式
def rate_limited(action: str): ...
```

调用示例：

```python
decision = await engine.acquire(acc.id, "send_message_group", peer_id=chat.id)
if decision.outcome == "drop":
    return
if decision.wait_seconds > 0:
    await asyncio.sleep(decision.wait_seconds)
await client.send_message(...)
```

C 内部还要：
- FloodWait 自适应（监听 worker 上抛事件，自动写 RateLimitOverride）
- 拟人化（在 acquire 里追加抖动 / typing / 阅读延迟）
- 冷启动渐进（按 account.cold_start_until）

### 插件 Hook（D 提供）

```python
# backend/app/worker/plugins/base.py
from telethon import TelegramClient, events
from telethon.tl.custom import Message  # 仅类型提示

class PluginContext:
    account_id: int
    feature_key: str
    config: dict[str, Any]
    rules: list[Rule]              # 该 account×feature 下的所有 rule
    client: TelegramClient
    engine: RateLimitEngine
    redis: redis.Redis
    log: Callable[..., None]       # 写 runtime_log

class Plugin:
    key: str
    display_name: str

    async def on_startup(self, ctx: PluginContext) -> None: ...
    async def on_shutdown(self, ctx: PluginContext) -> None: ...
    # event 是 NewMessage.Event；从 event.message 拿 Message，event.respond/reply/edit 直接发送
    async def on_message(self, ctx: PluginContext, event: events.NewMessage.Event) -> None: ...
    async def on_command(
        self, ctx: PluginContext, cmd: str, args: list[str], event: events.NewMessage.Event
    ) -> bool:
        """返回 True 表示已处理。"""
```

### Worker 主入口（B 提供）

```python
# backend/app/worker/runtime.py
async def run_worker(account_id: int) -> None: ...

# 子进程 entrypoint：
def worker_main(account_id: int) -> None:
    asyncio.run(run_worker(account_id))
```

主进程的 supervisor (`supervisor.py`) 负责：
- 拉起 multiprocessing.Process(target=worker_main, args=(account_id,))
- 监控存活，崩溃自动重启（指数退避）
- `pause`/`resume`/`stop`/`reload_config` 通过 IPC 下发
- 启动时扫描所有 `status='active'` 账号自动拉起

### Telethon session 序列化约定

绑定向导完成后用 `client.session.save()` 取出 StringSession 字符串，编码为 bytes 后经
`encrypt_bytes()` 加密存到 `account.session_enc`（BYTEA）。

worker 启动时反向恢复：

```python
from telethon import TelegramClient
from telethon.sessions import StringSession

session_str = decrypt_bytes(account.session_enc).decode()
api_id = int(decrypt_str(account.api_id_enc))
api_hash = decrypt_str(account.api_hash_enc)

client = TelegramClient(StringSession(session_str), api_id, api_hash, proxy=proxy_tuple)
await client.connect()
if not await client.is_user_authorized():
    # 上抛 EVT_LOGIN_REQUIRED，supervisor 把账号置为 login_required
    raise SessionInvalid
```

绑定向导（`/login/start` → `/login/code` → `/login/2fa`）需要在主进程内存里保留同一个
`TelegramClient` 实例（key 用临时 `login_token`），原因：Telethon 的 `auth_key` 与
`phone_code_hash` 都挂在 client 实例内部，跨请求重建会丢失中间态。30 分钟未完成则后台
任务调用 `await client.disconnect()` 清理。

代理写法（如绑定时配置了 Proxy）：

```python
proxy_tuple = (
    proxy.type,           # "socks5" | "http" | "mtproxy"
    proxy.host, proxy.port,
    True,                 # rdns
    proxy.username,
    decrypt_str(proxy.password_enc) if proxy.password_enc else None,
)
```

### TG 命令前缀

从 `system_setting` 表读 key=`command_prefix`，默认 `,`。
worker 启动时一次性 cache 到内存，热修改时由 IPC `reload_config` 通知。
