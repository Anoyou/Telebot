import SwiftUI

// MARK: - 页面顶部分段切换与页面级搜索（全 App 统一规则，见 DESIGN.md「分段切换与筛选」）
//
// 1. 页面级分段（插件中心／发现／已安装、AI 概览／供应商／用量、插件详情各分区、待确认操作的状态）：
//    原生 `Picker` + `.pickerStyle(.segmented)`、系统默认 controlSize，作为滚动内容的第一项，位于导航标题正下方。
//    List 页面用 `.pageHeaderRow()` 放在第一个 Section；ScrollView 页面用 `PageLayout.margin(width:)`
//    取与系统分组列表相同的左右边距。分段控件所在的行只放分段控件本身：iOS 26 起分组列表的单元格会按分组卡片的
//    大圆角裁切行内容，行一高（例如第五轮把搜索框和分段控件放在同一行）四角就会被切掉。
// 2. 页面级搜索：导航栏左上角的放大镜按钮（`.pageSearch(...)`），点按后展开系统原生搜索栏（`.searchable`），
//    可直接输入，点“取消”收起并清空。不在内容里常驻搜索框。
// 3. 内容内的筛选（时间范围、结果、分组方式等）是表单行里的原生分段控件；选项多或文字长时用原生 `Picker(.menu)`。

/// 页面内容的左右边距：与系统 insetGrouped 列表（UIKit systemMinimumLayoutMargins）一致——
/// 竖屏宽度不小于 414pt（Plus／Max、iPad）为 20pt，其余 iPhone 为 16pt。
/// ScrollView 页面用它对齐 List 页面的卡片边缘，切换分页时分段控件不左右跳动。
enum PageLayout {
    static func margin(width: CGFloat) -> CGFloat { width >= 414 ? 20 : 16 }

    /// List 页面第一行分段控件相对滚动内容顶部的下移量：insetGrouped 首个分组的上边距，加上分段控件在
    /// 最小行高（44pt）的行里垂直居中的偏移。ScrollView 页面（插件中心）在分段控件上方留同样的距离，
    /// 切换分页时分段控件的纵向位置一致。数值取自 iOS 26/27 模拟器实测（26px @3x），
    /// UI 测试 `testSegmentedHeadersShareFrameAndAreNotClipped` 会校验。
    static let listHeaderTop: CGFloat = 26.0 / 3.0
}

extension View {
    /// 把页面顶部的分段控件作为 List 第一个 Section 的行：透明背景、零边距、隐藏分隔线。
    /// 行内容宽度即分组卡片宽度，所以分段控件与下方卡片左右对齐。行内只放分段控件（见文件头第 1 条）。
    func pageHeaderRow() -> some View {
        listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 4, trailing: 0))
            .listRowSeparator(.hidden)
    }

    /// 页面级搜索：导航栏左上角放大镜按钮（无障碍标签「搜索」），点按展开系统原生搜索栏并聚焦；
    /// 点“取消”收起搜索栏并清空 `text`。全 App 有页面级搜索的页面都用它，位置与样式一致。
    /// - Parameters:
    ///   - text: 搜索内容，由页面用来过滤当前列表（或当前分页）。
    ///   - isActive: 搜索栏是否展开；页面可据此切换为搜索结果视图。
    ///   - identifier: 放大镜按钮的无障碍标识符（UI 测试使用）。
    func pageSearch(text: Binding<String>, isActive: Binding<Bool>, prompt: String, identifier: String) -> some View {
        modifier(PageSearchModifier(text: text, isActive: isActive, prompt: prompt, identifier: identifier))
    }
}

/// `pageSearch` 的实现。
///
/// 系统没有“平时隐藏、点按钮才出现”的导航栏搜索栏：`.searchable` 一挂上，大标题页面就会在标题下方常驻搜索栏。
/// 所以只在展开时挂 `.searchable(text:isPresented:placement:)`，并立即把 `isPresented` 置为真，让系统搜索栏
/// 直接进入输入状态；用户点“取消”时系统把 `isPresented` 置回假，此时清空内容并收起。
/// 页面需要跨展开／收起保留的状态（例如插件中心所选账号与筛选）应放在不随内容重建的位置。
private struct PageSearchModifier: ViewModifier {
    @Binding var text: String
    @Binding var isActive: Bool
    let prompt: String
    let identifier: String
    @State private var presented = false

    func body(content: Content) -> some View {
        Group {
            if isActive {
                content
                    .searchable(text: $text, isPresented: $presented,
                                placement: .navigationBarDrawer(displayMode: .always), prompt: Text(prompt))
                    .task {
                        // 等系统搜索控制器挂好后再激活，否则首次设置会被忽略。
                        await Task.yield()
                        presented = true
                    }
            } else {
                content
            }
        }
        .onChange(of: isActive) { _, active in
            if !active { presented = false }
        }
        .onChange(of: presented) { old, new in
            if old && !new {
                text = ""
                isActive = false
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    if isActive { presented = true } else { isActive = true }
                } label: {
                    Label("搜索", systemImage: "magnifyingglass")
                }
                .accessibilityLabel("搜索")
                .accessibilityIdentifier(identifier)
            }
        }
    }
}
