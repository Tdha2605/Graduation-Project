import os
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir) 

import json
import uuid
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image
from customtkinter import CTkImage
from datetime import datetime, timezone, timedelta, time as dt_time, date as dt_date
import io
import base64
import calendar

import face_enroll
import fingerprint_enroll
from mqtt_enroll import MQTTEnrollManager 
import database_enroll
import rfid_enroll 

try:
    from pyfingerprint.pyfingerprint import PyFingerprint
except ImportError:
    PyFingerprint = None
    print("[Enroll WARN] PyFingerprint library not found, fingerprint enrollment disabled.")
except Exception as e:
    PyFingerprint = None
    print(f"[Enroll ERROR] Error importing PyFingerprint: {e}")


try:
    import board 
    import busio 
    from adafruit_pn532.i2c import PN532_I2C
except ImportError:
    PN532_I2C = None
    board = None
    busio = None
    print("[Enroll WARN] Adafruit PN532/Blinka libraries not found, RFID enrollment disabled.")
except Exception as e_pn532_import:
    PN532_I2C = None
    board = None
    busio = None
    print(f"[Enroll ERROR] Error importing PN532/Blinka libraries: {e_pn532_import}")


DEBUG = True
BG_COLOR = "#F0F0F0" 
SCREEN_BG_COLOR = "#E0E0E0" 
ACCENT_COLOR = "#007AFF"
BUTTON_FG_TEXT = "#FFFFFF"
SUCCESS_COLOR = "#34C759"
WARNING_COLOR = "#FF9500"
ERROR_COLOR = "#FF3B30"


TITLE_FONT = ("Segoe UI", 26, "bold") # Quay lại kích thước lớn hơn
STEP_TITLE_FONT = ("Segoe UI", 22, "bold") 
LABEL_FONT = ("Segoe UI", 16) # Tăng
INPUT_FONT = ("Segoe UI", 16) # Tăng
BUTTON_FONT = ("Segoe UI", 18, "bold") 
SMALL_STATUS_FONT = ("Segoe UI", 13) 
OPTION_MENU_FONT = ("Segoe UI", 15) # Tăng
OPTION_MENU_DROPDOWN_FONT = ("Segoe UI", 14) # Tăng


LARGE_BUTTON_WIDTH = 250 # Cho nút Next/Back lớn ở dưới
MEDIUM_BUTTON_WIDTH = 180 
LARGE_BUTTON_HEIGHT = 65 # Tăng
MEDIUM_BUTTON_HEIGHT = 50 

ENTRY_HEIGHT = 42 # Tăng
OPTION_MENU_HEIGHT = 42 # Tăng
OPTION_MENU_WIDTH_S = 75  # Tăng (cho Day, Month, Hour, Min, Sec)
OPTION_MENU_WIDTH_M = 100 # Tăng (cho Year)

# --- Kích thước Icon (Giữ nguyên hoặc tăng nhẹ nếu muốn) ---
# Icon cho các nút ở Bước 2 nên giữ ở mức vừa phải để 3 nút không quá lớn
icon_size_large_button_step2 = (200, 200) # Có thể tăng chiều cao một chút
img_size_status = (28, 28) 
icon_size_nav_button = (20,20) 
icon_size_send_button = (30,30) 

# --- Padding (Tăng lại một chút so với lần thu nhỏ nhất) ---
PAD_X_MAIN_CONTAINER = 20 
PAD_Y_MAIN_CONTAINER = 15

PAD_X_SECTION = 12 
PAD_Y_SECTION = 8 # Tăng padding dọc giữa các section

PAD_X_WIDGET_HORIZONTAL = 5 
PAD_Y_WIDGET_VERTICAL = 4 # Tăng padding dọc giữa các widget

# --- Cửa sổ ---
WINDOW_WIDTH = 1024
WINDOW_HEIGHT = 600

CONFIG_FILE = "mqtt_enroll_config.json"
HEALTHCHECK_INTERVAL_MS = 10000

FINGERPRINT_PORT = '/dev/ttyAMA4' 
FINGERPRINT_BAUDRATE = 57600

RFID_RESET_PIN_BCM = None 
RFID_IRQ_PIN_BCM = None   

GMT_PLUS_7 = timezone(timedelta(hours=7))

# --- Helper functions for OptionMenu values ---
def get_hour_values(): return [f"{h:02d}" for h in range(24)]
def get_minute_second_values(): return [f"{m:02d}" for m in range(60)]
def get_year_values(start_offset=-2, end_offset=5): # Điều chỉnh khoảng năm nếu cần
    current_year = datetime.now().year
    return [str(y) for y in range(current_year + start_offset, current_year + end_offset + 1)]
def get_month_values(): return [f"{m:02d}" for m in range(1, 13)]
def get_day_values(year_str, month_str):
    try:
        year = int(year_str); month = int(month_str)
        num_days = calendar.monthrange(year, month)[1]
        return [f"{d:02d}" for d in range(1, num_days + 1)]
    except (ValueError, TypeError): return [f"{d:02d}" for d in range(1, 32)]

def get_mac_address():
    mac = uuid.getnode()
    return ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))

def load_image_ctk(path, size):
    try:
        full_path = os.path.join(script_dir, path)
        if not os.path.exists(full_path):
            print(f"[Load Image WARN] Image file not found: {full_path}"); return None
        img = Image.open(full_path)
        if size: img.thumbnail(size, Image.Resampling.LANCZOS) 
        return CTkImage(light_image=img, dark_image=img, size=img.size)
    except Exception as e:
        print(f"[Load Image ERROR] Failed to load {path}: {e}"); return None

