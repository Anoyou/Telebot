# builtin 目录说明

`backend/app/worker/plugins/builtin/` 只代表 TelePilot 核心平台能力和仍需随 Core 发布的轻量兼容代码，不再等同于“所有随包插件”。

当前口径：

- `scheduler` 是平台调度能力，运行时由 `PlatformScheduler` 承接，builtin 目录只保留兼容壳。
- `forward` 保留为核心兼容插件。
  - 规则可开启 `only_unread`（默认关闭），按当前账号的 Telegram 已读位置过滤消息；`unread_delay_seconds` 默认 30 秒，范围 0–600，仅开启过滤时生效。
  - 延迟由平台调度器执行，不阻塞消息派发；到期、风控等待后和重试时重新查询，已读或查询失败均跳过并记录原因。通知预览不产生已读回执时仍算未读。查询和发送之间无法保证原子性。
  - 延迟任务只保存在运行时；插件停用、重载或 worker 重启会清除待转发任务，不补发。旧规则维持立即转发行为。
- `auto_reply`、`autorepeat`、`chatgpt_image`、`codex_image`、`game24`、`math10` 都不随 TelePilot 本体分发；需要时由使用者自行接入包含它们的 Git 仓库并安装。
- `feature_registry` 和 worker loader 仍保留非核心可选插件的跳过名单，用来防止旧镜像或增量部署残留目录被重新注册为 builtin；这不是这些插件仍属于 Core 的信号。
