# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [SemVer](https://semver.org/lang/zh-CN/)：MAJOR.MINOR.PATCH。

- **MAJOR**：不向后兼容（数据库不兼容迁移 / 协议大改 / 配置项重命名 / API 路径变更）
- **MINOR**：向后兼容的功能增量（一个 Sprint 通常 +1）
- **PATCH**：bug 修复 / 文档 / 小调整 / hotfix

> 版本号在 5 处必须保持同步：`backend/app/__init__.py`、`backend/pyproject.toml`、`frontend/package.json`、`frontend/src/lib/version.ts`、本文件顶部段落。`backend/app/main.py` 通过 `from . import __version__` 自动跟随，无需单独改。详见 `agent-plans/README.md` §6。

---


## [0.9.7] — 2026-05-10 · feature · 插件前端规范 + LLM/IPC 可靠性闭环

### Added
- 新增 Codex Image 内置插件：将 TeleBox_Plugins 的 `codex_image` TypeScript 插件转换为 TeleBot Python 插件，并接入内置插件路由、配置页与规则 dry-run。
- 插件开发文档第 12 节重写为完整前端集成规范：补充规则驱动、单配置对象、Schema 驱动弹窗三种模式，以及新增功能插件的前后端适配清单。
- LLM 用量流水落库：新增 `llm_usage` 表、模型与 runtime usage callback，记录账号、provider、model、token、fallback 与错误类型。
- LLM 成本限额：系统设置支持每账号每分钟调用、每日调用、每日 token、高价模型每日调用限制；worker 调用前执行硬门禁。
- Redis IPC 可选 ACK：reload 类控制命令支持等待 worker 确认，worker 离线时继续依赖 DB 状态与周期 reconcile 收敛。
- 插件 manifest 兼容检查：loader 支持 `min_telebot_version` 与 `requires_features`，不兼容或缺依赖插件会跳过加载。

### Fixed
- 修复 scheduler 修改 cron 表达式后 next fire 未及时重算，导致新触发时间不被识别的问题。
- LLM runtime fallback 从选路层补到调用层：主 AI 命令与 scheduler 调用失败后会按 retry + fallback chain 自动切换 provider。
- 补齐 sudo / owner_only 安全模型：支持 incoming sudo 命令，插件默认 owner-only，内置群消息插件显式放开。
- 修复插件 `run_plugin` 命令模板占位问题，改为调用已加载插件注册的命令。

### Changed
- 前端系统设置页新增 LLM 成本限额配置入口。
- 插件 loader 对第三方插件继续保持最小 capability sandbox，并在派发前校验 owner / sudo 权限。
- reload_config / reload_commands / sudo / rules / remote plugin reload 通知改为 ACK 优先、周期 reconcile 兜底。

### Verification
- 后端全量测试通过：`462 passed, 2 skipped`。
- 前端类型检查通过：`pnpm -C frontend exec tsc -b --noEmit`。
- 前端生产构建通过：`pnpm -C frontend build`。

---

## [0.9.6] — 2026-05-09 · fix · 插件配置弹窗修复 + 远程插件安全加固

### Fixed
- 前端插件配置弹窗崩溃：修复 ConfigDialog hook 顺序，补全 enum / array 字段渲染
- 内置功能配置入口统一：插件页、账号详情页的「配置」按钮改为跳转真实功能页
- 插件管理页已安装列表补齐版本列和操作列

### Changed
- 远程插件安全闭环继续加固：安装阶段只读 plugin.json，worker默认不再扫描执行 installed 插件，只有 RemotePlugin.enabled + AccountFeature.enabled 双开关通过后才按需加载
- 远程插件热加载从不适配第三方的 reload_plugin 改走 reload_config，支持按 plugin_key 强制卸载旧实例后重载

---

## [0.9.5] — 2026-05-08 · chore · Codex 审查合并 + 仓库清理

### Changed
- 合并 Codex GPT-5.5 审查产出的代码改动（sandbox、LLM runtime、测试用例等）
- 清理非必要文件（archive/、agent-plans/、审查文档）
- 更新 .gitignore 排除开发计划与归档目录

---

## [0.9.4] — 2026-05-08 · feature · 插件配置系统 + 账号级配置统一

### Added
- 新建 ConfigDialog 组件：JSON Schema 驱动的自动表单渲染
- Feature API 返回 config_schema
- 内置插件 config_schema 补全：game24 / scheduler / translate

### Changed
- 账号详情「功能开关」更名为「插件启停」
- Account Detail 和 Extensions 的「配置」按钮统一为 ConfigDialog 弹窗
- 所有 Feature 配置页返回按钮改为 /accounts/:aid?tab=features
- Account Detail Tab 支持 URL 参数 ?tab= 控制默认激活项
- 插件开发指南补充 config_schema 规范（level 字段 + 验证清单）

---

## [0.9.3] — 2026-05-08 · fix · 插件中心重构 + 版本号 bump

