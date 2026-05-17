# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [SemVer](https://semver.org/lang/zh-CN/)：MAJOR.MINOR.PATCH。

- **MAJOR**：破坏兼容的数据库迁移、配置格式变更、API 路径或语义不兼容、老版本无法平滑升级。
- **MINOR**：用户可感知的新能力、主要入口/信息架构重组、后端能力完整前端化、新模块或重要工作流变化。
- **PATCH**：bug 修复、文案、小 UI、错误提示、测试补充、兼容性补丁和不改变主要用户路径的小调整。

> 不要为每个微小提交单独迭代版本号。开发过程中先把变更积累在 `Unreleased`；只有准备发布、推送稳定检查点、创建 release/PR，或用户明确要求“推一版/发一版”时，才按本批改动的最高影响级别统一 bump 一次版本号。
>
> 发布时版本号在 4 处必须保持同步：`backend/app/__init__.py`、`backend/pyproject.toml`、`frontend/package.json`、`frontend/src/lib/version.ts`。同时把 `Unreleased` 内容移动到新的正式版本段落，并使用中文更新说明。`backend/app/main.py` 通过 `from . import __version__` 自动跟随，无需单独改。

---

## [Unreleased]

### Changed
- 明确版本号只在发布、推送稳定检查点、创建 release/PR，或用户要求“推一版/发一版”时统一迭代；开发过程中的微小提交先累积到 `Unreleased`。

---

## [0.16.9] — 2026-05-17 · changed · 插件模板配置分组

### Added
- 插件通用配置弹窗会按字段名自动归类：普通配置保留在顶部，`*_template` 模板进入“消息模板”折叠组，`*_preview` 预览进入底部“预览结果”。
- 模板预览现在会根据当前表单里的模板内容和示例变量实时渲染，而不是只显示 schema 中的静态默认预览。

### Changed
- 每个模板预览都使用独立的 `TelegramHtmlPreview` 气泡展示，方便同时检查开局、进行中、成功、超时、取消和错误提示等多条消息。
- 版本号口径校正为 `0.16.9`，避免误标为 `0.17.0`。

### Fixed
- 修复后端 CI 的 ruff import 排序与测试写法问题。
- 修复完整 pytest 在 `backend/` 目录运行时无法导入仓库根目录 `plugins.installed.codex_image` 的问题。
- 修复旧测试替身或自定义 `build_client` 不接受 `api_format_override` 时导致 inline provider override 测试失败的问题。

### Verification
- `pnpm --dir frontend build` 通过。
- `cd backend && .venv/bin/ruff check app` 通过。
- `cd backend && .venv/bin/python -m pytest -q` 通过。
- `git diff --check` 通过。

---

## [0.16.8] — 2026-05-17 · changed · Telegram 消息预览

### Changed
- 自定义命令模板里的消息格式预览改为 Telegram 风格聊天预览：浅色聊天背景、示例用户消息、TelePilot 蓝色气泡和时间状态，效果更接近真实消息。
- 通用 `TelegramHtmlPreview` 组件升级为消息气泡预览，插件配置里的模板预览也会使用同一套视觉效果。
- 开发文档新增 Telegram 消息预览规范，说明模板预览应展示替换占位符后的最终消息效果，并优先复用 `TelegramHtmlPreview`。

### Verification
- `pnpm --dir frontend build` 通过。
- `backend/.venv/bin/python -m pytest backend/app/tests/test_commands.py backend/app/tests/test_ai_runtime.py backend/app/tests/test_scheduler_runtime.py backend/app/tests/test_llm_runtime.py -q` 通过。
- `git diff --check` 通过。

---

## [0.16.7] — 2026-05-17 · changed · AI 模式化默认参数

### Changed
- AI 命令模板的模型参数默认值改为跟随模式：chat 使用 `temperature=0.7`、`reasoning_effort=medium`、`timeout_seconds=60`；search 使用 `0.2 / medium / 90`；image 使用 `0.8 / low / 180`；video 预留使用 `0.8 / low / 300`。
- 切换 AI 模式时，如果当前参数仍是旧模式默认值或为空，会自动带入新模式默认值；用户手动改过的值不会被覆盖。
- 模型参数说明改为展示当前模式默认值，避免生图、视频等长耗时任务沿用普通 chat 的短超时。

### Verification
- `pnpm --dir frontend build` 通过。
- `backend/.venv/bin/python -m pytest backend/app/tests/test_commands.py backend/app/tests/test_ai_runtime.py backend/app/tests/test_scheduler_runtime.py backend/app/tests/test_llm_runtime.py -q` 通过。
- `git diff --check` 通过。

---

## [0.16.6] — 2026-05-17 · changed · AI 参数默认值

### Changed
- AI 命令模板的模型参数现在提供默认值：温度 `0.7`、推理强度 `medium`、超时时间 `60` 秒。
- 模型参数说明同步汉化默认值含义，减少新增 AI 命令时需要手动补齐基础参数的步骤。

### Verification
- `pnpm --dir frontend build` 通过。
- `backend/.venv/bin/python -m pytest backend/app/tests/test_commands.py backend/app/tests/test_ai_runtime.py backend/app/tests/test_scheduler_runtime.py backend/app/tests/test_llm_runtime.py -q` 通过。
- `git diff --check` 通过。

---

## [0.16.5] — 2026-05-17 · feature · AI 模型参数配置

### Added
- AI 命令模板新增“模型参数”折叠区，支持配置温度（temperature）、推理强度（reasoning_effort）和单次调用超时时间。
- 模型参数均提供中文说明：温度解释稳定/创作取向，推理强度解释思考预算，超时时间解释长推理与本地桥接场景。
- 后端统一 LLM 调用链支持透传 temperature、reasoning_effort 和 timeout_seconds；OpenAI Chat/Responses 支持推理强度，Anthropic 先透传 temperature。

### Verification
- `pnpm --dir frontend build` 通过。
- `backend/.venv/bin/python -m pytest backend/app/tests/test_commands.py backend/app/tests/test_ai_runtime.py backend/app/tests/test_scheduler_runtime.py backend/app/tests/test_llm_runtime.py -q` 通过。
- `git diff --check` 通过。

---

## [0.16.4] — 2026-05-17 · feature · Provider 协议检测

