# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [SemVer](https://semver.org/lang/zh-CN/)：MAJOR.MINOR.PATCH。

- **MAJOR**：不向后兼容（数据库不兼容迁移 / 协议大改 / 配置项重命名 / API 路径变更）
- **MINOR**：向后兼容的功能增量（一个 Sprint 通常 +1）
- **PATCH**：bug 修复 / 文档 / 小调整 / hotfix

> 版本号在 5 处必须保持同步：`backend/app/__init__.py`、`backend/pyproject.toml`、`frontend/package.json`、`frontend/src/lib/version.ts`、本文件顶部段落。`backend/app/main.py` 通过 `from . import __version__` 自动跟随，无需单独改。详见 `agent-plans/README.md` §6。

---

## [Unreleased]

### Changed
- （空）

---

## [0.14.0] — 2026-05-16 · feature · 前端 IA 导航骨架

### Changed
- 前端顶层导航从 8 个入口收敛为 6 个用户目标导向入口：概览、账号、插件、AI、日志、系统；调度与模板不再占据顶层菜单，为后续 Plugins 中心化做路由落点。
- 新增 `/plugins/templates`、`/plugins/aliases`、`/plugins/scheduler` 与 `/ai/providers` 路由骨架，暂时复用现有页面文件，不移动页面内容。
- 旧入口 `/scheduler`、`/templates`、`/settings/commands`、`/settings/aliases`、`/settings/llm-providers`、`/ai-settings` 保留重定向，避免旧书签失效。

### Verification
- `pnpm --dir frontend build` 通过。

---

## [0.13.1] — 2026-05-16 · fix · 本地启动与 0.13 可见性修复

### Fixed
- 修复本地 `make up` 首次启动时 `.env.example` 默认 `localhost:5432/6379` 与 `docker-compose.dev.yml` 暴露端口 `15432/16379` 不一致导致 Alembic 连接失败的问题；开发启动脚本现在只在当前进程环境中映射端口，不回写 `.env`，也不影响生产部署。
- 修复 `scripts/_lib.sh::auto_tune_env` 在已有 `MEMORY_TIER` 时因 shell 变量边界解析触发 `unbound variable` 的问题。
- 修复 `ai_runtime.invoke()` 收口后测试与旧调用约定无法 monkeypatch 默认 LLM client 的问题，恢复 AI inline provider override、图片/贴纸、语音转写路径的本地测试稳定性。
- 修复内置 feature 旧行更新 manifest 时未可靠写回 `x-experimental` 的问题，确保 `codex_image` 的“实验性”标识能从后端正确下发到插件列表和账号 feature 列表。
- 恢复 `app.db.models.feature.BUILTIN_FEATURES` 兼容导出，避免旧调用点导入失败。

### Changed
- 将 Config Bundle 从“系统设置 → 全局控制”底部移到独立的“备份恢复”Tab，降低入口隐藏感。
- 为 Config Bundle 补充大白话说明，明确它用于把 A 账号的规则、插件配置和自定义命令绑定复制到 B 账号；上传配置包只做预览，只有点击“确认写入目标账号”才会真正改数据。

### Verification
- `ruff check backend/app` 通过。
- `pytest backend` 通过：548 passed, 2 skipped。
- `pnpm --dir frontend build` 通过。

---

## [0.13.0] — 2026-05-15 · refactor · TelePilot 安全 / 架构 / Config Bundle 主线收口

### Changed
- PR1 安全收敛：删除高危 Telegram 命令入口，不再支持通过普通 Telegram 命令触发项目级 `,reboot/,rb`、远程插件运维 `,plugin install/remove/enable/disable/update`、以及 `,sudo add/,sudo del`；`sudo ls` 保留为只读查询。
- PR2 命令层拆分起步：已将 sudo 权限闸门与命令网关辅助逻辑抽离到 `backend/app/worker/commands/sudo_guard.py`，对外行为不变；dispatcher / builtin / template / long_message 等其余分层未在本版本落地。
- PR3 引入 `ai_runtime.invoke()` 统一标准 AI 调用入口，减少多路径调用漂移。
- PR4 补齐 sudo 关键路径 audit 记录，保证拒绝与关键操作可审计。
- PR5 收敛内置 feature 注册表：将动态扫描与惰性缓存逻辑抽离到 `backend/app/feature_registry.py`，`BUILTIN_FEATURES` 既有访问方式保持不变。
- PR6 将内置 `codex_image` 标记为 experimental（`x-experimental: true`），前端扩展列表与配置页增加实验性提示，不改变运行逻辑。
- PR7/PR8 Config Bundle 收口：支持 export/import 1MB 限制、dry-run 与 confirm 显式确认流程，并记录确认审计。
- PR9 补充 PluginContext 契约文档，明确字段能力边界与推荐访问模式。
- PR10 收口 account_bot confirm：token 改为 hashed + single-use，并修正 owner/action mismatch 时序。
- PR11 文档线收口：README 与 TelePilot 架构文档明确当前仓库与运行形态仍为 TeleBot，未承诺 rename/workflow/artifact/template renderer/marketplace。

