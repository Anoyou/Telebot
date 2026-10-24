import SwiftUI
import TelePilotCore

struct AccountManagementView: View {
    let id: Int
    @Environment(AppModel.self) private var model
    var body: some View {
        List {
            Section("账号资料") {
                AccountAvatarView(id: id).frame(maxWidth: .infinity)
                NavigationLink {
                    ManagementLoadedEditorView(title: "编辑账号", path: base, method: "PATCH")
                } label: { Label("编辑名称、标签与连接", systemImage: "pencil") }
                NavigationLink { AccountManagementLoginView(accountID: id, initialPhone: model.details[id]?.phone ?? "") } label: {
                    Label("重新登录 Telegram", systemImage: "person.badge.key")
                }
                ManagementActionLink(action: .init(title: "复制其他账号配置", path: base + "/clone-config",
                    explanation: "从来源账号复制插件与规则到当前账号。请确认来源账号和要复制的功能。"), symbol: "doc.on.doc")
                NavigationLink { FileManagementView(mode: .account(id)) } label: {
                    Label("导出与迁移配置", systemImage: "square.and.arrow.up.on.square")
                }
            }
            Section("自动化") {
                NavigationLink { AccountManagementSchedulerView(id: id) } label: {
                    Label("定时任务与自动指令白名单", systemImage: "calendar.badge.clock")
                }
                NavigationLink { PluginManagementView(accountID: id) } label: {
                    Label("插件配置与规则", systemImage: "puzzlepiece.extension")
                }
                document("消息频率与拟人化", "/humanize", edit: "PUT", symbol: "figure.walk")
                document("账号限流", "/rate-limit", edit: "PUT", symbol: "gauge.with.dots.needle.33percent")
                document("限流用量", "/rate-limit/usage", symbol: "chart.bar")
                collection("限流事件", "/rate-limit/events", symbol: "list.bullet.rectangle.portrait")
                NavigationLink {
                    ManagementCollectionView(resource: .init(title: "限流覆盖规则", path: base + "/rate-limit/overrides", kind: .editable, itemPath: base + "/rate-limit/overrides/{id}", idField: "action"))
                } label: { Label("限流覆盖规则", systemImage: "slider.horizontal.below.rectangle") }
                ManagementActionLink(action: .init(title: "估算发送频率", path: base + "/rate-limit/estimate"), symbol: "function")
                ManagementActionLink(action: .init(title: "严格限流模式", path: base + "/rate-limit/strict"), symbol: "hand.raised")
                NavigationLink { AccountManagementPassthroughView(id: id) } label: {
                    Label("直通优先顺序", systemImage: "arrow.up.arrow.down")
                }
                NavigationLink {
                    ManagementCollectionView(resource: .init(title: "会话白名单", path: base + "/ignored-peers", kind: .editable,
                        create: .init(title: "添加允许会话", path: base + "/ignored-peers"), itemPath: base + "/ignored-peers/{id}",
                        explanation: "列表为空时允许全部会话；列表非空时，仅这些会话会触发消息自动化。"))
                } label: { Label("会话白名单", systemImage: "checklist") }
                collection("最近会话", "/recent-peers", symbol: "bubble.left.and.bubble.right")
                NavigationLink {
                    ManagementCollectionView(resource: .init(title: "账号指令", path: base + "/commands", kind: .accountCommands, itemPath: base + "/commands/{id}"))
                } label: { Label("账号指令与白名单", systemImage: "terminal") }
            }
            Section("账号 Bot") {
                document("Bot 配置", "/bot", edit: "PUT", symbol: "gearshape", actions: [
                    .init(title: "测试 Bot", path: base + "/bot/test"),
                    .init(title: "重启 Bot 运行进程", path: base + "/bot/restart-runtime", destructive: true)
                ])
                document("交互规则", "/interaction-bot", edit: "PUT", symbol: "bubble.left.and.text.bubble.right")
                ManagementActionLink(action: .init(title: "组合交互配置", path: base + "/interaction-bot/composite", method: "PUT"), symbol: "square.stack.3d.up")
                document("转账通知", "/bot/transfer-notice", edit: "PUT", symbol: "arrow.left.arrow.right.circle")
                NavigationLink {
                    ManagementCollectionView(resource: .init(title: "Bot 用户", path: base + "/bot/users", kind: .botUsers,
                        create: .init(title: "添加 Bot 用户", path: base + "/bot/users"), itemPath: base + "/bot/users/{id}"))
                } label: { Label("Bot 用户与权限", systemImage: "person.2.badge.gearshape") }
                NavigationLink {
                    ManagementCollectionView(resource: .init(title: "失败投递", path: base + "/bot/polling-dlq", kind: .deadLetters, idField: "update_id", context: base + "/bot/polling-dlq"))
                } label: { Label("失败投递与恢复", systemImage: "tray.and.arrow.down") }
            }
            Section("Webhook") {
                NavigationLink {
                    ManagementDocumentView(title: "Webhook 配置", path: "/api/webhooks/\(id)", actions: [
                        .init(title: "重置 Webhook Token", path: "/api/webhooks/\(id)/token/reset", destructive: true,
                            explanation: "旧 Token 将立即失效，已有调用方需要同步更新。")
                    ])
                } label: { Label("账号 Webhook 配置", systemImage: "point.3.connected.trianglepath.dotted") }
            }
            Section("删除账号") {
                ManagementActionLink(action: .init(title: "删除账号", path: base, method: "DELETE", destructive: true,
                    explanation: "将撤销此 Telegram 会话并删除服务器上的账号数据。请先完成配置备份。"), symbol: "trash")
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("账号管理").navigationBarTitleDisplayMode(.inline)
    }
    private var base: String { "/api/accounts/\(id)" }
    private func document(_ title: String, _ suffix: String, edit: String? = nil, symbol: String = "doc.text", actions: [ManagementAction] = []) -> some View {
        NavigationLink {
            ManagementDocumentView(title: title, path: base + suffix, actions: actions + (edit.map { [.init(title: "编辑" + title, path: base + suffix, method: $0)] } ?? []))
        } label: { Label(title, systemImage: symbol) }
    }
    private func collection(_ title: String, _ suffix: String, symbol: String = "list.bullet.rectangle.portrait") -> some View {
        NavigationLink { ManagementCollectionView(resource: .init(title: title, path: base + suffix)) } label: { Label(title, systemImage: symbol) }
    }
}

struct ManagementLoadedEditorView: View {
    let title: String
    let path: String
    var method = "PATCH"
    @Environment(AppModel.self) private var model
    @State private var value: JSONValue?
    @State private var error: String?
    var body: some View {
        Group {
            if let value { ManagementFormView(action: .init(title: title, path: path, method: method, seed: value)) }
            else {
                List {
                    if let error { NoticeView(title: "读取失败", message: error) }
                    else { ProgressView("正在读取当前配置…") }
                }.navigationTitle(title).navigationBarTitleDisplayMode(.inline)
            }
        }.task {
            do { value = try await model.request(path: path) }
            catch { self.error = error.localizedDescription }
        }
    }
}

struct AccountAvatarView: View {
    let id: Int
    @Environment(AppModel.self) private var model
    @State private var image: UIImage?
    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFill() }
            else { Image(systemName: "person.crop.circle.fill").resizable().foregroundStyle(.secondary) }
        }.frame(width: 68, height: 68).clipShape(.circle).accessibilityLabel("账号头像")
            .task(id: id) { if let data = try? await model.download(path: "/api/accounts/\(id)/avatar") { image = UIImage(data: data) } }
    }
}

