import SwiftUI
import TelePilotCore
import WebKit

// 插件自带页面与菜单项（ui.pages / ui.actions，Plugin API 1.4，iOS 0.15.0）。
// 与 Web 同一套 Bridge 协议 v1（schemas/plugin-ui-widgets.json → pages.bridge）：
// - 页面在 WKWebView 里运行：非持久数据存储（拿不到 App 的 Cookie / 登录态）、内容规则屏蔽所有 http(s) 请求，
//   只允许 tp-plugin: 与 about: 导航；页面文件经 tp-plugin:// 自定义协议由 App 代取（/api/plugin-ui/page-assets/{token}/…）。
// - 页面只能经 window.webkit.messageHandlers.telepilot 发请求，App 转发到 POST /api/plugin-ui/pages/{key}/{page}/bridge，
//   服务器按页面声明的 bridge 权限逐次校验；theme.get 由 App 本地回答。
// 插件没有声明页面与菜单时整个分组不显示；旧服务器（接口 404）同样不显示。

struct PluginPageEntriesSection: View {
    let menu: String
    var pluginKey: String?
    var accountID: Int?
    @Environment(AppModel.self) private var model
    @State private var pages: [JSONValue] = []
    @State private var actions: [JSONValue] = []
    @State private var confirming: JSONValue?
    @State private var running: String?
    @State private var resultMessage: String?
    @State private var resultFailed = false
    /// 已读取过入口的 menu/插件/账号组合；没读完前放一行 1pt 透明占位，否则空 Group 在 List 里不渲染，`.task` 永远不会触发。
    @State private var loadedKey: String?

    private var taskKey: String { "\(menu)/\(pluginKey ?? "")/\(accountID ?? 0)" }

    var body: some View {
        Group {
            if loadedKey != taskKey {
                Section {
                    Color.clear.frame(height: 1).listRowBackground(Color.clear).accessibilityHidden(true)
                }
                .listSectionSpacing(.compact)
                .task(id: taskKey) {
                    let key = taskKey
                    await load()
                    if !Task.isCancelled { loadedKey = key }
                }
            } else if !pages.isEmpty || !actions.isEmpty {
                Section {
                    ForEach(Array(pages.enumerated()), id: \.offset) { _, page in
                        NavigationLink { PluginPageView(entry: page, accountID: accountID) } label: {
                            entryLabel(title: page["title"]?.stringValue ?? "插件页面", subtitle: subtitle(page), symbol: "macwindow")
                        }
                        .accessibilityIdentifier("plugin.page.\(page["page_id"]?.stringValue ?? "")")
                    }
                    ForEach(Array(actions.enumerated()), id: \.offset) { _, action in actionRow(action) }
                    if let resultMessage {
                        Label(resultMessage, systemImage: resultFailed ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                            .font(.footnote)
                            .foregroundStyle(resultFailed ? Color.orange : Color.green)
                            .accessibilityIdentifier("plugin.menuAction.result")
                    }
                } header: { Text("插件页面与操作") } footer: {
                    Text("由插件提供。页面在隔离环境中运行，只能读取它声明的数据；操作不会自动重试。")
                }
            }
        }
    }

    private func subtitle(_ item: JSONValue) -> String? {
        let parts = [pluginKey == nil ? item["plugin_name"]?.stringValue : nil, item["subtitle"]?.stringValue].compactMap { $0 }.filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private func entryLabel(title: String, subtitle: String?, symbol: String, destructive: Bool = false) -> some View {
        Label {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).foregroundStyle(destructive ? Color.red : Color.primary)
                if let subtitle { Text(subtitle).font(.caption).foregroundStyle(.secondary) }
            }
        } icon: {
            Image(systemName: symbol).foregroundStyle(destructive ? Color.red : Color.accentColor)
        }
    }

