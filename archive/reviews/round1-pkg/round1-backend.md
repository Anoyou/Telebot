# Code Review — Backend (FastAPI + Telethon Worker System)

## Project
Telegram Userbot Management System. Python 3.12, FastAPI, SQLAlchemy(async), Telethon, PostgreSQL, Redis.
Multi-account TG userbot with plugin system, rate limiting, humanize features, Fernet encryption.

## Review Focus
1. **Security** (top priority): Fernet key mgmt, session storage, plugin sandbox escape, JWT/TOTP auth, input validation
2. **Architecture**: API/Service/Worker layering, plugin extensibility vs isolation
3. **Reliability**: worker crash recovery, async correctness, DB session lifecycle
4. **Code quality**: error handling patterns, type safety, duplication

## Output Format
For each finding: **Severity** (Critical/Major/Minor/Suggestion) + **File:Line** + **Description** + **Fix**

---

```python
===== backend/app/__init__.py =====
"""telebot 后端应用包。

``__version__`` 是后端的单点版本号，main.py 的 ``FastAPI(version=...)`` 和
worker 的 ``,version`` 命令均读这里。每次 release 同时改前端 ``frontend/src/lib/version.ts``、
``frontend/package.json``、``backend/pyproject.toml``、``CHANGELOG.md``，详见
``agent-plans/README.md`` §6。
"""
__version__ = "0.2.0"

===== backend/app/api/__init__.py =====
"""api 子包：把已实现的 router 模块导出，便于 main.py 一处 import。"""

from . import accounts, auth, features, logs, plugins, rate_limit, rules  # noqa: F401

__all__ = [
    "accounts",
    "auth",
    "features",
    "logs",
    "plugins",
    "rate_limit",
    "rules",
]

===== backend/app/api/accounts.py =====
"""账号 API：CRUD + 暂停 / 恢复 + 复制配置 + Telethon 登录绑定向导。

绑定向导按 plan 设计：``/login/start``、``/login/code``、``/login/2fa`` 都不带 aid，
因为新建账号在 finalize 之前还没有 aid；老账号重登可以在 ``start`` 入参里带 ``account_id``。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..deps import CurrentUser, DBSession
from ..schemas.account import (
    AccountCloneConfigRequest,
    AccountConfirm2FARequest,
    AccountConfirmCodeRequest,
    AccountConfirmResponse,
    AccountDetail,
    AccountStartLoginRequest,
    AccountStartLoginResponse,
    AccountSummary,
    AccountUpdateRequest,
)
from ..services import account_service, audit, login_service

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


# ── 列表 / 详情 / 修改 / 删除 ─────────────────────────────────────
@router.get("", response_model=list[AccountSummary])
async def list_accounts(db: DBSession, user: CurrentUser) -> list[AccountSummary]:
    """列出全部账号。"""
    return await account_service.list_accounts(db)


@router.get("/{aid}", response_model=AccountDetail)
async def get_account(aid: int, db: DBSession, user: CurrentUser) -> AccountDetail:
    """读取账号详情。"""
    return await account_service.get_account(db, aid)


@router.patch("/{aid}", response_model=AccountDetail)
async def update_account(
    aid: int,
    payload: AccountUpdateRequest,
    db: DBSession,
    user: CurrentUser,
) -> AccountDetail:
    """修改账号字段。"""
    detail = await account_service.update_account(db, aid, payload)
    await audit.write(
        db,
        user.id,
        "account.update",
        target=f"account:{aid}",
        detail=payload.model_dump(exclude_unset=True),
    )
    await db.commit()
    return detail


@router.delete("/{aid}")
async def delete_account(aid: int, db: DBSession, user: CurrentUser) -> dict[str, bool]:
    """删除账号（撤销 session + 清理本地数据）。"""
    await account_service.delete_account(db, aid)
    await audit.write(db, user.id, "account.delete", target=f"account:{aid}")
    await db.commit()
    return {"ok": True}


# ── 头像 ───────────────────────────────────────────────────────────
@router.get("/{aid}/avatar")
async def get_avatar(aid: int, db: DBSession, user: CurrentUser):
    """返回账号头像 PNG/JPEG，本地缓存 24h。

    - 文件不存在（worker 离线 / 账号无头像 / 首次访问）→ 404，前端走首字母 fallback。
    - 文件存在 → 用浏览器私有缓存 1h；超过 24h 后台会触发 worker 重拉。
    """
    path = await account_service.ensure_avatar(db, aid)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail={"code": "no_avatar", "message": "暂无头像"})
    return FileResponse(
        str(path),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


# ── 绑定向导 ──────────────────────────────────────────────────────
@router.post("/login/start", response_model=AccountStartLoginResponse)
async def login_start(
    req: AccountStartLoginRequest,
    db: DBSession,
    user: CurrentUser,
) -> AccountStartLoginResponse:
    """绑定向导第 1 步：建立 Telethon client，发送验证码，返回 login_token。"""
    token = await login_service.start_login(
        db,
        api_id=req.api_id,
        api_hash=req.api_hash,
        phone=req.phone,
        proxy_id=req.proxy_id,
        device_profile_id=req.device_profile_id,
    )
    await audit.write(
        db,
        user.id,
        "account.login.start",
        target=f"phone:{req.phone}",
        detail={
            "proxy_id": req.proxy_id,
            "device_profile_id": req.device_profile_id,
        },
    )
    await db.commit()
    # phone_code_hash 不必返给前端（state 已在主进程内存里）
    return AccountStartLoginResponse(login_token=token, phone_code_hash=None)


@router.post("/login/code", response_model=AccountConfirmResponse)
async def login_code(
    req: AccountConfirmCodeRequest,
    db: DBSession,
    user: CurrentUser,
) -> AccountConfirmResponse:
    """绑定向导第 2 步：提交短信/Telegram 验证码。

    若账号未启用 2FA，本步同时完成 finalize；否则等待第 3 步。
    """
    require_2fa, pending = await login_service.confirm_code(req.login_token, req.code)
    if require_2fa:
        # 卡在两步验证；account_id 此时还没产生
        return AccountConfirmResponse(account_id=0, require_2fa=True, display_name=None)

    aid = await login_service.finalize(db, req.login_token, pending)
    await audit.write(db, user.id, "account.login.finalize", target=f"account:{aid}")
    await db.commit()

    detail = await account_service.get_account(db, aid)
    return AccountConfirmResponse(
        account_id=aid, require_2fa=False, display_name=detail.display_name
    )


@router.post("/login/2fa", response_model=AccountConfirmResponse)
async def login_2fa(
    req: AccountConfirm2FARequest,
    db: DBSession,
    user: CurrentUser,
) -> AccountConfirmResponse:
    """绑定向导第 3 步：提交两步验证密码，并完成 finalize。"""
    pending = await login_service.confirm_2fa(req.login_token, req.password)
    aid = await login_service.finalize(db, req.login_token, pending)
    await audit.write(db, user.id, "account.login.finalize2fa", target=f"account:{aid}")
    await db.commit()

    detail = await account_service.get_account(db, aid)
    return AccountConfirmResponse(
        account_id=aid, require_2fa=False, display_name=detail.display_name
    )


# ── 暂停 / 恢复 ───────────────────────────────────────────────────
@router.post("/{aid}/pause")
async def pause_account(aid: int, db: DBSession, user: CurrentUser) -> dict[str, bool]:
    """暂停账号。"""
    await account_service.pause(db, aid)
    await audit.write(db, user.id, "account.pause", target=f"account:{aid}")
    await db.commit()
    return {"ok": True}


@router.post("/{aid}/resume")
async def resume_account(aid: int, db: DBSession, user: CurrentUser) -> dict[str, bool]:
    """恢复账号。"""
    await account_service.resume(db, aid)
    await audit.write(db, user.id, "account.resume", target=f"account:{aid}")
    await db.commit()
    return {"ok": True}


# ── 复制配置 ──────────────────────────────────────────────────────
@router.post("/{aid}/clone-config")
async def clone_config(
    aid: int,
    req: AccountCloneConfigRequest,
    db: DBSession,
    user: CurrentUser,
) -> dict[str, int | bool]:
    """从 ``req.from_account_id`` 复制 features+rules 到 aid。"""
    stats = await account_service.clone_config(
        db, src_aid=req.from_account_id, dst_aid=aid, features=req.features or None
    )
    await audit.write(
        db,
        user.id,
        "account.clone_config",
        target=f"account:{aid}",
        detail={"from": req.from_account_id, "features": req.features, **stats},
    )
    await db.commit()
    return {"ok": True, **stats}

===== backend/app/api/auth.py =====
"""认证 API：注册（首次部署）/ 登录 / 注销 / 当前用户 / TOTP 启用与校验。"""

from __future__ import annotations

import logging
import time
import unicodedata

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select

from ..crypto import decrypt_str, encrypt_str
from ..db.models.user import WebUser
from ..deps import CurrentUser, DBSession
from ..schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    TotpDisableRequest,
    TotpEnableResponse,
    TotpVerifyRequest,
)
from ..schemas.auth import (
    CurrentUser as CurrentUserSchema,
)
from ..services import audit, auth_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# Cookie key 与 deps.get_current_user 中读取的 alias 必须保持一致
_COOKIE_NAME = "auth_token"


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _set_auth_cookie(resp: Response, token: str, max_age: int) -> None:
    """统一的 cookie 设置：HttpOnly + SameSite=Lax + (可选)Secure。

    生产 HTTPS 部署在 .env 里设 ``COOKIE_SECURE=true``，会在响应里直接打 Secure
    标记；本地 HTTP 调试默认 false。
    """
    from ..settings import settings as _s

    resp.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=_s.cookie_secure,
    )


# ── 登录限速 ──────────────────────────────────────────────────────
# 用 Redis INCR + EXPIRE 实现一个 per-minute 滑动窗口（粗粒度，简单可靠）。
# 双维度计数：IP 和 username 各算一份，任一超限即拒。

# 进程内 fallback：Redis 故障时仍能扛住基础暴力破解
# 结构：key -> (count, window_start_ts)；窗口固定 60s
_LOCAL_RL_BUCKETS: dict[str, tuple[int, float]] = {}


def _client_ip(request: Request) -> str:
    """提取客户端 IP。

    仅当 ``settings.trust_forwarded_for=true``（部署在可信反代后）时，才信任
    ``X-Forwarded-For``；否则一律取 TCP 直连地址，避免攻击者塞任意 header
    伪造新 IP 绕过限速。
    """
    from ..settings import settings as _s

    if _s.trust_forwarded_for:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            # X-Forwarded-For: client, proxy1, proxy2 → 取最左
            ip = fwd.split(",", 1)[0].strip()
            if ip:
                return ip
    return request.client.host if request.client else "?"


def _normalize_rl_username(raw: str) -> str:
    """限速 key 用的 username 归一化。

    - NFKC：全角 / 兼容字符归到标准形式（避免 ＡＤＭＩＮ vs ADMIN 算两个 key）
    - strip + lower：避免大小写 / 前后空格差异
    - 字节截断 64：防恶意超长 username 撑爆 Redis key 空间
    """
    s = unicodedata.normalize("NFKC", raw or "").strip().lower()
    return s.encode("utf-8", "ignore")[:64].decode("utf-8", "ignore")


def _local_rl_check(key: str, limit: int) -> bool:
    """进程内固定窗口计数器；返回 True=放行，False=超限。

    用于 Redis 故障时兜底（fail-degraded 而非 fail-open）。每进程独立，多实例
    部署下保护力会下降，但仍比完全无限速强；Redis 恢复后自动切回。
    """
    now = time.time()
    cnt, start = _LOCAL_RL_BUCKETS.get(key, (0, now))
    if now - start >= 60.0:
        cnt, start = 0, now
    cnt += 1
    _LOCAL_RL_BUCKETS[key] = (cnt, start)
    # 顺手清理太旧的桶，防内存泄漏（最多保留 1000 个活跃 key）
    if len(_LOCAL_RL_BUCKETS) > 1000:
        cutoff = now - 120.0
        for k in list(_LOCAL_RL_BUCKETS.keys()):
            if _LOCAL_RL_BUCKETS[k][1] < cutoff:
                _LOCAL_RL_BUCKETS.pop(k, None)
    return cnt <= limit


async def _enforce_login_rate_limit(request: Request, username: str) -> None:
    from ..redis_client import get_redis
    from ..settings import settings as _s

    limit = int(_s.login_rate_limit_per_min or 0)
    if limit <= 0:
        return

    ip = _client_ip(request)
    user_norm = _normalize_rl_username(username)
    keys = [
        f"auth_rl:ip:{ip}",
        f"auth_rl:user:{user_norm}",
    ]

    redis = get_redis()
    for k in keys:
        try:
            count = await redis.incr(k)
            if count == 1:
                await redis.expire(k, 60)
            if count > limit:
                raise _err(
                    "RATE_LIMITED",
                    "登录尝试过于频繁，请稍后再试",
                    429,
                )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            # Redis 故障：降级到进程内计数器，至少不让暴力破解完全无人看管
            log.warning("login rate-limit Redis fail-degraded: %s; using local bucket", e)
            if not _local_rl_check(k, limit):
                raise _err(
                    "RATE_LIMITED",
                    "登录尝试过于频繁，请稍后再试",
                    429,
                ) from None


# ── 注册（仅首次） ────────────────────────────────────────────────
@router.post("/register", response_model=LoginResponse)
async def register(
    req: RegisterRequest, request: Request, response: Response, db: DBSession
) -> LoginResponse:
    """系统首次部署时创建超管账号；只要 ``web_user`` 表非空，本接口禁用。"""
    # 注册接口同样限速，防止暴力枚举 / DoS
    await _enforce_login_rate_limit(request, req.username.strip())

    cnt = (await db.execute(select(func.count(WebUser.id)))).scalar_one()
    if cnt and int(cnt) > 0:
        raise _err("REGISTER_DISABLED", "系统已存在用户，注册接口已禁用", 403)

    user = WebUser(
        username=req.username.strip(),
        password_hash=auth_service.hash_password(req.password),
    )
    db.add(user)
    await db.flush()
    await audit.write(db, user.id, "auth.register", target=f"user:{user.id}")
    await db.commit()

    # 直接颁发 cookie，节省一次 login 请求
    from ..settings import settings as _s  # 局部 import 减小模块顶部依赖

    token = auth_service.issue_jwt_token(user.id)
    _set_auth_cookie(response, token, _s.jwt_expire_seconds)
    return LoginResponse(ok=True, require_totp=False)


# ── 登录 ──────────────────────────────────────────────────────────
@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request, response: Response, db: DBSession) -> LoginResponse:
    """用户名密码（+ 可选 TOTP）登录，成功后写 HttpOnly cookie。

    特殊情况：``web_user`` 表为空（首次部署还没注册）时返回 ``NO_USER``，
    前端据此切到注册页，避免一直提示"用户名或密码错误"让人摸不着头脑。
    """
    # 登录限速（IP + 用户名 双维度，任一超限即拒）
    await _enforce_login_rate_limit(request, req.username.strip())

    # 系统尚未创建任何用户：直接返回 NO_USER，前端会引导到注册流程
    user_count = (await db.execute(select(func.count(WebUser.id)))).scalar_one()
    if not user_count or int(user_count) == 0:
        raise _err("NO_USER", "系统尚未创建管理员账号，请先注册", 401)

    user = (
        await db.execute(select(WebUser).where(WebUser.username == req.username.strip()))
    ).scalar_one_or_none()
    # 用户不存在或密码错都返回相同错误，避免账号枚举
    if not user or not auth_service.verify_password(req.password, user.password_hash):
        raise _err("AUTH_INVALID", "用户名或密码错误", 401)

    # 已启用 TOTP 必须校验
    if user.totp_secret_enc:
        if not req.totp_code:
            return LoginResponse(ok=False, require_totp=True)
        secret = decrypt_str(user.totp_secret_enc)
        if not auth_service.verify_totp(secret, req.totp_code):
            raise _err("TOTP_INVALID", "动态验证码错误", 401)

    await audit.write(db, user.id, "auth.login", target=f"user:{user.id}")
    await db.commit()

    from ..settings import settings as _s

    token = auth_service.issue_jwt_token(user.id)
    _set_auth_cookie(response, token, _s.jwt_expire_seconds)
    return LoginResponse(ok=True, require_totp=False)


# ── 注销 ──────────────────────────────────────────────────────────
@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    """清除 auth_token cookie。"""
    from ..settings import settings as _s

    # delete_cookie 必须把 path/samesite/secure 等属性匹配上，浏览器才会真正清理
    response.delete_cookie(
        _COOKIE_NAME,
        samesite="lax",
        secure=_s.cookie_secure,
        httponly=True,
    )
    return {"ok": True}


# ── 当前用户 ──────────────────────────────────────────────────────
@router.get("/me", response_model=CurrentUserSchema)
async def me(user: CurrentUser) -> CurrentUserSchema:
    """返回当前登录用户信息。"""
    return CurrentUserSchema(
        id=user.id,
        username=user.username,
        has_totp=bool(user.totp_secret_enc),
    )


# ── TOTP 启用：先生成 secret，存到内存 / 临时 cookie，等 verify 通过再落库 ──
# 这里取的折中方案：把待验证 secret 也写到 HttpOnly cookie，cookie 名 `pending_totp`。
# 这样无需引入额外存储；secret 仅在浏览器与本服务之间传输；不暴露给页面 JS。
_PENDING_TOTP_COOKIE = "pending_totp"


@router.post("/totp/enable", response_model=TotpEnableResponse)
async def totp_enable(
    user: CurrentUser, response: Response, request: Request
) -> TotpEnableResponse:
    """生成新的 TOTP secret 与 otpauth url；尚未校验前不会落库到 user 表。"""
    secret = auth_service.generate_totp_secret()
    otpauth_url = auth_service.make_otpauth_url(user.username, secret)
    # 写入临时 cookie，5 分钟有效；真正落库由 totp_verify 完成
    from ..settings import settings as _s

    response.set_cookie(
        key=_PENDING_TOTP_COOKIE,
        value=secret,
        max_age=300,
        httponly=True,
        samesite="lax",
        secure=_s.cookie_secure,
    )
    return TotpEnableResponse(secret=secret, otpauth_url=otpauth_url)


@router.post("/totp/verify")
async def totp_verify(
    req: TotpVerifyRequest,
    user: CurrentUser,
    db: DBSession,
    request: Request,
    response: Response,
) -> dict[str, bool]:
    """用待启用的 secret 校验一次 6 位码，通过则把加密后的 secret 写入 user。"""
    pending_secret = request.cookies.get(_PENDING_TOTP_COOKIE)
    if not pending_secret:
        raise _err("TOTP_PENDING_MISSING", "请先调用 /totp/enable 生成密钥")
    if not auth_service.verify_totp(pending_secret, req.code):
        raise _err("TOTP_INVALID", "动态验证码错误", 401)

    user.totp_secret_enc = encrypt_str(pending_secret)
    await audit.write(db, user.id, "auth.totp.enable", target=f"user:{user.id}")
    await db.commit()

    response.delete_cookie(_PENDING_TOTP_COOKIE)
    return {"ok": True}


# ── 修改密码 ──────────────────────────────────────────────────────
# 必须验旧密码（防 cookie 被偷后默默改密）；改后强制清 cookie 让用户重登。
@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user: CurrentUser,
    db: DBSession,
    response: Response,
) -> dict[str, bool]:
    """修改当前用户密码。

    - 旧密码错误：返回 ``AUTH_INVALID``
    - 旧新相同：返回 ``PWD_SAME``
    - 成功后清 ``auth_token`` cookie，让用户用新密码重新登录
    """
    if not auth_service.verify_password(req.old_password, user.password_hash):
        raise _err("AUTH_INVALID", "旧密码错误", 401)
    if req.old_password == req.new_password:
        raise _err("PWD_SAME", "新密码不能与旧密码相同")

    user.password_hash = auth_service.hash_password(req.new_password)
    await audit.write(db, user.id, "auth.change_password", target=f"user:{user.id}")
    await db.commit()

    # 强制下线：清当前 session 的 cookie，前端拦到 401 会跳登录页
    from ..settings import settings as _s

    response.delete_cookie(
        _COOKIE_NAME,
        samesite="lax",
        secure=_s.cookie_secure,
        httponly=True,
    )
    return {"ok": True}


# ── 禁用 TOTP ────────────────────────────────────────────────────
# 要求当前 TOTP 码做最后一次校验，避免 cookie 被劫持后被悄悄关闭 2FA
@router.post("/totp/disable")
async def totp_disable(
    req: TotpDisableRequest,
    user: CurrentUser,
    db: DBSession,
) -> dict[str, bool]:
    """禁用当前用户的 TOTP（必须提供有效的当前 TOTP 码）。"""
    if not user.totp_secret_enc:
        raise _err("TOTP_NOT_ENABLED", "尚未启用 TOTP")
    secret = decrypt_str(user.totp_secret_enc)
    if not auth_service.verify_totp(secret, req.code):
        raise _err("TOTP_INVALID", "动态验证码错误", 401)

    user.totp_secret_enc = None
    await audit.write(db, user.id, "auth.totp.disable", target=f"user:{user.id}")
    await db.commit()
    return {"ok": True}

===== backend/app/api/commands.py =====
"""自定义命令 + LLM Provider REST API（Sprint2 #2）。

路由前缀：
- ``/api/commands/templates``       全局模板 CRUD
- ``/api/commands/llm-providers``   LLM provider CRUD + fetch-models + test-model
- ``/api/accounts/{aid}/commands``  账号 × 模板 启用关系

安全红线：
- LLM provider 任何 GET 接口都不返回明文 ``api_key``，只返 ``has_api_key:bool``
- 模板内容不含敏感信息，可正常 audit；audit log 里会写命令名和类型，不写完整 config
"""

from __future__ import annotations

import time as _time

import httpx
from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser, DBSession
from ..schemas.command import (
    AccountCommandItem,
    CommandTemplateCreate,
    CommandTemplateOut,
    CommandTemplateUpdate,
    FetchModelsResponse,
    LLMProviderCreate,
    LLMProviderOut,
    LLMProviderUpdate,
    TestModelRequest,
    TestModelResponse,
)
from ..services import audit, command_service

router = APIRouter(tags=["commands"])


# ════════════════════════════════════════════════════════════
# 命令模板 CRUD
# ════════════════════════════════════════════════════════════


@router.get("/api/commands/templates", response_model=list[CommandTemplateOut])
async def list_templates(db: DBSession, _user: CurrentUser) -> list[CommandTemplateOut]:
    """列出全部命令模板。"""
    rows = await command_service.list_templates(db)
    return [CommandTemplateOut.model_validate(r) for r in rows]


@router.post("/api/commands/templates", response_model=CommandTemplateOut)
async def create_template(
    payload: CommandTemplateCreate,
    db: DBSession,
    user: CurrentUser,
) -> CommandTemplateOut:
    """新建命令模板。"""
    tpl = await command_service.create_template(db, payload)
    await audit.write(
        db,
        user.id,
        "command_template.create",
        target=f"command_template:{tpl.id}",
        # 不记录完整 config（可能含 system_prompt 较长）
        detail={"name": tpl.name, "type": tpl.type},
    )
    await db.commit()
    return CommandTemplateOut.model_validate(tpl)


@router.patch(
    "/api/commands/templates/{tpl_id}", response_model=CommandTemplateOut
)
async def update_template(
    tpl_id: int,
    payload: CommandTemplateUpdate,
    db: DBSession,
    user: CurrentUser,
) -> CommandTemplateOut:
    """更新命令模板；任何字段变化都会通知所有启用了它的 worker reload。"""
    tpl = await command_service.update_template(db, tpl_id, payload)
    await audit.write(
        db,
        user.id,
        "command_template.update",
        target=f"command_template:{tpl.id}",
        detail=payload.model_dump(exclude_unset=True, exclude={"config"}),
    )
    await db.commit()
    # 通知所有启用此模板的 worker reload
    aids = await _aids_using_template(db, tpl.id)
    await command_service.notify_reload(aids)
    return CommandTemplateOut.model_validate(tpl)


@router.delete("/api/commands/templates/{tpl_id}")
async def delete_template(
    tpl_id: int, db: DBSession, user: CurrentUser
) -> dict[str, bool]:
    """删除命令模板；级联删 link。"""
    aids = await command_service.delete_template(db, tpl_id)
    await audit.write(
        db,
        user.id,
        "command_template.delete",
        target=f"command_template:{tpl_id}",
    )
    await db.commit()
    await command_service.notify_reload(aids)
    return {"ok": True}


async def _aids_using_template(db, tpl_id: int) -> list[int]:
    """收集启用了某模板的 account_id 列表（用于 reload 通知）。"""
    from sqlalchemy import select

    from ..db.models.command import AccountCommandLink

    rows = (
        await db.execute(
            select(AccountCommandLink.account_id).where(
                AccountCommandLink.template_id == tpl_id,
                AccountCommandLink.enabled.is_(True),
            )
        )
    ).scalars().all()
    return list(rows)


# ════════════════════════════════════════════════════════════
# LLM Provider CRUD
# ════════════════════════════════════════════════════════════


@router.get(
    "/api/commands/llm-providers", response_model=list[LLMProviderOut]
)
async def list_providers(db: DBSession, _user: CurrentUser) -> list[LLMProviderOut]:
    """列出全部 LLM provider；不含明文 key。"""
    return await command_service.list_providers(db)


@router.post(
    "/api/commands/llm-providers", response_model=LLMProviderOut
)
async def create_provider(
    payload: LLMProviderCreate, db: DBSession, user: CurrentUser
) -> LLMProviderOut:
    """新建 LLM provider；api_key 加密落库。

    通知 worker reload：理论上新建的 provider 还没有模板引用它，但用户场景里
    经常先 create 再立刻去模板 PATCH 一次去关联，那时就要 worker 已知道这条
    新 provider；统一让所有"启用了 ai 模板"的账号 reload 一次最简单——
    worker 重新拉一次 DB，新 provider 进 ctx.providers，下次模板 PATCH 触发的
    第二次 reload 也无害（重新拉同样数据）。
    """
    out = await command_service.create_provider(db, payload)
    await audit.write(
        db,
        user.id,
        "llm_provider.create",
        target=f"llm_provider:{out.id}",
        # 仅记录元信息，不记录 api_key 是否提供（元信息有限）
        detail={"name": out.name, "provider": out.provider, "default_model": out.default_model},
    )
    await db.commit()
    aids = await command_service.list_aids_with_ai_commands(db)
    await command_service.notify_reload(aids)
    return out


@router.patch(
    "/api/commands/llm-providers/{pid}", response_model=LLMProviderOut
)
async def update_provider(
    pid: int,
    payload: LLMProviderUpdate,
    db: DBSession,
    user: CurrentUser,
) -> LLMProviderOut:
    """更新 LLM provider。

    api_key 行为约定：``""`` 清空、非空替换、None / 缺省不动。
    audit detail 中**绝不写** api_key 字段。

    通知 worker reload：所有启用了 type=ai 模板的账号都会被通知，
    避免 api_key / base_url / tags 改动后"TG 里没生效"。
    """
    out = await command_service.update_provider(db, pid, payload)
    audit_detail = payload.model_dump(
        exclude_unset=True, exclude={"api_key"}
    )
    if "api_key" in payload.model_dump(exclude_unset=True):
        audit_detail["api_key_changed"] = True
    await audit.write(
        db,
        user.id,
        "llm_provider.update",
        target=f"llm_provider:{out.id}",
        detail=audit_detail,
    )
    await db.commit()
    # 通知所有启用了 ai 类型模板的账号热加载
    aids = await command_service.list_aids_with_ai_commands(db)
    await command_service.notify_reload(aids)
    return out


@router.delete("/api/commands/llm-providers/{pid}")
async def delete_provider(
    pid: int, db: DBSession, user: CurrentUser
) -> dict[str, bool]:
    """删除 LLM provider；引用此 provider 的 ai 命令调用之后会失败。

    同样要通知 worker reload，让 ctx.providers 把这条删掉——否则被引用的
    模板下一次还会用 worker 内存里的旧条目跑（还能跑通），等用户疑惑为什么
    "我都删了它还在用"。
    """
    aids = await command_service.list_aids_with_ai_commands(db)
    await command_service.delete_provider(db, pid)
    await audit.write(
        db,
        user.id,
        "llm_provider.delete",
        target=f"llm_provider:{pid}",
    )
    await db.commit()
    await command_service.notify_reload(aids)
    return {"ok": True}


# ════════════════════════════════════════════════════════════
# 账号 × 模板 启用关系
# ════════════════════════════════════════════════════════════


@router.get(
    "/api/accounts/{aid}/commands", response_model=list[AccountCommandItem]
)
async def list_account_commands(
    aid: int, db: DBSession, _user: CurrentUser
) -> list[AccountCommandItem]:
    """列出该账号已启用 + 可用全部命令模板。"""
    return await command_service.list_for_account(db, aid)


@router.post(
    "/api/accounts/{aid}/commands/{tpl_id}",
    response_model=dict,
)
async def enable_account_command(
    aid: int, tpl_id: int, db: DBSession, user: CurrentUser
) -> dict[str, bool]:
    """启用某账号的某模板。"""
    await command_service.enable_for_account(db, aid, tpl_id)
    await audit.write(
        db,
        user.id,
        "account_command.enable",
        target=f"account:{aid}/command_template:{tpl_id}",
    )
    await db.commit()
    await command_service.notify_reload(aid)
    return {"ok": True}


@router.delete(
    "/api/accounts/{aid}/commands/{tpl_id}",
    response_model=dict,
)
async def disable_account_command(
    aid: int, tpl_id: int, db: DBSession, user: CurrentUser
) -> dict[str, bool]:
    """禁用某账号的某模板。"""
    await command_service.disable_for_account(db, aid, tpl_id)
    await audit.write(
        db,
        user.id,
        "account_command.disable",
        target=f"account:{aid}/command_template:{tpl_id}",
    )
    await db.commit()
    await command_service.notify_reload(aid)
    return {"ok": True}


# ════════════════════════════════════════════════════════════
# LLM Provider 模型管理（Fetch + Test）
# ════════════════════════════════════════════════════════════


def _llm_err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _resolve_proxy_url(db, proxy_id: int | None) -> str | None:
    """把 provider.proxy_id 翻译成 httpx 接受的 ``socks5://...`` / ``http://...`` URL。

    与 ``worker/runtime._build_proxy_url`` 同一逻辑；这里独立实现是因为本模块跑在
    主进程内（不能 import worker.runtime——后者持有 telethon 等重依赖）。
    """
    if proxy_id is None:
        return None
    from urllib.parse import quote

    from ..crypto import decrypt_str
    from ..db.models.account import Proxy

    p = await db.get(Proxy, proxy_id)
    if p is None:
        return None
    t = (p.type or "").lower()
    if t == "socks5":
        scheme = "socks5"
    elif t in ("http", "https"):
        scheme = "http"
    else:
        return None  # mtproxy / 不支持的类型
    pwd = ""
    if p.password_enc:
        try:
            pwd = decrypt_str(p.password_enc)
        except Exception:  # noqa: BLE001
            pwd = ""
    auth = ""
    if p.username:
        auth = quote(p.username, safe="")
        if pwd:
            auth = f"{auth}:{quote(pwd, safe='')}"
        auth = f"{auth}@"
    return f"{scheme}://{auth}{p.host}:{int(p.port)}"


@router.post(
    "/api/commands/llm-providers/{pid}/fetch-models",
    response_model=FetchModelsResponse,
)
async def fetch_models(
    pid: int, db: DBSession, user: CurrentUser
) -> FetchModelsResponse:
    """从 ``GET {base_url}/models`` 拉模型列表，合并到 provider.models。

    URL 选择基于 ``api_format``：
    - ``chat_completions`` / ``responses`` → ``GET {base_url}/models``（OpenAI 兼容；
      Responses API 与 chat/completions 共用同一 ``/models`` 端点）
    - ``anthropic_messages`` → 没有 list models 接口；返 422 让用户手填

    合并策略：保留已有 enabled 状态 + 用户自定义条目；fetch 来的新条目默认 enabled=False，
    用户自己决定要启用哪些。
    """
    from ..crypto import decrypt_str
    from ..db.models.command import (
        LLM_API_FORMAT_ANTHROPIC_MESSAGES,
        default_api_format_for,
    )

    row = await command_service.get_provider_row(db, pid)

    fmt = (
        getattr(row, "api_format", None)
        or default_api_format_for(row.provider)
    )
    if fmt == LLM_API_FORMAT_ANTHROPIC_MESSAGES:
        raise _llm_err(
            "FETCH_NOT_SUPPORTED",
            "Anthropic Messages 协议没有列出模型接口；请去 docs.anthropic.com 查模型 ID 后手动添加",
            422,
        )

    base_url = (row.base_url or "https://api.openai.com/v1").rstrip("/")
    api_key = decrypt_str(row.api_key_enc) if row.api_key_enc else ""
    proxy_url = await _resolve_proxy_url(db, row.proxy_id)

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    client_kwargs: dict[str, object] = {"timeout": httpx.Timeout(15.0, connect=8.0)}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    try:
        async with httpx.AsyncClient(**client_kwargs) as cli:
            resp = await cli.get(f"{base_url}/models", headers=headers)
    except httpx.HTTPError as exc:
        raise _llm_err(
            "FETCH_NETWORK",
            f"拉取失败：{type(exc).__name__}: {str(exc) or '(无详情；常见 SSL/DNS/代理问题)'}",
            502,
        ) from None

    if resp.status_code >= 400:
        # 把 api_key 从 body 里剥掉再返
        body = resp.text[:300]
        if api_key:
            body = body.replace(api_key, "<redacted>")
        raise _llm_err(
            "FETCH_HTTP",
            f"接口返回 {resp.status_code}: {body}",
            502,
        )

    try:
        data = resp.json()
    except Exception:
        raise _llm_err("FETCH_BAD_JSON", "响应不是合法 JSON") from None

    # OpenAI 兼容：{data: [{id, object: "model", ...}, ...]}
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise _llm_err(
            "FETCH_BAD_SHAPE",
            f"响应缺 'data' 数组（实际顶层 keys: {list(data.keys())[:5] if isinstance(data, dict) else type(data).__name__}）",
        )
    new_ids: list[str] = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("id"), str):
            mid = it["id"].strip()
            if mid:
                new_ids.append(mid)

    # 合并：保留已 enabled 状态 + custom 条目
    existing: dict[str, dict] = {
        m["id"]: m for m in (row.models or []) if isinstance(m, dict) and "id" in m
    }
    merged: list[dict] = []
    for mid in new_ids:
        if mid in existing:
            # 老条目：保留 enabled / label，custom 改成 false（毕竟现在 fetch 拿到了）
            old = existing[mid]
            merged.append({
                "id": mid,
                "enabled": bool(old.get("enabled", False)),
                "custom": False,
                "label": old.get("label"),
            })
        else:
            merged.append({"id": mid, "enabled": False, "custom": False, "label": None})

    # 用户的自定义条目（fetch 没拿到 ID 的）保留
    fetched_ids = set(new_ids)
    for mid, old in existing.items():
        if mid not in fetched_ids and old.get("custom"):
            merged.append({
                "id": mid,
                "enabled": bool(old.get("enabled", False)),
                "custom": True,
                "label": old.get("label"),
            })

    row.models = merged
    await audit.write(
        db,
        user.id,
        "llm_provider.fetch_models",
        target=f"llm_provider:{pid}",
        detail={"fetched": len(new_ids), "total": len(merged)},
    )
    await db.commit()
    await db.refresh(row)
    # 通知 worker reload；让下游能看到新模型清单
    aids = await command_service.list_aids_with_ai_commands(db)
    await command_service.notify_reload(aids)

    return FetchModelsResponse(
        fetched=len(new_ids),
        provider=command_service._provider_to_out(row),
    )


@router.post(
    "/api/commands/llm-providers/{pid}/test-model",
    response_model=TestModelResponse,
)
async def test_model(
    pid: int, payload: TestModelRequest, db: DBSession, user: CurrentUser
) -> TestModelResponse:
    """用一次 max_tokens=4 的最小调用测某个 model 通不通 + 测延时。

    用 ``services.llm_client.build_client``（与正式 ai 命令同路径），
    一并验证 api_key / base_url / proxy_url 都对。
    """
    from ..services.llm_client import LLMError, build_client

    row = await command_service.get_provider_row(db, pid)
    proxy_url = await _resolve_proxy_url(db, row.proxy_id)

    started = _time.monotonic()
    try:
        cli = build_client(row, override_model=payload.model.strip(), proxy_url=proxy_url)
        result = await cli.complete("ping", "ping", max_tokens=4)
    except LLMError as e:
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        # LLMError 已脱敏
        return TestModelResponse(ok=False, latency_ms=elapsed_ms, error=str(e))
    except Exception as e:  # noqa: BLE001
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        return TestModelResponse(
            ok=False,
            latency_ms=elapsed_ms,
            error=f"{type(e).__name__}: {str(e)[:200]}",
        )

    elapsed_ms = int((_time.monotonic() - started) * 1000)
    # 不写 audit（测试调用频繁，写多了刷屏）
    return TestModelResponse(
        ok=True,
        latency_ms=elapsed_ms,
        model=result.model,
        preview=(result.text or "").strip()[:80] or None,
    )


__all__ = ["router"]

===== backend/app/api/device_profiles.py =====
"""设备伪装库 API。

提供：
  - GET    /api/device-profiles            列表
  - POST   /api/device-profiles            新建（is_default=true 时自动把其它行置 false）
  - GET    /api/device-profiles/{pid}      详情
  - PATCH  /api/device-profiles/{pid}      修改
  - POST   /api/device-profiles/{pid}/default   置为系统默认
  - DELETE /api/device-profiles/{pid}      删除（被账号引用时仅置 SET NULL）

修改 device profile 的字段不会影响**已有 session**：Telegram 把设备名绑在 auth_key 上，
账号要让 TG 端显示新设备名必须重新登录。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update

from ..db.models.account import Account, DeviceProfile
from ..deps import CurrentUser, DBSession
from ..schemas.device_profile import (
    DeviceProfileCreate,
    DeviceProfileOut,
    DeviceProfileUpdate,
)
from ..services import audit

router = APIRouter(prefix="/api/device-profiles", tags=["device-profiles"])


def _err(code: str, message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


async def _clear_other_defaults(db: DBSession, keep_id: int | None = None) -> None:
    """把除 keep_id 之外的所有 profile is_default 置 false。"""
    stmt = update(DeviceProfile).values(is_default=False)
    if keep_id is not None:
        stmt = stmt.where(DeviceProfile.id != keep_id)
    await db.execute(stmt)


# ── CRUD ─────────────────────────────────────────────────────────
@router.get("", response_model=list[DeviceProfileOut])
async def list_profiles(db: DBSession, _user: CurrentUser) -> list[DeviceProfile]:
    rows = (
        await db.execute(select(DeviceProfile).order_by(DeviceProfile.id))
    ).scalars().all()
    return list(rows)


@router.post(
    "", response_model=DeviceProfileOut, status_code=status.HTTP_201_CREATED
)
async def create_profile(
    payload: DeviceProfileCreate, db: DBSession, user: CurrentUser
) -> DeviceProfile:
    if payload.is_default:
        await _clear_other_defaults(db)
    p = DeviceProfile(
        name=payload.name,
        device_model=payload.device_model,
        system_version=payload.system_version,
        app_version=payload.app_version,
        lang_code=payload.lang_code,
        system_lang_code=payload.system_lang_code,
        is_default=payload.is_default,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    await audit.write(
        db,
        user.id,
        "create_device_profile",
        target=str(p.id),
        detail={"name": p.name, "is_default": p.is_default},
    )
    await db.commit()
    return p


@router.get("/{pid}", response_model=DeviceProfileOut)
async def get_profile(
    pid: int, db: DBSession, _user: CurrentUser
) -> DeviceProfile:
    p = await db.get(DeviceProfile, pid)
    if not p:
        raise _err("NOT_FOUND", "设备伪装不存在", 404)
    return p


@router.patch("/{pid}", response_model=DeviceProfileOut)
async def patch_profile(
    pid: int,
    payload: DeviceProfileUpdate,
    db: DBSession,
    user: CurrentUser,
) -> DeviceProfile:
    p = await db.get(DeviceProfile, pid)
    if not p:
        raise _err("NOT_FOUND", "设备伪装不存在", 404)

    # 字段更新
    for field in (
        "name",
        "device_model",
        "system_version",
        "app_version",
        "lang_code",
        "system_lang_code",
    ):
        v = getattr(payload, field)
        if v is not None:
            setattr(p, field, v)

    # 默认状态切换
    if payload.is_default is True and not p.is_default:
        await _clear_other_defaults(db, keep_id=pid)
        p.is_default = True
    elif payload.is_default is False and p.is_default:
        # 不允许直接取消唯一的默认。如果用户想换默认，应该 POST .../{other}/default
        raise _err(
            "CANNOT_UNSET_DEFAULT",
            "不能直接取消唯一的默认 profile；请先把其它 profile 设为默认",
        )

    await db.commit()
    await audit.write(db, user.id, "update_device_profile", target=str(pid))
    await db.commit()
    await db.refresh(p)
    return p


@router.post("/{pid}/default", response_model=DeviceProfileOut)
async def set_default(
    pid: int, db: DBSession, user: CurrentUser
) -> DeviceProfile:
    p = await db.get(DeviceProfile, pid)
    if not p:
        raise _err("NOT_FOUND", "设备伪装不存在", 404)
    await _clear_other_defaults(db, keep_id=pid)
    p.is_default = True
    await db.commit()
    await audit.write(
        db, user.id, "set_default_device_profile", target=str(pid)
    )
    await db.commit()
    await db.refresh(p)
    return p


@router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(pid: int, db: DBSession, user: CurrentUser) -> None:
    p = await db.get(DeviceProfile, pid)
    if not p:
        raise _err("NOT_FOUND", "设备伪装不存在", 404)
    if p.is_default:
        # 删掉默认会导致没有 fallback；要求用户先把别的设为默认
        raise _err(
            "CANNOT_DELETE_DEFAULT",
            "默认 profile 不能删除；请先把其它 profile 设为默认",
        )
    # 被账号引用：FK 是 SET NULL，可以删，但提醒用户哪些账号会受影响（仅警告，不阻止）
    used = (
        await db.execute(
            select(Account.id).where(Account.device_profile_id == pid)
        )
    ).scalars().all()
    await db.delete(p)
    await db.commit()
    await audit.write(
        db,
        user.id,
        "delete_device_profile",
        target=str(pid),
        detail={"affected_accounts": list(used)},
    )
    await db.commit()

===== backend/app/api/features.py =====
"""功能矩阵与账号-功能开关 REST API（PRD §9.2）。

Endpoint：
  - GET  /api/feature-matrix                      → 一次返回 N×M 矩阵
  - GET  /api/accounts/{aid}/features            → 该账号所有 feature 开关
  - PATCH /api/accounts/{aid}/features/{key}     → 启停或调整 config
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db.models.account import Account
from ..db.models.feature import Feature
from ..deps import CurrentUser, DBSession
from ..schemas.feature import (
    AccountFeatureItem,
    AccountFeatureToggle,
    FeatureMatrixResponse,
)
from ..services import audit, feature_service

router = APIRouter(tags=["features"])


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


# ─────────────────────────────────────────────────────
# 矩阵
# ─────────────────────────────────────────────────────
@router.get("/api/feature-matrix", response_model=FeatureMatrixResponse)
async def get_feature_matrix(db: DBSession, _user: CurrentUser) -> FeatureMatrixResponse:
    """返回 N(账号) × M(功能) 矩阵。"""
    data = await feature_service.feature_matrix(db)
    return FeatureMatrixResponse(**data)


# ─────────────────────────────────────────────────────
# 单账号 feature 列表
# ─────────────────────────────────────────────────────
@router.get(
    "/api/accounts/{aid}/features",
    response_model=list[AccountFeatureItem],
)
async def list_account_features(
    aid: int, db: DBSession, _user: CurrentUser
) -> list[AccountFeatureItem]:
    """列出该账号所有 ``account_feature`` 行。"""
    if await db.get(Account, aid) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    rows = await feature_service.get_account_features(db, aid)
    return [
        AccountFeatureItem(
            feature_key=r.feature_key,
            enabled=r.enabled,
            state=r.state,
            last_error=r.last_error,
            config=dict(r.config or {}),
        )
        for r in rows
    ]


# ─────────────────────────────────────────────────────
# 启停 / 改 config
# ─────────────────────────────────────────────────────
@router.patch(
    "/api/accounts/{aid}/features/{key}",
    response_model=AccountFeatureItem,
)
async def patch_account_feature(
    aid: int,
    key: str,
    payload: AccountFeatureToggle,
    db: DBSession,
    user: CurrentUser,
) -> AccountFeatureItem:
    """启用 / 禁用某 feature，或更新它的 config。

    若 feature key 在 ``feature`` 表里没有登记，会拒绝（避免误开未知插件）。
    """
    if await db.get(Account, aid) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    # 校验 feature 存在（首次调用矩阵或 list 时已 seed 过；这里也 seed 一次以幂等）
    await feature_service.seed_builtin_features(db)
    if await db.get(Feature, key) is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的 feature: {key}", 404)

    af = await feature_service.set_account_feature(
        db, aid, key, enabled=payload.enabled, config=payload.config
    )
    await audit.write(
        db,
        user.id,
        "feature.toggle",
        target=f"account:{aid}/feature:{key}",
        detail={"enabled": payload.enabled},
    )
    await db.commit()
    return AccountFeatureItem(
        feature_key=af.feature_key,
        enabled=af.enabled,
        state=af.state,
        last_error=af.last_error,
        config=dict(af.config or {}),
    )


__all__ = ["router"]

===== backend/app/api/ignored_peers.py =====
"""忽略 peer 名单 REST API。

Endpoints（统一前缀 ``/api/accounts/{aid}/``）：
  - GET    /ignored-peers       列表
  - POST   /ignored-peers       加入；幂等
  - DELETE /ignored-peers/{id}  移除
  - GET    /recent-peers        最近活跃会话（worker 内存里的 LRU）

写操作完成后通过 IPC ``CMD_RELOAD_IGNORED`` 通知 worker 热更新。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import CurrentUser, DBSession
from ..schemas.ignored_peer import (
    IgnoredPeerCreate,
    IgnoredPeerOut,
    RecentPeerItem,
    RecentPeersResponse,
)
from ..services import audit, ignored_peer_service

router = APIRouter(tags=["ignored-peers"])


# ── 列表 ──────────────────────────────────────────────────────────
@router.get(
    "/api/accounts/{aid}/ignored-peers",
    response_model=list[IgnoredPeerOut],
)
async def list_ignored(
    aid: int, db: DBSession, _user: CurrentUser
) -> list[IgnoredPeerOut]:
    """返回该账号的忽略 peer 列表（按 added_at 倒序）。"""
    rows = await ignored_peer_service.list_ignored(db, aid)
    return [IgnoredPeerOut.model_validate(r) for r in rows]


# ── 加入 ──────────────────────────────────────────────────────────
@router.post(
    "/api/accounts/{aid}/ignored-peers",
    response_model=IgnoredPeerOut,
    status_code=201,
)
async def add_ignored(
    aid: int,
    payload: IgnoredPeerCreate,
    db: DBSession,
    user: CurrentUser,
) -> IgnoredPeerOut:
    """加入忽略名单；同 (account_id, peer_id) 已存在直接返回原行（幂等）。

    成功后通过 IPC 通知 worker 热加载（worker 离线静默）。
    """
    row = await ignored_peer_service.add_ignored(db, aid, payload)
    await audit.write(
        db,
        user.id,
        "ignored_peer.add",
        target=f"account:{aid}/peer:{payload.peer_id}",
        detail={"peer_kind": row.peer_kind, "peer_label": row.peer_label},
    )
    await db.commit()
    # 提交事务后再下发 IPC，避免 worker 拉到尚未 commit 的视图
    await ignored_peer_service.notify_reload(aid)
    return IgnoredPeerOut.model_validate(row)


# ── 移除 ──────────────────────────────────────────────────────────
@router.delete("/api/accounts/{aid}/ignored-peers/{ignored_id}")
async def remove_ignored(
    aid: int,
    ignored_id: int,
    db: DBSession,
    user: CurrentUser,
) -> dict[str, bool]:
    """从忽略名单移除一行；找不到抛 404。"""
    await ignored_peer_service.remove_ignored(db, aid, ignored_id)
    await audit.write(
        db,
        user.id,
        "ignored_peer.remove",
        target=f"account:{aid}/ignored:{ignored_id}",
    )
    await db.commit()
    await ignored_peer_service.notify_reload(aid)
    return {"ok": True}


# ── 最近活跃 ──────────────────────────────────────────────────────
@router.get(
    "/api/accounts/{aid}/recent-peers",
    response_model=RecentPeersResponse,
)
async def list_recent(
    aid: int, _db: DBSession, _user: CurrentUser
) -> RecentPeersResponse:
    """通过 IPC RPC 向对应 worker 拉一次"最近活跃 peer"列表（≤50 条）。

    超时 1.5s。返回包含 ``worker_alive`` 字段以便前端区分：
    - ``worker_alive=False`` → worker 没在跑（让用户去暂停 → 启动）
    - ``worker_alive=True``  且 ``items=[]`` → worker 在跑但没收到 incoming
    - ``worker_alive=True``  且 ``items=[...]`` → 正常
    """
    alive, raw = await ignored_peer_service.fetch_recent(aid)
    out: list[RecentPeerItem] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        peer_id = it.get("peer_id")
        ts = it.get("ts")
        if peer_id is None or ts is None:
            continue
        out.append(
            RecentPeerItem(
                peer_id=int(peer_id),
                peer_kind=str(it.get("peer_kind") or "private"),
                peer_label=it.get("peer_label"),
                ts=float(ts),
            )
        )
    return RecentPeersResponse(worker_alive=alive, items=out)


__all__ = ["router"]

===== backend/app/api/logs.py =====
"""日志查询 API（PRD §9.6）。

涵盖：
  - ``GET /api/logs/audit``：操作日志（Web 端 Action）
  - ``GET /api/logs/runtime``：运行日志（worker 输出，由 supervisor 批量消费 stream 落库）

只读接口，鉴权后返回最近一段时间的日志列表，按 ts 倒序。前端在 Dashboard
摘要卡 + 日志页过滤都使用本路由。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ..db.models.log import AuditLog, RuntimeLog
from ..deps import CurrentUser, DBSession

router = APIRouter(tags=["logs"])


# ── 出参 ─────────────────────────────────────────────────────────
class AuditLogItem(BaseModel):
    """审计（操作）日志条目。"""

    id: int
    ts: datetime
    user_id: int | None
    action: str
    target: str | None
    detail: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class RuntimeLogItem(BaseModel):
    """运行日志条目（worker 上抛）。"""

    id: int
    ts: datetime
    # 兼容字段：前端 E 已使用 ``created_at``，这里同步输出，避免破坏现有页面
    created_at: datetime
    account_id: int | None
    level: str
    source: str | None
    message: str
    detail: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: RuntimeLog) -> RuntimeLogItem:
        return cls(
            id=row.id,
            ts=row.ts,
            created_at=row.ts,
            account_id=row.account_id,
            level=row.level,
            source=row.source,
            message=row.message,
            detail=row.detail,
        )

    model_config = ConfigDict(from_attributes=True)


# ── /api/logs/audit ──────────────────────────────────────────────
@router.get("/api/logs/audit", response_model=list[AuditLogItem])
async def list_audit_logs(
    db: DBSession,
    _user: CurrentUser,
    user_id: int | None = Query(None, description="按 web_user 过滤"),
    since: datetime | None = Query(None, description="ISO 时间，仅返回此后的日志"),
    limit: int = Query(50, ge=1, le=500),
) -> list[AuditLogItem]:
    """返回最近的操作日志，按时间倒序。"""
    stmt = select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if since is not None:
        stmt = stmt.where(AuditLog.ts >= since)
    rows = (await db.execute(stmt)).scalars().all()
    return [AuditLogItem.model_validate(r) for r in rows]


# ── /api/logs/runtime ────────────────────────────────────────────
# source 别名映射：
#   - 历史数据 source="worker" / "plugin" 一直存在，新代码改写成 "system" / "event"
#   - 前端只暴露 "system" / "event" 两种 tab；这里把请求转换成对应集合
_SOURCE_ALIAS: dict[str, tuple[str, ...]] = {
    "system": ("system", "worker"),
    "event": ("event", "plugin"),
}


@router.get("/api/logs/runtime", response_model=list[RuntimeLogItem])
async def list_runtime_logs(
    db: DBSession,
    _user: CurrentUser,
    account_id: int | None = Query(None, description="按账号过滤"),
    level: str | None = Query(None, description="debug | info | warn | warning | error"),
    source: str | None = Query(
        None,
        description='日志类别："system"（worker 启停/错误）或 "event"（消息事件/plugin 命中）',
    ),
    since: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[RuntimeLogItem]:
    """返回最近运行日志。

    兼容前端传 ``level=warning``：内部映射为 ``level >= 'warn'``（warn + error）。
    ``source`` 支持 ``"system"`` / ``"event"`` 两种 tab，自动包含历史 ``worker`` / ``plugin`` 旧值。
    """
    stmt = select(RuntimeLog).order_by(RuntimeLog.ts.desc()).limit(limit)
    if account_id is not None:
        stmt = stmt.where(RuntimeLog.account_id == account_id)
    if since is not None:
        stmt = stmt.where(RuntimeLog.ts >= since)
    if level:
        norm = level.lower()
        if norm == "warning":
            stmt = stmt.where(RuntimeLog.level.in_(("warn", "warning", "error")))
        else:
            stmt = stmt.where(RuntimeLog.level == norm)
    if source:
        aliases = _SOURCE_ALIAS.get(source.lower())
        if aliases is not None:
            stmt = stmt.where(RuntimeLog.source.in_(aliases))
        else:
            stmt = stmt.where(RuntimeLog.source == source)
    rows = (await db.execute(stmt)).scalars().all()
    return [RuntimeLogItem.from_row(r) for r in rows]

===== backend/app/api/network.py =====
"""网络环境探测 API。

提供：
  - ``GET /api/system/network``  返回当前后端进程出口 IP + 国家/地区
  - ``GET /api/system/network/refresh``  强制刷新（绕过缓存）

结果缓存 5 分钟（避免每次请求都打 ipinfo.io）。前端 TopBar 用此显示当前环境。
"""

from __future__ import annotations

import asyncio
import time as _time

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from ..deps import CurrentUser

router = APIRouter(prefix="/api/system", tags=["system"])


class NetworkInfo(BaseModel):
    ip: str | None = None
    country: str | None = None      # ISO 国家/地区代码（CN / US / JP / HK 等）
    region: str | None = None
    city: str | None = None
    org: str | None = None          # ISP / ASN
    cached_at: float = 0.0          # 客户端可用以判断是否过期
    fresh: bool = True              # 本次是否新拉到（false=用缓存）
    error: str | None = None


_TTL_SECONDS = 5 * 60
_CACHE: dict[str, NetworkInfo] = {}
_LOCK = asyncio.Lock()


async def _fetch() -> NetworkInfo:
    """实际调外部 API 拿出口 IP 信息。优先 ip-api.com（HTTP，免费 45/min），失败回退 ipinfo.io。"""
    # 主：ip-api.com（HTTP，无 token，字段全）
    try:
        async with httpx.AsyncClient(timeout=8.0) as cli:
            r = await cli.get("http://ip-api.com/json/")
            r.raise_for_status()
            d = r.json()
            if d.get("status") == "success":
                return NetworkInfo(
                    ip=d.get("query"),
                    country=d.get("countryCode"),
                    region=d.get("regionName") or d.get("region"),
                    city=d.get("city"),
                    org=d.get("isp") or d.get("org"),
                    cached_at=_time.time(),
                    fresh=True,
                )
    except Exception:
        pass

    # 备：ipinfo.io（HTTPS，限流后会 429）
    try:
        async with httpx.AsyncClient(timeout=8.0) as cli:
            r = await cli.get("https://ipinfo.io/json")
            r.raise_for_status()
            d = r.json()
            return NetworkInfo(
                ip=d.get("ip"),
                country=d.get("country"),
                region=d.get("region"),
                city=d.get("city"),
                org=d.get("org"),
                cached_at=_time.time(),
                fresh=True,
            )
    except Exception as e:
        return NetworkInfo(
            cached_at=_time.time(),
            fresh=True,
            error=f"{type(e).__name__}: {e}",
        )


async def _get_or_fetch(force: bool = False) -> NetworkInfo:
    async with _LOCK:
        cached = _CACHE.get("v1")
        now = _time.time()
        if not force and cached and (now - cached.cached_at) < _TTL_SECONDS:
            return cached.model_copy(update={"fresh": False})
        info = await _fetch()
        _CACHE["v1"] = info
        return info


@router.get("/network", response_model=NetworkInfo)
async def get_network(_user: CurrentUser) -> NetworkInfo:
    return await _get_or_fetch(force=False)


@router.post("/network/refresh", response_model=NetworkInfo)
async def refresh_network(_user: CurrentUser) -> NetworkInfo:
    return await _get_or_fetch(force=True)

===== backend/app/api/plugins.py =====
"""插件市场 REST API（PRD §9.5，MVP 骨架）。

包含两类资源：
  1. ``plugin_repo``：第三方插件源 CRUD + 同步触发；MVP 阶段同步本身返 501。
  2. ``plugins``：可用 / 已安装清单 + install / uninstall / enable / disable / reload；
     MVP 仅对内置 5 个 feature 生效（实际转发到 ``feature_service``），非内置返 501。

注意：第三方插件源的 manifest 解析与运行时安全沙箱在 V1+ 才会实现，本文件只搭骨架。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db.models.account import Account
from ..db.models.feature import (
    BUILTIN_FEATURES,
    AccountFeature,
    Feature,
)
from ..db.models.plugin import PluginAvailable, PluginRepo
from ..deps import CurrentUser, DBSession
from ..redis_client import get_redis
from ..services import audit, feature_service
from ..worker.ipc import CMD_RELOAD_PLUGIN, cmd_channel, make_cmd

log = logging.getLogger(__name__)
router = APIRouter(tags=["plugins"])


# ─────────────────────────────────────────────────────
# Pydantic 入参（这些 schema 比较简单，就地定义）
# ─────────────────────────────────────────────────────
class PluginRepoCreate(BaseModel):
    name: str
    url: str
    enabled: bool = True


class PluginRepoOut(BaseModel):
    id: int
    name: str
    url: str
    enabled: bool
    last_synced_at: datetime | None = None


class PluginAvailableOut(BaseModel):
    repo_id: int
    key: str
    name: str
    version: str
    author: str | None = None
    description: str | None = None


class PluginInstallRequest(BaseModel):
    plugin_key: str
    account_ids: list[int]


class PluginAccountActionRequest(BaseModel):
    """用于 enable / disable / reload 等"对一组账号统一动作"的入参。"""

    account_ids: list[int]


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _is_builtin(plugin_key: str) -> bool:
    return plugin_key in BUILTIN_FEATURES


# ─────────────────────────────────────────────────────
# plugin_repo CRUD（骨架）
# ─────────────────────────────────────────────────────
@router.get("/api/plugin-repos", response_model=list[PluginRepoOut])
async def list_repos(db: DBSession, _user: CurrentUser) -> list[PluginRepoOut]:
    rows = (
        await db.execute(select(PluginRepo).order_by(PluginRepo.id.asc()))
    ).scalars().all()
    return [
        PluginRepoOut(
            id=r.id,
            name=r.name,
            url=r.url,
            enabled=r.enabled,
            last_synced_at=r.last_synced_at,
        )
        for r in rows
    ]


@router.post("/api/plugin-repos", response_model=PluginRepoOut, status_code=201)
async def create_repo(
    payload: PluginRepoCreate, db: DBSession, user: CurrentUser
) -> PluginRepoOut:
    repo = PluginRepo(name=payload.name, url=payload.url, enabled=payload.enabled)
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    await audit.write(
        db,
        user.id,
        "plugin_repo.create",
        target=f"repo:{repo.id}",
        detail={"name": payload.name, "url": payload.url},
    )
    await db.commit()
    return PluginRepoOut(
        id=repo.id,
        name=repo.name,
        url=repo.url,
        enabled=repo.enabled,
        last_synced_at=repo.last_synced_at,
    )


@router.delete("/api/plugin-repos/{rid}", status_code=204)
async def delete_repo(rid: int, db: DBSession, user: CurrentUser) -> None:
    repo = await db.get(PluginRepo, rid)
    if repo is None:
        raise _bad("REPO_NOT_FOUND", "插件源不存在", 404)
    await db.delete(repo)
    await db.commit()
    await audit.write(db, user.id, "plugin_repo.delete", target=f"repo:{rid}")
    await db.commit()


@router.post("/api/plugin-repos/{rid}/sync")
async def sync_repo(rid: int, db: DBSession, _user: CurrentUser) -> dict[str, Any]:
    """触发插件源同步。MVP 暂未实现，直接返 501。"""
    if await db.get(PluginRepo, rid) is None:
        raise _bad("REPO_NOT_FOUND", "插件源不存在", 404)
    raise HTTPException(
        status_code=501,
        detail={"code": "NOT_IMPLEMENTED", "message": "插件源同步将在后续版本支持"},
    )


# ─────────────────────────────────────────────────────
# 可用 / 已安装清单
# ─────────────────────────────────────────────────────
@router.get("/api/plugins/available", response_model=list[PluginAvailableOut])
async def list_available(db: DBSession, _user: CurrentUser) -> list[PluginAvailableOut]:
    """从 ``plugin_available`` 取已同步的可用插件清单（MVP 通常为空）。"""
    rows = (
        await db.execute(
            select(PluginAvailable).order_by(PluginAvailable.repo_id, PluginAvailable.key)
        )
    ).scalars().all()
    return [
        PluginAvailableOut(
            repo_id=r.repo_id,
            key=r.key,
            name=r.name,
            version=r.version,
            author=r.author,
            description=r.description,
        )
        for r in rows
    ]


@router.get("/api/plugins/installed")
async def list_installed(
    db: DBSession,
    _user: CurrentUser,
    account_id: int | None = None,
) -> list[dict[str, Any]]:
    """返回安装关系列表：``account_feature`` join ``feature``，按账号过滤。"""
    q = select(AccountFeature, Feature).join(Feature, AccountFeature.feature_key == Feature.key)
    if account_id is not None:
        q = q.where(AccountFeature.account_id == account_id)
    rows = (await db.execute(q)).all()
    out: list[dict[str, Any]] = []
    for af, feat in rows:
        out.append(
            {
                "account_id": af.account_id,
                "feature_key": af.feature_key,
                "display_name": feat.display_name,
                "is_builtin": feat.is_builtin,
                "enabled": af.enabled,
                "state": af.state,
                "last_error": af.last_error,
                "version": feat.version,
            }
        )
    return out


# ─────────────────────────────────────────────────────
# install / uninstall / enable / disable / reload
# ─────────────────────────────────────────────────────
def _ensure_builtin_or_501(plugin_key: str) -> None:
    if not _is_builtin(plugin_key):
        raise HTTPException(
            status_code=501,
            detail={
                "code": "NOT_IMPLEMENTED",
                "message": f"非内置插件管理将在后续版本支持: {plugin_key}",
            },
        )


@router.post("/api/plugins/install")
async def install_plugin(
    payload: PluginInstallRequest, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    """对一组账号安装某插件（内置 = 启用 ``account_feature``）。"""
    _ensure_builtin_or_501(payload.plugin_key)
    await feature_service.seed_builtin_features(db)
    await _ensure_accounts_exist(db, payload.account_ids)
    n = await feature_service.bulk_set_enabled(
        db, payload.account_ids, payload.plugin_key, enabled=True
    )
    await audit.write(
        db,
        user.id,
        "plugin.install",
        target=f"plugin:{payload.plugin_key}",
        detail={"account_ids": payload.account_ids},
    )
    await db.commit()
    return {"applied": n}


@router.post("/api/plugins/uninstall")
async def uninstall_plugin(
    payload: PluginInstallRequest, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    """对一组账号卸载某插件（内置 = 禁用 ``account_feature``，配置保留以便重新启用）。"""
    _ensure_builtin_or_501(payload.plugin_key)
    await _ensure_accounts_exist(db, payload.account_ids)
    n = await feature_service.bulk_set_enabled(
        db, payload.account_ids, payload.plugin_key, enabled=False
    )
    await audit.write(
        db,
        user.id,
        "plugin.uninstall",
        target=f"plugin:{payload.plugin_key}",
        detail={"account_ids": payload.account_ids},
    )
    await db.commit()
    return {"applied": n}


@router.post("/api/plugins/{key}/enable")
async def enable_plugin(
    key: str, payload: PluginAccountActionRequest, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    _ensure_builtin_or_501(key)
    await feature_service.seed_builtin_features(db)
    await _ensure_accounts_exist(db, payload.account_ids)
    n = await feature_service.bulk_set_enabled(db, payload.account_ids, key, enabled=True)
    await audit.write(
        db,
        user.id,
        "plugin.enable",
        target=f"plugin:{key}",
        detail={"account_ids": payload.account_ids},
    )
    await db.commit()
    return {"applied": n}


@router.post("/api/plugins/{key}/disable")
async def disable_plugin(
    key: str, payload: PluginAccountActionRequest, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    _ensure_builtin_or_501(key)
    await _ensure_accounts_exist(db, payload.account_ids)
    n = await feature_service.bulk_set_enabled(db, payload.account_ids, key, enabled=False)
    await audit.write(
        db,
        user.id,
        "plugin.disable",
        target=f"plugin:{key}",
        detail={"account_ids": payload.account_ids},
    )
    await db.commit()
    return {"applied": n}


@router.post("/api/plugins/{key}/reload")
async def reload_plugin_endpoint(
    key: str, payload: PluginAccountActionRequest, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    """对一组账号广播 ``CMD_RELOAD_PLUGIN``，让 worker 重新 import 该插件并重新激活。"""
    _ensure_builtin_or_501(key)
    await _ensure_accounts_exist(db, payload.account_ids)
    redis = None
    try:
        redis = get_redis()
    except Exception:  # noqa: BLE001
        redis = None
    n = 0
    for aid in payload.account_ids:
        if redis is not None:
            try:
                await redis.publish(
                    cmd_channel(aid),
                    make_cmd(CMD_RELOAD_PLUGIN, plugin_key=key),
                )
                n += 1
            except Exception:  # noqa: BLE001
                log.debug("reload plugin 广播失败 aid=%s", aid, exc_info=True)
    await audit.write(
        db,
        user.id,
        "plugin.reload",
        target=f"plugin:{key}",
        detail={"account_ids": payload.account_ids},
    )
    await db.commit()
    return {"sent": n}


# ─────────────────────────────────────────────────────
# 内部工具
# ─────────────────────────────────────────────────────
async def _ensure_accounts_exist(db, aids: list[int]) -> None:
    """批量校验账号都存在，缺一个就 404。"""
    for aid in aids:
        if await db.get(Account, aid) is None:
            raise _bad("ACCOUNT_NOT_FOUND", f"账号不存在: {aid}", 404)


__all__ = ["router"]

===== backend/app/api/plugins_install.py =====
"""第三方插件 zip 上传 / 启停 / 卸载 REST API（阶段 B）。

为已有 ``api/plugins.py`` 的 builtin 插件管理补一组面向"安装包"维度的端点：

  GET    /api/plugins/installed-packages       列出 plugin_install 表
  POST   /api/plugins/install/upload           multipart：上传 zip + 可选 sig
  POST   /api/plugins/install/{key}/enable     启用安装包（不影响账号矩阵）
  POST   /api/plugins/install/{key}/disable    禁用安装包
  DELETE /api/plugins/install/{key}            卸载（删表 + 删目录）

注意路径与已有 ``/api/plugins/installed`` 区分：``installed`` 返回的是
"账号 × feature 矩阵"，这里加的 ``installed-packages`` 是"包级别"清单——
两者维度不同，前端会分别展示。

成功执行 enable/disable/uninstall 后，本接口会向所有在线账号广播
``CMD_RELOAD_CONFIG``，让 worker 重新扫描插件目录 + 差量加载。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from ..db.models.account import Account
from ..db.models.plugin import PluginInstall
from ..deps import CurrentUser, DBSession
from ..redis_client import get_redis
from ..services import audit
from ..services import plugin_install_service as pis
from ..services import plugin_repo_service as prs
from ..settings import settings
from ..worker.ipc import CMD_RELOAD_CONFIG, cmd_channel, make_cmd

log = logging.getLogger(__name__)
router = APIRouter(tags=["plugins"])


# ─────────────────────────────────────────────────────
# 出参 schema
# ─────────────────────────────────────────────────────
class PluginInstallOut(BaseModel):
    """暴露给前端的安装记录视图。"""

    key: str
    source: str
    version: str
    enabled: bool
    signature_ok: bool | None
    installed_path: str
    repo_id: int | None = None
    manifest: dict[str, Any] | None = None
    installed_at: datetime
    updated_at: datetime


def _to_out(row: PluginInstall) -> PluginInstallOut:
    return PluginInstallOut(
        key=row.key,
        source=row.source,
        version=row.version,
        enabled=bool(row.enabled),
        signature_ok=row.signature_ok,
        installed_path=row.installed_path,
        repo_id=row.repo_id,
        manifest=row.manifest_json,
        installed_at=row.installed_at,
        updated_at=row.updated_at,
    )


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _map_install_error(exc: pis.PluginInstallError) -> HTTPException:
    """把 service 层的 PluginInstallError 翻译成合适的 HTTP 状态。"""
    status_map = {
        "ZIP_TOO_LARGE": 413,
        "BAD_ZIP": 400,
        "ZIP_ABS_PATH": 400,
        "ZIP_PATH_TRAVERSAL": 400,
        "MISSING_REQUIRED_FILE": 400,
        "BAD_MANIFEST": 400,
        "BAD_MANIFEST_KEY": 400,
        "MANIFEST_LOAD_FAIL": 400,
        "MANIFEST_EXEC_FAIL": 400,
        "MANIFEST_MISSING_CONST": 400,
        "KEY_CONFLICTS_BUILTIN": 409,
        "BAD_KEY_PATH": 400,
        "PLUGIN_NOT_FOUND": 404,
        "SIGNATURE_FAILED": 403,
    }
    return _bad(exc.code, exc.message, status_map.get(exc.code, 400))


async def _broadcast_reload_config(db) -> int:
    """对所有账号广播 CMD_RELOAD_CONFIG；离线账号会 publish 到无人订阅的频道，无副作用。

    返回成功 publish 的次数（不代表 worker 真处理了）。
    """
    aids = (await db.execute(select(Account.id))).scalars().all()
    redis = None
    try:
        redis = get_redis()
    except Exception:  # noqa: BLE001
        log.debug("get_redis 失败，跳过广播", exc_info=True)
        return 0
    n = 0
    for aid in aids:
        try:
            await redis.publish(cmd_channel(int(aid)), make_cmd(CMD_RELOAD_CONFIG))
            n += 1
        except Exception:  # noqa: BLE001
            log.debug("publish reload_config 失败 aid=%s", aid, exc_info=True)
    return n


# ─────────────────────────────────────────────────────
# GET 安装包列表
# ─────────────────────────────────────────────────────
@router.get("/api/plugins/installed-packages", response_model=list[PluginInstallOut])
async def list_installed_packages(
    db: DBSession, _user: CurrentUser
) -> list[PluginInstallOut]:
    rows = await pis.list_installed(db)
    return [_to_out(r) for r in rows]


# ─────────────────────────────────────────────────────
# POST 上传 zip
# ─────────────────────────────────────────────────────
@router.post("/api/plugins/install/upload", response_model=PluginInstallOut)
async def upload_zip(
    db: DBSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="插件 zip"),
    signature: UploadFile | None = File(
        None, description="可选 detached Ed25519 签名（.sig）"
    ),
) -> PluginInstallOut:
    # 读 zip 字节；FastAPI 默认会 spool 到磁盘，read() 可能很大但有 settings 限制兜底
    zip_bytes = await file.read()
    if len(zip_bytes) > settings.plugin_zip_max_bytes:
        raise _bad(
            "ZIP_TOO_LARGE",
            f"zip 体积超出 {settings.plugin_zip_max_bytes // 1024 // 1024} MiB 上限",
            413,
        )
    sig_bytes: bytes | None = None
    if signature is not None:
        sig_bytes = await signature.read()
        # 简单防滥用：sig 应该很短；超 1 KiB 直接拒绝
        if len(sig_bytes) > 1024:
            raise _bad("SIG_TOO_LARGE", "签名文件过大（> 1 KiB）", 413)

    try:
        row = await pis.install_zip(
            db,
            zip_bytes=zip_bytes,
            signature=sig_bytes,
            source=pis.PLUGIN_SOURCE_ZIP if False else "zip",  # noqa: SIM222
        )
    except pis.PluginInstallError as exc:
        raise _map_install_error(exc) from exc

    await audit.write(
        db,
        user.id,
        "plugin.install_zip",
        target=f"plugin:{row.key}",
        detail={
            "version": row.version,
            "signature_ok": row.signature_ok,
            "filename": file.filename,
        },
    )
    await db.commit()
    return _to_out(row)


# ─────────────────────────────────────────────────────
# POST enable / disable
# ─────────────────────────────────────────────────────
@router.post("/api/plugins/install/{key}/enable", response_model=PluginInstallOut)
async def enable_install(
    key: str, db: DBSession, user: CurrentUser
) -> PluginInstallOut:
    try:
        row = await pis.set_enabled(db, key, True)
    except pis.PluginInstallError as exc:
        raise _map_install_error(exc) from exc
    await audit.write(db, user.id, "plugin.install_enable", target=f"plugin:{key}")
    await db.commit()
    # 通知 worker 重新扫描 + 差量加载
    await _broadcast_reload_config(db)
    return _to_out(row)


@router.post("/api/plugins/install/{key}/disable", response_model=PluginInstallOut)
async def disable_install(
    key: str, db: DBSession, user: CurrentUser
) -> PluginInstallOut:
    try:
        row = await pis.set_enabled(db, key, False)
    except pis.PluginInstallError as exc:
        raise _map_install_error(exc) from exc
    await audit.write(db, user.id, "plugin.install_disable", target=f"plugin:{key}")
    await db.commit()
    await _broadcast_reload_config(db)
    return _to_out(row)


# ─────────────────────────────────────────────────────
# DELETE 卸载
# ─────────────────────────────────────────────────────
@router.delete("/api/plugins/install/{key}", status_code=204)
async def delete_install(key: str, db: DBSession, user: CurrentUser) -> None:
    deleted = await pis.uninstall(db, key)
    if not deleted:
        raise _bad("PLUGIN_NOT_FOUND", f"插件不存在: {key}", 404)
    await audit.write(db, user.id, "plugin.install_uninstall", target=f"plugin:{key}")
    await db.commit()
    await _broadcast_reload_config(db)


# ─────────────────────────────────────────────────────
# 阶段 C：仓库同步 + 从仓库安装
# ─────────────────────────────────────────────────────
def _map_repo_error(exc: prs.PluginRepoError) -> HTTPException:
    """仓库错误 code → HTTP 状态。"""
    status_map = {
        "REPO_NOT_FOUND": 404,
        "REPO_DISABLED": 409,
        "BAD_URL": 400,
        "FETCH_FAILED": 502,
        "BAD_INDEX_JSON": 502,
        "BAD_INDEX_SCHEMA": 502,
        "PLUGIN_NOT_IN_REPO": 404,
        "MISSING_ZIP_URL": 502,
        "DOWNLOAD_FAILED": 502,
        "ZIP_TOO_LARGE": 413,
        "SIG_TOO_LARGE": 413,
    }
    # 未列入的 code（来自 service 透传）默认 400
    return _bad(exc.code, exc.message, status_map.get(exc.code, 400))


class RepoSyncOut(BaseModel):
    inserted: int


@router.post("/api/plugin-repos/{rid}/sync2", response_model=RepoSyncOut)
async def sync_repo_v2(rid: int, db: DBSession, user: CurrentUser) -> RepoSyncOut:
    """实际拉远程 ``index.json`` 并刷新 ``plugin_available``。

    路径用 ``/sync2`` 与 ``api/plugins.py`` 中既有的 501 占位 ``/sync`` 区分；
    阶段 C 真正接入此实现。前端市场页用 ``/sync2``。
    """
    try:
        n = await prs.sync_repo(db, rid)
    except prs.PluginRepoError as exc:
        raise _map_repo_error(exc) from exc
    await audit.write(
        db,
        user.id,
        "plugin_repo.sync",
        target=f"repo:{rid}",
        detail={"inserted": n},
    )
    await db.commit()
    return RepoSyncOut(inserted=n)


class InstallFromRepoIn(BaseModel):
    repo_id: int
    key: str


@router.post(
    "/api/plugins/install/from-repo", response_model=PluginInstallOut
)
async def install_from_repo(
    payload: InstallFromRepoIn, db: DBSession, user: CurrentUser
) -> PluginInstallOut:
    """从仓库下载并安装某个 key 的插件包；签名失败会写库但不启用。"""
    try:
        row = await prs.install_from_repo(db, payload.repo_id, payload.key)
    except prs.PluginRepoError as exc:
        raise _map_repo_error(exc) from exc
    await audit.write(
        db,
        user.id,
        "plugin.install_from_repo",
        target=f"plugin:{row.key}",
        detail={
            "repo_id": payload.repo_id,
            "version": row.version,
            "signature_ok": row.signature_ok,
        },
    )
    await db.commit()
    return _to_out(row)


__all__ = ["router"]

===== backend/app/api/proxies.py =====
"""代理（出口 IP）相关 API。

提供：
  - ``GET    /api/proxies``         列表
  - ``POST   /api/proxies``         新建（密码经主密钥加密落盘）
  - ``GET    /api/proxies/{pid}``   详情（不返密码）
  - ``PATCH  /api/proxies/{pid}``   修改
  - ``DELETE /api/proxies/{pid}``   删除（被账号引用时返 409）
  - ``POST   /api/proxies/{pid}/test``  连通性测试 + 出口 IP 与归属地

代理被绑定向导、账号详情用作下拉源；worker 启动时按账号配置串入 Telethon。
"""

from __future__ import annotations

import asyncio
import socket
import time as _time

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict
from python_socks import ProxyConnectionError, ProxyError, ProxyType
from python_socks.async_.asyncio import Proxy as AsyncProxy
from sqlalchemy import select

from ..crypto import decrypt_str, encrypt_str
from ..db.models.account import Account, Proxy
from ..deps import CurrentUser, DBSession
from ..services import audit

router = APIRouter(prefix="/api/proxies", tags=["proxies"])


# ── Schemas ──────────────────────────────────────────────────────
class ProxyOut(BaseModel):
    """代理出参，绝不返回明文密码。"""

    id: int
    type: str
    host: str
    port: int
    username: str | None = None
    has_password: bool = False

    model_config = ConfigDict(from_attributes=True)


class ProxyCreate(BaseModel):
    type: str          # socks5 | http | mtproxy
    host: str
    port: int
    username: str | None = None
    password: str | None = None


class ProxyUpdate(BaseModel):
    type: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None     # 显式传空字符串表示清空；None 表示保持
    clear_password: bool = False


class ProxyTestResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    exit_ip: str | None = None
    country: str | None = None
    region: str | None = None
    city: str | None = None
    error: str | None = None


# ── 工具 ─────────────────────────────────────────────────────────
def _err(code: str, message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _to_out(p: Proxy) -> ProxyOut:
    return ProxyOut(
        id=p.id, type=p.type, host=p.host, port=p.port,
        username=p.username, has_password=bool(p.password_enc),
    )


_VALID_TYPES = {"socks5", "http", "https", "mtproxy"}


def _validate_type(t: str) -> None:
    if t not in _VALID_TYPES:
        raise _err("INVALID_PROXY_TYPE", f"代理类型必须是 {', '.join(sorted(_VALID_TYPES))}")


# ── CRUD ─────────────────────────────────────────────────────────
@router.get("", response_model=list[ProxyOut])
async def list_proxies(db: DBSession, _user: CurrentUser) -> list[ProxyOut]:
    rows = (await db.execute(select(Proxy).order_by(Proxy.id))).scalars().all()
    return [_to_out(p) for p in rows]


@router.post("", response_model=ProxyOut, status_code=status.HTTP_201_CREATED)
async def create_proxy(payload: ProxyCreate, db: DBSession, user: CurrentUser) -> ProxyOut:
    _validate_type(payload.type)
    if payload.port <= 0 or payload.port > 65535:
        raise _err("INVALID_PORT", "端口范围必须是 1-65535")
    p = Proxy(
        type=payload.type, host=payload.host, port=payload.port,
        username=payload.username,
        password_enc=encrypt_str(payload.password) if payload.password else None,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    await audit.write(db, user.id, "create_proxy", target=str(p.id),
                       detail={"type": p.type, "host": p.host, "port": p.port})
    await db.commit()
    return _to_out(p)


@router.get("/{pid}", response_model=ProxyOut)
async def get_proxy(pid: int, db: DBSession, _user: CurrentUser) -> ProxyOut:
    p = await db.get(Proxy, pid)
    if not p:
        raise _err("NOT_FOUND", "代理不存在", 404)
    return _to_out(p)


@router.patch("/{pid}", response_model=ProxyOut)
async def patch_proxy(pid: int, payload: ProxyUpdate, db: DBSession, user: CurrentUser) -> ProxyOut:
    p = await db.get(Proxy, pid)
    if not p:
        raise _err("NOT_FOUND", "代理不存在", 404)
    if payload.type is not None:
        _validate_type(payload.type)
        p.type = payload.type
    if payload.host is not None:
        p.host = payload.host
    if payload.port is not None:
        if payload.port <= 0 or payload.port > 65535:
            raise _err("INVALID_PORT", "端口范围必须是 1-65535")
        p.port = payload.port
    if payload.username is not None:
        p.username = payload.username or None
    if payload.clear_password:
        p.password_enc = None
    elif payload.password is not None and payload.password != "":
        p.password_enc = encrypt_str(payload.password)
    await db.commit()
    await audit.write(db, user.id, "update_proxy", target=str(pid))
    await db.commit()
    return _to_out(p)


@router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proxy(pid: int, db: DBSession, user: CurrentUser) -> None:
    p = await db.get(Proxy, pid)
    if not p:
        raise _err("NOT_FOUND", "代理不存在", 404)
    # 被账号引用就拒删
    used = (await db.execute(
        select(Account.id).where(Account.proxy_id == pid).limit(1)
    )).scalar_one_or_none()
    if used:
        raise _err("PROXY_IN_USE", f"代理被账号 #{used} 使用中，无法删除", 409)
    await db.delete(p)
    await db.commit()
    await audit.write(db, user.id, "delete_proxy", target=str(pid))
    await db.commit()


# ── 连通性测试 ────────────────────────────────────────────────────
# Telegram MTProto 测试目标：DC2（公开常稳）
_TG_HOST = "149.154.167.50"
_TG_PORT = 443


async def _resolve_country(client_factory) -> dict[str, str | None]:
    """通过给定 client 拿到出口 IP + 国家/地区。优先 ip-api.com，失败回退 ipinfo.io。"""
    try:
        async with client_factory() as cli:
            r = await cli.get("http://ip-api.com/json/", timeout=8.0)
            r.raise_for_status()
            d = r.json()
            if d.get("status") == "success":
                return {
                    "exit_ip": d.get("query"),
                    "country": d.get("countryCode"),
                    "region": d.get("regionName") or d.get("region"),
                    "city": d.get("city"),
                }
    except Exception:
        pass
    try:
        async with client_factory() as cli:
            r = await cli.get("https://ipinfo.io/json", timeout=8.0)
            r.raise_for_status()
            d = r.json()
            return {
                "exit_ip": d.get("ip"),
                "country": d.get("country"),
                "region": d.get("region"),
                "city": d.get("city"),
            }
    except Exception:
        return {"exit_ip": None, "country": None, "region": None, "city": None}


@router.post("/{pid}/test", response_model=ProxyTestResult)
async def test_proxy(pid: int, db: DBSession, _user: CurrentUser) -> ProxyTestResult:
    """通过该代理连接 Telegram MTProto + 查询出口 IP 归属地。"""
    p = await db.get(Proxy, pid)
    if not p:
        raise _err("NOT_FOUND", "代理不存在", 404)

    pwd = decrypt_str(p.password_enc) if p.password_enc else None

    # 第一步：try TCP connect 到 Telegram DC2:443，记录延迟
    t0 = _time.monotonic()
    try:
        if p.type in ("socks5", "http", "https"):
            ptype = {"socks5": ProxyType.SOCKS5, "http": ProxyType.HTTP,
                     "https": ProxyType.HTTP}[p.type]
            proxy_obj = AsyncProxy(
                proxy_type=ptype, host=p.host, port=p.port,
                username=p.username or None, password=pwd or None,
            )
            sock = await asyncio.wait_for(
                proxy_obj.connect(dest_host=_TG_HOST, dest_port=_TG_PORT),
                timeout=8.0,
            )
            sock.close()
        elif p.type == "mtproxy":
            # MTProxy 不走 python-socks；这里只做 TCP 探活到代理端口
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            await asyncio.get_event_loop().run_in_executor(
                None, sock.connect, (p.host, p.port)
            )
            sock.close()
        else:
            return ProxyTestResult(ok=False, error=f"不支持的代理类型: {p.type}")
    except ProxyConnectionError as e:
        return ProxyTestResult(ok=False, error=f"代理连接失败: {e}")
    except ProxyError as e:
        return ProxyTestResult(ok=False, error=f"代理协议错误: {e}")
    except TimeoutError:
        return ProxyTestResult(ok=False, error="超时（8s 内未建立连接）")
    except Exception as e:
        return ProxyTestResult(ok=False, error=f"{type(e).__name__}: {e}")

    latency_ms = int((_time.monotonic() - t0) * 1000)

    # 第二步：通过该代理访问 ipinfo.io 拿出口 IP
    def _make_client():
        if p.type in ("http", "https"):
            url = f"http://{p.username + ':' + pwd + '@' if p.username else ''}{p.host}:{p.port}"
            return httpx.AsyncClient(proxy=url)
        if p.type == "socks5":
            scheme = "socks5"
            auth = f"{p.username}:{pwd}@" if p.username else ""
            url = f"{scheme}://{auth}{p.host}:{p.port}"
            return httpx.AsyncClient(proxy=url)
        # MTProxy 不能拿 IP
        return httpx.AsyncClient()

    geo = await _resolve_country(_make_client)
    return ProxyTestResult(
        ok=True, latency_ms=latency_ms,
        exit_ip=geo["exit_ip"], country=geo["country"],
        region=geo["region"], city=geo["city"],
    )

===== backend/app/api/rate_limit.py =====
"""风控相关 REST API（PRD §9.4）。

涵盖：
  - 模板 CRUD + 模板下规则 CRUD
  - 账号级风控配置（含继承后的有效阈值）
  - 用量查询（实时 token bucket 用量）
  - 事件流查询
  - 一键调严 / override 列表
  - 拟人化配置 GET/PUT
  - 模拟测算（MVP 简化）
  - 全局总闸 + 全局每秒上限

写操作通过 ``_audit`` 写一条 ``AuditLog``；A Agent 的 audit 服务到位时可改为调用它。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import time as dtime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from ..db.models.account import Account
from ..db.models.rate_limit import (
    ACTION_KEYS,
    SCOPE_ACCOUNT,
    SCOPE_TEMPLATE,
    RateLimitEvent,
    RateLimitTemplate,
)
from ..db.models.system import SystemSetting
from ..deps import CurrentUser, DBSession
from ..redis_client import get_redis
from ..schemas.rate_limit import (
    POLICIES,
    AccountRateLimitOut,
    EstimateRequest,
    EstimateResponse,
    GlobalLimitsRequest,
    HumanizeOut,
    HumanizeUpdate,
    KillSwitchRequest,
    RateLimitRuleConfig,
    StrictRequest,
    TemplateCreate,
    TemplateOut,
    UsageBucket,
    UsageResponse,
)
from ..services import audit as audit_svc
from ..services import rate_limit_service as svc
from ..worker.ipc import GCMD_KILL_SWITCH, GCMD_RELOAD_GLOBAL, GLOBAL_CHANNEL, make_cmd
from ..worker.ratelimit.buckets import TokenBuckets
from ..worker.ratelimit.overrides import add_override, drop_override, list_active

router = APIRouter(tags=["rate-limit"])


# ─────────────────────────────────────────────────────
# 公用：审计写入（统一走 services.audit；本端补一次 commit）
# ─────────────────────────────────────────────────────
async def _audit(
    db, user_id: int | None, action: str, target: str | None = None, detail: dict | None = None
) -> None:
    """``services.audit.write`` 不内部 commit，这里补一次 commit 确保落库。"""
    await audit_svc.write(db, user_id, action, target=target, detail=detail)
    await db.commit()


def _bad(code: str, msg: str, http_status: int = 400) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, "message": msg})


def _validate_action(action: str) -> None:
    if action not in ACTION_KEYS:
        raise _bad("invalid_action", f"未知 action：{action}")


def _validate_policy(policy: str | None) -> None:
    if policy is not None and policy not in POLICIES:
        raise _bad("invalid_policy", f"未知 policy：{policy}")


def _parse_time(s: str | None) -> dtime | None:
    """``'HH:MM'`` 或 ``'HH:MM:SS'`` → ``datetime.time``。"""
    if not s:
        return None
    parts = s.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        sec = int(parts[2]) if len(parts) > 2 else 0
        return dtime(hour=h, minute=m, second=sec)
    except (ValueError, IndexError) as e:
        raise _bad("invalid_time", f"非法时间格式：{s}") from e


# ─────────────────────────────────────────────────────
# 模板
# ─────────────────────────────────────────────────────
@router.get("/api/rate-templates", response_model=list[TemplateOut])
async def list_templates(db: DBSession, _user: CurrentUser) -> list[TemplateOut]:
    return [TemplateOut.model_validate(t) for t in await svc.list_templates(db)]


@router.post("/api/rate-templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(payload: TemplateCreate, db: DBSession, user: CurrentUser) -> TemplateOut:
    tpl = await svc.create_template(db, name=payload.name, is_default=payload.is_default)
    await _audit(db, user.id, "create_rate_template", target=str(tpl.id), detail={"name": payload.name})
    return TemplateOut.model_validate(tpl)


@router.patch("/api/rate-templates/{tpl_id}", response_model=TemplateOut)
async def patch_template(tpl_id: int, payload: TemplateCreate, db: DBSession, user: CurrentUser) -> TemplateOut:
    tpl = await svc.update_template(db, tpl_id, name=payload.name, is_default=payload.is_default)
    if tpl is None:
        raise _bad("not_found", "模板不存在", 404)
    await _audit(db, user.id, "update_rate_template", target=str(tpl_id))
    return TemplateOut.model_validate(tpl)


@router.delete("/api/rate-templates/{tpl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(tpl_id: int, db: DBSession, user: CurrentUser) -> None:
    ok = await svc.delete_template(db, tpl_id)
    if not ok:
        raise _bad("not_found", "模板不存在", 404)
    await _audit(db, user.id, "delete_rate_template", target=str(tpl_id))


@router.get("/api/rate-templates/{tpl_id}/rules", response_model=list[RateLimitRuleConfig])
async def list_template_rules(tpl_id: int, db: DBSession, _user: CurrentUser) -> list[RateLimitRuleConfig]:
    if await db.get(RateLimitTemplate, tpl_id) is None:
        raise _bad("not_found", "模板不存在", 404)
    return [RateLimitRuleConfig.model_validate(r) for r in await svc.list_rules(db, SCOPE_TEMPLATE, tpl_id)]


@router.patch("/api/rate-templates/{tpl_id}/rules/{action}", response_model=RateLimitRuleConfig)
async def patch_template_rule(
    tpl_id: int,
    action: str,
    payload: RateLimitRuleConfig,
    db: DBSession,
    user: CurrentUser,
) -> RateLimitRuleConfig:
    _validate_action(action)
    _validate_policy(payload.policy)
    if await db.get(RateLimitTemplate, tpl_id) is None:
        raise _bad("not_found", "模板不存在", 404)
    rule = await svc.upsert_rule(
        db,
        SCOPE_TEMPLATE,
        tpl_id,
        action,
        per_second=payload.per_second,
        per_minute=payload.per_minute,
        per_hour=payload.per_hour,
        per_day=payload.per_day,
        same_peer_per_minute=payload.same_peer_per_minute,
        policy=payload.policy,
        backoff_base_seconds=payload.backoff_base_seconds,
        backoff_max_seconds=payload.backoff_max_seconds,
        enabled=payload.enabled,
    )
    await _audit(db, user.id, "update_template_rule", target=f"tpl:{tpl_id}/{action}")
    await _broadcast_reload()
    return RateLimitRuleConfig.model_validate(rule)


# ─────────────────────────────────────────────────────
# 账号级风控
# ─────────────────────────────────────────────────────
@router.get("/api/accounts/{aid}/rate-limit", response_model=AccountRateLimitOut)
async def get_account_rate_limit(aid: int, db: DBSession, _user: CurrentUser) -> AccountRateLimitOut:
    acc = await db.get(Account, aid)
    if acc is None:
        raise _bad("not_found", "账号不存在", 404)
    rules: list[RateLimitRuleConfig] = []
    # 把每个 ACTION_KEYS 的"有效配置"返回，前端按行渲染并标注继承层
    for action in ACTION_KEYS:
        eff = await svc.get_effective(db, aid, action)
        rules.append(
            RateLimitRuleConfig(
                action=action,
                per_second=eff.per_second,
                per_minute=eff.per_minute,
                per_hour=eff.per_hour,
                per_day=eff.per_day,
                same_peer_per_minute=eff.same_peer_per_minute,
                policy=eff.policy,
                backoff_base_seconds=eff.backoff_base,
                backoff_max_seconds=eff.backoff_max,
                enabled=not eff.disabled,
            )
        )
    return AccountRateLimitOut(template_id=acc.template_id, rules=rules)


@router.put("/api/accounts/{aid}/rate-limit", response_model=AccountRateLimitOut)
async def put_account_rate_limit(
    aid: int,
    payload: list[RateLimitRuleConfig],
    db: DBSession,
    user: CurrentUser,
) -> AccountRateLimitOut:
    if await db.get(Account, aid) is None:
        raise _bad("not_found", "账号不存在", 404)
    for cfg in payload:
        _validate_action(cfg.action)
        _validate_policy(cfg.policy)
        await svc.upsert_rule(
            db,
            SCOPE_ACCOUNT,
            aid,
            cfg.action,
            per_second=cfg.per_second,
            per_minute=cfg.per_minute,
            per_hour=cfg.per_hour,
            per_day=cfg.per_day,
            same_peer_per_minute=cfg.same_peer_per_minute,
            policy=cfg.policy,
            backoff_base_seconds=cfg.backoff_base_seconds,
            backoff_max_seconds=cfg.backoff_max_seconds,
            enabled=cfg.enabled,
        )
    await _audit(db, user.id, "put_account_rate_limit", target=f"acc:{aid}")
    await _broadcast_reload()
    return await get_account_rate_limit(aid, db, user)


@router.patch("/api/accounts/{aid}/rate-limit/{action}", response_model=RateLimitRuleConfig)
async def patch_account_rule(
    aid: int,
    action: str,
    payload: RateLimitRuleConfig,
    db: DBSession,
    user: CurrentUser,
) -> RateLimitRuleConfig:
    _validate_action(action)
    _validate_policy(payload.policy)
    if await db.get(Account, aid) is None:
        raise _bad("not_found", "账号不存在", 404)
    rule = await svc.upsert_rule(
        db,
        SCOPE_ACCOUNT,
        aid,
        action,
        per_second=payload.per_second,
        per_minute=payload.per_minute,
        per_hour=payload.per_hour,
        per_day=payload.per_day,
        same_peer_per_minute=payload.same_peer_per_minute,
        policy=payload.policy,
        backoff_base_seconds=payload.backoff_base_seconds,
        backoff_max_seconds=payload.backoff_max_seconds,
        enabled=payload.enabled,
    )
    await _audit(db, user.id, "patch_account_rule", target=f"acc:{aid}/{action}")
    await _broadcast_reload()
    return RateLimitRuleConfig.model_validate(rule)


@router.delete("/api/accounts/{aid}/rate-limit/{action}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account_rule(aid: int, action: str, db: DBSession, user: CurrentUser) -> None:
    _validate_action(action)
    if await db.get(Account, aid) is None:
        raise _bad("not_found", "账号不存在", 404)
    await svc.delete_rule(db, SCOPE_ACCOUNT, aid, action)
    await _audit(db, user.id, "delete_account_rule", target=f"acc:{aid}/{action}")
    await _broadcast_reload()


# ─────────────────────────────────────────────────────
# 用量
# ─────────────────────────────────────────────────────
_WINDOW_TO_KEY = {"1m": "minute", "1h": "hour", "24h": "day", "1s": "second"}


@router.get("/api/accounts/{aid}/rate-limit/usage", response_model=UsageResponse)
async def get_usage(
    aid: int,
    db: DBSession,
    _user: CurrentUser,
    window: str = Query("1m", pattern="^(1s|1m|1h|24h)$"),
) -> UsageResponse:
    if await db.get(Account, aid) is None:
        raise _bad("not_found", "账号不存在", 404)
    redis = get_redis()
    buckets = TokenBuckets(redis)
    win_key = _WINDOW_TO_KEY[window]
    bucket_field = {
        "second": "per_second",
        "minute": "per_minute",
        "hour": "per_hour",
        "day": "per_day",
    }[win_key]

    out: list[UsageBucket] = []
    for action in ACTION_KEYS:
        eff = await svc.get_effective(db, aid, action)
        limit = getattr(eff, bucket_field, None)
        used = await buckets.usage(aid, action, win_key)
        pct = (used / limit * 100) if limit else 0.0
        out.append(UsageBucket(action=action, used=float(used), limit=limit, pct=round(pct, 2), warn=pct >= 80))

    actives = await list_active(db, aid)
    overrides = [
        {
            "action": o.action,
            "multiplier": float(o.multiplier),
            "expires_at": o.expires_at.isoformat() if o.expires_at else None,
            "reason": o.reason,
        }
        for o in actives
    ]
    return UsageResponse(window=window, buckets=out, active_overrides=overrides)


# ─────────────────────────────────────────────────────
# 事件流
# ─────────────────────────────────────────────────────
@router.get("/api/accounts/{aid}/rate-limit/events")
async def get_events(
    aid: int,
    db: DBSession,
    _user: CurrentUser,
    since: datetime | None = None,
    action: str | None = None,
    outcome: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
) -> list[dict]:
    if await db.get(Account, aid) is None:
        raise _bad("not_found", "账号不存在", 404)
    q = select(RateLimitEvent).where(RateLimitEvent.account_id == aid)
    if since is not None:
        q = q.where(RateLimitEvent.ts >= since)
    if action:
        q = q.where(RateLimitEvent.action == action)
    if outcome:
        q = q.where(RateLimitEvent.outcome == outcome)
    q = q.order_by(RateLimitEvent.ts.desc()).limit(limit)
    res = await db.execute(q)
    return [
        {
            "id": e.id,
            "ts": e.ts.isoformat() if e.ts else None,
            "action": e.action,
            "outcome": e.outcome,
            "detail": e.detail,
        }
        for e in res.scalars().all()
    ]


# ─────────────────────────────────────────────────────
# 一键调严 + override 列表
# ─────────────────────────────────────────────────────
@router.post("/api/accounts/{aid}/rate-limit/strict")
async def post_strict(
    aid: int,
    payload: StrictRequest,
    db: DBSession,
    user: CurrentUser,
) -> dict[str, Any]:
    if await db.get(Account, aid) is None:
        raise _bad("not_found", "账号不存在", 404)
    redis = get_redis()
    # 对所有 action 写 override（一键全局调严）
    for action in ACTION_KEYS:
        await add_override(
            db,
            redis,
            aid,
            action,
            multiplier=float(payload.multiplier),
            ttl_seconds=int(payload.ttl_seconds),
            reason=f"manual_strict by user#{user.id}",
        )
    await _audit(
        db,
        user.id,
        "rate_limit_strict",
        target=f"acc:{aid}",
        detail={"multiplier": payload.multiplier, "ttl_seconds": payload.ttl_seconds},
    )
    return {"applied": len(ACTION_KEYS), "expires_in": payload.ttl_seconds}


@router.delete(
    "/api/accounts/{aid}/rate-limit/overrides/{action}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_override(aid: int, action: str, db: DBSession, user: CurrentUser) -> None:
    _validate_action(action)
    redis = get_redis()
    await drop_override(db, redis, aid, action)
    await _audit(db, user.id, "drop_override", target=f"acc:{aid}/{action}")


@router.get("/api/accounts/{aid}/rate-limit/overrides")
async def get_overrides(aid: int, db: DBSession, _user: CurrentUser) -> list[dict]:
    if await db.get(Account, aid) is None:
        raise _bad("not_found", "账号不存在", 404)
    actives = await list_active(db, aid)
    return [
        {
            "id": o.id,
            "action": o.action,
            "multiplier": float(o.multiplier),
            "reason": o.reason,
            "expires_at": o.expires_at.isoformat() if o.expires_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in actives
    ]


# ─────────────────────────────────────────────────────
# 拟人化
# ─────────────────────────────────────────────────────
@router.get("/api/accounts/{aid}/humanize", response_model=HumanizeOut)
async def get_humanize(aid: int, db: DBSession, _user: CurrentUser) -> HumanizeOut:
    if await db.get(Account, aid) is None:
        raise _bad("not_found", "账号不存在", 404)
    cfg = await svc.get_humanize(db, aid)
    if cfg is None:
        # 返回默认值
        return HumanizeOut(
            jitter_pct=15,
            typing_simulate=True,
            typing_min_ms=1000,
            typing_max_ms=3000,
            typing_probability=80,
            read_before_reply=True,
            active_window_start=None,
            active_window_end=None,
            cold_start_days=7,
        )
    return HumanizeOut(
        jitter_pct=cfg.jitter_pct,
        typing_simulate=cfg.typing_simulate,
        typing_min_ms=cfg.typing_min_ms,
        typing_max_ms=cfg.typing_max_ms,
        typing_probability=cfg.typing_probability,
        read_before_reply=cfg.read_before_reply,
        active_window_start=cfg.active_window_start.isoformat() if cfg.active_window_start else None,
        active_window_end=cfg.active_window_end.isoformat() if cfg.active_window_end else None,
        cold_start_days=cfg.cold_start_days,
    )


@router.put("/api/accounts/{aid}/humanize", response_model=HumanizeOut)
async def put_humanize(
    aid: int,
    payload: HumanizeUpdate,
    db: DBSession,
    user: CurrentUser,
) -> HumanizeOut:
    if await db.get(Account, aid) is None:
        raise _bad("not_found", "账号不存在", 404)
    await svc.upsert_humanize(
        db,
        aid,
        jitter_pct=payload.jitter_pct,
        typing_simulate=payload.typing_simulate,
        typing_min_ms=payload.typing_min_ms,
        typing_max_ms=payload.typing_max_ms,
        typing_probability=payload.typing_probability,
        read_before_reply=payload.read_before_reply,
        active_window_start=_parse_time(payload.active_window_start),
        active_window_end=_parse_time(payload.active_window_end),
        cold_start_days=payload.cold_start_days,
    )
    await _audit(db, user.id, "update_humanize", target=f"acc:{aid}")
    await _broadcast_reload()
    return await get_humanize(aid, db, user)


# ─────────────────────────────────────────────────────
# 模拟测算（MVP 简化：只看 per_minute）
# ─────────────────────────────────────────────────────
@router.post("/api/accounts/{aid}/rate-limit/estimate", response_model=EstimateResponse)
async def estimate(
    aid: int,
    payload: EstimateRequest,
    db: DBSession,
    _user: CurrentUser,
) -> EstimateResponse:
    _validate_action(payload.action)
    if await db.get(Account, aid) is None:
        raise _bad("not_found", "账号不存在", 404)
    if payload.target_count <= 0 or payload.total_count <= 0:
        return EstimateResponse(eta_seconds=0, exceeds_limit=False)
    eff = await svc.get_effective(db, aid, payload.action)
    # 取最严的窗口估算（MVP）
    candidates: list[float] = []
    if eff.per_second:
        candidates.append(payload.total_count / float(eff.per_second))
    if eff.per_minute:
        candidates.append(payload.total_count / float(eff.per_minute) * 60.0)
    if eff.per_hour:
        candidates.append(payload.total_count / float(eff.per_hour) * 3600.0)
    if eff.per_day:
        candidates.append(payload.total_count / float(eff.per_day) * 86400.0)
    eta = max(candidates) if candidates else 0.0
    exceeds = bool(eff.per_day and payload.total_count > eff.per_day)
    return EstimateResponse(eta_seconds=int(eta), exceeds_limit=exceeds)


# ─────────────────────────────────────────────────────
# 全局总闸 + 全局每秒上限
# ─────────────────────────────────────────────────────
async def _get_setting(db, key: str, default) -> Any:
    row = await db.get(SystemSetting, key)
    return row.value if row else default


async def _set_setting(db, key: str, value: Any) -> None:
    row = await db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
    await db.commit()


@router.get("/api/system/kill-switch")
async def get_kill_switch(db: DBSession, _user: CurrentUser) -> dict[str, bool]:
    val = await _get_setting(db, "kill_switch", {"enabled": False})
    return {"enabled": bool(val.get("enabled", False)) if isinstance(val, dict) else bool(val)}


@router.post("/api/system/kill-switch")
async def post_kill_switch(payload: KillSwitchRequest, db: DBSession, user: CurrentUser) -> dict[str, bool]:
    await _set_setting(db, "kill_switch", {"enabled": bool(payload.enabled)})
    await _audit(
        db,
        user.id,
        "kill_switch",
        target="system",
        detail={"enabled": payload.enabled},
    )
    # 全局广播给所有 worker
    try:
        redis = get_redis()
        await redis.publish(GLOBAL_CHANNEL, make_cmd(GCMD_KILL_SWITCH, enabled=bool(payload.enabled)))
    except Exception:
        pass
    return {"enabled": payload.enabled}


@router.get("/api/system/global-limits")
async def get_global_limits(db: DBSession, _user: CurrentUser) -> dict[str, int]:
    val = await _get_setting(db, "global_api_qps", {"api_qps_total": 0})
    qps = val.get("api_qps_total", 0) if isinstance(val, dict) else int(val)
    return {"api_qps_total": int(qps)}


@router.put("/api/system/global-limits")
async def put_global_limits(
    payload: GlobalLimitsRequest, db: DBSession, user: CurrentUser
) -> dict[str, int]:
    await _set_setting(db, "global_api_qps", {"api_qps_total": int(payload.api_qps_total)})
    await _audit(
        db,
        user.id,
        "set_global_limits",
        target="system",
        detail={"api_qps_total": payload.api_qps_total},
    )
    await _broadcast_reload()
    return {"api_qps_total": payload.api_qps_total}


# ─────────────────────────────────────────────────────
# /api/system/settings —— 通用系统设置（命令前缀等）
# 前端 Settings 页用：读 command_prefix；写后通过 IPC 让所有 worker 热加载
# ─────────────────────────────────────────────────────
@router.get("/api/system/settings")
async def get_system_settings(db: DBSession, _user: CurrentUser) -> dict[str, Any]:
    """返回当前生效的全局设置。"""
    prefix_val = await _get_setting(db, "command_prefix", None)
    if isinstance(prefix_val, dict):
        prefix = prefix_val.get("value", ",")
    elif prefix_val is None:
        # 回落到 .env 默认
        from ..settings import settings as app_settings
        prefix = app_settings.command_prefix
    else:
        prefix = str(prefix_val)
    kill_val = await _get_setting(db, "kill_switch", {"enabled": False})
    qps_val = await _get_setting(db, "global_api_qps", {"api_qps_total": 0})
    return {
        "command_prefix": prefix,
        "kill_switch": bool(kill_val.get("enabled", False)) if isinstance(kill_val, dict) else bool(kill_val),
        "api_qps_total": int(qps_val.get("api_qps_total", 0)) if isinstance(qps_val, dict) else int(qps_val),
    }


class _SettingsPatch(BaseModel):
    """前端只会传子集；未传字段保持不变。"""

    command_prefix: str | None = None


@router.patch("/api/system/settings")
async def patch_system_settings(
    payload: _SettingsPatch, db: DBSession, user: CurrentUser
) -> dict[str, Any]:
    if payload.command_prefix is not None:
        prefix = payload.command_prefix.strip()
        if not prefix:
            raise _bad("invalid_prefix", "命令前缀不能为空")
        if len(prefix) > 3:
            raise _bad("invalid_prefix", "命令前缀最长 3 个字符")
        await _set_setting(db, "command_prefix", {"value": prefix})
        await _audit(db, user.id, "set_command_prefix", target="system", detail={"value": prefix})
        # 让所有 worker 热加载新前缀
        await _broadcast_reload()
    return await get_system_settings(db, user)


# ─────────────────────────────────────────────────────
# 内部工具：广播 reload
# ─────────────────────────────────────────────────────
async def _broadcast_reload() -> None:
    """风控 / 拟人化变更后通知所有 worker 重新加载。

    异常吞掉：广播失败不应影响 API 写库结果。
    """
    try:
        redis = get_redis()
        await redis.publish(GLOBAL_CHANNEL, make_cmd(GCMD_RELOAD_GLOBAL))
    except Exception:
        # 无 redis 时（例如测试环境）静默
        await asyncio.sleep(0)

===== backend/app/api/rules.py =====
"""规则（Rule）REST API（PRD §9.3）。

统一为 ``[账号 × feature]`` 下的 Rule 提供 CRUD + dry-run + 复制到其它账号。
所有写操作完成后通过 IPC ``CMD_RELOAD_CONFIG`` 通知对应 worker 热加载。

注意：当前 dry-run 仅对 ``auto_reply`` 实现真正的命中判断；其它 feature 返回不命中。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..db.models.account import Account
from ..db.models.feature import (
    BUILTIN_FEATURES,
    FEATURE_AUTO_REPLY,
    FEATURE_FORWARD,
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

===== backend/app/api/system_health.py =====
"""系统健康概览 API。

提供：
  - ``GET /api/system/health-overview``  一次性返回所有运维向状态：
    DB 连通 + alembic 版本同步 / Redis / LLM provider 池 / 代理 / 账号 worker 状态分布

设计目标：
- 所有探测 ≤ 2s 超时；任一项失败不影响其他项；前端能在 Dashboard 一眼看清"系统健不健康"
- 不返回敏感字段（不含明文 api_key、不含 proxy 密码、不含 session_str）
- 老数据兼容：getattr 兜底，避免历史迁移没跑齐时直接 500
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from ..db.base import AsyncSessionLocal
from ..db.models.account import Account, Proxy
from ..db.models.command import LLMProvider
from ..deps import CurrentUser
from ..redis_client import get_redis

router = APIRouter(prefix="/api/system", tags=["system"])


# ════════════════════════════════════════════════════════════
# Schemas
# ════════════════════════════════════════════════════════════


class DbStatus(BaseModel):
    ok: bool
    version: str | None = None
    """形如 ``"PostgreSQL 16.1"``。失败时为 None；error 字段含原因。"""
    error: str | None = None


class AlembicStatus(BaseModel):
    ok: bool
    """``True`` 表示 DB 当前版本 == 代码 head；``False`` 表示需要跑 ``alembic upgrade head``。"""
    current: str | None = None
    """DB 里 ``alembic_version`` 表的版本字符串。"""
    head: str | None = None
    """代码仓库里 alembic 链的最新版本。"""
    pending: list[str] = Field(default_factory=list)
    """已经写在文件里、但还没 apply 到 DB 的迁移版本号列表（按时间序）。"""
    error: str | None = None


class RedisStatus(BaseModel):
    ok: bool
    error: str | None = None


class ProvidersStatus(BaseModel):
    total: int = 0
    with_api_key: int = 0
    """配齐了 api_key（或 ollama 本地）能直接被调的数量。"""
    with_proxy: int = 0
    """指定了出口代理的 provider 数量；其余走 DIRECT。"""
    by_modality: dict[str, int] = Field(default_factory=dict)
    """按 modality 计数，如 ``{"text":2,"vision":1,"multimodal":1}``。"""
    by_cost_tier: dict[str, int] = Field(default_factory=dict)
    """按 cost_tier 计数，如 ``{"1":1,"2":2,"3":1}``。key 是 str 是因为 JSON 不支持 int 键。"""


class ProxiesStatus(BaseModel):
    total: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    """如 ``{"socks5":2,"http":1}``。mtproxy 也算在内；前端展示"可用于 LLM 的"由前端过滤。"""
    used_by_llm: int = 0
    """被某个 LLMProvider.proxy_id 引用的代理数量（去重）。"""


class WorkersStatus(BaseModel):
    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    """如 ``{"active":3,"paused":1,"login_required":1,"dead":0,"floodwait":0}``。"""


class HealthOverview(BaseModel):
    """前端 Dashboard 用的一次性聚合状态。"""

    db: DbStatus
    alembic: AlembicStatus
    redis: RedisStatus
    providers: ProvidersStatus
    proxies: ProxiesStatus
    workers: WorkersStatus


# ════════════════════════════════════════════════════════════
# 各子探测
# ════════════════════════════════════════════════════════════


async def _probe_db() -> DbStatus:
    """``SELECT version()`` 顺手把 DB 版本号也带回来。"""
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(text("SELECT version()"))).scalar()
            ver_str = str(row or "").strip()
            # 把超长字符串截断；PostgreSQL 16.1 (Debian 16.1-1.pgdg120+1) on x86_64...
            if len(ver_str) > 80:
                ver_str = ver_str[:80].rstrip() + "..."
            return DbStatus(ok=True, version=ver_str)
    except Exception as e:  # noqa: BLE001
        return DbStatus(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")


async def _probe_redis() -> RedisStatus:
    try:
        r = get_redis()
        pong = await r.ping()
        if not pong:
            return RedisStatus(ok=False, error="PING returned falsy")
        return RedisStatus(ok=True)
    except Exception as e:  # noqa: BLE001
        return RedisStatus(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")


def _probe_alembic() -> AlembicStatus:
    """对比 DB 里 alembic_version 与代码仓库里的 head。

    同步实现（alembic API 都是同步）；调用方应在 ``asyncio.to_thread`` 里跑。
    """
    try:
        from pathlib import Path

        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from sqlalchemy import create_engine

        from ..settings import settings

        ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
        if not ini_path.exists():
            return AlembicStatus(ok=False, error=f"alembic.ini 不存在：{ini_path}")

        cfg = Config(str(ini_path))
        script = ScriptDirectory.from_config(cfg)
        head_rev = script.get_current_head() or ""

        # 同步引擎读 alembic_version
        sync_engine = create_engine(settings.database_url_sync)
        try:
            with sync_engine.connect() as conn:
                ctx = MigrationContext.configure(conn)
                current = ctx.get_current_revision() or ""
        finally:
            sync_engine.dispose()

        in_sync = bool(head_rev) and current == head_rev
        pending: list[str] = []
        if not in_sync and head_rev:
            # 列出从 current 到 head 之间还差哪几个迁移
            try:
                for rev in script.walk_revisions(base="base", head=head_rev):
                    if rev.revision == current:
                        break
                    pending.append(rev.revision)
                pending.reverse()  # walk_revisions 默认 head→base，反过来变 base→head
            except Exception:
                pending = []
        return AlembicStatus(
            ok=in_sync, current=current or None, head=head_rev or None, pending=pending
        )
    except Exception as e:  # noqa: BLE001
        return AlembicStatus(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")


async def _probe_providers() -> ProvidersStatus:
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(LLMProvider))).scalars().all()
        total = len(rows)
        with_key = sum(
            1 for r in rows
            if r.api_key_enc or (r.provider or "").lower() == "ollama"
        )
        with_proxy = sum(1 for r in rows if r.proxy_id is not None)
        by_modality: Counter[str] = Counter(
            (getattr(r, "modality", None) or "text") for r in rows
        )
        by_cost_tier: Counter[str] = Counter(
            str(int(getattr(r, "cost_tier", None) or 2)) for r in rows
        )
        return ProvidersStatus(
            total=total,
            with_api_key=with_key,
            with_proxy=with_proxy,
            by_modality=dict(by_modality),
            by_cost_tier=dict(by_cost_tier),
        )
    except Exception:  # noqa: BLE001
        # 失败时返空统计而不是抛——alembic 不同步时 SELECT * 会爆，但 alembic 探测自己会标 ok=False
        return ProvidersStatus()


async def _probe_proxies() -> ProxiesStatus:
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(Proxy))).scalars().all()
            # 被 LLMProvider 引用的 proxy id 集合
            used_ids = (
                await db.execute(
                    select(LLMProvider.proxy_id).where(LLMProvider.proxy_id.is_not(None))
                )
            ).scalars().all()
        used_set = {x for x in used_ids if x is not None}
        by_type: Counter[str] = Counter((p.type or "?").lower() for p in rows)
        return ProxiesStatus(
            total=len(rows),
            by_type=dict(by_type),
            used_by_llm=len(used_set),
        )
    except Exception:  # noqa: BLE001
        return ProxiesStatus()


async def _probe_workers() -> WorkersStatus:
    """按 ``account.status`` 统计；不区分"是否真的 worker 子进程在跑"——那是 supervisor 的事。"""
    try:
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(Account.status, func.count(Account.id)).group_by(Account.status)
                )
            ).all()
        total = sum(int(c) for _, c in rows)
        by_status = {str(s): int(c) for s, c in rows}
        return WorkersStatus(total=total, by_status=by_status)
    except Exception:  # noqa: BLE001
        return WorkersStatus()


# ════════════════════════════════════════════════════════════
# 路由
# ════════════════════════════════════════════════════════════


@router.get("/health-overview", response_model=HealthOverview)
async def get_health_overview(_user: CurrentUser) -> HealthOverview:
    """聚合一次性返所有运维状态。各子探测并行 + 各自带 2s 超时。"""

    async def _safe(coro: Any, fallback: Any) -> Any:
        try:
            return await asyncio.wait_for(coro, timeout=2.0)
        except (TimeoutError, Exception):
            return fallback

    db_t = _safe(_probe_db(), DbStatus(ok=False, error="timeout/exception"))
    redis_t = _safe(_probe_redis(), RedisStatus(ok=False, error="timeout/exception"))
    providers_t = _safe(_probe_providers(), ProvidersStatus())
    proxies_t = _safe(_probe_proxies(), ProxiesStatus())
    workers_t = _safe(_probe_workers(), WorkersStatus())
    # alembic 探测是同步阻塞，扔到线程池跑
    alembic_t = _safe(asyncio.to_thread(_probe_alembic), AlembicStatus(ok=False, error="timeout"))

    db, alembic, redis_, providers, proxies, workers = await asyncio.gather(
        db_t, alembic_t, redis_t, providers_t, proxies_t, workers_t
    )
    return HealthOverview(
        db=db,
        alembic=alembic,
        redis=redis_,
        providers=providers,
        proxies=proxies,
        workers=workers,
    )


__all__ = ["router"]

===== backend/app/crypto.py =====
"""主密钥加解密工具。

所有敏感字段（session、api_id、api_hash、totp_secret）落库前必须经此加密。
丢失 MASTER_KEY 等于丢失所有 TG 账号 session，需要重新登录。
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .settings import settings

# 单例 Fernet 实例
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """惰性初始化，首次访问时读取 settings.master_key。"""
    global _fernet
    if _fernet is None:
        try:
            _fernet = Fernet(settings.master_key.encode())
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "MASTER_KEY 无效，请检查 .env：必须是 Fernet 生成的 32 字节 base64 密钥。"
            ) from exc
    return _fernet


def encrypt_str(plain: str) -> str:
    """加密字符串，返回 base64 字符串（DB 存 TEXT 即可）。"""
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_str(token: str) -> str:
    """解密字符串。失败抛 ValueError，由调用方决定如何处理。"""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("解密失败：可能 MASTER_KEY 已变更") from exc


def encrypt_bytes(plain: bytes) -> bytes:
    """加密字节流（用于存 Telethon ``StringSession.save()`` 序列化结果的 BYTEA）。"""
    return _get_fernet().encrypt(plain)


def decrypt_bytes(token: bytes) -> bytes:
    """解密字节流。"""
    try:
        return _get_fernet().decrypt(token)
    except InvalidToken as exc:
        raise ValueError("解密失败：可能 MASTER_KEY 已变更") from exc


def generate_master_key() -> str:
    """生成新的 Fernet 主密钥（仅用于初始化部署或测试）。"""
    return Fernet.generate_key().decode()

===== backend/app/db/__init__.py =====
"""DB 包初始化。"""
from .base import AsyncSessionLocal, Base, engine

__all__ = ["AsyncSessionLocal", "Base", "engine"]

===== backend/app/db/base.py =====
"""SQLAlchemy 异步引擎与基类。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from ..settings import settings


class Base(DeclarativeBase):
    """所有模型继承此基类。"""


# 全局异步引擎与 session factory
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

===== backend/app/db/models/__init__.py =====
"""所有 ORM 模型集中导出，便于 alembic autogenerate 与外部 import。"""

from .account import Account, HumanizeConfig, Proxy
from .command import AccountCommandLink, CommandTemplate, LLMProvider
from .feature import AccountFeature, Feature
from .ignored_peer import IgnoredPeer
from .log import AuditLog, RuntimeLog
from .plugin import PluginAvailable, PluginInstall, PluginRepo
from .rate_limit import RateLimitEvent, RateLimitOverride, RateLimitRule, RateLimitTemplate
from .rule import Rule
from .system import NotificationChannel, SystemSetting
from .user import WebUser

__all__ = [
    "Account",
    "AccountCommandLink",
    "AccountFeature",
    "AuditLog",
    "CommandTemplate",
    "Feature",
    "HumanizeConfig",
    "IgnoredPeer",
    "LLMProvider",
    "NotificationChannel",
    "PluginAvailable",
    "PluginInstall",
    "PluginRepo",
    "Proxy",
    "RateLimitEvent",
    "RateLimitOverride",
    "RateLimitRule",
    "RateLimitTemplate",
    "Rule",
    "RuntimeLog",
    "SystemSetting",
    "WebUser",
]

===== backend/app/db/models/account.py =====
"""TG 账号、出口代理、拟人化配置。"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class Proxy(Base):
    """出口代理（SOCKS5 / HTTPS / MTProxy）。"""

    __tablename__ = "proxy"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    host: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    # password 须用 master_key 加密
    password_enc: Mapped[str | None] = mapped_column(String, nullable=True)


class DeviceProfile(Base):
    """设备伪装库：一条 profile = 一组 (device_model, system_version, app_version, lang_code,
    system_lang_code)。被账号 ``device_profile_id`` 引用。

    is_default：全表只允许一条为 True，由 API 层在写入时维护（自动把其它行置 False）。
    新账号登录时如果调用方没指定 profile，就用 is_default 的那一条；都没有则回退到
    硬编码兜底（在 ``services.device_profile.resolve`` 里实现）。
    """

    __tablename__ = "device_profile"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    device_model: Mapped[str] = mapped_column(String(128), nullable=False)
    system_version: Mapped[str] = mapped_column(String(64), nullable=False)
    app_version: Mapped[str] = mapped_column(String(64), nullable=False)
    lang_code: Mapped[str] = mapped_column(String(16), nullable=False, default="zh")
    system_lang_code: Mapped[str] = mapped_column(
        String(16), nullable=False, default="zh-Hans"
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Account.status 枚举值（不用 PG ENUM，方便迁移）
ACCOUNT_STATUS_ACTIVE = "active"
ACCOUNT_STATUS_PAUSED = "paused"
ACCOUNT_STATUS_FLOODWAIT = "floodwait"
ACCOUNT_STATUS_DEAD = "dead"
ACCOUNT_STATUS_LOGIN_REQUIRED = "login_required"


class Account(Base):
    """一个 TG 账号 = 一个 session = 一个 worker 进程。"""

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # 来自 Telethon ``client.get_me()``：用户数字 ID 与 @username（不含 @）
    # 登录成功 / worker 启动连上 TG 时回填；旧账号在迁移后为空，重新登录后填上
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tg_username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # api_id / api_hash 全部加密落盘
    api_id_enc: Mapped[str] = mapped_column(String, nullable=False)
    api_hash_enc: Mapped[str] = mapped_column(String, nullable=False)
    # session 是 Telethon ``StringSession.save()`` 序列化后的字符串再编码为 bytes 后用主密钥加密
    session_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default=ACCOUNT_STATUS_LOGIN_REQUIRED, index=True)
    template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("rate_limit_template.id"), nullable=True
    )
    proxy_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("proxy.id"), nullable=True)
    # 设备伪装：决定 TG 设备列表里显示的 device_model / system_version / app_version；
    # 空 = 走系统默认 profile（device_profile.is_default = true）
    device_profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("device_profile.id", ondelete="SET NULL"), nullable=True
    )
    cold_start_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    humanize: Mapped[HumanizeConfig] = relationship(
        "HumanizeConfig", back_populates="account", uselist=False, cascade="all, delete-orphan"
    )


class HumanizeConfig(Base):
    """每账号一份的拟人化配置（PRD §L.3）。"""

    __tablename__ = "humanize_config"

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), primary_key=True
    )
    jitter_pct: Mapped[int] = mapped_column(SmallInteger, default=15)
    typing_simulate: Mapped[bool] = mapped_column(Boolean, default=True)
    typing_min_ms: Mapped[int] = mapped_column(Integer, default=1000)
    typing_max_ms: Mapped[int] = mapped_column(Integer, default=3000)
    typing_probability: Mapped[int] = mapped_column(SmallInteger, default=80)
    read_before_reply: Mapped[bool] = mapped_column(Boolean, default=True)
    active_window_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    active_window_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    cold_start_days: Mapped[int] = mapped_column(SmallInteger, default=7)

    account: Mapped[Account] = relationship("Account", back_populates="humanize")

===== backend/app/db/models/command.py =====
"""自定义命令模板与 LLM Provider 数据模型（Sprint2 #2）。

包含 3 张表：
- ``command_template``  全局模板库；每条记录 = 一个 ``,name`` 命令的"配方"
- ``account_command_link``  账号 × 模板 的多对多映射（仅勾选启用的才在 worker 生效）
- ``llm_provider``  AI 类命令调用的大模型供应商；``api_key_enc`` 落库前必须经
  ``app.crypto.encrypt_str`` 加密

设计要点：
- 模板与账号解耦：一份模板可被任意多个账号启用 / 禁用，互不影响；
- ``CommandTemplate.config`` 是 JSONB，按 ``type`` 字段约定结构（reply_text/forward_to/run_plugin/ai）；
- ``LLMProvider.api_key_enc`` 是 Fernet token；GET 接口禁返明文，只返 ``has_api_key:bool``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# ── 命令类型枚举 ────────────────────────────────────────────
COMMAND_TYPE_REPLY_TEXT = "reply_text"   # 收到 → 编辑原消息为指定文本
COMMAND_TYPE_FORWARD_TO = "forward_to"   # 收到 → 转发被引用消息到指定 chat_id
COMMAND_TYPE_RUN_PLUGIN = "run_plugin"   # 占位：调用某插件方法（V1 暂不实装）
COMMAND_TYPE_AI = "ai"                   # 收到 → 调 LLM → 编辑回原消息

ALL_COMMAND_TYPES = {
    COMMAND_TYPE_REPLY_TEXT,
    COMMAND_TYPE_FORWARD_TO,
    COMMAND_TYPE_RUN_PLUGIN,
    COMMAND_TYPE_AI,
}


# ── LLM Provider 厂商枚举 ──────────────────────────────────
LLM_PROVIDER_OPENAI = "openai"
LLM_PROVIDER_ANTHROPIC = "anthropic"
LLM_PROVIDER_OLLAMA = "ollama"

ALL_LLM_PROVIDERS = {
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_OLLAMA,
}


# ── API 格式枚举（独立于 provider 厂商；同一个 base_url 可能支持多种）─────
# - chat_completions    POST {base_url}/chat/completions   OpenAI 经典协议
# - responses           POST {base_url}/responses          OpenAI 2024 出的新协议
# - anthropic_messages  POST {base_url}/messages           Anthropic /v1/messages
LLM_API_FORMAT_CHAT_COMPLETIONS = "chat_completions"
LLM_API_FORMAT_RESPONSES = "responses"
LLM_API_FORMAT_ANTHROPIC_MESSAGES = "anthropic_messages"

ALL_LLM_API_FORMATS = {
    LLM_API_FORMAT_CHAT_COMPLETIONS,
    LLM_API_FORMAT_RESPONSES,
    LLM_API_FORMAT_ANTHROPIC_MESSAGES,
}


def default_api_format_for(provider_kind: str) -> str:
    """给定 provider 厂商，返回默认 API 格式。

    用于：alembic 迁移 0009 自动回填 + 创建 provider 时缺省值。
    """
    if (provider_kind or "").lower() == LLM_PROVIDER_ANTHROPIC:
        return LLM_API_FORMAT_ANTHROPIC_MESSAGES
    return LLM_API_FORMAT_CHAT_COMPLETIONS


# ── LLM 模态枚举（路由必备）──────────────────────────────
# - text         纯文本 LLM（最常见，GPT/Claude/GLM/Mimo 文本端点都属于这类）
# - vision       视觉多模态：能识图（图文输入 + 文本输出，如 GPT-4V/Claude Vision）
# - audio        音频多模态：能听语音 / 转写（如 Whisper / GPT-4o realtime audio）
# - multimodal   全模态：同时支持图、音、视频等多输入（GPT-4o / Gemini-Pro 类）
LLM_MODALITY_TEXT = "text"
LLM_MODALITY_VISION = "vision"
LLM_MODALITY_AUDIO = "audio"
LLM_MODALITY_MULTIMODAL = "multimodal"

ALL_LLM_MODALITIES = {
    LLM_MODALITY_TEXT,
    LLM_MODALITY_VISION,
    LLM_MODALITY_AUDIO,
    LLM_MODALITY_MULTIMODAL,
}


# ── 路由标签字典（前端做多选 chip，后端只校验集合） ────────
# 任何一条 LLMProvider.tags 是这些值的子集；路由器据 tag 选 provider。
# 标签维度按"擅长领域 / 上下文容量 / 速度档"三类组织。
LLM_TAG_CHAT = "chat"               # 通用闲聊 / 短问短答
LLM_TAG_CODE = "code"               # 代码生成 / 解释 / 调试
LLM_TAG_MATH = "math"               # 数学推导 / 计算
LLM_TAG_TRANSLATE = "translate"     # 多语种翻译
LLM_TAG_VISION = "vision"           # 看图说话 / 图像理解（与 modality 配合）
LLM_TAG_LONG_CONTEXT = "long_context"  # 大上下文（≥ 64K token）
LLM_TAG_REASON = "reason"           # 复杂推理 / 多步分析（旗舰模型）
LLM_TAG_SMART = "smart"             # 同 reason，强调"答主力"
LLM_TAG_CHEAP = "cheap"             # 量大优先（成本档 1）
LLM_TAG_FAST = "fast"               # 低延迟优先
LLM_TAG_CLASSIFY = "classify"       # 适合作"路由分类器"的轻量小模型

ALL_LLM_TAGS = {
    LLM_TAG_CHAT,
    LLM_TAG_CODE,
    LLM_TAG_MATH,
    LLM_TAG_TRANSLATE,
    LLM_TAG_VISION,
    LLM_TAG_LONG_CONTEXT,
    LLM_TAG_REASON,
    LLM_TAG_SMART,
    LLM_TAG_CHEAP,
    LLM_TAG_FAST,
    LLM_TAG_CLASSIFY,
}


class CommandTemplate(Base):
    """全局命令模板。

    ``name`` 是 ``,name`` 触发名，全表唯一；用户可在系统设置里 CRUD。
    每个账号通过 ``AccountCommandLink`` 选择是否启用某条模板。
    """

    __tablename__ = "command_template"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # ``,name`` 触发名；保持简洁仅允许 [a-zA-Z0-9_]
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 取值见上方常量；schema 层做合法性校验
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 按 type 决定结构；统一存 JSON
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AccountCommandLink(Base):
    """[账号 × 命令模板] 启用关系表。

    联合主键；``enabled=False`` 表示曾经启用过但已关闭（保留记录便于 UI 显示历史）。
    实际派发时只看 ``enabled=True`` 的行。
    """

    __tablename__ = "account_command_link"

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), primary_key=True
    )
    template_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("command_template.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class LLMProvider(Base):
    """LLM 供应商配置（AI 命令调用入口）。

    ``api_key_enc`` 是 Fernet 加密后的 base64 字符串（见 ``app/crypto.py``）；
    任何 GET 接口都不得返回明文，只返 ``has_api_key:bool``。

    路由相关字段（见 ``services.llm_router``）：
    - ``modality``  能力模态（text / vision / audio / multimodal）
    - ``tags``      路由标签数组；路由器根据用户消息特征匹配 tag 选 provider
    - ``cost_tier`` 1=便宜（量大优先）/ 2=中 / 3=旗舰（质量优先）
    - ``notes``     运维备注（不影响路由）
    """

    __tablename__ = "llm_provider"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 友好名称（前端展示用），全表唯一
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 厂商类型：openai / anthropic / ollama
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # Fernet 加密 token；可空（如 ollama 本地部署可不填）
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 自定义 base_url；OpenAI 兼容代理 / 自托管 Ollama 都靠它
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 默认模型 ID（命令 config 里允许覆盖单条调用的 model）
    default_model: Mapped[str] = mapped_column(String(64), nullable=False)
    # API 协议：chat_completions / responses / anthropic_messages；和 provider 厂商解耦
    # 因为同一个反代 base_url 可能只支持其中某种（典型例子：anyrouter 只接 /responses）
    api_format: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=LLM_API_FORMAT_CHAT_COMPLETIONS,
    )
    # 该 provider 下"已启用 + 自定义"的模型清单。
    # JSON 数组；每条形如：
    #   {"id": "gpt-5.5", "enabled": true, "custom": false, "label": null}
    # - ``id``       OpenAI / Anthropic 的模型 ID（fetch /v1/models 拿来 / 用户填的）
    # - ``enabled``  下游"自定义命令 ai 子表单"里是否会出现这条（ON 时会展开成
    #                ``Provider 名（提供商 · model_id）`` 一条候选）
    # - ``custom``   true = 用户手动添加；false = 从 GET /v1/models 拉的
    # - ``label``    可选展示名（默认就用 id）
    models: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    # ── 路由元数据 ────────────────────────────────
    # 模态：text/vision/audio/multimodal；默认 text
    modality: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=LLM_MODALITY_TEXT
    )
    # 标签数组；JSON list[str]；默认空列表
    tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    # 成本档：1=cheap / 2=mid / 3=premium；默认 2
    cost_tier: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="2"
    )
    # 运维备注（仅给自己看；路由不读）
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 出口代理（指向 proxy 表）；NULL = 直连（DIRECT），即不走任何代理。
    # mtproxy 类型仅给 Telegram 用，HTTP 客户端不支持——schema 层会拒绝。
    proxy_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("proxy.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

===== backend/app/db/models/feature.py =====
"""功能（feature/plugin）与账号-功能关联。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# 内置功能 key（同时 feature.is_builtin = True）
FEATURE_AUTO_REPLY = "auto_reply"
FEATURE_FORWARD = "forward"
FEATURE_GROUP_ADMIN = "group_admin"
FEATURE_SCHEDULER = "scheduler"
FEATURE_MONITOR = "monitor"

BUILTIN_FEATURES: dict[str, str] = {
    FEATURE_AUTO_REPLY: "自动回复",
    FEATURE_FORWARD: "消息转发",
    FEATURE_GROUP_ADMIN: "群组管理",
    FEATURE_SCHEDULER: "定时任务",
    FEATURE_MONITOR: "消息监控",
}


# AccountFeature.state
FEATURE_STATE_ACTIVE = "active"
FEATURE_STATE_FAILED = "failed"
FEATURE_STATE_DISABLED = "disabled"


class Feature(Base):
    """功能 / 插件登记表。第三方插件通过 plugin_repo 同步后写入。"""

    __tablename__ = "feature"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AccountFeature(Base):
    """[账号 × 功能] 矩阵的某个格子。"""

    __tablename__ = "account_feature"

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), primary_key=True
    )
    feature_key: Mapped[str] = mapped_column(
        String, ForeignKey("feature.key"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String, default=FEATURE_STATE_DISABLED)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

===== backend/app/db/models/ignored_peer.py =====
"""被账号忽略的 Telegram peer（私聊 / 群 / 频道）。

每个账号维护一份"忽略名单"。worker 收到来自这些 peer 的 incoming 消息时
会直接短路所有插件分发——不消耗任何风控配额、也不触发 auto_reply 等回调。

设计说明：
- ``peer_id`` 是 Telethon 的 ``event.chat_id``，可正可负（supergroup 形如 -100xxxxxxxxxx，
  超出 32 位整型范围），所以这里用 ``BigInteger``。
- ``peer_kind`` 为字符串枚举（``private`` / ``group`` / ``supergroup`` / ``channel``），
  仅作展示用途，业务逻辑只看 ``peer_id``。
- ``peer_label`` 为加入忽略名单时的群名/用户名快照；后续群名变更不会自动同步。
- ``UniqueConstraint(account_id, peer_id)`` 保证同一账号下不会重复加入同一 peer。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# peer_kind 取值枚举（仅用于展示）
PEER_KIND_PRIVATE = "private"
PEER_KIND_GROUP = "group"
PEER_KIND_SUPERGROUP = "supergroup"
PEER_KIND_CHANNEL = "channel"

PEER_KINDS = (PEER_KIND_PRIVATE, PEER_KIND_GROUP, PEER_KIND_SUPERGROUP, PEER_KIND_CHANNEL)


class IgnoredPeer(Base):
    """[账号 × peer] 忽略名单的一行。

    详见模块 docstring。
    """

    __tablename__ = "ignored_peer"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Telethon chat_id：私聊 = 用户 id（正数）；群/超级群/频道 = 负数
    peer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    peer_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # 群名/用户名快照；为空表示加入时未拿到（如 worker 离线场景的手填 ID）
    peer_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("account_id", "peer_id", name="uq_ignored_peer_account_peer"),
    )

===== backend/app/db/models/log.py =====
"""操作日志（Web 端动作）与运行日志（worker 输出）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# RuntimeLog level
LEVEL_DEBUG = "debug"
LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"


class AuditLog(Base):
    """Web 端操作日志，由依赖中间件写入。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("web_user.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class RuntimeLog(Base):
    """worker 运行时日志，由主进程从 IPC 收到后批量落库。"""

    __tablename__ = "runtime_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), nullable=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    level: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_runtime_log_account_ts", "account_id", "ts"),
        Index("ix_runtime_log_account_level_ts", "account_id", "level", "ts"),
    )

===== backend/app/db/models/plugin.py =====
"""插件市场：源 + 可用插件清单 + 已安装记录。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class PluginRepo(Base):
    """插件源：apt 风格 URL，定期同步。"""

    __tablename__ = "plugin_repo"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PluginAvailable(Base):
    """从 repo 同步来的插件清单。"""

    __tablename__ = "plugin_available"

    repo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("plugin_repo.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


# ─────────────────────────────────────────────────────
# 阶段 B：第三方插件安装记录
# ─────────────────────────────────────────────────────
# 来源枚举（写入 ``PluginInstall.source``）
PLUGIN_SOURCE_BUILTIN = "builtin"
PLUGIN_SOURCE_ZIP = "zip"
PLUGIN_SOURCE_REPO = "repo"


class PluginInstall(Base):
    """已安装的第三方插件记录（builtin 不必入此表，但允许）。

    一行 = 一个 ``key`` 的安装；安装目录 ``installed_path`` 是 worker 加载时实际读盘的位置。
    ``signature_ok`` 三态：
      - ``True``：附带的 ``.sig`` 文件用配置的公钥验签通过
      - ``False``：附带签名但验签失败 → 必须管理员手动 enable 才会启用
      - ``None``：未提供 ``.sig`` 文件（前端展示警告，但允许启用）
    """

    __tablename__ = "plugin_install"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # builtin/zip/repo
    version: Mapped[str] = mapped_column(String, nullable=False, default="0.0.0")
    # manifest 快照：解析 zip 内 manifest.py 后写入；前端列表展示用
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    signature_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # 解压后存放路径（一般在 ``data/plugins/installed/<key>``）
    installed_path: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 阶段 C：来源仓库（zip 直传时为空；从 repo 安装时引用 plugin_repo.id）
    repo_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("plugin_repo.id", ondelete="SET NULL"), nullable=True
    )
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


__all__ = [
    "PLUGIN_SOURCE_BUILTIN",
    "PLUGIN_SOURCE_REPO",
    "PLUGIN_SOURCE_ZIP",
    "PluginAvailable",
    "PluginInstall",
    "PluginRepo",
]

===== backend/app/db/models/rate_limit.py =====
"""风控相关：模板 / 规则 / 事件 / 临时覆盖。

PRD §L 完整实现。三层叠加由 service 层做，DB 只负责持久化。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

# RateLimitRule.scope
SCOPE_TEMPLATE = "template"
SCOPE_ACCOUNT = "account"
SCOPE_RULE = "rule"

# 抑制策略
POLICY_DROP = "drop"
POLICY_QUEUE = "queue"
POLICY_BACKOFF = "backoff"
POLICY_PAUSE = "pause"
POLICY_NOTIFY = "notify"

# 事件 outcome
OUTCOME_OK = "ok"
OUTCOME_DROP = "drop"
OUTCOME_QUEUED = "queued"
OUTCOME_BACKOFF = "backoff"
OUTCOME_PAUSE = "pause"
OUTCOME_FLOODWAIT = "floodwait"
OUTCOME_PEERFLOOD = "peerflood"
OUTCOME_SLOWMODE = "slowmode"


# 所有可配置动作（PRD §L.1）
ACTION_KEYS: tuple[str, ...] = (
    "send_message_private",
    "send_message_group",
    "same_peer_send",
    "edit_message",
    "delete_message",
    "forward_message",
    "callback_query",
    "read_history",
    "join_chat",
    "leave_chat",
    "create_chat",
    "invite_user",
    "dm_stranger",
    "update_profile",
    "upload_file",
    "download_file",
    "search",
    "api_total",
)


class RateLimitTemplate(Base):
    """风控模板：可应用到多个账号作为默认。"""

    __tablename__ = "rate_limit_template"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateLimitRule(Base):
    """单条动作的限速配置，支持模板 / 账号 / 规则三种作用域。"""

    __tablename__ = "rate_limit_rule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)

    per_second: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    same_peer_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)

    policy: Mapped[str] = mapped_column(String, nullable=False, default=POLICY_QUEUE)
    backoff_base_seconds: Mapped[int] = mapped_column(Integer, default=5)
    backoff_max_seconds: Mapped[int] = mapped_column(Integer, default=1800)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (UniqueConstraint("scope", "scope_id", "action", name="uq_rl_scope_action"),)


class RateLimitEvent(Base):
    """限速事件流（仪表盘 + 24h 事件流来源）。"""

    __tablename__ = "rate_limit_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    action: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_rl_event_account_ts", "account_id", "ts"),
        Index("ix_rl_event_account_action_ts", "account_id", "action", "ts"),
    )


class RateLimitOverride(Base):
    """临时阈值衰减（FloodWait 触发的 ×0.7 等），TTL 到期由后台清理。"""

    __tablename__ = "rate_limit_override"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    multiplier: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_rl_override_account_expires", "account_id", "expires_at"),)

===== backend/app/db/models/rule.py =====
"""规则表：从属于 [账号 × 功能]。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class Rule(Base):
    """统一规则表，按 feature_key 区分语义（关键词/cron/源-目标等）。"""

    __tablename__ = "rule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    feature_key: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_rule_account_feature_enabled", "account_id", "feature_key", "enabled"),)

===== backend/app/db/models/system.py =====
"""系统级配置 + 通知通道。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class SystemSetting(Base):
    """key-value 系统配置（command_prefix、kill_switch、global_api_qps 等）。"""

    __tablename__ = "system_setting"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotificationChannel(Base):
    """通知通道：email / webhook / tg_self（自发到收藏夹）。"""

    __tablename__ = "notification_channel"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    # config 内敏感字段需在写入前由 service 层加密
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

===== backend/app/db/models/user.py =====
"""Web 端用户表（单用户系统，但仍用表保存用户名/密码哈希/TOTP）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WebUser(Base):
    """Web 后台登录用户。系统单用户，但表结构保留扩展可能。"""

    __tablename__ = "web_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    # TOTP secret 用 master_key 加密后存（为空表示未启用 2FA）
    totp_secret_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

===== backend/app/db/session.py =====
"""DB session 依赖。"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from .base import AsyncSessionLocal


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个事务作用域的 session。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

===== backend/app/deps.py =====
"""FastAPI 通用依赖：DB、当前登录用户、操作日志写入。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models.user import WebUser
from .db.session import get_db


# ── 当前用户：由 services.auth_service 实现 decode_token，这里解耦避免循环依赖 ──
def _decode_token(token: str) -> int | None:
    """惰性 import 避免循环引用。返回 user_id 或 None。"""
    from .services.auth_service import decode_jwt_token

    return decode_jwt_token(token)


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    auth_token: Annotated[str | None, Cookie(alias="auth_token")] = None,
) -> WebUser:
    """从 HttpOnly cookie 读取 JWT，返回当前 WebUser。"""
    if not auth_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    user_id = _decode_token(auth_token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期")
    user = (await db.execute(select(WebUser).where(WebUser.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user


CurrentUser = Annotated[WebUser, Depends(get_current_user)]
DBSession = Annotated[AsyncSession, Depends(get_db)]

===== backend/app/main.py =====
"""FastAPI 入口：注册 router、CORS、全局异常 handler、lifespan。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import accounts as accounts_api
from .api import auth as auth_api
from .api import device_profiles as device_profiles_api
from .api import logs as logs_api
from .api import network as network_api
from .api import proxies as proxies_api
from .api import rate_limit as rate_limit_api
from .services.login_service import cleanup_expired_loop
from .settings import settings

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))


def _run_alembic_upgrade() -> None:
    """同步调 ``alembic upgrade head``。

    在 lifespan 启动钩子里以 ``asyncio.to_thread`` 调，避免阻塞 event loop。
    alembic 用的是同步 driver（settings.database_url_sync），跟 alembic CLI 走同一条路径
    （env.py），所以在 process 内调和命令行调结果一致。

    任何失败只 log，不抛——上面注释里有"失败不阻止启动"的设计理由。
    """
    try:
        # 局部 import：alembic 是 dev 路径常驻依赖，但 import 时会扫脚本目录，放函数内更轻
        from pathlib import Path

        from alembic.config import Config

        from alembic import command

        # alembic.ini 在 backend/ 根目录；以本文件所在目录的上一级定位，避免 cwd 漂移
        ini_path = Path(__file__).resolve().parents[1] / "alembic.ini"
        if not ini_path.exists():
            logging.warning("alembic.ini 不存在：%s；跳过启动期自动迁移", ini_path)
            return
        cfg = Config(str(ini_path))
        # alembic env.py 自己会读 settings.database_url_sync，不在这里传 -x url
        command.upgrade(cfg, "head")
        logging.info("alembic upgrade head 完成（启动期自动迁移）")
    except Exception:  # noqa: BLE001
        # 不打 exc_info=True 时也带 traceback；这里需要明显 → 用 ERROR
        logging.exception(
            "alembic 启动期自动迁移失败；服务仍会继续启动，请尽快手动 `make migrate` 排查"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动 supervisor + login 清理任务，退出时优雅关停。"""
    # 0) 启动期自动 alembic upgrade head
    #    解决"代码加了新字段、DB 还没跑迁移 → 前端列表 500"那类问题。
    #    失败不阻止启动（用户激进策略：让 service 启起来好排查 + /api/system/health-overview
    #    能看到 alembic.in_sync=False 的明确信号）；只在日志里 ERROR 醒目提示。
    if settings.auto_migrate_on_startup:
        await asyncio.to_thread(_run_alembic_upgrade)

    # 1) 启动登录会话清理后台任务（每 60s 扫一次）
    cleanup_task = asyncio.create_task(cleanup_expired_loop())

    # 2) 拉起 worker supervisor（B Agent 提供）；若尚未实现则跳过
    stop_all_workers = None
    try:
        from .worker.supervisor import start_supervisor
        from .worker.supervisor import stop_all_workers as _stop_all
    except ImportError:
        logging.warning("worker.supervisor 尚未实现，本进程不会拉起 worker 子进程")
    else:
        try:
            await start_supervisor()
            stop_all_workers = _stop_all
        except Exception as exc:  # noqa: BLE001
            logging.exception("启动 worker supervisor 失败：%s", exc)

    try:
        yield
    finally:
        # 3) 退出：取消清理任务 + 关停所有 worker
        cleanup_task.cancel()
        try:
            await cleanup_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        if stop_all_workers is not None:
            try:
                await stop_all_workers()
            except Exception:  # noqa: BLE001
                logging.exception("stop_all_workers 失败")


app = FastAPI(title="Telegram Userbot 管理系统", version=__version__, lifespan=lifespan)


# ── CORS ──────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 全局异常 handler：把 HTTPException 的结构化 detail 转成 {"error":...} ──
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return JSONResponse(status_code=exc.status_code, content={"error": detail})
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "HTTP", "message": str(detail)}},
    )


@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    logging.exception("未处理异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL", "message": "服务器内部错误"}},
    )


# ── Router ────────────────────────────────────────────────────────
app.include_router(auth_api.router)
app.include_router(accounts_api.router)
app.include_router(rate_limit_api.router)   # C Agent：风控 + 拟人化 + 全局总闸
app.include_router(logs_api.router)         # 主会话补：审计日志 + 运行日志
app.include_router(proxies_api.router)      # 主会话补：代理 CRUD + 连通性测试
app.include_router(device_profiles_api.router)  # 设备伪装库：device_model / app_version / lang_code
app.include_router(network_api.router)      # 主会话补：当前网络环境探测


# ── 健康检查 ─────────────────────────────────────────────────────
@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    """liveness：进程是否还在跑（不查依赖）。"""
    return {"ok": True}


@app.get("/readyz")
async def readyz() -> dict:
    """readiness：依赖是否健康（DB + Redis 实际 ping）。

    任一依赖不健康都返回 503，便于反代/编排系统据此把流量摘走。
    DB 与 Redis ping **并行执行**，各自 2s 超时；最坏耗时 ~2s 而非串行的 4s
    （后者会踩 docker compose healthcheck timeout: 5s 的边缘）。
    """
    import asyncio as _asyncio

    from sqlalchemy import text as _text

    from .db.base import AsyncSessionLocal
    from .redis_client import get_redis

    async def _db_ping() -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(_text("SELECT 1"))

    async def _redis_ping() -> None:
        r = get_redis()
        pong = await r.ping()
        if not pong:
            raise RuntimeError("redis PING returned falsy")

    # 并行：两个探测同时跑，各自带 2s 超时
    db_task = _asyncio.wait_for(_db_ping(), timeout=2.0)
    redis_task = _asyncio.wait_for(_redis_ping(), timeout=2.0)
    db_res, redis_res = await _asyncio.gather(db_task, redis_task, return_exceptions=True)

    checks: dict[str, dict] = {}
    overall_ok = True

    if isinstance(db_res, BaseException):
        checks["db"] = {"ok": False, "error": str(db_res)[:200]}
        overall_ok = False
    else:
        checks["db"] = {"ok": True}

    if isinstance(redis_res, BaseException):
        checks["redis"] = {"ok": False, "error": str(redis_res)[:200]}
        overall_ok = False
    else:
        checks["redis"] = {"ok": True}

    body = {"ok": overall_ok, "checks": checks}
    if not overall_ok:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content=body)
    return body


# === 以下 router 由其他 Agent 追加 ===

# Agent D：功能矩阵 / 规则 / 插件市场
from .api import features as features_api  # noqa: E402
from .api import plugins as plugins_api  # noqa: E402
from .api import plugins_install as plugins_install_api  # noqa: E402
from .api import rules as rules_api  # noqa: E402

app.include_router(features_api.router)
app.include_router(rules_api.router)
app.include_router(plugins_api.router)
# Sprint2 #4：第三方插件 zip 上传 / 启停 / 卸载
app.include_router(plugins_install_api.router)

# Sprint2 #3 Ignored Peers
from .api import ignored_peers as ignored_peers_api  # noqa: E402

app.include_router(ignored_peers_api.router)

# Sprint2 #2 Custom Commands（命令模板 + LLM provider）
from .api import commands as commands_api  # noqa: E402

app.include_router(commands_api.router)

# 系统健康概览（DB / alembic / redis / providers / proxies / workers）
from .api import system_health as system_health_api  # noqa: E402

app.include_router(system_health_api.router)

===== backend/app/redis_client.py =====
"""Redis 异步客户端封装。"""

from __future__ import annotations

import redis.asyncio as redis_async

from .settings import settings

# 全局共享实例（按 use case 复用连接池）
_pool: redis_async.ConnectionPool | None = None


def get_pool() -> redis_async.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis_async.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=64,
        )
    return _pool


def get_redis() -> redis_async.Redis:
    """每次返回一个 Redis 客户端（共享 pool）。"""
    return redis_async.Redis(connection_pool=get_pool())


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.disconnect()
        _pool = None

===== backend/app/schemas/__init__.py =====
"""Pydantic v2 schemas 集中导出。"""

===== backend/app/schemas/account.py =====
"""账号相关 schema。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class AccountStartLoginRequest(BaseModel):
    """绑定向导第 1 步：录入 API 凭据 + 手机号。"""
    api_id: int
    api_hash: str
    phone: str
    proxy_id: int | None = None
    # 设备伪装：影响 TG 设备列表里看到的 device_model / system_version / app_version；
    # 不传 = 用系统默认 profile
    device_profile_id: int | None = None


class AccountStartLoginResponse(BaseModel):
    """返回临时 login_token，后续步骤须带此 token。"""
    login_token: str
    phone_code_hash: str | None = None


class AccountConfirmCodeRequest(BaseModel):
    login_token: str
    code: str


class AccountConfirm2FARequest(BaseModel):
    login_token: str
    password: str


class AccountConfirmResponse(BaseModel):
    """登录成功返回创建好的账号 ID。"""
    account_id: int
    require_2fa: bool = False
    display_name: str | None = None


class AccountUpdateRequest(BaseModel):
    display_name: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    template_id: int | None = None
    proxy_id: int | None = None
    # 改 device_profile_id 不会影响**现有 session**：TG 端显示的设备名绑在 auth_key 上。
    # 想生效必须重新登录走 wizard。
    device_profile_id: int | None = None


class AccountSummary(BaseModel):
    id: int
    phone: str
    display_name: str | None
    # Telegram 身份信息（client.get_me() 回填，可空）
    tg_user_id: int | None = None
    tg_username: str | None = None
    status: str
    tags: list[str] | None = None
    enabled_features: int = 0
    cold_start_until: date | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AccountDetail(AccountSummary):
    notes: str | None = None
    template_id: int | None = None
    proxy_id: int | None = None
    device_profile_id: int | None = None


class AccountCloneConfigRequest(BaseModel):
    from_account_id: int
    features: list[str] = Field(default_factory=list)

===== backend/app/schemas/auth.py =====
"""认证相关 schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: str | None = Field(None, description="启用 TOTP 后必填")


class LoginResponse(BaseModel):
    ok: bool
    require_totp: bool = False


class RegisterRequest(BaseModel):
    """首次部署时创建超管账号的接口入参（系统已存在用户后该接口禁用）。"""
    username: str
    password: str


class TotpEnableResponse(BaseModel):
    secret: str
    otpauth_url: str


class TotpVerifyRequest(BaseModel):
    code: str


class CurrentUser(BaseModel):
    id: int
    username: str
    has_totp: bool


class ChangePasswordRequest(BaseModel):
    """修改当前用户密码：必须提供旧密码做二次校验。"""

    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256, description="≥ 8 位")


class TotpDisableRequest(BaseModel):
    """禁用 TOTP：要求当前 TOTP 码做最后一次校验，避免 cookie 被偷后被静默关掉。"""

    code: str = Field(min_length=6, max_length=8)

===== backend/app/schemas/command.py =====
"""自定义命令 + LLM Provider Pydantic schema（Sprint2 #2）。

字段约定：
- ``CommandTemplate.config`` 按 ``type`` 决定结构，schema 层做基础类型校验
- ``LLMProviderOut`` 永远不包含明文 ``api_key``；仅返回 ``has_api_key:bool``
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..db.models.command import (
    ALL_COMMAND_TYPES,
    ALL_LLM_MODALITIES,
    ALL_LLM_PROVIDERS,
    ALL_LLM_TAGS,
    COMMAND_TYPE_AI,
    COMMAND_TYPE_FORWARD_TO,
    COMMAND_TYPE_REPLY_TEXT,
    COMMAND_TYPE_RUN_PLUGIN,
    LLM_API_FORMAT_CHAT_COMPLETIONS,
    LLM_MODALITY_TEXT,
)

# ── 命令名校验正则：与 worker/command.py 中的 \w+ 派发兼容 ─────
_COMMAND_NAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,64}$")


# ════════════════════════════════════════════════════════════
# CommandTemplate
# ════════════════════════════════════════════════════════════


class CommandTemplateBase(BaseModel):
    """模板公共字段。"""

    name: str = Field(min_length=1, max_length=64)
    type: Literal["reply_text", "forward_to", "run_plugin", "ai"]
    config: dict[str, Any] = Field(default_factory=dict)
    description: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        # 命令名只允许 [a-zA-Z0-9_]，与派发正则 \w+ 对齐
        if not _COMMAND_NAME_RE.match(v):
            raise ValueError("命令名只能包含字母 / 数字 / 下划线，1-64 字符")
        return v

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        # 双保险：防 enum 绕过
        if v not in ALL_COMMAND_TYPES:
            raise ValueError(f"未知命令类型：{v}")
        return v

    @field_validator("config")
    @classmethod
    def _check_config_shape(cls, v: dict[str, Any], info) -> dict[str, Any]:
        """按 type 做基础结构校验，避免 worker 拿到不完整 config 才崩。

        ``info.data`` 在 v2 下可拿到同一对象上其他已校验字段。
        """
        t = info.data.get("type")
        if t == COMMAND_TYPE_REPLY_TEXT:
            # text 必须存在；允许空串（用户可能只想清空原消息）
            if "text" not in v or not isinstance(v["text"], str):
                raise ValueError("reply_text 类型必须配置 text:str")
        elif t == COMMAND_TYPE_FORWARD_TO:
            # 允许 int 或可被解析为 int 的字符串（前端 number input 提交时一般是 int）
            tgt = v.get("target_chat_id")
            if tgt is None:
                raise ValueError("forward_to 类型必须配置 target_chat_id:int")
            try:
                int(tgt)
            except (TypeError, ValueError) as exc:
                raise ValueError("target_chat_id 必须是整数") from exc
        elif t == COMMAND_TYPE_RUN_PLUGIN:
            if not v.get("plugin_key"):
                raise ValueError("run_plugin 类型必须配置 plugin_key")
        elif t == COMMAND_TYPE_AI:
            if not v.get("provider_id"):
                raise ValueError("ai 类型必须配置 provider_id（在系统设置 → LLM Provider 里建）")
            # 路由模式：fixed（默认）/ auto；其它值拒绝
            rm = v.get("routing_mode", "fixed")
            if rm not in ("fixed", "auto"):
                raise ValueError("routing_mode 只能是 'fixed' 或 'auto'")
            # auto 模式额外字段：fallback_provider_id 必须是正整数（缺省 = 用 provider_id 自身）
            for fld in ("routing_fallback_provider_id", "classifier_provider_id"):
                fv = v.get(fld)
                if fv is None:
                    continue
                try:
                    fvi = int(fv)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{fld} 必须是 LLM Provider 的整数 id") from exc
                if fvi <= 0:
                    raise ValueError(f"{fld} 必须是正整数")
                v[fld] = fvi
            # 输出格式 / 模板（全可选；默认走 HTML 预设）
            # 兼容老数据：'markdownv2' 不再支持（telethon 1.36 不识别），自动归一到 'html'
            of_raw = v.get("output_format", "html")
            of = "html" if of_raw == "markdownv2" else of_raw
            if of not in ("html", "markdown", "plain"):
                raise ValueError("output_format 只能是 'html' / 'markdown' / 'plain'")
            v["output_format"] = of  # 把归一后的写回（避免老 cfg 永远带着 markdownv2）
            tpl = v.get("output_template")
            if tpl is not None:
                if not isinstance(tpl, str):
                    raise ValueError("output_template 必须是字符串")
                if len(tpl) > 4000:
                    raise ValueError("output_template 长度不能超过 4000 字符")
            ev = v.get("escape_values", True)
            if not isinstance(ev, bool):
                raise ValueError("escape_values 必须是布尔值")
        return v


class CommandTemplateCreate(CommandTemplateBase):
    """新建模板入参。"""


class CommandTemplateUpdate(BaseModel):
    """PATCH 更新；所有字段可选。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    type: Literal["reply_text", "forward_to", "run_plugin", "ai"] | None = None
    config: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _COMMAND_NAME_RE.match(v):
            raise ValueError("命令名只能包含字母 / 数字 / 下划线，1-64 字符")
        return v


class CommandTemplateOut(CommandTemplateBase):
    """模板出参，比 base 多 id/created_at。"""

    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ════════════════════════════════════════════════════════════
# 账号 × 模板 关联
# ════════════════════════════════════════════════════════════


class AccountCommandItem(BaseModel):
    """账号详情 → 命令 tab 一行：模板内容 + 该账号是否启用。"""

    template: CommandTemplateOut
    enabled: bool

    model_config = ConfigDict(from_attributes=True)


# ════════════════════════════════════════════════════════════
# LLM Provider
# ════════════════════════════════════════════════════════════


class ProviderModel(BaseModel):
    """LLMProvider 下挂的一个候选模型。

    - ``id``       模型 ID（如 ``gpt-5.5`` / ``claude-haiku-4-5``）
    - ``enabled``  下游"自定义命令 ai 子表单"展开式 select 里是否会出现这条
    - ``custom``   true = 用户手动添加；false = 从 fetch /v1/models 拉的
    - ``label``    可选展示名（默认就用 id）
    """

    id: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    custom: bool = False
    label: str | None = Field(default=None, max_length=128)

    @field_validator("id")
    @classmethod
    def _strip(cls, v: str) -> str:
        v2 = v.strip()
        if not v2:
            raise ValueError("model id 不能为空")
        return v2


class LLMProviderCreate(BaseModel):
    """新建 LLM provider 入参；``api_key`` 可空（如本地 Ollama）。"""

    name: str = Field(min_length=1, max_length=64)
    provider: Literal["openai", "anthropic", "ollama"]
    api_key: str | None = Field(default=None, max_length=512)
    base_url: str | None = Field(default=None, max_length=255)
    default_model: str = Field(min_length=1, max_length=64)

    api_format: Literal["chat_completions", "responses", "anthropic_messages"] = (
        LLM_API_FORMAT_CHAT_COMPLETIONS
    )
    """API 协议；和 provider 厂商解耦——同一个反代 base_url 可能只支持其中某种。"""

    # ── 路由元数据（全可选；不填走默认）───────────────────────
    modality: Literal["text", "vision", "audio", "multimodal"] = Field(
        default=LLM_MODALITY_TEXT
    )
    """能力模态。决定该 provider 是否会被视觉路由命中。"""

    tags: list[str] = Field(default_factory=list, max_length=20)
    """路由标签。前端用 chips 编辑；空列表 = 不参与"按 tag 分类"路由（可作纯 fallback）。"""

    cost_tier: int = Field(default=2, ge=1, le=3)
    """1=便宜（量大走它）/ 2=中 / 3=旗舰；路由器据此在同 tag 里挑。"""

    notes: str | None = Field(default=None, max_length=500)
    """运维备注；路由不读。"""

    proxy_id: int | None = Field(default=None, ge=1)
    """出口代理 id（指向 proxy 表）；None = 直连（DIRECT）。mtproxy 类型的 proxy 不能给
    LLM 调用用——HTTP 客户端不支持 MTProto；service 层在校验时拒绝。"""

    models: list[ProviderModel] = Field(default_factory=list, max_length=200)
    """该 provider 下挂的候选模型清单。新建时通常留空；建完 provider 后用前端的
    ``Fetch 模型列表`` 按钮自动拉取，再 toggle 启用要用的几个。"""

    @field_validator("provider")
    @classmethod
    def _check_provider(cls, v: str) -> str:
        if v not in ALL_LLM_PROVIDERS:
            raise ValueError(f"未知 provider：{v}")
        return v

    @field_validator("modality")
    @classmethod
    def _check_modality(cls, v: str) -> str:
        if v not in ALL_LLM_MODALITIES:
            raise ValueError(f"未知 modality：{v}")
        return v

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, v: list[str]) -> list[str]:
        # 大小写不敏感 + 去重；非法标签拒绝
        normalized: list[str] = []
        seen: set[str] = set()
        for t in v:
            if not isinstance(t, str):
                raise ValueError("tags 必须是字符串数组")
            tag = t.strip().lower()
            if not tag:
                continue
            if tag not in ALL_LLM_TAGS:
                raise ValueError(
                    f"未知 tag：{tag}（合法：{sorted(ALL_LLM_TAGS)}）"
                )
            if tag in seen:
                continue
            seen.add(tag)
            normalized.append(tag)
        return normalized


class LLMProviderUpdate(BaseModel):
    """PATCH 更新；``api_key`` 给 None = 不动；空串 = 清空；非空字符串 = 替换。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    provider: Literal["openai", "anthropic", "ollama"] | None = None
    api_key: str | None = Field(default=None, max_length=512)
    base_url: str | None = Field(default=None, max_length=255)
    default_model: str | None = Field(default=None, min_length=1, max_length=64)
    api_format: Literal["chat_completions", "responses", "anthropic_messages"] | None = None

    # 路由元数据（全可选；None / 缺省 = 不动）
    modality: Literal["text", "vision", "audio", "multimodal"] | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    cost_tier: int | None = Field(default=None, ge=1, le=3)
    notes: str | None = Field(default=None, max_length=500)
    # proxy：要支持显式置 None（前端切回 DIRECT）；用 sentinel 区分"没传"和"传了 None"
    # 简化做法：另一个布尔 ``clear_proxy``（前端切到 DIRECT 时同时下发 proxy_id=None +
    # clear_proxy=True；切到具体 proxy 时下发 proxy_id=<id>）
    proxy_id: int | None = Field(default=None, ge=1)
    clear_proxy: bool = False
    """显式 True 表示「切回直连」；为 False 时如果 proxy_id 也是 None 则视为"不动"。"""

    models: list[ProviderModel] | None = Field(default=None, max_length=200)
    """整体替换式的 PATCH——None 表示不动；给 list（含空 list）则覆盖。
    fetch-models / test-model 等独立 endpoint 不通过这条字段，那些直接改 DB。"""

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        # 复用 Create 的校验逻辑
        return LLMProviderCreate._check_tags.__func__(cls, v)  # type: ignore[attr-defined]


class LLMProviderOut(BaseModel):
    """LLM provider 出参；**绝不含明文 api_key**。"""

    id: int
    name: str
    provider: str
    has_api_key: bool
    base_url: str | None = None
    default_model: str
    api_format: str = LLM_API_FORMAT_CHAT_COMPLETIONS
    # 路由元数据（出参始终带，便于前端展示）
    modality: str = LLM_MODALITY_TEXT
    tags: list[str] = Field(default_factory=list)
    cost_tier: int = 2
    notes: str | None = None
    # 出口代理：None = 直连
    proxy_id: int | None = None
    # 候选模型清单（带启用状态）
    models: list[ProviderModel] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FetchModelsResponse(BaseModel):
    """``POST /api/commands/llm-providers/{pid}/fetch-models`` 出参。"""

    fetched: int
    """从 ``GET {base_url}/models`` 拉到的模型条数（不含已有 enabled 状态合并前的差异）。"""
    provider: LLMProviderOut
    """合并后最新 provider 出参——前端可以直接用它替换缓存。"""


class TestModelRequest(BaseModel):
    """``POST /api/commands/llm-providers/{pid}/test-model`` 入参。"""

    model: str = Field(min_length=1, max_length=128)
    """要测的模型 ID。后端会用它做一次 max_tokens=4 的最小调用，回延时和返回片段。"""


class TestModelResponse(BaseModel):
    """``POST /api/commands/llm-providers/{pid}/test-model`` 出参。"""

    ok: bool
    """是否成功（HTTP 200 + 有正常 text 输出）。"""
    latency_ms: int
    """从发请求到收到响应的总耗时（毫秒）。"""
    model: str | None = None
    """API 实际返回的模型名（可能与请求的 model 略有差异，如带日期后缀）。"""
    preview: str | None = None
    """返回 text 前 80 字符；用于让用户在 UI 一眼看出"这个模型确实回话了"。"""
    error: str | None = None
    """失败时的错误消息（已脱敏，不含 api_key）。"""

===== backend/app/schemas/device_profile.py =====
"""设备伪装 (device_profile) 相关 schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceProfileOut(BaseModel):
    id: int
    name: str
    device_model: str
    system_version: str
    app_version: str
    lang_code: str
    system_lang_code: str
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeviceProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    device_model: str = Field(min_length=1, max_length=128)
    system_version: str = Field(min_length=1, max_length=64)
    app_version: str = Field(min_length=1, max_length=64)
    lang_code: str = Field(default="zh", max_length=16)
    system_lang_code: str = Field(default="zh-Hans", max_length=16)
    # 创建时 = True 会自动把其他行的 is_default 置 False
    is_default: bool = False


class DeviceProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    device_model: str | None = Field(default=None, min_length=1, max_length=128)
    system_version: str | None = Field(default=None, min_length=1, max_length=64)
    app_version: str | None = Field(default=None, min_length=1, max_length=64)
    lang_code: str | None = Field(default=None, max_length=16)
    system_lang_code: str | None = Field(default=None, max_length=16)
    is_default: bool | None = None

===== backend/app/schemas/feature.py =====
"""功能与功能矩阵 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class FeatureInfo(BaseModel):
    key: str
    display_name: str
    is_builtin: bool
    version: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AccountFeatureToggle(BaseModel):
    """启停某账号的某功能。"""
    enabled: bool
    config: dict[str, Any] | None = None


class AccountFeatureItem(BaseModel):
    feature_key: str
    enabled: bool
    state: str
    last_error: str | None = None
    config: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class FeatureMatrixCell(BaseModel):
    """功能矩阵的单元格状态。"""
    state: str  # active | failed | disabled


class FeatureMatrixRow(BaseModel):
    id: int
    name: str
    features: dict[str, str]  # feature_key -> state


class FeatureMatrixResponse(BaseModel):
    features: list[FeatureInfo]
    accounts: list[FeatureMatrixRow]

===== backend/app/schemas/ignored_peer.py =====
"""忽略 peer 名单的 Pydantic schema。

API：
- ``IgnoredPeerOut``       — 返回给前端的单行
- ``IgnoredPeerCreate``    — POST /api/accounts/{aid}/ignored-peers 入参
- ``RecentPeerItem``       — GET /api/accounts/{aid}/recent-peers 返回的每条
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..db.models.ignored_peer import PEER_KINDS


class IgnoredPeerOut(BaseModel):
    """忽略名单的一行（GET / POST 响应）。"""

    id: int
    account_id: int
    peer_id: int
    peer_kind: str
    peer_label: str | None = None
    added_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IgnoredPeerCreate(BaseModel):
    """加入忽略名单的入参。

    手填场景下 ``peer_kind`` 用户可能不知道——保留 "private" 默认值；后端不强校验，
    只把字符串原样落库（白名单见 ``PEER_KINDS``）。
    """

    peer_id: int
    peer_kind: str = Field(default="private")
    peer_label: str | None = None

    def normalized_kind(self) -> str:
        """归一化 ``peer_kind``：不在白名单内则视为 ``private``。"""
        return self.peer_kind if self.peer_kind in PEER_KINDS else "private"


class RecentPeerItem(BaseModel):
    """worker 内存里的最近活跃 peer 一条。

    ``ts`` 是 epoch 秒（``time.time()``），由 worker 写入；前端做相对时间显示。
    """

    peer_id: int
    peer_kind: str
    peer_label: str | None = None
    ts: float


class RecentPeersResponse(BaseModel):
    """``GET /recent-peers`` 的包裹响应。

    ``worker_alive`` 区分两种 ``items=[]`` 的情形：
    - ``True``  → worker 在跑，只是当前没有最近活跃 peer（让用户给自己发条消息试试）
    - ``False`` → worker 没在跑 / RPC 超时（让用户去概览暂停 → 启动一次）

    单独包裹一层是为了不破坏 ``RecentPeerItem`` 现有 schema。
    """

    worker_alive: bool
    items: list[RecentPeerItem]

===== backend/app/schemas/rate_limit.py =====
"""风控相关 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..db.models.rate_limit import (
    ACTION_KEYS,
    POLICY_BACKOFF,
    POLICY_DROP,
    POLICY_NOTIFY,
    POLICY_PAUSE,
    POLICY_QUEUE,
)

POLICIES = (POLICY_DROP, POLICY_QUEUE, POLICY_BACKOFF, POLICY_PAUSE, POLICY_NOTIFY)


class RateLimitRuleConfig(BaseModel):
    """单条动作的限速配置（前端表格一行）。"""
    action: str
    per_second: int | None = None
    per_minute: int | None = None
    per_hour: int | None = None
    per_day: int | None = None
    same_peer_per_minute: int | None = None
    policy: str = POLICY_QUEUE
    backoff_base_seconds: int = 5
    backoff_max_seconds: int = 1800
    enabled: bool = True

    model_config = ConfigDict(from_attributes=True)


class TemplateOut(BaseModel):
    id: int
    name: str
    is_default: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TemplateCreate(BaseModel):
    name: str
    is_default: bool = False


class AccountRateLimitOut(BaseModel):
    """账号级合并后的有效配置（含继承标记）。"""

    template_id: int | None
    rules: list[RateLimitRuleConfig]


class UsageBucket(BaseModel):
    action: str
    used: float
    limit: int | None
    pct: float
    warn: bool = False


class UsageResponse(BaseModel):
    window: str
    buckets: list[UsageBucket]
    active_overrides: list[dict[str, Any]] = Field(default_factory=list)


class StrictRequest(BaseModel):
    """一键调严：阈值 ×multiplier，TTL 秒。"""
    multiplier: float = 0.5
    ttl_seconds: int = 7200


class EstimateRequest(BaseModel):
    action: str
    target_count: int
    total_count: int


class EstimateResponse(BaseModel):
    eta_seconds: int
    exceeds_limit: bool


class HumanizeOut(BaseModel):
    jitter_pct: int
    typing_simulate: bool
    typing_min_ms: int
    typing_max_ms: int
    typing_probability: int
    read_before_reply: bool
    active_window_start: str | None = None
    active_window_end: str | None = None
    cold_start_days: int

    model_config = ConfigDict(from_attributes=True)


class HumanizeUpdate(BaseModel):
    jitter_pct: int | None = None
    typing_simulate: bool | None = None
    typing_min_ms: int | None = None
    typing_max_ms: int | None = None
    typing_probability: int | None = None
    read_before_reply: bool | None = None
    active_window_start: str | None = None
    active_window_end: str | None = None
    cold_start_days: int | None = None


class KillSwitchRequest(BaseModel):
    enabled: bool


class GlobalLimitsRequest(BaseModel):
    api_qps_total: int = 0


__all__ = [
    "ACTION_KEYS",
    "AccountRateLimitOut",
    "EstimateRequest",
    "EstimateResponse",
    "GlobalLimitsRequest",
    "HumanizeOut",
    "HumanizeUpdate",
    "KillSwitchRequest",
    "POLICIES",
    "RateLimitRuleConfig",
    "StrictRequest",
    "TemplateCreate",
    "TemplateOut",
    "UsageBucket",
    "UsageResponse",
]

===== backend/app/schemas/rule.py =====
"""规则 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuleCreate(BaseModel):
    name: str
    enabled: bool = True
    priority: int = 100
    config: dict[str, Any] = Field(default_factory=dict)


class RuleUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    config: dict[str, Any] | None = None


class RuleOut(BaseModel):
    id: int
    account_id: int
    feature_key: str
    name: str
    enabled: bool
    priority: int
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RuleCopyRequest(BaseModel):
    rule_ids: list[int]
    target_account_ids: list[int]


class RuleDryRunRequest(BaseModel):
    """试运行：把模拟消息喂给规则，返回是否命中 + 渲染结果。"""
    sample_message: str
    sample_chat_type: str | None = "private"  # private | group | channel
    sample_chat_id: int | None = None         # group/channel 类型可选，用于 group_specific 命中


class RuleDryRunResponse(BaseModel):
    matched: bool
    output: str | None = None
    detail: dict[str, Any] | None = None

===== backend/app/services/__init__.py =====
"""services 子包。"""

===== backend/app/services/account_service.py =====
"""账号 CRUD 业务层。

为 ``api/accounts.py`` 提供：
- 列表 / 详情 / 修改 / 删除
- 暂停 / 恢复
- 复制配置（account_feature + 关联 rule）
- 头像懒加载（本地磁盘缓存 + IPC 通知 worker 拉新）

只做 DB 与 IPC 协调，登录绑定向导在 ``login_service.py``。
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.sessions import StringSession

from ..crypto import decrypt_bytes, decrypt_str
from ..db.models.account import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_PAUSED,
    Account,
    Proxy,
)
from ..db.models.feature import AccountFeature
from ..db.models.rule import Rule
from ..redis_client import get_redis
from ..schemas.account import (
    AccountDetail,
    AccountSummary,
    AccountUpdateRequest,
)
from ..settings import settings
from ..worker.ipc import (
    CMD_FETCH_AVATAR,
    CMD_PAUSE,
    CMD_RESUME,
    CMD_STOP,
    GLOBAL_CHANNEL,
    cmd_channel,
    make_cmd,
)

# 头像缓存 TTL：超过这个时长就让 worker 重拉
_AVATAR_TTL_SECONDS = 24 * 3600


# ── 错误工具 ──────────────────────────────────────────────────────
def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _not_found() -> HTTPException:
    return _err("ACCOUNT_NOT_FOUND", "账号不存在", 404)


# ── 查询 ──────────────────────────────────────────────────────────
async def list_accounts(db: AsyncSession) -> list[AccountSummary]:
    """列出全部账号，并通过左连接计算每账号已启用的功能数。"""
    # 子查询：每账号 enabled=true 的 account_feature 计数
    enabled_count_sq = (
        select(
            AccountFeature.account_id.label("aid"),
            func.count(AccountFeature.feature_key).label("cnt"),
        )
        .where(AccountFeature.enabled.is_(True))
        .group_by(AccountFeature.account_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Account, func.coalesce(enabled_count_sq.c.cnt, 0))
            .outerjoin(enabled_count_sq, enabled_count_sq.c.aid == Account.id)
            .order_by(Account.id)
        )
    ).all()
    out: list[AccountSummary] = []
    for acc, cnt in rows:
        out.append(
            AccountSummary(
                id=acc.id,
                phone=acc.phone,
                display_name=acc.display_name,
                tg_user_id=acc.tg_user_id,
                tg_username=acc.tg_username,
                status=acc.status,
                tags=acc.tags,
                enabled_features=int(cnt or 0),
                cold_start_until=acc.cold_start_until,
                created_at=acc.created_at,
            )
        )
    return out


async def _enabled_count(db: AsyncSession, aid: int) -> int:
    """单账号的已启用功能计数（用于详情）。"""
    cnt = (
        await db.execute(
            select(func.count(AccountFeature.feature_key)).where(
                AccountFeature.account_id == aid, AccountFeature.enabled.is_(True)
            )
        )
    ).scalar_one()
    return int(cnt or 0)


async def get_account(db: AsyncSession, aid: int) -> AccountDetail:
    """读取账号详情。"""
    acc = await db.get(Account, aid)
    if not acc:
        raise _not_found()
    cnt = await _enabled_count(db, aid)
    return AccountDetail(
        id=acc.id,
        phone=acc.phone,
        display_name=acc.display_name,
        tg_user_id=acc.tg_user_id,
        tg_username=acc.tg_username,
        status=acc.status,
        tags=acc.tags,
        enabled_features=cnt,
        cold_start_until=acc.cold_start_until,
        created_at=acc.created_at,
        notes=acc.notes,
        template_id=acc.template_id,
        proxy_id=acc.proxy_id,
        device_profile_id=acc.device_profile_id,
    )


# ── 修改 ──────────────────────────────────────────────────────────
async def update_account(db: AsyncSession, aid: int, data: AccountUpdateRequest) -> AccountDetail:
    """PATCH 账号字段（display_name / notes / tags / template_id / proxy_id）。"""
    acc = await db.get(Account, aid)
    if not acc:
        raise _not_found()
    # 仅赋值用户显式给出的字段（exclude_unset 区分 None 与 未传）
    payload = data.model_dump(exclude_unset=True)
    for k, v in payload.items():
        setattr(acc, k, v)
    await db.commit()
    return await get_account(db, aid)


# ── 暂停 / 恢复 ───────────────────────────────────────────────────
async def pause(db: AsyncSession, aid: int) -> None:
    """暂停账号：状态置 paused，并通过 IPC 通知 worker pause。"""
    acc = await db.get(Account, aid)
    if not acc:
        raise _not_found()
    acc.status = ACCOUNT_STATUS_PAUSED
    await db.commit()
    await _publish(cmd_channel(aid), make_cmd(CMD_PAUSE))


async def resume(db: AsyncSession, aid: int) -> None:
    """恢复账号：状态置 active，通过 IPC 通知 worker resume；若 worker 未起则广播 start。"""
    acc = await db.get(Account, aid)
    if not acc:
        raise _not_found()
    acc.status = ACCOUNT_STATUS_ACTIVE
    await db.commit()
    # 先尝试恢复（若 worker 在跑）；同时广播 start_worker（若未起）让 supervisor 拉起
    await _publish(cmd_channel(aid), make_cmd(CMD_RESUME))
    await _publish(GLOBAL_CHANNEL, make_cmd("start_worker", account_id=aid))


# ── 删除 ──────────────────────────────────────────────────────────
async def delete_account(db: AsyncSession, aid: int) -> None:
    """删除账号：先发 STOP；再尝试 log_out（best effort）；最后 DELETE FROM account。"""
    acc = await db.get(Account, aid)
    if not acc:
        raise _not_found()

    # 1. 通知 worker 自杀
    await _publish(cmd_channel(aid), make_cmd(CMD_STOP))

    # 2. best effort：用现有 session 调 client.log_out() 让 TG 撤销 session
    try:
        await _logout_best_effort(db, acc)
    except Exception:  # noqa: BLE001
        # 撤销失败也不阻塞 DELETE（账号可能已 dead 或网络不可达）
        pass

    # 3. DELETE FROM account（cascade 会带走 humanize_config / account_feature / rule / 日志）
    await db.delete(acc)
    await db.commit()


async def _logout_best_effort(db: AsyncSession, acc: Account) -> None:
    """尝试用账号自身的 session 在 TG 服务端撤销登录。失败静默。"""
    api_id = int(decrypt_str(acc.api_id_enc))
    api_hash = decrypt_str(acc.api_hash_enc)
    session_str = decrypt_bytes(acc.session_enc).decode()

    proxy_tuple = None
    if acc.proxy_id:
        proxy = await db.get(Proxy, acc.proxy_id)
        if proxy:
            proxy_tuple = (
                proxy.type,
                proxy.host,
                proxy.port,
                True,
                proxy.username,
                decrypt_str(proxy.password_enc) if proxy.password_enc else None,
            )

    client = TelegramClient(StringSession(session_str), api_id, api_hash, proxy=proxy_tuple)
    try:
        await client.connect()
        if await client.is_user_authorized():
            await client.log_out()
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


# ── 复制配置 ──────────────────────────────────────────────────────
async def clone_config(
    db: AsyncSession,
    src_aid: int,
    dst_aid: int,
    features: Iterable[str] | None = None,
) -> dict[str, int]:
    """把源账号的 ``account_feature`` 与对应 ``rule`` 复制到目标账号。

    :param features: 指定要复制的 feature_key 列表；为空表示全部。
    :return: ``{"features": N, "rules": M}`` 复制条数统计。
    """
    if src_aid == dst_aid:
        raise _err("CLONE_SAME_ACCOUNT", "源账号和目标账号相同")

    # 校验两个账号都存在
    src = await db.get(Account, src_aid)
    dst = await db.get(Account, dst_aid)
    if not src or not dst:
        raise _not_found()

    feature_filter = list(features) if features else None

    # 1) 复制 account_feature
    af_q = select(AccountFeature).where(AccountFeature.account_id == src_aid)
    if feature_filter:
        af_q = af_q.where(AccountFeature.feature_key.in_(feature_filter))
    src_afs = (await db.execute(af_q)).scalars().all()

    # 先清掉目标账号同 key 的 account_feature，保证幂等
    if src_afs:
        keys_to_overwrite = [af.feature_key for af in src_afs]
        await db.execute(
            delete(AccountFeature).where(
                AccountFeature.account_id == dst_aid,
                AccountFeature.feature_key.in_(keys_to_overwrite),
            )
        )
        # 同样删掉这些 feature 在目标账号的 rule
        await db.execute(
            delete(Rule).where(
                Rule.account_id == dst_aid,
                Rule.feature_key.in_(keys_to_overwrite),
            )
        )

    feat_n = 0
    for af in src_afs:
        db.add(
            AccountFeature(
                account_id=dst_aid,
                feature_key=af.feature_key,
                enabled=af.enabled,
                config=dict(af.config or {}),
                state=af.state,
            )
        )
        feat_n += 1

    # 2) 复制 rule
    rule_q = select(Rule).where(Rule.account_id == src_aid)
    if feature_filter:
        rule_q = rule_q.where(Rule.feature_key.in_(feature_filter))
    src_rules = (await db.execute(rule_q)).scalars().all()
    rule_n = 0
    for r in src_rules:
        db.add(
            Rule(
                account_id=dst_aid,
                feature_key=r.feature_key,
                name=r.name,
                enabled=r.enabled,
                priority=r.priority,
                config=dict(r.config or {}),
            )
        )
        rule_n += 1

    await db.commit()

    # 通知目标 worker 重新加载配置（若在跑）
    await _publish(cmd_channel(dst_aid), make_cmd("reload_config"))

    return {"features": feat_n, "rules": rule_n}


# ── 头像懒加载 ────────────────────────────────────────────────────
def _avatar_path(aid: int) -> Path:
    """返回 ``data/avatars/{aid}.jpg`` 的绝对路径（不保证存在）。"""
    return Path(settings.avatars_dir).resolve() / f"{aid}.jpg"


async def ensure_avatar(db: AsyncSession, aid: int) -> Path | None:
    """检查本地头像缓存：

    - 文件存在且未过期（24h）→ 直接返；
    - 文件不存在 / 过期 → fire-and-forget 发 IPC 让 worker 写盘，本次返当前
      路径（可能为 None）；
    - 账号不存在 → 抛 404。

    worker 离线时 IPC 没人接收，本次仍返 None；前端会走首字母 fallback，
    下次刷新（等 worker 起来）就能看到。
    """
    acc = await db.get(Account, aid)
    if acc is None:
        raise _not_found()

    path = _avatar_path(aid)
    fresh = False
    if path.exists():
        try:
            mtime = path.stat().st_mtime
            fresh = (time.time() - mtime) < _AVATAR_TTL_SECONDS
        except OSError:
            fresh = False

    if not fresh:
        # 不阻塞请求：把绝对路径告诉 worker，worker 写盘后下次请求就能读到
        await _publish(
            cmd_channel(aid),
            make_cmd(CMD_FETCH_AVATAR, path=str(path)),
        )

    return path if path.exists() else None


# ── IPC 工具 ──────────────────────────────────────────────────────
async def _publish(channel: str, payload: str) -> None:
    """对 Redis publish 失败时静默；保证业务路径优先成功。"""
    try:
        redis = get_redis()
        await redis.publish(channel, payload)
    except Exception:  # noqa: BLE001
        pass

===== backend/app/services/audit.py =====
"""操作日志（audit log）写入工具。

由各 API 写操作调用，记录到 ``audit_log`` 表。本模块不在内部 commit，事务由调用方控制。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.log import AuditLog


async def write(
    db: AsyncSession,
    user_id: int | None,
    action: str,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """追加一条 audit log。

    :param db: 当前请求的 AsyncSession（事务由 API 层控制）。
    :param user_id: 触发操作的 Web 用户 id；后台/系统操作可为 None。
    :param action: 动作动词，例如 ``account.create`` / ``account.delete``。
    :param target: 目标资源的字符串描述（如 ``account:42``）。
    :param detail: 任意 JSON 附加信息（脱敏后再写入）。
    """
    db.add(AuditLog(user_id=user_id, action=action, target=target, detail=detail))
    # 不 commit；调用方负责事务边界

===== backend/app/services/auth_service.py =====
"""认证 / 鉴权服务：Argon2 密码哈希 + JWT + TOTP。

- 密码哈希采用 argon2id（argon2-cffi 默认）。
- JWT 采用 HS256，签名密钥来自 ``settings.jwt_secret``，过期时间来自 ``settings.jwt_expire_seconds``。
- TOTP 基于 RFC 6238（pyotp 默认 30s 时窗、6 位数字）。

注意：``deps.py`` 通过惰性 import 引用本模块的 ``decode_jwt_token``，函数名与签名禁止改动。
"""

from __future__ import annotations

import time
from urllib.parse import quote

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

from ..settings import settings

# 单例 PasswordHasher（线程安全；内部参数为 argon2-cffi 推荐默认值）
_hasher = PasswordHasher()


# ── 密码 ──────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    """生成 argon2id 哈希字符串。"""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码。任何失败原因（不匹配 / 哈希损坏）都返回 False。"""
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


# ── JWT ───────────────────────────────────────────────────────────
def issue_jwt_token(user_id: int) -> str:
    """颁发短期 JWT（HS256）。payload = ``{sub, exp, iat}``。"""
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + settings.jwt_expire_seconds,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_jwt_token(token: str) -> int | None:
    """解码 JWT，成功返回 user_id；失败 / 过期 / 签名不对一律返回 None。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return int(sub)
    except (TypeError, ValueError):
        return None


# ── TOTP ──────────────────────────────────────────────────────────
def generate_totp_secret() -> str:
    """生成 base32 TOTP 共享密钥。"""
    return pyotp.random_base32()


def make_otpauth_url(username: str, secret: str, issuer: str = "TelegramUserbot") -> str:
    """构造 ``otpauth://`` URL，可被 Authenticator / 1Password 等扫码识别。"""
    label = quote(f"{issuer}:{username}", safe="")
    issuer_q = quote(issuer, safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={issuer_q}"


def verify_totp(secret: str, code: str) -> bool:
    """校验 6 位 TOTP code。``valid_window=1`` 容忍 ±30s 时钟漂移。"""
    if not secret or not code:
        return False
    try:
        return bool(pyotp.TOTP(secret).verify(code.strip(), valid_window=1))
    except Exception:
        return False

===== backend/app/services/command_service.py =====
"""自定义命令业务层（Sprint2 #2）。

职责：
- ``command_template`` CRUD + 名称冲突检测
- ``llm_provider`` CRUD + Fernet 加密落库 + has_api_key 出参
- ``account_command_link`` 启用 / 禁用 + 通知 worker reload

约定：
- 服务层不在内部 ``commit``；事务边界由 API 层（``api/commands.py``）控制
- IPC ``CMD_RELOAD_COMMANDS`` 失败静默，redis 不可用时不阻塞 DB 操作
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import encrypt_str
from ..db.models.account import Account, Proxy
from ..db.models.command import (
    AccountCommandLink,
    CommandTemplate,
    LLMProvider,
)
from ..redis_client import get_redis
from ..schemas.command import (
    AccountCommandItem,
    CommandTemplateCreate,
    CommandTemplateOut,
    CommandTemplateUpdate,
    LLMProviderCreate,
    LLMProviderOut,
    LLMProviderUpdate,
)
from ..worker.ipc import CMD_RELOAD_COMMANDS, cmd_channel, make_cmd

log = logging.getLogger(__name__)


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


# ════════════════════════════════════════════════════════════
# CommandTemplate CRUD
# ════════════════════════════════════════════════════════════


async def list_templates(db: AsyncSession) -> list[CommandTemplate]:
    """列出全部模板（按 id 升序，便于 UI 稳定排序）。"""
    rows = (
        await db.execute(select(CommandTemplate).order_by(CommandTemplate.id.asc()))
    ).scalars().all()
    return list(rows)


async def get_template(db: AsyncSession, tpl_id: int) -> CommandTemplate:
    """取单条；不存在则 404。"""
    row = await db.get(CommandTemplate, tpl_id)
    if row is None:
        raise _err("TEMPLATE_NOT_FOUND", "命令模板不存在", 404)
    return row


async def create_template(db: AsyncSession, payload: CommandTemplateCreate) -> CommandTemplate:
    """新建模板；name 冲突 → 409。"""
    # 显式查重，避免依赖 IntegrityError 给出友好错误
    existing = (
        await db.execute(select(CommandTemplate).where(CommandTemplate.name == payload.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise _err("TEMPLATE_NAME_CONFLICT", f"已存在同名模板：{payload.name}", 409)

    tpl = CommandTemplate(
        name=payload.name,
        type=payload.type,
        config=dict(payload.config or {}),
        description=payload.description,
    )
    db.add(tpl)
    await db.flush()
    return tpl


async def update_template(
    db: AsyncSession, tpl_id: int, payload: CommandTemplateUpdate
) -> CommandTemplate:
    """PATCH 模板；只更新显式给出的字段。"""
    tpl = await get_template(db, tpl_id)
    data = payload.model_dump(exclude_unset=True)

    # 改名时查重
    if "name" in data and data["name"] != tpl.name:
        dup = (
            await db.execute(
                select(CommandTemplate).where(CommandTemplate.name == data["name"])
            )
        ).scalar_one_or_none()
        if dup is not None and dup.id != tpl.id:
            raise _err("TEMPLATE_NAME_CONFLICT", f"已存在同名模板：{data['name']}", 409)

    # 校验 config / type 一致性：要么二者都改，要么 schema validator 已经在 base 校验过；
    # 这里只在 PATCH 模式下额外保障 config 跟随 type 走
    new_type = data.get("type") or tpl.type
    new_config = data.get("config") if "config" in data else tpl.config
    if "type" in data or "config" in data:
        # 复用 base 验证：构造一个完整对象走一遍
        from ..schemas.command import CommandTemplateBase

        CommandTemplateBase(
            name=data.get("name") or tpl.name,
            type=new_type,
            config=dict(new_config or {}),
            description=data.get("description", tpl.description),
        )

    for k, v in data.items():
        setattr(tpl, k, v)
    await db.flush()
    return tpl


async def delete_template(db: AsyncSession, tpl_id: int) -> set[int]:
    """删除模板；返回受影响的 account_id 集合（用于 IPC 通知 reload）。

    级联删除会自动带走 ``account_command_link``；这里先抓 aid 集合再删，便于回调通知。
    """
    tpl = await get_template(db, tpl_id)
    aids = (
        await db.execute(
            select(AccountCommandLink.account_id).where(
                AccountCommandLink.template_id == tpl.id
            )
        )
    ).scalars().all()
    await db.delete(tpl)
    await db.flush()
    return set(aids)


# ════════════════════════════════════════════════════════════
# 账号 × 模板 关联
# ════════════════════════════════════════════════════════════


async def list_for_account(
    db: AsyncSession, account_id: int
) -> list[AccountCommandItem]:
    """列出某账号已启用 + 可用全部模板，标记 enabled 状态。

    返回顺序：模板按 id 升序；前端按需要再排序。
    """
    if await db.get(Account, account_id) is None:
        raise _err("ACCOUNT_NOT_FOUND", "账号不存在", 404)

    templates = await list_templates(db)
    links = {
        link.template_id: link.enabled
        for link in (
            await db.execute(
                select(AccountCommandLink).where(
                    AccountCommandLink.account_id == account_id
                )
            )
        ).scalars().all()
    }

    out: list[AccountCommandItem] = []
    for tpl in templates:
        enabled = bool(links.get(tpl.id, False))
        out.append(
            AccountCommandItem(
                template=CommandTemplateOut.model_validate(tpl),
                enabled=enabled,
            )
        )
    return out


async def list_active_for_worker(
    db: AsyncSession, account_id: int
) -> list[CommandTemplate]:
    """worker 启动 / reload 时调用：仅返回该账号实际启用的模板。"""
    rows = (
        await db.execute(
            select(CommandTemplate)
            .join(
                AccountCommandLink, AccountCommandLink.template_id == CommandTemplate.id
            )
            .where(
                AccountCommandLink.account_id == account_id,
                AccountCommandLink.enabled.is_(True),
            )
            .order_by(CommandTemplate.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def enable_for_account(
    db: AsyncSession, account_id: int, template_id: int
) -> AccountCommandLink:
    """启用某账号的某模板（upsert：若已存在但 enabled=False → 改为 True）。"""
    if await db.get(Account, account_id) is None:
        raise _err("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    if await db.get(CommandTemplate, template_id) is None:
        raise _err("TEMPLATE_NOT_FOUND", "命令模板不存在", 404)

    link = (
        await db.execute(
            select(AccountCommandLink).where(
                AccountCommandLink.account_id == account_id,
                AccountCommandLink.template_id == template_id,
            )
        )
    ).scalar_one_or_none()

    if link is None:
        link = AccountCommandLink(
            account_id=account_id, template_id=template_id, enabled=True
        )
        db.add(link)
    else:
        link.enabled = True
    await db.flush()
    return link


async def disable_for_account(
    db: AsyncSession, account_id: int, template_id: int
) -> None:
    """禁用某账号的某模板（直接删 link 行；下次启用再 upsert）。"""
    link = (
        await db.execute(
            select(AccountCommandLink).where(
                AccountCommandLink.account_id == account_id,
                AccountCommandLink.template_id == template_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        return
    await db.delete(link)
    await db.flush()


# ════════════════════════════════════════════════════════════
# LLM Provider CRUD
# ════════════════════════════════════════════════════════════


def _provider_to_out(row: LLMProvider) -> LLMProviderOut:
    """ORM → 出参；屏蔽明文 api_key。"""
    return LLMProviderOut(
        id=row.id,
        name=row.name,
        provider=row.provider,
        has_api_key=bool(row.api_key_enc),
        base_url=row.base_url,
        default_model=row.default_model,
        api_format=getattr(row, "api_format", None) or "chat_completions",
        # 路由元数据（老数据可能为 None / [] / 缺字段；用属性 getattr 兼容）
        modality=getattr(row, "modality", None) or "text",
        tags=list(getattr(row, "tags", None) or []),
        cost_tier=int(getattr(row, "cost_tier", None) or 2),
        notes=getattr(row, "notes", None),
        proxy_id=getattr(row, "proxy_id", None),
        # 候选模型清单
        models=list(getattr(row, "models", None) or []),
        created_at=row.created_at,
    )


async def _validate_proxy_for_llm(db: AsyncSession, proxy_id: int) -> Proxy:
    """校验 proxy_id 指向的 Proxy 行可用作 LLM 出口。

    拒绝条件：
    - 不存在 → 404
    - type=mtproxy → 422（HTTP 客户端不支持 Telegram MTProto）
    """
    p = await db.get(Proxy, proxy_id)
    if p is None:
        raise _err("PROXY_NOT_FOUND", f"proxy_id={proxy_id} 不存在", 404)
    if (p.type or "").lower() == "mtproxy":
        raise _err(
            "PROXY_KIND_NOT_SUPPORTED",
            "mtproxy 仅支持 Telegram，不能用于 LLM 调用；请选 socks5/http/https 类型的代理",
            422,
        )
    return p


async def list_providers(db: AsyncSession) -> list[LLMProviderOut]:
    """列出全部 LLM provider（不返明文 key）。"""
    rows = (
        await db.execute(select(LLMProvider).order_by(LLMProvider.id.asc()))
    ).scalars().all()
    return [_provider_to_out(r) for r in rows]


async def get_provider_row(db: AsyncSession, pid: int) -> LLMProvider:
    """内部使用；返回原始 ORM（含 api_key_enc）。worker 用来调 LLM 之前需要解密。"""
    row = await db.get(LLMProvider, pid)
    if row is None:
        raise _err("LLM_PROVIDER_NOT_FOUND", "LLM provider 不存在", 404)
    return row


async def create_provider(
    db: AsyncSession, payload: LLMProviderCreate
) -> LLMProviderOut:
    """新建 provider；api_key 给非空字符串则加密落库。"""
    dup = (
        await db.execute(select(LLMProvider).where(LLMProvider.name == payload.name))
    ).scalar_one_or_none()
    if dup is not None:
        raise _err("LLM_PROVIDER_NAME_CONFLICT", f"已存在同名 provider：{payload.name}", 409)

    # 校验 proxy_id（如果指定了）
    if payload.proxy_id is not None:
        await _validate_proxy_for_llm(db, payload.proxy_id)

    row = LLMProvider(
        name=payload.name,
        provider=payload.provider,
        # 空字符串视同未设置（避免存空 token 让 fernet 误判）
        api_key_enc=encrypt_str(payload.api_key) if payload.api_key else None,
        base_url=payload.base_url,
        default_model=payload.default_model,
        api_format=payload.api_format,
        # 路由元数据
        modality=payload.modality,
        tags=list(payload.tags or []),
        cost_tier=int(payload.cost_tier),
        notes=payload.notes,
        proxy_id=payload.proxy_id,
        # 候选模型清单（前端可以建完之后再调 fetch-models 自动填）
        models=[m.model_dump() for m in (payload.models or [])],
    )
    db.add(row)
    await db.flush()
    return _provider_to_out(row)


async def update_provider(
    db: AsyncSession, pid: int, payload: LLMProviderUpdate
) -> LLMProviderOut:
    """PATCH provider；
    api_key 行为：
    - None  → 不动
    - ""    → 清空
    - 非空  → 加密落库
    """
    row = await get_provider_row(db, pid)
    data = payload.model_dump(exclude_unset=True)

    if "name" in data and data["name"] != row.name:
        dup = (
            await db.execute(
                select(LLMProvider).where(LLMProvider.name == data["name"])
            )
        ).scalar_one_or_none()
        if dup is not None and dup.id != row.id:
            raise _err("LLM_PROVIDER_NAME_CONFLICT", f"已存在同名 provider：{data['name']}", 409)
        row.name = data["name"]

    if "provider" in data:
        row.provider = data["provider"]
    if "base_url" in data:
        row.base_url = data["base_url"]
    if "default_model" in data and data["default_model"]:
        row.default_model = data["default_model"]
    if "api_format" in data and data["api_format"]:
        row.api_format = data["api_format"]

    # 路由元数据：明确出现在 patch 内才覆盖
    if "modality" in data and data["modality"] is not None:
        row.modality = data["modality"]
    if "tags" in data and data["tags"] is not None:
        row.tags = list(data["tags"])
    if "cost_tier" in data and data["cost_tier"] is not None:
        row.cost_tier = int(data["cost_tier"])
    if "notes" in data:
        # notes 允许显式 None 清空
        row.notes = data["notes"]

    # proxy 处理：clear_proxy=True → 切回直连；否则只在 proxy_id 显式给值时改
    # （exclude_unset=True 已经过滤掉前端没传的字段，所以 proxy_id 出现 = 用户主动选了一条）
    if data.get("clear_proxy"):
        row.proxy_id = None
    elif "proxy_id" in data and data["proxy_id"] is not None:
        await _validate_proxy_for_llm(db, int(data["proxy_id"]))
        row.proxy_id = int(data["proxy_id"])

    # models：整体替换（前端 PATCH 整个 list；fetch-models / test-model 走独立接口）
    if "models" in data and data["models"] is not None:
        # data["models"] 此时可能是 list[dict]（pydantic 已把 ProviderModel 序列化）
        # 也可能是 list[ProviderModel]，两种都做 dict 化兜底
        new_models = []
        for m in data["models"]:
            if hasattr(m, "model_dump"):
                new_models.append(m.model_dump())
            else:
                new_models.append(dict(m))
        row.models = new_models

    if "api_key" in data:
        v = data["api_key"]
        if v is None:
            # 显式 None 表示前端没改 key 字段；保持原样
            pass
        elif v == "":
            row.api_key_enc = None
        else:
            row.api_key_enc = encrypt_str(v)

    await db.flush()
    return _provider_to_out(row)


async def delete_provider(db: AsyncSession, pid: int) -> None:
    """删除 provider；引用此 provider 的 ai 类模板调用会在 worker 内报错（friendly message）。"""
    row = await get_provider_row(db, pid)
    await db.delete(row)
    await db.flush()


# ════════════════════════════════════════════════════════════
# IPC：通知 worker 重新拉取启用模板
# ════════════════════════════════════════════════════════════


async def list_aids_with_ai_commands(db: AsyncSession) -> list[int]:
    """返回所有"启用了 type=ai 模板"的账号 id（去重）。

    用途：改 / 删 LLM Provider 后通知这些账号 reload——worker 端
    ``_refresh_command_context`` 是无差别全量拉 provider 表的，所以一次
    reload 就能让所有 ai 模板看到新配置（包括 api_key 轮换、tags 调整、
    base_url 切到反代等）。

    这里故意不去深扒每条模板 config.provider_id 是否真的引用了改动的那条
    provider——多发一次 reload 是廉价的（worker 只重读一次 DB），但漏发
    会让用户的 api_key 轮换"在 TG 里发现没生效"，体验差。
    """
    rows = (
        await db.execute(
            select(AccountCommandLink.account_id)
            .join(
                CommandTemplate,
                CommandTemplate.id == AccountCommandLink.template_id,
            )
            .where(
                AccountCommandLink.enabled.is_(True),
                CommandTemplate.type == "ai",
            )
            .distinct()
        )
    ).scalars().all()
    return list(rows)


async def notify_reload(account_ids: int | Sequence[int]) -> None:
    """对一个或多个账号发 ``CMD_RELOAD_COMMANDS`` IPC。

    redis 不可用时静默；DB 已落地为准，下次 worker 启动会拉到最新数据。
    """
    if isinstance(account_ids, int):
        aids: list[int] = [account_ids]
    else:
        aids = list(account_ids)
    if not aids:
        return
    try:
        redis = get_redis()
        for aid in aids:
            await redis.publish(cmd_channel(aid), make_cmd(CMD_RELOAD_COMMANDS))
    except Exception:  # noqa: BLE001
        log.debug("通知 worker reload_commands 失败 aids=%s", aids, exc_info=True)


__all__ = [
    "create_provider",
    "create_template",
    "delete_provider",
    "delete_template",
    "disable_for_account",
    "enable_for_account",
    "get_provider_row",
    "get_template",
    "list_active_for_worker",
    "list_aids_with_ai_commands",
    "list_for_account",
    "list_providers",
    "list_templates",
    "notify_reload",
    "update_provider",
    "update_template",
]

===== backend/app/services/device_profile.py =====
"""设备伪装 profile 解析。

worker 启动 / 登录向导构造 TelegramClient 时调用，按以下顺序回退：
  1. 账号显式绑定的 profile（``Account.device_profile_id``）
  2. 表里 ``is_default = true`` 的 profile
  3. 都没有 → ``HARDCODED_FALLBACK``

返回一个 dataclass 形态的结果，方便给 TelegramClient 解包。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account, DeviceProfile


@dataclass(frozen=True)
class ResolvedDeviceProfile:
    device_model: str
    system_version: str
    app_version: str
    lang_code: str
    system_lang_code: str

    def telethon_kwargs(self) -> dict[str, str]:
        return {
            "device_model": self.device_model,
            "system_version": self.system_version,
            "app_version": self.app_version,
            "lang_code": self.lang_code,
            "system_lang_code": self.system_lang_code,
        }


# 极端情况下的兜底（数据库为空、迁移未跑）。Telegram 设备列表里看到的"客户端"。
HARDCODED_FALLBACK = ResolvedDeviceProfile(
    device_model="MacBook Pro",
    system_version="macOS 14.5",
    app_version="Telegram macOS 11.5",
    lang_code="zh",
    system_lang_code="zh-Hans",
)


def _from_row(row: DeviceProfile) -> ResolvedDeviceProfile:
    return ResolvedDeviceProfile(
        device_model=row.device_model,
        system_version=row.system_version,
        app_version=row.app_version,
        lang_code=row.lang_code,
        system_lang_code=row.system_lang_code,
    )


async def get_default(db: AsyncSession) -> ResolvedDeviceProfile:
    """读 ``is_default = true`` 的 profile；找不到回退到 HARDCODED_FALLBACK。"""
    row = (
        await db.execute(
            select(DeviceProfile).where(DeviceProfile.is_default.is_(True)).limit(1)
        )
    ).scalar_one_or_none()
    return _from_row(row) if row else HARDCODED_FALLBACK


async def get_by_id(db: AsyncSession, profile_id: int) -> ResolvedDeviceProfile | None:
    """按 id 读；找不到返 None（让调用方决定继续回退到 default 还是报错）。"""
    row = await db.get(DeviceProfile, profile_id)
    return _from_row(row) if row else None


async def resolve_for_account(
    db: AsyncSession, account: Account
) -> ResolvedDeviceProfile:
    """根据账号绑定关系决定用哪条 profile。worker 启动时调这个。"""
    if account.device_profile_id is not None:
        bound = await get_by_id(db, account.device_profile_id)
        if bound is not None:
            return bound
    return await get_default(db)

===== backend/app/services/feature_service.py =====
"""功能（feature/plugin）业务层：feature 表 seed、account_feature 启停 + 矩阵查询。

API 层调本服务而不直接读写 ORM，便于以后引入更复杂的状态机或缓存。
所有需要"通知 worker 热重载"的写操作都会发一条 IPC ``CMD_RELOAD_CONFIG``，
异常吞掉避免影响 DB 事务结果。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account
from ..db.models.feature import (
    BUILTIN_FEATURES,
    FEATURE_STATE_ACTIVE,
    FEATURE_STATE_DISABLED,
    AccountFeature,
    Feature,
)
from ..redis_client import get_redis
from ..worker.ipc import CMD_RELOAD_CONFIG, cmd_channel, make_cmd

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# Feature 表 seed
# ─────────────────────────────────────────────────────
async def seed_builtin_features(db: AsyncSession) -> int:
    """确保内置 feature 行存在；返回新增条数。

    幂等：已经有同 key 的行就跳过；display_name 与 is_builtin 始终对齐到代码里的常量。
    """
    rows = (await db.execute(select(Feature))).scalars().all()
    existing: dict[str, Feature] = {f.key: f for f in rows}
    added = 0
    for key, name in BUILTIN_FEATURES.items():
        if key in existing:
            # 修正 display_name / is_builtin 标记（防御性同步）
            f = existing[key]
            changed = False
            if f.display_name != name:
                f.display_name = name
                changed = True
            if not f.is_builtin:
                f.is_builtin = True
                changed = True
            if changed:
                await db.flush()
            continue
        db.add(Feature(key=key, display_name=name, is_builtin=True))
        added += 1
    if added:
        await db.commit()
    return added


# ─────────────────────────────────────────────────────
# 列表查询
# ─────────────────────────────────────────────────────
async def list_features(db: AsyncSession) -> list[Feature]:
    """列出所有已登记的 feature；首次调用时会自动 seed 内置行。"""
    await seed_builtin_features(db)
    rows = (
        await db.execute(select(Feature).order_by(Feature.is_builtin.desc(), Feature.key.asc()))
    ).scalars().all()
    return list(rows)


async def get_account_features(db: AsyncSession, aid: int) -> list[AccountFeature]:
    """返回某账号已登记的 [account_feature] 行（未启用过的不在结果里）。"""
    rows = (
        await db.execute(
            select(AccountFeature)
            .where(AccountFeature.account_id == aid)
            .order_by(AccountFeature.feature_key.asc())
        )
    ).scalars().all()
    return list(rows)


# ─────────────────────────────────────────────────────
# upsert：启停 feature + 修改 config
# ─────────────────────────────────────────────────────
async def set_account_feature(
    db: AsyncSession,
    aid: int,
    key: str,
    enabled: bool,
    config: dict[str, Any] | None = None,
    *,
    notify: bool = True,
) -> AccountFeature:
    """对 [账号 × feature] 做 upsert。

    - 若该 account_feature 不存在 → 新建；state 默认 ``disabled``。
    - 若 enabled 由 False→True：state 仍记为 disabled，等 worker 实际激活后再改成 active。
    - 若 enabled 由 True→False：直接把 state 改成 ``disabled``。
    - ``config`` 不为 None 时整体覆盖（便于前端"保存即覆盖"语义）。

    完成后视 ``notify`` 决定是否发 IPC 通知 worker reload。
    """
    af = (
        await db.execute(
            select(AccountFeature).where(
                AccountFeature.account_id == aid,
                AccountFeature.feature_key == key,
            )
        )
    ).scalar_one_or_none()
    if af is None:
        af = AccountFeature(
            account_id=aid,
            feature_key=key,
            enabled=enabled,
            config=dict(config or {}),
            state=FEATURE_STATE_DISABLED,
        )
        db.add(af)
    else:
        af.enabled = enabled
        if config is not None:
            af.config = dict(config)
        if not enabled:
            # 立刻把状态置 disabled；激活由 worker 反向写
            af.state = FEATURE_STATE_DISABLED
            af.last_error = None
    await db.commit()
    await db.refresh(af)
    if notify:
        await _notify_reload(aid)
    return af


# ─────────────────────────────────────────────────────
# 矩阵：行=账号、列=feature
# ─────────────────────────────────────────────────────
async def feature_matrix(db: AsyncSession) -> dict[str, Any]:
    """构造功能矩阵数据：

    返回结构::

        {
          "features": [{key, display_name, is_builtin, version}, ...],
          "accounts": [
            {"id": 1, "name": "...", "features": {"auto_reply": "active", "forward": "disabled", ...}}
          ]
        }
    """
    # 1) 保证内置 feature 行齐全
    features = await list_features(db)

    # 2) 拿全部账号 + 全部 account_feature
    accounts = (
        await db.execute(select(Account).order_by(Account.id.asc()))
    ).scalars().all()
    afs = (await db.execute(select(AccountFeature))).scalars().all()
    by_aid: dict[int, dict[str, str]] = {}
    for af in afs:
        # 默认按 state 显示；若 enabled=False 则强制 disabled，避免脏状态混淆
        if not af.enabled:
            cell = FEATURE_STATE_DISABLED
        else:
            cell = af.state or FEATURE_STATE_ACTIVE
        by_aid.setdefault(af.account_id, {})[af.feature_key] = cell

    rows: list[dict[str, Any]] = []
    for acc in accounts:
        cells: dict[str, str] = {}
        existing = by_aid.get(acc.id, {})
        for f in features:
            cells[f.key] = existing.get(f.key, FEATURE_STATE_DISABLED)
        rows.append(
            {
                "id": acc.id,
                "name": acc.display_name or acc.phone,
                "features": cells,
            }
        )

    return {
        "features": [
            {
                "key": f.key,
                "display_name": f.display_name,
                "is_builtin": f.is_builtin,
                "version": f.version,
            }
            for f in features
        ],
        "accounts": rows,
    }


# ─────────────────────────────────────────────────────
# 批量启停（plugins/install / uninstall 用）
# ─────────────────────────────────────────────────────
async def bulk_set_enabled(
    db: AsyncSession,
    aids: Iterable[int],
    key: str,
    enabled: bool,
) -> int:
    """对一组账号统一启 / 停某 feature。返回受影响条数。"""
    n = 0
    for aid in aids:
        await set_account_feature(db, aid, key, enabled, config=None, notify=True)
        n += 1
    return n


# ─────────────────────────────────────────────────────
# IPC：通知 worker reload
# ─────────────────────────────────────────────────────
async def _notify_reload(account_id: int) -> None:
    """对指定 worker 发 ``CMD_RELOAD_CONFIG``；redis 不可用时静默。"""
    try:
        redis = get_redis()
        await redis.publish(cmd_channel(account_id), make_cmd(CMD_RELOAD_CONFIG))
    except Exception:  # noqa: BLE001
        log.debug("通知 worker reload 失败 account=%s", account_id, exc_info=True)


__all__ = [
    "bulk_set_enabled",
    "feature_matrix",
    "get_account_features",
    "list_features",
    "seed_builtin_features",
    "set_account_feature",
]

===== backend/app/services/humanize_service.py =====
"""拟人化（humanize）配置 Service 转发层。

实际数据访问已经在 ``rate_limit_service`` 里实现（``HumanizeConfig``
表跟风控引擎共享）。这个文件只是 Sprint 2 计划要求的稳定门面：
后续若把拟人化迁出风控模块，只需修改这里的转发，调用方无需改动。
"""

from __future__ import annotations

from datetime import time as dtime

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import HumanizeConfig
from . import rate_limit_service as _rate


async def get_humanize(db: AsyncSession, account_id: int) -> HumanizeConfig | None:
    """读取账号的拟人化配置；未配置返回 ``None``，由调用方决定如何回退默认值。"""
    return await _rate.get_humanize(db, account_id)


async def upsert_humanize(
    db: AsyncSession,
    account_id: int,
    *,
    jitter_pct: int | None = None,
    typing_simulate: bool | None = None,
    typing_min_ms: int | None = None,
    typing_max_ms: int | None = None,
    typing_probability: int | None = None,
    read_before_reply: bool | None = None,
    active_window_start: dtime | None = None,
    active_window_end: dtime | None = None,
    cold_start_days: int | None = None,
) -> HumanizeConfig:
    """局部更新（PATCH 语义）：``None`` 字段保持不变，其余落库。"""
    return await _rate.upsert_humanize(
        db,
        account_id,
        jitter_pct=jitter_pct,
        typing_simulate=typing_simulate,
        typing_min_ms=typing_min_ms,
        typing_max_ms=typing_max_ms,
        typing_probability=typing_probability,
        read_before_reply=read_before_reply,
        active_window_start=active_window_start,
        active_window_end=active_window_end,
        cold_start_days=cold_start_days,
    )


__all__ = ["get_humanize", "upsert_humanize"]

===== backend/app/services/ignored_peer_service.py =====
"""忽略 peer 名单业务层。

供 ``api/ignored_peers.py`` 使用：
- ``list_ignored``  返回某账号的忽略名单
- ``add_ignored``   加入一条；幂等（已存在则返回原行）
- ``remove_ignored``删除一条
- ``fetch_recent``  通过 IPC RPC 向 worker 请求最近活跃 peer 列表

写操作完成后通过 IPC ``CMD_RELOAD_IGNORED`` 通知 worker 重新拉取名单。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account
from ..db.models.ignored_peer import IgnoredPeer
from ..redis_client import get_redis
from ..schemas.ignored_peer import IgnoredPeerCreate
from ..worker.ipc import (
    CMD_GET_RECENT_PEERS,
    CMD_RELOAD_IGNORED,
    IPCMessage,
    cmd_channel,
    make_cmd,
)

log = logging.getLogger(__name__)

# 一次 RPC 默认超时；worker 离线 / 高负载时返回空列表，不阻塞前端
_RECENT_PEERS_TIMEOUT = 1.5


# ── 错误工具 ──────────────────────────────────────────────────────
def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _ensure_account(db: AsyncSession, aid: int) -> Account:
    """校验账号存在；不存在抛 404。"""
    acc = await db.get(Account, aid)
    if acc is None:
        raise _err("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    return acc


# ── 列表 / 增 / 删 ────────────────────────────────────────────────
async def list_ignored(db: AsyncSession, aid: int) -> list[IgnoredPeer]:
    """返回该账号当前的所有忽略 peer，按 added_at 倒序。"""
    await _ensure_account(db, aid)
    rows = (
        await db.execute(
            select(IgnoredPeer)
            .where(IgnoredPeer.account_id == aid)
            .order_by(IgnoredPeer.added_at.desc(), IgnoredPeer.id.desc())
        )
    ).scalars().all()
    return list(rows)


async def add_ignored(
    db: AsyncSession, aid: int, payload: IgnoredPeerCreate
) -> IgnoredPeer:
    """加入忽略名单；同 (account_id, peer_id) 已存在则返回原行（幂等）。"""
    await _ensure_account(db, aid)

    # 先查一遍——多数情况下用户点过一次"加入忽略"就不会重复，不必走异常路径
    existing = (
        await db.execute(
            select(IgnoredPeer).where(
                IgnoredPeer.account_id == aid,
                IgnoredPeer.peer_id == payload.peer_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = IgnoredPeer(
        account_id=aid,
        peer_id=payload.peer_id,
        peer_kind=payload.normalized_kind(),
        peer_label=payload.peer_label,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        # 并发情况下 UNIQUE 约束兜底：回滚后再次查询返回那一行
        await db.rollback()
        row = (
            await db.execute(
                select(IgnoredPeer).where(
                    IgnoredPeer.account_id == aid,
                    IgnoredPeer.peer_id == payload.peer_id,
                )
            )
        ).scalar_one()
    return row


async def remove_ignored(db: AsyncSession, aid: int, ignored_id: int) -> None:
    """删除忽略名单中的一行；找不到抛 404。"""
    await _ensure_account(db, aid)
    row = (
        await db.execute(
            select(IgnoredPeer).where(
                IgnoredPeer.id == ignored_id,
                IgnoredPeer.account_id == aid,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise _err("IGNORED_PEER_NOT_FOUND", "忽略项不存在", 404)
    await db.execute(
        delete(IgnoredPeer).where(
            IgnoredPeer.id == ignored_id,
            IgnoredPeer.account_id == aid,
        )
    )


# ── IPC 通知 ─────────────────────────────────────────────────────
async def notify_reload(aid: int) -> None:
    """忽略名单变更后通知 worker 重拉。Redis 不可达静默吞掉。"""
    try:
        redis = get_redis()
        await redis.publish(cmd_channel(aid), make_cmd(CMD_RELOAD_IGNORED))
    except Exception:  # noqa: BLE001
        log.debug("通知 worker reload_ignored 失败 aid=%s", aid, exc_info=True)


# ── RPC：拉最近活跃 peers ────────────────────────────────────────
async def fetch_recent(aid: int) -> tuple[bool, list[dict[str, Any]]]:
    """通过 Redis pub/sub 做一次性 RPC。

    返回 ``(worker_alive, items)``：
    - ``worker_alive=True``  且 ``items=[...]`` → worker 正常应答，可能为空也可能有数据
    - ``worker_alive=False`` 且 ``items=[]``    → 超时 / Redis 故障 / 任何异常

    流程：
    1. 主进程生成一个一次性 ``reply_to`` 频道名（含随机串），订阅它
    2. 向 ``worker_cmd:{aid}`` 发布 ``get_recent_peers``，payload 带上 ``reply_to``
    3. 等待 ``_RECENT_PEERS_TIMEOUT`` 内 worker 的应答；超时即视为 worker 不在跑
    4. 一定退订并关 pubsub，避免连接泄漏
    """
    reply_channel = f"worker_reply:{aid}:recent_peers:{secrets.token_hex(8)}"
    try:
        redis = get_redis()
    except Exception:  # noqa: BLE001
        return False, []

    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(reply_channel)
        # 订阅完成后再发请求，否则有竞态：worker 可能在我们订阅之前就回包了
        await redis.publish(
            cmd_channel(aid),
            make_cmd(CMD_GET_RECENT_PEERS, reply_to=reply_channel),
        )

        deadline = asyncio.get_event_loop().time() + _RECENT_PEERS_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return False, []
            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining),
                    timeout=remaining,
                )
            except TimeoutError:
                return False, []
            if msg is None:
                continue
            if msg.get("type") != "message":
                continue
            try:
                payload = IPCMessage.decode(msg["data"]).payload
            except Exception:  # noqa: BLE001
                return False, []
            items = payload.get("items") or []
            if not isinstance(items, list):
                return True, []
            return True, items
    except Exception:  # noqa: BLE001
        return False, []
    finally:
        try:
            await pubsub.unsubscribe(reply_channel)
        except Exception:  # noqa: BLE001
            pass
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            try:
                # 老版本 redis-py 用 close()，新版本用 aclose()
                await pubsub.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "add_ignored",
    "fetch_recent",
    "list_ignored",
    "notify_reload",
    "remove_ignored",
]

===== backend/app/services/llm_client.py =====
"""LLM provider 抽象 —— OpenAI / Anthropic / (占位) Ollama。

设计要点：
- 每个 provider 实现 ``LLMClient`` 接口；``complete`` 返 ``LLMResult``
- ``build_client`` 根据 ``LLMProvider`` ORM 行解密 api_key 并装配具体实现
- **安全红线**：解密后的 api_key 仅留在 client 实例内；不打 log，不 audit；
  错误路径用 ``_safe_error_message`` 兜底剥离任何含 sk-/secret-/Bearer 字样

调用入口在 worker 进程 (``worker/command.py:_run_ai``)，所以这里 httpx 调用是 async。

V1 仅实现 openai/anthropic 两类常用接口；ollama 走 OpenAI-compatible 端点（``/v1/chat/completions``）由 OpenAIClient 复用。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from ..crypto import decrypt_str
from ..db.models.command import (
    LLM_API_FORMAT_ANTHROPIC_MESSAGES,
    LLM_API_FORMAT_CHAT_COMPLETIONS,
    LLM_API_FORMAT_RESPONSES,
    LLM_PROVIDER_OLLAMA,
    LLMProvider,
    default_api_format_for,
)

# 默认调用超时；prompt 较长 / TG 端用户体验角度都不宜过长
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass
class LLMResult:
    """LLM 调用的统一结果。"""

    text: str           # 模型回答正文
    model: str          # 实际使用的模型名（便于 TG 内回显）
    input_tokens: int   # 入 tokens；若供应商不返就给 0
    output_tokens: int  # 出 tokens；若供应商不返就给 0


class LLMClient(ABC):
    """provider-agnostic 调用接口。"""

    @abstractmethod
    async def complete(self, system: str, user: str, max_tokens: int = 512) -> LLMResult:
        """以 system + user 拼 prompt，返回回答与 token 统计。"""
        raise NotImplementedError


# ────────────────────────────────────────────────────────────
# OpenAI / OpenAI 兼容（含 Ollama）
# ────────────────────────────────────────────────────────────


class OpenAIClient(LLMClient):
    """OpenAI Chat Completions 兼容协议。

    用 ``/v1/chat/completions`` 端点；Ollama (``/v1/chat/completions`` since 0.1.20+) 也走这里。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None,
        model: str,
        proxy_url: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._model = model
        self._proxy_url = proxy_url

    async def complete(self, system: str, user: str, max_tokens: int = 512) -> LLMResult:
        url = f"{self._base_url}/chat/completions"
        # Ollama 部署可能不需要 api_key；为空时不下发 Authorization 头
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        }
        # httpx 0.28+ 用 proxy=<str> 单参数；socks5 需要 httpx[socks] 安装的 socksio
        client_kwargs: dict[str, object] = {"timeout": _HTTP_TIMEOUT}
        if self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url
        try:
            async with httpx.AsyncClient(**client_kwargs) as cli:
                resp = await cli.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            # 很多 httpx 异常 str() 是空（典型 SSL 握手 / ConnectError("")）；
            # 把异常类名 + 目标 host 也透出来，否则用户只看到 "网络异常: " 没法排查
            raise LLMError(
                _safe_error_message(
                    _describe_http_error(exc, self._base_url),
                    self._api_key,
                )
            ) from None
        if resp.status_code >= 400:
            # 不要把 api_key 回显到错误里；构造前先剥离
            raise LLMError(
                _safe_error_message(
                    f"OpenAI 接口返回 {resp.status_code}: {resp.text[:200]}{_hint_for_status(resp.status_code)}",
                    self._api_key,
                )
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMError(f"OpenAI 返回非 JSON: {exc}") from None

        # 标准 OpenAI 形态：choices[0].message.content
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"OpenAI 返回结构异常: {exc}") from None

        usage = data.get("usage") or {}
        return LLMResult(
            text=text.strip(),
            model=str(data.get("model", self._model)),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )


# ────────────────────────────────────────────────────────────
# Anthropic Messages API
# ────────────────────────────────────────────────────────────


class AnthropicClient(LLMClient):
    """Anthropic ``/v1/messages`` 协议（Claude 系列）。"""

    # 文档要求的版本头；新版本兼容旧调用
    _ANTHROPIC_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: str,
        base_url: str | None,
        model: str,
        proxy_url: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = (base_url or "https://api.anthropic.com/v1").rstrip("/")
        self._model = model
        self._proxy_url = proxy_url

    async def complete(self, system: str, user: str, max_tokens: int = 512) -> LLMResult:
        url = f"{self._base_url}/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        client_kwargs: dict[str, object] = {"timeout": _HTTP_TIMEOUT}
        if self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url
        try:
            async with httpx.AsyncClient(**client_kwargs) as cli:
                resp = await cli.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            # 见 OpenAIClient 同名分支的注释——把异常类名 + host 也透出来
            raise LLMError(
                _safe_error_message(
                    _describe_http_error(exc, self._base_url),
                    self._api_key,
                )
            ) from None
        if resp.status_code >= 400:
            raise LLMError(
                _safe_error_message(
                    f"Anthropic 接口返回 {resp.status_code}: {resp.text[:200]}{_hint_for_status(resp.status_code)}",
                    self._api_key,
                )
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMError(f"Anthropic 返回非 JSON: {exc}") from None

        # content 是数组，每项 {type, text}；只取 text 拼接
        text_parts: list[str] = []
        for blk in data.get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "text":
                t = blk.get("text") or ""
                if t:
                    text_parts.append(t)
        text = "".join(text_parts).strip()
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            model=str(data.get("model", self._model)),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )


# ────────────────────────────────────────────────────────────
# OpenAI Responses API（POST /responses，2024 出的新协议）
# ────────────────────────────────────────────────────────────


class ResponsesClient(LLMClient):
    """OpenAI Responses API（POST ``/responses``）。

    与 chat/completions 的差异：
    - 入参 ``input=[{role, content}]`` + ``instructions`` + ``model`` + ``max_output_tokens``
    - 出参 ``output=[{type:"message", content:[{type:"output_text", text:"..."}]}]``
      也可能直接给 ``output_text`` 顶层字符串（不同实现略有差异，都做兼容）
    - usage 字段是 ``input_tokens`` / ``output_tokens``（不是 prompt_tokens / completion_tokens）

    很多国内 OpenAI 兼容反代（如 anyrouter）只接 ``/responses`` 不接 ``/chat/completions``，
    所以这条 client 是必须的。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None,
        model: str,
        proxy_url: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._model = model
        self._proxy_url = proxy_url

    async def complete(self, system: str, user: str, max_tokens: int = 512) -> LLMResult:
        url = f"{self._base_url}/responses"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {
            "model": self._model,
            # 用 instructions 字段传 system；input 列表按 role/content 给 user 输入
            "instructions": system,
            "input": [
                {"role": "user", "content": user},
            ],
            # Responses API 用 max_output_tokens（不是 max_tokens）
            "max_output_tokens": max_tokens,
        }

        client_kwargs: dict[str, object] = {"timeout": _HTTP_TIMEOUT}
        if self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url
        try:
            async with httpx.AsyncClient(**client_kwargs) as cli:
                resp = await cli.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(
                _safe_error_message(
                    _describe_http_error(exc, self._base_url),
                    self._api_key,
                )
            ) from None

        if resp.status_code >= 400:
            raise LLMError(
                _safe_error_message(
                    f"Responses 接口返回 {resp.status_code}: {resp.text[:200]}{_hint_for_status(resp.status_code)}",
                    self._api_key,
                )
            )

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMError(f"Responses 返回非 JSON: {exc}") from None

        # 解析 output：兼容多种形态
        # 形态 1：data["output_text"] = "..."（部分实现的便利字段）
        # 形态 2：data["output"] = [{"type":"message","content":[{"type":"output_text","text":"..."}]}]
        text = ""
        ot = data.get("output_text")
        if isinstance(ot, str):
            text = ot
        else:
            output_list = data.get("output") or []
            text_parts: list[str] = []
            for item in output_list if isinstance(output_list, list) else []:
                if not isinstance(item, dict):
                    continue
                content = item.get("content") or []
                if isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        t = c.get("text")
                        # type 通常是 output_text；保险起见全收
                        if isinstance(t, str):
                            text_parts.append(t)
            text = "".join(text_parts)

        # usage：input_tokens / output_tokens
        usage = data.get("usage") or {}
        return LLMResult(
            text=text.strip(),
            model=str(data.get("model", self._model)),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )


# ────────────────────────────────────────────────────────────
# 工厂 & 安全工具
# ────────────────────────────────────────────────────────────


class LLMError(Exception):
    """LLM 调用层统一异常；message 已脱敏。"""


def _safe_error_message(msg: str, api_key: str | None) -> str:
    """把可能含敏感信息的错误文本脱敏。

    - 若 api_key 出现在 msg 中，整段替换为 ``<redacted>``
    - 兜底过滤 ``sk-...`` / ``Bearer ...`` 形态
    """
    if not msg:
        return ""
    out = msg
    if api_key:
        out = out.replace(api_key, "<redacted>")
    # 统一截断，避免长串敏感数据透出
    if len(out) > 400:
        out = out[:400] + "..."
    return out


# Cloudflare 5xx 错误码的人话翻译（用户最常碰到 520，且不是应用问题）
_CF_5XX_HINTS: dict[int, str] = {
    520: "上游返回异常（Cloudflare 520 = 反代连不上目标 / 上游崩了；不是本项目代码问题）",
    521: "上游服务器拒绝连接（Cloudflare 521）",
    522: "上游连接超时（Cloudflare 522）",
    523: "上游不可达（Cloudflare 523）",
    524: "上游处理超时（Cloudflare 524；常见于慢模型 + 反代严格超时）",
    525: "SSL 握手失败（Cloudflare 525）",
    526: "SSL 证书无效（Cloudflare 526）",
}


def _hint_for_status(status: int) -> str:
    """根据 HTTP 状态码给一句人话提示，便于用户区分"我配错了"还是"反代/上游挂了"。"""
    if status in _CF_5XX_HINTS:
        return f"  ↳ {_CF_5XX_HINTS[status]}"
    if status == 401 or status == 403:
        return "  ↳ api_key 无效 / 权限不够"
    if status == 404:
        return "  ↳ model 名不对 / 端点不存在；试试 Fetch 模型列表选一条已支持的"
    if status == 429:
        return "  ↳ 限流，等会儿再试 / 或换一条不那么紧的反代"
    if 500 <= status < 600:
        return "  ↳ 服务器侧错误（不是 api_key / model 问题）"
    return ""


def _describe_http_error(exc: BaseException, base_url: str | None) -> str:
    """把 httpx 异常翻译成"用户能看懂的报错"。

    httpx 很多异常的 ``str(exc)`` 是空字符串（``ConnectError("")`` / SSL 握手错），
    单纯透 ``f"网络异常: {exc}"`` 会变成 "网络异常: " 难以排查。这里：

    - 总带上异常类名：``ConnectError`` / ``ReadTimeout`` / ``ProxyError`` / ``SSLError`` 等
    - 总带上目标 host（不带路径）：让用户一眼看出是 anthropic.com 还是 openai.com 不通
    - 细节为空时给一个建议性提示（"可能是 SSL/DNS/代理"）
    """
    name = type(exc).__name__
    detail = str(exc).strip()
    host = ""
    if base_url:
        try:
            from urllib.parse import urlparse

            host = urlparse(base_url).netloc or base_url
        except Exception:  # noqa: BLE001
            host = base_url

    parts = [f"网络异常 {name}"]
    if host:
        parts.append(f"→ {host}")
    if detail:
        parts.append(f": {detail}")
    else:
        parts.append("（无详情；常见原因：连不到目标域名 / SSL 握手失败 / 代理未生效）")
    return " ".join(parts)


def build_client(
    provider_row: LLMProvider,
    override_model: str | None = None,
    proxy_url: str | None = None,
) -> LLMClient:
    """根据 ORM 行装配具体 LLMClient。

    协议路由（以 ``api_format`` 为准；老数据没这字段时按 ``provider`` 厂商兜底）：
    - ``chat_completions``     → ``OpenAIClient``        ``POST /chat/completions``
    - ``responses``            → ``ResponsesClient``     ``POST /responses``
    - ``anthropic_messages``   → ``AnthropicClient``     ``POST /messages``

    - 解密 api_key（若该 provider 行没有 key 字段则 client 拿空串）
    - ``override_model`` 优先于 provider.default_model
    - ``proxy_url`` 给 None 表示直连；socks5/http/https 都接受 httpx URL
    """
    api_key = ""
    if provider_row.api_key_enc:
        api_key = decrypt_str(provider_row.api_key_enc)
    model = (override_model or provider_row.default_model or "").strip()
    if not model:
        raise ValueError("LLM provider 没配 default_model，且当次调用也未提供 model 覆盖")

    # api_format 优先；老数据兼容（无字段时按 provider 厂商兜底）
    fmt = (
        getattr(provider_row, "api_format", None)
        or default_api_format_for(provider_row.provider)
    )

    if fmt == LLM_API_FORMAT_CHAT_COMPLETIONS:
        # ollama 兜底 base_url（chat_completions 也兼容）
        base = provider_row.base_url
        if not base and provider_row.provider == LLM_PROVIDER_OLLAMA:
            base = "http://localhost:11434/v1"
        return OpenAIClient(
            api_key="" if provider_row.provider == LLM_PROVIDER_OLLAMA else api_key,
            base_url=base,
            model=model,
            proxy_url=proxy_url,
        )
    if fmt == LLM_API_FORMAT_RESPONSES:
        return ResponsesClient(
            api_key=api_key, base_url=provider_row.base_url, model=model, proxy_url=proxy_url
        )
    if fmt == LLM_API_FORMAT_ANTHROPIC_MESSAGES:
        return AnthropicClient(
            api_key=api_key, base_url=provider_row.base_url, model=model, proxy_url=proxy_url
        )
    raise ValueError(f"未知 api_format: {fmt}")


__all__ = [
    "AnthropicClient",
    "LLMClient",
    "LLMError",
    "LLMResult",
    "OpenAIClient",
    "ResponsesClient",
    "build_client",
]

===== backend/app/services/llm_format.py =====
"""消息格式渲染器：把 ``output_template`` + 上下文 → 最终 TG 消息字符串。

设计：
- **占位符** ``{key}``       — 直接替换；未知 key 留空（不抛 KeyError）
- **条件块** ``{?key}...{/?}`` — 仅当 ``ctx[key]`` 是真值（非空字符串/非零）时渲染括号内
- **派生变量**：``answer_first_2`` / ``answer_rest`` ——把 ``answer`` 切成"前 2 行 + 剩余"，
  让用户用 ``{answer_first_2}`` + ``<blockquote expandable>{answer_rest}</blockquote>``
  实现 alma 风的"前两行 + 折叠"
- **格式转义**：默认对所有占位符**值**做对应格式的转义；模板字面 markdown / HTML 标签不动
- **截断**：最终输出截到 4000 字符（TG 单条上限 4096，留余量）

关于 parse_mode 选择
=====================

Telethon 1.36 的 ``sanitize_parse_mode`` 只接受 ``md`` / ``markdown`` / ``html`` 字符串，
**不接受 ``markdownv2``**。所以我们没法直接走 Telegram Bot API 风的 MDV2——之前传
``markdownv2`` 会让 telethon 抛 ValueError，最终消息以纯文本发出，反斜杠原样显示。

因此默认走 **HTML**：telethon 内置完整支持，且 ``<blockquote expandable>`` 直接对应
"折叠引用块"的官方实现，比 MDV2 的 ``**>...**`` 更好控制。
"""

from __future__ import annotations

import re
import time as _time
from typing import Any

# 输出最大字符数；TG 单条上限 4096，预留缓冲
_MAX_OUTPUT_CHARS = 4000

# {?key}...{/?} 条件块
_COND_BLOCK_RE = re.compile(r"\{\?(\w+)\}(.*?)\{/\?\}", re.DOTALL)

# {key} 占位符（不含 { } / ? / ; 等特殊字符）
# 注意：要避开 {?key} 形式（前缀含 ?），所以这里负向先行
_PLACEHOLDER_RE = re.compile(r"\{(?!\?)(\w+)\}")

# Telegram MarkdownV2 必须转义的字符（来自 Bot API 文档）
# 仅在 escape_format='mdv2' 时使用；HTML 模式下不需要
_MDV2_SPECIAL_CHARS = set("_*[]()~`>#+-=|{}.!\\")


def _escape_mdv2(text: str) -> str:
    """对单个值做 Telegram MarkdownV2 转义（仅 escape_format='mdv2' 时用）。

    每个特殊字符前加反斜杠。空字符串原样返回。
    """
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        if ch in _MDV2_SPECIAL_CHARS:
            out.append("\\")
        out.append(ch)
    return "".join(out)


def _escape_html(text: str) -> str:
    """对单个值做 Telegram HTML 转义（默认转义模式）。

    Telegram HTML 只识别这三个特殊字符：& < >
    其它字符（包括 _ * 等 markdown 字符）都不会被解析为格式，所以模板里
    可以直接写 ``<b>{model}</b>``，{model} 的值里有 _ 也不会被搞乱。
    """
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _split_first_n_lines(text: str, n: int) -> tuple[str, str]:
    """把 ``text`` 按 ``\\n`` 切成 ``(前 n 行, 剩余)``，剩余包括行间的 ``\\n``。

    - 不足 n 行时：``(原文, "")``
    - 行尾不带 ``\\n``（用 ``\\n`` 拼回）
    """
    if not text:
        return "", ""
    lines = text.splitlines()
    if len(lines) <= n:
        return text, ""
    head = "\n".join(lines[:n])
    rest = "\n".join(lines[n:])
    return head, rest


def _is_truthy(v: Any) -> bool:
    """条件块判断：None / 空串 / 0 / False / 空 list 都视为假。"""
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, (list, dict, tuple)):
        return len(v) > 0
    return bool(v)


def _build_full_ctx(ctx: dict[str, Any], escape_format: str | None) -> dict[str, str]:
    """把入参 ``ctx`` 加工成"全字符串、转义后"的最终上下文。

    - 数值 / None 转字符串
    - 自动追加派生变量：``answer_first_2`` / ``answer_rest`` / ``display_input``、
      ``time``（若没传）
    - escape_format=``'html'`` 时走 HTML 转义；``'mdv2'`` 时走 MarkdownV2 转义；
      ``None`` 不转义（plain / markdown_v1 模式都用 None）
    """
    full: dict[str, Any] = dict(ctx)

    # 派生变量：answer 切前 2 行 + 剩余
    answer = str(full.get("answer", "") or "")
    a_head, a_rest = _split_first_n_lines(answer, 2)
    full.setdefault("answer_first_2", a_head)
    full.setdefault("answer_rest", a_rest)

    # 派生变量：display_input = quoted（被回复消息）或 question（用户跟在命令后的文字）
    # 用途：用户写 ``,ai 测试`` 时 quoted 为空、question="测试"；
    #       用户**回复某条消息**写 ``,ai 总结一下`` 时 quoted=被回消息正文、question="总结一下"。
    # 引用风模板想"无论哪种情况都把用户的输入显示在引用块里"——用这个派生变量最简单。
    quoted = str(full.get("quoted", "") or "").strip()
    question = str(full.get("question", "") or "").strip()
    full.setdefault("display_input", quoted or question)

    # 派生变量：time 默认当前 HH:MM
    full.setdefault("time", _time.strftime("%H:%M"))

    # 全部转字符串（None → ""）
    str_ctx: dict[str, str] = {}
    for k, v in full.items():
        if v is None:
            str_ctx[k] = ""
        elif isinstance(v, bool):
            # bool 是 int 子类，单独处理避免 True/False → "1"/"0"
            str_ctx[k] = "true" if v else ""
        else:
            str_ctx[k] = str(v)

    if escape_format == "html":
        str_ctx = {k: _escape_html(v) for k, v in str_ctx.items()}
    elif escape_format == "mdv2":
        str_ctx = {k: _escape_mdv2(v) for k, v in str_ctx.items()}
    return str_ctx


def render_output(
    template: str,
    ctx: dict[str, Any],
    *,
    escape_format: str | None = "html",
) -> str:
    """按 ``template`` + ``ctx`` 渲染最终消息。

    Args:
        template:       用户配的输出模板字符串
        ctx:            原始上下文 dict（answer / quoted / model / in_tokens / ...）
        escape_format:  ``'html'``（默认；转 ``& < >``）/ ``'mdv2'``（转 MDV2 所有特殊字符）/
                        ``None``（不转义；用于 plain 与 markdown_v1）

    Returns:
        最终消息字符串（已截到 4000 字符上限）
    """
    if not template:
        return ""

    full_ctx = _build_full_ctx(ctx, escape_format=escape_format)

    # 1) 先处理条件块：判断真假用**未转义**的原始 ctx，但渲染括号内仍用最终上下文
    raw_ctx = dict(ctx)
    answer_raw = str(raw_ctx.get("answer", "") or "")
    a_head, a_rest = _split_first_n_lines(answer_raw, 2)
    raw_ctx.setdefault("answer_first_2", a_head)
    raw_ctx.setdefault("answer_rest", a_rest)
    # 同 _build_full_ctx：display_input = quoted 或 question
    quoted_raw = str(raw_ctx.get("quoted", "") or "").strip()
    question_raw = str(raw_ctx.get("question", "") or "").strip()
    raw_ctx.setdefault("display_input", quoted_raw or question_raw)
    raw_ctx.setdefault("time", _time.strftime("%H:%M"))

    def _replace_cond(m: re.Match[str]) -> str:
        key = m.group(1)
        body = m.group(2)
        return body if _is_truthy(raw_ctx.get(key)) else ""

    expanded = _COND_BLOCK_RE.sub(_replace_cond, template)

    # 2) 再替换普通占位符 {key}（用最终上下文，含转义后的值）
    def _replace_ph(m: re.Match[str]) -> str:
        key = m.group(1)
        return full_ctx.get(key, "")

    rendered = _PLACEHOLDER_RE.sub(_replace_ph, expanded)

    # 3) 截断到 TG 单条上限以内
    return rendered[:_MAX_OUTPUT_CHARS]


# ────────────────────────────────────────────────────────────
# 预设（前端"快捷预设"按钮直接填进 textarea）
# 注意：HTML 模式下默认；这些字符串里的 <b> <blockquote> 等是字面 HTML，
# 渲染时**只**对占位符值做 HTML 转义，模板自身的标签保留。
# ────────────────────────────────────────────────────────────

# A. 简洁（默认）：纯文本风，任何 parse_mode 下都好看
PRESET_SIMPLE = (
    "{answer}\n\n"
    "— {model} · in {in_tokens} / out {out_tokens}"
    "{?routing_note}  ·  {routing_note}{/?}"
)

# B. 引用风（HTML 版）：alma 截图风格；前 2 行 + 折叠引用块
# 用 ``{display_input}`` 派生变量——它在"用户回复某条消息"时取被回消息正文，
# 在"用户直接发命令"时取命令后跟的问题文本。统一覆盖两种场景。
# footer 走精简风：模型 · 提供商 / In·Out·Total / 路由说明（仅 auto 模式）
PRESET_QUOTE = (
    "{?display_input}<blockquote>{display_input}</blockquote>\n"
    "{/?}<b>✨ AI 回答</b>\n"
    "{answer_first_2}"
    "{?answer_rest}\n<blockquote expandable>{answer_rest}</blockquote>{/?}\n\n"
    "━━━━━━━━━━━━━━━\n"
    "{model} · {provider}\n"
    "In: {in_tokens} | Out: {out_tokens} | Total: {total_tokens}"
    "{?routing_note}\n{routing_note}{/?}"
)

# C. 极简：答案 + 一行模型 / token 标签
PRESET_MINIMAL = "{answer}\n<code>{model}</code> · {total_tokens}t"

# D. 翻译/简答风：不显示引用（即使 quote_replied=True 仅供模型上下文，UI 不重复展示）
#   适合 ``,翻译`` / ``,简答`` 等命令——用户只想看答案，不想看自己/对方原文复读
PRESET_TRANSLATE = (
    "{answer}\n\n"
    "<i>— {model}</i>"
)

PRESETS: dict[str, str] = {
    "simple": PRESET_SIMPLE,
    "quote": PRESET_QUOTE,
    "minimal": PRESET_MINIMAL,
    "translate": PRESET_TRANSLATE,
}

# 默认模板（cfg.output_template 没设时使用）
DEFAULT_TEMPLATE = PRESET_SIMPLE


# ────────────────────────────────────────────────────────────
# 占位符元数据（前端用来渲染"占位符按钮 + 中文释义"）
# ────────────────────────────────────────────────────────────

PLACEHOLDER_META: list[dict[str, str]] = [
    {"key": "answer", "label": "[回答]", "desc": "AI 的回答正文"},
    {"key": "answer_first_2", "label": "[回答-前2行]", "desc": "回答的前 2 行（折叠用）"},
    {"key": "answer_rest", "label": "[回答-剩余]", "desc": "回答从第 3 行起（配 <blockquote expandable> 折叠）"},
    {"key": "display_input", "label": "[输入]", "desc": "用户的输入：被回复消息正文（优先）/ 没有则用问题"},
    {"key": "question", "label": "[问题]", "desc": "用户在命令后跟的问题文本"},
    {"key": "quoted", "label": "[被引用]", "desc": "被回复消息的正文（仅用户回复某条消息时才有）"},
    {"key": "model", "label": "[模型]", "desc": "API 实际返回的模型名"},
    {"key": "provider", "label": "[提供商]", "desc": "提供商名称（如 Any GPT）"},
    {"key": "provider_kind", "label": "[厂商]", "desc": "openai / anthropic / ollama"},
    {"key": "in_tokens", "label": "[输入tokens]", "desc": "输入 token 数"},
    {"key": "out_tokens", "label": "[输出tokens]", "desc": "输出 token 数"},
    {"key": "total_tokens", "label": "[总tokens]", "desc": "输入 + 输出"},
    {"key": "routing_note", "label": "[路由说明]", "desc": "auto 模式的决策原因（fixed 模式空）"},
    {"key": "time", "label": "[时间]", "desc": "当前时间 HH:MM"},
]

# 条件块元数据（条件块语法略不同，UI 单独一组按钮）
CONDITIONAL_META: list[dict[str, str]] = [
    {
        "key": "display_input",
        "label": "[条件:有输入]",
        "desc": "仅当用户有输入（被回复消息或命令后问题）才渲染；用作引用框最常见",
        "snippet": "{?display_input}\n\n{/?}",
    },
    {
        "key": "quoted",
        "label": "[条件:被引用]",
        "desc": "仅当被回复消息非空才渲染括号内（用户必须回复某条消息）",
        "snippet": "{?quoted}\n\n{/?}",
    },
    {
        "key": "routing_note",
        "label": "[条件:路由]",
        "desc": "仅 auto 模式才渲染括号内",
        "snippet": "{?routing_note}\n\n{/?}",
    },
    {
        "key": "answer_rest",
        "label": "[条件:有剩余]",
        "desc": "仅当回答超过 2 行才渲染（配折叠块用）",
        "snippet": "{?answer_rest}\n<blockquote expandable>{answer_rest}</blockquote>{/?}",
    },
]


__all__ = [
    "CONDITIONAL_META",
    "DEFAULT_TEMPLATE",
    "PLACEHOLDER_META",
    "PRESETS",
    "PRESET_MINIMAL",
    "PRESET_QUOTE",
    "PRESET_SIMPLE",
    "PRESET_TRANSLATE",
    "render_output",
]

===== backend/app/services/llm_router.py =====
"""LLM 自动路由：按用户消息特征选最合适的 provider。

设计目标
========

让一条 ``,ai`` 命令支持两种工作模式：

- ``fixed``  固定 provider —— 老行为，绑死某个 provider_id
- ``auto``   自动路由 —— 看消息内容自动挑 provider

路由策略（优先级从高到低）
--------------------------

1. **视觉/多模态**：消息含图（被引用消息有 photo）或关键词（识别图/这张图/截图） → 选 modality∈{vision,multimodal} 的 provider
2. **代码**：消息含 ```...``` 代码块 / def / function / class / import 等关键 token → 选 tag=code
3. **数学**：消息含 ``=``、``\\frac``、连续数字与运算符高密度 → 选 tag=math
4. **翻译**：消息含 "翻译为/translate (to|into)/翻成" 等关键词 → 选 tag=translate
5. **长上下文**：原文 + 问题字符数 ≥ 阈值（默认 1500 chars）→ 选 tag=long_context（按 cost_tier 升序兜底）
6. **复杂推理/分析**：包含"为什么/分析/比较/推导/原因/对比"等推理 trigger → 选 tag∈{reason,smart}（旗舰）
7. **闲聊/通用**：以上都不命中 → 选 tag=chat 中 cost_tier 最低（最便宜的量产档）

如果以上规则都没命中候选（标签未配齐），可选启用「分类器兜底」：
调用 ``classifier_provider`` 让一个轻量小模型返回 enum，然后再按 enum 走 tag 匹配。
分类失败 / 没配 classifier → 用 ``fallback_provider_id``；再没有 → 用候选里第一个。

为什么把规则做成"全靠 tag 匹配"而不是硬编码 provider_id？
-- 用户在前端给 provider 打标签即可改动路由，不用改代码。

无副作用
--------
路由器是纯函数式（除非启用 classifier，那时只调一次 LLM 完成短文本分类）。
不读 DB、不写日志（决策原因通过 ``RoutingDecision.reason`` 返回，由调用方决定记不记）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


# ── 触发关键词（Unicode 字符串匹配；case-insensitive）─────────
# 设计：保守命中——只要消息里能找到任意一个关键词就视为命中类别。
# 避免误命中：每个类别选 ≤ 12 个高特征 token。
_VISION_KEYWORDS = (
    "识别图", "这张图", "看图", "图片里", "截图", "ocr",
    "describe the image", "what's in this image", "what is in this image",
    "图中", "图里",
)

_TRANSLATE_KEYWORDS = (
    "翻译为", "翻译成", "翻成", "译为", "translate to", "translate into",
    "translate this", "翻译一下", "中译英", "英译中", "japanese to chinese",
)

_REASON_KEYWORDS = (
    "为什么", "分析一下", "推理", "推导", "为何", "原因是", "比较一下",
    "对比", "解释清楚", "step by step", "step-by-step", "reason about",
    "why does", "why is", "explain why",
)

# 代码 token：行内 / 块内任一命中即认为含代码
_CODE_TOKEN_RE = re.compile(
    r"```|"                            # 围栏代码块
    r"\bdef\s+\w+\s*\(|"               # python 函数
    r"\bfunction\s+\w+\s*\(|"          # js 函数
    r"\bclass\s+\w+\s*[:({]|"          # 类
    r"\bimport\s+[a-zA-Z_]|"           # import
    r"#include\s+<|"                   # C/C++
    r"\bconsole\.log\(|"               # js
    r"\bprint\(|"                      # python/js print
    r"=>\s*\{",                        # arrow function body
    re.IGNORECASE,
)

# 数学 trigger：含 latex \frac / \int / 较高密度数字+运算符
_MATH_LATEX_RE = re.compile(r"\\(frac|sum|int|sqrt|times|cdot|forall|exists)\b")
_MATH_DENSITY_RE = re.compile(r"\d+\s*[+\-*/=^×÷]\s*\d+")

# 长上下文阈值（用户问题 + 被回复原文合计 chars）
_LONG_CONTEXT_CHARS = 1500


# ── 模态常量（不直接 import models，避免循环；与 db.models.command 对齐）─
_MOD_TEXT = "text"
_MOD_VISION = "vision"
_MOD_MULTIMODAL = "multimodal"


@dataclass
class RoutingDecision:
    """路由决策结果。"""

    provider_id: int
    """选中的 provider id。"""

    reason: str
    """决策原因（短字符串，写日志/审计用）。例：``"matched tag=code"``、``"fallback"``。"""

    matched_tag: str | None = None
    """命中的 tag（若是规则路由）；分类器兜底时也会填这里。"""


# ════════════════════════════════════════════════════════════
# 内部：候选过滤 / 评分
# ════════════════════════════════════════════════════════════


def _has_api_key(p: dict[str, Any]) -> bool:
    """provider 是否配了 api_key（除 ollama 本地外，没 key 的 provider 调不通）。"""
    if p.get("provider") == "ollama":
        # ollama 本地部署可不要 key
        return True
    return bool(p.get("api_key_enc"))


def _provider_tags(p: dict[str, Any]) -> set[str]:
    """取 provider.tags，允许字段缺失（老数据兼容）。"""
    raw = p.get("tags") or []
    if not isinstance(raw, list):
        return set()
    return {str(t).strip() for t in raw if isinstance(t, str) and t.strip()}


def _provider_modality(p: dict[str, Any]) -> str:
    return str(p.get("modality") or _MOD_TEXT)


def _cost_tier(p: dict[str, Any]) -> int:
    """取 cost_tier，缺省视为 2（中档）。"""
    v = p.get("cost_tier")
    try:
        return int(v) if v is not None else 2
    except (TypeError, ValueError):
        return 2


def _select_by_tag(
    candidates: list[dict[str, Any]],
    tag: str,
    *,
    prefer_cheap: bool = False,
    prefer_premium: bool = False,
) -> dict[str, Any] | None:
    """从候选中找拥有指定 tag 的 provider；按 cost_tier 排序选一个。

    - ``prefer_cheap=True``    cost_tier 升序（便宜优先；用于 chat / classify / cheap）
    - ``prefer_premium=True``  cost_tier 降序（旗舰优先；用于 reason / smart）
    - 都不指定：cost_tier 升序（默认便宜优先，省钱）
    """
    matched = [p for p in candidates if tag in _provider_tags(p)]
    if not matched:
        return None
    if prefer_premium:
        matched.sort(key=_cost_tier, reverse=True)
    else:
        # cheap / 默认 都是升序
        matched.sort(key=_cost_tier)
    return matched[0]


# ════════════════════════════════════════════════════════════
# 规则层
# ════════════════════════════════════════════════════════════


def _looks_like_vision_request(user_q: str, replied_text: str | None, has_replied_photo: bool) -> bool:
    if has_replied_photo:
        return True
    text = (user_q or "").lower()
    return any(k.lower() in text for k in _VISION_KEYWORDS)


def _looks_like_code(user_q: str, replied_text: str | None) -> bool:
    blob = f"{replied_text or ''}\n{user_q or ''}"
    return bool(_CODE_TOKEN_RE.search(blob))


def _looks_like_math(user_q: str, replied_text: str | None) -> bool:
    blob = f"{replied_text or ''}\n{user_q or ''}"
    if _MATH_LATEX_RE.search(blob):
        return True
    # 数字+运算符模式至少出现 2 次
    return len(_MATH_DENSITY_RE.findall(blob)) >= 2


def _looks_like_translate(user_q: str) -> bool:
    text = (user_q or "").lower()
    return any(k.lower() in text for k in _TRANSLATE_KEYWORDS)


def _looks_long_context(user_q: str, replied_text: str | None) -> bool:
    return len(user_q or "") + len(replied_text or "") >= _LONG_CONTEXT_CHARS


def _looks_like_reason(user_q: str) -> bool:
    text = (user_q or "").lower()
    return any(k.lower() in text for k in _REASON_KEYWORDS)


def _rule_route(
    user_q: str,
    replied_text: str | None,
    has_replied_photo: bool,
    candidates: list[dict[str, Any]],
) -> RoutingDecision | None:
    """走规则层，命中即返；没命中返回 None。"""

    # 1) 视觉：必须有 modality∈{vision,multimodal} 才匹配；没有就跳过此条规则
    if _looks_like_vision_request(user_q, replied_text, has_replied_photo):
        vis = [
            p for p in candidates
            if _provider_modality(p) in (_MOD_VISION, _MOD_MULTIMODAL)
        ]
        if vis:
            # 视觉里也按 cost_tier 升序选便宜的
            vis.sort(key=_cost_tier)
            return RoutingDecision(
                provider_id=int(vis[0]["id"]),
                reason="vision request → modality=vision/multimodal",
                matched_tag="vision",
            )
        # 没视觉模型就不匹配此条；继续看其他规则（文本路径）

    # 2) 代码
    if _looks_like_code(user_q, replied_text):
        p = _select_by_tag(candidates, "code", prefer_cheap=True)
        if p:
            return RoutingDecision(int(p["id"]), "matched tag=code", "code")

    # 3) 数学
    if _looks_like_math(user_q, replied_text):
        p = _select_by_tag(candidates, "math", prefer_cheap=True)
        if p:
            return RoutingDecision(int(p["id"]), "matched tag=math", "math")

    # 4) 翻译
    if _looks_like_translate(user_q):
        p = _select_by_tag(candidates, "translate", prefer_cheap=True)
        if p:
            return RoutingDecision(int(p["id"]), "matched tag=translate", "translate")

    # 5) 长上下文（用 cheap 兜底，不要旗舰浪费 token）
    if _looks_long_context(user_q, replied_text):
        p = _select_by_tag(candidates, "long_context", prefer_cheap=True)
        if p:
            return RoutingDecision(int(p["id"]), "matched tag=long_context", "long_context")

    # 6) 复杂推理 → smart / reason，premium 优先
    if _looks_like_reason(user_q):
        for tag in ("reason", "smart"):
            p = _select_by_tag(candidates, tag, prefer_premium=True)
            if p:
                return RoutingDecision(int(p["id"]), f"matched tag={tag}", tag)

    # 7) 通用闲聊 / 短问短答：chat 中最便宜
    p = _select_by_tag(candidates, "chat", prefer_cheap=True)
    if p:
        return RoutingDecision(int(p["id"]), "matched tag=chat (default short)", "chat")

    return None


# ════════════════════════════════════════════════════════════
# 分类器兜底（可选；调一个 classifier provider 让它返回 enum）
# ════════════════════════════════════════════════════════════


# 让分类器只回这几个 token；任何其它输出按 chat 处理
_CLASSIFIER_LABELS = ("code", "math", "translate", "vision", "reason", "chat")

_CLASSIFIER_SYSTEM = (
    "你是一个消息分类器。读用户消息，只回一个英文小写词标签，不要解释，不要标点。"
    "可选范围严格限定为：code / math / translate / vision / reason / chat。"
    "判断不准时回 chat。"
)


async def _ask_classifier(
    classifier_provider: dict[str, Any],
    user_q: str,
    replied_text: str | None,
) -> str | None:
    """调 classifier provider 返回一个 label；任何错误返回 None。"""
    # 局部 import 避免与 worker 启动顺序耦合
    from ..db.models.command import LLMProvider as LLMProviderModel
    from .llm_client import LLMError, build_client

    # 把 dict 反捏成一个临时 ORM 实例（不绑 session），与 worker.command._run_ai 同一手法
    fake_row = LLMProviderModel(
        id=int(classifier_provider.get("id") or 0),
        name=str(classifier_provider.get("name", "")),
        provider=str(classifier_provider.get("provider", "")),
        api_key_enc=classifier_provider.get("api_key_enc"),
        base_url=classifier_provider.get("base_url"),
        default_model=str(classifier_provider.get("default_model", "")),
    )
    # 把"原文 + 问题"压成短摘要送进去；max_tokens=8 防滥调
    blob = (replied_text or "")[:300] + "\n---\n" + (user_q or "")[:200]
    try:
        cli = build_client(fake_row, proxy_url=classifier_provider.get("proxy_url"))
        result = await cli.complete(_CLASSIFIER_SYSTEM, blob, max_tokens=8)
    except (LLMError, ValueError, Exception) as e:  # noqa: BLE001
        log.debug("classifier call failed: %s", type(e).__name__)
        return None

    label = (result.text or "").strip().lower().split()[0] if result.text else ""
    # 严格白名单
    if label in _CLASSIFIER_LABELS:
        return label
    return None


# ════════════════════════════════════════════════════════════
# 公共入口
# ════════════════════════════════════════════════════════════


async def pick_provider(
    user_q: str,
    replied_text: str | None,
    has_replied_photo: bool,
    providers: dict[int, dict[str, Any]],
    *,
    classifier_provider_id: int | None = None,
    fallback_provider_id: int | None = None,
) -> RoutingDecision:
    """根据消息内容挑一个 provider。

    Args:
        user_q: 用户的问题文本（``,ai`` 后跟的参数拼起来）
        replied_text: 被回复消息的原文（如果有），否则 None
        has_replied_photo: 被回复消息是否含图片（影响视觉路径）
        providers: ``{provider_id: provider_dict}`` 候选池（已含 api_key_enc 等）
        classifier_provider_id: 可选；启用分类器兜底时的 provider id
        fallback_provider_id: 可选；规则 + 分类器都无果时使用

    Returns:
        ``RoutingDecision``。即使空候选也会抛 ``ValueError`` 让上层把错误信息编辑回 TG，
        而不是静默选一个错的 provider。

    Raises:
        ValueError: 没有任何可用 provider（候选池空 / 全无 api_key）
    """
    # 仅留有 api_key 的 provider（ollama 例外）
    candidates = [p for p in providers.values() if _has_api_key(p)]
    if not candidates:
        raise ValueError("没有任何可用 provider（候选池为空或全部未配 api_key）")

    # 1) 规则层
    rule = _rule_route(user_q, replied_text, has_replied_photo, candidates)
    if rule is not None:
        return rule

    # 2) 分类器兜底（如果配置了）
    if classifier_provider_id is not None:
        cls_p = providers.get(int(classifier_provider_id))
        if cls_p is not None and _has_api_key(cls_p):
            label = await _ask_classifier(cls_p, user_q, replied_text)
            if label:
                p = _select_by_tag(candidates, label, prefer_cheap=(label != "reason"),
                                   prefer_premium=(label == "reason"))
                if p:
                    return RoutingDecision(
                        int(p["id"]),
                        f"classifier→tag={label}",
                        label,
                    )

    # 3) fallback_provider_id
    if fallback_provider_id is not None:
        fp = providers.get(int(fallback_provider_id))
        if fp is not None and _has_api_key(fp):
            return RoutingDecision(
                int(fp["id"]),
                "fallback (no rule/classifier match)",
                None,
            )

    # 4) 候选池里第一个（cost_tier 最低，省钱）
    candidates.sort(key=_cost_tier)
    p = candidates[0]
    return RoutingDecision(int(p["id"]), "fallback (first available)", None)


__all__ = [
    "RoutingDecision",
    "pick_provider",
]

===== backend/app/services/login_service.py =====
"""Telethon 多步登录状态机：start → code → 2fa → finalize。

核心难点：Telethon 的 ``auth_key`` 与 ``phone_code_hash`` 都挂在 ``TelegramClient`` 实例
内部，跨请求重建会丢失中间态。所以这里在主进程内存里保留同一个 client 实例（按
``login_token`` 索引），30 分钟未完成由后台清理。
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from ..crypto import decrypt_str, encrypt_bytes, encrypt_str
from ..db.models.account import (
    ACCOUNT_STATUS_ACTIVE,
    Account,
    HumanizeConfig,
    Proxy,
)
from ..redis_client import get_redis
from ..worker.ipc import GLOBAL_CHANNEL, make_cmd


# ── 进程内挂起登录态 ────────────────────────────────────────────
@dataclass
class _PendingLogin:
    """单个挂起登录会话（持有未完成绑定的 TelegramClient）。"""

    client: TelegramClient
    api_id: int
    # api_hash 仅驻内存到 finalize；不落盘除非加密
    api_hash: str
    phone: str
    phone_code_hash: str | None = None
    require_2fa: bool = False
    # 重新登录老账号场景才有 account_id；新建则为空
    account_id: int | None = None
    proxy_id: int | None = None
    # 启动绑定时选定的设备伪装 profile id；为空则用系统默认
    device_profile_id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# 所有挂起登录的全局表，key=login_token；TTL 30 分钟
_PENDING: dict[str, _PendingLogin] = {}
_PENDING_TTL = timedelta(minutes=30)
# 串行化对 _PENDING 的读写，避免并发请求踩到同一个 token
_LOCK = asyncio.Lock()


# ── 内部工具 ──────────────────────────────────────────────────────
def _err(code: str, message: str, status: int = 400) -> HTTPException:
    """构造统一格式的错误响应。"""
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _build_proxy_tuple(db: AsyncSession, proxy_id: int | None):
    """根据 proxy_id 构造 Telethon 所需的 proxy 元组。

    proxy_id 为空时，回落到 ``settings.tg_default_proxy`` 全局代理；
    仍未配置则真正直连（适用于宿主机能直连 TG 的网络）。
    """
    if not proxy_id:
        from ..util.proxy import get_default_proxy_tuple
        return get_default_proxy_tuple()
    proxy = await db.get(Proxy, proxy_id)
    if not proxy:
        from ..util.proxy import get_default_proxy_tuple
        return get_default_proxy_tuple()
    password = decrypt_str(proxy.password_enc) if proxy.password_enc else None
    return (
        proxy.type,        # "socks5" | "http" | "mtproxy"
        proxy.host,
        proxy.port,
        True,              # rdns（远端解析 DNS，避免泄漏）
        proxy.username,
        password,
    )


# ── 对外 API：状态机三步 + finalize ───────────────────────────────
async def start_login(
    db: AsyncSession,
    *,
    api_id: int,
    api_hash: str,
    phone: str,
    account_id: int | None = None,
    proxy_id: int | None = None,
    device_profile_id: int | None = None,
) -> str:
    """第 1 步：建 client → 连接 → 发验证码，返回 login_token。"""
    proxy_tuple = await _build_proxy_tuple(db, proxy_id)
    # 解析设备伪装：调用方指定 → 系统默认 → 硬编码兜底
    from .device_profile import get_by_id, get_default
    profile = None
    if device_profile_id is not None:
        profile = await get_by_id(db, device_profile_id)
    if profile is None:
        profile = await get_default(db)
    client = TelegramClient(
        StringSession(),
        api_id,
        api_hash,
        proxy=proxy_tuple,
        **profile.telethon_kwargs(),
    )
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except FloodWaitError as e:
        await _safe_disconnect(client)
        raise _err("FLOOD_WAIT", f"请求过于频繁，请等待 {e.seconds} 秒", 429) from e
    except PhoneNumberInvalidError as e:
        await _safe_disconnect(client)
        raise _err("PHONE_INVALID", "手机号无效") from e
    except Exception as e:  # noqa: BLE001
        # 其它错误（网络、API 凭据错等）也要先回收 client，再向上抛
        await _safe_disconnect(client)
        raise _err("LOGIN_START_FAILED", f"发起登录失败：{e}") from e

    token = secrets.token_urlsafe(24)
    async with _LOCK:
        _PENDING[token] = _PendingLogin(
            client=client,
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
            phone_code_hash=sent.phone_code_hash,
            account_id=account_id,
            proxy_id=proxy_id,
            device_profile_id=device_profile_id,
        )
    return token


async def confirm_code(token: str, code: str) -> tuple[bool, _PendingLogin]:
    """第 2 步：提交短信/Telegram 验证码。

    返回 ``(require_2fa, pending)``：
    - ``require_2fa=True``：账号启用了两步验证，需要继续走 ``confirm_2fa``。
    - ``require_2fa=False``：可以直接进入 finalize。
    """
    async with _LOCK:
        pending = _PENDING.get(token)
    if not pending:
        raise _err("LOGIN_TOKEN_EXPIRED", "登录会话已过期，请重新发起绑定")
    try:
        await pending.client.sign_in(
            phone=pending.phone,
            code=code,
            phone_code_hash=pending.phone_code_hash,
        )
    except SessionPasswordNeededError:
        # 账号启用了两步验证，停在此步等 2fa
        pending.require_2fa = True
        return True, pending
    except PhoneCodeInvalidError as e:
        raise _err("CODE_INVALID", "验证码错误") from e
    except PhoneCodeExpiredError as e:
        # 验证码过期视为整个会话作废，回收 client
        await _cleanup(token)
        raise _err("CODE_EXPIRED", "验证码已过期，请重新发起绑定") from e
    return False, pending


async def confirm_2fa(token: str, password: str) -> _PendingLogin:
    """第 3 步：提交两步验证密码。"""
    async with _LOCK:
        pending = _PENDING.get(token)
    if not pending:
        raise _err("LOGIN_TOKEN_EXPIRED", "登录会话已过期，请重新发起绑定")
    try:
        await pending.client.sign_in(password=password)
    except PasswordHashInvalidError as e:
        raise _err("PASSWORD_INVALID", "两步密码错误") from e
    return pending


async def finalize(db: AsyncSession, token: str, pending: _PendingLogin) -> int:
    """登录成功后落库 + 通知 supervisor 拉起 worker。返回 account_id。"""
    me = await pending.client.get_me()
    session_str = pending.client.session.save()

    # me.username 不含 @；可能为 None（用户未设置用户名）
    tg_user_id = getattr(me, "id", None)
    tg_username = getattr(me, "username", None) or None

    if pending.account_id is None:
        # 新建账号
        acc = Account(
            phone=pending.phone,
            display_name=(me.first_name or me.username or pending.phone),
            tg_user_id=tg_user_id,
            tg_username=tg_username,
            api_id_enc=encrypt_str(str(pending.api_id)),
            api_hash_enc=encrypt_str(pending.api_hash),
            session_enc=encrypt_bytes(session_str.encode()),
            status=ACCOUNT_STATUS_ACTIVE,
            proxy_id=pending.proxy_id,
            device_profile_id=pending.device_profile_id,
        )
        db.add(acc)
        await db.flush()
        # 默认拟人化配置（PRD §L.3 默认值由模型 default 提供）
        db.add(HumanizeConfig(account_id=acc.id))
        await db.commit()
        account_id = acc.id
    else:
        # 重新登录已有账号：替换 session、状态置回 active；顺手回填 tg 身份
        acc = await db.get(Account, pending.account_id)
        if not acc:
            raise _err("ACCOUNT_NOT_FOUND", "账号不存在", 404)
        acc.session_enc = encrypt_bytes(session_str.encode())
        acc.status = ACCOUNT_STATUS_ACTIVE
        if tg_user_id is not None:
            acc.tg_user_id = tg_user_id
        # username 即使为 None 也覆盖：用户可能在 TG 主动清掉了用户名
        acc.tg_username = tg_username
        # 重新登录时如果显式选了新的设备伪装，更新绑定（这次重登会用新的 profile 注册到 TG）
        if pending.device_profile_id is not None:
            acc.device_profile_id = pending.device_profile_id
        await db.commit()
        account_id = acc.id

    await _safe_disconnect(pending.client)

    # 通知 supervisor 拉起该 worker（B Agent 的 supervisor 监听 worker_global 频道）
    try:
        redis = get_redis()
        await redis.publish(GLOBAL_CHANNEL, make_cmd("start_worker", account_id=account_id))
    except Exception:  # noqa: BLE001
        # Redis 暂不可用不应阻塞登录成功落库；supervisor 启动时会扫表自动拉起
        pass

    await _cleanup(token)
    return account_id


# ── 内部状态维护 ──────────────────────────────────────────────────
async def _cleanup(token: str) -> None:
    """从挂起表移除一个 token 的状态。"""
    async with _LOCK:
        _PENDING.pop(token, None)


async def get_pending(token: str) -> _PendingLogin | None:
    """读取当前挂起态（只读）。"""
    async with _LOCK:
        return _PENDING.get(token)


async def _safe_disconnect(client: TelegramClient) -> None:
    """无论是否处于已连接状态，都尝试断开（异常吞掉）。"""
    try:
        await client.disconnect()
    except Exception:  # noqa: BLE001
        pass


async def cleanup_expired_loop() -> None:
    """主进程 lifespan 中 spawn 的后台守护任务：每 60s 清理一次过期 pending。"""
    while True:
        try:
            await asyncio.sleep(60)
            now = datetime.now(UTC)
            expired: list[_PendingLogin] = []
            async with _LOCK:
                for tok, p in list(_PENDING.items()):
                    if now - p.created_at > _PENDING_TTL:
                        expired.append(p)
                        _PENDING.pop(tok, None)
            for p in expired:
                await _safe_disconnect(p.client)
        except asyncio.CancelledError:
            # 进程退出时正常终止
            break
        except Exception:  # noqa: BLE001
            # 守护循环不能因为偶发异常而退出
            pass

===== backend/app/services/plugin_install_service.py =====
"""第三方插件 zip 安装 / 卸载 / 启停服务（阶段 B）。

主要职责：
- 校验 zip 完整性（含 ``manifest.py`` / ``__init__.py`` / ``plugin.py``）
- 解析 manifest.py 拿到 ``MANIFEST`` 实例（不会 import 到 app 命名空间，全在临时目录隔离）
- 可选 Ed25519 签名校验：``settings.plugin_pubkey`` 配置公钥 + 上传 ``.sig`` 文件
- 把临时目录原子地搬到 ``settings.plugins_installed_dir/<key>/``
- 在 ``plugin_install`` 表写一行（已存在则视为升级，写库覆盖）
- ``set_enabled`` / ``uninstall`` 工具函数

安全约束：
- zip 体积上限 ``settings.plugin_zip_max_bytes``
- 拒绝路径穿越（绝对路径、含 ``..`` 的成员）
- 拒绝与 builtin feature key 冲突
- 解压后任意单个成员失败都视作整体失败（解压前先校验完所有 names）
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.feature import BUILTIN_FEATURES
from ..db.models.plugin import (
    PLUGIN_SOURCE_ZIP,
    PluginInstall,
)
from ..settings import settings
from ..worker.plugins.manifest import Manifest

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# 错误类型
# ─────────────────────────────────────────────────────
class PluginInstallError(Exception):
    """插件安装期间所有可恢复错误的基类。``code`` 用于 API 层映射 HTTP 状态。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ZipTooLarge(PluginInstallError):
    pass


class InvalidZipStructure(PluginInstallError):
    pass


class ManifestError(PluginInstallError):
    pass


class KeyConflict(PluginInstallError):
    pass


class SignatureFailed(PluginInstallError):
    pass


# ─────────────────────────────────────────────────────
# zip 解析结果
# ─────────────────────────────────────────────────────
@dataclass
class ParsedPlugin:
    """临时目录里待落盘的插件包元数据。"""

    manifest: Manifest
    # 解压后的临时目录（成功安装后会被 move 走；失败时调用方负责清理）
    extract_dir: Path


# ─────────────────────────────────────────────────────
# 公共：解析 zip
# ─────────────────────────────────────────────────────
REQUIRED_FILES = ("manifest.py", "__init__.py", "plugin.py")


def parse_zip(zip_bytes: bytes) -> ParsedPlugin:
    """解析上传的 zip，返回 ``ParsedPlugin``（含临时解压目录与 Manifest）。

    临时解压目录的 owner 是调用方：成功安装后 ``install_zip`` 会把它 move 到正式位置；
    失败时本函数已经在 except 分支里清理；调用方仅在 ``install_zip`` 之外使用 ParsedPlugin
    时需要自己 ``shutil.rmtree(parsed.extract_dir)``。
    """
    if len(zip_bytes) > settings.plugin_zip_max_bytes:
        raise ZipTooLarge(
            "ZIP_TOO_LARGE",
            f"zip 体积超出 {settings.plugin_zip_max_bytes // 1024 // 1024} MiB 上限",
        )

    # 解压到一个唯一的临时目录（在系统临时目录下，install 成功后再搬到正式位置）
    extract_dir = Path(tempfile.mkdtemp(prefix="telebot-plugin-"))
    try:
        from io import BytesIO

        try:
            with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
                # 1) 路径穿越校验：拒绝绝对路径与含 ".." 的成员
                _validate_zip_members(zf)
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            raise InvalidZipStructure("BAD_ZIP", f"zip 不可读: {exc}") from exc

        # 2) 自动展开"单顶层目录"约定：不少打包器会以 ``mypkg/...`` 形式
        #    打包，于是真正内容在 ``extract_dir/mypkg``。如解压根只有一个子目录、
        #    且根目录没有 manifest.py，则把它当真正的根。
        root = _resolve_real_root(extract_dir)

        # 3) 必含文件检查
        for required in REQUIRED_FILES:
            if not (root / required).is_file():
                raise InvalidZipStructure(
                    "MISSING_REQUIRED_FILE",
                    f"zip 必须包含 {required}（找不到 {root / required}）",
                )

        # 4) 加载 manifest.py 拿 Manifest
        manifest = _load_manifest_from_path(root / "manifest.py")
        if not isinstance(manifest, Manifest):
            raise ManifestError(
                "BAD_MANIFEST",
                f"manifest.py 顶层 MANIFEST 必须是 Manifest 实例，得到 {type(manifest).__name__}",
            )
        if not manifest.key or "/" in manifest.key or "\\" in manifest.key:
            raise ManifestError(
                "BAD_MANIFEST_KEY",
                f"manifest.key 非法: {manifest.key!r}（不能为空且不允许斜杠）",
            )

        # 5) 与 builtin 冲突
        if manifest.key in BUILTIN_FEATURES:
            raise KeyConflict(
                "KEY_CONFLICTS_BUILTIN",
                f"key {manifest.key!r} 与内置插件冲突，请改 manifest.key",
            )

        # 如果根目录被展开过，把内容浮到 extract_dir 顶层（统一接口）
        if root != extract_dir:
            _flatten_into(root, extract_dir)

        return ParsedPlugin(manifest=manifest, extract_dir=extract_dir)
    except Exception:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise


def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    """禁止绝对路径 / `..` 段，防止 zip slip。"""
    for name in zf.namelist():
        # 拒绝绝对路径
        if name.startswith("/") or (len(name) >= 2 and name[1] == ":"):
            raise InvalidZipStructure(
                "ZIP_ABS_PATH",
                f"zip 不允许绝对路径成员: {name!r}",
            )
        parts = Path(name).parts
        if any(p == ".." for p in parts):
            raise InvalidZipStructure(
                "ZIP_PATH_TRAVERSAL",
                f"zip 不允许 .. 路径穿越: {name!r}",
            )


def _resolve_real_root(extract_dir: Path) -> Path:
    """对"打包者把内容包了一层目录"的情况自动展开。

    判定：解压根直接含 manifest.py → 就是根；否则若根下只有一个子目录且该子目录有
    manifest.py → 把它当真正的根。其余情况返回原 ``extract_dir``，让 ``parse_zip``
    后续的 ``MISSING_REQUIRED_FILE`` 报错来兜。
    """
    if (extract_dir / "manifest.py").is_file():
        return extract_dir
    children = [p for p in extract_dir.iterdir() if not p.name.startswith("__")]
    if len(children) == 1 and children[0].is_dir() and (children[0] / "manifest.py").is_file():
        return children[0]
    return extract_dir


def _flatten_into(src: Path, dst: Path) -> None:
    """把 ``src`` 内的所有内容平移到 ``dst`` 目录顶层；操作完后删掉 ``src`` 自身。

    用于 ``_resolve_real_root`` 找到的"嵌一层"目录展开到统一布局。
    """
    for item in src.iterdir():
        target = dst / item.name
        if target.exists():
            # 父级（extract_dir）原本就有同名条目 → 不太可能，保险起见跳过
            continue
        shutil.move(str(item), str(target))
    src.rmdir()


def _load_manifest_from_path(manifest_py: Path) -> Manifest:
    """用 importlib spec 单独加载一个 manifest.py（不会污染 app 命名空间）。"""
    spec_name = f"_telebot_pending_manifest_{manifest_py.parent.name}_{id(manifest_py)}"
    spec = importlib.util.spec_from_file_location(spec_name, manifest_py)
    if spec is None or spec.loader is None:
        raise ManifestError("MANIFEST_LOAD_FAIL", f"无法加载 {manifest_py}")
    mod = importlib.util.module_from_spec(spec)
    try:
        # sys.modules 注册一份，避免 manifest.py 内部 `from .xxx` 时 KeyError；
        # 加载完立刻 pop 掉防止常驻
        sys.modules[spec_name] = mod
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.modules.pop(spec_name, None)
    except Exception as exc:  # noqa: BLE001
        raise ManifestError("MANIFEST_EXEC_FAIL", f"manifest.py 执行失败: {exc}") from exc
    manifest = getattr(mod, "MANIFEST", None)
    if manifest is None:
        raise ManifestError(
            "MANIFEST_MISSING_CONST",
            "manifest.py 必须导出顶层常量 MANIFEST: Manifest",
        )
    return manifest


# ─────────────────────────────────────────────────────
# 签名校验（Ed25519）
# ─────────────────────────────────────────────────────
def verify_signature(
    payload: bytes,
    signature: bytes | None,
    pubkey_pem: str | None,
) -> bool | None:
    """校验 detached 签名。返回三态：

    - ``True``：签名 + 公钥都在，且校验通过
    - ``False``：签名 + 公钥都在，但校验失败
    - ``None``：缺签名或缺公钥，跳过校验（前端展示"未签名"提示）
    """
    if not signature or not pubkey_pem:
        return None
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except Exception:  # noqa: BLE001
        log.warning("cryptography 未安装，跳过签名校验")
        return None
    try:
        key = load_pem_public_key(pubkey_pem.encode("utf-8"))
    except Exception:  # noqa: BLE001
        log.warning("plugin_pubkey 解析失败，跳过签名校验")
        return None
    try:
        # 直接 .verify(signature, payload)，对 Ed25519 / RSA-PKCS1 都通用？
        # 实际上 RSA 的 verify 签名不一样，这里只兼容 Ed25519，其他 key 类型走 hash + verify。
        # 为了简洁我们仅声明支持 Ed25519：
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        if not isinstance(key, Ed25519PublicKey):
            log.warning(
                "plugin_pubkey 不是 Ed25519 公钥（type=%s），跳过校验",
                type(key).__name__,
            )
            return None
        key.verify(signature, payload)
        return True
    except InvalidSignature:
        return False
    except Exception:  # noqa: BLE001
        log.exception("签名校验抛出未知异常")
        return False


# ─────────────────────────────────────────────────────
# 安装 / 升级 / 卸载 / 启停
# ─────────────────────────────────────────────────────
async def install_zip(
    db: AsyncSession,
    *,
    zip_bytes: bytes,
    signature: bytes | None = None,
    source: str = PLUGIN_SOURCE_ZIP,
    repo_id: int | None = None,
) -> PluginInstall:
    """完整的 zip 安装流程：解析 → 验签 → 落盘 → 写表。

    存在同名 ``key`` 时视作"升级"：写库 UPDATE 同时覆盖目录；
    保留旧的 ``enabled`` 状态，但若新签名失败强制 enabled=False（管理员需手动启用）。
    """
    parsed = parse_zip(zip_bytes)
    try:
        sig_ok = verify_signature(zip_bytes, signature, settings.plugin_pubkey or None)

        # 路径计算
        installed_root = Path(settings.plugins_installed_dir).resolve()
        installed_root.mkdir(parents=True, exist_ok=True)
        final_dir = (installed_root / parsed.manifest.key).resolve()
        # 防御性：再校验 final_dir 一定在 installed_root 之下（避免 manifest.key 含路径符）
        if installed_root not in final_dir.parents and final_dir != installed_root:
            raise KeyConflict(
                "BAD_KEY_PATH",
                f"非法 manifest.key 派生路径: {parsed.manifest.key!r}",
            )

        # 旧记录（升级情况）
        existing = await db.get(PluginInstall, parsed.manifest.key)
        was_enabled = bool(existing.enabled) if existing is not None else False

        # 删除旧目录后把临时目录搬过去
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.move(str(parsed.extract_dir), str(final_dir))
        # parsed.extract_dir 已被 move 走，不必再 rmtree

        # 计算最终 enabled：sig_ok=False 时强制 false
        final_enabled = was_enabled and (sig_ok is not False)

        manifest_json = parsed.manifest.to_dict()
        if existing is None:
            row = PluginInstall(
                key=parsed.manifest.key,
                source=source,
                version=parsed.manifest.version,
                manifest_json=manifest_json,
                signature_ok=sig_ok,
                installed_path=str(final_dir),
                enabled=final_enabled,
                repo_id=repo_id,
            )
            db.add(row)
        else:
            existing.source = source
            existing.version = parsed.manifest.version
            existing.manifest_json = manifest_json
            existing.signature_ok = sig_ok
            existing.installed_path = str(final_dir)
            existing.enabled = final_enabled
            existing.repo_id = repo_id
            row = existing
        await db.flush()
        return row
    except Exception:
        # 任何失败都清理临时目录（如果还在）
        if parsed.extract_dir.exists():
            shutil.rmtree(parsed.extract_dir, ignore_errors=True)
        raise


async def uninstall(db: AsyncSession, key: str) -> bool:
    """卸载指定 key：删表行 + 删目录。返回 True 表示真删了一行。"""
    row = await db.get(PluginInstall, key)
    if row is None:
        return False
    target = Path(row.installed_path)
    await db.delete(row)
    await db.flush()
    # 删目录失败不阻塞 DB 提交（但写日志方便排查）
    try:
        if target.exists():
            shutil.rmtree(target)
    except Exception:  # noqa: BLE001
        log.exception("卸载插件 %s 时删除目录失败 %s", key, target)
    return True


async def set_enabled(db: AsyncSession, key: str, enabled: bool) -> PluginInstall:
    """设置 enabled 标志；调用方负责后续向 worker 广播 reload_config。"""
    row = await db.get(PluginInstall, key)
    if row is None:
        raise PluginInstallError("PLUGIN_NOT_FOUND", f"插件不存在: {key}")
    if enabled and row.signature_ok is False:
        # 签名失败时不允许直接 enable；前端要显式"我知道风险"再调，会先把 signature_ok 置 None
        raise SignatureFailed(
            "SIGNATURE_FAILED",
            "签名校验失败，禁止启用；管理员可先重新上传带正确签名的 zip",
        )
    row.enabled = bool(enabled)
    await db.flush()
    return row


async def list_installed(db: AsyncSession) -> list[PluginInstall]:
    """列出所有已安装的第三方插件，按 key 字典序。"""
    rows = (
        await db.execute(select(PluginInstall).order_by(PluginInstall.key))
    ).scalars().all()
    return list(rows)


# ─────────────────────────────────────────────────────
# 内部 BytesIO 包装：保留兼容（早期实现遗留，目前直接走 io.BytesIO）
# ─────────────────────────────────────────────────────


__all__ = [
    "InvalidZipStructure",
    "KeyConflict",
    "ManifestError",
    "ParsedPlugin",
    "PluginInstallError",
    "SignatureFailed",
    "ZipTooLarge",
    "install_zip",
    "list_installed",
    "parse_zip",
    "set_enabled",
    "uninstall",
    "verify_signature",
]

===== backend/app/services/plugin_repo_service.py =====
"""第三方插件仓库（apt 风格）服务（阶段 C）。

职责：
- 抓取 ``PluginRepo.url`` 指向的 ``index.json`` 拉到一份"可用插件清单"
- 写入 ``plugin_available`` 表，更新 ``plugin_repo.last_synced_at``
- 提供 ``install_from_repo``：按 (repo_id, key) 下载 zip + 可选 .sig，复用
  ``plugin_install_service.install_zip`` 完成解压 + 写表

仓库 ``index.json`` 约定（简洁版）：
::

    {
      "name": "Official Repo",
      "plugins": [
        {
          "key": "weather",
          "name": "天气",
          "version": "1.2.0",
          "author": "alice",
          "description": "查询天气",
          "url": "https://.../weather-1.2.0.zip",
          "sig_url": "https://.../weather-1.2.0.zip.sig",
          "manifest": { ... 可选 manifest 快照 ... }
        }
      ]
    }

下载 zip 时只走 ``settings.plugin_zip_max_bytes`` 上限的 ``httpx.AsyncClient``；
URL scheme 仅允许 http/https。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.plugin import (
    PLUGIN_SOURCE_REPO,
    PluginAvailable,
    PluginInstall,
    PluginRepo,
)
from ..settings import settings
from . import plugin_install_service as pis

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# 错误
# ─────────────────────────────────────────────────────
class PluginRepoError(Exception):
    """仓库相关错误的基类。``code`` 给 API 层做 HTTP 状态映射用。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _validate_url(url: str) -> None:
    """仓库 / zip / sig URL 仅允许 http/https，避免 file:/// / ftp:// 越权。"""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise PluginRepoError("BAD_URL", f"非法 URL: {url!r}")


# ─────────────────────────────────────────────────────
# 同步：拉 index.json → 写 plugin_available
# ─────────────────────────────────────────────────────
async def sync_repo(db: AsyncSession, repo_id: int) -> int:
    """拉取仓库 index.json，更新 ``plugin_available``。返回写入条目数。"""
    repo = await db.get(PluginRepo, repo_id)
    if repo is None:
        raise PluginRepoError("REPO_NOT_FOUND", f"仓库不存在: {repo_id}")
    if not repo.enabled:
        raise PluginRepoError("REPO_DISABLED", f"仓库已禁用: {repo.name}")
    _validate_url(repo.url)

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(repo.url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise PluginRepoError("FETCH_FAILED", f"拉取 index 失败: {exc}") from exc
    except ValueError as exc:
        raise PluginRepoError("BAD_INDEX_JSON", f"index 不是合法 JSON: {exc}") from exc

    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, list):
        raise PluginRepoError(
            "BAD_INDEX_SCHEMA", "index.json 必须是 {plugins: [...]} 形式"
        )

    # 删旧 + 写新（事务由调用方在 outer commit；这里仅 flush）
    await db.execute(delete(PluginAvailable).where(PluginAvailable.repo_id == repo_id))

    inserted = 0
    for raw in plugins:
        if not isinstance(raw, dict):
            continue
        key = raw.get("key")
        version = raw.get("version") or "0.0.0"
        name = raw.get("name") or key
        if not key or not isinstance(key, str):
            continue
        db.add(
            PluginAvailable(
                repo_id=repo_id,
                key=key,
                name=str(name),
                version=str(version),
                author=raw.get("author"),
                description=raw.get("description"),
                manifest={
                    # 把整个原始对象塞 manifest，install 时还能再用一遍
                    **raw,
                },
            )
        )
        inserted += 1

    repo.last_synced_at = datetime.now(UTC)
    await db.flush()
    return inserted


# ─────────────────────────────────────────────────────
# 从仓库安装：下载 zip + 可选 sig → 走 install_zip
# ─────────────────────────────────────────────────────
async def install_from_repo(
    db: AsyncSession, repo_id: int, key: str
) -> PluginInstall:
    """从指定仓库下载并安装某个 key 的插件包。"""
    avail = (
        await db.execute(
            select(PluginAvailable).where(
                PluginAvailable.repo_id == repo_id,
                PluginAvailable.key == key,
            )
        )
    ).scalar_one_or_none()
    if avail is None:
        raise PluginRepoError(
            "PLUGIN_NOT_IN_REPO", f"仓库 {repo_id} 没有插件 {key}"
        )

    raw: dict[str, Any] = avail.manifest or {}
    zip_url = raw.get("url")
    sig_url = raw.get("sig_url")
    if not isinstance(zip_url, str) or not zip_url:
        raise PluginRepoError("MISSING_ZIP_URL", f"插件 {key} 在仓库中缺少 url 字段")
    _validate_url(zip_url)
    if sig_url is not None:
        if not isinstance(sig_url, str):
            sig_url = None
        else:
            _validate_url(sig_url)

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            zip_resp = await client.get(zip_url)
            zip_resp.raise_for_status()
            zip_bytes = zip_resp.content
            if len(zip_bytes) > settings.plugin_zip_max_bytes:
                raise PluginRepoError(
                    "ZIP_TOO_LARGE",
                    f"远程 zip 体积超过 {settings.plugin_zip_max_bytes // 1024 // 1024} MiB 上限",
                )
            sig_bytes: bytes | None = None
            if sig_url:
                sig_resp = await client.get(sig_url)
                sig_resp.raise_for_status()
                sig_bytes = sig_resp.content
                if len(sig_bytes) > 1024:
                    raise PluginRepoError(
                        "SIG_TOO_LARGE", "远程 .sig 文件过大（> 1 KiB）"
                    )
    except httpx.HTTPError as exc:
        raise PluginRepoError("DOWNLOAD_FAILED", f"下载失败: {exc}") from exc

    try:
        row = await pis.install_zip(
            db,
            zip_bytes=zip_bytes,
            signature=sig_bytes,
            source=PLUGIN_SOURCE_REPO,
            repo_id=repo_id,
        )
    except pis.PluginInstallError as exc:
        # 把 service 错误的 code 透传出去；API 层会做 HTTP 状态映射
        raise PluginRepoError(exc.code, exc.message) from exc
    return row


__all__ = [
    "PluginRepoError",
    "install_from_repo",
    "sync_repo",
]

===== backend/app/services/rate_limit_service.py =====
"""三层继承合并：默认 ← 模板 ← 账号 ← 规则。

engine 不直接读 DB，由本服务负责把 ``RateLimitTemplate`` / ``Account`` / ``Rule`` 三层
``RateLimitRule`` 拼成 ``EffectiveLimits``，并提供：
  - ``get_effective_factory(db_factory)``：给 worker 用的便利工厂
  - 模板 / 账号风控 CRUD（API 层调）
  - 拟人化 CRUD（API 层调）
"""

from __future__ import annotations

from datetime import time as dtime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account, HumanizeConfig
from ..db.models.rate_limit import (
    POLICY_QUEUE,
    SCOPE_ACCOUNT,
    SCOPE_TEMPLATE,
    RateLimitRule,
    RateLimitTemplate,
)
from ..worker.ratelimit.engine import EffectiveLimits
from ..worker.ratelimit.humanize import HumanizeOpts

# ─────────────────────────────────────────────────────
# 默认阈值（PRD §L.1 起点建议）
# ─────────────────────────────────────────────────────
_DEFAULTS: dict[str, dict] = {
    "send_message_private": {"per_second": 1, "per_minute": 20, "per_hour": 500},
    "send_message_group": {"per_second": 1, "per_minute": 30, "per_hour": 1000},
    "same_peer_send": {"same_peer_per_minute": 3},
    "edit_message": {"per_minute": 5},
    "delete_message": {"per_minute": 30},
    "forward_message": {"per_minute": 20},
    "callback_query": {"per_minute": 6, "per_hour": 60},
    "read_history": {"per_minute": 30},
    "join_chat": {"per_hour": 5, "per_day": 20},
    "leave_chat": {"per_hour": 5},
    "create_chat": {"per_day": 2},
    "invite_user": {"per_hour": 10, "per_day": 50},
    "dm_stranger": {"per_hour": 3, "per_day": 20},
    "update_profile": {"per_hour": 3},
    "upload_file": {"per_minute": 5},
    "download_file": {"per_minute": 10},
    "search": {"per_minute": 10},
    "api_total": {"per_second": 30, "per_minute": 1000},
}


def default_for(action: str) -> dict:
    """暴露给 API 层用：某 action 的默认阈值（用于"显示继承自默认"提示）。"""
    return dict(_DEFAULTS.get(action, {}))


# ─────────────────────────────────────────────────────
# 三层合并
# ─────────────────────────────────────────────────────
async def get_effective(db: AsyncSession, account_id: int, action: str) -> EffectiveLimits:
    """合并：默认 ← 模板 ← 账号级 ← 规则级（规则级当前未启用）。"""
    out = EffectiveLimits(policy=POLICY_QUEUE, backoff_base=5, backoff_max=1800)
    _apply_dict(out, _DEFAULTS.get(action, {}))

    acc = await db.get(Account, account_id)
    if acc is None:
        return out

    # 模板层
    if acc.template_id:
        rule = (
            await db.execute(
                select(RateLimitRule).where(
                    RateLimitRule.scope == SCOPE_TEMPLATE,
                    RateLimitRule.scope_id == acc.template_id,
                    RateLimitRule.action == action,
                    RateLimitRule.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        if rule is not None:
            _apply_rule(out, rule)
        else:
            # 若模板内显式禁用了该 action（enabled=False）→ 视作 disabled
            disabled_rule = (
                await db.execute(
                    select(RateLimitRule).where(
                        RateLimitRule.scope == SCOPE_TEMPLATE,
                        RateLimitRule.scope_id == acc.template_id,
                        RateLimitRule.action == action,
                        RateLimitRule.enabled.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if disabled_rule is not None:
                out.disabled = True

    # 账号层
    rule = (
        await db.execute(
            select(RateLimitRule).where(
                RateLimitRule.scope == SCOPE_ACCOUNT,
                RateLimitRule.scope_id == account_id,
                RateLimitRule.action == action,
                RateLimitRule.enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    if rule is not None:
        _apply_rule(out, rule)
    else:
        disabled_rule = (
            await db.execute(
                select(RateLimitRule).where(
                    RateLimitRule.scope == SCOPE_ACCOUNT,
                    RateLimitRule.scope_id == account_id,
                    RateLimitRule.action == action,
                    RateLimitRule.enabled.is_(False),
                )
            )
        ).scalar_one_or_none()
        if disabled_rule is not None:
            out.disabled = True

    # 规则层（按 rule_id 查 SCOPE_RULE）当前 MVP 不传入，预留接口

    return out


def _apply_dict(out: EffectiveLimits, d: dict) -> None:
    """把 dict 字段覆盖到 ``EffectiveLimits``（仅覆盖存在的字段）。"""
    for k, v in d.items():
        if hasattr(out, k):
            setattr(out, k, v)


def _apply_rule(out: EffectiveLimits, rule: RateLimitRule) -> None:
    """把一条 ``RateLimitRule`` 覆盖到 ``EffectiveLimits``。

    阈值字段 None 表示"继承上层"；不为 None 才覆盖。policy / backoff_* 永远以最深一层为准。
    """
    for f in ("per_second", "per_minute", "per_hour", "per_day", "same_peer_per_minute"):
        v = getattr(rule, f)
        if v is not None:
            setattr(out, f, v)
    if rule.policy:
        out.policy = rule.policy
    out.backoff_base = int(rule.backoff_base_seconds)
    out.backoff_max = int(rule.backoff_max_seconds)


def get_effective_factory(db_factory):
    """给 worker 用的便利工厂：返回一个 ``(account_id, action) -> EffectiveLimits`` 协程。

    ``db_factory`` 通常就是 ``AsyncSessionLocal``。
    """

    async def _f(aid: int, action: str) -> EffectiveLimits:
        async with db_factory() as db:
            return await get_effective(db, aid, action)

    return _f


# ─────────────────────────────────────────────────────
# 模板 CRUD（给 API 用）
# ─────────────────────────────────────────────────────
async def list_templates(db: AsyncSession) -> list[RateLimitTemplate]:
    res = await db.execute(select(RateLimitTemplate).order_by(RateLimitTemplate.id.asc()))
    return list(res.scalars().all())


async def create_template(db: AsyncSession, name: str, is_default: bool = False) -> RateLimitTemplate:
    # 若设为默认：把其它模板的 default 清掉，保证唯一
    if is_default:
        await _clear_default(db)
    tpl = RateLimitTemplate(name=name, is_default=is_default)
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return tpl


async def update_template(
    db: AsyncSession,
    tpl_id: int,
    name: str | None = None,
    is_default: bool | None = None,
) -> RateLimitTemplate | None:
    tpl = await db.get(RateLimitTemplate, tpl_id)
    if tpl is None:
        return None
    if name is not None:
        tpl.name = name
    if is_default is not None:
        if is_default:
            await _clear_default(db, exclude_id=tpl_id)
        tpl.is_default = is_default
    await db.commit()
    await db.refresh(tpl)
    return tpl


async def delete_template(db: AsyncSession, tpl_id: int) -> bool:
    tpl = await db.get(RateLimitTemplate, tpl_id)
    if tpl is None:
        return False
    # 同步删模板下所有 rule
    rules = (
        await db.execute(
            select(RateLimitRule).where(
                RateLimitRule.scope == SCOPE_TEMPLATE,
                RateLimitRule.scope_id == tpl_id,
            )
        )
    ).scalars().all()
    for r in rules:
        await db.delete(r)
    await db.delete(tpl)
    await db.commit()
    return True


async def _clear_default(db: AsyncSession, exclude_id: int | None = None) -> None:
    res = await db.execute(select(RateLimitTemplate).where(RateLimitTemplate.is_default.is_(True)))
    for tpl in res.scalars():
        if exclude_id is not None and tpl.id == exclude_id:
            continue
        tpl.is_default = False
    await db.flush()


# ─────────────────────────────────────────────────────
# RateLimitRule 读写（按 scope）
# ─────────────────────────────────────────────────────
async def list_rules(db: AsyncSession, scope: str, scope_id: int) -> list[RateLimitRule]:
    res = await db.execute(
        select(RateLimitRule)
        .where(RateLimitRule.scope == scope, RateLimitRule.scope_id == scope_id)
        .order_by(RateLimitRule.action.asc())
    )
    return list(res.scalars().all())


async def upsert_rule(
    db: AsyncSession,
    scope: str,
    scope_id: int,
    action: str,
    *,
    per_second: int | None = None,
    per_minute: int | None = None,
    per_hour: int | None = None,
    per_day: int | None = None,
    same_peer_per_minute: int | None = None,
    policy: str | None = None,
    backoff_base_seconds: int | None = None,
    backoff_max_seconds: int | None = None,
    enabled: bool | None = None,
) -> RateLimitRule:
    """新建或覆盖一条 ``RateLimitRule``（按 scope + scope_id + action 唯一）。"""
    rule = (
        await db.execute(
            select(RateLimitRule).where(
                RateLimitRule.scope == scope,
                RateLimitRule.scope_id == scope_id,
                RateLimitRule.action == action,
            )
        )
    ).scalar_one_or_none()
    if rule is None:
        rule = RateLimitRule(
            scope=scope,
            scope_id=scope_id,
            action=action,
            policy=policy or POLICY_QUEUE,
        )
        db.add(rule)
    if per_second is not None:
        rule.per_second = per_second
    if per_minute is not None:
        rule.per_minute = per_minute
    if per_hour is not None:
        rule.per_hour = per_hour
    if per_day is not None:
        rule.per_day = per_day
    if same_peer_per_minute is not None:
        rule.same_peer_per_minute = same_peer_per_minute
    if policy is not None:
        rule.policy = policy
    if backoff_base_seconds is not None:
        rule.backoff_base_seconds = backoff_base_seconds
    if backoff_max_seconds is not None:
        rule.backoff_max_seconds = backoff_max_seconds
    if enabled is not None:
        rule.enabled = enabled
    await db.commit()
    await db.refresh(rule)
    return rule


async def delete_rule(db: AsyncSession, scope: str, scope_id: int, action: str) -> bool:
    """删除某 scope 下某 action 的覆盖（恢复继承）。"""
    rule = (
        await db.execute(
            select(RateLimitRule).where(
                RateLimitRule.scope == scope,
                RateLimitRule.scope_id == scope_id,
                RateLimitRule.action == action,
            )
        )
    ).scalar_one_or_none()
    if rule is None:
        return False
    await db.delete(rule)
    await db.commit()
    return True


# ─────────────────────────────────────────────────────
# 拟人化配置 CRUD
# ─────────────────────────────────────────────────────
async def get_humanize(db: AsyncSession, account_id: int) -> HumanizeConfig | None:
    return await db.get(HumanizeConfig, account_id)


async def get_humanize_opts(db: AsyncSession, account_id: int) -> HumanizeOpts:
    """把 ORM 模型转为 ``HumanizeOpts``（engine 用）。

    若账号未配置，返回默认值；并把 ``Account.cold_start_until`` 一并带上。
    """
    cfg = await db.get(HumanizeConfig, account_id)
    acc = await db.get(Account, account_id)
    cold_until = acc.cold_start_until if acc is not None else None
    if cfg is None:
        return HumanizeOpts(cold_start_until=cold_until)
    return HumanizeOpts(
        jitter_pct=cfg.jitter_pct,
        typing_simulate=cfg.typing_simulate,
        typing_min_ms=cfg.typing_min_ms,
        typing_max_ms=cfg.typing_max_ms,
        typing_probability=cfg.typing_probability,
        read_before_reply=cfg.read_before_reply,
        active_window_start=cfg.active_window_start,
        active_window_end=cfg.active_window_end,
        cold_start_days=cfg.cold_start_days,
        cold_start_until=cold_until,
    )


async def upsert_humanize(
    db: AsyncSession,
    account_id: int,
    *,
    jitter_pct: int | None = None,
    typing_simulate: bool | None = None,
    typing_min_ms: int | None = None,
    typing_max_ms: int | None = None,
    typing_probability: int | None = None,
    read_before_reply: bool | None = None,
    active_window_start: dtime | None = None,
    active_window_end: dtime | None = None,
    cold_start_days: int | None = None,
) -> HumanizeConfig:
    cfg = await db.get(HumanizeConfig, account_id)
    if cfg is None:
        cfg = HumanizeConfig(account_id=account_id)
        db.add(cfg)
    for f, v in (
        ("jitter_pct", jitter_pct),
        ("typing_simulate", typing_simulate),
        ("typing_min_ms", typing_min_ms),
        ("typing_max_ms", typing_max_ms),
        ("typing_probability", typing_probability),
        ("read_before_reply", read_before_reply),
        ("active_window_start", active_window_start),
        ("active_window_end", active_window_end),
        ("cold_start_days", cold_start_days),
    ):
        if v is not None:
            setattr(cfg, f, v)
    await db.commit()
    await db.refresh(cfg)
    return cfg


__all__ = [
    "EffectiveLimits",
    "default_for",
    "delete_rule",
    "delete_template",
    "get_effective",
    "get_effective_factory",
    "get_humanize",
    "get_humanize_opts",
    "list_rules",
    "list_templates",
    "create_template",
    "update_template",
    "upsert_humanize",
    "upsert_rule",
]

===== backend/app/settings.py =====
"""全局配置：从 .env 加载，所有模块统一通过 settings.* 读取。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置，所有字段都可通过环境变量覆盖（pydantic-settings 自动映射）。"""

    # ── 加密 / 认证 ────────────────────────────────────────────────
    master_key: str = Field(..., description="Fernet 主密钥，加密 session 等敏感字段")
    jwt_secret: str = Field(..., description="JWT HS256 签名密钥")
    jwt_expire_seconds: int = 12 * 3600
    # Cookie 安全：HTTPS 部署应设为 true（反代后由后端直接打 Secure=True，少一层依赖）
    # 默认 false，方便本地 HTTP 调试；生产 .env 显式设 COOKIE_SECURE=true
    cookie_secure: bool = False
    # 登录限速（针对 /api/auth/login 与 /api/auth/register）
    # 0 表示不限速；默认 30 次/分钟，按 IP+用户名两个维度同时计数
    login_rate_limit_per_min: int = 30
    # 是否信任 X-Forwarded-For 取客户端 IP；
    # 仅当部署在可信反代（nginx/traefik）后面时才设为 true，否则攻击者可通过伪造头绕过 IP 限速
    trust_forwarded_for: bool = False

    # ── 数据库 / Redis ─────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://telebot:telebot@localhost:5432/telebot"
    redis_url: str = "redis://localhost:6379/0"

    # ── Web ────────────────────────────────────────────────────────
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    # ── userbot ────────────────────────────────────────────────────
    command_prefix: str = ","
    session_dir: str = "./sessions"
    # 头像本地缓存目录；主进程通过 IPC 让 worker 写盘 ``{aid}.jpg``
    # 24h TTL，worker 离线时返 404 → 前端首字母 fallback
    avatars_dir: str = "./data/avatars"

    # ── 第三方插件（阶段 B/C） ────────────────────────────────
    # 已安装第三方插件的根目录；loader.discover_plugins 会扫描这里下的子目录。
    # 对应 worker/plugins/loader.py 中的 _INSTALLED_DIR；二者一定要一致。
    plugins_installed_dir: str = "./data/plugins/installed"
    # 上传 zip 时验签使用的 Ed25519 公钥（PEM）；为空表示不验签，前端给出"未签名"警告。
    # 公钥示例：-----BEGIN PUBLIC KEY-----\nMC...\n-----END PUBLIC KEY-----
    plugin_pubkey: str = ""
    # 上传 zip 体积上限（字节），默认 10 MiB。超出直接 413。
    plugin_zip_max_bytes: int = 10 * 1024 * 1024

    # 全局默认代理（仅当账号未绑定 Proxy 行时兜底）。
    # 格式：``socks5://[user:pass@]host:port`` 或 ``http://host:port`` 或 ``mtproxy://host:port?secret=xxx``
    # 留空 = 直连（在能直接访问 Telegram 的网络下使用）
    tg_default_proxy: str = ""

    # ── 全局风控 ──────────────────────────────────────────────────
    kill_switch: bool = False
    global_api_qps: int = 0  # 0 表示不限制

    # ── 启动期自动迁移 ────────────────────────────────────────────
    # True（默认）= backend 启动时自动 ``alembic upgrade head``，把 DB schema 升到代码期望的版本
    #               避免"前端打开看不到列表"这种"列不存在"500 引发的体验问题
    # False = 完全不动 DB；适合多实例部署、由 CI/CD 单独跑迁移的场景，避免并发起服务时 race
    auto_migrate_on_startup: bool = True

    # ── 日志 ───────────────────────────────────────────────────────
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=(
            # 优先从仓库根目录 .env 加载
            Path(__file__).resolve().parents[2] / ".env",
            ".env",
        ),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """以逗号分隔的 CORS 源解析为 list。"""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_url_sync(self) -> str:
        """Alembic 用的同步 DSN（去掉 +asyncpg 后缀，psycopg2/psycopg 都能识别）。"""
        return self.database_url.replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    """单例配置访问器。"""
    return Settings()  # type: ignore[call-arg]


# 顶层导出便于 from app.settings import settings
settings = get_settings()

===== backend/app/util/__init__.py =====
"""通用工具子包：跨模块共享的小函数。"""

===== backend/app/util/proxy.py =====
"""代理 URL 解析工具。

把 ``.env`` 里的 ``TG_DEFAULT_PROXY`` 字符串（或任何 URL 风格的代理串）
转成 Telethon 接受的 PySocks 元组：

    (proxy_type, host, port, rdns, username, password)

支持的格式：
    socks5://user:pass@host:port
    socks5://host:port
    socks4://host:port
    http://host:port
    mtproxy://host:port?secret=xxxx     (Telethon 用 ``connection`` 参数走 MTProxy；
                                         本工具仅支持 PySocks 风格的 socks5/4/http；
                                         mtproxy 留作 TODO，目前会返 None 并记录 warning)

调用：
    >>> parse_proxy_url("socks5://user:pass@127.0.0.1:1080")
    ("socks5", "127.0.0.1", 1080, True, "user", "pass")
    >>> parse_proxy_url("")     # 空字符串视为不配置
    None
"""
from __future__ import annotations

import logging
from urllib.parse import unquote, urlparse

log = logging.getLogger(__name__)

# Telethon / PySocks 接受的 proxy_type 枚举
_VALID_TYPES: dict[str, str] = {
    "socks5": "socks5",
    "socks4": "socks4",
    "http": "http",
    "https": "http",        # https 走 HTTP CONNECT
}


ProxyTuple = tuple[str, str, int, bool, str | None, str | None]


def parse_proxy_url(url: str | None) -> ProxyTuple | None:
    """解析代理 URL；空 / 无效 → 返 None（即直连）。"""
    if not url:
        return None
    url = url.strip()
    if not url:
        return None

    # 兼容用户写 "127.0.0.1:1080" 不带 scheme：默认按 socks5 处理
    if "://" not in url:
        url = "socks5://" + url

    try:
        parsed = urlparse(url)
    except Exception:
        log.warning("代理 URL 无法解析：%r", url)
        return None

    scheme = (parsed.scheme or "").lower()

    if scheme == "mtproxy":
        # Telethon 的 MTProxy 不走 PySocks，需要 connection_class=ConnectionTcpMTProxyRandomizedIntermediate；
        # 这里暂不支持；如真要用，在账号详情里通过 Proxy 表（type='mtproxy'）单独配置——
        # 那条路径走的是 plan 里 build_proxy_tuple 的 (proxy.type, host, port, ...) 写法，
        # Telethon 内部会把 type='mtproxy' 当作 secret 登录。
        log.warning("MTProxy 全局代理暂不支持；请在账号绑定时单独选 mtproxy 代理")
        return None

    proxy_type = _VALID_TYPES.get(scheme)
    if not proxy_type:
        log.warning("未知代理 scheme：%r（仅支持 socks5/socks4/http/https/mtproxy）", scheme)
        return None

    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        log.warning("代理 URL 缺 host 或 port：%r", url)
        return None

    user = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None

    # rdns=True：让代理服务器做 DNS 解析，避免本地 DNS 泄漏（连 TG 时很关键）
    return (proxy_type, host, int(port), True, user, password)


def get_default_proxy_tuple() -> ProxyTuple | None:
    """读 settings.tg_default_proxy 并解析；用作所有未指定 proxy_id 的账号的兜底代理。"""
    # 延迟 import 避免循环
    from ..settings import settings
    return parse_proxy_url(settings.tg_default_proxy)

===== backend/app/worker/__init__.py =====
"""worker 子目录初始化。"""

===== backend/app/worker/command.py =====
"""TG 内命令派发。

用户在 TG 中**自己给自己发**（任何对话，含收藏夹）以前缀（默认 ``,``）开头的消息时，
worker 拦截命令并**编辑原消息**为执行结果（PagerMaid 风格）。

内置命令：``,help`` ``,status`` ``,ping`` ``,pause`` ``,resume`` ``,id``。
插件可以通过 ``register_plugin_command`` 追加额外命令（不会覆盖内置）。

Sprint2 #2 起新增 4 类"模板命令"：reply_text / forward_to / run_plugin / ai。
模板命令由主进程 DB 维护，worker 启动 / IPC reload 时拉取并合并到派发链路。
"""
from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from telethon import TelegramClient, events

from ..redis_client import get_redis
from ..settings import settings
from .ipc import CMD_PAUSE, CMD_RESUME, cmd_channel, make_cmd

log = logging.getLogger(__name__)

# ── 内置命令注册表 ──────────────────────────────────────────────
# key 是命令名（不含前缀），value 是 ``async fn(client, event, args, account_id)``
_BUILTIN: dict[str, Callable[..., Awaitable[None]]] = {}


# ── 模板命令派发上下文 ──────────────────────────────────────────
# 由 runtime.py 在 worker 启动 / IPC reload 时填充；handler 直接读
@dataclass
class CommandContext:
    """worker-local 命令派发上下文。

    - ``account_id``      当前 worker 服务的账号 id
    - ``templates``       {模板名: 模板 dict}；模板 dict 由 ``runtime.py`` 从 DB 拉出后投递
    - ``providers``       {provider_id: provider dict}；同样从 DB 拉，含 api_key 加密 token
    - ``command_prefix``  当前生效的命令前缀（``,`` / ``-`` / ``/`` 等）；
                          系统设置改了 → 主进程发 IPC 让 ``runtime`` 重拉，再写到这里
                          → handler 每次匹配时从 ctx 取，所以前缀热加载对已注册 handler 也生效
    """

    account_id: int
    templates: dict[str, dict[str, Any]]
    providers: dict[int, dict[str, Any]]
    command_prefix: str = ","


# 全局 ctx 由 runtime.py 在 worker 进程启动时初始化并通过闭包传给 handler；
# 同一进程只服务一个 account_id，所以可以直接用模块级单例
_ctx: CommandContext | None = None


def set_command_context(ctx: CommandContext) -> None:
    """runtime.py 启动 worker 后调用一次，IPC reload 时也调用更新内容。"""
    global _ctx
    _ctx = ctx


def get_command_context() -> CommandContext | None:
    """主要供测试 / 调试使用。"""
    return _ctx


def builtin(name: str):
    """装饰器：把命令注册到 ``_BUILTIN``。"""

    def deco(fn):
        _BUILTIN[name] = fn
        return fn

    return deco


def _safe_exception_text(e: BaseException, max_len: int = 200) -> str:
    """把异常信息净化成"安全可在 TG 里显示"的短字符串。

    具体做：
    - 去掉文件绝对路径（``/Users/.../foo.py`` / ``C:\\...\\foo.py``）—— 暴露目录结构是
      安全 & 隐私问题（用户截图里就泄漏过 ``/Users/anoyou/Desktop/telebot/...``）
    - 去掉 ``sk-`` / ``Bearer xxx`` 一类 token 字样
    - 截断到 ``max_len`` 字符
    """
    import re

    msg = f"{type(e).__name__}: {e}"
    # 去 unix 绝对路径 (含括号包裹的也匹配)
    msg = re.sub(r"\(?/[^()\s'\"]+\.py\)?", "<path>", msg)
    # 去 windows 绝对路径
    msg = re.sub(r"\(?[A-Za-z]:[\\/][^()\s'\"]+\.py\)?", "<path>", msg)
    # 去常见 token
    msg = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "<redacted>", msg)
    msg = re.sub(r"Bearer\s+[A-Za-z0-9_.\-]{8,}", "Bearer <redacted>", msg)
    if len(msg) > max_len:
        msg = msg[:max_len] + "…"
    return msg


@builtin("help")
async def _cmd_help(client, event, args, account_id):
    """列出所有可用命令及简短说明。

    每个 builtin 取其 docstring 第一行作为说明；插件注册的命令同样支持。
    模板命令也合并展示，标记 [模板]。
    """
    p = settings.command_prefix or ","
    lines = [f"📋 可用命令（前缀 `{p}`）：", "", "**内置：**"]
    for name in sorted(_BUILTIN.keys()):
        fn = _BUILTIN.get(name)
        doc = (fn.__doc__ or "").strip().splitlines()
        desc = doc[0].rstrip("。.") if doc else "（无说明）"
        lines.append(f"• `{p}{name}` — {desc}")
    # 模板命令（如有启用）
    if _ctx and _ctx.templates:
        lines.append("")
        lines.append("**自定义模板：**")
        for name in sorted(_ctx.templates.keys()):
            tpl = _ctx.templates[name]
            t = tpl.get("type", "?")
            desc = tpl.get("description") or f"模板：{t}"
            lines.append(f"• `{p}{name}` — {desc}（[{t}]）")
    await event.edit("\n".join(lines))


@builtin("status")
async def _cmd_status(client, event, args, account_id):
    """显示当前账号信息。"""
    me = await client.get_me()
    name = me.first_name or me.username or "<unnamed>"
    await event.edit(f"账号 #{account_id} · {name} · 在线 ✓")


@builtin("ping")
async def _cmd_ping(client, event, args, account_id):
    """连通性自检。"""
    await event.edit("pong")


@builtin("id")
async def _cmd_id(client, event, args, account_id):
    """显示当前会话 chat_id（用于配置 auto_reply 的指定群）。"""
    chat_id = event.chat_id
    peer_kind = (
        "私聊" if event.is_private
        else "频道" if event.is_channel
        else "群" if event.is_group
        else "?"
    )
    # supergroup / channel：去掉 -100 前缀给一个"裸 id"，方便用户对照 t.me/c/<id> URL
    bare = ""
    a = abs(int(chat_id)) if chat_id is not None else 0
    if a > 1_000_000_000_000:
        bare = f"\n裸 id（去掉 -100 前缀）：{a - 1_000_000_000_000}"
    text = (
        f"类型：{peer_kind}\n"
        f"chat_id：{chat_id}{bare}\n\n"
        "把上面任一格式填到 auto_reply 规则的「指定群 ID」即可。"
    )
    await event.edit(text)


@builtin("pause")
async def _cmd_pause(client, event, args, account_id):
    """通过 IPC 通知本 worker 暂停主动动作。"""
    redis = get_redis()
    await redis.publish(cmd_channel(account_id), make_cmd(CMD_PAUSE))
    await event.edit("已暂停（仅暂停主动动作；被动接收照常）")


@builtin("resume")
async def _cmd_resume(client, event, args, account_id):
    """通过 IPC 通知本 worker 恢复主动动作。"""
    redis = get_redis()
    await redis.publish(cmd_channel(account_id), make_cmd(CMD_RESUME))
    await event.edit("已恢复")


@builtin("version")
async def _cmd_version(client, event, args, account_id):
    """显示当前 telebot 版本与运行环境。"""
    import platform
    import sys

    import telethon

    from .. import __version__

    tlv = getattr(telethon, "__version__", "?")
    text = (
        f"📦 telebot v{__version__}\n"
        f"Python {sys.version.split()[0]} · Telethon {tlv}\n"
        f"Platform {platform.system()} {platform.release()}"
    )
    await event.edit(text)


def register_plugin_command(name: str, fn: Callable):
    """允许其他模块（主要是 D Agent 插件）注册命令；不会覆盖内置。"""
    if name in _BUILTIN:
        return  # 不覆盖
    _BUILTIN[name] = fn


# ════════════════════════════════════════════════════════════
# 模板命令执行（Sprint2 #2）
# ════════════════════════════════════════════════════════════


async def _run_template(client, event, args, tpl: dict[str, Any], account_id: int) -> None:
    """根据 ``tpl["type"]`` 分支执行模板命令。

    模板 dict 的字段（与 ``CommandTemplate`` 模型对应）：
    - ``id``        模板 id
    - ``name``      命令名（不含前缀）
    - ``type``      reply_text / forward_to / run_plugin / ai
    - ``config``    按 type 不同结构
    - ``description``  可选
    """
    t = tpl.get("type")
    cfg: dict[str, Any] = tpl.get("config") or {}

    if t == "reply_text":
        # 简单变量替换：{args} → 用户拼接的剩余参数
        text = str(cfg.get("text", "")).replace("{args}", " ".join(args))
        await event.edit(text or "(空文本)")
        return

    if t == "forward_to":
        replied = await event.get_reply_message()
        if not replied:
            await event.edit("✗ 请回复要转发的消息再用此命令")
            return
        try:
            target = int(cfg["target_chat_id"])
        except (KeyError, ValueError, TypeError):
            await event.edit("✗ 模板配置错误：target_chat_id 不是合法的整数")
            return
        try:
            await replied.forward_to(target)
        except Exception as e:  # noqa: BLE001
            await event.edit(f"✗ 转发失败：{type(e).__name__}: {str(e)[:80]}")
            return
        await event.edit(f"✓ 已转发到 {target}")
        return

    if t == "ai":
        await _run_ai(client, event, args, tpl, account_id)
        return

    if t == "run_plugin":
        # V1 占位：等 Sprint2 #4 插件模块化完成后再接
        await event.edit(
            f"⏳ run_plugin 占位：插件={cfg.get('plugin_key')!r}, 方法={cfg.get('method')!r}"
        )
        return

    await event.edit(f"✗ 未知模板类型：{t}")


async def _run_ai(client, event, args, tpl: dict[str, Any], account_id: int) -> None:
    """AI 类命令：调 LLM provider，把回答编辑回原消息。

    工作模式（cfg.routing_mode）：
    - ``fixed``（默认）— 用 cfg.provider_id 锁定的 provider
    - ``auto``        — 调 services.llm_router 按消息内容自动选 provider；
                       配置项：``routing_fallback_provider_id`` / ``classifier_provider_id``
                       自动路由失败兜底走 cfg.provider_id 自身，再不行才报错

    安全要求：
    - api_key 仅在 ``LLMClient.__init__`` 中持有；不打 log、不写 audit
    - 任何异常路径只透出 ``type(e).__name__`` 与裁剪后的 message
    """
    cfg: dict[str, Any] = tpl.get("config") or {}
    provider_id = cfg.get("provider_id")
    if provider_id is None:
        await event.edit("✗ AI 命令未配置 provider_id（系统设置 → LLM Provider 里建一个，填回此处）")
        return

    if _ctx is None:
        await event.edit("✗ worker 命令上下文尚未初始化")
        return

    # ── 拼 prompt 上下文（路由器与 LLM 都要看消息内容）─────────
    user_q = " ".join(args).strip()
    replied = await event.get_reply_message()
    quote = bool(cfg.get("quote_replied", True))
    replied_text: str | None = None
    has_replied_photo = False
    if replied is not None:
        original = replied.text or replied.message or ""
        replied_text = original or None
        # Telethon Message 上判断是否含图：photo 字段非空即视为有图（路由用）
        if getattr(replied, "photo", None) is not None:
            has_replied_photo = True

    # ── 决策 provider_id（fixed / auto）────────────────────────
    routing_mode = str(cfg.get("routing_mode") or "fixed").lower()
    routing_note: str | None = None  # 自动路由时附加在结尾的说明
    chosen_provider_id = int(provider_id)

    if routing_mode == "auto":
        # 局部 import 避免 worker 启动时强依赖
        from ..services.llm_router import pick_provider

        cls_id = cfg.get("classifier_provider_id")
        # 没显式配兜底就用 fixed 那条；保证 auto 模式失败也有 last resort
        fb_id = cfg.get("routing_fallback_provider_id") or provider_id
        try:
            decision = await pick_provider(
                user_q,
                replied_text,
                has_replied_photo,
                _ctx.providers,
                classifier_provider_id=int(cls_id) if cls_id else None,
                fallback_provider_id=int(fb_id),
            )
        except ValueError as e:
            # 路由器找不到任何可用 provider
            await event.edit(f"✗ AI 路由失败：{e}")
            return
        except Exception as e:  # noqa: BLE001
            # 任何意外都不要让命令静默卡住
            await event.edit(f"✗ AI 路由异常：{type(e).__name__}: {str(e)[:120]}")
            return
        chosen_provider_id = decision.provider_id
        routing_note = f"auto · {decision.reason}"

    provider_dict = _ctx.providers.get(chosen_provider_id)
    if provider_dict is None:
        await event.edit(
            f"✗ provider_id={chosen_provider_id} 不存在或未加载（试着保存一次模板？）"
        )
        return

    # 拼 user prompt
    if quote and replied_text:
        user_msg = f"[原文]\n{replied_text}\n\n[问题]\n{user_q or '解释/总结'}"
    else:
        user_msg = user_q or "请简要总结你能想到的内容"

    system = cfg.get("system_prompt") or "你是简洁有用的中文助手。回答控制在 100 字内。"
    max_tokens = int(cfg.get("max_tokens") or 512)
    override_model = cfg.get("model")

    # 占位回显，避免用户以为没反应（注意：edit 失败也要继续，非致命）
    # 一律简化为 "思考中..."；具体路由决策最终在 footer 的 {routing_note} 里展示
    try:
        await event.edit("思考中...")
    except Exception:  # noqa: BLE001
        pass

    # build_client 在内部解密 api_key；导入时点放函数内，避免循环依赖
    from ..db.models.command import LLMProvider as LLMProviderModel
    from ..services.llm_client import LLMError, build_client

    # 用一个 in-memory dataclass-like 对象传给 build_client 即可（属性访问相同）
    # 直接构造一个临时 ORM 对象（不绑定 session）保证字段一致
    fake_row = LLMProviderModel(
        id=int(chosen_provider_id),
        name=str(provider_dict.get("name", "")),
        provider=str(provider_dict.get("provider", "")),
        api_key_enc=provider_dict.get("api_key_enc"),
        base_url=provider_dict.get("base_url"),
        default_model=str(provider_dict.get("default_model", "")),
    )

    try:
        llm = build_client(
            fake_row,
            override_model=override_model,
            proxy_url=provider_dict.get("proxy_url"),
        )
        result = await llm.complete(system, user_msg, max_tokens=max_tokens)
    except LLMError as e:
        # message 已在 LLMError 内脱敏
        await event.edit(f"✗ AI 调用失败：{e}")
        return
    except Exception as e:  # noqa: BLE001
        await event.edit(f"✗ AI 调用失败：{type(e).__name__}: {str(e)[:120]}")
        return

    # ── 用 output_template 渲染最终消息 ─────────────────────────
    # 默认走 HTML：Telethon 1.36 的 sanitize_parse_mode 不接受 'markdownv2' 字符串
    # （会抛 ValueError），所以改用 HTML——telethon 内置全功能支持，包括
    # <blockquote expandable> 折叠引用块。
    # 老配置里 output_format='markdownv2' 自动当 'html' 处理（容错）。
    from ..services.llm_format import DEFAULT_TEMPLATE, render_output

    template = cfg.get("output_template") or DEFAULT_TEMPLATE
    raw_format = (cfg.get("output_format") or "html").lower()
    # 老数据兼容：markdownv2 → 当 html
    output_format = "html" if raw_format == "markdownv2" else raw_format
    escape_values = bool(cfg.get("escape_values", True))

    render_ctx = {
        "answer": result.text or "",
        "question": user_q,
        "quoted": replied_text or "",
        "model": result.model or "",
        "provider": provider_dict.get("name", ""),
        "provider_kind": provider_dict.get("provider", ""),
        "in_tokens": result.input_tokens,
        "out_tokens": result.output_tokens,
        "total_tokens": result.input_tokens + result.output_tokens,
        "routing_note": (routing_note or "").replace("auto · ", ""),  # 去掉前缀让模板自己加
    }

    # 转义模式：html 走 HTML 转义；plain / markdown_v1 不转义；老 mdv2 也不进这里（已映射到 html）
    if escape_values and output_format == "html":
        escape_format: str | None = "html"
    else:
        escape_format = None

    body = render_output(template, render_ctx, escape_format=escape_format)

    # parse_mode：telethon 1.36 sanitize_parse_mode 接受 md/markdown/htm/html
    # 我们这里用 'html' / 'md' / None（plain）
    parse_mode_arg: str | None
    if output_format == "html":
        parse_mode_arg = "html"
    elif output_format in ("markdown", "markdown_v1", "md"):
        parse_mode_arg = "md"
    else:
        parse_mode_arg = None  # plain

    try:
        await event.edit(body, parse_mode=parse_mode_arg)
    except Exception as e:  # noqa: BLE001
        # 解析失败时（用户模板有未闭合 HTML 标签 / 未转义的特殊字符）退化为纯文本
        # 避免命令彻底失败，让用户至少能看到答案
        try:
            await event.edit(body)
        except Exception:
            # 实在不行就最简化版，至少把答案露出来
            try:
                await event.edit(
                    f"{result.text}\n\n— {result.model} · in {result.input_tokens} / out {result.output_tokens}\n\n"
                    f"⚠ 模板渲染异常：{type(e).__name__}",
                )
            except Exception:
                pass


def make_command_handler(client: TelegramClient, account_id: int, prefix: str | None = None):
    """创建并注册 TG 命令派发 handler。

    监听 ``outgoing=True`` 即只对本人发送的消息生效，避免误触发其他用户的同前缀消息。

    前缀热加载：handler 每次拦截消息时**从 ctx 读 prefix**，不再用闭包里固定 pattern。
    系统设置改前缀 → 主进程广播 IPC ``reload_global`` → runtime 重拉 ctx → 下一条消息立刻按
    新前缀匹配。``prefix`` 参数仅作"启动期默认"，正常运行靠 ctx 动态。
    """
    fallback_prefix = prefix or settings.command_prefix or ","

    @client.on(events.NewMessage(outgoing=True))
    async def _h(event):
        # 每次消息从 ctx 取最新前缀；ctx 没就绪时退回闭包里的 fallback
        p = (_ctx.command_prefix if _ctx else "") or fallback_prefix
        # re.compile 微秒级；user message 频率本来就低，每条编译一次完全无所谓
        pattern = re.compile(rf"^{re.escape(p)}(\w+)(?:\s+(.*))?$", re.S)
        text = event.raw_text or ""
        m = pattern.match(text)
        if not m:
            return
        cmd = m.group(1)
        args_raw = (m.group(2) or "").strip()
        args = args_raw.split() if args_raw else []

        # 1. 内置命令优先
        fn = _BUILTIN.get(cmd)
        if fn is not None:
            try:
                await fn(client, event, args, account_id)
            except Exception as e:  # noqa: BLE001
                # 命令执行异常时，把错误原地写回消息，方便排查（消息已脱敏：去路径/token）
                try:
                    await event.edit(f"✗ 执行失败：{_safe_exception_text(e)}")
                except Exception:
                    pass
            return

        # 2. 模板命令（按 name 查 worker-local ctx）
        if _ctx is not None:
            tpl = _ctx.templates.get(cmd)
            if tpl is not None:
                try:
                    await _run_template(client, event, args, tpl, account_id)
                except Exception as e:  # noqa: BLE001
                    try:
                        await event.edit(f"✗ 执行失败：{_safe_exception_text(e)}")
                    except Exception:
                        pass
                return

        # 3. 未知命令
        try:
            await event.edit(f"未知命令：{cmd}（{p}help 查看可用列表）")
        except Exception:
            pass

    return _h

===== backend/app/worker/ipc.py =====
"""主进程 ↔ worker 之间的 IPC 协议。

通信通道命名约定（Redis pub/sub）：
- ``worker_cmd:{account_id}``    主进程 → worker  下发指令
- ``worker_event:{account_id}``  worker → 主进程  上报事件 / 日志 / 限速事件
- ``worker_global``              广播指令（全员适用，例如 kill switch 切换）

消息使用 JSON，统一字段：
    { "type": "...", "ts": <epoch_ms>, "payload": { ... } }
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


# ── Channel 模板 ──────────────────────────────────────────────────
def cmd_channel(account_id: int) -> str:
    return f"worker_cmd:{account_id}"


def event_channel(account_id: int) -> str:
    return f"worker_event:{account_id}"


GLOBAL_CHANNEL = "worker_global"
RUNTIME_LOG_STREAM = "runtime_log_stream"          # 主进程消费此 list 落库
RATELIMIT_EVENT_STREAM = "ratelimit_event_stream"  # 主进程消费此 list 落库


# ── 指令类型（主→worker） ────────────────────────────────────────
CMD_PAUSE = "pause"
CMD_RESUME = "resume"
CMD_STOP = "stop"
CMD_RELOAD_CONFIG = "reload_config"        # 拉新风控/拟人化配置
CMD_RELOAD_PLUGIN = "reload_plugin"        # payload: {plugin_key}
# 自定义命令模板 / LLM provider 变化后通知 worker 热加载（无 payload）
CMD_RELOAD_COMMANDS = "reload_commands"
CMD_RUN_TG_COMMAND = "run_tg_command"      # 用于 Web 触发 TG 命令（可选）
CMD_PING = "ping"
# 让 worker 把当前账号头像下载到本地磁盘缓存（payload: {"path": "<绝对路径>"}）
# 主进程的 ensure_avatar 用 fire-and-forget 方式发送，worker 写盘后由下次请求读到
CMD_FETCH_AVATAR = "fetch_avatar"
# 通知 worker 重新拉取忽略名单（账号忽略 peer 增删后下发；payload 为空）
CMD_RELOAD_IGNORED = "reload_ignored"
# RPC：拉 worker 内存中的最近活跃 peer（payload: {"reply_to": <一次性应答频道>}）
CMD_GET_RECENT_PEERS = "get_recent_peers"

# ── 事件类型（worker→主） ──────────────────────────────────────
EVT_STATUS = "status"                      # payload: {status: active|paused|...}
EVT_LOG = "log"                            # payload: {level, source, message, detail}
EVT_RATELIMIT = "ratelimit"                # payload: {action, outcome, detail}
EVT_PLUGIN_STATE = "plugin_state"          # payload: {feature_key, state, last_error?}
EVT_LOGIN_REQUIRED = "login_required"      # session 失效
EVT_PONG = "pong"


# ── 全局指令 ────────────────────────────────────────────────
GCMD_KILL_SWITCH = "kill_switch"           # payload: {enabled: bool}
GCMD_RELOAD_GLOBAL = "reload_global"


@dataclass
class IPCMessage:
    """统一的 IPC 消息结构。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def decode(cls, raw: str | bytes) -> IPCMessage:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        d = json.loads(raw)
        return cls(type=d["type"], payload=d.get("payload") or {}, ts=d.get("ts") or 0)


# ── 便捷构造函数 ──────────────────────────────────────────────
def make_cmd(type_: str, **payload: Any) -> str:
    return IPCMessage(type=type_, payload=payload).encode()


def make_event(type_: str, **payload: Any) -> str:
    return IPCMessage(type=type_, payload=payload).encode()


# Worker -> 主进程：限速事件结构（也用于直接写 RATELIMIT_EVENT_STREAM）
@dataclass
class RateLimitEventPayload:
    account_id: int
    action: str
    outcome: str
    detail: dict[str, Any] | None = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False)


# Worker -> 主进程：运行日志结构
@dataclass
class RuntimeLogPayload:
    account_id: int | None
    level: Literal["debug", "info", "warn", "error"]
    source: str | None
    message: str
    detail: dict[str, Any] | None = None
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False)

===== backend/app/worker/plugins/__init__.py =====
"""插件子包：提供 PluginContext / Plugin 基类、注册表、loader 与内置插件集合。"""

from .base import Plugin, PluginContext, all_plugins, get_plugin, register

__all__ = ["Plugin", "PluginContext", "all_plugins", "get_plugin", "register"]

===== backend/app/worker/plugins/base.py =====
"""插件框架：``PluginContext`` + ``Plugin`` 基类 + 全局注册表。

设计要点：
- ``Plugin`` 是基类，所有内置 / 第三方插件继承它并通过 ``@register`` 注册到全局表。
- 注册表存放的是 **类对象**（不是实例），每账号在 loader 里各自实例化一次，避免共享状态。
- ``PluginContext`` 是给插件运行期使用的"上下文容器"：账号 id、配置、规则、Telethon
  client、风控引擎、redis、日志写入器；插件实现各 hook 时只需读它就够了。
- 严格遵循 ``CONTRACTS.md`` 的"插件 Hook"段；所有 hook 默认实现为 no-op，子类按需重写。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from telethon import TelegramClient, events


# ─────────────────────────────────────────────────────
# 运行时上下文（每个 [账号 × feature] 一份）
# ─────────────────────────────────────────────────────
@dataclass
class PluginContext:
    """插件运行上下文。

    字段：
      - ``account_id``：当前 worker 服务的账号 id
      - ``feature_key``：插件对应的 feature key（与 ``Plugin.key`` 一致）
      - ``config``：``account_feature.config`` 中保存的 dict（可由 reload_config 热更新）
      - ``rules``：该 [账号 × feature] 下所有 ``enabled=True`` 的 ``Rule``，按 priority 倒序
      - ``client``：Telethon 客户端（loader 注入）
      - ``engine``：风控引擎（C Agent 提供，支持 ``acquire`` 与各 ``on_*`` 回调）
      - ``redis``：异步 Redis 客户端
      - ``log``：写运行日志的协程；签名 ``async (level, message, **detail)``

    为避免循环 import，``rules`` / ``engine`` / ``redis`` 都用 ``Any`` 标注。
    """

    account_id: int
    feature_key: str
    config: dict[str, Any] = field(default_factory=dict)
    rules: list[Any] = field(default_factory=list)  # list[Rule] —— 这里用 Any 防循环引用
    client: TelegramClient | None = None
    engine: Any = None  # RateLimitEngine
    redis: Any = None  # redis.asyncio.Redis
    log: Callable[..., Awaitable[None]] | None = None


# ─────────────────────────────────────────────────────
# 插件基类
# ─────────────────────────────────────────────────────
class Plugin:
    """插件基类。

    子类必须设置类属性 ``key`` / ``display_name``；可重写以下 hook：
      - ``on_startup``：[账号 × feature] 被激活时调用一次
      - ``on_shutdown``：禁用 / 卸载 / 热重载前调用一次
      - ``on_message``：每条 incoming 消息派发到本插件
      - ``on_command``：插件可声明的"账号内命令"；返回 True 表示已处理

    插件如要追加 TG 内命令，可在类属性 ``commands`` 里登记
    （key 是命令名，value 是 ``async fn(client, event, args, account_id, ctx)``），
    loader 会在 ``on_startup`` 后通过 ``register_plugin_command`` 暴露给命令分发。
    """

    key: str = ""
    display_name: str = ""
    # 插件想暴露的 TG 内命令：cmd_name -> async handler
    # handler 签名: (client, event, args, account_id, ctx) -> None
    commands: dict[str, Callable[..., Awaitable[None]]] = {}

    async def on_startup(self, ctx: PluginContext) -> None:
        """[账号 × feature] 激活时的钩子；默认 no-op。"""
        return None

    async def on_shutdown(self, ctx: PluginContext) -> None:
        """[账号 × feature] 关停时的钩子；默认 no-op。"""
        return None

    async def on_message(self, ctx: PluginContext, event: events.NewMessage.Event) -> None:
        """每条 incoming NewMessage 事件回调；默认 no-op。"""
        return None

    async def on_command(
        self,
        ctx: PluginContext,
        cmd: str,
        args: list[str],
        event: events.NewMessage.Event,
    ) -> bool:
        """命令派发回调；返回 True 表示已处理，否则继续向后传。默认 no-op 返回 False。"""
        return False


# ─────────────────────────────────────────────────────
# 全局注册表
# ─────────────────────────────────────────────────────
# feature_key -> Plugin 子类（不是实例！每账号都要新实例）
_REGISTRY: dict[str, type[Plugin]] = {}


def register(plugin_cls: type[Plugin]) -> type[Plugin]:
    """装饰器：把一个 ``Plugin`` 子类注册到全局表。

    用法：
        @register
        class AutoReplyPlugin(Plugin):
            key = "auto_reply"
            ...
    """
    if not getattr(plugin_cls, "key", ""):
        raise ValueError("Plugin.key 必须先设置")
    _REGISTRY[plugin_cls.key] = plugin_cls
    return plugin_cls


def get_plugin(key: str) -> type[Plugin] | None:
    """按 feature key 查找已注册的插件类，不存在返回 None。"""
    return _REGISTRY.get(key)


def all_plugins() -> dict[str, type[Plugin]]:
    """返回当前已注册的全部插件（拷贝）。"""
    return dict(_REGISTRY)


__all__ = [
    "Plugin",
    "PluginContext",
    "all_plugins",
    "get_plugin",
    "register",
]

===== backend/app/worker/plugins/builtin/__init__.py =====
"""内置插件包：模块化重构后每个 builtin 插件以 ``子目录`` 形式存在。

- 每个子目录里有 ``__init__.py`` / ``manifest.py`` / ``plugin.py``，
  ``__init__.py`` 暴露 ``PLUGIN_CLASS`` 与 ``MANIFEST`` 两个常量供 loader 扫描。
- 这里的 ``import`` 只是为了让 ``app.worker.plugins.builtin.<key>`` 路径继续可被
  外部代码（API / 测试）直接 import；具体注册仍由各 ``plugin.py`` 里的 ``@register``
  装饰器在 import 阶段触发。

新增内置插件：在本目录下建一个新子目录 + 在下方 ``from . import xxx`` 追加一行即可。
"""

# 注意：这里的 import 顺序不影响功能；loader._import_builtins / discover_plugins
# 都做了容错。下方注释掉的导入与 _BUILTIN_DIRS 都只是给测试与外部静态分析用的索引。
from . import auto_reply, forward, group_admin, monitor, scheduler  # noqa: F401

__all__ = [
    "auto_reply",
    "forward",
    "group_admin",
    "monitor",
    "scheduler",
]

===== backend/app/worker/plugins/builtin/auto_reply/__init__.py =====
"""auto_reply 插件包入口：

- 暴露 ``PLUGIN_CLASS`` / ``MANIFEST`` 给 loader 扫描使用
- re-export plugin.py 顶层公开符号，保证旧的 import 路径
  (``from app.worker.plugins.builtin.auto_reply import AutoReplyPlugin / _dry_run_match`` 等)
  在目录化重构后继续可用
"""

from .manifest import MANIFEST
from .plugin import (
    AutoReplyPlugin,
    _dry_run_match,
    _match,
    _render,
    _scope_ok,
)

# loader.discover_plugins 读取这两个常量，无须显式 @register
PLUGIN_CLASS = AutoReplyPlugin

__all__ = [
    "AutoReplyPlugin",
    "MANIFEST",
    "PLUGIN_CLASS",
    "_dry_run_match",
    "_match",
    "_render",
    "_scope_ok",
]

===== backend/app/worker/plugins/builtin/auto_reply/manifest.py =====
"""auto_reply 插件 manifest。"""

from __future__ import annotations

from app.db.models.feature import FEATURE_AUTO_REPLY
from app.worker.plugins.manifest import Manifest

# 顶层导出常量；loader 扫描时读取
MANIFEST = Manifest(
    key=FEATURE_AUTO_REPLY,
    display_name="自动回复",
    version="0.1.0",
    author="builtin",
    description="按规则匹配关键词或正则后自动回复目标会话",
    permissions=["send_message", "edit_message", "read_chat"],
)

__all__ = ["MANIFEST"]

===== backend/app/worker/plugins/builtin/auto_reply/plugin.py =====
"""内置插件：自动回复（PRD §C）。

支持能力：
  - 关键词匹配（默认）/ 正则匹配（``match_type=regex``）
  - 大小写敏感开关（``case_sensitive``）
  - 作用范围 ``scope``：``all`` | ``private`` | ``all_groups`` | ``groups``（结合 ``groups`` 列表）
  - 白 / 黑名单（``whitelist_chats`` / ``blacklist_chats``，以 chat_id 为单位）
  - 每规则、每会话独立冷却（Redis SETEX）
  - 模板变量 ``{sender}`` / ``{chat}`` / ``{text}``
  - 风控集成：发送前 ``engine.acquire`` 拿决策；FloodWait/PeerFlood/SlowMode 自动反馈到 engine
  - 拟人化：``simulate_read`` + ``simulate_typing``
  - 命中即止：所有 enabled rule 按 priority 倒序遍历，第一条命中即触发并 return

rule.config 形如：
    {
      "match_type": "keyword" | "regex",
      "patterns": ["hello", "hi"],
      "scope": "all" | "private" | "all_groups" | "groups",
      "groups": [123, 456],            // scope=groups 时使用
      "reply": "world {sender}",
      "cooldown_seconds": 30,
      "whitelist_chats": [...],
      "blacklist_chats": [...],
      "case_sensitive": false
    }
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from telethon import events

# 模块化重构后改用绝对 import：第三方插件解压到 data/plugins/installed/{key}/
# 时也只能走绝对 import，因此 builtin 同样统一用绝对路径以保持一致性。
from app.db.models.feature import FEATURE_AUTO_REPLY
from app.worker.plugins.base import Plugin, PluginContext, register
from app.worker.ratelimit.humanize import simulate_read, simulate_typing


@register
class AutoReplyPlugin(Plugin):
    """自动回复插件实现。"""

    key = FEATURE_AUTO_REPLY
    display_name = "自动回复"

    async def on_message(
        self, ctx: PluginContext, event: events.NewMessage.Event
    ) -> None:
        """对每条 incoming 消息按规则优先级匹配，命中第一条立刻回复并 return。"""
        # 没规则就直接退出，避免无谓的 redis / engine 调用
        if not ctx.rules:
            return

        text: str = event.raw_text or ""
        chat_id: int | None = event.chat_id

        # 调试：本插件本次拿到了多少条规则
        if ctx.log is not None:
            try:
                await ctx.log(
                    "info",
                    f"[auto_reply] 收到消息 chat_id={chat_id} text={text!r:.80} rules={len(ctx.rules)}",
                )
            except Exception:  # noqa: BLE001
                pass

        for rule in ctx.rules:
            cfg: dict[str, Any] = rule.config or {}
            # 1) 黑白名单
            if not _whitelist_ok(cfg, chat_id):
                if ctx.log is not None:
                    await ctx.log("info", f"[auto_reply] 规则 #{rule.id} 跳过：白名单")
                continue
            if _in_blacklist(cfg, chat_id):
                if ctx.log is not None:
                    await ctx.log("info", f"[auto_reply] 规则 #{rule.id} 跳过：黑名单")
                continue
            # 2) 作用范围
            if not _scope_ok(cfg, event):
                if ctx.log is not None:
                    await ctx.log(
                        "info",
                        f"[auto_reply] 规则 #{rule.id} 跳过：scope 不匹配 "
                        f"(scope={cfg.get('scope')!r} chat_id={chat_id} group_ids={cfg.get('group_ids') or cfg.get('groups')!r})",
                    )
                continue
            # 3) 模式匹配
            if not _match(cfg, text):
                if ctx.log is not None:
                    # 失败时打印 text + pattern 的 repr 与字节十六进制，揭示同形不同码点的情况
                    pats = cfg.get("patterns") or []
                    text_repr = repr(text)[:120]
                    text_hex = text.encode("utf-8")[:80].hex()
                    pat_dump = []
                    for p in pats[:5]:
                        pp = str(p)
                        pat_dump.append(
                            f"{pp!r:.60} hex={pp.encode('utf-8')[:60].hex()}"
                        )
                    await ctx.log(
                        "info",
                        f"[auto_reply] 规则 #{rule.id} 跳过：pattern 未命中 | "
                        f"text={text_repr} hex={text_hex} | patterns=[{' | '.join(pat_dump)}]",
                    )
                continue
            if ctx.log is not None:
                await ctx.log("info", f"[auto_reply] 规则 #{rule.id} 命中，准备回复")
            # 4) 冷却（Redis SETEX）
            cool_key = f"ar:cool:{ctx.account_id}:{rule.id}:{chat_id}"
            try:
                if await ctx.redis.get(cool_key):
                    if ctx.log is not None:
                        await ctx.log("info", f"[auto_reply] 规则 #{rule.id} 在冷却中")
                    continue
                cooldown = int(cfg.get("cooldown_seconds", 30) or 0)
                if cooldown > 0:
                    await ctx.redis.set(cool_key, "1", ex=cooldown)
            except Exception:
                # redis 不可用时不阻塞业务（可能本地 fakeredis 测试）；继续走风控
                pass

            # 5) 风控决策
            action = (
                "send_message_group"
                if (event.is_group or event.is_channel)
                else "send_message_private"
            )
            decision = await ctx.engine.acquire(
                ctx.account_id, action, peer_id=chat_id
            )
            if not decision.allowed:
                if ctx.log is not None:
                    await ctx.log(
                        "info",
                        f"auto_reply 被风控丢弃: outcome={decision.outcome}",
                        rule_id=rule.id,
                    )
                return
            if decision.wait_seconds and decision.wait_seconds > 0:
                await asyncio.sleep(float(decision.wait_seconds))

            # 6) 拟人化（best effort，异常忽略）
            try:
                chat_obj = await event.get_chat()
                opts = _get_humanize_opts(ctx)
                await simulate_read(ctx.client, chat_obj, opts)
                await simulate_typing(ctx.client, chat_obj, opts)
            except Exception:
                pass

            # 7) 模板渲染
            try:
                sender = await event.get_sender()
            except Exception:
                sender = None
            try:
                chat = await event.get_chat()
            except Exception:
                chat = None
            text_out = _render(cfg.get("reply", ""), sender, chat, text)
            if not text_out:
                # 无内容直接 return：也算命中并消耗冷却
                return

            # 8) 真正发送 + Telegram 异常回灌 engine
            #    reply_to 默认 True：以"引用"形式回复触发消息（视觉上挂在那条消息下方）；
            #    cfg.reply_to=False 时退化成普通新消息（event.respond）
            reply_to_msg = bool(cfg.get("reply_to", True))
            try:
                if reply_to_msg:
                    await event.reply(text_out)
                else:
                    await event.respond(text_out)
                if ctx.log is not None:
                    await ctx.log(
                        "info",
                        f"auto_reply 命中规则 #{rule.id} (reply_to={reply_to_msg})",
                        rule_id=rule.id,
                    )
            except Exception as exc:  # noqa: BLE001
                # 这里手动包装：因为我们没用 @rate_limited 装饰器
                await _handle_send_exception(ctx, action, chat_id, exc)
            return  # 命中一条即止


# ─────────────────────────────────────────────────────
# 工具：作用范围 / 黑白名单 / 匹配 / 模板渲染
# ─────────────────────────────────────────────────────
def _scope_ok(cfg: dict, event: Any) -> bool:
    """根据 ``scope`` 判断当前事件是否落在规则作用范围内。

    scope 支持（兼容前端命名）：
      - ``"all"``（默认）：任何会话
      - ``"private"``：仅私聊
      - ``"all_groups"`` 或 ``"group_all"``：所有群 / 频道
      - ``"groups"`` 或 ``"group_specific"``（配合 ``cfg.groups`` 或 ``cfg.group_ids``）：指定 chat_id
      - ``{"groups": [...]}``：dict 形式的等价写法
    """
    scope = cfg.get("scope", "all")
    if scope == "all":
        return True
    if scope == "private":
        return bool(event.is_private)
    if scope in ("all_groups", "group_all"):
        return bool(event.is_group or event.is_channel)
    # dict 形式：{"groups": [...]}
    if isinstance(scope, dict) and "groups" in scope:
        candidates = _coerce_int_list(scope.get("groups") or [])
        return _chat_id_in(event.chat_id, candidates)
    # 字符串 "groups" / "group_specific" + cfg.groups | cfg.group_ids
    if scope in ("groups", "group_specific"):
        candidates = _coerce_int_list(cfg.get("groups") or cfg.get("group_ids") or [])
        return _chat_id_in(event.chat_id, candidates)
    return True


def _coerce_int_list(raw: Any) -> list[int]:
    """前端表单里 chat_id 列表是 ``string[]``，比对前转 int；解析失败的项跳过。"""
    out: list[int] = []
    for item in raw or []:
        if isinstance(item, int):
            out.append(item)
            continue
        try:
            out.append(int(str(item).strip()))
        except (TypeError, ValueError):
            continue
    return out


# Telethon 对 supergroup/channel 的 ``event.chat_id`` 是 ``-100xxxxxxxxxx`` 形式
# （13 位以上、负数）；用户在 t.me/c/<id> URL 里看到的是去掉前缀的纯数字；
# basic group 是 ``-xxxxxxxxxx`` 形式；private 是正数。
# 为了让用户填什么都能命中，把每个 id 展开成所有合理等价表示。
_CHANNEL_PREFIX = 1_000_000_000_000  # -100... 实际上是 -(1e12 + bare)


def _expand_chat_id(raw: int) -> set[int]:
    """把一个 chat id 展开成所有可能的等价表示。

    例：用户填 1234567890 → 也能匹配 -1001234567890 / -1234567890
       用户填 -1001234567890 → 同样展开到 1234567890 / -1234567890
    """
    out: set[int] = {raw}
    a = abs(raw)
    out.add(a)
    out.add(-a)
    if a > _CHANNEL_PREFIX:
        bare = a - _CHANNEL_PREFIX
        out.add(bare)
        out.add(-bare)
    else:
        out.add(-(_CHANNEL_PREFIX + a))
    return out


def _chat_id_in(target: int | None, candidates: list[int]) -> bool:
    """鲁棒匹配：candidates 中任一 id 的等价集若包含 target 即命中。"""
    if target is None or not candidates:
        return False
    target_set = _expand_chat_id(int(target))
    for c in candidates:
        if target_set & _expand_chat_id(int(c)):
            return True
    return False


def _whitelist_ok(cfg: dict, chat_id: int | None) -> bool:
    """白名单非空时仅放行白名单 chat；为空表示不启用。

    兼容字段名 ``whitelist_chats``（后端原写法）和 ``whitelist``（前端写法）。
    """
    wl = _coerce_int_list(cfg.get("whitelist_chats") or cfg.get("whitelist") or [])
    if not wl:
        return True
    return chat_id in wl


def _in_blacklist(cfg: dict, chat_id: int | None) -> bool:
    """黑名单命中即拒。兼容 ``blacklist_chats`` / ``blacklist``。"""
    bl = _coerce_int_list(cfg.get("blacklist_chats") or cfg.get("blacklist") or [])
    return bool(bl) and chat_id in bl


def _match(cfg: dict, text: str) -> bool:
    """按 ``match_type`` (后端原名) 或 ``match`` (前端名) 做关键词或正则匹配。

    在普通字面比对失败时，会做一层 NFKC 归一化 + 去零宽字符 + strip 后再试一次，
    避免 Telegram 消息里夹零宽空格 / 全角数字 / BOM 等导致 "肉眼一样但码点不同" 的不命中。
    """
    patterns = cfg.get("patterns") or []
    if not patterns:
        return False
    case = bool(cfg.get("case_sensitive", False))
    mtype = cfg.get("match_type") or cfg.get("match") or "keyword"

    if mtype == "regex":
        flags = 0 if case else re.IGNORECASE
        for p in patterns:
            try:
                if re.search(p, text, flags):
                    return True
                # 归一化兜底
                if re.search(_normalize(p, case), _normalize(text, case), flags):
                    return True
            except re.error:
                continue
        return False

    # 默认走关键词包含匹配
    src = text if case else text.lower()
    if any((p if case else str(p).lower()) in src for p in patterns):
        return True
    # 归一化兜底
    src_n = _normalize(text, case)
    return any(_normalize(str(p), case) in src_n for p in patterns)


# ── Unicode 归一化兜底 ────────────────────────────────────────
# Telegram 客户端有时会插入零宽 / 不可见控制字符，或者使用全角数字 / 兼容字符；
# NFKC 把"宽形式 / 兼容形式"归到标准 ASCII 区，再把已知不可见字符抹掉。
_INVISIBLE_CODES = (
    "​‌‍‎‏‪‫‬‭‮⁠﻿"
)
_INVISIBLE_TABLE = str.maketrans("", "", _INVISIBLE_CODES)


def _normalize(s: str, case_sensitive: bool = False) -> str:
    import unicodedata

    out = unicodedata.normalize("NFKC", s).translate(_INVISIBLE_TABLE).strip()
    return out if case_sensitive else out.lower()


def _render(template: str, sender: Any, chat: Any, text: str) -> str:
    """简单模板渲染，仅支持 ``{sender}`` / ``{chat}`` / ``{text}``。

    sender / chat 为 None 时回退为空字符串，避免抛 AttributeError。
    """
    sender_name = ""
    if sender is not None:
        sender_name = (
            getattr(sender, "first_name", None)
            or getattr(sender, "username", None)
            or str(getattr(sender, "id", "") or "")
        )
    chat_name = ""
    if chat is not None:
        chat_name = (
            getattr(chat, "title", None)
            or getattr(chat, "first_name", None)
            or str(getattr(chat, "id", "") or "")
        )
    return (
        (template or "")
        .replace("{sender}", str(sender_name))
        .replace("{chat}", str(chat_name))
        .replace("{text}", text or "")
    )


def _get_humanize_opts(ctx: PluginContext):
    """从 engine 拿拟人化配置；兜底返回默认的 ``HumanizeOpts``。"""
    if ctx.engine is not None and getattr(ctx.engine, "humanize", None) is not None:
        return ctx.engine.humanize
    # 走到这里说明上下文异常，构造一个默认值避免崩溃
    from app.worker.ratelimit.humanize import HumanizeOpts as _Opts

    return _Opts()


async def _handle_send_exception(
    ctx: PluginContext, action: str, peer_id: int | None, exc: Exception
) -> None:
    """把 Telethon 发送异常映射回 engine 的回调；其它异常仅写日志。"""
    # 延迟 import：避免 telethon 缺失时 import 期失败
    try:
        from telethon.errors import (
            FloodWaitError,
            PeerFloodError,
            PhoneNumberFloodError,
            SlowModeWaitError,
        )
    except Exception:  # pragma: no cover - 极端环境兜底
        FloodWaitError = PeerFloodError = SlowModeWaitError = PhoneNumberFloodError = ()  # type: ignore[assignment]

    if isinstance(exc, FloodWaitError):
        await ctx.engine.on_flood_wait(action, exc)
    elif isinstance(exc, PeerFloodError):
        await ctx.engine.on_peer_flood("dm_stranger")
    elif isinstance(exc, SlowModeWaitError):
        await ctx.engine.on_slow_mode(action, exc, peer_id)
    elif isinstance(exc, PhoneNumberFloodError):
        await ctx.engine.on_phone_flood(action, exc)
    else:
        if ctx.log is not None:
            await ctx.log("error", f"auto_reply 发送失败: {type(exc).__name__}: {exc}")


# 暴露给 dry-run / 测试使用的内部工具
def _dry_run_match(
    cfg: dict,
    text: str,
    chat_type: str = "private",
    chat_id: int | None = None,
) -> tuple[bool, str | None]:
    """供 API ``dry-run`` 调用：仅做"是否命中 + 渲染"的纯函数判断。

    chat_id：仅在 chat_type 是 group/channel 且规则 scope=group_specific 时需要；
    未传时若规则 scope=group_specific 会用 ``group_ids`` 第一项当样本，让用户更容易看到命中。
    """
    if chat_id is None and cfg.get("scope") in ("groups", "group_specific"):
        gids = _coerce_int_list(cfg.get("groups") or cfg.get("group_ids") or [])
        if gids:
            chat_id = gids[0]

    class _FakeEvent:
        is_private = chat_type == "private"
        is_group = chat_type == "group"
        is_channel = chat_type == "channel"

    event = _FakeEvent()
    event.chat_id = chat_id if chat_id is not None else 0  # type: ignore[attr-defined]
    if not _scope_ok(cfg, event):
        return False, None
    if not _match(cfg, text):
        return False, None
    rendered = _render(cfg.get("reply", ""), None, None, text)
    return True, rendered


__all__ = [
    "AutoReplyPlugin",
    "_dry_run_match",
    "_match",
    "_render",
    "_scope_ok",
]

===== backend/app/worker/plugins/builtin/forward/__init__.py =====
"""forward 插件包入口：暴露 PLUGIN_CLASS / MANIFEST。"""

from .manifest import MANIFEST
from .plugin import ForwardPlugin

PLUGIN_CLASS = ForwardPlugin

__all__ = ["ForwardPlugin", "MANIFEST", "PLUGIN_CLASS"]

===== backend/app/worker/plugins/builtin/forward/manifest.py =====
"""forward 插件 manifest。"""

from __future__ import annotations

from app.db.models.feature import FEATURE_FORWARD
from app.worker.plugins.manifest import Manifest

# 顶层导出常量；loader 扫描时读取
MANIFEST = Manifest(
    key=FEATURE_FORWARD,
    display_name="消息转发",
    version="0.2.0",
    author="builtin",
    description="按规则把 incoming 消息转发到指定 chat（4 种 mode + 风控接入 + FloodWait 兜底）",
    permissions=["read_chat", "send_message", "send_file"],
    # rule.config 的 JSON Schema —— 前端可据此渲染表单 / 做兜底校验
    config_schema={
        "type": "object",
        "required": ["target_chat_id", "mode"],
        "properties": {
            # 源筛选：all = 任何 incoming；peers = 指定 chat_id 列表；keyword = 文本包含关键词
            "source_kind": {"enum": ["all", "peers", "keyword"]},
            "source_peers": {"type": "array", "items": {"type": "integer"}},
            "keyword": {"type": "string"},
            # 目标 chat_id（Telethon 形式：私聊正数 / 普通群 -xxx / 超级群与频道 -100xxx）
            "target_chat_id": {"type": "integer"},
            # 4 种转发方式
            "mode": {
                "enum": ["forward_native", "copy_text", "quote", "link_only"],
            },
            # include_media=False 时仅转纯文本，遇到含媒体消息直接跳过
            "include_media": {"type": "boolean"},
            # copy / quote 模式下可选的固定前缀
            "header": {"type": "string"},
        },
    },
)

__all__ = ["MANIFEST"]

===== backend/app/worker/plugins/builtin/forward/plugin.py =====
"""内置插件：消息转发（PRD §B）。

支持能力：
  - 三种源筛选：``all`` / ``peers``（chat_id 列表）/ ``keyword``（文本包含）
  - ``include_media`` 开关：False 时跳过含媒体的消息（仅文本）
  - 四种转发方式（``mode``）：
      * ``forward_native``  —— 原生转发，保留原作者署名（``message.forward_to``）
      * ``copy_text``       —— 仅复制文字内容，不显示原作者
      * ``quote``           —— 引用包装，自动加 "📨 来自 X" 前缀
      * ``link_only``       —— 公开超级群可点链接 ``t.me/c/<bare>/<msg_id>``
  - 风控集成：每次转发先 ``engine.acquire("forward_message", peer_id=...)``；不允许就丢弃
  - FloodWait 自动兜底：触发后 sleep(min(seconds,60)) 再重试一次，仍失败仅记 error
  - 全部异常吞掉走 ``ctx.log("error", ...)``，单条失败不影响后续 incoming 消息派发

rule.config 形如：
    {
      "source_kind": "all" | "peers" | "keyword",
      "source_peers": [-1001234567890, ...],
      "keyword": "紧急",
      "target_chat_id": -1001112223334,
      "mode": "forward_native" | "copy_text" | "quote" | "link_only",
      "include_media": true,
      "header": "[from team A]"
    }
"""

from __future__ import annotations

import asyncio
from typing import Any

from telethon import events

# 模块化重构后统一用绝对 import，方便第三方插件解压到 data/plugins/installed/
# 时也能复用同一套写法。
from app.db.models.feature import FEATURE_FORWARD
from app.worker.plugins.base import Plugin, PluginContext, register


@register
class ForwardPlugin(Plugin):
    """消息转发插件实现。"""

    key = FEATURE_FORWARD
    display_name = "消息转发"

    async def on_message(
        self, ctx: PluginContext, event: events.NewMessage.Event
    ) -> None:
        """对每条 incoming 消息遍历所有 enabled 规则，逐条尝试转发。

        与 auto_reply 不同：转发是"一对多"语义——一条消息可能命中多条规则
        （比如同时配了"全转到收藏夹"和"含关键词转到团队群"），所以这里 **不 break**，
        每条命中规则都各自走一遍流水线。
        """
        if not ctx.rules:
            return

        for rule in ctx.rules:
            cfg: dict[str, Any] = rule.config or {}
            # 1) 源筛选
            if not _match_source(event, cfg):
                continue
            # 2) 媒体过滤：默认 include_media=True（兼容旧配置），仅显式 False 才跳过
            include_media = cfg.get("include_media", True)
            if not include_media and event.message and event.message.media:
                continue
            # 3) target_chat_id 兜底校验：缺失 / 非法时直接跳过并记日志
            target_raw = cfg.get("target_chat_id")
            try:
                target = int(target_raw)
            except (TypeError, ValueError):
                if ctx.log is not None:
                    await ctx.log(
                        "warn",
                        f"[forward] 规则 #{rule.id} 缺少合法 target_chat_id：{target_raw!r}",
                        rule_id=rule.id,
                    )
                continue

            # 4) 真正发送 + FloodWait 自动重试一次
            try:
                await self._do_forward(ctx, event, cfg, target)
            except Exception as exc:  # noqa: BLE001
                # FloodWait 单独处理：写 override + sleep + retry 一次
                if _is_flood_wait(exc):
                    seconds = int(getattr(exc, "seconds", 0) or 0)
                    if ctx.log is not None:
                        await ctx.log(
                            "warning",
                            f"[forward] floodwait {seconds}s, sleep & retry once",
                            rule_id=rule.id,
                        )
                    # 把异常回灌给 engine（写 override + 标 floodwait 状态）
                    try:
                        await ctx.engine.on_flood_wait("forward_message", exc)
                    except Exception:  # noqa: BLE001
                        # engine 失败不影响 retry 流程
                        pass
                    await asyncio.sleep(min(seconds, 60))
                    try:
                        await self._do_forward(ctx, event, cfg, target)
                    except Exception as exc2:  # noqa: BLE001
                        if ctx.log is not None:
                            await ctx.log(
                                "error",
                                f"[forward] retry failed: {type(exc2).__name__}: {exc2}",
                                rule_id=rule.id,
                            )
                else:
                    # 其它异常仅写日志，保证不影响后续规则
                    if ctx.log is not None:
                        await ctx.log(
                            "error",
                            f"[forward] failed: {type(exc).__name__}: {exc}",
                            rule_id=rule.id,
                        )

    async def _do_forward(
        self,
        ctx: PluginContext,
        event: events.NewMessage.Event,
        cfg: dict[str, Any],
        target: int,
    ) -> None:
        """实际执行一次转发：风控 acquire → 按 mode 走不同 send 路径。"""
        # ── 风控 acquire ──
        decision = await ctx.engine.acquire(
            ctx.account_id, "forward_message", peer_id=target
        )
        if not decision.allowed:
            if ctx.log is not None:
                await ctx.log(
                    "info",
                    f"[forward] 被风控丢弃 outcome={decision.outcome}",
                )
            return
        if decision.wait_seconds and decision.wait_seconds > 0:
            await asyncio.sleep(float(decision.wait_seconds))

        mode = cfg.get("mode", "forward_native")
        header = cfg.get("header") or ""
        client = ctx.client

        if mode == "forward_native":
            # 原生转发：携带原作者署名（公开消息可点跳源）
            await event.message.forward_to(target)
        elif mode == "copy_text":
            # 复制文本：不带原作者，header + 原文（空文本 fallback "(empty)"）
            text = (header + (event.message.text or "")) or "(empty)"
            await client.send_message(target, text)
        elif mode == "quote":
            # 引用包装：📨 来自 <群名/用户名/chat_id>
            try:
                src = await event.get_chat()
            except Exception:  # noqa: BLE001
                src = None
            chat_label = (
                getattr(src, "title", None)
                or getattr(src, "username", None)
                or getattr(src, "first_name", None)
                or str(event.chat_id)
            )
            body_text = event.message.text or "(no text)"
            body = f"{header}📨 来自 {chat_label}\n\n{body_text}"
            await client.send_message(target, body)
        elif mode == "link_only":
            # 仅链接：公开超级群 / 频道生成 https://t.me/c/<bare>/<msg_id>；
            # 非公开会话退化成 "消息引用：chat=... id=..."
            link = _build_msg_link(event)
            await client.send_message(target, header + link if header else link)
        else:
            # 兜底：未知 mode 不发送，写一条 warn 方便排查
            if ctx.log is not None:
                await ctx.log("warn", f"[forward] 未知 mode={mode!r}，跳过")


# ─────────────────────────────────────────────────────
# 工具：源筛选 / chat_id 等价展开 / 链接生成 / FloodWait 判定
# ─────────────────────────────────────────────────────
def _match_source(event: Any, cfg: dict[str, Any]) -> bool:
    """按 ``source_kind`` 决定当前消息是否进入转发流水线。

    - ``all``     —— 永远命中（仅靠 include_media / target 兜底过滤）
    - ``peers``   —— 与 ``source_peers`` 列表做"等价 chat_id"交集
    - ``keyword`` —— 文本（小写化）包含关键词；空关键词视为不命中（避免误炸）
    """
    kind = cfg.get("source_kind", "all")
    if kind == "all":
        return True

    if kind == "peers":
        peers = _coerce_int_list(cfg.get("source_peers") or [])
        if not peers:
            return False
        target_set = _expand_chat_id(int(event.chat_id)) if event.chat_id is not None else set()
        for p in peers:
            if target_set & _expand_chat_id(int(p)):
                return True
        return False

    if kind == "keyword":
        kw = (cfg.get("keyword") or "").strip().lower()
        if not kw:
            return False
        text = ""
        try:
            text = event.message.text or event.raw_text or ""
        except Exception:  # noqa: BLE001
            text = getattr(event, "raw_text", "") or ""
        return kw in text.lower()

    return False


def _coerce_int_list(raw: Any) -> list[int]:
    """前端表单里 chat_id 列表是 ``string[]``，比对前转 int；解析失败的项跳过。"""
    out: list[int] = []
    for item in raw or []:
        if isinstance(item, int):
            out.append(item)
            continue
        try:
            out.append(int(str(item).strip()))
        except (TypeError, ValueError):
            continue
    return out


# Telegram 协议里 supergroup / channel 的 chat_id 都是 ``-100xxxxxxxxxx`` 形式；
# basic group 是 ``-xxxxxxxxxx``；私聊是正数。
# 用户从 t.me/c/<id>/<msg> 复制下来的是去掉 -100 的纯数字。
# 为了让用户填什么形式都能命中，把每个 id 展开成它所有合理的等价表示。
_CHANNEL_PREFIX = 1_000_000_000_000  # 即 1e12，supergroup/channel id 的固定前缀


def _expand_chat_id(raw: int) -> set[int]:
    """把一个 chat id 展开成所有可能的等价表示。

    例：
      - 1234567890       → 也能匹配 -1001234567890 / -1234567890
      - -1001234567890   → 同样展开到 1234567890 / -1234567890
    """
    out: set[int] = {raw}
    a = abs(raw)
    out.add(a)
    out.add(-a)
    if a > _CHANNEL_PREFIX:
        bare = a - _CHANNEL_PREFIX
        out.add(bare)
        out.add(-bare)
    else:
        out.add(-(_CHANNEL_PREFIX + a))
    return out


def _build_msg_link(event: Any) -> str:
    """根据 chat_id 生成 t.me/c/<bare>/<msg_id> 链接；非超级群退化成可读字符串。"""
    cid = event.chat_id
    mid = getattr(event.message, "id", None) if getattr(event, "message", None) else None
    if cid is None or mid is None:
        return f"消息引用：chat={cid}, id={mid}"
    sid = str(cid)
    if sid.startswith("-100"):
        return f"https://t.me/c/{sid[4:]}/{mid}"
    return f"消息引用：chat={cid}, id={mid}"


def _is_flood_wait(exc: Exception) -> bool:
    """判断异常是否为 ``FloodWaitError``（不强依赖 telethon 的具体类路径）。"""
    try:
        from telethon.errors import FloodWaitError

        return isinstance(exc, FloodWaitError)
    except Exception:  # pragma: no cover - 测试环境无 telethon 时兜底
        return type(exc).__name__ == "FloodWaitError"


# ─────────────────────────────────────────────────────
# 暴露给 dry-run / 测试使用的内部工具
# ─────────────────────────────────────────────────────
def _dry_run_match(
    cfg: dict[str, Any],
    text: str,
    chat_id: int | None = None,
) -> tuple[bool, str | None]:
    """供 API ``dry-run`` 调用：纯函数判断"是否命中"+ 返回一句话描述。

    返回的 ``output`` 是给前端展示的 "would forward to <target>" 文案，
    与真正转发并无关系（不会真的下发任何 send_message）。
    """

    class _FakeMsg:
        media = None

        def __init__(self, t: str) -> None:
            self.text = t
            self.id = 0

    class _FakeEvent:
        def __init__(self, t: str, cid: int | None) -> None:
            self.raw_text = t
            self.chat_id = cid if cid is not None else 0
            self.message = _FakeMsg(t)
            self.is_private = False
            self.is_group = False
            self.is_channel = False

    event = _FakeEvent(text, chat_id)
    if not _match_source(event, cfg):
        return False, None
    target = cfg.get("target_chat_id")
    mode = cfg.get("mode", "forward_native")
    return True, f"would forward to {target} (mode={mode})"


PLUGIN_CLASS = ForwardPlugin

__all__ = [
    "ForwardPlugin",
    "PLUGIN_CLASS",
    "_build_msg_link",
    "_dry_run_match",
    "_expand_chat_id",
    "_match_source",
]

===== backend/app/worker/plugins/builtin/group_admin/__init__.py =====
"""group_admin 插件包入口：暴露 PLUGIN_CLASS / MANIFEST。"""

from .manifest import MANIFEST
from .plugin import GroupAdminPlugin

PLUGIN_CLASS = GroupAdminPlugin

__all__ = ["GroupAdminPlugin", "MANIFEST", "PLUGIN_CLASS"]

===== backend/app/worker/plugins/builtin/group_admin/manifest.py =====
"""group_admin 插件 manifest。"""

from __future__ import annotations

from app.db.models.feature import FEATURE_GROUP_ADMIN
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key=FEATURE_GROUP_ADMIN,
    display_name="群组管理",
    version="0.1.0",
    author="builtin",
    description="入群欢迎 / 反垃圾 / 黑名单 / 关键词处置（MVP 占位骨架）",
    permissions=["send_message", "edit_message", "read_chat", "delete_message"],
)

__all__ = ["MANIFEST"]

===== backend/app/worker/plugins/builtin/group_admin/plugin.py =====
"""内置插件骨架：群组管理（PRD §E）。

入群欢迎、反垃圾、黑名单、关键词处置等能力将在 V1 实现；MVP 仅注册 feature key。
"""

from __future__ import annotations

from app.db.models.feature import FEATURE_GROUP_ADMIN
from app.worker.plugins.base import Plugin, register


@register
class GroupAdminPlugin(Plugin):
    """群组管理。MVP 骨架。"""

    key = FEATURE_GROUP_ADMIN
    display_name = "群组管理"


__all__ = ["GroupAdminPlugin"]

===== backend/app/worker/plugins/builtin/monitor/__init__.py =====
"""monitor 插件包入口：暴露 PLUGIN_CLASS / MANIFEST。"""

from .manifest import MANIFEST
from .plugin import MonitorPlugin

PLUGIN_CLASS = MonitorPlugin

__all__ = ["MANIFEST", "MonitorPlugin", "PLUGIN_CLASS"]

===== backend/app/worker/plugins/builtin/monitor/manifest.py =====
"""monitor 插件 manifest。"""

from __future__ import annotations

from app.db.models.feature import FEATURE_MONITOR
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key=FEATURE_MONITOR,
    display_name="消息监控",
    version="0.1.0",
    author="builtin",
    description="关键词命中告警 + 跨账号搜索（MVP 占位骨架）",
    permissions=["read_chat"],
)

__all__ = ["MANIFEST"]

===== backend/app/worker/plugins/builtin/monitor/plugin.py =====
"""内置插件骨架：消息监控 / 归档（PRD §G）。

关键词命中告警 + 跨账号搜索在 V1 实现；MVP 仅注册 feature key。
"""

from __future__ import annotations

from app.db.models.feature import FEATURE_MONITOR
from app.worker.plugins.base import Plugin, register


@register
class MonitorPlugin(Plugin):
    """消息监控。MVP 骨架。"""

    key = FEATURE_MONITOR
    display_name = "消息监控"


__all__ = ["MonitorPlugin"]

===== backend/app/worker/plugins/builtin/scheduler/__init__.py =====
"""scheduler 插件包入口：暴露 PLUGIN_CLASS / MANIFEST。"""

from .manifest import MANIFEST
from .plugin import SchedulerPlugin

PLUGIN_CLASS = SchedulerPlugin

__all__ = ["MANIFEST", "PLUGIN_CLASS", "SchedulerPlugin"]

===== backend/app/worker/plugins/builtin/scheduler/manifest.py =====
"""scheduler 插件 manifest。"""

from __future__ import annotations

from app.db.models.feature import FEATURE_SCHEDULER
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key=FEATURE_SCHEDULER,
    display_name="定时任务",
    version="0.1.0",
    author="builtin",
    description="cron 触发 + 多账号广播（MVP 占位骨架）",
    permissions=["send_message", "send_file"],
)

__all__ = ["MANIFEST"]

===== backend/app/worker/plugins/builtin/scheduler/plugin.py =====
"""内置插件骨架：定时任务（PRD §F）。

cron 触发 + 多账号广播在 V1 实现；MVP 仅注册 feature key。
"""

from __future__ import annotations

from app.db.models.feature import FEATURE_SCHEDULER
from app.worker.plugins.base import Plugin, register


@register
class SchedulerPlugin(Plugin):
    """定时任务。MVP 骨架。"""

    key = FEATURE_SCHEDULER
    display_name = "定时任务"


__all__ = ["SchedulerPlugin"]

===== backend/app/worker/plugins/loader.py =====
"""账号级插件加载器：连接 Telethon、实例化每个启用的 [账号 × feature] 插件，并维护其生命周期。

使用流程：
1. ``run_worker`` 在 ``client.connect()`` 前调 ``load_plugins_for_account``，本模块会：
   - 触发内置插件 import（``@register`` 写入全局注册表）
   - 在 ``client`` 上挂一个全局 ``NewMessage(incoming=True)`` 派发器，把消息广播给所有插件
   - 实例化该账号当前 ``account_feature.enabled=True`` 的所有插件，并把状态写回为 active
2. 主进程通过 IPC ``CMD_RELOAD_CONFIG`` 触发 ``reload_account_config`` 实现热更新（拉新 rules / config，
   并对新增 / 移除的 feature 做差量加载与卸载）
3. ``CMD_RELOAD_PLUGIN`` 调 ``reload_plugin``：``importlib.reload`` 单个内置插件模块并重新激活

模块化后插件以"目录"形式存在：
- 内置：``backend/app/worker/plugins/builtin/<key>/{__init__.py, manifest.py, plugin.py}``
- 第三方：``data/plugins/installed/<key>/{__init__.py, manifest.py, plugin.py}``（阶段 B 引入）
每个插件目录的 ``__init__.py`` 必须暴露 ``PLUGIN_CLASS``（Plugin 子类）与 ``MANIFEST``
（``Manifest`` 实例）两个常量；``discover_plugins()`` 扫描时按目录读取这两个常量装载。

任何插件抛出的异常都不会让整个 worker 崩溃；该 plugin 会被标记为 ``failed`` 状态。
"""

from __future__ import annotations

import asyncio
import collections
import importlib
import importlib.util
import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from telethon import TelegramClient, events

from ...db.base import AsyncSessionLocal
from ...db.models.account import Account, HumanizeConfig
from ...db.models.feature import (
    FEATURE_STATE_ACTIVE,
    FEATURE_STATE_DISABLED,
    FEATURE_STATE_FAILED,
    AccountFeature,
)
from ...db.models.ignored_peer import IgnoredPeer
from ...db.models.rule import Rule
from ...redis_client import get_redis
from ...services.rate_limit_service import get_effective
from ..command import register_plugin_command
from ..ipc import RUNTIME_LOG_STREAM, RuntimeLogPayload
from ..ratelimit.engine import RateLimitEngine
from ..ratelimit.humanize import HumanizeOpts
from .base import Plugin, PluginContext, all_plugins, get_plugin
from .manifest import Manifest

log = logging.getLogger(__name__)


# worker 内存里维护的最近活跃 peer 数量上限（超过则按 LRU 丢弃最旧）
RECENT_PEERS_LIMIT = 50


# 内置插件根目录：``backend/app/worker/plugins/builtin``
_BUILTIN_DIR: Path = Path(__file__).parent / "builtin"


def _installed_dir() -> Path:
    """解析第三方插件安装目录：阶段 B 引入，由 ``settings.plugins_installed_dir`` 配置。

    每次调用都重新解析，便于测试通过 monkeypatch settings 实现隔离；
    生产环境下值是稳定的。
    """
    try:
        from ...settings import settings  # 延迟 import 避免循环

        return Path(settings.plugins_installed_dir).resolve()
    except Exception:  # noqa: BLE001
        # settings 加载失败时退化到默认相对路径
        return Path("./data/plugins/installed").resolve()


def _scan_builtin_dirs() -> list[Path]:
    """扫描 builtin 子目录（仅取目录，跳过 ``__pycache__`` 等下划线开头的私有目录）。

    返回值的顺序按文件名字典序，便于测试稳定。
    """
    if not _BUILTIN_DIR.exists():
        return []
    return sorted(
        [p for p in _BUILTIN_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")],
        key=lambda p: p.name,
    )


# 内置插件模块名清单（运行期由扫描得出，保留 tuple 类型以兼容现有测试）
# 每次 import loader 时刷新一次；新增 builtin 子目录无需改这里。
_BUILTIN_MODULES: tuple[str, ...] = tuple(p.name for p in _scan_builtin_dirs())


def _import_builtins() -> None:
    """import 内置插件包，触发各模块的 ``@register`` 装饰器写入注册表。

    模块化重构后此函数等价于"调 ``discover_plugins()`` + 跳过返回值"——
    保留是因为现有调用方（runtime / 测试）仍以这个名字为入口；
    返回值忽略，单纯靠副作用（``@register`` + ``_manifest`` 注入）来工作。
    任意单个插件失败仅记日志，不影响其它插件加载。
    """
    try:
        from . import builtin  # noqa: F401  builtin/__init__.py 也会 re-export
    except Exception:  # noqa: BLE001
        log.exception("import plugins.builtin 失败")
    try:
        # discover_plugins 同时扫描 builtin + installed，并把 _manifest / _source
        # 挂到 plugin 类上；这里只关心其副作用。
        discover_plugins()
    except Exception:  # noqa: BLE001
        log.exception("discover_plugins 失败")


def _load_dir(path: Path, source: str) -> dict[str, type[Plugin]]:
    """从单个插件目录加载 ``PLUGIN_CLASS`` 与 ``MANIFEST``；失败返回 {} 并写日志。

    - ``source="builtin"``：走正常的 ``importlib.import_module`` 路径，包名是
      ``app.worker.plugins.builtin.<key>``，能享受 Python 的 import 缓存。
    - ``source="installed"``：第三方插件解压在 ``data/plugins/installed/<key>/``，
      不属于 ``app.*`` 包；用 ``spec_from_file_location`` + ``submodule_search_locations``
      手工创建模块对象再执行，使其能 ``from .plugin import ...`` 等相对 import。

    无论哪种来源，最终都把 ``Manifest`` 与 ``source`` 写到 plugin 类的 ``_manifest`` /
    ``_source`` 属性上，方便后续运行期、API 层直接读取。
    """
    init_file = path / "__init__.py"
    if not init_file.exists():
        log.warning("插件目录 %s 缺少 __init__.py，跳过", path)
        return {}

    try:
        if source == "builtin":
            mod = importlib.import_module(
                f".builtin.{path.name}", package=__package__
            )
        else:
            # 第三方插件：构造一个独立的模块对象，避免污染 app 包命名空间。
            # 关键：必须把 mod 注册到 sys.modules，否则 ``from .plugin import X``
            # 这种相对 import 会找不到父包。
            import sys as _sys

            mod_name = f"_telebot_installed_plugin_{path.name}"
            spec = importlib.util.spec_from_file_location(
                mod_name,
                init_file,
                submodule_search_locations=[str(path)],
            )
            if spec is None or spec.loader is None:
                log.warning("无法为插件 %s 构造 spec", path)
                return {}
            mod = importlib.util.module_from_spec(spec)
            _sys.modules[mod_name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                _sys.modules.pop(mod_name, None)
                raise
    except Exception:  # noqa: BLE001
        log.exception("加载插件目录 %s 失败", path)
        return {}

    cls = getattr(mod, "PLUGIN_CLASS", None)
    manifest = getattr(mod, "MANIFEST", None)
    if cls is None or manifest is None:
        log.warning("插件 %s 缺少 PLUGIN_CLASS 或 MANIFEST，跳过", path)
        return {}
    if not isinstance(manifest, Manifest):
        log.warning(
            "插件 %s 的 MANIFEST 不是 Manifest 实例 (got %s)，跳过",
            path,
            type(manifest).__name__,
        )
        return {}

    # 把 manifest / source 挂到 plugin 类上，方便 API 层暴露给前端
    cls._manifest = manifest
    cls._source = source

    # 防御性写入注册表：plugin.py 里若有 @register 已经写过；此处再写一次幂等
    # （主要是为了第三方插件——它们的 plugin.py 也应当 @register，但兜底一下）
    from .base import _REGISTRY  # 延迟 import 避免循环

    _REGISTRY[manifest.key] = cls
    return {manifest.key: cls}


def discover_plugins() -> dict[str, type[Plugin]]:
    """按目录扫描 builtin + installed 两个根，返回 ``{key -> Plugin 子类}``。

    - 同名时 ``installed`` 覆盖 ``builtin``（第三方插件可"覆盖升级"内置实现）。
    - 单个插件失败只记日志，不影响其它插件。
    - 不存在 ``data/plugins/installed`` 目录时直接跳过该源。
    """
    out: dict[str, type[Plugin]] = {}
    for sub in _scan_builtin_dirs():
        out.update(_load_dir(sub, source="builtin"))
    installed_dir = _installed_dir()
    if installed_dir.exists():
        for sub in sorted(installed_dir.iterdir(), key=lambda p: p.name):
            if not sub.is_dir() or sub.name.startswith("_"):
                continue
            out.update(_load_dir(sub, source="installed"))
    return out


# ─────────────────────────────────────────────────────
# 每账号一份运行态（worker 进程内单例）
# ─────────────────────────────────────────────────────
class _AccountState:
    """单账号 worker 的插件运行态，包含 engine、client、各插件实例与 ctx。"""

    def __init__(self, account_id: int) -> None:
        self.account_id = account_id
        self.engine: RateLimitEngine | None = None
        self.client: TelegramClient | None = None
        self.redis: Any = None  # redis.asyncio.Redis
        self.contexts: dict[str, PluginContext] = {}  # feature_key -> ctx
        self.instances: dict[str, Plugin] = {}  # feature_key -> Plugin 实例
        # paused 由 runtime 创建并传入；is_set() == True 表示正常运行
        self.paused: asyncio.Event | None = None
        # Sprint2 #3：忽略 peer 名单（int set），从 ignored_peer 表加载，IPC 触发热更
        self.ignored_peers: set[int] = set()
        # Sprint2 #3：最近活跃 peer 的 LRU（peer_id -> {peer_kind, peer_label, ts}）
        # 仅 worker 内存维护；重启后清空。前端不能假设它持久。
        self.recent_peers: collections.OrderedDict[int, dict[str, Any]] = collections.OrderedDict()


# 进程级状态字典（一个 worker 进程通常只服务一个账号；用 dict 是为了灵活）
_STATES: dict[int, _AccountState] = {}


# ─────────────────────────────────────────────────────
# 主入口：load_plugins_for_account
# ─────────────────────────────────────────────────────
async def load_plugins_for_account(
    client: TelegramClient,
    account_id: int,
    paused: asyncio.Event,
    redis: Any,
) -> None:
    """runtime 在 ``client.connect()`` 之前调一次。

    步骤：
      1. import 全部内置插件（首次会触发注册）
      2. 构造 ``RateLimitEngine``（依赖 humanize 配置 + service 层 ``get_effective``）
      3. 在 client 上注册全局 NewMessage 派发，把每条消息按 instances 顺序广播
      4. 加载该账号已启用的 features → ``_activate``
    """
    _import_builtins()

    state = _AccountState(account_id)
    state.client = client
    state.paused = paused
    state.redis = redis
    _STATES[account_id] = state

    # ── 1) 拉取拟人化 + 账号信息构造 engine ──
    async with AsyncSessionLocal() as db:
        acc = await db.get(Account, account_id)
        humanize_row = await db.get(HumanizeConfig, account_id)
    opts = HumanizeOpts(
        jitter_pct=humanize_row.jitter_pct if humanize_row else 15,
        typing_simulate=bool(humanize_row.typing_simulate) if humanize_row else True,
        typing_min_ms=humanize_row.typing_min_ms if humanize_row else 1000,
        typing_max_ms=humanize_row.typing_max_ms if humanize_row else 3000,
        typing_probability=humanize_row.typing_probability if humanize_row else 80,
        read_before_reply=bool(humanize_row.read_before_reply) if humanize_row else True,
        active_window_start=humanize_row.active_window_start if humanize_row else None,
        active_window_end=humanize_row.active_window_end if humanize_row else None,
        cold_start_days=humanize_row.cold_start_days if humanize_row else 7,
        cold_start_until=acc.cold_start_until if acc else None,
    )

    async def _get_eff(aid: int, action: str):
        """engine 用的 get_effective 工厂闭包：每次新开 session，避免共享。"""
        async with AsyncSessionLocal() as db:
            return await get_effective(db, aid, action)

    state.engine = RateLimitEngine(account_id, opts, _get_eff, redis=redis)

    # ── 1.5) 拉取忽略 peer 名单 ──
    await _load_ignored_peers(state)

    # ── 2) 全局事件派发 ──
    @client.on(events.NewMessage(incoming=True))
    async def _dispatch(event):  # noqa: ANN001 - telethon 类型由装饰器约束
        # paused.is_set()==True 表示正常运行；False 时跳过被动派发，保持"暂停主动动作"语义
        # 注：被动接收已由 telethon 自身处理；此处仅控制是否触发我们的插件回调
        if state.paused is not None and not state.paused.is_set():
            return

        # ── Sprint2 #3 ── 在做任何插件分发 / 风控计费之前，先维护 LRU + 检查忽略名单
        pid = event.chat_id
        if pid is not None:
            await _record_recent_peer(state, event)
            if pid in state.ignored_peers:
                # 忽略命中：直接早退，不写日志、不派发、不触发插件
                # （写一条 debug 日志方便排查"为什么没回复"——但只走 logger，不写 runtime_log）
                log.debug("[ignored] account=%s chat_id=%s", account_id, pid)
                return

        # 调试日志：每条 incoming 消息都记一行（包含 chat_id 与前 80 字），方便排查"为什么没回复"
        try:
            peer_kind = (
                "private" if event.is_private
                else "channel" if event.is_channel
                else "group" if event.is_group
                else "?"
            )
            text_preview = (event.raw_text or "")[:80]
            await _log(
                redis,
                account_id,
                "info",
                f"[event] {peer_kind} chat_id={event.chat_id} | {text_preview!r}",
            )
        except Exception:  # noqa: BLE001
            pass

        # 拿一份当前 instances 的快照避免迭代过程中并发改动
        for fkey, inst in list(state.instances.items()):
            ctx = state.contexts.get(fkey)
            if ctx is None:
                continue
            try:
                await inst.on_message(ctx, event)
            except Exception as exc:  # noqa: BLE001
                # plugin 异常归"系统"——这是技术错误不是业务事件
                await _log(
                    redis,
                    account_id,
                    "error",
                    f"插件 {fkey} on_message 异常: {type(exc).__name__}: {exc}",
                    source="system",
                )

    # ── 3) 加载该账号所有已启用 feature ──
    async with AsyncSessionLocal() as db:
        afs = (
            await db.execute(
                select(AccountFeature).where(
                    AccountFeature.account_id == account_id,
                    AccountFeature.enabled.is_(True),
                )
            )
        ).scalars().all()
        for af in afs:
            await _activate(db, state, af, redis)


# ─────────────────────────────────────────────────────
# 单 feature 激活
# ─────────────────────────────────────────────────────
async def _activate(db, state: _AccountState, af: AccountFeature, redis: Any) -> None:
    """根据 ``account_feature`` 行实例化对应插件，调 ``on_startup``，写状态。"""
    cls = get_plugin(af.feature_key)
    if cls is None:
        await _log(
            redis,
            state.account_id,
            "warn",
            f"feature {af.feature_key} 已启用但未找到插件实现",
        )
        await db.execute(
            update(AccountFeature)
            .where(
                AccountFeature.account_id == state.account_id,
                AccountFeature.feature_key == af.feature_key,
            )
            .values(state=FEATURE_STATE_FAILED, last_error="plugin not found")
        )
        await db.commit()
        return

    # 拉规则（按 priority 倒序：值越大越先匹配）
    rules = (
        await db.execute(
            select(Rule)
            .where(
                Rule.account_id == state.account_id,
                Rule.feature_key == af.feature_key,
                Rule.enabled.is_(True),
            )
            .order_by(Rule.priority.desc())
        )
    ).scalars().all()

    inst = cls()
    # 阶段 C：第三方插件 (source="installed") 拿到的 client 走沙箱包装；
    # builtin 仍直接拿真 client（避免对原代码做改动）。
    plugin_client: Any = state.client
    plugin_source = getattr(cls, "_source", "builtin")
    plugin_manifest = getattr(cls, "_manifest", None)
    if plugin_source == "installed" and state.client is not None:
        from .sandbox import SandboxClient  # 延迟 import 避免循环

        perms = list(plugin_manifest.permissions) if plugin_manifest else []
        plugin_client = SandboxClient(
            state.client, perms, plugin_key=af.feature_key
        )

    ctx = PluginContext(
        account_id=state.account_id,
        feature_key=af.feature_key,
        config=dict(af.config or {}),
        rules=list(rules),
        client=plugin_client,
        engine=state.engine,
        redis=state.redis or redis,
        log=_make_logger(redis, state.account_id),
    )

    try:
        await inst.on_startup(ctx)
    except Exception as exc:  # noqa: BLE001
        await db.execute(
            update(AccountFeature)
            .where(
                AccountFeature.account_id == state.account_id,
                AccountFeature.feature_key == af.feature_key,
            )
            .values(state=FEATURE_STATE_FAILED, last_error=str(exc))
        )
        await db.commit()
        await _log(
            redis,
            state.account_id,
            "error",
            f"插件 {af.feature_key} startup 失败: {exc}",
        )
        return

    state.instances[af.feature_key] = inst
    state.contexts[af.feature_key] = ctx

    # 暴露插件命令到 TG 命令分发表
    for cname, fn in (cls.commands or {}).items():
        register_plugin_command(cname, _wrap_cmd(fn, ctx))

    await db.execute(
        update(AccountFeature)
        .where(
            AccountFeature.account_id == state.account_id,
            AccountFeature.feature_key == af.feature_key,
        )
        .values(state=FEATURE_STATE_ACTIVE, last_error=None)
    )
    await db.commit()


def _wrap_cmd(fn, ctx: PluginContext):
    """把插件 ``commands`` 里登记的 5 参数 handler 包成命令分发期望的 4 参数签名。"""

    async def w(client, event, args, account_id):  # noqa: ANN001
        await fn(client, event, args, account_id, ctx)

    return w


def _make_logger(redis: Any, account_id: int):
    """构造一个 ctx.log 协程，写到 ``runtime_log_stream``。"""

    async def _writer(level: str, message: str, **detail: Any) -> None:
        await _log(redis, account_id, level, message, **detail)

    return _writer


# ─────────────────────────────────────────────────────
# 配置热更新：reload_account_config
# ─────────────────────────────────────────────────────
async def reload_account_config(account_id: int, payload: dict | None = None) -> None:
    """收到 IPC ``reload_config`` 时调用：

    - **先重新扫描插件目录**：``discover_plugins()`` 会发现 ``data/plugins/installed``
      下新解压的 zip 插件，并把它们注册到 ``_REGISTRY``，让后续 ``_activate`` 能找到
    - 已实例化的 feature：刷新 ``ctx.config`` / ``ctx.rules``；若该 feature 已被禁用则 shutdown
    - 数据库新增的 enabled feature：调 ``_activate`` 加载

    任何异常都吞掉，热更新失败不应让 worker 崩溃。
    """
    state = _STATES.get(account_id)
    if state is None:
        return
    redis = state.redis or get_redis()

    # 阶段 B：先扫描一次目录，把新装的第三方插件注册进来；存量 builtin 走 import 缓存几乎零开销
    try:
        discover_plugins()
    except Exception:  # noqa: BLE001
        log.exception("reload_account_config 时 discover_plugins 失败")

    async with AsyncSessionLocal() as db:
        # 1) 现有实例：刷新或卸载
        for fkey, inst in list(state.instances.items()):
            af = (
                await db.execute(
                    select(AccountFeature).where(
                        AccountFeature.account_id == account_id,
                        AccountFeature.feature_key == fkey,
                    )
                )
            ).scalar_one_or_none()
            if af is None or not af.enabled:
                ctx = state.contexts.get(fkey)
                if ctx is not None:
                    try:
                        await inst.on_shutdown(ctx)
                    except Exception:  # noqa: BLE001
                        log.exception("on_shutdown 失败 feature=%s", fkey)
                state.instances.pop(fkey, None)
                state.contexts.pop(fkey, None)
                # 同时写状态为 disabled，便于前端展示
                await db.execute(
                    update(AccountFeature)
                    .where(
                        AccountFeature.account_id == account_id,
                        AccountFeature.feature_key == fkey,
                    )
                    .values(state=FEATURE_STATE_DISABLED)
                )
                await db.commit()
                continue
            # 仍启用：刷新 rules + config
            rules = (
                await db.execute(
                    select(Rule)
                    .where(
                        Rule.account_id == account_id,
                        Rule.feature_key == fkey,
                        Rule.enabled.is_(True),
                    )
                    .order_by(Rule.priority.desc())
                )
            ).scalars().all()
            ctx = state.contexts[fkey]
            ctx.config = dict(af.config or {})
            ctx.rules = list(rules)

        # 2) 处理新增的 enabled feature
        afs = (
            await db.execute(
                select(AccountFeature).where(
                    AccountFeature.account_id == account_id,
                    AccountFeature.enabled.is_(True),
                )
            )
        ).scalars().all()
        for af in afs:
            if af.feature_key not in state.instances:
                await _activate(db, state, af, redis)

    await _log(redis, account_id, "info", "插件配置已热更新")


# ─────────────────────────────────────────────────────
# 单插件热重载：reload_plugin
# ─────────────────────────────────────────────────────
async def reload_plugin(account_id: int, plugin_key: str | None) -> None:
    """``importlib.reload`` 一个内置插件模块，并重新激活。

    仅支持内置插件；第三方插件目前不在 MVP 范围。
    """
    if not plugin_key:
        return
    state = _STATES.get(account_id)
    if state is None:
        return
    redis = state.redis or get_redis()

    # 1) 先 shutdown 旧实例
    if plugin_key in state.instances:
        try:
            await state.instances[plugin_key].on_shutdown(state.contexts[plugin_key])
        except Exception:  # noqa: BLE001
            log.exception("on_shutdown 失败 feature=%s", plugin_key)
        state.instances.pop(plugin_key, None)
        state.contexts.pop(plugin_key, None)

    # 2) reload 模块（仅内置）
    if plugin_key not in _BUILTIN_MODULES:
        await _log(redis, account_id, "warn", f"reload_plugin 仅支持内置插件: {plugin_key}")
        return
    try:
        # 模块化后每个 builtin 插件是子包：``manifest.py`` + ``plugin.py`` + ``__init__.py``。
        # 按"manifest → plugin → 子包入口"顺序 reload，确保 @register 重新触发，
        # MANIFEST / PLUGIN_CLASS 取到最新版本。
        for sub in ("manifest", "plugin"):
            try:
                m = importlib.import_module(
                    f".builtin.{plugin_key}.{sub}", package=__package__
                )
                importlib.reload(m)
            except ModuleNotFoundError:
                # 旧式单文件 builtin（理论上重构后已不存在），忽略
                pass
        pkg_mod = importlib.import_module(
            f".builtin.{plugin_key}", package=__package__
        )
        importlib.reload(pkg_mod)
    except Exception as exc:  # noqa: BLE001
        await _log(redis, account_id, "error", f"reload {plugin_key} 失败: {exc}")
        return

    # 3) 重新激活
    async with AsyncSessionLocal() as db:
        af = (
            await db.execute(
                select(AccountFeature).where(
                    AccountFeature.account_id == account_id,
                    AccountFeature.feature_key == plugin_key,
                )
            )
        ).scalar_one_or_none()
        if af is not None and af.enabled:
            await _activate(db, state, af, redis)
    await _log(redis, account_id, "info", f"插件 {plugin_key} 已重载")


# ─────────────────────────────────────────────────────
# 写运行日志的便利函数
# ─────────────────────────────────────────────────────
async def _log(
    redis: Any,
    account_id: int | None,
    level: str,
    message: str,
    *,
    source: str = "event",
    **detail: Any,
) -> None:
    """写入 ``runtime_log_stream``，主进程批量消费落库。任何异常吞掉。

    source 语义（前端 Logs 页 tab 区分）：
    - ``"event"``（loader 默认）  — incoming 消息事件 / plugin 命中 / 命令派发
    - ``"system"``                — plugin 内部错误 / 加载失败等技术异常应显式传

    历史数据里也会出现 ``"plugin"`` 旧值，API 层做了别名映射。
    """
    try:
        payload = RuntimeLogPayload(
            account_id=account_id,
            level=level,  # type: ignore[arg-type]
            source=source,
            message=message,
            detail=detail or None,
        )
        await redis.rpush(RUNTIME_LOG_STREAM, payload.encode())
    except Exception:  # noqa: BLE001
        log.exception("写 runtime_log_stream 失败 account=%s", account_id)


# 测试与外部需要时可用：列出当前所有已注册的 plugin 类
def registered_plugins() -> dict[str, type[Plugin]]:
    """便于测试 / 调试：返回当前注册表副本。"""
    return all_plugins()


# ─────────────────────────────────────────────────────
# Sprint2 #3：忽略 peer + 最近活跃 peer
# ─────────────────────────────────────────────────────
async def _load_ignored_peers(state: _AccountState) -> None:
    """从 ``ignored_peer`` 表把当前账号的所有 peer_id 装进内存 set。

    任何异常都吞掉——失败时退化为"空名单"，等价于不忽略，业务侧不至于挂。
    """
    try:
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(IgnoredPeer.peer_id).where(
                        IgnoredPeer.account_id == state.account_id
                    )
                )
            ).scalars().all()
        state.ignored_peers = {int(pid) for pid in rows}
    except Exception:  # noqa: BLE001
        log.exception("加载忽略名单失败 account=%s", state.account_id)
        state.ignored_peers = set()


def _classify_peer(event: Any) -> str:
    """把 Telethon event 的会话类型归一化为 ``private/group/supergroup/channel``。

    supergroup 与 channel 都属于 ``is_channel``；通过 chat_id 的 -100 前缀区分
    （Telegram 协议约定，supergroup 与 channel 的 chat_id 都以 -100 开头，
    我们这里粗略把"是 group 又是 channel 的"当作 supergroup）。
    """
    try:
        if event.is_private:
            return "private"
        if event.is_channel and not event.is_group:
            return "channel"
        if event.is_group and event.is_channel:
            return "supergroup"
        return "group"
    except Exception:  # noqa: BLE001
        return "private"


async def _record_recent_peer(state: _AccountState, event: Any) -> None:
    """把当前 event 的 peer 写入 LRU；超出上限则丢最旧。

    会尝试调 ``event.get_chat()`` 拿群名/用户名作为 ``peer_label``，失败则用 chat_id 字符串兜底。
    异常一律吞掉——这条 LRU 只是 UI 辅助，不能影响主流程。
    """
    pid = event.chat_id
    if pid is None:
        return
    try:
        kind = _classify_peer(event)
        label: str | None
        try:
            chat = await event.get_chat()
            label = (
                getattr(chat, "title", None)
                or getattr(chat, "username", None)
                or getattr(chat, "first_name", None)
                or str(pid)
            )
        except Exception:  # noqa: BLE001
            label = str(pid)
        state.recent_peers[pid] = {
            "peer_kind": kind,
            "peer_label": label,
            "ts": time.time(),
        }
        # OrderedDict.move_to_end 把"最近一次写入的 peer"挪到末尾，实现 LRU
        state.recent_peers.move_to_end(pid)
        while len(state.recent_peers) > RECENT_PEERS_LIMIT:
            state.recent_peers.popitem(last=False)
    except Exception:  # noqa: BLE001
        log.exception("维护 recent_peers 失败 account=%s pid=%s", state.account_id, pid)


async def reload_ignored_peers(account_id: int) -> None:
    """IPC ``reload_ignored`` 入口：从 DB 重新拉一遍名单。

    若该账号在本进程没有运行态（worker 未起 / 已退出），静默忽略。
    """
    state = _STATES.get(account_id)
    if state is None:
        return
    await _load_ignored_peers(state)
    redis = state.redis or get_redis()
    await _log(
        redis,
        account_id,
        "info",
        f"忽略名单已热更新（共 {len(state.ignored_peers)} 个 peer）",
    )


def get_recent_peers(account_id: int) -> list[dict[str, Any]]:
    """IPC ``get_recent_peers`` 应答：返回当前账号最近活跃 peer 列表。

    顺序：最新 → 最旧（OrderedDict 末尾是最近写入的，所以反向遍历）。
    若该账号在本进程没有运行态，返回空列表。
    """
    state = _STATES.get(account_id)
    if state is None:
        return []
    out: list[dict[str, Any]] = []
    for pid, info in reversed(state.recent_peers.items()):
        out.append(
            {
                "peer_id": int(pid),
                "peer_kind": info.get("peer_kind") or "private",
                "peer_label": info.get("peer_label"),
                "ts": float(info.get("ts") or 0.0),
            }
        )
    return out


__all__ = [
    "RECENT_PEERS_LIMIT",
    "discover_plugins",
    "get_recent_peers",
    "load_plugins_for_account",
    "registered_plugins",
    "reload_account_config",
    "reload_ignored_peers",
    "reload_plugin",
]

===== backend/app/worker/plugins/manifest.py =====
"""插件 Manifest 数据类。

每个插件目录里的 ``manifest.py`` 顶层导出 ``MANIFEST: Manifest`` 实例，
loader 在扫描目录阶段读取这个常量来决定加载方式、显示名、版本以及（阶段 C 引入的）权限范围。

字段说明：
- ``key``：插件唯一 key（与 ``Plugin.key`` 一致；同时也是 ``feature.key``）
- ``display_name``：用户可见名称
- ``version``：语义化版本号；第三方插件通过 zip / 仓库升级时按此对比
- ``author``：作者；内置默认 ``"builtin"``
- ``description``：一句话描述（前端"插件管理"列表展示）
- ``requires_features``：声明依赖的其它插件 key 列表（先注册了才能加载本插件）
- ``config_schema``：``rule.config`` 的 JSON Schema，前端可据此生成动态表单
- ``permissions``：阶段 C 沙箱用的能力声明（如 ``send_message`` / ``edit_message``）
- ``on_install``：可选的安装钩子模块路径（阶段 B/C 用，目前未启用）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Manifest:
    """插件元数据。"""

    key: str
    display_name: str
    version: str = "0.1.0"
    author: str = "builtin"
    description: str = ""
    # 依赖其它插件的 key（先加载它们再加载本插件；阶段 A 暂不强制校验）
    requires_features: list[str] = field(default_factory=list)
    # rule.config 的 JSON Schema，可选；前端编辑器据此渲染
    config_schema: dict[str, Any] | None = None
    # ===== 阶段 C 引入：能力清单 =====
    # 默认给三类常用能力，避免内置插件 manifest 漏写时被沙箱拦截
    permissions: list[str] = field(
        default_factory=lambda: ["send_message", "edit_message", "read_chat"]
    )
    # 可选：安装钩子（python module path），阶段 B/C 启用
    on_install: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化成可写入 DB / JSON 的 dict。"""
        return {
            "key": self.key,
            "display_name": self.display_name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "requires_features": list(self.requires_features),
            "config_schema": self.config_schema,
            "permissions": list(self.permissions),
            "on_install": self.on_install,
        }


__all__ = ["Manifest"]

===== backend/app/worker/plugins/sandbox.py =====
"""插件运行时沙箱（阶段 C）。

目标：限制第三方插件 (``installed`` source) 能调用的 Telethon API 范围；
内置 builtin 插件直接拿到原 ``TelegramClient``，不走沙箱。

设计：
- ``ALLOWED_API`` 把 manifest 中声明的"能力名" (e.g. ``send_message``) 映射到一组
  允许调用的 ``TelegramClient`` 方法名。
- ``SandboxClient`` 是一个动态代理：``__getattr__`` 时检查目标属性是否在允许集中，
  否则抛 ``PermissionError``。
- ``_log_call``：每次调用都会写一条 debug 日志（非 await，避免污染主流程）。

权限名清单（一期）：
- ``send_message``    : ``send_message`` / ``respond`` / ``reply``
- ``edit_message``    : ``edit`` / ``edit_message``
- ``read_chat``       : ``get_messages`` / ``get_chat`` / ``iter_messages``
- ``send_file``       : ``send_file``
- ``join_chat``       : ``join_chat``
- ``delete_message``  : ``delete_messages``

约束：
- 仅拦截顶层 ``getattr``；插件取到方法后多次调用都不再过 check（性能）
- 通用 ``connect/disconnect/_*`` 等内部方法默认放行（避免插件起步时崩）
- 调用方 (loader) 在 ``installed`` 源 plugin 启动时把 ``ctx.client`` 替成
  ``SandboxClient(real, perms)``；``builtin`` 不变

注意：MTProto raw API（``__call__``）目前未拦截 → V1.5 再做更细的方法白名单，
本期沙箱主要是"低门槛防误用"，不能阻挡恶意插件。
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


# 能力名 → 允许的 TelegramClient 方法集
ALLOWED_API: dict[str, frozenset[str]] = {
    "send_message": frozenset({"send_message", "respond", "reply"}),
    "edit_message": frozenset({"edit", "edit_message"}),
    "read_chat": frozenset({"get_messages", "get_chat", "iter_messages"}),
    "send_file": frozenset({"send_file"}),
    "join_chat": frozenset({"join_chat"}),
    "delete_message": frozenset({"delete_messages"}),
}


# 默认放行集合：连接 / 关闭 / 自身查询等，不属于业务 API，避免插件起步崩
_ALWAYS_ALLOWED: frozenset[str] = frozenset(
    {
        "connect",
        "disconnect",
        "is_connected",
        "is_user_authorized",
        "loop",
        "session",
        "get_me",
        # Telethon Helper 上下文管理器
        "__aenter__",
        "__aexit__",
        # 这些内部属性在 telethon 内部链式调用里频繁取，不能拦
        "_loop",
        "_sender",
    }
)


def resolve_permissions(perms: list[str] | None) -> frozenset[str]:
    """把权限名列表展开成允许的方法名集合（去重）。

    未识别的权限名只写 warn 日志，不抛异常——插件 manifest 写错时业务可降级。
    """
    out: set[str] = set()
    for p in perms or []:
        methods = ALLOWED_API.get(p)
        if methods is None:
            log.warning("manifest 引用未知权限名 %r", p)
            continue
        out |= methods
    return frozenset(out)


class SandboxClient:
    """``TelegramClient`` 的最小化代理：只放行 manifest 声明的方法。

    ``__getattr__`` 是唯一拦截点：插件每次取属性都会过 check，
    取到的对象（method）后续怎么用我们就不管了——这是性能权衡。
    """

    __slots__ = ("_real", "_allowed", "_plugin_key", "_perms")

    def __init__(
        self,
        real: Any,
        perms: list[str] | None,
        *,
        plugin_key: str = "?",
    ) -> None:
        self._real = real
        # frozenset 避免被插件 mutate
        self._allowed = resolve_permissions(perms)
        self._plugin_key = plugin_key
        self._perms = list(perms or [])

    def __getattr__(self, name: str) -> Any:
        # __slots__ 上的字段走原生协议，不会触发 __getattr__；这里确保不递归
        if name.startswith("_") and name not in _ALWAYS_ALLOWED:
            # _real / _allowed 等私有字段早被 __slots__ 处理；剩下的 _xxx 默认放行
            return getattr(self._real, name)
        if name in _ALWAYS_ALLOWED or name in self._allowed:
            return getattr(self._real, name)
        # 不在允许集内 → 抛 PermissionError；plugin 应在 manifest.permissions 中声明
        raise PermissionError(
            f"插件 {self._plugin_key!r} 缺少权限调用 client.{name}; "
            f"请在 manifest.permissions 中声明对应能力（持有: {self._perms}）"
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<SandboxClient plugin={self._plugin_key} perms={self._perms}>"


__all__ = ["ALLOWED_API", "SandboxClient", "resolve_permissions"]

===== backend/app/worker/ratelimit/__init__.py =====
"""风控引擎子包：对外暴露 ``RateLimitEngine`` / ``RateLimitDecision`` / ``rate_limited``。

worker 与插件直接 ``from app.worker.ratelimit import RateLimitEngine, rate_limited``。
"""

from .engine import (
    EffectiveLimits,
    RateLimitDecision,
    RateLimitEngine,
    rate_limited,
)
from .exceptions import AccountPaused, FloodWaitTriggered, RateLimitError
from .humanize import HumanizeOpts

__all__ = [
    "AccountPaused",
    "EffectiveLimits",
    "FloodWaitTriggered",
    "HumanizeOpts",
    "RateLimitDecision",
    "RateLimitEngine",
    "RateLimitError",
    "rate_limited",
]

===== backend/app/worker/ratelimit/buckets.py =====
"""Redis Lua 多窗口令牌桶（秒/分/时/天 + 同会话桶）。

所有窗口共用一段原子 Lua 脚本，避免在 Python 端做"先 ZCARD 再 ZADD"的非原子组合
带来的并发漏洞。脚本内部用 ``redis.call('TIME')`` 取秒级时间戳，避免主备时间漂移
导致排队判定不一致。

key 命名约定：
    rl:{account_id}:{action}:s          # 1 秒窗口 zset
    rl:{account_id}:{action}:m          # 60 秒窗口 zset
    rl:{account_id}:{action}:h          # 3600 秒窗口 zset
    rl:{account_id}:{action}:d          # 86400 秒窗口 zset
    rl:{account_id}:{action}:peer:{pid}:m   # 同会话 60 秒窗口 zset

每个窗口阈值若 <=0 表示该窗口不参与（继承层把空字段当作 None → 0）。
脚本返回 [allowed, retry_after_seconds, hit_window_idx]。
"""

from __future__ import annotations

# ── Lua 脚本：原子地检查 N 个窗口并按需消费 ──────────────────────
# 关键设计：
#   1) 用 KEYS 列表传入 N 个 zset key（顺序固定 = ARGV 阈值顺序）
#   2) score=now 时间戳，每次 ZADD 一个唯一 member（now-rand），ZCARD 数当前条数
#   3) 任一窗口超阈值即返回 (0, retry_after, idx)，retry_after = 该窗口最早一条 + span - now
#   4) 全通过且 consume=1 时，统一对各窗口 ZADD + EXPIRE（TTL = span * 1.2 兜底回收）
#   5) consume=0 表示仅查询不扣减，用于先做"双扣检查"（per-action + api_total）的预演
_LUA = """
-- KEYS[1..N]: 各窗口 zset key（按 ARGV[1] 给出的窗口数顺序）
-- ARGV[1]: 窗口数 N
-- ARGV[2..N+1]: 各窗口长度（秒）
-- ARGV[N+2..2N+1]: 各窗口阈值（>0 表示生效，<=0 表示该窗口跳过）
-- ARGV[2N+2]: 是否真正消费一个令牌（1）还是仅查询（0）
-- 返回：{allowed, retry_after_seconds, hit_window_idx_1based}
local now = tonumber(redis.call('TIME')[1])
local n = tonumber(ARGV[1])
local consume = tonumber(ARGV[2 * n + 2])

-- 唯一 member，避免同一秒内并发 ZADD 互相覆盖
local member = string.format('%d-%d', now, math.random(1, 1000000000))

-- ① 先做一次纯检查：任一窗口超限立即返回
for i = 1, n do
  local span = tonumber(ARGV[1 + i])
  local limit = tonumber(ARGV[1 + n + i])
  if limit and limit > 0 then
    local k = KEYS[i]
    redis.call('ZREMRANGEBYSCORE', k, '-inf', now - span)
    local cur = tonumber(redis.call('ZCARD', k))
    if cur >= limit then
      local oldest = redis.call('ZRANGE', k, 0, 0, 'WITHSCORES')
      local retry = 0
      if oldest and oldest[2] then
        retry = span - (now - tonumber(oldest[2]))
      end
      if retry < 0 then retry = 0 end
      return {0, retry, i}
    end
  end
end

-- ② 全部窗口通过 → 真正消费令牌
if consume == 1 then
  for i = 1, n do
    local span = tonumber(ARGV[1 + i])
    local limit = tonumber(ARGV[1 + n + i])
    if limit and limit > 0 then
      local k = KEYS[i]
      redis.call('ZADD', k, now, member)
      redis.call('EXPIRE', k, math.ceil(span * 1.2))
    end
  end
end
return {1, 0, 0}
"""


# 五个窗口的固定顺序（KEYS / ARGV 必须和这个顺序一致）
_WINDOWS = ("second", "minute", "hour", "day", "same_peer_minute")
_SPANS = (1, 60, 3600, 86400, 60)  # 单位：秒


class TokenBuckets:
    """对 ``RateLimitRule.{per_second/minute/hour/day/same_peer_per_minute}`` 五窗口的封装。

    单实例持有 evalsha 的 SHA 缓存；遇到 NOSCRIPT 自动重新 ``script_load``。
    """

    def __init__(self, redis) -> None:
        self.redis = redis
        # Lua 脚本上传后的 SHA（lazy 初始化）
        self._sha: str | None = None

    # ─────────────────────────────────────────────────────
    # 内部工具
    # ─────────────────────────────────────────────────────
    async def _ensure_loaded(self) -> str:
        """首次调用前把 Lua 脚本上传到 Redis，缓存 SHA 复用。"""
        if self._sha is None:
            self._sha = await self.redis.script_load(_LUA)
        return self._sha

    @staticmethod
    def _keys(account_id: int, action: str, peer_id: int | None) -> list[str]:
        """构造五个窗口对应的 Redis key（顺序与 ``_WINDOWS`` 严格一致）。"""
        base = f"rl:{account_id}:{action}"
        # 同会话桶：peer_id 为空时给一个占位 key，调用方传 same_peer_per_minute=0 让其失效
        peer_part = f"peer:{peer_id}:m" if peer_id is not None else "peer:none:m"
        return [
            f"{base}:s",
            f"{base}:m",
            f"{base}:h",
            f"{base}:d",
            f"{base}:{peer_part}",
        ]

    # ─────────────────────────────────────────────────────
    # 主接口
    # ─────────────────────────────────────────────────────
    async def check_and_consume(
        self,
        account_id: int,
        action: str,
        per_second: int | None,
        per_minute: int | None,
        per_hour: int | None,
        per_day: int | None,
        same_peer_per_minute: int | None,
        peer_id: int | None = None,
        consume: bool = True,
    ) -> tuple[bool, float, int]:
        """检查五窗口并按需扣减。

        返回 ``(allowed, retry_after_seconds, hit_window_idx)``：
          - ``allowed=True`` 表示通过；``retry_after`` 总是 0。
          - ``allowed=False`` 表示被某个窗口挡住，``hit_window_idx`` 为 1..5（顺序见 ``_WINDOWS``）。
          - ``retry_after`` 是浮点秒，调用方应当至少 sleep 这么久再重试。
        """
        sha = await self._ensure_loaded()
        keys = self._keys(account_id, action, peer_id)
        # 同会话窗口：peer_id 为空或显式禁用时强制 0
        sp_limit = (same_peer_per_minute or 0) if peer_id is not None else 0
        argv = [
            "5",
            *[str(s) for s in _SPANS],
            str(per_second or 0),
            str(per_minute or 0),
            str(per_hour or 0),
            str(per_day or 0),
            str(sp_limit),
            "1" if consume else "0",
        ]
        try:
            res = await self.redis.evalsha(sha, len(keys), *keys, *argv)
        except Exception as exc:  # 兼容 NOSCRIPT / 连接重置等
            # NOSCRIPT 重新 load 后再试一次
            msg = str(exc).upper()
            if "NOSCRIPT" not in msg:
                # 其它错误：清掉 sha 触发下次重新加载，并向上抛
                self._sha = None
                raise
            self._sha = None
            sha = await self._ensure_loaded()
            res = await self.redis.evalsha(sha, len(keys), *keys, *argv)
        # redis-py decode_responses=True 时返回 list[str|int]，这里统一转成基本类型
        allowed = int(res[0]) == 1
        retry = float(res[1])
        idx = int(res[2])
        return allowed, retry, idx

    async def usage(self, account_id: int, action: str, window: str = "minute") -> int:
        """实时查询单个窗口的当前已用量（用于风控仪表盘）。

        参数 ``window`` 取值见 ``_WINDOWS``。先 ZREMRANGEBYSCORE 清掉过期再 ZCARD，
        和 Lua 脚本的语义保持一致。
        """
        if window not in _WINDOWS:
            raise ValueError(f"未知窗口：{window}")
        idx = _WINDOWS.index(window)
        keys = self._keys(account_id, action, None)
        from time import time as _now

        now = int(_now())
        await self.redis.zremrangebyscore(keys[idx], "-inf", now - _SPANS[idx])
        return int(await self.redis.zcard(keys[idx]))

    async def usage_all_windows(self, account_id: int, action: str) -> dict[str, int]:
        """一次返回某动作四个主窗口（不含同会话）的当前用量，便于仪表盘渲染。"""
        out: dict[str, int] = {}
        for w in ("second", "minute", "hour", "day"):
            out[w] = await self.usage(account_id, action, w)
        return out

===== backend/app/worker/ratelimit/engine.py =====
"""风控引擎：三层叠加 + 5 抑制策略 + Telegram 异常自动响应。

⚠ 延迟处理铁律
─────────────────────────────────────────────────────
1. ``acquire()`` 返回 ``wait_seconds`` 给调用方；调用方在真正发请求前 ``await asyncio.sleep(wait_seconds)``。
2. ``policy=queue``：``wait_seconds = 触发窗口的 retry_after + 拟人化抖动``，``allowed=True``。
3. ``policy=backoff``：``wait_seconds = backoff_base * 2^(streak-1)``（封顶到 backoff_max）+ 抖动；``allowed=True``。
4. ``policy=drop``：``wait_seconds=0、allowed=False、outcome=drop``，调用方应直接 return。
5. ``policy=pause``：``wait_seconds=+inf、allowed=False、outcome=pause``；engine 同时把 account.status 改 paused 并广播 IPC 事件。
6. ``policy=notify``：仅落事件不阻塞，``allowed=True、wait_seconds=0、outcome=ok``。

⚠ 异常处理铁律
─────────────────────────────────────────────────────
1. ``FloodWaitError``：写 ``RateLimitOverride(action, multiplier=0.7, ttl=30min)`` + ``OUTCOME_FLOODWAIT`` 事件 +
   把 account.status 改成 ``floodwait`` 并广播；后续 ``acquire`` 自动按 0.7× 折扣阈值。
2. ``PeerFloodError``：写 ``RateLimitOverride(action="dm_stranger", multiplier=0.0, ttl=24h)`` →
   等同停用陌生人私聊 24 小时；落 ``OUTCOME_PEERFLOOD`` 事件。
3. ``SlowModeWaitError``：不写 override；只对该 peer/动作排队 ``e.seconds``；落 ``OUTCOME_SLOWMODE`` 事件。
4. ``AuthKeyUnregistered/SessionRevoked/UserDeactivated``：让 worker 上抛 ``EVT_LOGIN_REQUIRED``，
   engine 自身只落事件（``outcome=drop``，detail 携带异常名）。
5. ``PhoneNumberFloodError``：与 FloodWait 同处理，但 multiplier=0.5、TTL=2h（更严格）。
6. 其他 ``RPCError``：不当作风控触发，向上抛由插件层处理。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import update

from ...db.base import AsyncSessionLocal
from ...db.models.account import (
    ACCOUNT_STATUS_FLOODWAIT,
    ACCOUNT_STATUS_PAUSED,
    Account,
)
from ...db.models.rate_limit import (
    OUTCOME_BACKOFF,
    OUTCOME_DROP,
    OUTCOME_FLOODWAIT,
    OUTCOME_OK,
    OUTCOME_PAUSE,
    OUTCOME_PEERFLOOD,
    OUTCOME_QUEUED,
    OUTCOME_SLOWMODE,
    POLICY_BACKOFF,
    POLICY_DROP,
    POLICY_NOTIFY,
    POLICY_PAUSE,
    POLICY_QUEUE,
)
from ...redis_client import get_redis
from ..ipc import (
    EVT_LOGIN_REQUIRED,
    EVT_RATELIMIT,
    EVT_STATUS,
    RATELIMIT_EVENT_STREAM,
    RateLimitEventPayload,
    event_channel,
    make_event,
)
from .buckets import TokenBuckets
from .exceptions import AccountPaused, FloodWaitTriggered
from .humanize import HumanizeOpts, cold_start_factor, in_active_window, jitter, seconds_until_active_window
from .overrides import add_override, get_multiplier

# Telethon 异常按需 lazy import：本模块在 API 层也会被 import，避免 telethon 缺失时崩溃。
try:  # pragma: no cover - 仅在缺失 telethon 的极端环境跳过
    from telethon.errors import (  # type: ignore[import-not-found]
        AuthKeyUnregisteredError,
        FloodWaitError,
        PeerFloodError,
        PhoneNumberFloodError,
        SessionRevokedError,
        SlowModeWaitError,
        UserDeactivatedError,
    )
except Exception:  # pragma: no cover
    # 占位：纯文档/单测环境下（无 telethon 时）这些类只用作 isinstance 判断
    class _Missing(Exception):
        seconds: int = 0

    AuthKeyUnregisteredError = _Missing  # type: ignore[assignment, misc]
    FloodWaitError = _Missing  # type: ignore[assignment, misc]
    PeerFloodError = _Missing  # type: ignore[assignment, misc]
    PhoneNumberFloodError = _Missing  # type: ignore[assignment, misc]
    SessionRevokedError = _Missing  # type: ignore[assignment, misc]
    SlowModeWaitError = _Missing  # type: ignore[assignment, misc]
    UserDeactivatedError = _Missing  # type: ignore[assignment, misc]


log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# 公开数据结构（CONTRACTS.md 强约束）
# ─────────────────────────────────────────────────────
@dataclass
class RateLimitDecision:
    """``acquire`` 的返回值，外部 plugin 直接消费。

    字段语义：
      - ``allowed``: 是否允许调用方继续发请求；False 时 plugin 应立即停止本次动作。
      - ``wait_seconds``: 调用方在发请求前应 sleep 多久；inf 表示账号已暂停。
      - ``outcome``: 与 DB ``rate_limit_event.outcome`` 同义，会被落到事件流。
      - ``reason``: 给日志/UI 看的中文原因（可选）。
    """

    allowed: bool
    wait_seconds: float
    outcome: str
    reason: str | None = None


@dataclass
class EffectiveLimits:
    """三层合并 + override 折算后的最终阈值（service 层填充，engine 消费）。

    把 ``policy``、``backoff_*`` 也带在这里，避免 engine 单独再查一遍 rule 表。
    ``disabled=True`` 表示该账号该动作被显式禁用（rule.enabled=False 或 multiplier=0）。
    """

    per_second: int | None = None
    per_minute: int | None = None
    per_hour: int | None = None
    per_day: int | None = None
    same_peer_per_minute: int | None = None
    policy: str = POLICY_QUEUE
    backoff_base: int = 5
    backoff_max: int = 1800
    disabled: bool = False


# 兼容旧命名（plan 里写的是 _EffectiveLimits）
_EffectiveLimits = EffectiveLimits


# ─────────────────────────────────────────────────────
# 主动动作识别（用于"活跃时段"判断；被动接收无视该限制）
# ─────────────────────────────────────────────────────
_PROACTIVE_ACTIONS: frozenset[str] = frozenset(
    {
        "send_message_private",
        "send_message_group",
        "edit_message",
        "delete_message",
        "forward_message",
        "callback_query",
        "join_chat",
        "leave_chat",
        "create_chat",
        "invite_user",
        "dm_stranger",
        "update_profile",
        "upload_file",
    }
)


def _is_proactive(action: str) -> bool:
    return action in _PROACTIVE_ACTIONS


def _scale_limits(eff: EffectiveLimits, mult: float) -> EffectiveLimits:
    """按 multiplier 折算阈值。mult==1.0 直接返回原对象避免无谓拷贝。"""
    if mult >= 0.999 and mult <= 1.001:
        return eff

    def _s(v: int | None) -> int | None:
        if v is None:
            return None
        # 折算到 0 时返回 0；engine 把 0 视作"该窗口禁用"
        return max(0, int(v * mult))

    return EffectiveLimits(
        per_second=_s(eff.per_second),
        per_minute=_s(eff.per_minute),
        per_hour=_s(eff.per_hour),
        per_day=_s(eff.per_day),
        same_peer_per_minute=_s(eff.same_peer_per_minute),
        policy=eff.policy,
        backoff_base=eff.backoff_base,
        backoff_max=eff.backoff_max,
        disabled=eff.disabled or mult <= 0.0,
    )


# 类型别名：service 层提供的"取有效阈值"协程
GetEffectiveFn = Callable[[int, str], Awaitable[EffectiveLimits]]


class RateLimitEngine:
    """风控引擎，每个 worker 进程实例化一次。

    构造参数：
      - ``account_id``: 当前 worker 服务的账号
      - ``humanize``: ``HumanizeOpts``（由 service 从 ``HumanizeConfig`` 加载）
      - ``get_effective``: 协程，``(account_id, action) -> EffectiveLimits``，
        由 ``services.rate_limit_service.get_effective_factory`` 提供，避免 engine
        直接耦合 DB session。
      - ``redis``: 可选注入；不传则取全局 ``get_redis()``（测试时方便用 fakeredis）。
    """

    def __init__(
        self,
        account_id: int,
        humanize: HumanizeOpts,
        get_effective: GetEffectiveFn,
        redis=None,
    ) -> None:
        self.account_id = account_id
        self.humanize = humanize
        self.get_effective = get_effective
        # 允许测试注入 fakeredis
        self.redis = redis if redis is not None else get_redis()
        self.buckets = TokenBuckets(self.redis)
        # 每个 action 维护"连续失败"计数，policy=backoff 时用于指数退避
        self._backoff_streak: dict[str, int] = {}
        # 内存里的暂停标记，避免后续频繁查 DB
        self._paused = False

    # ─────────────────────────────────────────────────────
    # 主接口：调用方在发请求前调一次
    # ─────────────────────────────────────────────────────
    async def acquire(
        self,
        account_id: int,
        action: str,
        peer_id: int | None = None,
    ) -> RateLimitDecision:
        """检查能否继续；返回 decision。

        调用约定见模块顶部"延迟处理铁律"。
        """
        # 防御：对错的 account_id 直接 drop（一般不会发生）
        if account_id != self.account_id:
            log.warning(
                "engine.acquire 收到 account_id 不一致：传入 %s，本 engine %s",
                account_id,
                self.account_id,
            )
            return RateLimitDecision(False, 0.0, OUTCOME_DROP, reason="account mismatch")

        # 内存级暂停：直接拒绝
        if self._paused:
            return RateLimitDecision(False, float("inf"), OUTCOME_PAUSE, reason="账号已被风控暂停")

        # ── 1. 活跃时段（仅对主动动作生效）──
        if _is_proactive(action) and not in_active_window(self.humanize):
            wait = seconds_until_active_window(self.humanize)
            await self._emit(action, OUTCOME_QUEUED, detail={"reason": "out_of_active_window", "wait": wait})
            return RateLimitDecision(False, wait, OUTCOME_QUEUED, reason="不在活跃时段")

        # ── 2. 取有效阈值（service 层做三层合并）──
        try:
            eff = await self.get_effective(account_id, action)
        except Exception as exc:
            # 取阈值失败时不能让流量穿透：保守按 queue + 1s 处理
            log.exception("get_effective 失败 action=%s: %s", action, exc)
            await self._emit(action, OUTCOME_QUEUED, detail={"reason": "config_unavailable"})
            return RateLimitDecision(True, 1.0, OUTCOME_QUEUED, reason="风控配置暂不可用")

        if eff.disabled:
            await self._emit(action, OUTCOME_DROP, detail={"reason": "disabled"})
            return RateLimitDecision(False, 0.0, OUTCOME_DROP, reason="该动作已禁用")

        # ── 3. 应用 override（FloodWait 等临时折扣）+ 冷启动渐进 ──
        try:
            mult_action = await get_multiplier(self.redis, account_id, action)
        except Exception:
            mult_action = 1.0
        cold_factor = cold_start_factor(self.humanize)
        eff = _scale_limits(eff, mult_action * cold_factor)
        if eff.disabled:
            # 折算后等同禁用
            await self._emit(action, OUTCOME_DROP, detail={"reason": "override_zero"})
            return RateLimitDecision(False, 0.0, OUTCOME_DROP, reason="临时衰减已将阈值压到 0")

        # ── 4. 双扣检查：先不扣减预演（per-action + api_total），全过再扣 ──
        allowed_a, retry_a, idx_a = await self.buckets.check_and_consume(
            account_id,
            action,
            eff.per_second,
            eff.per_minute,
            eff.per_hour,
            eff.per_day,
            eff.same_peer_per_minute,
            peer_id=peer_id,
            consume=False,
        )
        # api_total 兜底桶（所有 MTProto 调用合计）
        try:
            eff_total_raw = await self.get_effective(account_id, "api_total")
        except Exception:
            eff_total_raw = EffectiveLimits()
        try:
            mult_total = await get_multiplier(self.redis, account_id, "api_total")
        except Exception:
            mult_total = 1.0
        eff_total = _scale_limits(eff_total_raw, mult_total * cold_factor)
        allowed_t, retry_t, idx_t = await self.buckets.check_and_consume(
            account_id,
            "api_total",
            eff_total.per_second,
            eff_total.per_minute,
            eff_total.per_hour,
            eff_total.per_day,
            None,
            peer_id=None,
            consume=False,
        )

        if allowed_a and allowed_t:
            # 真正消费令牌（对 per-action 与 api_total 各扣一次）
            await self.buckets.check_and_consume(
                account_id,
                action,
                eff.per_second,
                eff.per_minute,
                eff.per_hour,
                eff.per_day,
                eff.same_peer_per_minute,
                peer_id=peer_id,
                consume=True,
            )
            await self.buckets.check_and_consume(
                account_id,
                "api_total",
                eff_total.per_second,
                eff_total.per_minute,
                eff_total.per_hour,
                eff_total.per_day,
                None,
                peer_id=None,
                consume=True,
            )
            # 命中即重置该 action 的连续失败计数
            self._backoff_streak[action] = 0
            # 拟人化抖动：基线 200ms 加 ±jitter
            wait = jitter(0.2, self.humanize.jitter_pct)
            return RateLimitDecision(True, wait, OUTCOME_OK)

        # ── 5. 超限 → 走策略分支 ──
        retry = max(retry_a, retry_t)
        # 给 retry 也加抖动，避免多 worker 在同一秒同步重试
        retry = jitter(retry, self.humanize.jitter_pct)
        hit_window = idx_a if not allowed_a else idx_t
        return await self._apply_policy(action, eff, retry, hit_window)

    # ─────────────────────────────────────────────────────
    # 策略分支
    # ─────────────────────────────────────────────────────
    async def _apply_policy(
        self,
        action: str,
        eff: EffectiveLimits,
        retry: float,
        hit_window: int,
    ) -> RateLimitDecision:
        """根据 ``eff.policy`` 决定 wait_seconds 与是否阻塞。"""
        detail = {"hit_window": hit_window, "retry_after": retry}

        if eff.policy == POLICY_DROP:
            await self._emit(action, OUTCOME_DROP, detail=detail)
            return RateLimitDecision(False, 0.0, OUTCOME_DROP, reason="超限丢弃")

        if eff.policy == POLICY_BACKOFF:
            # 指数退避：每次未通过 streak +1，wait = base * 2^(streak-1)，封顶到 max
            self._backoff_streak[action] = self._backoff_streak.get(action, 0) + 1
            base = eff.backoff_base * (2 ** (self._backoff_streak[action] - 1))
            wait = min(int(base), int(eff.backoff_max))
            # 取 backoff 与 retry_after 的较大值（避免 backoff 还没等够桶就重试）
            wait = jitter(max(float(wait), retry), self.humanize.jitter_pct)
            await self._emit(
                action,
                OUTCOME_BACKOFF,
                detail={**detail, "wait": wait, "streak": self._backoff_streak[action]},
            )
            # backoff 同样要求调用方 sleep 后重试 → allowed=True
            return RateLimitDecision(True, wait, OUTCOME_BACKOFF, reason="指数退避")

        if eff.policy == POLICY_PAUSE:
            await self._pause_account(reason="超限自动暂停")
            await self._emit(action, OUTCOME_PAUSE, detail=detail)
            return RateLimitDecision(False, float("inf"), OUTCOME_PAUSE, reason="超限自动暂停")

        if eff.policy == POLICY_NOTIFY:
            # 仅告警不阻塞：落事件 outcome=ok 但 detail 标 notify_only
            await self._emit(action, OUTCOME_OK, detail={**detail, "notify_only": True})
            return RateLimitDecision(True, 0.0, OUTCOME_OK, reason="仅告警未抑制")

        # 默认 queue：等到桶恢复
        await self._emit(action, OUTCOME_QUEUED, detail={**detail, "wait": retry})
        return RateLimitDecision(True, retry, OUTCOME_QUEUED, reason="排队等待")

    # ─────────────────────────────────────────────────────
    # Telegram 异常自动响应（worker 在 except 中调用）
    # ─────────────────────────────────────────────────────
    async def on_flood_wait(self, action: str, exc: Exception) -> None:
        """``FloodWaitError`` 触发：写 override 让同动作阈值 ×0.7，TTL 30 分钟。

        同时把 account.status 改 ``floodwait`` 并通过 IPC 通知主进程更新 UI。
        """
        seconds = int(getattr(exc, "seconds", 0) or 0)
        log.warning("FloodWait %ds on action=%s account=%s", seconds, action, self.account_id)
        try:
            async with AsyncSessionLocal() as db:
                await add_override(
                    db,
                    self.redis,
                    self.account_id,
                    action=action,
                    multiplier=0.7,
                    ttl_seconds=30 * 60,
                    reason=f"FloodWait {seconds}s",
                )
                # 把账号置 floodwait 状态便于前端展示（不阻塞执行）
                await db.execute(
                    update(Account)
                    .where(Account.id == self.account_id)
                    .values(status=ACCOUNT_STATUS_FLOODWAIT)
                )
                await db.commit()
        except Exception:
            log.exception("写 FloodWait override 失败")
        await self._emit(
            action,
            OUTCOME_FLOODWAIT,
            detail={"seconds": seconds, "multiplier": 0.7, "ttl_seconds": 30 * 60},
        )
        await self._publish_status(ACCOUNT_STATUS_FLOODWAIT, reason=f"FloodWait {seconds}s")

    async def on_peer_flood(self, action: str = "dm_stranger") -> None:
        """``PeerFloodError`` 触发：停用陌生人私聊 24 小时。"""
        log.warning("PeerFlood account=%s 停用 %s 24h", self.account_id, action)
        try:
            async with AsyncSessionLocal() as db:
                await add_override(
                    db,
                    self.redis,
                    self.account_id,
                    action=action,
                    multiplier=0.0,
                    ttl_seconds=24 * 3600,
                    reason="PeerFlood 自动停用 24h",
                )
        except Exception:
            log.exception("写 PeerFlood override 失败")
        await self._emit(
            action,
            OUTCOME_PEERFLOOD,
            detail={"action_disabled_for": "24h", "multiplier": 0.0},
        )

    async def on_slow_mode(self, action: str, exc: Exception, peer_id: int | None) -> None:
        """``SlowModeWaitError`` 触发：本次单 peer 等待 ``e.seconds`` 秒；不写 override。"""
        seconds = int(getattr(exc, "seconds", 0) or 0)
        log.info(
            "SlowMode %ds on action=%s peer=%s account=%s",
            seconds,
            action,
            peer_id,
            self.account_id,
        )
        await self._emit(
            action,
            OUTCOME_SLOWMODE,
            detail={"seconds": seconds, "peer_id": peer_id},
        )

    async def on_phone_flood(self, action: str, exc: Exception) -> None:
        """``PhoneNumberFloodError``：比 FloodWait 更严重，按 0.5×、TTL 2h 处理。"""
        seconds = int(getattr(exc, "seconds", 0) or 0)
        log.warning("PhoneNumberFlood account=%s action=%s", self.account_id, action)
        try:
            async with AsyncSessionLocal() as db:
                await add_override(
                    db,
                    self.redis,
                    self.account_id,
                    action=action,
                    multiplier=0.5,
                    ttl_seconds=2 * 3600,
                    reason="PhoneNumberFlood 自动收紧",
                )
        except Exception:
            log.exception("写 PhoneNumberFlood override 失败")
        await self._emit(
            action,
            OUTCOME_FLOODWAIT,
            detail={
                "seconds": seconds,
                "kind": "phone_number_flood",
                "multiplier": 0.5,
                "ttl_seconds": 2 * 3600,
            },
        )

    async def on_session_invalid(self, action: str, exc: Exception) -> None:
        """session 失效：engine 仅落事件 + 广播 ``EVT_LOGIN_REQUIRED``，由 supervisor 处置账号状态。"""
        exc_name = type(exc).__name__
        log.error("session 失效 account=%s action=%s exc=%s", self.account_id, action, exc_name)
        await self._emit(
            action,
            OUTCOME_DROP,
            detail={"reason": "session_invalid", "exc": exc_name},
        )
        # 广播 login_required 事件，supervisor 会监听此事件改 account.status
        try:
            await self.redis.publish(
                event_channel(self.account_id),
                make_event(EVT_LOGIN_REQUIRED, exc=exc_name, action=action),
            )
        except Exception:
            log.exception("广播 EVT_LOGIN_REQUIRED 失败")

    # ─────────────────────────────────────────────────────
    # 内部
    # ─────────────────────────────────────────────────────
    async def _pause_account(self, reason: str = "rate_limit") -> None:
        """把账号写为 paused 状态并广播 IPC（幂等）。"""
        if self._paused:
            return
        self._paused = True
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(Account)
                    .where(Account.id == self.account_id)
                    .values(status=ACCOUNT_STATUS_PAUSED)
                )
                await db.commit()
        except Exception:
            log.exception("更新 account.status=paused 失败")
        await self._publish_status(ACCOUNT_STATUS_PAUSED, reason=reason)

    async def _publish_status(self, status: str, reason: str | None = None) -> None:
        """通知主进程更新 UI 状态。"""
        try:
            await self.redis.publish(
                event_channel(self.account_id),
                make_event(EVT_STATUS, status=status, reason=reason),
            )
        except Exception:
            log.exception("广播状态变更失败 account=%s status=%s", self.account_id, status)

    async def _emit(self, action: str, outcome: str, detail: dict | None = None) -> None:
        """落事件流（list 落库由主进程消费）+ 实时广播给监听者。

        任何异常都吞掉：风控事件不应反过来影响业务。
        """
        payload = RateLimitEventPayload(
            account_id=self.account_id,
            action=action,
            outcome=outcome,
            detail=detail,
        )
        try:
            await self.redis.rpush(RATELIMIT_EVENT_STREAM, payload.encode())
        except Exception:
            log.exception("写 RATELIMIT_EVENT_STREAM 失败")
        try:
            await self.redis.publish(
                event_channel(self.account_id),
                make_event(EVT_RATELIMIT, action=action, outcome=outcome, detail=detail),
            )
        except Exception:
            log.exception("广播 EVT_RATELIMIT 失败")


# ─────────────────────────────────────────────────────
# 装饰器：把 acquire + 异常映射封成一行用法
# ─────────────────────────────────────────────────────
def rate_limited(action: str):
    """装饰一个发起 TG API 调用的协程方法（需要 ``self.engine`` 或 ``engine`` kwarg）。

    用法：
        class MyPlugin:
            @rate_limited("send_message_group")
            async def send(self, peer_id, text):
                await self.client.send_message(peer_id, text)

    行为：
      - 调 ``engine.acquire(action, peer_id=...)`` 拿决策
      - ``decision.allowed=False`` 且 outcome=drop → 直接返回 None
      - ``decision.allowed=False`` 且 outcome=pause → 抛 ``AccountPaused``
      - ``decision.wait_seconds>0`` → ``await asyncio.sleep(wait)``
      - 真正调被装饰函数；按 Telegram 异常分支调对应 ``on_*`` 回调，再视情况向上抛
    """

    def deco(fn):
        async def wrapper(self, *args, **kwargs):
            engine: RateLimitEngine | None = getattr(self, "engine", None) or kwargs.get("engine")
            assert engine is not None, "rate_limited 需要 self.engine 或 engine kwarg"

            # peer_id 推断：优先 kwargs，再尝试位置参数（约定第一个位置参数是 peer）
            peer_id: int | None = kwargs.get("peer_id")
            if peer_id is None and args:
                first = args[0]
                if isinstance(first, int):
                    peer_id = first
            if not isinstance(peer_id, int):
                peer_id = None

            decision = await engine.acquire(engine.account_id, action, peer_id=peer_id)
            if not decision.allowed:
                if decision.outcome == OUTCOME_DROP:
                    return None
                if decision.outcome == OUTCOME_PAUSE:
                    raise AccountPaused(decision.reason or "账号被风控暂停")
                # queued/backoff 在 allowed=True 分支处理；走到这里属于异常返回
                log.warning("意外的 not-allowed outcome=%s", decision.outcome)
                return None
            if decision.wait_seconds > 0:
                await asyncio.sleep(decision.wait_seconds)

            try:
                return await fn(self, *args, **kwargs)
            except FloodWaitError as e:
                await engine.on_flood_wait(action, e)
                raise FloodWaitTriggered(int(getattr(e, "seconds", 0) or 0), action) from e
            except PeerFloodError:
                await engine.on_peer_flood("dm_stranger")
                raise
            except SlowModeWaitError as e:
                await engine.on_slow_mode(action, e, peer_id)
                # 不主动重试，把异常透传给调用方决定
                raise
            except PhoneNumberFloodError as e:
                await engine.on_phone_flood(action, e)
                raise
            except (
                AuthKeyUnregisteredError,
                SessionRevokedError,
                UserDeactivatedError,
            ) as e:
                await engine.on_session_invalid(action, e)
                raise

        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        wrapper.__name__ = getattr(fn, "__name__", "rate_limited_wrapper")
        return wrapper

    return deco


__all__ = [
    "EffectiveLimits",
    "GetEffectiveFn",
    "RateLimitDecision",
    "RateLimitEngine",
    "rate_limited",
]

===== backend/app/worker/ratelimit/exceptions.py =====
"""风控模块内部异常。

外部调用方一般只需要捕获 ``AccountPaused``（账号被风控暂停时不应继续重试）。
``FloodWaitTriggered`` 是装饰器在向上抛 Telethon ``FloodWaitError`` 之前包了一层
便于日志区分；调用方若不关心可让其继续向上抛即可，engine 内部已经写过 override 与事件。
"""

from __future__ import annotations


class RateLimitError(Exception):
    """风控基类。所有由本模块抛出的异常都继承自它。"""


class AccountPaused(RateLimitError):
    """账号已被风控暂停（``policy=pause`` 触发或外部强制暂停）。

    收到该异常表示当前 worker 不应再发起任何主动 TG 调用，应停止 / 退出循环。
    """


class FloodWaitTriggered(RateLimitError):
    """Telegram 抛了 ``FloodWaitError``，已被 engine 处理（写入 override + 落事件）。

    保留 ``seconds`` 与 ``action`` 字段供上层做额外业务处理（例如把当前任务挂起）。
    """

    def __init__(self, seconds: int, action: str) -> None:
        super().__init__(f"FloodWait {seconds}s on {action}")
        self.seconds = int(seconds)
        self.action = action

===== backend/app/worker/ratelimit/humanize.py =====
"""拟人化：抖动 / 打字模拟 / 阅读延迟 / 活跃时段 / 冷启动渐进。

设计原则：所有函数都是 **纯函数 / 可测**，副作用（实际 sleep、调 ``client.action``）
集中在 ``simulate_typing`` / ``simulate_read`` 两个协程里。engine 在 ``acquire`` 返回
``wait_seconds`` 之前调 ``jitter()`` 给基线加上随机抖动。

Telethon 依赖只在 type-checking 时引入，避免本模块被 API 层引入时强制拉 telethon。
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型提示用，不在运行时强引入 telethon
    from telethon import TelegramClient


@dataclass
class HumanizeOpts:
    """拟人化运行时配置。

    与 ``HumanizeConfig`` ORM 字段一一对应；engine 由 service 层传入。
    """

    jitter_pct: int = 15
    typing_simulate: bool = True
    typing_min_ms: int = 1000
    typing_max_ms: int = 3000
    typing_probability: int = 80
    read_before_reply: bool = True
    active_window_start: time | None = None
    active_window_end: time | None = None
    cold_start_days: int = 7
    cold_start_until: date | None = None


def jitter(base_seconds: float, pct: int) -> float:
    """对基线秒数加 ±pct% 的均匀抖动。

    pct<=0 时直接返回基线；负值结果会 clamp 到 0，避免反向 sleep。
    """
    if pct <= 0:
        return max(0.0, float(base_seconds))
    delta = float(base_seconds) * pct / 100.0
    return max(0.0, float(base_seconds) + random.uniform(-delta, delta))


def in_active_window(opts: HumanizeOpts, now: datetime | None = None) -> bool:
    """是否在主动动作允许的活跃时段内。

    规则：
      - 起止任一未配置 → 视为始终允许（True）
      - start <= end：常规白天窗口
      - start > end：跨夜窗口，例如 22:00–06:00
    """
    if opts.active_window_start is None or opts.active_window_end is None:
        return True
    cur = (now or datetime.now()).time()
    s, e = opts.active_window_start, opts.active_window_end
    if s == e:
        # 起止相同：等于"24h 全开"，避免出现死区
        return True
    if s <= e:
        return s <= cur <= e
    # 跨夜：在 start 之后或 end 之前都算允许
    return cur >= s or cur <= e


def cold_start_factor(opts: HumanizeOpts, today: date | None = None) -> float:
    """冷启动渐进系数：返回 0.3..1.0 之间的浮点数。

    设计：
      - 未设 ``cold_start_until`` → 1.0（完全放开）
      - today >= cold_start_until → 1.0（冷启动期已过）
      - 否则随剩余天数线性插值：剩余越多系数越低（越保守）
        progress = 1 - days_left/days_total
        factor = 0.3 + 0.7 * progress
    """
    if opts.cold_start_until is None:
        return 1.0
    today = today or date.today()
    if today >= opts.cold_start_until:
        return 1.0
    days_left = (opts.cold_start_until - today).days
    days_total = max(1, opts.cold_start_days or 7)
    progress = max(0.0, 1.0 - days_left / days_total)
    factor = 0.3 + 0.7 * progress
    # clamp 到 [0.3, 1.0] 防御
    return min(1.0, max(0.3, factor))


def seconds_until_active_window(opts: HumanizeOpts, now: datetime | None = None) -> float:
    """距离下一个活跃时段开始的秒数（粗略）。

    用于 policy=queue 在"不在活跃时段"时给出合理的 wait_seconds。
    """
    if opts.active_window_start is None:
        return 60.0
    from datetime import timedelta as _td

    now = now or datetime.now()
    target_today = datetime.combine(now.date(), opts.active_window_start)
    if target_today > now:
        return (target_today - now).total_seconds()
    return (target_today + _td(days=1) - now).total_seconds()


# ─────────────────────────────────────────────────────
# 副作用部分：真正调用 Telethon（异常一律吞掉，不让拟人化挡住业务）
# ─────────────────────────────────────────────────────
async def simulate_typing(client: TelegramClient, peer: Any, opts: HumanizeOpts) -> None:
    """按概率发送 ``typing`` action 一段随机时长，模拟人在打字。

    任何异常都吞掉：拟人化失败不应影响业务发送。
    """
    if not opts.typing_simulate:
        return
    if opts.typing_probability <= 0:
        return
    if random.randint(1, 100) > opts.typing_probability:
        return
    lo = max(0, int(opts.typing_min_ms))
    hi = max(lo, int(opts.typing_max_ms))
    duration_ms = random.randint(lo, hi) if hi > lo else lo
    try:
        async with client.action(peer, "typing"):
            await asyncio.sleep(duration_ms / 1000)
    except Exception:
        # 拟人化是 best-effort，吞掉异常即可
        pass


async def simulate_read(client: TelegramClient, peer: Any, opts: HumanizeOpts) -> None:
    """自动回复前先模拟"已读"：随机延迟 0.5~2s 后调 send_read_acknowledge。"""
    if not opts.read_before_reply:
        return
    delay = random.uniform(0.5, 2.0)
    await asyncio.sleep(delay)
    try:
        await client.send_read_acknowledge(peer)
    except Exception:
        pass

===== backend/app/worker/ratelimit/overrides.py =====
"""临时阈值衰减（FloodWait/PeerFlood 等触发后短期收紧）。

写：DB ``rate_limit_override`` 表 + Redis 缓存（带 TTL，便于 engine 高频读取）。
读：engine 在 ``acquire`` 流程里调 ``get_multiplier`` 拿到当前 multiplier，按比例
折算各窗口阈值后再做 token bucket 检查；multiplier=0 等同临时禁用该动作。

清理：``cleanup_expired`` 由主进程每分钟调用一次，删 DB 里 expires_at 已过期的行；
Redis 端依赖 EXPIRE 自动到期，无需显式清理。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.rate_limit import RateLimitOverride


def _redis_key(account_id: int, action: str) -> str:
    """Redis 缓存 key，与 DB 表行一一对应。"""
    return f"rlovr:{account_id}:{action}"


async def add_override(
    db: AsyncSession,
    redis,
    account_id: int,
    action: str,
    multiplier: float,
    ttl_seconds: int,
    reason: str = "",
) -> RateLimitOverride:
    """新增（或覆盖）一条临时阈值衰减。

    - 同一 (account_id, action) 已有未过期 override 时直接覆盖（保留最严的策略：
      取较小的 multiplier 与较大的 ttl，避免 FloodWait 多次重叠时被悄悄放宽）。
    - DB 与 Redis **同步写入**：DB 失败时整个操作回滚，Redis 不会留下脏数据。
    """
    now = datetime.now(UTC)
    new_expires = now + timedelta(seconds=ttl_seconds)

    # 取已有未过期 override（如果有）做"取严"合并
    existing = (
        await db.execute(
            select(RateLimitOverride).where(
                RateLimitOverride.account_id == account_id,
                RateLimitOverride.action == action,
                RateLimitOverride.expires_at > now,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # 取较小 multiplier（更严格） + 较大 expires_at（更长 TTL）
        existing.multiplier = min(float(existing.multiplier), float(multiplier))
        if new_expires > existing.expires_at:
            existing.expires_at = new_expires
        if reason:
            existing.reason = reason
        record = existing
    else:
        record = RateLimitOverride(
            account_id=account_id,
            action=action,
            multiplier=float(multiplier),
            expires_at=new_expires,
            reason=reason or None,
        )
        db.add(record)

    await db.commit()

    # 同步写 Redis 缓存。multiplier 用 str 存，读侧 float() 转回。
    effective_ttl = max(1, int((record.expires_at - now).total_seconds()))
    await redis.set(_redis_key(account_id, action), str(float(record.multiplier)), ex=effective_ttl)
    return record


async def get_multiplier(redis, account_id: int, action: str) -> float:
    """从 Redis 拿当前 multiplier，未命中视作 1.0（不打折）。"""
    val = await redis.get(_redis_key(account_id, action))
    if val is None:
        return 1.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 1.0


async def list_active(db: AsyncSession, account_id: int) -> list[RateLimitOverride]:
    """查询某账号所有未过期 override，给 API ``GET .../overrides`` 用。"""
    now = datetime.now(UTC)
    res = await db.execute(
        select(RateLimitOverride)
        .where(
            RateLimitOverride.account_id == account_id,
            RateLimitOverride.expires_at > now,
        )
        .order_by(RateLimitOverride.expires_at.desc())
    )
    return list(res.scalars().all())


async def cleanup_expired(db: AsyncSession) -> int:
    """删除所有已过期 override，返回删除条数。

    Redis 端的 key 自带 TTL 不需要清理；DB 端只是定期回收，避免 ``rate_limit_override``
    表无限增长。建议主进程每 60s 调一次。
    """
    now = datetime.now(UTC)
    res = await db.execute(delete(RateLimitOverride).where(RateLimitOverride.expires_at < now))
    await db.commit()
    return int(res.rowcount or 0)


async def drop_override(db: AsyncSession, redis, account_id: int, action: str) -> int:
    """显式撤销某 action 的临时衰减（手动放开 FloodWait 等）。"""
    res = await db.execute(
        delete(RateLimitOverride).where(
            RateLimitOverride.account_id == account_id,
            RateLimitOverride.action == action,
        )
    )
    await db.commit()
    await redis.delete(_redis_key(account_id, action))
    return int(res.rowcount or 0)

===== backend/app/worker/runtime.py =====
"""每账号 worker 子进程主入口。

设计要点：
- 子进程 entrypoint 是 ``worker_main(account_id)``；主进程 supervisor 用
  ``multiprocessing.Process(target=worker_main, args=(aid,))`` 拉起。
- worker 负责连 TG / 注册事件 / 监听 IPC / 把日志和限速事件写回 Redis stream。
- 所有 DB 写操作由主进程统一处理（消费 Redis stream）；worker 只读 DB（启动时拉一次配置）。
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from telethon.errors import (
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserDeactivatedError,
)

from ..crypto import decrypt_str
from ..db.base import AsyncSessionLocal
from ..db.models.account import Account, Proxy
from ..db.models.command import AccountCommandLink, CommandTemplate, LLMProvider
from ..db.models.system import SystemSetting
from ..redis_client import get_redis
from ..settings import settings as app_settings
from .command import CommandContext, make_command_handler, set_command_context
from .ipc import (
    CMD_FETCH_AVATAR,
    CMD_GET_RECENT_PEERS,
    CMD_PAUSE,
    CMD_PING,
    CMD_RELOAD_COMMANDS,
    CMD_RELOAD_CONFIG,
    CMD_RELOAD_IGNORED,
    CMD_RELOAD_PLUGIN,
    CMD_RESUME,
    CMD_STOP,
    EVT_LOGIN_REQUIRED,
    EVT_PONG,
    EVT_STATUS,
    GCMD_KILL_SWITCH,
    GCMD_RELOAD_GLOBAL,
    GLOBAL_CHANNEL,
    RUNTIME_LOG_STREAM,
    IPCMessage,
    RuntimeLogPayload,
    cmd_channel,
    event_channel,
    make_cmd,
    make_event,
)
from .tg_client import build_client

log = logging.getLogger(__name__)


async def run_worker(account_id: int) -> None:
    """worker 主协程；返回即代表退出（supervisor 决定是否重启）。"""
    redis = get_redis()

    # 启动时一次性读取账号 + 代理 + 设备伪装 profile
    async with AsyncSessionLocal() as db:
        account = (
            await db.execute(select(Account).where(Account.id == account_id))
        ).scalar_one_or_none()
        if not account:
            await _log(redis, account_id, "error", f"账号 {account_id} 不存在")
            return
        proxy = await db.get(Proxy, account.proxy_id) if account.proxy_id else None
        # 解析设备伪装：账号绑定 → 系统默认 → 硬编码兜底
        from ..services.device_profile import resolve_for_account
        device_profile = await resolve_for_account(db, account)

    # paused.is_set() == True  → 正常运行
    # paused.is_set() == False → 主动动作被暂停（被动接收照常）
    paused = asyncio.Event()
    paused.set()

    client = build_client(account, proxy, device_profile)
    make_command_handler(client, account_id)

    # 初始化命令派发上下文（含模板 + LLM provider 字典；由 IPC reload_commands 热更新）
    await _refresh_command_context(account_id)

    # ⚠ 顺序：必须先 connect，再加载插件。
    #
    # 插件的 on_startup 钩子可能要直接访问 TG（注册 event handler 之外，
    # 比如查 dialogs / 启动定时任务用的 self_id）；如果在 connect 之前调用，
    # 这些 API 会因 "not connected" 报错。把 connect 放最前面，并在 connect
    # 失败时直接返回，避免给插件留半连接的 client。
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await _publish(
                redis, account_id, EVT_LOGIN_REQUIRED, message="session 失效，请重新登录"
            )
            return

        # connect 成功后再加载插件
        # D Agent 的 plugin loader 会通过 hook 接到 client 上；
        # 这里 try-import：D 没写完时不影响 worker 拉起。
        try:
            from .plugins.loader import load_plugins_for_account  # type: ignore

            await load_plugins_for_account(client, account_id, paused, redis)
        except ImportError:
            await _log(redis, account_id, "warn", "插件系统尚未就绪（D Agent 待完成）")
        except Exception as e:
            await _log(redis, account_id, "error", f"加载插件失败: {e}")

        me = await client.get_me()
        # 顺便回填 tg_user_id / tg_username（旧账号迁移 + 用户在 TG 改用户名时同步）
        try:
            new_tg_user_id = getattr(me, "id", None)
            new_tg_username = getattr(me, "username", None) or None
            async with AsyncSessionLocal() as db:
                acc = await db.get(Account, account_id)
                if acc is not None:
                    changed = False
                    if new_tg_user_id is not None and acc.tg_user_id != new_tg_user_id:
                        acc.tg_user_id = new_tg_user_id
                        changed = True
                    if acc.tg_username != new_tg_username:
                        acc.tg_username = new_tg_username
                        changed = True
                    if changed:
                        await db.commit()
        except Exception as e:  # noqa: BLE001
            # 回填失败不影响 worker 继续运行
            await _log(redis, account_id, "warn", f"同步 TG 身份失败: {type(e).__name__}: {e}")
        await _log(
            redis,
            account_id,
            "info",
            f"已上线: {me.first_name or me.username or me.id}",
        )
        await _publish(redis, account_id, EVT_STATUS, status="active")

        # 后台协程：监听 IPC 指令通道与全局通道
        ipc_task = asyncio.create_task(_listen_cmd(redis, client, account_id, paused))
        global_task = asyncio.create_task(_listen_global(redis, account_id, paused))

        try:
            # 阻塞直到 client.disconnect() 被调用
            await client.run_until_disconnected()
        finally:
            for t in (ipc_task, global_task):
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
    except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedError) as e:
        # session 失效类异常：通知主进程置 status=login_required
        await _publish(redis, account_id, EVT_LOGIN_REQUIRED, reason=type(e).__name__)
        await _log(redis, account_id, "error", f"session 失效: {type(e).__name__}")
    except Exception as e:
        await _log(
            redis, account_id, "error", f"worker 异常退出: {type(e).__name__}: {e}"
        )
    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass
        await _publish(redis, account_id, EVT_STATUS, status="stopped")


async def _listen_cmd(redis, client, account_id: int, paused: asyncio.Event) -> None:
    """监听 ``worker_cmd:{aid}`` 频道，处理 pause/resume/stop/ping/reload_*。"""
    pubsub = redis.pubsub()
    await pubsub.subscribe(cmd_channel(account_id))
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                cmd = IPCMessage.decode(msg["data"])
            except Exception:
                continue
            if cmd.type == CMD_PAUSE:
                paused.clear()
                await _publish(redis, account_id, EVT_STATUS, status="paused")
                await _log(redis, account_id, "info", "已暂停")
            elif cmd.type == CMD_RESUME:
                paused.set()
                await _publish(redis, account_id, EVT_STATUS, status="active")
                await _log(redis, account_id, "info", "已恢复")
            elif cmd.type == CMD_STOP:
                await _log(redis, account_id, "info", "收到 stop 指令")
                await client.disconnect()
                return
            elif cmd.type == CMD_PING:
                await _publish(redis, account_id, EVT_PONG)
            elif cmd.type == CMD_RELOAD_CONFIG:
                # 让 plugin loader 自己处理（如果存在）
                try:
                    from .plugins.loader import reload_account_config  # type: ignore

                    await reload_account_config(account_id, cmd.payload)
                except Exception:
                    pass
                await _log(redis, account_id, "info", "reload_config 完成")
            elif cmd.type == CMD_RELOAD_PLUGIN:
                try:
                    from .plugins.loader import reload_plugin  # type: ignore

                    await reload_plugin(account_id, cmd.payload.get("plugin_key"))
                except Exception as e:
                    await _log(redis, account_id, "error", f"reload_plugin 失败: {e}")
            elif cmd.type == CMD_FETCH_AVATAR:
                # 主进程懒加载头像：worker 端调用 download_profile_photo 写盘
                # path 由主进程指定（绝对路径）；失败静默，前端会走首字母 fallback
                target_path = cmd.payload.get("path")
                if not target_path:
                    continue
                try:
                    import os
                    from pathlib import Path

                    out = Path(str(target_path))
                    out.parent.mkdir(parents=True, exist_ok=True)
                    # download_profile_photo 默认拉大图；账号没头像时返回 None
                    result = await client.download_profile_photo("me", file=str(out))
                    if result is None and out.exists():
                        # Telethon 在没头像时不会写文件，但保险起见若空文件则删
                        try:
                            if os.path.getsize(str(out)) == 0:
                                out.unlink()
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as e:  # noqa: BLE001
                    await _log(redis, account_id, "warn", f"fetch_avatar 失败: {type(e).__name__}: {e}")
            elif cmd.type == CMD_RELOAD_COMMANDS:
                # Sprint2 #2：账号启用/禁用模板、LLM provider 增删后通知 worker 热加载
                try:
                    await _refresh_command_context(account_id)
                except Exception as e:  # noqa: BLE001
                    await _log(
                        redis, account_id, "warn",
                        f"reload_commands 失败: {type(e).__name__}: {e}",
                    )
                else:
                    await _log(redis, account_id, "info", "reload_commands 完成")
            elif cmd.type == CMD_RELOAD_IGNORED:
                # Sprint2 #3：忽略名单变更后，让 plugin loader 从 DB 重拉 set
                try:
                    from .plugins.loader import reload_ignored_peers  # type: ignore

                    await reload_ignored_peers(account_id)
                except Exception as e:  # noqa: BLE001
                    await _log(
                        redis, account_id, "warn", f"reload_ignored 失败: {type(e).__name__}: {e}"
                    )
            elif cmd.type == CMD_GET_RECENT_PEERS:
                # Sprint2 #3 RPC：把内存里的最近活跃 peer 列表回发到 reply_to 频道
                reply_to = cmd.payload.get("reply_to")
                if not isinstance(reply_to, str) or not reply_to:
                    continue
                items: list[dict] = []
                try:
                    from .plugins.loader import get_recent_peers  # type: ignore

                    items = get_recent_peers(account_id)
                except Exception as e:  # noqa: BLE001
                    await _log(
                        redis, account_id, "warn",
                        f"get_recent_peers 失败: {type(e).__name__}: {e}",
                    )
                try:
                    await redis.publish(reply_to, make_cmd(CMD_GET_RECENT_PEERS, items=items))
                except Exception:  # noqa: BLE001
                    # 主进程超时后会自己关订阅；这里 publish 失败无所谓
                    pass
    finally:
        await pubsub.unsubscribe(cmd_channel(account_id))
        await pubsub.close()


async def _listen_global(redis, account_id: int, paused: asyncio.Event) -> None:
    """监听全局广播通道（kill switch / 全局配置 reload）。"""
    pubsub = redis.pubsub()
    await pubsub.subscribe(GLOBAL_CHANNEL)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                cmd = IPCMessage.decode(msg["data"])
            except Exception:
                continue
            if cmd.type == GCMD_KILL_SWITCH:
                if cmd.payload.get("enabled"):
                    paused.clear()
                    await _log(redis, account_id, "warn", "全局 kill switch 已启动")
                else:
                    paused.set()
                    await _log(redis, account_id, "info", "全局 kill switch 已解除")
            elif cmd.type == GCMD_RELOAD_GLOBAL:
                # 命令前缀 / 风控模板等全局设置变更后，主进程广播这条让所有 worker 重拉
                # 当前主要刷的是 system_setting.command_prefix（写到 ctx.command_prefix）
                # 风控相关 reload 由 ratelimit 模块自己监听，不在这里处理
                try:
                    await _refresh_command_context(account_id)
                except Exception as e:  # noqa: BLE001
                    await _log(
                        redis, account_id, "warn",
                        f"reload_global 失败: {type(e).__name__}: {e}",
                    )
                else:
                    await _log(redis, account_id, "info", "reload_global 完成（命令前缀等）")
    finally:
        await pubsub.unsubscribe(GLOBAL_CHANNEL)
        await pubsub.close()


async def _publish(redis, account_id: int, type_: str, **payload):
    """向 worker_event:{aid} 发一条事件。"""
    await redis.publish(event_channel(account_id), make_event(type_, **payload))


def _build_proxy_url(
    ptype: str, host: str, port: int, username: str | None, password: str
) -> str | None:
    """把 Proxy ORM 字段拼成 httpx 接受的 URL。

    支持的类型映射（与 ``app.util.proxy._VALID_TYPES`` 对齐 + httpx 实际支持）：
    - ``socks5``        →  ``socks5://``    需 socksio（``httpx[socks]``）
    - ``http`` / ``https``  →  ``http://``  HTTP CONNECT 代理
    - ``mtproxy`` / 其它   →  None          httpx 不支持，调用方应已经过滤

    用户名密码用 ``urllib.parse.quote`` 转义；空字符串视为不设。
    """
    from urllib.parse import quote

    t = (ptype or "").lower()
    if t == "socks5":
        scheme = "socks5"
    elif t in ("http", "https"):
        scheme = "http"
    else:
        # mtproxy / unknown → 不能给 httpx 用
        return None

    auth = ""
    if username:
        auth = quote(username, safe="")
        if password:
            auth = f"{auth}:{quote(password, safe='')}"
        auth = f"{auth}@"
    return f"{scheme}://{auth}{host}:{int(port)}"


async def _refresh_command_context(account_id: int) -> None:
    """从 DB 拉本账号已启用的命令模板 + 全部 LLM provider，写入 worker-local ctx。

    用作两个时机：
    - worker 启动时一次（确保新连上 TG 就能响应 ``,模板名``）
    - 收到 IPC ``CMD_RELOAD_COMMANDS`` 时热更新

    实现细节：
    - 避免拿原 ORM 实例（脱离 session 后属性访问会报 DetachedInstanceError），转 dict
    - LLM provider 仍持有 ``api_key_enc``（Fernet token）；解密在调用前的 ``build_client`` 里做
    """
    templates: dict[str, dict] = {}
    providers: dict[int, dict] = {}
    # 命令前缀：DB 里 system_setting.command_prefix 优先，没有则用 .env 默认
    prefix: str = app_settings.command_prefix or ","
    async with AsyncSessionLocal() as db:
        # 0) 命令前缀（系统设置）
        try:
            row0 = await db.get(SystemSetting, "command_prefix")
            if row0 is not None and isinstance(row0.value, dict):
                v = str(row0.value.get("value", "") or "").strip()
                if v:
                    prefix = v
            elif row0 is not None and isinstance(row0.value, str):
                v = row0.value.strip()
                if v:
                    prefix = v
        except Exception:  # noqa: BLE001
            # DB 读不到（如迁移没跑）就退回 .env 默认；不影响其它字段加载
            pass

        # 1) 该账号启用中的命令模板
        rows = (
            await db.execute(
                select(CommandTemplate)
                .join(
                    AccountCommandLink,
                    AccountCommandLink.template_id == CommandTemplate.id,
                )
                .where(
                    AccountCommandLink.account_id == account_id,
                    AccountCommandLink.enabled.is_(True),
                )
                .order_by(CommandTemplate.id.asc())
            )
        ).scalars().all()
        for r in rows:
            templates[r.name] = {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "config": dict(r.config or {}),
                "description": r.description,
            }

        # 2) 全部 LLM provider（AI 命令在调用时按 provider_id 索引；不预解密 key）
        #    顺带把 proxy 信息一起拉出来，让 worker 端调 LLM 时也能走代理
        prov_rows = (
            await db.execute(select(LLMProvider))
        ).scalars().all()

        # 收集所有用到的 proxy_id 一次性查出
        proxy_ids = {p.proxy_id for p in prov_rows if p.proxy_id is not None}
        proxy_rows: dict[int, Proxy] = {}
        if proxy_ids:
            rows2 = (
                await db.execute(select(Proxy).where(Proxy.id.in_(proxy_ids)))
            ).scalars().all()
            proxy_rows = {r.id: r for r in rows2}

        for p in prov_rows:
            proxy_url: str | None = None
            if p.proxy_id is not None:
                pr = proxy_rows.get(p.proxy_id)
                if pr is not None and (pr.type or "").lower() != "mtproxy":
                    # 主进程在这里就把 password 解密 + 拼成 httpx 接受的 URL；
                    # 比把 password_enc 下发到 worker 让它再解密少一次往返，明文也只在
                    # ctx 内存里活到 LLM 调用结束（worker 进程私有，不进 Redis / 日志）
                    pwd = ""
                    if pr.password_enc:
                        try:
                            pwd = decrypt_str(pr.password_enc)
                        except Exception:  # noqa: BLE001
                            # 密码解密失败时退化为无认证连接，避免一条坏 proxy 把所有 ai 命令打死
                            pwd = ""
                    proxy_url = _build_proxy_url(
                        pr.type, pr.host, pr.port, pr.username, pwd
                    )
            providers[p.id] = {
                "id": p.id,
                "name": p.name,
                "provider": p.provider,
                "api_key_enc": p.api_key_enc,
                "base_url": p.base_url,
                "default_model": p.default_model,
                # 路由元数据：worker 选 provider 时要看
                "modality": getattr(p, "modality", None) or "text",
                "tags": list(getattr(p, "tags", None) or []),
                "cost_tier": int(getattr(p, "cost_tier", None) or 2),
                "notes": getattr(p, "notes", None),
                # 出口代理 URL；None = 直连（DIRECT）
                "proxy_url": proxy_url,
                # 候选模型清单（worker 通常不直接读，但保持一致）
                "models": list(getattr(p, "models", None) or []),
            }

    set_command_context(
        CommandContext(
            account_id=account_id,
            templates=templates,
            providers=providers,
            command_prefix=prefix,
        )
    )


async def _log(
    redis, account_id: int | None, level: str, message: str, *, source: str = "system", **detail
):
    """写运行日志到 Redis stream，主进程批量消费落库。

    source 语义（前端 Logs 页 tab 区分）：
    - ``"system"``（默认） — worker 启停 / 错误 / IPC / 风控状态变化（runtime.py 几乎全是这种）
    - ``"event"``          — incoming 消息事件、plugin 命中、命令派发（业务/监控向）

    历史数据里也会出现 ``"worker"`` / ``"plugin"`` 两个旧值，API 层做了别名映射，
    前端不必关心。
    """
    payload = RuntimeLogPayload(
        account_id=account_id,
        level=level,
        source=source,
        message=message,
        detail=detail or None,
    )
    await redis.rpush(RUNTIME_LOG_STREAM, payload.encode())


def worker_main(account_id: int) -> None:
    """子进程 entrypoint。

    注意：multiprocessing 在 macOS 默认是 spawn，子进程不继承父进程的 logging handler，
    所以这里要重新初始化 logging 配置。
    """
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [worker:{account_id}] %(levelname)s %(message)s",
    )
    asyncio.run(run_worker(account_id))

===== backend/app/worker/supervisor.py =====
"""主进程内：账号 worker 子进程的拉起 / 监控 / 重启 / 停止。

被 main.py 的 lifespan 调用：
  - startup:  ``await start_supervisor()``
  - shutdown: ``await stop_all_workers()``

接收 ``worker_global`` 上的 ``"start_worker"`` 指令（A Agent 在登录完成时发）。
进程崩溃 → 指数退避重启（5/10/20/60/300s），连续 5 次失败置 ``status='dead'``。

主进程同时在这里**消费** Redis stream（runtime_log / ratelimit_event）落库。
（也可以拆到独立模块；此处合并，便于一个 supervisor 启动协程管全部。）

⚠ 子进程通过 ``_MP_CTX.Process`` 拉起，``_MP_CTX`` 固定为 spawn context（不是
默认的 mp.Process / Linux fork），见模块内 ``_MP_CTX`` 注释解释为什么不能 fork。

⚠ 关停保护：注册 ``atexit`` 与 ``SIGTERM/SIGINT`` 处理，确保即使 lifespan 来不及
跑（被 ``kill -9 uvicorn`` 等暴力中断），也尽量先把所有 worker 子进程 terminate
掉，避免遗留同 session 的孤儿 worker（多实例 worker 会争抢同一个 TG client 事件）。
"""
from __future__ import annotations

import asyncio
import atexit
import json
import logging
import multiprocessing as mp
import signal
import time
from dataclasses import dataclass

from sqlalchemy import select, update

from ..db.base import AsyncSessionLocal
from ..db.models.account import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_DEAD,
    Account,
)
from ..db.models.log import RuntimeLog
from ..db.models.rate_limit import RateLimitEvent
from ..redis_client import get_redis
from .ipc import (
    CMD_PAUSE,
    CMD_RESUME,
    CMD_STOP,
    GLOBAL_CHANNEL,
    RATELIMIT_EVENT_STREAM,
    RUNTIME_LOG_STREAM,
    IPCMessage,
    cmd_channel,
    make_cmd,
)
from .runtime import worker_main

log = logging.getLogger(__name__)

# ⚠ 强制 spawn 启动方式（不要 fork）
#
# Linux 默认是 fork，会把父进程的 asyncio event loop / SQLAlchemy engine /
# Redis 连接池 / 已打开的 socket 一并继承到 worker 子进程，引发：
#   - 子进程复用父 loop / engine 上的 fd，运行时随机炸 IPC
#   - SQLAlchemy 异步引擎在 fork 后状态错乱
#   - Redis 连接被两个进程同时使用，命令乱序
#
# spawn 起的是干净的 Python 解释器，符合本项目「主进程 + worker 完全独立」的契约。
# 我们使用 ``get_context("spawn")`` 拿到一个独立 context 而不是
# ``set_start_method`` 全局改，避免和宿主（uvicorn / pytest 等）冲突。
_MP_CTX = mp.get_context("spawn")

# 指数退避重启间隔（秒），用尽后置账号为 dead
_BACKOFF = [5, 10, 20, 60, 300]


@dataclass
class _WorkerHandle:
    """单个账号 worker 的运行时句柄。"""

    account_id: int
    process: mp.Process | None = None
    fail_count: int = 0
    next_retry_at: float = 0.0
    desired: str = "running"   # running | stopped


# 全局状态：account_id → handle
_WORKERS: dict[int, _WorkerHandle] = {}
# 后台协程列表（global listener / monitor / 两个 stream 消费者）
_BG_TASKS: list[asyncio.Task] = []


async def start_supervisor() -> None:
    """FastAPI lifespan startup 调用。"""
    # 1. 拉起所有 active 账号
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Account).where(Account.status == ACCOUNT_STATUS_ACTIVE)
            )
        ).scalars().all()
    for acc in rows:
        await start_worker(acc.id)

    # 2. 启动后台监听协程
    _BG_TASKS.append(asyncio.create_task(_listen_global()))
    _BG_TASKS.append(asyncio.create_task(_monitor_loop()))
    _BG_TASKS.append(asyncio.create_task(_consume_runtime_log()))
    _BG_TASKS.append(asyncio.create_task(_consume_ratelimit_event()))

    # 3. 注册退出/信号 hook，避免 uvicorn 被暴力杀时遗留孤儿 worker
    _install_kill_hooks()

    log.info("supervisor 启动完成，托管 %d 个账号", len(rows))


def _terminate_all_children_blocking() -> None:
    """同步版本的 worker 进程清理：直接 terminate + join。

    用于 atexit / signal handler 等无法 await 的场景，确保即使主进程异常退出也不留孤儿。
    """
    for h in list(_WORKERS.values()):
        p = h.process
        if p is None:
            continue
        try:
            if p.is_alive():
                p.terminate()
        except Exception:  # noqa: BLE001
            pass
    # 给一点点时间走 SIGTERM 触发的 try/finally；之后 kill
    deadline = time.time() + 2
    for h in list(_WORKERS.values()):
        p = h.process
        if p is None:
            continue
        try:
            timeout = max(0.0, deadline - time.time())
            p.join(timeout=timeout)
            if p.is_alive():
                p.kill()
                p.join(timeout=1)
        except Exception:  # noqa: BLE001
            pass


_HOOKS_INSTALLED = False


def _install_kill_hooks() -> None:
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    atexit.register(_terminate_all_children_blocking)

    def _on_signal(signum, _frame):  # noqa: ANN001
        log.warning("supervisor 收到信号 %s，正在 terminate 所有 worker…", signum)
        _terminate_all_children_blocking()
        # 让默认行为继续退出
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            # 非主线程或受限环境会失败，忽略
            pass
    _HOOKS_INSTALLED = True


async def stop_all_workers() -> None:
    """FastAPI lifespan shutdown 调用：停止所有 worker 与后台协程。"""
    for aid in list(_WORKERS.keys()):
        await stop_worker(aid)
    for t in _BG_TASKS:
        t.cancel()
    _BG_TASKS.clear()


async def start_worker(account_id: int) -> None:
    """拉起或恢复指定账号的 worker 子进程；幂等。"""
    h = _WORKERS.get(account_id)
    if not h:
        h = _WorkerHandle(account_id=account_id)
        _WORKERS[account_id] = h
    h.desired = "running"
    if h.process and h.process.is_alive():
        return
    p = _MP_CTX.Process(target=worker_main, args=(account_id,), daemon=False)
    p.start()
    h.process = p
    log.info("worker 启动: account=%d pid=%s", account_id, p.pid)


async def stop_worker(account_id: int) -> None:
    """停止指定账号的 worker：先发 IPC stop，等 5s 优雅退出，否则 terminate。"""
    h = _WORKERS.get(account_id)
    if not h:
        return
    h.desired = "stopped"
    redis = get_redis()
    await redis.publish(cmd_channel(account_id), make_cmd(CMD_STOP))
    if h.process:
        # 等 5s 优雅退出（每 100ms 探测一次）
        for _ in range(50):
            if not h.process.is_alive():
                break
            await asyncio.sleep(0.1)
        if h.process.is_alive():
            h.process.terminate()
            h.process.join(timeout=2)
        h.process = None
    log.info("worker 停止: account=%d", account_id)


async def pause_worker(account_id: int) -> None:
    """通过 IPC 让 worker 暂停主动动作。"""
    redis = get_redis()
    await redis.publish(cmd_channel(account_id), make_cmd(CMD_PAUSE))


async def resume_worker(account_id: int) -> None:
    """通过 IPC 让 worker 恢复主动动作。"""
    redis = get_redis()
    await redis.publish(cmd_channel(account_id), make_cmd(CMD_RESUME))


async def _listen_global() -> None:
    """监听 ``worker_global``：A Agent 登录完成会广播 ``start_worker`` 指令。"""
    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(GLOBAL_CHANNEL)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                cmd = IPCMessage.decode(msg["data"])
            except Exception:
                continue
            if cmd.type == "start_worker":
                aid = cmd.payload.get("account_id")
                if aid:
                    await start_worker(int(aid))
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await pubsub.unsubscribe(GLOBAL_CHANNEL)
            await pubsub.close()
        except Exception:
            pass


async def _monitor_loop() -> None:
    """每 2s 检查每个 worker 是否活着；崩溃则按指数退避重启。"""
    try:
        while True:
            await asyncio.sleep(2)
            now = time.time()
            for aid, h in list(_WORKERS.items()):
                if h.desired != "running":
                    continue
                if h.process and h.process.is_alive():
                    # 进程健康：清零失败计数
                    h.fail_count = 0
                    continue
                # 进程死了
                if now < h.next_retry_at:
                    continue
                h.fail_count += 1
                if h.fail_count > len(_BACKOFF):
                    log.error(
                        "worker %d 连续失败 %d 次，置 dead", aid, h.fail_count
                    )
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(Account)
                            .where(Account.id == aid)
                            .values(status=ACCOUNT_STATUS_DEAD)
                        )
                        await db.commit()
                    h.desired = "stopped"
                    continue
                wait = _BACKOFF[min(h.fail_count - 1, len(_BACKOFF) - 1)]
                h.next_retry_at = now + wait
                log.warning(
                    "worker %d 崩溃，%ds 后第 %d 次重启",
                    aid,
                    wait,
                    h.fail_count,
                )
                await start_worker(aid)
    except asyncio.CancelledError:
        pass


async def _consume_runtime_log() -> None:
    """从 Redis stream 批量消费运行日志，落库 runtime_log。

    实现策略：BLPOP 阻塞拿到第一条触发，然后 LRANGE+LTRIM 一次性吞掉一批，降低 DB 压力。
    """
    redis = get_redis()
    BATCH = 50
    try:
        while True:
            try:
                first = await redis.blpop(RUNTIME_LOG_STREAM, timeout=5)
                if not first:
                    continue
                items = [first[1]]
                more = await redis.lrange(RUNTIME_LOG_STREAM, 0, BATCH - 1)
                if more:
                    items.extend(more)
                    await redis.ltrim(RUNTIME_LOG_STREAM, len(more), -1)
                rows = []
                for raw in items:
                    try:
                        d = json.loads(raw)
                        rows.append(
                            RuntimeLog(
                                account_id=d.get("account_id"),
                                level=d.get("level", "info"),
                                source=d.get("source"),
                                message=d.get("message", ""),
                                detail=d.get("detail"),
                            )
                        )
                    except Exception:
                        continue
                if rows:
                    async with AsyncSessionLocal() as db:
                        db.add_all(rows)
                        await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("runtime_log 消费失败: %s", e)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass


async def _consume_ratelimit_event() -> None:
    """从 Redis stream 批量消费风控事件，落库 rate_limit_event。"""
    redis = get_redis()
    BATCH = 50
    try:
        while True:
            try:
                first = await redis.blpop(RATELIMIT_EVENT_STREAM, timeout=5)
                if not first:
                    continue
                items = [first[1]]
                more = await redis.lrange(RATELIMIT_EVENT_STREAM, 0, BATCH - 1)
                if more:
                    items.extend(more)
                    await redis.ltrim(RATELIMIT_EVENT_STREAM, len(more), -1)
                rows = []
                for raw in items:
                    try:
                        d = json.loads(raw)
                        rows.append(
                            RateLimitEvent(
                                account_id=d["account_id"],
                                action=d["action"],
                                outcome=d["outcome"],
                                detail=d.get("detail"),
                            )
                        )
                    except Exception:
                        continue
                if rows:
                    async with AsyncSessionLocal() as db:
                        db.add_all(rows)
                        await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("ratelimit_event 消费失败: %s", e)
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass

===== backend/app/worker/tg_client.py =====
"""Telethon 客户端工厂。

负责从 Account/Proxy 数据库记录恢复 Telethon 客户端实例：
- session 串经 master_key 解密后塞入 ``StringSession``
- api_id/api_hash 同样从加密字段中解出
- 如果挂了出口代理，构造 Telethon 接受的 proxy 元组
- 账号未绑定 Proxy 行时，自动 fallback 到 ``settings.tg_default_proxy``（全局兜底，
  本机测试场景常用，比如开发机本地启动了一个 SOCKS5 代理走出去 TG）
- 设备伪装信息（device_model / system_version / app_version / lang_code / system_lang_code）
  来自 ``ResolvedDeviceProfile``，由调用方先用 ``services.device_profile.resolve_for_account``
  解析好传进来；不传则使用硬编码兜底
"""
from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from ..crypto import decrypt_bytes, decrypt_str
from ..db.models.account import Account, Proxy
from ..services.device_profile import HARDCODED_FALLBACK, ResolvedDeviceProfile
from ..util.proxy import get_default_proxy_tuple


def build_proxy_tuple(proxy: Proxy | None):
    """构造 Telethon 接受的代理元组。

    Telethon 的 PySocks 兼容 tuple 形式：
        (proxy_type, addr, port, rdns, username, password)
    传入 ``None`` 时回落 ``settings.tg_default_proxy``；仍然没有则真正直连。
    """
    if not proxy:
        return get_default_proxy_tuple()
    return (
        proxy.type,                  # "socks5" | "http" | "mtproxy"
        proxy.host,
        proxy.port,
        True,                        # 强制走远端 DNS，避免本地 DNS 泄漏
        proxy.username,
        decrypt_str(proxy.password_enc) if proxy.password_enc else None,
    )


def build_client(
    account: Account,
    proxy: Proxy | None = None,
    profile: ResolvedDeviceProfile | None = None,
) -> TelegramClient:
    """根据账号记录构造一个未连接的 Telethon 客户端。

    profile=None 时使用兜底 (HARDCODED_FALLBACK)；正常路径里 worker 启动时调用
    ``services.device_profile.resolve_for_account`` 拿到 profile 再传入。
    """
    session_str = decrypt_bytes(account.session_enc).decode()
    api_id = int(decrypt_str(account.api_id_enc))
    api_hash = decrypt_str(account.api_hash_enc)
    p = profile or HARDCODED_FALLBACK
    return TelegramClient(
        StringSession(session_str),
        api_id,
        api_hash,
        proxy=build_proxy_tuple(proxy),
        request_retries=3,
        connection_retries=5,
        retry_delay=2,
        **p.telethon_kwargs(),
        # 关键：Telethon 默认 sequential_updates=False。我们的事件 handler 写在 plugin 里，
        # 互相不应该并发触发同一规则，但跨规则可以；保持默认即可。
    )

```
