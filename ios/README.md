# TelePilot iOS

自用的 SwiftUI 原生管理台。服务器继续运行 Telegram、插件、AI 和后台任务，手机通过现有 API 管理内容。原生 App 与服务器分别构建、分别升级。

## 功能

- 服务器连接、用户名密码登录、TOTP、通知验证码及服务器允许的恢复码流程。
- 基础服务健康、Worker 实际存活情况、主机资源。
- 账号列表、搜索、收藏、头像、资料编辑、Telegram 验证码／两步验证绑定、暂停恢复、删除、配置复制与迁移。
- 账号 Bot、交互规则、忽略对象、消息频率、限流、直通顺序、定时任务与自动指令白名单。
- 插件分类与收藏、安装卸载、仓库、启停、更新、签名状态、原生配置表单、规则、试运行和后台操作进度。
- 指令模板、别名、模板预览和真实测试发送；AI 供应商、密钥池、模型发现、测活、路由、输出与用量。
- 独立助手圆形入口：会话、图片、模型选择、流式回复、历史、重生成、停止／调整、队列、审批、轨迹与长期记忆。
- 日志筛选、调用链、路由模拟、脱敏导出；资金明细与补偿、Webhook、平台设置、代理、设备、通知 Bot、权限和 TOTP。
- 配置原生文件导入／导出、导入预检、网络诊断、服务器升级进度、重启与紧急停止。
- 可定制底部 Dock 和首页、全局搜索、iPad 双栏、深色模式、Face ID／设备验证与敏感信息遮盖。
- 可选 Apple 远程推送，配置步骤见 [iOS 推送通知配置教程](../docs/IOS-PUSH.md)，接口与投递语义见 [APNs 参考](../docs/APNS.md)；App 内「设置 → Apple 推送通知」提供状态检查与配置教程。
- 在 Safari 打开完整 Web 管理台。浏览器与 App 的会话独立，可能需要分别登录。

普通字段由原生表单承载，第三方插件配置使用服务端 schema 生成嵌套对象、数组、枚举等原生控件。App 不运行插件或模型，也不模拟 iOS 后台常驻。退出 App 不会停止服务器任务。

Dock 旁边的助手圆形按钮始终保留；Dock 的省略号菜单始终提供全部入口，即使隐藏设置也能重新配置。

## 打开和自签

要求 Mac 上安装完整 Xcode，并安装能构建 iOS 26 或更新版本的 SDK。最低系统为 iOS 26，使用最新 SDK 构建时由原生系统组件提供 Liquid Glass。项目不指定开发团队，不包含证书、私钥或描述文件。

1. 用 Xcode 打开 `ios/TelePilot.xcodeproj`，选择 `TelePilot` scheme。
2. 在 App target 的 Signing & Capabilities 选择自己的 Team。需要时把 Bundle Identifier 改为自己可签名的唯一标识。
3. 选择自己的 iPhone，按 Xcode 提示信任设备并开启开发者模式，再运行安装。
4. App 中填写服务器 HTTPS 根地址，例如 `https://telepilot.example.com`，使用已有 Web 用户登录。

只接受有效 HTTPS 根地址，不接受 URL 中的用户名密码、查询参数、片段或反代子路径。使用私有 CA 时应先在设备上正确信任证书；App 不提供跳过证书验证或全局允许明文 HTTP 的开关。

服务器前套了 Cloudflare Access（如邮箱白名单 + Google 登录）时，在登录页或 设置 → Cloudflare Access 点「使用 Google 验证」，在系统登录窗完成 Google 登录即可，Cloudflare 后台无需修改；Service Token 作为「高级」可选。详见 [docs/CLOUDFLARE-ACCESS.md](../docs/CLOUDFLARE-ACCESS.md)。

免费 Personal Team 签名有有效期限制，到期需重新签名安装。付费开发者账号同样需要维护有效签名或测试分发构建；自用不要求公开上架。

默认工程不强制申请推送 entitlement，普通自签可直接使用前台功能。需要 APNs 时，在 Xcode 为你的 App ID 添加 Push Notifications，并将自定义构建设置 `TELEPILOT_PUSH_ENTITLEMENTS` 设为 `TelePilotApp/TelePilot.entitlements`（或由 Xcode Signing & Capabilities 设置同等权限）。自签工具同样需要注入与描述文件一致的 `aps-environment`。App 从实际嵌入描述文件读取开发／生产环境，不根据 Debug／Release 猜测。

### 导出未签名 IPA

在仓库根目录运行：

