"""End-to-end test: hit the /usage endpoint via TestClient with auth bypass,
verify it returns the expected rows."""
from fastapi.testclient import TestClient

from app.db.models.user import WebUser
from app.deps import get_current_user
from app.main import app


def _fake_user() -> WebUser:
    return WebUser(id=1, username="diag", password_hash="x")


def main():
    app.dependency_overrides[get_current_user] = _fake_user
    with TestClient(app) as c:
        r = c.get(
            "/api/proxies/1/usage",
            headers={"X-Requested-With": "telebot-ui"},
        )
        print("status:", r.status_code)
        try:
            print("body:", r.json())
        except Exception:
            print("text:", r.text[:300])


if __name__ == "__main__":
    main()
