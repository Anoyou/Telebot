import SwiftUI

enum MoreDestination: Hashable {
    case accounts
    case account(Int)
    case settings
    case about
    case module(ManagementModule)
}

struct MoreView: View {
    @Binding var path: [MoreDestination]
    @Environment(AppModel.self) private var model
    /// 全局搜索：左上角放大镜展开系统搜索栏，列表换成搜索结果（与概览、插件、AI 页同一写法）。
    @State private var searchText = ""
    @State private var searching = false

    var body: some View {
        List {
            if searching {
                GlobalSearchResults(query: searchText)
            } else {
                moreContent
            }
        }
        .pageSearch(text: $searchText, isActive: $searching, prompt: "管理模块、账号或插件", identifier: "more.search")
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("更多")
        .accessibilityIdentifier("more.screen")
        .navigationDestination(for: MoreDestination.self) { destination in
            switch destination {
            case .accounts: AccountsView(embedded: true)
            case .account(let id): AccountDetailView(id: id)
            case .settings: SettingsView(embedded: true)
            case .about: AboutView()
            case .module(let module): ManagementModuleView(module: module)
            }
        }
    }

    @ViewBuilder private var moreContent: some View {
            EmergencyStopSection()

            Section("工作区") {
                NavigationLink(value: MoreDestination.accounts) {
                    Label {
                        LabeledContent("账号") { Text("\(model.accounts.count)") }
                    } icon: {
                        Image(systemName: "person.2")
                    }
                }
                .accessibilityIdentifier("more.accounts")
                NavigationLink(value: MoreDestination.settings) {
                    Label("设置", systemImage: "gearshape")
                }
                .accessibilityIdentifier("more.settings")
            }

            Section("管理") {
                ForEach(ManagementModule.moreCases) { module in
                    NavigationLink(value: MoreDestination.module(module)) {
                        Label(module.title, systemImage: module.symbol)
                    }
                }
            }

            Section {
                NavigationLink(value: MoreDestination.about) {
                    Label {
                        HStack {
                            Text("关于与更新")
                            Spacer()
                            if model.serverUpdateAvailable {
                                Text("服务器有新版本").font(.caption.weight(.semibold)).foregroundStyle(.tint)
                            }
                        }
                    } icon: { Image(systemName: "info.circle") }
                }
                .accessibilityIdentifier("more.about")
            }
    }
}
