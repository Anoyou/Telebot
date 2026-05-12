# TeleBot 远程插件开发与安装指南

> 远程插件是从 Git 仓库安装到 `plugins/installed/{name}/` 的第三方插件。安装阶段只解析静态 `plugin.json`，不会执行 Python；运行阶段再由 worker loader 加载 `manifest.py` / `plugin.py`。

> **重要约束**：本文只说明“远程安装、更新、沙箱、账号启用”的额外规则；插件的运行时写法、配置命名、消息发送边界、并发模板、日志与测试清单必须同时遵守 [插件开发指南](./PLUGIN-DEV-GUIDE.md)。从 `0.10.2` 起，旧版“只有 `plugin.json` + `plugin.py`”的单文件远程插件不再兼容，安装时会直接提示按本文档补齐标准插件包结构。

---

## 1. 总览

远程插件适合这些场景：

- 希望把插件独立成仓库，由 Web UI 一键安装、更新、卸载。
- 插件需要给多个 TeleBot 部署复用。
- 插件作者不想改 TeleBot 主仓库，但愿意遵循统一的 Manifest、权限和配置规范。

当前实现采用：

- Git clone / pull 安装更新。
- `plugin.json` 作为安装阶段元数据。
- `manifest.py` 作为运行阶段 Manifest。
- `Plugin` / `PluginContext` 作为运行时 API。
- 第三方插件使用 `SandboxClient`，按 `permissions` 最小授权。

---

## 2. 文件结构

一个可安装的远程插件仓库至少包含：

```text
guess_number/
├── plugin.json      # 安装阶段静态元数据，不能执行 Python
├── manifest.py      # 运行阶段 Manifest
├── plugin.py        # 插件主类
└── __init__.py      # 导出 PLUGIN_CLASS 和 MANIFEST
```

### plugin.json

`plugin.json` 用于安装、列表展示和安全校验。安装阶段只读取这个文件，但会同时检查运行期必须存在 `manifest.py`、`plugin.py`、`__init__.py`。缺文件会拒绝安装，前端会提示按本文档更新插件结构。

```json
{
  "name": "guess_number",
  "display_name": "猜数字",
  "description": "一个抢答小游戏插件",
  "author": "example",
  "version": "0.1.0",
  "entry": "plugin.py",
  "min_telebot_version": "0.9.8",
  "commands": ["guess"],
  "cleanup_mode": "resource",
  "tags": ["game", "quiz"],
  "permissions": ["send_message", "edit_message", "read_chat"],
  "config_schema": {
    "type": "object",
    "x-ui-mode": "single"
  }
}
```

字段说明：

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `name` / `key` | 二选一 | string | 插件唯一标识，优先使用 `name` |
| `display_name` | 推荐 | string | UI 显示名 |
| `description` | 推荐 | string | 插件用途说明 |
| `author` | 推荐 | string | 作者 |
| `version` | 是 | string | 语义化版本，如 `0.1.0` |
| `entry` | 否 | string | 入口文件，默认 `plugin.py`；当前仍要求同时提供标准 `manifest.py` / `__init__.py` |
| `min_telebot_version` | 推荐 | string | 最低 TeleBot 版本 |
| `commands` | 否 | array | 插件声明的命令名，用于帮助文档 |
| `cleanup_mode` | 否 | string | `resource` / `reset` / `no-op` |
| `tags` | 否 | array | 分类搜索标签 |
| `permissions` | 推荐 | array | 运行时沙箱权限声明 |
| `config_schema` | 推荐 | object | 配置表单和 API 校验依据 |

校验规则：

- `name` / `key` 只能包含字母、数字、`_`、`-`，不能包含 `.`、`/`、`\`。
- `version` 必须类似 `x.y.z`。
- `plugin.json.version` 必须与 `manifest.py` 中的 `MANIFEST.version` 保持一致；如果插件进入 Registry，Registry 里的 `version` 也要同步。
- `plugin.json` 必须是合法 JSON。
- 安装阶段不会 import `manifest.py`，所以不能依赖 Python 代码生成元数据。
- 不兼容旧的“只有 plugin.json + plugin.py”单文件插件。请补齐 `manifest.py` 和 `__init__.py` 后再安装。

### manifest.py

`manifest.py` 是运行阶段的 Manifest，必须导出 `MANIFEST`。

```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="guess_number",
    display_name="猜数字",
    version="0.1.0",
    author="example",
    description="一个抢答小游戏插件",
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
            "timeout": {
                "type": "integer",
                "title": "超时时间（秒）",
                "default": 60,
                "minimum": 10,
                "maximum": 86400,
            },
        },
    },
)
```

### plugin.py

远程插件仍然继承 `Plugin`，不要使用旧的 `group/private` 频道写法。当前只支持 `incoming` / `outgoing` 两类消息方向。

```python
from app.worker.plugins.base import Plugin, PluginContext, register