```bash
bash ios/scripts/build-unsigned.sh
```

生成 `ios/build/TelePilot-unsigned.ipa` 和 `ios/build/TelePilot.xcarchive`。IPA **未签名，不能直接安装**，需要通过你自己的签名流程处理。签名工具应保留有效 Bundle Identifier、Keychain 访问能力与对应描述文件；改变签名身份或 Bundle Identifier 后可能需要重新登录。

也可以把一个输出目录作为脚本的第一个参数。构建产物已被 Git 忽略，不会进入服务器发布包的源代码提交。

### App 版本号

iOS App 的版本号独立于服务器版本：`MARKETING_VERSION`（显示版本，纯数字 `X.Y.Z`，当前 `0.8.0`）只在 iOS 发版检查点按改动级别 bump，变化记在 [`ios/CHANGELOG.md`](CHANGELOG.md)（随 App 打包，在“关于 → App 更新日志”离线显示），`ios/project.yml` 与 `TelePilot.xcodeproj` 需同步修改，可用 `python3 ios/scripts/check-ios-version.py` 校验。build 号（`CFBundleVersion`）由 CI 打包时注入，取工作流运行序号，每次打包严格递增；本地构建使用工程里的默认值，也可设置 `TELEPILOT_BUILD_NUMBER=<正整数>` 再运行 `build-unsigned.sh` 覆盖。App 的“关于”页显示为 `版本（build）`。

没有 Mac 时，可直接下载 GitHub Actions 构建的未签名 IPA：`Beta` 或 `main` 上 `ios/**` 有改动时，工作流「iOS 未签名 IPA」会自动打包（也可在 Actions 页手动运行），在运行详情页底部 Artifacts 下载，工件名为 `TelePilot-<版本>-<build>-<短提交>-unsigned`，保留 30 天，内含 IPA、`TelePilot.entitlements` 和说明。免费 Apple ID 自签可以安装使用，但无法启用推送能力；测试 APNs 需要付费开发者账号签名并注入 `aps-environment`。

## 服务器修复后，App 要不要更新？

服务器版本与客户端版本独立，不要求两个版本号一致。

| 服务器变化 | 通常是否需要更新 App |
| --- | --- |
| Worker、插件执行或数据库内部 bug 修复，API 保持兼容 | 不需要，App 直接使用修复后的服务 |
| 新增接口或增加未使用的响应字段 | 不需要，旧 App 继续使用原来的功能 |
| 新增需要原生入口的业务能力 | 旧功能不受影响；要使用新原生页面才需要更新 |
| 删除或改名 App 使用的字段、改变字段类型、路径、登录协议 | 需要兼容处理或配套更新 App |
| 密码变更、会话到期或服务器密钥轮换 | 通常只需重新登录，不一定需要更新 App |
| App 自身的界面、系统适配或网络处理 bug | 需要重新构建并签名 App |

客户端忽略未知响应字段，保留未知账号状态并禁用不明确的写操作；缺失必要字段或接口不可用时显示错误，不把无法确认的状态伪装为成功。通用 API 表单契约随 App 打包，插件配置 schema 则从当前服务器读取。**这些措施降低耦合，但不能保证任意不兼容的后端更新都无需更新 App。**

服务端升级建议保持现有 API 的字段、路径与鉴权兼容，并运行下述客户端契约与回归检查。不要只因为后端版本号改变，就要求用户重装 App。

## 认证与安全边界

