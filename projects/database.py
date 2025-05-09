# database.py
import sqlite3
import numpy as np
from datetime import datetime, timezone,timedelta, time as dt_time, date as dt_date
import os
import base64
import json # Needed if storing complex data as JSON

script_dir = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(script_dir, "access_control.db")
VN_TZ = timezone(timedelta(hours=7)) 

# --- Database Initialization ---
def initialize_database():
    """Creates/Updates the database schema, adding finger_position."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            # Updated Embeddings Table Schema with finger_position
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bio_id TEXT NOT NULL UNIQUE,      -- Use bioId from server
                    id_number TEXT,                   -- Store CCCD (idNumber)
                    person_name TEXT,                 -- Store person name if provided, else use id_number/bio_id
                    mac_address TEXT,                 -- MAC address this record applies to/came from
                    valid_from_date TEXT,             -- YYYY-MM-DD
                    valid_to_date TEXT,               -- YYYY-MM-DD
                    valid_from_time TEXT,             -- HH:MM:SS
                    valid_to_time TEXT,               -- HH:MM:SS
                    active_days_mask TEXT,            -- e.g., "1111110"
                    face_template BLOB,               -- Decoded base64 face template
                    face_image TEXT,                  -- Base64 face image
                    finger_template BLOB,             -- Decoded base64 finger template (Optional: Keep for backup?)
                    finger_image TEXT,                -- Base64 finger image (placeholder for now)
                    finger_position INTEGER UNIQUE,   -- << NEW: Position ID on the fingerprint sensor module
                    -- Add columns for other bio types if needed
                    added_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Add indexes for faster lookups
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_bio_id ON embeddings (bio_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_mac ON embeddings (mac_address)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_dates ON embeddings (valid_from_date, valid_to_date)")
            # Add index for finger_position if frequent lookups are needed
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_finger_pos ON embeddings (finger_position)")
            
                # outbox table for queued MQTT publishes
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic      TEXT    NOT NULL,
                    payload    TEXT    NOT NULL,
                    qos        INTEGER NOT NULL DEFAULT 0,
                    properties TEXT    DEFAULT NULL,      -- JSON-encoded list of UserProperty tuples
                    timestamp  DATETIME NOT NULL DEFAULT (DATETIME('now')),
                    sent       INTEGER NOT NULL DEFAULT 0  -- 0 = pending, 1 = sent
               )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_outbox_sent ON outbox (sent)")
            conn.commit()
            print("[DB] Database schema initialized/updated with finger_position.")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to initialize database schema: {e}")
        raise

# --- Data Processing from Server Push ---
def process_biometric_push(data, mac_address, finger_position=None):
    """
    Processes a single biometric command object from the server push.
    Handles PUSH_NEW_BIO and PUSH_UPDATE_BIO.
    Now includes finger_position obtained after enrolling on the sensor.
    Returns True on success, False on failure.
    """
    try:
        bio_id = data.get('bioId')
        id_number = data.get('idNumber')
        person_name = data.get('personName')
        bio_datas = data.get('bioDatas', [])
        from_date_str = data.get('fromDate') # Expect YYYY-MM-DD
        to_date_str = data.get('toDate')     # Expect YYYY-MM-DD
        from_time_str = data.get('fromTime') # Expect HH:MM:SS
        to_time_str = data.get('toTime')     # Expect HH:MM:SS
        active_days = data.get('activeDays') # Expect "1111110" like string

        if not bio_id:
            print("[DB WARN] Skipping push item: Missing 'bioId'.")
            return False

        # Use idNumber or bioId as person_name if not explicitly provided elsewhere
        #person_name = id_number if id_number else bio_id

        face_template_blob = None
        face_image_b64 = None
        finger_template_blob = None # Keep for DB storage if desired
        finger_image_b64 = None
        processed_finger_position = finger_position # Use the position passed in

        # Process bioDatas array
        for bio_data in bio_datas:
            bio_type = bio_data.get("BioType", "").upper()
            template_b64 = bio_data.get("Template")
            img_b64 = bio_data.get("Img")

            if bio_type == "FACE" and template_b64:
                try:
                    padding = '=' * (-len(template_b64) % 4)
                    face_template_blob = base64.b64decode(template_b64 + padding)
                    face_image_b64 = img_b64 # Store image as is
                except base64.binascii.Error as e:
                    print(f"[DB ERROR] Failed to decode FACE template B64 for bioId {bio_id}: {e}")
                except Exception as e:
                     print(f"[DB ERROR] Unexpected error processing FACE template B64 for bioId {bio_id}: {e}")

            elif bio_type == "FINGER" and template_b64:
                # The enrollment onto sensor and getting position happens *before* this function
                # We only store the template blob here if we want to keep it in DB
                try:
                    padding = '=' * (-len(template_b64) % 4)
                    # Store the raw template blob if needed for backup/restore
                    finger_template_blob = base64.b64decode(template_b64 + padding)
                    finger_image_b64 = img_b64
                except base64.binascii.Error as e:
                    print(f"[DB ERROR] Failed to decode FINGER template B64 for DB storage (bioId {bio_id}): {e}")
                    # Decide if this error should prevent DB entry
                except Exception as e:
                    print(f"[DB ERROR] Unexpected error processing FINGER template B64 for DB storage (bioId {bio_id}): {e}")
            # Add elif for other BioTypes

        # Insert or Update the record based on bio_id, including finger_position
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            if processed_finger_position is not None:
                cursor.execute(
                    "SELECT bio_id FROM embeddings WHERE finger_position = ?", 
                    (processed_finger_position,)
                )
                row = cursor.fetchone()
                if row and row[0] != bio_id:
                    # slot taken, find next free
                    from database import find_next_available_finger_position
                    new_pos = find_next_available_finger_position()
                    print(f"[DB] Position {processed_finger_position} taken, using {new_pos}")
                    processed_finger_position = new_pos
            cursor.execute("""
                INSERT INTO embeddings (
                    bio_id, id_number, person_name, mac_address,
                    valid_from_date, valid_to_date, valid_from_time, valid_to_time, active_days_mask,
                    face_template, face_image, finger_template, finger_image, finger_position
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bio_id) DO UPDATE SET
                    id_number=excluded.id_number,
                    person_name=excluded.person_name,
                    mac_address=excluded.mac_address,
                    valid_from_date=excluded.valid_from_date,
                    valid_to_date=excluded.valid_to_date,
                    valid_from_time=excluded.valid_from_time,
                    valid_to_time=excluded.valid_to_time,
                    active_days_mask=excluded.active_days_mask,
                    face_template=excluded.face_template,
                    face_image=excluded.face_image,
                    finger_template=excluded.finger_template, -- Update if keeping in DB
                    finger_image=excluded.finger_image,
                    finger_position=excluded.finger_position, -- Update position
                    added_timestamp=CURRENT_TIMESTAMP
            """, (
                bio_id, id_number, person_name, mac_address,
                from_date_str, to_date_str, from_time_str, to_time_str, active_days,
                face_template_blob, face_image_b64, finger_template_blob, finger_image_b64,
                processed_finger_position # Use the passed-in/updated position
            ))
            conn.commit()
            print(f"[DB] Processed PUSH_NEW/UPDATE for bioId '{bio_id}' (Finger Position: {processed_finger_position}).")
            return True

    except Exception as e:
        print(f"[DB ERROR] Failed to process biometric push for bioId '{data.get('bioId', 'N/A')}': {e}")
        return False

# --- Data Deletion ---
# Note: The actual deletion from the *sensor* should be triggered by mqtt.py
# before calling these DB deletion functions.

def delete_embedding_by_bio_id(bio_id):
    """Deletes embedding record(s) matching the bioId from the database."""
    deleted_count = 0
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            # We might need the position before deleting if mqtt.py didn't get it first
            # position = get_finger_position_by_bio_id(bio_id) # Optional pre-fetch
            cursor.execute("DELETE FROM embeddings WHERE bio_id = ?", (bio_id,))
            deleted_count = cursor.rowcount
            conn.commit()
            if deleted_count > 0:
                print(f"[DB] Deleted {deleted_count} DB records for bioId '{bio_id}'.")
            else:
                print(f"[DB] No DB records found for bioId '{bio_id}' to delete.")
            return True # Indicate success even if no rows deleted
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to delete DB records for bioId '{bio_id}': {e}")
        return False # Indicate failure

def delete_all_embeddings_for_mac(mac_address):
    """Deletes ALL embedding records associated with a specific MAC address from the database."""
    deleted_count = 0
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            # Optionally: Get all finger positions for this MAC before deleting if needed elsewhere
            cursor.execute("DELETE FROM embeddings WHERE mac_address = ?", (mac_address,))
            deleted_count = cursor.rowcount
            conn.commit()
            print(f"[DB] Deleted {deleted_count} DB records for MAC address '{mac_address}' due to SYNC_ALL.")
            return True
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to delete DB records for MAC '{mac_address}': {e}")
        return False

# --- Data Querying ---

def get_active_embeddings(mac_address):
    """
    Retrieves currently active FACE embeddings for the given MAC address.
    (Fingerprint verification now uses get_user_info_by_finger_position after sensor match).
    Returns list: [{'user_id': bio_id, 'person_name':, 'embedding_data': (NumPy array)}, ...]
    """
    results = []
    try:
        now = datetime.now(VN_TZ)
        current_date_str = now.strftime('%Y-%m-%d')
        current_time_str = now.strftime('%H:%M:%S')
        # Python's weekday(): Monday is 0 and Sunday is 6
        current_day_index = now.weekday() # 0-6

        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Select potential candidates for FACE recognition
            cursor.execute("""
                SELECT bio_id, person_name, face_template,
                       valid_from_date, valid_to_date,
                       valid_from_time, valid_to_time, active_days_mask
                FROM embeddings
                WHERE mac_address = ? AND face_template IS NOT NULL
            """, (mac_address,))

            rows = cursor.fetchall()
            #print(f"[DB DEBUG] Found {len(rows)} potential FACE records for MAC {mac_address}.")

            for row in rows:
                try:
                    # 1. Check Date Validity
                    if row['valid_from_date'] and current_date_str < row['valid_from_date']: continue
                    if row['valid_to_date'] and current_date_str > row['valid_to_date']: continue

                    # 2. Check Active Day Mask (e.g., "1111110")
                    mask = row['active_days_mask']
                    if not mask or len(mask) != 7 or mask[current_day_index] != '1': continue

                    # 3. Check Time Validity
                    if row['valid_from_time'] and current_time_str < row['valid_from_time']: continue
                    # Note: For end time, check if it's *less than* (exclusive)
                    if row['valid_to_time'] and current_time_str >= row['valid_to_time']: continue

                    # If all checks pass, process the FACE embedding
                    embedding_blob = row['face_template']
                    embedding_array = np.frombuffer(embedding_blob, dtype=np.float32)
                    # print(f"[DB DEBUG] Face Embedding shape for {row['bio_id']}: {embedding_array.shape}") # Reduce noise

                    # Assuming face embedding shape is 512
                    if embedding_array.size == 512: # Check size instead of shape for robustness
                         results.append({
                             'user_id': row['bio_id'], # Use bio_id as user_id key
                             'person_name': row['person_name'],
                             'embedding_data': embedding_array
                         })
                         #print(f"[DB DEBUG] Active face record added: {row['bio_id']}")
                    else:
                         print(f"[DB WARN] Skipping active face record {row['bio_id']} due to unexpected embedding size: {embedding_array.size}")

                except Exception as e:
                     print(f"[DB ERROR] Failed processing face record {row['bio_id']} during active check: {e}")

    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to get active face embeddings for MAC '{mac_address}': {e}")

    print(f"[DB] Found {len(results)} active FACE embeddings for current time for MAC {mac_address}.")
    return results


def retrieve_bio_image_by_user_id(user_id): # user_id here is the bio_id
    """Retrieves the Base64 encoded face image string for a given bio_id."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT face_image FROM embeddings
                WHERE bio_id = ? AND face_image IS NOT NULL
            """, (user_id,)) # Use bio_id for lookup
            result = cursor.fetchone()
            if result:
                return result[0]
            else:
                # print(f"[DB] No face image found for bio_id {user_id}.") # Can be noisy
                return None
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to retrieve face image for bio_id {user_id}: {e}")
        return None
    
def get_user_info_by_bio_id(bio_id):
    """Retrieves user validity info based on the bio_id."""
    if not bio_id: return None
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT bio_id, person_name,
                       valid_from_date, valid_to_date,
                       valid_from_time, valid_to_time, active_days_mask,
                       face_image, finger_image, id_number
                FROM embeddings
                WHERE bio_id = ?
            """, (bio_id,))
            row = cursor.fetchone()
            # print(f"[DB DEBUG] User info for bio_id {bio_id}: {dict(row) if row else None}")
            return row # Returns a Row object or None
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to retrieve user info for bio_id {bio_id}: {e}")
        return None

