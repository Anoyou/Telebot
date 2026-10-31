import CryptoKit
import Foundation
import Observation
import TelePilotCore

struct AssistantItem: Identifiable, Hashable, Sendable {
    let value: JSONValue
    var id: String { value["id"]?.stringValue ?? value["id"]?.intValue.map(String.init) ?? "missing" }
    subscript(_ key: String) -> JSONValue? { value[key] }
    func text(_ key: String) -> String { value[key]?.stringValue ?? "" }
}

extension JSONValue {
    var assistantItems: [AssistantItem] { (arrayValue ?? []).map { AssistantItem(value: $0) } }
    func assistantText(_ key: String) -> String { self[key]?.stringValue ?? "" }
}

enum AssistantPresentation {
    static let root = "/api/system-agent"
    /// Agent 回答的发送者名称（第 22 轮由「助手」改为产品名，与 Web 一致）。
    static let senderName = "TelePilot"
    static let terminal: Set<String> = ["succeeded", "failed", "cancelled"]
    static func path(_ component: String) -> String {
        component.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? ""
    }
    static func status(_ value: String) -> String {
        ["queued": "排队中", "running": "执行中", "waiting_input": "等待补充", "waiting_approval": "等待审批",
         "succeeded": "已完成", "failed": "失败", "cancelled": "已取消", "pending": "待确认",
         "confirmed": "已确认", "executed": "已执行", "rejected": "已拒绝", "expired": "已过期",
         "active": "进行中", "archived": "已归档", "paused": "已暂停", "dispatching": "正在派发",
         "applied": "已应用", "not_required": "无需同步", "synced": "已同步", "normal": "普通",
         "high": "高风险", "critical": "危险", "completed": "已完成", "user": "我",
         "assistant": AssistantPresentation.senderName, "tool": "工具", "system": "系统"][value] ?? value
    }
    static func error(_ error: Error) -> String {
        (error as? APIError)?.errorDescription ?? "无法完成请求，请重新读取服务器状态后再试。"
    }
    static func eventTitle(_ value: JSONValue) -> String {
        let type = value.assistantText("type")
        return ["run_started": "开始执行", "provider_selected": "选择模型", "model_attempt": "模型调用",
                "tool_started": "调用工具", "tool_finished": "工具结果", "action_proposed": "待确认操作",
                "assistant_delta": "生成回答", "assistant_reasoning_delta": "推理中", "reasoning_delta": "推理中",
                "assistant_message": "最终回答", "assistant_delta_reset": "进入工具阶段", "error": "执行异常",
                "done": "本轮结束", "route_selected": "工具路由", "skill_selected": "加载能力",
                "retry_scheduled": "等待重试", "run_paused": "任务暂停", "input_applied": "已应用补充"] [type] ?? type
    }
    static func safeJSON(_ value: JSONValue, key: String = "") -> JSONValue {
        let lower = key.lowercased()
        let sensitive = ["secret", "password", "authorization", "api_key", "credential", "data_url"]
        if lower == "token" || lower.hasSuffix("_token") || lower.hasSuffix("_token_enc") || sensitive.contains(where: { lower.contains($0) }) { return .string("••••••") }
        switch value {
        case .object(let fields): return .object(fields.reduce(into: [:]) { $0[$1.key] = safeJSON($1.value, key: $1.key) })
        case .array(let values): return .array(values.map { safeJSON($0) })
        case .string(let text): return .string(AssistantRedaction.text(text))
        default: return value
        }
    }
}

@MainActor @Observable
final class AssistantStore {
    var sessions: [AssistantItem] = []
    /// 当前会话已加载的消息（按时间升序）。只通过 `setMessages` 修改，以便同步更新分组与预处理结果。
    private(set) var messages: [AssistantItem] = []
    /// 按轮次分组后的显示条目；消息变化时计算一次，渲染时不再重复分组。
    private(set) var entries: [AssistantDisplayEntry] = []
    /// 消息的预处理结果（脱敏、Markdown 解析），按消息 ID 与内容缓存，后台线程生成。
    private(set) var rendered: [String: AssistantRendered] = [:]
    /// 当前显示的第一条 `entries` 下标：打开会话时先显示最近一屏，其余逐帧向上追加（见 `AssistantReveal`）。
    private(set) var revealStart = 0
    /// 首次进入的读取流程（缓存 → 会话列表 → 当前会话）已经结束；之前不显示“空会话”提示。
    private(set) var initialLoadFinished = false
    /// 正在读取更早的消息（上滑到顶部时自动触发）。
    var loadingOlder = false
    /// 当前显示的是本机缓存，服务器刷新尚未完成。
    var showingCached = false
    var runs: [AssistantItem] = []
    var capabilities: JSONValue = [:]
    var sessionID: String?
    var session: JSONValue?
    var activeRun: JSONValue?
    /// 本轮收到的进度事件（只用于核对与调试，界面不读取，不参与观察）。
    @ObservationIgnored var events: [JSONValue] = []
    /// 正在生成的回答与进度阶段（第十一轮拆出的独立 Observable，只有回答位置的视图读取）。
    let live = AssistantLiveState()
    var notice: String?
    var error: String?
    var busy = false
    var loading = false
    var streaming = false
    var hasOlderMessages = true
    var unresolvedSend = false
    var includeArchived = false
    var includeBot = false
    var modelIndex = -1
    var accountID: Int?
    var deletingSessionID: String?
    /// 本会话尚未开始执行的排队消息（服务器 GET /queue，按 position 排序）。
    var queue: [AssistantItem] = []
    /// 已改为“插入调整”的消息：服务器把它并入当前任务（不单独存为历史消息），本机显示到当前任务结束。
    var steered: [AssistantSteeredMessage] = []
    /// 已提交“停止并替换”的排队消息（turn id）。
    var replacingTurnIDs: Set<String> = []
    /// 正在提交处理方式的排队消息（turn id）；同一时间只提交一个，不自动重试。
    var queueBusyID: String?
    @ObservationIgnored private var streamTask: Task<Void, Never>?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var lastSeq = 0
    @ObservationIgnored private var defaultsKey = ""
    @ObservationIgnored private var requestID: String?
    @ObservationIgnored private var subscriptionID = UUID()
    @ObservationIgnored private var cacheKey = ""
    @ObservationIgnored private var revealTask: Task<Void, Never>?
    @ObservationIgnored private var watchTask: Task<Void, Never>?
    @ObservationIgnored private var progressRunID: String?
    /// 最近一次“读取最近一页消息”的序号：并发的多次读取只采用最后发出的那次，避免较早的结果覆盖较新的。
    @ObservationIgnored private var messageLoadSequence = 0
    /// 每页消息数：先显示最近一页，上滑再按 before_id 读取更早的。
    static let pageSize = 40

