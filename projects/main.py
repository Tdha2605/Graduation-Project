import os
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
except RuntimeError:
    GPIO_AVAILABLE = False

from dotenv import load_dotenv
import json
import uuid
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image
from customtkinter import CTkImage
from datetime import datetime, timezone, timedelta, time as dt_time
import threading
import io
import base64
import time

import face
import id_card
import fingerprint
from door import Door
from mqtt import MQTTManager
import paho.mqtt.client as mqtt_paho
import database

try:
    from pyfingerprint.pyfingerprint import PyFingerprint
except ImportError:
    PyFingerprint = None
except Exception:
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

FINGERPRINT_PORT = '/dev/ttyAMA0'
FINGERPRINT_BAUDRATE = 57600

DOOR_SENSOR_PIN = 17
DOOR_RELAY_PIN = 27
SOS_BUTTON_PIN = 5
OPEN_BUTTON_PIN = 13
BUZZER_PIN = 26
BUTTON_DEBOUNCE_TIME = 300

GMT_PLUS_7 = timezone(timedelta(hours=7))

def get_mac_address():
    mac = uuid.getnode()
    mac_str = ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))
    return mac_str

def load_image(path, size):
    try:
        full_path = os.path.join(script_dir, path)
        if not os.path.exists(full_path):
            if DEBUG: print(f"[MAIN WARN] Image not found: {full_path}")
            return None
        img = Image.open(full_path)
        if size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        return CTkImage(light_image=img, dark_image=img, size=img.size)
    except Exception as e:
        if DEBUG: print(f"[MAIN ERROR] Loading image {path}: {e}")
        return None

def get_ctk_image_from_db(user_id, size=None):
    base64_str = database.retrieve_bio_image_by_user_id(user_id)
    if base64_str:
        try:
            image_bytes = base64.b64decode(base64_str)
            image_stream = io.BytesIO(image_bytes)
            pil_image = Image.open(image_stream)
            if size:
                pil_image = pil_image.resize(size, Image.Resampling.LANCZOS)
            img_size = pil_image.size
            if isinstance(size, tuple) and len(size) == 2:
                img_size=size
            ctk_img = CTkImage(light_image=pil_image, dark_image=pil_image, size=img_size)
            return ctk_img
        except base64.binascii.Error:
            if DEBUG: print(f"[MAIN ERROR] Base64 decode error for bio_id {user_id}.")
            return None
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Image processing error for bio_id {user_id}: {e}")
            return None
    return None

