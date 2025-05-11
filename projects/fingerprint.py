# fingerprint.py
import customtkinter as ctk
import threading
import time
from datetime import datetime, timezone,timedelta, time as dt_time, date as dt_date
from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1, FINGERPRINT_CHARBUFFER2
from PIL import Image
import database

DEFAULT_FINGERPRINT_PORT = '/dev/ttyAMA0'
DEFAULT_FINGERPRINT_BAUDRATE = 57600
SENSOR_SEARCH_CONFIDENCE = 50
VN_TZ = timezone(timedelta(hours=7))

def clear_frame(frame):
    for widget in frame.winfo_children():
        widget.destroy()

def update_fp_frame_with_error(fp_frame, message="Lỗi phần cứng", on_close=None):
    if not fp_frame or not fp_frame.winfo_exists(): return
    clear_frame(fp_frame)
    try:
        img = Image.open("/home/anhtd/projects/images/fp_failure.png")
    except Exception:
        img = Image.new("RGB", (1024, 600), color="orange")
    ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(1024, 600))
    lbl_img = ctk.CTkLabel(fp_frame, image=ctk_img, text="")
    lbl_img.image = ctk_img
    lbl_img.pack(pady=(10, 10), expand=True, fill="both")
    lbl_text = ctk.CTkLabel(fp_frame, text=message, font=ctk.CTkFont(size=18, weight="bold"), text_color="orange")
    lbl_text.pack(pady=(0, 20))
    fp_frame.after(2500, lambda: (fp_frame.destroy() if fp_frame.winfo_exists() else None, on_close() if on_close else None))


def update_fp_frame_with_success(fp_frame, user_name="", on_close=None):
    if not fp_frame or not fp_frame.winfo_exists(): return
    clear_frame(fp_frame)
    try:
        success_img = Image.open("/home/anhtd/projects/images/fp_success.png")
    except Exception:
        success_img = Image.new("RGB", (1024, 600), color="green")
    ctk_success = ctk.CTkImage(light_image=success_img, dark_image=success_img, size=(1024, 600))
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_success, text="")
    icon_label.image = ctk_success
    icon_label.pack(pady=(10, 10), expand=True, fill="both")
    success_text = f"Xác thực thành công!"
    if user_name: # Đã sửa ở lần trước
        success_text += f"\nXin chào, {user_name}!"
    text_label = ctk.CTkLabel(fp_frame, text=success_text, font=ctk.CTkFont(size=18, weight="bold"), text_color="green")
    text_label.pack(pady=(0, 20))
    fp_frame.after(2000, lambda: (fp_frame.destroy() if fp_frame.winfo_exists() else None, on_close() if on_close else None))


def update_fp_frame_with_failure(fp_frame, message="Không tìm thấy vân tay hoặc không hợp lệ", on_close=None):
    if not fp_frame or not fp_frame.winfo_exists(): return
    clear_frame(fp_frame)
    try:
        fail_img = Image.open("/home/anhtd/projects/images/fp_error.png")
    except Exception:
        fail_img = Image.new("RGB", (1024, 600), color="red")
    ctk_fail = ctk.CTkImage(light_image=fail_img, dark_image=fail_img, size=(1024, 600))
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_fail, text="")
    icon_label.image = ctk_fail
    icon_label.pack(pady=(10, 10), expand=True, fill="both")
    text_label = ctk.CTkLabel(fp_frame, text=message, font=ctk.CTkFont(size=18, weight="bold"), text_color="red")
    text_label.pack(pady=(0, 20))
    fp_frame.after(1000, lambda: (fp_frame.destroy() if fp_frame.winfo_exists() else None, on_close() if on_close else None))


def set_prompt_state(fp_frame, cancel_callback):
    if not fp_frame or not fp_frame.winfo_exists(): return
    clear_frame(fp_frame)
    try:
        img_grey = Image.open("/home/anhtd/projects/images/fp_initial.png")
    except Exception:
        img_grey = Image.new("RGB", (1024, 600), color="gray")
    ctk_img_grey = ctk.CTkImage(light_image=img_grey, dark_image=img_grey, size=(1024, 600))
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_img_grey, text="")
    icon_label.image = ctk_img_grey
    icon_label.pack(pady=(0, 0), expand=True, fill="both")
    text_label = ctk.CTkLabel(fp_frame, text="Đặt ngón tay lên cảm biến", font=ctk.CTkFont(size=16, weight="bold"), text_color="white")
    text_label.pack(pady=(0, 20))
    cancel_button = ctk.CTkButton(fp_frame, text="Hủy", command=cancel_callback)
    cancel_button.pack(pady=10)


