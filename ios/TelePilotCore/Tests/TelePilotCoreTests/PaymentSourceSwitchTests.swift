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
