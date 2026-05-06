# Sprint 4 — 已交付（Wave 1 = v0.3.1，Wave 2 = v0.4.0，Wave 3 = v0.5.0，2026-05-06）

> 本目录归档自 `agent-plans/`。详细变更见 [CHANGELOG.md](../../../CHANGELOG.md) 对应段落。

## Wave 1 交付（v0.3.1）

| Plan 文件 | 主要交付 |
|----------|---------|
| `SPRINT4-WAVE1.md` | Telethon 升 1.43.x + ,help 折叠 + 短别名（含自定义命令模板 aliases 字段）+ ,del N 撤回命令 |

数据库迁移：`0011_command_aliases`

## Wave 2 交付（v0.4.0）

| 子任务 | 主要交付 |
|--------|---------|
| 2-A | 砍空架子（删 group_admin / monitor / 插件市场 UI / plugin_repo 表） |
| 2-B | `docs/PLUGIN-DEV-GUIDE.md` + `examples/plugins/translate/` 移植样例 |
| 2-C | scheduler 插件完整实装（cron / once / interval 三种模式，20 行 → 283 行） |
| 2-D | 多 Telegram Bot 通知通道（项目启动 / account dead 自动告警） |
| 2-E | `docs/DEPLOY-PUBLIC.md` + `deploy/Caddyfile.example` |

数据库迁移：
- `0012_drop_plugin_repo`（DROP TABLE plugin_repo CASCADE）
- `0013_notify_bot`（NotifyBot 表，bot_token Fernet 加密）

## Wave 3 交付（v0.5.0 — RC1）

| 子任务 | 主要交付 |
|--------|---------|
| 3-A | GitHub Actions CI（`.github/workflows/ci.yml`：pytest + ruff + pnpm build） |
| 3-B | README 重写为开源向 + SECURITY-OPS 润色（应急响应工单模板） |
| 3-C | LICENSE（MIT）+ pyproject.toml / package.json 添加 license 字段 |
| 3-D | 仓库归档清理（本 README 更新，Wave 3 plan 归档） |

**里程碑**：首个 Release Candidate，项目可以开源了。

## 学到的教训（写入 agent-plans/README.md §1 约定 B 和 §7 反模式）

1. **alembic 编号要查 head**——Wave 2-A 误把 `down_revision` 设为 `"0010"` 而不是 `"0011"`，与 Wave 1 构成分叉。汇总人发现后改成 `"0011"` 修复。教训：每个会话写迁移前先 `alembic heads` 看现在哪。
2. **Wave 1 的"完成报告"过早写"pytest 红"**——当时 group/monitor 还没删，但报告作者自己没法删。正确做法：在完成报告里说"等 Wave 2-A 删完后整体跑应该绿"，而不是直接 ❌。这对后面汇总的人是误导。

## 归档时刻

2026-05-06，Sprint 4 三波全部完成，bump 0.5.0 RC1 + 写本溯源 README。
