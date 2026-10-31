// 第 21 轮：服务器更新到 100% 后自动「等服务器恢复」的判断。
//
// 纯函数（不依赖别名导入与 React），便于 node --test 直接测试；规则与 iOS
// `ios/TelePilotCore/Sources/TelePilotCore/ServerUpdateRecovery.swift` 一致：
//  - 知道目标提交且服务器报告了提交：两者前缀一致才算恢复；
//  - 否则版本号或提交与更新前不同即算恢复；
//  - 都判断不了（只是重启、或服务器不报告提交）：连续几次读到正常响应算恢复。

export const RECOVERY_POLL_INTERVAL_MS = 3_000;
export const RECOVERY_TIMEOUT_MS = 180_000;
export const RECOVERY_HEALTHY_STREAK = 3;

export interface RecoveryBaseline {
  version?: string | null;
  revision?: string | null;
  expectedCommit?: string | null;
}

export type RecoveryVerdict = "recovered" | "still_old" | "waiting";

function clean(value: string | null | undefined): string | null {
  const text = (value ?? "").trim();
  return text ? text : null;
}

/** 两个提交号是否指同一个提交（长短不同的缩写按前缀比较，至少 7 位）。 */
export function sameCommit(lhs: string | null | undefined, rhs: string | null | undefined): boolean {
  const a = clean(lhs)?.toLowerCase();
  const b = clean(rhs)?.toLowerCase();
  if (!a || !b || Math.min(a.length, b.length) < 7) return false;
  return a.startsWith(b) || b.startsWith(a);
}

/** 读到一次正常的 `/api/system/version` 后的判断；healthyCount 含这一次在内的连续正常次数。 */
export function recoveryVerdict(
  baseline: RecoveryBaseline,
  observed: { version?: string | null; revision?: string | null },
  healthyCount: number,
): RecoveryVerdict {
  const revision = clean(observed.revision);
  const version = clean(observed.version);
  const expected = clean(baseline.expectedCommit);
  if (expected && revision) return sameCommit(expected, revision) ? "recovered" : "still_old";
  const oldRevision = clean(baseline.revision);
  if (oldRevision && revision && !sameCommit(oldRevision, revision)) return "recovered";
  const oldVersion = clean(baseline.version);
  if (oldVersion && version && oldVersion !== version) return "recovered";
  return healthyCount >= RECOVERY_HEALTHY_STREAK ? "recovered" : "waiting";
}