# --- NEW: Functions for Fingerprint Position ---
def get_user_info_by_finger_position(position):
    """Retrieves user validity info based on the sensor position."""
    if position is None: return None
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT bio_id, person_name,
                       valid_from_date, valid_to_date,
                       valid_from_time, valid_to_time, active_days_mask,
                       face_image, finger_image, id_number
                FROM embeddings
                WHERE finger_position = ?
            """, (position,))
            row = cursor.fetchone()
            # print(f"[DB DEBUG] User info for position {position}: {dict(row) if row else None}")
            return row # Returns a Row object or None
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to retrieve user info for finger position {position}: {e}")
        return None

def get_finger_position_by_bio_id(bio_id):
     """Retrieves the sensor position for a given bio_id."""
     if not bio_id: return None
     try:
         with sqlite3.connect(DB_FILE) as conn:
             cursor = conn.cursor()
             cursor.execute("SELECT finger_position FROM embeddings WHERE bio_id = ?", (bio_id,))
             result = cursor.fetchone()
             # print(f"[DB DEBUG] Finger position for bio_id {bio_id}: {result[0] if result else None}")
             # Ensure position is not None before returning
             return result[0] if result and result[0] is not None else None
     except sqlite3.Error as e:
         print(f"[DB ERROR] Failed to retrieve finger position for bio_id {bio_id}: {e}")
         return None
# --- END NEW ---


def retrieve_all_bio_records_for_display(mac_address=None):
     """ Retrieves records for display, adapted to the new schema. """
     records = []
     try:
         with sqlite3.connect(DB_FILE) as conn:
             cursor = conn.cursor()
             # Added finger_position to the select list
             query = """
                 SELECT id, bio_id, id_number, person_name,
                        valid_from_date, valid_to_date, valid_from_time, valid_to_time,
                        active_days_mask, face_image, finger_image, finger_position, mac_address
                 FROM embeddings
             """
             params = []
             if mac_address:
                 query += " WHERE mac_address = ?"
                 params.append(mac_address)
             query += " ORDER BY person_name, bio_id"
             cursor.execute(query, params)
             rows = cursor.fetchall()

             for row in rows:
                 # Map DB columns to a tuple structure for display
                 # Added finger_position (index 11) and adjusted subsequent indices
                 record_data = (
                     row[0],  # rec_id (PK from DB)
                     row[1],  # bio_id
                     row[2],  # id_number (used also for name display if person_name is null)
                     row[4],  # from_date
                     row[5],  # to_date
                     row[6],  # from_time
                     row[7],  # to_time
                     row[8],  # active_days
                     # Determine bio_type based on available data for display
                     "Face" if row[9] else ("Finger" if row[10] or row[11] is not None else "Unknown"),
                     row[1],  # template_base64_key (using bio_id as the key)
                     row[9] if row[9] else row[10], # img_base64 (show face if available, else finger)
                     row[12], # mac_addr
                     row[3],  # person_name (actual name)
                     row[11]  # finger_position
                 )
                 records.append(record_data)
         return records
     except sqlite3.Error as e:
         print(f"[DB ERROR] Failed to retrieve records for display: {e}")
         return []

def find_next_available_finger_position(max_position=300):
    """
    Finds the lowest available finger position ID in the database.

    :param max_position: The maximum position ID supported by the sensor.
    :return: The lowest available integer position ID, or None if all positions are full.
    """
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            # Get all currently used positions, ordered
            cursor.execute("""
                SELECT finger_position FROM embeddings
                WHERE finger_position IS NOT NULL
                ORDER BY finger_position ASC
            """)
            used_positions = {row[0] for row in cursor.fetchall()}

            # Iterate from 1 up to max_position to find the first unused ID
            for i in range(1, max_position + 1):
                if i not in used_positions:
                    print(f"[DB DEBUG] Found next available finger position: {i}")
                    return i

            # If loop completes, all positions are full
            print(f"[DB WARN] All finger positions up to {max_position} seem to be used.")
            return None
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to find next available finger position: {e}")
        return None

def delete_expired_guests():
     """ Placeholder: Review guest handling based on how they are stored now. """
     print("[DB] delete_expired_guests() needs review.")
     return 0

# Alias for clarity if needed elsewhere
def delete_records_by_bio_id(bio_id):
     return delete_embedding_by_bio_id(bio_id)


def enqueue_outgoing_message(topic: str, payload: str, qos: int = 0, properties: list[tuple[str,str]] | None = None):
    """Store an MQTT message (and its UserProperty) to send later."""
    props_json = json.dumps(properties) if properties else None
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT INTO outbox (topic, payload, qos, properties) VALUES (?, ?, ?, ?)",
            (topic, payload, qos, props_json)
        )
        conn.commit()

def get_pending_outbox():
    """
    Return list of pending messages:
      (id, topic, payload, qos, properties_json)
    """
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.execute(
            "SELECT id, topic, payload, qos, properties FROM outbox WHERE sent = 0 ORDER BY id"
        )
        return cur.fetchall()


def mark_outbox_sent(entry_id):
    """Mark a queued message as sent."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE outbox SET sent = 1 WHERE id = ?", (entry_id,))
        conn.commit()
