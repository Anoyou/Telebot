import SwiftUI
import TelePilotCore
import UniformTypeIdentifiers

// MARK: - Agent Skills（助手页右上角「Skills」按钮；AI → 配置 → Skills）
//
// 与 Web 助手页顶部「Skills」按钮对应：列表、详情、编辑 SKILL.md、内置 skill 的差异 / 恢复默认 / 保留我的修改、
// 安装（GitHub 链接、zip 链接、粘贴、选择 .zip / .md 文件）与卸载。写操作不自动重试；结果不确定时如实提示。

enum AgentSkillAPI {
    static let root = "/api/agent-skills"
    static func path(_ name: String) -> String { root + "/" + ManagementText.pathSegment(name) }
    static let maxArchiveBytes = 10 * 1024 * 1024
    static let maxSkillMarkdownBytes = 256 * 1024

    /// 服务器错误码 → 中文说明；未知错误沿用通用文案。
    static func message(_ error: Error) -> String {
        guard case .server(let status, let serverCode)? = error as? APIError, let code = serverCode else { return error.localizedDescription }
        switch code {
        case "SKILL_NOT_FOUND": return "找不到这个 skill，可能已被卸载。请返回列表刷新。"
        case "FILE_NOT_FOUND": return "skill 里没有这个文件。"
        case "SKILL_EXISTS": return "已安装同名 skill。如需替换内容请在详情页“从源地址更新”或编辑，或先卸载再安装。"
        case "NAME_CHANGED": return "SKILL.md 里的 name 必须保持不变；如需改名请安装为新的 skill。"
        case "RESERVED_NAME": return "这个名称与 TelePilot 内置 skill 重名，请修改 SKILL.md 的 name 后再安装。"
        case "BUILTIN_SKILL": return "TelePilot 内置 skill 不能卸载，只能停用。"
        case "BUNDLED_SKILL": return "插件内置的 skill 不能卸载，只能停用；编辑过的内容可以“恢复默认”。"
        case "BUILTIN_PLUGIN_UNSUPPORTED": return "TelePilot 内置 skill 只用于系统 Agent，不能提供给插件。"
        case "NOT_EDITABLE": return "来自 GitHub / zip 链接的 skill 会在更新时被源地址覆盖，不能直接编辑。"
        case "NOT_BUNDLED": return "只有插件内置的 skill 才有默认版本可以对比或恢复。"
        case "NOTHING_TO_UPDATE": return "这个 skill 没有可重新拉取的源地址。"
        case "BAD_SOURCE_URL": return "链接不可用：请填写 https 开头的 GitHub 仓库 / 目录链接或 zip 下载链接。"
        case "MISSING_URL": return "请填写 GitHub 或 zip 链接。"
        case "MISSING_ARCHIVE": return "请先选择 zip 文件。"
        case "DOWNLOAD_FAILED": return "服务器下载失败，请检查链接是否公开可访问，或稍后再试。"
        case "DOWNLOAD_TIMEOUT": return "服务器下载超时，请稍后再试或改为上传 zip。"
        case "ARCHIVE_TOO_LARGE": return "zip 超过 10 MB 上限。"
        case "SKILL_TOO_LARGE": return "skill 内容超过大小上限（单文件 256 KB、合计 1 MB）。"
        case "BAD_ARCHIVE": return "zip 文件无法解压，请确认文件完整。"
        case "UNSAFE_PATH", "DUPLICATE_PATH": return "zip 中包含不安全或重复的路径，已拒绝安装。"
        case "MISSING_SKILL_MD": return "没有找到 SKILL.md。仓库里有多个 skill 时请在“子目录”里指定。"
        case "MULTIPLE_SKILLS": return "找到多个 skill，请在“子目录”里填写要安装的那一个。"
        case "EMPTY_SKILL_MD": return "SKILL.md 是空的。"
        case "BAD_FRONT_MATTER": return "SKILL.md 需要以 --- 包裹的 front matter 开头，并声明 name 与 description。"
        case "BAD_NAME": return "name 不合法：只能用小写字母、数字和连字符，最长 64 个字符。"
        case "BAD_DESCRIPTION": return "front matter 缺少 description，或 description 过长。"
        case "BAD_ENCODING": return "SKILL.md 必须是 UTF-8 文本。"
        default: return status == 429 ? "请求过于频繁，请稍后重试。" : error.localizedDescription
        }
    }

    static func isUncertain(_ error: Error) -> Bool { (error as? APIError)?.isOutcomeUncertain == true }
}

/// 列表与详情共用的 skill 摘要（字段与服务器 `AgentSkillOut` 对应）。
struct AgentSkillItem: Identifiable, Hashable {
    let raw: JSONValue
    var id: String { name }
    var name: String { raw["name"]?.stringValue ?? "" }
    var description: String { raw["description"]?.stringValue ?? "" }
    var version: String? { raw["version"]?.stringValue }
    var builtin: Bool { raw["builtin"]?.boolValue == true }
    var bundled: Bool { raw["bundled"]?.boolValue == true }
    var ownerPlugin: String? { raw["owner_plugin"]?.stringValue }
    var editable: Bool { raw["editable"]?.boolValue == true }
    var uninstallable: Bool { raw["uninstallable"]?.boolValue ?? !(builtin || bundled) }
    var userModified: Bool { raw["user_modified"]?.boolValue == true }
    var upstreamChanged: Bool { raw["upstream_changed"]?.boolValue == true }
    var upstreamVersion: String? { raw["upstream_version"]?.stringValue }
    var sourceType: String { raw["source_type"]?.stringValue ?? "" }
    var sourceURL: String? { raw["source_url"]?.stringValue }
    var sourceRef: String? { raw["source_ref"]?.stringValue }
    var sourcePath: String? { raw["source_path"]?.stringValue }
    var sourceCommit: String? { raw["source_commit"]?.stringValue }
    var enabledAgent: Bool { raw["enabled_agent"]?.boolValue == true }
    var enabledPlugins: Bool { raw["enabled_plugins"]?.boolValue == true }
    var pluginSupported: Bool { raw["plugin_supported"]?.boolValue == true }
    var updatable: Bool { raw["updatable"]?.boolValue == true }
    var fileCount: Int { raw["file_count"]?.intValue ?? 0 }
    var totalBytes: Int { raw["total_bytes"]?.intValue ?? 0 }
    var skippedFiles: [JSONValue] { raw["skipped_files"]?.arrayValue ?? [] }
    var installedAt: String? { raw["installed_at"]?.stringValue }
    var updatedAt: String? { raw["updated_at"]?.stringValue }

    var sourceLabel: String {
        switch sourceType {
        case "builtin": "TelePilot 内置"
        case "plugin_bundled": "插件内置" + (ownerPlugin.map { " · \($0)" } ?? "")
        case "github": "GitHub"
        case "zip_url": "zip 链接"
        case "zip_upload": "上传 zip"
        case "md_upload": "上传 SKILL.md"
        case "paste": "粘贴"
        default: sourceType
        }
    }

    var symbol: String {
        if builtin { return "shippingbox" }
        if bundled { return "puzzlepiece.extension" }
        return sourceType == "github" ? "chevron.left.forwardslash.chevron.right" : "doc.text"
    }

    /// 启用状态的文字说明（不只靠颜色）。
    var enabledSummary: String {
        var parts: [String] = []
        if enabledAgent { parts.append("系统 Agent") }
        if enabledPlugins, pluginSupported { parts.append("插件") }
        return parts.isEmpty ? "已停用" : "启用于：" + parts.joined(separator: "、")
    }

