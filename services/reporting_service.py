import os
import time
import threading
import json

from database import get_db
from services.beacon_logic import latest_messages


# ---- Helpers for report storage dirs ----

def ensure_reports_dir():
    reports_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    reports_dir = os.path.abspath(reports_dir)
    os.makedirs(reports_dir, exist_ok=True)
    return reports_dir


def ensure_activity_reports_dir():
    act_dir = os.path.join(os.path.dirname(__file__), "..", "activity_reports")
    act_dir = os.path.abspath(act_dir)
    os.makedirs(act_dir, exist_ok=True)
    return act_dir


# ---- PDF generation helpers ----

def generate_report_pdf(report_entries, created_at_iso, pdf_path):
    """
    Create a styled PDF daily report.
    report_entries: list of dicts with keys id, name, status, last_seen, last_device, distance (optional)
    """
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.lib.pagesizes import A4 as _A4

    c = _canvas.Canvas(pdf_path, pagesize=_A4)
    width, height = _A4

    margin = 50
    y = height - margin

    # Header
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, y, "Daily Beacon Report")
    y -= 24
    c.setFont("Helvetica", 10)
    c.drawString(margin, y, f"Generated at: {created_at_iso}")
    y -= 10
    c.line(margin, y, width - margin, y)
    y -= 20

    # Summary
    total = len(report_entries)
    offline = sum(1 for r in report_entries if r.get("status") == "Offline")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, f"Summary: {total} beacons, {offline} offline.")
    y -= 18

    # Table header
    c.setFont("Helvetica-Bold", 10)
    headers = ["Beacon ID", "Name", "Status", "Last seen", "Last device"]
    col_x = [margin, margin + 120, margin + 260, margin + 360, margin + 480]
    for x, h in zip(col_x, headers):
        c.drawString(x, y, h)
    y -= 14
    c.line(margin, y, width - margin, y)
    y -= 12

    # Rows
    c.setFont("Helvetica", 9)
    for entry in report_entries:
        if y < 60:
            c.showPage()
            y = height - margin
            c.setFont("Helvetica-Bold", 10)
            for x, h in zip(col_x, headers):
                c.drawString(x, y, h)
            y -= 14
            c.line(margin, y, width - margin, y)
            y -= 12
            c.setFont("Helvetica", 9)

        c.drawString(col_x[0], y, str(entry.get("id")))
        c.drawString(col_x[1], y, str(entry.get("name") or "-"))
        c.drawString(col_x[2], y, str(entry.get("status")))
        c.drawString(col_x[3], y, str(entry.get("last_seen") or "-"))
        c.drawString(col_x[4], y, str(entry.get("last_device") or "-"))
        y -= 12

    c.showPage()
    c.save()

    summary_text = f"{total} beacons, {offline} offline"
    return summary_text


def save_daily_report_to_db(report_entries, pdf_path, created_at_iso, summary_text):
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
    conn.execute(
        "INSERT INTO daily_reports (created_at, pdf_path, report_json, summary) VALUES (?, ?, ?, ?)",
        (created_at_iso, pdf_path, json.dumps(report_entries), summary_text),
    )
    conn.commit()
    conn.close()


def get_last_daily_report_time():
    from datetime import datetime as _dt
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
        "SELECT created_at FROM daily_reports ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    try:
        return _dt.fromisoformat(row[0])
    except Exception:
        return None


# ---- Daily report generation (used by 22:00 loop) ----

def generate_daily_report():
    """
    Build daily report using all beacons in DB, store it in memory,
    save to SQLite, and generate a styled PDF file.
    """
    conn = get_db()
    conn.execute("CREATE TABLE IF NOT EXISTS beacon_names (id TEXT PRIMARY KEY, name TEXT)")
    rows = conn.execute("SELECT id, name FROM beacon_names").fetchall()
    conn.close()
    beacon_list = [(r[0], r[1]) for r in rows]

    report = []
    for bid, bname in beacon_list:
        # find last info in latest_messages
        last_seen = None
        distance = None
        device = None
        status = "Offline"

        for ident, dev in latest_messages.items():
            if ident == "DAILY_REPORT":
                continue
            if not isinstance(dev, dict):
                continue
            beacons = dev.get("beacons") or []
            for b in beacons:
                if b.get("id") == bid:
                    last_seen = b.get("last_seen")
                    distance = b.get("distance")
                    device = ident

        if last_seen:
            status = "Online"

        report.append(
            {
                "id": bid,
                "name": bname,
                "status": status,
                "last_seen": last_seen,
                "last_device": device,
                "distance": distance,
            }
        )

    now_ts = time.time()
    created_at_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now_ts))

    reports_dir = ensure_reports_dir()
    filename = f"report_{time.strftime('%Y-%m-%d_%H-%M-%S', time.localtime(now_ts))}.pdf"
    pdf_path = os.path.join(reports_dir, filename)

    summary_text = generate_report_pdf(report, created_at_iso, pdf_path)
    save_daily_report_to_db(report, pdf_path, created_at_iso, summary_text)

    latest_messages["DAILY_REPORT"] = {
        "timestamp_raw": now_ts,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts)),
        "lat": None,
        "lon": None,
        "beacons": [],
        "report": report,
    }


