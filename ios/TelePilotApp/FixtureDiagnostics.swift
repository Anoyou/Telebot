#if DEBUG
import Foundation
import TelePilotCore

/// 第 19 轮界面测试数据：命中调试（`/api/dispatch/simulate`）、系统控制台、Agent 运行记录。
/// 只在 `--ui-testing` 的 FixtureService 里使用，不连接真实服务器。
extension FixtureService {
    static func diagnosticsFixture(_ clean: String, path: String, method: String, body: JSONValue?) throws -> JSONValue? {
        if clean == "/api/dispatch/simulate", method == "POST" { return try dispatchSimulation(body) }
        if clean == "/api/logs/system-console", method == "POST" { return systemConsole(body) }
        if clean == "/api/system-agent/runs", method == "GET" {
            let query = URLComponents(string: path)?.queryItems ?? []
            // 助手会话按 session_id 恢复进行中的任务：保持原来的空列表，避免影响助手界面测试。
            guard !query.contains(where: { $0.name == "session_id" }) else { return nil }
            let status = query.first { $0.name == "status" }?.value
            return .array(agentRuns.filter { status == nil || $0["status"]?.stringValue == status })
        }
        if clean.hasPrefix("/api/system-agent/runs/fixture-agent-run-"), clean.hasSuffix("/events") {
            if clean.contains("failed") { return failedRunEvents }
            let done: JSONValue = ["run_id": "fixture-agent-run-ok", "seq": 1, "event": ["type": "done", "ok": true]]
            return .array([done])
        }
        return nil
    }

    private static func dispatchSimulation(_ body: JSONValue?) throws -> JSONValue {
        let accountID = body?["account_id"]?.intValue ?? 1
        if accountID == 2 { throw APIError.server(status: 503, code: "WORKER_OFFLINE") }
        let text = body?["text"]?.stringValue ?? ""
        let via = body?["via"]?.stringValue ?? "userbot"
        let chatID: JSONValue = body?["chat_id"] ?? .null
        let senderID: JSONValue = body?["sender_id"] ?? .null
        let keywordMatched = ["你好", "提问"].contains(text)
        let isCommand = text.hasPrefix(".")
        let command = String(text.dropFirst().split(separator: " ").first ?? "")
        let direct: JSONValue
        if via == "userbot" {
            let candidates: [JSONValue] = [["plugin_key": "auto_reply", "matched": false, "reason_code": "plugin_disabled"],
                                           ["plugin_key": "content_assistant", "matched": false, "reason_code": "source_not_subscribed"]]
            direct = ["stage": "direct_passthrough", "matched": false, "reason_code": "filter_not_matched",
                      "message": "没有插件通过直通声明、账号配置和权限检查。", "matches": [], "candidates": .array(candidates)]
        } else {
            direct = ["stage": "direct_passthrough", "matched": false, "reason_code": "source_not_subscribed",
                      "message": "直通只适用于 userbot 来源。", "matches": []]
        }
        let prefix: JSONValue
        if isCommand {
            prefix = ["stage": "prefix_command", "matched": false, "reason_code": "command_not_matched",
                      "message": "userbot 前缀命令只在账号 outgoing 消息中触发。", "command": .string(command), "prefix": "."]
        } else {
            prefix = ["stage": "prefix_command", "matched": false, "reason_code": "command_not_matched", "message": "文本不是当前 userbot 前缀命令。"]
        }
        let keywordItem: JSONValue = ["source": "manifest_interaction_entry", "plugin_key": "content_assistant", "entry_key": "ask",
                                      "keywords": ["你好", "提问"], "matched": .bool(keywordMatched),
                                      "reason_code": .string(keywordMatched ? "matched" : "filter_not_matched")]
        let guardItem: JSONValue = ["source": "interaction_text_guard", "keywords": ["签到"], "matched": false, "reason_code": "filter_not_matched"]
        let keyword: JSONValue = ["stage": "keyword", "matched": .bool(keywordMatched),
                                  "reason_code": .string(keywordMatched ? "matched" : "filter_not_matched"),
                                  "message": .string(keywordMatched ? "关键词命中。" : "没有关键词规则命中。"),
                                  "matches": .array(keywordMatched ? [keywordItem] : []), "candidates": .array([guardItem, keywordItem])]
        let subscribed = via == "userbot"
        let decisions: [JSONValue] = [
            ["plugin_key": "content_assistant", "entry_key": "on_message", "matched": .bool(subscribed),
             "reason_code": .string(subscribed ? "matched" : "source_not_subscribed"),
             "message": .string(subscribed ? "订阅匹配" : "事件来源未订阅"), "dispatch_mode": "observe", "scope": ["chats": [-1001]], "filters": [:]],
            ["plugin_key": "auto_reply", "entry_key": "reply", "matched": false, "reason_code": "chat_not_in_allowed_chats",
             "message": "事件范围不匹配", "dispatch_mode": "claim", "scope": [:], "filters": ["text_contains": ["报价"]]]
        ]
        let event: JSONValue = ["stage": "event_subscription", "matched": .bool(subscribed),
                                "reason_code": .string(subscribed ? "matched" : "subscription_not_matched"),
                                "message": .string(subscribed ? "Event Bus 订阅命中。" : "Event Bus 订阅未命中。"),
                                "decisions": .array(decisions), "matches": .array(decisions.filter { $0["matched"]?.boolValue == true })]
        return ["account_id": .integer(Int64(accountID)), "via": .string(via),
                "chat": ["chat_id": chatID, "sender_id": senderID, "direction": "incoming", "edited": false],
                "text": .string(text), "stages": [direct, prefix, keyword, event]]
    }

