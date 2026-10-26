import Foundation
#if canImport(Security)
import Security
#endif

/// 请求带的是哪种 Cloudflare Access 凭证；决定被拦截时怎样提示用户。
public enum CloudflareAccessCredentialKind: Sendable, Equatable {
    /// 没有任何 Access 凭证。
    case none
    /// 带了 App 内 Google 验证取得的 Access 登录令牌，但被拒绝（已在后台失效、策略变更等）。
    case session
    /// 保存的 Access 登录令牌已过期，没有再发送。
    case expiredSession
    /// 带了 Service Token（高级），但被拒绝。
    case serviceToken
}

/// App 内 Google 验证（cloudflared `access login` 同款流程）失败的原因。
public enum CloudflareAccessLoginFailure: Sendable, Equatable {
    /// 服务器前没有 Cloudflare Access，或找不到对应的 Access 应用。
    case notProtected
    /// 用户关闭了登录窗口。
    case cancelled
    /// 等待登录完成超时。
    case timedOut
    /// 转交服务返回错误或数据无法解密。
    case failed
}

/// Cloudflare Access Service Token（Zero Trust → Access → Service Auth → Service Tokens 创建）。
/// 高级选项：需要在 Access 应用里另加一条 Action 为 Service Auth 的策略。
/// 只保存在本机钥匙串，按服务器地址隔离；不写入日志，也不出现在错误信息里。
public struct CloudflareAccessCredentials: Codable, Sendable, Equatable {
    public let clientID: String
    public let clientSecret: String

    /// 校验并规范化用户输入。两个值都必须填写，且只能是可见 ASCII 字符（不含空白），
    /// 防止换行等字符被拼进 HTTP 头。
    public init(clientID: String, clientSecret: String) throws {
        let id = clientID.trimmingCharacters(in: .whitespacesAndNewlines)
        let secret = clientSecret.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.isValidHeaderValue(id), Self.isValidHeaderValue(secret) else {
            throw APIError.cloudflareAccessTokenInvalid
        }
        self.clientID = id
        self.clientSecret = secret
    }

    static func isValidHeaderValue(_ value: String) -> Bool {
        !value.isEmpty && value.count <= 1024
            && value.unicodeScalars.allSatisfy { $0.value > 0x20 && $0.value < 0x7F }
    }

    /// 用于界面展示：只露出 Client ID 的前后几位。
    public var maskedClientID: String {
        guard clientID.count > 12 else { return String(repeating: "•", count: max(clientID.count, 4)) }
        return String(clientID.prefix(6)) + "…" + String(clientID.suffix(6))
    }

    enum CodingKeys: String, CodingKey { case clientID = "client_id", clientSecret = "client_secret" }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(clientID: container.decode(String.self, forKey: .clientID),
                      clientSecret: container.decode(String.self, forKey: .clientSecret))
    }
}

/// App 内 Google 验证取得的 Cloudflare Access 应用令牌（JWT，与浏览器的 `CF_Authorization` 同类）。
/// 请求时放在 `cf-access-token` 请求头；只保存在本机钥匙串，按服务器隔离。
public struct CloudflareAccessSession: Codable, Sendable, Equatable {
    public let token: String
    /// JWT 的 `exp`；Access 会话到期时间。
    public let expiresAt: Date?
    /// JWT 中的登录邮箱（仅用于界面显示）。
    public let email: String?

    /// 解析 JWT 载荷（不验签：令牌由 Cloudflare 边缘校验，这里只读取到期时间与邮箱用于显示）。
    public init(token raw: String) throws {
        let token = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let parts = token.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3, token.count <= 16 * 1024,
              token.unicodeScalars.allSatisfy(Self.isJWTScalar),
              let payloadData = Self.decodeBase64URL(String(parts[1])),
              let payload = try? JSONSerialization.jsonObject(with: payloadData) as? [String: Any] else {
            throw APIError.cloudflareAccessLogin(.failed)
        }
        self.token = token
        if let exp = (payload["exp"] as? NSNumber)?.doubleValue { expiresAt = Date(timeIntervalSince1970: exp) }
        else { expiresAt = nil }
        email = payload["email"] as? String
    }

    /// JWT 只允许 base64url 字符与点，保证可以安全放进请求头。
    static func isJWTScalar(_ scalar: Unicode.Scalar) -> Bool {
        switch scalar.value {
        case 0x30...0x39, 0x41...0x5A, 0x61...0x7A, 0x2D, 0x5F, 0x2E: true
        default: false
        }
    }

    public func isExpired(at date: Date = Date()) -> Bool { expiresAt.map { $0 <= date } ?? false }

    static func decodeBase64URL(_ text: String) -> Data? {
        var value = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while value.count % 4 != 0 { value += "=" }
        return Data(base64Encoded: value)
    }

    enum CodingKeys: String, CodingKey { case token }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(token: container.decode(String.self, forKey: .token))
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(token, forKey: .token)
    }
}

/// 一台服务器的 Cloudflare Access 凭证：App 内 Google 验证的会话（主方案）与可选的 Service Token（高级）。
public struct CloudflareAccessConfig: Codable, Sendable, Equatable {
    public var session: CloudflareAccessSession?
    public var serviceToken: CloudflareAccessCredentials?

    public init(session: CloudflareAccessSession? = nil, serviceToken: CloudflareAccessCredentials? = nil) {
        self.session = session
        self.serviceToken = serviceToken
    }

    public var isEmpty: Bool { session == nil && serviceToken == nil }