- 复用 `/api/auth/csrf`、`/api/auth/login`、`/api/auth/me` 与 `/api/auth/logout`，不创建 App 专用 token 或注册流程。
- 写请求保留服务器的 `X-Requested-With` 与双提交 CSRF 校验，不修改服务器安全策略。
- 密码、TOTP、OTP 和恢复码只在当次认证过程中使用，不写入偏好设置或日志。持久会话按服务器隔离保存到 Keychain。
- 不共享浏览器 Cookie，不把认证信息交给网页或第三方服务，不自动跟随重定向泄露凭据。
- Cloudflare Access 登录令牌（App 内 Google 验证取得）与可选的 Service Token 存钥匙串（按服务器隔离、仅本机），只作为 `cf-access-token` 或 `CF-Access-Client-Id` / `CF-Access-Client-Secret` 请求头发往该服务器；令牌转交轮询使用独立网络会话、不带 TelePilot 凭证；Access 拦截单独识别，其他重定向仍拒绝。
- 401 清除失效会话。普通离线错误不等同于“未登录”。
- 写请求网络失败不自动重试；先刷新确认服务器状态。服务器接受恢复请求，不等于进程已立即存活。
- 助手创建任务使用持久化的客户端请求 ID；断线只恢复只读进度订阅，不自动重复发消息。待核对的本机记录只含任务／会话标识，不含消息正文或附件。
- 敏感值默认遮盖，显示时通过设备验证；离开 App、录屏或镜像时遮住整幅场景，包括已经打开的 Sheet。助手常见凭据格式在显示、分享和复制前脱敏。
- 普通截图的通知发生在截取之后，无法用公开 API 可靠阻止已经显示的内容被截图；因此不承诺任何截图都不含敏感内容。[Apple 截图事件](https://developer.apple.com/documentation/uikit/uiapplication/userdidtakescreenshotnotification)
- 原生刷新仅在使用期间发生；APNs 默认关闭，需要服务器配置与推送签名。通知只带固定提示与导航标识，按服务器 origin 校验，解锁后进入对应页面。

## 开发与验证

`project.yml` 是 XcodeGen 工程定义，生成的 `.xcodeproj` 一并维护，普通使用者不需要安装 XcodeGen。修改工程结构后执行：

```bash
cd ios
xcodegen generate
```

网络类型和请求代码由 Apple Swift OpenAPI Generator 生成并随源码维护，普通 Xcode 构建不运行代码生成插件，无需额外信任自动执行的插件。代码生成工具单独放在 `TelePilotCore/Codegen/`，不进入 App 运行时。

服务器 API 变化后，在仓库根刷新客户端实际使用的接口投影，再生成 Swift：

```bash
python3 ios/TelePilotCore/scripts/sync-openapi.py
python3 ios/TelePilotCore/scripts/sync-contract.py
sh ios/TelePilotCore/scripts/generate.sh
```

投影从仓库的 `openapi/telepilot.openapi.json` 读取，不回写后端契约。FastAPI 的可空 `anyOf` 会转换为生成器支持的等价可空表示，保留 TOTP、OTP 等字段；生成结果须连同回归测试一起验证，不能把“生成成功”当成完整兼容证明。

在仓库根运行核心测试和模拟器 UI 测试：

```bash
python3 ios/TelePilotCore/scripts/sync-openapi.py --check
sh ios/TelePilotCore/scripts/generate.sh --check
swift test --package-path ios/TelePilotCore
xcodebuild test \
  -project ios/TelePilot.xcodeproj \
  -scheme TelePilot \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -parallel-testing-enabled NO \
  -collect-test-diagnostics never \
  CODE_SIGNING_ALLOWED=NO
sh ios/scripts/ui-visual-screenshots.sh ios/build/ui-screenshots-light
git diff --check
```

模拟器名称以 `xcrun simctl list devices available` 为准。GitHub Actions 的「iOS」工作流（`.github/workflows/ios.yml`）会在 GitHub `xcode-27` 运行器（Xcode 27；工程使用 `TabRole.prominent` 等 iOS 27 SDK API，需 Xcode 27 编译）上自动执行同样的契约检查、核心测试和 UI 测试，iOS 相关 PR 无需本地编译即可看到结果；手工增删 Swift 文件后可用 `python3 ios/scripts/check-xcodeproj-sources.py` 确认工程已收录。UI 测试以 Debug 专属的 fixture 启动，不连接真实服务器；Release 构建不包含这套测试服务。核心测试使用模拟 HTTP 响应验证真实网络层协议，不使用生产凭据。

视觉巡检脚本会连接当前已启动的 iPhone 模拟器，按固定路径拍摄概览、插件中心、插件管理、插件详情、AI 概览、供应商、测活和用量。脚本不会覆盖已有 result bundle；复查深色模式或其他模拟器时，先切换好外观或机型，再使用新的输出目录。

实际服务器验收仍需在自签安装后执行：

1. 输入服务器 HTTPS 地址，完成实际启用的登录与二次验证方式。
2. 对照 Web 管理台确认基础服务、账号与进程状态。
3. 对一个可安全暂停的账号，确认暂停、刷新状态、再恢复。检查运行状态而不只看成功提示。
4. 断网后刷新，确认有错误或旧数据提示；联网后能重试。
5. 锁屏再返回、会话过期、退出重启、切换服务器时，确认数据和登录态不会串用。
6. 检查浅色、深色、大字号和“减少透明度”，长账号名与错误信息应完整可读。

本工程只在客户端目录内构建。无需为 iOS 开发执行数据库迁移、启动本机完整后端或修改线上配置。
