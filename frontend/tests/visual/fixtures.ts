import type { Page, Route } from "@playwright/test";
import { APP_VERSION } from "../../src/lib/version";
import pluginW1Data from "./plugin-w1-features.json" with { type: "json" };

const emptyResourceDashboard = {
  host: {
    cpu_percent: 0,
    memory_used_percent: 0,
    memory_total_mb: 1024,
    disk_used_percent: 0,
    disk_free_gb: 100,
    sampled_at: 1_784_635_200,
    uptime_seconds: 0,
  },
  main_process: { pid: 1, cpu_percent: 0, rss_mb: 0, uss_mb: 0 },
  project_total: { cpu_percent: 0, rss_mb: 0, uss_mb: 0 },
  app_uptime_seconds: 0,
  other_processes: [],
  containers: [],
  container_total: { cpu_percent: 0, rss_mb: 0, uss_mb: 0 },
  container_probe_error: null,
  container_source: null,
  project_total_basis: "processes",
  workers: [],
  worker_alive: 0,
  worker_desired_running: 0,
  logs: { last_5m_total: 0, last_5m_error: 0, last_5m_warn: 0 },
};

const emptyLedgerSummary = {
  income: "0", payout: "0", net: "0", count: 0,
  by_day: [], by_chat: [], by_recipient: [],
};

const emptyLedgerStats = {
  total: {
    started_sessions: 0, participant_count: 0, payout_success_count: 0,
    payout_failure_count: 0, payout_attempt_count: 0, payout_success_rate: null,
    ledger_income: "0", ledger_payout: "0", ledger_net: "0", ledger_count: 0,
  },
  by_day: [], by_chat: [],
  source_matrix: [],
};

const platformCapabilitiesFixture = {
  modules: [],
  channels: [],
  worker_convergence: {
    notified: 0,
    acked: 0,
    pending: 0,
    offline_or_timeout: 0,
    notes: [],
  },
  cache_ready: true,
  updated_at: "2026-07-21T00:00:00Z",
};

// 插件运行统计（0.107.0+，插件详情「运行统计」）：固定一条正常事件钩子 + 一条有失败的配置动作 + 一条弃用登记。
export const pluginHookStatsFixture = (pluginKey: string) => ({
  plugin_key: pluginKey,
  account_id: null,
  available: true,
  message: null,
  retention_days: 7,
  hooks: [
    {
      account_id: 1, hook: "on_event", event_type: "message",
      calls: 120, ok: 118, failed: 1, timeout: 1, circuit_open: 0, skipped: 0,
      sum_ms: 9600, avg_ms: 80, p50_ms: 50, p95_ms: 250, max_ms: 1800, failure_rate: 0.0167,
      last_at: 1_784_635_200, last_error: "TimeoutError: PLUGIN_INVOKE_TIMEOUT: demo_plugin", last_error_at: 1_784_635_100, since: 1_784_000_000,
    },
    {
      account_id: 0, hook: "on_config_action", event_type: "sync",
      calls: 2, ok: 1, failed: 1, timeout: 0, circuit_open: 0, skipped: 0,
      sum_ms: 900, avg_ms: 450, p50_ms: 500, p95_ms: 500, max_ms: 600, failure_rate: 0.5,
      last_at: 1_784_635_000, last_error: "ValueError: 缺少 API Key", last_error_at: 1_784_635_000, since: 1_784_600_000,
    },
  ],
  totals: { calls: 122, ok: 119, fail: 2, timeout: 1, circuit_open: 0, skipped: 0, sum_ms: 10500 },
  deprecations: [
    {
      account_id: 1, id: "ai.tag_alias", count: 3, last_at: 1_784_635_200, detail: null,
      summary: "ctx.ai.complete / stream_complete 使用 tag= / tags= 兼容别名", replacement: "provider_tag=", removal: "Plugin API 2.0",
    },
  ],
});

