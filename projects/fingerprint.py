# fingerprint.py
import customtkinter as ctk
import threading
import time
from datetime import datetime, timezone, time as dt_time, date as dt_date # Import date/time for validity check
from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1, FINGERPRINT_CHARBUFFER2 # Keep for sensor interaction
from PIL import Image
import database  # Use the updated database module

# Default sensor port (adjust if needed)
DEFAULT_FINGERPRINT_PORT = '/dev/ttyAMA0'
# Default sensor baud rate (adjust if needed)
DEFAULT_FINGERPRINT_BAUDRATE = 57600
# Sensor search confidence threshold (adjust based on testing)
SENSOR_SEARCH_CONFIDENCE = 50 # Example value, needs tuning

# --- UI Helper Functions (largely unchanged) ---

def clear_frame(frame):
    for widget in frame.winfo_children():
        widget.destroy()

def update_fp_frame_with_error(fp_frame, message="Lỗi phần cứng", on_close=None):
    """Displays a hardware/general error message."""
    clear_frame(fp_frame)
    try:
        # Consider a more generic error image
        img = Image.open("/home/anhtd/projects/images/fp_failure.png")
    except Exception:
        img = Image.new("RGB", (1024, 600), color="orange") # Orange for general error
    ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(1024, 600)) # Adjust size if needed
    lbl_img = ctk.CTkLabel(fp_frame, image=ctk_img, text="")
    lbl_img.image = ctk_img
    lbl_img.pack(pady=(10, 10), expand=True, fill="both") # Make image fill more space

    lbl_text = ctk.CTkLabel(fp_frame, text=message, font=ctk.CTkFont(size=18, weight="bold"), text_color="orange")
    lbl_text.pack(pady=(0, 20))

    # Auto-close after a delay
    fp_frame.after(2500, lambda: (fp_frame.destroy(), on_close() if on_close else None))


def update_fp_frame_with_success(fp_frame, user_name="", on_close=None):
    """Displays success message."""
    clear_frame(fp_frame)
    try:
        success_img = Image.open("/home/anhtd/projects/images/fp_success.png")
    except Exception:
        success_img = Image.new("RGB", (1024, 600), color="green")
    ctk_success = ctk.CTkImage(light_image=success_img, dark_image=success_img, size=(1024, 600)) # Adjust size
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_success, text="")
    icon_label.image = ctk_success
    icon_label.pack(pady=(10, 10), expand=True, fill="both")

    success_text = f"Xác thực thành công!\n{user_name}" if user_name else "Xác thực thành công!"
    text_label = ctk.CTkLabel(fp_frame, text=success_text, font=ctk.CTkFont(size=18, weight="bold"), text_color="green")
    text_label.pack(pady=(0, 20))

    fp_frame.after(2000, lambda: (fp_frame.destroy(), on_close() if on_close else None))


def update_fp_frame_with_failure(fp_frame, message="Không tìm thấy vân tay hoặc không hợp lệ", on_close=None):
    """Displays failure/not found/invalid message."""
    clear_frame(fp_frame)
    try:
        fail_img = Image.open("/home/anhtd/projects/images/fp_error.png")
    except Exception:
        fail_img = Image.new("RGB", (1024, 600), color="red")
    ctk_fail = ctk.CTkImage(light_image=fail_img, dark_image=fail_img, size=(1024, 600)) # Adjust size
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_fail, text="")
    icon_label.image = ctk_fail
    icon_label.pack(pady=(10, 10), expand=True, fill="both")

    text_label = ctk.CTkLabel(fp_frame, text=message, font=ctk.CTkFont(size=18, weight="bold"), text_color="red")
    text_label.pack(pady=(0, 20))

    fp_frame.after(2500, lambda: (fp_frame.destroy(), on_close() if on_close else None))


def set_prompt_state(fp_frame, cancel_callback):
    """Sets initial prompt state with cancel button."""
    clear_frame(fp_frame)
    try:
        img_grey = Image.open("/home/anhtd/projects/images/fp_initial.png")
    except Exception:
        img_grey = Image.new("RGB", (1024, 600), color="gray")
    ctk_img_grey = ctk.CTkImage(light_image=img_grey, dark_image=img_grey, size=(1024, 600)) # Adjust size
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_img_grey, text="")
    icon_label.image = ctk_img_grey
    icon_label.pack(pady=(0, 0), expand=True, fill="both")

    text_label = ctk.CTkLabel(fp_frame, text="Đặt ngón tay lên cảm biến", font=ctk.CTkFont(size=16, weight="bold"), text_color="white")
    text_label.pack(pady=(0, 20))

    cancel_button = ctk.CTkButton(fp_frame, text="Hủy", command=cancel_callback)
    cancel_button.pack(pady=10)


