# rfid.py (cho thiết bị xác thực)
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
    """
    Cập nhật UI cho frame hiển thị trạng thái quét RFID.
    frame: CTkFrame được cung cấp từ main.py để hiển thị.
    duration: Thời gian hiển thị thông báo trước khi gọi on_close_callback (ms).
    on_close_callback: Hàm được gọi sau khi hết duration.
    """
    if not frame or not frame.winfo_exists():
        if on_close_callback and duration:
            print("[RFID Auth WARN] Calling on_close_callback directly due to missing frame.")
            on_close_callback()
        return

    clear_frame_content(frame)

    if image_path:
        try:
            # Giả định ảnh nằm trong thư mục 'images' cùng cấp với rfid.py hoặc main.py
            # Nếu cấu trúc khác, cần điều chỉnh đường dẫn này
            base_dir = os.path.dirname(os.path.abspath(frame.winfo_toplevel().tk.eval('info script'))) # Lấy dir của script main
            full_image_path = os.path.join(base_dir, "images", image_path)
            
            if not os.path.exists(full_image_path):
                print(f"[RFID Auth WARN] Image file not found: {full_image_path}")
            else:
                pil_img = Image.open(full_image_path)
                pil_img_resized = pil_img.resize((100, 100), Image.Resampling.LANCZOS) # Kích thước ảnh cho UI quét
                ctk_img_obj = CTkImage(light_image=pil_img_resized, dark_image=pil_img_resized, size=(100, 100))

                lbl_img = ctk.CTkLabel(frame, image=ctk_img_obj, text="")
                lbl_img.image = ctk_img_obj # Giữ tham chiếu
                lbl_img.pack(pady=(10, 5))
        except Exception as e:
            print(f"[RFID Auth WARN] Failed to load image {image_path}: {e}")

    frame.update_idletasks()
    wrap_len = max(250, frame.winfo_width() - 20 if frame.winfo_width() > 20 else 250)

    lbl_text = ctk.CTkLabel(frame, text=message, font=ctk.CTkFont(size=15, weight="bold"), text_color=color, wraplength=wrap_len)
    lbl_text.pack(pady=(5, 10), expand=True, fill='x')

    if duration and on_close_callback:
        # Đảm bảo on_close_callback chỉ được gọi một lần và frame còn tồn tại
        def timed_action():
            if frame.winfo_exists():
                on_close_callback()
        frame.after(duration, timed_action)


def start_rfid_authentication_scan(parent_ui_element, sensor_pn532, on_success_callback, on_failure_callback):
    """
    Khởi tạo giao diện và luồng quét RFID để xác thực.
    parent_ui_element: Widget cha (thường là root window) nơi UI quét sẽ hiển thị.
    sensor_pn532: Đối tượng cảm biến PN532 đã được khởi tạo.
    on_success_callback: Hàm callback khi quét UID thành công (nhận uid_hex).
    on_failure_callback: Hàm callback khi quét thất bại hoặc timeout (nhận reason).
    """
    rfid_scan_frame = ctk.CTkFrame(parent_ui_element, fg_color="gray15", corner_radius=12, border_width=1, border_color="gray40")
    # Vị trí và kích thước frame này có thể cần điều chỉnh
    rfid_scan_frame.place(relx=0.5, rely=0.5, anchor="center", width=300, height=200)
    rfid_scan_frame.lift()

    # Biến cờ để dừng luồng từ bên ngoài nếu cần (ví dụ: khi thoát ứng dụng)
    # Trong trường hợp này, nó sẽ tự đóng khi có kết quả hoặc lỗi.
    # Tuy nhiên, để nhất quán, chúng ta có thể truyền một đối tượng Event() từ main.py nếu muốn kiểm soát chặt chẽ hơn.
    # Hiện tại, sẽ để luồng tự kết thúc.

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
        max_scan_attempts_without_card = 300 # Khoảng 30s (300 * 0.1s timeout) - để tránh vòng lặp vô hạn nếu sensor lỗi âm thầm

        while not uid_found_hex and scan_attempts < max_scan_attempts_without_card:
            # Kiểm tra xem frame UI còn tồn tại không, nếu không thì dừng luồng
            if not rfid_scan_frame.winfo_exists():
                print("[RFID Auth INFO] Scan UI frame destroyed, stopping scan thread.")
                _handle_ui_close_and_callback(on_failure_callback, "UI closed") # Gọi failure callback
                return

            uid_bytes = None
            try:
                uid_bytes = sensor_pn532.read_passive_target(timeout=0.1) # 100ms timeout cho mỗi lần đọc
            except RuntimeError:
                scan_attempts += 1
                pass # Timeout, không có thẻ
            except Exception as e_read:
                print(f"[RFID Auth ERROR] Unexpected error reading RFID: {e_read}")
                parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, f"Lỗi đọc thẻ!", image_path="rfid_error.png", color="red", duration=3000, on_close_callback=lambda: _handle_ui_close_and_callback(on_failure_callback, f"Sensor read error: {e_read}")))
                return

            if uid_bytes is not None:
                if len(uid_bytes) == 4: # Chỉ chấp nhận UID 4 byte
                    uid_found_hex = uid_bytes.hex().upper()
                    print(f"[RFID Auth INFO] Card UID Scanned: {uid_found_hex}")
                    parent_ui_element.after(0, lambda uid=uid_found_hex: _handle_ui_close_and_callback(on_success_callback, uid))
                    return
                else:
                    print(f"[RFID Auth WARN] Ignored non 4-byte UID: {uid_bytes.hex()}")
                    if rfid_scan_frame.winfo_exists():
                        parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, "Thẻ không hợp lệ.\n(Cần UID 4 byte)", image_path="rfid_scan.png", color="orange"))
                        time.sleep(2) # Chờ người dùng đọc
                        if rfid_scan_frame.winfo_exists(): # Kiểm tra lại frame
                            parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, "Đưa thẻ RFID\nvào để xác thực...", image_path="rfid_scan.png", color="white"))
            
            time.sleep(0.02) # Nghỉ ngắn để giảm CPU usage

        if not uid_found_hex: # Nếu thoát vòng lặp mà không tìm thấy thẻ (do timeout max_scan_attempts)
            print("[RFID Auth WARN] Max scan attempts reached without finding a card.")
            parent_ui_element.after(0, lambda: update_rfid_auth_ui(rfid_scan_frame, "Không phát hiện thẻ.\nVui lòng thử lại.", image_path="rfid_error.png", color="orange", duration=3000, on_close_callback=lambda: _handle_ui_close_and_callback(on_failure_callback, "Timeout or no card")))

    threading.Thread(target=rfid_scan_thread_func, daemon=True).start()
    return rfid_scan_frame # Trả về frame để main.py có thể quản lý (ví dụ: hủy khi cần)