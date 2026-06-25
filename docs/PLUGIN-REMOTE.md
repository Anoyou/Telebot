# TelePilot 远程插件

本文是当前维护的远程插件开发与安装规范，覆盖仓库结构、安装元数据、运行时 Manifest、权限声明和兼容边界。

## 10. 远程插件

远程插件与内置插件共用同一套 Plugin API、`Manifest`、`PluginContext`、权限沙箱、配置 schema、插件分类和交互 Bot 声明。区别只在安装来源：远程插件从 Git 仓库安装到 `plugins/installed/{name}/`，安装阶段先静态读取 `plugin.json`，不会执行 Python；运行阶段再由 worker loader 加载 `manifest.py` / `plugin.py`。

旧文档 `docs/REMOTE-PLUGIN-GUIDE.md` 只保留跳转说明，远程插件开发与安装规范以本章节为准。

### 适用场景

远程插件适合这些场景：

- 希望把插件独立成仓库，由 Web UI 一键安装、更新、卸载。
- 插件需要给多个 TelePilot 部署复用。
- 插件作者不想改 TelePilot 主仓库，但愿意遵循统一的 Manifest、权限和配置规范。

当前实现采用：

- Git clone / pull 安装更新。
- `plugin.json` 作为安装阶段静态元数据。
- `manifest.py` 作为运行阶段真实 Manifest。
- `Plugin` / `PluginContext` 作为运行时 API。
- 第三方插件使用 `SandboxClient`，按 `permissions` 最小授权。

### 远程插件结构

一个可安装的远程插件仓库至少包含：

```text
guess_number/
├── plugin.json      # 安装阶段静态元数据，不能执行 Python
├── manifest.py      # 运行阶段 Manifest
├── plugin.py        # 插件主类
└── __init__.py      # 导出 PLUGIN_CLASS 和 MANIFEST
```

不再兼容旧的“只有 `plugin.json` + `plugin.py`”单文件结构；缺少 `manifest.py` 或 `__init__.py` 会在安装/更新阶段被拒绝。

### plugin.json

`plugin.json` 用于安装、列表展示和安全校验。安装阶段只读取这个文件，但会同时检查运行期必须存在 `manifest.py`、`plugin.py`、`__init__.py`。

