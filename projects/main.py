# main.py
import os
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("[MAIN WARN] RPi.GPIO library not found. GPIO functions disabled.")
except RuntimeError:
    GPIO_AVAILABLE = False
    print("[MAIN WARN] Could not initialize RPi.GPIO. GPIO functions disabled.")

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
import fingerprint
import rfid
from door import Door
from mqtt import MQTTManager
import database

try:
    from pyfingerprint.pyfingerprint import PyFingerprint
except ImportError:
    PyFingerprint = None
    print("[MAIN WARN] PyFingerprint library not found, fingerprint functions disabled.")
except Exception as e_fp_import:
    PyFingerprint = None
    print(f"[MAIN ERROR] Error importing PyFingerprint: {e_fp_import}")

try:
    import board
    import busio
    from adafruit_pn532.i2c import PN532_I2C
except ImportError:
    PN532_I2C = None
    board = None
    busio = None
    print("[MAIN WARN] Adafruit PN532/Blinka libraries not found, RFID functions disabled.")
except Exception as e_pn532_import:
    PN532_I2C = None
    board = None
    busio = None
    print(f"[MAIN ERROR] Error importing PN532/Blinka libraries: {e_pn532_import}")

load_dotenv()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "password")

DEBUG = True
BG_COLOR = "#F5F5F5"
BUTTON_FG = "#333333"
BUTTON_DISABLED_FG = "#A0A0A0"
BUTTON_FONT = ("Segoe UI", 24)
BUTTON_WIDTH = 350
BUTTON_HEIGHT = 350
PAD_X = 15
PAD_Y = 15
CONFIG_FILE = "mqtt_config.json"
DEVICE_AUTH_CONFIG_FILE = "device_auth_config.json"

FACE_RECOGNITION_TIMEOUT_MS = 2000
DOOR_OPEN_DURATION_MS = 10000
HEALTHCHECK_INTERVAL_MS = 10000

FINGERPRINT_PORT = '/dev/ttyAMA0'
FINGERPRINT_BAUDRATE = 57600

RFID_RESET_PIN_BCM = None
RFID_IRQ_PIN_BCM = None
RFID_AUTH_COOLDOWN_S = 3

DOOR_SENSOR_PIN = 17
DOOR_RELAY_PIN = 27
SOS_BUTTON_PIN = 5
OPEN_BUTTON_PIN = 13
BUZZER_PIN = 26
BUTTON_DEBOUNCE_TIME = 300
BUZZER_BEEP_DURATION_MS = 1000

SUCCESS_COLOR = "green"
ERROR_COLOR = "red"
GMT_PLUS_7 = timezone(timedelta(hours=7))
DATETIME_FORMAT_STR = "%Y-%m-%d %H:%M:%S"

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
            ctk_size = pil_image.size
            if isinstance(size, tuple) and len(size) == 2: ctk_size = size
            elif isinstance(size, int): pil_image.thumbnail((size,size), Image.Resampling.LANCZOS); ctk_size = pil_image.size
            return CTkImage(light_image=pil_image, dark_image=pil_image, size=ctk_size)
        except base64.binascii.Error:
            if DEBUG: print(f"[MAIN ERROR] Base64 decode error for user_id {user_id}.")
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Image processing error for user_id {user_id}: {e}")
    return None

def get_from_row(row, key, default=None):
    if row is None: return default
    try: return row[key]
    except (IndexError, KeyError): return default

