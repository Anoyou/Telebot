import SwiftUI
import Charts
import TelePilotCore

// MARK: - 筛选与展示选项

enum LedgerRange: Int, CaseIterable, Identifiable {
    case week = 7, month = 30, quarter = 90, year = 365
    var id: Int { rawValue }
    var title: String {
        switch self {
        case .week: "7 天"
        case .month: "30 天"
        case .quarter: "90 天"
        case .year: "1 年"
        }
    }
}

enum LedgerPeriod: String, CaseIterable, Identifiable {
    case day, week, month
    var id: String { rawValue }
    var title: String {
        switch self {
        case .day: "按日"
        case .week: "按周"
        case .month: "按月"
        }
    }
    var unit: Calendar.Component {
        switch self {
        case .day: .day
        case .week: .weekOfYear
        case .month: .month
        }
    }
}

enum LedgerBreakdown: String, CaseIterable, Identifiable {
    case chat, recipient, account, plugin
    var id: String { rawValue }
    var title: String {
        switch self {
        case .chat: "按群"
        case .recipient: "按收款方"
        case .account: "按账号"
        case .plugin: "按来源"
        }
    }
}

enum LedgerDirectionFilter: String, CaseIterable, Identifiable {
    case all, income, payout
    var id: String { rawValue }
    var title: String {
        switch self {
        case .all: "全部"
        case .income: "只看入账"
        case .payout: "只看出账"
        }
    }
    var query: String? {
        switch self {
        case .all: nil
        case .income: "in"
        case .payout: "out"
        }
    }
}

enum LedgerStatusFilter: String, CaseIterable, Identifiable {
    case posted = "", failed = "FAILED", pending = "PENDING", dryRun = "DRY_RUN"
    var id: String { rawValue }
    var title: String {
        switch self {
        case .posted: "已计账"
        case .failed: "失败尝试"
        case .pending: "处理中"
        case .dryRun: "演练"
        }
    }
}

// MARK: - 数据

/// 一笔资金流水（后端 `/api/ledger` 的 `items[]`）。
struct LedgerEntry: Identifiable, Sendable {
    let id: String
    let date: Date?
    let isIncome: Bool
    let amount: Decimal
    let status: String
    let accountID: Int?
    let chatID: Int?
    let chatTitle: String?
    let payer: String?
    let receiver: String?
    let pluginKey: String?
    let entryKey: String?
    let actionType: String
    let channel: String?
    let errorCode: String?
    let payoutKey: String?
    let raw: JSONValue

    init(_ value: JSONValue) {
        let source = value["source"]?.stringValue ?? "entry"
        id = source + ":" + (OpsFormat.text(value["source_id"]) ?? OpsFormat.text(value["id"]) ?? UUID().uuidString)
        date = OpsFormat.date(value["created_at"])
        isIncome = (value["direction"]?.stringValue ?? "") == "in"
        amount = OpsFormat.decimal(value["amount"])
        status = (value["status"]?.stringValue ?? "").uppercased()
        accountID = value["account_id"]?.intValue
        chatID = value["chat_id"]?.intValue
        chatTitle = OpsFormat.text(value["chat_title"])
        payer = OpsFormat.text(value["payer_name"]) ?? value["payer_user_id"]?.intValue.map { "用户 \($0)" }
        if let name = OpsFormat.text(value["receiver_name"]) { receiver = name }
        else if let username = OpsFormat.text(value["receiver_username"]) { receiver = "@" + username.trimmingCharacters(in: CharacterSet(charactersIn: "@")) }
        else { receiver = value["receiver_user_id"]?.intValue.map { "用户 \($0)" } }
        pluginKey = OpsFormat.text(value["plugin_key"])
        entryKey = OpsFormat.text(value["entry_key"])
        actionType = value["action_type"]?.stringValue ?? ""
        channel = OpsFormat.text(value["channel"])
        errorCode = OpsFormat.text(value["error_code"])
        payoutKey = OpsFormat.text(value["payout_key"])
        raw = value
    }

    var isPosted: Bool { status == "OK" || status == "COMPENSATED" }
    var signedAmount: Decimal { isIncome ? amount : -amount }
    var counterpart: String { isIncome ? (payer ?? chatTitle ?? "未记录付款方") : (receiver ?? chatTitle ?? "未记录收款方") }
}

/// 付款失败后进入自动补付队列的事项。
struct LedgerCompensation: Identifiable, Sendable {
    let id: Int
    let amount: Decimal
    let status: String
    let ambiguous: Bool
    let accountID: Int?
    let chatTitle: String?
    let chatID: Int?
    let receiver: String?
    let pluginKey: String?
    let retryCount: Int
    let error: String?
    let payoutKey: String?
    let traceID: String?
    let createdAt: Date?
    let nextAttemptAt: Date?
    let raw: JSONValue

