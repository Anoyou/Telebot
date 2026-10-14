// 账号域类型：从 OpenAPI 生成类型 `schema.ts` 引出（WP-B12 迁移启动）。
//
// 迁移口径：
// - 新代码一律从本模块（或 `schema.ts`）引类型，不要往 `types.ts` 加新的账号域手写类型。
// - `types.ts` 继续 re-export 这里的名字作为兼容层，老代码无需改动。
// - 状态字面量（`AccountStatus` 等）后端 schema 里是宽松 `string`，这里保留原有窄联合，
//   避免一次迁移引发大范围 TS 收窄错误；等域名真正全量迁移后再收紧。
import type { components as ApiComponents } from "@/api/schema";

type Schemas = ApiComponents["schemas"];

/** 已知状态保留窄联合；后端如新增状态，`string` 兜底不会让前端编译失败。 */
export type AccountStatus =
  | "active"
  | "paused"
  | "floodwait"
  | "dead"
  | "login_required"
  | (string & {});

export interface ProxySummary {
  id: number;
  /** socks5 / http / https；历史未知值按原字符串展示。 */
  type: string;
  host: string;
  port: number;
  /** 友好名字；后端目前给的是 host:port */
  label?: string | null;
  // ── 上次主动测的真实出口（30 min Redis 缓存）──
  /** ISO-2 国家代码（CN / US / JP），无缓存时 null */
  exit_country?: string | null;
  exit_ip?: string | null;
  /** epoch 秒；前端用来算"几分钟前测的" */
  probed_at?: number | null;
  /** 上次探测是否成功；null=从未探测 */
  probe_ok?: boolean | null;
}

export type AccountSummary = Schemas["AccountSummary"] & {
  status: AccountStatus;
  proxy?: ProxySummary | null;
};

export type AccountDetail = Schemas["AccountDetail"] & {
  status: AccountStatus;
  proxy?: ProxySummary | null;
};

export type AccountStartLoginRequest = Schemas["AccountStartLoginRequest"];
export type AccountStartLoginResponse = Schemas["AccountStartLoginResponse"];
export type AccountConfirmCodeRequest = Schemas["AccountConfirmCodeRequest"];
export type AccountConfirm2FARequest = Schemas["AccountConfirm2FARequest"];
export type AccountConfirmResponse = Schemas["AccountConfirmResponse"];
export type AccountUpdateRequest = Schemas["AccountUpdateRequest"];
export type ConfigBundleDryRunResponse = Schemas["ConfigBundleDryRunResponse"];
export type ConfigBundleConfirmResponse = Schemas["ConfigBundleConfirmResponse"];

export interface AccountFeatureItem {
  feature_key: string;
  enabled: boolean;
  state: string;
  config: Record<string, unknown>;
  last_error?: string | null;
}