    var runID: String? { activeRun?["id"]?.stringValue }
    var runStatus: String { activeRun?.assistantText("status") ?? "" }
    var runActive: Bool { !runStatus.isEmpty && !AssistantPresentation.terminal.contains(runStatus) }
    var models: [JSONValue] { (capabilities["model_matrix"]?.arrayValue ?? []).filter { $0["enabled"]?.boolValue != false && $0["provider_id"]?.intValue != nil && $0["model"]?.stringValue?.isEmpty == false } }
    var canSend: Bool { !busy && !unresolvedSend && capabilities["enabled"]?.boolValue == true && capabilities["ai_enabled"]?.boolValue != false && session?.assistantText("channel") != "bot" && session?.assistantText("status") != "archived" }
    var selectedModel: JSONValue { AgentSessionModel.selection(models: models, index: modelIndex) }

    /// 第 22 轮：每个会话记住上次选择的本轮模型。打开会话时恢复（服务器值优先，其次本机），
    /// 新会话为「自动」；所选模型已不在清单里时回落「自动」。模型清单晚于会话读到时，读到后再套用一次。
    @ObservationIgnored private var sessionModelSelection: JSONValue?
    @ObservationIgnored private weak var appModel: AppModel?
    private var sessionModelStore: AgentSessionModel.LocalStore { AgentSessionModel.LocalStore(scope: cacheKey) }

    private func applySessionModel(_ selection: JSONValue?) {
        sessionModelSelection = selection
        let index = AgentSessionModel.index(of: selection, in: models)
        if modelIndex != index { modelIndex = index }
    }

    /// 用户在输入栏选了模型：立即生效，并记到会话上（新服务器 PATCH 会话；同时本机按会话 id 记一份兜底）。
    func chooseModel(_ index: Int) {
        if modelIndex != index { modelIndex = index }
        let selection = selectedModel
        sessionModelSelection = selection
        guard let sessionID, session?.assistantText("channel") != "bot" else { return }
        sessionModelStore.save(sessionID, selection)
        guard let model = appModel, model.supportsAgentSessionModelSelection else { return }
        let path = AssistantPresentation.root + "/sessions/\(AssistantPresentation.path(sessionID))"
        Task { @MainActor [weak self] in
            // 记不住只影响下次打开时的默认值：失败不打扰用户（本机兜底已记下）。
            guard let row = try? await model.request(path: path, method: "PATCH", body: ["model_selection": selection]) else { return }
            guard let self, self.sessionID == sessionID, let saved = row["model_selection"] else { return }
            if var current = self.session?.objectValue {
                current["model_selection"] = saved
                self.session = .object(current)
            }
        }
    }

    func start(_ model: AppModel, initialID: String?) async {
        defer { initialLoadFinished = true }
        appModel = model
        defaultsKey = "telepilot.assistant.pending.\(model.serverURL?.absoluteString ?? "").\(model.user?.id ?? 0)"
        cacheKey = "\(model.serverURL?.absoluteString ?? "").\(model.user?.id ?? 0)"
        unresolvedSend = UserDefaults.standard.dictionary(forKey: defaultsKey) != nil
        let pending = UserDefaults.standard.dictionary(forKey: defaultsKey)?["session_id"] as? String
        // 先显示本机缓存（上次的会话列表与最近消息），再从服务器刷新。
        if sessions.isEmpty, messages.isEmpty, let snapshot = await AssistantHistoryCache.load(cacheKey) {
            sessions = snapshot.sessions.map { AssistantItem(value: $0) }
            let wanted = initialID ?? pending ?? snapshot.sessionID
            if let cachedID = snapshot.sessionID, wanted == cachedID, sessionID == nil {
                sessionID = cachedID
                session = snapshot.session
                accountID = snapshot.session?["account_id"]?.intValue
                hasOlderMessages = snapshot.hasOlderMessages
                await setMessages(snapshot.messages.map { AssistantItem(value: $0) })
                showingCached = true
            }
        }
        // 会话列表与能力并行读取。
        async let sessionsLoad: Bool = loadSessions(model)
        let capabilitiesPath = AssistantPresentation.root + "/capabilities"
        async let capabilitiesLoad = Self.capture { try await model.request(path: capabilitiesPath) }
        let sessionsLoaded = await sessionsLoad
        switch await capabilitiesLoad {
        case .success(let value):
            capabilities = value
            // 模型清单可能晚于缓存里的会话读到：按当前会话记住的选择重新对上下标。
            applySessionModel(sessionModelSelection)
        case .failure(let failure): self.error = AssistantPresentation.error(failure)
        }
        if let initialID { await select(initialID, model: model, keepVisible: initialID == sessionID) }
        else if let pending {
            await select(pending, model: model, keepVisible: pending == sessionID)
            await reconcileSend(model)
        } else if let current = sessionID, sessions.contains(where: { $0.id == current }) {
            await select(current, model: model, keepVisible: true)
        } else if let first = sessions.first { await select(first.id, model: model) }
        else {
            // 服务器已经没有任何会话（例如刚删除了最后一个）：清掉缓存里恢复出来的旧会话与消息并更新缓存，
            // 否则重新进入助手页会把已删除会话的内容显示出来。读取失败时保留缓存（只是暂时连不上）。
            if sessionsLoaded && sessionID != nil {
                await clearCurrentSession()
                saveCache()
            }
            showingCached = false
        }
    }

