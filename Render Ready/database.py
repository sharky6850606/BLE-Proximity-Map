import sqlite3
from config import DB_PATH

def get_db():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_db()
    # Beacon names table
    conn.execute("CREATE TABLE IF NOT EXISTS beacon_names (id TEXT PRIMARY KEY, name TEXT)")
    # Notifications history table
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
    # Daily reports table
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
    # Activity reports table
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
    conn.commit()
    conn.close()
