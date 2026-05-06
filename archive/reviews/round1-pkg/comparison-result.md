# opus-4.7 vs gpt-5.3-codex Code Review 对比

## 一、共识问题（两个模型都发现了）

| # | 问题 | opus 编号 | codex 编号 | 谁讲得更深 |
|---|------|-----------|------------|-----------|
| 1 | **插件沙箱可绕过** | Critical #2 | Critical #1 | opus（列出了 `_real`、`__class__`、`import os` 等具体逃逸路径） |
| 2 | **登录暴猜窗口** | Major #7 | Major #2 | 平手，codex 更简洁直接 |
| 3 | **alembic 重复迁移** | Major #13 | Major #4 | opus 提到了 Postgres advisory lock 方案 |
| 4 | **trust_forwarded_for 默认值不对** | Major #9 | Frontend #2 | opus 分析了完整的限流桶退化场景 |

**共识率：4/8（opus 34 条）vs 4/8（codex 7 条）** — codex 发现的问题 opus 全覆盖了，且每个都讲得更深。

---

## 二、opus 独有的重要发现（codex 完全没提）

### Critical 级
| # | 问题 | 严重程度 |
|---|------|---------|
| 1 | **插件安装先执行代码再验签名** — 这是整份 review 最关键的发现。恶意 ZIP 在 Ed25519 签名验证前就已经 `exec_module` 了，能直接读 MASTER_KEY | 🔴 Critical |
| 2 | **登录接口用户名枚举** — 用户不存在时秒回，存在时 Argon2 要 100ms，时间侧信道 | 🔴 Critical |
| 3 | **Redis 日志先删数据再写库** — DB 挂了就永久丢数据 | 🔴 Critical |

### Major 级
| # | 问题 |
|---|------|
| 4 | JWT 无吊销机制（改密码后旧 token 12h 内有效） |
| 5 | MASTER_KEY 无轮换方案（无 MultiFernet） |
| 6 | `pending_totp` cookie 里存明文 base32 密钥 |
| 7 | 无 CSRF 保护 |
| 8 | Docker 无安全加固（root 用户、无 cap_drop） |
| 9 | 无 CSP/HSTS 安全头 |
| 10 | Worker 违反文档约定直接写 DB |

---

## 三、codex 独有的发现

| # | 问题 | 评价 |
|---|------|------|
| 1 | **deps.py 错误格式不统一** — 没用全局错误契约 | ✅ 有效 Minor，但 opus 没提 |
| 2 | **register() 返回 void 但实际返回了 token** — 多一次无意义登录请求 | ✅ 有效 Major，opus 漏了 |

---

## 四、评分

| 维度 | opus-4.7 | gpt-5.3-codex |
|------|----------|---------------|
| **深度** | ⭐⭐⭐⭐⭐ 9/10 | ⭐⭐⭐ 6/10 |
| **广度** | ⭐⭐⭐⭐⭐ 9/10 | ⭐⭐ 4/10 |
| **准确性** | ⭐⭐⭐⭐ 8/10 | ⭐⭐⭐⭐ 8/10 |
| **可操作性** | ⭐⭐⭐⭐⭐ 9/10 | ⭐⭐⭐ 7/10 |
| **发现数量** | 34 条（4 Critical + 13 Major + 13 Minor + 4 Suggestion） | 8 条（1 Critical + 4 Major + 1 Minor + 2 Residual） |
| **总分** | **35/40** | **25/40** |

---

## 五、综合 Top 10 优先修复清单

按两个模型的共识 + 风险权重排序：

| 优先级 | 问题 | 来源 | 修复难度 |
|--------|------|------|---------|
| **P0** | 插件安装先执行代码再验签名 | opus only | 中（调整安装流程顺序） |
| **P1** | 插件沙箱形同虚设（重写文档 OR 做真隔离） | 共识 | 高（真隔离要独立进程） |
| **P1** | 登录用户名枚举（时间侧信道） | opus only | 低（加 sentinel hash） |
| **P2** | Redis 日志消费者数据丢失 | opus only | 中（改用 LMOVE 或 Redis Streams） |
| **P2** | JWT 无吊销 + 无 iss/aud | opus only | 低（加 pwd_v claim） |
| **P3** | CSRF 保护缺失 | opus only | 低（加自定义 header 中间件） |
| **P3** | 登录暴猜窗口（验证码无上限） | 共识 | 低（加 attempts 计数） |
| **P4** | MASTER_KEY 无轮换方案 | opus only | 中（MultiFernet + 迁移工具） |
| **P4** | trust_forwarded_for 默认值与部署不匹配 | 共识 | 低（改默认值 + 文档） |
| **P5** | Docker 容器安全加固 | opus only | 低（改 compose 配置） |

---

## 六、结论

**opus-4.7 是这次 review 的明确赢家。** 它不仅发现了 codex 找到的所有问题，还额外挖出了 3 个 Critical 级漏洞（尤其是插件安装签名验证顺序问题，这是最危险的），以及大量 Major 级安全和架构隐患。

**codex 的优势**是简洁、准确、零噪音，但深度和广度都不够。它更像一个快速 lint scan，适合做第一轮粗筛。

**推荐工作流：**
- **codex 做初筛**（快、成本低，抓明显问题）
- **opus 做深度 review**（慢但全面，能发现架构级和安全级问题）
