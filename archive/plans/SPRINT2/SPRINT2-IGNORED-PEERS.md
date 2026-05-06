# Sprint 2 — Session #3：忽略群组（ignored_peers + 一键加入）

> 工时：约 1 天
> 依赖：无
> 优先级：与 #1、#2 并行启动

## 1. 目标

每个账号维护一份"忽略 peer 名单"。worker 收到来自这些 peer 的消息时**直接跳过所有插件 + 命令分发**（包括 auto_reply / forward / 命令），不消耗任何风控配额。

UX 重点：**一键加入**——账号详情新增"最近活跃会话"列表，每行右侧一个 "加入忽略" 按钮，点击即从加入到名单。同时也支持手填 peer_id。

## 2. 文件白名单

### 后端
- `backend/app/db/models/ignored_peer.py`（**新建**）—
  ```python
  class IgnoredPeer(Base):
      __tablename__ = "ignored_peer"
      id: Mapped[int] = mapped_column(primary_key=True)
      account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
      peer_id: Mapped[int]   # Telethon chat_id（可正可负）
      peer_kind: Mapped[str] = mapped_column(String(16))  # private/group/supergroup/channel
      peer_label: Mapped[str | None] = mapped_column(String(128))  # 群名/用户名快照
      added_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
      __table_args__ = (UniqueConstraint("account_id", "peer_id"),)
  ```
- `backend/app/db/models/__init__.py` — 加 import
- `backend/app/schemas/ignored_peer.py`（**新建**）
- `backend/app/api/ignored_peers.py`（**新建**）—
  - `GET /api/accounts/{aid}/ignored-peers` — 列表
  - `POST /api/accounts/{aid}/ignored-peers` body `{peer_id, peer_kind, peer_label}` — 加入
  - `DELETE /api/accounts/{aid}/ignored-peers/{id}` — 移除
  - `GET /api/accounts/{aid}/recent-peers` — 拉 worker 内存中的"最近 50 个活跃会话"（worker 维护一个 LRU dict）
- `backend/app/services/ignored_peer_service.py`（**新建**）
- `backend/app/worker/runtime.py` — 启动时加载 set + 维护 recent_peers LRU
- `backend/app/worker/plugins/loader.py` — `_dispatch` 入口先查 `if event.chat_id in ctx.ignored_peers: return`
- `backend/app/worker/command.py` — 同样在 outgoing 命令分发前判断（**注意**：命令默认是自己发自己看 outgoing，但忽略针对 incoming，所以**命令不需要忽略**；只在 incoming 路径生效）
- `backend/app/main.py` — 注册 router
- `backend/alembic/versions/0004_ignored_peer.py`（**新建迁移**）

### 前端
- `frontend/src/api/ignored_peers.ts`（**新建**）
- `frontend/src/api/types.ts` — 追加 `Sprint2 #3` 块
- `frontend/src/pages/Accounts/Detail.tsx` — 新增 "忽略" tab：上半最近活跃 + 加入按钮，下半已忽略列表 + 移除按钮
- `frontend/src/pages/Accounts/IgnoredTab.tsx`（**新建**，从 Detail.tsx 拆出来更干净）

### 不要动
auto_reply 内部、风控引擎核心、登录服务、其他插件。

## 3. 实现要点

### 3.1 数据流

```
worker 收 incoming msg
  ↓
runtime._dispatch (loader.py)
  ↓
if event.chat_id in ctx.ignored_peers:
    log.debug("[ignored] %s", event.chat_id)
    return     ←← 早退，省风控、省 plugin
  ↓
否则正常派发到 plugins
```

`ctx.ignored_peers` 是 `set[int]`，启动时从 DB 查出 + 监听 IPC `worker_cmd:{aid}:reload_ignored` 热加载。

### 3.2 Recent peers 维护

在 worker 主 NewMessage handler 入口：