def set_scanning_state(fp_frame, cancel_callback):
    if not fp_frame or not fp_frame.winfo_exists(): return
    clear_frame(fp_frame)
    try:
        img_blue = Image.open("/home/anhtd/projects/images/fp_scanning.png")
    except Exception:
        img_blue = Image.new("RGB", (1024, 600), color="blue")
    ctk_img_blue = ctk.CTkImage(light_image=img_blue, dark_image=img_blue, size=(1024, 600))
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_img_blue, text="")
    icon_label.image = ctk_img_blue
    icon_label.pack(pady=(10, 10), expand=True, fill="both")
    text_label = ctk.CTkLabel(fp_frame, text="Đang quét và tìm kiếm...", font=ctk.CTkFont(size=16, weight="bold"))
    text_label.pack(pady=(0, 20))
    cancel_button = ctk.CTkButton(fp_frame, text="Hủy", command=cancel_callback)
    cancel_button.pack(pady=10)

def is_currently_valid(user_info):
    if not user_info:
        return False
    try:
        now = datetime.now(VN_TZ)
        current_date_str = now.strftime('%Y-%m-%d')
        current_time_str = now.strftime('%H:%M:%S')
        current_day_index = now.weekday()

        # Access sqlite3.Row using keys like a dictionary
        # Check for key existence to be safe if columns might be missing in some rows
        if 'valid_from_date' in user_info.keys() and user_info['valid_from_date'] and current_date_str < user_info['valid_from_date']: return False
        if 'valid_to_date' in user_info.keys() and user_info['valid_to_date'] and current_date_str > user_info['valid_to_date']: return False
        
        mask = user_info['active_days_mask'] if 'active_days_mask' in user_info.keys() else None
        if not mask or len(mask) != 7 or mask[current_day_index] != '1': return False
        
        if 'valid_from_time' in user_info.keys() and user_info['valid_from_time'] and current_time_str < user_info['valid_from_time']: return False
        if 'valid_to_time' in user_info.keys() and user_info['valid_to_time'] and current_time_str >= user_info['valid_to_time']: return False
        return True
    except KeyError as ke:
        bio_id_val = user_info['bio_id'] if 'bio_id' in user_info.keys() else 'N/A_KeyError'
        print(f"[FP ERROR] Missing key '{ke}' during validity check for bioId {bio_id_val}")
        return False
    except Exception as e:
        bio_id_val = user_info['bio_id'] if 'bio_id' in user_info.keys() else 'N/A_Exception'
        print(f"[FP ERROR] Exception during validity check for bioId {bio_id_val}: {e}")
        return False

def open_fingerprint_prompt(parent, sensor, on_success_callback=None, on_failure_callback=None):
    fp_frame = ctk.CTkFrame(parent, fg_color="transparent")
    fp_frame._owner_frame = 'fingerprint' # Thêm để clear_frames trong main.py nhận diện
    fp_frame.pack(expand=True, fill="both")
    cancel_flag = {"cancel": False}
    def cancel_scan():
        cancel_flag["cancel"] = True
        if fp_frame.winfo_exists():
             fp_frame.destroy()
        if on_failure_callback:
             on_failure_callback()
    set_prompt_state(fp_frame, cancel_callback=cancel_scan)
    threading.Thread(target=perform_fingerprint_verification,
                     args=(fp_frame, sensor, cancel_flag, on_success_callback, on_failure_callback),
                     daemon=True).start()

