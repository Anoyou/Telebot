import SwiftUI
import UIKit

// MARK: - 页面顶部分段切换（全 App 统一规则，见 DESIGN.md「分段切换与筛选」）
//
// 1. 页面级分段（插件中心／发现／已安装、AI 概览／供应商／用量、插件详情各分区、待确认操作的状态）：
//    原生 `Picker` + `.pickerStyle(.segmented)`、系统默认 controlSize，作为滚动内容的第一项，
//    位于导航标题正下方；该页需要搜索时，搜索栏紧贴其下（间距 8pt），两者左右边缘与下方列表卡片对齐。
// 2. List 页面用 `.pageHeaderRow()` 放在第一个 Section；ScrollView 页面用 `PageLayout.margin(width:)`
//    取与系统分组列表相同的左右边距。
// 3. 内容内的筛选（时间范围、结果、分组方式等）是表单行里的原生分段控件；选项多或文字长时用原生 `Picker(.menu)`。

/// 页面内容的左右边距：与系统 insetGrouped 列表（UIKit systemMinimumLayoutMargins）一致——
/// 竖屏宽度不小于 414pt（Plus／Max、iPad）为 20pt，其余 iPhone 为 16pt。
/// ScrollView 页面用它对齐 List 页面的卡片边缘，切换分页时分段控件不左右跳动。
enum PageLayout {
    static func margin(width: CGFloat) -> CGFloat { width >= 414 ? 20 : 16 }
}

extension View {
    /// 把页面顶部的“分段控件 + 搜索栏”作为 List 第一个 Section 的行：透明背景、零边距、隐藏分隔线。
    /// 行内容宽度即分组卡片宽度，所以分段控件与下方卡片左右对齐。
    func pageHeaderRow() -> some View {
        listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 4, trailing: 0))
            .listRowSeparator(.hidden)
    }
}

/// 页面顶部的“分段控件 + 搜索栏”组合，两者间距固定为系统标准的 8pt。
struct SegmentedSearchHeader<Segments: View>: View {
    let picker: Segments
    var search: Binding<String>?
    var prompt = "搜索"
    var identifier = "search.field"

    init(@ViewBuilder picker: () -> Segments, search: Binding<String>? = nil, prompt: String = "搜索", identifier: String = "search.field") {
        self.picker = picker()
        self.search = search
        self.prompt = prompt
        self.identifier = identifier
    }

    var body: some View {
        VStack(spacing: 8) {
            picker
            if let search {
                InlineSearchField(prompt: prompt, text: search, identifier: identifier)
            }
        }
    }
}

/// 分段控件下方的统一搜索栏（插件中心／发现／已安装、AI 供应商／用量共用）。
///
/// 直接使用系统搜索框 `UISearchTextField`（与系统搜索栏内部同一个原生控件：放大镜、占位文字、清除按钮、
/// 系统填充色与 Dynamic Type 都由系统提供），作为内容放在分段控件正下方。
/// 不用 `.searchable`：大标题页面的 `.searchable` 会把搜索栏放进导航栏、出现在分段控件上方，各分页位置不一致。
struct InlineSearchField: UIViewRepresentable {
    let prompt: String
    @Binding var text: String
    var identifier = "search.field"

    func makeUIView(context: Context) -> UISearchTextField {
        let field = UISearchTextField()
        field.placeholder = prompt
        field.text = text
        field.autocapitalizationType = .none
        field.autocorrectionType = .no
        field.returnKeyType = .search
        field.clearButtonMode = .whileEditing
        field.font = .preferredFont(forTextStyle: .body)
        field.adjustsFontForContentSizeCategory = true
        field.accessibilityIdentifier = identifier
        field.delegate = context.coordinator
        field.addTarget(context.coordinator, action: #selector(Coordinator.changed(_:)), for: .editingChanged)
        field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return field
    }

    func updateUIView(_ field: UISearchTextField, context: Context) {
        context.coordinator.text = $text
        if field.text != text { field.text = text }
        if field.placeholder != prompt { field.placeholder = prompt }
        if field.accessibilityIdentifier != identifier { field.accessibilityIdentifier = identifier }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: UISearchTextField, context: Context) -> CGSize? {
        CGSize(width: proposal.width ?? 320, height: max(36, uiView.intrinsicContentSize.height))
    }

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    @MainActor
    final class Coordinator: NSObject, UITextFieldDelegate {
        var text: Binding<String>

        init(text: Binding<String>) { self.text = text }

        @objc func changed(_ field: UITextField) { text.wrappedValue = field.text ?? "" }

        func textFieldShouldClear(_ textField: UITextField) -> Bool {
            text.wrappedValue = ""
            return true
        }

        func textFieldShouldReturn(_ textField: UITextField) -> Bool {
            textField.resignFirstResponder()
            return true
        }
    }
}
