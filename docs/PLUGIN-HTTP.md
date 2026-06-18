# TelePilot 插件 HTTP facade

本文是当前维护的 `ctx.http` facade 参考；`ctx.ai` 的完整说明仍见 [PLUGIN-AI.md](./PLUGIN-AI.md)。

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

注意：内置插件会拿到完整运行时能力；远程/第三方插件拿到的是受限上下文：`ctx.client` 为 `SandboxClient`，指令 handler 中传入的 `client` 参数与 `ctx.client` 同源（同样是 sandbox client），`ctx.engine` 和 `ctx.redis` 为 `None`，只能通过声明过的权限和 `ctx.scheduler` facade 使用有限能力。

### 4.0 受控 facade：ctx.http 与 ctx.ai

第三方插件可以使用两个受控 facade，但必须在 Manifest 中显式声明权限；未声明或策略不完整时字段会是 `None`：

- `ctx.http`：声明 `permissions=["external_http"]` 且填写 `allowed_hosts` 后注入。它限制协议、域名、超时、响应大小，并在发起请求前阻断 localhost/内网/链路本地地址。默认走账号代理；只有 Manifest 的 `http={"allow_direct": true}` 且账号配置请求 direct 时才允许直连。
- `ctx.ai`：声明 `permissions=["ai_text"]` 后注入。它复用 TelePilot 的 LLM Provider 池、fallback 链、账号级预算和 usage 记录；插件只能拿到脱敏 provider 元数据，不能读取 `api_key_enc`、`base_url` 或代理 URL。
- `ctx.ai.complete()` 推荐用 `provider_tag` 按用途选择 provider；`tag` / `tags` 是兼容别名且已 deprecated，新插件不要依赖它们作为主要入口。
- `ctx.ai.list_providers()` 可用于展示当前账号可见的脱敏 provider 摘要；更完整的 AI facade 说明见 `docs/PLUGIN-AI.md`。

示例：

```python
if ctx.http is None:
    await event.edit("本插件需要 external_http 权限和 allowed_hosts")
    return True
response = await ctx.http.get("https://api.github.com/zen")

if ctx.ai is None:
    await event.edit("本插件需要 ai_text 权限")
    return True
providers = await ctx.ai.list_providers()
result = await ctx.ai.complete(
    system="你是助手",
    user="总结这段文本",
    provider_tag="chat",
    max_tokens=512,
)
```

## allowed_hosts 匹配规则

`ctx.http` 只允许访问 Manifest 声明的 `allowed_hosts`。匹配语义与运行时 `PluginHTTP` 保持一致：

- `example.com` 只匹配 `example.com`。
- `*.example.com` 匹配一层子域名，例如 `api.example.com`，不匹配 `example.com` 或 `x.api.example.com`。
- `**.example.com` 匹配 `example.com` 以及任意层级子域名。

## SSRF 与响应限制

运行时只允许 `http` / `https` URL，并在连接前阻断这些目标：

- `localhost` 和 `*.localhost`。
- loopback、私网、链路本地、保留地址、组播地址、非 global IP。
- DNS 解析结果落到上述地址的 host。

响应体会流式计数，超过 `max_response_bytes` 会抛出 `PluginHTTPResponseTooLarge`，不会等完整 body 读完后才拒绝。

## 代理与 direct mode

默认网络模式是 `account_proxy`，会使用账号代理。只有 Manifest 显式声明 `http={"allow_direct": true}`，并且账号配置请求 `network_mode="direct"` 时，插件才可以直连；否则 direct 会被拒绝。
