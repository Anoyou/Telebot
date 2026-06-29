# TelePilot 插件开发指南（索引）

> 这是一页索引，不再承载完整正文。原来的开发指南已按主题拆分，代码层 API 仍叫 `Plugin` / `PluginContext`，产品文案统一称“插件”。

> 路线决策保留在这里：TelePilot 0.x 默认采用 **个人可信插件标准模式**。管理员安装并启用插件后，即视为信任该插件的业务逻辑；远程插件风险由管理员自行承担。平台不做公共插件市场式强沙箱，而是通过 `Manifest.permissions`、`ctx.client`、`ctx.http`、`ctx.ai`、`ctx.messages` 等 facade 收口常用能力，并保留频控、审计、急停、日志脱敏和 token/session 隔离。

> 如果未来要开放“任意第三方上传、未经人工审核”的公共市场，需要另行设计 subprocess/容器隔离、资源配额、文件系统/网络沙箱和供应链扫描。它不属于当前 0.x 默认方案，本文其余章节、示例、CI 和安全边界都按个人可信插件标准模式编写。

## 目录

- [概览](./PLUGIN-OVERVIEW.md)
- [API 参考](./PLUGIN-API-REFERENCE.md)
- [HTTP facade](./PLUGIN-HTTP.md)
- [安全边界](./PLUGIN-SAFETY.md)
- [远程插件](./PLUGIN-REMOTE.md)
- [速查表](./PLUGIN-CHEATSHEET.md)
- [AI facade](./PLUGIN-AI.md)

## 读法

1. 先看 [概览](./PLUGIN-OVERVIEW.md) 理清插件、远程插件和运行时边界。
2. 新 Telegram 交互插件先看 [概览](./PLUGIN-OVERVIEW.md) 与 [速查表](./PLUGIN-CHEATSHEET.md) 的 Event Bus + MessageOps 最小模板，再看 [API 参考](./PLUGIN-API-REFERENCE.md) 查 `Plugin`、`PluginContext`、`Manifest`、标准事件信封、MessageOps、Trace 排障；指令、消息、Conversation 和旧 hook 示例主要用于管理员命令兼容或迁移。
3. 需要外部网络能力时看 [HTTP facade](./PLUGIN-HTTP.md)，需要 AI 能力时看 [AI facade](./PLUGIN-AI.md)。
4. 需要权限、前缀、消息发送、并发和清理约束时看 [安全边界](./PLUGIN-SAFETY.md)。
5. 需要 Git 安装、`plugin.json`、Registry、发布检查时看 [远程插件](./PLUGIN-REMOTE.md)。
6. 需要快速回忆字段名和常用模式时看 [速查表](./PLUGIN-CHEATSHEET.md)。

## 兼容说明

- 旧章节锚点已经不再提供。
- `docs/REMOTE-PLUGIN-GUIDE.md` 仍保留为兼容入口，但正文已指向新的远程插件文档。
- `docs/PLUGIN-AI.md` 保持独立。
