import Foundation
import Testing
@testable import TelePilotCore

/// 用插件库（Anoyou/telebot-plugins @ e8594a76）真实 plugin.json 中的模型相关字段，
/// 验证 iOS 插件配置表单对 x-ui-widget 的识别与候选项生成，与 Web ConfigDialog 保持一致。
struct PluginSelectWidgetTests {
    static let manifests: JSONValue = {
        let raw = #"""
{
  "ai-chat": {
    "telepilot_model": {
      "default": "",
      "title": "TelePilot 模型覆盖（可选）",
      "type": "string",
      "x-ui-model-modality": "text",
      "x-ui-provider-field": "telepilot_provider",
      "x-ui-widget": "llm-model-select"
    },
    "telepilot_provider": {
      "default": "",
      "title": "固定 TelePilot Provider（可选）",
      "type": "string",
      "x-ui-widget": "llm-provider-select"
    }
  },
  "ai_redpacket": {
    "question_bank_id": {
      "default": "",
      "title": "默认题库",
      "type": "string",
      "x-ui-columns": 2,
      "x-ui-options-field": "question_bank_options",
      "x-ui-order": 20,
      "x-ui-section": "基础设置",
      "x-ui-widget": "dynamic-select"
    },
    "question_bank_options": {
      "default": [],
      "items": {
        "additionalProperties": false,
        "properties": {
          "label": {
            "type": "string"
          },
          "value": {
            "type": "string"
          }
        },
        "required": [
          "value",
          "label"
        ],
        "type": "object"
      },
      "title": "题库选项",
      "type": "array",
      "x-ui-hidden": true
    },
    "telepilot_model": {
      "default": "",
      "title": "题库生成模型（可选）",
      "type": "string",
      "x-ui-columns": 2,
      "x-ui-model-modality": "text",
      "x-ui-order": 210,
      "x-ui-provider-field": "telepilot_provider",
      "x-ui-section": "AI 生成",
      "x-ui-widget": "llm-model-select"
    },
    "telepilot_provider": {
      "default": "",
      "title": "题库生成 Provider（可选）",
      "type": "string",
      "x-ui-columns": 2,
      "x-ui-order": 200,
      "x-ui-section": "AI 生成",
      "x-ui-widget": "llm-provider-select"
    }
  },
  "chatgpt_image": {
    "default_model": {
      "default": "gpt-image-2",
      "enum": [
        "gpt-image-2",
        "codex-gpt-image-2",
        "auto",
        "gpt-5",
        "gpt-5-1",
        "gpt-5-2",
        "gpt-5-3",
        "gpt-5-3-mini",
        "gpt-5-mini"
      ],
      "level": "account",
      "title": "默认图片模型",
      "type": "string"
    }
  },
  "codex_image": {
    "image_model": {
      "default": "auto",
      "enum": [
        "auto",
        "gpt-image-2",
        "gpt-image-1.5",
        "gpt-image-1",
        "gpt-image-1-mini"
      ],
      "level": "account",
      "title": "底层图片模型",
      "type": "string"
    },
    "model": {
      "default": "gpt-5.5",
      "enum": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "o3",
        "gpt-5",
        "gpt-5-nano",
        "gpt-5.2",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.5"
      ],
      "level": "account",
      "title": "主模型",
      "type": "string"
    }
  },
  "interaction-ai-chat": {
    "telepilot_model": {
      "default": "",
      "title": "TelePilot 模型覆盖（可选）",
      "type": "string",
      "x-ui-model-modality": "text",
      "x-ui-provider-field": "telepilot_provider",
      "x-ui-widget": "llm-model-select"
    },
    "telepilot_provider": {
      "default": "",
      "title": "固定 TelePilot Provider（可选）",
      "type": "string",
      "x-ui-widget": "llm-provider-select"
    }
  },
  "quick_qa": {
    "telepilot_model": {
      "default": "",
      "title": "TelePilot 模型覆盖（可选）",
      "type": "string",
      "x-ui-model-modality": "text",
      "x-ui-provider-field": "telepilot_provider",
      "x-ui-widget": "llm-model-select"
    },
    "telepilot_provider": {
      "default": "",
      "title": "固定 TelePilot Provider（可选）",
      "type": "string",
      "x-ui-widget": "llm-provider-select"
    }
  },
  "sum": {
    "telepilot_model": {
      "default": "",
      "title": "TelePilot 模型覆盖（高级，可选）",
      "type": "string",
      "x-ui-model-modality": "text",
      "x-ui-provider-field": "telepilot_provider",
      "x-ui-widget": "llm-model-select"
    },
    "telepilot_provider": {
      "default": "",
      "title": "固定 TelePilot Provider（高级，可选）",
      "type": "string",
      "x-ui-widget": "llm-provider-select"
    }
  }
}
"""#
        return try! JSONValue(data: Data(raw.utf8))
    }()

    /// 与 `/api/commands/llm-providers`（LLMProviderOut）同形的样例数据。
    static let providers: [JSONValue] = [
        ["id": 3, "name": "个人模型网关", "provider": "openai", "default_model": "gpt-5.4-mini", "tags": ["chat"],
         "has_effective_key": true,
         "models": [["id": "gpt-5.4-mini", "label": "GPT-5.4 mini", "enabled": true], ["id": "gpt-5.4", "enabled": true],
                    ["id": "gpt-image-2", "enabled": true], ["id": "dall-e-3", "enabled": true], ["id": "o3", "enabled": false]]],
        ["id": 7, "name": "本地 Ollama", "provider": "ollama", "default_model": "qwen3:14b", "has_effective_key": false,
         "models": [["id": "qwen3:14b", "enabled": true]]],
    ]

    @Test(arguments: ["ai-chat", "ai_redpacket", "quick_qa", "sum", "interaction-ai-chat"])
    func telepilotProviderAndModelFieldsBecomePickers(plugin: String) throws {
        let fields = try #require(Self.manifests[plugin])
        let provider = try #require(fields["telepilot_provider"])
        let model = try #require(fields["telepilot_model"])
        #expect(PluginSelectWidget(spec: provider) == .llmProvider)
        let widget = try #require(PluginSelectWidget(spec: model))
        #expect(widget == .llmModel(providerField: "telepilot_provider", modality: "text"))
        #expect(widget.needsProviders)

        let providerOptions = PluginSelectOptions.providerOptions(Self.providers)
        #expect(providerOptions.map(\.value) == ["3", "7"])
        #expect(providerOptions[0].label.contains("个人模型网关"))
        #expect(!providerOptions[1].label.contains("未配置 API Key")) // Ollama 不需要 Key

        guard case .llmModel(let providerField, let modality) = widget else { return }
        // 配置里存的是 Provider id；旧配置里也可能是名称或 provider 类型。
        for selector in ["3", "个人模型网关", "openai"] {
            let draft: JSONValue = .object([providerField ?? "": .string(selector)])
            let matched = try #require(PluginSelectOptions.provider(Self.providers, selector: PluginSelectOptions.text(draft[providerField ?? ""])))
            let models = PluginSelectOptions.modelOptions(matched, modality: modality)
            // 仅已启用的文本模型：排除图片模型与已停用模型。
            #expect(models.map(\.value) == ["gpt-5.4-mini", "gpt-5.4"])
            #expect(models[0].label == "GPT-5.4 mini（gpt-5.4-mini）")
        }
        #expect(PluginSelectOptions.provider(Self.providers, selector: "") == nil)
        #expect(PluginSelectOptions.provider(Self.providers, selector: "已删除的 Provider") == nil)
    }

    @Test func redpacketQuestionBankUsesDynamicOptions() throws {
        let fields = try #require(Self.manifests["ai_redpacket"])
        let spec = try #require(fields["question_bank_id"])
        #expect(PluginSelectWidget(spec: spec) == .dynamic(optionsField: "question_bank_options"))
        let options = PluginSelectOptions.dynamicOptions([
            ["value": "idiom", "label": "成语题库"], ["id": "poem", "title": "古诗题库"], ["value": "idiom", "label": "重复"], ["label": "缺少值"],
        ])
        #expect(options == [.init(value: "idiom", label: "成语题库"), .init(value: "poem", label: "古诗题库")])
    }

    @Test(arguments: [("chatgpt_image", "default_model"), ("codex_image", "model"), ("codex_image", "image_model")])
    func externalImageModelsStayAsEnumPickers(plugin: String, key: String) throws {
        let spec = try #require(Self.manifests[plugin]?[key])
        // 这些字段用插件自带 token 的外部模型，不属于 TelePilot Provider：走通用 enum Picker。
        #expect(PluginSelectWidget(spec: spec) == nil)
        #expect((spec["enum"]?.arrayValue?.count ?? 0) > 1)
    }

    @Test func modalityFilterMatchesWeb() {
        #expect(PluginSelectOptions.matches("gpt-image-1", modality: "text") == false)
        #expect(PluginSelectOptions.matches("dall-e-3", modality: "text") == false)
        #expect(PluginSelectOptions.matches("flux-image-pro", modality: "text") == false)
        #expect(PluginSelectOptions.matches("gpt-image-1", modality: nil))
        #expect(PluginSelectOptions.matches("gpt-5.4", modality: "text"))
    }

    // MARK: - 统一模型列表（与 Web llmModelOptions.test.ts 同一组用例）

    static let unifiedProviders: [JSONValue] = [
        ["id": 7, "name": "猫羽", "provider": "openai",
         "models": [["id": "gpt-4o", "label": "GPT-4o", "enabled": true], ["id": "gpt-image-1", "enabled": true], ["id": "o3", "enabled": false]]],
        ["id": 3, "name": "DeepSeek", "provider": "deepseek",
         "models": [["id": "deepseek-chat", "enabled": true], ["id": "deepseek-chat", "enabled": true], "deepseek-reasoner"]],
    ]

    @Test func unifiedListGroupsEnabledTextModelsByProvider() {
        let selection = PluginSelectOptions.unifiedModelSelection(Self.unifiedProviders, providerValue: nil, modelValue: nil, modality: "text")
        #expect(selection.options.map { [$0.group, $0.label] } == [
            ["DeepSeek", "DeepSeek · deepseek-chat"],
            ["DeepSeek", "DeepSeek · deepseek-reasoner"],
            ["猫羽", "猫羽 · GPT-4o（gpt-4o）"],
        ])
        #expect(selection.selectedKey == PluginSelectOptions.autoRouteKey)
        #expect(selection.legacy == nil)
        #expect(selection.groups.map { "\($0.group):\($0.options.count)" } == ["DeepSeek:2", "猫羽:1"])
        #expect(PluginSelectOptions.autoRouteLabel == "自动路由（不指定）")
    }

    @Test func unifiedListKeepsImageModelsWithoutTextModality() {
        let selection = PluginSelectOptions.unifiedModelSelection(Self.unifiedProviders, providerValue: nil, modelValue: nil, modality: nil)
        #expect(selection.options.contains { $0.modelValue == "gpt-image-1" })
    }

    @Test func unifiedSelectionWritesProviderAndModelTogether() {
        let selection = PluginSelectOptions.unifiedModelSelection(Self.unifiedProviders, providerValue: "", modelValue: "", modality: "text")
        let picked = selection.resolve(PluginSelectOptions.unifiedModelKey(provider: "7", model: "gpt-4o"))
        #expect(picked?.provider == "7" && picked?.model == "gpt-4o")
        let auto = selection.resolve(PluginSelectOptions.autoRouteKey)
        #expect(auto?.provider == "" && auto?.model == "")
        #expect(selection.resolve("unknown") == nil)
    }

    @Test(arguments: ["7", "猫羽", "openai"])
    func unifiedSelectionMatchesProviderByIdNameOrType(selector: String) {
        let selection = PluginSelectOptions.unifiedModelSelection(Self.unifiedProviders, providerValue: .string(selector), modelValue: "gpt-4o", modality: "text")
        #expect(selection.selectedKey == PluginSelectOptions.unifiedModelKey(provider: "7", model: "gpt-4o"))
        #expect(selection.legacy == nil)
    }

    @Test func unifiedSelectionShowsLegacyValuesWithoutRewriting() {
        let disabled = PluginSelectOptions.unifiedModelSelection(Self.unifiedProviders, providerValue: "7", modelValue: "o3", modality: "text")
        #expect(disabled.legacy?.label == "当前值：猫羽 · o3")
        #expect(disabled.selectedKey == disabled.legacy?.key)
        let kept = disabled.resolve(disabled.selectedKey)
        #expect(kept?.provider == "7" && kept?.model == "o3")
        #expect(PluginSelectOptions.unifiedModelSelection(Self.unifiedProviders, providerValue: "9", modelValue: "x", modality: "text").legacy?.label == "当前值：9 · x")
        #expect(PluginSelectOptions.unifiedModelSelection(Self.unifiedProviders, providerValue: "3", modelValue: "", modality: "text").legacy?.label == "当前值：DeepSeek · 默认模型")
        #expect(PluginSelectOptions.unifiedModelSelection(Self.unifiedProviders, providerValue: "", modelValue: "orphan", modality: "text").legacy?.label == "当前值：orphan（未固定 Provider）")
    }

    @Test func unifiedSelectionWithoutProvidersOnlyOffersAutoRoute() {
        let selection = PluginSelectOptions.unifiedModelSelection([], providerValue: nil, modelValue: nil, modality: "text")
        #expect(selection.options.isEmpty)
        #expect(selection.selectedKey == PluginSelectOptions.autoRouteKey)
    }

    @Test func unifiedListUsesRealManifestFields() throws {
        // 真实插件 manifest 的模型字段 + 与 /api/commands/llm-providers 同形的数据：两个 Provider 的已启用文本模型都在列表里。
        let spec = try #require(Self.manifests["interaction-ai-chat"]?["telepilot_model"])
        guard case .llmModel(_, let modality) = try #require(PluginSelectWidget(spec: spec)) else { Issue.record("不是模型选择器"); return }
        let selection = PluginSelectOptions.unifiedModelSelection(Self.providers, providerValue: nil, modelValue: nil, modality: modality)
        #expect(selection.options.map(\.label) == ["个人模型网关 · GPT-5.4 mini（gpt-5.4-mini）", "个人模型网关 · gpt-5.4", "本地 Ollama · qwen3:14b"])
        #expect(selection.options.map(\.providerValue) == ["3", "3", "7"])
    }
}
