import SwiftUI
import TelePilotCore

/// 本供应商的模型选项：启用的在前，保持服务器返回顺序。
enum AIModelTestOptions {
    static func models(_ provider: JSONValue) -> [JSONValue] {
        let all = provider["models"]?.arrayValue ?? []
        return all.filter { $0["enabled"]?.boolValue == true } + all.filter { $0["enabled"]?.boolValue != true }
    }

    static func title(_ item: JSONValue) -> String {
        if let label = item["label"]?.stringValue, !label.isEmpty { return label }
        return item["id"]?.stringValue ?? "未命名模型"
    }
}

/// 模型测试结果：成功/失败、耗时、实际模型、回复与可读的失败原因。
private struct AIModelTestResultSection: View {
    let title: String
    let result: JSONValue
    var replyKey = "preview"

    var body: some View {
        let ok = result["ok"]?.boolValue == true
        let empty = result["empty_response"]?.boolValue == true
        let code = AILivenessResultRow.httpStatus(result)
        Section(title) {
            LabeledContent("结果") {
                AIStatusLabel(title: ok ? (empty ? "空响应" : "可用") : "失败" + (code.map { " · HTTP \($0)" } ?? ""),
                              symbol: ok && !empty ? "checkmark.circle.fill" : "exclamationmark.circle.fill",
                              color: ok && !empty ? .green : (ok ? .orange : .red))
            }
            LabeledContent("耗时") { Text(AIPresentation.latency(result["latency_ms"]?.intValue)).monospacedDigit() }
            if let actual = result["model"]?.stringValue, !actual.isEmpty {
                LabeledContent("实际模型") { Text(actual).multilineTextAlignment(.trailing).textSelection(.enabled) }
            }
            let input = result["input_tokens"]?.intValue ?? 0, output = result["output_tokens"]?.intValue ?? 0
            if input > 0 || output > 0 {
                LabeledContent("词元用量") { Text("输入 \(input) · 输出 \(output)").monospacedDigit() }
            }
            if let reply = (result[replyKey]?.stringValue ?? result["preview"]?.stringValue), !reply.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("模型回复").font(.caption).foregroundStyle(.secondary)
                    Text(AssistantRedaction.text(reply)).textSelection(.enabled)
                }
            }
            if let category = result["error_category"]?.stringValue, !category.isEmpty, !ok {
                LabeledContent("错误分类", value: AIPresentation.status(category))
            }
            if let error = result["error"]?.stringValue, !error.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("错误原因").font(.caption).foregroundStyle(.secondary)
                    Text(AssistantRedaction.text(error)).foregroundStyle(.red).textSelection(.enabled)
                }
            }
            if let suggestion = result["suggestion"]?.stringValue, !suggestion.isEmpty, suggestion != result["error"]?.stringValue {
                VStack(alignment: .leading, spacing: 4) {
                    Text("处理建议").font(.caption).foregroundStyle(.secondary)
                    Text(AssistantRedaction.text(suggestion))
                }
            }
            if let requestID = result["upstream_request_id"]?.stringValue ?? result["client_request_id"]?.stringValue, !requestID.isEmpty {
                LabeledContent("请求编号") { Text(requestID).font(.caption.monospaced()).textSelection(.enabled) }
            }
        }
    }
}

// MARK: - 单个模型快速测试

/// 原生“单个模型快速测试”：从本供应商模型中选一个，服务器让它回复几个字，确认连通并显示耗时。
/// 网络中断时结果未知，不自动重试。
struct AIModelQuickTestView: View {
    let provider: JSONValue
    @Environment(AppModel.self) private var model
    @State private var selected: String
    @State private var busy = false
    @State private var result: JSONValue?
    @State private var testedModel = ""
    @State private var error: String?
    @State private var uncertain = false

    init(provider: JSONValue, initialModel: String? = nil) {
        self.provider = provider
        let options = AIModelTestOptions.models(provider).compactMap { $0["id"]?.stringValue }
        var choice = options.first ?? ""
        if let defaultModel = provider["default_model"]?.stringValue, options.contains(defaultModel) { choice = defaultModel }
        if let initialModel, options.contains(initialModel) { choice = initialModel }
        _selected = State(initialValue: choice)
    }

    private var base: String { "/api/commands/llm-providers/\(provider["id"]?.intValue ?? 0)" }
    private var options: [JSONValue] { AIModelTestOptions.models(provider) }

