import SwiftUI
import UIKit

/// 第 24 轮：全 App 统一的「收起键盘」——键盘上方工具栏右侧「收起」按钮（`keyboard.chevron.compact.down`）。
///
/// 实现用 SwiftUI 原生的键盘工具栏 `.toolbar { ToolbarItemGroup(placement: .keyboard) }`。
/// 第一版在 App 启动时监听「开始编辑」通知、给输入框设置 `inputAccessoryView`，CI 界面测试证实对 SwiftUI 输入框无效：
/// SwiftUI 的 `TextField` / `TextEditor` 背后的 UIKit 输入框由 SwiftUI 自己管理辅助视图（它就是用这个位置放键盘工具栏的），
/// 外部设置会被忽略或覆盖，键盘上方什么也没有。所以改回系统支持的写法：
///
/// - 每个 Dock 分页（Agent 除外）和根级 sheet 的根上挂一次 `.keyboardDismissToolbar()`，页面里的输入框共用；
/// - Agent 页的输入栏自己挂（`AssistantComposer`，只在输入栏有焦点时出现，点按直接把焦点状态设为未聚焦）；
/// - 页面里已经有自己键盘工具栏「完成」的（登录页、Cloudflare Access 弹窗）不再叠加。
/// - 按钮读屏「收起键盘」，标识 `keyboard.dismiss`；点按让当前输入框放弃焦点（SwiftUI 的 `FocusState` 同步变为未聚焦）。
/// - 界面测试默认不挂（多出的 44pt 工具栏会挡住紧贴键盘的元素，影响既有测试的点按位置），
///   带 `--ui-test-keyboard-accessory` 启动时才挂，由专门的测试验证。
enum KeyboardDismiss {
    static let identifier = "keyboard.dismiss"

    /// 是否显示「收起」按钮：正式使用总是显示；界面测试只在带 `--ui-test-keyboard-accessory` 时显示。
    static let isEnabled: Bool = {
        let arguments = ProcessInfo.processInfo.arguments
        return !arguments.contains("--ui-testing") || arguments.contains("--ui-test-keyboard-accessory")
    }()

    /// 让当前第一响应者（任何输入框）放弃焦点，键盘随之收起。
    @MainActor static func resignCurrentResponder() {
        _ = UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }
}

/// 键盘工具栏里的「收起」按钮本身（全 App 同一个外观与标识）。
struct KeyboardDismissButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Label("收起", systemImage: "keyboard.chevron.compact.down")
        }
        .accessibilityLabel("收起键盘")
        .accessibilityIdentifier(KeyboardDismiss.identifier)
    }
}

private struct KeyboardDismissToolbar: ViewModifier {
    func body(content: Content) -> some View {
        content.toolbar {
            if KeyboardDismiss.isEnabled {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    KeyboardDismissButton { KeyboardDismiss.resignCurrentResponder() }
                }
            }
        }
    }
}

extension View {
    /// 给这一棵视图里的输入框挂上键盘上方的「收起」按钮。每个呈现层（Dock 分页、sheet）只在根上挂一次，避免重复。
    func keyboardDismissToolbar() -> some View {
        modifier(KeyboardDismissToolbar())
    }
}
