# Code Review — Frontend + Deployment

## Project
React + TypeScript + Vite + Tailwind + shadcn/ui. PWA-enabled. Manages TG userbot accounts via REST API.

## Review Focus
1. **State management**: React Query usage patterns, cache invalidation
2. **Component quality**: reusability, error boundaries, loading states
3. **Security**: auth flow, cookie handling, input sanitization
4. **Build/deploy**: Docker multi-stage build, nginx config, Makefile
5. **PWA**: service worker, offline support

## Output Format
For each finding: **Severity** (Critical/Major/Minor/Suggestion) + **File:Line** + **Description** + **Fix**

---

```yaml
===== docker-compose.yml =====
# 生产部署：postgres + redis + web (FastAPI + supervisor) + frontend (nginx)
# 使用方式：
#   1. cp .env.example .env  并填入 MASTER_KEY / JWT_SECRET 等
#   2. docker compose up -d --build
# 说明：
#   - web 容器内含 supervisor，统一管理每账号的 worker 子进程，故 uvicorn 必须 --workers 1
#   - 前端 nginx 通过容器名 web:8000 反代后端，无需暴露后端端口到宿主机

services:
  # ── PostgreSQL：主数据存储 ────────────────────────────────────
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-telebot}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-telebot}
      POSTGRES_DB: ${POSTGRES_DB:-telebot}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER:-telebot} -d $${POSTGRES_DB:-telebot}"]
      interval: 10s
      timeout: 3s
      retries: 10

  # ── Redis：IPC pub/sub + 限速令牌桶 ───────────────────────────
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 10

  # ── Web：FastAPI + supervisor，主进程内拉起每账号 worker 子进程 ─
  web:
    build:
      context: ./backend
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file: .env
    environment:
      # 容器内连接服务名（postgres / redis），覆盖 .env 中本机地址
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-telebot}:${POSTGRES_PASSWORD:-telebot}@postgres:5432/${POSTGRES_DB:-telebot}
      REDIS_URL: redis://redis:6379/0
      WEB_HOST: 0.0.0.0
      WEB_PORT: "8000"
    # 让容器内能用 ``host.docker.internal`` 访问宿主机（Mac/Windows Docker Desktop
    # 自带；Linux 需要这一行兜底）。如 .env 里 TG_DEFAULT_PROXY=socks5://host.docker.internal:1080
    # 即可让容器透过宿主机的本地代理出去。
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      # session 加密文件落盘目录，volume 持久化
      - sessions:/app/sessions
    healthcheck:
      # 用 /readyz 做真实探活：DB + Redis 任一异常即视为 unhealthy
      # urlopen 显式 timeout=4：让应用层超时先于 docker 的 5s 触发，错误更清晰
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/readyz', timeout=4).read()"]
      interval: 15s
      timeout: 5s
      retries: 5
    # 启动顺序：先跑 alembic 迁移再启 uvicorn
    # ⚠ 必须 --workers 1：supervisor 在主进程内管子进程，多 worker 会重复拉起 worker 子进程
    command: ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]

  # ── Frontend：nginx 静态托管 + 反代后端 ──────────────────────
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    restart: unless-stopped
    depends_on:
      - web
    ports:
      # 对外发布端口，可在 .env 中通过 WEB_PORT_PUBLISH 覆盖（默认 80）
      - "${WEB_PORT_PUBLISH:-80}:80"

# ── 持久化卷 ──────────────────────────────────────────────────
volumes:
  pgdata:
  redisdata:
  sessions:

===== docker-compose.dev.yml =====
# 仅启动本地依赖（PostgreSQL + Redis），后端/前端用本机 venv + pnpm 跑
# 使用： docker compose -f docker-compose.dev.yml up -d
version: "3.9"

services:
  postgres:
    image: postgres:16-alpine
    container_name: telebot-postgres
    environment:
      POSTGRES_USER: telebot
      POSTGRES_PASSWORD: telebot
      POSTGRES_DB: telebot
    ports:
      - "5432:5432"
    volumes:
      - telebot-pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U telebot -d telebot"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    container_name: telebot-redis
    ports:
      - "6379:6379"
    volumes:
      - telebot-redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  telebot-pgdata:
  telebot-redisdata:
```

```makefile
===== Makefile =====
# Telegram Userbot 管理系统 — 项目级 Makefile
# 一键命令：
#   make up          一键开发启动（pg + redis + 后端 + 前端）★最常用
#   make down        一键停止
#   make logs        实时跟踪后端 + 前端日志
#   make status      四组件状态总览
#   make prod-up     一键生产部署（纯 docker compose）
#   make nuke        彻底清理（删数据 + venv + node_modules + .env）
#   make help        全部命令清单

.PHONY: help up down restart logs status nuke bootstrap \
        dev-up dev-down dev-logs install migrate makemigration backend frontend \
        test lint codegen build prod-build prod-up prod-down backup clean

PYTHON := python3.12
VENV := backend/.venv
ACTIVATE := . $(VENV)/bin/activate

help:
	@echo "════════════ 一键命令（推荐） ════════════"
	@echo "  make up          ★ 一键启动开发环境（首次会自动 bootstrap）"
	@echo "  make down          一键停止开发环境（保留数据）"
	@echo "  make restart       ★ 改完代码后一键重启（down + up；确定性新代码）"
	@echo "  make logs          跟踪后端+前端日志（Ctrl+C 退出 tail）"
	@echo "  make logs be|fe|db 单独看某个组件日志"
	@echo "  make status        四组件状态总览"
	@echo "  make prod-up       一键生产部署（纯 docker compose 4 容器）"
	@echo "  make prod-down     停止生产栈"
	@echo "  make nuke          ⚠ 彻底清理（含数据库）"
	@echo ""
	@echo "════════════ 细粒度命令 ════════════"
	@echo "  make bootstrap     仅初始化环境（venv + .env + pnpm install）"
	@echo "  make dev-up        仅启动 pg + redis 容器"
	@echo "  make dev-down      仅停止 pg + redis 容器"
	@echo "  make dev-logs      跟踪 pg + redis 容器日志"
	@echo "  make install       重装后端 + 前端依赖（已有 venv 也会更新）"
	@echo "  make migrate       手动跑 alembic upgrade head"
	@echo "  make makemigration m='describe'   生成新迁移"
	@echo "  make backend       前台跑 uvicorn（不后台、不写 PID）"
	@echo "  make frontend      前台跑 vite"
	@echo "  make test          后端 pytest"
	@echo "  make lint          ruff check"
	@echo "  make codegen       OpenAPI → 前端类型"
	@echo "  make backup        备份脚本（pg_dump + sessions 卷）"
	@echo "  make clean         清 caches / .venv / node_modules（不删数据卷）"

# ════════════════════════════════════════════
# 一键命令（脚本驱动）
# ════════════════════════════════════════════
up:
	@./scripts/up.sh

down:
	@./scripts/down.sh

# 改完代码后必须用这个——只重启 backend uvicorn 不会让 worker 子进程拿新代码
# （它们是 multiprocessing.spawn 出来的独立 Python 进程，跟 uvicorn --reload 无关）。
# restart = down（清光所有 telebot 进程，含孤儿 worker）+ up（拉新进程）。
restart:
	@./scripts/down.sh
	@./scripts/up.sh

logs:
	@./scripts/logs.sh $(filter-out $@,$(MAKECMDGOALS))

status:
	@./scripts/status.sh

bootstrap:
	@./scripts/bootstrap.sh

prod-up:
	@./scripts/prod-up.sh

prod-down:
	docker compose down

nuke:
	@./scripts/nuke.sh

# 让 `make logs be` 这种"接位置参数"不报错
%:
	@:

# ════════════════════════════════════════════
# 细粒度（保留旧 target 不破坏既有用法）
# ════════════════════════════════════════════
dev-up:
	docker compose -f docker-compose.dev.yml up -d

dev-down:
	docker compose -f docker-compose.dev.yml down

dev-logs:
	docker compose -f docker-compose.dev.yml logs -f --tail=100

install:
	cd backend && $(PYTHON) -m venv .venv && $(ACTIVATE) && pip install -U pip && pip install -e .[dev]
	cd frontend && pnpm install

migrate:
	cd backend && $(ACTIVATE) && alembic upgrade head

makemigration:
	cd backend && $(ACTIVATE) && alembic revision --autogenerate -m "$(m)"

backend:
	cd backend && $(ACTIVATE) && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd frontend && pnpm dev

test:
	cd backend && $(ACTIVATE) && pytest -v

lint:
	cd backend && $(ACTIVATE) && ruff check app

codegen:
	cd frontend && pnpm codegen

build:
	docker compose build

prod-build:
	docker compose build

backup:
	./deploy/backup.sh

clean:
	rm -rf backend/.venv backend/.pytest_cache backend/.ruff_cache
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	rm -rf frontend/node_modules frontend/dist frontend/.vite
	rm -rf .run logs
```

```typescript
===== frontend/src/App.tsx =====
// 顶层路由：登录 + RequireAuth + AppShell + 各页面
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { RequireAuth } from "@/components/layout/RequireAuth";

import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { AccountList } from "@/pages/Accounts/List";
import { AccountWizard } from "@/pages/Accounts/Wizard";
import { AccountDetail } from "@/pages/Accounts/Detail";
import { FeatureMatrix } from "@/pages/FeatureMatrix";
import { AutoReplyConfig } from "@/pages/Features/AutoReply";
import { ForwardConfig } from "@/pages/Features/Forward";
import { GroupAdminConfig } from "@/pages/Features/GroupAdmin";
import { SchedulerConfig } from "@/pages/Features/Scheduler";
import { MonitorConfig } from "@/pages/Features/Monitor";
import { Logs } from "@/pages/Logs";
import { SettingsIndex } from "@/pages/Settings/Index";
import { CommandTemplates } from "@/pages/Settings/CommandTemplates";
import { Plugins } from "@/pages/Plugins";
import { AISettings } from "@/pages/AISettings";
import { Templates } from "@/pages/Templates";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<Dashboard />} />
          <Route path="accounts">
            <Route index element={<AccountList />} />
            <Route path="new" element={<AccountWizard />} />
            <Route path=":aid" element={<AccountDetail />} />
            <Route
              path=":aid/features/auto_reply"
              element={<AutoReplyConfig />}
            />
            <Route path=":aid/features/forward" element={<ForwardConfig />} />
            <Route
              path=":aid/features/group_admin"
              element={<GroupAdminConfig />}
            />
            <Route
              path=":aid/features/scheduler"
              element={<SchedulerConfig />}
            />
            <Route path=":aid/features/monitor" element={<MonitorConfig />} />
          </Route>
          <Route path="matrix" element={<FeatureMatrix />} />
          <Route path="logs" element={<Logs />} />
          <Route path="settings" element={<SettingsIndex />} />
          {/* Sprint2 #2：自定义命令模板（命令仍属 Settings 管辖范围） */}
          <Route path="settings/commands" element={<CommandTemplates />} />

          {/* 顶层独立页：通用模板（风控 / 代理 / 设备标识 / 自定义命令） */}
          <Route path="templates" element={<Templates />} />

          {/* 顶层独立页：插件管理（含已安装 / 插件市场两个子 tab） */}
          <Route path="plugins" element={<Plugins />} />
          {/* 老链接兼容：/settings/plugins → /plugins，/settings/plugin-market → /plugins?tab=market */}
          <Route
            path="settings/plugins"
            element={<Navigate to="/plugins" replace />}
          />
          <Route
            path="settings/plugin-market"
            element={<Navigate to="/plugins?tab=market" replace />}
          />

          {/* 顶层独立页：AI 设置 */}
          <Route path="ai" element={<AISettings />} />
          {/* 老链接兼容：/settings/llm-providers → /ai */}
          <Route
            path="settings/llm-providers"
            element={<Navigate to="/ai" replace />}
          />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
    </Routes>
  );
}

===== frontend/src/api/accounts.ts =====
// 账号 API 包装
import { api } from "@/lib/api";
import type {
  AccountConfirm2FARequest,
  AccountConfirmCodeRequest,
  AccountConfirmResponse,
  AccountDetail,
  AccountStartLoginRequest,
  AccountStartLoginResponse,
  AccountSummary,
  AccountUpdateRequest,
  AccountFeatureItem,
} from "@/api/types";

export async function listAccounts(): Promise<AccountSummary[]> {
  const { data } = await api.get<AccountSummary[]>("/api/accounts");
  return data;
}

// 头像 URL：直接拼成相对路径，给 <img src> 用；后端 24h 私有缓存 + 不存在时 404
// 调用方拿到 404 后由 AccountAvatar 自动 fallback 到首字母
export function avatarUrl(aid: number): string {
  const base = (api.defaults.baseURL || "").replace(/\/$/, "");
  return `${base}/api/accounts/${aid}/avatar`;
}

export async function getAccount(aid: number): Promise<AccountDetail> {
  const { data } = await api.get<AccountDetail>(`/api/accounts/${aid}`);
  return data;
}

export async function patchAccount(
  aid: number,
  payload: AccountUpdateRequest,
): Promise<AccountDetail> {
  const { data } = await api.patch<AccountDetail>(`/api/accounts/${aid}`, payload);
  return data;
}

export async function deleteAccount(aid: number): Promise<void> {
  await api.delete(`/api/accounts/${aid}`);
}

export async function pauseAccount(aid: number): Promise<void> {
  await api.post(`/api/accounts/${aid}/pause`);
}

export async function resumeAccount(aid: number): Promise<void> {
  await api.post(`/api/accounts/${aid}/resume`);
}

// ===================== 绑定向导 =====================
export async function loginStart(
  payload: AccountStartLoginRequest,
): Promise<AccountStartLoginResponse> {
  const { data } = await api.post<AccountStartLoginResponse>(
    "/api/accounts/login/start",
    payload,
  );
  return data;
}

export async function loginCode(
  payload: AccountConfirmCodeRequest,
): Promise<AccountConfirmResponse> {
  const { data } = await api.post<AccountConfirmResponse>(
    "/api/accounts/login/code",
    payload,
  );
  return data;
}

export async function login2fa(
  payload: AccountConfirm2FARequest,
): Promise<AccountConfirmResponse> {
  const { data } = await api.post<AccountConfirmResponse>(
    "/api/accounts/login/2fa",
    payload,
  );
  return data;
}

export async function cloneConfig(
  toAid: number,
  fromAid: number,
  features: string[],
): Promise<void> {
  await api.post(`/api/accounts/${toAid}/clone-config`, {
    from_account_id: fromAid,
    features,
  });
}

// ===================== 功能开关 =====================
export async function listAccountFeatures(
  aid: number,
): Promise<AccountFeatureItem[]> {
  const { data } = await api.get<AccountFeatureItem[]>(
    `/api/accounts/${aid}/features`,
  );
  return data;
}

export async function toggleAccountFeature(
  aid: number,
  key: string,
  enabled: boolean,
): Promise<void> {
  await api.patch(`/api/accounts/${aid}/features/${key}`, { enabled });
}

===== frontend/src/api/commands.ts =====
// 自定义命令 + LLM Provider API 包装（Sprint2 #2）
import { api } from "@/lib/api";
import type {
  AccountCommandItem,
  CommandTemplateCreate,
  CommandTemplateOut,
  CommandTemplateUpdate,
  FetchModelsResponse,
  LLMProviderCreate,
  LLMProviderOut,
  LLMProviderUpdate,
  TestModelRequest,
  TestModelResponse,
} from "@/api/types";

// ===================== 命令模板 CRUD =====================
export async function listCommandTemplates(): Promise<CommandTemplateOut[]> {
  const { data } = await api.get<CommandTemplateOut[]>(
    "/api/commands/templates",
  );
  return data;
}

export async function createCommandTemplate(
  payload: CommandTemplateCreate,
): Promise<CommandTemplateOut> {
  const { data } = await api.post<CommandTemplateOut>(
    "/api/commands/templates",
    payload,
  );
  return data;
}

export async function patchCommandTemplate(
  id: number,
  payload: CommandTemplateUpdate,
): Promise<CommandTemplateOut> {
  const { data } = await api.patch<CommandTemplateOut>(
    `/api/commands/templates/${id}`,
    payload,
  );
  return data;
}

export async function deleteCommandTemplate(id: number): Promise<void> {
  await api.delete(`/api/commands/templates/${id}`);
}

// ===================== LLM Provider CRUD =====================
export async function listLLMProviders(): Promise<LLMProviderOut[]> {
  const { data } = await api.get<LLMProviderOut[]>(
    "/api/commands/llm-providers",
  );
  return data;
}

export async function createLLMProvider(
  payload: LLMProviderCreate,
): Promise<LLMProviderOut> {
  const { data } = await api.post<LLMProviderOut>(
    "/api/commands/llm-providers",
    payload,
  );
  return data;
}

export async function patchLLMProvider(
  id: number,
  payload: LLMProviderUpdate,
): Promise<LLMProviderOut> {
  const { data } = await api.patch<LLMProviderOut>(
    `/api/commands/llm-providers/${id}`,
    payload,
  );
  return data;
}

export async function deleteLLMProvider(id: number): Promise<void> {
  await api.delete(`/api/commands/llm-providers/${id}`);
}

/** 调 GET {base_url}/models 拉模型列表，合并到 provider.models（保留已 enabled 状态）。
 *  Anthropic 不支持，会拿到 422。 */
export async function fetchProviderModels(
  id: number,
): Promise<FetchModelsResponse> {
  const { data } = await api.post<FetchModelsResponse>(
    `/api/commands/llm-providers/${id}/fetch-models`,
  );
  return data;
}

/** 用 max_tokens=4 的最小调用测某个 model 通不通；返延时和返回片段。 */
export async function testProviderModel(
  id: number,
  payload: TestModelRequest,
): Promise<TestModelResponse> {
  const { data } = await api.post<TestModelResponse>(
    `/api/commands/llm-providers/${id}/test-model`,
    payload,
  );
  return data;
}

// ===================== 账号 × 模板 关联 =====================
export async function listAccountCommands(
  aid: number,
): Promise<AccountCommandItem[]> {
  const { data } = await api.get<AccountCommandItem[]>(
    `/api/accounts/${aid}/commands`,
  );
  return data;
}

export async function enableAccountCommand(
  aid: number,
  templateId: number,
): Promise<void> {
  await api.post(`/api/accounts/${aid}/commands/${templateId}`);
}

export async function disableAccountCommand(
  aid: number,
  templateId: number,
): Promise<void> {
  await api.delete(`/api/accounts/${aid}/commands/${templateId}`);
}

===== frontend/src/api/device-profiles.ts =====
// 设备伪装库 API 包装。
import { api } from "@/lib/api";
import type {
  DeviceProfileCreate,
  DeviceProfileOut,
  DeviceProfileUpdate,
} from "@/api/types";

export async function listDeviceProfiles(): Promise<DeviceProfileOut[]> {
  const { data } = await api.get<DeviceProfileOut[]>("/api/device-profiles");
  return data;
}

export async function createDeviceProfile(
  payload: DeviceProfileCreate,
): Promise<DeviceProfileOut> {
  const { data } = await api.post<DeviceProfileOut>(
    "/api/device-profiles",
    payload,
  );
  return data;
}

export async function patchDeviceProfile(
  pid: number,
  payload: DeviceProfileUpdate,
): Promise<DeviceProfileOut> {
  const { data } = await api.patch<DeviceProfileOut>(
    `/api/device-profiles/${pid}`,
    payload,
  );
  return data;
}

export async function setDefaultDeviceProfile(
  pid: number,
): Promise<DeviceProfileOut> {
  const { data } = await api.post<DeviceProfileOut>(
    `/api/device-profiles/${pid}/default`,
  );
  return data;
}

export async function deleteDeviceProfile(pid: number): Promise<void> {
  await api.delete(`/api/device-profiles/${pid}`);
}

===== frontend/src/api/features.ts =====
// 功能矩阵 / 规则 / 自动回复 dry-run 等 API 包装
import { api } from "@/lib/api";
import type {
  FeatureMatrixResponse,
  RuleCopyRequest,
  RuleCreate,
  RuleDryRunRequest,
  RuleDryRunResponse,
  RuleOut,
  RuleUpdate,
} from "@/api/types";

export async function getFeatureMatrix(): Promise<FeatureMatrixResponse> {
  const { data } = await api.get<FeatureMatrixResponse>("/api/feature-matrix");
  return data;
}

export async function listRules(
  aid: number,
  feature: string,
): Promise<RuleOut[]> {
  const { data } = await api.get<RuleOut[]>(
    `/api/accounts/${aid}/features/${feature}/rules`,
  );
  return data;
}

export async function createRule(
  aid: number,
  feature: string,
  payload: RuleCreate,
): Promise<RuleOut> {
  const { data } = await api.post<RuleOut>(
    `/api/accounts/${aid}/features/${feature}/rules`,
    payload,
  );
  return data;
}

export async function updateRule(
  aid: number,
  feature: string,
  rid: number,
  payload: RuleUpdate,
): Promise<RuleOut> {
  const { data } = await api.patch<RuleOut>(
    `/api/accounts/${aid}/features/${feature}/rules/${rid}`,
    payload,
  );
  return data;
}

export async function deleteRule(
  aid: number,
  feature: string,
  rid: number,
): Promise<void> {
  await api.delete(`/api/accounts/${aid}/features/${feature}/rules/${rid}`);
}

export async function dryRunRule(
  aid: number,
  feature: string,
  rid: number,
  payload: RuleDryRunRequest,
): Promise<RuleDryRunResponse> {
  const { data } = await api.post<RuleDryRunResponse>(
    `/api/accounts/${aid}/features/${feature}/rules/${rid}/dry-run`,
    payload,
  );
  return data;
}

export async function copyRules(
  aid: number,
  feature: string,
  payload: RuleCopyRequest,
): Promise<void> {
  await api.post(
    `/api/accounts/${aid}/features/${feature}/rules/copy`,
    payload,
  );
}

===== frontend/src/api/ignored_peers.ts =====
// 忽略 peer API：列表 / 加入 / 移除 + 最近活跃会话
import { api } from "@/lib/api";
import type {
  IgnoredPeer,
  IgnoredPeerCreate,
  RecentPeersResponse,
} from "@/api/types";

/** 列出账号已忽略的 peer */
export async function listIgnoredPeers(aid: number): Promise<IgnoredPeer[]> {
  const { data } = await api.get<IgnoredPeer[]>(
    `/api/accounts/${aid}/ignored-peers`,
  );
  return data;
}

/** 加入忽略名单（幂等：同 peer_id 已存在则后端返回原行） */
export async function addIgnoredPeer(
  aid: number,
  payload: IgnoredPeerCreate,
): Promise<IgnoredPeer> {
  const { data } = await api.post<IgnoredPeer>(
    `/api/accounts/${aid}/ignored-peers`,
    payload,
  );
  return data;
}

/** 从忽略名单移除一行 */
export async function removeIgnoredPeer(
  aid: number,
  ignoredId: number,
): Promise<void> {
  await api.delete(`/api/accounts/${aid}/ignored-peers/${ignoredId}`);
}

/**
 * 拉 worker 内存中的最近活跃 peer 列表（≤50 条）+ worker 是否在跑。
 *
 * 后端把"worker 离线"和"worker 在跑只是没收到消息"分开报告，
 * 前端据此给出精准引导，而不是一律提示"暂无活跃会话"。
 */
export async function listRecentPeers(
  aid: number,
): Promise<RecentPeersResponse> {
  const { data } = await api.get<RecentPeersResponse>(
    `/api/accounts/${aid}/recent-peers`,
  );
  return data;
}

===== frontend/src/api/network.ts =====
// 网络环境探测：当前后端进程出口 IP / 国家 / 地区
import { api } from "@/lib/api";
import type { NetworkInfo } from "@/api/types";

export async function getNetworkInfo(): Promise<NetworkInfo> {
  const { data } = await api.get<NetworkInfo>("/api/system/network");
  return data;
}

export async function refreshNetworkInfo(): Promise<NetworkInfo> {
  const { data } = await api.post<NetworkInfo>("/api/system/network/refresh");
  return data;
}

===== frontend/src/api/plugins.ts =====
// 第三方插件 zip 安装 / 启停 / 卸载 API 包装（Sprint2 #4 阶段 B + C）
import { api } from "@/lib/api";

// ─── 类型 ───────────────────────────────────────────────
export interface PluginInstallOut {
  key: string;
  source: "builtin" | "zip" | "repo";
  version: string;
  enabled: boolean;
  signature_ok: boolean | null;
  installed_path: string;
  repo_id?: number | null;
  manifest?: Record<string, unknown> | null;
  installed_at: string;
  updated_at: string;
}

export interface PluginRepoOut {
  id: number;
  name: string;
  url: string;
  enabled: boolean;
  last_synced_at: string | null;
}

export interface PluginAvailableOut {
  repo_id: number;
  key: string;
  name: string;
  version: string;
  author: string | null;
  description: string | null;
}

// ─── 列表 ───────────────────────────────────────────────
export async function listInstalledPackages(): Promise<PluginInstallOut[]> {
  const { data } = await api.get<PluginInstallOut[]>(
    "/api/plugins/installed-packages",
  );
  return data;
}

// ─── 上传 zip（multipart） ────────────────────────────
export async function uploadPluginZip(
  zip: File,
  signature: File | null,
): Promise<PluginInstallOut> {
  const fd = new FormData();
  fd.append("file", zip);
  if (signature) fd.append("signature", signature);
  const { data } = await api.post<PluginInstallOut>(
    "/api/plugins/install/upload",
    fd,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return data;
}

// ─── 启用 / 禁用 / 卸载 ─────────────────────────────────
export async function enableInstall(key: string): Promise<PluginInstallOut> {
  const { data } = await api.post<PluginInstallOut>(
    `/api/plugins/install/${encodeURIComponent(key)}/enable`,
  );
  return data;
}

export async function disableInstall(key: string): Promise<PluginInstallOut> {
  const { data } = await api.post<PluginInstallOut>(
    `/api/plugins/install/${encodeURIComponent(key)}/disable`,
  );
  return data;
}

export async function uninstallPlugin(key: string): Promise<void> {
  await api.delete(`/api/plugins/install/${encodeURIComponent(key)}`);
}

// ─── 仓库 / 市场（阶段 C） ───────────────────────────────
export async function listPluginRepos(): Promise<PluginRepoOut[]> {
  const { data } = await api.get<PluginRepoOut[]>("/api/plugin-repos");
  return data;
}

export async function createPluginRepo(
  name: string,
  url: string,
): Promise<PluginRepoOut> {
  const { data } = await api.post<PluginRepoOut>("/api/plugin-repos", {
    name,
    url,
    enabled: true,
  });
  return data;
}

export async function deletePluginRepo(id: number): Promise<void> {
  await api.delete(`/api/plugin-repos/${id}`);
}

// 调阶段 C 真实实现（``/sync2``）；阶段 A 的 ``/sync`` 仍返 501 占位。
export async function syncPluginRepo(id: number): Promise<{ inserted: number }> {
  const { data } = await api.post<{ inserted: number }>(
    `/api/plugin-repos/${id}/sync2`,
  );
  return data;
}

export async function listAvailablePlugins(): Promise<PluginAvailableOut[]> {
  const { data } = await api.get<PluginAvailableOut[]>(
    "/api/plugins/available",
  );
  return data;
}

export async function installFromRepo(
  repo_id: number,
  key: string,
): Promise<PluginInstallOut> {
  const { data } = await api.post<PluginInstallOut>(
    "/api/plugins/install/from-repo",
    { repo_id, key },
  );
  return data;
}

===== frontend/src/api/proxies.ts =====
// 代理 API 包装
import { api } from "@/lib/api";
import type {
  ProxyCreate,
  ProxyOut,
  ProxyTestResult,
  ProxyUpdate,
} from "@/api/types";

export async function listProxies(): Promise<ProxyOut[]> {
  const { data } = await api.get<ProxyOut[]>("/api/proxies");
  return data;
}

export async function createProxy(payload: ProxyCreate): Promise<ProxyOut> {
  const { data } = await api.post<ProxyOut>("/api/proxies", payload);
  return data;
}

export async function patchProxy(
  id: number,
  payload: ProxyUpdate,
): Promise<ProxyOut> {
  const { data } = await api.patch<ProxyOut>(`/api/proxies/${id}`, payload);
  return data;
}

export async function deleteProxy(id: number): Promise<void> {
  await api.delete(`/api/proxies/${id}`);
}

export async function testProxy(id: number): Promise<ProxyTestResult> {
  const { data } = await api.post<ProxyTestResult>(`/api/proxies/${id}/test`);
  return data;
}

===== frontend/src/api/system.ts =====
// 风控 / 系统 API 包装
import { api } from "@/lib/api";
import type {
  AccountRateLimitOut,
  AuditLogItem,
  HealthOverview,
  HumanizeConfig,
  HumanizeUpdate,
  RateLimitRuleConfig,
  StrictRequest,
  RuntimeLogItem,
  SystemSettings,
  TemplateOut,
} from "@/api/types";

// ===================== 风控 =====================
export async function getAccountRateLimit(
  aid: number,
): Promise<AccountRateLimitOut> {
  const { data } = await api.get<AccountRateLimitOut>(
    `/api/accounts/${aid}/rate-limit`,
  );
  return data;
}

export async function patchAccountRateLimit(
  aid: number,
  action: string,
  payload: Partial<RateLimitRuleConfig>,
): Promise<void> {
  await api.patch(`/api/accounts/${aid}/rate-limit/${action}`, payload);
}

export async function strictRateLimit(
  aid: number,
  payload: StrictRequest = {},
): Promise<void> {
  await api.post(`/api/accounts/${aid}/rate-limit/strict`, payload);
}

// ===================== 日志 =====================
export interface RuntimeLogQuery {
  account_id?: number | string;
  level?: string;
  /** "system" = worker 启停 / 错误；"event" = 消息事件 / plugin 命中 */
  source?: "system" | "event" | string;
  since?: string;
  limit?: number;
}
export async function listRuntimeLogs(
  q: RuntimeLogQuery = {},
): Promise<RuntimeLogItem[]> {
  const { data } = await api.get<RuntimeLogItem[]>("/api/logs/runtime", {
    params: q,
  });
  return data;
}

// 操作日志（Dashboard 摘要 + 后续审计页用）
export interface AuditLogQuery {
  user_id?: number;
  since?: string;
  limit?: number;
}
export async function listAuditLogs(
  q: AuditLogQuery = {},
): Promise<AuditLogItem[]> {
  const { data } = await api.get<AuditLogItem[]>("/api/logs/audit", {
    params: q,
  });
  return data;
}

// ===================== 系统设置 =====================
export async function getSystemSettings(): Promise<SystemSettings> {
  const { data } = await api.get<SystemSettings>("/api/system/settings");
  return data;
}
export async function patchSystemSettings(
  payload: Partial<SystemSettings>,
): Promise<SystemSettings> {
  const { data } = await api.patch<SystemSettings>(
    "/api/system/settings",
    payload,
  );
  return data;
}

export async function getGlobalLimits(): Promise<{ api_qps_total: number }> {
  const { data } = await api.get<{ api_qps_total: number }>(
    "/api/system/global-limits",
  );
  return data;
}
export async function putGlobalLimits(api_qps_total: number): Promise<void> {
  await api.put("/api/system/global-limits", { api_qps_total });
}

// ===================== 风控模板 =====================
export async function listRateTemplates(): Promise<TemplateOut[]> {
  const { data } = await api.get<TemplateOut[]>("/api/rate-templates");
  return data;
}

export async function createRateTemplate(payload: {
  name: string;
  is_default?: boolean;
}): Promise<TemplateOut> {
  const { data } = await api.post<TemplateOut>("/api/rate-templates", payload);
  return data;
}

export async function deleteRateTemplate(id: number): Promise<void> {
  await api.delete(`/api/rate-templates/${id}`);
}

// ===================== 拟人化 humanize =====================
// 后端是 PUT 但语义是 PATCH（仅传非空字段，未传字段保持不变）
export async function getHumanize(aid: number): Promise<HumanizeConfig> {
  const { data } = await api.get<HumanizeConfig>(
    `/api/accounts/${aid}/humanize`,
  );
  return data;
}

export async function patchHumanize(
  aid: number,
  body: HumanizeUpdate,
): Promise<HumanizeConfig> {
  const { data } = await api.put<HumanizeConfig>(
    `/api/accounts/${aid}/humanize`,
    body,
  );
  return data;
}

// ===================== 系统健康概览（Dashboard 用）=====================
export async function getHealthOverview(): Promise<HealthOverview> {
  const { data } = await api.get<HealthOverview>("/api/system/health-overview");
  return data;
}

===== frontend/src/api/types.ts =====
// 与后端 schema 对齐的关键类型（手写版）。OpenAPI 生成的 schema.ts 后续替换。

// ===================== 鉴权 =====================
export interface LoginRequest {
  username: string;
  password: string;
  totp_code?: string | null;
}
export interface LoginResponse {
  ok: boolean;
  require_totp: boolean;
}
export interface CurrentUser {
  id: number;
  username: string;
  has_totp: boolean;
}

// ===================== 账号 =====================
export type AccountStatus =
  | "active"
  | "paused"
  | "floodwait"
  | "dead"
  | "login_required";

export interface AccountSummary {
  id: number;
  phone: string;
  display_name: string | null;
  /** Telegram 数字 ID（client.get_me().id），新账号登录后回填，老账号 worker 上线时自动同步 */
  tg_user_id?: number | null;
  /** Telegram 用户名（不含 @），用户可能未设置或随时修改 */
  tg_username?: string | null;
  status: AccountStatus;
  tags?: string[] | null;
  enabled_features: number;
  cold_start_until: string | null;
  created_at: string;
}

export interface AccountDetail extends AccountSummary {
  notes?: string | null;
  template_id?: number | null;
  proxy_id?: number | null;
  /** 设备伪装 profile id，决定 TG 设备列表里看到的 device_model / system_version / app_version */
  device_profile_id?: number | null;
}

export interface AccountStartLoginRequest {
  api_id: number;
  api_hash: string;
  phone: string;
  proxy_id?: number | null;
  device_profile_id?: number | null;
}
export interface AccountStartLoginResponse {
  login_token: string;
  phone_code_hash?: string | null;
}
export interface AccountConfirmCodeRequest {
  login_token: string;
  code: string;
}
export interface AccountConfirm2FARequest {
  login_token: string;
  password: string;
}
export interface AccountConfirmResponse {
  account_id: number;
  require_2fa: boolean;
  display_name?: string | null;
}
export interface AccountUpdateRequest {
  display_name?: string | null;
  notes?: string | null;
  tags?: string[] | null;
  template_id?: number | null;
  proxy_id?: number | null;
  device_profile_id?: number | null;
}

// ===================== 设备伪装 =====================
export interface DeviceProfileOut {
  id: number;
  name: string;
  device_model: string;
  system_version: string;
  app_version: string;
  lang_code: string;
  system_lang_code: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface DeviceProfileCreate {
  name: string;
  device_model: string;
  system_version: string;
  app_version: string;
  lang_code?: string;
  system_lang_code?: string;
  is_default?: boolean;
}

export interface DeviceProfileUpdate {
  name?: string;
  device_model?: string;
  system_version?: string;
  app_version?: string;
  lang_code?: string;
  system_lang_code?: string;
  is_default?: boolean;
}
export interface AccountCloneConfigRequest {
  from_account_id: number;
  features: string[];
}

// ===================== 功能 =====================
export type FeatureState = "active" | "failed" | "disabled";

export interface FeatureInfo {
  key: string;
  display_name: string;
  is_builtin: boolean;
  version?: string | null;
}
export interface AccountFeatureItem {
  feature_key: string;
  enabled: boolean;
  state: FeatureState;
  last_error?: string | null;
  config: Record<string, unknown>;
}
export interface AccountFeatureToggle {
  enabled: boolean;
  config?: Record<string, unknown> | null;
}
export interface FeatureMatrixRow {
  id: number;
  name: string;
  features: Record<string, FeatureState>;
}
export interface FeatureMatrixResponse {
  features: FeatureInfo[];
  accounts: FeatureMatrixRow[];
}

// ===================== 规则 =====================
export interface RuleOut {
  id: number;
  account_id: number;
  feature_key: string;
  name: string;
  enabled: boolean;
  priority: number;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
export interface RuleCreate {
  name: string;
  enabled?: boolean;
  priority?: number;
  config?: Record<string, unknown>;
}
export interface RuleUpdate {
  name?: string;
  enabled?: boolean;
  priority?: number;
  config?: Record<string, unknown>;
}
export interface RuleDryRunRequest {
  sample_message: string;
  sample_chat_type?: "private" | "group" | "channel";
  sample_chat_id?: number;
}
export interface RuleDryRunResponse {
  matched: boolean;
  output?: string | null;
  detail?: Record<string, unknown> | null;
}
export interface RuleCopyRequest {
  rule_ids: number[];
  target_account_ids: number[];
}

// ===================== 风控 =====================
export type RatePolicy = "drop" | "queue" | "backoff" | "pause" | "notify";

export interface RateLimitRuleConfig {
  action: string;
  per_second?: number | null;
  per_minute?: number | null;
  per_hour?: number | null;
  per_day?: number | null;
  same_peer_per_minute?: number | null;
  policy: RatePolicy;
  backoff_base_seconds: number;
  backoff_max_seconds: number;
  enabled: boolean;
}
export interface AccountRateLimitOut {
  template_id: number | null;
  rules: RateLimitRuleConfig[];
}
export interface UsageBucket {
  action: string;
  used: number;
  limit: number | null;
  pct: number;
  warn?: boolean;
}
export interface UsageResponse {
  window: string;
  buckets: UsageBucket[];
  active_overrides: Array<Record<string, unknown>>;
}
export interface StrictRequest {
  multiplier?: number;
  ttl_seconds?: number;
}

export interface TemplateOut {
  id: number;
  name: string;
  is_default: boolean;
  created_at: string;
}

// ===================== 代理 =====================
export type ProxyType = "socks5" | "http" | "https" | "mtproxy";

export interface ProxyOut {
  id: number;
  type: ProxyType;
  host: string;
  port: number;
  username: string | null;
  has_password: boolean;
}

export interface ProxyCreate {
  type: ProxyType;
  host: string;
  port: number;
  username?: string | null;
  password?: string | null;
}

export interface ProxyUpdate {
  type?: ProxyType;
  host?: string;
  port?: number;
  username?: string | null;
  password?: string | null;
  clear_password?: boolean;
}

export interface ProxyTestResult {
  ok: boolean;
  latency_ms?: number | null;
  exit_ip?: string | null;
  country?: string | null;
  region?: string | null;
  city?: string | null;
  error?: string | null;
}

// ===================== 网络环境 =====================
export interface NetworkInfo {
  ip: string | null;
  country: string | null;
  region: string | null;
  city: string | null;
  org: string | null;
  cached_at: number;
  fresh: boolean;
  error?: string | null;
}

// ===================== 自动回复 rule.config =====================
export type AutoReplyMatch = "keyword" | "regex";
export type AutoReplyScope = "private" | "group_all" | "group_specific";

export interface AutoReplyRuleConfig {
  match: AutoReplyMatch;
  patterns: string[];
  scope: AutoReplyScope;
  group_ids?: string[];
  reply: string;
  cooldown_seconds?: number;
  whitelist?: string[];
  blacklist?: string[];
  case_sensitive?: boolean;
  reply_to?: boolean;     // true = 以引用形式回复（默认）；false = 直接发新消息
}

// ===== Sprint2 #5 =====
// 与后端 ``builtin/forward/manifest.py:config_schema`` 对齐的 rule.config 结构
//
// source_kind：
//   - all       —— 任何 incoming 消息都进流水线
//   - peers     —— 仅 source_peers 中的 chat_id 命中（支持 -100 / -bare / bare 等价展开）
//   - keyword   —— 文本（小写）包含 keyword 时命中；空 keyword 视为不命中
//
// mode：
//   - forward_native  —— 原生转发，保留原作者署名
//   - copy_text       —— 复制文本，不显示原作者，可加 header 前缀
//   - quote           —— 引用包装，自动 "📨 来自 X" 前缀
//   - link_only       —— 公开超级群可点链接（私群退化为可读字符串）
export type ForwardSourceKind = "all" | "peers" | "keyword";
export type ForwardMode =
  | "forward_native"
  | "copy_text"
  | "quote"
  | "link_only";

export interface ForwardRuleConfig {
  source_kind: ForwardSourceKind;
  /** chat_id 列表；前端用 string[] 编辑，提交时转成 number[] */
  source_peers?: number[];
  keyword?: string;
  /** 必填：目标 chat_id（Telethon 形式） */
  target_chat_id: number;
  mode: ForwardMode;
  /** 默认 true；false 时跳过含媒体消息（仅文本通过） */
  include_media?: boolean;
  /** copy / quote / link_only 模式下的固定前缀 */
  header?: string;
}

// ===================== 日志 =====================
export interface RuntimeLogItem {
  id: number;
  account_id: number | null;
  level: string;
  message: string;
  created_at: string;
  source?: string | null;
}

// 操作日志（Web 端写操作）
export interface AuditLogItem {
  id: number;
  ts: string;
  user_id: number | null;
  action: string;
  target?: string | null;
  detail?: Record<string, unknown> | null;
}

// ===================== 系统设置 =====================
export interface SystemSettings {
  command_prefix: string;
  kill_switch?: boolean;
  api_qps_total?: number;
}

// ===================== 系统健康概览（Dashboard 用）=====================
//
// 与后端 ``app/api/system_health.py`` 对齐
export interface DbStatus {
  ok: boolean;
  /** PostgreSQL 16.x 字串；失败时 null */
  version?: string | null;
  error?: string | null;
}

export interface AlembicStatus {
  /** true = DB 当前版本就是代码 head；false = 需要跑 alembic upgrade head */
  ok: boolean;
  /** DB 里 alembic_version 表存的版本号 */
  current?: string | null;
  /** 代码仓库里 alembic 链的最新版本 */
  head?: string | null;
  /** 已写但还没 apply 的迁移版本号（按时间序） */
  pending: string[];
  error?: string | null;
}

export interface RedisStatus {
  ok: boolean;
  error?: string | null;
}

export interface ProvidersHealthStatus {
  total: number;
  with_api_key: number;
  with_proxy: number;
  /** {modality: count}，如 {"text":2,"vision":1} */
  by_modality: Record<string, number>;
  /** {cost_tier_str: count}，如 {"1":1,"2":2,"3":1} */
  by_cost_tier: Record<string, number>;
}

export interface ProxiesHealthStatus {
  total: number;
  /** {type: count}，如 {"socks5":2,"http":1,"mtproxy":1} */
  by_type: Record<string, number>;
  /** 被任意 LLMProvider.proxy_id 引用的代理数量（去重） */
  used_by_llm: number;
}

export interface WorkersHealthStatus {
  total: number;
  /** {status: count}，如 {"active":3,"paused":1,"login_required":1} */
  by_status: Record<string, number>;
}

export interface HealthOverview {
  db: DbStatus;
  alembic: AlembicStatus;
  redis: RedisStatus;
  providers: ProvidersHealthStatus;
  proxies: ProxiesHealthStatus;
  workers: WorkersHealthStatus;
}

// 通用 list 包装（部分接口直接返数组，但为了后续兼容预留）
export interface ListResponse<T> {
  items: T[];
  total?: number;
}

// ===================== Sprint2 #1：拟人化 humanize =====================
// 与后端 ``HumanizeOut`` / ``HumanizeUpdate`` 对齐
//   - active_window_*：``HH:MM[:SS]`` 字符串，``null`` = 不限活跃时段
//   - typing_probability / jitter_pct：百分比 0-100
//   - typing_min_ms <= typing_max_ms 由前端校验
export interface HumanizeConfig {
  jitter_pct: number;
  typing_simulate: boolean;
  typing_min_ms: number;
  typing_max_ms: number;
  typing_probability: number;
  read_before_reply: boolean;
  active_window_start?: string | null;
  active_window_end?: string | null;
  cold_start_days: number;
}
export type HumanizeUpdate = Partial<HumanizeConfig>;

// ==================== Sprint2 #3 Ignored Peers ====================
//
// peer 类型：
//   - private    1 对 1 私聊（chat_id 为正整数）
//   - group      普通群（旧版小群，chat_id 为负数但非 -100 开头）
//   - supergroup 超级群（chat_id 形如 -1001234567890）
//   - channel    频道（chat_id 形如 -1001234567890）
export type PeerKind = "private" | "group" | "supergroup" | "channel";

/** 已忽略的 peer 一行（GET / POST 响应） */
export interface IgnoredPeer {
  id: number;
  account_id: number;
  /** Telethon chat_id；可正可负（supergroup 形如 -100xxx） */
  peer_id: number;
  peer_kind: PeerKind | string;
  peer_label: string | null;
  added_at: string;
}

/** 加入忽略名单的入参 */
export interface IgnoredPeerCreate {
  peer_id: number;
  peer_kind?: PeerKind | string;
  peer_label?: string | null;
}

/**
 * worker 内存里"最近 50 个 incoming peer"的一条。
 * - 重启 worker 后清空
 * - worker 离线时后端返回空数组
 */
export interface RecentPeerItem {
  peer_id: number;
  peer_kind: PeerKind | string;
  peer_label: string | null;
  /** epoch 秒（time.time()），前端做相对时间显示 */
  ts: number;
}

/**
 * GET /recent-peers 包裹响应：把 "worker 是否在跑" 单独传一个布尔，
 * 这样前端可以区分"worker 离线导致空"vs"worker 在跑只是没收到 incoming"
 * 这两种 items=[] 的语义。
 */
export interface RecentPeersResponse {
  worker_alive: boolean;
  items: RecentPeerItem[];
}

// ==================== Sprint2 #2 Custom Commands ====================
//
// 4 种命令类型：
//   - reply_text   收到 → 编辑原消息为文本（支持 {args} 占位）
//   - forward_to   收到 → 转发被引用消息到指定 chat_id
//   - run_plugin   占位：调插件方法（V1 暂未实装）
//   - ai           收到 → 调 LLM provider → 编辑回原消息
export type CommandTemplateType =
  | "reply_text"
  | "forward_to"
  | "run_plugin"
  | "ai";

/** 命令模板出参（与 GET /api/commands/templates 对齐） */
export interface CommandTemplateOut {
  id: number;
  /** ,name 触发名；命令前缀在系统设置里改 */
  name: string;
  type: CommandTemplateType;
  /** 按 type 不同结构；前端按 type 切表单 */
  config: Record<string, unknown>;
  description: string | null;
  created_at: string;
}

/** 新建模板入参（POST /api/commands/templates） */
export interface CommandTemplateCreate {
  name: string;
  type: CommandTemplateType;
  config: Record<string, unknown>;
  description?: string | null;
}

/** PATCH 更新（任意字段可选） */
export interface CommandTemplateUpdate {
  name?: string;
  type?: CommandTemplateType;
  config?: Record<string, unknown>;
  description?: string | null;
}

// 各 type 对应的 config 形状（仅做编辑/校验参考；后端 schema 校验是权威）
export interface ReplyTextConfig {
  /** 命令文本；支持 {args} 占位，被 ,name xxx yyy 的剩余参数替换 */
  text: string;
}

export interface ForwardToConfig {
  /** 目标会话的 chat_id（int / 字符串都可） */
  target_chat_id: number | string;
}

export interface RunPluginConfig {
  plugin_key: string;
  method?: string;
  args?: unknown[];
}

export interface AICommandConfig {
  /** 关联的 LLMProvider.id（fixed 模式下的固定 provider；auto 模式下没命中规则也用它兜底） */
  provider_id: number;
  /** 单次覆盖 provider.default_model；空 = 用 provider 默认 */
  model?: string;
  /** 拼 prompt 时引用被回复消息内容 */
  quote_replied?: boolean;
  system_prompt?: string;
  max_tokens?: number;
  // ── 路由（Sprint2 #2 路由扩展）──
  /**
   * fixed = 永远用 provider_id（V1 行为，默认）
   * auto  = 看消息内容自动选 provider；规则全不命中时回退 fallback / classifier
   */
  routing_mode?: "fixed" | "auto";
  /** auto 模式下规则与分类器都失败时使用的 provider id；缺省 = 用 provider_id 自身 */
  routing_fallback_provider_id?: number;
  /** auto 模式下分类器 provider id；指定后路由器规则未命中时调一个轻量小模型分类 */
  classifier_provider_id?: number;
  // ── 输出格式（决定 TG 里编辑成什么样）──
  /**
   * Telegram 解析模式；默认 html
   * - html      Telethon 内置；支持 <b> <blockquote expandable> 等，能实现折叠引用块
   * - markdown  Telegram 经典 Markdown v1（telethon 接受 'md'）
   * - plain     不解析任何格式
   *
   * 老数据里可能存 'markdownv2'，后端读时自动归一到 'html'（telethon 1.36 不识别 v2）。
   */
  output_format?: "html" | "markdown" | "plain";
  /** 输出模板字符串；null = 用默认（PRESET_SIMPLE） */
  output_template?: string | null;
  /** 是否对占位符的值做对应格式的转义；默认 true。html 模式下会转义 & < > */
  escape_values?: boolean;
}

/** 账号详情 → 命令 tab 一行：模板内容 + 该账号是否启用 */
export interface AccountCommandItem {
  template: CommandTemplateOut;
  enabled: boolean;
}

// ── LLM Provider ──
export type LLMProviderKind = "openai" | "anthropic" | "ollama";

/**
 * API 协议（与 provider 厂商解耦；同一个反代 base_url 可能只支持其中某种）：
 * - chat_completions    POST /chat/completions    OpenAI 经典协议
 * - responses           POST /responses           OpenAI 2024 出的新协议
 * - anthropic_messages  POST /v1/messages         Anthropic 协议
 *
 * 国内常见反代（如 anyrouter）有的只接 responses 而拒 chat_completions，
 * 切到对应 api_format 即可解决报 404 / "模型不支持" 一类问题。
 */
export type LLMApiFormat = "chat_completions" | "responses" | "anthropic_messages";

/**
 * LLMProvider 下挂的一个候选模型条目（与后端 ProviderModel 对齐）。
 *
 * - id 是模型 ID（如 ``gpt-5.5`` / ``claude-haiku-4-5``）
 * - enabled = 该模型是否会出现在下游"自定义命令 ai 子表单"的展开式 select 里
 * - custom = true 表示用户手动加的；false 表示从 ``GET /v1/models`` fetch 拉的
 * - label 是可选的展示名（默认就用 id）
 */
export interface ProviderModel {
  id: string;
  enabled: boolean;
  custom: boolean;
  label?: string | null;
}

/**
 * 模态分类（与后端 ALL_LLM_MODALITIES 对齐）：
 * - text       纯文本 LLM（绝大多数）
 * - vision     视觉多模态（图文输入 → 文本输出，如 GPT-4V / Claude Vision）
 * - audio      音频多模态（语音转写 / TTS，如 Whisper / GPT-4o realtime）
 * - multimodal 全模态（图、音、视频同时输入，如 GPT-4o / Gemini-Pro）
 */
export type LLMModality = "text" | "vision" | "audio" | "multimodal";

/**
 * 路由标签集合（与后端 ALL_LLM_TAGS 对齐）：
 * - chat / code / math / translate / vision    擅长领域
 * - long_context                               大上下文（≥ 64K token）
 * - reason / smart                             复杂推理 / 旗舰
 * - cheap / fast                               量大优先 / 低延迟
 * - classify                                   适合做"路由分类器"的轻量小模型
 */
export type LLMTag =
  | "chat"
  | "code"
  | "math"
  | "translate"
  | "vision"
  | "long_context"
  | "reason"
  | "smart"
  | "cheap"
  | "fast"
  | "classify";

/** GET /api/commands/llm-providers 出参；不含明文 api_key */
export interface LLMProviderOut {
  id: number;
  name: string;
  provider: LLMProviderKind | string;
  has_api_key: boolean;
  base_url: string | null;
  default_model: string;
  /** API 协议；老数据可能缺，前端按 chat_completions 兜底 */
  api_format?: LLMApiFormat | string;
  /** 模态；老数据可能缺，前端按 "text" 兜底 */
  modality?: LLMModality | string;
  /** 路由标签；老数据可能为空数组 */
  tags?: string[];
  /** 1=便宜 / 2=中 / 3=旗舰；老数据按 2 兜底 */
  cost_tier?: number;
  /** 运维备注 */
  notes?: string | null;
  /** 出口代理 id；null = 直连（DIRECT） */
  proxy_id?: number | null;
  /** 候选模型清单 */
  models?: ProviderModel[];
  created_at: string;
}

export interface LLMProviderCreate {
  name: string;
  provider: LLMProviderKind;
  /** 空 / undefined → 不设；下发后由后端 Fernet 加密 */
  api_key?: string | null;
  base_url?: string | null;
  default_model: string;
  api_format?: LLMApiFormat;
  modality?: LLMModality;
  tags?: string[];
  cost_tier?: number;
  notes?: string | null;
  /** 出口代理；不传 / null = 直连 */
  proxy_id?: number | null;
  /** 候选模型清单；通常新建时留空，建完用"Fetch 模型列表"按钮自动填 */
  models?: ProviderModel[];
}

/**
 * PATCH provider；api_key 行为：
 * - 缺省 / undefined → 不动
 * - "" 空串       → 清空
 * - 非空字符串    → 替换并加密
 *
 * 路由字段（modality / tags / cost_tier / notes）：缺省 / undefined = 不动。
 *
 * proxy 切换语义：
 * - 想换成另一条 proxy：``proxy_id: <id>``，``clear_proxy`` 不传或 false
 * - 想切回 DIRECT（不走代理）：``clear_proxy: true``，``proxy_id`` 可不传
 * - 不动：两个都不传
 */
export interface LLMProviderUpdate {
  name?: string;
  provider?: LLMProviderKind;
  api_key?: string | null;
  base_url?: string | null;
  default_model?: string;
  api_format?: LLMApiFormat;
  modality?: LLMModality;
  tags?: string[];
  cost_tier?: number;
  notes?: string | null;
  proxy_id?: number | null;
  clear_proxy?: boolean;
  /** 整体替换式 PATCH——给 list（含空 list）就覆盖；undefined = 不动 */
  models?: ProviderModel[];
}

/** ``POST /api/commands/llm-providers/{pid}/fetch-models`` 出参 */
export interface FetchModelsResponse {
  /** 从 ``GET {base_url}/models`` 拉到的模型条数 */
  fetched: number;
  /** 合并后最新 provider 出参 */
  provider: LLMProviderOut;
}

/** ``POST /api/commands/llm-providers/{pid}/test-model`` 入参 */
export interface TestModelRequest {
  model: string;
}

/** ``POST /api/commands/llm-providers/{pid}/test-model`` 出参 */
export interface TestModelResponse {
  ok: boolean;
  /** 总耗时（毫秒） */
  latency_ms: number;
  /** API 实际返回的 model 名（可能带日期后缀） */
  model?: string | null;
  /** 返回 text 的前 80 字符；让用户一眼看出"模型确实回话了" */
  preview?: string | null;
  /** 失败时的错误消息（已脱敏） */
  error?: string | null;
}

===== frontend/src/components/AccountAvatar.tsx =====
// 圆形账号头像：尝试加载后端 /avatar 接口；
// - 失败（404 / worker 离线 / 账号无头像）→ 渲染首字母 + 基于 ID 的稳定背景色
// - 加载成功 → 渲染图片（object-cover 保证圆形不变形）
import { useState, useMemo } from "react";

import { avatarUrl } from "@/api/accounts";
import { cn } from "@/lib/utils";

interface AccountAvatarProps {
  /** 账号 ID（系统 PK，不是 TG user id） */
  id: number;
  /** 显示名 / @用户名，用于决定首字母 fallback；都为空时回落到 # */
  name?: string | null;
  /** 用户名，作为 name 的次选 */
  username?: string | null;
  /** 像素尺寸，默认 32（h-8 w-8） */
  size?: number;
  className?: string;
}

// 8 个柔和背景色，按 id 取模分配；保证同一账号始终拿到同一颜色
const PALETTE = [
  "bg-rose-200 text-rose-800",
  "bg-orange-200 text-orange-800",
  "bg-amber-200 text-amber-800",
  "bg-emerald-200 text-emerald-800",
  "bg-sky-200 text-sky-800",
  "bg-indigo-200 text-indigo-800",
  "bg-fuchsia-200 text-fuchsia-800",
  "bg-slate-200 text-slate-800",
];

export function AccountAvatar({
  id,
  name,
  username,
  size = 32,
  className,
}: AccountAvatarProps) {
  // 头像是否加载失败：失败一次后切到首字母，不再无脑重试
  const [failed, setFailed] = useState(false);

  // 取首字母：display_name 优先，其次 @username，最后回落 "#"
  const initial = useMemo(() => {
    const src = (name && name.trim()) || (username && username.trim()) || "";
    if (!src) return "#";
    // 取第一个可显示字符（兼容中文 / emoji surrogate pair）
    const codePoint = src.codePointAt(0);
    return codePoint ? String.fromCodePoint(codePoint).toUpperCase() : "#";
  }, [name, username]);

  const colorClass = PALETTE[id % PALETTE.length];

  const style = { width: size, height: size, fontSize: Math.round(size * 0.42) };

  if (failed) {
    return (
      <div
        className={cn(
          "shrink-0 inline-flex items-center justify-center rounded-full font-medium select-none",
          colorClass,
          className,
        )}
        style={style}
        aria-label={name || username || `账号 ${id}`}
      >
        {initial}
      </div>
    );
  }

  return (
    <img
      src={avatarUrl(id)}
      alt={name || username || `账号 ${id}`}
      width={size}
      height={size}
      style={style}
      onError={() => setFailed(true)}
      // referrerPolicy 与 crossOrigin 用默认值即可：同源 + cookie 已自动带上
      className={cn(
        "shrink-0 inline-block rounded-full object-cover bg-muted",
        className,
      )}
    />
  );
}

===== frontend/src/components/AccountStatusBadge.tsx =====
// 账号状态 → 中文标签 + Badge 颜色
//
// 复合状态：当全局 kill switch 开启时，所有账号的"运行中"应该被覆盖为"总闸暂停"，
// 否则用户看到 banner 红条但单条账号还是绿色，会困惑。badge 通过 react-query
// 直接查 ["system","kill-switch"] cache，跟 GlobalAlertBar / KillSwitch 共享数据。
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type { AccountStatus } from "@/api/types";

const MAP: Record<
  AccountStatus,
  { text: string; variant: "success" | "warn" | "destructive" | "secondary" }
> = {
  active: { text: "运行中", variant: "success" },
  paused: { text: "已暂停", variant: "secondary" },
  floodwait: { text: "FloodWait", variant: "warn" },
  dead: { text: "异常", variant: "destructive" },
  login_required: { text: "待重登", variant: "warn" },
};

interface KillSwitchState {
  enabled: boolean;
}

async function fetchKill(): Promise<KillSwitchState> {
  const { data } = await api.get<KillSwitchState>("/api/system/kill-switch");
  return data;
}

export function AccountStatusBadge({ status }: { status: AccountStatus }) {
  // 共享 cache key；GlobalAlertBar / KillSwitch 都用同一个 query，避免重复请求
  const { data: kill } = useQuery({
    queryKey: ["system", "kill-switch"],
    queryFn: fetchKill,
    refetchInterval: 30_000,
  });

  // 总闸开 + 当前 active → 显示为"总闸暂停"，引导用户去顶部恢复
  if (kill?.enabled && status === "active") {
    return (
      <Badge variant="destructive" title="全局总闸已开启，所有账号已被暂停">
        总闸暂停
      </Badge>
    );
  }

  const cfg = MAP[status] ?? { text: status, variant: "secondary" as const };
  return <Badge variant={cfg.variant}>{cfg.text}</Badge>;
}

===== frontend/src/components/AccountSummaryCard.tsx =====
// 账号概要卡：在概览页 / 账号列表页共用
//  - 显示：头像、显示名、状态徽章、@用户名、TG 数字 ID、手机号（默认遮掩，点击切换显示）
//  - 移动端单列、每条信息一行，避免横向挤压
//  - footer 可由调用方覆盖，用于列表页放置启停 / 删除等操作
import { Link } from "react-router-dom";
import { AtSign, Hash } from "lucide-react";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { AccountAvatar } from "@/components/AccountAvatar";
import { AccountStatusBadge } from "@/components/AccountStatusBadge";
import { MaskedPhone } from "@/components/MaskedPhone";
import { cn } from "@/lib/utils";
import type { AccountSummary } from "@/api/types";

interface AccountSummaryCardProps {
  account: AccountSummary;
  /**
   * 自定义页脚（操作区或额外信息）。不传则显示默认的"已启用 X 项功能 / 详情 →"。
   * 传入空 fragment / null 则不渲染页脚区。
   */
  footer?: React.ReactNode;
  /** 显示名是否作为详情链接，默认 true */
  linkToDetail?: boolean;
  className?: string;
}

export function AccountSummaryCard({
  account,
  footer,
  linkToDetail = true,
  className,
}: AccountSummaryCardProps) {
  const titleText = account.display_name || `#${account.id}`;

  return (
    <Card className={cn("transition-shadow hover:shadow-md", className)}>
      <CardHeader className="space-y-2.5 pb-3">
        {/* 标题行：头像 + 显示名 + 状态徽章 */}
        <div className="flex items-start gap-3">
          <AccountAvatar
            id={account.id}
            name={account.display_name}
            username={account.tg_username}
            size={40}
          />
          <div className="flex min-w-0 flex-1 items-start justify-between gap-2">
            {linkToDetail ? (
              <Link
                to={`/accounts/${account.id}`}
                className="min-w-0 flex-1 truncate text-base font-medium hover:underline"
              >
                {titleText}
              </Link>
            ) : (
              <span className="min-w-0 flex-1 truncate text-base font-medium">
                {titleText}
              </span>
            )}
            <div className="shrink-0">
              <AccountStatusBadge status={account.status} />
            </div>
          </div>
        </div>

        {/* 元信息：每行一条，移动端不会被挤压 */}
        <div className="space-y-1.5 text-xs text-muted-foreground">
          {account.tg_username ? (
            <InfoRow icon={AtSign} mono>
              {account.tg_username}
            </InfoRow>
          ) : null}
          {account.tg_user_id != null ? (
            <InfoRow icon={Hash} mono>
              {account.tg_user_id}
            </InfoRow>
          ) : null}
          <MaskedPhone phone={account.phone} />
        </div>
      </CardHeader>
      {footer === undefined ? (
        <CardContent className="flex items-center justify-between pt-0 text-xs text-muted-foreground">
          <span>已启用 {account.enabled_features} 项功能</span>
          {linkToDetail ? (
            <Link
              to={`/accounts/${account.id}`}
              className="text-primary hover:underline"
            >
              详情 →
            </Link>
          ) : null}
        </CardContent>
      ) : footer === null ? null : (
        <CardContent className="pt-0">{footer}</CardContent>
      )}
    </Card>
  );
}

// ── 子组件 ────────────────────────────────────────────────────────────

function InfoRow({
  icon: Icon,
  mono,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  mono?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className={cn("truncate", mono && "font-mono")}>{children}</span>
    </div>
  );
}

===== frontend/src/components/LineTrend.tsx =====
// ECharts 折线图：极简包装，按需要传 series
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  CanvasRenderer,
]);

interface SeriesItem {
  name: string;
  data: number[];
  color?: string;
}

interface LineTrendProps {
  xAxis: string[];
  series: SeriesItem[];
  height?: number;
}

export function LineTrend({ xAxis, series, height = 240 }: LineTrendProps) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    inst.current = echarts.init(ref.current);
    const onResize = () => inst.current?.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      inst.current?.dispose();
      inst.current = null;
    };
  }, []);

  useEffect(() => {
    if (!inst.current) return;
    inst.current.setOption({
      tooltip: { trigger: "axis" },
      legend: { top: 0, textStyle: { color: "#888" } },
      grid: { left: 30, right: 16, top: 36, bottom: 24 },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: xAxis,
        axisLine: { lineStyle: { color: "#888" } },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { type: "dashed", color: "#e5e7eb" } },
      },
      series: series.map((s) => ({
        name: s.name,
        type: "line",
        smooth: true,
        showSymbol: false,
        data: s.data,
        lineStyle: s.color ? { color: s.color } : undefined,
        itemStyle: s.color ? { color: s.color } : undefined,
      })),
    });
  }, [xAxis, series]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}

===== frontend/src/components/MaskedPhone.tsx =====
// 手机号码遮掩 + 点击切换显示。
//
// 遮掩规则：保留 + 号和国家代码（地区码），其后所有数字位换成 *。
//   "+8613812345678"  →  "+86***********"
//   "+15555551234"    →  "+1**********"
//   "+447911123456"   →  "+44**********"
//   "+8521234567"     →  "+852*******"
//
// 国家代码长度不固定（1 / 2 / 3 位），用 ITU 公开的 E.164 表中常见的 1 位
// （+1 NANP，+7 俄/哈）和 3 位前缀做白名单匹配，其余一律按 2 位处理。
import { useMemo, useState } from "react";
import { Eye, EyeOff, Phone } from "lucide-react";

import { cn } from "@/lib/utils";

// ── 遮掩逻辑 ───────────────────────────────────────────────────────

const COUNTRY_CODES_1 = new Set(["1", "7"]);

// E.164 中明确的 3 位国家代码集合（覆盖绝大多数；不在表里的按 2 位处理）
const COUNTRY_CODES_3 = new Set([
  // 非洲
  "212","213","216","218","220","221","222","223","224","225","226","227","228",
  "229","230","231","232","233","234","235","236","237","238","239","240","241",
  "242","243","244","245","246","247","248","249","250","251","252","253","254",
  "255","256","257","258","260","261","262","263","264","265","266","267","268",
  "269","290","291","297","298","299",
  // 欧洲
  "350","351","352","353","354","355","356","357","358","359","370","371","372",
  "373","374","375","376","377","378","380","381","382","383","385","386","387",
  "389","420","421","423",
  // 美洲（非 +1 NANP 部分）
  "500","501","502","503","504","505","506","507","508","509","590","591","592",
  "593","594","595","596","597","598","599",
  // 亚洲 / 中东 / 太平洋
  "670","672","673","674","675","676","677","678","679","680","681","682","683",
  "685","686","687","688","689","690","691","692",
  "850","852","853","855","856","880","886",
  "960","961","962","963","964","965","966","967","968","970","971","972","973",
  "974","975","976","977",
  "992","993","994","995","996","998",
]);

function getCountryCodeLength(digits: string): number {
  if (digits.length === 0) return 0;
  if (COUNTRY_CODES_1.has(digits[0])) return 1;
  if (digits.length >= 3 && COUNTRY_CODES_3.has(digits.slice(0, 3))) return 3;
  return Math.min(2, digits.length);
}

export function maskPhone(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";

  const hasPlus = trimmed.startsWith("+");
  const digits = trimmed.replace(/\D/g, "");
  if (digits.length === 0) return "*".repeat(trimmed.length);

  const ccLen = getCountryCodeLength(digits);
  const cc = digits.slice(0, ccLen);
  const tail = digits.length - ccLen;
  return (hasPlus ? "+" : "") + cc + "*".repeat(tail);
}

// ── 组件 ───────────────────────────────────────────────────────────

interface MaskedPhoneProps {
  phone: string;
  className?: string;
  /** 自定义图标尺寸 class，默认 h-3.5 w-3.5（卡片用）。详情页可传更大 */
  iconClassName?: string;
}

export function MaskedPhone({ phone, className, iconClassName }: MaskedPhoneProps) {
  const [shown, setShown] = useState(false);
  const masked = useMemo(() => maskPhone(phone), [phone]);

  return (
    <button
      type="button"
      onClick={() => setShown((s) => !s)}
      className={cn(
        "group inline-flex max-w-full items-center gap-1.5 rounded-sm",
        "text-left text-muted-foreground transition-colors hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      aria-label={shown ? "点击隐藏手机号" : "点击显示完整手机号"}
    >
      <Phone className={cn("shrink-0", iconClassName ?? "h-3.5 w-3.5")} />
      <span className="truncate font-mono">{shown ? phone : masked}</span>
      {shown ? (
        <EyeOff className="h-3 w-3 shrink-0 opacity-50 transition-opacity group-hover:opacity-100" />
      ) : (
        <Eye className="h-3 w-3 shrink-0 opacity-50 transition-opacity group-hover:opacity-100" />
      )}
    </button>
  );
}

===== frontend/src/components/NetworkBadge.tsx =====
// 网络环境徽章：显示当前后端进程出口 IP 的国家/地区，hover 看详情
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Globe2, Loader2, RefreshCw, AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getNetworkInfo, refreshNetworkInfo } from "@/api/network";
import { cn } from "@/lib/utils";

// 把国家代码 → emoji 国旗（仅 ISO-2 码）。失败回退 🌐
function flagOf(country?: string | null): string {
  if (!country || country.length !== 2) return "🌐";
  const cp = (s: string) => 0x1f1e6 + (s.toUpperCase().charCodeAt(0) - 65);
  try {
    return String.fromCodePoint(cp(country[0]), cp(country[1]));
  } catch {
    return "🌐";
  }
}

export function NetworkBadge() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["system", "network"],
    queryFn: getNetworkInfo,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });
  const refreshMut = useMutation({
    mutationFn: refreshNetworkInfo,
    onSuccess: (d) => qc.setQueryData(["system", "network"], d),
  });

  const data = q.data;
  const flag = flagOf(data?.country);
  const hasError = !!data?.error || (!q.isLoading && !data?.ip);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 gap-1 px-2 text-xs"
          title="当前后端出口网络环境"
        >
          {q.isLoading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : hasError ? (
            <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
          ) : (
            <span className="text-base leading-none">{flag}</span>
          )}
          <span
            className={cn(
              "font-mono",
              hasError && "text-amber-700",
            )}
          >
            {q.isLoading
              ? "探测中"
              : hasError
                ? "未知"
                : data?.country || "?"}
          </span>
          {!hasError && data?.ip ? (
            <span className="text-muted-foreground hidden sm:inline">
              · {data.ip}
            </span>
          ) : null}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[260px] p-3">
        <div className="space-y-2 text-xs">
          <div className="flex items-center gap-2 border-b pb-2">
            <Globe2 className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">后端出口网络环境</span>
          </div>
          {hasError ? (
            <div className="space-y-1">
              <div className="text-amber-700">⚠ 探测失败</div>
              <div className="text-muted-foreground break-all">
                {data?.error || "未拿到出口 IP（可能后端无外网）"}
              </div>
            </div>
          ) : (
            <dl className="grid grid-cols-[64px_1fr] gap-y-1.5">
              <dt className="text-muted-foreground">IP</dt>
              <dd className="font-mono">{data?.ip || "-"}</dd>
              <dt className="text-muted-foreground">国家</dt>
              <dd>
                {flag} {data?.country || "-"}
              </dd>
              <dt className="text-muted-foreground">地区</dt>
              <dd>{data?.region || "-"}</dd>
              <dt className="text-muted-foreground">城市</dt>
              <dd>{data?.city || "-"}</dd>
              <dt className="text-muted-foreground">ISP</dt>
              <dd className="break-all">{data?.org || "-"}</dd>
              <dt className="text-muted-foreground">缓存</dt>
              <dd className="text-muted-foreground">
                {data?.fresh ? "本次新拉" : "5min 缓存"}
              </dd>
            </dl>
          )}
          <div className="flex items-center justify-between border-t pt-2">
            <span className="text-muted-foreground">
              这是后端进程的直连出口；
              <br />
              账号 worker 走绑定代理时不同
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 text-xs"
              disabled={refreshMut.isPending}
              onClick={() => refreshMut.mutate()}
            >
              <RefreshCw
                className={cn(
                  "h-3 w-3",
                  refreshMut.isPending && "animate-spin",
                )}
              />
              刷新
            </Button>
          </div>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

===== frontend/src/components/SystemHealthCard.tsx =====
// 系统健康概览卡片：DB / alembic / Redis / providers / proxies / workers
// 数据来自 GET /api/system/health-overview；30s 自动刷新一次（前端轻量轮询）
//
// 设计：
//   - 顶部一排小绿点/红点，一眼看出"系统是否健康"
//   - 每块下方铺细节统计；alembic 不同步时高亮提示用户跑 `alembic upgrade head`
//   - 任一项失败不影响其他项；后端聚合接口已带 2s 超时
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/misc";
import { getHealthOverview } from "@/api/system";
import type { HealthOverview } from "@/api/types";

// 后端 account.status 枚举的中文标签
const ACCOUNT_STATUS_LABEL: Record<string, string> = {
  active: "运行中",
  paused: "已暂停",
  floodwait: "限流中",
  dead: "已停用",
  login_required: "需重登",
};

// 颜色映射：单字段 ok 给绿/红 dot，alembic 失同步给黄
type Tone = "ok" | "warn" | "err";
function Dot({ tone }: { tone: Tone }) {
  const cls = {
    ok: "bg-emerald-500",
    warn: "bg-amber-500",
    err: "bg-rose-500",
  }[tone];
  return <span className={`inline-block h-2 w-2 rounded-full ${cls}`} />;
}

export function SystemHealthCard() {
  const q = useQuery({
    queryKey: ["system", "health-overview"],
    queryFn: getHealthOverview,
    // 自动刷新；后台变化（如 worker 上线 / alembic 跑完）几十秒内可见
    refetchInterval: 30_000,
    // 切换 tab 回来时立刻刷一次
    refetchOnWindowFocus: true,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">系统状态</CardTitle>
      </CardHeader>
      <CardContent>
        {q.isLoading ? (
          <div className="flex h-24 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : q.error || !q.data ? (
          <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
            读取失败：{(q.error as Error)?.message || "未知错误"}
          </div>
        ) : (
          <HealthGrid data={q.data} />
        )}
      </CardContent>
    </Card>
  );
}

function HealthGrid({ data }: { data: HealthOverview }) {
  const dbTone: Tone = data.db.ok ? "ok" : "err";
  const redisTone: Tone = data.redis.ok ? "ok" : "err";
  const alembicTone: Tone = data.alembic.ok
    ? "ok"
    : data.alembic.error
    ? "err"
    : "warn";

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {/* DB */}
      <HealthBlock title="PostgreSQL" tone={dbTone}>
        {data.db.ok ? (
          <div className="text-xs text-muted-foreground">
            {data.db.version || "已连接"}
          </div>
        ) : (
          <div className="text-xs text-rose-700">{data.db.error || "连接失败"}</div>
        )}
      </HealthBlock>

      {/* Alembic */}
      <HealthBlock
        title="数据库迁移（alembic）"
        tone={alembicTone}
        right={
          <Badge
            variant={data.alembic.ok ? "success" : "warn"}
            className="font-mono text-xs"
          >
            {data.alembic.current || "?"}
            {data.alembic.head && data.alembic.head !== data.alembic.current
              ? ` → ${data.alembic.head}`
              : ""}
          </Badge>
        }
      >
        {data.alembic.ok ? (
          <div className="text-xs text-muted-foreground">
            DB 已升到代码 head（{data.alembic.head || "-"}），无待跑迁移
          </div>
        ) : data.alembic.error ? (
          <div className="text-xs text-rose-700">探测失败：{data.alembic.error}</div>
        ) : (
          <div className="space-y-1">
            <div className="text-xs text-amber-700">
              代码期望 <code>{data.alembic.head}</code>，DB 当前
              <code> {data.alembic.current || "（无）"}</code>。
            </div>
            {data.alembic.pending.length > 0 && (
              <div className="text-xs text-muted-foreground">
                待跑：
                {data.alembic.pending.map((p) => (
                  <Badge
                    key={p}
                    variant="outline"
                    className="ml-1 font-mono text-xs"
                  >
                    {p}
                  </Badge>
                ))}
              </div>
            )}
            <div className="text-xs">
              修复：在 backend 跑 <code>alembic upgrade head</code>{" "}
              <span className="text-muted-foreground">
                （或 <code>make migrate</code>；默认下次 backend 重启会自动升）
              </span>
            </div>
          </div>
        )}
      </HealthBlock>

      {/* Redis */}
      <HealthBlock title="Redis" tone={redisTone}>
        {data.redis.ok ? (
          <div className="text-xs text-muted-foreground">PING 正常</div>
        ) : (
          <div className="text-xs text-rose-700">
            {data.redis.error || "PING 失败"}
          </div>
        )}
      </HealthBlock>

      {/* LLM Providers */}
      <HealthBlock
        title={
          <Link to="/ai" className="hover:underline">
            LLM Provider
          </Link>
        }
        tone={
          data.providers.total === 0
            ? "warn"
            : data.providers.with_api_key < data.providers.total
            ? "warn"
            : "ok"
        }
        right={
          <Badge variant="secondary" className="text-xs">
            {data.providers.total} 条
          </Badge>
        }
      >
        {data.providers.total === 0 ? (
          <div className="text-xs text-muted-foreground">
            尚未配置；去 <Link to="/ai" className="underline">AI 设置</Link> 添加
          </div>
        ) : (
          <div className="space-y-1 text-xs text-muted-foreground">
            <div>
              已配 api_key：
              <Badge variant="outline" className="ml-1 text-xs">
                {data.providers.with_api_key}
              </Badge>
              {data.providers.with_api_key < data.providers.total && (
                <span className="ml-2 text-amber-700">
                  ⚠ {data.providers.total - data.providers.with_api_key} 条缺 key
                </span>
              )}
            </div>
            <div>
              走代理：
              <Badge variant="outline" className="ml-1 text-xs">
                {data.providers.with_proxy}
              </Badge>{" "}
              <span className="text-muted-foreground/80">
                · DIRECT {data.providers.total - data.providers.with_proxy}
              </span>
            </div>
            {Object.keys(data.providers.by_modality).length > 0 && (
              <div className="space-x-1">
                {Object.entries(data.providers.by_modality).map(([m, n]) => (
                  <Badge key={m} variant="outline" className="text-xs">
                    {m}:{n}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        )}
      </HealthBlock>

      {/* Proxies */}
      <HealthBlock
        title="代理库"
        tone={data.proxies.total === 0 ? "warn" : "ok"}
        right={
          <Badge variant="secondary" className="text-xs">
            {data.proxies.total} 条
          </Badge>
        }
      >
        {data.proxies.total === 0 ? (
          <div className="text-xs text-muted-foreground">
            尚未配置；如需翻墙调 LLM 去「系统设置 → 代理」加一条 socks5/http
          </div>
        ) : (
          <div className="space-y-1 text-xs text-muted-foreground">
            <div className="space-x-1">
              {Object.entries(data.proxies.by_type).map(([t, n]) => (
                <Badge key={t} variant="outline" className="text-xs">
                  {t}:{n}
                </Badge>
              ))}
            </div>
            <div>
              被 LLM 引用：
              <Badge variant="outline" className="ml-1 text-xs">
                {data.proxies.used_by_llm}
              </Badge>
            </div>
          </div>
        )}
      </HealthBlock>

      {/* Workers / 账号 */}
      <HealthBlock
        title={
          <Link to="/accounts" className="hover:underline">
            账号 worker
          </Link>
        }
        tone={
          data.workers.total === 0
            ? "warn"
            : (data.workers.by_status["dead"] ?? 0) > 0 ||
              (data.workers.by_status["login_required"] ?? 0) > 0
            ? "warn"
            : "ok"
        }
        right={
          <Badge variant="secondary" className="text-xs">
            {data.workers.total} 个
          </Badge>
        }
      >
        {data.workers.total === 0 ? (
          <div className="text-xs text-muted-foreground">
            尚未绑定任何账号
          </div>
        ) : (
          <div className="flex flex-wrap gap-1.5 text-xs">
            {Object.entries(data.workers.by_status)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([s, n]) => {
                const variant =
                  s === "active"
                    ? "success"
                    : s === "dead" || s === "login_required"
                    ? "warn"
                    : "outline";
                return (
                  <Badge key={s} variant={variant} className="text-xs">
                    {ACCOUNT_STATUS_LABEL[s] || s}: {n}
                  </Badge>
                );
              })}
          </div>
        )}
      </HealthBlock>
    </div>
  );
}

function HealthBlock({
  title,
  tone,
  right,
  children,
}: {
  title: React.ReactNode;
  tone: Tone;
  right?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border bg-card/50 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Dot tone={tone} />
          <span>{title}</span>
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

===== frontend/src/components/layout/AppShell.tsx =====
// 应用主框架：左侧 Sidebar（桌面）/ MobileSidebar（移动）+ 顶部 TopBar + 内容 outlet
// 高度用 100dvh：iOS Safari 浏览器模式下避免 100vh 把内容塞到地址栏后面；
//                PWA 全屏模式下行为与 100vh 一致。
import { useState } from "react";
import { Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { MobileSidebar, Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { GlobalAlertBar } from "./GlobalAlertBar";
import { fetchMe } from "@/lib/auth";
import { Spinner } from "@/components/ui/misc";

export function AppShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // 主体框架内顺手取一次当前用户用于顶栏展示
  const { data, isLoading } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: fetchMe,
  });

  if (isLoading) {
    return (
      <div className="flex h-[100dvh] items-center justify-center">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  return (
    <div className="flex h-[100dvh] w-full overflow-hidden bg-background">
      <Sidebar />
      <MobileSidebar open={mobileNavOpen} onOpenChange={setMobileNavOpen} />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <TopBar
          username={data?.username ?? "未知用户"}
          onMenuClick={() => setMobileNavOpen(true)}
        />
        {/* kill switch 开启时显示全局红色横幅；关闭时不渲染 */}
        <GlobalAlertBar />
        <main
          className="
            flex-1 overflow-auto
            p-4 md:p-6
            pb-[max(1rem,env(safe-area-inset-bottom))]
            pl-[max(1rem,env(safe-area-inset-left))]
            pr-[max(1rem,env(safe-area-inset-right))]
            md:pl-6 md:pr-6
          "
        >
          <Outlet />
        </main>
      </div>
    </div>
  );
}

===== frontend/src/components/layout/GlobalAlertBar.tsx =====
// 全局横幅：当 kill switch 开启时，在 TopBar 下方显示一条红色警示条
//
// 设计：
//  - 单独组件，不耦合 TopBar；放在 AppShell 内顶端
//  - 与 TopBar 的 KillSwitch 按钮共享 react-query cache key（"system","kill-switch"），
//    所以点 KillSwitch 切换会立即同步生效，不会出现按钮变了横幅没变的不一致
//  - 30s 兜底轮询，跟 KillSwitch 一致
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, getErrMsg } from "@/lib/api";

interface KillSwitchState {
  enabled: boolean;
}

async function fetchKillSwitch(): Promise<KillSwitchState> {
  const { data } = await api.get<KillSwitchState>("/api/system/kill-switch");
  return data;
}

export function GlobalAlertBar() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["system", "kill-switch"],
    queryFn: fetchKillSwitch,
    refetchInterval: 30_000,
  });

  const mut = useMutation({
    mutationFn: async () => {
      await api.post("/api/system/kill-switch", { enabled: false });
    },
    onSuccess: () => {
      toast.success("已恢复运行");
      qc.invalidateQueries({ queryKey: ["system", "kill-switch"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (!data?.enabled) return null;

  return (
    <div
      role="alert"
      className="
        flex items-center justify-between gap-3
        border-b border-destructive/40 bg-destructive/10 px-4 py-2
        text-sm text-destructive
      "
    >
      <div className="flex min-w-0 items-center gap-2">
        <ShieldAlert className="h-4 w-4 shrink-0" />
        <span className="font-medium">全局总闸已开启</span>
        <span className="hidden text-muted-foreground sm:inline">
          所有账号 worker 已暂停，仅保留接收
        </span>
      </div>
      <Button
        size="sm"
        variant="outline"
        className="shrink-0"
        disabled={mut.isPending}
        onClick={() => {
          if (confirm("确认恢复全部账号运行？")) mut.mutate();
        }}
      >
        恢复运行
      </Button>
    </div>
  );
}

===== frontend/src/components/layout/KillSwitch.tsx =====
// 顶部紧急停用按钮：调 POST /api/system/kill-switch 切换全局总闸
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, getErrMsg } from "@/lib/api";

interface KillSwitchState {
  enabled: boolean;
}

async function fetchKillSwitch(): Promise<KillSwitchState> {
  const { data } = await api.get<KillSwitchState>("/api/system/kill-switch");
  return data;
}

export function KillSwitch() {
  const qc = useQueryClient();
  // 实时显示总闸状态；轻量轮询：30s 刷新
  const { data } = useQuery({
    queryKey: ["system", "kill-switch"],
    queryFn: fetchKillSwitch,
    refetchInterval: 30_000,
  });
  const enabled = !!data?.enabled;

  const mut = useMutation({
    mutationFn: async (next: boolean) => {
      await api.post("/api/system/kill-switch", { enabled: next });
    },
    onSuccess: (_, next) => {
      toast.success(next ? "已开启紧急停用：所有 worker 已暂停" : "已恢复运行");
      qc.invalidateQueries({ queryKey: ["system", "kill-switch"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Button
      variant={enabled ? "outline" : "destructive"}
      size="sm"
      onClick={() => {
        if (mut.isPending) return;
        const next = !enabled;
        if (next && !confirm("确认要紧急停用所有账号？所有 worker 立即暂停。")) return;
        mut.mutate(next);
      }}
    >
      {enabled ? (
        <>
          <ShieldCheck className="mr-1 h-4 w-4" /> 恢复运行
        </>
      ) : (
        <>
          <ShieldAlert className="mr-1 h-4 w-4" /> 紧急停用
        </>
      )}
    </Button>
  );
}

===== frontend/src/components/layout/RequireAuth.tsx =====
// 路由级守卫：调用一次 /api/auth/me，401 跳 /login
import { Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchMe } from "@/lib/auth";
import { Spinner } from "@/components/ui/misc";

export function RequireAuth() {
  const loc = useLocation();
  const { isLoading, isError } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: fetchMe,
    // 401 在 axios 拦截器里会触发跳转；这里仅根据 error 渲染兜底
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  if (isError) {
    // axios 拦截器已处理跳转，这里展示一个最小占位
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
        正在跳转到登录…（{loc.pathname}）
      </div>
    );
  }

  return <Outlet />;
}

===== frontend/src/components/layout/Sidebar.tsx =====
// 左侧导航：
//  - <Sidebar> 桌面端（≥md）常驻显示
//  - <MobileSidebar> 移动端通过抽屉模式呈现（Radix Dialog 实现，左侧滑入）
// 两者共享 NavList，移动端点击导航后自动关闭抽屉。
import { NavLink } from "react-router-dom";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  LayoutDashboard,
  Users,
  Grid3x3,
  Cog,
  ScrollText,
  Puzzle,
  Sparkles,
  LayoutTemplate,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { APP_VERSION_LABEL } from "@/lib/version";

interface NavItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  end?: boolean;
}

// 顶层导航条目；功能配置入口由「功能矩阵」承担（点格子进入 [账号×功能] 配置）
const NAV: NavItem[] = [
  { to: "/", label: "概览", icon: LayoutDashboard, end: true },
  { to: "/accounts", label: "账号管理", icon: Users },
  { to: "/matrix", label: "功能矩阵", icon: Grid3x3 },
  { to: "/templates", label: "通用模板", icon: LayoutTemplate },
  { to: "/plugins", label: "插件管理", icon: Puzzle },
  { to: "/ai", label: "AI 设置", icon: Sparkles },
  { to: "/logs", label: "日志中心", icon: ScrollText },
  { to: "/settings", label: "系统设置", icon: Cog },
];

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex-1 space-y-1 overflow-y-auto p-3 text-sm">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2 rounded-md px-3 py-2 text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              isActive && "bg-accent text-accent-foreground",
            )
          }
        >
          <item.icon className="h-4 w-4" />
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      <div className="flex h-14 shrink-0 items-center border-b px-4 text-base font-semibold">
        Telegram Userbot
      </div>
      <NavList onNavigate={onNavigate} />
      <div className="shrink-0 border-t p-3 text-xs text-muted-foreground">
        {APP_VERSION_LABEL}
      </div>
    </>
  );
}

// 桌面常驻侧栏：< md 隐藏，由 MobileSidebar 接管
export function Sidebar() {
  return (
    <aside className="hidden w-56 shrink-0 flex-col border-r bg-card md:flex">
      <SidebarBody />
    </aside>
  );
}

interface MobileSidebarProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// 移动端抽屉：从左滑入。点击导航链接自动关闭；点击遮罩 / Esc / 关闭按钮也会关闭。
// 动画用纯 CSS transition（不依赖 tailwindcss-animate 插件）。
export function MobileSidebar({ open, onOpenChange }: MobileSidebarProps) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-black/60 transition-opacity duration-200 md:hidden",
            "data-[state=closed]:opacity-0 data-[state=open]:opacity-100",
          )}
        />
        <DialogPrimitive.Content
          className={cn(
            "fixed inset-y-0 left-0 z-50 flex w-64 max-w-[80vw] flex-col border-r bg-card shadow-lg md:hidden",
            // 安全区适配：iPhone 横屏时左侧刘海，全屏 PWA 顶/底状态栏区
            "pl-[env(safe-area-inset-left)] pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]",
            "transition-transform duration-200 ease-out",
            "data-[state=closed]:-translate-x-full data-[state=open]:translate-x-0",
          )}
          // 屏幕阅读器需要 Title；视觉上隐藏
          aria-describedby={undefined}
        >
          <DialogPrimitive.Title className="sr-only">导航菜单</DialogPrimitive.Title>
          <DialogPrimitive.Close
            className="absolute right-2 top-[calc(env(safe-area-inset-top)+0.5rem)] rounded-sm p-1 text-muted-foreground hover:text-foreground"
            aria-label="关闭菜单"
          >
            <X className="h-4 w-4" />
          </DialogPrimitive.Close>
          <SidebarBody onNavigate={() => onOpenChange(false)} />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

===== frontend/src/components/layout/TopBar.tsx =====
// 顶栏：移动端汉堡按钮 + 副标题（仅 sm+ 显示）+ 网络环境徽章 + 紧急停用 + 登出
// iOS PWA：背景色延伸到 safe-area-inset-top（与 black-translucent 状态栏配合），
// 内容区高度仍维持 56px。
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LogOut, Menu, UserCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { logout } from "@/lib/auth";
import { NetworkBadge } from "@/components/NetworkBadge";
import { KillSwitch } from "./KillSwitch";

interface TopBarProps {
  username: string;
  onMenuClick: () => void;
}

export function TopBar({ username, onMenuClick }: TopBarProps) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const mut = useMutation({
    mutationFn: logout,
    onSettled: () => {
      qc.clear();
      nav("/login", { replace: true });
    },
  });

  return (
    <header
      className="
        flex shrink-0 items-center justify-between border-b bg-card
        h-[calc(3.5rem+env(safe-area-inset-top))]
        pt-[env(safe-area-inset-top)]
        pl-[max(1rem,env(safe-area-inset-left))]
        pr-[max(1rem,env(safe-area-inset-right))]
      "
    >
      <div className="flex min-w-0 items-center gap-2">
        {/* 移动端汉堡按钮，桌面隐藏 */}
        <Button
          variant="ghost"
          size="icon"
          className="md:hidden"
          onClick={onMenuClick}
          aria-label="打开导航菜单"
        >
          <Menu className="h-5 w-5" />
        </Button>
        <div className="hidden truncate text-sm text-muted-foreground sm:block">
          多账号 Telegram userbot 管理控制台
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1 sm:gap-2">
        <NetworkBadge />
        <KillSwitch />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="max-w-[8rem]">
              <UserCircle className="mr-1 h-4 w-4 shrink-0" />
              <span className="truncate">{username}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem disabled>已登录账号</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => mut.mutate()}>
              <LogOut className="mr-2 h-4 w-4" /> 退出登录
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

===== frontend/src/components/ui/badge.tsx =====
// 状态/标签徽章
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        success: "border-transparent bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
        warn: "border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-300",
        destructive: "border-transparent bg-destructive/15 text-destructive",
        outline: "text-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

===== frontend/src/components/ui/button.tsx =====
// 按钮组件：variant + size，参考 shadcn/ui
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive:
          "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        outline:
          "border border-input bg-background hover:bg-accent hover:text-accent-foreground",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 rounded-md px-3",
        lg: "h-11 rounded-md px-8",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";
export { buttonVariants };

===== frontend/src/components/ui/card.tsx =====
// 卡片组件集合
import * as React from "react";
import { cn } from "@/lib/utils";

export const Card = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "rounded-lg border bg-card text-card-foreground shadow-sm",
      className,
    )}
    {...props}
  />
));
Card.displayName = "Card";

export const CardHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("flex flex-col space-y-1.5 p-6", className)}
    {...props}
  />
));
CardHeader.displayName = "CardHeader";

export const CardTitle = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("text-lg font-semibold leading-none tracking-tight", className)}
    {...props}
  />
));
CardTitle.displayName = "CardTitle";

export const CardDescription = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("text-sm text-muted-foreground", className)}
    {...props}
  />
));
CardDescription.displayName = "CardDescription";

export const CardContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("p-6 pt-0", className)} {...props} />
));
CardContent.displayName = "CardContent";

export const CardFooter = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("flex items-center p-6 pt-0", className)}
    {...props}
  />
));
CardFooter.displayName = "CardFooter";

===== frontend/src/components/ui/dialog.tsx =====
// Dialog 对话框，封装 radix-ui
import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogPortal = DialogPrimitive.Portal;
export const DialogClose = DialogPrimitive.Close;

export const DialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-50 bg-black/60 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className,
    )}
    {...props}
  />
));
DialogOverlay.displayName = "DialogOverlay";

export const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <DialogPortal>
    <DialogOverlay />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        "fixed left-[50%] top-[50%] z-50 grid w-full max-w-lg translate-x-[-50%] translate-y-[-50%] gap-4 border bg-background p-6 shadow-lg sm:rounded-lg",
        className,
      )}
      {...props}
    >
      {children}
      <DialogPrimitive.Close className="absolute right-4 top-4 rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-none">
        <X className="h-4 w-4" />
        <span className="sr-only">关闭</span>
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPortal>
));
DialogContent.displayName = "DialogContent";

export const DialogHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex flex-col space-y-1.5 text-left", className)} {...props} />
);
DialogHeader.displayName = "DialogHeader";

export const DialogFooter = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2", className)} {...props} />
);
DialogFooter.displayName = "DialogFooter";

export const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn("text-lg font-semibold leading-none tracking-tight", className)}
    {...props}
  />
));
DialogTitle.displayName = "DialogTitle";

export const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("text-sm text-muted-foreground", className)}
    {...props}
  />
));
DialogDescription.displayName = "DialogDescription";

===== frontend/src/components/ui/dropdown-menu.tsx =====
// DropdownMenu 下拉菜单，封装 radix-ui（仅暴露 MVP 用得到的子组件）
import * as React from "react";
import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import { cn } from "@/lib/utils";

export const DropdownMenu = DropdownPrimitive.Root;
export const DropdownMenuTrigger = DropdownPrimitive.Trigger;

export const DropdownMenuContent = React.forwardRef<
  React.ElementRef<typeof DropdownPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DropdownPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
  <DropdownPrimitive.Portal>
    <DropdownPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-50 min-w-[8rem] overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-md",
        className,
      )}
      {...props}
    />
  </DropdownPrimitive.Portal>
));
DropdownMenuContent.displayName = "DropdownMenuContent";

export const DropdownMenuItem = React.forwardRef<
  React.ElementRef<typeof DropdownPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof DropdownPrimitive.Item>
>(({ className, ...props }, ref) => (
  <DropdownPrimitive.Item
    ref={ref}
    className={cn(
      "relative flex cursor-default select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none transition-colors focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
      className,
    )}
    {...props}
  />
));
DropdownMenuItem.displayName = "DropdownMenuItem";

export const DropdownMenuSeparator = React.forwardRef<
  React.ElementRef<typeof DropdownPrimitive.Separator>,
  React.ComponentPropsWithoutRef<typeof DropdownPrimitive.Separator>
>(({ className, ...props }, ref) => (
  <DropdownPrimitive.Separator
    ref={ref}
    className={cn("-mx-1 my-1 h-px bg-muted", className)}
    {...props}
  />
));
DropdownMenuSeparator.displayName = "DropdownMenuSeparator";

===== frontend/src/components/ui/input.tsx =====
// 普通输入框
import * as React from "react";
import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

===== frontend/src/components/ui/label.tsx =====
// Label：表单标签，使用 radix 实现
import * as React from "react";
import * as LabelPrimitive from "@radix-ui/react-label";
import { cn } from "@/lib/utils";

export const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root
    ref={ref}
    className={cn(
      "text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70",
      className,
    )}
    {...props}
  />
));
Label.displayName = "Label";

===== frontend/src/components/ui/misc.tsx =====
// 占位/分割线小组件
import { cn } from "@/lib/utils";

export function Separator({ className }: { className?: string }) {
  return <div className={cn("h-px w-full bg-border", className)} />;
}

export function Spinner({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent",
        className,
      )}
    />
  );
}

===== frontend/src/components/ui/select.tsx =====
// 简单的 Select 组件（原生 select + tailwind 美化）
import * as React from "react";
import { cn } from "@/lib/utils";

export type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement>;

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  ),
);
Select.displayName = "Select";

===== frontend/src/components/ui/switch.tsx =====
// Switch 开关，封装 radix-ui
import * as React from "react";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";

export const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitive.Root
    ref={ref}
    className={cn(
      "peer inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-input",
      className,
    )}
    {...props}
  >
    <SwitchPrimitive.Thumb
      className={cn(
        "pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform data-[state=checked]:translate-x-5 data-[state=unchecked]:translate-x-0",
      )}
    />
  </SwitchPrimitive.Root>
));
Switch.displayName = "Switch";

===== frontend/src/components/ui/table.tsx =====
// 极简 Table 组件族
import * as React from "react";
import { cn } from "@/lib/utils";

export const Table = React.forwardRef<
  HTMLTableElement,
  React.HTMLAttributes<HTMLTableElement>
>(({ className, ...props }, ref) => (
  <div className="relative w-full overflow-auto">
    <table
      ref={ref}
      className={cn("w-full caption-bottom text-sm", className)}
      {...props}
    />
  </div>
));
Table.displayName = "Table";

export const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <thead ref={ref} className={cn("[&_tr]:border-b", className)} {...props} />
));
TableHeader.displayName = "TableHeader";

export const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tbody ref={ref} className={cn("[&_tr:last-child]:border-0", className)} {...props} />
));
TableBody.displayName = "TableBody";

export const TableRow = React.forwardRef<
  HTMLTableRowElement,
  React.HTMLAttributes<HTMLTableRowElement>
>(({ className, ...props }, ref) => (
  <tr
    ref={ref}
    className={cn("border-b transition-colors hover:bg-muted/50", className)}
    {...props}
  />
));
TableRow.displayName = "TableRow";

export const TableHead = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <th
    ref={ref}
    className={cn(
      "h-10 px-3 text-left align-middle font-medium text-muted-foreground",
      className,
    )}
    {...props}
  />
));
TableHead.displayName = "TableHead";

export const TableCell = React.forwardRef<
  HTMLTableCellElement,
  React.TdHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <td ref={ref} className={cn("p-3 align-middle", className)} {...props} />
));
TableCell.displayName = "TableCell";

===== frontend/src/components/ui/tabs.tsx =====
// Tabs 选项卡，封装 radix-ui
import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

export const Tabs = TabsPrimitive.Root;

export const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(
      "inline-flex h-10 items-center justify-center rounded-md bg-muted p-1 text-muted-foreground",
      className,
    )}
    {...props}
  />
));
TabsList.displayName = "TabsList";

export const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow",
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = "TabsTrigger";

export const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn("mt-2 ring-offset-background focus-visible:outline-none", className)}
    {...props}
  />
));
TabsContent.displayName = "TabsContent";

===== frontend/src/components/ui/textarea.tsx =====
// 多行文本输入
import * as React from "react";
import { cn } from "@/lib/utils";

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

===== frontend/src/lib/api.ts =====
// axios 客户端：携带 cookie；遇 401 自动跳登录页；统一错误信息提取
import axios, { type AxiosError } from "axios";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || "/",
  withCredentials: true,
  timeout: 15000,
});

api.interceptors.response.use(
  (r) => r,
  (err: AxiosError) => {
    const status = err.response?.status;
    if (status === 401 && !location.pathname.startsWith("/login")) {
      location.href = "/login";
    }
    return Promise.reject(err);
  },
);

// 后端错误统一形态：{ error: { code, message } }
type ApiErrorPayload = { error?: { code?: string; message?: string } };

export function getErrMsg(err: unknown): string {
  const e = err as AxiosError<ApiErrorPayload>;
  return e?.response?.data?.error?.message || e?.message || "请求失败";
}

export function getErrCode(err: unknown): string | undefined {
  const e = err as AxiosError<ApiErrorPayload>;
  return e?.response?.data?.error?.code;
}

===== frontend/src/lib/auth.ts =====
// 鉴权相关的 API 包装
import { api } from "./api";
import type { CurrentUser, LoginRequest, LoginResponse } from "@/api/types";

export async function fetchMe(): Promise<CurrentUser> {
  const { data } = await api.get<CurrentUser>("/api/auth/me");
  return data;
}

export async function login(payload: LoginRequest): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/api/auth/login", payload);
  return data;
}

export async function logout(): Promise<void> {
  await api.post("/api/auth/logout");
}

export async function register(username: string, password: string): Promise<void> {
  await api.post("/api/auth/register", { username, password });
}

===== frontend/src/lib/rate-actions.ts =====
// 风控动作（rate-limit action）的中文标签 + 一句话说明
//
// 18 个 key 与后端 services/rate_limit_service.py 的 `_DEFAULTS` 字典一一对齐。
// 改 key 名字之前先去后端确认；不要在这里发明后端不存在的 action。
//
// 用法：
//   import { actionLabel, actionHint } from "@/lib/rate-actions";
//   <span title={actionHint(r.action)}>{actionLabel(r.action)}</span>

export interface ActionInfo {
  /** 表格里显示的中文短标签（≤ 8 字） */
  label: string;
  /** 鼠标悬停 / 帮助行里的一句话说明（≤ 30 字） */
  hint: string;
}

export const ACTION_INFO: Record<string, ActionInfo> = {
  // ── 发消息 ────────────────────────────────
  send_message_private: {
    label: "私聊发消息",
    hint: "向单个用户/Bot 发送一条消息",
  },
  send_message_group: {
    label: "群里发消息",
    hint: "在群组或超级群中发送一条消息",
  },
  same_peer_send: {
    label: "同会话连发",
    hint: "短时间向同一个会话连续发送消息的频率上限",
  },

  // ── 编辑/删除 ─────────────────────────────
  edit_message: {
    label: "编辑消息",
    hint: "修改自己已发出的消息内容",
  },
  delete_message: {
    label: "删除消息",
    hint: "撤回/删除一条消息",
  },

  // ── 转发 ──────────────────────────────────
  forward_message: {
    label: "转发消息",
    hint: "把别处的消息原样转发到指定会话",
  },

  // ── 交互 ──────────────────────────────────
  callback_query: {
    label: "按钮回调",
    hint: "点击 inline keyboard 按钮触发的回调",
  },
  read_history: {
    label: "标记已读",
    hint: "把会话的最新消息标记为已读",
  },

  // ── 入/退/建群 ────────────────────────────
  join_chat: {
    label: "加入群组",
    hint: "加入公开群/超级群/频道",
  },
  leave_chat: {
    label: "退出群组",
    hint: "离开当前所在群组/频道",
  },
  create_chat: {
    label: "建群",
    hint: "创建新的群或频道",
  },

  // ── 邀请/陌生人 ───────────────────────────
  invite_user: {
    label: "邀请用户",
    hint: "把用户拉进群（被动添加，最敏感的反垃圾动作之一）",
  },
  dm_stranger: {
    label: "私聊陌生人",
    hint: "向没有共同群的用户主动开私聊（极易触发 PeerFlood）",
  },

  // ── 资料 ──────────────────────────────────
  update_profile: {
    label: "修改资料",
    hint: "改昵称/简介/头像/用户名等个人资料",
  },

  // ── 文件 ──────────────────────────────────
  upload_file: {
    label: "上传文件",
    hint: "发送图片/视频/文件等媒体（按上传次数计费，不分大小）",
  },
  download_file: {
    label: "下载文件",
    hint: "拉取媒体到本地（受限频率较宽松）",
  },

  // ── 搜索 ──────────────────────────────────
  search: {
    label: "搜索",
    hint: "全局或群内消息搜索",
  },

  // ── 全局 ──────────────────────────────────
  api_total: {
    label: "API 总量",
    hint: "本账号所有 API 调用的总速率上限（绕过单个动作的天花板）",
  },
};

/** 取人类可读标签；未知 action 直接返回原 key */
export function actionLabel(action: string): string {
  return ACTION_INFO[action]?.label ?? action;
}

/** 取一句话说明；未知 action 返回空串 */
export function actionHint(action: string): string {
  return ACTION_INFO[action]?.hint ?? "";
}

===== frontend/src/lib/utils.ts =====
// 通用工具：cn 用 clsx + tailwind-merge 合并 className，避免 tailwind 冲突
import clsx, { type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// 简单格式化：把 ISO 时间字符串转本地"年-月-日 时:分"
export function formatDateTime(iso?: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

===== frontend/src/lib/version.ts =====
// 应用版本号 — 前端单点定义。
//
// 每次 release 同时改 4 处（缺一不可）：
//   1) frontend/src/lib/version.ts          ← 本文件（前端 UI 显示）
//   2) frontend/package.json                ← npm/pnpm 包元数据
//   3) backend/app/__init__.py              ← Python 包 __version__（main.py + ,version 都读它）
//   4) backend/pyproject.toml               ← pip install / 打包发布元数据
//
// 同步追加：CHANGELOG.md 顶部新增 `## [x.y.z] — yyyy-mm-dd` 段。
// 详见 agent-plans/README.md §6 的"版本号与 CHANGELOG"清单。
//
// 命名约定（SemVer：MAJOR.MINOR.PATCH）：
//   - MAJOR  破坏性变更（数据库不兼容迁移 / 协议大改 / 配置项重命名）
//   - MINOR  向后兼容的功能增量（一个 Sprint 通常 +1）
//   - PATCH  bug 修复 / 文档 / 小调整 / hotfix
//
// APP_STAGE 是非正式标签：
//   - 路线图阶段："MVP"、"Sprint 2"、"RC1"
//   - 生产稳定时设为 null（达到 1.0.0 通常就摘掉）

export const APP_VERSION = "0.2.0";
export const APP_STAGE: string | null = "Sprint 2";

/** Sidebar / About 等 UI 处使用的展示串。例："v0.2.0 · Sprint 2" 或 "v0.2.0"（STAGE 为 null 时）。 */
export const APP_VERSION_LABEL = APP_STAGE
  ? `v${APP_VERSION} · ${APP_STAGE}`
  : `v${APP_VERSION}`;

===== frontend/src/main.tsx =====
// 入口文件：挂载 React + Router + Query Client + Toaster
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";

import App from "./App";
import "./index.css";
import { registerPWA } from "./pwa";

// 全局 query client：默认 30s 缓存、失焦不刷新（避免与 401 跳转冲突）
const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
      <Toaster richColors closeButton position="top-right" />
    </QueryClientProvider>
  </React.StrictMode>,
);

// 注册 Service Worker（生产构建会预缓存静态资源；开发环境也启用便于真机测试）
registerPWA();

===== frontend/src/pages/AISettings.tsx =====
// 顶层「AI 设置」页：把 LLM Provider 从系统设置里提出来，独立成页。
// 顶部展示路由原理 + 模型推荐配置；下半部是 LLMProviders 子组件做增删改查。
import { LLMProviders } from "@/pages/Settings/LLMProviders";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function AISettings() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">AI 设置</h1>
        <p className="text-sm text-muted-foreground">
          管理 LLM 供应商凭据 + 路由元数据。这些配置被「AI 类自定义命令」复用
        </p>
      </div>

      <HowItWorksCard />
      <ModalityGlossaryCard />
      <RecommendedSetupCard />

      <LLMProviders />
    </div>
  );
}

// ───────────────────────────────────────────────────────────
// 1) AI 命令工作原理（先看这个再去配 provider）
// ───────────────────────────────────────────────────────────
function HowItWorksCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">AI 命令是怎么工作的</CardTitle>
        <CardDescription>
          在 TG 任意对话中回复某条消息，发 “命令前缀ai”，如： <code>,ai 你的问题</code>，worker 会用 LLM
          的回答<strong>编辑你刚刚发出去的命令消息</strong>（PagerMaid 风格）
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <ol className="list-decimal space-y-1.5 pl-5 text-muted-foreground">
          <li>
            前缀默认是你系统设置里的命令前缀 <code>,</code>（不是 <code>/</code>）；要改成 <code>/</code> 去
            <span className="mx-1 font-medium">系统设置 → 命令前缀</span> 改
          </li>
          <li>
            worker 只拦截「自己发给自己 / 别人」的消息（outgoing），别人发的同样命令不会触发
          </li>
          <li>
            被回复消息的正文 + 你跟在命令后的问题被拼成 user prompt，
            其中 <code>system_prompt</code> 由模板配置决定
          </li>
          <li>
            返回结果时<strong>编辑你的命令消息</strong>而不是发新消息；末尾会附上 <code>—
              模型名 · in/out tokens</code>，自动路由模式还会标 <code>auto · 决策原因</code>
          </li>
          <li>
            两步配置才能用：先在下方建 <strong>LLM Provider</strong>（填 api_key），
            再去 <span className="font-medium">系统设置 → 自定义命令</span> 建 type=ai
            的模板（命名为 <code>ai</code>），最后在账号详情勾选启用
          </li>
        </ol>
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          安全说明：所有 api_key 经主密钥 Fernet 加密落库；GET 接口永远不返明文，
          调用错误的异常消息也会自动剥离 sk- / Bearer 等敏感串。
        </div>
      </CardContent>
    </Card>
  );
}

// ───────────────────────────────────────────────────────────
// 2) 术语速查：模态 / 标签 / 成本档（解释路由是怎么挑模型的）
// ───────────────────────────────────────────────────────────
function ModalityGlossaryCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">术语速查</CardTitle>
        <CardDescription>
          配 provider 时下面三类元数据决定「自动路由」如何挑模型。点击 provider 编辑里的字段
          会显示同样的解释。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div>
          <h4 className="mb-1.5 font-semibold">模态（modality）— 模型能"看/听/说"什么</h4>
          <ul className="space-y-1 text-xs text-muted-foreground">
            <li>
              <Badge variant="outline" className="mr-1.5">text</Badge>
              纯文本 LLM（绝大多数）。仅支持文本输入文本输出
            </li>
            <li>
              <Badge variant="outline" className="mr-1.5">vision</Badge>
              视觉多模态（VLM, Vision-Language Model）。支持图文输入 → 文本输出，
              典型如 GPT-4V / Claude 3.x Vision / GLM-4V
            </li>
            <li>
              <Badge variant="outline" className="mr-1.5">audio</Badge>
              音频多模态。支持语音转写 (STT) / 文转语音 (TTS) / 实时语音对话，
              典型如 Whisper / GPT-4o realtime audio
            </li>
            <li>
              <Badge variant="outline" className="mr-1.5">multimodal</Badge>
              全模态（Omnimodal）。同时支持图、音、视频等多种输入，
              典型如 GPT-4o / Gemini 2.0 Pro
            </li>
          </ul>
        </div>

        <div>
          <h4 className="mb-1.5 font-semibold">路由标签（tags）— 模型擅长干什么</h4>
          <div className="flex flex-wrap gap-2 text-xs">
            <TagDef tag="chat" desc="通用闲聊 / 短问短答" />
            <TagDef tag="code" desc="代码生成 / 解释 / 调试" />
            <TagDef tag="math" desc="数学推导 / 计算" />
            <TagDef tag="translate" desc="多语种翻译" />
            <TagDef tag="vision" desc="看图说话 / OCR / 图像理解（需配 modality=vision）" />
            <TagDef tag="long_context" desc="大上下文（≥ 64K token）" />
            <TagDef tag="reason" desc="复杂推理 / 多步分析（旗舰）" />
            <TagDef tag="smart" desc="答主力（同 reason，强调质量）" />
            <TagDef tag="cheap" desc="量大优先（成本档 1）" />
            <TagDef tag="fast" desc="低延迟优先" />
            <TagDef tag="classify" desc="路由分类器；轻量小模型" />
          </div>
        </div>

        <div>
          <h4 className="mb-1.5 font-semibold">成本档（cost_tier）— 同 tag 多个候选时挑谁</h4>
          <ul className="space-y-1 text-xs text-muted-foreground">
            <li>
              <Badge variant="secondary" className="mr-1.5">tier 1</Badge>
              便宜量产档：路由器在 chat / classify / translate 等高频场景下优先挑这档
            </li>
            <li>
              <Badge variant="secondary" className="mr-1.5">tier 2</Badge>
              中档：默认值；适合绝大多数场景
            </li>
            <li>
              <Badge variant="secondary" className="mr-1.5">tier 3</Badge>
              旗舰档：复杂推理 / smart / reason 等场景路由器优先挑这档
            </li>
          </ul>
        </div>

        <div>
          <h4 className="mb-1.5 font-semibold">路由策略（命中顺序）</h4>
          <ol className="list-decimal space-y-0.5 pl-5 text-xs text-muted-foreground">
            <li>被回复消息含图 / 关键词 → 选 modality∈{"{vision,multimodal}"}</li>
            <li>消息含代码块或 def/function/class 等 token → tag=code</li>
            <li>消息含 LaTeX 或多次"数字+运算符" → tag=math</li>
            <li>消息含「翻译为/translate to」等 → tag=translate</li>
            <li>原文+问题合计 ≥ 1500 字符 → tag=long_context</li>
            <li>消息含「为什么/分析/推导/对比」等 → tag∈{"{reason,smart}"}（旗舰优先）</li>
            <li>都不命中 → tag=chat 中 cost_tier 最低（最便宜）</li>
            <li>全失败 → 调 classifier provider 让小模型判类（可选）</li>
            <li>仍无 → 用模板里配的「独立兜底 provider」</li>
            <li>仍无 → 候选池里 cost_tier 最低的那条</li>
          </ol>
        </div>
      </CardContent>
    </Card>
  );
}

function TagDef({ tag, desc }: { tag: string; desc: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border bg-background px-2 py-1">
      <Badge variant="outline" className="font-mono">
        {tag}
      </Badge>
      <span className="text-muted-foreground">{desc}</span>
    </span>
  );
}

// ───────────────────────────────────────────────────────────
// 3) 模型推荐配置（针对常见 4 家做的预设）
// ───────────────────────────────────────────────────────────
function RecommendedSetupCard() {
  // 注意：模型版本号会随官方更新变化；这里只是建议起点，请以各厂商当前可用为准
  const rows: Array<{
    name: string;
    protocol: string;
    modality: string;
    tags: string[];
    tier: number;
    role: string;
    note: string;
  }> = [
      {
        name: "Claude Opus 4.7",
        protocol: "anthropic",
        modality: "vision",
        tags: ["smart", "reason", "code", "long_context", "vision"],
        tier: 3,
        role: "答主力（旗舰文本 + 视觉）",
        note: "代码、长文、复杂推理优先；做最终回答主力。",
      },
      {
        name: "GPT 5.5",
        protocol: "openai",
        modality: "multimodal",
        tags: ["smart", "reason", "vision"],
        tier: 3,
        role: "通用兜底 + 多模态备份",
        note: "全模态（图/音/视频）兜底；当 Claude Opus 不可用时顶上。",
      },
      {
        name: "GLM 4.7",
        protocol: "openai 兼容（自填 base_url）",
        modality: "text",
        tags: ["chat", "code", "classify", "cheap"],
        tier: 1,
        role: "中文闲聊 + 路由分类器",
        note: "中文短问短答性价比高；最适合做 classifier 让它判路由类别。",
      },
      {
        name: "Mimo V2.5 Pro",
        protocol: "openai 兼容（自填 base_url）",
        modality: "text",
        tags: ["chat", "translate", "cheap", "fast"],
        tier: 1,
        role: "翻译 + 短文闲聊量产",
        note: "中英互译 + 低延迟闲聊场景的量产档；不要给它复杂推理。",
      },
    ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">推荐配置（按 4 家常见模型）</CardTitle>
        <CardDescription>
          给四个模型建议的 modality / tags / cost_tier 组合；按下方填到 provider 编辑里即可。
          也可以全部建好后在自定义命令里把一条 <code>,ai</code> 设成 auto 模式 +
          GLM 做 classifier，自动路由到合适模型
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>模型</TableHead>
              <TableHead>provider 协议</TableHead>
              <TableHead>modality</TableHead>
              <TableHead>tags</TableHead>
              <TableHead>cost_tier</TableHead>
              <TableHead>定位</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.name}>
                <TableCell className="font-medium">{r.name}</TableCell>
                <TableCell className="font-mono text-xs">{r.protocol}</TableCell>
                <TableCell>
                  <Badge variant="outline">{r.modality}</Badge>
                </TableCell>
                <TableCell className="space-x-1">
                  {r.tags.map((t) => (
                    <Badge key={t} variant="outline" className="text-xs">
                      {t}
                    </Badge>
                  ))}
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">{r.tier}</Badge>
                </TableCell>
                <TableCell className="text-xs">
                  <div className="font-medium">{r.role}</div>
                  <div className="text-muted-foreground">{r.note}</div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        <div className="mt-4 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900">
          <p className="font-semibold">推荐落地组合（最省 token + 答主力都顾上）：</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            <li>
              建一条 <code>,ai</code> 模板设 auto 模式：
              默认/兜底 = Claude Opus 4.7，分类器 = GLM 4.7
            </li>
            <li>
              再建几条 fixed 模板做强制覆盖：<code>,opus</code> / <code>,gpt</code> /
              <code>,glm</code> / <code>,mimo</code> 各绑死一个 provider，方便手动选
            </li>
            <li>
              视觉场景在被回复消息含图时会自动用 modality=vision/multimodal 的 provider，
              不用单独建 <code>,看图</code> 命令
            </li>
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}

===== frontend/src/pages/Accounts/CommandsTab.tsx =====
// 账号详情 → 命令 tab：列出全量模板 + 勾选启用/禁用
//
// 一行一条模板：左侧名称 + 类型徽章 + 描述；右侧 Switch
// 模板内容由系统设置统一管理；这里仅负责"是否在该账号上启用"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import {
  disableAccountCommand,
  enableAccountCommand,
  listAccountCommands,
} from "@/api/commands";
import type { CommandTemplateType } from "@/api/types";
import { getErrMsg } from "@/lib/api";

const TYPE_LABELS: Record<CommandTemplateType, string> = {
  reply_text: "回复文本",
  forward_to: "转发到",
  run_plugin: "调插件",
  ai: "AI",
};

export function CommandsTab({ aid }: { aid: number }) {
  const qc = useQueryClient();

  const listQ = useQuery({
    queryKey: ["account", aid, "commands"],
    queryFn: () => listAccountCommands(aid),
  });

  const toggleMut = useMutation({
    mutationFn: async (vars: { templateId: number; enabled: boolean }) =>
      vars.enabled
        ? enableAccountCommand(aid, vars.templateId)
        : disableAccountCommand(aid, vars.templateId),
    onSuccess: (_d, vars) => {
      toast.success(`${vars.enabled ? "已启用" : "已禁用"}（worker 热加载）`);
      qc.invalidateQueries({ queryKey: ["account", aid, "commands"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">自定义命令</CardTitle>
            <CardDescription>
              勾选模板即在该账号生效，TG 内即可用 `,name` 触发；模板内容请到「系统设置 → 自定义命令」管理
            </CardDescription>
          </div>
          <Button asChild variant="outline" size="sm">
            <Link to="/settings">
              <Plus className="mr-1 h-4 w-4" /> 管理模板
            </Link>
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {listQ.isLoading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : listQ.data && listQ.data.length > 0 ? (
          <ul className="divide-y">
            {listQ.data.map((item) => {
              const t = item.template;
              return (
                <li
                  key={t.id}
                  className="flex items-center justify-between gap-3 py-3"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm">,{t.name}</span>
                      <Badge variant="secondary">
                        {TYPE_LABELS[t.type] || t.type}
                      </Badge>
                    </div>
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">
                      {t.description || "—"}
                    </div>
                  </div>
                  <Switch
                    checked={item.enabled}
                    onCheckedChange={(v) =>
                      toggleMut.mutate({ templateId: t.id, enabled: v })
                    }
                    disabled={toggleMut.isPending}
                  />
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="rounded-md border border-dashed py-8 text-center text-xs text-muted-foreground">
            尚未创建任何命令模板。先到「系统设置 → 自定义命令」新建一个
          </p>
        )}
      </CardContent>
    </Card>
  );
}

===== frontend/src/pages/Accounts/Detail.tsx =====
// 账号详情：3 个 Tab —— 概览 / 功能开关 / 风控
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronRight, Power, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Spinner } from "@/components/ui/misc";
import { AccountAvatar } from "@/components/AccountAvatar";
import { AccountStatusBadge } from "@/components/AccountStatusBadge";
import { MaskedPhone } from "@/components/MaskedPhone";
import { IgnoredTab } from "@/pages/Accounts/IgnoredTab";
import { CommandsTab } from "@/pages/Accounts/CommandsTab";
import {
  deleteAccount,
  getAccount,
  listAccountFeatures,
  patchAccount,
  pauseAccount,
  resumeAccount,
  toggleAccountFeature,
} from "@/api/accounts";
import { listProxies, testProxy } from "@/api/proxies";
import { listDeviceProfiles } from "@/api/device-profiles";
import {
  getAccountRateLimit,
  getHumanize,
  patchAccountRateLimit,
  patchHumanize,
  strictRateLimit,
} from "@/api/system";
import { getErrMsg } from "@/lib/api";
import { cn, formatDateTime } from "@/lib/utils";
import { Select } from "@/components/ui/select";
import { Activity, Loader2 } from "lucide-react";
import type { HumanizeConfig, ProxyTestResult } from "@/api/types";
import { actionHint, actionLabel } from "@/lib/rate-actions";

// 5 个内置功能，与 plan 对齐
const FEATURE_KEYS: { key: string; label: string }[] = [
  { key: "auto_reply", label: "自动回复" },
  { key: "forward", label: "消息转发" },
  { key: "group_admin", label: "群组管理" },
  { key: "scheduler", label: "定时任务" },
  { key: "monitor", label: "消息监控" },
];

export function AccountDetail() {
  const params = useParams();
  const aid = Number(params.aid);
  const nav = useNavigate();
  const qc = useQueryClient();

  const detailQ = useQuery({
    queryKey: ["account", aid],
    queryFn: () => getAccount(aid),
    enabled: !!aid,
  });

  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid,
  });

  const rateQ = useQuery({
    queryKey: ["account", aid, "rate-limit"],
    queryFn: () => getAccountRateLimit(aid),
    enabled: !!aid,
  });

  // ===================== 操作 mutations =====================
  const toggleStatusMut = useMutation({
    mutationFn: async (pause: boolean) =>
      pause ? pauseAccount(aid) : resumeAccount(aid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", aid] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已下发指令");
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // "重启 worker"快捷操作：暂停 → 1 秒 → 启动；让 runtime.py 启动钩子重新调一次
  // client.get_me() 回填 tg_user_id / tg_username。
  const restartWorkerMut = useMutation({
    mutationFn: async () => {
      await pauseAccount(aid);
      await new Promise((r) => setTimeout(r, 1000));
      await resumeAccount(aid);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", aid] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已重启 worker；几秒后字段会自动刷新");
      // 5 秒后再拉一次详情，让 UI 自动出来
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["account", aid] });
      }, 5000);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteAccount(aid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已删除");
      nav("/accounts", { replace: true });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const featureMut = useMutation({
    mutationFn: async (vars: { key: string; enabled: boolean }) =>
      toggleAccountFeature(aid, vars.key, vars.enabled),
    onSuccess: (_d, vars) => {
      toast.success(`${vars.enabled ? "已启用" : "已禁用"}：${vars.key}`);
      qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const ratePatchMut = useMutation({
    mutationFn: async (vars: { action: string; per_minute: number | null }) =>
      patchAccountRateLimit(aid, vars.action, { per_minute: vars.per_minute }),
    onSuccess: () => {
      toast.success("已保存（worker 热加载）");
      qc.invalidateQueries({ queryKey: ["account", aid, "rate-limit"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const strictMut = useMutation({
    mutationFn: () => strictRateLimit(aid, { multiplier: 0.5, ttl_seconds: 7200 }),
    onSuccess: () => {
      toast.success("已紧急调严：阈值 ×0.5 维持 2 小时");
      qc.invalidateQueries({ queryKey: ["account", aid, "rate-limit"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (!aid) return <p>账号 ID 不合法</p>;
  if (detailQ.isLoading)
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  if (!detailQ.data) return <p>账号不存在</p>;

  const acc = detailQ.data;
  // 老账号 / 异常账号可能 tg_user_id / tg_username 都是 null：worker 启动时
  // 会调 client.get_me() 自动回填（runtime.py:107）。这里给个友好提示，让用户
  // 明白"为什么这两栏是空的"以及怎么解。
  const idMissing = acc.tg_user_id == null && !acc.tg_username;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav("/accounts")}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回列表
        </Button>
        <AccountAvatar
          id={acc.id}
          name={acc.display_name}
          username={acc.tg_username}
          size={36}
        />
        <h1 className="min-w-0 truncate text-2xl font-semibold tracking-tight">
          {acc.display_name ||
            (acc.tg_username ? `@${acc.tg_username}` : `#${acc.id}`)}
        </h1>
        <AccountStatusBadge status={acc.status} />
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="features">功能开关</TabsTrigger>
          <TabsTrigger value="commands">命令</TabsTrigger>
          <TabsTrigger value="rate">风控基础</TabsTrigger>
          <TabsTrigger value="proxy">出口/伪装</TabsTrigger>
          <TabsTrigger value="ignored">忽略的群组</TabsTrigger>
        </TabsList>

        {/* 概览 */}
        <TabsContent value="overview">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">基本信息</CardTitle>
              <CardDescription>账号基础属性与运行控制</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {idMissing ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  <div className="mb-1.5">
                    ⚠ 该账号尚未同步 Telegram 用户 ID 与用户名。worker 启动时会
                    自动通过 <code>client.get_me()</code> 回填——但只在那一刻执行一次。
                  </div>
                  <div className="mb-2">
                    当前账号状态：<span className="font-medium">{acc.status}</span>。
                    点下面按钮一键重启 worker，几秒后这两栏会出现。
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="bg-amber-100 hover:bg-amber-200 border-amber-300"
                    disabled={restartWorkerMut.isPending}
                    onClick={() => restartWorkerMut.mutate()}
                  >
                    {restartWorkerMut.isPending ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    ) : null}
                    重启 worker 同步
                  </Button>
                </div>
              ) : null}
              <dl className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-muted-foreground">账号 ID（系统）</dt>
                  <dd>#{acc.id}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Telegram 用户 ID</dt>
                  <dd className="font-mono">{acc.tg_user_id ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Telegram 用户名</dt>
                  <dd className="font-mono">
                    {acc.tg_username ? `@${acc.tg_username}` : "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">电话</dt>
                  <dd>
                    <MaskedPhone phone={acc.phone} iconClassName="h-4 w-4" />
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">显示名</dt>
                  <dd>{acc.display_name || "—"}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">绑定时间</dt>
                  <dd>{formatDateTime(acc.created_at)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">冷启动结束</dt>
                  <dd>{acc.cold_start_until || "—"}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">备注</dt>
                  <dd>{acc.notes || "—"}</dd>
                </div>
              </dl>

              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => toggleStatusMut.mutate(acc.status === "active")}
                >
                  <Power className="mr-1 h-4 w-4" />
                  {acc.status === "active" ? "暂停账号" : "启动账号"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-destructive"
                  onClick={() => {
                    const label =
                      acc.display_name ||
                      (acc.tg_username ? `@${acc.tg_username}` : `#${acc.id}`);
                    if (
                      confirm(
                        `二次确认：删除账号 ${label}，将撤销 session 并清空所有规则。`,
                      )
                    )
                      deleteMut.mutate();
                  }}
                >
                  <Trash2 className="mr-1 h-4 w-4" /> 删除账号
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 功能开关 */}
        <TabsContent value="features">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">功能开关</CardTitle>
              <CardDescription>
                每个功能可独立启停。开启后跳到对应配置页配置规则
              </CardDescription>
            </CardHeader>
            <CardContent>
              {featuresQ.isLoading ? (
                <div className="flex h-20 items-center justify-center">
                  <Spinner className="text-primary" />
                </div>
              ) : (
                <ul className="divide-y">
                  {FEATURE_KEYS.map((f) => {
                    const item = featuresQ.data?.find(
                      (x) => x.feature_key === f.key,
                    );
                    const enabled = !!item?.enabled;
                    return (
                      <li
                        key={f.key}
                        className="flex items-center justify-between py-3"
                      >
                        <div>
                          <div className="font-medium">{f.label}</div>
                          <div className="text-xs text-muted-foreground">
                            {item?.state ? `状态：${item.state}` : "未启用"}
                            {item?.last_error
                              ? ` · 最近错误：${item.last_error}`
                              : ""}
                          </div>
                        </div>
                        <div className="flex items-center gap-3">
                          <Switch
                            checked={enabled}
                            onCheckedChange={(v) =>
                              featureMut.mutate({ key: f.key, enabled: v })
                            }
                          />
                          {enabled && (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() =>
                                nav(`/accounts/${aid}/features/${f.key}`)
                              }
                            >
                              配置 →
                            </Button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* 自定义命令（账号 × 模板 启用关系） */}
        <TabsContent value="commands">
          <CommandsTab aid={aid} />
        </TabsContent>

        {/* 风控基础 */}
        <TabsContent value="rate">
          <Card>
            <CardHeader>
              <div className="flex items-start justify-between">
                <div>
                  <CardTitle className="text-base">风控阈值（基础版）</CardTitle>
                  <CardDescription>
                    仅展示当前账号生效的 RateLimitRule，可编辑
                    per_minute；进阶配置请到模板页
                  </CardDescription>
                </div>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => {
                    if (confirm("确认要紧急调严？阈值 ×0.5，TTL 2 小时"))
                      strictMut.mutate();
                  }}
                >
                  紧急调严 ½ × 2h
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {rateQ.isLoading ? (
                <div className="flex h-20 items-center justify-center">
                  <Spinner className="text-primary" />
                </div>
              ) : rateQ.data && rateQ.data.rules.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>动作</TableHead>
                      <TableHead>每分钟</TableHead>
                      <TableHead>每小时</TableHead>
                      <TableHead>策略</TableHead>
                      <TableHead className="text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rateQ.data.rules.map((r) => (
                      <RateRow
                        key={r.action}
                        action={r.action}
                        perMinute={r.per_minute ?? null}
                        perHour={r.per_hour ?? null}
                        policy={r.policy}
                        onSave={(v) =>
                          ratePatchMut.mutate({ action: r.action, per_minute: v })
                        }
                      />
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  尚无风控配置
                </p>
              )}

              {/* 拟人化（humanize）配置：折叠面板，默认收起 */}
              <div className="mt-4 border-t pt-4">
                <HumanizePanel aid={aid} />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 出口 / 代理 + 设备伪装 */}
        <TabsContent value="proxy" className="space-y-4">
          <ProxyTab aid={aid} currentProxyId={acc.proxy_id ?? null} />
          <DeviceProfileTab
            aid={aid}
            currentProfileId={acc.device_profile_id ?? null}
          />
        </TabsContent>

        {/* 忽略群组 / peer */}
        <TabsContent value="ignored">
          <IgnoredTab aid={aid} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// 出口/代理 tab：选代理 + 立即测试 + 保存
function ProxyTab({
  aid,
  currentProxyId,
}: {
  aid: number;
  currentProxyId: number | null;
}) {
  const qc = useQueryClient();
  const proxiesQ = useQuery({ queryKey: ["proxies"], queryFn: listProxies });
  const [selected, setSelected] = useState<string>(
    currentProxyId !== null ? String(currentProxyId) : "",
  );
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ProxyTestResult | null>(null);

  const saveMut = useMutation({
    mutationFn: () =>
      patchAccount(aid, {
        proxy_id: selected ? Number(selected) : null,
      }),
    onSuccess: () => {
      toast.success("已保存。worker 重启后生效（账号详情 → 概览 → 暂停 → 恢复）");
      qc.invalidateQueries({ queryKey: ["account", aid] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  async function handleTest() {
    if (!selected) {
      toast.error("请先选一个代理");
      return;
    }
    setTesting(true);
    setResult(null);
    try {
      const r = await testProxy(Number(selected));
      setResult(r);
    } catch (err) {
      toast.error(getErrMsg(err));
    } finally {
      setTesting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">出口 / 代理</CardTitle>
        <CardDescription>
          为该账号绑定一个代理（SOCKS5 / HTTP / MTProxy）；空 = 直连。修改后 worker 须重启
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="space-y-1.5 max-w-xl">
          <label className="text-xs text-muted-foreground">绑定代理</label>
          <div className="flex gap-2">
            <Select
              className="flex-1"
              value={selected}
              onChange={(e) => {
                setSelected(e.target.value);
                setResult(null);
              }}
            >
              <option value="">直连（不走代理）</option>
              {proxiesQ.data?.map((p) => (
                <option key={p.id} value={String(p.id)}>
                  [{p.type}] {p.host}:{p.port}
                  {p.username ? ` @${p.username}` : ""}
                </option>
              ))}
            </Select>
            <Button
              variant="outline"
              onClick={handleTest}
              disabled={!selected || testing}
            >
              {testing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Activity className="h-4 w-4" />
              )}
              <span className="ml-1">测试</span>
            </Button>
            <Button
              onClick={() => saveMut.mutate()}
              disabled={
                saveMut.isPending ||
                (selected ? Number(selected) : null) === currentProxyId
              }
            >
              保存
            </Button>
          </div>
        </div>

        {/* 测试结果 */}
        {result ? (
          result.ok ? (
            <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
              ✓ 通过 · {result.latency_ms}ms · {result.country || "?"}
              {result.city ? ` · ${result.city}` : ""}
              {result.exit_ip ? ` · 出口 IP ${result.exit_ip}` : ""}
            </div>
          ) : (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              ✗ {result.error || "未知错误"}
            </div>
          )
        ) : null}

        {!proxiesQ.isLoading && (proxiesQ.data?.length ?? 0) === 0 ? (
          <p className="rounded-md border border-dashed px-3 py-3 text-xs text-muted-foreground">
            代理库为空。先到「系统设置 → 代理库」新建
          </p>
        ) : null}

        <div className="border-t pt-3 text-xs text-muted-foreground">
          ⚠ 修改代理不会立即生效；保存后请在「概览」tab 暂停并恢复账号让 worker 重启用新代理。
        </div>
      </CardContent>
    </Card>
  );
}

// 设备伪装 tab：选 profile + 保存。与 ProxyTab 同位级。
//
// ⚠ 切换 profile 不会让 TG 端立即显示新设备名 —— TG 把设备名绑在 auth_key 上，
// 切换后必须让账号重新登录（删除/重登）才会重新注册到 TG 那边。
function DeviceProfileTab({
  aid,
  currentProfileId,
}: {
  aid: number;
  currentProfileId: number | null;
}) {
  const qc = useQueryClient();
  const profilesQ = useQuery({
    queryKey: ["device-profiles"],
    queryFn: listDeviceProfiles,
  });
  const [selected, setSelected] = useState<string>(
    currentProfileId !== null ? String(currentProfileId) : "",
  );

  const saveMut = useMutation({
    mutationFn: () =>
      patchAccount(aid, {
        device_profile_id: selected ? Number(selected) : null,
      }),
    onSuccess: () => {
      toast.success("已保存。账号下次重新登录时 TG 才会显示新设备名");
      qc.invalidateQueries({ queryKey: ["account", aid] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const currentSelected = selected ? profilesQ.data?.find((p) => p.id === Number(selected)) : null;
  const defaultProfile = profilesQ.data?.find((p) => p.is_default) ?? null;
  const previewProfile = currentSelected ?? defaultProfile;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">设备伪装</CardTitle>
        <CardDescription>
          决定 TG 设备列表里看到的设备名 / 系统 / 客户端版本。空 = 用系统默认 profile。
          <br />
          ⚠ 切换 profile 对**已登录的 session 无效**；TG 把设备名绑在 auth_key 上，
          要让 TG 显示新名字必须让账号重新登录。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="space-y-1.5 max-w-xl">
          <Label className="text-xs text-muted-foreground">设备伪装 profile</Label>
          <div className="flex flex-wrap gap-2">
            <Select
              className="min-w-[16rem] flex-1"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">
                跟随系统默认
                {defaultProfile ? `（${defaultProfile.name}）` : ""}
              </option>
              {profilesQ.data?.map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {p.name}
                  {p.is_default ? " ★" : ""}
                </option>
              ))}
            </Select>
            <Button
              onClick={() => saveMut.mutate()}
              disabled={
                saveMut.isPending ||
                (selected ? Number(selected) : null) === currentProfileId
              }
            >
              保存
            </Button>
          </div>
        </div>

        {previewProfile ? (
          <div className="rounded-lg border bg-muted/30 p-3 text-xs">
            <div className="mb-1 font-medium">TG 设备列表中将显示：</div>
            <div className="font-mono text-foreground">
              {previewProfile.device_model}
            </div>
            <div className="font-mono text-muted-foreground">
              {previewProfile.system_version} · {previewProfile.app_version}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">
              lang: {previewProfile.lang_code} / {previewProfile.system_lang_code}
            </div>
          </div>
        ) : null}

        <div className="border-t pt-3 text-xs text-muted-foreground">
          要新增 / 修改 profile，请到「系统设置 → 设备伪装库」。
        </div>
      </CardContent>
    </Card>
  );
}

// 单行内联编辑：per_minute 输入 + dirty 时显示保存
function RateRow(props: {
  action: string;
  perMinute: number | null;
  perHour: number | null;
  policy: string;
  onSave: (v: number | null) => void;
}) {
  const label = actionLabel(props.action);
  const hint = actionHint(props.action);
  return (
    <TableRow>
      <TableCell>
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium">{label}</span>
          <span className="font-mono text-[11px] text-muted-foreground">
            {props.action}
          </span>
          {hint ? (
            <span className="text-xs text-muted-foreground">{hint}</span>
          ) : null}
        </div>
      </TableCell>
      <TableCell>
        <RateInput initial={props.perMinute} onSave={props.onSave} />
      </TableCell>
      <TableCell className="text-muted-foreground">
        {props.perHour ?? "—"}
      </TableCell>
      <TableCell className="text-muted-foreground">{props.policy}</TableCell>
      <TableCell />
    </TableRow>
  );
}

function RateInput({
  initial,
  onSave,
}: {
  initial: number | null;
  onSave: (v: number | null) => void;
}) {
  const [val, setVal] = useState(initial?.toString() ?? "");
  const dirty = val !== (initial?.toString() ?? "");
  return (
    <div className="flex items-center gap-2">
      <Input
        className="h-8 w-24"
        value={val}
        onChange={(e) => setVal(e.target.value.replace(/[^0-9]/g, ""))}
      />
      {dirty && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => onSave(val ? Number(val) : null)}
        >
          保存
        </Button>
      )}
    </div>
  );
}

// ── 拟人化（humanize）折叠面板 ──────────────────────────────────────
// 默认收起：高级用户才需要调；保存时只下发改过的字段（PATCH 语义）
function HumanizePanel({ aid }: { aid: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  const humanQ = useQuery({
    queryKey: ["account", aid, "humanize"],
    queryFn: () => getHumanize(aid),
    enabled: !!aid && open, // 折叠面板没展开前不去拉
  });

  // 本地编辑态：仅在数据加载后初始化一次
  const [draft, setDraft] = useState<HumanizeConfig | null>(null);
  useEffect(() => {
    if (humanQ.data && draft === null) setDraft(humanQ.data);
  }, [humanQ.data, draft]);

  const saveMut = useMutation({
    mutationFn: (body: Partial<HumanizeConfig>) => patchHumanize(aid, body),
    onSuccess: (data) => {
      toast.success("拟人化配置已保存（worker 热加载）");
      setDraft(data);
      qc.invalidateQueries({ queryKey: ["account", aid, "humanize"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const dirty =
    draft !== null && humanQ.data !== undefined && !shallowEqual(draft, humanQ.data);

  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1 text-sm text-muted-foreground hover:underline"
      >
        <ChevronRight
          className={cn("h-4 w-4 transition-transform", open && "rotate-90")}
        />
        <span>人类化（humanize）配置</span>
        <span className="ml-2 text-xs">{open ? "收起" : "展开"}</span>
      </button>

      {open ? (
        humanQ.isLoading || draft === null ? (
          <div className="flex h-16 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : (
          <div className="space-y-4 rounded-md border bg-muted/20 p-4 text-sm">
            {/* 模拟"对方正在输入" */}
            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="hz-typing">模拟"对方正在输入"</Label>
                <p className="text-xs text-muted-foreground">
                  发送前先 typing N ms，更像真人
                </p>
              </div>
              <Switch
                id="hz-typing"
                checked={draft.typing_simulate}
                onCheckedChange={(v) =>
                  setDraft({ ...draft, typing_simulate: v })
                }
              />
            </div>

            {/* typing 时长范围（min~max ms） */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="hz-tmin">typing 最短 (ms)</Label>
                <Input
                  id="hz-tmin"
                  inputMode="numeric"
                  className="h-8"
                  value={String(draft.typing_min_ms)}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      typing_min_ms: clampInt(e.target.value, 0, 60_000),
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="hz-tmax">typing 最长 (ms)</Label>
                <Input
                  id="hz-tmax"
                  inputMode="numeric"
                  className="h-8"
                  value={String(draft.typing_max_ms)}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      typing_max_ms: clampInt(e.target.value, 0, 60_000),
                    })
                  }
                />
              </div>
            </div>
            {draft.typing_min_ms > draft.typing_max_ms ? (
              <p className="text-xs text-destructive">
                最短不能大于最长
              </p>
            ) : null}

            {/* typing 触发概率 */}
            <div className="space-y-1">
              <Label htmlFor="hz-tprob">触发 typing 的概率（0–100%）</Label>
              <Input
                id="hz-tprob"
                inputMode="numeric"
                className="h-8 w-32"
                value={String(draft.typing_probability)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    typing_probability: clampInt(e.target.value, 0, 100),
                  })
                }
              />
            </div>

            {/* 阅读后再回 + 抖动比例 */}
            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="hz-read">回复前先标记已读</Label>
                <p className="text-xs text-muted-foreground">
                  对方更不容易察觉是机器人
                </p>
              </div>
              <Switch
                id="hz-read"
                checked={draft.read_before_reply}
                onCheckedChange={(v) =>
                  setDraft({ ...draft, read_before_reply: v })
                }
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="hz-jit">人类化抖动比例（0–100%）</Label>
              <Input
                id="hz-jit"
                inputMode="numeric"
                className="h-8 w-32"
                value={String(draft.jitter_pct)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    jitter_pct: clampInt(e.target.value, 0, 100),
                  })
                }
              />
              <p className="text-xs text-muted-foreground">
                所有等待时间会在 ±{draft.jitter_pct}% 范围内随机偏移
              </p>
            </div>

            {/* 活跃时段（可选） */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="hz-ws">活跃开始（HH:MM，可空）</Label>
                <Input
                  id="hz-ws"
                  className="h-8"
                  placeholder="09:00"
                  value={draft.active_window_start ?? ""}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      active_window_start: e.target.value || null,
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="hz-we">活跃结束（HH:MM，可空）</Label>
                <Input
                  id="hz-we"
                  className="h-8"
                  placeholder="23:00"
                  value={draft.active_window_end ?? ""}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      active_window_end: e.target.value || null,
                    })
                  }
                />
              </div>
            </div>

            <div className="space-y-1">
              <Label htmlFor="hz-cold">冷启动天数</Label>
              <Input
                id="hz-cold"
                inputMode="numeric"
                className="h-8 w-32"
                value={String(draft.cold_start_days)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    cold_start_days: clampInt(e.target.value, 0, 90),
                  })
                }
              />
              <p className="text-xs text-muted-foreground">
                新账号在该天数内自动调严风控
              </p>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <Button
                size="sm"
                disabled={
                  !dirty ||
                  saveMut.isPending ||
                  draft.typing_min_ms > draft.typing_max_ms
                }
                onClick={() => saveMut.mutate(draft)}
              >
                {saveMut.isPending ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : null}
                保存
              </Button>
              {dirty ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setDraft(humanQ.data ?? draft)}
                >
                  撤销
                </Button>
              ) : null}
            </div>
          </div>
        )
      ) : null}
    </div>
  );
}

// 把字符串转 int 并夹到 [min, max]；空字符串当 0
function clampInt(s: string, min: number, max: number): number {
  const cleaned = s.replace(/[^0-9]/g, "");
  if (!cleaned) return min;
  const n = parseInt(cleaned, 10);
  return Math.max(min, Math.min(max, Number.isNaN(n) ? min : n));
}

// 浅比较两个 humanize 对象，用来判断 dirty
function shallowEqual(a: object, b: object): boolean {
  const ar = a as Record<string, unknown>;
  const br = b as Record<string, unknown>;
  const keys = new Set([...Object.keys(ar), ...Object.keys(br)]);
  for (const k of keys) {
    if (ar[k] !== br[k]) return false;
  }
  return true;
}

===== frontend/src/pages/Accounts/IgnoredTab.tsx =====
// 账号详情 → 忽略 tab：左侧最近活跃会话（一键加入），右侧已忽略列表（手填+移除）
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import {
  addIgnoredPeer,
  listIgnoredPeers,
  listRecentPeers,
  removeIgnoredPeer,
} from "@/api/ignored_peers";
import { getAccount } from "@/api/accounts";
import type { IgnoredPeer, PeerKind, RecentPeerItem } from "@/api/types";
import { getErrMsg } from "@/lib/api";

// ── peer_kind 中文标签 ────────────────────────────────────────────
const KIND_LABEL: Record<string, string> = {
  private: "私聊",
  group: "普通群",
  supergroup: "超级群",
  channel: "频道",
};

function kindLabel(kind: string): string {
  return KIND_LABEL[kind] || kind;
}

// ── 简易相对时间："刚刚 / N 分钟前 / N 小时前 / N 天前" ──────────
function timeAgo(epochSec: number): string {
  if (!epochSec || epochSec <= 0) return "—";
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - epochSec));
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

export function IgnoredTab({ aid }: { aid: number }) {
  const qc = useQueryClient();

  // ── 数据：账号状态 + 最近活跃 + 已忽略 ──
  // 拉一次账号状态用于"为什么最近活跃为空"的精准引导：
  //   - paused / login_required → "账号未运行，先到概览启动"
  //   - active 但列表空        → "worker 在线但近期没有 incoming 消息"
  const accQ = useQuery({
    queryKey: ["account", aid],
    queryFn: () => getAccount(aid),
    enabled: !!aid,
  });
  const recentQ = useQuery({
    queryKey: ["recent-peers", aid],
    queryFn: () => listRecentPeers(aid),
    refetchInterval: 5_000, // 5s 轮询；worker 写入是内存级，足够快
  });
  const ignoredQ = useQuery({
    queryKey: ["ignored-peers", aid],
    queryFn: () => listIgnoredPeers(aid),
  });

  // 后端已经把 "worker 在跑没消息" 和 "worker 离线" 拆成两态
  const recentItems = recentQ.data?.items ?? [];
  const workerAlive = recentQ.data?.worker_alive ?? false;

  // 把已忽略 peer_id 抽成 Set，便于"最近活跃"列表标灰
  const ignoredSet = useMemo(
    () => new Set((ignoredQ.data ?? []).map((x) => x.peer_id)),
    [ignoredQ.data],
  );

  // ── mutation ──
  const addMut = useMutation({
    mutationFn: async (vars: {
      peer_id: number;
      peer_kind?: string;
      peer_label?: string | null;
    }) => addIgnoredPeer(aid, vars),
    onSuccess: () => {
      toast.success("已加入忽略名单");
      qc.invalidateQueries({ queryKey: ["ignored-peers", aid] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: async (id: number) => removeIgnoredPeer(aid, id),
    onSuccess: () => {
      toast.success("已移除");
      qc.invalidateQueries({ queryKey: ["ignored-peers", aid] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // ── 手填 peer_id 加入 ──
  const [manualId, setManualId] = useState("");
  const [manualKind, setManualKind] = useState<PeerKind | "">("");
  const handleAddManual = () => {
    const trimmed = manualId.trim();
    if (!trimmed) {
      toast.error("请输入 peer_id");
      return;
    }
    // peer_id 可正可负；用 Number 解析允许负号；过滤 NaN
    const num = Number(trimmed);
    if (!Number.isFinite(num) || !Number.isInteger(num)) {
      toast.error("peer_id 必须是整数（可正可负）");
      return;
    }
    addMut.mutate(
      { peer_id: num, peer_kind: manualKind || "private" },
      {
        onSuccess: () => {
          setManualId("");
          setManualKind("");
        },
      },
    );
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <RecentCard
        loading={recentQ.isLoading}
        items={recentItems}
        accountStatus={accQ.data?.status}
        workerAlive={workerAlive}
        ignoredSet={ignoredSet}
        onAdd={(p) =>
          addMut.mutate({
            peer_id: p.peer_id,
            peer_kind: p.peer_kind,
            peer_label: p.peer_label,
          })
        }
        adding={addMut.isPending}
      />
      <IgnoredCard
        loading={ignoredQ.isLoading}
        items={ignoredQ.data ?? []}
        onRemove={(id) => delMut.mutate(id)}
        removing={delMut.isPending}
        manualId={manualId}
        setManualId={setManualId}
        manualKind={manualKind}
        setManualKind={setManualKind}
        onAddManual={handleAddManual}
        adding={addMut.isPending}
      />
    </div>
  );
}

// ── 左卡片：最近活跃 ──
function RecentCard({
  loading,
  items,
  accountStatus,
  workerAlive,
  ignoredSet,
  onAdd,
  adding,
}: {
  loading: boolean;
  items: RecentPeerItem[];
  accountStatus?: string;
  workerAlive: boolean;
  ignoredSet: Set<number>;
  onAdd: (p: RecentPeerItem) => void;
  adding: boolean;
}) {
  // 三态空提示：
  //  - workerAlive=false                → "worker 没在跑，去暂停 → 启动一次"
  //  - workerAlive=true 且 accountStatus 不是 active → 应该不会同时出现，兜底也提示重启
  //  - workerAlive=true 且 active 且空   → "worker 在跑，让别人发条消息试试"
  const emptyHint = !workerAlive ? (
    <>
      worker 没在跑或没响应（账号状态：
      <span className="font-medium">{accountStatus ?? "未知"}</span>）。
      <br />
      <span className="text-xs">
        请到「概览」tab → 暂停账号 → 启动账号；worker 上线后 5 秒内自动出现。
      </span>
    </>
  ) : (
    <>
      worker 已在跑，但内存里还没有最近活跃会话。
      <br />
      <span className="text-xs">
        让小号 / 群组里发条消息给这个账号试试；或在右侧手动输入 ID 加入忽略。
      </span>
    </>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">最近活跃会话</CardTitle>
        <CardDescription className="flex items-center gap-2">
          worker 内存中最近 50 个 incoming 会话；重启 worker 后清空，5 秒自动刷新
          {!loading ? (
            workerAlive ? (
              <Badge
                variant="outline"
                className="border-emerald-300 text-emerald-700"
              >
                worker 在线
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="border-destructive/40 text-destructive"
              >
                worker 离线
              </Badge>
            )
          ) : null}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : items.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            暂无最近活跃会话
            <br />
            <span className="block pt-2">{emptyHint}</span>
          </p>
        ) : (
          <ul className="divide-y">
            {items.map((p) => {
              const ignored = ignoredSet.has(p.peer_id);
              return (
                <li
                  key={p.peer_id}
                  className="flex items-center justify-between gap-3 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm">
                      {p.peer_label || `(未命名) ${p.peer_id}`}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {kindLabel(p.peer_kind)} · ID {p.peer_id} ·{" "}
                      {timeAgo(p.ts)}
                    </div>
                  </div>
                  {ignored ? (
                    <Badge variant="outline" className="shrink-0">
                      已忽略
                    </Badge>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      className="shrink-0"
                      disabled={adding}
                      onClick={() => onAdd(p)}
                    >
                      加入忽略
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ── 右卡片：已忽略 ──
function IgnoredCard({
  loading,
  items,
  onRemove,
  removing,
  manualId,
  setManualId,
  manualKind,
  setManualKind,
  onAddManual,
  adding,
}: {
  loading: boolean;
  items: IgnoredPeer[];
  onRemove: (id: number) => void;
  removing: boolean;
  manualId: string;
  setManualId: (v: string) => void;
  manualKind: PeerKind | "";
  setManualKind: (v: PeerKind | "") => void;
  onAddManual: () => void;
  adding: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">已忽略会话</CardTitle>
        <CardDescription>
          这些会话的所有 incoming 消息将被丢弃，不触发任何插件 / 命令、不消耗风控配额
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* 手填 peer_id */}
        <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto]">
          <Input
            placeholder="手动输入 peer_id（可正可负）"
            value={manualId}
            onChange={(e) =>
              setManualId(e.target.value.replace(/[^\d-]/g, ""))
            }
            onKeyDown={(e) => {
              if (e.key === "Enter") onAddManual();
            }}
          />
          <select
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            value={manualKind}
            onChange={(e) =>
              setManualKind(e.target.value as PeerKind | "")
            }
          >
            <option value="">类型（可选）</option>
            <option value="private">私聊</option>
            <option value="group">普通群</option>
            <option value="supergroup">超级群</option>
            <option value="channel">频道</option>
          </select>
          <Button onClick={onAddManual} disabled={adding}>
            加入
          </Button>
        </div>

        {/* 列表 */}
        {loading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : items.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            尚未忽略任何会话
          </p>
        ) : (
          <ul className="divide-y">
            {items.map((x) => (
              <li
                key={x.id}
                className="flex items-center justify-between gap-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm">
                    {x.peer_label || "(未命名)"}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {kindLabel(x.peer_kind)} · ID {x.peer_id}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  className="shrink-0 text-destructive hover:text-destructive"
                  disabled={removing}
                  onClick={() => onRemove(x.id)}
                >
                  移除
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

===== frontend/src/pages/Accounts/List.tsx =====
// 账号列表：卡片网格形式（移动端单列），含启停 / 详情 / 删除（二次确认）操作
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Power, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/misc";
import { AccountSummaryCard } from "@/components/AccountSummaryCard";
import {
  deleteAccount,
  listAccounts,
  pauseAccount,
  resumeAccount,
} from "@/api/accounts";
import { getErrMsg } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";

export function AccountList() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  const toggleMut = useMutation({
    mutationFn: async (vars: { aid: number; pause: boolean }) =>
      vars.pause ? pauseAccount(vars.aid) : resumeAccount(vars.aid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已下发指令");
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: deleteAccount,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已删除账号");
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">账号管理</h1>
          <p className="text-sm text-muted-foreground">
            每个账号 = 一个 session = 一个独立 worker 进程
          </p>
        </div>
        <Button onClick={() => nav("/accounts/new")}>
          <Plus className="mr-1 h-4 w-4" /> 新增账号
        </Button>
      </div>

      {isLoading ? (
        <div className="flex h-32 items-center justify-center">
          <Spinner className="text-primary" />
        </div>
      ) : data && data.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {data.map((a) => (
            <AccountSummaryCard
              key={a.id}
              account={a}
              footer={
                <div className="space-y-2 text-xs">
                  <div className="flex items-center justify-between text-muted-foreground">
                    <span>已启用 {a.enabled_features} 项</span>
                    <span title={formatDateTime(a.created_at)}>
                      {formatDateTime(a.created_at).slice(0, 10)}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 px-2"
                      onClick={() =>
                        toggleMut.mutate({
                          aid: a.id,
                          pause: a.status === "active",
                        })
                      }
                    >
                      <Power className="mr-1 h-3.5 w-3.5" />
                      {a.status === "active" ? "暂停" : "启动"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 px-2"
                      onClick={() => nav(`/accounts/${a.id}`)}
                    >
                      详情
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 px-2 text-destructive hover:text-destructive"
                      onClick={() => {
                        const label =
                          a.display_name ||
                          (a.tg_username ? `@${a.tg_username}` : `#${a.id}`);
                        if (
                          confirm(
                            `确认删除账号 ${label}？此操作会撤销 session 并清空配置。`,
                          )
                        )
                          delMut.mutate(a.id);
                      }}
                    >
                      <Trash2 className="mr-1 h-3.5 w-3.5" /> 删除
                    </Button>
                  </div>
                </div>
              }
            />
          ))}
        </div>
      ) : (
        <p className="rounded-lg border bg-card py-12 text-center text-sm text-muted-foreground">
          尚未绑定账号，
          <Link to="/accounts/new" className="text-primary hover:underline">
            立即新增
          </Link>
        </p>
      )}
    </div>
  );
}

===== frontend/src/pages/Accounts/Wizard.tsx =====
// 账号绑定 4 步向导：API 凭据 → 验证码 → (可选)2FA → 完成（可复制其他账号配置）
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, ChevronRight } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import {
  cloneConfig,
  listAccounts,
  login2fa,
  loginCode,
  loginStart,
} from "@/api/accounts";
import { listProxies } from "@/api/proxies";
import { getErrCode, getErrMsg } from "@/lib/api";
import { cn } from "@/lib/utils";

// 中文错误码翻译表（PRD 列出的几种）
const ERR_MAP: Record<string, string> = {
  CODE_INVALID: "验证码错误",
  CODE_EXPIRED: "验证码已过期，请重新获取",
  PASSWORD_INVALID: "二步验证密码错误",
  FLOOD_WAIT: "Telegram 限流，请稍后再试",
  PHONE_INVALID: "手机号格式不正确",
  SESSION_EXPIRED: "登录会话已过期，请重新开始",
};
function readableError(err: unknown): string {
  const code = getErrCode(err);
  if (code && ERR_MAP[code]) return ERR_MAP[code];
  return getErrMsg(err);
}

type Step = 1 | 2 | 3 | 4;

export function AccountWizard() {
  const nav = useNavigate();
  const qc = useQueryClient();

  const [step, setStep] = useState<Step>(1);

  // 第一步表单
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [phone, setPhone] = useState("");
  const [proxyId, setProxyId] = useState("");

  // 后端返回的临时 token；仅放组件 state，刷新即丢失
  const [loginToken, setLoginToken] = useState<string | null>(null);

  // 第二/三步表单
  const [smsCode, setSmsCode] = useState("");
  const [twoFa, setTwoFa] = useState("");

  // 完成后的目标账号 ID
  const [createdAid, setCreatedAid] = useState<number | null>(null);

  // 代理列表（用于第 1 步下拉）
  const proxiesQ = useQuery({
    queryKey: ["proxies"],
    queryFn: listProxies,
    enabled: step === 1,
  });

  // 复制其他账号配置（可选）
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
    enabled: step === 4,
  });
  const [cloneFrom, setCloneFrom] = useState<string>("");

  // ===================== mutations =====================
  const startMut = useMutation({
    mutationFn: () =>
      loginStart({
        api_id: Number(apiId),
        api_hash: apiHash.trim(),
        phone: phone.trim(),
        proxy_id: proxyId ? Number(proxyId) : null,
      }),
    onSuccess: (res) => {
      setLoginToken(res.login_token);
      setStep(2);
      toast.success("已发送验证码，请到 Telegram 接收");
    },
    onError: (err) => toast.error(readableError(err)),
  });

  const codeMut = useMutation({
    mutationFn: () =>
      loginCode({ login_token: loginToken!, code: smsCode.trim() }),
    onSuccess: (res) => {
      if (res.require_2fa) {
        setStep(3);
        toast.info("该账号已启用两步验证，请输入密码");
      } else {
        setCreatedAid(res.account_id);
        setStep(4);
        qc.invalidateQueries({ queryKey: ["accounts"] });
        toast.success("绑定成功");
      }
    },
    onError: (err) => toast.error(readableError(err)),
  });

  const twoFaMut = useMutation({
    mutationFn: () =>
      login2fa({ login_token: loginToken!, password: twoFa }),
    onSuccess: (res) => {
      setCreatedAid(res.account_id);
      setStep(4);
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("绑定成功");
    },
    onError: (err) => toast.error(readableError(err)),
  });

  const cloneMut = useMutation({
    mutationFn: () =>
      cloneConfig(createdAid!, Number(cloneFrom), [
        "auto_reply",
        "forward",
        "group_admin",
        "scheduler",
        "monitor",
      ]),
    onSuccess: () => {
      toast.success("已复制配置");
      nav(`/accounts/${createdAid}`);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const stepInfo = useMemo(
    () =>
      [
        { n: 1, t: "API 凭据" },
        { n: 2, t: "验证码" },
        { n: 3, t: "两步密码（可选）" },
        { n: 4, t: "完成" },
      ] as const,
    [],
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav(-1)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">新增账号</h1>
      </div>

      {/* 步进条 */}
      <ol className="flex items-center gap-2 text-sm">
        {stepInfo.map((s, idx) => (
          <li key={s.n} className="flex items-center gap-2">
            <span
              className={cn(
                "flex h-6 w-6 items-center justify-center rounded-full border text-xs",
                step === s.n && "border-primary bg-primary text-primary-foreground",
                step > s.n && "border-emerald-500 bg-emerald-500 text-white",
              )}
            >
              {step > s.n ? <Check className="h-3.5 w-3.5" /> : s.n}
            </span>
            <span
              className={cn(
                "text-muted-foreground",
                step === s.n && "text-foreground font-medium",
              )}
            >
              {s.t}
            </span>
            {idx < stepInfo.length - 1 && (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            )}
          </li>
        ))}
      </ol>

      {/* Step 1 */}
      {step === 1 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">步骤 1 · API 凭据</CardTitle>
            <CardDescription>
              在{" "}
              <a
                href="https://my.telegram.org"
                target="_blank"
                rel="noreferrer"
                className="text-primary hover:underline"
              >
                my.telegram.org
              </a>{" "}
              申请 API ID / Hash
            </CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>API ID</Label>
              <Input value={apiId} onChange={(e) => setApiId(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>API Hash</Label>
              <Input
                value={apiHash}
                onChange={(e) => setApiHash(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>手机号</Label>
              <Input
                placeholder="+8613800000000"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>出口代理（可选）</Label>
              <Select
                value={proxyId}
                onChange={(e) => setProxyId(e.target.value)}
              >
                <option value="">直连（不走代理）</option>
                {proxiesQ.data?.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    [{p.type}] {p.host}:{p.port}
                    {p.username ? ` @${p.username}` : ""}
                  </option>
                ))}
              </Select>
              <p className="text-xs text-muted-foreground">
                若代理列表为空，先到「系统设置 → 代理库」创建
              </p>
            </div>
            <div className="sm:col-span-2 flex justify-end">
              <Button
                onClick={() => {
                  if (!apiId || !apiHash || !phone) {
                    toast.error("请填写 API ID/Hash 与手机号");
                    return;
                  }
                  startMut.mutate();
                }}
                disabled={startMut.isPending}
              >
                {startMut.isPending ? "发送中…" : "下一步"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Step 2 */}
      {step === 2 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">步骤 2 · 输入验证码</CardTitle>
            <CardDescription>
              我们已向 {phone} 发送了验证码（在 Telegram 客户端查看）
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5 max-w-xs">
              <Label>验证码</Label>
              <Input
                inputMode="numeric"
                maxLength={6}
                value={smsCode}
                onChange={(e) => setSmsCode(e.target.value)}
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setStep(1)}>
                上一步
              </Button>
              <Button
                onClick={() => smsCode && codeMut.mutate()}
                disabled={codeMut.isPending || !smsCode}
              >
                {codeMut.isPending ? "提交中…" : "下一步"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Step 3 */}
      {step === 3 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              步骤 3 · 输入两步验证密码
            </CardTitle>
            <CardDescription>
              该账号开启了 Telegram 两步验证，需要输入密码
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5 max-w-xs">
              <Label>密码</Label>
              <Input
                type="password"
                value={twoFa}
                onChange={(e) => setTwoFa(e.target.value)}
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setStep(2)}>
                上一步
              </Button>
              <Button
                onClick={() => twoFa && twoFaMut.mutate()}
                disabled={twoFaMut.isPending || !twoFa}
              >
                {twoFaMut.isPending ? "提交中…" : "完成"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Step 4 */}
      {step === 4 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">步骤 4 · 完成</CardTitle>
            <CardDescription>
              账号已绑定（ID #{createdAid}），可选择从已有账号复制功能配置
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5 max-w-md">
              <Label>从其他账号复制配置（可选）</Label>
              {accountsQ.isLoading ? (
                <div className="flex h-10 items-center">
                  <Spinner className="text-primary" />
                </div>
              ) : (
                <Select
                  value={cloneFrom}
                  onChange={(e) => setCloneFrom(e.target.value)}
                >
                  <option value="">-- 不复制 --</option>
                  {accountsQ.data
                    ?.filter((a) => a.id !== createdAid)
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.display_name || a.phone}
                      </option>
                    ))}
                </Select>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => nav(`/accounts/${createdAid}`)}
              >
                跳过
              </Button>
              <Button
                disabled={!cloneFrom || cloneMut.isPending}
                onClick={() => cloneMut.mutate()}
              >
                {cloneMut.isPending ? "复制中…" : "复制并完成"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

===== frontend/src/pages/Dashboard.tsx =====
// Dashboard：系统状态总览 + 账号状态卡
//
// 顶部新加 SystemHealthCard：DB / alembic / Redis / providers / proxies / workers
// 用 30s 轮询自动刷新，让"配置改动 / 子服务挂掉"这类变化几十秒内可见。
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AccountSummaryCard } from "@/components/AccountSummaryCard";
import { SystemHealthCard } from "@/components/SystemHealthCard";
import { Spinner } from "@/components/ui/misc";
import { listAccounts } from "@/api/accounts";

export function Dashboard() {
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">概览</h1>
        <p className="text-sm text-muted-foreground">
          多账号 · 系统状态 + 账号运行状态一览
        </p>
      </div>

      {/* 系统状态卡（DB / alembic / Redis / providers / proxies / workers）*/}
      <SystemHealthCard />

      {/* 账号状态卡 */}
      <section>
        <h2 className="mb-3 text-sm font-medium text-muted-foreground">
          账号状态
        </h2>
        {accountsQ.isLoading ? (
          <div className="flex h-24 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : accountsQ.data && accountsQ.data.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {accountsQ.data.map((a) => (
              <AccountSummaryCard key={a.id} account={a} />
            ))}
          </div>
        ) : (
          <Card>
            <CardContent className="flex flex-col items-center justify-center gap-3 py-10 text-sm text-muted-foreground">
              <span>尚未绑定任何 TG 账号</span>
              <Button asChild size="sm">
                <Link to="/accounts/new">立即绑定</Link>
              </Button>
            </CardContent>
          </Card>
        )}
      </section>
    </div>
  );
}

===== frontend/src/pages/FeatureMatrix.tsx =====
// 功能矩阵：行=账号，列=功能；点击格子打开浮层操作（启停/克隆）
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X, AlertTriangle } from "lucide-react";
import { toast } from "sonner";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/misc";
import { getFeatureMatrix } from "@/api/features";
import { toggleAccountFeature, cloneConfig } from "@/api/accounts";
import { getErrMsg } from "@/lib/api";
import type { FeatureState } from "@/api/types";
import { cn } from "@/lib/utils";

interface CellInfo {
  aid: number;
  aname: string;
  fkey: string;
  fname: string;
  state: FeatureState;
}

function StateIcon({ state }: { state: FeatureState }) {
  if (state === "active")
    return <Check className="mx-auto h-5 w-5 text-emerald-500" />;
  if (state === "failed")
    return <AlertTriangle className="mx-auto h-5 w-5 text-destructive" />;
  return <X className="mx-auto h-5 w-5 text-muted-foreground" />;
}

export function FeatureMatrix() {
  const nav = useNavigate();
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["matrix"],
    queryFn: getFeatureMatrix,
  });

  const [openCell, setOpenCell] = useState<CellInfo | null>(null);
  const [cloneFromAid, setCloneFromAid] = useState<string>("");

  const toggleMut = useMutation({
    mutationFn: async (vars: {
      aid: number;
      key: string;
      enabled: boolean;
    }) => toggleAccountFeature(vars.aid, vars.key, vars.enabled),
    onSuccess: () => {
      toast.success("已更新");
      qc.invalidateQueries({ queryKey: ["matrix"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const cloneMut = useMutation({
    mutationFn: async (vars: { toAid: number; fromAid: number; key: string }) =>
      cloneConfig(vars.toAid, vars.fromAid, [vars.key]),
    onSuccess: () => {
      toast.success("已克隆规则");
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">功能矩阵</h1>
        <p className="text-sm text-muted-foreground">
          一眼看清所有账号的功能启停状态。点击格子可启停 / 跳配置 / 克隆其他账号规则
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">矩阵</CardTitle>
          <CardDescription>
            ✓ active · ⚠ failed · ✗ disabled
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex h-24 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : data && data.accounts.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>账号 \ 功能</TableHead>
                  {data.features.map((f) => (
                    <TableHead key={f.key} className="text-center">
                      {f.display_name}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.accounts.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="font-medium">{row.name}</TableCell>
                    {data.features.map((f) => {
                      const state = (row.features[f.key] ?? "disabled") as FeatureState;
                      return (
                        <TableCell
                          key={f.key}
                          className={cn(
                            "cursor-pointer text-center hover:bg-accent/50",
                          )}
                          onClick={() =>
                            setOpenCell({
                              aid: row.id,
                              aname: row.name,
                              fkey: f.key,
                              fname: f.display_name,
                              state,
                            })
                          }
                        >
                          <StateIcon state={state} />
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              尚未绑定账号
            </p>
          )}
        </CardContent>
      </Card>

      {/* 浮层 */}
      <Dialog open={!!openCell} onOpenChange={(v) => !v && setOpenCell(null)}>
        <DialogContent>
          {openCell && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {openCell.aname} · {openCell.fname}
                </DialogTitle>
                <DialogDescription>
                  当前状态：{openCell.state}
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {openCell.state !== "active" ? (
                    <Button
                      onClick={() => {
                        toggleMut.mutate({
                          aid: openCell.aid,
                          key: openCell.fkey,
                          enabled: true,
                        });
                        setOpenCell(null);
                      }}
                    >
                      启用
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      onClick={() => {
                        toggleMut.mutate({
                          aid: openCell.aid,
                          key: openCell.fkey,
                          enabled: false,
                        });
                        setOpenCell(null);
                      }}
                    >
                      禁用
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    onClick={() => {
                      const aid = openCell.aid;
                      const key = openCell.fkey;
                      setOpenCell(null);
                      nav(`/accounts/${aid}/features/${key}`);
                    }}
                  >
                    打开配置页
                  </Button>
                </div>

                {/* 从其他账号克隆该 feature 配置 */}
                <div className="space-y-1.5 border-t pt-3">
                  <p className="text-xs text-muted-foreground">
                    从其他账号复制规则
                  </p>
                  <div className="flex gap-2">
                    <Select
                      value={cloneFromAid}
                      onChange={(e) => setCloneFromAid(e.target.value)}
                    >
                      <option value="">-- 选择来源账号 --</option>
                      {data?.accounts
                        .filter((a) => a.id !== openCell.aid)
                        .map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name}
                          </option>
                        ))}
                    </Select>
                    <Button
                      disabled={!cloneFromAid}
                      onClick={() => {
                        cloneMut.mutate({
                          toAid: openCell.aid,
                          fromAid: Number(cloneFromAid),
                          key: openCell.fkey,
                        });
                        setOpenCell(null);
                        setCloneFromAid("");
                      }}
                    >
                      克隆
                    </Button>
                  </div>
                </div>
              </div>

              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpenCell(null)}>
                  关闭
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

===== frontend/src/pages/Features/AutoReply.tsx =====
// 自动回复配置：列出该账号的 auto_reply rule，CRUD + 试运行
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, Pencil, Trash2, Play } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Spinner } from "@/components/ui/misc";
import {
  createRule,
  deleteRule,
  dryRunRule,
  listRules,
  updateRule,
} from "@/api/features";
import { listAccountFeatures, toggleAccountFeature } from "@/api/accounts";
import { getErrMsg } from "@/lib/api";
import type {
  AutoReplyMatch,
  AutoReplyRuleConfig,
  AutoReplyScope,
  RuleOut,
} from "@/api/types";

// rule.config 默认值
function defaultConfig(): AutoReplyRuleConfig {
  return {
    match: "keyword",
    patterns: [],
    scope: "private",
    reply: "",
    cooldown_seconds: 0,
    case_sensitive: false,
    reply_to: true,    // 默认以引用形式回复
  };
}

function readConfig(c: Record<string, unknown> | undefined): AutoReplyRuleConfig {
  // 把后端 rule.config 强转为前端类型；缺失字段补默认
  const def = defaultConfig();
  if (!c) return def;
  return { ...def, ...(c as Partial<AutoReplyRuleConfig>) };
}

interface FormState {
  name: string;
  enabled: boolean;
  priority: number;
  config: AutoReplyRuleConfig;
}

function emptyForm(): FormState {
  return { name: "", enabled: true, priority: 100, config: defaultConfig() };
}

export function AutoReplyConfig() {
  const params = useParams();
  const aid = Number(params.aid);
  const nav = useNavigate();
  const qc = useQueryClient();

  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid,
  });
  const featureItem = featuresQ.data?.find((x) => x.feature_key === "auto_reply");
  const featureEnabled = !!featureItem?.enabled;

  const rulesQ = useQuery({
    queryKey: ["account", aid, "rules", "auto_reply"],
    queryFn: () => listRules(aid, "auto_reply"),
    enabled: !!aid,
  });

  const featureToggleMut = useMutation({
    mutationFn: (next: boolean) => toggleAccountFeature(aid, "auto_reply", next),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // ===================== 编辑/新建 Dialog =====================
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<RuleOut | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());
  // patterns 文本编辑（一行一条）
  const [patternsText, setPatternsText] = useState("");
  // group_ids 文本编辑（自由编辑，保存时再 split）
  const [groupIdsText, setGroupIdsText] = useState("");

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setPatternsText("");
    setGroupIdsText("");
    setEditOpen(true);
  }
  function openEdit(r: RuleOut) {
    setEditing(r);
    const cfg = readConfig(r.config);
    setForm({
      name: r.name,
      enabled: r.enabled,
      priority: r.priority,
      config: cfg,
    });
    setPatternsText((cfg.patterns || []).join("\n"));
    setGroupIdsText((cfg.group_ids || []).join("\n"));
    setEditOpen(true);
  }

  function buildPayload() {
    const patterns = patternsText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    const groupIds = groupIdsText
      .split(/[\s,，;；]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    return {
      name: form.name.trim(),
      enabled: form.enabled,
      priority: form.priority,
      config: { ...form.config, patterns, group_ids: groupIds } as Record<
        string,
        unknown
      >,
    };
  }

  const saveMut = useMutation({
    mutationFn: async () => {
      const payload = buildPayload();
      if (!payload.name) throw new Error("规则名称必填");
      if (!editing) {
        await createRule(aid, "auto_reply", payload);
      } else {
        await updateRule(aid, "auto_reply", editing.id, payload);
      }
    },
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "auto_reply"] });
      setEditOpen(false);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: (rid: number) => deleteRule(aid, "auto_reply", rid),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "auto_reply"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // ===================== 试运行 Dialog =====================
  const [dryOpen, setDryOpen] = useState(false);
  const [dryRule, setDryRule] = useState<RuleOut | null>(null);
  const [drySample, setDrySample] = useState("");
  const [dryChat, setDryChat] = useState<"private" | "group">("private");
  const [dryChatId, setDryChatId] = useState("");
  const [dryResult, setDryResult] = useState<{
    matched: boolean;
    output?: string | null;
  } | null>(null);

  // 打开试运行：根据规则 scope 推导默认会话类型 + 默认 chat_id
  function openDryRun(rule: RuleOut) {
    setDryRule(rule);
    setDrySample("");
    setDryResult(null);
    const cfg = (rule.config || {}) as Record<string, unknown>;
    const scope = cfg.scope as string | undefined;
    if (scope === "private") {
      setDryChat("private");
    } else if (scope === "group_all" || scope === "group_specific") {
      setDryChat("group");
    }
    if (scope === "group_specific") {
      const gids = (cfg.group_ids as string[]) || [];
      setDryChatId(gids[0] ?? "");
    } else {
      setDryChatId("");
    }
    setDryOpen(true);
  }

  const dryMut = useMutation({
    mutationFn: () =>
      dryRunRule(aid, "auto_reply", dryRule!.id, {
        sample_message: drySample,
        sample_chat_type: dryChat,
        sample_chat_id: dryChatId ? Number(dryChatId) : undefined,
      }),
    onSuccess: (res) => setDryResult(res),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (!aid) return <p>账号 ID 不合法</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav(`/accounts/${aid}`)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回账号
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">
          自动回复配置 · #{aid}
        </h1>
      </div>

      {/* 提示条 */}
      <div className="rounded-md border border-blue-200 bg-blue-50/60 px-3 py-2 text-xs text-blue-800 space-y-1">
        <div>✅ 保存后立即生效，无需重启 worker。</div>
        <div>
          ⚠ <b>仅响应别人发来的消息</b>（incoming）。用绑定的 userbot 账号自己发关键词
          <b>不会触发</b>——必须用其他账号在群里 / 私聊里发。
        </div>
        <div>
          🔍 不命中时去「日志中心」筛 source=plugin/worker 的 info
          条，会显示 <code>[event]</code> 收到了什么、<code>[auto_reply]</code> 跳过的具体原因。
        </div>
      </div>

      {/* 总开关 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">功能总开关</CardTitle>
              <CardDescription>
                关闭后所有规则都不会触发；启用即生效
              </CardDescription>
            </div>
            <Switch
              checked={featureEnabled}
              onCheckedChange={(v) => featureToggleMut.mutate(v)}
            />
          </div>
        </CardHeader>
      </Card>

      {/* 规则列表 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">规则</CardTitle>
              <CardDescription>支持关键词与正则；按优先级排序</CardDescription>
            </div>
            <Button onClick={openCreate}>
              <Plus className="mr-1 h-4 w-4" /> 新建规则
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {rulesQ.isLoading ? (
            <div className="flex h-20 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : rulesQ.data && rulesQ.data.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>启用</TableHead>
                  <TableHead>优先级</TableHead>
                  <TableHead>匹配</TableHead>
                  <TableHead>作用范围</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rulesQ.data.map((r) => {
                  const cfg = readConfig(r.config);
                  return (
                    <TableRow key={r.id}>
                      <TableCell className="font-medium">{r.name}</TableCell>
                      <TableCell>
                        <Badge variant={r.enabled ? "success" : "secondary"}>
                          {r.enabled ? "ON" : "OFF"}
                        </Badge>
                      </TableCell>
                      <TableCell>{r.priority}</TableCell>
                      <TableCell>
                        {cfg.match === "regex" ? "正则" : "关键词"}（
                        {cfg.patterns?.length ?? 0}）
                      </TableCell>
                      <TableCell>{scopeLabel(cfg.scope)}</TableCell>
                      <TableCell className="text-right">
                        <div className="inline-flex gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => openEdit(r)}
                          >
                            <Pencil className="mr-1 h-3.5 w-3.5" /> 编辑
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => openDryRun(r)}
                          >
                            <Play className="mr-1 h-3.5 w-3.5" /> 试运行
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-destructive"
                            onClick={() => {
                              if (confirm(`删除规则 ${r.name}？`))
                                delMut.mutate(r.id);
                            }}
                          >
                            <Trash2 className="mr-1 h-3.5 w-3.5" /> 删除
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              暂无规则，点击右上角「新建规则」
            </p>
          )}
        </CardContent>
      </Card>

      {/* 编辑 / 新建 */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑规则" : "新建规则"}</DialogTitle>
            <DialogDescription>
              支持变量：{"{sender}"} {"{chat}"} {"{text}"}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <Field label="名称">
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="启用">
                <div className="flex h-10 items-center">
                  <Switch
                    checked={form.enabled}
                    onCheckedChange={(v) => setForm({ ...form, enabled: v })}
                  />
                </div>
              </Field>
              <Field label="优先级（数字越大越优先）">
                <Input
                  inputMode="numeric"
                  value={form.priority.toString()}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      priority: Number(e.target.value.replace(/[^0-9]/g, "") || 0),
                    })
                  }
                />
              </Field>
              <Field label="匹配类型">
                <Select
                  value={form.config.match}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        match: e.target.value as AutoReplyMatch,
                      },
                    })
                  }
                >
                  <option value="keyword">关键词</option>
                  <option value="regex">正则</option>
                </Select>
              </Field>
              <Field label="作用范围">
                <Select
                  value={form.config.scope}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        scope: e.target.value as AutoReplyScope,
                      },
                    })
                  }
                >
                  <option value="private">仅私聊</option>
                  <option value="group_all">所有群</option>
                  <option value="group_specific">指定群</option>
                </Select>
              </Field>
            </div>

            {form.config.scope === "group_specific" && (
              <Field label="指定群 ID（每行一个，或用空格 / 逗号分隔）">
                <Textarea
                  rows={4}
                  placeholder={
                    "支持以下任一格式：\n" +
                    "  -1001234567890   （Telethon 内部 id）\n" +
                    "  1234567890       （从 t.me/c/<id> 复制）\n" +
                    "  -1234567890      （basic group id）"
                  }
                  value={groupIdsText}
                  onChange={(e) => setGroupIdsText(e.target.value)}
                />
              </Field>
            )}

            <Field label="模式（每行一条）">
              <Textarea
                rows={4}
                value={patternsText}
                onChange={(e) => setPatternsText(e.target.value)}
                placeholder={
                  form.config.match === "regex"
                    ? "例：^/start.*$"
                    : "例：你好\n在吗"
                }
              />
            </Field>

            <Field label="回复内容">
              <Textarea
                rows={3}
                value={form.config.reply}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: { ...form.config, reply: e.target.value },
                  })
                }
                placeholder="支持变量：{sender}、{chat}、{text}"
              />
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="冷却秒数">
                <Input
                  inputMode="numeric"
                  value={(form.config.cooldown_seconds ?? 0).toString()}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        cooldown_seconds: Number(
                          e.target.value.replace(/[^0-9]/g, "") || 0,
                        ),
                      },
                    })
                  }
                />
              </Field>
              <Field label="区分大小写">
                <div className="flex h-10 items-center">
                  <Switch
                    checked={!!form.config.case_sensitive}
                    onCheckedChange={(v) =>
                      setForm({
                        ...form,
                        config: { ...form.config, case_sensitive: v },
                      })
                    }
                  />
                </div>
              </Field>
              <Field label="以「引用」形式回复">
                <div className="flex h-10 items-center gap-2">
                  <Switch
                    checked={form.config.reply_to !== false}
                    onCheckedChange={(v) =>
                      setForm({
                        ...form,
                        config: { ...form.config, reply_to: v },
                      })
                    }
                  />
                  <span className="text-xs text-muted-foreground">
                    开 = 引用触发消息；关 = 发新消息
                  </span>
                </div>
              </Field>
              <Field label="白名单（每行一个 user_id，可选）">
                <Textarea
                  rows={2}
                  value={(form.config.whitelist || []).join("\n")}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        whitelist: e.target.value
                          .split("\n")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      },
                    })
                  }
                />
              </Field>
              <Field label="黑名单（每行一个 user_id，可选）">
                <Textarea
                  rows={2}
                  value={(form.config.blacklist || []).join("\n")}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: {
                        ...form.config,
                        blacklist: e.target.value
                          .split("\n")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      },
                    })
                  }
                />
              </Field>
            </div>
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => saveMut.mutate()}
              disabled={saveMut.isPending}
            >
              {saveMut.isPending ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 试运行 */}
      <Dialog open={dryOpen} onOpenChange={setDryOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>试运行 · {dryRule?.name}</DialogTitle>
            <DialogDescription>
              输入一条样例消息，验证规则是否命中、回复内容
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <Field label="样例消息">
              <Textarea
                rows={3}
                value={drySample}
                onChange={(e) => setDrySample(e.target.value)}
              />
            </Field>
            <Field label="会话类型">
              <Select
                value={dryChat}
                onChange={(e) =>
                  setDryChat(e.target.value as "private" | "group")
                }
              >
                <option value="private">私聊</option>
                <option value="group">群聊</option>
              </Select>
            </Field>
            {dryChat === "group" && (
              <Field label="样本群 ID（可选；留空 = 任意群，scope=group_specific 时自动取规则中第一项）">
                <Input
                  inputMode="numeric"
                  placeholder="例：-1001234567890 或 1234567890"
                  value={dryChatId}
                  onChange={(e) =>
                    setDryChatId(e.target.value.replace(/[^0-9-]/g, ""))
                  }
                />
              </Field>
            )}

            {dryResult && (
              <div className="rounded-md border bg-muted/40 p-3 text-xs">
                <div className="mb-1">
                  命中：
                  <Badge variant={dryResult.matched ? "success" : "secondary"}>
                    {dryResult.matched ? "是" : "否"}
                  </Badge>
                </div>
                {dryResult.matched && dryResult.output != null && (
                  <pre className="whitespace-pre-wrap">{dryResult.output}</pre>
                )}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDryOpen(false)}>
              关闭
            </Button>
            <Button
              disabled={!drySample || dryMut.isPending}
              onClick={() => dryMut.mutate()}
            >
              {dryMut.isPending ? "运行中…" : "运行"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function scopeLabel(s: AutoReplyScope): string {
  switch (s) {
    case "private":
      return "私聊";
    case "group_all":
      return "全部群";
    case "group_specific":
      return "指定群";
    default:
      return s;
  }
}

===== frontend/src/pages/Features/Forward.tsx =====
// 转发规则配置：列出该账号的 forward rule，CRUD + 试运行
//
// 与 AutoReply.tsx 结构保持一致（左侧规则表 + 右侧编辑 Dialog + 底部 dry-run）。
// rule.config 的字段语义见 backend/app/worker/plugins/builtin/forward/manifest.py。
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, Pencil, Trash2, Play } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Spinner } from "@/components/ui/misc";
import {
  createRule,
  deleteRule,
  dryRunRule,
  listRules,
  updateRule,
} from "@/api/features";
import { listAccountFeatures, toggleAccountFeature } from "@/api/accounts";
import { getErrMsg } from "@/lib/api";
import type {
  ForwardMode,
  ForwardRuleConfig,
  ForwardSourceKind,
  RuleOut,
} from "@/api/types";

// rule.config 默认值（新建规则时用）
function defaultConfig(): ForwardRuleConfig {
  return {
    source_kind: "all",
    source_peers: [],
    keyword: "",
    target_chat_id: 0,
    mode: "forward_native",
    include_media: true,
    header: "",
  };
}

function readConfig(c: Record<string, unknown> | undefined): ForwardRuleConfig {
  // 把后端 rule.config 强转为前端类型；缺失字段补默认
  const def = defaultConfig();
  if (!c) return def;
  return { ...def, ...(c as Partial<ForwardRuleConfig>) };
}

interface FormState {
  name: string;
  enabled: boolean;
  priority: number;
  config: ForwardRuleConfig;
}

function emptyForm(): FormState {
  return { name: "", enabled: true, priority: 100, config: defaultConfig() };
}

export function ForwardConfig() {
  const params = useParams();
  const aid = Number(params.aid);
  const nav = useNavigate();
  const qc = useQueryClient();

  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid,
  });
  const featureItem = featuresQ.data?.find(
    (x) => x.feature_key === "forward",
  );
  const featureEnabled = !!featureItem?.enabled;

  const rulesQ = useQuery({
    queryKey: ["account", aid, "rules", "forward"],
    queryFn: () => listRules(aid, "forward"),
    enabled: !!aid,
  });

  const featureToggleMut = useMutation({
    mutationFn: (next: boolean) => toggleAccountFeature(aid, "forward", next),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // ===================== 编辑/新建 Dialog =====================
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<RuleOut | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm());
  // source_peers 用独立 state 文本编辑，避免 #3 提到的「换行 bug」
  // 保存时再 split + 转 int
  const [peersText, setPeersText] = useState("");
  // target_chat_id 同理：先用文本编辑，保存时转 number
  const [targetText, setTargetText] = useState("");

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setPeersText("");
    setTargetText("");
    setEditOpen(true);
  }
  function openEdit(r: RuleOut) {
    setEditing(r);
    const cfg = readConfig(r.config);
    setForm({
      name: r.name,
      enabled: r.enabled,
      priority: r.priority,
      config: cfg,
    });
    setPeersText((cfg.source_peers || []).map(String).join("\n"));
    setTargetText(cfg.target_chat_id ? String(cfg.target_chat_id) : "");
    setEditOpen(true);
  }

  function buildPayload() {
    // peersText：每行 / 逗号 / 分号 分隔；忽略解析失败项
    const peers = peersText
      .split(/[\s,，;；]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => Number(s))
      .filter((n) => Number.isFinite(n));
    const target = Number(targetText.trim());
    return {
      name: form.name.trim(),
      enabled: form.enabled,
      priority: form.priority,
      config: {
        ...form.config,
        source_peers: peers,
        target_chat_id: Number.isFinite(target) ? target : 0,
      } as Record<string, unknown>,
    };
  }

  const saveMut = useMutation({
    mutationFn: async () => {
      const payload = buildPayload();
      if (!payload.name) throw new Error("规则名称必填");
      // payload.config 是 Record<string, unknown>，先 cast 到 unknown 再到 ForwardRuleConfig
      // 才能通过 strict TS 检查（同类型断言两步走）
      const cfg = payload.config as unknown as ForwardRuleConfig;
      if (!cfg.target_chat_id) throw new Error("目标 chat_id 必填");
      if (cfg.source_kind === "keyword" && !(cfg.keyword || "").trim())
        throw new Error("关键词模式下 keyword 不能为空");
      if (cfg.source_kind === "peers" && !(cfg.source_peers?.length ?? 0))
        throw new Error("peers 模式下至少填一个 chat_id");
      if (!editing) {
        await createRule(aid, "forward", payload);
      } else {
        await updateRule(aid, "forward", editing.id, payload);
      }
    },
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "forward"] });
      setEditOpen(false);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: (rid: number) => deleteRule(aid, "forward", rid),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", "forward"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // ===================== 试运行 Dialog =====================
  const [dryOpen, setDryOpen] = useState(false);
  const [dryRule, setDryRule] = useState<RuleOut | null>(null);
  const [drySample, setDrySample] = useState("");
  const [dryChatId, setDryChatId] = useState("");
  const [dryResult, setDryResult] = useState<{
    matched: boolean;
    output?: string | null;
  } | null>(null);

  function openDryRun(rule: RuleOut) {
    setDryRule(rule);
    setDrySample("");
    setDryResult(null);
    const cfg = readConfig(rule.config);
    // peers 模式下默认带上第一个 chat_id 作样本，方便看到命中
    if (cfg.source_kind === "peers" && (cfg.source_peers || []).length) {
      setDryChatId(String(cfg.source_peers![0]));
    } else {
      setDryChatId("");
    }
    setDryOpen(true);
  }

  const dryMut = useMutation({
    mutationFn: () =>
      dryRunRule(aid, "forward", dryRule!.id, {
        sample_message: drySample,
        // forward 不区分 chat type，固定 group 即可（后端只看 source_kind）
        sample_chat_type: "group",
        sample_chat_id: dryChatId ? Number(dryChatId) : undefined,
      }),
    onSuccess: (res) => setDryResult(res),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (!aid) return <p>账号 ID 不合法</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav(`/accounts/${aid}`)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回账号
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">
          消息转发配置 · #{aid}
        </h1>
      </div>

      {/* 提示条 */}
      <div className="rounded-md border border-blue-200 bg-blue-50/60 px-3 py-2 text-xs text-blue-800 space-y-1">
        <div>✅ 保存后立即生效，无需重启 worker。</div>
        <div>
          ⚠ <b>仅响应别人发来的消息</b>（incoming）。本账号自己发的消息不会被转发。
        </div>
        <div>
          🚦 每条转发都会过风控引擎；触发 FloodWait 会自动 sleep ≤60s
          后重试一次，最终失败仅写日志，不会让 worker 崩溃。
        </div>
      </div>

      {/* 总开关 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">功能总开关</CardTitle>
              <CardDescription>
                关闭后所有转发规则都不会触发；启用即生效
              </CardDescription>
            </div>
            <Switch
              checked={featureEnabled}
              onCheckedChange={(v) => featureToggleMut.mutate(v)}
            />
          </div>
        </CardHeader>
      </Card>

      {/* 规则列表 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">规则</CardTitle>
              <CardDescription>
                按优先级排序；多条规则可同时命中（一对多）
              </CardDescription>
            </div>
            <Button onClick={openCreate}>
              <Plus className="mr-1 h-4 w-4" /> 新建规则
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {rulesQ.isLoading ? (
            <div className="flex h-20 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : rulesQ.data && rulesQ.data.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>启用</TableHead>
                  <TableHead>优先级</TableHead>
                  <TableHead>源</TableHead>
                  <TableHead>目标</TableHead>
                  <TableHead>方式</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rulesQ.data.map((r) => {
                  const cfg = readConfig(r.config);
                  return (
                    <TableRow key={r.id}>
                      <TableCell className="font-medium">{r.name}</TableCell>
                      <TableCell>
                        <Badge variant={r.enabled ? "success" : "secondary"}>
                          {r.enabled ? "ON" : "OFF"}
                        </Badge>
                      </TableCell>
                      <TableCell>{r.priority}</TableCell>
                      <TableCell>{sourceLabel(cfg)}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {cfg.target_chat_id || <span className="text-muted-foreground">未设置</span>}
                      </TableCell>
                      <TableCell>{modeLabel(cfg.mode)}</TableCell>
                      <TableCell className="text-right">
                        <div className="inline-flex gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => openEdit(r)}
                          >
                            <Pencil className="mr-1 h-3.5 w-3.5" /> 编辑
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => openDryRun(r)}
                          >
                            <Play className="mr-1 h-3.5 w-3.5" /> 试运行
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-destructive"
                            onClick={() => {
                              if (confirm(`删除规则 ${r.name}？`))
                                delMut.mutate(r.id);
                            }}
                          >
                            <Trash2 className="mr-1 h-3.5 w-3.5" /> 删除
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              暂无规则，点击右上角「新建规则」
            </p>
          )}
        </CardContent>
      </Card>

      {/* 编辑 / 新建 */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑规则" : "新建规则"}</DialogTitle>
            <DialogDescription>
              "原生转发"显示原作者；"复制 / 引用"不显示；"仅链接"对公开超级群可点
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <Field label="名称">
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="启用">
                <div className="flex h-10 items-center">
                  <Switch
                    checked={form.enabled}
                    onCheckedChange={(v) => setForm({ ...form, enabled: v })}
                  />
                </div>
              </Field>
              <Field label="优先级（数字越大越优先）">
                <Input
                  inputMode="numeric"
                  value={form.priority.toString()}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      priority: Number(e.target.value.replace(/[^0-9]/g, "") || 0),
                    })
                  }
                />
              </Field>
            </div>

            <Field label="源筛选">
              <Select
                value={form.config.source_kind}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: {
                      ...form.config,
                      source_kind: e.target.value as ForwardSourceKind,
                    },
                  })
                }
              >
                <option value="all">所有 incoming 消息</option>
                <option value="peers">指定 peer 列表</option>
                <option value="keyword">关键词触发</option>
              </Select>
            </Field>

            {form.config.source_kind === "peers" && (
              <Field label="源 chat_id（每行 / 逗号 / 分号 分隔）">
                <Textarea
                  rows={4}
                  placeholder={
                    "例：\n" +
                    "  -1001234567890\n" +
                    "  1234567890\n" +
                    "  -1234567890"
                  }
                  value={peersText}
                  onChange={(e) => setPeersText(e.target.value)}
                />
              </Field>
            )}

            {form.config.source_kind === "keyword" && (
              <Field label="关键词（不区分大小写；包含即命中）">
                <Input
                  value={form.config.keyword || ""}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      config: { ...form.config, keyword: e.target.value },
                    })
                  }
                  placeholder="例：紧急"
                />
              </Field>
            )}

            <Field label="目标 chat_id（必填）">
              <Input
                inputMode="numeric"
                value={targetText}
                onChange={(e) =>
                  setTargetText(e.target.value.replace(/[^0-9-]/g, ""))
                }
                placeholder="例：-1001234567890（你的收藏夹 / 团队群）"
              />
            </Field>

            <Field label="转发方式">
              <Select
                value={form.config.mode}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: {
                      ...form.config,
                      mode: e.target.value as ForwardMode,
                    },
                  })
                }
              >
                <option value="forward_native">原生转发（携带原作者）</option>
                <option value="copy_text">复制文本（不显示原作者）</option>
                <option value="quote">引用包装（带"来自 X"前缀）</option>
                <option value="link_only">仅发链接（公开群可点）</option>
              </Select>
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="包含含媒体的消息">
                <div className="flex h-10 items-center gap-2">
                  <Switch
                    checked={form.config.include_media !== false}
                    onCheckedChange={(v) =>
                      setForm({
                        ...form,
                        config: { ...form.config, include_media: v },
                      })
                    }
                  />
                  <span className="text-xs text-muted-foreground">
                    关 = 仅纯文本通过
                  </span>
                </div>
              </Field>
            </div>

            <Field label="固定前缀（copy / quote / link_only 模式生效）">
              <Textarea
                rows={2}
                value={form.config.header || ""}
                onChange={(e) =>
                  setForm({
                    ...form,
                    config: { ...form.config, header: e.target.value },
                  })
                }
                placeholder="例：[团队预警] "
              />
            </Field>
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => saveMut.mutate()}
              disabled={saveMut.isPending}
            >
              {saveMut.isPending ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 试运行 */}
      <Dialog open={dryOpen} onOpenChange={setDryOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>试运行 · {dryRule?.name}</DialogTitle>
            <DialogDescription>
              输入一条样例消息，验证 source_kind 是否命中（不会真的下发转发）
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <Field label="样例消息">
              <Textarea
                rows={3}
                value={drySample}
                onChange={(e) => setDrySample(e.target.value)}
              />
            </Field>
            <Field label="样本来源 chat_id（peers 模式必填；其它可选）">
              <Input
                inputMode="numeric"
                placeholder="例：-1001234567890"
                value={dryChatId}
                onChange={(e) =>
                  setDryChatId(e.target.value.replace(/[^0-9-]/g, ""))
                }
              />
            </Field>

            {dryResult && (
              <div className="rounded-md border bg-muted/40 p-3 text-xs">
                <div className="mb-1">
                  命中：
                  <Badge variant={dryResult.matched ? "success" : "secondary"}>
                    {dryResult.matched ? "是" : "否"}
                  </Badge>
                </div>
                {dryResult.matched && dryResult.output != null && (
                  <pre className="whitespace-pre-wrap">{dryResult.output}</pre>
                )}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDryOpen(false)}>
              关闭
            </Button>
            <Button
              disabled={!drySample || dryMut.isPending}
              onClick={() => dryMut.mutate()}
            >
              {dryMut.isPending ? "运行中…" : "运行"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function sourceLabel(cfg: ForwardRuleConfig): string {
  switch (cfg.source_kind) {
    case "all":
      return "所有 incoming";
    case "peers":
      return `指定 peers (${cfg.source_peers?.length ?? 0})`;
    case "keyword":
      return `关键词「${cfg.keyword || ""}」`;
    default:
      return cfg.source_kind;
  }
}

function modeLabel(m: ForwardMode): string {
  switch (m) {
    case "forward_native":
      return "原生转发";
    case "copy_text":
      return "复制文本";
    case "quote":
      return "引用包装";
    case "link_only":
      return "仅链接";
    default:
      return m;
  }
}

===== frontend/src/pages/Features/GroupAdmin.tsx =====
// 群组管理配置 - TODO
import { FeatureTodoPage } from "./TodoPage";
export function GroupAdminConfig() {
  return (
    <FeatureTodoPage
      title="群组管理"
      description="入群欢迎 / 反垃圾 / 黑名单 / 关键词处置"
    />
  );
}

===== frontend/src/pages/Features/Monitor.tsx =====
// 消息监控配置 - TODO
import { FeatureTodoPage } from "./TodoPage";
export function MonitorConfig() {
  return (
    <FeatureTodoPage
      title="消息监控"
      description="关键词命中告警 / 多账号归档 / 跨账号搜索"
    />
  );
}

===== frontend/src/pages/Features/Scheduler.tsx =====
// 定时任务配置 - TODO
import { FeatureTodoPage } from "./TodoPage";
export function SchedulerConfig() {
  return (
    <FeatureTodoPage
      title="定时任务"
      description="cron 触发 / 多账号广播 / 单会话定向"
    />
  );
}

===== frontend/src/pages/Features/TodoPage.tsx =====
// 共用 TODO 占位组件：feature 配置页未实现时复用
import { ArrowLeft } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface TodoPageProps {
  title: string;
  description: string;
}

export function FeatureTodoPage({ title, description }: TodoPageProps) {
  const nav = useNavigate();
  const { aid } = useParams();
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav(`/accounts/${aid}`)}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回账号
        </Button>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">即将上线</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          MVP 阶段仅完成自动回复的完整可视化配置；该功能页面将在下一迭代实现。
          可以通过 API 直连完成基础规则的写入。
        </CardContent>
      </Card>
    </div>
  );
}

===== frontend/src/pages/Login.tsx =====
// 登录页：支持 TOTP 二次输入 + 首次部署兜底注册流程
//
// iOS / iPadOS 注意事项：
//   ``<input type="password">`` 在 iOS 上**强制使用系统键盘**（系统级安全策略），
//   第三方输入法（搜狗、百度等）无法工作。变通方案：在密码框右侧加一个"显示密码"
//   按钮（type 切到 "text"）——切到 text 后系统不限制输入法，用户可用第三方输入法
//   输完后再切回隐藏。这是 web 应用的通用做法（GitHub / Google 都这么做）。
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { Eye, EyeOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getErrCode, getErrMsg } from "@/lib/api";
import { login, register } from "@/lib/auth";

type Mode = "login" | "register";

export function Login() {
  const nav = useNavigate();
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [totpCode, setTotpCode] = useState("");
  const [needTotp, setNeedTotp] = useState(false);

  // 登录 mutation：返回 require_totp 时切换到第二步
  const loginMut = useMutation({
    mutationFn: () =>
      login({
        username,
        password,
        totp_code: needTotp ? totpCode : null,
      }),
    onSuccess: (res) => {
      if (res.require_totp && !needTotp) {
        setNeedTotp(true);
        toast.info("请输入二次验证码（TOTP）");
        return;
      }
      toast.success("登录成功");
      nav("/", { replace: true });
    },
    onError: (err) => {
      const code = getErrCode(err);
      // 后端约定：系统尚未创建用户时返回 NO_USER → 引导注册
      if (code === "NO_USER" || code === "USER_NOT_INITIALIZED") {
        toast.info("尚未创建管理员账号，请先注册");
        setMode("register");
        return;
      }
      if (code === "TOTP_REQUIRED") {
        setNeedTotp(true);
        toast.info("请输入二次验证码（TOTP）");
        return;
      }
      toast.error(getErrMsg(err));
    },
  });

  const registerMut = useMutation({
    mutationFn: () => register(username, password),
    onSuccess: () => {
      toast.success("注册成功，正在登录…");
      setMode("login");
      loginMut.mutate();
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      toast.error("请填写用户名和密码");
      return;
    }
    if (mode === "login") loginMut.mutate();
    else registerMut.mutate();
  };

  const isLogin = mode === "login";
  const submitting = loginMut.isPending || registerMut.isPending;

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{isLogin ? "登录" : "首次部署 · 创建管理员"}</CardTitle>
          <CardDescription>
            {isLogin
              ? "Telegram Userbot 管理后台"
              : "本系统仅有一个超级管理员，密码请妥善保管"}
          </CardDescription>
        </CardHeader>
        <form onSubmit={onSubmit}>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="username">用户名</Label>
              <Input
                id="username"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">密码</Label>
              {/*
                密码框 + 右侧显示/隐藏按钮：iOS Safari 在 type="password" 时强制系统键盘，
                切到 type="text" 后第三方输入法（搜狗 / 百度等）才能工作。
              */}
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete={isLogin ? "current-password" : "new-password"}
                  // 切到 text 时关掉自动大写 / 自动更正避免污染密码
                  autoCapitalize={showPassword ? "none" : undefined}
                  autoCorrect={showPassword ? "off" : undefined}
                  spellCheck={showPassword ? false : undefined}
                  className="pr-10"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  title={
                    showPassword
                      ? "隐藏密码（切回安全的系统键盘）"
                      : "显示密码（iOS 上可用第三方输入法）"
                  }
                  // tabindex=-1 让 Tab 不停在按钮上，密码 → 提交直接 enter
                  tabIndex={-1}
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
              {showPassword && (
                <p className="text-[11px] text-amber-600">
                  ⚠ 密码已显示；输完后建议点击眼睛图标隐藏
                </p>
              )}
            </div>
            {isLogin && needTotp && (
              <div className="space-y-1.5">
                <Label htmlFor="totp">二次验证码 (TOTP)</Label>
                <Input
                  id="totp"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="6 位数字"
                />
              </div>
            )}
          </CardContent>
          <CardFooter className="flex flex-col gap-2">
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "提交中…" : isLogin ? "登录" : "注册并登录"}
            </Button>
            <button
              type="button"
              className="text-xs text-muted-foreground hover:underline"
              onClick={() => {
                setMode(isLogin ? "register" : "login");
                setNeedTotp(false);
                setTotpCode("");
              }}
            >
              {isLogin ? "首次部署？点此创建管理员" : "已有账号？返回登录"}
            </button>
          </CardFooter>
        </form>
      </Card>
    </div>
  );
}

===== frontend/src/pages/Logs.tsx =====
// 日志中心：runtime_log 拆成两个 tab —— 消息日志 / 系统日志
//
// 消息日志（source=event）：incoming 消息进来、plugin 命中、命令派发等业务事件，
// 适合用于"为什么我的 auto_reply 没回复 / 转发到底有没有发出"这类问题排查。
//
// 系统日志（source=system）：worker 启停、IPC reload、风控状态、技术异常，
// 适合用于"账号是不是真的 active / kill switch 是不是真的下发了"这类问题排查。
//
// 两个 tab 共享下方账号 / level 过滤器；切换 tab 不重置过滤；自动刷新只在当前
// tab 上拉，避免重复请求。
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/misc";
import { listRuntimeLogs } from "@/api/system";
import { listAccounts } from "@/api/accounts";
import { formatDateTime } from "@/lib/utils";
import type { RuntimeLogItem } from "@/api/types";

const LEVEL_VARIANT: Record<
  string,
  "secondary" | "warn" | "destructive" | "success"
> = {
  debug: "secondary",
  info: "success",
  warning: "warn",
  warn: "warn",
  error: "destructive",
};

type LogTab = "event" | "system";

export function Logs() {
  const [tab, setTab] = useState<LogTab>("event");
  const [accountId, setAccountId] = useState("");
  const [level, setLevel] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">日志中心</h1>
        <p className="text-sm text-muted-foreground">
          消息日志（业务事件）与系统日志（worker / 错误）分开看；默认 5 秒自动刷新
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">过滤</CardTitle>
          <CardDescription>账号 / 级别 / 自动刷新——两 tab 共用</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-4 sm:items-end">
            <div className="space-y-1.5">
              <Label>账号</Label>
              <Select
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
              >
                <option value="">全部</option>
                {accountsQ.data?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.display_name || a.phone}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>级别</Label>
              <Select value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="">全部</option>
                <option value="debug">debug</option>
                <option value="info">info</option>
                <option value="warning">warning</option>
                <option value="error">error</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>自动刷新</Label>
              <div className="flex h-10 items-center gap-2">
                <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} />
                <span className="text-sm text-muted-foreground">
                  {autoRefresh ? "5s 拉取一次" : "已停止"}
                </span>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>since (ISO 时间，可选)</Label>
              <Input placeholder="2026-05-02T00:00:00Z" disabled />
            </div>
          </div>
        </CardContent>
      </Card>

      <Tabs value={tab} onValueChange={(v) => setTab(v as LogTab)}>
        <TabsList>
          <TabsTrigger value="event">📨 消息日志</TabsTrigger>
          <TabsTrigger value="system">⚙️ 系统日志</TabsTrigger>
        </TabsList>

        <TabsContent value="event">
          <LogTable
            source="event"
            accountId={accountId}
            level={level}
            autoRefresh={autoRefresh && tab === "event"}
            description="incoming 消息事件、plugin 命中、命令派发——排查「为什么没回复 / 转发出去没」用这里"
          />
        </TabsContent>

        <TabsContent value="system">
          <LogTable
            source="system"
            accountId={accountId}
            level={level}
            autoRefresh={autoRefresh && tab === "system"}
            description="worker 启停、IPC reload、风控状态、技术异常——排查「账号是不是真的活着」用这里"
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── 单个 tab 的日志表 ─────────────────────────────────────────────
function LogTable({
  source,
  accountId,
  level,
  autoRefresh,
  description,
}: {
  source: "event" | "system";
  accountId: string;
  level: string;
  autoRefresh: boolean;
  description: string;
}) {
  const filters = {
    source,
    account_id: accountId || undefined,
    level: level || undefined,
    limit: 200,
  };
  const logsQ = useQuery({
    queryKey: ["logs", filters],
    queryFn: () => listRuntimeLogs(filters),
    refetchInterval: autoRefresh ? 5_000 : false,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {source === "event" ? "消息日志" : "系统日志"}
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        {logsQ.isLoading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : logsQ.data && logsQ.data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-40">时间</TableHead>
                <TableHead className="w-20">级别</TableHead>
                <TableHead className="w-24">账号</TableHead>
                <TableHead>消息</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {logsQ.data.map((l: RuntimeLogItem) => (
                <TableRow key={l.id}>
                  <TableCell className="font-mono text-xs">
                    {formatDateTime(l.created_at)}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        LEVEL_VARIANT[l.level.toLowerCase()] ?? "secondary"
                      }
                    >
                      {l.level.toUpperCase()}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {l.account_id ? `#${l.account_id}` : "—"}
                  </TableCell>
                  <TableCell className="font-mono text-xs whitespace-pre-wrap">
                    {l.message}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="py-8 text-center text-sm text-muted-foreground">
            该分类暂无日志
            {source === "event"
              ? " — 让人给本账号发条消息，再回来看"
              : " — 没有错误是好事"}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

===== frontend/src/pages/Plugins.tsx =====
// 顶层「插件管理」页：把原来散落在系统设置里的 PluginManager / PluginMarket 提到这里，
// 用 Tabs 区分"已安装"和"插件市场"两个子页面。
//
// 路由：/plugins、/plugins/installed（默认）、/plugins/market
// 也可以直接访问 /plugins?tab=market。
import { useSearchParams } from "react-router-dom";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PluginManager } from "@/pages/Settings/PluginManager";
import { PluginMarket } from "@/pages/Settings/PluginMarket";

const VALID_TABS = ["installed", "market"] as const;
type TabKey = (typeof VALID_TABS)[number];

function pickTab(raw: string | null): TabKey {
  return (VALID_TABS as readonly string[]).includes(raw ?? "")
    ? (raw as TabKey)
    : "installed";
}

export function Plugins() {
  // 把当前 tab 同步到 ?tab=xxx，方便分享 / 收藏 / 浏览器后退
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = pickTab(searchParams.get("tab"));

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">插件管理</h1>
        <p className="text-sm text-muted-foreground">
          管理已安装的第三方插件，或从插件市场订阅 / 远程安装新插件
        </p>
      </div>

      <Tabs
        value={tab}
        onValueChange={(v) => {
          const next = new URLSearchParams(searchParams);
          if (v === "installed") next.delete("tab");
          else next.set("tab", v);
          setSearchParams(next, { replace: true });
        }}
      >
        <TabsList>
          <TabsTrigger value="installed">已安装</TabsTrigger>
          <TabsTrigger value="market">插件市场</TabsTrigger>
        </TabsList>

        <TabsContent value="installed">
          <PluginManager />
        </TabsContent>
        <TabsContent value="market">
          <PluginMarket />
        </TabsContent>
      </Tabs>
    </div>
  );
}

===== frontend/src/pages/Settings/CommandTemplates.tsx =====
// 系统设置 → 自定义命令模板（4 种类型：reply_text / forward_to / run_plugin / ai）
//
// 设计：
//   列表页：全表展示模板，name 徽章 type，编辑/删除按钮
//   编辑对话框：根据 type 切不同子表单
//   保存后后端会通知所有启用此模板的 worker 热加载
import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Trash2, Edit3 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/misc";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import {
  createCommandTemplate,
  deleteCommandTemplate,
  listCommandTemplates,
  listLLMProviders,
  patchCommandTemplate,
} from "@/api/commands";
import { getSystemSettings } from "@/api/system";
import type {
  CommandTemplateOut,
  CommandTemplateType,
  LLMProviderOut,
} from "@/api/types";
import { getErrMsg } from "@/lib/api";

// 命令名仅允许 [a-zA-Z0-9_]，与后端正则对齐
const NAME_RE = /^[a-zA-Z0-9_]{1,64}$/;

const TYPE_LABELS: Record<CommandTemplateType, string> = {
  reply_text: "回复文本",
  forward_to: "转发到",
  run_plugin: "调插件",
  ai: "AI",
};

// ── 消息格式预设（与后端 services/llm_format.py 的 PRESETS 同源）─────────
//
// 这些字符串必须**逐字**与后端 PRESET_SIMPLE / PRESET_QUOTE / PRESET_MINIMAL /
// PRESET_TRANSLATE 一致。改后端要同步改这里；改这里要同步改后端。
//
// 注意：output_format 默认 'html'（Telethon 1.36 不接受 'markdownv2' 字符串）；
// 这些预设里的 <b> <blockquote expandable> 等是字面 HTML，渲染时只对占位符值做
// HTML 转义，模板自身的标签保留。
const PRESET_SIMPLE_TEMPLATE =
  "{answer}\n\n— {model} · in {in_tokens} / out {out_tokens}{?routing_note}  ·  {routing_note}{/?}";

const PRESET_QUOTE_TEMPLATE =
  "{?display_input}<blockquote>{display_input}</blockquote>\n" +
  "{/?}<b>✨ AI 回答</b>\n" +
  "{answer_first_2}" +
  "{?answer_rest}\n<blockquote expandable>{answer_rest}</blockquote>{/?}\n\n" +
  "━━━━━━━━━━━━━━━\n" +
  "{model} · {provider}\n" +
  "In: {in_tokens} | Out: {out_tokens} | Total: {total_tokens}" +
  "{?routing_note}\n{routing_note}{/?}";

const PRESET_MINIMAL_TEMPLATE = "{answer}\n<code>{model}</code> · {total_tokens}t";

// 翻译/简答风：不显示 quoted（即使 quote_replied=True 仅供模型上下文）
// 适合 ,翻译 / ,简答 / ,润色 等命令
const PRESET_TRANSLATE_TEMPLATE = "{answer}\n\n<i>— {model}</i>";

const FORMAT_PRESETS: Array<{ key: string; label: string; tpl: string; desc: string }> = [
  { key: "simple", label: "简洁（默认）", tpl: PRESET_SIMPLE_TEMPLATE, desc: "答案 + 一行 footer；任何模式下都好看" },
  { key: "quote", label: "引用风", tpl: PRESET_QUOTE_TEMPLATE, desc: "alma 风；前 2 行 + 折叠剩余（HTML 模式）" },
  { key: "minimal", label: "极简", tpl: PRESET_MINIMAL_TEMPLATE, desc: "答案 + 模型 + 总 tokens" },
  { key: "translate", label: "翻译/简答风", tpl: PRESET_TRANSLATE_TEMPLATE, desc: "不显示被引用原文；适合 ,翻译 / ,简答 这类" },
];

// 占位符按钮元数据；与后端 PLACEHOLDER_META 同源
const PLACEHOLDER_BUTTONS: Array<{ insert: string; label: string; desc: string }> = [
  { insert: "{answer}", label: "[回答]", desc: "AI 的回答正文" },
  { insert: "{answer_first_2}", label: "[回答-前2行]", desc: "回答的前 2 行（折叠用）" },
  { insert: "{answer_rest}", label: "[回答-剩余]", desc: "回答从第 3 行起（配 **>...** 折叠）" },
  { insert: "{question}", label: "[问题]", desc: "用户在命令后跟的问题" },
  { insert: "{quoted}", label: "[被引用]", desc: "被回复消息的正文（无被回复时为空）" },
  { insert: "{model}", label: "[模型]", desc: "API 实际返回的模型名" },
  { insert: "{provider}", label: "[提供商]", desc: "提供商名称（如 Any GPT）" },
  { insert: "{provider_kind}", label: "[厂商]", desc: "openai / anthropic / ollama" },
  { insert: "{in_tokens}", label: "[输入tokens]", desc: "输入 token 数" },
  { insert: "{out_tokens}", label: "[输出tokens]", desc: "输出 token 数" },
  { insert: "{total_tokens}", label: "[总tokens]", desc: "输入 + 输出" },
  { insert: "{routing_note}", label: "[路由说明]", desc: "auto 模式的决策原因（fixed 模式空）" },
  { insert: "{time}", label: "[时间]", desc: "当前时间 HH:MM" },
];

const CONDITIONAL_BUTTONS: Array<{ snippet: string; label: string; desc: string }> = [
  {
    snippet: "{?quoted}\n\n{/?}",
    label: "[条件:被引用]",
    desc: "仅当被回复消息非空才渲染括号内",
  },
  {
    snippet: "{?routing_note}\n\n{/?}",
    label: "[条件:路由]",
    desc: "仅 auto 模式才渲染括号内",
  },
  {
    snippet: "{?answer_rest}\n**>{answer_rest}**{/?}",
    label: "[条件:有剩余]",
    desc: "仅当回答超过 2 行才渲染（配折叠块用）",
  },
];

interface FormState {
  id?: number;
  name: string;
  type: CommandTemplateType;
  description: string;
  // 各 type 的 config 字段散开存，按 type 切表单时拼回
  text: string;
  target_chat_id: string;
  plugin_key: string;
  plugin_method: string;
  plugin_args: string; // JSON string
  ai_provider_id: string; // <select> value，转 number 后下发
  ai_model: string;
  ai_system_prompt: string;
  ai_max_tokens: string;
  ai_quote_replied: boolean;
  // ── 路由（auto 模式才用到，fixed 留空即可）──
  ai_routing_mode: "fixed" | "auto";
  ai_routing_fallback_provider_id: string;  // <select> value
  ai_classifier_provider_id: string;        // <select> value，可空
  // ── 输出格式（消息编辑回 TG 时长什么样）──
  ai_output_format: "html" | "markdown" | "plain";
  ai_output_template: string;
  ai_escape_values: boolean;
}

const EMPTY_FORM: FormState = {
  name: "",
  type: "reply_text",
  description: "",
  text: "",
  target_chat_id: "",
  plugin_key: "",
  plugin_method: "",
  plugin_args: "[]",
  ai_provider_id: "",
  ai_model: "",
  ai_system_prompt: "你是简洁有用的中文助手。回答控制在 100 字内。",
  ai_max_tokens: "512",
  ai_quote_replied: true,
  ai_routing_mode: "fixed",
  ai_routing_fallback_provider_id: "",
  ai_classifier_provider_id: "",
  ai_output_format: "html",
  ai_output_template: "",
  ai_escape_values: true,
};

function formFromTemplate(t: CommandTemplateOut): FormState {
  const cfg = t.config || {};
  return {
    id: t.id,
    name: t.name,
    type: t.type,
    description: t.description || "",
    text: typeof cfg.text === "string" ? (cfg.text as string) : "",
    target_chat_id:
      cfg.target_chat_id !== undefined && cfg.target_chat_id !== null
        ? String(cfg.target_chat_id)
        : "",
    plugin_key: typeof cfg.plugin_key === "string" ? (cfg.plugin_key as string) : "",
    plugin_method: typeof cfg.method === "string" ? (cfg.method as string) : "",
    plugin_args: cfg.args ? JSON.stringify(cfg.args) : "[]",
    ai_provider_id:
      cfg.provider_id !== undefined && cfg.provider_id !== null
        ? String(cfg.provider_id)
        : "",
    ai_model: typeof cfg.model === "string" ? (cfg.model as string) : "",
    ai_system_prompt:
      typeof cfg.system_prompt === "string"
        ? (cfg.system_prompt as string)
        : EMPTY_FORM.ai_system_prompt,
    ai_max_tokens:
      cfg.max_tokens !== undefined && cfg.max_tokens !== null
        ? String(cfg.max_tokens)
        : "512",
    ai_quote_replied: cfg.quote_replied !== false, // 默认 true
    ai_routing_mode:
      cfg.routing_mode === "auto" ? "auto" : "fixed",
    ai_routing_fallback_provider_id:
      cfg.routing_fallback_provider_id !== undefined &&
      cfg.routing_fallback_provider_id !== null
        ? String(cfg.routing_fallback_provider_id)
        : "",
    ai_classifier_provider_id:
      cfg.classifier_provider_id !== undefined &&
      cfg.classifier_provider_id !== null
        ? String(cfg.classifier_provider_id)
        : "",
    ai_output_format:
      cfg.output_format === "html" ||
      cfg.output_format === "markdown" ||
      cfg.output_format === "plain"
        ? cfg.output_format
        : "html", // 老 'markdownv2' / 缺省 → 默认 html
    ai_output_template: typeof cfg.output_template === "string" ? cfg.output_template : "",
    ai_escape_values: cfg.escape_values !== false,
  };
}

// 根据 type 拼出 config 对象 + 入参校验
function buildPayload(form: FormState): {
  ok: boolean;
  errMsg?: string;
  config?: Record<string, unknown>;
} {
  const t = form.type;
  if (t === "reply_text") {
    return { ok: true, config: { text: form.text } };
  }
  if (t === "forward_to") {
    const v = form.target_chat_id.trim();
    if (!v) return { ok: false, errMsg: "target_chat_id 必填" };
    const n = Number(v);
    if (!Number.isInteger(n)) return { ok: false, errMsg: "target_chat_id 必须是整数" };
    return { ok: true, config: { target_chat_id: n } };
  }
  if (t === "run_plugin") {
    if (!form.plugin_key.trim())
      return { ok: false, errMsg: "plugin_key 必填" };
    let args: unknown = [];
    try {
      args = form.plugin_args ? JSON.parse(form.plugin_args) : [];
    } catch {
      return { ok: false, errMsg: "args 不是合法 JSON" };
    }
    return {
      ok: true,
      config: {
        plugin_key: form.plugin_key.trim(),
        method: form.plugin_method.trim() || undefined,
        args,
      },
    };
  }
  // ai
  const pid = Number(form.ai_provider_id);
  if (!Number.isInteger(pid) || pid <= 0)
    return { ok: false, errMsg: "AI 类型必须选 LLM Provider" };
  const mt = form.ai_max_tokens.trim();
  const cfg: Record<string, unknown> = {
    provider_id: pid,
    quote_replied: form.ai_quote_replied,
    system_prompt: form.ai_system_prompt,
    routing_mode: form.ai_routing_mode,
  };
  if (form.ai_model.trim()) cfg.model = form.ai_model.trim();
  if (mt) cfg.max_tokens = Number(mt) || 512;
  // 路由字段：只在 auto 模式下下发，避免 fixed 留脏数据
  if (form.ai_routing_mode === "auto") {
    if (form.ai_routing_fallback_provider_id.trim()) {
      const fb = Number(form.ai_routing_fallback_provider_id);
      if (!Number.isInteger(fb) || fb <= 0)
        return { ok: false, errMsg: "兜底 provider 必须是有效 LLM Provider" };
      cfg.routing_fallback_provider_id = fb;
    }
    if (form.ai_classifier_provider_id.trim()) {
      const cls = Number(form.ai_classifier_provider_id);
      if (!Number.isInteger(cls) || cls <= 0)
        return { ok: false, errMsg: "分类器 provider 必须是有效 LLM Provider" };
      cfg.classifier_provider_id = cls;
    }
  }
  // 输出格式（默认 html + 空模板 = 用后端的 PRESET_SIMPLE）
  cfg.output_format = form.ai_output_format;
  if (form.ai_output_template.trim()) {
    cfg.output_template = form.ai_output_template;
  }
  // escape_values 默认 true；非默认值才下发
  if (!form.ai_escape_values) {
    cfg.escape_values = false;
  }
  return { ok: true, config: cfg };
}

export function CommandTemplates() {
  const qc = useQueryClient();
  const listQ = useQuery({
    queryKey: ["cmd-tpl"],
    queryFn: listCommandTemplates,
  });
  // 实时拉系统命令前缀，用在编辑器的"`,name` 触发"那行提示——避免硬编码逗号
  // 跟系统设置改了不一致
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const cmdPrefix = settingsQ.data?.command_prefix || ",";

  const [editing, setEditing] = useState<FormState | null>(null);

  const createMut = useMutation({
    mutationFn: (form: FormState) => {
      const r = buildPayload(form);
      if (!r.ok) throw new Error(r.errMsg || "config 校验失败");
      return createCommandTemplate({
        name: form.name.trim(),
        type: form.type,
        config: r.config!,
        description: form.description || null,
      });
    },
    onSuccess: () => {
      toast.success("已新建模板");
      qc.invalidateQueries({ queryKey: ["cmd-tpl"] });
      setEditing(null);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const updateMut = useMutation({
    mutationFn: (form: FormState) => {
      if (!form.id) throw new Error("缺少 id");
      const r = buildPayload(form);
      if (!r.ok) throw new Error(r.errMsg || "config 校验失败");
      return patchCommandTemplate(form.id, {
        name: form.name.trim(),
        type: form.type,
        config: r.config!,
        description: form.description || null,
      });
    },
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["cmd-tpl"] });
      setEditing(null);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteCommandTemplate(id),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["cmd-tpl"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">自定义命令模板</CardTitle>
            <CardDescription>
              全局模板库，每条 = 一个 `,name` 命令的"配方"。账号详情 → 命令 tab 选择是否启用
            </CardDescription>
          </div>
          <Button size="sm" onClick={() => setEditing({ ...EMPTY_FORM })}>
            <Plus className="mr-1 h-4 w-4" /> 新建
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {listQ.isLoading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : listQ.data && listQ.data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>命令</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>说明</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {listQ.data.map((t) => (
                <TableRow key={t.id}>
                  <TableCell className="font-mono text-sm">{cmdPrefix}{t.name}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">{TYPE_LABELS[t.type] || t.type}</Badge>
                  </TableCell>
                  <TableCell className="max-w-[420px] truncate text-xs text-muted-foreground">
                    {t.description || "—"}
                  </TableCell>
                  <TableCell className="space-x-2 text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setEditing(formFromTemplate(t))}
                    >
                      <Edit3 className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={deleteMut.isPending}
                      onClick={() => {
                        if (
                          confirm(
                            `确认删除模板「${t.name}」？所有启用此模板的账号都会失去这个命令`,
                          )
                        ) {
                          deleteMut.mutate(t.id);
                        }
                      }}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="rounded-md border border-dashed py-8 text-center text-xs text-muted-foreground">
            尚无模板。新建一个后即可在账号详情中勾选启用
          </p>
        )}
      </CardContent>

      {editing && (
        <CommandEditDialog
          form={editing}
          cmdPrefix={cmdPrefix}
          onChange={setEditing}
          onCancel={() => setEditing(null)}
          onSave={() => {
            const trimName = editing.name.trim();
            if (!NAME_RE.test(trimName)) {
              toast.error("命令名只能包含字母 / 数字 / 下划线，1-64 字符");
              return;
            }
            if (editing.id) {
              updateMut.mutate(editing);
            } else {
              createMut.mutate(editing);
            }
          }}
          saving={createMut.isPending || updateMut.isPending}
        />
      )}
    </Card>
  );
}

function CommandEditDialog({
  form,
  cmdPrefix,
  onChange,
  onCancel,
  onSave,
  saving,
}: {
  form: FormState;
  cmdPrefix: string;
  onChange: (s: FormState) => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
}) {
  const isEdit = !!form.id;
  const setField = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    onChange({ ...form, [k]: v });

  // ai 类型才需要拉 provider 列表
  const providersQ = useQuery({
    queryKey: ["llm-providers"],
    queryFn: listLLMProviders,
    enabled: form.type === "ai",
  });

  // 切类型时清相邻字段，避免上次填的脏数据落到 config
  const typeOptions = useMemo(
    () => Object.entries(TYPE_LABELS) as [CommandTemplateType, string][],
    [],
  );

  return (
    <Dialog open onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑" : "新建"}命令模板</DialogTitle>
          <DialogDescription>
            根据类型不同，下方表单会切到对应字段
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>命令名 *</Label>
              <Input
                value={form.name}
                maxLength={64}
                placeholder="hi / ai / forward_to_devs"
                onChange={(e) => setField("name", e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                只允许字母 / 数字 / 下划线；TG 内用 <code>{cmdPrefix}{form.name || "name"}</code> 触发
              </p>
            </div>
            <div className="space-y-1.5">
              <Label>类型 *</Label>
              <Select
                value={form.type}
                onChange={(e) =>
                  setField("type", e.target.value as CommandTemplateType)
                }
              >
                {typeOptions.map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}（{k}）
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>说明（可选）</Label>
            <Input
              value={form.description}
              maxLength={255}
              placeholder="便于 ,help 显示"
              onChange={(e) => setField("description", e.target.value)}
            />
          </div>

          {/* 按 type 切不同子表单 */}
          {form.type === "reply_text" && (
            <div className="space-y-1.5">
              <Label>回复文本 *</Label>
              <Textarea
                value={form.text}
                rows={4}
                placeholder="hello {args}"
                onChange={(e) => setField("text", e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                支持 `{"{args}"}` 占位符，会被命令后跟的参数替换
              </p>
            </div>
          )}

          {form.type === "forward_to" && (
            <div className="space-y-1.5">
              <Label>目标 chat_id *</Label>
              <Input
                inputMode="numeric"
                value={form.target_chat_id}
                onChange={(e) =>
                  setField(
                    "target_chat_id",
                    e.target.value.replace(/[^\d-]/g, ""),
                  )
                }
                placeholder="-1001234567890"
              />
              <p className="text-xs text-muted-foreground">
                群 ID 怎么填：在该群里执行 `,id` 可获得 chat_id；超级群以 -100 开头
              </p>
            </div>
          )}

          {form.type === "run_plugin" && (
            <div className="space-y-3">
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                ⏳ run_plugin 占位类型：V1 接口未实装，此处配置仅会保存到 DB；待 Sprint2 #4 插件模块化完成后接入
              </div>
              <div className="space-y-1.5">
                <Label>plugin_key *</Label>
                <Input
                  value={form.plugin_key}
                  maxLength={64}
                  onChange={(e) => setField("plugin_key", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>method（可选）</Label>
                <Input
                  value={form.plugin_method}
                  maxLength={64}
                  onChange={(e) => setField("plugin_method", e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>args（JSON 数组）</Label>
                <Input
                  value={form.plugin_args}
                  onChange={(e) => setField("plugin_args", e.target.value)}
                  placeholder='[]'
                />
              </div>
            </div>
          )}

          {form.type === "ai" && (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label>
                  {form.ai_routing_mode === "auto"
                    ? "默认 / 兜底模型 *"
                    : "提供商 + 模型 *"}
                </Label>
                <ProviderModelSelect
                  value={
                    form.ai_provider_id && form.ai_model
                      ? `${form.ai_provider_id}|${form.ai_model}`
                      : form.ai_provider_id
                      ? `${form.ai_provider_id}|`
                      : ""
                  }
                  providers={providersQ.data}
                  loading={providersQ.isLoading}
                  onChange={(v) => {
                    // 选项 value 形如 "<pid>|<model>"
                    const sep = v.indexOf("|");
                    if (sep < 0) {
                      setField("ai_provider_id", "");
                      setField("ai_model", "");
                      return;
                    }
                    const pid = v.slice(0, sep);
                    const model = v.slice(sep + 1);
                    setField("ai_provider_id", pid);
                    setField("ai_model", model);
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  下拉里每条 = 一个已启用的 (提供商 × 模型) 组合。要新增/启用模型去
                  <span className="mx-1 font-medium">AI 设置 → 模型提供商</span>编辑。
                  {form.ai_routing_mode === "auto"
                    ? " auto 模式下，规则未命中且未设独立兜底时走这条"
                    : ""}
                </p>
              </div>
              <div className="space-y-1.5">
                <Label>System Prompt</Label>
                <Textarea
                  value={form.ai_system_prompt}
                  rows={3}
                  onChange={(e) => setField("ai_system_prompt", e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>max_tokens</Label>
                  <Input
                    inputMode="numeric"
                    value={form.ai_max_tokens}
                    onChange={(e) =>
                      setField(
                        "ai_max_tokens",
                        e.target.value.replace(/[^\d]/g, ""),
                      )
                    }
                  />
                </div>
                <div className="flex items-center gap-2 self-end pb-2">
                  <Switch
                    checked={form.ai_quote_replied}
                    onCheckedChange={(v) => setField("ai_quote_replied", v)}
                    id="quoteReplied"
                  />
                  <Label htmlFor="quoteReplied" className="cursor-pointer">
                    引用被回复消息内容
                  </Label>
                </div>
              </div>

              {/* ── 路由模式 ────────────────────────────── */}
              <div className="rounded-md border bg-muted/30 p-3 space-y-3">
                <div>
                  <Label className="text-sm font-semibold">路由模式</Label>
                  <p className="text-xs text-muted-foreground">
                    fixed = 永远用上面选的固定 provider；auto = 看消息类型自动路由（详见 AI
                    设置页推荐配置）
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <label
                    className={
                      "cursor-pointer rounded-md border p-3 text-sm transition-colors " +
                      (form.ai_routing_mode === "fixed"
                        ? "border-primary bg-primary/5"
                        : "hover:bg-muted")
                    }
                  >
                    <input
                      type="radio"
                      name="routingMode"
                      className="mr-2"
                      checked={form.ai_routing_mode === "fixed"}
                      onChange={() => setField("ai_routing_mode", "fixed")}
                    />
                    <span className="font-medium">fixed（固定）</span>
                    <p className="mt-1 text-xs text-muted-foreground">
                      简单可控；适合"我就要某个模型"
                    </p>
                  </label>
                  <label
                    className={
                      "cursor-pointer rounded-md border p-3 text-sm transition-colors " +
                      (form.ai_routing_mode === "auto"
                        ? "border-primary bg-primary/5"
                        : "hover:bg-muted")
                    }
                  >
                    <input
                      type="radio"
                      name="routingMode"
                      className="mr-2"
                      checked={form.ai_routing_mode === "auto"}
                      onChange={() => setField("ai_routing_mode", "auto")}
                    />
                    <span className="font-medium">auto（自动路由）</span>
                    <p className="mt-1 text-xs text-muted-foreground">
                      按消息类型选 provider；省钱 + 更对路
                    </p>
                  </label>
                </div>

                {form.ai_routing_mode === "auto" && (
                  <div className="space-y-3">
                    <div className="space-y-1.5">
                      <Label>独立兜底 provider（可选）</Label>
                      <ProviderSelect
                        value={form.ai_routing_fallback_provider_id}
                        providers={providersQ.data}
                        loading={providersQ.isLoading}
                        onChange={(v) =>
                          setField("ai_routing_fallback_provider_id", v)
                        }
                        allowEmpty
                      />
                      <p className="text-xs text-muted-foreground">
                        留空 = 直接复用上面那条「默认 / 兜底 LLM Provider」；想分开就在这选另一条
                      </p>
                    </div>
                    <div className="space-y-1.5">
                      <Label>分类器 provider（可选）</Label>
                      <ProviderSelect
                        value={form.ai_classifier_provider_id}
                        providers={providersQ.data}
                        loading={providersQ.isLoading}
                        onChange={(v) =>
                          setField("ai_classifier_provider_id", v)
                        }
                        allowEmpty
                      />
                      <p className="text-xs text-muted-foreground">
                        指定后：规则未命中时调一个轻量小模型（建议 tag=classify、cost_tier=1）让它
                        判断 code/math/translate/vision/reason/chat 中的哪一个
                      </p>
                    </div>
                  </div>
                )}
              </div>

              {/* ── 消息格式 ─────────────────────────────── */}
              <MessageFormatSection
                outputFormat={form.ai_output_format}
                onOutputFormatChange={(v) => setField("ai_output_format", v)}
                template={form.ai_output_template}
                onTemplateChange={(v) => setField("ai_output_template", v)}
                escapeValues={form.ai_escape_values}
                onEscapeValuesChange={(v) => setField("ai_escape_values", v)}
              />

              <p className="text-xs text-muted-foreground">
                调用流程：用户在 TG 中回复某消息并发 <code>{cmdPrefix}{form.name || "ai"} 问题</code>，worker 将「被回复消息正文 + 问题」拼成 user prompt，把回答编辑回原消息
              </p>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onCancel} disabled={saving}>
            取消
          </Button>
          <Button onClick={onSave} disabled={saving}>
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ProviderSelect({
  value,
  providers,
  loading,
  onChange,
  allowEmpty = false,
}: {
  value: string;
  providers?: LLMProviderOut[];
  loading: boolean;
  onChange: (v: string) => void;
  /** 允许"不选"；选了就 value="" 上送（CommandTemplates 在保存时会按情况省略字段） */
  allowEmpty?: boolean;
}) {
  if (loading) {
    return (
      <div className="flex h-10 items-center gap-2 rounded-md border px-3 text-xs text-muted-foreground">
        <Spinner className="text-primary" /> 加载中…
      </div>
    );
  }
  if (!providers || providers.length === 0) {
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
        尚未配置 Provider。先到「AI 设置 → 模型提供商」新建一个
      </div>
    );
  }
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{allowEmpty ? "— 不指定 —" : "— 请选择 —"}</option>
      {providers.map((p) => (
        <option key={p.id} value={String(p.id)}>
          {p.name}（{p.provider} · {p.default_model}）
          {p.has_api_key ? "" : " · ⚠ 未配置 key"}
          {p.tags && p.tags.length > 0 ? ` · [${p.tags.join(",")}]` : ""}
        </option>
      ))}
    </Select>
  );
}

/**
 * 展开式 provider × model 选择器：
 *
 * 每个启用的 (provider, model) 组合 = 一个候选选项，形如
 * ``Any（OpenAI · gpt-5.5）``。选项 value 编码为 ``{provider_id}|{model}``，
 * 上层在 ``buildPayload`` 里拆开写到 ``cfg.provider_id`` + ``cfg.model``。
 *
 * 如果某 provider 还没启用任何模型，会自动展开成"用 default_model"那条
 * 选项（向后兼容老配置：以前 provider.default_model 直接作为模型）。
 *
 * value 是 ``"<pid>|<model>"`` 的形式；onChange 回传同样格式。
 */
function ProviderModelSelect({
  value,
  providers,
  loading,
  onChange,
}: {
  value: string;
  providers?: LLMProviderOut[];
  loading: boolean;
  onChange: (v: string) => void;
}) {
  if (loading) {
    return (
      <div className="flex h-10 items-center gap-2 rounded-md border px-3 text-xs text-muted-foreground">
        <Spinner className="text-primary" /> 加载中…
      </div>
    );
  }
  if (!providers || providers.length === 0) {
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
        尚未配置模型提供商。先到「AI 设置」新建一个，并在编辑里 Fetch + 启用至少一个模型
      </div>
    );
  }

  // 把每个 provider 展开成"启用的模型"列表
  // 每个 provider 都额外加一条 "用提供商默认（→ default_model）" 行；选了它后保存时
  // cfg.model 不下发，worker 调用时 build_client 会按 provider.default_model 走——
  // 这样用户改 default_model 后所有这种"默认"模板自动跟着变，不用一个个改模板
  type Row = {
    pid: number;
    providerName: string;
    providerKind: string;
    /** 空字符串 = "用提供商默认"；非空 = 具体 model id */
    modelId: string;
    /** 仅"用提供商默认"行才有值；UI 展示成 → gpt-5.5 让用户知道当前默认是啥 */
    defaultModelHint: string | null;
    custom: boolean;
    hasKey: boolean;
  };
  const rows: Row[] = [];
  for (const p of providers) {
    if (!p.has_api_key && (p.provider !== "ollama")) {
      // 没配 key 的 provider 不展开（除了 ollama 本地不需要 key）
      // 但仍想让用户看到，所以加一条 disabled 提示行——这里简单跳过
      // 没有 key 的还是要展示，让用户知道这条 provider 没法用，他能去配
    }
    // (1) 顶部一行：用提供商默认
    rows.push({
      pid: p.id,
      providerName: p.name,
      providerKind: String(p.provider),
      modelId: "",
      defaultModelHint: p.default_model || null,
      custom: false,
      hasKey: !!p.has_api_key,
    });
    // (2) 已启用的具体模型一一展开
    const enabled = (p.models || []).filter((m) => m.enabled);
    for (const m of enabled) {
      rows.push({
        pid: p.id,
        providerName: p.name,
        providerKind: String(p.provider),
        modelId: m.id,
        defaultModelHint: null,
        custom: !!m.custom,
        hasKey: !!p.has_api_key,
      });
    }
  }

  if (rows.length === 0) {
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
        所有提供商都没启用任何模型。在「AI 设置」编辑某个提供商，启用至少一条模型再来
      </div>
    );
  }

  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">— 请选择 —</option>
      {rows.map((r) => {
        // value 形如 "<pid>|<model>"；"用默认"那条 model 部分空
        const v = `${r.pid}|${r.modelId}`;
        const label =
          r.modelId === ""
            ? `${r.providerName}（${r.providerKind} · 用提供商默认${r.defaultModelHint ? ` → ${r.defaultModelHint}` : ""}）` +
              (r.hasKey ? "" : " · ⚠ 未配置 key")
            : `${r.providerName}（${r.providerKind} · ${r.modelId}）` +
              (r.hasKey ? "" : " · ⚠ 未配置 key");
        return (
          <option key={v} value={v}>
            {label}
          </option>
        );
      })}
    </Select>
  );
}

// ═══════════════════════════════════════════════════════════
// 消息格式编辑区：预设按钮 + 占位符按钮 + textarea + 格式 select
// ═══════════════════════════════════════════════════════════
function MessageFormatSection({
  outputFormat,
  onOutputFormatChange,
  template,
  onTemplateChange,
  escapeValues,
  onEscapeValuesChange,
}: {
  outputFormat: "html" | "markdown" | "plain";
  onOutputFormatChange: (v: "html" | "markdown" | "plain") => void;
  template: string;
  onTemplateChange: (v: string) => void;
  escapeValues: boolean;
  onEscapeValuesChange: (v: boolean) => void;
}) {
  const textareaRef = React.useRef<HTMLTextAreaElement | null>(null);

  // 在光标位置插入文本，光标停在插入末尾
  const insertAtCursor = (text: string) => {
    const ta = textareaRef.current;
    if (!ta) {
      onTemplateChange((template || "") + text);
      return;
    }
    const start = ta.selectionStart ?? template.length;
    const end = ta.selectionEnd ?? template.length;
    const next = template.slice(0, start) + text + template.slice(end);
    onTemplateChange(next);
    // 在 React 下次 render 后把光标停到插入末尾
    queueMicrotask(() => {
      ta.focus();
      const pos = start + text.length;
      ta.setSelectionRange(pos, pos);
    });
  };

  // "应用预设"按钮处理：直接覆盖 textarea
  const applyPreset = (tpl: string) => {
    onTemplateChange(tpl);
    queueMicrotask(() => textareaRef.current?.focus());
  };

  return (
    <div className="rounded-md border bg-muted/30 p-3 space-y-3">
      <div>
        <Label className="text-sm font-semibold">消息格式</Label>
        <p className="text-xs text-muted-foreground">
          决定 ,ai 调用后编辑回 TG 的消息长什么样。留空 = 用"简洁"预设。
          支持的占位符见下方按钮，点击直接插入光标位置。
        </p>
      </div>

      {/* 解析模式 */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label className="text-xs">解析模式（parse_mode）</Label>
          <Select
            value={outputFormat}
            onChange={(e) =>
              onOutputFormatChange(e.target.value as "html" | "markdown" | "plain")
            }
          >
            <option value="html">HTML（推荐；支持 &lt;b&gt; &lt;blockquote expandable&gt; 折叠引用）</option>
            <option value="markdown">Markdown v1（**bold** / `code` / [link](url)；不支持折叠）</option>
            <option value="plain">纯文本（不解析任何格式）</option>
          </Select>
          <p className="text-[11px] text-muted-foreground">
            注：Telethon 1.36 不识别 MarkdownV2；要折叠引用块请用 HTML 模式 +
            <code>&lt;blockquote expandable&gt;</code>
          </p>
        </div>
        <div className="flex items-center gap-2 self-end pb-2">
          <Switch
            checked={escapeValues}
            onCheckedChange={onEscapeValuesChange}
            id="escapeValues"
          />
          <Label htmlFor="escapeValues" className="cursor-pointer text-xs">
            自动转义占位符值
          </Label>
        </div>
      </div>
      {!escapeValues && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-700">
          ⚠ 关闭自动转义后，{"{answer}"} 里的 markdown 字符会被 TG 解析为格式（高级用法）；
          解析失败时本条命令会回落为纯文本展示
        </p>
      )}

      {/* 预设 */}
      <div className="space-y-1.5">
        <Label className="text-xs">快捷预设（直接覆盖下方模板）</Label>
        <div className="flex flex-wrap gap-1.5">
          {FORMAT_PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => applyPreset(p.tpl)}
              title={p.desc}
              className="rounded-full border px-2.5 py-0.5 text-xs hover:bg-muted"
            >
              {p.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => onTemplateChange("")}
            title="清空：保存后将自动用'简洁'预设"
            className="rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground hover:bg-muted"
          >
            清空（用默认）
          </button>
        </div>
      </div>

      {/* 占位符按钮 */}
      <div className="space-y-1.5">
        <Label className="text-xs">占位符（点击插入光标位置）</Label>
        <div className="flex flex-wrap gap-1">
          {PLACEHOLDER_BUTTONS.map((b) => (
            <button
              key={b.insert}
              type="button"
              onClick={() => insertAtCursor(b.insert)}
              title={b.desc}
              className="rounded border px-1.5 py-0.5 text-[11px] font-mono hover:bg-muted"
            >
              {b.label}
            </button>
          ))}
        </div>
        <Label className="text-xs">条件块（仅在条件为真时渲染括号内）</Label>
        <div className="flex flex-wrap gap-1">
          {CONDITIONAL_BUTTONS.map((b) => (
            <button
              key={b.label}
              type="button"
              onClick={() => insertAtCursor(b.snippet)}
              title={b.desc}
              className="rounded border px-1.5 py-0.5 text-[11px] font-mono hover:bg-muted"
            >
              {b.label}
            </button>
          ))}
        </div>
      </div>

      {/* 模板 textarea */}
      <div className="space-y-1.5">
        <Label className="text-xs">模板（≤ 4000 字符）</Label>
        <Textarea
          ref={textareaRef}
          value={template}
          rows={10}
          maxLength={4000}
          onChange={(e) => onTemplateChange(e.target.value)}
          placeholder={"留空 = 用'简洁'预设。\n试试上面的预设按钮先填一个再改。"}
          className="font-mono text-xs"
        />
        <p className="text-xs text-muted-foreground">
          剩余 {4000 - (template || "").length} 字符。{template.length === 0 ? "（已留空，会用默认）" : ""}
        </p>
      </div>
    </div>
  );
}

===== frontend/src/pages/Settings/DeviceProfileManager.tsx =====
// 设备伪装库管理：列表 + 新建 + 内联编辑 + 设为默认 + 删除。
// 在 Settings 页里以一个 Card 形式嵌入。
//
// 为什么需要：Telegram 会把 `device_model` / `system_version` / `app_version` 显示在设备列表里，
// 这些值通过 Telethon 的 init_connection 注册。每条 profile 可被账号引用，不引用就用 is_default。
//
// 重要：profile 的修改不会影响**已有 session**。TG 把设备名绑在 auth_key 上。
// 改了 profile 还得让账号重新登录走 wizard，TG 才会看到新值。
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Pencil, Plus, Star, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Spinner } from "@/components/ui/misc";
import {
  createDeviceProfile,
  deleteDeviceProfile,
  listDeviceProfiles,
  patchDeviceProfile,
  setDefaultDeviceProfile,
} from "@/api/device-profiles";
import type {
  DeviceProfileCreate,
  DeviceProfileOut,
} from "@/api/types";
import { getErrMsg } from "@/lib/api";
import { cn } from "@/lib/utils";

// 默认值方便快速创建：直接照抄 macOS Telegram
const DEFAULT_FORM: DeviceProfileCreate = {
  name: "",
  device_model: "MacBook Pro",
  system_version: "macOS 14.5",
  app_version: "Telegram macOS 11.5",
  lang_code: "zh",
  system_lang_code: "zh-Hans",
  is_default: false,
};

export function DeviceProfileManager() {
  const qc = useQueryClient();
  const profilesQ = useQuery({
    queryKey: ["device-profiles"],
    queryFn: listDeviceProfiles,
  });

  // 新建表单状态
  const [form, setForm] = useState<DeviceProfileCreate>(DEFAULT_FORM);
  const [showCreate, setShowCreate] = useState(false);

  const createMut = useMutation({
    mutationFn: () => createDeviceProfile(form),
    onSuccess: () => {
      toast.success("已创建");
      setForm(DEFAULT_FORM);
      setShowCreate(false);
      qc.invalidateQueries({ queryKey: ["device-profiles"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const setDefaultMut = useMutation({
    mutationFn: setDefaultDeviceProfile,
    onSuccess: () => {
      toast.success("已设为默认");
      qc.invalidateQueries({ queryKey: ["device-profiles"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteMut = useMutation({
    mutationFn: deleteDeviceProfile,
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["device-profiles"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="text-base">设备标识模板</CardTitle>
            <CardDescription>
              控制 TG 设备列表里看到的设备名、系统、客户端版本。修改只对**新登录**的
              session 生效，改了已有账号要重登才会显示新值。
            </CardDescription>
          </div>
          {!showCreate ? (
            <Button size="sm" onClick={() => setShowCreate(true)}>
              <Plus className="mr-1 h-4 w-4" /> 新增
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setShowCreate(false)}>
              <X className="mr-1 h-4 w-4" /> 取消
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 新建表单 */}
        {showCreate ? (
          <div className="rounded-lg border bg-muted/30 p-4">
            <ProfileForm
              value={form}
              onChange={setForm}
              showName
              showIsDefault
            />
            <div className="mt-3 flex justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setForm(DEFAULT_FORM);
                  setShowCreate(false);
                }}
              >
                取消
              </Button>
              <Button
                size="sm"
                onClick={() => createMut.mutate()}
                disabled={!form.name.trim() || createMut.isPending}
              >
                创建
              </Button>
            </div>
          </div>
        ) : null}

        {/* 列表 */}
        {profilesQ.isLoading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : profilesQ.data && profilesQ.data.length > 0 ? (
          <ul className="space-y-2">
            {profilesQ.data.map((p) => (
              <ProfileRow
                key={p.id}
                profile={p}
                onSetDefault={() => setDefaultMut.mutate(p.id)}
                onDelete={() => {
                  if (confirm(`确认删除「${p.name}」？引用该 profile 的账号会回落到默认。`))
                    deleteMut.mutate(p.id);
                }}
              />
            ))}
          </ul>
        ) : (
          <p className="rounded-md border border-dashed py-8 text-center text-sm text-muted-foreground">
            尚无 profile（迁移会预置 3 条 macOS / iPhone / Windows）
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ── 单行 ──────────────────────────────────────────────────────────

function ProfileRow({
  profile,
  onSetDefault,
  onDelete,
}: {
  profile: DeviceProfileOut;
  onSetDefault: () => void;
  onDelete: () => void;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<DeviceProfileCreate>({
    name: profile.name,
    device_model: profile.device_model,
    system_version: profile.system_version,
    app_version: profile.app_version,
    lang_code: profile.lang_code,
    system_lang_code: profile.system_lang_code,
  });

  const patchMut = useMutation({
    mutationFn: () =>
      patchDeviceProfile(profile.id, {
        name: draft.name !== profile.name ? draft.name : undefined,
        device_model:
          draft.device_model !== profile.device_model ? draft.device_model : undefined,
        system_version:
          draft.system_version !== profile.system_version
            ? draft.system_version
            : undefined,
        app_version:
          draft.app_version !== profile.app_version ? draft.app_version : undefined,
        lang_code:
          draft.lang_code !== profile.lang_code ? draft.lang_code : undefined,
        system_lang_code:
          draft.system_lang_code !== profile.system_lang_code
            ? draft.system_lang_code
            : undefined,
      }),
    onSuccess: () => {
      toast.success("已保存");
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["device-profiles"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <li
      className={cn(
        "rounded-lg border p-3",
        profile.is_default && "border-primary/40 bg-primary/5",
      )}
    >
      {/* 头部：名称 + 默认徽章 + 操作 */}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{profile.name}</span>
            {profile.is_default ? (
              <span className="inline-flex items-center gap-0.5 rounded-sm bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                <Star className="h-2.5 w-2.5" /> 默认
              </span>
            ) : null}
          </div>
          {/* 摘要（非编辑态） */}
          {!editing ? (
            <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
              <div className="font-mono">
                {profile.device_model} · {profile.system_version} · {profile.app_version}
              </div>
              <div className="text-[11px]">
                lang: {profile.lang_code} / {profile.system_lang_code}
              </div>
            </div>
          ) : null}
        </div>

        {/* 操作 */}
        <div className="flex shrink-0 items-center gap-1">
          {!editing ? (
            <>
              {!profile.is_default ? (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-8 px-2"
                  onClick={onSetDefault}
                  title="设为默认"
                >
                  <Star className="h-3.5 w-3.5" />
                </Button>
              ) : null}
              <Button
                size="sm"
                variant="ghost"
                className="h-8 px-2"
                onClick={() => setEditing(true)}
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 px-2 text-destructive hover:text-destructive"
                onClick={onDelete}
                disabled={profile.is_default}
                title={profile.is_default ? "默认 profile 不可删除" : "删除"}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </>
          ) : (
            <>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 px-2"
                onClick={() => {
                  setDraft({
                    name: profile.name,
                    device_model: profile.device_model,
                    system_version: profile.system_version,
                    app_version: profile.app_version,
                    lang_code: profile.lang_code,
                    system_lang_code: profile.system_lang_code,
                  });
                  setEditing(false);
                }}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="sm"
                className="h-8 px-2"
                onClick={() => patchMut.mutate()}
                disabled={patchMut.isPending}
              >
                <Check className="h-3.5 w-3.5" />
              </Button>
            </>
          )}
        </div>
      </div>

      {/* 编辑态字段 */}
      {editing ? (
        <div className="mt-3 border-t pt-3">
          <ProfileForm value={draft} onChange={setDraft} showName />
        </div>
      ) : null}
    </li>
  );
}

// ── 表单（创建 / 编辑共用） ─────────────────────────────────────────

function ProfileForm({
  value,
  onChange,
  showName,
  showIsDefault,
}: {
  value: DeviceProfileCreate;
  onChange: (v: DeviceProfileCreate) => void;
  showName?: boolean;
  showIsDefault?: boolean;
}) {
  const set = <K extends keyof DeviceProfileCreate>(
    k: K,
    v: DeviceProfileCreate[K],
  ) => onChange({ ...value, [k]: v });

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {showName ? (
        <Field label="名称" hint="例如：「我的 Mac」「老张的 iPhone」">
          <Input
            value={value.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="profile 名称"
          />
        </Field>
      ) : null}
      <Field label="设备型号 (device_model)" hint="TG 设备列表里的主标题">
        <Input
          value={value.device_model}
          onChange={(e) => set("device_model", e.target.value)}
          placeholder="MacBook Pro"
        />
      </Field>
      <Field label="系统版本 (system_version)" hint="副标题前半段">
        <Input
          value={value.system_version}
          onChange={(e) => set("system_version", e.target.value)}
          placeholder="macOS 14.5"
        />
      </Field>
      <Field label="客户端版本 (app_version)" hint="副标题后半段">
        <Input
          value={value.app_version}
          onChange={(e) => set("app_version", e.target.value)}
          placeholder="Telegram macOS 11.5"
        />
      </Field>
      <Field label="lang_code" hint="客户端 UI 语言（BCP-47 简写）">
        <Input
          value={value.lang_code ?? "zh"}
          onChange={(e) => set("lang_code", e.target.value)}
          placeholder="zh"
        />
      </Field>
      <Field label="system_lang_code" hint="系统语言">
        <Input
          value={value.system_lang_code ?? "zh-Hans"}
          onChange={(e) => set("system_lang_code", e.target.value)}
          placeholder="zh-Hans"
        />
      </Field>
      {showIsDefault ? (
        <Field label="设为默认" hint="勾上后其它 profile 自动取消默认">
          <div className="flex h-10 items-center">
            <Switch
              checked={value.is_default ?? false}
              onCheckedChange={(v) => set("is_default", v)}
            />
          </div>
        </Field>
      ) : null}
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {children}
      {hint ? (
        <p className="text-[11px] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

===== frontend/src/pages/Settings/Index.tsx =====
// 系统设置：仅留全局参数（命令前缀 / kill switch / 全局 QPS）+ 当前用户账号管理。
// 各类「模板」已迁到 /templates；插件已迁到 /plugins；LLM Provider 已迁到 /ai。
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import {
  getGlobalLimits,
  getSystemSettings,
  patchSystemSettings,
  putGlobalLimits,
} from "@/api/system";
import { getErrMsg, api } from "@/lib/api";
import { UserAccount } from "./UserAccount";

interface KillSwitchState {
  enabled: boolean;
}

export function SettingsIndex() {
  const qc = useQueryClient();

  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const limitsQ = useQuery({
    queryKey: ["system", "global-limits"],
    queryFn: getGlobalLimits,
  });
  const killQ = useQuery<KillSwitchState>({
    queryKey: ["system", "kill-switch"],
    queryFn: async () => (await api.get("/api/system/kill-switch")).data,
  });

  // 命令前缀本地编辑态
  const [prefix, setPrefix] = useState("");
  useEffect(() => {
    if (settingsQ.data) setPrefix(settingsQ.data.command_prefix ?? ",");
  }, [settingsQ.data]);

  // 每秒 API 上限本地编辑态
  const [qps, setQps] = useState("0");
  useEffect(() => {
    if (limitsQ.data) setQps(String(limitsQ.data.api_qps_total ?? 0));
  }, [limitsQ.data]);

  const savePrefix = useMutation({
    mutationFn: () => patchSystemSettings({ command_prefix: prefix }),
    onSuccess: () => {
      toast.success("命令前缀已保存（worker 将热加载）");
      qc.invalidateQueries({ queryKey: ["system", "settings"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const saveQps = useMutation({
    mutationFn: () => putGlobalLimits(Number(qps) || 0),
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["system", "global-limits"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const killMut = useMutation({
    mutationFn: async (next: boolean) => {
      await api.post("/api/system/kill-switch", { enabled: next });
    },
    onSuccess: () => {
      toast.success("已下发");
      qc.invalidateQueries({ queryKey: ["system", "kill-switch"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const loading = settingsQ.isLoading || limitsQ.isLoading || killQ.isLoading;

  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">系统设置</h1>
        <p className="text-sm text-muted-foreground">
          全局参数：命令前缀、kill switch、API 总上限。模板类配置请到「通用模板」页。
        </p>
      </div>

      {/* 命令前缀 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">命令前缀</CardTitle>
          <CardDescription>
            TG 内命令开头字符（默认 <code>,</code>）。修改后 worker 自动热加载
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex max-w-xs items-end gap-2">
            <div className="flex-1 space-y-1.5">
              <Label>前缀</Label>
              <Input
                value={prefix}
                maxLength={3}
                onChange={(e) => setPrefix(e.target.value)}
              />
            </div>
            <Button
              onClick={() => prefix && savePrefix.mutate()}
              disabled={savePrefix.isPending}
            >
              保存
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 全局 kill switch */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">全局总闸（Kill Switch）</CardTitle>
          <CardDescription>
            开启后所有账号 worker 立即暂停，仅保留接收
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center gap-4">
          <Switch
            checked={!!killQ.data?.enabled}
            onCheckedChange={(v) => {
              if (v && !confirm("确认开启总闸？所有账号立即暂停！")) return;
              killMut.mutate(v);
            }}
          />
          <span className="text-sm text-muted-foreground">
            当前：{killQ.data?.enabled ? "已暂停" : "正常运行"}
          </span>
        </CardContent>
      </Card>

      {/* 全局 QPS */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">全局每秒 API 上限</CardTitle>
          <CardDescription>0 = 不限制</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex max-w-xs items-end gap-2">
            <div className="flex-1 space-y-1.5">
              <Label>api_qps_total</Label>
              <Input
                inputMode="numeric"
                value={qps}
                onChange={(e) => setQps(e.target.value.replace(/[^0-9]/g, ""))}
              />
            </div>
            <Button onClick={() => saveQps.mutate()} disabled={saveQps.isPending}>
              保存
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 当前用户账号管理：修改密码 + 禁用 TOTP */}
      <UserAccount />
    </div>
  );
}

===== frontend/src/pages/Settings/LLMProviders.tsx =====
// 系统设置 → LLM Provider 管理
// 用于"AI 类自定义命令"的大模型供应商凭据配置；api_key 在后端 Fernet 加密落库
// 列表里只显示 has_api_key:✓/✗，永远不会回显明文 key（与后端约定）
//
// 路由元数据（modality / tags / cost_tier / notes）：决定"自动路由"模式下
// 一条 ,ai 命令该把请求送给哪个 provider；详见 backend/services/llm_router.py
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Trash2, KeyRound, Edit3, Download, Loader2, CheckCircle2, XCircle, Star } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/misc";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import {
  createLLMProvider,
  deleteLLMProvider,
  fetchProviderModels,
  listLLMProviders,
  patchLLMProvider,
  testProviderModel,
} from "@/api/commands";
import { listProxies } from "@/api/proxies";
import type { LLMApiFormat, LLMModality, LLMProviderKind, LLMProviderOut, LLMTag, ProviderModel, ProxyOut } from "@/api/types";
import { getErrMsg } from "@/lib/api";

// 各 provider 的默认 base_url 提示，仅作 placeholder
const DEFAULT_BASE_URLS: Record<LLMProviderKind, string> = {
  openai: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com/v1",
  ollama: "http://localhost:11434/v1",
};

// 各 provider 常见模型示例（首次新建友好填充）
const SUGGESTED_MODELS: Record<LLMProviderKind, string> = {
  openai: "gpt-4o-mini",
  anthropic: "claude-haiku-4-5",
  ollama: "llama3:8b",
};

// API Format 选项（与后端 ALL_LLM_API_FORMATS 对齐）
const API_FORMAT_OPTIONS: { value: LLMApiFormat; label: string; hint: string }[] = [
  {
    value: "chat_completions",
    label: "Chat Completions ( /chat/completions )",
    hint: "OpenAI 经典协议；最广为兼容；OpenAI 官方 / 大多数反代默认接这个",
  },
  {
    value: "responses",
    label: "Responses ( /responses )",
    hint: "OpenAI 2024 出的新协议；anyrouter 等部分反代只接这个；默认应该选这个解决 chat/completions 不通的问题",
  },
  {
    value: "anthropic_messages",
    label: "Anthropic Messages ( /v1/messages )",
    hint: "Anthropic 协议；走官方 https://api.anthropic.com 或兼容反代时选",
  },
];

// 模态选项 + 中文解释（与后端 ALL_LLM_MODALITIES 对齐）
const MODALITY_OPTIONS: { value: LLMModality; label: string; hint: string }[] = [
  { value: "text", label: "纯文本（text）", hint: "只支持文本输入输出（绝大多数 LLM）" },
  {
    value: "vision",
    label: "视觉多模态（vision）",
    hint: "支持图文输入 → 文本输出（如 GPT-4V、Claude Vision）",
  },
  {
    value: "audio",
    label: "音频多模态（audio）",
    hint: "支持语音转写 / TTS（如 Whisper、GPT-4o realtime）",
  },
  {
    value: "multimodal",
    label: "全模态（multimodal）",
    hint: "图、音、视频同时输入（如 GPT-4o、Gemini-Pro）",
  },
];

// 路由标签字典 + 解释（与后端 ALL_LLM_TAGS 对齐）
const TAG_OPTIONS: { value: LLMTag; label: string; hint: string }[] = [
  { value: "chat", label: "chat", hint: "通用闲聊 / 短问短答" },
  { value: "code", label: "code", hint: "代码生成 / 解释 / 调试" },
  { value: "math", label: "math", hint: "数学推导 / 计算" },
  { value: "translate", label: "translate", hint: "多语种翻译" },
  { value: "vision", label: "vision", hint: "看图说话 / 图像理解（需配合 modality=vision）" },
  { value: "long_context", label: "long_context", hint: "大上下文（≥ 64K token）" },
  { value: "reason", label: "reason", hint: "复杂推理 / 多步分析（旗舰）" },
  { value: "smart", label: "smart", hint: "答主力（同 reason，强调质量）" },
  { value: "cheap", label: "cheap", hint: "量大优先（成本档 1）" },
  { value: "fast", label: "fast", hint: "低延迟优先" },
  { value: "classify", label: "classify", hint: "适合做路由分类器的轻量小模型" },
];

const COST_TIER_OPTIONS = [
  { value: 1, label: "1 · 便宜（量大走它）" },
  { value: 2, label: "2 · 中（默认）" },
  { value: 3, label: "3 · 旗舰（贵但答主力）" },
];

interface FormState {
  id?: number; // 编辑模式时存在
  name: string;
  provider: LLMProviderKind;
  api_key: string; // 编辑时初始为空 = 不动；填非空 = 替换
  base_url: string;
  default_model: string;
  // API Format（chat_completions / responses / anthropic_messages）
  api_format: LLMApiFormat;
  // 编辑模式下，是否要"清空已有 key"（按钮触发）
  clearKey: boolean;
  // ── 路由元数据 ──
  modality: LLMModality;
  tags: LLMTag[];
  cost_tier: number;
  notes: string;
  // ── 出口代理 ──
  // "" 表示 DIRECT（不走代理）；其它是 proxy.id 字符串
  proxy_id: string;
  // ── 候选模型清单 ──
  // toggle / 自定义添加 / fetch 都改这个；保存时整体 PATCH 给后端
  models: ProviderModel[];
}

const EMPTY_FORM: FormState = {
  name: "",
  provider: "openai",
  api_key: "",
  base_url: "",
  default_model: SUGGESTED_MODELS.openai,
  api_format: "chat_completions",
  clearKey: false,
  modality: "text",
  tags: ["chat"],
  cost_tier: 2,
  notes: "",
  proxy_id: "",
  models: [],
};

export function LLMProviders() {
  const qc = useQueryClient();

  const listQ = useQuery({
    queryKey: ["llm-providers"],
    queryFn: listLLMProviders,
  });

  // 顶层也拉一次代理表，用于列表里把 proxy_id 翻译成 "host:port" 显示
  const proxiesListQ = useQuery({
    queryKey: ["proxies-for-llm"],
    queryFn: listProxies,
  });
  const proxyById: Map<number, ProxyOut> = new Map(
    (proxiesListQ.data || []).map((p) => [p.id, p]),
  );

  const [editing, setEditing] = useState<FormState | null>(null);

  const createMut = useMutation({
    mutationFn: (form: FormState) =>
      createLLMProvider({
        name: form.name.trim(),
        provider: form.provider,
        api_key: form.api_key || null,
        base_url: form.base_url || null,
        default_model: form.default_model.trim(),
        api_format: form.api_format,
        modality: form.modality,
        tags: form.tags,
        cost_tier: form.cost_tier,
        notes: form.notes || null,
        proxy_id: form.proxy_id ? Number(form.proxy_id) : null,
        models: form.models,
      }),
    onSuccess: () => {
      toast.success("已新建模型提供商");
      qc.invalidateQueries({ queryKey: ["llm-providers"] });
      setEditing(null);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const updateMut = useMutation({
    mutationFn: (form: FormState) => {
      if (!form.id) throw new Error("缺少 id");
      const apiKey = form.clearKey ? "" : form.api_key ? form.api_key : undefined;
      const proxyPatch =
        form.proxy_id === ""
          ? { clear_proxy: true, proxy_id: null }
          : { proxy_id: Number(form.proxy_id) };
      return patchLLMProvider(form.id, {
        name: form.name.trim(),
        provider: form.provider,
        api_key: apiKey,
        base_url: form.base_url || null,
        default_model: form.default_model.trim(),
        api_format: form.api_format,
        modality: form.modality,
        tags: form.tags,
        cost_tier: form.cost_tier,
        notes: form.notes || null,
        ...proxyPatch,
        models: form.models,
      });
    },
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["llm-providers"] });
      setEditing(null);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteLLMProvider(id),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["llm-providers"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const onEdit = (p: LLMProviderOut) => {
    setEditing({
      id: p.id,
      name: p.name,
      provider: (p.provider as LLMProviderKind) || "openai",
      // 编辑模式下永远不预填明文 key
      api_key: "",
      base_url: p.base_url || "",
      default_model: p.default_model,
      api_format: ((p.api_format as LLMApiFormat) || "chat_completions"),
      clearKey: false,
      modality: ((p.modality as LLMModality) || "text"),
      tags: ((p.tags as LLMTag[]) || []).filter((t) =>
        TAG_OPTIONS.some((opt) => opt.value === t),
      ),
      cost_tier: typeof p.cost_tier === "number" ? p.cost_tier : 2,
      notes: p.notes || "",
      proxy_id: p.proxy_id != null ? String(p.proxy_id) : "",
      models: (p.models || []).map((m) => ({
        id: m.id,
        enabled: !!m.enabled,
        custom: !!m.custom,
        label: m.label ?? null,
      })),
    });
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base">模型提供商</CardTitle>
              <CardDescription>
                每条 = 一个模型供应商凭据。配完 api_key + base_url 后，在编辑里点
                <strong>「Fetch 模型列表」</strong>就能自动拉取并 toggle 启用要用的模型。<br />
                <span className="text-muted-foreground/80">
                  modality（模态）+ tags（标签）+ cost_tier（成本档）三项决定「自动路由」模式
                  下该 provider 是否被选中——详见 AI 设置页顶部的推荐配置。
                </span>
              </CardDescription>
            </div>
            <Button size="sm" onClick={() => setEditing({ ...EMPTY_FORM })}>
              <Plus className="mr-1 h-4 w-4" /> 新建
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {listQ.isLoading ? (
            <div className="flex h-20 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : listQ.data && listQ.data.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>提供商</TableHead>
                  <TableHead>API 协议</TableHead>
                  <TableHead>默认模型 ID</TableHead>
                  <TableHead>已启用模型</TableHead>
                  <TableHead>模态 / 成本</TableHead>
                  <TableHead>标签</TableHead>
                  <TableHead>代理</TableHead>
                  <TableHead>api_key</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {listQ.data.map((p) => {
                  const enabledModels = (p.models || []).filter((m) => m.enabled);
                  return (
                  <TableRow key={p.id}>
                    <TableCell className="font-medium">{p.name}</TableCell>
                    <TableCell className="font-mono text-xs">{p.provider}</TableCell>
                    <TableCell className="text-xs">
                      <Badge variant="outline" className="font-mono">
                        {p.api_format || "chat_completions"}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{p.default_model}</TableCell>
                    <TableCell>
                      <Badge variant={enabledModels.length > 0 ? "secondary" : "warn"}>
                        {enabledModels.length} / {(p.models || []).length}
                      </Badge>
                    </TableCell>
                    <TableCell className="space-x-1 text-xs">
                      <Badge variant="outline">{p.modality || "text"}</Badge>
                      <Badge variant="secondary">tier {p.cost_tier ?? 2}</Badge>
                    </TableCell>
                    <TableCell className="space-x-1">
                      {(p.tags || []).length > 0 ? (
                        (p.tags || []).slice(0, 4).map((t) => (
                          <Badge key={t} variant="outline" className="text-xs">
                            {t}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                      {(p.tags || []).length > 4 ? (
                        <span className="text-xs text-muted-foreground">
                          +{(p.tags || []).length - 4}
                        </span>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      {p.proxy_id != null ? (
                        proxyById.has(p.proxy_id) ? (
                          <Badge variant="outline" className="font-mono text-xs">
                            {proxyById.get(p.proxy_id)!.type}://
                            {proxyById.get(p.proxy_id)!.host}:
                            {proxyById.get(p.proxy_id)!.port}
                          </Badge>
                        ) : (
                          <Badge variant="warn" className="text-xs">
                            #{p.proxy_id} 已删除
                          </Badge>
                        )
                      ) : (
                        <Badge variant="secondary" className="text-xs">
                          DIRECT
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {p.has_api_key ? (
                        <Badge variant="success" className="gap-1">
                          <KeyRound className="h-3 w-3" /> 已配置
                        </Badge>
                      ) : (
                        <Badge variant="secondary">未配置</Badge>
                      )}
                    </TableCell>
                    <TableCell className="space-x-2 text-right">
                      <Button variant="ghost" size="sm" onClick={() => onEdit(p)}>
                        <Edit3 className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={deleteMut.isPending}
                        onClick={() => {
                          if (confirm(`确认删除 provider「${p.name}」？引用此 provider 的 AI 命令将失败`)) {
                            deleteMut.mutate(p.id);
                          }
                        }}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </TableCell>
                  </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <p className="rounded-md border border-dashed py-8 text-center text-xs text-muted-foreground">
              尚未配置任何模型提供商。新建一个后，就能在「自定义命令」里创建 AI 类型命令
            </p>
          )}
        </CardContent>
      </Card>

      {editing && (
        <ProviderEditDialog
          form={editing}
          onChange={setEditing}
          onCancel={() => setEditing(null)}
          onSave={() => {
            if (!editing.name.trim()) {
              toast.error("名称必填");
              return;
            }
            if (!editing.default_model.trim()) {
              toast.error("默认模型必填");
              return;
            }
            if (editing.id) {
              updateMut.mutate(editing);
            } else {
              createMut.mutate(editing);
            }
          }}
          saving={createMut.isPending || updateMut.isPending}
        />
      )}
    </div>
  );
}

function ProviderEditDialog({
  form,
  onChange,
  onCancel,
  onSave,
  saving,
}: {
  form: FormState;
  onChange: (s: FormState) => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
}) {
  const isEdit = !!form.id;
  const setField = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    onChange({ ...form, [k]: v });

  // 列出所有代理；mtproxy 不能给 LLM 用，前端做硬过滤；
  // 后端 service 层有同样的拒绝逻辑兜底
  const proxiesQ = useQuery({
    queryKey: ["proxies-for-llm"],
    queryFn: listProxies,
  });
  const llmUsableProxies: ProxyOut[] = (proxiesQ.data || []).filter(
    (p) => (p.type || "").toLowerCase() !== "mtproxy",
  );

  const toggleTag = (tag: LLMTag) => {
    const has = form.tags.includes(tag);
    setField("tags", has ? form.tags.filter((t) => t !== tag) : [...form.tags, tag]);
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑" : "新建"}模型提供商</DialogTitle>
          <DialogDescription>
            api_key 加密落库；列表中只显示是否已配置，永远不回显明文。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>名称 *</Label>
            <Input
              value={form.name}
              maxLength={64}
              onChange={(e) => setField("name", e.target.value)}
              placeholder="例如：openai-main"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>提供商 *</Label>
              <Select
                value={form.provider}
                onChange={(e) => {
                  const p = e.target.value as LLMProviderKind;
                  setField("provider", p);
                  // 切提供商时给出建议默认模型 ID（若用户没改过）
                  if (
                    !form.default_model ||
                    Object.values(SUGGESTED_MODELS).includes(form.default_model)
                  ) {
                    onChange({
                      ...form,
                      provider: p,
                      default_model: SUGGESTED_MODELS[p],
                    });
                  }
                }}
              >
                <option value="openai">OpenAI（兼容协议）</option>
                <option value="anthropic">Anthropic</option>
                <option value="ollama">Ollama（本地）</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>默认模型 ID *</Label>
              <Input
                value={form.default_model}
                maxLength={64}
                onChange={(e) => setField("default_model", e.target.value)}
                placeholder={SUGGESTED_MODELS[form.provider]}
              />
              <p className="text-xs text-muted-foreground">
                自动路由 fallback 时用；可在下方"模型管理"区点 ✓ 直接设为此值
              </p>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>Base URL</Label>
            <Input
              value={form.base_url}
              maxLength={255}
              onChange={(e) => setField("base_url", e.target.value)}
              placeholder={DEFAULT_BASE_URLS[form.provider]}
            />
            <p className="text-xs text-muted-foreground">
              留空使用默认地址。OpenAI 兼容代理 / 自托管 Ollama 都填这里。
            </p>
          </div>

          <div className="space-y-1.5">
            <Label>API Format（API 协议）*</Label>
            <Select
              value={form.api_format}
              onChange={(e) => setField("api_format", e.target.value as LLMApiFormat)}
            >
              {API_FORMAT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
            <p className="text-xs text-muted-foreground">
              {API_FORMAT_OPTIONS.find((o) => o.value === form.api_format)?.hint}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label>API Key {isEdit ? "" : "*（建议）"}</Label>
            <Input
              type="password"
              value={form.api_key}
              maxLength={512}
              autoComplete="off"
              onChange={(e) => setField("api_key", e.target.value)}
              placeholder={isEdit ? "留空 = 保持原 key 不变" : "sk-..."}
              disabled={form.clearKey}
            />
            {isEdit && (
              <div className="flex items-center gap-2 pt-1 text-xs">
                <input
                  id="clearKey"
                  type="checkbox"
                  checked={form.clearKey}
                  onChange={(e) =>
                    onChange({
                      ...form,
                      clearKey: e.target.checked,
                      api_key: e.target.checked ? "" : form.api_key,
                    })
                  }
                />
                <label htmlFor="clearKey" className="cursor-pointer text-muted-foreground">
                  勾选 = 清空已存的 api_key（提交后该 provider 标记为未配置）
                </label>
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              Ollama 本地部署可不填。其它厂商请到对应控制台获取。
            </p>
          </div>

          {/* ── 路由元数据区 ─────────────────────────── */}
          <div className="rounded-md border bg-muted/30 p-3 space-y-3">
            <div>
              <Label className="text-sm font-semibold">路由元数据</Label>
              <p className="text-xs text-muted-foreground">
                这些字段决定「自动路由」模式下，一条 ,ai 命令的请求是否会被分配给本 provider。
                只用 fixed 模式可以全留默认。
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>模态（modality）</Label>
                <Select
                  value={form.modality}
                  onChange={(e) => setField("modality", e.target.value as LLMModality)}
                >
                  {MODALITY_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </Select>
                <p className="text-xs text-muted-foreground">
                  {MODALITY_OPTIONS.find((o) => o.value === form.modality)?.hint}
                </p>
              </div>
              <div className="space-y-1.5">
                <Label>成本档（cost_tier）</Label>
                <Select
                  value={String(form.cost_tier)}
                  onChange={(e) => setField("cost_tier", Number(e.target.value))}
                >
                  {COST_TIER_OPTIONS.map((opt) => (
                    <option key={opt.value} value={String(opt.value)}>
                      {opt.label}
                    </option>
                  ))}
                </Select>
                <p className="text-xs text-muted-foreground">
                  同 tag 内有多个 provider 时，路由器据此挑（cheap=1 优先做闲聊，premium=3 优先做推理）。
                </p>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>路由标签（tags）</Label>
              <div className="flex flex-wrap gap-1.5">
                {TAG_OPTIONS.map((opt) => {
                  const active = form.tags.includes(opt.value);
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => toggleTag(opt.value)}
                      title={opt.hint}
                      className={
                        "rounded-full border px-2.5 py-0.5 text-xs transition-colors " +
                        (active
                          ? "bg-primary text-primary-foreground border-transparent"
                          : "bg-background hover:bg-muted")
                      }
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
              <p className="text-xs text-muted-foreground">
                点击切换。常用搭配：闲聊模型 = ['chat','cheap'] · 旗舰答主力 = ['smart','reason','code','long_context'] · 视觉模型 = ['vision'] +
                modality=vision · 路由分类器 = ['classify','cheap']
              </p>
            </div>

            <div className="space-y-1.5">
              <Label>备注（notes，可选）</Label>
              <Textarea
                value={form.notes}
                rows={2}
                maxLength={500}
                onChange={(e) => setField("notes", e.target.value)}
                placeholder="例如：GLM 4.7，做路由分类器+中文短问；速率好但长文偶尔翻车"
              />
              <p className="text-xs text-muted-foreground">
                仅给自己看；路由器不读这个字段。
              </p>
            </div>
          </div>

          {/* ── 模型管理（Fetch + Toggle + 自定义 + 测试）──────── */}
          <ProviderModelsSection
            providerId={form.id ?? null}
            models={form.models}
            defaultModel={form.default_model}
            onModelsChange={(next) => setField("models", next)}
            onSetDefault={(id) => setField("default_model", id)}
            providerKind={form.provider}
          />

          {/* ── 出口代理 ───────────────────────────── */}
          <div className="rounded-md border bg-muted/30 p-3 space-y-2">
            <div>
              <Label className="text-sm font-semibold">出口代理</Label>
              <p className="text-xs text-muted-foreground">
                调 LLM API 的 HTTP 流量走哪个代理。各 provider 可独立选；
                <code>DIRECT</code> = 直连不走代理。 <span className="text-muted-foreground/80">
                  代理库在「系统设置 → 代理」管理；mtproxy 不支持，已自动过滤。
                </span>
              </p>
            </div>
            {proxiesQ.isLoading ? (
              <div className="flex h-10 items-center gap-2 rounded-md border px-3 text-xs text-muted-foreground">
                <Spinner className="text-primary" /> 加载代理列表…
              </div>
            ) : (
              <Select
                value={form.proxy_id}
                onChange={(e) => setField("proxy_id", e.target.value)}
              >
                <option value="">DIRECT — 不走代理（直连）</option>
                {llmUsableProxies.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    #{p.id} · {p.type} · {p.host}:{p.port}
                    {p.username ? ` (${p.username})` : ""}
                  </option>
                ))}
              </Select>
            )}
            {!proxiesQ.isLoading &&
              llmUsableProxies.length === 0 &&
              form.proxy_id === "" && (
                <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                  代理库为空。如果你在中国大陆访问 OpenAI / Anthropic，记得先到
                  「系统设置 → 代理」添加一条 socks5 / http 代理，再回来选上。
                </p>
              )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onCancel} disabled={saving}>
            取消
          </Button>
          <Button onClick={onSave} disabled={saving}>
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ═══════════════════════════════════════════════════════════
// ProviderModelsSection：候选模型清单 + Fetch + 自定义添加 + 测试
// ═══════════════════════════════════════════════════════════
//
// 设计：
// - models 是 form 的本地状态；toggle / 删除 / 自定义添加都改本地，最终随"保存"PATCH 落库
// - "Fetch 模型列表"和"测试连通性"则需要 provider 已落库（要解密 api_key，仅后端能做）
//   所以未保存的 provider（form.id 为空）按钮置灰 + 提示"先保存"
// - Fetch 调 backend → 后端拉 /v1/models → 合并保存到 DB → 返新 provider out → 我们用
//   它替换 form.models（保留前端这次会话里加的 custom 条目）
function ProviderModelsSection({
  providerId,
  models,
  defaultModel,
  onModelsChange,
  onSetDefault,
  providerKind,
}: {
  providerId: number | null;
  models: ProviderModel[];
  defaultModel: string;
  onModelsChange: (next: ProviderModel[]) => void;
  onSetDefault: (id: string) => void;
  providerKind: LLMProviderKind;
}) {
  const [customId, setCustomId] = useState("");
  // 测试某条模型时，记当前正在测的 id（用来禁用按钮 + 显示 spinner）
  const [testingId, setTestingId] = useState<string | null>(null);
  // 测试结果按 id 缓存：{[id]: {ok, latency_ms, error?}}
  const [testResults, setTestResults] = useState<
    Record<string, { ok: boolean; latency_ms: number; error?: string | null; preview?: string | null; model?: string | null }>
  >({});

  const persisted = providerId !== null;

  const fetchMut = useMutation({
    mutationFn: () => fetchProviderModels(providerId!),
    onSuccess: (resp) => {
      // 后端返了合并后的 models；用它覆盖前端 form.models
      const merged = resp.provider.models || [];
      onModelsChange(
        merged.map((m) => ({
          id: m.id,
          enabled: !!m.enabled,
          custom: !!m.custom,
          label: m.label ?? null,
        })),
      );
      toast.success(`已拉取 ${resp.fetched} 个模型；本地共 ${merged.length} 条`);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const testMut = useMutation({
    mutationFn: (modelId: string) => testProviderModel(providerId!, { model: modelId }),
  });

  const onTest = async (modelId: string) => {
    setTestingId(modelId);
    try {
      const r = await testMut.mutateAsync(modelId);
      setTestResults((prev) => ({
        ...prev,
        [modelId]: {
          ok: r.ok,
          latency_ms: r.latency_ms,
          error: r.error,
          preview: r.preview,
          model: r.model,
        },
      }));
      if (r.ok) {
        toast.success(`${modelId} 通：${r.latency_ms} ms`);
      } else {
        toast.error(`${modelId} 失败（${r.latency_ms} ms）：${r.error || "未知"}`);
      }
    } catch (e) {
      toast.error(getErrMsg(e));
    } finally {
      setTestingId(null);
    }
  };

  const toggle = (idx: number) => {
    const next = models.slice();
    next[idx] = { ...next[idx], enabled: !next[idx].enabled };
    onModelsChange(next);
  };

  const remove = (idx: number) => {
    const next = models.slice();
    next.splice(idx, 1);
    onModelsChange(next);
  };

  const addCustom = () => {
    const id = customId.trim();
    if (!id) return;
    if (models.some((m) => m.id === id)) {
      toast.error(`模型 ${id} 已存在`);
      return;
    }
    onModelsChange([...models, { id, enabled: true, custom: true, label: null }]);
    setCustomId("");
  };

  const fetchDisabledHint =
    providerKind === "anthropic"
      ? "Anthropic 不支持列出模型接口，请手动添加"
      : !persisted
      ? "先保存 provider 才能拉模型（需要 api_key）"
      : null;

  return (
    <div className="rounded-md border bg-muted/30 p-3 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <Label className="text-sm font-semibold">模型管理</Label>
          <p className="text-xs text-muted-foreground">
            点 <code>Fetch</code> 自动从 base_url 拉模型列表，toggle 启用要用的几个；也能手动添加。
            启用的模型会在「自定义命令 → AI 子表单」的下拉里展开成
            <code> 名称（提供商 · 模型ID）</code>
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!persisted || providerKind === "anthropic" || fetchMut.isPending}
          onClick={() => fetchMut.mutate()}
          title={fetchDisabledHint || ""}
        >
          {fetchMut.isPending ? (
            <Loader2 className="mr-1 h-4 w-4 animate-spin" />
          ) : (
            <Download className="mr-1 h-4 w-4" />
          )}
          Fetch 模型列表
        </Button>
      </div>

      {fetchDisabledHint && !fetchMut.isPending ? (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-700">
          {fetchDisabledHint}
        </p>
      ) : null}

      {/* 自定义添加 */}
      <div className="flex items-end gap-2">
        <div className="flex-1 space-y-1">
          <Label className="text-xs">自定义添加</Label>
          <Input
            value={customId}
            onChange={(e) => setCustomId(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addCustom();
              }
            }}
            placeholder="例如：gpt-4o-mini / claude-haiku-4-5 / glm-4-air"
            maxLength={128}
          />
        </div>
        <Button type="button" size="sm" onClick={addCustom} disabled={!customId.trim()}>
          <Plus className="mr-1 h-4 w-4" /> 添加
        </Button>
      </div>

      {/* 模型列表 */}
      {models.length === 0 ? (
        <p className="rounded-md border border-dashed py-4 text-center text-xs text-muted-foreground">
          尚无候选模型。{persisted ? "点 Fetch 自动拉，或在上面手动添加" : "先保存 provider 后再 Fetch / 添加"}
        </p>
      ) : (
        <div className="rounded-md border overflow-hidden">
          {models.map((m, idx) => {
            const isDefault = m.id === defaultModel;
            const result = testResults[m.id];
            return (
              <div
                key={m.id}
                className="flex items-center gap-2 border-b px-2 py-1.5 last:border-b-0 text-sm"
              >
                <Switch
                  checked={m.enabled}
                  onCheckedChange={() => toggle(idx)}
                />
                <span className="font-mono text-xs flex-1 truncate" title={m.id}>
                  {m.id}
                </span>
                {m.custom ? (
                  <Badge variant="outline" className="text-[10px]">custom</Badge>
                ) : null}
                {isDefault ? (
                  <Badge variant="success" className="text-[10px]">默认</Badge>
                ) : null}
                {result ? (
                  result.ok ? (
                    <Badge variant="success" className="gap-1 text-[10px]">
                      <CheckCircle2 className="h-3 w-3" />
                      {result.latency_ms} ms
                    </Badge>
                  ) : (
                    <Badge
                      variant="destructive"
                      className="gap-1 text-[10px]"
                      title={result.error || ""}
                    >
                      <XCircle className="h-3 w-3" />
                      失败
                    </Badge>
                  )
                ) : null}
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={!persisted || testingId !== null}
                  onClick={() => onTest(m.id)}
                  title={persisted ? "测试连通性 + 延时" : "先保存 provider 再测"}
                >
                  {testingId === m.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    "测试"
                  )}
                </Button>
                {!isDefault ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => onSetDefault(m.id)}
                    title="设为默认模型 ID"
                  >
                    <Star className="h-3.5 w-3.5" />
                  </Button>
                ) : null}
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => remove(idx)}
                  title="移除"
                >
                  <Trash2 className="h-3.5 w-3.5 text-destructive" />
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

===== frontend/src/pages/Settings/PluginManager.tsx =====
// 第三方插件管理：上传 zip + 启停 / 卸载（Sprint2 #4 阶段 B）
//
// - 顶部一个上传区域，支持 zip + 可选 .sig
// - 中部表格展示 plugin_install 行
// - 每行右侧有"启用 / 禁用 / 卸载"操作
// - 签名状态 badge：绿（通过）/ 黄（未签名）/ 红（失败）
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, FileWarning, Trash2, Upload, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/misc";
import {
  disableInstall,
  enableInstall,
  listInstalledPackages,
  uninstallPlugin,
  uploadPluginZip,
} from "@/api/plugins";
import { getErrMsg } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";

const PLUGINS_QK = ["plugins", "installed-packages"] as const;

export function PluginManager() {
  const qc = useQueryClient();

  // 列表查询
  const listQ = useQuery({
    queryKey: PLUGINS_QK,
    queryFn: listInstalledPackages,
  });

  // 上传表单状态
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [sigFile, setSigFile] = useState<File | null>(null);
  const zipRef = useRef<HTMLInputElement>(null);
  const sigRef = useRef<HTMLInputElement>(null);

  const uploadMut = useMutation({
    mutationFn: () => {
      if (!zipFile) throw new Error("请选择 zip 文件");
      return uploadPluginZip(zipFile, sigFile);
    },
    onSuccess: (row) => {
      toast.success(`已安装 ${row.key} v${row.version}`);
      // 清空表单
      setZipFile(null);
      setSigFile(null);
      if (zipRef.current) zipRef.current.value = "";
      if (sigRef.current) sigRef.current.value = "";
      qc.invalidateQueries({ queryKey: PLUGINS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const enableMut = useMutation({
    mutationFn: (key: string) => enableInstall(key),
    onSuccess: (row) => {
      toast.success(`已启用 ${row.key}`);
      qc.invalidateQueries({ queryKey: PLUGINS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const disableMut = useMutation({
    mutationFn: (key: string) => disableInstall(key),
    onSuccess: (row) => {
      toast.success(`已禁用 ${row.key}`);
      qc.invalidateQueries({ queryKey: PLUGINS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const uninstallMut = useMutation({
    mutationFn: (key: string) => uninstallPlugin(key),
    onSuccess: (_void, key) => {
      toast.success(`已卸载 ${key}`);
      qc.invalidateQueries({ queryKey: PLUGINS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">插件管理</h1>
        <p className="text-sm text-muted-foreground">
          上传第三方 zip 插件、启停 / 卸载；签名失败的插件不能直接启用。
        </p>
      </div>

      {/* 上传区域 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">上传插件包</CardTitle>
          <CardDescription>
            zip 中需含 <code>manifest.py</code> / <code>__init__.py</code> /{" "}
            <code>plugin.py</code>。
            可选附 <code>.sig</code>（detached Ed25519 签名）。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="plugin-zip">插件 ZIP（必填）</Label>
              <Input
                id="plugin-zip"
                ref={zipRef}
                type="file"
                accept=".zip,application/zip"
                onChange={(e) => setZipFile(e.target.files?.[0] ?? null)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="plugin-sig">签名 .sig（可选）</Label>
              <Input
                id="plugin-sig"
                ref={sigRef}
                type="file"
                accept=".sig,application/octet-stream"
                onChange={(e) => setSigFile(e.target.files?.[0] ?? null)}
              />
            </div>
          </div>
          <div className="flex justify-end">
            <Button
              onClick={() => uploadMut.mutate()}
              disabled={!zipFile || uploadMut.isPending}
            >
              <Upload className="mr-1 h-4 w-4" />
              {uploadMut.isPending ? "上传中..." : "上传"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 列表 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">已安装第三方插件</CardTitle>
          <CardDescription>
            内置 5 个插件不在此列；它们的安装状态由"功能矩阵"页管理。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {listQ.isLoading ? (
            <div className="flex h-24 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : !listQ.data || listQ.data.length === 0 ? (
            <p className="rounded-md border border-dashed py-8 text-center text-xs text-muted-foreground">
              尚未安装任何第三方插件。上传一个 zip 试试。
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Key</TableHead>
                  <TableHead>版本</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead>签名</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>安装时间</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {listQ.data.map((row) => (
                  <TableRow key={row.key}>
                    <TableCell className="font-mono text-xs">{row.key}</TableCell>
                    <TableCell>{row.version}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{row.source}</Badge>
                    </TableCell>
                    <TableCell>
                      <SignatureBadge ok={row.signature_ok} />
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={row.enabled ? "default" : "outline"}
                      >
                        {row.enabled ? "已启用" : "未启用"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDateTime(row.installed_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        {row.enabled ? (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => disableMut.mutate(row.key)}
                            disabled={disableMut.isPending}
                          >
                            禁用
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            // 签名失败时禁止启用——按钮也禁用
                            disabled={
                              row.signature_ok === false ||
                              enableMut.isPending
                            }
                            onClick={() => enableMut.mutate(row.key)}
                            title={
                              row.signature_ok === false
                                ? "签名校验失败，无法启用；请重新上传带正确签名的 zip"
                                : ""
                            }
                          >
                            启用
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            if (
                              !confirm(
                                `确认卸载插件「${row.key}」？目录与表记录都会被删除`,
                              )
                            )
                              return;
                            uninstallMut.mutate(row.key);
                          }}
                          disabled={uninstallMut.isPending}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ─── 签名状态 badge ──────────────────────────────────────
function SignatureBadge({ ok }: { ok: boolean | null }) {
  if (ok === true) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
        <CheckCircle2 className="h-3.5 w-3.5" />
        通过
      </span>
    );
  }
  if (ok === false) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-destructive">
        <XCircle className="h-3.5 w-3.5" />
        失败
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-amber-600">
      <FileWarning className="h-3.5 w-3.5" />
      未签名
    </span>
  );
}

===== frontend/src/pages/Settings/PluginMarket.tsx =====
// 插件市场（仓库订阅 + 同步 + 从仓库安装）— Sprint2 #4 阶段 C
//
// - 顶部：添加 / 列出 / 删除仓库；每行一个"同步"按钮拉远端 index.json
// - 底部：所有仓库的 plugin_available 合表展示，每行一个"安装"按钮
// - 安装成功后会写 plugin_install 表；用户在 PluginManager 页能看到这条新装的插件
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, Plus, RefreshCw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/misc";
import {
  createPluginRepo,
  deletePluginRepo,
  installFromRepo,
  listAvailablePlugins,
  listPluginRepos,
  syncPluginRepo,
} from "@/api/plugins";
import { getErrMsg } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";

const REPOS_QK = ["plugins", "repos"] as const;
const AVAIL_QK = ["plugins", "available"] as const;

export function PluginMarket() {
  const qc = useQueryClient();

  // ─── 仓库列表 ────────────────────────────────────────
  const reposQ = useQuery({ queryKey: REPOS_QK, queryFn: listPluginRepos });
  const availQ = useQuery({ queryKey: AVAIL_QK, queryFn: listAvailablePlugins });

  // 新增仓库表单
  const [newName, setNewName] = useState("");
  const [newUrl, setNewUrl] = useState("");

  const addRepoMut = useMutation({
    mutationFn: () => createPluginRepo(newName.trim(), newUrl.trim()),
    onSuccess: () => {
      toast.success("已添加仓库");
      setNewName("");
      setNewUrl("");
      qc.invalidateQueries({ queryKey: REPOS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteRepoMut = useMutation({
    mutationFn: (id: number) => deletePluginRepo(id),
    onSuccess: () => {
      toast.success("已删除仓库");
      qc.invalidateQueries({ queryKey: REPOS_QK });
      qc.invalidateQueries({ queryKey: AVAIL_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const syncRepoMut = useMutation({
    mutationFn: (id: number) => syncPluginRepo(id),
    onSuccess: (data) => {
      toast.success(`同步完成，刷新 ${data.inserted} 条`);
      qc.invalidateQueries({ queryKey: REPOS_QK });
      qc.invalidateQueries({ queryKey: AVAIL_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const installMut = useMutation({
    mutationFn: ({ repo_id, key }: { repo_id: number; key: string }) =>
      installFromRepo(repo_id, key),
    onSuccess: (row) => {
      toast.success(`已安装 ${row.key} v${row.version}`);
      qc.invalidateQueries({ queryKey: ["plugins", "installed-packages"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">插件市场</h2>
        <p className="text-sm text-muted-foreground">
          订阅 apt 风格的远程仓库；点同步拉取索引、点安装下载并解压到本地。
        </p>
      </div>

      {/* 添加仓库 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">添加仓库</CardTitle>
          <CardDescription>
            URL 必须是 http/https 的 <code>index.json</code>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1.5">
              <Label>名称</Label>
              <Input
                placeholder="local"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
            </div>
            <div className="flex-1 space-y-1.5">
              <Label>URL</Label>
              <Input
                placeholder="http://example.com/index.json"
                value={newUrl}
                onChange={(e) => setNewUrl(e.target.value)}
              />
            </div>
            <Button
              onClick={() => addRepoMut.mutate()}
              disabled={
                !newName.trim() || !newUrl.trim() || addRepoMut.isPending
              }
            >
              <Plus className="mr-1 h-4 w-4" />
              添加
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 仓库列表 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">已订阅仓库</CardTitle>
        </CardHeader>
        <CardContent>
          {reposQ.isLoading ? (
            <div className="flex h-16 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : !reposQ.data || reposQ.data.length === 0 ? (
            <p className="rounded-md border border-dashed py-6 text-center text-xs text-muted-foreground">
              尚未订阅仓库。
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>URL</TableHead>
                  <TableHead>上次同步</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {reposQ.data.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">{r.name}</TableCell>
                    <TableCell className="font-mono text-xs">{r.url}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {r.last_synced_at ? formatDateTime(r.last_synced_at) : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => syncRepoMut.mutate(r.id)}
                          disabled={syncRepoMut.isPending}
                        >
                          <RefreshCw className="mr-1 h-3.5 w-3.5" />
                          同步
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            if (!confirm(`删除仓库「${r.name}」？`)) return;
                            deleteRepoMut.mutate(r.id);
                          }}
                          disabled={deleteRepoMut.isPending}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* 可用插件列表 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">仓库内插件</CardTitle>
          <CardDescription>
            点同步后会刷新本表；点安装会下载 zip 解压并写入安装表（默认未启用）。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {availQ.isLoading ? (
            <div className="flex h-16 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : !availQ.data || availQ.data.length === 0 ? (
            <p className="rounded-md border border-dashed py-6 text-center text-xs text-muted-foreground">
              尚无可用插件，先去同步一个仓库吧。
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Key</TableHead>
                  <TableHead>名称</TableHead>
                  <TableHead>版本</TableHead>
                  <TableHead>仓库</TableHead>
                  <TableHead>作者</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {availQ.data.map((p) => (
                  <TableRow key={`${p.repo_id}-${p.key}`}>
                    <TableCell className="font-mono text-xs">{p.key}</TableCell>
                    <TableCell>{p.name}</TableCell>
                    <TableCell>{p.version}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">#{p.repo_id}</Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {p.author ?? "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        onClick={() =>
                          installMut.mutate({
                            repo_id: p.repo_id,
                            key: p.key,
                          })
                        }
                        disabled={installMut.isPending}
                      >
                        <Download className="mr-1 h-3.5 w-3.5" />
                        安装
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

===== frontend/src/pages/Settings/ProxyManager.tsx =====
// 代理库管理：列表 + 新建（含 type/host/port/账密）+ 测试连通性 + 删除
// 在 Settings 页里以一个 Card 形式嵌入。
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Activity, Loader2, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/misc";
import {
  createProxy,
  deleteProxy,
  listProxies,
  testProxy,
} from "@/api/proxies";
import type {
  ProxyOut,
  ProxyTestResult,
  ProxyType,
} from "@/api/types";
import { getErrMsg } from "@/lib/api";

const TYPE_OPTIONS: { value: ProxyType; label: string }[] = [
  { value: "socks5", label: "SOCKS5" },
  { value: "http", label: "HTTP" },
  { value: "https", label: "HTTPS" },
  { value: "mtproxy", label: "MTProxy" },
];

function flagOf(country?: string | null): string {
  if (!country || country.length !== 2) return "🌐";
  const cp = (s: string) => 0x1f1e6 + (s.toUpperCase().charCodeAt(0) - 65);
  try {
    return String.fromCodePoint(cp(country[0]), cp(country[1]));
  } catch {
    return "🌐";
  }
}

export function ProxyManager() {
  const qc = useQueryClient();
  const proxiesQ = useQuery({ queryKey: ["proxies"], queryFn: listProxies });

  // 新建表单
  const [type, setType] = useState<ProxyType>("socks5");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("1080");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const createMut = useMutation({
    mutationFn: () =>
      createProxy({
        type,
        host: host.trim(),
        port: Number(port),
        username: username.trim() || null,
        password: password || null,
      }),
    onSuccess: () => {
      toast.success("已创建");
      setHost("");
      setUsername("");
      setPassword("");
      qc.invalidateQueries({ queryKey: ["proxies"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteProxy(id),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["proxies"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // 每条 proxy 的测试结果 inline 显示
  const [testResults, setTestResults] = useState<
    Record<number, ProxyTestResult | "loading">
  >({});

  async function handleTest(p: ProxyOut) {
    setTestResults((m) => ({ ...m, [p.id]: "loading" }));
    try {
      const res = await testProxy(p.id);
      setTestResults((m) => ({ ...m, [p.id]: res }));
      if (res.ok) {
        toast.success(
          `测试通过：${flagOf(res.country)} ${res.country || "?"} · ${res.latency_ms}ms`,
        );
      } else {
        toast.error(`测试失败：${res.error || "未知错误"}`);
      }
    } catch (err) {
      toast.error(getErrMsg(err));
      setTestResults((m) => {
        const { [p.id]: _, ...rest } = m;
        return rest;
      });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">网络代理模板</CardTitle>
        <CardDescription>
          公用代理池：在绑定 TG 账号或账号详情中可选用其中一个；带「测试」按钮验证连通性 + 出口归属地
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 新建 */}
        <div className="grid grid-cols-1 gap-2 md:grid-cols-[120px_1fr_100px_1fr_1fr_auto]">
          <div className="space-y-1.5">
            <Label className="text-xs">类型</Label>
            <Select
              value={type}
              onChange={(e) => setType(e.target.value as ProxyType)}
            >
              {TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">主机</Label>
            <Input
              placeholder="1.2.3.4 或 example.com"
              value={host}
              onChange={(e) => setHost(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">端口</Label>
            <Input
              inputMode="numeric"
              value={port}
              onChange={(e) => setPort(e.target.value.replace(/[^0-9]/g, ""))}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">用户名（可选）</Label>
            <Input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">密码（可选）</Label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="flex items-end">
            <Button
              onClick={() => createMut.mutate()}
              disabled={
                !host.trim() ||
                !port ||
                Number(port) <= 0 ||
                createMut.isPending
              }
              className="w-full md:w-auto"
            >
              <Plus className="mr-1 h-4 w-4" /> 新建
            </Button>
          </div>
        </div>

        {/* 列表 */}
        {proxiesQ.isLoading ? (
          <div className="flex h-16 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : proxiesQ.data && proxiesQ.data.length > 0 ? (
          <ul className="divide-y rounded-md border">
            {proxiesQ.data.map((p) => {
              const tr = testResults[p.id];
              const isLoading = tr === "loading";
              const result = isLoading ? null : (tr as ProxyTestResult | undefined);
              return (
                <li key={p.id} className="space-y-1 px-3 py-2 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-secondary px-1.5 py-0.5 text-xs font-mono uppercase">
                        {p.type}
                      </span>
                      <span className="font-mono">
                        {p.host}:{p.port}
                      </span>
                      {p.username ? (
                        <span className="text-xs text-muted-foreground">
                          @ {p.username}
                          {p.has_password ? " · 含密码" : ""}
                        </span>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleTest(p)}
                        disabled={isLoading}
                      >
                        {isLoading ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Activity className="h-4 w-4" />
                        )}
                        <span className="ml-1">测试</span>
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={deleteMut.isPending}
                        onClick={() => {
                          if (!confirm(`确认删除代理 ${p.host}:${p.port}？`)) return;
                          deleteMut.mutate(p.id);
                        }}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                  </div>
                  {result ? (
                    result.ok ? (
                      <div className="text-xs text-emerald-700">
                        ✓ 通过 · {result.latency_ms}ms ·{" "}
                        {flagOf(result.country)} {result.country || "?"}
                        {result.city ? ` · ${result.city}` : ""}
                        {result.exit_ip ? (
                          <span className="ml-1 font-mono text-muted-foreground">
                            ({result.exit_ip})
                          </span>
                        ) : null}
                      </div>
                    ) : (
                      <div className="text-xs text-destructive">
                        ✗ {result.error || "未知错误"}
                      </div>
                    )
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="rounded-md border border-dashed py-6 text-center text-xs text-muted-foreground">
            尚无代理。绑定 TG 账号时如需走代理，先在此新建
          </p>
        )}
      </CardContent>
    </Card>
  );
}

===== frontend/src/pages/Settings/RateTemplates.tsx =====
// 风控模板：账号可绑定模板作为默认风控阈值预设。
// 原本嵌在 SettingsIndex 里，现归到「通用模板」页统一管理。
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Star, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/misc";
import {
  createRateTemplate,
  deleteRateTemplate,
  listRateTemplates,
} from "@/api/system";
import { getErrMsg } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";

export function RateTemplates() {
  const qc = useQueryClient();

  const templatesQ = useQuery({
    queryKey: ["rate-templates"],
    queryFn: listRateTemplates,
  });

  const [newTplName, setNewTplName] = useState("");
  const [newTplDefault, setNewTplDefault] = useState(false);

  const createMut = useMutation({
    mutationFn: () =>
      createRateTemplate({ name: newTplName.trim(), is_default: newTplDefault }),
    onSuccess: () => {
      toast.success("已创建");
      setNewTplName("");
      setNewTplDefault(false);
      qc.invalidateQueries({ queryKey: ["rate-templates"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteRateTemplate(id),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["rate-templates"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">风控模板</CardTitle>
        <CardDescription>
          一组阈值（每秒 / 每分钟 / 每小时 / 每日 API 调用上限）。被账号绑定后作为默认起点；
          单条规则的精细调整在账号详情 → 风控基础。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 新建表单 */}
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[12rem] flex-1 space-y-1.5">
            <Label>模板名称</Label>
            <Input
              placeholder="例如：conservative"
              value={newTplName}
              onChange={(e) => setNewTplName(e.target.value)}
              maxLength={50}
            />
          </div>
          <div className="flex items-center gap-2 pb-2">
            <Switch
              checked={newTplDefault}
              onCheckedChange={setNewTplDefault}
              id="rateNewDefault"
            />
            <Label htmlFor="rateNewDefault" className="cursor-pointer">
              设为默认
            </Label>
          </div>
          <Button
            onClick={() => createMut.mutate()}
            disabled={!newTplName.trim() || createMut.isPending}
          >
            <Plus className="mr-1 h-4 w-4" /> 新建
          </Button>
        </div>

        {/* 列表 */}
        {templatesQ.isLoading ? (
          <div className="flex h-16 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : templatesQ.data && templatesQ.data.length > 0 ? (
          <ul className="divide-y rounded-md border">
            {templatesQ.data.map((t) => (
              <li
                key={t.id}
                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
              >
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="truncate font-medium">{t.name}</span>
                  {t.is_default ? (
                    <span className="inline-flex items-center gap-0.5 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
                      <Star className="h-3 w-3" /> 默认
                    </span>
                  ) : null}
                  <span className="text-xs text-muted-foreground">
                    创建于 {formatDateTime(t.created_at)}
                  </span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={deleteMut.isPending}
                  onClick={() => {
                    if (!confirm(`确认删除模板「${t.name}」？`)) return;
                    deleteMut.mutate(t.id);
                  }}
                >
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="rounded-md border border-dashed py-6 text-center text-xs text-muted-foreground">
            尚无模板。新建一个后即可在账号详情中绑定
          </p>
        )}
      </CardContent>
    </Card>
  );
}

===== frontend/src/pages/Settings/UserAccount.tsx =====
// 系统设置 → 当前用户账号管理：修改密码 + (可选) 禁用 TOTP
//
// 不提供"用户列表"——本系统是单租户的超管模型，只有一个 web 用户；
// 真正需要换人时走数据库手动改 username + 密码即可。
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { KeyRound, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { fetchMe } from "@/lib/auth";
import { api, getErrMsg } from "@/lib/api";

export function UserAccount() {
  const qc = useQueryClient();
  const meQ = useQuery({ queryKey: ["auth", "me"], queryFn: fetchMe });

  // ── 修改密码 ────────────────────────────────────────────────
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [newPwd2, setNewPwd2] = useState("");

  const changeMut = useMutation({
    mutationFn: async () => {
      await api.post("/api/auth/change-password", {
        old_password: oldPwd,
        new_password: newPwd,
      });
    },
    onSuccess: () => {
      // 后端已清 cookie；提示后跳登录页
      toast.success("密码已修改，请用新密码重新登录");
      setOldPwd("");
      setNewPwd("");
      setNewPwd2("");
      qc.clear();
      // 用 hard reload 避免 React Query 还在用旧 token 再发请求
      setTimeout(() => {
        window.location.href = "/login";
      }, 800);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const handleChange = () => {
    if (!oldPwd || !newPwd || !newPwd2) {
      toast.error("三项都要填");
      return;
    }
    if (newPwd.length < 8) {
      toast.error("新密码至少 8 位");
      return;
    }
    if (newPwd !== newPwd2) {
      toast.error("两次输入的新密码不一致");
      return;
    }
    if (oldPwd === newPwd) {
      toast.error("新密码不能与旧密码相同");
      return;
    }
    changeMut.mutate();
  };

  // ── 禁用 TOTP ──────────────────────────────────────────────
  const [totpCode, setTotpCode] = useState("");
  const disableTotpMut = useMutation({
    mutationFn: async () => {
      await api.post("/api/auth/totp/disable", { code: totpCode });
    },
    onSuccess: () => {
      toast.success("已禁用动态验证码（TOTP）");
      setTotpCode("");
      qc.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <KeyRound className="h-4 w-4" /> 当前账号
        </CardTitle>
        <CardDescription>
          {meQ.data ? (
            <>
              已登录：<span className="font-mono">{meQ.data.username}</span>
              {meQ.data.has_totp ? (
                <span className="ml-2 inline-flex items-center gap-1 text-emerald-700">
                  <ShieldCheck className="h-3.5 w-3.5" /> TOTP 已启用
                </span>
              ) : (
                <span className="ml-2 text-amber-700">TOTP 未启用</span>
              )}
            </>
          ) : (
            "加载中…"
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* 修改密码 */}
        <div className="space-y-3 max-w-md">
          <h3 className="text-sm font-medium">修改密码</h3>
          <div className="space-y-2">
            <Label htmlFor="oldpwd">当前密码</Label>
            <Input
              id="oldpwd"
              type="password"
              autoComplete="current-password"
              value={oldPwd}
              onChange={(e) => setOldPwd(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="newpwd">新密码（≥ 8 位）</Label>
            <Input
              id="newpwd"
              type="password"
              autoComplete="new-password"
              value={newPwd}
              onChange={(e) => setNewPwd(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="newpwd2">确认新密码</Label>
            <Input
              id="newpwd2"
              type="password"
              autoComplete="new-password"
              value={newPwd2}
              onChange={(e) => setNewPwd2(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleChange();
              }}
            />
          </div>
          <Button onClick={handleChange} disabled={changeMut.isPending}>
            修改密码
          </Button>
          <p className="text-xs text-muted-foreground">
            修改成功后会强制下线，请用新密码重新登录
          </p>
        </div>

        {/* 禁用 TOTP（仅当前已启用时显示） */}
        {meQ.data?.has_totp ? (
          <div className="space-y-3 max-w-md border-t pt-4">
            <h3 className="text-sm font-medium">禁用动态验证码</h3>
            <p className="text-xs text-muted-foreground">
              输入当前 6 位 TOTP 码以禁用 2FA。禁用后下次登录将不再要求二次验证。
            </p>
            <div className="flex gap-2 items-end">
              <div className="flex-1 space-y-2">
                <Label htmlFor="totpcode">当前 TOTP 码</Label>
                <Input
                  id="totpcode"
                  inputMode="numeric"
                  maxLength={8}
                  placeholder="6 位数字"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ""))}
                />
              </div>
              <Button
                variant="outline"
                onClick={() => {
                  if (totpCode.length < 6) {
                    toast.error("TOTP 码至少 6 位");
                    return;
                  }
                  if (!confirm("确认禁用 TOTP？账号安全等级会降低")) return;
                  disableTotpMut.mutate();
                }}
                disabled={disableTotpMut.isPending}
              >
                禁用 TOTP
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

===== frontend/src/pages/Templates.tsx =====
// 顶层「通用模板」页：把可被多个账号复用的 4 类模板集中到一处。
//   - 风控模板        每秒/每分钟/每小时/每日 API 调用上限
//   - 网络代理模板    SOCKS5/HTTP/MTProxy 代理出口
//   - 设备标识模板    device_model / system_version / app_version / lang_code
//   - 自定义命令模板  ,name 命令的配方（含 ai/regex/manifest 等类型）
//
// 这些模板都是「账号级别可引用」的预设；账号详情里通过下拉选择即可应用。
import { RateTemplates } from "@/pages/Settings/RateTemplates";
import { ProxyManager } from "@/pages/Settings/ProxyManager";
import { DeviceProfileManager } from "@/pages/Settings/DeviceProfileManager";
import { CommandTemplates } from "@/pages/Settings/CommandTemplates";

export function Templates() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">通用模板</h1>
        <p className="text-sm text-muted-foreground">
          可被已绑定的 TG 账号选用的各类模板：风控阈值、网络代理、设备标识、自定义命令。
          一处维护，账号详情里选用即可。
        </p>
      </div>

      <RateTemplates />
      <ProxyManager />
      <DeviceProfileManager />
      <CommandTemplates />
    </div>
  );
}

===== frontend/src/pwa.ts =====
// PWA Service Worker 注册 + 更新提示。
// vite-plugin-pwa 在构建时把 `virtual:pwa-register` 解析为真正的注册代码；
// 类型声明在 src/vite-env.d.ts 里通过 `vite-plugin-pwa/client` 引入。
import { toast } from "sonner";

export function registerPWA() {
  // 服务端渲染 / 测试环境跳过
  if (typeof window === "undefined") return;

  // 动态 import 避免在没有插件的构建里直接报错
  import("virtual:pwa-register")
    .then(({ registerSW }) => {
      const updateSW = registerSW({
        // 检测到新版本：sonner 弹一个带"刷新"按钮的提示
        onNeedRefresh() {
          toast("发现新版本", {
            description: "点击刷新加载最新内容",
            duration: Infinity,
            action: {
              label: "刷新",
              onClick: () => updateSW(true),
            },
          });
        },
        // 首次缓存完成（可离线使用）
        onOfflineReady() {
          toast.success("已可离线使用");
        },
        immediate: true,
      });
    })
    .catch(() => {
      // 开发环境关闭 PWA 时这里会失败，静默忽略
    });
}

===== frontend/src/vite-env.d.ts =====
/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

```
