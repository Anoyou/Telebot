# Sprint 3 — 已交付（v0.3.0，2026-05-05）

> **本 sprint 没有正式的 plan md 文件**——是 ad-hoc（用户和单会话直接迭代）完成的，
> 因此这个 README 是事后追溯。详细变更见 [CHANGELOG.md](../../../CHANGELOG.md) `[0.3.0]` 段。

## 主轴：LLM Provider 体系大升级 + 安全加固

之前 0.2.0 的 LLM 仅是骨架级（OpenAI / Anthropic 直连，单模型，chat_completions 写死），
本 sprint 把这条线做到生产可用级别。

## 数据库迁移

| 编号 | 内容 |
|------|------|
| `0006_llm_provider_routing` | modality / tags / cost_tier / notes 字段 |
| `0007_llm_provider_proxy` | proxy_id 外键，LLM 调用走代理 |
| `0008_llm_provider_models` | models JSONB[] —— 单 provider 多模型 |
| `0009_llm_provider_api_format` | api_format（chat_completions / responses / anthropic_messages）|
| `0010_web_user_pwd_version` | JWT pwd_version 校验，改密旧 token 自动失效 |

## 关键代码新增

- `services/llm_client.py` 重构（635 行）— `OpenAIClient` / `ResponsesClient` / `AnthropicClient`
- `services/llm_format.py` 新建（336 行）— 输出模板渲染器（占位符 + 条件块 + HTML 折叠）

## 教训

本 sprint 用户没让 sub-session 走 README §6 的版本号 + CHANGELOG bump 流程。
因此 0.2.0 -> 0.3.0 之间堆了 4 次迁移没记录。后来由 0.3.0 bump 时一次性补回 CHANGELOG。

**约定**：以后哪怕是 ad-hoc 单会话迭代，只要触及 schema / 协议层，必须在收尾时 bump 版本 + 更新 CHANGELOG。
