# TelePilot 模块开发指南（Plugin API）

> 本文档涵盖模块开发全流程：本地模块、远程模块、框架约束、调试建议。代码层 API 仍叫 `Plugin` / `PluginContext`，用户界面和产品文案统一称“模块”。

---

## 目录

1. [快速开始](#1-快速开始)
2. [模块结构（Plugin 包）](#2-模块结构plugin-包)
3. [Plugin 基类](#3-plugin-基类)
4. [PluginContext](#4-plugincontext)
5. [Manifest 元数据](#5-manifest-元数据)
   - [交互 Bot 兼容声明](#交互-bot-兼容声明interaction-entries)
6. [指令系统（command API）](#6-指令系统command-api)
7. [消息监听](#7-消息监听)
8. [Conversation 工具](#8-conversation-工具)
9. [模块日志](#9-模块日志)
10. [远程模块](#10-远程模块)
11. [清理生命周期（cleanup）](#11-清理生命周期cleanup)
12. [安全边界](#12-安全边界)
13. [前端集成](#13-前端集成)
    - [配置形态概览](#配置形态概览)
    - [统一配置页样式规范](#统一配置页样式规范)
    - [规则驱动配置页](#规则驱动配置页forward--autoreply--autorepeat)
    - [规则驱动补充：后端 Dry-Run 适配](#规则驱动补充后端-dry-run-适配)
    - [单配置对象页](#单配置对象页game24--codex-image)
    - [通用 Schema 驱动独立页](#通用-schema-驱动独立页legacy-schema-兼容)
    - [基础能力：平台内置功能](#基础能力平台内置功能scheduler)
    - [适配自检清单](#适配自检清单)
14. [模块工程规范](#14-模块工程规范plugin-实现)
    - [发布与交互体验检查清单](#发布与交互体验检查清单)
    - [指令权限底线](#指令权限底线)
    - [消息发送能力边界](#消息发送能力边界)
    - [并发与抢答标准模板](#并发与抢答标准模板)
    - [配置项完整性原则](#配置项完整性原则)
    - [统一配置项命名与校验](#统一配置项命名与校验)
    - [模板配置与占位符](#模板配置与占位符)
    - [定时任务与后台任务生命周期](#定时任务与后台任务生命周期)
    - [奖惩系统接入约定](#奖惩系统接入约定)
    - [模块最小测试清单](#模块最小测试清单)
    - [可复制的游戏模块骨架](#可复制的游戏模块骨架)
15. [调试建议](#15-调试建议)
16. [安全与合规](#16-安全与合规)
17. [完整示例](#17-完整示例)

---

## 1. 快速开始

### 文件结构

```
plugins/installed/{模块名}/
├── __init__.py        # 导出 PLUGIN_CLASS 和 MANIFEST
├── manifest.py        # Manifest 元数据
├── plugin.py          # 模块主类
└── (其他模块)
```

### 最小可运行模块

**plugin.py：**
```python
from app.worker.plugins.base import Plugin, register

@register
class PingPlugin(Plugin):
    key = "ping"
    display_name = "Ping"

    async def on_command(self, ctx, cmd, args, event) -> bool:
        if cmd == "ping":
            await event.edit("pong")
            return True
        return False
```

**manifest.py：**
```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="ping",
    display_name="Ping",
    version="0.1.0",
    author="example",
    description="响应 ping 指令",
)
```

**__init__.py：**
```python
from .manifest import MANIFEST
from .plugin import PingPlugin

PLUGIN_CLASS = PingPlugin
__all__ = ["PLUGIN_CLASS", "MANIFEST"]
```

放进 `plugins/installed/ping/` 后重启 worker 即可。

---

## 2. 模块结构（Plugin 包）

### 目录约定

```
backend/app/worker/plugins/
├── base.py              # Plugin 基类 + register 装饰器
├── manifest.py          # Manifest 数据类
├── loader.py            # 模块加载器 + 热重载 + generation guard
└── builtin/             # 内置模块
    ├── game24/
    └── forward/

plugins/installed/       # 远程/用户安装的模块
├── translate/
└── (更多模块...)
```

### 生命周期

```
loader._load_all()
  → scan builtin/ + plugins/installed/
  → import plugin.py + manifest.py
  → 验证 Manifest 合法性
  → 实例化 Plugin 子类
  → 调用 on_startup(ctx)

热重载 (reload_plugin):
  → state.generation += 1          # generation guard
  → 旧模块: on_shutdown(ctx)
  → 重新 import + 实例化
  → 新模块: on_startup(ctx)

消息派发:
  → 检查 ctx.generation == state.generation
  → 跳过过期 handler（竞态保护）
  → 调用 on_command / on_message
```

---

## 3. Plugin 基类

```python
class Plugin:
    # === 必须设置 ===
    key: str                          # 唯一标识，也是模块 key
    display_name: str                 # 显示名

    # === 可选配置 ===
    message_channels: set[str]        # 监听方向: {"incoming"} / {"outgoing"} / 二者都监听
    owner_only: bool = True           # 只影响 on_message；False 表示允许普通成员消息进入 on_message
    commands: dict = {}               # TG 内指令；只由本账号 outgoing 指令触发
    command_config_keys: set[str] = set()  # 这些配置变化后需要重载并重新注册指令
    description: str = ""             # 描述（用于帮助系统）

    # === 生命周期钩子 ===
    async def on_startup(self, ctx: PluginContext) -> None:
        """模块激活时调用一次。"""

    async def on_shutdown(self, ctx: PluginContext) -> None:
        """模块关停前调用一次。必须幂等。"""

    # === 事件处理 ===
    async def on_message(self, ctx: PluginContext, event) -> None:
        """消息事件回调。"""

    async def on_command(self, ctx: PluginContext, cmd: str, args: list[str], event) -> bool:
        """指令派发回调。返回 True 表示已处理。"""
        return False
```

### 注册

```python
@register
class MyPlugin(Plugin):
    key = "my_plugin"
    ...
```

`@register` 装饰器把模块类注册到全局表，loader 通过 key 查找。

---

## 4. PluginContext

```python
@dataclass
class PluginContext:
    account_id: int
    feature_key: str
    config: dict           # 当前账号的模块配置
    rules: list            # 规则列表
    client: TelegramClient | None
    engine: Any            # RateLimitEngine
    redis: Any             # redis.asyncio.Redis
    log: Callable          # 日志函数
    scheduler: Any         # 平台调度器 facade
    generation: int        # generation guard 计数

    # 工具方法
    async def conversation(self, peer, timeout=30) -> Conversation:
        """创建与 bot 的对话会话。"""
```

注意：内置模块会拿到完整运行时能力；远程/第三方模块拿到的是受限上下文：`ctx.client` 为 `SandboxClient`，指令 handler 中传入的 `client` 参数与 `ctx.client` 同源（同样是 sandbox client），`ctx.engine` 和 `ctx.redis` 为 `None`，只能通过声明过的权限和 `ctx.scheduler` facade 使用有限能力。

### 4.1 可用上下文与访问方式（PluginContext Contract）

模块请只从 `PluginContext` 读取运行时信息，不要跨层 import worker 私有实现。

| 字段 | 访问方式 | 说明 |
|------|----------|------|
| `ctx.account_id` | `ctx.account_id` | 当前账号 ID（账号级隔离边界） |
| `ctx.feature_key` | `ctx.feature_key` | 当前模块 feature key |
| `ctx.config` | `ctx.config.get("k")` | 模块配置（账号/全局已合并后的可见配置） |
| `ctx.rules` | 遍历 `ctx.rules` | 当前账号 + 当前模块已启用规则 |
| `ctx.client` | `await ctx.client.send_message(...)` | Telegram 客户端；第三方模块场景会是 `SandboxClient` 包装 |
| `ctx.engine` | `await ctx.engine.acquire(...)` | 仅内置模块可用；第三方模块通常为 `None` |
| `ctx.redis` | `await ctx.redis.get(...)` | 仅内置模块可用；第三方模块通常为 `None` |
| `ctx.log` | `await ctx.log("info", "...", **detail)` | 运行日志写入器 |
| `ctx.scheduler` | `ctx.scheduler.register(job_id, schedule, callback, *, replace=True)` / `ctx.scheduler.unregister(job_id)` | 调度 facade（按权限/能力边界开放） |
| `ctx.conversation(...)` | `async with ctx.conversation(peer)` | 与目标 peer 建立会话 |

### 4.2 权限边界与禁止事项

1. 第三方模块必须遵循 `manifest.permissions` 最小授权，未声明的客户端能力不可调用。
2. 第三方模块不得假设 `ctx.engine`、`ctx.redis` 恒可用；访问前必须判空。
3. 禁止通过模块绕过账号边界：不要读写其他账号配置、规则、会话状态。
4. 禁止在模块中执行系统级/运维级动作（如重启进程、安装/卸载模块、修改权限模型）。
5. 禁止依赖 worker 私有模块或 monkey patch 运行时对象来“扩权”。
6. 禁止把敏感凭据直接打到日志；`ctx.log` 只记录最小必要信息。

### 4.3 配置/账号/运行时数据访问建议

1. 配置：通过 `ctx.config` 读取；按 `config_schema` 的 `level` 设计字段，不自行拼接跨账号配置。
2. 账号：通过 `ctx.account_id` 做所有业务隔离键，不缓存到跨账号全局变量。
3. 运行时：仅使用 `ctx.client` / `ctx.scheduler` / `ctx.conversation` 提供的公开入口。
4. 日志：统一用 `ctx.log`，并在 `detail` 里带结构化字段（如 `chat_id`、`action`）。
5. 兜底：对可选能力（`engine`/`redis`）做 feature-detection，保证第三方模块在受限上下文也能安全降级。

最小示例见：[docs/examples/plugin_context_minimal.py](./examples/plugin_context_minimal.py)。

---

## 5. Manifest 元数据

### 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `key` | str | 唯一标识，与 Plugin.key 一致 |
| `display_name` | str | 显示名称 |
| `version` | str | 语义化版本（如 `1.0.0`） |
| `author` | str | 作者 |
| `description` | str | 功能描述，用于帮助系统 |

### 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `permissions` | list | 权限声明，默认 `["send_message", "edit_message", "read_chat"]` |
| `config_schema` | dict | JSON Schema，有配置的模块必须写 |
| `requires_features` | list | 依赖的其他模块 key |
| `min_telepilot_version` | str | 最低 TelePilot 版本要求，远程模块建议填写 |
| `min_telebot_version` | str | 旧字段名，0.15 起仅作为兼容别名保留，新模块不要再新增 |

### 完整示例

```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="my_plugin",
    display_name="我的模块",
    version="1.0.0",
    author="your_name",
    description="模块功能描述",
    permissions=["send_message", "edit_message", "read_chat"],
    config_schema={
        "type": "object",
        "properties": {
            "api_key": {
                "type": "string",
                "title": "API Key",
                "level": "global",
            },
            "target_chat": {
                "type": "string",
                "title": "目标聊天 ID",
                "level": "account",
            },
        },
    },
    requires_features=[],
)
```

### config_schema 配置规范

`config_schema` 遵循 JSON Schema 规范，额外支持 `level` 字段控制配置的作用域：

| level | 作用域 | 存储位置 | 说明 |
|-------|--------|---------|------|
| `global` | 全局（所有账号共享） | plugin_config | API Key、通用参数等 |
| `account` | 单个账号 | rule.config | 聊天 ID、行为开关等 |
| （不填） | 默认 account | rule.config | 向后兼容 |

**优先级：** 账号级配置 > 模块全局配置 > config_schema 中的 default

**前端渲染：** `config_schema["x-ui-mode"]` 决定模块配置入口：
- `rules` → 规则驱动独立配置页，适合多条规则 CRUD 和 dry-run
- `single` → 单配置对象独立配置页；没有专属页面的轻量模块也应按通用独立配置页处理
- `platform` → 平台基础能力页，不混在普通模块列表里
- `schema` → 兼容旧模块的别名；不再代表“Schema 弹窗”类，按通用单配置独立页读取字段
- `level: global` 的字段 → 全局配置区（所有账号共享）
- `level: account` 的字段 → 账号配置区（按账号隔离）
- 无 level 的字段 → 默认按账号隔离

**必填字段验证清单（内置模块）：**

| 模块 | config_schema | UI 模式 | 状态 |
|------|--------------|---------|------|
| forward | ✅ target_chat_id, mode | `rules` | 已有 |
| auto_reply | 规则通过 Rules API 管理 | `rules` fallback | 已有 |
| autorepeat | ✅ trigger / repeat / chat 配置 | `rules` | 已有 |
| game24 | ✅ command, timeout | `single` | 已补 |
| codex_image | ✅ command, access_token, model, message_template, image_size/aspect_ratio/image_format, timeout/status/output/instructions | `single` | 已补（experimental） |
| scheduler | ✅ default_notify, max_tasks | `platform` | 已迁移为平台基础能力 |
| translate | ✅ default_lang, llm_provider | `single` / `schema` 兼容 | 已补 |

### Manifest 验证

远程模块安装阶段验证的是 `plugin.json`，不会执行 Python：

```python
required = ["name 或 key", "version"]
name_pattern = r"^[A-Za-z0-9_][A-Za-z0-9_-]*$"
version_pattern = r"^\d+\.\d+\.\d+"
```

运行阶段 loader 会 import `__init__.py`，并检查：

- `PLUGIN_CLASS` 是 `Plugin` 子类
- `MANIFEST` 是 `Manifest` 实例
- `MANIFEST.key` 与模块 key / 目录名保持一致

### 交互 Bot 兼容声明（interaction entries）

交互 Bot 用来承接群内高频互动，不能直接复用 UserBot 插件命令作为启动入口；否则高频游戏又会回到 UserBot 账号身上，违背风控隔离目标。后续模块要支持“转账命中后启动”，应通过 Manifest 声明一个或多个交互入口，由平台统一适配。

推荐在 `manifest.py` 顶层声明 `category` 和 `interaction_entries`；旧写法也兼容 `config_schema["x-category"]` 与 `config_schema["x-interaction-entries"]`。这是声明式协议，不要求模块自己解析转账通知、Bot Token 或群消息格式。

模块分类只保留三类，前端会按中文分组展示：

| category | 中文分组 | 适用模块 |
| --- | --- | --- |
| `interactive` | 互动娱乐 | 游戏、群内娱乐、需要交互 Bot 承接高频消息的模块 |
| `automation` | 自动化 | 自动回复、转发、定时任务等账号自动化能力 |
| `utility` | 工具能力 | AI、媒体生成、查询、辅助工具等能力 |

`category` 只决定展示分组；是否能被交互 Bot 启动，只看是否声明了 `interaction_entries`。

```python
MANIFEST = Manifest(
    key="game24",
    display_name="24点游戏",
    version="1.1.0",
    category="interactive",
    interaction_entries=[
        {
            "key": "start_paid_game",
            "title": "付费开局",
            "description": "转账命中或模块关键词命中后，由交互 Bot 开启一局游戏。",
            "session_scope": "chat",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "prize": {
                        "type": "integer",
                        "title": "奖金",
                        "default": 123,
                        "minimum": 1,
                    },
                    "timeout": {
                        "type": "integer",
                        "title": "答题限时（秒）",
                        "default": 500,
                        "minimum": 30,
                        "maximum": 3600,
                    },
                },
                "required": ["prize"],
            },
        }
    ],
    config_schema={
        "type": "object",
        "x-ui-mode": "single",
        "properties": {
            "command": {"type": "string", "title": "触发指令名", "default": "24d"},
            "timeout": {"type": "integer", "title": "答题限时（秒）", "default": 500},
        },
    },
)
```

`interaction_entries` 中的 `session_scope` 建议使用：

- `chat`：同一个群内同一时间只开一局，适合 24 点这类公共抢答。
- `user`：同一个用户一条会话，适合个人答题或私聊流程。
- `none`：模块自己不提供并发隔离，由平台按全局规则限制。

#### 配置合并顺序

交互入口的实际参数按以下顺序合并，越靠后优先级越高：

```text
input_schema default
< 模块全局配置
< 账号级模块配置
< 交互规则 module_config
< 转账事件动态参数（payer / receiver / amount / chat_id 等）
```

其中 `module_config` 只保存当前交互规则的覆盖项，例如“这条门票规则奖金为 200”；模块自身的通用配置仍放在模块配置页中。

#### 标准事件输入

平台调用交互入口时，会向适配层提供标准事件对象。模块不要依赖转账通知原文。

```json
{
  "account_id": 1,
  "chat_id": -100123,
  "rule_id": "game24-ticket",
  "payer_user_id": 111,
  "payer_name": "AAA",
  "receiver_name": "BBB",
  "amount": 100,
  "source_message_id": 80,
  "notice_message_id": 81
}
```

#### 标准动作输出

交互入口或适配器应返回平台可执行的标准动作，而不是直接调用 Telegram API。交互 Bot runtime 统一负责发送、编辑、回复、持久化状态和幂等处理。

```json
[
  {
    "type": "send_message",
    "text": "24 点开始...",
    "state_key": "game24",
    "state": {"numbers": [1, 5, 5, 5], "prize": 123}
  },
  {
    "type": "wait_answer",
    "state_key": "game24",
    "answer_type": "game24_expression"
  },
  {
    "type": "award_notice",
    "text": "答对了：AAA\n题目：24 点 [1 5 5 5]\n奖金：123",
    "reply_to": "winner_message"
  }
]
```

#### 兼容边界

1. 原模块本体不得为了交互 Bot 直接改写 `commands` / `on_message` 语义；UserBot 入口和交互 Bot 入口是两套边界。
2. 可以把纯业务逻辑抽到共享函数，例如题目生成、答案校验、渲染模板；UserBot 插件和交互 Bot 适配器共同调用这些纯函数。
3. 模块不处理 Bot Token、Abot 通知格式、转账过滤、发奖账号；这些都属于平台层职责。
4. 交互 Bot 中奖公告必须引用赢家的答案消息，方便 UserBot 账号按公告自动回复发奖。
5. 若模块未声明 `x-interaction-entries`，前端不应把它展示为可由交互 Bot 启动的模块。

---

## 6. 指令系统（command API）

**安全底线：普通指令只能由当前 UserBot 账号自己发出的 outgoing 消息触发。** 群成员、普通用户、频道消息等 incoming 消息不能直接触发模块 `commands`。`owner_only=False` 只表示模块的 `on_message` 可以监听普通成员消息，不表示开放指令执行权限。

红包、抢答、24 点、猜数字这类“公共参与 + 私有管理”的模块必须按这个模型设计：

- 开局、发红包、撤销、强制结束、查看管理状态等管理动作写成 `commands`，只能由本账号 outgoing 指令触发。
- 领取口令、答题、参与投票等普通成员行为写在 `on_message`，通过普通文本判断，不要求用户发送系统指令前缀。
- 如果自动回复、定时任务等平台内部动作需要“代替本账号执行指令”，使用平台内部派发能力，不让普通 incoming 消息直接进 `commands`。

### 指令派发流程

1. 当前账号 outgoing 消息到达 → 检查前缀匹配
2. 提取指令名和参数
3. 检查别名（贪心最长匹配）
4. 遍历已注册模块，调用 `on_command(ctx, cmd, args, event)`
5. 第一个返回 True 的模块接管，后续不再传递

### on_command 签名

```python
async def on_command(
    self,
    ctx: PluginContext,       # 上下文
    cmd: str,                 # 指令名（如 "weather"）
    args: list[str],          # 参数列表
    event: NewMessage.Event,  # 原始事件
) -> bool:
    """返回 True 表示已处理。"""
```

### 别名支持

指令别名支持多词贪心匹配和参数透传：

```
用户: ,fy zh hello
→ 别名 "fy zh" → "translate"
→ 参数透传: translate hello
```

---

## 7. 消息监听

```python
class MyPlugin(Plugin):
    message_channels = {"incoming"}

    async def on_message(self, ctx: PluginContext, event) -> None:
        """监听所有匹配方向的消息。"""
        # 兼容 NewMessage.Event 与裸 Message；不要直接 event.outgoing。
        msg = getattr(event, "message", event)
        if bool(getattr(event, "outgoing", getattr(msg, "out", False))):
            return  # 忽略自己发的
        # 处理逻辑
```

### channels 类型

| 值 | 说明 |
|---|------|
| `incoming` | 别人发给本账号、群、频道的消息 |
| `outgoing` | 当前 UserBot 账号自己发出的消息 |

> 注意：当前 loader 的方向过滤只有 `incoming/outgoing` 两类。群组、私聊、频道请在 hook 内用 `event.is_group` / `event.is_private` / `event.is_channel` 或 `chat_id` 判断。

### 事件对象兼容写法

模块收到的对象通常是 `events.NewMessage.Event`，但在测试、热重载、Telethon 代理属性等场景里，也可能表现得更像裸 `Message`。因此建议用 `getattr` 做兼容，不要直接假设 `event.outgoing`、`event.message.id` 一定存在：

```python
def event_message(event):
    return getattr(event, "message", event)

def event_text(event) -> str:
    msg = event_message(event)
    return str(getattr(event, "raw_text", None) or getattr(msg, "raw_text", None) or "").strip()

def is_outgoing(event) -> bool:
    msg = event_message(event)
    return bool(getattr(event, "outgoing", getattr(msg, "out", False)))
```

这样可以避免类似 `'Message' object has no attribute 'outgoing'` 的运行时错误。

---

## 8. Conversation 工具

与其他 Bot 交互的工具类（如 @BotFather）：

```python
async with ctx.conversation("@BotFather") as conv:
    await conv.send("/newbot")
    resp = await conv.get_response(timeout=30)
    print(resp.text)

    # 点击内联按钮
    await conv.click_button(msg, row=0, col=0)
```

### API

| 方法 | 说明 |
|------|------|
| `send(text, **kwargs)` | 发送文本/文件/图片 |
| `get_response(timeout)` | 等对方回复 |
| `click_button(msg, row, col)` | 点击 inline keyboard |
| `mark_read()` | 标记已读 |
| `close()` | 清理 handler |

### 超时处理

```python
from app.worker.conversation import ConversationTimeout

try:
    resp = await conv.get_response(timeout=10)
except ConversationTimeout:
    await conv.send("超时了，请重试")
```

---

## 9. 模块日志

模块日志会进入后台的“日志中心 → Runtime → 模块日志”分页，和“消息日志”“系统日志”分开显示；涉及 sudo、Config Bundle confirm、account_bot confirm 等安全决策的记录则在“日志中心 → Audit”查看。

### 如何写日志

模块运行时通过 `ctx.log(level, message, **detail)` 输出日志：

```python
await ctx.log(
    "info",
    "自动回复命中：关键词 hello，准备发送回复。",
    chat_id=event.chat_id,
    rule_id=rule.id,
    keyword="hello",
)
```

日志会自动带上：

- `source="plugin"`
- `plugin_key`
- `account_id`
- `level`
- `message`
- `detail`

### 日志写法规范

- `message` 写给人看：用一句通俗的话说明发生了什么。
- `detail` 写给排障看：放 `chat_id`、`rule_id`、`sender_id`、`message_preview`、`elapsed_ms` 等结构化字段。
- 不要在日志中写 API Key、Bot Token、session、完整文件路径、完整群聊长文本。
- 错误日志要说明“哪一步失败 + 失败原因 + 是否已跳过/重试/继续运行”。

推荐：

```python
await ctx.log(
    "error",
    f"图片生成失败：上游返回限额错误，本次任务已停止。原因：{err_type}",
    chat_id=chat_id,
    elapsed_ms=elapsed_ms,
)
```

不推荐：

```python
await ctx.log("error", f"failed: {raw_exception_with_token}")
```

### loader 自动记录的模块异常

如果模块 `on_message` 抛异常，loader 会自动写一条模块日志，并附带：

- `plugin_key`
- `direction`
- `chat_id`
- `sender_id`
- `message_preview`
- `traceback`

这类异常不会让 worker 崩溃，当前消息会被跳过，其它模块继续运行。

---

## 10. 远程模块

### 安装方式

**通过 Web UI：**
1. 进入模块中心的远程模块页面
2. 输入 GitHub 仓库地址或子目录 URL
3. 点击安装

**通过 REST API：**
```
POST /api/remote-plugins/install
POST /api/remote-plugins/{name}/enable
POST /api/remote-plugins/{name}/enable-accounts
POST /api/remote-plugins/{name}/update
DELETE /api/remote-plugins/{name}
```

### 远程模块规范

远程模块仓库必须包含 `plugin.json`，同时运行时必须提供 `manifest.py` / `plugin.py` / `__init__.py`：

```json
{
  "name": "weather",
  "display_name": "天气查询",
  "description": "查询天气信息",
  "author": "community",
  "version": "1.0.0",
  "entry": "plugin.py",
  "min_telepilot_version": "0.15.0",
  "commands": ["weather", "w"],
  "cleanup_mode": "no-op",
  "tags": ["weather", "utility"],
  "permissions": ["send_message", "read_chat"]
}
```

**最小必填：** `name` / `key` 二选一，`version` 必填。`display_name`、`description`、`author`、`entry` 强烈建议填写，`entry` 未填时默认 `plugin.py`。

安全约束：

- 安装阶段只静态解析 `plugin.json`，不会执行 `manifest.py` 或其它 Python 文件。
- `name` / `key` 只能包含字母、数字、`_`、`-`，不能包含路径分隔符。
- 运行阶段由 loader import `__init__.py`，此时必须导出 `PLUGIN_CLASS` 和 `MANIFEST`。
- 不再兼容旧的“只有 plugin.json + plugin.py”单文件远程模块；缺少 `manifest.py` 或 `__init__.py` 会在安装/更新阶段被拒绝。
- 相对安装目录会按项目根目录解析，不要依赖后端进程当前工作目录。

### 安装流程

```
1. git clone 到 plugins/installed/{name}/
2. 读取 plugin.json → Pydantic 校验（安装阶段不执行 Python）
3. 静态检查 `manifest.py` / `plugin.py` / `__init__.py` 是否齐全
4. 验证通过 → 注册到数据库
5. 广播 CMD_RELOAD_CONFIG，worker 重新扫描 installed 模块
6. 验证失败 → 删除目录，返回错误
```

### Registry 机制

支持从远程 registry 同步可用模块列表：

```json
{
  "plugins": [
    {
      "name": "weather",
      "display_name": "天气查询",
      "source_url": "https://github.com/user/repo",
      "version": "1.0.0"
    }
  ]
}
```

---

## 11. 清理生命周期（cleanup）

参考 TeleBox 的三种风格：

| 风格 | 适用场景 | cleanup 行为 |
|------|---------|-------------|
| `resource` | 持有定时器/子进程/网络连接 | 真正释放资源 |
| `reset` | 持有 db/缓存/配置引用 | 引用置空 |
| `no-op` | 流程型模块，无长期资源 | 空方法 + 注释说明 |

### 统一约束

- **必须幂等**：重复调用不报错
- **不应依赖用户输入**
- **不应误伤系统级资源**：systemd 服务、iptables 等不要在 reload 时停掉

### 实现

```python
class MyPlugin(Plugin):
    _timer = None
    _db = None

    async def on_startup(self, ctx):
        self._timer = create_timer(...)
        self._db = get_db()

    async def on_shutdown(self, ctx):
        """resource 风格：释放资源"""
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._db:
            self._db = None
```

---

## 12. 安全边界

### 指令前缀（command_prefix）

- 所有 Telegram 指令必须有明确前缀（如 `,` 或自定义）
- 前缀由系统设置里的 `command_prefix` 控制；模块配置中不要再单独硬编码 `prefix`

### 权限声明

Manifest 中的 `permissions` 字段声明模块需要的能力：

| 权限 | 说明 |
|------|------|
| `send_message` | 发送消息 |
| `edit_message` | 编辑消息 |
| `read_chat` | 读取聊天历史 |

默认给三类常用能力，内置模块漏写时不会被沙箱拦截。

### 禁止行为

- 不允许 `os.system` / `subprocess` 执行系统命令（除非显式声明）
- 不允许把明文 key 写入日志
- 不允许持久化完整隐私消息到外部系统
- 对外部请求必须做超时和异常处理

---

## 13. 前端集成

模块前端配置推荐分为两种配置形态，另有一类平台内置基础能力。历史 `schema` 只作为兼容别名保留，不再新增“Schema 弹窗”类模块。后续新增模块时，优先通过 `manifest.py` 的 `config_schema["x-ui-mode"]` 声明分类，前端会自动归类展示。

### 配置形态概览

| 分类 | 适用场景 | 大白话 | 典型功能 | 配置入口 |
|------|---------|--------|---------|---------|
| **规则驱动配置页** | 多条规则独立配置，需 CRUD + 试运行 | 像自动化流水线：先建规则，再按匹配条件触发动作 | forward、auto_reply、autorepeat | 专属配置页 |
| **单配置对象 / 通用独立配置页** | 每个账号只保存一份模块配置，或轻量模块只需要字段表单 | 像一个工具面板：配置好触发指令和参数，直接运行；普通字段由 schema 驱动渲染 | game24、codex_image、简单远程模块 / 小工具模块 | 专属或通用独立配置页 |
| **基础能力 — 平台内置** | 系统运行时常驻能力，不作为普通模块展示 | 像底座服务：给模块或平台调用，不强调启停 | scheduler | 平台功能页 |

**关键判断**：需要维护多条规则 → `rules`；只有一份账号配置或普通字段表单足够 → `single`；旧模块已经写了 `schema` → 按 `single` 通用独立页兼容；像调度器这种系统服务 → `platform`。

#### 自动分类规则

新增模块应在 `config_schema` 顶层声明 `x-ui-mode`：

```python
config_schema={
    "type": "object",
    "x-ui-mode": "single",  # 推荐：rules / single / platform；schema 仅作兼容别名
    "properties": {
        "command": {"type": "string", "title": "触发指令名", "default": "demo"},
    },
}
```

| `x-ui-mode` | 展示位置 | 说明 |
|-------------|----------|------|
| `rules` | 规则驱动配置页 | 规则驱动模块，通常有规则列表、创建/编辑、dry-run |
| `single` | 单配置对象 / 通用独立配置页 | 单配置对象或通用独立配置页，字段可由 `config_schema` 驱动 |
| `schema` | legacy alias | 旧别名；不要在新模块中使用，不再表示弹窗类 |
| `platform` | 基础能力 | 平台内置能力，不混在普通模块列表里 |

前端统一从 `frontend/src/lib/plugin-modes.ts` 读取分类。旧内置模块仍保留 key fallback，但新模块不要依赖 fallback。

---

### 统一配置页样式规范

所有账号级模块配置入口都使用独立页面，不再新增 Schema 弹窗或内部分类的用户可见分组。账号详情的“模块启停”页只展示“基础能力 · 平台内置”和“模块”两组，模块列表按 `feature.key` 首字母排序；用户界面统一称“模块”，代码、API、数据库字段和 Manifest 仍保留 `plugin` / `feature` 命名。

配置页从上到下固定为：

1. 返回按钮 + 模块标题
2. 顶部冻结“配置操作”条（只有存在可保存配置的页面需要）
3. 使用说明
4. 功能总开关
5. 配置主体（规则列表或字段表单）

#### 顶部冻结“配置操作”

长表单页必须把保存操作放在标题下方的 sticky 工具条中，参考 `ChatGPTImageConfig.tsx`、`CodexImageConfig.tsx`、`Game24Config.tsx` 和 `GenericPluginConfig.tsx`：

```tsx
<div className="sticky top-0 z-30 -mx-2 rounded-b-lg border bg-background/95 px-2 py-3 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-background/80">
  <div className="flex flex-wrap items-center justify-between gap-3">
    <div className="text-sm">
      <div className="font-medium">配置操作</div>
      <div className="text-xs text-muted-foreground">
        {dirty ? "有未保存修改，保存后 worker 会热加载。" : "当前配置已同步。"}
      </div>
    </div>
    <div className="flex items-center gap-4">
      <Button disabled={!dirty || saveMut.isPending} onClick={handleSave}>
        保存配置
      </Button>
      <Button type="button" variant="ghost" className="px-0" disabled={!dirty || saveMut.isPending} onClick={resetForm}>
        撤销
      </Button>
    </div>
  </div>
</div>
```

规则驱动页面如果单条规则在 Dialog 内保存，主页面可以不放 sticky 保存条；但 Dialog 外的说明、总开关和规则列表顺序仍必须一致。

#### 使用说明卡片

“使用说明”必须是独立 `Card`，放在“功能总开关”之前。说明内容用一层 `rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground` 包住，再用短 bullet 写真实用法、指令示例、触发条件和排障入口。不要把使用说明写成页面顶部散落的提示块，也不要把总开关塞进说明卡。

规则驱动页面复用 `RuleInfoBox`，单配置和通用 schema 页面直接使用同样结构。指令示例必须读取当前系统前缀和当前配置中的指令名，不要写死 `,draw`、`,24d`、`,cximg`。

#### 功能总开关卡片

“功能总开关”也必须是独立 `Card`，放在“使用说明”之后、“配置”之前。卡片右侧放 `Switch`，左侧展示说明、启用 Badge、`state` 和 `last_error`。关闭总开关表示当前账号不运行该模块，但仍允许进入配置页提前填写配置。

规则驱动页面复用 `RuleFeatureToggleCard`；单配置和通用页面按同样布局实现。不要再使用旧的“运行状态”卡片替代总开关。

#### 配置主体与宽度

配置主体必须独立成“配置”或“规则”卡片，宽度跟随页面容器自适应，不要给表单区域加 `max-w-lg`、`max-w-3xl` 这类窄宽限制。字段多时用响应式网格：

- 普通字段：`grid gap-4 md:grid-cols-2 xl:grid-cols-3`
- 小型配置：`grid gap-6 md:grid-cols-2`
- 复杂分组：外层 `CardContent className="space-y-6"`，内部再分组

字段控件统一使用项目内 `Input`、`Select`、`Switch`、`Textarea`、`Label`、`Button`、`Card`、`Badge`、`Table`。指令字段只填指令名，不填系统前缀；密码、Token 和只读预览字段要遵守现有脱敏和只读规则。

#### 禁止回退

- 不新增 Schema 配置弹窗；`ConfigDialog` 只作为通用 schema 表单实现细节或兼容代码存在。
- 不在账号详情页展示内部分类名或 legacy schema 分组。
- 不把“使用说明”“功能总开关”“配置”合并到同一张卡片。
- 不把保存按钮只放在长表单底部。
- 不在用户界面继续使用“插件”指代可启停能力；面向用户称“模块”。

---

### 规则驱动配置页（Forward / AutoReply / Autorepeat）

规则驱动模块每条 rule 存储独立的 `config` JSON，通过 CRUD API 管理。前端专属页面提供：规则列表 + 创建/编辑对话框 + 试运行（dry-run）。

#### 适配清单（6 处必改）

| # | 文件 | 修改内容 |
|---|------|---------|
| 1 | `frontend/src/api/types.ts` | 添加 `XxxRuleConfig` 接口（描述单条规则的 config 字段） |
| 2 | `frontend/src/pages/Plugins/configs/XxxConfig.tsx` | **新建**：规则列表页（参考 `AutoReply.tsx` 或 `Forward.tsx`） |
| 3 | `backend/app/worker/plugins/builtin/xxx/manifest.py` | `config_schema["x-ui-mode"] = "rules"` |
| 4 | `frontend/src/App.tsx` | ① import 新页面组件 ② 添加路由 `:aid/features/xxx` ③ 在 `FEATURE_CONFIG_PAGES` 中添加 key |
| 5 | `frontend/src/pages/Plugins/_shared/featureConfig.ts` | 在共享的 `FEATURE_CONFIG_PAGE_KEYS` Set 中添加 key |
| 6 | `backend/app/db/models/feature.py` | 添加 `FEATURE_XXX = "xxx"` 常量（如已有可跳过） |

#### 1. types.ts — RuleConfig 接口

```typescript
// frontend/src/api/types.ts
export interface AutorepeatRuleConfig {
  target_chat_id: number;   // 必填
  time_window?: number;     // 可选，默认 300
  min_users?: number;       // 可选，默认 5
}
```

接口字段应与 `manifest.py` 中 `config_schema.properties` 一一对应，必填字段不加 `?`。

#### 2. 新建配置页面

创建 `frontend/src/pages/Plugins/configs/XxxConfig.tsx`，核心结构：

```tsx
// 标准页面骨架（以 AutoReply 为模板）
import { useParams } from "react-router-dom";
// ... UI 组件导入

export function XxxConfig() {
  const { aid } = useParams<{ aid: string }>();
  const queryClient = useQueryClient();

  // ① 规则列表查询
  const { data: rules } = useQuery({
    queryKey: ["rules", Number(aid), "xxx"],
    queryFn: () => api.getRules(Number(aid), "xxx"),
  });

  // ② 创建/编辑对话框状态
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<RuleOut | null>(null);

  // ③ CRUD mutations（create / update / delete）
  // ④ 试运行 mutation（dry-run）
  // ⑤ 表单渲染 + 规则表格
}
```

**页面要素**：
- 顶部：返回按钮 + 模块标题
- 使用说明：独立 `Card`，复用 `RuleInfoBox`，写清触发方向、指令/规则用法和排障入口
- 功能总开关：独立 `Card`，复用 `RuleFeatureToggleCard`，展示启用 Badge、运行状态和最近错误
- 规则卡片：标题为“规则”，右侧放新建按钮，主体为规则表格（序号 / 关键字段 / 启用状态 / 操作按钮）
- 对话框：创建/编辑表单，字段来自 RuleConfig；保存按钮留在 Dialog 内
- 试运行：选规则 → 填样本消息 → 显示命中结果

#### 3. manifest.py — UI 分类

```python
config_schema={
    "type": "object",
    "x-ui-mode": "rules",
    "properties": {
        "target_chat_id": {"type": "integer", "title": "目标聊天"},
        "enabled": {"type": "boolean", "title": "启用", "default": True},
    },
}
```

#### 4. App.tsx — 路由 + 注册

```tsx
// ① import
import { XxxConfig } from "@/pages/Plugins/configs/XxxConfig";

// ② 路由
<Route path=":aid/features/xxx" element={<XxxConfig />} />

// ③ FEATURE_CONFIG_PAGES 注册
const FEATURE_CONFIG_PAGES: Record<string, { title: string; description: string }> = {
  auto_reply: { title: "自动回复", description: "..." },
  xxx:        { title: "模块显示名", description: "..." },
  // ...
};
```

路由路径格式固定为 `:aid/features/{plugin_key}`，`plugin_key` 必须与 `MANIFEST.key` 一致。

#### 5. FEATURE_CONFIG_PAGE_KEYS — 共享入口点

0.18.0 起，账号详情与模块中心统一复用同一个 helper，不再维护两份 Set。新增专属配置页时只改这一处：

```tsx
// frontend/src/pages/Plugins/_shared/featureConfig.ts
const FEATURE_CONFIG_PAGE_KEYS = new Set([
  "auto_reply", "autorepeat", "forward", "game24", "codex_image",
  "xxx",  // ← 新增
]);
```

**作用**：Set 中的 key 会让账号详情和模块中心的“配置”按钮跳转到专属页面路由 `/accounts/:aid/features/xxx`；不在 Set 中的 key 应进入通用独立配置页。历史代码和旧文档中出现的 `ConfigDialog` 只代表通用 schema 表单实现，不再是一类模块形态。

#### 6. feature.py — 后端常量

```python
# backend/app/db/models/feature.py
FEATURE_XXX = "xxx"
```

此常量供 `rules.py` dry-run 分支和其它模块引用。

---

### 规则驱动补充：后端 Dry-Run 适配

规则驱动页面通常需要试运行功能，后端需同步适配 `rules.py`：

#### 模块侧导出 _dry_run_match

```python
# backend/app/worker/plugins/builtin/xxx/plugin.py

def _dry_run_match(cfg: dict, text: str, chat_id: int | None = None) -> tuple[bool, str | None]:
    """纯函数：给定规则 config + 样本消息，返回 (matched, output)。
    不访问 DB / Redis / 网络，仅做模式匹配逻辑判断。
    """
    # 匹配逻辑（与 on_message 中使用的判断一致）
    if cfg.get("target_chat_id") and chat_id == cfg["target_chat_id"]:
        return True, "命中目标群组"
    return False, None
```

```python
# backend/app/worker/plugins/builtin/xxx/__init__.py
from .plugin import _dry_run_match  # noqa: F401 — 供 API dry-run 导入
```

#### rules.py — 添加 dry-run 分支

```python
# backend/app/api/rules.py

# ① import
from ..db.models.feature import FEATURE_XXX
from ..worker.plugins.builtin.xxx.plugin import _dry_run_match as _xxx_dry_run_match

# ② 在 dry_run_rule() 函数中，在 fallback return 之前添加分支
#    ⚠️ 必须放在最后的 `return RuleDryRunResponse(matched=False, ...)` 之前！

if key == FEATURE_XXX:
    cfg = rule.config or {}
    matched, output = _xxx_dry_run_match(cfg, payload.sample_message, payload.sample_chat_id)
    logs = [
        {"step": "config", "msg": f"关键字段：{cfg.get('xxx_field', '(未设置)')}"},
        # ... 更多诊断步骤
    ]
    if not matched:
        logs.append({"step": "result", "msg": "未命中"})
    else:
        logs.append({"step": "result", "msg": "命中"})
    return RuleDryRunResponse(
        matched=matched,
        output=output,
        detail={"feature": key, "rule_id": rid, "logs": logs},
    )
```

**常见错误**：dry-run 分支放在 `return RuleDryRunResponse(matched=False, ...)` 之后 → 永远不可达。
**正确位置**：在所有已实现的 dry-run 分支之后、fallback return 之前。

---

### 单配置对象页（Game24 / Codex Image）

只有一份配置、无规则列表的模块，使用专属页面但不需要 CRUD 和 dry-run：

- 创建 `frontend/src/pages/Plugins/configs/XxxConfig.tsx`，直接展示/编辑单个 config 对象
- `manifest.py` 中声明 `config_schema["x-ui-mode"] = "single"`
- 其余适配步骤与规则驱动专属页相同（App.tsx 路由 + FEATURE_CONFIG_PAGES + 两个 PAGE_KEYS）
- 后端不需要 dry-run 分支

#### 页面布局约定

单配置对象页参考 `Game24Config.tsx`、`CodexImageConfig.tsx` 与 `ChatGPTImageConfig.tsx`，并遵守“统一配置页样式规范”。页面从上到下固定为：

1. 返回按钮 + 模块标题
2. 顶部冻结“配置操作”条（保存配置 / 撤销，滚动长表单时保持可见）
3. 使用说明（真实触发指令示例、参数示例、注意事项）
4. 功能总开关（当前账号是否启用、关键运行状态、最近错误）
5. 配置表单（账号级配置为主，必要时展示全局配置）

“使用说明 → 功能总开关 → 配置”要作为三张独立卡片，不要把总开关塞进说明或配置里。单配置模块通常靠指令触发，用户最关心的是“怎么叫它”“现在能不能用”“要改哪些参数”，所以顺序保持稳定。配置字段要按可用屏幕宽度展开，避免窄表单造成长配置反复滚动。

#### 指令型模块配置

如果模块支持自定义触发指令，应同时做三件事：

```python
class XxxPlugin(Plugin):
    key = "xxx"
    command_config_keys = {"command"}
```

```python
config_schema={
    "type": "object",
    "x-ui-mode": "single",
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "xxx",
            "description": "跟在系统指令前缀后使用，支持中文；不要包含空格。",
        },
    },
}
```

- `command_config_keys` 用于告诉 loader：指令字段变化后要重启该模块并重新注册指令。
- 指令名支持中文，例如 `,画图 一只猫`；但不能包含空格，因为指令解析以第一个空白分隔指令和参数。
- 说明文案必须用当前配置中的指令动态生成，不要把 `,cximg`、`,24d` 写死。

#### 已有单配置模块字段参考

| 模块 | 推荐字段 | 说明 |
|------|---------|------|
| `game24` | `command`, `timeout` | 触发指令名、答题限时 |
| `codex_image` | `command`, `access_token`, `model`, `message_template`, `image_size`, `aspect_ratio`, `image_format`, `max_wait_seconds`, `status_interval_seconds`, `delete_command_message`, `show_revised_prompt`, `reasoning_effort`, `custom_instructions` | 触发指令、鉴权、模型、消息模板、图片尺寸/比例/格式、等待与状态提示、输出行为、自定义生成指令 |

专属页面字段应与运行时实际读取的配置保持一致；`manifest.config_schema` 也要同步，避免通用配置页、接口校验和文档出现三套口径。

`codex_image` 已恢复为真正的内置实验模块，代码位于 `backend/app/worker/plugins/builtin/codex_image/`，会随后端镜像发布并由 builtin registry 自动 seed。旧数据库里的 `account_feature(feature_key="codex_image")` 不需要迁移，worker 会按普通内置模块路径加载；若未来再次作为远程模块发布，必须另起 key 或先设计清晰迁移策略，避免和 builtin key 冲突。

---

### 通用 Schema 驱动独立页（legacy schema 兼容）

不再新增“Schema 弹窗”类模块。历史上无专属页面的模块可能声明 `x-ui-mode: "schema"`，现在应把它理解为“由 `config_schema` 提供字段的通用单配置独立页”：

- `level: "global"` 的字段 → 全局配置区
- `level: "account"` 或无 level → 账号配置区
- **不需要**添加到 `FEATURE_CONFIG_PAGE_KEYS`，不需要创建模块专属页面文件
- 新模块请优先写 `config_schema["x-ui-mode"] = "single"`；`schema` 只保留为旧模块兼容别名
- 页面同样使用“使用说明 → 功能总开关 → 配置”的独立卡片顺序，并在有可保存字段时显示顶部冻结“配置操作”条
- 页面宽度、滚动高度、字段间距和控件风格应与 ChatGPT2API / 自定义指令 / LLM 等系统配置页保持一致：使用统一的 `Input`、`Select`、`Switch`、`Textarea`、`Label` 视觉语言，不在字段标题里放 emoji 或临时说明块
- 普通配置字段展示在配置区顶部；`message_template` / `*_message_template` / `*_template` 等消息模板字段进入“消息模板”折叠组；`template_preview` / `*_preview` 进入底部“预览结果”。
- `message_template`、`*_message_template`、`prompt`、`content`、`text` 等长文案字段会按多行文本体验展示；字段描述里应写清占位符和示例值。
- `field.readOnly === true`、`template_preview`、`*_preview`、`template_placeholders` 会自动按只读块渲染，不会保存回配置；其中预览字段使用 `TelegramHtmlPreview` 展示最终 HTML 消息效果。
- 多个预览字段应在同一个 Telegram 风格预览场景里按字段顺序展示为多条气泡，方便同时检查开局、进行中、答对、超时、取消和错误提示等模板。

```python
# config_schema 示例（适用于通用独立配置页自动渲染）
config_schema={
    "type": "object",
    "x-ui-mode": "single",
    "properties": {
        "api_key": {
            "type": "string",
            "title": "API Key",
            "level": "global",
        },
        "threshold": {
            "type": "number",
            "title": "阈值",
            "default": 5,
        },
    },
}
```

---

### 基础能力：平台内置功能（Scheduler）

基础能力不是普通模块卡片，而是系统运行时一起初始化的服务。比如 `scheduler` 现在属于平台内置调度能力：页面仍可配置定时任务，但不再强调“作为模块启停”。

适配规则：

- `manifest.py` 声明 `config_schema["x-ui-mode"] = "platform"`
- 前端会在账号详情和模块中心里放到“基础能力 / 平台内置”分组
- 如果有专属页面，仍需 `App.tsx` 路由和共享 `FEATURE_CONFIG_PAGE_KEYS`
- 后端运行时由 `PlatformScheduler` 常驻初始化；调度算法与 action 执行在平台层，`scheduler` 模块壳只保留兼容入口或配置入口
- 普通模块需要定时执行时，不要自己 `create_task` 写永久循环，优先使用 `ctx.scheduler`

#### 模块调用平台调度器

`ctx.scheduler` 是绑定到当前模块的最小 capability facade。模块只能注册 / 注销自己名下的任务，热重载、禁用、worker 退出时 loader 会统一清理，避免旧 callback 继续触发。

```python
from app.worker.scheduler_runtime import ScheduledJob


class DemoPlugin(Plugin):
    key = "demo"

    async def on_startup(self, ctx: PluginContext) -> None:
        if ctx.scheduler is None:
            return
        ctx.scheduler.register(
            "daily_digest",
            {"kind": "cron", "cron": "0 9 * * *"},
            self._send_daily_digest,
        )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if ctx.scheduler is not None:
            ctx.scheduler.unregister_all()

    async def _send_daily_digest(self, job: ScheduledJob) -> None:
        # callback 可闭包引用模块自己的状态，也可以在 config 中保存轻量参数
        ...
```

支持的 `schedule` 字段与定时任务页面一致：

| 类型 | 示例 | 说明 |
|------|------|------|
| `cron` | `{"kind": "cron", "cron": "*/10 * * * *"}` | 按系统时区解析 cron |
| `interval` | `{"kind": "interval", "interval_sec": 300}` | 首次 tick 会立即执行一次，之后按间隔推进 |
| `once` | `{"kind": "once", "fire_at": "2026-05-11T10:00:00+00:00"}` | 执行后自动置为 disabled |

注意：

- callback 异常会写入模块日志，并保留任务等待下次 tick；不要把异常吞掉后静默失败
- `ctx.scheduler` 注册的是运行期任务；worker 重启后会由模块 `on_startup` 重新注册，若需要精确保存 `last_fire` / `next_fire`，模块应把状态写回自己的配置或规则表
- 如果任务依赖模块配置，配置变更后建议触发模块热重载，或在 callback 中读取最新 `ctx.config`
- 第三方模块拿到的是 scheduler facade，不会直接获得 Redis / DB / Telethon session
- GUI 定时任务页仍走 `Rule(feature_key="scheduler")`，由同一个 `PlatformScheduler` 调度；后续新增模块不要依赖 `SchedulerPlugin`，只依赖 `ctx.scheduler`

---

### 风格要求

- 与 TelePilot 现有页面风格一致
- React + TypeScript + TailwindCSS
- 新页面参考 `AutoReply.tsx`（规则驱动）、`Game24Config.tsx` / `CodexImageConfig.tsx` / `ChatGPTImageConfig.tsx`（单配置）或 `GenericPluginConfig.tsx`（通用 schema）的代码结构
- 使用说明、功能总开关、配置主体必须是独立卡片，顺序固定为“使用说明 → 功能总开关 → 配置”
- 有可保存字段的长表单必须使用顶部冻结“配置操作”条，按钮文案统一为“保存配置”“撤销”
- 配置区域宽度随页面自适应，不使用窄 `max-w-*` 限制；字段多时使用响应式 grid
- 表格列宽要稳定，账号详情页和模块中心的同类列表要纵向对齐
- 配置按钮不依赖启用状态；即使模块当前关闭，也应允许先配置
- 用户界面统一称“模块”，开发文档、API、代码标识可以继续使用 plugin / feature

### 适配自检清单

新增模块前端配置页后，逐项检查：

- [ ] `manifest.py` 中 `config_schema["x-ui-mode"]` 已声明：推荐 `rules` / `single` / `platform`；仅旧模块保留 `schema`
- [ ] `types.ts` 中 `XxxRuleConfig` 接口与 `manifest.py` config_schema 字段一致
- [ ] 如果有专属页面：`App.tsx` 中路由路径 `:aid/features/{key}` 与模块 key 一致
- [ ] 如果有专属页面：`App.tsx` 中 `FEATURE_CONFIG_PAGES` 包含该 key
- [ ] 如果有专属页面：`frontend/src/pages/Plugins/_shared/featureConfig.ts` 的 `FEATURE_CONFIG_PAGE_KEYS` 包含该 key
- [ ] 如果是指令型模块：`command` 字段可配置，`Plugin.command_config_keys = {"command"}`，说明文案动态读取当前指令
- [ ] 指令型模块的帮助、取消/结束、撤销、自动删除、冷却/超时、消息模板等用户常调行为已尽量配置化；帮助模板支持 `{prefix}`，不硬编码 `,命令`
- [ ] `owner_only=False` 仅用于开放 `on_message`，没有把普通 incoming 消息当成管理指令入口
- [ ] 页面按“使用说明 → 功能总开关 → 配置”的独立卡片顺序排布；不要把说明、总开关和配置混在一张卡片
- [ ] 有可保存字段的页面使用顶部冻结“配置操作”条；长配置不只在底部放保存按钮
- [ ] 配置主体宽度自适应屏幕宽度，字段用响应式 grid 或分组，不使用窄 `max-w-*` 限制
- [ ] 用户可见文案使用“模块”，不展示内部分类名或“Schema 弹窗”
- [ ] 如需 dry-run：`plugin.py` 导出 `_dry_run_match`，`__init__.py` re-export，`rules.py` 在 fallback 之前添加分支
- [ ] 如需 dry-run：`feature.py` 中有 `FEATURE_XXX` 常量
- [ ] 前端 `pnpm -C frontend exec tsc -b --noEmit` 和 `pnpm -C frontend build` 通过

---

## 14. 模块工程规范（Plugin 实现）

这一章是给模块作者看的“不要踩坑”规范。只要模块涉及发消息、抢答、后台任务、奖励或远程发布，都建议先按这里的模板走。

### 发布与交互体验检查清单

这部分是发布前的产品质量门槛。模块能跑起来只是第一步；能被用户确认版本、理解状态、稳定退出、少刷屏，才算适合放进模块市场。

#### 版本与发布

- 远程模块发布时必须同步更新所有元数据入口的版本号：`plugin.json.version`、`manifest.py` 里的 `MANIFEST.version`、Registry 索引中的 `version`。
- `plugin.json` 是安装/更新阶段的静态来源，`manifest.py` 是运行阶段的真实 Manifest。两者版本不一致时，市场展示、配置缓存和运行日志会很难排查。
- 需要热更新验证的模块，建议在 `on_startup` 日志和主要业务消息中暴露版本，例如 `"[quiz] 已启动 v1.2.3，指令：quiz"`。
- 发布说明里要写清最低 TelePilot 版本、权限、依赖库、是否需要 `send_file` / `delete_message` 等敏感能力；版本字段优先写 `min_telepilot_version`。

#### 消息与交互

- 优先复用用户触发指令消息或已有业务消息：指令状态用 `event.edit(...)`，题面进度用编辑原题面消息，答对奖励再回复答题者消息。
- 模块进行中时，重复触发指令必须给出明确提示，并说明下一步：继续当前流程、等待超时、或使用 `stop` / `cancel` / `结束`。
- 指令型模块必须提供帮助入口或帮助子命令，例如 `help` / `status` / 空参数展示帮助；帮助内容要显示当前配置的指令名和当前系统指令前缀。
- 视觉题、图片题、文件题不要在文本说明、alt 文案、日志或 preview 中泄露答案；文本只说明规则和限时。
- 避免连续发送多条含义重复的消息。降级发送时也要保证“同一事件只产生一个用户可见结果”。

#### 状态管理

- 明确区分 `idle` / `running` / `completed` / `cancelled` / `timeout` / `failed` 等状态，并为每种状态设计用户可见反馈。
- 同一聊天只能有一个活跃任务时，启动前必须检查已有状态；不要直接覆盖进行中的局。
- 每个流程都要有取消或强制结束入口，默认可用 `stop`、`cancel`、`结束` 等别名，并在结束时清理状态和后台任务。
- 状态对象里要冻结单局动态参数，例如奖励、题目、答案、超时时间；一局进行中不要反复读取可变配置。

#### 防滥用与公平性

- 抢答、竞猜、投票、抽奖等高频交互模块，应设计用户级或聊天级冷却，避免刷屏、暴力枚举和误触抢占。
- 冷却、限流、超时等策略尽量配置化，并在开局文案中说明关键规则，例如“每人每 3 秒可答一次，限时 60 秒”。
- 游戏类/互动类模块建议使用 `plugin.json.tags` 标记为 `game`、`quiz`、`interactive`，后续平台可按标签做统一启停、限流或分组展示。当前不要自己发明全局开关协议，先复用模块全局开关、账号级开关和配置项。
- 媒体型题面必须保证可观测、可辨认、不遮挡；如果图片或文件发送失败，应降级为明确错误提示，而不是泄露答案。

#### 资源清理与降级

- 模块产生的临时消息、图片、文件、后台任务、定时器，应在完成、取消、超时、禁用、热重载和卸载时清理。
- 群聊类模块建议提供消息清理策略，例如 `cleanup_mode` / `cleanup_delay_seconds` / `delete_command_message`，并允许用户选择保留记录。
- 平台不支持编辑、删除、发媒体时，应降级为回复文本或普通发送；降级路径要写日志，并避免发送多条重复消息。
- 可配置指令发生变化时，建议保留常用历史别名一段时间，或在重复触发/未知指令提示里告诉用户新指令。

### 指令权限底线

`owner_only` 不是“公开指令开关”。框架约定如下：

- `commands` 只处理当前账号 outgoing 指令；普通群成员直接发送 `{prefix}{command}` 不会触发模块命令。
- `owner_only=False` 只开放 `on_message`，用于答题、口令、领取码、关键词参与等普通消息监听。
- 平台内部动作（自动回复、scheduler）如果需要触发指令，应通过内部命令派发能力执行，并把返回结果转成回复/普通发送；不要要求用户直接发送管理指令。
- 指令 handler 内可以假设事件来自当前账号 outgoing 消息，因此可以优先 `event.edit(...)`；`on_message` 处理 incoming 消息时不要 `event.edit(...)`。

示例模型：

```text
用户: 我想玩 24 点
自动回复规则: 命中关键词后由 TelePilot 内部执行 "{prefix}24d 100"
模块 commands: 本账号开局
模块 on_message: 普通成员提交答案，答对后反馈奖励
```

不要这样做：

```text
用户: {prefix}24d 100
模块: 直接开局
```

普通成员要参与流程，应发答案、口令或关键词，而不是发送系统指令。

### 消息发送能力边界

不同回调能拿到的对象不同，不要在没有 `event` 的地方调用 `event.reply`，也不要尝试编辑别人的 incoming 消息。推荐按下表选择发送方式：

| 场景 | 有 `event` | 推荐方式 | 适用说明 | 远程模块权限 |
|------|------------|----------|----------|--------------|
| `on_command` 指令回调 | 有 | `event.edit(...)` | 把用户发出的指令改成状态/结果，适合 UserBot 自己发出的指令 | `edit_message` |
| `on_command` 需要另发一条 | 有 | `event.respond(...)` 或 `ctx.client.send_message(event.chat_id, ...)` | 不想覆盖原指令，或指令消息可能已删除 | `send_message` |
| `on_message` 回复触发消息 | 有 | `event.reply(...)` | 自动回复、答题奖励、引用原消息 | `send_message` |
| `on_message` 普通发送 | 有 | `event.respond(...)` | 在同一聊天里发新消息，不引用原消息 | `send_message` |
| 跨聊天发送/转发 | 有或无 | `ctx.client.send_message(target_chat_id, ...)` / `ctx.client.send_file(...)` | 转发、通知、发图、调度任务 | `send_message` / `send_file` |
| `ctx.scheduler` 定时回调 | 无 | `ctx.client.send_message(chat_id, ...)` | 定时任务没有原始 `event`，必须从配置或规则里拿 `chat_id` | `send_message` |
| `on_startup` / `on_shutdown` | 无 | 默认不发；确需通知时用 `ctx.client.send_message(...)` | 启停阶段容易重复触发，必须有显式配置开关 | `send_message` |
| `ctx.conversation()` | conversation 内部 | `conv.send(...)` / `conv.get_response(...)` | 与 BotFather 或其它 bot 进行会话 | 取决于底层发送/读取能力 |

#### 安全回复模板

群组、频道、匿名频道消息里，`event.reply` 可能失败。需要强可靠发送时，使用“reply 优先，send_message 兜底”的写法：

```python
async def safe_reply(ctx, event, text: str, *, chat_id: int, reply_to_id: int | None = None) -> bool:
    try:
        reply = getattr(event, "reply", None)
        if callable(reply):
            await reply(text)
            return True
    except Exception as exc:
        if ctx.log:
            await ctx.log(
                "warn",
                "引用回复失败，准备改用普通发送兜底。",
                error=type(exc).__name__,
                chat_id=chat_id,
                reply_to_id=reply_to_id,
            )

    if ctx.client is None:
        return False

    try:
        await ctx.client.send_message(chat_id, text, reply_to=reply_to_id)
        return True
    except Exception:
        await ctx.client.send_message(chat_id, text)
        return True
```

注意：

- `event.edit(...)` 只适合编辑当前账号自己发出的指令/状态消息；不要用它编辑别人发来的 incoming 消息。
- 远程模块安装阶段只读 `plugin.json`，但运行时仍会受 `manifest.py` 的 `permissions` 沙箱限制。
- 第三方模块不要把 `event.reply/respond/edit` 当作绕过权限的路径；凡是会发送、编辑、删除、读取消息的行为，都必须在 `permissions` 中声明对应能力。
- 需要发送图片、文件时，为 `BytesIO` 设置 `name`，例如 `image_file.name = "result.png"`，否则 Telegram 客户端可能显示无后缀文件。
- 长消息要按 Telegram 4096 字符限制分段；HTML 模式下切分前要保证标签闭合，失败时应降级为纯文本。

### 并发与抢答标准模板

抢答类、竞猜类、抽奖类模块都要处理并发：多个人几乎同时答对时，只能有一个人获胜。推荐使用 `chat_id -> asyncio.Lock`，并在加锁后再次检查状态。

```python
import asyncio
from collections import defaultdict


class QuizPlugin(Plugin):
    key = "quiz"
    display_name = "抢答示例"
    message_channels = {"incoming"}
    owner_only = False

    def __init__(self) -> None:
        super().__init__()
        self._games: dict[int, dict] = {}
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def on_message(self, ctx: PluginContext, event) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        text = str(getattr(event, "raw_text", "") or "").strip()
        if not chat_id or not text:
            return

        lock = self._locks[chat_id]
        async with lock:
            # 第一次检查：是否有进行中的局
            game = self._games.get(chat_id)
            if not game or game.get("answered"):
                return

            # 判断可能比较耗 CPU 时，尽量先做轻量过滤，再进入重计算
            if not self._is_correct(text, game):
                return

            # 第二次状态变更必须在锁内完成，防止两个答对者同时发奖
            game["answered"] = True
            self._games.pop(chat_id, None)

        # 发消息可以放到锁外，避免网络慢时阻塞同群后续消息
        await safe_reply(ctx, event, f"答对了，奖励 {game['reward']} 分！", chat_id=chat_id)

    def _is_correct(self, text: str, game: dict) -> bool:
        ...
```

常见竞态：

- 只在锁外检查 `answered`：两个协程都看到未答对，最后发两次奖励。
- 在锁内等待网络请求：一个群的消息会被长时间阻塞。
- 超时任务和答题消息同时结束一局：超时回调也要拿同一把锁，再二次检查。
- 热重载没有清理状态：旧任务继续执行，和新模块实例抢状态。

### 配置项完整性原则

模块作者要尽量把“用户可能合理想改”的行为做成配置项，而不是写死在代码里。尤其是互动类、游戏类、生成类和通知类模块，至少检查这些能力是否需要外露：

- 指令名：主指令、帮助子命令、取消/结束命令、撤销命令、管理子命令。
- 自动删除：是否删除触发指令、是否删除中间状态、完成后多久清理、失败时是否保留排障信息。
- 消息模板：帮助、开局、进行中、成功、失败、超时、取消、撤销、冷却、权限拒绝。
- 交互策略：冷却时间、超时时间、是否引用原消息、是否保留历史别名、是否允许指定群启用。
- 输出行为：HTML/纯文本、是否展示详细错误、是否展示内部 ID、是否发送预览或来源说明。

帮助模板必须支持 `{prefix}` 占位符。不要在帮助列表里硬编码 `,命令`，否则用户把系统指令前缀改成 `。`、`/` 或其它字符后，帮助会误导。推荐默认帮助模板写成：

```text
{prefix}{command} 100 - 开始一局
{prefix}{command} status - 查看状态
{prefix}{command} {help_command} - 查看帮助
{prefix}{command} {cancel_command} - 取消当前流程
```

运行时渲染帮助时，`{prefix}` 应来自 `current_command_prefix()` 或平台注入的当前前缀；配置页预览时，`{prefix}` 应来自系统设置中的 `command_prefix`，拿不到时才用 `,` 兜底。

### 统一配置项命名与校验

新增模块尽量复用以下字段名，减少前端、文档、Bot 指令和用户认知的分裂。

| 字段 | 类型 | 推荐默认值 | 推荐范围/校验 | 说明 |
|------|------|------------|---------------|------|
| `command` | string | 模块短名 | 1-32 字符，不含空白，支持中文 | 触发指令名，配合 `command_config_keys = {"command"}` |
| `help_command` | string | `help` | 1-32 字符，不含空白 | 帮助子命令或独立帮助指令名 |
| `help_message_template` | string | 内置模板 | 建议限制最大长度 | 帮助文本模板，必须支持 `{prefix}` 和 `{command}` |
| `default_reward` | integer | `0` | `0` 到业务允许上限 | 可选默认奖励；抢答/下注类模块的单局奖励优先由指令参数传入 |
| `timeout` | integer | `60` | 10-86400 秒 | 用户可理解的超时秒数；已有模块沿用该字段 |
| `auto_next` | boolean | `false` | 布尔 | 游戏/任务结束后是否自动开下一轮 |
| `message_template` | string | 内置模板 | 建议限制最大长度 | 用户可编辑输出消息模板 |
| `template_preview` | string | 只读示例 | 由前端/后端生成 | 展示模板渲染后的示例文本，不参与运行时配置 |
| `status_interval_seconds` | integer | `30` | 10-300 秒 | 状态编辑频率，避免频繁编辑触发风控 |
| `cooldown_seconds` | integer | `0` | 0-3600 秒 | 聊天级或用户级冷却时间 |
| `cleanup_delay_seconds` | integer | `0` | 0-86400 秒 | 流程结束后延迟清理临时消息 |
| `cancel_commands` | array[string] | `["stop", "cancel", "结束"]` | 每项 1-32 字符，不含空白 | 取消/强制结束指令别名 |
| `undo_command` | string | `undo` | 1-32 字符，不含空白 | 撤销上一步、撤回本轮或回滚最近动作的指令名 |
| `allowed_chat_ids` | array[int] | `[]` | 留空表示不限制 | 限制模块只在指定聊天生效 |
| `delete_command_message` | boolean | `false` | 布尔 | 指令完成后是否删除原指令 |
| `auto_delete_enabled` | boolean | `false` | 布尔 | 是否自动删除模块产生的临时消息 |
| `auto_delete_delay_seconds` | integer | `0` | 0-86400 秒 | 自动删除延迟；0 表示立即或不启用，按模块语义说明 |

示例：

```python
class GamePlugin(Plugin):
    key = "game"
    command_config_keys = {"command"}


config_schema={
    "type": "object",
    "x-ui-mode": "single",
    "properties": {
        "command": {
            "type": "string",
            "title": "触发指令名",
            "default": "game",
            "minLength": 1,
            "maxLength": 32,
            "pattern": r"^\S+$",
            "description": "跟在系统指令前缀后使用，支持中文；不要包含空格。",
        },
        "default_reward": {
            "type": "integer",
            "title": "默认奖励",
            "default": 0,
            "minimum": 0,
            "description": "仅作为指令未传奖励时的兜底值；本轮奖励建议通过指令参数传入。",
        },
        "timeout": {
            "type": "integer",
            "title": "超时时间（秒）",
            "default": 60,
            "minimum": 10,
            "maximum": 86400,
        },
        "auto_next": {
            "type": "boolean",
            "title": "结束后自动下一轮",
            "default": False,
        },
    },
}
```

配置页只适合放长期稳定配置，例如 `command`、`help_command`、`cancel_commands`、`undo_command`、`timeout`、`auto_next`、`message_template`、`delete_command_message`、`auto_delete_enabled`。像奖励金额、题目范围、下注金额这类单局动态参数，优先从指令参数读取，例如 `{prefix}game 100`，并在开局时冻结到本轮状态里。

### 模板配置与占位符

凡是会向 Telegram 用户发送、编辑或回复文案的模块，都必须把用户可见文案模板化，尤其是开局文案、进行中文案、答对文案、超时文案、取消文案和错误提示。模板配置要参考“通用模板 → 自定义指令模板”的输出模板编辑体验，告诉用户可用占位符、含义和示例。

不要把面向用户的句子硬编码在 `plugin.py` 里。代码里只能保留模板默认值、不可恢复的兜底错误、内部日志和开发者调试信息；只要这段文字可能在群聊、私聊、指令回复、状态编辑或媒体 caption 中出现，就应该有对应的 `*_message_template` 配置项，或至少复用一个通用 `message_template`。

推荐字段拆分：

| 字段 | 说明 |
|------|------|
| `help_message_template` | 帮助/用法文案；必须支持 `{prefix}` 和 `{command}` |
| `start_message_template` | 开局/题面文案 |
| `progress_message_template` | 进行中状态文案 |
| `success_message_template` | 答对/成功文案 |
| `timeout_message_template` | 超时文案 |
| `cancel_message_template` | 取消/结束文案 |
| `undo_message_template` | 撤销/回滚成功文案 |
| `error_message_template` | 可恢复错误文案 |

占位符说明建议写进 `description`，并保持稳定：

```python
"start_message_template": {
    "type": "string",
    "title": "开局文案模板",
    "default": "第 {round} 轮开始，奖励 +{reward}，限时 {timeout}s。",
    "description": (
        "可用占位符："
        "{round}=轮次，例如 1；"
        "{reward}=本轮奖励，例如 100；"
        "{timeout}=限时秒数，例如 60；"
        "{command}=当前触发指令，例如 game；"
        "{prefix}=系统指令前缀，例如 ,。"
    ),
}
```

`{prefix}` 是平台约定的系统级占位符，表示“系统设置 → 指令前缀”的当前值。运行时需要展示指令示例、帮助列表、错误提示里的用法示例时，优先从 worker 的当前指令前缀读取；前端配置预览中应通过 `getSystemSettings().command_prefix` 注入示例上下文，接口未返回时才兜底使用 `,`。不要把逗号硬编码成固定前缀。

如果模块有专属配置页，建议提供只读预览：用户修改模板后，用示例上下文渲染一段 `template_preview`。预览应展示“模板 + 示例上下文”替换后的最终消息效果，而不是简单重复默认值或字段说明。没有专属页面时，也至少在字段描述里给出一条完整示例，避免用户猜最终效果。

配置页里的模板预览体验应对齐自定义指令模板：模板输入、占位符说明/按钮、最终消息预览三者放在同一个配置上下文里。预览只使用模拟数据，不读取真实群消息，也不触发实际发送；如果模板支持 Telegram HTML，应复用 `frontend/src/components/TelegramHtmlPreview.tsx`。

#### Telegram 消息预览规范

模板预览用于回答一个问题：“这段模板最终发到 Telegram 里大概长什么样？”它不是字段说明，也不是原始模板文本回显。

- 预览必须使用示例上下文渲染最终消息，例如把 `{answer}`、`{question}`、`{sources}` 等占位符替换成模拟值。
- 如果模板支持 `{prefix}`，预览必须使用系统设置里的 `command_prefix` 渲染，不要硬编码为 `,`。
- 预览必须使用 Telegram 风格聊天场景：浅色聊天背景、左侧示例用户消息、右侧 TelePilot 蓝色气泡、时间和已读状态。不要只用普通灰色文本框展示。
- HTML 模式允许展示 Telegram 常用标签效果：`<b>`、`<i>`、`<code>`、`<pre>`、`<blockquote expandable>`；不支持的标签应被转义为普通文本。
- Markdown / plain 模式在同一气泡里按纯文本预览，不尝试模拟完整 Telegram Markdown 解析。
- 预览只使用模拟数据，不读取真实聊天、账号、用户资料，也不触发发送或编辑消息。
- 如果模块或页面需要做消息模板预览，优先直接使用 `TelegramHtmlPreview`；只有需要嵌入极小空间时，才使用更轻量的纯内容预览。

通用独立配置页兼容已有 schema 约定：`message_template` / `*_template` 是可编辑多行模板；`template_placeholders` 是只读占位符说明；`template_preview` / `*_preview` 是只读渲染预览。模块只需在 schema 中提供这些字段和默认值，不需要额外协议。多个 `*_preview` 字段会合并到同一个预览区域，以多条 Telegram 气泡展示。

### 定时任务与后台任务生命周期

优先使用平台调度器：

- 重复执行、cron、一次性延迟任务：用 `ctx.scheduler.register(...)`。
- 模块禁用、热重载、worker 退出时，loader 会清理该模块名下的调度任务。
- 调度回调没有 `event`，必须从 `job.config` 或模块状态里拿 `chat_id`、模板、目标参数。

只有短期后台动作才建议自己 `asyncio.create_task`，例如“本轮游戏 60 秒后超时”。自建 task 必须集中管理并在 `on_shutdown` 取消：

```python
class RoundPlugin(Plugin):
    key = "round"

    def __init__(self) -> None:
        super().__init__()
        self._tasks: set[asyncio.Task] = set()

    def _track_task(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def on_command(self, ctx, cmd, args, event) -> bool:
        if cmd != "round":
            return False
        task = asyncio.create_task(self._round_timeout(ctx, event.chat_id, timeout=60))
        self._track_task(task)
        await event.edit("已开局")
        return True

    async def on_shutdown(self, ctx) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _round_timeout(self, ctx, chat_id: int, timeout: int) -> None:
        try:
            await asyncio.sleep(timeout)
            # 超时前必须再次检查该局是否仍存在
        except asyncio.CancelledError:
            raise
```

不要这样做：

- `while True: await asyncio.sleep(...)` 的永久循环。
- task 不保存引用，导致卸载时无法取消。
- `on_shutdown` 里只 `cancel()` 不 `await gather()`。
- 超时任务不做二次状态检查，导致已答对后仍发超时消息。

### 奖惩系统接入约定

当前 TelePilot 没有统一积分服务时，模块奖励分三类：

| 模式 | 适用场景 | 推荐做法 |
|------|----------|----------|
| 文案奖励 | 游戏娱乐、无真实积分 | 只发送“奖励 X 分/金币”的消息，并写模块日志 |
| 模块内记分 | 模块自己维护排行榜 | 仅写自己模块的配置/状态文件或专属表，不直接改其它模块数据 |
| 统一积分接口 | 后续平台提供积分服务后 | 通过平台 service/event 接口发放，模块不直接操作 DB |

奖励日志建议统一字段：

```python
await ctx.log(
    "info",
    "抢答成功，准备发放奖励。",
    chat_id=chat_id,
    winner_id=sender_id,
    reward=reward,
    reward_mode="text_only",
    round_id=round_id,
)
```

约束：

- 不要在日志里记录完整昵称、完整消息正文或隐私文本。
- 真正记分前必须保证“首个答对”已经在锁内原子判定。
- 奖励金额不建议作为抢答类模块的固定配置项；优先由触发指令携带，例如 `{prefix}game 100`。
- 开局时把本轮奖励写入局状态，例如 `RoundState.reward`；一局进行中不要再读取运行时可变配置，避免配置变更导致结算金额漂移。
- 答对后建议两步反馈：先回复答对者消息发送纯文本奖励（如 `+100`），再编辑原题目消息追加答对者、正确答案、奖励金额、耗时等结算信息。
- 图片题面模块必须在 `plugin.json` 和 `manifest.py` 的 `permissions` 中声明 `send_file`，并给发送的文件设置明确后缀名。
- 图片题面模块不要隐式依赖未声明系统库；如果不用 Pillow，可以说明使用标准库生成 PNG；如果必须使用 Pillow、numpy 等第三方库，要在 README 或模块说明中写清安装约束。
- 奖励发送失败要写 `warn/error` 日志，并说明是否已经兜底发送普通消息。

### 模块最小测试清单

发布前至少覆盖这些路径：

- [ ] 指令能触发，指令名改配置后能热重载生效。
- [ ] 指令冲突时只由一个模块处理，`on_command` 正确返回 `True/False`。
- [ ] 群聊、私聊、频道/匿名频道场景下不崩溃。
- [ ] `event` 兼容裸 `Message`：不直接假设 `event.outgoing`、`event.message.id` 存在。
- [ ] 重复开局/重复创建规则会给出明确提示。
- [ ] 抢答并发：两个答对消息同时到达只奖励一次。
- [ ] 超时任务和答题消息同时发生时，只结束一次。
- [ ] 模块禁用、热重载、worker shutdown 后没有幽灵 task。
- [ ] 远程模块 `plugin.json`、`manifest.py`、`__init__.py`、`plugin.py` 均可被加载。
- [ ] 远程模块 `plugin.json.version`、`MANIFEST.version`、Registry `version` 一致。
- [ ] 远程模块不是单文件旧结构；缺少 `manifest.py` 或 `__init__.py` 会被安装阶段拒绝。
- [ ] 缺权限时远程模块会收到可理解的 `PermissionError`，不会访问 session/Redis/engine。
- [ ] 主要交互消息或启动日志能显示当前模块版本，便于确认热更新是否生效。
- [ ] 模板类配置包含占位符说明和示例预览。
- [ ] 高频交互模块有冷却/限流/超时策略，并在用户文案里说明关键规则。
- [ ] 取消、完成、超时、禁用、热重载路径都会清理状态和后台任务。
- [ ] 所有外部 HTTP 请求有 timeout，错误提示不泄露 token、路径、session。
- [ ] 模块日志能说明“收到什么、判断了什么、为什么跳过/为什么执行、失败在哪一步”。

### 可复制的游戏模块骨架

下面是一个最小抢答游戏骨架，包含指令注册、状态管理、并发锁、超时、日志和清理。真实模块可以从这里删改。

**plugin.py：**

```python
from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register


@dataclass
class RoundState:
    chat_id: int
    answer: str
    reward: int
    timeout: int
    answered: bool = False


def _event_text(event: Any) -> str:
    msg = getattr(event, "message", event)
    return str(getattr(event, "raw_text", None) or getattr(msg, "raw_text", None) or "").strip()


@register
class GuessNumberPlugin(Plugin):
    key = "guess_number"
    display_name = "猜数字"
    message_channels = {"incoming"}
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._rounds: dict[int, RoundState] = {}
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: set[asyncio.Task] = set()

    async def on_startup(self, ctx: PluginContext) -> None:
        self.commands = {self._command(ctx): self._cmd_start}
        if ctx.log:
            await ctx.log("info", "猜数字模块已启动。", command=self._command(ctx))

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._rounds.clear()
        self._locks.clear()

    async def _cmd_start(self, client, event, args: list[str], account_id: int, ctx: PluginContext) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        # 单局奖励优先由指令参数传入，如：,guess 100。
        # 没传时才使用 default_reward 兜底，并在开局时冻结到 RoundState。
        reward = int(args[0]) if args else int(ctx.config.get("default_reward", 0) or 0)
        timeout = int(ctx.config.get("timeout", 60) or 60)

        async with self._locks[chat_id]:
            if chat_id in self._rounds:
                await event.edit("当前聊天已有进行中的游戏。")
                return
            answer = str(random.randint(1, 9))
            self._rounds[chat_id] = RoundState(chat_id, answer, reward, timeout)

        task = asyncio.create_task(self._timeout_round(ctx, chat_id, timeout))
        self._track_task(task)
        await event.edit(f"猜一个 1-9 的数字，限时 {timeout} 秒，奖励 +{reward}。")

    async def on_message(self, ctx: PluginContext, event) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        text = _event_text(event)
        if not chat_id or not text:
            return

        async with self._locks[chat_id]:
            state = self._rounds.get(chat_id)
            if not state or state.answered:
                return
            if text != state.answer:
                return
            state.answered = True
            self._rounds.pop(chat_id, None)

        if ctx.log:
            await ctx.log("info", "猜数字答对，准备发送奖励文案。", chat_id=chat_id, reward=state.reward)
        prize_text = f"+{state.reward}"
        try:
            await event.reply(prize_text)
        except Exception:
            if ctx.client:
                await ctx.client.send_message(chat_id, prize_text)

    async def _timeout_round(self, ctx: PluginContext, chat_id: int, timeout: int) -> None:
        try:
            await asyncio.sleep(timeout)
            async with self._locks[chat_id]:
                state = self._rounds.get(chat_id)
                if not state or state.answered:
                    return
                self._rounds.pop(chat_id, None)
            if ctx.client:
                await ctx.client.send_message(chat_id, f"本轮超时，答案是 {state.answer}。")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if ctx.log:
                await ctx.log("error", "猜数字超时任务异常，本轮已跳过。", chat_id=chat_id, error=type(exc).__name__)

    def _track_task(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _command(self, ctx: PluginContext) -> str:
        return str(ctx.config.get("command") or "guess")
```

**manifest.py：**

```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="guess_number",
    display_name="猜数字",
    version="0.1.0",
    author="example",
    description="一个可作为游戏模块模板的猜数字抢答模块。",
    permissions=["send_message", "edit_message", "read_chat"],
    config_schema={
        "type": "object",
        "x-ui-mode": "single",
        "properties": {
            "command": {
                "type": "string",
                "title": "触发指令名",
                "default": "guess",
                "minLength": 1,
                "maxLength": 32,
                "pattern": r"^\S+$",
            },
            "default_reward": {
                "type": "integer",
                "title": "默认奖励",
                "default": 0,
                "minimum": 0,
                "description": "仅作为指令未传奖励时的兜底值；本轮奖励建议通过指令参数传入。",
            },
            "timeout": {
                "type": "integer",
                "title": "答题限时（秒）",
                "default": 60,
                "minimum": 10,
                "maximum": 86400,
            },
        },
    },
)
```

**plugin.json（远程安装元数据）：**

```json
{
  "name": "guess_number",
  "display_name": "猜数字",
  "description": "一个可作为游戏模块模板的猜数字抢答模块。",
  "author": "example",
  "version": "0.1.0",
  "entry": "plugin.py",
  "permissions": ["send_message", "edit_message", "read_chat"]
}
```

**__init__.py：**

```python
from .manifest import MANIFEST
from .plugin import GuessNumberPlugin

PLUGIN_CLASS = GuessNumberPlugin
__all__ = ["PLUGIN_CLASS", "MANIFEST"]
```

---

## 15. 调试建议

### 快速自检

- [ ] `__init__.py` 是否导出 `PLUGIN_CLASS` 和 `MANIFEST`
- [ ] `MANIFEST.key` 是否和模块 class key 一致
- [ ] `permissions` 是否覆盖实际调用的方法
- [ ] `on_command` 签名是否是 5 参数
- [ ] 错误是否都被捕获并反馈给用户

### 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| 模块被跳过 | MANIFEST 类型不对或导出缺失 | 检查 `__init__.py` |
| 指令没反应 | feature 未启用或前缀不匹配 | 检查 rule 配置和前缀 |
| 热重载后旧 handler 还在触发 | generation guard 未生效 | 检查 loader.py 版本 |
| 远程模块安装失败 | plugin.json 缺必填字段或格式不合法 | 检查 name/description/version/entry |
| cleanup 后模块状态异常 | cleanup 未幂等 | 重复调用测试 |

---

## 16. 安全与合规

- 不要把明文 key 写入日志
- 不要把完整隐私消息持久化到外部系统
- 对外部请求做超时和异常处理
- 对高风险操作（删消息、批量发送）加显式开关

---

## 17. 完整示例

### 天气查询模块

```python
# manifest.py
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="weather",
    display_name="天气查询",
    version="1.0.0",
    author="community",
    description="查询天气信息，支持城市名",
    permissions=["send_message"],
    config_schema={
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "可选的 API Key"},
        },
    },
)
```

```python
# plugin.py
import httpx
from app.worker.plugins.base import Plugin, register

@register
class WeatherPlugin(Plugin):
    key = "weather"
    display_name = "天气查询"

    async def on_command(self, ctx, cmd, args, event) -> bool:
        if cmd not in ("weather", "w"):
            return False

        city = " ".join(args) if args else "Beijing"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                geo = await client.get(
                    f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
                )
                if not geo.json().get("results"):
                    await event.edit(f"未找到: {city}")
                    return True
                lat = geo.json()["results"][0]["latitude"]
                lon = geo.json()["results"][0]["longitude"]

                weather = await client.get(
                    f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
                )
                data = weather.json()["current_weather"]
                temp = data["temperature"]
                wmo = data["weathercode"]

                await event.edit(f"🌤 {city}: {temp}°C (代码: {wmo})")
        except Exception as e:
            await event.edit(f"天气查询失败: {e}")

        return True
```

```python
# __init__.py
from .manifest import MANIFEST
from .plugin import WeatherPlugin

PLUGIN_CLASS = WeatherPlugin

__all__ = ["PLUGIN_CLASS", "MANIFEST"]
```

---

## 版本与兼容

- `0.x`：开发阶段，允许快速迭代
- `1.x`：接口稳定后
- 不要依赖私有内部模块路径
- 尽量只依赖 `Plugin` / `Manifest` / `PluginContext` 公开契约
- 新增行为优先通过 `config` 可选项实现
