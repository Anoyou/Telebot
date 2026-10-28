# TelePilot Agent Rules

## 版本与发布

- `main` 是稳定发布线，只接收已经验证稳定的 beta；开发期间 `Beta` 必须维护带 prerelease 后缀的版本号（例如 `0.81.0-beta.1`），稳定后再合入 `main` 发布对应正式版（例如 `0.81.0`）。正式版检查点完成后，允许 `Beta` 暂时与 `main` 指向同一个稳定版提交；`Beta` 的下一次开发提交必须先切换到新的 prerelease 版本。
- 发布前必须执行 `git fetch origin --prune`，同时检查 `origin/main` 与所有仍活跃的发布/开发分支版本，不能只依据当前工作树选择下一个版本号。
- 不要为每个微小提交单独迭代版本号。版本号只在准备 beta 检查点、稳定发布、推送稳定检查点、创建 release/PR，或用户明确要求“推一版/发一版”时统一迭代。
- 一批相关改动只对应一个版本号；开发过程中先积累到 `CHANGELOG.md` 的 `Unreleased`，准备 beta 检查点或稳定发布时再决定版本号并移动到对应版本段落。
- 版本 bump 必须按 SemVer 判断：
  - `MAJOR（主版本）`：破坏兼容的数据库迁移、配置格式变更、API 路径或语义不兼容、老版本无法平滑升级。
  - `MINOR（次版本）`：用户可感知的新能力、主要入口/信息架构重组、后端能力完整前端化、新插件或重要工作流变化。
  - `PATCH（补丁版本）`：bug 修复、文案、小 UI、错误提示、测试补充、兼容性补丁和不改变主要用户路径的小调整。
- 0.x 阶段额外约定：`0.X.0` 表示阶段性能力版本，`0.X.Y` 表示同一阶段内的补丁；不要把第三位当作日常流水号。
- prerelease 的基础版本决定 SemVer 级别，`beta.N` 只表示同一候选版本的迭代次数：
  - 新阶段能力从新的基础版本 `-beta.1` 开始，例如 `0.81.0-beta.1`。
  - 同一候选版本继续修复和收口时只递增 beta 序号，例如 `0.81.0-beta.2`。
  - 同阶段补丁版本从新的补丁基础版本开始，例如 `0.81.1-beta.1`。
  - beta 期间若加入新的 minor 或 major 级变化，必须提升基础版本并从 `-beta.1` 重新开始，不能只递增 beta 序号。
  - beta 稳定并合入 `main` 后去掉 prerelease 后缀，发布相同基础版本的正式版。
- 版本号用一条命令迭代（仓库根目录）：先把本批改动用中文写进 `CHANGELOG.md` 的 `Unreleased`，再运行 `python3 scripts/bump-version.py --title "主题"`（或 `make bump T="主题"`）。脚本会 `git fetch origin --prune`，读取 `origin/Beta`、`origin/main` 与当前工作树的版本，按上面的规则算出下一个版本号：默认 `--level patch`（同一候选版本递增 `beta.N`；当前是正式版时从下一个补丁版本 `-beta.1` 开始），`--level minor` / `--level major` 提升基础版本并从 `-beta.1` 开始，`--level stable` 去掉预发布后缀，`--set X.Y.Z-beta.N` 显式指定；随后写入所有版本源、把 `Unreleased` 移到 `## [版本] - 日期 · 级别 · 主题` 段落，并运行 CI 同款校验 `scripts/check-version-sync.py`。`--dry-run` 只打印计算结果。
- 版本源只有三处字面量：`backend/app/__init__.py` 的 `__version__`（`backend/pyproject.toml` 用 `dynamic` 读取它，不再写 `version`）、`frontend/package.json` 的 `version`（`frontend/src/lib/version.ts` 从它导入，不再写字面量）、`openapi/telepilot.openapi.json` 的 `info.version`（bump 脚本直接改写；改 API 时仍用 `make codegen` 重新生成）。CI「版本同步」用 `scripts/check-version-sync.py` 校验三处一致、上述两个文件的结构，以及 `CHANGELOG.md` 有当前版本段落。不要手改这些文件里的版本号。
- iOS 的 OpenAPI 原生子集（`ios/TelePilotCore/Sources/TelePilotCore/openapi.json`）不携带服务器版本（`info.version` 固定为 `native-subset`），服务器升版本不改动 `ios/**`；「iOS」工作流发现 iOS 相关路径里只有 `openapi/telepilot.openapi.json` 变化且 `sync-openapi.py` / `sync-contract.py --check` 通过时，跳过 iOS 编译与 UI 测试。iOS App 版本独立，见下方「iOS 版本」。
- 并行 PR 撞版本号：rebase 到最新 `origin/Beta` 时，版本文件的冲突直接取目标分支的值，`CHANGELOG.md` 保留双方段落，然后再运行一次 `python3 scripts/bump-version.py`（可省略 `--title`）：脚本识别出本分支尚未合并的版本段落，顺延到下一个空闲版本号并移到最上方。
- commit / PR / release 文案使用中文。

## iOS 版本

