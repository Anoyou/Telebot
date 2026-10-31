import Foundation
import OpenAPIRuntime

// 第 22 轮：连接失败诊断。每次连不上或断线时，用一句大白话告诉用户原因 + 怎么办，
// 而不是笼统的“连接失败”（思路参考 relay-kit 的断线诊断）。
//
// 隐私：这里只保存枚举、数字错误码、HTTP 状态、Cloudflare 错误码与 cf-ray，
// 从不保存错误对象本身、请求头、Cookie、Token 或查询参数（URLError 的 userInfo 可能带完整地址）。

/// 底层网络失败原因（由 URLError 数字码归类）。
public enum NetworkFailureCause: String, Sendable, Equatable, Codable {
    case unknown
    /// 没有网络 / 飞行模式 / 关了蜂窝数据。
    case offline
    /// 域名解析失败（地址写错或 DNS 未生效）。
    case dnsFailed
    /// 连接被拒绝（服务器没在运行或端口不对）。
    case connectionRefused
    case timedOut
    /// HTTPS 证书 / TLS 握手失败。
    case tlsFailed
    /// 连接中途断开（网络切换、服务器重启）。
    case connectionLost

    public static func classify(urlErrorCode code: Int?) -> NetworkFailureCause {
        guard let code else { return .unknown }
        switch code {
        case NSURLErrorNotConnectedToInternet, NSURLErrorDataNotAllowed, NSURLErrorInternationalRoamingOff,
             NSURLErrorCallIsActive:
            return .offline
        case NSURLErrorCannotFindHost, NSURLErrorDNSLookupFailed:
            return .dnsFailed
        case NSURLErrorCannotConnectToHost:
            return .connectionRefused
        case NSURLErrorTimedOut:
            return .timedOut
        case NSURLErrorSecureConnectionFailed, NSURLErrorServerCertificateHasBadDate, NSURLErrorServerCertificateUntrusted,
             NSURLErrorServerCertificateHasUnknownRoot, NSURLErrorServerCertificateNotYetValid,
             NSURLErrorClientCertificateRejected, NSURLErrorClientCertificateRequired,
             NSURLErrorAppTransportSecurityRequiresSecureConnection:
            return .tlsFailed
        case NSURLErrorNetworkConnectionLost, NSURLErrorBadServerResponse, NSURLErrorZeroByteResource,
             NSURLErrorCannotParseResponse, NSURLErrorResourceUnavailable:
            return .connectionLost
        default:
            return .unknown
        }
    }

    /// 在错误链里找 URLError 的数字码（URLError、NSURLErrorDomain、OpenAPI ClientError 与 NSUnderlyingError 逐层展开）。
    public static func urlErrorCode(in error: any Error) -> Int? {
        var current: (any Error)? = error
        for _ in 0..<5 {
            guard let value = current else { return nil }
            if let urlError = value as? URLError { return urlError.code.rawValue }
            if let client = value as? ClientError { current = client.underlyingError; continue }
            let ns = value as NSError
            if ns.domain == NSURLErrorDomain { return ns.code }
            current = ns.userInfo[NSUnderlyingErrorKey] as? any Error
        }
        return nil
    }

    public static func classify(_ error: any Error) -> NetworkFailureCause {
        classify(urlErrorCode: urlErrorCode(in: error))
    }
}

/// 归类后的连接问题。
public enum ConnectionProblem: String, Sendable, Equatable {
    case offline, dnsFailed, connectionRefused, timedOut, tlsFailed, connectionLost
    case cloudflareAccess, cloudflareFirewall, tunnelOffline, originUnreachable
    case gatewayError, serviceUnavailable, serverError, rateLimited
    case unauthorized, serverTooOld, redirect, unknownNetwork
}

/// 一句原因 + 一句建议。`summary` 用作页面上的错误文字。
public struct ConnectionDiagnosis: Sendable, Equatable {
    public let problem: ConnectionProblem
    /// 短标题（≤12 字），例如「没有网络」。
    public let title: String
    /// 原因（一句大白话，以“。”或“，”结尾）。
    public let reason: String
    /// 怎么办。
    public let advice: String
    /// HTTP 状态或 Cloudflare 错误码（可选）。
    public let code: String?

