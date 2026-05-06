# Agent C：风控引擎

> 你是新会话里的工程师。**先读完整份 plan**，再读引用的只读文件，然后开干。注释一律中文。

## 目标

实现细粒度风控引擎：
- 按动作分桶的令牌桶算法
- 三层叠加（全局 ≥ 账号 ≥ 规则）
- 5 种抑制策略（drop / queue / backoff / pause / notify）
- FloodWait/PeerFlood/SlowMode 自动响应
- 拟人化行为（随机延迟、打字模拟等）
- 冷启动渐进

## 项目根目录

`/Users/anoyou/Desktop/telebot`

## 必读（只读，禁止修改）

1. `/Users/anoyou/Desktop/telebot/teleuserbot.md`（PRD：§4-L 风控与限流，重点读 L.1-L.7）
2. `/Users/anoyou/Desktop/telebot/CONTRACTS.md`（契约总览，重点读限速接口部分）
3. `/Users/anoyou/Desktop/telebot/backend/app/db/models/rate_limit.py`（风控相关表）
4. `/Users/anoyou/Desktop/telebot/backend/app/db/models/account.py`（HumanizeConfig）

## 要写的文件（白名单）

### 核心引擎
- `backend/app/worker/ratelimit/engine.py`：RateLimitEngine 主类 + acquire 方法
- `backend/app/worker/ratelimit/buckets.py`：令牌桶算法实现
- `backend/app/worker/ratelimit/exceptions.py`：风控异常类

### 高级特性
- `backend/app/worker/ratelimit/humanize.py`：拟人化行为实现
- `backend/app/worker/ratelimit/overrides.py`：临时覆盖管理（FloodWait 响应）

## 动作类型（PRD §4-L.1）

```python
ACTIONS = {
    "send_message_private": (1, 20, 500),      # 秒/分/时
    "send_message_group": (1, 30, 1000),
    "same_peer_send": (None, 3, None),         # 同会话防刷屏
    "edit_message": (None, 5, None),
    "delete_message": (None, 30, None),
    "forward_message": (None, 20, None),
    "callback_query": (None, 6, 60),
    "read_history": (None, 30, None),
    "join_chat": (None, None, 5, 20),          # 时/天
    "leave_chat": (None, None, 5),
    "create_chat": (None, None, None, 2),      # 天
    "invite_user": (None, None, 10, 50),       # 时/天
    "dm_stranger": (None, None, 3, 20),        # 最危险
    "update_profile": (None, None, 3),
    "upload_file": (None, 5, None),
    "download_file": (None, 10, None),
    "search": (None, 10, None),
    "api_total": (30, 1000, None),             # 兜底
}
```

## 抑制策略（PRD §4-L.2）

```python
class RateLimitDecision:
    allowed: bool
    wait_seconds: float
    outcome: str  # "ok" | "drop" | "queued" | "backoff" | "pause"
    reason: str | None = None
```

## 自检命令

```bash
cd /Users/anoyou/Desktop/telebot/backend
python -m pytest app/tests/test_ratelimit_engine.py -v
python -m pytest app/tests/test_ratelimit_buckets.py -v

# 手动测试
python -c "
from app.worker.ratelimit.engine import RateLimitEngine
import asyncio
async def test():
    engine = RateLimitEngine()
    decision = await engine.acquire(1, 'send_message_group')
    print(f'Decision: {decision.outcome}, wait: {decision.wait_seconds}s')
asyncio.run(test())
"
```

## 汇报格式

```
✅ Agent C 完工汇报

实现功能：
- [x] RateLimitEngine 核心引擎
- [x] 令牌桶算法（多时间窗口）
- [x] 三层叠加策略
- [x] 5 种抑制策略
- [x] FloodWait 自适应响应
- [x] 拟人化行为
- [x] 冷启动渐进

自检结果：
- 测试通过：X/Y
- 引擎可调用：✅/❌
- 限流生效：✅/❌

待其他 Agent：
- B 在 worker 中调用 engine.acquire()
- D 在插件中使用 @rate_limited 装饰器
- A 提供风控配置 API
```