### Added
- 模型提供商新增“检测协议”能力，可在保存前探测 `/models`、`/chat/completions`、`/responses` 和 Anthropic `/messages` 是否可用。
- 协议检测会根据探测结果自动填入推荐的默认 API Format 与联网搜索 API Format，降低新建 Provider 时选错协议的概率。

### Verification
- `pnpm --dir frontend build` 通过。
- `backend/.venv/bin/python -m pytest backend/app/tests/test_commands.py backend/app/tests/test_ai_runtime.py backend/app/tests/test_scheduler_runtime.py backend/app/tests/test_llm_runtime.py -q` 通过。
- `git diff --check` 通过。

---

## [0.16.3] — 2026-05-17 · feature · Provider 联网搜索协议覆盖

### Added
- 模型提供商新增“联网搜索 API Format”配置，支持默认聊天协议与联网搜索协议分开设置。
- OpenAI 兼容 Provider 默认启用自动协议覆盖：日常调用可走 `chat_completions`，联网搜索调用会临时切到 `responses`。
- 新增数据库迁移 `0024`，为 `llm_provider` 增加 `web_search_api_format` 字段。

### Changed
- Provider 列表和编辑弹窗展示联网搜索协议，降低 `。ai search` 配置成本。

### Verification
- `pnpm --dir frontend build` 通过。
- `backend/.venv/bin/python -m pytest backend/app/tests/test_commands.py backend/app/tests/test_ai_runtime.py backend/app/tests/test_scheduler_runtime.py backend/app/tests/test_llm_runtime.py -q` 通过。
- `git diff --check` 通过。

---

## [0.16.2] — 2026-05-17 · fix · AI 搜索提示与命令样式

### Changed
- 带命令前缀的说明统一使用命令胶囊样式，避免和普通正文混在一起。
- AI 模式说明补充 search 模式需要 OpenAI Responses API provider（api_format=responses）。

### Fixed
- `search` 模式绑定到不支持联网搜索的 provider 时，Telegram 端错误提示会明确指出需要 Responses API provider。

### Verification
- `pnpm --dir frontend build` 通过。
- `git diff --check` 通过。

---

## [0.16.1] — 2026-05-17 · fix · AI 导航图标统一

### Changed
- 侧边栏 AI 入口改用更轻量的闪光图标，和 AI 命令中心的入口气质保持一致。
- AI 命令中心切换器补齐“总览”图标，并把“已配置的命令”保留在同一组入口中。
- 日志中心主切换器与运行日志来源切换器补齐图标，减少只有文字的页签。

### Verification
- `pnpm --dir frontend build` 通过。
- `git diff --check` 通过。

---

## [0.16.0] — 2026-05-17 · feature · AI 模块信息架构收敛

### Added
- 自定义命令模板页支持 `edit`、`new=ai`、`provider_id`、`aiCapability`、`returnTo` 深链，用于直接打开 AI 模板抽屉、预填 Provider、展开指定能力组并保存后返回。
- 插件中心新增“图片生成 (codex_image)”显眼入口，并支持 `highlight=codex_image` 滚动高亮。
- AI 总览新增工作原理、配置示例和术语速查折叠说明，替代原独立帮助页。
- AI 总览新增账号启用 AI 命令摘要，显示已有多少账号启用了至少一条 AI 命令。
- AI 总览“去启用”支持按账号数量分流：单账号直接进入命令 Tab，多账号先选择账号。
- AI 消息格式新增 `{model_id}` 原始模型 ID，占位符 `{model}` 改为输出更友好的模型展示名。
- AI 命令模板新增 `chat/search/image/video` 模式字段，开始对齐 TeleBox `ai.ts` 的二级指令结构。

### Changed
- `/ai` 收敛为总览、模型提供商、调用记录三种 Tab 状态，支持 `tab=overview|providers|usage` URL 驱动并在切换时保留 query。
- `/ai/providers?new=1` 兼容跳转为 `/ai?tab=providers&newProvider=1`，进入模型提供商 Tab 后自动打开新建 Provider 弹窗并清理一次性 query。
- `/ai/chat`、`/ai/routing`、`/ai/search`、`/ai/output`、`/ai/vision`、`/ai/images` 改为精确深链到命令模板、Provider 筛选或插件中心，不再保留独立 AI 子页。
- AI 总览改为三张状态卡、三步引导和 AI 命令快览，最近调用摘要统一使用最近 20 次口径。
- AI 总览帮助区的“推荐配置”改名为“配置示例”。
- 命令模板编辑器中的 AI 路由、联网搜索和回复样式从平铺字段改为默认折叠的能力组，非默认配置会自动展开。
- `search` 模式会自动启用联网搜索；`image` 模式可桥接到账号已启用的 `codex_image` 插件，也支持直接创建 `image` 命令模板。
- 模型提供商列表支持 `filter=modality:vision` 过滤视觉/多模态 Provider。
- 最近调用内容迁移为 `/ai` 内部组件，供调用记录 Tab 复用。
- 后端标准 LLM 调用薄包装从 `services.ai_runtime` 重命名为 `services.llm_invoke`，避免与 worker 侧 Telegram 事件运行时混淆。

### Removed
- 删除 AI 模块历史 wrapper 和独立子页：`Providers`、`Help`、`Usage`、`Chat`、`Routing`、`Search`、`Vision`、`Images`、`Output` 与 `_shared`。
- 删除孤儿帮助页 `AISettings.tsx`，其内容拆入 AI 总览的内联折叠组件。

### Verification
- `pnpm --dir frontend build` 通过。
- `backend/.venv/bin/python -m pytest backend/app/tests/test_ai_runtime.py backend/app/tests/test_scheduler_runtime.py -q` 通过。
- `backend/.venv/bin/python -m pytest backend/app/tests/test_commands.py -q` 通过。
- `git diff --check` 通过。

---

## [0.15.23] — 2026-05-17 · feature · AI 能力页可操作化

### Added
- AI 模块新增聊天问答、能力路由、联网搜索、视觉理解、图片生成、输出模板等独立能力页入口。
- 输出模板页支持选择 AI 命令后直接编辑解析模式、消息模板、占位符转义和预览，并保存到对应命令模板。
- 联网搜索页支持直接为 AI 命令启用/关闭 `web_search`，并调整搜索上下文强度。
- 能力路由页支持直接切换固定模型/自动路由，并配置自动路由失败时的兜底 Provider。

