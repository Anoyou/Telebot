# TelePilot 插件 AI facade

`ctx.ai` 是第三方插件可用的受控 AI facade。普通文本调用声明 `permissions=["ai_text"]`；需要模型自主选择工具时必须额外声明独立的 `ai_agent` 权限。两种入口都复用平台 Provider、fallback、账号预算、插件 quota、usage 和密钥隔离。

当系统设置中 **AI 平台能力**关闭时，worker 不加载 Provider；正常热切换会把现有插件上下文的 `ctx.ai` 置为 `None`，新上下文也不会注入该 facade，因此插件应在调用前先检查 `ctx.ai`。如果切换竞态中仍有旧代码持有先前取得的 `PluginAI` facade 并继续调用，facade 会抛出结构化 `AIUnavailableError`。新插件可声明 `requires_platform_capabilities=["ai"]`，让路由层提前裁剪依赖 AI 的入口。详见 [平台能力热插拔](PLATFORM-CAPABILITIES.md)。

## Event Bus 主路径写法

```python
async def on_event(self, ctx, payload):
    message = payload["message"]
    chat = payload["chat"]
    chat_id = message.get("chat_id") or chat["id"]
    reply_to = message.get("message_id")

    if ctx.ai is None:
        return [{
            "type": "send_message",
            "chat_id": chat_id,
            "reply_to_message_id": reply_to,
            "text": "本插件需要 ai_text 权限",
        }]

    providers = await ctx.ai.list_providers()
    result = await ctx.ai.complete(
        system="你是一个简洁助手。",
        user=message.get("text") or "总结这段内容",
        provider_tag="chat",
        max_tokens=512,
        timeout_seconds=30,
    )
    return [{
        "type": "send_message",
        "chat_id": chat_id,
        "reply_to_message_id": reply_to,
        "text": result.text,
    }]
```

也可以在交互入口中调用 `ctx.messages.send(...)` 缓存同等标准动作。最终版插件不应把 `event.edit(...)` 作为公共群互动或高频交互的主输出路径；命令式 `event.edit(...)` 只适合管理员命令兼容示例。

## Provider 选择

- `route`：显式选择路由模式，取值 `fixed` / `tag` / `auto`。
  - `fixed`：固定某个 provider，需同时传 `provider`（id 或 name）。
  - `tag`：按用途标签选择，需同时传 `provider_tag`；平台在可用 provider 中挑成本优先的匹配项。
  - `auto`：平台自动挑选（chat 优先、成本优先）。
  - 留空时按旧参数推断（给了 `provider`→fixed、给了 `provider_tag`→tag、都没有→auto），保持向后兼容。
- `provider_tag`：推荐写法。按用途标签选择 provider。
- `provider`：需要固定 provider 时可传 provider id 或 provider name。
- `tag` / `tags`：兼容别名，已 deprecated；新插件请使用 `provider_tag`。

`complete()` / `run_agent()` 返回值带 `routing` 脱敏摘要（`mode` / `provider_id` / `provider_name` / `matched_tag` / `model` / `api_format` / `client_identity_profile` / `used_fallback`）。插件**不能**指定 UA、客户端身份、API Key、Base URL、代理、内部分类器或全局 fallback 策略——这些由平台统一决定，路由摘要也绝不含密钥 / Base URL / 代理。`run_agent()` 会预先排除没有已启用模型的 provider（无法支撑 tools 调用）。

Provider 的 `execution_backend` 同样由平台配置决定，插件不能要求某次调用强制标准 API 直连或 Gateway。选择 `codex_gateway` 的 Responses Provider 时，`complete()`、`stream_complete()` 和 `run_agent()` 仍复用同一 Client Builder：Gateway 只替换传输层，工具 handler、插件 quota、账号预算、fallback 和 usage 继续由 TelePilot 管理。同一 Provider 的 Gateway 失败不会偷跑 direct；可在尚未输出文本时按平台策略 fallback 到其它 Provider。实际 backend、Gateway 版本、request ID 和失败阶段写入「AI → 近期调用」，不加入插件可见的 routing 摘要。详见 [Codex 客户端兼容模式（Gateway）](./CODEX-GATEWAY.md)。

同一 Provider 可配置多把 API Key（密钥池）：选钥、密钥级冷却和 prompt cache 亲和都由平台在 Provider 内部完成，插件不能指定 Key，routing 摘要也不含 Key 信息；命中的 Key 标签只写入「AI → 近期调用」。详见 [LLM 密钥池与模型元数据](./LLM-KEY-POOL.md)。

