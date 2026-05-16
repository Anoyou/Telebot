from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_remote_plugin_routes_require_login() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/remote-plugins")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTH_REQUIRED"

        r = await c.post(
            "/api/remote-plugins/install",
            headers={"X-Requested-With": "telepilot-ui"},
            json={"source_url": "https://github.com/example/plugin.git"},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_plugin_repo_routes_require_login() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/plugin-repos")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTH_REQUIRED"

        r = await c.post(
            "/api/plugin-repos",
            headers={"X-Requested-With": "telepilot-ui"},
            json={"url": "https://github.com/example/plugins.git"},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTH_REQUIRED"