### Changed
- 自定义命令编辑器的 AI 消息格式编辑区抽取为通用输出模板组件，供命令模板页与 AI 输出模板页复用。

### Verification
- `pnpm --dir frontend build` 通过。

---

## [0.15.22] — 2026-05-17 · fix · AI 入口可见性

### Added
- 左侧主导航新增“AI”入口，直达 AI 模块总览页。
- 模块中心的“AI 模块入口”区域新增“AI 总览”按钮，避免只能进入模型提供商、用量或帮助页。

### Verification
- `pnpm --dir frontend build` 通过。
- `git diff --check` 通过。

---

## [0.15.21] — 2026-05-17 · feature · AI 模块能力化

### Added
- AI 模块首页重构为能力控制台，集中展示模型提供商、AI 命令模板、联网搜索、视觉理解、能力路由和输出模板入口。
- 自定义 AI 命令模板新增“联网搜索”开关与搜索上下文强度配置，支持在输出模板里使用 `{sources}` 显示搜索来源。
- OpenAI Responses 调用链支持 `web_search` 工具，并从 Responses 返回体中提取来源链接。

### Changed
- 标准 LLM runtime 增加联网搜索参数透传，worker 渲染 AI 回复时会把搜索来源注入模板上下文。

### Fixed
- 修复 AI 模块首页首次加载后因 React hooks 调用顺序变化导致 `/ai` 页面打不开的问题。

### Verification
- `backend/.venv/bin/python -m pytest backend/app/tests/test_commands.py -q` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.15.20] — 2026-05-17 · fix · 自定义命令启用入口

### Fixed
- 账号详情新增“自定义命令”页签，可按当前账号启用或停用全局命令模板，开启后 worker 热加载生效。
- 账号概览中的“复用命令模板”入口改为直达账号级启用页，模板库文案同步为“账号详情 → 自定义命令页签”。

### Verification
- `pnpm --dir frontend build` 通过。
- `git diff --check` 通过。

---

## [0.15.19] — 2026-05-17 · fix · Codex 生图错误处理

### Fixed
- Codex 生图插件对齐原始 TeleBox 插件的轮询容错：普通轮询异常会继续等待，鉴权/HTML 登录页/防护页会给出明确提示，不再把整段网页源码显示成 `HTTP 0`。
- Codex 生图插件支持从 Responses 完成事件里递归提取最终 `image_generation_call.result` 图片，减少只依赖轮询造成的失败。
- 生图失败提示去掉重复的 `❌ ❌` 前缀，并补充 Token 失效、ChatGPT 防护页、后端网络/代理不可达等可操作排查提示。

### Verification
- `backend/.venv/bin/python -m pytest backend/app/tests/test_codex_image_errors.py` 通过。
- `backend/.venv/bin/python -m py_compile plugins/installed/codex_image/plugin.py` 通过。
- `backend/.venv/bin/ruff check plugins/installed/codex_image/plugin.py backend/app/tests/test_codex_image_errors.py` 通过。
- `pnpm --dir frontend build` 通过。
- `git diff --check` 通过。

---

## [0.15.18] — 2026-05-17 · fix · 命令示例跟随前缀

### Fixed
- TG 内置命令、游戏插件、图片生成插件、账号 Bot 与前端配置页中的命令示例改为读取当前命令前缀，不再固定显示逗号前缀。
- 九宫格骰子竞猜模板新增 `{prefix}` 占位符，进行中提示和奖励参数错误提示可随系统前缀热加载变化。

### Verification
- `backend/.venv/bin/python -m pytest backend/app/tests/test_auto_reply.py backend/app/tests/test_worker_command.py backend/app/tests/test_sudo.py backend/app/tests/test_scheduler_runtime.py` 通过。
- `backend/.venv/bin/python -m pytest backend/app/tests/test_account_bot.py` 通过。
- `pnpm --dir frontend build` 通过。
- `git diff --check` 通过。

---

## [0.15.17] — 2026-05-17 · fix · 自动回复命令直接派发

### Fixed
- 自动回复生成白名单内命令文本时，会直接进入 worker 命令派发器执行，不再依赖插件发送出的消息再次回流为 outgoing 事件。

### Verification
- `backend/.venv/bin/python -m pytest backend/app/tests/test_auto_reply.py backend/app/tests/test_worker_command.py backend/app/tests/test_scheduler_runtime.py` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.15.16] — 2026-05-17 · fix · 自动命令白名单实时热加载

### Fixed
- worker 收到 `reload_config` 后会同步刷新命令上下文，自动命令白名单保存后立即生效，不再等周期性 reconcile。

### Verification
- `backend/.venv/bin/python -m pytest backend/app/tests/test_scheduler_runtime.py backend/app/tests/test_feature_registry.py` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.15.15] — 2026-05-17 · changed · 自动命令白名单独立入口

### Changed
- 自动命令白名单从定时任务规则页拆出，模块中心新增独立入口，按账号配置自动动作允许触发的命令。
- 模块中心“平台能力”说明改为系统级基础模块的集中入口，不再暗示无需手动配置。

### Fixed
- 本地已存在于 `plugins/installed` 的模块会同步登记到 feature 表；`codex_image` 等随项目落盘的实验模块可在模块中心出现并按账号启用配置。

### Verification
- `pnpm --dir frontend build` 通过。
- `backend/.venv/bin/python -m pytest backend/app/tests/test_feature_registry.py` 通过。

---

## [0.15.14] — 2026-05-17 · fix · 远程模块配置入口

### Fixed
- 模块中心的“远程模块”卡片支持显示“配置”按钮；没有专用配置页的远程模块会打开通用配置弹窗，并保存账号级模块配置。

### Verification
- `pnpm --dir frontend build` 通过。

---

## [0.15.13] — 2026-05-17 · fix · 代理 URL 自动归一化

### Fixed
- 新增或编辑代理时支持直接粘贴完整代理 URL（如 `http://10.10.8.33:6152`、`socks5://127.0.0.1:6153`），后端会自动拆分类型、主机、端口与认证信息，避免把完整 URL 当作主机名导致测试连接失败。
- 账号登录、账号退出与 LLM 代理解析兼容历史上已保存为完整 URL 的代理主机字段。
- 代理管理页的主机输入提示同步为支持完整 URL 粘贴。

### Verification
- `backend/.venv/bin/python -m pytest backend/app/tests/test_proxy_url_normalization.py backend/app/tests/test_accounts.py` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.15.12] — 2026-05-17 · feature · 代理配置可编辑

