import SwiftUI
import TelePilotCore

enum ManagementSchema {
    static func allowsNull(_ schema: JSONValue) -> Bool {
        schema["type"]?.stringValue == "null"
            || (schema["type"]?.arrayValue?.contains(.string("null")) ?? false)
            || ((schema["anyOf"]?.arrayValue ?? schema["oneOf"]?.arrayValue ?? []).contains { $0["type"]?.stringValue == "null" })
    }
    /// 不可为空的布尔字段：表单里只用一个开关表示它的值，缺省即为关闭（或 schema 默认值）。
    static func isPlainBoolean(_ raw: JSONValue) -> Bool {
        normalized(raw)["type"]?.stringValue == "boolean" && !allowsNull(raw)
    }
    static func inputValue(_ raw: JSONValue) -> JSONValue {
        let schema = normalized(raw)
        if let value = schema["default"], !value.isNull { return value }
        if let choice = schema["enum"]?.arrayValue?.first { return choice }
        switch schema["type"]?.stringValue {
        case "integer": return .integer(Int64(schema["minimum"]?.intValue ?? 0))
        case "number": return .number(schema["minimum"]?.doubleValue ?? 0)
        case "boolean": return .bool(false)
        case "array": return .array([])
        case "object": return initial(schema)
        default: return .string("")
        }
    }
    static func normalized(_ schema: JSONValue) -> JSONValue {
        guard let branches = schema["anyOf"]?.arrayValue ?? schema["oneOf"]?.arrayValue,
              let branch = branches.first(where: { $0["type"]?.stringValue != "null" }) else { return schema }
        return .object((schema.objectValue ?? [:]).merging(branch.objectValue ?? [:]) { _, new in new })
    }
    static func initial(_ raw: JSONValue, seed: JSONValue? = nil, defaults: Bool = true) -> JSONValue {
        let schema = normalized(raw)
        if let properties = schema["properties"]?.objectValue {
            var result: [String: JSONValue] = [:]
            let required = Set(schema["required"]?.arrayValue?.compactMap(\.stringValue) ?? [])
            for (key, spec) in properties where spec["readOnly"]?.boolValue != true {
                if let value = seed?[key], !value.isNull {
                    if ManagementText.secret(key) { continue }
                    result[key] = value
                } else if defaults, let value = spec["default"], !value.isNull { result[key] = value }
                else if required.contains(key) { result[key] = initial(spec, defaults: defaults) }
            }
            return .object(result)
        }
        if let seed, !seed.isNull { return seed }
        if let value = schema["default"] { return value }
        switch schema["type"]?.stringValue {
        case "object": return .object([:])
        case "array": return .array([])
        case "boolean": return .bool(false)
        case "integer", "number": return .null
        default: return .string("")
        }
    }
    static func validate(_ value: JSONValue, schema raw: JSONValue, name: String = "") -> String? {
        let schema = normalized(raw)
        if value.isNull { return allowsNull(raw) ? nil : "请填写\(name)。" }
        if schema["x-telepilot-kind"]?.stringValue == "scheduler" {
            let kind = value["kind"]?.stringValue ?? ""
            if kind == "cron", value["cron"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != false { return "请填写 Cron 时间表达式。" }
            if kind == "once", value["fire_at"]?.stringValue?.isEmpty != false { return "请选择单次触发时间。" }
            if kind == "interval", (value["interval_sec"]?.intValue ?? 0) <= 0 { return "间隔秒数必须大于零。" }
            let action = value["action"]
            let type = action?["type"]?.stringValue ?? ""
            if ["send_message", "call_llm", "agent_prompt"].contains(type), action?["target_chat_id"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != false { return "请填写目标会话。" }
            let field = type == "send_message" ? "text" : type == "run_command" ? "command" : "prompt"
            if action?[field]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != false { return "请填写\(ManagementText.label(field))。" }
        }
        if let object = schema["properties"]?.objectValue {
            let required = schema["required"]?.arrayValue?.compactMap(\.stringValue) ?? []
            for key in required {
                guard let item = value[key] else { return "请填写\(ManagementText.label(key))。" }
                if item.isNull, !allowsNull(object[key] ?? .object([:])) { return "请填写\(ManagementText.label(key))。" }
            }
            for (key, spec) in object {
                if let item = value[key], let error = validate(item, schema: spec, name: ManagementText.fieldLabel(key, schema: spec)) { return error }
            }
        }
        if let string = value.stringValue {
            if let minimum = schema["minLength"]?.intValue, string.count < minimum { return "\(name)至少需要 \(minimum) 个字符。" }
            if let maximum = schema["maxLength"]?.intValue, string.count > maximum { return "\(name)最多允许 \(maximum) 个字符。" }
        }
        if let number = value.doubleValue {
            if let minimum = schema["minimum"]?.doubleValue, number < minimum { return "\(name)不能小于 \(minimum)。" }
            if let maximum = schema["maximum"]?.doubleValue, number > maximum { return "\(name)不能大于 \(maximum)。" }
        }
        if let choices = schema["enum"]?.arrayValue, !choices.contains(value) { return "请选择\(name)。" }
        if let constant = schema["const"], value != constant { return "请输入确认口令：\(ManagementText.scalar(constant))。" }
        if let items = value.arrayValue {
            if let minimum = schema["minItems"]?.intValue, items.count < minimum { return "\(name)至少需要 \(minimum) 项。" }
            if let maximum = schema["maxItems"]?.intValue, items.count > maximum { return "\(name)最多允许 \(maximum) 项。" }
            if let itemSchema = schema["items"] {
                for item in items { if let error = validate(item, schema: itemSchema, name: name) { return error } }
            }
        }
        return nil
    }
}

struct ManagementFormView: View {
    let action: ManagementAction
    var onSuccess: ((JSONValue) -> Void)? = nil
    @Environment(AppModel.self) private var model
    @State private var schema: JSONValue = .object([:])
    @State private var draft: JSONValue = .object([:])
    @State private var result: JSONValue?
    @State private var error: String?
    @State private var busy = false
    @State private var confirming = false
    @State private var uncertain = false
    @State private var initialized = false
    @State private var expectsBody = false

    var body: some View {
        Form {
            Section {
                Text(action.explanation).font(.subheadline).foregroundStyle(.secondary)
            }
            ManagementFieldsView(schema: schema, value: $draft)
            if let error { Section { NoticeView(title: uncertain ? "结果待确认" : "操作未完成", message: error) } }
            if let result {
                Section("操作结果") { ManagementValueView(value: result) }
                if let job = result["job_id"]?.stringValue {
                    NavigationLink("查看任务进度与日志") {
                        ManagementJobView(id: job, updateJob: action.path == "/api/system/pull-update")
                    }
                }
                if let run = result["run_id"]?.stringValue, action.path.contains("/liveness/") {
                    NavigationLink("查看测活进度") { ManagementJobView(id: run, liveness: true) }
                }
            }
            Section {
                Button(action.title, role: action.destructive ? .destructive : nil) {
                    if let validation = ManagementSchema.validate(draft, schema: schema) { error = validation; return }
                    confirming = true
                }
                .disabled(busy || uncertain || !initialized)
                .frame(minHeight: 44)
                .accessibilityIdentifier("management.submit")
                .confirmationDialog(action.title + "？", isPresented: $confirming, titleVisibility: .visible) {
                    Button("确认" + action.title, role: action.destructive ? .destructive : nil) { Task { await submit() } }
                    Button("取消", role: .cancel) {}
                } message: { Text(action.explanation) }
                if busy { ProgressView("正在提交到服务器…") }
                if uncertain { Text("请返回列表刷新，确认服务器上的实际结果后再操作。").foregroundStyle(.secondary) }
            }
        }
        .navigationTitle(action.title).navigationBarTitleDisplayMode(.inline)
        .task {
            guard !initialized else { return }
            let contract = model.catalog.operation(path: action.path, method: action.method)
            let raw = action.schema ?? contract?.requestSchema ?? .object([:])
            schema = raw.objectValue?.isEmpty == true ? .object(["type": .string("object"), "properties": .object([:])]) : raw
            expectsBody = action.schema != nil || contract?.contentType == "application/json"
            if let seed = action.seed, var object = schema.objectValue,
               let required = object["required"]?.arrayValue {
                object["required"] = .array(required.filter { field in
                    guard let key = field.stringValue else { return true }
                    return !ManagementText.secret(key) || seed[key] == nil || seed[key]?.isNull == true
                })
                schema = .object(object)
            }
            draft = ManagementSchema.initial(schema, seed: action.seed, defaults: !["PUT", "PATCH"].contains(action.method))
            initialized = true
        }
    }

    private func submit() async {
        guard !busy, !uncertain else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            let wrapped = action.envelope.map { JSONValue.object([$0: draft]) } ?? draft
            let body = JSONValue.object((wrapped.objectValue ?? [:]).merging(action.supplementalBody) { _, new in new })
            let response = try await model.request(path: action.path, method: action.method, body: expectsBody ? body : nil)
            result = response
            onSuccess?(response)
        } catch {
            self.error = error.localizedDescription
            uncertain = (error as? APIError)?.isOutcomeUncertain == true
        }
    }
}

struct ManagementFieldsView: View {
    let schema: JSONValue
    @Binding var value: JSONValue
    var body: some View {
        let schema = ManagementSchema.normalized(schema)
        let properties = schema["properties"]?.objectValue ?? [:]
        let required = Set(schema["required"]?.arrayValue?.compactMap(\.stringValue) ?? [])
        if !properties.isEmpty {
            ForEach(properties.keys.sorted { lhs, rhs in
                if required.contains(lhs) != required.contains(rhs) { return required.contains(lhs) }
                return lhs < rhs
            }, id: \.self) { key in
                if let spec = properties[key], spec["readOnly"]?.boolValue != true {
                    // 可选布尔字段本身就是开关：不再额外渲染“是否提交此项”的同名开关，
                    // 否则会出现两个同名开关（一个开、一个关），且实际提交的是用户没注意到的那个值。
                    let inlineToggle = ManagementSchema.isPlainBoolean(spec)
                    Section {
                        if !required.contains(key), !inlineToggle {
                            Toggle(isOn: included(key, spec: spec)) { Text(ManagementText.fieldLabel(key, schema: spec)) }
                        }
                        if required.contains(key) || inlineToggle || value[key] != nil {
                            if value[key]?.isNull == true, ManagementSchema.allowsNull(spec) {
                                Text("此项将设为空，使用服务器默认值或解除关联。").font(.footnote).foregroundStyle(.secondary)
                                Button("填写" + ManagementText.fieldLabel(key, schema: spec)) { field(key).wrappedValue = ManagementSchema.inputValue(spec) }
                            } else {
                                ManagementFieldView(title: ManagementText.fieldLabel(key, schema: spec), key: key, schema: spec, value: field(key))
                                if ManagementSchema.allowsNull(spec) {
                                    Button("清空此项 / 设为空", role: .destructive) { field(key).wrappedValue = .null }
                                }
                            }
                        }
                    } header: { if required.contains(key) { Text(ManagementText.fieldLabel(key, schema: spec) + " · 必填") } } footer: {
                        if let description = spec["description"]?.stringValue { Text(description) }
                        if let constant = spec["const"] { Text("请输入 \(ManagementText.scalar(constant)) 以确认。") }
                    }
                }
            }
        } else if schema["additionalProperties"]?.boolValue == true || schema["additionalProperties"]?.objectValue != nil
                    || (schema["properties"] == nil && value.objectValue?.isEmpty == false) {
            ManagementDictionaryView(value: $value, itemSchema: schema["additionalProperties"])
        }
    }
    private func field(_ key: String) -> Binding<JSONValue> {
        Binding(get: { value[key] ?? .null }, set: { new in
            var object = value.objectValue ?? [:]; object[key] = new; value = .object(object)
        })
    }
    private func included(_ key: String, spec: JSONValue) -> Binding<Bool> {
        Binding(get: { value[key] != nil }, set: { include in
            var object = value.objectValue ?? [:]
            object[key] = include ? ManagementSchema.inputValue(spec) : nil
            value = .object(object)
        })
    }
}

struct ManagementFieldView: View {
    let title: String
    var key = ""
    let schema: JSONValue
    @Binding var value: JSONValue
    @State private var inputError: String?