---

## [0.12.1] — 2026-05-14 · refactor · Feature 配置页 CRUD 骨架抽取

### Changed
- 把 AutoReply / Autorepeat / Forward / Scheduler 四个 feature 配置页中重复的 rule CRUD 骨架（features+rules 查询、save/delete/toggle/dry-run mutation、规则编辑 Dialog 外壳、Dry-run Dialog 外壳）抽到 `frontend/src/pages/Features/_shared/` 共享模块。
- 新增 `useRuleCrud` hook 与 `RulePageHeader` / `RuleFeatureToggleCard` / `RuleInfoBox` / `Field` / `RuleEditDialogShell` / `DryRunDialogShell` 共享组件。
- 三个简单页面按字符数瘦身 27–35%：AutoReply -29%、Autorepeat -35%、Forward -27%；Scheduler 因深层嵌套 `setForm` 被 prettier 风格展开行数表观增加，CRUD/Dialog 逻辑已迁移到 hook，等效精简。
- 后续新增 feature 配置页时复用共享骨架即可，无需再复制 200+ 行 CRUD/Dialog 模板。

### Verification
- `tsc -b --noEmit` 通过。
- 浏览器手测：自动回复 / 自动复读 / 转发 / 调度四个页面的 CRUD、dry-run、立即执行行为均与重构前等价；不改任何对外行为。

---

## [0.12.0] — 2026-05-13 · feature · 小 VPS 资源预算与失败隔离

### Added
- 生产 `docker-compose.yml` 为 web / postgres / redis / frontend 增加明确的 `mem_limit` / `mem_reservation`，用于小 VPS 场景的资源预算与失败隔离。
- 新增 `scripts/_lib.sh::auto_tune_env`，在 `.env` 未显式设置 `MEMORY_TIER` 时按 Docker 可用内存写入 tiny / small / large 档位。
- worker 子进程新增独立 DB / Redis 连接池默认值，并通过 `app.worker.entry.worker_entry` 在导入 runtime 前标记 `TELEBOT_WORKER_PROC=1`。
- 新增 `log_incoming_messages_default` 与系统设置热加载开关，默认关闭逐条 incoming message 可见性日志。

### Changed
- PostgreSQL / Redis 默认资源参数收紧，降低小部署的峰值占用；web 增加 `init: true` 与 uvicorn `--limit-concurrency 64 --backlog 64`。
- worker 配置 reconcile 默认间隔从 60s 调整为 180s，并在插件加载完成后主动 GC 一次。
- Dashboard 资源面板和健康状态轮询降频，所有轮询查询在后台标签页停止 refetch。
- 前端非首屏路由改为 lazy load，Vite 将 markdown / radix 拆分为独立 chunk，降低首屏 JS 压力。
- `system_health` 进程采样缓存 psutil `Process` 实例，移除每次读取时的固定 `sleep(0.05)`；runtime_log 5 分钟计数增加短 TTL memo。

### Fixed
- 修复本地 OrbStack / Docker Desktop 场景下内存档位误读 Mac 宿主机 RAM 的问题：`detect_memory_tier` 现在优先读取 `docker info .MemTotal`。
- 撤回 `pip --no-compile` / 删除 `__pycache__` 的镜像瘦身尝试，避免冷启动现场编译依赖导致 web RSS 明显升高。

