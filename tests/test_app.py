from datetime import datetime, timedelta

import database
import notifier
import scheduler


def add(client, **overrides):
    data = {"name": "Router", "host": "192.168.1.1", "device_type": "Router",
            "location": "Server Room", "ports": "80,22", "csrf_token": client.csrf_token}
    data.update(overrides)
    return client.post("/devices/add", data=data)


def stub_check(monkeypatch, status="online"):
    monkeypatch.setattr(scheduler.monitor, "check_device", lambda d: {
        "device_id": d["id"], "status": status, "latency_ms": 3.2 if status == "online" else None,
        "open_ports": "80" if status == "online" else "", "http_status": None,
    })


def test_requires_login(client):
    assert client.get("/").status_code == 302
    assert client.get("/api/status").status_code == 401


def test_wrong_password(client):
    from conftest import csrf
    resp = client.post("/login", data={"username": "admin", "password": "x", "csrf_token": csrf(client)})
    assert resp.status_code == 200
    assert "ไม่ถูกต้อง" in resp.get_data(as_text=True)


def test_post_without_csrf_rejected(auth_client):
    assert auth_client.post("/devices/add", data={"name": "x", "host": "1.1.1.1"}).status_code == 400


def test_device_crud(auth_client, monkeypatch):
    stub_check(monkeypatch)
    assert add(auth_client).status_code == 302
    device = database.list_devices()[0]
    assert device["ports"] == "22,80"
    assert database.get_last_log(device["id"])["status"] == "online"

    resp = auth_client.post(f"/devices/{device['id']}/edit", data={
        "name": "Core Router", "host": "router.local", "device_type": "Router",
        "location": "", "ports": "443", "csrf_token": auth_client.csrf_token})
    assert resp.status_code == 302
    assert database.get_device(device["id"])["name"] == "Core Router"

    assert auth_client.get(f"/devices/{device['id']}").status_code == 200
    auth_client.post(f"/devices/{device['id']}/delete", data={"csrf_token": auth_client.csrf_token})
    assert database.list_devices() == []


def test_invalid_device_rejected(auth_client):
    assert add(auth_client, host="not a host!").status_code == 400
    assert add(auth_client, ports="99999").status_code == 400
    assert database.list_devices() == []


def test_status_change_sends_notification(auth_client, monkeypatch):
    sent = []
    monkeypatch.setattr(notifier, "send", lambda msg: sent.append(msg) or ["discord"])
    stub_check(monkeypatch, "online")
    add(auth_client)
    assert sent == []  # first check is not a transition

    stub_check(monkeypatch, "offline")
    scheduler.check_all_devices()
    assert len(sent) == 1 and "OFFLINE" in sent[0]

    scheduler.check_all_devices()
    assert len(sent) == 1  # still offline, no repeat alert

    stub_check(monkeypatch, "online")
    scheduler.check_all_devices()
    assert len(sent) == 2 and "ONLINE" in sent[1]


def test_api_and_uptime(auth_client, monkeypatch):
    stub_check(monkeypatch)
    add(auth_client)
    device_id = database.list_devices()[0]["id"]
    yesterday = (datetime.now() - timedelta(days=1)).strftime(database.TIME_FORMAT)
    database.add_check_log(device_id, "offline", None, "", checked_at=yesterday)

    status = auth_client.get("/api/status").get_json()
    assert status["summary"] == {"total": 1, "online": 1, "offline": 0, "unknown": 0}

    latency = auth_client.get(f"/api/devices/{device_id}/latency?hours=48").get_json()
    assert len(latency) == 2

    uptime = auth_client.get(f"/api/devices/{device_id}/uptime").get_json()
    assert uptime["uptime_7d"] == 50.0
    assert uptime["daily"][-1]["uptime"] == 100.0
    assert uptime["daily"][-2]["uptime"] == 0.0
    assert uptime["daily"][0]["uptime"] is None


def test_export_csv(auth_client, monkeypatch):
    stub_check(monkeypatch)
    add(auth_client)
    resp = auth_client.get("/export.csv")
    assert resp.mimetype == "text/csv"
    lines = resp.get_data(as_text=True).lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("id,device_id,device_name")
    assert len(lines) == 2 and "Router" in lines[1]


def test_pages_render(auth_client, monkeypatch):
    stub_check(monkeypatch)
    add(auth_client)
    for path in ("/", "/devices", "/scan", f"/devices/{database.list_devices()[0]['id']}/edit"):
        assert auth_client.get(path).status_code == 200, path


def test_purge_old_logs(auth_client, monkeypatch):
    stub_check(monkeypatch)
    add(auth_client)
    device_id = database.list_devices()[0]["id"]
    old = (datetime.now() - timedelta(days=100)).strftime(database.TIME_FORMAT)
    database.add_check_log(device_id, "online", 1.0, "", checked_at=old)
    assert database.purge_old_logs(90) == 1
