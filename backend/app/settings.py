"""全局配置：从 .env 加载，所有模块统一通过 settings.* 读取。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    redis_url: str = "redis://localhost:6379/0"

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
    plugins_installed_dir: str = "./data/plugins/installed"
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
            Path(__file__).resolve().parents[2] / ".env",
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


@lru_cache
def get_settings() -> Settings:
    """单例配置访问器。"""
    return Settings()  # type: ignore[call-arg]


# 顶层导出便于 from app.settings import settings
settings = get_settings()
