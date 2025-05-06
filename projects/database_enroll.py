import sqlite3
import json
import os
import time # Import time for retry delay

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
                    timestamp  DATETIME NOT NULL DEFAULT (DATETIME('now')),
                    sent       INTEGER NOT NULL DEFAULT 0
               )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_outbox_sent ON outbox (sent)")
            conn.commit()
            print("[DB Enroll] Outbox database initialized.")
    except sqlite3.Error as e:
        print(f"[DB Enroll ERROR] Failed to initialize outbox database: {e}")
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
                print(f"[DB Enroll DEBUG] Enqueued message for topic: {topic}")
                return
        except sqlite3.OperationalError as e:
             if 'database is locked' in str(e):
                 print(f"[DB Enroll WARN] Database locked, retrying enqueue... ({retries} left)")
                 retries -= 1
                 time.sleep(0.2)
             else:
                 print(f"[DB Enroll ERROR] Failed to enqueue message: {e}")
                 raise
        except sqlite3.Error as e:
            print(f"[DB Enroll ERROR] Failed to enqueue message: {e}")
            raise
    print(f"[DB Enroll ERROR] Failed to enqueue message after retries (DB locked?).")

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
                print(f"[DB Enroll WARN] Database locked, retrying get_pending_outbox... ({retries} left)")
                retries -= 1
                time.sleep(0.2)
            else:
                 print(f"[DB Enroll ERROR] Failed to get pending outbox: {e}")
                 return []
        except sqlite3.Error as e:
            print(f"[DB Enroll ERROR] Failed to get pending outbox: {e}")
            return []
    print(f"[DB Enroll ERROR] Failed to get pending outbox after retries (DB locked?).")
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
                 print(f"[DB Enroll WARN] Database locked, retrying mark_outbox_sent... ({retries} left)")
                 retries -= 1
                 time.sleep(0.2)
             else:
                 print(f"[DB Enroll ERROR] Failed to mark outbox sent: {e}")
                 raise
        except sqlite3.Error as e:
            print(f"[DB Enroll ERROR] Failed to mark outbox sent: {e}")
            raise
    print(f"[DB Enroll ERROR] Failed to mark outbox sent {entry_id} after retries (DB locked?).")