// 插件原生卡片（Plugin API 1.3）：插件详情顶部的宿主「运行统计」卡，结构与后端 plugin_ui_cards._host_stats_card 一致。
export const hostStatsCardFixture = (pluginKey: string) => ({
  plugin_key: pluginKey,
  plugin_name: "示例插件",
  card_id: "_host.stats",
  slot: "plugin.overview",
  title: "运行统计",
  subtitle: "按钩子统计调用数、耗时与失败数，保留到最近一次调用后 7 天",
  source_kind: "read",
  refresh_seconds: null,
  host: true,
  blocks: [
    { type: "stats", items: [
      { label: "调用", path: "summary.calls", format: "number", tone_path: "summary.calls_tone" },
      { label: "失败", path: "summary.failed", format: "number", tone_path: "summary.failed_tone" },
      { label: "超时", path: "summary.timeout", format: "number", tone_path: "summary.timeout_tone" },
      { label: "平均耗时", path: "summary.avg_ms", format: "duration_ms" },
    ] },
    { type: "list", id: "deprecations", title: "使用了已弃用接口", items_path: "deprecations", hide_when_empty: true },
    { type: "table", id: "hooks", rows_path: "hooks", row_key: "key", empty: "还没有调用记录。", columns: [
      { key: "hook", label: "钩子" }, { key: "event_type", label: "事件" }, { key: "account", label: "账号" },
      { key: "health", label: "状态", tone_key: "health_tone" }, { key: "calls", label: "调用", format: "number" },
      { key: "failure_rate", label: "失败率", format: "percent" }, { key: "avg_ms", label: "平均", format: "duration_ms" },
      { key: "p95_ms", label: "P95", format: "duration_ms" }, { key: "last_at", label: "最近", format: "relative_time" },
      { key: "last_error", label: "最近错误" },
    ] },
    { type: "buttons", items: [{ id: "reset_stats", label: "清零", style: "destructive", confirm: "清零这个插件的运行统计和弃用计数？" }] },
  ],
});

export const hostStatsCardDataFixture = (pluginKey: string) => ({
  plugin_key: pluginKey,
  card_id: "_host.stats",
  status: "ok",
  message: null,
  updated_at: 1_784_635_200,
  duration_ms: 3,
  data: {
    summary: { calls: 122, failed: 2, timeout: 1, avg_ms: 85, calls_tone: "accent", failed_tone: "danger", timeout_tone: "warning" },
    deprecations: [
      { title: "ai.tag_alias", subtitle: "ctx.ai.complete / stream_complete 使用 tag= / tags= 兼容别名；请改用 provider_tag=；计划移除：Plugin API 2.0", trailing: "3 次", tone: "warning" },
    ],
    hooks: [
      { key: "on_event", hook: "事件处理", event_type: "message", account: "视觉测试账号", health: "有失败", health_tone: "warning", calls: 120, failure_rate: 0.0167, avg_ms: 80, p95_ms: 250, last_at: 1_784_635_200, last_error: "TimeoutError: PLUGIN_INVOKE_TIMEOUT: demo_plugin" },
      { key: "on_config_action", hook: "配置动作", event_type: "sync", account: "", health: "失败偏多", health_tone: "danger", calls: 2, failure_rate: 0.5, avg_ms: 450, p95_ms: 500, last_at: 1_784_635_000, last_error: "ValueError: 缺少 API Key" },
    ],
  },
});

const pluginCapabilitiesFixture = {
  host_version: APP_VERSION,
  plugin_api_version: "1.0",
  capabilities: [
    {
      name: "messages.guest_reply_markup", version: 1, since: "0.106.0-beta.17", plugin_api: "1.0",
      summary: "answer_guest_query 保留 callback_data 按钮", legacy_attr: "guest_reply_markup",
      facades: ["buffered", "entry", "live"], doc: "", deprecated_in: null, removed_in: null,
    },
  ],
  deprecations: [
    { id: "ai.tag_alias", summary: "ctx.ai.complete 使用 tag= 兼容别名", replacement: "provider_tag=", deprecated_since: "0.25.0", removal: "Plugin API 2.0", doc: "" },
  ],
};

export const accountFixture = {
  id: 1,
  phone: "+8613800000000",
  display_name: "视觉测试账号",
  tg_user_id: 10001,
  tg_username: "visual_test",
  status: "paused",
  tags: ["fixture"],
  enabled_features: 2,
  cold_start_until: null,
  created_at: "2026-07-21T00:00:00Z",
  proxy: null,
};

const ledgerEntryFixture = {
  id: 1, source: "transfer", source_id: 101, direction: "out", amount: "88.00", signed_amount: "-88.00",
  status: "posted", account_id: 1, chat_id: -10010001, chat_title: "视觉回归测试群",
  payer_user_id: 10001, payer_name: "测试付款人", receiver_user_id: 20002, receiver_name: "测试收款人",
  receiver_username: "recipient", plugin_key: "transfer", entry_key: "reward:101", channel: "telegram",
  session_key: "fixture-session", action_type: "reward", payout_key: "fixture-payout-101", error_code: null,
  created_at: "2026-07-21T08:00:00Z", params_summary: {},
};

const ledgerCompensationFixture = {
  id: 1, payout_key: "fixture-compensation-1", account_id: 1, trace_id: "fixture-trace",
  plugin_key: "transfer", entry_key: "reward:failed:1", origin: "worker", chat_id: -10010001,
  chat_title: "视觉回归测试群", receiver_user_id: 20002, receiver_name: "测试收款人", amount: "66.00",
  status: "pending", error_code_first: "UPSTREAM_TIMEOUT", error_code_last: "UPSTREAM_TIMEOUT",
  error_last: "上游响应超时", ambiguous: false, retry_count: 2, next_attempt_at: "2026-07-21T08:05:00Z",
  sent_message_id: null, sent_at: null, notified_at: null, created_at: "2026-07-21T08:00:00Z",
  updated_at: "2026-07-21T08:01:00Z",
};

