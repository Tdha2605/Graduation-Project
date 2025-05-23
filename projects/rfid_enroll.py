# rfid_enroll.py
import customtkinter as ctk
from customtkinter import CTkImage # Đảm bảo CTkImage được import đúng cách
import threading
import time
import os
from PIL import Image # Cần PIL để mở và resize ảnh cho CTkImage

script_dir = os.path.dirname(os.path.abspath(__file__))



def clear_frame(frame):
    for widget in frame.winfo_children():
        widget.destroy()

def update_rfid_enroll_ui(frame, message, image_path=None, color="white", close_delay=None, on_close=None):
    if not frame or not frame.winfo_exists():
         if on_close and close_delay:
             print("[RFID Enroll WARN] Calling on_close directly due to missing frame.")
             on_close()
         return

    clear_frame(frame)

    if image_path:
        try:
            full_image_path = os.path.join(script_dir, image_path)
            if not os.path.exists(full_image_path):
                 print(f"[RFID Enroll WARN] Image file not found: {full_image_path}")
            else:
                # Sử dụng PIL để mở và resize, sau đó tạo CTkImage
                pil_img = Image.open(full_image_path)
                pil_img_resized = pil_img.resize((1000, 400), Image.Resampling.LANCZOS)
                ctk_img_obj = CTkImage(light_image=pil_img_resized, dark_image=pil_img_resized, size=(150,150))
                
                lbl_img = ctk.CTkLabel(frame, image=ctk_img_obj, text="")
                lbl_img.image = ctk_img_obj # Giữ tham chiếu
                lbl_img.pack(pady=(20, 10))
        except Exception as e:
            print(f"[RFID Enroll WARN] Failed to load image {image_path}: {e}")

    frame.update_idletasks() # Đảm bảo frame có kích thước trước khi tính wraplength
    wrap_len = max(300, frame.winfo_width() - 40 if frame.winfo_width() > 40 else 300)

    lbl_text = ctk.CTkLabel(frame, text=message, font=ctk.CTkFont(size=18, weight="bold"), text_color=color, wraplength=wrap_len)
    lbl_text.pack(pady=(10, 20), expand=True, fill='x')

    if close_delay:
        # Đảm bảo hàm lambda xử lý đúng khi frame không tồn tại hoặc on_close là None
        def close_action():
            if frame.winfo_exists():
                frame.destroy()
            if on_close:
                on_close()
        frame.after(close_delay, close_action)


