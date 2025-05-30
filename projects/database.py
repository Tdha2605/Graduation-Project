# database.py
import sqlite3
import numpy as np
from datetime import datetime, timezone,timedelta, time as dt_time, date as dt_date
import os
import base64
import json 

script_dir = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(script_dir, "access_control.db")
VN_TZ = timezone(timedelta(hours=7)) 

# --- Database Initialization ---
def initialize_database():
    """Creates/Updates the database schema, adding idcard_uid and bio_type."""
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
                    finger_template BLOB,      -- Giữ nguyên cho template vân tay (BLOB)
                    finger_image TEXT,
                    finger_position INTEGER UNIQUE,
                    idcard_uid TEXT UNIQUE,       -- << NEW: Cột mới cho UID thẻ RFID (TEXT)
                    bio_type TEXT,                -- Phân biệt (FACE, FINGER, IDCARD, hoặc kết hợp)
                    added_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Kiểm tra và thêm cột mới nếu chưa có (cho các DB cũ)
            cursor.execute("PRAGMA table_info(embeddings)")
            columns = [info[1] for info in cursor.fetchall()]
            if 'bio_type' not in columns:
                cursor.execute("ALTER TABLE embeddings ADD COLUMN bio_type TEXT")
                print("[DB] Added 'bio_type' column to embeddings table.")
            if 'idcard_uid' not in columns:
                cursor.execute("ALTER TABLE embeddings ADD COLUMN idcard_uid TEXT UNIQUE") # Thêm UNIQUE nếu mỗi thẻ chỉ cho 1 bio_id
                print("[DB] Added 'idcard_uid' column to embeddings table.")
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_bio_id ON embeddings (bio_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_mac ON embeddings (mac_address)")
            # cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_dates ON embeddings (valid_from_date, valid_to_date)") # Có thể không cần thiết bằng các index khác
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_finger_pos ON embeddings (finger_position)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_idcard_uid ON embeddings (idcard_uid)") # Index cho tra cứu thẻ RFID
            
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
            print("[DB] Database schema initialized/updated with idcard_uid and bio_type.")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to initialize database schema: {e}")
        raise

# --- Data Processing from Server Push ---
def process_biometric_push(data, mac_address, finger_position_from_sensor=None): # Đổi tên finger_position
    """
    Processes a single biometric command object from the server push.
    Handles PUSH_NEW_BIO and PUSH_UPDATE_BIO.
    """
    try:
        bio_id = data.get('bioId')
        id_number = data.get('id_number')
        person_name = data.get('personName')
        bio_datas = data.get('bioDatas', []) # Đây là list các mẫu sinh trắc học
        from_date_str = data.get('fromDate')
        to_date_str = data.get('toDate')
        from_time_str = data.get('fromTime')
        to_time_str = data.get('toTime')
        active_days = data.get('activeDays')

        if not bio_id:
            print("[DB WARN] Skipping push item: Missing 'bioId'.")
            return False

        # Khởi tạo các giá trị sẽ được lưu vào DB
        face_template_to_save = None
        face_image_to_save = None
        finger_template_to_save = None
        finger_image_to_save = None
        idcard_uid_to_save = None
        # finger_position_from_sensor là vị trí thực tế trên cảm biến vân tay
        # finger_position_to_save sẽ là finger_position_from_sensor sau khi kiểm tra
        finger_position_to_save = finger_position_from_sensor 
        
        # Xác định bio_type chính dựa trên bio_datas
        # Một người có thể có nhiều loại sinh trắc, nhưng bản ghi embeddings là UNIQUE theo bio_id
        # Chúng ta sẽ lưu tất cả các mẫu sinh trắc nhận được vào cùng một bản ghi bio_id.
        # Cột bio_type có thể lưu một string mô tả các loại có, ví dụ "FACE,FINGER,IDCARD"
        # Hoặc đơn giản là type chính/đầu tiên. Hiện tại, giả sử chỉ lưu type chính.
        
        list_of_bio_types_in_push = []

        for bio_data_entry in bio_datas:
            entry_bio_type = bio_data_entry.get("BioType", "").upper()
            entry_template_b64 = bio_data_entry.get("Template") # String từ JSON
            entry_img_b64 = bio_data_entry.get("Img")

            if entry_bio_type == "FACE" and entry_template_b64:
                list_of_bio_types_in_push.append("FACE")
                try:
                    padding = '=' * (-len(entry_template_b64) % 4)
                    face_template_to_save = base64.b64decode(entry_template_b64 + padding)
                    face_image_to_save = entry_img_b64
                except Exception as e:
                    print(f"[DB ERROR] Processing FACE data for bioId {bio_id}: {e}")
            
            elif entry_bio_type == "FINGER" and entry_template_b64:
                list_of_bio_types_in_push.append("FINGER")
                try:
                    padding = '=' * (-len(entry_template_b64) % 4)
                    finger_template_to_save = base64.b64decode(entry_template_b64 + padding) # Lưu template gốc
                    finger_image_to_save = entry_img_b64
                    # finger_position_to_save đã được truyền vào từ MQTTManager sau khi lưu vào sensor
                except Exception as e:
                    print(f"[DB ERROR] Processing FINGER data for bioId {bio_id}: {e}")

            elif entry_bio_type == "IDCARD" and entry_template_b64:
                list_of_bio_types_in_push.append("IDCARD")
                idcard_uid_to_save = entry_template_b64.strip().upper() # UID dạng HEX string
                # Không có ảnh cho IDCARD
                # finger_image_to_save = None # Đảm bảo không ghi đè nếu trước đó là FINGER
                # finger_position_to_save = None # IDCARD không có finger position
        
        # Xác định bio_type chính để lưu (có thể là một list được join bằng comma)
        main_bio_type_to_store = ",".join(sorted(list(set(list_of_bio_types_in_push))))
        if not main_bio_type_to_store and bio_datas: # Nếu có bio_datas nhưng không parse được type
             print(f"[DB WARN] Could not determine any valid BioType for bioId {bio_id} from bioDatas. Skipping DB operation.")
             return False
        elif not bio_datas: # Nếu không có bio_datas, có thể chỉ là cập nhật thông tin người dùng
            print(f"[DB INFO] No bioDatas for bioId {bio_id}. Will attempt to update user info if record exists.")
            # Trong trường hợp này, các template sẽ là None, chỉ cập nhật các trường khác

        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()

            # Xử lý finger_position nếu là FINGER
            if "FINGER" in list_of_bio_types_in_push and finger_position_to_save is not None:
                cursor.execute(
                    "SELECT bio_id FROM embeddings WHERE finger_position = ? AND bio_id != ?", 
                    (finger_position_to_save, bio_id) # Kiểm tra vị trí bị chiếm bởi bio_id *khác*
                )
                row = cursor.fetchone()
                if row: # Vị trí đã bị chiếm bởi người khác
                    print(f"[DB WARN] Finger position {finger_position_to_save} is already taken by bioId {row[0]}. Finding next available for {bio_id}.")
                    new_pos = find_next_available_finger_position() 
                    if new_pos is not None:
                        print(f"[DB] Using new finger position {new_pos} for bioId {bio_id}")
                        finger_position_to_save = new_pos
                        # CẦN THÔNG BÁO LẠI CHO MQTT MANAGER ĐỂ NÓ CẬP NHẬT VỊ TRÍ TRÊN SENSOR?
                        # Hoặc MQTTManager phải đảm bảo vị trí truyền vào là duy nhất.
                        # Hiện tại, giả sử MQTTManager đã xử lý việc này, hoặc chấp nhận ghi đè nếu bio_id trùng.
                    else:
                        print(f"[DB ERROR] No available finger position found for bioId {bio_id}. Cannot store fingerprint.")
                        return False 
            elif "FINGER" not in list_of_bio_types_in_push: # Nếu không có data vân tay, đảm bảo position là null
                finger_position_to_save = None
                finger_template_to_save = None # Không có template vân tay
                finger_image_to_save = None

            # Xử lý idcard_uid nếu là IDCARD
            if "IDCARD" in list_of_bio_types_in_push and idcard_uid_to_save is not None:
                 cursor.execute(
                    "SELECT bio_id FROM embeddings WHERE idcard_uid = ? AND bio_id != ?",
                    (idcard_uid_to_save, bio_id)
                 )
                 row = cursor.fetchone()
                 if row: # UID thẻ đã được gán cho bio_id khác
                      print(f"[DB ERROR] IDCARD UID {idcard_uid_to_save} is already assigned to bioId {row[0]}. Cannot assign to {bio_id}.")
                      return False # Hoặc có chính sách ghi đè/cập nhật bio_id cho UID đó
            elif "IDCARD" not in list_of_bio_types_in_push:
                idcard_uid_to_save = None # Nếu không có data thẻ, đảm bảo UID là null

            # Nếu không có bio_datas, khi UPDATE, chúng ta muốn giữ lại các template cũ
            # Điều này cần query bản ghi cũ trước. Để đơn giản, ON CONFLICT sẽ xử lý.
            # Nếu các template mới là None, và có bản ghi cũ, chúng ta muốn giữ template cũ.
            # SQLite's ON CONFLICT DO UPDATE SET column=excluded.column sẽ ghi đè bằng giá trị mới (kể cả NULL).
            # Để giữ giá trị cũ nếu giá trị mới là NULL, cần phức tạp hơn:
            # Ví dụ: face_template = COALESCE(excluded.face_template, embeddings.face_template)
            # Hiện tại, sẽ ghi đè.

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
                    face_template=COALESCE(excluded.face_template, embeddings.face_template), -- Giữ template cũ nếu mới là NULL
                    face_image=COALESCE(excluded.face_image, embeddings.face_image),
                    finger_template=COALESCE(excluded.finger_template, embeddings.finger_template),
                    finger_image=COALESCE(excluded.finger_image, embeddings.finger_image),
                    finger_position=COALESCE(excluded.finger_position, embeddings.finger_position),
                    idcard_uid=COALESCE(excluded.idcard_uid, embeddings.idcard_uid),
                    bio_type= CASE WHEN excluded.bio_type IS NOT NULL AND excluded.bio_type != '' 
                                   THEN excluded.bio_type 
                                   ELSE embeddings.bio_type 
                              END, -- Chỉ cập nhật bio_type nếu có giá trị mới
                    added_timestamp=CURRENT_TIMESTAMP
            """, (
                bio_id, id_number, person_name, mac_address,
                from_date_str, to_date_str, from_time_str, to_time_str, active_days,
                face_template_to_save, face_image_to_save, 
                finger_template_to_save, finger_image_to_save, finger_position_to_save,
                idcard_uid_to_save, main_bio_type_to_store
            ))
            conn.commit()
            print(f"[DB] Processed PUSH_NEW/UPDATE for bioId '{bio_id}' (BioTypes: {main_bio_type_to_store}, FingerPos: {finger_position_to_save}, IDCardUID: {idcard_uid_to_save}).")
            return True

    except Exception as e:
        print(f"[DB ERROR] Failed to process biometric push for bioId '{data.get('bioId', 'N/A')}': {e}")
        import traceback
        traceback.print_exc()
        return False

# --- Data Deletion ---
def delete_biometrics_and_access_for_bio_id(bio_id, mac_address, delete_globally=False): # Thêm mac_address
    """
    Deletes biometric records for a bio_id.
    Nếu PUSH_DELETE_BIO chỉ áp dụng cho 1 MAC, thì chỉ xóa access rule (nếu có bảng riêng).
    Hiện tại, giả sử PUSH_DELETE_BIO là xóa hẳn bio_id đó khỏi embeddings nếu mac_address khớp.
    """
    deleted_count = 0
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            # Chỉ xóa nếu bio_id thuộc về MAC này (theo schema hiện tại)
            # Hoặc nếu delete_globally=True (dùng cho admin tool chẳng hạn)
            if delete_globally:
                 cursor.execute("DELETE FROM embeddings WHERE bio_id = ?", (bio_id,))
            else:
                 cursor.execute("DELETE FROM embeddings WHERE bio_id = ? AND mac_address = ?", (bio_id, mac_address))
            deleted_count = cursor.rowcount
            conn.commit()
            if deleted_count > 0:
                print(f"[DB] Deleted {deleted_count} DB records for bioId '{bio_id}' (MAC: {mac_address if not delete_globally else 'GLOBAL'}).")
            else:
                print(f"[DB] No DB records found for bioId '{bio_id}' (MAC: {mac_address if not delete_globally else 'GLOBAL'}) to delete.")
            return True
    except sqlite3.Error as e:
        print(f"[DB ERROR] Failed to delete DB records for bioId '{bio_id}': {e}")
        return False

def delete_all_biometrics_and_access_for_mac(mac_address):
    deleted_count = 0
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM embeddings WHERE mac_address = ?", (mac_address,))
            deleted_count = cursor.rowcount
            conn.commit()
            print(f"[DB] SYNC_ALL: Deleted {deleted_count} DB records for MAC address '{mac_address}'.")
            return True
    except sqlite3.Error as e:
        print(f"[DB ERROR] SYNC_ALL: Failed to delete DB records for MAC '{mac_address}': {e}")
        return False

# --- Data Querying ---
def get_user_by_bio_type_and_template(bio_type, template_data, mac_address):
    """
    Lấy thông tin người dùng dựa trên BioType, template_data và mac_address.
    Kiểm tra cả is_active (ngầm định qua việc query) và thời gian hiệu lực.
    """
    if not bio_type or not template_data or not mac_address:
        return None
    
    query_field = None
    if bio_type.upper() == "FACE":
        # template_data cho FACE là bio_id từ key của insightface (ví dụ: "PersonName_123_ActualBioID")
        # Cần parse ActualBioID từ template_data
        parsed_bio_id = template_data.split('_')[-1]
        query_field = "bio_id" # Sẽ query theo bio_id đã parse
        template_data_for_query = parsed_bio_id 
    elif bio_type.upper() == "FINGER":
        # template_data cho FINGER là position từ sensor
        query_field = "finger_position"
        template_data_for_query = int(template_data) # Đảm bảo là số
    elif bio_type.upper() == "IDCARD":
        # template_data cho IDCARD là UID thẻ (HEX string)
        query_field = "idcard_uid"
        template_data_for_query = template_data.upper()
    else:
        print(f"[DB WARN] Unknown bio_type '{bio_type}' for template query.")
        return None

    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row 
            cursor = conn.cursor()
            # Query để lấy thông tin người dùng và các điều kiện truy cập
            # Lọc theo mac_address để đảm bảo người này được phép trên thiết bị này
            # Cột bio_type trong DB có thể chứa nhiều giá trị (vd "FACE,FINGER")
            # nên cần LIKE để tìm.
            sql_query = f"""
                SELECT bio_id, id_number, person_name, face_image, finger_image,
                       valid_from_date, valid_to_date, 
                       valid_from_time, valid_to_time, active_days_mask
                FROM embeddings
                WHERE {query_field} = ? AND mac_address = ? 
                      AND (bio_type LIKE ? OR bio_type = ?) 
            """
            # Đối với LIKE, chúng ta cần %bio_type%
            # Ví dụ, nếu bio_type là "FACE", chúng ta tìm '%FACE%'
            like_bio_type_param = f"%{bio_type.upper()}%"
            
            cursor.execute(sql_query, (template_data_for_query, mac_address, like_bio_type_param, bio_type.upper()))
            user_data_row = cursor.fetchone()

            if user_data_row:
                return user_data_row # Trả về sqlite3.Row object
            else:
                # print(f"[DB TRACE] No user found for {bio_type} template {template_data_for_query} on MAC {mac_address}")
                return None
    except sqlite3.Error as e:
        print(f"[DB ERROR] Getting user by {bio_type} template {template_data_for_query}: {e}")
        return None
    except ValueError as ve: # Lỗi chuyển đổi int(template_data) cho FINGER
        print(f"[DB ERROR] Invalid template_data format for {bio_type}: {ve}")
        return None


def is_user_access_valid_now(bio_id, mac_address): # Giữ nguyên hàm này, nó dựa trên bio_id
    """Kiểm tra xem người dùng có quyền truy cập thiết bị vào thời điểm hiện tại không."""
    user_record = get_user_info_by_bio_id(bio_id) # Lấy bản ghi đầy đủ
    if not user_record:
        # print(f"[DB TRACE] is_user_access_valid_now: No record found for bio_id {bio_id}.")
        return False
    
    # Kiểm tra MAC address của bản ghi có khớp không
    # Quan trọng: schema hiện tại gán mac_address vào bản ghi chính.
    # Nếu một bio_id có thể dùng trên nhiều MAC với rule khác nhau, cần bảng user_device_access riêng.
    # Hiện tại, giả sử mac_address trong embeddings là MAC mà bio_id này được phép.
    if user_record['mac_address'] != mac_address:
        # print(f"[DB TRACE] is_user_access_valid_now: MAC mismatch for bio_id {bio_id}. DB_MAC: {user_record['mac_address']}, Req_MAC: {mac_address}")
        return False # Không thuộc MAC này

    try:
        now = datetime.now(VN_TZ)
        current_date_str = now.strftime('%Y-%m-%d')
        current_time_str = now.strftime('%H:%M:%S')
        current_day_index = now.weekday() # Monday 0, Sunday 6

        if user_record['valid_from_date'] and current_date_str < user_record['valid_from_date']: return False
        if user_record['valid_to_date'] and current_date_str > user_record['valid_to_date']: return False
        
        mask = user_record['active_days_mask']
        if not mask or len(mask) != 6 or mask[current_day_index] != '1': return False
        
        if user_record['valid_from_time'] and current_time_str < user_record['valid_from_time']: return False
        if user_record['valid_to_time'] and current_time_str >= user_record['valid_to_time']: return False # >= vì to_time là giới hạn cuối
        
        return True
    except KeyError as ke:
        print(f"[DB ERROR] is_user_access_valid_now: Missing key '{ke}' for bioId {bio_id}")
        return False
    except Exception as e:
        print(f"[DB ERROR] is_user_access_valid_now: Exception for bioId {bio_id}: {e}")
        return False

# --- Các hàm truy vấn khác giữ nguyên hoặc điều chỉnh nhẹ ---

def get_active_embeddings(mac_address): # Chủ yếu cho Face Recognition
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
                    if not mask or len(mask) != 6 or mask[current_day_index] != '1': continue
                    if row['valid_from_time'] and current_time_str < row['valid_from_time']: continue
                    if row['valid_to_time'] and current_time_str >= row['valid_to_time']: continue

                    embedding_blob = row['face_template']
                    embedding_array = np.frombuffer(embedding_blob, dtype=np.float32)
                    if embedding_array.size == 512:
                         results.append({
                             'user_id': row['bio_id'],
                             'person_name': row['person_name'],
                             'embedding_data': embedding_array
                         })
                    else:
                         print(f"[DB WARN] Skipping active face {row['bio_id']} due to embedding size: {embedding_array.size}")
                except Exception as e:
                     print(f"[DB ERROR] Processing face record {row['bio_id']} for active check: {e}")
    except sqlite3.Error as e:
        print(f"[DB ERROR] Getting active face embeddings for MAC '{mac_address}': {e}")
    # print(f"[DB] Found {len(results)} active FACE embeddings for MAC {mac_address}.")
    return results

def retrieve_bio_image_by_user_id(user_id):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT face_image FROM embeddings WHERE bio_id = ? AND face_image IS NOT NULL", (user_id,))
            result = cursor.fetchone()
            return result[0] if result else None
    except sqlite3.Error as e:
        print(f"[DB ERROR] Retrieving face image for bio_id {user_id}: {e}")
        return None
    
def get_user_info_by_bio_id(bio_id): # Gần như giữ nguyên, thêm idcard_uid
    if not bio_id: return None
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
            """, (bio_id,))
            row = cursor.fetchone()
            return row
    except sqlite3.Error as e:
        print(f"[DB ERROR] Retrieving user info for bio_id {bio_id}: {e}")
        return None

