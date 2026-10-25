import SwiftUI
import TelePilotCore

private enum AIUsageScope: String, CaseIterable, Identifiable {
    case recent, plugins
    var id: String { rawValue }
    var title: String { self == .recent ? "最近调用" : "插件汇总" }
}

struct AIUsageSection: View {
    @Bindable var workspace: AIWorkspaceStore
    @Environment(AppModel.self) private var model
    @State private var scope: AIUsageScope = .recent
    @State private var filter = "all"
    @State private var search = ""
    @State private var resetting = false
    @State private var confirmingReset = false
    @State private var error: String?

    private var items: [JSONValue] {
        workspace.usageItems.filter { item in
            if filter == "success", item["success"]?.boolValue != true { return false }
            if filter == "failed", item["success"]?.boolValue != false { return false }
            guard !search.isEmpty else { return true }
            return [item["provider_name"]?.stringValue, item["model"]?.stringValue, item["source"]?.stringValue,
                    item["error_type"]?.stringValue].contains { $0?.localizedCaseInsensitiveContains(search) == true }
        }
    }

    var body: some View {
        Section {
            Picker("用量范围", selection: $scope) {
                ForEach(AIUsageScope.allCases) { Text($0.title).tag($0) }
            }.pickerStyle(.segmented)
        }
        if scope == .recent { recent } else { plugins }
    }

    @ViewBuilder private var recent: some View {
        let summary = workspace.usageSummary
        Section {
            HStack(spacing: 20) {
                AIMetric(title: "总词元", value: AIPresentation.tokens(summary["total_tokens"]?.intValue ?? 0))
                AIMetric(title: "请求", value: String(summary["request_count"]?.intValue ?? 0))
                AIMetric(title: "成功率", value: successRate(summary), detail: "平均耗时 \(AIPresentation.latency(summary["avg_latency_ms"]?.intValue))")
            }
            HStack(spacing: 20) {
                AIMetric(title: "失败", value: String(summary["failed_count"]?.intValue ?? 0), color: summary["failed_count"]?.intValue ?? 0 > 0 ? .orange : .primary)
                AIMetric(title: "改用备用模型", value: String(summary["fallback_count"]?.intValue ?? 0))
                AIMetric(title: "缓存命中词元", value: cacheRead)
            }
        } header: { Text("最近 100 条以内") } footer: { Text("“词元（Token）”是模型计费和长度的单位；“改用备用模型”表示首选模型失败后自动换了备用模型；“缓存命中词元”是供应商从缓存读取、通常更便宜的部分，仅在供应商返回该数据时统计。") }

        Section("筛选") {
            Picker("结果", selection: $filter) {
                Text("全部").tag("all"); Text("成功").tag("success"); Text("失败").tag("failed")
            }.pickerStyle(.segmented)
            TextField("搜索供应商、模型、来源或错误", text: $search)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
        }

        Section {
            ForEach(items, id: \.self) { item in
                NavigationLink { AIUsageDetailView(item: item) } label: { AIUsageRow(item: item) }
                    .accessibilityIdentifier("ai.usage.\(item["id"]?.intValue ?? 0)")
            }
            if items.isEmpty {
                Text(workspace.usageItems.isEmpty ? "暂无调用记录。触发一次 AI 指令或模型测活后再查看。" : "当前筛选下没有记录。")
                    .foregroundStyle(.secondary)
            }
        } header: { Text("\(items.count) 条调用") }

        Section {
            Button(role: .destructive) { confirmingReset = true } label: { Label("清空调用记录", systemImage: "trash") }
                .disabled(workspace.usageItems.isEmpty || resetting)
                .accessibilityIdentifier("ai.usage.reset")
                .confirmationDialog("清空全部 AI 调用记录？", isPresented: $confirmingReset, titleVisibility: .visible) {
                    Button("确认清空调用记录", role: .destructive) { Task { await reset() } }
                    Button("取消", role: .cancel) {}
                } message: { Text("此操作不可撤销。历史用量和错误记录将删除，服务器业务配置保持不变。") }
            if resetting { ProgressView("正在清空服务器记录…") }
            if let error { NoticeView(title: "清空操作未完成", message: error) }
        } footer: { Text("将清空近期调用、成功率和插件 AI 用量统计，不影响供应商、模型或指令配置。") }
    }

    private var plugins: some View {
        Section {
            ForEach(workspace.pluginUsage, id: \.self) { item in
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text(item["plugin_key"]?.stringValue ?? "未知插件").font(.headline)
                        Spacer()
                        Text("\(item["request_count"]?.intValue ?? 0) 次").monospacedDigit().foregroundStyle(.secondary)
                    }
                    HStack(spacing: 16) {
                        Text("词元 \(AIPresentation.tokens(item["total_tokens"]?.intValue ?? ((item["input_tokens"]?.intValue ?? 0) + (item["output_tokens"]?.intValue ?? 0))))")
                        Text("失败 \(item["failed_count"]?.intValue ?? 0)")
                            .foregroundStyle(item["failed_count"]?.intValue ?? 0 > 0 ? .orange : .secondary)
                    }.font(.caption).monospacedDigit()
                    Text("最近使用：\(AIPresentation.date(item["last_used_at"]?.stringValue))")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            if workspace.pluginUsage.isEmpty { Text("暂无插件 AI 调用。只有插件产生的调用会进入此汇总。").foregroundStyle(.secondary) }
        } header: { Text("按插件汇总") }
    }