def set_scanning_state(fp_frame, cancel_callback):
    """Sets scanning state UI."""
    clear_frame(fp_frame)
    try:
        img_blue = Image.open("/home/anhtd/projects/images/fp_scanning.png")
    except Exception:
        img_blue = Image.new("RGB", (1024, 600), color="blue")
    ctk_img_blue = ctk.CTkImage(light_image=img_blue, dark_image=img_blue, size=(1024, 600)) # Adjust size
    icon_label = ctk.CTkLabel(fp_frame, image=ctk_img_blue, text="")
    icon_label.image = ctk_img_blue
    icon_label.pack(pady=(10, 10), expand=True, fill="both")

    text_label = ctk.CTkLabel(fp_frame, text="Đang quét và tìm kiếm...", font=ctk.CTkFont(size=16, weight="bold"))
    text_label.pack(pady=(0, 20))

    cancel_button = ctk.CTkButton(fp_frame, text="Hủy", command=cancel_callback)
    cancel_button.pack(pady=10)
    

# --- REMOVED ---
# load_finger_db()
# normalize()
# finger_db global variable
# FINGERPRINT_THRESHOLD (using sensor's confidence now)
# --- END REMOVED ---

# --- NEW: Validity Check Function ---
def is_currently_valid(user_info):
    """Checks if the user record from DB is valid at the current time."""
    if not user_info:
        return False
    try:
        now = datetime.now(timezone.utc)
        current_date_str = now.strftime('%Y-%m-%d')
        current_time_str = now.strftime('%H:%M:%S')
        current_day_index = now.weekday() # Monday is 0

        # 1. Check Date Validity
        if user_info['valid_from_date'] and current_date_str < user_info['valid_from_date']: return False
        if user_info['valid_to_date'] and current_date_str > user_info['valid_to_date']: return False

        # 2. Check Active Day Mask
        mask = user_info['active_days_mask']
        if not mask or len(mask) != 7 or mask[current_day_index] != '1': return False

        # 3. Check Time Validity
        if user_info['valid_from_time'] and current_time_str < user_info['valid_from_time']: return False
        if user_info['valid_to_time'] and current_time_str >= user_info['valid_to_time']: return False # End time is exclusive

        return True
    except Exception as e:
        print(f"[ERROR] Exception during validity check for bioId {user_info.get('bio_id', 'N/A')}: {e}")
        return False

# --- Updated Fingerprint Workflow ---

def open_fingerprint_prompt(parent, sensor, on_success_callback=None, on_failure_callback=None):
    """
    Initiates the fingerprint prompt using sensor-based 1:N search.

    :param parent: Parent widget (for the prompt frame).
    :param sensor: An initialized and verified PyFingerprint sensor object.
    :param on_success_callback: Function accepting bio_id on successful + valid recognition.
    :param on_failure_callback: Function called on failure, timeout, or invalid time.
    """
    fp_frame = ctk.CTkFrame(parent, fg_color="transparent")
    fp_frame.pack(expand=True, fill="both")

    # Flag to signal cancellation from the UI button
    cancel_flag = {"cancel": False}
    def cancel_scan():
        print("[INFO] Fingerprint scan cancelled by user.")
        cancel_flag["cancel"] = True
        # Ensure frame is destroyed even if thread is stuck briefly
        if fp_frame.winfo_exists():
             fp_frame.destroy()
        # Optionally call failure callback on cancel
        if on_failure_callback:
             on_failure_callback()


    set_prompt_state(fp_frame, cancel_callback=cancel_scan)

    # Start background thread for sensor interaction
    threading.Thread(target=perform_fingerprint_verification,
                     args=(fp_frame, sensor, cancel_flag, on_success_callback, on_failure_callback),
                     daemon=True).start()