## 联网搜索、识图与推理模型

`complete()` 的返回值 `AIResult` 字段：`text`（只含模型最终答案）、`model`、`provider_id`、`provider_name`、`used_fallback`、`input_tokens`、`output_tokens`、`sources`（联网搜索引用）、`routing`、`reasoning`（推理过程，只读参考，不要发给用户）、`reasoning_tokens`、`finish_reason`。

| 能力 | 写法 | 权限 / 版本 | 不可用时 |
| --- | --- | --- | --- |
| 原生联网搜索 | `complete(..., web_search=True, web_search_context_size="low"\|"medium"\|"high")` | `ai_text`；0.106.0-beta.1+ | 所选 Provider 与 fallback 链都不支持时抛 `AICapabilityError`，可去掉该参数重试 |
| 识图 | `complete(..., images=[bytes, ...])`（最多 4 张、每张 ≤ 8MB）；交互 Bot 收到的图片用 `await ctx.ai.fetch_bot_image(file_id)` 下载 | `ai_vision`（须与 `ai_text` 或 `ai_agent` 同时声明）；0.106.0-beta.1+；`ctx.ai.vision_enabled` 表示是否已授权 | 未授权时报错；不支持图片的 Provider 抛 `AICapabilityError` |
| 关闭 / 开启思考 | `complete(..., thinking="disabled"\|"enabled")` | 0.106.0-beta.2+；只对智谱 / Z.ai、DeepSeek、DashScope（Qwen3）生效，其它上游忽略 | — |
| 推理被截断 | 模型只给出推理、没有最终答案（含 `finish_reason=length`） | 0.106.0-beta.2+ | 抛 `AIResponseTruncated`（`AIUnavailableError` 子类），fallback 链会先尝试下一个 Provider |