    enum CodingKeys: String, CodingKey { case session, serviceToken = "service_token" }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        // 单项损坏时只丢弃该项，不影响另一项。
        session = try? container.decodeIfPresent(CloudflareAccessSession.self, forKey: .session)
        serviceToken = try? container.decodeIfPresent(CloudflareAccessCredentials.self, forKey: .serviceToken)
    }
}

protocol CloudflareAccessStore: Sendable {
    func load(origin: String) throws -> CloudflareAccessConfig?
    func save(_ config: CloudflareAccessConfig, origin: String) throws
    func clear(origin: String) throws
}

#if canImport(Security)
struct KeychainCloudflareAccessStore: CloudflareAccessStore {
    private func query(_ origin: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: "TelePilot.Native.CloudflareAccess",
         kSecAttrAccount as String: origin,
         kSecAttrSynchronizable as String: false]
    }

    func load(origin: String) throws -> CloudflareAccessConfig? {
        var query = query(origin)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw APIError.secureStorage }
        guard let config = try? JSONDecoder().decode(CloudflareAccessConfig.self, from: data) else {
            try clear(origin: origin)
            return nil
        }
        return config
    }

    func save(_ config: CloudflareAccessConfig, origin: String) throws {
        if config.isEmpty { try clear(origin: origin); return }
        let data = try JSONEncoder().encode(config)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]
        let status = SecItemUpdate(query(origin) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query(origin)
            item.merge(attributes) { _, new in new }
            guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw APIError.secureStorage }
        } else if status != errSecSuccess { throw APIError.secureStorage }
    }

    func clear(origin: String) throws {
        let status = SecItemDelete(query(origin) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw APIError.secureStorage }
    }
}
#endif

/// 识别 Cloudflare Access 的拦截响应。只做识别，不跟随任何重定向：
/// Access 拦截发生在 Cloudflare 边缘，请求没有到达 TelePilot，因此写操作的结果是确定的“未执行”。
public enum CloudflareAccessDetector {
    public static let clientIDHeader = "CF-Access-Client-Id"
    public static let clientSecretHeader = "CF-Access-Client-Secret"
    /// App 内 Google 验证取得的应用令牌所用请求头（与 `cloudflared access curl` 相同）。
    public static let accessTokenHeader = "cf-access-token"

    /// `*.cloudflareaccess.com`（团队登录域名）。
    public static func isAccessHost(_ host: String?) -> Bool {
        guard let host = host?.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ".")), !host.isEmpty else { return false }
        return host == "cloudflareaccess.com" || host.hasSuffix(".cloudflareaccess.com")
    }

    /// 3xx 的 Location 指向 `*.cloudflareaccess.com` 或本站的 `/cdn-cgi/access/`。
    public static func isAccessRedirect(location: String?, origin: URL) -> Bool {
        guard let location = location?.trimmingCharacters(in: .whitespaces), !location.isEmpty,
              let url = URL(string: location, relativeTo: origin)?.absoluteURL else { return false }
        if isAccessHost(url.host()) { return true }
        let sameHost = url.host()?.lowercased() == origin.host()?.lowercased()
        return sameHost && url.path().lowercased().hasPrefix("/cdn-cgi/access/")
    }

    /// 非重定向的 Access 响应：Service Token 无效时的 401／403（严格 Service Token 模式或 Service Auth 策略不匹配），
    /// 或旧行为下直接以 200 返回的 Access 登录页。TelePilot 自己的错误始终是 JSON，因此 JSON 响应一律不算。
    /// - Parameters:
    ///   - headers: 响应头，名称不区分大小写。
    ///   - tokenSent: 本次请求是否带了 Access 凭证（登录令牌或 Service Token）。只有带了凭证时，才把 Cloudflare 边缘返回的
    ///     无 Access 字样的 401／403 视为 Access 拒绝（严格 Service Token 模式），避免把 WAF 拦截误判为 Access。
    public static func isAccessBlock(status: Int, headers: [String: String], body: Data, tokenSent: Bool) -> Bool {
        let lowered = Dictionary(headers.map { ($0.key.lowercased(), $0.value) }, uniquingKeysWith: { first, _ in first })
        let contentType = lowered["content-type"]?.lowercased() ?? ""
        if contentType.contains("json") || contentType.contains("ndjson") { return false }
        if lowered.keys.contains(where: { $0.hasPrefix("cf-access-") }) && [401, 403].contains(status) { return true }
        let prefix = String(decoding: body.prefix(64 * 1024), as: UTF8.self).lowercased()
        let mentionsAccess = prefix.contains("cloudflareaccess.com") || prefix.contains("/cdn-cgi/access/")
            || prefix.contains("cloudflare access")
        switch status {
        case 401, 403:
            // Access 的拒绝页由 Cloudflare 边缘直接返回（Server: cloudflare，HTML 或空白正文）。
            if mentionsAccess { return true }
            let fromEdge = lowered["server"]?.lowercased() == "cloudflare"
            let wafPage = prefix.contains("you have been blocked") || prefix.contains("attention required")
            return tokenSent && fromEdge && !wafPage && looksLikeEdgePage(contentType, prefix)
        case 200..<300:
            return contentType.contains("html") && mentionsAccess
        default:
            return false
        }
    }

    private static func looksLikeEdgePage(_ contentType: String, _ body: String) -> Bool {
        contentType.isEmpty || contentType.contains("html") || contentType.contains("text/plain")
            || body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}
