import os
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

import json
import uuid
import customtkinter as ctk
from tkinter import messagebox, ttk
from PIL import Image
from customtkinter import CTkImage
from datetime import datetime, timezone, time as dt_time, date as dt_date
import threading
import io
import base64
import time

import face_enroll
import fingerprint_enroll
from mqtt_enroll import MQTTEnrollManager
import paho.mqtt.client as mqtt
import database_enroll

try:
    from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1
except ImportError:
    print("[ERROR] PyFingerprint library not found. Fingerprint functionality disabled.")
    PyFingerprint = None
except Exception as e:
    print(f"[ERROR] Failed to import PyFingerprint: {e}. Fingerprint functionality disabled.")
    PyFingerprint = None

DEBUG = True
BG_COLOR = "#F5F5F5"
BUTTON_FG = "#333333"
BUTTON_FONT = ("Segoe UI", 14)
INPUT_FONT = ("Segoe UI", 14)
BUTTON_WIDTH_BOTTOM = 180
BUTTON_HEIGHT_BOTTOM = 130
PAD_X = 2
PAD_Y = 2
CONFIG_FILE = "mqtt_enroll_config.json"
HEALTHCHECK_INTERVAL_MS = 10000

FINGERPRINT_PORT = '/dev/ttyAMA4'
FINGERPRINT_BAUDRATE = 57600

ROOM_TO_MAC = {
    "P1003": "D8:3A:DD:51:09:02",
    "P1004": "AA:BB:CC:44:55:66",
    "TESTROOM": "00:11:22:33:44:55",
}

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

def is_valid_date_format(date_str):
    if not date_str:
        return False # Now required
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def is_valid_time_format(time_str):
    if not time_str:
        return False # Now required
    try:
        datetime.strptime(time_str, "%H:%M:%S")
        return True
    except ValueError:
        return False

def parse_date(date_str):
    if not date_str: return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None

def parse_time(time_str):
    if not time_str: return None
    try:
        return datetime.strptime(time_str, "%H:%M:%S").time()
    except ValueError:
        return None

