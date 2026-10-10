# LLM 密钥池与模型元数据

本文说明同一 Provider 挂多把 API Key 的选钥规则、密钥级冷却、prompt cache 亲和，以及从上游 `/v1/models` 自动读入的模型元数据如何影响路由与表单。它只覆盖 TelePilot 自身的行为；插件侧仍按 [PLUGIN-AI.md](./PLUGIN-AI.md) 的 `route` / `provider_tag` 选 Provider，不能指定 Key。

## 密钥池

### 候选与策略

- 候选 = Provider 自身的主密钥（若已配置）+ 池内 `enabled` 的 Key。主密钥固定优先级 0，池内 Key 按 `priority`（越小越先）排序。
- 没有池内 Key 时三条调用链路（`complete` / `stream_complete` / `run_agent`）与单密钥时期完全一致，不会产生任何 Redis 流量。
- `key_strategy` 只影响池内顺序：
  - `order`（默认）：永远先试优先级最小的 Key，只有它不可用时才落到下一把。
  - `rotate`：每次请求轮转起点，把并发摊到多把 Key 上。
  - `least_used`：按当日 Token 用量从少到多排序，适合额度分散的多把免费 Key。
- 池内 Key 可单独设置 `daily_request_limit` / `daily_token_limit`（按 UTC 日计）。超限的 Key 在当日不再参与选钥；全部超限且无主密钥时该 Provider 本轮视为不可用，走既有 fallback。
- 池内 Key 可指定独立出口代理（`proxy_id`），留空沿用 Provider 代理。

### 密钥级冷却

- 只有「跟 Key 有关」的错误会换钥：鉴权失败（401 / 403）与限流 / 额度耗尽（429）。换钥不消耗 Provider 级重试次数；其余错误仍走原有重试与 Provider fallback。
- 冷却时长：限流 30 秒；凭据错误 10 分钟；其它按连续失败次数指数递增，上限 10 分钟。成功一次即清零。
- 冷却状态只保存在进程内存，与 `provider_health` 的 Provider 级健康相互独立：一把 Key 冷却不会拖累整个 Provider。
- 冷却中的 Key 排到末尾而不是剔除：所有 Key 都在冷却时仍会按剩余时间最短的先试。
- 流式调用只在收到第一个增量之前允许换钥，避免给用户重复输出。

### prompt cache 亲和

- 同一对话尽量粘在同一把 Key 上，让上游 prompt cache 继续命中。作用域优先 System Agent 会话（`session:{id}`），其次 Telegram 账号（`account:{id}`）；记录保存在 Redis，30 分钟内有效。
- 亲和 Key 在冷却时会被跳过；Redis 不可用时退化为无亲和、无当日用量约束，但选钥照常进行。

### 安全边界

- 池内 Key 以 `api_key_enc` 经 `MASTER_KEY` 加密落库，`app.scripts.rekey` 已覆盖。
- 所有读取接口只返回 `label` 与尾号 `key_hint`（Key 长度不足 8 位时不提供尾号）；审计只记录 `api_key_changed`，不记录值。
- `codex_gateway` Provider 不支持密钥池（凭据由 Gateway 自身管理）。

### 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/commands/llm-providers/{pid}/keys` | 列出池内 Key，含健康状态与当日用量 |
| `POST` | `/api/commands/llm-providers/{pid}/keys` | 新增（每个 Provider 最多 32 把） |
| `PATCH` | `/api/commands/llm-providers/{pid}/keys/{key_id}` | 修改；`api_key` 留空不动，`clear_proxy` 切回沿用 Provider 代理 |
| `DELETE` | `/api/commands/llm-providers/{pid}/keys/{key_id}` | 删除 |

`key_strategy` 随 Provider 的创建 / `PATCH` 一起保存。「AI → 近期调用」的 `provider_key_label` 记录本次命中的 Key 标签，空表示主密钥或老记录。

## 模型元数据

「获取模型列表」除模型 ID 外还会解析上游 `/v1/models` 里的元数据并写入模型清单：

| 字段 | 来源字段（任一） |
| --- | --- |
| `label` | `display_name` / `name` / `magpie_label`（与 ID 相同时丢弃） |
| `context_window` | `context_window` / `context_length` / `max_input_tokens` / `max_model_len` / `capabilities.context_length` |
| `max_output_tokens` | `max_output_tokens` / `max_completion_tokens` / `top_provider.max_completion_tokens` |
| `input_modalities` / `supports_images` | `modalities.input` / `architecture.input_modalities` / `capabilities.supports_vision` |
| `supported_reasoning_levels` | `supported_reasoning_levels`（支持 `[{"effort": "low"}]` 形态）/ `capabilities.reasoning_effort` |
| `supported_api_formats` | `native_endpoints`（`/v1/responses` → `responses`） |

覆盖 Magpie、OpenRouter、LiteLLM 三类上游的命名；`kind: image` 等绘图 / 嵌入条目会被过滤。合并规则是「只填空缺」：用户已勾选的 `enabled`、手填的 `label` 与能力位永远保留，`metadata_source` 记为 `upstream`。

自动路由（`route=auto`）处理长输入时，若没有打 `long_context` 标签的 Provider，会按 `context_window` 过滤掉放不下的候选，再在放得下的里面选成本最低的；没有元数据时沿用旧逻辑。

## Magpie 网关

[yetone/magpie](https://github.com/yetone/magpie) 默认监听 `127.0.0.1:3425`。在「新建模型提供商 → Base URL」下方的快捷模板里点「Magpie 网关」会预填地址 `http://127.0.0.1:3425/v1`、Chat Completions 协议与 `openai` 服务类型；API Key 填网关自身的 Key。网关 `/v1/models` 返回的 `provider/model` ID、上下文窗口、推理档位会随「获取模型列表」一并写入。快速验证对 3425 端口或主机名含 `magpie` 的地址会建议名称「Magpie 网关」，并且不会误判为 Ollama。