    static func sizeText(_ bytes: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }
}

// MARK: - 列表

/// 从助手页工具栏「Skills」按钮打开时使用的 sheet 包装。
struct AgentSkillsSheet: View {
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            AgentSkillsView()
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }
    }
}

struct AgentSkillsView: View {
    @Environment(AppModel.self) private var model
    @State private var items: [AgentSkillItem] = []
    @State private var limits: JSONValue?
    @State private var loading = false
    @State private var loaded = false
    @State private var error: String?
    @State private var installing = false

    private var installed: [AgentSkillItem] { items.filter { !$0.builtin && !$0.bundled } }
    private var bundled: [AgentSkillItem] { items.filter(\.bundled) }
    private var builtin: [AgentSkillItem] { items.filter(\.builtin) }

    var body: some View {
        List {
            Section {
                Text("Skill 是一份 SKILL.md 说明书（可附参考文件）。系统 Agent 按需加载对它启用的 skill；插件只能读取“对插件启用”的 skill 文本，其中的脚本永远不会被执行。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if let error {
                Section {
                    NoticeView(title: loaded ? "刷新失败，下面是上次读取的列表" : "读取失败", message: error)
                    Button("重新读取", systemImage: "arrow.clockwise") { Task { await load() } }
                }
            }
            if loading && !loaded {
                Section { ProgressView("正在读取 Skills…") }
            } else if loaded {
                Section {
                    if installed.isEmpty {
                        ContentUnavailableView {
                            Label("还没有安装 skill", systemImage: "wand.and.stars")
                        } description: {
                            Text("可以从 GitHub 链接、zip 链接安装，或粘贴 / 选择 SKILL.md。")
                        } actions: {
                            Button("安装 skill", systemImage: "plus") { installing = true }
                        }
                    }
                    ForEach(installed) { item in row(item) }
                } header: { Text("已安装") }
                if !bundled.isEmpty {
                    Section {
                        ForEach(bundled) { item in row(item) }
                    } header: { Text("插件内置") } footer: {
                        Text("随插件分发，可编辑和停用；插件升级不会覆盖你的修改，有新默认版本时会标出“内置有新版本”。")
                    }
                }
                if !builtin.isEmpty {
                    Section {
                        ForEach(builtin) { item in row(item) }
                    } header: { Text("TelePilot 内置") } footer: { Text("内置领域技能只用于系统 Agent，可停用，不能卸载或编辑。") }
                }
                if let limits {
                    Section {
                        Text("单个 skill 最多 \(limits["max_files"]?.intValue ?? 64) 个文件；单文件 \(AgentSkillItem.sizeText(limits["max_file_bytes"]?.intValue ?? 0))，合计 \(AgentSkillItem.sizeText(limits["max_skill_bytes"]?.intValue ?? 0))；zip 不超过 \(AgentSkillItem.sizeText(limits["max_archive_bytes"]?.intValue ?? 0))。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("Skills").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button("安装 skill", systemImage: "plus") { installing = true }
                    .accessibilityIdentifier("skills.install")
            }
        }
        .refreshable { await load() }
        .task { if !loaded { await load() } }
        .sheet(isPresented: $installing) {
            AgentSkillInstallView { Task { await load() } }
        }
    }

    private func row(_ item: AgentSkillItem) -> some View {
        NavigationLink {
            AgentSkillDetailView(name: item.name, initial: item) { Task { await load() } }
        } label: {
            AgentSkillRow(item: item)
        }
        .accessibilityIdentifier("skills.row.\(item.name)")
    }

    private func load() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            let value = try await model.request(path: AgentSkillAPI.root)
            items = (value["items"]?.arrayValue ?? []).map(AgentSkillItem.init(raw:)).filter { !$0.name.isEmpty }
            limits = value["limits"]
            loaded = true
            error = nil
        } catch {
            self.error = AgentSkillAPI.message(error)
        }
    }
}

struct AgentSkillRow: View {
    let item: AgentSkillItem
    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(item.name).font(.body.monospaced()).lineLimit(1)
                    if let version = item.version { Text(version).font(.caption).foregroundStyle(.secondary) }
                }
                if !item.description.isEmpty {
                    Text(item.description).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                HStack(spacing: 8) {
                    Label(item.enabledSummary, systemImage: item.enabledAgent || item.enabledPlugins ? "checkmark.circle" : "pause.circle")
                    if item.upstreamChanged {
                        Label("内置有新版本", systemImage: "sparkles").foregroundStyle(.orange)
                    } else if item.userModified {
                        Label("已修改", systemImage: "pencil.circle")
                    }
                }
                .font(.caption2).foregroundStyle(.secondary)
            }
        } icon: { Image(systemName: item.symbol) }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - 详情

struct AgentSkillDetailView: View {
    let name: String
    var initial: AgentSkillItem?
    var onChange: () -> Void = {}
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var detail: JSONValue?
    @State private var error: String?
    @State private var actionError: String?
    @State private var uncertain = false
    @State private var busy: String?
    @State private var notice: String?
    @State private var editing = false
    @State private var confirmingUpdate = false
    @State private var confirmingRestore = false
    @State private var confirmingUninstall = false

    private var item: AgentSkillItem? { detail.map(AgentSkillItem.init(raw:)) ?? initial }
    private var skillMarkdown: String { detail?["skill_md"]?.stringValue ?? "" }
    private var files: [JSONValue] { detail?["files"]?.arrayValue ?? [] }

