import os
import json
import base64
import time
from datetime import datetime
from uuid import uuid4

import numpy as np
import cv2
from picamera2 import Picamera2
import customtkinter as ctk
from tkinter import messagebox
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Fingerprint sensor
from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1, FINGERPRINT_CHARBUFFER2
# Face recognition
from insightface.app import FaceAnalysis

# MQTT settings
MQTT_BROKER = os.getenv("MQTT_BROKER", "p06299ce.ala.eu-central-1.emqxsl.com")
MQTT_PORT   = int(os.getenv("MQTT_PORT", 8883))
MQTT_USER   = os.getenv("MQTT_USERNAME", "studentaccount")
MQTT_PASS   = os.getenv("MQTT_PASSWORD", "studentaccount@123")

# Ensure output directory
os.makedirs("registrations", exist_ok=True)

class MQTTPublisher:
    def __init__(self, broker, port, user, pw):
        self.client = mqtt.Client(protocol=mqtt.MQTTv5)
        self.client.username_pw_set(user, pw)
        if port == 8883:
            self.client.tls_set()
        self.client.connect(broker, port)
        self.client.loop_start()
        print(f"[DEBUG] MQTT connected {broker}:{port}")

    def push_biometric(self, door_mac: str, commands: list):
        topic = f"iot/server/{door_mac}/push_biometric"
        payload = json.dumps(commands, separators=(",", ":"))
        self.client.publish(topic, payload=payload, qos=1)
        print(f"[DEBUG] Published to {topic}: {payload}")

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()
        print("[DEBUG] MQTT disconnected")

# Utilities

def capture_image():
    picam2 = Picamera2()
    cfg = picam2.create_preview_configuration(main={"size": (640, 480), "format": "RGB888"})
    picam2.configure(cfg)
    picam2.start()
    frame = picam2.capture_array()
    picam2.stop()
    picam2.close()
    return frame


def encode_embedding(emb: np.ndarray) -> str:
    return base64.b64encode(emb.astype(np.float32).tobytes()).decode()


def encode_image_b64(img: np.ndarray) -> str:
    ok, buf = cv2.imencode('.jpg', img)
    return base64.b64encode(buf).decode() if ok else ""

