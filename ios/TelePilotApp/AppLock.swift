import Foundation
import LocalAuthentication
import Observation

enum AppLockError: LocalizedError {
    case cancelled
    case failed
    case unavailable

    var errorDescription: String? {
        switch self {
        case .cancelled: "验证已取消，内容仍保持锁定。"
        case .failed: "未能通过设备验证，请重试。"
        case .unavailable: "设备未设置可用的 Face ID、Touch ID 或锁屏密码。"
        }
    }
}

protocol AppLockAuthenticating: Sendable {
    func authenticate(reason: String) async throws
    func availability() -> String?
}

struct DeviceAuthenticator: AppLockAuthenticating {
    func availability() -> String? {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            return error?.code == LAError.biometryNotEnrolled.rawValue
                ? "请先在系统设置中录入 Face ID 或 Touch ID。"
                : AppLockError.unavailable.localizedDescription
        }
        return nil
    }

    func authenticate(reason: String) async throws {
        let context = LAContext()
        context.localizedCancelTitle = "保持锁定"
        do { try await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) }
        catch let error as LAError {
            switch error.code {
            case .userCancel, .appCancel, .systemCancel: throw AppLockError.cancelled
            case .biometryNotAvailable, .biometryNotEnrolled, .biometryLockout,
                 .passcodeNotSet, .notInteractive: throw AppLockError.unavailable
            default: throw AppLockError.failed
            }
        }
    }
}

@MainActor @Observable
final class AppLockModel {
    private(set) var isEnabled = false
    private(set) var isObscured = false
    private(set) var isAuthenticating = false
    private(set) var failureMessage: String?

    @ObservationIgnored private let authenticator: any AppLockAuthenticating
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let preferenceKey = "telepilot.interfaceLock"
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private let graceKey = "telepilot.interfaceLock.grace"
    @ObservationIgnored private var backgroundedAt: Date?

    /// 进入后台后多久需要重新验证。0 表示立即锁定（默认）。
    static let graceOptions: [Int] = [0, 60, 300, 900]
    var graceSeconds: Int {
        didSet { defaults.set(graceSeconds, forKey: graceKey) }
    }

    init(defaults: UserDefaults = .standard, authenticator: (any AppLockAuthenticating)? = nil) {
        self.defaults = defaults
        let storedGrace = defaults.integer(forKey: "telepilot.interfaceLock.grace")
        graceSeconds = Self.graceOptions.contains(storedGrace) ? storedGrace : 0
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--ui-test-lock") {
            self.authenticator = FixtureAuthenticator()
            isEnabled = true
            isObscured = true
            return
        }
        if ProcessInfo.processInfo.arguments.contains("--ui-testing") {
            self.authenticator = FixtureAuthenticator()
            return
        }
        #endif
        self.authenticator = authenticator ?? DeviceAuthenticator()
        isEnabled = defaults.bool(forKey: preferenceKey)
        isObscured = isEnabled
    }

    /// App 进入后台。宽限期为 0 时立即锁定；否则记录时间，回到前台时由 `resume()` 判断。
    /// 任务切换器与录屏遮挡由场景遮罩独立负责，不受宽限期影响。
    func suspend() {
        guard isEnabled else { return }
        guard graceSeconds > 0 else { lockNow(); return }
        if backgroundedAt == nil { backgroundedAt = Date() }
    }

    /// App 回到前台。超过宽限期（或系统时间被回拨）则锁定。
    func resume() {
        guard isEnabled, let since = backgroundedAt else { return }
        backgroundedAt = nil
        let elapsed = Date().timeIntervalSince(since)
        if elapsed < 0 || elapsed >= Double(graceSeconds) { lockNow() }
    }

    private func lockNow() {
        backgroundedAt = nil
        generation += 1
        isObscured = true
        isAuthenticating = false
        failureMessage = nil
    }

    func unlock() async {
        guard isEnabled, isObscured, !isAuthenticating else { return }
        isAuthenticating = true
        failureMessage = nil
        let token = generation
        defer { isAuthenticating = false }
        do {
            try await authenticator.authenticate(reason: "解锁 TelePilot 管理界面")
            guard token == generation, isEnabled else { return }
            isObscured = false
        } catch {
            guard token == generation else { return }
            failureMessage = (error as? AppLockError)?.localizedDescription ?? AppLockError.failed.localizedDescription
        }
    }

    func setEnabled(_ enabled: Bool) async -> Bool {
        guard enabled != isEnabled, !isAuthenticating else { return false }
        if enabled, let message = authenticator.availability() {
            failureMessage = message
            return false
        }
        isAuthenticating = true
        failureMessage = nil
        let token = generation
        defer { isAuthenticating = false }
        do {
            try await authenticator.authenticate(reason: enabled ? "启用 TelePilot 界面锁" : "关闭 TelePilot 界面锁")
            guard token == generation else { return false }
            isEnabled = enabled
            isObscured = false
            defaults.set(enabled, forKey: preferenceKey)
            return true
        } catch {
            failureMessage = (error as? AppLockError)?.localizedDescription ?? AppLockError.failed.localizedDescription
            return false
        }
    }

    func verifySensitiveAccess() async -> Bool {
        do { try await authenticator.authenticate(reason: "显示 TelePilot 敏感内容"); return true }
        catch { failureMessage = "验证未完成，敏感内容继续隐藏。"; return false }
    }
}

#if DEBUG
private struct FixtureAuthenticator: AppLockAuthenticating {
    func availability() -> String? { nil }
    func authenticate(reason: String) async throws {
        if ProcessInfo.processInfo.arguments.contains("--ui-test-lock-cancel") { throw AppLockError.cancelled }
    }
}
#endif

import SwiftUI

struct AppLockCover: View {
    @Environment(AppLockModel.self) private var lock

    var body: some View {
        ZStack {
            Color(.systemBackground).ignoresSafeArea()
            VStack(spacing: 20) {
                Image("BrandMark").resizable().scaledToFit().frame(width: 76, height: 76)
                Label("TelePilot 已锁定", systemImage: "lock.fill").font(.title2.weight(.semibold))
                Text("通过设备验证后继续管理服务器。").foregroundStyle(.secondary)
                if let failureMessage = lock.failureMessage {
                    Text(failureMessage).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                Button {
                    Task { await lock.unlock() }
                } label: {
                    Label(lock.isAuthenticating ? "正在验证…" : "解锁", systemImage: "faceid")
                        .frame(minWidth: 160, minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .disabled(lock.isAuthenticating)
                .accessibilityIdentifier("lock.unlock")
            }
            .padding(32)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("lock.cover")
        .task { await lock.unlock() }
    }
}
