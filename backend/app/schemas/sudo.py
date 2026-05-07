"""Sudo 用户管理 Schemas。"""

from __future__ import annotations

from pydantic import BaseModel


class SudoUserCreate(BaseModel):
    """创建 Sudo 用户请求。"""
    account_id: int
    tg_user_id: int
    display_name: str | None = None
    allowed_chat_ids: list[int] | None = None
    allowed_commands: list[str] | None = None


class SudoUserUpdate(BaseModel):
    """更新 Sudo 用户请求。"""
    display_name: str | None = None
    allowed_chat_ids: list[int] | None = None
    allowed_commands: list[str] | None = None


class SudoUserResponse(BaseModel):
    """Sudo 用户响应。"""
    id: int
    account_id: int
    tg_user_id: int
    display_name: str | None = None
    allowed_chat_ids: list[int] | None = None
    allowed_commands: list[str] | None = None
    created_at: str

    class Config:
        from_attributes = True
