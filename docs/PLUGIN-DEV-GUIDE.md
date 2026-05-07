# Telebot 插件开发指南

## 0. 这是什么 / 不是什么

这是 Telebot 的插件开发文档，目标是让你在不改主工程代码的前提下，为账号增加新能力。

它是：
- 一个稳定的插件目录约定（`__init__.py` + `manifest.py` + `plugin.py`）
- 一套运行期契约（`Plugin`、`PluginContext`、`Manifest`）
- 一个可复制到 `data/plugins/installed/` 的第三方加载机制

它不是：
- PagerMaid 原生插件运行环境（我们不是 Pyrogram）
- 远程插件市场（本项目当前不做 repo 订阅安装）
- 可绕过权限控制的后门（installed 插件默认走沙箱）

如果你正在从 PagerMaid 迁移，请重点看第 7 章。

---

## 1. 目录结构（最小骨架）

每个插件一个目录，至少包含 3 个文件：

```text
my_plugin/
  __init__.py
  manifest.py
  plugin.py
```

推荐再带一个 `README.md`：

```text
my_plugin/
  __init__.py
  manifest.py
  plugin.py
  README.md
```

### 1.1 `__init__.py`

必须导出两个顶层常量：
- `PLUGIN_CLASS`
- `MANIFEST`

示例：

```python
from .manifest import MANIFEST
from .plugin import MyPlugin

PLUGIN_CLASS = MyPlugin

__all__ = ["PLUGIN_CLASS", "MANIFEST"]
```

### 1.2 `manifest.py`

定义插件元数据：key、版本、描述、权限、可选 schema。

### 1.3 `plugin.py`

写插件逻辑：消息回调、命令处理、启动/关闭钩子。

---

## 2. Manifest 字段

`Manifest` 定义在 `backend/app/worker/plugins/manifest.py`。

字段速查：
- `key: str`：插件唯一标识，建议小写 snake_case
- `display_name: str`：展示名
- `version: str = "0.1.0"`
- `author: str = "builtin"`
- `description: str = ""`
- `requires_features: list[str]`
- `config_schema: dict | None`
- `permissions: list[str]`
- `on_install: str | None`

示例：

```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="translate",
    display_name="翻译助手",
    version="0.1.0",
    author="example",
    description="把回复消息翻译到目标语言",
    permissions=["read_chat", "send_message", "edit_message"],
)
```

### 2.1 `permissions` 如何写

installed 插件中的 `ctx.client` 会被 `SandboxClient` 包装。你声明什么权限，决定能调哪些 Telethon 方法。

常见映射（简化）：
- `send_message` -> `send_message` / `respond`
- `edit_message` -> `edit` / `edit_message`
- `read_chat` -> `get_messages` / `iter_messages` / `get_chat`
- `send_file` -> `send_file`
- `delete_message` -> `delete_messages`

未声明直接调用会抛 `PermissionError`。

### 2.2 `config_schema` 什么时候需要

如果你的插件需要配 rule.config（比如转发规则），建议提供 JSON Schema，前端可基于它做表单和校验。

---

## 3. Plugin 基类与上下文

基类在 `backend/app/worker/plugins/base.py`。

可重写 hook：
- `on_startup(ctx)`：插件实例激活时调用
- `on_shutdown(ctx)`：卸载或停用时调用
- `on_message(ctx, event)`：消息事件回调（接收哪些方向由 `message_channels` 控制）
- `on_command(ctx, cmd, args, event)`：命令派发钩子（默认返回 False）

类属性：
- `key: str`：插件唯一标识
- `display_name: str`：展示名
- `message_channels: set[str]`：声明需要监听的消息方向，默认 `{"incoming"}`
- `commands: dict`：TG 内命令注册表

`PluginContext` 常用字段：
- `account_id`
- `feature_key`
- `config`
- `rules`
- `client`
- `engine`
- `redis`
- `log`

### 3.1 `message_channels`：声明消息监听方向

插件通过类属性 `message_channels` 声明需要监听哪些方向的消息：