class EnrollmentApp:
    def __init__(self, root):
        self.root = root
        self.enroll_mac = get_mac_address()
        if DEBUG: print("[Enroll DEBUG] Enrollment Device MAC Address:", self.enroll_mac)

        try: database_enroll.initialize_database()
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to initialize database: {e}\nApp exit."); root.quit(); return

        self.discovered_rooms_macs = database_enroll.get_all_discovered_devices()
        if DEBUG: print(f"[Enroll DEBUG] Loaded discovered rooms: {self.discovered_rooms_macs}")

        self.current_bio_id = None; self.current_id_number = ""; self.current_person_name = ""; self.current_room_name_selected = None
        now = datetime.now()
        self.from_hour_str = "00"; self.from_minute_str = "00"; self.from_second_str = "00"
        self.from_day_str = now.strftime("%d"); self.from_month_str = now.strftime("%m"); self.from_year_str = now.strftime("%Y")
        to_dt = now + timedelta(days=6)
        self.to_hour_str = "23"; self.to_minute_str = "59"; self.to_second_str = "59"
        self.to_day_str = to_dt.strftime("%d"); self.to_month_str = to_dt.strftime("%m"); self.to_year_str = to_dt.strftime("%Y")
        self.active_day_mask_list = [True] * 7

        self.current_face_image_b64 = None; self.current_face_template_b64 = None
        self.current_finger_template_b64 = None; self.current_rfid_uid_str = None
        self.preview_face_image_ctk = None

        self.mqtt_manager = None; self.mqtt_config = {}
        self.config_path = os.path.join(script_dir, CONFIG_FILE)
        self.screen_history = []
        
        self.fingerprint_sensor = None; self.rfid_sensor = None

        self.connection_status_label = None; self.bg_label = None
        self.main_frame = None; self.config_btn_ref = None # Tham chiếu nút config

        self.connected_image = load_image_ctk("images/connected.jpg", img_size_status)
        self.disconnected_image = load_image_ctk("images/disconnected.jpg", img_size_status)
        self.bg_photo = load_image_ctk("images/background_enroll.jpeg", (WINDOW_WIDTH, WINDOW_HEIGHT))
        self.face_icon_large = load_image_ctk("images/face.png", icon_size_large_button_step2)
        self.fingerprint_icon_large = load_image_ctk("images/fingerprint.png", icon_size_large_button_step2)
        self.rfid_icon_large = load_image_ctk("images/rfid.png", icon_size_large_button_step2)
        self.next_icon = load_image_ctk("images/next_arrow.png", icon_size_nav_button)
        self.back_icon = load_image_ctk("images/back_arrow.png", icon_size_nav_button)
        self.send_icon_large = load_image_ctk("images/send.png", icon_size_send_button)

        self.root.configure(fg_color=BG_COLOR)
        self.show_background()
        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="Chưa kết nối", font=("Segoe UI", 10), text_color=ERROR_COLOR, compound="left")
        self.connection_status_label.place(relx=0.01, rely=0.98, anchor="sw") # Điều chỉnh vị trí
        self.create_config_button()

        self.initialize_fingerprint_sensor()
        self.initialize_rfid_sensor()

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f: self.mqtt_config = json.load(f)
                if not self.mqtt_config.get("broker") or not self.mqtt_config.get("port"):
                     raise ValueError("Config missing broker/port.")
                self.initialize_mqtt()
                self.start_new_enrollment_process()
            except Exception as e: # Bắt lỗi rộng hơn
                if DEBUG: print(f"[Enroll ERROR] Load config/init: {e}.")
                if os.path.exists(self.config_path):
                    try: os.remove(self.config_path); self.mqtt_config = {}
                    except OSError as re: print(f"[Enroll ERROR] Removing invalid config: {re}")
                self.push_screen("mqtt_config", self.build_mqtt_config_screen)
        else:
            self.push_screen("mqtt_config", self.build_mqtt_config_screen)

        self.schedule_healthcheck()
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup)
    
    def generate_new_bio_id(self): # (Giữ nguyên)
        self.current_bio_id = uuid.uuid4().hex[:10].upper()
        if DEBUG: print(f"[Enroll DEBUG] New Bio ID: {self.current_bio_id}")
        
    def initialize_fingerprint_sensor(self): # (Giữ nguyên)
        if PyFingerprint is None: return
        try:
            self.fingerprint_sensor = PyFingerprint(FINGERPRINT_PORT, FINGERPRINT_BAUDRATE, 0xFFFFFFFF, 0x00000000)
            if not self.fingerprint_sensor.verifyPassword():
                if DEBUG: print("[Enroll ERROR] FP sensor password verify failed.")
                self.fingerprint_sensor = None
            elif DEBUG: print("[Enroll INFO] FP sensor verified.")
        except Exception as e:
            if DEBUG: print(f"[Enroll ERROR] Init FP sensor: {e}")
            self.fingerprint_sensor = None

    def initialize_rfid_sensor(self): # (Giữ nguyên - I2C)
        if PN532_I2C is None or board is None or busio is None:
            if DEBUG: print("[Enroll WARN] PN532 I2C libs missing. RFID disabled.")
            self.rfid_sensor = None; return
        try:
            i2c = busio.I2C(board.SCL, board.SDA) # SCL, SDA từ board
            reset_pin_obj = irq_pin_obj = None
            if RFID_RESET_PIN_BCM is not None:
                import digitalio
                reset_pin_obj = digitalio.DigitalInOut(getattr(board, f"D{RFID_RESET_PIN_BCM}"))
            if RFID_IRQ_PIN_BCM is not None:
                import digitalio
                irq_pin_obj = digitalio.DigitalInOut(getattr(board, f"D{RFID_IRQ_PIN_BCM}"))
            self.rfid_sensor = PN532_I2C(i2c, debug=False, reset=reset_pin_obj, irq=irq_pin_obj)
            self.rfid_sensor.SAM_configuration()
            ic, ver, rev, support = self.rfid_sensor.firmware_version
            if DEBUG: print(f"[Enroll INFO] PN532 I2C ver: {ver}.{rev}")
        except Exception as e: # Bắt lỗi rộng hơn
            if DEBUG: print(f"[Enroll ERROR] Init RFID I2C: {e}")
            self.rfid_sensor = None

    def initialize_mqtt(self): # (Giữ nguyên)
        if self.mqtt_config and not self.mqtt_manager:
            if DEBUG: print("[Enroll DEBUG] Init MQTTManager...")
            self.mqtt_manager = MQTTEnrollManager(self.mqtt_config, self.enroll_mac, self.config_path, debug=DEBUG)
            self.mqtt_manager.on_connection_status_change = self.update_connection_status
            self.mqtt_manager.on_device_info_received = self.handle_discovered_device_info
            if not self.mqtt_manager.initialize_connection() and DEBUG:
                 print("[Enroll WARN] Initial MQTT connection failed.")

    def handle_discovered_device_info(self, room_name, mac_address): # (Giữ nguyên)
        if room_name and mac_address:
            self.discovered_rooms_macs[room_name] = mac_address
            active_screen_id = self.screen_history[-1][0] if self.screen_history else None
            if active_screen_id == "step1_basic_info" and hasattr(self, 'room_name_option_menu_s1') and \
               self.room_name_option_menu_s1 and self.room_name_option_menu_s1.winfo_exists():
                new_room_options = sorted(list(self.discovered_rooms_macs.keys()))
                current_selection = self.room_name_var_s1.get()
                self.room_name_option_menu_s1.configure(values=new_room_options if new_room_options else ["(Chưa có phòng)"])
                if current_selection in new_room_options: self.room_name_var_s1.set(current_selection)
                elif new_room_options: self.room_name_var_s1.set(new_room_options[0])
                else: self.room_name_var_s1.set("(Chưa có phòng)")
        elif DEBUG: print(f"[Enroll WARN] Incomplete device info: r='{room_name}', m='{mac_address}'")

    def schedule_healthcheck(self): # (Giữ nguyên)
        if self.mqtt_manager and hasattr(self.mqtt_manager, 'connected') and self.mqtt_manager.connected:
            self.mqtt_manager.send_healthcheck()
        self.root.after(HEALTHCHECK_INTERVAL_MS, self.schedule_healthcheck)

    def update_connection_status(self, is_connected): # (Giữ nguyên)
        if not (hasattr(self,'connection_status_label') and self.connection_status_label and self.connection_status_label.winfo_exists()): return
        img = self.connected_image if is_connected else self.disconnected_image
        txt = " Đã kết nối" if is_connected else " Mất kết nối"
        clr = SUCCESS_COLOR if is_connected else ERROR_COLOR
        self.connection_status_label.configure(image=img, text=txt, text_color=clr)

    def show_background(self): # (Giữ nguyên)
        if hasattr(self,'bg_photo') and self.bg_photo:
            if hasattr(self,'bg_label') and self.bg_label and self.bg_label.winfo_exists(): self.bg_label.destroy()
            self.bg_label = ctk.CTkLabel(self.root, image=self.bg_photo, text=""); self.bg_label.place(x=0, y=0, relwidth=1, relheight=1); self.bg_label.lower()

    def clear_frames(self, keep_background=True): # (Giữ nguyên)
        if hasattr(self, 'main_frame') and self.main_frame and self.main_frame.winfo_exists():
            self.main_frame.destroy(); self.main_frame = None
        if hasattr(self, 'nav_frame') and self.nav_frame and self.nav_frame.winfo_exists(): # Xóa cả nav_frame
            self.nav_frame.destroy(); self.nav_frame = None
        if keep_background:
            self.show_background()
            if hasattr(self, 'connection_status_label') and self.connection_status_label and self.connection_status_label.winfo_exists():
                 self.connection_status_label.lift()
            self.create_config_button()

    def push_screen(self, screen_id, screen_func, *args): # (Giữ nguyên)
        if self.screen_history and self.screen_history[-1][0] == screen_id and not screen_id.startswith("step"): return
        self.screen_history.append((screen_id, screen_func, args))
        self.clear_frames()
        self.root.update_idletasks(); screen_func(*args)

    def go_back(self): # (Giữ nguyên)
        if len(self.screen_history) > 1:
            self.screen_history.pop()
            prev_screen_id, prev_screen_func, prev_args = self.screen_history[-1]
            self.clear_frames(); self.root.update_idletasks(); prev_screen_func(*prev_args)
        elif not (hasattr(self, 'main_frame') and self.main_frame and self.main_frame.winfo_exists()):
             self.start_new_enrollment_process()

    def start_new_enrollment_process(self): # (Giữ nguyên)
        face_enroll.stop_face_capture()
        self.reset_enrollment_state_full(); self.screen_history = []
        self.push_screen("step1_basic_info", self.show_step1_basic_info_screen)

    def create_config_button(self): # (Giữ nguyên)
        if hasattr(self, 'config_btn_ref') and self.config_btn_ref and self.config_btn_ref.winfo_exists():
            self.config_btn_ref.lift(); return
        self.config_btn_ref = ctk.CTkButton(self.root, text="Cấu hình", command=self.confirm_reconfigure_mqtt, width=120, height=35, fg_color="#6c757d", hover_color="#5a6268", font=("Segoe UI", 12), text_color="white") # Giảm kích thước
        self.config_btn_ref.place(relx=0.99, rely=0.02, anchor="ne") # Điều chỉnh vị trí

    def confirm_reconfigure_mqtt(self): # (Giữ nguyên)
        if messagebox.askyesno("Xác nhận", "Cấu hình lại MQTT?", icon='warning', parent=self.root):
            self.reconfigure_mqtt_station()

    def reconfigure_mqtt_station(self): # (Giữ nguyên)
        if self.mqtt_manager: self.mqtt_manager.disconnect_client(); self.mqtt_manager = None; self.update_connection_status(False)
        if os.path.exists(self.config_path):
            try: os.remove(self.config_path)
            except Exception as e: print(f"[Enroll ERROR] Removing config: {e}")
        self.mqtt_config = {}; self.screen_history = []
        self.push_screen("mqtt_config", self.build_mqtt_config_screen)

    def build_mqtt_config_screen(self): # (Giữ nguyên, có thể cần co bớt padding/font nếu muốn vừa hơn)
        self.clear_frames(keep_background=False) # Không cần bg cho màn config này
        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=10) # Sử dụng SCREEN_BG_COLOR
        self.main_frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.7, relheight=0.65) # Giảm relheight
        ctk.CTkLabel(self.main_frame, text="CẤU HÌNH MQTT", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN_CONTAINER + 5, PAD_Y_MAIN_CONTAINER + 5))
        form_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        form_frame.pack(pady=PAD_Y_WIDGET_VERTICAL, padx=PAD_X_SECTION + 10, fill="x")
        def add_cfg_row(parent, lbl, placeholder="", default_val=""):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=PAD_Y_WIDGET_VERTICAL)
            ctk.CTkLabel(row, text=lbl, font=LABEL_FONT, width=150, anchor="w").pack(side="left", padx=(0,5)) # Giảm width
            entry = ctk.CTkEntry(row, font=INPUT_FONT, height=ENTRY_HEIGHT, placeholder_text=placeholder)
            entry.pack(side="left", expand=True, fill="x")
            if default_val: entry.insert(0, str(default_val))
            return entry
        self.server_entry_cfg = add_cfg_row(form_frame, "Broker IP/Domain:", "mqtt.example.com", self.mqtt_config.get("broker"))
        self.port_entry_cfg = add_cfg_row(form_frame, "Broker Port:", "1883", self.mqtt_config.get("port"))
        self.http_port_entry_cfg = add_cfg_row(form_frame, "HTTP Port (API):", "", self.mqtt_config.get("http_port", "8080"))
        self.enroll_room_entry_cfg = add_cfg_row(form_frame, "Vị trí trạm ĐK:", "VD: B1-HUST", self.mqtt_config.get("enroll_station_room", "EnrollDesk"))
        
        button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        button_frame.pack(pady=(PAD_Y_MAIN_CONTAINER + 10, PAD_Y_MAIN_CONTAINER), padx=PAD_X_SECTION, fill="x", side="bottom")
        ctk.CTkButton(button_frame, text="LƯU & KẾT NỐI", width=MEDIUM_BUTTON_WIDTH+10, height=MEDIUM_BUTTON_HEIGHT, font=BUTTON_FONT, command=self.validate_and_save_mqtt_config, fg_color=ACCENT_COLOR, text_color=BUTTON_FG_TEXT).pack(side="right", padx=PAD_X_WIDGET_HORIZONTAL)
        # Nút Back ở đây không cần thiết lắm nếu đây là màn hình đầu tiên hoặc sau khi reset
        # if len(self.screen_history) > 1 : 
        #      ctk.CTkButton(button_frame, text="TRỞ VỀ", width=MEDIUM_BUTTON_WIDTH, height=MEDIUM_BUTTON_HEIGHT, font=BUTTON_FONT, command=self.go_back, fg_color="#A0A0A0").pack(side="left", padx=PAD_X_WIDGET_HORIZONTAL)

    def validate_and_save_mqtt_config(self): # (Giữ nguyên)
        broker=self.server_entry_cfg.get().strip(); port_s=self.port_entry_cfg.get().strip()
        http_s=self.http_port_entry_cfg.get().strip(); loc=self.enroll_room_entry_cfg.get().strip()
        if not all([broker, port_s, http_s, loc]):
            messagebox.showerror("Lỗi", "Điền đủ thông tin.", parent=self.main_frame); return
        try:
            port = int(port_s); http_port = int(http_s)
            if not (0 < port < 65536 and 0 < http_port < 65536): raise ValueError("Port range")
        except ValueError: messagebox.showerror("Lỗi", "Port không hợp lệ.", parent=self.main_frame); return
        new_cfg = {"broker": broker, "port": port, "http_port": http_port, "enroll_station_room": loc}
        try:
            with open(self.config_path, "w") as f: json.dump(new_cfg, f, indent=2)
            self.mqtt_config = new_cfg
        except Exception as e: messagebox.showerror("Lỗi Lưu", f"Lưu config thất bại: {e}", parent=self.main_frame); return
        self.show_connecting_screen_mqtt_station()
        self.root.after(100, self._init_mqtt_after_save_config)

    def _init_mqtt_after_save_config(self): # (Giữ nguyên)
        if self.mqtt_manager: self.mqtt_manager.disconnect_client(); self.mqtt_manager = None
        self.initialize_mqtt()
        self.root.after(2000, self.start_new_enrollment_process) # Giảm delay

    def show_connecting_screen_mqtt_station(self): # (Giữ nguyên)
        self.clear_frames(keep_background=False) # Không cần bg cho màn này
        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=10)
        self.main_frame.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(self.main_frame, text="Đang kết nối MQTT...", font=STEP_TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=20, padx=40)
        prog = ctk.CTkProgressBar(self.main_frame, width=300, height=18, corner_radius=8)
        prog.pack(pady=(0,20), padx=40); prog.set(0); prog.start()

       # --- STEP 1: BASIC INFO --- (Loại bỏ nút nhanh, tăng kích thước widget)
    def show_step1_basic_info_screen(self):
        self.clear_frames()
        if not self.current_bio_id: self.generate_new_bio_id()

        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=10)
        self.main_frame.place(relx=0.5, rely=0.47, anchor="center", relwidth=0.94, relheight=0.81) 

        ctk.CTkLabel(self.main_frame, text="ĐĂNG KÝ THÔNG TIN", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN_CONTAINER, PAD_Y_MAIN_CONTAINER - 5))

        content_cols_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        content_cols_frame.pack(fill="both", expand=True, padx=PAD_X_SECTION, pady=(0, PAD_Y_WIDGET_VERTICAL + 2))
        content_cols_frame.columnconfigure(0, weight=1); content_cols_frame.columnconfigure(1, weight=1) # Giữ 2 cột

        # --- Left Column Content (Thông tin cá nhân, Phòng) ---
        left_col = ctk.CTkFrame(content_cols_frame, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="new", padx=(0, PAD_X_WIDGET_HORIZONTAL + 5)) # Tăng padding giữa cột
        
        def create_labeled_input(parent, label_txt, current_val="", placeholder=""):
            ctk.CTkLabel(parent, text=label_txt, font=LABEL_FONT, anchor="w").pack(fill="x", pady=(PAD_Y_WIDGET_VERTICAL + 2, 1)) # Tăng pady trên
            entry = ctk.CTkEntry(parent, placeholder_text=placeholder, font=INPUT_FONT, height=ENTRY_HEIGHT)
            entry.pack(fill="x", pady=(0, PAD_Y_WIDGET_VERTICAL + 3)) # Tăng pady dưới
            entry.insert(0, current_val); return entry
        
        self.person_name_entry_s1 = create_labeled_input(left_col, "Họ và Tên (*):", self.current_person_name)
        self.id_number_entry_s1 = create_labeled_input(left_col, "Số CCCD/Mã ID (*):", self.current_id_number)

        ctk.CTkLabel(left_col, text="Phòng đăng ký", font=LABEL_FONT, anchor="w").pack(fill="x", pady=(PAD_Y_WIDGET_VERTICAL + 2, 1))
        room_opts = sorted(list(self.discovered_rooms_macs.keys())) or ["(Chưa có phòng)"]
        room_val = self.current_room_name_selected if self.current_room_name_selected in room_opts else room_opts[0]
        self.room_name_var_s1 = ctk.StringVar(value=room_val)
        self.room_name_option_menu_s1 = ctk.CTkOptionMenu(left_col, variable=self.room_name_var_s1, values=room_opts, font=OPTION_MENU_FONT, height=OPTION_MENU_HEIGHT, dropdown_font=OPTION_MENU_DROPDOWN_FONT, corner_radius=7) # Tăng corner_radius
        self.room_name_option_menu_s1.pack(fill="x", pady=(0, PAD_Y_WIDGET_VERTICAL + 3))

        # --- Right Column Content (Thời gian & Lịch) ---
        right_col = ctk.CTkFrame(content_cols_frame, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="new", padx=(PAD_X_WIDGET_HORIZONTAL + 5, 0))

        ctk.CTkLabel(right_col, text="Thời gian đăng ký", font=LABEL_FONT, anchor="w").pack(fill="x", pady=(PAD_Y_WIDGET_VERTICAL + 2, 1))
        
        from_frame_outer = ctk.CTkFrame(right_col, fg_color="transparent")
        from_frame_outer.pack(fill="x", pady=(0, PAD_Y_WIDGET_VERTICAL)) 
        ctk.CTkLabel(from_frame_outer, text="Từ:", font=LABEL_FONT, width=30, anchor="w").pack(side="left", padx=(0,3)) # Tăng width label
        from_pickers_frame = ctk.CTkFrame(from_frame_outer, fg_color="transparent")
        from_pickers_frame.pack(side="left", fill="x", expand=True)
        self._create_datetime_pickers(from_pickers_frame, "from")

        to_frame_outer = ctk.CTkFrame(right_col, fg_color="transparent")
        to_frame_outer.pack(fill="x", pady=(0, PAD_Y_WIDGET_VERTICAL + 2)) # Tăng pady dưới
        ctk.CTkLabel(to_frame_outer, text="Đến:", font=LABEL_FONT, width=30, anchor="w").pack(side="left", padx=(0,3))
        to_pickers_frame = ctk.CTkFrame(to_frame_outer, fg_color="transparent")
        to_pickers_frame.pack(side="left", fill="x", expand=True)
        self._create_datetime_pickers(to_pickers_frame, "to")
        
        
        ctk.CTkLabel(right_col, text="Lịch cố định trong tuần", font=LABEL_FONT, anchor="w").pack(fill="x", pady=(PAD_Y_WIDGET_VERTICAL + 5, 2)) # Tăng pady trên
        
        days_chk_frame = ctk.CTkFrame(right_col, fg_color="transparent")
        days_chk_frame.pack(fill="x", pady=(0, PAD_Y_WIDGET_VERTICAL + 2)) # Tăng pady dưới
        day_names = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]; self.day_vars_s1 = []
        chk_font = ("Segoe UI", 14) # Tăng font checkbox
        checkbox_height = 30 # Tăng chiều cao checkbox
        checkbox_box_size = 20 # Tăng kích thước ô tick

        for i, day_name in enumerate(day_names):
            var = ctk.BooleanVar(value=self.active_day_mask_list[i]); self.day_vars_s1.append(var)
            chk = ctk.CTkCheckBox(days_chk_frame, text=day_name, variable=var, font=chk_font, 
                                  height=checkbox_height, checkbox_height=checkbox_box_size, 
                                  checkbox_width=checkbox_box_size, corner_radius=5, border_width=2) # Tăng border_width
            chk.pack(side="left", padx=4, pady=1, expand=True, fill="x") # Tăng padx


        if hasattr(self, 'nav_frame') and self.nav_frame and self.nav_frame.winfo_exists():
            self.nav_frame.destroy()
        self.nav_frame = ctk.CTkFrame(self.root, fg_color=BG_COLOR) 
        self.nav_frame.place(relx=0.5, rely=1.0, anchor="s", relwidth=1.0, relheight=0.12) 
        ctk.CTkButton(self.nav_frame, text="TIẾP TỤC", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH, height=LARGE_BUTTON_HEIGHT,
                      command=self._action_goto_step2, fg_color=ACCENT_COLOR, text_color=BUTTON_FG_TEXT, image=self.next_icon, compound="right", corner_radius=8).pack(side="right", pady=(5,8), padx=PAD_X_MAIN_CONTAINER)
    def _create_datetime_pickers(self, parent_frame, prefix):
        parent_frame.columnconfigure((0,2,4,6,8), weight=0) 
        parent_frame.columnconfigure((1,3,5,7,9), weight=0) 
        
        current_option_menu_width_s = OPTION_MENU_WIDTH_S # Sử dụng hằng số đã định nghĩa
        current_option_menu_width_m = OPTION_MENU_WIDTH_M
        current_option_menu_height = OPTION_MENU_HEIGHT

        h_var=ctk.StringVar(value=getattr(self,f"{prefix}_hour_str","00"));om_h=ctk.CTkOptionMenu(parent_frame,variable=h_var,values=get_hour_values(),width=current_option_menu_width_s,height=current_option_menu_height,font=OPTION_MENU_FONT,dropdown_font=OPTION_MENU_DROPDOWN_FONT,corner_radius=6);om_h.grid(row=0,column=0,padx=(0,1), pady=1) # Thêm pady
        ctk.CTkLabel(parent_frame,text=":",font=LABEL_FONT).grid(row=0,column=1)
        m_var=ctk.StringVar(value=getattr(self,f"{prefix}_minute_str","00"));om_m=ctk.CTkOptionMenu(parent_frame,variable=m_var,values=get_minute_second_values(),width=current_option_menu_width_s,height=current_option_menu_height,font=OPTION_MENU_FONT,dropdown_font=OPTION_MENU_DROPDOWN_FONT,corner_radius=6);om_m.grid(row=0,column=2,padx=1, pady=1)
        ctk.CTkLabel(parent_frame,text=":",font=LABEL_FONT).grid(row=0,column=3)
        s_var=ctk.StringVar(value=getattr(self,f"{prefix}_second_str","00"));om_s=ctk.CTkOptionMenu(parent_frame,variable=s_var,values=get_minute_second_values(),width=current_option_menu_width_s,height=current_option_menu_height,font=OPTION_MENU_FONT,dropdown_font=OPTION_MENU_DROPDOWN_FONT,corner_radius=6);om_s.grid(row=0,column=4,padx=(1,4), pady=1) # Tăng padx phải

        date_row = 0 
        col_offset = 5 

        y_var=ctk.StringVar(value=getattr(self,f"{prefix}_year_str",datetime.now().strftime("%Y")))
        mth_var=ctk.StringVar(value=getattr(self,f"{prefix}_month_str",datetime.now().strftime("%m")))
        d_var=ctk.StringVar(value=getattr(self,f"{prefix}_day_str",datetime.now().strftime("%d")))
        d_vals=get_day_values(y_var.get(),mth_var.get())
        if d_var.get() not in d_vals: d_var.set(d_vals[0] if d_vals else "01")
        
        om_d=ctk.CTkOptionMenu(parent_frame,variable=d_var,values=d_vals,width=current_option_menu_width_s,height=current_option_menu_height,font=OPTION_MENU_FONT,dropdown_font=OPTION_MENU_DROPDOWN_FONT,corner_radius=6)
        om_d.grid(row=date_row,column=col_offset,padx=(0,1), pady=1)
        ctk.CTkLabel(parent_frame,text="/",font=LABEL_FONT).grid(row=date_row,column=col_offset+1)
        
        om_mth=ctk.CTkOptionMenu(parent_frame,variable=mth_var,values=get_month_values(),width=current_option_menu_width_s,height=current_option_menu_height,font=OPTION_MENU_FONT,dropdown_font=OPTION_MENU_DROPDOWN_FONT,corner_radius=6,command=lambda _:self._update_days_for_picker(prefix,d_var,y_var.get(),mth_var.get(),om_d))
        om_mth.grid(row=date_row,column=col_offset+2,padx=1, pady=1)
        ctk.CTkLabel(parent_frame,text="/",font=LABEL_FONT).grid(row=date_row,column=col_offset+3)
        
        om_y=ctk.CTkOptionMenu(parent_frame,variable=y_var,values=get_year_values(),width=current_option_menu_width_m,height=current_option_menu_height,font=OPTION_MENU_FONT,dropdown_font=OPTION_MENU_DROPDOWN_FONT,corner_radius=6,command=lambda _:self._update_days_for_picker(prefix,d_var,y_var.get(),mth_var.get(),om_d))
        om_y.grid(row=date_row,column=col_offset+4,padx=1, pady=1)
        
        if prefix == "from":
            self.from_hour_var, self.from_min_var, self.from_sec_var = h_var, m_var, s_var
            self.from_day_var, self.from_month_var, self.from_year_var = d_var, mth_var, y_var
            self.from_day_optionmenu = om_d
        else: 
            self.to_hour_var, self.to_min_var, self.to_sec_var = h_var, m_var, s_var
            self.to_day_var, self.to_month_var, self.to_year_var = d_var, mth_var, y_var
            self.to_day_optionmenu = om_d
        return h_var, m_var, s_var, d_var, mth_var, y_var, om_d

    def _update_days_for_picker(self, prefix, day_var, year_str, month_str, day_optionmenu_widget): # (Giữ nguyên)
        if not (day_optionmenu_widget and day_optionmenu_widget.winfo_exists()): return
        new_day_values = get_day_values(year_str, month_str)
        current_day = day_var.get()
        day_optionmenu_widget.configure(values=new_day_values)
        if current_day in new_day_values: day_var.set(current_day)
        elif new_day_values: day_var.set(new_day_values[0])
        else: day_var.set("01")
        # Cập nhật giá trị đã lưu trữ trong self
        if prefix == "from":
            self.from_year_str, self.from_month_str = year_str, month_str
        else: # prefix == "to"
            self.to_year_str, self.to_month_str = year_str, month_str

    def set_default_validity_s1(self): # (Giữ nguyên)
        today = datetime.now(); seven_days_later = today + timedelta(days=6)
        self.from_hour_var.set("00"); self.from_min_var.set("00"); self.from_sec_var.set("00")
        self.from_day_var.set(today.strftime("%d")); self.from_month_var.set(today.strftime("%m")); self.from_year_var.set(today.strftime("%Y"))
        if hasattr(self, 'from_day_optionmenu'): self._update_days_for_picker("from", self.from_day_var, self.from_year_var.get(), self.from_month_var.get(), self.from_day_optionmenu)
        self.to_hour_var.set("23"); self.to_min_var.set("59"); self.to_sec_var.set("59")
        self.to_day_var.set(seven_days_later.strftime("%d")); self.to_month_var.set(seven_days_later.strftime("%m")); self.to_year_var.set(seven_days_later.strftime("%Y"))
        if hasattr(self, 'to_day_optionmenu'): self._update_days_for_picker("to", self.to_day_var, self.to_year_var.get(), self.to_month_var.get(), self.to_day_optionmenu)

    def set_today_validity_s1(self): # (Giữ nguyên)
        today = datetime.now()
        self.from_hour_var.set("00"); self.from_min_var.set("00"); self.from_sec_var.set("00")
        self.from_day_var.set(today.strftime("%d")); self.from_month_var.set(today.strftime("%m")); self.from_year_var.set(today.strftime("%Y"))
        if hasattr(self, 'from_day_optionmenu'): self._update_days_for_picker("from", self.from_day_var, self.from_year_var.get(), self.from_month_var.get(), self.from_day_optionmenu)
        self.to_hour_var.set("23"); self.to_min_var.set("59"); self.to_sec_var.set("59")
        self.to_day_var.set(today.strftime("%d")); self.to_month_var.set(today.strftime("%m")); self.to_year_var.set(today.strftime("%Y"))
        if hasattr(self, 'to_day_optionmenu'): self._update_days_for_picker("to", self.to_day_var, self.to_year_var.get(), self.to_month_var.get(), self.to_day_optionmenu)
    
    def clear_all_datetime_s1(self): # (Giữ nguyên)
        now = datetime.now()
        self.from_hour_var.set("00"); self.from_min_var.set("00"); self.from_sec_var.set("00")
        self.from_day_var.set(now.strftime("%d")); self.from_month_var.set(now.strftime("%m")); self.from_year_var.set(now.strftime("%Y"))
        if hasattr(self, 'from_day_optionmenu'): self._update_days_for_picker("from", self.from_day_var, self.from_year_var.get(), self.from_month_var.get(), self.from_day_optionmenu)
        tomorrow = now + timedelta(days=1)
        self.to_hour_var.set("23"); self.to_min_var.set("59"); self.to_sec_var.set("59")
        self.to_day_var.set(tomorrow.strftime("%d")); self.to_month_var.set(tomorrow.strftime("%m")); self.to_year_var.set(tomorrow.strftime("%Y"))
        if hasattr(self, 'to_day_optionmenu'): self._update_days_for_picker("to", self.to_day_var, self.to_year_var.get(), self.to_month_var.get(), self.to_day_optionmenu)

    def set_all_days_s1(self, select_all): # (Giữ nguyên)
        if hasattr(self, 'day_vars_s1'):
            for var in self.day_vars_s1: var.set(select_all)

    def _save_step1_data(self): # (Giữ nguyên)
        self.current_person_name = self.person_name_entry_s1.get().strip()
        self.current_id_number = self.id_number_entry_s1.get().strip()
        self.current_room_name_selected = self.room_name_var_s1.get()
        if self.current_room_name_selected == "(Chưa có phòng)": self.current_room_name_selected = None
        
        # Lấy giá trị từ các biến StringVars đã được gán trong _create_datetime_pickers
        self.from_hour_str = self.from_hour_var.get(); self.from_minute_str = self.from_min_var.get(); self.from_second_str = self.from_sec_var.get()
        self.from_day_str = self.from_day_var.get(); self.from_month_str = self.from_month_var.get(); self.from_year_str = self.from_year_var.get()
        self.to_hour_str = self.to_hour_var.get(); self.to_minute_str = self.to_min_var.get(); self.to_second_str = self.to_sec_var.get()
        self.to_day_str = self.to_day_var.get(); self.to_month_str = self.to_month_var.get(); self.to_year_str = self.to_year_var.get()
        
        if hasattr(self, 'day_vars_s1') and self.day_vars_s1:
            self.active_day_mask_list = [var.get() for var in self.day_vars_s1]
        return True

    def _action_goto_step2(self): # (Giữ nguyên)
        if not (hasattr(self, 'person_name_entry_s1') and hasattr(self, 'id_number_entry_s1')):
             messagebox.showerror("Lỗi", "Lỗi giao diện.", parent=self.root); return
        person_name = self.person_name_entry_s1.get().strip(); id_number = self.id_number_entry_s1.get().strip()
        if not person_name or not id_number:
            messagebox.showerror("Thiếu thông tin", "Nhập Họ Tên và Số CCCD.", parent=self.main_frame or self.root); return
        if not self._validate_datetime_logic(): return
        self._save_step1_data()
        self.push_screen("step2_biometrics", self.show_step2_biometric_screen)
    
    def _validate_datetime_logic(self): # (Giữ nguyên)
        try:
            # Đảm bảo các biến _var tồn tại trước khi get()
            if not all(hasattr(self, f"{p}_{c}_var") for p in ["from", "to"] for c in ["year", "month", "day", "hour", "min", "sec"]):
                messagebox.showerror("Lỗi", "Lỗi cấu hình ngày giờ.", parent=self.main_frame or self.root); return False

            from_dt_str = f"{self.from_year_var.get()}-{self.from_month_var.get()}-{self.from_day_var.get()} {self.from_hour_var.get()}:{self.from_min_var.get()}:{self.from_sec_var.get()}"
            to_dt_str = f"{self.to_year_var.get()}-{self.to_month_var.get()}-{self.to_day_var.get()} {self.to_hour_var.get()}:{self.to_min_var.get()}:{self.to_sec_var.get()}"
            from_dt_obj = datetime.strptime(from_dt_str, "%Y-%m-%d %H:%M:%S")
            to_dt_obj = datetime.strptime(to_dt_str, "%Y-%m-%d %H:%M:%S")
            if to_dt_obj < from_dt_obj:
                messagebox.showerror("Lỗi Thời Gian", "'Đến' không thể trước 'Từ'.", parent=self.main_frame or self.root); return False
        except (ValueError, AttributeError) as e: # Thêm AttributeError
            messagebox.showerror("Lỗi Thời Gian", f"Ngày giờ không hợp lệ: {e}", parent=self.main_frame or self.root); return False
        return True

    # --- STEP 2: BIOMETRIC ENROLLMENT --- (Tối ưu không gian)
    def show_step2_biometric_screen(self):
        self.clear_frames()
        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=10)
        self.main_frame.place(relx=0.5, rely=0.47, anchor="center", relwidth=0.94, relheight=0.81)

        ctk.CTkLabel(self.main_frame, text="ĐĂNG KÝ SINH TRẮC HỌC", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN_CONTAINER, PAD_Y_MAIN_CONTAINER-5))
        info_txt = f"ĐK cho: {self.current_person_name[:20]}{'...' if len(self.current_person_name)>20 else ''} ({self.current_id_number})" # Rút gọn
        ctk.CTkLabel(self.main_frame, text=info_txt, font=LABEL_FONT).pack(pady=(0, PAD_Y_MAIN_CONTAINER-2))
        
        btns_container = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        btns_container.pack(expand=True, fill="both", padx=PAD_X_SECTION, pady=0)
        btns_container.columnconfigure((0,1,2), weight=1, uniform="bio_btn_col") # 3 cột đều
        btns_container.rowconfigure(0, weight=1)

        btn_width_s2 = (WINDOW_WIDTH * 0.94 * 0.33) - (PAD_X_WIDGET_HORIZONTAL * 3) # Ước lượng chiều rộng nút
        btn_height_s2 = (WINDOW_HEIGHT * 0.81 * 0.6) # Ước lượng chiều cao nút
        btn_border_spacing = 8 # Giảm

        def create_bio_button_frame(parent, col, img, txt, cmd):
            frm = ctk.CTkFrame(parent, fg_color="transparent")
            frm.grid(row=0, column=col, padx=PAD_X_WIDGET_HORIZONTAL, pady=0, sticky="nsew")
            btn = ctk.CTkButton(frm, image=img, text=txt, font=BUTTON_FONT, compound="top", width=btn_width_s2, height=btn_height_s2, command=cmd, corner_radius=10, border_spacing=btn_border_spacing)
            btn.pack(expand=True, pady=(5,0))
            status_lbl = ctk.CTkLabel(frm, text="", font=SMALL_STATUS_FONT)
            status_lbl.pack(pady=(2,5))
            return btn, status_lbl

        self.face_enroll_btn_s2, self.face_status_label_s2 = create_bio_button_frame(btns_container, 0, self.face_icon_large, "KHUÔN MẶT", self.start_face_enrollment_s2)
        self.finger_enroll_btn_s2, self.finger_status_label_s2 = create_bio_button_frame(btns_container, 1, self.fingerprint_icon_large, "VÂN TAY", self.start_fingerprint_enrollment_s2)
        self.rfid_enroll_btn_s2, self.rfid_status_label_s2 = create_bio_button_frame(btns_container, 2, self.rfid_icon_large, "THẺ RFID", self.start_rfid_enrollment_s2)
        
        self._update_biometric_status_s2()
        
        self.nav_frame = ctk.CTkFrame(self.root, fg_color=BG_COLOR)
        self.nav_frame.place(relx=0.5, rely=1.0, anchor="s", relwidth=1.0, relheight=0.12)
        ctk.CTkButton(self.nav_frame, text="QUAY LẠI", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH, height=LARGE_BUTTON_HEIGHT, command=self.go_back, image=self.back_icon, compound="left", corner_radius=8, fg_color="#A0A0A0").pack(side="left", pady=(5,8), padx=PAD_X_MAIN_CONTAINER)
        self.next_step3_button = ctk.CTkButton(self.nav_frame, text="TIẾP TỤC", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH, height=LARGE_BUTTON_HEIGHT, command=self._action_goto_step3, fg_color=ACCENT_COLOR, text_color=BUTTON_FG_TEXT, image=self.next_icon, compound="right", corner_radius=8)
        self.next_step3_button.pack(side="right", pady=(5,8), padx=PAD_X_MAIN_CONTAINER)
        self._update_next_button_step2_state()

    def _update_biometric_status_s2(self): # (Giữ nguyên)
        for btn, lbl, template_attr in [
            (self.face_enroll_btn_s2, self.face_status_label_s2, self.current_face_template_b64),
            (self.finger_enroll_btn_s2, self.finger_status_label_s2, self.current_finger_template_b64),
            (self.rfid_enroll_btn_s2, self.rfid_status_label_s2, self.current_rfid_uid_str)
        ]:
            if hasattr(lbl,'winfo_exists') and lbl.winfo_exists(): # Check if widget exists
                is_enrolled = bool(template_attr)
                lbl.configure(text="Đã đăng ký" if is_enrolled else "Chưa đăng ký", 
                              text_color=SUCCESS_COLOR if is_enrolled else "grey50")
                if hasattr(btn, 'winfo_exists') and btn.winfo_exists():
                     btn.configure(fg_color=SUCCESS_COLOR if is_enrolled else "#606060", 
                                   hover_color="#2b9e4c" if is_enrolled else "#707070")
        self._update_next_button_step2_state()


    def _update_next_button_step2_state(self): # (Giữ nguyên)
         if hasattr(self, 'next_step3_button') and self.next_step3_button.winfo_exists():
            can_proceed = self.current_face_template_b64 or self.current_finger_template_b64 or self.current_rfid_uid_str
            self.next_step3_button.configure(state="normal" if can_proceed else "disabled", 
                                             fg_color=ACCENT_COLOR if can_proceed else "#A0A0A0")

    # Callbacks for face, fingerprint, RFID enrollment (Giữ nguyên logic, chỉ gọi _schedule_return_to_step2)
    def start_face_enrollment_s2(self): face_enroll.capture_face_for_enrollment(parent=self.root, on_success_callback=self.handle_face_enroll_success_s2, on_cancel_callback=self.handle_face_enroll_cancel_s2)
    def handle_face_enroll_success_s2(self, img_b64, tmpl_b64): self.current_face_image_b64 = img_b64; self.current_face_template_b64 = tmpl_b64; self._schedule_return_to_step2()
    def handle_face_enroll_cancel_s2(self): self._schedule_return_to_step2()

    def start_fingerprint_enrollment_s2(self):
        parent = self.main_frame or self.root
        if not self.fingerprint_sensor: messagebox.showerror("Lỗi", "Cảm biến vân tay lỗi.", parent=parent); return
        try:
            if not self.fingerprint_sensor.verifyPassword(): messagebox.showerror("Lỗi", "Lỗi xác thực cảm biến VT.", parent=parent); return
        except Exception as e: messagebox.showerror("Lỗi", f"Lỗi giao tiếp cảm biến VT: {e}", parent=parent); return
        fingerprint_enroll.enroll_fingerprint_template(parent=self.root, sensor=self.fingerprint_sensor, on_success_callback=self.handle_finger_enroll_success_s2, on_failure_callback=self.handle_finger_enroll_failure_s2, on_cancel_callback=self.handle_finger_enroll_cancel_s2)
    def handle_finger_enroll_success_s2(self, tmpl_b64): self.current_finger_template_b64 = tmpl_b64; self._schedule_return_to_step2()
    def handle_finger_enroll_failure_s2(self, reason=""): messagebox.showerror("Lỗi", f"ĐK vân tay thất bại: {reason}", parent=self.root); self._schedule_return_to_step2()
    def handle_finger_enroll_cancel_s2(self): self._schedule_return_to_step2()
    
    def start_rfid_enrollment_s2(self):
        parent = self.main_frame or self.root
        if not self.rfid_sensor: messagebox.showerror("Lỗi", "Đầu đọc RFID lỗi.", parent=parent); return
        try: self.rfid_sensor.SAM_configuration() 
        except Exception as e: messagebox.showerror("Lỗi", f"Lỗi giao tiếp đầu đọc RFID: {str(e)[:80]}", parent=parent); return
        rfid_enroll.enroll_rfid_card(parent=self.root, sensor_pn532=self.rfid_sensor, on_success_callback=self.handle_rfid_enroll_success_s2, on_failure_callback=self.handle_rfid_enroll_failure_s2, on_cancel_callback=self.handle_rfid_enroll_cancel_s2)
    def handle_rfid_enroll_success_s2(self, uid_str): self.current_rfid_uid_str = uid_str; self._schedule_return_to_step2()
    def handle_rfid_enroll_failure_s2(self, reason=""): messagebox.showerror("Lỗi", f"ĐK RFID thất bại: {reason}", parent=self.root); self._schedule_return_to_step2()
    def handle_rfid_enroll_cancel_s2(self): self._schedule_return_to_step2()

    def _schedule_return_to_step2(self): self.root.after(10, lambda: self.push_screen("step2_biometrics", self.show_step2_biometric_screen))
    def _action_goto_step3(self): # (Giữ nguyên)
        if not (self.current_face_template_b64 or self.current_finger_template_b64 or self.current_rfid_uid_str):
            messagebox.showwarning("Thiếu Sinh Trắc Học", "Cần đăng ký ít nhất một mẫu.", parent=self.main_frame or self.root); return
        self.push_screen("step3_confirmation", self.show_step3_confirmation_screen)

    # --- STEP 3: CONFIRMATION --- (Sửa lỗi master cho CTkLabel ảnh)
    def show_step3_confirmation_screen(self):
        self.clear_frames()
        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=10)
        self.main_frame.place(relx=0.5, rely=0.47, anchor="center", relwidth=0.94, relheight=0.81)

        ctk.CTkLabel(self.main_frame, text="XÁC NHẬN THÔNG TIN", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN_CONTAINER, PAD_Y_MAIN_CONTAINER - 5))

        # --- Khung 1: Thông tin cá nhân và Thời gian hiệu lực ---
        personal_validity_outer_frame = ctk.CTkFrame(self.main_frame, fg_color=BG_COLOR, corner_radius=8)
        personal_validity_outer_frame.pack(fill="x", padx=PAD_X_SECTION, pady=(PAD_Y_SECTION, PAD_Y_SECTION + 3))
        
        ctk.CTkLabel(personal_validity_outer_frame, text="Thông Tin Chung & Thời Gian", font=STEP_TITLE_FONT, text_color=ACCENT_COLOR, anchor="w").pack(fill="x", padx=12, pady=(8, 5))

        personal_validity_content_frame = ctk.CTkFrame(personal_validity_outer_frame, fg_color="transparent")
        personal_validity_content_frame.pack(fill="x", padx=12, pady=(0, 8))
        personal_validity_content_frame.columnconfigure(0, weight=1); personal_validity_content_frame.columnconfigure(1, weight=2)
        personal_validity_content_frame.columnconfigure(2, weight=1); personal_validity_content_frame.columnconfigure(3, weight=2)

        current_row_list_ref = [0] 
        def add_compact_info_row(parent, current_row_idx_list, col_label, col_value, label_text, value_text, value_font=None, wraplen=200):
            eff_val_font = value_font if value_font else INPUT_FONT
            ctk.CTkLabel(parent, text=f"{label_text}:", font=LABEL_FONT, anchor="e").grid(row=current_row_idx_list[0], column=col_label, sticky="e", padx=(0,3), pady=2)
            ctk.CTkLabel(parent, text=str(value_text) if value_text is not None else "N/A", font=eff_val_font, anchor="w", wraplength=wraplen).grid(row=current_row_idx_list[0], column=col_value, sticky="w", pady=2)
        
        add_compact_info_row(personal_validity_content_frame, current_row_list_ref, 0, 1, "Họ và Tên", self.current_person_name)
        add_compact_info_row(personal_validity_content_frame, current_row_list_ref, 2, 3, "Phòng", self.current_room_name_selected or "N/A")
        current_row_list_ref[0] += 1
        
        add_compact_info_row(personal_validity_content_frame, current_row_list_ref, 0, 1, "Số CCCD/ID", self.current_id_number)
        from_dt_str = f"{self.from_day_str}/{self.from_month_str}/{self.from_year_str} {self.from_hour_str}:{self.from_minute_str}"
        add_compact_info_row(personal_validity_content_frame, current_row_list_ref, 2, 3, "Từ", from_dt_str)
        current_row_list_ref[0] += 1

        add_compact_info_row(personal_validity_content_frame, current_row_list_ref, 0, 1, "Bio ID", self.current_bio_id, ("Segoe UI", 14, "italic"))
        to_dt_str = f"{self.to_day_str}/{self.to_month_str}/{self.to_year_str} {self.to_hour_str}:{self.to_minute_str}"
        add_compact_info_row(personal_validity_content_frame, current_row_list_ref, 2, 3, "Đến", to_dt_str)
        current_row_list_ref[0] += 1

        days_map = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        active_days = ", ".join([days_map[i] for i, act in enumerate(self.active_day_mask_list) if act]) or "Không ngày nào"
        add_compact_info_row(personal_validity_content_frame, current_row_list_ref, 0, 1, "Lịch HĐ", active_days, wraplen=220)


        # --- Khung 2: Thông tin sinh trắc học ---
        biometrics_outer_frame = ctk.CTkFrame(self.main_frame, fg_color=BG_COLOR, corner_radius=8)
        biometrics_outer_frame.pack(fill="both", expand=True, padx=PAD_X_SECTION, pady=(PAD_Y_SECTION, PAD_Y_SECTION))
        
        ctk.CTkLabel(biometrics_outer_frame, text="Thông Tin Sinh Trắc Học Đăng Ký", font=STEP_TITLE_FONT, text_color=ACCENT_COLOR, anchor="w").pack(fill="x", padx=12, pady=(8, 5))
        
        biometrics_content_cols_frame = ctk.CTkFrame(biometrics_outer_frame, fg_color="transparent")
        biometrics_content_cols_frame.pack(fill="both", expand=True, padx=5, pady=(0, 8))
        biometrics_content_cols_frame.columnconfigure((0,1,2), weight=1, uniform="bio_info_col_s3")
        biometrics_content_cols_frame.rowconfigure(0, weight=1)

        # Helper để tạo cột thông tin sinh trắc
        def create_bio_detail_column(parent_frame_for_grid, col_index_in_grid, bio_title_text, is_bio_enrolled, 
                                     face_image_ctk_obj=None, rfid_uid_text_val=None): # Thay đổi tham số
            
            col_content_container = ctk.CTkFrame(parent_frame_for_grid, fg_color=SCREEN_BG_COLOR, corner_radius=6, border_width=1, border_color="gray70")
            col_content_container.grid(row=0, column=col_index_in_grid, sticky="nsew", padx=5, pady=3)
            # col_content_container.pack_propagate(False) # Có thể không cần nếu rowconfigure weight=1 hoạt động tốt

            ctk.CTkLabel(col_content_container, text=bio_title_text, font=LABEL_FONT, text_color=ACCENT_COLOR).pack(pady=(8,3))
            
            current_status_color = SUCCESS_COLOR if is_bio_enrolled else WARNING_COLOR
            current_status_text = "ĐÃ ĐĂNG KÝ" if is_bio_enrolled else "CHƯA ĐĂNG KÝ"
            ctk.CTkLabel(col_content_container, text=current_status_text, font=INPUT_FONT, text_color=current_status_color).pack(pady=(0,8))

            if is_bio_enrolled:
                if bio_title_text == "Khuôn Mặt" and face_image_ctk_obj:
                    # Tạo CTkLabel cho ảnh preview trực tiếp ở đây, với master là col_content_container
                    preview_label = ctk.CTkLabel(col_content_container, image=face_image_ctk_obj, text="")
                    preview_label.pack(pady=(0,8), expand=True, anchor="center")
                elif bio_title_text == "Thẻ RFID" and rfid_uid_text_val:
                    ctk.CTkLabel(col_content_container, text=str(rfid_uid_text_val), font=("Segoe UI", 13, "italic"), text_color="gray20").pack(pady=(0,8))
            # else:
                # Nếu muốn các cột có chiều cao bằng nhau khi một số không có chi tiết,
                # có thể thêm 1 frame trống với min height ở đây.
                # Hoặc dựa vào rowconfigure weight=1 của parent để chúng tự điều chỉnh.
                # ctk.CTkFrame(col_content_container, height=100, fg_color="transparent").pack()


        # Tạo CTkImage cho khuôn mặt (nếu có) MỘT LẦN
        # và lưu vào self để không bị garbage collected
        self.preview_face_image_ctk_s3_obj = None # Khởi tạo
        if self.current_face_image_b64:
            try:
                img_data = base64.b64decode(self.current_face_image_b64)
                pil_img = Image.open(io.BytesIO(img_data)); 
                preview_size_face = (70, 70) 
                pil_img.thumbnail(preview_size_face, Image.Resampling.LANCZOS)
                final_pil = Image.new("RGBA", preview_size_face, (0,0,0,0)) 
                paste_x = (preview_size_face[0] - pil_img.width) // 2
                paste_y = (preview_size_face[1] - pil_img.height) // 2
                final_pil.paste(pil_img, (paste_x, paste_y))
                self.preview_face_image_ctk_s3_obj = CTkImage(light_image=final_pil,dark_image=final_pil,size=preview_size_face)
            except Exception as e: print(f"Error S3 preview generation (image data): {e}")
        
        # Face Column
        create_bio_detail_column(biometrics_content_cols_frame, 0, "Khuôn Mặt", 
                                 bool(self.current_face_template_b64), 
                                 face_image_ctk_obj=self.preview_face_image_ctk_s3_obj) # Truyền CTkImage object
        
        # Fingerprint Column
        create_bio_detail_column(biometrics_content_cols_frame, 1, "Vân Tay", 
                                 bool(self.current_finger_template_b64))
        
        # RFID Column
        rfid_detail_text_val = f"UID: {self.current_rfid_uid_str}" if self.current_rfid_uid_str else None
        create_bio_detail_column(biometrics_content_cols_frame, 2, "Thẻ RFID", 
                                 bool(self.current_rfid_uid_str), 
                                 rfid_uid_text_val=rfid_detail_text_val)
        
        # Nav Frame (giữ nguyên)
        if hasattr(self, 'nav_frame') and self.nav_frame and self.nav_frame.winfo_exists():
            self.nav_frame.destroy()
        self.nav_frame = ctk.CTkFrame(self.root, fg_color=BG_COLOR)
        self.nav_frame.place(relx=0.5, rely=1.0, anchor="s", relwidth=1.0, relheight=0.12)
        ctk.CTkButton(self.nav_frame, text="CHỈNH SỬA", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH, height=LARGE_BUTTON_HEIGHT, command=self._action_goto_step1_from_step3, image=self.back_icon, compound="left", corner_radius=8, fg_color="#A0A0A0").pack(side="left", pady=(5,8), padx=PAD_X_MAIN_CONTAINER)
        ctk.CTkButton(self.nav_frame, text="GỬI ĐĂNG KÝ", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH + 20, height=LARGE_BUTTON_HEIGHT, command=self.prepare_and_send_data, fg_color=SUCCESS_COLOR, text_color=BUTTON_FG_TEXT, image=self.send_icon_large, compound="right", corner_radius=8).pack(side="right", pady=(5,8), padx=PAD_X_MAIN_CONTAINER)

    def _action_goto_step1_from_step3(self): # (Giữ nguyên)
        if len(self.screen_history) > 0: self.screen_history.pop() 
        if len(self.screen_history) > 0: self.screen_history.pop() 
        self.push_screen("step1_basic_info", self.show_step1_basic_info_screen)

    def generate_active_days_mask_from_list(self): # (Giữ nguyên)
        return "".join(['1' if active else '0' for active in self.active_day_mask_list])

    def prepare_and_send_data(self): # (Giữ nguyên logic, chỉ có thể cần parent cho messagebox)
        parent_msg = self.main_frame or self.root
        if not self.current_room_name_selected:
            messagebox.showerror("Lỗi", "Chọn phòng (Bước 1).", parent=parent_msg); self._action_goto_step1_from_step3(); return
        target_mac = self.discovered_rooms_macs.get(self.current_room_name_selected)
        if not target_mac:
            messagebox.showerror("Lỗi", f"Không có MAC cho phòng '{self.current_room_name_selected}'.", parent=parent_msg); return
        if not all([self.current_id_number, self.current_person_name]):
            messagebox.showerror("Lỗi", "Nhập Họ Tên và Số CCCD (Bước 1).", parent=parent_msg); self._action_goto_step1_from_step3(); return
        if not self._validate_datetime_logic(): self._action_goto_step1_from_step3(); return

        if not (self.current_face_template_b64 or self.current_finger_template_b64 or self.current_rfid_uid_str):
            messagebox.showwarning("Lỗi", "Cần ít nhất một mẫu sinh trắc (Bước 2).", parent=parent_msg)
            if len(self.screen_history) > 0: self.screen_history.pop()
            self.push_screen("step2_biometrics", self.show_step2_biometric_screen); return
        
        bio_datas = []
        if self.current_face_template_b64:
            if not self.current_face_image_b64: messagebox.showerror("Lỗi", "Thiếu ảnh khuôn mặt.", parent=parent_msg); return
            bio_datas.append({"BioType": "FACE", "Template": self.current_face_template_b64, "Img": self.current_face_image_b64})
        if self.current_finger_template_b64: bio_datas.append({"BioType": "FINGER", "Template": self.current_finger_template_b64})
        if self.current_rfid_uid_str: bio_datas.append({"BioType": "IDCARD", "Template": self.current_rfid_uid_str})
        
        from_date = f"{self.from_year_str}-{self.from_month_str}-{self.from_day_str}"
        from_time = f"{self.from_hour_str}:{self.from_minute_str}:{self.from_second_str}"
        to_date = f"{self.to_year_str}-{self.to_month_str}-{self.to_day_str}"
        to_time = f"{self.to_hour_str}:{self.to_minute_str}:{self.to_second_str}"

        payload = {
            "bioId": self.current_bio_id, "idNumber": self.current_id_number, "personName": self.current_person_name,
            "cmdType": "PUSH_NEW_BIO", "bioDatas": bio_datas,
            "fromDate": from_date, "toDate": to_date, "fromTime": from_time, "toTime": to_time,
            "activeDays": self.generate_active_days_mask_from_list()
        }
        
        if self.mqtt_manager:
            published = self.mqtt_manager.publish_enrollment_payload([payload], target_mac)
            # Giả sử publish_enrollment_payload trả về True nếu gửi trực tiếp, False nếu xếp hàng đợi hoặc lỗi publish
            if self.mqtt_manager.connected and published:
                messagebox.showinfo("Thành Công", f"Đã gửi dữ liệu cho '{self.current_person_name}' đến '{self.current_room_name_selected}'.", parent=self.root)
                self.start_new_enrollment_process()
            elif not self.mqtt_manager.connected and not published: # Xếp hàng đợi
                 messagebox.showinfo("Đã Xếp Hàng Đợi", f"Dữ liệu cho '{self.current_person_name}' đã được xếp hàng đợi (MQTT offline).", parent=self.root)
                 self.start_new_enrollment_process()
            else: # Kết nối nhưng publish lỗi, hoặc trường hợp khác
                 messagebox.showerror("Lỗi Gửi", "Không thể gửi dữ liệu. Dữ liệu có thể đã được xếp hàng đợi.", parent=self.root)
        else:
            messagebox.showerror("Lỗi MQTT", "MQTT Manager chưa sẵn sàng.", parent=self.root)

    def reset_enrollment_state_full(self): # (Giữ nguyên)
        self.generate_new_bio_id()
        self.current_id_number = ""; self.current_person_name = ""; self.current_room_name_selected = None
        now = datetime.now()
        self.from_hour_str = "00"; self.from_minute_str = "00"; self.from_second_str = "00"
        self.from_day_str = now.strftime("%d"); self.from_month_str = now.strftime("%m"); self.from_year_str = now.strftime("%Y")
        to_dt = now + timedelta(days=6)
        self.to_hour_str = "23"; self.to_minute_str = "59"; self.to_second_str = "59"
        self.to_day_str = to_dt.strftime("%d"); self.to_month_str = to_dt.strftime("%m"); self.to_year_str = to_dt.strftime("%Y")
        self.active_day_mask_list = [True] * 7
        self.current_face_image_b64 = None; self.current_face_template_b64 = None
        self.current_finger_template_b64 = None; self.current_rfid_uid_str = None
        self.preview_face_image_ctk = None

    def cleanup(self): # (Giữ nguyên)
        face_enroll.stop_face_capture()
        if self.mqtt_manager: self.mqtt_manager.disconnect_client()
        self.root.destroy()

if __name__ == "__main__":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass 
    ctk.set_appearance_mode("Light") 
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
    root.title("Enrollment Station - Compact")
    app = EnrollmentApp(root)
    root.mainloop()