    var body: some View {
        Form {
            if options.isEmpty {
                Section {
                    NoticeView(title: "还没有模型", message: "请先在“模型清单”里同步或手动添加模型，再回来测试。", symbol: "tray")
                }
            } else {
                Section {
                    Picker("模型", selection: $selected) {
                        ForEach(options, id: \.self) { item in
                            let id = item["id"]?.stringValue ?? ""
                            VStack(alignment: .leading) {
                                Text(AIModelTestOptions.title(item))
                                Text(item["enabled"]?.boolValue == true ? id : id + "（未启用）")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            .tag(id)
                        }
                    }
                    .pickerStyle(.navigationLink)
                    .disabled(busy)
                    .accessibilityIdentifier("ai.quicktest.model")
                } footer: {
                    Text("服务器会让所选模型回复几个字，确认能否连通，并显示耗时。会产生极少量费用。")
                }
                Section {
                    Button {
                        Task { await run() }
                    } label: {
                        HStack {
                            Label(busy ? "正在测试…" : "开始测试", systemImage: "bolt.horizontal")
                            if busy { Spacer(); ProgressView() }
                        }
                    }
                    .disabled(busy || selected.isEmpty)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("ai.quicktest.run")
                }
            }
            if let error {
                Section {
                    NoticeView(title: uncertain ? "测试结果未知" : "测试未完成",
                               message: error + (uncertain ? "\n连接在请求途中中断，App 不会自动重试；需要时请再次手动测试。" : ""))
                }
            }
            if let result {
                AIModelTestResultSection(title: testedModel, result: result)
            }
        }
        .navigationTitle("单个模型快速测试").navigationBarTitleDisplayMode(.inline)
    }

    private func run() async {
        guard !busy, !selected.isEmpty else { return }
        busy = true; error = nil; uncertain = false; result = nil
        defer { busy = false }
        let target = selected
        do {
            result = try await model.request(path: base + "/test-model", method: "POST", body: ["model": .string(target)])
            testedModel = target
        } catch {
            self.error = error.localizedDescription
            uncertain = (error as? APIError)?.isOutcomeUncertain == true
        }
    }
}

// MARK: - 多模型对话测试

/// 原生“多模型对话测试”：多选本供应商模型，用同一句测试语并发测试真实对话效果；
/// 支持在本页连续对话（每个模型各自保留临时历史，不落库）。
struct AIModelChatTestView: View {
    let provider: JSONValue
    @Environment(AppModel.self) private var model
    @State private var selected: Set<String>
    @State private var message = "你好，请用一句话介绍你自己。"
    @State private var systemPrompt = AIModelChatTestView.defaultSystemPrompt
    @State private var maxTokens = 1200
    @State private var timeoutSeconds = 90
    @State private var keepHistory = true
    @State private var history: [String: [JSONValue]] = [:]
    @State private var rounds = 0
    @State private var busy = false
    @State private var confirming = false
    @State private var results: [JSONValue] = []
    @State private var lastMessage = ""
    @State private var error: String?
    @State private var uncertain = false

    static let defaultSystemPrompt = "你是一个自然、简洁的中文聊天助手。请像真实聊天一样直接回复用户，不要只返回 ping/pong。"
    private static let limit = 20

    init(provider: JSONValue, initialModels: [String] = []) {
        self.provider = provider
        let options = Set(AIModelTestOptions.models(provider).compactMap { $0["id"]?.stringValue })
        let initial = Set(initialModels).intersection(options)
        _selected = State(initialValue: initial.isEmpty ? Set(AIPresentation.enabledModels(provider).prefix(Self.limit)) : initial)
    }

    private var base: String { "/api/commands/llm-providers/\(provider["id"]?.intValue ?? 0)" }
    private var options: [JSONValue] { AIModelTestOptions.models(provider) }
    private var orderedSelection: [String] {
        options.compactMap { $0["id"]?.stringValue }.filter { selected.contains($0) }
    }
    private var trimmedMessage: String { message.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        Form {
            if options.isEmpty {
                Section {
                    NoticeView(title: "还没有模型", message: "请先在“模型清单”里同步或手动添加模型，再回来测试。", symbol: "tray")
                }
            } else {
                modelSection
                messageSection
                settingsSection
                runSection
            }
            if let error {
                Section {
                    NoticeView(title: uncertain ? "测试结果未知" : "测试未完成",
                               message: error + (uncertain ? "\n连接在请求途中中断，App 不会自动重试；需要时请再次手动发送。" : ""))
                }
            }
            if !results.isEmpty {
                Section {
                    Text(lastMessage).font(.subheadline).textSelection(.enabled)
                } header: { Text("第 \(rounds) 轮测试语") }
                ForEach(Array(results.enumerated()), id: \.offset) { _, item in
                    AIModelTestResultSection(title: item["requested_model"]?.stringValue ?? "未知模型", result: item, replyKey: "response")
                }
            }
        }
        .navigationTitle("多模型对话测试").navigationBarTitleDisplayMode(.inline)
    }

