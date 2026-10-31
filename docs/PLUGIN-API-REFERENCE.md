# TelePilot 插件 API 参考

本文是当前维护的插件 API 参考，覆盖配置、派发、日志、前端集成、调试和示例。用户界面与开发文档统一使用“插件”指代可安装、可启停、可配置的扩展能力；历史代码字段名仍按兼容要求保留。

## 1. 三链路心智模型

新插件先判断消息从哪条链路进入，再看 Event Bus、Trace、MessageOps 这些内部契约：

| 链路 | 触发与输入 | 会话/发送语义 | 典型用法 |
| --- | --- | --- | --- |
| 裸直通 | userbot Telethon 实时事件；安装插件收到 `SandboxEvent` 权限包装 | 插件自行处理 userbot 能力 | 抢首响、极低延时 userbot 监听；不覆盖 interaction bot |
| UserBot 标准消息链路 | Event Bus、前缀命令、legacy `on_message`；可含 incoming/outgoing | 只有建立会话时才由 `session.channel=userbot` 路由后续普通收发 | 管理员命令、账号身份动作、第三方 Bot 消息监听 |
| Interaction Bot 标准消息链路 | Event Bus 自主订阅或旧规则会话 | 只有建立会话时才由 `session.channel=interaction_bot` 路由后续普通收发 | 高频群内互动、按钮、题面、普通会话提示 |

唯一例外：`payout`、收付款、发奖永远由 userbot 执行，不随会话通道切到 interaction bot。

