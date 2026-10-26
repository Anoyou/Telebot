import SwiftUI
import TelePilotCore

struct AccountsView: View {
    var embedded = false
    @Environment(AppModel.self) private var model
    @Environment(WorkspacePreferences.self) private var preferences
    @State private var search = ""
    @State private var searching = false

    private var filteredAccounts: [Account] {
        guard !search.isEmpty else { return model.accounts }
        return model.accounts.filter {
            AccountPresentation.name($0).localizedCaseInsensitiveContains(search)
                || $0.phone.localizedCaseInsensitiveContains(search)
                || ($0.tgUsername?.localizedCaseInsensitiveContains(search) ?? false)
        }
    }

    var body: some View {
        Group {
            if embedded { list }
            else { NavigationStack { list } }
        }
    }

    private var list: some View {
            List {
                if let error = model.accountsError {
                    Section { NoticeView(title: model.accountsLoaded ? "账号数据已过期" : "账号读取失败", message: error) }
                }
                if !filteredAccounts.isEmpty {
                    Section {
                        ForEach(filteredAccounts) { account in
                            NavigationLink {
                                AccountDetailView(id: account.id)
                            } label: {
                                AccountRow(account: account)
                            }
                            .accessibilityIdentifier("account.row.\(account.id)")
                            .swipeActions(edge: .leading) {
                                Button(preferences.isFavoriteAccount(account.id) ? "取消收藏" : "收藏", systemImage: "star") {
                                    preferences.toggleAccount(account.id)
                                }.tint(.orange)
                            }
                        }
                    } header: { Text("\(filteredAccounts.count) 个账号") } footer: {
                        Text("账号启用状态不代表运行进程存活；实时进程信息请看概览。")
                    }
                } else if model.accountsLoaded {
                    ContentUnavailableView(
                        search.isEmpty ? "还没有账号" : "没有匹配的账号",
                        systemImage: search.isEmpty ? "person.crop.circle.badge.plus" : "magnifyingglass",
                        description: Text(search.isEmpty ? "点击右上角添加你的 Telegram 账号。" : "试试姓名、手机号或用户名。")
                    )
                } else if model.isRefreshing {
                    HStack { Spacer(); ProgressView("正在读取账号…"); Spacer() }
                }
                if model.accountsError != nil || (model.accountsLoaded && model.accounts.isEmpty) {
                    Section {
                        Button("重新读取账号", systemImage: "arrow.clockwise") { Task { await model.refresh() } }
                            .disabled(model.isRefreshing)
                        if let url = model.serverURL {
                            Link(destination: url) { Label("打开 Web 管理台", systemImage: "safari") }
                        }
                    }
                }
            }
            .navigationTitle("账号").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink { AccountManagementLoginView() } label: { Label("添加账号", systemImage: "plus") }
                        .accessibilityIdentifier("account.add")
                }
            }
            .pageSearch(text: $search, isActive: $searching, prompt: "姓名、手机号或用户名", identifier: "accounts.search")
            .refreshable { await model.refresh() }
            .accessibilityIdentifier("accounts.screen")
    }
}

private struct AccountRow: View {
    let account: Account

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(ManagementText.accountName(account)).font(.headline)
            Text("•••• " + account.phone.suffix(4)).font(.subheadline).foregroundStyle(.secondary)
            Label(AccountPresentation.status(account.status), systemImage: AccountPresentation.statusSymbol(account.status))
                .font(.caption.weight(.medium))
                .foregroundStyle(account.status == "active" ? Color.primary : Color.secondary)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

struct AccountDetailView: View {
    let id: Int
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var confirming = false
    @State private var confirmedStatus = ""

    private var account: Account? { model.details[id] }
    private var busy: Bool { model.loadingDetails.contains(id) || model.mutatingAccounts.contains(id) }