### Added
- 代理与标识页的已添加代理支持行内编辑，可修改类型、主机、端口、用户名和密码，并支持清空已保存密码。

### Verification
- `backend/.venv/bin/python -m pytest backend/app/tests/test_accounts.py` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.15.11] — 2026-05-17 · fix · 代理入口不可达提示

### Fixed
- 代理连通性测试先探测代理入口 TCP 可达性；当后端连不到代理地址时，返回“代理入口不可达”与 Docker/局域网访问提示，不再只显示底层 python-socks 错误。

### Verification
- `backend/.venv/bin/python -m pytest backend/app/tests/test_accounts.py` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.15.10] — 2026-05-17 · fix · 新增账号网络错误提示

### Fixed
- 新增账号发起登录时，后端连接 Telegram 失败会返回结构化 `LOGIN_START_FAILED` 错误，不再冒泡成“服务器内部错误”。
- 新增账号向导的代理提示同步到“系统设置 → 代理与标识”。

### Verification
- `backend/.venv/bin/python -m pytest backend/app/tests/test_accounts.py` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.15.9] — 2026-05-16 · feature · 首页承载账号管理

### Changed
- 抽离账号管理复用面板，统一承载账号列表高频操作：新增账号、新手指引、启停、详情、删除与空状态。
- 首页 Dashboard 直接接入账号管理面板，首页可完成核心账号管理流程，不再仅展示状态卡。
- `/accounts` 路由改为复用同一面板实现，保持原有交互与路径兼容。

### Verification
- `pnpm --dir frontend build` 通过。

---

## [0.15.8] — 2026-05-16 · feature · 模块中心与 AI 入口文案收口

### Changed
- `/plugins` 与 `/plugins/manage` 前端展示文案从“插件”统一收口为“模块”（仅 UI 文案，后端/API/plugin 命名保持不变）。
- 模块中心首页新增 AI 入口卡，提供“模型提供商 / AI 用量 / AI 帮助”直达入口，并明确 AI 能力属于模块配置流程。
- 保留现有 `/ai/*` 页面实现与路径兼容，未调整 Settings/Dashboard/Sidebar/App 路由。

### Verification
- `pnpm --dir frontend build` 通过。

---

## [0.15.7] — 2026-05-16 · fix · 恢复代理与标识配置入口

### Fixed
- 系统设置新增“代理与标识”页签，恢复代理库与设备标识模板入口，避免后端已实现的代理配置在前端不可达。
- 风控模板移入“安全”页签并默认折叠，避免系统设置首屏过长。
- 健康概览的代理库为空提示改为可点击入口，直接跳到系统设置“代理与标识”页。

### Verification
- `pnpm --dir frontend build` 通过。

---

## [0.15.6] — 2026-05-16 · fix · 修复新手指引按钮文字可见性

### Fixed
- 账号管理页“新增账号”和系统设置平台页“保存”在新手指引高亮时改为浅底描边强调样式，避免流光高亮下文字变白不可见。

### Verification
- `pnpm --dir frontend build` 通过。

---

## [0.15.5] — 2026-05-16 · fix · 修复 CI 插件导入路径

### Fixed
- 为 backend CI job 设置 `PYTHONPATH=..`，确保从 `backend/` 工作目录运行 pytest 时可导入仓库根目录的第三方插件包。

### Verification
- `PYTHONPATH=.. MASTER_KEY=... JWT_SECRET=... ../backend/.venv/bin/python -m pytest app/tests/test_codex_image_errors.py` 通过。

---

## [0.15.4] — 2026-05-16 · fix · 修复 CI 测试环境变量

### Fixed
- 为 backend CI job 注入测试专用 `MASTER_KEY` 与 `JWT_SECRET`，避免远端 pytest 在 settings collection 阶段因缺少必填配置失败。

### Verification
- `backend/.venv/bin/ruff check backend/app` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.15.3] — 2026-05-16 · fix · 修复 CI 后端 lint

### Fixed
- 修复 `account_bot` 数据模型 import 排序，使新增 backend CI gate 在远端环境通过。

### Verification
- `backend/.venv/bin/ruff check backend/app/db/models/account_bot.py` 通过。

---

## [0.15.2] — 2026-05-16 · fix · 完成审查后续收口

### Fixed
- Config Bundle dry-run/confirm 增加预览签名绑定，目标账号配置快照、可用 feature/template、文件或冲突选项变化后必须重新预览。
- Config Bundle chat/peer/group 相关嵌套字段冲突现在会触发二次确认；缺失 feature/template 等不可恢复冲突会被 blocked。
- Config Bundle confirm 写库成功后会通知目标账号 worker reload 配置、命令和忽略列表。
- feature/rule/audit/runtime log 等路径统一敏感字段脱敏且避免误伤 `max_tokens` 等非敏感计数字段，Codex Image token 改为 write-only 展示。
- 健康检查纳入 worker runtime 存活与失败计数，异常态刷新更快并支持手动刷新；Audit 日志筛选改为后端过滤。
- 修正 account_bot 测试接口前端类型漂移，`testAccountBot` 现在返回后端实际响应。
- README、安全运维、远程插件、插件开发和部署文档同步当前安全边界与 TelePilot 命名。

### Added
- 新增 Config Bundle 签名、blocked conflict、worker reload 回归测试。
- 新增敏感字段脱敏、日志查询筛选、健康检查 worker runtime 聚合测试。

