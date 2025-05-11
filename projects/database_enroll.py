import sqlite3
import json
import os
import time
from datetime import datetime

script_dir = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(script_dir, "enrollment_outbox.db")

def initialize_database():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic      TEXT    NOT NULL,
                    payload    TEXT    NOT NULL,
                    qos        INTEGER NOT NULL DEFAULT 0,
                    properties TEXT    DEFAULT NULL,
                    timestamp  DATETIME NOT NULL DEFAULT (STRFTIME('%Y-%m-%d %H:%M:%f', 'now')),
                    sent       INTEGER NOT NULL DEFAULT 0
               )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_outbox_sent ON outbox (sent)")
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovered_devices (
                    room_name   TEXT PRIMARY KEY,
                    mac_address TEXT NOT NULL,
                    last_seen   DATETIME NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_discovered_mac ON discovered_devices (mac_address)")
            conn.commit()
            print("[DB Enroll] Databases (outbox, discovered_devices) initialized.")
    except sqlite3.Error as e:
        print(f"[DB Enroll ERROR] Failed to initialize database: {e}")
        raise

def enqueue_outgoing_message(topic: str, payload: str, qos: int = 0, properties: list[tuple[str,str]] | None = None):
    props_json = json.dumps(properties) if properties else None
    retries = 3
    while retries > 0:
        try:
            with sqlite3.connect(DB_FILE, timeout=10) as conn:
                conn.execute(
                    "INSERT INTO outbox (topic, payload, qos, properties) VALUES (?, ?, ?, ?)",
                    (topic, payload, qos, props_json)
                )
                conn.commit()
                return
        except sqlite3.OperationalError as e:
             if 'database is locked' in str(e):
                 retries -= 1
                 time.sleep(0.2)
             else:
                 print(f"[DB Enroll ERROR] Failed to enqueue message (OperationalError): {e}")
                 raise
        except sqlite3.Error as e:
            print(f"[DB Enroll ERROR] Failed to enqueue message (SQLite Error): {e}")
            raise
    print(f"[DB Enroll ERROR] Failed to enqueue message after retries.")

def get_pending_outbox() -> list:
    retries = 3
    while retries > 0:
        try:
            with sqlite3.connect(DB_FILE, timeout=10) as conn:
                cur = conn.execute(
                    "SELECT id, topic, payload, qos, properties FROM outbox WHERE sent = 0 ORDER BY id"
                )
                return cur.fetchall()
        except sqlite3.OperationalError as e:
            if 'database is locked' in str(e):
                retries -= 1
                time.sleep(0.2)
            else:
                 print(f"[DB Enroll ERROR] Failed to get pending outbox (OperationalError): {e}")
                 return []
        except sqlite3.Error as e:
            print(f"[DB Enroll ERROR] Failed to get pending outbox (SQLite Error): {e}")
            return []
    print(f"[DB Enroll ERROR] Failed to get pending outbox after retries.")
    return []

def mark_outbox_sent(entry_id: int):
    retries = 3
    while retries > 0:
        try:
            with sqlite3.connect(DB_FILE, timeout=10) as conn:
                conn.execute("UPDATE outbox SET sent = 1 WHERE id = ?", (entry_id,))
                conn.commit()
                return
        except sqlite3.OperationalError as e:
             if 'database is locked' in str(e):
                 retries -= 1
                 time.sleep(0.2)
             else:
                 print(f"[DB Enroll ERROR] Failed to mark outbox sent (OperationalError): {e}")
                 raise
        except sqlite3.Error as e:
            print(f"[DB Enroll ERROR] Failed to mark outbox sent (SQLite Error): {e}")
            raise
    print(f"[DB Enroll ERROR] Failed to mark outbox sent {entry_id} after retries.")

def update_discovered_device(room_name: str, mac_address: str):
    if not room_name or not mac_address:
        return
    last_seen_dt = datetime.now().isoformat(timespec='seconds')
    retries = 3
    while retries > 0:
        try:
            with sqlite3.connect(DB_FILE, timeout=10) as conn:
                conn.execute("""
                    INSERT INTO discovered_devices (room_name, mac_address, last_seen)
                    VALUES (?, ?, ?)
                    ON CONFLICT(room_name) DO UPDATE SET
                        mac_address = excluded.mac_address,
                        last_seen = excluded.last_seen
                """, (room_name, mac_address, last_seen_dt))
                conn.commit()
                return
        except sqlite3.OperationalError as e:
            if 'database is locked' in str(e):
                retries -= 1
                time.sleep(0.2)
            else:
                print(f"[DB Enroll ERROR] Failed to update discovered device (OperationalError): {e}")
                return 
        except sqlite3.Error as e:
            print(f"[DB Enroll ERROR] Failed to update discovered device (SQLite Error): {e}")
            return
    print(f"[DB Enroll ERROR] Failed to update discovered device {room_name} after retries.")


def get_all_discovered_devices() -> dict[str, str]:
    devices = {}
    retries = 3
    while retries > 0:
        try:
            with sqlite3.connect(DB_FILE, timeout=10) as conn:
                cur = conn.execute("SELECT room_name, mac_address FROM discovered_devices ORDER BY room_name")
                rows = cur.fetchall()
                for row in rows:
                    devices[row[0]] = row[1]
                return devices
        except sqlite3.OperationalError as e:
            if 'database is locked' in str(e):
                retries -= 1
                time.sleep(0.2)
            else:
                print(f"[DB Enroll ERROR] Failed to get discovered devices (OperationalError): {e}")
                return devices 
        except sqlite3.Error as e:
            print(f"[DB Enroll ERROR] Failed to get discovered devices (SQLite Error): {e}")
            return devices
    print(f"[DB Enroll ERROR] Failed to get discovered devices after retries.")
    return devices