```json
{
  "name": "guess_number",
  "display_name": "猜数字",
  "description": "一个抢答小游戏插件",
  "author": "example",
  "version": "0.1.0",
  "entry": "plugin.py",
  "min_telepilot_version": "0.19.0",
  "commands": ["guess"],
  "cleanup_mode": "resource",
  "tags": ["game", "quiz"],
  "category": "interactive",
  "interaction_entries": [
    {
      "key": "start_guess_number",
      "title": "开始游戏",
      "description": "由交互 Bot 在群内开启一局游戏。",
      "launch_mode": "hybrid",
      "session_scope": "chat",
      "events": ["keyword", "payment_confirmed", "message", "callback_query", "session_close"],
      "preserve_command_trigger": true,
      "command_fallback": {
        "enabled": true,
        "command": "guess",
        "mode": "hint_only"
      },
      "session_policy": {
        "ttl_seconds": 3600,
        "duplicate_start": "reject",
        "close_on": ["winner", "timeout", "session_close"]
      },
      "input_schema": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "prize": {
            "type": "integer",
            "title": "奖金",
            "default": 123,
            "minimum": 1
          }
        }
      },
      "payload_contract": {
        "required_envelope": ["source", "actor", "trigger", "session"],
        "required_event_fields": ["type", "chat_id"]
      },
      "result_contract": {
        "actions": ["send_message", "send_photo", "send_file", "end_session", "result", "settlement"],
        "send_via": ["interaction_bot", "userbot_reply", "bbot_notice"]
      },
      "settlement": {
        "mode": "announce_only",
        "winner_field": "actor.user_id",
        "amount_field": "prize"
      }
    }
  ],
  "permissions": ["send_message", "edit_message", "read_chat"],
  "config_schema": {
    "type": "object",
    "x-ui-mode": "single",
    "properties": {
      "command": {
        "type": "string",
        "title": "触发指令名",
        "default": "guess",
        "minLength": 1,
        "maxLength": 32,
        "pattern": "^\\S+$"
      }
    }
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
| `min_telepilot_version` | 推荐 | string | 最低 TelePilot 版本 |
| `min_telebot_version` | 兼容 | string | 旧字段名，0.15 起仅作为 legacy alias 解析 |
| `commands` | 否 | array | 插件声明的触发指令名，用于帮助文档 |
| `cleanup_mode` | 否 | string | 开发约定字段，建议取 `resource` / `reset` / `no-op`；当前平台不会自动执行该字段对应的清理策略，插件仍需在 `on_shutdown` / 取消 / 超时路径里自行实现幂等清理 |
| `tags` | 否 | array | 分类搜索标签 |
| `category` | 推荐 | string | `interactive` / `automation` / `utility`，与 `manifest.py` 保持一致 |
| `interaction_entries` | 按需 | array | 只有需要交互 Bot 触发的插件才声明；工具/自动化插件保持空或不填 |
| `permissions` | 推荐 | array | 运行时沙箱权限声明 |
| `config_schema` | 推荐 | object | 配置表单和 API 校验依据 |

`plugin.json` 与 `manifest.py` 中的 `version`、`category`、`interaction_entries` 应保持一致；如果插件进入 Registry，Registry 里的 `version` 也要同步。交互入口的新规范还要求 `launch_mode`、`events`、`session_scope`、`session_policy`、`payload_contract`、`result_contract`、`settlement`、`command_fallback`、`preserve_command_trigger` 在两处含义一致，不能只写一边。

`interaction_entries` 是交互 Bot 的兼容声明。每个入口至少要写：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `key` | 是 | 传给插件 `on_interaction(ctx, entry_key, payload)` 的入口名 |
| `title` | 推荐 | 前端下拉框和实验室展示名 |
| `description` | 推荐 | 告诉用户这个入口做什么 |
| `launch_mode` | 是 | `bridge` / `direct` / `hybrid`，决定交互 Bot 如何启动插件 |
| `session_scope` | 是 | `chat` / `user` / `none`，决定平台如何保存会话和路由后续消息 |
| `events` | 是 | 支持的事件白名单，例如 `keyword`、`payment_confirmed`、`message`、`callback_query`、`session_close` |
| `command_fallback` | 按需 | 交互入口不可用时是否提示或受控回退到原 UserBot 命令 |
| `preserve_command_trigger` | 是 | 必须为 `true`，表示原有命令触发不受交互入口影响 |
| `session_policy` | 推荐 | TTL、重复触发、关闭条件、并发策略 |
| `payload_contract` | 推荐 | 平台提供的 `source` / `actor` / `reply_to` / `trigger` / `session` 信封要求 |
| `result_contract` | 推荐 | 标准动作类型、`send_via` 白名单和结束语义 |
| `settlement` | 按需 | 涉及奖金、补发、对账时声明结算责任和字段 |
| `input_schema` | 推荐 | 当前规则可覆盖的入口参数，默认值用于前端预填 |

推荐额外声明 `interaction_profile`，让平台和前端知道这是什么玩法类型。当前约定值：

| `interaction_profile` | 适用场景 |
| --- | --- |
| `session_game` | 群局抢答、竞猜、填空、算题、24 点 |
| `challenge_game` | 双人/多人对战、轮流操作 |
| `reward_pool` | 红包、抽奖池、下注开奖 |
| `utility_trigger` | 只是借交互 Bot 做入口，但后续不是群局互动 |

这只是声明性元数据，不改变插件原有命令触发，也不改变 `on_interaction` 的调用方式。

`launch_mode` 的取值不要随意发明：

| launch_mode | 说明 |
| --- | --- |
| `bridge` | 交互 Bot 收到事件，平台组装信封后调用插件 `on_interaction` |
| `direct` | 只走原命令或插件内部调用，不依赖交互 Bot |
| `hybrid` | 两种方式都支持，但必须保留原命令触发 |

`command_fallback` 不是改写命令语义的开关，只是“交互入口暂不可用时，平台要不要提示用户去用原命令”。如果插件以前能被 `{prefix}game` 启动，新规范下仍必须能被 `{prefix}game` 启动，不能因为加了 `interaction_entries` 就吞掉原命令。

`session_scope` 必须按业务写准：

- 群局、抢答、抽奖、红包、填空等公共流程用 `chat`。
- 置顶促销、个人查询、个人表单等每个用户互不影响的流程用 `user`。
- 一次性执行且不需要平台保存会话的入口用 `none`，并在返回动作里显式返回 `end_session` / `no_session` 更清楚。

不要把 `session_scope` 和交互规则里的 `concurrency` 混在一起。`concurrency=user` 表示“规则按用户限流、冷却、每日次数”；`session_scope=chat` 表示“这个插件是一场群局，其他群友的后续消息也要能进入同一局”。如果群局插件漏写 `session_scope=chat`，用户开启每人 CD 后，后续答题消息可能只会路由给开局者，表现为“联动 Bot 没反应”。

推荐矩阵：

| 插件类型 | `category` | `session_scope` | 说明 |
| --- | --- | --- | --- |
| 24 点、九宫格、猜数字、诗词填空 | `interactive` | `chat` | 群内同一时间一局，大家抢答 |
| 口令红包、彩票下注 | `interactive` | `chat` 或 `none` | 如果后续群消息还要进入入口用 `chat`，只发起一次可用 `none` |
| PT 置顶促销 | `utility` 或 `automation` | `user` | 群友关键词触发，但执行的是个人请求 |
| AI/图片/查询工具 | `utility` | 通常不声明 | 只有确实需要交互 Bot 规则触发时才声明 |

远程插件推荐把 `config_schema["x-ui-mode"]` 写成 `single`。需要多条规则 CRUD 时使用 `rules`，系统常驻能力才使用 `platform`。旧 `schema` 仅作为兼容别名，含义是“字段由 schema 提供，入口使用通用单配置独立页”。

交互入口 payload 使用标准信封，不再只是一组平铺字段：

| 信封 | 说明 |
| --- | --- |
| `source` | 事件来源和发送通道，例如 `interaction_bot`、`userbot`、`platform` |
| `actor` | 触发事件的人；答题、中奖、个人限流和审计优先按它判断 |
| `reply_to` | 应引用的原消息或被回复对象；中奖公告必须尽量保留 |
| `trigger` | 命中的规则、入口、事件和消息，用于排障与幂等 |
| `session` | 平台会话标识、作用域、TTL 和是否新建 |

`payload_contract` 声明插件需要哪些信封和事件字段，`input_schema` 只声明规则可覆盖参数。`source` 不代表中奖用户，`actor` 才是行为主体；`reply_to` 不等同于当前消息，通常用于让结果回复原答案；`trigger` 用来还原“为什么这次调用发生”；`session` 要和插件内部状态 key 的粒度一致。

插件返回动作时必须遵守 `result_contract`。`send_via` 是发送者白名单，常见值只有：

| send_via | 说明 |
| --- | --- |
| `interaction_bot` | 交互 Bot 发送题面、答复、图片和会话提示 |
| `userbot_reply` | 当前账号的 userbot 由 worker 代发指定消息 |
| `bbot_notice` | 通知 Bot 发公告、命中和对账提示 |

未声明 `result_contract.send_via` 时按最小权限处理，只允许 `interaction_bot`。涉及奖金、补发、转账、催付的插件要写 `settlement`，但 `settlement` 只能描述结果和对账字段，不能让交互 Bot 直接拥有发奖权限。钱相关动作仍应由账号 worker 的 userbot 代发或由平台受控结算流程处理。

交互入口不得影响原有命令触发。远程插件可以把业务逻辑抽成共享函数，但 `commands`、`on_command`、`message_channels`、`on_message` 的既有语义必须保持；普通群成员 incoming 消息不能因为 `launch_mode=bridge/hybrid` 或 `command_fallback` 就直接进入 `on_command`。

### manifest.py

`manifest.py` 是运行阶段的 Manifest，必须导出 `MANIFEST`。运行时真正生效的是这里的 `Manifest`，因此远程插件要在 `plugin.json` 和 `manifest.py` 两处同步声明插件身份。

```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="guess_number",
    display_name="猜数字",
    version="0.1.0",
    author="example",
    description="一个抢答小游戏插件",
    category="interactive",
    interaction_entries=[
        {
            "key": "start_guess_number",
            "title": "开始游戏",
            "description": "由交互 Bot 在群内开启一局游戏。",
            "launch_mode": "hybrid",
            "session_scope": "chat",
            "events": ["keyword", "payment_confirmed", "message", "callback_query", "session_close"],
            "preserve_command_trigger": True,
            "command_fallback": {
                "enabled": True,
                "command": "guess",
                "mode": "hint_only",
            },
            "session_policy": {
                "ttl_seconds": 3600,
                "duplicate_start": "reject",
                "close_on": ["winner", "timeout", "session_close"],
            },
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
                },
            },
            "payload_contract": {
                "required_envelope": ["source", "actor", "trigger", "session"],
                "required_event_fields": ["type", "chat_id"],
            },
            "result_contract": {
                "actions": ["send_message", "end_session"],
                "send_via": ["interaction_bot"],
            },
            "settlement": {
                "mode": "announce_only",
                "winner_field": "actor.user_id",
                "amount_field": "prize",
            },
        }
    ],
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

