# main.py
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

from dotenv import load_dotenv
import json
import uuid
import customtkinter as ctk
from tkinter import messagebox, ttk # Added ttk for potential Treeview later
from PIL import Image
from customtkinter import CTkImage
from datetime import datetime, timezone, time as dt_time
import threading
import io
import base64
import time
# Import device interaction modules
import face
import id_card # Assuming exists and has open_id_card_recognition
import fingerprint # Uses updated fingerprint module
from door import Door
from mqtt import MQTTManager
import paho.mqtt.client as mqtt
import database # Use updated database module
# Import fingerprint sensor library
try:
    from pyfingerprint.pyfingerprint import PyFingerprint
except ImportError:
    print("[ERROR] PyFingerprint library not found. Fingerprint functionality disabled.")
    PyFingerprint = None
except Exception as e:
    print(f"[ERROR] Failed to import PyFingerprint: {e}. Fingerprint functionality disabled.")
    PyFingerprint = None


load_dotenv()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "password")

DEBUG = True
BG_COLOR = "#F5F5F5"
BUTTON_FG = "#333333"
BUTTON_FONT = ("Segoe UI", 24)
BUTTON_WIDTH = 250
BUTTON_HEIGHT = 250
PAD_X = 15
PAD_Y = 15
CONFIG_FILE = "mqtt_config.json"
FACE_RECOGNITION_TIMEOUT_MS = 5000
DOOR_OPEN_DURATION_MS = 10000
HEALTHCHECK_INTERVAL_MS = 10000
GUEST_CLEANUP_INTERVAL_MS = 3600000 # Currently unused placeholder interval

# Fingerprint Sensor Configuration (adjust as needed)
FINGERPRINT_PORT = '/dev/ttyAMA0'
FINGERPRINT_BAUDRATE = 57600

def get_mac_address():
    mac = uuid.getnode()
    mac_str = ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))
    return mac_str

def load_image(path, size):
    try:
        full_path = os.path.join(script_dir, path)
        if not os.path.exists(full_path):
            print(f"[WARN] Image file not found: {full_path}")
            return None
        img = Image.open(full_path)
        if size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        return CTkImage(light_image=img, dark_image=img, size=img.size)
    except Exception as e:
        if DEBUG: print(f"[DEBUG] Error loading image {path}: {e}")
        return None

def get_ctk_image_from_db(user_id, size=None): # user_id is bio_id
    base64_str = database.retrieve_bio_image_by_user_id(user_id)
    if base64_str:
        try:
            image_bytes = base64.b64decode(base64_str)
            image_stream = io.BytesIO(image_bytes)
            pil_image = Image.open(image_stream)
            if size:
                pil_image = pil_image.resize(size, Image.Resampling.LANCZOS)
            img_size = pil_image.size
            # Ensure size from parameter is used if valid
            if isinstance(size, tuple) and len(size) == 2:
                img_size=size
            ctk_img = CTkImage(light_image=pil_image, dark_image=pil_image, size=img_size)
            return ctk_img
        except base64.binascii.Error:
            print(f"Error decoding Base64 image for bio_id {user_id}.")
            return None
        except Exception as e:
            print(f"Error processing image for bio_id {user_id}: {e}")
            return None
    else:
        return None