    init?(_ value: JSONValue) {
        guard let id = value["id"]?.intValue else { return nil }
        self.id = id
        amount = OpsFormat.decimal(value["amount"])
        status = value["status"]?.stringValue ?? ""
        ambiguous = value["ambiguous"]?.boolValue ?? false
        accountID = value["account_id"]?.intValue
        chatTitle = OpsFormat.text(value["chat_title"])
        chatID = value["chat_id"]?.intValue
        receiver = OpsFormat.text(value["receiver_name"]) ?? value["receiver_user_id"]?.intValue.map { "用户 \($0)" }
        pluginKey = OpsFormat.text(value["plugin_key"])
        retryCount = value["retry_count"]?.intValue ?? 0
        error = OpsFormat.text(value["error_last"]) ?? OpsFormat.text(value["error_code_last"]) ?? OpsFormat.text(value["error_code_first"])
        payoutKey = OpsFormat.text(value["payout_key"])
        traceID = OpsFormat.text(value["trace_id"])
        createdAt = OpsFormat.date(value["created_at"])
        nextAttemptAt = OpsFormat.date(value["next_attempt_at"])
        raw = value
    }
}

struct LedgerTrendPoint: Identifiable {
    let date: Date
    var income: Double
    var payout: Double
    var count: Int
    var net: Double { income - payout }
    var id: Date { date }
}

struct LedgerShare: Identifiable {
    let id: String
    let label: String
    let value: Double
    let detail: String
}

enum LedgerText {
    static func accountName(_ id: Int?, accounts: [Account]) -> String {
        guard let id else { return "未知账号" }
        if let account = accounts.first(where: { $0.id == id }) { return AccountPresentation.name(account) }
        return "账号 \(id)"
    }

    static func status(_ raw: String) -> (title: String, symbol: String, color: Color) {
        switch raw.uppercased() {
        case "OK": ("已计账", "checkmark.circle.fill", .green)
        case "COMPENSATED": ("已补付", "checkmark.seal.fill", .green)
        case "FAILED": ("失败", "xmark.octagon.fill", .red)
        case "PENDING": ("处理中", "clock.fill", .orange)
        case "DRY_RUN": ("演练（未真实付款）", "testtube.2", .secondary)
        default: (raw.isEmpty ? "未知" : raw, "questionmark.circle", .secondary)
        }
    }

    static func compensation(_ raw: String) -> (title: String, symbol: String, color: Color) {
        switch raw.lowercased() {
        case "pending": ("等待自动补付", "clock.arrow.circlepath", .orange)
        case "sending": ("正在补付", "paperplane.fill", .blue)
        case "sent": ("已补付", "checkmark.circle.fill", .green)
        case "compensated": ("已人工核销", "checkmark.seal.fill", .green)
        case "abandoned": ("自动补付已放弃，需人工处理", "exclamationmark.triangle.fill", .red)
        default: (raw.isEmpty ? "未知" : raw, "questionmark.circle", .secondary)
        }
    }

    static func action(_ raw: String) -> String {
        switch raw.lowercased() {
        case "payment_confirmed": return "收到付款"
        case "payout": return "派奖 / 付款"
        case "start_session": return "开局"
        case "close_session": return "结束对局"
        case "manual_compensation": return "人工补付"
        default:
            if raw.hasSuffix("_payout") || raw.hasPrefix("payout_") { return "派奖 / 付款" }
            return raw.isEmpty ? "资金动作" : raw
        }
    }

    static func channel(_ raw: String?) -> String? {
        guard let raw else { return nil }
        switch raw {
        case "userbot": return "UserBot 账号"
        case "interaction_bot": return "交互 Bot"
        case "account_bot": return "管理 Bot"
        case "manual_compensation": return "人工补付"
        case "external_payment_notice": return "收款通知"
        default: return raw
        }
    }
}

// MARK: - 主页面

struct LedgerView: View {
    @Environment(AppModel.self) private var model
    @State private var range: LedgerRange = .month
    @State private var accountID: Int?
    @State private var direction: LedgerDirectionFilter = .all
    @State private var statusFilter: LedgerStatusFilter = .posted
    @State private var period: LedgerPeriod = .day
    @State private var breakdown: LedgerBreakdown = .chat
    @State private var summary: JSONValue?
    @State private var stats: JSONValue?
    @State private var entries: [LedgerEntry] = []
    @State private var compensations: [LedgerCompensation] = []
    @State private var summaryError: String?
    @State private var entriesError: String?
    @State private var compensationsError: String?
    @State private var loading = false
    @State private var lastLoaded: Date?

