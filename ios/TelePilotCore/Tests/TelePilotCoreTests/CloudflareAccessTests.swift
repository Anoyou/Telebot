import Foundation
import Testing
@testable import TelePilotCore

private actor AccessStreamEvents {
    var count = 0
    func append() { count += 1 }
}

extension ClientTests {
    var accessToken: CloudflareAccessCredentials {
        get throws { try CloudflareAccessCredentials(clientID: "abc123.access", clientSecret: "s3cr3t-value") }
    }

    private func accessError(_ operation: () async throws -> Void) async -> APIError? {
        do { try await operation(); return nil }
        catch let error as APIError { return error }
        catch { return nil }
    }

    // MARK: 头注入

    @Test func serviceTokenHeadersAreSentOnEveryRequest() async throws {
        let access = MemoryAccessStore()
        try access.save(accessToken, origin: origin)
        let stream = MockReply(headers: ["Content-Type": "application/x-ndjson"], body: "{\"type\":\"done\",\"ok\":true}\n")
        let (client, script, _) = try setup([csrfReply, loginReply, userReply, MockReply(body: "{\"ok\":true}"), stream],
                                            access: access)
        #expect(try await client.login(LoginRequest(username: "admin", password: "secret")).ok)
        _ = try await client.currentUser()
        _ = try await client.request(path: "/api/plugins/demo/reload", method: "POST")
        let events = AccessStreamEvents()
        try await client.stream(path: "/api/system-agent/runs/run-one/stream") { _ in await events.append() }
        #expect(await events.count == 1)
        let requests = script.requests
        #expect(requests.count == 5)
        for request in requests {
            #expect(request.value(forHTTPHeaderField: "CF-Access-Client-Id") == "abc123.access")
            #expect(request.value(forHTTPHeaderField: "CF-Access-Client-Secret") == "s3cr3t-value")
        }
        // Service Token 不会混进 TelePilot 的 Cookie。
        #expect(requests.allSatisfy { !($0.value(forHTTPHeaderField: "Cookie") ?? "").contains("s3cr3t") })
    }

    @Test func serviceTokenChangesApplyImmediatelyAndClearRemovesHeaders() async throws {
        let access = MemoryAccessStore()
        let (client, script, _) = try setup([userReply, userReply, userReply], access: access)
        _ = try await client.currentUser()
        try await client.setCloudflareAccess(accessToken)
        #expect(try await client.cloudflareAccess() == accessToken)
        #expect(try access.load(origin: origin) == accessToken)
        _ = try await client.currentUser()
        try await client.setCloudflareAccess(nil)
        #expect(try await client.cloudflareAccess() == nil)
        #expect(try access.load(origin: origin) == nil)
        _ = try await client.currentUser()
        let ids = script.requests.map { $0.value(forHTTPHeaderField: "CF-Access-Client-Id") }
        #expect(ids == [nil, "abc123.access", nil])
    }

    @Test func serviceTokensAreIsolatedPerServer() async throws {
        let access = MemoryAccessStore()
        try access.save(accessToken, origin: "https://other.example")
        let (client, script, _) = try setup([userReply], access: access)
        _ = try await client.currentUser()
        #expect(script.requests.first?.value(forHTTPHeaderField: "CF-Access-Client-Id") == nil)
    }

    @Test func credentialInputIsTrimmedAndUnsafeValuesRejected() throws {
        let token = try CloudflareAccessCredentials(clientID: "  id.access \n", clientSecret: "\tsecret ")
        #expect(token.clientID == "id.access" && token.clientSecret == "secret")
        for (id, secret) in [("", "secret"), ("id", ""), ("id\r\nX-Evil: 1", "secret"), ("id", "sec ret"), ("身份", "secret")] {
            #expect(throws: APIError.self) { try CloudflareAccessCredentials(clientID: id, clientSecret: secret) }
        }
        let description = APIError.cloudflareAccessTokenInvalid.errorDescription ?? ""
        #expect(description.contains("Client ID"))
    }

    // MARK: Access 拦截识别

