# fingerprint.py
import customtkinter as ctk
import threading
import time
import os
from datetime import datetime, timezone, timedelta, time as dt_time, date as dt_date
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
        img_path = os.path.join(os.path.dirname(__file__), "images", "fp_failure.png")
        img = Image.open(img_path)
    except Exception:
        img = Image.new("RGB", (150, 150), color="orange")
    ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(150, 150))
    lbl_img = ctk.CTkLabel(fp_frame, image=ctk_img, text="")
    lbl_img.image = ctk_img
    lbl_img.pack(pady=(20, 10), expand=True)
    lbl_text = ctk.CTkLabel(fp_frame, text=message, font=ctk.CTkFont(size=18, weight="bold"), text_color="orange", wraplength=fp_frame.winfo_width()-20 if fp_frame.winfo_width() > 20 else 280, justify="center")
    lbl_text.pack(pady=(0, 20), fill="x", padx=10)
    if on_close:
        fp_frame.after(2500, lambda: (fp_frame.destroy() if fp_frame.winfo_exists() else None, on_close()))

def update_fp_frame_with_success(fp_frame, user_name="", on_close=None):
    if not fp_frame or not fp_frame.winfo_exists(): return
    clear_frame(fp_frame)
    try:
        img_path = os.path.join(os.path.dirname(__file__), "images", "fp_success.png")
        success_img = Image.open(img_path)
    except Exception:
        success_img = Image.new("RGB", (150, 150), color="green")
    ctk_success = ctk.CTkImage(light_image=success_img, dark_image=success_img, size=(150, 150))
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_success, text="")
    icon_label.image = ctk_success
    icon_label.pack(pady=(20, 10), expand=True)
    success_text = f"Xác thực thành công!"
    if user_name:
        success_text += f"\nXin chào, {user_name}!"
    text_label = ctk.CTkLabel(fp_frame, text=success_text, font=ctk.CTkFont(size=18, weight="bold"), text_color="green", wraplength=fp_frame.winfo_width()-20 if fp_frame.winfo_width() > 20 else 280, justify="center")
    text_label.pack(pady=(0, 20), fill="x", padx=10)
    if on_close:
        fp_frame.after(2000, lambda: (fp_frame.destroy() if fp_frame.winfo_exists() else None, on_close()))

def update_fp_frame_with_failure(fp_frame, message="Không tìm thấy vân tay hoặc không hợp lệ", on_close=None):
    if not fp_frame or not fp_frame.winfo_exists(): return
    clear_frame(fp_frame)
    try:
        img_path = os.path.join(os.path.dirname(__file__), "images", "fp_error.png")
        fail_img = Image.open(img_path)
    except Exception:
        fail_img = Image.new("RGB", (150, 150), color="red")
    ctk_fail = ctk.CTkImage(light_image=fail_img, dark_image=fail_img, size=(150, 150))
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_fail, text="")
    icon_label.image = ctk_fail
    icon_label.pack(pady=(20, 10), expand=True)
    text_label = ctk.CTkLabel(fp_frame, text=message, font=ctk.CTkFont(size=18, weight="bold"), text_color="red", wraplength=fp_frame.winfo_width()-20 if fp_frame.winfo_width() > 20 else 280, justify="center")
    text_label.pack(pady=(0, 20), fill="x", padx=10)
    if on_close:
        fp_frame.after(2000, lambda: (fp_frame.destroy() if fp_frame.winfo_exists() else None, on_close()))

def set_prompt_state(fp_frame, cancel_callback):
    if not fp_frame or not fp_frame.winfo_exists(): return
    clear_frame(fp_frame)
    try:
        img_path = os.path.join(os.path.dirname(__file__), "images", "fp_initial.png")
        img_grey = Image.open(img_path)
    except Exception:
        img_grey = Image.new("RGB", (200, 200), color="grey")
    ctk_img_grey = ctk.CTkImage(light_image=img_grey, dark_image=img_grey, size=(200, 200))
    
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_img_grey, text="")
    icon_label.image = ctk_img_grey
    icon_label.pack(pady=(50, 20), expand=True)
    
    text_label = ctk.CTkLabel(fp_frame, text="ĐẶT NGÓN TAY LÊN CẢM BIẾN", font=ctk.CTkFont(size=20, weight="bold"), text_color="#333333")
    text_label.pack(pady=(0, 30))
    
    cancel_button = ctk.CTkButton(fp_frame, text="HỦY BỎ", command=cancel_callback, width=150, height=45, font=("Segoe UI", 16, "bold"), fg_color="#7F8C8D", hover_color="#95A5A6")
    cancel_button.pack(pady=(0, 30), side="bottom")

