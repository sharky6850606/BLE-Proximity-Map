from flask import Blueprint, request, jsonify, render_template, redirect, url_for

from database import get_db
from services.beacon_logic import latest_messages

map_bp = Blueprint("map", __name__)


@map_bp.route("/", methods=["GET"])
def root():
    """Redirect base URL to the main map page.""" 
    return redirect(url_for("map.map_page"))


@map_bp.route("/map", methods=["GET"])
def map_page():
    """Main map page.""" 
    return render_template("index.html")


def _ensure_tables(conn):
    # Beacon and device tables (names + colors)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS beacon_names (id TEXT PRIMARY KEY, name TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            name TEXT,
            color TEXT
        )
        """
    )


@map_bp.route("/data", methods=["GET"])
def map_data():
    """Return current devices + beacon names for the frontend.""" 

    # Snapshot so we don't hold the global dict too long
    snapshot = dict(latest_messages)

    conn = get_db()
    _ensure_tables(conn)

    # Load beacon names
    beacon_rows = conn.execute(
        "SELECT id, name FROM beacon_names"
    ).fetchall()
    beacon_names = {row[0]: row[1] for row in beacon_rows}

    # Load device metadata
    device_rows = conn.execute(
        "SELECT id, name, color FROM devices"
    ).fetchall()
    device_meta = {
        row[0]: {"name": row[1], "color": row[2]} for row in device_rows
    }

    # Color palette for devices
    palette = [
        "#3b82f6",  # blue
        "#10b981",  # green
        "#f59e0b",  # amber
        "#ef4444",  # red
        "#8b5cf6",  # violet
        "#ec4899",  # pink
        "#22c55e",  # emerald
        "#f97316",  # orange
        "#0ea5e9",  # sky
        "#a855f7",  # purple
    ]
    used_colors = {m["color"] for m in device_meta.values() if m.get("color")}

    def next_color():
        # Pick first unused color, then cycle
        for c in palette:
            if c not in used_colors:
                used_colors.add(c)
                return c
        if not palette:
            return "#3b82f6"
        idx = len(used_colors) % len(palette)
        c = palette[idx]
        used_colors.add(c)
        return c

    # Ensure every device has a row + color
    for ident, msg in snapshot.items():
        if ident == "DAILY_REPORT":
            continue
        if ident not in device_meta:
            color = next_color()
            conn.execute(
                "INSERT OR REPLACE INTO devices (id, name, color) VALUES (?, ?, ?)",
                (ident, None, color),
            )
            device_meta[ident] = {"name": None, "color": color}

    conn.commit()
    conn.close()

    devices_payload = []

    for ident, msg in snapshot.items():
        if ident == "DAILY_REPORT":
            devices_payload.append(msg)
            continue

        meta = device_meta.get(ident, {})
        devices_payload.append(
            {
                "ident": ident,
                "name": meta.get("name"),
                "color": meta.get("color"),
                "timestamp_raw": msg.get("timestamp_raw"),
                "timestamp": msg.get("timestamp"),
                "lat": msg.get("lat"),
                "lon": msg.get("lon"),
                "beacons": msg.get("beacons") or [],
            }
        )

    return jsonify(
        {
            "devices": devices_payload,
            "beacon_names": beacon_names,
        }
    )


@map_bp.route("/rename", methods=["POST"])
def rename_beacon():
    """Rename a beacon (stored in beacon_names table).""" 
    data = request.get_json(silent=True) or {}
    beacon_id = data.get("beacon_id")
    new_name = data.get("new_name")

    if not beacon_id or new_name is None:
        return jsonify({"status": "error", "message": "Invalid input"}), 400

    conn = get_db()
    _ensure_tables(conn)
    conn.execute(
        "INSERT OR REPLACE INTO beacon_names (id, name) VALUES (?, ?)",
        (beacon_id, new_name),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "ok"})


@map_bp.route("/rename_device", methods=["POST"])
def rename_device():
    """Rename a device and preserve its color.""" 
    data = request.get_json(silent=True) or {}
    device_id = data.get("device_id")
    new_name = data.get("new_name")

    if not device_id or new_name is None:
        return jsonify({"status": "error", "message": "Invalid input"}), 400

    conn = get_db()
    _ensure_tables(conn)

    row = conn.execute(
        "SELECT color FROM devices WHERE id = ?",
        (device_id,),
    ).fetchone()
    existing_color = row[0] if row and row[0] else None
    color = existing_color or "#3b82f6"

    conn.execute(
        "INSERT OR REPLACE INTO devices (id, name, color) VALUES (?, ?, ?)",
        (device_id, new_name, color),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "ok"})