不需要交互 Bot 规则触发的插件不要声明 `interaction_entries`：

```python
MANIFEST = Manifest(
    key="image_tool",
    display_name="图片工具",
    version="1.0.0",
    category="utility",
    interaction_entries=[],
)
```

`plugin.py` 仍然继承 `Plugin`，不要使用旧的 `group/private` 频道写法。当前只支持 `incoming` / `outgoing` 两类消息方向。`__init__.py` 必须导出 `PLUGIN_CLASS` 和 `MANIFEST`：

```python
from .manifest import MANIFEST
from .plugin import GuessNumberPlugin

PLUGIN_CLASS = GuessNumberPlugin

__all__ = ["PLUGIN_CLASS", "MANIFEST"]
```

### 安装与启用流程

通过 Web UI：

1. 进入插件中心的远程插件页面。
2. 输入 Git 仓库地址或子目录 URL。
3. 点击安装。
4. 安装完成后，回到插件中心选择账号启用和配置。

通过 REST API：

```text
POST /api/remote-plugins/install
POST /api/remote-plugins/{name}/enable
POST /api/remote-plugins/{name}/enable-accounts   # body: {"account_ids": [1, 2]}
POST /api/remote-plugins/{name}/disable-accounts  # body: {"account_ids": [1, 2]}
POST /api/remote-plugins/{name}/update
DELETE /api/remote-plugins/{name}
```

