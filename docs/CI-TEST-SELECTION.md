# CI 增量测试

Beta push 和面向 Beta 的 PR 按改动影响选择测试。main push、面向 main 的 PR、`v*` 标签及手动运行执行完整回归。首次提交这批 CI 规则变更也会全量；后续符合条件的业务改动才缩小范围。

## 选择与保留

- 后端 pytest 合并 base/head 的 Python 导入、字面量动态目标和反向依赖，运行相关测试文件；固定保留鉴权、OpenAPI 和读取源码的守护用例。全局 fixture 会在选定用例中照常执行，其直接执行/重置的模块及 fixture 本身变化时全量。
- 前端基础类型检查、lint、单测、构建保持原有门禁。慢速浏览器回归在安装依赖后用仓库的 TypeScript 解析器建图，合并 base/head 影响；三个设备仍执行相同的已选 spec 集合，每片单 worker。
- iOS 保留编译、打包校验与 Core 测试；UI 用例按本次清单和历史耗时分到最多四片。不存在的 ID、空清单、重复 ID 或分片数错误会失败，不能悄悄漏掉用例。
- Gateway 仅在 gateway 源码/构建范围变化时运行；数据库迁移仅在模型、数据库基础设施、迁移文件或后端依赖变化时运行。相关 API/插件契约与版本一致性门禁继续保留。

## 已核对的页面

| 范围 | 当前支持增量的页面 | 仍保留的跨页面覆盖 |
| --- | --- | --- |
| Web | 账本、Webhook | 无障碍、视觉基线，以及对应 spec 中的完整跨页流程 |
| iOS | 账本、消息模板实验室 | 登录、界面锁、概览、导航/搜索、SF Symbols，以及直接访问该页面的测试 |

例如 `LineTrend.tsx` 会通过引用关系追到 Web 账本。共享组件影响任何未登记页面时回退整个浏览器套件。iOS 采用经核对的页文件白名单，并检查导出类型在当前/基线版本的外部引用边界；新增其他调用方、顶层共享定义或未知页面均全量。

当前仓库的真实清单模拟结果（不是云端耗时）：

| 仅改动的范围 | 完整清单 | 增量清单 |
| --- | ---: | ---: |
| Web 账本页 | 408 项 / 三设备 | 207 项 / 三设备 |
| Web Webhook 页 | 408 项 / 三设备 | 135 项 / 三设备 |
| iOS 账本页 | 114 个 UI 方法 | 18 个 UI 方法 |
| iOS 模板实验室 | 114 个 UI 方法 | 16 个 UI 方法 |
| 后端 interaction_rule_migration.py | 248 个测试文件 | 63 个测试文件 |

页面外的源码、依赖、公共入口、fixture、动态引用无法解析、删除/重命名或无可追溯测试时保守全量。后端共享模型、权限、配置、worker、事件总线和插件边界全量。测试集摘要变化时页面白名单也失效，直到重新核对覆盖关系。

## 基线与输出

Beta 使用最近成功且确为当前提交祖先的对应工作流 push，不使用刚被取消/失败的上一提交作增量基线。获取失败、没有成功祖先或 Git 差异不可用时全量。PR 使用 merge-base 到 PR head 的完整差异。iOS 既有的同树复用/契约快捷路径不会绕过正式回归，快捷路径与增量选择共享成功基线。

仅 `backend/app/__init__.py` 的版本字面量、`frontend/package.json` 的 version、OpenAPI 的 info.version 变化会在比较内容后排除运行时改动。依赖、Python 语句或 API 内容同时改变时不能排除。版本同步检查和必要的镜像发布继续执行。

Actions 的范围摘要记录选择模式和原因；实际 pytest / Playwright 参数也写入对应 job 摘要。浏览器每个 job 只建一次依赖图，a11y/visual 共用该清单；缓存与变更上下文不匹配时回退全量。

## 修改规则

入口为 `scripts/ci_test_plan.py`、`scripts/ci_frontend_impact.cjs`，已核对关系为 `scripts/ci-test-map.json`。扩展白名单时必须阅读实际源文件、调用方和完整跨页测试；新用例或 fixture 改动后重新核对相关映射，再更新 `suite_hash` / `fixture_hash` 摘要。不能只刷新摘要来消除全量回退。

本地预览最近一次提交（CI 的 Beta 基线由 `ci_baseline.py` 自动确定）：

```bash
python3 scripts/ci_test_plan.py --base HEAD~1 --head HEAD --target ci
python3 scripts/ci_test_plan.py --base HEAD~1 --head HEAD --target ios
python3 scripts/ci_test_plan.py --force-full
node --test scripts/ci_frontend_impact.test.cjs
```

Python 回归见 `backend/app/tests/test_ci_incremental.py`；它验证依赖扩散、版本误判、未知范围/坏清单、成功祖先、实际工作流全量条件及 iOS 分片并集。完整回归保留作增量策略的兜底；没有新增定时任务。