class App:
    def __init__(self, root):
        self.root = root
        self.mac = get_mac_address()
        if DEBUG: print("[MAIN DEBUG] MAC Address:", self.mac)

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
        self.fingerprint_sensor = None
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
        self.mqtt_port_entry = None
        self.room_entry = None

        self.last_sos_button_state = GPIO.HIGH if GPIO_AVAILABLE else None
        self.last_open_button_state = GPIO.HIGH if GPIO_AVAILABLE else None
        self.open_button_press_time = None
        self.open_door_timer = None

        self.connected_image = load_image("images/connected.jpg", (40, 40))
        self.disconnected_image = load_image("images/disconnected.jpg", (40, 40))
        self.bg_photo = load_image("images/background.jpeg", (1024, 600))
        self.face_img = load_image("images/face.png", (BUTTON_WIDTH-50, BUTTON_HEIGHT-80))
        self.fingerprint_img = load_image("images/fingerprint.png", (BUTTON_WIDTH-50, BUTTON_HEIGHT-80))
        self.idcard_img = load_image("images/id_card.png", (BUTTON_WIDTH-50, BUTTON_HEIGHT-80))
        self.sync_img = load_image("images/sync.png", (30, 30))

        self.root.configure(fg_color=BG_COLOR)
        self.show_background()
        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="")
        self.connection_status_label.place(relx=0.01, rely=0.95, anchor="sw")
        self.create_config_button()
        self.sync_button = ctk.CTkButton(self.root, image=self.sync_img, text="", width=35, height=35, fg_color="transparent", hover_color="#E0E0E0", command=self.request_manual_sync)
        self.sync_button.place(relx=0.05, rely=0.07, anchor="se")
        
        self.initialize_fingerprint_sensor()

        if GPIO_AVAILABLE:
            self.setup_gpio_components()

        config_path = os.path.join(script_dir, CONFIG_FILE)
        mqtt_init_successful = False
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    self.mqtt_config = json.load(f)
                if DEBUG: print("[MAIN DEBUG] MQTT config loaded:", self.mqtt_config)
                self.token = self.mqtt_config.get("token")
                self.initialize_mqtt()
                if self.mqtt_manager:
                    mqtt_init_successful = True
            except json.JSONDecodeError:
                if DEBUG: print(f"[MAIN ERROR] Error reading {CONFIG_FILE}. Please reconfigure.")
                if os.path.exists(config_path): os.remove(config_path)
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] An error occurred loading config: {e}")
        
        self.door_sensor_handler = None
        if GPIO_AVAILABLE:
            try:
                self.door_sensor_handler = Door(
                    sensor_pin=DOOR_SENSOR_PIN,
                    relay_pin=DOOR_RELAY_PIN,
                    relay_active_high=False,
                    mqtt_publish_callback=self.door_state_changed_mqtt
                )
                if DEBUG: print("[MAIN INFO] Door handler initialized.")
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Error initializing Door Handler: {e}.")
                self.door_sensor_handler = None
        else:
            if DEBUG: print("[MAIN WARN] GPIO not available, Door handler not initialized.")

        if mqtt_init_successful:
            self.show_main_menu()
        else:
            self.push_screen("admin_login", self.build_admin_login_screen)

        self.schedule_healthcheck()
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup)

    def setup_gpio_components(self):
        if not GPIO_AVAILABLE: return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(SOS_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(SOS_BUTTON_PIN, GPIO.BOTH, callback=self._sos_button_callback, bouncetime=BUTTON_DEBOUNCE_TIME)
            self.last_sos_button_state = GPIO.input(SOS_BUTTON_PIN)
            GPIO.setup(OPEN_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(OPEN_BUTTON_PIN, GPIO.BOTH, callback=self._open_button_callback, bouncetime=BUTTON_DEBOUNCE_TIME)
            self.last_open_button_state = GPIO.input(OPEN_BUTTON_PIN)
            GPIO.setup(BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
            if DEBUG: print("[MAIN INFO] GPIO components initialized.")
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Failed to setup GPIO: {e}")

    def initialize_fingerprint_sensor(self):
        if PyFingerprint is None:
            if DEBUG: print("[MAIN WARN] PyFingerprint not loaded. Sensor disabled.")
            return
        try:
            if DEBUG: print(f"[MAIN INFO] Initializing sensor on {FINGERPRINT_PORT}...")
            self.fingerprint_sensor = PyFingerprint(FINGERPRINT_PORT, FINGERPRINT_BAUDRATE, 0xFFFFFFFF, 0x00000000)
            if self.fingerprint_sensor.verifyPassword():
                if DEBUG: print("[MAIN INFO] Fingerprint sensor verified.")
                if self.mqtt_manager:
                    self.mqtt_manager.set_fingerprint_sensor(self.fingerprint_sensor)
            else:
                if DEBUG: print("[MAIN ERROR] Failed to verify sensor password.")
                self.fingerprint_sensor = None
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Failed to initialize sensor: {e}")
            self.fingerprint_sensor = None

    def initialize_mqtt(self):
        if self.mqtt_config and not self.mqtt_manager:
            if DEBUG: print("[MAIN DEBUG] Initializing MQTT Manager:", self.mqtt_config)
            self.mqtt_manager = MQTTManager(self.mqtt_config, self.mac, fingerprint_sensor=self.fingerprint_sensor, debug=DEBUG)
            self.mqtt_manager.on_token_received = self.on_token_received_from_mqtt
            self.mqtt_manager.on_connection_status_change = self.update_connection_status
            if not self.mqtt_manager.connect_and_register():
                if DEBUG: print("[MAIN WARN] Initial MQTT connection/registration attempt failed.")
        elif self.mqtt_manager and self.fingerprint_sensor and not self.mqtt_manager.fingerprint_sensor:
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

    def on_token_received_from_mqtt(self, new_username, new_token):
        config_changed = False
        if new_token and new_username:
            if self.token != new_token or self.mqtt_config.get("mqtt_username") != new_username:
                self.token = new_token
                self.mqtt_config["token"] = new_token
                self.mqtt_config["mqtt_username"] = new_username
                config_changed = True
                if DEBUG: print(f"[MAIN DEBUG] New token/username received and updated in mqtt_config.")
        else:
            if self.token is not None or self.mqtt_config.get("token") is not None:
                self.token = None
                if "token" in self.mqtt_config: del self.mqtt_config["token"]
                if "mqtt_username" in self.mqtt_config: del self.mqtt_config["mqtt_username"]
                config_changed = True
                if DEBUG: print("[MAIN ERROR] Invalid token (None) received. Clearing from mqtt_config.")

        if config_changed:
            config_path = os.path.join(script_dir, CONFIG_FILE)
            try:
                with open(config_path, "w") as f:
                    json.dump(self.mqtt_config, f, indent=2)
                if DEBUG: print(f"[MAIN DEBUG] mqtt_config saved to {CONFIG_FILE}.")
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Failed to save mqtt_config: {e}")

        if not new_token:
            if self.mqtt_manager:
                self.mqtt_manager.disconnect_client()
            self.root.after(5000, self.initialize_mqtt) 

    def door_state_changed_mqtt(self, door_payload):
        if not self.mqtt_manager or not self.mqtt_manager.token or not hasattr(self.mqtt_manager, '_publish_or_queue'):
            if DEBUG: print("[MAIN DEBUG] Door state changed, but MQTT manager or its publish method not ready.")
            return
        
        door_payload["MacAddress"]  = self.mac
        door_payload["DeviceTime"]  = datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
        if DEBUG: print("[MAIN DEBUG] Door state changed, queueing/publishing:", door_payload)
        try:
            self.mqtt_manager._publish_or_queue("iot/devices/doorstatus", door_payload, qos=1, user_properties=[("MacAddress", self.mac)])
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Error in door_state_changed_mqtt(): {e}")

    def trigger_door_open(self, duration_ms=DOOR_OPEN_DURATION_MS):
        if self.open_door_timer:
            self.root.after_cancel(self.open_door_timer)
            self.open_door_timer = None
        if self.door_sensor_handler:
            try:
                self.door_sensor_handler.open_door()
                if duration_ms > 0 :
                    self.open_door_timer = self.root.after(duration_ms, self.trigger_door_close)
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Error opening door: {e}")
        elif DEBUG: print("[MAIN DEBUG] Door handler not available.")

    def trigger_door_close(self):
        if self.open_door_timer:
            self.root.after_cancel(self.open_door_timer)
            self.open_door_timer = None
        if self.door_sensor_handler:
            try:
                self.door_sensor_handler.close_door()
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Error closing door: {e}")
        elif DEBUG: print("[MAIN DEBUG] Door handler not available.")

    def show_background(self):
        if self.bg_photo:
            if self.bg_label and self.bg_label.winfo_exists(): self.bg_label.destroy()
            self.bg_label = ctk.CTkLabel(self.root, image=self.bg_photo, text="")
            self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            self.bg_label.lower()

    def clear_frames(self, keep_background=True, clear_face_elements=True):
        face.stop_face_recognition()
        widgets_to_destroy = []
        for widget in self.root.winfo_children():
            if widget == self.frame_mqtt or widget == self.frame_menu or \
               widget == self.loading_progress or \
               (hasattr(widget, '_owner_frame') and widget._owner_frame == 'fingerprint'):
                widgets_to_destroy.append(widget)
            elif clear_face_elements and \
                 (widget == self.face_info_label or \
                  widget == self.face_image_label or \
                  widget == self.name_label):
                 widgets_to_destroy.append(widget)
        for widget in widgets_to_destroy:
             if widget and widget.winfo_exists():
                 widget.destroy()
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
            self.create_config_button()
            if self.sync_button and self.sync_button.winfo_exists():
                 self.sync_button.lift()

    def push_screen(self, screen_id, screen_func, *args):
        if self.screen_history and self.screen_history[-1][0] == screen_id:
            current_args = self.screen_history[-1][2]
            if args == current_args:
                 if DEBUG: print(f"[MAIN DEBUG] Screen {screen_id} with same args already at top.")
                 return
        self.screen_history.append((screen_id, screen_func, args))
        if DEBUG:
            history_ids = [sid for sid, _, _ in self.screen_history]
            print(f"[MAIN DEBUG] Pushing screen: {screen_id}. History: {history_ids}")
        self.clear_frames()
        screen_func(*args)

    def go_back(self):
        if len(self.screen_history) > 1:
            self.screen_history.pop()
            screen_id, screen_func, args = self.screen_history[-1]
            if DEBUG:
                history_ids = [sid for sid, _, _ in self.screen_history]
                print(f"[MAIN DEBUG] Going back to screen: {screen_id}. History: {history_ids}")
            self.clear_frames()
            screen_func(*args)
        else:
            if DEBUG: print("[MAIN DEBUG] No previous screen, going to main menu.")
            self.return_to_main_menu()

    def return_to_main_menu(self, event=None):
        if DEBUG: print("[MAIN DEBUG] Returning to main menu...")
        face.stop_face_recognition()
        self.screen_history = [( "main_menu", self.show_main_menu, ())]
        self.clear_frames()
        self.show_main_menu()

    def create_config_button(self):
        for widget in self.root.winfo_children():
            if isinstance(widget, ctk.CTkButton) and hasattr(widget, '_button_id') and widget._button_id == 'config_button':
                widget.lift()
                return
        config_button = ctk.CTkButton(self.root, text="Cài Đặt", command=self.confirm_reconfigure, width=90, height=40, fg_color="#6c757d", hover_color="#5a6268", font=("Segoe UI", 16), text_color="white")
        config_button._button_id = 'config_button'
        config_button.place(relx=0.99, rely=0.01, anchor="ne")

    def confirm_reconfigure(self):
        result = messagebox.askyesno("Xác nhận", "Bạn có chắc chắn muốn cấu hình lại thiết bị không?\nThao tác này sẽ xóa cấu hình MQTT hiện tại (bao gồm cả token đã lưu) và yêu cầu đăng nhập lại.", icon='warning', parent=self.root)
        if result: self.reconfigure()

    def reconfigure(self):
        if DEBUG: print("[MAIN DEBUG] Reconfiguration requested.")
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client()
            self.mqtt_manager = None
        self.token = None
        self.update_connection_status(False)
        if DEBUG: print("[MAIN DEBUG] MQTT Manager disconnected for reconfiguration.")
        config_path = os.path.join(script_dir, CONFIG_FILE)
        if os.path.exists(config_path):
            try:
                os.remove(config_path)
                if DEBUG: print("[MAIN DEBUG] Removed configuration file:", CONFIG_FILE)
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Error removing config file {config_path}: {e}")
        self.mqtt_config = {}
        self.screen_history = []
        self.push_screen("admin_login", self.build_admin_login_screen)

    def build_admin_login_screen(self):
        self.frame_mqtt = ctk.CTkFrame(self.root, fg_color=BG_COLOR, bg_color=BG_COLOR)
        self.frame_mqtt.place(relx=0.5, rely=0.35, anchor="center")
        ctk.CTkLabel(self.frame_mqtt, text="Xác thực tài khoản Admin", font=("Segoe UI", 24, "bold")).grid(row=0, column=0, columnspan=2, pady=(10, 20))
        ctk.CTkLabel(self.frame_mqtt, text="Tài khoản", font=("Segoe UI", 16)).grid(row=1, column=0, padx=(5, 10), pady=5, sticky="e")
        self.admin_user_entry = ctk.CTkEntry(self.frame_mqtt, width=250, height=35, font=("Segoe UI", 14))
        self.admin_user_entry.grid(row=1, column=1, padx=(10, 5), pady=5, sticky="w")
        ctk.CTkLabel(self.frame_mqtt, text="Mật khẩu", font=("Segoe UI", 16)).grid(row=2, column=0, padx=(5, 10), pady=5, sticky="e")
        self.admin_pass_entry = ctk.CTkEntry(self.frame_mqtt, width=250, height=35, show="*", font=("Segoe UI", 14))
        self.admin_pass_entry.grid(row=2, column=1, padx=(10, 5), pady=5, sticky="w")
        ctk.CTkButton(self.frame_mqtt, text="Ðăng Nhập", width=150, height=40, font=("Segoe UI", 18, "bold"), fg_color="#4f918b",hover_color="#427b75", text_color="white", command=self.check_admin_login).grid(row=3, column=0, columnspan=2, pady=(20, 20))

    def check_admin_login(self):
        username = self.admin_user_entry.get()
        password = self.admin_pass_entry.get()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            if DEBUG: print("[MAIN DEBUG] Admin authentication successful.")
            self.push_screen("mqtt_config", self.build_mqtt_config_screen)
        else:
            messagebox.showerror("Lỗi Đăng Nhập", "Tài khoản hoặc mật khẩu admin không đúng.", parent=self.root)
            self.admin_pass_entry.delete(0, "end")

    def build_mqtt_config_screen(self):
        self.frame_mqtt = ctk.CTkFrame(self.root, fg_color=BG_COLOR, bg_color=BG_COLOR)
        self.frame_mqtt.place(relx=0.5, rely=0.4, anchor="center")
        ctk.CTkLabel(self.frame_mqtt, text="CẤU HÌNH KẾT NỐI SERVER & THIẾT BỊ", font=("Segoe UI", 24, "bold")).grid(row=0, column=0, columnspan=2, pady=(5, 15))
        
        ctk.CTkLabel(self.frame_mqtt, text="Địa chỉ Server", font=("Segoe UI", 16)).grid(row=1, column=0, padx=(5,10), pady=8, sticky="e")
        self.server_entry = ctk.CTkEntry(self.frame_mqtt, width=300, height=35, placeholder_text="VD: your.server.com hoặc 192.168.1.100", font=("Segoe UI", 14))
        self.server_entry.grid(row=1, column=1, padx=(0,5), pady=8, sticky="w")
        self.server_entry.insert(0, self.mqtt_config.get("server", ""))

        ctk.CTkLabel(self.frame_mqtt, text="Cổng kết nối", font=("Segoe UI", 16)).grid(row=2, column=0, padx=(5,10), pady=8, sticky="e")
        self.mqtt_port_entry = ctk.CTkEntry(self.frame_mqtt, width=100, height=35, placeholder_text="VD: 1883", font=("Segoe UI", 14))
        self.mqtt_port_entry.grid(row=2, column=1, padx=(0,5), pady=8, sticky="w")
        self.mqtt_port_entry.insert(0, str(self.mqtt_config.get("mqtt_port", "1883")))

        ctk.CTkLabel(self.frame_mqtt, text="Phòng", font=("Segoe UI", 16)).grid(row=3, column=0, padx=(5,10), pady=8, sticky="e")
        self.room_entry = ctk.CTkEntry(self.frame_mqtt, width=200, height=35, placeholder_text="VD: P101, Lab02", font=("Segoe UI", 14))
        self.room_entry.grid(row=3, column=1, padx=(0,5), pady=8, sticky="w")
        self.room_entry.insert(0, self.mqtt_config.get("room", ""))

        button_frame = ctk.CTkFrame(self.frame_mqtt, fg_color="transparent")
        button_frame.grid(row=4, column=0, columnspan=2, pady=(20, 15))
        ctk.CTkButton(button_frame, text="TRỞ VỀ", width=120, height=40, font=("Segoe UI", 16), fg_color="#6c757d", hover_color="#5a6268", text_color="white", command=self.go_back).pack(side="left", padx=10)
        ctk.CTkButton(button_frame, text="LƯU & KẾT NỐI", width=180, height=40, font=("Segoe UI", 16, "bold"), fg_color="#4f918b", hover_color="#427b75", text_color="white", command=self.validate_and_save_connect).pack(side="right", padx=10)

    def validate_and_save_connect(self):
        server_address = self.server_entry.get().strip()
        mqtt_port_str = self.mqtt_port_entry.get().strip()
        room_name = self.room_entry.get().strip()

        if not server_address or not mqtt_port_str:
            messagebox.showerror("Lỗi", "Vui lòng điền Địa chỉ Server và MQTT Port.", parent=self.root)
            return
        
        if not room_name:
            messagebox.showerror("Lỗi", "Vui lòng điền Tên Phòng.", parent=self.root)
            return
        
        try:
            mqtt_port = int(mqtt_port_str)
            if not (0 < mqtt_port < 65536): raise ValueError("MQTT Port out of range")
        except ValueError:
            messagebox.showerror("Lỗi", "MQTT Port không hợp lệ.", parent=self.root)
            return

        http_api_port = 8080 
        
        current_token = self.mqtt_config.get("token")
        current_mqtt_user = self.mqtt_config.get("mqtt_username")
        
        new_config = { 
            "server": server_address, 
            "mqtt_port": mqtt_port, 
            "http_port": http_api_port,
            "room": room_name
        }
        
        if current_token and current_mqtt_user:
            new_config["token"] = current_token
            new_config["mqtt_username"] = current_mqtt_user
        
        config_path = os.path.join(script_dir, CONFIG_FILE)
        try:
            with open(config_path, "w") as f: json.dump(new_config, f, indent=2)
            self.mqtt_config = new_config
            if DEBUG: print("[MAIN DEBUG] Saved MQTT config:", self.mqtt_config)
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Error saving MQTT config: {e}")
            messagebox.showerror("Lỗi Lưu Trữ", f"Không thể lưu cấu hình MQTT: {e}", parent=self.root)
            return
        
        self.show_connecting_screen()
        self.root.after(100, self._init_mqtt_after_save)

    def _init_mqtt_after_save(self):
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client()
        self.mqtt_manager = None
        self.token = self.mqtt_config.get("token")
        self.initialize_mqtt()
        self.root.after(3000, self.return_to_main_menu)

    def show_connecting_screen(self):
        self.clear_frames()
        self.show_background()
        ctk.CTkLabel(self.root, text="Đang lưu cấu hình và kết nối...", font=("Segoe UI", 22), text_color="#333").place(relx=0.5, rely=0.45, anchor="center")
        self.loading_progress = ctk.CTkProgressBar(self.root, width=400, height=15, progress_color="#4f918b")
        self.loading_progress.place(relx=0.5, rely=0.55, anchor="center")
        self.loading_progress.set(0)
        self.loading_progress.start()

    def show_main_menu(self):
        self.clear_frames()
        self.show_background()
        if not self.frame_menu or not self.frame_menu.winfo_exists():
            self.frame_menu = ctk.CTkFrame(self.root, fg_color="transparent")
            self.frame_menu.place(relx=0.5, rely=0.5, anchor="center")
        else:
            for widget in self.frame_menu.winfo_children():
                widget.destroy()
        options = [
            (self.face_img, "KHUÔN MẶT", self.show_face_recognition_screen),
            (self.fingerprint_img, "VÂN TAY", self.start_fingerprint_scan),
            (self.idcard_img, "THẺ CCCD", self.start_id_card_scan),
        ]
        for idx, (img, label_text, cmd) in enumerate(options):
            if img is None: continue
            option_frame = ctk.CTkFrame(self.frame_menu, width=BUTTON_WIDTH, height=BUTTON_HEIGHT, fg_color=BG_COLOR, bg_color="transparent", corner_radius=15, border_width=2, border_color="#CCCCCC")
            option_frame.grid(row=0, column=idx, padx=PAD_X, pady=PAD_Y)
            option_frame.grid_propagate(False)
            button = ctk.CTkButton(option_frame, image=img, text=label_text, font=("Segoe UI", 20, "bold"), text_color=BUTTON_FG, compound="top", fg_color="transparent", hover_color="#EAEAEA", command=cmd)
            button.place(relx=0.5, rely=0.5, anchor="center", relwidth=1.0, relheight=1.0)

    def start_fingerprint_scan(self):
         if not self.fingerprint_sensor:
             messagebox.showerror("Lỗi Cảm Biến", "Cảm biến vân tay chưa được khởi tạo hoặc bị lỗi.", parent=self.root)
             return
         try:
              if not self.fingerprint_sensor.verifyPassword():
                   messagebox.showerror("Lỗi Cảm Biến", "Không thể xác thực với cảm biến vân tay.", parent=self.root)
                   return
         except Exception as e:
              messagebox.showerror("Lỗi Cảm Biến", f"Lỗi giao tiếp cảm biến: {e}", parent=self.root)
              return
         if DEBUG: print("[MAIN DEBUG] Starting fingerprint prompt...")
         self.clear_frames()
         fp_ui_frame = ctk.CTkFrame(self.root, fg_color="transparent")
         fp_ui_frame._owner_frame = 'fingerprint'
         fp_ui_frame.pack(expand=True, fill="both")
         fingerprint.open_fingerprint_prompt(fp_ui_frame, sensor=self.fingerprint_sensor, on_success_callback=self.handle_fingerprint_success, on_failure_callback=self.handle_fingerprint_failure)

    def start_id_card_scan(self):
         if DEBUG: print("[MAIN DEBUG] Starting ID card recognition...")
         id_card.open_id_card_recognition(on_close_callback=self.return_to_main_menu)

    def handle_fingerprint_success(self, bio_id):
        if DEBUG: print(f"[MAIN DEBUG] Fingerprint Success for bioId: {bio_id}")
        user_info_row = database.get_user_info_by_bio_id(bio_id)
        person_name_to_send = bio_id
        id_number_to_send = None
        face_image_b64 = None
        finger_image_b64 = None

        if user_info_row:
            display_name = user_info_row['person_name'] or user_info_row['id_number'] or display_name # Assuming person_name exists
            id_number_to_send = user_info_row['id_number']
            face_image_b64 = user_info_row['face_image']
            finger_image_b64 = user_info_row['finger_image']
        else:
            if DEBUG: print(f"[MAIN WARN] No user_info_row found for bioId: {bio_id}")
        self.trigger_door_open()
        if self.mqtt_manager:
            
             self.mqtt_manager.send_recognition_success(bio_id, id_number_to_send, face_image_b64, finger_image_b64)
        self.root.after(2000, self.return_to_main_menu)

    def handle_fingerprint_failure(self):
        if DEBUG: print("[MAIN DEBUG] Fingerprint Failure.")
        self.root.after(100, self.return_to_main_menu)

    def request_manual_sync(self):
        if self.mqtt_manager and self.mqtt_manager.connected:
             if DEBUG: print("[MAIN INFO] Manual sync requested.")
             self.mqtt_manager.send_device_sync()
             messagebox.showinfo("Đồng Bộ", "Đã gửi yêu cầu đồng bộ dữ liệu đến server.", parent=self.root)
        else:
             messagebox.showwarning("Lỗi MQTT", "Chưa kết nối MQTT. Không thể gửi yêu cầu đồng bộ.", parent=self.root)

    def show_face_recognition_screen(self):
        self.push_screen("face_recognition", self._build_face_recognition_ui)

    def _build_face_recognition_ui(self):
        self.clear_frames(clear_face_elements=False)
        self.show_background()
        if DEBUG: print(f"[MAIN DEBUG] Loading active FACE vectors for MAC: {self.mac}")
        face.load_active_vectors_from_db(self.mac)
        if not face.face_db:
            messagebox.showinfo("Không Tìm Thấy Khuôn Mặt", f"Không tìm thấy dữ liệu khuôn mặt nào đang hoạt động.", parent=self.root)
            self.root.after(100, self.return_to_main_menu)
            return

        if not self.face_info_label or not self.face_info_label.winfo_exists():
            self.face_info_label = ctk.CTkLabel(self.root, text="", font=("Segoe UI", 20), text_color="#333", wraplength=900)
            self.face_info_label.place(relx=0.5, rely=0.02, anchor="n")
        self.face_info_label.configure(text="")

        if not self.face_image_label or not self.face_image_label.winfo_exists():
            self.face_image_label = ctk.CTkLabel(self.root, text="", fg_color="black", width=640, height=480)
            self.face_image_label.place(relx=0.5, rely=0.5, anchor="center")
        self.face_image_label.configure(text="Đang khởi tạo Camera...", image=None, font=("Segoe UI", 18, "bold"), text_color="white")

        if not self.name_label or not self.name_label.winfo_exists():
            self.name_label = ctk.CTkLabel(self.root, text="", font=("Segoe UI", 26, "bold"), text_color="#0044cc", wraplength=900)
            self.name_label.place(relx=0.5, rely=0.95, anchor="s")
        self.name_label.configure(text="Vui lòng nhìn thẳng vào Camera")

        if DEBUG: print("[MAIN DEBUG] Starting face recognition thread...")
        if self.face_image_label and self.face_image_label.winfo_exists():
            face.open_face_recognition(on_recognition=self.handle_recognition_success, on_failure_callback=self.handle_recognition_failure, parent_label=self.face_image_label)
        else:
            if DEBUG: print("[MAIN ERROR] Cannot start face recognition: UI Label not ready.")
            messagebox.showerror("Lỗi UI", "Không thể khởi tạo khu vực camera.", parent=self.root)
            self.root.after(100, self.return_to_main_menu)

    def handle_recognition_success(self, name_key, score, frame_arr):
        if DEBUG: print(f"[MAIN DEBUG] Face Success: Key={name_key}, Score={score:.2f}")
        parts = name_key.split('_')
        display_name = parts[0] if parts else name_key
        bio_id = parts[-1] if len(parts) > 1 else name_key
        
        user_info_row = database.get_user_info_by_bio_id(bio_id)
        id_number_to_send = None
        face_image_b64 = None
        finger_image_b64 = None

        if user_info_row:
             display_name = user_info_row['person_name'] or user_info_row['id_number'] or display_name # Assuming person_name exists
             id_number_to_send = user_info_row['id_number']
             face_image_b64 = user_info_row['face_image']
             finger_image_b64 = user_info_row['finger_image']
        else:
             if DEBUG: print(f"[MAIN WARN] No user_info_row found for bioId from face key: {bio_id}")

        if self.face_info_label and self.face_info_label.winfo_exists():
            self.face_info_label.configure(text="THÀNH CÔNG", text_color="green")
        if self.name_label and self.name_label.winfo_exists():
             self.name_label.configure(text=f"XIN CHÀO, {display_name} !", text_color="green")
        
        if self.face_image_label and self.face_image_label.winfo_exists():
             profile_pic_size = (min(self.face_image_label.winfo_width(),400), min(self.face_image_label.winfo_height(),400))
             ctk_img = get_ctk_image_from_db(bio_id, size=profile_pic_size)
             if ctk_img:
                 self.face_image_label.configure(image=ctk_img, text="")
             else:
                 if DEBUG: print(f"[MAIN WARN] Stored image not found for bio_id: {bio_id}. Using captured frame.")
                 try:
                    pil_img = Image.fromarray(frame_arr)
                    pil_img_resized = pil_img.resize(profile_pic_size, Image.Resampling.LANCZOS)
                    ctk_frame_img = CTkImage(light_image=pil_img_resized, dark_image=pil_img_resized, size=profile_pic_size)
                    self.face_image_label.configure(image=ctk_frame_img, text="")
                    if not face_image_b64:
                        buffered = io.BytesIO()
                        pil_img.save(buffered, format="JPEG")
                        face_image_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                 except Exception as e:
                    if DEBUG: print(f"[MAIN ERROR] Failed to display/convert captured frame: {e}")
                    self.face_image_label.configure(image=None, text=f"Không tìm thấy ảnh\n{display_name}", font=("Segoe UI", 16), text_color="white")
        self.trigger_door_open()
        if self.mqtt_manager:
             self.mqtt_manager.send_recognition_success(bio_id, id_number_to_send, face_image_b64, finger_image_b64)
        #self.trigger_door_open()
        self.root.after(FACE_RECOGNITION_TIMEOUT_MS, self.return_to_main_menu)

    def handle_recognition_failure(self, reason="Unknown"):
        if DEBUG: print(f"[MAIN DEBUG] Face Recognition Failure: {reason}")
        if self.name_label and self.name_label.winfo_exists():
            self.name_label.configure(text="Không thể nhận diện", text_color="red")
        if self.face_image_label and self.face_image_label.winfo_exists():
             self.face_image_label.configure(image=None, text="Nhận diện thất bại.\nVui lòng thử lại.", font=("Segoe UI", 18), text_color="orange")
        self.root.after(2000, self.return_to_main_menu)

    def _sos_button_callback(self, channel):
        if not GPIO_AVAILABLE: return
        current_state = GPIO.input(SOS_BUTTON_PIN)
        if current_state != self.last_sos_button_state:
            self.last_sos_button_state = current_state
            if current_state == GPIO.LOW:
                if DEBUG: print("[MAIN INFO] SOS Button PRESSED")
                GPIO.output(BUZZER_PIN, GPIO.HIGH)
                if self.mqtt_manager:
                    self.mqtt_manager.send_sos_alert()
            else:
                if DEBUG: print("[MAIN INFO] SOS Button RELEASED")
                GPIO.output(BUZZER_PIN, GPIO.LOW)

    def _open_button_callback(self, channel):
        if not GPIO_AVAILABLE: return
        current_state = GPIO.input(OPEN_BUTTON_PIN)
        if current_state != self.last_open_button_state:
            self.last_open_button_state = current_state
            if current_state == GPIO.LOW:
                if DEBUG: print("[MAIN INFO] Open Door Button PRESSED")
                self.open_button_press_time = time.time()
                self.trigger_door_open(duration_ms=DOOR_OPEN_DURATION_MS)
            else:
                if DEBUG: print("[MAIN INFO] Open Door Button RELEASED")
                if self.open_button_press_time and (time.time() - self.open_button_press_time < DOOR_OPEN_DURATION_MS / 1000.0):
                    self.trigger_door_close()
                self.open_button_press_time = None

    def cleanup(self):
        if DEBUG: print("[MAIN INFO] Cleaning up resources...")
        face.stop_face_recognition()
        if self.mqtt_manager:
             if DEBUG: print("[MAIN INFO] Disconnecting MQTT client...")
             self.mqtt_manager.disconnect_client()
        if self.door_sensor_handler:
             if DEBUG: print("[MAIN INFO] Cleaning up door handler GPIO...")
             self.door_sensor_handler.cleanup()
        if GPIO_AVAILABLE:
            if DEBUG: print("[MAIN INFO] Cleaning up app-level GPIO...")
            channels_to_clean = [BUZZER_PIN]
            if SOS_BUTTON_PIN is not None: channels_to_clean.append(SOS_BUTTON_PIN)
            if OPEN_BUTTON_PIN is not None: channels_to_clean.append(OPEN_BUTTON_PIN)
            channels_to_clean = [pin for pin in channels_to_clean if pin is not None]
            if channels_to_clean:
                GPIO.cleanup(channels_to_clean)
        if DEBUG: print("[MAIN INFO] Exiting application.")
        if self.root and self.root.winfo_exists():
            self.root.destroy()

if __name__ == "__main__":
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.geometry("1024x600")
    root.title("Access Control System")
    # root.attributes('-fullscreen', True)
    root.resizable(False, False)
    app = App(root)
    root.mainloop()