    public init(problem: ConnectionProblem, title: String, reason: String, advice: String, code: String? = nil) {
        self.problem = problem; self.title = title; self.reason = reason; self.advice = advice; self.code = code
    }

    public var summary: String { reason + advice }
}

/// Cloudflare 等前置代理返回的非 TelePilot 错误页识别：合成 `APIError.server` 的 code，界面据此给出原因。
public enum ConnectionEdgeDetector {
    /// FastAPI 默认的“路由不存在”（服务器版本比 App 旧，没有这个接口）。
    public static let routeNotFound = "ROUTE_NOT_FOUND"
    public static let cloudflarePrefix = "CLOUDFLARE_"

    /// - Parameter headers: 响应头，名称不区分大小写。
    public static func syntheticCode(status: Int, headers: [String: String], body: Data) -> String? {
        guard !(200..<400).contains(status) else { return nil }
        let lowered = Dictionary(headers.map { ($0.key.lowercased(), $0.value) }, uniquingKeysWith: { first, _ in first })
        let contentType = lowered["content-type"]?.lowercased() ?? ""
        if contentType.contains("json") {
            guard status == 404 || status == 405,
                  let root = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                  let detail = root["detail"] as? String,
                  detail == "Not Found" || detail == "Method Not Allowed" else { return nil }
            return routeNotFound
        }
        let fromCloudflare = lowered["cf-ray"] != nil || lowered["server"]?.lowercased() == "cloudflare"
        guard fromCloudflare else { return nil }
        let text = String(decoding: body.prefix(32 * 1024), as: UTF8.self).lowercased()
        if let code = cloudflareErrorCode(in: text) { return cloudflarePrefix + String(code) }
        if [502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 530].contains(status) { return cloudflarePrefix + String(status) }
        return nil
    }

    /// 从 Cloudflare 错误页正文里取 1xxx 错误码（“error code: 1033”或“Error 1033”）。
    static func cloudflareErrorCode(in text: String) -> Int? {
        for marker in ["error code:", "error "] {
            var search = text[...]
            while let range = search.range(of: marker) {
                let rest = search[range.upperBound...]
                let digits = rest.drop(while: { $0 == " " }).prefix(while: { $0.isASCII && $0.isNumber })
                if digits.count == 4, let value = Int(digits), (1000...1999).contains(value) { return value }
                search = rest
            }
        }
        return nil
    }

    /// `CLOUDFLARE_1033` → 1033。
    public static func cloudflareCode(_ code: String?) -> Int? {
        guard let code, code.hasPrefix(cloudflarePrefix) else { return nil }
        return Int(code.dropFirst(cloudflarePrefix.count))
    }
}

/// 一次失败请求的诊断记录（可复制给维护者）。不含 Token、Cookie、请求头或查询参数。
public struct ConnectionFailureRecord: Sendable, Equatable {
    /// 服务器根地址（https://host[:port]）。
    public var origin: String
    /// 请求路径（去掉查询参数）。
    public var path: String
    public var method: String
    /// 本次请求实际发出的次数（CSRF 预取与重试也算）。
    public var attempts: Int
    /// 从发出到失败的耗时（秒）。
    public var elapsed: TimeInterval
    public var urlErrorCode: Int?
    public var httpStatus: Int?
    /// 服务器错误码或合成码（`CLOUDFLARE_1033`、`ROUTE_NOT_FOUND`）。
    public var errorCode: String?
    /// Cloudflare 请求编号（cf-ray），便于在 Cloudflare 后台查日志。
    public var cloudflareRay: String?
    /// 响应头 Server（如 cloudflare、uvicorn），截断到 40 个 ASCII 字符。
    public var server: String?
    public var isStream: Bool
    public var at: Date

