"""插件仓库 Pydantic schemas。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PluginRepoCreate(BaseModel):
    """新增仓库请求体。

    ``url`` 必填；``name``/``description`` 可选，缺省时服务端会从 URL 派生显示名。
    """

    url: str = Field(..., description="git URL，如 https://github.com/foo/bar.git")
    name: str | None = Field(default=None, description="展示名；为空时从 URL 派生")
    description: str | None = Field(default=None, description="可选备注")


class PluginRepoOut(BaseModel):
    """仓库 DB 行的对外形态。"""

    id: int
    name: str
    url: str
    description: str
    added_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class PluginRepoPlugin(BaseModel):
    """仓库内单个插件的描述（仅来自 plugin.json 静态解析，无执行）。

    ``installed`` 由服务层根据 ``remote_plugin.name`` 是否已存在来填，便于
    前端给出“已安装/可安装”按钮状态。
    """

    name: str
    display_name: str = ""
    description: str = ""
    author: str = ""
    version: str = "0.0.0"
    installed: bool = False
    installed_version: str | None = None
    update_available: bool = False
    # 该插件在仓库内的相对子目录（用于安装时定位）；若插件位于仓库根目录则为 ""
    subdir: str = ""
