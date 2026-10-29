"""Send alerts to Discord Webhook and/or Telegram Bot when a device changes state."""

import logging
import os

import requests

log = logging.getLogger(__name__)


def _send_discord(message):
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return False
    try:
        resp = requests.post(url, json={"content": message}, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("Discord notification failed: %s", exc)
        return False


def _send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.warning("Telegram notification failed: %s", exc)
        return False


def send(message):
    """Send to every configured channel. Returns the list of channels that succeeded."""
    sent = []
    if _send_discord(message):
        sent.append("discord")
    if _send_telegram(message):
        sent.append("telegram")
    return sent


def format_status_change(device, result, checked_at):
    location = f" @ {device['location']}" if device["location"] else ""
    if result["status"] == "offline":
        return (
            f"🔴 OFFLINE: {device['name']} ({device['host']}){location}\n"
            f"ประเภท: {device['device_type']} | เวลา: {checked_at}"
        )
    latency = f"{result['latency_ms']} ms" if result["latency_ms"] is not None else "-"
    return (
        f"🟢 ONLINE: {device['name']} ({device['host']}){location} กลับมาใช้งานได้แล้ว\n"
        f"Latency: {latency} | เวลา: {checked_at}"
    )


def notify_status_change(device, result, checked_at):
    return send(format_status_change(device, result, checked_at))
