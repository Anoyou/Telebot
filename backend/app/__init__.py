"""TelePilot 后端应用包。

``__version__`` 是后端的单点版本号，main.py 的 ``FastAPI(version=...)``、
worker 的 ``,version`` 命令和 ``backend/pyproject.toml``（``dynamic = ["version"]``）均读这里。
版本号只在准备发布、推送稳定检查点、创建 release/PR，或用户明确要求“推一版/发一版”时
统一迭代；不要为每个微小提交单独 bump。不要手改：在仓库根目录运行
``python3 scripts/bump-version.py --title "主题"``，它同时改写 ``frontend/package.json``
（``frontend/src/lib/version.ts`` 从中导入）与 ``openapi/telepilot.openapi.json`` 的 ``info.version``，
并把 ``CHANGELOG.md`` 的 ``Unreleased`` 内容移动到新的中文版本段落。

``APP_STAGE`` 是非正式标签（Sprint 4 / RC1 / 等）；和前端 ``frontend/src/lib/version.ts``
的 ``APP_STAGE`` 必须保持一致。设为 ``None`` 可以摘掉（达到 1.0.0 通常就摘）。
``GET /api/system/version`` 会返回它，前端启动时拉一次做版本一致性检测。
"""
__version__ = "0.107.0-beta.1"
APP_STAGE: str | None = None
