import Foundation

public struct LoginRequest: Sendable {
    public var username: String
    public var password: String
    public var totpCode: String?
    public var otpToken: String?
    public var otpCode: String?
    public var recoveryCode: String?
    public init(username: String, password: String, totpCode: String? = nil, otpToken: String? = nil,
                otpCode: String? = nil, recoveryCode: String? = nil) {
        self.username = username; self.password = password; self.totpCode = totpCode
        self.otpToken = otpToken; self.otpCode = otpCode; self.recoveryCode = recoveryCode
    }
}

public struct LoginResult: Sendable {
    public let ok: Bool
    public let requireTotp: Bool
    public let requireOtp: Bool
    public let otpToken: String?
    public let otpMessage: String?
    public let otpTTLSeconds: Int?
    public let recoveryAvailable: Bool
    public init(ok: Bool, requireTotp: Bool = false, requireOtp: Bool = false, otpToken: String? = nil,
                otpMessage: String? = nil, otpTTLSeconds: Int? = nil, recoveryAvailable: Bool = false) {
        self.ok = ok; self.requireTotp = requireTotp; self.requireOtp = requireOtp
        self.otpToken = otpToken; self.otpMessage = otpMessage; self.otpTTLSeconds = otpTTLSeconds
        self.recoveryAvailable = recoveryAvailable
    }
}

public struct CurrentUser: Sendable {
    public let id: Int
    public let username: String
    public let hasTotp: Bool
    public init(id: Int, username: String, hasTotp: Bool) {
        self.id = id; self.username = username; self.hasTotp = hasTotp
    }
}

public struct Account: Identifiable, Sendable {
    public let id: Int
    public let phone: String
    public let displayName: String?
    public let tgUsername: String?
    public let status: String
    public let tags: [String]
    public let enabledFeatures: Int
    public let notes: String?
    public let createdAt: String?
    public let proxyLabel: String?
    public init(id: Int, phone: String, displayName: String? = nil, tgUsername: String? = nil,
                status: String, tags: [String] = [], enabledFeatures: Int = 0,
                notes: String? = nil, createdAt: String? = nil, proxyLabel: String? = nil) {
        self.id = id; self.phone = phone; self.displayName = displayName; self.tgUsername = tgUsername
        self.status = status; self.tags = tags; self.enabledFeatures = enabledFeatures
        self.notes = notes; self.createdAt = createdAt; self.proxyLabel = proxyLabel
    }
}

public struct ProbeStatus: Sendable {
    public let ok: Bool
    public let error: String?
    public init(ok: Bool, error: String? = nil) { self.ok = ok; self.error = error }
}

public struct WorkersStatus: Sendable {
    public let total: Int
    public let byStatus: [String: Int]
    public let runtimeAlive: Int
    public let runtimeDesiredRunning: Int
    public let runtimeDesiredRunningAlive: Int
    public let runtimeFailing: Int
    public init(total: Int, byStatus: [String: Int] = [:], runtimeAlive: Int = 0,
                runtimeDesiredRunning: Int = 0, runtimeDesiredRunningAlive: Int = 0, runtimeFailing: Int = 0) {
        self.total = total; self.byStatus = byStatus; self.runtimeAlive = runtimeAlive
        self.runtimeDesiredRunning = runtimeDesiredRunning
        self.runtimeDesiredRunningAlive = runtimeDesiredRunningAlive; self.runtimeFailing = runtimeFailing
    }
}

public struct HealthOverview: Sendable {
    public let db: ProbeStatus
    public let redis: ProbeStatus
    public let alembic: ProbeStatus
    public let workers: WorkersStatus
    public init(db: ProbeStatus, redis: ProbeStatus, alembic: ProbeStatus, workers: WorkersStatus) {
        self.db = db; self.redis = redis; self.alembic = alembic; self.workers = workers
    }
}

public struct HostResource: Sendable {
    public let cpuPercent: Double?
    public let memoryUsedPercent: Double?
    public let diskUsedPercent: Double?
    public let uptimeSeconds: Int?
    public init(cpuPercent: Double? = nil, memoryUsedPercent: Double? = nil,
                diskUsedPercent: Double? = nil, uptimeSeconds: Int? = nil) {
        self.cpuPercent = cpuPercent; self.memoryUsedPercent = memoryUsedPercent
        self.diskUsedPercent = diskUsedPercent; self.uptimeSeconds = uptimeSeconds
    }
}

public struct WorkerResource: Sendable {
    public let accountID: Int
    public let alive: Bool
    public let desired: String
    public let failCount: Int
    public init(accountID: Int, alive: Bool, desired: String, failCount: Int) {
        self.accountID = accountID; self.alive = alive; self.desired = desired; self.failCount = failCount
    }
}

public struct ResourceDashboard: Sendable {
    public let host: HostResource
    public let workers: [WorkerResource]
    public init(host: HostResource, workers: [WorkerResource] = []) { self.host = host; self.workers = workers }
}

public struct ServerVersion: Sendable {
    public let version: String
    public let channel: String
    public let revision: String?
    public init(version: String, channel: String, revision: String? = nil) {
        self.version = version; self.channel = channel; self.revision = revision
    }
}

