import assert from "node:assert/strict";
import test from "node:test";

import { RECOVERY_HEALTHY_STREAK, recoveryVerdict, sameCommit } from "./updateRecovery.ts";

test("知道目标提交时必须对上", () => {
  const baseline = { version: "0.114.0-beta.1", revision: "56246fd0a1b2", expectedCommit: "9a8b7c6d5e4f" };
  assert.equal(recoveryVerdict(baseline, { version: "0.114.0-beta.1", revision: "56246fd0a1b2c3d4" }, 5), "still_old");
  assert.equal(recoveryVerdict(baseline, { version: "0.115.0-beta.1", revision: "9a8b7c6d5e4f3a2b" }, 1), "recovered");
});

test("没有提交时版本号变化即恢复，否则连续正常若干次", () => {
  const baseline = { version: "0.114.0-beta.1" };
  assert.equal(recoveryVerdict(baseline, { version: "0.115.0-beta.1" }, 1), "recovered");
  assert.equal(recoveryVerdict(baseline, { version: "0.114.0-beta.1" }, 1), "waiting");
  assert.equal(recoveryVerdict(baseline, { version: "0.114.0-beta.1" }, RECOVERY_HEALTHY_STREAK), "recovered");
});

test("没有目标提交时提交号变化即恢复", () => {
  const baseline = { version: "0.114.0", revision: "aaaaaaa1", expectedCommit: " " };
  assert.equal(recoveryVerdict(baseline, { version: "0.114.0", revision: "bbbbbbb2" }, 1), "recovered");
  assert.equal(recoveryVerdict(baseline, { version: "0.114.0", revision: "aaaaaaa1ffff" }, 1), "waiting");
});

test("短提交号不比对", () => {
  assert.equal(sameCommit("abc", "abcdef0"), false);
  assert.equal(sameCommit("ABCDEF0123", "abcdef0"), true);
  assert.equal(sameCommit(null, "abcdef0"), false);
});
