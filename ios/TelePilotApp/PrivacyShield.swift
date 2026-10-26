import SwiftUI
import UIKit

/// 界面锁遮罩：只在用户开启的界面锁处于锁定状态时，用场景级窗口盖住内容（包括已弹出的 sheet）。
/// 不再在多任务切换器、截屏或录屏时遮挡内容（iOS 0.12.0 起按用户要求移除）。
struct AppLockShield: UIViewRepresentable {
    let lock: AppLockModel

    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> UIView { UIView(frame: .zero) }
    func updateUIView(_ uiView: UIView, context: Context) {
        let shielded = lock.isObscured
        let coordinator = context.coordinator
        // updateUIView can run before SwiftUI attaches the anchor to its window.
        DispatchQueue.main.async {
            guard let scene = uiView.window?.windowScene else { return }
            coordinator.show(in: scene, shielded: shielded, lock: lock)
        }
    }
    static func dismantleUIView(_ uiView: UIView, coordinator: Coordinator) { coordinator.window?.isHidden = true }

    @MainActor final class Coordinator {
        var window: UIWindow?
        func show(in scene: UIWindowScene, shielded: Bool, lock: AppLockModel) {
            guard shielded else { window?.isHidden = true; return }
            if window?.windowScene !== scene {
                window = UIWindow(windowScene: scene)
                window?.windowLevel = .alert + 1
                window?.rootViewController = UIHostingController(rootView: AppLockCover().environment(lock))
            }
            window?.isHidden = false
        }
    }
}

struct SensitiveText: View {
    let value: String
    @State private var revealed = false
    @Environment(AppLockModel.self) private var lock
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        HStack {
            Text(revealed ? value : "••••••••").textSelection(.disabled).privacySensitive()
            Button {
                Task {
                    if revealed { revealed = false }
                    else if await lock.verifySensitiveAccess() { revealed = true }
                }
            } label: { Image(systemName: revealed ? "eye.slash" : "eye") }
                .buttonStyle(.borderless).accessibilityLabel(revealed ? "隐藏敏感内容" : "验证后显示")
        }
        // 进入后台后重新遮盖已显示的密钥；不再因截屏、录屏或多任务切换器而遮盖。
        .onChange(of: scenePhase) { _, phase in if phase == .background { revealed = false } }
    }
}
