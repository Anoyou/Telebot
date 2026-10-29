#if DEBUG
import Foundation
import TelePilotCore

actor FixtureService: AppServing {
    private let offline: Bool
    private let mutationUncertain: Bool
    private let refetchError: Bool
    private var changed = false
    private var firstStatus = "active"
    private var secondStatus = "paused"
    private var currentUserReads = 0
    private var cleanupAttempts = 0
    private var fixtureValues: [String: JSONValue] = [:]
    private var accountFeatureEnabled: [Int: [String: Bool]] = [1: ["auto_reply": true], 2: ["auto_reply": false]]
    private var accountAICommands: [Int: Set<Int>] = [1: [11], 2: []]
    private var fixtureSettings: JSONValue = [
        "timezone": "Asia/Shanghai", "command_prefix": ".", "ai_enabled": true, "ai_context_messages": 3,
        "llm_limits": ["per_minute": 30, "daily_requests": 0, "daily_tokens": 500000, "premium_daily": 0],
        "log_retention": ["llm_usage_retention_days": 30],
        "app_update_target": ["remote": "origin", "branch": "Beta"]
    ]
    private var fixtureUpdateCheck: JSONValue = ["enabled": true, "interval_minutes": 1440]
    /// 更新任务界面测试：前几次读取返回执行中（带进度与日志），之后返回完成。
    private var fixtureUpdateJobReads = 0
    private var pluginGlobalEnabled: [String: Bool] = ["auto_reply": true, "content_assistant": true, "forward_guard": false]
    /// 插件商店界面测试：内容整理助手当前版本（更新后变为 2.2.0）与从仓库新装的插件。
    private var contentAssistantVersion = "2.1.0"
    private var contentAssistantUpdatedAt: Date?
    private var storeInstalledPlugins: [String] = []
    /// Cloudflare Access 界面测试：`--ui-test-cf-access` 时，没有任何 Access 凭证的登录会被 Access 拦截。
    private var accessConfig = CloudflareAccessConfig()
    private let accessRequired = ProcessInfo.processInfo.arguments.contains("--ui-test-cf-access")
    /// 已删除的助手会话：列表不再返回，读取返回 404。
    private var deletedSessions: Set<String> = []
    /// 运行阶段与排队消息界面测试（`--ui-test-run-phases`）：第一条消息开始一个一直执行中的任务，
    /// 进度流依次给出 run_started → provider_selected → model_attempt → tool_started；执行中再发的消息进入排队。
    private let runPhases = ProcessInfo.processInfo.arguments.contains("--ui-test-run-phases")
    private var phaseRunActive = false
    private var phaseRunCancelRequested = false
    private var phaseRunStartedAt: Date?
    private var phaseQueue: [JSONValue] = []
    private var phaseQueueSerial = 0

    init(offline: Bool = false) {
        self.offline = offline
        mutationUncertain = ProcessInfo.processInfo.arguments.contains("--ui-test-mutation-uncertain")
        refetchError = ProcessInfo.processInfo.arguments.contains("--ui-test-refetch-error")
    }

    func login(_ request: LoginRequest) async throws -> LoginResult {
        if accessRequired && accessConfig.isEmpty { throw APIError.cloudflareAccessRequired(.none) }
        if request.recoveryCode == "RECOVERY-TEST" { return LoginResult(ok: true) }
        if request.otpToken == "fixture-otp" {
            guard request.otpCode == "654321" else { throw APIError.server(status: 400, code: "OTP_INVALID") }
            return LoginResult(ok: true)
        }
        guard request.totpCode != nil else { return LoginResult(ok: false, requireTotp: true, recoveryAvailable: true) }
        guard request.totpCode == "123456" else { throw APIError.server(status: 400, code: "TOTP_INVALID") }
        return LoginResult(ok: false, requireOtp: true, otpToken: "fixture-otp", otpMessage: "界面测试验证码为 654321。", otpTTLSeconds: 300, recoveryAvailable: true)
    }

    func currentUser() async throws -> CurrentUser {
        currentUserReads += 1
        if ProcessInfo.processInfo.arguments.contains("--ui-test-session-retry"), currentUserReads == 1 {
            throw APIError.network(outcomeUncertain: false)
        }
        // 冷启动快照用例：模拟断网启动（核对会话也连不上服务器）。
        if ProcessInfo.processInfo.arguments.contains("--ui-test-offline-start") {
            throw APIError.network(outcomeUncertain: false)
        }
        return CurrentUser(id: 1, username: "界面测试用户", hasTotp: true)
    }

    func accounts() async throws -> [Account] {
        try checkConnection()
        return [
            firstAccount(),
            Account(id: 2, phone: "+10000000002", displayName: "备用账号", status: secondStatus, tags: ["备用"]),
            Account(id: 3, phone: "+10000000003", displayName: "需要重新登录的账号", status: "login_required"),
            Account(id: 4, phone: "+10000000004", displayName: "未来状态兼容性测试账号", status: "new_server_status")
        ]
    }

    func account(id: Int) async throws -> Account {
        try checkConnection()
        if changed && refetchError { throw APIError.network(outcomeUncertain: false) }
        guard let account = try await accounts().first(where: { $0.id == id }) else { throw APIError.server(status: 404, code: nil) }
        return account
    }

    func health() async throws -> HealthOverview {
        try checkConnection()
        if ProcessInfo.processInfo.arguments.contains("--ui-test-session-expired") { throw APIError.unauthorized }
        let activeCount = [firstStatus, secondStatus].filter { $0 == "active" }.count
        let pausedCount = [firstStatus, secondStatus].filter { $0 == "paused" }.count
        return HealthOverview(
            db: ProbeStatus(ok: true), redis: ProbeStatus(ok: true), alembic: ProbeStatus(ok: true),
            workers: WorkersStatus(total: 4, byStatus: ["active": activeCount, "paused": pausedCount],
                                   runtimeAlive: activeCount, runtimeDesiredRunning: activeCount,
                                   runtimeDesiredRunningAlive: activeCount, runtimeFailing: 0)
        )
    }

    func resources() async throws -> ResourceDashboard {
        try checkConnection()
        return ResourceDashboard(host: HostResource(cpuPercent: 12, memoryUsedPercent: 38, diskUsedPercent: 24, uptimeSeconds: 482_400),
                                 workers: [
                                    WorkerResource(accountID: 1, alive: firstStatus == "active", desired: firstStatus == "active" ? "running" : "stopped", failCount: 0),
                                    WorkerResource(accountID: 2, alive: secondStatus == "active", desired: secondStatus == "active" ? "running" : "stopped", failCount: 0)
                                 ])
    }

    func version() async throws -> ServerVersion {
        try checkConnection()
        return ServerVersion(version: "界面测试版本", channel: "测试", revision: nil)
    }

    func pauseAccount(id: Int) async throws {
        try checkConnection()
        if id == 1 { firstStatus = "paused" }
        if id == 2 { secondStatus = "paused" }
        changed = true
        if mutationUncertain { throw APIError.network(outcomeUncertain: true) }
    }

    func resumeAccount(id: Int) async throws {
        try checkConnection()
        if id == 2, ProcessInfo.processInfo.arguments.contains("--ui-test-resume-rejected") {
            secondStatus = "login_required"
            throw APIError.server(status: 422, code: "ACCOUNT_SESSION_DECRYPT_FAILED")
        }
        if id == 1 { firstStatus = "active" }
        if id == 2 { secondStatus = "active" }
        changed = true
        if mutationUncertain { throw APIError.network(outcomeUncertain: true) }
    }

    func logout() async throws {}

    /// 长会话界面测试数据（`--ui-test-long-history`）：共 120 条消息，每页 40 条，按 before_id 往前翻。
    /// 用户消息与带 Markdown（标题、列表、代码块）的助手回答交替，最后一轮是“第 119 条提问 / 第 120 条回答”。
    static func longHistoryPage(before: Int?) -> JSONValue {
        let upper = min(before.map { $0 - 1 } ?? 120, 120)
        guard upper >= 1 else { return [] }
        let lower = max(1, upper - 39)
        return .array((lower...upper).map { id -> JSONValue in
            if id % 2 == 1 {
                return ["id": .integer(Int64(id)), "role": "user", "content": ["text": .string("第 \(id) 条提问：请检查账号与插件状态。")], "run_status": "completed"]
            }
            let text = "## 第 \(id) 条回答\n\n已检查完毕，以下是结果：\n\n- 账号运行正常\n- 插件全部启用\n- 最近一小时没有错误\n\n```\nstatus: ok\nchecked: \(id)\n```\n\n如需进一步排查，可以告诉我具体的群组或插件。"
            return ["id": .integer(Int64(id)), "role": "assistant", "content": ["text": .string(text)], "run_status": "completed",
                    "usage": ["provider_name": "测试供应商", "model": "fixture-model", "input_tokens": 800, "output_tokens": 120, "elapsed_ms": 900, "schema_version": 2]]
        })
    }
    /// 含超长行与不可断行 URL，用于检查代码对比视图换行、不横向溢出。
    static let agentSkillLongLine = "- 结果整理成表格，列为：标题、大小、做种/下载、优惠（Free/2xFree/50% 等）、发布时间。标题列写成加粗的详情页链接，不要再单独放“详情页”列。按做种数从高到低排列，同名多版本时优先显示官种。"
    static let agentSkillUpstreamMarkdown = (["---", "name: group-helper", "description: 群聊答疑助手", "---", "", "# 群聊答疑助手", "", "## PT 站点搜索", "",
                                              agentSkillLongLine.replacingOccurrences(of: "做种/下载、", with: ""), ""]
                                             + (1...12).map { "- 第 \($0) 条规则：保持简短。" }).joined(separator: "\n")
    static let agentSkillCurrentMarkdown = (["---", "name: group-helper", "description: 群聊答疑助手", "---", "", "# 群聊答疑助手", "", "## PT 站点种子搜索", "",
                                             agentSkillLongLine, "https://example.com/" + String(repeating: "a", count: 200), ""]
                                            + (1...12).map { "- 第 \($0) 条规则：保持简短。" }).joined(separator: "\n")

    /// Agent Skills 界面测试数据：一个已安装 skill、一个插件内置（有新默认版本）、一个 TelePilot 内置。
    static func agentSkillFixture(_ path: String, method: String, body: JSONValue?) -> JSONValue {
        let installed: JSONValue = [
            "name": "pdf-helper", "description": "拆分、合并与提取 PDF 文本的操作说明。", "builtin": false, "bundled": false,
            "version": "1.2.0", "editable": false, "uninstallable": true, "user_modified": false, "upstream_changed": false,
            "source_type": "github", "source_url": "https://github.com/example/skills/tree/main/pdf-helper", "source_ref": "main",
            "enabled_agent": true, "enabled_plugins": true, "plugin_supported": true, "updatable": true,
            "file_count": 2, "total_bytes": 5120, "skipped_files": [], "installed_at": "2026-10-25 20:00"]
        let bundled: JSONValue = [
            "name": "group-helper", "description": "群聊答疑助手的能力与行为准则。", "builtin": false, "bundled": true,
            "owner_plugin": "interaction-ai-chat", "editable": true, "uninstallable": false, "user_modified": true,
            "upstream_changed": true, "upstream_version": "0.4.0", "source_type": "plugin_bundled",
            "enabled_agent": false, "enabled_plugins": true, "plugin_supported": true, "updatable": false,
            "file_count": 1, "total_bytes": 2048, "skipped_files": []]
        let builtin: JSONValue = [
            "name": "telepilot-accounts", "description": "账号巡检与暂停恢复流程。", "builtin": true, "bundled": false,
            "editable": false, "uninstallable": false, "source_type": "builtin", "enabled_agent": true, "enabled_plugins": false,
            "plugin_supported": false, "updatable": false, "file_count": 0, "total_bytes": 0, "skipped_files": []]
        let all = [installed, bundled, builtin]
        if path == "/api/agent-skills" {
            if method == "POST" { return installed }
            return ["items": .array(all), "limits": ["max_files": 64, "max_file_bytes": 262_144, "max_skill_bytes": 1_048_576,
                                                       "max_archive_bytes": 10_485_760, "agent_load_max_chars": 24_000, "plugin_load_max_chars": 6000]]
        }
        let rest = path.dropFirst("/api/agent-skills/".count)
        let name = String(rest.split(separator: "/").first ?? "")
        let item = all.first { $0["name"]?.stringValue == name } ?? installed
        if rest.hasSuffix("/upstream") {
            return ["name": .string(name), "owner_plugin": "interaction-ai-chat", "user_modified": true, "upstream_changed": true,
                    "upstream_version": "0.4.0", "current_skill_md": .string(Self.agentSkillCurrentMarkdown),
                    "upstream_skill_md": .string(Self.agentSkillUpstreamMarkdown),
                    "diff": "--- 默认版本（插件内置）\n+++ 当前版本（你的修改）\n@@ -8 +8 @@\n-## PT 站点搜索\n+## PT 站点种子搜索"]
        }
        if rest.contains("/files/") { return ["name": .string(name), "path": "reference.md", "content": "参考资料示例。", "truncated": false] }
        if rest.hasSuffix("/update") { return ["skill": item, "changed": false] }
        if method == "DELETE" { return ["name": .string(name), "uninstalled": true, "unbound": []] }
        if method == "PATCH", var object = item.objectValue {
            for (key, value) in body?.objectValue ?? [:] { object[key] = value }
            return .object(object)
        }
        guard var object = item.objectValue else { return item }
        object["skill_md"] = .string("---\nname: \(name)\ndescription: 示例\n---\n\n# \(name)\n\n界面测试数据。")
        let files: JSONValue = name == "pdf-helper" ? [["path": "reference.md", "size": 1024]] : []
        object["files"] = files
        return .object(object)
    }

    func request(path: String, method: String, body: JSONValue?) async throws -> JSONValue {
        try checkConnection()
        // 客户端把路径里的 ID 按 .alphanumerics 百分号编码（“fixture-session”会变成“fixture%2Dsession”），
        // 与真实服务器一样先解码再匹配，否则按 ID 区分的界面测试数据（删除会话、运行阶段、排队消息）都不会命中。
        let rawPath = String(path.split(separator: "?").first ?? "")
        let clean = rawPath.removingPercentEncoding ?? rawPath
        if let value = fixtureValues[clean], method == "GET" { return value }
        if method == "GET" {
            let opsValue = Self.opsFixture(clean)
            if let rows = opsValue.arrayValue {
                let query = URLComponents(string: path)?.queryItems ?? []
                let filtered = rows.filter { row in
                    query.allSatisfy { item in
                        switch item.name {
                        case "verdict", "status": return row[item.name]?.stringValue == item.value
                        case "account_id": return row["account_id"]?.intValue == item.value.flatMap(Int.init)
                        case "level":
                            let level = row["level"]?.stringValue ?? ""
                            return item.value == "warning" ? ["warn", "warning", "error"].contains(level) : level == item.value
                        default: return true
                        }
                    }
                }
                let limit = query.first { $0.name == "limit" }?.value.flatMap(Int.init) ?? filtered.count
                return .array(Array(filtered.prefix(limit)))
            }
            if !opsValue.isNull { return opsValue }
        }
        if clean == "/api/system-agent/checkup" { return Self.checkupFixture }
        if clean == "/api/system-agent/sessions", method == "POST" {
            deletedSessions.remove("fixture-session")
            return ["id": "fixture-session", "title": body?["title"] ?? "新会话", "status": "active"]
        }
        if clean == "/api/system-agent/sessions" {
            // 删除后的会话不再出现在列表里（第八轮：删除最后一个会话后重进应显示空状态）。
            return deletedSessions.contains("fixture-session") ? [] : [["id": "fixture-session", "title": "服务检查", "status": "active", "channel": "web"]]
        }
        if clean.hasPrefix("/api/system-agent/sessions/"), method == "DELETE" {
            let id = String(clean.dropFirst("/api/system-agent/sessions/".count))
            deletedSessions.insert(id)
            return ["ok": true, "deleted": .string(id)]
        }
        if clean.hasPrefix("/api/system-agent/sessions/"),
           let id = clean.dropFirst("/api/system-agent/sessions/".count).split(separator: "/").first.map(String.init),
           deletedSessions.contains(id) {
            throw APIError.server(status: 404, code: "SESSION_NOT_FOUND")
        }
        if runPhases, let response = runPhaseResponse(clean, path: path, method: method, body: body) { return response }
        if clean.hasSuffix("/messages"), ProcessInfo.processInfo.arguments.contains("--ui-test-long-history") {
            let before = URLComponents(string: path)?.queryItems?.first { $0.name == "before_id" }?.value.flatMap(Int.init)
            return Self.longHistoryPage(before: before)
        }
        if clean.hasSuffix("/messages") {
            return [["id": 1, "role": "assistant", "content": ["text": "服务状态正常。这里是界面测试数据。"], "run_status": "completed",
                     "usage": ["provider_name": "测试供应商", "model": "fixture-model", "input_tokens": 1200, "output_tokens": 340,
                               "tool_calls": 2, "elapsed_ms": 3200, "schema_version": 2]]]
        }
        if clean == "/api/system-agent/actions" {
            return [["id": "fixture-action", "tool_name": "accounts.pause", "summary": "暂停备用账号", "status": "pending",
                     "arguments": ["account_id": 2], "session_id": "fixture-session"]]
        }
        if clean.contains("/system-agent/actions/") {
            return ["id": "fixture-action", "summary": "暂停备用账号", "status": method == "GET" ? "pending" : "confirmed",
                    "ok": true, "tool_name": "accounts.pause", "arguments": ["account_id": 2]]
        }
        if clean.contains("/runs") {
            if method == "POST" { return ["id": "fixture-run", "session_id": "fixture-session", "status": "running"] }
            if clean.hasSuffix("/events") { return [] }
            if clean == "/api/system-agent/runs" { return [] }
            return ["id": "fixture-run", "session_id": "fixture-session", "status": "succeeded"]
        }
        if clean == "/api/system-agent/capabilities" {
            return ["enabled": true, "provider_name": "测试供应商", "resolved_model": "openai/fixture-model", "model_matrix": [
                ["provider_id": 1, "provider_name": "测试供应商", "model": "openai/fixture-model", "enabled": true],
                ["provider_id": 1, "provider_name": "测试供应商", "model": "anthropic/fixture-model-with-a-much-longer-name", "enabled": true],
                // 第九轮：不同模型家族（模型选择器每行前显示家族 logo）；最后一个无法识别，显示中性通用图标。
                ["provider_id": 2, "provider_name": "个人模型网关", "model": "deepseek-v4-flash", "enabled": true],
                ["provider_id": 2, "provider_name": "个人模型网关", "model": "google/gemini-3.5-flash", "enabled": true],
                ["provider_id": 2, "provider_name": "个人模型网关", "model": "tencent/hy3-preview", "enabled": true],
                ["provider_id": 3, "provider_name": "自建网关", "model": "my-private-model", "enabled": true]
            ]]
        }
        if let match = clean.firstMatch(of: #/^\/api\/plugins\/install\/([^\/]+)\/(enable|disable)$/#), method == "POST" {
            pluginGlobalEnabled[String(match.1)] = match.2 == "enable"
            return ["key": .string(String(match.1)), "enabled": .bool(match.2 == "enable")]
        }
        if clean == "/api/plugins/installed-overview" || clean == "/api/plugins/installed" {
            let autoReplyAccounts: JSONValue = [
                ["account_id": 1, "account_name": "日常工作账号", "enabled": .bool(accountFeatureEnabled[1]?["auto_reply"] ?? true), "state": "active"],
                ["account_id": 2, "account_name": "备用账号", "enabled": .bool(accountFeatureEnabled[2]?["auto_reply"] ?? false), "state": "stopped"]
            ]
            var items: [JSONValue] = [
                ["key": "auto_reply", "display_name": "自动回复", "global_enabled": .bool(pluginGlobalEnabled["auto_reply"] ?? true), "version": "1.4.0", "signature_ok": true, "source": "builtin", "accounts": autoReplyAccounts],
                ["key": "content_assistant", "display_name": "内容整理助手", "global_enabled": .bool(pluginGlobalEnabled["content_assistant"] ?? true),
                 "version": .string(contentAssistantVersion), "signature_ok": true, "source": "git", "source_url": "https://github.com/example/telepilot-plugins.git",
                 "update": ["update_available": .bool(contentAssistantVersion != "2.2.0"), "latest_version": "2.2.0", "last_update_check_at": .string(Self.isoDate(hoursAgo: 3))]],
                ["key": "forward_guard", "display_name": "转发风控与来源保护", "global_enabled": .bool(pluginGlobalEnabled["forward_guard"] ?? false), "version": "0.9.2", "signature_ok": false, "source": "local"]
            ]
            for name in storeInstalledPlugins {
                items.append(["key": .string(name), "display_name": .string(Self.storeTitles[name] ?? name), "global_enabled": true, "version": "1.0.0",
                              "signature_ok": true, "source": "repo", "source_url": "https://github.com/example/telepilot-plugins.git"])
            }
            return .array(items)
        }
        if clean == "/api/plugins/auto_reply/config", method == "GET" {
            return ["plugin_key": "auto_reply", "config": ["max_per_hour": 40], "global_config": ["max_per_hour": 40]]
        }
        if let value = pluginUIFixture(clean, method: method, path: path) { return value }
        if clean.firstMatch(of: #/^\/api\/accounts\/\d+\/features\/[^\/]+\/config$/#) != nil {
            if method == "PATCH" {
                fixtureValues[clean + "#effective"] = body?["config"]
                return ["feature_key": "auto_reply", "enabled": true, "state": "active", "config": body?["config"] ?? [:]]
            }
            var effective: [String: JSONValue] = ["reply": "收到", "cooldown_seconds": 30, "max_per_hour": 40, "notify_owner": false]
            for (key, value) in fixtureValues[clean + "#effective"]?.objectValue ?? [:] { effective[key] = value }
            return .object(effective)
        }
        if let value = pluginStoreFixture(clean, method: method) { return value }
        if clean == "/api/feature-matrix" {
            let matrix: JSONValue = [
                "accounts": [
                    ["id": 1, "name": "日常工作账号", "features": ["auto_reply": "active", "content_assistant": "failed"],
                     "feature_enabled": ["auto_reply": true, "content_assistant": true]],
                    ["id": 2, "name": "备用账号", "features": ["auto_reply": "paused"], "feature_enabled": ["auto_reply": false]]
                ],
                "features": [
                    ["key": "auto_reply", "display_name": "自动回复", "usage": "按规则自动回复私聊与群组消息。",
                     "category": "automation", "version": "1.4.0", "latest_version": "1.4.0", "update_available": false,
                     "source_type": "builtin", "source_label": "TelePilot 内置", "is_builtin": true, "signature_ok": true,
                     "orphan": false, "runtime_availability": "ready", "permissions": ["send_message"],
                     "event_subscriptions": [["event_types": ["message", "command"]]],
                     "config_schema": ["type": "object", "properties": [
                        "reply": ["type": "string", "title": "默认回复", "description": "规则没有单独设置回复时使用。", "default": "收到"],
                        "cooldown_seconds": ["type": "integer", "title": "冷却秒数", "minimum": 0, "default": 30],
                        "max_per_hour": ["type": "integer", "title": "每小时上限", "minimum": 1, "default": 60, "level": "global"],
                        "notify_owner": ["type": "boolean", "title": "回复后通知我", "default": false, "level": "global"]
                     ]]],
                    ["key": "content_assistant", "display_name": "内容整理助手", "usage": "整理消息、提取摘要并交给 AI 处理。",
                     "category": "interactive", "version": .string(contentAssistantVersion), "latest_version": "2.2.0",
                     "update_available": .bool(contentAssistantVersion != "2.2.0"), "last_update_check_at": .string(Self.isoDate(hoursAgo: 3)),
                     "source_type": "remote", "source_label": "个人插件仓库", "is_builtin": false, "signature_ok": true,
                     "orphan": false, "permissions": ["call_llm"],
                     // 带分组、声明顺序与可选字段：只有“目标群组 ID”真正必填。
                     "config_schema": ["type": "object", "properties": [
                        "telepilot_provider": ["type": "string", "title": "固定 TelePilot Provider（可选）", "default": "", "x-ui-widget": "llm-provider-select",
                                               "x-ui-section": "AI 模型"],
                        "telepilot_model": ["type": "string", "title": "TelePilot 模型覆盖（可选）", "default": "", "x-ui-widget": "llm-model-select",
                                            "x-ui-provider-field": "telepilot_provider", "x-ui-model-modality": "text", "x-ui-section": "AI 模型"],
                        "enable_guest": ["type": "boolean", "title": "访客模式触发", "default": true, "x-ui-section": "触发方式"],
                        "bot_username": ["type": "string", "title": "交互 Bot 用户名（兜底，可选）", "default": "", "x-ui-section": "触发方式"],
                        "target_chat_id": ["type": "integer", "title": "目标群组 ID", "description": "插件工作的群组，必须填写。", "x-ui-section": "触发方式"],
                        "trigger_keywords": ["type": "string", "title": "触发关键词（可选）", "default": "", "x-ui-section": "触发方式"],
                        "max_tokens": ["type": "integer", "title": "最大输出 Token", "default": 800, "minimum": 64],
                        "rich_text": ["type": "boolean", "title": "Telegram 富文本", "default": true],
                        "pt_enabled": ["type": "boolean", "title": "启用 PT 站点数据源", "default": false, "x-ui-section": "PT 站点数据源"],
                        "pt_site_url": ["type": "string", "title": "站点地址", "default": "", "x-ui-section": "PT 站点数据源"],
                        "pt_user_agent": ["type": "string", "title": "User-Agent（可选）", "default": "", "x-ui-section": "PT 站点数据源"],
                        "help_template": ["type": "string", "title": "帮助提示", "default": "@我 + 问题即可提问。"],
                        "error_template": ["type": "string", "title": "模型调用失败提示", "default": "AI 暂时不可用，请稍后再试。"]
                     ],
                     "x-ui-property-order": ["telepilot_provider", "telepilot_model", "enable_guest", "bot_username", "target_chat_id", "trigger_keywords",
                                             "max_tokens", "rich_text", "pt_enabled", "pt_site_url", "pt_user_agent", "help_template", "error_template"],
                     "required": ["target_chat_id"]]],
                    ["key": "forward_guard", "display_name": "转发风控与来源保护", "usage": "过滤异常转发并保留来源。",
                     "category": "utility", "version": "0.9.2", "update_available": false,
                     "source_type": "local", "source_label": "本地导入", "is_builtin": false, "signature_ok": false,
                     "orphan": true, "lint_warnings": ["插件来源目录不存在"]],
                    Self.widgetGalleryFeature
                ]
            ]
            guard var object = matrix.objectValue, var features = object["features"]?.arrayValue else { return matrix }
            for name in storeInstalledPlugins {
                features.append(["key": .string(name), "display_name": .string(Self.storeTitles[name] ?? name), "usage": "从插件仓库安装的界面测试插件。",
                                 "category": "automation", "version": "1.0.0", "update_available": false, "source_type": "remote",
                                 "source_label": "个人插件仓库", "is_builtin": false, "signature_ok": true, "orphan": false, "permissions": ["send_message"]])
            }
            // 与服务器一致：按保存的展示顺序排列，未在顺序里的保持原顺序排在后面。
            let order = (fixtureValues["/api/feature-matrix/order"]?["keys"]?.arrayValue ?? []).compactMap(\.stringValue)
            if !order.isEmpty {
                features = features.enumerated().sorted { lhs, rhs in
                    let l = order.firstIndex(of: lhs.element["key"]?.stringValue ?? "") ?? Int.max
                    let r = order.firstIndex(of: rhs.element["key"]?.stringValue ?? "") ?? Int.max
                    return l == r ? lhs.offset < rhs.offset : l < r
                }.map(\.element)
            }
            object["features"] = .array(features)
            return .object(object)
        }
        if clean == "/api/feature-matrix/order" {
            if method == "PUT" {
                let keys = (body?["keys"]?.arrayValue ?? []).compactMap(\.stringValue)
                fixtureValues[clean] = ["keys": .array(keys.map { .string($0) })]
            }
            return fixtureValues[clean] ?? ["keys": []]
        }
        if let match = clean.firstMatch(of: #/^\/api\/accounts\/(\d+)\/features$/#), method == "GET" {
            let accountID = Int(match.1) ?? 1
            let autoReplyEnabled = accountFeatureEnabled[accountID]?["auto_reply"] ?? true
            return [
                ["feature_key": "auto_reply", "enabled": .bool(autoReplyEnabled), "state": autoReplyEnabled ? (accountID == 1 ? "active" : "pending") : "stopped", "last_error": nil, "config": ["reply": "收到"]],
                ["feature_key": "content_assistant", "enabled": true, "state": accountID == 1 ? "failed" : "active",
                 "last_error": accountID == 1 ? "模型供应商连接超时，请检查密钥与网络。" : nil, "config": [:]],
                ["feature_key": "forward_guard", "enabled": false, "state": "stopped", "last_error": nil, "config": [:]],
                ["feature_key": "widget_gallery", "enabled": true, "state": "active", "last_error": nil, "config": [:]]
            ]
        }
        if let match = clean.firstMatch(of: #/^\/api\/accounts\/(\d+)\/features\/([^\/]+)$/#), method == "PATCH" {
            fixtureValues[clean] = body
            let accountID = Int(match.1) ?? 1
            let featureKey = String(match.2)
            if let enabled = body?["enabled"]?.boolValue {
                accountFeatureEnabled[accountID, default: [:]][featureKey] = enabled
            }
            return ["feature_key": .string(String(match.2)), "enabled": body?["enabled"] ?? false, "state": body?["enabled"]?.boolValue == true ? "pending" : "stopped", "config": [:]]
        }
        if clean == "/api/agent-skills" || clean.hasPrefix("/api/agent-skills/") {
            return Self.agentSkillFixture(clean, method: method, body: body)
        }
        if clean == "/api/commands/llm-providers" && method == "GET" {
            return [
                ["id": 1, "name": "个人模型网关", "provider": "openai", "base_url": "https://llm.example.com/v1",
                 "default_model": "gpt-5.4-mini", "models": [["id": "gpt-5.4-mini", "enabled": true, "supports_tools": true, "supports_images": true,
                                                         "context_window": 400000, "max_output_tokens": 128000,
                                                         "supported_reasoning_levels": ["low", "medium", "high"], "metadata_source": "upstream"],
                                                        ["id": "gpt-5.4", "enabled": true]],
                 "has_api_key": true, "has_effective_key": true, "pool_keys": [["id": 1, "label": "日常密钥"]],
                 "key_strategy": "least_used", "api_format": "responses", "execution_backend": "direct", "protocol_profile": "standard",
                 "client_identity_profile": "auto", "web_search_api_format": "auto", "modality": "multimodal", "cost_tier": 2],
                ["id": 2, "name": "本地 Ollama", "provider": "ollama", "base_url": "http://ollama.internal:11434",
                 "default_model": "qwen3:14b", "models": [["id": "qwen3:14b", "enabled": true]],
                 "has_api_key": false, "has_effective_key": false, "pool_keys": [], "key_strategy": "order",
                 "api_format": "chat_completions", "execution_backend": "direct", "protocol_profile": "standard",
                 "client_identity_profile": "auto", "web_search_api_format": "auto", "modality": "text", "cost_tier": 1]
            ]
        }
        if clean == "/api/commands/llm-providers/runtime-health" {
            return [["provider_id": 1, "model": "gpt-5.4", "state": "cooling", "consecutive_failures": 2,
                     "last_error_class": "timeout", "last_error_message": "上游响应超时，冷却 32 秒。", "cooldown_remaining_seconds": 32]]
        }
        if clean == "/api/llm/usage/recent" {
            return ["items": [
                ["id": 1, "provider_name": "个人模型网关", "model": "gpt-5.4-mini", "source": "system_agent",
                 "success": true, "input_tokens": 1820, "output_tokens": 386, "latency_ms": 1420, "created_at": "2026-10-24T08:30:00Z"],
                ["id": 2, "provider_name": "个人模型网关", "model": "gpt-5.4", "source": "content_assistant",
                 "success": false, "error": "上游请求超时", "input_tokens": 920, "output_tokens": 0, "latency_ms": 30000, "created_at": "2026-10-24T08:20:00Z"]
            ], "summary": ["request_count": 2, "success_count": 1, "failed_count": 1, "total_tokens": 3126, "avg_latency_ms": 15710]]
        }
        if clean == "/api/llm/usage/plugins/summary" {
            return [["plugin_key": "content_assistant", "request_count": 12, "total_tokens": 32100, "failed_count": 1]]
        }
        if clean == "/api/commands/ai/enablement-summary" { return ["enabled_accounts": 1, "total_accounts": 4, "enabled_templates": 2] }
        if clean == "/api/commands/llm-providers/liveness/preview", method == "POST" {
            let scope = body?["models_by_provider"]?.objectValue ?? [:]
            let count = scope.values.reduce(0) { $0 + ($1.arrayValue?.count ?? 0) }
            return ["provider_total": .integer(Int64(scope.count)), "executable_provider_total": .integer(Int64(scope.count)),
                    "enabled_model_total": .integer(Int64(count)), "task_total": .integer(Int64(count)),
                    "needs_confirmation": false, "global_concurrency": 8, "provider_concurrency": 2, "max_tokens": 256, "max_output_tokens": 256,
                    "providers": [["provider_id": 1, "provider_name": "个人模型网关", "enabled_models": ["gpt-5.4-mini", "gpt-5.4"], "executable": true]]]
        }
        if clean == "/api/commands/llm-providers/liveness/run" { return ["run_id": "fixture-liveness", "status": "queued"] }
        if clean == "/api/commands/llm-providers/liveness/fixture-liveness" {
            return ["run_id": "fixture-liveness", "status": "completed", "task_total": 2, "completed": 2, "healthy": 1, "failed": 1, "skipped": 0,
                    "results": [["provider_id": 1, "provider_name": "个人模型网关", "model_id": "gpt-5.4-mini", "status": "healthy", "latency_ms": 620,
                                 "preview": "连接正常", "effective_api_format": "responses", "client_identity_profile": "openai_sdk"],
                                ["provider_id": 1, "provider_name": "个人模型网关", "model_id": "gpt-5.4", "status": "timeout", "latency_ms": 30000,
                                 "error": "上游响应超时", "suggestion": "稍后重试或调大超时秒数。", "effective_api_format": "responses"]]]
        }
        if clean == "/api/commands/templates", method == "GET" {
            return [
                ["id": 11, "name": "ai", "type": "ai", "description": "通用问答", "aliases": ["a", "ask"],
                 "config": ["mode": "chat", "provider_id": 1, "model": "gpt-5.4-mini", "routing_mode": "auto", "output_format": "html", "send_mode": "edit"],
                 "created_at": "2026-10-01T08:00:00Z"],
                ["id": 12, "name": "img", "type": "ai", "description": "生成图片", "aliases": [],
                 "config": ["mode": "image", "image_backend": "codex_image"], "created_at": "2026-10-01T08:00:00Z"],
                ["id": 13, "name": "hi", "type": "reply_text", "aliases": [], "config": ["text": "你好"], "created_at": "2026-10-01T08:00:00Z"]
            ]
        }
        if let match = clean.firstMatch(of: #/^\/api\/accounts\/(\d+)\/commands$/#), method == "GET" {
            let enabled = accountAICommands[Int(match.1) ?? 0] ?? []
            let names: [Int: String] = [11: "ai", 12: "img", 13: "hi"]
            return .array([11, 12, 13].map { (id: Int) -> JSONValue in
                .object(["template": .object(["id": .integer(Int64(id)), "name": .string(names[id] ?? "")]), "enabled": .bool(enabled.contains(id))])
            })
        }
        if let match = clean.firstMatch(of: #/^\/api\/accounts\/(\d+)\/commands\/(\d+)$/#), method == "POST" || method == "DELETE" {
            let accountID = Int(match.1) ?? 0, templateID = Int(match.2) ?? 0
            if method == "POST" { accountAICommands[accountID, default: []].insert(templateID) }
            else { accountAICommands[accountID, default: []].remove(templateID) }
            return ["ok": true]
        }
        if clean == "/api/commands/llm-providers/identity-versions" {
            return ["items": [["key": "codex_tui", "label": "Codex TUI", "current": "0.50.0", "default": "0.50.0", "registry": "npm:@openai/codex", "detectable": true],
                              ["key": "codex_desktop_core", "label": "Codex Desktop 核心", "current": "1.2.0", "default": "1.2.0",
                               "registry": "appcast:codex-desktop-core", "detectable": true]],
                    "profiles": [["profile": "codex_tui", "label": "Codex TUI", "description": "交互式 Codex TUI 的 Responses 身份。", "api_formats": ["responses"],
                                  "headers": [["name": "originator", "value": "codex_cli_rs", "description": "Codex TUI 或 Desktop 的来源标识。", "management": "fixed"]]]]]
        }
        if clean == "/api/commands/llm-providers/identity-versions/detect" {
            return ["items": [["key": "codex_tui", "current": "0.50.0", "latest": "0.51.0", "up_to_date": false],
                              ["key": "codex_desktop_core", "current": "1.2.0", "latest": "1.3.0-alpha.2.1", "up_to_date": false]]]
        }
        if clean.firstMatch(of: #/^\/api\/commands\/llm-providers\/(\d+)\/test-model$/#) != nil, method == "POST" {
            return ["ok": true, "latency_ms": 820, "model": body?["model"] ?? "gpt-5.4-mini", "preview": "你好"]
        }
        if let match = clean.firstMatch(of: #/^\/api\/commands\/llm-providers\/(\d+)\/chat-test-models$/#), method == "POST" {
            let models = (body?["models"]?.arrayValue ?? []).compactMap(\.stringValue)
            return ["provider_id": .integer(Int64(match.1) ?? 1), "provider_name": "个人模型网关",
                    "results": .array(models.map { id -> JSONValue in
                        ["ok": true, "requested_model": .string(id), "model": .string(id), "latency_ms": 1240,
                         "response": "你好！我是一个简洁的中文聊天助手。", "input_tokens": 42, "output_tokens": 18]
                    })]
        }
        if let match = clean.firstMatch(of: #/^\/api\/commands\/llm-providers\/(\d+)\/fetch-models$/#), method == "POST" {
            guard let provider = (try? await request(path: "/api/commands/llm-providers", method: "GET", body: nil))?.arrayValue?
                .first(where: { $0["id"]?.intValue == Int(match.1) }) else { return ["fetched": 0] }
            var object = provider.objectValue ?? [:]
            var models = provider["models"]?.arrayValue ?? []
            models.append(["id": "gpt-5.4-nano", "enabled": false, "metadata_source": "upstream"])
            object["models"] = .array(models)
            return ["fetched": 3, "provider": .object(object)]
        }
        if clean == "/api/commands/llm-providers/fetch-models-preview", method == "POST" {
            return ["fetched": 2, "ids": ["gpt-5.4-mini", "gpt-5.4"],
                    "models": [["id": "gpt-5.4-mini", "enabled": true], ["id": "gpt-5.4", "enabled": true]]]
        }
        if clean == "/api/commands/llm-providers/detect-protocols" {
            return ["chat_completions": ["ok": true, "latency_ms": 420, "status_code": 200],
                    "responses": ["ok": true, "latency_ms": 510, "status_code": 200, "client_identity_profile": "openai_sdk"],
                    "anthropic_messages": ["ok": false, "latency_ms": 120, "status_code": 404, "error": "接口不存在"],
                    "models": ["ok": true, "latency_ms": 200, "status_code": 200],
                    "recommended_api_format": "responses", "recommended_protocol_profile": "standard",
                    "recommended_client_identity_profile": "openai_sdk", "recommended_web_search_api_format": "responses"]
        }
        if clean == "/api/system/kill-switch" {
            if method == "POST" {
                let value: JSONValue = ["enabled": .bool(body?["enabled"]?.boolValue ?? false)]
                fixtureValues[clean] = value
                return value
            }
            return ["enabled": false]
        }
        if clean == "/api/system/update-check", method == "PATCH" {
            var current = fixtureUpdateCheck.objectValue ?? [:]
            for (key, value) in body?.objectValue ?? [:] { current[key] = value }
            fixtureUpdateCheck = .object(current)
        }
        if clean == "/api/system/update-status" || clean == "/api/system/update-check" {
            var status = fixtureUpdateCheck.objectValue ?? [:]
            status["last_check"] = ["has_update": true, "can_check": true, "checked_at": "2026-10-24T08:00:00.123456+00:00"]
            return .object(status)
        }
        if clean == "/api/system/update-target-options" {
            return ["ok": true, "remotes": ["origin"], "branches": ["main", "Beta"], "remote": "origin"]
        }
        if clean == "/api/system/check-update", method == "POST" {
            return ["has_update": true, "can_check": true, "current_version": "0.104.0-beta.1", "target_version": "0.105.0-beta.1",
                    "current_commit": "56246fd0a1b2c3d4", "remote_commit": "9a8b7c6d5e4f3a2b", "ahead": 2,
                    "remote": "origin", "branch": "Beta", "runtime_mode": "prod_container_with_updater", "action_required": "backend",
                    "plan_label": "后端增量更新", "plan_detail": "拉取镜像并重启后端，前端无需重建。",
                    "commit_titles": ["iOS：新增关于页与独立的服务器更新入口", "修复插件更新检查偶发超时"],
                    "requires_backup": true, "requires_migration": false, "requires_full_update": false, "can_apply": true]
        }
        if clean == "/api/system/pull-update", method == "POST" {
            return ["success": true, "job_id": "fixture-update", "status": "queued", "remote": "origin", "branch": "Beta",
                    "plan_label": "更新任务已启动", "plan_detail": "更新将在内部 updater 中执行，期间服务可能短暂重启；请观察任务日志。"]
        }
        if clean == "/api/system/update-jobs/fixture-update" {
            fixtureUpdateJobReads += 1
            if fixtureUpdateJobReads <= 4 {
                return ["ok": true, "job_id": "fixture-update", "status": "running", "progress": 40, "phase": "拉取镜像",
                        "detail": "正在拉取后端镜像", "logs": ["[updater] git fetch origin Beta", "[updater] 拉取镜像 telepilot-web"],
                        "step_timings": [["phase": "准备", "duration_ms": 1200]]]
            }
            return ["ok": true, "job_id": "fixture-update", "status": "succeeded", "progress": 100, "phase": "完成", "summary": "更新任务已完成。",
                    "new_commit": "9a8b7c6d5e4f3a2b", "logs": ["[updater] git fetch origin Beta", "[updater] 完成"]]
        }
        if clean == "/api/system/restart", method == "POST" { return ["success": true] }
        if clean == "/api/apns/config" { return ["enabled": false, "configured": false, "environment": "sandbox", "has_auth_key": false] }
        if clean == "/api/apns/devices", method == "POST" {
            return ["device_id": body?["device_id"] ?? "fixture-device", "bundle_id": body?["bundle_id"] ?? "", "environment": body?["environment"] ?? "sandbox",
                    "enabled": true, "categories": body?["categories"] ?? [], "last_error": nil]
        }
        if clean == "/api/apns/devices" { return [] }
        if clean == "/api/apns/test", method == "POST" { return ["ok": false, "attempted": 0, "accepted": 0, "failed": 0, "unregistered": 0, "message": "APNs 尚未启用", "device_id": body?["device_id"] ?? ""] }
        if clean == "/api/system/settings" {
            if method == "PATCH", let patch = body?.objectValue {
                var current = fixtureSettings.objectValue ?? [:]
                for (key, value) in patch {
                    if let nested = value.objectValue, var existing = current[key]?.objectValue {
                        for (nestedKey, nestedValue) in nested { existing[nestedKey] = nestedValue }
                        current[key] = .object(existing)
                    } else { current[key] = value }
                }
                fixtureSettings = .object(current)
            }
            return fixtureSettings
        }
        if clean == "/api/system/capabilities" { return ["modules": []] }
        if clean.contains("/import-config/preview") { return ["preview_signature": "fixture-signature", "warnings": [], "imported": 0] }
        if method == "GET" {
            if clean.contains("/config") || clean.contains("/health") || clean.contains("/network") || clean.contains("/status") {
                return [:]
            }
            return []
        }
        if let body { fixtureValues[clean] = body }
        return body ?? ["ok": true]
    }

    func upload(path: String, file: Data, filename: String, fields: [String: String]) async throws -> JSONValue {
        try await request(path: path, method: "POST", body: nil)
    }

    func download(path: String, method: String, body: JSONValue?) async throws -> Data {
        try await request(path: path, method: method, body: body).data()
    }

    /// `--ui-test-run-phases`：任务、排队与进度流。只返回服务器真实会给的字段（status／phase／queue_ahead、事件类型）。
    private func runPhaseResponse(_ clean: String, path: String, method: String, body: JSONValue?) -> JSONValue? {
        // 与进度流同一节奏推进 phase（每 1.2 秒一个阶段），读取任务时不会“跑在”事件前面。
        let elapsed = phaseRunStartedAt.map { Date().timeIntervalSince($0) } ?? 0
        let phases = ["starting", "understanding", "selecting_model", "thinking", "using_tool"]
        let phase = phases[min(phases.count - 1, Int(elapsed / 1.2))]
        let activeRun: JSONValue = ["id": "fixture-run", "session_id": "fixture-session", "status": "running", "phase": .string(phase),
                                    "cancel_requested": .bool(phaseRunCancelRequested), "last_seq": .integer(Int64(min(4, Int(elapsed / 1.2))))]
        if clean == "/api/system-agent/sessions/fixture-session/runs", method == "POST" {
            guard phaseRunActive else {
                phaseRunActive = true
                phaseRunStartedAt = Date()
                // 刚创建、尚未开始执行：queued，没有排在前面的任务（queue_ahead 为空）。
                return ["id": "fixture-run", "session_id": "fixture-session", "status": "queued", "phase": "queued", "last_seq": 0]
            }
            phaseQueueSerial += 1
            let serial = phaseQueueSerial
            phaseQueue.append(["id": .string("turn-\(serial)"), "session_id": "fixture-session", "run_id": .string("fixture-queued-\(serial)"),
                               "channel": "web", "kind": "message", "position": .integer(Int64(serial)), "status": "pending",
                               "content": body?["content"] ?? ""])
            return ["id": .string("fixture-queued-\(serial)"), "session_id": "fixture-session", "status": "queued", "phase": "queued",
                    "queue_ahead": .integer(Int64(phaseQueue.count)), "last_seq": 0]
        }
        if clean == "/api/system-agent/queue" { return .array(phaseQueue) }
        if let match = clean.firstMatch(of: #/^\/api\/system-agent\/queue\/([^\/]+)\/(steer|replace)$/#), method == "POST" {
            let turnID = String(match.1)
            guard let index = phaseQueue.firstIndex(where: { $0["id"]?.stringValue == turnID }) else { return nil }
            let item = phaseQueue[index]
            if match.2 == "steer" {
                phaseQueue.remove(at: index)
                return ["id": 41, "run_id": "fixture-run", "kind": "steer", "status": "pending", "client_request_id": body?["client_request_id"] ?? ""]
            }
            phaseQueue.remove(at: index)
            phaseQueue.insert(item, at: 0)
            phaseRunCancelRequested = true
            return ["id": item["run_id"] ?? "", "session_id": "fixture-session", "status": "queued", "phase": "queued", "queue_ahead": 1, "last_seq": 0]
        }
        if clean == "/api/system-agent/runs/fixture-run" { return phaseRunActive ? activeRun : nil }
        if clean == "/api/system-agent/runs", phaseRunActive { return [activeRun] }
        return nil
    }

    /// 进度流：每隔约 1.2 秒给出下一个阶段事件，之后保持连接（任务一直执行中），直到客户端取消订阅。
    private func streamRunPhases(path: String, onEvent: @escaping @Sendable (JSONValue) async -> Void) async throws {
        let after = URLComponents(string: path)?.queryItems?.first { $0.name == "after_seq" }?.value.flatMap(Int.init) ?? 0
        let events: [JSONValue] = [
            ["type": "run_started", "seq": 1, "run_id": "fixture-run"],
            ["type": "provider_selected", "seq": 2, "run_id": "fixture-run", "provider_name": "测试供应商", "model": "openai/fixture-model"],
            ["type": "model_attempt", "seq": 3, "run_id": "fixture-run", "attempt": 1],
            ["type": "tool_started", "seq": 4, "run_id": "fixture-run", "tool_name": "accounts.list", "tool_description": "读取账号列表"]
        ]
        for event in events where (event["seq"]?.intValue ?? 0) > after {
            try await Task.sleep(nanoseconds: 1_200_000_000)
            await onEvent(event)
        }
        for _ in 0..<240 {
            try await Task.sleep(nanoseconds: 500_000_000)
        }
    }

    func stream(path: String, onEvent: @escaping @Sendable (JSONValue) async -> Void) async throws {
        try checkConnection()
        if runPhases, (path.removingPercentEncoding ?? path).contains("/runs/fixture-run/stream") {
            try await streamRunPhases(path: path, onEvent: onEvent)
            return
        }
        await onEvent(["type": "assistant_message", "content": "服务器任务已完成。", "seq": 1, "run_id": "fixture-run"])
        await onEvent(["type": "done", "ok": true, "seq": 2, "run_id": "fixture-run"])
    }
    func cloudflareAccessConfig() async throws -> CloudflareAccessConfig { accessConfig }

    func hasStoredSession() async throws -> Bool { true }

    func setCloudflareAccess(_ credentials: CloudflareAccessCredentials?) async throws { accessConfig.serviceToken = credentials }

    func setCloudflareAccessSession(_ session: CloudflareAccessSession?) async throws { accessConfig.session = session }

    /// 界面测试不弹出系统登录窗：稍等片刻后直接“取回”一个 30 天后到期的测试令牌。
    func startCloudflareAccessLogin() async throws -> CloudflareAccessLoginTicket {
        CloudflareAccessLoginTicket(url: nil) { [self] in
            try await Task.sleep(nanoseconds: 300_000_000)
            let session = try CloudflareAccessSession(token: Self.fixtureAccessToken())
            try await self.setCloudflareAccessSession(session)
            return session
        }
    }

    private static func fixtureAccessToken() -> String {
        func encode(_ json: String) -> String {
            Data(json.utf8).base64EncodedString().replacingOccurrences(of: "+", with: "-")
                .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
        }
        let exp = Int(Date().timeIntervalSince1970) + 30 * 24 * 3600
        return encode(#"{"alg":"RS256","typ":"JWT"}"#) + "."
            + encode(#"{"aud":["fixture-aud"],"email":"owner@example.com","exp":\#(exp)}"#) + ".c2lnbmF0dXJl"
    }

    func clearLocalSession() async throws {
        cleanupAttempts += 1
        if ProcessInfo.processInfo.arguments.contains("--ui-test-cleanup-failure"), cleanupAttempts == 1 {
            throw APIError.secureStorage
        }
    }

    private func firstAccount() -> Account {
        Account(id: 1, phone: "+10000000001", displayName: "日常工作账号", tgUsername: "telepilot_ui_test", status: firstStatus,
                tags: ["日常", "工作"], enabledFeatures: 3, notes: "这是一条界面测试记录，不连接 Telegram 或真实服务器。",
                createdAt: "2026-10-01T08:00:00Z", proxyLabel: "测试代理")
    }

    /// 日志、台账与概览的界面测试数据。日期固定，金额用字符串以匹配后端 Decimal 输出。
    private static func opsFixture(_ path: String) -> JSONValue {
        switch path {
        case "/api/logs/runtime":
            return [
                ["id": 3, "ts": "2026-10-24T08:40:00.123456+00:00", "created_at": "2026-10-24T08:40:00Z", "account_id": 1, "level": "error",
                 "source": "plugin", "message": "内容整理助手调用模型超时（30 秒）", "detail": ["plugin_key": "content_assistant", "trace_id": "fixture-trace-2"]],
                ["id": 2, "ts": "2026-10-24T08:20:00Z", "created_at": "2026-10-24T08:20:00Z", "account_id": 2, "level": "warn",
                 "source": "system", "message": "备用账号连接中断，正在重连", "detail": [:]],
                ["id": 1, "ts": "2026-10-24T08:00:00Z", "created_at": "2026-10-24T08:00:00Z", "account_id": 1, "level": "info",
                 "source": "system", "message": "界面日志测试 sk-proj-THIS_IS_A_FAKE_SECRET_FOR_UI_TEST_123456", "detail": nil]
            ]
        case "/api/logs/runtime/stats":
            return ["total": 3, "debug": 0, "info": 1, "warn": 1, "error": 1, "by_account": [], "by_source": [], "cached": false]
        case "/api/logs/messages", "/api/logs/trace/events":
            return [
                ["id": 2, "trace_id": "fixture-trace-2", "account_id": 1, "source_channel": "userbot", "event_type": "message",
                 "chat_id": -1001234567, "chat_title": "测试群", "sender_user_id": 42, "sender_name": "小王", "text_preview": "帮我总结一下今天的讨论",
                 "status": "failed", "started_at": "2026-10-24T08:39:30Z", "duration_ms": 30120, "plugin_count": 1,
                 "plugin_keys": ["content_assistant"], "action_count": 0, "error_count": 1,
                 "funel": ["received": "pass", "routed": "pass", "ran": "fail", "sent": "none", "verdict": "failed", "stuck_at": nil,
                           "reason_code": "plugin_runtime_error", "reason_text": "插件运行异常：模型请求超时", "next_step": "检查 AI 供应商是否可用，或在插件中心查看错误详情。"],
                 "verdict": "failed", "stuck_at": nil, "reason_code": "plugin_runtime_error",
                 "reason_text": "插件运行异常：模型请求超时", "next_step": "检查 AI 供应商是否可用，或在插件中心查看错误详情。"],
                ["id": 1, "trace_id": "fixture-trace-1", "account_id": 1, "source_channel": "userbot", "event_type": "command",
                 "chat_id": 10086, "sender_user_id": 7, "sender_name": "老李", "text_preview": ".ping", "status": "ok",
                 "started_at": "2026-10-24T08:10:00Z", "duration_ms": 180, "plugin_count": 1, "plugin_keys": ["auto_reply"],
                 "action_count": 1, "error_count": 0,
                 "funel": ["received": "pass", "routed": "pass", "ran": "pass", "sent": "pass", "verdict": "responded", "stuck_at": nil,
                           "reason_code": "command_matched", "reason_text": "指令已命中并回复", "next_step": "无需处理。"],
                 "verdict": "responded", "stuck_at": nil, "reason_code": "command_matched", "reason_text": "指令已命中并回复", "next_step": "无需处理。"]
            ]
        case "/api/logs/audit":
            return [["id": 1, "ts": "2026-10-24T07:30:00Z", "user_id": 1, "action": "account.pause", "target": "account:2", "detail": ["reason": "界面测试"]]]
        case "/api/ledger/summary":
            return ["income": "1280.50", "payout": "860", "net": "420.50", "count": 18,
                    "by_day": [
                        ["key": "2026-10-20", "label": "2026-10-20", "income": "200", "payout": "150", "net": "50", "count": 3],
                        ["key": "2026-10-21", "label": "2026-10-21", "income": "320.50", "payout": "100", "net": "220.50", "count": 4],
                        ["key": "2026-10-22", "label": "2026-10-22", "income": "80", "payout": "260", "net": "-180", "count": 3],
                        ["key": "2026-10-23", "label": "2026-10-23", "income": "400", "payout": "200", "net": "200", "count": 5],
                        ["key": "2026-10-24", "label": "2026-10-24", "income": "280", "payout": "150", "net": "130", "count": 3]
                    ],
                    "by_chat": [
                        ["key": "-1001234567", "label": "测试群", "income": "900", "payout": "500", "net": "400", "count": 12],
                        ["key": "-1007654321", "label": "活动群", "income": "380.50", "payout": "360", "net": "20.50", "count": 6]
                    ],
                    "by_recipient": [
                        ["key": "42", "label": "小王", "user_id": 42, "username": "xiaowang", "received": "500", "income": "0", "payout": "500", "count": 4]
                    ]]
        case "/api/ledger/stats":
            return ["total": ["started_sessions": 26, "participant_count": 11, "payout_success_count": 9, "payout_failure_count": 1,
                              "payout_attempt_count": 10, "payout_success_rate": "90.00", "ledger_income": "1280.50", "ledger_payout": "860",
                              "ledger_net": "420.50", "ledger_count": 18],
                    "by_day": [], "by_chat": [], "source_matrix": []]
        case "/api/ledger":
            return ["items": [
                ["id": 2, "source": "action_event", "source_id": 2, "direction": "out", "amount": "150", "signed_amount": "-150", "status": "OK",
                 "account_id": 1, "chat_id": -1001234567, "chat_title": "测试群", "receiver_user_id": 42, "receiver_name": "小王",
                 "plugin_key": "lucky_game", "action_type": "payout", "channel": "userbot", "created_at": "2026-10-24T08:30:00Z", "params_summary": ["round": 12]],
                ["id": 1, "source": "action_event", "source_id": 1, "direction": "in", "amount": "280", "signed_amount": "280", "status": "OK",
                 "account_id": 1, "chat_id": -1001234567, "chat_title": "测试群", "payer_user_id": 7, "payer_name": "老李",
                 "plugin_key": "lucky_game", "action_type": "payment_confirmed", "channel": "userbot", "created_at": "2026-10-24T08:00:00Z", "params_summary": [:]]
            ]]
        case "/api/ledger/compensations":
            return ["items": [
                ["id": 1, "payout_key": "fixture-payout-1", "account_id": 1, "trace_id": "fixture-trace-2", "plugin_key": "lucky_game", "origin": "payout",
                 "chat_id": -1001234567, "chat_title": "测试群", "receiver_user_id": 42, "receiver_name": "小王", "amount": "50", "status": "pending",
                 "error_code_last": "FLOOD_WAIT", "error_last": "Telegram 限流，稍后重试", "ambiguous": false, "retry_count": 2,
                 "next_attempt_at": "2026-10-24T09:00:00Z", "created_at": "2026-10-24T08:31:00Z", "updated_at": "2026-10-24T08:45:00Z"]
            ]]
        default:
            if path.hasPrefix("/api/logs/trace/events/") {
                return ["id": 2, "trace_id": "fixture-trace-2", "account_id": 1, "source_channel": "userbot", "event_type": "message",
                        "chat_id": -1001234567, "chat_title": "测试群", "sender_user_id": 42, "sender_name": "小王",
                        "text_preview": "帮我总结一下今天的讨论", "status": "failed", "started_at": "2026-10-24T08:39:30Z", "duration_ms": 30120,
                        "spans": [
                            ["id": 1, "span_id": "s1", "trace_id": "fixture-trace-2", "phase": "receive", "status": "ok", "started_at": "2026-10-24T08:39:30Z", "duration_ms": 4],
                            ["id": 2, "span_id": "s2", "trace_id": "fixture-trace-2", "phase": "subscription_match", "plugin_key": "content_assistant", "status": "ok",
                             "reason_code": "matched", "started_at": "2026-10-24T08:39:30.010Z", "duration_ms": 6],
                            ["id": 3, "span_id": "s3", "trace_id": "fixture-trace-2", "phase": "plugin_invoke", "plugin_key": "content_assistant", "status": "failed",
                             "reason_code": "plugin_runtime_error", "message": "模型请求超时", "started_at": "2026-10-24T08:39:30.020Z", "duration_ms": 30100]
                        ],
                        "actions": [],
                        "related_runtime_logs": [
                            ["id": 3, "ts": "2026-10-24T08:40:00Z", "created_at": "2026-10-24T08:40:00Z", "account_id": 1, "level": "error",
                             "source": "plugin", "message": "内容整理助手调用模型超时（30 秒）", "detail": ["plugin_key": "content_assistant"]]
                        ]]
            }
            return .null
        }
    }

    static let storeTitles = ["weather_digest": "天气早报", "dice_party": "骰子派对", "hello_world": "示例：你好世界"]

    /// 一键体检（GET /api/system-agent/checkup）：一项异常、一项需关注，其余正常。
    static var checkupFixture: JSONValue {
        let restart: JSONValue = ["severity": "error", "text": "账号 备用账号 的 Worker 未运行：先看原因，再重启（需确认）。",
                                  "tool": "accounts.restart_worker", "requires_confirmation": true, "section": "accounts", "order": 1]
        let logs: JSONValue = ["severity": "warn", "text": "最近 24 小时有 3 条错误日志，最常见：连接超时。",
                               "tool": "logs.search_errors", "requires_confirmation": false, "section": "errors", "order": 2]
        func ok(_ key: String, _ title: String, _ finding: String) -> JSONValue {
            ["key": .string(key), "title": .string(title), "status": "ok", "finding": .string(finding), "facts": [:], "next_steps": [], "elapsed_ms": 12]
        }
        return [
            "kind": "system_checkup", "checked_at": .string(isoDate(hoursAgo: 0)), "window_hours": 24,
            "verdict": "error", "verdict_text": "发现异常：8 项正常、1 项需关注、1 项异常、0 项未能检查",
            "counts": ["ok": 8, "warn": 1, "error": 1, "unknown": 0], "unknown_sections": [],
            "next_steps": [restart, logs],
            "sections": [
                ok("health", "基础设施（数据库 / Redis / 运行时组件）", "数据库、Redis 与运行时组件均正常。"),
                ok("versions", "版本", "当前 0.106.0-beta.9，已是最新。"),
                ok("safety", "总闸与平台模块", "全局总闸未开启，平台模块正常。"),
                ["key": "accounts", "title": "账号与 Worker", "status": "error", "finding": "2 个账号中 1 个 Worker 未运行。",
                 "facts": [:], "next_steps": [restart], "elapsed_ms": 30],
                ["key": "errors", "title": "错误日志", "status": "warn", "finding": "最近 24 小时 3 条错误。",
                 "facts": [:], "next_steps": [logs], "elapsed_ms": 25],
                ok("providers", "模型 Provider", "2 个供应商可调用。"),
                ok("system_agent", "系统助手配置", "系统助手已启用。"),
                ok("scheduler", "定时任务", "没有逾期的定时任务。"),
                ok("plugins", "插件", "16 个插件运行正常。"),
                ok("notifications", "通知与推送", "APNs 推送已配置。")
            ],
            "elapsed_ms": 812, "business_changed": false,
            "note": "只读体检，未修改任何配置、未调用上游模型。建议中的写操作均需用户确认后执行。"
        ]
    }

    static func isoDate(hoursAgo: Double) -> String {
        ISO8601DateFormatter().string(from: Date().addingTimeInterval(-hoursAgo * 3600))
    }

    /// 控件契约示例插件（iOS 0.14.0）：契约里 iOS 新补的控件、密钥列表、未知控件，以及插件原生卡片。
    static let widgetGalleryFeature: JSONValue = [
        "key": "widget_gallery", "display_name": "控件示例", "usage": "用于界面测试的声明式控件与卡片示例。",
        "category": "utility", "version": "1.0.0", "update_available": false, "source_type": "remote", "source_label": "个人插件仓库",
        "is_builtin": false, "signature_ok": true, "orphan": false, "permissions": [],
        "config_schema": ["type": "object", "properties": [
            "reply_rules": ["type": "array", "title": "回复规则", "x-ui-widget": "config-list", "x-ui-title-field": "name",
                      "x-ui-summary": "{keyword} → {reply}", "x-ui-add-label": "添加规则",
                      "items": ["type": "object", "properties": [
                        "name": ["type": "string", "title": "名称"], "enabled": ["type": "boolean", "title": "启用", "default": true],
                        "keyword": ["type": "string", "title": "关键词"], "reply": ["type": "string", "title": "回复"]]]],
            "target_chats": ["type": "array", "title": "生效会话", "x-ui-widget": "allowed-peer-multi-select", "items": ["type": "integer"]],
            "weekdays": ["type": "array", "title": "生效星期", "x-ui-widget": "multi-select",
                         "items": ["type": "string", "enum": ["mon", "tue", "wed"], "enumNames": ["周一", "周二", "周三"]]],
            "handle_mode": ["type": "string", "title": "处理模式", "x-ui-widget": "list-select", "enum": ["fast", "safe"],
                     "enumNames": ["快速", "稳妥"], "enumDescriptions": ["优先速度", "优先稳定"], "default": "safe"],
            "tokens": ["type": "array", "title": "Token 池", "x-secret-list": ["secret_field": "token", "note_field": "note"],
                       "items": ["type": "object", "properties": ["token": ["type": "string"], "note": ["type": "string"]]]],
            "palette": ["type": "string", "title": "主题色", "x-ui-widget": "color-wheel", "default": "#ff8800"]
        ], "x-ui-property-order": ["reply_rules", "target_chats", "weekdays", "handle_mode", "tokens", "palette"]]
    ]

    /// 插件原生卡片（GET /api/plugin-ui/cards、POST …/data、POST …/actions/{id}）与控件契约。
    private func pluginUIFixture(_ clean: String, method: String, path: String) -> JSONValue? {
        if clean == "/api/plugins/ui-contract", method == "GET" {
            return ["widgets": [["id": "color-wheel", "x_ui_widget": "color-wheel", "ios_since": "0.15.0"]]]
        }
        if clean.firstMatch(of: #/^\/api\/accounts\/\d+\/ignored-peers$/#) != nil, method == "GET" {
            return [["id": 1, "account_id": 1, "peer_id": -1001, "peer_kind": "group", "peer_label": "测试群 A", "added_at": .string(Self.isoDate(hoursAgo: 5))],
                    ["id": 2, "account_id": 1, "peer_id": -1002, "peer_kind": "group", "peer_label": "测试群 B", "added_at": .string(Self.isoDate(hoursAgo: 4))]]
        }
        if clean == "/api/plugins/widget_gallery/config", method == "GET" {
            return ["plugin_key": "widget_gallery", "config": [:], "global_config": [:]]
        }
        if clean.firstMatch(of: #/^\/api\/accounts\/\d+\/features\/widget_gallery\/config$/#) != nil, method == "GET" {
            var effective: [String: JSONValue] = [
                "reply_rules": [["name": "问候", "enabled": true, "keyword": "你好", "reply": "你好呀"]],
                "target_chats": [-1001], "weekdays": ["mon"], "handle_mode": "safe", "palette": "#ff8800",
                "tokens": [["token": "abcd···wxyz", "note": "主号", "secret_id": "token:0123456789"]]
            ]
            for (key, value) in fixtureValues[clean + "#effective"]?.objectValue ?? [:] { effective[key] = value }
            return .object(effective)
        }
        guard clean.hasPrefix("/api/plugin-ui/cards") else { return nil }
        if clean == "/api/plugin-ui/cards", method == "GET" {
            let query = URLComponents(string: path)?.queryItems ?? []
            let slot = query.first { $0.name == "slot" }?.value ?? ""
            let plugin = query.first { $0.name == "plugin_key" }?.value
            guard slot == "plugin.overview", plugin == "widget_gallery" else { return ["slot": .string(slot), "cards": [], "warnings": []] }
            let statsBlocks: JSONValue = [
                ["type": "stats", "items": [["label": "调用", "path": "summary.calls", "format": "number"],
                                            ["label": "失败", "path": "summary.failed", "format": "number", "tone_path": "summary.failed_tone"]]],
                ["type": "table", "rows_path": "hooks", "row_key": "key", "columns": [
                    ["key": "hook", "label": "钩子"], ["key": "health", "label": "状态", "tone_key": "health_tone"],
                    ["key": "calls", "label": "调用", "format": "number"], ["key": "failure_rate", "label": "失败率", "format": "percent"]]],
                ["type": "buttons", "items": [["id": "reset_stats", "label": "清零", "style": "destructive", "confirm": "清零这个插件的运行统计和弃用计数？"]]]
            ]
            let stats: JSONValue = ["plugin_key": "widget_gallery", "plugin_name": "控件示例", "card_id": "_host.stats", "slot": "plugin.overview",
                                    "title": "运行统计", "subtitle": "按钩子统计调用数、耗时与失败数，保留到最近一次调用后 7 天",
                                    "source_kind": "read", "host": true, "blocks": statsBlocks]
            let quota: JSONValue = ["plugin_key": "widget_gallery", "plugin_name": "控件示例", "card_id": "quota", "slot": "plugin.overview", "title": "今日配额",
                                    "source_kind": "action", "host": false, "blocks": [["type": "kv", "items": [["label": "剩余", "path": "left", "format": "number"]]]]]
            let broken: JSONValue = ["plugin_key": "widget_gallery", "plugin_name": "控件示例", "card_id": "broken", "slot": "plugin.overview", "title": "外部服务",
                                     "source_kind": "action", "host": false, "blocks": [["type": "kv", "items": [["label": "状态", "path": "status"]]]]]
            return ["slot": .string(slot), "warnings": [], "cards": [stats, quota, broken]]
        }
        if method == "POST", clean.hasSuffix("/_host.stats/actions/reset_stats") {
            fixtureValues["plugin-ui#stats-reset"] = true
            return ["ok": true, "message": "已清零运行统计", "refresh": true]
        }
        if method == "POST", clean.hasSuffix("/_host.stats/data") {
            let reset = fixtureValues["plugin-ui#stats-reset"]?.boolValue == true
            let hooks: JSONValue = [
                ["key": "on_event", "hook": "事件处理", "health": "有失败", "health_tone": "warning", "calls": 120, "failure_rate": 0.0167],
                ["key": "on_config_action", "hook": "配置动作", "health": "失败偏多", "health_tone": "danger", "calls": 2, "failure_rate": 0.5]
            ]
            let summary: JSONValue = reset ? ["calls": 0, "failed": 0, "failed_tone": "neutral"] : ["calls": 122, "failed": 2, "failed_tone": "danger"]
            let data: JSONValue = ["summary": summary, "hooks": reset ? JSONValue.array([]) : hooks]
            return ["plugin_key": "widget_gallery", "card_id": "_host.stats", "status": "ok", "message": nil, "updated_at": 1_784_635_200, "duration_ms": 3, "data": data]
        }
        if method == "POST", clean.hasSuffix("/quota/data") {
            return ["plugin_key": "widget_gallery", "card_id": "quota", "status": "ok", "message": nil, "updated_at": 1_784_635_200, "duration_ms": 5, "data": ["left": 42]]
        }
        if method == "POST", clean.hasSuffix("/data") {
            return ["plugin_key": "widget_gallery", "card_id": "broken", "status": "error", "data": nil, "message": "已停止工作：读取超时（3 秒）",
                    "updated_at": 1_784_635_200, "duration_ms": 3000]
        }
        return nil
    }

    /// 插件商店（发现 / 已安装 / 产品页）所需的接口：仓库目录、安装包清单、安装历史、更新日志、安装与更新。
    private func pluginStoreFixture(_ clean: String, method: String) -> JSONValue? {
        let repoURL: JSONValue = "https://github.com/example/telepilot-plugins.git"
        switch (method, clean) {
        case ("GET", "/api/plugins/installed-packages"):
            var items: [JSONValue] = [
                ["key": "content_assistant", "source": "git", "source_url": repoURL, "version": .string(contentAssistantVersion), "enabled": true,
                 "signature_ok": true, "installed_path": "", "installed_at": .string(Self.isoDate(hoursAgo: 24 * 40)),
                 "manifest": ["author": "TelePilot 社区", "description": "把群聊里的长消息整理成摘要，并交给 AI 回答追问。", "min_telepilot_version": "0.100.0"]],
                ["key": "forward_guard", "source": "local", "source_url": "local://forward_guard", "version": "0.9.2", "enabled": false,
                 "signature_ok": false, "installed_path": "", "installed_at": .string(Self.isoDate(hoursAgo: 24 * 10)),
                 "manifest": ["author": "本机", "description": "过滤异常转发，保留原始来源。"]]
            ]
            for name in storeInstalledPlugins {
                items.append(["key": .string(name), "source": "repo", "source_url": repoURL, "version": "1.0.0", "enabled": true, "signature_ok": true,
                              "installed_path": "", "installed_at": .string(Self.isoDate(hoursAgo: 0)), "manifest": ["author": "TelePilot 社区"]])
            }
            return .array(items)
        case ("GET", "/api/plugin-repos"):
            return [["id": 1, "name": "个人插件仓库", "url": repoURL, "description": "", "auth_type": "none", "has_credentials": false]]
        case ("GET", "/api/plugin-repos/1/plugins"):
            let changelog = "# 更新日志\n\n## 2.2.0 (2026-10-25)\n- 新增「追问」按钮，可以就摘要继续提问。\n- 摘要支持 Telegram 富文本。\n\n## 2.1.0 (2026-09-30)\n- 修复长消息被截断。\n"
            var items: [JSONValue] = [
                ["name": "content_assistant", "display_name": "内容整理助手", "description": "把群聊里的长消息整理成摘要，并交给 AI 回答追问。",
                 "author": "TelePilot 社区", "version": "2.2.0", "installed": true, "installed_version": .string(contentAssistantVersion), "installed_source": "git",
                 "installed_source_url": repoURL, "source_matches": true, "update_available": .bool(contentAssistantVersion != "2.2.0"),
                 "permissions": ["call_llm", "send_message"], "tags": ["ai", "summary"], "category": "interactive", "min_telepilot_version": "0.100.0",
                 "changelog": .string(changelog), "event_subscriptions": [], "capabilities": [:], "subdir": "content_assistant"]
            ]
            for name in ["weather_digest", "dice_party"] {
                let installed = storeInstalledPlugins.contains(name)
                items.append(["name": .string(name), "display_name": .string(Self.storeTitles[name] ?? name),
                              "description": name == "weather_digest" ? "每天早上把天气和出行提醒发到指定会话。" : "群里掷骰子比大小的小游戏。",
                              "author": "TelePilot 社区", "version": "1.0.0", "installed": .bool(installed), "installed_version": installed ? .string("1.0.0") : .null,
                              "source_matches": .bool(installed), "update_available": false, "permissions": ["send_message"], "tags": [],
                              "category": name == "weather_digest" ? "automation" : "interactive",
                              "changelog": "## 1.0.0 (2026-10-20)\n- 首个版本。", "event_subscriptions": [], "capabilities": [:], "subdir": .string(name)])
            }
            return .array(items)
        case ("GET", "/api/plugin-repos/local/plugins"):
            return [["name": "hello_world", "display_name": "示例：你好世界", "description": "最小的插件示例。", "author": "TelePilot", "version": "0.1.0",
                     "installed": false, "permissions": [], "tags": ["example"], "category": "utility", "event_subscriptions": [], "capabilities": [:], "subdir": "hello_world"]]
        case ("POST", "/api/remote-plugins/check-updates"):
            return ["total": 1, "checked": 1, "update_available": .integer(contentAssistantVersion == "2.2.0" ? 0 : 1), "failed": 0]
        case ("POST", "/api/remote-plugins/content_assistant/update"):
            contentAssistantVersion = "2.2.0"
            contentAssistantUpdatedAt = Date()
            return ["id": 1, "name": "content_assistant", "display_name": "内容整理助手", "version": "2.2.0", "enabled": true]
        case ("GET", "/api/plugins/install/content_assistant/history"):
            guard let date = contentAssistantUpdatedAt else { return [] }
            return [["id": 9, "plugin_key": "content_assistant", "event_type": "updated", "version": "2.2.0", "previous_version": "2.1.0",
                     "created_at": .string(ISO8601DateFormatter().string(from: date))]]
        case ("GET", "/api/plugins/install/forward_guard/history"):
            return [["id": 8, "plugin_key": "forward_guard", "event_type": "updated", "version": "0.9.2", "previous_version": "0.9.1",
                     "created_at": .string(Self.isoDate(hoursAgo: 50))]]
        case ("GET", "/api/plugins/install/content_assistant/changelog"):
            let content = contentAssistantVersion == "2.2.0"
                ? "## 2.2.0 (2026-10-25)\n- 新增「追问」按钮，可以就摘要继续提问。\n\n## 2.1.0 (2026-09-30)\n- 修复长消息被截断。"
                : "## 2.1.0 (2026-09-30)\n- 修复长消息被截断。"
            return ["key": "content_assistant", "available": true, "content": .string(content), "truncated": false]
        case ("GET", "/api/plugins/install/forward_guard/changelog"):
            return ["key": "forward_guard", "available": true, "content": "## 0.9.2 (2026-10-24)\n- 修复来源校验误判。", "truncated": false]
        default:
            break
        }
        if method == "GET", clean.hasPrefix("/api/plugins/install/"), clean.hasSuffix("/history") { return [] }
        if method == "GET", clean.hasPrefix("/api/plugins/install/"), clean.hasSuffix("/changelog") {
            return ["available": false, "content": "", "message": "该插件未提供 CHANGELOG.md。"]
        }
        if method == "POST", let match = clean.firstMatch(of: #/^\/api\/plugin-repos\/(?:1|local)\/plugins\/([^\/]+)\/install$/#) {
            let name = String(match.1)
            if !storeInstalledPlugins.contains(name) { storeInstalledPlugins.append(name) }
            return ["id": 2, "name": .string(name), "display_name": .string(Self.storeTitles[name] ?? name), "version": "1.0.0", "enabled": true]
        }
        return nil
    }

    private func checkConnection() throws {
        if offline { throw APIError.network(outcomeUncertain: false) }
    }
}
#endif
