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
    private let store: any SessionStore
    private let accessStore: any CloudflareAccessStore
    private var access: CloudflareAccessCredentials?
    private var accessLoaded = false
    private var auth: StoredSession?
    private var csrf: StoredSession?
    private var loaded = false
    private var generation = 0
    private static let csrfHeader = HTTPField.Name("X-CSRF-Token")!
    private static let requestedWith = HTTPField.Name("X-Requested-With")!
    private static let accessClientID = HTTPField.Name(CloudflareAccessDetector.clientIDHeader)!
    private static let accessClientSecret = HTTPField.Name(CloudflareAccessDetector.clientSecretHeader)!

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
    }

    deinit { session.invalidateAndCancel(); updateSession.invalidateAndCancel() }

    func clear() throws {
        generation += 1
        loaded = true
        auth = nil
        csrf = nil
        try store.clear(origin: origin.absoluteString)
    }

    // MARK: Cloudflare Access Service Token

    /// 本服务器保存的 Service Token（首次读取钥匙串后缓存在内存）。
    func cloudflareAccess() throws -> CloudflareAccessCredentials? {
        if !accessLoaded {
            access = try accessStore.load(origin: origin.absoluteString)
            accessLoaded = true
        }
        return access
    }

    /// 保存或清除（传 nil）本服务器的 Service Token，之后的所有请求立即使用新值。
    func setCloudflareAccess(_ credentials: CloudflareAccessCredentials?) throws {
        if let credentials { try accessStore.save(credentials, origin: origin.absoluteString) }
        else { try accessStore.clear(origin: origin.absoluteString) }
        access = credentials
        accessLoaded = true
    }

    /// 3xx 指向 Access 登录，或 Access 的 401／403／登录页。
    private func isCloudflareAccessResponse(status: Int, headers: [String: String], body: Data) -> Bool {
        if (300..<400).contains(status) {
            let location = headers.first { $0.key.lowercased() == "location" }?.value
            return CloudflareAccessDetector.isAccessRedirect(location: location, origin: origin)
        }
        return CloudflareAccessDetector.isAccessBlock(status: status, headers: headers, body: body, tokenSent: access != nil)
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
        let accessToken = try cloudflareAccess()
        var request = original
        request.headerFields[Self.requestedWith] = "telepilot-ui"
        // Cloudflare Access：每个请求都带 Service Token（值只进请求头，从不写日志或错误信息）。
        request.headerFields[Self.accessClientID] = accessToken?.clientID
        request.headerFields[Self.accessClientSecret] = accessToken?.clientSecret
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
        if isCloudflareAccessResponse(status: response.status.code, headers: headers, body: data) {
            throw APIError.cloudflareAccessRequired(tokenConfigured: accessToken != nil)
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

    // Subscribe to an existing durable run. Cancelling this GET never cancels the server task.
    func stream(path: String, onEvent: @escaping @Sendable (JSONValue) async -> Void) async throws {
        guard Self.validPath(path) else { throw APIError.invalidServerURL }
        try loadAndExpire()
        let accessToken = try cloudflareAccess()
        let epoch = generation
        guard let url = URL(string: origin.absoluteString + path) else { throw APIError.invalidServerURL }
        var request = URLRequest(url: url)
        request.timeoutInterval = 90
        request.setValue("application/x-ndjson", forHTTPHeaderField: "Accept")
        request.setValue("telepilot-ui", forHTTPHeaderField: "X-Requested-With")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        if let auth { request.setValue("auth_token=" + auth.token, forHTTPHeaderField: "Cookie") }
        if let accessToken {
            request.setValue(accessToken.clientID, forHTTPHeaderField: CloudflareAccessDetector.clientIDHeader)
            request.setValue(accessToken.clientSecret, forHTTPHeaderField: CloudflareAccessDetector.clientSecretHeader)
        }
        do {
            let (bytes, response) = try await session.bytes(for: request)
            defer { bytes.task.cancel() }
            guard epoch == generation else { throw APIError.sessionChanged }
            guard let http = response as? HTTPURLResponse else { throw APIError.incompatibleResponse }
            var headers: [String: String] = [:]
            for (key, value) in http.allHeaderFields {
                if let key = key as? String, let value = value as? String { headers[key.lowercased()] = value }
            }
            let isStream = headers["content-type"]?.lowercased().hasPrefix("application/x-ndjson") == true
            if (300..<400).contains(http.statusCode) {
                if isCloudflareAccessResponse(status: http.statusCode, headers: headers, body: Data()) {
                    throw APIError.cloudflareAccessRequired(tokenConfigured: accessToken != nil)
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
                if isCloudflareAccessResponse(status: http.statusCode, headers: headers, body: prefix) {
                    throw APIError.cloudflareAccessRequired(tokenConfigured: accessToken != nil)
                }
            }
            if http.statusCode == 401 { try clear(); throw APIError.unauthorized }
            guard (200..<300).contains(http.statusCode) else { throw APIError.server(status: http.statusCode, code: nil) }
            guard isStream else { throw APIError.incompatibleResponse }
            var line = Data()
            for try await byte in bytes {
                try Task.checkCancellation()
                guard epoch == generation else { throw APIError.sessionChanged }
                if byte == 10 {
                    if !line.isEmpty {
                        let event = try JSONValue(data: line)
                        await onEvent(event)
                        line.removeAll(keepingCapacity: true)
                        if event["type"]?.stringValue == "done" { return }
                    }
                } else {
                    guard line.count < 16 * 1024 * 1024 else { throw APIError.incompatibleResponse }
                    line.append(byte)
                }
            }
            if !line.isEmpty {
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
