"""内置插件：定时任务（cron / once / interval）。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from croniter import CroniterBadCronError, croniter
from telethon.errors import FloodWaitError

from app.db.base import AsyncSessionLocal
from app.db.models.command import LLMProvider
from app.db.models.feature import FEATURE_SCHEDULER
from app.db.models.rule import Rule
from app.services.llm_client import LLMError, build_client
from app.worker.plugins.base import Plugin, PluginContext, register

_TICK_SECONDS = 30
_MAX_MESSAGE_LEN = 3900


@register
class SchedulerPlugin(Plugin):
    """按 rule.config 驱动定时动作。"""

    key = FEATURE_SCHEDULER
    display_name = "定时任务"

    async def on_startup(self, ctx: PluginContext) -> None:
        ctx.scheduler_task = asyncio.create_task(self._tick_loop(ctx))
        if ctx.log is not None:
            await ctx.log("info", "[scheduler] started")

    async def on_shutdown(self, ctx: PluginContext) -> None:
        task = getattr(ctx, "scheduler_task", None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if ctx.log is not None:
            await ctx.log("info", "[scheduler] stopped")

    async def _tick_loop(self, ctx: PluginContext) -> None:
        while True:
            try:
                await self._tick_once(ctx)
            except Exception as exc:  # noqa: BLE001
                if ctx.log is not None:
                    await ctx.log("error", f"[scheduler] tick error: {type(exc).__name__}: {exc}")
            await asyncio.sleep(_TICK_SECONDS)

    async def _tick_once(self, ctx: PluginContext) -> None:
        now = datetime.now(UTC)
        for rule in ctx.rules:
            cfg = dict(rule.config or {})
            due, next_fire = self._resolve_due(cfg, now)

            changed = False
            next_iso = _to_iso(next_fire)
            if cfg.get("next_fire") != next_iso:
                cfg["next_fire"] = next_iso
                changed = True

            if not due:
                if changed:
                    await self._persist_rule_config(rule.id, cfg)
                continue

            fired_at = datetime.now(UTC)
            ok = await self._fire(ctx, rule.id, cfg)
            if ok:
                cfg["last_fire"] = _to_iso(fired_at)
                cfg["last_result"] = "ok"
                cfg["last_error"] = None
                self._advance_after_fire(cfg, fired_at)
            else:
                cfg["last_result"] = "error"
                # 失败时下一次 tick 继续尝试；不推进 next_fire

            await self._persist_rule_config(rule.id, cfg)

    def _resolve_due(self, cfg: dict[str, Any], now: datetime) -> tuple[bool, datetime | None]:
        kind = str(cfg.get("kind") or "cron").lower()
        if kind == "once":
            return self._resolve_once(cfg, now)
        if kind == "interval":
            return self._resolve_interval(cfg, now)
        return self._resolve_cron(cfg, now)

    def _resolve_once(self, cfg: dict[str, Any], now: datetime) -> tuple[bool, datetime | None]:
        fire_at = _parse_dt(cfg.get("fire_at"))
        if fire_at is None:
            return False, None
        if cfg.get("last_fire"):
            return False, fire_at
        return fire_at <= now, fire_at

    def _resolve_interval(self, cfg: dict[str, Any], now: datetime) -> tuple[bool, datetime | None]:
        interval = _to_positive_int(cfg.get("interval_sec"))
        if interval <= 0:
            return False, None
        last = _parse_dt(cfg.get("last_fire"))
        if last is None:
            # 首次：立即触发一次
            return True, now
        next_fire = last + timedelta(seconds=interval)
        return next_fire <= now, next_fire

    def _resolve_cron(self, cfg: dict[str, Any], now: datetime) -> tuple[bool, datetime | None]:
        expr = str(cfg.get("cron") or "").strip()
        if not expr:
            return False, None

        next_fire = _parse_dt(cfg.get("next_fire"))
        if next_fire is None:
            try:
                next_fire = croniter(expr, now).get_next(datetime)
            except (CroniterBadCronError, ValueError):
                return False, None
            return False, next_fire

        return next_fire <= now, next_fire

    def _advance_after_fire(self, cfg: dict[str, Any], fired_at: datetime) -> None:
        kind = str(cfg.get("kind") or "cron").lower()
        if kind == "once":
            cfg["enabled"] = False
            cfg["next_fire"] = None
            return
        if kind == "interval":
            interval = _to_positive_int(cfg.get("interval_sec"))
            cfg["next_fire"] = _to_iso(fired_at + timedelta(seconds=max(interval, 1)))
            return

        expr = str(cfg.get("cron") or "").strip()
        if not expr:
            cfg["next_fire"] = None
            return
        try:
            cfg["next_fire"] = _to_iso(croniter(expr, fired_at).get_next(datetime))
        except (CroniterBadCronError, ValueError):
            cfg["next_fire"] = None

    async def _fire(self, ctx: PluginContext, rule_id: int, cfg: dict[str, Any]) -> bool:
        action = cfg.get("action")
        if not isinstance(action, dict):
            if ctx.log is not None:
                await ctx.log("error", f"[scheduler] rule={rule_id} missing action")
            cfg["last_error"] = "missing action"
            return False

        action_type = str(action.get("type") or "send_message").lower()
        try:
            if action_type == "send_message":
                await self._action_send_message(ctx, action)
            elif action_type == "run_command":
                await self._action_run_command(ctx, action)
            elif action_type == "call_llm":
                await self._action_call_llm(ctx, action)
            else:
                raise ValueError(f"unknown action.type={action_type}")
            return True
        except Exception as exc:  # noqa: BLE001
            cfg["last_error"] = f"{type(exc).__name__}: {exc}"
            if ctx.log is not None:
                await ctx.log("error", f"[scheduler] rule={rule_id} fire failed: {type(exc).__name__}: {exc}")
            return False

    async def _action_send_message(self, ctx: PluginContext, action: dict[str, Any]) -> None:
        target = int(action["target_chat_id"])
        text = str(action.get("text") or "").strip()
        if not text:
            raise ValueError("send_message requires non-empty text")
        await self._send_with_ratelimit(ctx, target, text)

    async def _action_run_command(self, ctx: PluginContext, action: dict[str, Any]) -> None:
        target = int(action.get("target_chat_id") or 0)
        command = str(action.get("command") or action.get("text") or "").strip()
        if not command:
            raise ValueError("run_command requires command/text")
        await self._send_with_ratelimit(ctx, target or "me", command)

    async def _action_call_llm(self, ctx: PluginContext, action: dict[str, Any]) -> None:
        provider_id = int(action["provider_id"])
        prompt = str(action.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("call_llm requires prompt")

        row = await self._get_provider_row(provider_id)
        if row is None:
            raise ValueError(f"provider_id={provider_id} not found")
        llm = build_client(row, override_model=action.get("model"))

        system_prompt = str(action.get("system_prompt") or "你是简洁有用的中文助手。")
        max_tokens = _to_positive_int(action.get("max_tokens")) or 256

        try:
            result = await llm.complete(system_prompt, prompt, max_tokens=max_tokens)
        except LLMError:
            raise

        text = (result.text or "").strip() or "(empty)"
        target = int(action["target_chat_id"])
        await self._send_with_ratelimit(ctx, target, text[:_MAX_MESSAGE_LEN])

    async def _send_with_ratelimit(self, ctx: PluginContext, peer: int | str, text: str) -> None:
        peer_id = int(peer) if isinstance(peer, int) else None
        decision = await ctx.engine.acquire(
            ctx.account_id,
            "send_message_group",
            peer_id=peer_id,
        )
        if not decision.allowed:
            if ctx.log is not None:
                await ctx.log("info", f"[scheduler] ratelimited drop outcome={decision.outcome}")
            return
        if decision.wait_seconds and decision.wait_seconds > 0:
            await asyncio.sleep(float(decision.wait_seconds))

        try:
            await ctx.client.send_message(peer, text)
        except FloodWaitError as exc:
            await ctx.engine.on_flood_wait("send_message_group", exc, peer_id=peer_id)
            await asyncio.sleep(min(int(getattr(exc, "seconds", 0) or 0), 60))
            await ctx.client.send_message(peer, text)

    async def _get_provider_row(self, provider_id: int) -> LLMProvider | None:
        async with AsyncSessionLocal() as db:
            return await db.get(LLMProvider, provider_id)

    async def _persist_rule_config(self, rid: int, cfg: dict[str, Any]) -> None:
        async with AsyncSessionLocal() as db:
            row = await db.get(Rule, rid)
            if row is None:
                return
            row.config = cfg
            await db.commit()


PLUGIN_CLASS = SchedulerPlugin



def _parse_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        s = str(raw).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)



def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat()



def _to_positive_int(raw: Any) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return v if v > 0 else 0


__all__ = ["SchedulerPlugin", "PLUGIN_CLASS"]