    private static func capture<T: Sendable>(_ operation: @Sendable () async throws -> T) async -> Result<T, Error> {
        do { return .success(try await operation()) } catch { return .failure(error) }
    }

    @discardableResult
    func loadSessions(_ model: AppModel) async -> Bool {
        loading = true
        defer { loading = false }
        do {
            let status = includeArchived ? "archived" : "active"
            // compact：服务器只返回列表需要的摘要字段（不含会话记忆），旧服务器会忽略该参数。
            sessions = try await model.request(path: AssistantPresentation.root + "/sessions?limit=200&status=\(status)&include_bot=\(includeBot)&compact=true").assistantItems
            return true
        } catch {
            self.error = AssistantPresentation.error(error)
            return false
        }
    }

    /// 打开会话：会话详情与最近一页消息并行读取。`keepVisible` 为真时保留当前显示的内容（例如缓存），避免闪烁。
    func select(_ id: String, model: AppModel, keepVisible: Bool = false) async {
        generation += 1
        streamTask?.cancel()
        streaming = false
        let keep = keepVisible && sessionID == id
        sessionID = id
        if !keep {
            session = nil
            await setMessages([])
            hasOlderMessages = true
        }
        events = []
        live.clearText()
        activeRun = nil
        lastSeq = 0
        error = nil
        resetQueueState()
        // 先按本机记的值恢复（立即显示），读到服务器会话后再以服务器值为准。
        let localModel = cacheKey.isEmpty ? nil : sessionModelStore.load(id)
        if !keep { applySessionModel(localModel) }
        let token = generation
        let base = AssistantPresentation.root + "/sessions/\(AssistantPresentation.path(id))"
        let messagesPath = base + "/messages?limit=\(Self.pageSize)"
        async let rowLoad = Self.capture { try await model.request(path: base) }
        async let messagesLoad = Self.capture { try await model.request(path: messagesPath) }
        let (rowResult, messagesResult) = await (rowLoad, messagesLoad)
        guard token == generation else { return }
        switch rowResult {
        case .success(let row):
            session = row
            accountID = row["account_id"]?.intValue
            applySessionModel(AgentSessionModel.restored(serverRow: row, serverSupports: model.supportsAgentSessionModelSelection, local: localModel))
        case .failure(let failure):
            self.error = AssistantPresentation.error(failure)
            showingCached = false
            return
        }
        switch messagesResult {
        case .success(let value):
            let rows = value.assistantItems
            await setMessages(rows)
            guard token == generation else { return }
            hasOlderMessages = rows.count >= Self.pageSize
            showingCached = false
            saveCache()
        case .failure(let failure):
            self.error = AssistantPresentation.error(failure)
            showingCached = false
        }
        await restoreRun(model)
    }