export const pluginMarketItemFixture = (overrides: Record<string, unknown> = {}) => ({
  id: "official:game24",
  key: "game24",
  name: { zh: "24 点", en: "game24" },
  kind: "interactive",
  icon: "🎮",
  summary: "群内 24 点小游戏。",
  version: "1.1.18",
  author: "Anoyou",
  min_telepilot_version: "0.72.0-beta.2",
  plugin_api: null,
  permissions: ["send_message"],
  reads: [],
  tags: ["game"],
  channels: { stable: "main" },
  path: "game24",
  repository: "https://github.com/Anoyou/telebot-plugins.git",
  trust: "official",
  source_id: "official",
  source_name: "官方插件库",
  compatible: true,
  incompatible_reason: null,
  installed: null,
  update_available: false,
  pinned_version: null,
  auto_update: false,
  rollout: null,
  ...overrides,
});

export const pluginMarketCatalogFixture = ({
  items,
  updates,
  rollouts = [],
}: {
  items: Array<Record<string, unknown>>;
  updates: number;
  rollouts?: Array<Record<string, unknown>>;
}) => ({
  server_version: APP_VERSION,
  plugin_api: "1.6",
  sources: [
    {
      id: "official",
      name: "官方插件库",
      url: "https://raw.githubusercontent.com/Anoyou/telebot-plugins/main/registry.json",
      trust: "official",
      enabled: true,
      auto_update: false,
      builtin: true,
      available: true,
      status: "ok",
      plugin_count: items.length,
      fetched_at: "2026-10-30T03:00:00+00:00",
      error: null,
      fallback: false,
    },
  ],
  items,
  updates_available: updates,
  settings: { update_concurrency: 3, health_window_minutes: 10, pinned: {} },
  rollouts,
});

export const providerFixtures = [
  {
    id: 2,
    name: "Grok",
    provider: "anthropic",
    has_api_key: true,
    base_url: "https://example.invalid/v1",
    default_model: "grok-4.20-fast",
    api_format: "anthropic_messages",
    protocol_profile: "standard",
    web_search_api_format: "auto",
    client_identity_profile: "claude_code",
    modality: "text",
    tags: ["chat", "reason", "translate", "vision"],
    cost_tier: 2,
    notes: null,
    proxy_id: null,
    models: [
      { id: "grok-4.20-fast", enabled: true, custom: false, label: null },
      { id: "grok-4.20", enabled: true, custom: false, label: null },
      { id: "grok-4-fast", enabled: true, custom: false, label: null },
    ],
  },
  {
    id: 1,
    name: "猫羽",
    provider: "openai",
    has_api_key: true,
    base_url: "https://example.invalid/v1",
    default_model: "deepseek-chat",
    api_format: "responses",
    protocol_profile: "standard",
    web_search_api_format: "auto",
    client_identity_profile: "codex_cli",
    modality: "text",
    tags: ["chat"],
    cost_tier: 2,
    notes: null,
    proxy_id: null,
    models: [
      { id: "deepseek-chat", enabled: true, custom: false, label: null },
      { id: "deepseek-reasoner", enabled: true, custom: false, label: null },
      { id: "deepseek-lite", enabled: false, custom: false, label: null },
      { id: "deepseek-coder", enabled: false, custom: false, label: null },
    ],
  },
] as const;

