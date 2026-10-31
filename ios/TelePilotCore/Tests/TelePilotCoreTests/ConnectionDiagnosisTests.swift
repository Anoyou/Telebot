import Foundation
import Testing
@testable import TelePilotCore

/// 第 22 轮：连接失败诊断（分类器、Cloudflare / 框架错误页识别、诊断详情不泄露凭证、重连文案）。
@Suite struct ConnectionDiagnosisTests {
    @Test func urlErrorCodesMapToPlainReasons() {
        let cases: [(URLError.Code, NetworkFailureCause, ConnectionProblem)] = [
            (.notConnectedToInternet, .offline, .offline),
            (.dataNotAllowed, .offline, .offline),
            (.cannotFindHost, .dnsFailed, .dnsFailed),
            (.dnsLookupFailed, .dnsFailed, .dnsFailed),
            (.cannotConnectToHost, .connectionRefused, .connectionRefused),
            (.timedOut, .timedOut, .timedOut),
            (.secureConnectionFailed, .tlsFailed, .tlsFailed),
            (.serverCertificateUntrusted, .tlsFailed, .tlsFailed),
            (.serverCertificateHasBadDate, .tlsFailed, .tlsFailed),
            (.networkConnectionLost, .connectionLost, .connectionLost),
        ]
        for (code, cause, problem) in cases {
            #expect(NetworkFailureCause.classify(URLError(code)) == cause, "\(code)")
            let diagnosis = ConnectionDiagnoser.diagnose(APIError.network(outcomeUncertain: false, cause: cause))
            #expect(diagnosis?.problem == problem)
            #expect(diagnosis?.summary.isEmpty == false)
        }
        #expect(NetworkFailureCause.classify(URLError(.unknown)) == .unknown)
        // 原始 URLError（不经 APIError）也能归类；取消不算连接问题。
        #expect(ConnectionDiagnoser.diagnose(URLError(.cannotFindHost))?.problem == .dnsFailed)
        #expect(ConnectionDiagnoser.diagnose(URLError(.cancelled)) == nil)
        #expect(ConnectionDiagnoser.diagnose(CancellationError()) == nil)
    }

    @Test func nestedUnderlyingErrorIsFound() {
        let inner = NSError(domain: NSURLErrorDomain, code: NSURLErrorCannotConnectToHost)
        let outer = NSError(domain: "wrapper", code: 1, userInfo: [NSUnderlyingErrorKey: inner])
        #expect(NetworkFailureCause.urlErrorCode(in: outer) == NSURLErrorCannotConnectToHost)
        #expect(NetworkFailureCause.classify(outer) == .connectionRefused)
    }

    @Test func networkMessagesExplainCauseAndKeepLegacyTextWhenUnknown() {
        #expect(APIError.network(outcomeUncertain: false).errorDescription == "无法连接服务器，请检查网络、地址和 HTTPS 证书后重试。")
        #expect(APIError.network(outcomeUncertain: true).errorDescription == "连接中断，操作结果尚未确认。请刷新账号状态，不要立即重复操作。")
        let offline = APIError.network(outcomeUncertain: false, cause: .offline).errorDescription ?? ""
        #expect(offline.contains("飞行模式"))
        let dns = APIError.network(outcomeUncertain: false, cause: .dnsFailed).errorDescription ?? ""
        #expect(dns.contains("域名") && dns.contains("拼写"))
        let refused = APIError.network(outcomeUncertain: false, cause: .connectionRefused).errorDescription ?? ""
        #expect(refused.contains("没在运行"))
        let tls = APIError.network(outcomeUncertain: false, cause: .tlsFailed).errorDescription ?? ""
        #expect(tls.contains("证书"))
        // 写操作结果未知时仍提醒不要重复。
        let uncertain = APIError.network(outcomeUncertain: true, cause: .timedOut)
        #expect(uncertain.isOutcomeUncertain)
        #expect(uncertain.errorDescription?.contains("不要立即重复") == true)
    }

