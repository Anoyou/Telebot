# 插件配置系统改进方案

## 1. 问题

### 1.1 配置入口太深
当前流程：插件中心 → 功能 Tab → 点击插件 → "配置 →" → 账号详情 → 功能 Tab → 规则配置页
总共 3-4 层跳转才能配置一个插件。

### 1.2 没有 config_schema 的插件
game24、scheduler、translate 都没有 config_schema，点"配置"跳到空页面。

### 1.3 账号隔离 vs 复用
- **规则（rule.config）**：天然按账号隔离，每个账号独立配置 ✅
- **API Key 等全局配置**：应该跨账号共享，不需要每个账号配一遍 ❌ 当前不支持
- **插件默认配置**：新账号继承的默认值应该可配置 ❌ 当前不支持

---

## 2. 方案

### 2.1 配置层级：两级配置

| 层级 | 作用域 | 存储 | 说明 |
|------|--------|------|------|
| **插件级配置** | 全局（所有账号共享） | remote_plugin 或 plugin_config 表 | API Key、通用参数等 |
| **账号级配置** | 单个账号 | rule.config | 聊天 ID、模式开关等账号特有的 |

### 2.2 配置入口：内联弹窗（不再跳页）

从插件中心的"配置"按钮直接弹 Dialog，不再跳转到账号详情页：

```
点击"配置" → 弹出 Dialog：
┌─────────────────────────────────────┐
│ 插件配置 — weather                   │
│                                     │
│ ┌───────────────────────────────┐   │
│ │ 📦 全局配置                    │   │
│ │ API Key: [___________]        │   │
│ │ 默认城市: [Beijing___]         │   │
│ └───────────────────────────────┘   │
│                                     │
│ ┌───────────────────────────────┐   │
│ │ 👤 账号配置 (当前: 账号A)       │   │
│ │ 聊天 ID: [___________]         │   │
│ │ 启用天气: [✓]                   │   │
│ └───────────────────────────────┘   │
│                                     │
│ [保存] [取消]                       │
└─────────────────────────────────────┘
```

### 2.3 config_schema 扩展：两级 schema

```json
{
  "type": "object",
  "properties": {
    "api_key": {
      "type": "string",
      "title": "API Key",
      "level": "global"
    },
    "default_city": {
      "type": "string",
      "title": "默认城市",
      "default": "Beijing",
      "level": "global"
    },
    "target_chat_id": {
      "type": "string",
      "title": "目标聊天",
      "level": "account"
    }
  }
}
```

- `level: "global"` → 存插件级配置（跨账号共享）
- `level: "account"` → 存 rule.config（按账号隔离）
- 不写 level → 默认 account

### 2.4 插件开发指南补充

需要补到 PLUGIN-DEV-GUIDE.md 的规范：

**Manifest 必填字段确认清单：**
- [x] key — 唯一标识
- [x] display_name — 显示名
- [x] version — 语义化版本（如 1.0.0）
- [x] description — 功能描述
- [x] author — 作者
- [x] permissions — 权限声明
- [ ] config_schema — 可选但推荐，有配置的插件必须写

**内置插件 config_schema 补全：**

| 插件 | 需要补 config_schema |
|------|---------------------|
| game24 | 答题时间、奖金金额、最大参与人数 |
| scheduler | 默认 cron 表达式、通知方式 |
| translate | 默认目标语言、LLM provider |
| forward | ✅ 已有 |

---

## 3. 账号隔离 vs 复用的设计

### 3.1 隔离的场景（rule.config）
- 每个账号转发到不同聊天 → target_chat_id 按账号
- 每个账号的定时任务不同 → cron 表达式按账号

### 3.2 共享的场景（全局配置）
- API Key → 所有账号共用同一个
- 默认语言 → 所有账号共用
- 插件开关 → 按账号独立（已有 AccountFeature 表）

### 3.3 实现方式

```
全局配置存储：
  plugin_config 表（或 JSON 文件）
  key: plugin_key + config_key
  value: JSON value

账号配置存储：
  rule.config 字段（已有）
  key: rule.config.config_key
  value: JSON value
```

读取配置时优先级：
```
账号级 config > 插件全局 config > config_schema.default
```

---

## 4. 实施步骤

| 步骤 | 内容 |
|------|------|
| 1 | 补全 game24 / scheduler / translate 的 config_schema |
| 2 | config_schema 加 level 字段（global/account） |
| 3 | 前端配置弹窗（Dialog + JSON Schema 表单渲染） |
| 4 | 全局配置存储（plugin_config 表或 JSON 文件） |
| 5 | 插件级配置 + 账号级配置合并渲染 |
| 6 | 插件开发指南补充配置规范 |

---

## 5. 不做的事情

- 不改现有的 rule.config 存储结构
- 不改功能矩阵（已废弃）
- 不引入新的前端 UI 组件库
- 配置弹窗用现有 Dialog + 简单 form 实现
