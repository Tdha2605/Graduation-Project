import os
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

import json
import uuid
import customtkinter as ctk
from tkinter import messagebox # Removed ttk as it's not directly used by CTk for Treeview
from PIL import Image
from customtkinter import CTkImage
from datetime import datetime, timezone, timedelta, time as dt_time, date as dt_date # Added timedelta
import io
import base64
import time

import face_enroll
import fingerprint_enroll
from mqtt_enroll import MQTTEnrollManager # Your updated MQTT manager for enrollment
import database_enroll # Your updated database for enrollment

try:
    from pyfingerprint.pyfingerprint import PyFingerprint # FINGERPRINT_CHARBUFFER1 not used here
except ImportError:
    # print("[ERROR] PyFingerprint library not found. Fingerprint functionality disabled.")
    PyFingerprint = None
except Exception as e:
    # print(f"[ERROR] Failed to import PyFingerprint: {e}. Fingerprint functionality disabled.")
    PyFingerprint = None

DEBUG = True
BG_COLOR = "#F5F5F5"
BUTTON_FG = "#333333"
BUTTON_FONT = ("Segoe UI", 14)
INPUT_FONT = ("Segoe UI", 14)
BUTTON_WIDTH_BOTTOM = 180 # Adjusted for potentially 3 buttons
BUTTON_HEIGHT_BOTTOM = 130
PAD_X = 2 # Reduced padding
PAD_Y = 2 # Reduced padding
CONFIG_FILE = "mqtt_enroll_config.json" # For this enrollment station's MQTT config
HEALTHCHECK_INTERVAL_MS = 10000

FINGERPRINT_PORT = '/dev/ttyAMA4' # Specific to enrollment station's sensor
FINGERPRINT_BAUDRATE = 57600

# ROOM_TO_MAC removed - will be dynamic
GMT_PLUS_7 = timezone(timedelta(hours=7))

def get_mac_address(): # Gets MAC of THIS enrollment station
    mac = uuid.getnode()
    mac_str = ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))
    return mac_str

def load_image(path, size):
    try:
        full_path = os.path.join(script_dir, path)
        if not os.path.exists(full_path):
            # if DEBUG: print(f"[WARN] Image file not found: {full_path}")
            return None
        img = Image.open(full_path)
        if size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        return CTkImage(light_image=img, dark_image=img, size=img.size)
    except Exception as e:
        # if DEBUG: print(f"[DEBUG] Error loading image {path}: {e}")
        return None

def is_valid_date_format(date_str):
    if not date_str: return False
    try: datetime.strptime(date_str, "%Y-%m-%d"); return True
    except ValueError: return False

def is_valid_time_format(time_str):
    if not time_str: return False
    try: datetime.strptime(time_str, "%H:%M:%S"); return True
    except ValueError: return False

def parse_date(date_str):
    if not date_str: return None
    try: return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError: return None

def parse_time(time_str):
    if not time_str: return None
    try: return datetime.strptime(time_str, "%H:%M:%S").time()
    except ValueError: return None