    /// 新建会话。第 20 轮：不再沿用当前会话的账号；只有 1 个账号时默认关联它（新服务器由服务器判断，
    /// 旧服务器由 App 按账号数代填），多账号保持「全局」。`accountID` 参数用于从某个账号的页面进入时带入该账号。
    func createSession(_ model: AppModel, accountID requested: Int? = nil) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let body = AgentSessionAccount.newSessionBody(
                requestedAccountID: requested,
                accountIDs: model.accountsLoaded ? model.accounts.map(\.id) : nil,
                serverSupportsDefault: model.supportsAgentSessionDefaultAccount
            )
            let created = try await model.request(path: AssistantPresentation.root + "/sessions", method: "POST", body: .object(body))
            guard let id = created["id"]?.stringValue else { throw APIError.incompatibleResponse }
            await loadSessions(model)
            await select(id, model: model)
            if AgentSessionAccount.wasAutoLinked(request: body, response: created),
               let linked = created["account_id"]?.intValue {
                let name = model.accounts.first(where: { $0.id == linked }).map { AccountPresentation.name($0) } ?? "#\(linked)"
                notice = "只有一个账号，新会话已自动关联「\(name)」；可在会话设置 › 账号上下文改回全局。"
            }
        } catch { self.error = AssistantPresentation.error(error) }
    }

    func updateSession(_ body: JSONValue, model: AppModel) async {
        guard let sessionID, !busy else { return }
        busy = true
        defer { busy = false }
        do {
            session = try await model.request(path: AssistantPresentation.root + "/sessions/\(AssistantPresentation.path(sessionID))", method: "PATCH", body: body)
            accountID = session?["account_id"]?.intValue
            await loadSessions(model)
        } catch { self.error = AssistantPresentation.error(error) }
    }

    /// 删除指定会话。写操作只提交一次：失败或结果未知时重新读取列表展示真实状态，不自动重试。
    @discardableResult
    func deleteSession(id: String, model: AppModel) async -> Bool {
        guard !busy, deletingSessionID == nil else { return false }
        guard sessions.first(where: { $0.id == id })?.text("channel") != "bot",
              !(sessionID == id && session?.assistantText("channel") == "bot") else {
            error = "Telegram 会话仅支持查看历史，不能在这里删除。"
            return false
        }
        if id == sessionID && runActive {
            error = "当前会话仍有任务在执行。请先停止任务，结束后再删除。"
            return false
        }
        busy = true
        deletingSessionID = id
        defer { busy = false; deletingSessionID = nil }
        error = nil
        var submitted = false
        do {
            // 删除前读取一次服务器任务列表，避免删除仍在执行任务的会话。
            let rows = try await model.request(path: AssistantPresentation.root + "/runs?limit=200&include_bot=true").assistantItems
            if rows.contains(where: { $0.text("session_id") == id && !AssistantPresentation.terminal.contains($0.text("status")) }) {
                error = "该会话仍有任务在执行。请先打开会话停止任务，结束后再删除。"
                return false
            }
            submitted = true
            _ = try await model.request(path: AssistantPresentation.root + "/sessions/\(AssistantPresentation.path(id))", method: "DELETE")
        } catch {
            let failure = AssistantPresentation.error(error)
            // 超时不代表删除失败；只读取一次目标，404 才能确认它已被删除。
            if submitted {
                do {
                    _ = try await model.request(path: AssistantPresentation.root + "/sessions/\(AssistantPresentation.path(id))")
                } catch APIError.server(status: 404, code: _) {
                    await finishDeletingSession(id, model: model)
                    return true
                } catch {}
            }
            await loadSessions(model)
            self.error = failure
            return false
        }
        await finishDeletingSession(id, model: model)
        return true
    }

    private func finishDeletingSession(_ id: String, model: AppModel) async {
        sessions.removeAll { $0.id == id }
        sessionModelStore.remove(id)
        let wasCurrent = id == sessionID
        if wasCurrent { await clearCurrentSession() }
        let token = generation
        await loadSessions(model)
        if wasCurrent, token == generation, sessionID == nil, let next = sessions.first(where: { $0.id != id }) {
            await select(next.id, model: model)
        }
        // 同步本机缓存：缓存里仍是已删除的会话与消息时，下次进入助手页（.task 会重新执行 start）会先把它们恢复出来。
        saveCache()
        notice = "会话已删除。"
    }

    /// 清空当前会话的全部本机状态（删除当前会话、或服务器已没有任何会话时）。
    private func clearCurrentSession() async {
        generation += 1
        streamTask?.cancel()
        sessionID = nil
        session = nil
        applySessionModel(nil)
        await setMessages([])
        runs = []
        events = []
        activeRun = nil
        accountID = nil
        live.clearText()
        streaming = false
        hasOlderMessages = false
        resetQueueState()
    }

    private func resetQueueState() {
        watchTask?.cancel()
        queue = []
        steered = []
        replacingTurnIDs = []
        queueBusyID = nil
        live.resetProgress()
        progressRunID = nil
    }

    /// 读取消息。`older` 为真时按 before_id 读取更早一页并拼到前面；否则读取最近一页，
    /// 与已加载的更早消息合并（不丢弃用户已经上滑加载的历史）。
    func refreshMessages(_ model: AppModel, older: Bool = false) async {
        guard let sessionID else { return }
        if older {
            guard !loadingOlder, hasOlderMessages else { return }
            loadingOlder = true
        }
        defer { if older { loadingOlder = false } }
        let token = generation
        let before = older ? messages.first?["id"]?.intValue.map { "&before_id=\($0)" } ?? "" : ""
        if !older { messageLoadSequence += 1 }
        let sequence = messageLoadSequence
        do {
            let rows = try await model.request(path: AssistantPresentation.root + "/sessions/\(AssistantPresentation.path(sessionID))/messages?limit=\(Self.pageSize)\(before)").assistantItems
            guard token == generation else { return }
            // 发送后“订阅进度”与“读取消息”并发（第十一轮）：若本轮很快结束，结束时的读取可能比发送时的读取先返回，
            // 较早发出的那次结果已过时，丢弃。
            if !older && sequence != messageLoadSequence { return }
            if older {
                let known = Set(messages.map(\.id))
                await setMessages(rows.filter { !known.contains($0.id) } + messages)
                hasOlderMessages = rows.count >= Self.pageSize
            } else {
                let firstNew = rows.first?["id"]?.intValue
                let kept = firstNew.map { first in messages.filter { ($0["id"]?.intValue ?? Int.max) < first } } ?? []
                await setMessages(kept + rows)
                if kept.isEmpty { hasOlderMessages = rows.count >= Self.pageSize }
            }
            guard token == generation else { return }
            saveCache()
        } catch { if token == generation { self.error = AssistantPresentation.error(error) } }
    }

    /// 更新消息：先在后台线程完成脱敏与 Markdown 预处理（只处理尚未缓存的消息），再一次性替换消息与分组。
    func setMessages(_ rows: [AssistantItem]) async {
        let missing = rows.filter { rendered[AssistantRendered.key(for: $0)] == nil && $0.text("role") != "tool" }
        if !missing.isEmpty {
            let prepared = await Task.detached(priority: .userInitiated) { missing.map(AssistantRendered.make) }.value
            var cache = rendered.count > 800 ? [:] : rendered
            for item in prepared { cache[item.key] = item }
            rendered = cache
        }
        var grouped = AssistantTurnGrouping.entries(rows)
        for index in grouped.indices {
            if let item = grouped[index].item { grouped[index].rendered = rendered[AssistantRendered.key(for: item)] }
        }
        let start = AssistantReveal.start(wasEmpty: entries.isEmpty, count: grouped.count, current: revealStart)
        messages = rows
        revealStart = start
        entries = grouped
        if start > 0 { scheduleReveal() } else { revealTask?.cancel() }
    }

    /// 逐帧把更早的消息加回列表顶部，直到全部显示。
    private func scheduleReveal() {
        revealTask?.cancel()
        revealTask = Task { @MainActor [weak self] in
            while true {
                try? await Task.sleep(for: .milliseconds(40))
                guard let self, !Task.isCancelled, self.revealStart > 0 else { return }
                self.revealStart = AssistantReveal.next(self.revealStart)
            }
        }
    }

    /// 保存最近一页消息与会话列表到本机缓存，下次进入先显示。
    private func saveCache() {
        guard !cacheKey.isEmpty else { return }
        let snapshot = AssistantHistoryCache.Snapshot(
            sessions: Array(sessions.prefix(200)).map(\.value),
            sessionID: sessionID,
            session: session,
            messages: Array(messages.suffix(Self.pageSize)).map(\.value),
            hasOlderMessages: messages.count > Self.pageSize || hasOlderMessages,
            savedAt: Date())
        AssistantHistoryCache.save(snapshot, key: cacheKey)
    }

    /// 读取本会话的任务：有进行中／排队的任务就订阅它的进度；没有时核对上一任务的最终状态并刷新消息。
    /// - Parameter afterTurn: 刚结束一轮（进度流已收到结束并刷新过任务与消息）时为真：只查找下一条排队任务与队列，
    ///   不再重复读取任务详情和消息页（第十一轮：一轮结束只拉一次消息）。
    func restoreRun(_ model: AppModel, afterTurn: Bool = false) async {
        guard let sessionID else { return }
        let token = generation
        do {
            // 只读取本会话的任务（session_id 过滤）；旧服务器不认识该参数时会返回全部会话的任务，此时退回读取最近 200 项。
            var rows = try await model.request(path: AssistantPresentation.root + "/runs?limit=20&include_bot=true&session_id=\(AssistantPresentation.path(sessionID))").assistantItems
            if rows.contains(where: { $0.text("session_id") != sessionID }) {
                rows = try await model.request(path: AssistantPresentation.root + "/runs?limit=200&include_bot=true").assistantItems
            }
            guard token == generation else { return }
            runs = rows.filter { $0.text("session_id") == sessionID }
            // 正在执行／等待中的任务优先；都没有时取最早的排队任务（列表按创建时间倒序）。
            let unfinished = runs.filter { !AssistantPresentation.terminal.contains($0.text("status")) }
            if let running = unfinished.first(where: { $0.text("status") != "queued" }) ?? unfinished.last {
                if runID != running.id { events = []; lastSeq = 0; live.clearText() }
                activeRun = running.value
                attach(model)
            } else if !afterTurn, let runID {
                let current = try await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))")
                guard token == generation else { return }
                activeRun = current
                await refreshMessages(model)
                live.clearText()
            }
            guard token == generation else { return }
            await refreshQueue(model)
        } catch { if token == generation { self.error = AssistantPresentation.error(error) } }
    }

    func reconcileSend(_ model: AppModel) async {
        guard let pending = UserDefaults.standard.dictionary(forKey: defaultsKey), let key = pending["request_id"] as? String else { unresolvedSend = false; return }
        do {
            let rows = try await model.request(path: AssistantPresentation.root + "/runs?limit=200&include_bot=true").assistantItems
            if let found = rows.first(where: { $0.text("client_request_id") == key }) {
                clearPending()
                if sessionID != found.text("session_id") { await select(found.text("session_id"), model: model) }
                activeRun = found.value
                events = []
                lastSeq = 0
                live.clearText()
                attach(model)
                await refreshMessages(model)
                notice = "已找到服务器接收的任务，没有重复发送。"
            } else { notice = "暂未在最近 200 项任务中找到该请求，仍保留待核对记录。请检查任务历史后决定是否放弃记录。" }
        } catch { self.error = AssistantPresentation.error(error) }
    }

    func clearPending() {
        UserDefaults.standard.removeObject(forKey: defaultsKey)
        unresolvedSend = false
        requestID = nil
    }

    func send(_ content: String, attachments: [JSONValue], model: AppModel, mode: String = "message") async -> Bool {
        guard let sessionID, canSend else { return false }
        var body: [String: JSONValue] = ["content": .string(content), "attachments": .array(attachments), "model_selection": selectedModel]
        // 新服务器发送时会顺带记住；旧服务器靠本机按会话 id 记。
        if mode != "input" { sessionModelStore.save(sessionID, selectedModel) }
        if let accountID { body["account_id"] = .number(Double(accountID)) }
        let path: String
        // 第八轮：输入栏不再手选发送方式。任务等待补充信息时自动作为补充信息提交；
        // 其余情况一律新建任务（执行中会进入排队，在待处理气泡下改为插入调整或停止并替换）。
        if mode == "input", let runID {
            body.removeValue(forKey: "model_selection")
            body.removeValue(forKey: "account_id")
            body.removeValue(forKey: "attachments")
            return await input(runID: runID, endpoint: "input", body: body, model: model)
        } else { path = AssistantPresentation.root + "/sessions/\(AssistantPresentation.path(sessionID))/runs" }
        return await createRun(path: path, body: body, model: model)
    }

    func retry(_ message: AssistantItem, model: AppModel) async {
        guard let sessionID, !busy, !unresolvedSend else { return }
        _ = await createRun(path: AssistantPresentation.root + "/sessions/\(AssistantPresentation.path(sessionID))/messages/\(message.id)/retry/runs", body: ["model_selection": selectedModel], model: model)
    }

    func regenerate(user: AssistantItem, assistant: AssistantItem, edited: String?, model: AppModel) async {
        guard let sessionID, !busy, !unresolvedSend, let assistantID = assistant["id"] else { return }
        var body: [String: JSONValue] = ["assistant_message_id": assistantID, "model_selection": selectedModel]
        if let edited { body["content"] = .string(edited) }
        _ = await createRun(path: AssistantPresentation.root + "/sessions/\(AssistantPresentation.path(sessionID))/messages/\(user.id)/regenerate/runs", body: body, model: model)
    }

    private func createRun(path: String, body: [String: JSONValue], model: AppModel) async -> Bool {
        guard let sessionID, !busy, !unresolvedSend else { return false }
        busy = true
        defer { busy = false }
        error = nil
        let token = generation
        let previousRunID = runActive ? runID : nil
        let key = UUID().uuidString
        requestID = key
        let fingerprint = (try? JSONValue.object(body).data()).map { SHA256.hash(data: $0).map { String(format: "%02x", $0) }.joined() } ?? ""
        UserDefaults.standard.set(["request_id": key, "session_id": sessionID, "fingerprint": fingerprint], forKey: defaultsKey)
        var payload = body
        payload["client_request_id"] = .string(key)
        do {
            let row = try await model.request(path: path, method: "POST", body: .object(payload))
            guard row["id"]?.stringValue != nil else { throw APIError.incompatibleResponse }
            clearPending()
            guard token == generation else { return true }
            if let previousRunID, row["id"]?.stringValue != previousRunID, row.assistantText("status") == "queued" {
                // 上一轮仍在执行：这条消息进入排队。保留当前任务的进度订阅，把它显示为待处理气泡。
                await refreshQueue(model)
                startWatching(model)
                return true
            }
            streamTask?.cancel()
            activeRun = row
            events = []
            lastSeq = 0
            live.clearText()
            // 第十一轮：先订阅进度流，再并行刷新消息（显示刚发出的“我的消息”），首个字不再被一次消息页往返挡住。
            attach(model)
            await refreshMessages(model)
            return true
        } catch {
            if let api = error as? APIError, !api.isOutcomeUncertain && !api.isIncompatibleResponse { clearPending() }
            else { unresolvedSend = true }
            self.error = AssistantPresentation.error(error)
            return false
        }
    }

    func input(runID: String, endpoint: String, body: [String: JSONValue], model: AppModel) async -> Bool {
        guard !busy else { return false }
        busy = true
        defer { busy = false }
        var payload = body
        payload["client_request_id"] = .string(UUID().uuidString)
        do {
            _ = try await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))/\(endpoint)", method: "POST", body: .object(payload))
            notice = "服务器已接收，正在核对任务进度。"
            await restoreRun(model)
            return true
        } catch {
            self.error = AssistantPresentation.error(error)
            await restoreRun(model)
            return false
        }
    }

    func cancel(_ model: AppModel) async {
        guard let runID, !busy else { return }
        busy = true
        defer { busy = false }
        do {
            activeRun = try await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))/cancel", method: "POST")
            notice = "已提交停止请求，请以服务器任务状态为准。"
            await restoreRun(model)
        } catch { self.error = AssistantPresentation.error(error); await restoreRun(model) }
    }

    func pauseSubscription() { subscriptionID = UUID(); streamTask?.cancel(); streaming = false }

    func attach(_ model: AppModel) {
        streamTask?.cancel()
        guard let runID else { return }
        if progressRunID != runID {
            progressRunID = runID
            live.resetProgress()
        }
        startWatching(model)
        let token = generation
        subscriptionID = UUID()
        let subscription = subscriptionID
        streamTask = Task { [weak self] in
            guard let self else { return }
            streaming = true
            defer { if token == generation && subscription == subscriptionID { streaming = false } }
            for attempt in 0..<4 {
                guard !Task.isCancelled, token == generation, subscription == subscriptionID else { return }
                do {
                    // 回调本身在主线程执行：每个事件只切换一次线程（AppModel 核对会话后直接调用）。
                    try await model.stream(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))/stream?after_seq=\(lastSeq)") { @MainActor [weak self] event in
                        self?.consume(event, generation: token, subscription: subscription)
                    }
                } catch {
                    if Task.isCancelled { return }
                }
                guard !Task.isCancelled, token == generation, subscription == subscriptionID else { return }
                do {
                    live.flush()
                    let row = try await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))")
                    guard token == generation else { return }
                    activeRun = row
                    await refreshMessages(model)
                    let status = row.assistantText("status")
                    if AssistantPresentation.terminal.contains(status) || status == "waiting_input" || status == "waiting_approval" {
                        live.clearText()
                        if AssistantPresentation.terminal.contains(status) {
                            // 本轮结束：插入调整已随本轮完成；接着读取下一条排队任务（如有）并订阅它的进度。
                            // 在新的任务里执行，避免当前（即将被替换的）订阅任务的取消状态影响请求。
                            // 任务与消息刚刚读过，restoreRun 只查下一条排队任务与队列（afterTurn）。
                            steered = []
                            Task { @MainActor [weak self] in
                                guard let self, token == self.generation else { return }
                                await self.restoreRun(model, afterTurn: true)
                            }
                        }
                        return
                    }
                } catch { if attempt == 3 { self.error = AssistantPresentation.error(error) } }
                if attempt < 3 { try? await Task.sleep(for: .seconds(1 << attempt)) }
            }
            if token == generation { notice = "进度连接已中断，任务仍在服务器执行。点击“恢复进度”重新订阅。" }
        }
    }

    // MARK: 真实阶段与排队消息（第八轮）

    /// 收到进度事件说明任务已经开始执行：本机把 queued 更新为 running（服务器同时把 phase 推进到对应阶段）。
    private func trackProgress(_ event: JSONValue, type: String) {
        guard !type.isEmpty, type != "heartbeat" else { return }
        if runStatus == "queued", var object = activeRun?.objectValue {
            object["status"] = "running"
            object["queue_ahead"] = nil
            activeRun = .object(object)
        }
        switch type {
        case "tool_started":
            live.setProgress(tool: event.assistantText("tool_description").nilIfBlank ?? event.assistantText("tool_name"))
        case "steer_applied":
            if let index = steered.firstIndex(where: { !$0.applied }) { steered[index].applied = true }
        default: break
        }
        // 写入去重在 AssistantLiveState 里：同一阶段的连续增量不会触发界面更新。
        if AssistantProgress.tracked.contains(type) { live.setProgress(type: type) }
    }

    /// 回答位置的状态行：只根据服务器给出的任务状态、phase 与进度事件；没有数据的阶段不显示。
    var liveStatus: AssistantProgress.Status? {
        guard runActive else { return nil }
        return AssistantProgress.status(run: activeRun, eventType: live.progressType, tool: live.progressTool)
    }

    /// 尚未开始执行的排队消息（不含当前任务自己的排队记录）。
    var pendingQueue: [AssistantItem] {
        queue.filter { ["pending", "paused"].contains($0.text("status")) && ($0["run_id"]?.stringValue ?? "") != (runID ?? "-") }
            .sorted { ($0["position"]?.intValue ?? 0) < ($1["position"]?.intValue ?? 0) }
    }

    /// 读取本会话的排队消息（只读，可随时重复）。
    func refreshQueue(_ model: AppModel) async {
        guard let sessionID else { queue = []; return }
        let token = generation
        do {
            let rows = try await model.request(path: AssistantPresentation.root + "/queue?session_id=\(AssistantPresentation.path(sessionID))").assistantItems
            guard token == generation else { return }
            queue = rows.filter { $0.text("session_id").isEmpty || $0.text("session_id") == sessionID }
            replacingTurnIDs.formIntersection(Set(queue.map(\.id)))
        } catch {
            // 旧服务器没有队列接口时不显示待处理气泡，也不打断对话。
            if token == generation { queue = [] }
        }
    }

    /// 任务排队期间每 3 秒读取一次任务与队列（只读）：更新“前面还有几个”、发现任务开始执行；
    /// 没有排队的任务、也没有排队消息时停止。
    private func startWatching(_ model: AppModel) {
        watchTask?.cancel()
        let token = generation
        watchTask = Task { @MainActor [weak self] in
            while true {
                try? await Task.sleep(for: .seconds(3))
                guard let self, !Task.isCancelled, token == self.generation, self.runActive else { return }
                let queued = self.runStatus == "queued"
                guard queued || !self.pendingQueue.isEmpty || !self.steered.isEmpty else { return }
                if queued, let runID = self.runID,
                   let row = try? await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))"),
                   token == self.generation, self.runID == runID, self.runStatus == "queued" {
                    self.activeRun = row
                }
                await self.refreshQueue(model)
            }
        }
    }

    /// 排队消息改为“插入调整”：服务器原子地取消这条排队并把内容作为当前任务的调整说明（只提交一次）。
    @discardableResult
    func steerQueued(_ item: AssistantItem, model: AppModel) async -> Bool {
        guard queueBusyID == nil, runStatus == "running" else { return false }
        queueBusyID = item.id
        defer { queueBusyID = nil }
        do {
            _ = try await model.request(path: AssistantPresentation.root + "/queue/\(AssistantPresentation.path(item.id))/steer", method: "POST",
                                        body: ["client_request_id": .string(UUID().uuidString)])
            queue.removeAll { $0.id == item.id }
            steered.append(AssistantSteeredMessage(id: item.id, content: item.text("content")))
            return true
        } catch {
            self.error = AssistantPresentation.error(error)
            await refreshQueue(model)
            return false
        }
    }

    /// 排队消息改为“停止并替换”：服务器请求停止当前任务，并把这条消息排到队首（只提交一次）。
    @discardableResult
    func replaceWithQueued(_ item: AssistantItem, model: AppModel) async -> Bool {
        guard queueBusyID == nil, runActive else { return false }
        queueBusyID = item.id
        defer { queueBusyID = nil }
        do {
            _ = try await model.request(path: AssistantPresentation.root + "/queue/\(AssistantPresentation.path(item.id))/replace", method: "POST")
            replacingTurnIDs.insert(item.id)
            if let runID, let current = try? await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))"),
               self.runID == runID {
                activeRun = current
            }
            await refreshQueue(model)
            return true
        } catch {
            self.error = AssistantPresentation.error(error)
            await refreshQueue(model)
            return false
        }
    }

    private func consume(_ event: JSONValue, generation token: Int, subscription: UUID) {
        guard token == generation, subscription == subscriptionID else { return }
        let type = event.assistantText("type")
        if type == "heartbeat" { return }
        if let seq = event["seq"]?.intValue {
            guard seq > lastSeq else { return }
            lastSeq = seq
        }
        events.append(event)
        if events.count > 500 { events.removeFirst(events.count - 500) }
        trackProgress(event, type: type)
        switch type {
        case "assistant_delta": live.appendText(event.assistantText("delta"))
        case "assistant_reasoning_delta", "reasoning_delta": live.appendReasoning(event.assistantText("delta"))
        case "assistant_delta_reset": live.clearText()
        case "assistant_message": live.replace(text: event.assistantText("content"), reasoning: event.assistantText("reasoning"))
        default: break
        }
    }
}