@register
class GuessNumberPlugin(Plugin):
    key = "guess_number"
    display_name = "猜数字"
    message_channels = {"incoming"}
    owner_only = False
    command_config_keys = {"command"}

    async def on_startup(self, ctx: PluginContext) -> None:
        self.commands = {str(ctx.config.get("command") or "guess"): self._cmd_start}

    async def on_shutdown(self, ctx: PluginContext) -> None:
        # 必须幂等；重复调用不报错
        return None

    async def on_message(self, ctx: PluginContext, event) -> None:
        # 监听群里用户回答
        return None

    async def _cmd_start(self, client, event, args, account_id, ctx: PluginContext) -> None:
        await event.edit("已开局")
```

### __init__.py

```python
from .manifest import MANIFEST
from .plugin import GuessNumberPlugin

PLUGIN_CLASS = GuessNumberPlugin
__all__ = ["PLUGIN_CLASS", "MANIFEST"]
```

---

## 3. 安装与启用流程

Web UI 或 API 安装远程插件时，后端会执行：

```text
1. 校验 source_url，只允许 https://、git+ssh:// 或 git@host:org/repo.git
2. git clone --depth 1 到 plugins/installed/{name}/，带超时
3. 读取 plugin.json，做 Pydantic 校验
4. 静态检查运行期文件：manifest.py / plugin.py / __init__.py
5. 写入 remote_plugin 表
6. 注册 Feature(is_builtin=False)，让插件出现在功能矩阵里
7. 可选：default_enabled=true 时为已有账号写 AccountFeature
8. 广播 CMD_RELOAD_CONFIG，让 worker 重新扫描插件
```

启用远程插件有两层开关：

| 开关 | 含义 |
|------|------|
| `RemotePlugin.enabled` | 全局开关，关闭后所有账号都不加载 |
| `AccountFeature.enabled` | 账号级开关，控制某个账号是否加载 |

只有两个开关都为 `true` 时，worker 才会实例化插件。

经验提示：

- “插件管理”里的启用/禁用是远程插件全局开关。
- “账号插件管理”里的启用/禁用是账号级开关。
- 新安装插件首次在“插件管理”点“启用”时，如果还没有任何账号级配置，后端会为现有账号创建启用行，方便立即试用。
- 一旦插件已有账号级配置，后续全局启用不会覆盖用户的账号选择；要调整范围，请到“账号插件管理”或使用按账号启用 API。
- 如果插件缺少运行期文件，安装/更新阶段会失败，而不是等到 worker 运行时才报“未找到插件实现”。
- 热更新后建议看插件日志中的启动版本，确认 worker 已经切换到新代码。

---

## 4. API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/remote-plugins` | 列出已安装远程插件 |
| `POST` | `/api/remote-plugins/install` | 从 Git URL 安装 |
| `POST` | `/api/remote-plugins/{name}/enable` | 打开全局开关 |
| `POST` | `/api/remote-plugins/{name}/disable` | 关闭全局开关 |
| `POST` | `/api/remote-plugins/{name}/enable-accounts` | 按账号启用 |
| `POST` | `/api/remote-plugins/{name}/disable-accounts` | 按账号禁用 |
| `POST` | `/api/remote-plugins/{name}/update` | `git pull` 并重读 `plugin.json` |
| `DELETE` | `/api/remote-plugins/{name}` | 卸载并清理 DB/文件 |

安装请求示例：

```json
{
  "source_url": "https://github.com/example/telebot-plugin-guess-number.git",
  "default_enabled": false
}
```

---

## 5. 沙箱与权限

第三方插件运行时拿到的 `ctx.client` 是 `SandboxClient`，只允许调用 `manifest.py` 中声明的权限。