    public init(origin: String, path: String, method: String, attempts: Int = 1, elapsed: TimeInterval = 0,
                urlErrorCode: Int? = nil, httpStatus: Int? = nil, errorCode: String? = nil,
                cloudflareRay: String? = nil, server: String? = nil, isStream: Bool = false, at: Date = Date()) {
        self.origin = origin
        self.path = Self.sanitizedPath(path)
        self.method = method
        self.attempts = attempts
        self.elapsed = elapsed
        self.urlErrorCode = urlErrorCode
        self.httpStatus = httpStatus
        self.errorCode = errorCode
        self.cloudflareRay = cloudflareRay.map { Self.safeHeader($0) }
        self.server = server.map { Self.safeHeader($0) }
        self.isStream = isStream
        self.at = at
    }

    /// 去掉查询参数与片段，只保留路径。
    public static func sanitizedPath(_ path: String) -> String {
        var value = Substring(path)
        if let index = value.firstIndex(where: { $0 == "?" || $0 == "#" }) { value = value[..<index] }
        return String(value)
    }

    /// 这条记录是否就是这个错误（并发请求时最近一次失败可能是另一个请求），不匹配时诊断详情不带它。
    public func matches(_ error: any Error) -> Bool {
        guard let api = error as? APIError else { return false }
        switch api {
        case .network(_, let cause):
            // 流式读到一半断开时已有 HTTP 状态；其余网络失败没有收到响应。
            if cause == .connectionLost && isStream { return true }
            return httpStatus == nil && NetworkFailureCause.classify(urlErrorCode: urlErrorCode) == cause
        case .server(let status, let code):
            return httpStatus == status && (code == nil || errorCode == code)
        case .unauthorized:
            return httpStatus == 401
        case .cloudflareAccessRequired, .redirectRejected:
            return httpStatus.map { (200..<500).contains($0) } ?? false
        default:
            return false
        }
    }

    static func safeHeader(_ value: String) -> String {
        String(value.filter { $0.isASCII && !$0.isNewline && $0 != ";" }.prefix(40))
    }
}

/// 把错误归类成连接问题。业务错误（验证码错、插件已存在等）返回 nil，页面照常显示原来的文字。
public enum ConnectionDiagnoser {
    public static func diagnose(_ error: any Error) -> ConnectionDiagnosis? {
        if error is CancellationError { return nil }
        if let api = error as? APIError { return diagnose(api: api) }
        guard let code = NetworkFailureCause.urlErrorCode(in: error), code != NSURLErrorCancelled else { return nil }
        return network(NetworkFailureCause.classify(urlErrorCode: code))
    }

    public static func diagnose(api error: APIError) -> ConnectionDiagnosis? {
        switch error {
        case .network(_, let cause):
            return network(cause)
        case .cloudflareAccessRequired:
            return ConnectionDiagnosis(problem: .cloudflareAccess, title: "被 Cloudflare Access 拦截",
                                       reason: "请求被 Cloudflare Access 拦在门外，还没到达 TelePilot。",
                                       advice: "在登录页或「App 设置 › Cloudflare Access」重新用 Google 验证；用 Service Token 的请检查它是否过期。")
        case .unauthorized:
            return unauthorized
        case .redirectRejected:
            return ConnectionDiagnosis(problem: .redirect, title: "服务器要求跳转",
                                       reason: "服务器把请求重定向到了别的地址，为保护登录信息 App 不会跟过去。",
                                       advice: "请直接填写最终的 HTTPS 地址（不要填会跳转的旧域名或 http 地址）。")
        case .server(let status, let code):
            return server(status: status, code: code)
        default:
            return nil
        }
    }

    static let unauthorized = ConnectionDiagnosis(problem: .unauthorized, title: "登录已失效",
                                                  reason: "服务器不再认这次登录（Token 过期，或已在别处退出、改了密码）。",
                                                  advice: "重新登录即可，服务器上的数据不受影响。", code: "401")