    private var modelSection: some View {
        Section {
            ForEach(options, id: \.self) { item in
                let id = item["id"]?.stringValue ?? ""
                let isOn = selected.contains(id)
                Button {
                    if isOn { selected.remove(id) } else if selected.count < Self.limit { selected.insert(id) }
                } label: {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(AIModelTestOptions.title(item)).foregroundStyle(.primary)
                            Text(item["enabled"]?.boolValue == true ? id : id + "（未启用）")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Image(systemName: isOn ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(isOn ? Color.accentColor : Color.secondary)
                            .imageScale(.large)
                    }
                    .contentShape(Rectangle())
                }
                .disabled(busy || (!isOn && selected.count >= Self.limit))
                .accessibilityAddTraits(isOn ? .isSelected : [])
                .accessibilityIdentifier("ai.chattest.model.\(id)")
            }
        } header: {
            HStack {
                Text("模型（已选 \(selected.count)）")
                Spacer()
                Button("全选启用") {
                    selected = Set(AIPresentation.enabledModels(provider).prefix(Self.limit))
                }
                .font(.caption).disabled(busy)
                Button("清空") { selected.removeAll() }
                    .font(.caption).disabled(busy || selected.isEmpty)
            }
        } footer: {
            Text("一次最多选 \(Self.limit) 个模型，会同时发送。")
        }
    }

    private var messageSection: some View {
        Section {
            TextField("测试语", text: $message, axis: .vertical)
                .lineLimit(2...6)
                .disabled(busy)
                .accessibilityIdentifier("ai.chattest.message")
        } header: { Text("测试语") } footer: {
            Text("模拟用户在 Telegram 里发出的一句话，最多 2000 字。")
        }
    }

    private var settingsSection: some View {
        Section {
            Toggle(isOn: $keepHistory) { Label("连续对话", systemImage: "text.bubble") }
                .disabled(busy)
            if rounds > 0 {
                Button(role: .destructive) {
                    history.removeAll(); rounds = 0; results = []
                } label: { Label("清空本页对话（已进行 \(rounds) 轮）", systemImage: "trash") }
                .disabled(busy)
            }
            DisclosureGroup {
                TextField("系统提示词", text: $systemPrompt, axis: .vertical).lineLimit(2...6)
                Picker("最长输出", selection: $maxTokens) {
                    Text("600 词元").tag(600)
                    Text("1200 词元").tag(1200)
                    Text("4000 词元").tag(4000)
                }
                Picker("超时", selection: $timeoutSeconds) {
                    Text("60 秒").tag(60)
                    Text("90 秒").tag(90)
                    Text("120 秒").tag(120)
                }
            } label: { Label("高级设置", systemImage: "slider.horizontal.3") }
            .disabled(busy)
        } footer: {
            Text("开启“连续对话”后，每个模型会带上本页之前的问答作为上下文；离开本页即清空，不会保存到服务器。")
        }
    }

    private var runSection: some View {
        Section {
            Button {
                confirming = true
            } label: {
                HStack {
                    Label(busy ? "正在等待模型回复…" : "发送测试", systemImage: "paperplane")
                    if busy { Spacer(); ProgressView() }
                }
            }
            .disabled(busy || selected.isEmpty || trimmedMessage.isEmpty || trimmedMessage.count > 2000
                      || systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .frame(minHeight: 44)
            .accessibilityIdentifier("ai.chattest.run")
            .confirmationDialog("发送对话测试？", isPresented: $confirming, titleVisibility: .visible) {
                Button("确认发送") { Task { await run() } }
                Button("取消", role: .cancel) {}
            } message: {
                Text("将向 \(selected.count) 个模型各发送一次真实对话请求，可能产生上游费用。")
            }
        }
    }

    private func run() async {
        guard !busy, !selected.isEmpty, !trimmedMessage.isEmpty else { return }
        busy = true; error = nil; uncertain = false
        defer { busy = false }
        let models = orderedSelection
        let text = trimmedMessage
        var payload: [String: JSONValue] = [
            "models": .array(models.map { .string($0) }),
            "message": .string(text),
            "system_prompt": .string(systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines)),
            "max_tokens": .integer(Int64(maxTokens)),
            "timeout_seconds": .integer(Int64(timeoutSeconds))
        ]
        if keepHistory {
            var byModel: [String: JSONValue] = [:]
            for id in models { if let turns = history[id], !turns.isEmpty { byModel[id] = .array(Array(turns.suffix(20))) } }
            if !byModel.isEmpty { payload["history_by_model"] = .object(byModel) }
        }
        do {
            let response = try await model.request(path: base + "/chat-test-models", method: "POST", body: .object(payload))
            let items = response["results"]?.arrayValue ?? []
            results = items
            lastMessage = text
            rounds += 1
            if keepHistory {
                for item in items where item["ok"]?.boolValue == true {
                    guard let id = item["requested_model"]?.stringValue,
                          let reply = item["response"]?.stringValue ?? item["preview"]?.stringValue, !reply.isEmpty else { continue }
                    var turns = history[id] ?? []
                    turns.append(["role": "user", "content": .string(String(text.prefix(4000)))])
                    turns.append(["role": "assistant", "content": .string(String(reply.prefix(4000)))])
                    history[id] = Array(turns.suffix(20))
                }
            }
        } catch {
            self.error = error.localizedDescription
            uncertain = (error as? APIError)?.isOutcomeUncertain == true
        }
    }
}