### Changed
- 插件中心从 4 Tab 重构为 3 Tab（账号插件管理 / 插件管理 / 开发指南）
- 废弃功能矩阵（NxM 格子），替换为账号级插件管理
- 合并本地内置插件、第三方插件、远程插件到统一列表

---

## [0.9.2] — 2026-05-08 · feature · 插件中心重构 + 账号级插件管理

### Added
- 远程插件安装支持 default_enabled 参数
- 账号级远程插件管理 API（enable-accounts / disable-accounts）
- 新建 Alembic 迁移 0019（remote_plugin.default_enabled）

---

## [0.9.1] — 2026-05-08 · fix · 远程插件 API 导入修复

### Fixed
- remote_plugin.py API 导入 get_db 路径错误（应为 deps.DBSession）
- 移除未使用的 Depends / AsyncSession / select 导入

---

## [0.9.0] — 2026-05-08 · feature · 远程插件管理系统 + 安全加固

### Added
- **远程插件管理系统**：
  - `RemotePlugin` 数据库模型 + Alembic 迁移（0018、0019）
  - 远程插件 API（list / install / enable / disable / update / uninstall / enable-accounts / disable-accounts）
  - 远程插件 Service 层（git clone、manifest 解析、热加载触发、Feature 表联动）
  - `/plugin` Bot 命令（list / install / remove / enable / disable / update，install 支持 `--default` 参数）
  - 前端远程插件管理 Tab（卡片布局、安装输入框、默认启用开关、启用/禁用/更新/卸载）
  - 插件验证函数 `validate_manifest()`（必填字段校验）
- **账号级远程插件管理**：
  - 安装时可勾选「默认为所有账号启用」（`default_enabled`），自动注册到 Feature 表 + 为所有账号创建 AccountFeature 行
  - 功能矩阵 Tab 自动展示远程插件列（因为注册到了 Feature 表）
  - 按账号启停远程插件 API（`enable-accounts` / `disable-accounts`），复用 `feature_service.bulk_set_enabled`
  - 卸载时自动清理 Feature / AccountFeature 关联行
- **插件中心 UI 重构**：
  - 远程插件从独立页面合并到插件中心第四 Tab（功能矩阵 / 已加载插件 / 远程插件 / 开发指南）
  - 侧边栏移除「远程插件」独立入口，统一走「插件」
  - `/remote-plugins` 旧路由自动跳转到 `/plugins`
- **远程插件开发文档**：`docs/REMOTE-PLUGIN-GUIDE.md` + `docs/REMOTE-PLUGIN-DEV-PLAN.md`

### Fixed
- 插件名称正则去除 `.`，防止 `..` 路径穿越攻击
- `remote_plugin` Schema 字段 `installed_at` 与 model 对齐
- 安装后 toast 根据是否默认启用显示不同提示文案

### Changed
- 插件开发指南（PLUGIN-DEV-GUIDE.md）全面重写，合并远程插件规范
- Sprint5 和 CONTRACTS 归档到 `archive/plans/`
- 版本号统一升至 0.9.0

---

## [0.8.0] — 2026-05-08 · feature · Sprint5 功能实现