    public static func network(_ cause: NetworkFailureCause) -> ConnectionDiagnosis {
        switch cause {
        case .offline:
            return ConnectionDiagnosis(problem: .offline, title: "没有网络",
                                       reason: "手机现在没有联网（可能开了飞行模式，或关了 Wi‑Fi 和蜂窝数据）。",
                                       advice: "打开网络后下拉重试。")
        case .dnsFailed:
            return ConnectionDiagnosis(problem: .dnsFailed, title: "找不到服务器域名",
                                       reason: "找不到这个服务器域名，可能地址写错了，或域名解析还没生效。",
                                       advice: "检查服务器地址的拼写；刚改过域名就等几分钟再试。")
        case .connectionRefused:
            return ConnectionDiagnosis(problem: .connectionRefused, title: "服务器拒绝连接",
                                       reason: "找到了服务器，但它拒绝连接，通常是 TelePilot 没在运行，或地址里的端口不对。",
                                       advice: "确认服务器上的 TelePilot 已启动、端口填写正确，再下拉重试。")
        case .timedOut:
            return ConnectionDiagnosis(problem: .timedOut, title: "连接超时",
                                       reason: "等了很久服务器都没回应，可能网络太慢、服务器卡住，或被防火墙挡住。",
                                       advice: "换个网络试试；还不行请检查服务器负载和防火墙。")
        case .tlsFailed:
            return ConnectionDiagnosis(problem: .tlsFailed, title: "HTTPS 证书有问题",
                                       reason: "服务器的 HTTPS 证书无效（过期、域名不匹配或不被信任），为了安全 App 不会继续连接。",
                                       advice: "检查证书是否有效、是否包含这个域名；手机的系统时间也要正确。")
        case .connectionLost:
            return ConnectionDiagnosis(problem: .connectionLost, title: "连接中途断开",
                                       reason: "连接在传输中途断了，切换网络或服务器重启时常见。",
                                       advice: "稍等片刻后下拉重试。")
        case .unknown:
            // 与旧版文字一致：原因不明时不猜。
            return ConnectionDiagnosis(problem: .unknownNetwork, title: "无法连接服务器",
                                       reason: "无法连接服务器，", advice: "请检查网络、地址和 HTTPS 证书后重试。")
        }
    }

    public static func server(status: Int, code: String?) -> ConnectionDiagnosis? {
        if let cf = ConnectionEdgeDetector.cloudflareCode(code) { return cloudflare(cf) }
        if code == ConnectionEdgeDetector.routeNotFound {
            return ConnectionDiagnosis(problem: .serverTooOld, title: "服务器版本过旧",
                                       reason: "服务器上没有 App 要用的这个接口（HTTP \(status)），通常是服务器版本比 App 旧。",
                                       advice: "在「App 设置 › 软件更新」把服务器更新到最新版本。", code: String(status))
        }
        if status == 401, code == nil || code == "AUTH_EXPIRED" || code == "AUTH_REQUIRED" { return unauthorized }
        // 带 TelePilot 自己错误码的是业务错误（例如 WORKER_OFFLINE），保持原文字。
        guard code == nil else { return nil }
        switch status {
        case 530:
            return cloudflare(530)
        case 502, 504:
            return ConnectionDiagnosis(problem: .gatewayError, title: "网关连不上 TelePilot",
                                       reason: "前面的反向代理在线，但连不上后面的 TelePilot（HTTP \(status)），通常是 TelePilot 没启动、正在重启或卡住。",
                                       advice: "确认 TelePilot 正在运行；刚更新过就等一两分钟再下拉重试。", code: String(status))
        case 503:
            return ConnectionDiagnosis(problem: .serviceUnavailable, title: "服务暂时不可用",
                                       reason: "服务器暂时不可用（HTTP 503），可能正在重启或更新。",
                                       advice: "等一两分钟后下拉重试。", code: "503")
        case 429:
            return ConnectionDiagnosis(problem: .rateLimited, title: "请求太频繁",
                                       reason: "请求过于频繁，服务器暂时拒绝（HTTP 429）。",
                                       advice: "请稍后重试。", code: "429")
        case 500:
            return ConnectionDiagnosis(problem: .serverError, title: "服务器内部出错",
                                       reason: "服务器处理请求时出错（HTTP 500）。",
                                       advice: "到 Web 管理台「日志 › 系统控制台」查看报错，或把诊断详情发给维护者。", code: "500")
        default:
            return nil
        }
    }

