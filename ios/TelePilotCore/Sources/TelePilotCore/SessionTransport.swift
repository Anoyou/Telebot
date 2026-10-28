import Foundation
import HTTPTypes
import OpenAPIRuntime
import OpenAPIURLSession

final class RejectRedirectDelegate: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

actor SessionTransport: ClientTransport {
    private let origin: URL
    private let transport: URLSessionTransport
    private let session: URLSession
    private let updateTransport: URLSessionTransport
    private let updateSession: URLSession
    /// 只用于访问 Cloudflare 令牌转交服务（login.cloudflareaccess.org），不带 TelePilot 任何凭证。
    private let transferSession: URLSession
    private let store: any SessionStore
    private let accessStore: any CloudflareAccessStore
    private var access = CloudflareAccessConfig()
    private var accessLoaded = false
    private var auth: StoredSession?
    private var csrf: StoredSession?
    private var loaded = false
    private var generation = 0
    private static let csrfHeader = HTTPField.Name("X-CSRF-Token")!
    private static let requestedWith = HTTPField.Name("X-Requested-With")!
    private static let accessClientID = HTTPField.Name(CloudflareAccessDetector.clientIDHeader)!
    private static let accessClientSecret = HTTPField.Name(CloudflareAccessDetector.clientSecretHeader)!
    private static let accessToken = HTTPField.Name(CloudflareAccessDetector.accessTokenHeader)!

    init(origin: URL, configuration: URLSessionConfiguration, store: any SessionStore,
         accessStore: any CloudflareAccessStore) {
        self.origin = origin
        self.store = store
        self.accessStore = accessStore
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.urlCredentialStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 3_600
        session = URLSession(configuration: configuration, delegate: RejectRedirectDelegate(), delegateQueue: nil)
        transport = URLSessionTransport(configuration: .init(session: session, httpBodyProcessingMode: .buffered))
        // Update checks wait for synchronous Git operations, as in the Web client.
        configuration.timeoutIntervalForRequest = 150
        updateSession = URLSession(configuration: configuration, delegate: RejectRedirectDelegate(), delegateQueue: nil)
        updateTransport = URLSessionTransport(configuration: .init(session: updateSession, httpBodyProcessingMode: .buffered))
        configuration.timeoutIntervalForRequest = 75
        transferSession = URLSession(configuration: configuration, delegate: RejectRedirectDelegate(), delegateQueue: nil)
    }

    deinit { session.invalidateAndCancel(); updateSession.invalidateAndCancel(); transferSession.invalidateAndCancel() }

    func clear() throws {
        generation += 1
        loaded = true
        auth = nil
        csrf = nil
        try store.clear(origin: origin.absoluteString)
    }

    // MARK: Cloudflare Access

    /// 这台服务器保存的 Access 凭证（首次读取钥匙串后缓存在内存）。
    func cloudflareAccessConfig() throws -> CloudflareAccessConfig {
        if !accessLoaded {
            access = try accessStore.load(origin: origin.absoluteString) ?? CloudflareAccessConfig()
            accessLoaded = true
        }
        return access
    }

    private func saveAccess(_ config: CloudflareAccessConfig) throws {
        if config.isEmpty { try accessStore.clear(origin: origin.absoluteString) }
        else { try accessStore.save(config, origin: origin.absoluteString) }
        access = config
        accessLoaded = true
    }

    /// 保存或清除（nil）Service Token（高级），之后的请求立即生效。
    func setCloudflareAccess(_ credentials: CloudflareAccessCredentials?) throws {
        var config = try cloudflareAccessConfig()
        config.serviceToken = credentials
        try saveAccess(config)
    }

    /// 保存或清除（nil）App 内 Google 验证取得的 Access 登录令牌。
    func setCloudflareAccessSession(_ session: CloudflareAccessSession?) throws {
        var config = try cloudflareAccessConfig()
        config.session = session
        try saveAccess(config)
    }

    /// 本次请求要带的 Access 凭证。配置了 Service Token（高级）时只带它：严格 Service Token 模式下，
    /// 带 Service Token 头的请求只由 Service Auth 策略判断，混带登录令牌没有意义。否则带未过期的登录令牌。
    private func accessCredential(now: Date = Date()) throws -> (headers: [(HTTPField.Name, String)], kind: CloudflareAccessCredentialKind, sent: Bool) {
        let config = try cloudflareAccessConfig()
        if let token = config.serviceToken {
            return ([(Self.accessClientID, token.clientID), (Self.accessClientSecret, token.clientSecret)], .serviceToken, true)
        }
        if let session = config.session {
            if session.isExpired(at: now) { return ([], .expiredSession, false) }
            return ([(Self.accessToken, session.token)], .session, true)
        }
        return ([], .none, false)
    }

    /// 3xx 指向 Access 登录，或 Access 的 401／403／登录页。只读不可变的 `origin`，可在 actor 外调用（流式订阅）。
    private nonisolated func isCloudflareAccessResponse(status: Int, headers: [String: String], body: Data, credentialSent: Bool) -> Bool {
        if (300..<400).contains(status) {
            let location = headers.first { $0.key.lowercased() == "location" }?.value
            return CloudflareAccessDetector.isAccessRedirect(location: location, origin: origin)
        }
        return CloudflareAccessDetector.isAccessBlock(status: status, headers: headers, body: body, tokenSent: credentialSent)
    }

    /// 准备 App 内 Google 验证：发现 Access 应用并生成登录地址（不带任何凭证、不跟随重定向）。
    func prepareCloudflareAccessLogin() async throws -> CloudflareAccessLoginRequest {
        try await CloudflareAccessLogin.prepare(origin: origin, session: session)
    }

    /// 等待用户在系统登录窗完成 Google 登录，取回令牌并保存到钥匙串。
    func completeCloudflareAccessLogin(_ request: CloudflareAccessLoginRequest) async throws -> CloudflareAccessSession {
        let result = try await CloudflareAccessLogin.awaitSession(request, session: transferSession)
        try setCloudflareAccessSession(result)
        return result
    }

    func hasSession() throws -> Bool {
        try loadAndExpire()
        return auth != nil
    }

    private func loadAndExpire() throws {
        if !loaded {
            auth = try store.load(origin: origin.absoluteString)
            loaded = true
        }
        if let auth, auth.expiresAt <= Date() {
            self.auth = nil
            try store.clear(origin: origin.absoluteString)
        }
        if let csrf, csrf.expiresAt <= Date() { self.csrf = nil }
    }

    func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String)
        async throws -> (HTTPResponse, HTTPBody?) {
        guard baseURL == origin, let path = request.path, path.hasPrefix("/api/"),
              !path.hasPrefix("//"), request.authority == nil || request.authority == origin.host() else {
            throw APIError.invalidServerURL
        }
        try loadAndExpire()
        let epoch = generation
        let write = ![HTTPRequest.Method.get, .head, .options].contains(request.method)
        if write { try await ensureCSRF(epoch: epoch) }
        // Buffer the small generated request once, allowing only middleware-rejected CSRF retries.
        let payload = try await body.mapAsync { try await Data(collecting: $0, upTo: 64 * 1024 * 1024) }
        var result = try await execute(request, payload: payload, operationID: operationID, epoch: epoch)
        let code = Self.errorCode(result.1)
        if write, result.0.status.code == 403,
           code == "CSRF_TOKEN_REQUIRED" || code == "CSRF_HEADER_REQUIRED" {
            csrf = nil
            try await ensureCSRF(epoch: epoch)
            result = try await execute(request, payload: payload, operationID: operationID, epoch: epoch)
        }
        try validateStatus(result.0, data: result.1)
        return (result.0, HTTPBody(result.1))
    }

    private func ensureCSRF(epoch: Int) async throws {
        if csrf != nil { return }
        let request = HTTPRequest(method: .get, scheme: nil, authority: nil, path: "/api/auth/csrf")
        let result = try await execute(request, payload: nil, operationID: "csrf", epoch: epoch)
        try validateStatus(result.0, data: result.1)
        guard csrf != nil else { throw APIError.incompatibleResponse }
    }

    private func execute(_ original: HTTPRequest, payload: Data?, operationID: String, epoch: Int)
        async throws -> (HTTPResponse, Data) {
        guard generation == epoch else { throw APIError.sessionChanged }
        try loadAndExpire()
        let credential = try accessCredential()
        var request = original
        request.headerFields[Self.requestedWith] = "telepilot-ui"
        // Cloudflare Access：每个请求都带 Access 凭证（值只进请求头，从不写日志或错误信息）。
        for (name, value) in credential.headers { request.headerFields[name] = value }
        request.headerFields[.cacheControl] = "no-store"
        let cookie = [auth.map { "auth_token=\($0.token)" }, csrf.map { "csrf_token=\($0.token)" }]
            .compactMap { $0 }.joined(separator: "; ")
        request.headerFields[.cookie] = cookie.isEmpty ? nil : cookie
        let write = ![HTTPRequest.Method.get, .head, .options].contains(request.method)
        if write { request.headerFields[Self.csrfHeader] = csrf?.token }
        let response: HTTPResponse
        let data: Data
        do {
            let path = original.path?.split(separator: "?").first.map(String.init)
            let selectedTransport = Self.isSlowOperation(path ?? "", write: write) ? updateTransport : transport
            let result = try await selectedTransport.send(request, body: payload.map(HTTPBody.init),
                                                  baseURL: origin, operationID: operationID)
            response = result.0
            data = try await result.1.mapAsync { try await Data(collecting: $0, upTo: 64 * 1024 * 1024) } ?? Data()
        } catch {
            // Never expose ClientError/URLSession diagnostics, which can include credentials.
            if Task.isCancelled && !write { throw CancellationError() }
            throw APIError.network(outcomeUncertain: write)
        }
        guard generation == epoch else { throw APIError.sessionChanged }
        // 先识别 Cloudflare Access：它的 401 来自 Cloudflare 边缘，不能当成 TelePilot 会话失效而清除登录。
        let headers = Dictionary(response.headerFields.map { ($0.name.canonicalName, $0.value) },
                                 uniquingKeysWith: { first, _ in first })
        if isCloudflareAccessResponse(status: response.status.code, headers: headers, body: data,
                                      credentialSent: credential.sent) {
            throw APIError.cloudflareAccessRequired(credential.kind)
        }
        // 其他重定向一律拒绝，不跟随、不转发凭据（安全规则不变）。
        if (300..<400).contains(response.status.code) { throw APIError.redirectRejected }
        if response.status.code == 401 {
            try clear()
            throw APIError.server(status: 401, code: Self.errorCode(data))
        }
        let receivedSession = try acceptCookies(response)
        if operationID == "login_api_auth_login_post", (200..<300).contains(response.status.code),
           let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           payload["ok"] as? Bool == true, !receivedSession {
            try clear()
            throw APIError.incompatibleResponse
        }
        return (response, data)
    }

    private func validateStatus(_ response: HTTPResponse, data: Data) throws {
        guard (200..<300).contains(response.status.code) else {
            throw APIError.server(status: response.status.code, code: Self.errorCode(data))
        }
    }

    private func acceptCookies(_ response: HTTPResponse) throws -> Bool {
        var receivedSession = false
        for field in response.headerFields where field.name == .setCookie {
            for cookie in HTTPCookie.cookies(withResponseHeaderFields: ["Set-Cookie": field.value], for: origin) {
                guard ["auth_token", "csrf_token"].contains(cookie.name), cookie.path == "/",
                      cookie.domain.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ".")) == origin.host()?.lowercased(),
                      !cookie.value.contains(where: { $0 == ";" || $0.isNewline }) else { continue }
                let maxAge = field.value.split(separator: ";").compactMap { item -> TimeInterval? in
                    let pair = item.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
                    guard pair.count == 2, pair[0].lowercased() == "max-age" else { return nil }
                    return TimeInterval(pair[1])
                }.first
                let expiry = maxAge.map { Date().addingTimeInterval($0) } ?? cookie.expiresDate
                if cookie.value.isEmpty || (expiry.map { $0 <= Date() } ?? false) {
                    if cookie.name == "auth_token" {
                        auth = nil
                        try store.clear(origin: origin.absoluteString)
                    } else { csrf = nil }
                    continue
                }
                // Auth is persisted only with an explicit server expiry, never indefinitely.
                guard let expiry else { continue }
                let stored = StoredSession(token: cookie.value, expiresAt: expiry)
                if cookie.name == "auth_token" {
                    try store.save(stored, origin: origin.absoluteString)
                    auth = stored
                    receivedSession = true
                } else { csrf = stored }
            }
        }
        return receivedSession
    }

    private static func errorCode(_ data: Data) -> String? {
        guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let detail = (root["error"] ?? root["detail"]) as? [String: Any],
              let code = detail["code"] as? String,
              code.count <= 80, code.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_") }) else { return nil }
        return code
    }

    /// 需要服务器同步执行 Git 操作的请求：沿用较长的请求超时，避免服务器仍在执行时
    /// 客户端先超时，用户误以为失败而重复提交（例如插件安装第二次撞上“已安装”的 409）。
    static func isSlowOperation(_ path: String, write: Bool) -> Bool {
        if ["/api/system/check-update", "/api/system/pull-update", "/api/system/update-target-options"].contains(path) { return true }
        // 一键体检在服务器上顺序跑 10 项只读检查，总预算约 25 秒，超过普通请求的 20 秒超时。
        if !write, path == "/api/system-agent/checkup" { return true }
        let parts = path.split(separator: "/", omittingEmptySubsequences: true)
        // 浏览仓库内插件：首次访问会在服务器上 git clone，与 Web 一样放宽超时（GET /api/plugin-repos/{id}/plugins）。
        if !write, parts.count == 4, parts[0] == "api", parts[1] == "plugin-repos", parts[3] == "plugins", parts[2] != "local" { return true }
        guard write else { return false }
        guard parts.count >= 3, parts[0] == "api" else { return false }
        switch parts[1] {
        case "plugin-repos":
            // /api/plugin-repos/{id}/plugins/{name}/install、/{id}/refresh、/{id}/update-installed
            return parts.last == "install" || parts.last == "refresh" || parts.last == "update-installed"
        case "remote-plugins":
            // /api/remote-plugins/install、/api/remote-plugins/{name}/update、/api/remote-plugins/check-updates
            return parts.last == "install" || parts.last == "update" || parts.last == "check-updates"
        case "commands":
            // 模型诊断会等上游真实回复：/api/commands/llm-providers/{id}/test-model、/chat-test-models、/fetch-models
            return parts.count == 5 && parts[2] == "llm-providers"
                && ["test-model", "chat-test-models", "fetch-models"].contains(String(parts[4]))
        case "plugins":
            // /api/plugins/install/upload
            return parts.count == 4 && parts[2] == "install" && parts[3] == "upload"
        default:
            return false
        }
    }

    static func validPath(_ path: String) -> Bool {
        guard path.hasPrefix("/api/"), !path.contains("#"), !path.contains("\\"),
              let url = URLComponents(string: path), url.scheme == nil, url.host == nil,
              let decoded = url.percentEncodedPath.removingPercentEncoding,
              !decoded.contains("\\") else { return false }
        return !decoded.split(separator: "/", omittingEmptySubsequences: false).contains(where: { $0 == ".." || $0 == "." })
    }

    func raw(path: String, method: String, body: Data?, contentType: String? = nil) async throws -> Data {
        guard Self.validPath(path), ["GET", "POST", "PATCH", "PUT", "DELETE"].contains(method),
              let httpMethod = HTTPRequest.Method(rawValue: method) else {
            throw APIError.invalidServerURL
        }
        var request = HTTPRequest(method: httpMethod, scheme: nil, authority: nil, path: path)
        if let contentType { request.headerFields[.contentType] = contentType }
        let result = try await send(request, body: body.map(HTTPBody.init), baseURL: origin, operationID: "native_" + method)
        return try await result.1.mapAsync { try await Data(collecting: $0, upTo: 64 * 1024 * 1024) } ?? Data()
    }

    /// 订阅一个运行中任务的 NDJSON 进度流前，在 actor 上取一次凭据快照。
    private struct StreamPreparation: Sendable {
        let request: URLRequest
        let credentialKind: CloudflareAccessCredentialKind
        let credentialSent: Bool
        let epoch: Int
    }

    private func prepareStream(path: String) throws -> StreamPreparation {
        try loadAndExpire()
        let credential = try accessCredential()
        guard let url = URL(string: origin.absoluteString + path) else { throw APIError.invalidServerURL }
        var request = URLRequest(url: url)
        request.timeoutInterval = 90
        request.setValue("application/x-ndjson", forHTTPHeaderField: "Accept")
        request.setValue("telepilot-ui", forHTTPHeaderField: "X-Requested-With")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        if let auth { request.setValue("auth_token=" + auth.token, forHTTPHeaderField: "Cookie") }
        for (name, value) in credential.headers { request.setValue(value, forHTTPHeaderField: name.rawName) }
        return StreamPreparation(request: request, credentialKind: credential.kind, credentialSent: credential.sent, epoch: generation)
    }

    private func isCurrent(_ epoch: Int) -> Bool { epoch == generation }

    private func expireAfterStreamUnauthorized() throws { try clear() }

    // Subscribe to an existing durable run. Cancelling this GET never cancels the server task.
    //
    // 第十一轮：`nonisolated`。原来在 actor 内逐字节读取，每个字节都检查取消与会话代次，缓冲区有数据时
    // actor 一直被流占住，其他请求只能在事件间隙进入。现在凭据快照之后在 actor 外按行（`\n`）切分，
    // 每行只回到 actor 核对一次会话代次。服务器用 ensure_ascii=False 输出，字符串里可能有 U+2028，
    // 所以只按 `\n` 切分，不用会把 Unicode 行分隔符也当换行的 `bytes.lines`。
    nonisolated func stream(path: String, onEvent: @escaping @Sendable (JSONValue) async -> Void) async throws {
        guard Self.validPath(path) else { throw APIError.invalidServerURL }
        let prepared = try await prepareStream(path: path)
        do {
            let (bytes, response) = try await session.bytes(for: prepared.request)
            defer { bytes.task.cancel() }
            guard await isCurrent(prepared.epoch) else { throw APIError.sessionChanged }
            guard let http = response as? HTTPURLResponse else { throw APIError.incompatibleResponse }
            var headers: [String: String] = [:]
            for (key, value) in http.allHeaderFields {
                if let key = key as? String, let value = value as? String { headers[key.lowercased()] = value }
            }
            let isStream = headers["content-type"]?.lowercased().hasPrefix("application/x-ndjson") == true
            if (300..<400).contains(http.statusCode) {
                if isCloudflareAccessResponse(status: http.statusCode, headers: headers, body: Data(),
                                              credentialSent: prepared.credentialSent) {
                    throw APIError.cloudflareAccessRequired(prepared.credentialKind)
                }
                throw APIError.redirectRejected
            }
            if !(200..<300).contains(http.statusCode) || !isStream {
                // 只读取一小段正文用于识别 Access 拒绝页，不把内容带进错误信息。
                var prefix = Data()
                for try await byte in bytes {
                    prefix.append(byte)
                    if prefix.count >= 64 * 1024 { break }
                }
                if isCloudflareAccessResponse(status: http.statusCode, headers: headers, body: prefix,
                                              credentialSent: prepared.credentialSent) {
                    throw APIError.cloudflareAccessRequired(prepared.credentialKind)
                }
            }
            if http.statusCode == 401 { try await expireAfterStreamUnauthorized(); throw APIError.unauthorized }
            guard (200..<300).contains(http.statusCode) else { throw APIError.server(status: http.statusCode, code: nil) }
            guard isStream else { throw APIError.incompatibleResponse }
            var line = Data()
            line.reserveCapacity(4096)
            for try await byte in bytes {
                if byte == 10 {
                    guard !line.isEmpty else { continue }
                    try Task.checkCancellation()
                    guard await isCurrent(prepared.epoch) else { throw APIError.sessionChanged }
                    let event = try JSONValue(data: line)
                    line.removeAll(keepingCapacity: true)
                    await onEvent(event)
                    if event["type"]?.stringValue == "done" { return }
                } else {
                    guard line.count < 16 * 1024 * 1024 else { throw APIError.incompatibleResponse }
                    line.append(byte)
                }
            }
            if !line.isEmpty {
                guard await isCurrent(prepared.epoch) else { throw APIError.sessionChanged }
                let event = try JSONValue(data: line)
                await onEvent(event)
                if event["type"]?.stringValue == "done" { return }
            }
            throw APIError.network(outcomeUncertain: false)
        } catch let error as APIError { throw error }
        catch {
            if Task.isCancelled { throw CancellationError() }
            throw APIError.network(outcomeUncertain: false)
        }
    }
}

private extension Optional where Wrapped: Sendable {
    func mapAsync<T: Sendable>(_ transform: (Wrapped) async throws -> T) async rethrows -> T? {
        switch self {
        case .some(let value): try await transform(value)
        case .none: nil
        }
    }
}
