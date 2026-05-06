# inline @provider 覆盖 Bug 修复说明

## 问题描述

用户反馈：使用 `,ai @AnyGPT 问题` 时，系统报错：

```
✗ AI 调用失败：OpenAI 接口返回 404: {"error":"当前 API 不支持所选模型 mimo-v2.5"...}
```

**预期行为**：应该使用 AnyGPT 的 `default_model`（如 `gpt-4o`）

**实际行为**：错误地使用了命令模板里配置的 `mimo-v2.5`

## 根本原因

在 `backend/app/worker/command.py` 的 `_run_ai` 函数中，`override_model` 的赋值逻辑有误：

```python
# 旧代码（有 bug）
override_model = cfg.get("model")  # ← 总是从模板读取
if inline_model_override:
    override_model = inline_model_override
```

当用户使用 `,ai @AnyGPT 问题` 时：
1. ✅ `inline_provider_override` 被正确设置为 AnyGPT 的 ID
2. ❌ `inline_model_override` 是 `None`（因为用户没写 `:model`）
3. ❌ `override_model = cfg.get("model")` 从命令模板读取了 `mimo-v2.5`
4. ❌ 因为 `inline_model_override` 是 `None`，所以 `override_model` 保持为 `mimo-v2.5`
5. ❌ 最终传给 `build_client(fake_row, override_model="mimo-v2.5", ...)`
6. ❌ 即使 provider 换成了 AnyGPT，但 model 还是用的模板里的 `mimo-v2.5`

## 修复方案

修改 `override_model` 的决策逻辑，明确三种情况的优先级：

```python
# 新代码（已修复）
# 决策 override_model 优先级：
#   1. inline @name:model 显式指定 → 用该 model
#   2. inline @name（未指定 model）→ 清空 override，让 build_client 用 provider.default_model
#   3. 都没 inline override → 用模板配置的 model（可能为 None）
if inline_model_override:
    # 情况 1：用户显式写了 @name:model
    override_model = inline_model_override
elif inline_provider_override is not None:
    # 情况 2：用户只写了 @name，没写 :model
    # 必须清空 override_model，否则会错误地用模板里配的 model（那是给原 provider 用的）
    override_model = None
else:
    # 情况 3：没有 inline override，按模板配置走
    override_model = cfg.get("model")
```

## 修复后的行为

### 场景 1：`,ai @AnyGPT 问题`
- provider：AnyGPT
- model：AnyGPT 的 `default_model`（如 `gpt-4o`）
- ✅ 正确

### 场景 2：`,ai @AnyGPT:claude-3-5-sonnet 问题`
- provider：AnyGPT
- model：`claude-3-5-sonnet`（显式指定）
- ✅ 正确

### 场景 3：`,ai 问题`（无 inline override）
- provider：模板配置的 provider（如 Mimo）
- model：模板配置的 model（如 `mimo-v2.5`）
- ✅ 正确

## 测试覆盖

新增 3 个测试用例：

1. `test_run_ai_inline_provider_without_model_clears_template_model`
   - 验证 `,ai @AnyGPT 问题` 清空 `override_model`

2. `test_run_ai_inline_provider_with_model_overrides_everything`
   - 验证 `,ai @AnyGPT:claude-3-5-sonnet 问题` 完全覆盖

3. 已有测试全部通过（无回归）

## 用户操作建议

修复后，用户可以：

1. **查看可用 provider**：
   ```
   ,ai @list
   ```

2. **临时切换 provider**（使用其 default_model）：
   ```
   ,ai @AnyGPT 你的问题
   ```

3. **临时切换 provider + 指定 model**：
   ```
   ,ai @AnyGPT:claude-3-5-sonnet 你的问题
   ```

4. **强制 auto 路由**（即使模板是 fixed）：
   ```
   ,ai @auto 你的问题
   ```

## 相关文件

- 修复：`backend/app/worker/command.py` (L697-710)
- 测试：`backend/app/tests/test_inline_override.py` (新增 3 个测试)
- 文档：`backend/app/worker/inline_override.py` (DSL 解析逻辑)
- 变更日志：`CHANGELOG.md` (v0.5.0 Fixed 段落)

## 版本信息

- 修复版本：v0.5.0
- 发布日期：2026-05-06
- 类型：Bug Fix