```python
# runtime.py
ctx.recent_peers = collections.OrderedDict()  # peer_id → {kind, label, ts}
RECENT_LIMIT = 50

async def _on_message(event):
    pid = event.chat_id
    if pid:
        kind = "private" if event.is_private else (
            "channel" if event.is_channel and not event.is_group else
            ("supergroup" if str(pid).startswith("-100") else "group")
        )
        try:
            chat = await event.get_chat()
            label = getattr(chat, "title", None) or getattr(chat, "username", None) or str(pid)
        except Exception:
            label = str(pid)
        ctx.recent_peers[pid] = {"kind": kind, "label": label, "ts": time.time()}
        ctx.recent_peers.move_to_end(pid)
        while len(ctx.recent_peers) > RECENT_LIMIT:
            ctx.recent_peers.popitem(last=False)
    # 然后再走忽略检查 + plugin 派发
    if pid in ctx.ignored_peers:
        return
    await dispatch_plugins(...)
```

**注意**：recent_peers 是进程内存，重启 worker 后清空。前端不要假设它持久。

### 3.3 API：拉最近活跃

```python
@router.get("/{aid}/recent-peers")
async def list_recent(aid: int):
    # 通过 IPC 向 worker 请求，超时 1.5s
    payload = await ipc.request(f"worker_cmd:{aid}:get_recent_peers", timeout=1.5)
    if payload is None:  # worker 离线
        return []  # 前端显示"worker 未运行"
    return payload  # [{peer_id, kind, label, ts}]
```

`ipc.request` 在 Sprint 1 的 `worker/ipc.py` 已有；如没有则用 `pub` + 临时 `sub` reply channel 实现一次 RPC。worker 端：

```python
# runtime.py
async def _on_get_recent_peers(_payload):
    items = [{"peer_id": k, **v} for k, v in reversed(ctx.recent_peers.items())]
    return items
ipc.register_handler(f"worker_cmd:{account_id}:get_recent_peers", _on_get_recent_peers)
```

### 3.4 前端：忽略 tab

```tsx
// IgnoredTab.tsx
export function IgnoredTab({ aid }: { aid: number }) {
  const recentQ = useQuery({
    queryKey: ["recent-peers", aid],
    queryFn: () => listRecentPeers(aid),
    refetchInterval: 5_000,
  });
  const ignoredQ = useQuery({
    queryKey: ["ignored", aid],
    queryFn: () => listIgnored(aid),
  });
  const ignoredSet = new Set(ignoredQ.data?.map(x => x.peer_id) ?? []);

  const addMut = useMutation({ mutationFn: addIgnored, onSuccess: ... });
  const delMut = useMutation({ mutationFn: removeIgnored, onSuccess: ... });

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>最近活跃会话</CardTitle>
          <CardDescription>worker 内存中最近 50 个 incoming 会话；重启后清空</CardDescription>
        </CardHeader>
        <CardContent>
          {recentQ.data?.map(p => (
            <li className="flex items-center justify-between py-2 border-b">
              <div>
                <div className="text-sm">{p.label}</div>
                <div className="text-xs text-muted-foreground">
                  {p.kind} · ID {p.peer_id} · {timeAgo(p.ts)}
                </div>
              </div>
              {ignoredSet.has(p.peer_id) ? (
                <Badge variant="outline">已忽略</Badge>
              ) : (
                <Button size="sm" variant="outline"
                        onClick={() => addMut.mutate({ aid, peer_id: p.peer_id, peer_kind: p.kind, peer_label: p.label })}>
                  加入忽略
                </Button>
              )}
            </li>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>已忽略会话</CardTitle>
          <CardDescription>这些会话的所有 incoming 消息将被丢弃，不触发任何插件/命令</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2 mb-3">
            <Input placeholder="手动输入 peer_id 加入" value={manualId} onChange=... />
            <Button onClick={addManual}>加入</Button>
          </div>
          {ignoredQ.data?.map(x => (
            <li className="flex items-center justify-between py-2 border-b">
              <div>
                <div className="text-sm">{x.peer_label || `(未命名)`}</div>
                <div className="text-xs text-muted-foreground">{x.peer_kind} · ID {x.peer_id}</div>
              </div>
              <Button size="sm" variant="ghost" className="text-destructive"
                      onClick={() => delMut.mutate({ aid, id: x.id })}>
                移除
              </Button>
            </li>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
```

