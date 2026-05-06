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
# 用于"用户不存在"分支的哨兵哈希：
# 登录时即使查不到用户，也执行一次等价 Argon2 verify，降低用户名枚举的时序侧信道。
_SENTINEL_PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$A1x4bVcxU1JQb3QxS3N3cg$5nM6vEJaM7uI0H95v3zUPSNIP3I5iecbMYUfM5nXx1E"


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


def verify_password_with_sentinel(plain: str, hashed: str | None) -> bool:
    """带哨兵哈希的密码校验。

    当 ``hashed`` 为 None（例如用户名不存在）时，仍执行一次 Argon2 verify，
    以缩小"用户存在/不存在"的时序差异；返回值始终遵循原语义（不存在用户必为 False）。
    """
    if hashed:
        return verify_password(plain, hashed)
    # 不存在用户时也做一次等价计算，但不泄露任何成功路径
    verify_password(plain, _SENTINEL_PASSWORD_HASH)
    return False


# ── JWT ───────────────────────────────────────────────────────────
def issue_jwt_token(user_id: int, pwd_version: int = 0) -> str:
    """颁发短期 JWT（HS256）。payload = ``{sub, pwd_v, exp, iat}``。"""
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "pwd_v": int(pwd_version),
        "iat": now,
        "exp": now + settings.jwt_expire_seconds,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_jwt_claims(token: str) -> dict | None:
    """解码 JWT 原始 payload；失败 / 过期 / 格式非法返回 None。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if not payload or not payload.get("sub"):
        return None
    return payload


def decode_jwt_token(token: str) -> int | None:
    """解码 JWT，成功返回 user_id；失败 / 过期 / 签名不对一律返回 None。"""
    payload = decode_jwt_claims(token)
    if not payload:
        return None
    sub = payload["sub"]
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
