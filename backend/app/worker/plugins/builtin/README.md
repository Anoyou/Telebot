# builtin 插件目录说明

## codex_image 内置实验模块（0.18.1）

- `codex_image` 已恢复为真正的 builtin 模块，目录为 `backend/app/worker/plugins/builtin/codex_image/`。
- 生产 Docker 镜像只构建 `backend/` 上下文，因此必须把它放在 builtin 目录内，才能随镜像发布并由 builtin registry 自动 seed。
- 模块仍标记为 experimental；旧账号保留的 `account_feature(feature_key='codex_image')` 会按普通内置模块路径加载，不需要数据迁移。
- `plugins/installed/codex_image` 只保留给历史部署的运行时残留，不再作为源码里的 TelePilot 内置入口。
