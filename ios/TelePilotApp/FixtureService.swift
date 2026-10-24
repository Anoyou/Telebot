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
        if clean == "/api/plugins/installed-overview" || clean == "/api/plugins/installed" {
            return [
                ["key": "auto_reply", "display_name": "自动回复", "global_enabled": true, "version": "1.4.0", "signature_ok": true, "source": "builtin"],
                ["key": "content_assistant", "display_name": "内容整理助手", "global_enabled": true, "version": "2.1.0", "signature_ok": true, "source": "git"],
                ["key": "forward_guard", "display_name": "转发风控与来源保护", "global_enabled": false, "version": "0.9.2", "signature_ok": false, "source": "local"]
            ]
        }
        if clean == "/api/feature-matrix" {
            return [
                "accounts": [
                    ["id": 1, "name": "日常工作账号", "features": ["auto_reply": "active"], "feature_enabled": ["auto_reply": true]],
                    ["id": 2, "name": "备用账号", "features": ["auto_reply": "paused"], "feature_enabled": ["auto_reply": false]]
                ],
                "features": [
                    ["key": "auto_reply", "display_name": "自动回复", "usage": "按规则自动回复私聊与群组消息。",
                     "category": "automation", "version": "1.4.0", "latest_version": "1.4.0", "update_available": false,
                     "source_type": "builtin", "source_label": "TelePilot 内置", "is_builtin": true, "signature_ok": true,
                     "orphan": false, "runtime_availability": "ready", "permissions": ["send_message"]],
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
            return ["provider_total": 2, "executable_provider_total": 2, "enabled_model_total": 3, "task_total": 3,
                    "needs_confirmation": false, "global_concurrency": 8, "provider_concurrency": 2, "max_tokens": 256, "max_output_tokens": 256,
                    "providers": [["provider_id": 1, "provider_name": "个人模型网关", "enabled_models": ["gpt-5.4-mini", "gpt-5.4"], "executable": true]]]
        }
        if clean == "/api/commands/llm-providers/liveness/run" { return ["run_id": "fixture-liveness", "status": "queued"] }
        if clean == "/api/commands/llm-providers/liveness/fixture-liveness" {
            return ["run_id": "fixture-liveness", "status": "completed", "task_total": 1, "completed": 1, "healthy": 1, "failed": 0,
                    "results": [["provider_id": 1, "provider_name": "个人模型网关", "model_id": "gpt-5.4-mini", "status": "healthy", "latency_ms": 620, "preview": "连接正常"]]]
        }
        if clean == "/api/system/update-status" {
            return ["enabled": true, "interval_minutes": 1440,
                    "last_check": ["has_update": true, "can_check": true, "checked_at": "2026-10-24T08:00:00.123456+00:00"]]
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
        if clean == "/api/system/settings" { return ["timezone": "Asia/Shanghai", "command_prefix": ".", "ai_enabled": true] }
        if clean == "/api/system/capabilities" { return ["modules": []] }
        if clean == "/api/ledger/summary" { return ["total": 0, "income": 0, "expense": 0] }
        if clean == "/api/logs/runtime" {
            return [["id": 1, "level": "info", "message": "界面日志测试 sk-proj-THIS_IS_A_FAKE_SECRET_FOR_UI_TEST_123456"]]
        }
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

    private func checkConnection() throws {
        if offline { throw APIError.network(outcomeUncertain: false) }
    }
}
#endif