加入后立刻 invalidate `["ignored", aid]` 和 `["recent-peers", aid]`，并发 IPC 推送 `worker_cmd:{aid}:reload_ignored`。

## 4. 验收清单

```bash
cd backend && alembic upgrade head && pytest -v && ruff check .
cd frontend && pnpm run build

# 浏览器手测
# 1. 配 auto_reply 关键词 "ping" → "pong"
# 2. 在群 A 测试 ping → 收到 pong
# 3. 账号详情 → 忽略 tab → 最近活跃应该看到群 A → 点"加入忽略"
# 4. 再发 ping → 不再回复（worker 日志应有 [ignored] chat_id=-100xxx）
# 5. 移除忽略 → 再发 ping → 恢复回复
# 6. worker 重启后 ignored 持久化（DB），recent_peers 清空
```

## 5. 完成报告模板

```markdown
## 完成报告 — Session #3

- [x] IgnoredPeer 模型 + 迁移 0004_ignored_peer.py
- [x] CRUD API + 一键加入端点
- [x] worker recent_peers LRU + IPC RPC 拉取
- [x] loader._dispatch 入口忽略短路
- [x] 前端"忽略" tab：双卡片（最近活跃 / 已忽略）
- [x] 命令路径不受影响（验证 outgoing ,help 仍可用）
- 改动文件：XX 个
- 测试：pytest XX passed
- 已知遗留：（如 worker 离线时 recent_peers 显示空，需要后续考虑磁盘缓存）
```

---

## 完成报告 — Session #3（2026-05-03 实际交付）

### 验收清单
- [x] `IgnoredPeer` 模型（`backend/app/db/models/ignored_peer.py`）+ 迁移
  `0004_ignored_peer.py`（chain off 0003，head 单一）
- [x] CRUD API（`backend/app/api/ignored_peers.py`）：
  - `GET /api/accounts/{aid}/ignored-peers`
  - `POST /api/accounts/{aid}/ignored-peers`（幂等：UNIQUE 命中即返回原行）
  - `DELETE /api/accounts/{aid}/ignored-peers/{id}`
  - `GET /api/accounts/{aid}/recent-peers`
- [x] 业务层（`backend/app/services/ignored_peer_service.py`）含 IPC RPC 实现
  （主进程订阅一次性 `worker_reply:{aid}:recent_peers:{nonce}`，1.5s 超时）
- [x] IPC 协议扩展：`CMD_RELOAD_IGNORED` + `CMD_GET_RECENT_PEERS`
  （`backend/app/worker/ipc.py`）
- [x] worker runtime 处理 reload + RPC 应答（`backend/app/worker/runtime.py`）
- [x] plugin loader 内 `_AccountState.ignored_peers` set + `recent_peers`
  OrderedDict（cap=50）；`_dispatch` 在维护 LRU 之后、派发之前查 set 短路
  （`backend/app/worker/plugins/loader.py`）
- [x] 前端：
  - `frontend/src/api/types.ts` 追加 `IgnoredPeer / IgnoredPeerCreate /
    RecentPeerItem / PeerKind` 块（仅追加，未动他人）
  - `frontend/src/api/ignored_peers.ts` 新建
  - `frontend/src/pages/Accounts/IgnoredTab.tsx` 新建
  - `frontend/src/pages/Accounts/Detail.tsx` 追加"忽略"tab（与 #2 commands
    tab 共存无冲突）
- [x] 命令路径不受影响：忽略只在 incoming `_dispatch` 入口生效，命令派发使用
  `events.NewMessage(outgoing=True)`，路径完全独立
- [x] `app/main.py` 注册 router：`app.include_router(ignored_peers_api.router)`

### 改动 / 新建文件