class App:
    def __init__(self, root):
        self.root = root
        self.mac = get_mac_address()
        if DEBUG: print("[DEBUG] MAC Address:", self.mac)

        try:
            database.initialize_database()
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to initialize database: {e}\nApplication cannot continue.")
            root.quit()
            return

        self.token = None
        self.mqtt_manager = None
        self.mqtt_config = {}
        self.screen_history = []
        self.door_sensor = None
        self.fingerprint_sensor = None # Initialize fingerprint sensor attribute
        self.connection_status_label = None
        self.frame_mqtt = None
        self.frame_menu = None
        self.bg_label = None
        self.loading_progress = None
        self.face_info_label = None
        self.face_image_label = None
        self.name_label = None
        self.admin_user_entry = None
        self.admin_pass_entry = None
        self.server_entry = None
        self.port_entry = None
        self.mqtt_user_entry = None
        self.mqtt_pass_entry = None

        self.connected_image = load_image("images/connected.jpg", (40, 40))
        self.disconnected_image = load_image("images/disconnected.jpg", (40, 40))
        self.bg_photo = load_image("images/background.jpeg", (1024, 600))
        self.face_img = load_image("images/face.png", (BUTTON_WIDTH-50, BUTTON_HEIGHT-80))
        self.fingerprint_img = load_image("images/fingerprint.png", (BUTTON_WIDTH-50, BUTTON_HEIGHT-80))
        self.idcard_img = load_image("images/id_card.png", (BUTTON_WIDTH-50, BUTTON_HEIGHT-80))
        self.sync_img = load_image("images/sync.png", (BUTTON_WIDTH-50, BUTTON_HEIGHT-80)) # Assuming sync image exists

        self.root.configure(fg_color=BG_COLOR)
        self.show_background()
        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="")
        self.connection_status_label.place(relx=0.01, rely=0.93, anchor="sw")
        self.create_config_button()

        self.initialize_fingerprint_sensor() # Attempt to initialize sensor

        config_path = os.path.join(script_dir, CONFIG_FILE)
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    self.mqtt_config = json.load(f)
                if DEBUG: print("[DEBUG] MQTT config loaded:", self.mqtt_config)
                self.initialize_mqtt()
                self.show_main_menu()
            except json.JSONDecodeError:
                print(f"Error reading {CONFIG_FILE}. Please reconfigure.")
                if os.path.exists(config_path): os.remove(config_path)
                self.push_screen("admin_login", self.build_admin_login_screen)
            except Exception as e:
                print(f"An error occurred loading config: {e}")
                self.push_screen("admin_login", self.build_admin_login_screen)
        else:
            self.push_screen("admin_login", self.build_admin_login_screen)

        self.schedule_healthcheck()
        try:
            self.door_sensor = Door(sensor_pin=17, relay_pin=27, # Adjust pins if needed
                                      mqtt_publish_callback=self.door_state_changed,
                                      relay_active_high=False)
            print("[Door] Door sensor initialized.")
        except Exception as e:
            print(f"[ERROR] Error initializing Door Sensor: {e}. Door control may fail.")
            self.door_sensor = None

        self.root.protocol("WM_DELETE_WINDOW", self.cleanup) # Register cleanup on close

    def initialize_fingerprint_sensor(self):
        if PyFingerprint is None:
            print("[WARN] PyFingerprint library not loaded. Fingerprint sensor disabled.")
            return
        try:
            print(f"[INFO] Initializing fingerprint sensor on {FINGERPRINT_PORT} at {FINGERPRINT_BAUDRATE} baud...")
            self.fingerprint_sensor = PyFingerprint(FINGERPRINT_PORT, FINGERPRINT_BAUDRATE, 0xFFFFFFFF, 0x00000000)
            if self.fingerprint_sensor.verifyPassword():
                print("[INFO] Fingerprint sensor initialized and verified successfully.")
            else:
                print("[ERROR] Failed to verify fingerprint sensor password. Check sensor connection/config.")
                self.fingerprint_sensor = None # Disable sensor if verification fails
        except Exception as e:
            print(f"[ERROR] Failed to initialize fingerprint sensor: {e}")
            self.fingerprint_sensor = None

    def initialize_mqtt(self):
        if self.mqtt_config and not self.mqtt_manager:
            if DEBUG: print("[DEBUG] Initializing MQTT Manager...")
            # Pass the initialized fingerprint sensor to MQTTManager
            self.mqtt_manager = MQTTManager(self.mqtt_config, self.mac,
                                            fingerprint_sensor=self.fingerprint_sensor, # Pass sensor object
                                            debug=DEBUG)
            self.mqtt_manager.on_token_received = self.on_token_received
            self.mqtt_manager.on_connection_status_change = self.update_connection_status
            if not self.mqtt_manager.connect_and_register():
                print("[WARN] Initial MQTT connection/registration attempt failed.")
        elif self.mqtt_manager and self.fingerprint_sensor:
             # If MQTT Manager exists but sensor was initialized later, set it
             self.mqtt_manager.set_fingerprint_sensor(self.fingerprint_sensor)


    def schedule_healthcheck(self):
        if self.mqtt_manager:
            self.mqtt_manager.send_healthcheck()
        self.root.after(HEALTHCHECK_INTERVAL_MS, self.schedule_healthcheck)

    def update_connection_status(self, is_connected):
        if not self.connection_status_label or not self.connection_status_label.winfo_exists(): return
        image_to_show = self.connected_image if is_connected else self.disconnected_image
        text_color = "green" if is_connected else "red"
        status_text = "Đã kết nối" if is_connected else "Mất kết nối"
        if image_to_show:
            self.connection_status_label.configure(image=image_to_show, text=status_text, text_color=text_color, font=("Segoe UI", 10), compound="top")
        else:
            self.connection_status_label.configure(image=None, text=status_text, text_color=text_color, font=("Segoe UI", 12,"bold"))

    def on_token_received(self, token):
        if token:
            self.token = token
            if DEBUG: print("[DEBUG] Token received callback triggered.")
            self.root.after(500, self._connect_mqtt_with_token)
        else:
             print("[ERROR] Invalid token received (None). Triggering re-registration.")
             self.token = None
             self.mqtt_manager = None # Reset MQTT manager
             # Optionally show config screen again or attempt registration automatically
             self.root.after(1000, self.initialize_mqtt) # Try re-registering


    def _connect_mqtt_with_token(self):
        if self.mqtt_manager is not None:
            if DEBUG: print("[DEBUG] Attempting to connect with token...")
            # Ensure sensor is passed if manager was re-created
            if not self.mqtt_manager.fingerprint_sensor and self.fingerprint_sensor:
                 self.mqtt_manager.set_fingerprint_sensor(self.fingerprint_sensor)
            self.mqtt_manager.connect_with_token()
        else:
            print("[WARN] Cannot connect with token: MQTT Manager not initialized.")

    def door_state_changed(self, door_payload):
        if not self.mqtt_manager or not self.mqtt_manager.connected or not self.token:
            if DEBUG: print("[DEBUG] Door state changed, but MQTT not ready to publish.")
            return
        door_payload["MacAddress"] = self.mac
        door_payload["Token"] = self.token
        door_payload["DeviceTime"] = datetime.now(timezone.utc).isoformat(timespec='seconds') + "Z"
        try:
            json_payload = json.dumps(door_payload, separators=(",", ":"))
            if DEBUG: print("[DEBUG] Door state changed, publishing payload:", json_payload)
            if self.mqtt_manager.client:
                result, mid = self.mqtt_manager.client.publish("iot/devices/doorstatus", payload=json_payload, qos=1)
                if result != mqtt.MQTT_ERR_SUCCESS: print(f"[WARN] Failed to publish door status (Error code: {result})")
            else: print("[WARN] MQTT client not available within manager to publish door state.")
        except Exception as e: print(f"[ERROR] Error publishing door state: {e}")

    def trigger_door_open(self):
        if self.door_sensor:
            try:
                self.door_sensor.open_door()
                self.root.after(DOOR_OPEN_DURATION_MS, self.trigger_door_close)
            except Exception as e: print(f"[ERROR] Error opening door: {e}")
        elif DEBUG: print("[DEBUG] Door sensor not available, cannot open door.")

    def trigger_door_close(self):
        if self.door_sensor:
            try:
                self.door_sensor.close_door()
            except Exception as e: print(f"[ERROR] Error closing door: {e}")
        elif DEBUG: print("[DEBUG] Door sensor not available, cannot close door.")

    def schedule_guest_cleanup(self):
        pass

    def clean_guest_data(self):
        pass

    def show_background(self):
        if self.bg_photo:
            if self.bg_label and self.bg_label.winfo_exists(): self.bg_label.destroy()
            self.bg_label = ctk.CTkLabel(self.root, image=self.bg_photo, text="")
            self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            self.bg_label.lower()

    def clear_frames(self, keep_background=True, clear_face_elements=True):
        face.stop_face_recognition()
        # Destroy specific frames or widgets used by different screens
        widgets_to_destroy = []
        for widget in self.root.winfo_children():
            # Check if widget belongs to a screen frame or is a temporary UI element
            if widget == self.frame_mqtt or widget == self.frame_menu or \
               widget == self.loading_progress or \
               (hasattr(widget, '_owner_frame') and widget._owner_frame == 'fingerprint'): # Tag fingerprint frame if needed
                widgets_to_destroy.append(widget)
            elif clear_face_elements and \
                 (widget == self.face_info_label or \
                  widget == self.face_image_label or \
                  widget == self.name_label):
                 widgets_to_destroy.append(widget)
            # Avoid destroying persistent elements like bg_label, connection_status_label, config_button

        for widget in widgets_to_destroy:
             if widget and widget.winfo_exists():
                 widget.destroy()

        # Reset frame references
        self.frame_mqtt = None
        self.frame_menu = None
        self.loading_progress = None
        if clear_face_elements:
            self.face_info_label = None
            self.face_image_label = None
            self.name_label = None

        if keep_background:
            self.show_background()
            if self.connection_status_label and self.connection_status_label.winfo_exists():
                 self.connection_status_label.lift()
            self.create_config_button() # Ensure config button is always visible

    def push_screen(self, screen_id, screen_func, *args):
        # Allow passing args to screen build function
        if self.screen_history and self.screen_history[-1][0] == screen_id:
            # Avoid pushing the same screen consecutively
            return
        self.screen_history.append((screen_id, screen_func, args)) # Store args
        if DEBUG:
            history_ids = [sid for sid, _, _ in self.screen_history]
            print(f"[DEBUG] Pushing screen: {screen_id}. History: {history_ids}")
        self.clear_frames()
        screen_func(*args) # Call with stored args

    def go_back(self):
        if len(self.screen_history) > 1:
            self.screen_history.pop()
            screen_id, screen_func, args = self.screen_history[-1]
            if DEBUG:
                history_ids = [sid for sid, _, _ in self.screen_history]
                print(f"[DEBUG] Going back to screen: {screen_id}. History: {history_ids}")
            self.clear_frames()
            screen_func(*args) # Call with stored args
        else:
            if DEBUG: print("[DEBUG] No previous screen in history, going to main menu.")
            self.return_to_main_menu() # Use dedicated function


    def return_to_main_menu(self, event=None):
        if DEBUG: print("[DEBUG] Returning to main menu...")
        face.stop_face_recognition()
        self.screen_history = [] # Clear history before pushing main menu
        self.push_screen("main_menu", self.show_main_menu)

    def create_config_button(self):
        # Check if button already exists
        for widget in self.root.winfo_children():
            if isinstance(widget, ctk.CTkButton) and hasattr(widget, '_button_id') and widget._button_id == 'config_button':
                widget.lift()
                return
        # Create new button
        config_button = ctk.CTkButton(
            self.root, text="Cài Đặt", command=self.confirm_reconfigure, width=90, height=40,
            fg_color="#6c757d", hover_color="#5a6268", font=("Segoe UI", 16), text_color="white"
        )
        config_button._button_id = 'config_button' # Add identifier
        config_button.place(relx=0.99, rely=0.01, anchor="ne")

    def confirm_reconfigure(self):
        result = messagebox.askyesno("Xác nhận", "Bạn có chắc chắn muốn cấu hình lại thiết bị không?\nThao tác này sẽ xóa cấu hình MQTT hiện tại và yêu cầu đăng nhập lại.", icon='warning', parent=self.root)
        if result: self.reconfigure()

    def reconfigure(self):
        if DEBUG: print("[DEBUG] Reconfiguration requested.")
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client()
            self.mqtt_manager = None
            self.token = None
            self.update_connection_status(False)
            if DEBUG: print("[DEBUG] MQTT Manager disconnected for reconfiguration.")
        config_path = os.path.join(script_dir, CONFIG_FILE)
        if os.path.exists(config_path):
            try:
                os.remove(config_path)
                if DEBUG: print("[DEBUG] Removed configuration file:", CONFIG_FILE)
            except Exception as e: print(f"[ERROR] Error removing config file {config_path}: {e}")
        self.mqtt_config = {}
        self.screen_history = []
        self.push_screen("admin_login", self.build_admin_login_screen)

    def build_admin_login_screen(self):
        self.frame_mqtt = ctk.CTkFrame(self.root, fg_color=BG_COLOR, bg_color=BG_COLOR)
        self.frame_mqtt.place(relx=0.5, rely=0.25, anchor="center")
        title_label = ctk.CTkLabel(self.frame_mqtt, text="Xác thực tài khoản", font=("Segoe UI", 24, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=(10, 20))
        user_label = ctk.CTkLabel(self.frame_mqtt, text="Tài khoản", font=("Segoe UI", 16))
        user_label.grid(row=1, column=0, padx=(5, 10), pady=5, sticky="e")
        self.admin_user_entry = ctk.CTkEntry(self.frame_mqtt, width=250, height=35, placeholder_text="", font=("Segoe UI", 14))
        self.admin_user_entry.grid(row=1, column=1, padx=(10, 5), pady=5, sticky="w")
        pass_label = ctk.CTkLabel(self.frame_mqtt, text="Mật khẩu", font=("Segoe UI", 16))
        pass_label.grid(row=2, column=0, padx=(5, 10), pady=5, sticky="e")
        self.admin_pass_entry = ctk.CTkEntry(self.frame_mqtt, width=250, height=35, show="*", placeholder_text="", font=("Segoe UI", 14))
        self.admin_pass_entry.grid(row=2, column=1, padx=(10, 5), pady=5, sticky="w")
        login_button = ctk.CTkButton(self.frame_mqtt, text="Ðăng Nhập", width=150, height=40, font=("Segoe UI", 18, "bold"), fg_color="#4f918b", text_color="white", command=self.check_admin_login)
        login_button.grid(row=3, column=0, columnspan=2, pady=(10, 20))
        # self.admin_user_entry.focus()
        # self.admin_user_entry.bind("<Return>", lambda event: self.check_admin_login())
        # self.admin_pass_entry.bind("<Return>", lambda event: self.check_admin_login())


    def check_admin_login(self):
        username = self.admin_user_entry.get()
        password = self.admin_pass_entry.get()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            if DEBUG: print("[DEBUG] Admin authentication successful.")
            self.push_screen("mqtt_config", self.build_mqtt_config_screen)
        else:
            messagebox.showerror("Lỗi Đăng Nhập", "Tài khoản hoặc mật khẩu admin không đúng.", parent=self.root)
            self.admin_pass_entry.delete(0, "end")

    def build_mqtt_config_screen(self):
        self.frame_mqtt = ctk.CTkFrame(self.root, fg_color=BG_COLOR, bg_color=BG_COLOR)
        self.frame_mqtt.place(relx=0.5, rely=0.2, anchor="center")
        title_label = ctk.CTkLabel(self.frame_mqtt, text="CẤU HÌNH SERVER", font=("Segoe UI", 24, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=(5, 20))
   
        self.server_entry = ctk.CTkEntry(self.frame_mqtt, width=150, height=35, placeholder_text="ĐỊA CHỈ", font=("Segoe UI", 14))
        self.server_entry.grid(row=2, column=0, padx=5, pady=(0, 10), sticky="w")
        self.port_entry = ctk.CTkEntry(self.frame_mqtt, width=65, height=35, placeholder_text="CỔNG", font=("Segoe UI", 14))
        self.port_entry.grid(row=2, column=1, padx=5, pady=(0, 10), sticky="w")
        self.mqtt_user_entry = ctk.CTkEntry(self.frame_mqtt, width=150, height=35, placeholder_text="TÀI KHOẢN", font=("Segoe UI", 14))
        self.mqtt_user_entry.grid(row=4, column=0, padx=5, pady=(0, 10), sticky="w")
        self.mqtt_pass_entry = ctk.CTkEntry(self.frame_mqtt, width=150, height=35, show="*", placeholder_text="MẬT KHẨU", font=("Segoe UI", 14))
        self.mqtt_pass_entry.grid(row=4, column=1, padx=5, pady=(0, 20), sticky="w")
        button_frame = ctk.CTkFrame(self.frame_mqtt, fg_color="transparent")
        button_frame.grid(row=5, column=0, columnspan=2, pady=(10, 20))
        ctk.CTkButton(button_frame, text="TRỞ VỀ", width=120, height=40, font=("Segoe UI", 16),
                     fg_color="#6c757d", hover_color="#5a6268", text_color="white", command=self.go_back).pack(side="left", padx=10)
        ctk.CTkButton(button_frame, text="KẾT NỐI", width=150, height=40, font=("Segoe UI", 16, "bold"),
                     fg_color="#4f918b", hover_color="#427b75", text_color="white", command=self.validate_and_save_connect).pack(side="right", padx=10)
        #self.server_entry.focus()
        # self.server_entry.bind("<Return>", lambda event: self.validate_and_save_connect())
        # self.port_entry.bind("<Return>", lambda event: self.validate_and_save_connect())
        # self.mqtt_user_entry.bind("<Return>", lambda event: self.validate_and_save_connect())
        # self.mqtt_pass_entry.bind("<Return>", lambda event: self.validate_and_save_connect())


    def validate_and_save_connect(self):
        broker = self.server_entry.get().strip()
        port_str = self.port_entry.get().strip()
        mqtt_username = self.mqtt_user_entry.get().strip()
        mqtt_password = self.mqtt_pass_entry.get() # Get password even if empty
        if not all([broker, port_str, mqtt_username]): # Password can be empty? Check requirements
            messagebox.showerror("Lỗi", "Vui lòng điền Địa Chỉ Server, Cổng, và Tài Khoản Đăng Ký.", parent=self.root)
            return
        try:
            port = int(port_str)
            if not (0 < port < 65536): raise ValueError("Port out of range")
        except ValueError:
            messagebox.showerror("Lỗi", "Cổng MQTT không hợp lệ. Vui lòng nhập một số từ 1 đến 65535.", parent=self.root)
            return
        config = { "broker": broker, "port": port, "mqtt_username": mqtt_username, "mqtt_password": mqtt_password }
        config_path = os.path.join(script_dir, CONFIG_FILE)
        try:
            with open(config_path, "w") as f: json.dump(config, f, indent=4)
            self.mqtt_config = config
            if DEBUG: print("[DEBUG] Saved MQTT config:", self.mqtt_config)
        except Exception as e:
            print(f"Error saving MQTT config to {config_path}: {e}")
            messagebox.showerror("Lỗi Lưu Trữ", f"Không thể lưu cấu hình MQTT: {e}", parent=self.root)
            return
        self.show_connecting_screen()
        self.root.after(100, self._init_mqtt_after_save)

    def _init_mqtt_after_save(self):
        # Disconnect old manager if exists before initializing new one
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client()
            self.mqtt_manager = None
            self.token = None # Reset token on reconfig
        self.initialize_mqtt()
        self.root.after(2000, self.return_to_main_menu) # Allow time for connection attempt

    def show_connecting_screen(self):
        self.clear_frames()
        self.show_background()
        ctk.CTkLabel(self.root, text="Đang lưu cấu hình và kết nối MQTT...",
                       font=("Segoe UI", 22), text_color="#333").place(relx=0.5, rely=0.45, anchor="center")
        self.loading_progress = ctk.CTkProgressBar(self.root, width=400, height=15)
        self.loading_progress.place(relx=0.5, rely=0.55, anchor="center")
        self.loading_progress.set(0)
        self.loading_progress.start()

    def show_main_menu(self):
        self.clear_frames()
        self.show_background()
        self.frame_menu = ctk.CTkFrame(self.root, fg_color="transparent")
        # Đặt frame menu vào giữa màn hình
        self.frame_menu.place(relx=0.5, rely=0.5, anchor="center")

        # Options definition (3 tùy chọn)
        options = [
            (self.face_img, "KHUÔN MẶT", self.show_face_recognition_screen),
            (self.fingerprint_img, "VÂN TAY", self.start_fingerprint_scan),
            (self.idcard_img, "THẺ CCCD", self.start_id_card_scan),
        ]

        # --- THAY ĐỔI LAYOUT ---
        num_options = len(options)
        cols = 3 # Đặt số cột là 3 để hiển thị trên một hàng
        # rows = (num_options + cols - 1) // cols # Giờ chỉ có 1 hàng chính
        # --- KẾT THÚC THAY ĐỔI LAYOUT ---

        for idx, (img, label_text, cmd) in enumerate(options):
            if img is None:
                print(f"[WARN] Skipping main menu option '{label_text}' due to missing image.")
                continue

            # Tất cả các nút giờ sẽ ở hàng 0 (row_num = 0)
            row_num = 0
            col_num = idx # Cột sẽ là 0, 1, 2

            option_frame = ctk.CTkFrame(self.frame_menu, width=BUTTON_WIDTH, height=BUTTON_HEIGHT,
                                        fg_color=BG_COLOR, bg_color="transparent",
                                        corner_radius=15, border_width=2, border_color="#CCCCCC")
            # Đặt frame vào lưới tại hàng 0, cột tương ứng
            option_frame.grid(row=row_num, column=col_num, padx=PAD_X, pady=PAD_Y)
            option_frame.grid_propagate(False)

            button = ctk.CTkButton(
                option_frame, image=img, text=label_text, font=("Segoe UI", 20, "bold"),
                text_color=BUTTON_FG, compound="top", fg_color="transparent",
                hover_color="#EAEAEA", command=cmd
            )
            button.place(relx=0.5, rely=0.5, anchor="center", relwidth=1.0, relheight=1.0)


    def start_fingerprint_scan(self):
         if not self.fingerprint_sensor:
             messagebox.showerror("Lỗi Cảm Biến", "Cảm biến vân tay chưa được khởi tạo hoặc bị lỗi.", parent=self.root)
             return
         # Make sure sensor password verification still holds
         try:
              if not self.fingerprint_sensor.verifyPassword():
                   messagebox.showerror("Lỗi Cảm Biến", "Không thể xác thực với cảm biến vân tay.", parent=self.root)
                   return
         except Exception as e:
              messagebox.showerror("Lỗi Cảm Biến", f"Lỗi giao tiếp cảm biến vân tay: {e}", parent=self.root)
              return

         if DEBUG: print("[DEBUG] Starting fingerprint prompt...")
         # Push fingerprint screen without adding main menu again
         self.clear_frames() # Clear main menu buttons
         fingerprint.open_fingerprint_prompt(
             self.root,
             sensor=self.fingerprint_sensor, # Pass the initialized sensor object
             on_success_callback=self.handle_fingerprint_success,
             on_failure_callback=self.handle_fingerprint_failure
         )

    def start_id_card_scan(self):
         if DEBUG: print("[DEBUG] Starting ID card recognition...")
         # Không cần clear_frames() vì messagebox sẽ hiện trên màn hình hiện tại
         # self.clear_frames()

         # --- GỌI HÀM VỚI CALLBACK ---
         id_card.open_id_card_recognition(
                on_close_callback=self.return_to_main_menu # Truyền hàm quay lại menu
         )


    def handle_fingerprint_success(self, bio_id):
        """Callback for successful fingerprint recognition AND validity check."""
        if DEBUG: print(f"[DEBUG] MainApp: Fingerprint Success Callback for bioId: {bio_id}")
        # Retrieve person's name for MQTT message (optional, could add function to database.py)
        person_name = bio_id # Default to bio_id if name retrieval isn't implemented/needed
        # Example: Fetch name if function exists
        # user_info = database.get_user_info_by_bio_id(bio_id)
        # if user_info and user_info['person_name']: person_name = user_info['person_name']

        if self.mqtt_manager:
             self.mqtt_manager.send_recognition_success(bio_id, person_name)
        self.trigger_door_open()
        # UI update is handled by fingerprint.py, just return to main menu after delay
        self.root.after(100, self.return_to_main_menu) # Return quickly after door triggers

    def handle_fingerprint_failure(self):
        """Callback for fingerprint failure (no match, invalid time, error, timeout)."""
        if DEBUG: print("[DEBUG] MainApp: Fingerprint Failure Callback.")
        # UI update (failure message) is handled by fingerprint.py
        # Just return to main menu
        self.root.after(100, self.return_to_main_menu) # Return quickly

    def request_manual_sync(self):
        if self.mqtt_manager and self.mqtt_manager.connected:
             print("[INFO] Manual sync requested.")
             self.mqtt_manager.send_device_sync()
             messagebox.showinfo("Đồng Bộ", "Đã gửi yêu cầu đồng bộ dữ liệu đến server.", parent=self.root)
        else:
             messagebox.showwarning("Lỗi MQTT", "Chưa kết nối MQTT. Không thể gửi yêu cầu đồng bộ.", parent=self.root)

    def show_bio_records_screen(self):
        self.push_screen("bio_records", self._build_bio_records_ui)

    def _build_bio_records_ui(self):
        # Function to handle deletion confirmation and action
        def confirm_delete(bio_id_to_delete):
             if messagebox.askyesno("Xác nhận Xóa", f"Bạn có chắc chắn muốn xóa người dùng có BioID: {bio_id_to_delete}?\nDữ liệu sẽ bị xóa khỏi thiết bị và cảm biến.", icon='warning', parent=self.root):
                  delete_record(bio_id_to_delete)

        # Function to perform the deletion via MQTT command
        def delete_record(bio_id_to_delete):
             if self.mqtt_manager and self.mqtt_manager.connected:
                  # Simulate sending a PUSH_DELETE_BIO command locally for immediate effect?
                  # Or just rely on server sync? For now, let's assume we need to trigger delete locally too.

                  # 1. Get Position from DB
                  position = database.get_finger_position_by_bio_id(bio_id_to_delete)
                  # 2. Delete from Sensor
                  sensor_deleted = False
                  if position is not None and self.fingerprint_sensor:
                      try:
                          if self.fingerprint_sensor.verifyPassword():
                              if self.fingerprint_sensor.deleteTemplate(position):
                                   print(f"[INFO] Deleted fingerprint from sensor position {position} for bioId {bio_id_to_delete}.")
                                   sensor_deleted = True
                              else: print(f"[ERROR] Failed to delete fingerprint from sensor position {position}.")
                          else: print("[ERROR] Sensor password verify failed for delete.")
                      except Exception as e: print(f"[ERROR] Exception deleting from sensor: {e}")
                  # 3. Delete from DB
                  db_deleted = database.delete_embedding_by_bio_id(bio_id_to_delete)

                  if db_deleted:
                      print(f"[INFO] Record for bioId {bio_id_to_delete} deleted from DB.")
                      # Optionally send a PUSH_DELETE message to server if needed,
                      # or assume server handles sync based on device state.
                      # Refresh the UI
                      self._build_bio_records_ui() # Rebuild the screen
                      messagebox.showinfo("Thành Công", f"Đã xóa người dùng {bio_id_to_delete}.", parent=self.root)
                  else:
                       messagebox.showerror("Lỗi", f"Không thể xóa người dùng {bio_id_to_delete} khỏi database.", parent=self.root)

             else:
                  messagebox.showerror("Lỗi MQTT", "Không kết nối MQTT. Không thể xóa.", parent=self.root)


        self.clear_frames()
        self.show_background()

        # Main frame for the records display
        main_records_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        main_records_frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.9, relheight=0.8)

        ctk.CTkLabel(main_records_frame, text="Dữ liệu Sinh trắc học Lưu trữ", font=("Segoe UI", 20, "bold")).pack(pady=(5, 10))

        # Frame for Treeview and Scrollbar
        tree_frame = ctk.CTkFrame(main_records_frame)
        tree_frame.pack(expand=True, fill="both", padx=10, pady=5)

        # Treeview Scrollbar
        tree_scroll = ctk.CTkScrollbar(tree_frame)
        tree_scroll.pack(side="right", fill="y")

        # Treeview Widget
        cols = ("Name", "BioID", "CCCD", "FromDate", "ToDate", "FromTime", "ToTime", "Days", "FingerPos", "MAC")
        self.records_tree = ttk.Treeview(tree_frame, columns=cols, show='headings', yscrollcommand=tree_scroll.set, height=15)

        # Define headings
        self.records_tree.heading("Name", text="Tên")
        self.records_tree.heading("BioID", text="BioID")
        self.records_tree.heading("CCCD", text="CCCD/ID")
        self.records_tree.heading("FromDate", text="Từ Ngày")
        self.records_tree.heading("ToDate", text="Đến Ngày")
        self.records_tree.heading("FromTime", text="Từ Giờ")
        self.records_tree.heading("ToTime", text="Đến Giờ")
        self.records_tree.heading("Days", text="Ngày Active")
        self.records_tree.heading("FingerPos", text="Vị Trí Vân Tay")
        self.records_tree.heading("MAC", text="MAC Address")


        # Configure column widths (adjust as needed)
        self.records_tree.column("Name", width=120, anchor="w")
        self.records_tree.column("BioID", width=100, anchor="w")
        self.records_tree.column("CCCD", width=100, anchor="w")
        self.records_tree.column("FromDate", width=80, anchor="center")
        self.records_tree.column("ToDate", width=80, anchor="center")
        self.records_tree.column("FromTime", width=60, anchor="center")
        self.records_tree.column("ToTime", width=60, anchor="center")
        self.records_tree.column("Days", width=80, anchor="center")
        self.records_tree.column("FingerPos", width=80, anchor="center")
        self.records_tree.column("MAC", width=110, anchor="w")


        self.records_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.configure(command=self.records_tree.yview)

        records = database.retrieve_all_bio_records_for_display(mac_address=self.mac)
        if not records:
             ctk.CTkLabel(main_records_frame, text="Không có dữ liệu sinh trắc học nào được lưu trữ cục bộ.",
                           font=("Segoe UI", 18)).pack(pady=20)

        else:
            for i, rec in enumerate(records):
                try:
                    # Unpack all 14 elements returned by the updated DB function
                    rec_id, bio_id, id_number, from_date, to_date, from_time, to_time, \
                    active_days, bio_type, template_key, img_b64, mac_addr, person_name, finger_pos = rec

                    # Prepare display values, handling None/empty strings
                    display_name = person_name if person_name else (id_number if id_number else bio_id)
                    display_cccd = id_number if id_number else "N/A"
                    display_fdate = from_date if from_date else "-"
                    display_tdate = to_date if to_date else "-"
                    display_ftime = from_time if from_time else "-"
                    display_ttime = to_time if to_time else "-"
                    display_days = active_days if active_days else "-------"
                    display_fpos = str(finger_pos) if finger_pos is not None else "-"
                    display_mac = mac_addr if mac_addr else "-"

                    # Insert into Treeview
                    self.records_tree.insert("", "end", iid=i, values=(
                        display_name, bio_id, display_cccd, display_fdate, display_tdate,
                        display_ftime, display_ttime, display_days, display_fpos, display_mac
                    ), tags=(bio_id,)) # Use bio_id as a tag for potential actions

                except ValueError as e: print(f"[ERROR] Could not unpack record: {rec} - Error: {e}")
                except Exception as e: print(f"[ERROR] Error displaying record {rec}: {e}")

        # Add Buttons below Treeview
        button_frame = ctk.CTkFrame(main_records_frame, fg_color="transparent")
        button_frame.pack(pady=10)

        # Delete Button (example - requires selecting a row)
        def get_selected_bio_id():
            selected_item = self.records_tree.focus() # Get selected item IID
            if selected_item:
                item_tags = self.records_tree.item(selected_item, "tags")
                if item_tags:
                    return item_tags[0] # Return the bio_id tag
            return None

        delete_button = ctk.CTkButton(button_frame, text="Xóa Người Dùng Đã Chọn", fg_color="red", hover_color="#CC0000",
                                      command=lambda: confirm_delete(get_selected_bio_id()) if get_selected_bio_id() else messagebox.showwarning("Chưa Chọn", "Vui lòng chọn một người dùng từ danh sách để xóa.", parent=self.root))
        delete_button.pack(side="right", padx=10)


        back_button = ctk.CTkButton(button_frame, text="Quay Lại Menu Chính", command=self.return_to_main_menu)
        back_button.pack(side="left", padx=10)


    def show_face_recognition_screen(self):
        self.push_screen("face_recognition", self._build_face_recognition_ui)

    def _build_face_recognition_ui(self):
        self.clear_frames(clear_face_elements=False)
        self.show_background()

        if DEBUG: print(f"[DEBUG] Loading active FACE vectors from DB for MAC: {self.mac}")
        face.face_db.clear()
        embedding_records = database.get_active_embeddings(self.mac)
        loaded_count = 0
        for record in embedding_records:
            try:
                key = f"{record['person_name']}_{record['user_id']}" # user_id is bio_id
                face.face_db[key] = record['embedding_data']
                loaded_count += 1
            except Exception as e:
                print(f"[ERROR] Failed to load record {record.get('user_id','N/A')} into face_db: {e}")

        if loaded_count == 0:
            messagebox.showinfo("Không Tìm Thấy Khuôn Mặt",
                                 f"Không tìm thấy dữ liệu khuôn mặt nào đang hoạt động trong database cho thời điểm hiện tại.",
                                 parent=self.root)
            self.root.after(100, self.return_to_main_menu)
            return

        if not self.face_info_label or not self.face_info_label.winfo_exists():
            self.face_info_label = ctk.CTkLabel(self.root, text="", font=("Segoe UI", 20), text_color="#333", wraplength=900)
            self.face_info_label.place(relx=0.5, rely=0.02, anchor="n")
        self.face_info_label.configure(text="") # Clear previous info

        if not self.face_image_label or not self.face_image_label.winfo_exists():
            self.face_image_label = ctk.CTkLabel(self.root, text="", fg_color="black", width=640, height=480)
            self.face_image_label.place(relx=0.5, rely=0.5, anchor="center")
        self.face_image_label.configure(text="Đang khởi tạo Camera...", image=None, font=("Segoe UI", 18, "bold"), text_color="white")

        if not self.name_label or not self.name_label.winfo_exists():
            self.name_label = ctk.CTkLabel(self.root, text="", font=("Segoe UI", 26, "bold"), text_color="#0044cc", wraplength=900)
            self.name_label.place(relx=0.5, rely=0.95, anchor="s")
        self.name_label.configure(text="Vui lòng nhìn thẳng vào Camera")

        if DEBUG: print("[DEBUG] Starting face recognition thread...")
        if self.face_image_label and self.face_image_label.winfo_exists():
            threading.Thread(
                target=face.open_face_recognition,
                args=(self.handle_recognition_success, self.handle_recognition_failure, self.face_image_label),
                daemon=True
            ).start()
        else:
            print("[ERROR] Cannot start face recognition: UI Label not ready.")
            messagebox.showerror("Lỗi UI", "Không thể khởi tạo khu vực hiển thị camera.", parent=self.root)
            self.root.after(100, self.return_to_main_menu)

    def handle_recognition_success(self, name_key, score, frame):
        if DEBUG: print(f"[DEBUG] MainApp: Face Recognition Success: Key={name_key}, Score={score:.2f}")
        parts = name_key.split('_')
        display_name = parts[0] if parts else name_key
        bio_id = parts[1] if len(parts) > 1 else None # This is the bio_id

        if bio_id is None:
            print(f"[ERROR] Could not extract user_id (bio_id) from key: {name_key}")
            if self.face_info_label and self.face_info_label.winfo_exists():
                self.face_info_label.configure(text="Nhận diện thành công (ID lỗi)!", text_color="orange")
            if self.name_label and self.name_label.winfo_exists():
                self.name_label.configure(text="Xin chào!", text_color="green")
        else:
            if self.face_info_label and self.face_info_label.winfo_exists():
                self.face_info_label.configure(text="Nhận diện thành công!", text_color="green")
            if self.name_label and self.name_label.winfo_exists():
                 self.name_label.configure(text=f"Xin chào, {display_name}!", text_color="green")

            if self.face_image_label and self.face_image_label.winfo_exists():
                 profile_pic_size = (200, 200) # Example size
                 ctk_img = get_ctk_image_from_db(bio_id, size=profile_pic_size)
                 if ctk_img:
                     self.face_image_label.configure(image=ctk_img, text="")
                     self.face_image_label.image = ctk_img
                 else:
                     print(f"[WARN] Could not load stored image for user_id: {bio_id}")
                     self.face_image_label.configure(image=None, text=f"Không tìm thấy ảnh\n{display_name}",
                                                    font=("Segoe UI", 16), text_color="white")

            if self.mqtt_manager:
                 # Pass bio_id and display_name to MQTT
                 self.mqtt_manager.send_recognition_success(bio_id, display_name)

        self.trigger_door_open()
        self.root.after(FACE_RECOGNITION_TIMEOUT_MS, self.return_to_main_menu)

    def handle_recognition_failure(self, reason="Unknown"):
        if DEBUG: print(f"[DEBUG] MainApp: Face Recognition Failure. Reason: {reason}")
        if self.name_label and self.name_label.winfo_exists():
            self.name_label.configure(text="Không thể nhận diện", text_color="red")
        # Optionally display the reason in face_info_label
        # if self.face_info_label and self.face_info_label.winfo_exists():
        #     self.face_info_label.configure(text=f"Lỗi: {reason}", text_color="red")

        self.root.after(2000, self.return_to_main_menu) # Return after showing message


    def cleanup(self):
        """Cleanup resources on application exit."""
        print("[INFO] Cleaning up resources...")
        face.stop_face_recognition()
        if self.mqtt_manager:
             print("[INFO] Disconnecting MQTT client...")
             self.mqtt_manager.disconnect_client()
        if self.door_sensor:
             print("[INFO] Cleaning up door sensor GPIO...")
             self.door_sensor.cleanup()
        # Add fingerprint sensor cleanup if needed (depends on library)
        # if self.fingerprint_sensor:
        #     try:
        #         # sensor specific cleanup if available
        #         print("[INFO] Cleaning up fingerprint sensor...")
        #     except Exception as e:
        #         print(f"[WARN] Error during fingerprint sensor cleanup: {e}")
        print("[INFO] Exiting application.")
        self.root.destroy()


if __name__ == "__main__":
    root = ctk.CTk()
    root.geometry("1024x600")
    root.title("Access Control System")
    # root.attributes('-fullscreen', True) # Uncomment for fullscreen
    root.resizable(False, False)
    app = App(root)
    root.mainloop()