    var body: some View {
        List {
            if let error, detail == nil {
                Section {
                    NoticeView(title: "读取失败", message: error)
                    Button("重新读取", systemImage: "arrow.clockwise") { Task { await load() } }
                }
            }
            if let item {
                summarySection(item)
                if let notice { Section { Label(notice, systemImage: "checkmark.circle.fill").foregroundStyle(.green) } }
                if let actionError { Section { NoticeView(title: uncertain ? "结果待确认" : "操作未完成", message: actionError) } }
                enableSection(item)
                if item.bundled && (item.upstreamChanged || item.userModified) { upstreamSection(item) }
                contentSection(item)
                if !files.isEmpty { filesSection }
                if !item.skippedFiles.isEmpty { skippedSection(item) }
                manageSection(item)
            } else if error == nil {
                Section { ProgressView("正在读取 skill…") }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle(name).navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task { if detail == nil { await load() } }
        .sheet(isPresented: $editing) {
            AgentSkillEditorView(name: name, original: skillMarkdown) { updated in
                detail = nil
                notice = "已保存 SKILL.md。"
                await load()
                onChange()
                _ = updated
            }
        }
    }

    private func summarySection(_ item: AgentSkillItem) -> some View {
        Section {
            if !item.description.isEmpty { Text(item.description) }
            LabeledContent { Text(item.sourceLabel) } label: { Label("来源", systemImage: item.symbol) }
            if let url = item.sourceURL {
                LabeledContent { Text(url).font(.caption).textSelection(.enabled).lineLimit(3) } label: { Label("地址", systemImage: "link") }
            }
            if let ref = item.sourceRef { LabeledContent { Text(ref) } label: { Label("分支 / 标签", systemImage: "arrow.triangle.branch") } }
            if let path = item.sourcePath { LabeledContent { Text(path).font(.caption.monospaced()) } label: { Label("子目录", systemImage: "folder") } }
            if let commit = item.sourceCommit { LabeledContent { Text(String(commit.prefix(12))).font(.caption.monospaced()) } label: { Label("提交", systemImage: "number") } }
            if let version = item.version { LabeledContent { Text(version) } label: { Label("版本", systemImage: "shippingbox") } }
            if !item.builtin {
                LabeledContent { Text("\(item.fileCount) 个 · \(AgentSkillItem.sizeText(item.totalBytes))") } label: { Label("文件", systemImage: "doc.on.doc") }
            }
            if let updated = item.updatedAt ?? item.installedAt {
                LabeledContent { Text(updated).font(.caption) } label: { Label("更新时间", systemImage: "clock") }
            }
        }
    }

    private func enableSection(_ item: AgentSkillItem) -> some View {
        Section {
            Toggle(isOn: Binding(get: { item.enabledAgent }, set: { value in Task { await patch(["enabled_agent": .bool(value)]) } })) {
                Label("对系统 Agent 启用", systemImage: "sparkles")
            }
            .disabled(busy != nil || uncertain)
            if item.pluginSupported {
                Toggle(isOn: Binding(get: { item.enabledPlugins }, set: { value in Task { await patch(["enabled_plugins": .bool(value)]) } })) {
                    Label("对插件启用", systemImage: "puzzlepiece.extension")
                }
                .disabled(busy != nil || uncertain)
            }
            if busy == "patch" { ProgressView("正在保存…") }
        } header: { Text("启用") } footer: {
            Text(item.pluginSupported
                 ? "对插件启用后，声明了 agent_skills 权限的插件可以读取它的文本（插件内置 skill 只由所属插件使用）。停用不会删除内容。"
                 : "内置领域技能只用于系统 Agent。停用不会删除内容。")
        }
    }

    private func upstreamSection(_ item: AgentSkillItem) -> some View {
        Section {
            if item.upstreamChanged {
                Label("插件带来了新的默认内容（\(item.upstreamVersion ?? "新版本")），你保留着自己的修改。", systemImage: "sparkles")
                    .foregroundStyle(.orange)
            } else {
                Label("你修改过这个内置 skill，插件升级不会覆盖它。", systemImage: "pencil.circle")
            }
            NavigationLink {
                AgentSkillUpstreamView(name: name) {
                    detail = nil
                    await load()
                    onChange()
                }
            } label: { Label("查看与默认版本的差异", systemImage: "plus.forwardslash.minus") }
        } header: { Text("默认版本") }
    }

    private func contentSection(_ item: AgentSkillItem) -> some View {
        Section {
            NavigationLink {
                AgentSkillTextView(title: item.builtin ? "流程说明" : "SKILL.md", text: skillMarkdown)
            } label: { Label(item.builtin ? "查看流程说明" : "查看 SKILL.md", systemImage: "doc.text") }
            .disabled(detail == nil)
            if item.editable {
                Button("编辑 SKILL.md", systemImage: "square.and.pencil") { editing = true }
                    .disabled(detail == nil || busy != nil || uncertain)
                    .accessibilityIdentifier("skills.edit")
            }
        } header: { Text("内容") } footer: {
            if item.bundled { Text("编辑内置 skill 会保存为你的版本，可随时“恢复默认”。") }
            else if !item.editable && !item.builtin { Text("来自 GitHub / zip 链接的 skill 只能从源地址更新，不能直接编辑。") }
        }
    }

    private var filesSection: some View {
        Section("附属文件") {
            ForEach(Array(files.enumerated()), id: \.offset) { _, file in
                let path = file["path"]?.stringValue ?? ""
                NavigationLink {
                    AgentSkillFileView(name: name, path: path)
                } label: {
                    LabeledContent {
                        Text(AgentSkillItem.sizeText(file["size"]?.intValue ?? 0)).font(.caption)
                    } label: { Label(path, systemImage: "doc.text").font(.callout.monospaced()) }
                }
            }
        }
    }

    private func skippedSection(_ item: AgentSkillItem) -> some View {
        Section {
            ForEach(Array(item.skippedFiles.enumerated()), id: \.offset) { _, file in
                LabeledContent {
                    Text(skipReason(file["reason"]?.stringValue ?? "")).font(.caption)
                } label: { Text(file["path"]?.stringValue ?? "").font(.caption.monospaced()) }
            }
        } header: { Text("安装时跳过的文件") } footer: { Text("二进制、过大、隐藏文件与符号链接不会被保存。") }
    }

    private func manageSection(_ item: AgentSkillItem) -> some View {
        Section {
            if item.updatable {
                Button("从源地址更新", systemImage: "arrow.down.circle") { confirmingUpdate = true }
                    .disabled(busy != nil || uncertain)
                    .confirmationDialog("从源地址重新拉取「\(name)」？", isPresented: $confirmingUpdate, titleVisibility: .visible) {
                        Button("更新") { Task { await update() } }
                    } message: { Text("会用源地址的最新内容替换当前文件。") }
            }
            if item.bundled && item.userModified {
                Button("恢复默认", systemImage: "arrow.uturn.backward", role: .destructive) { confirmingRestore = true }
                    .disabled(busy != nil || uncertain)
                    .confirmationDialog("恢复「\(name)」为插件默认版本？", isPresented: $confirmingRestore, titleVisibility: .visible) {
                        Button("恢复默认", role: .destructive) { Task { await restoreDefault() } }
                    } message: { Text("你的修改会被丢弃。") }
            }
            if item.uninstallable {
                Button("卸载", systemImage: "trash", role: .destructive) { confirmingUninstall = true }
                    .disabled(busy != nil || uncertain)
                    .confirmationDialog("卸载「\(name)」？", isPresented: $confirmingUninstall, titleVisibility: .visible) {
                        Button("卸载", role: .destructive) { Task { await uninstall() } }
                    } message: { Text("会删除这个 skill 的全部文件，并从使用它的插件配置里移除。") }
            }
            if let busy, busy != "patch" { ProgressView(busyText(busy)) }
        } footer: {
            if uncertain { Text("上次操作的结果尚未确认。请下拉刷新，确认服务器上的实际状态后再操作。") }
            else if !item.uninstallable { Text(item.builtin ? "TelePilot 内置 skill 不能卸载，只能停用。" : "插件内置 skill 不能卸载，只能停用。") }
        }
    }

    private func busyText(_ key: String) -> String {
        switch key {
        case "update": "正在从源地址更新…"
        case "restore": "正在恢复默认…"
        case "uninstall": "正在卸载…"
        default: "正在处理…"
        }
    }

    private func skipReason(_ raw: String) -> String {
        switch raw {
        case "binary": "二进制文件"
        case "too_large": "文件过大"
        case "symlink": "符号链接"
        case "hidden": "隐藏文件"
        case "ignored_dir", "ignored_file": "已忽略"
        case "outside": "不在 skill 目录内"
        default: raw
        }
    }

    private func load() async {
        do {
            detail = try await model.request(path: AgentSkillAPI.path(name))
            error = nil
            uncertain = false
        } catch {
            self.error = AgentSkillAPI.message(error)
        }
    }

    /// 写操作只发一次：失败如实显示；网络中断时标记“结果待确认”，不自动重试。
    private func perform(_ key: String, _ action: () async throws -> String?) async {
        guard busy == nil, !uncertain else { return }
        busy = key
        actionError = nil
        notice = nil
        defer { busy = nil }
        do {
            notice = try await action()
            onChange()
        } catch {
            actionError = AgentSkillAPI.message(error)
            uncertain = AgentSkillAPI.isUncertain(error)
        }
    }

    private func patch(_ body: [String: JSONValue]) async {
        await perform("patch") {
            let value = try await model.request(path: AgentSkillAPI.path(name), method: "PATCH", body: .object(body))
            if var merged = detail?.objectValue, let updates = value.objectValue {
                for (key, entry) in updates { merged[key] = entry }
                detail = .object(merged)
            }
            return nil
        }
    }

    private func update() async {
        await perform("update") {
            let value = try await model.request(path: AgentSkillAPI.path(name) + "/update", method: "POST", body: .object([:]))
            await load()
            return value["changed"]?.boolValue == false ? "源地址没有新内容。" : "已从源地址更新。"
        }
    }

    private func restoreDefault() async {
        await perform("restore") {
            _ = try await model.request(path: AgentSkillAPI.path(name) + "/restore-default", method: "POST", body: .object([:]))
            await load()
            return "已恢复为插件默认版本。"
        }
    }

    private func uninstall() async {
        await perform("uninstall") {
            _ = try await model.request(path: AgentSkillAPI.path(name), method: "DELETE")
            dismiss()
            return "已卸载。"
        }
    }
}

// MARK: - 只读文本与文件

struct AgentSkillTextView: View {
    let title: String
    let text: String
    var body: some View {
        ScrollView {
            Text(text.isEmpty ? "（空）" : text)
                .font(.callout.monospaced())
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
        }
        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
    }
}

struct AgentSkillFileView: View {
    let name: String
    let path: String
    @Environment(AppModel.self) private var model
    @State private var content: JSONValue?
    @State private var error: String?
    var body: some View {
        Group {
            if let content {
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        if content["truncated"]?.boolValue == true {
                            Label("文件较长，只显示前一部分。", systemImage: "scissors").font(.caption).foregroundStyle(.secondary)
                        }
                        Text(content["content"]?.stringValue ?? "")
                            .font(.callout.monospaced())
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding()
                }
            } else if let error {
                List { NoticeView(title: "读取失败", message: error) }
            } else {
                ProgressView("正在读取文件…")
            }
        }
        .navigationTitle(path).navigationBarTitleDisplayMode(.inline)
        .task {
            guard content == nil else { return }
            let encoded = path.split(separator: "/").map { ManagementText.pathSegment(String($0)) }.joined(separator: "/")
            do { content = try await model.request(path: AgentSkillAPI.path(name) + "/files/" + encoded); error = nil }
            catch { self.error = AgentSkillAPI.message(error) }
        }
    }
}

// MARK: - 编辑

struct AgentSkillEditorView: View {
    let name: String
    let original: String
    let onSaved: (JSONValue) async -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var saving = false
    @State private var error: String?
    @State private var uncertain = false
    @State private var confirmingDiscard = false

