"""全局配置：从 .env 加载，所有模块统一通过 settings.* 读取。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用配置，所有字段都可通过环境变量覆盖（pydantic-settings 自动映射）。"""

    # ── 加密 / 认证 ────────────────────────────────────────────────
    master_key: str = Field(..., description="Fernet 主密钥，加密 session 等敏感字段")
    jwt_secret: str = Field(..., description="JWT HS256 签名密钥")
    jwt_expire_seconds: int = 12 * 3600
    # Cookie 安全：HTTPS 部署应设为 true（反代后由后端直接打 Secure=True，少一层依赖）
    # 默认 false，方便本地 HTTP 调试；生产 .env 显式设 COOKIE_SECURE=true
    cookie_secure: bool = False
    # 登录限速（针对 /api/auth/login 与 /api/auth/register）
    # 0 表示不限速；默认 30 次/分钟，按 IP+用户名两个维度同时计数
    login_rate_limit_per_min: int = 30
    # 登录向导挂起会话上限（防止批量 start_login 造成进程内存占用过高）
    max_pending_logins: int = 100
    # 是否信任 X-Forwarded-For 取客户端 IP；
    # 仅当部署在可信反代（nginx/traefik）后面时才设为 true，否则攻击者可通过伪造头绕过 IP 限速
    trust_forwarded_for: bool = False

    # ── 数据库 / Redis ─────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://telebot:telebot@localhost:5432/telebot"
    db_pool_size: int = 5
    db_max_overflow: int = 2
    db_pool_timeout: int = 30
    # Worker 子进程默认更紧的连接池：worker 大多只用 1-2 条连接，
    # 没必要每个 spawn 出来的子进程都按主进程的 5+2 预留。
    # worker.entry.worker_entry 会在 import runtime 之前设 ``TELEBOT_WORKER_PROC=1``，
    # db.base / redis_client 据此切到下面的 ``*_worker`` 默认值。
    db_pool_size_worker: int = 1
    db_max_overflow_worker: int = 0
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 16
    redis_max_connections_worker: int = 4

    # Worker 周期性配置 reconcile 间隔（秒）；只是 IPC 丢消息兜底，
    # 180s 足够，原 60s 在多账号下产生不必要的 DB 抖动。
    worker_reconcile_seconds: int = 180

    # 是否在每条 incoming TG 消息上都额外写一行可见性 runtime_log。
    # 默认关闭：活跃账号每分钟可能数百条，对小机器是显著开销。
    # 不影响命令派发 / 插件错误 / 业务事件——它们各自独立写日志。
    # 也可在系统设置（``system_setting`` 表 key=``log_incoming_messages``）覆盖。
    log_incoming_messages_default: bool = False

    # ── Web ────────────────────────────────────────────────────────
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    # ── userbot ────────────────────────────────────────────────────
    command_prefix: str = ","
    session_dir: str = "./sessions"
    # 头像本地缓存目录；主进程通过 IPC 让 worker 写盘 ``{aid}.jpg``
    # 24h TTL，worker 离线时返 404 → 前端首字母 fallback
    avatars_dir: str = "./data/avatars"

    # ── 第三方插件（阶段 B/C） ────────────────────────────────
    # 已安装第三方插件的根目录；loader.discover_plugins 会扫描这里下的子目录。
    # 对应 worker/plugins/loader.py 中的 _INSTALLED_DIR；二者一定要一致。
    plugins_installed_dir: str = "./plugins/installed"
    # 插件仓库（plugin_repo）本地克隆缓存目录；用于浏览仓库内可装插件而不重复克隆。
    plugin_repos_cache_dir: str = "./data/plugin_repos"
    # 上传 zip 时验签使用的 Ed25519 公钥（PEM）；为空表示不验签，前端给出"未签名"警告。
    # 公钥示例：-----BEGIN PUBLIC KEY-----\nMC...\n-----END PUBLIC KEY-----
    plugin_pubkey: str = ""
    # 上传 zip 体积上限（字节），默认 10 MiB。超出直接 413。
    plugin_zip_max_bytes: int = 10 * 1024 * 1024

    # 全局默认代理（仅当账号未绑定 Proxy 行时兜底）。
    # 格式：``socks5://[user:pass@]host:port`` 或 ``http://host:port`` 或 ``mtproxy://host:port?secret=xxx``
    # 留空 = 直连（在能直接访问 Telegram 的网络下使用）
    tg_default_proxy: str = ""

    # ── 全局风控 ──────────────────────────────────────────────────
    kill_switch: bool = False
    global_api_qps: int = 0  # 0 表示不限制

    # ── LLM 成本控制 ───────────────────────────────────────────────
    # 以下限制按账号生效，0 表示关闭该项限制。限制在 worker 调用 LLM 前检查，
    # 防止 scheduler / sudo / 误配置高价模型造成不可追踪的成本飙升。
    llm_per_minute_request_limit_per_account: int = 0
    llm_daily_request_limit_per_account: int = 0
    llm_daily_token_limit_per_account: int = 0
    # cost_tier >= 3 视为高价模型；0 表示不限制。
    llm_premium_daily_request_limit_per_account: int = 0
    # 0 表示不覆盖调用方传入的 max_tokens。
    llm_max_output_tokens: int = 0

    # ── 启动期自动迁移 ────────────────────────────────────────────
    # True = backend 启动时自动 ``alembic upgrade head``，把 DB schema 升到代码期望的版本
    #        适合单实例或开发环境
    # False = 完全不动 DB；适合多实例部署、由 CI/CD 单独跑迁移的场景，避免并发起服务时 race
    # 默认 false：与 docker-compose 的"启动前迁移"职责分离，避免重复执行迁移
    auto_migrate_on_startup: bool = False

    # ── 日志 ───────────────────────────────────────────────────────
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=(
            # 优先从仓库根目录 .env 加载
            PROJECT_ROOT / ".env",
            ".env",
        ),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """以逗号分隔的 CORS 源解析为 list。"""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_url_sync(self) -> str:
        """Alembic 用的同步 DSN（去掉 +asyncpg 后缀，psycopg2/psycopg 都能识别）。"""
        return self.database_url.replace("+asyncpg", "")

    def resolve_project_path(self, raw: str) -> Path:
        """把相对路径固定解析到仓库根目录，避免主进程/worker cwd 不同导致分裂。"""
        path = Path(raw)
        if path.is_absolute():
            return path.resolve()
        return (PROJECT_ROOT / path).resolve()

    @property
    def plugins_installed_path(self) -> Path:
        return self.resolve_project_path(self.plugins_installed_dir)


@lru_cache
def get_settings() -> Settings:
    """单例配置访问器。"""
    return Settings()  # type: ignore[call-arg]


# 顶层导出便于 from app.settings import settings
settings = get_settings()
