import customtkinter as ctk
from customtkinter import CTkImage
import threading
import time
import os
from PIL import Image

script_dir = os.path.dirname(os.path.abspath(__file__))

def clear_frame_content(frame):
    """Xóa các widget con của một frame."""
    for widget in frame.winfo_children():
        widget.destroy()

def update_rfid_auth_ui(frame, message, image_path=None, color="white", duration=None, on_close_callback=None):
    if not frame or not frame.winfo_exists():
        if on_close_callback and duration:
            print("[RFID Auth WARN] Calling on_close_callback directly due to missing frame.")
            on_close_callback()
        return

    clear_frame_content(frame)

    if image_path:
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            full_image_path = os.path.join(base_dir, "images", image_path)
            
            if not os.path.exists(full_image_path):
                print(f"[RFID Auth WARN] Image file not found: {full_image_path}")
            else:
                pil_img = Image.open(full_image_path)
                pil_img_resized = pil_img.resize((300, 300), Image.Resampling.LANCZOS) 
                ctk_img_obj = CTkImage(light_image=pil_img_resized, dark_image=pil_img_resized, size=(300, 300))

                lbl_img = ctk.CTkLabel(frame, image=ctk_img_obj, text="")
                lbl_img.image = ctk_img_obj 
                lbl_img.pack(pady=(10, 5))
        except Exception as e:
            print(f"[RFID Auth WARN] Failed to load image {image_path}: {e}")

    frame.update_idletasks()
    wrap_len = max(350, frame.winfo_width() - 20 if frame.winfo_width() > 20 else 350)

    lbl_text = ctk.CTkLabel(frame, text=message, font=ctk.CTkFont(size=30, weight="bold"), text_color=color, wraplength=wrap_len)
    lbl_text.pack(pady=(5, 10), expand=True, fill='x')

    if duration and on_close_callback:
        # Đảm bảo on_close_callback chỉ được gọi một lần và frame còn tồn tại
        def timed_action():
            if frame.winfo_exists():
                on_close_callback()
        frame.after(duration, timed_action)


def start_rfid_authentication_scan(parent_ui_element, sensor_pn532, on_success_callback, on_failure_callback):
    rfid_scan_frame = ctk.CTkFrame(parent_ui_element, fg_color="gray15", corner_radius=12, border_width=1, border_color="gray40", width=300, height=200)
    rfid_scan_frame.place(relx=0.5, rely=0.5, anchor="center")
    rfid_scan_frame.lift()


    def _handle_ui_close_and_callback(callback_func, *args):
        if rfid_scan_frame.winfo_exists():
            rfid_scan_frame.destroy()
        if callback_func:
            callback_func(*args)

    def rfid_scan_thread_func():
        if not sensor_pn532:
            print("[RFID Auth ERROR] Sensor PN532 is None for authentication.")
            parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, "Lỗi: Đầu đọc RFID\nkhông sẵn sàng!", color="red", image_path="rfid_error.png", duration=3000, on_close_callback=lambda: _handle_ui_close_and_callback(on_failure_callback, "Sensor unavailable")))
            return

        try:
            sensor_pn532.SAM_configuration()
        except Exception as e_sam:
            print(f"[RFID Auth ERROR] SAM Configuration failed: {e_sam}")
            parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, f"Lỗi cấu hình\nđầu đọc RFID!", color="red", image_path="rfid_error.png", duration=3000, on_close_callback=lambda: _handle_ui_close_and_callback(on_failure_callback, f"Sensor SAM Config error: {e_sam}")))
            return

        parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, "Đưa thẻ RFID\nvào để xác thực...", image_path="rfid_scan.png", color="white"))

        uid_found_hex = None
        scan_attempts = 0
        max_scan_attempts_without_card = 50

        while not uid_found_hex and scan_attempts < max_scan_attempts_without_card:
            if not rfid_scan_frame.winfo_exists():
                print("[RFID Auth INFO] Scan UI frame destroyed, stopping scan thread.")
                _handle_ui_close_and_callback(on_failure_callback, "UI closed") 
                return

            uid_bytes = None
            try:
                uid_bytes = sensor_pn532.read_passive_target(timeout=0.1) 
            except RuntimeError:
                scan_attempts += 1
                print({scan_attempts})
                pass 
            except Exception as e_read:
                print(f"[RFID Auth ERROR] Unexpected error reading RFID: {e_read}")
                parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, f"Lỗi đọc thẻ!", image_path="rfid_error.png", color="red", duration=3000, on_close_callback=lambda: _handle_ui_close_and_callback(on_failure_callback, f"Sensor read error: {e_read}")))
                return

            if uid_bytes is not None:
                if len(uid_bytes) == 4: 
                    uid_found_hex = uid_bytes.hex().upper()
                    print(f"[RFID Auth INFO] Card UID Scanned: {uid_found_hex}")
                    parent_ui_element.after(0, lambda uid=uid_found_hex: _handle_ui_close_and_callback(on_success_callback, uid))
                    return
                else:
                    print(f"[RFID Auth WARN] Ignored non 4-byte UID: {uid_bytes.hex()}")
                    if rfid_scan_frame.winfo_exists():
                        parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, "Thẻ không hợp lệ.\n(Cần UID 4 byte)", image_path="rfid_scan.png", color="orange"))
                        time.sleep(2) 
                        if rfid_scan_frame.winfo_exists(): 
                            parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, "Đưa thẻ RFID\nvào để xác thực...", image_path="rfid_scan.png", color="white"))
            else:
                scan_attempts += 1
            time.sleep(0.02) 

        if not uid_found_hex: 
            print("[RFID Auth WARN] Max scan attempts reached without finding a card.")
            parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, "Không phát hiện thẻ.\nVui lòng thử lại.", image_path="rfid_error.png", color="orange", duration=3000, on_close_callback=lambda: _handle_ui_close_and_callback(on_failure_callback, "Timeout or no card")))

    threading.Thread(target=rfid_scan_thread_func, daemon=True).start()
    return rfid_scan_frame 