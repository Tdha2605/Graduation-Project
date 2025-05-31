# database.py
import sqlite3
import numpy as np
from datetime import datetime, timezone, timedelta
import os
import base64
import json

script_dir = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(script_dir, "access_control.db")
VN_TZ = timezone(timedelta(hours=7))

def initialize_database():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bio_id TEXT NOT NULL UNIQUE,
                    id_number TEXT,
                    person_name TEXT,
                    mac_address TEXT,
                    valid_from_date TEXT,
                    valid_to_date TEXT,
                    valid_from_time TEXT,
                    valid_to_time TEXT,
                    active_days_mask TEXT,
                    face_template BLOB,
                    face_image TEXT,
                    finger_template BLOB,
                    finger_image TEXT,
                    finger_position INTEGER UNIQUE,
                    idcard_uid TEXT UNIQUE,
                    bio_type TEXT,
                    added_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("PRAGMA table_info(embeddings)")
            columns = [info[1] for info in cursor.fetchall()]
            if 'bio_type' not in columns:
                cursor.execute("ALTER TABLE embeddings ADD COLUMN bio_type TEXT")
            if 'idcard_uid' not in columns:
                cursor.execute("ALTER TABLE embeddings ADD COLUMN idcard_uid TEXT UNIQUE")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_bio_id ON embeddings (bio_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_mac ON embeddings (mac_address)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_finger_pos ON embeddings (finger_position)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_idcard_uid ON embeddings (idcard_uid)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    qos INTEGER NOT NULL DEFAULT 0,
                    properties TEXT DEFAULT NULL,
                    timestamp DATETIME NOT NULL DEFAULT (DATETIME('now')),
                    sent INTEGER NOT NULL DEFAULT 0
               )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_outbox_sent ON outbox (sent)")
            conn.commit()
            print("[DB] Database schema initialized/updated.")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to initialize database schema: {e}")
        raise

