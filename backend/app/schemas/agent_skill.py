"""Agent Skills 管理 API 的请求/响应 schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgentSkillSkippedFile(BaseModel):
    path: str
    reason: str = Field(description="跳过原因：binary / too_large / symlink / hidden / ignored_dir / ignored_file / outside")


class AgentSkillOut(BaseModel):
    name: str
    description: str
    builtin: bool = Field(description="内置领域技能：不可卸载，只能对系统 Agent 停用")
    version: str | None = None
    license: str | None = None
    bundled: bool = Field(default=False, description="随插件分发的内置 skill：不可卸载，可停用；编辑保存为覆盖层")
    owner_plugin: str | None = Field(default=None, description="内置 skill 所属插件")
    editable: bool = Field(default=False, description="可以直接编辑 SKILL.md")
    uninstallable: bool = Field(default=True, description="可以卸载（内置技能与插件内置 skill 为 false）")
    user_modified: bool = Field(default=False, description="插件内置 skill 已被用户编辑")
    upstream_version: str | None = Field(default=None, description="插件分发的默认版本号")
    upstream_changed: bool = Field(default=False, description="内置有新版本：插件升级带来了新的默认内容，而你保留着自己的修改")
    source_type: Literal["builtin", "github", "zip_url", "zip_upload", "paste", "md_upload", "plugin_bundled"]
    source_url: str | None = None
    source_ref: str | None = Field(default=None, description="GitHub 分支/标签/提交")
    source_path: str | None = Field(default=None, description="仓库或 zip 内的 skill 子目录")
    source_commit: str | None = Field(default=None, description="安装时的提交 SHA（GitHub 来源）")
    content_sha256: str | None = None
    enabled_agent: bool
    enabled_plugins: bool
    plugin_supported: bool = Field(description="是否可以提供给插件（内置技能为 false）")
    updatable: bool = Field(description="是否可从原地址重新拉取更新")
    file_count: int = 0
    total_bytes: int = 0
    skipped_files: list[AgentSkillSkippedFile] = Field(default_factory=list)
    installed_at: str | None = None
    updated_at: str | None = None


class AgentSkillLimits(BaseModel):
    max_files: int
    max_file_bytes: int
    max_skill_bytes: int
    max_archive_bytes: int
    agent_load_max_chars: int
    plugin_load_max_chars: int


class AgentSkillListOut(BaseModel):
    items: list[AgentSkillOut]
    limits: AgentSkillLimits


class AgentSkillFileInfo(BaseModel):
    path: str
    size: int


class AgentSkillDetailOut(AgentSkillOut):
    skill_md: str = Field(default="", description="SKILL.md 原文（内置技能为渲染后的流程说明）")
    files: list[AgentSkillFileInfo] = Field(default_factory=list)


class AgentSkillFileOut(BaseModel):
    name: str
    path: str
    content: str
    truncated: bool = False


class AgentSkillInstallIn(BaseModel):
    source: Literal["github", "zip_url", "zip_upload", "paste", "md_upload"]
    url: str | None = Field(default=None, max_length=2048, description="GitHub 链接或 zip 下载链接")
    ref: str | None = Field(default=None, max_length=255, description="可选：分支/标签/提交，覆盖链接里的分支")
    path: str | None = Field(default=None, max_length=512, description="可选：仓库或 zip 内的 skill 子目录")
    zip_base64: str | None = Field(default=None, description="zip_upload：zip 文件的 base64（≤10MB）")
    skill_md: str | None = Field(default=None, max_length=262144, description="paste / md_upload：SKILL.md 全文")
    enabled_agent: bool = True
    enabled_plugins: bool = False


class AgentSkillUpdateIn(BaseModel):
    zip_base64: str | None = None
    skill_md: str | None = Field(default=None, max_length=262144)


class AgentSkillUpdateOut(BaseModel):
    skill: AgentSkillOut
    changed: bool


class AgentSkillEnablePatch(BaseModel):
    enabled_agent: bool | None = None
    enabled_plugins: bool | None = None


class AgentSkillUnbound(BaseModel):
    plugin_key: str
    scope: Literal["global", "account"]
    account_id: int | None = None
    fields: list[str] = Field(default_factory=list)


class AgentSkillUninstallOut(BaseModel):
    name: str
    uninstalled: bool
    unbound: list[AgentSkillUnbound] = Field(default_factory=list)


class AgentSkillContentIn(BaseModel):
    skill_md: str = Field(max_length=262144, description="新的 SKILL.md 全文；name 必须保持不变")


class AgentSkillUpstreamOut(BaseModel):
    name: str
    owner_plugin: str | None = None
    user_modified: bool
    upstream_changed: bool
    upstream_version: str | None = None
    current_skill_md: str
    upstream_skill_md: str
    diff: str = Field(description="unified diff：默认版本 → 当前版本")