| 权限 | 允许方法 | 说明 |
|------|----------|------|
| `send_message` | `send_message` / `respond` / `reply` | 发送文本消息 |
| `edit_message` | `edit` / `edit_message` | 编辑消息 |
| `read_chat` | `get_messages` / `get_chat` / `iter_messages` | 读取聊天 |
| `send_file` | `send_file` | 发送图片、文件 |
| `join_chat` | `join_chat` | 加入聊天 |
| `delete_message` | `delete_messages` | 删除消息 |

第三方插件不会拿到：

- 真实 `client.session`
- Redis 客户端
- DB engine/session
- worker 内部 `engine`
- raw MTProto `client(functions.xxx(...))`

注意：

- 缺权限时会抛 `PermissionError`，请在日志里提示“缺少哪个权限”，不要吞掉异常。
- `event.reply/respond/edit` 也必须按同等能力声明权限，不要把 event helper 当成越权发送路径。
- 不要把 API Key、session、Bot Token、完整本地路径写进日志。
- 外部 HTTP 请求必须设置 timeout。
- 需要长期后台任务时，优先使用 `ctx.scheduler`，不要自己写永久循环。

---

## 6. 消息发送能力边界

远程插件最容易踩坑的是不知道该用 `event.reply` 还是 `ctx.client.send_message`。简版规则：

| 场景 | 推荐 |
|------|------|
| 命令回调中更新状态 | `event.edit(...)` |
| 命令回调中另发消息 | `event.respond(...)` 或 `ctx.client.send_message(event.chat_id, ...)` |
| 回复别人消息 | `event.reply(...)` |
| 跨群发送/通知 | `ctx.client.send_message(target_chat_id, ...)` |
| 发送图片/文件 | `ctx.client.send_file(...)`，并给 `BytesIO.name` 设置后缀 |
| 调度器回调 | 没有 `event`，只能使用 `ctx.client.send_message(...)` |