/// 已改为“插入调整”的消息（本机显示用）。
struct AssistantSteeredMessage: Identifiable, Hashable, Sendable {
    let id: String
    let content: String
    /// 服务器已在安全边界把它并入当前任务（收到 steer_applied）。
    var applied = false
}

/// 任务的真实阶段：与服务器 `_phase_from_event` 同一套事件→阶段映射。
enum AssistantProgress {
    struct Status: Equatable {
        let symbol: String
        let text: String
    }

    static let tracked: Set<String> = ["run_started", "model_capability_check", "provider_selected", "route_selected", "skill_selected",
                                       "model_attempt", "assistant_delta", "assistant_reasoning_delta", "reasoning_delta", "tool_started",
                                       "tool_finished", "steer_applied", "assistant_message", "done", "retry_scheduled", "model_exhausted",
                                       "assistant_delta_reset"]

    static func phase(forEvent type: String) -> String? {
        [
            "run_started": "understanding", "model_capability_check": "checking_model", "provider_selected": "selecting_model",
            "route_selected": "routing", "skill_selected": "selecting_skill", "model_attempt": "thinking", "assistant_delta": "responding",
            "assistant_reasoning_delta": "thinking", "reasoning_delta": "thinking", "tool_started": "using_tool", "tool_finished": "thinking",
            "steer_applied": "steered", "assistant_message": "finalizing", "done": "finishing", "assistant_delta_reset": "thinking"
        ][type]
    }

