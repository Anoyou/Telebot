import XCTest
@testable import TelePilotCore

final class PaymentSourceSwitchTests: XCTestCase {
    private func status(authoritative: Bool, effective: String, active: Bool, reason: String? = nil) -> JSONValue {
        [
            "authoritative": .bool(authoritative), "effective": .string(effective), "label": "x",
            "setting_key": "log_retention.payment_source_plugin_authoritative",
            "sources": [["plugin_key": "payment-source-cn-transfer", "display_name": "中文转账通知", "active": .bool(active),
                         "inactive_reason": reason.map { JSONValue.string($0) } ?? .null]],
        ]
    }

    func testOldServerOrOtherPluginHidden() {
        XCTAssertNil(PaymentSourceSwitchView.build(nil, pluginKey: "payment-source-cn-transfer"))
        XCTAssertNil(PaymentSourceSwitchView.build(status(authoritative: false, effective: "legacy", active: true), pluginKey: "red_packet"))
    }

    func testOffMeansLegacy() throws {
        let view = try XCTUnwrap(PaymentSourceSwitchView.build(status(authoritative: false, effective: "legacy", active: true),
                                                              pluginKey: "payment-source-cn-transfer"))
        XCTAssertFalse(view.checked)
        XCTAssertEqual(view.tone, .neutral)
        XCTAssertTrue(view.stateLabel.contains("旧解析"))
        XCTAssertNil(view.warning)
    }

    func testOnAndWorking() throws {
        let view = try XCTUnwrap(PaymentSourceSwitchView.build(status(authoritative: true, effective: "plugin", active: true),
                                                              pluginKey: "payment-source-cn-transfer"))
        XCTAssertTrue(view.checked)
        XCTAssertEqual(view.tone, .success)
    }

    func testOnButPluginInactiveWarns() throws {
        let view = try XCTUnwrap(PaymentSourceSwitchView.build(status(authoritative: true, effective: "legacy", active: false, reason: "插件已停用"),
                                                              pluginKey: "payment-source-cn-transfer"))
        XCTAssertEqual(view.tone, .warning)
        XCTAssertEqual(view.warning, "插件已停用")
    }

    func testSummaryListsAllSources() throws {
        let view = try XCTUnwrap(PaymentSourceSwitchView.build(status(authoritative: false, effective: "legacy", active: true), pluginKey: nil))
        XCTAssertEqual(view.sources.map(\.pluginKey), ["payment-source-cn-transfer"])
    }

    // 第 23 轮：App 里可拨，确认文案与 Web paymentSourceConfirmText 一致，写入只带这一个子键。
    func testConfirmationMatchesWeb() {
        let on = PaymentSourceSwitchView.confirmation(turningOn: true)
        XCTAssertEqual(on.title, "改为以插件结果为准？")
        XCTAssertEqual(on.confirmLabel, "打开")
        XCTAssertTrue(on.message.contains("插件出错或超时会自动回退旧解析"))
        let off = PaymentSourceSwitchView.confirmation(turningOn: false)
        XCTAssertEqual(off.title, "改回以旧解析为准？")
        XCTAssertEqual(off.confirmLabel, "关闭")
        XCTAssertTrue(off.message.contains("插件只做对照"))
    }

    func testPatchBodyOnlyTouchesTheSwitch() {
        XCTAssertEqual(PaymentSourceSwitchView.patchBody(enabled: true),
                       .object(["log_retention": .object(["payment_source_plugin_authoritative": .bool(true)])]))
        XCTAssertEqual(PaymentSourceSwitchView.patchBody(enabled: false)["log_retention"]?["payment_source_plugin_authoritative"]?.boolValue, false)
    }

    func testMessages() {
        XCTAssertEqual(PaymentSourceSwitchView.successMessage(enabled: true), "已改为以插件结果为准，30 秒内生效")
        XCTAssertEqual(PaymentSourceSwitchView.successMessage(enabled: false), "已改回以旧解析为准，30 秒内生效")
        XCTAssertTrue(PaymentSourceSwitchView.failureMessage("网络错误").hasPrefix("保存失败：网络错误"))
    }
}

