import Foundation
import Testing
@testable import TelePilotCore

private actor AccessStreamEvents {
    var count = 0
    func append() { count += 1 }
}

/// 生成测试用 JWT（签名部分随意；App 不验签，只读 exp 与 email）。
func makeJWT(_ claims: [String: Any]) -> String {
    func encode(_ object: [String: Any]) -> String {
        let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        return data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
    return encode(["alg": "RS256", "typ": "JWT"]) + "." + encode(claims) + ".c2lnbmF0dXJl"
}

extension ClientTests {
    var accessToken: CloudflareAccessCredentials {
        get throws { try CloudflareAccessCredentials(clientID: "abc123.access", clientSecret: "s3cr3t-value") }
    }

    func accessSession(expiresIn seconds: TimeInterval = 3600) throws -> CloudflareAccessSession {
        try CloudflareAccessSession(token: makeJWT(["exp": Int(Date().timeIntervalSince1970 + seconds), "email": "owner@example.com",
                                                    "aud": ["app-aud"]]))
    }

    private func accessError(_ operation: () async throws -> Void) async -> APIError? {
        do { try await operation(); return nil }
        catch let error as APIError { return error }
        catch { return nil }
    }

    private func accessKind(_ error: APIError?) -> CloudflareAccessCredentialKind? {
        if case .cloudflareAccessRequired(let kind) = error { return kind }
        return nil
    }

    // MARK: 头注入：Google 验证的登录令牌（主方案）

    @Test func accessSessionTokenIsSentOnEveryRequestIncludingStream() async throws {
        let access = MemoryAccessStore()
        let session = try accessSession()
        try access.save(session: session, origin: origin)
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
            #expect(request.value(forHTTPHeaderField: "cf-access-token") == session.token)
            #expect(request.value(forHTTPHeaderField: "CF-Access-Client-Id") == nil)
        }
        // Access 令牌不会混进 TelePilot 的 Cookie。
        #expect(requests.allSatisfy { !($0.value(forHTTPHeaderField: "Cookie") ?? "").contains(session.token) })
    }

    @Test func expiredAccessSessionIsNotSentAndBlockSaysExpired() async throws {
        let access = MemoryAccessStore()
        try access.save(session: try accessSession(expiresIn: -60), origin: origin)
        let redirect = MockReply(status: 302, headers: ["Location": "https://team.cloudflareaccess.com/cdn-cgi/access/login/telepilot.example?kid=app-aud"])
        let (client, script, _) = try setup([redirect], access: access)
        let error = await accessError { _ = try await client.currentUser() }
        #expect(accessKind(error) == .expiredSession)
        #expect(error?.errorDescription?.contains("过期") == true)
        #expect(script.requests.first?.value(forHTTPHeaderField: "cf-access-token") == nil)
    }

    @Test func rejectedAccessSessionAsksToReverify() async throws {
        let access = MemoryAccessStore()
        try access.save(session: try accessSession(), origin: origin)
        let redirect = MockReply(status: 302, headers: ["Location": "https://team.cloudflareaccess.com/cdn-cgi/access/login/x"])
        let (client, _, _) = try setup([redirect], access: access)
        let error = await accessError { _ = try await client.currentUser() }
        #expect(accessKind(error) == .session)
    }

    @Test func sessionChangesApplyImmediatelyAndPersistPerServer() async throws {
        let access = MemoryAccessStore()
        let (client, script, _) = try setup([userReply, userReply, userReply], access: access)
        _ = try await client.currentUser()
        let session = try accessSession()
        try await client.setCloudflareAccessSession(session)
        #expect(try await client.cloudflareAccessConfig().session == session)
        #expect(try access.load(origin: origin)?.session == session)
        #expect(try access.load(origin: "https://other.example") == nil)
        _ = try await client.currentUser()
        try await client.setCloudflareAccessSession(nil)
        #expect(try access.load(origin: origin) == nil)
        _ = try await client.currentUser()
        #expect(script.requests.map { $0.value(forHTTPHeaderField: "cf-access-token") } == [nil, session.token, nil])
    }

    @Test func jwtExpiryAndEmailAreParsedAndUnsafeTokensRejected() throws {
        let session = try CloudflareAccessSession(token: makeJWT(["exp": 1_893_456_000, "email": "owner@example.com"]))
        #expect(session.expiresAt == Date(timeIntervalSince1970: 1_893_456_000))
        #expect(session.email == "owner@example.com")
        #expect(!session.isExpired(at: Date(timeIntervalSince1970: 1_893_455_999)))
        #expect(session.isExpired(at: Date(timeIntervalSince1970: 1_893_456_000)))
        let roundTrip = try JSONDecoder().decode(CloudflareAccessSession.self, from: JSONEncoder().encode(session))
        #expect(roundTrip == session)
        for bad in ["", "a.b", "a.b.c\r\nX: 1", "not a jwt", makeJWT(["exp": 1]) + "\n.x"] {
            #expect(throws: APIError.self) { try CloudflareAccessSession(token: bad) }
        }
    }

    // MARK: 头注入：Service Token（高级）

    @Test func serviceTokenTakesPrecedenceAndIsSentOnEveryRequest() async throws {
        let access = MemoryAccessStore()
        try access.save(token: accessToken, origin: origin)
        try access.save(session: try accessSession(), origin: origin)
        let (client, script, _) = try setup([csrfReply, loginReply, userReply], access: access)
        #expect(try await client.login(LoginRequest(username: "admin", password: "secret")).ok)
        _ = try await client.currentUser()
        for request in script.requests {
            #expect(request.value(forHTTPHeaderField: "CF-Access-Client-Id") == "abc123.access")
            #expect(request.value(forHTTPHeaderField: "CF-Access-Client-Secret") == "s3cr3t-value")
            #expect(request.value(forHTTPHeaderField: "cf-access-token") == nil)
        }
    }

    @Test func serviceTokenChangesKeepTheGoogleSession() async throws {
        let access = MemoryAccessStore()
        let session = try accessSession()
        try access.save(session: session, origin: origin)
        let (client, script, _) = try setup([userReply, userReply], access: access)
        try await client.setCloudflareAccess(accessToken)
        _ = try await client.currentUser()
        try await client.setCloudflareAccess(nil)
        _ = try await client.currentUser()
        #expect(try access.load(origin: origin)?.session == session)
        #expect(script.requests.map { $0.value(forHTTPHeaderField: "CF-Access-Client-Id") } == ["abc123.access", nil])
        #expect(script.requests.map { $0.value(forHTTPHeaderField: "cf-access-token") } == [nil, session.token])
    }

    @Test func credentialInputIsTrimmedAndUnsafeValuesRejected() throws {
        let token = try CloudflareAccessCredentials(clientID: "  id.access \n", clientSecret: "\tsecret ")
        #expect(token.clientID == "id.access" && token.clientSecret == "secret")
        for (id, secret) in [("", "secret"), ("id", ""), ("id\r\nX-Evil: 1", "secret"), ("id", "sec ret"), ("身份", "secret")] {
            #expect(throws: APIError.self) { try CloudflareAccessCredentials(clientID: id, clientSecret: secret) }
        }
        #expect(APIError.cloudflareAccessTokenInvalid.errorDescription?.contains("Client ID") == true)
    }

    // MARK: Access 拦截识别

    @Test func accessLoginRedirectIsIdentifiedWithoutClearingSession() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "persisted", expiresAt: .distantFuture), origin: origin)
        let redirect = MockReply(status: 302, headers: ["Location": "https://team.cloudflareaccess.com/cdn-cgi/access/login/telepilot.example?kid=1"])
        let (client, script, _) = try setup([redirect], store: store)
        let error = await accessError { _ = try await client.currentUser() }
        #expect(accessKind(error) == CloudflareAccessCredentialKind.none)
        #expect(error?.errorDescription == "这台服务器启用了 Cloudflare Access，请先用 Google 完成 Access 验证。")
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

    @Test func accessRejectionOfServiceTokenIsExplainedAndKeepsSession() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "persisted", expiresAt: .distantFuture), origin: origin)
        let access = MemoryAccessStore()
        try access.save(token: accessToken, origin: origin)
        let forbidden = MockReply(status: 403, headers: ["Content-Type": "text/html", "Server": "cloudflare"],
                                  body: "<html><title>Forbidden</title></html>")
        let (client, _, _) = try setup([forbidden], store: store, access: access)
        let error = await accessError { _ = try await client.currentUser() }
        #expect(accessKind(error) == .serviceToken)
        #expect(error?.errorDescription?.contains("Service Auth") == true)
        #expect(try store.load(origin: origin)?.token == "persisted")
    }

    @Test func accessBlockOnWriteIsCertainAndNeverRetried() async throws {
        let access = MemoryAccessStore()
        try access.save(session: try accessSession(), origin: origin)
        let unauthorized = MockReply(status: 401, headers: ["Content-Type": "text/html"],
                                     body: "<a href=\"https://team.cloudflareaccess.com/cdn-cgi/access/login\">Sign in</a>")
        let (client, script, _) = try setup([csrfReply, unauthorized], access: access)
        let error = await accessError { try await client.pauseAccount(id: 3) }
        #expect(error?.isCloudflareAccess == true)
        #expect(error?.isOutcomeUncertain == false)
        #expect(script.requests.count == 2)
    }

    @Test func telePilotJson401StillClearsSessionWhenAccessConfigured() async throws {
        let store = MemoryStore()
        try store.save(.init(token: "persisted", expiresAt: .distantFuture), origin: origin)
        let access = MemoryAccessStore()
        try access.save(session: try accessSession(), origin: origin)
        let expired = MockReply(status: 401, headers: ["Server": "cloudflare"], body: "{\"detail\":{\"code\":\"AUTH_EXPIRED\"}}")
        let (client, _, _) = try setup([expired], store: store, access: access)
        let error = await accessError { _ = try await client.currentUser() }
        #expect(error?.isUnauthorized == true)
        #expect(try store.load(origin: origin) == nil)
        #expect(try access.load(origin: origin)?.session != nil)
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
        #expect(!CloudflareAccessDetector.isAccessBlock(status: 403, headers: ["Content-Type": "application/json", "Server": "cloudflare"],
                                                        body: Data("{\"detail\":\"cloudflareaccess.com\"}".utf8), tokenSent: true))
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

    // MARK: App 内 Google 验证（cloudflared access login 流程）

    @Test func loginURLMatchesCloudflaredCliRequest() throws {
        let url = try #require(CloudflareAccessLogin.authorizationURL(origin: URL(string: "https://tp.example.com")!,
                                                                     audience: "abc123", publicKey: "AB+/c="))
        #expect(url.absoluteString == "https://tp.example.com/cdn-cgi/access/cli?aud=abc123&edge_token_transfer=true&redirect_url=https%3A%2F%2Ftp.example.com%3Faud%3Dabc123%26token%3DAB%252B%252Fc%253D&send_org_token=true&token=AB%2B%2Fc%3D")
        let ported = try #require(CloudflareAccessLogin.authorizationURL(origin: URL(string: "https://tp.example.com:8443")!,
                                                                        audience: "a", publicKey: "k"))
        #expect(ported.absoluteString.hasPrefix("https://tp.example.com:8443/cdn-cgi/access/cli?"))
        #expect(CloudflareAccessLogin.transferURL(publicKey: "k_-=")?.absoluteString == "https://login.cloudflareaccess.org/transfer/k_-=")
    }

    @Test func audienceComesFromMetadataOrRedirect() {
        let origin = URL(string: "https://telepilot.example")!
        let metadata = makeJWT(["aud": "app-aud", "hostname": "telepilot.example", "type": "match",
                                "auth_domain": "team.cloudflareaccess.com"])
        #expect(CloudflareAccessLogin.audience(fromMetadata: metadata, origin: origin) == "app-aud")
        let listAud = makeJWT(["aud": ["first", "second"], "hostname": "telepilot.example"])
        #expect(CloudflareAccessLogin.audience(fromMetadata: listAud, origin: origin) == "first")
        let otherHost = makeJWT(["aud": "app-aud", "hostname": "evil.example"])
        #expect(CloudflareAccessLogin.audience(fromMetadata: otherHost, origin: origin) == nil)
        #expect(CloudflareAccessLogin.audience(fromRedirect: "https://team.cloudflareaccess.com/cdn-cgi/access/login/telepilot.example?kid=kid-aud&redirect_url=%2F",
                                               origin: origin) == "kid-aud")
        #expect(CloudflareAccessLogin.audience(fromRedirect: "https://evil.example/login?kid=x", origin: origin) == nil)
    }

    @Test func prepareDiscoversAudienceWithoutCredentialsOrRedirects() async throws {
        let access = MemoryAccessStore()
        try access.save(session: try accessSession(), origin: origin)
        let metadata = makeJWT(["aud": "app-aud", "hostname": "telepilot.example", "type": "match"])
        let head = MockReply(status: 302, headers: ["cf-access-metadata": metadata,
                                                    "Location": "https://team.cloudflareaccess.com/cdn-cgi/access/login/x?kid=other"])
        let (client, script, _) = try setup([head], access: access)
        let request = try await client.prepareCloudflareAccessLogin()
        let sent = try #require(script.requests.first)
        #expect(sent.httpMethod == "HEAD")
        #expect(sent.value(forHTTPHeaderField: "cf-access-metadata-request") == "true")
        #expect(sent.value(forHTTPHeaderField: "cf-access-token") == nil)
        #expect(sent.value(forHTTPHeaderField: "Cookie") == nil)
        let components = try #require(URLComponents(url: request.authorizationURL, resolvingAgainstBaseURL: false))
        #expect(components.host == "telepilot.example" && components.path == "/cdn-cgi/access/cli")
        let items = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value ?? "") })
        #expect(items["aud"] == "app-aud")
        #expect(items["send_org_token"] == "true" && items["edge_token_transfer"] == "true")
        #expect((items["token"] ?? "").count == 44) // 32 字节 X25519 公钥的 base64url
        #expect(request.transferURL.absoluteString == "https://login.cloudflareaccess.org/transfer/" + (items["token"] ?? ""))
    }

    @Test func prepareReportsServersWithoutAccess() async throws {
        let (client, _, _) = try setup([MockReply(status: 200, body: "")])
        let error = await accessError { _ = try await client.prepareCloudflareAccessLogin() }
        guard case .cloudflareAccessLogin(.notProtected) = error else { Issue.record("\(String(describing: error))"); return }
    }

    @Test func transferServerErrorFailsWithoutRetryingTelePilot() async throws {
        let metadata = makeJWT(["aud": "app-aud", "hostname": "telepilot.example"])
        let (client, script, store) = try setup([MockReply(status: 302, headers: ["cf-access-metadata": metadata]),
                                                 MockReply(status: 404, body: ""), MockReply(status: 502, body: "bad")])
        let request = try await client.prepareCloudflareAccessLogin()
        let error = await accessError { _ = try await client.completeCloudflareAccessLogin(request) }
        guard case .cloudflareAccessLogin(.failed) = error else { Issue.record("\(String(describing: error))"); return }
        #expect(script.requests.dropFirst().allSatisfy { $0.url?.host == "login.cloudflareaccess.org" && $0.httpMethod == "GET" })
        #expect(script.requests.count == 3)
        #expect(try store.load(origin: origin) == nil)
    }

    #if canImport(CryptoKit)
    /// 用 PyNaCl（与 Go nacl/box 同格式）生成的转交服务响应：解密后得到 app_token。
    @Test func transferResponseDecryptsLikeCloudflared() throws {
        let keyPair = try NaClKeyPair(rawPrivateKey: Data(base64Encoded: "34UND3mVwpHOS71LsOSmUAa7RsFJhL1EZLjWok/rFvs=")!)
        let body = "cIAJbVavoeMoXA6uZb7eHqy1lzc5IaijFR7JTt4iHnKDk7MQoHlYIvJcOAk9ZRWMipUsQUz+UZAp0Ppcq7B6YvZ+gKL1bVp60h7SpA3+c0zbRFN2NYRpOmPxqHfX7EL1qVp8J6tk6z2PGbdedpxBJ9hz5eHXXMEhKB2dhjbgamAbJrLvzgapQyGSobl1fRr+JnJxbX5FGPjyZhc/9zNyhfFLi2gQ2rQ="
        let session = try CloudflareAccessLogin.decrypt(Data(body.utf8), senderPublicKey: "tiVODfm69PD0FCYYzWV9L6c5ZXuTziC7xcaAftDgxQg=", keyPair: keyPair)
        #expect(session.email == "owner@example.com")
        #expect(session.expiresAt == Date(timeIntervalSince1970: 1_893_456_000))
        var tampered = Array(Data(base64Encoded: body)!)
        tampered[tampered.count - 1] ^= 1
        #expect(throws: APIError.self) {
            try CloudflareAccessLogin.decrypt(Data(Data(tampered).base64EncodedString().utf8),
                                              senderPublicKey: "tiVODfm69PD0FCYYzWV9L6c5ZXuTziC7xcaAftDgxQg=", keyPair: keyPair)
        }
    }
    #endif

    @Test func poly1305AndSecretboxMatchReferenceVectors() {
        func hex(_ text: String) -> [UInt8] {
            var out: [UInt8] = []; var index = text.startIndex
            while index < text.endIndex { let next = text.index(index, offsetBy: 2); out.append(UInt8(text[index..<next], radix: 16)!); index = next }
            return out
        }
        // RFC 8439 §2.5.2
        let tag = NaClBox.poly1305(message: Array("Cryptographic Forum Research Group".utf8),
                                   key: hex("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b"))
        #expect(tag == hex("a8061dc1305136c6c22b8baf0c0127a9"))
        // NaCl 测试向量（box.c / box2.c：Alice → Bob），用共享密钥 k 解开。
        let shared = hex("4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")
        let nonce = hex("69696ee955b62b73cd62bda875fc73d68219e0036b7a0b37")
        let boxed = hex("f3ffc7703f9400e52a7dfb4b3d3305d9" + "8e993b9f48681273c29650ba32fc76ce48332ea7164d96a4476fb8c531a1186ac0dfc17c98dce87b4da7f011ec48c97271d2c20f9b928fe2270d6fb863d51738b48eeee314a7cc8ab932164548e526ae90224368517acfeabd6bb3732bc0e9da99832b61ca01b6de56244a9e88d5f9b37973f622a43d14a6599b1f654cb45a74e355a5")
        let plain = NaClBox.open(message: nonce + boxed, sharedSecret: shared)
        #expect(plain.map { Array($0.prefix(8)) } == hex("be075fc53c81f2d5"))
        #expect(plain?.count == 131)
    }
}
