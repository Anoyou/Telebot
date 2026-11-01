"""插件市场 API 的请求 / 响应模型（0.113.0；0.117.0 追加详情字段与稳定 / 测试通道）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PluginMarketName(BaseModel):
    zh: str
    en: str


class PluginMarketSettings(BaseModel):
    update_concurrency: int = Field(description="批量更新并发上限（1–8，默认 3）")
    health_window_minutes: int = Field(description="更新后的健康观察窗口（分钟，3–60，默认 10）")
    pinned: dict[str, str] = Field(default_factory=dict, description="固定版本的插件：key → 固定时的版本")


class PluginMarketSettingsPatch(BaseModel):
    update_concurrency: int | None = Field(default=None, ge=1, le=8)
    health_window_minutes: int | None = Field(default=None, ge=3, le=60)


class PluginMarketSource(BaseModel):
    id: str
    name: str
    url: str
    trust: str = Field(description="official / community / unreviewed")
    enabled: bool
    auto_update: bool = Field(description="是否自动更新（只有 official 来源可以开启）")
    builtin: bool = Field(description="内置官方源：不能删除，只能停用")
    available: bool = Field(default=True, description="内置官方源被 TELEPILOT_PLUGIN_MARKET=off 关闭时为 false")
    status: str = Field(default="disabled", description="ok / stale / fallback / error / disabled")
    plugin_count: int = 0
    fetched_at: str | None = None
    error: str | None = None
    fallback: bool = Field(default=False, description="官方源拉取失败，正在使用随服务器打包的索引副本")


class PluginMarketSourceCreate(BaseModel):
    url: str = Field(description="registry.json 的 https 地址")
    name: str | None = None
    trust: str | None = Field(default="unreviewed", description="official / community / unreviewed")


class PluginMarketSourcePatch(BaseModel):
    name: str | None = None
    trust: str | None = None
    enabled: bool | None = None
    auto_update: bool | None = None


class PluginMarketInstalled(BaseModel):
    version: str
    enabled: bool
    source: str | None = None
    source_url: str | None = None
    same_source: bool = Field(description="已安装版本是否来自该条目的同一仓库（只有同源才提示更新）")


class PluginMarketRollout(BaseModel):
    key: str | None = None
    status: str | None = Field(default=None, description="watching / healthy / rolled_back / rollback_failed")
    trigger: str | None = Field(default=None, description="manual / auto / repo")
    from_version: str | None = None
    to_version: str | None = None
    started_at: str | None = None
    deadline: str | None = None
    finished_at: str | None = None
    reason: str | None = None


class PluginMarketChannel(BaseModel):
    name: str = Field(description="通道名：stable（稳定）/ beta（测试）/ 其它索引自定义的名字")
    ref: str | None = Field(default=None, description="通道对应的 Git 分支")
    version: str | None = Field(default=None, description="该通道当前版本；索引没写时为空（未知）")


class PluginMarketItem(BaseModel):
    id: str = Field(description="来源 id:插件 key")
    key: str
    name: PluginMarketName
    kind: str
    icon: str | None = None
    summary: str = ""
    version: str
    author: str = ""
    min_telepilot_version: str | None = None
    plugin_api: str | None = None
    permissions: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    channels: dict[str, str] = Field(default_factory=dict)
    path: str
    repository: str
    trust: str
    source_id: str
    source_name: str
    compatible: bool
    incompatible_reason: str | None = None
    installed: PluginMarketInstalled | None = None
    update_available: bool = False
    pinned_version: str | None = None
    auto_update: bool = False
    rollout: PluginMarketRollout | None = None
    # ── 0.117.0：应用商店式详情（registry 目录版本 1.1 追加字段，旧索引没有时为空）──
    summary_i18n: dict[str, str] = Field(default_factory=dict, description="多语言简介：zh / en …")
    description: str | None = Field(default=None, description="完整简介")
    usage: str | None = Field(default=None, description="使用说明（可能含 {prefix} 指令前缀占位）")
    commands: list[str] = Field(default_factory=list, description="指令名（不含前缀）")
    release_notes: str | None = Field(default=None, description="最新版本说明（Markdown 列表）")
    released_at: str | None = Field(default=None, description="最新版本发布日期 YYYY-MM-DD")
    homepage: str | None = Field(default=None, description="插件主页（https）")
    license: str | None = None
    icon_url: str | None = Field(default=None, description="图片图标（https）；没有时用 icon emoji")
    screenshots: list[str] = Field(default_factory=list, description="截图（https 链接）")
    channel_versions: dict[str, str] = Field(default_factory=dict, description="各通道当前版本")
    # ── 0.117.0：稳定 / 测试通道 ──
    channel: str | None = Field(default=None, description="已安装插件所在的通道；未安装或来自别的仓库时为空")
    available_channels: list[PluginMarketChannel] = Field(
        default_factory=list, description="可选通道（稳定通道总在第一个）；字段缺失说明服务器早于 0.117.0"
    )
    update_version: str | None = Field(default=None, description="有更新时要更新到的版本（按所在通道）")


class PluginMarketCatalog(BaseModel):
    server_version: str
    plugin_api: str
    registry_refresh_hours: int = Field(default=6, description="服务器后台拉取各来源索引的间隔（小时）")
    sources: list[PluginMarketSource]
    items: list[PluginMarketItem]
    updates_available: int = Field(description="有新版本且未固定的插件数（更新角标）")
    settings: PluginMarketSettings
    rollouts: list[PluginMarketRollout] = Field(default_factory=list)


class PluginMarketInstallBody(BaseModel):
    source_id: str
    key: str = Field(description="插件 key；也可写安装串 registry:<key>[@version|#channel]")
    default_enabled: bool = False
    channel: str | None = Field(default=None, description="安装哪个通道（默认 stable）")
    version: str | None = Field(default=None, description="期望的版本；必须等于该通道当前版本（市场不保存历史版本）")


class PluginMarketChannelBody(BaseModel):
    key: str
    channel: str = Field(description="要切换到的通道：stable / beta …")
    source_id: str | None = Field(default=None, description="同一插件出现在多个来源时指定来源")


class PluginMarketUpdateBody(BaseModel):
    keys: list[str] | None = Field(default=None, description="要更新的插件；不传表示全部有更新且未固定的插件")


class PluginMarketUpdateItem(BaseModel):
    key: str
    display_name: str = ""
    from_version: str | None = None
    to_version: str | None = None
    status: str = Field(description="updated / skipped / pinned / failed")
    message: str = ""
    health_check: bool = Field(default=False, description="是否进入了健康观察窗口（失败会自动回滚）")


class PluginMarketUpdateResult(BaseModel):
    concurrency: int
    total: int
    updated: int
    skipped: int
    failed: int
    items: list[PluginMarketUpdateItem]


class PluginMarketPinBody(BaseModel):
    pinned: bool


class PluginMarketPinResult(BaseModel):
    key: str
    pinned_version: str | None = None