def process_biometric_push(data, mac_address, finger_position_from_sensor=None):
    try:
        bio_id_int = data.get('BioId') # This is an integer
        if bio_id_int is None:
            print("[DB WARN] Skipping push item: Missing 'BioId'.")
            return False
        
        bio_id_str = str(bio_id_int) # Convert to string for DB operations if column is TEXT

        id_number = data.get('IdNumber')
        person_name = data.get('PersonName')
        from_date_str = data.get('FromDate')
        to_date_str = data.get('ToDate')
        from_time_str = data.get('FromTime')
        to_time_str = data.get('ToTime')
        active_days = data.get('ActiveDays')

        face_templates_b64 = data.get("FaceTemps", [])
        face_image_b64 = data.get("FaceImg")
        finger_templates_b64 = data.get("FingerTemps", [])
        idcard_uids_from_iris = data.get("IrisTemps", [])

        face_template_to_save = None
        face_image_to_save = None
        finger_template_to_save = None
        finger_image_to_save = None
        idcard_uid_to_save = None
        finger_position_to_save = finger_position_from_sensor

        list_of_bio_types_in_push = []

        if face_templates_b64:
            first_face_template_b64 = face_templates_b64[0]
            if first_face_template_b64:
                list_of_bio_types_in_push.append("FACE")
                try:
                    padding = '=' * (-len(first_face_template_b64) % 4)
                    face_template_to_save = base64.b64decode(first_face_template_b64 + padding)
                    face_image_to_save = face_image_b64
                except Exception as e:
                    print(f"[DB ERROR] Processing FACE data for BioId {bio_id_str}: {e}")

        if finger_templates_b64:
            first_finger_template_b64 = finger_templates_b64[0]
            if first_finger_template_b64:
                list_of_bio_types_in_push.append("FINGER")
                try:
                    padding = '=' * (-len(first_finger_template_b64) % 4)
                    finger_template_to_save = base64.b64decode(first_finger_template_b64 + padding)
                except Exception as e:
                    print(f"[DB ERROR] Processing FINGER data for BioId {bio_id_str}: {e}")

        if idcard_uids_from_iris:
            first_idcard_uid = idcard_uids_from_iris[0]
            if first_idcard_uid:
                list_of_bio_types_in_push.append("IDCARD")
                idcard_uid_to_save = first_idcard_uid.strip().upper()

        main_bio_type_to_store = ",".join(sorted(list(set(list_of_bio_types_in_push))))
        has_biometric_data = bool(face_templates_b64 or finger_templates_b64 or idcard_uids_from_iris)

        if not main_bio_type_to_store and has_biometric_data:
             print(f"[DB WARN] Could not determine valid BioType for BioId {bio_id_str} from biometric fields.")
        elif not has_biometric_data:
            print(f"[DB INFO] No biometric data for BioId {bio_id_str}. Will attempt user info update if record exists.")

        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()

            if "FINGER" in list_of_bio_types_in_push and finger_position_to_save is not None:
                cursor.execute(
                    "SELECT bio_id FROM embeddings WHERE finger_position = ? AND bio_id != ?",
                    (finger_position_to_save, bio_id_str)
                )
                row = cursor.fetchone()
                if row:
                    new_pos = find_next_available_finger_position()
                    if new_pos is not None:
                        finger_position_to_save = new_pos
                    else:
                        print(f"[DB ERROR] No available finger position for BioId {bio_id_str}.")
                        return False
            elif "FINGER" not in list_of_bio_types_in_push:
                if finger_templates_b64 is None or not finger_templates_b64:
                     finger_position_to_save = None
                     finger_template_to_save = None

            if "IDCARD" in list_of_bio_types_in_push and idcard_uid_to_save is not None:
                 cursor.execute(
                    "SELECT bio_id FROM embeddings WHERE idcard_uid = ? AND bio_id != ?",
                    (idcard_uid_to_save, bio_id_str)
                 )
                 row = cursor.fetchone()
                 if row:
                      print(f"[DB ERROR] IDCARD UID {idcard_uid_to_save} already assigned to bioId {row[0]}.")
                      return False
            elif "IDCARD" not in list_of_bio_types_in_push:
                if idcard_uids_from_iris is None or not idcard_uids_from_iris:
                    idcard_uid_to_save = None
            
            if not main_bio_type_to_store and has_biometric_data:
                cursor.execute("SELECT 1 FROM embeddings WHERE bio_id = ?", (bio_id_str,))
                if not cursor.fetchone():
                    print(f"[DB WARN] BioId {bio_id_str} does not exist and no valid new BioType provided. Aborting insert.")
                    return False

            cursor.execute("""
                INSERT INTO embeddings (
                    bio_id, id_number, person_name, mac_address,
                    valid_from_date, valid_to_date, valid_from_time, valid_to_time, active_days_mask,
                    face_template, face_image,
                    finger_template, finger_image, finger_position,
                    idcard_uid, bio_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bio_id) DO UPDATE SET
                    id_number=excluded.id_number,
                    person_name=excluded.person_name,
                    mac_address=excluded.mac_address,
                    valid_from_date=excluded.valid_from_date,
                    valid_to_date=excluded.valid_to_date,
                    valid_from_time=excluded.valid_from_time,
                    valid_to_time=excluded.valid_to_time,
                    active_days_mask=excluded.active_days_mask,
                    face_template=COALESCE(excluded.face_template, embeddings.face_template),
                    face_image=COALESCE(excluded.face_image, embeddings.face_image),
                    finger_template=COALESCE(excluded.finger_template, embeddings.finger_template),
                    finger_image=COALESCE(excluded.finger_image, embeddings.finger_image),
                    finger_position=COALESCE(excluded.finger_position, embeddings.finger_position),
                    idcard_uid=COALESCE(excluded.idcard_uid, embeddings.idcard_uid),
                    bio_type=CASE
                                 WHEN excluded.bio_type IS NOT NULL AND excluded.bio_type != '' THEN excluded.bio_type
                                 ELSE embeddings.bio_type
                             END,
                    added_timestamp=CURRENT_TIMESTAMP
            """, (
                bio_id_str, id_number, person_name, mac_address,
                from_date_str, to_date_str, from_time_str, to_time_str, active_days,
                face_template_to_save, face_image_to_save,
                finger_template_to_save, finger_image_to_save, finger_position_to_save,
                idcard_uid_to_save, main_bio_type_to_store
            ))
            conn.commit()
            print(f"[DB] Processed PUSH for BioId '{bio_id_str}'.")
            return True

    except Exception as e:
        print(f"[DB ERROR] Failed to process biometric push for BioId '{data.get('BioId', 'N/A')}': {e}")
        import traceback
        traceback.print_exc()
        return False

def delete_biometrics_and_access_for_bio_id(bio_id_int, mac_address, delete_globally=False):
    deleted_count = 0
    bio_id_str = str(bio_id_int)
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            if delete_globally:
                 cursor.execute("DELETE FROM embeddings WHERE bio_id = ?", (bio_id_str,))
            else:
                 cursor.execute("DELETE FROM embeddings WHERE bio_id = ? AND mac_address = ?", (bio_id_str, mac_address))
            deleted_count = cursor.rowcount
            conn.commit()
            if deleted_count > 0:
                print(f"[DB] Deleted {deleted_count} DB records for BioId '{bio_id_str}'.")
            else:
                print(f"[DB] No DB records found for BioId '{bio_id_str}' to delete.")
            return True
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to delete DB records for BioId '{bio_id_str}': {e}")
        return False

