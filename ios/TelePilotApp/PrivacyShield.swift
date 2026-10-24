import SwiftUI
import UIKit

/// A scene-level window covers presented sheets as well as the root controller.
struct PrivacyShield: UIViewRepresentable {
    let lock: AppLockModel
    let hidden: Bool
    @Environment(\.isSceneCaptured) private var captured

    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> UIView { UIView(frame: .zero) }
    func updateUIView(_ uiView: UIView, context: Context) {
        let shielded = hidden || captured || lock.isObscured
        let coordinator = context.coordinator
        // updateUIView can run before SwiftUI attaches the anchor to its window.
        DispatchQueue.main.async {
            guard let scene = uiView.window?.windowScene else { return }
            coordinator.show(in: scene, shielded: shielded, lock: lock, canUnlock: !hidden && !captured)
        }
    }
    static func dismantleUIView(_ uiView: UIView, coordinator: Coordinator) { coordinator.window?.isHidden = true }

    @MainActor final class Coordinator {
        var window: UIWindow?
        private var mode = 0
        func show(in scene: UIWindowScene, shielded: Bool, lock: AppLockModel, canUnlock: Bool) {
            guard shielded else { window?.isHidden = true; mode = 0; return }
            if window?.windowScene !== scene {
                window = UIWindow(windowScene: scene)
                window?.windowLevel = .alert + 1
                mode = 0
            }
            let desiredMode = canUnlock && lock.isObscured ? 2 : 1
            if mode == desiredMode { window?.isHidden = false; return }
            mode = desiredMode
            let content = Group {
                if canUnlock && lock.isObscured { AppLockCover().environment(lock) }
                else {
                    ZStack {
                        Color(.systemBackground).ignoresSafeArea()
                        Label("TelePilot 内容已隐藏", systemImage: "lock.shield").font(.headline)
                    }
                }
            }
            window?.rootViewController = UIHostingController(rootView: content)
            window?.isHidden = false
        }
    }
}

struct SensitiveText: View {
    let value: String
    @State private var revealed = false
    @Environment(AppLockModel.self) private var lock
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.isSceneCaptured) private var captured
    var body: some View {
        HStack {
            Text(revealed && !captured ? value : "••••••••").textSelection(.disabled).privacySensitive()
            Button {
                Task {
                    if revealed { revealed = false }
                    else if await lock.verifySensitiveAccess() { revealed = true }
                }
            } label: { Image(systemName: revealed ? "eye.slash" : "eye") }
                .buttonStyle(.borderless).accessibilityLabel(revealed ? "隐藏敏感内容" : "验证后显示")
        }
        .onChange(of: scenePhase) { _, phase in if phase != .active { revealed = false } }
        .onChange(of: captured) { _, value in if value { revealed = false } }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.userDidTakeScreenshotNotification)) { _ in revealed = false }
    }
}