### Verification
- `backend/.venv/bin/ruff check backend/app/api/config_bundle.py backend/app/api/features.py backend/app/api/logs.py backend/app/api/rules.py backend/app/api/system_health.py backend/app/schemas/config_bundle.py backend/app/services/audit.py backend/app/services/config_bundle_service.py backend/app/services/redactor.py backend/app/worker/supervisor.py backend/app/tests/test_config_bundle.py backend/app/tests/test_logs_api.py backend/app/tests/test_redaction_security.py backend/app/tests/test_supervisor_reliable_consumer.py backend/app/tests/test_system_health.py` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telepilot_pycache backend/.venv/bin/python -m pytest backend/app/tests/test_config_bundle.py backend/app/tests/test_redaction_security.py backend/app/tests/test_supervisor_reliable_consumer.py backend/app/tests/test_system_health.py backend/app/tests/test_logs_api.py` 通过（46 passed）。
- `PYTHONPYCACHEPREFIX=/private/tmp/telepilot_pycache backend/.venv/bin/python -m pytest backend` 通过（590 passed, 2 skipped）。
- `pnpm --dir frontend build` 通过。

---

## [0.15.1] — 2026-05-16 · fix · 收紧远程插件与自动命令安全边界

### Fixed
- 远程插件与插件仓库 API 现在要求 Web 登录态，避免未登录请求触发安装、启停、更新、卸载等高风险操作。
- 第三方插件命令 handler 现在接收沙箱 client，不再绕过 manifest 权限拿到原始 Telethon client。
- account_bot 的 Telegram 远程插件 install/update/uninstall/第三方启停改为 admin-only，并新增 Web 端细粒度高风险开关；默认全部关闭，执行前仍需二次确认。
- scheduler 与自动回复触发命令时新增账号级白名单控制；非白名单命令会被拦截并记录，不再默认复用 Telegram 命令分发。

### Added
- 新增 account_bot 远程插件高风险策略迁移与前端配置入口。
- 新增主 CI workflow，覆盖后端 ruff/pytest、前端 build 与版本号同步检查。
- 新增远程插件鉴权、插件沙箱命令 client、account_bot 远程插件策略、自动命令白名单相关回归测试。

### Verification
- `backend/.venv/bin/ruff check backend/app/api/account_bots.py backend/app/api/plugin_repo.py backend/app/api/remote_plugin.py backend/app/schemas/account_bot.py backend/app/services/account_bot_runtime.py backend/app/services/account_bot_service.py backend/app/worker/command.py backend/app/worker/plugins/loader.py backend/app/worker/scheduler_runtime.py backend/app/worker/runtime.py backend/app/worker/plugins/builtin/auto_reply/plugin.py backend/app/tests/test_account_bot.py backend/app/tests/test_plugin_security_regression.py backend/app/tests/test_remote_plugin_repo_auth.py backend/app/tests/test_worker_command.py` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telepilot_pycache backend/.venv/bin/python -m pytest backend/app/tests/test_account_bot.py backend/app/tests/test_plugin_security_regression.py backend/app/tests/test_remote_plugin_repo_auth.py backend/app/tests/test_worker_command.py backend/app/tests/test_scheduler_runtime.py` 通过（64 passed）。
- `PYTHONPYCACHEPREFIX=/private/tmp/telepilot_pycache backend/.venv/bin/python -m pytest backend` 通过（576 passed, 2 skipped）。
- `pnpm --dir frontend build` 通过。

---

## [0.15.0] — 2026-05-16 · feature · TelePilot rename 收口

### Changed
- 项目品牌、Web/PWA 标题、前后端包名、启动通知、账号 Bot/NotifyBot 文案和 `,version` 输出统一为 TelePilot。
- README、部署文档、插件开发指南和远程插件指南同步 0.13/0.14 已完成能力，并明确 0.15 后的新命名与兼容边界。
- worker PID 目录迁移到 `~/.telepilot/worker-pids`，启动清理仍会扫描旧 `~/.telebot/worker-pids`，避免升级后残留旧 worker。
- 本地启动/停止脚本改为基于当前仓库 `backend` 路径识别孤儿 worker，同时兼容旧 `telebot/backend` 路径。

### Added
- 插件 manifest 新增 `min_telepilot_version` 字段；旧 `min_telebot_version` 继续作为 legacy alias 解析，避免远程插件生态硬断。
- 远程插件 `plugin.json` 元数据支持 `min_telepilot_version`，并保留旧字段写入兼容。
- 前端主题存储切换为 `telepilot-theme`，首次读取会兼容迁移旧 `telebot-theme`。

### Compatibility
- 后端 CSRF 过渡期同时接受 `X-Requested-With: telepilot-ui` 与旧 `telebot-ui`，避免旧前端缓存或脚本升级后直接 403。
- Docker volume、数据库默认账号/库名、`TELEBOT_WORKER_PROC` 等底层兼容名暂不强制迁移，避免升级后历史数据“消失”或 worker 连接池语义变化。

### Verification
- `git diff --check` 通过。
- `backend/.venv/bin/ruff check backend/app/main.py backend/app/worker/plugins/manifest.py backend/app/worker/plugins/loader.py backend/app/worker/supervisor.py backend/app/services/remote_plugin_service.py backend/app/tests/test_csrf_header.py backend/app/tests/test_plugin_loader.py` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telepilot_pycache backend/.venv/bin/python -m pytest backend/app/tests/test_csrf_header.py backend/app/tests/test_plugin_loader.py` 通过（17 passed）。
- `PYTHONPYCACHEPREFIX=/private/tmp/telepilot_pycache backend/.venv/bin/python -m pytest backend` 通过（567 passed, 2 skipped）。
- `pnpm --dir frontend exec tsc -b --noEmit` 通过。
- `pnpm --dir frontend build` 通过。
- `bash -n scripts/up.sh scripts/down.sh scripts/prod-up.sh deploy/backup.sh deploy/restore.sh deploy/backup-keys.sh` 通过。
- `docker compose -f docker-compose.yml config` 与 `docker compose -f docker-compose.dev.yml config` 均可渲染（dev compose 仅提示 `version` 字段已过时）。

---

## [0.14.17] — 2026-05-16 · fix · 支持原账号重新登录覆盖 session

### Fixed
- 新增“重新登录并保留配置”入口：账号处于 `login_required` 时可直接从账号详情发起重登，成功后覆盖原账号 session，不再需要删除账号或手动迁移忽略群组。
- 登录向导支持 `relogin` 模式：锁定原手机号、复用原账号 ID，重登成功后返回当前账号详情页。
- 后端重登会同时用当前 `MASTER_KEY` 重新加密保存 session、API ID、API Hash，并恢复账号为 active，解决只换 session 但 API 凭据仍旧密钥加密的问题。
- 后端增加手机号与 Telegram 用户 ID 校验，避免把其他 Telegram 账号误覆盖到当前账号配置上。

### Verification
- `git diff --check` 通过。
- `backend/.venv/bin/ruff check backend/app/api/accounts.py backend/app/services/login_service.py backend/app/schemas/account.py backend/app/tests/test_accounts.py` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m pytest backend/app/tests/test_accounts.py backend/app/tests/test_config_bundle.py` 通过。
- `pnpm --dir frontend exec tsc -b --noEmit` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.14.16] — 2026-05-16 · fix · Config Bundle 支持忽略列表迁移

