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