### Verification
- 本地 OrbStack 约 1GiB / 1CPU 环境：PR fixed 总峰值约 `152.0 MiB`，低于 main fresh 的 `178.3 MiB`；平均总占用略高，定位为资源预算 / 峰值削平 / 失败隔离收益，而非平均 RSS 显著下降。
- VPS forced-tiny smoke：web 约 `210-215 MiB / 320 MiB`，postgres / redis / frontend 均在各自限制内。
- VPS 10 分钟轻负载验证：所有容器保持 healthy，`OOMKilled=false`，`RestartCount=0`。
- 后端 / 前端验证沿用优化 PR 验证：`ruff check backend/app`、`pytest backend`、`tsc -b --noEmit`、`vite build`。

---

## [0.11.4] — 2026-05-12 · fix · 远程插件模板预览

### Fixed
- 远程插件 `ConfigDialog` 中的 `template_preview` / `*_preview` 不再渲染为可编辑输入框，改为使用 `TelegramHtmlPreview` 展示只读 HTML 预览卡片。
- `readOnly: true`、`template_preview`、`*_preview`、`template_placeholders` 字段统一只读展示，并在保存配置时过滤掉，避免把预览和说明写回运行配置。
- `message_template` / `*_template` 保持可编辑多行输入，继续兼容现有远程插件 schema。

### Documentation
- 插件开发指南和远程插件指南补充自动弹窗对 `readOnly`、`template_placeholders`、`template_preview` / `*_preview` 的渲染规则。

### Verification
- 前端构建通过：`pnpm --dir frontend build`。

---

## [0.11.3] — 2026-05-12 · docs · 插件文案模板规范

### Documentation
- 修正插件消息模板预览规范：基础表单风格可参考 LLM / 自定义命令配置页，但消息模板预览应对齐“通用模板 → 自定义命令模板”的输出模板预览。
- 明确所有会发送、编辑或回复给 Telegram 用户看的插件文案都必须模板化，包括开局、进行中、答对、超时、取消、错误提示、媒体 caption 和重复触发提示。
- 明确用户可见文案不应硬编码在 `plugin.py` 中；代码里只保留模板默认值、不可恢复兜底错误、内部日志和开发者调试信息。
- 版本线说明：`1.1.x` 曾是误写；由于 `0.10.2` 后的 Sudo / 远程插件链路修复属于重要稳定性修复，版本线按 `0.11.0 -> 0.11.1 -> 0.11.2 -> 0.11.3` 延续。

### Verification
- 文档和版本号同步检查通过。

---

## [0.11.2] — 2026-05-12 · polish · 远程插件配置弹窗与文档

### Changed
- 远程插件 `ConfigDialog` 对齐自定义命令 / LLM 配置弹窗的宽度、滚动高度、字段间距和统一表单控件风格。
- 消息模板、提示词、正文、长文本等 schema 字段在自动配置弹窗中使用多行文本输入，减少模板编辑时的拥挤感。
- 运行期远程插件安装目录加入 `.gitignore`，避免本地安装/更新插件产生未跟踪文件；保留仓库内已有的 `translate` 示例。

### Documentation
- 插件开发指南补充 Schema 驱动弹窗的 UI 约定，要求自动配置弹窗对齐 LLM 配置页体验。
- 远程插件指南补充模板字段的占位符说明、示例值和只读预览建议。

### Verification
- 前端构建通过：`pnpm --dir frontend build`。

---

## [0.11.1] — 2026-05-12 · fix · 远程插件启用与热更新修复

### Added
- 插件开发指南新增“发布与交互体验检查清单”，覆盖版本同步、消息复用、状态管理、防滥用、公平性、资源清理和降级策略。
- 远程插件指南补充版本一致性、热更新确认、模板占位符、冷却、清理延迟和结束命令等发布前检查项。
- 新增远程插件启用流程测试，覆盖首次全局启用自动补账号级启用行，以及已有账号选择不被覆盖。

### Changed
- 远程插件首次在“插件管理”点“启用”时，如果还没有任何账号级配置，会自动为现有账号创建启用行，减少“已启用但不能触发”的误解。
- 远程插件 install / enable / update / uninstall 的 worker reload 通知改为事务提交后执行，确保 worker 热加载时读到已提交的版本、开关和账号配置。
- 远程插件从仓库安装时，`default_enabled=true` 会同步打开远程插件全局开关，并把账号级状态设为等待 worker 激活的 disabled，而不是提前标 active。
- 插件管理页远程插件按钮顺序调整为“更新 / 启用或禁用 / 卸载 / 按账号管理”，并为启用、禁用、卸载补齐图标。

