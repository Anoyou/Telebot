import SwiftUI
import UniformTypeIdentifiers
import TelePilotCore

struct PushSettingsView: View {
    @Environment(PushNotificationManager.self) private var push
    @Environment(AppModel.self) private var model
    @State private var teamID = ""
    @State private var keyID = ""
    @State private var bundleID = ""
    @State private var environment = "sandbox"
    @State private var enabled = false
    @State private var privateKey: String?
    @State private var pickingKey = false
    @State private var saving = false
    @State private var feedback: String?
    @State private var confirmingSave = false

    var body: some View {
        Form {
            Section("本机通知") {
                LabeledContent("系统授权", value: permissionLabel)
                LabeledContent("设备注册", value: push.isEnabled ? "已请求启用" : "关闭")
                if let message = push.statusMessage { Text(message).font(.footnote).foregroundStyle(.secondary) }
                if let error = push.registrationError { NoticeView(title: "推送尚未就绪", message: error) }
                Button(push.isRegistering ? "正在注册…" : "授权并注册设备") { Task { await push.enable() } }
                    .disabled(push.isRegistering || saving)
                Button("发送测试通知") { Task { await push.test() } }.disabled(!push.isEnabled || saving)
                Button("注销本机通知", role: .destructive) { Task { await push.disable() } }.disabled(!push.isEnabled || saving)
            }
            Section {
                if let error = push.configurationError { NoticeView(title: "无法读取服务器配置", message: error) }
                Toggle("启用 APNs 服务", isOn: $enabled)
                TextField("Team ID", text: $teamID).textInputAutocapitalization(.characters).autocorrectionDisabled()
                TextField("Key ID", text: $keyID).textInputAutocapitalization(.characters).autocorrectionDisabled()
                TextField("Bundle ID", text: $bundleID).textInputAutocapitalization(.never).autocorrectionDisabled()
                Picker("推送环境", selection: $environment) {
                    Text("开发／自签").tag("sandbox")
                    Text("生产分发").tag("production")
                }
                LabeledContent("Auth Key", value: privateKey == nil ? (push.configuration?["has_auth_key"]?.boolValue == true ? "服务器已保存" : "尚未配置") : "已选择新密钥")
                Button("选择 .p8 私钥文件") { pickingKey = true }
                if privateKey != nil { Button("取消替换密钥") { privateKey = nil } }
                Button(saving ? "正在保存…" : "保存服务器配置") { confirmingSave = true }
                    .disabled(saving || push.configuration == nil)
            } header: { Text("服务器推送通道") } footer: {
                Text("需要具有 Push Notifications 权限的签名、Apple Auth Key 和相同的 Bundle ID。仅保存必要信息到自己的服务器；通知不包含密钥或会话正文。")
            }
            if let feedback { Section { Text(feedback).font(.footnote) } }
        }
        .navigationTitle("Apple 推送")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await push.connect(model: model)
            fillConfiguration()
        }
        .onDisappear { privateKey = nil }
        .fileImporter(isPresented: $pickingKey, allowedContentTypes: [.item]) { result in
            do {
                let url = try result.get()
                let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                let handle = try FileHandle(forReadingFrom: url)
                defer { try? handle.close() }
                let data = try handle.read(upToCount: 16_385) ?? Data()
                guard data.count <= 16_384, let text = String(data: data, encoding: .utf8), text.contains("PRIVATE KEY") else {
                    feedback = "请选择有效的 .p8 私钥文件。"; return
                }
                privateKey = text
                feedback = nil
            } catch { feedback = "未能读取文件，请重新选择。" }
        }
        .confirmationDialog("保存推送通道配置？", isPresented: $confirmingSave, titleVisibility: .visible) {
            Button("确认保存") { Task { await save() } }
        } message: { Text("这会修改服务器所有设备使用的 APNs 通道。") }
    }

    private var permissionLabel: String {
        switch push.authorization {
        case .authorized: "已允许"
        case .provisional: "临时允许"
        case .denied: "已拒绝，请在系统设置中修改"
        case .ephemeral: "临时授权"
        default: "尚未授权"
        }
    }

    private func fillConfiguration() {
        guard let config = push.configuration else { return }
        teamID = config["team_id"]?.stringValue ?? ""
        keyID = config["key_id"]?.stringValue ?? ""
        bundleID = config["bundle_id"]?.stringValue ?? Bundle.main.bundleIdentifier ?? ""
        environment = config["environment"]?.stringValue ?? "sandbox"
        enabled = config["enabled"]?.boolValue ?? false
    }

    private func save() async {
        guard !saving else { return }
        saving = true
        defer { saving = false; privateKey = nil }
        var fields: [String: JSONValue] = ["enabled": .bool(enabled), "environment": .string(environment),
                                         "team_id": .string(teamID), "key_id": .string(keyID), "bundle_id": .string(bundleID)]
        if let privateKey { fields["auth_key_p8"] = .string(privateKey) }
        do {
            _ = try await model.request(path: "/api/apns/config", method: "PATCH", body: .object(fields))
            await push.reloadConfiguration()
            feedback = "服务器配置已保存。请注册本机设备并发送测试通知确认。"
        } catch {
            feedback = (error as? APIError)?.localizedDescription ?? "未能确认保存结果，请重新读取后核实。"
        }
    }
}