# ---- Activity report generation (per beacon, detailed) ----

def generate_activity_report(beacon_name):
    """
    Generate a detailed activity PDF for a single beacon using notifications history.
    Returns the PDF path or None if there is no data.
    """
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.lib.pagesizes import A4 as _A4

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
    rows = conn.execute(
        "SELECT type, event_time, distance, created_at FROM notifications WHERE beacon_name = ? ORDER BY id ASC",
        (beacon_name,),
    ).fetchall()

    if not rows:
        conn.close()
        return None

    now_ts = time.time()
    created_at_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now_ts))

    act_dir = ensure_activity_reports_dir()
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (beacon_name or "unknown"))
    filename = f"activity_{safe_name}_{time.strftime('%Y-%m-%d_%H-%M-%S', time.localtime(now_ts))}.pdf"
    pdf_path = os.path.join(act_dir, filename)

    c = _canvas.Canvas(pdf_path, pagesize=_A4)
    width, height = _A4
    margin = 50
    y = height - margin

    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, y, f"Beacon Activity Report: {beacon_name}")
    y -= 24
    c.setFont("Helvetica", 10)
    c.drawString(margin, y, f"Generated at: {created_at_iso}")
    y -= 10
    c.line(margin, y, width - margin, y)
    y -= 20

    c.setFont("Helvetica-Bold", 11)
    total_events = len(rows)
    left_events = sum(1 for r in rows if r[0] == "left")
    in_events = sum(1 for r in rows if r[0] == "in")
    c.drawString(margin, y, f"Summary: {total_events} events ({left_events} LEFT, {in_events} IN)")
    y -= 18

    c.setFont("Helvetica-Bold", 10)
    headers = ["Type", "Event time", "Distance (m)", "Recorded at"]
    col_x = [margin, margin + 80, margin + 260, margin + 360]
    for x, h in zip(col_x, headers):
        c.drawString(x, y, h)
    y -= 14
    c.line(margin, y, width - margin, y)
    y -= 12

    c.setFont("Helvetica", 9)
    for typ, event_time, distance, created_at in rows:
        if y < 60:
            c.showPage()
            y = height - margin
            c.setFont("Helvetica-Bold", 10)
            for x, h in zip(col_x, headers):
                c.drawString(x, y, h)
            y -= 14
            c.line(margin, y, width - margin, y)
            y -= 12
            c.setFont("Helvetica", 9)

        c.drawString(col_x[0], y, (typ or "-").upper())
        c.drawString(col_x[1], y, event_time or "-")
        c.drawString(col_x[2], y, f"{distance:.2f}" if distance is not None else "-")
        c.drawString(col_x[3], y, created_at or "-")
        y -= 12

    c.showPage()
    c.save()

    summary = f"{total_events} events ({left_events} LEFT, {in_events} IN)"
    conn.execute(
        "INSERT INTO activity_reports (beacon_name, pdf_path, created_at, summary) VALUES (?, ?, ?, ?)",
        (beacon_name, pdf_path, created_at_iso, summary),
    )
    conn.commit()
    conn.close()
    return pdf_path


# ---- Background daily loop starter ----

def daily_beacon_check_loop():
    """
    Background loop that runs generate_daily_report once per day at 22:00 local time.
    """
    while True:
        try:
            now = time.localtime()
            if now.tm_hour == 22 and now.tm_min == 0:
                generate_daily_report()
                time.sleep(60)
            time.sleep(30)
        except Exception:
            time.sleep(60)


def start_daily_beacon_check_thread():
    """
    Helper to start the daily check thread from app.py.
    """
    t = threading.Thread(target=daily_beacon_check_loop, daemon=True)
    t.start()
    return t
