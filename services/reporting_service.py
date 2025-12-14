import os
import time
import threading
import json

from database import get_db
from services.beacon_logic import latest_messages, format_samoa_time


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
    # Use Samoa-local time for all stored timestamps and filenames
    created_at_iso = format_samoa_time(now_ts).replace(" ", "T")

    reports_dir = ensure_reports_dir()
    # File name uses the same Samoa-local time, but formatted safely for a file path
    dt_label = format_samoa_time(now_ts)  # YYYY-MM-DD HH:MM:SS
    dt_for_file = dt_label.replace(":", "-").replace(" ", "_")
    filename = f"report_{dt_for_file}.pdf"
    pdf_path = os.path.join(reports_dir, filename)

    summary_text = generate_report_pdf(report, created_at_iso, pdf_path)
    save_daily_report_to_db(report, pdf_path, created_at_iso, summary_text)

    latest_messages["DAILY_REPORT"] = {
        "timestamp_raw": now_ts,
        "timestamp": format_samoa_time(now_ts),
        "lat": None,
        "lon": None,
        "beacons": [],
        "report": report,
    }


# ---- Activity report generation (per beacon, detailed) ----


def generate_activity_report(beacon_name, start_date=None, end_date=None):
    '''Generate a detailed activity PDF for a single beacon using notifications history.

    Optional start_date and end_date should be strings in YYYY-MM-DD format.
    If provided, the report will only include events whose event_time falls
    within that date range (inclusive). Times are interpreted in Samoa local time.
    Returns the PDF path or None if there is no data.
    '''
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.lib.pagesizes import A4 as _A4

    # Normalise date range if both provided and out of order
    if start_date and end_date and end_date < start_date:
        start_date, end_date = end_date, start_date

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

    # Build query with optional date filters on event_time.
    # We normalise both old 'YYYY-MM-DDTHH:MM:SS' and new 'YYYY-MM-DD HH:MM:SS'
    # formats by replacing 'T' with space for comparisons.
    where_clauses = ["beacon_name = ?"]
    params = [beacon_name]

    if start_date:
        start_cmp = f"{start_date} 00:00:00"
        where_clauses.append("REPLACE(event_time, 'T', ' ') >= ?")
        params.append(start_cmp)
    if end_date:
        end_cmp = f"{end_date} 23:59:59"
        where_clauses.append("REPLACE(event_time, 'T', ' ') <= ?")
        params.append(end_cmp)

    sql = (
        "SELECT type, event_time, distance, created_at FROM notifications "
        "WHERE " + " AND ".join(where_clauses) + " ORDER BY id ASC"
    )
    rows = conn.execute(sql, params).fetchall()

    if not rows:
        conn.close()
        return None

    now_ts = time.time()
    # Samoa-local timestamp for when this PDF was generated
    created_at_iso = format_samoa_time(now_ts).replace(" ", "T")

    act_dir = ensure_activity_reports_dir()
    safe_name = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in (beacon_name or "unknown")
    )
    dt_label = format_samoa_time(now_ts)  # YYYY-MM-DD HH:MM:SS
    dt_for_file = dt_label.replace(":", "-").replace(" ", "_")
    filename = f"activity_{safe_name}_{dt_for_file}.pdf"
    pdf_path = os.path.join(act_dir, filename)

    c = _canvas.Canvas(pdf_path, pagesize=_A4)
    width, height = _A4
    margin = 50
    y = height - margin

    # Modern header block
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, y, "Beacon Activity Report")
    y -= 20
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, f"Beacon: {beacon_name}")
    y -= 14
    c.setFont("Helvetica", 9)
    c.drawString(margin, y, f"Generated at: {created_at_iso.replace('T', ' ')} (Samoa time)")
    y -= 12

    # If a date range was provided, show it under the header
    range_parts = []
    if start_date:
        range_parts.append(f"from {start_date}")
    if end_date:
        if start_date:
            range_parts.append(f"to {end_date}")
        else:
            range_parts.append(f"up to {end_date}")
    if range_parts:
        c.drawString(margin, y, "Date range: " + " ".join(range_parts))
        y -= 12

    # Thin separator
    c.line(margin, y, width - margin, y)
    y -= 18

    # Summary row
    c.setFont("Helvetica-Bold", 11)
    total_events = len(rows)
    left_events = sum(1 for r in rows if r[0] == "left")
    in_events = sum(1 for r in rows if r[0] == "in")
    offline_events = sum(1 for r in rows if r[0] == "offline")
    online_events = sum(1 for r in rows if r[0] == "online")
    alert_events = sum(1 for r in rows if r[0] in ("distance", "signal"))

    summary_suffix = ""
    if start_date and end_date:
        summary_suffix = f" from {start_date} to {end_date}"
    elif start_date:
        summary_suffix = f" from {start_date} onwards"
    elif end_date:
        summary_suffix = f" up to {end_date}"

    summary = (
        f"{total_events} events: {in_events} IN, {left_events} LEFT, "
        f"{online_events} ONLINE, {offline_events} OFFLINE, {alert_events} alerts"
        f"{summary_suffix}"
    )
    c.drawString(margin, y, "Summary: " + summary)
    y -= 18

    # Table header
    c.setFont("Helvetica-Bold", 10)
    headers = ["Type", "Event time", "Distance (m)", "Recorded at"]
    col_x = [margin, margin + 80, margin + 260, margin + 360]
    for x, h in zip(col_x, headers):
        c.drawString(x, y, h)
    y -= 12
    c.setLineWidth(0.5)
    c.line(margin, y, width - margin, y)
    y -= 10

    # Table rows
    c.setFont("Helvetica", 9)
    for typ, event_time, distance, created_at in rows:
        if y < 60:
            c.showPage()
            y = height - margin
            c.setFont("Helvetica-Bold", 10)
            for x, h in zip(col_x, headers):
                c.drawString(x, y, h)
            y -= 12
            c.setLineWidth(0.5)
            c.line(margin, y, width - margin, y)
            y -= 10
            c.setFont("Helvetica", 9)

        event_display = (event_time or "-").replace("T", " ")
        created_display = (created_at or "-").replace("T", " ")
        c.drawString(col_x[0], y, (typ or "-").upper())
        c.drawString(col_x[1], y, event_display)
        c.drawString(col_x[2], y, f"{distance:.2f}" if distance is not None else "-")
        c.drawString(col_x[3], y, created_display)
        y -= 12

    c.showPage()
    c.save()

    # Save entry in activity_reports history for listing
    conn.execute(
        "INSERT INTO activity_reports (beacon_name, pdf_path, created_at, summary) VALUES (?, ?, ?, ?)",
        (beacon_name, pdf_path, created_at_iso, summary),
    )
    conn.commit()
    conn.close()
    return pdf_path

