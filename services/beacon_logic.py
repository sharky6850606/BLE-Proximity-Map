from datetime import datetime, timedelta
import time

from config import SAMOA_OFFSET_HOURS, TTL_SECONDS, TX_POWER, PATH_LOSS_N

# Shared in-memory state
latest_messages = {}  # ident -> simplified device payload
beacon_state = {}     # (ident, beacon_id) -> beacon info



def voltage_to_percent(mv):
    """Convert beacon battery.voltage (mV) into percent 0-100."""
    if mv is None:
        return None
    try:
        v = float(mv) / 1000.0  # mV -> V
    except (TypeError, ValueError):
        return None
    pct = (v - 2.0) / 1.0  # 2.0V -> 0%, 3.0V -> 100%
    if pct < 0:
        pct = 0.0
    if pct > 1:
        pct = 1.0
    return round(pct * 100)


def format_samoa_time(ts):
    """Convert Unix timestamp (sec or ms) into Samoa local time string."""
    if ts is None:
        return "Never"
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return "Invalid time"
    # If timestamp is in milliseconds, convert to seconds
    if ts > 1_000_000_000_000:
        ts = ts / 1000.0
    dt_utc = datetime.utcfromtimestamp(ts)
    dt_samoa = dt_utc + timedelta(hours=SAMOA_OFFSET_HOURS)
    return dt_samoa.strftime("%Y-%m-%d %H:%M:%S")


def rssi_to_distance(rssi, tx_power=TX_POWER, n=PATH_LOSS_N):
    """Estimate distance in meters based on raw RSSI (no smoothing)."""
    if rssi is None:
        return None
    try:
        return round(10 ** ((tx_power - float(rssi)) / (10 * n)), 2)
    except Exception:
        return None


def _coerce_timestamp(ts_raw):
    """Convert various timestamp formats to float seconds (Unix epoch)."""
    if isinstance(ts_raw, (int, float)):
        ts = float(ts_raw)
    elif isinstance(ts_raw, str):
        try:
            ts = float(ts_raw)
        except ValueError:
            return time.time()
    else:
        return time.time()

    # If it's milliseconds, convert to seconds
    if ts > 1_000_000_000_000:
        ts = ts / 1000.0
    return ts


def simplify_message(msg):
    """Extract compact structure, apply TTL, use raw RSSI, and update beacon_state."""
    global latest_messages, beacon_state

    ident = msg.get("ident") or msg.get("device.id") or "unknown"

    ts_raw = msg.get("timestamp") or msg.get("server.timestamp") or time.time()
    ts = _coerce_timestamp(ts_raw)

    lat = msg.get("position.latitude")
    lon = msg.get("position.longitude")

    # BLE beacon data fields differ between firmwares
    raw_beacons = msg.get("ble.beacons") or msg.get("ble.beacons.list") or []

    now_ts = time.time()

    # Update beacon_state with any beacons in this message
    if isinstance(raw_beacons, list):
        for b in raw_beacons:
            bid = b.get("id") or b.get("uuid") or b.get("mac") or "unknown"
            rssi = b.get("rssi")
            dist = rssi_to_distance(rssi)
            key = (ident, bid)
            beacon_state[key] = {
                "id": bid,
                "device_ident": ident,
                "rssi": rssi,
                "distance": dist,
                "last_seen_raw": now_ts,
                "last_seen": format_samoa_time(now_ts),
                "battery_percent": voltage_to_percent(b.get("battery.voltage") or (b.get("battery") or {}).get("voltage")),
            }

    # TTL filtering: keep only fresh beacons for this device
    simple_beacons = []
    for (dev_id, bid), info in list(beacon_state.items()):
        last_seen_raw = info.get("last_seen_raw", 0)
        if now_ts - last_seen_raw > TTL_SECONDS:
            # Expire globally
            del beacon_state[(dev_id, bid)]
            continue
        if dev_id == ident:
            simple_beacons.append(info)

    return {
        "ident": ident,
        "timestamp_raw": ts,
        "timestamp": format_samoa_time(ts),
        "lat": lat,
        "lon": lon,
        "beacons": simple_beacons,
    }

def get_current_health():
    """Return a simple snapshot of system health: (active_devices, active_beacons).

    Devices are considered active if their last message timestamp is within TTL_SECONDS.
    Beacons are counted after applying TTL filtering on beacon_state.
    The special ident "DAILY_REPORT" is ignored when counting devices.
    """
    now_ts = time.time()

    # Count active devices
    active_devices = 0
    for ident, msg in list(latest_messages.items()):
        if ident == "DAILY_REPORT":
            continue
        ts_raw = msg.get("timestamp_raw")
        try:
            ts_val = float(ts_raw)
        except (TypeError, ValueError):
            # If we can't parse, treat as active (we know about this device)
            active_devices += 1
            continue
        # If timestamp looks like milliseconds, convert to seconds
        if ts_val > 1_000_000_000_000:
            ts_val = ts_val / 1000.0
        if now_ts - ts_val <= TTL_SECONDS:
            active_devices += 1

    # Count active beacons with TTL filtering
    active_beacons = 0
    for key, info in list(beacon_state.items()):
        last_seen_raw = info.get("last_seen_raw", 0)
        try:
            last_seen_val = float(last_seen_raw)
        except (TypeError, ValueError):
            continue
        if now_ts - last_seen_val > TTL_SECONDS:
            # Expire stale entries
            del beacon_state[key]
            continue
        active_beacons += 1

    return active_devices, active_beacons
