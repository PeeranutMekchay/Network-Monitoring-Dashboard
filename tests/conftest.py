import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    for var in ("DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setenv(var, "")
    from app import create_app
    return create_app({
        "TESTING": True,
        "START_SCHEDULER": False,
        "SECRET_KEY": "test",
        "DATABASE_PATH": str(tmp_path / "test.db"),
    })


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client):
    client.get("/login")  # renders a page so the session gets a token
    with client.session_transaction() as sess:
        return sess["csrf_token"]


@pytest.fixture
def auth_client(client):
    token = csrf(client)
    resp = client.post("/login", data={"username": "admin", "password": "secret", "csrf_token": token})
    assert resp.status_code == 302
    client.csrf_token = csrf(client)  # login starts a fresh session with a new token
    return client
