import XCTest
import UIKit

@MainActor
final class ManagementUITests: XCTestCase {
    private func launch(_ extra: [String] = []) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"] + extra
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
        let field = app.textFields.matching(NSPredicate(format: "label == %@", "确认口令")).firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        app.buttons["management.submit"].tap()
        XCTAssertTrue(app.staticTexts["请输入确认口令：RESET_LEDGER。"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["确认重置资金台账"].exists)
    }

    func testRestoreCannotCommitWithoutPreviewingAFile() {
        let app = launch()
        // 0.21.0 起「备份与维护」排在「工具与设置」最后（命中调试之后），小屏首屏可能放不下：先滚到它。
        let maintenance = app.buttons["备份与维护"]
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        reveal(maintenance, in: app)
        maintenance.tap()
        app.buttons["配置导入与导出"].tap()
        let choose = app.buttons["选择文件"]
        for _ in 0..<8 where !choose.isHittable { app.swipeUp() }
        XCTAssertTrue(choose.exists)
        XCTAssertFalse(app.buttons["management.file.confirm"].exists)
        XCTAssertFalse(app.buttons["预览导入影响"].isEnabled)
    }

    /// “App 设置 › 关于 → App 更新日志”读取随 App 打包的 ios/CHANGELOG.md，fixture 模式下不连服务器也能显示，
    /// 并标出当前安装的版本。
    func testAboutShowsBundledAppChangelog() {
        let app = launch()
        openAppSettingsEntry("settings.about", in: app)
        let entry = app.buttons["about.appChangelog"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        for _ in 0..<4 where !entry.isHittable { app.swipeUp() }
        entry.tap()
        XCTAssertTrue(app.navigationBars["App 更新日志"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.descendants(matching: .any)["changelog.unavailable"].exists)
        XCTAssertTrue(app.descendants(matching: .any)["changelog.list"].firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["changelog.current"].firstMatch.waitForExistence(timeout: 5))
        let oldest = app.staticTexts["首个原生客户端与 APNs"]
        // 更新日志随版本增长，最旧条目在底部：快速滑动并多给几次（0.19.0 起 30 次普通滑动到不了底）。
        for _ in 0..<60 where !oldest.exists { app.swipeUp(velocity: .fast) }
        XCTAssertTrue(oldest.exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "App 更新日志"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testServerUpdateRequiresConfirmationBeforeApplying() {
        let app = launch()
        openAppSettingsEntry("settings.update", in: app)
        XCTAssertTrue(app.navigationBars["软件更新"].waitForExistence(timeout: 10))
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
        app.buttons["more.logs"].tap()
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
        openAppSettingsEntry("settings.update", in: app)
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
        // 任务完成后先等服务器恢复（第 21 轮），恢复后才显示“更新完成”；位于进度区下方，medium 高度的面板里
        // 可能在可见区外（List 懒加载），等不到就向下滚动找。
        if !applied.waitForExistence(timeout: 30) { scrollUpdateSheet(to: applied, in: app) }
        XCTAssertTrue(applied.waitForExistence(timeout: 15))
        XCTAssertFalse(app.buttons["update.apply"].exists)
    }

    /// 第 21 轮：更新任务到 100% 后不再停在进度条，自动等服务器恢复（期间第一次读版本失败 = 重启中），
    /// 读到新版本后自动重新检查：显示新版本号和「已是最新」，不用手动关闭任务或下拉。
    func testServerUpdateWaitsForServerAndRefreshesVersion() {
        let app = launch()
        openAppSettingsEntry("settings.update", in: app)
        let open = app.buttons["update.open"]
        for _ in 0..<4 where !open.isHittable { app.swipeUp() }
        XCTAssertTrue(open.waitForExistence(timeout: 10))
        open.tap()
        let apply = app.buttons["update.apply"]
        scrollUpdateSheet(to: apply, in: app)
        XCTAssertTrue(apply.waitForExistence(timeout: 10))
        apply.tap()
        let confirm = app.buttons["update.confirm"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 10))
        confirm.tap()
        XCTAssertTrue(app.descendants(matching: .any)["update.progress"].waitForExistence(timeout: 10))
        let recovering = app.descendants(matching: .any)["update.recovering"]
        XCTAssertTrue(recovering.waitForExistence(timeout: 25), "任务完成后应进入「等待服务器恢复」，而不是停在 100%")
        XCTAssertFalse(app.descendants(matching: .any)["update.progress"].exists, "等恢复时不再显示 100% 进度条")
        let applied = app.descendants(matching: .any)["update.applied"]
        if !applied.waitForExistence(timeout: 20) { scrollUpdateSheet(to: applied, in: app) }
        XCTAssertTrue(applied.waitForExistence(timeout: 10))
        let recovered = NSPredicate(format: "label CONTAINS %@ AND label CONTAINS %@", "0.105.0-beta.1", "已是最新")
        expectation(for: recovered, evaluatedWith: applied)
        waitForExpectations(timeout: 15)
        XCTAssertFalse(app.buttons["update.apply"].exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "服务器更新完成后自动刷新版本"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        let finish = app.buttons["update.finishJob"]
        scrollUpdateSheet(to: finish, in: app)
        finish.tap()
        // 状态区在面板最上方；刚才为点「收起更新记录」滚到了下面，List 懒加载时它不在可见区就不存在，往回滚找。
        let latest = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", "服务器已是最新版本")).firstMatch
        scrollUpdateSheetBack(to: latest, in: app)
        XCTAssertTrue(latest.waitForExistence(timeout: 10), "收起后状态区应显示「服务器已是最新版本」")
        let current = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", "0.105.0-beta.1")).firstMatch
        XCTAssertTrue(current.exists)
    }

    /// 在更新面板里往回（向上）滚，直到目标出现；只在列表内慢拖，找到即停，避免拖到顶后把面板拉下关闭。
    private func scrollUpdateSheetBack(to element: XCUIElement, in app: XCUIApplication, attempts: Int = 8) {
        let list = app.otherElements["update.sheet"].collectionViews.firstMatch
        guard list.waitForExistence(timeout: 10) else { return }
        for _ in 0..<attempts {
            if element.waitForExistence(timeout: 1.5) { return }
            let frame = list.frame
            let start = list.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3))
            let end = start.withOffset(CGVector(dx: 0, dy: frame.height * 0.4))
            start.press(forDuration: 0.1, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.4)
        }
    }

    func testLogDetailExplainsMessageFailureAndOpensTimeline() {
        let app = launch()
        app.buttons["more.logs"].tap()
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
    /// 第 20 轮：交互 Bot Token 行有标题，圆点遮罩；已保存的 Token 点眼睛才向服务器读取明文，再点隐藏。
    func testInteractionTokenSecretFieldRevealsSavedValue() {
        let app = launch()
        let entry = app.tabBars.buttons["交互"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        let title = app.staticTexts["interaction.token.title"]
        XCTAssertTrue(title.waitForExistence(timeout: 10))
        XCTAssertEqual(title.label, "交互 Bot Token")
        let secure = app.secureTextFields["interaction.token"]
        XCTAssertTrue(secure.exists)
        XCTAssertTrue((secure.placeholderValue ?? "").contains("已配置"), secure.placeholderValue ?? "")
        let eye = app.buttons["interaction.token.reveal"]
        reveal(eye, in: app)
        XCTAssertTrue(eye.isEnabled, "已保存的 Token 在新服务器上可以查看")
        XCTAssertEqual(eye.label, "显示")
        eye.tap()
        let plain = app.textFields["interaction.token"]
        XCTAssertTrue(plain.waitForExistence(timeout: 10))
        XCTAssertEqual(plain.value as? String, "fixture-interaction_bot_token-plaintext")
        XCTAssertEqual(eye.label, "隐藏")
        eye.tap()
        XCTAssertTrue(app.secureTextFields["interaction.token"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.textFields["interaction.token"].exists)
    }

    /// 旧服务器没有查看接口：已保存的 Token 显示「已配置（此服务器版本不支持查看）」，眼睛只对新输入生效。
    func testInteractionTokenSecretFieldOnOldServer() {
        let app = launch(["--ui-test-old-server-secrets"])
        let entry = app.tabBars.buttons["交互"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        let note = app.staticTexts["interaction.token.note"]
        XCTAssertTrue(note.waitForExistence(timeout: 10))
        XCTAssertTrue(note.label.contains("此服务器版本不支持查看"), note.label)
        let eye = app.buttons["interaction.token.reveal"]
        reveal(eye, in: app)
        XCTAssertFalse(eye.isEnabled, "旧服务器上已保存的值不能查看")
        let secure = app.secureTextFields["interaction.token"]
        secure.tap()
        secure.typeText("123:NEW")
        XCTAssertTrue(eye.isEnabled, "新输入的值可以切换明文")
        eye.tap()
        let plain = app.textFields["interaction.token"]
        XCTAssertTrue(plain.waitForExistence(timeout: 5))
        XCTAssertEqual(plain.value as? String, "123:NEW")
    }

    /// 第 20 轮（iOS 0.24.0）：交互页补齐 Web「身份配置」右栏——真实转账结果通知 Bot ID 搬到交互页，
    /// 新增「测试模拟通知 Bot」：Token（统一密钥框，已保存值点眼睛读取）、当前测试 Bot ID、模拟测试群原生多选 + 手填、
    /// 测试 Bot 群成员状态；交互 Bot 的入群情况改名「监听群成员状态」并放进交互 Bot 分组。保存一次提交全部字段。
    func testInteractionTestNotifyBotSection() {
        let app = launch()
        let entry = app.tabBars.buttons["交互"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        XCTAssertTrue(app.buttons["interaction.statusCard"].waitForExistence(timeout: 10))

        let listen = app.descendants(matching: .any)["interaction.memberships"].firstMatch
        reveal(listen, in: app)
        XCTAssertTrue(listen.label.contains("监听群成员状态"), listen.label)

        // 第 21 轮：两种通知 Bot 收进二级页「通知 Bot 设置」，交互页只剩一行带状态摘要的入口。
        XCTAssertFalse(app.staticTexts["interaction.transferToken.title"].exists, "测试模拟通知 Bot 不再直接放在交互页")
        let notifyBots = app.buttons["interaction.notifyBots"]
        reveal(notifyBots, in: app)
        XCTAssertTrue(notifyBots.label.contains("通知 Bot 设置"), notifyBots.label)
        XCTAssertTrue(notifyBots.label.contains("真实 1 个"), notifyBots.label)
        XCTAssertTrue(notifyBots.label.contains("测试 Bot 已配置"), notifyBots.label)
        XCTAssertTrue(notifyBots.label.contains("2 个测试群"), notifyBots.label)
        notifyBots.tap()
        XCTAssertTrue(app.navigationBars["通知 Bot 设置"].waitForExistence(timeout: 10))

        let trusted = app.descendants(matching: .any)["interaction.trustedBotIDs"].firstMatch
        reveal(trusted, in: app)
        XCTAssertTrue((trusted.value as? String ?? "").contains("777000"), "真实通知 Bot ID 现在在交互页上")

        let title = app.staticTexts["interaction.transferToken.title"]
        reveal(title, in: app)
        XCTAssertEqual(title.label, "测试模拟通知 Bot Token")
        let secure = app.secureTextFields["interaction.transferToken"]
        XCTAssertTrue(secure.exists)
        XCTAssertTrue((secure.placeholderValue ?? "").contains("已配置"), secure.placeholderValue ?? "")
        let eye = app.buttons["interaction.transferToken.reveal"]
        reveal(eye, in: app)
        eye.tap()
        let plain = app.textFields["interaction.transferToken"]
        XCTAssertTrue(plain.waitForExistence(timeout: 10))
        XCTAssertEqual(plain.value as? String, "fixture-transfer_bot_token-plaintext")
        eye.tap()
        XCTAssertTrue(app.secureTextFields["interaction.transferToken"].waitForExistence(timeout: 5))

        let botID = app.descendants(matching: .any)["interaction.transferBotID"].firstMatch
        reveal(botID, in: app)
        XCTAssertTrue(botID.label.contains("8980553289"), botID.label)

        let testChats = app.buttons["interaction.testChats"]
        reveal(testChats, in: app)
        XCTAssertTrue(testChats.label.contains("2 个已选"), testChats.label)
        let testMembers = app.descendants(matching: .any)["interaction.testMemberships"].firstMatch
        reveal(testMembers, in: app)
        XCTAssertTrue(testMembers.label.contains("测试 Bot 群成员状态"), testMembers.label)
        XCTAssertTrue(testMembers.label.contains("1/2"), testMembers.label)
        XCTAssertTrue(app.buttons["interaction.clearNotify"].exists)

        reveal(testChats, in: app)
        testChats.tap()
        XCTAssertTrue(app.navigationBars["模拟测试群"].waitForExistence(timeout: 10))
        let count = app.staticTexts["interaction.testChats.count"]
        XCTAssertTrue(count.waitForExistence(timeout: 5))
        XCTAssertEqual(count.label, "2 个已选")
        XCTAssertTrue(app.buttons["移除 -1003806095342"].exists, "不在已允许会话里的手填 Chat ID 单独列出")
        let peerA = app.switches["interaction.testChats.peer.-1001"]
        let peerB = app.switches["interaction.testChats.peer.-1002"]
        XCTAssertTrue(peerA.exists)
        XCTAssertEqual(peerA.value as? String, "1", "测试群 A 已选")
        XCTAssertEqual(peerB.value as? String, "0")
        peerB.coordinate(withNormalizedOffset: CGVector(dx: 0.92, dy: 0.5)).tap()
        XCTAssertEqual(peerB.value as? String, "1")
        XCTAssertEqual(count.label, "3 个已选")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        XCTAssertTrue(testChats.waitForExistence(timeout: 5))
        XCTAssertTrue(testChats.label.contains("3 个已选"), testChats.label)
        XCTAssertTrue(app.descendants(matching: .any)["interaction.notifyBots.unsaved"].firstMatch.exists, "二级页提示回交互页保存")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        XCTAssertTrue(notifyBots.waitForExistence(timeout: 5))
        XCTAssertTrue(notifyBots.label.contains("3 个测试群"), notifyBots.label)
        XCTAssertTrue(notifyBots.label.contains("未保存"), notifyBots.label)
        let save = app.buttons["interaction.save"]
        XCTAssertTrue(save.isEnabled, "改了模拟测试群后应能保存")
        save.tap()
        let saved = app.descendants(matching: .any)["interaction.saved"].firstMatch
        let saveError = app.descendants(matching: .any)["interaction.saveError"].firstMatch
        if !saved.waitForExistence(timeout: 10) {
            for _ in 0..<8 where !(saved.exists || saveError.exists) { app.swipeDown() }
        }
        XCTAssertFalse(saveError.exists, saveError.exists ? saveError.label : "")
        XCTAssertTrue(saved.exists, "保存后应显示成功结果")
        XCTAssertFalse(save.isEnabled)
        reveal(notifyBots, in: app)
        XCTAssertTrue(notifyBots.label.contains("3 个测试群"), "保存后服务器返回的模拟测试群：\(notifyBots.label)")
        XCTAssertFalse(notifyBots.label.contains("未保存"), notifyBots.label)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "交互页通知 Bot 设置入口"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    /// 旧服务器的交互配置没有测试模拟通知 Bot 字段：整组隐藏，真实通知 Bot ID 仍可编辑。
    func testInteractionTestNotifyBotHiddenOnOldServer() {
        let app = launch(["--ui-test-old-server-interaction"])
        let entry = app.tabBars.buttons["交互"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        XCTAssertTrue(app.buttons["interaction.statusCard"].waitForExistence(timeout: 10))
        let notifyBots = app.buttons["interaction.notifyBots"]
        reveal(notifyBots, in: app)
        XCTAssertTrue(notifyBots.label.contains("真实 1 个"), notifyBots.label)
        XCTAssertFalse(notifyBots.label.contains("测试"), "旧服务器没有测试模拟通知 Bot：\(notifyBots.label)")
        notifyBots.tap()
        XCTAssertTrue(app.navigationBars["通知 Bot 设置"].waitForExistence(timeout: 10))
        let trusted = app.descendants(matching: .any)["interaction.trustedBotIDs"].firstMatch
        XCTAssertTrue(trusted.waitForExistence(timeout: 5), "真实通知 Bot ID 仍可编辑")
        XCTAssertFalse(app.staticTexts["interaction.transferToken.title"].exists)
        XCTAssertFalse(app.buttons["interaction.testChats"].exists)
        XCTAssertFalse(app.buttons["interaction.clearNotify"].exists)
    }

    /// 第 21 轮：交互页「运行状态」从 4～6 行的分组缩成一行小卡（状态点 + 一句话 + 关键数字），点进去看全部细节。
    func testInteractionStatusCardIsCompactAndOpensDetail() {
        let app = launch()
        let entry = app.tabBars.buttons["交互"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        let card = app.buttons["interaction.statusCard"]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        XCTAssertTrue(card.label.contains("交互运行中"), card.label)
        XCTAssertTrue(card.label.contains("@telepilot_play_bot"), card.label)
        XCTAssertTrue(card.label.contains("1/2 条规则启用"), card.label)
        XCTAssertTrue(card.label.contains("1 个群"), card.label)
        XCTAssertLessThan(card.frame.height, 90, "运行状态应是一行紧凑卡片：\(card.frame)")
        XCTAssertFalse(app.descendants(matching: .any)["interaction.status.gate"].firstMatch.exists, "细节不再铺在交互页上")
        // 紧接着就是交互总闸，不用再滑过一整组状态。
        XCTAssertTrue(app.switches["interaction.enabled"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.switches["interaction.enabled"].isHittable)

        card.tap()
        XCTAssertTrue(app.navigationBars["运行状态"].waitForExistence(timeout: 10))
        let runtime = app.descendants(matching: .any)["interaction.status.runtime"].firstMatch
        XCTAssertTrue(runtime.waitForExistence(timeout: 5))
        XCTAssertTrue(runtime.label.contains("运行中"), runtime.label)
        XCTAssertTrue(app.descendants(matching: .any)["interaction.status.gate"].firstMatch.label.contains("已启用"))
        XCTAssertTrue(app.descendants(matching: .any)["interaction.status.token"].firstMatch.label.contains("@telepilot_play_bot"))
        XCTAssertTrue(app.descendants(matching: .any)["interaction.status.rules"].firstMatch.label.contains("1/2"))
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "交互运行状态详情"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testInteractionCenterEditsRulesAndSaves() {
        let app = launch()
        // 0.18.0 起交互中心在默认 Dock 的「交互」格。
        let entry = app.tabBars.buttons["交互"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        // 第 21 轮：运行状态缩成一行卡片（状态点 + 一句话 + 关键数字）。
        let subscribed = app.buttons["interaction.statusCard"]
        XCTAssertTrue(subscribed.waitForExistence(timeout: 10))
        XCTAssertTrue(subscribed.label.contains("运行中"), subscribed.label)
        XCTAssertTrue(subscribed.label.contains("1/2"), subscribed.label)
        let save = app.buttons["interaction.save"]
        XCTAssertTrue(save.exists)
        XCTAssertFalse(save.isEnabled, "没有修改时不能保存")

        let notice = app.buttons["interaction.rule.rule-notice"]
        for _ in 0..<10 where !(notice.exists && notice.isHittable) { app.swipeUp() }
        XCTAssertTrue(notice.exists)
        XCTAssertTrue(notice.label.contains("停用"), notice.label)
        notice.tap()
        let enabled = app.switches["interaction.rule.enabled"]
        XCTAssertTrue(enabled.waitForExistence(timeout: 10))
        // 推入编辑页的转场刚结束时点按偶尔被吞（CI 两次迭代都没打开）：等开关真的变成「开」，没变就再点一次。
        let turnedOn = NSPredicate(format: "value == %@", "1")
        for _ in 0..<3 where (enabled.value as? String) != "1" {
            enabled.coordinate(withNormalizedOffset: CGVector(dx: 0.92, dy: 0.5)).tap()
            if XCTWaiter().wait(for: [XCTNSPredicateExpectation(predicate: turnedOn, object: enabled)], timeout: 3) == .completed { break }
        }
        XCTAssertEqual(enabled.value as? String, "1", "规则开关应已打开")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        XCTAssertTrue(save.waitForExistence(timeout: 5))
        XCTAssertTrue(save.isEnabled, "修改后应能保存")
        save.tap()
        // 保存结果显示在列表顶部，保存后列表会滚回顶部。
        let saved = app.descendants(matching: .any)["interaction.saved"].firstMatch
        let saveError = app.descendants(matching: .any)["interaction.saveError"].firstMatch
        if !saved.waitForExistence(timeout: 10) {
            for _ in 0..<8 where !(saved.exists || saveError.exists) { app.swipeDown() }
        }
        XCTAssertFalse(saveError.exists, saveError.exists ? saveError.label : "")
        XCTAssertTrue(saved.exists, "保存后应显示成功结果")
        XCTAssertFalse(save.isEnabled)
        XCTAssertTrue(subscribed.waitForExistence(timeout: 5))
        XCTAssertTrue(subscribed.label.contains("2/2"), subscribed.label)
        for _ in 0..<10 where !(notice.exists && notice.isHittable) { app.swipeUp() }
        XCTAssertTrue(notice.exists)
        XCTAssertTrue(notice.label.contains("启用"), notice.label)

        let ask = app.buttons["interaction.rule.rule-ask"]
        for _ in 0..<10 where !(ask.exists && ask.isHittable) { app.swipeUp() }
        ask.tap()
        XCTAssertTrue(app.descendants(matching: .any)["interaction.ruleEditor"].firstMatch.waitForExistence(timeout: 10))
        let params = app.staticTexts["玩法参数"]
        for _ in 0..<6 where !params.exists { app.swipeUp() }
        XCTAssertTrue(params.exists, "保存后插件玩法参数应保留")
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "交互规则编辑"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    /// 第 21 轮：输入行左侧始终有标题（填了值也看得出是哪一项），数字项带单位，空的可选项显示「可选」；
    /// 多行项标题在输入框上方。
    func testRuleEditorFieldsKeepTitlesAndUnits() {
        let app = launch()
        let entry = app.tabBars.buttons["交互"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        XCTAssertTrue(app.buttons["interaction.statusCard"].waitForExistence(timeout: 10))
        let ask = app.buttons["interaction.rule.rule-ask"]
        for _ in 0..<10 where !(ask.exists && ask.isHittable) { app.swipeUp() }
        ask.tap()
        XCTAssertTrue(app.descendants(matching: .any)["interaction.ruleEditor"].firstMatch.waitForExistence(timeout: 10))

        // 规则名称：左标题 + 右侧值
        let name = app.textFields["interaction.rule.name"]
        XCTAssertTrue(name.waitForExistence(timeout: 5))
        XCTAssertEqual(name.value as? String, "AI 答疑")
        let nameTitle = app.staticTexts["interaction.rule.name.title"]
        XCTAssertTrue(nameTitle.exists)
        XCTAssertEqual(nameTitle.label, "规则名称")
        XCTAssertLessThan(nameTitle.frame.minX, name.frame.minX, "标题在输入框左边")

        // 按页面从上到下的顺序查：「每用户每日上限」在「参与有效期」上方（第 22 轮：原先先查下面那项，
        // 偶尔把上面那项滚出屏幕顶部，再往下找就永远找不到）。
        // 空的可选数字项：灰色提示「可选」，不显示单位
        let daily = app.textFields["interaction.rule.dailyLimit"]
        reveal(daily, in: app)
        XCTAssertEqual(daily.placeholderValue, "可选")
        let dailyUnit = app.staticTexts["interaction.rule.dailyLimit.unit"]
        XCTAssertFalse(dailyUnit.exists, "空值不显示单位")

        // 有值的数字项：标题 + 值 + 单位
        let valid = app.textFields["interaction.rule.validSeconds"]
        reveal(valid, in: app)
        XCTAssertEqual(valid.value as? String, "600")
        XCTAssertEqual(app.staticTexts["interaction.rule.validSeconds.title"].label, "参与有效期")
        let validUnit = app.staticTexts["interaction.rule.validSeconds.unit"]
        XCTAssertTrue(validUnit.exists)
        XCTAssertEqual(validUnit.label, "秒")
        XCTAssertGreaterThanOrEqual(validUnit.frame.minX, valid.frame.maxX - 1, "单位在值的右边")

        // 多行项：标题在输入框上方，可选项标「可选」（先查这一项，数字键盘弹出后没有收起键，会挡住下面的行）
        let open = app.descendants(matching: .any)["interaction.rule.open"].firstMatch
        reveal(open, in: app)
        let openTitle = app.descendants(matching: .any)["interaction.rule.open.title"].firstMatch
        XCTAssertTrue(openTitle.exists)
        XCTAssertTrue(openTitle.label.contains("开启指令") && openTitle.label.contains("可选"), openTitle.label)
        XCTAssertLessThanOrEqual(openTitle.frame.maxY, open.frame.minY + 2, "多行项标题在输入框上方")

        // 回到每日上限填值：出现单位，标题不消失
        reveal(daily, in: app, up: false)
        focus(daily)
        daily.typeText("6")
        XCTAssertTrue(dailyUnit.waitForExistence(timeout: 3))
        XCTAssertEqual(dailyUnit.label, "次")
        XCTAssertEqual(app.staticTexts["interaction.rule.dailyLimit.title"].label, "每用户每日上限")

        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "输入行标题与单位"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    /// 运行树（第 19 轮）：插件页「插件 | 运行」切到运行视图。
    private func openRuntimeTree(_ app: XCUIApplication) {
        let plugins = app.tabBars.buttons["插件"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 20))
        plugins.tap()
        let mode = app.buttons["plugins.mode.runtime"]
        XCTAssertTrue(mode.waitForExistence(timeout: 15))
        mode.tap()
        XCTAssertTrue(app.staticTexts["runtime.profile"].waitForExistence(timeout: 15))
    }

    /// 第 24 轮：App 设置 › Agent ›「显示思考过程」（默认开），关掉后 Agent 回答上方不再有「思考了…」这一行。
    func testShowReasoningSettingHidesThinkingRow() {
        // 默认显示思考行由 AssistantUITests.testReasoningRowCollapsedAboveAnswerAndExpands 覆盖；
        // 这里先在「App 设置」关掉开关，再进 Agent 确认思考行完全不显示（避免 Agent 收起/更多页滚动的偶发定位问题）。
        let app = launch(["--ui-test-reasoning"])
        // 「App 设置」在「更多」最后一组，List 懒加载：首屏放不下时它还不存在，不能先等它出现（CI 上等了 15 秒失败于此），
        // 要先等首屏的「账号」行出现，再滑到「App 设置」（与 openAppSettingsEntry 相同）。
        let settings = app.buttons["more.settings"]
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 15))
        reveal(settings, in: app)
        XCTAssertTrue(settings.waitForExistence(timeout: 5))
        waitUntilStill(settings)
        settings.tap()
        XCTAssertTrue(app.navigationBars["App 设置"].waitForExistence(timeout: 10))
        // 「Agent › 显示思考过程」在设置页中部，同样可能要先滑到。
        let toggle = app.switches["settings.showReasoning"]
        reveal(toggle, in: app)
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        XCTAssertEqual(toggle.value as? String, "1")
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.92, dy: 0.5)).tap()
        expectation(for: NSPredicate(format: "value == %@", "0"), evaluatedWith: toggle)
        waitForExpectations(timeout: 5)
        // 滑动后 Dock 可能已缩小（.tabBarMinimizeBehavior(.onScrollDown)），只剩当前分页：往回滑让它展开再点 Agent。
        let agent = app.tabBars.buttons["Agent"]
        for _ in 0..<4 where !(agent.exists && agent.isHittable) { app.swipeDown() }
        XCTAssertTrue(agent.waitForExistence(timeout: 10))
        agent.tap()
        XCTAssertTrue(app.descendants(matching: .any)["assistant.composer"].firstMatch.waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["服务状态正常。这里是界面测试数据。"].waitForExistence(timeout: 15))
        XCTAssertEqual(app.buttons.matching(identifier: "assistant.reasoning.toggle").count, 0, "关闭后完全不显示思考行")
    }

    /// 0.21.0 按 iPhone「设置 › 通用」排序：
    /// - 「更多」：「工具与设置」为 服务器设置 → Webhook → 命中调试 → 备份与维护；最后一组 App 只有「App 设置」，不再有「关于与更新」。
    /// - 「App 设置」：最顶部一组「关于」「软件更新」（两页，标题 = 入口名），常用配置居中，「更换服务器／退出登录」单独在最底部一组。
    /// - 「关于」只看信息不含更新按钮；「软件更新」有全 App 唯一的「检查服务器更新」与 App 安装包入口。
    func testAppSettingsFollowsGeneralSettingsOrder() {
        let app = launch()
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        let server = app.buttons["服务器设置"]
        let webhook = app.buttons["Webhook"]
        let debug = app.buttons["more.dispatchDebug"]
        let maintenance = app.buttons["备份与维护"]
        let settings = app.buttons["more.settings"]
        reveal(settings, in: app)
        // 小屏时滑到底后「服务器设置」可能已滚出可见区（List 回收单元格）：小步下拉直到整组都在。
        for _ in 0..<3 where !(server.exists && settings.exists) {
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4))
            start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: 120)))
        }
        for element in [server, webhook, debug, maintenance] {
            XCTAssertTrue(element.waitForExistence(timeout: 5), "\(element) 应在「工具与设置」里")
        }
        XCTAssertLessThan(server.frame.minY, webhook.frame.minY, "服务器设置应排在 Webhook 前")
        XCTAssertLessThan(webhook.frame.minY, debug.frame.minY, "Webhook 应排在命中调试前")
        XCTAssertLessThan(debug.frame.minY, maintenance.frame.minY, "备份与维护应排在本组最后")
        XCTAssertLessThan(maintenance.frame.maxY, settings.frame.minY, "App 设置应在最后一组")
        XCTAssertFalse(app.buttons["more.about"].exists, "「关于与更新」已拆分并移进 App 设置")
        XCTAssertFalse(app.staticTexts["关于与更新"].exists)

        reveal(settings, in: app)
        waitUntilStill(settings)
        settings.tap()
        XCTAssertTrue(app.navigationBars["App 设置"].waitForExistence(timeout: 10))
        let about = app.buttons["settings.about"]
        let update = app.buttons["settings.update"]
        let appearance = app.descendants(matching: .any)["settings.appearance"].firstMatch
        XCTAssertTrue(about.waitForExistence(timeout: 10))
        XCTAssertTrue(update.exists)
        XCTAssertTrue(about.label.contains("关于"), about.label)
        XCTAssertTrue(update.label.contains("软件更新"), update.label)
        XCTAssertTrue(appearance.waitForExistence(timeout: 5))
        XCTAssertLessThan(about.frame.minY, update.frame.minY, "关于在软件更新上方")
        XCTAssertLessThan(update.frame.maxY, appearance.frame.minY, "关于／软件更新在最顶部一组，外观在其下")
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "App 设置顶部"
        screenshot.lifetime = .keepAlways
        add(screenshot)

        // 危险操作单独在最底部一组：更换服务器、退出登录排在 Cloudflare Access 与其它配置之后。
        let access = app.buttons["settings.cfAccess"]
        let change = app.buttons["settings.changeServer"]
        let logout = app.buttons["settings.logout"]
        reveal(logout, in: app)
        XCTAssertTrue(change.exists)
        XCTAssertLessThan(change.frame.minY, logout.frame.minY)
        if access.exists { XCTAssertLessThan(access.frame.maxY, change.frame.minY, "退出登录一组应在常用配置之后") }
        let usage = app.staticTexts["管理功能使用服务器现有权限"]
        if usage.exists { XCTAssertLessThan(usage.frame.maxY, change.frame.minY, "危险操作应在最底部") }

        // 「关于」：标题 = 入口名，只有信息，不含更新按钮。
        reveal(about, in: app, up: false)
        XCTAssertTrue(tap(about, expecting: app.navigationBars["关于"]))
        XCTAssertTrue(app.descendants(matching: .any)["about.appVersion"].firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["about.serverVersion"].firstMatch.exists)
        let changelog = app.buttons["about.appChangelog"]
        XCTAssertTrue(changelog.exists)
        for _ in 0..<4 { app.swipeUp() }
        XCTAssertFalse(app.buttons["update.open"].exists, "检查服务器更新只在「软件更新」里")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // 「软件更新」：标题 = 入口名，服务器与 App 两组。
        XCTAssertTrue(update.waitForExistence(timeout: 10))
        XCTAssertTrue(tap(update, expecting: app.navigationBars["软件更新"]))
        XCTAssertTrue(app.descendants(matching: .any)["softwareUpdate.serverVersion"].firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["update.open"].waitForExistence(timeout: 10))
        let appVersion = app.descendants(matching: .any)["softwareUpdate.appVersion"].firstMatch
        let download = app.descendants(matching: .any)["softwareUpdate.appDownload"].firstMatch
        if !appVersion.exists { app.swipeUp() }
        XCTAssertTrue(appVersion.waitForExistence(timeout: 5))
        XCTAssertTrue(download.exists)
        XCTAssertFalse(app.buttons["about.appChangelog"].exists, "更新日志在「关于」里，不在软件更新重复")
        let updateShot = XCTAttachment(screenshot: app.screenshot())
        updateShot.name = "软件更新"
        updateShot.lifetime = .keepAlways
        add(updateShot)
    }

    /// 等列表滚动停下（元素位置连续两次不变）再点：滑动后的惯性滚动还没停就点，偶尔会点到别的行（CI 偶发失败）。
    private func waitUntilStill(_ element: XCUIElement, timeout: TimeInterval = 3) {
        guard element.exists else { return }
        let deadline = Date().addingTimeInterval(timeout)
        var last = element.frame
        var stable = 0
        while Date() < deadline && stable < 2 {
            Thread.sleep(forTimeInterval: 0.2)
            let now = element.exists ? element.frame : .zero
            stable = now == last ? stable + 1 : 0
            last = now
        }
    }

    /// 0.21.0：「关于」「软件更新」拆成两页，放在「更多 › App 设置」最顶部一组（像 iPhone「通用」的「关于本机／软件更新」）。
    /// 「App 设置」在「更多」最后一组，List 懒加载：先滑到它再点，再点设置页顶部的入口。
    private func openAppSettingsEntry(_ identifier: String, in app: XCUIApplication) {
        let settings = app.buttons["more.settings"]
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        reveal(settings, in: app)
        XCTAssertTrue(settings.waitForExistence(timeout: 5))
        waitUntilStill(settings)
        settings.tap()
        XCTAssertTrue(app.navigationBars["App 设置"].waitForExistence(timeout: 10))
        let entry = app.buttons[identifier]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
    }

    /// 在列表里上下滚动，直到元素整行落在「导航栏下沿」与「Dock／值守提示上沿」之间，再交给调用方点按。
    ///
    /// 第 22 轮：
    /// - **上沿**：iOS 26 起导航栏是悬浮在列表之上的玻璃层，行滚到它下面时仍 exists/isHittable，但点按落在导航栏上
    ///   （CI 录像：「关于」行停在「App 设置」导航栏下面，点了没进「关于」；值守中「恢复生产」同理）。
    /// - **下沿**：Dock 标签栏与值守提示浮在列表之上，元素中心落在它们里面时同样会被截走；值守中另外要求在屏幕上 3/4。
    /// - **不用惯性滑动**：`swipeUp/Down` 是快速轻扫，松手后列表还在滚，偶尔把懒加载的行整段滑过去，或在滚动中点按
    ///   （滚动中的第一次点按只会让列表停下）。改为「按住—拖动—停住再松手」的受控拖动，没有惯性。
    /// - **两个方向都找**：先按 `up` 指定的方向找，到头还没找到就反向（CI 录像：「每用户每日上限」在「参与有效期」上方，
    ///   先找后者时已滚出屏幕上方，只往下找永远找不到）。
    private func reveal(_ element: XCUIElement, in app: XCUIApplication, up: Bool = true, attempts: Int = 10) {
        let window = app.windows.firstMatch.frame
        func bottomLimit() -> CGFloat {
            var bottom = CGFloat.infinity
            let bar = app.tabBars.firstMatch
            if bar.exists, !bar.frame.isEmpty { bottom = bar.frame.minY }
            // 第 20 轮：值守中 Dock 上方多了全局提示（系统底部附件），同样会挡住下面的行。
            // 附件在 Dock 缩小 / 展开切换时不一定能按标识查到（CI 实测），所以另外要求元素在屏幕下方 1/4 以上。
            let banner = app.descendants(matching: .any)["safeWatch.banner"].firstMatch
            if banner.exists, !banner.frame.isEmpty { bottom = min(bottom, banner.frame.minY) }
            let safeWatchOn = banner.exists || app.descendants(matching: .any)["safeWatch.restore"].firstMatch.exists
                || app.buttons["runtime.restore"].exists
            if !window.isEmpty, safeWatchOn {
                bottom = min(bottom, window.minY + window.height * 0.75)
            }
            return bottom
        }
        func topLimit() -> CGFloat {
            let bar = app.navigationBars.firstMatch
            guard bar.exists else { return -.infinity }
            let frame = bar.frame
            // 只认屏幕上部的正常导航栏，防止异常帧把整屏都算成“被遮住”。
            guard !frame.isEmpty, window.isEmpty || frame.maxY < window.minY + window.height * 0.4 else { return -.infinity }
            return frame.maxY
        }
        /// 受控拖动：`contentUp` 为 true 时内容上移（看下面的行），false 时内容下移（看上面的行）。
        func drag(contentUp: Bool, distance: CGFloat) {
            let height = window.isEmpty ? 800 : window.height
            let from = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: contentUp ? 0.7 : 0.3))
            let sign: CGFloat = contentUp ? -1 : 1
            let dy = sign * min(distance, height * 0.45)
            from.press(forDuration: 0.05, thenDragTo: from.withOffset(CGVector(dx: 0, dy: dy)),
                       withVelocity: .default, thenHoldForDuration: 0.3)
        }
        func placed() -> (ok: Bool, fix: Bool?) {
            // 返回 ok=整行在可点区内；fix=需要往哪边挪（true=内容上移），nil=不存在或不可点。
            guard element.exists, element.isHittable else { return (false, nil) }
            let frame = element.frame
            if frame.midY >= bottomLimit() - 8 { return (false, true) }
            if frame.minY < topLimit() + 2 { return (false, false) }
            return (true, nil)
        }
        let height = window.isEmpty ? 800 : window.height
        var contentUp = up
        var moves = 0
        var reversed = false
        for _ in 0..<(attempts * 2 + 6) {
            let state = placed()
            if state.ok {
                waitUntilStill(element)
                if placed().ok { break }
                continue
            }
            if let fix = state.fix {
                // 已在屏幕上，只是压在导航栏或 Dock 下面：小步挪进来。
                drag(contentUp: fix, distance: 160)
                continue
            }
            if moves >= attempts {
                guard !reversed else { break }
                reversed = true
                contentUp.toggle()
                moves = 0
            }
            drag(contentUp: contentUp, distance: height * 0.45)
            moves += 1
        }
        XCTAssertTrue(placed().ok, "\(element) 不可见或被导航栏／标签栏遮住")
    }

    /// 点按后等目标出现；点按被吞（转场刚结束、列表刚停）且原元素还在原处时再点一次，最多 `retries` 次。
    /// 只在“原元素仍可点、目标没出现”时重点，避免点到新页面上。
    @discardableResult
    private func tap(_ element: XCUIElement, expecting target: XCUIElement, timeout: TimeInterval = 10, retries: Int = 1) -> Bool {
        waitUntilStill(element)
        element.tap()
        if target.waitForExistence(timeout: timeout) { return true }
        for _ in 0..<retries where element.exists && element.isHittable && !target.exists {
            element.tap()
            if target.waitForExistence(timeout: timeout) { return true }
        }
        return target.exists
    }

    /// 点输入框并等它真正拿到键盘焦点再输入：点按落在列表停稳前会被吞，直接 typeText 报
    /// “Neither element nor any descendant has keyboard focus”。
    private func focus(_ field: XCUIElement, at offset: CGVector = CGVector(dx: 0.9, dy: 0.5)) {
        waitUntilStill(field)
        let focused = NSPredicate(format: "hasKeyboardFocus == true")
        for _ in 0..<3 {
            field.coordinate(withNormalizedOffset: offset).tap()
            if XCTWaiter().wait(for: [XCTNSPredicateExpectation(predicate: focused, object: field)], timeout: 5) == .completed { return }
        }
        XCTAssertTrue(field.value(forKey: "hasKeyboardFocus") as? Bool ?? false, "\(field) 没有拿到键盘焦点")
    }

    /// 运行模式卡（预演 → 确认进入值守 → 恢复生产）与 5 个可选模块：被插件需要的关不掉并写明原因；
    /// 没人需要的可以关（确认框 + 现有平台能力接口）；点模块看需要它的插件。服务器设置里的旧入口跳到这里。
    func testRuntimeTreeSafeWatchAndModules() {
        let app = launch()
        openRuntimeTree(app)
        let profile = app.staticTexts["runtime.profile"]
        XCTAssertTrue(profile.label.contains("生产"), profile.label)
        let workers = app.staticTexts["runtime.workers"]
        XCTAssertTrue(workers.label.contains("1/2 在线"), workers.label)

        // AI 被本账号的内容整理助手需要：开关不可用，并写明原因（新服务器 blocked_reason）。
        let ai = app.buttons["runtime.module.ai"]
        reveal(ai, in: app)
        XCTAssertTrue(ai.label.contains("被 1 个插件需要"), ai.label)
        XCTAssertTrue(ai.label.contains("本账号启用的 1 个插件需要它"), ai.label)
        XCTAssertFalse(app.switches["runtime.module.ai.toggle"].isEnabled, "被插件需要的模块不能关")

        // 资金台账没人需要：可关闭，走确认框。
        let ledger = app.buttons["runtime.module.ledger"]
        reveal(ledger, in: app)
        XCTAssertTrue(ledger.label.contains("可关闭"), ledger.label)
        let ledgerToggle = app.switches["runtime.module.ledger.toggle"]
        XCTAssertTrue(ledgerToggle.isEnabled)
        XCTAssertEqual(ledgerToggle.value as? String, "1")
        ledgerToggle.tap()
        let close = app.buttons["关闭"].firstMatch
        XCTAssertTrue(close.waitForExistence(timeout: 5), "关闭模块前应有确认框")
        close.tap()
        let message = app.descendants(matching: .any)["runtime.module.message"].firstMatch
        XCTAssertTrue(message.waitForExistence(timeout: 10))
        expectation(for: NSPredicate(format: "value == %@", "0"), evaluatedWith: app.switches["runtime.module.ledger.toggle"])
        waitForExpectations(timeout: 10)

        // 点模块：列出需要它的插件。
        reveal(ai, in: app, up: false)
        ai.tap()
        XCTAssertTrue(app.navigationBars["AI"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["runtime.moduleDetail.plugin.content_assistant"].waitForExistence(timeout: 5))
        app.navigationBars["AI"].buttons.firstMatch.tap()

        // 进入值守：先预演，确认框列出会关掉什么。
        let safeWatch = app.buttons["runtime.safeWatch"]
        reveal(safeWatch, in: app, up: false)
        safeWatch.tap()
        let preview = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "将关闭：AI、交互 Bot")).firstMatch
        XCTAssertTrue(preview.waitForExistence(timeout: 10), "确认框应列出将关闭的模块")
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "运行树 · 进入值守确认"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        app.buttons["确认进入值守"].firstMatch.tap()
        let restore = app.buttons["runtime.restore"]
        XCTAssertTrue(restore.waitForExistence(timeout: 10), "值守中应显示「恢复生产」")
        reveal(profile, in: app, up: false)
        expectation(for: NSPredicate(format: "label CONTAINS %@", "值守"), evaluatedWith: profile)
        waitForExpectations(timeout: 10)
        reveal(ai, in: app)
        XCTAssertTrue(ai.label.contains("值守模式关闭了它"), ai.label)
        XCTAssertFalse(app.switches["runtime.module.ai.toggle"].isEnabled, "值守中不能单独开模块")

        reveal(restore, in: app, up: false)
        let confirmRestore = app.buttons["确认恢复生产"].firstMatch
        XCTAssertTrue(tap(restore, expecting: confirmRestore), "点「恢复生产」应弹出确认框")
        confirmRestore.tap()
        XCTAssertTrue(app.buttons["runtime.safeWatch"].waitForExistence(timeout: 10))
        // List 行滚出屏幕后不在无障碍树里：先滚回顶部让运行模式行可见再断言。
        reveal(profile, in: app, up: false)
        expectation(for: NSPredicate(format: "label CONTAINS %@", "生产"), evaluatedWith: profile)
        waitForExpectations(timeout: 10)
    }

    /// 第 21 轮：运行树只在 插件 ›「运行」，服务器设置里不再有运行树或它的跳转行；插件页「运行」照常可用。
    func testServerSettingsHasNoRuntimeTreeEntry() {
        let app = launch()
        let system = app.buttons["服务器设置"]
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<8 where !(system.exists && system.isHittable) { app.swipeUp() }
        system.tap()
        XCTAssertTrue(app.buttons["system.serverSettings"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["system.runtimeTree"].exists, "服务器设置里不再保留运行树跳转")
        XCTAssertFalse(app.buttons["system.platformTree"].exists, "旧「能力与插件树」入口应删除")
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "运行树")).firstMatch.exists)
    }

    /// 插件按「直通 / 主通道 / 交互 Bot」分组；点插件进本账号配置，交互插件旁「交互规则 ›」跳交互中心；
    /// 底部链接到命中调试。「全部账号」走旧服务器形状（没有 scope／中文名），客户端回退计算照常显示。
    func testRuntimeTreePluginGroupsAndNavigation() {
        let app = launch()
        openRuntimeTree(app)
        let autoReply = app.buttons["runtime.plugin.auto_reply"]
        reveal(autoReply, in: app)
        XCTAssertTrue(app.descendants(matching: .any)["runtime.group.passthrough"].firstMatch.exists)
        XCTAssertTrue(autoReply.label.contains("自动回复"), autoReply.label)
        XCTAssertTrue(autoReply.label.contains("靠直通、命令工作"), autoReply.label)
        XCTAssertEqual(autoReply.value as? String, "已启用")
        let assistant = app.buttons["runtime.plugin.content_assistant"]
        reveal(assistant, in: app)
        XCTAssertTrue(app.descendants(matching: .any)["runtime.group.interaction"].firstMatch.exists)
        XCTAssertTrue(assistant.label.contains("内容整理助手"), assistant.label)
        XCTAssertTrue(assistant.label.contains("+AI"), assistant.label)
        XCTAssertTrue(assistant.label.contains("运行异常"), assistant.label)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "运行树 · 插件分组"
        screenshot.lifetime = .keepAlways
        add(screenshot)

        // 交互规则 › → 交互中心。
        let rules = app.buttons["runtime.plugin.content_assistant.rules"]
        reveal(rules, in: app)
        // 第 22 轮：CI 录像里点「交互规则 ›」后页面原地不动（点按被吞）；原元素还在原处时再点一次。
        XCTAssertTrue(tap(rules, expecting: app.navigationBars["交互"]))
        app.navigationBars["交互"].buttons.firstMatch.tap()

        // 点插件 → 本账号配置。
        reveal(autoReply, in: app, up: false)
        autoReply.tap()
        XCTAssertTrue(app.navigationBars["自动回复"].waitForExistence(timeout: 10))
        app.navigationBars["自动回复"].buttons.firstMatch.tap()

        // 命中调试链接：直接打开原生命中调试页（iOS 0.20.0）。
        let debug = app.buttons["runtimeTree.dispatchDebug"]
        reveal(debug, in: app, attempts: 14)
        debug.tap()
        XCTAssertTrue(app.navigationBars["命中调试"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["dispatchDebug.screen"].firstMatch.waitForExistence(timeout: 10),
                      "运行树底部应打开原生命中调试页")
        app.navigationBars["命中调试"].buttons.firstMatch.tap()

        // 全部账号：旧服务器形状，中文名来自插件目录，「命令」归入主通道。
        let picker = app.buttons["plugins.accountPicker"]
        reveal(picker, in: app, up: false, attempts: 14)
        picker.tap()
        let all = app.buttons["全部账号"]
        XCTAssertTrue(all.waitForExistence(timeout: 5))
        all.tap()
        let guardRow = app.buttons["runtime.plugin.forward_guard"]
        reveal(guardRow, in: app, attempts: 14)
        XCTAssertTrue(guardRow.label.contains("转发风控与来源保护"), guardRow.label)
        XCTAssertTrue(app.descendants(matching: .any)["runtime.group.main"].firstMatch.exists)

        // 备用账号（新服务器账号口径）：自动回复在这个账号未启用。
        reveal(picker, in: app, up: false, attempts: 14)
        picker.tap()
        let backup = app.buttons["备用账号"]
        XCTAssertTrue(backup.waitForExistence(timeout: 5))
        backup.tap()
        reveal(autoReply, in: app)
        expectation(for: NSPredicate(format: "value == %@", "未启用"), evaluatedWith: autoReply)
        waitForExpectations(timeout: 10)
    }

    /// 第 20 轮：「设备配置」改名「登录设备」，列表顶部说明这是登录 Telegram 时上报、显示在 Telegram「设置 › 设备」里的信息。
    func testDeviceProfilesRenamedToLoginDevice() {
        let app = launch()
        let system = app.buttons["服务器设置"]
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<6 where !(system.exists && system.isHittable) { app.swipeUp() }
        XCTAssertTrue(system.waitForExistence(timeout: 5))
        system.tap()
        let entry = app.buttons["system.deviceProfiles"]
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Sudo 权限")).firstMatch.waitForExistence(timeout: 10))
        // 「登录设备」在「连接与通知」组，位于 Sudo 之上：先小步上滑找，滑过头再往回拉。
        for _ in 0..<4 where !(entry.exists && entry.isHittable) { app.swipeUp() }
        for _ in 0..<6 where !(entry.exists && entry.isHittable) { app.swipeDown() }
        XCTAssertTrue(entry.isHittable, "未找到「登录设备」行")
        XCTAssertTrue(entry.label.contains("登录设备"), "行标题应为「登录设备」：\(entry.label)")
        XCTAssertFalse(app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "设备配置")).firstMatch.exists, "不应再出现「设备配置」")
        entry.tap()
        XCTAssertTrue(app.navigationBars["登录设备"].waitForExistence(timeout: 10))
        let intro = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Telegram「设置 › 设备」")).firstMatch
        XCTAssertTrue(intro.waitForExistence(timeout: 5), "列表顶部应有登录设备说明")
        XCTAssertTrue(intro.label.contains("iPhone 15 Pro · iOS 18"))
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