    @Test func cloudflareTunnelAndGatewayStatusesAreExplained() {
        // 1033 / 530：cloudflared 没在运行。
        let tunnel = ConnectionDiagnoser.diagnose(APIError.server(status: 530, code: "CLOUDFLARE_1033"))
        #expect(tunnel?.problem == .tunnelOffline)
        #expect(tunnel?.summary.contains("cloudflared") == true)
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 530, code: nil))?.problem == .tunnelOffline)
        #expect(APIError.server(status: 530, code: "CLOUDFLARE_1033").errorDescription?.contains("cloudflared") == true)
        // Cloudflare 在线但连不上 TelePilot。
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 502, code: "CLOUDFLARE_502"))?.problem == .originUnreachable)
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 403, code: "CLOUDFLARE_1020"))?.problem == .cloudflareFirewall)
        // 普通反向代理。
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 502, code: nil))?.problem == .gatewayError)
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 504, code: nil))?.problem == .gatewayError)
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 503, code: nil))?.problem == .serviceUnavailable)
        #expect(APIError.server(status: 502, code: nil).errorDescription?.contains("HTTP 502") == true)
    }

    @Test func authOldServerAndBusinessErrors() {
        #expect(ConnectionDiagnoser.diagnose(APIError.unauthorized)?.problem == .unauthorized)
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 401, code: nil))?.problem == .unauthorized)
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 401, code: "AUTH_EXPIRED"))?.problem == .unauthorized)
        // 密码错、验证码错是业务错误，不当成连接问题，文字不变。
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 401, code: "AUTH_INVALID")) == nil)
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 400, code: "OTP_INVALID")) == nil)
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 503, code: "WORKER_OFFLINE")) == nil)
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 409, code: "PLUGIN_EXISTS")) == nil)
        // 接口不存在：服务器版本过旧。
        let old = ConnectionDiagnoser.diagnose(APIError.server(status: 404, code: ConnectionEdgeDetector.routeNotFound))
        #expect(old?.problem == .serverTooOld)
        #expect(APIError.server(status: 404, code: ConnectionEdgeDetector.routeNotFound).errorDescription?.contains("软件更新") == true)
        // 带业务码 / 无码的普通 404 保持原文字。
        #expect(ConnectionDiagnoser.diagnose(APIError.server(status: 404, code: nil)) == nil)
        #expect(APIError.server(status: 404, code: nil).errorDescription == "服务器拒绝了请求（HTTP 404），请在 Web 管理台检查详情。")
        #expect(ConnectionDiagnoser.diagnose(APIError.cloudflareAccessRequired(.none))?.problem == .cloudflareAccess)
        #expect(ConnectionDiagnoser.diagnose(APIError.redirectRejected)?.problem == .redirect)
        #expect(ConnectionDiagnoser.diagnose(APIError.incompatibleResponse) == nil)
        #expect(ConnectionDiagnoser.diagnose(APIError.secureStorage) == nil)
    }

    @Test func edgeDetectorRecognisesCloudflarePagesAndFastAPIDefault404() {
        let cf = ["CF-RAY": "8d2f-SJC", "Server": "cloudflare", "Content-Type": "text/plain; charset=UTF-8"]
        #expect(ConnectionEdgeDetector.syntheticCode(status: 530, headers: cf, body: Data("error code: 1033".utf8)) == "CLOUDFLARE_1033")
        let html = "<html><title>Cloudflare Tunnel error | example.com | Cloudflare</title><h1>Error 1033</h1></html>"
        #expect(ConnectionEdgeDetector.syntheticCode(status: 530, headers: ["cf-ray": "x", "content-type": "text/html"], body: Data(html.utf8)) == "CLOUDFLARE_1033")
        #expect(ConnectionEdgeDetector.syntheticCode(status: 502, headers: cf, body: Data("Bad gateway".utf8)) == "CLOUDFLARE_502")
        #expect(ConnectionEdgeDetector.syntheticCode(status: 530, headers: cf, body: Data()) == "CLOUDFLARE_530")
        // 不是 Cloudflare 的 502：不合成。
        #expect(ConnectionEdgeDetector.syntheticCode(status: 502, headers: ["server": "nginx"], body: Data("Bad Gateway".utf8)) == nil)
        // TelePilot 自己的 JSON 错误不合成；FastAPI 默认 404 / 405 合成 ROUTE_NOT_FOUND。
        let json = ["content-type": "application/json"]
        #expect(ConnectionEdgeDetector.syntheticCode(status: 404, headers: json, body: Data(#"{"detail":"Not Found"}"#.utf8)) == "ROUTE_NOT_FOUND")
        #expect(ConnectionEdgeDetector.syntheticCode(status: 405, headers: json, body: Data(#"{"detail":"Method Not Allowed"}"#.utf8)) == "ROUTE_NOT_FOUND")
        #expect(ConnectionEdgeDetector.syntheticCode(status: 404, headers: json, body: Data(#"{"detail":"账号不存在"}"#.utf8)) == nil)
        #expect(ConnectionEdgeDetector.syntheticCode(status: 200, headers: cf, body: Data("error code: 1033".utf8)) == nil)
        #expect(ConnectionEdgeDetector.cloudflareErrorCode(in: "error code: 20") == nil)
    }

    @Test func reportDetailsNeverContainSecretsOrQuery() {
        let record = ConnectionFailureRecord(origin: "https://tp.example", path: "/api/system/version?token=SECRET#frag", method: "GET",
                                             attempts: 2, elapsed: 20.04, urlErrorCode: NSURLErrorTimedOut,
                                             cloudflareRay: "abc\n;Set-Cookie: x", server: "cloudflare")
        #expect(record.path == "/api/system/version")
        let report = ConnectionReport(diagnosis: ConnectionDiagnoser.network(.timedOut), record: record,
                                      serverURL: "https://tp.example", consecutiveFailures: 3,
                                      firstFailureAt: Date(timeIntervalSince1970: 0), appVersion: "0.32.0（9）")
        let text = report.copyText(timeZone: TimeZone(identifier: "Asia/Shanghai")!)
        #expect(!text.contains("SECRET"))
        #expect(!text.contains("frag"))
        #expect(!text.contains(";"))
        #expect(text.contains("https://tp.example"))
        #expect(text.contains("GET /api/system/version"))
        #expect(text.contains("本次 2 次 · 连续失败 3 轮"))
        #expect(text.contains("20.0 秒"))
        #expect(text.contains("NSURLError -1001（超时）"))
        #expect(text.contains("0.32.0（9）"))
        let ids = report.detailLines().map(\.id)
        #expect(ids.starts(with: ["reason", "target", "request", "attempts", "elapsed", "error"]))
        // 没有失败记录时仍给出目标地址与连续失败轮数。
        let bare = ConnectionReport(diagnosis: ConnectionDiagnoser.network(.offline), serverURL: "https://tp.example", consecutiveFailures: 2)
        #expect(bare.detailLines().map(\.id).contains("target"))
        #expect(bare.copyText().contains("连续失败 2 轮"))
    }

    @Test func recordMatchesOnlyItsOwnError() {
        let timeout = ConnectionFailureRecord(origin: "https://a", path: "/api/x", method: "GET", urlErrorCode: NSURLErrorTimedOut)
        #expect(timeout.matches(APIError.network(outcomeUncertain: false, cause: .timedOut)))
        #expect(!timeout.matches(APIError.network(outcomeUncertain: false, cause: .dnsFailed)))
        let tunnel = ConnectionFailureRecord(origin: "https://a", path: "/api/x", method: "GET", httpStatus: 530, errorCode: "CLOUDFLARE_1033")
        #expect(tunnel.matches(APIError.server(status: 530, code: "CLOUDFLARE_1033")))
        #expect(!tunnel.matches(APIError.server(status: 502, code: nil)))
        let stream = ConnectionFailureRecord(origin: "https://a", path: "/api/s", method: "GET", httpStatus: 200, isStream: true)
        #expect(stream.matches(APIError.network(outcomeUncertain: false, cause: .connectionLost)))
    }

    @Test func reconnectStatusCountsSecondsAndAttempts() {
        let start = Date(timeIntervalSince1970: 1_000)
        var status = StreamReconnectStatus(since: start, diagnosis: ConnectionDiagnoser.network(.connectionLost))
        #expect(status.text(now: start.addingTimeInterval(3.6)) == "已断开 3 秒，正在第 1 次重连")
        status.failedAgain(ConnectionDiagnoser.network(.offline))
        #expect(status.attempt == 2)
        #expect(status.diagnosis?.problem == .offline)
        status.failedAgain(nil)
        #expect(status.diagnosis?.problem == .offline)
        #expect(status.text(now: start.addingTimeInterval(12)) == "已断开 12 秒，正在第 3 次重连")
        #expect(status.text(now: start.addingTimeInterval(-5)) == "已断开 0 秒，正在第 3 次重连")
    }
}