完整矩阵、兜底发送模板、长消息/HTML 注意事项见 [PLUGIN-DEV-GUIDE.md](./PLUGIN-DEV-GUIDE.md#消息发送能力边界)。

---

## 7. 并发、超时与生命周期

抢答类插件必须保证“首个答对”原子判定：

- 使用 `chat_id -> asyncio.Lock`。
- 加锁后再次检查当前局是否仍存在。
- 在锁内完成 `answered=True` 和状态删除。
- 发消息、HTTP 请求等慢操作尽量放在锁外。
- 超时任务也必须拿同一把锁，再二次检查状态。

后台任务规则：

- cron / interval / once 使用 `ctx.scheduler.register(...)`。
- 临时超时任务可以 `asyncio.create_task`，但必须保存引用。
- `on_shutdown` 里要 `cancel()` 并 `await asyncio.gather(..., return_exceptions=True)`。
- `on_shutdown` 必须幂等，重复调用不能报错。

完整模板见 [PLUGIN-DEV-GUIDE.md](./PLUGIN-DEV-GUIDE.md#并发与抢答标准模板) 和 [定时任务与后台任务生命周期](./PLUGIN-DEV-GUIDE.md#定时任务与后台任务生命周期)。

---

## 8. 配置规范

远程插件建议统一使用这些字段名：

| 字段 | 类型 | 说明 |
|------|------|------|
| `command` | string | 触发指令名，支持中文，不含空格 |
| `timeout` | integer | 超时时间（秒） |
| `auto_next` | boolean | 是否自动下一轮 |
| `message_template` | string | 输出消息模板 |
| `cooldown_seconds` | integer | 聊天级或用户级冷却时间 |
| `cleanup_delay_seconds` | integer | 流程结束后延迟清理临时消息 |
| `end_commands` | array[string] | 取消/强制结束命令别名 |
| `status_interval_seconds` | integer | 状态编辑间隔，建议 10-300 秒 |
| `allowed_chat_ids` | array[int] | 限制生效聊天 |

抢答/答题类插件不要把“本轮奖励金额、下注金额、题目范围”这类单局动态参数优先做成固定 `config_schema` 字段。更推荐让用户在触发命令里带上，例如：

```text
,game 100
```

配置页只放稳定配置项：`command`、`timeout`、`auto_next`、`message_template`、`allowed_chat_ids`。如果确实需要默认奖励，可以命名为 `default_reward`，只作为命令未传奖励时的兜底值。

命令型插件必须设置：

```python
command_config_keys = {"command"}
```

这样用户在 GUI 修改命令名后，loader 才能重新注册命令。

所有会发送、编辑或回复给 Telegram 用户看的文案都必须模板化，不要把用户可见句子硬编码在 `plugin.py` 里。开局、进行中、答对、超时、取消、错误提示、媒体 caption、重复触发提示等，都应使用 `*_message_template` 或通用 `message_template` 配置；代码里只保留模板默认值、不可恢复兜底错误、内部日志和开发者调试信息。

模板类字段应提供占位符说明和示例值。比如答题插件的开局模板可说明：

```text
可用占位符：{round}=轮次，例如 1；{reward}=奖励，例如 100；{timeout}=限时秒数，例如 60；{command}=当前命令，例如 game。
```

如果插件有专属配置页，建议提供只读预览字段，用示例上下文渲染最终文案。预览应参考“通用模板 → 自定义命令模板”的输出模板预览：展示占位符替换后的最终 Telegram 消息效果，而不是简单展示默认值或说明文字。没有专属页面时，至少在 `description` 中给出完整示例。

远程插件默认使用 `ConfigDialog` 小窗口配置页。这个弹窗的基础表单风格应和 TeleBot 的自定义命令 / LLM 配置页保持同一套体验：

- 宽度、滚动高度、字段间距和表单控件风格统一，避免做成更窄、更拥挤的临时表单。
- 普通字符串用单行输入；消息模板、提示词、正文、长文本说明等字段用多行输入。
- 模板字段必须在 `description` 中列出可用占位符、含义和示例值。
- 凡是会出现在 Telegram 消息里的文案，都必须有模板配置入口；不要只在源码里改字符串。
- 如果插件提供专属页面，模板输入、占位符说明/按钮、只读预览应放在同一个配置上下文里，预览只用模拟数据渲染，不读取真实消息，也不实际发送。
- 消息模板预览交互应对齐“自定义命令模板”，不是 LLM Provider 配置页；LLM 配置页只作为基础表单宽度、间距和控件风格的参考。
- 自动弹窗会按字段名兼容已有 schema：`message_template` / `*_template` 渲染为可编辑多行输入，`template_placeholders` 渲染为只读占位符说明，`template_preview` / `*_preview` 渲染为 `TelegramHtmlPreview` 预览卡片。
- `readOnly: true` 字段统一只读展示，不会渲染为可编辑控件，也不会在保存时写回配置。
- 没有专属页面时，至少通过字段标题、默认值和 `description` 让自动弹窗能呈现出清晰的模板说明和示例效果。

### 配置弹窗的数据来源

远程插件配置页有一个很容易误判的点：前端点击“配置”时，不会直接读取磁盘上的 `plugin.json` 或 `manifest.py`，而是读取：

```text
GET /api/accounts/{aid}/features
```

也就是接口返回的 `features[].config_schema`。这个字段来自数据库里的 `Feature.manifest.config_schema`。

因此，远程插件的配置链路是：

```text
plugin.json.config_schema
  -> 安装/更新服务解析 plugin.json
  -> 后端写入 Feature.manifest.config_schema
  -> /api/accounts/{aid}/features 返回 config_schema
  -> 前端 ConfigDialog 自动渲染配置表单
```

开发与排查时请记住：

- `plugin.json.config_schema` 是远程插件安装阶段的静态来源。
- 安装/更新后，后端必须把它持久化到 `Feature.manifest.config_schema`。
- 前端配置弹窗实际读取 `/api/accounts/{aid}/features` 返回的 `config_schema`。
- 如果 UI 显示“该插件没有可配置的选项”，先检查该 API 返回里的 `config_schema` 是否为空。
- 修改远程插件的 `config_schema` 后，需要在插件中心执行“更新插件”，并确认后端完成元数据回写。

### 答题/抢答奖励交互规范

远程答题、抢答、下注类插件建议统一使用下面的交互方式：

1. 奖励金额优先从触发命令参数读取，例如 `,game 100`。
2. 开局时立即把本轮奖励金额写入局状态，例如 `RoundState.reward = reward`；后续答题、超时、结算都读取局状态，不再读取运行时可变配置，避免一局进行中配置被修改后奖励不一致。
3. 答对后使用两步反馈：
   - 回复答对者消息发送纯文本奖励，例如 `+100`；
   - 编辑原题目消息，追加答对者、正确答案、奖励金额、耗时等结算信息。
4. 如果插件需要发送题面图片，必须同时在 `plugin.json.permissions` 和 `manifest.py` 的 `permissions` 里声明 `send_file`。
5. 图片题面插件应避免隐式依赖未声明系统库；如果不引入 Pillow，应说明可以用标准库输出 PNG；如果必须用 Pillow、numpy 等第三方库，要在 README 或插件说明中写清安装约束。
6. 单局动态参数，如奖励金额、题目范围、下注金额，优先由命令参数传入；配置页只承载长期稳定配置。

最小示例：

```python
async def _cmd_start(self, client, event, args, account_id, ctx):
    reward = int(args[0]) if args else int(ctx.config.get("default_reward", 0) or 0)
    state = RoundState(chat_id=event.chat_id, reward=reward, answer="42")
    self._rounds[state.chat_id] = state

async def on_message(self, ctx, event):
    ...
    await event.reply(f"+{state.reward}")
    await ctx.client.edit_message(
        state.chat_id,
        state.question_message_id,
        f"{state.question_text}\n\n已答对：{winner_name}\n答案：{state.answer}\n奖励：+{state.reward}",
    )
```

---

## 9. 插件日志

插件日志写法：

```python
await ctx.log(
    "info",
    "猜数字答对，准备发送奖励文案。",
    chat_id=chat_id,
    winner_id=sender_id,
    reward=reward,
)
```

日志原则：

- `message` 写给人看，要通俗说明发生了什么。
- `detail` 放结构化字段：`chat_id`、`rule_id`、`sender_id`、`elapsed_ms`、`reason`。
- 错误日志要说明“哪一步失败 + 是否重试/兜底/跳过”。
- 不要记录完整聊天内容、Token、session、本地绝对路径。

---

## 10. 发布前最小测试清单

- [ ] `plugin.json` 能被静态解析，不依赖 Python 执行。
- [ ] `manifest.py` 导出 `MANIFEST`，`__init__.py` 导出 `PLUGIN_CLASS` 和 `MANIFEST`。
- [ ] 不是旧的单文件结构；`manifest.py`、`plugin.py`、`__init__.py` 三个运行期文件都存在。
- [ ] 插件名、Manifest key、目录名一致。
- [ ] `plugin.json.version`、`MANIFEST.version`、Registry `version` 一致。
- [ ] 启动日志或主要交互消息包含当前插件版本，方便确认远程热更新是否生效。
- [ ] `permissions` 覆盖实际调用的 `ctx.client` 方法。
- [ ] 命令可触发，命令改名后热重载生效。
- [ ] 群聊、私聊、频道/匿名频道下不会因为事件属性缺失崩溃。
- [ ] 进行中重复触发会提示当前状态和下一步操作，不会覆盖已有局。
- [ ] 抢答并发只奖励一次。
- [ ] 超时和答题同时发生时只结束一次。
- [ ] 高频交互有冷却/限流/超时策略，且用户可见文案说明关键规则。
- [ ] 模板类配置有占位符说明和示例预览。
- [ ] 插件禁用、热重载、worker 退出后没有幽灵任务。
- [ ] 取消、完成、超时、禁用、热重载都会清理临时消息、文件和后台任务。
- [ ] 外部 HTTP 请求有 timeout，错误提示已脱敏。
- [ ] 插件日志足够排查“为什么没触发/为什么没发出去”。

---

## 11. Registry 格式

远程 Registry 用于展示可安装插件列表：

```json
{
  "name": "TeleBot Community Plugins",
  "url": "https://github.com/Anoyou/telebot-plugins",
  "plugins": [
    {
      "name": "guess_number",
      "display_name": "猜数字",
      "description": "一个抢答小游戏插件",
      "author": "community",
      "source_url": "https://github.com/example/telebot-plugin-guess-number.git",
      "version": "0.1.0",
      "tags": ["game", "quiz"],
      "min_telebot_version": "0.9.8"
    }
  ]
}
```

Registry 只做索引，不替代插件仓库里的 `plugin.json`。安装时仍以仓库内 `plugin.json` 为准。

---

## 12. 与主开发指南的关系

远程插件必须遵守主开发指南中的通用契约：

- [插件工程规范](./PLUGIN-DEV-GUIDE.md#14-插件工程规范)
- [前端集成模式](./PLUGIN-DEV-GUIDE.md#13-前端集成)
- [插件日志](./PLUGIN-DEV-GUIDE.md#9-插件日志)
- [安全边界](./PLUGIN-DEV-GUIDE.md#12-安全边界)

远程插件作者优先阅读本文件了解安装与沙箱，再阅读主开发指南选择配置模式、消息发送方式和测试清单。
