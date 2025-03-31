# main.py
import os
import json
import time
import uuid
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image
from customtkinter import CTkImage
from datetime import datetime, timezone

from mqtt import MQTTManager
import face
import id_card
import fingerprint

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

        # Load connection status images
        try:
            self.connected_image = CTkImage(Image.open("projects/images/connected.jpg"), size=(50, 50))
            self.disconnected_image = CTkImage(Image.open("projects/images/disconnected.jpg"), size=(50, 50))
        except Exception as e:
            self.connected_image = self.disconnected_image = None
            if DEBUG:
                print("[DEBUG] Error loading connection status images:", e)

        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="")
        self.connection_status_label.place(relx=0.02, rely=0.02, anchor="nw")
        
        # Frames
        self.frame_mqtt = None
        self.frame_menu = None
        self.bg_photo = None
        self.face_img = None
        self.fingerprint_img = None
        self.idcard_img = None
        self.loading_progress = None
        self.bg_label = None

        # Load background image
        try:
            self.bg_image = Image.open("projects/images/background.jpeg").resize((1024,600))
            self.bg_photo = CTkImage(self.bg_image, size=(1024,600))
        except Exception:
            pass

        self.show_background()

        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                self.mqtt_config = json.load(f)
            if DEBUG:
                print("[DEBUG] MQTT config loaded:", self.mqtt_config)
        else:
            self.push_screen("admin_login", self.build_admin_login_screen)
            return

        self.root.configure(fg_color=BG_COLOR)
        self.re_init_images()
        self.create_config_button()
        self.show_main_menu()

        # Setup MQTT manager
        self.mqtt_manager = MQTTManager(self.mqtt_config, self.mac, debug=DEBUG)
        self.mqtt_manager.on_token_received = self.on_token_received
        self.mqtt_manager.on_connection_status_change = self.update_connection_status

        # Start registration process
        self.mqtt_manager.connect_and_register()

        # Schedule healthcheck periodically
        self.schedule_healthcheck()

    def schedule_healthcheck(self):
        if self.mqtt_manager and self.mqtt_manager.connected:
            self.mqtt_manager.send_healthcheck()
        self.root.after(10000, self.schedule_healthcheck)

    def on_token_received(self, token):
        self.token = token
        messagebox.showinfo("Thông Báo", "Đăng ký thiết bị thành công")
        if self.mqtt_manager is not None:
            self.mqtt_manager.token = token
            self.mqtt_manager.connect_with_token()

    def re_init_images(self):
        try:
            self.face_img = CTkImage(Image.open("projects/images/face.png"), size=(250,250))
        except Exception:
            self.face_img = None
        try:
            self.fingerprint_img = CTkImage(Image.open("projects/images/fingerprint.png"), size=(250,250))
        except Exception:
            self.fingerprint_img = None
        try:
            self.idcard_img = CTkImage(Image.open("projects/images/id_card.png"), size=(250,250))
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
            text="Cài Đặt",
            command=self.reconfigure,
            width=70,
            height=50,
            fg_color="#ba8809",
            font=("Segoe UI", 18, "bold")
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
        container = ctk.CTkFrame(self.frame_mqtt, fg_color="white", corner_radius=10)
        container.pack(padx=20, pady=20)
        ctk.CTkLabel(container, text="Xác thực danh tính", font=("Segoe UI", 28, "bold"), text_color="#222")\
            .grid(row=0, column=0, columnspan=2, pady=(20,30))
        self.admin_user_entry = ctk.CTkEntry(container, width=200, height=40,
                                              placeholder_text="Tài khoản", font=("Segoe UI",20),
                                              justify="center")
        self.admin_user_entry.grid(row=2, column=0, padx=10, pady=(0,20))
        self.admin_pass_entry = ctk.CTkEntry(container, width=200, height=40, show="*",
                                              placeholder_text="Mật khẩu", font=("Segoe UI",20),
                                              justify="center")
        self.admin_pass_entry.grid(row=2, column=1, padx=10, pady=(0,30))
        ctk.CTkButton(container, text="Đăng nhập", width=150, height=40, font=("Segoe UI", 24),
                      fg_color="#3738e2", command=self.check_admin_login)\
            .grid(row=3, column=0, columnspan=2, padx=10, pady=(0,20))

    def check_admin_login(self):
        username = self.admin_user_entry.get()
        password = self.admin_pass_entry.get()
        if username == "navis" and password == "navis@123":
            if DEBUG:
                print("[DEBUG] Admin authentication successful.")
            self.admin_username = username
            self.admin_password = password
            self.push_screen("mqtt_config", self.build_mqtt_config_screen)
        else:
            messagebox.showerror("Truy Cập Bị Từ Chối", "Tài khoản hoặc mật khẩu không hợp lệ")
            self.admin_user_entry.delete(0, "end")
            self.admin_pass_entry.delete(0, "end")

    def build_mqtt_config_screen(self):
        self.clear_frames()
        self.frame_mqtt = ctk.CTkFrame(self.root, fg_color="transparent")
        self.frame_mqtt.place(relx=0.5, rely=0.25, anchor="center")
        container = ctk.CTkFrame(self.frame_mqtt, fg_color="white", corner_radius=10)
        container.pack(padx=20, pady=20)
        ctk.CTkLabel(container, text="Đăng ký thiết bị", font=("Segoe UI",20,"bold"), text_color="#222")\
            .grid(row=0, column=0, columnspan=2, pady=(10,15))
        self.server_entry = ctk.CTkEntry(container, width=200, height=40,
                                         placeholder_text="Địa chỉ IP", font=("Segoe UI",16),
                                         justify="center")
        self.server_entry.grid(row=2, column=0, padx=10, pady=(0,10))
        self.port_entry = ctk.CTkEntry(container, width=200, height=40,
                                       placeholder_text="Cổng", font=("Segoe UI",16),
                                       justify="center")
        self.port_entry.grid(row=2, column=1, padx=10, pady=(0,10))
        self.mqtt_user_entry = ctk.CTkEntry(container, width=200, height=40,
                                            placeholder_text="Tài khoản MQTT", font=("Segoe UI",16),
                                            justify="center")
        self.mqtt_user_entry.grid(row=4, column=0, padx=10, pady=(0,10))
        self.mqtt_pass_entry = ctk.CTkEntry(container, width=200, height=40, show="*",
                                            placeholder_text="Mật khẩu MQTT", font=("Segoe UI",16),
                                            justify="center")
        self.mqtt_pass_entry.grid(row=4, column=1, padx=10, pady=(0,10))
        ctk.CTkButton(container, text="Quay lại", width=150, height=40, font=("Segoe UI",18,"bold"),
                      fg_color="gray", hover_color="orange", command=self.go_back)\
            .grid(row=5, column=0, padx=10, pady=(10,10))
        ctk.CTkButton(container, text="Đăng ký", width=150, height=40, font=("Segoe UI",18),
                      fg_color="#3738e2", hover_color="#218838", command=self.save_and_connect)\
            .grid(row=5, column=1, padx=10, pady=(10,10))

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

        # Create a transparent container so the background is visible
        self.frame_menu = ctk.CTkFrame(self.root, fg_color="transparent")
        self.frame_menu.place(relx=0.5, rely=0.5, anchor="center")

        # Define the three options as (image, label, command)
        options = [
            (self.face_img, "", self.handle_face),
            (self.idcard_img, "", id_card.open_id_card_recognition),
            (self.fingerprint_img, "", fingerprint.open_fingerprint_scanner)
        ]

        # For each option, create a frame and a clickable label (acting as a button)
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
            # Bind a click event to trigger the command
            option_label.bind("<Button-1>", lambda e, cmd=cmd: cmd())

    def handle_face(self):
        self.clear_frames()
        face.open_face_recognition(self.root)

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
    root.title("Hệ Thống Kiểm Soát Truy Cập")
    root.geometry("1024x600")
    app = App(root)
    root.mainloop()
