# Archive — 历史归档区

> 所有"老旧 / 无用 / 已完成"的文档统一搬这里。**只进不出**——归档后不再修改，仅供溯源和考古。

## 1. 目录结构

```
archive/
├── README.md                   # 本文件（结构索引）
├── plans/                      # 已交付的 sprint plan
│   ├── SPRINT2/                # Sprint 编号子目录
│   │   ├── README.md           # 简述本 sprint 交付了什么
│   │   ├── SPRINT2-UX-OPS.md
│   │   ├── SPRINT2-CUSTOM-COMMAND.md
│   │   ├── SPRINT2-IGNORED-PEERS.md
│   │   ├── SPRINT2-PLUGIN-MODULARIZE.md
│   │   └── SPRINT2-FORWARD.md
│   ├── SPRINT3/                # 没有正式 plan，写 README 追溯
│   │   └── README.md           # 0007-0010 LLM 重构 + JWT 安全加固，这一波是 ad-hoc 进行的
│   └── SPRINT4/                # Wave 1/2/3 完成后从 agent-plans 搬过来
├── reviews/                    # review 中间产物
│   ├── round1-pkg/             # 第一轮 review 的 7 个分文件
│   └── REVIEW-FIXES-REPORT.md  # 修复总报告
├── prd/                        # 历史 PRD 版本
│   └── PRD-original.md         # teleuserbotold.md 改名搬进来
└── per-module-plans/           # Sprint 1 时给单 agent 用的 module-level AGENT_PLAN.md
    ├── frontend.md             # ./frontend/AGENT_PLAN.md
    ├── deploy.md               # ./deploy/AGENT_PLAN.md
    ├── backend-services.md     # ./backend/app/services/AGENT_PLAN.md
    ├── backend-worker.md       # ./backend/app/worker/AGENT_PLAN.md
    ├── backend-worker-plugins.md       # ./backend/app/worker/plugins/AGENT_PLAN.md
    └── backend-worker-ratelimit.md     # ./backend/app/worker/ratelimit/AGENT_PLAN.md
```

## 2. 归档规则

### 什么时候归档
- 一个 plan 的"完成报告"已经填到 plan 末尾且通过了所有验收
- 一份 review 报告已经在主代码里被吸收（见 CHANGELOG）
- 一个 module-level AGENT_PLAN.md 对应的模块已经稳定到不需要 plan 文档指导

### 怎么归档
**永远不要直接 `rm`**：

```bash
# 已完成的 sprint plan
mv agent-plans/SPRINT2-*.md archive/plans/SPRINT2/
# 在 archive/plans/SPRINT2/ 加一个 README.md 写"本 sprint 交付了什么"

# review 中间产物
mv review-pkg/ archive/reviews/round1-pkg/
mv REVIEW-FIXES-REPORT.md archive/reviews/

# 旧 PRD
mv teleuserbotold.md archive/prd/PRD-original.md

# module-level AGENT_PLAN
mv frontend/AGENT_PLAN.md archive/per-module-plans/frontend.md
# ...
```

### 不归档的（保留在原位）
- `agent-plans/README.md` — 是规范文档，不算 plan
- `agent-plans/SPRINT*-*.md`（**未完成的**）
- `docs/*.md` — 用户/开发者向文档（PLUGIN-DEV-GUIDE / SECURITY-OPS / DEPLOY-PUBLIC / CONTRACTS）
- `CHANGELOG.md` / `README.md` / `LICENSE`
- 顶层任何"持续维护"性质的 md

### 归档后的操作约束
- 文件**不再修改**——发现错也只在新文档里更正，不回头改归档
- 文件**不再被代码引用**——CI / pre-commit 应该 fail
- 文件**目录可以重组**（如把 SPRINT2 整体改名 SPRINT2-features），但单个文件名尽量保留，方便链接

## 3. 何时清理 archive 本身

- 不主动清。仓库 Git 历史 + 归档目录是项目的考古层。
- 如果 archive 体积超过仓库本身（极少见），可以把 review 中间产物之类压缩成单 zip。
- 永远不删 plan/SPRINT*/——这是 sprint 历史。