**后端（10 个）**
1. `backend/app/db/models/ignored_peer.py`（新建）
2. `backend/app/db/models/__init__.py`（追加导出）
3. `backend/alembic/versions/0004_ignored_peer.py`（新建）
4. `backend/app/schemas/ignored_peer.py`（新建）
5. `backend/app/services/ignored_peer_service.py`（新建）
6. `backend/app/api/ignored_peers.py`（新建）
7. `backend/app/main.py`（注册 router 一行）
8. `backend/app/worker/ipc.py`（追加 `CMD_RELOAD_IGNORED` /
   `CMD_GET_RECENT_PEERS`）
9. `backend/app/worker/runtime.py`（IPC dispatch 增两个 elif）
10. `backend/app/worker/plugins/loader.py`(
    state 字段 + `_dispatch` 短路 + `_load_ignored_peers` /
    `_record_recent_peer` / `reload_ignored_peers` / `get_recent_peers`)

**前端（4 个）**
1. `frontend/src/api/types.ts`（追加 Sprint2 #3 类型块）
2. `frontend/src/api/ignored_peers.ts`（新建）
3. `frontend/src/pages/Accounts/IgnoredTab.tsx`（新建，双卡片 UI）
4. `frontend/src/pages/Accounts/Detail.tsx`（追加 tab）

**测试（1 个）**
1. `backend/app/tests/test_ignored_peers.py`（新建，11 用例）

### 测试与验证

```
backend:  pytest -v                 → 115 passed, 2 skipped
backend:  ruff check (我的文件)     → All checks passed
backend:  alembic heads             → 0004 (single head)
frontend: pnpm run build            → ✓ built in 6.11s
frontend: tsc --noEmit              → 静默通过（无 TS 错误）
```

新增 11 个针对 ignored peers / recent peers 行为的单元测试覆盖：
- `IgnoredPeerCreate.normalized_kind` 异常 kind 归一化
- `_record_recent_peer` LRU 上限、move_to_end、kind 分类
- `get_recent_peers` 反向输出（最新在前），未运行 worker 返回空
- `reload_ignored_peers` 从 DB 重拉名单 + 无 state 时静默
- `_dispatch` 在忽略命中时不调用插件、recent_peers 仍记录
- `_dispatch` 不命中时正常派发到插件

### 关键设计点

1. **IPC RPC 实现**：原 `ipc.py` 没有 request/reply 模式，本会话用「主进程订阅
   一次性 reply 频道 + worker publish 回 reply 频道」的方式实现 1.5s 超时
   RPC，复用现有 `worker_cmd:{aid}` 通道分发请求。`reply_to` 频道名包含
   `secrets.token_hex(8)` 防碰撞。
2. **顺序约束**：`_dispatch` 入口先调 `_record_recent_peer`（保证用户能在
   "最近活跃"看到所有进过来的 peer，包括已忽略的），再查 ignored set 决定是
   否短路。这样 UI 上的"已忽略"badge 仍能持续刷新。
3. **命令隔离**：通过 `events.NewMessage(outgoing=True)` 注册的命令派发与
   `events.NewMessage(incoming=True)` 的 `_dispatch` 是两个独立 handler，
   忽略名单逻辑完全不会影响 `,help` / `,id` / `,status` 等命令。
4. **写后通知**：所有写 API 在 `db.commit()` **之后**才发 IPC，避免 worker 拉到
   尚未提交的视图。
5. **worker 离线回退**：RPC 超时 → API 返回 `[]`；前端按"暂无最近活跃会话"
   提示，并在描述里说明 worker 离线/刚启动场景。

### 已知遗留

- `recent_peers` 是进程内存维护，worker 重启后清空（plan 已注明，UI 描述里也
  做了提示）。如未来需要持久化，可考虑落到 Redis hash + TTL（每个账号一份）。
- 同一 chat_id 加入忽略后，"最近活跃"卡片仍会显示该会话（带"已忽略" badge），
  这是有意保留的——便于用户随时反向移除忽略。
- `peer_label` 在加入时为 `event.get_chat()` 快照；用户后续在 TG 改群名不会
  自动同步。如需要，可在 `_record_recent_peer` 同步刷新已忽略行的 label。