    private var changed: Bool { text != original }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextEditor(text: $text)
                        .font(.callout.monospaced())
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .frame(minHeight: 360)
                        .accessibilityIdentifier("skills.editor")
                } footer: {
                    Text("保留开头的 front matter，name 必须仍为「\(name)」。\(text.utf8.count > AgentSkillAPI.maxSkillMarkdownBytes ? "内容超过 256 KB 上限。" : "")")
                }
                if let error { Section { NoticeView(title: uncertain ? "结果待确认" : "保存失败", message: error) } }
                if saving { Section { ProgressView("正在保存到服务器…") } }
            }
            .navigationTitle("编辑 SKILL.md").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { if changed && !uncertain { confirmingDiscard = true } else { dismiss() } }
                        .disabled(saving)
                        .confirmationDialog("放弃未保存的修改？", isPresented: $confirmingDiscard, titleVisibility: .visible) {
                            Button("放弃修改", role: .destructive) { dismiss() }
                        }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") { Task { await save() } }
                        .disabled(!changed || saving || uncertain || text.utf8.count > AgentSkillAPI.maxSkillMarkdownBytes)
                        .accessibilityIdentifier("skills.editor.save")
                }
            }
            .onAppear { if text.isEmpty { text = original } }
        }
        .interactiveDismissDisabled(saving || changed)
    }

    private func save() async {
        guard !saving, !uncertain else { return }
        saving = true
        error = nil
        defer { saving = false }
        do {
            let value = try await model.request(path: AgentSkillAPI.path(name) + "/content", method: "PUT", body: .object(["skill_md": .string(text)]))
            await onSaved(value)
            dismiss()
        } catch {
            self.error = AgentSkillAPI.isUncertain(error)
                ? "网络中断，无法确认是否已保存。请关闭此页，刷新详情确认服务器上的内容后再操作。"
                : AgentSkillAPI.message(error)
            uncertain = AgentSkillAPI.isUncertain(error)
        }
    }
}

// MARK: - 与默认版本的差异

struct AgentSkillUpstreamView: View {
    let name: String
    let onChange: () async -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var upstream: JSONValue?
    @State private var error: String?
    @State private var actionError: String?
    @State private var uncertain = false
    @State private var busy = false
    @State private var confirmingRestore = false
    @State private var confirmingKeep = false
    @State private var expandedGaps: Set<Int> = []
    @State private var chosenLayout: AgentSkillDiffLayout?
    @State private var codeDiff: AgentSkillCodeDiff?
    @Environment(\.horizontalSizeClass) private var sizeClass

    /// 宽屏（iPad、大屏横屏）默认双栏，紧凑宽度固定统一视图。
    private var layout: AgentSkillDiffLayout { sizeClass == .regular ? (chosenLayout ?? .split) : .unified }

