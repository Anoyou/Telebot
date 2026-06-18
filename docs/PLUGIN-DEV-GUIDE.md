# TelePilot 插件开发指南（索引）

> 这是一页索引，不再承载完整正文。原来的开发指南已按主题拆分，代码层 API 仍叫 `Plugin` / `PluginContext`，产品文案统一称“插件”。

> 路线决策保留在这里：TelePilot 0.x 默认采用 **Route A：受信/签名插件市场**。仅接收 TelePilot/Anoyou 审核过的插件源，安装包需要签名或可信来源记录，插件在同一 worker 进程内运行，通过 `Manifest.permissions`、`ctx.client`、`ctx.http`、`ctx.ai` 等 facade 收口能力。它适合 0.x 快速稳定迭代，重点放在插件 API、安装体验、权限声明、审计日志和回滚能力上。

> **Route B：开放社区市场** 面向任意第三方上传或未经人工审核的插件，需要 subprocess/容器隔离、资源配额、文件系统/网络沙箱、供应链扫描和更完整的安全策略。它不属于 0.x 默认方案，若 1.0 之后开放社区市场，应作为独立 Epic 设计和验收。本文其余章节、示例、CI 和安全边界都按 Route A 编写。

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
2. 再看 [API 参考](./PLUGIN-API-REFERENCE.md) 找 `Plugin`、`PluginContext`、`Manifest`、指令、消息、Conversation、前端集成和完整示例。
3. 需要外部网络能力时看 [HTTP facade](./PLUGIN-HTTP.md)，需要 AI 能力时看 [AI facade](./PLUGIN-AI.md)。
4. 需要权限、前缀、消息发送、并发和清理约束时看 [安全边界](./PLUGIN-SAFETY.md)。
5. 需要 Git 安装、`plugin.json`、Registry、发布检查时看 [远程插件](./PLUGIN-REMOTE.md)。
6. 需要快速回忆字段名和常用模式时看 [速查表](./PLUGIN-CHEATSHEET.md)。

## 兼容说明

- 旧章节锚点已经不再提供。
- `docs/REMOTE-PLUGIN-GUIDE.md` 仍保留为兼容入口，但正文已指向新的远程插件文档。
- `docs/PLUGIN-AI.md` 保持独立。
