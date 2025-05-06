import customtkinter as ctk
from customtkinter import CTkImage
import threading
import time
from datetime import datetime
from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1, FINGERPRINT_CHARBUFFER2
from PIL import Image, ImageTk
import base64
import io
import os

script_dir = os.path.dirname(os.path.abspath(__file__))

def clear_frame(frame):
    for widget in frame.winfo_children():
        widget.destroy()

def update_enroll_ui(frame, message, image_path=None, color="white", close_delay=None, on_close=None):
    if not frame or not frame.winfo_exists():
         print("[FP Enroll WARN] Attempted to update non-existent frame.")
         if on_close and close_delay:
             print("[FP Enroll INFO] Calling on_close directly due to missing frame.")
             on_close()
         return

    clear_frame(frame)

    if image_path:
        try:
            full_image_path = os.path.join(script_dir, image_path)
            if not os.path.exists(full_image_path):
                 print(f"[FP Enroll WARN] Image file not found: {full_image_path}")
            else:
                img = Image.open(full_image_path)
                img = img.resize((150, 150), Image.Resampling.LANCZOS)
                ctk_img = CTkImage(light_image=img, dark_image=img, size=img.size)
                lbl_img = ctk.CTkLabel(frame, image=ctk_img, text="")
                lbl_img.image = ctk_img
                lbl_img.pack(pady=(20, 10))
        except Exception as e:
            print(f"[FP Enroll WARN] Failed to load image {image_path}: {e}")

    frame.update_idletasks()
    wrap_len = max(300, frame.winfo_width() - 40)

    lbl_text = ctk.CTkLabel(frame, text=message, font=ctk.CTkFont(size=18, weight="bold"), text_color=color, wraplength=wrap_len)
    lbl_text.pack(pady=(10, 20), expand=True, fill='x')

    if close_delay:
        close_func = lambda: (frame.destroy() if frame.winfo_exists() else None, on_close() if on_close else None)
        frame.after(close_delay, close_func)


def enroll_fingerprint_template(parent, sensor, on_success_callback=None, on_failure_callback=None, on_cancel_callback=None):
    fp_enroll_frame = ctk.CTkFrame(parent, fg_color="black")
    fp_enroll_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
    fp_enroll_frame.lift()

    cancel_flag = {"cancel": False}

    def cancel_enroll():
        print("[FP Enroll INFO] Fingerprint enrollment cancelled by user.")
        cancel_flag["cancel"] = True
        if fp_enroll_frame.winfo_exists():
             fp_enroll_frame.after(0, fp_enroll_frame.destroy)
        if on_cancel_callback:
            on_cancel_callback()

    cancel_button = ctk.CTkButton(fp_enroll_frame, text="Hủy", command=cancel_enroll, width=100, height=35, fg_color="#f44336", hover_color="#e57373")
    cancel_button.pack(pady=10, side="bottom")

    threading.Thread(target=perform_single_scan_enrollment,
                     args=(fp_enroll_frame, sensor, cancel_flag, on_success_callback, on_failure_callback),
                     daemon=True).start()