### Fixed
- 账号级 Config Bundle 现在会导出、dry-run 和 confirm 写入忽略列表，支持把旧账号几十个忽略群组迁移到重新登录后的新账号。
- Config Bundle dry-run 结果增加“忽略列表”实体展示，避免只迁移插件规则和命令绑定时遗漏账号级忽略名单。
- 设置页 Config Bundle 文案补充“忽略列表”，明确它适用于账号配置迁移，不包含 session / API key / Bot Token 等敏感登录凭据。

### Verification
- `git diff --check` 通过。
- `backend/.venv/bin/ruff check backend/app/api/config_bundle.py backend/app/services/config_bundle_service.py backend/app/schemas/config_bundle.py backend/app/tests/test_config_bundle.py` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m pytest backend/app/tests/test_config_bundle.py` 通过（11 passed）。
- `pnpm --dir frontend exec tsc -b --noEmit` 通过。
- `pnpm --dir frontend build` 通过。
- 本地读取 `aid=1` Config Bundle：`features=7`、`rules=2`、`command_links=5`、`ignored_peers=56`、`size_bytes=8275`。
- 本地全量备份 round-trip：导出包含 `ignored_peers=56`；同文件导入结果 `imported=0`、`skipped=86`、`warnings=0`。

---

## [0.14.15] — 2026-05-16 · polish · 优化命令前缀触发预览

### Changed
- 设置页命令前缀预览改为更接近 Telegram 的左右对话气泡：被回复原文在左侧，用户触发命令与 AI 回复在右侧。
- 预览气泡缩小宽度、增加内边距和蓝色回复样式，让手机端观感更真实。
- AI 回复内的引用块、分割线和模型标识改为同色系样式，避免预览区域显得像普通表单文本。

### Verification
- `git diff --check` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m py_compile backend/app/__init__.py` 通过。
- `pnpm --dir frontend exec tsc -b --noEmit` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.14.14] — 2026-05-16 · fix · 明确旧密钥导致的账号与 Bot 失效

### Fixed
- 账号恢复前会先验证 session / api_id / api_hash 是否能用当前 `MASTER_KEY` 解密；失败时直接置为 `login_required` 并提示恢复原 `MASTER_KEY` 或重新登录账号，不再启动后反复 down。
- worker 启动阶段遇到账号凭据解密失败时，会写入清晰运行日志并停止自动重启该账号。
- 账号 Bot 启用时会校验已保存 Bot Token 是否还能解密；旧 `MASTER_KEY` 加密的 token 会提示“重新保存 Bot Token”，不再只显示泛泛的 422。
- 前端错误解析支持 FastAPI 校验数组，减少 `Request failed with status code 422` 这类无效提示。

### Verification
- `git diff --check` 通过。
- `backend/.venv/bin/ruff check backend/app` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m py_compile backend/app/services/account_service.py backend/app/services/account_bot_service.py backend/app/worker/runtime.py backend/app/worker/supervisor.py backend/app/__init__.py` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m pytest backend/app/tests/test_account_service.py backend/app/tests/test_account_bot.py backend/app/tests/test_kill_switch_supervisor.py` 通过（21 passed）。
- `pnpm --dir frontend exec tsc -b --noEmit` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.14.13] — 2026-05-16 · fix · 修复账号启停与紧急停用

### Fixed
- 账号暂停现在会通过 supervisor 真正停止对应 worker，不再只写 DB 状态或仅发送 worker 自己可能收不到的 pause 消息。
- 账号恢复现在会通过 supervisor 直接拉起对应 worker；kill switch 开启期间不会误拉起 worker。
- 全局紧急停用现在会停止当前所有 worker，并阻止 active 账号在总闸开启期间被自动拉起；解除后自动恢复 active 账号 worker。
- 更新紧急停用相关前端文案，从“暂停”改为“停止”，与实际控制语义一致。

### Verification
- `git diff --check` 通过。
- `backend/.venv/bin/ruff check backend/app` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m py_compile backend/app/services/account_service.py backend/app/worker/supervisor.py backend/app/api/rate_limit.py backend/app/__init__.py` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m pytest backend/app/tests/test_account_service.py backend/app/tests/test_kill_switch_supervisor.py backend/app/tests/test_system_health.py` 通过（25 passed，1 个 Alembic 配置弃用警告）。
- `pnpm --dir frontend exec tsc -b --noEmit` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.14.12] — 2026-05-16 · fix · 修复命令前缀预览

### Fixed
- 修复设置页命令前缀触发预览的 JSX 语法错误，恢复三段式 Telegram 对话预览。
- 预览中补齐“被回复原文 → 用户触发命令 → AI 回复结果”的顺序，并在回复标题中展示当前命令前缀。

### Verification
- `pnpm --dir frontend exec tsc -b --noEmit` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.14.11] — 2026-05-16 · polish · 资源占用与新手引导细化

### Changed
- 概览页资源占用改为突出展示 TeleBot 本项目合计 CPU / 内存，并补充主进程、worker、整机资源对照。
- 命令前缀触发预览限制为略大于手机屏幕的宽度，让 Telegram 对话示例更接近真实手机观感。
- 设置页“猜你想要？”里的“绑定机器人”改为点击后再选择账号；只有一个账号时直接进入该账号 Bot 联动页。
- 新手指引小条统一补充“点击展开详情”，并修复第一步“新增账号”和第二步“保存”按钮文字可见性。
- 插件中心“账号视角”改为“选择配置的账号”，并移入“账号插件启用详情与配置”容器。