function jsonResponse(pathname: string): unknown | undefined {
  if (pathname === "/api/auth/me") return { id: 1, username: "visual-test", has_totp: false };
  if (pathname === "/api/auth/csrf") return { csrf_token: "visual-test-csrf" };
  if (pathname === "/api/accounts") return [accountFixture];
  if (pathname === "/api/commands/llm-providers") return [];
  if (pathname === "/api/commands/llm-providers/runtime-health") return [];
  const keyPool = pathname.match(/^\/api\/commands\/llm-providers\/(\d+)\/keys$/);
  if (keyPool) return {
    provider_id: Number(keyPool[1]),
    key_strategy: "order",
    has_main_key: true,
    main_key_health: { state: "healthy", consecutive_failures: 0, cooldown_remaining_seconds: 0 },
    keys: [],
  };
  if (pathname === "/api/commands/llm-providers/identity-versions") return {
    items: [{
      key: "codex_tui",
      label: "Codex CLI",
      current: "0.162.1",
      default: "0.162.1",
      registry: "npm",
      detectable: true,
    }],
    profiles: [],
  };
  if (pathname === "/api/system/resource-dashboard") return emptyResourceDashboard;
  if (pathname === "/api/system/settings") return { timezone: "Asia/Shanghai", login_security: {} };
  if (pathname === "/api/system-agent/config") return {
    enabled: false, provider_id: null, model: null, fallback_provider_ids: [],
    require_tool_approval: false, max_steps: 8, max_tool_calls: 24,
    session_token_limit: 16_384,
  };
  if (pathname === "/api/system-agent/capabilities") return {
    enabled: false, provider_id: null, model: null, provider_name: null,
    resolved_model: null, ai_enabled: true, timezone: "Asia/Shanghai",
    tools: [], stage: 1, write_tools_available: false, model_matrix: [],
  };
  if (pathname === "/api/system-agent/checkup") return {
    kind: "system_checkup", checked_at: "2026-10-27T00:00:00+00:00", window_hours: 24,
    verdict: "warn", verdict_text: "基本可用，有需关注项：9 项正常、1 项需关注、0 项异常、0 项未能检查",
    counts: { ok: 9, warn: 1, error: 0, unknown: 0 }, unknown_sections: [],
    next_steps: [{ severity: "warn", text: "最近 24 小时有 3 条错误日志。", tool: "logs.search_errors", requires_confirmation: false, section: "errors", order: 1 }],
    sections: [
      { key: "health", title: "基础设施（数据库 / Redis / 运行时组件）", status: "ok", finding: "均正常。", facts: {}, next_steps: [], elapsed_ms: 10 },
      { key: "errors", title: "错误日志", status: "warn", finding: "最近 24 小时 3 条错误。", facts: {},
        next_steps: [{ severity: "warn", text: "最近 24 小时有 3 条错误日志。", tool: "logs.search_errors", requires_confirmation: false }], elapsed_ms: 12 },
    ],
    elapsed_ms: 812, business_changed: false, note: "只读体检，未修改任何配置、未调用上游模型。",
  };
  if (pathname === "/api/system-agent/sessions") return [];
  if (pathname === "/api/system-agent/runs") return [];
  if (pathname === "/api/system-agent/queue") return [];
  if (pathname === "/api/system-agent/actions") return [];
  if (pathname === "/api/system-agent/memory") return [];
  // Agent Skills：基线固定为“未安装任何 skill”，上限与后端默认值一致。
  if (pathname === "/api/agent-skills") return {
    items: [],
    limits: { max_files: 64, max_file_bytes: 262144, max_skill_bytes: 1048576, max_archive_bytes: 10485760, agent_load_max_chars: 20000, plugin_load_max_chars: 8000 },
  };
  if (pathname === "/api/system/capabilities") return platformCapabilitiesFixture;
  if (pathname === "/api/platform/profile") return {
    active_profile: null,
    current_profile: "production",
    status: "idle",
    last_error: null,
    operator_id: null,
    updated_at: null,
    modules: {},
    blind_spot: null,
  };
  if (pathname === "/api/platform/tree") return {
    trunk: {
      userbot: { workers: [], total: 0, alive: 0 },
      kill_switch: false,
      current_profile: "production",
    },
    branches: {},
    leaves: [],
  };
  if (pathname === "/api/feature-matrix") return { features: [] };
  // 插件中心卡片展示顺序（iOS 长按拖动排序保存到服务器）；基线为空 = 按默认顺序。
  if (pathname === "/api/feature-matrix/order") return { keys: [] };
  if (pathname === "/api/ledger") return { items: [ledgerEntryFixture] };
  if (pathname === "/api/ledger/summary") return emptyLedgerSummary;
  if (pathname === "/api/ledger/stats") return emptyLedgerStats;
  if (pathname === "/api/ledger/compensations") return { items: [ledgerCompensationFixture] };
  if (pathname === "/api/logs/messages" || pathname === "/api/logs/runtime" || pathname === "/api/logs/audit" || pathname === "/api/logs/trace/events") return [];
  if (pathname === "/api/logs/trace/events/" || pathname.startsWith("/api/logs/trace/events/")) return { events: [] };
  if (pathname === "/api/commands/templates" || pathname === "/api/commands/builtin") return [];
  if (pathname === "/api/llm/usage/recent") return [];
  if (pathname === "/api/llm/usage/plugins/summary") return { items: [] };
  if (pathname === "/api/commands/ai/enablement-summary") return { total_accounts: 0, enabled_accounts: 0, ai_templates: 0 };
  if (pathname === "/api/proxies") return [];
  if (pathname === "/api/plugin-repos" || pathname === "/api/plugin-repos/local/plugins") return [];
  if (pathname === "/api/plugins/installed-overview" || pathname === "/api/plugins/installed-packages") return [];
  if (pathname === "/api/plugins/capabilities") return pluginCapabilitiesFixture;
  const pluginStatsMatch = pathname.match(/^\/api\/plugins\/([^/]+)\/stats$/);
  if (pluginStatsMatch) return pluginHookStatsFixture(decodeURIComponent(pluginStatsMatch[1]));
  if (pathname === "/api/remote-plugins") return [];
  // 插件市场（0.113.0）：插件中心顶部“插件市场”页签的更新角标读这里；基线没有可更新插件（不显示角标）。
  if (pathname === "/api/plugin-market/catalog") return pluginMarketCatalogFixture({ items: [], updates: 0 });
  if (pathname === "/api/system/network") return { online: true };
  if (pathname === "/api/system/version") return { version: APP_VERSION };
  // 自动检查更新状态：基线固定为"尚未执行过自动检查"，避免顶栏角标让截图产生随机差异。
  if (pathname === "/api/system/update-status") return { enabled: true, interval_minutes: 1440, last_check: null };
  if (pathname === "/api/system/update-target-options") return { ok: true, remotes: ["origin"], branches: ["main"], remote: "origin" };
  if (pathname === "/api/system/check-update") return {
    has_update: true, can_check: true,
    current_version: APP_VERSION, target_version: "0.72.0-beta.2",
    current_commit: "aaaa1111aaaa", remote_commit: "bbbb2222bbbb", ahead: 2,
    remote: "origin", branch: "main", changed_files: ["frontend/src/App.tsx"],
    commit_titles: ["优化更新弹窗的信息层级", "修复系统控制台噪声过滤"],
    runtime_mode: "local_source", action_required: "frontend", plan_label: "仅需更新前端",
    plan_detail: "更新前端资源后重启服务。", components: ["frontend"], services: ["frontend"], can_apply: true,
  };
  if (pathname === "/api/system/kill-switch") return { enabled: false };
  // 第 20 轮信息架构：服务器设置（Sudo 列表）与 Webhook 页标题核对用。
  if (pathname === "/api/sudo") return [];
  if (pathname === "/api/webhooks/1") return { account_id: 1, token: "fixture-webhook-token", token_header: "X-TelePilot-Token", token_storage: "encrypted", hooks: [], max_body_bytes: 65536, rate_limit: { action: "reject", per_minute: 60 } };
  if (pathname === "/api/system/health-overview") return {
    db: { ok: true }, redis: { ok: true }, alembic: { ok: true, pending: [] },
    providers: { total: 0, with_api_key: 0 }, proxies: { total: 0 },
    workers: { total: 0, by_status: {}, runtime_failing: 0, runtime_desired_running: 0, runtime_desired_running_alive: 0 },
    codex_gateway: { state: "not_required", required: false, provider_count: 0, revision: 0 },
  };
  if (pathname === "/api/notify-bots") return [];
  if (pathname === "/api/accounts/1") return { ...accountFixture, notes: null, template_id: null, proxy_id: null, device_profile_id: null };
  if (pathname === "/api/accounts/1/features") return [];
  if (pathname === "/api/accounts/1/commands") return [];
  if (pathname === "/api/accounts/1/rate-limit") return { template_id: null, rules: [] };
  if (pathname === "/api/accounts/1/bot") return { account_id: 1, enabled: false, status: "stopped", has_token: false, remote_plugin_policy: { enabled: false, install: false, update: false, uninstall: false, enable_disable: false } };
  if (pathname === "/api/accounts/1/bot/users" || pathname === "/api/accounts/1/ignored-peers") return [];
  if (pathname === "/api/accounts/1/interaction-bot") return { enabled: false, trigger_text: "", response_template: "" };
  if (pathname === "/api/device-profiles") return [];
  return undefined;
}