struct AccountManagementLoginView: View {
    var accountID: Int? = nil
    var initialPhone = ""
    @Environment(AppModel.self) private var model
    @State private var apiID = ""
    @State private var apiHash = ""
    @State private var phone = ""
    @State private var proxyID: Int? = nil
    @State private var deviceID: Int? = nil
    @State private var proxies: [JSONValue] = []
    @State private var devices: [JSONValue] = []
    @State private var token: String?
    @State private var code = ""
    @State private var password = ""
    @State private var requiresPassword = false
    @State private var completedID: Int?
    @State private var error: String?
    @State private var busy = false
    @State private var uncertain = false

    var body: some View {
        Form {
            if let completedID {
                Section {
                    Label("Telegram 账号绑定成功", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    NavigationLink { AccountDetailView(id: completedID) } label: { Label("打开账号", systemImage: "person.crop.circle") }
                }
            } else if token == nil {
                Section {
                    TextField("API ID", text: $apiID).keyboardType(.numberPad)
                    SecureField("API Hash", text: $apiHash).textInputAutocapitalization(.never).autocorrectionDisabled().privacySensitive()
                    TextField("国际区号与手机号", text: $phone).keyboardType(.phonePad).textContentType(.telephoneNumber).privacySensitive()
                } header: { Text("Telegram API 凭据") } footer: { Text("使用你自己的 Telegram API 凭据。验证码将由 Telegram 发送，账号会话保存在服务器。") }
                Section("连接") {
                    Picker("代理", selection: $proxyID) {
                        Text("服务器默认出口").tag(nil as Int?)
                        ForEach(Array(proxies.enumerated()), id: \.offset) { _, item in
                            if let id = item["id"]?.intValue { Text(ManagementText.title(item)).tag(Optional(id)) }
                        }
                    }
                    Picker("设备配置", selection: $deviceID) {
                        Text("服务器默认配置").tag(nil as Int?)
                        ForEach(Array(devices.enumerated()), id: \.offset) { _, item in
                            if let id = item["id"]?.intValue { Text(ManagementText.title(item)).tag(Optional(id)) }
                        }
                    }
                }
            } else if requiresPassword {
                Section("Telegram 两步验证") {
                    SecureField("Telegram 两步验证密码", text: $password).textContentType(.password).privacySensitive()
                }
            } else {
                Section("Telegram 验证码") {
                    TextField("验证码", text: $code).keyboardType(.numberPad).textContentType(.oneTimeCode).privacySensitive()
                }
            }
            if let error { Section { NoticeView(title: uncertain ? "结果待确认" : "登录未完成", message: error) } }
            if completedID == nil {
                Section {
                    Button(busy ? "正在处理…" : token == nil ? "发送验证码" : requiresPassword ? "确认两步验证" : "确认验证码",
                           systemImage: token == nil ? "paperplane" : "checkmark.circle") {
                        Task { await submit() }
                    }.disabled(busy || uncertain || !valid)
                    if token != nil {
                        Button("重新开始", systemImage: "arrow.counterclockwise", role: .destructive) {
                            token = nil; code = ""; password = ""; requiresPassword = false; uncertain = false; error = nil
                        }.disabled(busy || uncertain)
                    }
                    if uncertain { Text("请先返回账号列表刷新，确认是否已经绑定成功。不要重复提交。").foregroundStyle(.secondary) }
                }
            }
        }
        .navigationTitle(accountID == nil ? "添加 Telegram 账号" : "重新登录 Telegram").navigationBarTitleDisplayMode(.inline)
        .task {
            phone = initialPhone
            do {
                proxies = ManagementText.items(try await model.request(path: "/api/proxies"))
                devices = ManagementText.items(try await model.request(path: "/api/device-profiles"))
            } catch { self.error = error.localizedDescription }
        }
    }
    private var valid: Bool {
        if token == nil { return Int(apiID) != nil && !apiHash.isEmpty && !phone.isEmpty }
        return requiresPassword ? !password.isEmpty : !code.isEmpty
    }
    private func submit() async {
        guard !busy, !uncertain, valid else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            if let token {
                let path = requiresPassword ? "/api/accounts/login/2fa" : "/api/accounts/login/code"
                let body: JSONValue = .object(["login_token": .string(token), requiresPassword ? "password" : "code": .string(requiresPassword ? password : code)])
                let response = try await model.request(path: path, method: "POST", body: body)
                password = ""; code = ""
                if response["require_2fa"]?.boolValue == true { requiresPassword = true }
                else {
                    guard let id = response["account_id"]?.intValue, id > 0 else { throw APIError.incompatibleResponse }
                    completedID = id; self.token = nil; apiHash = ""; await model.refresh()
                }
            } else {
                guard let id = Int64(apiID) else { return }
                var body: [String: JSONValue] = ["api_id": .integer(id), "api_hash": .string(apiHash), "phone": .string(phone)]
                if let accountID { body["account_id"] = .integer(Int64(accountID)) }
                if let proxyID { body["proxy_id"] = .integer(Int64(proxyID)) }
                if let deviceID { body["device_profile_id"] = .integer(Int64(deviceID)) }
                let response = try await model.request(path: "/api/accounts/login/start", method: "POST", body: .object(body))
                guard let token = response["login_token"]?.stringValue, !token.isEmpty else { throw APIError.incompatibleResponse }
                self.token = token
            }
        } catch { self.error = error.localizedDescription; uncertain = (error as? APIError)?.isOutcomeUncertain == true }
    }
}