    var body: some View {
        List {
            if let error, upstream == nil {
                Section {
                    NoticeView(title: "读取失败", message: error)
                    Button("重新读取", systemImage: "arrow.clockwise") { Task { await load() } }
                }
            } else if let upstream {
                Section {
                    if upstream["upstream_changed"]?.boolValue == true {
                        Label("插件默认版本 \(upstream["upstream_version"]?.stringValue ?? "") 有更新。", systemImage: "sparkles").foregroundStyle(.orange)
                    }
                    Text("红底“−”是默认版本（插件内置），绿底“+”是你当前保存的版本；改动的字词深色标出，长行自动换行。").font(.footnote).foregroundStyle(.secondary)
                    if let codeDiff, codeDiff.changed {
                        HStack(spacing: 10) {
                            Text("+\(codeDiff.added)").foregroundStyle(.green)
                            Text("−\(codeDiff.removed)").foregroundStyle(.red)
                            Spacer()
                            let gaps = codeDiff.gapIDs(layout)
                            if !gaps.isEmpty {
                                let all = gaps.isSubset(of: expandedGaps)
                                Button(all ? "折叠未改动" : "全部展开", systemImage: "arrow.up.and.down") {
                                    expandedGaps = all ? [] : gaps
                                }
                                .font(.footnote)
                                .buttonStyle(.borderless)
                            }
                        }
                        .font(.subheadline.monospacedDigit().weight(.semibold))
                        .accessibilityElement(children: .contain)
                        .accessibilityIdentifier("skills.diff.stats")
                        if sizeClass == .regular {
                            Picker("对比视图", selection: Binding(get: { layout }, set: { chosenLayout = $0; expandedGaps = [] })) {
                                Text("双栏").tag(AgentSkillDiffLayout.split)
                                Text("统一").tag(AgentSkillDiffLayout.unified)
                            }
                            .pickerStyle(.segmented)
                        }
                    }
                }
                Section {
                    if let codeDiff {
                        if !codeDiff.changed {
                            Text("当前内容与默认版本相同。").foregroundStyle(.secondary)
                        } else {
                            ForEach(codeDiff.visibleRows(layout, expanded: expandedGaps)) { row in
                                AgentSkillCodeDiffRowView(row: row) { id in expandedGaps.insert(id) }
                                    .listRowInsets(EdgeInsets())
                                    .listRowSeparator(.hidden)
                                    .listRowBackground(row.rowBackground)
                            }
                        }
                    } else {
                        ProgressView("正在对比…")
                    }
                } header: {
                    Text(layout == .split ? "默认版本 ｜ 当前版本" : "差异")
                } footer: {
                    if codeDiff?.degraded == true { Text("内容较长，改动区按整段显示，未逐行对齐。") }
                }
                Section("默认版本全文") {
                    NavigationLink {
                        AgentSkillTextView(title: "默认版本", text: upstream["upstream_skill_md"]?.stringValue ?? "")
                    } label: { Label("查看默认版本", systemImage: "doc.text") }
                }
                if let actionError { Section { NoticeView(title: uncertain ? "结果待确认" : "操作未完成", message: actionError) } }
                Section {
                    if upstream["upstream_changed"]?.boolValue == true {
                        Button("保留我的修改", systemImage: "checkmark.circle") { confirmingKeep = true }
                            .disabled(busy || uncertain)
                            .confirmationDialog("保留你的修改并忽略这次默认版本更新？", isPresented: $confirmingKeep, titleVisibility: .visible) {
                                Button("保留我的修改") { Task { await act("keep-modified") } }
                            } message: { Text("“内置有新版本”标记会消失，直到插件再次更新默认内容。") }
                    }
                    if upstream["user_modified"]?.boolValue == true {
                        Button("恢复默认", systemImage: "arrow.uturn.backward", role: .destructive) { confirmingRestore = true }
                            .disabled(busy || uncertain)
                            .confirmationDialog("恢复为插件默认版本？", isPresented: $confirmingRestore, titleVisibility: .visible) {
                                Button("恢复默认", role: .destructive) { Task { await act("restore-default") } }
                            } message: { Text("你的修改会被丢弃。") }
                    }
                    if busy { ProgressView("正在处理…") }
                } footer: {
                    if uncertain { Text("上次操作的结果尚未确认。请返回详情页刷新后再操作。") }
                }
            } else {
                Section { ProgressView("正在读取默认版本…") }
            }
        }
        .navigationTitle("与默认版本对比").navigationBarTitleDisplayMode(.inline)
        .task { if upstream == nil { await load() } }
    }

    private func load() async {
        do {
            let value = try await model.request(path: AgentSkillAPI.path(name) + "/upstream")
            upstream = value
            error = nil
            let old = value["upstream_skill_md"]?.stringValue ?? ""
            let new = value["current_skill_md"]?.stringValue ?? ""
            codeDiff = await Task.detached(priority: .userInitiated) { AgentSkillCodeDiff(old: old, new: new) }.value
            expandedGaps = []
        }
        catch { self.error = AgentSkillAPI.message(error) }
    }

    private func act(_ suffix: String) async {
        guard !busy, !uncertain else { return }
        busy = true
        actionError = nil
        defer { busy = false }
        do {
            _ = try await model.request(path: AgentSkillAPI.path(name) + "/" + suffix, method: "POST", body: .object([:]))
        } catch {
            actionError = AgentSkillAPI.message(error)
            uncertain = AgentSkillAPI.isUncertain(error)
            return
        }
        await onChange()
        dismiss()
    }
}

// MARK: - 代码对比（行号、红绿底、字词级高亮、折叠未改动段）

/// 与 Web `frontend/src/lib/skillDiff.ts` 同一算法：裁掉公共前后缀后做 LCS 行对齐，
/// 修改行再按“空白 / 英文单词 / 单个汉字或标点”分词做字词级对比。
enum AgentSkillDiffLayout: Hashable { case split, unified }
enum AgentSkillDiffKind: Hashable { case equal, del, add }

struct AgentSkillDiffSegment: Hashable {
    let text: String
    let changed: Bool
}

struct AgentSkillDiffCell: Hashable {
    let kind: AgentSkillDiffKind
    let text: String
    let oldNo: Int?
    let newNo: Int?
    var segments: [AgentSkillDiffSegment]?
}

struct AgentSkillDiffRow: Identifiable {
    enum Content {
        case unified(AgentSkillDiffCell)
        case split(left: AgentSkillDiffCell?, right: AgentSkillDiffCell?)
        case gap(id: Int, count: Int)
    }
    let id: String
    let content: Content
    /// 折叠段内的原始行，展开时原样插回。
    var children: [AgentSkillDiffRow] = []

    var rowBackground: Color? {
        switch content {
        case .unified(let cell): AgentSkillCodeDiff.lineColor(cell.kind)
        case .gap: Color.blue.opacity(0.07)
        case .split: nil
        }
    }
}

struct AgentSkillCodeDiff: Sendable {
    struct Op: Sendable {
        let kind: AgentSkillDiffKind
        let text: String
        let oldNo: Int?
        let newNo: Int?
    }

    let ops: [Op]
    let added: Int
    let removed: Int
    let degraded: Bool
    var changed: Bool { added + removed > 0 }

    init(old: String, new: String) {
        let a = Self.lines(old)
        let b = Self.lines(new)
        var start = 0
        while start < a.count, start < b.count, a[start] == b[start] { start += 1 }
        var endA = a.count
        var endB = b.count
        while endA > start, endB > start, a[endA - 1] == b[endB - 1] { endA -= 1; endB -= 1 }
        let midA = Array(a[start..<endA])
        let midB = Array(b[start..<endB])
        let pairs = Self.lcs(midA, midB)
        var result: [Op] = []
        var oldNo = 1
        var newNo = 1
        func equal(_ text: String) { result.append(Op(kind: .equal, text: text, oldNo: oldNo, newNo: newNo)); oldNo += 1; newNo += 1 }
        func del(_ text: String) { result.append(Op(kind: .del, text: text, oldNo: oldNo, newNo: nil)); oldNo += 1 }
        func add(_ text: String) { result.append(Op(kind: .add, text: text, oldNo: nil, newNo: newNo)); newNo += 1 }
        for index in 0..<start { equal(a[index]) }
        var i = 0
        var j = 0
        for (pi, pj) in pairs ?? [] {
            while i < pi { del(midA[i]); i += 1 }
            while j < pj { add(midB[j]); j += 1 }
            equal(midA[i]); i += 1; j += 1
        }
        while i < midA.count { del(midA[i]); i += 1 }
        while j < midB.count { add(midB[j]); j += 1 }
        for index in endA..<a.count { equal(a[index]) }
        ops = result
        added = result.filter { $0.kind == .add }.count
        removed = result.filter { $0.kind == .del }.count
        degraded = pairs == nil
    }

    static func lines(_ text: String) -> [String] {
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n")
        if normalized.isEmpty { return [] }
        var rows = normalized.components(separatedBy: "\n")
        if rows.last == "" { rows.removeLast() }
        return rows
    }

