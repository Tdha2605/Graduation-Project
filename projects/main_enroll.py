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
import calendar # For getting days in month

import face_enroll
import fingerprint_enroll
from mqtt_enroll import MQTTEnrollManager
import database_enroll

try:
    from pyfingerprint.pyfingerprint import PyFingerprint
except ImportError:
    PyFingerprint = None
except Exception:
    PyFingerprint = None

DEBUG = True
BG_COLOR = "#F0F0F0"
SCREEN_BG_COLOR = "#E0E0E0"
ACCENT_COLOR = "#007AFF"
BUTTON_FG_TEXT = "#FFFFFF"
SUCCESS_COLOR = "#34C759"
WARNING_COLOR = "#FF9500"
ERROR_COLOR = "#FF3B30"

TITLE_FONT = ("Segoe UI", 26, "bold")
STEP_TITLE_FONT = ("Segoe UI", 22, "bold")
LABEL_FONT = ("Segoe UI", 17)
INPUT_FONT = ("Segoe UI", 17) # Still used for Name/ID
BUTTON_FONT = ("Segoe UI", 18, "bold")
SMALL_STATUS_FONT = ("Segoe UI", 13)
OPTION_MENU_FONT = ("Segoe UI", 15) # Slightly smaller for date/time parts
OPTION_MENU_DROPDOWN_FONT = ("Segoe UI", 14)

LARGE_BUTTON_WIDTH = 280
LARGE_BUTTON_HEIGHT = 70
MEDIUM_BUTTON_WIDTH = 200
MEDIUM_BUTTON_HEIGHT = 55
ENTRY_HEIGHT = 45
OPTION_MENU_HEIGHT = 40 # Adjusted for date/time parts
OPTION_MENU_WIDTH_S = 75  # For day, month, hour, minute, second
OPTION_MENU_WIDTH_M = 100 # For year

ENTRY_WIDTH_LARGE = 350

PAD_X_MAIN = 25
PAD_Y_MAIN = 20
PAD_X_WIDGET = (4, 4) # Reduced for tighter date/time layout
PAD_Y_WIDGET = (5, 5)

CONFIG_FILE = "mqtt_enroll_config.json"
HEALTHCHECK_INTERVAL_MS = 10000

FINGERPRINT_PORT = '/dev/ttyAMA4'
FINGERPRINT_BAUDRATE = 57600

GMT_PLUS_7 = timezone(timedelta(hours=7))

# --- Helper functions for OptionMenu values ---
def get_hour_values(): return [f"{h:02d}" for h in range(24)]
def get_minute_second_values(): return [f"{m:02d}" for m in range(60)]
def get_year_values(start_offset=0, end_offset=10):
    current_year = datetime.now().year
    return [str(y) for y in range(current_year + start_offset, current_year + end_offset + 1)]
def get_month_values(): return [f"{m:02d}" for m in range(1, 13)]
def get_day_values(year_str, month_str):
    try:
        year = int(year_str)
        month = int(month_str)
        num_days = calendar.monthrange(year, month)[1]
        return [f"{d:02d}" for d in range(1, num_days + 1)]
    except (ValueError, TypeError): # Handle cases where year/month might not be valid numbers yet
        return [f"{d:02d}" for d in range(1, 32)] # Default to 31 days

def get_mac_address():
    mac = uuid.getnode()
    mac_str = ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))
    return mac_str

def load_image_ctk(path, size):
    try:
        full_path = os.path.join(script_dir, path)
        if not os.path.exists(full_path):
            return None
        img = Image.open(full_path)
        if size:
            img.thumbnail(size, Image.Resampling.LANCZOS)
        return CTkImage(light_image=img, dark_image=img, size=img.size)
    except Exception:
        return None

# Removed is_valid_date/time_format as we construct from OptionMenus
# Removed parse_date/time as we construct from OptionMenus

