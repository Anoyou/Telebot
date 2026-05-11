# TeleBot 插件开发指南

> 本文档涵盖插件开发全流程：本地插件、远程插件、框架约束、调试建议。

---

## 目录

1. [快速开始](#1-快速开始)
2. [插件结构](#2-插件结构)
3. [Plugin 基类](#3-plugin-基类)
4. [PluginContext](#4-plugincontext)
5. [Manifest 元数据](#5-manifest-元数据)
6. [命令系统](#6-命令系统)
7. [消息监听](#7-消息监听)
8. [Conversation 工具](#8-conversation-工具)
9. [插件日志](#9-插件日志)
10. [远程插件](#10-远程插件)
11. [清理生命周期（cleanup）](#11-清理生命周期cleanup)
12. [安全边界](#12-安全边界)
13. [前端集成](#13-前端集成)
    - [模式概览](#模式概览)
    - [模式 A：规则驱动配置页](#模式-a规则驱动配置页forward--autoreply--autorepeat)
    - [模式 A 补充：后端 Dry-Run 适配](#模式-a-补充后端-dry-run-适配)
    - [模式 B：单配置对象页](#模式-b单配置对象页game24--codex-image)
    - [模式 C：Schema 驱动弹窗](#模式-cschema-驱动弹窗configdialog)
    - [基础能力：平台内置功能](#基础能力平台内置功能scheduler)
    - [适配自检清单](#适配自检清单)
14. [插件工程规范](#14-插件工程规范)
    - [消息发送能力边界](#消息发送能力边界)
    - [并发与抢答标准模板](#并发与抢答标准模板)
    - [统一配置项命名与校验](#统一配置项命名与校验)
    - [定时任务与后台任务生命周期](#定时任务与后台任务生命周期)
    - [奖惩系统接入约定](#奖惩系统接入约定)
    - [插件最小测试清单](#插件最小测试清单)
    - [可复制的游戏插件骨架](#可复制的游戏插件骨架)
15. [调试建议](#15-调试建议)
16. [安全与合规](#16-安全与合规)
17. [完整示例](#17-完整示例)

---

## 1. 快速开始

### 文件结构

```
plugins/installed/{插件名}/
├── __init__.py        # 导出 PLUGIN_CLASS 和 MANIFEST
├── manifest.py        # Manifest 元数据
├── plugin.py          # 插件主类
└── (其他模块)
```

### 最小可运行插件

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
    description="响应 ping 命令",
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

## 2. 插件结构

### 目录约定

```
backend/app/worker/plugins/
├── base.py              # Plugin 基类 + register 装饰器
├── manifest.py          # Manifest 数据类
├── loader.py            # 插件加载器 + 热重载 + generation guard
└── builtin/             # 内置插件
    ├── game24/
    └── forward/

plugins/installed/       # 远程/用户安装的插件
├── translate/
└── (更多插件...)
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
  → 旧插件: on_shutdown(ctx)
  → 重新 import + 实例化
  → 新插件: on_startup(ctx)

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
    key: str                          # 唯一标识
    display_name: str                 # 显示名

    # === 可选配置 ===
    message_channels: set[str]        # 监听方向: {"incoming"} / {"outgoing"} / 二者都监听
    owner_only: bool = True           # on_message 是否只允许账号本人/授权 sudo 触发
    commands: dict = {}               # TG 内命令: command_name -> 5 参数 handler
    command_config_keys: set[str] = set()  # 这些配置变化后需要重载并重新注册命令
    description: str = ""             # 描述（用于帮助系统）

    # === 生命周期钩子 ===
    async def on_startup(self, ctx: PluginContext) -> None:
        """插件激活时调用一次。"""

    async def on_shutdown(self, ctx: PluginContext) -> None:
        """插件关停前调用一次。必须幂等。"""

    # === 事件处理 ===
    async def on_message(self, ctx: PluginContext, event) -> None:
        """消息事件回调。"""

    async def on_command(self, ctx: PluginContext, cmd: str, args: list[str], event) -> bool:
        """命令派发回调。返回 True 表示已处理。"""
        return False
```

### 注册

```python
@register
class MyPlugin(Plugin):
    key = "my_plugin"
    ...
```

`@register` 装饰器把插件类注册到全局表，loader 通过 key 查找。

---

## 4. PluginContext

```python
@dataclass
class PluginContext:
    account_id: int
    feature_key: str
    config: dict           # 当前账号的插件配置
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

注意：内置插件会拿到完整运行时能力；远程/第三方插件的 `ctx.client` 会被 `SandboxClient` 包装，`ctx.engine` 和 `ctx.redis` 为 `None`，只能通过声明过的权限和 `ctx.scheduler` facade 使用有限能力。

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
| `config_schema` | dict | JSON Schema，有配置的插件必须写 |
| `requires_features` | list | 依赖的其他插件 key |
| `min_telebot_version` | str | 最低 TeleBot 版本要求，远程插件建议填写 |

### 完整示例

```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="my_plugin",
    display_name="我的插件",
    version="1.0.0",
    author="your_name",
    description="插件功能描述",
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

**优先级：** 账号级配置 > 插件全局配置 > config_schema 中的 default

**前端渲染：** `config_schema["x-ui-mode"]` 决定插件配置入口：
- `rules` / `single` / `platform` 且已注册专属页面 → 跳转到专属配置页
- `schema` 或没有专属页面 → 弹出 `ConfigDialog` 自动表单
- `level: global` 的字段 → 全局配置区（所有账号共享）
- `level: account` 的字段 → 账号配置区（按账号隔离）
- 无 level 的字段 → 默认按账号隔离

**必填字段验证清单（内置插件）：**

| 插件 | config_schema | UI 模式 | 状态 |
|------|--------------|---------|------|
| forward | ✅ target_chat_id, mode | `rules` | 已有 |
| auto_reply | 规则通过 Rules API 管理 | `rules` fallback | 已有 |
| autorepeat | ✅ trigger / repeat / chat 配置 | `rules` | 已有 |
| game24 | ✅ command, timeout | `single` | 已补 |
| codex_image | ✅ command, access_token, model, message_template, image_size/aspect_ratio/image_format, timeout/status/output/instructions | `single` | 已补 |
| scheduler | ✅ default_notify, max_tasks | `platform` | 已迁移为平台基础能力 |
| translate | ✅ default_lang, llm_provider | `schema` | 已补 |

### Manifest 验证

远程插件安装阶段验证的是 `plugin.json`，不会执行 Python：

```python
required = ["name 或 key", "version"]
name_pattern = r"^[A-Za-z0-9_][A-Za-z0-9_-]*$"
version_pattern = r"^\d+\.\d+\.\d+"
```

运行阶段 loader 会 import `__init__.py`，并检查：

- `PLUGIN_CLASS` 是 `Plugin` 子类
- `MANIFEST` 是 `Manifest` 实例
- `MANIFEST.key` 与插件 key / 目录名保持一致

---

## 6. 命令系统

### 命令派发流程

1. 消息到达 → 检查前缀匹配
2. 提取命令名和参数
3. 检查别名（贪心最长匹配）
4. 遍历已注册插件，调用 `on_command(ctx, cmd, args, event)`
5. 第一个返回 True 的插件接管，后续不再传递

### on_command 签名

```python
async def on_command(
    self,
    ctx: PluginContext,       # 上下文
    cmd: str,                 # 命令名（如 "weather"）
    args: list[str],          # 参数列表
    event: NewMessage.Event,  # 原始事件
) -> bool:
    """返回 True 表示已处理。"""
```

### 别名支持

命令别名支持多词贪心匹配和参数透传：

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

插件收到的对象通常是 `events.NewMessage.Event`，但在测试、热重载、Telethon 代理属性等场景里，也可能表现得更像裸 `Message`。因此建议用 `getattr` 做兼容，不要直接假设 `event.outgoing`、`event.message.id` 一定存在：

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

## 9. 插件日志

插件日志会进入后台的“日志中心 → 插件日志”分页，和“消息日志”“系统日志”分开显示。

### 如何写日志

插件运行时通过 `ctx.log(level, message, **detail)` 输出日志：

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

### loader 自动记录的插件异常

如果插件 `on_message` 抛异常，loader 会自动写一条插件日志，并附带：

- `plugin_key`
- `direction`
- `chat_id`
- `sender_id`
- `message_preview`
- `traceback`

这类异常不会让 worker 崩溃，当前消息会被跳过，其它插件继续运行。

---

## 10. 远程插件

### 安装方式

**通过 Web UI：**
1. 进入远程插件页面
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

### 远程插件规范

远程仓库必须包含 `plugin.json`，同时运行时必须提供 `manifest.py` / `plugin.py` / `__init__.py`：

```json
{
  "name": "weather",
  "display_name": "天气查询",
  "description": "查询天气信息",
  "author": "community",
  "version": "1.0.0",
  "entry": "plugin.py",
  "min_telebot_version": "0.9.0",
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
- 不再兼容旧的“只有 plugin.json + plugin.py”单文件远程插件；缺少 `manifest.py` 或 `__init__.py` 会在安装/更新阶段被拒绝。
- 相对安装目录会按项目根目录解析，不要依赖后端进程当前工作目录。

### 安装流程

```
1. git clone 到 plugins/installed/{name}/
2. 读取 plugin.json → Pydantic 校验（安装阶段不执行 Python）
3. 静态检查 `manifest.py` / `plugin.py` / `__init__.py` 是否齐全
4. 验证通过 → 注册到数据库
5. 广播 CMD_RELOAD_CONFIG，worker 重新扫描 installed 插件
6. 验证失败 → 删除目录，返回错误
```

### Registry 机制

支持从远程 registry 同步可用插件列表：

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
| `no-op` | 流程型插件，无长期资源 | 空方法 + 注释说明 |

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

### 命令前缀

- 所有命令必须有明确前缀（如 `,` 或自定义）
- 前缀由 `ctx.config` 中的 `prefix` 控制

### 权限声明

Manifest 中的 `permissions` 字段声明插件需要的能力：

| 权限 | 说明 |
|------|------|
| `send_message` | 发送消息 |
| `edit_message` | 编辑消息 |
| `read_chat` | 读取聊天历史 |

默认给三类常用能力，内置插件漏写时不会被沙箱拦截。

### 禁止行为

- 不允许 `os.system` / `subprocess` 执行系统命令（除非显式声明）
- 不允许把明文 key 写入日志
- 不允许持久化完整隐私消息到外部系统
- 对外部请求必须做超时和异常处理

---

## 13. 前端集成

插件前端配置分三种插件模式，另有一类平台内置基础能力。后续新增插件时，优先通过 `manifest.py` 的 `config_schema["x-ui-mode"]` 声明分类，前端会自动归类展示。

### 模式概览

| 分类 | 适用场景 | 大白话 | 典型功能 | 配置入口 |
|------|---------|--------|---------|---------|
| **A — 规则驱动** | 多条规则独立配置，需 CRUD + 试运行 | 像自动化流水线：先建规则，再按匹配条件触发动作 | forward、auto_reply、autorepeat | 专属配置页 |
| **B — 单配置对象** | 每个账号只保存一份插件配置 | 像一个工具面板：配置好触发指令和参数，直接运行 | game24、codex_image | 专属配置页 |
| **C — Schema 驱动** | 轻量插件，不需要定制页面 | 像通用表单：插件声明字段，前端自动渲染 | 简单远程插件 / 小工具插件 | ConfigDialog 弹窗 |
| **基础能力 — 平台内置** | 系统运行时常驻能力，不作为普通插件展示 | 像底座服务：给插件或平台调用，不强调启停 | scheduler | 平台功能页 |

**关键判断**：需要维护多条规则 → 模式 A；只有一份账号配置并需要更好的专属体验 → 模式 B；普通字段表单足够 → 模式 C；像调度器这种系统服务 → 基础能力。

#### 自动分类规则

新增插件应在 `config_schema` 顶层声明 `x-ui-mode`：

```python
config_schema={
    "type": "object",
    "x-ui-mode": "single",  # rules / single / schema / platform
    "properties": {
        "command": {"type": "string", "title": "触发指令名", "default": "demo"},
    },
}
```

| `x-ui-mode` | 展示位置 | 说明 |
|-------------|----------|------|
| `rules` | 模式 A | 规则驱动插件，通常有规则列表、创建/编辑、dry-run |
| `single` | 模式 B | 单配置对象插件，通常有专属配置页 |
| `schema` | 模式 C | 强制走 `ConfigDialog` 自动表单 |
| `platform` | 基础能力 | 平台内置能力，不混在普通插件列表里 |

前端统一从 `frontend/src/lib/plugin-modes.ts` 读取分类。旧内置插件仍保留 key fallback，但新插件不要依赖 fallback。

---

### 模式 A：规则驱动配置页（Forward / AutoReply / Autorepeat）

规则驱动插件每条 rule 存储独立的 `config` JSON，通过 CRUD API 管理。前端专属页面提供：规则列表 + 创建/编辑对话框 + 试运行（dry-run）。

#### 适配清单（6 处必改）

| # | 文件 | 修改内容 |
|---|------|---------|
| 1 | `frontend/src/api/types.ts` | 添加 `XxxRuleConfig` 接口（描述单条规则的 config 字段） |
| 2 | `frontend/src/pages/Features/XxxConfig.tsx` | **新建**：规则列表页（参考 `AutoReply.tsx` 或 `Forward.tsx`） |
| 3 | `backend/app/worker/plugins/builtin/xxx/manifest.py` | `config_schema["x-ui-mode"] = "rules"` |
| 4 | `frontend/src/App.tsx` | ① import 新页面组件 ② 添加路由 `:aid/features/xxx` ③ 在 `FEATURE_CONFIG_PAGES` 中添加 key |
| 5 | `frontend/src/pages/Accounts/Detail.tsx` / `frontend/src/pages/Extensions.tsx` | 在 `FEATURE_CONFIG_PAGE_KEYS` Set 中添加 key |
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

创建 `frontend/src/pages/Features/XxxConfig.tsx`，核心结构：

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
- 顶部：返回按钮 + 标题 + 新建按钮
- 主体：规则表格（序号 / 关键字段 / 启用状态 / 操作按钮）
- 对话框：创建/编辑表单，字段来自 RuleConfig
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
import { XxxConfig } from "@/pages/Features/XxxConfig";

// ② 路由
<Route path=":aid/features/xxx" element={<XxxConfig />} />

// ③ FEATURE_CONFIG_PAGES 注册
const FEATURE_CONFIG_PAGES: Record<string, { title: string; description: string }> = {
  auto_reply: { title: "自动回复", description: "..." },
  xxx:        { title: "插件显示名", description: "..." },
  // ...
};
```

路由路径格式固定为 `:aid/features/{plugin_key}`，`plugin_key` 必须与 `MANIFEST.key` 一致。

#### 5. FEATURE_CONFIG_PAGE_KEYS — 两个入口点

两个文件中的 `FEATURE_CONFIG_PAGE_KEYS` 必须同步添加：

```tsx
// Detail.tsx（账号详情页 → 插件列表"配置"按钮）
const FEATURE_CONFIG_PAGE_KEYS = new Set([
  "auto_reply", "autorepeat", "forward", "game24", "codex_image",
  "xxx",  // ← 新增
]);

// Extensions.tsx（插件中心 → 账号插件"配置"按钮）
const FEATURE_CONFIG_PAGE_KEYS = new Set([
  "auto_reply", "autorepeat", "forward", "game24", "codex_image",
  "xxx",  // ← 新增
]);
```

**作用**：Set 中的 key 会让"配置"按钮跳转到专属页面路由 `/:aid/features/xxx`；
不在 Set 中的 key 会弹 `ConfigDialog`（模式 C）。

#### 6. feature.py — 后端常量

```python
# backend/app/db/models/feature.py
FEATURE_XXX = "xxx"
```

此常量供 `rules.py` dry-run 分支和其它模块引用。

---

### 模式 A 补充：后端 Dry-Run 适配

规则驱动页面通常需要试运行功能，后端需同步适配 `rules.py`：

#### 插件侧导出 _dry_run_match

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

### 模式 B：单配置对象页（Game24 / Codex Image）

只有一份配置、无规则列表的插件，使用专属页面但不需要 CRUD 和 dry-run：

- 创建 `frontend/src/pages/Features/XxxConfig.tsx`，直接展示/编辑单个 config 对象
- `manifest.py` 中声明 `config_schema["x-ui-mode"] = "single"`
- 其余适配步骤与模式 A 相同（App.tsx 路由 + FEATURE_CONFIG_PAGES + 两个 PAGE_KEYS）
- 后端不需要 dry-run 分支

#### 页面布局约定

单配置对象页参考 `Game24Config.tsx` 与 `CodexImageConfig.tsx`。页面从上到下固定为：

1. 返回按钮 + 插件标题
2. 当前状态（是否启用、当前命令、关键运行状态）
3. 使用说明（真实触发命令示例、参数示例、注意事项）
4. 配置表单（账号级配置为主，必要时展示全局配置）
5. 保存 / 还原操作

当前状态和使用说明要放在顶部，因为这类插件通常靠命令触发，用户最关心的是“现在能不能用”和“怎么叫它”。

#### 命令型插件配置

如果插件支持自定义触发指令，应同时做三件事：

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
            "description": "跟在系统命令前缀后使用，支持中文；不要包含空格。",
        },
    },
}
```

- `command_config_keys` 用于告诉 loader：命令字段变化后要重启该插件并重新注册命令。
- 命令名支持中文，例如 `,画图 一只猫`；但不能包含空格，因为命令解析以第一个空白分隔命令和参数。
- 说明文案必须用当前配置中的命令动态生成，不要把 `,cximg`、`,24d` 写死。

#### 已有单配置插件字段参考

| 插件 | 推荐字段 | 说明 |
|------|---------|------|
| `game24` | `command`, `timeout` | 触发指令名、答题限时 |
| `codex_image` | `command`, `access_token`, `model`, `message_template`, `image_size`, `aspect_ratio`, `image_format`, `max_wait_seconds`, `status_interval_seconds`, `delete_command_message`, `show_revised_prompt`, `reasoning_effort`, `custom_instructions` | 触发指令、鉴权、模型、消息模板、图片尺寸/比例/格式、等待与状态提示、输出行为、自定义生成指令 |

专属页面字段应与运行时实际读取的配置保持一致；`manifest.config_schema` 也要同步，避免 ConfigDialog、接口校验和文档出现三套口径。

---

### 模式 C：Schema 驱动弹窗（ConfigDialog）

无专属页面的插件，在"配置"按钮点击后弹出 `ConfigDialog`，自动根据 `manifest.py` 中的 `config_schema` 渲染表单：

- `level: "global"` 的字段 → 全局配置区
- `level: "account"` 或无 level → 账号配置区
- **不需要**添加到 `FEATURE_CONFIG_PAGE_KEYS`，不需要创建页面文件
- `config_schema["x-ui-mode"]` 可写 `schema`，ConfigDialog 自动渲染

```python
# config_schema 示例（适用于 ConfigDialog 自动渲染）
config_schema={
    "type": "object",
    "x-ui-mode": "schema",
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

基础能力不是普通插件卡片，而是系统运行时一起初始化的服务。比如 `scheduler` 现在属于平台内置调度能力：页面仍可配置定时任务，但不再强调“作为插件启停”。

适配规则：

- `manifest.py` 声明 `config_schema["x-ui-mode"] = "platform"`
- 前端会在账号详情和插件中心里放到“基础能力 / 平台内置”分组
- 如果有专属页面，仍需 `App.tsx` 路由和 `FEATURE_CONFIG_PAGE_KEYS`
- 后端运行时由 `PlatformScheduler` 常驻初始化；调度算法与 action 执行在平台层，`scheduler` 插件壳只保留兼容入口或配置入口
- 普通插件需要定时执行时，不要自己 `create_task` 写永久循环，优先使用 `ctx.scheduler`

#### 插件调用平台调度器

`ctx.scheduler` 是绑定到当前插件的最小 capability facade。插件只能注册 / 注销自己名下的任务，热重载、禁用、worker 退出时 loader 会统一清理，避免旧 callback 继续触发。

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
        # callback 可闭包引用插件自己的状态，也可以在 config 中保存轻量参数
        ...
```

支持的 `schedule` 字段与定时任务页面一致：

| 类型 | 示例 | 说明 |
|------|------|------|
| `cron` | `{"kind": "cron", "cron": "*/10 * * * *"}` | 按系统时区解析 cron |
| `interval` | `{"kind": "interval", "interval_sec": 300}` | 首次 tick 会立即执行一次，之后按间隔推进 |
| `once` | `{"kind": "once", "fire_at": "2026-05-11T10:00:00+00:00"}` | 执行后自动置为 disabled |

注意：

- callback 异常会写入插件日志，并保留任务等待下次 tick；不要把异常吞掉后静默失败
- `ctx.scheduler` 注册的是运行期任务；worker 重启后会由插件 `on_startup` 重新注册，若需要精确保存 `last_fire` / `next_fire`，插件应把状态写回自己的配置或规则表
- 如果任务依赖插件配置，配置变更后建议触发插件热重载，或在 callback 中读取最新 `ctx.config`
- 第三方插件拿到的是 scheduler facade，不会直接获得 Redis / DB / Telethon session
- GUI 定时任务页仍走 `Rule(feature_key="scheduler")`，由同一个 `PlatformScheduler` 调度；后续新增插件不要依赖 `SchedulerPlugin`，只依赖 `ctx.scheduler`

---

### 风格要求

- 与 TeleBot 现有页面风格一致
- React + TypeScript + TailwindCSS
- 新页面参考 `AutoReply.tsx`（规则驱动）或 `Game24Config.tsx`（单配置）的代码结构
- 表格列宽要稳定，账号详情页和插件中心的同类列表要纵向对齐
- 配置按钮不依赖启用状态；即使插件当前关闭，也应允许先配置

### 适配自检清单

新增插件前端配置页后，逐项检查：

- [ ] `manifest.py` 中 `config_schema["x-ui-mode"]` 已声明：`rules` / `single` / `schema` / `platform`
- [ ] `types.ts` 中 `XxxRuleConfig` 接口与 `manifest.py` config_schema 字段一致
- [ ] 如果有专属页面：`App.tsx` 中路由路径 `:aid/features/{key}` 与插件 key 一致
- [ ] 如果有专属页面：`App.tsx` 中 `FEATURE_CONFIG_PAGES` 包含该 key
- [ ] 如果有专属页面：`Detail.tsx` 和 `Extensions.tsx` 的 `FEATURE_CONFIG_PAGE_KEYS` 包含该 key
- [ ] 如果是命令型插件：`command` 字段可配置，`Plugin.command_config_keys = {"command"}`，说明文案动态读取当前命令
- [ ] 如果是模式 B：当前状态和使用说明位于配置表单之前
- [ ] 如需 dry-run：`plugin.py` 导出 `_dry_run_match`，`__init__.py` re-export，`rules.py` 在 fallback 之前添加分支
- [ ] 如需 dry-run：`feature.py` 中有 `FEATURE_XXX` 常量
- [ ] 前端 `pnpm -C frontend exec tsc -b --noEmit` 和 `pnpm -C frontend build` 通过

---

## 14. 插件工程规范

这一章是给插件作者看的“不要踩坑”规范。只要插件涉及发消息、抢答、后台任务、奖励或远程发布，都建议先按这里的模板走。

### 消息发送能力边界

不同回调能拿到的对象不同，不要在没有 `event` 的地方调用 `event.reply`，也不要尝试编辑别人的 incoming 消息。推荐按下表选择发送方式：

| 场景 | 有 `event` | 推荐方式 | 适用说明 | 远程插件权限 |
|------|------------|----------|----------|--------------|
| `on_command` 命令回调 | 有 | `event.edit(...)` | 把用户发出的命令改成状态/结果，适合 UserBot 自己发出的命令 | `edit_message` |
| `on_command` 需要另发一条 | 有 | `event.respond(...)` 或 `ctx.client.send_message(event.chat_id, ...)` | 不想覆盖原命令，或命令消息可能已删除 | `send_message` |
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

- `event.edit(...)` 只适合编辑当前账号自己发出的命令/状态消息；不要用它编辑别人发来的 incoming 消息。
- 远程插件安装阶段只读 `plugin.json`，但运行时仍会受 `manifest.py` 的 `permissions` 沙箱限制。
- 第三方插件不要把 `event.reply/respond/edit` 当作绕过权限的路径；凡是会发送、编辑、删除、读取消息的行为，都必须在 `permissions` 中声明对应能力。
- 需要发送图片、文件时，为 `BytesIO` 设置 `name`，例如 `image_file.name = "result.png"`，否则 Telegram 客户端可能显示无后缀文件。
- 长消息要按 Telegram 4096 字符限制分段；HTML 模式下切分前要保证标签闭合，失败时应降级为纯文本。

### 并发与抢答标准模板

抢答类、竞猜类、抽奖类插件都要处理并发：多个人几乎同时答对时，只能有一个人获胜。推荐使用 `chat_id -> asyncio.Lock`，并在加锁后再次检查状态。

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
- 热重载没有清理状态：旧任务继续执行，和新插件实例抢状态。

### 统一配置项命名与校验

新增插件尽量复用以下字段名，减少前端、文档、Bot 命令和用户认知的分裂。

| 字段 | 类型 | 推荐默认值 | 推荐范围/校验 | 说明 |
|------|------|------------|---------------|------|
| `command` | string | 插件短名 | 1-32 字符，不含空白，支持中文 | 触发指令名，配合 `command_config_keys = {"command"}` |
| `reward` | integer | `0` | `0` 到业务允许上限 | 奖励值；无积分系统时仅作为文案奖励 |
| `timeout` | integer | `60` | 10-86400 秒 | 用户可理解的超时秒数；已有插件沿用该字段 |
| `auto_next` | boolean | `false` | 布尔 | 游戏/任务结束后是否自动开下一轮 |
| `message_template` | string | 内置模板 | 建议限制最大长度 | 用户可编辑输出消息模板 |
| `status_interval_seconds` | integer | `30` | 10-300 秒 | 状态编辑频率，避免频繁编辑触发风控 |
| `allowed_chat_ids` | array[int] | `[]` | 留空表示不限制 | 限制插件只在指定聊天生效 |
| `delete_command_message` | boolean | `false` | 布尔 | 命令完成后是否删除原命令 |

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
            "description": "跟在系统命令前缀后使用，支持中文；不要包含空格。",
        },
        "reward": {
            "type": "integer",
            "title": "默认奖励",
            "default": 0,
            "minimum": 0,
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

### 定时任务与后台任务生命周期

优先使用平台调度器：

- 重复执行、cron、一次性延迟任务：用 `ctx.scheduler.register(...)`。
- 插件禁用、热重载、worker 退出时，loader 会清理该插件名下的调度任务。
- 调度回调没有 `event`，必须从 `job.config` 或插件状态里拿 `chat_id`、模板、目标参数。

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

当前 TeleBot 没有统一积分服务时，插件奖励分三类：

| 模式 | 适用场景 | 推荐做法 |
|------|----------|----------|
| 文案奖励 | 游戏娱乐、无真实积分 | 只发送“奖励 X 分/金币”的消息，并写插件日志 |
| 插件内记分 | 插件自己维护排行榜 | 仅写自己插件的配置/状态文件或专属表，不直接改其它插件数据 |
| 统一积分接口 | 后续平台提供积分服务后 | 通过平台 service/event 接口发放，插件不直接操作 DB |

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
- 奖励发送失败要写 `warn/error` 日志，并说明是否已经兜底发送普通消息。

### 插件最小测试清单

发布前至少覆盖这些路径：

- [ ] 命令能触发，命令名改配置后能热重载生效。
- [ ] 命令冲突时只由一个插件处理，`on_command` 正确返回 `True/False`。
- [ ] 群聊、私聊、频道/匿名频道场景下不崩溃。
- [ ] `event` 兼容裸 `Message`：不直接假设 `event.outgoing`、`event.message.id` 存在。
- [ ] 重复开局/重复创建规则会给出明确提示。
- [ ] 抢答并发：两个答对消息同时到达只奖励一次。
- [ ] 超时任务和答题消息同时发生时，只结束一次。
- [ ] 插件禁用、热重载、worker shutdown 后没有幽灵 task。
- [ ] 远程插件 `plugin.json`、`manifest.py`、`__init__.py`、`plugin.py` 均可被加载。
- [ ] 远程插件不是单文件旧结构；缺少 `manifest.py` 或 `__init__.py` 会被安装阶段拒绝。
- [ ] 缺权限时远程插件会收到可理解的 `PermissionError`，不会访问 session/Redis/engine。
- [ ] 所有外部 HTTP 请求有 timeout，错误提示不泄露 token、路径、session。
- [ ] 插件日志能说明“收到什么、判断了什么、为什么跳过/为什么执行、失败在哪一步”。

### 可复制的游戏插件骨架

下面是一个最小抢答游戏骨架，包含命令注册、状态管理、并发锁、超时、日志和清理。真实插件可以从这里删改。

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
            await ctx.log("info", "猜数字插件已启动。", command=self._command(ctx))

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
        reward = int(ctx.config.get("reward", 0) or 0)
        timeout = int(ctx.config.get("timeout", 60) or 60)

        async with self._locks[chat_id]:
            if chat_id in self._rounds:
                await event.edit("当前聊天已有进行中的游戏。")
                return
            answer = str(random.randint(1, 9))
            self._rounds[chat_id] = RoundState(chat_id, answer, reward, timeout)

        task = asyncio.create_task(self._timeout_round(ctx, chat_id, timeout))
        self._track_task(task)
        await event.edit(f"猜一个 1-9 的数字，限时 {timeout} 秒。")

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
        prize_text = f"答对了！奖励 {state.reward}。"
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
    description="一个可作为游戏插件模板的猜数字抢答插件。",
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
            "reward": {
                "type": "integer",
                "title": "奖励文案数值",
                "default": 0,
                "minimum": 0,
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
  "description": "一个可作为游戏插件模板的猜数字抢答插件。",
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
- [ ] `MANIFEST.key` 是否和插件 class key 一致
- [ ] `permissions` 是否覆盖实际调用的方法
- [ ] `on_command` 签名是否是 5 参数
- [ ] 错误是否都被捕获并反馈给用户

### 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| 插件被跳过 | MANIFEST 类型不对或导出缺失 | 检查 `__init__.py` |
| 命令没反应 | feature 未启用或前缀不匹配 | 检查 rule 配置和前缀 |
| 热重载后旧 handler 还在触发 | generation guard 未生效 | 检查 loader.py 版本 |
| 远程插件安装失败 | plugin.json 缺必填字段或格式不合法 | 检查 name/description/version/entry |
| cleanup 后插件状态异常 | cleanup 未幂等 | 重复调用测试 |

---

## 16. 安全与合规

- 不要把明文 key 写入日志
- 不要把完整隐私消息持久化到外部系统
- 对外部请求做超时和异常处理
- 对高风险操作（删消息、批量发送）加显式开关

---

## 17. 完整示例

### 天气查询插件

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