    static func lcs<T: Equatable>(_ a: [T], _ b: [T], limit: Int = 1_500_000) -> [(Int, Int)]? {
        let n = a.count
        let m = b.count
        if n == 0 || m == 0 { return [] }
        if n * m > limit { return nil }
        let width = m + 1
        var table = [Int32](repeating: 0, count: (n + 1) * width)
        for i in stride(from: n - 1, through: 0, by: -1) {
            for j in stride(from: m - 1, through: 0, by: -1) {
                table[i * width + j] = a[i] == b[j]
                    ? table[(i + 1) * width + j + 1] + 1
                    : max(table[(i + 1) * width + j], table[i * width + j + 1])
            }
        }
        var pairs: [(Int, Int)] = []
        var i = 0
        var j = 0
        while i < n && j < m {
            if a[i] == b[j] { pairs.append((i, j)); i += 1; j += 1 }
            else if table[(i + 1) * width + j] >= table[i * width + j + 1] { i += 1 }
            else { j += 1 }
        }
        return pairs
    }

    /// 连续空白、连续 ASCII 字母数字各为一段，其余（汉字、标点）逐字一段。
    static func tokens(_ text: String) -> [String] {
        var out: [String] = []
        var current = ""
        var currentType = 0
        for character in text {
            let type = character.isWhitespace ? 1 : (character.isASCII && (character.isLetter || character.isNumber || character == "_")) ? 2 : 0
            if type != 0 && type == currentType {
                current.append(character)
            } else {
                if !current.isEmpty { out.append(current) }
                current = String(character)
                currentType = type
            }
        }
        if !current.isEmpty { out.append(current) }
        return out
    }

    static func inline(_ old: String, _ new: String) -> ([AgentSkillDiffSegment], [AgentSkillDiffSegment]) {
        let whole = ([AgentSkillDiffSegment(text: old, changed: true)], [AgentSkillDiffSegment(text: new, changed: true)])
        let a = tokens(old)
        let b = tokens(new)
        guard a.count <= 1500, b.count <= 1500, let pairs = lcs(a, b) else { return whole }
        let kept = pairs.reduce(0) { $0 + (a[$1.0].trimmingCharacters(in: .whitespaces).isEmpty ? 0 : a[$1.0].count) }
        let total = max(old.filter { !$0.isWhitespace }.count, new.filter { !$0.isWhitespace }.count, 1)
        if Double(kept) / Double(total) < 0.3 { return whole }
        var changedA = Array(repeating: true, count: a.count)
        var changedB = Array(repeating: true, count: b.count)
        for (i, j) in pairs { changedA[i] = false; changedB[j] = false }
        return (merge(a, changedA), merge(b, changedB))
    }

    private static func merge(_ tokens: [String], _ changed: [Bool]) -> [AgentSkillDiffSegment] {
        var out: [AgentSkillDiffSegment] = []
        for (index, token) in tokens.enumerated() {
            if let last = out.last, last.changed == changed[index] {
                out[out.count - 1] = AgentSkillDiffSegment(text: last.text + token, changed: last.changed)
            } else {
                out.append(AgentSkillDiffSegment(text: token, changed: changed[index]))
            }
        }
        return out
    }

    private enum Block { case equal([Op]), change(dels: [Op], adds: [Op]) }

    private var blocks: [Block] {
        var out: [Block] = []
        for op in ops {
            switch (op.kind, out.last) {
            case (.equal, .equal(let list)?): out[out.count - 1] = .equal(list + [op])
            case (.equal, _): out.append(.equal([op]))
            case (.del, .change(let dels, let adds)?): out[out.count - 1] = .change(dels: dels + [op], adds: adds)
            case (.add, .change(let dels, let adds)?): out[out.count - 1] = .change(dels: dels, adds: adds + [op])
            case (.del, _): out.append(.change(dels: [op], adds: []))
            case (.add, _): out.append(.change(dels: [], adds: [op]))
            }
        }
        return out
    }

    private static func cell(_ op: Op, segments: [AgentSkillDiffSegment]? = nil) -> AgentSkillDiffCell {
        AgentSkillDiffCell(kind: op.kind, text: op.text, oldNo: op.oldNo, newNo: op.newNo, segments: segments)
    }

    /// 生成行（未改动长段折叠为 gap，距改动保留 `context` 行）。
    func rows(_ layout: AgentSkillDiffLayout, context: Int = 3) -> [AgentSkillDiffRow] {
        let blocks = self.blocks
        var gapID = 0
        var out: [AgentSkillDiffRow] = []
        for (index, block) in blocks.enumerated() {
            switch block {
            case .equal(let list):
                let rendered: [AgentSkillDiffRow] = list.map { op in
                    let c = Self.cell(op)
                    let id = "e\(op.oldNo ?? 0)-\(op.newNo ?? 0)"
                    return layout == .split ? AgentSkillDiffRow(id: id, content: .split(left: c, right: c)) : AgentSkillDiffRow(id: id, content: .unified(c))
                }
                let head = index == 0 ? 0 : context
                let tail = index == blocks.count - 1 ? 0 : context
                if rendered.count <= head + tail + 1 {
                    out += rendered
                } else {
                    let hidden = Array(rendered[head..<(rendered.count - tail)])
                    out += rendered[0..<head]
                    out.append(AgentSkillDiffRow(id: "g\(gapID)", content: .gap(id: gapID, count: hidden.count), children: hidden))
                    gapID += 1
                    out += rendered[(rendered.count - tail)...]
                }
            case .change(let dels, let adds):
                let inlines: [([AgentSkillDiffSegment], [AgentSkillDiffSegment])?] = dels.indices.map { k in
                    k < adds.count ? Self.inline(dels[k].text, adds[k].text) : nil
                }
                if layout == .split {
                    for k in 0..<max(dels.count, adds.count) {
                        let left = k < dels.count ? Self.cell(dels[k], segments: inlines[k]?.0) : nil
                        let right = k < adds.count ? Self.cell(adds[k], segments: k < inlines.count ? inlines[k]?.1 : nil) : nil
                        out.append(AgentSkillDiffRow(id: "c\(left?.oldNo ?? 0)-\(right?.newNo ?? 0)", content: .split(left: left, right: right)))
                    }
                } else {
                    for (k, op) in dels.enumerated() {
                        out.append(AgentSkillDiffRow(id: "d\(op.oldNo ?? 0)", content: .unified(Self.cell(op, segments: inlines[k]?.0))))
                    }
                    for (k, op) in adds.enumerated() {
                        out.append(AgentSkillDiffRow(id: "a\(op.newNo ?? 0)", content: .unified(Self.cell(op, segments: k < inlines.count ? inlines[k]?.1 : nil))))
                    }
                }
            }
        }
        return out
    }

    func gapIDs(_ layout: AgentSkillDiffLayout) -> Set<Int> {
        Set(rows(layout).compactMap { row -> Int? in
            if case .gap(let id, _) = row.content { return id }
            return nil
        })
    }

    func visibleRows(_ layout: AgentSkillDiffLayout, expanded: Set<Int>) -> [AgentSkillDiffRow] {
        rows(layout).flatMap { row -> [AgentSkillDiffRow] in
            if case .gap(let id, _) = row.content, expanded.contains(id) { return row.children }
            return [row]
        }
    }

    static func lineColor(_ kind: AgentSkillDiffKind) -> Color? {
        switch kind {
        case .add: Color.green.opacity(0.13)
        case .del: Color.red.opacity(0.13)
        case .equal: nil
        }
    }

    static func gutterColor(_ kind: AgentSkillDiffKind) -> Color {
        switch kind {
        case .add: Color.green.opacity(0.22)
        case .del: Color.red.opacity(0.22)
        case .equal: Color.secondary.opacity(0.08)
        }
    }