// 第 20 轮：已保存密钥按需查看（GET /api/secrets/reveal）。每个 kind 一个固定明文，
// 真实服务器每次读取都会写审计并带 no-store。
// 第 20 轮 Web IA 第二批：Agent 待确认角标与「常用入口」偏好。
export const pendingActionsFixture = [1, 2].map((index) => ({
  id: `act-${index}`,
  session_id: "s-1",
  account_id: 1,
  channel: "web",
  tool_name: "update_plugin_config",
  summary: `示例待确认操作 ${index}`,
  preview: {},
  risk: index === 1 ? "dangerous" : "normal",
  status: "pending",
  expires_at: "2026-10-31T12:00:00+08:00",
  created_at: "2026-10-31T10:00:00+08:00",
  session_title: "示例会话",
}));

export const settingsWithPrimaryNav = (primaryNav: string[]) => ({
  timezone: "Asia/Shanghai",
  login_security: {},
  ui_preferences: { sidebar_order: [], mobile_nav_order: [], primary_nav: primaryNav, provider_order: [] },
});

export const SECRET_REVEAL_FIXTURE: Record<string, string> = {
  interaction_bot_token: "7000000001:AAFixtureInteractionToken",
  transfer_bot_token: "7000000004:AAFixtureTransferToken",
  management_bot_token: "7000000002:AAFixtureManagementToken",
  notify_bot_token: "7000000003:AAFixtureNotifyToken",
  llm_provider_api_key: "sk-fixture-provider-key",
  llm_provider_key: "sk-fixture-pool-key",
  proxy_password: "fixture-proxy-password",
  webhook_token: "fixture-webhook-token",
  plugin_repo_credential: "ghp_fixtureRepoCredential",
  plugin_config_secret: "fixture-plugin-secret",
};

