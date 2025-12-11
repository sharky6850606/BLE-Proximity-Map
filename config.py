import os

# Database path
DB_PATH = os.path.join(os.path.dirname(__file__), "beacons.db")

# Beacon TTL (seconds)
TTL_SECONDS = 360  # keep a beacon "alive" this long after last seen

# Samoa timezone offset
SAMOA_OFFSET_HOURS = 13  # UTC+13 for Samoa

# RSSI -> distance model
TX_POWER = -59
PATH_LOSS_N = 2.0
