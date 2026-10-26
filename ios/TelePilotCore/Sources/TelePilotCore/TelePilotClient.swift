import Foundation
import OpenAPIRuntime

public actor TelePilotClient {
    public nonisolated let serverURL: URL
    private let transport: SessionTransport
    private let client: Client

    public init(serverURL: URL) throws {
        try self.init(serverURL: serverURL, configuration: .ephemeral, store: KeychainSessionStore(),
                      accessStore: KeychainCloudflareAccessStore())
    }

    init(serverURL: URL, configuration: URLSessionConfiguration, store: any SessionStore,
         accessStore: any CloudflareAccessStore) throws {
        let origin = try Self.normalizedServerURL(serverURL.absoluteString)
        self.serverURL = origin
        transport = SessionTransport(origin: origin, configuration: configuration, store: store, accessStore: accessStore)
        client = Client(serverURL: origin, configuration: .init(dateTranscoder: ServerDateTranscoder()), transport: transport)
    }

    public nonisolated static func normalizedServerURL(_ text: String) throws -> URL {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var components = URLComponents(string: trimmed), components.scheme?.lowercased() == "https",
              let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              components.percentEncodedPath.isEmpty || components.percentEncodedPath == "/",
              components.port == nil || (1...65535).contains(components.port!) else {
            throw APIError.invalidServerURL
        }
        components.scheme = "https"
        components.host = host.lowercased()
        components.path = ""
        if components.port == 443 { components.port = nil }
        guard let url = components.url else { throw APIError.invalidServerURL }
        return url
    }

    public func login(_ request: LoginRequest) async throws -> LoginResult {
        try await safely {
            var input = Components.Schemas.LoginRequest(password: request.password, username: request.username)
            input.totp_code = request.totpCode
            input.otp_token = request.otpToken
            input.otp_code = request.otpCode
            input.recovery_code = request.recoveryCode
            let result = try await client.login_api_auth_login_post(body: .json(input)).ok.body.json
            if result.ok, try await !transport.hasSession() { throw APIError.incompatibleResponse }
            return LoginResult(ok: result.ok, requireTotp: result.require_totp ?? false,
                               requireOtp: result.require_otp ?? false, otpToken: result.otp_token,
                               otpMessage: result.otp_message, otpTTLSeconds: result.otp_ttl_seconds,
                               recoveryAvailable: result.recovery_available ?? false)
        }
    }

    public func currentUser() async throws -> CurrentUser {
        try await safely {
            let value = try await client.me_api_auth_me_get().ok.body.json
            return CurrentUser(id: value.id, username: value.username, hasTotp: value.has_totp)
        }
    }

    public func accounts() async throws -> [Account] {
        try await safely {
            let values = try await client.list_accounts_api_accounts_get().ok.body.json
            return values.map { value in
                Account(id: value.id, phone: value.phone, displayName: value.display_name,
                        tgUsername: value.tg_username, status: value.status, tags: value.tags ?? [],
                        enabledFeatures: value.enabled_features ?? 0,
                        createdAt: value.created_at.ISO8601Format(),
                        proxyLabel: value.proxy.map { $0.label ?? "\($0.host):\($0.port)" })
            }
        }
    }

    public func account(id: Int) async throws -> Account {
        try await safely {
            let value = try await client.get_account_api_accounts__aid__get(path: .init(aid: id)).ok.body.json
            return Account(id: value.id, phone: value.phone, displayName: value.display_name,
                           tgUsername: value.tg_username, status: value.status, tags: value.tags ?? [],
                           enabledFeatures: value.enabled_features ?? 0, notes: value.notes,
                           createdAt: value.created_at.ISO8601Format(),
                           proxyLabel: value.proxy.map { $0.label ?? "\($0.host):\($0.port)" })
        }
    }

    public func health() async throws -> HealthOverview {
        try await safely {
            let value = try await client.get_health_overview_api_system_health_overview_get().ok.body.json
            let workers = value.workers
            guard let total = workers.total, let byStatus = workers.by_status,
                  let alive = workers.runtime_alive, let desired = workers.runtime_desired_running,
                  let desiredAlive = workers.runtime_desired_running_alive, let failing = workers.runtime_failing else {
                throw APIError.incompatibleResponse
            }
            return HealthOverview(
                db: ProbeStatus(ok: value.db.ok, error: value.db.error),
                redis: ProbeStatus(ok: value.redis.ok, error: value.redis.error),
                alembic: ProbeStatus(ok: value.alembic.ok, error: value.alembic.error),
                workers: WorkersStatus(total: total, byStatus: byStatus.additionalProperties,
                                       runtimeAlive: alive, runtimeDesiredRunning: desired,
                                       runtimeDesiredRunningAlive: desiredAlive, runtimeFailing: failing))
        }
    }

    public func resources() async throws -> ResourceDashboard {
        try await safely {
            let value = try await client.get_resource_dashboard_api_system_resource_dashboard_get().ok.body.json
            return ResourceDashboard(
                host: HostResource(cpuPercent: value.host.cpu_percent, memoryUsedPercent: value.host.memory_used_percent,
                                   diskUsedPercent: value.host.disk_used_percent, uptimeSeconds: value.host.uptime_seconds),
                workers: (value.workers ?? []).map {
                    WorkerResource(accountID: $0.account_id, alive: $0.alive, desired: $0.desired, failCount: $0.fail_count)
                })
        }
    }

    public func version() async throws -> ServerVersion {
        try await safely {
            let value = try await client.get_version_api_system_version_get().ok.body.json
            return ServerVersion(version: value.version, channel: value.channel.rawValue, revision: value.revision)
        }
    }

    public func pauseAccount(id: Int) async throws {
        try await mutationSafely {
            let value = try await client.pause_account_api_accounts__aid__pause_post(path: .init(aid: id)).ok.body.json
            guard value.additionalProperties["ok"] == true else { throw APIError.incompatibleResponse }
        }
    }

    public func resumeAccount(id: Int) async throws {
        try await mutationSafely {
            let value = try await client.resume_account_api_accounts__aid__resume_post(path: .init(aid: id)).ok.body.json
            guard value.additionalProperties["ok"] == true else { throw APIError.incompatibleResponse }
        }
    }

    public func logout() async throws {
        do {
            try await safely { _ = try await client.logout_api_auth_logout_post().ok.body.json }
        } catch {
            try await transport.clear()
            throw error
        }
        try await transport.clear()
    }

    public func clearLocalSession() async throws { try await transport.clear() }

    // MARK: Cloudflare Access

    /// 这台服务器保存在钥匙串里的 Cloudflare Access 凭证（Google 验证的登录令牌与 Service Token）。
    public func cloudflareAccessConfig() async throws -> CloudflareAccessConfig {
        try await transport.cloudflareAccessConfig()
    }

    /// 保存（或传 nil 清除）Service Token（高级）。只改本机钥匙串，不发请求。
    public func setCloudflareAccess(_ credentials: CloudflareAccessCredentials?) async throws {
        try await transport.setCloudflareAccess(credentials)
    }

    /// 清除（或替换）App 内 Google 验证取得的 Access 登录令牌。只改本机钥匙串。
    public func setCloudflareAccessSession(_ session: CloudflareAccessSession?) async throws {
        try await transport.setCloudflareAccessSession(session)
    }

    /// 第一步：发现 Access 应用，返回要在系统登录窗（ASWebAuthenticationSession）打开的地址。
    public func prepareCloudflareAccessLogin() async throws -> CloudflareAccessLoginRequest {
        try await transport.prepareCloudflareAccessLogin()
    }

    /// 第二步：等待用户完成 Google 登录，取回并保存 Access 登录令牌。取消所在 Task 即停止等待。
    public func completeCloudflareAccessLogin(_ request: CloudflareAccessLoginRequest) async throws -> CloudflareAccessSession {
        try await transport.completeCloudflareAccessLogin(request)
    }

    public func request(path: String, method: String = "GET", body: JSONValue? = nil) async throws -> JSONValue {
        let data = try await download(path: path, method: method, body: body)
        guard !data.isEmpty else { return .null }
        do { return try JSONValue(data: data) }
        catch {
            if method != "GET" { throw APIError.mutationOutcomeUnknown(incompatibleResponse: true) }
            throw APIError.incompatibleResponse
        }
    }

    public func download(path: String, method: String = "GET", body: JSONValue? = nil) async throws -> Data {
        do {
            return try await transport.raw(path: path, method: method, body: try body?.data(),
                                           contentType: body == nil ? nil : "application/json")
        } catch let error as APIError {
            if method != "GET", case .server(let status, _) = error, status >= 500 {
                throw APIError.mutationOutcomeUnknown(incompatibleResponse: false)
            }
            throw error
        }
    }

    public func upload(path: String, file: Data, filename: String, fields: [String: String] = [:]) async throws -> JSONValue {
        let boundary = "TelePilot-" + UUID().uuidString
        let safeName = filename.filter { $0.isASCII && ($0.isLetter || $0.isNumber || "._-".contains($0)) }
        var body = Data()
        func append(_ value: String) { body.append(contentsOf: value.utf8) }
        for (key, value) in fields.sorted(by: { $0.key < $1.key }) {
            guard key.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_") }) else {
                throw APIError.incompatibleResponse
            }
            append("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + key + "\"\r\n\r\n" + value + "\r\n")
        }
        append("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + (safeName.isEmpty ? "upload.bin" : safeName) + "\"\r\nContent-Type: application/octet-stream\r\n\r\n")
        body.append(file)
        append("\r\n--" + boundary + "--\r\n")
        guard body.count <= 64 * 1024 * 1024 else { throw APIError.server(status: 413, code: nil) }
        do {
            let data = try await transport.raw(path: path, method: "POST", body: body,
                                               contentType: "multipart/form-data; boundary=" + boundary)
            do { return try JSONValue(data: data) }
            catch { throw APIError.mutationOutcomeUnknown(incompatibleResponse: true) }
        } catch let error as APIError {
            if case .server(let status, _) = error, status >= 500 { throw APIError.mutationOutcomeUnknown(incompatibleResponse: false) }
            throw error
        }
    }

    public func stream(path: String, onEvent: @escaping @Sendable (JSONValue) async -> Void) async throws {
        try await transport.stream(path: path, onEvent: onEvent)
    }

    private func mutationSafely(_ operation: () async throws -> Void) async throws {
        do { try await safely(operation) }
        catch let error as APIError {
            switch error {
            case .incompatibleResponse: throw APIError.mutationOutcomeUnknown(incompatibleResponse: true)
            case .server(let status, _) where status >= 500:
                throw APIError.mutationOutcomeUnknown(incompatibleResponse: false)
            default: throw error
            }
        }
    }

    private func safely<T: Sendable>(_ operation: () async throws -> T) async throws -> T {
        do { return try await operation() }
        catch let error as APIError { throw error }
        catch is CancellationError { throw CancellationError() }
        catch let error as ClientError {
            if let safe = error.underlyingError as? APIError { throw safe }
            if error.underlyingError is CancellationError { throw CancellationError() }
            throw APIError.incompatibleResponse
        } catch { throw APIError.incompatibleResponse }
    }
}

private struct ServerDateTranscoder: DateTranscoder {
    private let fractional: ISO8601DateTranscoder = .iso8601WithFractionalSeconds
    private let whole: ISO8601DateTranscoder = .iso8601

    func encode(_ date: Date) throws -> String { try fractional.encode(date) }
    func decode(_ text: String) throws -> Date {
        // PostgreSQL/Pydantic timestamps may include microseconds; fixtures/older rows may not.
        if let date = try? fractional.decode(text) { return date }
        return try whole.decode(text)
    }
}
