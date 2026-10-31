import Foundation
import Testing
@testable import TelePilotCore

@Suite struct AgentAskContextTests {
    @Test func hiddenOnlyWhenAIModuleDisabled() {
        #expect(AgentAskContext.isAvailable(disabledModules: []))
        #expect(AgentAskContext.isAvailable(disabledModules: ["ledger"]))
        #expect(!AgentAskContext.isAvailable(disabledModules: ["ai"]))
    }

    @Test func pluginPrefill() {
        #expect(AgentAskContext.composerPrefill(pluginName: "自动回复", pluginKey: "auto_reply") == "关于插件「自动回复」（auto_reply）：")
        #expect(AgentAskContext.composerPrefill(pluginName: "auto_reply", pluginKey: "auto_reply") == "关于插件「auto_reply」：")
        #expect(AgentAskContext.composerPrefill(pluginName: " 自动回复 ", pluginKey: nil) == "关于插件「自动回复」：")
        #expect(AgentAskContext.composerPrefill(pluginName: "", pluginKey: "auto_reply") == "关于插件「auto_reply」：")
        #expect(AgentAskContext.composerPrefill(pluginName: nil, pluginKey: nil) == nil)
    }

    @Test func noticeForAccountPage() {
        let request = AgentAskRequest(accountID: 2, source: .account)
        #expect(AgentAskContext.notice(request: request, linkedAccountName: "备用账号", autoLinked: false)
            == "已带入账号「备用账号」（来自账号页）。可在会话设置 › 账号上下文修改。")
    }

    @Test func noticeForPluginPage() {
        let scoped = AgentAskRequest(accountID: 1, source: .plugin, pluginName: "自动回复", pluginKey: "auto_reply")
        #expect(AgentAskContext.notice(request: scoped, linkedAccountName: "日常", autoLinked: false)
            == "已带入账号「日常」（来自插件页）；插件名已填进输入栏，补充问题后发送。可在会话设置 › 账号上下文修改。")
        let global = AgentAskRequest(accountID: nil, source: .plugin, pluginName: "自动回复", pluginKey: "auto_reply")
        #expect(AgentAskContext.notice(request: global, linkedAccountName: nil, autoLinked: false).hasPrefix("新会话为系统级（来自插件页"))
        #expect(AgentAskContext.notice(request: global, linkedAccountName: "唯一", autoLinked: true).hasPrefix("只有一个账号，新会话已自动关联「唯一」"))
    }

    @Test func reuseOnlyEmptySessionOfSameAccount() {
        func reuse(_ requested: Int?, current: Int?, messages: Bool = false, run: Bool = false, channel: String = "web", status: String = "active", id: String? = "s") -> Bool {
            AgentAskContext.canReuseCurrentSession(requestedAccountID: requested, currentSessionID: id, currentAccountID: current,
                                                   hasMessages: messages, runActive: run, channel: channel, status: status)
        }
        #expect(reuse(2, current: 2))
        #expect(!reuse(2, current: 1))
        #expect(!reuse(2, current: nil))
        #expect(!reuse(nil, current: nil))
        #expect(!reuse(2, current: 2, messages: true))
        #expect(!reuse(2, current: 2, run: true))
        #expect(!reuse(2, current: 2, channel: "bot"))
        #expect(!reuse(2, current: 2, status: "archived"))
        #expect(!reuse(2, current: 2, id: nil))
    }
}