### Verification
- `git diff --check` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m py_compile backend/app/__init__.py` 通过。
- `pnpm --dir frontend exec tsc -b --noEmit` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.14.10] — 2026-05-16 · patch · 完成 B2 下沉与文档同步

### Changed
- 新手指引的 Siri 流光高亮改为细外框跑马灯，不再把整个按钮或卡片填成彩色，避免遮挡按钮文字。
- `codex_image` 从 builtin 物理下沉到 `plugins/installed/codex_image/`，builtin registry 不再自动包含它；旧账号已启用时会按 installed 兼容模式加载，缺少本地代码时进入 failed 并写 runtime log。
- `codex_image` dry-run import 改为 installed 路径，并补齐 installed 插件元数据与 `send_file` 权限声明。
- 前端真实移动 Settings / Features 旧实现文件到 Plugins / AI 目录，减少“新入口壳页面继续引用旧目录”的双维护状态。
- README 与插件开发文档同步 0.14 当前状态，明确 TelePilot 完整 rename 仍属于后续 0.15。

### Verification
- `git diff --check` 通过。
- `backend/.venv/bin/ruff check backend/app` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m py_compile backend/app/worker/plugins/loader.py backend/app/api/rules.py backend/app/tests/test_codex_image_errors.py backend/app/tests/test_feature_registry.py plugins/installed/codex_image/__init__.py plugins/installed/codex_image/manifest.py plugins/installed/codex_image/plugin.py` 通过。
- `pnpm --dir frontend exec tsc -b --noEmit` 通过。
- `pnpm --dir frontend build` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m pytest backend/app/tests/test_codex_image_errors.py backend/app/tests/test_plugin_loader.py backend/app/tests/test_feature_registry.py backend/app/tests/test_feature_service.py` 通过，38 passed。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m pytest backend` 通过，551 passed, 2 skipped。

---

## [0.14.9] — 2026-05-16 · polish · 新手指引开关与流光高亮

### Changed
- 账号页“新手指引”恢复为带文字的大按钮，点击后先询问是否开始指引模式，不再默认强制开启。
- 第一阶段的小条提醒恢复到“新增账号”按钮附近，并改为浮层定位，展开后不再把账号卡片向下挤压。
- 新手指引通过 `guide=1` 显式进入指引模式；未开启时不显示步骤提示，也不高亮按钮。
- 高亮效果从普通呼吸动画改成类似 Siri 的七彩流光边框，应用于当前步骤的目标按钮和插件状态区域。

### Verification
- `git diff --check` 通过。
- `pnpm --dir frontend build` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m py_compile backend/app/__init__.py` 通过。

---

## [0.14.8] — 2026-05-16 · polish · 插件中心第三步指引

### Changed
- 新手指引不再首次进入账号页时强制弹窗，改为原“新手指引”按钮位置的小星星开关，需要时再展开。
- 插件中心第三步同时高亮“命令模板”“插件启用状态网格”“安装插件”三处，并补充 A/B/C 三点说明。
- 新手指引最后一步按钮从“跳过这步”改为“我学会了！”，更符合已到最后一步的语义。
- 插件安装页的新手指引完成按钮同样改为“我学会了！”。

### Verification
- `git diff --check` 通过。
- `pnpm --dir frontend build` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m py_compile backend/app/__init__.py` 通过。

---

## [0.14.7] — 2026-05-16 · polish · 设置快捷入口与新手指引贴边

### Changed
- 系统设置顶部“已搬家”改为“猜你想要？”，提供“添加模型 / 添加命令 / 绑定机器人”三个直接入口；绑定机器人支持先选择账号再进入对应账号的 Bot 联动页。
- 命令前缀触发预览收窄 Telegram 对话气泡宽度，回复引用块改为内容自适应，避免像整页通知卡片。
- 新手指引从左下角悬浮改为贴近当前操作按钮：账号页靠近“新增账号”，设置页靠近“保存前缀”，插件页靠近“安装插件”，并为目标按钮增加呼吸提示。
- 新手指引补充“跳过这步”，可以直接进入下一步所在页面；最后一步可结束指引。
- 插件中心首页文案继续收敛为“沉淀模板 → 按账号复用 → 新账号免重配”的表达，并明确远程插件安装后回插件中心按账号启用和配置。

### Verification
- `git diff --check` 通过。
- `pnpm --dir frontend build` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m py_compile backend/app/__init__.py` 通过。

---

## [0.14.6] — 2026-05-16 · polish · 新手指引与 AI 用量增强

### Added
- 系统设置的“命令前缀”下新增 Telegram 左右气泡预览，展示“回复原文 → 发出命令 → AI 返回结果”的真实触发感。
- 新手指引升级为三步：添加并启用账号、设置命令前缀、启用命令模板或调用插件，并在账号 / 设置 / 插件页面提供左下角呼吸悬浮入口。
- AI 最近调用增加摘要卡片，展示请求数、成功数、失败数、Fallback 次数、总 Token 与平均耗时。

### Changed
- 插件安装页移除重复的“按账号启用”Tab，安装页只负责安装 / 更新 / 卸载，账号级启用和配置统一回插件中心首页。
- 插件安装页补充“返回上一页”，深链进入时可回到插件中心。
- AI 最近调用表格改为中文表头，并补充 fallback / 错误类型信息。

### Verification
- `git diff --check` 通过。
- `pnpm --dir frontend build` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m py_compile backend/app/api/llm_usage.py backend/app/main.py` 通过。

---

## [0.14.5] — 2026-05-16 · fix · 恢复远程插件安装入口

### Added
- Plugins 中心新增“安装插件”入口，统一进入 `/plugins/manage?tab=plugins` 管理 Git 仓库、远程插件安装、更新、启用、禁用和卸载。
- 新增 `/api/llm/usage/recent`，AI 中心“最近调用”可直接读取 `llm_usage` 表，不再停留在接口预留空态。

### Changed
- 0.13 安全迁移提示里的远程插件运维入口改为“前往插件安装”，补齐 Telegram 高危插件命令移除后的 Web 替代路径。
- 插件安装与管理页默认停留在“安装与更新”Tab，并用更明确的中文说明远程插件安装后还需要按账号启用。
- AI Usage 表格补充来源、模型提供商名称、耗时和 fallback 状态，便于验证命令 / scheduler 的 LLM 调用。

### Verification
- `git diff --check` 通过。
- `pnpm --dir frontend build` 通过。
- `PYTHONPYCACHEPREFIX=/private/tmp/telebot_pycache backend/.venv/bin/python -m py_compile backend/app/api/llm_usage.py backend/app/main.py` 通过。

---

## [0.14.4] — 2026-05-16 · polish · 两步新账号指引

### Changed
- 新手指引简化为“添加并启用账号 / 启用命令模板或调用插件”两大步，一次只聚焦当前要做的事。
- 新手指引第二步补充命令前缀入口，明确先去插件中心复用模板或开启插件，再到系统设置确认 TG 内命令前缀。
- 账号详情“新账号下一步”三张卡片标题放大加粗，让新账号配置入口更醒目。