```python
class MyPlugin(Plugin):
    # 默认值，只监听 incoming（别人发的消息）
    message_channels = {"incoming"}

class Game24Plugin(Plugin):
    # 同时监听 incoming 和 outgoing
    message_channels = {"incoming", "outgoing"}
```

可选值：
- `"incoming"`（默认）：群/私聊中别人发的消息
- `"outgoing"`：自己发送的消息

在 `on_message` 内通过 `event.outgoing` 判断消息来源：

```python
async def on_message(self, ctx, event):
    if event.outgoing:
        # 处理自己发的消息
        ...
    else:
        # 处理别人发的消息
        ...
```

**重要原则**：不应该因为单个插件的需求去改框架核心（新增 hook、改 loader 逻辑等）。新功能应优先通过插件自身的声明（如 `message_channels`）和内部路由实现，让 loader 统一处理。这样开发新插件时不需要动其他文件。

### 3.2 命令推荐走 `commands` 字典

当前工程里，插件最稳定的命令接入方式是类属性 `commands`：

```python
class MyPlugin(Plugin):
    key = "my_plugin"
    display_name = "我的插件"
    commands = {
        "hello": hello_handler,
    }
```

handler 签名：

```python
async def hello_handler(client, event, args, account_id, ctx):
    ...
```

命令分发器会把它注册成 `,hello`（前缀可配置，默认是 `,`）。

### 3.3 `on_message` 与 `commands` 的选择

- 你要处理被动 incoming 消息：用 `on_message`（默认）
- 你要同时监听自己发的和别人的消息：设 `message_channels = {"incoming", "outgoing"}`，在 `on_message` 内用 `event.outgoing` 分流
- 你要处理主动发出的命令（`,xxx`）：用 `commands`
- 以上可组合使用

---

## 4. 风控 / 限流接入

插件主动发送消息前，建议调用风控引擎：

```python
decision = await ctx.engine.acquire(ctx.account_id, "send_message_group", peer_id=event.chat_id)
if not decision.allowed:
    return
if decision.wait_seconds and decision.wait_seconds > 0:
    await asyncio.sleep(float(decision.wait_seconds))
```

建议：
- 对每个会触发外发的路径都做 acquire
- floodwait 异常单独处理，避免把整个插件打挂
- 用 `ctx.log` 写可追踪日志，避免裸 `print`

---

## 5. Telethon API 速查（常用）

消息对象：
- `event.raw_text`
- `event.chat_id`
- `event.sender_id`
- `event.message`

回复/发送：
- `await event.reply(text)`
- `await event.respond(text)`
- `await event.edit(text)`
- `await ctx.client.send_message(peer, text)`

读取上下文：
- `await event.get_reply_message()`
- `await event.get_chat()`
- `async for msg in client.iter_messages(peer, limit=N): ...`

文件与媒体：
- `await client.send_file(peer, file=...)`

异常常见：
- `telethon.errors.FloodWaitError`

---

## 6. 安装与启用

### 6.1 内置插件（维护者路径）

内置目录：
- `backend/app/worker/plugins/builtin/<key>/`

注意：并行开发时请避免多人同时改同一个 builtin。

### 6.2 第三方插件（推荐）

放到：
- `data/plugins/installed/<key>/`

示例：

```bash
cd /path/to/telebot/data/plugins/installed
git clone https://github.com/somebody/my-telebot-plugin.git
```

重启 worker 或触发 reload 后，loader 会扫描并加载。

### 6.3 卸载

```bash
rm -rf /path/to/telebot/data/plugins/installed/<key>
```

然后重启 worker 或 reload。

### 6.4 启用/禁用

插件目录可被扫描到，不代表账号一定启用。实际是否生效取决于对应账号 feature 开关。

---

## 7. 从 PagerMaid 插件移植（菜谱）

PagerMaid 插件多是 Pyrogram 写法；Telebot 是 Telethon，API 不兼容，但逻辑可以迁移。

核心原则：
- 看源码“在干嘛”
- 不抄原实现
- 用 Telethon API 重写

### 7.1 速查映射

