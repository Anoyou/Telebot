"""内置插件：定时任务（cron / once / interval）。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from croniter import CroniterBadCronError, croniter
from sqlalchemy import select
from telethon.errors import FloodWaitError

from app.db.base import AsyncSessionLocal
from app.db.models.command import LLMProvider
from app.db.models.feature import FEATURE_SCHEDULER
from app.db.models.rule import Rule
from app.services.llm_client import LLMCallFailed, LLMError, build_client
from app.services.llm_dto import LLMProviderDTO
from app.services.llm_runtime import build_fallback_chain, call_with_fallback
from app.worker.plugins.base import Plugin, PluginContext, register

_TICK_SECONDS = 30
_MAX_MESSAGE_LEN = 3900


async def _get_system_tz() -> ZoneInfo | None:
    """从 system_setting 读取用户配置的时区，未配置时返回 None（按 UTC）。"""
    try:
        from app.db.models.system import SystemSetting

        async with AsyncSessionLocal() as db:
            row = await db.get(SystemSetting, "timezone")
            if row and isinstance(row.value, dict):
                tz_str = str(row.value.get("value", "")).strip()
                if tz_str:
                    return ZoneInfo(tz_str)
    except Exception:
        pass
    return None


def _croniter_next(
    expr: str, start_utc: datetime, tz: ZoneInfo | None
) -> datetime | None:
    """在指定时区下计算 cron 的下一次触发时间，返回 UTC。

    * tz=None 时行为与原来完全一致（直接按 UTC 解析）。
    * tz 非 None 时将 start_utc 转为本地时间后交给 croniter，
      再把结果转回 UTC，确保存储/比较全部在 UTC 下进行。
    """
    try:
        if tz is not None:
            local_now = start_utc.astimezone(tz)
            next_local: datetime = croniter(expr, local_now).get_next(datetime)
            return next_local.astimezone(UTC)
        return croniter(expr, start_utc).get_next(datetime)
    except (CroniterBadCronError, ValueError):
        return None


def _croniter_prev(
    expr: str, start_utc: datetime, tz: ZoneInfo | None
) -> datetime | None:
    """在指定时区下计算 cron 的上一次触发时间，返回 UTC。"""
    try:
        if tz is not None:
            local_now = start_utc.astimezone(tz)
            prev_local: datetime = croniter(expr, local_now).get_prev(datetime)
            return prev_local.astimezone(UTC)
        return croniter(expr, start_utc).get_prev(datetime)
    except (CroniterBadCronError, ValueError):
        return None


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
        tz = await _get_system_tz()
        for rule in ctx.rules:
            cfg = dict(rule.config or {})
            due, next_fire = self._resolve_due(cfg, now, tz)

            changed = False
            next_iso = _to_iso(next_fire)
            if cfg.get("next_fire") != next_iso:
                cfg["next_fire"] = next_iso
                changed = True
            # _resolve_cron 可能修改 cfg 的元数据字段（如 _last_cron），
            # 也需要持久化
            if cfg.pop("_config_dirty", False):
                changed = True

            if not due:
                if changed:
                    rule.config = cfg
                    await self._persist_rule_config(rule.id, cfg)
                continue

            fired_at = datetime.now(UTC)
            ok = await self._fire(ctx, rule.id, cfg)
            if ok:
                cfg["last_fire"] = _to_iso(fired_at)
                cfg["last_result"] = "ok"
                cfg["last_error"] = None
                self._advance_after_fire(cfg, fired_at, tz)
            else:
                cfg["last_result"] = "error"
                # 失败时下一次 tick 继续尝试；不推进 next_fire

            # 同步更新 rule.config，避免下一个 tick 读到旧的 next_fire 导致重复触发
            rule.config = cfg
            await self._persist_rule_config(rule.id, cfg)

    def _resolve_due(
        self, cfg: dict[str, Any], now: datetime, tz: ZoneInfo | None = None
    ) -> tuple[bool, datetime | None]:
        kind = str(cfg.get("kind") or "cron").lower()
        if kind == "once":
            return self._resolve_once(cfg, now)
        if kind == "interval":
            return self._resolve_interval(cfg, now)
        return self._resolve_cron(cfg, now, tz)

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

    def _resolve_cron(
        self, cfg: dict[str, Any], now: datetime, tz: ZoneInfo | None = None
    ) -> tuple[bool, datetime | None]:
        expr = str(cfg.get("cron") or "").strip()
        if not expr:
            return False, None

        next_fire = _parse_dt(cfg.get("next_fire"))
        last_cron = cfg.get("_last_cron")
        cron_changed = (last_cron is not None) and (last_cron != expr)

        # 每次都更新 _last_cron，供下次比较
        if last_cron != expr:
            cfg["_last_cron"] = expr
            cfg["_config_dirty"] = True

        if cron_changed:
            # cron 表达式被修改 — 从当前时间重新计算 next_fire
            nf = _croniter_next(expr, now, tz)
            if nf is None:
                return False, None
            # 检查是否刚好在一个触发窗口内（刚过触发点 ≤ 一个 tick）
            prev = _croniter_prev(expr, now, tz)
            if prev is not None and prev <= now and (now - prev) <= timedelta(seconds=_TICK_SECONDS + 5):
                return True, nf
            # 下一次触发时间即将到来（在一个 tick 内）
            if nf <= now + timedelta(seconds=_TICK_SECONDS):
                return True, nf
            return False, nf

        if next_fire is None:
            nf = _croniter_next(expr, now, tz)
            if nf is None:
                return False, None
            return False, nf

        return next_fire <= now, next_fire

    def _advance_after_fire(
        self, cfg: dict[str, Any], fired_at: datetime, tz: ZoneInfo | None = None
    ) -> None:
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
        nf = _croniter_next(expr, fired_at, tz)
        cfg["next_fire"] = _to_iso(nf)

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
        msg = await self._send_with_ratelimit(ctx, target, text)
        delete_after = _to_positive_int(action.get("delete_after"))
        if delete_after > 0 and msg is not None:
            asyncio.create_task(self._delete_message_after(ctx, msg, delete_after))

    async def _action_run_command(self, ctx: PluginContext, action: dict[str, Any]) -> None:
        target = int(action.get("target_chat_id") or 0)
        command = str(action.get("command") or action.get("text") or "").strip()
        if not command:
            raise ValueError("run_command requires command/text")
        msg = await self._send_with_ratelimit(ctx, target or "me", command)
        delete_after = _to_positive_int(action.get("delete_after"))
        if delete_after > 0 and msg is not None:
            asyncio.create_task(self._delete_message_after(ctx, msg, delete_after))

    async def _action_call_llm(self, ctx: PluginContext, action: dict[str, Any]) -> None:
        provider_id = int(action["provider_id"])
        prompt = str(action.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("call_llm requires prompt")

        row = await self._get_provider_row(provider_id)
        if row is None:
            raise ValueError(f"provider_id={provider_id} not found")

        # 使用 LLMProviderDTO 确保 api_format/proxy_url 等字段正确传递
        dto = LLMProviderDTO.from_orm_row(row)

        system_prompt = str(action.get("system_prompt") or "你是简洁有用的中文助手。")
        max_tokens = _to_positive_int(action.get("max_tokens")) or 256

        provider_rows = await self._get_provider_rows()
        provider_dtos = {
            int(p.id): LLMProviderDTO.from_orm_row(p)
            for p in provider_rows
        }
        provider_dtos[dto.id] = dto
        fallback_id = _to_positive_int(action.get("fallback_provider_id"))
        chain = build_fallback_chain(
            dto,
            providers=provider_dtos,
            fallback_provider_id=fallback_id or None,
            matched_tag="scheduler",
        )

        def _build_runtime_client(
            provider_dto: LLMProviderDTO,
            *,
            override_model: str | None = None,
            proxy_url: str | None = None,
        ):
            return build_client(
                _dto_to_fake_row(provider_dto),
                override_model=override_model,
                proxy_url=proxy_url or provider_dto.proxy_url,
            )

        try:
            result, _, _ = await call_with_fallback(
                chain,
                system_prompt,
                prompt,
                override_model=action.get("model"),
                max_tokens=max_tokens,
                client_factory=_build_runtime_client,
                account_id=ctx.account_id,
                source="scheduler",
            )
        except (LLMError, LLMCallFailed):
            raise

        text = (result.text or "").strip() or "(empty)"
        target = int(action["target_chat_id"])
        msg = await self._send_with_ratelimit(ctx, target, text[:_MAX_MESSAGE_LEN])
        delete_after = _to_positive_int(action.get("delete_after"))
        if delete_after > 0 and msg is not None:
            asyncio.create_task(self._delete_message_after(ctx, msg, delete_after))

    async def _delete_message_after(
        self, ctx: PluginContext, msg: Any, seconds: int
    ) -> None:
        """延时删除消息，异常时静默吞掉（不影响调度主循环）。"""
        try:
            await asyncio.sleep(seconds)
            await ctx.client.delete_messages(msg.peer_id, msg.id)
        except Exception:  # noqa: BLE001
            if ctx.log is not None:
                await ctx.log("warn", f"[scheduler] delete_message failed (msg_id={getattr(msg, 'id', '?')})")


    async def _get_provider_row(self, provider_id: int) -> LLMProvider | None:
        async with AsyncSessionLocal() as db:
            return await db.get(LLMProvider, provider_id)

    async def _get_provider_rows(self) -> list[LLMProvider]:
        async with AsyncSessionLocal() as db:
            return list((await db.execute(select(LLMProvider))).scalars().all())

    async def _persist_rule_config(self, rid: int, cfg: dict[str, Any]) -> None:
        async with AsyncSessionLocal() as db:
            row = await db.get(Rule, rid)
            if row is None:
                return
            row.config = cfg
            await db.commit()

    async def _send_with_ratelimit(
        self, ctx: PluginContext, peer: int | str, text: str
    ) -> Any | None:
        """发送消息（风控 + FloodWait 重试），成功返回 message 对象，被风控丢弃返回 None。"""
        peer_id = int(peer) if isinstance(peer, int) else None
        decision = await ctx.engine.acquire(
            ctx.account_id,
            "send_message_group",
            peer_id=peer_id,
        )
        if not decision.allowed:
            if ctx.log is not None:
                await ctx.log("info", f"[scheduler] ratelimited drop outcome={decision.outcome}")
            return None
        if decision.wait_seconds and decision.wait_seconds > 0:
            await asyncio.sleep(float(decision.wait_seconds))

        try:
            msg = await ctx.client.send_message(peer, text)
            return msg
        except Exception as exc:
            if not isinstance(exc, FloodWaitError) and not hasattr(exc, "seconds"):
                raise
            # FloodWaitError 参数匹配修复：
            # engine.on_flood_wait(action, exc) 只接受 2 个参数，
            # 不需要传 peer_id（该参数已在 engine 内部通过 action 区分）
            await ctx.engine.on_flood_wait("send_message_group", exc)
            await asyncio.sleep(min(int(getattr(exc, "seconds", 0) or 0), 60))
            try:
                msg = await ctx.client.send_message(peer, text)
                return msg
            except Exception as retry_exc:
                if not isinstance(retry_exc, FloodWaitError) and not hasattr(retry_exc, "seconds"):
                    raise
                if ctx.log is not None:
                    await ctx.log("warn", "[scheduler] send_message still flood-waited after retry; drop once")
                return None


PLUGIN_CLASS = SchedulerPlugin


def _dto_to_fake_row(dto: LLMProviderDTO) -> LLMProvider:
    """将 LLMProviderDTO 转为 ORM 行（向后兼容 build_client）。"""
    return LLMProvider(
        id=dto.id,
        name=dto.name,
        provider=dto.provider,
        api_key_enc=dto.api_key_enc,
        base_url=dto.base_url,
        default_model=dto.default_model,
        api_format=dto.api_format,
    )


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
