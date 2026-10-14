// WP-B12 静态回归：`api/types.ts` → 域名模块迁移启动后的引用边界。
//
// 迁移口径：账号域 / 命令域类型改由 `types.accounts.ts` / `types.commands.ts` 引出
// （底层引 `schema.ts` 生成类型），`types.ts` 只保留 re-export 兼容层。
// 这条回归保证两件事：
//   1. 域名 API 包装不再回到 `types.ts` 引这些名字（否则兼容层一删就又断）；
//   2. `types.ts` 自己不再重复声明同名手写类型（否则与 re-export 冲突，tsc 直接报重复定义）。
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const ACCOUNT_DOMAIN_NAMES = [
  "AccountSummary",
  "AccountDetail",
  "AccountStatus",
  "ProxySummary",
  "AccountStartLoginRequest",
  "AccountConfirmCodeRequest",
  "AccountConfirm2FARequest",
  "AccountUpdateRequest",
  "AccountFeatureItem",
];

const COMMAND_DOMAIN_NAMES = [
  "CommandTemplateType",
  "CommandTemplateOut",
  "CommandTemplateCreate",
  "CommandTemplateUpdate",
  "AccountCommandItem",
  "AICommandEnablementSummary",
  "BuiltinCommandItem",
];

/**
 * 收集所有 `import type {...} from "module"` / `export type {...} from "module"` 段的完整文本。
 * import/re-export 段跨多行，必须按段取，不能按行 grep `from "..."`。
 * modulePath 需带引号一起传，避免 `@/api/types` 误中 `@/api/types.accounts`。
 */
function typeStatementsFrom(source: string, modulePath: string): string {
  const pattern = new RegExp(
    `(?:import|export)\\s+type\\s*\\{[^}]*\\}\\s*from\\s*"${escapeRegExp(modulePath)}"`,
    "g",
  );
  return (source.match(pattern) ?? []).join("\n");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

test("api/accounts.ts 账号域类型从 ./types.accounts 引出", () => {
  const source = readFileSync("src/api/accounts.ts", "utf8");
  const block = typeStatementsFrom(source, "@/api/types.accounts");
  assert.ok(block.length > 0, "src/api/accounts.ts 缺少 @/api/types.accounts import");
  for (const name of ACCOUNT_DOMAIN_NAMES) {
    // 包装层用到的名字必须来自域名模块；没用到的不强制（避免为凑测试加无用 import）
    const used = new RegExp(`\\b${escapeRegExp(name)}\\b`).test(
      source.replace(block, ""),
    );
    if (used) {
      assert.ok(
        new RegExp(`\\b${escapeRegExp(name)}\\b`).test(block),
        `src/api/accounts.ts 用了 ${name} 但没从 @/api/types.accounts 引`,
      );
    }
  }
});

test("api/commands.ts 命令域类型从 ./types.commands 引出", () => {
  const source = readFileSync("src/api/commands.ts", "utf8");
  const block = typeStatementsFrom(source, "@/api/types.commands");
  assert.ok(block.length > 0, "src/api/commands.ts 缺少 @/api/types.commands import");
  for (const name of COMMAND_DOMAIN_NAMES) {
    const used = new RegExp(`\\b${escapeRegExp(name)}\\b`).test(
      source.replace(block, ""),
    );
    if (used) {
      assert.ok(
        new RegExp(`\\b${escapeRegExp(name)}\\b`).test(block),
        `src/api/commands.ts 用了 ${name} 但没从 @/api/types.commands 引`,
      );
    }
  }
});

test("域名模块导出本域名全部名字", () => {
  for (const [file, names] of [
    ["src/api/types.accounts.ts", ACCOUNT_DOMAIN_NAMES],
    ["src/api/types.commands.ts", COMMAND_DOMAIN_NAMES],
  ] as const) {
    const source = readFileSync(file, "utf8");
    const exported = [
      ...typeStatementsFrom(source, "@/api/schema"),
      source,
    ].join("\n");
    for (const name of names) {
      assert.ok(
        new RegExp(`\\b${escapeRegExp(name)}\\b`).test(exported),
        `${file} 应导出 ${name}`,
      );
    }
  }
});

test("域名 API 包装不再从兼容层 @/api/types 引已迁移的名字", () => {
  for (const file of ["src/api/accounts.ts", "src/api/commands.ts"]) {
    const legacy = typeStatementsFrom(readFileSync(file, "utf8"), "@/api/types\"");
    for (const name of [...ACCOUNT_DOMAIN_NAMES, ...COMMAND_DOMAIN_NAMES]) {
      assert.ok(
        !new RegExp(`\\b${escapeRegExp(name)}\\b`).test(legacy),
        `${file} 不应再从 @/api/types 引 ${name}（兼容层回引会让迁移白做）`,
      );
    }
  }
});

test("api/types.ts 保留两个域名的 re-export 兼容层", () => {
  const source = readFileSync("src/api/types.ts", "utf8");
  const accountsReExport = typeStatementsFrom(source, "./types.accounts");
  const commandsReExport = typeStatementsFrom(source, "./types.commands");
  for (const name of ACCOUNT_DOMAIN_NAMES) {
    assert.ok(
      new RegExp(`\\b${escapeRegExp(name)}\\b`).test(accountsReExport),
      `src/api/types.ts 应 re-export ${name}`,
    );
  }
  for (const name of COMMAND_DOMAIN_NAMES) {
    assert.ok(
      new RegExp(`\\b${escapeRegExp(name)}\\b`).test(commandsReExport),
      `src/api/types.ts 应 re-export ${name}`,
    );
  }
});

test("api/types.ts 不再重复声明已迁移的账号/命令域类型", () => {
  const source = readFileSync("src/api/types.ts", "utf8");
  // 只看声明行，import re-export 段不参与判断
  const declarationLines = source
    .split(/\r?\n/)
    .filter((line) => /^(export )?(interface|type|class) /.test(line.trim()));
  for (const name of [...ACCOUNT_DOMAIN_NAMES, ...COMMAND_DOMAIN_NAMES]) {
    const offenders = declarationLines.filter((line) =>
      new RegExp(`^\\s*(export\\s+)?(interface|type)\\s+${escapeRegExp(name)}\\b`).test(line),
    );
    assert.deepEqual(offenders, [], `src/api/types.ts 仍重复声明 ${name}，与 re-export 冲突`);
  }
});

test("域名模块底层引用 OpenAPI 生成类型", () => {
  for (const file of ["src/api/types.accounts.ts", "src/api/types.commands.ts"]) {
    const source = readFileSync(file, "utf8");
    assert.ok(
      source.includes("@/api/schema"),
      `${file} 应引用 @/api/schema 生成类型`,
    );
    assert.ok(
      /components as ApiComponents|ApiComponents\["schemas"\]/.test(source),
      `${file} 应通过 components/schemas 引出类型`,
    );
  }
});