| PagerMaid / Pyrogram | Telebot / Telethon |
|---|---|
| `@Client.on_message` | `Plugin.on_message` |
| `filters.command(["x"])` | `commands = {"x": handler}` |
| `filters.regex(...)` | `on_message + re` |
| `message.reply_text` | `event.reply` |
| `message.edit` | `event.edit` |
| `message.text` | `event.raw_text` |
| `message.chat.id` | `event.chat_id` |
| `message.reply_to_message` | `await event.get_reply_message()` |
| `pyrogram.errors.FloodWait` | `telethon.errors.FloodWaitError` |

### 7.2 推荐迁移步骤

1. 先读懂插件业务目标（输入/输出/异常）
2. 画出命令入口与分支
3. 用本项目骨架建目录
4. 把每个分支替换成 Telethon 等价调用
5. 加 manifest 权限声明
6. 本地 smoke test

### 7.3 翻译类插件的注意点

- 输入文本优先用“被回复消息内容”，不是命令本身
- 用户只输 `,fy zh` 时，默认目标是中文
- 语言代码和语言名做映射与容错（如 `zh-cn`、`en`、`japanese`）
- 回复过长时避免直接塞超长 prompt

---

## 8. 沙箱权限声明

installed 插件默认拿到的是 `SandboxClient` 而不是真实 `TelegramClient`。

这意味着：
- 你需要在 manifest 显式声明能力
- 不声明的 client 方法不可调用
- 权限不足时会抛 `PermissionError`

建议实践：
- 最小权限原则，只申请你真正要用的能力
- 在 README 写明本插件依赖权限
- 错误提示要清晰，方便使用者排查

---

## 9. 完整样例：translate

参考样例目录：
- `examples/plugins/translate/`

它演示了：
- 如何声明 `PLUGIN_CLASS` + `MANIFEST`
- 如何注册 `,fy` 命令
- 如何读取被回复消息
- 如何复用现有 `LLMProvider` / `build_client` 做翻译
- 如何在失败时返回可读错误

建议你从复制这个样例开始，改成自己的能力插件。

---

## 10. 调试建议

快速自检清单：
- `__init__.py` 是否导出 `PLUGIN_CLASS` 和 `MANIFEST`
- `MANIFEST.key` 是否和插件 class key 一致
- `permissions` 是否覆盖实际调用的方法
- 命令 handler 签名是否是 5 参数
- 错误是否都被捕获并反馈给用户

常见问题：
- 插件被跳过：通常是 `MANIFEST` 类型不对或导出缺失
- 命令没反应：通常是 feature 未启用或命令前缀不匹配
- 调 LLM 失败：provider 不存在、没 key、模型名不对

---

## 11. 版本与兼容建议

建议语义化版本：
- `0.x`：开发阶段，允许快速迭代
- `1.x`：接口稳定后

兼容性建议：
- 不要依赖私有内部模块路径
- 尽量只依赖 `Plugin` / `Manifest` / `PluginContext` 公开契约
- 新增行为优先通过 `config` 可选项实现，减少破坏性变更

---

## 12. 安全与合规建议

- 不要把明文 key 写入日志
- 不要把完整隐私消息持久化到外部系统
- 对外部请求做超时和异常处理
- 对高风险操作（删消息、批量发送）加显式开关

---

## 13. 最小可运行模板

```python
# plugin.py
from __future__ import annotations

from app.worker.plugins.base import Plugin, register


async def ping_handler(client, event, args, account_id, ctx):
    await event.edit("pong")


@register
class DemoPlugin(Plugin):
    key = "demo"
    display_name = "Demo"
    commands = {"ping": ping_handler}
```

```python
# manifest.py
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="demo",
    display_name="Demo",
    version="0.1.0",
    author="example",
    description="最小示例",
    permissions=["edit_message"],
)
```

```python
# __init__.py
from .manifest import MANIFEST
from .plugin import DemoPlugin

PLUGIN_CLASS = DemoPlugin

__all__ = ["PLUGIN_CLASS", "MANIFEST"]
```

把目录放进 `data/plugins/installed/demo/` 后重启 worker，即可用 `,ping`。
