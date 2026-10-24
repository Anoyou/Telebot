import { expect, test, type Page } from "@playwright/test";

import { installApiFixture } from "./fixtures";

const session = {
  id: "send-recovery-session", web_user_id: 1, account_id: null, channel: "web",
  title: "发送与恢复回归", origin: "interactive", status: "active",
  created_at: "2026-10-21T00:00:00Z", updated_at: "2026-10-21T00:00:00Z",
};
const run = {
  id: "recovery-run", run_id: "recovery-run", session_id: session.id,
  web_user_id: 1, user_message_id: 1, client_request_id: "recovery-request",
  kind: "message", status: "running", phase: "运行中", last_seq: 0,
  cancel_requested: false, created_at: "2026-10-21T00:00:00Z", updated_at: "2026-10-21T00:00:00Z",
};
const composer = (page: Page) => page.locator("[data-assistant-composer]");
const input = (page: Page) => composer(page).getByRole("textbox");

async function installScenario(page: Page, options: { enabled?: boolean; saved?: boolean } = {}) {
  const fixture = await installApiFixture(page);
  const state = { posts: [] as Record<string, unknown>[], runs: [] as typeof run[], streams: 0, snapshots: 0 };
  await page.route("**/api/system-agent/config", (route) => route.fulfill({ json: {
    enabled: options.enabled ?? true, provider_id: 1, model: "fixture-model", fallback_provider_ids: [],
    require_tool_approval: false, max_steps: 8, max_tool_calls: 24, session_token_limit: 16_384,
  } }));
  await page.route("**/api/system-agent/sessions?**", (route) => route.fulfill({ json: [session] }));
  await page.route(`**/api/system-agent/sessions/${session.id}/messages?**`, (route) => route.fulfill({ json: [] }));
  await page.route("**/api/system-agent/runs?**", (route) => route.fulfill({ json: state.runs }));
  await page.route(`**/api/system-agent/runs/${run.id}`, (route) => {
    state.snapshots += 1;
    return route.fulfill({ json: state.runs[0] ?? { ...run, status: "succeeded", last_seq: 2 } });
  });
  await page.route(`**/api/system-agent/runs/${run.id}/events?**`, (route) => route.fulfill({ json: [] }));
  if (options.saved) {
    await page.addInitScript(({ sessionId, runId }) => {
      localStorage.setItem("telepilot.system-agent.active-runs.v1", JSON.stringify({ [sessionId]: { runId, lastSeq: 2 } }));
    }, { sessionId: session.id, runId: run.id });
  }
  return { fixture, state };
}

test("服务器未受理时保留文字与图片，不把失败当作发送成功", async ({ page }) => {
  const { fixture, state } = await installScenario(page);
  await page.route(`**/api/system-agent/sessions/${session.id}/runs`, async (route) => {
    state.posts.push(route.request().postDataJSON());
    await route.fulfill({ status: 503, json: { detail: { message: "测试：服务器暂未受理" } } });
  });
  await page.goto("/assistant");
  await input(page).fill("失败时这条消息必须留在输入框");
  await composer(page).locator('input[type="file"]').setInputFiles({
    name: "retained.png", mimeType: "image/png",
    buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64"),
  });
  await expect(composer(page).getByRole("img", { name: "retained.png" })).toBeVisible();
  await composer(page).getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("测试：服务器暂未受理", { exact: true })).toBeVisible();
  await expect(input(page)).toHaveValue("失败时这条消息必须留在输入框");
  await expect(composer(page).getByRole("img", { name: "retained.png" })).toBeVisible();
  expect(state.posts).toHaveLength(1);
  fixture.assertClean();
});

test("助手未启用时保留草稿且不发起任务", async ({ page }) => {
  const { fixture, state } = await installScenario(page, { enabled: false });
  await page.goto("/assistant");
  await input(page).fill("未开启也不能清空");
  await composer(page).getByRole("button", { name: "发送", exact: true }).click();
  await expect(input(page)).toHaveValue("未开启也不能清空");
  expect(state.posts).toHaveLength(0);
  fixture.assertClean();
});

