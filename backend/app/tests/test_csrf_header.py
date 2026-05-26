from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _write_headers(token: str, *, requested_with: str = "telepilot-ui") -> dict[str, str]:
    return {
        "X-Requested-With": requested_with,
        "X-CSRF-Token": token,
        "Cookie": f"csrf_token={token}",
    }


@pytest.mark.asyncio
async def test_post_without_csrf_header_rejected() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/logout")
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "CSRF_HEADER_REQUIRED"


@pytest.mark.asyncio
async def test_get_csrf_sets_token_cookie() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/auth/csrf")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert "csrf_token=" in r.headers["set-cookie"]


@pytest.mark.asyncio
async def test_post_with_csrf_header_but_missing_token_rejected() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/logout", headers={"X-Requested-With": "telepilot-ui"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_TOKEN_REQUIRED"


@pytest.mark.asyncio
async def test_post_with_mismatched_csrf_token_rejected() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/auth/logout",
            headers={
                "X-Requested-With": "telepilot-ui",
                "X-CSRF-Token": "header-token",
                "Cookie": "csrf_token=cookie-token",
            },
        )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_TOKEN_REQUIRED"


@pytest.mark.asyncio
async def test_post_with_csrf_header_allowed() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/logout", headers=_write_headers("token"))
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_post_with_legacy_csrf_header_allowed() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/auth/logout",
            headers=_write_headers("legacy-token", requested_with="telebot-ui"),
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_post_with_unknown_csrf_header_rejected() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/logout", headers={"X-Requested-With": "random-ui"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_HEADER_REQUIRED"


@pytest.mark.asyncio
async def test_get_does_not_require_csrf_header() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
