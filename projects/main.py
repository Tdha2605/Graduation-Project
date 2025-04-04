import os
from dotenv import load_dotenv
import json
import time
import uuid
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image
from customtkinter import CTkImage
from datetime import datetime
from amg8833 import AMG8833Sensor
from mqtt import MQTTManager
import threading
import face
import id_card
import fingerprint
from door import Door
os.chdir("/home/anhtd/projects")
load_dotenv()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
DEBUG = True
BG_COLOR = "#F5F5F5"
BUTTON_FG = "#333333"
BUTTON_FONT = ("Segoe UI", 28)
BUTTON_WIDTH = 240
BUTTON_HEIGHT = 240
PAD_X = 25
PAD_Y = 25
CONFIG_FILE = "mqtt_config.json"

def get_mac_address():
    mac = uuid.getnode()
    mac_str = ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))
    return mac_str

class App:
    def __init__(self, root):
        self.root = root
        self.mac = get_mac_address()
        if DEBUG:
            print("[DEBUG] MAC Address:", self.mac)
        self.token = None
        self.mqtt_manager = None
        self.mqtt_config = {}
        self.screen_history = []
        self.connection_status_icon = None
        try:
            self.connected_image = CTkImage(Image.open("/home/anhtd/projects/images/connected.jpg"), size=(50, 50))
            self.disconnected_image = CTkImage(Image.open("/home/anhtd/projects/images/disconnected.jpg"), size=(50, 50))
        except Exception as e:
            self.connected_image = self.disconnected_image = None
            if DEBUG:
                print("[DEBUG] Error loading connection status images:", e)
        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="")
        self.connection_status_label.place(relx=0.02, rely=0.02, anchor="nw")
        self.frame_mqtt = None
        self.frame_menu = None
        self.face_frame = None
        self.bg_photo = None
        self.face_img = None
        self.fingerprint_img = None
        self.idcard_img = None
        self.loading_progress = None
        self.bg_label = None
        self.face_info_label = None
        self.face_image_label = None
        self.name_label = None
        self.auto_back_scheduled = False
        try:
            self.bg_image = Image.open("/home/anhtd/projects/images/background.jpeg").resize((1024,600))
            self.bg_photo = CTkImage(self.bg_image, size=(1024,600))
        except Exception as e:
            if DEBUG:
                print("[DEBUG] Error loading background image:", e)
        self.show_background()
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                self.mqtt_config = json.load(f)
            if DEBUG:
                print("[DEBUG] MQTT config loaded:", self.mqtt_config)
        else:
            self.push_screen("admin_login", self.build_admin_login_screen)
        self.root.configure(fg_color=BG_COLOR)
        self.re_init_images()
        self.create_config_button()
        self.show_main_menu()
        self.mqtt_manager = MQTTManager(self.mqtt_config, self.mac, debug=DEBUG)
        self.mqtt_manager.on_token_received = self.on_token_received
        self.mqtt_manager.on_connection_status_change = self.update_connection_status
        if self.mqtt_config:
            self.mqtt_manager.connect_and_register()
        self.schedule_healthcheck()
        self.schedule_guest_cleanup()
        self.amg_sensor = AMG8833Sensor()
        self.door_sensor = Door(sensor_pin=17, relay_pin=27, mqtt_publish_callback=self.door_state_changed, relay_active_high=False)


    def schedule_healthcheck(self):
        if self.mqtt_manager and self.mqtt_manager.connected:
            self.mqtt_manager.send_healthcheck()
        self.root.after(10000, self.schedule_healthcheck)

    def schedule_guest_cleanup(self):
        self.clean_guest_data()
        # Run cleanup once every 24 hours (86400000 ms)
        self.root.after(86400000, self.schedule_guest_cleanup)

    def clean_guest_data(self):
        today = datetime.now().date()
        guest_dirs = [os.path.join("guest", "embeddings"), os.path.join("guest", "images")]
        for directory in guest_dirs:
            if os.path.exists(directory):
                for filename in os.listdir(directory):
                    filepath = os.path.join(directory, filename)
                    if os.path.isfile(filepath):
                        file_date = datetime.fromtimestamp(os.path.getmtime(filepath)).date()
                        if file_date < today:
                            os.remove(filepath)
                            if DEBUG:
                                print(f"[DEBUG] Removed old guest file: {filepath}")

    def on_token_received(self, token):
        self.token = token
        #messagebox.showinfo("Info", "Device registration successful")
        if self.mqtt_manager is not None:
            self.mqtt_manager.token = token
            self.mqtt_manager.connect_with_token()

    def door_state_changed(self, payload):
        payload["MacAddress"] = self.mac
        payload["Token"] = self.token if self.token else ""
        json_payload = json.dumps(payload, separators=(",", ":"))
        print("Door state changed, publishing payload:", json_payload)
        if self.mqtt_manager and self.mqtt_manager.client:
            self.mqtt_manager.client.publish("iot/devices/doorstatus", payload=json_payload)
        else:
            print("MQTT Manager not ready; cannot publish door state.")

    def re_init_images(self):
        try:
            self.face_img = CTkImage(Image.open("/home/anhtd/projects/images/face.png"), size=(250,250))
        except Exception as e:
            if DEBUG:
                print("[DEBUG] Error loading face image:", e)
            self.face_img = None
        try:
            self.fingerprint_img = CTkImage(Image.open("/home/anhtd/projects/images/fingerprint.png"), size=(250,250))
        except Exception:
            self.fingerprint_img = None
        try:
            self.idcard_img = CTkImage(Image.open("/home/anhtd/projects/images/id_card.png"), size=(250,250))
        except Exception:
            self.idcard_img = None

    def show_background(self):
        if self.bg_photo:
            if self.bg_label:
                self.bg_label.destroy()
            self.bg_label = ctk.CTkLabel(self.root, image=self.bg_photo, text="")
            self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
            self.bg_label.lower()

    def update_connection_status(self, is_connected):
        if is_connected:
            self.connection_status_label.configure(image=self.connected_image)
        else:
            self.connection_status_label.configure(image=self.disconnected_image)

    def push_screen(self, screen_id, screen_func):
        if self.screen_history and self.screen_history[-1][0] == screen_id:
            return
        self.screen_history.append((screen_id, screen_func))
        screen_func()

    def go_back(self):
        if len(self.screen_history) > 1:
            old_id, _ = self.screen_history.pop()
            while len(self.screen_history) > 1 and self.screen_history[-1][0] == old_id:
                self.screen_history.pop()
            self.screen_history[-1][1]()
        else:
            self.show_main_menu()

    def create_config_button(self):
        self.config_button = ctk.CTkButton(
            self.root,
            text="Settings",
            command=self.reconfigure,
            width=70,
            height=50,
            fg_color="#4f918b",
            font=("Segoe UI", 18, "bold"),
            text_color="white",
        )
        self.config_button.place(relx=0.98, rely=0.02, anchor="ne")

    def reconfigure(self):
        self.clear_frames()
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client()
            self.mqtt_manager = None
        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)
            if DEBUG:
                print("[DEBUG] Removed configuration file:", CONFIG_FILE)
        self.push_screen("admin_login", self.build_admin_login_screen)

    def build_admin_login_screen(self):
        self.clear_frames()
        self.frame_mqtt = ctk.CTkFrame(self.root, fg_color="transparent")
        self.frame_mqtt.place(relx=0.5, rely=0.25, anchor="center")
        container = ctk.CTkFrame(self.frame_mqtt, fg_color="transparent", corner_radius=10)
        container.pack(padx=20, pady=20)
        ctk.CTkLabel(
            container,
            text="Identity Verification",
            font=("Segoe UI", 28, "bold"),
            text_color="#222"
        ).grid(row=0, column=0, columnspan=2, pady=(20,30))
        self.admin_user_entry = ctk.CTkEntry(
            container,
            width=200,
            height=40,
            placeholder_text="Username",
            font=("Segoe UI",20),
            justify="center"
        )
        self.admin_user_entry.grid(row=2, column=0, padx=10, pady=(0,20))
        self.admin_pass_entry = ctk.CTkEntry(
            container,
            width=200,
            height=40,
            show="*",
            placeholder_text="Password",
            font=("Segoe UI",20),
            justify="center"
        )
        self.admin_pass_entry.grid(row=2, column=1, padx=10, pady=(0,30))
        ctk.CTkButton(
            container,
            text="Login",
            width=150,
            height=40,
            font=("Segoe UI", 24, "bold"),
            fg_color="#4f918b",
            text_color="white",
            command=self.check_admin_login
        ).grid(row=3, column=0, columnspan=2, padx=10, pady=(0,20))

    def check_admin_login(self):
        username = self.admin_user_entry.get()
        password = self.admin_pass_entry.get()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            if DEBUG:
                print("[DEBUG] Admin authentication successful.")
            self.admin_username = username
            self.admin_password = password
            self.push_screen("mqtt_config", self.build_mqtt_config_screen)
        else:
            messagebox.showerror("Access Denied", "Invalid username or password")
            self.admin_user_entry.delete(0, "end")
            self.admin_pass_entry.delete(0, "end")

    def build_mqtt_config_screen(self):
        self.clear_frames()
        self.frame_mqtt = ctk.CTkFrame(self.root, fg_color="transparent")
        self.frame_mqtt.place(relx=0.5, rely=0.25, anchor="center")
        container = ctk.CTkFrame(self.frame_mqtt, fg_color="transparent", corner_radius=10)
        container.pack(padx=20, pady=20)
        ctk.CTkLabel(
            container,
            text="Device Registration",
            font=("Segoe UI",20,"bold"),
            text_color="#222"
        ).grid(row=0, column=0, columnspan=2, pady=(10,15))
        self.server_entry = ctk.CTkEntry(
            container,
            width=200,
            height=40,
            placeholder_text="IP Address",
            font=("Segoe UI",16),
            justify="center"
        )
        self.server_entry.grid(row=2, column=0, padx=10, pady=(0,10))
        self.port_entry = ctk.CTkEntry(
            container,
            width=200,
            height=40,
            placeholder_text="Port",
            font=("Segoe UI",16),
            justify="center"
        )
        self.port_entry.grid(row=2, column=1, padx=10, pady=(0,10))
        self.mqtt_user_entry = ctk.CTkEntry(
            container,
            width=200,
            height=40,
            placeholder_text="MQTT Username",
            font=("Segoe UI",16),
            justify="center"
        )
        self.mqtt_user_entry.grid(row=4, column=0, padx=10, pady=(0,10))
        self.mqtt_pass_entry = ctk.CTkEntry(
            container,
            width=200,
            height=40,
            show="*",
            placeholder_text="MQTT Password",
            font=("Segoe UI",16),
            justify="center"
        )
        self.mqtt_pass_entry.grid(row=4, column=1, padx=10, pady=(0,10))
        ctk.CTkButton(
            container,
            text="Back",
            width=150,
            height=40,
            font=("Segoe UI",18,"bold"),
            fg_color="#4f918b",
            hover_color="orange",
            text_color="white",
            command=self.go_back
        ).grid(row=5, column=0, padx=10, pady=(10,10))
        ctk.CTkButton(
            container,
            text="Register",
            width=150,
            height=40,
            font=("Segoe UI",18,"bold"),
            fg_color="#4f918b",
            hover_color="#218838",
            text_color="white",
            command=self.save_and_connect
        ).grid(row=5, column=1, padx=10, pady=(10,10))

    def save_and_connect(self):
        broker = self.server_entry.get()
        port = self.port_entry.get()
        mqtt_username = self.mqtt_user_entry.get()
        mqtt_password = self.mqtt_pass_entry.get()
        self.save_mqtt_config(broker, port, mqtt_username, mqtt_password)
        self.clear_frames()
        self.show_background()
        self.loading_progress = ctk.CTkProgressBar(self.root, width=300)
        self.loading_progress.place(relx=0.5, rely=0.6, anchor="center")
        self.loading_progress.set(0)
        self.update_progress_bar()
        self.root.after(2000, self._finish_connection)

    def update_progress_bar(self):
        if self.loading_progress is None or not self.loading_progress.winfo_exists():
            return
        current_value = self.loading_progress.get()
        new_value = current_value + 0.01
        if new_value > 1:
            new_value = 0
        self.loading_progress.set(new_value)
        self.root.after(50, self.update_progress_bar)

    def _finish_connection(self):
        if self.loading_progress and self.loading_progress.winfo_exists():
            self.loading_progress.destroy()
        if self.mqtt_manager is None:
            self.mqtt_manager = MQTTManager(self.mqtt_config, self.mac, debug=DEBUG)
            self.mqtt_manager.on_token_received = self.on_token_received
            self.mqtt_manager.on_connection_status_change = self.update_connection_status
        else:
            self.mqtt_manager.mqtt_config = self.mqtt_config
        self.mqtt_manager.connect_and_register()
        self.show_main_menu()

    def save_mqtt_config(self, broker, port, mqtt_username, mqtt_password):
        config = {
            "broker": broker,
            "port": int(port),
            "mqtt_username": mqtt_username,
            "mqtt_password": mqtt_password
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, separators=(",", ":"))
        self.mqtt_config = config
        if DEBUG:
            print("[DEBUG] Saved MQTT config:", self.mqtt_config)

    def show_main_menu(self):
        self.screen_history = [("main_menu", self.show_main_menu)]
        self.clear_frames()
        self.show_background()
        self.frame_menu = ctk.CTkFrame(self.root, fg_color="transparent")
        self.frame_menu.place(relx=0.5, rely=0.5, anchor="center")
        options = [
            (self.face_img, "", self.show_face_recognition_screen),
            (self.idcard_img, "", id_card.open_id_card_recognition),
            (self.fingerprint_img, "", fingerprint.open_fingerprint_scanner)
        ]
        for idx, (img, label, cmd) in enumerate(options):
            option_frame = ctk.CTkFrame(
                self.frame_menu,
                width=BUTTON_WIDTH,
                height=BUTTON_HEIGHT,
                fg_color="transparent",
                corner_radius=0,
                border_width=0
            )
            option_frame.grid(row=0, column=idx, padx=PAD_X, pady=PAD_Y)
            option_frame.grid_propagate(False)
            option_label = ctk.CTkLabel(
                option_frame,
                image=img,
                text=label,
                fg_color="transparent",
                text_color=BUTTON_FG,
                font=BUTTON_FONT,
                compound="top"
            )
            option_label.place(relx=0.5, rely=0.5, anchor="center")
            option_label.bind("<Button-1>", lambda e, cmd=cmd: cmd())

    def show_face_recognition_screen(self):
        self.clear_frames()
        self.auto_back_scheduled = False
        self.show_background()
        self.face_info_label = ctk.CTkLabel(self.root, text="Face recognition is running...", anchor="center")
        self.face_info_label.place(relx=0.5, rely=0.1, anchor="n")
        self.face_image_label = ctk.CTkLabel(self.root, text="")
        self.face_image_label.place(relx=0.5, rely=0.5, anchor="center")
        self.name_label = ctk.CTkLabel(self.root, text="No face recognized yet", anchor="center", font=("Segoe UI", 20))
        self.name_label.place(relx=0.5, rely=0.75, anchor="n")
        threading.Thread(target=face.open_face_recognition, args=(self.update_recognized_face,), daemon=True).start()

    def stop_face_recognition_and_go_back(self):
        face.stop_face_recognition()
        if self.face_info_label is not None:
            self.face_info_label.destroy()
            self.face_info_label = None
        if self.face_image_label is not None:
            self.face_image_label.destroy()
            self.face_image_label = None
        if self.name_label is not None:
            self.name_label.destroy()
            self.name_label = None
        self.show_main_menu()

    def update_recognized_face(self, name, score, frame):
        def update_ui():
            parts = name.split("_")
            if len(parts) == 4:
                try:
                    guest_name, user_id, start_str, end_str = parts
                    start_time = datetime.strptime(start_str, "%Y%m%dT%H%M")
                    end_time = datetime.strptime(end_str, "%Y%m%dT%H%M")
                    current_time = datetime.utcnow()
                    allowed = (start_time <= current_time <= end_time)
                except Exception:
                    allowed = False
                msg = f"Welcome, {name}" if allowed else "Sorry, It is not time for you !"
                image_path = os.path.join("guest", "images", f"{name}.jpg")
            else:
                allowed = True
                msg = f"Name: {name}"
                image_path = os.path.join("employee", "images", f"{name}.jpg")
            if os.path.exists(image_path):
                img = Image.open(image_path)
                screen_width = self.root.winfo_width() or 1024
                screen_height = self.root.winfo_height() or 600
                new_width = int(screen_width * 0.3)
                new_height = int(screen_height * 0.55)
                img = img.resize((new_width, new_height))
                recognized_img = CTkImage(img, size=(new_width, new_height))
                self.face_image_label.configure(image=recognized_img, text="")
                self.face_image_label.image = recognized_img
            self.name_label.configure(text=msg)
            if self.mqtt_manager:
                self.mqtt_manager.send_recognition_success(name)
            if allowed:
                self.door_sensor.open_door()
                self.root.after(5000, self.door_sensor.close_door)
            if not self.auto_back_scheduled:
                self.auto_back_scheduled = True
                self.root.after(2000, self.stop_face_recognition_and_go_back)
        self.root.after(0, update_ui)


    def clear_frames(self):
        self.root.update_idletasks()
        if self.frame_mqtt and self.frame_mqtt.winfo_exists():
            self.frame_mqtt.destroy()
        if self.frame_menu and self.frame_menu.winfo_exists():
            self.frame_menu.destroy()

if __name__ == "__main__":
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.title("Access Control System")
    root.geometry("1024x600")
    app = App(root)
    root.mainloop()
