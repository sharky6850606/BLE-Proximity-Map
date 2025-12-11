import time

from database import get_db
from services.beacon_logic import get_current_health

# Simple in-memory throttle so we don't write to the DB too often
_last_log_ts = 0.0


def log_uptime_snapshot(min_interval_seconds: int = 60) -> None:
    """
    Log a single uptime snapshot into the uptime_logs table.

    This is expected to be called from the Flespi webhook whenever new
    telemetry arrives. It will record at most one row per
    `min_interval_seconds` to avoid spamming the database.
    """
    global _last_log_ts

    now = time.time()
    if now - _last_log_ts < float(min_interval_seconds):
        return

    active_devices, active_beacons = get_current_health()

    if active_devices == 0 and active_beacons == 0:
        status = "NO_DATA"
    elif active_devices == 0:
        status = "NO_DEVICES"
    elif active_beacons == 0:
        status = "NO_BEACONS"
    else:
        status = "OK"

    ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

    conn = get_db()
    # Ensure table exists (also created in init_db but safe to repeat)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS uptime_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            device_count INTEGER,
            beacon_count INTEGER,
            status TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO uptime_logs (timestamp, device_count, beacon_count, status) VALUES (?, ?, ?, ?)",
        (ts_str, active_devices, active_beacons, status),
    )
    conn.commit()
    conn.close()

    _last_log_ts = now
