import Foundation
import Observation
import TelePilotCore

@MainActor @Observable
final class AppModel {
    enum Phase { case starting, signedOut, signedIn }

    private(set) var phase: Phase = .starting
    private(set) var serverURL: URL?
    private(set) var user: CurrentUser?
    private(set) var health: HealthOverview?
    private(set) var resources: ResourceDashboard?
    private(set) var serverVersion: ServerVersion?
    private(set) var accounts: [Account] = []
    private(set) var accountsLoaded = false
    private(set) var healthError: String?
    private(set) var resourcesError: String?
    private(set) var accountsError: String?
    private(set) var versionError: String?
    private(set) var lastUpdated: Date?
    private(set) var isRefreshing = false
    private(set) var isAuthenticating = false
    private(set) var details: [Int: Account] = [:]
    private(set) var detailErrors: [Int: String] = [:]
    private(set) var loadingDetails: Set<Int> = []
    private(set) var mutatingAccounts: Set<Int> = []
    private(set) var actionMessages: [Int: String] = [:]
    private(set) var uncertainActions: Set<Int> = []
    private(set) var canRetrySession = false
    private(set) var sessionCleanupError: String?
    var loginNotice: String?
    private(set) var isFixture = false
    let catalog = APIContractCatalog.bundled
    private(set) var pendingActions: [JSONValue] = []
    private(set) var plugins: [JSONValue] = []
    private(set) var workspaceError: String?
    var requestedRoute: AppRoute?
    /// 推送或其他入口要求 Agent 标签页打开的会话；由内嵌助手读取后清空。
    private(set) var assistantSessionRequest: String?
    /// 写操作已被服务器接受，正在重新读取状态。与读取失败（*Error）区分，避免误报“已过期”。
    private(set) var isSyncingAfterAction = false
    /// 已登录时请求被 Cloudflare Access 拦截：根视图据此弹出 Service Token 设置（每次拦截只弹一次）。
    var cloudflareAccessPrompt = false
    /// 启动时恢复会话就被 Cloudflare Access 拦截：登录页据此展开 Cloudflare Access 区。
    private(set) var loginAccessBlocked = false
    /// 当前服务器是否已保存 Service Token；nil 表示尚未读取。
    private(set) var cloudflareAccessConfigured: Bool?

