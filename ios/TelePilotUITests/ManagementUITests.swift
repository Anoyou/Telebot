import XCTest
import UIKit

@MainActor
final class ManagementUITests: XCTestCase {
    private func launch() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        XCTAssertTrue(app.tabBars.buttons["更多"].waitForExistence(timeout: 45))
        app.tabBars.buttons["更多"].tap()
        return app
    }

    /// 服务器更新面板以 medium 高度的 sheet 呈现，iOS 26 起内联导航栏是悬浮在列表之上的玻璃层，
    /// 列表行可以滚到导航栏下面：此时元素仍 exists/isHittable，但点击中心会落在导航栏上而丢失
    /// （iOS 27 上“应用更新”曾停在 y≈420–490、导航栏 430–482 处，点击后确认弹窗没有出现）。
    /// 这里按帧判断：只有目标整行落在导航栏下沿与列表下沿之间才算到位；否则按目标与可见区中线的
    /// 距离做“按住—拖动—停留”的受控拖动（无惯性），目标被导航栏遮住时向下拖、在下方或尚未加载时向上拖。
    private func scrollUpdateSheet(to element: XCUIElement, in app: XCUIApplication, attempts: Int = 14) {
        let sheet = app.otherElements["update.sheet"]
        let list = sheet.collectionViews.firstMatch
        XCTAssertTrue(list.waitForExistence(timeout: 10))
        let bar = sheet.navigationBars.firstMatch
        for _ in 0..<attempts {
            let listFrame = list.frame
            let top = (bar.exists ? max(listFrame.minY, bar.frame.maxY) : listFrame.minY) + 4
            let bottom = listFrame.maxY - 4
            let span = bottom - top
            guard span > 40 else { return }
            var dy = -span * 0.5
            if element.exists {
                let frame = element.frame
                if frame.minY >= top && frame.maxY <= bottom {
                    if element.isHittable { return }
                    Thread.sleep(forTimeInterval: 0.3)
                    continue
                }
                dy = (top + span / 2) - frame.midY
                dy = max(-span * 0.6, min(span * 0.6, dy))
                if abs(dy) < 24 { dy = dy < 0 ? -24 : 24 }
            }
            let origin = list.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
            let startY = dy < 0 ? bottom - span * 0.15 : top + span * 0.15
            let start = origin.withOffset(CGVector(dx: listFrame.width / 2, dy: startY - listFrame.minY))
            let end = start.withOffset(CGVector(dx: 0, dy: dy))
            start.press(forDuration: 0.1, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.4)
        }
    }

    /// 断言目标整行位于 sheet 可见区（导航栏下沿以下），点击不会被悬浮导航栏截走。
    private func assertVisibleInUpdateSheet(_ element: XCUIElement, in app: XCUIApplication,
                                            file: StaticString = #filePath, line: UInt = #line) {
        let sheet = app.otherElements["update.sheet"]
        let bar = sheet.navigationBars.firstMatch
        XCTAssertTrue(element.exists, "\(element) 不存在", file: file, line: line)
        XCTAssertTrue(element.isHittable, "\(element) 不可点", file: file, line: line)
        if bar.exists {
            XCTAssertGreaterThanOrEqual(element.frame.minY, bar.frame.maxY,
                                        "\(element) 仍被导航栏遮挡", file: file, line: line)
        }
    }

    func testTelegramBindingRequiresCredentialsBeforeSendingCode() {
        let app = launch()
        let accounts = app.buttons["more.accounts"]
        XCTAssertTrue(accounts.waitForExistence(timeout: 10))
        accounts.tap()
        app.buttons["account.add"].tap()
        XCTAssertTrue(app.secureTextFields["API Hash"].waitForExistence(timeout: 10))
        let send = app.buttons["发送验证码"]
        for _ in 0..<4 where !send.isHittable { app.swipeUp() }
        XCTAssertTrue(send.exists)
        XCTAssertFalse(send.isEnabled)
        XCTAssertFalse(app.textFields["验证码"].exists)
    }

    /// 第十轮：服务器设置（原系统设置）「权限与限制」分组里各行图标同为强调色——「修改管理员密码」此前整行设了主色，图标被染成白色。
    /// 截图后统计两行图标区域内的暖橙色像素，「修改管理员密码」应与「Sudo 权限」一样有明显的强调色。
    func testPermissionRowsShareAccentIconTint() {
        let app = launch()
        let system = app.buttons["服务器设置"]
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<6 where !(system.exists && system.isHittable) { app.swipeUp() }
        XCTAssertTrue(system.waitForExistence(timeout: 5))
        system.tap()
        let sudo = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Sudo 权限")).firstMatch
        let password = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "修改管理员密码")).firstMatch
        XCTAssertTrue(sudo.waitForExistence(timeout: 10))
        for _ in 0..<8 where !(password.exists && password.isHittable && sudo.isHittable) { app.swipeUp() }
        XCTAssertTrue(password.isHittable, "未找到「修改管理员密码」行")
        XCTAssertTrue(sudo.isHittable, "未找到「Sudo 权限」行")
        Thread.sleep(forTimeInterval: 0.5)
        let shot = app.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = "权限与限制"
        attachment.lifetime = .keepAlways
        add(attachment)
        guard let image = shot.image.cgImage, shot.image.size.width > 0 else { return XCTFail("截图失败") }
        let scale = CGFloat(image.width) / shot.image.size.width
        var bytes = [UInt8](repeating: 0, count: image.width * image.height * 4)
        let drawn = bytes.withUnsafeMutableBytes { raw -> Bool in
            guard let context = CGContext(data: raw.baseAddress, width: image.width, height: image.height, bitsPerComponent: 8,
                                          bytesPerRow: image.width * 4, space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
            return true
        }
        XCTAssertTrue(drawn)
        /// 行左侧图标区域（行左缘起 56pt）里的暖橙色像素数：红明显高于蓝、且足够亮。
        func accentPixels(_ row: XCUIElement) -> Int {
            let frame = row.frame
            let x0 = Int(frame.minX * scale), x1 = Int((frame.minX + 56) * scale)
            let y0 = Int((frame.minY + 4) * scale), y1 = Int((frame.maxY - 4) * scale)
            var count = 0
            for y in max(0, y0)..<min(image.height, max(y0, y1)) {
                for x in max(0, x0)..<min(image.width, max(x0, x1)) {
                    let i = (y * image.width + x) * 4
                    let r = Int(bytes[i]), g = Int(bytes[i + 1]), b = Int(bytes[i + 2])
                    if r > 150 && r - b > 70 && g > b { count += 1 }
                }
            }
            return count
        }
        let sudoCount = accentPixels(sudo)
        let passwordCount = accentPixels(password)
        print("UI-RECORD R10-TINT: Sudo 权限 \(sudoCount) 个强调色像素，修改管理员密码 \(passwordCount) 个")
        XCTAssertGreaterThan(sudoCount, 20, "Sudo 权限图标应为强调色")
        XCTAssertGreaterThan(passwordCount, max(20, sudoCount / 3), "修改管理员密码图标应与同组一样为强调色（\(passwordCount) / \(sudoCount)）")
    }

    func testLedgerResetRequiresExactPhraseBeforeConfirmation() {
        let app = launch()
        app.buttons["资金台账"].tap()
        let reset = app.buttons["重置资金台账"]
        XCTAssertTrue(app.descendants(matching: .any)["ledger.screen"].firstMatch.waitForExistence(timeout: 10))
        for _ in 0..<12 where !(reset.exists && reset.isHittable) { app.swipeUp() }
        reset.tap()
        let field = app.textFields["确认口令"]
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        app.buttons["management.submit"].tap()
        XCTAssertTrue(app.staticTexts["请输入确认口令：RESET_LEDGER。"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["确认重置资金台账"].exists)
    }

    func testRestoreCannotCommitWithoutPreviewingAFile() {
        let app = launch()
        app.buttons["备份与维护"].tap()
        app.buttons["配置导入与导出"].tap()
        let choose = app.buttons["选择文件"]
        for _ in 0..<8 where !choose.isHittable { app.swipeUp() }
        XCTAssertTrue(choose.exists)
        XCTAssertFalse(app.buttons["management.file.confirm"].exists)
        XCTAssertFalse(app.buttons["预览导入影响"].isEnabled)
    }

    /// “关于 → App 更新日志”读取随 App 打包的 ios/CHANGELOG.md，fixture 模式下不连服务器也能显示，
    /// 并标出当前安装的版本。
    func testAboutShowsBundledAppChangelog() {
        let app = launch()
        let about = app.buttons["more.about"]
        // “更多”顶部的全局紧急停止说明较长，“关于与更新”可能在首屏之外；List 懒加载，先滚动再等待。
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<6 where !(about.exists && about.isHittable) { app.swipeUp() }
        XCTAssertTrue(about.waitForExistence(timeout: 5))
        about.tap()
        let entry = app.buttons["about.appChangelog"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        for _ in 0..<4 where !entry.isHittable { app.swipeUp() }
        entry.tap()
        XCTAssertTrue(app.navigationBars["App 更新日志"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.descendants(matching: .any)["changelog.unavailable"].exists)
        XCTAssertTrue(app.descendants(matching: .any)["changelog.list"].firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["changelog.current"].firstMatch.waitForExistence(timeout: 5))
        let oldest = app.staticTexts["首个原生客户端与 APNs"]
        // 更新日志随版本增长，最旧条目在底部：多给几次滑动。
        for _ in 0..<30 where !oldest.exists { app.swipeUp() }
        XCTAssertTrue(oldest.exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "App 更新日志"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testServerUpdateRequiresConfirmationBeforeApplying() {
        let app = launch()
        let about = app.buttons["more.about"]
        // “更多”顶部的全局紧急停止说明较长，“关于与更新”可能在首屏之外；List 懒加载，先滚动再等待。
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<6 where !(about.exists && about.isHittable) { app.swipeUp() }
        XCTAssertTrue(about.waitForExistence(timeout: 5))
        about.tap()
        XCTAssertTrue(app.staticTexts["App 版本"].waitForExistence(timeout: 10))
        let open = app.buttons["update.open"]
        for _ in 0..<4 where !open.isHittable { app.swipeUp() }
        open.tap()
        XCTAssertTrue(app.staticTexts["发现新版本"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["0.105.0-beta.1"].exists)
        let apply = app.buttons["update.apply"]
        scrollUpdateSheet(to: apply, in: app)
        assertVisibleInUpdateSheet(apply, in: app)
        apply.tap()
        XCTAssertTrue(app.buttons["update.confirm"].firstMatch.waitForExistence(timeout: 10))
        let cancel = app.buttons["取消"].firstMatch
        if cancel.exists && cancel.isHittable { cancel.tap() }
        else { app.otherElements["PopoverDismissRegion"].firstMatch.tap() }
        XCTAssertFalse(app.otherElements["update.applied"].exists)
        XCTAssertFalse(app.staticTexts["更新任务已启动"].exists)
    }

    func testRuntimeLogCredentialsAreMaskedByDefault() {
        let app = launch()
        app.buttons["日志与诊断"].tap()
        let runtime = app.buttons["logs.kind.runtime"]
        for _ in 0..<8 where !(runtime.exists && runtime.isHittable) { app.swipeUp() }
        XCTAssertTrue(runtime.waitForExistence(timeout: 10))
        runtime.tap()
        let message = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "界面日志测试")).firstMatch
        XCTAssertTrue(message.waitForExistence(timeout: 10))
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "sk-proj-THIS_IS_A_FAKE_SECRET_FOR_UI_TEST_123456")).firstMatch.exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "原生日志默认脱敏"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testServerUpdateReceiptSurvivesClosingPanel() {
        let app = launch()
        let about = app.buttons["more.about"]
        // “更多”顶部的全局紧急停止说明较长，“关于与更新”可能在首屏之外；List 懒加载，先滚动再等待。
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<6 where !(about.exists && about.isHittable) { app.swipeUp() }
        XCTAssertTrue(about.waitForExistence(timeout: 5))
        about.tap()
        let open = app.buttons["update.open"]
        for _ in 0..<4 where !open.isHittable { app.swipeUp() }
        XCTAssertTrue(open.waitForExistence(timeout: 10))
        open.tap()
        let apply = app.buttons["update.apply"]
        scrollUpdateSheet(to: apply, in: app)
        XCTAssertTrue(apply.waitForExistence(timeout: 10))
        assertVisibleInUpdateSheet(apply, in: app)
        apply.tap()
        let confirm = app.buttons["update.confirm"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 10))
        confirm.tap()
        // 提交后面板内直接显示更新进度（只读轮询），不再跳转到原始 JSON 任务页。
        let progress = app.descendants(matching: .any)["update.progress"]
        XCTAssertTrue(progress.waitForExistence(timeout: 10))
        app.buttons["update.done"].tap()
        XCTAssertTrue(open.waitForExistence(timeout: 5))
        open.tap()
        // 重新打开时从本地收据恢复同一个任务继续跟踪，期间不显示“应用更新”，不能重复提交。
        XCTAssertTrue(app.descendants(matching: .any)["update.progress"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["update.apply"].exists)
        let applied = app.descendants(matching: .any)["update.applied"]
        // “更新已完成”位于进度行下方，medium 高度的面板里可能在可见区外（List 懒加载），等不到就向下滚动找。
        if !applied.waitForExistence(timeout: 20) { scrollUpdateSheet(to: applied, in: app) }
        XCTAssertTrue(applied.waitForExistence(timeout: 15))
        XCTAssertFalse(app.buttons["update.apply"].exists)
    }

    func testLogDetailExplainsMessageFailureAndOpensTimeline() {
        let app = launch()
        app.buttons["日志与诊断"].tap()
        let messages = app.buttons["logs.kind.messages"]
        for _ in 0..<8 where !(messages.exists && messages.isHittable) { app.swipeUp() }
        XCTAssertTrue(messages.waitForExistence(timeout: 10))
        messages.tap()
        let failure = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "处理失败")).firstMatch
        XCTAssertTrue(failure.waitForExistence(timeout: 10))
        failure.tap()
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "建议下一步")).firstMatch.waitForExistence(timeout: 10))
        let timeline = app.buttons["logs.openTrace"]
        for _ in 0..<8 where !(timeline.exists && timeline.isHittable) { app.swipeUp() }
        timeline.tap()
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "插件开始执行")).firstMatch.waitForExistence(timeout: 10))
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "日志处理时间线"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testLedgerShowsSummaryAndCharts() {
        let app = launch()
        app.buttons["资金台账"].tap()
        let net = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "净额")).firstMatch
        XCTAssertTrue(net.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "待补付")).firstMatch.exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "资金台账汇总与图表"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    // MARK: 第 17 轮（iOS 0.16.0）

    /// 源码里写死的 SF Symbol 名都必须真实存在（0.15.0 的「App 设置」用了不存在的 `iphone.gear`，真机上没有图标）。
    /// 扫描 TelePilotApp/*.swift 中 `systemImage:` / `systemName:` / `symbol:` 后面的字面量（含三元表达式两侧），
    /// 在模拟器上逐个 `UIImage(systemName:)`。不启动 App。
    func testAllSFSymbolNamesInSourcesExist() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("TelePilotApp")
        let files = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "swift" }
        XCTAssertFalse(files.isEmpty, "没有读到 App 源文件：\(root.path)")
        var checked = Set<String>()
        var missing: [String] = []
        for file in files {
            let text = try String(contentsOf: file, encoding: .utf8)
            for name in SymbolLiteralScanner.names(in: text) where checked.insert(name).inserted {
                if UIImage(systemName: name) == nil { missing.append("\(file.lastPathComponent): \(name)") }
            }
        }
        XCTAssertGreaterThan(checked.count, 150, "扫描到的符号太少，扫描规则可能失效")
        XCTAssertTrue(missing.isEmpty, "不存在的 SF Symbol：\(missing.joined(separator: "、"))")
    }

    /// 交互中心（对齐 Web /interaction）：运行状态、规则列表；在规则编辑页启用一条规则后保存，
    /// 保存后服务器返回的状态刷新，插件玩法参数（module_config）原样保留。
    func testInteractionCenterEditsRulesAndSaves() {
        let app = launch()
        let entry = app.buttons["交互中心"]
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<6 where !(entry.exists && entry.isHittable) { app.swipeUp() }
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        entry.tap()
        let runtime = app.descendants(matching: .any)["interaction.status.runtime"].firstMatch
        XCTAssertTrue(runtime.waitForExistence(timeout: 10))
        XCTAssertTrue(runtime.label.contains("运行中"), runtime.label)
        let subscribed = app.descendants(matching: .any)["interaction.status.rules"].firstMatch
        XCTAssertTrue(subscribed.label.contains("1/2"), subscribed.label)
        let save = app.buttons["interaction.save"]
        XCTAssertTrue(save.exists)
        XCTAssertFalse(save.isEnabled, "没有修改时不能保存")

        let notice = app.buttons["interaction.rule.rule-notice"]
        for _ in 0..<6 where !(notice.exists && notice.isHittable) { app.swipeUp() }
        XCTAssertTrue(notice.exists)
        XCTAssertTrue(notice.label.contains("停用"), notice.label)
        notice.tap()
        let enabled = app.switches["interaction.rule.enabled"]
        XCTAssertTrue(enabled.waitForExistence(timeout: 10))
        enabled.coordinate(withNormalizedOffset: CGVector(dx: 0.92, dy: 0.5)).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()

        XCTAssertTrue(save.waitForExistence(timeout: 5))
        XCTAssertTrue(save.isEnabled, "修改后应能保存")
        save.tap()
        // 保存结果显示在列表顶部，保存后列表会滚回顶部。
        let saved = app.descendants(matching: .any)["interaction.saved"].firstMatch
        let saveError = app.descendants(matching: .any)["interaction.saveError"].firstMatch
        if !saved.waitForExistence(timeout: 10) {
            for _ in 0..<4 where !(saved.exists || saveError.exists) { app.swipeDown() }
        }
        XCTAssertFalse(saveError.exists, saveError.exists ? saveError.label : "")
        XCTAssertTrue(saved.exists, "保存后应显示成功结果")
        XCTAssertFalse(save.isEnabled)
        XCTAssertTrue(notice.waitForExistence(timeout: 5))
        XCTAssertTrue(notice.label.contains("启用"), notice.label)

        let ask = app.buttons["interaction.rule.rule-ask"]
        for _ in 0..<6 where !(ask.exists && ask.isHittable) { app.swipeUp() }
        ask.tap()
        XCTAssertTrue(app.descendants(matching: .any)["interaction.ruleEditor"].firstMatch.waitForExistence(timeout: 10))
        let params = app.staticTexts["玩法参数"]
        for _ in 0..<6 where !params.exists { app.swipeUp() }
        XCTAssertTrue(params.exists, "保存后插件玩法参数应保留")
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "交互中心规则编辑"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    /// 服务器设置里的「能力与插件树」（原「平台运行树」原始文档）：主干、直接接入的插件、能力分支可展开；
    /// 切换到账号范围后按该账号的插件启用状态显示。
    func testPlatformTreeShowsNativeHierarchy() {
        let app = launch()
        let system = app.buttons["服务器设置"]
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<8 where !(system.exists && system.isHittable) { app.swipeUp() }
        system.tap()
        let entry = app.buttons["system.platformTree"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        XCTAssertTrue(entry.label.contains("能力与插件树"), entry.label)
        entry.tap()
        XCTAssertTrue(app.navigationBars["能力与插件树"].waitForExistence(timeout: 10))
        let workers = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "1/2 在线")).firstMatch
        XCTAssertTrue(workers.waitForExistence(timeout: 10))
        let killSwitch = app.descendants(matching: .any)["platformTree.killSwitch"].firstMatch
        XCTAssertTrue(killSwitch.label.contains("正常"), killSwitch.label)
        let autoReply = app.descendants(matching: .any)["platformTree.leaf.auto_reply"].firstMatch
        XCTAssertTrue(autoReply.waitForExistence(timeout: 5))
        XCTAssertTrue(autoReply.label.contains("自动回复"), autoReply.label)
        XCTAssertEqual(autoReply.value as? String, "已启用")

        // 与 Web 一样，交互分支上挂的插件默认展开；点分支的「接入的插件」可收起。
        let assistant = app.descendants(matching: .any)["platformTree.leaf.content_assistant"].firstMatch
        for _ in 0..<6 where !(assistant.exists && assistant.isHittable) { app.swipeUp() }
        XCTAssertTrue(assistant.waitForExistence(timeout: 5), "交互分支应列出接入的插件")
        XCTAssertTrue(assistant.label.contains("内容整理助手"), assistant.label)
        XCTAssertTrue(assistant.label.contains("+AI"), assistant.label)
        let hosted = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", "接入的插件")).firstMatch
        XCTAssertTrue(hosted.exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "能力与插件树"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        hosted.tap()
        XCTAssertTrue(assistant.waitForNonExistence(timeout: 5), "点「接入的插件」应收起插件列表")

        for _ in 0..<6 where !app.buttons["platformTree.scope"].isHittable { app.swipeDown() }
        app.buttons["platformTree.scope"].tap()
        let backup = app.buttons["备用账号"]
        XCTAssertTrue(backup.waitForExistence(timeout: 5))
        backup.tap()
        let disabled = NSPredicate(format: "value == %@", "未启用")
        expectation(for: disabled, evaluatedWith: autoReply)
        waitForExpectations(timeout: 10)
    }
}

