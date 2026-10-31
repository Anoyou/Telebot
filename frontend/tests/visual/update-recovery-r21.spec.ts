import { expect, test } from "@playwright/test";

import { APP_VERSION } from "../../src/lib/version";
import { installApiFixture } from "./fixtures";

// 第 21 轮：更新任务完成后不再停在 100% / 「更新计划已执行」，自动等服务器恢复并刷新版本；
// 运行树旧深链跳到插件页「运行树」。
test.describe("第 21 轮：更新后自动刷新与运行树深链", () => {
  test("更新任务完成后自动等服务器恢复，显示新版本与已是最新", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "流程与视口无关，只跑桌面");
    const fixture = await installApiFixture(page);
    let updated = false;
    let versionReadsAfterUpdate = 0;
    let jobReads = 0;
    await page.route("**/api/system/version", async (route) => {
      if (!updated) {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ version: APP_VERSION, revision: "aaaa1111aaaa", channel: "beta" }) });
        return;
      }
      versionReadsAfterUpdate += 1;
      if (versionReadsAfterUpdate === 1) {
        await route.fulfill({ status: 502, contentType: "text/plain", body: "Bad Gateway" });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ version: "0.72.0-beta.2", revision: "bbbb2222bbbb", channel: "beta" }) });
    });
    await page.route("**/api/system/check-update", async (route) => {
      const body = updated
        ? { has_update: false, can_check: true, current_version: "0.72.0-beta.2", target_version: "0.72.0-beta.2",
            current_commit: "bbbb2222bbbb", remote_commit: "bbbb2222bbbb", ahead: 0, remote: "origin", branch: "main",
            runtime_mode: "prod_container_with_updater", action_required: "none", can_apply: false }
        : { has_update: true, can_check: true, current_version: APP_VERSION, target_version: "0.72.0-beta.2",
            current_commit: "aaaa1111aaaa", remote_commit: "bbbb2222bbbb", ahead: 2, remote: "origin", branch: "main",
            changed_files: ["backend/app/main.py"], commit_titles: ["修复更新后停在 100%"],
            runtime_mode: "prod_container_with_updater", action_required: "backend", plan_label: "后端增量更新",
            plan_detail: "拉取镜像并重启后端。", components: ["backend"], services: ["web"], can_apply: true };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.route("**/api/system/pull-update", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ success: true, job_id: "job-r21", status: "queued", remote: "origin", branch: "main" }) });
    });
    await page.route("**/api/system/update-jobs/job-r21", async (route) => {
      jobReads += 1;
      const done = jobReads >= 2;
      if (done) updated = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(done
          ? { ok: true, job_id: "job-r21", status: "succeeded", progress: 100, phase: "更新完成", summary: "更新完成。", new_commit: "bbbb2222bbbb", logs: ["完成"] }
          : { ok: true, job_id: "job-r21", status: "running", progress: 60, phase: "拉取镜像", logs: ["拉取镜像"] }),
      });
    });

    await page.goto("/plugins", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "检查更新" }).click();
    const dialog = page.getByRole("dialog", { name: "检查更新" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "拉取镜像并重启后端" }).click();
    await expect(dialog.getByTestId("update-recovering")).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByRole("progressbar")).toHaveCount(0);
    await expect(dialog.getByTestId("update-recovered")).toContainText("0.72.0-beta.2", { timeout: 20_000 });
    await expect(dialog.getByTestId("update-up-to-date")).toContainText("已是最新");
    await expect(dialog.getByTestId("update-up-to-date")).toContainText("0.72.0-beta.2");
    await expect(dialog.getByRole("button", { name: "刷新页面" })).toBeVisible();
    fixture.assertClean();
  });

  test("运行树旧深链跳到插件页运行树", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "只验证跳转，跑桌面");
    await installApiFixture(page);
    for (const legacy of ["/settings?tab=tree", "/settings?tab=platform-tree", "/platform-tree", "/runtime"]) {
      await page.goto(legacy, { waitUntil: "domcontentloaded" });
      await expect(page).toHaveURL(/\/plugins\?view=tree$/);
    }
    await expect(page.getByRole("tab", { name: "运行树" })).toHaveAttribute("aria-selected", "true");
    await page.goto("/settings?tab=platform", { waitUntil: "domcontentloaded" });
    await expect(page).toHaveURL(/\/settings\?tab=platform$/);
  });
});
