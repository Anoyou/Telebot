# Agent A：认证 + 账号 API + 登录状态机

> 你是新会话里的工程师。**先读完整份 plan**，再读引用的只读文件，然后开干。注释一律中文。

## 目标

实现 Web 端登录认证、TOTP、账号 CRUD、Telethon 多步登录绑定向导，以及 FastAPI 入口与 Alembic 初始迁移。

## 项目根目录

`/Users/anoyou/Desktop/telebot`

## 必读（只读，禁止修改）

1. `/Users/anoyou/Desktop/telebot/teleuserbot.md`（PRD：§3 实体、§4-A 账号管理、§9.1-9.2 接口、§9.6 系统）
2. `/Users/anoyou/Desktop/telebot/CONTRACTS.md`（契约总览，必读全部）
3. `/Users/anoyou/Desktop/telebot/backend/app/db/models/`（全部 14 张表已就绪）
   - `account.py` 含 `Account` / `Proxy` / `HumanizeConfig` 与状态常量
   - `user.py` 含 `WebUser`
   - `feature.py` 含 `Feature` / `AccountFeature`
   - `rate_limit.py` 含 `RateLimitTemplate` / `RateLimitRule` / `RateLimitEvent` / `RateLimitOverride`
   - `rule.py` 含 `Rule`
   - `log.py` 含 `AuditLog` / `RuntimeLog`
   - `plugin.py` 含 `PluginRepo` / `PluginAvailable`
   - `system.py` 含 `SystemSetting` / `NotificationChannel`

## 要写的文件（白名单）

### 核心入口
- `backend/app/main.py`：FastAPI app + 全局异常处理 + CORS + 路由挂载
- `backend/alembic/versions/0001_init.py`：初始建表迁移

### 认证服务层
- `backend/app/services/auth_service.py`：Web 登录 / TOTP / JWT 签发校验
- `backend/app/services/login_service.py`：Telethon 多步登录状态机
- `backend/app/services/account_service.py`：账号 CRUD + 暂停恢复 + 复制配置

### REST API
- `backend/app/api/auth.py`：`/api/auth/*` 登录 / 登出 / 刷新 token / TOTP
- `backend/app/api/accounts.py`：`/api/accounts/*` 账号 CRUD + 绑定向导

### 依赖注入
- `backend/app/deps.py`：`CurrentUser` / `DBSession` 依赖（已有骨架，需完善）

## 关键 Telethon API

```python
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, 
    PasswordHashInvalidError, PhoneNumberInvalidError
)

# 1. 创建临时客户端（绑定向导用）
client = TelegramClient(StringSession(), api_id, api_hash)
await client.connect()

# 2. 发送验证码
await client.send_code_request(phone)

# 3. 提交验证码
try:
    await client.sign_in(phone, code)
except SessionPasswordNeededError:
    # 需要 2FA 密码
    pass

# 4. 提交 2FA 密码
await client.sign_in(password=password)

# 5. 序列化 session
session_str = client.session.save()

# 6. 获取用户信息
me = await client.get_me()
# me.id, me.username, me.first_name, me.last_name
```

## 自检命令

```bash
cd /Users/anoyou/Desktop/telebot/backend
python -m pytest app/tests/test_auth.py -v
python -m pytest app/tests/test_accounts.py -v
curl -X POST http://localhost:8000/api/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}'
```

## 汇报格式

```
✅ Agent A 完工汇报

实现功能：
- [x] FastAPI 主入口 + 全局异常处理
- [x] Web 登录认证 + TOTP 二次验证
- [x] JWT token 签发与校验
- [x] 账号 CRUD API
- [x] Telethon 三步绑定向导
- [x] Alembic 初始迁移

自检结果：
- 测试通过：X/Y
- API 可访问：✅/❌
- 数据库迁移：✅/❌

待其他 Agent：
- B 需要 account_service.list_accounts() 来拉起 worker
- C 需要 deps.CurrentUser 来做权限检查
```