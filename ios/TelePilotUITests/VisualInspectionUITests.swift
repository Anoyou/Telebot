import XCTest

@MainActor
final class VisualInspectionUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUp() {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        XCTAssertTrue(app.tabBars.buttons["概览"].waitForExistence(timeout: 15))
        app.tabBars.buttons["概览"].tap()
    }

    func testOperationsAndAssistantWorkspaces() {
        app.tabBars.buttons["更多"].tap()
        app.buttons["日志与诊断"].tap()
        XCTAssertTrue(app.descendants(matching: .any)["logs.screen"].firstMatch.waitForExistence(timeout: 10))
        capture("11-log-overview")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        app.buttons["资金台账"].tap()
        XCTAssertTrue(app.descendants(matching: .any)["ledger.screen"].firstMatch.waitForExistence(timeout: 10))
        capture("12-ledger-summary")
        app.swipeUp()
        capture("13-ledger-charts")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        app.buttons["more.about"].tap()
        XCTAssertTrue(app.descendants(matching: .any)["about.appVersion"].firstMatch.waitForExistence(timeout: 10))
        capture("14-about")

        switchTab("Agent")
        XCTAssertTrue(app.descendants(matching: .any)["assistant.composer"].firstMatch.waitForExistence(timeout: 10))
        capture("15-agent")
    }

    func testPluginAndAIWorkspaces() {
        capture("01-overview")

        app.tabBars.buttons["插件"].tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        capture("02-plugin-center")

        app.buttons["plugins.management"].tap()
        XCTAssertTrue(app.buttons["plugins.checkUpdates"].waitForExistence(timeout: 5))
        capture("03-plugin-management")

        app.buttons["plugins.center"].tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 5))
        app.buttons["plugin.card.auto_reply"].tap()
        XCTAssertTrue(app.buttons["plugin.account.toggle"].waitForExistence(timeout: 5))
        capture("04-plugin-detail-overview")

        app.buttons["账号配置"].tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
        capture("05-plugin-detail-configuration")

        app.buttons["plugin.config.edit"].tap()
        XCTAssertTrue(app.buttons["plugin.config.toolbarReview"].waitForExistence(timeout: 10))
        capture("05b-plugin-config-editor")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        app.buttons["概况"].tap()
        let serverMaintenance = app.buttons["plugin.detail.serverMaintenance"]
        if !serverMaintenance.waitForExistence(timeout: 3) { app.swipeUp() }
        XCTAssertTrue(serverMaintenance.waitForExistence(timeout: 5))
        serverMaintenance.tap()
        XCTAssertTrue(app.buttons["plugin.global.toggle"].waitForExistence(timeout: 10))
        capture("06-plugin-detail-maintenance")

        switchTab("AI")
        XCTAssertTrue(app.buttons["ai.liveness.patrol"].waitForExistence(timeout: 10))
        capture("07-ai-overview")

        app.buttons["供应商"].firstMatch.tap()
        XCTAssertTrue(app.buttons["ai.provider.1"].waitForExistence(timeout: 10))
        capture("08-ai-providers")

        app.segmentedControls.buttons["概览"].tap()
        XCTAssertTrue(app.buttons["ai.liveness.patrol"].waitForExistence(timeout: 5))
        app.buttons["ai.liveness.patrol"].tap()
        XCTAssertTrue(app.navigationBars["全部供应商巡检"].waitForExistence(timeout: 10))
        capture("09-ai-liveness")

        app.terminate()
        app.launch()
        XCTAssertTrue(app.tabBars.buttons["AI"].waitForExistence(timeout: 15))
        app.tabBars.buttons["AI"].tap()
        XCTAssertTrue(app.segmentedControls.buttons["用量"].waitForExistence(timeout: 10))
        app.segmentedControls.buttons["用量"].tap()
        XCTAssertTrue(app.staticTexts["最近 100 条以内"].waitForExistence(timeout: 5))
        capture("10-ai-usage")
    }

    private func capture(_ name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func switchTab(_ title: String) {
        let tab = app.tabBars.buttons[title]
        if !tab.exists { app.tabBars.buttons.firstMatch.tap() }
        XCTAssertTrue(tab.waitForExistence(timeout: 5))
        tab.tap()
    }
}