test("旧会话提交迟到成功不得清空新会话草稿", async ({ page }) => {
  const { fixture } = await installScenario(page);
  const otherSession = { ...session, id: "other-session", title: "迟到响应的新会话" };
  await page.route("**/api/system-agent/sessions?**", (route) => route.fulfill({ json: [session, otherSession] }));
  await page.route(`**/api/system-agent/sessions/${otherSession.id}/messages?**`, (route) => route.fulfill({ json: [] }));
  let releaseResponse!: () => void;
  let markArrived!: () => void;
  const responseGate = new Promise<void>((resolve) => { releaseResponse = resolve; });
  const arrived = new Promise<void>((resolve) => { markArrived = resolve; });
  await page.route(`**/api/system-agent/sessions/${session.id}/runs`, async (route) => {
    markArrived();
    await responseGate;
    await route.fulfill({ json: { ...run, status: "succeeded" } });
  });
  await page.goto(`/assistant?session=${session.id}`);
  await input(page).fill("旧会话已提交");
  await composer(page).locator('button[type="submit"]').click();
  await arrived;
  const otherButton = page.getByText(otherSession.title, { exact: true }).filter({ visible: true });
  if (!await otherButton.isVisible()) await page.getByRole("button", { name: "打开会话列表" }).click();
  await otherButton.click();
  await input(page).fill("新会话尚未发送的草稿");
  releaseResponse();
  await page.waitForTimeout(500);
  await expect(input(page)).toHaveValue("新会话尚未发送的草稿");
  const drafts = await page.evaluate(() => JSON.parse(localStorage.getItem("telepilot.system-agent.drafts.v1") || "{}"));
  expect(Object.values(drafts)).toContain("新会话尚未发送的草稿");
  fixture.assertClean();
});

test("恢复终态旧任务后连续发送，不能静默清空或永久保留停止态", async ({ page }) => {
  const { fixture, state } = await installScenario(page, { saved: true });
  await page.route(`**/api/system-agent/runs/${run.id}/stream?**`, async (route) => {
    state.streams += 1;
    await route.fulfill({ contentType: "application/x-ndjson", body: "" });
  });
  await page.route(`**/api/system-agent/sessions/${session.id}/runs`, async (route) => {
    state.posts.push(route.request().postDataJSON());
    await route.fulfill({ json: { ...run, status: "succeeded", last_seq: 2 } });
  });
  await page.goto("/assistant");
  await input(page).fill("旧任务已经完成，提交新消息");
  await composer(page).locator('button[type="submit"]').click();
  await expect.poll(() => state.posts.length).toBe(1);
  await expect(input(page)).toHaveValue("");
  await expect(composer(page).getByRole("button", { name: "停止", exact: true })).toHaveCount(0);
  await input(page).fill("下一条仍然能发送");
  await composer(page).locator('button[type="submit"]').click();
  await expect.poll(() => state.posts.length).toBe(2);
  await expect(input(page)).toHaveValue("");
  fixture.assertClean();
});

// 用浏览器原生 ReadableStream 模拟代理不关闭连接；其它 API 仍走隔离路由。
async function holdProgressStream(page: Page, withDone = false, initialDelta = "") {
  await page.addInitScript(({ withDone, initialDelta }) => {
    const original = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      if (String(input).includes("/api/system-agent/runs/") && String(input).includes("/stream?")) {
        window.sessionStorage.setItem("test-stream-opened", "true");
        const count = Number(window.sessionStorage.getItem("test-stream-count") || 0);
        window.sessionStorage.setItem("test-stream-count", String(count + 1));
        const afterSeq = Number(new URL(String(input), window.location.origin).searchParams.get("after_seq") || 0);
        return new Response(new ReadableStream({
          start(controller) {
            if (initialDelta && afterSeq < 1) controller.enqueue(new TextEncoder().encode(
              JSON.stringify({ type: "assistant_delta", seq: 1, delta: initialDelta }) + "\n",
            ));
            if (withDone) controller.enqueue(new TextEncoder().encode('{"type":"done","seq":2,"ok":true}\n'));
            init?.signal?.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")), { once: true });
          },
        }), { headers: { "Content-Type": "application/x-ndjson" } });
      }
      return original(input, init);
    };
  }, { withDone, initialDelta });
}