    @ViewBuilder private func actionRow(_ action: JSONValue) -> some View {
        let id = action["action_id"]?.stringValue ?? ""
        let title = action["title"]?.stringValue ?? id
        let destructive = action["destructive"]?.boolValue == true
        if let pageID = action["page_id"]?.stringValue, !pageID.isEmpty {
            let page = pages.first { $0["plugin_key"] == action["plugin_key"] && $0["page_id"]?.stringValue == pageID }
            let entry: JSONValue = page ?? ["plugin_key": action["plugin_key"] ?? .null, "plugin_name": action["plugin_name"] ?? .null,
                                            "page_id": .string(pageID), "title": .string(title)]
            NavigationLink { PluginPageView(entry: entry, accountID: accountID) } label: {
                entryLabel(title: title, subtitle: subtitle(action), symbol: "macwindow")
            }
            .accessibilityIdentifier("plugin.menuAction.\(id)")
        } else {
            let needsConfirm = destructive || action["confirm"]?.stringValue != nil
            Button {
                if needsConfirm { confirming = action } else { Task { await run(action) } }
            } label: {
                HStack {
                    entryLabel(title: title, subtitle: subtitle(action), symbol: destructive ? "exclamationmark.triangle" : "play.circle", destructive: destructive)
                    Spacer()
                    if running == id { ProgressView() }
                }
                .frame(minHeight: 44)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .disabled(running != nil)
            .accessibilityIdentifier("plugin.menuAction.\(id)")
            .confirmationDialog(title, isPresented: Binding(
                get: { confirming?["action_id"] == action["action_id"] && confirming?["plugin_key"] == action["plugin_key"] },
                set: { if !$0 { confirming = nil } }
            ), titleVisibility: .visible) {
                Button("确定", role: destructive ? .destructive : nil) { Task { await run(action) } }
                Button("取消", role: .cancel) {}
            } message: {
                Text(action["confirm"]?.stringValue ?? "确定执行这个插件操作吗？不会自动重试。")
            }
        }
    }

    private func load() async {
        var path = "/api/plugin-ui/entries?menu=\(menu)"
        if let pluginKey { path += "&plugin_key=" + (pluginKey.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? pluginKey) }
        if let accountID { path += "&account_id=\(accountID)" }
        // 旧服务器没有这个接口或读取失败：不显示入口（不打扰没有声明的插件）。
        guard let value = try? await model.request(path: path) else {
            pages = []
            actions = []
            return
        }
        pages = value["pages"]?.arrayValue ?? []
        actions = value["actions"]?.arrayValue ?? []
    }

    private func run(_ action: JSONValue) async {
        guard running == nil, let key = action["plugin_key"]?.stringValue, let id = action["action_id"]?.stringValue else { return }
        running = id
        defer { running = nil }
        let path = "/api/plugin-ui/actions/\(ManagementText.pathSegment(key))/\(ManagementText.pathSegment(id))"
        var body: [String: JSONValue] = ["menu": action["menu"] ?? .string(menu)]
        body["account_id"] = accountID.map { JSONValue.integer(Int64($0)) } ?? JSONValue.null
        do {
            let result = try await model.request(path: path, method: "POST", body: .object(body))
            resultFailed = result["ok"]?.boolValue == false
            resultMessage = result["message"]?.stringValue ?? (resultFailed ? "插件没有完成这个操作" : "已完成")
        } catch {
            resultFailed = true
            resultMessage = "操作失败或结果未知：\(error.localizedDescription)。不会自动重试。"
        }
    }
}

/// 一个插件页面：先换取 1 小时有效的页面 token，再在隔离的 WKWebView 里加载。
struct PluginPageView: View {
    let entry: JSONValue
    var accountID: Int?
    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var colorScheme
    @State private var session: JSONValue?
    @State private var error: String?

    private var pluginKey: String { entry["plugin_key"]?.stringValue ?? "" }
    private var pageID: String { entry["page_id"]?.stringValue ?? "" }

    var body: some View {
        Group {
            if let session, let token = session["token"]?.stringValue, let entryPath = session["entry"]?.stringValue {
                PluginPageWebView(model: model, pluginKey: pluginKey, pageID: pageID, token: token, entryPath: entryPath,
                                  accountID: accountID, scheme: colorScheme == .dark ? "dark" : "light",
                                  csp: session["csp"]?.stringValue ?? "default-src 'none'")
                    .ignoresSafeArea(edges: .bottom)
                    .accessibilityIdentifier("plugin.page.webView")
            } else if let error {
                ContentUnavailableView {
                    Label("页面打开失败", systemImage: "exclamationmark.triangle")
                } description: {
                    Text(error)
                } actions: {
                    Button("重试", systemImage: "arrow.clockwise") { Task { await open() } }
                }
            } else {
                ProgressView("正在打开页面…")
            }
        }
        .navigationTitle(entry["title"]?.stringValue ?? "插件页面")
        .navigationBarTitleDisplayMode(.inline)
        .task { await open() }
    }

