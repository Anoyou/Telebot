# builtin 插件目录说明

## codex_image 下沉兼容（0.14 PLAN F9/B2）

- `codex_image` 已从 builtin 目录物理下沉到 `plugins/installed/codex_image/`，不再由 builtin registry 自动 seed。
- 旧账号若仍保留 `account_feature(feature_key='codex_image')`，worker 会在本地 installed 代码存在时按兼容模式加载；若代码缺失，会写入 runtime log 并将该功能标记为 failed，不会导致 worker 进程崩溃。
- dry-run 入口也已改为 installed 路径，避免 builtin 目录再次成为隐性依赖。
- 后续若继续把 `codex_image` 发布为远程插件，请保持 `plugin.json.version` 与 `manifest.py` 里的 `MANIFEST.version` 同步，并确认 `send_file` 权限已声明。