def set_scanning_state(fp_frame, cancel_callback):
    if not fp_frame or not fp_frame.winfo_exists(): return
    clear_frame(fp_frame)
    try:
        img_path = os.path.join(os.path.dirname(__file__), "images", "fp_scanning.png")
        img_blue = Image.open(img_path)
    except Exception:
        img_blue = Image.new("RGB", (200, 200), color="blue")
    ctk_img_blue = ctk.CTkImage(light_image=img_blue, dark_image=img_blue, size=(200, 200))
    
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_img_blue, text="")
    icon_label.image = ctk_img_blue
    icon_label.pack(pady=(50, 20), expand=True)
    
    text_label = ctk.CTkLabel(fp_frame, text="ĐANG QUÉT VÀ TÌM KIẾM...", font=ctk.CTkFont(size=18, weight="bold"), text_color="#2980B9")
    text_label.pack(pady=(0, 30))
    if cancel_callback: # Chỉ hiển thị nút hủy nếu có callback được cung cấp
        cancel_button = ctk.CTkButton(fp_frame, text="Hủy", command=cancel_callback, width=120, height=40)
        cancel_button.pack(pady=10, side="bottom")


def is_currently_valid(user_info_row, device_mac):
    if not user_info_row: return False
    try:
        bio_id = user_info_row['bio_id']
        return database.is_user_access_valid_now(bio_id, device_mac)
    except KeyError:
        print(f"[FP ERROR] 'bio_id' not found in user_info_row for validity check.")
        return False
    except Exception as e:
        print(f"[FP ERROR] Exception during validity check redirect: {e}")
        return False

def open_fingerprint_prompt(parent, sensor, on_success_callback=None, on_failure_callback=None, device_mac_address=None):
    if device_mac_address is None:
        print("[FP ERROR] device_mac_address is required for open_fingerprint_prompt.")
        if on_failure_callback:
            on_failure_callback("Thiếu MAC thiết bị")
        return

    fp_frame = ctk.CTkFrame(parent, fg_color="white", corner_radius=0)
    fp_frame._owner_module = 'fingerprint_ui' 
    fp_frame.pack(expand=True, fill="both")
    
    cancel_flag = {"cancel": False}
    def cancel_scan_action():
        cancel_flag["cancel"] = True
        if fp_frame.winfo_exists():
             fp_frame.destroy()
        if on_failure_callback:
             on_failure_callback("Người dùng hủy")
    
    set_prompt_state(fp_frame, cancel_callback=cancel_scan_action)
    
    threading.Thread(target=perform_fingerprint_verification,
                     args=(fp_frame, sensor, cancel_flag, on_success_callback, on_failure_callback, device_mac_address),
                     daemon=True).start()