def delete_all_biometrics_and_access_for_mac(mac_address):
    deleted_count = 0
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM embeddings WHERE mac_address = ?", (mac_address,))
            deleted_count = cursor.rowcount
            conn.commit()
            print(f"[DB] SYNC_ALL: Deleted {deleted_count} DB records for MAC '{mac_address}'.")
            return True
    except sqlite3.Error as e:
        print(f"[DB ERROR] SYNC_ALL: Failed to delete DB records for MAC '{mac_address}': {e}")
        return False

def get_user_by_bio_type_and_template(bio_type, template_data, mac_address):
    if not bio_type or not template_data or not mac_address:
        return None
    
    query_field = None
    template_data_for_query = template_data

    if bio_type.upper() == "FACE":
        query_field = "bio_id"
        template_data_for_query = str(template_data) # Assuming template_data from FR is the bio_id (as int or str)
    elif bio_type.upper() == "FINGER":
        query_field = "finger_position"
        try:
            template_data_for_query = int(template_data)
        except ValueError:
            print(f"[DB ERROR] Invalid finger position (not int): {template_data}")
            return None
    elif bio_type.upper() == "IDCARD":
        query_field = "idcard_uid"
        template_data_for_query = template_data.upper()
    else:
        return None

    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row 
            cursor = conn.cursor()
            sql_query = f"""
                SELECT bio_id, id_number, person_name, face_image, finger_image,
                       valid_from_date, valid_to_date, 
                       valid_from_time, valid_to_time, active_days_mask
                FROM embeddings
                WHERE {query_field} = ? AND mac_address = ? 
                      AND (bio_type LIKE ? OR bio_type = ?) 
            """
            like_bio_type_param = f"%{bio_type.upper()}%"
            cursor.execute(sql_query, (template_data_for_query, mac_address, like_bio_type_param, bio_type.upper()))
            return cursor.fetchone()
    except sqlite3.Error as e:
        print(f"[DB ERROR] Getting user by {bio_type} template {template_data_for_query}: {e}")
        return None

def is_user_access_valid_now(bio_id_int, mac_address):
    user_record = get_user_info_by_bio_id(bio_id_int)
    if not user_record:
        return False
    
    if user_record['mac_address'] != mac_address:
        return False

    try:
        now = datetime.now(VN_TZ)
        current_date_str = now.strftime('%Y-%m-%d')
        current_time_str = now.strftime('%H:%M:%S')
        current_day_index = now.weekday()

        if user_record['valid_from_date'] and current_date_str < user_record['valid_from_date']: return False
        if user_record['valid_to_date'] and current_date_str > user_record['valid_to_date']: return False
        
        mask = user_record['active_days_mask']
        if not mask or not (len(mask) == 6 or len(mask) == 7): return False
        if len(mask) == 7 and mask[current_day_index] != '1': return False
        if len(mask) == 6:
            if current_day_index == 6: return False 
            if mask[current_day_index] != '1': return False
        
        if user_record['valid_from_time'] and current_time_str < user_record['valid_from_time']: return False
        if user_record['valid_to_time'] and current_time_str >= user_record['valid_to_time']: return False
        
        return True
    except KeyError as ke:
        print(f"[DB ERROR] is_user_access_valid_now: Missing key '{ke}' for BioId {bio_id_int}")
        return False
    except Exception as e:
        print(f"[DB ERROR] is_user_access_valid_now: Exception for BioId {bio_id_int}: {e}")
        return False