/// 经过真实 transport（URLProtocol 模拟）的诊断：原因、诊断记录与不泄露凭证。
extension ClientTests {
    @Test func transportKeepsUrlErrorCauseAndRecordsFailure() async throws {
        let (client, _, _) = try setup([MockReply(error: .cannotFindHost)])
        do { _ = try await client.version(); Issue.record("Expected failure") }
        catch let error as APIError {
            #expect(ConnectionDiagnoser.diagnose(error)?.problem == .dnsFailed)
            #expect(error.errorDescription?.contains("域名") == true)
        }
        let record = try #require(await client.lastConnectionFailure())
        #expect(record.origin == origin)
        #expect(record.path == "/api/system/version")
        #expect(record.method == "GET")
        #expect(record.attempts == 1)
        #expect(record.urlErrorCode == NSURLErrorCannotFindHost)
        #expect(record.httpStatus == nil)
    }

    @Test func transportRecognisesCloudflareTunnelErrorPage() async throws {
        let store = MemoryStore()
        try store.save(StoredSession(token: "session-secret", expiresAt: Date().addingTimeInterval(3600)), origin: origin)
        let reply = MockReply(status: 530, headers: ["Content-Type": "text/plain; charset=UTF-8", "CF-RAY": "8d2f00000000abcd-SJC", "Server": "cloudflare"],
                              body: "error code: 1033")
        let (client, _, _) = try setup([reply], store: store)
        do { _ = try await client.request(path: "/api/system/health/overview?secret=1"); Issue.record("Expected failure") }
        catch let error as APIError {
            guard case .server(530, let code) = error else { Issue.record("Unexpected \(error)"); return }
            #expect(code == "CLOUDFLARE_1033")
            #expect(ConnectionDiagnoser.diagnose(error)?.problem == .tunnelOffline)
        }
        let record = try #require(await client.lastConnectionFailure())
        #expect(record.httpStatus == 530)
        #expect(record.errorCode == "CLOUDFLARE_1033")
        #expect(record.cloudflareRay == "8d2f00000000abcd-SJC")
        #expect(record.server == "cloudflare")
        #expect(record.path == "/api/system/health/overview")
        let diagnosis = try #require(ConnectionDiagnoser.diagnose(APIError.server(status: 530, code: record.errorCode)))
        let report = ConnectionReport(diagnosis: diagnosis, record: record)
        #expect(!report.copyText().contains("session-secret"))
        #expect(!report.copyText().contains("secret=1"))
        #expect(await client.lastConnectedAt() != nil)
    }