    private static func systemConsole(_ body: JSONValue?) -> JSONValue {
        let keyword = body?["keyword"]?.stringValue ?? ""
        let all = [
            "web-1  | 2026-10-30 07:00:01 INFO  [supervisor] worker 1 started",
            "web-1  | 2026-10-30 07:00:03 WARN  [worker:2] 连接中断，5 秒后重连",
            "web-1  | 2026-10-30 07:00:09 ERROR [plugin:content_assistant] 模型请求超时 token=sk-proj-THIS_IS_A_FAKE_SECRET_FOR_UI_TEST_123456",
            "web-1  | 2026-10-30 07:00:10 INFO  [api] GET /api/health 200"
        ]
        let lines = keyword.isEmpty ? all : all.filter { $0.localizedCaseInsensitiveContains(keyword) }
        return ["ok": true, "source": "docker compose logs web", "services": ["web"],
                "tail": body?["tail"] ?? 100, "lines": .array(lines.map { .string($0) }), "error": nil]
    }

    private static var agentRuns: [JSONValue] {
        [
            ["id": "fixture-agent-run-failed", "run_id": "fixture-agent-run-failed", "session_id": "fixture-session", "kind": "message",
             "status": "failed", "error_code": "provider_timeout", "error_message": "模型供应商 30 秒内没有响应",
             "elapsed_ms": 31200, "last_seq": 7, "cancel_requested": false, "client_request_id": "ui-1",
             "usage": ["provider_name": "测试供应商", "model": "fixture-model"],
             "created_at": .string(isoDate(hoursAgo: 1)), "started_at": .string(isoDate(hoursAgo: 1))],
            ["id": "fixture-agent-run-ok", "run_id": "fixture-agent-run-ok", "session_id": "fixture-session", "kind": "retry",
             "status": "succeeded", "elapsed_ms": 4200, "last_seq": 1, "cancel_requested": false, "client_request_id": "ui-2",
             "created_at": .string(isoDate(hoursAgo: 2)), "started_at": .string(isoDate(hoursAgo: 2)), "finished_at": .string(isoDate(hoursAgo: 2))]
        ]
    }

    private static var failedRunEvents: JSONValue {
        let rows: [JSONValue] = [
            ["type": "heartbeat"],
            ["type": "route_selected", "domains": ["accounts", "logs"]],
            ["type": "provider_selected", "provider_name": "测试供应商", "model": "fixture-model", "reason": "auto"],
            ["type": "model_attempt", "provider_name": "测试供应商", "model": "fixture-model", "attempt": 1],
            ["type": "assistant_delta", "text": "正在"],
            ["type": "retry_scheduled", "retry_number": 1, "max_retries": 1],
            ["type": "error", "code": "provider_timeout", "message": "模型供应商 30 秒内没有响应"],
            ["type": "done", "ok": false]
        ]
        let created = JSONValue.string(isoDate(hoursAgo: 1))
        return .array(rows.enumerated().map { pair -> JSONValue in
            ["run_id": "fixture-agent-run-failed", "seq": .integer(Int64(pair.offset + 1)), "event": pair.element, "created_at": created]
        })
    }
}
#endif