test("提交受理就释放输入框，不等长任务执行完才允许继续排队", async ({ page }) => {
  const { fixture, state } = await installScenario(page);
  await holdProgressStream(page);
  await page.route(`**/api/system-agent/sessions/${session.id}/runs`, (route) => {
    state.posts.push(route.request().postDataJSON());
    state.runs = [run];
    return route.fulfill({ json: run });
  });
  await page.goto("/assistant");
  await input(page).fill("第一条已受理但还在执行");
  await composer(page).locator('button[type="submit"]').click();
  await expect(input(page)).toBeEnabled();
  await expect(input(page)).toHaveValue("");
  await input(page).fill("第二条进入队列");
  await composer(page).locator('button[type="submit"]').click();
  await expect.poll(() => state.posts.length).toBe(2);
  await expect(input(page)).toHaveValue("");
  fixture.assertClean();
});

test("收到 done 后立即结算，即使代理没有关闭响应连接", async ({ page }) => {
  const { fixture, state } = await installScenario(page, { saved: true });
  await holdProgressStream(page, true);
  await page.goto("/assistant");
  await expect.poll(() => state.snapshots).toBeGreaterThan(0);
  await expect(composer(page).getByRole("button", { name: "停止", exact: true })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => localStorage.getItem("telepilot.system-agent.active-runs.v1"))).toBe("{}");
  expect(state.posts).toHaveLength(0);
  fixture.assertClean();
});

test("进度流静默超时只核对旧任务，不重发消息或调用新任务", async ({ page }) => {
  const { fixture, state } = await installScenario(page, { saved: true });
  await holdProgressStream(page);
  await page.clock.install();
  await page.goto("/assistant");
  await expect(composer(page).getByRole("button", { name: "停止", exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("test-stream-opened"))).toBe("true");
  await page.clock.fastForward(90_001);
  await expect.poll(() => state.snapshots).toBeGreaterThan(0);
  await expect(composer(page).getByRole("button", { name: "停止", exact: true })).toHaveCount(0);
  expect(state.posts).toHaveLength(0);
  fixture.assertClean();
});

test("排队转运行时保留已接收的回复，不重复订阅或跳过文本", async ({ page }) => {
  const { fixture, state } = await installScenario(page);
  const delta = "排队期间已收到的回复不能消失";
  state.runs = [{ ...run, status: "queued" }];
  await holdProgressStream(page, false, delta);
  await page.clock.install();
  await page.goto("/assistant");
  await expect(page.getByText(delta, { exact: true })).toBeVisible();

  state.runs = [{ ...run, status: "running", last_seq: 1 }];
  const runningPoll = page.waitForResponse(async (response) => {
    if (new URL(response.url()).pathname !== "/api/system-agent/runs") return false;
    const rows = await response.json();
    return rows.some((row: typeof run) => row.id === run.id && row.status === "running");
  });
  await page.clock.fastForward(3_001);
  await runningPoll;
  await page.clock.runFor(100);

  await expect(page.getByText(delta, { exact: true })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("test-stream-count"))).toBe("1");
  expect(state.posts).toHaveLength(0);
  fixture.assertClean();
});

test("轮询确认旧任务完成时清理订阅状态，下一条可以正常发送", async ({ page }) => {
  const { fixture, state } = await installScenario(page);
  state.runs = [{ ...run, status: "queued" }];
  await holdProgressStream(page);
  await page.clock.install();
  await page.route(`**/api/system-agent/sessions/${session.id}/runs`, (route) => {
    state.posts.push(route.request().postDataJSON());
    return route.fulfill({ json: run });
  });
  await page.goto("/assistant");
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("test-stream-opened"))).toBe("true");
  state.runs = [{ ...run, status: "succeeded", last_seq: 2 }];
  await page.clock.fastForward(3_001);
  await expect(composer(page).getByRole("button", { name: "停止", exact: true })).toHaveCount(0);
  await input(page).fill("轮询完成后继续对话");
  await composer(page).locator('button[type="submit"]').click();
  await expect.poll(() => state.posts.length).toBe(1);
  await expect(input(page)).toHaveValue("");
  fixture.assertClean();
});