class EnrollmentApp:
    def __init__(self, root):
        self.root = root
        self.enroll_mac = get_mac_address() # This enrollment station's MAC
        if DEBUG: print("[Enroll DEBUG] Enrollment Device MAC Address:", self.enroll_mac)

        try:
            database_enroll.initialize_database() # For this station's queuing & discovered rooms
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to initialize enrollment database: {e}\nApplication cannot continue.")
            root.quit(); return

        self.discovered_rooms_macs = database_enroll.get_all_discovered_devices() # Load persisted rooms on init
        if DEBUG: print(f"[Enroll DEBUG] Loaded discovered rooms on init: {self.discovered_rooms_macs}")

        # Variables to store current enrollment form data + biometric data
        self.current_room_name_selected = None # Stores the string from OptionMenu
        self.current_bio_id = None
        self.current_id_number = None
        self.current_person_name = None
        self.current_face_image_b64 = None
        self.current_face_template_b64 = None
        self.current_finger_template_b64 = None
        self.valid_from_date_str = None # Store as strings to repopulate UI
        self.valid_to_date_str = None
        self.valid_from_time_str = None
        self.valid_to_time_str = None
        # self.active_day_mask = None # Generated on send

        self.mqtt_manager = None # Instance of MQTTEnrollManager
        self.mqtt_config = {} # For this enrollment station's own MQTT settings
        self.config_path = os.path.join(script_dir, CONFIG_FILE)
        self.screen_history = []
        self.fingerprint_sensor = None # This enrollment station's fingerprint sensor

        # UI Element References (initialized in their respective build methods or show_enrollment_screen)
        self.connection_status_label = None; self.bg_label = None; self.loading_progress = None
        self.main_frame = None; self.room_name_option_menu = None; self.room_name_var = None
        self.bio_id_display_label = None; self.id_number_entry = None; self.person_name_entry = None
        self.from_date_entry = None; self.to_date_entry = None; self.from_time_entry = None; self.to_time_entry = None
        self.day_vars = []; self.face_status_label = None; self.finger_status_label = None
        self.config_btn_ref = None # For the settings button

        # Load Images
        self.connected_image = load_image("images/connected.jpg", (25, 25))
        self.disconnected_image = load_image("images/disconnected.jpg", (25, 25))
        self.bg_photo = load_image("images/background_enroll.jpeg", (1024, 600))
        img_w, img_h = BUTTON_WIDTH_BOTTOM - 20, BUTTON_HEIGHT_BOTTOM - 25
        self.face_img = load_image("images/face.png", (img_w, img_h))
        self.fingerprint_img = load_image("images/fingerprint.png", (img_w, img_h))
        self.send_img = load_image("images/send.png", (45,45)) # Smaller image for send button

        self.root.configure(fg_color=BG_COLOR)
        self.show_background() # Show background first
        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="Chưa kết nối", font=("Segoe UI", 8), text_color="red", compound="left")
        self.connection_status_label.place(relx=0.01, rely=0.97, anchor="sw")
        self.create_config_button() # Create settings button early

        self.initialize_fingerprint_sensor() # For this station

        # Load MQTT config for this enrollment station
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f: self.mqtt_config = json.load(f)
                if DEBUG: print("[Enroll DEBUG] MQTT config for enrollment station loaded:", self.mqtt_config)
                if not self.mqtt_config.get("broker") or not self.mqtt_config.get("port"):
                     raise ValueError("Config file missing broker or port for enrollment station.")
                self.initialize_mqtt() # Initializes MQTTEnrollManager
                self.show_enrollment_screen() # Go to main UI
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                if DEBUG: print(f"[Enroll ERROR] Reading/parsing {self.config_path}: {e}. Please reconfigure enrollment station.")
                if os.path.exists(self.config_path):
                    try: os.remove(self.config_path); self.mqtt_config = {}
                    except OSError as re: print(f"[Enroll ERROR] Removing invalid config: {re}")
                self.push_screen("mqtt_config", self.build_mqtt_config_screen) # Go to config UI
            except Exception as e: # Catch-all for other init errors
                if DEBUG: print(f"[Enroll ERROR] Unexpected error loading config/init: {e}")
                self.push_screen("mqtt_config", self.build_mqtt_config_screen)
        else: # No config file
            self.push_screen("mqtt_config", self.build_mqtt_config_screen)

        self.schedule_healthcheck() # For this enrollment station
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup)

    def generate_new_bio_id(self):
        self.current_bio_id = uuid.uuid4().hex[:10].upper() # Shorter Bio ID
        if DEBUG: print(f"[Enroll DEBUG] Generated new Bio ID: {self.current_bio_id}")
        if hasattr(self, 'bio_id_display_label') and self.bio_id_display_label and self.bio_id_display_label.winfo_exists():
            self.bio_id_display_label.configure(text=self.current_bio_id)

    def initialize_fingerprint_sensor(self): # For this enrollment station's sensor
        if PyFingerprint is None: return
        try:
            self.fingerprint_sensor = PyFingerprint(FINGERPRINT_PORT, FINGERPRINT_BAUDRATE, 0xFFFFFFFF, 0x00000000)
            if self.fingerprint_sensor.verifyPassword():
                if DEBUG: print("[Enroll INFO] Enrollment station fingerprint sensor verified.")
            else:
                if DEBUG: print("[Enroll ERROR] Failed to verify enrollment station sensor password.")
                self.fingerprint_sensor = None
        except Exception as e:
            if DEBUG: print(f"[Enroll ERROR] Failed to initialize enrollment station sensor: {e}")
            self.fingerprint_sensor = None

    def initialize_mqtt(self): # For THIS enrollment station
        if self.mqtt_config and not self.mqtt_manager: # Only if config exists and no manager yet
            if DEBUG: print("[Enroll DEBUG] Initializing MQTTEnrollManager for enrollment station...")
            self.mqtt_manager = MQTTEnrollManager(
                self.mqtt_config, # Its own MQTT settings
                self.enroll_mac,  # Its own MAC
                self.config_path, # Path to its own config file
                debug=DEBUG
            )
            self.mqtt_manager.on_connection_status_change = self.update_connection_status
            self.mqtt_manager.on_device_info_received = self.handle_discovered_device_info # Set callback
            if not self.mqtt_manager.initialize_connection(): # Attempt to connect itself
                 if DEBUG: print("[Enroll WARN] Initial MQTT connection attempt for enrollment station failed.")
    
    def handle_discovered_device_info(self, room_name, mac_address): # Called by MQTTEnrollManager
        if room_name and mac_address:
            if DEBUG: print(f"[EnrollApp] Received discovered device: Room '{room_name}', MAC '{mac_address}'")
            
            # Update internal dictionary (key by room_name for UI, but MAC should be unique in DB)
            current_mac_for_room = self.discovered_rooms_macs.get(room_name)
            needs_ui_update = (current_mac_for_room != mac_address) or (room_name not in self.discovered_rooms_macs)

            self.discovered_rooms_macs[room_name] = mac_address # Store/update
            
            if needs_ui_update and hasattr(self,'room_name_option_menu') and self.room_name_option_menu and self.room_name_option_menu.winfo_exists():
                new_room_options = sorted(list(self.discovered_rooms_macs.keys()))
                current_selection = self.room_name_var.get() # Get current UI selection
                
                self.room_name_option_menu.configure(values=new_room_options) # Update dropdown
                
                if current_selection in new_room_options: # Try to reselect if still valid
                    self.room_name_var.set(current_selection)
                elif new_room_options: # Else select first available
                    self.room_name_var.set(new_room_options[0])
                else: # No rooms
                    self.room_name_var.set("(Chưa có phòng)") # Placeholder if list becomes empty
                if DEBUG: print(f"[EnrollApp] Room list UI updated. Rooms: {new_room_options}")
        elif DEBUG:
            print(f"[EnrollApp WARN] Incomplete device info received for UI update: room='{room_name}', mac='{mac_address}'")

    def schedule_healthcheck(self): # For THIS enrollment station
        if self.mqtt_manager and self.mqtt_manager.connected:
            self.mqtt_manager.send_healthcheck()
        self.root.after(HEALTHCHECK_INTERVAL_MS, self.schedule_healthcheck)

    def update_connection_status(self, is_connected): # For THIS enrollment station's MQTT
        if not hasattr(self,'connection_status_label') or not self.connection_status_label or not self.connection_status_label.winfo_exists(): return
        image_to_show = self.connected_image if is_connected else self.disconnected_image
        text_to_show = "Đã kết nối" if is_connected else "Mất kết nối"
        color_to_show = "green" if is_connected else "red"
        self.connection_status_label.configure(image=image_to_show, text=text_to_show, text_color=color_to_show)

    def show_background(self):
        if hasattr(self,'bg_photo') and self.bg_photo:
            if hasattr(self,'bg_label') and self.bg_label and self.bg_label.winfo_exists(): self.bg_label.destroy()
            self.bg_label = ctk.CTkLabel(self.root, image=self.bg_photo, text=""); self.bg_label.place(x=0, y=0, relwidth=1, relheight=1); self.bg_label.lower()

    def clear_frames(self, keep_background=True): # Simplified
        if hasattr(self, 'main_frame') and self.main_frame and self.main_frame.winfo_exists():
            self.main_frame.destroy()
        self.main_frame = None
        # Reset UI element references that are children of main_frame
        self.room_name_option_menu = None; self.bio_id_display_label = None; self.id_number_entry = None
        self.person_name_entry = None; self.from_date_entry = None; self.to_date_entry = None
        self.from_time_entry = None; self.to_time_entry = None; self.day_vars = []
        self.face_status_label = None; self.finger_status_label = None

        if keep_background:
            self.show_background()
            if hasattr(self, 'connection_status_label') and self.connection_status_label and self.connection_status_label.winfo_exists():
                 self.connection_status_label.lift()
            self.create_config_button() # Ensure config button is always present

    def push_screen(self, screen_id, screen_func, *args):
        if self.screen_history and self.screen_history[-1][0] == screen_id: return # Avoid pushing same screen
        self.screen_history.append((screen_id, screen_func, args))
        self.clear_frames() # Clear before building new screen
        self.root.update_idletasks() # Ensure old frames are gone
        screen_func(*args)

    def go_back(self):
        if len(self.screen_history) > 1:
            self.screen_history.pop() # Remove current
            screen_id, screen_func, args = self.screen_history[-1] # Get previous
            self.clear_frames()
            self.root.update_idletasks()
            screen_func(*args)
        elif not (hasattr(self, 'main_frame') and self.main_frame and self.main_frame.winfo_exists()): # If history empty and no main frame
             self.show_enrollment_screen() # Default to main enrollment

    def return_to_enrollment_screen(self): # Public method to return to main screen
        face_enroll.stop_face_capture() # Ensure any active capture is stopped
        # Always rebuild main screen to reflect latest data
        self.screen_history = [] # Clear history, main screen is new base
        self.push_screen("enrollment_main", self.show_enrollment_screen)

    def create_config_button(self): # For enrollment station's own MQTT config
        if hasattr(self, 'config_btn_ref') and self.config_btn_ref and self.config_btn_ref.winfo_exists():
            self.config_btn_ref.lift()
            return
        self.config_btn_ref = ctk.CTkButton(self.root, text="Cài đặt", command=self.confirm_reconfigure, width=40, height=30, fg_color="#6c757d", hover_color="#5a6268", font=("Segoe UI", 11), text_color="white")
        self.config_btn_ref.place(relx=0.99, rely=0.01, anchor="ne")

    def confirm_reconfigure(self):
        if messagebox.askyesno("Xác nhận", "Cấu hình lại MQTT cho trạm đăng ký?", icon='warning', parent=self.root):
            self.reconfigure()

    def reconfigure(self): # For enrollment station's own MQTT config
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client(); self.mqtt_manager = None
            self.update_connection_status(False) # Show disconnected
        if os.path.exists(self.config_path):
            try: os.remove(self.config_path)
            except Exception as e: print(f"[Enroll ERROR] Removing config: {e}")
        self.mqtt_config = {} # Clear loaded config
        self.screen_history = [] # Reset screen history
        self.push_screen("mqtt_config", self.build_mqtt_config_screen) # Go to config UI

    def build_mqtt_config_screen(self): # UI for enrollment station's own MQTT
        self.main_frame = ctk.CTkFrame(self.root, fg_color=BG_COLOR); self.main_frame.place(relx=0.5, rely=0.35, anchor="center") # Adjusted rely
        ctk.CTkLabel(self.main_frame, text="CẤU HÌNH MQTT (TRẠM ĐĂNG KÝ)", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, columnspan=2, pady=(5, 10))
        
        sf = ctk.CTkFrame(self.main_frame, fg_color="transparent"); sf.grid(row=1, column=0, columnspan=2, pady=2)
        ctk.CTkLabel(sf, text="Broker:", font=INPUT_FONT).pack(side="left", padx=(0,3)); self.server_entry = ctk.CTkEntry(sf, width=180, font=INPUT_FONT, placeholder_text="IP hoặc domain"); self.server_entry.pack(side="left", padx=3)
        ctk.CTkLabel(sf, text="Port:", font=INPUT_FONT).pack(side="left", padx=(5,3)); self.port_entry = ctk.CTkEntry(sf, width=60, font=INPUT_FONT, placeholder_text="1883"); self.port_entry.pack(side="left", padx=3)
        
        hf = ctk.CTkFrame(self.main_frame, fg_color="transparent"); hf.grid(row=2, column=0, columnspan=2, pady=2)
        ctk.CTkLabel(hf, text="HTTP Port (token):", font=INPUT_FONT).pack(side="left", padx=(0,3)); self.http_port_entry = ctk.CTkEntry(hf, width=60, font=INPUT_FONT, placeholder_text="8080"); self.http_port_entry.pack(side="left", padx=3)
        self.http_port_entry.insert(0, self.mqtt_config.get("http_port", "8080"))

        # Input for enroll_station_room
        rf = ctk.CTkFrame(self.main_frame, fg_color="transparent"); rf.grid(row=3, column=0, columnspan=2, pady=2)
        ctk.CTkLabel(rf, text="Vị trí trạm ĐK:", font=INPUT_FONT).pack(side="left", padx=(0,3)); self.enroll_room_entry = ctk.CTkEntry(rf, width=180, font=INPUT_FONT, placeholder_text="VD: Quầy Lễ Tân"); self.enroll_room_entry.pack(side="left", padx=3)
        self.enroll_room_entry.insert(0, self.mqtt_config.get("enroll_station_room", "EnrollDesk1")) # Default value

        bf = ctk.CTkFrame(self.main_frame, fg_color="transparent"); bf.grid(row=4, column=0, columnspan=2, pady=(10,5)) # Adjusted row
        if len(self.screen_history) > 1 : ctk.CTkButton(bf, text="TRỞ VỀ", width=100, command=self.go_back).pack(side="left", padx=5)
        ctk.CTkButton(bf, text="LƯU & KẾT NỐI", width=150, command=self.validate_and_save_connect).pack(side="right", padx=5)

        if self.mqtt_config.get("broker"): self.server_entry.insert(0, self.mqtt_config.get("broker"))
        if self.mqtt_config.get("port"): self.port_entry.insert(0, str(self.mqtt_config.get("port")))

    def validate_and_save_connect(self): # For enrollment station's own MQTT
        broker = self.server_entry.get().strip(); port_str = self.port_entry.get().strip()
        http_port_str = self.http_port_entry.get().strip()
        enroll_station_location = self.enroll_room_entry.get().strip() # Get enroll station location

        if not all([broker, port_str, http_port_str, enroll_station_location]):
            messagebox.showerror("Lỗi", "Điền đủ thông tin Broker, Port, HTTP Port, và Vị trí trạm.", parent=self.root); return
        try:
            port = int(port_str); http_port = int(http_port_str)
            if not (0 < port < 65536 and 0 < http_port < 65536): raise ValueError("Port out of range")
        except ValueError: messagebox.showerror("Lỗi", "Port hoặc HTTP Port không hợp lệ.", parent=self.root); return
        
        new_config = {"broker": broker, "port": port, "http_port": http_port, "enroll_station_room": enroll_station_location}
        try:
            with open(self.config_path, "w") as f: json.dump(new_config, f, indent=2)
            self.mqtt_config = new_config # Update app's current config
        except Exception as e:
            messagebox.showerror("Lỗi Lưu Trữ", f"Không thể lưu cấu hình: {e}", parent=self.root); return
        
        self.show_connecting_screen()
        self.root.after(100, self._init_mqtt_after_save) # Triggers MQTT re-initialization

    def _init_mqtt_after_save(self): # For enrollment station's own MQTT
        if self.mqtt_manager: self.mqtt_manager.disconnect_client(); self.mqtt_manager = None
        self.initialize_mqtt() # Create and connect MQTTEnrollManager
        self.root.after(3000, self.return_to_enrollment_screen) # Go back to main UI

    def show_connecting_screen(self): # For enrollment station's own MQTT connection
        self.clear_frames()
        ctk.CTkLabel(self.root, text="Đang kết nối MQTT (Trạm Đăng Ký)...", font=("Segoe UI", 22)).place(relx=0.5, rely=0.45, anchor="center")
        prog = ctk.CTkProgressBar(self.root, width=350, height=12); prog.place(relx=0.5, rely=0.55, anchor="center"); prog.set(0); prog.start()

    def show_enrollment_screen(self):
        self.clear_frames() # This also calls show_background and create_config_button
        if not self.current_bio_id: self.generate_new_bio_id()

        self.main_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.main_frame.pack(pady=30, padx=30, fill="both", expand=True)
        self.main_frame.grid_rowconfigure(2, weight=1); self.main_frame.grid_columnconfigure(0, weight=1)

        top_input_frame = ctk.CTkFrame(self.main_frame, fg_color=BG_COLOR, corner_radius=6)
        top_input_frame.grid(row=0, column=0, padx=2, pady=(0,2), sticky="new")
        top_input_frame.grid_columnconfigure(1, weight=1) # Allow entry fields to expand

        ctk.CTkLabel(top_input_frame, text="Thông Tin Đăng Ký Sinh Trắc Học", font=("Segoe UI", 15, "bold")).grid(row=0, column=0, columnspan=2, pady=(3,5), sticky="w", padx=8)
        
        # Room Selection
        ctk.CTkLabel(top_input_frame, text="Phòng:", font=INPUT_FONT).grid(row=1, column=0, padx=(8,3), pady=1, sticky="w")
        room_options = sorted(list(self.discovered_rooms_macs.keys()))
        if not room_options : room_options = ["(Chưa có phòng)"]
        current_room_val = self.current_room_name_selected if self.current_room_name_selected in room_options else room_options[0]
        self.room_name_var = ctk.StringVar(value=current_room_val)
        self.room_name_option_menu = ctk.CTkOptionMenu(top_input_frame, variable=self.room_name_var, values=room_options, font=INPUT_FONT, height=26, dynamic_resizing=False, width=220)
        self.room_name_option_menu.grid(row=1, column=1, padx=(0,8), pady=1, sticky="ew")

        # Bio ID, CCCD, Ho Ten
        ctk.CTkLabel(top_input_frame, text="Bio ID:", font=INPUT_FONT).grid(row=2, column=0, padx=(8,3), pady=1, sticky="w")
        self.bio_id_display_label = ctk.CTkLabel(top_input_frame, text=self.current_bio_id or "N/A", font=INPUT_FONT, text_color="blue")
        self.bio_id_display_label.grid(row=2, column=1, padx=(0,8), pady=1, sticky="ew")

        ctk.CTkLabel(top_input_frame, text="Số CCCD:", font=INPUT_FONT).grid(row=3, column=0, padx=(8,3), pady=1, sticky="w")
        self.id_number_entry = ctk.CTkEntry(top_input_frame, placeholder_text="VD: 0123456789", font=INPUT_FONT, height=26)
        self.id_number_entry.grid(row=3, column=1, padx=(0,8), pady=1, sticky="ew")
        if self.current_id_number: self.id_number_entry.insert(0, self.current_id_number)

        ctk.CTkLabel(top_input_frame, text="Họ Tên:", font=INPUT_FONT).grid(row=4, column=0, padx=(8,3), pady=1, sticky="w")
        self.person_name_entry = ctk.CTkEntry(top_input_frame, placeholder_text="VD: Nguyễn Văn An", font=INPUT_FONT, height=26)
        self.person_name_entry.grid(row=4, column=1, padx=(0,8), pady=1, sticky="ew")
        if self.current_person_name: self.person_name_entry.insert(0, self.current_person_name)
        
        # Thoi Gian Hieu Luc
        ctk.CTkLabel(top_input_frame, text="Thời Gian Active", font=("Segoe UI", 14, "bold")).grid(row=5, column=0, columnspan=2, pady=(5,1), sticky="w", padx=8)
        date_frame = ctk.CTkFrame(top_input_frame, fg_color="transparent"); date_frame.grid(row=6, column=0, columnspan=2, padx=2, pady=0, sticky="ew")
        ctk.CTkLabel(date_frame, text="Từ Ngày:", font=INPUT_FONT, width=60).pack(side="left", padx=(5,0))
        self.from_date_entry = ctk.CTkEntry(date_frame, width=90, placeholder_text="YYYY-MM-DD", font=INPUT_FONT, height=26); self.from_date_entry.pack(side="left", padx=1)
        if self.valid_from_date_str: self.from_date_entry.insert(0, self.valid_from_date_str)
        ctk.CTkLabel(date_frame, text="Đến:", font=INPUT_FONT, width=25).pack(side="left", padx=(5,0))
        self.to_date_entry = ctk.CTkEntry(date_frame, width=90, placeholder_text="YYYY-MM-DD", font=INPUT_FONT, height=26); self.to_date_entry.pack(side="left", padx=1)
        if self.valid_to_date_str: self.to_date_entry.insert(0, self.valid_to_date_str)

        time_frame = ctk.CTkFrame(top_input_frame, fg_color="transparent"); time_frame.grid(row=7, column=0, columnspan=2, padx=2, pady=0, sticky="ew")
        ctk.CTkLabel(time_frame, text="Từ Giờ:", font=INPUT_FONT, width=60).pack(side="left", padx=(5,0))
        self.from_time_entry = ctk.CTkEntry(time_frame, width=70, placeholder_text="HH:MM:SS", font=INPUT_FONT, height=26); self.from_time_entry.pack(side="left", padx=1)
        if self.valid_from_time_str: self.from_time_entry.insert(0, self.valid_from_time_str)
        ctk.CTkLabel(time_frame, text="Đến:", font=INPUT_FONT, width=25).pack(side="left", padx=(5,0))
        self.to_time_entry = ctk.CTkEntry(time_frame, width=70, placeholder_text="HH:MM:SS", font=INPUT_FONT, height=26); self.to_time_entry.pack(side="left", padx=1)
        if self.valid_to_time_str: self.to_time_entry.insert(0, self.valid_to_time_str)
        
        # Ngay Active
        ctk.CTkLabel(top_input_frame, text="Lịch trong tuần", font=INPUT_FONT).grid(row=8, column=0, padx=(8,3), pady=(3,0), sticky="w")
        days_checkbox_frame = ctk.CTkFrame(top_input_frame, fg_color="transparent"); days_checkbox_frame.grid(row=9, column=0, columnspan=2, padx=(12,2), pady=(0,5), sticky="ew")
        day_names = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]; self.day_vars = []
        for day_name in day_names:
            var = ctk.BooleanVar(); self.day_vars.append(var)
            chk = ctk.CTkCheckBox(days_checkbox_frame, text=day_name, variable=var, font=("Segoe UI", 11), height=18, checkbox_height=16, checkbox_width=16); chk.pack(side="left", padx=4, pady=0)

        # Bottom Buttons (Face, Finger, Send)
        bottom_button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        bottom_button_frame.grid(row=2, column=0, padx=2, pady=2, sticky="nsew")
        bottom_button_frame.grid_columnconfigure((0,1,2), weight=1) # Equal weight for 3 columns
        bottom_button_frame.grid_rowconfigure(0, weight=1) # Allow buttons to expand vertically

        face_button = ctk.CTkButton(bottom_button_frame, image=self.face_img, text="KHUÔN MẶT", font=BUTTON_FONT, compound="top", width=BUTTON_WIDTH_BOTTOM, height=BUTTON_HEIGHT_BOTTOM, command=self.start_face_enrollment); face_button.grid(row=0, column=0, padx=1, pady=(0,1), sticky="nsew")
        self.face_status_label = ctk.CTkLabel(bottom_button_frame, text="Chưa đăng ký" if not self.current_face_template_b64 else "Đăng ký thành công", font=("Segoe UI",10), text_color="green" if self.current_face_template_b64 else "grey"); self.face_status_label.grid(row=1,column=0, pady=(0,1), sticky="n")
        
        finger_button = ctk.CTkButton(bottom_button_frame, image=self.fingerprint_img, text="VÂN TAY", font=BUTTON_FONT, compound="top", width=BUTTON_WIDTH_BOTTOM, height=BUTTON_HEIGHT_BOTTOM, command=self.start_fingerprint_enrollment); finger_button.grid(row=0, column=1, padx=1, pady=(0,1), sticky="nsew")
        self.finger_status_label = ctk.CTkLabel(bottom_button_frame, text="Chưa đăng ký" if not self.current_finger_template_b64 else "Đăng ký thành công", font=("Segoe UI",10), text_color="green" if self.current_finger_template_b64 else "grey"); self.finger_status_label.grid(row=1,column=1, pady=(0,1), sticky="n")
        
        send_button = ctk.CTkButton(bottom_button_frame, image=self.send_img, text="GỬI ĐĂNG KÝ", font=BUTTON_FONT, compound="top", width=BUTTON_WIDTH_BOTTOM - 50, height=BUTTON_HEIGHT_BOTTOM - 45, command=self.prepare_and_send_data, fg_color="#A5D6A7", hover_color="#81C784"); send_button.grid(row=0, column=2, rowspan=2, padx=1, pady=(0,1), sticky="nsew")

    def _save_current_form_data(self): # Helper to persist form data if navigating away
        if hasattr(self, 'room_name_var') and self.room_name_var: self.current_room_name_selected = self.room_name_var.get()
        if hasattr(self, 'id_number_entry') and self.id_number_entry: self.current_id_number = self.id_number_entry.get().strip()
        if hasattr(self, 'person_name_entry') and self.person_name_entry: self.current_person_name = self.person_name_entry.get().strip()
        if hasattr(self, 'from_date_entry') and self.from_date_entry: self.valid_from_date_str = self.from_date_entry.get().strip()
        if hasattr(self, 'to_date_entry') and self.to_date_entry: self.valid_to_date_str = self.to_date_entry.get().strip()
        if hasattr(self, 'from_time_entry') and self.from_time_entry: self.valid_from_time_str = self.from_time_entry.get().strip()
        if hasattr(self, 'to_time_entry') and self.to_time_entry: self.valid_to_time_str = self.to_time_entry.get().strip()
        # Day vars are handled by generate_active_days_mask

    def start_face_enrollment(self):
        self._save_current_form_data() # Save form data before calling external module
        face_enroll.capture_face_for_enrollment(parent=self.root, on_success_callback=self.handle_face_enroll_success, on_cancel_callback=self.handle_face_enroll_cancel)
    
    def handle_face_enroll_success(self, image_b64, template_b64):
        self.current_face_image_b64 = image_b64; self.current_face_template_b64 = template_b64
        self._schedule_return_to_enrollment() # Rebuild UI to show status

    def handle_face_enroll_cancel(self): self._schedule_return_to_enrollment()

    def start_fingerprint_enrollment(self):
        self._save_current_form_data() # Save form data
        if not self.fingerprint_sensor: messagebox.showerror("Lỗi", "Cảm biến vân tay trạm ĐK chưa sẵn sàng.", parent=self.root); return
        try:
            if not self.fingerprint_sensor.verifyPassword(): messagebox.showerror("Lỗi", "Lỗi xác thực cảm biến vân tay.", parent=self.root); return
        except Exception: messagebox.showerror("Lỗi", "Lỗi giao tiếp cảm biến.", parent=self.root); return
        fingerprint_enroll.enroll_fingerprint_template(parent=self.root, sensor=self.fingerprint_sensor, on_success_callback=self.handle_finger_enroll_success, on_failure_callback=self.handle_finger_enroll_failure, on_cancel_callback=self.handle_finger_enroll_cancel)

    def handle_finger_enroll_success(self, template_b64):
        self.current_finger_template_b64 = template_b64
        self._schedule_return_to_enrollment()

    def handle_finger_enroll_failure(self, reason=""):
        messagebox.showerror("Lỗi Vân Tay", f"Đăng ký vân tay thất bại: {reason}", parent=self.root)
        self._schedule_return_to_enrollment()
    
    def handle_finger_enroll_cancel(self): self._schedule_return_to_enrollment()

    def _schedule_return_to_enrollment(self): # Ensures UI update happens in main thread after callback
        self.root.after(10, self.show_enrollment_screen) # Rebuild the main enrollment screen

    def generate_active_days_mask(self):
        return "".join(['1' if var.get() else '0' for var in self.day_vars]) if hasattr(self,'day_vars') and len(self.day_vars)==7 else "0000000"

    def prepare_and_send_data(self):
        self._save_current_form_data() # Make sure to use the latest from UI
        selected_room = self.current_room_name_selected
        if not selected_room or selected_room == "(Chưa có phòng)":
            messagebox.showerror("Lỗi", "Vui lòng chọn một phòng từ danh sách.", parent=self.root); return
        
        target_mac = self.discovered_rooms_macs.get(selected_room)
        if not target_mac:
            messagebox.showerror("Lỗi", f"Không tìm thấy địa chỉ MAC cho phòng '{selected_room}'.", parent=self.root); return

        if not all([self.current_id_number, self.current_person_name, self.valid_from_date_str, self.valid_to_date_str, self.valid_from_time_str, self.valid_to_time_str]):
            messagebox.showerror("Lỗi", "Vui lòng điền đầy đủ thông tin cá nhân và thời gian hiệu lực.", parent=self.root); return
        if not (is_valid_date_format(self.valid_from_date_str) and is_valid_date_format(self.valid_to_date_str) and \
                is_valid_time_format(self.valid_from_time_str) and is_valid_time_format(self.valid_to_time_str)):
            messagebox.showerror("Lỗi", "Định dạng ngày hoặc giờ không hợp lệ. YYYY-MM-DD và HH:MM:SS.", parent=self.root); return
        
        from_date_obj = parse_date(self.valid_from_date_str); to_date_obj = parse_date(self.valid_to_date_str)
        if from_date_obj and to_date_obj and to_date_obj < from_date_obj:
            messagebox.showerror("Lỗi", "'Đến Ngày' không thể trước 'Từ Ngày'.", parent=self.root); return
        
        if not self.current_face_template_b64 and not self.current_finger_template_b64:
            messagebox.showwarning("Lỗi", "Cần đăng ký ít nhất một mẫu sinh trắc (Khuôn mặt hoặc Vân tay).", parent=self.root); return
        
        bio_datas = []
        if self.current_face_template_b64:
            if not self.current_face_image_b64: # Should be set if template is set
                 messagebox.showerror("Lỗi Dữ Liệu", "Thiếu ảnh khuôn mặt cho template đã đăng ký.", parent=self.root); return
            bio_datas.append({"BioType": "FACE", "Template": self.current_face_template_b64, "Img": self.current_face_image_b64})
        if self.current_finger_template_b64:
            bio_datas.append({"BioType": "FINGER", "Template": self.current_finger_template_b64})
        
        payload_object = {
            "bioId": self.current_bio_id, "idNumber": self.current_id_number, "personName": self.current_person_name,
            "cmdType": "PUSH_NEW_BIO", "bioDatas": bio_datas,
            "fromDate": self.valid_from_date_str, "toDate": self.valid_to_date_str,
            "fromTime": self.valid_from_time_str, "toTime": self.valid_to_time_str,
            "activeDays": self.generate_active_days_mask()
        }
        
        if self.mqtt_manager:
            publish_was_attempted_or_queued = self.mqtt_manager.publish_enrollment_payload([payload_object], target_mac)
            if self.mqtt_manager.connected and publish_was_attempted_or_queued: # Check if it was truly sent (not just queued due to disconnect)
                messagebox.showinfo("Thông báo", f"Dữ liệu cho '{self.current_person_name}' (Bio ID: {self.current_bio_id}) đã được gửi thành công đến phòng '{selected_room}'.", parent=self.root)
                self.reset_enrollment_state()
            elif not self.mqtt_manager.connected and publish_was_attempted_or_queued == False: # False from _publish_or_queue means it was queued
                 messagebox.showinfo("Thông báo", f"Dữ liệu cho '{self.current_person_name}' đã được xếp hàng đợi do MQTT chưa kết nối.", parent=self.root)
                 self.reset_enrollment_state()
            else: # Publish error even when connected (e.g., broker queue full or other unexpected error)
                 messagebox.showerror("Lỗi Gửi MQTT", "Không thể gửi dữ liệu. Dữ liệu có thể đã được xếp hàng đợi nếu MQTT không kết nối.", parent=self.root)
        else:
            messagebox.showerror("Lỗi MQTT", "MQTT Manager chưa được khởi tạo. Không thể gửi dữ liệu.", parent=self.root)

    def reset_enrollment_state(self):
        self.generate_new_bio_id() # Get new ID for next enrollment
        self.current_face_image_b64 = None; self.current_face_template_b64 = None
        self.current_finger_template_b64 = None
        self.current_id_number = None; self.current_person_name = None
        self.valid_from_date_str = None; self.valid_to_date_str = None
        self.valid_from_time_str = None; self.valid_to_time_str = None
        # Clear UI fields that are part of self.main_frame
        if hasattr(self,'id_number_entry') and self.id_number_entry and self.id_number_entry.winfo_exists(): self.id_number_entry.delete(0,'end')
        if hasattr(self,'person_name_entry') and self.person_name_entry and self.person_name_entry.winfo_exists(): self.person_name_entry.delete(0,'end')
        if hasattr(self,'from_date_entry') and self.from_date_entry and self.from_date_entry.winfo_exists(): self.from_date_entry.delete(0,'end')
        if hasattr(self,'to_date_entry') and self.to_date_entry and self.to_date_entry.winfo_exists(): self.to_date_entry.delete(0,'end')
        if hasattr(self,'from_time_entry') and self.from_time_entry and self.from_time_entry.winfo_exists(): self.from_time_entry.delete(0,'end')
        if hasattr(self,'to_time_entry') and self.to_time_entry and self.to_time_entry.winfo_exists(): self.to_time_entry.delete(0,'end')
        
        if hasattr(self,'day_vars') and self.day_vars: # Check if day_vars list was initialized
            for var in self.day_vars: var.set(False)
        
        # Update status labels
        if hasattr(self,'face_status_label') and self.face_status_label and self.face_status_label.winfo_exists(): self.face_status_label.configure(text="Chưa có",text_color="grey")
        if hasattr(self,'finger_status_label') and self.finger_status_label and self.finger_status_label.winfo_exists(): self.finger_status_label.configure(text="Chưa có",text_color="grey")
        
        # Don't reset room selection, user might enroll multiple people for same room

    def cleanup(self):
        face_enroll.stop_face_capture() # Ensure external processes are stopped
        if self.mqtt_manager: self.mqtt_manager.disconnect_client()
        self.root.destroy()

if __name__ == "__main__":
    ctk.set_appearance_mode("System"); ctk.set_default_color_theme("blue") # Or "green", "dark-blue"
    root = ctk.CTk()
    root.geometry("1024x600") # Adjust as needed
    root.title("Enrollment Device")
    root.resizable(False, False)
    app = EnrollmentApp(root)
    root.mainloop()