    var body: some View {
        let spec = ManagementSchema.normalized(schema)
        Group {
            if let choices = spec["enum"]?.arrayValue {
                Picker(title, selection: $value) {
                    if value.isNull { Text("请选择").tag(JSONValue.null) }
                    ForEach(choices, id: \.self) { choice in Text(ManagementText.scalar(choice)).tag(choice) }
                    // 保留不在候选里的旧值（例如插件升级后移除了某个模型），避免选择器显示为空。
                    if !value.isNull, !choices.contains(value) { Text("当前值：\(ManagementText.scalar(value))").tag(value) }
                }
            } else if spec["type"]?.stringValue == "boolean" || value.boolValue != nil {
                Toggle(title, isOn: Binding(get: { value.boolValue ?? false }, set: { value = .bool($0) }))
            } else if spec["type"]?.stringValue == "object" || spec["properties"] != nil || value.objectValue != nil {
                NavigationLink {
                    Form { ManagementFieldsView(schema: spec, value: $value) }.navigationTitle(title).navigationBarTitleDisplayMode(.inline)
                } label: { LabeledContent("编辑" + title, value: ManagementText.scalar(value)) }
            } else if spec["type"]?.stringValue == "array" || value.arrayValue != nil {
                NavigationLink {
                    ManagementArrayView(title: title, schema: spec["items"] ?? .object(["type": .string("string")]), value: $value)
                } label: { LabeledContent("编辑" + title, value: ManagementText.scalar(value)) }
            } else if spec["format"]?.stringValue == "date-time" {
                ManagementDateField(title: title, value: $value)
            } else if ManagementText.secret(key) || spec["format"]?.stringValue == "password" || spec["writeOnly"]?.boolValue == true {
                SecureField(title, text: stringBinding).textInputAutocapitalization(.never).autocorrectionDisabled().privacySensitive()
            } else if ["integer", "number"].contains(spec["type"]?.stringValue ?? "") {
                ManagementNumberField(title: title, integer: spec["type"]?.stringValue == "integer", value: $value)
            } else {
                TextField(title, text: stringBinding, axis: .vertical)
                    .lineLimit(1...8).textInputAutocapitalization(.never).autocorrectionDisabled()
            }
        }
    }
    private var stringBinding: Binding<String> {
        Binding(get: { value.stringValue ?? "" }, set: { value = .string($0) })
    }
}

struct ManagementNumberField: View {
    let title: String
    let integer: Bool
    @Binding var value: JSONValue
    @State private var text = ""
    @State private var invalid = false
    var body: some View {
        VStack(alignment: .leading) {
            TextField(title, text: $text).keyboardType(.numbersAndPunctuation)
                .onChange(of: text) { _, next in
                    if next.isEmpty { value = .null; invalid = false }
                    else if integer, let parsed = Int64(next) { value = .integer(parsed); invalid = false }
                    else if !integer, let parsed = Double(next), parsed.isFinite { value = .number(parsed); invalid = false }
                    else { value = .null; invalid = true }
                }
            if invalid { Text(integer ? "请输入有效的整数。" : "请输入有效的数字。").font(.caption).foregroundStyle(.red) }
        }.onAppear { if !value.isNull { text = ManagementText.scalar(value) } }
    }
}

struct ManagementDateField: View {
    let title: String
    @Binding var value: JSONValue
    @State private var date = Date()
    var body: some View {
        DatePicker(title, selection: $date, displayedComponents: [.date, .hourAndMinute])
            .onChange(of: date) { _, next in value = .string(next.ISO8601Format()) }
            .onAppear {
                if let text = value.stringValue, let parsed = ISO8601DateFormatter().date(from: text) { date = parsed }
                else { value = .string(date.ISO8601Format()) }
            }
    }
}

struct ManagementArrayView: View {
    let title: String
    let schema: JSONValue
    @Binding var value: JSONValue
    var body: some View {
        List {
            ForEach(Array((value.arrayValue ?? []).enumerated()), id: \.offset) { index, _ in
                ManagementFieldView(title: "第 \(index + 1) 项", schema: schema, value: item(index))
            }.onDelete { offsets in
                var array = value.arrayValue ?? []; array.remove(atOffsets: offsets); value = .array(array)
            }.onMove { offsets, destination in
                var array = value.arrayValue ?? []; array.move(fromOffsets: offsets, toOffset: destination); value = .array(array)
            }
            Button("添加项目", systemImage: "plus") {
                var array = value.arrayValue ?? []; array.append(ManagementSchema.initial(schema)); value = .array(array)
            }
        }.navigationTitle(title).navigationBarTitleDisplayMode(.inline).toolbar { EditButton() }
    }
    private func item(_ index: Int) -> Binding<JSONValue> {
        Binding(get: { (value.arrayValue ?? []).indices.contains(index) ? value.arrayValue![index] : .null }, set: { new in
            var array = value.arrayValue ?? []; guard array.indices.contains(index) else { return }; array[index] = new; value = .array(array)
        })
    }
}

struct ManagementDictionaryView: View {
    @Binding var value: JSONValue
    var itemSchema: JSONValue?
    @State private var newKey = ""
    @State private var newType = "string"
    var body: some View {
        ForEach((value.objectValue ?? [:]).keys.sorted(), id: \.self) { key in
            Section {
                ManagementFieldView(title: ManagementText.label(key), key: key, schema: itemSchema?.objectValue == nil ? inferred(value[key] ?? .null) : itemSchema!, value: field(key))
                Button("移除此字段", role: .destructive) {
                    var object = value.objectValue ?? [:]; object.removeValue(forKey: key); value = .object(object)
                }
            }
        }
        Section("添加字段") {
            TextField("字段名称", text: $newKey).textInputAutocapitalization(.never).autocorrectionDisabled()
            Picker("内容类型", selection: $newType) {
                Text("文本").tag("string"); Text("整数").tag("integer"); Text("数字").tag("number")
                Text("开关").tag("boolean"); Text("列表").tag("array"); Text("分组").tag("object")
            }
            Button("添加字段") {
                let key = newKey.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !key.isEmpty, value[key] == nil else { return }
                var object = value.objectValue ?? [:]
                object[key] = ManagementSchema.initial(itemSchema?.objectValue == nil ? .object(["type": .string(newType)]) : itemSchema!)
                value = .object(object); newKey = ""
            }.disabled(newKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || value[newKey] != nil)
        }
    }
    private func field(_ key: String) -> Binding<JSONValue> {
        Binding(get: { value[key] ?? .null }, set: { new in var object = value.objectValue ?? [:]; object[key] = new; value = .object(object) })
    }
    private func inferred(_ value: JSONValue) -> JSONValue {
        let type = value.objectValue != nil ? "object" : value.arrayValue != nil ? "array" : value.boolValue != nil ? "boolean" : value.intValue != nil ? "integer" : value.doubleValue != nil ? "number" : "string"
        return .object(["type": .string(type)])
    }
}
