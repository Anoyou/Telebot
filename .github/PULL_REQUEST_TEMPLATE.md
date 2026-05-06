<!--
感谢提 PR！开发流程 / 代码规范请先读：
- CONTRIBUTING.md
- agent-plans/README.md §1 跨会话约定（其中"约定 B alembic 编号"和"约定 D AI 密钥红线"硬规矩，PR 撞了会被打回）
-->

## 这个 PR 在干嘛

<!-- 一两句话说明，不要复制 commit message -->

## 关联 issue

<!-- 用 `Closes #123` / `Refs #456` 关联；纯重构 / 文档改动可留空 -->



## 改动类型

- [ ] 🐛 Bug 修复
- [ ] ✨ 新功能
- [ ] 🔨 重构（不改外部行为）
- [ ] 📚 文档
- [ ] 🧪 测试
- [ ] ⚙️ CI / 构建
- [ ] 💥 破坏性变更（数据库不兼容迁移 / API 路径变更 / 配置项重命名）

## 测试

- [ ] `cd backend && pytest -q` 全绿
- [ ] `cd backend && ruff check .` 全绿
- [ ] `cd frontend && pnpm run build` 全绿
- [ ] 手测：`make restart` 后浏览器硬刷验证关键路径

## 涉及数据库迁移？

- [ ] 不涉及
- [ ] 涉及（迁移文件名：`alembic/versions/____.py`）
  - [ ] `alembic heads` 只一个 head（**重要**：之前出过两次分叉 0003 / 0012，参见 agent-plans/README.md §7）
  - [ ] `alembic upgrade head` 在 PG 上跑通
  - [ ] downgrade 已实现且测过（除非该迁移本质不可逆，已在迁移注释里说明）

## 涉及版本号 bump？

- [ ] 不需要（纯文档 / 测试 / CI 改动）
- [ ] 需要（已同步 5 处：`backend/app/__init__.py` / `pyproject.toml` / `package.json` / `frontend/src/lib/version.ts` / `CHANGELOG.md`）

## 安全自查

- [ ] **不**含明文密钥 / token / api_key / 密码 / session 字符串入库或入日志
- [ ] **不**在 GET 接口返回敏感字段（必要的只返 `has_xxx: bool`）
- [ ] **不**改 CORS 为 `*`
- [ ] 新增 LLM provider / 通知 bot / 任何含密码字段：用 `app/crypto.py:encrypt_str` 入库（约定 D）

## 截图（UI 改动必填）

<!-- 拖图进来 / 或贴 GIF -->

## 备注

<!-- 评审人需要知道的额外信息：性能权衡、未做的事、后续 PR 要做的事 -->
