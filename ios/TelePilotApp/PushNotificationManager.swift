import Foundation
import Observation
import UIKit
import UserNotifications
import TelePilotCore
import CryptoKit

@MainActor @Observable
final class PushNotificationManager: NSObject, UNUserNotificationCenterDelegate {
    private(set) var authorization: UNAuthorizationStatus = .notDetermined
    private(set) var registrationError: String?
    private(set) var isRegistering = false
    private(set) var isEnabled = false
    private(set) var configuration: JSONValue?
    private(set) var configurationError: String?
    private(set) var statusMessage: String?
    /// 服务器上当前安装的注册记录（来自 GET /api/apns/devices），nil 表示服务器没有这台设备。
    private(set) var serverDevice: JSONValue?
    /// 设备列表读取失败时为 true；此时“设备已注册”状态未知，不能显示成未注册。
    private(set) var deviceLookupFailed = false
    private(set) var hasLoadedDevices = false
    private(set) var categories: Set<String> = Set(PushNotificationManager.allCategories)
    private(set) var isTesting = false
    private(set) var testResult: PushTestResult?
    @ObservationIgnored private weak var model: AppModel?
    @ObservationIgnored private var deviceToken: Data?
    var onRoute: (@MainActor (AppRoute) -> Void)?
    var canRoute: (@MainActor () -> Bool)?
    @ObservationIgnored private var pendingRoute: AppRoute?
    @ObservationIgnored private var pendingOriginHash: String?

    static let allCategories = ["assistant_approval", "assistant_result", "account_alert"]

    override init() {
        super.init()
        UNUserNotificationCenter.current().delegate = self
    }

    func prepare() async {
        authorization = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
    }

    var deviceID: String {
        if let id = UserDefaults.standard.string(forKey: "telepilot.push.installation") { return id }
        let id = UUID().uuidString
        UserDefaults.standard.set(id, forKey: "telepilot.push.installation")
        return id
    }
    private var preferenceKey: String {
        "telepilot.push.enabled." + (model?.serverURL?.absoluteString ?? "") + "." + String(model?.user?.id ?? 0)
    }
    private var categoriesKey: String { preferenceKey + ".categories" }
    private var originHash: String {
        SHA256.hash(data: Data((model?.serverURL?.absoluteString ?? "").utf8)).map { String(format: "%02x", $0) }.joined()
    }

    func connect(model: AppModel) async {
        self.model = model
        isEnabled = UserDefaults.standard.bool(forKey: preferenceKey)
        if let saved = UserDefaults.standard.stringArray(forKey: categoriesKey) {
            categories = Set(saved).intersection(Self.allCategories)
        } else {
            categories = Set(Self.allCategories)
        }
        await prepare()
        await reloadConfiguration()
        await reloadDevices()
        if isEnabled, authorization == .authorized || authorization == .provisional {
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    func reloadConfiguration() async {
        guard let model else { return }
        do { configuration = try await model.request(path: "/api/apns/config"); configurationError = nil }
        catch { configurationError = "服务器未提供推送配置，可能需要升级服务端，或当前连接暂不可用。"; configuration = nil }
    }

    /// 读取服务器上的设备列表，找出本机安装 ID 对应的记录，用于“设备已注册”检查项。
    func reloadDevices() async {
        guard let model else { return }
        do {
            let list = try await model.request(path: "/api/apns/devices")
            serverDevice = list.arrayValue?.first { $0["device_id"]?.stringValue == deviceID }
            deviceLookupFailed = false
        } catch {
            deviceLookupFailed = true
        }
        hasLoadedDevices = true
    }

    /// 修改订阅的通知类别；已有设备令牌时立即重新注册，使服务器按新类别投递。
    func setCategory(_ category: String, enabled on: Bool) {
        if on { categories.insert(category) } else { categories.remove(category) }
        UserDefaults.standard.set(Array(categories).sorted(), forKey: categoriesKey)
        if isEnabled, let deviceToken { registered(deviceToken: deviceToken) }
    }

    func enable() async {
        guard !isRegistering else { return }
        isRegistering = true
        registrationError = nil
        do {
            let granted = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])
            authorization = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
            guard granted else { isRegistering = false; return }
            isEnabled = true
            UserDefaults.standard.set(true, forKey: preferenceKey)
            UIApplication.shared.registerForRemoteNotifications()
        } catch {
            isRegistering = false
            registrationError = "无法设置系统通知权限，请稍后在系统设置中重试。"
        }
    }

    func disable() async {
        guard let model else { return }
        do {
            _ = try await model.request(path: "/api/apns/devices/" + deviceID, method: "DELETE")
        } catch let error as APIError {
            if case .server(status: 404, code: _) = error { }
            else { registrationError = "未能注销服务器设备，请联网后重试。"; return }
        } catch { registrationError = "未能注销服务器设备，请联网后重试。"; return }
        UIApplication.shared.unregisterForRemoteNotifications()
        isEnabled = false
        deviceToken = nil
        serverDevice = nil
        testResult = nil
        UserDefaults.standard.set(false, forKey: preferenceKey)
        isRegistering = false
        registrationError = nil
    }