class App:
    def __init__(self, root):
        self.frame_result_display = None
        self.root = root
        self.mac = get_mac_address()
        if DEBUG: print("[MAIN DEBUG] Device MAC Address:", self.mac)

        try:
            database.initialize_database()
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to initialize database: {e}\nApplication cannot continue.", parent=self.root)
            root.quit(); return

        self.token = None
        self.mqtt_manager = None
        self.mqtt_config = {}
        self.screen_history = []
        self.fingerprint_sensor = None
        self.rfid_sensor = None

        self.auth_config = {
            "FaceSecurityLevel": 1,
            "BioAuthType": {
                "IsFace": True, "IsFinger": True, "IsIdCard": True, "IsIris": False,
                "FingerTime": 30, "IdCardTime": 30, "IrisTime": 30, "Direction": "IN"
            }
        }
        self.load_device_auth_config()

        self.multi_factor_auth_state = {
            "active": False, "current_step_succeeded": None, "required_level": 0,
            "auth_data_collected": {}, "timeout_timer_id": None, "prompt_frame": None
        }

        self.connection_status_label = None; self.frame_mqtt_config = None; self.frame_main_menu = None
        self.bg_label = None; self.loading_progress = None
        self.face_ui_container = None; self.face_info_label = None; self.face_image_label = None; self.face_name_label = None
        self.admin_user_entry = None; self.admin_pass_entry = None; self.server_entry = None; self.mqtt_port_entry = None; self.room_entry = None
        self.last_sos_button_state = GPIO.HIGH if GPIO_AVAILABLE else None
        self.last_open_button_state = GPIO.HIGH if GPIO_AVAILABLE else None
        self.open_button_press_time = None; self.open_door_timer = None
        self.current_rfid_scan_display_frame = None; self.last_rfid_auth_time = 0; self.rfid_scan_active = False
        self.buzzer_timer_id = None

        self.connected_image = load_image("images/connected.jpg", (50, 50))
        self.disconnected_image = load_image("images/disconnected.jpg", (50, 50))
        self.bg_photo = load_image("images/background.jpeg", (1024, 600))
        
        self.face_icon_img = load_image("images/face.png", (BUTTON_WIDTH-80, BUTTON_HEIGHT-100))
        self.fingerprint_icon_img = load_image("images/fingerprint.png", (BUTTON_WIDTH-80, BUTTON_HEIGHT-100))
        self.rfid_icon_img = load_image("images/rfid.png", (BUTTON_WIDTH-80, BUTTON_HEIGHT-100))
        
        self.face_icon_disable_img = load_image("images/face_disable.png", (BUTTON_WIDTH-80, BUTTON_HEIGHT-100))
        self.fingerprint_icon_disable_img = load_image("images/fingerprint_disable.png", (BUTTON_WIDTH-80, BUTTON_HEIGHT-100))
        self.rfid_icon_disable_img = load_image("images/rfid_disable.png", (BUTTON_WIDTH-80, BUTTON_HEIGHT-100))
        
        self.sync_icon_img = load_image("images/sync.png", (40, 40))

        self.root.configure(fg_color=BG_COLOR)
        self.show_background()
        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="Chưa kết nối", font=("Segoe UI", 11), text_color="red", compound="left")
        self.connection_status_label.place(relx=0.04, rely=0.95, anchor="sw")
        self.create_config_button()
        self.sync_button = ctk.CTkButton(self.root, image=self.sync_icon_img, text="", width=40, height=40, fg_color="transparent", hover_color="#E0E0E0", command=self.request_manual_sync)
        self.sync_button.place(relx=0.04, rely=0.02, anchor="nw")
        self.initialize_fingerprint_sensor(); self.initialize_rfid_sensor()
        if GPIO_AVAILABLE: self.setup_gpio_components()
        self.door_sensor_handler = None
        if GPIO_AVAILABLE:
            try:
                self.door_sensor_handler = Door(sensor_pin=DOOR_SENSOR_PIN, relay_pin=DOOR_RELAY_PIN, relay_active_high=False, mqtt_publish_callback=self.door_state_changed_mqtt_publish)
                if DEBUG: print("[MAIN INFO] Door handler initialized.")
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Error initializing Door Handler: {e}.")
        else:
            if DEBUG: print("[MAIN WARN] GPIO not available, Door handler not initialized.")

        config_path = os.path.join(script_dir, CONFIG_FILE)
        proceed_to_main_menu = False
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f: loaded_config = json.load(f)
                if loaded_config.get("server") and loaded_config.get("mqtt_port") and loaded_config.get("room"):
                    self.mqtt_config = loaded_config; self.token = self.mqtt_config.get("token")
                    if DEBUG: print("[MAIN INFO] Config file found and seems valid. Initializing MQTT...")
                    self.initialize_mqtt(); proceed_to_main_menu = True
                else:
                    if DEBUG: print(f"[MAIN ERROR] MQTT Config file {config_path} is incomplete. Deleting and reconfiguring.")
                    if os.path.exists(config_path): os.remove(config_path)
                    self.mqtt_config = {}; self.token = None
            except json.JSONDecodeError:
                if DEBUG: print(f"[MAIN ERROR] Error reading MQTT config {config_path}. Invalid JSON. Deleting and reconfiguring.")
                if os.path.exists(config_path): os.remove(config_path)
                self.mqtt_config = {}; self.token = None
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] An error occurred loading MQTT config: {e}. Deleting and reconfiguring.")
                if os.path.exists(config_path): os.remove(config_path)
                self.mqtt_config = {}; self.token = None
        else:
            if DEBUG: print(f"[MAIN INFO] MQTT Config file {CONFIG_FILE} not found. Proceeding to admin login for setup.")

        if proceed_to_main_menu: self.push_screen("main_menu", self.show_main_menu_screen)
        else: self.push_screen("admin_login", self.build_admin_login_screen)
        self.schedule_healthcheck()
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup)
    def door_state_changed_mqtt_publish(self, door_payload): 
        if not self.mqtt_manager or not self.mqtt_manager.is_actively_connected():
            if DEBUG: print("[MAIN DEBUG] Door state changed, but MQTT manager not ready or not actively connected.")
            return
        
        door_payload["MacAddress"]  = self.mac
        door_payload["DeviceTime"]  = datetime.now(GMT_PLUS_7).strftime(DATETIME_FORMAT_STR) 
        
        if DEBUG: print("[MAIN DEBUG] Door state changed, attempting to publish:", door_payload)
        try:
            self.mqtt_manager._publish_or_queue( 
                topic="iot/devices/doorstatus", 
                payload_dict=door_payload, 
                qos=1, 
                user_properties=[("MacAddress", self.mac)]
            )
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Error in door_state_changed_mqtt_publish(): {e}")

    def on_token_received_from_mqtt(self, new_username, new_token): 
        config_changed = False
        if new_token and new_username:
            if self.token != new_token or self.mqtt_config.get("mqtt_username") != new_username:
                if DEBUG: print(f"[MAIN DEBUG] New token/username received via callback. Updating App's local config. User: {new_username}")
                self.token = new_token
                self.mqtt_config["token"] = new_token 
                self.mqtt_config["mqtt_username"] = new_username
                config_changed = True
        else: 
            if self.token is not None or self.mqtt_config.get("token") is not None:
                if DEBUG: print("[MAIN INFO] Token cleared by MQTT manager (via callback). Updating App's local config.")
                self.token = None
                if "token" in self.mqtt_config: del self.mqtt_config["token"]
                if "mqtt_username" in self.mqtt_config: del self.mqtt_config["mqtt_username"]
                config_changed = True

        if config_changed:
            config_path = os.path.join(script_dir, CONFIG_FILE)
            try:
                with open(config_path, "w") as f:
                    json.dump(self.mqtt_config, f, indent=2)
                if DEBUG: print(f"[MAIN DEBUG] App's mqtt_config (reflecting token status) saved to {CONFIG_FILE}.")
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Failed to save updated mqtt_config from App: {e}")

    def push_screen(self, screen_id, screen_func, *args): 
       
        if self.screen_history and self.screen_history[-1][0] == screen_id:
            current_args = self.screen_history[-1][2]
            if args == current_args: 
                 if DEBUG: print(f"[MAIN DEBUG] Screen {screen_id} with same arguments already at top of history. Skipping push.")
                 return
                 
        self.screen_history.append((screen_id, screen_func, args))
        if DEBUG:
            history_ids = [sid for sid, _, _ in self.screen_history] 
            print(f"[MAIN DEBUG] Pushing screen: {screen_id}. History: {history_ids}")
        
        self.clear_frames() 
        self.root.update_idletasks() 
        screen_func(*args) 

    def clear_frames(self, keep_background=True, clear_face_ui=True, clear_rfid_ui=True): 
       
        face.stop_face_recognition() 

        widgets_to_destroy = []
        if self.frame_mqtt_config and self.frame_mqtt_config.winfo_exists():
            widgets_to_destroy.append(self.frame_mqtt_config); self.frame_mqtt_config = None
        if self.frame_main_menu and self.frame_main_menu.winfo_exists():
            widgets_to_destroy.append(self.frame_main_menu); self.frame_main_menu = None
        if self.loading_progress and self.loading_progress.winfo_exists():
            widgets_to_destroy.append(self.loading_progress); self.loading_progress = None
        if self.frame_result_display and self.frame_result_display.winfo_exists():
            widgets_to_destroy.append(self.frame_result_display); self.frame_result_display = None
        
        # Tìm các frame được tạo bởi module fingerprint
        for widget in self.root.winfo_children():
            if hasattr(widget, '_owner_module') and widget._owner_module == 'fingerprint_ui':
                widgets_to_destroy.append(widget)

        if clear_face_ui:
            if self.face_ui_container and self.face_ui_container.winfo_exists():
                widgets_to_destroy.append(self.face_ui_container)
            self.face_ui_container = None; self.face_info_label = None
            self.face_image_label = None; self.face_name_label = None

        if clear_rfid_ui:
            if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
                widgets_to_destroy.append(self.current_rfid_scan_display_frame)
            self.current_rfid_scan_display_frame = None
            self.rfid_scan_active = False

        for widget in widgets_to_destroy:
             if widget and widget.winfo_exists(): 
                 widget.destroy()
        
        if keep_background:
            self.show_background() # Vẽ lại background
            # Đưa các widget cố định lên trên lại
            if self.connection_status_label and self.connection_status_label.winfo_exists():
                 self.connection_status_label.lift()
            self.create_config_button() # Vẽ lại nút config (hoặc lift nếu đã có)
            if self.sync_button and self.sync_button.winfo_exists():
                 self.sync_button.lift()
    
    def create_config_button(self):
        # Kiểm tra xem nút đã tồn tại chưa để tránh tạo lại không cần thiết
        for widget in self.root.winfo_children():
            if isinstance(widget, ctk.CTkButton) and hasattr(widget, '_button_id') and widget._button_id == 'config_button':
                widget.lift() # Nếu đã có, chỉ cần đưa nó lên trên
                return
                
        config_button = ctk.CTkButton(self.root, text="Cài Đặt", 
                                      command=self.confirm_reconfigure_device, # Đảm bảo hàm này cũng tồn tại
                                      width=100, height=38, 
                                      font=("Segoe UI", 15), text_color="white",
                                      fg_color="#6C87D0", hover_color="#5A6268", corner_radius=6)
        config_button._button_id = 'config_button' # Đánh dấu nút để kiểm tra lại
        config_button.place(relx=0.98, rely=0.015, anchor="ne")

    def confirm_reconfigure_device(self): # Đảm bảo hàm này cũng tồn tại
        result = messagebox.askyesno("Xác Nhận Cấu Hình Lại", 
                                     "Bạn có muốn cấu hình lại thiết bị không?\n\n",
                                     icon='warning', parent=self.root)
        if result:
            self.reconfigure_device_settings() # Đảm bảo hàm này cũng tồn tại
    def initialize_fingerprint_sensor(self):
        if PyFingerprint is None:
            if DEBUG: print("[MAIN WARN] PyFingerprint library not loaded. Fingerprint sensor disabled.")
            self.fingerprint_sensor = None # Đảm bảo self.fingerprint_sensor được khởi tạo là None
            return
        try:
            if DEBUG: print(f"[MAIN INFO] Initializing fingerprint sensor on {FINGERPRINT_PORT}...")
            self.fingerprint_sensor = PyFingerprint(FINGERPRINT_PORT, FINGERPRINT_BAUDRATE, 0xFFFFFFFF, 0x00000000)
            if self.fingerprint_sensor.verifyPassword():
                if DEBUG: print("[MAIN INFO] Fingerprint sensor verified.")
                if self.mqtt_manager: 
                    self.mqtt_manager.set_fingerprint_sensor(self.fingerprint_sensor)
            else:
                if DEBUG: print("[MAIN ERROR] Failed to verify fingerprint sensor password.")
                self.fingerprint_sensor = None
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Failed to initialize fingerprint sensor: {e}")
            self.fingerprint_sensor = None

    def initialize_rfid_sensor(self):
        if PN532_I2C is None or board is None or busio is None:
            if DEBUG: print("[MAIN WARN] PN532/Blinka libraries not found, RFID functions disabled.")
            self.rfid_sensor = None 
            return
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            
            reset_pin_obj = None
            if RFID_RESET_PIN_BCM is not None and GPIO_AVAILABLE: 
                import digitalio 
                pin_name = f"D{RFID_RESET_PIN_BCM}"
                if hasattr(board, pin_name):
                    reset_pin_obj = digitalio.DigitalInOut(getattr(board, pin_name))
                else:
                    if DEBUG: print(f"[MAIN WARN] Board D{RFID_RESET_PIN_BCM} not found for RFID reset pin.")


            irq_pin_obj = None
            if RFID_IRQ_PIN_BCM is not None and GPIO_AVAILABLE: 
                import digitalio
                pin_name = f"D{RFID_IRQ_PIN_BCM}"
                if hasattr(board, pin_name):
                    irq_pin_obj = digitalio.DigitalInOut(getattr(board, pin_name))
                else:
                    if DEBUG: print(f"[MAIN WARN] Board D{RFID_IRQ_PIN_BCM} not found for RFID IRQ pin.")


            self.rfid_sensor = PN532_I2C(i2c, debug=False, reset=reset_pin_obj, irq=irq_pin_obj)
            self.rfid_sensor.SAM_configuration()
            ic, ver, rev, support = self.rfid_sensor.firmware_version
            if DEBUG: print(f"[MAIN INFO] PN532 I2C sensor initialized for RFID. Firmware ver: {ver}.{rev}")
            if self.mqtt_manager: 
                self.mqtt_manager.set_rfid_sensor(self.rfid_sensor)
        except ValueError as ve: 
            if DEBUG: print(f"[MAIN ERROR] RFID I2C device not found or pin config error: {ve}")
            self.rfid_sensor = None
        except RuntimeError as rte: 
            if DEBUG: print(f"[MAIN ERROR] Failed to initialize PN532 (RuntimeError, possibly device not found/ready): {rte}")
            self.rfid_sensor = None
        except NameError as ne: 
             if DEBUG: print(f"[MAIN ERROR] Failed to initialize RFID pins, digitalio/Blinka may not be set up correctly: {ne}")
             self.rfid_sensor = None
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Failed to initialize RFID I2C sensor: {e}")
            self.rfid_sensor = None
    def setup_gpio_components(self): 
        if not GPIO_AVAILABLE:
            if DEBUG: print("[MAIN WARN] GPIO not available, skipping GPIO component setup.")
            return
        try:
            if DEBUG: print("[MAIN INFO] Starting GPIO component setup...")
            GPIO.setmode(GPIO.BCM) # Sử dụng BCM numbering
            GPIO.setwarnings(False) # Tắt các cảnh báo GPIO không cần thiết
            # Nút SOS
            GPIO.setup(SOS_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            # Gỡ bỏ event cũ nếu có để tránh đăng ký nhiều lần
            try: GPIO.remove_event_detect(SOS_BUTTON_PIN)
            except Exception as e_remove: 
                if DEBUG: print(f"[MAIN TRACE] Error removing event for SOS_BUTTON_PIN (ignorable): {e_remove}")
            GPIO.add_event_detect(SOS_BUTTON_PIN, GPIO.BOTH, # Phát hiện cả nhấn và thả
                                  callback=self._sos_button_state_changed_callback, # Hàm callback
                                  bouncetime=BUTTON_DEBOUNCE_TIME) # Thời gian chống rung
            self.last_sos_button_state = GPIO.input(SOS_BUTTON_PIN) # Lưu trạng thái ban đầu
            if DEBUG: print(f"[MAIN DEBUG] SOS Button (Pin {SOS_BUTTON_PIN}) setup with GPIO.BOTH complete.")

            # Nút mở cửa
            GPIO.setup(OPEN_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            try: GPIO.remove_event_detect(OPEN_BUTTON_PIN)
            except Exception as e_remove:
                if DEBUG: print(f"[MAIN TRACE] Error removing event for OPEN_BUTTON_PIN (ignorable): {e_remove}")
            GPIO.add_event_detect(OPEN_BUTTON_PIN, GPIO.BOTH,
                                  callback=self._open_button_state_changed_callback,
                                  bouncetime=BUTTON_DEBOUNCE_TIME)
            self.last_open_button_state = GPIO.input(OPEN_BUTTON_PIN)
            if DEBUG: print(f"[MAIN DEBUG] Open Button (Pin {OPEN_BUTTON_PIN}) setup with GPIO.BOTH complete.")

            # Còi Buzzer
            GPIO.setup(BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW) # LOW là TẮT (nếu còi kích hoạt mức HIGH)
            if DEBUG: print(f"[MAIN DEBUG] Buzzer (Pin {BUZZER_PIN}) setup as OUTPUT complete.")
            
            if DEBUG: print("[MAIN INFO] All GPIO components initialized successfully.")
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Failed to setup GPIO components: {e}")
            
    def beep_buzzer(self, duration_ms=BUZZER_BEEP_DURATION_MS): #  
        if GPIO_AVAILABLE and hasattr(self, 'BUZZER_PIN'): # Kiểm tra GPIO và sự tồn tại của BUZZER_PIN
            # Hủy timer còi cũ nếu có (để tránh nhiều tiếng bíp chồng chéo hoặc kéo dài không mong muốn)
            if self.buzzer_timer_id: 
                try: 
                    self.root.after_cancel(self.buzzer_timer_id)
                    if DEBUG: print(f"[MAIN TRACE] Canceled existing buzzer timer: {self.buzzer_timer_id}")
                except Exception as e_cancel_buzzer:
                    if DEBUG: print(f"[MAIN TRACE] Error canceling buzzer timer (ignorable): {e_cancel_buzzer}")
                self.buzzer_timer_id = None # Đặt lại timer ID
            
            # Bật còi (giả sử HIGH là BẬT, LOW là TẮT)
            GPIO.output(BUZZER_PIN, GPIO.HIGH) 
            if DEBUG: print(f"[MAIN DEBUG] Buzzer ON (for {duration_ms}ms)")
            
            # Lên lịch để tắt còi sau một khoảng thời gian
            self.buzzer_timer_id = self.root.after(duration_ms, self._turn_off_buzzer)
        elif DEBUG and not GPIO_AVAILABLE:
            print("[MAIN DEBUG] Buzzer beep skipped: GPIO not available.")
        elif DEBUG:
            print("[MAIN DEBUG] Buzzer beep skipped: BUZZER_PIN attribute not found or not configured.")


    def _turn_off_buzzer(self): #  HÀM NÀY CŨNG CẦN CÓ
        if GPIO_AVAILABLE and hasattr(self, 'BUZZER_PIN'):
            GPIO.output(BUZZER_PIN, GPIO.LOW) # Tắt còi
            if DEBUG: print(f"[MAIN DEBUG] Buzzer OFF")
        
        # Đặt lại ID timer sau khi còi đã tắt
        self.buzzer_timer_id = None
            
    def trigger_door_open(self, duration_ms=DOOR_OPEN_DURATION_MS): #  
        # Hủy timer mở cửa cũ nếu có (để tránh nhiều timer chạy song song)
        if self.open_door_timer is not None:
            try:
               self.root.after_cancel(self.open_door_timer)
               if DEBUG: print(f"[MAIN TRACE] Canceled existing door open timer: {self.open_door_timer}")
            except Exception as e_cancel:
               # Lỗi này thường không nghiêm trọng, có thể bỏ qua nếu timer ID không hợp lệ
               if DEBUG: print(f"[MAIN TRACE] Error canceling door open timer (ignorable): {e_cancel}")
            self.open_door_timer = None

        if self.door_sensor_handler: # Kiểm tra xem door_sensor_handler đã được khởi tạo chưa
            try:
                self.door_sensor_handler.open_door() # Gọi hàm open_door của đối tượng Door
                if DEBUG: print("[MAIN INFO] Door opened via trigger_door_open.")
                
                # Nếu duration_ms > 0, lên lịch tự động đóng cửa
                if duration_ms > 0:
                   self.open_door_timer = self.root.after(duration_ms, self.trigger_door_close)
                   if DEBUG: print(f"[MAIN DEBUG] Door auto-close timer set: ID {self.open_door_timer} for {duration_ms}ms")
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Error triggering door open: {e}")
        elif DEBUG:
            print("[MAIN DEBUG] Door handler not available to open door.")
        
    def trigger_door_close(self): #  HÀM NÀY CŨNG CẦN CÓ
        # Hủy timer nếu nó được gọi thủ công trước khi timeout
        if self.open_door_timer is not None:
            try:
                self.root.after_cancel(self.open_door_timer)
                if DEBUG: print(f"[MAIN TRACE] Door auto-close timer explicitly canceled by trigger_door_close: {self.open_door_timer}")
            except Exception as e_cancel:
                if DEBUG: print(f"[MAIN TRACE] Error canceling door auto-close timer (ignorable): {e_cancel}")
            self.open_door_timer = None

        if self.door_sensor_handler: # Kiểm tra xem door_sensor_handler đã được khởi tạo chưa
            try:
               self.door_sensor_handler.close_door() # Gọi hàm close_door của đối tượng Door
               if DEBUG: print("[MAIN INFO] Door closed via trigger_door_close.")
            except Exception as e:
               if DEBUG: print(f"[MAIN ERROR] Error triggering door close: {e}")
        elif DEBUG:
            print("[MAIN DEBUG] Door handler not available to close door.")
            
    def build_admin_login_screen(self): #  
        if self.frame_mqtt_config and self.frame_mqtt_config.winfo_exists(): # Sử dụng frame_mqtt_config để chứa UI này
            self.frame_mqtt_config.destroy()
        
        self.frame_mqtt_config = ctk.CTkFrame(self.root, fg_color=BG_COLOR, corner_radius=10)
        self.frame_mqtt_config.place(relx=0.5, rely=0.4, anchor="center") # Điều chỉnh vị trí nếu cần

        ctk.CTkLabel(self.frame_mqtt_config, text="XÁC THỰC TÀI KHOẢN ADMIN", 
                     font=("Segoe UI", 22, "bold"), text_color="#333").grid(row=0, column=0, columnspan=2, pady=(15, 25), padx=20)
        
        ctk.CTkLabel(self.frame_mqtt_config, text="Tài khoản Admin:", 
                     font=("Segoe UI", 16)).grid(row=1, column=0, padx=(20, 10), pady=8, sticky="e")
        self.admin_user_entry = ctk.CTkEntry(self.frame_mqtt_config, width=280, height=40, 
                                             font=("Segoe UI", 15), placeholder_text="Nhập tài khoản")
        self.admin_user_entry.grid(row=1, column=1, padx=(0, 20), pady=8, sticky="w")
        
        ctk.CTkLabel(self.frame_mqtt_config, text="Mật khẩu:", 
                     font=("Segoe UI", 16)).grid(row=2, column=0, padx=(20, 10), pady=8, sticky="e")
        self.admin_pass_entry = ctk.CTkEntry(self.frame_mqtt_config, width=280, height=40, show="*", 
                                             font=("Segoe UI", 15), placeholder_text="Nhập mật khẩu")
        self.admin_pass_entry.grid(row=2, column=1, padx=(0, 20), pady=8, sticky="w")
        
        login_button = ctk.CTkButton(self.frame_mqtt_config, text="ĐĂNG NHẬP", width=180, height=45, 
                                     font=("Segoe UI", 17, "bold"), fg_color="#007AFF", hover_color="#0056B3", 
                                     text_color="white", command=self.validate_admin_login) # Đảm bảo hàm validate_admin_login tồn tại
        login_button.grid(row=3, column=0, columnspan=2, pady=(30, 20))
    def schedule_healthcheck(self): #  
        # Gửi healthcheck nếu MQTT manager tồn tại và đang kết nối
        if self.mqtt_manager and self.mqtt_manager.is_actively_connected():
            self.mqtt_manager.send_healthcheck() # Giả sử MQTTManager có hàm này

        # Lên lịch để hàm này được gọi lại sau HEALTHCHECK_INTERVAL_MS
        # Kiểm tra xem root window còn tồn tại không trước khi gọi after
        if self.root and self.root.winfo_exists():
            self.root.after(HEALTHCHECK_INTERVAL_MS, self.schedule_healthcheck)
        else:
            if DEBUG: print("[MAIN WARN] Root window destroyed, cannot schedule next healthcheck.")
    def validate_admin_login(self): #  
        if not self.admin_user_entry or not self.admin_pass_entry:
            if DEBUG: print("[MAIN ERROR] Admin login UI elements not initialized.")
            messagebox.showerror("Lỗi Giao Diện", "Không thể xác thực, thành phần giao diện bị thiếu.", parent=self.root)
            return

        username = self.admin_user_entry.get()
        password = self.admin_pass_entry.get()

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            if DEBUG: print("[MAIN DEBUG] Admin authentication successful.")
           
            self.push_screen("mqtt_config_setup", self.build_mqtt_config_screen) 
        else:
            messagebox.showerror("Lỗi Đăng Nhập", 
                                 "Tài khoản hoặc mật khẩu Admin không đúng.\nVui lòng thử lại.", 
                                 parent=self.frame_mqtt_config or self.root) 
            if self.admin_pass_entry: 
                self.admin_pass_entry.delete(0, "end")
                
    def build_mqtt_config_screen(self): 
        if self.frame_mqtt_config and self.frame_mqtt_config.winfo_exists():
            self.frame_mqtt_config.destroy()

        self.frame_mqtt_config = ctk.CTkFrame(self.root, fg_color=BG_COLOR, corner_radius=10)
        self.frame_mqtt_config.place(relx=0.5, rely=0.45, anchor="center") # Điều chỉnh vị trí nếu cần

        ctk.CTkLabel(self.frame_mqtt_config, text="CẤU HÌNH KẾT NỐI MQTT & THIẾT BỊ", 
                     font=("Segoe UI", 22, "bold"), text_color="#333").grid(row=0, column=0, columnspan=2, pady=(15, 20), padx=20)

        ctk.CTkLabel(self.frame_mqtt_config, text="Địa chỉ Server MQTT:", 
                     font=("Segoe UI", 16)).grid(row=1, column=0, padx=(20,10), pady=8, sticky="e")
        self.server_entry = ctk.CTkEntry(self.frame_mqtt_config, width=320, height=40, 
                                         placeholder_text="VD: mqtt.example.com hoặc IP", font=("Segoe UI", 15))
        self.server_entry.grid(row=1, column=1, padx=(0,20), pady=8, sticky="w")
        self.server_entry.insert(0, self.mqtt_config.get("server", "")) # Hiển thị giá trị cũ nếu có

        # Trường nhập Cổng MQTT
        ctk.CTkLabel(self.frame_mqtt_config, text="Cổng MQTT:", 
                     font=("Segoe UI", 16)).grid(row=2, column=0, padx=(20,10), pady=8, sticky="e")
        self.mqtt_port_entry = ctk.CTkEntry(self.frame_mqtt_config, width=120, height=40, 
                                            placeholder_text="VD: 1883", font=("Segoe UI", 15))
        self.mqtt_port_entry.grid(row=2, column=1, padx=(0,20), pady=8, sticky="w")
        self.mqtt_port_entry.insert(0, str(self.mqtt_config.get("mqtt_port", "1883"))) # Hiển thị giá trị cũ

        # Trường nhập Tên Phòng/Vị trí
        ctk.CTkLabel(self.frame_mqtt_config, text="Tên Phòng/Vị trí:", 
                     font=("Segoe UI", 16)).grid(row=3, column=0, padx=(20,10), pady=8, sticky="e")
        self.room_entry = ctk.CTkEntry(self.frame_mqtt_config, width=250, height=40, 
                                       placeholder_text="VD: Phòng Họp A, Sảnh Chính", font=("Segoe UI", 15))
        self.room_entry.grid(row=3, column=1, padx=(0,20), pady=8, sticky="w")
        self.room_entry.insert(0, self.mqtt_config.get("room", "")) # Hiển thị giá trị cũ

        # Frame chứa các nút Quay Lại và Lưu
        button_frame = ctk.CTkFrame(self.frame_mqtt_config, fg_color="transparent")
        button_frame.grid(row=4, column=0, columnspan=2, pady=(30, 20))
        
        back_button = ctk.CTkButton(button_frame, text="QUAY LẠI", width=140, height=45, 
                                    font=("Segoe UI", 16), fg_color="#6C87D0", hover_color="#5A6268", 
                                    text_color="white", command=self.go_back) # Đảm bảo hàm go_back tồn tại
        back_button.pack(side="left", padx=15)
        
        save_button = ctk.CTkButton(button_frame, text="LƯU & KẾT NỐI", width=200, height=45, 
                                   font=("Segoe UI", 16, "bold"), fg_color="#007AFF", hover_color="#0056B3", 
                                   text_color="white", command=self.validate_and_save_mqtt_settings) # Đảm bảo hàm này tồn tại
        save_button.pack(side="right", padx=15)
    def go_back(self): #  
        if len(self.screen_history) > 1: # Chỉ quay lại nếu có ít nhất 2 màn hình trong lịch sử
            self.screen_history.pop() # Xóa màn hình hiện tại khỏi lịch sử
            screen_id, screen_func, args = self.screen_history[-1] # Lấy màn hình trước đó
            
            if DEBUG:
                history_ids = [sid for sid, _, _ in self.screen_history]
                print(f"[MAIN DEBUG] Going back to screen: {screen_id}. History: {history_ids}")
            
            self.clear_frames() # Xóa các frame của màn hình hiện tại
            self.root.update_idletasks() # Đảm bảo UI được cập nhật
            screen_func(*args) # Gọi hàm để vẽ lại màn hình trước đó
        else:
            # Nếu không còn màn hình nào để quay lại (hoặc chỉ còn main_menu)
            # thì quay về main menu
            if DEBUG: print("[MAIN DEBUG] No previous screen in history, or at main menu. Returning to main menu.")
            self.return_to_main_menu_screen() # Đảm bảo hàm này tồn tại
    def validate_and_save_mqtt_settings(self): #  
        if not self.server_entry or not self.mqtt_port_entry or not self.room_entry:
            if DEBUG: print("[MAIN ERROR] MQTT config UI elements not initialized.")
            messagebox.showerror("Lỗi Giao Diện", "Không thể lưu cấu hình, thành phần giao diện bị thiếu.", parent=self.root)
            return

        server_address = self.server_entry.get().strip()
        mqtt_port_str = self.mqtt_port_entry.get().strip()
        room_name = self.room_entry.get().strip()

        if not server_address or not mqtt_port_str:
            messagebox.showerror("Thiếu Thông Tin", 
                                 "Vui lòng điền Địa chỉ Server và Cổng MQTT.", 
                                 parent=self.frame_mqtt_config or self.root)
            return
        if not room_name: # Tên phòng cũng là bắt buộc
            messagebox.showerror("Thiếu Thông Tin", 
                                 "Vui lòng điền Tên Phòng/Vị trí của thiết bị.", 
                                 parent=self.frame_mqtt_config or self.root)
            return
        
        try:
            mqtt_port = int(mqtt_port_str)
            if not (0 < mqtt_port < 65536): # Cổng hợp lệ từ 1 đến 65535
                raise ValueError("MQTT Port out of valid range (1-65535)")
        except ValueError:
            messagebox.showerror("Lỗi Dữ Liệu", 
                                 "Cổng MQTT không hợp lệ. Vui lòng nhập một số (1-65535).", 
                                 parent=self.frame_mqtt_config or self.root)
            return

        # Lấy http_port từ config cũ nếu có, hoặc dùng default
        http_api_port = self.mqtt_config.get("http_port", 8080) 
        
        # Tạo dictionary cấu hình mới
        new_config = { 
            "server": server_address, 
            "mqtt_port": mqtt_port, 
            "http_port": http_api_port, # Giữ lại hoặc cho phép cấu hình http_port nếu cần
            "room": room_name
            # Token và mqtt_username sẽ được MQTTManager tự lấy và callback về App để lưu
        }
        
        config_path = os.path.join(script_dir, CONFIG_FILE)
        try:
            with open(config_path, "w") as f:
                json.dump(new_config, f, indent=2)
            self.mqtt_config = new_config # Cập nhật config trong App instance
            if DEBUG: print("[MAIN DEBUG] Saved new MQTT configuration (token will be fetched by MQTTManager):", self.mqtt_config)
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Error saving MQTT config to file {config_path}: {e}")
            messagebox.showerror("Lỗi Lưu Trữ", 
                                 f"Không thể lưu cấu hình MQTT: {e}", 
                                 parent=self.frame_mqtt_config or self.root)
            return
        
        self.show_connecting_to_server_screen() # Đảm bảo hàm này tồn tại
        self.root.after(200, self._initialize_mqtt_after_save) # Đảm bảo hàm này tồn tại
        
    def show_connecting_to_server_screen(self): #  
        self.clear_frames() # Xóa các frame hiện tại

        # Hiển thị label thông báo
        ctk.CTkLabel(self.root, text="Đang lưu cấu hình và kết nối đến Server...", 
                     font=("Segoe UI", 20, "bold"), 
                     text_color="#333333").place(relx=0.5, rely=0.45, anchor="center")
        
        if self.loading_progress and self.loading_progress.winfo_exists():
            self.loading_progress.destroy()
            
        self.loading_progress = ctk.CTkProgressBar(self.root, width=350, height=18, corner_radius=8,
                                                 progress_color="#007AFF", mode="indeterminate")
        self.loading_progress.place(relx=0.5, rely=0.55, anchor="center")
        self.loading_progress.start() # Bắt đầu animation của progress bar

    def _initialize_mqtt_after_save(self): #  
        # Ngắt kết nối client MQTT cũ nếu có và xóa instance MQTTManager
        if self.mqtt_manager:
            if DEBUG: print("[MAIN DEBUG] Disconnecting old MQTTManager before re-initializing.")
            self.mqtt_manager.disconnect_client(explicit=True) 
        self.mqtt_manager = None # Đặt lại để initialize_mqtt tạo mới
        
        self.token = None # Xóa token cũ trong App instance, MQTTManager mới sẽ tự lấy
        
        # Gọi hàm initialize_mqtt, hàm này sẽ tạo MQTTManager mới và cố gắng kết nối
        # với cấu hình đã được cập nhật trong self.mqtt_config
        if DEBUG: print("[MAIN DEBUG] Attempting to initialize new MQTT connection after saving settings.")
        self.initialize_mqtt() # Đảm bảo hàm này tồn tại và đúng
        
        # Sau một khoảng thời gian, kiểm tra và quay về màn hình chính
        # Thời gian này cho phép MQTTManager thử kết nối lần đầu
        # Đảm bảo hàm return_to_main_menu_screen_after_config tồn tại
        self.root.after(2000, self.return_to_main_menu_screen_after_config) 

    def show_background(self):
        if self.bg_photo:
            if self.bg_label and self.bg_label.winfo_exists(): self.bg_label.destroy()
            self.bg_label = ctk.CTkLabel(self.root, image=self.bg_photo, text="")
            self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            self.bg_label.lower()

    def load_device_auth_config(self):
        config_path = os.path.join(script_dir, DEVICE_AUTH_CONFIG_FILE)
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f: loaded_config = json.load(f)
                if "FaceSecurityLevel" in loaded_config: self.auth_config["FaceSecurityLevel"] = loaded_config["FaceSecurityLevel"]
                if "BioAuthType" in loaded_config and isinstance(loaded_config["BioAuthType"], dict):
                    if not isinstance(self.auth_config.get("BioAuthType"), dict): self.auth_config["BioAuthType"] = {}
                    for key, value in loaded_config["BioAuthType"].items():
                        if key in ["IsFace", "IsFinger", "IsIdCard", "IsIris", "FingerTime", "IdCardTime", "IrisTime", "Direction"]:
                            self.auth_config["BioAuthType"][key] = value
                if DEBUG: print(f"[MAIN INFO] Loaded device auth config: {self.auth_config}")
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Failed to load {DEVICE_AUTH_CONFIG_FILE}: {e}. Using defaults.")
                self.auth_config = {"FaceSecurityLevel": 1,"BioAuthType": {"IsFace": True, "IsFinger": True, "IsIdCard": True, "IsIris": False,"FingerTime": 30, "IdCardTime": 30, "IrisTime": 30, "Direction": "IN"}}
        else:
            if DEBUG: print(f"[MAIN INFO] {DEVICE_AUTH_CONFIG_FILE} not found. Using default auth config.")

    def save_device_auth_config(self):
        config_path = os.path.join(script_dir, DEVICE_AUTH_CONFIG_FILE)
        try:
            with open(config_path, "w") as f: json.dump(self.auth_config, f, indent=4)
            if DEBUG: print(f"[MAIN INFO] Saved device auth config to {DEVICE_AUTH_CONFIG_FILE}")
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Failed to save {DEVICE_AUTH_CONFIG_FILE}: {e}")

    def on_device_config_received_from_mqtt(self, new_config_payload):
        if DEBUG: print(f"[MAIN DEBUG] Received new device auth config from MQTT: {new_config_payload}")
        updated = False
        if "FaceSecurityLevel" in new_config_payload:
            if self.auth_config.get("FaceSecurityLevel") != new_config_payload["FaceSecurityLevel"]:
                self.auth_config["FaceSecurityLevel"] = new_config_payload["FaceSecurityLevel"]; updated = True
        if "BioAuthType" in new_config_payload and isinstance(new_config_payload["BioAuthType"], dict):
            if not isinstance(self.auth_config.get("BioAuthType"), dict): self.auth_config["BioAuthType"] = {}
            for key, value in new_config_payload["BioAuthType"].items():
                if key in ["IsFace", "IsFinger", "IsIdCard", "IsIris", "FingerTime", "IdCardTime", "IrisTime", "Direction"]:
                    if self.auth_config["BioAuthType"].get(key) != value:
                        self.auth_config["BioAuthType"][key] = value; updated = True
                elif DEBUG: print(f"[MAIN WARN] Received unknown key '{key}' in BioAuthType from server. Ignoring.")
        if updated:
            if DEBUG: print(f"[MAIN INFO] Device auth config updated: {self.auth_config}")
            self.save_device_auth_config()
            self.update_main_menu_button_states()
            return True
        else:
            if DEBUG: print("[MAIN INFO] Received device auth config, but no changes were made to current settings.")
            return True
    def return_to_main_menu_screen_after_config(self): 
        # Kiểm tra trạng thái kết nối MQTT trước khi quay về
        if self.mqtt_manager and self.mqtt_manager.is_actively_connected():
             if DEBUG: print("[MAIN INFO] MQTT connected successfully after config. Returning to main menu.")
        else:
             if DEBUG: print("[MAIN WARN] MQTT NOT connected after config. Returning to main menu anyway, will show warning.")
             messagebox.showwarning("Cảnh Báo Kết Nối", 
                                   "Không thể kết nối đến server MQTT với cấu hình mới.\n"
                                   "Thiết bị sẽ tiếp tục thử kết nối trong nền.\n"
                                   "Vui lòng kiểm tra lại cấu hình nếu vấn đề kéo dài, hoặc xem trạng thái kết nối ở góc màn hình.", 
                                   parent=self.root) # Parent là root window
        
        self.return_to_main_menu_screen() 
    def initialize_mqtt(self):
        if self.mqtt_config and not self.mqtt_manager:
            if DEBUG: print("[MAIN DEBUG] Initializing MQTT Manager with config:", self.mqtt_config)
            self.mqtt_manager = MQTTManager(mqtt_config=self.mqtt_config, mac=self.mac, fingerprint_sensor=self.fingerprint_sensor, rfid_sensor=self.rfid_sensor, door_handler=self.door_sensor_handler, debug=DEBUG)
            self.mqtt_manager.on_token_received = self.on_token_received_from_mqtt
            self.mqtt_manager.on_connection_status_change = self.update_connection_status_display
            self.mqtt_manager.on_device_config_received = self.on_device_config_received_from_mqtt
            if not self.mqtt_manager.connect_and_register():
                if DEBUG: print("[MAIN WARN] Initial MQTT connection/registration attempt did not start successfully (via MQTTManager).")
        elif self.mqtt_manager:
            if self.fingerprint_sensor and not self.mqtt_manager.fingerprint_sensor: self.mqtt_manager.set_fingerprint_sensor(self.fingerprint_sensor)
            if self.rfid_sensor and not self.mqtt_manager.rfid_sensor: self.mqtt_manager.set_rfid_sensor(self.rfid_sensor)
            if self.door_sensor_handler and not self.mqtt_manager.door: self.mqtt_manager.set_door_handler(self.door_sensor_handler)
            if not self.mqtt_manager.is_actively_connected() and not self.mqtt_manager.connecting:
                if DEBUG: print("[MAIN DEBUG] MQTT Manager exists but is not connected/connecting. Calling connect_and_register on it.")
                self.mqtt_manager.connect_and_register()
                
    def update_connection_status_display(self, is_connected): 
        if not self.connection_status_label or not self.connection_status_label.winfo_exists():
            if DEBUG: print("[MAIN WARN] connection_status_label not available for update.")
            return
        
       
        image_to_show = self.connected_image if is_connected else self.disconnected_image
        text_color = "#2ECC71" if is_connected else "#E74C3C" 
        status_text = "" if is_connected else ""
        
      
        def _update_ui():
            if self.connection_status_label and self.connection_status_label.winfo_exists():
                 self.connection_status_label.configure(image=image_to_show, 
                                                        text=status_text, 
                                                        text_color=text_color)
        
        if self.root and self.root.winfo_exists():
            self.root.after(0, _update_ui)
        elif DEBUG:
            print("[MAIN WARN] Root window not available for UI update in update_connection_status_display.")
            
    def return_to_main_menu_screen(self, event=None): #  
        if DEBUG: print("[MAIN DEBUG] Returning to main menu screen...")
        
        face.stop_face_recognition() 

        self.rfid_scan_active = False 
        if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
            self.current_rfid_scan_display_frame.destroy()
            self.current_rfid_scan_display_frame = None
            
        self.screen_history = [("main_menu", self.show_main_menu_screen, ())] 
        
        self.clear_frames(clear_face_ui=True, clear_rfid_ui=True) 
        self.root.update_idletasks() 
        self.show_main_menu_screen() 

    def show_main_menu_screen(self):
        self.reset_multi_factor_state()
        self.clear_frames(clear_face_ui=True, clear_rfid_ui=True)
        if self.frame_main_menu and self.frame_main_menu.winfo_exists(): self.frame_main_menu.destroy()
        self.frame_main_menu = ctk.CTkFrame(self.root, fg_color="transparent")
        self.frame_main_menu.place(relx=0.5, rely=0.5, anchor="center")
        self.face_button_container = ctk.CTkFrame(self.frame_main_menu, width=BUTTON_WIDTH, height=BUTTON_HEIGHT, fg_color=BG_COLOR, corner_radius=12, border_width=1, border_color="#D0D0D0")
        self.face_button_container.grid(row=0, column=0, padx=PAD_X, pady=PAD_Y); self.face_button_container.grid_propagate(False)
        self.face_button = ctk.CTkButton(self.face_button_container, image=self.face_icon_img, text="KHUÔN MẶT", font=("Segoe UI", 18, "bold"), text_color=BUTTON_FG, compound="top", fg_color="transparent", hover_color="#E8E8E8", command=lambda: self.initiate_mfa_session(start_method="FACE"), anchor="center")
        self.face_button.pack(expand=True, fill="both")
        self.fingerprint_button_container = ctk.CTkFrame(self.frame_main_menu, width=BUTTON_WIDTH, height=BUTTON_HEIGHT, fg_color=BG_COLOR, corner_radius=12, border_width=1, border_color="#D0D0D0")
        self.fingerprint_button_container.grid(row=0, column=1, padx=PAD_X, pady=PAD_Y); self.fingerprint_button_container.grid_propagate(False)
        self.fingerprint_button = ctk.CTkButton(self.fingerprint_button_container, image=self.fingerprint_icon_img, text="VÂN TAY", font=("Segoe UI", 18, "bold"), text_color=BUTTON_FG, compound="top", fg_color="transparent", hover_color="#E8E8E8", command=lambda: self.initiate_mfa_session(start_method="FINGER"), anchor="center")
        self.fingerprint_button.pack(expand=True, fill="both")
        self.rfid_button_container = ctk.CTkFrame(self.frame_main_menu, width=BUTTON_WIDTH, height=BUTTON_HEIGHT, fg_color=BG_COLOR, corner_radius=12, border_width=1, border_color="#D0D0D0")
        self.rfid_button_container.grid(row=0, column=2, padx=PAD_X, pady=PAD_Y); self.rfid_button_container.grid_propagate(False)
        self.rfid_button = ctk.CTkButton(self.rfid_button_container, image=self.rfid_icon_img, text="THẺ TỪ", font=("Segoe UI", 18, "bold"), text_color=BUTTON_FG, compound="top", fg_color="transparent", hover_color="#E8E8E8", command=lambda: self.initiate_mfa_session(start_method="IDCARD"), anchor="center")
        self.rfid_button.pack(expand=True, fill="both")
        self.update_main_menu_button_states()

    def update_main_menu_button_states(self):
        if not hasattr(self, 'frame_main_menu') or not self.frame_main_menu or not self.frame_main_menu.winfo_exists():
            return 

        bio_auth_type = self.auth_config.get("BioAuthType", {})
        is_face_enabled = bio_auth_type.get("IsFace", False)
        is_finger_enabled = bio_auth_type.get("IsFinger", False)
        is_idcard_enabled = bio_auth_type.get("IsIdCard", False)

        face_image_to_use = self.face_icon_img if is_face_enabled else self.face_icon_disable_img
        face_text_color = BUTTON_FG if is_face_enabled else BUTTON_DISABLED_FG
        
        fingerprint_image_to_use = self.fingerprint_icon_img if is_finger_enabled else self.fingerprint_icon_disable_img
        fingerprint_text_color = BUTTON_FG if is_finger_enabled else BUTTON_DISABLED_FG

        rfid_image_to_use = self.rfid_icon_img if is_idcard_enabled else self.rfid_icon_disable_img
        rfid_text_color = BUTTON_FG if is_idcard_enabled else BUTTON_DISABLED_FG

        if hasattr(self, 'face_button') and self.face_button.winfo_exists():
            actual_face_image = face_image_to_use if face_image_to_use else self.face_icon_img 
            self.face_button.configure(
                state="normal" if is_face_enabled else "disabled",
                text_color=face_text_color,
                image=actual_face_image
            )

        if hasattr(self, 'fingerprint_button') and self.fingerprint_button.winfo_exists():
            actual_fingerprint_image = fingerprint_image_to_use if fingerprint_image_to_use else self.fingerprint_icon_img
            self.fingerprint_button.configure(
                state="normal" if is_finger_enabled else "disabled",
                text_color=fingerprint_text_color,
                image=actual_fingerprint_image
            )

        if hasattr(self, 'rfid_button') and self.rfid_button.winfo_exists():
            actual_rfid_image = rfid_image_to_use if rfid_image_to_use else self.rfid_icon_img
            self.rfid_button.configure(
                state="normal" if is_idcard_enabled else "disabled",
                text_color=rfid_text_color,
                image=actual_rfid_image
            )
        
        if DEBUG: 
            print(f"[MAIN UI] Main menu buttons updated: "
                  f"Face(enabled={is_face_enabled}, img={'normal' if is_face_enabled else 'disable'}), "
                  f"Finger(enabled={is_finger_enabled}, img={'normal' if is_finger_enabled else 'disable'}), "
                  f"Card(enabled={is_idcard_enabled}, img={'normal' if is_idcard_enabled else 'disable'})")
            
    def reset_multi_factor_state(self):
        if DEBUG: print("[MFA DEBUG] Resetting MFA state.")
        if self.multi_factor_auth_state.get("timeout_timer_id"):
            try: self.root.after_cancel(self.multi_factor_auth_state["timeout_timer_id"])
            except: pass
        if self.multi_factor_auth_state.get("prompt_frame") and self.multi_factor_auth_state["prompt_frame"].winfo_exists():
            self.multi_factor_auth_state["prompt_frame"].destroy()
        self.multi_factor_auth_state = {"active": False, "current_step_succeeded": None, "required_level": 0, "auth_data_collected": {}, "timeout_timer_id": None, "prompt_frame": None}
        face.stop_face_recognition()

    def initiate_mfa_session(self, start_method="FACE"):
        self.reset_multi_factor_state()
        self.multi_factor_auth_state["active"] = True
        self.multi_factor_auth_state["required_level"] = self.auth_config.get("FaceSecurityLevel", 1)
        bio_config = self.auth_config.get("BioAuthType", {})
        if DEBUG: print(f"[MFA INFO] Initiating MFA session. Required Level: {self.multi_factor_auth_state['required_level']}, Started with: {start_method}")
        first_step_to_try = None
        if start_method == "FACE" and bio_config.get("IsFace"): first_step_to_try = "START_FACE"
        elif start_method == "FINGER" and bio_config.get("IsFinger"): first_step_to_try = "START_FINGER"
        elif start_method == "IDCARD" and bio_config.get("IsIdCard"): first_step_to_try = "START_IDCARD"

        if self.multi_factor_auth_state["required_level"] > 1:
            if bio_config.get("IsFace"): first_step_to_try = "START_FACE"
            elif bio_config.get("IsFinger"): first_step_to_try = "START_FINGER"
            elif bio_config.get("IsIdCard"): first_step_to_try = "START_IDCARD"
            else: first_step_to_try = None
        
        if not first_step_to_try:
            if bio_config.get("IsFace"): first_step_to_try = "START_FACE"
            elif bio_config.get("IsFinger"): first_step_to_try = "START_FINGER"
            elif bio_config.get("IsIdCard"): first_step_to_try = "START_IDCARD"
            else:
                messagebox.showerror("Lỗi Cấu Hình", "Không có phương thức xác thực nào được kích hoạt.", parent=self.root)
                self.reset_multi_factor_state(); self.return_to_main_menu_screen(); return
        self.multi_factor_auth_state["current_step_succeeded"] = first_step_to_try
        self.proceed_to_next_auth_step()

    def proceed_to_next_auth_step(self):
        if not self.multi_factor_auth_state["active"]: return
        level = self.multi_factor_auth_state["required_level"]
        last_success = self.multi_factor_auth_state["current_step_succeeded"]
        bio_config = self.auth_config.get("BioAuthType", {})
        if DEBUG: print(f"[MFA DEBUG] Proceeding. Level: {level}, Last Success: {last_success}")

        if self.multi_factor_auth_state.get("timeout_timer_id"):
            try: self.root.after_cancel(self.multi_factor_auth_state["timeout_timer_id"])
            except: pass; self.multi_factor_auth_state["timeout_timer_id"] = None
        if self.multi_factor_auth_state.get("prompt_frame") and self.multi_factor_auth_state["prompt_frame"].winfo_exists():
            self.multi_factor_auth_state["prompt_frame"].destroy(); self.multi_factor_auth_state["prompt_frame"] = None
        
        next_method = None; next_method_name_display = ""; next_image = ""; next_timeout = 0; next_scan_func = None

        if last_success == "START_FACE" or (last_success is None and bio_config.get("IsFace")):
            next_method = "FACE"
        elif last_success == "FACE_OK" and level >= 2 and bio_config.get("IsFinger"):
            next_method = "FINGER"; next_method_name_display = "VÂN TAY"; next_image="images/fingerprint.png"; next_timeout=bio_config.get("FingerTime",30)
            next_scan_func = lambda: self.start_fingerprint_scan_flow(_internal_mfa_step=True)
        elif last_success == "START_FINGER" and bio_config.get("IsFinger") and (level == 1 or not bio_config.get("IsFace")):
            next_method = "FINGER"
        elif (last_success == "FINGER_OK" or (last_success == "FACE_OK" and not bio_config.get("IsFinger"))) and \
             level == 3 and bio_config.get("IsIdCard"):
            next_method = "IDCARD"; next_method_name_display = "THẺ TỪ"; next_image="images/rfid.png"; next_timeout=bio_config.get("IdCardTime",30)
            next_scan_func = lambda: self.start_rfid_scan_flow(_internal_mfa_step=True)
        elif last_success == "START_IDCARD" and bio_config.get("IsIdCard") and (level == 1 or (not bio_config.get("IsFace") and not bio_config.get("IsFinger"))):
            next_method = "IDCARD"
        
        if next_method == "FACE":
            if DEBUG: print("[MFA DEBUG] Starting/Resuming FACE authentication step.")
            self.start_face_recognition_flow(_internal_mfa_step=True); return
        elif next_method == "FINGER":
            if DEBUG: print("[MFA DEBUG] Proceeding to FINGER authentication step.")
            self.multi_factor_auth_state["prompt_frame"] = self.display_mfa_prompt_and_wait(next_method_name_display, "Xác thực Khuôn mặt thành công.\nVUI LÒNG ĐẶT VÂN TAY", next_image, next_timeout, next_scan_func); return
        elif next_method == "IDCARD":
            if DEBUG: print("[MFA DEBUG] Proceeding to IDCARD authentication step.")
            self.multi_factor_auth_state["prompt_frame"] = self.display_mfa_prompt_and_wait(next_method_name_display, "Xác thực Vân tay thành công.\nVUI LÒNG QUẸT THẺ TỪ", next_image, next_timeout, next_scan_func); return

        all_steps_done = False
        if level == 1 and last_success in ["FACE_OK", "FINGER_OK", "IDCARD_OK"]: all_steps_done = True
        elif level == 2 and (last_success == "FINGER_OK" or (last_success == "FACE_OK" and not bio_config.get("IsFinger"))): all_steps_done = True
        elif level == 3 and (last_success == "IDCARD_OK" or (last_success == "FINGER_OK" and not bio_config.get("IsIdCard"))): all_steps_done = True
        
        if all_steps_done:
            if DEBUG: print(f"[MFA INFO] All steps for Level {level} completed based on last_success: {last_success}.")
            self.complete_mfa_session(success=True)
        else:
            if DEBUG: print(f"[MFA WARN] No next step determined or condition not met. Level: {level}, LastSuccess: {last_success}.")
            self.complete_mfa_session(success=False, reason="Không thể xác định bước xác thực tiếp theo.")

    def display_mfa_prompt_and_wait(self, next_method_name, prompt_message, next_method_image_path, timeout_seconds, scan_function):
        self.clear_frames(keep_background=True, clear_face_ui=True, clear_rfid_ui=True)
        prompt_frame = ctk.CTkFrame(self.root, fg_color="white", corner_radius=10, width=700, height=450)
        prompt_frame.place(relx=0.5, rely=0.5, anchor="center")
        self.multi_factor_auth_state["prompt_frame"] = prompt_frame
        ctk.CTkLabel(prompt_frame, text=prompt_message, font=("Segoe UI", 22, "bold"), wraplength=650, justify="center").pack(pady=(30,15))
        img = load_image(next_method_image_path, (180,180))
        if img: ctk.CTkLabel(prompt_frame, image=img, text="").pack(pady=10)
        self.mfa_countdown_label = ctk.CTkLabel(prompt_frame, text=f"Vui lòng thực hiện trong: {timeout_seconds} giây", font=("Segoe UI", 20))
        self.mfa_countdown_label.pack(pady=15)
        ctk.CTkButton(prompt_frame, text="HỦY BỎ XÁC THỰC", width=200, height=45, fg_color="#E74C3C", hover_color="#C0392B", command=lambda: self.complete_mfa_session(success=False, reason="Người dùng hủy bỏ")).pack(pady=(20,30))
        # cần delay 2s để người dùng kịp đọc thông tin
        self.root.after(2000, lambda: self._start_mfa_countdown(timeout_seconds, next_method_name, scan_function))
        return prompt_frame

    def _start_mfa_countdown(self, remaining_time, current_method_name_for_timeout_msg, scan_function_to_call):
        if not self.multi_factor_auth_state["active"] or not self.multi_factor_auth_state.get("prompt_frame") or not self.multi_factor_auth_state["prompt_frame"].winfo_exists():
            if DEBUG: print("[MFA COUNTDOWN] Countdown stopped: MFA not active or prompt frame destroyed.")
            return
        
        timeout_key_map = {"VÂN TAY": "FingerTime", "THẺ TỪ": "IdCardTime"}
        original_timeout_key = timeout_key_map.get(current_method_name_for_timeout_msg, "FingerTime")
        original_timeout = self.auth_config.get("BioAuthType",{}).get(original_timeout_key, 30)

        if scan_function_to_call and remaining_time == original_timeout :
             if DEBUG: print(f"[MFA COUNTDOWN] Initial call for {current_method_name_for_timeout_msg}, calling scan function.")
             self.root.after(100, scan_function_to_call)

        if remaining_time >= 0:
            if hasattr(self, 'mfa_countdown_label') and self.mfa_countdown_label.winfo_exists():
                 self.mfa_countdown_label.configure(text=f"Vui lòng thực hiện trong: {remaining_time} giây")
            self.multi_factor_auth_state["timeout_timer_id"] = self.root.after(1000, lambda t=remaining_time - 1: self._start_mfa_countdown(t, current_method_name_for_timeout_msg, scan_function_to_call))
        else:
            if DEBUG: print(f"[MFA TIMEOUT] Timeout waiting for {current_method_name_for_timeout_msg}.")
            self.complete_mfa_session(success=False, reason=f"Quá thời gian chờ xác thực {current_method_name_for_timeout_msg}")

    def complete_mfa_session(self, success, reason=""):
        if not self.multi_factor_auth_state["active"]:
            if DEBUG: print("[MFA DEBUG] complete_mfa_session called but MFA not active.")
            return
        if DEBUG: print(f"[MFA COMPLETE] Success: {success}, Reason: {reason}, Collected Data: {self.multi_factor_auth_state['auth_data_collected']}")
        if self.multi_factor_auth_state.get("timeout_timer_id"):
            try: self.root.after_cancel(self.multi_factor_auth_state["timeout_timer_id"])
            except: pass
        if self.multi_factor_auth_state.get("prompt_frame") and self.multi_factor_auth_state["prompt_frame"].winfo_exists():
            self.multi_factor_auth_state["prompt_frame"].destroy()
        collected_data = self.multi_factor_auth_state["auth_data_collected"]
        final_bio_id = None; final_id_number = None; final_person_name = "Người dùng"
        if "face_bio_id" in collected_data: final_bio_id = collected_data["face_bio_id"]; final_id_number = collected_data.get("face_id_number"); final_person_name = collected_data.get("face_person_name", final_person_name)
        elif "finger_bio_id" in collected_data: final_bio_id = collected_data["finger_bio_id"]; final_id_number = collected_data.get("finger_id_number"); final_person_name = collected_data.get("finger_person_name", final_person_name)
        elif "rfid_bio_id" in collected_data: final_bio_id = collected_data["rfid_bio_id"]; final_id_number = collected_data.get("rfid_id_number"); final_person_name = collected_data.get("rfid_person_name", final_person_name)

        if success:
            self.trigger_door_open(); self.beep_buzzer()
            methods_used = []
            if "face_bio_id" in collected_data: methods_used.append("FACE")
            if "finger_bio_id" in collected_data: methods_used.append("FINGER")
            if "rfid_bio_id" in collected_data: methods_used.append("IDCARD")
            auth_method_str = "+".join(methods_used) or "MULTIFACTOR_UNKNOWN"
            if self.mqtt_manager and self.mqtt_manager.is_actively_connected():
                self.mqtt_manager.send_recognition_event(bio_id=final_bio_id, id_number=final_id_number, auth_method=auth_method_str, auth_data=f"Level {self.multi_factor_auth_state['required_level']} Success", status="SUCCESS", face_image_b64=collected_data.get("face_image_b64"), finger_image_b64=collected_data.get("finger_image_b64"))
            self.show_authentication_result_screen(success=True, message_main=f"XIN CHÀO, {final_person_name.upper()}!", message_sub=f"XÁC THỰC ĐA YẾU TỐ THÀNH CÔNG", user_image_ctk=get_ctk_image_from_db(final_bio_id, size=(180,180)) if final_bio_id else load_image("images/success_general.png", (150,150)))
        else:
            self.show_authentication_result_screen(success=False, message_main="XÁC THỰC THẤT BẠI", message_sub=reason or "CẦN HOÀN THÀNH ĐỦ CÁC PHƯƠNG THỨC", user_image_ctk=load_image("images/mfa_fail_general.png", (150,150)))
        self.reset_multi_factor_state()
        self.root.after(2000, self.return_to_main_menu_screen)

    def start_face_recognition_flow(self, _internal_mfa_step=False):
        if not _internal_mfa_step and self.multi_factor_auth_state["active"]:
             if DEBUG: print("[MFA WARN] Face flow called while MFA active. Ignoring."); return
        bio_auth_type = self.auth_config.get("BioAuthType", {})
        if not bio_auth_type.get("IsFace", False):
            if not _internal_mfa_step: messagebox.showwarning("Tính năng bị tắt", "Xác thực bằng khuôn mặt hiện không được kích hoạt.", parent=self.root)
            else:
                 if DEBUG: print("[MFA ERROR] Face step required by MFA but IsFace is false.")
                 self.complete_mfa_session(success=False, reason="Cấu hình khuôn mặt không hợp lệ cho MFA")
            return
        self.clear_frames(clear_face_ui=False, clear_rfid_ui=True)
        if DEBUG: print(f"[MAIN DEBUG] Attempting to load active FACE vectors for device MAC: {self.mac}")
        loaded_count = face.load_active_vectors_from_db(self.mac)
        if not face.face_db:
            messagebox.showinfo("Không Tìm Thấy Dữ Liệu Khuôn Mặt", f"Không có dữ liệu khuôn mặt hợp lệ nào được tìm thấy cho thiết bị này.\nVui lòng đồng bộ dữ liệu từ Server.", parent=self.root)
            if self.multi_factor_auth_state["active"]: self.complete_mfa_session(success=False, reason="Không có dữ liệu khuôn mặt")
            else: self.root.after(100, self.return_to_main_menu_screen)
            return
        if not self.face_ui_container or not self.face_ui_container.winfo_exists():
            self.face_ui_container = ctk.CTkFrame(self.root, fg_color="transparent"); self.face_ui_container.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.75, relheight=0.75)
            self.face_image_label = ctk.CTkLabel(self.face_ui_container, text="", fg_color="black", width=400, height=300); self.face_image_label.pack(pady=(10,5), anchor="n")
            self.face_name_label = ctk.CTkLabel(self.face_ui_container, text="", font=("Segoe UI", 28, "bold"), text_color="#0056B3", wraplength=800); self.face_name_label.pack(pady=(5,0), anchor="n", fill="x")
            self.face_info_label = ctk.CTkLabel(self.face_ui_container, text="", font=("Segoe UI", 20), text_color="#4A4A4A", wraplength=800); self.face_info_label.pack(pady=(0,10), anchor="n", fill="x")
        self.face_image_label.configure(text="ĐANG NHẬN DIỆN", image=None, font=("Segoe UI", 18, "italic"), text_color="white", width=400, height=300)
        self.face_name_label.configure(text="VUI LÒNG NHÌN THẲNG VÀO CAMERA"); self.face_info_label.configure(text="")
        if DEBUG: print("[MAIN DEBUG] Starting face recognition process...")
        face.open_face_recognition(on_recognition=self.handle_face_auth_success, on_failure_callback=lambda reason="FACE_SCAN_FAIL": self.complete_mfa_session(success=False, reason=f"Nhận diện Khuôn mặt thất bại: {reason}") if self.multi_factor_auth_state["active"] else self.handle_face_auth_failure(reason), parent_label=self.face_image_label)

    def handle_face_auth_success(self, recognized_key, confidence_score, captured_frame_array):
        face.stop_face_recognition()
        if DEBUG: print(f"[AUTH DEBUG] Face Auth Success: Key='{recognized_key}', MFA Active: {self.multi_factor_auth_state['active']}")
        parts = recognized_key.split('_'); bio_id_from_face_key = parts[-1] if parts else None
        if not bio_id_from_face_key:
            if self.multi_factor_auth_state["active"]: self.complete_mfa_session(success=False, reason="Lỗi xử lý khóa khuôn mặt")
            else: self.handle_face_auth_failure("INVALID_KEY_FORMAT")
            return
        user_info = database.get_user_by_bio_type_and_template("FACE", bio_id_from_face_key, self.mac)
        is_valid_now = user_info and database.is_user_access_valid_now(get_from_row(user_info, 'bio_id'), self.mac)
        if self.multi_factor_auth_state["active"]:
            if is_valid_now:
                if DEBUG: print(f"[MFA DEBUG] Face step SUCCESS for {get_from_row(user_info, 'person_name')}")
                self.multi_factor_auth_state["current_step_succeeded"] = "FACE_OK"
                collected = self.multi_factor_auth_state["auth_data_collected"]
                collected["face_bio_id"] = get_from_row(user_info, 'bio_id'); collected["face_id_number"] = get_from_row(user_info, 'id_number'); collected["face_person_name"] = get_from_row(user_info, 'person_name')
                db_face_img = get_from_row(user_info, 'face_image')
                if db_face_img: collected["face_image_b64"] = db_face_img
                elif captured_frame_array is not None:
                    try: buffered = io.BytesIO(); Image.fromarray(captured_frame_array).save(buffered, format="JPEG", quality=85); collected["face_image_b64"] = base64.b64encode(buffered.getvalue()).decode('utf-8')
                    except: pass
                self.proceed_to_next_auth_step()
            else:
                reason = "Không tìm thấy người dùng" if not user_info else "Người dùng không hợp lệ/ngoài giờ"
                self.complete_mfa_session(success=False, reason=f"Xác thực Khuôn mặt: {reason}")
        else:
            final_display_image_ctk = None; db_face_image_b64 = None
            if user_info:
                actual_bio_id = get_from_row(user_info, 'bio_id'); person_name = get_from_row(user_info, 'person_name', 'Người dùng không xác định')
                id_number = get_from_row(user_info, 'id_number'); finger_image_b64_db = get_from_row(user_info, 'finger_image')
                db_face_image_b64 = get_from_row(user_info, 'face_image'); final_display_image_ctk = get_ctk_image_from_db(actual_bio_id, size=(300,250))
                if not is_valid_now:
                    if DEBUG: print(f"[MAIN WARN] Access DENIED for {person_name} (BioID: {actual_bio_id}) from face. Outside schedule.")
                    if self.face_name_label: self.face_name_label.configure(text=f"{person_name}", text_color="#E74C3C")
                    if self.face_info_label: self.face_info_label.configure(text="TRUY CẬP BỊ TỪ CHỐI (NGOÀI GIỜ)", text_color="#E74C3C")
                    if self.mqtt_manager and self.mqtt_manager.is_actively_connected(): self.mqtt_manager.send_recognition_event(bio_id=actual_bio_id, id_number=id_number, auth_method="FACE", auth_data=bio_id_from_face_key, status="DENIED_SCHEDULE", face_image_b64=db_face_image_b64, finger_image_b64=finger_image_b64_db)
                else:
                    if DEBUG: print(f"[MAIN SUCCESS] Face Access GRANTED for {person_name} (BioID: {actual_bio_id})")
                    if self.face_name_label: self.face_name_label.configure(text=f"XIN CHÀO, {person_name.upper()}!", text_color="#2ECC71")
                    if self.face_info_label: self.face_info_label.configure(text="XÁC THỰC THÀNH CÔNG", text_color="#2ECC71")
                    #self.trigger_door_open(); self.beep_buzzer()
                    final_face_image_b64_to_send_mqtt = db_face_image_b64
                    if not final_face_image_b64_to_send_mqtt and captured_frame_array is not None:
                        try: buffered = io.BytesIO(); Image.fromarray(captured_frame_array).save(buffered, format="JPEG", quality=85); final_face_image_b64_to_send_mqtt = base64.b64encode(buffered.getvalue()).decode('utf-8')
                        except Exception as e_b64_encode: 
                            if DEBUG: print(f"[MAIN ERROR] Failed to encode captured live frame to Base64 for MQTT: {e_b64_encode}")
                    if self.mqtt_manager and self.mqtt_manager.is_actively_connected(): self.mqtt_manager.send_recognition_event(bio_id=actual_bio_id, id_number=id_number, auth_method="FACE", auth_data=bio_id_from_face_key, status="SUCCESS", face_image_b64=final_face_image_b64_to_send_mqtt, finger_image_b64=finger_image_b64_db)
            else:
                if DEBUG: print(f"[MAIN WARN] Face key '{bio_id_from_face_key}' recognized, but user not found in DB or not active.")
                if self.face_name_label: self.face_name_label.configure(text="KHÔNG TÌM THẤY TRONG HỆ THỐNG", text_color="#E67E22")
                if self.face_info_label: self.face_info_label.configure(text="Người lạ", text_color="#E67E22")
                if self.mqtt_manager and self.mqtt_manager.is_actively_connected(): self.mqtt_manager.send_recognition_event(bio_id=None, id_number=None, auth_method="FACE", auth_data=bio_id_from_face_key, status="NOT_FOUND")
            if not final_display_image_ctk and captured_frame_array is not None:
                try: pil_img = Image.fromarray(captured_frame_array).resize((300,250), Image.Resampling.LANCZOS); final_display_image_ctk = CTkImage(light_image=pil_img, dark_image=pil_img, size=(300,250))
                except Exception as e_img_display: 
                    if DEBUG: print(f"[MAIN ERROR] Failed to process captured frame for display: {e_img_display}")
            if self.face_image_label:
                if final_display_image_ctk: self.face_image_label.configure(image=final_display_image_ctk, text="", width=300, height=250)
                else: self.face_image_label.configure(image=None, text=f"Ảnh không có sẵn", font=("Segoe UI", 16), text_color="grey", width=300, height=250)
            self.root.after(FACE_RECOGNITION_TIMEOUT_MS, self.return_to_main_menu_screen)

    def handle_face_auth_failure(self, reason="Unknown"):
        if DEBUG: print(f"[MAIN DEBUG] Face Authentication FAILED (non-MFA). Reason: {reason}")
        face.stop_face_recognition()
        if self.face_name_label and self.face_name_label.winfo_exists(): self.face_name_label.configure(text="XÁC THỰC KHUÔN MẶT THẤT BẠI", text_color="#E74C3C")
        if self.face_info_label and self.face_info_label.winfo_exists(): self.face_info_label.configure(text="Không thể nhận diện.\nVui lòng thử lại.", text_color="#E74C3C")
        if self.face_image_label and self.face_image_label.winfo_exists():
             fail_img = load_image("images/face_error.png", (300,250))
             if fail_img: self.face_image_label.configure(image=fail_img, text="", width=300, height=250)
             else: self.face_image_label.configure(text="Lỗi", image=None, width=300, height=250)
        self.root.after(1500, self.return_to_main_menu_screen)

    def start_fingerprint_scan_flow(self, _internal_mfa_step=False):
        if not _internal_mfa_step and self.multi_factor_auth_state["active"]:
             if DEBUG: print("[MFA WARN] Fingerprint flow called while MFA active. Ignoring."); return
        bio_auth_type = self.auth_config.get("BioAuthType", {})
        if not bio_auth_type.get("IsFinger", False):
            if not _internal_mfa_step: messagebox.showwarning("Tính năng bị tắt", "Xác thực bằng vân tay hiện không được kích hoạt.", parent=self.root)
            else:
                 if DEBUG: print("[MFA ERROR] Fingerprint step required by MFA but IsFinger is false.")
                 self.complete_mfa_session(success=False, reason="Cấu hình vân tay không hợp lệ cho MFA")
            return
        if not self.fingerprint_sensor:
             messagebox.showerror("Lỗi Cảm Biến Vân Tay", "Cảm biến vân tay chưa sẵn sàng hoặc bị lỗi.", parent=self.root)
             if self.multi_factor_auth_state["active"]: self.complete_mfa_session(success=False, reason="Lỗi cảm biến vân tay")
             else: self.return_to_main_menu_screen()
             return
        try:
            if not self.fingerprint_sensor.verifyPassword(): raise Exception("Sensor password verification failed")
        except Exception as e:
            messagebox.showerror("Lỗi Cảm Biến Vân Tay", f"Lỗi giao tiếp cảm biến: {str(e)[:100]}", parent=self.root)
            if self.multi_factor_auth_state["active"]: self.complete_mfa_session(success=False, reason=f"Lỗi cảm biến: {str(e)[:50]}")
            else: self.return_to_main_menu_screen()
            return
        if DEBUG: print("[MAIN DEBUG] Starting fingerprint scan prompt...")
        self.clear_frames(clear_face_ui=True, clear_rfid_ui=True)
        fp_ui_host_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        fp_ui_host_frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.75, relheight=0.75)
        fp_ui_host_frame._owner_module = 'fingerprint_ui'
        fingerprint.open_fingerprint_prompt(parent=fp_ui_host_frame, sensor=self.fingerprint_sensor, on_success_callback=self.handle_fingerprint_auth_success, on_failure_callback=lambda reason="FP_SCAN_FAIL": self.complete_mfa_session(success=False, reason=f"Xác thực Vân tay thất bại: {reason}") if self.multi_factor_auth_state["active"] else self.handle_fingerprint_auth_failure(reason), device_mac_address=self.mac)

    def handle_fingerprint_auth_success(self, user_info_from_module):
        for widget in self.root.winfo_children():
            if hasattr(widget, '_owner_module') and widget._owner_module == 'fingerprint_ui':
                if widget.winfo_exists(): widget.destroy(); break
        if DEBUG: print(f"[AUTH DEBUG] Fingerprint Auth Success. MFA Active: {self.multi_factor_auth_state['active']}")
        if user_info_from_module is None:
            err_msg = "Lỗi xử lý dữ liệu vân tay"
            if self.multi_factor_auth_state["active"]: self.complete_mfa_session(success=False, reason=err_msg)
            else: self.show_authentication_result_screen(success=False, message_main="LỖI DỮ LIỆU", message_sub=err_msg, user_image_ctk=load_image("images/fp_error.png", (150,150))); self.root.after(1500, self.return_to_main_menu_screen)
            return
        actual_bio_id = get_from_row(user_info_from_module, 'bio_id')
        if not actual_bio_id:
            err_msg = "Thiếu BioID từ dữ liệu vân tay"
            if self.multi_factor_auth_state["active"]: self.complete_mfa_session(success=False, reason=err_msg)
            else: self.handle_fingerprint_auth_failure("MISSING_BIO_ID")
            return
        
        if self.multi_factor_auth_state["active"]:
            if DEBUG: print(f"[MFA DEBUG] Fingerprint step SUCCESS for {get_from_row(user_info_from_module, 'person_name')}")
            self.multi_factor_auth_state["current_step_succeeded"] = "FINGER_OK"
            collected = self.multi_factor_auth_state["auth_data_collected"]
            collected["finger_bio_id"] = actual_bio_id
            collected["finger_id_number"] = get_from_row(user_info_from_module, 'id_number')
            collected["finger_person_name"] = get_from_row(user_info_from_module, 'person_name')
            collected["finger_image_b64"] = get_from_row(user_info_from_module, 'finger_image')
            if "id_number" not in collected: collected["id_number"] = collected["finger_id_number"]
            if "person_name" not in collected: collected["person_name"] = collected["finger_person_name"]
            self.proceed_to_next_auth_step()
        else:
            person_name = get_from_row(user_info_from_module, 'person_name', 'Người dùng không xác định')
            id_number = get_from_row(user_info_from_module, 'id_number')
            face_image_b64_db = get_from_row(user_info_from_module, 'face_image')
            finger_image_b64_db = get_from_row(user_info_from_module, 'finger_image')
            if DEBUG: print(f"[MAIN SUCCESS] Fingerprint Access GRANTED for {person_name} (BioID: {actual_bio_id})")
            #self.trigger_door_open(); self.beep_buzzer()
            if self.mqtt_manager and self.mqtt_manager.is_actively_connected(): self.mqtt_manager.send_recognition_event(bio_id=actual_bio_id, id_number=id_number, auth_method="FINGER", auth_data=actual_bio_id, status="SUCCESS", face_image_b64=face_image_b64_db, finger_image_b64=finger_image_b64_db)
            self.show_authentication_result_screen(success=True, message_main=f"XIN CHÀO, {person_name.upper()}!", message_sub="XÁC THỰC BẰNG VÂN TAY THÀNH CÔNG", user_image_ctk=get_ctk_image_from_db(actual_bio_id, size=(180,180)))
            self.root.after(1500, self.return_to_main_menu_screen)

    def handle_fingerprint_auth_failure(self, reason=""):
        for widget in self.root.winfo_children():
            if hasattr(widget, '_owner_module') and widget._owner_module == 'fingerprint_ui':
                if widget.winfo_exists(): widget.destroy(); break
        if DEBUG: print(f"[MAIN DEBUG] Fingerprint Authentication FAILED (non-MFA). Reason: {reason}")
        self.show_authentication_result_screen(success=False, message_main="XÁC THỰC VÂN TAY THẤT BẠI", message_sub=f"{reason}".strip().upper(), user_image_ctk=load_image("images/fp_error.png", (150,150)))
        self.root.after(1000, self.return_to_main_menu_screen)

    def start_rfid_scan_flow(self, _internal_mfa_step=False):
        if not _internal_mfa_step and self.multi_factor_auth_state["active"]:
             if DEBUG: print("[MFA WARN] RFID flow called while MFA active. Ignoring."); return
        bio_auth_type = self.auth_config.get("BioAuthType", {})
        if not bio_auth_type.get("IsIdCard", False):
            if not _internal_mfa_step: messagebox.showwarning("Tính năng bị tắt", "Xác thực bằng thẻ từ hiện không được kích hoạt.", parent=self.root)
            else:
                 if DEBUG: print("[MFA ERROR] IdCard step required by MFA but IsIdCard is false.")
                 self.complete_mfa_session(success=False, reason="Cấu hình thẻ từ không hợp lệ cho MFA")
            return
        if not self.rfid_sensor:
            messagebox.showerror("Lỗi Đầu Đọc RFID", "Đầu đọc thẻ RFID chưa sẵn sàng hoặc bị lỗi.", parent=self.root)
            if self.multi_factor_auth_state["active"]: self.complete_mfa_session(success=False, reason="Lỗi đầu đọc RFID")
            else: self.return_to_main_menu_screen()
            return
        try: self.rfid_sensor.SAM_configuration()
        except Exception as e:
            messagebox.showerror("Lỗi Đầu Đọc RFID", f"Lỗi giao tiếp đầu đọc RFID: {str(e)[:100]}", parent=self.root)
            if self.multi_factor_auth_state["active"]: self.complete_mfa_session(success=False, reason=f"Lỗi đầu đọc RFID: {str(e)[:50]}")
            else: self.return_to_main_menu_screen()
            return
        if DEBUG: print("[MAIN DEBUG] Starting RFID authentication scan flow...")
        self.clear_frames(clear_face_ui=True, clear_rfid_ui=False)
        if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
            self.current_rfid_scan_display_frame.destroy(); self.current_rfid_scan_display_frame = None
        self.rfid_scan_active = True
        self.current_rfid_scan_display_frame = rfid.start_rfid_authentication_scan(parent_ui_element=self.root, sensor_pn532=self.rfid_sensor, on_success_callback=self.handle_rfid_auth_success, on_failure_callback=lambda reason="RFID_SCAN_FAIL": self.complete_mfa_session(success=False, reason=f"Quét thẻ thất bại: {reason}") if self.multi_factor_auth_state["active"] else self.handle_rfid_auth_failure(reason))

    def handle_rfid_auth_success(self, uid_hex_from_card):
        if not self.rfid_scan_active and not self.multi_factor_auth_state["active"]:
            if DEBUG: print("[AUTH DEBUG] RFID success, but no scan active (MFA or single). Ignoring."); return
        if DEBUG: print(f"[AUTH DEBUG] RFID Auth Success: UID='{uid_hex_from_card}', MFA Active: {self.multi_factor_auth_state['active']}")
        self.beep_buzzer()
        current_time = time.time()
        if not self.multi_factor_auth_state["active"] and (current_time - self.last_rfid_auth_time < RFID_AUTH_COOLDOWN_S):
            if DEBUG: print(f"[MAIN DEBUG] RFID UID {uid_hex_from_card} scanned too soon (cooldown active). Ignoring.")
            if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists(): rfid.update_rfid_auth_ui(self.current_rfid_scan_display_frame, "Thẻ đã quét.\nVui lòng đợi giây lát...", image_path="rfid_scan.png", color="orange")
            return
        self.last_rfid_auth_time = current_time
        try:
          uid_base64 = base64.b64encode(uid_hex_from_card.encode("utf-8")).decode("utf-8")
        except ValueError:
           if DEBUG: print(f"[AUTH ERROR] UID '{uid_hex_from_card}' không hợp lệ. Không thể chuyển sang base64.")
           return
        print({uid_base64})
        user_info = database.get_user_by_bio_type_and_template("IDCARD", uid_base64, self.mac)
        is_valid_now = user_info and database.is_user_access_valid_now(get_from_row(user_info, 'bio_id'), self.mac)

        if self.multi_factor_auth_state["active"]:
            self.rfid_scan_active = False
            if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
                self.current_rfid_scan_display_frame.destroy(); self.current_rfid_scan_display_frame = None
            if is_valid_now:
                if DEBUG: print(f"[MFA DEBUG] IdCard step SUCCESS for {get_from_row(user_info, 'person_name')}")
                self.multi_factor_auth_state["current_step_succeeded"] = "IDCARD_OK"
                collected = self.multi_factor_auth_state["auth_data_collected"]
                collected["rfid_bio_id"] = get_from_row(user_info, 'bio_id'); collected["rfid_id_number"] = get_from_row(user_info, 'id_number'); collected["rfid_person_name"] = get_from_row(user_info, 'person_name')
                if "id_number" not in collected: collected["id_number"] = collected["rfid_id_number"]
                if "person_name" not in collected: collected["person_name"] = collected["rfid_person_name"]
                self.proceed_to_next_auth_step()
            else:
                reason = "Thẻ không tìm thấy" if not user_info else "Thẻ không hợp lệ/ngoài giờ"
                self.complete_mfa_session(success=False, reason=f"Xác thực Thẻ từ: {reason}")
        else:
            self.rfid_scan_active = False
            if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
                self.current_rfid_scan_display_frame.destroy(); self.current_rfid_scan_display_frame = None
            if user_info:
                actual_bio_id = get_from_row(user_info, 'bio_id'); person_name = get_from_row(user_info, 'person_name', 'Người dùng không xác định')
                id_number = get_from_row(user_info, 'id_number'); face_image_b64_db = get_from_row(user_info, 'face_image'); finger_image_b64_db = get_from_row(user_info, 'finger_image')
                if not is_valid_now:
                    if DEBUG: print(f"[MAIN WARN] Access DENIED for {person_name} (UID: {uid_hex_from_card}). Outside valid schedule.")
                    self.show_authentication_result_screen(success=False, message_main="TRUY CẬP BỊ TỪ CHỐI", message_sub=f"{person_name}\nNGOÀI GIỜ HOẶC HẾT HẠN TRUY CẬP", user_image_ctk=get_ctk_image_from_db(actual_bio_id, size=(180,180)))
                    if self.mqtt_manager and self.mqtt_manager.is_actively_connected(): self.mqtt_manager.send_recognition_event(bio_id=actual_bio_id, id_number=id_number, auth_method="IDCARD", auth_data=uid_base64, status="DENIED_SCHEDULE", face_image_b64=face_image_b64_db, finger_image_b64=finger_image_b64_db)
                else:
                    if DEBUG: print(f"[MAIN SUCCESS] RFID Access GRANTED for {person_name} (BioID: {actual_bio_id})")
                    #self.trigger_door_open()
                    if self.mqtt_manager and self.mqtt_manager.is_actively_connected(): self.mqtt_manager.send_recognition_event(bio_id=actual_bio_id, id_number=id_number, auth_method="IDCARD", auth_data=uid_base64, status="SUCCESS", face_image_b64=face_image_b64_db, finger_image_b64=finger_image_b64_db)
                    self.show_authentication_result_screen(success=True, message_main=f"XIN CHÀO, {person_name.upper()}!", message_sub="XÁC THỰC BẰNG THẺ TỪ THÀNH CÔNG", user_image_ctk=get_ctk_image_from_db(actual_bio_id, size=(180,180)))
            else:
                if DEBUG: print(f"[MAIN WARN] RFID UID {uid_hex_from_card} not found in DB or not active for this device.")
                self.show_authentication_result_screen(success=False, message_main="THẺ KHÔNG HỢP LỆ", message_sub=f"THẺ CHƯA ĐƯỢC ĐĂNG KÝ", user_image_ctk=load_image("images/rfid_unknown.png", (180,180)))
                if self.mqtt_manager and self.mqtt_manager.is_actively_connected(): self.mqtt_manager.send_recognition_event(bio_id=None, id_number=None, auth_method="IDCARD", auth_data=uid_base64, status="NOT_FOUND")
            self.root.after(1500, self.return_to_main_menu_screen)

    def handle_rfid_auth_failure(self, reason=""):
        if not self.rfid_scan_active and not self.multi_factor_auth_state["active"]:
            if DEBUG: print("[AUTH DEBUG] RFID failure, but no scan active. Ignoring."); return
        if DEBUG: print(f"[MAIN DEBUG] RFID Authentication FAILED (non-MFA). Reason: {reason}")
        self.rfid_scan_active = False
        if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
            self.current_rfid_scan_display_frame.destroy(); self.current_rfid_scan_display_frame = None
        if reason not in ["UI closed", "Sensor unavailable", "Không có thẻ", "Người dùng hủy"]:
            self.show_authentication_result_screen(success=False, message_main="QUÉT THẺ THẤT BẠI", message_sub=str(reason).upper(), user_image_ctk=load_image("images/rfid_error.png", (150,150)))
            self.root.after(1500, self.return_to_main_menu_screen)
        else:
            self.root.after(100, self.return_to_main_menu_screen)
            
    def show_authentication_result_screen(self, success, message_main, message_sub, user_image_ctk=None):
        self.clear_frames(clear_face_ui=True, clear_rfid_ui=True)
        if self.frame_result_display and self.frame_result_display.winfo_exists(): self.frame_result_display.destroy()
        self.frame_result_display = ctk.CTkFrame(self.root, fg_color="transparent")
        self.frame_result_display.place(relx=0.5, rely=0.5, anchor="center")
        if user_image_ctk:
            img_label = ctk.CTkLabel(self.frame_result_display, image=user_image_ctk, text=""); img_label.pack(pady=(0, 25))
        main_text_color = SUCCESS_COLOR if success else ERROR_COLOR
        ctk.CTkLabel(self.frame_result_display, text=message_main, font=("Segoe UI", 30, "bold"), text_color=main_text_color).pack(pady=(0, 8))
        sub_text_color = "#555555" if success else "#777777"
        ctk.CTkLabel(self.frame_result_display, text=message_sub, font=("Segoe UI", 25), text_color=sub_text_color, wraplength=550, justify="center").pack()

    def _sos_button_state_changed_callback(self, channel):
        if not GPIO_AVAILABLE: return
        time.sleep(0.02)
        current_state = GPIO.input(SOS_BUTTON_PIN)
        if current_state != self.last_sos_button_state:
            self.last_sos_button_state = current_state
            if current_state == GPIO.LOW:
                if DEBUG: print("[MAIN INFO] SOS Button State Changed: PRESSED (LOW)")
                GPIO.output(BUZZER_PIN, GPIO.HIGH)
                if self.mqtt_manager and self.mqtt_manager.is_actively_connected(): self.mqtt_manager.send_sos_alert()
            else:
                if DEBUG: print("[MAIN INFO] SOS Button State Changed: RELEASED (HIGH)")
                GPIO.output(BUZZER_PIN, GPIO.LOW)

    def _open_button_state_changed_callback(self, channel):
        if not GPIO_AVAILABLE: return
        time.sleep(0.02)
        current_state = GPIO.input(OPEN_BUTTON_PIN)
        if current_state != self.last_open_button_state:
            self.last_open_button_state = current_state
            if current_state == GPIO.LOW:
                if DEBUG: print("[MAIN INFO] Open Door Button State Changed: PRESSED (LOW)")
                self.open_button_press_time = time.time()
                self.trigger_door_open(duration_ms=DOOR_OPEN_DURATION_MS); self.beep_buzzer(duration_ms=200)

    def request_manual_sync(self):
        if self.mqtt_manager and self.mqtt_manager.is_actively_connected():
             if DEBUG: print("[MAIN INFO] Manual data sync requested by user.")
             self.mqtt_manager.send_device_sync_request()
             messagebox.showinfo("Yêu Cầu Đồng Bộ", "Đã gửi yêu cầu đồng bộ dữ liệu sinh trắc học đến Server.", parent=self.root)
        else:
             messagebox.showwarning("Lỗi Kết Nối MQTT", "Chưa kết nối MQTT hoặc kết nối không ổn định.\nKhông thể gửi yêu cầu đồng bộ.", parent=self.root)

    def cleanup(self):
        if DEBUG: print("[MAIN INFO] Application cleanup process started...")
        self.reset_multi_factor_state()
        face.stop_face_recognition()
        self.rfid_scan_active = False
        if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists(): self.current_rfid_scan_display_frame.destroy()
        if self.open_door_timer: 
            try: self.root.after_cancel(self.open_door_timer)
            except: pass; self.open_door_timer = None
        if self.buzzer_timer_id: 
            try: self.root.after_cancel(self.buzzer_timer_id)
            except: pass; self.buzzer_timer_id = None
        if self.mqtt_manager:
             if DEBUG: print("[MAIN INFO] Disconnecting MQTT client (explicitly from App cleanup)...")
             self.mqtt_manager.disconnect_client(explicit=True)
        if self.door_sensor_handler:
             if DEBUG: print("[MAIN INFO] Cleaning up Door Handler GPIO...")
             self.door_sensor_handler.cleanup()
        if GPIO_AVAILABLE:
            if DEBUG: print("[MAIN INFO] Performing general GPIO cleanup...")
            GPIO.cleanup()
        if DEBUG: print("[MAIN INFO] Exiting application.")
        if self.root and self.root.winfo_exists(): self.root.destroy()

if __name__ == "__main__":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass
    ctk.set_appearance_mode("Light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.geometry("1024x600")
    root.title("Hệ Thống Kiểm Soát Ra Vào Thông Minh - Navis SmartLock")
    app = App(root)
    root.mainloop()