export function secretRevealFixture(params: URLSearchParams): Record<string, unknown> {
  const kind = params.get("kind") || "";
  const value = SECRET_REVEAL_FIXTURE[kind];
  return { kind, label: kind, configured: value !== undefined, value: value ?? null };
}

export async function installApiFixture(page: Page): Promise<{ assertClean: () => void }> {
  const unexpected: string[] = [];
  await page.route("**/api/**", async (route: Route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (!pathname.startsWith("/api/")) {
      await route.continue();
      return;
    }
    if (request.method() === "GET" && pathname === "/api/plugin-ui/cards") {
      const params = new URL(request.url()).searchParams;
      const slot = params.get("slot") || "";
      const pluginKey = params.get("plugin_key");
      const cards = slot === "plugin.overview" && pluginKey ? [hostStatsCardFixture(pluginKey)] : [];
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ slot, cards, warnings: [] }) });
      return;
    }
    if (request.method() === "GET" && pathname === "/api/secrets/reveal") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: JSON.stringify(secretRevealFixture(new URL(request.url()).searchParams)),
      });
      return;
    }
    if (request.method() === "GET" && pathname === "/api/plugin-ui/entries") {
      // 插件页面与菜单（Plugin API 1.4）：基线里的插件都没有声明，返回空列表（不显示入口）。
      const menu = new URL(request.url()).searchParams.get("menu") || "";
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ menu, pages: [], actions: [], warnings: [] }) });
      return;
    }
    const cardDataMatch = pathname.match(/^\/api\/plugin-ui\/cards\/([^/]+)\/_host\.stats\/data$/);
    if (request.method() === "POST" && cardDataMatch) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(hostStatsCardDataFixture(decodeURIComponent(cardDataMatch[1]))) });
      return;
    }
    if (request.method() === "POST" && pathname === "/api/system/check-update") {
      const response = jsonResponse(pathname);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) });
      return;
    }
    if (request.method() !== "GET") {
      unexpected.push(`${request.method()} ${pathname}`);
      await route.abort("blockedbyclient");
      return;
    }
    if (pathname.endsWith("/avatar")) {
      await route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64") });
      return;
    }
    const response = jsonResponse(pathname);
    if (response === undefined) {
      unexpected.push(`GET ${pathname}`);
      await route.abort("blockedbyclient");
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) });
  });
  return {
    assertClean: () => {
      if (unexpected.length) throw new Error(`视觉基线触发了未声明 API 请求: ${unexpected.join(", ")}`);
    },
  };
}

export async function installProviderFixture(page: Page): Promise<void> {
  await page.route("**/api/commands/llm-providers", async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(providerFixtures) });
  });
}

/** Cloudflare Access 团队登录域名（测试用，不发真实请求）。 */
export const ACCESS_LOGIN_ORIGIN = "https://visual-team.cloudflareaccess.com";

/**
 * 模拟 Cloudflare Access 会话过期：匹配的同源 API 被 302 到团队登录域名，登录页不带 CORS 头，
 * 浏览器里 XHR / fetch 因跨域失败。需在 installApiFixture 之后调用（后注册的路由优先）。
 */
export async function installAccessExpiredFixture(page: Page, paths: RegExp): Promise<void> {
  await page.route(`${ACCESS_LOGIN_ORIGIN}/**`, async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>Sign in ・ Cloudflare Access</title>" });
  });
  await page.route("**/api/**", async (route: Route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (!paths.test(pathname)) {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 302,
      headers: { location: `${ACCESS_LOGIN_ORIGIN}/cdn-cgi/access/login/visual.example?kid=visual-aud&redirect_url=%2F` },
    });
  });
}

// ─────────────────────────────────────────────────────
// W1 迁移（0.110.0-beta.2）：插件通用配置页 / 通用规则页的接口夹具。
// 五个插件的 feature 来自 plugin-w1-features.json（由插件库 plugin.json 生成）；
// legacyAutoReply=true 时 auto_reply 换成没有 config_schema 的旧版本（1.0.4），验证自动落到对照旧页。
// ─────────────────────────────────────────────────────

export interface PluginConfigFixtureWrite {
  method: string;
  path: string;
  body: unknown;
}

export interface PluginConfigFixtureOptions {
  legacyAutoReply?: boolean;
}