安装流程：

```text
1. git clone 到 plugins/installed/{name}/
2. 读取 plugin.json → Pydantic 校验（安装阶段不执行 Python）
3. 静态检查 manifest.py / plugin.py / __init__.py 是否齐全
4. 验证通过 → 注册到数据库
5. 广播 CMD_RELOAD_CONFIG，worker 重新扫描 installed 插件
6. 验证失败 → 删除目录，返回错误
```

启用远程插件有两层开关：

- “远程插件管理”里的启用/禁用是远程插件全局开关。
- “插件中心”里的账号启用/禁用是账号级开关。

远程插件实际加载需要 `InstalledPlugin.enabled` 和 `AccountFeature.enabled` 同时为真；旧 `RemotePlugin` 表仅作为升级兼容的只读快照保留。

Docker 部署时，`plugins/installed/{name}/` 和 `data/plugin_repos/` 必须挂载到持久化卷。否则 `docker compose up -d --build` 重建 web 容器后，数据库可能还保留插件开关，但插件文件或仓库缓存已经从容器临时文件系统消失，最终表现为远程插件指令没有响应。

### API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/remote-plugins` | 列出已安装远程插件 |
| `POST` | `/api/remote-plugins/install` | 从 Git URL 安装 |
| `POST` | `/api/remote-plugins/{name}/enable` | 全局启用远程插件 |
| `POST` | `/api/remote-plugins/{name}/disable` | 全局禁用远程插件 |
| `POST` | `/api/remote-plugins/{name}/enable-accounts` | 按请求体 `account_ids` 批量启用账号 |
| `POST` | `/api/remote-plugins/{name}/disable-accounts` | 按请求体 `account_ids` 批量禁用账号 |
| `POST` | `/api/remote-plugins/{name}/update` | 拉取更新并热重载 |
| `DELETE` | `/api/remote-plugins/{name}` | 卸载并清理 DB |

### 沙箱与权限

第三方远程插件运行时拿到的 `ctx.client` 是 `SandboxClient`，只允许调用 `manifest.py` 中声明的权限。

| 权限 | 允许方法 | 说明 |
|------|----------|------|
| `send_message` | `send_message` / `respond` / `reply` | 发送文本消息 |
| `edit_message` | `edit` / `edit_message` | 编辑消息 |
| `read_chat` | `get_messages` / `get_chat` / `iter_messages` | 读取聊天 |
| `send_file` | `send_file` | 发送图片、文件 |
| `join_chat` | `join_chat` | 加入聊天 |
| `delete_message` | `delete_messages` | 删除消息 |
| `moderate_chat` | `ban_user` / `kick_user` / `mute_user` / `unban_user` | 受控成员管理；高危权限，仅在确实需要封禁、踢出、禁言或解封成员时声明 |

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

### 通用配置页的数据来源

远程插件通用配置页由 `config_schema` 驱动。前端点击“配置”时，不会直接读取磁盘上的 `plugin.json` 或 `manifest.py`，而是读取：

```text
GET /api/accounts/{aid}/features
```

也就是接口返回的 `features[].config_schema`。这个字段来自数据库里的 `Feature.manifest.config_schema`。

远程插件配置链路：

```text
plugin.json.config_schema
  -> 安装/更新服务解析 plugin.json
  -> 后端写入 Feature.manifest.config_schema
  -> /api/accounts/{aid}/features 返回 config_schema
  -> 前端通用独立配置页自动渲染配置表单
```