    static func attributed(_ cell: AgentSkillDiffCell) -> AttributedString {
        guard let segments = cell.segments else { return AttributedString(cell.text.isEmpty ? " " : cell.text) }
        var out = AttributedString()
        for segment in segments {
            var piece = AttributedString(segment.text)
            if segment.changed {
                let highlight: Color = cell.kind == .add ? Color.green.opacity(0.4) : Color.red.opacity(0.4)
                piece.backgroundColor = highlight
                piece.inlinePresentationIntent = .stronglyEmphasized
            }
            out += piece
        }
        return out.characters.isEmpty ? AttributedString(" ") : out
    }

    static func spokenLabel(_ cell: AgentSkillDiffCell) -> String {
        switch cell.kind {
        case .add: "新增：\(cell.text)"
        case .del: "删除：\(cell.text)"
        case .equal: cell.text
        }
    }
}

struct AgentSkillCodeDiffRowView: View {
    let row: AgentSkillDiffRow
    let expand: (Int) -> Void

    var body: some View {
        switch row.content {
        case .gap(let id, let count):
            Button { expand(id) } label: {
                Label("展开 \(count) 行未改动内容", systemImage: "arrow.up.and.down")
                    .font(.caption)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12).padding(.vertical, 6)
            }
            .accessibilityIdentifier("skills.diff.gap")
        case .unified(let cell):
            HStack(alignment: .top, spacing: 0) {
                HStack(alignment: .top, spacing: 0) {
                    number(cell.oldNo)
                    number(cell.newNo)
                }
                .background(AgentSkillCodeDiff.gutterColor(cell.kind))
                Text(cell.kind == .add ? "+" : cell.kind == .del ? "−" : " ")
                    .foregroundStyle(cell.kind == .add ? Color.green : cell.kind == .del ? Color.red : Color.clear)
                    .frame(width: 16)
                code(cell)
            }
            .font(.caption.monospaced())
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(AgentSkillCodeDiff.spokenLabel(cell))
            .accessibilityValue(cell.newNo.map { "当前第 \($0) 行" } ?? cell.oldNo.map { "默认第 \($0) 行" } ?? "")
        case .split(let left, let right):
            HStack(alignment: .top, spacing: 0) {
                side(left, number: left?.oldNo)
                Divider()
                side(right, number: right?.newNo)
            }
            .font(.caption.monospaced())
        }
    }

    private func number(_ value: Int?) -> some View {
        Text(value.map(String.init) ?? "")
            .monospacedDigit()
            .foregroundStyle(.secondary)
            .frame(width: 34, alignment: .trailing)
            .padding(.trailing, 4)
            .padding(.vertical, 2)
    }

    private func code(_ cell: AgentSkillDiffCell) -> some View {
        Text(AgentSkillCodeDiff.attributed(cell))
            // 长行在行内换行，不撑宽列表
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 2)
            .padding(.trailing, 8)
            .textSelection(.enabled)
    }

    @ViewBuilder
    private func side(_ cell: AgentSkillDiffCell?, number value: Int?) -> some View {
        if let cell {
            HStack(alignment: .top, spacing: 0) {
                number(value).background(AgentSkillCodeDiff.gutterColor(cell.kind))
                code(cell).padding(.leading, 6)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(AgentSkillCodeDiff.lineColor(cell.kind))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(AgentSkillCodeDiff.spokenLabel(cell))
            .accessibilityValue(value.map { "第 \($0) 行" } ?? "")
        } else {
            Color.secondary.opacity(0.08)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityHidden(true)
        }
    }
}

// MARK: - 安装

struct AgentSkillInstallView: View {
    enum Source: String, CaseIterable, Identifiable {
        case github, zipURL, paste, file
        var id: String { rawValue }
        var title: String {
            switch self {
            case .github: "GitHub"
            case .zipURL: "zip 链接"
            case .paste: "粘贴"
            case .file: "选择文件"
            }
        }
    }

    let onInstalled: () -> Void
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var source: Source = .github
    @State private var url = ""
    @State private var ref = ""
    @State private var subpath = ""
    @State private var pasted = ""
    @State private var fileName: String?
    @State private var fileData: Data?
    @State private var fileIsZip = false
    @State private var picking = false
    @State private var enabledAgent = true
    @State private var enabledPlugins = false
    @State private var installing = false
    @State private var error: String?
    @State private var uncertain = false
    @State private var result: AgentSkillItem?

    private var markdownType: UTType { UTType(filenameExtension: "md", conformingTo: .plainText) ?? .plainText }