def perform_fingerprint_verification(fp_frame, sensor, cancel_flag, on_success_callback, on_failure_callback):
    start_time = time.time()
    timeout_seconds = 15
    try:
        if not sensor or not sensor.verifyPassword():
             print("[FP ERROR] Fingerprint sensor not available or password incorrect at verification start.")
             if fp_frame.winfo_exists():
                 fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi cảm biến vân tay", on_close=on_failure_callback))
             return
        print("[FP INFO] Waiting for finger...")
        while not cancel_flag["cancel"]:
            if time.time() - start_time > timeout_seconds:
                print("[FP WARN] Fingerprint scan timed out.")
                if fp_frame.winfo_exists():
                    fp_frame.after(0, lambda: update_fp_frame_with_failure(fp_frame, "Quá thời gian chờ", on_close=on_failure_callback))
                return
            finger_detected = False
            try:
                finger_detected = sensor.readImage()
            except Exception as e:
                 print(f"[FP ERROR] Exception reading fingerprint image: {e}")
                 if fp_frame.winfo_exists():
                     fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi đọc cảm biến", on_close=on_failure_callback))
                 return
            if finger_detected:
                print("[FP INFO] Finger detected. Processing...")
                if fp_frame.winfo_exists():
                    def scanning_cancel():
                        cancel_flag["cancel"] = True
                        if fp_frame.winfo_exists():
                            fp_frame.destroy()
                        if on_failure_callback:
                            on_failure_callback()
                    fp_frame.after(0, lambda: set_scanning_state(fp_frame, cancel_callback=scanning_cancel))
                try:
                    if sensor.convertImage(FINGERPRINT_CHARBUFFER1):
                        result = sensor.searchTemplate()
                        position = result[0]
                        accuracy = result[1]
                        print(f"[FP DEBUG] Sensor search result - Position: {position}, Accuracy: {accuracy}")
                        if position >= 0 and accuracy >= SENSOR_SEARCH_CONFIDENCE:
                            print(f"[FP INFO] Match found by sensor at position {position} with score {accuracy}.")
                            user_info = database.get_user_info_by_finger_position(position)
                            if user_info:
                                if is_currently_valid(user_info):
                                    user_bio_id = user_info['bio_id'] if 'bio_id' in user_info.keys() else 'Unknown_BioID'
                                    person_name_val = user_info['person_name'] if 'person_name' in user_info.keys() and user_info['person_name'] else None
                                    id_number_val = user_info['id_number'] if 'id_number' in user_info.keys() and user_info['id_number'] else None
                                    name_for_log = person_name_val or id_number_val or 'N/A'
                                    print(f"[FP INFO] User {user_bio_id} ({name_for_log}) is valid.")
                                    display_name_fp = person_name_val or id_number_val or user_bio_id
                                    if fp_frame.winfo_exists():
                                        fp_frame.after(0, lambda: update_fp_frame_with_success(fp_frame, user_name=display_name_fp, on_close=lambda: on_success_callback(user_bio_id) if on_success_callback else None))
                                    return
                                else:
                                    user_bio_id_for_warn = user_info['bio_id'] if 'bio_id' in user_info.keys() else 'Unknown_BioID'
                                    print(f"[FP WARN] Fingerprint match for {user_bio_id_for_warn}, but user is not currently valid (time/date/day).")
                                    if fp_frame.winfo_exists():
                                        fp_frame.after(0, lambda: update_fp_frame_with_failure(fp_frame, "Vân tay đúng, nhưng không hợp lệ", on_close=on_failure_callback))
                                    return
                            else:
                                print(f"[FP ERROR] Sensor found match at position {position}, but no user found in DB for this position!")
                                if fp_frame.winfo_exists():
                                    fp_frame.after(0, lambda: update_fp_frame_with_failure(fp_frame, "Lỗi dữ liệu người dùng", on_close=on_failure_callback))
                                return
                        else:
                            print("[FP INFO] No matching fingerprint found by sensor.")
                            if fp_frame.winfo_exists():
                                fp_frame.after(0, lambda: update_fp_frame_with_failure(fp_frame, "Vân tay không khớp", on_close=on_failure_callback))
                            return
                    else:
                        print("[FP ERROR] Failed to convert fingerprint image on sensor.")
                        if fp_frame.winfo_exists():
                             fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi xử lý ảnh vân tay", on_close=on_failure_callback))
                        return
                except Exception as e:
                    print(f"[FP ERROR] Exception during fingerprint search/processing: {e}")
                    import traceback
                    traceback.print_exc()
                    if fp_frame.winfo_exists():
                         fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi xử lý vân tay", on_close=on_failure_callback))
                    return
            time.sleep(0.1)
        print("[FP INFO] Fingerprint verification thread finished or cancelled.")
    except Exception as e:
        print(f"[FP ERROR] Unhandled exception in fingerprint verification thread: {e}")
        import traceback
        traceback.print_exc()
        try:
            if fp_frame.winfo_exists():
                fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi không xác định", on_close=on_failure_callback))
        except Exception as ui_e:
             print(f"[FP ERROR] Could not update UI after thread exception: {ui_e}")
        if on_failure_callback and not cancel_flag["cancel"]:
             on_failure_callback()