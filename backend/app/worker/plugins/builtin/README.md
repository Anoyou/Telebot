# builtin 兼容目录说明

`backend/app/worker/plugins/builtin/` 只代表 TelePilot 核心平台能力和历史兼容代码，不再等同于“所有随包插件”。

从 0.35 起：

- `scheduler` 是平台调度能力，运行时由 `PlatformScheduler` 承接，builtin 目录只保留兼容壳。
- `forward` 保留为核心兼容插件。
- `auto_reply`、`autorepeat`、`chatgpt_image`、`codex_image`、`game24`、`math10` 走 `backend/app/worker/plugins/official/` 官方可选插件库。Web 安装后会复制到 `plugins/installed/{key}/`，再按安装型插件加载。
- 这个目录里仍可能留有上述插件的历史源码，供旧测试、迁移或兼容导入使用；`feature_registry` 和 worker loader 会跳过它们，不会再作为 builtin registry 自动 seed。
