"""SQLite connection helpers, schema and queries."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    host        TEXT NOT NULL,
    device_type TEXT NOT NULL DEFAULT 'Server',
    location    TEXT DEFAULT '',
    ports       TEXT DEFAULT '',
    created_at  DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS check_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    status      TEXT NOT NULL CHECK (status IN ('online', 'offline')),
    latency_ms  REAL,
    open_ports  TEXT DEFAULT '',
    http_status INTEGER,
    checked_at  DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_check_logs_device_time
    ON check_logs (device_id, checked_at);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    DATETIME NOT NULL
);
"""

DEVICE_TYPES = ["Router", "Switch", "Server", "Printer", "Access Point", "Camera", "Website", "Other"]
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_str():
    return datetime.now().strftime(TIME_FORMAT)


def db_path():
    return os.getenv("DATABASE_PATH", "network_monitor.db")


def connect():
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode = WAL")
    ensure_admin_user()


def ensure_admin_user():
    """Create the initial admin account from env vars if no user exists yet."""
    username = os.getenv("ADMIN_USERNAME", "admin")
    password = os.getenv("ADMIN_PASSWORD", "admin")
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), now_str()),
        )


# ---------- users ----------

def get_user_by_username(username):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def get_user(user_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


# ---------- devices ----------

def list_devices():
    with get_db() as conn:
        return conn.execute("SELECT * FROM devices ORDER BY name COLLATE NOCASE").fetchall()


def get_device(device_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()


def device_exists(host):
    with get_db() as conn:
        return conn.execute("SELECT 1 FROM devices WHERE host = ?", (host,)).fetchone() is not None


def add_device(name, host, device_type, location="", ports=""):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO devices (name, host, device_type, location, ports, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, host, device_type, location, ports, now_str()),
        )
        return cur.lastrowid


def update_device(device_id, name, host, device_type, location="", ports=""):
    with get_db() as conn:
        conn.execute(
            "UPDATE devices SET name = ?, host = ?, device_type = ?, location = ?, ports = ? "
            "WHERE id = ?",
            (name, host, device_type, location, ports, device_id),
        )


def delete_device(device_id):
    with get_db() as conn:
        conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))


# ---------- check logs ----------

def add_check_log(device_id, status, latency_ms, open_ports, http_status=None, checked_at=None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO check_logs (device_id, status, latency_ms, open_ports, http_status, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (device_id, status, latency_ms, open_ports, http_status, checked_at or now_str()),
        )


def get_last_log(device_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM check_logs WHERE device_id = ? ORDER BY checked_at DESC, id DESC LIMIT 1",
            (device_id,),
        ).fetchone()


def get_devices_with_status():
    """Every device joined with its most recent check (if any)."""
    with get_db() as conn:
        return conn.execute(
            """
            SELECT d.*, l.status, l.latency_ms, l.open_ports, l.http_status, l.checked_at
            FROM devices d
            LEFT JOIN check_logs l ON l.id = (
                SELECT id FROM check_logs
                WHERE device_id = d.id
                ORDER BY checked_at DESC, id DESC LIMIT 1
            )
            ORDER BY d.name COLLATE NOCASE
            """
        ).fetchall()


def get_recent_logs(device_id, limit=50):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM check_logs WHERE device_id = ? ORDER BY checked_at DESC, id DESC LIMIT ?",
            (device_id, limit),
        ).fetchall()


def get_latency_series(device_id, hours=24):
    since = (datetime.now() - timedelta(hours=hours)).strftime(TIME_FORMAT)
    with get_db() as conn:
        return conn.execute(
            "SELECT checked_at, status, latency_ms FROM check_logs "
            "WHERE device_id = ? AND checked_at >= ? ORDER BY checked_at",
            (device_id, since),
        ).fetchall()


def get_daily_uptime(device_id, days=7):
    """Uptime % per day for the last `days` days (days without data are None)."""
    start = (datetime.now() - timedelta(days=days - 1)).date()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date(checked_at) AS day, COUNT(*) AS total, "
            "SUM(CASE WHEN status = 'online' THEN 1 ELSE 0 END) AS up "
            "FROM check_logs WHERE device_id = ? AND date(checked_at) >= ? "
            "GROUP BY day",
            (device_id, start.isoformat()),
        ).fetchall()
    by_day = {r["day"]: r for r in rows}
    result = []
    for i in range(days):
        day = (start + timedelta(days=i)).isoformat()
        row = by_day.get(day)
        uptime = round(row["up"] * 100.0 / row["total"], 2) if row else None
        result.append({"day": day, "uptime": uptime, "checks": row["total"] if row else 0})
    return result


def get_uptime(device_id, hours):
    """Uptime % over the last `hours` hours, or None if there is no data."""
    since = (datetime.now() - timedelta(hours=hours)).strftime(TIME_FORMAT)
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN status = 'online' THEN 1 ELSE 0 END) AS up "
            "FROM check_logs WHERE device_id = ? AND checked_at >= ?",
            (device_id, since),
        ).fetchone()
    if not row["total"]:
        return None
    return round(row["up"] * 100.0 / row["total"], 2)


def iter_logs_for_export(device_id=None, since=None):
    query = (
        "SELECT l.id, l.device_id, d.name AS device_name, d.host, d.device_type, d.location, "
        "l.status, l.latency_ms, l.open_ports, l.http_status, l.checked_at "
        "FROM check_logs l JOIN devices d ON d.id = l.device_id WHERE 1 = 1"
    )
    params = []
    if device_id:
        query += " AND l.device_id = ?"
        params.append(device_id)
    if since:
        query += " AND l.checked_at >= ?"
        params.append(since)
    query += " ORDER BY l.checked_at, l.id"
    conn = connect()
    try:
        yield from conn.execute(query, params)
    finally:
        conn.close()


def purge_old_logs(days):
    cutoff = (datetime.now() - timedelta(days=days)).strftime(TIME_FORMAT)
    with get_db() as conn:
        return conn.execute("DELETE FROM check_logs WHERE checked_at < ?", (cutoff,)).rowcount
