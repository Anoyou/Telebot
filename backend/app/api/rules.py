"""规则（Rule）REST API（PRD §9.3）。

统一为 ``[账号 × feature]`` 下的 Rule 提供 CRUD + dry-run + 复制到其它账号。
所有写操作完成后通过 IPC ``CMD_RELOAD_CONFIG`` 通知对应 worker 热加载。

注意：当前 dry-run 仅对 ``auto_reply`` 实现真正的命中判断；其它 feature 返回不命中。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from croniter import CroniterBadCronError, croniter
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..db.models.account import Account
from ..db.models.feature import (
    BUILTIN_FEATURES,
    FEATURE_AUTO_REPLY,
    FEATURE_FORWARD,
    FEATURE_SCHEDULER,
    Feature,
)
from ..db.models.rule import Rule
from ..deps import CurrentUser, DBSession
from ..redis_client import get_redis
from ..schemas.rule import (
    RuleCopyRequest,
    RuleCreate,
    RuleDryRunRequest,
    RuleDryRunResponse,
    RuleOut,
    RuleUpdate,
)
from ..services import audit
from ..worker.ipc import CMD_RELOAD_CONFIG, cmd_channel, make_cmd
from ..worker.plugins.builtin.auto_reply import _dry_run_match
from ..worker.plugins.builtin.forward.plugin import _dry_run_match as _forward_dry_run_match

log = logging.getLogger(__name__)
router = APIRouter(tags=["rules"])


# ─────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────
def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _ensure_account(db, aid: int) -> Account:
    acc = await db.get(Account, aid)
    if acc is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    return acc


async def _ensure_feature(db, key: str) -> None:
    """feature_key 必须在 feature 表里有登记（包括内置 5 + 第三方同步）。"""
    if key in BUILTIN_FEATURES:
        return
    if await db.get(Feature, key) is None:
        raise _bad("FEATURE_NOT_FOUND", f"未知 feature: {key}", 404)


async def _notify_reload(aid: int) -> None:
    """规则变化后通知对应 worker 热加载。redis 不可用静默。"""
    try:
        redis = get_redis()
        await redis.publish(cmd_channel(aid), make_cmd(CMD_RELOAD_CONFIG))
    except Exception:  # noqa: BLE001
        log.debug("通知 worker reload 失败 aid=%s", aid, exc_info=True)


def _to_out(r: Rule) -> RuleOut:
    return RuleOut.model_validate(r)


def _parse_scheduler_dt(raw: Any) -> datetime | None:
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


# ─────────────────────────────────────────────────────
# 列表 / 创建
# ─────────────────────────────────────────────────────
@router.get(
    "/api/accounts/{aid}/features/{key}/rules",
    response_model=list[RuleOut],
)
async def list_rules(
    aid: int, key: str, db: DBSession, _user: CurrentUser
) -> list[RuleOut]:
    """按 priority 倒序返回该 [账号 × feature] 下的所有 rule。"""
    await _ensure_account(db, aid)
    await _ensure_feature(db, key)
    rows = (
        await db.execute(
            select(Rule)
            .where(Rule.account_id == aid, Rule.feature_key == key)
            .order_by(Rule.priority.desc(), Rule.id.asc())
        )
    ).scalars().all()
    return [_to_out(r) for r in rows]


@router.post(
    "/api/accounts/{aid}/features/{key}/rules",
    response_model=RuleOut,
    status_code=201,
)
async def create_rule(
    aid: int,
    key: str,
    payload: RuleCreate,
    db: DBSession,
    user: CurrentUser,
) -> RuleOut:
    """新建一条 rule。"""
    await _ensure_account(db, aid)
    await _ensure_feature(db, key)
    rule = Rule(
        account_id=aid,
        feature_key=key,
        name=payload.name,
        enabled=payload.enabled,
        priority=payload.priority,
        config=dict(payload.config or {}),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    await audit.write(
        db,
        user.id,
        "rule.create",
        target=f"account:{aid}/feature:{key}/rule:{rule.id}",
        detail={"name": payload.name, "priority": payload.priority},
    )
    await db.commit()
    await _notify_reload(aid)
    return _to_out(rule)


# ─────────────────────────────────────────────────────
# 单条 GET / PATCH / DELETE
# ─────────────────────────────────────────────────────
async def _load_rule(db, aid: int, key: str, rid: int) -> Rule:
    rule = await db.get(Rule, rid)
    if rule is None or rule.account_id != aid or rule.feature_key != key:
        raise _bad("RULE_NOT_FOUND", "规则不存在", 404)
    return rule


@router.get(
    "/api/accounts/{aid}/features/{key}/rules/{rid}",
    response_model=RuleOut,
)
async def get_rule(
    aid: int, key: str, rid: int, db: DBSession, _user: CurrentUser
) -> RuleOut:
    await _ensure_account(db, aid)
    await _ensure_feature(db, key)
    rule = await _load_rule(db, aid, key, rid)
    return _to_out(rule)


@router.patch(
    "/api/accounts/{aid}/features/{key}/rules/{rid}",
    response_model=RuleOut,
)
async def patch_rule(
    aid: int,
    key: str,
    rid: int,
    payload: RuleUpdate,
    db: DBSession,
    user: CurrentUser,
) -> RuleOut:
    """更新单条 rule 的部分字段（exclude_unset）。"""
    await _ensure_account(db, aid)
    await _ensure_feature(db, key)
    rule = await _load_rule(db, aid, key, rid)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(rule, k, dict(v) if k == "config" and v is not None else v)
    await db.commit()
    await db.refresh(rule)
    await audit.write(
        db,
        user.id,
        "rule.update",
        target=f"account:{aid}/feature:{key}/rule:{rid}",
        detail=data,
    )
    await db.commit()
    await _notify_reload(aid)
    return _to_out(rule)


@router.delete(
    "/api/accounts/{aid}/features/{key}/rules/{rid}",
    status_code=204,
)
async def delete_rule(
    aid: int, key: str, rid: int, db: DBSession, user: CurrentUser
) -> None:
    await _ensure_account(db, aid)
    await _ensure_feature(db, key)
    rule = await _load_rule(db, aid, key, rid)
    await db.delete(rule)
    await db.commit()
    await audit.write(
        db,
        user.id,
        "rule.delete",
        target=f"account:{aid}/feature:{key}/rule:{rid}",
    )
    await db.commit()
    await _notify_reload(aid)


# ─────────────────────────────────────────────────────
# Dry-run
# ─────────────────────────────────────────────────────
@router.post(
    "/api/accounts/{aid}/features/{key}/rules/{rid}/dry-run",
    response_model=RuleDryRunResponse,
)
async def dry_run_rule(
    aid: int,
    key: str,
    rid: int,
    payload: RuleDryRunRequest,
    db: DBSession,
    _user: CurrentUser,
) -> RuleDryRunResponse:
    """试运行：把 sample 消息喂给规则，返回是否命中 + 渲染输出。

    - ``auto_reply``：完整匹配 + 渲染
    - ``forward``：按 ``source_kind`` 判断是否进入转发流水线，输出 "would forward to ..." 描述
    - 其它 feature：当前返回 matched=False（未实现）
    """
    await _ensure_account(db, aid)
    await _ensure_feature(db, key)
    rule = await _load_rule(db, aid, key, rid)
    if key == FEATURE_AUTO_REPLY:
        chat_type = payload.sample_chat_type or "private"
        matched, output = _dry_run_match(
            rule.config or {},
            payload.sample_message,
            chat_type,
            payload.sample_chat_id,
        )
        return RuleDryRunResponse(
            matched=matched,
            output=output,
            detail={"feature": key, "rule_id": rid},
        )
    if key == FEATURE_FORWARD:
        # forward 的 dry-run 只关心"源是否命中"，不真正下发任何消息
        matched, output = _forward_dry_run_match(
            rule.config or {},
            payload.sample_message,
            payload.sample_chat_id,
        )
        return RuleDryRunResponse(
            matched=matched,
            output=output,
            detail={"feature": key, "rule_id": rid},
        )
    if key == FEATURE_SCHEDULER:
        cfg = rule.config or {}
        kind = str(cfg.get("kind") or "cron").lower()
        action = cfg.get("action") if isinstance(cfg.get("action"), dict) else {}
        now = datetime.now(UTC)
        next_fire: datetime | None = None

        try:
            if kind == "once":
                next_fire = _parse_scheduler_dt(cfg.get("fire_at"))
            elif kind == "interval":
                interval_sec = int(cfg.get("interval_sec") or 0)
                last_fire = _parse_scheduler_dt(cfg.get("last_fire"))
                if interval_sec > 0:
                    next_fire = (last_fire if last_fire is not None else now).astimezone(UTC)
                    if last_fire is not None:
                        next_fire = next_fire + timedelta(seconds=interval_sec)
            else:
                expr = str(cfg.get("cron") or "").strip()
                if expr:
                    next_fire = croniter(expr, now).get_next(datetime)
        except (ValueError, CroniterBadCronError) as exc:
            return RuleDryRunResponse(
                matched=False,
                output=None,
                detail={"feature": key, "rule_id": rid, "error": f"{type(exc).__name__}: {exc}"},
            )

        due = bool(next_fire and next_fire <= now)
        action_type = str(action.get("type") or "send_message")
        target = action.get("target_chat_id")
        output = (
            f"would fire {action_type} to {target}"
            if due
            else f"next fire at {next_fire.isoformat() if next_fire else 'N/A'}"
        )
        return RuleDryRunResponse(
            matched=due,
            output=output,
            detail={
                "feature": key,
                "rule_id": rid,
                "kind": kind,
                "next_fire": next_fire.isoformat() if next_fire else None,
                "due": due,
            },
        )
    return RuleDryRunResponse(
        matched=False,
        output=None,
        detail={"feature": key, "note": "dry-run for this feature is not implemented yet"},
    )


# ─────────────────────────────────────────────────────
# 复制规则到其它账号
# ─────────────────────────────────────────────────────
@router.post(
    "/api/accounts/{aid}/features/{key}/rules/copy",
    response_model=dict,
)
async def copy_rules(
    aid: int,
    key: str,
    payload: RuleCopyRequest,
    db: DBSession,
    user: CurrentUser,
) -> dict[str, Any]:
    """把 ``rule_ids`` 指定的 rule（必须属于 source aid×key）复制到 ``target_account_ids``。

    每条 rule 在每个目标账号下都会插入新行（自增 id），feature_key 保持一致。
    """
    await _ensure_account(db, aid)
    await _ensure_feature(db, key)
    if not payload.rule_ids or not payload.target_account_ids:
        return {"copied": 0}
    if aid in payload.target_account_ids:
        # 防呆：避免误把自己复制成第二份
        targets = [t for t in payload.target_account_ids if t != aid]
    else:
        targets = list(payload.target_account_ids)
    if not targets:
        return {"copied": 0}

    src_rules = (
        await db.execute(
            select(Rule).where(
                Rule.account_id == aid,
                Rule.feature_key == key,
                Rule.id.in_(list(payload.rule_ids)),
            )
        )
    ).scalars().all()
    if not src_rules:
        return {"copied": 0}

    # 校验目标账号都存在
    for tgt in targets:
        if await db.get(Account, tgt) is None:
            raise _bad("ACCOUNT_NOT_FOUND", f"目标账号不存在: {tgt}", 404)

    copied = 0
    for tgt in targets:
        for r in src_rules:
            db.add(
                Rule(
                    account_id=tgt,
                    feature_key=key,
                    name=r.name,
                    enabled=r.enabled,
                    priority=r.priority,
                    config=dict(r.config or {}),
                )
            )
            copied += 1
    await db.commit()

    await audit.write(
        db,
        user.id,
        "rule.copy",
        target=f"account:{aid}/feature:{key}",
        detail={"rule_ids": list(payload.rule_ids), "targets": targets, "copied": copied},
    )
    await db.commit()
    # 每个目标 worker 都通知一遍
    for tgt in targets:
        await _notify_reload(tgt)
    return {"copied": copied, "targets": targets}


__all__ = ["router"]
