from app.worker.plugins.base import Plugin, PluginContext, register


@register
class ContextEchoPlugin(Plugin):
    key = "context_echo"
    display_name = "Context Echo"
    message_channels = {"incoming"}
    owner_only = True

    async def on_startup(self, ctx: PluginContext) -> None:
        # 配置与账号范围：都从 PluginContext 读取，不跨账号查询
        self._command = str(ctx.config.get("command") or "cecho").strip() or "cecho"

        async def _handler(client, event, args: list[str], account_id: int, runtime_ctx: PluginContext) -> None:
            # 运行时访问：只用公开上下文对象；第三方插件应把 engine/redis 视为可选能力
            await runtime_ctx.log(
                "info",
                "context_echo.triggered",
                account_id=account_id,
                feature=runtime_ctx.feature_key,
            )
            payload = " ".join(args) if args else "ok"
            await event.reply(f"[aid={account_id}] {payload}")

        self.commands = {self._command: _handler}
