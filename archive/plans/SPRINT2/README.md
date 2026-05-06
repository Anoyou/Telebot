# Sprint 2 — 已交付（v0.2.0，2026-05-03）

> 本目录归档自 `agent-plans/`。本 sprint 是 5 个 sub-session 并行 / 接力完成的。
> 详细变更见 [CHANGELOG.md](../../../CHANGELOG.md) `[0.2.0]` 段。

## 交付清单

| Plan 文件 | 模块 | 主要交付 |
|----------|------|---------|
| `SPRINT2-UX-OPS.md` | UX 清理 + Humanize | 头像懒加载 / Humanize 折叠面板 / SECURITY-OPS.md / device_profile（用户后期追加） |
| `SPRINT2-CUSTOM-COMMAND.md` | 自定义命令 | 4 种类型（reply_text/forward_to/ai/run_plugin）+ LLMProvider 抽象 + AI key Fernet 加密 |
| `SPRINT2-IGNORED-PEERS.md` | 忽略群组 | ignored_peer 表 + worker recent_peers LRU + 一键加入按钮 |
| `SPRINT2-PLUGIN-MODULARIZE.md` | 插件模块化 tier C | Manifest + zip 上传 + 仓库订阅 + SandboxClient 沙箱 |
| `SPRINT2-FORWARD.md` | 转发插件 | 4 种 mode（forward_native/copy_text/quote/link_only）× 3 种 source |

## 数据库迁移

- `0002_add_tg_identity` — tg_user_id / tg_username
- `0003_command_template` — command_template / account_command_link / llm_provider
- `0003b_add_device_profile` — device_profile（追加，与 0003 同号被线性化）
- `0004_ignored_peer` — ignored_peer
- `0005_plugin_install` — plugin_install

## 后续修复（v0.2.1 hotfix，2026-05-03）

详见 [CHANGELOG.md](../../../CHANGELOG.md) `[0.2.1]` 段。
- alembic 0003 分叉线性化
- 自动回复 hotfix（PG 升 0006 后才补）
- 日志中心拆「消息日志 / 系统日志」
- 风控动作中文标签全对齐
- 修改密码 + TOTP 禁用 API