class EnrollmentApp:
    def __init__(self, root):
        self.root = root
        self.enroll_mac = get_mac_address()
        if DEBUG: print("[Enroll DEBUG] Enrollment Device MAC Address:", self.enroll_mac)

        try:
            database_enroll.initialize_database()
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to initialize enrollment database: {e}\nApplication cannot continue.")
            root.quit()
            return

        self.target_mac = None
        self.current_room_name = None
        self.current_bio_id = None
        self.current_id_number = None
        self.current_person_name = None
        self.current_face_image_b64 = None
        self.current_face_template_b64 = None
        self.current_finger_template_b64 = None
        self.valid_from_date = None
        self.valid_to_date = None
        self.valid_from_time = None
        self.valid_to_time = None
        self.active_day_mask = None

        self.token = None
        self.mqtt_manager = None
        self.mqtt_config = {}
        self.config_path = os.path.join(script_dir, CONFIG_FILE)
        self.screen_history = []
        self.fingerprint_sensor = None
        self.connection_status_label = None
        self.bg_label = None
        self.loading_progress = None
        self.main_frame = None

        self.room_name_entry = None
        self.bio_id_display_label = None
        self.id_number_entry = None
        self.person_name_entry = None
        self.from_date_entry = None
        self.to_date_entry = None
        self.from_time_entry = None
        self.to_time_entry = None
        self.day_vars = []
        self.face_status_label = None
        self.finger_status_label = None

        self.connected_image = load_image("images/connected.jpg", (25, 25))
        self.disconnected_image = load_image("images/disconnected.jpg", (25, 25))
        self.bg_photo = load_image("images/background_enroll.jpeg", (1024, 600))
        img_w = BUTTON_WIDTH_BOTTOM - 50
        img_h = BUTTON_HEIGHT_BOTTOM - 45
        self.face_img = load_image("images/face.png", (img_w, img_h))
        self.fingerprint_img = load_image("images/fingerprint.png", (img_w, img_h))
        self.send_img = load_image("images/send.png", (45, 45))

        self.root.configure(fg_color=BG_COLOR)
        self.show_background()
        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="Chưa kết nối", font=("Segoe UI", 8), text_color="red", compound="left")
        self.connection_status_label.place(relx=0.01, rely=0.97, anchor="sw")
        self.create_config_button()

        self.initialize_fingerprint_sensor()

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    self.mqtt_config = json.load(f)
                if DEBUG: print("[Enroll DEBUG] MQTT config loaded:", self.mqtt_config)
                if not self.mqtt_config.get("broker") or not self.mqtt_config.get("port"):
                     raise ValueError("Config file missing broker or port.")
                self.initialize_mqtt()
                self.show_enrollment_screen()
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                print(f"Error reading/parsing {self.config_path}: {e}. Please reconfigure.")
                try:
                     if os.path.exists(self.config_path): os.remove(self.config_path)
                     self.mqtt_config = {}
                except OSError as remove_err:
                     print(f"Error removing invalid config file: {remove_err}")
                self.push_screen("mqtt_config", self.build_mqtt_config_screen)
            except Exception as e:
                print(f"An unexpected error occurred loading config or initializing: {e}")
                self.push_screen("mqtt_config", self.build_mqtt_config_screen)
        else:
            self.push_screen("mqtt_config", self.build_mqtt_config_screen)

        self.schedule_healthcheck()
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup)

    def generate_new_bio_id(self):
        self.current_bio_id = uuid.uuid4().hex[:10].upper()
        if DEBUG: print(f"[Enroll DEBUG] Generated new Bio ID: {self.current_bio_id}")
        if self.bio_id_display_label and self.bio_id_display_label.winfo_exists():
            self.bio_id_display_label.configure(text=self.current_bio_id)

    def day_name_to_mask(self, day_name): # This function is now OBSOLETE
        pass # Remove or keep for reference, but it's not used anymore

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
                self.fingerprint_sensor = None
        except Exception as e:
            print(f"[ERROR] Failed to initialize fingerprint sensor: {e}")
            self.fingerprint_sensor = None

    def initialize_mqtt(self):
        if self.mqtt_config and not self.mqtt_manager:
            if DEBUG: print("[Enroll DEBUG] Initializing MQTT Manager...")
            self.mqtt_manager = MQTTEnrollManager(
                self.mqtt_config,
                self.enroll_mac,
                self.config_path,
                debug=DEBUG
            )
            self.mqtt_manager.on_connection_status_change = self.update_connection_status
            if not self.mqtt_manager.initialize_connection():
                 print("[Enroll WARN] Initial MQTT connection attempt failed.")

    def schedule_healthcheck(self):
        if self.mqtt_manager:
            if self.mqtt_manager.connected:
                self.mqtt_manager.send_healthcheck()
        self.root.after(HEALTHCHECK_INTERVAL_MS, self.schedule_healthcheck)

    def update_connection_status(self, is_connected):
        if not self.connection_status_label or not self.connection_status_label.winfo_exists(): return
        image_to_show = self.connected_image if is_connected else self.disconnected_image
        text_color = "green" if is_connected else "red"
        status_text = "Đã kết nối" if is_connected else "Chưa kết nối"
        if image_to_show:
            self.connection_status_label.configure(image=image_to_show, text=status_text, text_color=text_color, font=("Segoe UI", 8), compound="left")
        else:
            self.connection_status_label.configure(image=None, text=status_text, text_color=text_color, font=("Segoe UI", 9,"bold"))

    def show_background(self):
        if self.bg_photo:
            if self.bg_label and self.bg_label.winfo_exists(): self.bg_label.destroy()
            self.bg_label = ctk.CTkLabel(self.root, image=self.bg_photo, text="")
            self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            self.bg_label.lower()

    def clear_frames(self, keep_background=True):
        widgets_to_destroy = []
        for widget in self.root.winfo_children():
             if widget == self.main_frame:
                 widgets_to_destroy.append(widget)

        for widget in widgets_to_destroy:
             if widget and widget.winfo_exists():
                 for child in widget.winfo_children():
                     if isinstance(child, ctk.CTkFrame):
                         for grandchild in child.winfo_children():
                             if isinstance(grandchild, ctk.CTkCanvas):
                                 try: grandchild.destroy()
                                 except: pass
                     if isinstance(child, ctk.CTkCanvas):
                         try: child.destroy()
                         except: pass
                 try:
                     widget.destroy()
                 except Exception as e:
                     print(f"[Enroll WARN] Error destroying widget during clear_frames: {e}")


        self.main_frame = None
        self.room_name_entry = None
        self.bio_id_display_label = None
        self.id_number_entry = None
        self.person_name_entry = None
        self.from_date_entry = None
        self.to_date_entry = None
        self.from_time_entry = None
        self.to_time_entry = None
        self.day_vars = []
        self.face_status_label = None
        self.finger_status_label = None

        if keep_background:
            self.show_background()
            if self.connection_status_label and self.connection_status_label.winfo_exists():
                 self.connection_status_label.lift()
            self.create_config_button()

    def push_screen(self, screen_id, screen_func, *args):
        if self.screen_history and self.screen_history[-1][0] == screen_id:
             print(f"[Enroll DEBUG] Screen '{screen_id}' already at top of history. Skipping push.")
             return

        self.screen_history.append((screen_id, screen_func, args))
        if DEBUG:
            history_ids = [sid for sid, _, _ in self.screen_history]
            print(f"[Enroll DEBUG] Pushing screen: {screen_id}. History: {history_ids}")

        self.clear_frames()
        self.root.update_idletasks()
        screen_func(*args)

    def go_back(self):
        if len(self.screen_history) > 1:
            current_screen_tuple = self.screen_history.pop()
            screen_id, screen_func, args = self.screen_history[-1]
            if DEBUG:
                history_ids = [sid for sid, _, _ in self.screen_history]
                print(f"[Enroll DEBUG] Going back to screen: {screen_id}. History: {history_ids}")
            self.clear_frames()
            self.root.update_idletasks()
            screen_func(*args)
        else:
            if DEBUG: print("[Enroll DEBUG] No previous screen, staying on main enrollment screen.")
            if not self.main_frame or not self.main_frame.winfo_exists():
                 self.show_enrollment_screen()


    def return_to_main_menu(self, event=None):
        self.return_to_enrollment_screen()

    def return_to_enrollment_screen(self):
        if DEBUG: print("[Enroll DEBUG] Requesting return to main enrollment screen...")
        face_enroll.stop_face_capture()

        if not self.screen_history or self.screen_history[-1][0] != "enrollment_main":
            self.screen_history = []
            self.push_screen("enrollment_main", self.show_enrollment_screen)
        else:
            print("[Enroll DEBUG] Already on enrollment_main. No transition needed.")

    def _schedule_return_to_enrollment(self):
        self.root.after(50, self.return_to_enrollment_screen)


    def create_config_button(self):
        for widget in self.root.winfo_children():
            if isinstance(widget, ctk.CTkButton) and hasattr(widget, '_button_id') and widget._button_id == 'config_button':
                widget.lift()
                return
        config_button = ctk.CTkButton(
            self.root, text="Cài đặt", command=self.confirm_reconfigure, width=40, height=30,
            fg_color="#6c757d", hover_color="#5a6268", font=("Segoe UI", 11), text_color="white"
        )
        config_button._button_id = 'config_button'
        config_button.place(relx=0.99, rely=0.01, anchor="ne")

    def confirm_reconfigure(self):
        result = messagebox.askyesno("Xác nhận", "Cấu hình lại MQTT?\nThao tác này sẽ xóa cấu hình MQTT hiện tại.", icon='warning', parent=self.root)
        if result: self.reconfigure()

    def reconfigure(self):
        if DEBUG: print("[Enroll DEBUG] Reconfiguration requested.")
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client()
            self.mqtt_manager = None
            self.update_connection_status(False)
            if DEBUG: print("[Enroll DEBUG] MQTT Manager disconnected for reconfiguration.")
        if os.path.exists(self.config_path):
            try:
                os.remove(self.config_path)
                if DEBUG: print("[Enroll DEBUG] Removed configuration file:", self.config_path)
            except Exception as e: print(f"[ERROR] Error removing config file {self.config_path}: {e}")
        self.mqtt_config = {}
        self.screen_history = []
        self.push_screen("mqtt_config", self.build_mqtt_config_screen)

    def build_mqtt_config_screen(self):
        self.main_frame = ctk.CTkFrame(self.root, fg_color=BG_COLOR, bg_color=BG_COLOR)
        self.main_frame.place(relx=0.5, rely=0.3, anchor="center")
        title_label = ctk.CTkLabel(self.main_frame, text="CẤU HÌNH MQTT", font=("Segoe UI", 22, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=(5, 15))

        server_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        server_frame.grid(row=1, column=0, columnspan=2, pady=3)
        ctk.CTkLabel(server_frame, text="Broker IP/Domain:", font=INPUT_FONT).pack(side="left", padx=(0, 3))
        self.server_entry = ctk.CTkEntry(server_frame, width=180, height=30, placeholder_text="Địa chỉ server", font=INPUT_FONT)
        self.server_entry.pack(side="left", padx=3)
        ctk.CTkLabel(server_frame, text="Port:", font=INPUT_FONT).pack(side="left", padx=(5, 3))
        self.port_entry = ctk.CTkEntry(server_frame, width=60, height=30, placeholder_text="Cổng", font=INPUT_FONT)
        self.port_entry.pack(side="left", padx=3)

        http_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        http_frame.grid(row=2, column=0, columnspan=2, pady=3)
        ctk.CTkLabel(http_frame, text="HTTP Port (token):", font=INPUT_FONT).pack(side="left", padx=(0,3))
        self.http_port_entry = ctk.CTkEntry(http_frame, width=60, height=30, font=INPUT_FONT)
        self.http_port_entry.pack(side="left", padx=3)
        default_http_port = self.mqtt_config.get("http_port", "8080")
        self.http_port_entry.insert(0, default_http_port)

        button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        button_frame.grid(row=3, column=0, columnspan=2, pady=(15, 5))
        if len(self.screen_history) > 1:
            ctk.CTkButton(button_frame, text="TRỞ VỀ", width=100, height=35, font=("Segoe UI", 14),
                         fg_color="#6c757d", hover_color="#5a6268", text_color="white", command=self.go_back).pack(side="left", padx=5)
        ctk.CTkButton(button_frame, text="LƯU & KẾT NỐI", width=150, height=35, font=("Segoe UI", 14, "bold"),
                     fg_color="#4f918b", hover_color="#427b75", text_color="white", command=self.validate_and_save_connect).pack(side="right", padx=5)

        if self.mqtt_config.get("broker"):
            self.server_entry.insert(0, self.mqtt_config.get("broker"))
        if self.mqtt_config.get("port"):
            self.port_entry.insert(0, str(self.mqtt_config.get("port")))


    def validate_and_save_connect(self):
        broker = self.server_entry.get().strip()
        port_str = self.port_entry.get().strip()
        http_port_str = self.http_port_entry.get().strip()

        if not all([broker, port_str, http_port_str]):
            messagebox.showerror("Lỗi", "Vui lòng điền Địa Chỉ Server, Cổng MQTT, và HTTP Port.", parent=self.root)
            return
        try:
            port = int(port_str)
            if not (0 < port < 65536): raise ValueError("MQTT Port out of range")
        except ValueError:
            messagebox.showerror("Lỗi", "Cổng MQTT không hợp lệ.", parent=self.root)
            return
        try:
            http_port = int(http_port_str)
            if not (0 < http_port < 65536): raise ValueError("HTTP Port out of range")
        except ValueError:
             messagebox.showerror("Lỗi", "HTTP Port không hợp lệ.", parent=self.root)
             return

        config = { "broker": broker, "port": port, "http_port": http_port }

        try:
            with open(self.config_path, "w") as f: json.dump(config, f, indent=2)
            self.mqtt_config = config
            if DEBUG: print("[Enroll DEBUG] Saved basic MQTT config:", self.mqtt_config)
        except Exception as e:
            print(f"Error saving MQTT config to {self.config_path}: {e}")
            messagebox.showerror("Lỗi Lưu Trữ", f"Không thể lưu cấu hình MQTT: {e}", parent=self.root)
            return

        self.show_connecting_screen()
        self.root.after(100, self._init_mqtt_after_save)

    def _init_mqtt_after_save(self):
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client()
            self.mqtt_manager = None
        self.initialize_mqtt()
        self.root.after(3000, self.return_to_enrollment_screen)

    def show_connecting_screen(self):
        self.clear_frames()
        self.show_background()
        ctk.CTkLabel(self.root, text="Đang lưu cấu hình và kết nối MQTT...",
                       font=("Segoe UI", 22), text_color="#333").place(relx=0.5, rely=0.45, anchor="center")
        self.loading_progress = ctk.CTkProgressBar(self.root, width=350, height=12)
        self.loading_progress.place(relx=0.5, rely=0.55, anchor="center")
        self.loading_progress.set(0)
        self.loading_progress.start()

    def show_enrollment_screen(self):
        print(f"[DEBUG show_enrollment] REBUILDING START. Face data: {bool(self.current_face_template_b64)}, Finger data: {bool(self.current_finger_template_b64)}")
        self.clear_frames()
        self.show_background()
        if not self.current_bio_id:
            self.generate_new_bio_id()
        elif self.bio_id_display_label and self.bio_id_display_label.winfo_exists():
             self.bio_id_display_label.configure(text=self.current_bio_id)

        self.main_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.main_frame.pack(pady=40, padx=40, fill="both", expand=True)

        self.main_frame.grid_rowconfigure(0, weight=0)
        self.main_frame.grid_rowconfigure(1, weight=0)
        self.main_frame.grid_rowconfigure(2, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        top_input_frame = ctk.CTkFrame(self.main_frame, fg_color="#F5F5F5", corner_radius=8)
        top_input_frame.grid(row=0, column=0, padx=5, pady=(0, 5), sticky="new")

        top_input_frame.grid_columnconfigure(0, weight=0, minsize=90)
        top_input_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top_input_frame, text="Thông Tin Đăng Ký", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=2, pady=(5, 10), sticky="w", padx=10)

        ctk.CTkLabel(top_input_frame, text="Tên Phòng:", anchor="w", font=INPUT_FONT).grid(row=1, column=0, padx=(10, 3), pady=3, sticky="w")
        room_options = list(ROOM_TO_MAC.keys())
        current_room_val = self.current_room_name if self.current_room_name in room_options else (room_options[0] if room_options else "")
        self.room_name_var = ctk.StringVar(value=current_room_val)
        self.room_name_entry = ctk.CTkOptionMenu(top_input_frame, variable=self.room_name_var, values=room_options, font=INPUT_FONT, height=28)
        self.room_name_entry.grid(row=1, column=1, padx=(0, 10), pady=3, sticky="ew")

        ctk.CTkLabel(top_input_frame, text="Bio ID:", anchor="w", font=INPUT_FONT).grid(row=2, column=0, padx=(10, 3), pady=3, sticky="w")
        self.bio_id_display_label = ctk.CTkLabel(top_input_frame, text=self.current_bio_id or "N/A", anchor="w", font=INPUT_FONT, text_color="blue")
        self.bio_id_display_label.grid(row=2, column=1, padx=(0, 10), pady=3, sticky="ew")

        ctk.CTkLabel(top_input_frame, text="Số CCCD:", anchor="w", font=INPUT_FONT).grid(row=3, column=0, padx=(10, 3), pady=3, sticky="w")
        self.id_number_entry = ctk.CTkEntry(top_input_frame, placeholder_text="Số căn cước", font=INPUT_FONT, height=28)
        self.id_number_entry.grid(row=3, column=1, padx=(0, 10), pady=3, sticky="ew")
        if self.current_id_number:
             self.id_number_entry.insert(0, self.current_id_number)

        ctk.CTkLabel(top_input_frame, text="Họ và Tên:", anchor="w", font=INPUT_FONT).grid(row=4, column=0, padx=(10, 3), pady=3, sticky="w")
        self.person_name_entry = ctk.CTkEntry(top_input_frame, placeholder_text="Tên hiển thị", font=INPUT_FONT, height=28)
        self.person_name_entry.grid(row=4, column=1, padx=(0, 10), pady=3, sticky="ew")
        if self.current_person_name:
             self.person_name_entry.insert(0, self.current_person_name)

        ctk.CTkLabel(top_input_frame, text="Thời Gian Hiệu Lực", font=("Segoe UI", 15, "bold")).grid(row=5, column=0, columnspan=2, pady=(10, 3), sticky="w", padx=10)

        date_frame = ctk.CTkFrame(top_input_frame, fg_color="transparent")
        date_frame.grid(row=6, column=0, columnspan=2, padx=5, pady=1, sticky="ew")
        ctk.CTkLabel(date_frame, text="Từ Ngày:", font=INPUT_FONT, width=80).pack(side="left", padx=(5, 0))
        self.from_date_entry = ctk.CTkEntry(date_frame, width=100, placeholder_text="YYYY-MM-DD", font=INPUT_FONT, height=28)
        self.from_date_entry.pack(side="left", padx=3)
        ctk.CTkLabel(date_frame, text="Đến:", font=INPUT_FONT, width=40).pack(side="left", padx=(10, 0))
        self.to_date_entry = ctk.CTkEntry(date_frame, width=100, placeholder_text="YYYY-MM-DD", font=INPUT_FONT, height=28)
        self.to_date_entry.pack(side="left", padx=3)
        if self.valid_from_date: self.from_date_entry.insert(0, self.valid_from_date)
        if self.valid_to_date: self.to_date_entry.insert(0, self.valid_to_date)

        time_frame = ctk.CTkFrame(top_input_frame, fg_color="transparent")
        time_frame.grid(row=7, column=0, columnspan=2, padx=5, pady=1, sticky="ew")
        ctk.CTkLabel(time_frame, text="Từ Giờ:", font=INPUT_FONT, width=80).pack(side="left", padx=(5, 0))
        self.from_time_entry = ctk.CTkEntry(time_frame, width=80, placeholder_text="HH:MM:SS", font=INPUT_FONT, height=28)
        self.from_time_entry.pack(side="left", padx=3)
        ctk.CTkLabel(time_frame, text="Đến:", font=INPUT_FONT, width=40).pack(side="left", padx=(10, 0))
        self.to_time_entry = ctk.CTkEntry(time_frame, width=80, placeholder_text="HH:MM:SS", font=INPUT_FONT, height=28)
        self.to_time_entry.pack(side="left", padx=3)
        if self.valid_from_time: self.from_time_entry.insert(0, self.valid_from_time)
        if self.valid_to_time: self.to_time_entry.insert(0, self.valid_to_time)

        days_label_frame = ctk.CTkFrame(top_input_frame, fg_color="transparent")
        days_label_frame.grid(row=8, column=0, columnspan=2, padx=5, pady=(3, 0), sticky="ew")
        ctk.CTkLabel(days_label_frame, text="Ngày Active:", anchor="w", font=INPUT_FONT, width=95).pack(side="left", padx=(5,5))

        days_checkbox_frame = ctk.CTkFrame(top_input_frame, fg_color="transparent")
        days_checkbox_frame.grid(row=9, column=0, columnspan=2, padx=(15, 5), pady=(0, 10), sticky="ew")

        day_names = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        self.day_vars = []
        for day_name in day_names:
            var = ctk.BooleanVar()
            self.day_vars.append(var)
            chk = ctk.CTkCheckBox(days_checkbox_frame, text=day_name, variable=var, font=("Segoe UI", 12), height=20)
            chk.pack(side="left", padx=6, pady=2)

        bottom_button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        bottom_button_frame.grid(row=2, column=0, padx=5, pady=5, sticky="nsew")

        bottom_button_frame.grid_columnconfigure(0, weight=1)
        bottom_button_frame.grid_columnconfigure(1, weight=1)
        bottom_button_frame.grid_columnconfigure(2, weight=1)
        bottom_button_frame.grid_rowconfigure(0, weight=0)
        bottom_button_frame.grid_rowconfigure(1, weight=0)

        face_button = ctk.CTkButton(
            bottom_button_frame, image=self.face_img, text="KHUÔN MẶT", font=BUTTON_FONT,
            text_color=BUTTON_FG, compound="top", fg_color="#E0F7FA", hover_color="#B2EBF2",
            width=BUTTON_WIDTH_BOTTOM, height=BUTTON_HEIGHT_BOTTOM, corner_radius=8,
            command=self.start_face_enrollment)
        face_button.grid(row=0, column=0, padx=PAD_X, pady=(0, 1))
        face_template_present = bool(self.current_face_template_b64)
        face_status_text = "Đăng ký thành công" if face_template_present else "Chưa đăng ký"
        face_text_color = "green" if face_template_present else "grey"
        print(f"[DEBUG show_enrollment_screen] Creating Face Label: text='{face_status_text}', color='{face_text_color}'")
        self.face_status_label = ctk.CTkLabel(bottom_button_frame, text=face_status_text, font=("Segoe UI", 10), text_color=face_text_color)
        self.face_status_label.grid(row=1, column=0, pady=(0, PAD_Y), sticky="n")

        finger_button = ctk.CTkButton(
            bottom_button_frame, image=self.fingerprint_img, text="VÂN TAY", font=BUTTON_FONT,
            text_color=BUTTON_FG, compound="top", fg_color="#E0F7FA", hover_color="#B2EBF2",
            width=BUTTON_WIDTH_BOTTOM, height=BUTTON_HEIGHT_BOTTOM, corner_radius=8,
            command=self.start_fingerprint_enrollment)
        finger_button.grid(row=0, column=1, padx=PAD_X, pady=(0, 1))
        finger_template_present = bool(self.current_finger_template_b64)
        finger_status_text = "Đăng ký thành công" if finger_template_present else "Chưa đăng ký"
        finger_text_color = "green" if finger_template_present else "grey"
        print(f"[DEBUG show_enrollment_screen] Creating Finger Label: text='{finger_status_text}', color='{finger_text_color}'")
        self.finger_status_label = ctk.CTkLabel(bottom_button_frame, text=finger_status_text, font=("Segoe UI", 10), text_color=finger_text_color)
        self.finger_status_label.grid(row=1, column=1, pady=(0, PAD_Y), sticky="n")

        send_button = ctk.CTkButton(
            bottom_button_frame, image=self.send_img, text="ĐĂNG KÝ", font=BUTTON_FONT,
            text_color="black", compound="top", fg_color="#E0F7FA", hover_color="#FFD700",
            width=BUTTON_WIDTH_BOTTOM-130, height=BUTTON_HEIGHT_BOTTOM-100, corner_radius=8,
            command=self.prepare_and_send_data)
        send_button.grid(row=0, column=2, padx=PAD_X, pady=(0, 1))

    def start_face_enrollment(self):
        print(f"[DEBUG start_face] BEFORE START. Face data: {bool(self.current_face_template_b64)}, Finger data: {bool(self.current_finger_template_b64)}")
        if not self.current_bio_id:
             messagebox.showerror("Lỗi", "Không thể tạo Bio ID. Vui lòng thử lại.", parent=self.root)
             return
        if DEBUG: print("[Enroll DEBUG] Starting face enrollment...")
        self.current_room_name = self.room_name_var.get()
        self.current_id_number = self.id_number_entry.get().strip()
        self.current_person_name = self.person_name_entry.get().strip()
        self.valid_from_date = self.from_date_entry.get().strip()
        self.valid_to_date = self.to_date_entry.get().strip()
        self.valid_from_time = self.from_time_entry.get().strip()
        self.valid_to_time = self.to_time_entry.get().strip()

        face_enroll.capture_face_for_enrollment(
            parent=self.root,
            on_success_callback=self.handle_face_enroll_success,
            on_cancel_callback=self.handle_face_enroll_cancel
        )

    def handle_face_enroll_success(self, image_b64, template_b64):
        try:
            if DEBUG: print("[Enroll DEBUG] Face Enrollment Success callback received.")
            print("Raw template received in handle_face_enroll_success:", template_b64[:50], "...")
            self.root.after(10, lambda tb64=image_b64, tpl_b64=template_b64: self._handle_face_success_ui_update(tb64, tpl_b64))
        except Exception as e:
            print(f"[Enroll ERROR] EXCEPTION in handle_face_enroll_success: {e}")
            self.root.after(20, self._schedule_return_to_enrollment)

    def _handle_face_success_ui_update(self, image_b64, template_b64):
        try:
          print("[Enroll DEBUG] Entering _handle_face_success_ui_update.")
          self.current_face_image_b64 = image_b64
          self.current_face_template_b64 = template_b64
          print(f"[DEBUG _handle_face_success] AFTER ASSIGN. Face data: {bool(self.current_face_template_b64)}, Finger data: {bool(self.current_finger_template_b64)}")

          if self.face_status_label and self.face_status_label.winfo_exists():
            self.face_status_label.configure(text="Đăng ký thành công", text_color="green")
            print("[Enroll DEBUG] Face status label updated directly.")
          else:
            print("[Enroll WARN] Face status label does not exist when trying to update.")

        except Exception as e:
          print(f"[Enroll ERROR] EXCEPTION in _handle_face_success_ui_update: {e}")
          self.root.after(20, self._schedule_return_to_enrollment)

    def handle_face_enroll_cancel(self):
        if DEBUG: print("[Enroll DEBUG] Face Enrollment Cancel callback received.")
        self.root.after(10, self._handle_face_cancel_ui_update)

    def _handle_face_cancel_ui_update(self):
         self._schedule_return_to_enrollment()

    def start_fingerprint_enrollment(self):
        print(f"[DEBUG start_fingerprint] BEFORE START. Face data: {bool(self.current_face_template_b64)}, Finger data: {bool(self.current_finger_template_b64)}")
        if not self.current_bio_id:
             messagebox.showerror("Lỗi", "Không thể tạo Bio ID. Vui lòng thử lại.", parent=self.root)
             return
        if not self.fingerprint_sensor:
             messagebox.showerror("Lỗi Cảm Biến", "Cảm biến vân tay chưa sẵn sàng.", parent=self.root)
             return
        try:
            if not self.fingerprint_sensor.verifyPassword():
                 messagebox.showerror("Lỗi Cảm Biến", "Không thể xác thực với cảm biến vân tay.", parent=self.root)
                 return
        except Exception as e:
              messagebox.showerror("Lỗi Cảm Biến", f"Lỗi giao tiếp cảm biến vân tay: {e}", parent=self.root)
              return
        if DEBUG: print("[Enroll DEBUG] Starting fingerprint enrollment...")
        self.current_room_name = self.room_name_var.get()
        self.current_id_number = self.id_number_entry.get().strip()
        self.current_person_name = self.person_name_entry.get().strip()
        self.valid_from_date = self.from_date_entry.get().strip()
        self.valid_to_date = self.to_date_entry.get().strip()
        self.valid_from_time = self.from_time_entry.get().strip()
        self.valid_to_time = self.to_time_entry.get().strip()

        fingerprint_enroll.enroll_fingerprint_template(
             parent=self.root,
             sensor=self.fingerprint_sensor,
             on_success_callback=self.handle_finger_enroll_success,
             on_failure_callback=self.handle_finger_enroll_failure,
             on_cancel_callback=self.handle_finger_enroll_cancel
        )

    def handle_finger_enroll_success(self, template_b64):
        try:
            if DEBUG: print("[Enroll DEBUG] Fingerprint Enrollment Success callback received.")
            print("Raw template received in handle_finger_enroll_success:", template_b64[:50], "...")
            self.root.after(10, lambda tb64=template_b64: self._handle_finger_success_ui_update(tb64))
        except Exception as e:
            print(f"[Enroll ERROR] EXCEPTION in handle_finger_enroll_success: {e}")
            self.root.after(20, self._schedule_return_to_enrollment)

    def _handle_finger_success_ui_update(self, template_b64):
        try:
            print("[Enroll DEBUG] Entering _handle_finger_success_ui_update.")
            self.current_finger_template_b64 = template_b64
            print(f"[DEBUG _handle_finger_success] AFTER ASSIGN. Face data: {bool(self.current_face_template_b64)}, Finger data: {bool(self.current_finger_template_b64)}")

            if self.finger_status_label and self.finger_status_label.winfo_exists():
               self.finger_status_label.configure(text="Đăng ký thành công", text_color="green")
               print("[Enroll DEBUG] Finger status label updated directly.")
            else:
               print("[Enroll WARN] Finger status label does not exist when trying to update.")
        except Exception as e:
            print(f"[Enroll ERROR] EXCEPTION in _handle_finger_success_ui_update: {e}")
            self.root.after(20, self._schedule_return_to_enrollment)

    def handle_finger_enroll_failure(self, reason=""):
        if DEBUG: print(f"[Enroll DEBUG] Fingerprint Enrollment Failure callback received: {reason}")
        self.root.after(10, lambda: self._handle_finger_failure_ui_update(reason))

    def _handle_finger_failure_ui_update(self, reason):
         messagebox.showerror("Lỗi Đăng Ký Vân Tay", f"Không thể đăng ký vân tay. {reason}", parent=self.root)
         self._schedule_return_to_enrollment()

    def handle_finger_enroll_cancel(self):
        if DEBUG: print("[Enroll DEBUG] Fingerprint Enrollment Cancel callback received.")
        self.root.after(10, self._handle_finger_cancel_ui_update)

    def _handle_finger_cancel_ui_update(self):
        self._schedule_return_to_enrollment()

    def generate_active_days_mask(self):
        mask = ['0'] * 7
        if len(self.day_vars) == 7:
            for i, var in enumerate(self.day_vars):
                if var.get():
                    mask[i] = '1'
        else:
            print(f"[Enroll WARN] Incorrect number of day variables found ({len(self.day_vars)}). Returning default mask.")
            return "0000000"
        return "".join(mask)

    def prepare_and_send_data(self):
        self.current_room_name = self.room_name_var.get()
        self.current_id_number = self.id_number_entry.get().strip()
        self.current_person_name = self.person_name_entry.get().strip()
        self.valid_from_date = self.from_date_entry.get().strip()
        self.valid_to_date = self.to_date_entry.get().strip()
        self.valid_from_time = self.from_time_entry.get().strip()
        self.valid_to_time = self.to_time_entry.get().strip()

        if not self.current_bio_id:
             messagebox.showerror("Lỗi Hệ Thống", "Bio ID chưa được tạo. Vui lòng thử lại.", parent=self.root)
             return

        if not self.current_room_name:
            messagebox.showerror("Thiếu Thông Tin", "Vui lòng chọn Tên Phòng.", parent=self.root)
            return
        self.target_mac = ROOM_TO_MAC.get(self.current_room_name.upper())
        if not self.target_mac:
             messagebox.showerror("Lỗi Phòng", f"Không tìm thấy địa chỉ MAC cho phòng '{self.current_room_name}'.\nVui lòng kiểm tra lại tên phòng hoặc cập nhật cấu hình.", parent=self.root)
             return

        if not self.current_id_number:
            messagebox.showerror("Thiếu Thông Tin", "Vui lòng nhập Số CCCD.", parent=self.root)
            return
        if not self.current_person_name:
            messagebox.showerror("Thiếu Thông Tin", "Vui lòng nhập Họ và Tên.", parent=self.root)
            return
        if not self.valid_from_date:
            messagebox.showerror("Thiếu Thông Tin", "Vui lòng nhập 'Từ Ngày'.", parent=self.root)
            return
        if not self.valid_to_date:
            messagebox.showerror("Thiếu Thông Tin", "Vui lòng nhập 'Đến Ngày'.", parent=self.root)
            return
        if not self.valid_from_time:
            messagebox.showerror("Thiếu Thông Tin", "Vui lòng nhập 'Từ Giờ'.", parent=self.root)
            return
        if not self.valid_to_time:
            messagebox.showerror("Thiếu Thông Tin", "Vui lòng nhập 'Đến Giờ'.", parent=self.root)
            return

        if not self.current_id_number.isdigit():
            messagebox.showerror("Lỗi Định Dạng", "Số CCCD chỉ được chứa chữ số.", parent=self.root)
            return

        if not is_valid_date_format(self.valid_from_date):
            messagebox.showerror("Lỗi Định Dạng Ngày", f"Định dạng 'Từ Ngày' không hợp lệ ({self.valid_from_date}).\nYêu cầu: YYYY-MM-DD.", parent=self.root)
            return
        if not is_valid_date_format(self.valid_to_date):
            messagebox.showerror("Lỗi Định Dạng Ngày", f"Định dạng 'Đến Ngày' không hợp lệ ({self.valid_to_date}).\nYêu cầu: YYYY-MM-DD.", parent=self.root)
            return

        if not is_valid_time_format(self.valid_from_time):
            messagebox.showerror("Lỗi Định Dạng Giờ", f"Định dạng 'Từ Giờ' không hợp lệ ({self.valid_from_time}).\nYêu cầu: HH:MM:SS.", parent=self.root)
            return
        if not is_valid_time_format(self.valid_to_time):
            messagebox.showerror("Lỗi Định Dạng Giờ", f"Định dạng 'Đến Giờ' không hợp lệ ({self.valid_to_time}).\nYêu cầu: HH:MM:SS.", parent=self.root)
            return

        from_date_obj = parse_date(self.valid_from_date)
        to_date_obj = parse_date(self.valid_to_date)
        from_time_obj = parse_time(self.valid_from_time)
        to_time_obj = parse_time(self.valid_to_time)

        if from_date_obj and to_date_obj:
            if to_date_obj < from_date_obj:
                messagebox.showerror("Lỗi Logic Ngày", "'Đến Ngày' không được trước 'Từ Ngày'.", parent=self.root)
                return
            if from_date_obj == to_date_obj and from_time_obj and to_time_obj:
                 if to_time_obj < from_time_obj:
                     messagebox.showerror("Lỗi Logic Giờ", "Vào cùng ngày, 'Đến Giờ' không được trước 'Từ Giờ'.", parent=self.root)
                     return

        if not self.current_face_template_b64 and not self.current_finger_template_b64:
             messagebox.showwarning("Thiếu Dữ Liệu Sinh Trắc", "Vui lòng đăng ký Khuôn mặt hoặc Vân tay trước khi gửi.", parent=self.root)
             return

        print("[Enroll INFO] Validation passed. Preparing payload...")

        self.active_day_mask = self.generate_active_days_mask()
        print(f"[Enroll DEBUG] Generated Active Days Mask: {self.active_day_mask}")

        bio_datas = []
        if self.current_face_template_b64:
            if not self.current_face_image_b64:
                 messagebox.showerror("Lỗi Dữ Liệu", "Thiếu dữ liệu ảnh khuôn mặt dù đã có template.", parent=self.root)
                 return
            bio_datas.append({
                "BioType": "FACE",
                "Template": self.current_face_template_b64,
                "Img": self.current_face_image_b64
            })
        if self.current_finger_template_b64:
            bio_datas.append({
                "BioType": "FINGER",
                "Template": self.current_finger_template_b64
            })

        payload_object = {
            "bioId": self.current_bio_id,
            "idNumber": self.current_id_number,
            "personName": self.current_person_name,
            "cmdType": "PUSH_NEW_BIO",
            "bioDatas": bio_datas,
            "fromDate": self.valid_from_date,
            "toDate": self.valid_to_date,
            "fromTime": self.valid_from_time,
            "toTime": self.valid_to_time,
            "activeDays": self.active_day_mask
        }

        final_payload_list = [payload_object]

        if not self.mqtt_manager or not self.mqtt_manager.connected:
            messagebox.showerror("Lỗi MQTT", "Chưa kết nối MQTT. Dữ liệu sẽ được xếp hàng đợi nếu có thể.", parent=self.root)

        try:
            final_payload_str = json.dumps(final_payload_list, indent=2)
            if DEBUG: print(f"[Enroll DEBUG] Sending enrollment data to MAC {self.target_mac} (Room: {self.current_room_name}): {final_payload_str}")

            self.mqtt_manager.publish_enrollment_payload(final_payload_list, self.target_mac)

            send_status = "được xếp hàng đợi" if (not self.mqtt_manager or not self.mqtt_manager.connected) else "được gửi"
            messagebox.showinfo("Hoàn Tất", f"Dữ liệu đăng ký cho Bio ID: {self.current_bio_id} đến phòng '{self.current_room_name}' (MAC: {self.target_mac}) đã {send_status}.", parent=self.root)

            self.reset_enrollment_state()

        except ValueError as ve:
             messagebox.showerror("Lỗi Payload", f"Lỗi tạo dữ liệu gửi: {ve}", parent=self.root)
             print(f"[Enroll ERROR] Invalid payload list: {ve}")
        except Exception as e:
             messagebox.showerror("Lỗi Gửi MQTT", f"Gặp lỗi khi gửi/xếp hàng đợi dữ liệu: {e}", parent=self.root)
             print(f"[Enroll ERROR] Failed to send/queue enrollment data: {e}")

    def reset_enrollment_state(self):
        self.generate_new_bio_id()
        self.current_face_image_b64 = None
        self.current_face_template_b64 = None
        self.current_finger_template_b64 = None
        self.current_id_number = None
        self.current_person_name = None
        self.valid_from_date = None
        self.valid_to_date = None
        self.valid_from_time = None
        self.valid_to_time = None

        if self.id_number_entry and self.id_number_entry.winfo_exists(): self.id_number_entry.delete(0,'end')
        if self.person_name_entry and self.person_name_entry.winfo_exists(): self.person_name_entry.delete(0,'end')
        if self.from_date_entry and self.from_date_entry.winfo_exists(): self.from_date_entry.delete(0,'end')
        if self.to_date_entry and self.to_date_entry.winfo_exists(): self.to_date_entry.delete(0,'end')
        if self.from_time_entry and self.from_time_entry.winfo_exists(): self.from_time_entry.delete(0,'end')
        if self.to_time_entry and self.to_time_entry.winfo_exists(): self.to_time_entry.delete(0,'end')

        print(f"[Enroll DEBUG] Resetting day checkboxes. Found {len(self.day_vars)} vars.")
        for var in self.day_vars:
            try:
                var.set(False)
            except Exception as e:
                print(f"[Enroll WARN] Error resetting a day checkbox variable: {e}")

        if self.face_status_label and self.face_status_label.winfo_exists():
             self.face_status_label.configure(text="Chưa đăng ký", text_color="grey")
        if self.finger_status_label and self.finger_status_label.winfo_exists():
             self.finger_status_label.configure(text="Chưa đăng ký", text_color="grey")

    def cleanup(self):
        print("[Enroll INFO] Cleaning up resources...")
        face_enroll.stop_face_capture()
        if self.mqtt_manager:
             print("[Enroll INFO] Disconnecting MQTT client...")
             self.mqtt_manager.disconnect_client()
        print("[Enroll INFO] Exiting application.")
        self.root.destroy()

if __name__ == "__main__":
    root = ctk.CTk()
    root.geometry("1024x600")
    root.title("Enrollment Device")
    root.resizable(False, False)
    app = EnrollmentApp(root)
    root.mainloop()