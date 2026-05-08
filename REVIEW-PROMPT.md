# TeleBot 项目审查提示词（Codex / GPT-5.5）

> 复制以下内容到 Codex 对话中，附上项目仓库链接即可。

---

## 项目背景

TeleBot 是一个基于 Telegram UserBot 的多账号管理平台，部署在 Oracle Cloud 服务器上。

**技术栈：**
- 后端: Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic + Redis (async)
- 前端: React 18 + TypeScript + TailwindCSS + TanStack Query
- Telegram: Telethon 1.43+ (MTProto API，UserBot 模式，非 Bot API)
- Worker: 每个账号一个独立 worker 子进程，Redis IPC 通信
- 插件系统: Plugin 基类 + loader + generation guard 热重载

**仓库地址:** https://github.com/Anoyou/telebot

---

## 审查维度

### 一、LLM 集成审查（重点）

当前 LLM 用于插件的 AI 功能（翻译、引用分析、24点游戏辅助等），通过 UserBot 方式在 Telegram 中调用。

**请审查以下方面：**

1. **Provider 架构**
   - 多 provider 支持（openrouter / anyrouter / nvidia / yunai / qwen）
   - fallback 机制是否可靠（一个 provider 挂了能否自动切换）
   - provider 配置的灵活度（用户能否自定义 endpoint/model）

2. **调用链路**
   - UserBot → Telethon → Plugin → LLM Provider → API 的完整链路
   - 超时处理：LLM API 响应慢时是否有 timeout 保护
   - 重试策略：网络抖动时是否自动重试
   - 降级策略：LLM 不可用时是否有 fallback（如返回固定提示）

3. **Telegram 场景特有问题**
   - UserBot 模式下 LLM 回复的消息长度限制（Telegram 单条消息 4096 字符）
   - 消息编辑（msg.edit）作为 LLM 流式输出的替代方案
   - LLM 回复中的 HTML 格式化（Telegram 支持 `<b>` `<code>` 等）
   - 长回复时是否自动分段发送

4. **安全性**
   - Prompt 注入防护：用户消息拼接到 prompt 前是否有 sanitization
   - API Key 存储：master_key 加密链路是否完整
   - 敏感信息泄露：LLM 回复中是否可能暴露 API Key / session 等

5. **成本控制**
   - 是否有 token 用量统计
   - 是否有调用频率限制（避免用户滥用）
   - 模型选择是否有成本意识（贵的模型是否加了限额）

6. **未来扩展建议**
   - 流式输出（SSE）在 UserBot 中的可行方案
   - 多轮对话的上下文管理
   - LLM 结果缓存（相同 prompt 的重复调用）
   - A/B 测试不同 provider 的效果

### 二、插件系统审查（重点）

**请审查以下方面：**

1. **插件生命周期**
   - 启动: on_startup 在什么时机调用？失败时怎么处理？
   - 热重载: generation guard 机制是否可靠？热重载时旧 handler 是否真的不再触发？
   - 卸载: on_shutdown 是否保证调用？cleanup 是否幂等？
   - 异常隔离: 单个插件崩溃是否影响其他插件和 worker？

2. **远程插件系统（v0.9.x 新增）**
   - git clone 的安全性：是否有路径穿越、恶意仓库防护
   - manifest.json 验证是否完备
   - 插件安装后的热加载是否可靠（通过 Redis IPC + 进程内双路径）
   - 卸载时文件清理和 DB 清理是否完整

3. **config_schema 两级配置**
   - global（插件级）和 account（账号级）配置的读取优先级
   - config_schema 的 JSON Schema 验证是否到位
   - 配置变更是否触发 worker 热加载
   - 前端 ConfigDialog 的 schema 渲染是否完整

4. **owner_only 安全机制**
   - UserBot 模式下非 owner 触发命令的防护
   - Sudo 用户的豁免逻辑
   - owner_only 是否覆盖了所有消息处理路径

5. **插件与 Worker 的交互**
   - 插件拿到的 client 是否有权限边界（如不能直接访问 Redis/DB）
   - 插件的 on_command 返回 True 后，后续插件是否正确跳过
   - 插件的 message_channels 声明是否被正确过滤

6. **优化建议**
   - 插件依赖管理（requires_features）
   - 插件版本兼容性检查（min_telebot_version）
   - 插件配置表单的 JSON Schema 完整性
   - 插件的单元测试覆盖率

### 三、架构 & 安全通用审查

1. **Worker 隔离**: 每个账号一个子进程，进程间的资源隔离边界
2. **Redis IPC**: 消息丢失/重复/乱序的容错
3. **Flood Wait**: Telethon 的 FloodWaitError 处理
4. **Session 加密**: Fernet 加密链路完整性
5. **JWT 安全**: 签发/校验/过期机制
6. **Alembic 迁移**: 版本管理是否安全（有无撞号风险）

---

## 输出格式要求

请按以下格式输出：

### 每个发现的问题：
```
**[P0/P1/P2] 问题标题**

- 描述: 一句话说明
- 文件: 具体文件路径和行号
- 影响: 什么场景下会触发
- 修复: 具体代码修改方案或架构建议
```

等级说明：
- P0: 安全漏洞 / 数据丢失风险 / 生产环境崩溃
- P1: 功能缺陷 / 用户体验问题 / 性能瓶颈
- P2: 代码质量 / 可维护性 / 优化建议

### 最后给出：
1. **优先级排序的修复清单**（P0 → P1 → P2）
2. **短期优化路线图**（1-2 周内可做的改进）
3. **中期架构建议**（1-3 个月的方向性建议）
4. **测试覆盖率分析**：哪些核心路径缺少测试

---

## 额外检查项

- [ ] 扫描 TODO / FIXME / HACK 注释，列出未处理的技术债
- [ ] 检查 TypeScript strict 模式下的潜在类型问题
- [ ] 对比 TeleBox (TypeScript userbot) 找出功能差距
- [ ] LLM 的 prompt 模板是否可配置（用户能否自定义 system prompt）
- [ ] 插件的 config_schema 是否覆盖了所有已知插件的可配置项
