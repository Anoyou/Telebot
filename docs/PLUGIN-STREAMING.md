# 插件流式回复：节流编辑与私聊草稿

> 适用：TelePilot 0.106.0-beta.20+。能力标记 `getattr(ctx.messages, "streaming_edit", False) is True`。
> 字段与兼容语义的完整参考仍以 [PLUGIN-API-REFERENCE.md](./PLUGIN-API-REFERENCE.md) 为准；本页只讲流式回复这一件事。

长回答如果等模型完整生成后才把「🤔」改成正文，用户要盯着占位看十几秒。平台已经提供原生文本流 `ctx.ai.stream_complete()`（见 [PLUGIN-AI.md](./PLUGIN-AI.md#原生文本流)），但插件如果每收到一段就 `editMessageText`，会撞上 Telegram 对同一会话的编辑频控（429），还要自己处理 Rich 转换、截断和失败回退。

`ctx.messages.stream_editor()` / `ctx.messages.stream_edit()` 把这些都收进平台：

| 平台负责 | 说明 |
| --- | --- |
| 节流 | 只保留最新快照；私聊 / inline 约 1 次/秒，群约 1.5 秒一次（`min_interval` 可调，限制在 0.5–10 s）；`min_chars` 控制两次中间编辑之间的最少新增字数 |
| 首字不延迟 | 第一段文本当场发出，之后才按节奏合并 |
| 429 | 中间编辑**不等待**限流；被 429 就按 `retry_after` 推迟、到期后用最新快照重发；“message is not modified” 静默忽略 |
| 轻量中间态 | 中间编辑直接走交互 Bot 的 `editMessageText`（或私聊 `sendMessageDraft`），默认纯文本，不写动作记录；超过 4000 字时中间态只显示开头并以“…”收尾 |
| 最终一次走标准动作 | `finish()` 先停掉节流器并等在途请求结束，再经 `ctx.messages.apply` 发出最终 `edit_message`（草稿模式是 `send_message`），Rich Message、紧凑表格、表格默认样式、inline 编辑、动作记录与 Trace 全部沿用既有链路 |

中间编辑只支持交互 Bot 通道。拿不到交互 Bot token、或插件处于开发模式 dry-run 时，中间更新静默跳过（`stats["disabled"] = True`），`finish()` 照常执行——所以同一份代码在不支持的环境里会自然退化成“一次性回答”。

## 目标：编辑占位还是私聊草稿

| 传入 | 模式 | 行为 |
| --- | --- | --- |
| `chat_id` + `message_id` | `edit` | 编辑已发出的占位（例如「🤔」） |
| `inline_message_id` | `edit` | 编辑 inline 消息（访客模式的占位） |
| 只有私聊 `chat_id`（> 0） | `draft` | Bot API 草稿流式显示；`finish()` 清空草稿并发送真实消息 |

`mode="auto"`（默认）按上表自动选择；群聊没有占位时会抛 `ValueError`（群里不能用草稿）。

## 最短写法

```python
async def answer(ctx, chat_id, placeholder_id, system, user):
    if not getattr(ctx.messages, "streaming_edit", False):
        result = await ctx.ai.complete(system=system, user=user, timeout_seconds=40)
        return await ctx.messages.apply([{
            "type": "edit_message", "chat_id": chat_id, "message_id": placeholder_id,
            "text": result.text, "send_via": "interaction_bot",
        }])

    return await ctx.messages.stream_edit(
        chunks=ctx.ai.stream_complete(system=system, user=user, timeout_seconds=40),
        chat_id=chat_id,
        message_id=placeholder_id,
    )
```

`stream_edit()` 边收边显示，流结束后用累计文本做最终编辑，返回 `apply()` 的结果，并附 `text`（完整文本）与 `stream`（统计）。流中途抛异常时会放弃流式（草稿会被清空）并把异常继续抛出，插件照常走自己的失败分支；流为空时返回 `{"ok": False, "error": "empty_stream"}`，不会发出空消息。

## 需要后处理时：用编辑器对象

模型原文往往还要脱敏、截断、追加来源或转成 Rich Message。这时自己驱动编辑器：

```python
editor = ctx.messages.stream_editor(chat_id=chat_id, message_id=placeholder_id)
try:
    async for delta in ctx.ai.stream_complete(system=system, user=user, timeout_seconds=40):
        await editor.append(delta)          # 节流的中间编辑（纯文本）
except Exception:
    await editor.abort()                    # 不做最终编辑，占位留给失败分支处理
    raise

answer = sanitize(editor.text)              # editor.text 是完整原文
result = await editor.finish(markdown=answer + sources_md, reply_markup=buttons)
if not result.get("ok"):
    ...  # result["errors"] 里有最终编辑失败的原因（已脱敏）
```

编辑器方法：

| 方法 | 说明 |
| --- | --- |
| `await update(text)` | 用完整快照更新（节流），返回本次是否当场发出 |
| `await append(delta)` | 追加增量（节流） |
| `await consume(chunks)` | 遍历异步文本流并逐段 `append`，返回完整文本 |
| `editor.text` | 目前累计的完整文本（未截断、未渲染） |
| `await finish(...)` | 停掉节流器后发最终内容：默认用累计文本；可传 `text` + `parse_mode`，或 `html` / `markdown` / `blocks` 之一（Rich Message，可加 `compact_tables=True`），以及 `reply_markup`；草稿模式还可传 `reply_to_message_id`。返回 `apply()` 结果并附 `stream` 统计 |
| `await abort()` | 放弃流式，不做最终编辑（草稿会被清空） |

`stream_editor()` 参数：`chat_id`、`message_id`、`inline_message_id`、`mode`（`auto` / `edit` / `draft`）、`parse_mode`（中间态 `plain` / `html`，默认 `plain`）、`min_interval`、`min_chars`、`render`（可选的同步函数，只在真正发出中间编辑时对最新快照调用一次，例如把未闭合的 Markdown 渲染成 HTML；配合 `parse_mode="html"`）。

> 中间态建议保持纯文本：半截 Markdown / HTML 很容易渲染失败（Telegram 会拒绝整条编辑）。最终格式放到 `finish()` 里一次性给出。

## 与超时、fallback 的关系

- `ctx.ai.stream_complete()` 开始输出后不会中途切 Provider；首个 delta 之前失败时平台按 fallback 链切换。
- `ctx.ai.complete()` 的 `timeout_seconds` 自 0.106.0-beta.20 起是**整次调用的总时限**：重试和备用 Provider 都在这段时间内完成，主 Provider 超时 / 不可用时会直接切备用（见 [PLUGIN-AI.md](./PLUGIN-AI.md#quota-与脱敏)）。
- 插件自己的 handler 时限仍然有效：`stream_edit()` 不会替你截断流，需要时用 `asyncio.timeout(...)` 包住，并在超时分支里 `await editor.abort()` 后走失败提示。

## 示例：interaction-ai-chat 改流式（参考改法）

插件当前流程是「发 🤔 占位 → `ctx.ai.complete()` → 脱敏 / 截断 / 格式化 → 编辑占位」。改流式只动“调用模型”和“最终编辑”两处：

1. **能力探测**：`streaming = getattr(ctx.messages, "streaming_edit", False) and hasattr(ctx.ai, "stream_complete")`；同时要求有可编辑的占位（`chat_id` + `message_id`，或访客模式的 `inline_message_id`）。不满足时保持现有 `complete()` 路径。
2. **调用模型**：把 `_call_for_final_answer` 里的 `complete(...)` 换成
   `editor = ctx.messages.stream_editor(chat_id=..., message_id=placeholder_id)`（访客模式传 `inline_message_id=`），
   `async with asyncio.timeout(remaining): async for d in ctx.ai.stream_complete(...): await editor.append(d)`。
   联网搜索、识图、`thinking` 参数目前只在 `complete()` 上有，开启这些选项的请求继续走 `complete()`。
3. **后处理不变**：用 `editor.text` 代替 `result.text` 进入现有的“PT 二次脱敏 → 指令形态拦截 → 截断 → 记忆写入”流程。中间态是未脱敏原文，如果 PT 脱敏或指令拦截是硬性要求，中间编辑应传 `render=` 做同等过滤，或对启用 PT 的会话关闭流式。
4. **最终编辑**：Rich 开启时 `await editor.finish(markdown=rich_markdown, reply_markup=...)`，否则 `await editor.finish(text=segments[0], parse_mode="html")`；多段回答的后续段落仍用现有 `send_message` 追加。
5. **失败分支**：超时 / 模型异常时先 `await editor.abort()`，再用现有失败模板编辑占位（重试按钮等逻辑不变）。
6. **配置**：建议加开关 `stream_reply`（默认开），并把 `min_telepilot_version` 保持不变、靠第 1 步探测兼容旧平台。
