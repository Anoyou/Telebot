import SwiftUI
import UniformTypeIdentifiers
import TelePilotCore

struct ManagementFile: FileDocument {
    static var readableContentTypes: [UTType] { [.json, .data] }
    var data: Data
    init(data: Data) { self.data = data }
    init(configuration: ReadConfiguration) throws { data = configuration.file.regularFileContents ?? Data() }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper { FileWrapper(regularFileWithContents: data) }
}

struct FileManagementView: View {
    enum Mode: Hashable { case backup, account(Int), plugin }
    let mode: Mode
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var picking = false
    @State private var exporting = false
    @State private var selectedData: Data?
    @State private var filename = ""
    @State private var exportFile = ManagementFile(data: Data())
    @State private var signature = ""
    @State private var applyConflicts = false
    @State private var confirmChatConflicts = false
    @State private var includeSensitive = false
    @State private var password = ""
    @State private var totp = ""
    @State private var selectedCategories: Set<String> = []
    @State private var preview: JSONValue?
    @State private var result: JSONValue?
    @State private var error: String?
    @State private var busy = false
    @State private var uncertain = false
    @State private var confirming = false
    @State private var selectingSignature = false
    @State private var signatureFilename = ""

    private let categories = [
        ("account_settings", "账号设置"), ("feature_config", "账号插件配置"), ("plugin_global_configs", "插件全局配置"),
        ("command_templates", "指令模板"), ("llm_providers", "AI 供应商"), ("system_settings", "系统设置"),
        ("proxies", "代理"), ("device_profiles", "设备配置"), ("rate_limit_templates", "限流模板"),
        ("rate_limit_rules", "限流规则"), ("humanize_configs", "消息频率"), ("ignored_peers", "会话白名单"), ("notify_bots", "通知 Bot")
    ]
    private var title: String {
        switch mode { case .backup: "配置备份与恢复"; case .account: "账号配置迁移"; case .plugin: "安装插件包" }
    }
    var body: some View {
        Form {
            if mode != .plugin { exportSection }
            Section(mode == .plugin ? "插件文件" : "导入文件") {
                Button("选择文件", systemImage: "folder") { selectingSignature = false; picking = true }.disabled(busy)
                if !filename.isEmpty { LabeledContent("已选择", value: filename) }
                if let selectedData { LabeledContent("文件大小", value: ByteCountFormatter.string(fromByteCount: Int64(selectedData.count), countStyle: .file)) }
                if case .account = mode {
                    Toggle("覆盖可解决的配置冲突", isOn: $applyConflicts).disabled(busy)
                    Toggle("已确认会话 ID 冲突", isOn: $confirmChatConflicts).disabled(busy)
                }
                if mode == .plugin {
                    TextField("可选签名（粘贴签名文本）", text: $signature, axis: .vertical).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("选择签名文件", systemImage: "signature") { selectingSignature = true; picking = true }.disabled(busy)
                    if !signatureFilename.isEmpty { LabeledContent("签名文件", value: signatureFilename) }
                    Text("安装包会由服务器验证，签名和权限检查结果以服务器为准。").font(.footnote).foregroundStyle(.secondary)
                }
                if mode != .plugin {
                    Button("预览导入影响", systemImage: "eye") { Task { await makePreview() } }.disabled(selectedData == nil || busy || uncertain)
                }
            }
            if let preview { Section("导入预览") { ManagementValueView(value: preview) } }
            if selectedData != nil, mode == .plugin || preview?["preview_signature"]?.stringValue != nil {
                Section {
                    Button(mode == .plugin ? "安装插件" : "确认导入", systemImage: mode == .plugin ? "square.and.arrow.down.on.square" : "tray.and.arrow.down",
                           role: mode == .plugin ? nil : .destructive) { confirming = true }
                        .disabled(busy || uncertain)
                        .accessibilityIdentifier("management.file.confirm")
                        .confirmationDialog(mode == .plugin ? "安装这个插件？" : "确认将预览配置导入服务器？", isPresented: $confirming, titleVisibility: .visible) {
                            Button(mode == .plugin ? "确认安装" : "确认导入", role: mode == .plugin ? nil : .destructive) { Task { await importFile() } }
                            Button("取消", role: .cancel) {}
                        }
                } footer: { Text(mode == .plugin ? "插件会在服务器执行代码，请确认来源和权限。" : "将按以上预览写入服务器。文件和冲突选项改变后必须重新预览。") }
            }
            if busy { Section { ProgressView("正在处理…") } }
            if let error { Section { NoticeView(title: uncertain ? "结果待确认" : "操作未完成", message: error) } }
            if let result { Section("操作结果") { ManagementValueView(value: result) } }
        }
        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
        .fileImporter(isPresented: $picking, allowedContentTypes: selectingSignature ? [.data] : mode == .plugin ? [.zip, .data] : [.json, .data]) { result in selectFile(result) }
        .fileExporter(isPresented: $exporting, document: exportFile, contentType: .json, defaultFilename: "TelePilot-配置备份") { result in
            if case .failure(let failure) = result { error = failure.localizedDescription }
        }
        .onChange(of: applyConflicts) { _, _ in preview = nil }
        .onChange(of: confirmChatConflicts) { _, _ in preview = nil }
        .onChange(of: scenePhase) { _, phase in if phase != .active { password = ""; totp = "" } }
    }
    @ViewBuilder private var exportSection: some View {
        Section("导出备份") {
            if mode == .backup {
                ForEach(categories, id: \.0) { key, label in
                    Toggle(label, isOn: Binding(get: { selectedCategories.contains(key) }, set: { selected in
                        if selected { selectedCategories.insert(key) } else { selectedCategories.remove(key) }
                    }))
                }
                Toggle(isOn: $includeSensitive) { Label("包含敏感配置", systemImage: "lock") }
                if includeSensitive {
                    SecureField("当前管理员密码", text: $password).privacySensitive()
                    SecureField("验证器验证码（如已启用）", text: $totp).keyboardType(.numberPad).privacySensitive()
                }
            }
            Button("导出到文件", systemImage: "square.and.arrow.up") { Task { await export() } }
                .disabled(busy || (mode == .backup && selectedCategories.isEmpty))
        }
    }
    private func selectFile(_ result: Result<URL, Error>) {
        do {
            let url = try result.get()
            let access = url.startAccessingSecurityScopedResource()
            defer { if access { url.stopAccessingSecurityScopedResource() } }
            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            if selectingSignature {
                guard size <= 64 * 1024 else { throw FileManagementError.tooLarge }
                let data = try Data(contentsOf: url)
                guard data.count <= 64 * 1024 else { throw FileManagementError.tooLarge }
                signature = data.base64EncodedString(); signatureFilename = url.lastPathComponent
                return
            }
            let limit = mode == .plugin ? 64 * 1024 * 1024 : (mode == .backup ? 32 * 1024 * 1024 : 1_048_576)
            guard size <= limit else { throw FileManagementError.tooLarge }
            let data = try Data(contentsOf: url)
            guard data.count <= limit else { throw FileManagementError.tooLarge }
            selectedData = data; filename = url.lastPathComponent
            preview = nil; self.result = nil; error = nil; uncertain = false
        } catch { self.error = error.localizedDescription }
    }
    private var uploadFields: [String: String] {
        if case .account = mode { return ["apply_conflicts": applyConflicts ? "true" : "false", "confirm_chat_id_conflicts": confirmChatConflicts ? "true" : "false"] }
        return [:]
    }
    private func makePreview() async {
        guard let data = selectedData, !busy else { return }
        busy = true; error = nil; preview = nil
        defer { busy = false }
        do {
            let path: String
            if case .account(let id) = mode { path = "/api/accounts/\(id)/config-bundle/dry-run" }
            else { path = "/api/system/import-config/preview" }
            preview = try await model.upload(path: path, file: data, filename: filename, fields: uploadFields)
            if preview?["preview_signature"]?.stringValue?.isEmpty != false { throw APIError.incompatibleResponse }
        } catch { self.error = error.localizedDescription; preview = nil }
    }
    private func importFile() async {
        guard let data = selectedData, !busy, !uncertain else { return }
        var fields = uploadFields
        let path: String
        switch mode {
        case .plugin:
            path = "/api/plugins/install/upload"
            if !signature.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { fields["signature"] = signature }
        case .backup:
            guard let signature = preview?["preview_signature"]?.stringValue, !signature.isEmpty else { return }
            fields["preview_signature"] = signature; path = "/api/system/import-config"
        case .account(let id):
            guard let signature = preview?["preview_signature"]?.stringValue, !signature.isEmpty else { return }
            fields["preview_signature"] = signature; path = "/api/accounts/\(id)/config-bundle/confirm"
        }
        busy = true; error = nil
        defer { busy = false }
        do {
            result = try await model.upload(path: path, file: data, filename: filename, fields: fields)
            selectedData = nil; preview = nil
        } catch {
            self.error = error.localizedDescription; uncertain = (error as? APIError)?.isOutcomeUncertain == true
            if mode != .plugin { preview = nil }
        }
    }
    private func export() async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false; password = ""; totp = "" }
        do {
            let data: Data
            if case .account(let id) = mode { data = try await model.download(path: "/api/accounts/\(id)/config-bundle/export") }
            else {
                var body: [String: JSONValue] = ["categories": .array(selectedCategories.sorted().map(JSONValue.string)), "include_sensitive": .bool(includeSensitive)]
                if includeSensitive { body["password"] = .string(password); if !totp.isEmpty { body["totp_code"] = .string(totp) } }
                data = try await model.download(path: "/api/system/export-config", method: "POST", body: .object(body))
            }
            exportFile = ManagementFile(data: data); exporting = true
        } catch { self.error = error.localizedDescription }
    }
}

private enum FileManagementError: LocalizedError {
    case tooLarge
    var errorDescription: String? { "文件超过此导入流程允许的大小，请拆分配置或选择更小的文件。" }
}