    func registered(deviceToken: Data) {
        isRegistering = false
        self.deviceToken = deviceToken
        Task {
            guard let model, isEnabled else { return }
            do {
                let environment = Self.signingEnvironment
                guard let environment else {
                    registrationError = "当前签名未声明推送环境。请用包含 Push Notifications 的描述文件自签后重试。"
                    return
                }
                let token = deviceToken.map { String(format: "%02x", $0) }.joined()
                let subscribed = Self.allCategories.filter { categories.contains($0) }
                let body: JSONValue = ["device_id": .string(deviceID), "device_token": .string(token),
                                       "bundle_id": .string(Bundle.main.bundleIdentifier ?? ""),
                                       "environment": .string(environment),
                                       "server_origin_sha256": .string(originHash),
                                       "categories": .array(subscribed.map { JSONValue.string($0) })]
                let device = try await model.request(path: "/api/apns/devices", method: "POST", body: body)
                serverDevice = device
                deviceLookupFailed = false
                hasLoadedDevices = true
                registrationError = nil
                statusMessage = "设备已注册，实际送达可通过测试通知确认。"
            } catch let error as APIError {
                statusMessage = nil
                if case .server(status: 409, code: _) = error {
                    registrationError = "服务器拒绝注册：本机 Bundle ID 或推送环境与服务器 APNs 配置不一致，或此设备已绑定其他用户。请对照下方检查项修改服务器配置。"
                } else {
                    registrationError = "设备尚未注册到服务器，请检查网络与 APNs 配置后重试。"
                }
            } catch { registrationError = "设备尚未注册到服务器，请检查网络与 APNs 配置后重试。"; statusMessage = nil }
        }
    }

    /// 当前安装包实际使用的 APNs 环境：sandbox（开发签名 / Xcode 调试）或 production（Ad Hoc、TestFlight、App Store）。
    static var signingEnvironment: String? {
        #if targetEnvironment(simulator)
        return "sandbox"
        #else
        // TestFlight 与 App Store 安装包不含 embedded.mobileprovision，它们始终使用生产环境。
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision") else { return "production" }
        guard let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .isoLatin1),
              let start = text.range(of: "<?xml"), let end = text.range(of: "</plist>"),
              start.lowerBound < end.upperBound,
              let plistData = String(text[start.lowerBound..<end.upperBound]).data(using: .isoLatin1),
              let plist = try? PropertyListSerialization.propertyList(from: plistData, format: nil) as? [String: Any],
              let entitlements = plist["Entitlements"] as? [String: Any],
              let environment = entitlements["aps-environment"] as? String else { return nil }
        return environment == "development" ? "sandbox" : environment == "production" ? "production" : nil
        #endif
    }

    /// 发送测试通知。写操作不自动重试；网络失败时结果标记为未知而不是失败。
    func test() async {
        guard let model, !isTesting else { return }
        isTesting = true
        testResult = nil
        defer { isTesting = false }
        do {
            let result = try await model.request(path: "/api/apns/test", method: "POST", body: ["device_id": .string(deviceID)])
            let ok = result["ok"]?.boolValue == true
            let message = result["message"]?.stringValue ?? (ok ? "Apple 已接受测试通知。" : "Apple 未接受测试通知。")
            testResult = PushTestResult(outcome: ok ? .accepted : .rejected, message: message)
        } catch {
            testResult = PushTestResult(outcome: .unknown, message: "未能确认测试结果，请检查网络后手动重试；不要重复点击以免收到多条通知。")
        }
        await reloadDevices()
    }

    func failedToRegister(error: Error) {
        isRegistering = false
        registrationError = "Apple 尚未提供推送设备令牌。请检查网络、推送签名权限和真机环境后重试。"
    }

    func consumePendingRoute() -> AppRoute? {
        guard model?.phase == .signedIn, pendingOriginHash == originHash else { return nil }
        defer { pendingRoute = nil }
        return pendingRoute
    }

    func deliverPendingRoute(unlocked: Bool) {
        guard unlocked, let route = consumePendingRoute() else { return }
        onRoute?(route)
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        guard response.actionIdentifier == UNNotificationDefaultActionIdentifier,
              let route = AppRoute(notificationUserInfo: response.notification.request.content.userInfo),
              let origin = response.notification.request.content.userInfo["server_origin_sha256"] as? String else { return }
        await MainActor.run {
            self.pendingRoute = route
            self.pendingOriginHash = origin
            self.deliverPendingRoute(unlocked: self.canRoute?() == true)
        }
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .list, .badge, .sound]
    }
}

struct PushTestResult: Equatable, Sendable {
    enum Outcome: Sendable { case accepted, rejected, unknown }
    let outcome: Outcome
    let message: String
}
