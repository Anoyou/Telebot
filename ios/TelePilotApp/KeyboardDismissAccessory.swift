import UIKit

/// 第 24 轮：全 App 统一的「收起键盘」——键盘上方一条工具栏，右侧「收起」按钮（`keyboard.chevron.compact.down`）。
///
/// 为什么不用 SwiftUI 的 `.toolbar { ToolbarItemGroup(placement: .keyboard) }`：它要挂在每个页面（sheet 不继承），
/// 父子页面都挂时按钮会重复出现，且在 TabView / NavigationStack 嵌套里偶有不显示。这里在 App 启动时监听系统的
/// 「开始编辑」通知，给**还没有**辅助视图的输入框（`UITextField`，以及 SwiftUI 多行 `TextField(axis: .vertical)`、
/// `TextEditor` 背后的 `UITextView`）挂上同一条工具栏，所有页面、sheet、Agent 输入栏一次覆盖。
///
/// - 已经有辅助视图的输入框不动：登录页、Cloudflare Access 自己的「完成」（SwiftUI 键盘工具栏）保持原样，不会出现两条栏。
/// - 搜索栏（`UISearchTextField`）不挂：系统搜索栏有自己的「取消」。
/// - 只读的 `UITextView`（「选择文本」面板）不会开始编辑，自然不挂。
/// - 按钮无障碍标签「收起键盘」，标识 `keyboard.dismiss`；点按让当前输入框放弃焦点（SwiftUI 的 `FocusState` 同步变为未聚焦）。
/// - 界面测试默认不挂（多出的 44pt 工具栏会挡住紧贴键盘的元素，影响既有测试的点按位置），
///   带 `--ui-test-keyboard-accessory` 启动时才挂，由专门的测试验证。
@MainActor
final class KeyboardDismissAccessory: NSObject {
    static let shared = KeyboardDismissAccessory()
    static let identifier = "keyboard.dismiss"

    private var installed = false

    /// App 启动时调用一次。
    func install() {
        guard !installed else { return }
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--ui-testing") && !arguments.contains("--ui-test-keyboard-accessory") { return }
        installed = true
        let center = NotificationCenter.default
        center.addObserver(self, selector: #selector(textFieldBegan(_:)), name: UITextField.textDidBeginEditingNotification, object: nil)
        center.addObserver(self, selector: #selector(textViewBegan(_:)), name: UITextView.textDidBeginEditingNotification, object: nil)
    }

    @objc private func textFieldBegan(_ note: Notification) {
        guard let field = note.object as? UITextField, !(field is UISearchTextField), field.inputAccessoryView == nil else { return }
        field.inputAccessoryView = makeBar()
        field.reloadInputViews()
    }

    @objc private func textViewBegan(_ note: Notification) {
        guard let view = note.object as? UITextView, view.isEditable, view.inputAccessoryView == nil else { return }
        view.inputAccessoryView = makeBar()
        view.reloadInputViews()
    }

    private func makeBar() -> UIToolbar {
        let bar = UIToolbar(frame: CGRect(x: 0, y: 0, width: 320, height: 44))
        bar.autoresizingMask = [.flexibleWidth]
        let dismiss = UIBarButtonItem(title: "收起", image: UIImage(systemName: "keyboard.chevron.compact.down"),
                                      primaryAction: UIAction { _ in
                                          // 无论 SDK 把处理闭包标成什么隔离，点按总在主线程：显式回到主 actor（Swift 6 严格并发）。
                                          MainActor.assumeIsolated {
                                              _ = UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
                                          }
                                      })
        dismiss.accessibilityLabel = "收起键盘"
        dismiss.accessibilityIdentifier = Self.identifier
        bar.items = [UIBarButtonItem(systemItem: .flexibleSpace), dismiss]
        bar.sizeToFit()
        return bar
    }
}
