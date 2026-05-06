# Agent E：前端（React + Vite + TypeScript）

> 你是新会话里的工程师。注释一律中文。

## 目标

实现 PRD §5（页面结构）里 MVP 范围的前端页面：
- 登录页（含 TOTP 二次输入）
- Dashboard（多账号状态卡 + 紧急停用按钮）
- 账号管理：列表 / 4 步绑定向导 / 账号详情（概览 + 功能开关 + 风控基础）
- 功能矩阵
- 自动回复配置页
- 日志查看
- 系统设置（命令前缀、kill switch）

## 项目根目录

`/Users/anoyou/Desktop/telebot`

## 必读（只读）

1. `/Users/anoyou/Desktop/telebot/teleuserbot.md`（**重点 §5 信息架构、§6 流程、§7 ASCII 线框稿、§9 REST API**）
2. `/Users/anoyou/Desktop/telebot/CONTRACTS.md`
3. `/Users/anoyou/Desktop/telebot/backend/app/schemas/`（所有 schema 决定了前端的类型）

## 你的可写文件白名单（整个 frontend/ 目录）

- `frontend/package.json`、`pnpm-lock.yaml` 之类（你建）
- `frontend/vite.config.ts`、`tsconfig.json`、`tsconfig.node.json`
- `frontend/index.html`
- `frontend/tailwind.config.ts`、`postcss.config.cjs`
- `frontend/src/main.tsx`、`src/App.tsx`、`src/index.css`
- `frontend/src/lib/{api.ts, auth.ts, utils.ts}`
- `frontend/src/api/`（手写或用 openapi-typescript 生成的 schema 类型）
- `frontend/src/components/layout/{AppShell.tsx, Sidebar.tsx, TopBar.tsx, KillSwitch.tsx}`
- `frontend/src/components/ui/`（必要的 shadcn 组件 button/input/card/dialog/table/badge/switch/tabs/form 等）
- `frontend/src/pages/Login.tsx`
- `frontend/src/pages/Dashboard.tsx`
- `frontend/src/pages/Accounts/{List.tsx, Wizard.tsx, Detail.tsx}`
- `frontend/src/pages/FeatureMatrix.tsx`
- `frontend/src/pages/Features/AutoReply.tsx`（其他 4 个 feature 留 TODO 页）
- `frontend/src/pages/Logs.tsx`
- `frontend/src/pages/Settings/{Index.tsx}`

**禁止修改**：`backend/`、根目录 `Makefile`/`docker-compose.dev.yml`/`.env.example`（已就绪）。

## 技术选型（必须严格遵守）

```json
{
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "react-router-dom": "^6.26.0",
    "@tanstack/react-query": "^5.51.0",
    "axios": "^1.7.0",
    "zod": "^3.23.0",
    "react-hook-form": "^7.52.0",
    "@hookform/resolvers": "^3.9.0",
    "lucide-react": "^0.408.0",
    "clsx": "^2.1.0",
    "tailwind-merge": "^2.4.0",
    "class-variance-authority": "^0.7.0",
    "@radix-ui/react-dialog": "^1.1.0",
    "@radix-ui/react-tabs": "^1.1.0",
    "@radix-ui/react-switch": "^1.1.0",
    "@radix-ui/react-label": "^2.1.0",
    "@radix-ui/react-slot": "^1.1.0",
    "@radix-ui/react-dropdown-menu": "^2.1.0",
    "sonner": "^1.5.0",
    "echarts": "^5.5.0"
  },
  "devDependencies": {
    "vite": "^5.4.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.5.0",
    "tailwindcss": "^3.4.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "openapi-typescript": "^7.0.0"
  },
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "codegen": "openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.ts"
  }
}
```

## 关键约定

### 鉴权

- 后端 cookie：`auth_token` (HttpOnly, SameSite=Lax)
- axios `withCredentials: true`
- 401 自动跳 `/login`
- TOTP：`POST /api/auth/login` 返 `{ require_totp: true }` 时跳出第二步输入框

### API 客户端骨架

```ts
// src/lib/api.ts
import axios from "axios";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || "http://localhost:8000",
  withCredentials: true,
  timeout: 15000,
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && !location.pathname.startsWith("/login")) {
      location.href = "/login";
    }
    return Promise.reject(err);
  },
);
```

错误响应形态固定 `{"error": {"code": "...", "message": "..."}}`，包一个 helper：

```ts
export function getErrMsg(err: any): string {
  return err?.response?.data?.error?.message || err?.message || "请求失败";
}
```

### 路由

```tsx
<Routes>
  <Route path="/login" element={<Login />} />
  <Route element={<RequireAuth />}>
    <Route element={<AppShell />}>
      <Route index element={<Dashboard />} />
      <Route path="accounts">
        <Route index element={<AccountList />} />
        <Route path="new" element={<AccountWizard />} />
        <Route path=":aid" element={<AccountDetail />} />
      </Route>
      <Route path="matrix" element={<FeatureMatrix />} />
      <Route path="accounts/:aid/features/auto_reply" element={<AutoReplyConfig />} />
      <Route path="logs" element={<Logs />} />
      <Route path="settings" element={<SettingsIndex />} />
    </Route>
  </Route>
</Routes>
```

`RequireAuth`：调一次 `GET /api/auth/me`，401 跳 `/login`。

## 实施步骤

### 1. 项目初始化

```bash
cd /Users/anoyou/Desktop/telebot/frontend
# 用 vite 模板（手写更可控；不要 pnpm create vite，因为目录已有内容）
```

直接手写 `package.json` / `vite.config.ts` / `tailwind.config.ts` / `index.html` / `tsconfig*`。