def perform_fingerprint_verification(fp_frame, sensor, cancel_flag, on_success_callback, on_failure_callback, device_mac):
    start_time = time.time()
    timeout_seconds = 15
    
    from pyfingerprint.pyfingerprint import FINGERPRINT_CHARBUFFER1 

    try:
        if not sensor or not hasattr(sensor, 'verifyPassword') or not sensor.verifyPassword():
             print("[FP ERROR] Fingerprint sensor not available or password incorrect at verification start.")
             if fp_frame.winfo_exists():
                 fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi cảm biến vân tay.\nVui lòng thử lại sau.", on_close=lambda: on_failure_callback("Lỗi cảm biến") if on_failure_callback else None))
             return
        
        print("[FP INFO] Waiting for finger...")
        while not cancel_flag["cancel"]:
            if time.time() - start_time > timeout_seconds:
                print("[FP WARN] Fingerprint scan timed out waiting for finger.")
                if fp_frame.winfo_exists():
                    fp_frame.after(0, lambda: update_fp_frame_with_failure(fp_frame, "Quá thời gian chờ.\nVui lòng đặt tay nhanh hơn.", on_close=lambda: on_failure_callback("Timeout") if on_failure_callback else None))
                return
            
            finger_detected_by_sensor = False
            try:
                finger_detected_by_sensor = sensor.readImage()
            except Exception as e_read_img:
                 print(f"[FP ERROR] Exception reading fingerprint image: {e_read_img}")
                 if fp_frame.winfo_exists():
                     fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi đọc cảm biến.\Thử lại.", on_close=lambda: on_failure_callback("Lỗi đọc sensor") if on_failure_callback else None))
                 return

            if finger_detected_by_sensor:
                print("[FP INFO] Finger detected. Processing image and searching...")
                if fp_frame.winfo_exists():
                    fp_frame.after(0, lambda: set_scanning_state(fp_frame, cancel_callback=None)) 
                
                try:
                    if sensor.convertImage(FINGERPRINT_CHARBUFFER1):
                        search_result = sensor.searchTemplate()
                        found_position = search_result[0]    
                        match_accuracy = search_result[1]  

                        print(f"[FP DEBUG] Sensor search result - Position: {found_position}, Accuracy: {match_accuracy}")

                        if found_position >= 0 and match_accuracy >= SENSOR_SEARCH_CONFIDENCE:
                            print(f"[FP INFO] Match found by sensor at position {found_position} with score {match_accuracy}.")
                            
                            user_info_from_db = database.get_user_by_bio_type_and_template("FINGER", str(found_position), device_mac)
                            
                            if user_info_from_db:
                                if is_currently_valid(user_info_from_db, device_mac): 
                                    person_name_val = user_info_from_db['person_name'] if 'person_name' in user_info_from_db.keys() and user_info_from_db['person_name'] else None
                                    id_number_val = user_info_from_db['id_number'] if 'id_number' in user_info_from_db.keys() and user_info_from_db['id_number'] else None
                                    bio_id_val = user_info_from_db['bio_id']
                                    
                                    display_name_fp = person_name_val or id_number_val or bio_id_val

                                    print(f"[FP INFO] User {bio_id_val} ({display_name_fp}) is valid for access.")
                                    if fp_frame.winfo_exists():
                                        fp_frame.after(0, lambda ui=user_info_from_db: update_fp_frame_with_success(fp_frame, user_name=display_name_fp, on_close=lambda: on_success_callback(ui) if on_success_callback else None))
                                    return
                                else:
                                    bioid_warn = user_info_from_db['bio_id'] if 'bio_id' in user_info_from_db.keys() else 'Unknown BioID'
                                    print(f"[FP WARN] Fingerprint match for {bioid_warn}, but user is not currently valid (time/date/day).")
                                    if fp_frame.winfo_exists():
                                        fp_frame.after(0, lambda: update_fp_frame_with_failure(fp_frame, "Vân tay đúng,\nnhưng ngoài giờ cho phép.", on_close=lambda: on_failure_callback("Ngoài giờ") if on_failure_callback else None))
                                    return
                            else:
                                print(f"[FP ERROR] Sensor found match at pos {found_position}, but no user found/active in DB for this position on MAC {device_mac}!")
                                if fp_frame.winfo_exists():
                                    fp_frame.after(0, lambda: update_fp_frame_with_failure(fp_frame, "Lỗi dữ liệu người dùng.\nVui lòng liên hệ quản trị viên.", on_close=lambda: on_failure_callback("Lỗi DB/không active") if on_failure_callback else None))
                                return
                        else:
                            print("[FP INFO] No matching fingerprint found by sensor.")
                            if fp_frame.winfo_exists():
                                fp_frame.after(0, lambda: update_fp_frame_with_failure(fp_frame, "Vân tay không khớp.\nVui lòng thử lại.", on_close=lambda: on_failure_callback("Không khớp") if on_failure_callback else None))
                            return 
                    else:
                        print("[FP ERROR] Failed to convert fingerprint image on sensor.")
                        if fp_frame.winfo_exists():
                             fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi xử lý ảnh vân tay.\Thử lại.", on_close=lambda: on_failure_callback("Lỗi convert ảnh sensor") if on_failure_callback else None))
                        return
                except Exception as e_search:
                    print(f"[FP ERROR] Exception during fingerprint search/processing: {e_search}")
                    import traceback
                    traceback.print_exc()
                    if fp_frame.winfo_exists():
                         fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi xử lý vân tay.\Thử lại.", on_close=lambda: on_failure_callback("Lỗi xử lý") if on_failure_callback else None))
                    return
            
            time.sleep(0.1)
            
        if cancel_flag["cancel"]:
            print("[FP INFO] Fingerprint verification cancelled by user.")

    except Exception as e_thread_main:
        print(f"[FP CRITICAL ERROR] Unhandled exception in fingerprint verification thread: {e_thread_main}")
        import traceback
        traceback.print_exc()
        try:
            if fp_frame.winfo_exists():
                fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi hệ thống vân tay.\Liên hệ quản trị.", on_close=lambda: on_failure_callback("Lỗi hệ thống") if on_failure_callback else None))
        except Exception as ui_e_critical:
             print(f"[FP ERROR] Could not update UI after critical thread exception: {ui_e_critical}")
        
        if on_failure_callback and not cancel_flag.get("cancel", False): 
             on_failure_callback("Lỗi hệ thống")