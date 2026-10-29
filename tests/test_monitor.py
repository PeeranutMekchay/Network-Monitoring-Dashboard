import socket
import threading

import pytest

import monitor


def test_parse_ports():
    assert monitor.parse_ports("443, 80,22,80") == [22, 80, 443]
    assert monitor.parse_ports("") == []
    with pytest.raises(ValueError):
        monitor.parse_ports("80,abc")
    with pytest.raises(ValueError):
        monitor.parse_ports("70000")


def test_hostname_of():
    assert monitor.hostname_of("https://example.com/path") == "example.com"
    assert monitor.hostname_of("192.168.1.1") == "192.168.1.1"


@pytest.fixture
def listening_port():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    stop = threading.Event()

    def accept():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                server.accept()[0].close()
            except OSError:
                pass

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield server.getsockname()[1]
    stop.set()
    server.close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_check_port_open_and_closed(listening_port):
    assert monitor.check_port("127.0.0.1", listening_port) is not None
    assert monitor.check_port("127.0.0.1", free_port(), timeout=0.5) is None


def test_device_online_via_port_when_icmp_blocked(monkeypatch, listening_port):
    monkeypatch.setattr(monitor, "ping", lambda host, timeout=None: None)
    device = {"id": 1, "host": "127.0.0.1", "device_type": "Server", "ports": str(listening_port)}
    result = monitor.check_device(device)
    assert result["status"] == "online"
    assert result["open_ports"] == str(listening_port)
    assert result["latency_ms"] is not None


def test_device_offline(monkeypatch):
    monkeypatch.setattr(monitor, "ping", lambda host, timeout=None: None)
    device = {"id": 1, "host": "127.0.0.1", "device_type": "Router", "ports": str(free_port())}
    result = monitor.check_device(device)
    assert result == {"device_id": 1, "status": "offline", "latency_ms": None,
                      "open_ports": "", "http_status": None}


def test_website_check(monkeypatch):
    monkeypatch.setattr(monitor, "check_website", lambda url: (503, 120.0, None))
    device = {"id": 2, "host": "https://example.com", "device_type": "Website", "ports": ""}
    assert monitor.check_device(device)["status"] == "offline"
    monkeypatch.setattr(monitor, "check_website", lambda url: (200, 80.0, None))
    result = monitor.check_device(device)
    assert result["status"] == "online" and result["http_status"] == 200


def test_scan_subnet_validation():
    with pytest.raises(ValueError):
        monitor.scan_subnet("10.0.0.0/16")
    with pytest.raises(ValueError):
        monitor.scan_subnet("not-a-subnet")


def test_scan_subnet_finds_host(monkeypatch):
    monkeypatch.setattr(monitor, "ping", lambda ip, timeout=None: 1.5 if ip == "10.0.0.2" else None)
    monkeypatch.setattr(monitor, "check_port", lambda ip, port, timeout=None: None)
    found = monitor.scan_subnet("10.0.0.0/29")
    assert [r["ip"] for r in found] == ["10.0.0.2"]
