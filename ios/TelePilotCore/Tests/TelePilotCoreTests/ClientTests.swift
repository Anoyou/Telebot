import Foundation
import Testing
@testable import TelePilotCore

final class MemoryStore: SessionStore, @unchecked Sendable {
    private let lock = NSLock()
    private var sessions: [String: StoredSession] = [:]
    private var rejectClear = false
    func setRejectClear(_ value: Bool) { lock.withLock { rejectClear = value } }
    func load(origin: String) throws -> StoredSession? { lock.withLock { sessions[origin] } }
    func save(_ session: StoredSession, origin: String) throws { lock.withLock { sessions[origin] = session } }
    func clear(origin: String) throws {
        try lock.withLock {
            if rejectClear { throw APIError.secureStorage }
            sessions[origin] = nil
        }
    }
}

struct MockReply: Sendable {
    var status = 200
    var headers: [String: String] = [:]
    var body = "{}"
    var error: URLError.Code?
}

final class MockProtocol: URLProtocol, @unchecked Sendable {
    static let lock = NSLock()
    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) -> MockReply)?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let callback = Self.lock.withLock { Self.handler }
        guard let callback else { fatalError("Missing test handler") }
        let reply = callback(request)
        if let error = reply.error {
            client?.urlProtocol(self, didFailWithError: URLError(error))
            return
        }
        var headers = reply.headers
        headers["Content-Type"] = headers["Content-Type"] ?? "application/json"
        let response = HTTPURLResponse(url: request.url!, statusCode: reply.status,
                                       httpVersion: "HTTP/1.1", headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(reply.body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

final class Script: @unchecked Sendable {
    let lock = NSLock()
    private var replies: [MockReply]
    private var captured: [URLRequest] = []
    init(_ replies: [MockReply]) { self.replies = replies }
    func handle(_ request: URLRequest) -> MockReply {
        lock.withLock {
            captured.append(request)
            guard !replies.isEmpty else { return MockReply(status: 599) }
            return replies.removeFirst()
        }
    }
    var requests: [URLRequest] { lock.withLock { captured } }
    var remaining: Int { lock.withLock { replies.count } }
}

@Suite(.serialized)
struct ClientTests {
    let origin = "https://telepilot.example"
    var csrfReply: MockReply { MockReply(headers: ["Set-Cookie": "csrf_token=csrf-one; Path=/; Max-Age=43200; Secure"], body: "{\"ok\":true}") }
    var loginReply: MockReply { MockReply(headers: ["Set-Cookie": "auth_token=session-one; Path=/; Max-Age=3600; Secure; HttpOnly"], body: "{\"ok\":true}") }
    var userReply: MockReply { MockReply(body: "{\"id\":1,\"username\":\"admin\",\"has_totp\":false}") }

    func setup(_ replies: [MockReply], store: MemoryStore = MemoryStore(), origin: String? = nil) throws -> (TelePilotClient, Script, MemoryStore) {
        let script = Script(replies)
        MockProtocol.lock.withLock { MockProtocol.handler = { script.handle($0) } }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockProtocol.self]
        return (try TelePilotClient(serverURL: URL(string: origin ?? self.origin)!, configuration: config, store: store), script, store)
    }

    func body(_ request: URLRequest) throws -> [String: Any] {
        var data = request.httpBody ?? Data()
        if data.isEmpty, let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(contentsOf: buffer.prefix(count))
            }
        }
        return try JSONSerialization.jsonObject(with: data) as! [String: Any]
    }

    @Test func originsNormalizeAndRejectUnsafeInput() throws {
        #expect(try TelePilotClient.normalizedServerURL(" HTTPS://EXAMPLE.COM:443/ ").absoluteString == "https://example.com")
        #expect(try TelePilotClient.normalizedServerURL("https://example.com:8443").port == 8443)
        for invalid in ["http://example.com", "example.com", "https://u:p@example.com", "https://example.com/api", "https://example.com/?x=1", "https://example.com/#x", "https://example.com:0", "https://example.com/%2F"] {
            #expect(throws: APIError.self) { try TelePilotClient.normalizedServerURL(invalid) }
        }
    }

    @Test func loginUsesDoubleSubmitAndPersistsOnlySession() async throws {
        let (client, script, store) = try setup([csrfReply, loginReply, userReply])
        #expect(try await client.login(LoginRequest(username: "admin", password: "secret")).ok)
        #expect(try await client.currentUser().username == "admin")
        let requests = script.requests
        #expect(requests.map { $0.url!.path } == ["/api/auth/csrf", "/api/auth/login", "/api/auth/me"])
        #expect(requests[1].value(forHTTPHeaderField: "X-CSRF-Token") == "csrf-one")
        #expect(requests[1].value(forHTTPHeaderField: "Cookie") == "csrf_token=csrf-one")
        #expect(requests[1].value(forHTTPHeaderField: "X-Requested-With") == "telepilot-ui")
        #expect(requests[2].value(forHTTPHeaderField: "Cookie")?.contains("auth_token=session-one") == true)
        let saved = try #require(try store.load(origin: origin))
        #expect(saved.token == "session-one")
        #expect(abs(saved.expiresAt.timeIntervalSinceNow - 3600) < 5)
        #expect(script.remaining == 0)
    }

    @Test func multiStepTotpOtpAndRecoveryFieldsMatchServer() async throws {
        let totp = MockReply(body: "{\"ok\":false,\"require_totp\":true}")
        let otp = MockReply(body: "{\"ok\":false,\"require_otp\":true,\"otp_token\":\"challenge\",\"otp_ttl_seconds\":60,\"recovery_available\":true}")
        let (client, script, _) = try setup([csrfReply, totp, otp, loginReply])
        #expect(try await client.login(.init(username: "admin", password: "secret")).requireTotp)
        let challenge = try await client.login(.init(username: "admin", password: "secret", totpCode: "123456"))
        #expect(challenge.requireOtp && challenge.recoveryAvailable)
        #expect(challenge.otpToken == "challenge" && challenge.otpTTLSeconds == 60)
        #expect(try await client.login(.init(username: "admin", password: "secret", totpCode: "123456", otpToken: "challenge", otpCode: "654321", recoveryCode: "recovery")).ok)
        let payload = try body(script.requests[3])
        #expect(payload["totp_code"] as? String == "123456")
        #expect(payload["otp_code"] as? String == "654321")
        #expect(payload["otp_token"] as? String == "challenge")
        #expect(payload["recovery_code"] as? String == "recovery")
    }

    @Test func csrfRejectionRefreshesExactlyOnce() async throws {
        let rejected = MockReply(status: 403, body: "{\"detail\":{\"code\":\"CSRF_TOKEN_REQUIRED\"}}")
        let newToken = MockReply(headers: ["Set-Cookie": "csrf_token=csrf-two; Path=/; Max-Age=3600; Secure"])
        let (client, script, _) = try setup([csrfReply, rejected, newToken, MockReply(body: "{\"ok\":true}")])
        try await client.pauseAccount(id: 7)
        #expect(script.requests.map { $0.httpMethod! } == ["GET", "POST", "GET", "POST"])
        #expect(script.requests[3].value(forHTTPHeaderField: "X-CSRF-Token") == "csrf-two")
        #expect(script.requests[3].value(forHTTPHeaderField: "Cookie") == "csrf_token=csrf-two")
    }

    @Test func repeatedCsrfFailureDoesNotLoop() async throws {
        let rejected = MockReply(status: 403, body: "{\"error\":{\"code\":\"CSRF_HEADER_REQUIRED\"}}")
        let (client, script, _) = try setup([csrfReply, rejected, csrfReply, rejected])
        await #expect(throws: APIError.self) { try await client.pauseAccount(id: 7) }
        #expect(script.requests.count == 4)
    }

    @Test func arbitrary403NeverRetriesMutation() async throws {
        let (client, script, _) = try setup([csrfReply, MockReply(status: 403, body: "{\"error\":{\"code\":\"FORBIDDEN\"}}")])
        await #expect(throws: APIError.self) { try await client.resumeAccount(id: 7) }
        #expect(script.requests.count == 2)
    }

    @Test func timeoutNeverRetriesMutationAndWarnsUncertainResult() async throws {
        let (client, script, _) = try setup([csrfReply, MockReply(error: .timedOut)])
        do { try await client.pauseAccount(id: 7); Issue.record("Expected failure") }
        catch let error as APIError { #expect(error.isOutcomeUncertain) }
        #expect(script.requests.count == 2)
    }

    @Test func serverUpdateTransportKeepsAuthenticationAndNeverReplaysTimeout() async throws {
        let store = MemoryStore()
        try store.save(StoredSession(token: "session-one", expiresAt: Date().addingTimeInterval(3600)), origin: origin)
        let (client, script, _) = try setup([csrfReply, MockReply(body: "{\"has_update\":true}"), MockReply(error: .timedOut)], store: store)
        _ = try await client.request(path: "/api/system/check-update", method: "POST")
        do {
            _ = try await client.request(path: "/api/system/pull-update", method: "POST", body: ["remote": "origin", "branch": "Beta"])
            Issue.record("Expected uncertain update outcome")
        } catch let error as APIError { #expect(error.isOutcomeUncertain) }
        #expect(script.requests.count == 3)
        for request in script.requests.dropFirst() {
            #expect(request.value(forHTTPHeaderField: "Cookie")?.contains("auth_token=session-one") == true)
            #expect(request.value(forHTTPHeaderField: "X-CSRF-Token") == "csrf-one")
        }
    }

    @Test func unauthorizedClearsPersistentAndMemorySession() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "old-session", expiresAt: .distantFuture), origin: origin)
        let (client, script, _) = try setup([MockReply(status: 401, body: "{\"error\":{\"code\":\"AUTH_INVALID\"}}"), userReply], store: store)
        do { _ = try await client.currentUser(); Issue.record("Expected failure") }
        catch let error as APIError { #expect(error.isUnauthorized) }
        #expect(try store.load(origin: origin) == nil)
        _ = try await client.currentUser()
        #expect(script.requests[1].value(forHTTPHeaderField: "Cookie") == nil)
    }

    @Test func logoutClearsLocallyEvenOnNetworkFailure() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "old-session", expiresAt: .distantFuture), origin: origin)
        let (client, _, _) = try setup([csrfReply, MockReply(error: .notConnectedToInternet)], store: store)
        await #expect(throws: APIError.self) { try await client.logout() }
        #expect(try store.load(origin: origin) == nil)
    }

    @Test func expiredSessionNeverLeavesDevice() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "expired", expiresAt: .distantPast), origin: origin)
        let (client, script, _) = try setup([userReply], store: store)
        _ = try await client.currentUser()
        #expect(script.requests[0].value(forHTTPHeaderField: "Cookie") == nil)
        #expect(try store.load(origin: origin) == nil)
    }

    @Test func originsHaveIsolatedSessionsAndValidSessionRestores() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "server-A", expiresAt: .distantFuture), origin: origin)
        let (second, requestsB, _) = try setup([userReply], store: store, origin: "https://second.example")
        _ = try await second.currentUser()
        #expect(requestsB.requests[0].value(forHTTPHeaderField: "Cookie") == nil)
        let (restored, requestsA, _) = try setup([userReply], store: store)
        _ = try await restored.currentUser()
        #expect(requestsA.requests[0].value(forHTTPHeaderField: "Cookie") == "auth_token=server-A")
    }

    @Test func additiveFieldsAndUnknownAccountStatusAreAccepted() async throws {
        let account = "{\"id\":4,\"phone\":\"+123\",\"display_name\":null,\"status\":\"future_status\",\"created_at\":\"2026-10-01T00:00:00Z\",\"new_field\":{\"hello\":true}}"
        let (client, _, _) = try setup([MockReply(body: "[\(account)]")])
        let accounts = try await client.accounts()
        let value = try #require(accounts.first)
        #expect(value.status == "future_status")
        #expect(value.tags.isEmpty && value.enabledFeatures == 0)
    }

    @Test func incompatiblePayloadReturnsSafeErrorNotGeneratedDiagnostics() async throws {
        let (client, _, _) = try setup([MockReply(body: "{\"secret\":\"should-never-appear\"}")])
        do { _ = try await client.currentUser(); Issue.record("Expected failure") }
        catch let error as APIError {
            #expect(error.isIncompatibleResponse)
            #expect(!error.localizedDescription.contains("should-never-appear"))
        }
    }

    @Test func redirectsAreRejectedAndDelegateNeverForwardsCredentials() async throws {
        let (client, script, _) = try setup([MockReply(status: 302, headers: ["Location": "https://evil.example/"])])
        await #expect(throws: APIError.self) { try await client.currentUser() }
        #expect(script.requests.count == 1)
        let delegate = RejectRedirectDelegate()
        let session = URLSession(configuration: .ephemeral)
        let request = URLRequest(url: URL(string: "https://evil.example")!)
        let task = session.dataTask(with: request)
        delegate.urlSession(session, task: task,
                            willPerformHTTPRedirection: HTTPURLResponse(url: URL(string: origin)!, statusCode: 302, httpVersion: nil, headerFields: nil)!,
                            newRequest: request) { redirected in #expect(redirected == nil) }
        session.invalidateAndCancel()
    }

    @Test func clearFailureIsVisibleAndRetryDeletesPersistedSession() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "persisted", expiresAt: .distantFuture), origin: origin)
        let (client, script, _) = try setup([userReply, userReply], store: store)
        _ = try await client.currentUser()
        store.setRejectClear(true)
        await #expect(throws: APIError.self) { try await client.clearLocalSession() }
        _ = try await client.currentUser()
        #expect(script.requests[1].value(forHTTPHeaderField: "Cookie") == nil)
        #expect(try store.load(origin: origin)?.token == "persisted")
        store.setRejectClear(false)
        try await client.clearLocalSession()
        #expect(try store.load(origin: origin) == nil)
    }

    @Test func loginSuccessWithoutExpiringCookieIsRejected() async throws {
        let (client, _, store) = try setup([csrfReply, MockReply(headers: ["Set-Cookie": "auth_token=no-expiry; Path=/; Secure"], body: "{\"ok\":true}")])
        await #expect(throws: APIError.self) { try await client.login(.init(username: "a", password: "b")) }
        #expect(try store.load(origin: origin) == nil)
    }

    @Test func serverVersionDoesNotRequireMatchingAppVersion() async throws {
        let (client, _, _) = try setup([MockReply(body: "{\"version\":\"99.123.456\",\"channel\":\"beta\",\"future\":true}")])
        #expect(try await client.version().version == "99.123.456")
    }

    @Test func ambiguousMutationResponsesNeverEncourageBlindRetry() async throws {
        for response in [MockReply(status: 500), MockReply(body: "not-json"), MockReply(body: "{\"ok\":false}")] {
            let (client, script, _) = try setup([csrfReply, response])
            do { try await client.resumeAccount(id: 1); Issue.record("Expected failure") }
            catch let error as APIError { #expect(error.isOutcomeUncertain) }
            #expect(script.requests.count == 2)
        }
    }

    @Test func healthPreservesActualCountersAndRejectsMissingTelemetry() async throws {
        let complete = "{\"total\":3,\"by_status\":{\"paused\":2,\"future\":1},\"runtime_alive\":1,\"runtime_desired_running\":2,\"runtime_desired_running_alive\":1,\"runtime_failing\":1}"
        func health(_ workers: String) -> MockReply {
            MockReply(body: "{\"db\":{\"ok\":true},\"redis\":{\"ok\":true},\"alembic\":{\"ok\":false,\"error\":\"pending\"},\"workers\":\(workers),\"providers\":{},\"proxies\":{},\"codex_gateway\":{}}")
        }
        let (client, _, _) = try setup([health(complete), health("{}")])
        let value = try await client.health()
        #expect(value.workers.runtimeAlive == 1 && value.workers.runtimeDesiredRunning == 2)
        #expect(value.workers.runtimeFailing == 1 && value.workers.byStatus["future"] == 1)
        #expect(!value.alembic.ok && value.alembic.error == "pending")
        await #expect(throws: APIError.self) { try await client.health() }
    }

    @Test func resourcesPreserveNullableMetricsAndRuntimeStatus() async throws {
        let response = MockReply(body: "{\"host\":{\"sampled_at\":123,\"cpu_percent\":null,\"memory_used_percent\":45.5,\"uptime_seconds\":60},\"workers\":[{\"account_id\":4,\"alive\":false,\"desired\":\"future\",\"fail_count\":2}],\"main_process\":{},\"project_total\":{},\"logs\":{}}")
        let (client, _, _) = try setup([response])
        let value = try await client.resources()
        #expect(value.host.cpuPercent == nil && value.host.memoryUsedPercent == 45.5)
        #expect(value.host.uptimeSeconds == 60 && value.workers[0].desired == "future")
        #expect(value.workers[0].failCount == 2 && !value.workers[0].alive)
    }

    @Test func concurrentWritesAlwaysSendMatchingDoubleSubmitTokens() async throws {
        let (client, _, _) = try setup([])
        let requests = Script([])
        MockProtocol.lock.withLock {
            MockProtocol.handler = { request in
                _ = requests.handle(request)
                if request.httpMethod == "GET" {
                    return MockReply(headers: ["Set-Cookie": "csrf_token=\(UUID().uuidString); Path=/; Max-Age=3600; Secure"])
                }
                let token = request.value(forHTTPHeaderField: "X-CSRF-Token") ?? "missing"
                #expect(request.value(forHTTPHeaderField: "Cookie") == "csrf_token=\(token)")
                return MockReply(body: "{\"ok\":true}")
            }
        }
        async let first: Void = client.pauseAccount(id: 1)
        async let second: Void = client.pauseAccount(id: 2)
        _ = try await (first, second)
        #expect(requests.requests.filter { $0.httpMethod == "POST" }.count == 2)
    }

    @Test func clearingDuringLoginCannotResurrectLateSessionCookie() async throws {
        let (client, _, store) = try setup([])
        let started = DispatchSemaphore(value: 0)
        let resume = DispatchSemaphore(value: 0)
        let csrf = csrfReply
        let login = loginReply
        MockProtocol.lock.withLock {
            MockProtocol.handler = { request in
                if request.httpMethod == "GET" { return csrf }
                started.signal()
                #expect(resume.wait(timeout: .now() + 5) == .success)
                return login
            }
        }
        let attempt = Task { try await client.login(.init(username: "admin", password: "secret")) }
        await withCheckedContinuation { continuation in
            DispatchQueue.global().async {
                #expect(started.wait(timeout: .now() + 5) == .success)
                continuation.resume()
            }
        }
        try await client.clearLocalSession()
        resume.signal()
        await #expect(throws: APIError.self) { try await attempt.value }
        #expect(try store.load(origin: origin) == nil)
    }

    @Test func logoutSuccessSendsCsrfAndClearsSession() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "old-session", expiresAt: .distantFuture), origin: origin)
        let deleted = MockReply(headers: ["Set-Cookie": "auth_token=; Path=/; Max-Age=0; Secure; HttpOnly"], body: "{\"ok\":true}")
        let (client, script, _) = try setup([csrfReply, deleted, userReply], store: store)
        try await client.logout()
        #expect(script.requests[1].value(forHTTPHeaderField: "X-CSRF-Token") == "csrf-one")
        #expect(try store.load(origin: origin) == nil)
        _ = try await client.currentUser()
        #expect(script.requests[2].value(forHTTPHeaderField: "Cookie") == nil)
    }

    @Test func fractionalServerDatesDecodeForListAndDetail() async throws {
        let account = "{\"id\":4,\"phone\":\"+123\",\"display_name\":null,\"status\":\"paused\",\"created_at\":\"2026-10-01T00:00:00.123456Z\"}"
        let (client, _, _) = try setup([MockReply(body: "[\(account)]"), MockReply(body: account)])
        #expect(try await client.accounts().count == 1)
        #expect(try await client.account(id: 4).id == 4)
    }

    @Test func loginCannotReuseOlderCookieWhenServerOmitsNewSession() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "previous-user", expiresAt: .distantFuture), origin: origin)
        let (client, _, _) = try setup([csrfReply, MockReply(body: "{\"ok\":true}")], store: store)
        await #expect(throws: APIError.self) { try await client.login(.init(username: "other-user", password: "secret")) }
    }

    @Test func pluginInstallUsesLongTimeoutTransport() {
        #expect(SessionTransport.isSlowOperation("/api/plugin-repos/3/plugins/interaction-ai-chat/install", write: true))
        #expect(SessionTransport.isSlowOperation("/api/plugin-repos/local/plugins/demo/install", write: true))
        #expect(SessionTransport.isSlowOperation("/api/plugin-repos/3/refresh", write: true))
        #expect(SessionTransport.isSlowOperation("/api/plugin-repos/3/update-installed", write: true))
        #expect(SessionTransport.isSlowOperation("/api/remote-plugins/install", write: true))
        #expect(SessionTransport.isSlowOperation("/api/remote-plugins/demo/update", write: true))
        #expect(SessionTransport.isSlowOperation("/api/plugins/install/upload", write: true))
        #expect(SessionTransport.isSlowOperation("/api/system/pull-update", write: true))
        #expect(!SessionTransport.isSlowOperation("/api/plugin-repos/3/plugins", write: false))
        #expect(!SessionTransport.isSlowOperation("/api/plugins/install/demo/enable", write: true))
        #expect(!SessionTransport.isSlowOperation("/api/accounts/1/features/demo", write: true))
    }

    @Test func pluginInstallConflictExplainsAlreadyInstalled() {
        let message = APIError.server(status: 409, code: "PLUGIN_EXISTS").errorDescription ?? ""
        #expect(message.contains("已安装"))
        #expect(!message.contains("HTTP 409"))
    }
}