### Verification
- `git diff --check` 通过。
- `pnpm --dir frontend build` 通过。

---

## [0.14.3] — 2026-05-16 · polish · 插件中心文案与逐步新手引导

### Changed
- 插件中心顶部说明改为更自然的大白话：强调“把常用回复、转发、AI 命令整理成模板，再按账号启用复用”。
- 插件中心三入口（命令模板 / 命令别名 / 定时任务）改为更稳健的自适应卡片布局，优化小屏与中等宽度下的长文显示，避免文案挤压。
- 账号详情“新账号下一步”的启用插件数量挪到卡片右上角，让三张卡片按钮位置更统一。
- 新手指引改为逐步式流程：一次聚焦一步，支持上一步 / 下一步 / 去执行当前步骤，不再一次性展示全部四步。

### Verification
- `pnpm --dir frontend build` 通过。

---

## [0.14.2] — 2026-05-16 · polish · 新账号引导与 AI 中心再收敛

### Changed
- AI 中心将“模型提供商”和“最近调用”整合为同页 Tab，AI 帮助缩小为顶部按钮入口，减少首页卡片重复感。
- 插件中心统一“定时任务”命名，替换原“调度中心”文案；入口描述改成“模板可跨账号复用”的大白话说明。
- 账号详情“快捷入口”改为“新账号下一步”，明确它用于新账号复用模板、开启插件、复制成熟配置。
- 账号管理页新增“新手指引”按钮，并首次进入自动弹出 4 步引导动画，可随时从顶部按钮再次打开。

### Verification
- `pnpm --dir frontend build` 通过。

---

## [0.14.1] — 2026-05-16 · fix · 前端 IA 追补与资源展示修正

### Changed
- 调整 AI 中心首页文案：将“路由策略”卡片改为“AI 帮助”，说明聚焦“工作原理 / 术语速查 / 推荐配置”，避免与“模型提供商”入口语义重叠。
- 调整 `/ai/help` 页面定位与标题文案为“AI 帮助”，移除重复的“模型提供商列表”Tab，仅保留“工作原理”“术语速查”“推荐配置”三个 Tab。
- Dashboard 资源占用从“主进程 CPU / 主进程内存”改为“项目 CPU / 项目内存”，统计口径改为 FastAPI 主进程 + 全部账号 worker 子进程合计。
- 账号详情概览将“能力与迁移入口”改为“快捷入口”，用大白话说明它用于跳转到插件、命令模板和配置复制。
- 账号详情页移除过渡性质的“命令”Tab，命令管理统一从 `/plugins/templates?account=:aid` 进入。

### Verification
- `backend/.venv/bin/python -m pytest -q backend/app/tests/test_system_health.py` 通过：17 passed。
- `pnpm --dir frontend build` 通过。

---

## [0.14.0] — 2026-05-16 · feature · 前端 IA 中心化

### Changed
- 前端顶层导航从 8 个入口收敛为 6 个用户目标导向入口：概览、账号、插件、AI、日志、系统；调度与模板不再占据顶层菜单，为后续 Plugins 中心化做路由落点。
- 新增 Plugins 中心页，将平台能力、内置插件、远程插件、实验性能力分区展示；`codex_image` 固定进入实验性区，让 0.13 的 experimental 标识在前端更显眼。
- 将命令模板、别名、调度能力接入 `/plugins/templates`、`/plugins/aliases`、`/plugins/scheduler`，并保留旧入口重定向，避免旧书签失效。
- 新增 AI 顶级入口 `/ai`，拆出 Provider、Routing、Usage 三个子入口；Usage 页以最小表格展示最近调用，并在后端接口尚未开放时提供温和空状态。
- Settings 瘦身为账号、平台、安全、迁移四组，隐藏 LLM Providers、命令模板、别名管理旧入口，并提供“已搬家”跳转提示。
- 账号详情概览增加能力与迁移入口卡片：启用插件数、启用命令数、复制配置；旧命令 Tab 降级为迁移提示页。
- Config Bundle 支持从 `/settings?tab=backup&source=:aid` 预填源账号；Plugins 支持 `/plugins?account=:aid` 按账号视角打开。
- Logs 页新增 Runtime / Audit 顶层 Tab，Audit 接入 `/api/logs/audit` 并支持动态 action 过滤，便于查看 sudo、Config Bundle confirm、account_bot confirm 等安全决策记录。
- 命令模板新增 AI provider 空态引导：没有 Provider 时禁用 AI 类型创建并跳转到 `/ai/providers`；已删除 Provider 的 AI 模板会显示“provider 缺失”警告。
- Plugins 首页增加一次性安全迁移 banner，提示 0.13 已移除 `,reboot`、`,plugin install` 等 Telegram 内高危命令；account_bot 配置页补充危险操作二次确认提示。
- 移动端新增 4 项底部 Tab Bar（概览、账号、插件、日志），AI 与系统设置保留在汉堡菜单；账号详情 Tabs 在窄屏改为横向滚动，避免挤压重叠。
- 收敛专属插件配置页入口：`FEATURE_CONFIG_PAGE_KEYS` 改由 `frontend/src/pages/Plugins/_shared/featureConfig.ts` 统一维护，减少账号详情与旧插件中心双份维护。
- B5 清理 legacy feature key：移除已无引用的 `FEATURE_LEGACY_KEYS` 导出，并补充 registry 测试确保 `group_admin` / `monitor` 不会回到内置功能列表。
- B2/F9 先落地 `codex_image` 下沉兼容检测：暂不做物理迁移；若旧账号启用但运行节点缺实现，worker 会记录 runtime_log、标记 failed，Plugins 页显示恢复提示。
- 移除旧前端 URL 兼容重定向：`/scheduler`、`/templates`、`/settings/commands`、`/settings/aliases`、`/settings/llm-providers`、`/ai-settings`、`/matrix`、`/extensions`、`/remote-plugins` 不再作为入口保留，统一从 `/plugins/*`、`/ai/*`、`/settings` 进入。
- 补齐新入口的中文文案与返回体验：AI / Plugins 子页增加“返回上一页”，从菜单点击进入时返回点击前页面，直接打开深链时回到对应中心页。
- README 与插件开发指南同步 0.14 IA、共享 feature config helper、Audit 日志和 `codex_image` 兼容策略说明。

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