    private var cacheRead: String {
        let measured = workspace.usageItems.filter { $0["cache_read_tokens"] != nil || $0["cache_write_tokens"] != nil }
        guard !measured.isEmpty else { return "未采集" }
        return AIPresentation.tokens(measured.reduce(0) { $0 + ($1["cache_read_tokens"]?.intValue ?? 0) })
    }

    private func successRate(_ summary: JSONValue) -> String {
        let total = summary["request_count"]?.intValue ?? 0
        guard total > 0 else { return "暂无" }
        return "\(Int(round(Double(summary["success_count"]?.intValue ?? 0) / Double(total) * 100)))%"
    }

    private func reset() async {
        guard !resetting else { return }
        resetting = true; error = nil
        defer { resetting = false }
        do {
            _ = try await model.request(path: "/api/llm/usage/recent", method: "DELETE")
            await workspace.refresh(model)
        } catch { self.error = error.localizedDescription }
    }
}

struct AIUsageRow: View {
    let item: JSONValue
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(item["provider_name"]?.stringValue ?? "未知供应商").font(.subheadline.weight(.medium)).lineLimit(2)
                Spacer(minLength: 6)
                AIStatusLabel(title: item["success"]?.boolValue == true ? "成功" : "失败",
                    symbol: item["success"]?.boolValue == true ? "checkmark.circle.fill" : "xmark.circle.fill",
                    color: item["success"]?.boolValue == true ? .green : .red)
            }
            Text(item["model"]?.stringValue ?? "未记录模型").font(.caption).foregroundStyle(.secondary).lineLimit(2)
            HStack {
                Text(AIPresentation.usageSource(item["source"]?.stringValue))
                Spacer()
                Text("\(AIPresentation.tokens((item["input_tokens"]?.intValue ?? 0) + (item["output_tokens"]?.intValue ?? 0))) 词元")
                Text(AIPresentation.latency(item["latency_ms"]?.intValue))
            }.font(.caption2).foregroundStyle(.secondary).monospacedDigit()
            if item["used_fallback"]?.boolValue == true { Text("已改用备用模型").font(.caption2).foregroundStyle(.orange) }
            if let error = item["error_type"]?.stringValue, !error.isEmpty {
                Text(AIPresentation.status(error)).font(.caption).foregroundStyle(.red).lineLimit(2)
            }
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
    }
}

struct AIUsageDetailView: View {
    let item: JSONValue
    var body: some View {
        List {
            Section {
                LabeledContent("结果") {
                    Text(item["success"]?.boolValue == true ? "成功" : "失败")
                        .foregroundStyle(item["success"]?.boolValue == true ? .green : .red)
                }
                LabeledContent("时间", value: AIPresentation.date(item["created_at"]?.stringValue))
                LabeledContent("来源", value: AIPresentation.usageSource(item["source"]?.stringValue))
                LabeledContent("供应商", value: item["provider_name"]?.stringValue ?? "未记录")
                LabeledContent("模型", value: item["model"]?.stringValue ?? "未记录")
                if let key = item["provider_key_label"]?.stringValue, !key.isEmpty { LabeledContent("使用的密钥", value: key) }
                if let account = item["account_id"]?.intValue { LabeledContent("账号", value: "账号 #\(account)") }
            }
            Section("用量与传输") {
                LabeledContent("输入词元", value: AIPresentation.tokens(item["input_tokens"]?.intValue ?? 0))
                LabeledContent("输出词元", value: AIPresentation.tokens(item["output_tokens"]?.intValue ?? 0))
                LabeledContent("耗时", value: AIPresentation.latency(item["latency_ms"]?.intValue))
                LabeledContent("备用模型", value: item["used_fallback"]?.boolValue == true ? "首选失败，已改用备用模型" : "未使用")
                LabeledContent("客户端身份", value: item["client_identity_profile"]?.stringValue.map { AIPresentation.clientIdentity($0) } ?? "未记录")
                LabeledContent("调用方式", value: item["execution_backend"]?.stringValue.map { AIPresentation.executionBackend($0) } ?? "未记录")
                if let cache = item["cache_read_tokens"]?.intValue { LabeledContent("缓存命中词元", value: AIPresentation.tokens(cache)) }
                if let cache = item["cache_write_tokens"]?.intValue { LabeledContent("写入缓存词元", value: AIPresentation.tokens(cache)) }
            }
            if let error = item["error_type"]?.stringValue, !error.isEmpty {
                Section("错误类型") { Text(AIPresentation.status(error)).foregroundStyle(.red) }
            }
            if let preview = item["request_preview"]?.stringValue, !preview.isEmpty {
                Section("请求预览") { Text(AssistantRedaction.text(preview)).textSelection(.enabled) }
            }
            if let preview = item["response_preview"]?.stringValue, !preview.isEmpty {
                Section(item["success"]?.boolValue == true ? "返回预览" : "错误预览") {
                    Text(AssistantRedaction.text(preview)).textSelection(.enabled)
                }
            }
            if item["execution_backend"]?.stringValue == "codex_gateway" {
                Section("内置网关诊断") {
                    LabeledContent("网关版本", value: item["gateway_version"]?.stringValue ?? "未记录")
                    LabeledContent("处理阶段", value: item["gateway_stage"]?.stringValue ?? "未记录")
                    LabeledContent("请求编号", value: item["gateway_request_id"]?.stringValue ?? "未记录")
                }
            }
        }
        .navigationTitle("调用详情").navigationBarTitleDisplayMode(.inline)
    }
}
