"""init schema

Revision ID: 0001
Revises:
Create Date: 2026-05-02

按 PRD §8 一次性建出全部 14 张表（PostgreSQL 方言）。
顺序考虑：
- ``rate_limit_template`` 必须先于 ``account``（``account.template_id`` 外键）
- ``proxy`` 必须先于 ``account``（``account.proxy_id`` 外键）
- ``account`` 必须先于 ``humanize_config / account_feature / rule / 风控事件 / 日志``
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """建表 SQL 与 PRD §8 严格对齐。"""
    op.execute(
        """
        -- ────────────────────────────────────────────────────────────
        -- 系统级
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE web_user (
            id              BIGSERIAL PRIMARY KEY,
            username        TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            -- 模型字段名为 totp_secret_enc（明确表示加密后的值）
            totp_secret_enc TEXT,
            created_at      TIMESTAMPTZ DEFAULT now()
        );

        CREATE TABLE system_setting (
            key             TEXT PRIMARY KEY,
            value           JSONB NOT NULL,
            updated_at      TIMESTAMPTZ DEFAULT now()
        );

        CREATE TABLE notification_channel (
            id              BIGSERIAL PRIMARY KEY,
            type            TEXT NOT NULL,
            config          JSONB NOT NULL,
            enabled         BOOLEAN DEFAULT true
        );

        -- ────────────────────────────────────────────────────────────
        -- 风控模板（先于 account 创建）
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE rate_limit_template (
            id              BIGSERIAL PRIMARY KEY,
            name            TEXT NOT NULL,
            is_default      BOOLEAN DEFAULT false,
            created_at      TIMESTAMPTZ DEFAULT now()
        );

        -- ────────────────────────────────────────────────────────────
        -- 出口代理（先于 account 创建）
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE proxy (
            id              BIGSERIAL PRIMARY KEY,
            type            TEXT NOT NULL,
            host            TEXT NOT NULL,
            port            INTEGER NOT NULL,
            username        TEXT,
            password_enc    TEXT
        );

        -- ────────────────────────────────────────────────────────────
        -- 账号
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE account (
            id                 BIGSERIAL PRIMARY KEY,
            phone              TEXT NOT NULL,
            display_name       TEXT,
            api_id_enc         TEXT NOT NULL,
            api_hash_enc       TEXT NOT NULL,
            session_enc        BYTEA NOT NULL,
            -- 默认 login_required，登录绑定完成后再置为 active
            status             TEXT NOT NULL DEFAULT 'login_required',
            template_id        BIGINT REFERENCES rate_limit_template(id),
            proxy_id           BIGINT REFERENCES proxy(id),
            cold_start_until   DATE,
            tags               TEXT[],
            notes              TEXT,
            created_at         TIMESTAMPTZ DEFAULT now(),
            updated_at         TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX ix_account_status ON account (status);

        CREATE TABLE humanize_config (
            account_id           BIGINT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
            jitter_pct           SMALLINT DEFAULT 15,
            typing_simulate      BOOLEAN DEFAULT true,
            typing_min_ms        INTEGER DEFAULT 1000,
            typing_max_ms        INTEGER DEFAULT 3000,
            typing_probability   SMALLINT DEFAULT 80,
            read_before_reply    BOOLEAN DEFAULT true,
            active_window_start  TIME,
            active_window_end    TIME,
            cold_start_days      SMALLINT DEFAULT 7
        );

        -- ────────────────────────────────────────────────────────────
        -- 功能 / 插件 / 规则
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE feature (
            key             TEXT PRIMARY KEY,
            display_name    TEXT NOT NULL,
            is_builtin      BOOLEAN DEFAULT false,
            version         TEXT,
            manifest        JSONB
        );

        CREATE TABLE account_feature (
            account_id      BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            feature_key     TEXT NOT NULL REFERENCES feature(key),
            enabled         BOOLEAN DEFAULT false,
            config          JSONB DEFAULT '{}',
            state           TEXT DEFAULT 'disabled',
            last_error      TEXT,
            installed_at    TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (account_id, feature_key)
        );

        CREATE TABLE rule (
            id              BIGSERIAL PRIMARY KEY,
            account_id      BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            feature_key     TEXT NOT NULL,
            name            TEXT NOT NULL,
            enabled         BOOLEAN DEFAULT true,
            priority        INTEGER DEFAULT 100,
            config          JSONB NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT now(),
            updated_at      TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX ix_rule_account_feature_enabled ON rule (account_id, feature_key, enabled);

        -- ────────────────────────────────────────────────────────────
        -- 风控（rule / event / override）
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE rate_limit_rule (
            id              BIGSERIAL PRIMARY KEY,
            scope           TEXT NOT NULL,
            scope_id        BIGINT NOT NULL,
            action          TEXT NOT NULL,
            per_second      INTEGER,
            per_minute      INTEGER,
            per_hour        INTEGER,
            per_day         INTEGER,
            same_peer_per_minute INTEGER,
            policy          TEXT NOT NULL DEFAULT 'queue',
            backoff_base_seconds INTEGER DEFAULT 5,
            backoff_max_seconds  INTEGER DEFAULT 1800,
            enabled         BOOLEAN DEFAULT true,
            CONSTRAINT uq_rl_scope_action UNIQUE (scope, scope_id, action)
        );

        CREATE TABLE rate_limit_event (
            id          BIGSERIAL PRIMARY KEY,
            account_id  BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            ts          TIMESTAMPTZ DEFAULT now(),
            action      TEXT NOT NULL,
            outcome     TEXT NOT NULL,
            detail      JSONB
        );
        CREATE INDEX ix_rl_event_account_ts ON rate_limit_event (account_id, ts);
        CREATE INDEX ix_rl_event_account_action_ts ON rate_limit_event (account_id, action, ts);

        CREATE TABLE rate_limit_override (
            id              BIGSERIAL PRIMARY KEY,
            account_id      BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            action          TEXT NOT NULL,
            multiplier      NUMERIC(4,2) NOT NULL,
            reason          TEXT,
            expires_at      TIMESTAMPTZ NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX ix_rl_override_account_expires ON rate_limit_override (account_id, expires_at);

        -- ────────────────────────────────────────────────────────────
        -- 日志
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE audit_log (
            id          BIGSERIAL PRIMARY KEY,
            ts          TIMESTAMPTZ DEFAULT now(),
            user_id     BIGINT REFERENCES web_user(id),
            action      TEXT NOT NULL,
            target      TEXT,
            detail      JSONB
        );

        CREATE TABLE runtime_log (
            id          BIGSERIAL PRIMARY KEY,
            account_id  BIGINT REFERENCES account(id) ON DELETE CASCADE,
            ts          TIMESTAMPTZ DEFAULT now(),
            level       TEXT NOT NULL,
            source      TEXT,
            message     TEXT NOT NULL,
            detail      JSONB
        );
        CREATE INDEX ix_runtime_log_account_ts ON runtime_log (account_id, ts);
        CREATE INDEX ix_runtime_log_account_level_ts ON runtime_log (account_id, level, ts);

        -- ────────────────────────────────────────────────────────────
        -- 插件市场
        -- ────────────────────────────────────────────────────────────
        CREATE TABLE plugin_repo (
            id              BIGSERIAL PRIMARY KEY,
            name            TEXT NOT NULL,
            url             TEXT NOT NULL,
            enabled         BOOLEAN DEFAULT true,
            last_synced_at  TIMESTAMPTZ
        );

        CREATE TABLE plugin_available (
            repo_id         BIGINT NOT NULL REFERENCES plugin_repo(id) ON DELETE CASCADE,
            key             TEXT NOT NULL,
            name            TEXT NOT NULL,
            version         TEXT NOT NULL,
            author          TEXT,
            description     TEXT,
            manifest        JSONB,
            PRIMARY KEY (repo_id, key)
        );
        """
    )


def downgrade() -> None:
    """按反向依赖顺序删表。CASCADE 兜底外键。"""
    op.execute(
        """
        DROP TABLE IF EXISTS plugin_available CASCADE;
        DROP TABLE IF EXISTS plugin_repo CASCADE;
        DROP TABLE IF EXISTS runtime_log CASCADE;
        DROP TABLE IF EXISTS audit_log CASCADE;
        DROP TABLE IF EXISTS rate_limit_override CASCADE;
        DROP TABLE IF EXISTS rate_limit_event CASCADE;
        DROP TABLE IF EXISTS rate_limit_rule CASCADE;
        DROP TABLE IF EXISTS rule CASCADE;
        DROP TABLE IF EXISTS account_feature CASCADE;
        DROP TABLE IF EXISTS feature CASCADE;
        DROP TABLE IF EXISTS humanize_config CASCADE;
        DROP TABLE IF EXISTS account CASCADE;
        DROP TABLE IF EXISTS proxy CASCADE;
        DROP TABLE IF EXISTS rate_limit_template CASCADE;
        DROP TABLE IF EXISTS notification_channel CASCADE;
        DROP TABLE IF EXISTS system_setting CASCADE;
        DROP TABLE IF EXISTS web_user CASCADE;
        """
    )