const w1AccountConfigs: Record<string, Record<string, unknown>> = {
  auto_reply: {},
  autorepeat: {},
  chatgpt_image: {
    command: "gptimg",
    tokens: [
      { token: "eyJh…Qx9z", token_id: "tok_1", note: "网页 session" },
      { token: "eyJk…Lm2c", token_id: "tok_2", note: "sub2api 分组 3" },
    ],
    default_model: "gpt-image-2",
    default_count: 1,
    max_count: 4,
  },
  codex_image: { command: "cximg", access_token: "***", model: "gpt-5.5", image_model: "auto", max_wait_seconds: 600 },
  game24: { command: "24d", timeout: 500 },
};

export const w1RulesFixture: Record<string, Array<Record<string, unknown>>> = {
  auto_reply: [
    {
      id: 11, account_id: 1, feature_key: "auto_reply", name: "私聊问候", enabled: true, priority: 100,
      config: { match: "keyword", patterns: ["你好", "hello"], scope: "private", reply: "你好 {sender}，稍后回复你", cooldown_seconds: "5m", cooldown_scope: "chat", reply_to: true },
      created_at: "2026-10-29T01:00:00Z", updated_at: "2026-10-29T01:00:00Z",
    },
    {
      id: 12, account_id: 1, feature_key: "auto_reply", name: "指定群签到", enabled: false, priority: 50,
      config: { match: "regex", patterns: ["^签到(\\d+)?$"], scope: "group_specific", group_ids: [-1001234567890], reply: "已签到 {1|1} 次" },
      created_at: "2026-10-29T01:00:00Z", updated_at: "2026-10-29T01:00:00Z",
    },
  ],
  autorepeat: [
    {
      id: 21, account_id: 1, feature_key: "autorepeat", name: "主群复读", enabled: true, priority: 100,
      config: { target_chat_id: -1001234567890, time_window: 300, min_users: 5 },
      created_at: "2026-10-29T01:00:00Z", updated_at: "2026-10-29T01:00:00Z",
    },
  ],
};

export function pluginW1Features(options: PluginConfigFixtureOptions = {}): Array<Record<string, unknown>> {
  const features = (pluginW1Data as { features: Array<Record<string, unknown>> }).features.map((item) => ({ ...item }));
  if (options.legacyAutoReply) {
    const index = features.findIndex((item) => item.key === "auto_reply");
    features[index] = { ...features[index], version: "1.0.4", config_schema: null };
  }
  return features;
}

export async function installPluginConfigFixture(
  page: Page,
  options: PluginConfigFixtureOptions = {},
): Promise<{ writes: PluginConfigFixtureWrite[] }> {
  const writes: PluginConfigFixtureWrite[] = [];
  const features = pluginW1Features(options);
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/**", async (route: Route) => {
    const request = route.request();
    const method = request.method();
    const pathname = new URL(request.url()).pathname;
    if (method === "GET" && pathname === "/api/feature-matrix") return json(route, { features });
    if (method === "GET" && pathname === "/api/accounts/1/features") {
      return json(route, features.map((item) => ({
        feature_key: item.key, enabled: true, state: "running", last_error: null,
        config: w1AccountConfigs[String(item.key)] ?? {},
      })));
    }
    const globalMatch = pathname.match(/^\/api\/plugins\/([^/]+)\/config$/);
    if (method === "GET" && globalMatch) return json(route, { plugin_key: decodeURIComponent(globalMatch[1]), config: {} });
    if (method === "GET" && /^\/api\/accounts\/1\/features\/[^/]+\/config\/action-jobs$/.test(pathname)) return json(route, []);
    // 旧专属页（如 legacy/ChatGPTImageConfig）读取合并后的生效配置
    const effectiveMatch = pathname.match(/^\/api\/accounts\/1\/features\/([^/]+)\/config$/);
    if (method === "GET" && effectiveMatch) return json(route, w1AccountConfigs[decodeURIComponent(effectiveMatch[1])] ?? {});
    const rulesMatch = pathname.match(/^\/api\/accounts\/1\/features\/([^/]+)\/rules$/);
    if (rulesMatch) {
      const key = decodeURIComponent(rulesMatch[1]);
      if (method === "GET") return json(route, w1RulesFixture[key] ?? []);
      if (method === "POST") {
        const body = request.postDataJSON();
        writes.push({ method, path: pathname, body });
        return json(route, { id: 99, account_id: 1, feature_key: key, created_at: "2026-10-29T02:00:00Z", updated_at: "2026-10-29T02:00:00Z", ...body }, 201);
      }
    }
    const dryRunMatch = pathname.match(/^\/api\/accounts\/1\/features\/([^/]+)\/rules\/(\d+)\/dry-run$/);
    if (method === "POST" && dryRunMatch) {
      const body = request.postDataJSON() as { sample_message?: string };
      writes.push({ method, path: pathname, body });
      const matched = String(body.sample_message || "").includes("你好");
      return json(route, {
        matched,
        output: matched ? "你好 张三，稍后回复你" : null,
        detail: {
          feature: decodeURIComponent(dryRunMatch[1]), rule_id: Number(dryRunMatch[2]), protocol: "dry_run_rule",
          logs: [
            { step: "scope", msg: "会话类型：private；规则作用范围：仅私聊" },
            { step: "match", msg: "匹配类型：关键词；模式 2 条" },
            { step: "result", msg: matched ? "命中，将回复下方内容" : "未命中：作用范围或模式不匹配" },
          ],
        },
      });
    }
    const ruleMatch = pathname.match(/^\/api\/accounts\/1\/features\/([^/]+)\/rules\/(\d+)$/);
    if (method === "PATCH" && ruleMatch) {
      const body = request.postDataJSON();
      writes.push({ method, path: pathname, body });
      return json(route, { id: Number(ruleMatch[2]), account_id: 1, feature_key: decodeURIComponent(ruleMatch[1]), ...body });
    }
    const featureMatch = pathname.match(/^\/api\/accounts\/1\/features\/([^/]+)$/);
    if (method === "PATCH" && featureMatch) {
      const body = request.postDataJSON();
      writes.push({ method, path: pathname, body });
      return json(route, { feature_key: decodeURIComponent(featureMatch[1]), enabled: true, config: (body as { config?: unknown }).config ?? {} });
    }
    return route.fallback();
  });
  return { writes };
}

// 第 20 轮：Agent 会话账号上下文（只有 1 个账号时新会话默认关联；账号页 / 插件页带账号进入）。
export type AgentAccountFixtureOptions = {
  /** 账号列表；缺省只有 accountFixture 一个。 */
  accounts?: Array<Record<string, unknown>>;
  /** 服务器是否声明 features.agent_session_default_account（旧服务器为 false）。 */
  serverDefault?: boolean;
  /** 已有会话。 */
  sessions?: Array<Record<string, unknown>>;
};

