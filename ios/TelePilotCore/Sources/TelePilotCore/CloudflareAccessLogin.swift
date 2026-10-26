import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// App 内用 Google 完成 Cloudflare Access 登录，照搬 cloudflared `access login` 的原生客户端流程
/// （cloudflare/cloudflared：token/token.go、token/transfer.go、token/encrypt.go）：
///
/// 1. 向服务器发 `HEAD /`（请求头 `cf-access-metadata-request: true`，不跟随重定向），
///    从响应头 `cf-access-metadata`（JWT）读出 Access 应用的 `aud`；没有该头时退回读取 302 Location 里的 `kid`。
/// 2. 生成一次性 X25519 密钥对，用系统登录窗（ASWebAuthenticationSession）打开
///    `https://<服务器>/cdn-cgi/access/cli?aud=…&token=<公钥>&redirect_url=…&send_org_token=true&edge_token_transfer=true`，
///    用户在系统浏览器里完成 Google 登录（Google 不允许在 App 内嵌网页登录）。
/// 3. 同时长轮询 `https://login.cloudflareaccess.org/transfer/<公钥>`；登录完成后得到用本机公钥加密（NaCl box）的
///    `{"app_token": …, "org_token": …}`，解密取 `app_token`（JWT），之后所有请求带 `cf-access-token` 头。
///
/// 私钥只在内存里；转交服务只认这把公钥，所以别人无法取走令牌。`org_token` 不保存。
public struct CloudflareAccessLoginRequest: Sendable {
    /// 交给系统登录窗打开的地址（在用户自己的服务器域名下）。
    public let authorizationURL: URL
    let transferURL: URL
    #if canImport(CryptoKit)
    let keyPair: NaClKeyPair
    #endif
}

enum CloudflareAccessLogin {
    static let transferBase = "https://login.cloudflareaccess.org/"
    static let metadataRequestHeader = "cf-access-metadata-request"
    static let metadataResponseHeader = "cf-access-metadata"

    // MARK: 纯函数（便于单元测试）

    /// Go `url.QueryEscape`：只保留非保留字符，空格转 `+`。
    static func queryEscape(_ value: String) -> String {
        var out = ""
        for byte in value.utf8 {
            switch byte {
            case 0x30...0x39, 0x41...0x5A, 0x61...0x7A, 0x2D, 0x2E, 0x5F, 0x7E: out.append(Character(Unicode.Scalar(byte)))
            case 0x20: out.append("+")
            default: out += String(format: "%%%02X", byte)
            }
        }
        return out
    }

    /// Go `url.Values.Encode`：按键排序。
    static func encode(_ values: [String: String]) -> String {
        values.keys.sorted().map { queryEscape($0) + "=" + queryEscape(values[$0]!) }.joined(separator: "&")
    }

    /// 与 cloudflared `buildRequestURL(…, cli: true, useHostOnly: true)` 相同的登录地址。
    static func authorizationURL(origin: URL, audience: String, publicKey: String) -> URL? {
        guard let host = origin.host() else { return nil }
        let base = "https://" + host + (origin.port.map { ":\($0)" } ?? "")
        let inner = ["aud": audience, "token": publicKey]
        let redirect = base + "?" + encode(inner)
        let query = encode(inner.merging(["redirect_url": redirect, "send_org_token": "true",
                                          "edge_token_transfer": "true"]) { $1 })
        return URL(string: base + "/cdn-cgi/access/cli?" + query)
    }

    static func transferURL(publicKey: String) -> URL? {
        URL(string: transferBase + "transfer/" + publicKey)
    }

    /// 读取 `cf-access-metadata` JWT 的 `aud`（不验签：只用来拼出本服务器自己的登录地址；令牌本身由
    /// 转交服务按本机公钥加密交付）。同时核对 `hostname` 与服务器一致，避免拿到别的应用的 aud。
    static func audience(fromMetadata jwt: String, origin: URL) -> String? {
        let parts = jwt.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3, let data = CloudflareAccessSession.decodeBase64URL(String(parts[1])),
              let claims = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if let hostname = claims["hostname"] as? String, hostname.lowercased() != origin.host()?.lowercased() { return nil }
        if let type = claims["type"] as? String, type != "match" { return nil }
        let aud: String?
        if let value = claims["aud"] as? String { aud = value }
        else { aud = (claims["aud"] as? [Any])?.first as? String }
        return aud.flatMap(validAudience)
    }

    /// 302 Location（`https://<team>.cloudflareaccess.com/cdn-cgi/access/login/<host>?kid=<aud>&…`）里的 `kid`。
    static func audience(fromRedirect location: String?, origin: URL) -> String? {
        guard let location, CloudflareAccessDetector.isAccessRedirect(location: location, origin: origin),
              let url = URL(string: location, relativeTo: origin),
              let items = URLComponents(url: url, resolvingAgainstBaseURL: true)?.queryItems else { return nil }
        return items.first { $0.name == "kid" }?.value.flatMap(validAudience)
    }