/// 从 Swift 源码里取出 `systemImage:` / `systemName:` / `symbol:` 参数里的 SF Symbol 字面量。
/// 只取参数表达式（到同层的逗号或右括号为止）里紧跟在参数名、`?` 或 `:` 之后的字面量，
/// 这样 `x == "module" ? "puzzlepiece.extension" : "bell.badge"` 只取后两个，不会把比较值当成符号名。
enum SymbolLiteralScanner {
    static func names(in text: String) -> [String] {
        let chars = Array(text)
        let keys = ["systemImage:", "systemName:", "symbol:"].map { Array($0) }
        var out: [String] = []
        var i = 0
        while i < chars.count {
            guard let key = keys.first(where: { key in
                i + key.count <= chars.count && Array(chars[i..<(i + key.count)]) == key
            }), i == 0 || !(chars[i - 1].isLetter || chars[i - 1].isNumber) else {
                i += 1
                continue
            }
            var j = i + key.count
            var depth = 0
            var last: Character = ":"
            scan: while j < chars.count {
                let c = chars[j]
                switch c {
                case "\n":
                    break scan
                case "\"":
                    var k = j + 1
                    var literal = ""
                    while k < chars.count, chars[k] != "\"", chars[k] != "\n" {
                        if chars[k] == "\\" { k += 1 }
                        if k < chars.count { literal.append(chars[k]) }
                        k += 1
                    }
                    if (last == ":" || last == "?"), literal.range(of: #"^[a-z0-9]+(\.[a-z0-9]+)*$"#, options: .regularExpression) != nil {
                        out.append(literal)
                    }
                    last = "\""
                    j = k + 1
                    continue scan
                case "(", "[":
                    depth += 1
                case ")", "]":
                    if depth == 0 { break scan }
                    depth -= 1
                case ",", "{":
                    if depth == 0 { break scan }
                default:
                    break
                }
                if !c.isWhitespace { last = c }
                j += 1
            }
            i = max(j, i + 1)
        }
        return out
    }
}
