import XCTest

@MainActor
final class WorkspaceUITests: XCTestCase {
    private func launch() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        return app
    }

    func testDockKeepsPrimarySectionsAndAgentEntry() {
        let app = launch()
        XCTAssertTrue(app.tabBars.buttons["概览"].waitForExistence(timeout: 45))
        XCTAssertTrue(app.tabBars.buttons["插件"].exists)
        XCTAssertTrue(app.tabBars.buttons["AI"].exists)
        XCTAssertTrue(app.tabBars.buttons["更多"].exists)
        XCTAssertTrue(app.tabBars.buttons["Agent"].exists)
    }

    func testMoreSectionKeepsAccountsAndSettingsReachable() {
        let app = launch()
        let more = app.tabBars.buttons["更多"]
        XCTAssertTrue(more.waitForExistence(timeout: 45))
        more.tap()
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["more.settings"].exists)
        XCTAssertTrue(app.buttons["more.search"].exists)
    }

    func testPluginScreenUsesTwoColumnCards() {
        let app = launch()
        let plugins = app.tabBars.buttons["插件"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 45))
        plugins.tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["plugins.category"].exists)
        XCTAssertTrue(app.buttons["plugins.accountPicker"].exists)
        app.buttons["plugins.management"].tap()
        XCTAssertTrue(app.buttons["plugins.checkUpdates"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["远程插件与更新"].exists)
        XCTAssertTrue(app.buttons["插件仓库"].exists)
    }

    func testPluginAccountStateIsSeparateFromGlobalMaintenance() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        app.buttons["plugin.card.auto_reply"].tap()
        XCTAssertTrue(app.buttons["plugin.account.toggle"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["账号运行详情"].exists)
        app.buttons["plugin.account.toggle"].tap()
        let confirm = app.buttons["确认停用"]
        if !confirm.waitForExistence(timeout: 5) {
            app.buttons["plugin.account.toggle"].tap()
            XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        }
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        XCTAssertTrue(app.buttons["在此账号启用该插件"].firstMatch.waitForExistence(timeout: 10))
        let serverMaintenance = app.buttons["plugin.detail.serverMaintenance"]
        if !serverMaintenance.waitForExistence(timeout: 3) { app.swipeUp() }
        XCTAssertTrue(serverMaintenance.waitForExistence(timeout: 5))
        serverMaintenance.tap()
        XCTAssertTrue(app.buttons["plugin.global.toggle"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["卸载插件"].exists == false)
        XCTAssertTrue(app.buttons["重新加载到所选账号"].exists)
    }

    func testPluginConfigSubpageReturnsToConfigurationTab() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        app.buttons["plugin.card.auto_reply"].tap()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 5))
        app.buttons["账号配置"].tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
        app.buttons["plugin.config.edit"].tap()
        XCTAssertTrue(app.buttons["plugin.config.toolbarReview"].waitForExistence(timeout: 10))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        // 返回后仍停留在“账号配置”，而不是跳回“概况”。
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
    }

    func testPluginConfigUsesNativeProviderAndModelPickers() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        let card = app.buttons["plugin.card.content_assistant"]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.tap()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 5))
        app.buttons["账号配置"].tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
        app.buttons["plugin.config.edit"].tap()
        // x-ui-widget=llm-provider-select / llm-model-select 渲染为原生选择器，而不是手输文本框。
        let provider = app.descendants(matching: .any)["plugin.config.select.固定 TelePilot Provider（可选）"].firstMatch
        XCTAssertTrue(provider.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["plugin.config.select.TelePilot 模型覆盖（可选）"].firstMatch.exists)
        XCTAssertFalse(app.textFields["固定 TelePilot Provider（可选）"].exists)
        // 统一模型列表：Provider 选择器保留，但标注随模型自动填写。
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "随模型自动填写")).firstMatch.waitForExistence(timeout: 5))
    }

    func testPluginConfigFollowsSectionsAndMarksOnlyRequiredFields() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        let card = app.buttons["plugin.card.content_assistant"]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.tap()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 5))
        app.buttons["账号配置"].tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
        app.buttons["plugin.config.edit"].tap()
        // 基础分组（“AI 模型”）留在主页，组标题位于组内第一项上方。
        let aiGroup = app.descendants(matching: .any)["plugin.config.group.AI 模型"].firstMatch
        XCTAssertTrue(aiGroup.waitForExistence(timeout: 10))
        let provider = app.descendants(matching: .any)["plugin.config.select.固定 TelePilot Provider（可选）"].firstMatch
        XCTAssertTrue(provider.waitForExistence(timeout: 5))
        XCTAssertLessThan(aiGroup.frame.minY, provider.frame.minY)
        XCTAssertFalse(app.descendants(matching: .any)["plugin.config.required.telepilot_provider"].exists)
        // 其他分组各自成为一行子页面入口，行上显示摘要；必填未填的分组有提示。
        let triggerPage = app.buttons["plugin.config.page.触发方式"]
        var swipes = 0
        while !triggerPage.exists && swipes < 4 {
            app.swipeUp()
            swipes += 1
        }
        XCTAssertTrue(triggerPage.exists)
        XCTAssertTrue(triggerPage.label.contains("有必填项未填"))
        let ptPage = app.buttons["plugin.config.page.PT 站点数据源"]
        XCTAssertTrue(ptPage.exists)
        XCTAssertTrue(ptPage.label.contains("未启用"))
        XCTAssertTrue(app.buttons["plugin.config.page.消息模板"].exists)
        XCTAssertFalse(app.descendants(matching: .any)["plugin.config.required.bot_username"].exists)

        triggerPage.tap()
        XCTAssertTrue(app.navigationBars["触发方式"].waitForExistence(timeout: 5))
        // 写着“可选”、有默认值的字段不标“必填”；只有没有默认值的“目标群组 ID”是必填。
        let required = app.descendants(matching: .any)["plugin.config.required.target_chat_id"].firstMatch
        XCTAssertTrue(required.waitForExistence(timeout: 5))
        XCTAssertFalse(app.descendants(matching: .any)["plugin.config.required.bot_username"].exists)
        XCTAssertFalse(app.descendants(matching: .any)["plugin.config.required.enable_guest"].exists)
        // 可选字段只有一个控件：不再出现“是否提交此项”的同名开关。
        let guest = app.switches.matching(NSPredicate(format: "label == %@", "访客模式触发"))
        XCTAssertEqual(guest.count, 1)
        // 子页面里修改同一份草稿，回到主页统一检查并保存。
        guest.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.92, dy: 0.5)).tap()
        app.navigationBars["触发方式"].buttons.element(boundBy: 0).tap()
        let review = app.buttons["plugin.config.toolbarReview"]
        XCTAssertTrue(review.waitForExistence(timeout: 5))
        XCTAssertTrue(review.isEnabled)
        // 有未保存的修改时离开要确认。
        let back = app.buttons["plugin.config.back"]
        XCTAssertTrue(back.waitForExistence(timeout: 5))
        back.tap()
        let discard = app.buttons["放弃修改"]
        XCTAssertTrue(discard.waitForExistence(timeout: 5))
        discard.tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
    }
}