vite.config.ts 要配代理：
```ts
server: {
  port: 5173,
  proxy: {
    "/api": "http://localhost:8000",
  },
},
```

### 2. shadcn 组件最小集

不依赖 shadcn CLI，**手写**这些组件（每个 ≤ 80 行）：
- `Button`（variant: default / outline / ghost / destructive）
- `Input` / `Textarea` / `Label`
- `Card` / `CardHeader` / `CardTitle` / `CardContent`
- `Dialog`（用 @radix-ui/react-dialog）
- `Tabs` / `TabsList` / `TabsTrigger` / `TabsContent`
- `Switch`
- `Badge`
- `Table`（简单 thead/tbody 组件）
- `DropdownMenu`
- `cn` utility（clsx + tailwind-merge）

### 3. 登录页

```
┌─ 登录 ──────────────┐
│ 用户名 [____]       │
│ 密码   [____]       │
│ TOTP   [____] ★     │  ← require_totp=true 时显示
│  [ 登录 ]           │
└────────────────────┘
```

如果是首次部署（`POST /login` 返 `{"error":{"code":"NO_USER",...}}` 之类），跳转**注册页**让用户创建第一个账号。

### 4. Dashboard

- 顶部一行：N 个账号卡片（头像/姓名/状态徽章/在线时长）
- 中部：今日消息数、规则触发数、错误数（折线图，echarts）
- 右侧：最近告警 + 操作日志摘要
- 顶栏：`紧急停用` 按钮（红色），点击后调 `POST /api/system/kill-switch`

### 5. 账号管理

#### 列表

table，列：头像、用户名、电话、状态、启用功能数、绑定时间、操作（启停/详情/删除）

#### 4 步绑定向导

```
Step 1: API ID + API Hash + 手机号 + （可选）代理
   ↓ POST /api/accounts/login/start
Step 2: 输入验证码
   ↓ POST /api/accounts/login/code
Step 3: （仅 require_2fa 时）输入两步密码
   ↓ POST /api/accounts/login/2fa
Step 4: 完成 → 弹出"是否复制其他账号配置？" → 完成
```

实现要点：
- 步进条 + 表单分步显示
- `login_token` 存在组件 state（不要进 localStorage）
- 错误码 `CODE_INVALID/CODE_EXPIRED/PASSWORD_INVALID/FLOOD_WAIT/PHONE_INVALID` 都给中文提示

#### 详情

3 tabs：
- **概览**：基本信息 + 启停按钮 + 删除（二次确认）
- **功能开关**：5 行（auto_reply/forward/group_admin/scheduler/monitor）每行一个 Switch，开启后跳到对应配置页
- **风控**：MVP 只做最简形态：列出当前账号有效的 RateLimitRule 表（GET /api/accounts/{aid}/rate-limit），允许编辑 PER_minute；`紧急调严` 按钮（POST .../strict）

### 6. 功能矩阵

table，行=账号，列=功能，格子：✓（active 绿）、✗（disabled 灰）、⚠（failed 红）。点击格子打开浮层「为账号 X 启用功能 Y」+ 「从其他账号复制规则」。

### 7. 自动回复配置页

URL: `/accounts/:aid/features/auto_reply`

- 顶部一个 Toggle（启用/禁用整个功能）
- 列表展示该 [aid × auto_reply] 下所有 rule
- 「+ 新建规则」按钮 → 打开 Dialog 表单：
  - 名称
  - 启用 / 优先级
  - 匹配类型（关键词 / 正则）
  - 模式（多个，分行）
  - 作用范围（私聊 / 全部群 / 指定群）
  - 回复内容（支持变量 `{sender}` `{chat}` `{text}`）
  - 冷却秒数
  - 白名单 / 黑名单（可选）
  - case sensitive
- 每条 rule 行：编辑 / 删除 / **试运行** Dialog（输入样例消息 + 私/群类型 → 调 `POST .../{rid}/dry-run` 显示是否命中 + 渲染输出）

保存后调 `POST/PATCH /api/accounts/{aid}/features/auto_reply/rules`。

### 8. 日志页

简单 table：filter 账号 / level；调 `GET /api/logs/runtime?account_id=&level=&since=`。auto-refresh 每 5s。

### 9. 系统设置

只做最小：
- 命令前缀（input + 保存按钮，调 `PATCH /api/system/settings`）
- 全局 kill switch（toggle）
- 全局每秒 API 上限（input）
- 风控模板（list + 新建/编辑，跳到模板编辑子页 — MVP 可只列表）

### 10. 类型同步

启动后端后跑 `pnpm codegen` 把 OpenAPI 拉成 `src/api/schema.ts`。MVP 阶段也可以**手写关键类型**到 `src/api/types.ts`：
```ts
export interface AccountSummary {
  id: number;
  phone: string;
  display_name: string | null;
  status: "active"|"paused"|"floodwait"|"dead"|"login_required";
  enabled_features: number;
  cold_start_until: string | null;
  created_at: string;
}
// ...
```

## TanStack Query 用法约定

每个页面用 query key 数组：`["accounts"]`、`["account", aid]`、`["account", aid, "features"]`、`["matrix"]`、`["account", aid, "rules", "auto_reply"]`、`["logs", filters]`。

mutation 后 `qc.invalidateQueries({ queryKey: [...] })`。

## 自检

```bash
cd /Users/anoyou/Desktop/telebot/frontend
pnpm install
pnpm build       # 类型检查 + 构建（真正能跑通）
```

## 完成报告

≤300 字总结：建立的页面/组件清单、依赖列表、TODO（如：风控仪表盘环形图、ECharts 大屏、矩阵浮层）。