def get_active_embeddings(mac_address):
    results = []
    try:
        now = datetime.now(VN_TZ)
        current_date_str = now.strftime('%Y-%m-%d')
        current_time_str = now.strftime('%H:%M:%S')
        current_day_index = now.weekday()

        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT bio_id, person_name, face_template,
                       valid_from_date, valid_to_date,
                       valid_from_time, valid_to_time, active_days_mask
                FROM embeddings
                WHERE mac_address = ? AND face_template IS NOT NULL 
                      AND (bio_type LIKE '%FACE%' OR bio_type = 'FACE')
            """, (mac_address,))
            rows = cursor.fetchall()

            for row in rows:
                try:
                    if row['valid_from_date'] and current_date_str < row['valid_from_date']: continue
                    if row['valid_to_date'] and current_date_str > row['valid_to_date']: continue
                    mask = row['active_days_mask']
                    if not mask or not (len(mask) == 6 or len(mask) == 7): continue
                    if len(mask) == 7 and mask[current_day_index] != '1': continue
                    if len(mask) == 6:
                        if current_day_index == 6: continue
                        if mask[current_day_index] != '1': continue
                    if row['valid_from_time'] and current_time_str < row['valid_from_time']: continue
                    if row['valid_to_time'] and current_time_str >= row['valid_to_time']: continue

                    embedding_blob = row['face_template']
                    if embedding_blob:
                        embedding_array = np.frombuffer(embedding_blob, dtype=np.float32)
                        if embedding_array.size == 512: # Or your specific embedding size
                             results.append({
                                 'user_id': str(row['bio_id']), # FR system might expect string ID
                                 'person_name': row['person_name'],
                                 'embedding_data': embedding_array
                             })
                except Exception as e:
                     print(f"[DB ERROR] Processing face record {row['bio_id']} for active check: {e}")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Getting active face embeddings for MAC '{mac_address}': {e}")
    return results

def retrieve_bio_image_by_user_id(bio_id_int):
    bio_id_str = str(bio_id_int)
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT face_image FROM embeddings WHERE bio_id = ? AND face_image IS NOT NULL", (bio_id_str,))
            result = cursor.fetchone()
            return result[0] if result else None
    except sqlite3.Error as e:
        print(f"[DB ERROR] Retrieving face image for bio_id {bio_id_str}: {e}")
        return None
    
def get_user_info_by_bio_id(bio_id_int):
    if bio_id_int is None: return None
    bio_id_str = str(bio_id_int)
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT bio_id, id_number, person_name, mac_address,
                       valid_from_date, valid_to_date,
                       valid_from_time, valid_to_time, active_days_mask,
                       face_image, finger_image, finger_position, idcard_uid, bio_type
                FROM embeddings
                WHERE bio_id = ?
            """, (bio_id_str,))
            return cursor.fetchone()
    except sqlite3.Error as e:
        print(f"[DB ERROR] Retrieving user info for bio_id {bio_id_str}: {e}")
        return None

def get_user_info_by_finger_position(position):
    if position is None: return None
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT bio_id, id_number, person_name, mac_address,
                       valid_from_date, valid_to_date,
                       valid_from_time, valid_to_time, active_days_mask,
                       face_image, finger_image, finger_position, idcard_uid, bio_type
                FROM embeddings
                WHERE finger_position = ? AND (bio_type LIKE '%FINGER%' OR bio_type = 'FINGER')
            """, (position,))
            return cursor.fetchone()
    except sqlite3.Error as e:
        print(f"[DB ERROR] Retrieving user info for finger position {position}: {e}")
        return None

def get_finger_position_by_bio_id_and_mac(bio_id_int, mac_address):
     if bio_id_int is None or not mac_address: return None
     bio_id_str = str(bio_id_int)
     try:
         with sqlite3.connect(DB_FILE) as conn:
             cursor = conn.cursor()
             cursor.execute("SELECT finger_position FROM embeddings WHERE bio_id = ? AND mac_address = ? AND finger_position IS NOT NULL", 
                            (bio_id_str, mac_address))
             result = cursor.fetchone()
             return result[0] if result and result[0] is not None else None
     except sqlite3.Error as e:
         print(f"[DB ERROR] Retrieving finger position for bio_id {bio_id_str}, MAC {mac_address}: {e}")
         return None

def find_next_available_finger_position(max_position=299):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT finger_position FROM embeddings WHERE finger_position IS NOT NULL ORDER BY finger_position ASC")
            used_positions = {row[0] for row in cursor.fetchall()}
            for i in range(0, max_position + 1):
                if i not in used_positions:
                    return i
            return None
    except sqlite3.Error as e:
        print(f"[DB ERROR] Finding next available finger position: {e}")
        return None

def enqueue_outgoing_message(topic: str, payload: str, qos: int = 0, properties: str | None = None):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                "INSERT INTO outbox (topic, payload, qos, properties) VALUES (?, ?, ?, ?)",
                (topic, payload, qos, properties)
            )
            conn.commit()
    except sqlite3.Error as e:
        print(f"[DB ERROR] Enqueuing message to outbox (Topic: {topic}): {e}")

def get_pending_outbox(limit: int = 50):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cur = conn.execute(
                "SELECT id, topic, payload, qos, properties FROM outbox WHERE sent = 0 ORDER BY id LIMIT ?", (limit,)
            )
            return cur.fetchall()
    except sqlite3.Error as e:
        print(f"[DB ERROR] Getting pending outbox messages: {e}")
        return []

def mark_outbox_sent(entry_id):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("UPDATE outbox SET sent = 1 WHERE id = ?", (entry_id,))
            conn.commit()
    except sqlite3.Error as e:
        print(f"[DB ERROR] Marking outbox message ID {entry_id} as sent: {e}")