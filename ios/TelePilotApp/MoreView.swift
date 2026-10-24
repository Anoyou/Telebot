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
    @State private var searching = false

    var body: some View {
        List {
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
                NavigationLink(value: MoreDestination.about) {
                    Label("关于与更新", systemImage: "info.circle")
                }
                .accessibilityIdentifier("more.about")
            }

            Section("管理") {
                ForEach(ManagementModule.moreCases) { module in
                    NavigationLink(value: MoreDestination.module(module)) {
                        Label(module.title, systemImage: module.symbol)
                    }
                }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("更多")
        .accessibilityIdentifier("more.screen")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("全局搜索", systemImage: "magnifyingglass") { searching = true }
                    .accessibilityIdentifier("more.search")
            }
        }
        .navigationDestination(for: MoreDestination.self) { destination in
            switch destination {
            case .accounts: AccountsView(embedded: true)
            case .account(let id): AccountDetailView(id: id)
            case .settings: SettingsView(embedded: true)
            case .about: AboutView()
            case .module(let module): ManagementModuleView(module: module)
            }
        }
        .sheet(isPresented: $searching) { GlobalSearchView() }
    }
}