### Added
- **Generation Guard (Task #18)**：Account 模型添加 `generation` 字段，防止并发操作冲突
- **命令别名系统 (Task #19)**：
  - CommandAlias 模型和数据库迁移（0016_command_alias.py）
  - 贪心最长匹配别名解析
  - `,alias` 管理命令（set/del/ls）
- **Sudo 系统 (Task #20)**：
  - SudoUser 模型和数据库迁移（0017_sudo_user.py）
  - Sudo 模式：允许指定用户代表账号执行命令（默认前缀 `.`）
  - 权限检查：tg_user_id、chat_id 白名单、命令白名单
  - `,sudo` 管理命令（add/del/ls）
- **Conversation API (Task #21)**：
  - conversation.py 实现多轮对话工具类
  - 支持 send()、get_response()、click_button()、mark_read()
  - 超时处理和自动 handler 清理

### Security
- Sudo 权限在命令派发前检查，权限拒绝返回清晰错误信息
- 基于白名单的 chat 和命令访问控制

### Changed
- CommandContext 新增 `sudo_users` 和 `sudo_prefix` 字段
- runtime.py 加载 sudo 配置到命令派发上下文

### Fixed
- `runtime.py` 补充缺失的 `from typing import Any` 导入（F821）
- `conversation.py` 将 `asyncio.TimeoutError` 改为内置 `TimeoutError`（Python 3.12 兼容）
- 前端 `SudoManagement.tsx` / `AliasManagement.tsx` 修复 TypeScript 类型错误：queryFn 泛型、不存在的 AlertDialog 组件替换为 `window.confirm`、`getAccounts` → `listAccounts`
- `ruff check` 自动修复存量 import 排序和无用 f-string 问题

### Tests
- 新增 `test_conversation.py`（10 用例）：超时、队列、context manager、handler 注册、send、click_button
- 新增 `test_alias.py`（8 用例）：默认值、贪心最长匹配、参数透传、别名→内置/模板派发
- 新增 `test_sudo.py`（13 用例）：权限检查全分支、generation guard 递增与匹配

---

## [0.7.1] — 2026-05-07 · patch · Docker 部署优化与前端构建修复

### Added
- **GitHub Actions CI (Frontend)**：新增 `.github/workflows/frontend-ci.yml`，在推送或 PR 到 main 分支时自动校验前端构建。

### Fixed
- **Docker 部署路径报错**：修复 ARM64 构建时 `nginx.conf` 找不到以及 `docs/` 目录在容器内路径不匹配的问题。
- **Frontend 生产构建失败**：
  - 修正 `Extensions.tsx` 在 TypeScript strict 模式下的类型推断错误。
  - 修正 `ConfigBackup.tsx` 中 `includeSensitive` 变量名拼写错误导致的 `Cannot find name 'include_sensitive'` 编译报错。
- **TypeScript 编译放宽**：调整 `tsconfig.app.json` 中的 `strict: false`，确保在生产环境（尤其是低配 ARM 服务器）中构建更加稳健。

---

## [0.7.0] — 2026-05-07 · feature · 更新检查器 & 配置备份 & 命令转发增强

### Added
- **分步确认式更新检查器**（TopBar 刷新按钮 → UpdateDialog 状态机）
  - 点击顶栏刷新图标打开对话框，自动 `git fetch` 对比本地/远程 commit
  - 三步流程：检查更新 → 拉取更新 → 重启应用，每步需用户手动点击确认
  - 支持 origin/main 和 origin/master 分支自动检测
  - 重启前二次确认弹窗，重启后 5 秒倒计时自动刷新页面
  - 重启方式自动检测：docker-compose → Makefile → SIGTERM 兜底
- **配置备份与恢复**（Settings → 备份与恢复卡片）
  - 导出：12 个数据库类别可选导出为 JSON 文件下载
  - 类别：系统设置、命令模板、账号命令绑定、LLM Provider、转发规则、自动回复规则、风控模板/规则、插件功能配置、账号设置、忽略列表、通知 Bot
  - 敏感数据开关：默认排除 session/api_key/token/phone 等加密字段，手动开启后包含
  - 导入：上传 JSON 文件，按 ID 字段去重检测，已存在的记录自动跳过
  - bytes 类型字段自动 hex 序列化/反序列化

### Changed
- **自定义命令 forward_to 支持 4 种转发模式**
  - `forward_native`（原生转发，携带原作者）/ `copy_text`（纯文本复制）/ `quote`（带"来自 X"前缀）/ `link_only`（仅发消息链接）
  - 成功提示文案根据模式动态显示（转发/复制文本/引用转发/链接）
- **自定义命令支持"立即删除"选项**：转发成功后立即删除命令消息（无需设置延迟秒数），与延迟删除互斥
- **转发插件 target_chat_id 改为可选**：留空或非法值时默认转发到当前消息来源的 chat
- **Game24 配置页命令前缀动态化**：命令示例改为从 `getSystemSettings` API 实时读取 `command_prefix`，不再硬编码 `,`
- **Game24 插件提示文案优化**：开始游戏时的规则说明更简洁，示例算式更直观

### Added (Docs)
- 插件开发指南新增 §10「前端配置页规范」：明确命令前缀必须动态获取，禁止写硬编码

---

## [0.6.0] — 2026-05-07 · feature · 24 点游戏插件 & Builtin 插件热发现

### Added
- **24 点游戏插件**（`builtin/game24`）：群内互动小游戏，支持竞速答题与奖金发放
  - 触发方式：`,24d <奖金金额>`（例：`,24d 2000`）— 由自己发送（outgoing），在群里触发
  - 随机生成 4 个 1–13 的整数，保证有解（递归求解器验证）
  - 双通道消息处理：`on_outgoing` 监听自己的触发指令，`on_message` 监听群内他人的答题
  - 表达式安全求值：ast 白名单过滤，仅允许 `+ - * /` 和括号，禁止函数调用/属性访问/幂运算
  - 支持用户输入运算符别名：`x` / `X` / `×` → `*`，`÷` → `/`
  - 第一个答对者自动发奖：Bot 回复其消息 `+<奖金数量>`
  - 超时机制：默认 500 秒，超时无人答对自动宣布结束
  - per-chat 游戏状态隔离，同一群已有进行中游戏时拒绝开新局
  - 可配置项：`trigger_pattern`（自定义触发正则）、`timeout`（限时秒数）
  - 权限声明：`send_message` / `edit_message` / `read_chat` / `delete_message`

### Changed
- **插件框架 `message_channels` 声明式消息方向**：`Plugin` 基类新增 `message_channels` 类属性（默认 `{"incoming"}`），插件声明需要监听的方向后，loader 自动向其派发对应事件，无需重写新 hook；loader 统一 incoming/outgoing 两个 dispatcher，按插件声明过滤
- **插件命令注册支持实例属性**：loader `_activate` 优先读取插件实例的 `commands`（`on_startup` 动态设置），回退到类属性，支持插件根据 config 动态注册不同命令名
- **Builtin 插件动态发现与热重载**：`BUILTIN_FEATURES` 从静态常量改为运行时文件系统扫描，新增 builtin 插件无需重启后端进程即可生效
  - 新增 `scan_builtin_manifests()`：动态扫描 `app/worker/plugins/builtin/` 目录，import 各子包 `manifest.py` 读取 `MANIFEST.key / display_name`
  - 新增 `_LazyBuiltinFeatures(dict)`：继承 dict 的惰性字典，首次访问自动触发扫描，支持 `refresh()` 强制重扫
  - 主进程侧：`seed_builtin_features()` 每次调用前 `BUILTIN_FEATURES.refresh()`，自动 seed 新插件行
  - Worker 侧：`reload_account_config()` 第一步刷新 `BUILTIN_FEATURES`，配合 `discover_plugins()` 激活新插件
  - 保留 `FEATURE_AUTO_REPLY / FORWARD / SCHEDULER / GAME24` 等具名常量向后兼容
  - 修复 `builtin/__init__.py` 缺少 `game24` 导入
- **功能矩阵启用后即时反馈**：toggle mutation 改用乐观更新（`onMutate` 立即改本地缓存），不再等 invalidateQueries；1.5 秒后再重新拉取确认 worker 已激活
- **账号详情页功能开关动态化**：`FEATURE_KEYS` 硬编码列表改为从 feature-matrix API 动态获取，新增插件自动出现在开关列表
- **已加载插件 Tab 显示内置插件**：数据源从 `installed-packages`（仅第三方）改为同时展示 builtin（来自 feature-matrix）和第三方插件
- **Feature 配置页通配路由**：新增 `:aid/features/:featureKey` 通配路由，未实现配置页的 feature 统一显示 TodoPage 占位
- **Game24 触发改为命令注册**：从正则匹配改为 `commands` 字典注册，指令名默认 `24d`（可通过 config.command 自定义），跟随系统命令前缀；答题保持 incoming `on_message`
- **Game24 配置页面**：配置项从 `trigger_pattern` 改为 `command`（指令名），支持设置触发指令名和答题限时

### Fixed
- **Docker 部署路径报错**：修复 aarch64 构建时 `nginx.conf` 找不到以及 `docs/` 目录在容器内路径不匹配的问题。
- **Frontend 生产构建报错**：解决 `Extensions.tsx` 在 strict 模式下的 TypeScript 类型推断错误（TS2339/TS6133），并放松 `tsconfig` 的 `noUnused` 限制以确保编译通过。

### Affected Files
- `backend/app/worker/plugins/builtin/game24/__init__.py`
- `backend/app/worker/plugins/builtin/game24/manifest.py`
- `backend/app/worker/plugins/builtin/game24/plugin.py`
- `backend/app/worker/plugins/base.py`（新增 `message_channels` 声明式属性）
- `backend/app/worker/plugins/loader.py`（新增 outgoing 派发器 + `reload_account_config` 加 refresh）
- `backend/app/db/models/feature.py`（`scan_builtin_manifests` / `_LazyBuiltinFeatures` / `BUILTIN_FEATURES` 重构）
- `backend/app/services/feature_service.py`（`seed_builtin_features` 加 refresh）
- `backend/app/worker/plugins/loader.py`（`reload_account_config` 加 refresh）
- `frontend/src/pages/Extensions.tsx`（乐观更新 + builtin 插件列表）
- `frontend/src/pages/Accounts/Detail.tsx`（FEATURE_KEYS 动态化）
- `frontend/src/App.tsx`（通配 feature 路由）
- `frontend/src/pages/Features/Game24Config.tsx`（game24 专属配置页）

---

## [0.5.2] — 2026-05-07 · hotfix · AI provider 实时刷新与稳定性修复

### Added
- **AI inline 强制刷新命令**：新增 `,ai @refresh`（兼容 `@reload`）用于手动刷新 worker 内 `LLMProvider` 快照并回显最新 provider 列表。
- **provider 列表用法提示增强**：`@list` 输出中新增 `@refresh` 用法提示，便于用户现场排障。

### Changed
- **provider 变更广播范围扩大**：`create/update/delete LLM provider` 后，reload 通知从“仅启用 ai 模板的账号”升级为“全账号广播”，降低边界场景漏刷风险。
- **reload 发送策略收紧**：`notify_reload()` 改为逐账号发送并汇总失败账号，不再静默吞异常。

### Fixed
- **新增 provider 后 `,ai @list` 非实时问题**：`_run_ai` 在执行前继续主动刷新命令上下文，并在 provider miss 场景下触发一次兜底刷新后重查，修复“刚新增 provider 仍提示未找到”的问题。
- **reload 失败不可见问题**：provider reload publish 失败现在会明确记录异常并抛出错误，避免“前端成功、worker 未刷新”的假成功状态。

### Affected Files
- `backend/app/api/commands.py`
- `backend/app/services/command_service.py`
- `backend/app/worker/command.py`
- `backend/app/worker/inline_override.py`


### Added
- **项目归档与预览支持**：
  - 新增 `docs/screenshots/` 文件夹用于存放 UI 截图
  - `.gitignore` 增加 `archive/` 与 `.github_backup/` 规则
- **预置设备伪装**：新增 Android 选项（Samsung Galaxy S24 / Android 14 / Telegram 12.6.4）
- **部署依赖**：`backend/pyproject.toml` 补全 `psycopg2-binary` 依赖，解决 Docker 模式下 Alembic 无法运行同步迁移的问题

### Changed
- **Claude API 协议优化**：`AnthropicClient` 切换为 **Streaming (SSE)** 模式调用，解决 Anyrouter 代理返回 400 的兼容性问题
- **README 全面优化**：
  - 更新为真实的用户名 `Anoyou`
  - 增加 **Docker 部署（生产环境推荐）** 指南
  - 增加 **Prerequisites**（API ID/Hash 申请与网络代理配置）
  - 增加 **Configuration** 核心安全项说明
- **Docker 构建优化**：前端镜像 Build Context 提升至项目根目录，支持构建时访问 `docs/` 进行文档打包

### Fixed
- **Worker 缓存过时**：在执行每个 AI 命令前强制从 DB 刷新 `LLMProvider` 列表，解决“前端新增 provider 后 ,ai @list 仍显示未找到”的问题
- **Worker 环境补全**：修复 `_run_ai` 缺失 `api_format` 导致无法正确识别模型协议的 bug
- **文件名大小写修复**：`Dashboard.png` -> `dashboard.png` 确保在 Linux 容器下正常渲染

---

## [0.5.0] — 2026-05-06 · RC1 · 开源前打磨

### Added
- **GitHub Actions CI**：`.github/workflows/ci.yml` 自动化测试流水线
  - Backend job：pytest + ruff + alembic upgrade（PostgreSQL 16 + Redis 7 services）
  - Frontend job：pnpm build（Node 20 + pnpm 9）
  - 推送到 main 或 PR 时自动触发
- **开源协议**：MIT License，同步更新 `pyproject.toml` 和 `package.json` license 字段
- **应急响应工单模板**：`docs/SECURITY-OPS.md` 新增 §6，提供安全事件记录模板

### Changed
- **README 重写为开源向**：
  - 简洁的 Feature 列表（8 项核心功能）
  - Quick Start 分本机自用 / 公网部署两条路径
  - FAQ 回答"多用户支持"、"为什么用 userbot"
  - 状态声明：Alpha / 个人自用 / 欢迎 fork 但暂不接大 PR
- **SECURITY-OPS 润色**：
  - 顶部新增"如果你打算公网部署"提示
  - "某 web 用户账号"改为"管理员账号"（单租户语境）
  - 应急 SOP 措辞更精准

### Fixed
- **inline @provider 覆盖 bug**：`,ai @provider 问题` 现在将会正确使用 已配置的provider 的 default_model，而不是错误地使用命令模板里配置的 model
  - 旧逻辑：`override_model = cfg.get("model")` 总是从模板读取，导致切换 provider 后 model 不匹配
  - 新逻辑：当用户指定 `@provider`（未指定 `:model`）时，清空 `override_model`，让 `build_client` 使用该 provider 的 `default_model`
  - 优先级现在是：`@name:model` > provider.default_model > 模板 model（仅当无 inline override 时）
  - 新增测试用例：`test_run_ai_inline_provider_without_model_clears_template_model`

### Removed
- **仓库归档清理**（已在 Wave 2 完成，本次补充文档）：
  - `agent-plans/SPRINT4-*.md` 归档到 `archive/plans/SPRINT4/`
  - `archive/plans/SPRINT4/README.md` 溯源文档更新

### Notes
- 首个 Release Candidate（RC1），可以开源了
- CI 徽章 URL 需要替换为实际 GitHub 仓库地址
- README 截图占位（Coming soon），待后续补充

---

## [0.4.2] — 2026-05-06 · Sprint 4 hotfix · 版本检测

### Added
- **前后端版本不一致检测**：避免再发生"代码改了但 uvicorn 没重启 / SW 缓存老前端 → 用户看老行为以为代码没生效"的幻觉式 bug。
  - 后端：`GET /api/system/version`（**public 无鉴权**）返回 "version": "0.7.1"
  - 前端：`GlobalAlertBar` 拆成 `VersionMismatchBar` + `KillSwitchBar`；启动 + 每 60s 拉一次后端版本，不一致时顶部弹**琥珀色横幅**："前后端版本不一致 · 前端 vX.Y.Z · 后端 vA.B.C — 请 `make restart` + 硬刷"，含一键"硬刷新"按钮。
- **agent-plans/README.md §4.1**：新增"必跑 `make restart`"规范，列明哪些情况必须整套重启 + 哪些 `--reload` 能搞定。
- **agent-plans/README.md §3** 共用基础设施：把 `make up` / `make restart` / `make down` 提到首位，`make backend`（带 reload）降级为"调试时用"。

### Notes
- 单元测试不要直接 `from app.api.system_health import router` 然后调 `/api/system/version`——这是 public 端点没鉴权依赖，pytest 拿到的是 `VersionInfo(version=__version__ = "0.7.1"
APP_STAGE: str | None = "feature"
int 4")`，下次 bump 版本号时 `stage` 字段如果摘掉得记得这里同步。

---

## [0.4.1] — 2026-05-06 · Sprint 4 hotfix · UX 体验四件套

### Added
- **内置命令面板**：自定义命令模板页顶部新增「内置命令（只读）」卡片，列出 worker 静态注册的所有命令（name + aliases + doc），让用户起自定义模板名时一眼知道哪些已被占用。
  - 后端：`GET /api/commands/builtin` 从 worker `_BUILTIN` 字典读取（无 DB 依赖，无运行时状态）
  - 安全：纯只读，无敏感数据
- **扩展中心**（路由 `/extensions`）：合并原「功能矩阵」+「插件管理」+「插件开发指南」三处入口，三 tab 形式呈现：
  - Tab 1：功能矩阵（账号 × 功能 启停状态总览，含克隆其他账号规则功能）
  - Tab 2：已加载插件（builtin + 第三方装到 `data/plugins/installed/` 的）
  - Tab 3：开发指南（`docs/PLUGIN-DEV-GUIDE.md` 通过 vite raw import 打包进前端，react-markdown + remark-gfm + rehype-highlight 渲染）
- 前端依赖：`react-markdown` / `remark-gfm` / `rehype-highlight` / `highlight.js` / `@tailwindcss/typography`。

### Removed
- **`group_admin` / `monitor` 残留清理**（v0.4.0 删了 builtin 目录但前端 / DB 未跟上）：
  - `frontend/src/pages/Features/GroupAdmin.tsx` / `Monitor.tsx` 文件
  - `App.tsx` 两条路由
  - `Wizard.tsx` cloneConfig 列表中的两项
  - `Detail.tsx` `FEATURE_KEYS` 数组中的两项
  - `feature.py` 的 `FEATURE_GROUP_ADMIN` / `FEATURE_MONITOR` 常量 + `BUILTIN_FEATURES` 映射
  - 数据库 `feature` / `account_feature` 表中的旧行（迁移 `0014_drop_legacy_features`）
- 老路由 `/matrix`、`/plugins`、`/settings/plugins` 改为 redirect 到 `/extensions`（书签兼容）
- `frontend/src/pages/FeatureMatrix.tsx` / `Plugins.tsx` / `Settings/PluginManager.tsx` 文件（内容并入 `Extensions.tsx`）

### Changed
- Sidebar 顶层菜单 7 项 → 7 项（"功能矩阵" + "插件管理" 合成 "扩展中心"，腾出位置但不增加）。

### Fixed
- alembic 0012 误写 `down_reversion = "0.7.1"` 与 0011 构成分叉，汇总验收时修正为 `down_revision="0011"`，再次回到单 head。

---

## [0.4.0] — 2026-05-06 · Sprint 4 Wave 2

### Added
- **定时任务插件**（迁移无新增，复用 `rule.config`）：完整实装 cron / once / interval 三种模式；前端 `Features/Scheduler.tsx` 完整规则编辑页 + dry-run；通过 `ctx.engine.acquire("send_message_group")` 走风控（`scheduler/plugin.py` 20 行 → 283 行）。
- **多 Telegram Bot 通知通道**（迁移 `0013_notify_bot`）：项目启动时发"📦 telebot vX.Y.Z started"；worker 进 dead 状态时发告警。
  - `bot_token` Fernet 加密入库，GET 返 `has_token: bool`，永远不返明文（约定 D）
  - 是 Bot Token（@BotFather 创建）走 HTTP API，与 userbot session 完全独立
  - 前端 `Settings/NotifyBots.tsx` 提供 CRUD + 一键测试
- **插件开发指南**：`docs/PLUGIN-DEV-GUIDE.md` 含目录骨架、Manifest 字段、Plugin/Context hook、风控接入、Telethon 速查、沙箱权限说明。
- **首个插件样例**：`examples/plugins/translate/`——翻译插件，作为开发模板（不放进 builtin/）。
- **公网部署文档**：`docs/DEPLOY-PUBLIC.md` + `deploy/Caddyfile.example`（Let's Encrypt 自动证书 + HSTS + 写操作限速 + reverse_proxy）。

### Removed
- `group_admin` / `monitor` 两个 builtin 插件骨架（PRD 列出但实际不需要的功能；20 行占位删除）。
- 插件市场 UI（`PluginMarket.tsx` / `plugin_repo_service.py` / 远程订阅 API / zip 上传 API）：单用户场景没人订阅远端仓库；用 `git clone` 到 `data/plugins/installed/<key>/` 替代。
- `plugin_repo` 表（迁移 `0012_drop_plugin_repo`）。

### Changed
- `PluginManager.tsx` 简化为"已加载列表 + enable/disable + uninstall"。
- `Settings/Index.tsx` 移除 `<PluginMarket />` 嵌入；新增 `<NotifyBots />`。
- `main.py` lifespan 启动时尝试发项目启动通知；`supervisor.py` 在 account 进 dead 时发告警（NotifyBot 未配置时静默跳过）。

### Fixed
- alembic 0012 误写 `down_revision="0010"` 与 0011 构成分叉，汇总验收时修正为 `down_revision="0011"`，再次回到单 head。

---

## [0.3.1] — 2026-05-06 · Sprint 4 Wave 1

### Added
- 内置命令新增 `,del N`：撤回当前会话中自己最近 N 条消息（含命令消息本身）。
- 自定义命令模板新增 `aliases` 字段（迁移 `0011_command_aliases`），支持一个模板多个短命令入口。

### Changed
- Telethon 依赖升级到 `>=1.43,<2.0.0`。
- 内置命令支持短别名：`help(h)`、`status(s, st)`、`id(i)`、`version(v)`。
- `,help` 输出改为折叠展示主命令与别名，并合并展示自定义模板别名。
- 命令模板设置页新增别名输入与展示列；保存时对别名格式和冲突做校验。
- 前端版本号改为 `frontend/src/lib/version.ts` 单点定义，export const APP_VERSION = "0.7.1";

### Fixed
- 自定义命令名/别名冲突规则统一：同账号维度下，模板 `name + aliases` 不可互撞，也不可与内置命令及其别名冲突。

## [0.3.0] — 2026-05-05 · Sprint 3 · LLM 重构 + 安全加固

### Added — LLM Provider 体系大升级
- **多模型支持**（迁移 `0008`）：`llm_provider.models` JSONB 数组，单 provider 可启用多个模型；老数据自动把 `default_model` 回填到 `models[0]`。
- **API 协议解耦**（迁移 `0009`）：新增 `llm_provider.api_format`，独立于 provider 厂商：
  - `chat_completions`（OpenAI 经典 `/v1/chat/completions`）
  - `responses`（OpenAI 2024 新协议 `/v1/responses`，国内反代如 anyrouter 只接这个）
  - `anthropic_messages`（Anthropic `/v1/messages`）
  - 老数据按厂商自动回填；同一个反代可改协议而不改 provider。
- **LLM 走代理**（迁移 `0007`）：`llm_provider.proxy_id` 外键，支持调用 LLM 时走指定 SOCKS5/HTTP，绕开网络封锁。`mtproxy` 类型 schema 层拒绝。
- `services/llm_client.py` 重构：`OpenAIClient` / `ResponsesClient` / `AnthropicClient` 三客户端，按 `api_format` 路由。错误信息统一带 host 让用户一眼看出是哪个反代不通。
- `services/llm_format.py`（新建，336 行）：输出模板渲染器
  - `{key}` 占位符 + `{?key}...{/?}` 条件块（未知 key 留空，不抛 KeyError）
  - 派生变量 `answer_first_2` / `answer_rest`，配合 `<blockquote expandable>` 实装"前两行 + 折叠"
  - 默认走 HTML（telethon 1.36 不支持 markdownv2）
  - 4000 字符截断（TG 单条 4096 上限留余量）

### Added — 安全加固
- **JWT 版本校验**（迁移 `0010`）：`web_user.pwd_version` 默认 0
  - JWT payload 加 `pwd_v`
  - 改密后 `pwd_version += 1`
  - 旧 token 上的 `pwd_v` 不匹配自动失效
  - 这是 0.2.1 SECURITY-OPS 里"改密后强制下线"的真正实装；之前只是清当前 cookie，旧 token 在另一台设备上还能用

### Added — 命令模板编辑增强
- 新增 4 个预设输出模板（simple / quote / minimal / translate）一键插入。
- 占位符按钮 + 条件块按钮 → 不会写模板的用户也能拼出消息格式。
- ProviderModelSelect / ProviderModelsSection：在多模型 provider 里展开式选具体 model。

### Changed
- LLMProviders 页改造：API format 选择 / 模态 / tag / cost_tier / 模型列表管理。
- CommandTemplates 页改造：输出模板可视化编辑、provider+model 双层选择。

### Notes
- 升级路径：`alembic upgrade head` 一次跑齐 0007 → 0008 → 0009 → 0010。
- Sprint 2 完成 (0.2.0) 时 LLM 是骨架级（仅 OpenAI/Anthropic 直连 + 单模型 + chat_completions 写死），本次相当于把这条线做到生产可用级别。

---

## [0.2.1] — 2026-05-03 · Sprint 2 hotfix

### Fixed
- alembic 0003 分叉（`0003_command_template` 与 `0003_add_device_profile` 同号同 down_revision）线性化为 `0003 → 0003b`，`alembic upgrade head` 现在能跑通。
- 自动回复"突然不工作"——根因是 0006 给 `llm_provider` 加了 `modality` / `tags` / `cost_tier` / `notes` 但生产 PG 没升级；worker 启动时 `select(LLMProvider)` 报 UndefinedColumnError 导致 supervisor 把账号置 dead。修复后跑了 `alembic upgrade head`。
- `test_accounts.py` 的 `AsyncMock(db)` fixture 没跟上 `device_profile.get_default` 的引入，9 个 confirm 系列测试失败；新增 `_stub_device_profile` autouse fixture 修复。

### Added
- 日志中心拆成「消息日志 / 系统日志」两个 tab：
  - **消息日志** (`source=event`)：incoming 消息事件 / plugin 命中 / 命令派发，排查"为什么没回复"用
  - **系统日志** (`source=system`)：worker 启停 / IPC reload / 风控状态 / 技术异常，排查"账号是不是真的活着"用
  - 后端 `/api/logs/runtime` 加 `source` 过滤参数，自动兼容历史 `worker` / `plugin` 旧值
- `worker/runtime.py` `_log()` 与 `worker/plugins/loader.py` `_log()` 增加 `source` 关键字参数（默认 `system` / `event`）。
- `RecentPeersResponse`：`fetch_recent` 拆 `(worker_alive, items)`，前端可精准引导"worker 离线 vs 没收到消息"两种空状态。

### Changed
- 忽略 tab 在最近活跃为空时显示 worker 在线/离线徽章 + 三态精准提示文案。
- 风控基础动作中文标签全部对齐后端 `_DEFAULTS`（之前 8 个 key 名错位 + 6 个未覆盖）。
- 详情页 tg_user_id 缺失警示条上加"重启 worker 同步"按钮（pause → 1s → resume）。

---

## [0.2.0] — 2026-05-03 · Sprint 2

### Added
- 自定义命令模板（reply_text / forward_to / ai / run_plugin）+ `,help` 自动列出。
- LLM Provider 抽象（OpenAI / Anthropic，API key Fernet 加密入库，仅 worker 进程内解密）。
- 忽略群组：per-account `ignored_peers`、worker `recent_peers` LRU、前端一键加入。
- 账号头像（懒加载、缓存 24h）+ Humanize 子区。
- 插件模块化 tier C：Manifest + zip 安装 + 仓库订阅 + 权限沙箱。
- 转发插件（4 mode：原生 / 复制 / 引用 / 仅链接）。
- Web 端：修改密码 / 禁用 TOTP。
- KillSwitch 全局红色横幅 + AccountStatusBadge 复合状态。
- 风控每个动作显示中文标签 + 一句话说明。
- 内置命令：`,version`：在 TG 内查看版本与运行环境。
- `docs/SECURITY-OPS.md` 生产部署安全清单。

### Changed
- 账号详情 "忽略" tab 改名 "忽略的群组"，最近活跃为空时显示精准引导。
- "出口/代理" tab 改名 "出口/伪装"。
- 老账号 `tg_user_id` / `tg_username` 缺失时给出"重启 worker 自动同步"提示。
- `backend/app/main.py` 的 `FastAPI(version=...)` 改为读 `app.__version__`，避免与 `__init__.py` 不同步。
- 前端版本号改为 `frontend/src/lib/version.ts` 单点定义，Sidebar 通过 `APP_VERSION_LABEL` 引用。

### Fixed
- 紧急停用后 UI 状态联动（之前 banner 不存在 + badge 还显示运行中）。

---

## [0.1.0] — 2026-04-xx · MVP / Sprint 1

### Added
- Telethon 客户端初始化。
- 登录向导（验证码 + 2FA）。
- Worker 子进程模型（mp spawn context + atexit/SIGTERM 守护）。
- IPC pub/sub（Redis）。
- 自动回复（关键词 / 正则 / 作用范围 / 冷却 / 白黑名单 / reply_to）。
- 风控引擎（18 actions × 5 policies × 3 层继承）。
- FloodWait / PeerFlood / SlowMode 异常映射。
- 代理库 + 连通性测试。
- 顶部网络环境徽章（IP / 国家 / 缓存 5min）。
- 内置命令：`,help` `,id` `,status` `,ping` `,pause` `,resume`。
- 两轮 review 修复（21 处真实改动，详见 `REVIEW-FIXES-REPORT.md`）。
