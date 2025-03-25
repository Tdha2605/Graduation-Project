import tkinter as tk
from tkinter import messagebox
import re
import paho.mqtt.client as mqtt
import ssl
import json
import time
from PIL import Image, ImageTk

# ====== MQTT CONFIG ======
MQTT_BROKER = "vd113f18.ala.eu-central-1.emqxsl.com"
MQTT_PORT = 8883
MQTT_USERNAME = "hti"
MQTT_PASSWORD = "Hti@123"
MQTT_PUB_TOPIC = "device/register"
MQTT_SUB_TOPIC = "device/response"
MQTT_HEALTH_TOPIC = "device/health"

def validate_mac(mac):
    return re.match(r"^([0-9A-Fa-f]{2}:){5}([0-9A-Fa-f]{2})$", mac) is not None

class App:
    def __init__(self, root):
        self.root = root
        self.mac = ""
        self.client = None

        # Cài đặt background và hiển thị ảnh nền
        self.root.configure(bg="#e0f7fa")
        self.bg_image = Image.open("B1.jpg").resize((600, 600))
        self.bg_photo = ImageTk.PhotoImage(self.bg_image)
        self.bg_label = tk.Label(root, image=self.bg_photo)
        self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)

        self.frame_mac = tk.Frame(root, bg=None, bd=0, highlightthickness=0)
        self.frame_menu = tk.Frame(root, bg=None, bd=0, highlightthickness=0)
        self.build_mac_screen()

    def build_mac_screen(self):
        self.clear_frames()
        self.frame_mac.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(self.frame_mac, text="Enter MAC Address", 
                 font=("San Francisco", 18, "bold"), bg="#000000", fg="white").pack(pady=10)

        self.entry = tk.Entry(self.frame_mac, font=("Courier", 20), width=20,
                              justify="center", bd=3, bg="white")
        self.entry.pack(pady=10)

        keyboard_frame = tk.Frame(self.frame_mac, bg=None)
        keyboard_frame.pack()

        keys = [
            ['A', 'B', 'C', 'D', 'E', 'F'],
            ['0', '1', '2', '3', '4', '5'],
            ['6', '7', '8', '9', ':']
        ]

        for row in keys:
            row_frame = tk.Frame(keyboard_frame, bg=None)
            row_frame.pack(pady=2)
            for key in row:
                btn = tk.Button(row_frame, text=key, width=4, height=2, font=("Arial", 16),
                                bg="#ffffff", fg="black", activebackground="#dddddd",
                                command=lambda k=key: self.entry.insert(tk.END, k))
                btn.pack(side=tk.LEFT, padx=2)

        func_frame = tk.Frame(self.frame_mac, bg=None)
        func_frame.pack(pady=10)

        tk.Button(func_frame, text="Xóa", width=6, height=2, font=("San Francisco", 14),
                  bg="#ffcdd2", command=self.backspace).pack(side=tk.LEFT, padx=5)
        tk.Button(func_frame, text="Xóa toàn bộ", width=10, height=2, font=("San Francisco", 14),
                  bg="#ffe082", command=lambda: self.entry.delete(0, tk.END)).pack(side=tk.LEFT, padx=5)
        tk.Button(func_frame, text="Lưu", width=6, height=2, font=("SOpen Sans", 14),
                  bg="#a5d6a7", command=self.save_mac).pack(side=tk.LEFT, padx=5)

    def backspace(self):
        current = self.entry.get()
        self.entry.delete(0, tk.END)
        self.entry.insert(0, current[:-1])

    def save_mac(self):
        mac = self.entry.get().strip()
        if validate_mac(mac):
            with open("device_mac.txt", "w", encoding="utf-8") as f:
                f.write(mac)
            self.mac = mac
            self.connect_mqtt_and_send(mac)
        else:
            messagebox.showerror("Error", "Invalid MAC address!\nFormat: AA:BB:CC:DD:EE:FF")

    def connect_mqtt_and_send(self, mac):
        try:
            self.client = mqtt.Client()
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            self.client.tls_set(cert_reqs=ssl.CERT_NONE)
            self.client.tls_insecure_set(True)

            self.client.on_message = self.on_message
            self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.client.loop_start()

            self.client.subscribe(MQTT_SUB_TOPIC)
            self.client.publish(MQTT_PUB_TOPIC, mac)
            # Không hiển thị thông báo ngay lúc gửi MAC, chờ phản hồi từ server

            self.send_healthcheck()

        except Exception as e:
            messagebox.showerror("MQTT Error", str(e))

    def on_message(self, client, userdata, msg):
        payload = msg.payload.decode()
        print(f"[MQTT] Received from {msg.topic}: {payload}")
        if msg.topic == MQTT_SUB_TOPIC:
            if "thành công" in payload.lower():
                messagebox.showinfo("Thông báo", "Đăng ký thiết bị thành công")
                self.show_main_menu()
            else:
                messagebox.showinfo("Thông báo", payload)

    def send_healthcheck(self):
        if self.client and self.mac:
            heartbeat = {
                "mac": self.mac,
                "status": "alive",
                "timestamp": int(time.time())
            }
            self.client.publish(MQTT_HEALTH_TOPIC, json.dumps(heartbeat))
            print("[MQTT] Healthcheck sent:", heartbeat)

        self.root.after(10000, self.send_healthcheck)

    def show_main_menu(self):
        self.clear_frames()
        self.frame_menu.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(self.frame_menu, text="Hãy lựa chọn hình thức sinh trắc học", 
                 font=("Arial", 18, "bold"), bg="red", fg="white").pack(pady=20)

        tk.Button(self.frame_menu, text="Nhận diện bằng khuôn mặt", font=("Arial", 16),
                  width=25, bg="#bbdefb", command=self.handle_face).pack(pady=10)

        tk.Button(self.frame_menu, text="Nhận diện bằng vân tay", font=("Arial", 16),
                  width=25, bg="#ffe0b2", command=self.handle_barcode).pack(pady=10)

    def handle_face(self):
        messagebox.showinfo("Info", "Open camera for face recognition (not implemented)")

    def handle_barcode(self):
        messagebox.showinfo("Info", "Open barcode scanner (not implemented)")

    def clear_frames(self):
        for frame in (self.frame_mac, self.frame_menu):
            frame.place_forget()

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Hệ thống giám sát vào ra")
    root.geometry("600x600")
    app = App(root)
    root.mainloop()
    
    
    
