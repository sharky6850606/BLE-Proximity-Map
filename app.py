from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file
import os
import time

from database import init_db, get_db
from routes import map_bp, flespi_bp
from services.reporting_service import start_daily_beacon_check_thread, generate_activity_report

app = Flask(__name__)
app.register_blueprint(map_bp)
app.register_blueprint(flespi_bp)


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

    created_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

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

@app.route("/activity-reports", methods=["GET", "POST"])  # noqa: E501
def activity_reports():
    """
    Page to generate and list beacon activity reports.
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
        beacon_name = (request.form.get("beacon_name") or "").strip()
        if beacon_name:
            generate_activity_report(beacon_name)
        return redirect(url_for("activity_reports"))

    # Distinct beacon names from notifications
    rows_beacons = conn.execute(
        "SELECT DISTINCT beacon_name FROM notifications WHERE beacon_name IS NOT NULL ORDER BY beacon_name"
    ).fetchall()
    beacon_names = [r[0] for r in rows_beacons if r[0]]

    # Existing activity reports
    rows_reports = conn.execute(
        "SELECT id, beacon_name, created_at, summary FROM activity_reports ORDER BY id DESC LIMIT 200"
    ).fetchall()
    conn.close()

    return render_template("activity_reports.html", beacons=beacon_names, reports=rows_reports)


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