### Fixed
- 修复 `owner_only=False` 的远程插件命令无法被群内 incoming 消息直接触发的问题，例如群成员发送 `。cy 100` 能进入公开插件命令分发。
- 修复远程插件更新后偶发仍运行旧版本的问题：installed 插件 reload 会清理模块缓存、注册表旧类和 `__pycache__`。
- 修复配置默认值合并前后不一致导致热重载误判命令配置变化的问题。
- 修复远程插件禁用、更新、卸载后 reload 时机过早，worker 可能读到旧数据库状态的问题。

### Verification
- 后端定向测试通过：`cd backend && .venv/bin/pytest app/tests/test_plugin_loader.py app/tests/test_plugin_security_regression.py -q`。
- 后端静态检查通过：`cd backend && .venv/bin/ruff check app/services/remote_plugin_service.py app/services/plugin_repo_service.py app/api/remote_plugin.py app/api/plugin_repo.py app/worker/commands/plugin_cmd.py app/worker/plugins/loader.py app/tests/test_plugin_loader.py app/tests/test_plugin_security_regression.py`。
- 前端类型检查通过：`cd frontend && pnpm -s exec tsc --noEmit`。

---

## [0.11.0] — 2026-05-12 · hotfix · Sudo 安全收紧 + 远程插件文档规范

### Added
- 远程插件指南补充配置弹窗数据来源说明：
  - `ConfigDialog` 不直接读取磁盘上的 `plugin.json` / `manifest.py`；
  - 前端实际读取 `/api/accounts/{aid}/features` 返回的 `features[].config_schema`；
  - 该字段来自数据库 `Feature.manifest.config_schema`；
  - 修改远程插件 `config_schema` 后，需要执行“更新插件”并确认后端完成元数据回写。
- 插件开发指南和远程插件指南补充抢答/答题类插件奖励交互规范：
  - 奖励金额优先通过触发命令传入，例如 `,game 100`；
  - 开局时把本轮奖励写入局状态，避免局中配置变化导致奖励不一致；
  - 答对后建议两步反馈：回复答对者纯文本 `+金额`，再编辑原题目消息追加结算信息；
  - 图片题面插件必须声明 `send_file` 权限；
  - 图片生成应避免隐式依赖未声明系统库，第三方依赖需要在文档中明确安装约束；
  - 配置页只放稳定配置项，如 `command`、`timeout`、`auto_next`。

### Changed
- 调整游戏插件开发规范中的推荐配置字段：`reward` 不再作为抢答类插件的首选固定配置项，改为“可选默认值/兜底值”，单局动态参数优先从命令参数读取。
- 更新可复制游戏插件骨架：示例改为从命令参数读取奖励金额，并在开局时冻结到 `RoundState.reward`。

### Fixed
- 修复群聊中发送 `..`、`...` 等多个小数点会被误判为 sudo 命令，并公开回复“Sudo 权限拒绝”的问题。
- incoming sudo 现在只对真实可派发的命令做权限检查；纯标点、重复前缀和未知文本会静默忽略，避免普通聊天内容误触发。
- Sudo 新增全局总开关，默认关闭；开启后也只允许在账号自身 chat（收藏夹语义）触发，群组和普通私聊里的 sudo 前缀都会静默忽略。
- 全局命令前缀也增加重复前缀静默过滤：例如前缀为 `。` 时，发送 `。。。` 不再提示“未知命令”。

---

## [0.10.2] — 2026-05-11 · fix · 远程插件契约收紧 + 开发文档发布

### Added
- 远程插件文档明确升级为“远程安装规范 + 主插件开发指南”的双文档契约：
  - `REMOTE-PLUGIN-GUIDE.md` 负责安装、更新、沙箱、账号启用、插件仓库等远程插件专属流程；
  - `PLUGIN-DEV-GUIDE.md` 负责运行时 API、配置命名、消息发送边界、并发抢答、调度生命周期、日志、测试清单等通用开发规范；
  - 从本版本开始，不再兼容旧版“只有 `plugin.json` + `plugin.py`”的单文件远程插件结构。
