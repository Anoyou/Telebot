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
}
