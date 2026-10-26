import SwiftUI
import TelePilotCore

/// 模型能力：只依据服务器返回的真实字段（supports_images / input_modalities / supports_tools /
/// reasoning_* / context_window / max_output_tokens），没有数据就是“未知”，不做推测。
/// 用词与 Web 管理台一致：多模态（图片）、工具调用、推理、上下文、输出。
struct AIModelCapabilities: Equatable {
    var images: Bool?
    var tools: Bool?
    var reasoning: Bool?
    var reasoningLevels: [String]
    var defaultReasoningLevel: String?
    var contextWindow: Int?
    var maxOutputTokens: Int?
    var webSearch: Bool?
    var apiFormats: [String]
    var source: String?

    init(_ model: JSONValue) {
        let inputs = (model["input_modalities"]?.arrayValue ?? []).compactMap(\.stringValue)
        if let declared = model["supports_images"]?.boolValue {
            images = declared
        } else if !inputs.isEmpty {
            images = inputs.contains("image")
        } else {
            images = nil
        }
        tools = model["supports_tools"]?.boolValue
        let levels = (model["supported_reasoning_levels"]?.arrayValue ?? model["reasoning_efforts"]?.arrayValue ?? [])
            .compactMap(\.stringValue)
        reasoningLevels = levels
        defaultReasoningLevel = model["default_reasoning_level"]?.stringValue
        let transport = model["reasoning_transport"]?.stringValue
        if !levels.isEmpty || (transport != nil && transport != "none") {
            reasoning = true
        } else if transport == "none" {
            reasoning = false
        } else {
            reasoning = nil
        }
        contextWindow = model["context_window"]?.intValue
        maxOutputTokens = model["max_output_tokens"]?.intValue
        webSearch = model["supports_web_search"]?.boolValue
        apiFormats = (model["supported_api_formats"]?.arrayValue ?? []).compactMap(\.stringValue)
        source = model["metadata_source"]?.stringValue
    }

    var allUnknown: Bool {
        images == nil && tools == nil && reasoning == nil && contextWindow == nil && maxOutputTokens == nil
    }

    // MARK: 文案

    var inputText: String {
        switch images {
        case true?: "多模态（图片）"
        case false?: "仅文本"
        case nil: "未知"
        }
    }

    var toolsText: String {
        switch tools {
        case true?: "支持"
        case false?: "不支持"
        case nil: "未知"
        }
    }

    var reasoningText: String {
        guard let reasoning else { return "未知" }
        guard reasoning else { return "不支持" }
        guard !reasoningLevels.isEmpty else { return "支持" }
        var text = "支持（" + reasoningLevels.map(Self.level).joined(separator: "、")
        if let defaultReasoningLevel { text += "；默认" + Self.level(defaultReasoningLevel) }
        return text + "）"
    }

    var webSearchText: String {
        switch webSearch {
        case true?: "支持"
        case false?: "不支持"
        case nil: "未知"
        }
    }

    var contextText: String { contextWindow.map { Self.tokens($0) + " 词元" } ?? "未知" }
    var outputText: String { maxOutputTokens.map { "最多 " + Self.tokens($0) + " 词元" } ?? "未知" }

    var sourceText: String {
        switch source {
        case "upstream": "供应商模型列表"
        case "probe": "探测结果"
        case "manual": "手动填写"
        default: "未记录"
        }
    }

    /// 列表行里的一行简短能力摘要；全部未知时只显示“能力未知”。
    var summary: String {
        if allUnknown { return "能力未知" }
        var parts: [String] = []
        switch images {
        case true?: parts.append("多模态（图片）")
        case false?: parts.append("仅文本")
        case nil: parts.append("图片输入未知")
        }
        switch tools {
        case true?: parts.append("工具调用")
        case false?: parts.append("无工具调用")
        case nil: parts.append("工具调用未知")
        }
        switch reasoning {
        case true?:
            if reasoningLevels.count > 1 {
                parts.append("推理 " + Self.level(reasoningLevels[0]) + "→" + Self.level(reasoningLevels[reasoningLevels.count - 1]))
            } else if let only = reasoningLevels.first {
                parts.append("推理 " + Self.level(only))
            } else {
                parts.append("推理")
            }
        case false?: parts.append("无推理")
        case nil: parts.append("推理未知")
        }
        parts.append("上下文 " + (contextWindow.map(Self.tokens) ?? "未知"))
        parts.append("输出 " + (maxOutputTokens.map { "≤" + Self.tokens($0) } ?? "未知"))
        return parts.joined(separator: " · ")
    }