export const agentSessionFixture = (overrides: Record<string, unknown> = {}) => ({
  id: "agent-account-session", web_user_id: 1, bot_tg_user_id: null, account_id: null, channel: "web",
  title: "已有会话", origin: "interactive", status: "active", memory_summary: "", memory_state: {},
  created_at: "Wed 2026-10-21 8:00 AM CST (UTC+08:00)", updated_at: "Wed 2026-10-21 8:00 AM CST (UTC+08:00)",
  ...overrides,
});

export async function installAgentAccountFixture(page: Page, options: AgentAccountFixtureOptions = {}) {
  const accounts = options.accounts ?? [accountFixture];
  const state = {
    sessions: [...(options.sessions ?? [])] as Array<Record<string, unknown>>,
    creates: [] as Array<Record<string, unknown>>,
    patches: [] as Array<{ id: string; body: Record<string, unknown> }>,
  };
  const fulfill = (route: Route, body: unknown) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route((url) => url.pathname === "/api/accounts", (route) => fulfill(route, accounts));
  await page.route((url) => url.pathname === "/api/system/capabilities", (route) =>
    fulfill(route, {
      ...platformCapabilitiesFixture,
      features: options.serverDefault === false ? {} : { agent_session_default_account: true },
    }));
  await page.route((url) => url.pathname === "/api/system-agent/config", (route) => fulfill(route, {
    enabled: true, provider_id: 1, model: "fixture-model", fallback_provider_ids: [],
    require_tool_approval: false, max_steps: 8, max_tool_calls: 24, session_token_limit: 16_384,
  }));
  await page.route((url) => url.pathname === "/api/system-agent/sessions", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const body = (request.postDataJSON() ?? {}) as Record<string, unknown>;
      state.creates.push(body);
      // 与后端一致：未指定账号且 default_account=true 时，只有 1 个账号才自动关联；旧服务器忽略该字段。
      let accountId = (body.account_id as number | null | undefined) ?? null;
      let autoLinked = false;
      if (accountId == null && body.default_account === true && options.serverDefault !== false && accounts.length === 1) {
        accountId = accounts[0].id as number;
        autoLinked = true;
      }
      const session = agentSessionFixture({
        id: `agent-account-new-${state.creates.length}`, title: "新会话", account_id: accountId,
      });
      state.sessions.unshift(session);
      await fulfill(route, options.serverDefault === false ? session : { ...session, account_auto_linked: autoLinked });
      return;
    }
    await fulfill(route, state.sessions);
  });
  await page.route((url) => /^\/api\/system-agent\/sessions\/[^/]+$/.test(url.pathname), async (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() || "");
    const session = state.sessions.find((item) => item.id === id);
    if (route.request().method() === "PATCH" && session) {
      const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
      state.patches.push({ id, body });
      if ("account_id" in body) session.account_id = body.account_id ?? null;
    }
    if (!session) return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: { code: "SESSION_NOT_FOUND" } }) });
    await fulfill(route, session);
  });
  await page.route((url) => /^\/api\/system-agent\/sessions\/[^/]+\/messages$/.test(url.pathname), (route) => fulfill(route, []));
  return state;
}