    private func open() async {
        error = nil
        let path = "/api/plugin-ui/pages/\(ManagementText.pathSegment(pluginKey))/\(ManagementText.pathSegment(pageID))/session"
        do {
            let accountValue: JSONValue = accountID.map { JSONValue.integer(Int64($0)) } ?? JSONValue.null
            session = try await model.request(path: path, method: "POST", body: .object(["account_id": accountValue]))
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// tp-plugin://page/<相对路径> → GET /api/plugin-ui/page-assets/{token}/<相对路径>（经 App 的连接取回，页面本身不能联网）。
@MainActor private final class PluginPageSchemeHandler: NSObject, @preconcurrency WKURLSchemeHandler {
    static let scheme = "tp-plugin"
    let fetch: @MainActor (String) async throws -> Data
    let csp: String
    private var stopped = Set<ObjectIdentifier>()

    init(csp: String, fetch: @escaping @MainActor (String) async throws -> Data) {
        self.csp = csp
        self.fetch = fetch
    }

    func webView(_ webView: WKWebView, start urlSchemeTask: any WKURLSchemeTask) {
        let key = ObjectIdentifier(urlSchemeTask)
        guard let url = urlSchemeTask.request.url, url.host == "page" else {
            urlSchemeTask.didFailWithError(URLError(.unsupportedURL))
            return
        }
        let relative = url.path.split(separator: "/").map(String.init).filter { !$0.isEmpty && $0 != "." && $0 != ".." }
        let path = relative.map { $0.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? $0 }.joined(separator: "/")
        let fetch = fetch
        Task { @MainActor in
            do {
                let data = try await fetch(path)
                guard !stopped.contains(key) else { return }
                let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1",
                                               headerFields: ["Content-Type": Self.mimeType(for: url.pathExtension), "Cache-Control": "no-store",
                                                              "Content-Security-Policy": csp])!
                urlSchemeTask.didReceive(response)
                urlSchemeTask.didReceive(data)
                urlSchemeTask.didFinish()
            } catch {
                guard !stopped.contains(key) else { return }
                urlSchemeTask.didFailWithError(error)
            }
            stopped.remove(key)
        }
    }

    func webView(_ webView: WKWebView, stop urlSchemeTask: any WKURLSchemeTask) {
        stopped.insert(ObjectIdentifier(urlSchemeTask))
    }

    static func mimeType(for ext: String) -> String {
        switch ext.lowercased() {
        case "html", "htm": "text/html; charset=utf-8"
        case "css": "text/css; charset=utf-8"
        case "js", "mjs": "text/javascript; charset=utf-8"
        case "json": "application/json"
        case "svg": "image/svg+xml"
        case "png": "image/png"
        case "jpg", "jpeg": "image/jpeg"
        case "gif": "image/gif"
        case "webp": "image/webp"
        case "woff2": "font/woff2"
        case "woff": "font/woff"
        case "txt", "md": "text/plain; charset=utf-8"
        default: "application/octet-stream"
        }
    }
}

/// 不强引用宿主（WKUserContentController 会强引用 handler）。
@MainActor private final class PluginBridgeProxy: NSObject, @preconcurrency WKScriptMessageHandler {
    weak var target: PluginPageWebView.Coordinator?
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.receive(message)
    }
}

struct PluginPageWebView: UIViewRepresentable {
    let model: AppModel
    let pluginKey: String
    let pageID: String
    let token: String
    let entryPath: String
    let accountID: Int?
    let scheme: String
    /// 服务器下发的页面 CSP（文件响应头里再带一次；HTML 里服务器已注入同样的 meta）
    let csp: String