def enroll_rfid_card(parent, sensor_pn532, on_success_callback=None, on_failure_callback=None, on_cancel_callback=None):
    rfid_enroll_frame = ctk.CTkFrame(parent, fg_color="black")
    rfid_enroll_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
    rfid_enroll_frame.lift()
    cancel_flag = {"cancel": False}

    def cancel_enroll_rfid():
        print("[RFID Enroll INFO] RFID enrollment cancelled by user.")
        cancel_flag["cancel"] = True
        # Luồng sẽ tự hủy frame trong finally block
        if on_cancel_callback:
            on_cancel_callback() # Gọi callback ngay

    cancel_button = ctk.CTkButton(rfid_enroll_frame, text="Hủy", command=cancel_enroll_rfid, width=100, height=35, fg_color="#f44336", hover_color="#e57373")
    cancel_button.pack(pady=10, side="bottom")

    def rfid_scan_thread_func():
        start_time = time.time()
        scan_timeout_seconds = 6 # Tổng thời gian chờ thẻ

        def _on_close_failure_cb_wrapper(reason):
            if on_failure_callback:
                on_failure_callback(reason)

        def _on_close_success_cb_wrapper(uid_hex):
            if on_success_callback:
                on_success_callback(uid_hex)
        
        try:
            if not sensor_pn532:
                print("[RFID Enroll ERROR] Sensor PN532 is None.")
                if rfid_enroll_frame.winfo_exists():
                    rfid_enroll_frame.after(0, lambda: update_rfid_enroll_ui(rfid_enroll_frame, "Lỗi: Đầu đọc RFID không sẵn sàng!", color="red", close_delay=3000, on_close=lambda: _on_close_failure_cb_wrapper("Sensor unavailable")))
                else: _on_close_failure_cb_wrapper("Sensor unavailable")
                return

            # Thử gọi SAM_configuration một lần nữa ở đây để chắc chắn sensor hoạt động
            try:
                sensor_pn532.SAM_configuration()
            except Exception as e_sam:
                print(f"[RFID Enroll ERROR] SAM Configuration failed in thread: {e_sam}")
                if rfid_enroll_frame.winfo_exists():
                    rfid_enroll_frame.after(0, lambda: update_rfid_enroll_ui(rfid_enroll_frame, f"Lỗi cấu hình đầu đọc: {str(e_sam)[:30]}", color="red", close_delay=3000, on_close=lambda: _on_close_failure_cb_wrapper(f"Sensor SAM Config error: {e_sam}")))
                else: _on_close_failure_cb_wrapper(f"Sensor SAM Config error: {e_sam}")
                return

            if rfid_enroll_frame.winfo_exists():
                rfid_enroll_frame.after(0, lambda: update_rfid_enroll_ui(rfid_enroll_frame, "Đưa thẻ RFID vào đầu đọc...", image_path="images/rfid_scan.png", color="white"))

            uid_found_hex = None
            while not cancel_flag["cancel"] and not uid_found_hex:
                if time.time() - start_time > scan_timeout_seconds:
                    print("[RFID Enroll WARN] Timeout waiting for RFID card.")
                    if rfid_enroll_frame.winfo_exists():
                        rfid_enroll_frame.after(0, lambda: update_rfid_enroll_ui(rfid_enroll_frame, "Hết thời gian chờ thẻ.", image_path="images/rfid_error.png", color="orange", close_delay=3000, on_close=lambda: _on_close_failure_cb_wrapper("Timeout")))
                    else: _on_close_failure_cb_wrapper("Timeout")
                    return

                uid_bytes = None
                try:
                    uid_bytes = sensor_pn532.read_passive_target(timeout=0.1) # timeout cho mỗi lần thử đọc (giây)
                except RuntimeError as e_rt: # Lỗi từ thư viện PN532, ví dụ timeout khi không có thẻ
                    # print(f"[RFID Enroll DEBUG] PN532 read error (expected on timeout): {e_rt}")
                    pass # Không làm gì cả, vòng lặp sẽ tiếp tục thử
                except Exception as e_read:
                    print(f"[RFID Enroll ERROR] Unexpected error reading RFID: {e_read}")
                    if rfid_enroll_frame.winfo_exists():
                         rfid_enroll_frame.after(0, lambda: update_rfid_enroll_ui(rfid_enroll_frame, f"Lỗi đọc thẻ: {str(e_read)[:50]}", image_path="images/rfid_error.png", color="red", close_delay=3000, on_close=lambda: _on_close_failure_cb_wrapper(f"Sensor read error: {e_read}")))
                    else: _on_close_failure_cb_wrapper(f"Sensor read error: {e_read}")
                    return
                
                if uid_bytes is not None:
                    if len(uid_bytes) == 4: # Chỉ chấp nhận UID 4 byte
                        uid_found_hex = uid_bytes.hex().upper() # Chuyển sang chuỗi HEX
                        print(f"[RFID Enroll INFO] Card UID: {uid_found_hex}")
                        # Gọi callback thành công trước, sau đó cập nhật UI và đóng
                        _on_close_success_cb_wrapper(uid_found_hex)
                        if rfid_enroll_frame.winfo_exists():
                            rfid_enroll_frame.after(0, lambda uid=uid_found_hex: update_rfid_enroll_ui(rfid_enroll_frame, f"Đã đọc thẻ: {uid}", image_path="images/rfid_success.png", color="green", close_delay=1500, on_close=None)) # on_close=None vì callback đã được gọi
                        return # Thoát luồng khi thành công
                    else:
                        print(f"[RFID Enroll WARN] Ignored non 4-byte UID: {uid_bytes.hex()}")
                        if rfid_enroll_frame.winfo_exists() and not cancel_flag["cancel"]:
                            rfid_enroll_frame.after(0, lambda: update_rfid_enroll_ui(rfid_enroll_frame, "Thẻ không hợp lệ (cần UID 4 byte). Đưa thẻ khác...", image_path="images/rfid_scan.png", color="orange"))
                            time.sleep(1.5) # Đợi chút để người dùng đọc
                            if rfid_enroll_frame.winfo_exists() and not cancel_flag["cancel"]:
                                 rfid_enroll_frame.after(0, lambda: update_rfid_enroll_ui(rfid_enroll_frame, "Đưa thẻ RFID vào đầu đọc...", image_path="images/rfid_scan.png", color="white"))
                
                if cancel_flag["cancel"]:
                    print("[RFID Enroll DEBUG] RFID scan thread cancelled.")
                    break
                time.sleep(0.05) # Nghỉ ngắn giữa các lần thử

            if cancel_flag["cancel"] and not uid_found_hex: # Nếu hủy mà chưa tìm thấy thẻ
                 # Callback on_cancel_callback đã được gọi từ nút Hủy
                 print("[RFID Enroll DEBUG] Exiting scan thread due to cancellation before card found.")


        except Exception as e:
            print(f"[RFID Enroll FATAL] Unhandled exception in RFID thread: {e}")
            if rfid_enroll_frame.winfo_exists():
                rfid_enroll_frame.after(0, lambda: update_rfid_enroll_ui(rfid_enroll_frame, "Lỗi không xác định", color="red", close_delay=3000, on_close=lambda: _on_close_failure_cb_wrapper("Unknown thread error")))
            else: _on_close_failure_cb_wrapper("Unknown thread error")
        finally:
            # Không cần gọi power_down ở đây, main_app sẽ quản lý vòng đời của sensor
            # if sensor_pn532 and hasattr(sensor_pn532, 'power_down'):
            #     try: sensor_pn532.power_down()
            #     except: pass
            if rfid_enroll_frame.winfo_exists():
                 rfid_enroll_frame.after(0, rfid_enroll_frame.destroy)


    threading.Thread(target=rfid_scan_thread_func, daemon=True).start()