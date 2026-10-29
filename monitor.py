"""Network checks: ICMP ping, TCP port check, HTTP check and subnet scan."""

import ipaddress
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

COMMON_PORTS = {
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    80: "HTTP",
    139: "NetBIOS",
    443: "HTTPS",
    445: "SMB",
    3306: "MySQL",
    3389: "RDP",
    8080: "HTTP-Alt",
    9100: "Printer",
}

# Ports tried when sweeping a subnet for hosts that drop ICMP.
SCAN_PROBE_PORTS = [80, 443, 22, 445, 3389]

MAX_SCAN_HOSTS = 1024

_LATENCY_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)


def _float_env(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def parse_ports(value):
    """'80, 443,22' -> [22, 80, 443]. Raises ValueError on invalid input."""
    ports = set()
    for part in (value or "").replace(" ", "").split(","):
        if not part:
            continue
        if not part.isdigit() or not 1 <= int(part) <= 65535:
            raise ValueError(f"Port ไม่ถูกต้อง: {part}")
        ports.add(int(part))
    return sorted(ports)


def is_url(host):
    return host.lower().startswith(("http://", "https://"))


def hostname_of(host):
    """Strip scheme/path from a URL so it can be pinged or port-checked."""
    if is_url(host):
        return urlparse(host).hostname or host
    return host


def ping(host, timeout=None):
    """Send one ICMP echo request using the system `ping` command.

    Returns latency in ms, or None if the host did not answer
    (or `ping` is not available on this machine).
    """
    timeout = timeout or _float_env("PING_TIMEOUT_SECONDS", 2)
    if not shutil.which("ping"):
        return None

    if platform.system().lower() == "windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    elif platform.system().lower() == "darwin":
        cmd = ["ping", "-c", "1", "-W", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), host]

    start = time.perf_counter()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
    except (subprocess.TimeoutExpired, OSError):
        return None
    elapsed = (time.perf_counter() - start) * 1000

    # Windows returns 0 for "Destination host unreachable", so also require a TTL in the output.
    if result.returncode != 0 or "ttl=" not in result.stdout.lower():
        return None
    match = _LATENCY_RE.search(result.stdout)
    return round(float(match.group(1)), 2) if match else round(elapsed, 2)


def check_port(host, port, timeout=None):
    """Try a TCP connect. Returns connect time in ms if open, else None."""
    timeout = timeout or _float_env("PORT_TIMEOUT_SECONDS", 1.5)
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.perf_counter() - start) * 1000, 2)
    except OSError:
        return None


def check_ports(host, ports, timeout=None):
    """Check several ports in parallel. Returns {port: latency_ms_or_None}."""
    if not ports:
        return {}
    with ThreadPoolExecutor(max_workers=min(len(ports), 16)) as pool:
        results = pool.map(lambda p: check_port(host, p, timeout), ports)
    return dict(zip(ports, results))


def check_website(url, timeout=None):
    """HTTP GET a URL. Returns (status_code_or_None, response_time_ms_or_None, error)."""
    timeout = timeout or _float_env("HTTP_TIMEOUT_SECONDS", 5)
    if not is_url(url):
        url = f"http://{url}"
    start = time.perf_counter()
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True,
                            headers={"User-Agent": "NetworkMonitor/1.0"})
        return resp.status_code, round((time.perf_counter() - start) * 1000, 2), None
    except requests.RequestException as exc:
        return None, None, type(exc).__name__


def check_device(device):
    """Run every check for one device row and return a result dict.

    A device is online if it answers ping, or (when ICMP is blocked by a firewall)
    if at least one configured port accepts a connection. Websites are online when
    they return an HTTP status below 500.
    """
    host = device["host"]
    target = hostname_of(host)
    ports = parse_ports(device["ports"]) if device["ports"] else []
    http_status = None

    if device["device_type"] == "Website" or is_url(host):
        http_status, latency, _ = check_website(host)
        online = http_status is not None and http_status < 500
    else:
        latency = ping(target)
        online = latency is not None

    port_results = check_ports(target, ports)
    open_ports = [p for p, ms in port_results.items() if ms is not None]

    if not online and open_ports and http_status is None:
        online = True
        latency = min(port_results[p] for p in open_ports)

    return {
        "device_id": device["id"],
        "status": "online" if online else "offline",
        "latency_ms": latency if online else None,
        "open_ports": ",".join(str(p) for p in open_ports),
        "http_status": http_status,
    }


def _probe_host(ip):
    latency = ping(ip, timeout=1)
    open_ports = []
    if latency is None:
        for port in SCAN_PROBE_PORTS:
            ms = check_port(ip, port, timeout=0.5)
            if ms is not None:
                latency = ms
                open_ports.append(port)
                break
    if latency is None:
        return None
    try:
        hostname = socket.gethostbyaddr(ip)[0]
    except OSError:
        hostname = ""
    return {"ip": ip, "hostname": hostname, "latency_ms": latency, "open_ports": open_ports}


def scan_subnet(cidr, max_workers=64):
    """Find live hosts in a subnet such as '192.168.1.0/24'."""
    network = ipaddress.ip_network(cidr.strip(), strict=False)
    if network.version != 4:
        raise ValueError("รองรับเฉพาะ IPv4")
    hosts = [str(ip) for ip in network.hosts()] or [str(network.network_address)]
    if len(hosts) > MAX_SCAN_HOSTS:
        raise ValueError(f"Subnet ใหญ่เกินไป (สูงสุด {MAX_SCAN_HOSTS} hosts เช่น /22)")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        found = [r for r in pool.map(_probe_host, hosts) if r]
    return sorted(found, key=lambda r: ipaddress.ip_address(r["ip"]))