public enum APIError: Error, LocalizedError, Sendable {
    case invalidServerURL
    case unauthorized
    case server(status: Int, code: String?)
    case incompatibleResponse
    case network(outcomeUncertain: Bool)
    case mutationOutcomeUnknown(incompatibleResponse: Bool)
    case redirectRejected
    case secureStorage
    case sessionChanged
    /// 请求被 Cloudflare Access 拦截（重定向到 `*.cloudflareaccess.com`／`/cdn-cgi/access/`，或 Access 的 401／403 页面）。
    /// 请求没有到达 TelePilot，不清除登录会话，写操作也确定未执行。
    case cloudflareAccessRequired(tokenConfigured: Bool)
    /// Service Token 输入不完整或包含不能放进 HTTP 头的字符。
    case cloudflareAccessTokenInvalid

    public var isUnauthorized: Bool {
        switch self {
        case .unauthorized, .server(status: 401, code: _): true
        default: false
        }
    }
    /// 需要引导用户去填写／检查 Cloudflare Access Service Token。
    public var isCloudflareAccess: Bool {
        if case .cloudflareAccessRequired = self { return true }
        return false
    }
    public var isIncompatibleResponse: Bool {
        switch self {
        case .incompatibleResponse, .mutationOutcomeUnknown(incompatibleResponse: true): true
        default: false
        }
    }
    public var isOutcomeUncertain: Bool {
        switch self {
        case .network(let uncertain): uncertain
        case .mutationOutcomeUnknown: true
        default: false
        }
    }
    public var errorDescription: String? {
        switch self {
        case .invalidServerURL: "请输入 HTTPS 服务器根地址，不要包含账号、路径、查询参数或片段。"
        case .unauthorized: "登录已失效或验证信息不正确，请重新登录。"
        case .incompatibleResponse: "服务器返回的数据与此 App 不兼容，请检查服务器与 App 更新。"
        case .network(let uncertain): uncertain
            ? "连接中断，操作结果尚未确认。请刷新账号状态，不要立即重复操作。"
            : "无法连接服务器，请检查网络、地址和 HTTPS 证书后重试。"
        case .mutationOutcomeUnknown: "未能确认服务器操作结果。请刷新账号状态或打开 Web 管理台核实，不要立即重复操作。"
        case .redirectRejected: "服务器返回了重定向。为保护登录信息，请直接填写最终 HTTPS 地址。"
        case .secureStorage: "无法访问系统钥匙串，请解锁设备后重试。"
        case .sessionChanged: "登录状态已改变，请重新打开此页面。"
        case .cloudflareAccessRequired(let configured): configured
            ? "Cloudflare Access 拒绝了已填写的 Service Token。请检查 Client ID 与 Client Secret 是否正确、Token 是否过期，并确认该 Access 应用里有一条 Action 为 Service Auth 且包含此 Token 的策略。"
            : "这台服务器启用了 Cloudflare Access，请填写 Service Token。"
        case .cloudflareAccessTokenInvalid: "请同时填写 Cloudflare Access 的 Client ID 和 Client Secret（不能包含空格或换行）。"
        case .server(let status, let code):
            switch code {
            case "NO_USER": "服务器尚未创建管理员，请先在 Web 管理台完成初始化。"
            case "CSRF_TOKEN_REQUIRED", "CSRF_HEADER_REQUIRED": "安全令牌校验失败，请重新登录。"
            case "OTP_INVALID", "TOTP_INVALID": "验证码错误或已过期，请重新输入。"
            case "RECOVERY_CODE_INVALID": "一次性恢复码无效或已过期。"
            case "AUTH_INVALID": "用户名或密码错误。"
            case "AUTH_EXPIRED", "AUTH_REQUIRED": "登录已失效，请重新登录。"
            case "ACCOUNT_SESSION_DECRYPT_FAILED": "账号登录凭据无法解密，请在 Web 管理台恢复原密钥或重新登录该 Telegram 账号。"
            case "SESSION_BUSY": "会话仍有执行中、等待确认或排队的任务，请先停止或处理任务后再删除。"
            case "PLUGIN_EXISTS": "服务器上已安装同名插件（可能上一次安装请求其实已经成功）。请到插件中心确认；如需覆盖安装，请打开「替换已有插件」后再提交。"
            case "DIR_EXISTS": "服务器插件目录里已有同名目录但没有安装记录，请在 Web 管理台清理后重试。"
            case "KEY_CONFLICTS_BUILTIN", "BUILTIN_PLUGIN_CONFLICT": "该插件名与服务器内置插件冲突，不能由插件仓库安装或覆盖。"
            case "PLUGIN_CAPABILITY_FORCED_OFF": "插件需要的平台能力模块已被关闭，请先在系统设置的能力开关中开启后再启用插件。"
            default: status == 429 ? "请求过于频繁，请稍后重试。" : "服务器拒绝了请求（HTTP \(status)），请在 Web 管理台检查详情。"
            }
        }
    }
}