注意：`complete()` 自 0.73 起就接受并忽略未知关键字参数，所以在旧平台上传 `images` / `web_search` / `thinking` **不会报错，但也不会生效**。依赖这些能力时要么把 `min_telepilot_version` 提到表中版本，要么先用 `inspect.signature(ctx.ai.complete).parameters` 或 `getattr(ctx.ai, "vision_enabled", False)` 探测。更多细节见 [API 参考 4.0](./PLUGIN-API-REFERENCE.md#40-受控-facadectxhttp-与-ctxai)。

## 原生文本流

需要边生成边处理时使用 `ctx.ai.stream_complete()`；每次迭代只会返回 Provider 原生流中真实抵达的文本 delta，不会把完整结果拆字模拟：

```python
parts = []
async for delta in ctx.ai.stream_complete(
    system="你是一个简洁助手。",
    user="总结这段内容",
    provider_tag="chat",
    max_tokens=512,
    timeout_seconds=30,
):
    parts.append(delta)
answer = "".join(parts)
```

`stream_complete()` 自 0.109.0-beta.1 起也接受 `extra_body={...}`（与 `complete()` 同一套白名单，见 [AI 请求体透传](./PLUGIN-API-REFERENCE.md#ai-请求体透传extra_body)）；流式不中途换 Provider，选中 Provider 不支持某字段时直接抛 `AICapabilityError`。

要把流式文本显示在 Telegram 里（编辑「🤔」占位或私聊草稿），用平台的节流编辑器 `ctx.messages.stream_edit(chunks=ctx.ai.stream_complete(...), ...)`，频控、429 与最终 Rich 编辑由平台处理，见 [插件流式回复](./PLUGIN-STREAMING.md)。

该窄接口支持 Chat Completions、Responses 与 Anthropic Messages。流开始后不会中途切 Provider；消费者取消、超时或上游中断会按已发起的请求保守结算预算和插件 quota。若上游接受了 `stream=true` 却返回普通 JSON，迭代器会把真实完整文本作为一个块交给插件，不会拆字，也不会再发起第二次请求。消费者必须遍历到自然结束，提前 `break` 或关闭迭代器会按中断调用结算。

## 有界 Agent 与工具调用

Agent 工具采用双白名单：插件 manifest 先声明工具名称和参数 Schema，宿主代码调用 `run_agent()` 时还必须传入同名 handler。只有两侧同时存在的工具才会暴露给模型，插件不能用模型返回的任意名称调用 Python 函数。

```json
{
  "permissions": ["ai_agent"],
  "capabilities": {"agent_tools": {"enabled": true}},
  "agent_tools": [
    {
      "name": "lookup_order",
      "description": "按订单号查询只读摘要",
      "parameters": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": false
      },
      "read_only": true,
      "strict": true
    }
  ]
}
```

```python
async def lookup_order(arguments):
    return await query_order_summary(arguments["order_id"])

result = await ctx.ai.run_agent(
    system="只根据工具结果回答，不要猜测订单状态。",
    user="查询订单 A-100",
    handlers={"lookup_order": lookup_order},
    max_steps=6,
    max_tool_calls=12,
    max_total_tokens=12000,
    timeout_seconds=90,
)
```

- `ai_agent` 不继承 `ai_text`，反之亦然；按实际入口最小授权。
- 同一轮全部为 `read_only=true` 时可并行执行；只要包含副作用工具就按模型顺序串行执行。
- 平台限制总轮数、单轮/总工具数、重复同参调用、会话 token 和总超时；到达轮数上限后只允许无工具的最终总结。
- 每次模型调用仍单独经过账号预算、fallback 和 usage；插件 quota 按会话上限预留，并按成功或异常前已实际消耗的累计 token 结算。
- 工具开始/完成会写 ActionEvent。handler 异常会作为结构化工具错误返回模型，不会把未声明函数变成可执行入口。

## Quota 与脱敏

- `ctx.ai.complete()` 复用平台 LLM Provider 池、fallback 链、账号级预算和 usage 记录。
- 插件传入的 `max_tokens` 与超时会被平台上限收紧，不能绕过账号配额。
- `complete()` 的 `timeout_seconds`（0.106.0-beta.20+）是**整次调用的总时限**：所有重试与备用 Provider 都在这段时间内完成。有备用 Provider 时，主 Provider 单次尝试的超时会被收紧、给备用预留约三分之一的时间；超时 / 连接失败 / 5xx / 429 不再在同一 Provider 上反复重试，而是直接切备用；同一 Provider 连续不可用会被短暂熔断（约 30 秒内优先跳过）。所以插件按自己的 handler 时限传 `timeout_seconds`，主 Provider 卡住时也能在时限内拿到备用的回答。
- `ctx.ai.list_providers()` 只返回脱敏元数据，例如 provider 名称、默认模型、标签和成本层级；`models[]` 只含 `id`、`name`、`label`、`display_name`、`modality`、`max_tokens`、`context_window` 与 `enabled`（0.106.0-beta.13+，插件据此只列已启用模型——未启用模型传给 `complete(model=...)` 会被拒绝）。
- 插件不会拿到 `api_key_enc`、明文 API Key、`base_url` 或代理 URL。
- 不要在插件日志里记录用户完整隐私输入或模型完整输出；需要排障时只记录长度、截断摘要或 request id。

## 示例

完整最小示例见 `examples/plugins/with_ai/`。CI 只导入示例并校验 manifest / plugin 元数据，不会执行真实 AI 请求。

## 配额限制

`plugin_ai_max_output_tokens` 是宿主对单次插件 AI 输出的保护上限，默认为 `131072`。它只是上限：插件仍必须通过 `max_tokens` 主动请求所需长度，普通对话不会因此自动变长。AI 题库、离线摘要等长时间任务仍应使用可恢复的分段格式，避免上游模型的实际最大输出低于请求值时丢失整批结果。

平台从 `system_setting` 的 `plugin_ai_quota` 读取插件 AI 配额配置。示例：

```json
{
  "per_minute_tokens": 20000,
  "daily_tokens": 200000,
  "plugins": {
    "sum": {
      "per_minute_tokens": 5000,
      "daily_tokens": 50000
    }
  }
}
```

- `per_minute_tokens` 是每分钟 token 软上限，`daily_tokens` 是自然日 token 软上限。
- `plugins.{key}` 可覆盖单个插件的全局配置，例如上面的 `sum`。
- 任一限制设为 `0` 表示不限制。
- 超限时，插件会收到 `AIQuotaError`；平台同时写入一条 `LLMUsage(success=False, error_type="plugin_quota_exceeded")`，可在 Usage 页排查。
- Redis 不可用时会降级为 DB 检查，但并发预扣保护会暂时关闭；生产环境建议保留 Redis 可用性监控。
- DB 降级统计会同时聚合 `plugin:{key}` 与 `plugin:{key}:*` usage，因此 `run_agent()` 和普通文本调用共享同一插件额度，不会因 source 子类型漏计。
- token 估算是软上限：平台先把 `system` 与 `user` 合并后的 UTF-8 字节数按每 4 字节向上取整（`(bytes + 3) // 4`，至少 1），再加上本次请求的 `max_output_tokens` 作为预扣值；并发尖峰仍可能瞬时越限。
- 跨午夜的请求按 acquire 当时所属的自然日记账，软上限场景误差可接受。

## AI 玩法组件

`app.worker.plugins.ai_components` 提供三个"AI 主路径 + 确定性降级"的玩法组件，供插件
直接组合使用。它们**不持有全局状态**，AI 依赖由构造参数注入（生产传 `ctx.ai`，测试传
stub），可直接单测。

两条硬约束：

- **降级不依赖任何 AI 调用成功**：平台虽然提供重试和 fallback，但网络、预算、模型能力或
  Provider 配置仍可能让整条链失败，因此每个组件都有一条不调用 AI 也能走通的确定性降级路径。
- **只走 `ctx.ai` 正常计量路径**：组件不 import `llm_client` / `llm_invoke` /
  `llm_runtime`，所有模型调用都经 `ctx.ai.complete()`，天然继承 quota、账号预算、usage
  记录与 token 钳制。禁止在组件里新增任何绕过计量的直连调用。

### QuizMaker — 出题（AI 主，题库降级）

```python
from app.worker.plugins.ai_components import QuizMaker

maker = QuizMaker(ctx.ai)          # ctx.ai 为 None 时直接走内置题库
quiz = await maker.generate("成语")  # -> Quiz(question, answer, hints, accepted, topic, source)
```

- 优先让 AI 出题；AI 失败/超时/无 provider/返回无法解析为 JSON 时，降级到随包题库
  （`ai_components_quiz_bank.json`，≥30 题，按 `谜语 / 成语 / 常识` 分组）。
- `topic` 命中某个题库分组则从该组抽题，否则从全部题目里抽；`Quiz.source` 标注 `"ai"`
  或 `"builtin"`。
- 降级选题只依赖构造时可注入的 `rng`（`random.Random`），便于测试确定性；不碰 AI。

### AnswerJudge — 判题（规则先行，AI 兜底，失败 unsure）

```python
from app.worker.plugins.ai_components import AnswerJudge, JudgeOutcome

judge = AnswerJudge(ctx.ai)
verdict = await judge.judge(question, expected, answer, accepted=[...], regex=r"...")
if verdict.outcome is JudgeOutcome.CORRECT:
    ...
```

判定顺序（严格短路，命中即返回，**规则命中时绝不调用 AI**）：

1. 精确匹配：去首尾空白后逐字相等；
2. 归一化匹配：NFKC（全角转半角）+ 去所有空白 + casefold 后相等；
3. 可选正则：`regex`（`re.fullmatch`，忽略大小写）；
4. 以上都判不了时，**仅当 `ctx.ai` 可用**才问 AI（prompt 要求只回 `yes` / `no` /
   `unsure`）。

降级语义：AI 失败/超时/返回不可解析 → `UNSURE`；无 AI（`ctx.ai=None`）→ `UNSURE`。
规则只负责"确认正确"，从不独自宣判"错误"，因此拿不准时一律返回 `UNSURE`，交由插件走
保守分支（例如不判对、不派奖、转人工）。`Verdict` 暴露 `.correct` / `.incorrect` /
`.unsure` 便捷属性与 `source`（`exact` / `normalized` / `regex` / `ai` / `no_ai` /
`ai_failed` / `ai_unparsed`）。

### PersonaChat — 人设对话（历史裁剪，失败静默）

```python
from app.worker.plugins.ai_components import PersonaChat

chat = PersonaChat(ctx.ai)  # 内置 friendly / tsundere / sage，可用 personas= 覆盖或追加
reply = await chat.reply("friendly", history, user_text)  # -> str | None
if reply:
    await ctx.messages.send(chat_id=..., text=reply)
```

- 按 persona preset（system prompt 模板）生成一句回复；`history` 支持 `{"role","text"}`
  字典或 `(role, text)` 二元组，按 `persona.max_history` 轮裁剪，再按字符软上限二次裁剪。
- 单次输出 `max_tokens` 交给 `PluginAI` 钳制到账号上限，组件不重复实现配额。
- 降级语义：未知 persona / 空输入 / AI 不可用 / AI 失败 / 空回复 → 返回 `None`，插件静默
  （不发消息）。

### 示例

完整用法见 `examples/plugins/with_ai_components/`：用 `QuizMaker` + `AnswerJudge`
做一个最小问答局（`,quiz_new [主题]` 出题、`,quiz_answer 答案` 判题），并演示无 AI 时
的题库降级与 `unsure` 保守分支。