- 插件开发指南补齐插件工程规范：
  - 新增“命令回调 / on_message / 定时任务”的可用对象矩阵，说明什么时候用 `event.reply`，什么时候用 `ctx.client` / `ctx.scheduler`；
  - 新增抢答类插件标准模板：`chat_id -> asyncio.Lock` + 二次状态检查，避免并发双发奖；
  - 新增统一配置字段建议：`command`、`reward`、`timeout`、`auto_next`、`message_template`、`status_interval_seconds` 等；
  - 新增后台任务与调度生命周期说明，避免插件卸载后遗留幽灵任务；
  - 新增奖惩系统接入约定、发布前最小测试清单、可复制的游戏插件骨架。

### Changed
- 远程插件安装与更新改为强校验标准插件包结构：
  - 安装阶段仍只解析静态 `plugin.json`，不会 import 未信任 Python；
  - 同时要求仓库内必须存在 `manifest.py`、`plugin.py`、`__init__.py`；
  - 缺少运行期文件时返回可读错误，并引导开发者按 `docs/REMOTE-PLUGIN-GUIDE.md` 更新插件。
- 插件安装目录解析统一锚定项目根目录，避免不同启动目录下把远程插件写到错误位置。
- 远程插件更新流程支持从插件仓库的子目录重新安装覆盖，更新前后都会重新校验运行期结构。
- 插件中心的远程插件交互收口：
  - 已安装插件如果仓库版本更高，显示“可更新”并把按钮切换为“更新”；
  - 已安装远程插件增加“按账号管理”入口；
  - 账号级启用远程插件前会检查全局远程插件开关，避免 UI 提示已启用但 worker 找不到实现；
  - 更新 / 卸载按钮样式与禁用态统一。

### Fixed
- 修复 Codex 生图发送图片时引用了未定义 `reply_msg`，导致 Telegram 发送阶段报 `NameError` 的问题。
- 修复 Codex 生图图片发送回归测试缺口：新增测试覆盖显式 `reply_to_id`、PNG 文件名和后缀。
- 修复前端 API 错误解析不识别 FastAPI `{ detail: { code, message } }` 的问题，现在远程插件安装失败能显示后端给出的明确原因。

### Verification
- 后端定向测试通过：`backend/.venv/bin/python -m pytest backend/app/tests/test_plugin_security_regression.py backend/app/tests/test_codex_image_errors.py`。
- 后端静态检查通过：`backend/.venv/bin/python -m ruff check backend/app/settings.py backend/app/services/remote_plugin_service.py backend/app/services/plugin_install_service.py backend/app/services/plugin_repo_service.py backend/app/worker/plugins/loader.py backend/app/tests/test_plugin_security_regression.py`。
- 前端类型检查通过：`pnpm -C frontend exec tsc -b --noEmit`。
- 前端生产构建通过：`pnpm -C frontend build`。

---

## [0.10.1] — 2026-05-11 · feature · 插件仓库系统 + 调度平台化 + Codex 生图增强

### Added
- **插件仓库系统**：新增后端 `plugin_repo` 表与完整 CRUD API，支持添加 Git 仓库、浏览仓库内插件、选择性安装。
  - 仓库地址持久化存储在数据库，跟随系统配置，不依赖浏览器 localStorage。
  - 自动扫描仓库根目录或一级子目录的 `plugin.json`，识别单插件/多插件仓库。
  - 前端 Extensions 页面「插件仓库」卡片：添加仓库 → 展开浏览 → 点击安装，已安装插件标记状态。
- **调度平台化**：定时任务从普通插件语义收口为平台基础能力。
  - 新增 `scheduler_runtime.py` 平台调度器模块，提供 `PlatformScheduler` 与 `SchedulerFacade`。
  - `PluginContext` 新增 `ctx.scheduler` capability，插件可注册/注销定时任务。
  - 新增系统级路由 `/scheduler`（调度中心）与侧边栏「调度」一级菜单，支持账号选择器。
  - 插件中心与账号详情中的「基础能力→配置」统一跳转调度中心，形成单真相双入口。
