from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file
import os
import time

from database import init_db, get_db
from routes import map_bp, flespi_bp
from services.reporting_service import start_daily_beacon_check_thread, generate_activity_report, generate_device_activity_report
from services.beacon_logic import format_samoa_time, latest_messages


app = Flask(__name__)
app.register_blueprint(map_bp)
app.register_blueprint(flespi_bp)


def samoa_iso_now() -> str:
    """Return current Samoa local time in ISO-like format YYYY-MM-DDTHH:MM:SS."""
    # Reuse the same Samoa conversion used everywhere else
    return format_samoa_time(time.time()).replace(" ", "T")


def build_beacon_alias_map(conn):
    """Return a mapping so old IDs and renamed beacons share one display label.

    Keys include both the raw beacon id and any friendly name; values are the
    unified label like "FriendlyName – ID" (or just ID if no rename).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS beacon_names (id TEXT PRIMARY KEY, name TEXT)"
    )
    rows = conn.execute("SELECT id, name FROM beacon_names").fetchall()

    alias = {}
    for bid, bname in rows:
        if not bid:
            continue
        # Base label is either the friendly name or the id
        base_label = bname or bid
        if bname and bname != bid:
            label = f"{bname} – {bid}"
        else:
            label = bid
        # Map both the raw id and the plain friendly name (if present)
        alias[bid] = label
        if bname:
            alias[bname] = label

    return alias

# ---- API for saving notifications ----

@app.route("/api/notifications", methods=["POST"])
def save_notification():
    """
    Store a single notification event in the database.
    Expected JSON: { "type": "left"/"in", "name": "...", "time": "...", "distance": <number> }
    """
    data = request.get_json(silent=True) or {}
    ntype = data.get("type")
    name = data.get("name")
    event_time = data.get("time")
    distance = data.get("distance")

    if not ntype or not name:
        return jsonify({"status": "error", "message": "Invalid notification"}), 400

    created_at = samoa_iso_now()

    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            beacon_name TEXT,
            event_time TEXT,
            distance REAL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO notifications (type, beacon_name, event_time, distance, created_at) VALUES (?, ?, ?, ?, ?)",
        (ntype, name, event_time, distance, created_at),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"}), 201


# ---- Reports history & downloads ----

@app.route("/reports/history", methods=["GET"])
def reports_history():
    """
    Simple page showing daily_reports history.
    """
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            pdf_path TEXT,
            report_json TEXT,
            summary TEXT
        )
        """
    )
    rows = conn.execute(
        "SELECT id, created_at, summary FROM daily_reports ORDER BY id DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return render_template("reports_history.html", reports=rows)


@app.route("/notifications/history", methods=["GET"])
def notifications_history():
    """
    Page showing notifications history with a simple search bar.
    """
    q = (request.args.get("q") or "").strip()
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            beacon_name TEXT,
            event_time TEXT,
            distance REAL,
            created_at TEXT
        )
        """
    )

    # Map beacon ids and friendly names onto a single display label
    alias = build_beacon_alias_map(conn)

    if q:
        like = f"%{q}%"
        rows_raw = conn.execute(
            """
            SELECT id, type, beacon_name, event_time, distance, created_at
            FROM notifications
            WHERE type IN ('in', 'left')
              AND (beacon_name LIKE ? OR type LIKE ? OR event_time LIKE ? OR created_at LIKE ?)
            ORDER BY id DESC
            LIMIT 500
            """,
            (like, like, like, like),
        ).fetchall()
    else:
        rows_raw = conn.execute(
            """
            SELECT id, type, beacon_name, event_time, distance, created_at
            FROM notifications
            WHERE type IN ('in', 'left')
            ORDER BY id DESC
            LIMIT 500
            """
        ).fetchall()

    # Apply alias map so the same beacon (ID vs renamed) looks like one thing
    rows = []
    for rid, rtype, bname, event_time, distance, created_at in rows_raw:
        display_name = alias.get(bname, bname or "Unknown")
        rows.append((rid, rtype, display_name, event_time, distance, created_at))

    conn.close()
    return render_template("notifications_history.html", notifications=rows, query=q)
@app.route("/uptime", methods=["GET"])
def uptime_page():
    """
    Simple page showing recent system health snapshots from uptime_logs.
    """
    conn = get_db()
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
    rows = conn.execute(
        """
        SELECT id, timestamp, device_count, beacon_count, status
        FROM uptime_logs
        ORDER BY id DESC
        LIMIT 500
        """
    ).fetchall()
    conn.close()

    return render_template("uptime.html", logs=rows)



@app.route("/download/latest-report", methods=["GET"])
def download_latest_report():
    """
    Download the most recent daily report PDF.
    """
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            pdf_path TEXT,
            report_json TEXT,
            summary TEXT
        )
        """
    )
    row = conn.execute(
        "SELECT id, pdf_path FROM daily_reports ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if not row or not row[1] or not os.path.exists(row[1]):
        return "No reports available yet.", 404

    pdf_path = row[1]
    filename = os.path.basename(pdf_path)
    return send_file(pdf_path, as_attachment=True, download_name=filename)


@app.route("/download/report/<int:report_id>", methods=["GET"])
def download_report(report_id):
    """
    Download a specific report PDF by id.
    """
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            pdf_path TEXT,
            report_json TEXT,
            summary TEXT
        )
        """
    )
    row = conn.execute(
        "SELECT pdf_path FROM daily_reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    conn.close()

    if not row or not row[0] or not os.path.exists(row[0]):
        return "Report not found.", 404

    pdf_path = row[0]
    filename = os.path.basename(pdf_path)
    return send_file(pdf_path, as_attachment=True, download_name=filename)


# ---- Activity reports page ----


@app.route("/activity-reports", methods=["GET", 'POST'])  # noqa: E501
def activity_reports():
    """
    Page to generate and list activity reports for individual beacons or whole devices.
    """
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beacon_name TEXT,
            pdf_path TEXT,
            created_at TEXT,
            summary TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            beacon_name TEXT,
            event_time TEXT,
            distance REAL,
            created_at TEXT
        )
        """
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS beacon_names (
            id TEXT PRIMARY KEY,
            name TEXT
        )
        """
    )

    if request.method == "POST":
        report_kind = (request.form.get("report_kind") or "beacon").strip()
        start_date = (request.form.get("start_date") or "").strip()
        end_date = (request.form.get("end_date") or "").strip()

        if report_kind == "device":
            device_ident = (request.form.get("device_ident") or "").strip()
            if device_ident:
                generate_device_activity_report(device_ident, start_date or None, end_date or None)
        else:
            beacon_key = (request.form.get("beacon_id") or "").strip()
            if beacon_key:
                generate_activity_report(beacon_key, start_date or None, end_date or None)

        return redirect(url_for("activity_reports"))

    # ---- Build beacon dropdown with unified labels (ID + rename merged) ----
    rows_beacon_meta = conn.execute("SELECT id, name FROM beacon_names").fetchall()
    id_to_label = {}
    known_ids = set()
    known_names = set()
    for bid, bname in rows_beacon_meta:
        if not bid:
            continue
        known_ids.add(bid)
        if bname:
            known_names.add(bname)
        if bname and bname != bid:
            label = f"{bname} ({bid})"
        else:
            label = bid
        id_to_label[bid] = label

    rows_beacons = conn.execute(
        "SELECT DISTINCT beacon_name FROM notifications WHERE beacon_name IS NOT NULL ORDER BY beacon_name"
    ).fetchall()
    beacon_strings = [r[0] for r in rows_beacons if r[0]]

    beacon_options = []
    # First: every known beacon id with its combined label
    for bid in sorted(id_to_label.keys(), key=lambda x: id_to_label[x].lower()):
        beacon_options.append({"ident": bid, "label": id_to_label[bid]})

    # Then: any notification names that were never renamed at all
    for name in beacon_strings:
        if name in known_ids or name in known_names:
            continue
        beacon_options.append({"ident": name, "label": name})

    # ---- Build device dropdown options ----
    device_rows = conn.execute("SELECT id, name FROM devices").fetchall()
    device_options = []
    seen_device_ids = set()
    for did, dname in device_rows:
        if not did or did in seen_device_ids:
            continue
        seen_device_ids.add(did)
        label = f"{dname} ({did})" if dname and dname != did else did
        device_options.append({"ident": did, "label": label})

    # Include any in-memory devices that might not yet be in the devices table
    for ident in sorted(latest_messages.keys()):
        if ident == "DAILY_REPORT" or ident in seen_device_ids:
            continue
        device_options.append({"ident": ident, "label": ident})
        seen_device_ids.add(ident)

    # Existing activity reports history
    rows_reports = conn.execute(
        "SELECT id, beacon_name, created_at, summary FROM activity_reports ORDER BY id DESC LIMIT 200"
    ).fetchall()
    conn.close()

    return render_template(
        "activity_reports.html",
        beacons=beacon_options,
        devices=device_options,
        reports=rows_reports,
    )

@app.route("/timeline", methods=["GET"])
def beacon_timeline():
    """Per-beacon activity timeline page, built from notifications history."""
    beacon_key = (request.args.get("beacon") or "").strip()
    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()

    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            beacon_name TEXT,
            event_time TEXT,
            distance REAL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS beacon_names (
            id TEXT PRIMARY KEY,
            name TEXT
        )
        """
    )

    # Build beacon dropdown in the same way as activity_reports
    rows_beacon_meta = conn.execute("SELECT id, name FROM beacon_names").fetchall()
    id_to_label = {}
    known_ids = set()
    known_names = set()
    for bid, bname in rows_beacon_meta:
        if not bid:
            continue
        known_ids.add(bid)
        if bname:
            known_names.add(bname)
        if bname and bname != bid:
            label = f"{bname} ({bid})"
        else:
            label = bid
        id_to_label[bid] = label

    rows_beacons = conn.execute(
        "SELECT DISTINCT beacon_name FROM notifications WHERE beacon_name IS NOT NULL ORDER BY beacon_name"
    ).fetchall()
    beacon_strings = [r[0] for r in rows_beacons if r[0]]

    beacon_options = []
    for bid in sorted(id_to_label.keys(), key=lambda x: id_to_label[x].lower()):
        beacon_options.append({"ident": bid, "label": id_to_label[bid]})
    for name in beacon_strings:
        if name in known_ids or name in known_names:
            continue
        beacon_options.append({"ident": name, "label": name})

    # Work out which raw names in notifications should be treated as the selected beacon
    beacon_id = None
    friendly_name = None
    for bid, bname in rows_beacon_meta:
        if beacon_key == bid or (bname and beacon_key == bname):
            beacon_id = bid
            friendly_name = bname
            break

    names_to_match = []
    if beacon_id:
        names_to_match.append(beacon_id)
        if friendly_name and friendly_name != beacon_id:
            names_to_match.append(friendly_name)
    elif beacon_key:
        names_to_match.append(beacon_key)

    events = []
    if names_to_match:
        where_clauses = []
        params = []

        if len(names_to_match) == 1:
            where_clauses.append("beacon_name = ?")
            params.append(names_to_match[0])
        else:
            placeholders = ", ".join("?" for _ in names_to_match)
            where_clauses.append(f"beacon_name IN ({placeholders})")
            params.extend(names_to_match)

        if start_date:
            start_iso = f"{start_date} 00:00:00"
            where_clauses.append("REPLACE(event_time, 'T', ' ') >= ?")
            params.append(start_iso)
        if end_date:
            end_iso = f"{end_date} 23:59:59"
            where_clauses.append("REPLACE(event_time, 'T', ' ') <= ?")
            params.append(end_iso)

        sql = (
            "SELECT id, type, event_time, distance, created_at "
            "FROM notifications WHERE " + " AND ".join(where_clauses) +
            " ORDER BY event_time ASC, id ASC"
        )
        events = conn.execute(sql, params).fetchall()

    conn.close()
    return render_template(
        "timeline.html",
        beacons=beacon_options,
        selected_beacon=beacon_key,
        events=events,
        start_date=start_date,
        end_date=end_date,
    )