    private var ready: Bool {
        switch source {
        case .github, .zipURL: url.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("https://")
        case .paste: !pasted.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && pasted.utf8.count <= AgentSkillAPI.maxSkillMarkdownBytes
        case .file: fileData != nil
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                if let result {
                    resultSection(result)
                } else {
                    Section {
                        Picker("来源", selection: $source) {
                            ForEach(Source.allCases) { item in Text(item.title).tag(item) }
                        }
                        .pickerStyle(.segmented)
                        .disabled(installing || uncertain)
                    } footer: { Text(sourceHint) }
                    inputSection
                    Section {
                        Toggle(isOn: $enabledAgent) { Label("对系统 Agent 启用", systemImage: "sparkles") }
                        Toggle(isOn: $enabledPlugins) { Label("对插件启用", systemImage: "puzzlepiece.extension") }
                    } footer: { Text("安装后也可以在详情页随时调整。skill 中的脚本不会被执行。") }
                    if let error { Section { NoticeView(title: uncertain ? "结果待确认" : "安装失败", message: error) } }
                    Section {
                        Button { Task { await install() } } label: {
                            Label("安装", systemImage: "square.and.arrow.down").frame(maxWidth: .infinity, minHeight: 44)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!ready || installing || uncertain)
                        .listRowInsets(EdgeInsets()).listRowBackground(Color.clear)
                        .accessibilityIdentifier("skills.install.submit")
                        if installing { ProgressView(source == .github || source == .zipURL ? "服务器正在下载并校验…" : "正在上传并校验…").frame(maxWidth: .infinity) }
                    } footer: {
                        if uncertain { Text("网络中断，无法确认是否已安装。请关闭此页刷新 Skills 列表确认后再操作，不要立即重复安装。") }
                    }
                }
            }
            .navigationTitle("安装 skill").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(result == nil ? "取消" : "完成") { dismiss() }.disabled(installing)
                }
            }
            .fileImporter(isPresented: $picking, allowedContentTypes: [.zip, markdownType, .plainText]) { selected in select(selected) }
            .onChange(of: source) { _, _ in error = nil }
        }
        .interactiveDismissDisabled(installing)
    }

    private var sourceHint: String {
        switch source {
        case .github: "支持仓库、分支或子目录链接，例如 https://github.com/owner/repo/tree/main/skills/pdf。"
        case .zipURL: "https 开头的 zip 下载链接（不超过 10 MB），zip 内需包含 SKILL.md。"
        case .paste: "粘贴完整的 SKILL.md，开头需要 --- 包裹的 name 与 description。"
        case .file: "选择 .zip（不超过 10 MB）或 SKILL.md 文件。"
        }
    }

    @ViewBuilder private var inputSection: some View {
        switch source {
        case .github, .zipURL:
            Section {
                TextField(source == .github ? "https://github.com/owner/repo" : "https://example.com/skill.zip", text: $url, axis: .vertical)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("skills.install.url")
                if source == .github {
                    TextField("分支 / 标签 / 提交（可选）", text: $ref).textInputAutocapitalization(.never).autocorrectionDisabled()
                }
                TextField("子目录（可选，多个 skill 时填写）", text: $subpath).textInputAutocapitalization(.never).autocorrectionDisabled()
            } header: { Text("链接") }
        case .paste:
            Section {
                TextEditor(text: $pasted)
                    .font(.callout.monospaced())
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .frame(minHeight: 240)
                    .accessibilityIdentifier("skills.install.paste")
            } header: { Text("SKILL.md") } footer: {
                if pasted.utf8.count > AgentSkillAPI.maxSkillMarkdownBytes { Text("内容超过 256 KB 上限。").foregroundStyle(.red) }
            }
        case .file:
            Section {
                Button(fileName == nil ? "选择 .zip 或 .md 文件" : "重新选择", systemImage: "folder") { picking = true }
                if let fileName, let fileData {
                    LabeledContent {
                        Text(AgentSkillItem.sizeText(fileData.count))
                    } label: { Label(fileName, systemImage: fileIsZip ? "doc.zipper" : "doc.text") }
                }
                if fileIsZip {
                    TextField("zip 内的子目录（可选）", text: $subpath).textInputAutocapitalization(.never).autocorrectionDisabled()
                }
            } header: { Text("文件") }
        }
    }

    private func resultSection(_ item: AgentSkillItem) -> some View {
        Group {
            Section {
                Label("已安装「\(item.name)」", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                if !item.description.isEmpty { Text(item.description).font(.subheadline) }
                LabeledContent("启用状态", value: item.enabledSummary)
                LabeledContent("文件", value: "\(item.fileCount) 个 · \(AgentSkillItem.sizeText(item.totalBytes))")
            }
            if !item.skippedFiles.isEmpty {
                Section {
                    ForEach(Array(item.skippedFiles.enumerated()), id: \.offset) { _, file in
                        Text(file["path"]?.stringValue ?? "").font(.caption.monospaced())
                    }
                } header: { Text("跳过了 \(item.skippedFiles.count) 个文件") } footer: { Text("二进制、过大、隐藏文件与符号链接不会被保存。") }
            }
        }
    }

    private func select(_ selected: Result<URL, Error>) {
        do {
            let fileURL = try selected.get()
            let access = fileURL.startAccessingSecurityScopedResource()
            defer { if access { fileURL.stopAccessingSecurityScopedResource() } }
            let isZip = fileURL.pathExtension.lowercased() == "zip"
            let limit = isZip ? AgentSkillAPI.maxArchiveBytes : AgentSkillAPI.maxSkillMarkdownBytes
            let size = try fileURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard size <= limit else {
                error = isZip ? "zip 超过 10 MB 上限。" : "SKILL.md 超过 256 KB 上限。"
                return
            }
            let data = try Data(contentsOf: fileURL)
            guard data.count <= limit else {
                error = isZip ? "zip 超过 10 MB 上限。" : "SKILL.md 超过 256 KB 上限。"
                return
            }
            if !isZip, String(data: data, encoding: .utf8) == nil {
                error = "SKILL.md 必须是 UTF-8 文本。"
                return
            }
            fileData = data
            fileName = fileURL.lastPathComponent
            fileIsZip = isZip
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func requestBody() -> JSONValue? {
        var body: [String: JSONValue] = ["enabled_agent": .bool(enabledAgent), "enabled_plugins": .bool(enabledPlugins)]
        let trimmedPath = subpath.trimmingCharacters(in: .whitespacesAndNewlines)
        switch source {
        case .github, .zipURL:
            body["source"] = .string(source == .github ? "github" : "zip_url")
            body["url"] = .string(url.trimmingCharacters(in: .whitespacesAndNewlines))
            let trimmedRef = ref.trimmingCharacters(in: .whitespacesAndNewlines)
            if source == .github, !trimmedRef.isEmpty { body["ref"] = .string(trimmedRef) }
            if !trimmedPath.isEmpty { body["path"] = .string(trimmedPath) }
        case .paste:
            body["source"] = .string("paste")
            body["skill_md"] = .string(pasted)
        case .file:
            guard let fileData else { return nil }
            if fileIsZip {
                body["source"] = .string("zip_upload")
                body["zip_base64"] = .string(fileData.base64EncodedString())
                if !trimmedPath.isEmpty { body["path"] = .string(trimmedPath) }
            } else {
                body["source"] = .string("md_upload")
                body["skill_md"] = .string(String(decoding: fileData, as: UTF8.self))
            }
        }
        return .object(body)
    }

    private func install() async {
        guard !installing, !uncertain, let body = requestBody() else { return }
        installing = true
        error = nil
        defer { installing = false }
        do {
            let value = try await model.request(path: AgentSkillAPI.root, method: "POST", body: body)
            result = AgentSkillItem(raw: value)
            onInstalled()
        } catch {
            self.error = AgentSkillAPI.message(error)
            uncertain = AgentSkillAPI.isUncertain(error)
        }
    }
}

// MARK: - 插件配置：x-ui-widget = agent-skill-multi-select

/// 从已安装且“对插件启用”的 skills 中多选（与 Web `AgentSkillMultiSelectField` 一致）。
/// 已保存但当前不可用（被停用或卸载）的名称如实保留显示，由用户决定是否取消。
struct AgentSkillMultiSelectField: View {
    static let widget = "agent-skill-multi-select"
    let title: String
    @Binding var value: JSONValue
    @Environment(AppModel.self) private var model
    @State private var options: [AgentSkillItem] = []
    @State private var loaded = false
    @State private var error: String?

    static func matches(_ spec: JSONValue) -> Bool {
        ManagementSchema.normalized(spec)["x-ui-widget"]?.stringValue == widget
    }

    private var selected: [String] { value.arrayValue?.compactMap(\.stringValue) ?? [] }
    private var stale: [String] { selected.filter { name in !options.contains { $0.name == name } } }

    var body: some View {
        Label(title, systemImage: "wand.and.stars")
        if !loaded && error == nil {
            ProgressView("正在读取已安装的 skills…")
        } else if let error {
            NoticeView(title: "读取 skills 失败", message: error)
            Button("重新读取", systemImage: "arrow.clockwise") { Task { await load() } }
        } else if options.isEmpty && stale.isEmpty {
            Text("还没有对插件启用的 skill。可在助手页右上角的“Skills”按钮里安装并打开“对插件启用”。").font(.footnote).foregroundStyle(.secondary)
        }
        ForEach(options) { option in
            Toggle(isOn: binding(option.name)) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(option.name).font(.callout.monospaced())
                    if !option.description.isEmpty { Text(option.description).font(.caption).foregroundStyle(.secondary).lineLimit(3) }
                }
            }
        }
        ForEach(stale, id: \.self) { name in
            Toggle(isOn: binding(name)) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(name).font(.callout.monospaced())
                    Label("当前不可用（已停用或卸载）", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                }
            }
        }
        NavigationLink { AgentSkillsView() } label: { Label("管理 Skills", systemImage: "wand.and.stars") }
            .task { if !loaded { await load() } }
    }

    private func binding(_ name: String) -> Binding<Bool> {
        Binding(get: { selected.contains(name) }, set: { on in
            var next = selected.filter { $0 != name }
            if on { next.append(name) }
            value = .array(next.map(JSONValue.string))
        })
    }

    private func load() async {
        do {
            let response = try await model.request(path: AgentSkillAPI.root)
            options = (response["items"]?.arrayValue ?? []).map(AgentSkillItem.init(raw:))
                .filter { $0.pluginSupported && $0.enabledPlugins && !$0.name.isEmpty }
            loaded = true
            error = nil
        } catch {
            self.error = AgentSkillAPI.message(error)
        }
    }
}