    static func cloudflare(_ code: Int) -> ConnectionDiagnosis {
        switch code {
        case 1033, 530, 1016:
            return ConnectionDiagnosis(problem: .tunnelOffline, title: "Cloudflare Tunnel 不在线",
                                       reason: "Cloudflare 找不到你服务器的隧道（错误 \(code)），通常是服务器上的 cloudflared 没在运行。",
                                       advice: "在服务器上启动 cloudflared（例如 systemctl restart cloudflared 或 docker compose up -d），再下拉重试。",
                                       code: String(code))
        case 1006, 1007, 1008, 1009, 1010, 1012, 1015, 1020:
            return ConnectionDiagnosis(problem: .cloudflareFirewall, title: "被 Cloudflare 防火墙拦截",
                                       reason: "Cloudflare 的安全规则拦下了这次请求（错误 \(code)），还没到达 TelePilot。",
                                       advice: "到 Cloudflare 后台「安全 › 事件」查看并放行手机的访问。", code: String(code))
        case 503:
            return ConnectionDiagnosis(problem: .serviceUnavailable, title: "服务暂时不可用",
                                       reason: "Cloudflare 后面的服务暂时不可用（HTTP 503），可能正在重启或更新。",
                                       advice: "等一两分钟后下拉重试。", code: "503")
        default:
            return ConnectionDiagnosis(problem: .originUnreachable, title: "Cloudflare 连不上 TelePilot",
                                       reason: "Cloudflare 在线，但连不上后面的 TelePilot（错误 \(code)），通常是 TelePilot 没启动、正在重启，或隧道指向的端口不对。",
                                       advice: "确认 TelePilot 正在运行，cloudflared 配置里的服务地址（如 http://localhost:端口）正确。",
                                       code: String(code))
        }
    }

    /// URLError 码的中文名（诊断详情用）。
    public static func urlErrorName(_ code: Int) -> String {
        switch code {
        case NSURLErrorNotConnectedToInternet: return "没有网络"
        case NSURLErrorDataNotAllowed: return "不允许使用蜂窝数据"
        case NSURLErrorInternationalRoamingOff: return "国际漫游已关闭"
        case NSURLErrorCannotFindHost: return "找不到主机"
        case NSURLErrorDNSLookupFailed: return "DNS 解析失败"
        case NSURLErrorCannotConnectToHost: return "无法连接主机"
        case NSURLErrorTimedOut: return "超时"
        case NSURLErrorNetworkConnectionLost: return "连接中断"
        case NSURLErrorSecureConnectionFailed: return "TLS 握手失败"
        case NSURLErrorServerCertificateHasBadDate: return "证书已过期"
        case NSURLErrorServerCertificateUntrusted: return "证书不受信任"
        case NSURLErrorServerCertificateHasUnknownRoot: return "证书根未知"
        case NSURLErrorServerCertificateNotYetValid: return "证书尚未生效"
        case NSURLErrorBadServerResponse: return "服务器响应异常"
        case NSURLErrorCancelled: return "已取消"
        default: return "URLError"
        }
    }
}

/// 页面展示用：诊断 + 最近一次失败请求 + 连续失败次数。`detailLines` 与 `copyText` 不含任何凭证。
public struct ConnectionReport: Sendable, Equatable {
    public var diagnosis: ConnectionDiagnosis
    public var record: ConnectionFailureRecord?
    /// 当前服务器地址（没有失败记录时用于显示目标）。
    public var serverURL: String?
    /// 连续失败的读取轮数（成功一次后清零）。
    public var consecutiveFailures: Int
    public var firstFailureAt: Date?
    public var lastSuccessAt: Date?
    public var appVersion: String?

    public init(diagnosis: ConnectionDiagnosis, record: ConnectionFailureRecord? = nil, serverURL: String? = nil,
                consecutiveFailures: Int = 1, firstFailureAt: Date? = nil, lastSuccessAt: Date? = nil, appVersion: String? = nil) {
        self.diagnosis = diagnosis; self.record = record; self.serverURL = serverURL
        self.consecutiveFailures = consecutiveFailures; self.firstFailureAt = firstFailureAt
        self.lastSuccessAt = lastSuccessAt; self.appVersion = appVersion
    }

