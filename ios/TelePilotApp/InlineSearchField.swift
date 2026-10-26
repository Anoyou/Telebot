import SwiftUI

/// 分段控件下方的统一搜索栏（插件中心／发现／已安装、AI 供应商／用量共用）。
///
/// 外观对齐系统搜索栏（放大镜、占位文字、清除按钮、系统填充色圆角背景），
/// 作为内容放在分段控件正下方、间距 8pt；不用 `.searchable`，因为大标题页面的
/// `.searchable` 会把搜索栏放进导航栏、出现在分段控件上方，各分页位置不一致。
struct InlineSearchField: View {
    let prompt: String
    @Binding var text: String
    var identifier = "search.field"
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
            TextField(prompt, text: $text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .focused($focused)
                .accessibilityIdentifier(identifier)
            if !text.isEmpty {
                Button {
                    text = ""
                } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("清除搜索")
            }
        }
        .font(.body)
        .padding(.horizontal, 8)
        .frame(minHeight: 36)
        .background(Color(.tertiarySystemFill), in: .rect(cornerRadius: 10))
        .contentShape(.rect)
        .onTapGesture { focused = true }
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
