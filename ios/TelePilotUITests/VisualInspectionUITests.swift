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

        app.tabBars.buttons["AI"].tap()
        XCTAssertTrue(app.buttons["模型批量测活"].waitForExistence(timeout: 10))
        capture("07-ai-overview")

        app.buttons["供应商"].firstMatch.tap()
        XCTAssertTrue(app.buttons["ai.provider.1"].waitForExistence(timeout: 10))
        capture("08-ai-providers")

        app.segmentedControls.buttons["概览"].tap()
        XCTAssertTrue(app.buttons["模型批量测活"].waitForExistence(timeout: 5))
        app.buttons["模型批量测活"].tap()
        XCTAssertTrue(app.navigationBars["模型测活"].waitForExistence(timeout: 10))
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
}
