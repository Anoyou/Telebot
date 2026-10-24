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
    private var pluginGlobalEnabled: [String: Bool] = ["auto_reply": true, "content_assistant": true, "forward_guard": false]

    init(offline: Bool = false) {
        self.offline = offline
        mutationUncertain = ProcessInfo.processInfo.arguments.contains("--ui-test-mutation-uncertain")
        refetchError = ProcessInfo.processInfo.arguments.contains("--ui-test-refetch-error")
    }

    func login(_ request: LoginRequest) async throws -> LoginResult {
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
    func request(path: String, method: String, body: JSONValue?) async throws -> JSONValue {
        try checkConnection()
        let clean = String(path.split(separator: "?").first ?? "")
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
        if clean == "/api/system-agent/sessions", method == "POST" {
            return ["id": "fixture-session", "title": body?["title"] ?? "新会话", "status": "active"]
        }
        if clean == "/api/system-agent/sessions" {
            return [["id": "fixture-session", "title": "服务检查", "status": "active", "channel": "web"]]
        }
        if clean.hasPrefix("/api/system-agent/sessions/"), method == "DELETE" {
            return ["ok": true, "deleted": .string(String(clean.dropFirst("/api/system-agent/sessions/".count)))]
        }
        if clean.hasSuffix("/messages") {
            return [["id": 1, "role": "assistant", "content": ["text": "服务状态正常。这里是界面测试数据。"], "run_status": "completed"]]
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
        if clean == "/api/system-agent/capabilities" { return ["enabled": true, "model_matrix": []] }
        if let match = clean.firstMatch(of: #/^\/api\/plugins\/install\/([^\/]+)\/(enable|disable)$/#), method == "POST" {
            pluginGlobalEnabled[String(match.1)] = match.2 == "enable"
            return ["key": .string(String(match.1)), "enabled": .bool(match.2 == "enable")]
        }
        if clean == "/api/plugins/installed-overview" || clean == "/api/plugins/installed" {
            let autoReplyAccounts: JSONValue = [
                ["account_id": 1, "account_name": "日常工作账号", "enabled": .bool(accountFeatureEnabled[1]?["auto_reply"] ?? true), "state": "active"],
                ["account_id": 2, "account_name": "备用账号", "enabled": .bool(accountFeatureEnabled[2]?["auto_reply"] ?? false), "state": "stopped"]
            ]
            return [
                ["key": "auto_reply", "display_name": "自动回复", "global_enabled": .bool(pluginGlobalEnabled["auto_reply"] ?? true), "version": "1.4.0", "signature_ok": true, "source": "builtin", "accounts": autoReplyAccounts],
                ["key": "content_assistant", "display_name": "内容整理助手", "global_enabled": .bool(pluginGlobalEnabled["content_assistant"] ?? true), "version": "2.1.0", "signature_ok": true, "source": "git"],
                ["key": "forward_guard", "display_name": "转发风控与来源保护", "global_enabled": .bool(pluginGlobalEnabled["forward_guard"] ?? false), "version": "0.9.2", "signature_ok": false, "source": "local"]
            ]
        }
        if clean == "/api/plugins/auto_reply/config", method == "GET" {
            return ["plugin_key": "auto_reply", "config": ["max_per_hour": 40], "global_config": ["max_per_hour": 40]]
        }
        if clean.firstMatch(of: #/^\/api\/accounts\/\d+\/features\/[^\/]+\/config$/#) != nil {
            if method == "PATCH" {
                fixtureValues[clean + "#effective"] = body?["config"]
                return ["feature_key": "auto_reply", "enabled": true, "state": "active", "config": body?["config"] ?? [:]]
            }
            var effective: [String: JSONValue] = ["reply": "收到", "cooldown_seconds": 30, "max_per_hour": 40, "notify_owner": false]
            for (key, value) in fixtureValues[clean + "#effective"]?.objectValue ?? [:] { effective[key] = value }
            return .object(effective)
        }
        if clean == "/api/feature-matrix" {
            return [
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
                     "category": "interactive", "version": "2.1.0", "latest_version": "2.2.0", "update_available": true,
                     "source_type": "remote", "source_label": "个人插件仓库", "is_builtin": false, "signature_ok": true,
                     "orphan": false, "permissions": ["call_llm"]],
                    ["key": "forward_guard", "display_name": "转发风控与来源保护", "usage": "过滤异常转发并保留来源。",
                     "category": "utility", "version": "0.9.2", "update_available": false,
                     "source_type": "local", "source_label": "本地导入", "is_builtin": false, "signature_ok": false,
                     "orphan": true, "lint_warnings": ["插件来源目录不存在"]]
                ]
            ]
        }
        if let match = clean.firstMatch(of: #/^\/api\/accounts\/(\d+)\/features$/#), method == "GET" {
            let accountID = Int(match.1) ?? 1
            let autoReplyEnabled = accountFeatureEnabled[accountID]?["auto_reply"] ?? true
            return [
                ["feature_key": "auto_reply", "enabled": .bool(autoReplyEnabled), "state": autoReplyEnabled ? (accountID == 1 ? "active" : "pending") : "stopped", "last_error": nil, "config": ["reply": "收到"]],
                ["feature_key": "content_assistant", "enabled": true, "state": accountID == 1 ? "failed" : "active",
                 "last_error": accountID == 1 ? "模型供应商连接超时，请检查密钥与网络。" : nil, "config": [:]],
                ["feature_key": "forward_guard", "enabled": false, "state": "stopped", "last_error": nil, "config": [:]]
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
        if clean == "/api/commands/llm-providers" && method == "GET" {
            return [
                ["id": 1, "name": "个人模型网关", "provider": "openai", "base_url": "https://llm.example.com/v1",
                 "default_model": "gpt-5.4-mini", "models": [["id": "gpt-5.4-mini", "enabled": true], ["id": "gpt-5.4", "enabled": true]],
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
                              ["key": "codex_desktop_core", "label": "Codex Desktop 核心", "current": "1.2.0", "default": "1.2.0", "detectable": false]],
                    "profiles": [["profile": "codex_tui", "label": "Codex TUI", "description": "交互式 Codex TUI 的 Responses 身份。", "api_formats": ["responses"],
                                  "headers": [["name": "originator", "value": "codex_cli_rs", "description": "Codex TUI 或 Desktop 的来源标识。", "management": "fixed"]]]]]
        }
        if clean == "/api/commands/llm-providers/identity-versions/detect" {
            return ["items": [["key": "codex_tui", "current": "0.50.0", "latest": "0.51.0", "up_to_date": false]]]
        }
        if clean == "/api/commands/llm-providers/detect-protocols" {
            return ["chat_completions": ["ok": true, "latency_ms": 420, "status_code": 200],
                    "responses": ["ok": true, "latency_ms": 510, "status_code": 200, "client_identity_profile": "openai_sdk"],
                    "anthropic_messages": ["ok": false, "latency_ms": 120, "status_code": 404, "error": "接口不存在"],
                    "models": ["ok": true, "latency_ms": 200, "status_code": 200],
                    "recommended_api_format": "responses", "recommended_protocol_profile": "standard",
                    "recommended_client_identity_profile": "openai_sdk", "recommended_web_search_api_format": "responses"]
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
            return ["ok": true, "job_id": "fixture-update", "status": "succeeded", "progress": 100, "phase": "完成", "summary": "更新任务已完成。"]
        }
        if clean == "/api/apns/config" { return ["enabled": false, "configured": false, "environment": "sandbox", "has_auth_key": false] }
        if clean == "/api/apns/devices" { return [] }
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

    func stream(path: String, onEvent: @escaping @Sendable (JSONValue) async -> Void) async throws {
        try checkConnection()
        await onEvent(["type": "assistant_message", "content": "服务器任务已完成。", "seq": 1, "run_id": "fixture-run"])
        await onEvent(["type": "done", "ok": true, "seq": 2, "run_id": "fixture-run"])
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

    private func checkConnection() throws {
        if offline { throw APIError.network(outcomeUncertain: false) }
    }
}
#endif
