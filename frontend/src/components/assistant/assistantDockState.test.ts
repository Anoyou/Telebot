import assert from "node:assert/strict";
import test from "node:test";

import {
  agentAccountFromPath,
  agentAccountFromSearch,
  draftDisplayAccountId,
  newSessionAccountPayload,
  nextAssistantOutcomeSignal,
  shouldOpenAssistantDock,
  withAgentAccountSearch,
  withoutAgentAccountSearch,
} from "./assistantDockState.ts";

test("助手直达和会话深链在首次加载时展开 Dock", () => {
  assert.equal(shouldOpenAssistantDock("/assistant", ""), true);
  assert.equal(shouldOpenAssistantDock("/ai", "?session=session-1"), true);
  assert.equal(shouldOpenAssistantDock("/ai", ""), false);
});

test("助手终态事件按发生顺序递增并保留最新状态", () => {
  const completed = nextAssistantOutcomeSignal(null, "complete");
  const failed = nextAssistantOutcomeSignal(completed, "failed");

  assert.deepEqual(completed, { id: 1, status: "complete" });
  assert.deepEqual(failed, { id: 2, status: "failed" });
});

test("带账号进入 Agent 的参数会展开 Dock，无效值忽略", () => {
  assert.equal(shouldOpenAssistantDock("/accounts/3", "?agent_account=3&agent_from=account"), true);
  assert.equal(shouldOpenAssistantDock("/plugins", "?agent_account=abc"), false);
  assert.deepEqual(agentAccountFromSearch("?agent_account=7&agent_from=plugins"), { id: 7, source: "plugins" });
  assert.deepEqual(agentAccountFromSearch("?agent_account=7&agent_from=evil"), { id: 7, source: "account" });
  assert.equal(agentAccountFromSearch("?agent_account=0"), null);
  assert.equal(agentAccountFromSearch("?agent_account=-1"), null);
  assert.equal(agentAccountFromSearch(""), null);
});

test("账号详情与账号插件配置路由自带账号上下文", () => {
  assert.deepEqual(agentAccountFromPath("/accounts/12"), { id: 12, source: "account" });
  assert.deepEqual(agentAccountFromPath("/accounts/12/features/auto_reply"), { id: 12, source: "plugin_config" });
  assert.deepEqual(agentAccountFromPath("/accounts/12/features/scheduler"), { id: 12, source: "plugin_config" });
  assert.equal(agentAccountFromPath("/accounts/new"), null);
  assert.equal(agentAccountFromPath("/plugins"), null);
});

test("带账号参数保留原查询参数，去掉后不影响其它参数", () => {
  const search = withAgentAccountSearch("?tab=bot-management", { id: 5, source: "account" });
  assert.equal(search, "?tab=bot-management&agent_account=5&agent_from=account");
  assert.equal(withoutAgentAccountSearch(search), "?tab=bot-management");
  assert.equal(withoutAgentAccountSearch("?agent_account=5&agent_from=account"), "");
});

test("新会话账号：指定账号优先，显式系统级不关联", () => {
  assert.deepEqual(
    newSessionAccountPayload({ selectedAccountId: 4, explicitGlobal: false, accountIds: [1, 4], serverSupportsDefault: true }),
    { account_id: 4 },
  );
  assert.deepEqual(
    newSessionAccountPayload({ selectedAccountId: null, explicitGlobal: true, accountIds: [1], serverSupportsDefault: true }),
    { account_id: null },
  );
});

test("新会话账号：新服务器交给服务器判断，旧服务器单账号由客户端填", () => {
  assert.deepEqual(
    newSessionAccountPayload({ selectedAccountId: null, explicitGlobal: false, accountIds: [9], serverSupportsDefault: true }),
    { account_id: null, default_account: true },
  );
  assert.deepEqual(
    newSessionAccountPayload({ selectedAccountId: null, explicitGlobal: false, accountIds: [9], serverSupportsDefault: false }),
    { account_id: 9, default_account: true },
  );
  // 多账号保持系统级（旧服务器忽略 default_account）
  assert.deepEqual(
    newSessionAccountPayload({ selectedAccountId: null, explicitGlobal: false, accountIds: [1, 2], serverSupportsDefault: false }),
    { account_id: null, default_account: true },
  );
  // 账号列表还没读到
  assert.deepEqual(
    newSessionAccountPayload({ selectedAccountId: null, explicitGlobal: false, accountIds: null, serverSupportsDefault: false }),
    { account_id: null, default_account: true },
  );
});

test("草稿下拉：单账号预选，多账号或显式系统级显示系统级", () => {
  assert.equal(draftDisplayAccountId({ selectedAccountId: null, explicitGlobal: false, accountIds: [3] }), 3);
  assert.equal(draftDisplayAccountId({ selectedAccountId: null, explicitGlobal: true, accountIds: [3] }), null);
  assert.equal(draftDisplayAccountId({ selectedAccountId: null, explicitGlobal: false, accountIds: [3, 4] }), null);
  assert.equal(draftDisplayAccountId({ selectedAccountId: 4, explicitGlobal: false, accountIds: [3, 4] }), 4);
});
