"""Network Monitoring Dashboard - Flask entry point."""

import csv
import io
import ipaddress
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, session, stream_with_context, url_for)
from werkzeug.security import check_password_hash

load_dotenv()

import database  # noqa: E402  (env must be loaded first)
import monitor  # noqa: E402
import notifier  # noqa: E402
import scheduler  # noqa: E402

HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.?)+$")


def validate_host(host):
    host = host.strip()
    if not host:
        raise ValueError("กรุณาระบุ IP Address หรือ Hostname")
    if monitor.is_url(host):
        if not monitor.hostname_of(host) or monitor.hostname_of(host) == host:
            raise ValueError("URL ไม่ถูกต้อง")
        return host
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if not HOSTNAME_RE.match(host):
        raise ValueError("IP Address หรือ Hostname ไม่ถูกต้อง")
    return host


def read_device_form(form):
    name = form.get("name", "").strip()
    if not name:
        raise ValueError("กรุณาระบุชื่ออุปกรณ์")
    host = validate_host(form.get("host", ""))
    device_type = form.get("device_type", "Other")
    if device_type not in database.DEVICE_TYPES:
        raise ValueError("ประเภทอุปกรณ์ไม่ถูกต้อง")
    ports = ",".join(str(p) for p in monitor.parse_ports(form.get("ports", "")))
    return {
        "name": name[:100],
        "host": host,
        "device_type": device_type,
        "location": form.get("location", "").strip()[:100],
        "ports": ports,
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify(error="unauthorized"), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def serialize_status(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "device_type": row["device_type"],
        "location": row["location"],
        "ports": row["ports"],
        "status": row["status"] or "unknown",
        "latency_ms": row["latency_ms"],
        "open_ports": row["open_ports"] or "",
        "http_status": row["http_status"],
        "checked_at": row["checked_at"],
    }


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY") or secrets.token_hex(32),
        START_SCHEDULER=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    if test_config:
        app.config.update(test_config)
        if "DATABASE_PATH" in test_config:
            os.environ["DATABASE_PATH"] = test_config["DATABASE_PATH"]

    database.init_db()

    # ---------- request hooks ----------

    @app.before_request
    def load_user():
        user_id = session.get("user_id")
        g.user = database.get_user(user_id) if user_id else None

    @app.before_request
    def csrf_protect():
        if request.method == "POST":
            token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
                abort(400, "CSRF token ไม่ถูกต้อง กรุณารีเฟรชหน้าแล้วลองใหม่")

    @app.context_processor
    def inject_globals():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return {
            "csrf_token": session["csrf_token"],
            "device_types": database.DEVICE_TYPES,
            "common_ports": monitor.COMMON_PORTS,
            "check_interval": int(os.getenv("CHECK_INTERVAL_SECONDS", "60")),
        }

    @app.template_filter("port_names")
    def port_names(value):
        if not value:
            return []
        return [(int(p), monitor.COMMON_PORTS.get(int(p), "")) for p in str(value).split(",") if p]

    # ---------- auth ----------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = database.get_user_by_username(request.form.get("username", "").strip())
            if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
                session.clear()
                session.permanent = True
                session["user_id"] = user["id"]
                next_url = request.args.get("next", "")
                # Only allow local redirects.
                if not next_url.startswith("/") or next_url.startswith("//"):
                    next_url = url_for("dashboard")
                return redirect(next_url)
            flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", "danger")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- pages ----------

    @app.route("/")
    @login_required
    def dashboard():
        devices = [serialize_status(r) for r in database.get_devices_with_status()]
        return render_template("dashboard.html", devices=devices)

    @app.route("/devices")
    @login_required
    def devices():
        return render_template("devices.html", devices=database.list_devices(), form={})

    @app.route("/devices/add", methods=["POST"])
    @login_required
    def add_device():
        try:
            data = read_device_form(request.form)
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("devices.html", devices=database.list_devices(),
                                   form=request.form), 400
        device_id = database.add_device(**data)
        flash(f"เพิ่มอุปกรณ์ “{data['name']}” แล้ว", "success")
        _check_now(device_id)
        return redirect(url_for("devices"))

    @app.route("/devices/<int:device_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_device(device_id):
        device = database.get_device(device_id) or abort(404)
        if request.method == "POST":
            try:
                data = read_device_form(request.form)
            except ValueError as exc:
                flash(str(exc), "danger")
                return render_template("device_edit.html", device=device, form=request.form), 400
            database.update_device(device_id, **data)
            flash("บันทึกการแก้ไขแล้ว", "success")
            return redirect(url_for("device_detail", device_id=device_id))
        return render_template("device_edit.html", device=device, form=device)

    @app.route("/devices/<int:device_id>/delete", methods=["POST"])
    @login_required
    def delete_device(device_id):
        device = database.get_device(device_id) or abort(404)
        database.delete_device(device_id)
        flash(f"ลบอุปกรณ์ “{device['name']}” แล้ว", "success")
        return redirect(url_for("devices"))

    @app.route("/devices/<int:device_id>")
    @login_required
    def device_detail(device_id):
        device = database.get_device(device_id) or abort(404)
        return render_template(
            "device_detail.html",
            device=device,
            last=database.get_last_log(device_id),
            uptime_24h=database.get_uptime(device_id, 24),
            uptime_7d=database.get_uptime(device_id, 24 * 7),
            uptime_30d=database.get_uptime(device_id, 24 * 30),
            logs=database.get_recent_logs(device_id, 50),
        )

    @app.route("/devices/<int:device_id>/check", methods=["POST"])
    @login_required
    def check_device_now(device_id):
        database.get_device(device_id) or abort(404)
        result = _check_now(device_id)
        if result:
            flash(f"ตรวจสอบแล้ว: {result['status'].upper()}", "info")
        return redirect(request.referrer or url_for("device_detail", device_id=device_id))

    @app.route("/check-all", methods=["POST"])
    @login_required
    def check_all():
        results = scheduler.check_all_devices()
        online = sum(1 for r in results if r["status"] == "online")
        flash(f"ตรวจสอบ {len(results)} อุปกรณ์: Online {online}, Offline {len(results) - online}", "info")
        return redirect(url_for("dashboard"))

    @app.route("/scan", methods=["GET", "POST"])
    @login_required
    def scan():
        results, cidr = None, request.form.get("cidr", "192.168.1.0/24").strip()
        if request.method == "POST":
            try:
                results = monitor.scan_subnet(cidr)
                for r in results:
                    r["known"] = database.device_exists(r["ip"])
            except ValueError as exc:
                flash(f"Subnet ไม่ถูกต้อง: {exc}", "danger")
        return render_template("scan.html", results=results, cidr=cidr)

    @app.route("/scan/add", methods=["POST"])
    @login_required
    def scan_add():
        added = 0
        for ip in request.form.getlist("ip"):
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                continue
            if database.device_exists(ip):
                continue
            name = request.form.get(f"name_{ip}", "").strip() or ip
            ports = request.form.get(f"ports_{ip}", "")
            database.add_device(name[:100], ip, "Other", "", ports)
            added += 1
        flash(f"เพิ่ม {added} อุปกรณ์จากการสแกน", "success")
        return redirect(url_for("devices"))

    @app.route("/notify/test", methods=["POST"])
    @login_required
    def notify_test():
        sent = notifier.send(f"✅ ทดสอบการแจ้งเตือนจาก Network Monitoring Dashboard ({database.now_str()})")
        if sent:
            flash(f"ส่งข้อความทดสอบไปที่ {', '.join(sent)} แล้ว", "success")
        else:
            flash("ยังไม่ได้ตั้งค่า DISCORD_WEBHOOK_URL หรือ TELEGRAM_BOT_TOKEN/CHAT_ID หรือส่งไม่สำเร็จ", "warning")
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/export.csv")
    @login_required
    def export_csv():
        device_id = request.args.get("device_id", type=int)
        days = request.args.get("days", type=int)
        since = (datetime.now() - timedelta(days=days)).strftime(database.TIME_FORMAT) if days else None
        columns = ["id", "device_id", "device_name", "host", "device_type", "location",
                   "status", "latency_ms", "open_ports", "http_status", "checked_at"]

        def generate():
            buf = io.StringIO()
            writer = csv.writer(buf)
            buf.write("﻿")  # BOM so Excel reads Thai text correctly
            writer.writerow(columns)
            for row in database.iter_logs_for_export(device_id, since):
                writer.writerow([row[c] for c in columns])
                if buf.tell() > 8192:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
            yield buf.getvalue()

        filename = f"check_logs_{device_id or 'all'}_{datetime.now():%Y%m%d_%H%M}.csv"
        return Response(stream_with_context(generate()), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    # ---------- JSON API ----------

    @app.route("/api/status")
    @login_required
    def api_status():
        devices = [serialize_status(r) for r in database.get_devices_with_status()]
        summary = {
            "total": len(devices),
            "online": sum(1 for d in devices if d["status"] == "online"),
            "offline": sum(1 for d in devices if d["status"] == "offline"),
            "unknown": sum(1 for d in devices if d["status"] == "unknown"),
        }
        return jsonify(summary=summary, devices=devices, server_time=database.now_str())

    @app.route("/api/devices/<int:device_id>/latency")
    @login_required
    def api_latency(device_id):
        database.get_device(device_id) or abort(404)
        hours = max(1, min(request.args.get("hours", 24, type=int), 24 * 30))
        rows = database.get_latency_series(device_id, hours)
        return jsonify([
            {"t": r["checked_at"], "latency_ms": r["latency_ms"], "status": r["status"]} for r in rows
        ])

    @app.route("/api/devices/<int:device_id>/uptime")
    @login_required
    def api_uptime(device_id):
        database.get_device(device_id) or abort(404)
        days = max(1, min(request.args.get("days", 7, type=int), 90))
        return jsonify(
            daily=database.get_daily_uptime(device_id, days),
            uptime_24h=database.get_uptime(device_id, 24),
            uptime_7d=database.get_uptime(device_id, 24 * 7),
        )

    def _check_now(device_id):
        device = database.get_device(device_id)
        try:
            return scheduler.check_and_record(device)
        except Exception:
            app.logger.exception("Immediate check failed for device %s", device_id)
            flash("ตรวจสอบอุปกรณ์ไม่สำเร็จ", "warning")
            return None

    if app.config["START_SCHEDULER"]:
        scheduler.start()

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    application = create_app()
    # use_reloader=False so the background scheduler is not started twice.
    application.run(host=os.getenv("FLASK_HOST", "127.0.0.1"),
                    port=int(os.getenv("FLASK_PORT", "5000")),
                    debug=os.getenv("FLASK_DEBUG") == "1", use_reloader=False)