- iOS App 使用**独立于 TelePilot 服务器**的版本号，不跟随、也不要求等于服务器版本；服务器 beta 检查点或稳定发布不需要改 iOS 版本，反之亦然。
- `MARKETING_VERSION`（`CFBundleShortVersionString`）按 Apple 规范只能是纯数字 `X.Y.Z`，不能带 `-beta` 等后缀；按上文同一套 SemVer 级别判断，只在 **iOS 发版检查点**（准备给用户分发新 IPA、或用户明确要求 iOS 发一版）时按本批 iOS 改动的最高级别统一 bump 一次，不为每个提交单独迭代：
  - `MINOR`：用户可感知的新能力、页面/信息架构重组、重要工作流变化；`PATCH`：bug 修复、文案、小 UI 与兼容性调整；`MAJOR`：要求重新配置或无法平滑升级的变化（如更换 Bundle ID、Keychain 数据不兼容）。
  - iOS 改动必须**同时**记入 `ios/CHANGELOG.md`（iOS App 自身的更新日志，只写用户能感知的 App 变化）和根目录 `CHANGELOG.md` 的 `Unreleased`；bump 时把 `ios/CHANGELOG.md` 的 `Unreleased` 移到新版本段落，标题写 `## [X.Y.Z] - 日期 · 级别 · 主题`，下一行写 `> 对应服务器：…`（随哪个服务器检查点或 Beta 合并发布），并在根 CHANGELOG 对应条目注明 `iOS X.Y.Z`。
  - `ios/CHANGELOG.md` 随 App 打包，在“关于 → App 更新日志”离线显示，修改时保持上述标题格式。
- `CURRENT_PROJECT_VERSION`（`CFBundleVersion`，build 号）由 CI 生成：工作流「iOS 未签名 IPA」以本工作流的 `github.run_number` 通过 `TELEPILOT_BUILD_NUMBER` 注入 `ios/scripts/build-unsigned.sh`，保证每次打包严格递增；工程里的值只是本地构建默认值，不要手工递增。IPA 工件名为 `TelePilot-<版本>-<build>-<短提交>-unsigned`。
- 改版本时 `ios/project.yml` 与 `ios/TelePilot.xcodeproj/project.pbxproj` 必须同步修改；`python3 ios/scripts/check-ios-version.py` 校验两处一致、格式合规，且 `ios/CHANGELOG.md` 最新版本段落等于当前 `MARKETING_VERSION`，「iOS」与「iOS 未签名 IPA」工作流都会运行它。

## 工作区安全

- 可能存在用户或其他 agent 的未提交改动。不要 revert、checkout 或 reset 你没有明确负责的改动。
- 手工编辑文件使用 `apply_patch`。

## 插件目录边界

- `plugins/installed/` 是本机运行时插件目录，必须保持 Git 忽略，禁止用 `git add -f` 将其中插件随 Core 发布；可分发参考实现放入 `examples/plugins/`，正式插件通过插件仓库或安装流程交付。

## 日志安全

- 主进程或独立 worker 子进程调用 `logging.basicConfig()` 后，必须安装 `install_sensitive_log_filter()`；新增进程入口时要用回归测试确认 Telegram Bot API URL、Authorization 和代理凭据不会以明文进入日志。
- `SystemSetting` 中保存的 Token、密码或其他可复用凭据必须使用 `*_enc` 字段经 `MASTER_KEY` 加密，并同步加入 `app.scripts.rekey` 覆盖；公开鉴权入口不得在凭据校验前创建或修改持久化配置。

## 项目级 Agent Playbook

- 处理代码、文档、排障、UI、部署或发布任务时，先阅读并按需使用 `docs/AGENT-PLAYBOOKS.md`。
- 复杂需求先走基础进入流程；Bug/异常优先使用 Plugin Hunt 的根因定位口径；UI 改动使用 UI Check；部署/远端操作使用 Deploy Check；推版、PR、release 使用 Release Check。
- Playbook 只用于约束执行和验收，不得覆盖用户当前指令、版本发布规则或工作区安全规则。

## 项目地图与验证

- `backend/app/api/`：FastAPI 路由与请求 schema 边界；接口事实以运行中的 OpenAPI 和 Pydantic schema 为准。
- 新增或修改内部 API、鉴权依赖、错误响应或 Pydantic schema 后，必须从仓库根运行 `make codegen`，同时提交 `openapi/telepilot.openapi.json` 与 `frontend/src/api/schema.ts`；新改前端 API 类型优先引用生成类型。
- `backend/app/services/`：控制面业务服务。`account_bot_runtime.py` 是 Bot runtime 热点，改动后至少运行 `pytest app/tests/test_account_bot.py`。
- `backend/app/worker/plugins/`：插件加载、Event Bus、MessageOps、权限和兼容桥。`loader.py` 是插件运行时热点，改动后至少运行 `pytest app/tests/test_plugin_loader.py` 与插件示例校验。
- `backend/app/services/system_agent/`：System Agent 路由、工具、Skill、Action 与 Durable Run；改动后运行对应 `test_system_agent_*.py`，不要只做单文件语法检查。
- `frontend/src/pages/` 与 `frontend/src/components/`：Web/PWA 页面和共享组件；前端改动至少运行 `pnpm --dir frontend typecheck`，涉及交互或构建入口时再运行 `pnpm --dir frontend build`。
- `docs/PLUGIN-API-REFERENCE.md` 是插件字段与兼容语义的完整参考；短文档只保留入口和特有内容，避免复制完整字段表。
- 默认全量验证入口：`cd backend && . .venv/bin/activate && ruff check app && pytest`，随后在仓库根目录运行 `backend/.venv/bin/python scripts/validate-plugin-examples.py`、`pnpm --dir frontend test`、`pnpm --dir frontend build` 和 `git diff --check`。