- Codex 生图插件新增 `image_model` 配置项，支持选择底层图片模型（auto / gpt-image-2 / gpt-image-1.5 / gpt-image-1 / gpt-image-1-mini）。
- Codex 生图消息模板新增 `{image_model}` 占位符，主模型与图片模型分开展示。
- Codex 生图 `aspect_ratio` 和 `image_size` 新增 `from_reference` 选项，回复图片生成时自动使用原图比例和分辨率。
- 前端 Codex 配置页同步新增图片模型下拉选择器。
- `system_health` 进程采样改为优先 psutil，失败回退 ps，提升 Oracle/Linux 环境稳定性。

### Changed
- 调度器兼容壳（builtin scheduler plugin）移除自建 tick loop，只保留兼容注册与方法表。
- Codex 生图错误提示全面白话化：
  - 401/403 鉴权失败：区分 Token 过期、无效、权限不足，附带修复建议。
  - 429 限流：说明原因和等待策略。
  - 额度用完：显示计划类型和恢复时间。
  - 内容审核拦截、超时、余额不足等常见错误均有 ❌ + 💡 结构化提示。
- Codex 生图轮询状态展示优化：显示 API 返回的实际状态（in_progress / queued 等），不再只显示"正在等待"。
- 参考图比例匹配容差从 ±0.02 放宽到 ±0.05，减少因图片压缩导致的比例误判。
- PWA 移动端 Tabs 自动换行居中，Card/Content 增加 min-w-0 防止长内容撑破布局。
- 插件开发指南增加平台调度器说明、`ctx.scheduler` 用法与迁移约束。

### Fixed
- 修复 `from_reference` 在无参考图时传给 API 的 "from_reference" 字符串导致请求失败的问题，现在自动 fallback 到默认值。
- 修复 `_poll_codex_response` 吞掉 HTTP 4xx/5xx 错误的问题，现在正确提取并返回错误信息。
- 修复轮询时 `status=failed` 等终态被忽略的问题，现在立即报错而非空等超时。
- 修复 Extensions 页面缺少 `X` icon import 导致 CI 构建失败。
- 修复 `system_health` 在部分 Linux 环境下 CPU/内存读取失败的问题。

### Verification
- 后端测试通过：`pytest backend/app/tests`（492 passed, 2 skipped）。
- 前端类型检查通过：`pnpm -C frontend exec tsc -b --noEmit`。

---

## [0.10.0] — 2026-05-10 · feature · 账号 Bot 联动 + 日志可观测性 + Sudo 收紧

### Added
- 新增账号绑定 Bot 联动系统（每账号独立 Bot Token、独立授权、独立运行）：
  - 新增数据模型：`account_bot`、`account_bot_user`；
  - 新增迁移：`backend/alembic/versions/0021_account_bot.py`；
  - 新增 API：`/api/accounts/{aid}/bot`、`/bot/test`、`/bot/restart-runtime`、`/bot/users`；
  - 新增运行时：`account_bot_runtime` polling manager，应用启动自动拉起、关闭自动停止；
  - 新增前端账号详情页「Bot 联动」Tab，支持配置、测试、授权用户与角色管理。
- 日志系统新增最小落库级别配置 `runtime_log_min_level`（`debug/info/warn/error`），支持在系统设置中切换排障与日常模式。
- 日志中心新增“插件日志”独立视图与 `plugin_key` 过滤，支持按插件维度快速定位故障。
- 新增 `TelegramHtmlPreview` 组件，用于消息模板 HTML 预览。

### Changed
- Sudo 权限模型改为默认拒绝：`allowed_chat_ids` / `allowed_commands` 空值不再等于“全部允许”，显式全部需设置 `*`（前端通过 `allow_all_*` 开关表达）。
- Sudo 管理页合并“允许命令”与“当前可用命令”选择体验，支持点击卡片启停，减少手填。
- 运行日志保留策略在原有“保留天数/长度截断”基础上增加“最小级别”控制，便于生产环境降噪。
- 24 点插件完成结构化重构：配置解析、事件适配、判题、发奖、超时、公告拆分；同 chat 加锁避免并发双发奖。
- 24 点插件 manifest 与运行时配置统一为 `command + timeout`，移除历史错位字段。
- 24 点插件对“是否本人消息”判定改为优先 `sender_id == self_tg_user_id`，减少部分群组中 `outgoing` 标记不稳定导致的误跳过。
- Codex 生图与 24 点配置页数值输入交互优化：允许先清空再输入，保存时再校验，避免输入框强制回弹。