    /// 屏蔽页面发起的一切 http(s) 请求（CSP 之外的第二道防线）。
    static let blockNetworkRules = #"[{"trigger":{"url-filter":"^https?://"},"action":{"type":"block"}}]"#

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.limitsNavigationsToAppBoundDomains = false
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        let tokenValue = token
        let model = model
        let handler = PluginPageSchemeHandler(csp: csp) { path in
            try await model.download(path: "/api/plugin-ui/page-assets/\(tokenValue)/\(path)")
        }
        configuration.setURLSchemeHandler(handler, forURLScheme: PluginPageSchemeHandler.scheme)
        let proxy = PluginBridgeProxy()
        proxy.target = context.coordinator
        configuration.userContentController.add(proxy, name: "telepilot")
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.isOpaque = false
        webView.backgroundColor = .systemBackground
        webView.allowsLinkPreview = false
        webView.isInspectable = false
        context.coordinator.webView = webView
        context.coordinator.lastScheme = scheme
        let entry = entryPath.split(separator: "/").map(String.init).filter { $0 != ".." }.joined(separator: "/")
        let url = URL(string: "\(PluginPageSchemeHandler.scheme)://page/\(entry)")
        // 内容规则编译完成（或失败）后才加载页面；回调在主线程。
        nonisolated(unsafe) let target = webView
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "telepilot-plugin-page-block-network",
                                                                encodedContentRuleList: Self.blockNetworkRules) { list, _ in
            MainActor.assumeIsolated {
                if let list { target.configuration.userContentController.add(list) }
                if let url { target.load(URLRequest(url: url)) }
            }
        }
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        context.coordinator.parent = self
        if context.coordinator.lastScheme != scheme {
            context.coordinator.lastScheme = scheme
            context.coordinator.send(["tp": "telepilot", "v": 1, "event": "theme", "data": ["scheme": .string(scheme)]])
        }
    }

    static func dismantleUIView(_ webView: WKWebView, coordinator: Coordinator) {
        webView.configuration.userContentController.removeScriptMessageHandler(forName: "telepilot")
        webView.stopLoading()
    }

    @MainActor final class Coordinator: NSObject, @preconcurrency WKNavigationDelegate, @preconcurrency WKUIDelegate {
        var parent: PluginPageWebView
        weak var webView: WKWebView?
        var lastScheme = ""

        init(parent: PluginPageWebView) { self.parent = parent }

        func receive(_ message: WKScriptMessage) {
            // 只接受本页面主框架的 Bridge 消息
            guard message.frameInfo.isMainFrame,
                  let request = PluginPageBridge.parse(PluginPageBridge.decode(any: message.body)) else { return }
            if request.method == "theme.get" {
                send(PluginPageBridge.ok(request.id, ["scheme": .string(parent.scheme)]))
                return
            }
            let parent = parent
            Task { @MainActor in
                let path = "/api/plugin-ui/pages/\(ManagementText.pathSegment(parent.pluginKey))/\(ManagementText.pathSegment(parent.pageID))/bridge"
                var body: [String: JSONValue] = ["method": .string(request.method), "params": request.params]
                body["account_id"] = parent.accountID.map { JSONValue.integer(Int64($0)) } ?? JSONValue.null
                do {
                    let response = try await parent.model.request(path: path, method: "POST", body: .object(body))
                    if response["ok"]?.boolValue == true {
                        send(PluginPageBridge.ok(request.id, response["result"] ?? .null))
                    } else {
                        send(PluginPageBridge.error(request.id, code: response["error"]?["code"]?.stringValue ?? "BRIDGE_FAILED",
                                                    message: response["error"]?["message"]?.stringValue ?? "请求失败"))
                    }
                } catch {
                    send(PluginPageBridge.error(request.id, code: "BRIDGE_HOST_FAILED", message: error.localizedDescription))
                }
            }
        }

        func send(_ message: JSONValue) {
            guard let webView, let data = try? message.data(), let json = String(data: data, encoding: .utf8) else { return }
            webView.evaluateJavaScript("window.__telepilotReceive && window.__telepilotReceive(\(json));", completionHandler: nil)
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction) async -> WKNavigationActionPolicy {
            let scheme = navigationAction.request.url?.scheme?.lowercased() ?? ""
            return PluginPageBridge.allowsNavigation(scheme: scheme) ? .allow : .cancel
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            send(["tp": "telepilot", "v": 1, "event": "theme", "data": ["scheme": .string(parent.scheme)]])
        }

        // 页面不能开新窗口
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? { nil }
    }
}