def generate_device_activity_report(device_ident, start_date=None, end_date=None):
    '''Generate a detailed activity PDF for a device, including all beacons attached to it
    (based on the current in-memory state) and their events from notifications history.

    device_ident: the internal FMC130 device identifier string.
    Optional start_date and end_date should be strings in YYYY-MM-DD format.
    Returns the PDF path or None if there is no data.
    '''
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.lib.pagesizes import A4 as _A4

    if not device_ident:
        return None

    # Normalise date range if both provided and out of order
    if start_date and end_date and end_date < start_date:
        start_date, end_date = end_date, start_date

    # Determine which beacons are currently attached to this device
    dev = latest_messages.get(device_ident)
    beacon_ids = []
    if isinstance(dev, dict):
        for b in dev.get("beacons") or []:
            bid = b.get("id")
            if bid and bid not in beacon_ids:
                beacon_ids.append(bid)

    if not beacon_ids:
        # No known beacons for this device
        return None

    conn = get_db()
    # Ensure core tables exist
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
        CREATE TABLE IF NOT EXISTS beacon_names (
            id TEXT PRIMARY KEY,
            name TEXT
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

    # Map beacon IDs -> human-readable names (fall back to ID)
    placeholders = ",".join("?" for _ in beacon_ids)
    id_to_name = {}
    if beacon_ids:
        rows = conn.execute(
            f"SELECT id, name FROM beacon_names WHERE id IN ({placeholders})",
            beacon_ids,
        ).fetchall()
        id_to_name = {row[0]: row[1] for row in rows}

    beacon_labels = []
    for bid in beacon_ids:
        label = id_to_name.get(bid) or bid
        if label and label not in beacon_labels:
            beacon_labels.append(label)

    if not beacon_labels:
        conn.close()
        return None

    # Resolve a display name for the device
    dev_row = conn.execute(
        "SELECT name FROM devices WHERE id = ?",
        (device_ident,),
    ).fetchone()
    device_display = dev_row[0] if dev_row and dev_row[0] else device_ident

    # Build notifications query
    where_clauses = [
        "beacon_name IN (" + ",".join("?" for _ in beacon_labels) + ")"
    ]
    params = list(beacon_labels)

    if start_date:
        start_cmp = f"{start_date} 00:00:00"
        where_clauses.append("REPLACE(event_time, 'T', ' ') >= ?")
        params.append(start_cmp)
    if end_date:
        end_cmp = f"{end_date} 23:59:59"
        where_clauses.append("REPLACE(event_time, 'T', ' ') <= ?")
        params.append(end_cmp)

    sql = (
        "SELECT beacon_name, type, event_time, distance, created_at "
        "FROM notifications WHERE "
        + " AND ".join(where_clauses)
        + " ORDER BY id ASC"
    )
    rows = conn.execute(sql, params).fetchall()

    if not rows:
        conn.close()
        return None

    now_ts = time.time()
    created_at_iso = format_samoa_time(now_ts).replace(" ", "T")

    act_dir = ensure_activity_reports_dir()
    safe_device = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in (device_display or device_ident)
    )
    dt_label = format_samoa_time(now_ts)  # YYYY-MM-DD HH:MM:SS
    dt_for_file = dt_label.replace(":", "-").replace(" ", "_")
    filename = f"device_{safe_device}_{dt_for_file}.pdf"
    pdf_path = os.path.join(act_dir, filename)

    c = _canvas.Canvas(pdf_path, pagesize=_A4)
    width, height = _A4
    margin = 50
    y = height - margin

    # Header
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, y, "Device Activity Report")
    y -= 20
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, y, f"Device: {device_display}")
    y -= 12
    c.setFont("Helvetica", 9)
    c.drawString(margin, y, f"Generated at: {created_at_iso.replace('T', ' ')} (Samoa time)")
    y -= 12

    beacons_line = ", ".join(sorted(set(beacon_labels)))
    c.drawString(
        margin,
        y,
        f"Beacons in report ({len(set(beacon_labels))}): {beacons_line}",
    )
    y -= 12

    # Date range line (if any)
    range_parts = []
    if start_date:
        range_parts.append(f"from {start_date}")
    if end_date:
        if start_date:
            range_parts.append(f"to {end_date}")
        else:
            range_parts.append(f"up to {end_date}")
    if range_parts:
        c.drawString(margin, y, "Date range: " + " ".join(range_parts))
        y -= 12

    # Separator
    c.line(margin, y, width - margin, y)
    y -= 18

    # Summary
    c.setFont("Helvetica-Bold", 11)
    total_events = len(rows)
    left_events = sum(1 for _, t, *_ in rows if t == "left")
    in_events = sum(1 for _, t, *_ in rows if t == "in")
    offline_events = sum(1 for _, t, *_ in rows if t == "offline")
    online_events = sum(1 for _, t, *_ in rows if t == "online")
    alert_events = sum(1 for _, t, *_ in rows if t in ("distance", "signal"))
    unique_beacons = len(set(r[0] for r in rows))

    summary_suffix = ""
    if start_date and end_date:
        summary_suffix = f" from {start_date} to {end_date}"
    elif start_date:
        summary_suffix = f" from {start_date} onwards"
    elif end_date:
        summary_suffix = f" up to {end_date}"

    summary = (
        f"{total_events} events across {unique_beacons} beacons: "
        f"{in_events} IN, {left_events} LEFT, "
        f"{online_events} ONLINE, {offline_events} OFFLINE, {alert_events} alerts"
        f"{summary_suffix}"
    )
    c.drawString(margin, y, "Summary: " + summary)
    y -= 18

    # Table header
    c.setFont("Helvetica-Bold", 10)
    headers = ["Beacon", "Type", "Event time", "Distance (m)", "Recorded at"]
    col_x = [margin, margin + 120, margin + 180, margin + 280, margin + 380]
    for x, h in zip(col_x, headers):
        c.drawString(x, y, h)
    y -= 12
    c.setLineWidth(0.5)
    c.line(margin, y, width - margin, y)
    y -= 10

    # Table rows
    c.setFont("Helvetica", 9)
    for beacon_label, typ, event_time, distance, created_at in rows:
        if y < 60:
            c.showPage()
            y = height - margin
            c.setFont("Helvetica-Bold", 10)
            for x, h in zip(col_x, headers):
                c.drawString(x, y, h)
            y -= 12
            c.setLineWidth(0.5)
            c.line(margin, y, width - margin, y)
            y -= 10
            c.setFont("Helvetica", 9)

        event_display = (event_time or "-").replace("T", " ")
        created_display = (created_at or "-").replace("T", " ")
        c.drawString(col_x[0], y, beacon_label or "-")
        c.drawString(col_x[1], y, (typ or "-").upper())
        c.drawString(col_x[2], y, event_display)
        c.drawString(col_x[3], y, f"{distance:.2f}" if distance is not None else "-")
        c.drawString(col_x[4], y, created_display)
        y -= 12

    c.showPage()
    c.save()

    # Save entry in the shared activity_reports table
    display_name = f"[Device] {device_display}"
    conn.execute(
        "INSERT INTO activity_reports (beacon_name, pdf_path, created_at, summary) VALUES (?, ?, ?, ?)",
        (display_name, pdf_path, created_at_iso, summary),
    )
    conn.commit()
    conn.close()
    return pdf_path

# ---- Background daily loop starter ----

def daily_beacon_check_loop():
    """Background loop that runs generate_daily_report once per day at 22:00 Samoa local time."""
    while True:
        try:
            now_ts = time.time()
            # Parse the Samoa-local clock using the same helper as everywhere else
            label = format_samoa_time(now_ts)  # YYYY-MM-DD HH:MM:SS
            hour = int(label[11:13])
            minute = int(label[14:16])
            if hour == 22 and minute == 0:
                generate_daily_report()
                # Avoid running multiple times within the same minute
                time.sleep(60)
            time.sleep(30)
        except Exception:
            # If anything goes wrong, wait a bit and try again instead of crashing the thread
            time.sleep(60)


def start_daily_beacon_check_thread():
    """
    Helper to start the daily check thread from app.py.
    """
    t = threading.Thread(target=daily_beacon_check_loop, daemon=True)
    t.start()
    return t