def perform_fingerprint_verification(fp_frame, sensor, cancel_flag, on_success_callback, on_failure_callback):
    """
    Background thread function to handle fingerprint scanning and verification using the sensor.
    """
    start_time = time.time()
    timeout_seconds = 15 # Increased timeout for potentially slower sensor search

    try:
        # Initial sensor check (already done outside ideally, but double-check)
        if not sensor or not sensor.verifyPassword():
             print("[ERROR] Fingerprint sensor not available or password incorrect at verification start.")
             if fp_frame.winfo_exists():
                 fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi cảm biến vân tay", on_close=on_failure_callback))
             return

        print("[INFO] Waiting for finger...")
        last_ui_update_time = 0

        while not cancel_flag["cancel"]:
            # Check for timeout
            if time.time() - start_time > timeout_seconds:
                print("[WARN] Fingerprint scan timed out.")
                if fp_frame.winfo_exists():
                    fp_frame.after(0, lambda: update_fp_frame_with_failure(fp_frame, "Quá thời gian chờ", on_close=on_failure_callback))
                return

            finger_detected = False
            try:
                finger_detected = sensor.readImage()
            except Exception as e:
                 print(f"[ERROR] Exception reading fingerprint image: {e}")
                 # Handle potential serial communication errors, etc.
                 if fp_frame.winfo_exists():
                     fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi đọc cảm biến", on_close=on_failure_callback))
                 return # Exit thread on sensor read error

            if finger_detected:
                print("[INFO] Finger detected. Processing...")
                # Update UI to scanning state (if frame still exists)
                if fp_frame.winfo_exists():
                    # Use the cancel callback associated with the scanning state
                    fp_frame.after(0, lambda: set_scanning_state(fp_frame, cancel_callback=lambda: (cancel_flag.update({"cancel": True}), fp_frame.destroy() if fp_frame.winfo_exists() else None)))

                try:
                    # Convert image in buffer 1
                    if sensor.convertImage(FINGERPRINT_CHARBUFFER1):
                        # Search the template database on the sensor
                        result = sensor.searchTemplate()
                        position = result[0]
                        accuracy = result[1]

                        print(f"[DEBUG] Sensor search result - Position: {position}, Accuracy: {accuracy}")

                        if position >= 0 and accuracy >= SENSOR_SEARCH_CONFIDENCE:
                            print(f"[INFO] Match found by sensor at position {position} with score {accuracy}.")
                            # Match found on sensor, now check database and validity
                            user_info = database.get_user_info_by_finger_position(position)

                            if user_info:
                                if is_currently_valid(user_info):
                                    print(f"[INFO] User {user_info['bio_id']} ({user_info['person_name']}) is valid.")
                                    if fp_frame.winfo_exists():
                                        fp_frame.after(0, lambda: update_fp_frame_with_success(fp_frame, user_info['person_name'], on_close=lambda: on_success_callback(user_info['bio_id']) if on_success_callback else None))
                                    return # Success, exit thread
                                else:
                                    print(f"[WARN] Fingerprint match for {user_info['bio_id']}, but user is not currently valid (time/date/day).")
                                    if fp_frame.winfo_exists():
                                        fp_frame.after(2000, lambda: update_fp_frame_with_failure(fp_frame, "Vân tay đúng, nhưng không hợp lệ", on_close=on_failure_callback))
                                    return # Failure (invalid), exit thread
                            else:
                                print(f"[ERROR] Sensor found match at position {position}, but no user found in DB for this position!")
                                # This indicates a sync issue between sensor and DB
                                if fp_frame.winfo_exists():
                                    fp_frame.after(1500, lambda: update_fp_frame_with_failure(fp_frame, "Lỗi dữ liệu người dùng", on_close=on_failure_callback))
                                return # Failure (DB error), exit thread
                        else:
                            # No match found by sensor
                            print("[INFO] No matching fingerprint found by sensor.")
                            if fp_frame.winfo_exists():
                                fp_frame.after(2000, lambda: update_fp_frame_with_failure(fp_frame, "Vân tay không khớp", on_close=on_failure_callback))
                            return # Failure (no match), exit thread

                    else:
                        print("[ERROR] Failed to convert fingerprint image on sensor.")
                        if fp_frame.winfo_exists():
                             fp_frame.after(2000, lambda: update_fp_frame_with_error(fp_frame, "Lỗi xử lý ảnh vân tay", on_close=on_failure_callback))
                        return # Exit thread on sensor processing error

                except Exception as e:
                    print(f"[ERROR] Exception during fingerprint search/processing: {e}")
                    if fp_frame.winfo_exists():
                         fp_frame.after(1000, lambda: update_fp_frame_with_error(fp_frame, "Lỗi xử lý vân tay", on_close=on_failure_callback))
                    return # Exit thread on error

            # Small delay to prevent busy-waiting
            time.sleep(0.1)

        # Loop exited due to cancellation
        print("[INFO] Fingerprint verification thread finished or cancelled.")
        # Ensure frame is destroyed if cancelled mid-operation
        if cancel_flag["cancel"] and fp_frame.winfo_exists():
             fp_frame.destroy()


    except Exception as e:
        print(f"[ERROR] Unhandled exception in fingerprint verification thread: {e}")
        # Try to update UI with generic error if possible
        try:
            if fp_frame.winfo_exists():
                fp_frame.after(0, lambda: update_fp_frame_with_error(fp_frame, "Lỗi không xác định", on_close=on_failure_callback))
        except Exception as ui_e:
             print(f"[ERROR] Could not update UI after thread exception: {ui_e}")
        # Optionally call failure callback directly
        if on_failure_callback:
             on_failure_callback()


# --- REMOVED ---
# open_fingerprint_scanning (merged into perform_fingerprint_verification)
# --- END REMOVED ---