/// 第 24 轮：找得到开关——服务器没列出 / 旧服务器 / 读取失败时说明原因，搜索别名。
final class PaymentSourceAvailabilityTests: XCTestCase {
    private let key = "payment-source-cn-transfer"
    private func status(sources: [JSONValue]) -> JSONValue {
        ["authoritative": false, "effective": "legacy", "label": "x",
         "setting_key": "log_retention.payment_source_plugin_authoritative", "sources": .array(sources)]
    }

    func testListedPluginIsAvailable() {
        let listed = status(sources: [["plugin_key": .string(key), "display_name": "转账通知解析（中文）", "active": false,
                                       "inactive_reason": "插件已停用（全局关闭），转账通知只用旧解析"]])
        guard case .available(let view) = PaymentSourceSwitchView.availability(status: listed, notFound: false, failure: nil,
                                                                                 pluginKey: key, transferPlugin: true) else {
            return XCTFail("应显示开关")
        }
        XCTAssertEqual(view.warning, "插件已停用（全局关闭），转账通知只用旧解析")
    }

    func testUnlistedTransferPluginExplainsWhy() {
        let result = PaymentSourceSwitchView.availability(status: status(sources: []), notFound: false, failure: nil,
                                                          pluginKey: key, transferPlugin: true)
        guard case .notParticipating(let global) = result else { return XCTFail("应说明没参与的原因：\(result)") }
        XCTAssertEqual(global?.checked, false)
        XCTAssertTrue(PaymentSourceSwitchView.notParticipatingMessage.contains("没在参与转账通知解析"))
    }

    func testOldServerShowsRequirementOnlyForTransferPlugins() {
        XCTAssertEqual(PaymentSourceSwitchView.availability(status: nil, notFound: true, failure: nil, pluginKey: key, transferPlugin: true),
                       .serverTooOld)
        XCTAssertEqual(PaymentSourceSwitchView.availability(status: nil, notFound: true, failure: nil, pluginKey: "red_packet", transferPlugin: false),
                       .hidden)
        XCTAssertTrue(PaymentSourceSwitchView.serverTooOldMessage.contains("0.116.0"))
    }

    func testOtherFailureIsExplainedForTransferPlugins() {
        XCTAssertEqual(PaymentSourceSwitchView.availability(status: nil, notFound: false, failure: "网络已断开", pluginKey: key, transferPlugin: true),
                       .unreadable("网络已断开"))
        XCTAssertEqual(PaymentSourceSwitchView.availability(status: nil, notFound: false, failure: "网络已断开", pluginKey: "x", transferPlugin: false),
                       .hidden)
    }

    func testNonTransferPluginStaysHidden() {
        XCTAssertEqual(PaymentSourceSwitchView.availability(status: status(sources: []), notFound: false, failure: nil,
                                                            pluginKey: "red_packet", transferPlugin: false), .hidden)
    }

    func testTransferPluginDetection() {
        XCTAssertTrue(PaymentSourceSwitchView.isTransferPluginKey("payment-source-cn-transfer"))
        XCTAssertTrue(PaymentSourceSwitchView.isTransferPluginKey("payment_source_alipay"))
        XCTAssertFalse(PaymentSourceSwitchView.isTransferPluginKey("red_packet"))
        XCTAssertTrue(PaymentSourceSwitchView.declaresPaymentSource(["payment_sources": [["id": "cn_transfer"]]]))
        XCTAssertTrue(PaymentSourceSwitchView.declaresPaymentSource(["manifest": ["tags": ["payment_source", "utility"]]]))
        XCTAssertFalse(PaymentSourceSwitchView.declaresPaymentSource(["tags": ["utility"]]))
        XCTAssertFalse(PaymentSourceSwitchView.declaresPaymentSource(nil))
    }

    func testSearchAliases() {
        XCTAssertTrue(PaymentSourceSwitchView.matchesSearch(key: key, feature: nil, query: "中文转账通知"))
        XCTAssertTrue(PaymentSourceSwitchView.matchesSearch(key: key, feature: nil, query: "转账"))
        XCTAssertTrue(PaymentSourceSwitchView.matchesSearch(key: key, feature: nil, query: "Transfer"))
        XCTAssertFalse(PaymentSourceSwitchView.matchesSearch(key: key, feature: nil, query: "红包"))
        XCTAssertFalse(PaymentSourceSwitchView.matchesSearch(key: "red_packet", feature: nil, query: "转账"))
        XCTAssertFalse(PaymentSourceSwitchView.matchesSearch(key: key, feature: nil, query: " "))
    }
}