    static func status(run: JSONValue?, eventType: String, tool: String) -> Status {
        let status = run?.assistantText("status") ?? ""
        if run?["cancel_requested"]?.boolValue == true { return Status(symbol: "stop.circle", text: "正在停止当前任务…") }
        switch status {
        case "waiting_input": return Status(symbol: "text.bubble", text: "等待补充信息")
        case "waiting_approval": return Status(symbol: "checkmark.shield", text: "等待审批")
        case "queued":
            if let ahead = run?["queue_ahead"]?.intValue, ahead > 0 { return Status(symbol: "clock", text: "排队中 · 前面 \(ahead) 个") }
            return Status(symbol: "hourglass", text: "正在开始…")
        default: break
        }
        switch eventType {
        case "retry_scheduled": return Status(symbol: "clock.arrow.circlepath", text: "模型暂时失败，等待重试…")
        case "model_exhausted": return Status(symbol: "arrow.triangle.2.circlepath", text: "切换备用模型…")
        default: break
        }
        let current = Self.phase(forEvent: eventType) ?? run?.assistantText("phase") ?? ""
        switch current {
        case "starting": return Status(symbol: "hourglass", text: "正在开始…")
        case "recovering": return Status(symbol: "arrow.clockwise", text: "正在恢复任务…")
        case "understanding": return Status(symbol: "sparkles", text: "理解问题…")
        case "checking_model", "selecting_model": return Status(symbol: "cpu", text: "连接模型…")
        case "routing": return Status(symbol: "arrow.triangle.branch", text: "选择工具…")
        case "selecting_skill": return Status(symbol: "wand.and.stars", text: "加载技能…")
        case "thinking": return Status(symbol: "brain", text: "思考中…")
        case "using_tool": return Status(symbol: "wrench.and.screwdriver", text: tool.isEmpty ? "调用工具…" : "调用工具：\(tool)…")
        case "responding": return Status(symbol: "text.bubble", text: "生成回复…")
        case "finalizing", "finishing": return Status(symbol: "checkmark.circle", text: "整理回复…")
        case "steered": return Status(symbol: "arrow.turn.down.right", text: "已插入调整，继续执行…")
        case "paused": return Status(symbol: "pause.circle", text: "任务已暂停")
        default: return Status(symbol: "ellipsis", text: "执行中…")
        }
    }
}

extension String {
    fileprivate var nilIfBlank: String? { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self }
}