    private var filterKey: String { "\(range.rawValue)|\(accountID ?? 0)|\(direction.rawValue)|\(statusFilter.rawValue)" }

    var body: some View {
        List {
            filterSection
            if let summaryError, summary == nil {
                Section { NoticeView(title: "台账汇总读取失败", message: summaryError) }
            }
            summarySection
            trendSection
            breakdownSection
            operationsSection
            compensationSection
            entriesSection
            Section {
                NavigationLink {
                    ManagementFormView(action: .init(title: "重置资金台账", path: "/api/ledger/reset", destructive: true,
                        explanation: "将清空服务器资金台账。此操作无法在 App 中撤销，请先完成备份并输入确认口令。"))
                } label: {
                    Label("重置资金台账", systemImage: "trash").foregroundStyle(.red)
                }
            } header: { Text("高风险维护") } footer: {
                Text("会永久删除流水、趋势、开局统计和待补付记录，仅在测试数据需要清理时使用。")
            }
        }
        .navigationTitle("资金台账")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: filterKey) { await load() }
        .refreshable { await load() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("复制汇总", systemImage: "doc.on.doc") { copySummary() }
                    .disabled(summary == nil)
            }
        }
        .accessibilityIdentifier("ledger.screen")
    }

    // MARK: Sections

    @ViewBuilder private var filterSection: some View {
        Section {
            Picker("时间范围", selection: $range) {
                ForEach(LedgerRange.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("ledger.range")
            Picker(selection: $accountID) {
                Text("全部账号").tag(Int?.none)
                ForEach(model.accounts) { account in
                    Text(AccountPresentation.name(account)).tag(Int?.some(account.id))
                }
            } label: { Label("账号", systemImage: "person.crop.circle") }
            Picker(selection: $direction) {
                ForEach(LedgerDirectionFilter.allCases) { Text($0.title).tag($0) }
            } label: { Label("方向", systemImage: "arrow.left.arrow.right") }
            Picker(selection: $statusFilter) {
                ForEach(LedgerStatusFilter.allCases) { Text($0.title).tag($0) }
            } label: { Label("流水状态", systemImage: "line.3.horizontal.decrease.circle") }
        } footer: {
            if loading {
                Text("正在读取…")
            } else if let lastLoaded {
                Text("统计只包含已计账（成功或已补付）的资金。“流水状态”只影响下方明细，可用来查看失败或演练的尝试。更新于 \(lastLoaded.formatted(date: .omitted, time: .shortened))。")
            }
        }
    }

    @ViewBuilder private var summarySection: some View {
        if let summary {
            let income = OpsFormat.decimal(summary["income"])
            let payout = OpsFormat.decimal(summary["payout"])
            let net = OpsFormat.decimal(summary["net"])
            Section {
                OpsMetricGrid {
                    OpsMetricTile(title: "入账", value: OpsFormat.amount(income), symbol: "arrow.down.left.circle", color: .green, detail: "收到的钱")
                    OpsMetricTile(title: "出账", value: OpsFormat.amount(payout), symbol: "arrow.up.right.circle", color: .orange, detail: "付出去的钱")
                    OpsMetricTile(title: "净额", value: OpsFormat.amount(net, signed: true), symbol: "equal.circle",
                                  color: net < 0 ? .red : .primary, detail: net < 0 ? "亏损：出账多于入账" : "入账 − 出账")
                    OpsMetricTile(title: "流水笔数", value: OpsFormat.integer(summary["count"]?.intValue ?? 0), symbol: "list.number",
                                  detail: "近 \(range.title)已计账")
                }
                if !compensations.isEmpty {
                    Label("有 \(compensations.count) 笔付款失败、等待补付，见下方“待补付”。", systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange).font(.subheadline)
                }
            } header: { Text("汇总（近 \(range.title)）") }
        } else if loading {
            Section { ProgressView("正在读取台账…") }
        }
    }

    private var trendPoints: [LedgerTrendPoint] {
        let buckets = summary?["by_day"]?.arrayValue ?? []
        var grouped: [Date: LedgerTrendPoint] = [:]
        let calendar = Calendar.current
        for bucket in buckets {
            guard let key = bucket["key"]?.stringValue, let day = OpsFormat.day(key) else { continue }
            let start = period == .day ? day : (calendar.dateInterval(of: period.unit, for: day)?.start ?? day)
            var point = grouped[start] ?? LedgerTrendPoint(date: start, income: 0, payout: 0, count: 0)
            point.income += OpsFormat.double(OpsFormat.decimal(bucket["income"]))
            point.payout += OpsFormat.double(OpsFormat.decimal(bucket["payout"]))
            point.count += bucket["count"]?.intValue ?? 0
            grouped[start] = point
        }
        return grouped.values.sorted { $0.date < $1.date }
    }

    @ViewBuilder private var trendSection: some View {
        if summary != nil {
            Section {
                Picker("统计周期", selection: $period) {
                    ForEach(LedgerPeriod.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                let points = trendPoints
                if points.isEmpty {
                    ContentUnavailableView("这段时间没有资金流水", systemImage: "chart.bar", description: Text("换一个时间范围或账号再看看。"))
                } else {
                    Chart {
                        ForEach(points) { point in
                            BarMark(x: .value("日期", point.date, unit: period.unit), y: .value("金额", point.income))
                                .foregroundStyle(by: .value("类型", "入账"))
                            BarMark(x: .value("日期", point.date, unit: period.unit), y: .value("金额", -point.payout))
                                .foregroundStyle(by: .value("类型", "出账"))
                        }
                        ForEach(points) { point in
                            LineMark(x: .value("日期", point.date, unit: period.unit), y: .value("金额", point.net))
                                .foregroundStyle(by: .value("类型", "净额"))
                                .interpolationMethod(.monotone)
                                .symbol(.circle)
                        }
                        RuleMark(y: .value("零线", 0)).foregroundStyle(Color.secondary.opacity(0.4))
                    }
                    .chartForegroundStyleScale(["入账": Color.green, "出账": Color.orange, "净额": Color.blue])
                    .chartLegend(position: .bottom)
                    .frame(height: 220)
                    .padding(.vertical, 6)
                    .accessibilityLabel("资金趋势图")
                    if let best = points.max(by: { $0.net < $1.net }), let worst = points.min(by: { $0.net < $1.net }), points.count > 1 {
                        LabeledContent {
                            Text(OpsFormat.amount(Decimal(best.net), signed: true)).monospacedDigit().foregroundStyle(.green)
                        } label: { Label("净额最高：\(periodLabel(best.date))", systemImage: "arrow.up.forward") }
                        LabeledContent {
                            Text(OpsFormat.amount(Decimal(worst.net), signed: true)).monospacedDigit().foregroundStyle(worst.net < 0 ? .red : .primary)
                        } label: { Label("净额最低：\(periodLabel(worst.date))", systemImage: "arrow.down.forward") }
                    }
                }
            } header: { Text("资金趋势") } footer: {
                Text("绿色柱向上为入账，橙色柱向下为出账，蓝线为每期净额（入账 − 出账）。")
            }
        }
    }

    private func periodLabel(_ date: Date) -> String {
        switch period {
        case .day: date.formatted(.dateTime.month().day())
        case .week: date.formatted(.dateTime.month().day()) + " 起一周"
        case .month: date.formatted(.dateTime.year().month())
        }
    }

    private var shares: [LedgerShare] {
        switch breakdown {
        case .chat:
            return (summary?["by_chat"]?.arrayValue ?? []).prefix(8).map { bucket in
                LedgerShare(id: bucket["key"]?.stringValue ?? UUID().uuidString,
                            label: OpsFormat.text(bucket["label"]) ?? "未知群",
                            value: OpsFormat.double(OpsFormat.decimal(bucket["net"])),
                            detail: "\(bucket["count"]?.intValue ?? 0) 笔 · 入 \(OpsFormat.amount(OpsFormat.decimal(bucket["income"]))) / 出 \(OpsFormat.amount(OpsFormat.decimal(bucket["payout"])))")
            }
        case .recipient:
            return (summary?["by_recipient"]?.arrayValue ?? []).prefix(8).map { bucket in
                LedgerShare(id: bucket["key"]?.stringValue ?? UUID().uuidString,
                            label: OpsFormat.text(bucket["label"]) ?? "未识别收款方",
                            value: OpsFormat.double(OpsFormat.decimal(bucket["received"])),
                            detail: "\(bucket["count"]?.intValue ?? 0) 笔 · 入 \(OpsFormat.amount(OpsFormat.decimal(bucket["income"]))) / 出 \(OpsFormat.amount(OpsFormat.decimal(bucket["payout"])))")
            }
        case .account:
            return group(entries.filter(\.isPosted)) { entry in
                let id = entry.accountID ?? 0
                return ("\(id)", accountName(entry.accountID))
            }
        case .plugin:
            return group(entries.filter(\.isPosted)) { entry in
                let key = entry.pluginKey ?? entry.actionType
                return (key, entry.pluginKey.map { "插件 \($0)" } ?? LedgerText.action(entry.actionType))
            }
        }
    }

    private func group(_ items: [LedgerEntry], key: (LedgerEntry) -> (String, String)) -> [LedgerShare] {
        var totals: [String: (label: String, income: Decimal, payout: Decimal, count: Int)] = [:]
        for entry in items {
            let (id, label) = key(entry)
            var current = totals[id] ?? (label, 0, 0, 0)
            if entry.isIncome { current.income += entry.amount } else { current.payout += entry.amount }
            current.count += 1
            totals[id] = current
        }
        return totals.map { id, value in
            LedgerShare(id: id, label: value.label, value: OpsFormat.double(value.income - value.payout),
                        detail: "\(value.count) 笔 · 入 \(OpsFormat.amount(value.income)) / 出 \(OpsFormat.amount(value.payout))")
        }
        .sorted { abs($0.value) > abs($1.value) }
        .prefix(8).map { $0 }
    }

    @ViewBuilder private var breakdownSection: some View {
        if summary != nil {
            Section {
                Picker("分组方式", selection: $breakdown) {
                    ForEach(LedgerBreakdown.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                let rows = shares
                if rows.isEmpty {
                    Text("暂无可分组的数据").foregroundStyle(.secondary)
                } else {
                    Chart(rows) { row in
                        BarMark(x: .value("金额", row.value), y: .value("对象", row.label))
                            .foregroundStyle(row.value < 0 ? Color.red : Color.green)
                            .annotation(position: .trailing, alignment: .leading) {
                                Text(OpsFormat.amount(Decimal(row.value), signed: breakdown != .recipient))
                                    .font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                            }
                    }
                    .frame(height: CGFloat(rows.count) * 36 + 24)
                    .padding(.vertical, 6)
                    .accessibilityLabel("资金分布图")
                    ForEach(rows) { row in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.label).font(.subheadline).lineLimit(1)
                            Text(row.detail).font(.caption).foregroundStyle(.secondary)
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
            } header: { Text("资金分布（前 8 名）") } footer: {
                Text(breakdownFootnote)
            }
        }
    }

    private var breakdownFootnote: String {
        switch breakdown {
        case .chat: "按群统计净额，绿色为赚、红色为亏。"
        case .recipient: "按收款方统计其收到的金额。"
        case .account: "根据下方最近 \(entries.count) 笔已计账流水计算，数量多时可能不完整。"
        case .plugin: "按产生资金动作的插件或动作类型统计，根据最近 \(entries.count) 笔已计账流水计算。"
        }
    }

    @ViewBuilder private var operationsSection: some View {
        if let total = stats?["total"] {
            Section {
                OpsMetricGrid {
                    OpsMetricTile(title: "开局数", value: OpsFormat.integer(total["started_sessions"]?.intValue ?? 0), symbol: "gamecontroller",
                                  detail: "有效开局事件")
                    OpsMetricTile(title: "派奖成功率", value: rateText(total["payout_success_rate"]), symbol: "percent",
                                  color: rateColor(total), detail: "\(total["payout_success_count"]?.intValue ?? 0) 成功 / \(total["payout_attempt_count"]?.intValue ?? 0) 次")
                    OpsMetricTile(title: "参与人数", value: OpsFormat.integer(total["participant_count"]?.intValue ?? 0), symbol: "person.3",
                                  detail: "按用户去重")
                    OpsMetricTile(title: "派奖失败", value: OpsFormat.integer(total["payout_failure_count"]?.intValue ?? 0), symbol: "xmark.circle",
                                  color: (total["payout_failure_count"]?.intValue ?? 0) > 0 ? .red : .primary, detail: "失败会进入待补付")
                }
            } header: { Text("运营概览") } footer: {
                Text("开局、派奖与参与人数来自插件记录的动作事件，与资金汇总使用同一时间范围。")
            }
        }
    }

    private func rateText(_ value: JSONValue?) -> String {
        guard let value, !value.isNull else { return "暂无" }
        let number = OpsFormat.double(OpsFormat.decimal(value))
        return String(format: "%.1f%%", number)
    }

    private func rateColor(_ total: JSONValue) -> Color {
        guard (total["payout_attempt_count"]?.intValue ?? 0) > 0, let raw = total["payout_success_rate"], !raw.isNull else { return .primary }
        let rate = OpsFormat.double(OpsFormat.decimal(raw))
        return rate >= 90 ? .green : rate >= 70 ? .orange : .red
    }

    @ViewBuilder private var compensationSection: some View {
        Section {
            if let compensationsError {
                NoticeView(title: "待补付读取失败", message: compensationsError)
            } else if compensations.isEmpty, lastLoaded != nil {
                Label("没有待补付事项", systemImage: "checkmark.circle").foregroundStyle(.green)
            }
            ForEach(compensations) { item in
                NavigationLink { LedgerCompensationDetailView(item: item, accountName: accountName(item.accountID)) } label: {
                    LedgerCompensationRow(item: item, accountName: accountName(item.accountID))
                }
            }
        } header: { Text("待补付") } footer: {
            Text("付款失败后会自动重试补付；成功补付或你人工核销后会从这里消失。正常情况下应为 0。")
        }
    }

    @ViewBuilder private var entriesSection: some View {
        Section {
            if let entriesError {
                NoticeView(title: "流水读取失败", message: entriesError)
            } else if entries.isEmpty, lastLoaded != nil {
                ContentUnavailableView("没有符合条件的流水", systemImage: "tray", description: Text("调整时间范围、账号或状态筛选。"))
            }
            ForEach(entries.prefix(20)) { entry in
                NavigationLink { LedgerEntryDetailView(entry: entry, accountName: accountName(entry.accountID)) } label: {
                    LedgerEntryRow(entry: entry, accountName: accountName(entry.accountID))
                }
            }
            if entries.count > 20 {
                NavigationLink {
                    LedgerEntriesListView(entries: entries)
                } label: { Label("查看全部 \(entries.count) 笔", systemImage: "list.bullet") }
                    .accessibilityIdentifier("ledger.allEntries")
            }
        } header: { Text("最近流水（\(statusFilter.title)）") }
    }

    // MARK: Helpers

    private func accountName(_ id: Int?) -> String { LedgerText.accountName(id, accounts: model.accounts) }

    private func load() async {
        loading = true
        defer { loading = false }
        let model = self.model
        let since = OpsFormat.iso(Date.now.addingTimeInterval(-Double(range.rawValue) * 86_400))
        let accountText = accountID.map(String.init)
        let common: [(String, String?)] = [("since", since), ("account_id", accountText)]
        let summaryPath = OpsFormat.path("/api/ledger/summary", common + [("direction", direction.query)])
        let statsPath = OpsFormat.path("/api/ledger/stats", common)
        let entriesPath = OpsFormat.path("/api/ledger", common + [("direction", direction.query), ("status", statusFilter.rawValue), ("limit", "200")])
        let compensationsPath = OpsFormat.path("/api/ledger/compensations", [("account_id", accountText), ("limit", "100")])
        async let summaryResult = OpsFetch.result(model, summaryPath)
        async let statsResult = OpsFetch.result(model, statsPath)
        async let entriesResult = OpsFetch.result(model, entriesPath)
        async let compensationsResult = OpsFetch.result(model, compensationsPath)
        let results = await (summaryResult, statsResult, entriesResult, compensationsResult)
        guard !Task.isCancelled else { return }
        switch results.0 {
        case .success(let value): summary = value; summaryError = nil
        case .failure(let error): summaryError = OpsFormat.errorMessage(error)
        }
        if case .success(let value) = results.1 { stats = value }
        switch results.2 {
        case .success(let value): entries = ManagementText.items(value).map(LedgerEntry.init); entriesError = nil
        case .failure(let error): entriesError = OpsFormat.errorMessage(error)
        }
        switch results.3 {
        case .success(let value): compensations = ManagementText.items(value).compactMap(LedgerCompensation.init); compensationsError = nil
        case .failure(let error): compensationsError = OpsFormat.errorMessage(error)
        }
        lastLoaded = Date.now
    }

    private func copySummary() {
        guard let summary else { return }
        var lines = [
            "资金台账汇总：近 \(range.title)，\(accountID.map { accountName($0) } ?? "全部账号")，\(direction.title)",
            "入账 \(OpsFormat.amount(OpsFormat.decimal(summary["income"])))，出账 \(OpsFormat.amount(OpsFormat.decimal(summary["payout"])))，净额 \(OpsFormat.amount(OpsFormat.decimal(summary["net"]), signed: true))，共 \(summary["count"]?.intValue ?? 0) 笔",
            "待补付 \(compensations.count) 笔"
        ]
        for item in compensations.prefix(10) {
            lines.append("· 待补付 #\(item.id) \(OpsFormat.amount(item.amount)) \(LedgerText.compensation(item.status).title) \(item.error ?? "")")
        }
        OpsFormat.copyForDiagnosis(lines)
    }
}

// MARK: - 行与详情

struct LedgerEntryRow: View {
    let entry: LedgerEntry
    let accountName: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: entry.isIncome ? "arrow.down.left.circle.fill" : "arrow.up.right.circle.fill")
                .font(.title3)
                .foregroundStyle(entry.isPosted ? (entry.isIncome ? Color.green : Color.orange) : Color.secondary)
                .accessibilityLabel(entry.isIncome ? "入账" : "出账")
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline) {
                    Text(entry.counterpart).font(.subheadline.weight(.medium)).lineLimit(1)
                    Spacer(minLength: 8)
                    Text(OpsFormat.amount(entry.signedAmount, signed: true))
                        .font(.subheadline.weight(.semibold)).monospacedDigit()
                        .foregroundStyle(entry.isPosted ? (entry.isIncome ? Color.green : Color.orange) : Color.secondary)
                        .strikethrough(!entry.isPosted)
                }
                Text([LedgerText.action(entry.actionType), entry.chatTitle, accountName].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                HStack(spacing: 8) {
                    Text(OpsFormat.time(entry.date)).font(.caption2).foregroundStyle(.secondary)
                    if !entry.isPosted || entry.status == "COMPENSATED" {
                        let status = LedgerText.status(entry.status)
                        OpsBadge(title: status.title, symbol: status.symbol, color: status.color)
                    }
                }
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

struct LedgerEntriesListView: View {
    let entries: [LedgerEntry]
    @Environment(AppModel.self) private var model
    @State private var search = ""

    private func accountName(_ id: Int?) -> String { LedgerText.accountName(id, accounts: model.accounts) }

    private var filtered: [LedgerEntry] {
        guard !search.isEmpty else { return entries }
        return entries.filter { entry in
            [entry.chatTitle, entry.payer, entry.receiver, entry.pluginKey, entry.payoutKey, LedgerText.action(entry.actionType), accountName(entry.accountID), OpsFormat.amount(entry.amount)]
                .compactMap { $0 }.contains { $0.localizedCaseInsensitiveContains(search) }
        }
    }

    var body: some View {
        List {
            Section {
                ForEach(filtered) { entry in
                    NavigationLink { LedgerEntryDetailView(entry: entry, accountName: accountName(entry.accountID)) } label: {
                        LedgerEntryRow(entry: entry, accountName: accountName(entry.accountID))
                    }
                }
                if filtered.isEmpty { ContentUnavailableView.search(text: search) }
            } header: { Text("共 \(filtered.count) 笔") }
        }
        .navigationTitle("全部流水")
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $search, prompt: "群名、收款方、插件或金额")
    }
}

struct LedgerEntryDetailView: View {
    let entry: LedgerEntry
    let accountName: String

    private var sentence: String {
        let place = entry.chatTitle.map { "在群“\($0)”" } ?? ""
        if entry.isIncome {
            return "\(accountName)\(place)收到 \(entry.payer ?? "未记录的付款方") 的 \(OpsFormat.amount(entry.amount))。"
        }
        return "\(accountName)\(place)向 \(entry.receiver ?? "未记录的收款方") 付出 \(OpsFormat.amount(entry.amount))。"
    }

    var body: some View {
        let status = LedgerText.status(entry.status)
        List {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Text(OpsFormat.amount(entry.signedAmount, signed: true))
                        .font(.largeTitle.weight(.semibold)).monospacedDigit()
                        .foregroundStyle(entry.isPosted ? (entry.isIncome ? Color.green : Color.orange) : Color.secondary)
                    OpsBadge(title: status.title, symbol: status.symbol, color: status.color)
                    Text(sentence).font(.subheadline).foregroundStyle(.secondary)
                    if !entry.isPosted {
                        Text("这笔没有计入汇总：只有“已计账”和“已补付”的流水才算真实资金变动。").font(.footnote).foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 4)
            }
            Section("基本信息") {
                OpsFieldRow(title: "时间", value: OpsFormat.fullTime(entry.date), symbol: "clock")
                OpsFieldRow(title: "方向", value: entry.isIncome ? "入账（收到的钱）" : "出账（付出去的钱）", symbol: "arrow.left.arrow.right")
                OpsFieldRow(title: "类型", value: LedgerText.action(entry.actionType), symbol: "tag")
                OpsFieldRow(title: "账号", value: accountName, symbol: "person.crop.circle")
                OpsFieldRow(title: "群", value: [entry.chatTitle, entry.chatID.map { "ID \($0)" }].compactMap { $0 }.joined(separator: " · "), symbol: "bubble.left.and.bubble.right")
                OpsFieldRow(title: "付款方", value: entry.payer, symbol: "person")
                OpsFieldRow(title: "收款方", value: entry.receiver, symbol: "person.fill.checkmark")
            }
            Section("排查信息") {
                OpsFieldRow(title: "来源插件", value: entry.pluginKey.map { [$0, entry.entryKey].compactMap { $0 }.joined(separator: " › ") }, symbol: "puzzlepiece.extension")
                OpsFieldRow(title: "发送通道", value: LedgerText.channel(entry.channel), symbol: "paperplane")
                OpsFieldRow(title: "错误码", value: entry.errorCode, symbol: "exclamationmark.triangle", monospaced: true)
                OpsFieldRow(title: "流水键", value: entry.payoutKey, symbol: "number", monospaced: true)
                if let params = entry.raw["params_summary"], params.objectValue?.isEmpty == false {
                    NavigationLink {
                        List { ManagementValueView(value: params) }.navigationTitle("原始参数")
                    } label: { Label("原始参数", systemImage: "curlybraces") }
                }
            }
            Section {
                Button {
                    OpsFormat.copyForDiagnosis([
                        sentence, "状态：\(status.title)", "时间：\(OpsFormat.fullTime(entry.date))",
                        "插件：\(entry.pluginKey ?? "无")", "错误码：\(entry.errorCode ?? "无")", "流水键：\(entry.payoutKey ?? entry.id)"
                    ])
                } label: { Label("复制诊断信息", systemImage: "doc.on.doc") }
            }
        }
        .navigationTitle("流水详情")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct LedgerCompensationRow: View {
    let item: LedgerCompensation
    let accountName: String
    var body: some View {
        let status = LedgerText.compensation(item.status)
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: status.symbol).foregroundStyle(status.color).font(.title3)
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(item.receiver ?? "未记录收款方").font(.subheadline.weight(.medium)).lineLimit(1)
                    Spacer()
                    Text(OpsFormat.amount(item.amount)).font(.subheadline.weight(.semibold)).monospacedDigit().foregroundStyle(.orange)
                }
                Text(status.title + (item.ambiguous ? " · 结果待确认" : "")).font(.caption).foregroundStyle(status.color)
                Text([item.chatTitle, accountName, "已重试 \(item.retryCount) 次"].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

struct LedgerCompensationDetailView: View {
    let item: LedgerCompensation
    let accountName: String
    var body: some View {
        let status = LedgerText.compensation(item.status)
        List {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Text(OpsFormat.amount(item.amount)).font(.largeTitle.weight(.semibold)).monospacedDigit().foregroundStyle(.orange)
                    OpsBadge(title: status.title, symbol: status.symbol, color: status.color)
                    if item.ambiguous {
                        Text("上次付款结果不确定：Telegram 可能已经收到了付款消息。核销前请先在群里确认对方是否已收到，避免重复付款。")
                            .font(.footnote).foregroundStyle(.orange)
                    }
                }.padding(.vertical, 4)
            }
            Section("详情") {
                OpsFieldRow(title: "收款方", value: item.receiver, symbol: "person.fill.checkmark")
                OpsFieldRow(title: "群", value: [item.chatTitle, item.chatID.map { "ID \($0)" }].compactMap { $0 }.joined(separator: " · "), symbol: "bubble.left.and.bubble.right")
                OpsFieldRow(title: "账号", value: accountName, symbol: "person.crop.circle")
                OpsFieldRow(title: "来源插件", value: item.pluginKey, symbol: "puzzlepiece.extension")
                OpsFieldRow(title: "已重试", value: "\(item.retryCount) 次", symbol: "arrow.clockwise")
                OpsFieldRow(title: "下次自动重试", value: item.nextAttemptAt.map { OpsFormat.fullTime($0) }, symbol: "clock.arrow.circlepath")
                OpsFieldRow(title: "最近错误", value: item.error, symbol: "exclamationmark.triangle")
                OpsFieldRow(title: "创建时间", value: OpsFormat.fullTime(item.createdAt), symbol: "clock")
                OpsFieldRow(title: "付款键", value: item.payoutKey, symbol: "number", monospaced: true)
            }
            if let traceID = item.traceID {
                Section {
                    NavigationLink { LogTraceTimelineView(traceID: traceID) } label: {
                        Label("查看这笔付款的处理时间线", systemImage: "point.topleft.down.to.point.bottomright.curvepath")
                    }
                }
            }
            if ["pending", "abandoned"].contains(item.status.lowercased()) {
                Section {
                    NavigationLink {
                        ManagementFormView(action: .init(title: "标记人工已支付", path: "/api/ledger/compensations/\(item.id)/manual-paid",
                            explanation: "仅在你已经通过其他方式实际完成支付后确认，避免重复付款。"))
                    } label: { Label("标记人工已支付", systemImage: "checkmark.seal") }
                } footer: { Text("核销后不会再自动补付，这笔会从待补付中移除。") }
            }
        }
        .navigationTitle("待补付详情")
        .navigationBarTitleDisplayMode(.inline)
    }
}