    var body: some View {
        List {
            if let error = model.detailErrors[id] {
                Section { NoticeView(title: account == nil ? "账号读取失败" : "账号信息已过期", message: error) }
            }
            if let account {
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(ManagementText.accountName(account)).font(.title2.weight(.semibold))
                        SensitiveText(value: account.phone).foregroundStyle(.secondary)
                        Label(AccountPresentation.status(account.status), systemImage: AccountPresentation.statusSymbol(account.status))
                            .font(.subheadline.weight(.medium))
                            .accessibilityIdentifier("account.status")
                    }
                    .padding(.vertical, 6)
                }
                Section("账号信息") {
                    ReadOnlyRow(title: "编号", value: String(account.id))
                    if let username = account.tgUsername, !username.isEmpty {
                        ReadOnlyRow(title: "Telegram 用户名", value: "@\(username)")
                    }
                    ReadOnlyRow(title: "已启用功能", value: "\(account.enabledFeatures)")
                    ReadOnlyRow(title: "代理", value: account.proxyLabel ?? "使用服务器默认出口")
                    if !account.tags.isEmpty { ReadOnlyRow(title: "标签", value: account.tags.joined(separator: "、")) }
                    if let created = account.createdAt { ReadOnlyRow(title: "创建时间", value: formattedDate(created)) }
                    if let notes = account.notes, !notes.isEmpty {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("备注").font(.subheadline).foregroundStyle(.secondary)
                            Text(notes).textSelection(.enabled)
                        }
                    }
                }
                Section("完整管理") {
                    NavigationLink { AccountManagementView(id: id) } label: {
                        Label("资料、插件与自动化", systemImage: "slider.horizontal.3")
                    }
                }
                Section {
                    if model.isSyncingAfterAction {
                        NoticeView(title: "正在同步进程状态", message: "服务器已收到操作，进程数据稍后更新。", symbol: "arrow.triangle.2.circlepath")
                    } else if model.resourcesError != nil {
                        NoticeView(title: "进程数据已过期", message: "请返回概览刷新后查看最新结果。")
                    }
                    if let worker = model.resources?.workers.first(where: { $0.accountID == id }) {
                        ReadOnlyRow(title: "服务器期望", value: worker.desired == "running" ? "运行" : (worker.desired == "stopped" ? "停止" : worker.desired))
                        ReadOnlyRow(title: "实际进程", value: worker.alive ? "已存活" : "未存活")
                        ReadOnlyRow(title: "失败记录", value: "\(worker.failCount)")
                    } else {
                        Text("暂无此账号的运行进程数据。")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                } header: { Text("运行进程") } footer: {
                    Text("显示服务器最近一次读取结果，不依据账号启用状态推断。")
                }
                if let action = AccountPresentation.actionLabel(account) {
                    Section {
                        Button {
                            confirmedStatus = account.status
                            confirming = true
                        } label: {
                            Label(busy ? "正在处理…" : action, systemImage: account.status == "active" ? "pause.circle" : "play.circle")
                                .frame(minHeight: 44)
                        }
                        .disabled(busy || model.detailErrors[id] != nil || model.uncertainActions.contains(id))
                        .accessibilityIdentifier(account.status == "active" ? "account.pause" : "account.resume")
                        .confirmationDialog(confirmedStatus == "active" ? "暂停这个账号？" : "恢复这个账号？", isPresented: $confirming, titleVisibility: .visible) {
                            Button(confirmedStatus == "active" ? "确认暂停" : "确认恢复") {
                                Task { await model.toggleAccount(id: id, expectedStatus: confirmedStatus) }
                            }
                            .accessibilityIdentifier("account.confirmAction")
                            Button("取消", role: .cancel) {}
                                .accessibilityIdentifier("account.cancelAction")
                        } message: {
                            Text(confirmedStatus == "active" ? "服务器将暂停此账号的运行。其他账号不受影响。" : "服务器将尝试恢复此账号。成功接受请求不代表进程已立即就绪。")
                        }
                    } header: { Text("运行控制") } footer: {
                        Text("操作修改服务器上的账号状态，运行进程会随后同步。App 不在手机上运行 Telegram 任务。")
                    }
                } else {
                    Section {
                        NoticeView(
                            title: "请在 Web 管理台处理",
                            message: ["dead", "login_required"].contains(account.status)
                                ? "此账号需要检查或重新登录，App 中暂停与恢复操作不可用。"
                                : "服务器返回了当前 App 尚不支持的账号状态，已禁用运行操作。"
                        )
                    }
                }
            } else if busy {
                HStack { Spacer(); ProgressView("正在读取账号详情…"); Spacer() }
            }
            if let message = model.actionMessages[id] {
                Section {
                    NoticeView(title: model.uncertainActions.contains(id) ? "结果待确认" : "操作反馈", message: message, symbol: "info.circle")
                        .accessibilityIdentifier("account.actionResult")
                }
            }
            Section {
                Button { Task { await model.confirmLatestAccount(id: id) } } label: {
                    Label("刷新账号状态", systemImage: "arrow.clockwise").frame(minHeight: 44)
                }
                .disabled(busy)
                .accessibilityIdentifier("account.refresh")
                if let url = model.serverURL {
                    Link(destination: url) { Label("打开 Web 管理台", systemImage: "safari").frame(minHeight: 44) }
                }
            }
        }
        .navigationTitle("账号详情")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("account.detail")
        .task(id: id) { await model.loadAccount(id: id) }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await model.loadAccount(id: id) } }
        }
        .refreshable { await model.confirmLatestAccount(id: id) }
    }

    private func formattedDate(_ value: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
        return date?.formatted(date: .abbreviated, time: .shortened) ?? value
    }
}