开发与排查时请记住：

- `plugin.json.config_schema` 是远程插件安装阶段的静态来源。
- 安装/更新后，后端必须把它持久化到 `Feature.manifest.config_schema`。
- 前端通用配置页实际读取 `/api/accounts/{aid}/features` 返回的 `config_schema`。
- 如果 UI 显示“该插件没有可配置的选项”，先检查该 API 返回里的 `config_schema` 是否为空。
- 修改远程插件的 `config_schema` 后，需要在插件中心执行“更新插件”，并确认后端完成元数据回写。

### 远程插件发布前检查

- [ ] `plugin.json` 能被静态解析，不依赖 Python 执行。
- [ ] `manifest.py` 导出 `MANIFEST`，`__init__.py` 导出 `PLUGIN_CLASS` 和 `MANIFEST`。
- [ ] 不是旧的单文件结构；`manifest.py`、`plugin.py`、`__init__.py` 三个运行期文件都存在。
- [ ] 插件名、Manifest key、目录名一致。
- [ ] `plugin.json.version`、`MANIFEST.version`、Registry `version` 一致。
- [ ] `category` 在 `plugin.json` 与 `manifest.py` 中一致。
- [ ] 只有需要交互 Bot 规则触发的插件声明 `interaction_entries`；纯工具类和纯自动化类插件保持空或不填。
- [ ] 交互入口声明了 `launch_mode`，且取值只用 `bridge` / `direct` / `hybrid`。
- [ ] 交互入口声明了 `events`、`session_scope`、`session_policy`、`payload_contract`、`result_contract`，并与插件实现一致。
- [ ] `preserve_command_trigger=true`；新增交互入口后，原有 UserBot 命令仍按原指令名、参数和权限触发。
- [ ] 如声明 `command_fallback`，只做提示或受控回退，不让普通 incoming 消息直接进入 `on_command`。
- [ ] 返回动作的 `send_via` 都命中 `result_contract.send_via` 白名单。
- [ ] 涉及奖金/补发/对账的插件声明了 `settlement`，且交互 Bot 只公告结果，不直接执行钱相关动作。
- [ ] 插件按 `source` / `actor` / `reply_to` / `trigger` / `session` 信封读取输入，不依赖转账通知原文或 Bot Token。
- [ ] 启动日志或主要交互消息包含当前插件版本，方便确认远程热更新是否生效。
- [ ] `permissions` 覆盖实际调用的 `ctx.client` 方法。
- [ ] 指令可触发，指令改名后热重载生效。
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

### 示例与 CI 建议

当前 CI 已校验 `examples/plugins/with_http`、`examples/plugins/with_ai` 和 `examples/plugins/with_interaction`，分别用于演示 `ctx.http` / `external_http` / `allowed_hosts` 的最小组合，`ctx.ai` / `ai_text` / 平台统一 LLM 池的推荐接入方式，以及“原命令 + 交互 Bot 入口”双兼容的最小交互契约。`examples/plugins/translate` 是历史示例，尚未迁移到受限 `PluginContext` + sandbox 权限模型，因此在示例校验脚本中明确跳过。

新增示例时请同步更新 `scripts/validate-plugin-examples.py`：

1. 已迁移到公开 API 的示例加入 `INCLUDED_EXAMPLES`，CI 会检查必要文件、`plugin.json`、`MANIFEST`、`PLUGIN_CLASS`、key/version/category/interaction_profile/interaction_entries/permissions/allowed_hosts 一致性，并实例化插件类。
2. 暂时保留的历史示例加入 `SKIPPED_EXAMPLES` 并写清原因；不要让示例通过 import worker 私有实现绕过 `ctx.http` / `ctx.ai` facade。
3. 示例不得访问网络、真实 LLM Provider、数据库或账号会话；CI 只做静态和 import 级校验。

已接入的 installed 互动插件可额外运行 `python scripts/validate-installed-interaction-plugins.py`，检查 `plugin.json` 与 `manifest.py` 的 `version`、`category`、`interaction_profile`、`interaction_entries` 是否一致，避免安装态和运行态的交互契约慢慢漂移。

### Registry 机制

支持从远程 registry 同步可用插件列表：

```json
{
  "plugins": [
    {
      "name": "guess_number",
      "display_name": "猜数字",
      "source_url": "https://github.com/user/repo",
      "version": "0.1.0",
      "description": "一个抢答小游戏插件",
      "author": "example",
      "tags": ["game", "quiz"]
    }
  ]
}
```

---
