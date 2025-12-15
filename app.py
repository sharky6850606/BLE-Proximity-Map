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
    if q:
        like = f"%{q}%"
        rows = conn.execute(
            """
            SELECT id, type, beacon_name, event_time, distance, created_at
            FROM notifications
            WHERE beacon_name LIKE ? OR type LIKE ? OR event_time LIKE ? OR created_at LIKE ?
            ORDER BY id DESC
            LIMIT 500
            """,
            (like, like, like, like),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, type, beacon_name, event_time, distance, created_at
            FROM notifications
            ORDER BY id DESC
            LIMIT 500
            """
        ).fetchall()
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

    if request.method == "POST":
        report_kind = (request.form.get("report_kind") or "beacon").strip()
        start_date = (request.form.get("start_date") or "").strip()
        end_date = (request.form.get("end_date") or "").strip()

        if report_kind == "device":
            device_ident = (request.form.get("device_ident") or "").strip()
            if device_ident:
                generate_device_activity_report(device_ident, start_date or None, end_date or None)
        else:
            beacon_name = (request.form.get("beacon_name") or "").strip()
            if beacon_name:
                generate_activity_report(beacon_name, start_date or None, end_date or None)

        return redirect(url_for("activity_reports"))

    # Distinct beacon names from notifications
    rows_beacons = conn.execute(
        "SELECT DISTINCT beacon_name FROM notifications WHERE beacon_name IS NOT NULL ORDER BY beacon_name"
    ).fetchall()
    beacon_names = [r[0] for r in rows_beacons if r[0]]

    # List of currently-known devices (from in-memory latest_messages)
    device_idents = sorted(
        ident for ident in latest_messages.keys() if ident != "DAILY_REPORT"
    )

    # Existing activity reports (beacon-level + device-level)
    rows_reports = conn.execute(
        "SELECT id, beacon_name, created_at, summary FROM activity_reports ORDER BY id DESC LIMIT 200"
    ).fetchall()
    conn.close()

    return render_template(
        "activity_reports.html",
        beacons=beacon_names,
        devices=device_idents,
        reports=rows_reports,
    )
@app.route("/timeline", methods=["GET"])
def beacon_timeline():
    """Per-beacon activity timeline page, built from notifications history."""
    beacon_name = (request.args.get("beacon") or "").strip()
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

    # Drop-down options
    rows_beacons = conn.execute(
        "SELECT DISTINCT beacon_name FROM notifications WHERE beacon_name IS NOT NULL ORDER BY beacon_name"
    ).fetchall()
    beacon_names = [r[0] for r in rows_beacons if r[0]]

    events = []
    if beacon_name:
        where_clauses = ["beacon_name = ?"]
        params = [beacon_name]

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
        beacons=beacon_names,
        selected_beacon=beacon_name,
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

    # Notifications data in the same window
    try:
        notif_rows = conn.execute(
            """
            SELECT beacon_name, type, event_time
            FROM notifications
            WHERE event_time >= ?
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

    for beacon_name, typ, event_time in notif_rows:
        name = beacon_name or "Unknown"
        beacon_activity[name] = beacon_activity.get(name, 0) + 1
        if typ == "in":
            beacon_in_counts[name] = beacon_in_counts.get(name, 0) + 1
        elif typ == "left":
            beacon_left_counts[name] = beacon_left_counts.get(name, 0) + 1

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

    # Top beacons by activity
    top_items = sorted(beacon_activity.items(), key=lambda x: x[1], reverse=True)[:8]
    beacon_labels = [name for name, _ in top_items]
    beacon_totals = [beacon_activity[name] for name in beacon_labels]
    beacon_ins = [beacon_in_counts.get(name, 0) for name in beacon_labels]
    beacon_lefts = [beacon_left_counts.get(name, 0) for name in beacon_labels]

    # Status breakdown
    total_status_points = sum(status_counts.values()) or 1
    status_breakdown = []
    for key, count in sorted(status_counts.items(), key=lambda x: x[0]):
        status_breakdown.append(
            {
                "status": key,
                "count": count,
                "percent": round(count * 100.0 / total_status_points, 1),
            }
        )

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