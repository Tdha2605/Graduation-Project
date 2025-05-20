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
BUTTON_FONT = ("Segoe UI", 24)
BUTTON_WIDTH = 350
BUTTON_HEIGHT = 350
PAD_X = 15
PAD_Y = 15
CONFIG_FILE = "mqtt_config.json"
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
            
            ctk_size = pil_image.size
            if isinstance(size, tuple) and len(size) == 2:
                ctk_size = size
            elif isinstance(size, int):
                 pil_image.thumbnail((size,size), Image.Resampling.LANCZOS)
                 ctk_size = pil_image.size

            ctk_img = CTkImage(light_image=pil_image, dark_image=pil_image, size=ctk_size)
            return ctk_img
        except base64.binascii.Error:
            if DEBUG: print(f"[MAIN ERROR] Base64 decode error for user_id {user_id}.")
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Image processing error for user_id {user_id}: {e}")
    return None

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
            root.quit()
            return

        self.token = None
        self.mqtt_manager = None
        self.mqtt_config = {}
        self.screen_history = []
        
        self.fingerprint_sensor = None
        self.rfid_sensor = None

        self.connection_status_label = None
        self.frame_mqtt_config = None
        self.frame_main_menu = None
        self.bg_label = None
        self.loading_progress = None
        
        self.face_ui_container = None
        self.face_info_label = None
        self.face_image_label = None
        self.face_name_label = None
        
        self.admin_user_entry = None
        self.admin_pass_entry = None
        self.server_entry = None
        self.mqtt_port_entry = None
        self.room_entry = None

        self.last_sos_button_state = GPIO.HIGH if GPIO_AVAILABLE else None
        self.last_open_button_state = GPIO.HIGH if GPIO_AVAILABLE else None
        self.open_button_press_time = None
        self.open_door_timer = None

        self.current_rfid_scan_display_frame = None
        self.last_rfid_auth_time = 0
        self.rfid_scan_active = False

        self.connected_image = load_image("images/connected.jpg", (50, 50))
        self.disconnected_image = load_image("images/disconnected.jpg", (50, 50))
        self.bg_photo = load_image("images/background.jpeg", (1024, 600))
        self.face_icon_img = load_image("images/face.png", (BUTTON_WIDTH-80, BUTTON_HEIGHT-100))
        self.fingerprint_icon_img = load_image("images/fingerprint.png", (BUTTON_WIDTH-80, BUTTON_HEIGHT-100))
        self.rfid_icon_img = load_image("images/rfid.png", (BUTTON_WIDTH-80, BUTTON_HEIGHT-100))
        self.sync_icon_img = load_image("images/sync.png", (40, 40))

        self.root.configure(fg_color=BG_COLOR)
        self.show_background()
        
        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="Chưa kết nối", font=("Segoe UI", 11), text_color="red", compound="left")
        self.connection_status_label.place(relx=0.04, rely=0.95, anchor="sw")
        
        self.create_config_button()
        
        self.sync_button = ctk.CTkButton(self.root, image=self.sync_icon_img, text="", width=40, height=40, 
                                         fg_color="transparent", hover_color="#E0E0E0", command=self.request_manual_sync)
        self.sync_button.place(relx=0.04, rely=0.02, anchor="nw")
        
        self.initialize_fingerprint_sensor()
        self.initialize_rfid_sensor()

        if GPIO_AVAILABLE:
            self.setup_gpio_components()
        
        self.door_sensor_handler = None
        if GPIO_AVAILABLE:
            try:
                self.door_sensor_handler = Door(
                    sensor_pin=DOOR_SENSOR_PIN,
                    relay_pin=DOOR_RELAY_PIN,
                    relay_active_high=False,
                    mqtt_publish_callback=self.door_state_changed_mqtt_publish
                )
                if DEBUG: print("[MAIN INFO] Door handler initialized.")
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Error initializing Door Handler: {e}.")
        else:
            if DEBUG: print("[MAIN WARN] GPIO not available, Door handler not initialized.")

        config_path = os.path.join(script_dir, CONFIG_FILE)
        proceed_to_main_menu = False

        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    loaded_config = json.load(f)
                
                if loaded_config.get("server") and loaded_config.get("mqtt_port") and loaded_config.get("room"):
                    self.mqtt_config = loaded_config
                    self.token = self.mqtt_config.get("token") # Sẽ là None nếu chưa có
                    
                    if DEBUG: print("[MAIN INFO] Config file found and seems valid. Initializing MQTT...")
                    self.initialize_mqtt() 
                    proceed_to_main_menu = True # Sẽ đi đến menu chính, MQTT sẽ cố kết nối
                else:
                    if DEBUG: print(f"[MAIN ERROR] Config file {config_path} is incomplete. Deleting and reconfiguring.")
                    os.remove(config_path)
                    self.mqtt_config = {}
                    self.token = None
            except json.JSONDecodeError:
                if DEBUG: print(f"[MAIN ERROR] Error reading {config_path}. Invalid JSON. Deleting and reconfiguring.")
                if os.path.exists(config_path): os.remove(config_path)
                self.mqtt_config = {}
                self.token = None
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] An error occurred loading MQTT config: {e}. Deleting and reconfiguring.")
                if os.path.exists(config_path): os.remove(config_path)
                self.mqtt_config = {}
                self.token = None
        else:
            if DEBUG: print(f"[MAIN INFO] Config file {CONFIG_FILE} not found. Proceeding to admin login for setup.")

        if proceed_to_main_menu:
            self.push_screen("main_menu", self.show_main_menu_screen) 
        else: 
            self.push_screen("admin_login", self.build_admin_login_screen)

        self.schedule_healthcheck()
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup)
    
    def setup_gpio_components(self):
        if not GPIO_AVAILABLE:
            if DEBUG: print("[MAIN WARN] GPIO not available, skipping GPIO component setup.")
            return
        try:
            if DEBUG: print("[MAIN INFO] Starting GPIO component setup...")
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            
            GPIO.setup(SOS_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            try: GPIO.remove_event_detect(SOS_BUTTON_PIN)
            except Exception as e_remove: 
                if DEBUG: print(f"[MAIN TRACE] Error removing event for SOS_BUTTON_PIN (ignorable): {e_remove}")
            GPIO.add_event_detect(SOS_BUTTON_PIN, GPIO.BOTH, 
                                  callback=self._sos_button_state_changed_callback,
                                  bouncetime=BUTTON_DEBOUNCE_TIME)
            self.last_sos_button_state = GPIO.input(SOS_BUTTON_PIN)
            if DEBUG: print(f"[MAIN DEBUG] SOS Button (Pin {SOS_BUTTON_PIN}) setup with GPIO.BOTH complete.")

            GPIO.setup(OPEN_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            try: GPIO.remove_event_detect(OPEN_BUTTON_PIN)
            except Exception as e_remove:
                if DEBUG: print(f"[MAIN TRACE] Error removing event for OPEN_BUTTON_PIN (ignorable): {e_remove}")
            GPIO.add_event_detect(OPEN_BUTTON_PIN, GPIO.BOTH, 
                                  callback=self._open_button_state_changed_callback,
                                  bouncetime=BUTTON_DEBOUNCE_TIME)
            self.last_open_button_state = GPIO.input(OPEN_BUTTON_PIN)
            if DEBUG: print(f"[MAIN DEBUG] Open Button (Pin {OPEN_BUTTON_PIN}) setup with GPIO.BOTH complete.")

            GPIO.setup(BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
            if DEBUG: print(f"[MAIN DEBUG] Buzzer (Pin {BUZZER_PIN}) setup as OUTPUT complete.")
            
            if DEBUG: print("[MAIN INFO] All GPIO components initialized successfully.")
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Failed to setup GPIO components: {e}")

    def initialize_fingerprint_sensor(self):
        if PyFingerprint is None:
            if DEBUG: print("[MAIN WARN] PyFingerprint library not loaded. Fingerprint sensor disabled.")
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
            if RFID_RESET_PIN_BCM is not None:
                import digitalio 
                reset_pin_obj = digitalio.DigitalInOut(getattr(board, f"D{RFID_RESET_PIN_BCM}"))

            irq_pin_obj = None
            if RFID_IRQ_PIN_BCM is not None:
                import digitalio
                irq_pin_obj = digitalio.DigitalInOut(getattr(board, f"D{RFID_IRQ_PIN_BCM}"))

            self.rfid_sensor = PN532_I2C(i2c, debug=False, reset=reset_pin_obj, irq=irq_pin_obj)
            self.rfid_sensor.SAM_configuration()
            ic, ver, rev, support = self.rfid_sensor.firmware_version
            if DEBUG: print(f"[MAIN INFO] PN532 I2C sensor initialized for RFID. Firmware ver: {ver}.{rev}")
        except ValueError as ve: 
            if DEBUG: print(f"[MAIN ERROR] RFID I2C device not found or pin config error: {ve}")
            self.rfid_sensor = None
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Failed to initialize RFID I2C sensor: {e}")
            self.rfid_sensor = None

    def initialize_mqtt(self):
        if self.mqtt_config and not self.mqtt_manager:
            if DEBUG: print("[MAIN DEBUG] Initializing MQTT Manager with config:", self.mqtt_config)
            self.mqtt_manager = MQTTManager(
                mqtt_config=self.mqtt_config, 
                mac=self.mac, 
                fingerprint_sensor=self.fingerprint_sensor,
                rfid_sensor=self.rfid_sensor,
                door_handler=self.door_sensor_handler,
                debug=DEBUG
            )
            self.mqtt_manager.on_token_received = self.on_token_received_from_mqtt
            self.mqtt_manager.on_connection_status_change = self.update_connection_status_display
            if not self.mqtt_manager.connect_and_register():
                if DEBUG: print("[MAIN WARN] Initial MQTT connection/registration attempt failed.")
        elif self.mqtt_manager:
            if self.fingerprint_sensor and not self.mqtt_manager.fingerprint_sensor:
                 self.mqtt_manager.set_fingerprint_sensor(self.fingerprint_sensor)
            if self.rfid_sensor and not self.mqtt_manager.rfid_sensor:
                 self.mqtt_manager.set_rfid_sensor(self.rfid_sensor)
            if self.door_sensor_handler and not self.mqtt_manager.door:
                self.mqtt_manager.set_door_handler(self.door_sensor_handler)

    def schedule_healthcheck(self):
        if self.mqtt_manager and self.mqtt_manager.is_connected():
            self.mqtt_manager.send_healthcheck()
        self.root.after(HEALTHCHECK_INTERVAL_MS, self.schedule_healthcheck)

    def update_connection_status_display(self, is_connected):
        if not self.connection_status_label or not self.connection_status_label.winfo_exists(): return
        
        image_to_show = self.connected_image if is_connected else self.disconnected_image
        text_color = "#2ECC71" if is_connected else "#E74C3C"
        status_text = "" if is_connected else ""
        
        self.connection_status_label.configure(image=image_to_show, text=status_text, text_color=text_color)

    def on_token_received_from_mqtt(self, new_username, new_token):
        config_changed = False
        if new_token and new_username:
            if self.token != new_token or self.mqtt_config.get("mqtt_username") != new_username:
                self.token = new_token
                self.mqtt_config["token"] = new_token
                self.mqtt_config["mqtt_username"] = new_username
                config_changed = True
                if DEBUG: print(f"[MAIN DEBUG] New token/username received from MQTT and updated in local mqtt_config.")
        else:
            if self.token is not None or self.mqtt_config.get("token") is not None:
                self.token = None
                if "token" in self.mqtt_config: del self.mqtt_config["token"]
                if "mqtt_username" in self.mqtt_config: del self.mqtt_config["mqtt_username"]
                config_changed = True
                if DEBUG: print("[MAIN ERROR] Invalid token (None) received from MQTT. Clearing token from local mqtt_config.")

        if config_changed:
            config_path = os.path.join(script_dir, CONFIG_FILE)
            try:
                with open(config_path, "w") as f:
                    json.dump(self.mqtt_config, f, indent=2)
                if DEBUG: print(f"[MAIN DEBUG] mqtt_config (with new token/username status) saved to {CONFIG_FILE}.")
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Failed to save updated mqtt_config: {e}")

        if not new_token and self.mqtt_manager:
            if DEBUG: print("[MAIN INFO] Token was cleared. Disconnecting MQTT client.")
            self.mqtt_manager.disconnect_client()

    def door_state_changed_mqtt_publish(self, door_payload):
        if not self.mqtt_manager or not self.mqtt_manager.is_connected():
            if DEBUG: print("[MAIN DEBUG] Door state changed, but MQTT manager not ready.")
            return
        
        door_payload["MacAddress"]  = self.mac
        door_payload["DeviceTime"]  = datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
        
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

    def trigger_door_open(self, duration_ms=DOOR_OPEN_DURATION_MS):
        if self.open_door_timer is not None:
            try:
               self.root.after_cancel(self.open_door_timer)
               if DEBUG: print(f"[MAIN TRACE] Canceled existing door timer: {self.open_door_timer}")
            except Exception as e_cancel:
               if DEBUG: print(f"[MAIN TRACE] Error canceling timer (ignorable): {e_cancel}")
            self.open_door_timer = None

        if self.door_sensor_handler:
            try:
                self.door_sensor_handler.open_door()
                if DEBUG: print("[MAIN INFO] Door opened via trigger_door_open.")
                if duration_ms > 0:
                   self.open_door_timer = self.root.after(duration_ms, self.trigger_door_close)
                   if DEBUG: print(f"[MAIN DEBUG] Door close timer set: {self.open_door_timer} for {duration_ms}ms")
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Error triggering door open: {e}")
        elif DEBUG: print("[MAIN DEBUG] Door handler not available to open door.")
        
    def trigger_door_close(self):
        if self.open_door_timer is not None:
            try:
                self.root.after_cancel(self.open_door_timer)
                if DEBUG: print(f"[MAIN TRACE] Door close timer explicitly canceled: {self.open_door_timer}")
            except Exception as e_cancel:
                if DEBUG: print(f"[MAIN TRACE] Error canceling timer (ignorable): {e_cancel}")
            self.open_door_timer = None

        if self.door_sensor_handler:
            try:
               self.door_sensor_handler.close_door()
               if DEBUG: print("[MAIN INFO] Door closed via trigger_door_close.")
            except Exception as e:
               if DEBUG: print(f"[MAIN ERROR] Error triggering door close: {e}")
        elif DEBUG: print("[MAIN DEBUG] Door handler not available to close door.")

    def show_background(self):
        if self.bg_photo:
            if self.bg_label and self.bg_label.winfo_exists(): self.bg_label.destroy()
            self.bg_label = ctk.CTkLabel(self.root, image=self.bg_photo, text="")
            self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            self.bg_label.lower()

    def clear_frames(self, keep_background=True, clear_face_ui=True, clear_rfid_ui=True):
        face.stop_face_recognition()

        widgets_to_destroy = []
        if self.frame_mqtt_config and self.frame_mqtt_config.winfo_exists():
            widgets_to_destroy.append(self.frame_mqtt_config)
            self.frame_mqtt_config = None
        if self.frame_main_menu and self.frame_main_menu.winfo_exists():
            widgets_to_destroy.append(self.frame_main_menu)
            self.frame_main_menu = None
        if self.loading_progress and self.loading_progress.winfo_exists():
            widgets_to_destroy.append(self.loading_progress)
            self.loading_progress = None
        if self.frame_result_display and self.frame_result_display.winfo_exists():
            widgets_to_destroy.append(self.frame_result_display)
            self.frame_result_display = None
        
        for widget in self.root.winfo_children():
            if hasattr(widget, '_owner_module') and widget._owner_module == 'fingerprint_ui':
                widgets_to_destroy.append(widget)

        if clear_face_ui:
            if self.face_ui_container and self.face_ui_container.winfo_exists():
                widgets_to_destroy.append(self.face_ui_container)
            self.face_ui_container = None
            self.face_info_label = None
            self.face_image_label = None
            self.face_name_label = None

        if clear_rfid_ui:
            if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
                widgets_to_destroy.append(self.current_rfid_scan_display_frame)
            self.current_rfid_scan_display_frame = None
            self.rfid_scan_active = False

        for widget in widgets_to_destroy:
             if widget and widget.winfo_exists():
                 widget.destroy()
        
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
                 if DEBUG: print(f"[MAIN DEBUG] Screen {screen_id} with same arguments already at top of history. Skipping push.")
                 return
                 
        self.screen_history.append((screen_id, screen_func, args))
        if DEBUG:
            history_ids = [sid for sid, _, _ in self.screen_history]
            print(f"[MAIN DEBUG] Pushing screen: {screen_id}. History: {history_ids}")
        
        self.clear_frames()
        self.root.update_idletasks()
        screen_func(*args)

    def go_back(self):
        if len(self.screen_history) > 1:
            self.screen_history.pop()
            screen_id, screen_func, args = self.screen_history[-1]
            if DEBUG:
                history_ids = [sid for sid, _, _ in self.screen_history]
                print(f"[MAIN DEBUG] Going back to screen: {screen_id}. History: {history_ids}")
            self.clear_frames()
            self.root.update_idletasks()
            screen_func(*args)
        else:
            if DEBUG: print("[MAIN DEBUG] No previous screen in history, returning to main menu.")
            self.return_to_main_menu_screen()

    def return_to_main_menu_screen(self, event=None):
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

    def create_config_button(self):
        for widget in self.root.winfo_children():
            if isinstance(widget, ctk.CTkButton) and hasattr(widget, '_button_id') and widget._button_id == 'config_button':
                widget.lift()
                return
                
        config_button = ctk.CTkButton(self.root, text="Cài Đặt", 
                                      command=self.confirm_reconfigure_device,
                                      width=100, height=38, 
                                      font=("Segoe UI", 15), text_color="white",
                                      fg_color="#6C87D0", hover_color="#5A6268", corner_radius=6)
        config_button._button_id = 'config_button'
        config_button.place(relx=0.98, rely=0.015, anchor="ne")

    def confirm_reconfigure_device(self):
        result = messagebox.askyesno("Xác Nhận Cấu Hình Lại", 
                                     "Bạn có muốn cấu hình lại thiết bị không?\n\n"
                                     "Thao tác này sẽ:\n"
                                     "  - Xóa cấu hình MQTT hiện tại (bao gồm token).\n"
                                     "  - Yêu cầu đăng nhập lại bằng tài khoản Admin.",
                                     icon='warning', parent=self.root)
        if result:
            self.reconfigure_device_settings()

    def reconfigure_device_settings(self):
        if DEBUG: print("[MAIN DEBUG] Device reconfiguration process started.")
        
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client()
            self.mqtt_manager = None
        self.token = None
        self.update_connection_status_display(False)
        if DEBUG: print("[MAIN DEBUG] MQTT Manager disconnected and token cleared for reconfiguration.")

        config_path = os.path.join(script_dir, CONFIG_FILE)
        if os.path.exists(config_path):
            try:
                os.remove(config_path)
                if DEBUG: print(f"[MAIN DEBUG] Removed configuration file: {config_path}")
            except Exception as e:
                if DEBUG: print(f"[MAIN ERROR] Error removing config file {config_path}: {e}")
        self.mqtt_config = {}

        self.screen_history = [] 
        self.push_screen("admin_login", self.build_admin_login_screen)

    def build_admin_login_screen(self):
        if self.frame_mqtt_config and self.frame_mqtt_config.winfo_exists():
            self.frame_mqtt_config.destroy()
        
        self.frame_mqtt_config = ctk.CTkFrame(self.root, fg_color=BG_COLOR, corner_radius=10)
        self.frame_mqtt_config.place(relx=0.5, rely=0.4, anchor="center")

        ctk.CTkLabel(self.frame_mqtt_config, text="XÁC THỰC TÀI KHOẢN ADMIN", font=("Segoe UI", 22, "bold"), text_color="#333").grid(row=0, column=0, columnspan=2, pady=(15, 25), padx=20)
        
        ctk.CTkLabel(self.frame_mqtt_config, text="Tài khoản Admin:", font=("Segoe UI", 16)).grid(row=1, column=0, padx=(20, 10), pady=8, sticky="e")
        self.admin_user_entry = ctk.CTkEntry(self.frame_mqtt_config, width=280, height=40, font=("Segoe UI", 15), placeholder_text="Nhập tài khoản")
        self.admin_user_entry.grid(row=1, column=1, padx=(0, 20), pady=8, sticky="w")
        
        ctk.CTkLabel(self.frame_mqtt_config, text="Mật khẩu:", font=("Segoe UI", 16)).grid(row=2, column=0, padx=(20, 10), pady=8, sticky="e")
        self.admin_pass_entry = ctk.CTkEntry(self.frame_mqtt_config, width=280, height=40, show="*", font=("Segoe UI", 15), placeholder_text="Nhập mật khẩu")
        self.admin_pass_entry.grid(row=2, column=1, padx=(0, 20), pady=8, sticky="w")
        
        login_button = ctk.CTkButton(self.frame_mqtt_config, text="ĐĂNG NHẬP", width=180, height=45, 
                                     font=("Segoe UI", 17, "bold"), fg_color="#007AFF", hover_color="#0056B3", 
                                     text_color="white", command=self.validate_admin_login)
        login_button.grid(row=3, column=0, columnspan=2, pady=(30, 20))

    def validate_admin_login(self):
        username = self.admin_user_entry.get()
        password = self.admin_pass_entry.get()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            if DEBUG: print("[MAIN DEBUG] Admin authentication successful.")
            self.push_screen("mqtt_config_setup", self.build_mqtt_config_screen)
        else:
            messagebox.showerror("Lỗi Đăng Nhập", "Tài khoản hoặc mật khẩu Admin không đúng.\nVui lòng thử lại.", parent=self.frame_mqtt_config or self.root)
            if self.admin_pass_entry: self.admin_pass_entry.delete(0, "end")

    def build_mqtt_config_screen(self):
        if self.frame_mqtt_config and self.frame_mqtt_config.winfo_exists():
            self.frame_mqtt_config.destroy()

        self.frame_mqtt_config = ctk.CTkFrame(self.root, fg_color=BG_COLOR, corner_radius=10)
        self.frame_mqtt_config.place(relx=0.5, rely=0.45, anchor="center")

        ctk.CTkLabel(self.frame_mqtt_config, text="CẤU HÌNH KẾT NỐI MQTT & THIẾT BỊ", font=("Segoe UI", 22, "bold"), text_color="#333").grid(row=0, column=0, columnspan=2, pady=(15, 20), padx=20)
        
        ctk.CTkLabel(self.frame_mqtt_config, text="Địa chỉ Server MQTT:", font=("Segoe UI", 16)).grid(row=1, column=0, padx=(20,10), pady=8, sticky="e")
        self.server_entry = ctk.CTkEntry(self.frame_mqtt_config, width=320, height=40, placeholder_text="VD: mqtt.example.com hoặc IP", font=("Segoe UI", 15))
        self.server_entry.grid(row=1, column=1, padx=(0,20), pady=8, sticky="w")
        self.server_entry.insert(0, self.mqtt_config.get("server", ""))

        ctk.CTkLabel(self.frame_mqtt_config, text="Cổng MQTT:", font=("Segoe UI", 16)).grid(row=2, column=0, padx=(20,10), pady=8, sticky="e")
        self.mqtt_port_entry = ctk.CTkEntry(self.frame_mqtt_config, width=120, height=40, placeholder_text="VD: 1883", font=("Segoe UI", 15))
        self.mqtt_port_entry.grid(row=2, column=1, padx=(0,20), pady=8, sticky="w")
        self.mqtt_port_entry.insert(0, str(self.mqtt_config.get("mqtt_port", "1883")))

        ctk.CTkLabel(self.frame_mqtt_config, text="Tên Phòng/Vị trí:", font=("Segoe UI", 16)).grid(row=3, column=0, padx=(20,10), pady=8, sticky="e")
        self.room_entry = ctk.CTkEntry(self.frame_mqtt_config, width=250, height=40, placeholder_text="VD: Phòng Họp A, Sảnh Chính", font=("Segoe UI", 15))
        self.room_entry.grid(row=3, column=1, padx=(0,20), pady=8, sticky="w")
        self.room_entry.insert(0, self.mqtt_config.get("room", ""))

        button_frame = ctk.CTkFrame(self.frame_mqtt_config, fg_color="transparent")
        button_frame.grid(row=4, column=0, columnspan=2, pady=(30, 20))
        
        back_button = ctk.CTkButton(button_frame, text="QUAY LẠI", width=140, height=45, 
                                    font=("Segoe UI", 16), fg_color="#6C87D", hover_color="#5A6268", 
                                    text_color="white", command=self.go_back)
        back_button.pack(side="left", padx=15)
        
        save_button = ctk.CTkButton(button_frame, text="LƯU & KẾT NỐI", width=200, height=45, 
                                   font=("Segoe UI", 16, "bold"), fg_color="#007AFF", hover_color="#0056B3", 
                                   text_color="white", command=self.validate_and_save_mqtt_settings)
        save_button.pack(side="right", padx=15)

    def validate_and_save_mqtt_settings(self):
        server_address = self.server_entry.get().strip()
        mqtt_port_str = self.mqtt_port_entry.get().strip()
        room_name = self.room_entry.get().strip()

        if not server_address or not mqtt_port_str:
            messagebox.showerror("Thiếu Thông Tin", "Vui lòng điền Địa chỉ Server và Cổng MQTT.", parent=self.frame_mqtt_config or self.root)
            return
        if not room_name:
            messagebox.showerror("Thiếu Thông Tin", "Vui lòng điền Tên Phòng/Vị trí của thiết bị.", parent=self.frame_mqtt_config or self.root)
            return
        
        try:
            mqtt_port = int(mqtt_port_str)
            if not (0 < mqtt_port < 65536): raise ValueError("MQTT Port out of valid range (1-65535)")
        except ValueError:
            messagebox.showerror("Lỗi Dữ Liệu", "Cổng MQTT không hợp lệ. Vui lòng nhập một số.", parent=self.frame_mqtt_config or self.root)
            return

        http_api_port = self.mqtt_config.get("http_port", 8080) 
        
        new_config = { 
            "server": server_address, 
            "mqtt_port": mqtt_port, 
            "http_port": http_api_port,
            "room": room_name
        }
        
        config_path = os.path.join(script_dir, CONFIG_FILE)
        try:
            with open(config_path, "w") as f: json.dump(new_config, f, indent=2)
            self.mqtt_config = new_config
            if DEBUG: print("[MAIN DEBUG] Saved new MQTT configuration (without token yet):", self.mqtt_config)
        except Exception as e:
            if DEBUG: print(f"[MAIN ERROR] Error saving MQTT config to file {config_path}: {e}")
            messagebox.showerror("Lỗi Lưu Trữ", f"Không thể lưu cấu hình MQTT: {e}", parent=self.frame_mqtt_config or self.root)
            return
        
        self.show_connecting_to_server_screen()
        self.root.after(200, self._initialize_mqtt_after_save)

    def _initialize_mqtt_after_save(self):
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client()
        self.mqtt_manager = None
        
        self.token = None # Token sẽ được lấy mới bởi MQTTManager
        self.initialize_mqtt() # MQTTManager sẽ gọi retrieve_token_via_http
        self.root.after(3500, self.return_to_main_menu_screen) 

    def show_connecting_to_server_screen(self):
        self.clear_frames()

        ctk.CTkLabel(self.root, text="Đang lưu cấu hình và kết nối đến Server...", 
                     font=("Segoe UI", 20, "bold"), text_color="#333333").place(relx=0.5, rely=0.45, anchor="center")
        
        self.loading_progress = ctk.CTkProgressBar(self.root, width=350, height=18, corner_radius=8,
                                                 progress_color="#007AFF", mode="indeterminate")
        self.loading_progress.place(relx=0.5, rely=0.55, anchor="center")
        self.loading_progress.start()

    def show_main_menu_screen(self):
        self.clear_frames(clear_face_ui=True, clear_rfid_ui=True)

        if self.frame_main_menu and self.frame_main_menu.winfo_exists():
            self.frame_main_menu.destroy()
        
        self.frame_main_menu = ctk.CTkFrame(self.root, fg_color="transparent")
        self.frame_main_menu.place(relx=0.5, rely=0.5, anchor="center")
        
        menu_options = [
            (self.face_icon_img, "KHUÔN MẶT", self.start_face_recognition_flow),
            (self.fingerprint_icon_img, "VÂN TAY", self.start_fingerprint_scan_flow),
            (self.rfid_icon_img, "THẺ TỪ", self.start_rfid_scan_flow),
        ]
        
        for idx, (icon, label_text, command_func) in enumerate(menu_options):
            if icon is None:
                if DEBUG: print(f"[MAIN WARN] Icon for '{label_text}' is None, skipping menu button.")
                continue

            option_button_container = ctk.CTkFrame(self.frame_main_menu, 
                                             width=BUTTON_WIDTH, height=BUTTON_HEIGHT, 
                                             fg_color=BG_COLOR, corner_radius=12, 
                                             border_width=1, border_color="#D0D0D0")
            option_button_container.grid(row=0, column=idx, padx=PAD_X, pady=PAD_Y)
            option_button_container.grid_propagate(False)

            button = ctk.CTkButton(option_button_container, image=icon, text=label_text, 
                                   font=("Segoe UI", 18, "bold"),
                                   text_color=BUTTON_FG, compound="top", 
                                   fg_color="transparent", hover_color="#E8E8E8", 
                                   command=command_func,
                                   anchor="center")
            button.pack(expand=True, fill="both")

    def start_fingerprint_scan_flow(self):
         if not self.fingerprint_sensor:
             messagebox.showerror("Lỗi Cảm Biến Vân Tay", "Cảm biến vân tay chưa sẵn sàng hoặc bị lỗi.", parent=self.root)
             return
         try:
              if not self.fingerprint_sensor.verifyPassword():
                   messagebox.showerror("Lỗi Cảm Biến Vân Tay", "Không thể xác thực với cảm biến vân tay.", parent=self.root)
                   return
         except Exception as e:
              messagebox.showerror("Lỗi Cảm Biến Vân Tay", f"Lỗi giao tiếp cảm biến: {str(e)[:100]}", parent=self.root)
              return
              
         if DEBUG: print("[MAIN DEBUG] Starting fingerprint scan prompt...")
         self.clear_frames(clear_face_ui=True, clear_rfid_ui=True)

         fp_ui_host_frame = ctk.CTkFrame(self.root, fg_color="transparent")
         fp_ui_host_frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.8, relheight=0.8)
         fp_ui_host_frame._owner_module = 'fingerprint_ui'
         #fp_ui_host_frame.pack(expand=True, fill="both")

         fingerprint.open_fingerprint_prompt(
             parent=fp_ui_host_frame,
             sensor=self.fingerprint_sensor, 
             on_success_callback=self.handle_fingerprint_auth_success,
             on_failure_callback=self.handle_fingerprint_auth_failure,
             device_mac_address=self.mac
         )

    def handle_fingerprint_auth_success(self, user_info_from_module):
        if user_info_from_module is None:
            if DEBUG: print("[MAIN ERROR] Received None user_info from fingerprint module on success callback.")
            self.show_authentication_result_screen(
                success=False,
                message_main="LỖI XỬ LÝ DỮ LIỆU",
                message_sub="KHÔNG LẤY ĐƯỢC THÔNG TIN NGƯỜI DÙNG",
                user_image_ctk=load_image("images/fp_error.png", (150,150))
            )
            self.root.after(1500, self.return_to_main_menu_screen)
            return

        user_info = user_info_from_module
        
        if DEBUG: 
            try:
                print(f"[MAIN DEBUG] Fingerprint Authentication SUCCEEDED. User Info from fingerprint module: {dict(user_info)}")
            except TypeError: 
                print(f"[MAIN DEBUG] Fingerprint Authentication SUCCEEDED. User Info from fingerprint module (not dict-like): {user_info}")

        actual_bio_id = user_info['bio_id'] 
        person_name = user_info['person_name'] if 'person_name' in user_info.keys() and user_info['person_name'] else 'Người dùng không xác định'
        id_number = user_info['id_number'] if 'id_number' in user_info.keys() else None
        face_image_b64_db = user_info['face_image'] if 'face_image' in user_info.keys() else None
        finger_image_b64_db = user_info['finger_image'] if 'finger_image' in user_info.keys() else None
        
        auth_data_for_mqtt = actual_bio_id 

        if DEBUG: print(f"[MAIN SUCCESS] Fingerprint Access GRANTED for {person_name} (BioID: {actual_bio_id})")
        self.trigger_door_open()
        
        if self.mqtt_manager:
            self.mqtt_manager.send_recognition_event(
                bio_id=actual_bio_id, id_number=id_number,
                auth_method="FINGER", auth_data=auth_data_for_mqtt, 
                status="SUCCESS",
                face_image_b64=face_image_b64_db,
                finger_image_b64=finger_image_b64_db 
            )

        self.show_authentication_result_screen(
            success=True,
            message_main=f"XIN CHÀO, {person_name.upper()}!",
            message_sub="XÁC THỰC BẰNG VÂN TAY THÀNH CÔNG",
            user_image_ctk=get_ctk_image_from_db(actual_bio_id, size=(180,180))
        )
        
        self.root.after(3000, self.return_to_main_menu_screen)

    def handle_fingerprint_auth_failure(self, reason=""):
        if DEBUG: print(f"[MAIN DEBUG] Fingerprint Authentication FAILED. Reason: {reason}")
        self.show_authentication_result_screen(
            success=False,
            message_main="XÁC THỰC VÂN TAY THẤT BẠI",
            message_sub=f"",
            user_image_ctk=load_image("images/fp_error.png", (150,150))
        )
        self.root.after(1500, self.return_to_main_menu_screen)

    def start_rfid_scan_flow(self):
        if not self.rfid_sensor:
            messagebox.showerror("Lỗi Đầu Đọc RFID", "Đầu đọc thẻ RFID chưa sẵn sàng hoặc bị lỗi.", parent=self.root)
            return 
        try:
            self.rfid_sensor.SAM_configuration()
        except Exception as e:
            messagebox.showerror("Lỗi Đầu Đọc RFID", f"Lỗi giao tiếp đầu đọc RFID: {str(e)[:100]}", parent=self.root)
            return

        if DEBUG: print("[MAIN DEBUG] Starting RFID authentication scan flow...")
        self.clear_frames(clear_face_ui=True, clear_rfid_ui=False)

        if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
            self.current_rfid_scan_display_frame.destroy()
            self.current_rfid_scan_display_frame = None
        
        self.rfid_scan_active = True
        self.current_rfid_scan_display_frame = rfid.start_rfid_authentication_scan(
            parent_ui_element=self.root,
            sensor_pn532=self.rfid_sensor,
            on_success_callback=self.handle_rfid_auth_success,
            on_failure_callback=self.handle_rfid_auth_failure
        )

    def handle_rfid_auth_success(self, uid_hex_from_card):
        if not self.rfid_scan_active:
            if DEBUG: print("[MAIN DEBUG] RFID success callback received, but RFID scan mode is no longer active.")
            return

        current_time = time.time()
        if current_time - self.last_rfid_auth_time < RFID_AUTH_COOLDOWN_S:
            if DEBUG: print(f"[MAIN DEBUG] RFID UID {uid_hex_from_card} scanned too soon (cooldown). Ignoring.")
            return

        if DEBUG: print(f"[MAIN DEBUG] RFID Authentication SUCCEEDED for UID from card: {uid_hex_from_card}")

        user_info = database.get_user_by_bio_type_and_template("IDCARD", uid_hex_from_card, self.mac)

        if user_info:
            actual_bio_id = user_info['bio_id']
            person_name = user_info['person_name'] if 'person_name' in user_info.keys() and user_info['person_name'] else 'Người dùng không xác định'
            id_number = user_info['id_number'] if 'id_number' in user_info.keys() else None
            face_image_b64_db = user_info['face_image'] if 'face_image' in user_info.keys() else None
            finger_image_b64_db = user_info['finger_image'] if 'finger_image' in user_info.keys() else None


            if not database.is_user_access_valid_now(actual_bio_id, self.mac):
                if DEBUG: print(f"[MAIN WARN] Access DENIED for {person_name} (UID: {uid_hex_from_card}). Outside valid schedule.")
                self.show_authentication_result_screen(
                    success=False,
                    message_main="TRUY CẬP BỊ TỪ CHỐI",
                    message_sub=f"{person_name}\nNGOÀI GIỜ HOẶC HẾT HẠN",
                    user_image_ctk=get_ctk_image_from_db(actual_bio_id, size=(400,250))
                )
                if self.mqtt_manager:
                    self.mqtt_manager.send_recognition_event(
                        bio_id=actual_bio_id, id_number=id_number,
                        auth_method="IDCARD", auth_data=uid_hex_from_card, status="DENIED_SCHEDULE",
                        face_image_b64=face_image_b64_db,
                        finger_image_b64=finger_image_b64_db
                    )
                self.last_rfid_auth_time = current_time
                self.rfid_scan_active = False
                self.root.after(3000, self.return_to_main_menu_screen)
                return

            if DEBUG: print(f"[MAIN SUCCESS] RFID Access GRANTED for {person_name} (BioID: {actual_bio_id})")
            self.last_rfid_auth_time = current_time
            self.trigger_door_open()
            
            if self.mqtt_manager:
                self.mqtt_manager.send_recognition_event(
                    bio_id=actual_bio_id, id_number=id_number,
                    auth_method="IDCARD", auth_data=uid_hex_from_card, status="SUCCESS",
                    face_image_b64=face_image_b64_db,
                    finger_image_b64=finger_image_b64_db
                )
            
            self.show_authentication_result_screen(
                success=True,
                message_main=f"XIN CHÀO, {person_name.upper()}!",
                message_sub="XÁC THỰC BẰNG THẺ TỪ THÀNH CÔNG",
                user_image_ctk=load_image("images/rfid_success.png", (400,250))
            )
        else:
            if DEBUG: print(f"[MAIN WARN] RFID UID {uid_hex_from_card} not found in DB or not active for this device.")
            self.last_rfid_auth_time = current_time
            self.show_authentication_result_screen(
                success=False,
                message_main="THẺ KHÔNG HỢP LỆ",
                message_sub=f"THẺ CHƯA ĐƯỢC ĐĂNG KÝ",
                user_image_ctk=load_image("images/rfid_unknown.png", (400,250))
            )
            if self.mqtt_manager:
                 self.mqtt_manager.send_recognition_event(
                    bio_id=None, id_number=None,
                    auth_method="IDCARD", auth_data=uid_hex_from_card, status="NOT_FOUND"
                )
        
        self.rfid_scan_active = False
        if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
            self.current_rfid_scan_display_frame.destroy()
            self.current_rfid_scan_display_frame = None
        self.root.after(3000, self.return_to_main_menu_screen)

    def handle_rfid_auth_failure(self, reason=""):
        if not self.rfid_scan_active:
            return
        if DEBUG: print(f"[MAIN DEBUG] RFID Authentication FAILED. Reason: {reason}")
        self.rfid_scan_active = False 
        if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
            self.current_rfid_scan_display_frame.destroy()
            self.current_rfid_scan_display_frame = None
        self.root.after(100, self.return_to_main_menu_screen)

    def start_face_recognition_flow(self):
        self.clear_frames(clear_face_ui=False, clear_rfid_ui=True)

        if DEBUG: print(f"[MAIN DEBUG] Attempting to load active FACE vectors for device MAC: {self.mac}")
        loaded_count = face.load_active_vectors_from_db(self.mac) 
        
        if not face.face_db:
            messagebox.showinfo("Không Tìm Thấy Dữ Liệu Khuôn Mặt", 
                                f"Không có dữ liệu khuôn mặt trong khoảng thời gian hiện tại\n"
                                "Vui lòng đồng bộ dữ liệu từ Server hoặc đăng ký mới.", 
                                parent=self.root)
            self.root.after(100, self.return_to_main_menu_screen)
            return

        if not self.face_ui_container or not self.face_ui_container.winfo_exists():
            self.face_ui_container = ctk.CTkFrame(self.root, fg_color="transparent")
            self.face_ui_container.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.8, relheight=0.8)

            self.face_info_label = ctk.CTkLabel(self.face_ui_container, text="", font=("Segoe UI", 20), text_color="#4A4A4A", wraplength=800)
            self.face_info_label.pack(pady=(10,5), anchor="n")

            self.face_image_label = ctk.CTkLabel(self.face_ui_container, text="", fg_color="black")
            self.face_image_label.pack(pady=10, expand=False)

            self.face_name_label = ctk.CTkLabel(self.face_ui_container, text="", font=("Segoe UI", 28, "bold"), text_color="#0056B3", wraplength=800)
            self.face_name_label.pack(pady=(5,10), anchor="s")
        
        self.face_info_label.configure(text="")
        self.face_image_label.configure(text="ĐANG XÁC THỰC", image=None, font=("Segoe UI", 18, "italic"), text_color="white", width=480, height=360)
        self.face_name_label.configure(text="VUI LÒNG NHÌN THẲNG VÀO CAMERA")

        if DEBUG: print("[MAIN DEBUG] Starting face recognition process (thread will be created by face.py)...")
        
        face.open_face_recognition(
            on_recognition=self.handle_face_auth_success,
            on_failure_callback=self.handle_face_auth_failure,
            parent_label=self.face_image_label
        )

    def handle_face_auth_success(self, recognized_key, confidence_score, captured_frame_array):
        if DEBUG: print(f"[MAIN DEBUG] Face Authentication SUCCEEDED: Key='{recognized_key}', Score={confidence_score:.2f}")
        face.stop_face_recognition() 

        parts = recognized_key.split('_')
        bio_id_from_face_key = parts[-1]

        user_info = database.get_user_by_bio_type_and_template("FACE", bio_id_from_face_key, self.mac)
        
        if user_info:
            actual_bio_id = user_info['bio_id'] 
            person_name = user_info['person_name'] if 'person_name' in user_info.keys() and user_info['person_name'] else 'Người dùng không xác định'
            id_number = user_info['id_number'] if 'id_number' in user_info.keys() else None
            finger_image_b64_db = user_info['finger_image'] if 'finger_image' in user_info.keys() else None

            if not database.is_user_access_valid_now(actual_bio_id, self.mac):
                if DEBUG: print(f"[MAIN WARN] Access DENIED for {person_name} (BioID: {actual_bio_id}) from face. Outside valid schedule.")
                if self.face_info_label: self.face_info_label.configure(text="TRUY CẬP BỊ TỪ CHỐI", text_color="#E74C3C")
                if self.face_name_label: self.face_name_label.configure(text=f"{person_name}\nNGOÀI GIỜ HOẶC HẾT HẠN", text_color="#E74C3C")
                
                if captured_frame_array is not None:
                    try:
                        captured_pil_image = Image.fromarray(captured_frame_array)
                        error_img_ctk = CTkImage(light_image=captured_pil_image, dark_image=captured_pil_image, size=(400,400))
                        if self.face_image_label: self.face_image_label.configure(image=error_img_ctk, text="")
                    except Exception as e_img_err:
                        if DEBUG: print(f"[MAIN ERROR] Failed to display captured frame on DENIED: {e_img_err}")

                if self.mqtt_manager:
                    self.mqtt_manager.send_recognition_event(
                        bio_id=actual_bio_id, id_number=id_number,
                        auth_method="FACE", auth_data=bio_id_from_face_key, status="DENIED_SCHEDULE",
                        finger_image_b64=finger_image_b64_db
                    )
                self.root.after(FACE_RECOGNITION_TIMEOUT_MS, self.return_to_main_menu_screen)
                return

            if DEBUG: print(f"[MAIN SUCCESS] Face Access GRANTED for {person_name} (BioID: {actual_bio_id})")
            if self.face_info_label: self.face_info_label.configure(text="XÁC THỰC THÀNH CÔNG", text_color="#2ECC71")
            if self.face_name_label: self.face_name_label.configure(text=f"XIN CHÀO, {person_name.upper()}!", text_color="#2ECC71")
            
            profile_ctk_image = get_ctk_image_from_db(actual_bio_id, size=(300,250))
            if not profile_ctk_image and captured_frame_array is not None:
                 try:
                    pil_img = Image.fromarray(captured_frame_array)
                    pil_img_resized = pil_img.resize((300,250), Image.Resampling.LANCZOS)
                    profile_ctk_image = CTkImage(light_image=pil_img_resized, dark_image=pil_img_resized, size=(300,250))
                 except Exception as e_img:
                    if DEBUG: print(f"[MAIN ERROR] Failed to process captured frame for display: {e_img}")
            
            if self.face_image_label and profile_ctk_image:
                 self.face_image_label.configure(image=profile_ctk_image, text="", width=300, height=250)
            elif self.face_image_label:
                 self.face_image_label.configure(image=None, text=f"Ảnh không có sẵn\n{person_name}", font=("Segoe UI", 16), text_color="grey")

            self.trigger_door_open()
            
            final_face_image_b64_to_send = user_info['face_image'] if 'face_image' in user_info.keys() else None
            if not final_face_image_b64_to_send and captured_frame_array is not None:
                try:
                    buffered = io.BytesIO()
                    Image.fromarray(captured_frame_array).save(buffered, format="JPEG", quality=8)
                    final_face_image_b64_to_send = base64.b64encode(buffered.getvalue()).decode('utf-8')
                except Exception as e_b64:
                    if DEBUG: print(f"[MAIN ERROR] Failed to encode captured frame to Base64: {e_b64}")

            if self.mqtt_manager:
                 self.mqtt_manager.send_recognition_event(
                    bio_id=actual_bio_id, id_number=id_number,
                    auth_method="FACE", auth_data=bio_id_from_face_key,
                    status="SUCCESS",
                    face_image_b64=final_face_image_b64_to_send,
                    finger_image_b64=finger_image_b64_db
                )
        else:
            if DEBUG: print(f"[MAIN WARN] Face key {bio_id_from_face_key} recognized, but user not found/active in DB for this device.")
            if self.face_info_label: self.face_info_label.configure(text="KHÔNG TÌM THẤY TRONG HỆ THỐNG", text_color="#E67E22")
            if self.face_name_label: self.face_name_label.configure(text="Người lạ", text_color="#E67E22")
            
            if captured_frame_array is not None and self.face_image_label:
                try:
                    pil_img_unknown = Image.fromarray(captured_frame_array)
                    pil_img_unknown_resized = pil_img_unknown.resize((300,200), Image.Resampling.LANCZOS)
                    unknown_face_ctk = CTkImage(light_image=pil_img_unknown_resized, dark_image=pil_img_unknown_resized, size=(300,200))
                    self.face_image_label.configure(image=unknown_face_ctk, text="")
                except Exception as e_img_unk:
                    if DEBUG: print(f"[MAIN ERROR] Failed to display captured unknown face: {e_img_unk}")
            
            if self.mqtt_manager:
                 self.mqtt_manager.send_recognition_event(
                    bio_id=None, id_number=None,
                    auth_method="FACE", auth_data=bio_id_from_face_key, status="NOT_FOUND"
                )
        
        self.root.after(FACE_RECOGNITION_TIMEOUT_MS, self.return_to_main_menu_screen)

    def handle_face_auth_failure(self, reason="Unknown"):
        if DEBUG: print(f"[MAIN DEBUG] Face Authentication FAILED. Reason: {reason}")
        face.stop_face_recognition()

        if self.face_info_label and self.face_info_label.winfo_exists():
            self.face_info_label.configure(text="XÁC THỰC THẤT BẠI", text_color="#E74C3C")
        if self.face_name_label and self.face_name_label.winfo_exists():
            self.face_name_label.configure(text="Không thể nhận diện.\nVui lòng thử lại.", text_color="#E74C3C")
        
        if self.face_image_label and self.face_image_label.winfo_exists():
             pass
             
        self.root.after(2500, self.return_to_main_menu_screen)

    def show_authentication_result_screen(self, success, message_main, message_sub, user_image_ctk=None):
        self.clear_frames(clear_face_ui=True, clear_rfid_ui=True)

        if self.frame_result_display and self.frame_result_display.winfo_exists():
           self.frame_result_display.destroy()
    
        self.frame_result_display = ctk.CTkFrame(self.root, fg_color="transparent")
        self.frame_result_display.place(relx=0.5, rely=0.5, anchor="center")

        if user_image_ctk:
            img_label = ctk.CTkLabel(self.frame_result_display, image=user_image_ctk, text="")
            img_label.pack(pady=(0, 25))
        
        main_text_color = "#2ECC71" if success else "#E74C3C"
        ctk.CTkLabel(self.frame_result_display, text=message_main, 
                     font=("Segoe UI", 30, "bold"), 
                     text_color=main_text_color).pack(pady=(0, 8))
        
        sub_text_color = "#555555" if success else "#777777"
        ctk.CTkLabel(self.frame_result_display, text=message_sub, 
                     font=("Segoe UI", 25), 
                     text_color=sub_text_color, wraplength=550, justify="center").pack()

    def _sos_button_state_changed_callback(self, channel):
        if not GPIO_AVAILABLE: return
        time.sleep(0.02) 
        current_state = GPIO.input(SOS_BUTTON_PIN)
        
        if current_state != self.last_sos_button_state:
            self.last_sos_button_state = current_state
            if current_state == GPIO.LOW: 
                if DEBUG: print("[MAIN INFO] SOS Button State Changed: PRESSED (LOW)")
                GPIO.output(BUZZER_PIN, GPIO.HIGH)
                if self.mqtt_manager and self.mqtt_manager.is_connected():
                    self.mqtt_manager.send_sos_alert() 
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
                self.trigger_door_open(duration_ms=DOOR_OPEN_DURATION_MS) 
            else:
                if DEBUG: print("[MAIN INFO] Open Door Button State Changed: RELEASED (HIGH)")
                self.open_button_press_time = None
    
    def request_manual_sync(self):
        if self.mqtt_manager and self.mqtt_manager.is_connected():
             if DEBUG: print("[MAIN INFO] Manual data sync requested by user.")
             self.mqtt_manager.send_device_sync_request() 
             messagebox.showinfo("Yêu Cầu Đồng Bộ", "Đã gửi yêu cầu đồng bộ dữ liệu đến Server.", parent=self.root)
        else:
             messagebox.showwarning("Lỗi Kết Nối MQTT", "Chưa kết nối MQTT.\nKhông thể gửi yêu cầu đồng bộ.", parent=self.root)

    def cleanup(self):
        if DEBUG: print("[MAIN INFO] Application cleanup process started...")
        
        face.stop_face_recognition()
        
        self.rfid_scan_active = False
        if self.current_rfid_scan_display_frame and self.current_rfid_scan_display_frame.winfo_exists():
            self.current_rfid_scan_display_frame.destroy()
            self.current_rfid_scan_display_frame = None
            
        if self.mqtt_manager:
             if DEBUG: print("[MAIN INFO] Disconnecting MQTT client...")
             self.mqtt_manager.disconnect_client()
        
        if self.door_sensor_handler:
             if DEBUG: print("[MAIN INFO] Cleaning up Door Handler GPIO...")
             self.door_sensor_handler.cleanup()
        
        if GPIO_AVAILABLE:
            if DEBUG: print("[MAIN INFO] Cleaning up App-level GPIO (Buzzer, SOS, Open Button)...")
            GPIO.cleanup()

        if DEBUG: print("[MAIN INFO] Exiting application.")
        if self.root and self.root.winfo_exists():
            self.root.destroy()

if __name__ == "__main__":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception: 
        pass

    ctk.set_appearance_mode("Light")
    ctk.set_default_color_theme("blue")
    
    root = ctk.CTk()
    root.geometry("1024x600")
    root.title("Hệ Thống Kiểm Soát Ra Vào Thông Minh")
    root.resizable(False, False)
    
    app = App(root)
    root.mainloop()