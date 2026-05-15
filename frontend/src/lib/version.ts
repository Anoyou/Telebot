// 应用版本号 — 前端单点定义。
//
// 每次 release 同时改 4 处（缺一不可）：
//   1) frontend/src/lib/version.ts          ← 本文件（前端 UI 显示）
//   2) frontend/package.json                ← npm/pnpm 包元数据
//   3) backend/app/__init__.py              ← Python 包 __version__（main.py + ,version 都读它）
//   4) backend/pyproject.toml               ← pip install / 打包发布元数据
//
// 同步追加：CHANGELOG.md 顶部新增 `## [x.y.z] — yyyy-mm-dd` 段。
// 详见 agent-plans/README.md §6 的"版本号与 CHANGELOG"清单。
//
// 命名约定（SemVer：MAJOR.MINOR.PATCH）：
//   - MAJOR  破坏性变更（数据库不兼容迁移 / 协议大改 / 配置项重命名）
//   - MINOR  向后兼容的功能增量（一个 Sprint 通常 +1）
//   - PATCH  bug 修复 / 文档 / 小调整 / hotfix
//
// APP_STAGE 是非正式标签：
//   - 路线图阶段："MVP"、"Sprint 2"、"RC1"
//   - 生产稳定时设为 null（达到 1.0.0 通常就摘掉）

export const APP_VERSION = "0.14.3";
export const APP_STAGE: string | null = "feature";

/** Sidebar / About 等 UI 处使用的展示串。例："v0.2.0 · Sprint 2" 或 "v0.2.0"（STAGE 为 null 时）。 */
export const APP_VERSION_LABEL = APP_STAGE
  ? `v${APP_VERSION} · ${APP_STAGE}`
  : `v${APP_VERSION}`;