    @ObservationIgnored private var service: (any AppServing)?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let serverKey = "telepilot.serverURL"
    @ObservationIgnored private var cleanupForgetServer = false
    @ObservationIgnored private var cleanupNotice: String?
    @ObservationIgnored private var refreshAgain = false
    @ObservationIgnored private var detailRequestIDs: [Int: Int] = [:]
    @ObservationIgnored private var cloudflareAccessPrompted = false

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--ui-testing") {
            isFixture = true
            serverURL = URL(string: "https://ui-test.invalid")
            service = FixtureService(offline: ProcessInfo.processInfo.arguments.contains("--ui-test-offline"))
            return
        }
        #endif
        if let saved = defaults.string(forKey: serverKey),
           let url = try? TelePilotClient.normalizedServerURL(saved),
           let client = try? TelePilotClient(serverURL: url) {
            serverURL = url
            service = client
        }
    }

    func start() async {
        guard phase == .starting else { return }
        #if DEBUG
        if isFixture && ProcessInfo.processInfo.arguments.contains("--ui-test-login") {
            phase = .signedOut
            return
        }
        #endif
        guard let service else { phase = .signedOut; return }
        let token = generation
        do {
            let result = try await service.currentUser()
            guard token == generation else { return }
            user = result
            phase = .signedIn
            await refresh()
        } catch {
            guard token == generation else { return }
            if requiresSessionCleanup(error) { await expireSession() }
            else {
                phase = .signedOut
                canRetrySession = true
                loginAccessBlocked = (error as? APIError)?.isCloudflareAccess == true
                loginNotice = message(error)
            }
        }
    }

    func retrySession() async {
        guard canRetrySession, !isAuthenticating, sessionCleanupError == nil, let service else { return }
        isAuthenticating = true
        let token = generation
        defer { isAuthenticating = false }
        do {
            let current = try await service.currentUser()
            guard token == generation else { return }
            user = current
            phase = .signedIn
            canRetrySession = false
            loginAccessBlocked = false
            loginNotice = nil
            await refresh()
        } catch {
            guard token == generation else { return }
            if requiresSessionCleanup(error) { await expireSession() }
            else {
                loginAccessBlocked = (error as? APIError)?.isCloudflareAccess == true
                loginNotice = message(error)
            }
        }
    }

    /// - Parameter access: 登录页填写的 Cloudflare Access Service Token；nil 表示不使用（会清除此服务器已保存的值）。
    func login(server: String, request: LoginRequest, access: CloudflareAccessCredentials? = nil) async throws -> LoginResult? {
        guard !isAuthenticating, sessionCleanupError == nil else { return nil }
        isAuthenticating = true
        defer { isAuthenticating = false }
        loginNotice = nil
        let url = try TelePilotClient.normalizedServerURL(server)
        if serverURL != url || service == nil {
            generation += 1
            let previous = service
            clearData()
            do { try await previous?.clearLocalSession() }
            catch {
                cleanupForgetServer = true
                cleanupNotice = nil
                sessionCleanupError = "未能从系统钥匙串移除旧会话，已阻止更换服务器。请解锁设备后重试。"
                throw error
            }
            #if DEBUG
            if isFixture { service = FixtureService() }
            else { service = try TelePilotClient(serverURL: url) }
            #else
            service = try TelePilotClient(serverURL: url)
            #endif
            serverURL = url
            if !isFixture { defaults.set(url.absoluteString, forKey: serverKey) }
        }
        guard let service else { return nil }
        let token = generation
        try await service.setCloudflareAccess(access)
        cloudflareAccessConfigured = access != nil
        cloudflareAccessPrompted = false
        let result = try await service.login(request)
        guard token == generation else { return nil }
        if result.ok {
            let current: CurrentUser
            do { current = try await service.currentUser() }
            catch {
                guard token == generation else { return nil }
                if requiresSessionCleanup(error) { await expireSession() }
                else { canRetrySession = true }
                throw error
            }
            guard token == generation else { return nil }
            user = current
            phase = .signedIn
            canRetrySession = false
            loginAccessBlocked = false
            await refresh()
        }
        return result
    }

    func requestAssistantSession(_ id: String?) {
        assistantSessionRequest = id
    }

    func consumeAssistantSessionRequest() -> String? {
        defer { assistantSessionRequest = nil }
        return assistantSessionRequest
    }

    func refresh() async {
        guard phase == .signedIn, let service else { return }
        if isRefreshing { refreshAgain = true; return }
        isRefreshing = true
        let token = generation
        async let healthResult = capture { try await service.health() }
        async let resourcesResult = capture { try await service.resources() }
        async let accountsResult = capture { try await service.accounts() }
        async let versionResult = capture { try await service.version() }
        let results = await (healthResult, resourcesResult, accountsResult, versionResult)
        guard token == generation else { return }
        isRefreshing = false
        let errors: [Error] = [results.0.failure, results.1.failure, results.2.failure, results.3.failure].compactMap { $0 }
        if errors.contains(where: { requiresSessionCleanup($0) }) {
            await expireSession()
            return
        }
        for error in errors { noteCloudflareAccess(error) }
        switch results.0 {
        case .success(let value): health = value; healthError = nil
        case .failure(let error): healthError = message(error)
        }
        switch results.1 {
        case .success(let value): resources = value; resourcesError = nil
        case .failure(let error): resourcesError = message(error)
        }
        switch results.2 {
        case .success(let value): accounts = value; accountsLoaded = true; accountsError = nil
        case .failure(let error): accountsError = message(error)
        }
        switch results.3 {
        case .success(let value): serverVersion = value; versionError = nil
        case .failure(let error): versionError = message(error)
        }
        if errors.isEmpty { lastUpdated = Date() }
        if !refreshAgain { isSyncingAfterAction = false }
        if refreshAgain {
            refreshAgain = false
            await refresh()
        }
    }

    func loadAccount(id: Int, replacingPending: Bool = false) async {
        guard phase == .signedIn, replacingPending || !loadingDetails.contains(id),
              replacingPending || !mutatingAccounts.contains(id), let service else { return }
        loadingDetails.insert(id)
        let token = generation
        let readID = (detailRequestIDs[id] ?? 0) + 1
        detailRequestIDs[id] = readID
        do {
            let account = try await service.account(id: id)
            guard token == generation, detailRequestIDs[id] == readID else { return }
            details[id] = account
            detailErrors[id] = nil
            if let index = accounts.firstIndex(where: { $0.id == id }) { accounts[index] = account }
        } catch {
            guard token == generation, detailRequestIDs[id] == readID else { return }
            if requiresSessionCleanup(error) { await expireSession(); return }
            noteCloudflareAccess(error)
            detailErrors[id] = message(error)
        }
        guard token == generation, detailRequestIDs[id] == readID else { return }
        loadingDetails.remove(id)
    }

    func toggleAccount(id: Int, expectedStatus: String) async {
        if let current = details[id], current.status != expectedStatus {
            actionMessages[id] = "账号状态已变化，请重新确认后操作。"
            return
        }
        guard phase == .signedIn, !mutatingAccounts.contains(id),
              !loadingDetails.contains(id), detailErrors[id] == nil,
              !uncertainActions.contains(id), let account = details[id],
              account.status == expectedStatus,
              AccountPresentation.actionLabel(account) != nil, let service else { return }
        mutatingAccounts.insert(id)
        actionMessages[id] = nil
        let token = generation
        do {
            if account.status == "active" { try await service.pauseAccount(id: id) }
            else { try await service.resumeAccount(id: id) }
            guard token == generation else { return }
            await loadAccount(id: id, replacingPending: true)
            guard token == generation else { return }
            actionMessages[id] = detailErrors[id] == nil
                ? "服务器已接受操作，账号信息已重新读取。运行进程可能需要片刻同步。"
                : "服务器已接受操作，但未能读取最新状态。请刷新确认，不要重复操作。"
            isSyncingAfterAction = true
            await refresh()
        } catch {
            guard token == generation else { return }
            if requiresSessionCleanup(error) { await expireSession(); return }
            if (error as? APIError)?.isOutcomeUncertain == true {
                uncertainActions.insert(id)
                actionMessages[id] = "操作结果尚未确认，服务器可能已执行。请先刷新状态，不要重复操作。"
                healthError = "账号操作后的进程状态尚未确认，请刷新。"
                resourcesError = "账号操作后的资源状态尚未确认，请刷新。"
                accountsError = "账号操作结果尚未确认，请刷新账号详情。"
            } else {
                let failureMessage = message(error)
                // A rejected resume can still persist login_required on the server.
                await loadAccount(id: id, replacingPending: true)
                guard token == generation else { return }
                actionMessages[id] = detailErrors[id] == nil
                    ? failureMessage
                    : failureMessage + " 最新账号状态也未能读取，请刷新确认后再操作。"
                isSyncingAfterAction = true
                await refresh()
            }
        }
        guard token == generation else { return }
        mutatingAccounts.remove(id)
    }

    func confirmLatestAccount(id: Int) async {
        guard !mutatingAccounts.contains(id), !loadingDetails.contains(id) else { return }
        let token = generation
        await loadAccount(id: id)
        guard token == generation else { return }
        if details[id] != nil && detailErrors[id] == nil {
            uncertainActions.remove(id)
            actionMessages[id] = nil
            await refresh()
        }
    }

    func logout(forgetServer: Bool = false) async {
        if phase == .signedIn, let id = defaults.string(forKey: "telepilot.push.installation") {
            _ = try? await request(path: "/api/apns/devices/" + id, method: "DELETE")
        }
        generation += 1
        let token = generation
        let oldService = service
        clearData()
        phase = .signedOut
        isAuthenticating = true
        canRetrySession = false
        cleanupForgetServer = forgetServer
        var failed = false
        do { try await oldService?.logout() } catch { failed = true }
        guard token == generation else { return }
        cleanupNotice = failed ? "本机已退出。服务器未确认退出，原会话将在服务器端到期。" : nil
        await finishSessionCleanup()
    }

    private func expireSession() async {
        generation += 1
        clearData()
        phase = .signedOut
        canRetrySession = false
        cleanupForgetServer = false
        cleanupNotice = "登录已过期或无法读取，请重新登录。"
        await finishSessionCleanup()
    }

    func retrySessionCleanup() async {
        guard sessionCleanupError != nil, !isAuthenticating else { return }
        await finishSessionCleanup()
    }

    func request(path: String, method: String = "GET", body: JSONValue? = nil) async throws -> JSONValue {
        guard phase == .signedIn, let service else { throw APIError.unauthorized }
        let token = generation
        do {
            let value = try await service.request(path: path, method: method, body: body)
            guard token == generation else { throw APIError.sessionChanged }
            return value
        } catch {
            if token == generation, requiresSessionCleanup(error) { await expireSession() }
            if token == generation { noteCloudflareAccess(error) }
            throw error
        }
    }

    func upload(path: String, file: Data, filename: String, fields: [String: String] = [:]) async throws -> JSONValue {
        guard phase == .signedIn, let service else { throw APIError.unauthorized }
        let token = generation
        do {
            let value = try await service.upload(path: path, file: file, filename: filename, fields: fields)
            guard token == generation else { throw APIError.sessionChanged }
            return value
        } catch {
            if token == generation, requiresSessionCleanup(error) { await expireSession() }
            if token == generation { noteCloudflareAccess(error) }
            throw error
        }
    }

    func download(path: String, method: String = "GET", body: JSONValue? = nil) async throws -> Data {
        guard phase == .signedIn, let service else { throw APIError.unauthorized }
        let token = generation
        do {
            let value = try await service.download(path: path, method: method, body: body)
            guard token == generation else { throw APIError.sessionChanged }
            return value
        } catch {
            if token == generation, requiresSessionCleanup(error) { await expireSession() }
            if token == generation { noteCloudflareAccess(error) }
            throw error
        }
    }

    func stream(path: String, onEvent: @escaping @Sendable (JSONValue) async -> Void) async throws {
        guard phase == .signedIn, let service else { throw APIError.unauthorized }
        let token = generation
        do {
            try await service.stream(path: path) { [weak self] event in
                guard await self?.sessionMatches(token) == true else { return }
                await onEvent(event)
            }
            guard token == generation else { throw APIError.sessionChanged }
        } catch {
            if token == generation, requiresSessionCleanup(error) { await expireSession() }
            if token == generation { noteCloudflareAccess(error) }
            throw error
        }
    }

    private func sessionMatches(_ token: Int) -> Bool { token == generation && phase == .signedIn }

    func refreshWorkspace() async {
        guard phase == .signedIn else { return }
        let token = generation
        // 待确认事项与插件矩阵互不依赖，并发读取以缩短概览刷新时间。
        async let actionsResult = capture { try await self.request(path: "/api/system-agent/actions?status=pending&limit=200") }
        async let pluginsResult = capture { try await self.request(path: "/api/feature-matrix") }
        let (actions, matrix) = await (actionsResult, pluginsResult)
        guard token == generation else { return }
        switch actions {
        case .success(let values):
            pendingActions = values.arrayValue ?? []
            workspaceError = nil
        case .failure:
            workspaceError = "待确认事项读取失败，请重试。"
        }
        if case .success(let value) = matrix {
            plugins = value.arrayValue ?? value["items"]?.arrayValue ?? value["plugins"]?.arrayValue ?? value["features"]?.arrayValue ?? []
        }
    }

    /// 插件页读取到新的插件列表后同步过来，让 Tab 角标与概览使用同一份真实数据。
    func updatePlugins(_ values: [JSONValue]) {
        guard phase == .signedIn else { return }
        plugins = values
    }

    /// 服务器报告有新版本的插件数（插件 Tab 角标）。
    var pluginUpdateCount: Int { plugins.filter { $0["update_available"]?.boolValue == true }.count }

    private func finishSessionCleanup() async {
        isAuthenticating = true
        defer { isAuthenticating = false }
        do {
            try await service?.clearLocalSession()
            sessionCleanupError = nil
            if cleanupForgetServer {
                // 更换服务器同时移除这台服务器的 Cloudflare Access Service Token。
                try await service?.setCloudflareAccess(nil)
                cloudflareAccessConfigured = nil
                loginAccessBlocked = false
                service = nil
                serverURL = nil
                if !isFixture { defaults.removeObject(forKey: serverKey) }
            }
            loginNotice = cleanupNotice
        } catch {
            loginNotice = nil
            sessionCleanupError = "未能从系统钥匙串移除旧会话，暂时无法登录或更换服务器。请解锁设备后重试清理。"
        }
    }

    // MARK: Cloudflare Access

    /// 读取当前服务器保存的 Service Token（只读本机钥匙串）。
    func cloudflareAccess() async throws -> CloudflareAccessCredentials? {
        guard let service else { return nil }
        let value = try await service.cloudflareAccess()
        cloudflareAccessConfigured = value != nil
        return value
    }

    /// 保存或清除（nil）当前服务器的 Service Token，只改本机钥匙串。
    func setCloudflareAccess(_ credentials: CloudflareAccessCredentials?) async throws {
        guard let service else { throw APIError.invalidServerURL }
        try await service.setCloudflareAccess(credentials)
        cloudflareAccessConfigured = credentials != nil
        cloudflareAccessPrompted = false
    }

    /// 用一次只读请求（当前用户）验证能否穿过 Cloudflare Access 到达 TelePilot。成功时顺带刷新概览。
    /// 返回 nil 表示连接正常，否则为可展示的失败原因。不重试，也不发出任何写请求。
    func verifyCloudflareAccess() async -> String? {
        guard phase == .signedIn, let service else { return "请先登录后再验证连接。" }
        let token = generation
        do {
            _ = try await service.currentUser()
            guard token == generation else { return APIError.sessionChanged.localizedDescription }
            cloudflareAccessPrompt = false
            await refresh()
            return nil
        } catch {
            guard token == generation else { return APIError.sessionChanged.localizedDescription }
            if requiresSessionCleanup(error) { await expireSession() }
            return message(error)
        }
    }

    private func noteCloudflareAccess(_ error: Error) {
        guard phase == .signedIn, (error as? APIError)?.isCloudflareAccess == true, !cloudflareAccessPrompted else { return }
        cloudflareAccessPrompted = true
        cloudflareAccessPrompt = true
    }

    private func requiresSessionCleanup(_ error: Error) -> Bool {
        guard let apiError = error as? APIError else { return false }
        if case .secureStorage = apiError { return true }
        return apiError.isUnauthorized
    }

    private func clearData() {
        pendingActions = []
        plugins = []
        assistantSessionRequest = nil
        isSyncingAfterAction = false
        workspaceError = nil
        requestedRoute = nil
        user = nil
        health = nil
        resources = nil
        serverVersion = nil
        accounts = []
        accountsLoaded = false
        healthError = nil
        resourcesError = nil
        accountsError = nil
        versionError = nil
        lastUpdated = nil
        isRefreshing = false
        refreshAgain = false
        details = [:]
        detailRequestIDs = [:]
        detailErrors = [:]
        loadingDetails = []
        mutatingAccounts = []
        actionMessages = [:]
        uncertainActions = []
    }

    private func message(_ error: Error) -> String {
        if let error = error as? APIError { return error.localizedDescription }
        return "暂时无法连接服务器，请检查网络和服务器地址后重试。"
    }
}

private func capture<T: Sendable>(_ operation: @Sendable () async throws -> T) async -> Result<T, Error> {
    do { return .success(try await operation()) }
    catch { return .failure(error) }
}

private extension Result {
    var failure: Failure? {
        if case .failure(let error) = self { return error }
        return nil
    }
}
