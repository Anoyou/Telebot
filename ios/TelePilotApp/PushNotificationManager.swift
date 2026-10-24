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
    @ObservationIgnored private weak var model: AppModel?
    @ObservationIgnored private var deviceToken: Data?
    var onRoute: (@MainActor (AppRoute) -> Void)?
    var canRoute: (@MainActor () -> Bool)?
    @ObservationIgnored private var pendingRoute: AppRoute?
    @ObservationIgnored private var pendingOriginHash: String?

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
    private var originHash: String {
        SHA256.hash(data: Data((model?.serverURL?.absoluteString ?? "").utf8)).map { String(format: "%02x", $0) }.joined()
    }

    func connect(model: AppModel) async {
        self.model = model
        isEnabled = UserDefaults.standard.bool(forKey: preferenceKey)
        await prepare()
        await reloadConfiguration()
        if isEnabled, authorization == .authorized || authorization == .provisional {
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    func reloadConfiguration() async {
        guard let model else { return }
        do { configuration = try await model.request(path: "/api/apns/config"); configurationError = nil }
        catch { configurationError = "服务器未提供推送配置，可能需要升级服务端，或当前连接暂不可用。"; configuration = nil }
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
                let body: JSONValue = ["device_id": .string(deviceID), "device_token": .string(token),
                                       "bundle_id": .string(Bundle.main.bundleIdentifier ?? ""),
                                       "environment": .string(environment),
                                       "server_origin_sha256": .string(originHash),
                                       "categories": ["assistant_approval", "account_alert", "assistant_result"]]
                _ = try await model.request(path: "/api/apns/devices", method: "POST", body: body)
                registrationError = nil
                statusMessage = "设备已注册，实际送达可通过测试通知确认。"
            } catch { registrationError = "设备尚未注册到服务器，请检查 APNs 配置后重试。"; statusMessage = nil }
        }
    }

    static var signingEnvironment: String? {
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .isoLatin1),
              let start = text.range(of: "<?xml"), let end = text.range(of: "</plist>"),
              start.lowerBound < end.upperBound,
              let plistData = String(text[start.lowerBound..<end.upperBound]).data(using: .isoLatin1),
              let plist = try? PropertyListSerialization.propertyList(from: plistData, format: nil) as? [String: Any],
              let entitlements = plist["Entitlements"] as? [String: Any],
              let environment = entitlements["aps-environment"] as? String else { return nil }
        return environment == "development" ? "sandbox" : environment == "production" ? "production" : nil
    }

    func test() async {
        guard let model else { return }
        do {
            let result = try await model.request(path: "/api/apns/test", method: "POST", body: ["device_id": .string(deviceID)])
            statusMessage = result["message"]?.stringValue ?? "测试请求已提交，请检查系统通知。"
        } catch { registrationError = "推送测试未完成，请检查服务器配置与设备注册状态。" }
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
