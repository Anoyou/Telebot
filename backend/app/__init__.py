"""telebot 后端应用包。

``__version__`` 是后端的单点版本号，main.py 的 ``FastAPI(version=...)`` 和
worker 的 ``,version`` 命令均读这里。每次 release 同时改前端 ``frontend/src/lib/version.ts``、
``frontend/package.json``、``backend/pyproject.toml``、``CHANGELOG.md``，详见
``agent-plans/README.md`` §6。

``APP_STAGE`` 是非正式标签（Sprint 4 / RC1 / 等）；和前端 ``frontend/src/lib/version.ts``
的 ``APP_STAGE`` 必须保持一致。设为 ``None`` 可以摘掉（达到 1.0.0 通常就摘）。
``GET /api/system/version`` 会返回它，前端启动时拉一次做版本一致性检测。
"""
__version__ = "0.5.0"
APP_STAGE: str | None = "RC1"
