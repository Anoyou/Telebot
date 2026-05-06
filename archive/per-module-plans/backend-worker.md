# Agent B：Worker 运行时 + Supervisor

> 你是新会话里的工程师。**先读完整份 plan**，再读引用的只读文件，然后开干。注释一律中文。

## 目标

实现每账号一个独立 worker 子进程的运行时框架：
- 主进程内的 `supervisor`：拉起 / 监控 / 重启 / 停止 worker 子进程
- 每个 worker：连接 Telethon、注册事件、监听 IPC、暴露 TG 内置命令（`,help` `,status` `,ping` `,pause` `,resume`）

## 项目根目录

`/Users/anoyou/Desktop/telebot`

## 必读（只读，禁止修改）

1. `/Users/anoyou/Desktop/telebot/teleuserbot.md`（PRD：§2 目标用户、§4-I TG 内命令、§10 技术架构）
2. `/Users/anoyou/Desktop/telebot/CONTRACTS.md`（契约总览，必读全部）
3. `/Users/anoyou/Desktop/telebot/backend/app/worker/ipc.py`（IPC 协议常量）
4. `/Users/anoyou/Desktop/telebot/backend/app/db/models/account.py`（Account 模型 + 状态常量）

## 要写的文件（白名单）

### 核心运行时
- `backend/app/worker/runtime.py`：单 worker 主循环 + Telethon 连接 + 事件注册
- `backend/app/worker/supervisor.py`：主进程内 supervisor + 子进程管理
- `backend/app/worker/tg_client.py`：Telethon 客户端封装 + session 恢复

### TG 内置命令
- `backend/app/worker/command.py`：`,help` `,status` `,ping` `,pause` `,resume` 实现

## 关键 Telethon API

```python
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# 1. 从加密 session 恢复客户端
session_str = decrypt_bytes(account.session_enc).decode()
client = TelegramClient(StringSession(session_str), api_id, api_hash)
await client.connect()

# 2. 检查授权状态
if not await client.is_user_authorized():
    raise SessionInvalid

# 3. 注册消息事件
@client.on(events.NewMessage(pattern=r'^,(\w+)(.*)'))
async def handle_command(event):
    cmd = event.pattern_match.group(1)
    args = event.pattern_match.group(2).strip()
    # 处理命令

# 4. 获取用户信息
me = await client.get_me()

# 5. 发送消息
await event.respond("回复内容")
await client.send_message(chat_id, "消息内容")
```

## IPC 协议（来自 worker/ipc.py）

```python
# 主进程 → worker
CMD_PAUSE = "pause"
CMD_RESUME = "resume" 
CMD_STOP = "stop"
CMD_RELOAD_CONFIG = "reload_config"

# worker → 主进程
EVT_STARTED = "started"
EVT_STOPPED = "stopped"
EVT_LOG = "log"
EVT_LOGIN_REQUIRED = "login_required"

# Redis 频道
worker_cmd:{account_id}    # 主→worker 命令
worker_event:{account_id}  # worker→主 事件
worker_global             # 全局广播
```

## 自检命令

```bash
cd /Users/anoyou/Desktop/telebot/backend
python -m pytest app/tests/test_worker_runtime.py -v
python -m pytest app/tests/test_supervisor.py -v

# 手动测试（需要先有账号数据）
python -c "
from app.worker.supervisor import start_supervisor
import asyncio
asyncio.run(start_supervisor())
"
```

## 汇报格式

```
✅ Agent B 完工汇报

实现功能：
- [x] Worker 运行时框架
- [x] Supervisor 子进程管理
- [x] Telethon 客户端封装
- [x] TG 内置命令（,help ,status ,ping ,pause ,resume）
- [x] IPC 通信机制

自检结果：
- 测试通过：X/Y
- Worker 可启动：✅/❌
- IPC 通信：✅/❌

待其他 Agent：
- A 提供 account_service 后可拉起真实 worker
- C 提供 RateLimitEngine 后可接入限流
- D 提供插件框架后可加载功能
```