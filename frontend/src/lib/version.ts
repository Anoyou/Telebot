// 应用版本号 — 前端从 frontend/package.json 读取，不再单独维护字面量。
//
// 版本号只在准备发布、推送稳定检查点、创建 release/PR，或用户明确要求
// “推一版/发一版”时统一迭代；不要为每个微小提交单独 bump。
//
// 改版本用一条命令（仓库根目录）：
//   python3 scripts/bump-version.py --title "本次主题"
// 它按 AGENTS.md 规则算出下一个版本号，写入全部版本源：
//   1) backend/app/__init__.py              ← 后端单点（pyproject.toml 通过 dynamic 读取它）
//   2) frontend/package.json                ← 前端单点（本文件按名导入 version 字段，Vite 只打包这一个字段）
//   3) openapi/telepilot.openapi.json       ← info.version 直接改写，无需完整 codegen
// 并把 CHANGELOG.md 的 Unreleased 移到新版本段落。`python3 scripts/check-version-sync.py` 校验一致性（CI 同款）。
//
// 命名约定（SemVer：MAJOR.MINOR.PATCH）：
//   - MAJOR  破坏兼容的数据库迁移、配置/API 不兼容、无法平滑升级
//   - MINOR  用户可感知的新能力、主要入口/信息架构重组、重要工作流变化
//   - PATCH  bug 修复、文案、小 UI、错误提示、测试补充、兼容性补丁
//
// APP_STAGE 是非正式标签：
//   - 路线图阶段："MVP"、"Sprint 2"、"RC1"
//   - 生产稳定时设为 null（达到 1.0.0 通常就摘掉）

import packageJson from "../../package.json" with { type: "json" };

export const APP_VERSION: string = packageJson.version;
export const APP_STAGE: string | null = null;

/** Sidebar / About 等 UI 处使用的展示串。例："v0.2.0 · Sprint 2" 或 "v0.2.0"（STAGE 为 null 时）。 */
export const APP_VERSION_LABEL = APP_STAGE
  ? `v${APP_VERSION} · ${APP_STAGE}`
  : `v${APP_VERSION}`;