    private static func validAudience(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed.count <= 256,
              trimmed.unicodeScalars.allSatisfy({ CloudflareAccessSession.isJWTScalar($0) }) else { return nil }
        return trimmed
    }

    /// 转交服务的 JSON（已解密）。
    static func session(fromTransferPayload data: Data) throws -> CloudflareAccessSession {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let token = object["app_token"] as? String, !token.isEmpty else {
            throw APIError.cloudflareAccessLogin(.failed)
        }
        return try CloudflareAccessSession(token: token)
    }

    // MARK: 网络

    /// 发现 Access 应用的 aud。`session` 必须拒绝重定向且不带任何凭证。
    static func discoverAudience(origin: URL, session: URLSession) async throws -> String {
        guard let root = URL(string: origin.absoluteString + "/") else { throw APIError.invalidServerURL }
        var request = URLRequest(url: root)
        request.httpMethod = "HEAD"
        request.timeoutInterval = 15
        request.setValue("true", forHTTPHeaderField: metadataRequestHeader)
        let response: URLResponse
        do { (_, response) = try await session.data(for: request) }
        catch {
            if Task.isCancelled { throw CancellationError() }
            throw APIError.network(outcomeUncertain: false)
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.incompatibleResponse }
        if let jwt = http.value(forHTTPHeaderField: metadataResponseHeader),
           let aud = audience(fromMetadata: jwt, origin: origin) { return aud }
        if (300..<400).contains(http.statusCode),
           let aud = audience(fromRedirect: http.value(forHTTPHeaderField: "Location"), origin: origin) { return aud }
        throw APIError.cloudflareAccessLogin(.notProtected)
    }

    #if canImport(CryptoKit)
    static func prepare(origin: URL, session: URLSession) async throws -> CloudflareAccessLoginRequest {
        let audience = try await discoverAudience(origin: origin, session: session)
        let keyPair = NaClKeyPair()
        guard let authorization = authorizationURL(origin: origin, audience: audience, publicKey: keyPair.publicKeyBase64URL),
              let transfer = transferURL(publicKey: keyPair.publicKeyBase64URL) else {
            throw APIError.cloudflareAccessLogin(.failed)
        }
        return CloudflareAccessLoginRequest(authorizationURL: authorization, transferURL: transfer, keyPair: keyPair)
    }

    /// 长轮询转交服务直到拿到令牌。只发只读 GET，不涉及 TelePilot 写操作；被取消时立即停止。
    static func awaitSession(_ request: CloudflareAccessLoginRequest, session: URLSession,
                             deadline: Date = Date().addingTimeInterval(10 * 60)) async throws -> CloudflareAccessSession {
        while Date() < deadline {
            try Task.checkCancellation()
            var poll = URLRequest(url: request.transferURL)
            poll.timeoutInterval = 70
            poll.setValue("TelePilot-iOS", forHTTPHeaderField: "User-Agent")
            let data: Data
            let http: HTTPURLResponse
            do {
                let (body, response) = try await session.data(for: poll)
                guard let value = response as? HTTPURLResponse else { throw APIError.cloudflareAccessLogin(.failed) }
                data = body
                http = value
            } catch let error as URLError where error.code == .timedOut {
                continue // 长轮询超时：用户还在登录，继续等。
            } catch let error as APIError {
                throw error
            } catch {
                if Task.isCancelled { throw CancellationError() }
                throw APIError.network(outcomeUncertain: false)
            }
            if http.statusCode >= 500 { throw APIError.cloudflareAccessLogin(.failed) }
            guard http.statusCode == 200, !data.isEmpty else {
                try await Task.sleep(nanoseconds: 1_000_000_000)
                continue
            }
            return try decrypt(data, senderPublicKey: http.value(forHTTPHeaderField: "service-public-key"), keyPair: request.keyPair)
        }
        throw APIError.cloudflareAccessLogin(.timedOut)
    }

    /// 响应体是 base64（标准字母表）编码的 NaCl box，发送方公钥在 `service-public-key` 响应头。
    static func decrypt(_ body: Data, senderPublicKey: String?, keyPair: NaClKeyPair) throws -> CloudflareAccessSession {
        guard let senderPublicKey,
              let text = String(data: body, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
              let boxed = Data(base64Encoded: text),
              let plain = keyPair.open(boxed, senderPublicKey: senderPublicKey) else {
            throw APIError.cloudflareAccessLogin(.failed)
        }
        return try session(fromTransferPayload: plain)
    }
    #endif
}