struct AccountManagementPassthroughView: View {
    let id: Int
    @Environment(AppModel.self) private var model
    @State private var features: [JSONValue] = []
    @State private var error: String?
    @State private var result: JSONValue?
    @State private var confirming = false
    @State private var busy = false
    @State private var uncertain = false
    var body: some View {
        List {
            Section {
                ForEach(Array(features.enumerated()), id: \.offset) { _, feature in
                    Text(feature["feature_key"]?.stringValue ?? "插件")
                }.onMove { offsets, destination in features.move(fromOffsets: offsets, toOffset: destination) }
            } header: { Text("从上到下按优先顺序执行") } footer: { Text("仅显示已开启直通的插件。使用右上角编辑调整顺序，再保存。") }
            if let error { NoticeView(title: uncertain ? "结果待确认" : "操作未完成", message: error) }
            if let result { Section("操作结果") { ManagementValueView(value: result) } }
            Button("保存顺序", systemImage: "square.and.arrow.down") { confirming = true }.disabled(features.isEmpty || busy || uncertain)
        }
        .navigationTitle("直通优先顺序").navigationBarTitleDisplayMode(.inline).toolbar { EditButton() }
        .task { await load() }.refreshable { await load() }
        .confirmationDialog("保存此执行顺序？", isPresented: $confirming, titleVisibility: .visible) {
            Button("确认保存") { Task { await save() } }
            Button("取消", role: .cancel) {}
        }
    }
    private func load() async {
        do {
            let result = try await model.request(path: "/api/accounts/\(id)/features")
            features = (result.arrayValue ?? []).filter { $0["enabled"]?.boolValue == true && $0["config"]?["direct_passthrough"]?["enabled"]?.boolValue == true }
                .sorted { ($0["config"]?["direct_passthrough"]?["priority"]?.intValue ?? 100) < ($1["config"]?["direct_passthrough"]?["priority"]?.intValue ?? 100) }
            error = nil; uncertain = false
        } catch { self.error = error.localizedDescription }
    }
    private func save() async {
        guard !busy, !uncertain else { return }
        busy = true; defer { busy = false }
        do {
            result = try await model.request(path: "/api/accounts/\(id)/direct-passthrough/order", method: "PUT",
                body: .object(["order": .array(features.compactMap { $0["feature_key"] })]))
            error = nil
        } catch { self.error = error.localizedDescription; uncertain = (error as? APIError)?.isOutcomeUncertain == true }
    }
}