def perform_single_scan_enrollment(fp_frame, sensor, cancel_flag, on_success_callback, on_failure_callback):
    start_time = time.time()
    timeout_seconds = 20

    on_close_failure = lambda reason: on_failure_callback(reason) if on_failure_callback else None
    on_close_success = lambda tmpl: on_success_callback(tmpl) if on_success_callback else None

    try:
        if not sensor or not sensor.verifyPassword():
             print("[FP Enroll ERROR] Sensor not available or password incorrect.")
             if fp_frame.winfo_exists():
                 fp_frame.after(0, lambda: update_enroll_ui(fp_frame, "Lỗi: Cảm biến vân tay không sẵn sàng!", color="red", close_delay=3000, on_close=lambda: on_close_failure("Sensor unavailable")))
             else: on_close_failure("Sensor unavailable")
             return

        if fp_frame.winfo_exists():
            fp_frame.after(0, lambda: update_enroll_ui(fp_frame, "Vui lòng đặt ngón tay lên cảm biến...", image_path="images/fp_initial.png", color="white"))

        finger_detected = False
        while not cancel_flag["cancel"] and not finger_detected:
            if time.time() - start_time > timeout_seconds:
                print("[FP Enroll WARN] Timeout waiting for finger placement.")
                if fp_frame.winfo_exists():
                    fp_frame.after(0, lambda: update_enroll_ui(fp_frame, "Quá thời gian chờ đặt ngón tay.", color="orange", close_delay=3000, on_close=lambda: on_close_failure("Timeout")))
                else: on_close_failure("Timeout")
                return

            try:
                finger_detected = sensor.readImage()
            except Exception as e:
                 print(f"[FP Enroll ERROR] Exception reading fingerprint image: {e}")
                 if fp_frame.winfo_exists():
                     fp_frame.after(0, lambda: update_enroll_ui(fp_frame, f"Lỗi đọc cảm biến: {e}", color="red", close_delay=3000, on_close=lambda: on_close_failure("Sensor read error")))
                 else: on_close_failure("Sensor read error")
                 return
            time.sleep(0.1)

        if cancel_flag["cancel"]: return

        if fp_frame.winfo_exists():
             fp_frame.after(0, lambda: update_enroll_ui(fp_frame, "Đang xử lý...", image_path="images/fp_scanning.png", color="cyan"))

        try:
            if sensor.convertImage(FINGERPRINT_CHARBUFFER1):
                print("[FP Enroll INFO] Image converted successfully.")
                characteristics = sensor.downloadCharacteristics()
                if characteristics and isinstance(characteristics, list):
                    print(f"[FP Enroll INFO] Template characteristics downloaded successfully (Length: {len(characteristics)} bytes).")
                    template_bytes = bytes(characteristics)
                    template_base64 = base64.b64encode(template_bytes).decode('utf-8')
                    print("[FP Enroll INFO] Template base64: ", template_base64[:50], "...")
                    print("[FP Enroll DEBUG] Calling success callback immediately.")
                    try:
                        if on_close_success: # Đảm bảo callback tồn tại
                             on_close_success(template_base64) # Gọi trực tiếp hàm on_success_callback của main_enroll
                             print("[FP Enroll DEBUG] Success callback invoked.")
                        else:
                             print("[FP Enroll WARN] on_close_success (on_success_callback) is None.")
                    except Exception as cb_err:
                        print(f"[FP Enroll ERROR] Exception calling on_close_success: {cb_err}")
                        # Nếu gọi callback lỗi, nên gọi failure callback
                        if on_close_failure:
                             on_close_failure(f"Callback execution error: {cb_err}")
                        return # Thoát khỏi hàm nếu callback lỗi

                    # 2. Lên lịch cập nhật UI và đóng frame sau đó (không cần truyền callback vào on_close nữa)
                    try:
                        if fp_frame.winfo_exists():
                             print("[FP Enroll INFO] Scheduling UI update for success message and frame destruction.")
                             # Chỉ cần hủy frame sau delay, không cần gọi lại callback ở đây
                             fp_frame.after(100, lambda: update_enroll_ui(fp_frame, "Đăng ký vân tay thành công!", image_path="images/fp_success.png", color="green", close_delay=1500, on_close=None)) # Giảm delay, on_close=None
                        else:
                             print("[FP Enroll WARN] fp_frame destroyed before scheduling success UI.")
                    except Exception as e_after:
                         print(f"[FP Enroll ERROR] Exception scheduling success UI: {e_after}")
                         # Không cần gọi failure callback lần nữa vì success đã được gọi

                    return # Kết thúc hàm sau khi xử lý thành công
                else:
                    print("[FP Enroll ERROR] Failed to download characteristics from sensor buffer.")
                    if fp_frame.winfo_exists():
                         fp_frame.after(0, lambda: update_enroll_ui(fp_frame, "Lỗi: Không lấy được dữ liệu vân tay.", color="red", close_delay=3000, on_close=lambda: on_close_failure("Download characteristics failed")))
                    else: on_close_failure("Download characteristics failed")
                    return
            else:
                print("[FP Enroll ERROR] Failed to convert fingerprint image. Image quality might be poor.")
                if fp_frame.winfo_exists():
                     fp_frame.after(0, lambda: update_enroll_ui(fp_frame, "Lỗi: Chất lượng ảnh vân tay kém. Vui lòng thử lại.", image_path="images/fp_error.png", color="orange", close_delay=3500, on_close=lambda: on_close_failure("Image conversion failed")))
                else: on_close_failure("Image conversion failed")
                return
        except Exception as e:
            print(f"[FP Enroll ERROR] Exception during template processing: {e}")
            if fp_frame.winfo_exists():
                 fp_frame.after(0, lambda: update_enroll_ui(fp_frame, f"Lỗi xử lý vân tay: {e}", color="red", close_delay=3000, on_close=lambda: on_close_failure(f"Processing error: {e}")))
            else: on_close_failure(f"Processing error: {e}")
            return

    except Exception as e:
        print(f"[FP Enroll ERROR] Unhandled exception in fingerprint enrollment thread: {e}")
        try:
            if fp_frame.winfo_exists():
                fp_frame.after(0, lambda: update_enroll_ui(fp_frame, "Lỗi không xác định", color="red", close_delay=3000, on_close=lambda: on_close_failure("Unknown thread error")))
            else: on_close_failure("Unknown thread error")
        except Exception as ui_e:
             print(f"[FP Enroll ERROR] Could not update UI after thread exception: {ui_e}")
             on_close_failure("Unknown thread error")
    finally:
        if fp_frame.winfo_exists():
            fp_frame.after(0, fp_frame.destroy)