class EnrollmentApp:
    def __init__(self, root):
        self.root = root
        self.enroll_mac = get_mac_address()
        if DEBUG: print("[Enroll DEBUG] Enrollment Device MAC Address:", self.enroll_mac)

        try:
            database_enroll.initialize_database()
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to initialize enrollment database: {e}\nApplication cannot continue.")
            root.quit(); return

        self.discovered_rooms_macs = database_enroll.get_all_discovered_devices()
        if DEBUG: print(f"[Enroll DEBUG] Loaded discovered rooms on init: {self.discovered_rooms_macs}")

        # Enrollment data state
        self.current_bio_id = None
        self.current_id_number = ""
        self.current_person_name = ""
        self.current_room_name_selected = None

        # Store individual date/time components as strings from OptionMenus
        self.from_hour_str = "00"
        self.from_minute_str = "00"
        self.from_second_str = "00"
        self.from_day_str = datetime.now().strftime("%d")
        self.from_month_str = datetime.now().strftime("%m")
        self.from_year_str = datetime.now().strftime("%Y")

        self.to_hour_str = "23"
        self.to_minute_str = "59"
        self.to_second_str = "59"
        to_date_default = datetime.now() + timedelta(days=6)
        self.to_day_str = to_date_default.strftime("%d")
        self.to_month_str = to_date_default.strftime("%m")
        self.to_year_str = to_date_default.strftime("%Y")
        
        self.active_day_mask_list = [True] * 7 # Default to all days active

        self.current_face_image_b64 = None
        self.current_face_template_b64 = None
        self.current_finger_template_b64 = None
        self.preview_face_image_ctk = None

        self.mqtt_manager = None
        self.mqtt_config = {}
        self.config_path = os.path.join(script_dir, CONFIG_FILE)
        self.screen_history = []
        self.fingerprint_sensor = None

        self.connection_status_label = None; self.bg_label = None
        self.main_frame = None
        self.config_btn_ref = None

        img_size_status = (40, 40)
        icon_size_large_button = (200, 175)
        icon_size_nav_button = (20,20)

        self.connected_image = load_image_ctk("images/connected.jpg", img_size_status)
        self.disconnected_image = load_image_ctk("images/disconnected.jpg", img_size_status)
        self.bg_photo = load_image_ctk("images/background_enroll.jpeg", (1024, 600))
        self.face_icon_large = load_image_ctk("images/face.png", icon_size_large_button)
        self.fingerprint_icon_large = load_image_ctk("images/fingerprint.png", icon_size_large_button)
        self.next_icon = load_image_ctk("images/next_arrow.png", icon_size_nav_button)
        self.back_icon = load_image_ctk("images/back_arrow.png", icon_size_nav_button)
        self.send_icon_large = load_image_ctk("images/send.png", (35,35))

        self.root.configure(fg_color=BG_COLOR)
        self.show_background()
        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="Chưa kết nối", font=("Segoe UI", 11), text_color=ERROR_COLOR, compound="left")
        self.connection_status_label.place(relx=0.015, rely=0.97, anchor="sw")
        self.create_config_button()

        self.initialize_fingerprint_sensor()

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f: self.mqtt_config = json.load(f)
                if not self.mqtt_config.get("broker") or not self.mqtt_config.get("port"):
                     raise ValueError("Config file missing broker or port.")
                self.initialize_mqtt()
                self.start_new_enrollment_process()
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                if DEBUG: print(f"[Enroll ERROR] Reading/parsing {self.config_path}: {e}.")
                if os.path.exists(self.config_path):
                    try: os.remove(self.config_path); self.mqtt_config = {}
                    except OSError as re: print(f"[Enroll ERROR] Removing invalid config: {re}")
                self.push_screen("mqtt_config", self.build_mqtt_config_screen)
            except Exception as e:
                if DEBUG: print(f"[Enroll ERROR] Unexpected error loading config/init: {e}")
                self.push_screen("mqtt_config", self.build_mqtt_config_screen)
        else:
            self.push_screen("mqtt_config", self.build_mqtt_config_screen)

        self.schedule_healthcheck()
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup)
    
    # ... (keep other methods like generate_new_bio_id, initialize_fingerprint_sensor, etc., as they were) ...
    # Make sure to check and adjust any part that previously relied on:
    # self.valid_from_date_str, self.valid_to_date_str, self.valid_from_time_str, self.valid_to_time_str
    # These will now be constructed from the individual component strings.
    def generate_new_bio_id(self):
        self.current_bio_id = uuid.uuid4().hex[:10].upper() # Shorter Bio ID
        if DEBUG: print(f"[Enroll DEBUG] Generated new Bio ID: {self.current_bio_id}")
        
    def initialize_fingerprint_sensor(self): # For this enrollment station's sensor
        if PyFingerprint is None:
            if DEBUG: print("[Enroll WARN] PyFingerprint library not available, fingerprint sensor disabled.")
            return
        try:
            self.fingerprint_sensor = PyFingerprint(FINGERPRINT_PORT, FINGERPRINT_BAUDRATE, 0xFFFFFFFF, 0x00000000)
            if self.fingerprint_sensor.verifyPassword():
                if DEBUG: print("[Enroll INFO] Enrollment station fingerprint sensor verified.")
            else:
                if DEBUG: print("[Enroll ERROR] Failed to verify enrollment station sensor password.")
                self.fingerprint_sensor = None # Set to None if verification fails
        except Exception as e:
            if DEBUG: print(f"[Enroll ERROR] Failed to initialize enrollment station sensor: {e}")
            self.fingerprint_sensor = None

    def initialize_mqtt(self):
        if self.mqtt_config and not self.mqtt_manager:
            if DEBUG: print("[Enroll DEBUG] Initializing MQTTEnrollManager for enrollment station...")
            self.mqtt_manager = MQTTEnrollManager(
                self.mqtt_config, self.enroll_mac, self.config_path, debug=DEBUG
            )
            self.mqtt_manager.on_connection_status_change = self.update_connection_status
            self.mqtt_manager.on_device_info_received = self.handle_discovered_device_info
            if not self.mqtt_manager.initialize_connection():
                 if DEBUG: print("[Enroll WARN] Initial MQTT connection attempt for enrollment station failed.")

    def handle_discovered_device_info(self, room_name, mac_address):
        if room_name and mac_address:
            current_mac_for_room = self.discovered_rooms_macs.get(room_name)
            self.discovered_rooms_macs[room_name] = mac_address
            
            active_screen_id = self.screen_history[-1][0] if self.screen_history else None
            if active_screen_id == "step1_basic_info" and hasattr(self, 'room_name_option_menu_s1') and \
               self.room_name_option_menu_s1 and self.room_name_option_menu_s1.winfo_exists():
                new_room_options = sorted(list(self.discovered_rooms_macs.keys()))
                current_selection = self.room_name_var_s1.get()
                self.room_name_option_menu_s1.configure(values=new_room_options if new_room_options else ["(Chưa có phòng)"])
                if current_selection in new_room_options:
                    self.room_name_var_s1.set(current_selection)
                elif new_room_options:
                    self.room_name_var_s1.set(new_room_options[0])
                else:
                    self.room_name_var_s1.set("(Chưa có phòng)")
        elif DEBUG:
            print(f"[EnrollApp WARN] Incomplete device info received: room='{room_name}', mac='{mac_address}'")

    def schedule_healthcheck(self):
        if self.mqtt_manager and self.mqtt_manager.connected:
            self.mqtt_manager.send_healthcheck()
        self.root.after(HEALTHCHECK_INTERVAL_MS, self.schedule_healthcheck)

    def update_connection_status(self, is_connected):
        if not hasattr(self,'connection_status_label') or not self.connection_status_label or not self.connection_status_label.winfo_exists(): return
        image_to_show = self.connected_image if is_connected else self.disconnected_image
        text_to_show = "  Đã kết nối" if is_connected else "  Mất kết nối"
        color_to_show = SUCCESS_COLOR if is_connected else ERROR_COLOR
        self.connection_status_label.configure(image=image_to_show, text=text_to_show, text_color=color_to_show)

    def show_background(self):
        if hasattr(self,'bg_photo') and self.bg_photo:
            if hasattr(self,'bg_label') and self.bg_label and self.bg_label.winfo_exists(): self.bg_label.destroy()
            self.bg_label = ctk.CTkLabel(self.root, image=self.bg_photo, text=""); self.bg_label.place(x=0, y=0, relwidth=1, relheight=1); self.bg_label.lower()

    def clear_frames(self, keep_background=True):
        if hasattr(self, 'main_frame') and self.main_frame and self.main_frame.winfo_exists():
            self.main_frame.destroy()
        self.main_frame = None
        if keep_background:
            self.show_background()
            if hasattr(self, 'connection_status_label') and self.connection_status_label and self.connection_status_label.winfo_exists():
                 self.connection_status_label.lift()
            self.create_config_button()

    def push_screen(self, screen_id, screen_func, *args):
        if self.screen_history and self.screen_history[-1][0] == screen_id and not screen_id.startswith("step"):
            return
        self.screen_history.append((screen_id, screen_func, args))
        self.clear_frames()
        self.root.update_idletasks()
        screen_func(*args)

    def go_back(self):
        if len(self.screen_history) > 1:
            self.screen_history.pop()
            prev_screen_id, prev_screen_func, prev_args = self.screen_history[-1]
            self.clear_frames()
            self.root.update_idletasks()
            prev_screen_func(*prev_args)
        elif not (hasattr(self, 'main_frame') and self.main_frame and self.main_frame.winfo_exists()):
             self.start_new_enrollment_process()

    def start_new_enrollment_process(self):
        face_enroll.stop_face_capture()
        self.reset_enrollment_state_full()
        self.screen_history = []
        self.push_screen("step1_basic_info", self.show_step1_basic_info_screen)

    def create_config_button(self):
        if hasattr(self, 'config_btn_ref') and self.config_btn_ref and self.config_btn_ref.winfo_exists():
            self.config_btn_ref.lift(); return
        self.config_btn_ref = ctk.CTkButton(self.root, text="Cài đặt MQTT", command=self.confirm_reconfigure_mqtt, width=150, height=40, fg_color="#6c757d", hover_color="#5a6268", font=("Segoe UI", 14), text_color="white")
        self.config_btn_ref.place(relx=0.985, rely=0.03, anchor="ne")

    def confirm_reconfigure_mqtt(self):
        if messagebox.askyesno("Xác nhận", "Cấu hình lại MQTT cho trạm đăng ký này?", icon='warning', parent=self.root):
            self.reconfigure_mqtt_station()

    def reconfigure_mqtt_station(self):
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client(); self.mqtt_manager = None
            self.update_connection_status(False)
        if os.path.exists(self.config_path):
            try: os.remove(self.config_path)
            except Exception as e: print(f"[Enroll ERROR] Removing config: {e}")
        self.mqtt_config = {}
        self.screen_history = []
        self.push_screen("mqtt_config", self.build_mqtt_config_screen)

    def build_mqtt_config_screen(self): # Kept for MQTT station config
        self.main_frame = ctk.CTkFrame(self.root, fg_color=BG_COLOR, corner_radius=15)
        self.main_frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.7, relheight=0.7)
        ctk.CTkLabel(self.main_frame, text="CẤU HÌNH MQTT (TRẠM ĐĂNG KÝ)", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN + 10, PAD_Y_MAIN + 15))
        form_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        form_frame.pack(pady=PAD_Y_WIDGET, padx=PAD_X_MAIN + 10, fill="x")
        def add_config_row(parent, label_text, placeholder="", default_value=""):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=PAD_Y_WIDGET[0])
            ctk.CTkLabel(row, text=label_text, font=LABEL_FONT, width=180, anchor="w").pack(side="left", padx=(0,10))
            entry = ctk.CTkEntry(row, font=INPUT_FONT, height=ENTRY_HEIGHT, placeholder_text=placeholder)
            entry.pack(side="left", expand=True, fill="x")
            if default_value: entry.insert(0, str(default_value))
            return entry
        self.server_entry_cfg = add_config_row(form_frame, "Broker IP/Domain:", "mqtt.example.com", self.mqtt_config.get("broker"))
        self.port_entry_cfg = add_config_row(form_frame, "Broker Port:", "1883", self.mqtt_config.get("port"))
        self.http_port_entry_cfg = add_config_row(form_frame, "HTTP Port (API):", "8080", self.mqtt_config.get("http_port", "8080"))
        self.enroll_room_entry_cfg = add_config_row(form_frame, "Vị trí trạm ĐK:", "VD: Quầy Lễ Tân", self.mqtt_config.get("enroll_station_room", "EnrollDesk1"))
        button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        button_frame.pack(pady=(PAD_Y_MAIN + 20, PAD_Y_MAIN), padx=PAD_X_MAIN, fill="x", side="bottom")
        ctk.CTkButton(button_frame, text="LƯU & KẾT NỐI", width=MEDIUM_BUTTON_WIDTH+20, height=MEDIUM_BUTTON_HEIGHT, font=BUTTON_FONT, command=self.validate_and_save_mqtt_config, fg_color=ACCENT_COLOR, text_color=BUTTON_FG_TEXT).pack(side="right", padx=PAD_X_WIDGET)
        if len(self.screen_history) > 1 :
             ctk.CTkButton(button_frame, text="TRỞ VỀ", width=MEDIUM_BUTTON_WIDTH, height=MEDIUM_BUTTON_HEIGHT, font=BUTTON_FONT, command=self.go_back, fg_color="#A0A0A0").pack(side="left", padx=PAD_X_WIDGET)

    def validate_and_save_mqtt_config(self): # Kept for MQTT station config
        broker = self.server_entry_cfg.get().strip()
        port_str = self.port_entry_cfg.get().strip()
        http_port_str = self.http_port_entry_cfg.get().strip()
        enroll_station_location = self.enroll_room_entry_cfg.get().strip()
        if not all([broker, port_str, http_port_str, enroll_station_location]):
            messagebox.showerror("Lỗi", "Điền đủ thông tin Broker, Port, HTTP Port, và Vị trí trạm.", parent=self.main_frame); return
        try:
            port = int(port_str); http_port = int(http_port_str)
            if not (0 < port < 65536 and 0 < http_port < 65536): raise ValueError("Port out of range")
        except ValueError: messagebox.showerror("Lỗi", "Port hoặc HTTP Port không hợp lệ.", parent=self.main_frame); return
        new_config = {"broker": broker, "port": port, "http_port": http_port, "enroll_station_room": enroll_station_location}
        try:
            with open(self.config_path, "w") as f: json.dump(new_config, f, indent=2)
            self.mqtt_config = new_config
        except Exception as e:
            messagebox.showerror("Lỗi Lưu Trữ", f"Không thể lưu cấu hình: {e}", parent=self.main_frame); return
        self.show_connecting_screen_mqtt_station()
        self.root.after(100, self._init_mqtt_after_save_config)

    def _init_mqtt_after_save_config(self): # Kept
        if self.mqtt_manager: self.mqtt_manager.disconnect_client(); self.mqtt_manager = None
        self.initialize_mqtt()
        self.root.after(2500, self.start_new_enrollment_process)

    def show_connecting_screen_mqtt_station(self): # Kept
        self.clear_frames()
        self.main_frame = ctk.CTkFrame(self.root, fg_color=BG_COLOR, corner_radius=10)
        self.main_frame.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(self.main_frame, text="Đang kết nối MQTT (Trạm Đăng Ký)...", font=STEP_TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=30, padx=50)
        prog = ctk.CTkProgressBar(self.main_frame, width=400, height=20, corner_radius=10)
        prog.pack(pady=(0,30), padx=50); prog.set(0); prog.start()

    # --- STEP 1: BASIC INFO ---
    def show_step1_basic_info_screen(self):
        self.clear_frames()
        if not self.current_bio_id: self.generate_new_bio_id()

        self.main_frame = ctk.CTkScrollableFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=15)
        self.main_frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.85, relheight=0.75) # Increased width slightly

        ctk.CTkLabel(self.main_frame, text="Bước 1: Thông Tin Cơ Bản", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN-5, PAD_Y_MAIN))

        input_fields_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        input_fields_frame.pack(fill="x", padx=PAD_X_MAIN-5, pady=(0, PAD_Y_WIDGET[1]))

        ctk.CTkLabel(input_fields_frame, text="Họ và Tên (*):", font=LABEL_FONT, anchor="w").pack(fill="x", pady=(PAD_Y_WIDGET[0], 2))
        self.person_name_entry_s1 = ctk.CTkEntry(input_fields_frame, placeholder_text="VD: Nguyễn Văn An", font=INPUT_FONT, height=ENTRY_HEIGHT)
        self.person_name_entry_s1.pack(fill="x", pady=(0, PAD_Y_WIDGET[1]))
        self.person_name_entry_s1.insert(0, self.current_person_name)

        ctk.CTkLabel(input_fields_frame, text="Số CCCD/Mã định danh (*):", font=LABEL_FONT, anchor="w").pack(fill="x", pady=(PAD_Y_WIDGET[0], 2))
        self.id_number_entry_s1 = ctk.CTkEntry(input_fields_frame, placeholder_text="VD: 012345678912", font=INPUT_FONT, height=ENTRY_HEIGHT)
        self.id_number_entry_s1.pack(fill="x", pady=(0, PAD_Y_WIDGET[1]))
        self.id_number_entry_s1.insert(0, self.current_id_number)
        
        ctk.CTkLabel(input_fields_frame, text="Phòng truy cập:", font=LABEL_FONT, anchor="w").pack(fill="x", pady=(PAD_Y_WIDGET[0]+5, 2))
        room_options = sorted(list(self.discovered_rooms_macs.keys()))
        if not room_options : room_options = ["(Chưa có phòng)"]
        current_room_val = self.current_room_name_selected if self.current_room_name_selected in room_options else room_options[0]
        self.room_name_var_s1 = ctk.StringVar(value=current_room_val)
        self.room_name_option_menu_s1 = ctk.CTkOptionMenu(input_fields_frame, variable=self.room_name_var_s1, values=room_options, font=OPTION_MENU_FONT, height=OPTION_MENU_HEIGHT, dynamic_resizing=False, dropdown_font=OPTION_MENU_DROPDOWN_FONT, corner_radius=8)
        self.room_name_option_menu_s1.pack(fill="x", pady=(0, PAD_Y_WIDGET[1]))

        # --- Thời Gian Hiệu Lực Frame ---
        ctk.CTkLabel(self.main_frame, text="Thời Gian Hiệu Lực:", font=LABEL_FONT, anchor="w").pack(fill="x", padx=PAD_X_MAIN-5, pady=(PAD_Y_WIDGET[0]+5, 2))
        
        # FROM Datetime
        from_datetime_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        from_datetime_frame.pack(fill="x", padx=PAD_X_MAIN-5)
        ctk.CTkLabel(from_datetime_frame, text="Từ:", font=LABEL_FONT, width=40, anchor="w").pack(side="left", padx=(0,5))
        
        self.from_hour_var, self.from_min_var, self.from_sec_var, \
        self.from_day_var, self.from_month_var, self.from_year_var, \
        self.from_day_optionmenu = self._create_datetime_pickers(from_datetime_frame, "from")

        # TO Datetime
        to_datetime_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        to_datetime_frame.pack(fill="x", padx=PAD_X_MAIN-5, pady=(PAD_Y_WIDGET[1],0))
        ctk.CTkLabel(to_datetime_frame, text="Đến:", font=LABEL_FONT, width=40, anchor="w").pack(side="left", padx=(0,5))

        self.to_hour_var, self.to_min_var, self.to_sec_var, \
        self.to_day_var, self.to_month_var, self.to_year_var, \
        self.to_day_optionmenu = self._create_datetime_pickers(to_datetime_frame, "to")
        
        # Quick set buttons for validity
        quick_set_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        quick_set_frame.pack(fill="x", padx=PAD_X_MAIN-5, pady=(PAD_Y_WIDGET[0]+3, PAD_Y_WIDGET[1]))
        btn_font_quick = ("Segoe UI", 13)
        btn_h_quick = 35
        ctk.CTkButton(quick_set_frame, text="Mặc định (7 ngày)", font=btn_font_quick, height=btn_h_quick, command=self.set_default_validity_s1).pack(side="left", padx=3)
        ctk.CTkButton(quick_set_frame, text="Hôm nay", font=btn_font_quick, height=btn_h_quick, command=self.set_today_validity_s1).pack(side="left", padx=3)
        ctk.CTkButton(quick_set_frame, text="Xóa thời gian", font=btn_font_quick, height=btn_h_quick, command=self.clear_all_datetime_s1).pack(side="left", padx=3)


        # --- Lịch trong tuần ---
        days_outer_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        days_outer_frame.pack(fill="x", padx=PAD_X_MAIN-5, pady=(PAD_Y_WIDGET[0]+5, 0))
        ctk.CTkLabel(days_outer_frame, text="Lịch hoạt động trong tuần:", font=LABEL_FONT, anchor="w").pack(fill="x", pady=(0,5))
        
        days_checkbox_frame = ctk.CTkFrame(days_outer_frame, fg_color="transparent")
        days_checkbox_frame.pack(fill="x")
        day_names = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        self.day_vars_s1 = []
        for i, day_name in enumerate(day_names):
            var = ctk.BooleanVar(value=self.active_day_mask_list[i]) # Use stored value
            self.day_vars_s1.append(var)
            chk = ctk.CTkCheckBox(days_checkbox_frame, text=day_name, variable=var, font=INPUT_FONT, height=30, checkbox_height=20, checkbox_width=20, corner_radius=5)
            chk.pack(side="left", padx=4, pady=3, expand=True, fill="x")
        
        quick_set_days_frame = ctk.CTkFrame(days_outer_frame, fg_color="transparent")
        quick_set_days_frame.pack(fill="x", pady=(8, PAD_Y_WIDGET[1]))
        ctk.CTkButton(quick_set_days_frame, text="Chọn Tất Cả", font=btn_font_quick, height=btn_h_quick, command=lambda: self.set_all_days_s1(True)).pack(side="left", padx=3)
        ctk.CTkButton(quick_set_days_frame, text="Bỏ Chọn Tất Cả", font=btn_font_quick, height=btn_h_quick, command=lambda: self.set_all_days_s1(False)).pack(side="left", padx=3)

        nav_button_frame_s1 = ctk.CTkFrame(self.root, fg_color=BG_COLOR) 
        nav_button_frame_s1.place(relx=0.5, rely=0.95, anchor="s", relwidth=0.85)
        ctk.CTkButton(nav_button_frame_s1, text="TIẾP TỤC", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH, height=LARGE_BUTTON_HEIGHT -10,
                      command=self._action_goto_step2, fg_color=ACCENT_COLOR, text_color=BUTTON_FG_TEXT, image=self.next_icon, compound="right", corner_radius=10).pack(side="right", pady=8, padx=PAD_X_MAIN-5)

    def _create_datetime_pickers(self, parent_frame, prefix):
        time_frame = ctk.CTkFrame(parent_frame, fg_color="transparent")
        time_frame.pack(side="left", padx=(0,10))
        
        hour_var = ctk.StringVar(value=getattr(self, f"{prefix}_hour_str", "00"))
        om_h = ctk.CTkOptionMenu(time_frame, variable=hour_var, values=get_hour_values(), width=OPTION_MENU_WIDTH_S-10, height=OPTION_MENU_HEIGHT, font=OPTION_MENU_FONT, dropdown_font=OPTION_MENU_DROPDOWN_FONT, corner_radius=6)
        om_h.pack(side="left", padx=2)
        ctk.CTkLabel(time_frame, text=":", font=LABEL_FONT).pack(side="left")
        
        min_var = ctk.StringVar(value=getattr(self, f"{prefix}_minute_str", "00"))
        om_m = ctk.CTkOptionMenu(time_frame, variable=min_var, values=get_minute_second_values(), width=OPTION_MENU_WIDTH_S-10, height=OPTION_MENU_HEIGHT, font=OPTION_MENU_FONT, dropdown_font=OPTION_MENU_DROPDOWN_FONT, corner_radius=6)
        om_m.pack(side="left", padx=2)
        ctk.CTkLabel(time_frame, text=":", font=LABEL_FONT).pack(side="left")
        
        sec_var = ctk.StringVar(value=getattr(self, f"{prefix}_second_str", "00"))
        om_s = ctk.CTkOptionMenu(time_frame, variable=sec_var, values=get_minute_second_values(), width=OPTION_MENU_WIDTH_S-10, height=OPTION_MENU_HEIGHT, font=OPTION_MENU_FONT, dropdown_font=OPTION_MENU_DROPDOWN_FONT, corner_radius=6)
        om_s.pack(side="left", padx=(2,8))

        date_frame = ctk.CTkFrame(parent_frame, fg_color="transparent")
        date_frame.pack(side="left")

        year_var = ctk.StringVar(value=getattr(self, f"{prefix}_year_str", datetime.now().strftime("%Y")))
        month_var = ctk.StringVar(value=getattr(self, f"{prefix}_month_str", datetime.now().strftime("%m")))
        day_var = ctk.StringVar(value=getattr(self, f"{prefix}_day_str", datetime.now().strftime("%d")))

        # Day (needs to be updated dynamically)
        day_values = get_day_values(year_var.get(), month_var.get())
        if day_var.get() not in day_values: day_var.set(day_values[0] if day_values else "01")
        om_d = ctk.CTkOptionMenu(date_frame, variable=day_var, values=day_values, width=OPTION_MENU_WIDTH_S-10, height=OPTION_MENU_HEIGHT, font=OPTION_MENU_FONT, dropdown_font=OPTION_MENU_DROPDOWN_FONT, corner_radius=6)
        om_d.pack(side="left", padx=2)
        ctk.CTkLabel(date_frame, text="/", font=LABEL_FONT).pack(side="left")

        # Month
        om_month = ctk.CTkOptionMenu(date_frame, variable=month_var, values=get_month_values(), width=OPTION_MENU_WIDTH_S-10, height=OPTION_MENU_HEIGHT, font=OPTION_MENU_FONT, dropdown_font=OPTION_MENU_DROPDOWN_FONT, corner_radius=6,
                                     command=lambda _: self._update_days_for_picker(prefix, day_var, year_var.get(), month_var.get(), om_d))
        om_month.pack(side="left", padx=2)
        ctk.CTkLabel(date_frame, text="/", font=LABEL_FONT).pack(side="left")
        
        # Year
        om_year = ctk.CTkOptionMenu(date_frame, variable=year_var, values=get_year_values(), width=OPTION_MENU_WIDTH_M-15, height=OPTION_MENU_HEIGHT, font=OPTION_MENU_FONT, dropdown_font=OPTION_MENU_DROPDOWN_FONT, corner_radius=6,
                                    command=lambda _: self._update_days_for_picker(prefix, day_var, year_var.get(), month_var.get(), om_d))
        om_year.pack(side="left", padx=2)
        
        return hour_var, min_var, sec_var, day_var, month_var, year_var, om_d # Return day_optionmenu to update it

    def _update_days_for_picker(self, prefix, day_var, year_str, month_str, day_optionmenu_widget):
        if not (day_optionmenu_widget and day_optionmenu_widget.winfo_exists()):
            return
        new_day_values = get_day_values(year_str, month_str)
        current_day = day_var.get()
        
        day_optionmenu_widget.configure(values=new_day_values)
        if current_day in new_day_values:
            day_var.set(current_day)
        elif new_day_values: # If current day is invalid, set to first valid day
            day_var.set(new_day_values[0])
        else: # Should not happen if year/month are somewhat valid
            day_var.set("01")
        
        # Store the updated year/month immediately as the command is on their change
        setattr(self, f"{prefix}_year_str", year_str)
        setattr(self, f"{prefix}_month_str", month_str)


    def set_default_validity_s1(self):
        today = datetime.now()
        seven_days_later = today + timedelta(days=6)

        self.from_hour_var.set("00"); self.from_min_var.set("00"); self.from_sec_var.set("00")
        self.from_day_var.set(today.strftime("%d"))
        self.from_month_var.set(today.strftime("%m"))
        self.from_year_var.set(today.strftime("%Y"))
        self._update_days_for_picker("from", self.from_day_var, self.from_year_var.get(), self.from_month_var.get(), self.from_day_optionmenu)


        self.to_hour_var.set("23"); self.to_min_var.set("59"); self.to_sec_var.set("59")
        self.to_day_var.set(seven_days_later.strftime("%d"))
        self.to_month_var.set(seven_days_later.strftime("%m"))
        self.to_year_var.set(seven_days_later.strftime("%Y"))
        self._update_days_for_picker("to", self.to_day_var, self.to_year_var.get(), self.to_month_var.get(), self.to_day_optionmenu)


    def set_today_validity_s1(self):
        today = datetime.now()
        self.from_hour_var.set("00"); self.from_min_var.set("00"); self.from_sec_var.set("00")
        self.from_day_var.set(today.strftime("%d"))
        self.from_month_var.set(today.strftime("%m"))
        self.from_year_var.set(today.strftime("%Y"))
        self._update_days_for_picker("from", self.from_day_var, self.from_year_var.get(), self.from_month_var.get(), self.from_day_optionmenu)

        self.to_hour_var.set("23"); self.to_min_var.set("59"); self.to_sec_var.set("59")
        self.to_day_var.set(today.strftime("%d"))
        self.to_month_var.set(today.strftime("%m"))
        self.to_year_var.set(today.strftime("%Y"))
        self._update_days_for_picker("to", self.to_day_var, self.to_year_var.get(), self.to_month_var.get(), self.to_day_optionmenu)
    
    def clear_all_datetime_s1(self):
        # Reset to some sensible defaults or empty if you allow that
        # For now, resetting to current day 00:00:00 to tomorrow 23:59:59
        self.from_hour_var.set("00"); self.from_min_var.set("00"); self.from_sec_var.set("00")
        self.from_day_var.set(datetime.now().strftime("%d"))
        self.from_month_var.set(datetime.now().strftime("%m"))
        self.from_year_var.set(datetime.now().strftime("%Y"))
        self._update_days_for_picker("from", self.from_day_var, self.from_year_var.get(), self.from_month_var.get(), self.from_day_optionmenu)

        tomorrow = datetime.now() + timedelta(days=1)
        self.to_hour_var.set("23"); self.to_min_var.set("59"); self.to_sec_var.set("59")
        self.to_day_var.set(tomorrow.strftime("%d"))
        self.to_month_var.set(tomorrow.strftime("%m"))
        self.to_year_var.set(tomorrow.strftime("%Y"))
        self._update_days_for_picker("to", self.to_day_var, self.to_year_var.get(), self.to_month_var.get(), self.to_day_optionmenu)


    def set_all_days_s1(self, select_all):
        if hasattr(self, 'day_vars_s1'):
            for var in self.day_vars_s1:
                var.set(select_all)

    def _save_step1_data(self):
        self.current_person_name = self.person_name_entry_s1.get().strip()
        self.current_id_number = self.id_number_entry_s1.get().strip()
        self.current_room_name_selected = self.room_name_var_s1.get()
        if self.current_room_name_selected == "(Chưa có phòng)": self.current_room_name_selected = None

        self.from_hour_str = self.from_hour_var.get()
        self.from_minute_str = self.from_min_var.get()
        self.from_second_str = self.from_sec_var.get()
        self.from_day_str = self.from_day_var.get()
        self.from_month_str = self.from_month_var.get()
        self.from_year_str = self.from_year_var.get()

        self.to_hour_str = self.to_hour_var.get()
        self.to_minute_str = self.to_min_var.get()
        self.to_second_str = self.to_sec_var.get()
        self.to_day_str = self.to_day_var.get()
        self.to_month_str = self.to_month_var.get()
        self.to_year_str = self.to_year_var.get()
        
        if hasattr(self, 'day_vars_s1') and self.day_vars_s1:
            self.active_day_mask_list = [var.get() for var in self.day_vars_s1]
        return True

    def _action_goto_step2(self):
        if not (hasattr(self, 'person_name_entry_s1') and hasattr(self, 'id_number_entry_s1')):
             messagebox.showerror("Lỗi Giao Diện", "Không tìm thấy trường nhập liệu. Vui lòng thử lại.", parent=self.root)
             return

        person_name = self.person_name_entry_s1.get().strip()
        id_number = self.id_number_entry_s1.get().strip()

        if not person_name or not id_number:
            messagebox.showerror("Thiếu thông tin", "Vui lòng nhập Họ Tên và Số CCCD.", parent=self.main_frame if self.main_frame and self.main_frame.winfo_exists() else self.root)
            return
        
        if not self._validate_datetime_logic(): # Validate before proceeding
            return

        self._save_step1_data()
        self.push_screen("step2_biometrics", self.show_step2_biometric_screen)
    
    def _validate_datetime_logic(self):
        try:
            from_dt_str = f"{self.from_year_var.get()}-{self.from_month_var.get()}-{self.from_day_var.get()} {self.from_hour_var.get()}:{self.from_min_var.get()}:{self.from_sec_var.get()}"
            to_dt_str = f"{self.to_year_var.get()}-{self.to_month_var.get()}-{self.to_day_var.get()} {self.to_hour_var.get()}:{self.to_min_var.get()}:{self.to_sec_var.get()}"
            
            from_datetime_obj = datetime.strptime(from_dt_str, "%Y-%m-%d %H:%M:%S")
            to_datetime_obj = datetime.strptime(to_dt_str, "%Y-%m-%d %H:%M:%S")

            if to_datetime_obj < from_datetime_obj:
                messagebox.showerror("Lỗi Thời Gian", "'Thời gian đến' không thể trước 'Thời gian từ'.", 
                                     parent=self.main_frame if self.main_frame and self.main_frame.winfo_exists() else self.root)
                return False
        except ValueError:
            messagebox.showerror("Lỗi Thời Gian", "Ngày giờ không hợp lệ. Vui lòng kiểm tra lại.",
                                 parent=self.main_frame if self.main_frame and self.main_frame.winfo_exists() else self.root)
            return False
        return True

    # --- STEP 2: BIOMETRIC ENROLLMENT (Largely unchanged, ensure callbacks return to step 2) ---
    def show_step2_biometric_screen(self):
        self.clear_frames()
        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=15)
        self.main_frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.85, relheight=0.75)
        ctk.CTkLabel(self.main_frame, text="Bước 2: Đăng Ký Sinh Trắc Học", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN, PAD_Y_MAIN + 5))
        info_text = f"Đăng ký cho: {self.current_person_name} ({self.current_id_number})"
        ctk.CTkLabel(self.main_frame, text=info_text, font=LABEL_FONT).pack(pady=(0, PAD_Y_MAIN + 10))
        buttons_container_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        buttons_container_frame.pack(expand=True, fill="both", padx=PAD_X_MAIN, pady=PAD_Y_WIDGET)
        buttons_container_frame.columnconfigure((0,1), weight=1)
        buttons_container_frame.rowconfigure(0, weight=1)
        face_button_frame = ctk.CTkFrame(buttons_container_frame, fg_color="transparent")
        face_button_frame.grid(row=0, column=0, padx=PAD_X_WIDGET, pady=PAD_Y_WIDGET, sticky="nsew")
        self.face_enroll_btn_s2 = ctk.CTkButton(face_button_frame, image=self.face_icon_large, text="KHUÔN MẶT", font=BUTTON_FONT, compound="top", width=LARGE_BUTTON_WIDTH, height=LARGE_BUTTON_HEIGHT * 2.2, command=self.start_face_enrollment_s2, corner_radius=12, border_spacing=15)
        self.face_enroll_btn_s2.pack(expand=True, pady=(10,0))
        self.face_status_label_s2 = ctk.CTkLabel(face_button_frame, text="", font=SMALL_STATUS_FONT)
        self.face_status_label_s2.pack(pady=(5,10))
        finger_button_frame = ctk.CTkFrame(buttons_container_frame, fg_color="transparent")
        finger_button_frame.grid(row=0, column=1, padx=PAD_X_WIDGET, pady=PAD_Y_WIDGET, sticky="nsew")
        self.finger_enroll_btn_s2 = ctk.CTkButton(finger_button_frame, image=self.fingerprint_icon_large, text="VÂN TAY", font=BUTTON_FONT, compound="top", width=LARGE_BUTTON_WIDTH, height=LARGE_BUTTON_HEIGHT * 2.2, command=self.start_fingerprint_enrollment_s2, corner_radius=12, border_spacing=15)
        self.finger_enroll_btn_s2.pack(expand=True, pady=(10,0))
        self.finger_status_label_s2 = ctk.CTkLabel(finger_button_frame, text="", font=SMALL_STATUS_FONT)
        self.finger_status_label_s2.pack(pady=(5,10))
        self._update_biometric_status_s2()
        nav_button_frame_s2 = ctk.CTkFrame(self.root, fg_color=BG_COLOR)
        nav_button_frame_s2.place(relx=0.5, rely=0.95, anchor="s", relwidth=0.85)
        ctk.CTkButton(nav_button_frame_s2, text="QUAY LẠI", font=BUTTON_FONT, width=MEDIUM_BUTTON_WIDTH, height=MEDIUM_BUTTON_HEIGHT, command=self.go_back, image=self.back_icon, compound="left", corner_radius=10, fg_color="#A0A0A0").pack(side="left", pady=10, padx=PAD_X_MAIN)
        self.next_step3_button = ctk.CTkButton(nav_button_frame_s2, text="TIẾP TỤC", font=BUTTON_FONT, width=MEDIUM_BUTTON_WIDTH, height=MEDIUM_BUTTON_HEIGHT, command=self._action_goto_step3, fg_color=ACCENT_COLOR, text_color=BUTTON_FG_TEXT, image=self.next_icon, compound="right", corner_radius=10)
        self.next_step3_button.pack(side="right", pady=10, padx=PAD_X_MAIN)
        self._update_next_button_step2_state()

    def _update_biometric_status_s2(self):
        if hasattr(self, 'face_status_label_s2') and self.face_status_label_s2.winfo_exists():
            if self.current_face_template_b64:
                self.face_status_label_s2.configure(text="Đã đăng ký", text_color=SUCCESS_COLOR)
                self.face_enroll_btn_s2.configure(fg_color=SUCCESS_COLOR, hover_color="#2b9e4c")
            else:
                self.face_status_label_s2.configure(text="Chưa đăng ký", text_color="grey60")
                self.face_enroll_btn_s2.configure(fg_color="#707070", hover_color="#808080")
        if hasattr(self, 'finger_status_label_s2') and self.finger_status_label_s2.winfo_exists():
            if self.current_finger_template_b64:
                self.finger_status_label_s2.configure(text="Đã đăng ký", text_color=SUCCESS_COLOR)
                self.finger_enroll_btn_s2.configure(fg_color=SUCCESS_COLOR, hover_color="#2b9e4c")
            else:
                self.finger_status_label_s2.configure(text="Chưa đăng ký", text_color="grey60")
                self.finger_enroll_btn_s2.configure(fg_color="#707070", hover_color="#808080")
        self._update_next_button_step2_state()

    def _update_next_button_step2_state(self):
         if hasattr(self, 'next_step3_button') and self.next_step3_button.winfo_exists():
            if self.current_face_template_b64 or self.current_finger_template_b64:
                self.next_step3_button.configure(state="normal", fg_color=ACCENT_COLOR)
            else:
                self.next_step3_button.configure(state="disabled", fg_color="#A0A0A0")

    def start_face_enrollment_s2(self):
        face_enroll.capture_face_for_enrollment(parent=self.root, on_success_callback=self.handle_face_enroll_success_s2, on_cancel_callback=self.handle_face_enroll_cancel_s2)
    def handle_face_enroll_success_s2(self, image_b64, template_b64):
        self.current_face_image_b64 = image_b64
        self.current_face_template_b64 = template_b64
        self._schedule_return_to_step2()
    def handle_face_enroll_cancel_s2(self): self._schedule_return_to_step2()
    def start_fingerprint_enrollment_s2(self):
        current_main_frame_parent = self.main_frame if self.main_frame and self.main_frame.winfo_exists() else self.root
        if not self.fingerprint_sensor:
            messagebox.showerror("Lỗi Cảm Biến", "Cảm biến vân tay trạm ĐK chưa sẵn sàng.", parent=current_main_frame_parent); return
        try:
            if not self.fingerprint_sensor.verifyPassword():
                messagebox.showerror("Lỗi Cảm Biến", "Lỗi xác thực cảm biến vân tay.", parent=current_main_frame_parent); return
        except Exception as e:
            messagebox.showerror("Lỗi Cảm Biến", f"Lỗi giao tiếp cảm biến vân tay: {e}", parent=current_main_frame_parent); return
        fingerprint_enroll.enroll_fingerprint_template(parent=self.root, sensor=self.fingerprint_sensor, on_success_callback=self.handle_finger_enroll_success_s2, on_failure_callback=self.handle_finger_enroll_failure_s2, on_cancel_callback=self.handle_finger_enroll_cancel_s2)
    def handle_finger_enroll_success_s2(self, template_b64):
        self.current_finger_template_b64 = template_b64
        self._schedule_return_to_step2()
    def handle_finger_enroll_failure_s2(self, reason=""):
        messagebox.showerror("Lỗi Vân Tay", f"Đăng ký vân tay thất bại: {reason}", parent=self.root)
        self._schedule_return_to_step2()
    def handle_finger_enroll_cancel_s2(self): self._schedule_return_to_step2()
    def _schedule_return_to_step2(self):
        self.root.after(10, lambda: self.push_screen("step2_biometrics", self.show_step2_biometric_screen))
    def _action_goto_step3(self):
        if not self.current_face_template_b64 and not self.current_finger_template_b64:
            messagebox.showwarning("Thiếu Sinh Trắc Học", "Cần đăng ký ít nhất một mẫu sinh trắc (Khuôn mặt hoặc Vân tay).", parent=self.main_frame if self.main_frame and self.main_frame.winfo_exists() else self.root)
            return
        self.push_screen("step3_confirmation", self.show_step3_confirmation_screen)


    # --- STEP 3: CONFIRMATION ---
        # --- STEP 3: CONFIRMATION ---
    def show_step3_confirmation_screen(self):
        self.clear_frames()
        self.main_frame = ctk.CTkScrollableFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=15)
        # Điều chỉnh relwidth/relheight tại đây nếu bạn muốn khung tổng thể của Bước 3 nhỏ hơn
        # Ví dụ: self.main_frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.85, relheight=0.80)
        self.main_frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.85, relheight=0.75) # Giá trị ví dụ

        ctk.CTkLabel(self.main_frame, text="Bước 3: Xác Nhận Thông Tin", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN-5, PAD_Y_MAIN + 5))

        # --- Helper function to create info sections ---
        def create_info_section(parent, title):
            section_frame = ctk.CTkFrame(parent, fg_color=BG_COLOR, corner_radius=10)
            section_frame.pack(fill="x", padx=PAD_X_MAIN, pady=(PAD_Y_WIDGET[0]+5, PAD_Y_WIDGET[1]))
            
            ctk.CTkLabel(section_frame, text=title, font=STEP_TITLE_FONT, text_color=ACCENT_COLOR, anchor="w").pack(fill="x", padx=15, pady=(10, 5))
            
            content_section_frame = ctk.CTkFrame(section_frame, fg_color="transparent")
            content_section_frame.pack(fill="x", padx=15, pady=(0,10))
            content_section_frame.columnconfigure(1, weight=1) # Cho phép giá trị mở rộng
            return content_section_frame

        # --- Helper function to add rows within a section ---
        def add_info_row_to_section(content_frame, row_idx_ref, label_text, value_text_or_widget, is_widget=False, value_font=None):
            effective_value_font = value_font if value_font else INPUT_FONT

            ctk.CTkLabel(content_frame, text=f"{label_text}:", font=LABEL_FONT, anchor="e", justify="right").grid(row=row_idx_ref[0], column=0, sticky="ne", padx=(0,10), pady=4)
            if is_widget:
                value_text_or_widget.grid(row=row_idx_ref[0], column=1, sticky="nw", pady=4)
            else:
                val_str = str(value_text_or_widget) if value_text_or_widget is not None else "N/A"
                ctk.CTkLabel(content_frame, text=val_str, font=effective_value_font, anchor="w", wraplength=450).grid(row=row_idx_ref[0], column=1, sticky="nw", pady=4)
            row_idx_ref[0] += 1


        # --- Section 1: Personal Information ---
        personal_info_content = create_info_section(self.main_frame, "Thông Tin Cá Nhân")
        personal_row_idx = [0] 
        add_info_row_to_section(personal_info_content, personal_row_idx, "Họ và Tên", self.current_person_name)
        add_info_row_to_section(personal_info_content, personal_row_idx, "Số CCCD/Mã ID", self.current_id_number)
        add_info_row_to_section(personal_info_content, personal_row_idx, "Bio ID (Hệ thống)", self.current_bio_id, value_font=("Segoe UI", 17, "italic"))
        add_info_row_to_section(personal_info_content, personal_row_idx, "Phòng truy cập", self.current_room_name_selected or "Chưa chọn")

        # --- Section 2: Validity Period ---
        validity_content = create_info_section(self.main_frame, "Thời Gian Hiệu Lực")
        validity_row_idx = [0]
        from_date_display = f"{self.from_day_str}/{self.from_month_str}/{self.from_year_str}"
        from_time_display = f"{self.from_hour_str}:{self.from_minute_str}:{self.from_second_str}"
        to_date_display = f"{self.to_day_str}/{self.to_month_str}/{self.to_year_str}"
        to_time_display = f"{self.to_hour_str}:{self.to_minute_str}:{self.to_second_str}"
        add_info_row_to_section(validity_content, validity_row_idx, "Hiệu lực từ", f"{from_date_display}  {from_time_display}")
        add_info_row_to_section(validity_content, validity_row_idx, "Hiệu lực đến", f"{to_date_display}  {to_time_display}")
        
        days = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ Nhật"] # Sửa lại tên ngày cho dễ đọc hơn
        active_days_str = ", ".join([days[i] for i, active in enumerate(self.active_day_mask_list) if active])
        add_info_row_to_section(validity_content, validity_row_idx, "Lịch hoạt động", active_days_str if active_days_str else "Không chọn ngày nào")


        # --- Section 3: Biometrics ---
        biometrics_content_section = create_info_section(self.main_frame, "Thông Tin Sinh Trắc Học")
        
        # Frame chính để chứa cả Face và Fingerprint ngang nhau bên trong section
        biometrics_main_horizontal_frame = ctk.CTkFrame(biometrics_content_section, fg_color="transparent")
        biometrics_main_horizontal_frame.pack(fill="x", pady=(0,5)) # Giảm pady nếu cần

        # --- Cột Khuôn mặt ---
        face_column_frame = ctk.CTkFrame(biometrics_main_horizontal_frame, fg_color="transparent")
        face_column_frame.pack(side="left", padx=(0, 20), expand=False, fill="y", anchor="nw") # expand=False, fill="y"

        # Nhãn và trạng thái khuôn mặt
        face_label_status_frame = ctk.CTkFrame(face_column_frame, fg_color="transparent")
        face_label_status_frame.pack(anchor="w", pady=(0,5))
        ctk.CTkLabel(face_label_status_frame, text="Khuôn mặt:", font=LABEL_FONT).pack(side="left", padx=(0,10))
        face_status_text = "ĐÃ ĐĂNG KÝ" if self.current_face_template_b64 else "CHƯA ĐĂNG KÝ"
        face_color = SUCCESS_COLOR if self.current_face_template_b64 else WARNING_COLOR
        ctk.CTkLabel(face_label_status_frame, text=face_status_text, font=INPUT_FONT, text_color=face_color).pack(side="left")

        # Ảnh preview khuôn mặt (nếu có)
        if self.current_face_image_b64:
            try:
                image_data = base64.b64decode(self.current_face_image_b64)
                pil_image = Image.open(io.BytesIO(image_data))
                preview_size = (150, 150) 
                pil_image.thumbnail(preview_size, Image.Resampling.LANCZOS)
                
                final_pil_image = Image.new("RGBA", preview_size, (0,0,0,0))
                paste_x = (preview_size[0] - pil_image.width) // 2
                paste_y = (preview_size[1] - pil_image.height) // 2
                final_pil_image.paste(pil_image, (paste_x, paste_y))

                self.preview_face_image_ctk = CTkImage(light_image=final_pil_image, dark_image=final_pil_image, size=preview_size)
                
                img_preview_frame = ctk.CTkFrame(face_column_frame, fg_color="gray50", corner_radius=6, border_width=1, border_color="gray40")
                img_preview_frame.pack(anchor="w", padx=(0,5)) 
                img_label = ctk.CTkLabel(img_preview_frame, image=self.preview_face_image_ctk, text="")
                img_label.pack(padx=1, pady=1)
            except Exception as e:
                if DEBUG: print(f"Error loading preview image: {e}")
                ctk.CTkLabel(face_column_frame, text="(Lỗi ảnh)", font=INPUT_FONT, text_color=ERROR_COLOR).pack(anchor="w")
        
        # --- Cột Vân tay ---
        fingerprint_column_frame = ctk.CTkFrame(biometrics_main_horizontal_frame, fg_color="transparent")
        # Căn chỉnh frame này để nó bắt đầu từ cùng một chiều cao với face_label_status_frame
        fingerprint_column_frame.pack(side="left", padx=(20, 0), expand=False, fill="y", anchor="nw")

        # Nhãn và trạng thái vân tay
        fp_label_status_frame = ctk.CTkFrame(fingerprint_column_frame, fg_color="transparent")
        fp_label_status_frame.pack(anchor="w", pady=(0,5)) # pady giống face_label_status_frame
        ctk.CTkLabel(fp_label_status_frame, text="Vân tay:", font=LABEL_FONT).pack(side="left", padx=(0,10))
        finger_status_text = "ĐÃ ĐĂNG KÝ" if self.current_finger_template_b64 else "CHƯA ĐĂNG KÝ"
        finger_color = SUCCESS_COLOR if self.current_finger_template_b64 else WARNING_COLOR
        ctk.CTkLabel(fp_label_status_frame, text=finger_status_text, font=INPUT_FONT, text_color=finger_color).pack(side="left")
        # (Không có ảnh preview cho vân tay ở đây)


        # --- Navigation Buttons ---
        # Đảm bảo relwidth của nav_button_frame_s3 khớp với main_frame nếu bạn đã điều chỉnh nó
        nav_button_frame_s3 = ctk.CTkFrame(self.root, fg_color=BG_COLOR)
        nav_button_frame_s3.place(relx=0.5, rely=0.95, anchor="s", relwidth=0.85) # Phải khớp với relwidth của main_frame
        
        ctk.CTkButton(nav_button_frame_s3, text="CHỈNH SỬA", font=BUTTON_FONT, width=MEDIUM_BUTTON_WIDTH, height=MEDIUM_BUTTON_HEIGHT,
                      command=self._action_goto_step1_from_step3, image=self.back_icon, compound="left", corner_radius=10, fg_color="#A0A0A0").pack(side="left", pady=10, padx=PAD_X_MAIN)
        
        ctk.CTkButton(nav_button_frame_s3, text="GỬI ĐĂNG KÝ", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH - 40, height=LARGE_BUTTON_HEIGHT -10,
                      command=self.prepare_and_send_data, fg_color=SUCCESS_COLOR, text_color=BUTTON_FG_TEXT, image=self.send_icon_large, compound="right", corner_radius=10).pack(side="right", pady=10, padx=PAD_X_MAIN)
    def _action_goto_step1_from_step3(self):
        if len(self.screen_history) > 0: self.screen_history.pop()
        if len(self.screen_history) > 0: self.screen_history.pop()
        self.push_screen("step1_basic_info", self.show_step1_basic_info_screen)

    def generate_active_days_mask_from_list(self):
        return "".join(['1' if active else '0' for active in self.active_day_mask_list])

    def prepare_and_send_data(self):
        parent_frame_for_msgbox = self.main_frame if self.main_frame and self.main_frame.winfo_exists() else self.root
        if not self.current_room_name_selected:
            messagebox.showerror("Lỗi", "Vui lòng chọn một phòng (ở Bước 1).", parent=parent_frame_for_msgbox)
            self._action_goto_step1_from_step3(); return
        target_mac = self.discovered_rooms_macs.get(self.current_room_name_selected)
        if not target_mac:
            messagebox.showerror("Lỗi", f"Không tìm thấy địa chỉ MAC cho phòng '{self.current_room_name_selected}'.", parent=parent_frame_for_msgbox); return
        if not all([self.current_id_number, self.current_person_name]):
            messagebox.showerror("Lỗi", "Vui lòng điền Họ Tên và Số CCCD (ở Bước 1).", parent=parent_frame_for_msgbox)
            self._action_goto_step1_from_step3(); return
        
        if not self._validate_datetime_logic(): # Re-validate before sending
            self._action_goto_step1_from_step3(); return # Go to step 1 if invalid

        if not self.current_face_template_b64 and not self.current_finger_template_b64:
            messagebox.showwarning("Lỗi Sinh Trắc", "Cần đăng ký ít nhất một mẫu (Khuôn mặt hoặc Vân tay) (ở Bước 2).", parent=parent_frame_for_msgbox)
            if len(self.screen_history) > 0: self.screen_history.pop()
            self.push_screen("step2_biometrics", self.show_step2_biometric_screen); return
        
        bio_datas = []
        if self.current_face_template_b64:
            if not self.current_face_image_b64:
                 messagebox.showerror("Lỗi Dữ Liệu", "Thiếu ảnh khuôn mặt cho template đã đăng ký.", parent=parent_frame_for_msgbox); return
            bio_datas.append({"BioType": "FACE", "Template": self.current_face_template_b64, "Img": self.current_face_image_b64})
        if self.current_finger_template_b64:
            bio_datas.append({"BioType": "FINGER", "Template": self.current_finger_template_b64})
        
        # Construct YYYY-MM-DD and HH:MM:SS from individual components
        final_from_date_str = f"{self.from_year_str}-{self.from_month_str}-{self.from_day_str}"
        final_from_time_str = f"{self.from_hour_str}:{self.from_minute_str}:{self.from_second_str}"
        final_to_date_str = f"{self.to_year_str}-{self.to_month_str}-{self.to_day_str}"
        final_to_time_str = f"{self.to_hour_str}:{self.to_minute_str}:{self.to_second_str}"

        payload_object = {
            "bioId": self.current_bio_id, "idNumber": self.current_id_number, "personName": self.current_person_name,
            "cmdType": "PUSH_NEW_BIO", "bioDatas": bio_datas,
            "fromDate": final_from_date_str, "toDate": final_to_date_str,
            "fromTime": final_from_time_str, "toTime": final_to_time_str,
            "activeDays": self.generate_active_days_mask_from_list()
        }
        
        if self.mqtt_manager:
            publish_was_attempted_or_queued = self.mqtt_manager.publish_enrollment_payload([payload_object], target_mac)
            if self.mqtt_manager.connected and publish_was_attempted_or_queued:
                messagebox.showinfo("Thành Công", f"Dữ liệu cho '{self.current_person_name}' đã được gửi thành công đến '{self.current_room_name_selected}'.", parent=self.root)
                self.start_new_enrollment_process()
            elif not self.mqtt_manager.connected and isinstance(publish_was_attempted_or_queued, bool) and not publish_was_attempted_or_queued:
                 messagebox.showinfo("Đã Xếp Hàng Đợi", f"Dữ liệu cho '{self.current_person_name}' đã được xếp hàng đợi do MQTT chưa kết nối.", parent=self.root)
                 self.start_new_enrollment_process()
            else:
                 messagebox.showerror("Lỗi Gửi MQTT", "Không thể gửi dữ liệu. Dữ liệu có thể đã được xếp hàng đợi nếu MQTT không kết nối.", parent=self.root)
        else:
            messagebox.showerror("Lỗi MQTT", "MQTT Manager chưa được khởi tạo. Không thể gửi dữ liệu.", parent=self.root)

    def reset_enrollment_state_full(self):
        self.generate_new_bio_id()
        self.current_id_number = ""
        self.current_person_name = ""
        self.current_room_name_selected = None

        now = datetime.now()
        self.from_hour_str = "00"; self.from_minute_str = "00"; self.from_second_str = "00"
        self.from_day_str = now.strftime("%d"); self.from_month_str = now.strftime("%m"); self.from_year_str = now.strftime("%Y")
        
        to_dt = now + timedelta(days=6)
        self.to_hour_str = "23"; self.to_minute_str = "59"; self.to_second_str = "59"
        self.to_day_str = to_dt.strftime("%d"); self.to_month_str = to_dt.strftime("%m"); self.to_year_str = to_dt.strftime("%Y")
        
        self.active_day_mask_list = [True] * 7 # Reset to all true

        self.current_face_image_b64 = None; self.current_face_template_b64 = None
        self.current_finger_template_b64 = None
        self.preview_face_image_ctk = None
        # UI elements related to these (like OptionMenu vars) will be reset when show_step1 is called

    def cleanup(self):
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
    root.geometry("1024x600")
    root.title("Enrollment Station - DateTime Pickers")
    app = EnrollmentApp(root)
    root.mainloop()