    @Test func transportMapsFastAPIDefault404ToOldServer() async throws {
        let (client, _, _) = try setup([MockReply(status: 404, body: #"{"detail":"Not Found"}"#)])
        do { _ = try await client.request(path: "/api/new-feature"); Issue.record("Expected failure") }
        catch let error as APIError {
            guard case .server(404, let code) = error else { Issue.record("Unexpected \(error)"); return }
            #expect(code == ConnectionEdgeDetector.routeNotFound)
            #expect(ConnectionDiagnoser.diagnose(error)?.problem == .serverTooOld)
        }
    }

    @Test func streamEndingWithoutDoneIsConnectionLost() async throws {
        let store = MemoryStore()
        try store.save(StoredSession(token: "session-one", expiresAt: Date().addingTimeInterval(3600)), origin: origin)
        let reply = MockReply(headers: ["Content-Type": "application/x-ndjson"], body: "{\"type\":\"run_started\",\"seq\":1}\n")
        let (client, _, _) = try setup([reply], store: store)
        do {
            try await client.stream(path: "/api/system-agent/runs/r1/stream?after_seq=0") { _ in }
            Issue.record("Expected failure")
        } catch let error as APIError {
            guard case .network(false, let cause) = error else { Issue.record("Unexpected \(error)"); return }
            #expect(cause == .connectionLost)
        }
        let record = try #require(await client.lastConnectionFailure())
        #expect(record.isStream)
        #expect(record.path == "/api/system-agent/runs/r1/stream")
    }
}