class RegistrationWizard(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Đăng ký sinh trắc học")
        self.geometry("1024x600")
        ctk.set_appearance_mode("Light")
        ctk.set_default_color_theme("green")

        # Initialize face model
        self.face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
        self.face_app.prepare(ctx_id=0)
        print("[DEBUG] Face model initialized")

        # Initialize fingerprint sensor
        try:
            self.fsensor = PyFingerprint('/dev/ttyAMA0', 57600, 0xFFFFFFFF, 0x00000000)
            assert self.fsensor.verifyPassword()
            print("[DEBUG] Finger sensor initialized")
        except Exception as e:
            messagebox.showerror("Fingerprint Error", str(e))
            self.fsensor = None

        # MQTT publisher
        self.mqtt = MQTTPublisher(MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS)

        # State
        self.selection = {}      # {(day, timeslot): Button}
        self.face_emb = None
        self.face_b64 = None
        self.finger_b64 = None
        self.finger_position = None

        # Wizard setup
        self.tab_names = ["1.Chọn khung giờ", "2.Đăng ký", "3.Xác nhận"]
        self.tabs = ctk.CTkTabview(self, width=1000, height=520)
        self.tabs.pack(padx=12, pady=(12,0))
        for name in self.tab_names:
            self.tabs.add(name)

        self.current = 0
        self._build_step1()
        self._build_step2()
        self._build_step3()

        # Navigation buttons
        nav = ctk.CTkFrame(self)
        nav.pack(fill="x", pady=12)
        self.btn_prev = ctk.CTkButton(nav, text="← Quay lại", width=200, command=self.prev_step)
        self.btn_next = ctk.CTkButton(nav, text="Tiếp theo →", width=200, command=self.next_step)
        self.btn_prev.pack(side="left", padx=20)
        self.btn_next.pack(side="right", padx=20)
        self._update_nav()

    def _build_step1(self):
        f = self.tabs.tab(self.tab_names[0])
        days = ["T2","T3","T4","T5","T6","T7","CN"]
        # Timeslots: 07:00–17:30 every 45 minutes
        timeslots = []
        h, m = 7, 0
        while True:
            label = f"{h:02d}:{m:02d}"
            timeslots.append(label)
            total = h*60 + m + 45
            if total > 17*60 + 30:
                break
            h, m = divmod(total, 60)
        # Column headers
        for j, d in enumerate(days):
            ctk.CTkLabel(f, text=d).place(x=100+110*j, y=20)
        # Grid of buttons
        for i, ts in enumerate(timeslots):
            ctk.CTkLabel(f, text=ts).place(x=50, y=60+30*i)
            for j, d in enumerate(days):
                btn = ctk.CTkButton(
                    f, text="", width=100, height=28, fg_color="#ccc",
                    command=lambda dd=d, tt=ts: self._toggle(dd, tt)
                )
                btn.place(x=100+110*j, y=60+30*i)
                self.selection[(d, ts)] = btn

    def _toggle(self, day, timeslot):
        btn = self.selection[(day, timeslot)]
        sel = getattr(btn, 'selected', False)
        btn.selected = not sel
        btn.configure(fg_color="#4f918b" if not sel else "#ccc")

    def _build_step2(self):
        f = self.tabs.tab(self.tab_names[1])
        ctk.CTkLabel(f, text="Chọn phương thức", font=(None,18)).place(x=50,y=20)
        # Enroll face
        ctk.CTkButton(f, text="Enroll Face", width=200, command=self._enroll_face).place(x=50,y=80)
        self.face_status = ctk.CTkLabel(f, text="[ ]", font=(None,16))
        self.face_status.place(x=270,y=85)
        # Enroll finger
        ctk.CTkButton(f, text="Enroll Finger", width=200, command=self._enroll_finger).place(x=50,y=140)
        self.finger_status = ctk.CTkLabel(f, text="[ ]", font=(None,16))
        self.finger_status.place(x=270,y=145)

    def _build_step3(self):
        f = self.tabs.tab(self.tab_names[2])
        ctk.CTkLabel(f, text="Xác nhận và gửi", font=(None,18)).pack(pady=(20,10))
        self.summary = ctk.CTkTextbox(f, width=900, height=300)
        self.summary.pack(pady=10)
        ctk.CTkButton(f, text="Gửi", width=200, command=self._submit).pack(pady=10)

    def _update_nav(self):
        self.tabs.set(self.tab_names[self.current])
        self.btn_prev.configure(state="normal" if self.current>0 else "disabled")
        self.btn_next.configure(text="Hoàn thành" if self.current==2 else "Tiếp theo →")

    def next_step(self):
        if self.current<2:
            self.current+=1
            if self.current==2:
                self._refresh_summary()
        else:
            self._submit()
        self._update_nav()

    def prev_step(self):
        if self.current>0:
            self.current-=1
            self._update_nav()

    def _enroll_face(self):
        frame = capture_image()
        faces = self.face_app.get(frame)
        if not faces:
            messagebox.showwarning("Face","Không phát hiện khuôn mặt.")
            return
        emb = faces[0].embedding.astype(np.float32)
        self.face_emb = emb
        self.face_b64 = encode_image_b64(frame)
        self.face_status.configure(text="[X]")
        print("[DEBUG] Face enrolled")

    def _enroll_finger(self):
        if not self.fsensor:
            messagebox.showerror("Finger","Sensor vân tay không khả dụng.")
            return
        s = self.fsensor
        try:
            messagebox.showinfo("Finger","Scan lần 1")
            if not s.readImage(): raise RuntimeError("Quét lần 1 thất bại")
            s.convertImage(FINGERPRINT_CHARBUFFER1)
            messagebox.showinfo("Finger","Scan lần 2")
            time.sleep(1)
            if not s.readImage(): raise RuntimeError("Quét lần 2 thất bại")
            s.convertImage(FINGERPRINT_CHARBUFFER2)
            s.createTemplate()
            print("[DEBUG] Templates merged into buffer1")
            # Store on sensor
            pos = s.storeTemplate()
            if pos < 0:
                raise RuntimeError(f"Lưu mẫu vân tay thất bại: {pos}")
            self.finger_position = pos
            print(f"[DEBUG] Stored at position {pos}")
            # Reload from sensor
            if not s.loadTemplate(pos, FINGERPRINT_CHARBUFFER1):
                raise RuntimeError("Tải lại mẫu thất bại")
            chars = s.downloadCharacteristics(FINGERPRINT_CHARBUFFER1)
            self.finger_b64 = base64.b64encode(bytes(chars)).decode()
            self.finger_status.configure(text="[X]")
            print("[DEBUG] Finger enrolled and stored")
        except Exception as e:
            messagebox.showerror("Finger",str(e))
            print(f"[ERROR] {e}")

    def _refresh_summary(self):
        slots = [f"{d}-{t}" for (d,t),btn in self.selection.items() if getattr(btn,'selected',False)]
        self.summary.delete("0.0","end")
        self.summary.insert("end","Slots:\n"+", ".join(slots)+"\n")
        self.summary.insert("end",f"Face: {bool(self.face_b64)}\n")
        self.summary.insert("end",f"Finger: {bool(self.finger_b64)}\n")

    def _submit(self):
        slots = [(d,t) for (d,t), btn in self.selection.items() if getattr(btn,'selected',False)]
        if not slots:
            messagebox.showerror("Lỗi","Chưa chọn khung giờ")
            self.current=0; self._update_nav(); return
        if not self.face_b64 and not self.finger_b64:
            messagebox.showerror("Lỗi","Chưa enroll face hoặc finger")
            self.current=1; self._update_nav(); return
        bio_id = str(uuid4())
        bio_datas = []
        if self.face_b64:
            bio_datas.append({"BioType":"FACE","Template":encode_embedding(self.face_emb),"Img":self.face_b64})
        if self.finger_b64:
            bio_datas.append({"BioType":"FINGER","Template":self.finger_b64,"Img":""})
        now = datetime.now().strftime("%Y-%m-%d")
        cmd = {
            "bioId":bio_id,
            "idNumber":"",
            "cmdType":"PUSH_NEW_BIO",
            "bioDatas":bio_datas,
            "fromDate":now,
            "toDate":now,
            "fromTime":"00:00:00",
            "toTime":"23:59:59",
            "activeDays":"1111110"
        }
        mac = "D8:3A:DD:51:09:02"
        self.mqtt.push_biometric(mac,[cmd])
        messagebox.showinfo("Hoàn tất","Đã gửi dữ liệu đăng ký")
        print("[DEBUG] Cmd:",cmd)
        self.destroy()

if __name__ == "__main__":
    ctk.set_appearance_mode("Light")
    app = RegistrationWizard()
    app.mainloop()