    public struct Line: Sendable, Equatable, Identifiable {
        public let id: String
        public let label: String
        public let value: String
    }

    public func detailLines(timeZone: TimeZone = .current) -> [Line] {
        var lines = [Line(id: "reason", label: "原因", value: diagnosis.title + (diagnosis.code.map { "（\($0)）" } ?? ""))]
        if let target = record?.origin ?? serverURL { lines.append(Line(id: "target", label: "目标地址", value: target)) }
        if let record {
            lines.append(Line(id: "request", label: "请求", value: "\(record.method) \(record.path)" + (record.isStream ? "（流式）" : "")))
            var attempts = "本次 \(record.attempts) 次"
            if consecutiveFailures > 1 { attempts += " · 连续失败 \(consecutiveFailures) 轮" }
            lines.append(Line(id: "attempts", label: "尝试次数", value: attempts))
            lines.append(Line(id: "elapsed", label: "耗时", value: String(format: "%.1f 秒", record.elapsed)))
            var low: [String] = []
            if let code = record.urlErrorCode { low.append("NSURLError \(code)（\(ConnectionDiagnoser.urlErrorName(code))）") }
            if let status = record.httpStatus { low.append("HTTP \(status)") }
            if let code = record.errorCode { low.append(code) }
            if !low.isEmpty { lines.append(Line(id: "error", label: "底层错误", value: low.joined(separator: " · "))) }
            if let server = record.server, !server.isEmpty { lines.append(Line(id: "server", label: "响应来自", value: server)) }
            if let ray = record.cloudflareRay, !ray.isEmpty { lines.append(Line(id: "ray", label: "Cloudflare 编号", value: ray)) }
            lines.append(Line(id: "at", label: "失败时间", value: Self.format(record.at, timeZone)))
        } else {
            lines.append(Line(id: "attempts", label: "尝试次数", value: "连续失败 \(consecutiveFailures) 轮"))
        }
        if let first = firstFailureAt, consecutiveFailures > 1 {
            lines.append(Line(id: "since", label: "首次失败", value: Self.format(first, timeZone)))
        }
        if let last = lastSuccessAt { lines.append(Line(id: "lastSuccess", label: "上次连上", value: Self.format(last, timeZone))) }
        if let appVersion { lines.append(Line(id: "app", label: "App 版本", value: appVersion)) }
        return lines
    }

    /// 复制给维护者的纯文本。
    public func copyText(timeZone: TimeZone = .current) -> String {
        var text = "TelePilot 连接诊断\n" + diagnosis.summary + "\n"
        for line in detailLines(timeZone: timeZone) { text += "\(line.label)：\(line.value)\n" }
        return text
    }

    static func format(_ date: Date, _ timeZone: TimeZone) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.string(from: date) + " " + (timeZone.abbreviation(for: date) ?? timeZone.identifier)
    }
}

/// 流式进度（Agent 运行）断线后的自动重连状态：「已断开 x 秒，正在第 n 次重连」，重连成功后清除。
public struct StreamReconnectStatus: Sendable, Equatable {
    public let since: Date
    /// 正在进行第几次重连（从 1 开始）。
    public var attempt: Int
    public var diagnosis: ConnectionDiagnosis?

    public init(since: Date = Date(), attempt: Int = 1, diagnosis: ConnectionDiagnosis? = nil) {
        self.since = since; self.attempt = attempt; self.diagnosis = diagnosis
    }

    /// 又一次重连失败：次数 +1，原因换成最新的（断开时间不变）。
    public mutating func failedAgain(_ diagnosis: ConnectionDiagnosis?) {
        attempt += 1
        if let diagnosis { self.diagnosis = diagnosis }
    }

    public func text(now: Date) -> String {
        let seconds = max(0, Int(now.timeIntervalSince(since)))
        return "已断开 \(seconds) 秒，正在第 \(attempt) 次重连"
    }
}