    @Test func accessLoginRedirectIsIdentifiedWithoutClearingSession() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "persisted", expiresAt: .distantFuture), origin: origin)
        let redirect = MockReply(status: 302, headers: ["Location": "https://team.cloudflareaccess.com/cdn-cgi/access/login/telepilot.example?kid=1"])
        let (client, script, _) = try setup([redirect], store: store)
        let error = await accessError { _ = try await client.currentUser() }
        guard case .cloudflareAccessRequired(let configured) = error else { Issue.record("Expected Access error, got \(String(describing: error))"); return }
        #expect(!configured)
        #expect(error?.errorDescription == "这台服务器启用了 Cloudflare Access，请填写 Service Token。")
        #expect(error?.isCloudflareAccess == true && error?.isUnauthorized == false && error?.isOutcomeUncertain == false)
        #expect(script.requests.count == 1)
        #expect(try store.load(origin: origin)?.token == "persisted")
    }

    @Test func sameHostAccessPathRedirectIsIdentifiedDuringLogin() async throws {
        let redirect = MockReply(status: 302, headers: ["Location": "/cdn-cgi/access/login?redirect_url=%2F"])
        let (client, script, _) = try setup([redirect])
        let error = await accessError { _ = try await client.login(LoginRequest(username: "admin", password: "secret")) }
        #expect(error?.isCloudflareAccess == true)
        // 被拦截在 CSRF 预取，登录请求本身没有发出，也没有重试。
        #expect(script.requests.map { $0.url!.path } == ["/api/auth/csrf"])
    }

    @Test func otherRedirectsStayRejected() async throws {
        for location in ["https://evil.example/", "https://cloudflareaccess.com.evil.example/login",
                         "https://evilcloudflareaccess.com/", "https://evil.example/cdn-cgi/access/login"] {
            let (client, _, _) = try setup([MockReply(status: 302, headers: ["Location": location])])
            let error = await accessError { _ = try await client.currentUser() }
            guard case .redirectRejected = error else { Issue.record("\(location) → \(String(describing: error))"); continue }
        }
    }

    @Test func accessRejectionOfConfiguredTokenIsExplainedAndKeepsSession() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "persisted", expiresAt: .distantFuture), origin: origin)
        let access = MemoryAccessStore()
        try access.save(accessToken, origin: origin)
        let forbidden = MockReply(status: 403, headers: ["Content-Type": "text/html", "Server": "cloudflare"],
                                  body: "<html><title>Forbidden</title></html>")
        let (client, _, _) = try setup([forbidden], store: store, access: access)
        let error = await accessError { _ = try await client.currentUser() }
        guard case .cloudflareAccessRequired(let configured) = error else { Issue.record("Expected Access error"); return }
        #expect(configured)
        #expect(error?.errorDescription?.contains("Service Auth") == true)
        #expect(try store.load(origin: origin)?.token == "persisted")
    }

    @Test func accessBlockOnWriteIsCertainAndNeverRetried() async throws {
        let access = MemoryAccessStore()
        try access.save(accessToken, origin: origin)
        let unauthorized = MockReply(status: 401, headers: ["Content-Type": "text/html"],
                                     body: "<a href=\"https://team.cloudflareaccess.com/cdn-cgi/access/login\">Sign in</a>")
        let (client, script, _) = try setup([csrfReply, unauthorized], access: access)
        let error = await accessError { try await client.pauseAccount(id: 3) }
        #expect(error?.isCloudflareAccess == true)
        #expect(error?.isOutcomeUncertain == false)
        #expect(script.requests.count == 2)
    }

    @Test func telePilotJson401StillClearsSessionWhenTokenConfigured() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "persisted", expiresAt: .distantFuture), origin: origin)
        let access = MemoryAccessStore()
        try access.save(accessToken, origin: origin)
        let expired = MockReply(status: 401, headers: ["Server": "cloudflare"], body: "{\"detail\":{\"code\":\"AUTH_EXPIRED\"}}")
        let (client, _, _) = try setup([expired], store: store, access: access)
        let error = await accessError { _ = try await client.currentUser() }
        #expect(error?.isUnauthorized == true)
        #expect(try store.load(origin: origin) == nil)
    }

    @Test func streamAccessRedirectIsIdentified() async throws {
        let redirect = MockReply(status: 302, headers: ["Location": "https://team.cloudflareaccess.com/cdn-cgi/access/login/x"])
        let (client, _, _) = try setup([redirect])
        let error = await accessError { try await client.stream(path: "/api/system-agent/runs/run-one/stream") { _ in } }
        #expect(error?.isCloudflareAccess == true)
    }

    @Test func detectorRules() {
        let origin = URL(string: "https://telepilot.example")!
        #expect(CloudflareAccessDetector.isAccessHost("team.cloudflareaccess.com"))
        #expect(CloudflareAccessDetector.isAccessHost("TEAM.CloudflareAccess.com."))
        #expect(!CloudflareAccessDetector.isAccessHost("cloudflareaccess.com.evil.example"))
        #expect(!CloudflareAccessDetector.isAccessHost("evilcloudflareaccess.com"))
        #expect(!CloudflareAccessDetector.isAccessHost(nil))
        #expect(CloudflareAccessDetector.isAccessRedirect(location: "https://telepilot.example/cdn-cgi/access/authorized", origin: origin))
        #expect(!CloudflareAccessDetector.isAccessRedirect(location: "https://other.example/cdn-cgi/access/login", origin: origin))
        #expect(!CloudflareAccessDetector.isAccessRedirect(location: nil, origin: origin))
        // TelePilot 自己的 JSON 错误永远不算 Access。
        #expect(!CloudflareAccessDetector.isAccessBlock(status: 403, headers: ["Content-Type": "application/json", "Server": "cloudflare"],
                                                        body: Data("{\"detail\":\"cloudflareaccess.com\"}".utf8), tokenSent: true))
        // 没带 Token 时，Cloudflare 边缘的普通 403（如 WAF）不判定为 Access。
        #expect(!CloudflareAccessDetector.isAccessBlock(status: 403, headers: ["Content-Type": "text/html", "Server": "cloudflare"],
                                                        body: Data("<html>Forbidden</html>".utf8), tokenSent: false))
        #expect(!CloudflareAccessDetector.isAccessBlock(status: 403, headers: ["Content-Type": "text/html", "Server": "cloudflare"],
                                                        body: Data("<html>Sorry, you have been blocked</html>".utf8), tokenSent: true))
        #expect(CloudflareAccessDetector.isAccessBlock(status: 403, headers: ["CF-Access-Domain": "telepilot.example"],
                                                       body: Data(), tokenSent: false))
        #expect(CloudflareAccessDetector.isAccessBlock(status: 200, headers: ["Content-Type": "text/html; charset=utf-8"],
                                                       body: Data("<script src=\"https://team.cloudflareaccess.com/x.js\"></script>".utf8), tokenSent: false))
        #expect(!CloudflareAccessDetector.isAccessBlock(status: 500, headers: ["Content-Type": "text/html"],
                                                        body: Data("cloudflareaccess.com".utf8), tokenSent: true))
    }

    @Test func maskedClientIDHidesMiddle() throws {
        let token = try CloudflareAccessCredentials(clientID: "0123456789abcdef.access", clientSecret: "x")
        #expect(token.maskedClientID == "012345…access")
        #expect(try CloudflareAccessCredentials(clientID: "short", clientSecret: "x").maskedClientID == "•••••")
    }
}