def get_user_info_by_finger_position(position): # Giữ nguyên
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
            row = cursor.fetchone()
            return row
    except sqlite3.Error as e:
        print(f"[DB ERROR] Retrieving user info for finger position {position}: {e}")
        return None

def get_finger_position_by_bio_id_and_mac(bio_id, mac_address): # Cập nhật để dùng mac_address
     if not bio_id or not mac_address: return None
     try:
         with sqlite3.connect(DB_FILE) as conn:
             cursor = conn.cursor()
             cursor.execute("SELECT finger_position FROM embeddings WHERE bio_id = ? AND mac_address = ? AND finger_position IS NOT NULL", 
                            (bio_id, mac_address))
             result = cursor.fetchone()
             return result[0] if result and result[0] is not None else None
     except sqlite3.Error as e:
         print(f"[DB ERROR] Retrieving finger position for bio_id {bio_id}, MAC {mac_address}: {e}")
         return None

def find_next_available_finger_position(max_position=299): # Giảm max_position nếu sensor có ít hơn
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT finger_position FROM embeddings WHERE finger_position IS NOT NULL ORDER BY finger_position ASC")
            used_positions = {row[0] for row in cursor.fetchall()}
            for i in range(0, max_position + 1): # Sensor thường bắt đầu từ 0 hoặc 1
                if i not in used_positions:
                    # print(f"[DB DEBUG] Found next available finger position: {i}")
                    return i
            print(f"[DB WARN] All finger positions up to {max_position} seem to be used.")
            return None
    except sqlite3.Error as e:
        print(f"[DB ERROR] Finding next available finger position: {e}")
        return None

# --- Outbox functions ---
def enqueue_outgoing_message(topic: str, payload: str, qos: int = 0, properties: list[tuple[str,str]] | None = None):
    props_json = json.dumps(properties) if properties else None
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                "INSERT INTO outbox (topic, payload, qos, properties) VALUES (?, ?, ?, ?)",
                (topic, payload, qos, props_json)
            )
            conn.commit()
    except sqlite3.Error as e:
        print(f"[DB ERROR] Enqueuing message to outbox (Topic: {topic}): {e}")


def get_pending_outbox(limit: int = 50): # Thêm limit để tránh load quá nhiều
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

# Các hàm khác (retrieve_all_bio_records_for_display, delete_expired_guests) có thể cần xem lại
# dựa trên cách bạn muốn hiển thị và quản lý "guest".
# Hiện tại, không thay đổi chúng.