### Fixed
- 修复插件日志与系统日志混叠：`source=event` 不再包含插件日志，插件日志独立归类为 `source=plugin`。
- 修复 Codex 生图网络中断错误提示不友好问题，常见 chunked stream 断连改为可读提示并继续脱敏。
- 修复 Sudo incoming 拒绝提示在群聊噪声过高的问题：对未配置/未授权场景默认静默。
- 修复配置页部分数值输入无法删除为空再重输的问题（24 点、Codex 生图）。

### Verification
- 后端测试通过：`backend/app/tests/test_game24_plugin.py`、`backend/app/tests/test_supervisor_reliable_consumer.py`、`backend/app/tests/test_plugin_loader.py`、`backend/app/tests/test_sudo.py`。
- 前端类型检查通过：`pnpm -C frontend exec tsc -b --noEmit`。

---


## [0.9.8] — 2026-05-10 · feature · Codex 生图体验 + 插件模式与调度器收口

### Added
- Codex 图片生成插件新增完整账号级配置：
  - `command` 触发指令名，支持中文命令；
  - `message_template` 消息模板，统一用于生成中状态编辑与最终图片 caption；
  - `image_size`、`aspect_ratio`、`image_format` 默认图片参数；
  - `status_interval_seconds`、`delete_command_message`、`show_revised_prompt`、`reasoning_effort`、`custom_instructions` 等高级行为配置。
- Codex 生图命令支持临时覆盖图片参数：`--比例/--ratio`、`--size/--resolution`、`--format/--格式`，例如 `,cximg --比例 4:3 --size 1536x1024 --format jpeg 云海里的城市`。
- Codex 图片生成配置页新增消息模板编辑器、占位符快捷插入、实时预览、分辨率/比例/格式下拉配置。
- 自定义命令 AI 消息模板配置新增实时预览，便于保存前确认最终 Telegram 展示效果。
- 前端新增 `plugin-modes.ts` 统一插件模式分类工具；后续插件可通过 `config_schema["x-ui-mode"]` 自动进入规则驱动、单配置对象、Schema 弹窗或平台内置分组。

### Fixed
- 修复 Codex 生图发送裸 `bytes` 导致 Telegram 中显示 `unnamed` 且没有图片后缀的问题：现在使用带 `.name` 的 `BytesIO` 上传，并根据图片 magic bytes / 配置格式生成 `.png`、`.jpg` 或 `.webp` 文件名。
- 修复 Codex 生图生成中状态消息没有按 HTML 解析，导致 `<b>...</b>` 标签原样展示的问题；模板 HTML 解析失败时会退回纯文本，避免把标签直接发给用户。
- 优化 Codex / LLM 错误提示：额度耗尽、限流、鉴权失败、网络异常等错误改成人可读提示，并继续脱敏 token、路径和密钥片段。
- 修复自定义触发指令名变更后不能实时生效的问题：命令型插件通过 `command_config_keys` 触发 worker 重新注册命令。
- 命令解析从 `\w+` 放宽为非空白 token，支持中文插件命令。

### Changed
- 定时任务从普通插件 tick loop 迁移为 worker runtime 的平台基础能力：只要账号 worker 运行，调度器就按规则执行；插件壳仅保留兼容入口和调度算法复用。
- 账号详情页与插件中心的插件列表改为按模式分组展示：规则驱动、单配置对象、Schema 弹窗、基础能力；表格列宽统一，配置按钮不再依赖启用状态。
- Codex Image 与 24 点游戏配置页调整为“当前状态 + 使用说明”置顶，命令示例统一读取系统命令前缀和当前配置。
- 内置插件 manifest 补充 `x-ui-mode` 元数据：`forward` / `autorepeat` 为规则驱动，`game24` / `codex_image` 为单配置对象，`scheduler` 为平台内置。
- 插件开发指南更新前端配置页规则，补充模式自动分类、命令型插件热重载要求、Codex Image / 24 点页面布局规范和平台调度器说明。
- 后端 ruff 静态检查收口：整理 import、类型注解、异常链、未使用变量和测试静态问题。

### Verification
- 后端全量测试通过：`469 passed, 2 skipped`。
- 后端静态检查通过：`ruff check app`。
- 前端生产构建通过：`pnpm -C frontend build`。

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