    /// 与 Web 一致的紧凑写法：400K、1M、1.5M。
    static func tokens(_ value: Int) -> String {
        if value >= 1_000_000 {
            let millions = Double(value) / 1_000_000
            return (millions == millions.rounded() ? String(Int(millions)) : String(format: "%.1f", millions)) + "M"
        }
        if value >= 1000 {
            let thousands = Double(value) / 1000
            return (thousands == thousands.rounded() ? String(Int(thousands)) : String(format: "%.1f", thousands)) + "K"
        }
        return String(value)
    }

    static func level(_ raw: String) -> String {
        ["minimal": "极简", "low": "低", "medium": "中", "high": "高", "xhigh": "超高", "max": "最高"][raw] ?? raw
    }
}

/// 模型详情：基本信息、能力（真实字段，无数据为“未知”）与针对该模型的快速测试入口。
struct AIModelDetailView: View {
    let provider: JSONValue
    let item: JSONValue

    private var modelID: String { item["id"]?.stringValue ?? "" }

    var body: some View {
        let caps = AIModelCapabilities(item)
        List {
            Section {
                LabeledContent("模型 ID") { Text(modelID).textSelection(.enabled).multilineTextAlignment(.trailing) }
                if let label = item["label"]?.stringValue, !label.isEmpty { LabeledContent("显示名称", value: label) }
                LabeledContent("状态", value: item["enabled"]?.boolValue == true ? "已启用" : "未启用")
                LabeledContent("来源", value: item["custom"]?.boolValue == true ? "手动添加" : "从供应商发现")
                if provider["default_model"]?.stringValue == modelID { LabeledContent("默认模型", value: "是") }
            }
            Section {
                LabeledContent { Text(caps.inputText) } label: { Label("输入", systemImage: "photo") }
                LabeledContent { Text(caps.toolsText) } label: { Label("工具调用", systemImage: "wrench.and.screwdriver") }
                LabeledContent { Text(caps.reasoningText).multilineTextAlignment(.trailing) } label: { Label("推理", systemImage: "brain") }
                LabeledContent { Text(caps.contextText).monospacedDigit() } label: { Label("上下文", systemImage: "text.alignleft") }
                LabeledContent { Text(caps.outputText).monospacedDigit() } label: { Label("输出", systemImage: "text.append") }
                LabeledContent { Text(caps.webSearchText) } label: { Label("联网搜索", systemImage: "globe") }
                if !caps.apiFormats.isEmpty {
                    LabeledContent("可用接口") {
                        Text(caps.apiFormats.map(AIPresentation.apiFormat).joined(separator: "、")).multilineTextAlignment(.trailing)
                    }
                }
            } header: {
                Text("能力")
            } footer: {
                Text("能力信息来源：\(caps.sourceText)。“未知”表示服务器没有这项数据，不代表不支持；可在“编辑供应商”中手动填写。")
            }
            Section {
                NavigationLink { AIModelQuickTestView(provider: provider, initialModel: modelID) } label: {
                    Label("快速测试此模型", systemImage: "bolt.horizontal")
                }
                NavigationLink { AIModelChatTestView(provider: provider, initialModels: [modelID]) } label: {
                    Label("对话测试此模型", systemImage: "bubble.left.and.bubble.right")
                }
            } header: { Text("测试") } footer: { Text("测试会向供应商发送真实请求，可能产生少量费用。") }
        }
        .navigationTitle(AIModelTestOptions.title(item))
        .navigationBarTitleDisplayMode(.inline)
    }
}