@app.route("/analytics", methods=["GET"])
def analytics_dashboard():
    """Analytics dashboard showing uptime, status breakdown, and beacon activity."""
    conn = get_db()

    # Define a rolling window (last 24 hours) for analytics
    now_ts = time.time()
    window_hours = 24
    window_start_ts = now_ts - window_hours * 3600
    # Use Samoa-local timestamps so they match what we store in the DB
    uptime_from = format_samoa_time(window_start_ts)
    notif_from = format_samoa_time(window_start_ts)

    # Uptime data
    try:
        uptime_rows = conn.execute(
            """
            SELECT timestamp, device_count, beacon_count, status
            FROM uptime_logs
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (uptime_from,),
        ).fetchall()
    except Exception:
        uptime_rows = []

    uptime_labels = []
    device_counts = []
    beacon_counts = []
    status_counts = {}
    for ts, devc, beac, status in uptime_rows:
        uptime_labels.append(ts)
        device_counts.append(devc or 0)
        beacon_counts.append(beac or 0)
        key = status or "UNKNOWN"
        status_counts[key] = status_counts.get(key, 0) + 1

    # Build status breakdown table (status, count, percent)
    total_status = sum(status_counts.values())
    status_breakdown = []
    if total_status > 0:
        for status_key, count in sorted(status_counts.items(), key=lambda kv: kv[0]):
            percent = round(count * 100.0 / total_status, 1)
            status_breakdown.append({
                'status': status_key,
                'count': count,
                'percent': percent,
            })
    else:
        status_breakdown = []

    # Notifications data in the same window
    try:
        notif_rows = conn.execute(
            """
            SELECT beacon_name, type, event_time
            FROM notifications
            WHERE event_time >= ?
              AND type IN ('in', 'left')
            ORDER BY event_time ASC
            """,
            (notif_from,),
        ).fetchall()

    except Exception:
        notif_rows = []

    conn.close()
    beacon_activity = {}
    beacon_in_counts = {}
    beacon_left_counts = {}
    hourly_buckets = {}  # hour label -> count
    presence_summary = []

    # Group events per beacon so we can also compute time-in-range metrics
    per_beacon_events = {}  # name -> list[(type, event_time)]

    for beacon_name, typ, event_time in notif_rows:
        name = beacon_name or "Unknown"
        beacon_activity[name] = beacon_activity.get(name, 0) + 1
        if typ == "in":
            beacon_in_counts[name] = beacon_in_counts.get(name, 0) + 1
        elif typ == "left":
            beacon_left_counts[name] = beacon_left_counts.get(name, 0) + 1

        # Track raw events for presence computation
        per_beacon_events.setdefault(name, []).append((typ, event_time or ""))

        # Hourly distribution for notifications
        event_time = (event_time or "").strip()
        if event_time:
            # Normalise to the "YYYY-MM-DD HH:MM:SS" pattern (older rows may contain a 'T')
            normalized = event_time.replace("T", " ")
            try:
                tm = time.strptime(normalized[:19], "%Y-%m-%d %H:%M:%S")
                hour_label = time.strftime("%H:00", tm)
                hourly_buckets[hour_label] = hourly_buckets.get(hour_label, 0) + 1
            except Exception:
                # Ignore rows with unexpected time format
                pass

    # Build per-beacon presence summary (in-range vs out-of-range %)
    if notif_rows:
        # Same 24h window we used for fetching notif_rows
        window_start_label = notif_from
        window_end_label = format_samoa_time(now_ts)
        for name, events in per_beacon_events.items():
            presence = compute_presence_stats(events, window_start_label, window_end_label)
            if presence:
                in_hours = presence["in_seconds"] / 3600.0
                out_hours = presence["out_seconds"] / 3600.0
                presence_summary.append({
                    "name": name,
                    "in_percent": presence["in_percent"],
                    "out_percent": presence["out_percent"],
                    "in_hours": round(in_hours, 1),
                    "out_hours": round(out_hours, 1),
                })

    # Sort presence summary by highest in-range percentage
    presence_summary.sort(key=lambda p: (-p["in_percent"], p["name"]))

    # Top beacons by total events for chart/table display
    if beacon_activity:
        sorted_beacons = sorted(beacon_activity.items(), key=lambda kv: (-kv[1], kv[0]))
        beacon_labels = [name for name, _ in sorted_beacons]
        beacon_totals = [beacon_activity[name] for name in beacon_labels]
        beacon_ins = [beacon_in_counts.get(name, 0) for name in beacon_labels]
        beacon_lefts = [beacon_left_counts.get(name, 0) for name in beacon_labels]
    else:
        beacon_labels = []
        beacon_totals = []
        beacon_ins = []
        beacon_lefts = []

    # Top beacons by total events for chart/table display

    # Hourly distribution for notifications
    hourly_labels = sorted(hourly_buckets.keys())
    hourly_counts = [hourly_buckets[h] for h in hourly_labels]

    # Latest uptime snapshot summary
    if uptime_rows:
        latest_ts, latest_devices, latest_beacons, latest_status = uptime_rows[-1]
        latest_devices = latest_devices or 0
        latest_beacons = latest_beacons or 0
    else:
        latest_ts = None
        latest_devices = 0
        latest_beacons = 0
        latest_status = "NO_DATA"

    total_events = len(notif_rows)

    return render_template(
        "analytics.html",
        window_hours=window_hours,
        uptime_labels=uptime_labels,
        device_counts=device_counts,
        beacon_counts=beacon_counts,
        status_breakdown=status_breakdown,
        hourly_labels=hourly_labels,
        hourly_counts=hourly_counts,
        beacon_labels=beacon_labels,
        beacon_totals=beacon_totals,
        beacon_ins=beacon_ins,
        beacon_lefts=beacon_lefts,
        presence_summary=presence_summary,
        latest_devices=latest_devices,
        latest_beacons=latest_beacons,
        latest_status=latest_status,
        latest_timestamp=latest_ts,
        total_events=total_events,
    )

@app.route("/download/activity-report/<int:report_id>", methods=["GET"])
def download_activity_report(report_id):
    """
    Download a specific activity report PDF by id.
    """
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            beacon_name TEXT,
            pdf_path TEXT,
            created_at TEXT,
            summary TEXT
        )
        """
    )
    row = conn.execute(
        "SELECT pdf_path FROM activity_reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    conn.close()

    if not row or not row[0] or not os.path.exists(row[0]):
        return "Activity report not found.", 404

    pdf_path = row[0]
    filename = os.path.basename(pdf_path)
    return send_file(pdf_path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    init_db()
    start_daily_beacon_check_thread()
    app.run(host="0.0.0.0", port=5000, debug=True)