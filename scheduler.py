"""Background jobs: check every device on an interval and purge old logs."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

import database
import monitor
import notifier

log = logging.getLogger(__name__)

_scheduler = None


def check_and_record(device):
    """Check one device, store the result and alert if its status changed."""
    previous = database.get_last_log(device["id"])
    result = monitor.check_device(device)
    checked_at = database.now_str()
    database.add_check_log(
        device["id"], result["status"], result["latency_ms"],
        result["open_ports"], result["http_status"], checked_at,
    )
    # Alert only on a real transition; the very first check of a new device is not one.
    if previous is not None and previous["status"] != result["status"]:
        notifier.notify_status_change(device, result, checked_at)
    return result


def check_all_devices():
    devices = database.list_devices()
    if not devices:
        return []

    def safe_check(device):
        try:
            return check_and_record(device)
        except Exception:  # one bad device must not stop the whole round
            log.exception("Check failed for device %s", device["id"])
            return None

    with ThreadPoolExecutor(max_workers=min(len(devices), 32)) as pool:
        return [r for r in pool.map(safe_check, devices) if r]


def purge_logs():
    days = int(os.getenv("LOG_RETENTION_DAYS", "90"))
    if days > 0:
        deleted = database.purge_old_logs(days)
        if deleted:
            log.info("Purged %d check logs older than %d days", deleted, days)


def start():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    interval = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(check_all_devices, "interval", seconds=interval, id="check_all",
                       max_instances=1, coalesce=True,
                       next_run_time=datetime.now())  # first round runs immediately
    _scheduler.add_job(purge_logs, "interval", hours=24, id="purge_logs")
    _scheduler.start()
    log.info("Scheduler started: checking every %d seconds", interval)
    return _scheduler


def shutdown():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