必填顶层字段 `requires_platform_capabilities` 必须同时写在 `plugin.json` 与 `Manifest`，声明插件对 `ai` / `interaction_bot` / `webhooks` / `ledger` / `dispatch_debug` 平台模块的依赖；无需求时显式写 `[]`。它不能写在 interaction entry 或 event subscription 上。存量插件缺失时只挂 warning，新装或升级缺失时拒绝（`PLATFORM_CAPABILITIES_REQUIRED`）；`Manifest(requires_platform_capabilities=...)` 参数 0.72.0-beta.2 起才有，传了它的插件 `min_telepilot_version` 不得低于 `0.72.0-beta.2`（见[版本与兼容](#版本与兼容)）；启用叶只会自动点亮允许开启的枝，不会顶开值守预设或管理员强制关闭。完整规则见[远程插件：`requires_platform_capabilities` 声明](./PLUGIN-REMOTE.md#requires_platform_capabilities-声明)和[平台能力热插拔](./PLATFORM-CAPABILITIES.md)。

Event Bus、Trace、MessageOps 是两条标准消息链路的内部契约，不是第四种模式。两条标准消息链路都可使用标准事件信封，并通过 `ctx.messages` / 标准 action 输出；legacy hook 仍保留兼容语义：

```python
from app.worker.plugins.events import event_from_interaction_payload


async def on_event(self, ctx, payload):
    event = payload["tp_event"] if "tp_event" in payload else event_from_interaction_payload(payload)
    text = event.message.text or ""
    if "ping" not in text:
        return []
    return [{
        "type": "send_message",
        "chat_id": event.message.chat_id,
        "reply_to_message_id": event.message.message_id,
        "text": "pong",
    }]
```

旧 `on_message`、`on_command`、`interaction_entries`、旧平铺 payload 只作为迁移兼容说明出现，不再是公共群玩法或新插件的推荐主路径。

裸直通入口只给 userbot 使用：

```python
async def on_direct_message(self, ctx, event):
    if not match(event):
        return {"status": "ignored"}
    try:
        await handle(event)
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
    return {"status": "consumed"}
```

这里的 `event` 不是 `payload`，也不会自动生成标准事件信封或解释 handler 返回的标准 action。安装插件收到的是保留实时字段读取能力的 `SandboxEvent`，嵌套按钮已净化；builtin 内部插件才保留真实 Telethon event。平台会记录直通路由与插件调用 Trace；显式 `ctx.messages` 动作和受支持的 `ctx.client` 操作仍进入统一执行或动作审计。它不接 interaction bot 事件；需要 Interaction Bot 按钮回调、Inline、付款确认、标准会话或完整事件回放时，改用标准消息链路。

账号二次开关只决定插件是否加入直通调度，优先级只决定尝试顺序。只有 `consumed` 会停止后续直通与普通链路；`ignored`、`failed`、异常和未返回结果都会继续调度并最终回退。兼容返回值为 `True → consumed`、`False/None → ignored`、`{"consume": true/false} → consumed/ignored`。

直通不是账号允许名单的豁免通道：incoming 消息与标准链路走同一账号允许名单（"允许会话"白名单）判定，名单非空时会话不在名单内的消息不会进入任何直通插件；outgoing 消息不受该名单限制。

## 2. 标准会话链路与单入口模型

进入标准链路后，互动插件按“触发方式决定整段会话通道”理解；插件全程不需要感知或选择收发通道：

| 开局方式 | 会话通道（收 + 发） | 说明 |
| --- | --- | --- |
| UserBot 前缀命令 | `userbot` | 后续消息、继续追问、普通回复默认都走 userbot，不覆盖 interaction bot |
| 关键词 / 付款确认 / 按钮回调 | `interaction_bot` | 题面、按钮、编辑消息默认都走 interaction bot |
| `payout` | 固定 `userbot` | 不受会话通道影响，始终经 userbot 执行 |

推荐写法：

1. 用一个 `on_event(ctx, payload)` 覆盖 `command`、`keyword`、`payment_confirmed`、`message`、`callback_query`、`session_expired`。
2. 读取 `payload["tp_event"]` 或 `event_from_interaction_payload(payload)`，不要再围绕旧平铺字段写分支。
3. 单局状态保存在 `session.data`，变更后返回 `update_session`；Event Bus 订阅入口首次建局必须先返回 `start_session`，只有当前事件已带有效会话时才可直接 `update_session`。不要再依赖进程内全局状态才能继续游戏。
4. 普通发送类动作不要写 `send_via`，平台会继承 `session.channel`；只有跨通道公告、特殊管理消息或迁移桥兼容这类高级场景才显式覆盖。
5. userbot 会话里的按钮会降级为文本编号面板；玩家回复编号后，平台会把它合成为 `callback_query`，并在 `source.synthetic="text_button"` 标记来源。

`interaction_trigger_modes`、`default_trigger_modes`、`callback_fast_ack` 是当前运行时契约。插件发布前应在目标 TelePilot 版本上运行示例校验和真实账号 smoke test，不要为旧分支猜测兼容行为。

### 2.1 动作 × 执行体分工表

标准 action 会按入口进入三类执行体：E1 是 Event Bus 的 UserBot 直执行（[loader.py](../backend/app/worker/plugins/loader.py)），E2 是 Interaction Bot 的投递编排（[delivery.py](../backend/app/services/interaction/delivery.py)），E3 是 E2 调用的 UserBot RPC 动作面（[runtime.py](../backend/app/worker/runtime.py)）。下表是插件作者可依赖的边界；不是三个执行体都必须具备同一种能力。回归覆盖见 [三方 parity 矩阵](../backend/app/tests/test_interaction_executor_parity.py)。

| 动作/情形 | E1 Event Bus UserBot | E2 Interaction Bot delivery | E3 Worker RPC | 插件作者应知的契约 |
| --- | --- | --- | --- | --- |
| `send_message`、媒体、编辑、删除、置顶（`userbot_reply`） | 直接以账号身份执行 | 编排、审计后委派 | 实际 UserBot RPC | UserBot 会话中 inline 按钮降级为文本编号；不要依赖原始 `reply_markup`。 |
| `send_message`、媒体、编辑（`interaction_bot`） | 仅在该入口已具备 Bot token 时按 Bot API 发送 | Bot API 实际发送 | 不进入 | `reply_markup` 是 Interaction Bot 富消息能力；不要把它当 E3/UserBot 富消息能力。 |
| `send_rich_message` | 可走 UserBot 富消息路径，但不承诺 Bot API inline markup 等能力 | `interaction_bot` 时走 Bot API | `userbot_reply` 时实际执行 | 选择通道时以富消息能力边界为准，不能假定三端渲染相同。 |
| `click_callback_button` | 支持，以 UserBot 点击第三方 Bot 按钮 | 有意不代点，记录 `skipped/unsupported_send_via` | 不支持 | 这是 E1 专属能力，不是待修的不一致。 |
| `answer_callback` / `answer_inline_query` | 调用 Interaction Bot API | 调用 Interaction Bot API | 不进入 | 回答回调/Inline 必须视为 Bot API 动作，不能期待账号 UserBot RPC 代答。 |
| `answer_guest_query` | 调用 Interaction Bot API（`answerGuestQuery`） | 调用 Interaction Bot API；访客事件中每条消息只回复一次，重复调用记 `guest_reply_already_used` | 不进入 | Bot API 10.0 访客模式专用；访客事件里的消息类动作会自动合并成一次访客回复。 |
| `payout` | 固定 UserBot 路径 | 编排、审计、补偿后委派 | 实际 UserBot RPC | 始终经过 T3 资金闸与补偿/幂等语义；`message_id=None` 不能安全关联时仍按既有边界放行。 |
| `start_session` | 内联创建/续用会话 | `apply` 仅记录控制动作，外层入口负责建会话 | 不进入 | Event Bus 需要先显式 `start_session`；关键词、付款等既有入口会按自己的已修流程预建会话。 |
| `update_session` | 更新已有会话 | 更新已有会话 | 不进入 | Event Bus 不会隐式建会话：裸 `update_session` 失败为 `session_not_found`，必须先 `start_session`。 |
| `result`、`end_session`、`close_session`、`no_session` | 控制类动作 | 控制类动作 | 不进入 | 会话结束/结果的外层编排归入口负责；这些动作不是普通 UserBot RPC。 |
| 未知 action type | `failed/unsupported_action` | `failed/unsupported_action` | RPC 拒绝为 `unsupported_action` | 未知动作会计入批处理 `failed`，不得把它当作可忽略的向前兼容。 |

限流拒绝统一记录为 `failed/rate_limited`；插件可据错误码和可选等待信息决定是否延迟重试，平台不会隐式重放。按 `rule_id` 隔离去重键的既有边界保持不变。

## 3. Plugin 基类（兼容层）

```python
class Plugin:
    # === 必须设置 ===
    key: str                          # 唯一标识，也是插件 key
    display_name: str                 # 显示名

    # === 可选配置 ===
    message_channels: set[str]        # 监听方向: {"incoming"} / {"outgoing"} / 二者都监听
    owner_only: bool = True           # 只影响 on_message；False 表示允许普通成员消息进入 on_message
    commands: dict = {}               # TG 内指令；只由本账号 outgoing 指令触发
    command_config_keys: set[str] = set()  # 这些配置变化后需要重载并重新注册指令
    description: str = ""             # 描述（用于帮助系统）

    # === 生命周期钩子 ===
    async def on_startup(self, ctx: PluginContext) -> None:
        """插件激活时调用一次。"""

    async def on_shutdown(self, ctx: PluginContext) -> None:
        """插件关停前调用一次。必须幂等。"""

    # === 事件处理 ===
    async def on_event(self, ctx: PluginContext, payload: dict) -> list[dict] | None:
        """Event Bus 主入口；新插件优先实现。"""

    async def on_interaction(
        self, ctx: PluginContext, entry_key: str, payload: dict
    ) -> list[dict] | None:
        """历史交互入口兼容桥。"""

    async def on_message(self, ctx: PluginContext, event) -> None:
        """历史消息事件回调。"""

    async def on_message_edited(self, ctx: PluginContext, event) -> None:
        """历史消息编辑回调；只有显式重写该方法的插件才会收到。"""

    async def on_command(self, ctx: PluginContext, cmd: str, args: list[str], event) -> bool:
        """指令派发回调。返回 True 表示已处理。"""
        return False
```

`commands` 是基类上的空字典占位。命令名来自配置时，请像完整示例那样在 `__init__` 或 `on_startup` 中赋值 `self.commands = {...}`；不要修改 `Plugin.commands` / `type(self).commands`，否则同一进程里其它账号实例可能共享到错误命令。

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
    account_config: dict   # 原始账号级配置（不含 schema/global 合并值）
    rules: list            # 规则列表
    client: Any | None     # 受控客户端 facade；新插件不要作为主动发送主路径
    messages: Any | None   # MessageOps facade；发送/编辑/删除/按钮/Inline 主路径
    identities: Any | None # 群内安全公开身份 facade
    http: Any | None       # HTTP facade；需要 external_http + allowed_hosts
    ai: Any | None         # AI facade；需要 ai_text（或独立 ai_agent）；识图另需 ai_vision
    skills: Any | None     # Agent Skills 只读 facade；需要 agent_skills（0.106.0-beta.1+）
    capabilities: PluginCapabilities  # 能力协商（0.107.0-beta.1+）：has / version / names
    engine: Any | None     # RateLimitEngine；安装型插件通常为 None
    redis: Any | None      # builtin 可为原始 Redis；安装插件为 PluginRedisFacade
    storage: Any | None    # PluginStorage；按账号和插件隔离的持久化 KV facade
    data_dir: Path | None  # 插件独享的持久化文件目录；更新插件代码时不会被覆盖
    log: Callable          # 日志函数
    scheduler: Any         # 平台调度器 facade
    generation: int        # generation guard 计数
    account_proxy_url: str | None  # 账号代理 URL；只供平台 facade 组装，不应写入日志
    account_proxy_error: str | None  # 账号代理配置错误（已删除/不支持/无效）；ctx.http 据此在请求前失败

    # 简单模式命令运行字段
    event: Any | None
    args: list[str]
    command: str

    # 工具方法
    async def conversation(self, peer, timeout=30) -> Conversation:
        """内部/真实 TelegramClient 兼容入口；普通安装插件不可依赖。"""
```

注意：核心平台兼容代码可能拿到完整运行时能力；远程/本地/插件库安装型插件拿到的是受控上下文：`ctx.client` 是 `SandboxClient` facade，指令 handler 中传入的 `client` 参数与 `ctx.client` 同源。安装型插件的 `ctx.redis` 是 `PluginRedisFacade`，会自动添加 `plugin_store:{account_id}:{plugin_key}:` 前缀，并拒绝 `keys`、`scan`、`eval`、`pipeline`、`pubsub` 等操作；builtin 兼容代码才可能拿到原始 Redis client。新插件的普通状态仍应优先使用 `ctx.storage`。`ctx.engine` 只供核心 builtin 兼容代码直接依赖，安装型插件通常为 `None`。受控上下文用于收口常用操作和审计，不是公共插件市场式强沙箱。

`SandboxClient.send_message(...)` / `send_file(...)` 会先过 UserBot 动作限速：限速拒绝时抛出 `app.worker.plugins.sandbox.PluginSendRateLimited`（不再静默返回 `None`）。异常的 `detail` 携带结构化限速详情（`rate_limit_action` / `rate_limit_backend` / `reason` / `wait_seconds`），`retry_after` 是建议等待秒数；未触发限速时返回值与 Telethon 原生调用一致。插件应在主动发送处捕获该异常做降级或提示。

插件需要 SQLite、索引文件或其他文件型持久化时，必须写入 `ctx.data_dir`，不要写进 `Path(__file__).parent`、插件安装目录或仓库源码目录。TelePilot 更新插件时会整体替换代码目录，而 `ctx.data_dir` 位于持久化插件卷的 `_data/<plugin_key>/` 下，不随代码更新删除。插件仍需使用 `ctx.account_id` 隔离账号数据；若一个数据库文件服务多个账号，表结构必须包含账号隔离键。

为兼容旧插件，更新器会在替换代码目录前迁移插件根目录的 `*.sqlite3` 和顶层运行态 `*.json`（排除 `plugin.json`），并在新目录保留兼容链接；已有 `ctx.data_dir` 文件不会被旧文件覆盖。这个迁移只负责旧数据过渡，不扫描嵌套目录，也不能替代新代码直接使用 `ctx.data_dir`。

### 4.0 受控 facade：ctx.http 与 ctx.ai

第三方插件可以使用两个受控 facade，但必须在 Manifest 中显式声明权限；未声明或策略不完整时字段会是 `None`：

- `ctx.http`：声明 `permissions=["external_http"]` 且填写 `allowed_hosts` 后注入。它限制协议、域名、超时、响应大小，并在发起请求前阻断 localhost/内网/链路本地地址。默认走账号代理；账号代理若已删除、类型不受支持或配置无效，Web 配置动作和 worker 都会在网络请求前失败。只有 Manifest 的 `http={"allow_direct": true}` 与账号配置共同请求 direct 时才允许直连，这属于显式授权而非失败回落；未注入 `ctx.http` 的纯本地配置动作不会查询代理。注意：`ctx.http` 目前只提供协议/域名/SSRF/超时/响应上限防护，不做按插件的请求频率或流量配额（区别于 `ctx.ai` 的 token 配额）；异常高频使用需要管理员通过运行日志观察并处置。
- `ctx.ai`：声明 `permissions=["ai_text"]` 后注入。它复用 TelePilot 的 LLM Provider 池、fallback 链、账号级预算和 usage 记录；插件只能拿到脱敏 provider 元数据，不能读取 `api_key_enc`、`base_url` 或代理 URL。显式绑定缺失、已停用或无效代理的 Provider 会从插件路由排除，不会自动改成 DIRECT。
- `ctx.ai.complete()` 推荐用 `provider_tag` 按用途选择 provider；`tag` / `tags` 是兼容别名且已 deprecated，新插件不要依赖它们作为主要入口。可选 `route="fixed"|"tag"|"auto"` 显式声明路由模式（留空时按旧参数推断，向后兼容）；返回结果的 `routing` 字段是脱敏路由摘要（模式 / provider / 生效模型 / 命中 tag / 协议 / 身份 / 是否 fallback），不含 key、base_url、代理或内部分类器细节。插件不能指定 UA、身份、密钥、代理、内部分类器或全局 fallback。
- `ctx.ai.stream_complete()` 返回 Provider 原生文本 delta 的异步迭代器，支持 Chat Completions、Responses 与 Anthropic Messages；不拆分完整响应、不在已输出部分文本后切 Provider。上游忽略 `stream=true` 而返回普通 JSON 时，同一次请求的完整文本作为一个块产出，不会再次调用模型。调用方必须消费到迭代器自然结束；取消、提前关闭、超时或异常按已发起调用保守结算。需要跨 Provider 的完整响应 fallback 时使用 `complete()`。
- `ctx.ai.run_agent()` 需要独立 `ai_agent` 权限，并同时要求 `capabilities.agent_tools.enabled=true`、manifest `agent_tools[]` 声明和调用方传入同名 handler。平台限制轮数、工具数、重复调用、token 与总超时（插件可用 `max_steps` / `max_tool_calls` / `max_total_tokens` 在平台上限内收紧）；只读工具可并行，副作用工具串行。同样支持 `route="fixed"|"tag"|"auto"`，且 Agent 路由会预先排除没有已启用模型的 provider（无法支撑 tools 调用）。
- `ctx.ai.list_providers()` 可用于展示当前账号可见的脱敏 provider 摘要；更完整的 AI facade 说明见 `docs/PLUGIN-AI.md`。
- `ctx.ai.complete(..., web_search=True, web_search_context_size="low"|"medium"|"high")` 请求原生联网搜索：把 `web_search` 作为**工具**交给模型（Responses API，`tool_choice` 保持 auto，由模型自行决定是否搜索，不强制每次搜索）；OpenAI 官方 chat_completions Provider 按 `web_search_api_format` 临时切到 Responses。引用来源在 `AIResult.sources`。需要“该搜就搜”时请在系统提示里引导（并注入当前日期），不要用关键词强制。
- `ctx.ai.complete(..., images=[bytes, ...])` 把图片交给支持图片输入的模型（最多 4 张、每张 ≤ 8MB），需要 `ai_vision` 权限；`ctx.ai.vision_enabled` 表示是否已授权。`await ctx.ai.fetch_bot_image(file_id, max_bytes=...)` 用当前账号交互 Bot 下载用户发来的图片（`message.media.file_id` 或 `reply_to.media.file_id`），同样需要 `ai_vision`，不落盘、不返回路径或 Token。
- 请求的能力（`web_search` / `images`）在所选 Provider 及 fallback 链上都不支持时抛 `AICapabilityError`（`AIUnavailableError` 子类），在发出网络请求前判定；插件可去掉该能力降级重试。
- 推理模型（GLM / DeepSeek / Qwen / Kimi 等）：`AIResult.text` **只包含模型的最终 content**，思考过程（Chat Completions 的 `reasoning_content`、content 里的 `<think>…</think>`、Responses 的 reasoning 项）单独放在 `AIResult.reasoning`（另有 `reasoning_tokens`、`finish_reason`），插件不要把它发给用户。content 为空时平台不再用推理过程兜底：输出额度被推理耗尽（`finish_reason=length`）或只返回了推理时抛 `AIResponseTruncated`（`AIUnavailableError` 子类，fallback 链会先尝试下一个 Provider），并记一条 info 日志（finish_reason、max_tokens、推理 Token）。`ctx.ai.stream_complete` 同样跳过推理增量。
- `ctx.ai.complete(..., thinking="disabled"|"enabled")`（0.106.0-beta.2+）：对支持的 OpenAI 兼容上游关闭/开启思考——智谱 / Z.ai（`bigmodel.cn`、`z.ai`、`glm-*`）与 DeepSeek 下发 `thinking={"type": ...}`，阿里云百炼 DashScope（Qwen3）下发 `enable_thinking`；其它上游忽略该参数（OpenAI 官方等会拒绝未知参数）。旧平台的 `complete` 没有这个参数，插件可用 `inspect.signature` 探测后再传。
- 推理模型的输出 Token 下限：模型名属于常见推理系列（GLM-4.5+/GLM-5、DeepSeek-R1/Reasoner、QwQ/Qwen3、Kimi K2/K3、o1/o3/o4、GPT-5 等）或模型清单声明了推理档位时，`complete` 请求的 `max_tokens` 低于 `PLUGIN_AI_REASONING_MIN_OUTPUT_TOKENS`（默认 4096，0 表示不抬高）会自动抬到该值，避免额度全被推理吃掉；传 `thinking="disabled"` 时不抬高。
- `ctx.skills`：在 `permissions` 声明 `agent_skills` 后注入（旧版本平台为 `None`，插件需降级）。只读获取 **Agent Skills**（Anthropic `SKILL.md` 目录格式）文本，**从不执行**其中脚本：
  - `await ctx.skills.list(names=None)` 返回管理员已「对插件启用」（Web 助手页顶部「Skills」按钮、iOS App 助手页右上角「Skills」按钮里的开关）的已安装 skill（`name` / `description` / `version` / `files`）；传 `names` 时只返回其中仍可用的，通常传插件配置里的多选值。
  - `await ctx.skills.load(name, max_chars=None)` 返回 SKILL.md 正文（去掉 front matter，默认截断 8000 字符、上限 16000）；`await ctx.skills.read_file(name, path, max_chars=None)` 读取附属文本文件。不可用时抛 `PluginSkillUnavailable`（`LookupError` 子类）。
  - `await ctx.skills.bundled(max_chars=None)` 返回**本插件内置**且仍对插件启用的 skill 正文：插件目录 `skills/<name>/SKILL.md` 会在插件加载时由平台登记为内置 skill（默认只对插件启用、不可卸载、可停用）；管理员在上述 Skills 管理界面编辑后保存为覆盖层，插件升级不会覆盖，只标记「内置有新版本」并允许查看差异、恢复默认或保留修改。
  - 配置里让管理员选择 skills：数组字段声明 `"x-ui-widget": "agent-skill-multi-select"`（`items.type=string`，可选字段、不进 `required`）。卸载 skill 时平台会从所有插件配置的这类字段中移除该名称。
  - 推荐渐进式披露：先只把 `name + description` 交给模型判断要不要用（`ctx.ai.complete` 不支持自定义工具时用两步法），再 `load` 需要的正文拼进最终请求；面向访客时只注入文本并控制长度。

标准链路示例：

```python
async def on_event(self, ctx, payload):
    message = payload["message"]
    chat_id = message.get("chat_id") or (payload.get("chat") or {}).get("id")
    reply_to = message.get("message_id")

    if ctx.http is None:
        return [{
            "type": "send_message",
            "chat_id": chat_id,
            "reply_to_message_id": reply_to,
            "text": "本插件需要 external_http 权限和 allowed_hosts",
        }]

    response = await ctx.http.get("https://api.github.com/zen")
    preview = response.text.strip().replace("\n", " ")[:120]
    return [{
        "type": "send_message",
        "chat_id": chat_id,
        "reply_to_message_id": reply_to,
        "text": f"HTTP {response.status_code}: {preview}",
    }]
```

管理员命令兼容示例可以继续用 `event.edit(...)` 更新命令消息；标准公共互动插件应优先返回标准 action 或通过 `ctx.messages` 缓存标准 action。

### 4.1 持久化 facade：ctx.storage

`ctx.storage` 是 `PluginStorage` facade，用于插件自己的持久化 key-value 状态。loader 使用原始 Redis 后端以及 `PluginContext.account_id`、`PluginContext.feature_key` 单独构造它，不会再套一层安装插件可见的 `ctx.redis` facade，因此不会产生双重前缀。它与 `PluginRedisFacade` 使用相同的 `plugin_store:{account_id}:{plugin_key}:` 命名空间格式，所以同一插件在不同账号之间、同一账号的不同插件之间不会串数据。

API 签名：

| 方法 | 签名 | 返回 | 说明 |
|------|------|------|------|
| `available` | `ctx.storage.available` | `bool` | 是否挂载了 Redis 后端 |
| `get` | `await ctx.storage.get(key, default=None)` | `Any` | 读取 JSON 值；缺失、Redis 不可用或 JSON 不可读时返回 `default` |
| `set` | `await ctx.storage.set(key, value, *, ttl=None)` | `bool` | 写入 JSON 值；`ttl` 为可选秒数，必须大于 0；Redis 不可用时返回 `False` |
| `delete` | `await ctx.storage.delete(*keys)` | `int` | 删除一个或多个用户 key，返回删除数量 |
| `incr` | `await ctx.storage.incr(key, amount=1, *, ttl=None)` | `int | None` | 整数递增；Redis 不可用时返回 `None` |
| `get_all` | `await ctx.storage.get_all()` | `dict[str, Any]` | 返回当前账号和插件命名空间下全部可解析 JSON 值 |
| `items` | `await ctx.storage.items()` | `dict[str, Any]` | `get_all()` 别名 |

`key` 会先转成去首尾空白的字符串，空 key 会抛 `ValueError`。`value` 通过 `json.dumps(..., ensure_ascii=False, separators=(",", ":"))` 序列化，不能 JSON 序列化的值会在 `set` 时抛出原始序列化错误。

`ttl` 只支持 `set` 和 `incr`，单位为秒，可传 `int` / `float` / 可转整数的值；`None` 表示不设置过期，`<= 0` 或不可转整数会抛 `ValueError`。`get_all()` / `items()` 只扫描当前命名空间，过期或不可解析 JSON 的值会被跳过。

`incr(ttl=...)` 的窗口语义要特别注意：Redis 后端优先使用 `INCRBY` / `INCR` 做递增，然后单独调用 `EXPIRE` 刷新 TTL，所以 TTL 是“每次递增都续期”的滑动窗口，不是固定窗口。递增和设置过期不是一个事务；如果进程在两条命令之间崩溃，可能留下已递增但未设置过期的 key。若 Redis facade 没有 `incrby` / `incr`，实现会退回 `get` + `set`，该退回路径本身也不是原子递增。需要 NX、CAS、锁或严格原子计数时，不要把 `ctx.storage` 当分布式锁使用，应交给平台会话/结算链路或官方受控低层实现。

不使用时没有额外开销：运行时只是在上下文里挂载 facade；插件不调用 `ctx.storage`，就不会触发 Redis 读写或扫描。

推荐写法：

```python
round_key = f"round:{chat_id}"

state = await ctx.storage.get(round_key, default={})
await ctx.storage.set(round_key, {"answer": 24, "active": True}, ttl=3600)

attempts = await ctx.storage.incr(f"attempts:{chat_id}:{user_id}", ttl=60)
if attempts is not None and attempts > 5:
    return [{"type": "send_message", "text": "尝试太频繁，请稍后再试。"}]
```

### 4.2 可用上下文与访问方式（PluginContext Contract）

插件请只从 `PluginContext` 读取运行时信息，不要跨层 import worker 私有实现。

| 字段 | 访问方式 | 说明 |
|------|----------|------|
| `ctx.account_id` | `ctx.account_id` | 当前账号 ID（账号级隔离边界） |
| `ctx.feature_key` | `ctx.feature_key` | 当前插件 feature key |
| `ctx.config` | `ctx.config.get("k")` | 插件配置（账号/全局已合并后的可见配置） |
| `ctx.account_config` | `ctx.account_config.get("k")` | 原始账号级配置；通常优先读已合并的 `ctx.config` |
| `ctx.rules` | 遍历 `ctx.rules` | 当前账号 + 当前插件已启用规则 |
| `ctx.client` | 高级兼容场景只读或受控调用 | UserBot 客户端 facade；新插件不要用它作为主动发送主路径，消息输出优先使用 `ctx.messages` 或标准 action |
| `ctx.engine` | `await ctx.engine.acquire(...)` | 仅核心 builtin 兼容代码可直接依赖；安装型插件通常为 `None` |
| `ctx.redis` | `await ctx.redis.get("key")` | 安装插件拿 `PluginRedisFacade`，自动添加账号 + 插件前缀；不支持 `keys/scan/eval/pipeline/pubsub` 等操作。builtin 才可能拿原始 Redis；普通状态优先用 `ctx.storage` |
| `ctx.storage` | `await ctx.storage.get("k")` / `await ctx.storage.set("k", value, ttl=3600)` | 推荐的插件持久化 facade；按账号 + 插件自动命名空间隔离 |
| `ctx.log` | `await ctx.log("info", "...", **detail)` | 运行日志写入器 |
| `ctx.scheduler` | `ctx.scheduler.register(job_id, schedule, callback, *, replace=True)` / `ctx.scheduler.unregister(job_id)` / `ctx.scheduler.unregister_all()` / `ctx.scheduler.list_jobs()` | 调度 facade；只能看到和操作本插件名下的任务 |
| `ctx.http` | `await ctx.http.get(url, params={...})` / `await ctx.http.post(url, json={...})` | 安全 HTTP facade；第三方插件需声明 `external_http` + `allowed_hosts` |
| `ctx.ai` | `await ctx.ai.complete(system="...", user="...")` | 文本 LLM facade；第三方插件需声明 `ai_text` |
| `ctx.ai` | `await ctx.ai.run_agent(..., handlers={...})` | 有界工具调用；第三方插件需声明独立 `ai_agent` 与工具双白名单 |
| `ctx.skills` | `await ctx.skills.list(names)` / `load(name)` / `read_file(name, path)` / `bundled()` | Agent Skills 只读 facade；需声明 `agent_skills`，旧平台为 `None` |
| `ctx.capabilities` | `ctx.capabilities.has("messages.guest_reply_markup")` | 能力协商（0.107.0-beta.1+，旧平台没有该属性，用 `getattr(ctx, "capabilities", None)`），见 [能力协商与 Plugin API 版本](#能力协商与-plugin-api-版本) |
| `ctx.data_dir` | `ctx.data_dir / "state.sqlite3"` | 插件独享、更新代码不会覆盖的持久化目录 |
| `ctx.messages` | `await ctx.messages.send(...)` / `apply([...])` | MessageOps facade；具体 helper 取决于交互缓冲或常驻/命令/调度上下文，见下方矩阵 |
| `ctx.identities` | 通常通过 `resolve_public_sender_identity(ctx, ...)` / `resolve_public_sender_identities(...)` 间接使用；也可直接 `await ctx.identities.resolve(...)` / `resolve_many(...)`，UserBot 专用场景调用 `resolve_userbot(...)` | 平台注入的群内安全身份 facade；只返回标签、姓名、管理员状态和解析状态，不向插件开放成员目录 |
| `ctx.conversation(...)` | `async with ctx.conversation(peer)` | 仅真实 `TelegramClient` 的 builtin/内部兼容场景可靠；普通安装插件的 `SandboxClient` 不支持其 handler/raw MTProto 要求 |

### 4.3 `ctx.messages` 上下文 × 方法矩阵

上表的 `send_photo/edit_caption` 示例只适用于交互缓冲 facade，不能据此推断所有入口都有同名 helper。`ctx.messages` 会按入口注入不同的 Python 类：

| 上下文 | facade | 可直接调用的方法 |
| --- | --- | --- |
| Event Bus / `on_interaction` 的当前交互入口 | 缓冲 facade | `apply`、`send`、`send_rich`、`send_photo`、`send_file`、`edit`、`edit_rich`、`edit_caption`、`delete`、`pin`、`answer_callback`、`answer_inline_query`、`answer_guest_query`、`payout`、`update_session`、`read_saved_message_id`、`delete_saved_message_id`；UserBot 执行链路才允许 `click_callback_button` |
| 常驻上下文、插件命令、legacy `on_message` / `on_message_edited`、裸直通、scheduler/后台 callback | live facade | `apply`、`send`、`send_rich`、`edit`、`delete`、`pin`、`answer_callback`、`click_callback_button`、`answer_inline_query`、`answer_guest_query`、`payout`、`read_saved_message_id`、`delete_saved_message_id` |

live facade 当前没有 `send_photo`、`send_file`、`edit_rich`、`edit_caption`、`update_session` helper；直接调用会得到属性不存在错误。后台需要发送媒体或编辑 caption 时，构造文档化的标准 action 后调用 `await ctx.messages.apply([action], entry_key="...")`。`update_session` 只在拥有当前交互会话的入口中使用，不把后台任务伪装成会话更新。

`apply(actions, entry_key=None)` 是公共 live 执行入口：它会规范化 action、补 trace context 并立即交给 UserBot/Event Bus 动作执行器，同时返回 JSON-safe 的批次结果。旧插件可以继续忽略返回值；需要在动作成功后更新业务状态或记录成功日志的插件，应检查 `ok`：

```python
result = await ctx.messages.apply(actions, entry_key="main")
if not result["ok"]:
    raise RuntimeError(
        f"消息动作失败：failed={result['failed']}, dropped={result['dropped']}"
    )
```

返回字段固定为 `ok`、`executed`、`failed`、`skipped`、`dropped`、`kinds`；0.106.0-beta.3+ 有失败动作时另带 `errors`：`[{"index", "type", "error_code", "error"}]`（`error` 已脱敏，Telegram 拒绝时为 Telegram 返回的错误描述，`error_code=telegram_api_error`），插件可直接记进自己的日志；平台的“插件消息动作部分执行失败”告警同时带 `error_code`、`error` 与 `telegram_error`。`ok` 仅在没有失败动作且没有因批次上限丢弃动作时为 `true`；`kinds` 按执行顺序列出规范化动作类型。平台仍会为失败批次写结构化插件告警和逐动作 ActionEvent，插件不需要解析日志来判断成功。交互缓冲 facade 也提供同一返回契约，供延迟或即时提交，但 Interaction Bot 入口仍拒绝 `click_callback_button`。

`read_saved_message_id(key)` 与 `delete_saved_message_id(key)` 只访问平台拥有、按账号和插件隔离的 message-id 命名空间，不开放原始 Redis。普通纯 `BufferedMessageOps` 没有 live 存储时读取返回 `None`、删除返回 `False`；平台注入的交互 adapter 和 live facade 会转发到真实存储。

### 4.4 权限边界与禁止事项

1. 第三方插件必须遵循 `manifest.permissions` 最小授权，未声明的客户端能力不可调用。
2. 第三方插件不得假设 `ctx.engine` 恒可用；需要状态持久化时优先使用 `ctx.storage`。确需 Redis 的 NX、hash/list/set 等已开放语义时才访问 `ctx.redis`，并接受自动命名空间和方法白名单；不要假设它是原始 Redis client。
3. 禁止通过插件绕过账号边界：不要读写其他账号配置、规则、会话状态。
4. 禁止在插件中执行系统级/运维级动作（如重启进程、安装/卸载插件、修改权限模型）。
5. 禁止依赖 worker 私有实现或 monkey patch 运行时对象来“扩权”。
6. 禁止把敏感凭据直接打到日志；`ctx.log` 只记录最小必要信息。

### 4.5 配置/账号/运行时数据访问建议

1. 配置：通过 `ctx.config` 读取；按 `config_schema` 的 `level` 设计字段，不自行拼接跨账号配置。
2. 账号：通过 `ctx.account_id` 做所有业务隔离键；插件持久化状态优先放进 `ctx.storage`，由平台按账号 + 插件隔离。
3. 运行时：管理员命令兼容场景可使用 `ctx.client` 已开放的方法和 `ctx.scheduler`；普通安装插件不要依赖 `ctx.conversation`。公共群互动、Interaction Bot callback、Inline、付款确认和后台通知优先使用 `ctx.messages` 或标准 action。
4. 日志：统一用 `ctx.log`，并在 `detail` 里带结构化字段（如 `chat_id`、`action`）。
5. 兜底：对可选能力（`engine`/`storage`/`redis`）做 feature-detection，保证第三方插件在受限上下文也能安全降级。

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
| `permissions` | list | 权限声明，默认 `[]`；第三方插件必须显式声明需要的能力 |
| `config_schema` | dict | JSON Schema，有配置的插件必须写 |
| `requires_features` | list | 依赖的其他插件 key |
| `min_telepilot_version` | str | 最低 TelePilot 版本要求，远程插件建议填写；0.107.0-beta.1 起可带 beta 号，见 [`min_telepilot_version` 怎么填](#min_telepilot_version-怎么填) |
| `min_telebot_version` | str | 旧字段名，仅作为兼容别名保留（已弃用，加载时计入弃用统计），新插件不要再新增 |
| `plugin_api` | str | Plugin API 兼容范围（0.107.0-beta.1+，如 `"^1.0"`）；**只写在 `plugin.json`**，不要传给 `Manifest(...)`，见 [能力协商与 Plugin API 版本](#能力协商与-plugin-api-版本) |
| `category` | str | `interactive` / `automation` / `utility`，只决定展示分组 |
| `event_subscriptions` | list | 标准链路订阅声明，描述插件想从 Event Bus 接收哪些事件；每条可选 `timeout_seconds` 放宽单次处理时限（默认 15 秒，平台上限 90 秒，见下文“处理时限”） |
| `capabilities` | dict | 高风险能力声明，例如 `telegram_native_raw` |
| `agent_tools` | list | Agent 工具名、说明、object JSON Schema、`read_only` 与 `strict`；必须配合 `ai_agent` 权限和 `capabilities.agent_tools` |
| `agent_keywords` | list | 可选；暴露给系统助手时的路由关键词（最多 6 个） |
| `strict_trace` | bool | 是否要求路由投递层常驻全链路 trace；默认 `false`，资金类 / `payout` 插件建议开启 |
| `requires_platform_capabilities` | list | **新装/升级必填**（允许 `[]`），`plugin.json` 与 `Manifest` 同值；可选 `ai` / `interaction_bot` / `webhooks` / `ledger` / `dispatch_debug`。`Manifest` 0.72.0-beta.2 起接受该参数 |
| `usage` | str | 插件中心展示的详细使用说明；缺失会产生高级规范警告 |
| `allowed_hosts` | list | `external_http` 的域名白名单，支持 `example.com` / `*.example.com` / `**.example.com`；未填写时不注入 `ctx.http` |
| `http` | dict | `ctx.http` 扩展策略，目前为 `{"allow_direct": true}`（与账号 `network_mode="direct"` 双门禁才直连） |
| `config_actions` | list | 配置页动作按钮声明，点击后调用插件 `on_config_action`；也可写在 `config_schema["x-config-actions"]` |
| `interaction_profile` / `interaction_entries` / `command_fallback` / `preserve_command_trigger` | — | 旧交互入口迁移字段，见下文“旧交互 Bot 兼容声明” |
| `interaction_send_via` | list | 交互动作可选发送通道的声明性元数据（如 `interaction_bot`、`userbot_reply`）；平台负责执行和审计 |
| `experimental` | bool | 实验性插件；序列化为 `x-experimental`，前端显示风险标记 |

### 暴露工具给系统助手（System Agent）

插件自用的 `ctx.ai.run_agent()` 与系统助手工具注册表是两套路径。若希望**系统助手**也能调用插件工具，在对应 `agent_tools[]` 条目上增加：

```json
"expose": ["system_agent"]
```

约束（第一期）：

1. **只读**：`read_only` 必须为 `true`（或缺省）。写语义条目会被拒绝暴露，并在安装/刷新时写警告日志。
2. **命名**：系统助手侧工具名为 `plugin_{plugin_key}.{tool_name}`，例如 `plugin_demo_game.list_recent_rounds`。
3. **数量**：每个插件最多暴露 5 个工具。
4. **执行**：主进程经 worker IPC 调用；worker 侧需提供 handler：
   - 插件实例方法 `system_agent_{tool_name}(arguments, ctx)`，或
   - `system_agent_tool(name, arguments, ctx)`，或
   - `register_system_agent_tool_handler(plugin_key, name, handler)` 显式注册。
5. **安全**：调用前校验账号已启用该插件（`AccountFeature`）；结果文本经 `mark_external_text` 防注入；超时 10s 返回结构化错误且 `business_changed=false`。
6. **权限**：仍需 `permissions` 含 `ai_agent`，且 `capabilities.agent_tools.enabled=true`。

示例中的 `demo_game` 可暴露只读工具 `list_recent_rounds`（近期轮次摘要）；它只用于说明命名与契约，不代表 TelePilot 随包内置该插件。

### 完整示例

```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="my_plugin",
    display_name="我的插件",
    version="1.0.0",
    min_telepilot_version="0.72.0-beta.2",
    author="your_name",
    description="插件功能描述",
    category="interactive",
    requires_platform_capabilities=[],
    permissions=["send_message", "edit_message", "read_chat"],
    event_subscriptions=[
        {
            "events": ["message", "command", "callback_query"],
            "source": ["userbot", "interaction_bot"],
            "scope": "all_allowed_chats",
        }
    ],
    capabilities={
        "telegram_native_raw": {
            "enabled": False,
            "reason": "默认只读取标准事件信封。",
        }
    },
    strict_trace=False,
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
| `global` | 全局（所有账号共享） | `plugin_config` | API Key、通用参数等 |
| `account` | 单个账号 | 普通/`single` 模式写入 `account_feature.config`；`rules` 模式的每条规则写入 `rule.config` | 聊天 ID、行为开关等 |
| （不填） | 默认 account | 与 `account` 相同 | 向后兼容 |

**优先级：** 账号级配置 > 插件全局配置 > config_schema 中的 default

**敏感配置保存：** 密码、Token 等凭据落库后使用 `secret:v1:` 加密信封，读取接口返回遮罩。保存账号或全局配置时，省略已加密字段，或提交空字符串、`null`、`***`，均表示保留原凭据；提交新内容才会替换。该规则也适用于仅由 schema 标记为敏感的自定义字段名，普通非敏感字段仍遵循整体替换语义，省略即移除。

**前端渲染：** `config_schema["x-ui-mode"]` 决定插件配置入口：
- `rules` → 规则配置独立页，适合多条规则 CRUD 和 dry-run；不表示旧运行时规则驱动主路径
- `single` → 单配置对象独立配置页；没有专属页面的轻量插件也应按通用独立配置页处理
- `platform` → 平台基础能力页，不混在普通插件列表里
- `schema` → 兼容旧插件的别名；不再代表“Schema 弹窗”类，按通用单配置独立页读取字段
- `level: global` 的字段 → 全局配置区（所有账号共享）
- `level: account` 的字段 → 账号配置区（按账号隔离）
- 无 level 的字段 → 默认按账号隔离

#### 通用配置控件

通用独立配置页支持声明式控件，插件不要为了某个字段新增 TelePilot 前端特例。常用扩展字段：

| 声明 | 适用字段 | 效果 |
|------|----------|------|
| `x-ui-widget: "textarea"` | `string` | 多行文本 |
| `x-ui-widget: "llm-provider-select"` | `string` | 选择当前 TelePilot AI Provider |
| `x-ui-widget: "llm-model-select"` | `string` | 选择 Provider 下的模型；用 `x-ui-provider-field` 指向 Provider 字段 |
| `x-ui-widget: "dynamic-select"` | `string` | 从同一配置对象的动态选项数组中单选；用 `x-ui-options-field` 指向 `[{value,label}]` 字段 |
| `x-ui-widget: "multi-select"` | `array` + `items.enum` | 多选列表 |
| `x-ui-widget: "list-select"` | `string` + `enum` | 列表式单选 |
| `x-ui-widget: "config-list"` | `array` + `items.type="object"` | 多组配置行，支持添加、编辑、复制、删除、启停、排序 |
| `x-ui-widget: "allowed-peer-multi-select"` | `array` + `items.type="integer"` | 从当前账号“允许会话”中选择群聊/频道，保存为 Chat ID 数组；适合插件群聊白名单 |
| `x-ui-placeholder` | `string` / `textarea` | 输入框浅色占位内容；与实际 `default` 分离，适合展示内置提示词但让留空继续使用内置值 |
| `x-ui-hidden: true` | 任意字段 | 不在 UI 渲染，但仍保留在表单值和保存链路中 |

`config-list` 适合“多组配置，每组一行”的常见体验。支持这些元数据：

| 字段 | 说明 |
|------|------|
| `x-ui-summary` | 行摘要模板，支持 `{field}` 和 `{items.length}` 这类简单路径 |
| `x-ui-title-field` | 行标题字段，例如 `remark` / `title` / `name` |
| `x-ui-description-field` | 行描述字段，例如 `url` / `description` |
| `x-ui-enabled-field` | 启停开关字段，通常为 `enabled` |
| `x-ui-reorderable` | 是否允许拖拽和上下移动，默认允许 |
| `x-ui-add-label` | 添加按钮文案 |

示例：

```python
"knowledge_bases": {
    "type": "array",
    "title": "题库",
    "x-ui-widget": "config-list",
    "x-ui-summary": "{questions.length} 题 · {summary}",
    "x-ui-title-field": "title",
    "x-ui-description-field": "url",
    "x-ui-enabled-field": "enabled",
    "items": {
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean", "title": "启用", "default": True},
            "title": {"type": "string", "title": "标题"},
            "url": {"type": "string", "title": "URL"},
            "summary": {"type": "string", "title": "摘要", "x-ui-widget": "textarea"},
            "questions": {"type": "array", "title": "题目 JSON", "items": {"type": "object"}},
        },
    },
}
```

#### 配置页动作

插件可以在 `Manifest.config_actions` 或 `config_schema["x-config-actions"]` 声明配置页按钮。前端按 `placement` 放置按钮，当前推荐 `field:<字段名>`，例如 `field:knowledge_bases`。

```python
config_actions=[
    {
        "key": "generate_knowledge_base",
        "title": "获取并整理为题库",
        "placement": "field:knowledge_bases",
        "submit_label": "生成题库",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "title": "来源 URL"},
                "title": {"type": "string", "title": "标题提示（可选）"},
            },
            "required": ["url"],
        },
    }
]
```

后端会调用插件的 `on_config_action(ctx, action_key, payload)`。`ctx` 不带 Telegram client，但会按 manifest 权限注入受控 `ctx.http` 与 `ctx.ai`；`payload["input"]` 是按钮弹窗输入，`payload["config"]` 是当前表单配置。插件返回：

```python
return {
    "message": "已生成题库，请保存配置后生效。",
    "config_patch": {"knowledge_bases": next_items},
    "result": {},
}
```

后台配置动作完成后，`config_patch` 会自动写入配置并通知 worker 热加载；前端当前草稿若已有未保存修改会继续保留并提示远端配置已变化。通用 API 路径是：

```text
POST /api/accounts/{aid}/features/{key}/config/actions/{action_key}/jobs
GET  /api/plugin-config-action-jobs/{job_id}
POST /api/plugin-config-action-jobs/{job_id}/control  {"action":"pause"|"cancel"}
```

长时间动作支持真实中断和终止。`pause` 将任务标记为“已中断”，适合插件已经分批持久化阶段性结果后调整配置再重新执行；`cancel` 将任务标记为“已终止”。平台会取消当前进程中的在途异步调用，但“继续”的业务进度仍需插件自行持久化，不能只保存在插件实例内存中。

**字段验证清单（平台能力与仓库插件）：**

| 插件 | config_schema | UI 模式 | 状态 |
|------|--------------|---------|------|
| auto_reply | 规则通过 Rules API 管理 | `rules` | 仓库插件，按需安装 |
| autorepeat | ✅ trigger / repeat / chat 配置 | `rules` | 仓库插件，按需安装 |
| game24 | ✅ command, timeout | `single` | 仓库插件，按需安装 |
| math10 | ✅ Event Bus / prize；历史 `interaction_entries.start_math_game` 兼容 | `single` | 仓库插件，交互 Bot 可启动 |
| codex_image | ✅ command, access_token, model, message_template, image_size/aspect_ratio/image_format, timeout/status/output/instructions | `single` | 仓库图片插件，按需安装 |
| scheduler | ✅ default_notify, max_tasks | `platform` | 平台基础能力 |

新增第三方 Telegram 事件插件优先参考 `examples/plugins/event_bus_demo`；需要 HTTP 时参考 `examples/plugins/with_http`，需要 AI 文本能力时参考 `examples/plugins/with_ai`，需要把旧交互入口迁移到标准信封时参考 `examples/plugins/with_interaction`。曾经存在的 `translate` 示例依赖后端私有 LLM 链路，已从当前示例集移除；如需考古只查看 Git 历史，不要把它复原成可安装模板。

### Manifest 验证

远程插件安装/升级阶段只读 `plugin.json`，不会执行 Python（`remote_plugin_service._read_plugin_metadata` + `_validate_runtime_plugin_shape`）：

- `name` 或 `key` 必填其一，格式 `^[A-Za-z0-9_][A-Za-z0-9_-]*$`；`version` 必须以 `x.y.z` 开头；`author` 不超过 255 字符。
- `requires_platform_capabilities` 必须显式声明（允许 `[]`），值只能是 `ai` / `interaction_bot` / `webhooks` / `ledger` / `dispatch_debug`，缺失报 `PLATFORM_CAPABILITIES_REQUIRED`。
- `category` 只能是 `interactive` / `automation` / `utility`；`config_schema` 必须是合法的 JSON Schema（Draft 7）；`agent_tools` 的名称、参数 Schema、`read_only` / `strict` / `min_role` 会逐项校验，且必须同时声明 `capabilities.agent_tools` 与 `ai_agent` 权限。
- 目录必须同时包含 `plugin.json`、`manifest.py`、`plugin.py`、`__init__.py`；`entry` 只能是插件目录下的 `.py` 文件。
- 安装后还会做一次不阻断的静态 lint：缺少使用说明、硬编码命令前缀、直接 import `app.db` / `app.services`、`httpx` / `requests` 调用缺少 `timeout`、`event_subscriptions` 的未知事件/来源/范围/过滤键、`telegram_native_raw` 缺少 `reason` 等，结果在插件中心显示为规范警告。

运行期加载（worker）在执行 Python 前再读一次 `plugin.json`：目录名必须等于 `name/key`，`min_telepilot_version` 高于当前版本或 `requires_features` 缺失时直接拒绝加载并给出中文原因；导入后还要求 `PLUGIN_CLASS` 是 `Plugin` 子类、`MANIFEST` 是 `Manifest` 实例，且目录名、`MANIFEST.key`、`Plugin.key` 三者一致。

ZIP 上传还会在解压和加载 Python 前执行供应链门禁：

- 配置 `PLUGIN_PUBKEY` 后，签名缺失或验签失败都会拒绝安装。
- 未配置公钥时，新 ZIP 默认仍拒绝；只有管理员显式设置 `PLUGIN_ALLOW_NEW_UNSIGNED_PLUGINS=true` 才允许以 `trust_tier=community` 安装。
- `PLUGIN_ALLOW_LEGACY_UNSIGNED_PLUGINS` 只控制历史 `signature_ok=NULL` 插件能否继续加载，不影响新 ZIP 安装。

这两个未签名开关不能互相替代。兼容历史插件时可以保留 legacy 开关，但新安装入口仍应保持关闭；临时允许未签名安装后，应在完成安装后恢复为 `false`。

### 标准链路内部契约：Event Bus + Trace + MessageOps

UserBot 与 Interaction Bot 两条标准消息链路的内部链路是：

```text
Telegram 来源
  -> Source Adapter
  -> TelePilotEvent 标准事件信封
  -> Trace / Event Bus matcher
  -> 插件标准入口
  -> MessageOps/action
  -> Delivery Executor
```

开发新插件时先写 `plugin.json`：

```json
{
  "name": "event_bus_demo",
  "display_name": "Event Bus 示例",
  "version": "0.1.0",
  "min_telepilot_version": "0.72.0-beta.2",
  "category": "interactive",
  "permissions": ["send_message", "read_chat"],
  "requires_platform_capabilities": ["interaction_bot"],
  "usage": "启用后按 Event Bus 订阅处理 message/command/callback/inline/payment。",
  "event_subscriptions": [
    {"events": ["message", "command"], "source": ["userbot", "interaction_bot"], "scope": "all_allowed_chats"},
    {"events": ["callback_query"], "source": ["interaction_bot"], "scope": "all_allowed_chats"},
    {"events": ["inline_query", "chosen_inline_result"], "source": ["interaction_bot"], "scope": "inline_all"},
    {"events": ["payment_confirmed"], "source": ["external_payment_notice", "userbot"], "scope": "rule_bound"}
  ],
  "capabilities": {
    "telegram_native_raw": {
      "enabled": false,
      "reason": "默认只读取标准事件信封。"
    }
  }
}
```

`usage` 必须让开发者和安装者不用理解旧规则也能知道插件怎么启用。`event_subscriptions` 描述 Event Bus 投递范围；`capabilities` 描述高风险能力，没有高风险能力也建议显式写 `{}`。这组契约服务于标准消息链路，不是裸直通，也不是独立于三条链路之外的第四种模式。

自主发送的 Inline Keyboard 按钮不依赖交互 Bot 触发规则或活跃会话。插件只需订阅
`source=["interaction_bot"]`、`events=["callback_query"]`、`scope="all_allowed_chats"`，
并在 `on_event(ctx, payload)` 中处理回调；账号允许会话（会话白名单）留空时，`all_allowed_chats`
表示全部会话，交互规则的群范围不影响它。只有确实依附某条旧交互规则的按钮才使用 `rule_bound`。
新标准 `on_event` 订阅不要求 `entry_key`；`entry_key` 仅在需要回落到旧
`on_interaction(ctx, entry_key, payload)` 入口时必填。按钮必须由当前 Interaction
Bot 发送，Telegram 才会把点击更新投递给该 Bot。

### Inline 按钮的两种完全不同场景

“收到按钮回调”和“主动点击别的 Bot 的按钮”在 Telegram 协议里是两件事：

| 场景 | 谁发按钮 | 谁执行点击 | TelePilot 收到/发出什么 | 插件入口 |
| --- | --- | --- | --- | --- |
| Interaction Bot 按钮回调 | 当前 TelePilot Interaction Bot | Telegram 用户 | Interaction Bot 收到 `callback_query` update | `on_event` 处理 `callback_query`，用 `answer_callback` ACK |
| UserBot 主动点击第三方 Bot 按钮 | 第三方 Bot | TelePilot UserBot | UserBot 经 MTProto 发送 `GetBotCallbackAnswerRequest` | UserBot 执行链路调用 `ctx.messages.click_callback_button(...)` |

第一种场景的标准订阅如下：

```json
{
  "events": ["callback_query"],
  "source": ["interaction_bot"],
  "scope": "all_allowed_chats"
}
```

这不依赖旧 Interaction Bot 规则或活跃会话。插件用
`ctx.messages.answer_callback(...)` 或 `{"type": "answer_callback", ...}` 确认 Telegram
已经投递给当前 Interaction Bot 的 callback。`answer_callback` 不会、也不能让 UserBot
点击第三方 Bot 的按钮。

第二种场景使用平台正式 MessageOps 接口。安装插件的 manifest 必须声明：

```python
permissions = ["click_bot_button"]
```

legacy `on_message` 示例：

```python
from app.worker.plugins.base import Plugin, PluginContext, register


@register
class ThirdPartyBotButtonPlugin(Plugin):
    key = "third_party_bot_button"
    display_name = "第三方 Bot 按钮示例"

    # legacy on_message 默认 owner_only=True；要接收第三方 Bot 消息必须显式关闭。
    message_channels = {"incoming"}
    owner_only = False

    async def on_message(self, ctx: PluginContext, event) -> None:
        message = getattr(event, "message", event)
        sender_id = int(
            getattr(message, "sender_id", None)
            or getattr(event, "sender_id", 0)
            or 0
        )
        chat_id = int(
            getattr(message, "chat_id", None)
            or getattr(event, "chat_id", 0)
            or 0
        )

        # 必须同时限制发送 Bot 和会话；这里的 ID 由插件配置提供。
        if sender_id != int(ctx.config["target_bot_id"]):
            return
        if chat_id != int(ctx.config["target_chat_id"]):
            return

        await ctx.messages.click_callback_button(
            chat_id=chat_id,
            message_id=int(getattr(message, "id", 0) or 0),
            row=0,
            column=0,
            expected_bot_id=int(ctx.config["target_bot_id"]),
            expected_button_text="确认",
        )
```

要让这段逻辑收到消息，还必须满足：

- 插件已经在目标 UserBot 账号启用。
- 账号级“允许会话”留空，表示全部会话；列表非空时，目标会话必须在名单内。
- 不需要配置旧 Interaction Bot 触发规则。
- `owner_only=False` 只开放 legacy `on_message`，不会把第三方消息变成管理命令；UserBot outgoing 指令仍是独立安全边界。

`click_callback_button(...)` 属于 UserBot 执行链路能力：UserBot Event Bus、legacy
`on_message`、插件命令、裸直通和后台/调度任务的 `ctx.messages` 都可调用；Interaction
Bot 插件入口不支持，收到当前 Interaction Bot 的用户点击时必须使用 `answer_callback`。
实践中仍推荐在收到第三方 Bot 消息的处理函数内立即调用，并同时校验目标会话、Bot ID
和按钮文字，避免依赖已经变化的旧消息。

平台会重新读取指定 Telegram 消息，确认发送者确实为 Bot，定位对应行列，并且只接受
callback 按钮（Telethon 1.45+ 的 `KeyboardInlineButton` + `InlineButtonTypeCallback`）。
callback data 不向插件暴露，也不接受插件传入；推荐同时传
`expected_bot_id` 和 `expected_button_text`，防止消息或按钮在读取前被替换。平台统一负责：

- installed 插件的 `click_bot_button` 权限检查；
- `callback_query` 限流；如果限流要求等待，等待结束后重新读取并复核目标消息；
- Trace、ActionEvent 和 dev-mode dry-run；
- 15 秒消息读取/点击超时；
- 同账号、同消息、同行列的物理点击锁：明确成功后保护 20 秒，超时或结果未知时保守保护 5 分钟；
- Redis 不可用时 fail-closed，拒绝失去幂等保护的点击。

普通安装插件看到的 `message.buttons[row][column]` 和
`message.reply_markup.rows[row].buttons[column]` 都是平台只读视图，只暴露 `text`
与 `kind`，不会暴露原始按钮对象、client 或 callback data。旧
`message.buttons[row][column].click()`、`message.click()`、`get_buttons()` 等穿透调用
会被沙箱拒绝。

标准事件信封中的 `payload["message"]["reply_markup"]["buttons"]` 是扁平按钮数组，
每项只包含 `row`、兼容字段 `col`、正式字段 `column`、`text`、`kind`；转换成
`tp_event.message.reply_markup` 后得到 `ReplyMarkupRef`，其中每个 `ButtonRef` 只公开
`row`、`column`、`text`、`kind`。这两种标准投影都不公开 callback data 或 URL。

普通键盘、URL、Switch Inline、手机号、地理位置和其他非 callback 按钮都会被拒绝。

`strict_trace` 是布尔字段，默认 `false`。开启后，插件声明的路由投递层会更积极保留全链路 trace，便于复盘订阅匹配、插件执行、动作投递和失败原因；动作层 `record_action` 不受这个开关影响。涉及资金、发奖、`payout`、补偿重放或对账的插件建议设为 `true`，普通查询/娱乐插件可保持默认。

当前标准事件：

| event.type | 说明 |
| --- | --- |
| `message` | 普通消息，读取 `payload["message"]["text"]` |
| `command` | 管理员/授权用户命令，仍受 UserBot command 权限约束 |
| `callback_query` | 当前 Interaction Bot 收到的 Inline keyboard 按钮回调，用 `answer_callback` ACK；不是 UserBot 主动点击第三方 Bot 按钮 |
| `inline_query` | Inline 查询，用 `answer_inline_query` 返回结果 |
| `chosen_inline_result` | 用户选择了 Inline 结果，用于记录选择或后续结算 |
| `guest_message` | Bot API 10.0 访客模式：交互 Bot 在**未加入**的会话被 `@提及`/回复。需显式订阅，`message` / `all_events` 不会收到；只能用一次 `answer_guest_query`（或消息类动作，平台合并为一次回复） |
| `payment_confirmed` | 可信外部通知或平台解析确认到账后生成 |
| `message_edited` | 平台已登记的编辑后消息事件，需显式订阅 |
| `session_expired` | 会话 TTL 到期后由平台投递，供插件清理状态 |
| `webhook` | 外部 HTTP 入站 webhook，经账号 token 鉴权后投递 |
| `all_messages` | 兼容聚合订阅，当前仍只覆盖 `message` / `command` |
| `all_events` | 平台聚合订阅，覆盖当前已登记的常见事件类型 |
| `session_close` | 会话关闭或规则关闭，插件可清理状态 |

`all_messages` 目前仍只等于 `message` / `command`；需要覆盖更多已登记事件时，使用 `all_events`。Inline 相关事件、付款确认、`message_edited` 与 `session_expired` 仍可按需显式订阅。

`known_users` 只认平台 state 提供的真实集合，不会自动把当前 sender 算进去。

### 访客消息（Guest Mode）

Telegram Bot API 10.0 的访客模式让 Bot 在没有加入的群聊或私聊里被 `@提及`（或被回复）时收到 `guest_message` 更新，并且只能通过 `answerGuestQuery(guest_query_id, result)` 回复**一次**（结果是一个 `InlineQueryResult`，成功返回 `SentGuestMessage.inline_message_id`）。Bot 需要先在 BotFather Mini App 中启用 “Guest Mode”；平台可通过 `GET /api/accounts/{aid}/bot/capabilities` 读取 `getMe.supports_guest_queries`。

订阅写法：

```json
{"events": ["guest_message"], "source": ["interaction_bot"], "scope": "guest_all"}
```

`scope` 说明：`guest_all` 接收任意会话的访客消息（高风险，安装界面会提示）；`owner_only` / `known_users` 按调用者收窄；`all_allowed_chats` 仍按账号允许会话（会话白名单，留空 = 全部）判断，访客会话不在列表中会记录 `scope_not_matched`。

事件信封额外字段：

| 字段 | 说明 |
| --- | --- |
| `source.type` / `event_type` | `guest_message` |
| `source.guest_query_id` | 回答用的 `guest_query_id` |
| `message.text` | 已去掉开头/结尾的 `@xxx_bot` 提及；原文在 `guest.raw_text` |
| `chat` | 召唤所在的会话（Bot 不在其中，不能 `sendMessage`） |
| `reply_to` | 用户回复某条消息召唤时的被回复消息 |
| `guest` | `{"query_id", "chat_id", "chat_type", "caller_user_id", "raw_text", "reply_once": true, "reply_via": "answer_guest_query"}` |

回复方式：

- 直接返回 `send_message` / `send_rich_message` / `edit_message` / `delete_message`：平台写入一次性回复缓冲（合成 `message_id` 只在本次处理内有效），处理结束后合成一条 `InlineQueryResultArticle` 调一次 `answerGuestQuery`。多条消息以空行拼接；对占位消息的编辑会替换内容；超过 4096 字截断；HTML 解析失败退回纯文本；只保留 URL / 复制 / 切换 Inline 按钮。
- 返回 `answer_guest_query` 立即回答，之后的消息类动作记 `guest_reply_already_used` 跳过：

```python
return [{
    "type": "answer_guest_query",
    "guest_query_id": payload["guest"]["query_id"],  # 省略时取当前事件
    "text": "<b>答案</b>：42",
    "parse_mode": "html",           # 省略为纯文本
    # 或 "rich_message": {"markdown": "..."}
    # 或 "result": {完整 InlineQueryResult，缺 id 自动补}
}]
```

MessageOps 对应 `await ctx.messages.answer_guest_query(text=..., parse_mode="html")`。

先回占位、再编辑为最终答案（访客模式下的“思考中”提示）：

- `answer_guest_query` 动作可带 `save_message_id_key`，平台把 `answerGuestQuery` 返回的 `inline_message_id` 存到该键（2 小时）；实时 MessageOps 下可用 `await ctx.messages.read_saved_inline_message_id(key)` 读回。
- 之后返回 `{"type": "edit_message", "inline_message_id": "...", "text": "...", "parse_mode": "html"}` 编辑这条 inline 消息（只支持交互 Bot）。访客事件里带 `inline_message_id` 的编辑会直接执行，不进一次性回复缓冲。
- 0.106.0-beta.2+：`edit_message` 同时带 `inline_message_id` 与 `rich_message` 时，平台用 `editMessageText(inline_message_id, rich_message)` 以 Telegram 原生 Rich Message 编辑 inline 消息（Bot API：rich_message 同样适用于 inline 消息，但不能直传或按 URL 上传新文件），访客模式的“🤔”占位因此也能原生渲染表格、标题等。Rich 编辑失败如实记失败、不静默降级，插件读取批次结果后自行退回 HTML。实时 MessageOps 以类属性 `ctx.messages.inline_rich_edit = True` 声明该能力，插件可据此判断，旧平台没有该属性时保持 HTML 编辑。
- 0.106.0-beta.3+：上一条在 beta.2 只对“入口返回的动作”生效；插件在入口里用 `await ctx.messages.apply([...])` 实时编辑 inline 消息时，执行器仍要求 `chat_id` + `message_id`，请求未发到 Telegram 就以 `target_message_id_missing` 失败。beta.3 起实时 `apply` 同样支持 `inline_message_id` 编辑（`rich_message` 与 `text`/`parse_mode`，HTML 实体被拒时退回纯文本，只走交互 Bot），并以类属性 `ctx.messages.live_inline_edit = True` 声明；需要“🤔 占位后实时 Rich 编辑”的插件应以该属性为准。
- `edit_message` 可带 `send_on_edit_failure: true`：编辑失败时改为发送一条新消息，结果带 `edit_fallback_send: true`。

#### 访客回复上的 callback 按钮（0.106.0-beta.17+）

访客回复是 inline 消息：按钮被点击时 Telegram 只给 `callback_query.inline_message_id`、`chat_instance`、`data` 与点击者 `from`，**没有** `message` / `chat`。平台以类属性 `ctx.messages.guest_reply_markup = True`（缓冲与实时 MessageOps 都有）声明下面整套能力，插件据此判断，旧平台没有该属性时不要在访客回复里放 callback 按钮（会被剥掉，点击也不会投递）。

- **发出**：插件显式 `answer_guest_query`（`text` / `rich_message`（InputRichMessageContent）/ 完整 `result`）的 `reply_markup` 里 `callback_data` 按钮原样保留，HTML 被拒退回纯文本时同样保留；平台自己合成的访客回复（命令、AI 助手、规则的消息类动作合并）仍只保留 URL / 复制 / 切换 inline 按钮。之后用 `edit_message` 的 `inline_message_id` + `reply_markup`（文本或 `rich_message`）修改文字与按钮。
- **拿到 inline_message_id**：实时 `await ctx.messages.apply([...answer_guest_query...])` 的批次结果带 `inline_message_id`（最后一条）与 `inline_message_ids`（按动作顺序）；live facade 的 `await ctx.messages.answer_guest_query(...)` 返回的动作带 `inline_message_id`；`save_message_id_key` + `read_saved_inline_message_id(key)` 仍可用。
- **接收点击**：订阅 `{"events": ["callback_query"], "source": ["interaction_bot"], "scope": "guest_all"}`（`inline_all` 同样接收 inline 回调；`all_allowed_chats` 等按会话判断的范围收不到，因为没有 chat）。inline 回调不会匹配 `all_allowed_chats` 等按会话判断的 `callback_query` 订阅，所以同时声明两条订阅也不会重复投递（同一插件不要同时声明 `guest_all` 与 `inline_all` 的 `callback_query`）。payload：

| 字段 | 内容 |
| --- | --- |
| `event_type` / `source.type` | `callback_query` |
| `inline_message_id`、`source.inline_message_id` | 被点击的 inline 消息 |
| `callback_query_id` / `callback_data` | 同普通按钮 |
| `chat_id`、`message.chat_id`、`message.message_id`、`chat.id` | `null` |
| `sender.user_id` / `sender_user_id` | **点击者** |
| `inline_callback` | `{"inline_message_id", "chat_instance", "data", "clicker_user_id", "origin"}`；`origin` 是平台记下的原访客会话 `{"guest_query_id", "chat_id", "chat_type", "caller_user_id", "message_id"}`（answerGuestQuery 成功时按 Bot 记录 48 小时，Redis 不可用或记录过期时为 `null`）。`origin.caller_user_id` 是当初 @Bot 的访客，不是点击者，判断权限一律用 `sender.user_id`。 |
| `owner_user_ids` / `admin_user_ids` | 同其它交互 Bot 投递（账号本人 + sudo） |

  inline 回调不带 `guest` 信封（那是 `guest_message` 专用，插件常把 `guest.caller_user_id` 当发送者）。
- **响应点击**：`answer_callback`（实时或返回动作均可）；`edit_message` 带 `inline_message_id`（不带 `chat_id` / `message_id`）编辑文字与按钮，`rich_message` 同样支持。想判断群管理员时只能用 `origin.chat_id` 或插件自己记录的原会话，平台拿不到时应按“不是群管理员”处理。
- `send_message` / `edit_message` / `answer_guest_query` 使用 `parse_mode="html"` 时，Telegram 因实体解析失败拒绝（`can't parse entities` 等）会去掉标签以纯文本重试一次，插件不必自行兜底。

事件信封补充：交互 Bot 事件带 `bot`（`{"id", "username", "can_read_all_group_messages"}`，来自 `getMe` 短缓存，读取失败时缺省），插件可据此识别 @Bot / 回复 Bot，并在群隐私模式开启（`can_read_all_group_messages=false`，群里只能收到 @Bot、回复 Bot 与命令）时提示用户；`reply_to.media` 给出被回复消息的图片/文件元数据（含 `file_id`）。

### 处理时限

交互 Bot / Event Bus 投递给插件的单次处理默认 15 秒（主进程等待 worker 的 RPC 时限，worker 内同步以此为 deadline）。需要更久的插件（如先回占位再调用大模型、联网搜索）可在 manifest 中静态声明：

```python
event_subscriptions=[
    {"events": ["message"], "source": ["interaction_bot"], "scope": "all_allowed_chats", "timeout_seconds": 45},
]
# 旧 interaction_entries 入口同样支持：{"key": "chat", ..., "timeout_seconds": 45}
```

- 取值 1–90 秒，超过 90 按 90、非法值忽略（回到 15 秒）；交互入口的时限以主进程下发的 deadline 为准，不再受全局 `plugin_invoke_timeout_seconds`（默认 30 秒）截断。
- 时限只能静态声明；想让用户可配，请声明上限，并在插件内部按配置自行超时（例如把占位改为超时提示）。
- 长时限会占用该会话的处理并发，建议先回复占位让用户知道在处理。
- 声明时限 > 15 秒的入口在账号 worker 内走「长请求」通道（与游戏、按钮等短请求各自独立排队，0.106.0-beta.18 起）；同一会话的后续消息仍按到达顺序排在这条后面，其它会话不受影响。

#### 交互 Bot 事件的会话范围（`all_allowed_chats`）

“允许会话”指**账号的会话白名单**（Web 账号详情页「允许会话」、iOS 账号管理「会话白名单」，接口 `/accounts/{id}/ignored-peers`），与 UserBot 插件分发同一份名单、同一语义：

- 名单**留空 = 全部会话**；配置了名单时，`scope=all_allowed_chats` 的订阅只接收名单内会话的事件。
- **交互规则的群范围不参与**：促销、游戏等关键词规则 / 会话模块 / 支付规则各自配置的 `chat_ids` 只决定这些规则在哪些群生效，不会收窄（也不会放宽）直接订阅 Event Bus 的插件。新增、修改、停用或删除规则都不影响插件收到的事件。（0.106.0-beta.16 前，规则的群会被并进 Event Bus 的允许范围，导致把 Bot 拉进新群后 @Bot 无反应。）
- 名单外会话的事件由平台在路由阶段丢弃（插件收不到），记一条 info 日志（`account`、`chat_id`、`reason=chat_not_in_allowed_chats`；同一账号同一会话 10 分钟内只记一次，其余为 debug），开启路由 Trace 时每条都有 `subscription_match` / `chat_not_in_allowed_chats` span。
- 修改允许会话后下一条消息即按新名单判断（配置包导入等其他写入路径最多 30 秒缓存后生效）。
- 访客模式 `guest_all` 不受此限制；`owner_only` / `known_users` 订阅在名单外会话同样收不到（路由阶段已丢弃）；按钮回调（`callback_query`）不在路由阶段收窄，由订阅匹配按同一名单判断。

访客事件（`guest_message`）中 `payout`、`pin_message`、`answer_callback`、`answer_inline_query` 记 `guest_action_unsupported` 跳过（访客回复上按钮的点击是独立的 `callback_query` 事件，可以 `answer_callback`）；`send_photo` / `send_file` 只保留说明文字并记 `guest_media_unsupported`；所有发送都不会走 `userbot_reply`。相关 reason code：`guest_reply_buffered`、`guest_reply_already_used`、`guest_no_reply`、`guest_answer_failed`、`guest_rate_limited`、`guest_updates_disabled`。

### 入站 webhook 事件

第一次接入请先看 [入站 Webhook Quickstart](./PLUGIN-WEBHOOK-QUICKSTART.md)，可运行示例位于 `examples/plugins/webhook_receiver`。

插件可以通过 Event Bus 订阅外部 HTTP webhook。Manifest / `plugin.json` 写法如下：

```json
{
  "event_subscriptions": [
    {
      "source": ["webhook"],
      "events": ["webhook"],
      "scope": "all_allowed_chats",
      "filters": {"hook_key": "default"}
    }
  ]
}
```

`filters.hook_key` 可写单个字符串，`filters.hook_keys` 可写多个 key。也可以使用 trigger 简写：

```json
{
  "event_subscriptions": [
    {
      "source": ["webhook"],
      "events": ["webhook"],
      "scope": "all_allowed_chats",
      "triggers": {"webhook": "default"}
    }
  ]
}
```

`triggers.webhook` 会被归一化为 `filters.hook_key`；对象形式也支持 `{"hook_key": "orders"}`、`{"hook_keys": ["orders", "billing"]}`、`{"key": "orders"}` 或 `{"keys": [...]}`。Webhook 事件的 `scope="all_allowed_chats"` 会直接通过 scope 检查，因为外部 HTTP 请求没有 Telegram `chat_id`。

插件收到的标准 payload 包含：

```json
{
  "event_type": "webhook",
  "source": {
    "type": "webhook",
    "channel": "webhook",
    "driver": "http_webhook",
    "hook_key": "default"
  },
  "trigger": {"hook_key": "default"},
  "message": {"text": "{\"order_id\":\"A-1\"}"},
  "webhook": {
    "hook_key": "default",
    "body": {"order_id": "A-1"},
    "headers": {"content-type": "application/json"},
    "body_size": 19,
    "content_type": "application/json",
    "received_at": "2026-07-10T00:00:00+00:00"
  },
  "hook_key": "default",
  "body": {"order_id": "A-1"},
  "headers": {"content-type": "application/json"}
}
```

`body` 会按 `Content-Type` 尝试解析 JSON；JSON 解析失败或非 JSON 请求会保留为文本。`message.text` 是 body 的文本摘要，最长 2000 字符。headers 只保留白名单字段，并统一小写，单个值最多 512 字符；当前白名单为 `content-type`、`user-agent`、`x-request-id`、`x-correlation-id`、`x-github-event`、`x-gitlab-event`、`x-hub-signature`、`x-hub-signature-256`、`x-signature`、`x-telegram-bot-api-secret-token`。账号 token 头不会进入插件 payload。

外部触发方式：

```bash
curl -X POST "https://<telepilot-host>/api/webhooks/{account_id}/{hook_key}" \
  -H "X-TelePilot-Webhook-Token: <account_webhook_token>" \
  -H "Content-Type: application/json" \
  --data '{"order_id":"A-1","status":"paid"}'
```

默认只接受 `X-TelePilot-Webhook-Token` 请求头。旧调用方只有在服务端显式设置 `WEBHOOK_ALLOW_QUERY_TOKEN=true` 时才能继续使用 `?token=<account_webhook_token>`；查询参数可能进入 URL、反代和访问日志，生产环境不建议开启。`hook_key` 必须是 1-64 位的字母、数字、下划线、点或短横线组合，且必须存在于账号 webhook 配置并处于启用状态；当前页面保证提供 `default`，插件声明不会自动创建其他 Hook key。请求体上限为 64 KiB；投递接口返回 `202` 只表示 worker 已确认接收，插件是否命中和执行结果请在日志/trace 中查看。Webhook 投递受 `webhook_deliver` 风控动作限流，Worker 暂停或全局总闸开启时拒绝投递。

标准事件信封优先读这些字段：`source`、`message`、`chat`、`sender`、`actor`、`source_actor`、`player`、`payment`、`reply_to`、`trigger`、`session`、`native_raw_meta`。新插件不要依赖 `payload["text"]`、`payload["chat_id"]`、`payload.get("message")` 这类旧平铺字段；`payload["message"]` 是消息对象，不是配置字符串。

重点补充字段：

- `payload["tp_event"]`：进程内调用时已挂好的 `TelePilotEvent` 投影对象；跨 IPC 路径平台会重建同形对象。
- `message.entities` / `message.media` / `message.date`：统一消息摘要，编辑消息和媒体消息也走同一套字段。
- `message.rich_message` / `message.text_source`：Userbot 收到 Layer 228+ Rich Message 时保留规范化 blocks；普通文本为空时，`message.text` 使用纯文本 fallback，并标记 `text_source="rich_message_fallback"`。
- `chat.title` / `chat.username`：群标题、username 等可直接读取，不必回头翻 `native_raw`。
- `session.channel` / `session.expires_at` / `session.data`：当前会话通道、超时点和持久化状态。
- `source.synthetic`：平台合成事件来源，例如 userbot 文本按钮降级后的 `text_button`。

`tp_event` 对象字段主看：

| 字段 | 说明 |
| --- | --- |
| `tp_event.type` | 事件类型，等价于 `payload["source"]["type"]` |
| `tp_event.source_channel` | 当前事件来源通道 |
| `tp_event.message` | `MessageRef`，含 `text/caption/date/entities/media/forward/sender_chat/edited` |
| `tp_event.callback` | `CallbackRef`，按钮事件时可读 `id/data` |
| `tp_event.payment` | `PaymentRef`，付款确认场景可读金额、币种、payer/receiver |
| `tp_event.session` | `SessionRef`，可读 `key/scope/channel/data` |
| `tp_event.trigger` | 命中的规则、入口和触发参数 |

`capabilities.telegram_native_raw` 只用于排障。声明 `enabled=true` 时必须写 `reason` 和 `sources`，插件仍要先检查：

```python
native_raw_meta = payload.get("native_raw_meta") or {}
if not native_raw_meta.get("enabled"):
    # 降级到标准事件信封。
    pass
```

不要读取旧 `raw_event`。它只能作为迁移风险或回归测试名出现。

MessageOps/action 示例：

```python
event = event_from_interaction_payload(payload)
return [
    {
        "type": "send_message",
        "chat_id": event.message.chat_id,
        "reply_to_message_id": event.message.message_id,
        "text": f"收到：{event.message.text}",
    }
]
```

默认 `send_message` 类动作按 `parse_mode="plain"` 发送；只有显式写 `parse_mode="html"` 时才启用 HTML。HTML 内容应先用 `app.worker.plugins.textutil.html_escape()` 或等价工具转义，再把标签手动拼好。

#### 原生 Rich Message

需要标题、任务列表、折叠详情、表格等 Telegram 原生结构时，使用 Bot API Rich Message，而不是把普通 `send_message` 的 HTML 当成同一种格式：

```python
await ctx.messages.send_rich(
    chat_id=event.message.chat_id,
    html=(
        "<h1>巡检结果</h1>"
        "<ul>"
        '<li><input type="checkbox" checked>数据库正常</li>'
        '<li><input type="checkbox">等待人工复核</li>'
        "</ul>"
        "<details><summary>失败详情</summary><p>上游返回 429</p></details>"
        "<table bordered striped>"
        "<tr><th>模型</th><th>状态</th></tr>"
        "<tr><td>grok-4</td><td>正常</td></tr>"
        "</table>"
    ),
    reply_to_message_id=event.message.message_id,
    save_message_id_key="latest_health_report",
)
```

`ctx.messages.send_rich()` 必须且只能提供 `html`、`markdown`、`blocks` 其中一个；也可附带 `media`、`is_rtl`、`skip_entity_detection`、`reply_markup`、`save_message_id_key` 和 `pin`。动态文本嵌入 `html` 前仍要转义。对应标准 action 为：

```python
return [{
    "type": "send_rich_message",
    "chat_id": event.message.chat_id,
    "rich_message": {
        "markdown": "# 巡检结果\n\n- [x] 数据库正常\n- [ ] 等待人工复核",
    },
}]
```

Rich Message 默认通过 `interaction_bot` 执行：即使当前是 UserBot 命令会话，省略通道也会切到 Interaction Bot。显式指定 `userbot_reply` 时使用 Telethon Layer 228+；主号必须具备 Telegram Premium 且 Telegram app config 的 `rich_message_posting` 可用，当前接受 HTML、Markdown 和可无损转换的纯文本 blocks，不支持媒体型/复杂 blocks、media 或 Bot `reply_markup`。两条路径都不做静默降级。Telegram 原生限制包括 32768 文本单位、500 个结构块、16 层嵌套、50 个媒体附件和表格最多 20 列；TelePilot 的 Userbot 适配按 32768 UTF-8 bytes 检查，Bot 通道保持 32768 字符计量。最终格式解析仍以 [Telegram Rich Message 文档](https://core.telegram.org/bots/api#rich-message-formatting-options) 为准。

##### 表格默认样式：边框 + 隔行着色（TelePilot 0.106.0-beta.15+）

平台发出的所有 Rich Message 表格默认**带边框并隔行着色**（Bot API `is_bordered` / `is_striped`，Rich HTML `bordered` / `striped`）。在发送出口统一处理，覆盖 `send_rich` / `edit_rich` / `answer_guest_query`、对应标准动作、Rich 草稿流（`sendRichMessageDraft`）、inline 编辑，以及 Interaction Bot 与 Userbot 两条通道：

- `markdown`：GFM 管道表格（`| a | b |` + 分隔行）一律转成 `<table bordered striped>` HTML 块（开 `compact_tables` 时为 `<table bordered striped compact>`）；表头行用 `th`，`:---` / `---:` / `:---:` 转为 `align`，单元格内联格式转为 HTML，代码块内不处理。作者在 Markdown 里直接写的 `<table>` 按下面 HTML 规则处理。转换后若超过 32768 字符上限，则按原文发送。
- `html`：`<table>` 缺哪个属性补哪个（`<table>` → `<table bordered striped>`，`<table bordered>` → `<table bordered striped>`）；已写的原样保留。HTML 布尔属性无法表达“关”，因此 `bordered="false"` / `striped="0"`（值为 `false` / `0` / `no` / `off`）视为调用方显式关闭，平台发送前去掉该属性。
- `blocks`：`{"type": "table"}` 缺 `is_bordered` / `is_striped` 时补 `true`；调用方显式写了（包括 `false`）就原样保留。
- 想要无边框或不着色的表格：blocks 写 `"is_bordered": false` / `"is_striped": false`，HTML/Markdown 内写 `<table bordered="false" striped="false">`。
- 兼容性：官方 Bot API changelog 中 Rich Message 与 `RichBlockTable` 在 10.1（2026-06-11）引入，10.3 只给表格新增了 `is_compact`，所以 `is_bordered` / `is_striped` 是任何支持 Rich Message 的 Bot API / Telegram layer 都认识的字段，不需要降级重试；不带紧凑的请求被拒时照常报错、不重发。紧凑被旧 Bot API 拒绝的降级重发只去掉 `compact`，边框与隔行着色保留。
- 能力判断：0.106.0-beta.15+ 的 `ctx.messages` 带类属性 `rich_table_default_style = True`。插件无需任何改动；需要在旧平台上也带样式时，自己写 `<table bordered striped>` 即可（新平台不会重复添加）。

##### 紧凑表格（Bot API 10.3，TelePilot 0.106.0-beta.12+）

Telegram Bot API 10.3 给 `InputRichBlockTable` / `RichBlockTable` 增加了 `is_compact`（类型 True，可选，“单元格内边距更小”），Rich HTML 对应 `<table compact>`。TelePilot 的支持方式：

- 显式写法原样透传：`blocks` 里的 `{"type": "table", "is_compact": true, ...}`、`html` 里的 `<table bordered compact>`；`is_compact` 必须是布尔值。收到的 `RichBlockTable.is_compact` 也原样保留在事件里。
- 一键开关：`ctx.messages.send_rich(..., compact_tables=True)`、`edit_rich(..., compact_tables=True)`、`answer_guest_query(rich_message=..., compact_tables=True)`，或在标准动作（`send_rich_message`、带 `rich_message` 的 `edit_message`、`answer_guest_query`）上写 `"compact_tables": true`。平台把消息里所有表格改为紧凑：`blocks` 加 `is_compact: true`，`html` 的 `<table>` 加 `compact`，`markdown` 的管道表格（`| a | b |` + 分隔行）转成 `<table bordered striped compact>` HTML 块（0.106.0-beta.15 起带隔行着色，beta.13/14 为 `<table bordered compact>`；0.106.0-beta.13 起保留边框：管道表格本来带边框显示，而 `compact` 只缩小单元格内边距、不包含边框；表头行用 `th`，`:---` / `---:` / `:---:` 对齐转为 `align`），单元格里的加粗、斜体、删除线、行内代码、链接转为对应 HTML（官方说明 Markdown 不在块级 HTML 标签内解析）；代码块里的内容不处理。该字段只由平台消费，不会发给 Telegram；不写或写 `false` 时不加紧凑（表格仍按默认样式带边框 + 隔行着色），平台不会发送 `is_compact: false`。
- 边框与紧凑是两个独立标志：Bot API 的 `is_compact` / `compact` 只缩小单元格内边距，边框由 `is_bordered` / `bordered` 控制（官方示例 `<table bordered striped compact>`）。`compact_tables` 本身只加紧凑标志；0.106.0-beta.15 起边框与隔行着色由上面的“表格默认样式”在发送时统一补上（调用方显式值优先），0.106.0-beta.13/14 上 `blocks` 与 `html` 里没写边框的表格开启紧凑后仍无边框。旧 Bot API 拒绝时去掉紧凑重发，边框与隔行着色照旧保留。
- 能力判断：0.106.0-beta.12+ 的 `ctx.messages` 带类属性 `rich_table_compact = True`。插件应先 `getattr(ctx.messages, "rich_table_compact", False) is True` 再加 `compact_tables`；旧平台没有该属性，直接按普通表格发送即可。
- **声明式写法 `render_profile`（0.109.0-beta.1+，能力 `messages.render_profile`）**：在单条动作或 `plugin.json` 顶层写
  `"render_profile": {"table": {"compact": true, "bordered": true, "striped": true}}`，三项都可省略（省略 = 平台默认：不紧凑、带边框、带隔行着色）。
  `compact_tables: true` 就是 `{"table": {"compact": true}}` 的旧写法，两者效果逐字节一致。优先级：`plugin.json` < 动作 < 中间件改写后的动作。
  `bordered: false` / `striped: false` 关闭对应样式（blocks 写 `is_bordered: false`；HTML / Markdown 表格不带该属性）；调用方在表格上写的显式值仍然优先。
  `render_profile` 由平台中间件 `core.render_profile` / `core.rich_compact` 消费，不发给 Telegram；表格默认样式由发送出口的 `core.rich_table_defaults` 统一补，见 [消息管线中间件](#消息管线中间件middleware)。
- 安全回退：Bot API 服务器（自建或未升级到 10.3）以 `Bad Request` 明确拒绝带紧凑表格的请求时，平台去掉紧凑标记重发一次并写日志；Userbot 路径收到 400 RPC 错误同样降级一次。只有确认请求未生效才降级，网络错误、超时、5xx、限流与“消息未修改”不会触发重发。

```python
compact = getattr(ctx.messages, "rich_table_compact", False) is True
await ctx.messages.send_rich(
    chat_id=event.message.chat_id,
    markdown="| 标题 | 大小 | 发布时间 |\n|---|---|---|\n| 示例 | 1.2 GB | 2026-10-28 |",
    **({"compact_tables": True} if compact else {}),
)
```

插件 action 的失败语义保持严格，不会自动改成普通消息。只有 TelePilot 自己拥有的账号 Bot 移动控制页和系统告警采用“同一个 Bot 先发 Rich Message，Bot API 拒绝时回退原有 HTML”的兼容策略；这个内部回退不会切换到 UserBot，也不会改变插件 action 的契约。

会话内状态更新示例：

```python
return [{
    "type": "update_session",
    "data": {
        "answer": "42",
        "attempts": 3,
    },
}]
```

`update_session` 会回写当前 `session.data`，并按平台规则续租 Redis TTL；除非额外声明 `extend_seconds`，它不会偷偷改掉原来的 `expires_at`。

免费答题、抽奖、按钮加入、互动游戏这类“不想让玩家为了发奖额外刷消息”的玩法，推荐让玩家点击 inline 按钮加入或确认参与。按钮回调里的点击者在 `payload["sender"]["user_id"]`；插件仍可按自身玩法保存完整业务状态，仅从后续发奖锚点角度，至少保留这个 `tgid` 即可。发奖时交给平台用 `reply_to_user_id` 在当前群查找该玩家最近一次发言作为回复锚点，不需要插件自己遍历群消息。

如果还在用显式 `userbot_reply` 发奖消息，可以这样写：

```python
event = event_from_interaction_payload(payload)
winner_user_id = event.sender.user_id

return [
    {
        "type": "send_message",
        "send_via": "userbot_reply",
        "chat_id": event.message.chat_id,
        "reply_to_user_id": winner_user_id,
        "reply_to_search_limit": 2000,
        "reply_anchor_missing_text": "未找到对应用户（{user_id}）的近期消息，本次发奖需要人工补发。",
        "text": "+88",
        "settlement": {
            "mode": "auto",
            "amount": 88,
            "winner_user_id": winner_user_id,
            "winner_name": event.sender.display_name,
            "status": "announced",
        },
    }
]
```

平台会先读取账号 UserBot 在当前群为该真实用户保存的近期消息锚点，缓存未命中时再使用 Telegram `from_user` 精确搜索，最后最多扫描 2000 条近期消息；找到后把 `+88` 回复到那条消息。缓存键按账号、群和用户隔离，只接受 Telegram 消息自身的 `PeerUser`，匿名管理员、频道身份和按钮 callback 不会反向生成真实用户锚点。若插件已经有明确的 `reply_to_message_id`，平台优先使用它；若只给了 `settlement.winner_user_id` 而没写 `reply_to_user_id`，平台也会尝试用赢家 user id 作为回复锚点。找不到近期消息时，本次 `userbot_reply` 会失败并记录 action 错误，避免把发奖消息误发成普通群消息；平台默认会在群里提示 `未找到对应用户（用户 ID）的近期消息。`，插件可通过 `reply_anchor_missing_text` 自定义失败提示，提示文案支持 `{user_id}` 占位符，并沿用当前 action 的 `parse_mode`。例如在 HTML 模式中使用 `<code>/command</code>`，Telegram 会将命令显示为可点击复制的代码文本。

更推荐的新写法是直接返回 `payout`：

```python
event = event_from_interaction_payload(payload)
winner_user_id = event.sender.user_id
identity = await resolve_public_sender_identity(
    ctx,
    chat_id=event.message.chat_id,
    user_id=winner_user_id,
    fallback_display_name=event.sender.display_name,
)

return [{
    "type": "payout",
    "chat_id": event.message.chat_id,
    "amount": 88,
    "reply_to_user_id": winner_user_id,
    "reply_to_display_name": identity.display_name,
    "reply_to_username": None if identity.is_anonymous_admin else event.sender.username,
    "reply_to_search_limit": 2000,
    "reply_anchor_missing_text": "未找到对应用户（{user_id}）的近期消息，本次发奖需要人工补发。",
}]
```

`reply_to_display_name` 必须来自上文的 `resolve_public_sender_identity()` / `resolve_public_sender_identities()`，不能直接复制按钮回调中的姓名；匿名管理员应同时把 `reply_to_username` 设为 `None`。平台会在 UserBot 发奖消息发送后先保存这组安全公开身份，再完成 payout 账本，供后续转账通知复用。历史动作未提供公开名、映射尚未写入或 Redis 暂时不可用时，平台会再用 Interaction Bot 的官方 `getChatMember` 核验；无法确认时隐藏为“匿名用户”，不会回退回复消息里的真实姓名。

`payout` 永远经 userbot 执行，并进入限速与 trace。已安装插件必须在 Manifest 的 `permissions` 中显式声明 `payout`，否则运行时以 `error_code=permission_denied` 拒绝动作；涉及资金的插件同时建议开启 `strict_trace=true`。它适合“发奖文案本身就是协议”的玩法，普通 Bot 不会代替它执行转账样动作。找不到 `reply_to_user_id` 对应的近期发言时，`payout` 同样会失败、写入日志，并发送默认或自定义的 `reply_anchor_missing_text` 提示。

`payout` 的额度校验与失败补偿由平台负责，插件不用自己实现，但要理解它对 trace 的影响：

- **超限直接拒**：账号可在「系统设置 → 风控与预算」配置 `payout` 的单笔上限与日累计上限（默认 `0` = 不限，按业务币种 / 积分的整数口径填写）。本笔金额超单笔上限、或“今日已累计 + 本笔”超日累计上限时，`payout` 被拒绝执行，action 记 `FAILED`、`error_code=payout_limit_exceeded`。超限属配置拒绝，**不进补偿队列、也不自动重试**；需要人工调额度，日累计上限跨 UTC 零点自动重置。
- **瞬时失败自动补偿**：userbot 离线（`userbot_offline`）、Telegram API 报错（`telegram_api_error`）、命中限速（`rate_limited`）这类可恢复失败，平台会把这一笔 `payout` 写进补偿队列并按退避自动重放。插件侧看到的仍是本次的 `FAILED` action（detail 带 `compensation_queued=true` 和 `payout_key`）；补发成功后会**以一条新的 `OK` action 出现**（同 `payout_key`、带 `replay` 标记）。**插件不要自己重试 `payout`**——补发交给平台，重复返回只会靠幂等去重。
- **补发幂等**：平台以数据库中的 `payout_key` intent / claim / sent 状态作为持久化幂等边界，发送完成状态与 ActionEvent 在同一事务提交。只有 Telegram 明确拒绝发送时才释放 claim；超时、断连或未知异常会进入 ambiguous，不会假定“异常等于未发送”。
- **暧昧送达核对**：只有 payload 带稳定 `payout_probe_fingerprint` 时，worker 才会回查账号最近自己发言并自动确认送达。旧记录或缺少 fingerprint 的记录需要人工核对，平台不会只凭相同文本和回复锚点认定已发送。插件若要把多次调用视为同一笔，应提供稳定 `payout_key`，不要自行重试。
- **彻底放弃会告警**：重放次数耗尽或遇到不可补偿错误时，补偿单置为 `abandoned` 并写一条 error 级运维日志，供“收款成功但发奖失败”场景人工介入。运维侧配置与巡检见 [SECURITY-OPS](./SECURITY-OPS.md) §7。

按钮回调：

```python
return [{
    "type": "answer_callback",
    "callback_query_id": payload["source"]["callback_query_id"],
    "text": "按钮已收到",
    "show_alert": False,
}]
```

Inline：

```python
return [{
    "type": "answer_inline_query",
    "inline_query_id": payload["inline_query"]["id"],
    "results": [{
        "type": "article",
        "id": "demo",
        "title": "示例结果",
        "input_message_content": {"message_text": "Inline 示例"},
    }],
    "cache_time": 0,
    "is_personal": True,
}]
```

付款确认：

```python
return [{
    "type": "settlement",
    "mode": "confirm_only",
    "payer_user_id": payload["payment"]["payer"]["user_id"],
    "amount": payload["payment"]["amount"],
    "currency": payload["payment"]["currency"],
    "status": "confirmed",
}]
```

`notice` / `bbot_notice` / `notice_bot` 不再是可执行发送通道，只能出现在迁移说明或故意回归测试里。普通 Bot 不执行转账、催付或发奖；钱相关动作应交给 `settlement`、`userbot_reply` 或平台受控结算链路。

userbot 会话里的 `reply_markup` 不会直接丢掉：

- 平台会把按钮渲染成文本编号列表。
- 玩家回复序号或按钮文案后，平台会合成 `callback_query` 回投插件。
- 这类事件的 `callback_query_id` 可能为空，同时 `source.synthetic="text_button"`。
- 对合成 callback 返回 `answer_callback` 时，平台会按“已合成事件”跳过真正的 Bot API ACK。

入口控制字段：

| 字段 | 位置 | 作用 |
| --- | --- | --- |
| `interaction_trigger_modes` | 平台注入的配置字段 | `all` / `keyword_only`，控制声明了 command trigger 的入口是否仍注册命令 |
| `default_trigger_modes` | manifest entry | 为 `interaction_trigger_modes` 提供默认值，强按钮玩法建议设为 `keyword_only` |
| `callback_fast_ack` | manifest entry | callback 分类后立即空 ACK，插件晚到的 `answer_callback` 会记为 `already_acked` |

`callback_fast_ack` 只适合“先消除按钮转圈，再慢慢算结果”的入口；启用后不要再依赖 `show_alert=True` 的晚返回提示。

常见 `reason_code` 排障表：

| reason_code | 说明 |
| --- | --- |
| `matched` | Event Bus 订阅命中，准备投递 |
| `subscription_not_matched` / `event_type_not_subscribed` / `source_not_subscribed` | 没有订阅命中、事件类型未订阅或来源未订阅 |
| `scope_not_matched` / `filter_not_matched` | 允许会话、owner_only、inline_all、guest_all 等范围不匹配，或关键词、金额、callback data 等过滤不匹配 |
| `chat_not_in_allowed_chats` | 交互 Bot 事件所在会话不在账号「允许会话」（会话白名单）中，路由阶段未投递给直接订阅 Event Bus 的插件；把群加入允许会话或清空列表即可 |
| `plugin_disabled` / `plugin_load_failed` / `plugin_runtime_error` | 插件未启用、加载失败或运行异常 |
| `entry_key_missing` | 订阅未声明 `entry_key`，且插件也没有实现标准 `on_event` 入口 |
| `command_matched` / `command_not_matched` / `command_unauthorized` | 管理员命令命中、普通文本未命中命令、权限不足 |
| `event_bus_delivery_disabled` / `inline_disabled` | 运维回滚开关关闭 Event Bus 新投递路径或 Inline updates |
| `native_raw_not_allowed` / `native_raw_skipped` | 插件未声明 `telegram_native_raw` 或本次因来源、大小、设置未下发 |
| `contract_warning` / `contract_failed` | 插件越声明调用被告警放行，或请求客观不可执行能力 |
| `action_limit_exceeded` | 插件返回的动作超出平台允许数量，后续动作被截断并写入可见告警 |
| `send_channel_deprecated` / `unsupported_send_via` | 请求旧 `notice` 通道或未知通道 |
| `premium_required` / `rich_message_posting_disabled` / `rich_message_capability_unknown` / `rich_message_blocks_unsupported` / `rich_message_media_unsupported` / `invalid_rich_message` | Userbot Rich Message 能力不足或无法确认、输入超出当前适配范围、内容结构未通过校验 |
| `bot_not_configured` / `bot_token_missing` / `userbot_offline` | 交互 Bot 未配置、Bot token 缺失或 UserBot 离线 |
| `settlement_requires_userbot` / `telegram_api_error` | 普通 Bot 请求钱相关能力，或 Telegram API 返回失败 |
| `trace_write_failed` | Trace 写库失败，平台已降级写入旧 runtime log |

运行阶段 loader 会 import `__init__.py`，并检查：

- `PLUGIN_CLASS` 是 `Plugin` 子类
- `MANIFEST` 是 `Manifest` 实例
- `MANIFEST.key` 与插件 key / 目录名保持一致

### 旧交互 Bot 兼容声明（interaction entries，仅迁移）

本节只用于迁移历史 `interaction_entries` / `on_interaction` 插件。新插件不要把旧交互规则、旧平铺 payload 或旧入口声明当主路径；请优先使用上一节的 `usage`、`event_subscriptions`、`capabilities` 和标准 action。

迁移旧插件时，可以暂时保留 `manifest.py` 顶层的 `interaction_entries`，但必须同时补齐 `event_subscriptions`。旧 `config_schema["x-category"]` 与 `config_schema["x-interaction-entries"]` 仅作为兼容入口，新插件不要再新增。

### 双通道与外部转账证据

当前标准模式只有两个主动发送通道：交互 Bot 负责承接高频互动、按钮和会话提示，`UserBot` 负责管理员命令、账号身份动作、收款确认和发奖。群里已有的转账结果通知 Bot 只属于外部付款证据来源；TelePilot 监听它的到账消息并生成 `payment_confirmed`，但不会把它作为插件主动发送通道。

`outgoing` 的频率控制，指的是当前 `UserBot` 账号自己发出的消息要保持低频、可解释、可回溯，避免把大量游戏交互都压回账号本身。`incoming` 订阅则只表示这个账号愿意看见哪些外部消息，用来做公告监听、状态同步和必要的自动回复，不表示这些消息都能直接触发指令，更不表示可以绕过风控做批量互动。

原则上，交互 Bot 不碰钱，只负责题面、答复、结果提示、按钮和规则事件；真正的奖金发放仍由 `UserBot` 或平台受控结算链路完成。插件如果要做发奖、结算或红包类动作，也应把钱相关动作留在 `UserBot` 侧，不要把转账、发奖、催付这些动作混进交互 Bot 的高频入口里。

插件分类只保留三类，前端会按中文分组展示：

| category | 中文分组 | 适用插件 |
| --- | --- | --- |
| `interactive` | 互动娱乐 | 游戏、群内娱乐、需要交互 Bot 承接高频消息的插件 |
| `automation` | 自动化 | 自动回复、转发、定时任务等账号自动化能力 |
| `utility` | 工具能力 | AI、媒体生成、查询、辅助工具等能力 |

`category` 只决定展示分组；标准会话事件投递看 `event_subscriptions`。`interaction_entries` 只用于旧交互中心规则迁移和入口参数兼容。

注意：`interaction_entries` 只负责“让前端知道这个插件有哪些交互入口可选”。真正运行时，worker 会调用插件实例的 `on_interaction(ctx, entry_key, payload)`。如果插件只声明入口但没有实现这个 hook，交互 Bot 会提示“插件尚未实现交互入口”。

Interaction Bot 运行时采用事件路由模型：普通 Bot 负责接收群消息、按钮回调和规则指令；UserBot/回复上下文与外部转账通知来源负责补充付款证据。新标准插件可以通过 `event_subscriptions` 自主订阅，并以 `all_allowed_chats` 接收账号允许会话（会话白名单，留空 = 全部；不受交互规则群范围影响）内的事件，不要求先命中旧规则或存在活跃会话；平台仍会按 source、event、scope、filters 做匹配，不会无条件广播给所有插件。旧 `interaction_entries` / `on_interaction` 兼容路径才依赖规则匹配及相应会话上下文。

交互入口是新增触发面，不是命令系统的替代品。插件原有 `commands`、`on_command`、`message_channels` 和 `on_message` 语义必须保持不变；任何新入口都不得让普通 incoming 消息绕过 UserBot outgoing 指令边界。需要复用能力时，把业务逻辑抽成共享函数，由 UserBot 命令和交互入口分别调用。

#### 平台、规则、插件的职责边界

交互 Bot 的核心设计是“触发器和业务分离”。开发插件时先按下面的边界判断代码应该放在哪里：

| 层级 | 负责 | 不负责 |
| --- | --- | --- |
| 自动回复 | 轻量关键词/变量触发，把消息转换成普通回复或白名单命令 | 不承载复杂业务状态，不直接实现插件业务 |
| 交互 Bot 规则 | 匹配群、关键词、转账通知、金额/收款人过滤、每用户冷却、每日次数、开关命令、会话路由 | 不生成题目、不查询 PT、不校验答案、不发奖 |
| 插件 `on_interaction` | 真正业务逻辑：开局、查询、校验答案、渲染结果、维护插件内部状态 | 不解析 Bot Token、不解析转账通知原文、不自己做规则级冷却/每日次数 |
| UserBot 命令 | 管理员手动触发同一业务能力，例如 `{prefix}pt 12345` 或 `{prefix}24d 100` | 不承接群友高频互动 |

因此，同一个能力推荐有多个触发器，但只有一份业务实现：

```text
群友关键词 / 转账通知
  -> 交互 Bot 规则过滤、限流、路由
  -> 插件 on_interaction 执行业务

管理员 UserBot 命令
  -> commands 入口
  -> 调用同一份插件业务函数
```

不要把自动回复做成“业务实现”。自动回复可以作为轻量触发器，但 PT 促销、抽奖、游戏、查询这类能力必须沉到插件本体里，再由交互 Bot 或 UserBot 命令调用。

当前标准事件类型写在 `payload["source"]["type"]`：

| event.type | 触发时机 | 说明 |
| --- | --- | --- |
| `payment_confirmed` | 转账通知命中规则 | 常用于付费开局 |
| `keyword` | 插件启动关键词命中且无付费门槛 | 常用于免费开局 |
| `message` | 自主订阅命中，或旧规则已有活跃会话后的普通群消息 | 常用于答题、猜测、继续流程 |
| `callback_query` | 当前 Interaction Bot 收到的 inline keyboard 点击；可自主订阅，也可来自旧规则会话 | 常用于按钮选择、翻页、确认操作；不是 UserBot 主动点击第三方 Bot 按钮 |
| `session_close` | 规则被关闭或会话被强制结束 | 插件可清理状态，第一版可按需实现 |

#### interaction_entries 迁移字段

旧交互入口迁移时必须把启动方式、事件、会话和输出边界写清楚。推荐同时映射到 `event_subscriptions` 和标准 action：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `key` | 是 | 传给插件 `on_interaction(ctx, entry_key, payload)` 的入口名 |
| `title` / `description` | 推荐 | 前端选择器、实验室和日志里展示给人的说明 |
| `launch_mode` | 兼容 | `bridge` / `direct` / `hybrid`，旧字段；新插件建议同时声明 `dispatch_modes` |
| `dispatch_modes` | 推荐 | `admin_command` / `public_keyword`，分别表示管理员带前缀命令触发、群友关键词/转账规则触发 |
| `message_channels` | 兼容 | 旧 `Plugin` hook 的 incoming/outgoing 监听方向；不要把它当发送通道偏好 |
| `money_channel` | 兼容 | 旧文档提示字段；钱相关动作现在由 `payout` / 结算链路固定路由 userbot |
| `events` | 是 | 入口接受的事件白名单，例如 `keyword`、`payment_confirmed`、`message`、`callback_query`、`session_close` |
| `session_scope` | 是 | `chat` / `user` / `none`，决定平台如何保存会话和路由后续消息 |
| `session_policy` | 推荐 | 会话 TTL、重复触发、关闭策略、并发策略的声明 |
| `payload_contract` | 推荐 | 插件要求平台提供的输入信封与必填字段 |
| `input_schema` | 推荐 | 当前规则可覆盖的入口参数，默认值用于前端预填 |
| `result_contract` | 推荐 | 插件会返回的标准动作类型、结算字段和结束语义 |
| `settlement` | 按需 | 涉及奖金、补发、对账时声明结算责任和字段 |
| `command_fallback` | 按需 | 是否允许平台在无法走交互入口时提示或回退到 UserBot 命令 |
| `preserve_command_trigger` | 是 | 必须为 `true`，表示保留原有 UserBot 命令触发，不被交互入口覆盖 |
| `interaction_profile` | 推荐 | 玩法类型声明，供前端展示和后续插件接入分型使用 |

`interaction_profile` 当前建议值：

| 值 | 说明 |
| --- | --- |
| `session_game` | 群局抢答、竞猜、填空、算题、24 点等单局互动玩法 |
| `challenge_game` | 双人/多人对战、轮流操作的互动玩法 |
| `reward_pool` | 红包、奖池、下注开奖这类多人结算玩法 |
| `utility_trigger` | 只借交互 Bot 做入口，但主体不是群局玩法的工具插件 |

`launch_mode` 的含义：

| launch_mode | 启动路径 | 适用场景 |
| --- | --- | --- |
| `bridge` | 交互 Bot 收到事件，平台组装信封后调用插件 `on_interaction` | 群局、抢答、抽奖、转账命中开局等高频群内流程 |
| `direct` | UserBot 原有命令或插件内部调用直接执行业务，不经过交互 Bot | 管理员命令、私有工具、无需交互 Bot 规则的能力 |
| `hybrid` | 同一能力同时支持 `bridge` 和 `direct`，但两边仍是独立触发边界 | 既允许管理员 `{prefix}24d 100` 开局，也允许群友关键词/转账由交互 Bot 开局 |

`direct` 和 `hybrid` 都不表示普通群友 incoming 消息可以直接触发 `commands`。`command_fallback` 只用于平台提示或受控内部派发，不能把群友文本原样送入 `on_command`。如果启用回退，必须同时声明 `preserve_command_trigger: true`，并保证原命令名、参数格式、权限和 outgoing 限制保持兼容。

```python
MANIFEST = Manifest(
    key="game24",
    display_name="24点游戏",
    version="1.1.0",
    min_telepilot_version="0.72.0-beta.2",
    category="interactive",
    requires_platform_capabilities=["interaction_bot", "ledger"],
    interaction_entries=[
        {
            "key": "start_paid_game",
            "title": "付费开局",
            "description": "转账命中或插件关键词命中后，由交互 Bot 开启一局游戏。",
            "launch_mode": "hybrid",
            "dispatch_modes": ["admin_command", "public_keyword"],
            "session_scope": "chat",
            "events": ["payment_confirmed", "keyword", "message", "callback_query", "session_close"],
            "preserve_command_trigger": True,
            "command_fallback": {
                "enabled": True,
                "command": "24d",
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
            "payload_contract": {
                "required_envelope": ["source", "actor", "trigger", "session"],
                "required_event_fields": ["type", "chat_id"],
            },
            "result_contract": {
                "actions": ["send_message", "send_photo", "end_session"],
            },
            "settlement": {
                "mode": "announce_only",
                "winner_field": "actor.user_id",
                "amount_field": "prize",
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

`input_schema` 描述的是某个交互入口允许接收的参数形态和默认值，不是插件的全局配置。Web 端在交互规则里保存的是 `module_config`：它只属于当前规则，只保存这条规则对入口参数的覆盖值，并会随规则 payload 一起提交给后端。

例如某条规则可以绑定 `game24 / start_paid_game`，并保存：

```json
{
  "module_key": "game24",
  "module_action": "start_paid_game",
  "module_config": {
    "prize": 200,
    "timeout": 600
  }
}
```

运行时入口收到的 payload 会包含当前规则的 `module_config` 字段。Web 端会在选择入口时用 `input_schema.properties.*.default` 辅助生成初始 JSON。新插件应从 `payload["module_config"]` 读取本次规则参数；插件自身的账号级配置仍通过 `ctx.config` 读取。历史兼容层可能继续在 payload 顶层附带平铺字段，但那只用于旧插件迁移，不作为新插件示例或主路径。

### on_interaction 迁移实现

```python
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register
from app.worker.plugins.events import event_from_interaction_payload


@register
class GuessNumberPlugin(Plugin):
    key = "guess_number"
    display_name = "猜数字"

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_guess_number":
            return None

        event = event_from_interaction_payload(payload)
        event_type = event.type

        if event_type == "message":
            answer = event.message.text.strip()
            if answer != "42":
                return []
            if ctx.messages:
                await ctx.messages.send(
                    text=f"答对了：{event.actor.display_name or '玩家'}\n奖金：{payload.get('prize') or 123}",
                    reply_to_message_id=event.message.message_id,
                )
                return []
            return [{"type": "send_message", "text": "答对了"}]

        module_config = payload.get("module_config") if isinstance(payload.get("module_config"), dict) else {}
        prize = int(module_config.get("prize") or 123)
        if ctx.messages:
            await ctx.messages.send(
                text=f"猜数字开始，奖金：{prize}",
                reply_markup={
                    "inline_keyboard": [[{"text": "查看状态", "callback_data": "guess:status"}]]
                },
            )
            return []
        return [{"type": "send_message", "text": f"猜数字开始，奖金：{prize}"}]
```

当前平台已支持的标准动作：

| type | 字段 | 说明 |
| --- | --- | --- |
| `send_message` | `text` | 在命中的群或动作指定的 `chat_id` 发送消息 |
| `send_message` | `reply_to_message_id` | 可选，指定回复哪条消息 |
| `send_message` | `reply_to_user_id` / `reply_to_search_limit` | 可选；`send_via=userbot_reply` 时，平台按用户 ID 搜索近期发言作为回复锚点 |
| `send_message` | `reply_anchor_missing_text` | 可选；找不到 `reply_to_user_id` 的近期发言时发送的提示，支持 `{user_id}` |
| `send_message` | `chat_id` | 可选；不填时发送到触发会话，填写时由平台按通道能力发送到指定会话 |
| `send_message` | `send_via` / `channel` / `channel_selector` / `send_via_options` | 高级可选；普通互动省略，平台继承 `session.channel`，只在跨通道公告、管理提示或迁移桥兼容时显式覆盖 |
| `send_message` | `reply_markup` | 可选，Bot API inline keyboard；只会透传给 `interaction_bot`，`userbot_reply` 不承接按钮 |
| `send_message` | `save_message_id_key` | 可选；发送成功后把本次 Telegram `message_id` 按 key 保存 2 小时，供后续编辑、删除或替换使用 |
| `send_message` | `replace_saved_message_id_key` | 可选；发送新消息并保存新 `message_id` 后，读取该 key 原来的消息 ID 并删除旧消息，适合“只保留最新一条”的滚动通知 |
| `send_rich_message` | `rich_message` | Telegram 原生 Rich Message；对象中必须且只能提供 `html`、`markdown`、`blocks` 之一，可表达标题、任务列表、表格、折叠详情、公式和媒体块 |
| `send_rich_message` | `reply_to_message_id`、`reply_markup`、`save_message_id_key`、`pin` | 可选；Userbot 路径支持回复，但 `reply_markup` 仅由 Interaction Bot 执行 |
| `send_rich_message` | `send_via` | 可省略，平台默认选择 `interaction_bot`；显式 `userbot_reply` 使用 Layer 228+，并受 Premium/能力与格式限制 |
| `send_rich_message` | `compact_tables` | 可选（0.106.0-beta.12+）；`true` 时把消息里的表格改为 Bot API 10.3 紧凑表格（blocks 加 `is_compact`、HTML 加 `compact`、Markdown 管道表格转 `<table bordered striped compact>`），平台消费不外发；旧 Bot API 拒绝时自动去掉重发一次（边框与隔行着色保留）。不写时表格也默认带边框 + 隔行着色（0.106.0-beta.15+，见“表格默认样式”）。`edit_message`（带 `rich_message`）与 `answer_guest_query` 同样支持 |
| `send_photo` / `send_file` | `photo_base64` / `file_base64` | 按动作通道发送图片/文件字节；交互 Bot 下 `send_photo` 走 `sendPhoto`，`send_file` 走 `sendDocument` |
| `send_photo` / `send_file` | `filename`、`caption`、`reply_to_message_id` | 可选，文件名、说明文字、回复目标 |
| `send_photo` / `send_file` | `save_message_id_key` | 可选；媒体发送成功后把 Telegram `message_id` 按 key 保存 2 小时，供后续 `edit_caption`、删除或替换使用 |
| `edit_message` | `message_id`、`text` | 编辑纯文本消息；不用于编辑媒体 caption |
| `edit_message` | `message_id`、`rich_message` | 编辑 Rich Message；默认由 Interaction Bot 执行，显式 Userbot 支持 HTML、Markdown 和纯文本 blocks，并受 Premium 与 `rich_message_posting` 门禁 |
| `edit_message` | `inline_message_id`、`rich_message` | 以 Rich Message 编辑 inline 消息（含 Guest Mode 占位），只支持 Interaction Bot（0.106.0-beta.2+） |
| `edit_caption` | `message_id` / `message_id_key`、`caption` | 编辑图片或文件消息的 caption；`message_id_key` 会读取同账号命名空间下由 `save_message_id_key` 保存的消息 ID |
| `edit_caption` | `parse_mode`、`reply_markup` | 可选；`parse_mode="html"` 时按 HTML 发送，`reply_markup` 只由 `interaction_bot` 原生承接 |
| `delete_message` | `message_id` | 删除对应 Bot 通道可操作的消息 |
| `pin_message` | `message_id` | 置顶对应 Bot 通道可操作的消息 |
| `click_callback_button` | `chat_id`、`message_id`、`row`、`column`、可选 `expected_bot_id` / `expected_button_text` | 仅 UserBot 执行链路；Interaction Bot 插件入口（包括 `ctx.messages.apply`）不支持。installed 插件需声明 `click_bot_button`。平台重新读取 callback data，只允许 callback 类型，并执行限流、审计、dry-run；明确成功后保护 20 秒，超时或结果未知时保护 5 分钟 |
| `answer_callback` | `callback_query_id`、`text`、`show_alert` | ACK 当前 Interaction Bot 收到的 inline keyboard callback；不能点击第三方 Bot 按钮 |
| `payout` | `amount`、`text`、`reply_to_message_id`、`reply_to_user_id`、`reply_to_display_name`、`reply_to_username`、`reply_to_search_limit`、`reply_anchor_missing_text` | UserBot 发奖动作；有消息 ID 时直接回复，否则可按用户 ID 查找近期发言作为锚点。公开名必须来自安全身份 facade，匿名管理员不传 username。超限拒（`error_code=payout_limit_exceeded`），瞬时失败自动进补偿队列重发，插件无需自己重试（见上文 payout 语义） |
| `end_session` | 无 | 本次入口处理完成后不保留交互会话，适合彩票、红包等长期轮回插件 |

通道原则是：**触发方式决定会话通道，插件默认不感知通道，框架负责路由和执行**。命令触发的普通会话收发走 userbot，关键词/付款/按钮触发的普通会话收发走交互 Bot。能力固定路由有两个例外：`payout`、收款确认和发奖等钱相关动作永远走 userbot；`send_rich_message` 默认走 Interaction Bot，只有明确指定时才走 Layer 228+ Userbot。其他动作只有在跨通道公告、特殊管理提示或迁移桥兼容时才显式写 `send_via`：

| send_via | 含义 | 约束 |
| --- | --- | --- |
| `interaction_bot` | 由交互 Bot 发送群内题面、答复、图片、会话提示 | 高级覆盖值；别名 `bot` |
| `userbot_reply` | 由当前账号 worker 的 userbot 代发指定消息 | 高级覆盖值；适合确有账号身份需要的动作 |
| `auto` | 按平台默认候选顺序尝试 | 迁移兼容值；新互动插件通常不需要使用 |

入口未声明 `result_contract.send_via` 时，平台按会话通道和动作类型决定实际发送方。入口声明了 `result_contract.actions` 或 `result_contract.send_via` 时，运行时把它作为可见契约和调试依据：插件调用未声明动作或未声明通道会写入 runtime log、交互中心调试面板和插件 lint 告警，但不会因为“未声明”本身静默丢弃动作。`reply_markup` 在 interaction bot 会话中由 Bot API 承接；在 userbot 会话中由平台降级成文本编号并把玩家回复合成为 callback 事件。`bbot_notice` / `notice` / `notice_bot` 已移除且不兼容，不再作为插件主动发送通道；插件显式请求这些旧通道会返回明确失败并提示迁移到会话通道模型。群里已有的转账结果通知 Bot 只作为外部到账证据来源，TelePilot 监听它来确认付款，不用它发送插件结果。涉及奖金、补发、转账、催付的插件必须在 `settlement` 或 `payout` 中写清职责：普通 Bot 只能公告和给出可对账结果，真正收款确认和发奖仍由账号 worker 的 userbot 代发或由平台受控结算流程处理。

普通互动推荐不写通道：

```python
await ctx.messages.send(
    text="题面或回复内容",
    reply_to_message_id=event_from_interaction_payload(payload).message.message_id,
)

await ctx.messages.send_rich(
    html="<h1>任务状态</h1><ul><li><input type=\"checkbox\" checked>已完成</li></ul>",
)

await ctx.messages.send(
    chat_id=-1001234567890,
    text="指定会话发送，仍由平台按会话通道执行",
)

await ctx.messages.send_photo(
    photo=image_bytes,
    filename="round.png",
    caption="题面",
    save_message_id_key="round",
)

await ctx.messages.edit_caption(
    message_id_key="round",
    caption="题面\n\n答对结果",
)
```

推荐迁移路径：旧插件继续返回 `list[dict]` 标准动作可以兼容；新插件或重构插件优先调用 `ctx.messages.send/send_rich/send_photo/send_file/edit/edit_caption/delete/pin/click_callback_button/answer_callback`。`ctx.messages` 只生成或提交标准动作，不会暴露 Bot Token、UserBot session 或 callback data。

框架层源码位于 `backend/app/services/interaction/`：`contracts.py` 负责记录 `result_contract` 告警与旧通道失败，`delivery.py` 负责受控发送、编辑、删除、置顶、按钮 ACK、媒体发送和 message_id 保存。

#### Contract Guard 行为

Contract Guard 不是公共插件市场式硬沙箱，而是个人可信插件标准下的契约提示器：

| 场景 | 运行时行为 |
| --- | --- |
| 调用未声明 `result_contract.actions` 的动作 | 记录 `guard_level=warning`，动作继续进入执行链路 |
| 显式请求未声明 `result_contract.send_via` 的高级覆盖通道 | 记录 `guard_level=warning`，按可执行能力尝试并保留审计 |
| `send_via` 同时包含受控通道和旧 `notice` / `bbot_notice` / `notice_bot` | 整个动作记录 `guard_level=failed`，返回 `send_channel_deprecated`，不做自动改写 |
| `send_via` 只包含未知值 | 记录 `guard_level=failed`，返回不可执行失败和迁移提示 |
| `send_via` 同时包含受控通道和非旧未知值 | 记录 `guard_level=warning`，保留可执行受控通道并继续执行 |
| `send_rich_message` 未指定通道或使用 `auto` | 默认选择 `interaction_bot`，保持既有行为 |
| `send_rich_message` 只指定 `userbot_reply` | 使用 Layer 228+ Userbot；能力或格式不满足时返回稳定错误码，不降级 |
| 交互 Bot token 缺失、UserBot worker 离线、Telegram API 失败 | 返回客观能力失败，不伪装成功 |

#### 标准事件信封

`payload` 本身就是标准事件信封。新插件不要把旧平铺字段当主路径，也不要依赖 `payload["event"]`；如果想少写字段判断，优先使用 `event_from_interaction_payload(payload)`。

```json
{
  "source": {
    "type": "payment_confirmed",
    "channel": "interaction_bot",
    "driver": "telegram_bot_api",
    "account_id": 1,
    "chat_id": -100123,
    "chat_type": "supergroup",
    "update_id": 10,
    "message_id": 81,
    "callback_query_id": null,
    "callback_data": null
  },
  "message": {
    "chat_id": -100123,
    "message_id": 81,
    "text": "转账成功...",
    "entities": [],
    "media": null,
    "date": null,
    "reply_to_message_id": 80
  },
  "chat": {
    "id": -100123,
    "type": "supergroup",
    "title": null,
    "username": null
  },
  "sender": {
    "user_id": 8980553289,
    "display_name": "转账通知 Bot",
    "username": null
  },
  "actor": {
    "user_id": 111,
    "display_name": "AAA",
    "username": "aaa"
  },
  "source_actor": {
    "user_id": 8980553289,
    "display_name": "转账通知 Bot"
  },
  "payment": {
    "status": "confirmed",
    "amount": 100,
    "payer_user_id": 111,
    "payer_display_name": "AAA",
    "receiver_display_name": "BBB",
    "notice_sender_user_id": 8980553289,
    "notice_message_id": 81,
    "source_message_id": 81,
    "reply_to_message_id": 80
  },
  "player": {
    "user_id": 111,
    "display_name": "AAA",
    "username": "aaa",
    "identity_key": "tg:111",
    "identity_confidence": "reply_context"
  },
  "reply_to": {
    "message_id": 99,
    "user_id": 111,
    "display_name": "AAA",
    "text": "+100"
  },
  "trigger": {
    "type": "payment_confirmed",
    "rule_id": "game24-ticket",
    "rule_name": "24 点门票",
    "module_key": "game24",
    "entry_key": "start_paid_game"
  },
  "session": {
    "key": "account_bot:interaction_session:...",
    "scope": "chat",
    "ttl_seconds": 3600,
    "active": true,
    "data": {}
  },
  "raw": {
    "update_id": 10,
    "message_id": 81,
    "event_type": "payment_confirmed",
    "rule_id": "game24-ticket",
    "module_key": "game24",
    "entry_key": "start_paid_game",
    "parsed": {
      "payer_name": "AAA",
      "receiver_name": "BBB",
      "amount": 100
    }
  },
  "module_config": {
    "prize": 200
  }
}
```

信封字段说明：

| 字段 | 说明 |
| --- | --- |
| `source` | 事件来源、事件类型、update/message/callback 基础索引；`source.type` 是插件分流主字段 |
| `message` | 当前消息文本、消息 ID、回复目标、实体、媒体和可选 Rich Message 摘要；`text_source` 标记文本来源 |
| `chat` | 当前会话 ID、类型、标题和 username；标题可能为空 |
| `sender` | Telegram 实际发送者；转账触发时通常是外部转账通知 Bot |
| `source_actor` | 实际发来本条 Telegram 消息的 Bot/用户。转账触发时通常是可信转账通知 Bot，不应当作玩家 |
| `actor` | 当前事件的业务行为主体。答题、按钮点击、关键词触发时通常就是发送者；付费开局时平台会尽量映射到付款玩家 |
| `payment` | 可信转账通知 Bot 已确认到账后的结构化凭证，包含金额、付款人、收款人和通知消息信息 |
| `player` | 付费开局绑定的玩家身份。独玩/按钮玩法应优先读取它，并检查 `player.user_id` 是否存在 |
| `reply_to` | 本动作应引用的原消息或被回复对象，中奖公告必须尽量带上 |
| `trigger` | 命中的规则、入口、消息和触发类型；用于排障和幂等 |
| `session` | 平台会话标识、作用域、TTL 和数据；插件内部状态 key 应与它一致 |
| `raw` | 脱敏后的 Telegram 更新摘要，只用于排障，不作为常规业务字段 |
| `owner_user_ids` / `admin_user_ids` | 交互 Bot Event Bus 投递（0.106.0-beta.13+）同样带上：`owner_user_ids` 是账号本人的 Telegram user id，`admin_user_ids` 是 TelePilot 管理员（账号本人 + 已授权 sudo 用户），与 UserBot 分发状态同义；只含 user id。插件可据此实现“仅 TelePilot 管理员可点”的按钮；群管理员另用 `ctx.identities.resolve(...).is_admin` 判断。旧平台没有这两个字段 |

`payload_contract` 用来声明插件对上述信封的要求。平台和前端可以据此校验规则是否能保存，排障时也能判断是“事件没到”还是“字段不满足”。不要把敏感原文、Bot Token、完整付款通知文本写进信封；只传插件业务需要的结构化字段。

#### 群内安全公开身份

Telegram 按钮回调的 `payload.sender` 必须保留真实点击者，供权限校验、幂等、每日限制和发奖使用；但匿名管理员点击按钮时，这个对象同样包含真实姓名，因此不得直接用于群内文案。平台提供统一身份解析器和姓名清洗函数：

```python
from app.worker.plugins.base import resolve_public_sender_identity, sanitize_public_display_name

identity = await resolve_public_sender_identity(
    ctx,
    chat_id=payload["chat"]["id"],
    user_id=payload["sender"]["user_id"],
    fallback_display_name=payload["sender"].get("display_name") or "",
)

# 只把 identity.display_name 写入公开消息。
# user_id 仍使用 payload.sender.user_id 做业务校验。

# 已有一个不经过身份解析的公开标签时，也必须先清洗。
safe_label = sanitize_public_display_name(raw_label, limit=10)
```

结算、排行榜等多人名单使用 `resolve_public_sender_identities(ctx, chat_id=..., senders={user_id: name})` 批量解析；平台通过 `ctx.identities` 使用不受插件沙箱影响的内部 UserBot 读取管理员目录和成员权限，不依赖 Interaction Bot 的 `getChatMember`。身份无法由 UserBot 确认时平台会隐藏姓名，而不会回退按钮回调中的真实姓名。平台不会把 Bot Token、原始客户端或成员列表交给插件。

身份解析结果不做应用层缓存：每次调用都会重新读取当前群管理员目录和成员权限。UserBot 实体恢复只使用本地实体缓存或 Redis 中可重新校验的近期消息 `message_id`；缓存未命中时不会在按钮 callback 内扫描群历史。近期消息锚点不会缓存姓名、username、管理员状态或标签。

身份解析返回的名称已统一调用 `sanitize_public_display_name()`：移除 Unicode 控制符、零宽格式符、各类空白与不可见填充字符，并限制为最多 10 个字符；清洗后为空时使用“匿名用户”。这只解决公开姓名安全，不是 HTML/Markdown 转义，插件仍须按实际 `parse_mode` 转义后再发送。匿名管理员若无法由 UserBot 安全确认，会按 fail-closed 结果隐藏姓名。

明确要求锁定账号 UserBot 自身视角时，可直接调用专用 facade：

```python
identity = await ctx.identities.resolve_userbot(
    chat_id=chat_id,
    user_id=user_id,
    fallback_display_name=name_from_userbot_message,
)
```

`resolve_userbot()` 不调用 Interaction Bot；姓名以 UserBot 实体和传入的 UserBot 消息姓名为准，因此会保留该账号保存的联系人姓名。它仍会读取 UserBot 群权限并隐藏匿名管理员。

返回对象字段：

| 字段 | 说明 |
| --- | --- |
| `user_id` | 真实 Telegram User ID，只用于业务校验，不等于可公开姓名 |
| `display_name` | 可安全写入群消息的名称；已过滤不可见字符并限制为 10 个字符，匿名管理员为清洗后的标签，无标签时为“匿名管理员” |
| `is_anonymous_admin` | 当前成员是否开启匿名管理员身份 |
| `is_admin` | 当前成员是否为本群管理员；由 UserBot 成员权限或管理员目录确认，不能通过是否存在标签推断 |
| `tag` | Telegram 成员标签或管理员自定义头衔；普通成员存在标签时也不会覆盖 `display_name` |
| `resolved` | 是否成功读取群成员权限；为 `false` 时 `display_name` 固定使用隐藏身份的回退值 |

普通消息事件会同步投影 `sender.is_anonymous_admin` 与 `sender.tag`。匿名管理员消息的 `sender.user_id` 为 `null`，`sender.display_name` 已使用 `sender_tag` / `author_signature`；普通成员即使带 `sender_tag`，`sender.display_name` 仍是其姓名。插件不要用“是否存在标签”判断匿名状态，只能使用 `is_anonymous_admin`。

付费触发有两个证据源：UserBot/回复上下文负责补充付款玩家身份，可信转账通知 Bot 负责证明到账成功。插件不得把普通 `+金额` 文本当作到账依据；只有 `source.type=payment_confirmed` 且 `payment.status=confirmed` 才表示平台已经通过转账通知完成金额、收款人和规则校验。如果转账通知只提供付款人名称，平台会把 `player.identity_confidence` 标为 `name_only`；`participant_policy=solo_owner` 或 `paid_pool` 的入口会先要求付款人点击确认来获得真实 `player.user_id`。

入口可声明 `participant_policy` 来描述参与边界：

| 值 | 说明 |
| --- | --- |
| `open_race` | 一人付款/关键词开局，全群可参与抢答或竞猜 |
| `solo_owner` | 只有开局付款人/触发人可继续操作，适合 21 点、个人按钮流程 |
| `paid_pool` | 只有已确认付费的玩家池可参与，适合多人付费入场 |
| `notify_only` | 只做通知或一次性动作，不建立玩家操作边界 |

`solo_owner` / `paid_pool` 的按钮回调明确指向牌桌操作，平台会在调用插件前完成参与者校验。普通群消息具有歧义，平台会先让插件判断是否属于当前会话；插件返回业务动作后才应用参与者门禁，返回零动作或仅返回 `no_session` / `end_session` 时静默放行。插件仍必须在修改内部状态前校验消息发送者是否属于自己的玩家集合，不能把平台门禁当成业务状态校验的唯一依据。

`interaction_entries` 中的 `session_scope` 是插件会话作用域，必须按插件业务形态声明。它和交互规则里的 `concurrency` 不是一回事：

| 字段 | 归属 | 含义 | 示例 |
| --- | --- | --- | --- |
| `interaction_entries[].session_scope` | 插件入口声明 | 插件会话怎么保存和路由后续 `message` 事件 | 九宫格、24 点、猜数字填 `chat` |
| 交互规则 `concurrency` | 规则层 | 规则的触发/限流对象，用于每用户 CD、每日次数、触发去重 | 群友每天最多置顶 2 次可填 `user` |

可选值：

- `chat`：同一个群内同一时间只开一局，适合 24 点、九宫格、猜数字、诗词填空、红包这类公共抢答或公共流程。
- `user`：同一个用户一条会话，适合个人查询、个人表单、每个人互不影响的私有流程，例如 `pt_promote.promote_torrent`。
- `none`：入口本身不需要平台保存会话，适合只执行一次就结束的动作；插件仍可在内部维护自己的长期状态。

后端保存规则时会优先读取 `plugin.json` / `manifest.py` 中声明的 `session_scope`，并写入规则的 `module_session_scope`。这样即使规则为了“每个群友 6 小时 CD、每日 2 次”设置了 `concurrency=user`，九宫格这类 `session_scope=chat` 的群局也仍然会按群保存会话，其他群友回复 `1-9` 才能进入同一局。

如果插件没有声明 `session_scope`，平台只能回退到规则 `concurrency`，这很容易让群局被误判成用户私有会话。所有声明了 `interaction_entries` 的插件都必须显式填写 `session_scope`。

### 事件过滤与 rule_bound

当前事件订阅支持 `filters` 过滤。已知过滤键按平台约定校验，常见值包括 `keywords`、`contains`、`callback_data`、`commands` 和 `rule_id`；未知 filter key 会被保留用于兼容，但会触发 warning，便于插件作者尽快修正。

`rule_bound` 订阅如果声明了 `filters.rule_id`，它必须与当前触发上下文里的 `trigger.rule_id` 完全相等，否则本次订阅会被判定为 `filter_not_matched`。这条规则用于把“规则绑定”从松散匹配收紧到明确的一对一绑定，避免跨规则误投递。

#### 入口参数来源

交互入口 payload 由平台运行时组装，当前不会在后端再次读取 manifest 默认值或插件账号级配置做自动合并。有效来源如下，越靠后越容易覆盖同名字段：

```text
交互规则 module_config
< 转账事件动态参数（payer / receiver / amount / chat_id 等）
```

`input_schema` 的默认值主要给前端表单预填使用；旧规则、API 直接写入或第三方客户端不一定会带上这些默认值，所以插件仍应在代码里为关键参数提供兜底。`module_config` 只保存当前交互规则的覆盖项，例如“这条门票规则奖金为 200”。插件自身的通用配置仍放在插件配置页中，运行时从 `ctx.config` 读取，不能混进规则的 `module_config`。

`session_policy` 用来告诉平台和维护者会话如何结束、重复触发如何处理、TTL 多久。常见写法：

```json
{
  "ttl_seconds": 3600,
  "duplicate_start": "reject",
  "close_on": ["winner", "timeout", "session_close"],
  "max_active_per_scope": 1
}
```

`payload_contract` 描述输入，`result_contract` 描述输出。它们是文档化契约，也会成为 Contract Guard 告警依据。`result_contract.actions` 只能列标准动作；`result_contract.send_via` 只用于高级覆盖或迁移兼容的可见契约，不是普通互动必填项，也不是硬沙箱白名单；`settlement` 只说明结算/公告语义，不能让普通 Bot 直接拥有发奖权限。

#### 标准事件输入

平台调用交互入口时，会提供标准信封；历史适配层或旧规则还可能同时提供平铺字段。新插件不要依赖转账通知原文或旧平铺字段，优先读取 `source` / `message` / `chat` / `sender` / `actor` / `source_actor` / `player` / `payment` / `reply_to` / `trigger` / `session` 信封。

```json
{
  "source": {
    "type": "payment_confirmed",
    "channel": "interaction_bot",
    "driver": "telegram_bot_api",
    "account_id": 1,
    "chat_id": -100123,
    "message_id": 81
  },
  "message": {
    "chat_id": -100123,
    "message_id": 81,
    "text": "转账成功...",
    "reply_to_message_id": 80
  },
  "sender": {
    "user_id": 8980553289,
    "display_name": "转账通知 Bot"
  },
  "actor": {
    "user_id": 111,
    "display_name": "AAA"
  },
  "payment": {
    "status": "confirmed",
    "amount": 100,
    "payer_user_id": 111,
    "payer_display_name": "AAA",
    "receiver_display_name": "BBB",
    "source_message_id": 81,
    "reply_to_message_id": 80
  },
  "player": {
    "user_id": 111,
    "display_name": "AAA",
    "identity_key": "tg:111",
    "identity_confidence": "reply_context"
  },
  "trigger": {
    "type": "payment_confirmed",
    "rule_id": "game24-ticket",
    "module_key": "game24",
    "entry_key": "start_paid_game"
  },
  "session": {
    "key": "account_bot:interaction_session:...",
    "scope": "chat",
    "ttl_seconds": 3600,
    "active": true,
    "data": {}
  }
}
```

为了兼容历史插件，payload 顶层还可能同时带 `account_id`、`chat_id`、`payer_user_id`、`notice_message_id` 等平铺字段。它们只用于旧插件迁移期，不要在新插件里当作标准输入主路径。

#### 标准动作输出

交互入口或适配器应返回平台可执行的标准动作，或通过 `ctx.messages` 缓存这些动作，而不是直接调用 Telegram API。交互 Bot runtime 统一负责发送、回复、删除、置顶、按钮 ACK 与基础动作执行。需要跨消息保存业务状态时，插件应优先使用 `ctx.storage`；它会按账号和插件自动隔离命名空间。安装插件的 `ctx.redis` 是 `PluginRedisFacade`，会自动添加同一命名空间并限制可调用方法，不要自行拼接完整 `plugin_store:` 前缀。需要 NX 时可使用 facade 已开放的 `set(..., nx=True, ex=...)`；CAS、Lua、pipeline、分布式锁或严格原子抢占不在公开能力内，应交给平台会话/结算链路或官方受控实现。

```json
[
  {
    "type": "send_message",
    "text": "24 点开始..."
  },
  {
    "type": "send_message",
    "text": "答对了：AAA\n题目：24 点 [1 5 5 5]\n奖金：123",
    "reply_to_message_id": 99,
    "settlement": {
      "status": "winner_confirmed",
      "winner_user_id": 111,
      "amount": 123,
      "currency": "points"
    }
  },
  {
    "type": "send_photo",
    "photo_base64": "...",
    "filename": "puzzle.png",
    "caption": "题面"
  }
]
```

#### 端到端示例：24 点交互入口

下面是 `payment_confirmed` / `keyword` 开局、`message` 答题、`session_close` 清理的最小形态。真实插件可以把 `generate_24_puzzle()`、`check_answer()`、`render_start()` 拆成纯函数复用。示例用 `ctx.storage` 做持久化状态；它适合普通会话状态，不适合充当原子抢答锁。

```python
import secrets
import time
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register
from app.worker.plugins.events import event_from_interaction_payload


@register
class Game24Plugin(Plugin):
    key = "game24"
    display_name = "24点游戏"

    async def on_interaction(
        self,
        ctx: PluginContext,
        entry_key: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if entry_key != "start_paid_game":
            return None
        event = event_from_interaction_payload(payload)
        event_type = event.type
        chat_id = int(event.message.chat_id or 0)
        if not chat_id:
            return []

        state_key = f"game24:{chat_id}"
        if ctx.storage is None or not ctx.storage.available:
            if event_type in ("payment_confirmed", "keyword"):
                return [
                    {
                        "type": "send_message",
                        "text": "当前运行上下文没有可用状态存储，无法启动需要持续会话的游戏。",
                    }
                ]
            return []

        if event_type in ("payment_confirmed", "keyword"):
            numbers = generate_24_puzzle()
            module_config = payload.get("module_config") if isinstance(payload.get("module_config"), dict) else {}
            prize = int(module_config.get("prize") or 123)
            state = {
                "account_id": ctx.account_id,
                "chat_id": chat_id,
                "numbers": numbers,
                "prize": prize,
                "active": True,
                "game_id": secrets.token_hex(8),
                "created_at": time.time(),
            }
            await ctx.storage.set(state_key, state, ttl=3600)
            return [{"type": "send_message", "text": render_start(numbers, prize)}]

        state = await ctx.storage.get(state_key, default={})
        if event_type == "message":
            if not state.get("active") or not check_answer(event.message.text, state["numbers"]):
                return []
            claim_key = f"game24_claim:{chat_id}:{state['game_id']}"
            if await ctx.storage.get(claim_key):
                return []
            await ctx.storage.set(claim_key, event.message.message_id or "", ttl=3600)
            state["active"] = False
            await ctx.storage.set(state_key, state, ttl=3600)
            return [
                {
                    "type": "send_message",
                    "text": f"答对了：{event.actor.display_name or '玩家'}\n奖金：{state['prize']}",
                    "reply_to_message_id": event.message.message_id,
                }
            ]

        if event_type == "session_close":
            if state.get("active"):
                state["active"] = False
                await ctx.storage.set(state_key, state, ttl=3600)
            return []

        return []
```

#### 兼容边界

1. 原插件本体不得为了交互 Bot 直接改写 `commands` / `on_message` 语义；UserBot 入口和交互 Bot 入口是两套边界。
2. 可以把纯业务逻辑抽到共享函数，例如题目生成、答案校验、渲染模板；UserBot 插件和交互 Bot 适配器共同调用这些纯函数。
3. 插件不处理 Bot Token、外部转账通知原文格式、转账过滤、发奖账号；这些都属于平台层职责，钱相关动作也不该放进交互 Bot 的高频入口。
4. 交互 Bot 中奖公告必须引用赢家的答案消息，方便 `UserBot` 账号按结构化公告自动回复发奖或补发奖金。
5. 若插件未声明 `interaction_entries`，前端不应把它展示为可由交互 Bot 启动的插件。旧 `config_schema["x-interaction-entries"]` 仅作为兼容入口，新插件不要再用旧字段。
6. `interaction_entries[].session_scope` 必须和插件内部状态 key 一致：群局状态 key 应包含 `chat_id`，用户私有流程状态 key 应同时包含 `chat_id` 和 `user_id`。
7. 返回 `end_session` / `close_session` / `no_session` 时，平台会清理规则会话；插件自己的 Redis 状态仍由插件负责清理。
8. `preserve_command_trigger` 必须保持为 `true`。交互入口新增后，原本能用的 UserBot 指令仍要按原指令名、原参数和原权限工作。
9. 新插件建议声明 `dispatch_modes`，让前端明确入口来源；不要用 `message_channels` / `money_channel` 表达发送通道，普通动作继承 `session.channel`，`payout` 永远 userbot。
10. 使用 inline keyboard 时，入口必须声明 `callback_query` 事件；按钮动作只通过 `send_message.reply_markup` 交给交互 Bot 发送，`userbot_reply` 不承接按钮。
11. `settlement` / `result_contract` 只描述可对账结果和平台动作，不得把发奖、转账、催付等钱相关动作塞进交互 Bot 高频入口。

---

## 6. 指令系统（command API）

**安全底线：普通指令只能由当前 UserBot 账号自己发出的 outgoing 消息触发。** 群成员、普通用户、频道消息等 incoming 消息不能直接触发插件 `commands`。`owner_only=False` 只表示插件的 `on_message` 可以监听普通成员消息，不表示开放指令执行权限。

**前缀底线：插件不能在用户可见文案、帮助、错误提示、配置默认值、预览或示例里硬编码英文逗号 `,` 作为指令前缀。** 指令名配置只保存裸命令名，例如 `game`、`help`、`cancel`；真正展示给用户时必须使用 `{prefix}` 占位符或运行时当前前缀拼接。

必须使用当前命令前缀的场景：

- 帮助/用法模板：写 `{prefix}{command}`，不要写 `,{command}` 或 `,game`。
- 错误提示里的示例：运行时用 `current_command_prefix()` 拼接，例如 `f"{current_command_prefix()}{command} 100"`。
- 配置页预览：从 `getSystemSettings().command_prefix` 注入 `{prefix}`，接口未返回时才用 `,` 兜底。
- `plugin.json` / `manifest.py` / `config_schema` 的默认模板：默认值应包含 `{prefix}`，不要包含固定 `,` 前缀。
- 交互 Bot、通知 Bot、定时任务或自动回复里展示“如何发送命令”的文字：同样使用 `{prefix}` 渲染。

允许保存为配置项的是“裸命令名”，不是完整命令文本：

```python
# 推荐：command 只保存裸指令名，模板使用 {prefix}
"command": {"type": "string", "default": "game"}
"help_message_template": {"type": "string", "default": "{prefix}{command} 100 - 开始一局"}

# 不推荐：默认值、帮助或预览写死英文逗号
"help_message_template": {"type": "string", "default": ",game 100 - 开始一局"}
```

红包、抢答、24 点、猜数字这类“公共参与 + 私有管理”的新插件，标准会话链路应先声明 `event_subscriptions`，由 Event Bus 接收玩家关键词、答案、callback、inline 和付款确认，再通过 `ctx.messages` 或标准 action 输出结果。下面的 `commands` / `on_message` 模型仅用于管理员命令兼容和仍未迁移的旧 hook 插件，不应作为公开玩法的新模板：

- 开局、发红包、撤销、强制结束、查看管理状态等管理动作优先声明为 `command` 事件；保留旧 hook 时才写成 `commands`，且只能由本账号 outgoing 指令触发。
- 领取口令、答题、参与投票等普通成员行为优先订阅 `message` / `callback_query` / `inline_query`；保留旧 hook 时才写在 `on_message`，通过普通文本判断，不要求用户发送系统指令前缀。
- 如果自动回复、定时任务等平台内部动作需要“代替本账号执行指令”，使用平台内部派发能力，不让普通 incoming 消息直接进 `commands`。
- 自动回复需要把群友输入的参数传给指令时，可以用变量模式：例如模式 `置顶 id=数字` 会匹配群友消息 `置顶 id=12345`，回复内容 `{prefix}pt {id}` 会使用 `12345`；游戏金额建议写 `num=数字`，可选参数写 `num=数字?`，`?` 表示这个 `num=...` 参数整体可以不填，默认值写 `{num|1000}`。熟悉正则时也可用模式 `^置顶\s+(\d+)$`、回复内容 `{prefix}pt {1}`。这些自动命令仍必须通过自动指令白名单，并受规则冷却、冷却对象和每人每日上限限制；冷却时间支持 `2s`、`2m`、`2h`、`2d`，纯数字按秒处理。自动命令成功后会按规则名称或 `usage_label` 把“今日已成功置顶促销 1/2 次”追加到结果底部；冷却中也会提示剩余 CD 和今日次数，达到每日上限时提示当日不可再用；管理员可回复群友消息发送 `{prefix}arcd`，或发送 `{prefix}arcd 用户ID` 重置当前会话相关的自动回复会话/用户冷却与该用户今日次数。

### 指令派发流程

1. 当前账号 outgoing 消息到达 → 检查前缀匹配
2. 提取指令名和参数
3. 检查别名（贪心最长匹配）
4. 遍历已注册插件，调用 `on_command(ctx, cmd, args, event)`
5. 第一个返回 True 的插件接管，后续不再传递

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
        # 兼容 NewMessage.Event 与裸 Message；Telethon 1.44 的方向字段是 message.out。
        msg = getattr(event, "message", event)
        if bool(getattr(msg, "out", False)):
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

插件收到的对象通常表现为 Telegram 消息事件，但在测试、热重载、代理属性等场景里，也可能表现得更像裸 `Message`。Telethon 1.44 的 `NewMessage.Event` 不提供 `event.outgoing`；方向应从事件的 `message.out`（或裸 `Message` 的 `out`）读取。因此建议用 `getattr` 做兼容，不要直接假设 `event.message.id` 一定存在：

```python
def event_message(event):
    return getattr(event, "message", event)

def event_text(event) -> str:
    msg = event_message(event)
    return str(getattr(event, "raw_text", None) or getattr(msg, "raw_text", None) or "").strip()

def is_outgoing(event) -> bool:
    msg = event_message(event)
    return bool(getattr(msg, "out", False))
```

这样可以避免类似 `'Message' object has no attribute 'outgoing'` 的运行时错误。

---

## 8. Conversation 工具（仅内部/真实客户端兼容）

`Conversation` 的实现会在进入上下文时调用客户端的 `add_event_handler`，发送和点击时还会
直接使用 TelegramClient/raw MTProto。它只适合 builtin 或其他明确持有真实
`TelegramClient` 的内部兼容代码。

普通安装插件的 `ctx.client` 是 `SandboxClient`：它不开放 `add_event_handler`，也会拒绝
`GetBotCallbackAnswerRequest` 等 raw MTProto，因此不能把 `ctx.conversation()` 当作可靠的
公开 API。`event.message.click(...)` 和 `message.buttons[row][column].click()` 都会被安全
包装拒绝。普通安装插件点击第三方 Bot callback 必须改用
`ctx.messages.click_callback_button(...)`；具体限制见
[Inline 按钮的两种完全不同场景](#inline-按钮的两种完全不同场景)。
`send_message` / `send_file` 受 UserBot 动作限速约束：被限速拒绝时抛
`PluginSendRateLimited`（含 `detail` / `retry_after`），不会静默返回 `None`。

以下示例仅用于真实客户端的内部兼容代码（如与 @BotFather 交互）：

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

> 不要把本节的 `click_button()` 复制到普通远程/本地安装插件；它不是安装插件 Contract。

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

插件日志会进入后台的“日志中心 → Runtime → 插件日志”分页，和“消息日志”“系统日志”分开显示；涉及 sudo、Config Bundle confirm、userbot_reply confirm 等安全决策的记录则在“日志中心 → Audit”查看。

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

## 13. 前端集成

插件前端配置推荐分为两种配置形态，另有一类平台内置基础能力。历史 `schema` 只作为兼容别名保留，不再新增“Schema 弹窗”类插件。后续新增插件时，优先通过 `manifest.py` 的 `config_schema["x-ui-mode"]` 声明分类，前端会自动归类展示。

### 配置形态概览

| 分类 | 适用场景 | 大白话 | 典型功能 | 配置入口 |
|------|---------|--------|---------|---------|
| **规则配置页** | 多条规则独立配置，需 CRUD + 试运行 | 像自动化流水线：先建规则，规则只保存配置和 dry-run 输入 | 插件库 auto_reply / autorepeat、远程规则插件 | 专属配置页 |
| **单配置对象 / 通用独立配置页** | 每个账号只保存一份插件配置，或轻量插件只需要字段表单 | 像一个工具面板：配置好触发指令和参数，直接运行；普通字段由 schema 驱动渲染 | 插件库 game24 / math10 / codex_image / chatgpt_image、简单远程插件 / 小工具插件 | 专属或通用独立配置页 |
| **基础能力 — 平台内置** | 系统运行时常驻能力，不作为普通插件展示 | 像底座服务：给插件或平台调用，不强调启停 | scheduler | 平台功能页 |

**关键判断**：需要维护多条规则 → `rules`；只有一份账号配置或普通字段表单足够 → `single`；旧插件已经写了 `schema` → 按 `single` 通用独立页兼容；像调度器这种系统服务 → `platform`。这里的 `rules` 只表示配置页/CRUD/dry-run 形态，不是旧运行时规则驱动主路径；标准会话事件投递仍以 Event Bus + `event_subscriptions` + 标准 action 为主。通用 schema 独立页只读写单个配置对象，不会自动提供 Rules API 的列表、新建、编辑、删除或 dry-run，因此不能用来替代真正的规则 CRUD 页。

#### 自动分类规则

新增插件应在 `config_schema` 顶层声明 `x-ui-mode`：

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
| `rules` | 规则配置页 | 多条规则配置插件，通常有规则列表、创建/编辑、dry-run；不改变标准会话链路的投递方式 |
| `single` | 单配置对象 / 通用独立配置页 | 单配置对象或通用独立配置页，字段可由 `config_schema` 驱动 |
| `schema` | legacy alias | 旧别名；不要在新插件中使用，不再表示弹窗类 |
| `platform` | 基础能力 | 平台内置能力，不混在普通插件列表里 |

前端统一从 `frontend/src/lib/plugin-modes.ts` 读取分类。旧内置插件仍保留 key fallback，但新插件不要依赖 fallback。

---

### 统一配置页样式规范

所有账号级插件配置入口都使用独立页面，不再新增 Schema 弹窗或内部分类的用户可见分组。账号详情的“插件启停”页只展示“基础能力 · 平台内置”和“插件”两组，插件列表按 `feature.key` 首字母排序；用户界面和文档统一称“插件”，代码、API、数据库字段和 Manifest 仍保留 `plugin` / `feature` 命名。

配置页从上到下固定为：

1. 返回按钮 + 插件标题
2. 使用说明
3. 功能总开关
4. 插件配置（规则列表或字段表单）
5. 插件预览（建议项；没有预览时显示轻量提示）

#### 配置操作条

长表单页必须把保存操作放在“插件配置”卡片底部的 sticky 工具条中，参考 `ChatGPTImageConfig.tsx`、`CodexImageConfig.tsx`、`Game24Config.tsx` 和 `GenericPluginConfig.tsx`：

```tsx
<div className="sticky bottom-0 z-20 mt-4 rounded-b-lg border-t bg-background/95 px-6 py-3 shadow-[0_-8px_20px_rgba(15,23,42,0.06)] backdrop-blur supports-[backdrop-filter]:bg-background/85">
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

规则配置页如果单条规则在 Dialog 内保存，主页面可以不放 sticky 保存条；但 Dialog 外的说明、总开关和规则列表顺序仍必须一致。

#### 使用说明卡片

“使用说明”必须是独立 `Card`，放在“功能总开关”之前。说明内容用一层 `rounded-md border bg-muted/20 p-3 text-xs text-muted-foreground` 包住，再用短 bullet 写真实用法、指令示例、触发条件和排障入口。不要把使用说明写成页面顶部散落的提示块，也不要把总开关塞进说明卡。

规则配置页复用 `RuleInfoBox`，单配置和通用 schema 页面直接使用同样结构。指令示例必须读取当前系统前缀和当前配置中的指令名，不要写死 `,draw`、`,24d`、`,cximg`。

通用 schema 配置页不再提供默认兜底说明。插件只要声明 `config_schema` 并进入配置页，就必须自带详细使用说明。推荐在 schema 顶层写 `x-usage-guide`、`x-usage-instructions` 或 `x-usage-steps`；也可以继续提供只读字段 `usage_preview`、`usage_guide`、`usage_instructions`、`ai_usage_guide`。缺少这些内容时，插件中心会显示红色“高级规范警告”，配置页也会用红色警告替代说明内容。

#### 功能总开关卡片

“功能总开关”也必须是独立 `Card`，放在“使用说明”之后、“配置”之前。卡片右侧放 `Switch`，左侧展示说明、启用 Badge、`state` 和 `last_error`。关闭总开关表示当前账号不运行该插件，但仍允许进入配置页提前填写配置。

规则配置页复用 `RuleFeatureToggleCard`；单配置和通用页面按同样布局实现。不要再使用旧的“运行状态”卡片替代总开关。

#### 插件配置与宽度

配置主体必须独立成“插件配置”或“规则”卡片，宽度跟随页面容器自适应，不要给表单区域加 `max-w-lg`、`max-w-3xl` 这类窄宽限制。字段多时用响应式网格：

- 普通字段：`grid gap-4 md:grid-cols-2 xl:grid-cols-3`
- 小型配置：`grid gap-6 md:grid-cols-2`
- 复杂分组：外层 `CardContent className="space-y-6"`，内部再分组

字段控件统一使用项目内 `Input`、`Select`、`Switch`、`Textarea`、`Label`、`Button`、`Card`、`Badge`、`Table`。指令字段只填指令名，不填系统前缀；密码、Token 和只读预览字段要遵守现有脱敏和只读规则。

通用 schema 页允许插件在平台容器内声明更自由的布局，但不能注入任意 HTML、外链样式或脚本。可用声明：

- `x-ui-section`：把字段放进同名分组。
- `x-ui-order`：控制字段排序，数值越小越靠前；未声明视为 0，相同值保持 `properties` 的声明顺序（服务器写库时记在 `x-ui-property-order`，Web 与 iOS 都按它还原，不按字母排序）。
- `required`：只列出真正必填、没有可用默认值且留空插件无法工作的字段；Web/iOS 会给它们标“必填”。有 `default` 的字段不要放进 `required`：留空或不提交时运行时按默认值补齐（默认值 < 全局 < 账号），清空成 `null` 的不可空字段也按“未设置”处理。
- `x-ui-columns`：控制分组列数，允许 1 到 3。
- 长配置页（多于 2 个分组且不少于 12 个字段）：标题含“基础 / 基本 / 常用 / 模型”的分组和未分组字段留在顶层，其余分组在 iOS 成为子页面入口、在 Web 默认折叠；组内任一字段声明 `x-ui-section-inline: true` 可让该分组留在顶层（`false` 则强制进子页面/折叠）。
- `x-ui-widget: "config-list"`：把 `array<object>` 渲染为多组配置列表，内置添加、编辑、复制、删除、启停和排序。
- `x-ui-widget: "multi-select"`：把枚举数组渲染为多选列表。
- `x-ui-widget: "list-select"`：把枚举字符串渲染为列表式单选。
- `x-ui-widget: "allowed-peer-multi-select"`：把 `array<integer>` 渲染为当前账号“允许会话”选择器，保存 Chat ID 数组；留空语义由插件自行定义，常用于群聊白名单。
- `x-ui-hidden: true`：隐藏兼容字段或内部字段，但仍保留保存链路。
- `config_actions` / `x-config-actions`：把插件后端动作渲染为字段旁按钮，动作只能调用插件的 `on_config_action`，不能执行任意前端脚本。

#### 插件预览

“插件预览”是独立 `Card`，位于“插件配置”之后。预览不是强制项，但强烈建议所有会发送消息的插件声明 `template_preview` 或 `*_preview`，让用户能用模拟上下文看到最终 Telegram 消息效果。没有预览字段时，通用配置页只显示建议提示，不阻断保存或运行。

#### 禁止回退

- 不新增 Schema 配置弹窗；当前入口是 `GenericPluginConfigPage` 独立页。`GenericPluginConfig.tsx` 可以复用 `ConfigDialog.tsx` 导出的 schema 解析/表单 helper 与类型，但 `ConfigDialog` 弹窗本身不是当前配置入口，也不是一种插件分类。
- 不在账号详情页展示内部分类名或 legacy schema 分组。
- 不把“使用说明”“功能总开关”“插件配置”“插件预览”合并到同一张卡片。
- 不把保存按钮放到页面顶部，或只放在滚动到底才能看到的位置。
- 不在用户界面继续使用“模块”指代可启停能力；面向用户统一称“插件”。

---

### 规则配置页：通用规则页（0.110.0-beta.2+，能力 `ui.rules_page`）

每条 rule 存储独立的 `config` JSON，通过 Rules CRUD API（`/api/accounts/{aid}/features/{key}/rules`）管理。**宿主不再为插件写专属规则页**：`config_schema["x-ui-mode"] = "rules"` 的插件由 Web `GenericPluginConfigPage` 的规则区与 iOS「自动化规则」统一渲染（规则列表 + 新建 / 编辑表单 + 试运行卡片），表单声明见下方「表单契约 2」。真正的 Telegram 消息投递仍走 Event Bus 的 `event_subscriptions`、标准事件信封和标准 action，规则页只管理配置数据。

```python
config_schema = {
    "type": "object",
    "x-ui-mode": "rules",
    "x-rule-summary": ["match", "patterns", "scope"],          # 列表每行显示的摘要字段（最多 3 个）
    "x-dry-run": {"sample_fields": ["message", "chat_type"],    # 试运行卡片要填的样例字段
                  "title": "试运行", "description": "不会真的发送。"},
    "properties": {"max_per_hour": {"type": "integer", "level": "global"}},   # 账号 / 全局配置（与规则无关）
    "x-rule-schema": {                                           # 每条规则 rule.config 的 schema
        "type": "object", "required": ["patterns", "reply"],
        "properties": {
            "scope": {"type": "string", "enum": ["private", "group_specific"], "enumNames": ["仅私聊", "指定群"]},
            "group_ids": {"type": "array", "items": {"type": "string"}, "x-ui-widget": "textarea",
                          "x-ui-visible-if": {"field": "scope", "equals": "group_specific"}},
            "patterns": {"type": "array", "items": {"type": "string"}, "x-ui-widget": "textarea"},
            "reply": {"type": "string", "x-ui-widget": "textarea"},
        },
    },
}
```

- 省略 `x-rule-schema` 时整个 `config_schema` 视为规则字段（例如 `autorepeat`），客户端不再把这些字段重复渲染成账号配置表单。
- `x-rule-schema` 写在 `config_schema` 里，旧宿主的 Manifest 校验会忽略未知 `x-*` 键，旧客户端照旧显示原表单；新插件不必为此抬高 `min_telepilot_version`。
- 旧专属页（`AutoReply` / `Autorepeat` / `ChatGPTImageConfig` / `CodexImageConfig` / `Game24Config`）在 0.110.0-beta.2 移到 `/accounts/:aid/features/:key/legacy`，带横幅与「对照旧版页面」入口，保留一个版本用于对比，之后删除；没有声明规则 schema 的旧版 `auto_reply` 自动落到旧页，功能不变。不要再新增专属规则页，也不要改 `App.tsx` 路由或兜底名单。
- `forward`（转发）与 `scheduler`（定时任务）是宿主内置功能，规则 schema 与试运行由宿主自己解释。

### 规则试运行协议：`dry_run_rule`（能力 `rules.dry_run`）

`POST /api/accounts/{aid}/features/{key}/rules/{rid}/dry-run`（请求体 `{sample_message, sample_chat_type, sample_chat_id}`）由 `services/rule_dry_run_service.plugin_dry_run` 处理，REST 与 System Agent 共用。插件包导出一个纯函数即可，**不要改 `rules.py`**：

```python
# 插件包 plugin.py（并在 __init__.py 里 from .plugin import dry_run_rule）
def dry_run_rule(config: dict, sample: dict) -> dict:
    """sample = {"message": str, "chat_type": "private" | "group" | "channel", "chat_id": int | None}"""
    hit = any(p in sample["message"] for p in config.get("patterns", []))
    return {
        "matched": hit,
        "output": config.get("reply") if hit else None,          # 命中后将发送的内容（渲染后）
        "logs": [{"step": "match", "msg": f"模式 {len(config.get('patterns', []))} 条"}],  # 显示在「判断过程」
    }
```

- 可以是 `async def`；宿主限时 **3 秒**，超时或抛异常返回明确的失败原因，不影响规则本身。也接受返回 `(matched, output)` 元组。
- 必须无副作用：不发消息、不访问 DB / Redis / 网络，判断逻辑与 `on_message` 共用同一个函数，并在插件自己的测试里覆盖。
- 插件未安装、全局关闭、签名不合规或没有导出时，接口返回明确提示，不会从内置路径兜底。
- **旧协议兼容**：没有 `dry_run_rule` 时回退旧的 `_dry_run_match(cfg, text, chat_id)`；各旧插件的参数形状与日志文案登记在 `app/plugin_compat.py`（`legacy_dry_run_args` / `legacy_dry_run_logs`），宿主的服务代码里不再按插件 key 分支。新插件只写 `dry_run_rule`。
- 插件可用 `config_schema["x-dry-run"] = false` 关闭试运行入口。

---

### 单配置对象页（Game24 / Codex Image）

只有一份配置、无规则列表的插件，由通用 `GenericPluginConfigPage`（Web）与 iOS 插件配置页按 `config_schema` 渲染，不需要 CRUD 和 dry-run：

- `manifest.py` 中声明 `config_schema["x-ui-mode"] = "single"`，字段用配置控件契约里的 `x-ui-*` 声明（多 Token 用 `x-secret-list`、需要校验用 `on_config_validate`）
- 0.110.0-beta.2 起不再新增专属 React 页面，也不改 `App.tsx` 路由或兜底名单；`game24` / `codex_image` / `chatgpt_image` 的旧专属页只在 `…/legacy` 对照路由保留一个版本
- 不需要导出 `dry_run_rule`

#### 页面布局约定

通用配置页遵守“统一配置页样式规范”，页面从上到下固定为：

1. 返回按钮 + 插件标题
2. 使用说明（真实触发指令示例、参数示例、注意事项）
3. 功能总开关（当前账号是否启用、关键运行状态、最近错误）
4. 插件配置（账号级配置为主，必要时展示全局配置；保存条固定在卡片底部）
5. 插件预览（模板预览是建议项，没有预览时给出提示）

“使用说明 → 功能总开关 → 插件配置 → 插件预览”要作为独立卡片，不要把总开关塞进说明或配置里。单配置插件通常靠指令触发，用户最关心的是“怎么叫它”“现在能不能用”“要改哪些参数”“最终发出去是什么样”，所以顺序保持稳定。配置字段要按可用屏幕宽度展开，避免窄表单造成长配置反复滚动。

#### 指令型插件配置

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
            "description": "跟在系统指令前缀后使用，支持中文；不要包含空格。",
        },
    },
}
```

- `command_config_keys` 用于告诉 loader：指令字段变化后要重启该插件并重新注册指令。
- 指令名支持中文，例如 `,画图 一只猫`；但不能包含空格，因为指令解析以第一个空白分隔指令和参数。
- 说明文案必须用当前配置中的指令动态生成，不要把 `,cximg`、`,24d` 写死。

#### 已有单配置插件字段参考

| 插件 | 推荐字段 | 说明 |
|------|---------|------|
| `game24` | `command`, `timeout` | 触发指令名、答题限时 |
| `codex_image` | `command`, `access_token`, `model`, `message_template`, `image_size`, `aspect_ratio`, `image_format`, `max_wait_seconds`, `status_interval_seconds`, `delete_command_message`, `show_revised_prompt`, `reasoning_effort`, `custom_instructions` | 触发指令、鉴权、模型、消息模板、图片尺寸/比例/格式、等待与状态提示、输出行为、自定义生成指令 |

配置字段应与运行时实际读取的配置保持一致；`manifest.config_schema` 也要同步，避免通用配置页、接口校验和文档出现三套口径。

`codex_image` 现在是插件库图片插件，源码由插件库分发，用户需在“安装插件”页安装后才会复制到 `plugins/installed/codex_image/` 并加载。旧数据库中已经启用或保存配置的 `codex_image` 会在 seed 阶段尝试从插件库登记为 repo installed 插件，保留账号配置和规则引用；未使用过的旧 builtin feature 行会被清理，避免误展示。

---

### 通用 Schema 驱动独立页（legacy schema 兼容）

不再新增“Schema 弹窗”类插件。历史上无专属页面的插件可能声明 `x-ui-mode: "schema"`，现在应把它理解为“由 `config_schema` 提供字段的通用单配置独立页”：

- `level: "global"` 的字段 → 全局配置区
- `level: "account"` 或无 level → 账号配置区
- **不需要**添加到 `FEATURE_CONFIG_PAGE_KEYS`，不需要创建插件专属页面文件
- 新插件请优先写 `config_schema["x-ui-mode"] = "single"`；`schema` 只保留为旧插件兼容别名
- 页面同样使用“使用说明 → 功能总开关 → 插件配置 → 插件预览”的独立卡片顺序，并在有可保存字段时把“配置操作”条固定在插件配置卡片底部
- 页面宽度、滚动高度、字段间距和控件风格应与 ChatGPT2API / 自定义指令 / LLM 等系统配置页保持一致：使用统一的 `Input`、`Select`、`Switch`、`Textarea`、`Label` 视觉语言，不在字段标题里放 emoji 或临时说明块
- 普通配置字段展示在配置区顶部；`message_template` / `*_message_template` / `*_template` 等消息模板字段进入“消息模板”折叠组；`template_preview` / `*_preview` 进入独立“插件预览”卡片。
- `message_template`、`*_message_template`、`prompt`、`content`、`text` 等长文案字段会按多行文本体验展示；字段描述里应写清占位符和示例值。
- `field.readOnly === true`、`template_preview`、`*_preview`、`template_placeholders` 会自动按只读块渲染，不会保存回配置；其中预览字段使用 `TelegramHtmlPreview` 展示最终 HTML 消息效果。
- 多个预览字段应在同一个 Telegram 风格预览场景里按字段顺序展示为多条气泡，方便同时检查开局、进行中、答对、超时、取消和错误提示等模板。
- `usage_preview` / `usage_guide` / `usage_instructions` / `ai_usage_guide` 只用于“使用说明”卡片，不会再出现在插件配置字段区；`template_placeholders` 只作为只读占位符说明，不算详细使用说明。
- 配置布局可使用 `x-ui-section`、`x-ui-order`、`x-ui-columns` 在平台容器内做分组、排序和列数控制。
- 这个页面只编辑一份账号/全局配置对象，不提供 Rules API 的多条列表、创建、编辑、删除或 dry-run；规则插件不能用通用 schema 页代替专属 CRUD。

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

基础能力不是普通插件卡片，而是系统运行时一起初始化的服务。比如 `scheduler` 现在属于平台内置调度能力：页面仍可配置定时任务，但不再强调“作为插件启停”。

适配规则：

- `manifest.py` 声明 `config_schema["x-ui-mode"] = "platform"`
- 前端会在账号详情和插件中心里放到“基础能力 / 平台内置”分组
- 如果有专属页面，仍需 `App.tsx` 路由和共享 `FEATURE_CONFIG_PAGE_KEYS`
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
- 第三方插件拿到的是 scheduler facade，不会直接获得 DB 或 userbot session；安装插件的 `ctx.redis` 是自动命名空间、方法受限的 `PluginRedisFacade`，不是原始 Redis client
- GUI 定时任务页仍走 `Rule(feature_key="scheduler")`，由同一个 `PlatformScheduler` 调度；后续新增插件不要依赖 `SchedulerPlugin`，只依赖 `ctx.scheduler`

---

### 风格要求

- 与 TelePilot 现有页面风格一致
- React + TypeScript + TailwindCSS
- 新页面参考 `AutoReply.tsx`（规则配置页）、`Game24Config.tsx` / `CodexImageConfig.tsx` / `ChatGPTImageConfig.tsx`（单配置）或 `GenericPluginConfig.tsx`（通用 schema）的代码结构
- 使用说明、功能总开关、插件配置、插件预览必须是独立卡片，顺序固定为“使用说明 → 功能总开关 → 插件配置 → 插件预览”
- 有可保存字段的长表单必须在插件配置卡片底部使用 sticky“配置操作”条，按钮文案统一为“保存配置”“撤销”
- 配置区域宽度随页面自适应，不使用窄 `max-w-*` 限制；字段多时使用响应式 grid
- 表格列宽要稳定，账号详情页和插件中心的同类列表要纵向对齐
- 配置按钮不依赖启用状态；即使插件当前关闭，也应允许先配置
- 用户界面和文档统一称“插件”，开发文档、API、代码标识可以继续使用 plugin / feature

### 适配自检清单

新增插件前端配置页后，逐项检查：

- [ ] `manifest.py` 中 `config_schema["x-ui-mode"]` 已声明：推荐 `rules` / `single` / `platform`；仅旧插件保留 `schema`
- [ ] `config_schema` 已声明详细使用说明：优先使用 `x-usage-guide` / `x-usage-steps`，或只读 `usage_preview`
- [ ] `types.ts` 中 `XxxRuleConfig` 接口与 `manifest.py` config_schema 字段一致
- [ ] 如果有专属页面：`App.tsx` 中路由路径 `:aid/features/{key}` 与插件 key 一致
- [ ] 如果有专属页面：`frontend/src/pages/Plugins/_shared/featureConfig.ts` 的 `FEATURE_CONFIG_PAGE_KEYS` 包含该 key
- [ ] 如果是指令型插件：`command` 字段可配置，`Plugin.command_config_keys = {"command"}`，说明文案动态读取当前指令
- [ ] 指令型插件的帮助、取消/结束、撤销、自动删除、冷却/超时、消息模板等用户常调行为已尽量配置化；帮助模板支持 `{prefix}`，不硬编码 `,命令`
- [ ] `owner_only=False` 仅用于开放 `on_message`，没有把普通 incoming 消息当成管理指令入口
- [ ] 页面按“使用说明 → 功能总开关 → 插件配置 → 插件预览”的独立卡片顺序排布；不要把说明、总开关、配置和预览混在一张卡片
- [ ] 有可保存字段的页面在插件配置卡片底部使用 sticky“配置操作”条；长配置不只在滚动到底才能保存
- [ ] 如会发送消息，建议提供 `template_preview` 或 `*_preview`；没有预览不会阻断运行，但会降低配置体验
- [ ] 配置主体宽度自适应屏幕宽度，字段用响应式 grid 或分组，不使用窄 `max-w-*` 限制
- [ ] 用户可见文案使用“插件”，不展示内部分类名或“Schema 弹窗”
- [ ] 如需 dry-run：`plugin.py` 导出 `_dry_run_match`，`__init__.py` re-export；平台 `rules.py` 会动态加载，无需改主仓库
- [ ] `_dry_run_match` 是纯函数，并在插件自身测试中覆盖
- [ ] 前端 `pnpm --dir frontend typecheck` 和 `pnpm --dir frontend build` 通过

---

## 15. 调试建议

### 快速自检

- [ ] `__init__.py` 是否导出 `PLUGIN_CLASS` 和 `MANIFEST`
- [ ] `MANIFEST.key` 是否和插件 class key 一致
- [ ] 新 Telegram 交互插件是否声明了 `usage`、`event_subscriptions`、`capabilities`
- [ ] 插件主入口是否读取标准事件信封，例如 `payload["message"]`、`payload["chat"]`、`payload["sender"]`、`payload["payment"]`
- [ ] 发送、编辑、删除、置顶、按钮 ACK、Inline answer、结算是否通过 `ctx.messages` 或标准 action，而不是直接调用 live client
- [ ] 是否区分 Interaction Bot callback ACK 和 UserBot 主动点击第三方 Bot 按钮；后者使用 `ctx.messages.click_callback_button(...)`，没有混用 `answer_callback`
- [ ] legacy `on_message` 若需接收普通成员/第三方 Bot，是否同时设置 `message_channels={"incoming"}` 与 `owner_only=False`
- [ ] 账号级“允许会话”留空是否按“全部会话”理解；有没有误套到插件自定义 `allowed_chat_ids=[]`
- [ ] 自动点击第三方 Bot callback 是否声明 `click_bot_button`，并传入 `expected_bot_id`、`expected_button_text`；没有读取/传入 callback data，也没有尝试点击 URL 等非 callback 按钮
- [ ] 日志页是否能用 `trace_id`、`plugin_key`、`reason_code` 查到订阅匹配、插件执行和动作结果
- [ ] `permissions` 是否覆盖实际调用的方法
- [ ] 如果保留旧管理员命令 hook，`on_command` 签名是否是 5 参数；不要把旧 hook 当作公共玩法的新入口
- [ ] 错误是否都被捕获并反馈给用户

### 为什么我的 Event Bus / on_interaction 没被调用

按这条顺序排查，基本能定位 90% 的插件启动问题：

- `InstalledPlugin.enabled`：远程插件是否已安装并启用（旧 `RemotePlugin` 表仅作只读兼容）。
- `AccountFeature.enabled`：当前账号是否启用了这个插件。
- `plugin.json` / `manifest.py` 是否声明了 `event_subscriptions`，事件来源、事件类型、scope 和 filters 是否覆盖当前输入。
- 日志页按 `trace_id` 或消息 ID 搜索，查看 `subscription_match` 的 `reason_code`：`source_not_subscribed`、`event_type_not_subscribed`、`scope_not_matched`、`filter_not_matched` 通常能直接说明未触发原因。
- 需要原生字段的插件是否声明 `capabilities.telegram_native_raw.enabled=true`；未声明时读取不到 `native_raw` 是正常边界。
- 返回 action 后，日志页是否有 `event_action`；如果出现 `send_channel_deprecated`，说明插件还在请求旧 `notice` / `bbot_notice` 通道。
- 如果仍使用旧 `interaction_entries` / `on_interaction` 兼容层，再检查规则动作是否是 `action == "module"`，`module_key` 是否和 `MANIFEST.key` 完全一致，`module_action` 是否等于 `interaction_entries[].key`。
- 当前群 `chat_id` 是否在规则 `chat_ids` 内；未配置时才表示所有群。
- 触发模式是否匹配：付费通知走 `payment_confirmed`，免费关键词走 `keyword`，已有会话后的群消息才走 `message`。
- 群局兼容入口是否声明了 `interaction_entries[].session_scope = "chat"`；如果漏写，规则设置 `concurrency=user` 后，后续群友消息可能找不到会话。
- 用户私有流程是否声明了 `session_scope = "user"`，并在插件内部状态 key 中包含用户 ID。
- worker 是否在线；离线时交互 Bot 会返回“插件启动失败：worker 调用超时”。
- 日志页搜索 `plugin_runtime_status`、`run_interaction_entry`、`interaction module`、`unsupported type`、`result_contract`，未知 action type 或越权 `send_via` 会写入 trace/runtime log，便于发现返回了平台尚不支持或未声明的动作。

### 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| 插件被跳过 | MANIFEST 类型不对或导出缺失 | 检查 `__init__.py` |
| 指令没反应 | feature 未启用或前缀不匹配 | 检查 rule 配置和前缀 |
| 热重载后旧 handler 还在触发 | generation guard 未生效 | 检查 loader.py 版本 |
| 远程插件安装失败 | plugin.json 缺必填字段或格式不合法 | 检查 name/description/version/entry |
| 群友回复数字/答案没反应 | 群局入口漏写 `session_scope=chat`，或规则没有保存活跃会话 | 补齐 `plugin.json` / `manifest.py` 的 `interaction_entries[].session_scope`，检查规则有效期 |
| cleanup 后插件状态异常 | cleanup 未幂等 | 重复调用测试 |

---

## 17. 完整示例

标准 Telegram 交互插件请优先参考 `examples/plugins/event_bus_demo`，它覆盖 message、command、callback、inline、payment、`native_raw` 和旧 `notice` 迁移错误。下面的天气查询插件是 **管理员命令型兼容示例**，用于说明旧 `on_command` API 和前缀处理；它不应作为公共群玩法或高频交互插件的新模板。

### 天气查询插件（管理员命令兼容）

```python
# manifest.py
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="weather",
    display_name="天气查询",
    version="1.0.0",
    min_telepilot_version="0.72.0-beta.2",
    author="community",
    description="查询天气信息，支持城市名",
    permissions=["send_message"],
    requires_platform_capabilities=[],
    config_schema={
        "type": "object",
        "x-ui-mode": "single",
        "properties": {
            "command": {
                "type": "string",
                "title": "触发指令名",
                "default": "weather",
                "minLength": 1,
                "maxLength": 32,
                "pattern": "^\\S+$",
            },
            "default_city": {
                "type": "string",
                "title": "默认城市",
                "default": "Beijing",
            },
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
    command_config_keys = {"command"}

    def _command(self, ctx) -> str:
        return str(ctx.config.get("command") or "weather").strip()

    async def on_command(self, ctx, cmd, args, event) -> bool:
        if cmd != self._command(ctx):
            return False

        city = " ".join(args) if args else str(ctx.config.get("default_city") or "Beijing")
        try:
            # 第三方插件发布时应声明 external_http + allowed_hosts，并优先使用 ctx.http。
            # 这里保留直接 httpx 调用只是为了展示旧管理员命令兼容写法。
            async with httpx.AsyncClient(timeout=10.0) as client:
                geo = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": city, "count": 1},
                )
                if not geo.json().get("results"):
                    await event.edit(f"未找到: {city}")
                    return True
                lat = geo.json()["results"][0]["latitude"]
                lon = geo.json()["results"][0]["longitude"]

                weather = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={"latitude": lat, "longitude": lon, "current_weather": True},
                )
                data = weather.json()["current_weather"]
                temp = data["temperature"]
                wmo = data["weathercode"]

                await event.edit(f"{city}: {temp}°C (天气代码: {wmo})")
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

- **稳定契约 1.0**（0.112.0-beta.4 起）：以 Plugin API 1.6 时的接口面为基线冻结，契约 1.x 期间只增不减。契约版本独立于 `PLUGIN_API_VERSION`（后者继续递增，`plugin_api` 范围的写法与判断不变）。冻结范围、承诺与 CI 检查见 [PLUGIN-CONTRACT.md](PLUGIN-CONTRACT.md)，机器可读快照在 `schemas/plugin-api-contract.json`。 0.115.0-beta.2（Plugin API 1.7）起插件可读 `ctx.capabilities.contract_version` 得到契约版本，见 [契约版本探测](#契约版本探测contract_version)；冻结准备与兼容层清单见 [PLUGIN-API-1.0-PLAN.md](PLUGIN-API-1.0-PLAN.md)（草案）。
- `0.x`：开发阶段，允许快速迭代；`1.x`：接口稳定后。
- 只依赖 `Plugin` / `Manifest` / `PluginContext` 与本文列出的 facade 公开契约，不要依赖私有内部插件路径；新增行为优先通过 `config` 可选项实现。

### `min_telepilot_version` 怎么填

- 宿主在执行插件 Python 之前比较 `plugin.json` 的 `min_telepilot_version`（旧名 `min_telebot_version`，已弃用，见 [弃用登记](#弃用登记)），版本不够时拒绝加载并提示“请先更新 TelePilot”。
- **0.107.0-beta.1 起比较考虑 beta 号**（SemVer 规则，`manifest.min_version_prerelease`）：
  - 声明带 beta 号（如 `0.106.0-beta.12`）→ 完整比较：`0.106.0-beta.3` 宿主拒绝，`0.106.0-beta.12`、`0.106.0`、`0.107.0-beta.1` 都满足；
  - 声明不带 beta 号（如 `0.106.0`）→ 仍只比较 `x.y.z`，`0.106.0-beta.3` 宿主也满足（与旧行为一致，已发布插件不会在 beta 宿主上被误拒）。
  - 0.107 之前的宿主只比较 `x.y.z`：在旧宿主上 `0.106.0-beta.12` 与 `0.106.0-beta.1` 效果相同，所以需要某个 beta 才有的能力仍要同时做能力探测。
- 最低版本要覆盖插件**不做探测就直接使用**的能力。0.106.0-beta.2 之后新增的能力，门槛以宿主能力注册表为准（`backend/app/worker/plugins/capabilities.py`，`GET /api/plugins/capabilities` 返回同一份数据的 `since` 字段）；下表把注册表与更早的 CHANGELOG 门槛汇总在一起：

| 能力 | 最低版本 |
| --- | --- |
| `ctx.messages` / 标准 action | 0.33.0 |
| `event_subscriptions` / `on_event` | 0.40.0 |
| `config_actions` / `on_config_action` | 0.45.0 |
| `on_direct_message`（`telegram_direct_passthrough`） | 0.46.0 |
| `ctx.messages.send_photo` / `send_file` / `edit_caption` | 0.49.13 |
| `ctx.ai.stream_complete` | 0.51.0 |
| `ctx.storage`、`ai_components`（QuizMaker / AnswerJudge / PersonaChat） | 0.54.0 |
| `ctx.ai.run_agent`（`ai_agent`） | 0.56.0 |
| `ctx.data_dir` | 0.59.1 |
| `ctx.messages.send_rich` / `send_rich_message` | 0.60.0 |
| `ctx.identities` | 0.60.6 |
| `ctx.messages.edit_rich` | 0.67.1 |
| `Manifest(requires_platform_capabilities=...)` | 0.72.0-beta.2（更早版本导入 manifest 即 `TypeError`） |
| `ctx.messages.click_callback_button`（`click_bot_button`） | 0.90.0 |
| `http.allow_direct` | 0.97.0 |
| `ctx.skills`（`agent_skills`）、识图（`images=` / `fetch_bot_image` / `ai_vision`）、`web_search`、访客模式（`guest_message` / `guest_all` / `answer_guest_query`）、`timeout_seconds` | 0.106.0-beta.1（`complete()` 自 0.73 起忽略未知关键字参数：旧平台上传 `images` / `web_search` / `thinking` 不报错但不生效） |
| `ctx.ai.complete(thinking=...)`、`AIResponseTruncated`；注册表 `messages.inline_rich_edit` | 0.106.0-beta.2 |
| 注册表 `messages.live_inline_edit`（实时 inline 编辑、`apply()` 结果的 `errors`） | 0.106.0-beta.3 |
| 注册表 `messages.rich_table_compact`（`compact_tables`，Bot API 10.3 紧凑表格） | 0.106.0-beta.12 |
| Event Bus payload 的 `owner_user_ids` / `admin_user_ids`（交互 Bot 投递）、`ctx.ai.list_providers()` 模型 `enabled` | 0.106.0-beta.14（探测：字段是否存在） |
| 注册表 `messages.rich_table_default_style`（Rich 表格默认边框 + 隔行着色，平台行为，插件无需声明） | 0.106.0-beta.15 |
| 注册表 `messages.guest_reply_markup`（访客回复 callback 按钮、`apply()` 结果的 `inline_message_id`、inline 回调投递给 `guest_all` / `inline_all`、`inline_callback` 信封） | 0.106.0-beta.17 |
| 注册表 `messages.streaming_edit`（`ctx.messages.stream_editor()` / `stream_edit()` 流式编辑 / 私聊草稿，见 [PLUGIN-STREAMING.md](./PLUGIN-STREAMING.md)）；`ctx.ai.complete()` 的 `timeout_seconds` 改为含重试与备用 Provider 的总时限 | 0.106.0-beta.20 |
| 注册表 `runtime.capabilities`（`ctx.capabilities`）、`manifest.plugin_api`、`manifest.min_version_prerelease`、`runtime.hook_stats`、`runtime.deprecations` | 0.107.0-beta.1 |
| 通用交互原语（Plugin API 1.1）：`messages.callback_routing`（按钮 `{"callback": {...}}` + 订阅 `own_buttons`）、`messages.unified_ops`、`messages.bot_api_extra`、`ai.extra_body`、`host.read`（`plugin.json` 的 `reads` + `ctx.host.read`）及 4 个 `host.read.*@1` 资源、`runtime.hook_trace`、`runtime.entry_policy` | 0.108.0-beta.1（旧宿主把不认识的 `own_buttons` 当 `all_allowed_chats`、忽略 `reads`，没有 `ctx.host`） |
| 消息管线中间件（`plugin.json` 的 `middleware`、`render_profile`）、`stream_complete(extra_body=)`、钩子统计 `blocked` / `blocking_ms` | 0.109.0-beta.1（旧宿主忽略 `middleware`，插件照常加载但中间件不生效；需要时用 `plugin_api: "^1.2"` 拒绝旧宿主） |
| 声明式 UI（Plugin API 1.3）：`ui.cards`（`plugin.json` 的 `ui.cards` + `on_ui_card` / `on_ui_action`）、`config.validate`（`on_config_validate`）、`config.saved`（`on_config_saved`）、`config.secret_list`（`x-secret-list`）、`ui.widgets`（配置控件契约 1） | 0.110.0-beta.1（旧宿主忽略 `ui` 声明、不调用这些钩子；旧客户端把不认识的控件按普通字段显示） |
| 表单契约 2：`ui.rules_page`（`x-ui-mode: rules` 通用规则页 + `x-rule-schema` / `x-rule-summary` / `x-dry-run`）、`rules.dry_run`（插件导出 `dry_run_rule`）、字段级 `x-ui-visible-if`、数组 `x-ui-widget: textarea` 每行一项 | 0.110.0-beta.2（iOS 0.14.1 起；旧客户端忽略这些声明，字段照常全部显示） |
| 转账通知解析扩展点：`payment.sources`（`plugin.json` 的 `payment_sources` + 模块导出 `parse_payment_notice(text, meta)`，见 [转账通知解析（payment_sources）](#转账通知解析payment_sources)） | 0.111.0-beta.1（旧宿主忽略 `payment_sources`，插件装上不会被调用） |
| 插件页面与菜单（Plugin API 1.4）：`ui.pages`（插件自带 HTML 页面 + Bridge）、`ui.actions`（菜单项）、`hooks.storage_read`（卡片 / 配置钩子 / 页面动作里只读 `ctx.storage`）、`ai.models.metadata`（`ai.models@1` 只增显示名 / 可用状态 / 成本档） | 0.112.0-beta.1（iOS 0.15.0 起显示页面与菜单；旧宿主忽略 `ui.pages` / `ui.actions`，卡片钩子里 `ctx.storage` 为 None；旧 App 不显示新入口、不报错） |
| 业务下沉（Plugin API 1.5）：`interaction.entry_policy`（`interaction_entries[].entry_policy`）、`provides.ai.image_backend`（`plugin.json` 的 `provides`）、`ui.rule_editor`（`ui.rule_editor` + 卡片 slot `interaction.rule_editor`） | 0.112.0-beta.2（旧宿主忽略这三项声明；顶层字段照写即可两边一致） |
| 旧入口 key 声明（Plugin API 1.6）：`interaction.legacy_entry_keys`（`interaction_entries[].legacy_entry_keys`，见 [旧入口 key 声明](#旧入口-key-声明legacy_entry_keys)） | 0.112.0-beta.3（旧宿主忽略该字段、照旧走兜底入口表，结论相同；不要为它提高最低版本） |
| 契约版本探测（Plugin API 1.7）：`runtime.contract_version`（`ctx.capabilities.contract_version`、`GET /api/plugins/capabilities` 的 `contract_version`，见 [契约版本探测](#契约版本探测contract_version)） | 0.115.0-beta.2（旧宿主没有该属性，用 `getattr(..., None)` 回退） |

- 想兼容更旧的平台时不要提高最低版本，而是先探测再用，见下一节 [能力协商与 Plugin API 版本](#能力协商与-plugin-api-版本)。
- 声明 `requires_platform_capabilities` 的 `manifest.py` 在 0.72 之前的平台上导入就会失败，因此现在所有新插件的最低版本至少是 `0.72.0-beta.2`。
- 插件仓库 `Anoyou/telebot-plugins` 提供 `scripts/check_host_compat.py`：用本仓库真实的安装校验、loader、沙箱权限与 facade 签名逐个检查插件，并按上表推断最低版本；`TELEPILOT_REPO` 指向本仓库任意历史版本的检出即可验证插件在旧平台能否加载。示例插件也可以用 `--plugins-root examples/plugins` 检查。
- 主仓库 PR 也会反向检查：工作流「插件库兼容」（`.github/workflows/plugin-compat.yml` + `scripts/plugin-compat-check.py`）用本次提交的宿主跑插件库的 `check_host_compat.py` 与全量 pytest；有失败时再用目标分支的宿主重跑，只有“基线通过、本次失败”才判失败，插件库自身已有的失败只给警告。

### 能力协商与 Plugin API 版本

TelePilot 0.107.0-beta.1 起，宿主的可探测能力只在一张注册表里登记（`backend/app/worker/plugins/capabilities.py` 的 `CAPABILITIES`）：每项能力有名称、版本、首个提供它的宿主版本（`since`，可带 beta 号）、适用的 `ctx.messages` facade、兼容用的旧属性名与说明。注册表同时驱动三处：

- 运行时：`ctx.capabilities`；
- 接口：`GET /api/plugins/capabilities`（返回 `host_version`、`plugin_api_version`、`capabilities[]`、`deprecations[]`；0.115.0-beta.2 起另有 `contract_version` / `contract_baseline_plugin_api`；0.115.0-beta.3 起另有 `compat_usage` 探测标志；0.109.0 起另有 `host_resources[]`（`ctx.host.read` 可读资源，含模型清单 `ai.models@1`）、`core_middleware[]` 与 `middleware_limits`）；
- 文档：上面的版本门槛表。

**探测能力（推荐写法）**

```python
caps = getattr(ctx, "capabilities", None)  # 0.107 之前的宿主没有 ctx.capabilities
if caps is not None:
    can_button = caps.has("messages.guest_reply_markup")
else:  # 旧宿主：退回旧属性探测
    can_button = getattr(ctx.messages, "guest_reply_markup", False) is True
```

| 方法 / 属性 | 说明 |
| --- | --- |
| `caps.has(name, min_version=1)` | 当前上下文是否支持（版本不低于 `min_version`） |
| `caps.version(name)` | 能力版本；不支持或当前 facade 不适用时为 `0` |
| `caps.names()` / `caps.as_dict()` | 当前上下文支持的全部能力 |
| `name in caps` | 等价于 `caps.has(name)` |
| `caps.plugin_api` / `caps.host_version` | 宿主 Plugin API 版本（如 `1.7`）与 TelePilot 版本 |
| `caps.contract_version` | 宿主承诺的插件稳定契约版本（如 `1.0`）；0.115.0-beta.2 起，旧宿主没有该属性，用 `getattr(caps, "contract_version", None)` |

- `messages.*` 能力与当前 `ctx.messages` facade 有关：同一插件在交互 Bot 入口（`entry`）、命令 / 后台任务（`live`）、UserBot Event Bus 投递（`buffered`）里拿到的 facade 不同。例如 `messages.live_inline_edit` 在 `buffered` facade 上为 `False`，因为那里只能缓冲动作、没有实时 apply。`runtime.*` / `manifest.*` 是宿主级能力，与 facade 无关。
- 旧探测写法继续可用：`getattr(ctx.messages, "rich_table_compact", False)`、`inline_rich_edit`、`live_inline_edit`、`rich_table_default_style`、`guest_reply_markup` 这些类属性现在由注册表生成，与 `ctx.capabilities` 结果一致。注册表之外的探测（`ctx.skills is None`、`getattr(ctx.ai, "vision_enabled", False)`、`hasattr(ctx.messages, "edit_rich")`、参数级新增用 `inspect.signature` 或捕获 `TypeError`）照旧。
- 当前注册表全集：

| 能力名 | 旧属性 | 适用 facade | since |
| --- | --- | --- | --- |
| `messages.inline_rich_edit` | `inline_rich_edit` | 全部（0.108.0 起含 buffered） | 0.106.0-beta.2 |
| `messages.live_inline_edit` | `live_inline_edit` | live / entry | 0.106.0-beta.3 |
| `messages.rich_table_compact` | `rich_table_compact` | 全部 | 0.106.0-beta.12 |
| `messages.rich_table_default_style` | `rich_table_default_style` | 全部 | 0.106.0-beta.15 |
| `messages.guest_reply_markup` | `guest_reply_markup` | 全部 | 0.106.0-beta.17 |
| `runtime.capabilities` | — | 宿主级 | 0.107.0-beta.1 |
| `manifest.plugin_api` | — | 宿主级 | 0.107.0-beta.1 |
| `manifest.min_version_prerelease` | — | 宿主级 | 0.107.0-beta.1 |
| `runtime.hook_stats` | — | 宿主级 | 0.107.0-beta.1 |
| `runtime.deprecations` | — | 宿主级 | 0.107.0-beta.1 |
| `messages.streaming_edit` | `streaming_edit` | live / entry | 0.106.0-beta.20 |
| `messages.callback_routing` | — | 全部 | 0.108.0-beta.1 |
| `messages.unified_ops` | — | 全部 | 0.108.0-beta.1 |
| `messages.bot_api_extra` | — | 全部 | 0.108.0-beta.1 |
| `ai.extra_body` | — | 宿主级 | 0.108.0-beta.1 |
| `host.read` | — | 宿主级 | 0.108.0-beta.1 |
| `host.read.account.admins@1` / `host.read.ai.models@1` / `host.read.chat.allowed@1` / `host.read.plugin.stats@1` | — | 宿主级 | 0.108.0-beta.1 |
| `runtime.hook_trace` | — | 宿主级 | 0.108.0-beta.1 |
| `runtime.entry_policy` | — | 宿主级 | 0.108.0-beta.1 |
| `middleware.outbound` / `middleware.inbound` / `middleware.delivered` / `middleware.stream_preview` | — | 宿主级 | 0.109.0-beta.1 |
| `messages.render_profile` | — | 全部 | 0.109.0-beta.1 |
| `ai.stream_extra_body` | — | 宿主级 | 0.109.0-beta.1 |
| `runtime.blocking_monitor` | — | 宿主级 | 0.109.0-beta.1 |
| `ui.cards` / `config.validate` / `config.saved` / `config.secret_list` / `ui.widgets` | — | 宿主级 | 0.110.0-beta.1 |
| `ui.rules_page` / `rules.dry_run` | — | 宿主级 | 0.110.0-beta.2 |
| `payment.sources` | — | 宿主级 | 0.111.0-beta.1 |
| `interaction.entry_policy` / `provides.ai.image_backend` / `ui.rule_editor` | — | 宿主级 | 0.112.0-beta.2 |
| `interaction.legacy_entry_keys` | — | 宿主级 | 0.112.0-beta.3 |
| `runtime.contract_version` | — | 宿主级 | 0.115.0-beta.2 |

以注册表与 `GET /api/plugins/capabilities` 为准；本表随宿主发版同步。

**Plugin API 版本（`plugin_api`）**

- 宿主提供的 Plugin API 版本是 `MAJOR.MINOR`（当前 `1.7`；`1.0` = 0.107.0，`1.1` = 0.108.0 通用交互原语，`1.2` = 0.109.0 消息管线中间件，`1.3` = 0.110.0 声明式 UI，`1.4` = 0.112.0-beta.1 插件页面与菜单，`1.5` = 0.112.0-beta.2 业务下沉：`entry_policy` / `provides` / `ui.rule_editor`，`1.6` = 0.112.0-beta.3 旧入口 key 声明 `legacy_entry_keys`（稳定契约 1.0 的冻结基线），`1.7` = 0.115.0-beta.2 契约版本探测 `contract_version`；0.110.0-beta.2 的表单契约 2 / 通用规则页、0.111.0-beta.1 的转账通知解析扩展点 `payment_sources` 都是同一 MINOR 内的向后兼容新增，没有再升版本）：新增向后兼容的接口 / 能力只升 MINOR；删除或改变已有接口语义才升 MAJOR，并且要先在 [弃用登记](#弃用登记) 里公告至少一个 minor 宿主版本。
- 插件可以在 `plugin.json` 里声明兼容范围，宿主在执行插件代码前校验，不满足时拒绝加载并说明原因：

```json
{ "plugin_api": "^1.0" }
```

  支持 `"1"` / `"1.x"`（1.0 ≤ v < 2.0）、`"1.2"`（等同 `^1.2`）、`"^1.2"`、`"~1.2"`（1.2 ≤ v < 1.3）、`">=1.1"`、`">=1.0,<2"`（逗号或空格取交集）、`"*"`。写法无法识别时拒绝加载并提示示例。
- **`plugin_api` 只写在 `plugin.json`，不要写进 `Manifest(...)`**：0.107 之前的宿主 `Manifest` 不认识这个参数，写进 `manifest.py` 会在旧宿主上导入失败；宿主加载时会自动把 `plugin.json` 里的值补到 Manifest 上。旧宿主会忽略 `plugin.json` 里的这个字段，所以它不能代替 `min_telepilot_version`。
- 插件不声明 `plugin_api` 时视为兼容任意版本（与 0.107 之前一致）。

### 契约版本探测（contract_version）

Plugin API 1.7，TelePilot 0.115.0-beta.2+，能力 `runtime.contract_version`。宿主把自己承诺的 [插件稳定契约](PLUGIN-CONTRACT.md) 版本告诉插件（当前 `"1.0"`），插件据此判断“这个宿主是否承诺契约 1.x 的只增不减”。它是只读字段，不改变任何已有行为；不读它的插件完全不受影响。

```python
caps = getattr(ctx, "capabilities", None)                            # 0.107 之前没有 ctx.capabilities
contract = getattr(caps, "contract_version", None) if caps else None  # 0.115 之前没有这个属性
if contract is None:
    ...  # 旧宿主：按“契约之前”处理，照旧用 caps.has(...) / 旧属性探测具体能力
```

- `GET /api/plugins/capabilities` 同时返回 `contract_version` 与 `contract_baseline_plugin_api`（契约冻结基线对应的 Plugin API 版本，契约 1.0 = `1.6`）；旧服务器不返回这两个字段，客户端按缺省处理。0.115.0-beta.3 起另有 `compat_usage`（布尔，管理员可读兼容层使用统计 `GET /api/plugins/compat-usage`），同样是旧服务器不返回、按 `false` 处理。
- 契约版本与 Plugin API 版本相互独立：契约 1.x 期间 `contract_version` 保持 `"1.0"`，Plugin API 继续 1.7、1.8 … 递增。具体能力仍以 `caps.has(...)` 为准，不要用契约版本代替能力探测。
- 不要因为读取 `contract_version` 而抬 `min_telepilot_version`：旧宿主上它是 `None`，回退即可。

### 钩子统计与弃用登记

0.107.0-beta.1 起平台按 **账号 × 插件 × 钩子 × 事件类型** 统计每次插件调用（能力 `runtime.hook_stats`），插件无需任何改动：

- 统计的钩子：`on_event`、`on_interaction`、`on_message`、`on_direct_message`、`on_startup`、`on_config_action`、系统助手工具（`system_agent_tool`）等；
- 每行记录调用数、成功 / 失败（抛异常）/ 超时 / 熔断跳过 / 插件更新中跳过次数、平均与最大耗时、按耗时直方图估算的 p50 / p95、失败率，以及最近一次失败摘要（异常类型 + 脱敏、截断到 300 字的消息，不保存消息正文）；
- worker 进程内累加，最多每 5 秒批量写入 Redis，主进程从 Redis 汇总，因此跨进程、跨 worker 重启都能累计；Redis 不可用时只丢统计，不影响插件运行；
- 统计保留到最近一次调用后 7 天；
- 查看：Web 插件详情的「运行统计」，或 `GET /api/plugins/{key}/stats?account_id=`（返回 `hooks[]`、`totals`、`deprecations[]`、`retention_days`；Redis 不可用时 `available=false`）；清零：`DELETE /api/plugins/{key}/stats`（各 worker 尚未写入的增量会在下次写入时重新出现）。

- **同步阻塞**（0.109.0+，能力 `runtime.blocking_monitor`）：插件钩子与中间件的协程由平台逐步驱动计时，两次 `await` 之间的同步代码单步执行 ≥ 200 ms（与事件循环 `loop.slow_callback_duration` 同口径）记一次 `blocked`，累计到 `blocking_ms`。它说明插件在 `async def` 里做了同步重活（`time.sleep`、同步 HTTP、大计算），会卡住整个账号的消息处理；持续偏高的插件应改成异步或迁到独立进程。中间件超时也会把阻塞算进去：同步阻塞超过时限的中间件结果作废、按超时计。
- 中间件按 `middleware.inbound` / `middleware.outbound` / `middleware.delivered` 钩子名单独成行，平台内置中间件记在插件 key `core` 下（`core.rich_compact`、`core.rich_table_defaults`）。

同一份统计还记录插件对已弃用接口的调用次数（能力 `runtime.deprecations`），见下节。

### 弃用登记

每个已弃用的插件接口在 `backend/app/worker/plugins/deprecations.py` 的 `DEPRECATIONS` 里登记一次（`GET /api/plugins/capabilities` 的 `deprecations[]` 返回同一份）。插件调用到它们时：

- 每次调用都计数，插件详情「运行统计」与 `GET /api/plugins/{key}/stats` 的 `deprecations[]` 可见（次数、最近时间、替代写法、计划移除版本）；
- 每个 [进程 × 插件 × 弃用项] 每 6 小时最多写一条 WARNING 日志，避免刷屏；
- 移除计划写在 `removal` 字段（通常是计划移除的 Plugin API 大版本），移除前至少提前一个 minor 宿主版本公告，并以使用量统计为依据。
- **登记 ≠ 删除**：2026-10-31 起兼容层暂时全部保留（不删除、不标记即将删除）。宿主为旧插件保留的全部兼容层（含没有登记计数的项目）、各自的用途与保留理由见 [PLUGIN-API-1.0-PLAN.md](PLUGIN-API-1.0-PLAN.md) 第 3 节，守护测试 `backend/app/tests/test_plugin_compat_layer.py`。
- **兼容层使用统计**（0.115.0-beta.3+）：宿主另按「兼容项 C01–C20 × 插件」统计每个兼容层的命中次数与最近使用时间，**只计数**，不写警告日志、不提醒插件作者，插件无需任何改动、也读不到这份数据。管理员在 Web「插件 → 安装与更新」底部的「兼容层使用统计」卡片或 `GET /api/plugins/compat-usage`（只读）查看；`GET /api/plugins/capabilities` 的 `compat_usage: true` 表示服务器支持（旧服务器不返回该字段）。计数口径见 [PLUGIN-API-1.0-PLAN.md](PLUGIN-API-1.0-PLAN.md) 第 3.2 节。

| id | 弃用内容 | 替代写法 | 弃用自 | 计划移除 |
| --- | --- | --- | --- | --- |
| `hook.on_interaction` | 只实现 `on_interaction(ctx, entry_key, payload)`（交互入口迁移桥），没有实现 `on_event` | 实现 `on_event(ctx, payload)`，入口信息在 `payload["trigger"]` / `payload["entry_key"]` | 0.107.0 | Plugin API 2.0 |
| `manifest.min_telebot_version` | `plugin.json` / manifest 使用旧字段 `min_telebot_version` | `min_telepilot_version`（可带 beta 号） | 0.15.0 | Plugin API 2.0 |
| `ai.tag_alias` | `ctx.ai.complete` / `stream_complete` 使用 `tag=` / `tags=` 兼容别名 | `provider_tag=` | 0.25.0 | Plugin API 2.0 |
| `action.send_via_notice` | 动作 `send_via` / `channel` 使用已下线的 `notice` / `bbot_notice` / `notice_bot` 通道（动作会失败） | `send_via: interaction_bot` 或 `userbot_reply` | 0.36.0 | 已下线（只保留失败提示与计数） |
| `markup.raw_callback_data` | 插件动作的 `reply_markup` 直接写 `callback_data`（点击按会话范围投递，可能被其它插件或规则收到） | 按钮写 `{"text", "callback": {"data"}}`，订阅 `scope: "own_buttons"` | 0.108.0 | Plugin API 2.0（至少保留到 0.109.0 之后） |
| `payload.admin_user_ids` | 从 Event Bus payload 的 `owner_user_ids` / `admin_user_ids` 读取管理员名单（0.106.0-beta.14 临时字段，继续下发，只登记不计数） | `reads: ["account.admins@1"]` + `await ctx.host.read("account.admins@1")` | 0.108.0 | Plugin API 2.0 |

宿主侧新增弃用项时：在 `DEPRECATIONS` 登记，在调用点调用 `note_deprecated("<id>", plugin_key=..., account_id=...)`，并同步本表。

### 通用交互原语（Plugin API 1.1，0.108.0-beta.1+）

目标：插件的新交互需求只改插件、不改宿主。下面五项都在能力注册表登记，用 `ctx.capabilities.has(...)` 探测；旧宿主上探测为 `False` 时请退回旧写法。

#### 通用回调路由（own_buttons）

能力 `messages.callback_routing`。任意动作（`send` / `send_rich` / `edit` / `answer_guest_query` / `answer_inline_query` 的结果……）的 `reply_markup` 里，按钮可以不写 `callback_data`，改写：

```python
await ctx.messages.send_rich(
    chat_id=chat_id,
    markdown=answer,
    reply_markup={"inline_keyboard": [[
        {"text": "🔄 重试", "callback": {"data": f"retry:{turn_id}"}},
        {"text": "🔀 换模型", "callback": {"data": {"op": "switch", "turn": turn_id}, "ttl_seconds": 3600}},
    ]]},
)
```

- 宿主为每个这样的按钮签发 `cb1.<token>` 作为真实 `callback_data`（≤ 64 字节），Redis 记下 `token → {plugin_key, account_id, data, origin}`。`data` 可以是字符串或任意 JSON（≤ 4 KB，不再受 64 字节限制）；`ttl_seconds` 默认 7 天，范围 60 秒～30 天。
- `origin.kind`：`chat`（普通消息，带 `chat_id`）、`inline`（inline 结果 / inline 消息编辑）、`guest`（访客回复，带 `guest_query_id`）、`userbot_text`（UserBot 渠道发出）。签发时还没有 `message_id`。
- 点击后宿主按 token 找到发按钮的插件，只投递给它：订阅 `{"events": ["callback_query"], "scope": "own_buttons"}` 即可收到，**与会话白名单、chat scope 无关**（访客回复、inline 消息上的按钮同样收得到）。其它插件即使订阅了 `callback_query` 也收不到（Trace reason `callback_not_owned`）。
- payload 新增统一字段 `callback`：

```json
{"data": "retry:42", "token": "cb1.…", "plugin_key": "demo",
 "origin": {"kind": "guest", "guest_query_id": "…"},
 "clicker": {"user_id": 1, "display_name": "…", "username": "…"},
 "chat_id": null, "message_id": null, "inline_message_id": "…"}
```

  兼容旧字段：`payload["callback_data"]`、`source.callback_data`、`inline_callback.data` 也换成插件自己写的 `data`，所以 `filters.callback_data` 照常可用。ACK 照旧用 `answer_callback`。
- **fail-closed**：Redis 不可用时，点击一律拒绝（提示“按钮暂时不可用”，Trace `callback_token_unavailable`）；token 过期或不存在提示“按钮已过期”（`callback_token_expired`）。发出时 Redis 不可用，签发失败的按钮会被去掉、消息照常发出（动作记录 `callback_token_warnings`）。
- 平台合成的访客回复原本会去掉 callback 按钮，`cb1.` 按钮因为不依赖 chat/message，会保留。
- 旧写法（直接写 `callback_data`）原样透传，并计入弃用 `markup.raw_callback_data`。旧宿主不认识 `own_buttons`，会按 `all_allowed_chats` 处理——请先探测能力再声明。
- **同一插件的多条订阅不去重**：一次点击命中几条订阅就投递几次。`cb1.` 按钮在允许会话里被点击时，`own_buttons` 与 `all_allowed_chats`（或 inline 消息上的 `guest_all` / `inline_all`）的 `callback_query` 订阅都会命中；旧宿主又把 `own_buttons` 当 `all_allowed_chats`。所以已经有 `all_allowed_chats` / `guest_all` 的 `callback_query` 订阅、又要兼容旧宿主的插件，**只改按钮写法、不要再加 `own_buttons` 订阅**：点击仍经原订阅投递，`payload["callback_data"]` 已还原成插件自己的 `data`，原有解析不用改，其它插件也收不到这些点击（`callback_not_owned`）。新插件只想要“与会话白名单无关”的按钮时，才单独订阅 `own_buttons` 而不订阅其它 `callback_query`。写法见 [新旧平台兼容写法](#新旧平台兼容写法)。

#### 统一 MessageOps

能力 `messages.unified_ops`。`ctx.messages` 的三种 facade（交互入口 `entry`、命令 / 后台任务 `live`、UserBot Event Bus `buffered`）共用一套动作构造与执行器：

- `live` facade 补齐 `send_photo` / `send_file` / `edit_rich` / `edit_caption`，参数与缓冲 facade 完全一致；
- `live` / `entry` 的单动作方法（`send`、`edit_rich`……）失败时，返回的动作字典带 `errors`（与 `apply()` 结果同结构）；
- 同一段插件代码在三种上下文里行为一致，差异只剩 `buffered` 没有实时 `apply()`（因此没有 `live_inline_edit`、`streaming_edit`）；`inline_rich_edit` 现在三种 facade 都为真。

#### Bot API 字段透传（bot_api_extra）

能力 `messages.bot_api_extra`。动作（或 `ctx.messages.send(..., bot_api_extra={...})`）可附带白名单内的 Bot API 字段，宿主只把它合并进该动作对应的 Bot API 方法：

| 动作 | 允许的字段 |
| --- | --- |
| `send_message` / `send_rich_message` | `disable_notification`、`protect_content`、`message_effect_id`、`message_thread_id`、`direct_messages_topic_id`、`link_preview_options` |
| `send_photo` | 同上通用字段 + `has_spoiler`、`show_caption_above_media` |
| `send_file` | 同上通用字段 + `disable_content_type_detection` |
| `edit_message` | `link_preview_options` |
| `edit_caption` | `show_caption_above_media` |
| `pin_message` | `disable_notification` |
| `answer_callback` | `url`、`cache_time` |

- 平台字段（`chat_id`、`text`、`parse_mode`、`reply_markup`、`reply_parameters`、`business_connection_id`、`allow_paid_broadcast` 等）不能透传；未知字段、非 JSON 值或超过 2 KB 时**整条动作拒绝**，`apply()` 结果的 `errors` 带 `error_code=bot_api_extra_rejected` 与原因。
- 平台已给出的同名字段优先，透传只补缺；只在交互 Bot 渠道生效（UserBot 渠道忽略）。`payout` 等资金动作不支持透传。
- Trace 与日志只记字段名（`bot_api_extra_fields`），不记值。

#### AI 请求体透传（extra_body）

能力 `ai.extra_body`。`ctx.ai.complete(..., extra_body={"reasoning_effort": "low"})` 按上游 API 格式白名单透传：

| 上游格式 | 允许的字段 |
| --- | --- |
| OpenAI 兼容 `chat_completions` | `reasoning_effort`、`verbosity`、`top_p`、`top_k`、`min_p`、`frequency_penalty`、`presence_penalty`、`repetition_penalty`、`seed`、`stop`、`response_format`、`logit_bias`、`enable_thinking`、`thinking` |
| `responses` | `reasoning`、`text`、`top_p`、`truncation` |
| Anthropic | `top_p`、`top_k`、`stop_sequences`、`thinking` |

- 不在任何白名单或覆盖平台字段（`model`、`messages`、`max_tokens`、`stream`、`tools`、`service_tier` 等）时立即抛 `AIUnavailableError`；
- 选中的 Provider 格式不支持某字段时跳到备用 Provider，全部不支持时抛 `AICapabilityError`——不会静默丢弃；
- `ctx.ai.stream_complete(..., extra_body=...)`（0.109.0-beta.1+，能力 `ai.stream_extra_body`）用同一套白名单；流式不中途换 Provider，所以选中 Provider 的格式不支持某字段时直接抛 `AICapabilityError`（0.108 上传了会报错“暂不支持”）。

#### 宿主只读查询（ctx.host.read）

能力 `host.read`，每个资源另有子能力 `host.read.<名称>@<版本>`。先在 `plugin.json` 声明（推荐只写在 plugin.json，旧宿主 Manifest 不认识 `reads`）：

```json
{ "reads": ["account.admins@1", "ai.models@1"] }
```

```python
admins = await ctx.host.read("account.admins@1")
if clicker_id in admins["admin_user_ids"]:
    ...
models = await ctx.host.read("ai.models@1", enabled_only=True)
```

| 资源 | 返回 | 备注 |
| --- | --- | --- |
| `account.admins@1` | `{owner_user_ids: [...], admin_user_ids: [...]}` | 账号本人 + 授权 sudo 用户；代替 payload 的 `owner_user_ids` / `admin_user_ids` |
| `ai.models@1` | `[{provider_id, provider_name, provider, model, enabled, default, tags}]` | 参数 `enabled_only`（默认 True）；需 `ai_text` / `ai_agent` 权限 |
| `chat.allowed@1` | `{all: bool, chat_ids: [...]}` | 账号「允许会话」；`all=true` 表示未限制 |
| `plugin.stats@1` | 与 `GET /api/plugins/{key}/stats` 同结构 | 只含本插件、本账号 |

- 未声明、未知资源、当前上下文不提供（例如没有 AI 权限读 `ai.models@1`）都抛 `HostReadError`（`PermissionError` 子类）；资源结构变化会发新版本号，旧版本继续可读。
- 安装 / 更新时校验 `reads`，未知资源会被拒绝。

#### 钩子 Trace 与熔断

能力 `runtime.hook_trace`。钩子统计、Trace 与熔断用同一份结果：

- 钩子失败 / 超时 / 熔断跳过在消息链路 Trace 记一条 `plugin_hook` span，`reason_code` 为 `plugin_error` / `plugin_timeout` / `plugin_circuit_open` / `plugin_update_in_progress`，带 `plugin_key`、`hook`、`event_type`、`duration_ms`；成功但耗时 ≥ 1 秒的调用也记一条，便于找慢插件。
- 熔断：连续失败 N 次（`plugin_circuit_failure_threshold`，默认 3）照旧熔断 60 秒；另外最近 120 秒内调用 ≥ 20 次且失败率 ≥ 80% 也会熔断（时好时坏、从不连续失败的插件）。成功一次即关闭熔断。

#### 入口策略（policy）

能力 `runtime.entry_policy`。交互入口的参与方式与会话边界只从插件声明里读：`interaction_entries[]` 的 `session_scope`（见 [interaction_entries 迁移字段](#interaction_entries-迁移字段)）、`participant_policy`（`open_race` / `solo_owner` / `paid_pool` / `notify_only`）与入口 schema 里是否有 `prize`（是否有奖）。宿主不再按插件 key 写死这些判断；没有声明的旧插件按 `app/plugin_compat.py` 的兜底入口表推断（计划 Plugin API 2.0 移除）；插件写了 `entry_policy` 或 `legacy_entry_keys` 后宿主不再查这张表。注册表 `summary` 里的“policy”指的就是这三项声明，不存在名为 `policy` 的单独字段。

#### 入口策略声明（entry_policy）

Plugin API 1.5，TelePilot 0.112.0-beta.2+，能力 `interaction.entry_policy`。上面三项可以集中写在入口的 `entry_policy` 里，并且**只要插件任一入口写了 `entry_policy`（哪怕是空对象），宿主就不再查兜底入口表**——插件对自己的入口负全责：

```json
{
  "key": "start_paid_game",
  "session_scope": "chat",
  "input_schema": {"type": "object", "properties": {"prize": {"type": "integer"}}},
  "entry_policy": {"session_scope": "chat", "prize": true, "default_start_keywords": ["开24点"]}
}
```

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `session_scope` | `chat` / `user` / `none` | 顶层同名字段优先；顶层没写时用这里的 |
| `participant_policy` | `open_race` / `solo_owner` / `paid_pool` / `notify_only` | 顶层同名字段优先 |
| `prize` | `true` / `false` | 是否有奖；声明后优先于“入口 schema 里有没有 `prize`”的推断 |
| `default_start_keywords` | 字符串数组 | 规则编辑器的启动词提示；运行时不自动生效 |

兼容：

- **旧插件 + 新宿主**：没写 `entry_policy` 的插件照旧按顶层字段 → 兜底入口表推断，结果与 0.110 完全相同（兜底表至少保留到 Plugin API 2.0）。
- **新插件 + 旧宿主**（≤ 0.110）：旧宿主忽略 `entry_policy`，所以**顶层字段要照常写、并与 `entry_policy` 一致**；这样新旧宿主得出同一结论，不需要 `ctx.capabilities` 探测。插件库 `game24` / `pt_promote` / `blackjack` 就是这样写的（`tests/test_r16_entry_policy_provides.py` 守护）。
- 插件在兜底表里还有自己没正式声明的旧入口 key（例如 `math10` 的 `start_math10`、`dice_grid_hunt` 的 `answer_dice_grid_hunt`、`guess_number` 的 `start_game`）时，**不要写 `entry_policy`，也不要把旧 key 补成正式入口**：0.112.0-beta.2 见到 `entry_policy` 就整表跳过兜底，旧 key 会失去推断；补成正式入口则入口变成多个（“单入口推断”失效），旧 key 还会继承正式入口的 schema。改用下一节的 `legacy_entry_keys`。

#### 旧入口 key 声明（legacy_entry_keys）

Plugin API 1.6，TelePilot 0.112.0-beta.3+，能力 `interaction.legacy_entry_keys`。插件把早期用过、已不再是正式入口、但存量规则里可能还写着的旧入口 key 写在正式入口下面，每一项与兜底入口表的一行完全等价：

```json
{
  "key": "start_math_game",
  "session_scope": "chat",
  "legacy_entry_keys": [{"key": "start_math10", "session_scope": "chat"}]
}
```

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `key` | 字符串 | 旧入口 key（规则里的 `module_action`）；与正式入口同名时忽略，正式入口优先 |
| `session_scope` | `chat` / `user` / `none` | 该旧 key 的会话边界；不写 = 未声明（运行时用规则自己的 `concurrency`） |
| `participant_policy` | `open_race` / `solo_owner` / `paid_pool` / `notify_only` | 不写 = 未声明 |
| `prize` | `true` / `false` | 是否有奖；不写 = 未声明（不按正式入口的 schema 推断） |

- 旧 key **不算正式入口**：不参与“规则没写 `module_action` 时取唯一入口”的推断，`declared_module_entry_manifest` 不返回它（时限按默认 15 秒、事件与规则编辑器都按未知入口），和兜底表时代完全一样；插件代码里照旧自己接受这个 key。
- 任一入口写了 `legacy_entry_keys`（哪怕是空列表）就和写了 `entry_policy` 一样，宿主不再查兜底表；所以要把兜底表里属于本插件、而正式入口没覆盖的旧 key 全部列出来。
- **新插件 + 旧宿主**（0.110 / 0.112）：旧宿主忽略 `legacy_entry_keys`；只要插件没写 `entry_policy`，旧宿主照旧走兜底表，结论相同，不需要提高 `min_telepilot_version`。
- **旧插件 + 新宿主**：没写的插件照旧走兜底表（兜底表至少保留到 Plugin API 2.0）。
- 插件库 `math10`（1.0.15+，`start_math10`）、`dice_grid_hunt`（1.1.39+，`answer_dice_grid_hunt`）、`guess_number`（1.0.22+，`start_game`）就是这样写的；宿主 `app/tests/test_plugin_entries_r17.py` 用改动前宿主生成的 golden 逐字段比对，插件库 `tests/test_r17_legacy_entry_keys.py` 守护声明与兜底表一致。

#### 扩展点（provides）

Plugin API 1.5，TelePilot 0.112.0-beta.2+。插件在 `plugin.json` 里声明自己实现了哪个宿主扩展点，宿主按能力选择插件，而不是按插件 key 写死。只写在 `plugin.json`（旧宿主的 `Manifest(...)` 不接受这个参数）：

```json
{"provides": ["ai.image_backend"]}
```

| 扩展点 | 能力 | 宿主怎么用 |
| --- | --- | --- |
| `ai.image_backend` | `provides.ai.image_backend` | AI 指令模板 `mode=image` 且 `image_backend="plugin"`（0.111.0 起新模板默认）时，取本账号已加载、声明了它的第一个插件，把指令参数交给它的第一条命令（或模板里的 `image_plugin_method`）。模板里填了 `image_plugin_key` 时永远用它。 |

兼容：模板里已保存的 `image_backend="codex_image"` 继续固定调用 codex_image；没有任何插件声明 `ai.image_backend` 时也回退 codex_image——旧插件 + 新宿主与 0.110 行为一致。新插件 + 旧宿主：旧宿主忽略 `provides`，照旧按模板里的插件名调用。未登记的扩展点名在安装时给 lint 警告，运行时忽略。

#### 规则编辑器参数区（ui.rule_editor）

Plugin API 1.5，TelePilot 0.112.0-beta.2+，能力 `ui.rule_editor`。交互规则编辑器（账号 → 交互 Bot → 规则）不再写死任何插件的默认插件、启动词或示例；选中某个插件入口后，参数区按插件声明渲染：

```json
{
  "ui": {
    "rule_editor": {
      "entries": {
        "start_paid_game": {
          "hint": "转账或关键词开一局 24 点，第一个答对的人拿奖金。",
          "start_keywords_placeholder": "开24点",
          "default_start_keywords": ["开24点"],
          "default_start_text": "正在开局…",
          "schema": {"type": "object", "properties": {"rounds": {"type": "integer", "title": "轮数"}}},
          "defaults": {"rounds": 3}
        }
      }
    },
    "cards": [{"id": "rule_help", "slot": "interaction.rule_editor", "title": "玩法说明", "layout": "kv"}]
  }
}
```

- `schema.properties` 追加或覆盖入口 `input_schema` 里同名字段的展示定义（不会删掉 `input_schema` 已有字段，奖金等宿主字段识别不变），`defaults` 写进字段默认值；控件规范同 [声明式 UI](#声明式-uiplugin-api-130110-beta1)。
- `start_keywords_placeholder` 缺省时用 `default_start_keywords`，再缺省用 `entry_policy.default_start_keywords`，都没有时显示通用提示。
- 卡片 slot `interaction.rule_editor`：编辑器里只显示规则所选插件的卡片（请求必须带 `plugin_key`），取数同其它 slot 的 `on_ui_card`。目前只有 Web 显示这个 slot。
- 旧宿主 / 旧客户端忽略 `ui.rule_editor`，参数区只按 `input_schema` 渲染，与以前相同。

#### 历史插件兼容表

宿主里仍按插件 key 区分的历史兼容逻辑（math10 旧规则、兜底入口表、codex_image 模型改名、chatgpt_image 多 Token 配置、`,ai` 图片后端默认值……）全部集中在 `backend/app/plugin_compat.py`，每项写明原因与计划移除版本；业务代码不再出现插件 key 字面量（有测试守护）。新插件请用 `interaction_entries[]` 的 `session_scope` / `participant_policy` / `entry_policy` 与入口 schema 声明，不要依赖兼容表。

旧版“算数题”规则（`action=math10`）可以用 `python -m app.scripts.migrate_math10_rules` 改写成通用 `action="module"` 规则：默认只预演，`--apply` 才写入，原规则按 id 备份到 `SystemSetting[interaction_rule_migration:math10:<账号>]`，`--rollback --apply` 恢复迁移后没被编辑过的规则。启动与发奖链路逐字段等价（golden 测试守护），但迁移后规则会走普通玩法规则的会话续玩、关闭通知与查询显示，所以**不会自动运行**，由管理员在真群验证后决定。

### 消息管线中间件（middleware）

Plugin API 1.2，TelePilot 0.109.0-beta.1+，能力 `middleware.inbound` / `middleware.outbound` / `middleware.delivered` / `middleware.stream_preview`。以前“所有插件的表格都紧凑”“所有消息加署名”“敏感词打码”这类横切需求只能改宿主；现在插件可以在消息管线上挂中间件。示例：`examples/plugins/word_guard_mw`、`examples/plugins/signature_mw`。

**声明**（只写在 `plugin.json`，旧宿主忽略；`manifest.py` 不用写）：

```json
{
  "plugin_api": "^1.2",
  "permissions": ["message_middleware"],
  "middleware": [
    {"stage": "outbound", "handler": "on_outbound", "order": 500,
     "match": {"action_types": ["send_message", "send_rich_message", "edit_message", "answer_guest_query"],
               "channels": ["interaction_bot", "userbot_reply"], "sources": ["self", "any_plugin"]},
     "timeout_ms": 250},
    {"stage": "inbound", "handler": "on_inbound", "order": 100, "match": {"events": ["message"], "sources": ["userbot", "interaction_bot"]}},
    {"stage": "delivered", "handler": "on_delivered"}
  ]
}
```

| 字段 | 说明 |
| --- | --- |
| `stage` | `inbound` / `outbound` / `delivered` |
| `handler` | 插件类上的公开 async 方法名，签名统一 `async def handler(self, ctx, value, mw)` |
| `order` | 1–899，默认 500，小的先跑；900+ 留给平台 `core.*` |
| `timeout_ms` | 50–2000，默认 250 |
| `match.action_types`（outbound / delivered） | 默认全部消息类动作：`send_message`、`send_rich_message`、`edit_message`、`send_media` / `send_photo` / `send_document` / `send_file`、`edit_caption`、`answer_guest_query`、`answer_inline_query`、`answer_callback`；outbound 另可加 `stream_preview`（流式中间态）。资金 / 会话类动作（`payout`、`settlement`、`start_session` …）永远不进插件中间件 |
| `match.channels`（outbound / delivered） | `interaction_bot` / `userbot_reply`，不写 = 不限 |
| `match.sources`（outbound / delivered） | `self`（默认，只处理本插件发的动作）/ `any_plugin`（所有插件的动作，需要 `message_middleware` 权限）；`system` 预留，平台自己的消息（告警、管理 Bot）不经过插件中间件 |
| `match.events` / `match.sources`（inbound） | 事件类型（默认 `["message"]`）与来源 `userbot` / `interaction_bot`；inbound 能看到并丢弃所有插件的事件，必须声明 `message_middleware` 权限 |

`critical`（fail-closed）只允许平台 `core.*`，插件写了会被拒绝安装。安装 / 更新时严格校验声明；本地插件声明有问题时按规则修正或忽略并写警告日志。

**三个阶段**

| 阶段 | 位置 | `value` | 返回 |
| --- | --- | --- | --- |
| `inbound` | 事件信封生成之后、路由之前（UserBot Event Bus：分发前一次；交互 Bot：投给插件前，同一事件只跑一次） | 事件信封（只读） | `None` 原样；补丁 `{"text": "...", "annotations": {...}}`；`mw.drop(reason)` 丢弃，所有插件都收不到 |
| `outbound` | 插件返回动作、规范化之后、投递之前（覆盖交互 Bot、UserBot、实时 `ctx.messages.apply`、访客合并、流式最终一次） | 动作 | `None` 原样；改过的动作 dict；`mw.reject(code, message)` 拒绝该动作 |
| `delivered` | 投递结果确定之后，异步执行，不阻塞投递 | 结果摘要：`action_type`、`ok`、`error_code`、`chat_id`、`channel`、`source_plugin_key`、`entry_key`、`trace_id`、`inline_message_id`（不含正文） | 忽略 |

```python
async def on_outbound(self, ctx, action, mw):
    if mw.action_type == "send_message" and not mw.is_own:
        action["text"] = action["text"] + "\n— by bot"
        return action
    return None

async def on_inbound(self, ctx, event, mw):
    if "spam" in (event.get("message") or {}).get("text", ""):
        mw.drop("spam")
    return {"annotations": {"lang": "zh"}}   # 写进 event["annotations"]["<本插件 key>"]
```

`mw` 提供：`stage`、`plugin_key`、`account_id`、`action_type` / `event_type`、`source_plugin_key`、`is_own`、`channels`、`trace_id`，以及 `reject()` / `drop()` / `thaw()`。

**执行规则**

- **只读视图 + 结构共享**：`value` 顶层是普通 dict，嵌套的 `rich_message`、`reply_markup`、`context` 等是只读视图（改它会抛 `TypeError`，按失败处理）。要改嵌套值就整体替换：`markup = mw.thaw(action["reply_markup"]); markup["inline_keyboard"].append(...); action["reply_markup"] = markup`。没被替换的值原样共享，大 `rich_message` 经过多个中间件也不会被反复深拷贝。
- **字段白名单**：outbound 只能改 `text`、`caption`、`parse_mode`、`rich_message`、`reply_markup`、`render_profile`、`disable_web_page_preview`、`link_preview_options`、`disable_notification`、`result`（访客回复）、`results`（inline 结果）、`annotations`。改了 `type`、`chat_id`、`send_via` / 通道、`message_id`、`settlement`、金额或任何别的字段，**这个中间件的整次修改回滚**，记一次失败（`reason_code=middleware_field_violation`）并计入熔断。inbound 补丁只能有 `text`（同步改 `message.text` / `source.text` / `message_text`）和 `annotations`；`chat_id`、发送者、来源改不了。
- **fail-open**：抛异常、超时、返回畸形值（`middleware_malformed_result`）都丢弃它的修改、按原样继续，并记入插件统计与熔断（超时 `reason_code=plugin_timeout`，同步阻塞超过时限也按超时算）。熔断中的插件中间件直接跳过。
- **预算**：单钩子默认 250 ms；一条消息整条链 1 秒，超出后剩余中间件跳过（统计记 `skipped`）；`stream_preview` 每个钩子 50 ms。
- **链长**：每个账号每个阶段最多 8 个插件中间件，按 `order`、插件 key 排序，多出的不生效并写警告日志。
- **拒绝**：`mw.reject()` 的动作不会发出，插件拿到的结果 / 动作记录里 `error_code=middleware_rejected`，`error` 带中间件插件 key 与 `code`。
- **流式**：`ctx.messages.stream_editor()` 的中间态只经过 `match.action_types` 显式写了 `stream_preview` 的 outbound 中间件（`value` 是 `{"type": "stream_preview", "text": ...}`），`mw.reject()` 跳过这一次中间编辑；`finish()` 的最终一次是普通动作，走完整 outbound 链。
- **访客模式**：outbound 在 worker 里跑完后，主进程的 `core.guest_merge` 再把消息类动作合并成一次 `answerGuestQuery`，访客回复里看到的是中间件处理后的内容。
- **统计**：每次调用记在 `[账号 × 插件 × middleware.<stage> × 动作 / 事件类型]`；失败与超时在 Trace 记 `plugin_middleware` span。

**平台内置中间件（core.\*）**：顺序固定、不能卸载，`GET /api/plugins/capabilities` 的 `core_middleware[]` 列出同一份：

| 名称 | 阶段 | order | 作用 |
| --- | --- | --- | --- |
| `core.render_profile` | outbound | 900 | 合并 `plugin.json` 与动作上的 `render_profile`（`compact_tables: true` 是旧写法），不外发 |
| `core.rich_compact` | outbound | 950 | 按 `render_profile.table` 写紧凑 / 显式边框 / 隔行着色 |
| `core.rich_table_defaults` | transport | 1000 | 发送出口给没写样式的表格补边框 + 隔行着色；旧 Bot API 拒绝紧凑表格时去掉 compact 重发一次 |
| `core.html_fallback` | transport | 1010 | Telegram 拒绝 HTML 实体时退回纯文本重发一次（fail-closed） |
| `core.guest_merge` | delivery | 1020 | 访客模式合并成一次 `answerGuestQuery`（fail-closed） |

插件中间件的 order 都小于 900，所以插件新增或改写的表格同样套平台样式。插件只想要“我的表格紧凑”时不需要写中间件，声明 `render_profile` 即可（见 [紧凑表格](#紧凑表格bot-api-103telepilot-01060-beta12)）。没有声明 `render_profile` 的消息，表格外观与 0.108 完全一致（golden 测试守护）。

### 声明式 UI（Plugin API 1.3，0.110.0-beta.1+）

目标：插件要在 Web / iOS 上多一张卡片、多一种配置控件，只改 `plugin.json` 与插件代码，不改宿主与 App。规范来源是仓库根的 [`schemas/plugin-ui-widgets.json`](../schemas/plugin-ui-widgets.json)（当前 `contract_version` 2：1 = 0.110.0-beta.1 控件与卡片，2 = 0.110.0-beta.2 表单契约 2），后端、Web、iOS 各有一份由 `scripts/sync-plugin-ui-widgets.py` 生成的逐字节副本，CI「后端」与「iOS」工作流都运行 `--check`。`GET /api/plugins/ui-contract` 返回同一份契约。能力名 `ui.cards` 等登记在注册表里；这些都是声明 + 钩子，旧宿主自动忽略，插件不需要为它们写探测分支（见 [新旧平台兼容写法](#新旧平台兼容写法)）。

#### 配置控件契约

能力 `ui.widgets`（version 1）。契约列出全部 23 种配置控件、各自识别条件（`resolution_order` 自上而下第一个命中）、可用的 `x-ui-*` 键、最低 iOS 版本与示例。Web `ConfigDialog` 与 iOS 插件配置页都实现全部控件，并在单测里逐项对照契约。本版补齐 iOS 的 4 种控件：

| 控件 | 写法 | 说明 |
| --- | --- | --- |
| `multi-select` | `x-ui-widget: "multi-select"`，或数组字段 `items.enum` 非空 | 固定选项多选，`enumNames`（数组字段写在 `items` 上）作显示名 |
| `list-select` | `x-ui-widget: "list-select"`（字段带 `enum`） | 单选，选项平铺为列表 |
| `allowed-peer-multi-select` | `x-ui-widget: "allowed-peer-multi-select"`（数组） | 从账号「允许会话」里多选；全局配置只读 |
| `config-list` | `x-ui-widget: "config-list"`（对象数组） | 每项一行摘要（`x-ui-title-field` / `x-ui-description-field`），点开编辑，可增删排序 |

**未知控件**：客户端遇到不认识的 `x-ui-widget` 时显示「此控件需要更新 App」并保持只读，保存时原值不动，不会退化成通用编辑器把数据改坏。

#### 表单契约 2（0.110.0-beta.2+）

`schemas/plugin-ui-widgets.json` 升到 `contract_version` 2，新增四个声明，Web 与 iOS（0.14.1 起）都实现；不认识的客户端忽略它们，表单照旧可用：

| 声明 | 写在 | 含义 |
| --- | --- | --- |
| `x-rule-schema` | `config_schema`（`x-ui-mode: rules`） | 每条规则 `rule.config` 的 schema；省略时整个 `config_schema` 都是规则字段 |
| `x-rule-summary` | `config_schema` | 规则列表每行显示的字段名数组（最多 3 个，只取规则 schema 里有的）；省略时取前两个可见字段 |
| `x-dry-run` | `config_schema` | 试运行卡片 `{sample_fields, title, description}`，`sample_fields` 取 `message` / `chat_type` / `chat_id`；`false` 关闭；省略时默认「样例消息 + 会话 ID」 |
| `x-ui-visible-if` | 任一字段（配置或规则） | `{field, equals}` / `{field, in: [...]}` / `{field, not_in: [...]}`，按**同级**字段当前值显示 / 隐藏；隐藏的必填字段不再要求填写；写法不合法时一律显示 |

另外：数组字段写 `x-ui-widget: "textarea"` 时是**每行一项**的多行文本框（项里可以有逗号，适合正则 / 关键词 / 会话 ID 列表），否则仍按逗号分隔。宿主安装与兼容检查会 lint 这些声明（引用了不存在的字段、`sample_fields` 取值不对等给出警告）。

#### 密钥列表（x-secret-list）

能力 `config.secret_list`。在 `config_schema` 的数组字段上声明：

```json
"tokens": {
  "type": "array", "title": "Token 列表",
  "x-secret-list": {"secret_field": "token", "note_field": "note", "legacy_field": "token"},
  "items": {"type": "object", "properties": {"token": {"type": "string"}, "note": {"type": "string"}}}
}
```

- 读取配置时宿主把每项的 `secret_field` 换成遮罩（前几位…后几位），并附稳定的 `id_field`（默认 `secret_id`）；明文密钥不会下发到客户端。
- 保存时 `id_field` 或遮罩命中已保存条目就保留原密钥，否则当作新填写的明文，加密保存；空值与重复项丢弃。
- `legacy_field`（可选，顶层字符串字段）里按行 / 逗号分隔的旧密钥会并入列表后清空，方便从单 Token 字段迁移。
- Web 与 iOS 显示原生密钥列表：已保存的只显示遮罩，可删除、改备注；新加的显示前几位并标「保存后加密」。
- 旧版 `chatgpt_image`（未声明 `x-secret-list`）由 `app/plugin_compat.py` 补一份等价声明，行为不变。

#### 配置钩子（on_config_validate / on_config_saved）

能力 `config.validate`、`config.saved`。

```python
async def on_config_validate(self, ctx, config: dict, scope: str) -> dict | None:
    if config.get("model") == "gpt-image-old":
        return {"config": {**config, "model": "gpt-image-1"}}          # 规范化后保存
    if not config.get("tokens"):
        return {"errors": {"tokens": "至少填写一个 Token"}}             # 拒绝保存，逐字段提示
    return None

async def on_config_saved(self, ctx, config: dict, scope: str) -> None:
    self._client_cache.clear()                                         # 后台执行，不影响保存结果
```

- `scope` 为 `"account"` 或 `"global"`；`config` 已通过 JSON Schema 校验、已合并保留原密钥（明文只在内存里）。
- `on_config_validate` 同步调用、超时 2 秒；超时或抛异常时**放行保存**（fail-open）并记入钩子运行统计。返回 `errors` 时保存接口返回 400（`code: CONFIG_VALIDATION_ERROR`，`detail.fields` 为逐字段原因），Web / iOS 配置页显示「字段: 原因」。
- `on_config_saved` 在保存成功后后台调用、超时 10 秒，返回值与异常都不影响保存结果。

#### 声明式卡片（ui.cards）

能力 `ui.cards`。在 `plugin.json` 声明：

```json
"reads": ["plugin.stats@1"],
"ui": {"cards": [
  {"id": "health", "slot": "plugin.overview", "title": "最近 24 小时",
   "source": {"read": "plugin.stats@1"},
   "layout": {"type": "stats", "items": [{"label": "调用", "path": "totals.calls"},
                                         {"label": "失败", "path": "totals.failed", "tone": "danger"}]}},
  {"id": "quota", "slot": "account.detail", "title": "配额",
   "source": {"action": "quota"},
   "blocks": [
     {"type": "kv", "items": [{"label": "剩余", "path": "left", "format": "number"},
                              {"label": "最近刷新", "path": "refreshed_at", "format": "relative_time"}]},
     {"type": "buttons", "items": [{"id": "refresh", "label": "立即刷新"},
                                   {"id": "reset", "label": "重置", "style": "destructive", "confirm": "确定重置配额？"}]}
   ]}
]}
```

- **slot**：`plugin.overview`（插件详情顶部）、`account.detail`（账号详情，只显示该账号启用的插件）、`dashboard`（概览末尾，全局启用的插件）。
- **布局**：`layout` 单块或 `blocks` 多块，首批块类型 `stats` / `kv` / `table`（手机上每行折成小卡）/ `list` / `buttons`。取值写 `path`（点分路径）或 `value`；`format`：`text` / `number` / `percent` / `duration_ms` / `datetime` / `relative_time`；`tone`：`neutral` / `success` / `warning` / `danger` / `accent`。
- **数据来源**：`source.read` 必须是 `reads` 里声明过的宿主资源（`plugin.stats@1` / `chat.allowed@1` / `account.admins@1`）；`source.action` 调插件 `on_ui_card(ctx, card_id, context) -> dict`，同步、超时 3 秒，`context` 含 `slot` / `account_id` / `action`。
- **块字段**（完整定义以契约 `cards.layouts` 为准）：`stats` / `kv` 的 `items: [{label, path | value, format?, tone?, unit?}]`；`table` 的 `rows_path`（默认 `rows`）、`columns: [{key, label, format?}]`、`empty`（空表文案）；`list` 的 `items_path`（默认 `items`）、`title_key` / `subtitle_key` / `trailing_key` / `tone_key`（默认 `title` / `subtitle` / `trailing` / `tone`）、`empty`；`buttons` 的 `items: [{id, label, style?, confirm?}]`。`relative_time` / `datetime` 接受秒级时间戳。
- **钩子上下文**：`on_ui_card` / `on_ui_action`（以及 `on_config_validate` / `on_config_saved`、`on_config_action`）在**主进程**里调用，`ctx` 是不带 Telegram client 的受控上下文：有 `ctx.config`（该作用域的有效配置）、`ctx.account_id`（全局作用域 / 概览卡片为 `0`）、`ctx.data_dir`、`ctx.http`、`ctx.ai`；**没有** `ctx.storage`、`ctx.messages`、`ctx.host`、client。每次调用都是新的插件实例（不执行 `on_startup`），所以不能读 worker 里内存状态。数据放在 `ctx.storage` 里的插件目前只能用 `source.read` 宿主资源出卡片；数据在 `ctx.data_dir` 文件里的插件可以只读打开文件（参考插件库 `sum` 的「定时总结任务」卡片）。
- **按钮**：点按调 `on_ui_action(ctx, action_id, context)`，超时 10 秒；`confirm` 非空时客户端先确认；返回 `{"message": "已刷新", "refresh": true}`，`message` 显示给管理员，`refresh` 为真时重新读取这张卡。
- **隔离**：每张卡单独读取（`POST /api/plugin-ui/cards/{plugin}/{card}/data`），读取失败或超时只让这张卡显示「已停止工作 · 重试」，不影响页面其它部分；不合规的卡片整张忽略并给出 lint 警告。客户端不认识的块类型只读提示「需要更新 App」。
- 宿主自带一张 `_host.stats`「运行统计」卡（钩子级调用数、失败、耗时与弃用接口调用），显示在每个插件详情顶部——原来写死在插件详情里的统计区改由卡片 DSL 渲染。
- 接口：`GET /api/plugin-ui/cards?slot=…&account_id=…` 列出卡片声明；`POST /api/plugin-ui/cards/{plugin}/{card}/data` 取数；`POST /api/plugin-ui/cards/{plugin}/{card}/actions/{action}` 执行按钮。

### 插件页面与菜单（Plugin API 1.4，0.112.0-beta.1+）

声明式卡片放不下的界面（图表、表格编辑、小游戏面板），插件可以自带 HTML 页面；不需要页面的一次性操作，可以声明菜单项。全部可选：**没有声明时插件详情与账号详情不出现任何新入口**，旧插件行为完全不变。契约见 [`schemas/plugin-ui-widgets.json`](../schemas/plugin-ui-widgets.json) 的 `pages` 段（Web / iOS / 后端同一份）。示例：`examples/plugins/ui_page_demo`。

#### 插件页面（ui.pages）

```json
"ui": {
  "pages": [{
    "id": "dashboard",
    "title": {"zh": "ping 面板", "en": "Ping dashboard"},
    "subtitle": "插件自带页面示例",
    "file": "ui/index.html",
    "menus": ["plugin.detail", "account.detail"],
    "bridge": ["config.read", "cards.read", "actions.invoke"],
    "actions": ["summarize"]
  }]
}
```

- `file`：插件目录内的 `.html` 文件。服务器只对页面开放入口文件所在目录（如 `ui/`），`plugin.json` 与插件代码不对页面开放；单文件 ≤ 2 MB。
- `menus`：出现在哪里，默认 `["plugin.detail"]`（插件详情「插件页面与操作」）；`account.detail` 是账号详情，只列在该账号启用的插件。
- 打开页面时客户端调 `POST /api/plugin-ui/pages/{key}/{page}/session` 换一个 1 小时有效的签名地址（不含登录凭据），文件由 `GET /api/plugin-ui/page-assets/{token}/{path}` 提供。
- **沙箱**：Web 用 `iframe sandbox="allow-scripts"`（不给 `allow-same-origin`，页面读不到宿主 Cookie / localStorage / token）；iOS 用非持久数据的 `WKWebView`，页面文件经 `tp-plugin://` 自定义协议由 App 代取，内容规则屏蔽所有 http(s) 请求，只允许 `tp-plugin:` / `about:` 导航。服务器响应头与注入的 `<meta>` 都是严格 CSP：`connect-src 'none'`（页面不能联网，`fetch` / XHR / WebSocket 一律失败），只能加载插件目录内的脚本、样式与图片。
- **Bridge 协议 1**：宿主在页面 `<head>` 最前面注入 `window.telepilot`：

| 方法 | 需要的 `bridge` 权限 | 说明 |
| --- | --- | --- |
| `ready()` | 无 | `{plugin_key, page_id, account_id, bridge, protocol, host_version}` |
| `theme()` / `onTheme(cb)` | 无 | `{scheme: "light" \| "dark"}`，宿主切换外观时推送事件，并自动设置 `<html data-theme>` |
| `config()` | `config.read` | 当前作用域（全局或账号）的配置；密钥字段（`x-secret-list`、`format: password/secret/token`、敏感键名）一律去掉 |
| `card(id)` | `cards.read` | 本插件声明的卡片数据，与原生卡片同一取数通道（3 秒） |
| `action(id, payload)` | `actions.invoke` | 调插件 `on_ui_action(ctx, id, context)`（10 秒），`context.page_id` 为页面 id、`context.payload` 为页面传的对象（≤ 16 KB）；`actions` 非空时只能调列出的 id。不自动重试，有审计 |

  底层消息格式：页面 → 宿主 `{tp: "telepilot", v: 1, id, method, params}`，宿主 → 页面 `{tp, v, id, ok, result | error: {code, message}}`。宿主只做格式校验与转发，**权限由服务器按页面声明逐次校验**（`POST /api/plugin-ui/pages/{key}/{page}/bridge`），失败以 `ok: false` 返回给页面（`BRIDGE_PERMISSION_DENIED`、`BRIDGE_METHOD_UNKNOWN` 等），15 秒无响应页面侧报 `BRIDGE_TIMEOUT`。

#### 菜单项（ui.actions）

```json
"ui": {
  "actions": [
    {"id": "summarize", "menu": "plugin.detail", "title": "生成计数摘要"},
    {"id": "reset", "menu": "account.detail", "title": "清零计数", "confirm": "清零 ping 计数？", "style": "destructive"},
    {"id": "open_dashboard", "menu": "account.detail", "title": "打开 ping 面板", "page": "dashboard"}
  ]
}
```

- 点击后调用 `on_ui_action(ctx, id, {"menu", "account_id", "page_id": None, "payload": {}})`（10 秒），返回 `{"ok", "message"}` 显示给用户；带 `page` 时改为打开该页面。
- `confirm`（`true` 或确认文案）与 `style: "destructive"` 会先弹确认；客户端不自动重试，结果未知时提示刷新确认。
- 接口：`GET /api/plugin-ui/entries?menu=plugin.detail|account.detail[&plugin_key][&account_id]` 列入口，`POST /api/plugin-ui/actions/{key}/{id}` 执行。

#### 钩子里的只读 ctx.storage

能力 `hooks.storage_read`：卡片（`on_ui_card` / `on_ui_action`）、配置钩子（`on_config_validate` / `on_config_saved`）与页面 / 菜单动作里，`ctx.storage` 是只读视图（`get` / `get_all` / `items`），写入抛 `PermissionError`。之前这些钩子里 `ctx.storage` 为 `None`，兼容写法：

```python
storage = getattr(ctx, "storage", None)
pings = int((await storage.get("pings")) or 0) if storage is not None else 0
```

#### 模型清单新增字段（ai.models@1）

能力 `ai.models.metadata`：`ctx.host.read("ai.models@1")` 每项**只增**字段 `display_name`、`label`、`available`（有可用 Key 且模型启用）、`has_api_key`、`cost_tier`（1 便宜 · 2 标准 · 3 旗舰）、`cost_tier_label`；旧字段与语义不变，旧宿主没有这些字段，读取时用 `item.get(...)`。

### 转账通知解析（payment_sources）

0.111.0-beta.1+（能力 `payment.sources`）。交互 Bot 收到「可信转账 Bot」发来的转账 / 扣减通知时，宿主要从文案里解析出付款人、收款人和金额，再去匹配交互规则、结算。过去这段中文正则写死在宿主里（`account_bot_runtime._parse_transfer_notice`），现在可以由插件提供；官方实现是插件库的 [`payment-source-cn-transfer`](https://github.com/Anoyou/telebot-plugins/tree/main/payment-source-cn-transfer)（宿主旧正则的逐字迁移）。

> 这里的“金额”是群游戏里的积分（例如 PT 站点魔力值），不是真实货币；但它决定群游戏结算，所以按资金路径处理：插件只负责“读懂文案”，结算永远由宿主执行。

**声明与导出**

```json
// plugin.json（旧宿主忽略这个字段）
{
  "payment_sources": [
    {"id": "cn_transfer", "title": "中文转账通知", "units": ["蝌蚪", "魔力值"]}
  ],
  "permissions": [],
  "requires_platform_capabilities": ["interaction_bot"]
}
```

```python
# plugin.py（或包 __init__.py）导出纯函数；可以是 async def
def parse_payment_notice(text: str, meta: dict) -> dict | None:
    """meta = {"account_id", "chat_id", "message_id", "sender_id", "source": "text" | "reply_to_text"}"""
    ...
    return {
        "payer_name": "路人A",          # 必填，非空字符串（≤256 字）；也接受别名 payer
        "receiver_name": "我的TG名",    # 必填；也接受别名 receiver
        "amount": 100,                  # 必填，≥0 的整数（数字字符串也接受）
        "payer_user_id": 1122,          # 可选，整数
        "receiver_user_id": 9988,       # 可选，整数
        "unit": "魔力值",                # 可选（别名 currency），只记录，不参与对照和结算
    }  # 不是转账通知时返回 None
```

**宿主怎么调用**

- 只在**可信 Bot ID 白名单**通过之后调用（交互 Bot 配置的 `trusted_bot_ids` / `transfer_bot_id`）；其它人发的消息永远到不了插件。
- 和旧解析器同样的顺序把文本交给插件：先消息正文（`source="text"`），认不出再给被回复的那条消息（`source="reply_to_text"`），第一段认出即止。
- 在主进程调用，限时 **1 秒**；同步函数放在线程里跑，超时后不再等待。插件拿不到 `ctx`、Telegram client、数据库或任何结算接口。
- 只调用「已安装、全局启用、签名策略允许、并在 `plugin.json` 声明了 `payment_sources`」的插件，按插件 key 排序；第一个认出通知的插件作为结果。没声明 `payment_sources` 的插件不会因此被加载。发现结果缓存 30 秒（安装 / 升级 / 启停后最多 30 秒生效）。
- 插件超时、抛异常、返回值不合法、未安装或未启用：一律回到宿主旧解析（fail-open）。
- 返回结果之后的规则匹配（收款人 / 金额过滤）、回复链校验、付款人补全、幂等与补偿全部不变。

**双跑对照（至少一个 beta 周期）**

- 默认**以宿主旧解析器为准**（`backend/app/services/payment_notice_legacy.py`），插件结果只做对照，比较字段 `payer_name / receiver_name / amount / payer_user_id / receiver_user_id`。
- 不一致时：warning 日志 `payment source mismatch aid=… chat_id=… message_id=… plugin=… used=legacy diffs=…`；Trace span `payment_source`（`reason_code=payment_source_mismatch`，详情里有两边结果和逐字段差异）；插件「运行统计」钩子「转账通知解析」的事件维度「与宿主一致 / 与宿主不一致 / 插件失败（已回退宿主）」，不一致行显示「对照不一致」。插件失败记 `reason_code=payment_source_failed`。
- 系统设置 → 运行日志 → 「转账通知以插件结果为准」（`PATCH /api/system/settings` 的 `log_retention.payment_source_plugin_authoritative`，默认 `false`，30 秒内生效）打开后改用插件结果；插件明确返回 `None`（不是转账通知）时也以插件为准。打开后，可信 Bot 发来的、宿主旧正则认不出的消息也会交给插件判断（对照模式下路由与旧版本完全一样）。
- 宿主旧解析器至少保留一个大版本作为兜底。

**测试要求**：插件库的 `tests/fixtures/payment_notice_golden.json`（419 条：历史 / 现有测试文案 + 默认转账 / 扣减模板批量渲染 + 手工边界）由主仓库 `backend/app/tests/data/payment_notice_golden.json` 同步而来，期望值是宿主旧解析器的输出；新的转账通知插件至少要对这份 golden 100% 一致，再加自己格式的样本。

### 新旧平台兼容写法

插件库约定：用新能力**不抬高** `min_telepilot_version`，而是先探测、再回退；兼容层保留到 Plugin API 2.0。下面是插件库 `Anoyou/telebot-plugins` 实际在用的写法。

**1. 能力探测助手**（旧宿主没有 `ctx.capabilities`；探测本身出错也按“不支持”处理）

```python
def host_has(ctx, name: str) -> bool:
    caps = getattr(ctx, "capabilities", None)      # 0.107.0-beta.1 之前为 None
    if caps is None:
        return False
    try:
        return bool(caps.has(name))
    except Exception:  # noqa: BLE001
        return False
```

**2. 按钮：通用回调路由（0.108.0+），旧宿主写 `callback_data`**

```python
def button(text: str, data: str, *, routed: bool) -> dict:
    return {"text": text, "callback": {"data": data}} if routed else {"text": text, "callback_data": data}

routed = host_has(ctx, "messages.callback_routing")
markup = {"inline_keyboard": [[button("🔄 重试", f"demo:r:{token}", routed=routed)]]}

# 点击处理两种宿主都一样：新宿主把 payload["callback_data"] 还原成上面的 data
data = (payload.get("source") or {}).get("callback_data") or payload.get("callback_data") \
    or (payload.get("callback") or {}).get("data")
```

订阅保持原来的 `all_allowed_chats` / `guest_all` 的 `callback_query`，**不要再加 `own_buttons`**（原因见 [通用回调路由](#通用回调路由own_buttons)）。参考：插件库 `interaction-ai-chat` 的 `retry.button()` / `callback_routing_supported()`。

**3. 管理员名单：`ctx.host.read`（0.108.0+），旧宿主读 payload**

```python
async def admin_ids(ctx, payload) -> set[int]:
    ids = {int(x) for k in ("owner_user_ids", "admin_user_ids") for x in payload.get(k) or []}  # 旧字段（已弃用但仍下发）
    if host_has(ctx, "host.read.account.admins@1") and getattr(ctx, "host", None) is not None:
        try:
            data = await ctx.host.read("account.admins@1")   # plugin.json 声明 "reads": ["account.admins@1"]
            ids |= {int(x) for k in ("owner_user_ids", "admin_user_ids") for x in data.get(k) or []}
        except Exception:  # noqa: BLE001 - 未声明 / 不可用按“没有额外管理员”
            pass
    return ids
```

注意 `account.admins@1` 是“账号本人 + sudo 用户”。UserBot 渠道的 Event Bus payload 本来不带这两个字段，改用 `host.read` 会让 sudo 用户也被当成管理员——这是行为变化，迁移前先确认插件原本的判定口径。

**4. 只在新宿主生效、旧宿主自动忽略的声明**（不用探测，也不用抬版本）

| 声明 / 钩子 | 新宿主（门槛） | 旧宿主 / 旧客户端 |
| --- | --- | --- |
| `plugin.json` 的 `ui.cards` + `on_ui_card` / `on_ui_action` | 插件详情 / 账号详情 / 概览出卡片（0.110.0-beta.1） | 忽略 `ui`，不调用钩子 |
| `on_config_validate` / `on_config_saved` | 保存前校验、保存后通知（0.110.0-beta.1） | 不调用，保存行为不变 |
| `x-secret-list` | 原生密钥列表、明文不下发（0.110.0-beta.1） | 按普通数组字段显示 |
| `x-ui-visible-if`、数组 `x-ui-widget: "textarea"`、`x-rule-schema` / `x-rule-summary` / `x-dry-run`、`dry_run_rule` | 按值显隐、每行一项、通用规则页与试运行（0.110.0-beta.2 / iOS 0.14.1） | 字段照常全部显示；数组仍按逗号分隔 |
| `plugin.json` 的 `payment_sources` + `parse_payment_notice` | 转账通知解析（0.111.0-beta.1，双跑对照期默认只做对照） | 忽略声明，不调用；插件只是一个没有订阅的空插件 |
| `plugin.json` 的 `ui.pages` / `ui.actions`、钩子里的只读 `ctx.storage` | 插件页面与菜单项（0.112.0-beta.1 / iOS 0.15.0）；卡片与配置钩子能读 `ctx.storage` | 忽略声明，不出现入口；旧 App 不显示新入口、不报错；钩子里 `ctx.storage` 为 None（先判断再用） |
| `plugin.json` 的 `middleware` / `render_profile` | 消息管线中间件（0.109.0-beta.1） | 忽略；插件照常加载但中间件不生效。中间件是插件主要功能时用 `"plugin_api": "^1.2"` 拒绝旧宿主 |

- `x-ui-visible-if` 推荐写 `not_in`：`{"field": "pt_enabled", "not_in": [false]}` 只在开关被明确关掉时隐藏。Web / iOS 按表单里的当前值判断，值缺省（没保存过）或控制字段在另一作用域（全局 / 账号）时，`equals: true` 会把字段误藏起来。被隐藏的字段值保留不删。
- `on_config_validate` 拿到的 `config` 已合并保留的敏感字段（明文只在内存里），只校验本作用域里出现的字段；只拦确实无法工作的值，不要用它改变既有配置的含义。

**5. 参数级新增：`stream_complete(extra_body=...)`**

0.108 的 `stream_complete` 收到 `extra_body` 会报“暂不支持”，更早的宿主静默忽略（不生效）。先探测再传：

```python
kwargs = {"extra_body": {"reasoning_effort": "low"}} if host_has(ctx, "ai.stream_extra_body") else {}
async for chunk in ctx.ai.stream_complete(system, user, **kwargs):
    ...
```

`complete(extra_body=...)` 同理探测 `ai.extra_body`（0.108.0-beta.1+）；0.73 起 `complete()` 会忽略未知关键字参数，所以旧宿主上传了不报错、但也不生效。
