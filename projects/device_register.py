import customtkinter as ctk
from tkinter import messagebox, StringVar, Checkbutton, Frame, LEFT
from PIL import Image, ImageTk
import base64
import threading
import time
import json
import os
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
import cv2
import numpy as np
from datetime import datetime, timedelta, timezone
import hashlib
import io  # Added import for io

try:
    from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1, FINGERPRINT_CHARBUFFER2
except ImportError:
    print("[WARN] PyFingerprint library not found.")
    PyFingerprint = None
except Exception as e:
    print(f"[WARN] Failed to import PyFingerprint: {e}.")
    PyFingerprint = None

try:
    from insightface.app import FaceAnalysis
except ImportError:
    print("[WARN] insightface library not found.")
    FaceAnalysis = None
except Exception as e:
    print(f"[WARN] Failed to import insightface: {e}.")
    FaceAnalysis = None

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FINGERPRINT_PORT = '/dev/ttyAMA0'
DEFAULT_FINGERPRINT_BAUDRATE = 57600
CAMERA_INDEX = 0
CAMERA_WIDTH = 320
CAMERA_HEIGHT = 240

MQTT_BROKER = os.getenv("MQTT_BROKER", "p06299ce.ala.eu-central-1.emqxsl.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_CLIENT_ID = f"reg-device-sim-hc-token-{os.urandom(4).hex()}"
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "studentaccount")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "studentaccount")

DEVICE_REGISTER_TOPIC = "iot/devices/register_device"
SERVER_REGISTER_RESP_TOPIC = "iot/server/register_device_resp"
DEVICE_SYNC_REQUEST_TOPIC = "iot/devices/device_sync_bio"
SERVER_PUSH_BIO_TOPIC_TPL = "iot/server/{mac_address}/push_biometric"

HARDCODED_ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJEZXZpY2VUZXN0MDAwMSIsImV4cCI6MTc0MjgxMTY3NCwiaXNzIjoicnVsZWVuZ2luZSIsImF1ZCI6ImRldmljZXMifQ.rUzJVjQKvNLhFnrpEpeF335twuuiEKWLw1nesor9WTY"

registered_devices_sim = {}
device_biometric_data_sim = {}

def generate_hashed_password(mac):
    data = (mac + "navis@salt").encode("utf-8")
    hash_bytes = hashlib.sha256(data).digest()
    return base64.b64encode(hash_bytes).decode("utf-8")

def verify_password(mac_address, provided_hash):
    expected_hash = generate_hashed_password(mac_address)
    return expected_hash == provided_hash

class RegistrationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Thiết bị Đăng ký + Server Sim (Hardcoded Token)")
        self.root.geometry("700x980")
        self.fingerprint_sensor = None
        self.fingerprint_template_data = None
        self.face_app = None
        self.camera = None
        self.face_embedding = None
        self.face_image_data = None
        self.camera_running = False
        self.capture_in_progress = False
        self.last_detected_face_info = None
        self.last_detected_face_frame = None
        self.mqtt_client = None
        self.mqtt_connected = False
        self.is_shutting_down = False
        self.registered_devices_sim = registered_devices_sim
        self.device_biometric_data_sim = device_biometric_data_sim
        self.setup_mqtt_client()
        self.setup_ui()
        self.initialize_fingerprint_sensor()
        self.initialize_face_components()
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup)

    def setup_mqtt_client(self):
        try:
            self.mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID)
            if MQTT_USERNAME and MQTT_PASSWORD:
                self.mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
            self.mqtt_client.on_connect = self.on_mqtt_connect
            self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
            self.mqtt_client.on_message = self.on_mqtt_message
            print(f"Connecting to MQTT Broker: {MQTT_BROKER}:{MQTT_PORT}...")
            self.mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, 60)
            self.mqtt_client.loop_start()
        except Exception as e:
            print(f"[ERROR] Failed to setup MQTT Client: {e}")
            messagebox.showerror("Lỗi MQTT", f"Không thể khởi tạo MQTT Client: {e}")

    def on_mqtt_connect(self, client, userdata, flags, rc):
        if self.is_shutting_down:
            print("[INFO] App is shutting down, skipping on_connect UI update.")
            return

        if rc == 0:
            print("MQTT Client Connected Successfully (Dual Role).")
            self.mqtt_connected = True
            client.subscribe(DEVICE_REGISTER_TOPIC)
            print(f"Subscribed to: {DEVICE_REGISTER_TOPIC}")
            client.subscribe(DEVICE_SYNC_REQUEST_TOPIC)
            print(f"Subscribed to: {DEVICE_SYNC_REQUEST_TOPIC}")
            if hasattr(self, 'mqtt_status_label'):
                if self.mqtt_status_label and self.mqtt_status_label.winfo_exists():
                    self.root.after(0, self.update_ui_status, self.mqtt_status_label, "MQTT: Đã kết nối", "green")
        else:
            print(f"MQTT Connection Failed. Code: {rc}")
            self.mqtt_connected = False
            if hasattr(self, 'mqtt_status_label'):
                if self.mqtt_status_label and self.mqtt_status_label.winfo_exists():
                    self.root.after(0, self.update_ui_status, self.mqtt_status_label, "MQTT: Mất kết nối", "red")

    def on_mqtt_disconnect(self, client, userdata, rc):
        if self.is_shutting_down:
            print("[INFO] App is shutting down, skipping on_disconnect UI update.")
            return

        print(f"MQTT Client Disconnected. Code: {rc}")
        self.mqtt_connected = False
        if hasattr(self, 'mqtt_status_label'):
            if self.mqtt_status_label and self.mqtt_status_label.winfo_exists():
                self.root.after(0, self.update_ui_status, self.mqtt_status_label, "MQTT: Mất kết nối", "red")

    def on_mqtt_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload_str = msg.payload.decode("utf-8")
            print(f"\n[Server Sim] Received message on topic '{topic}': {payload_str[:200]}...")
            payload_dict = json.loads(payload_str)
            if topic == DEVICE_REGISTER_TOPIC:
                self.handle_registration_request(payload_dict)
            elif topic == DEVICE_SYNC_REQUEST_TOPIC:
                self.handle_sync_request(payload_dict)
            else:
                print(f"[Server Sim] Received message on unhandled topic: {topic}")
        except Exception as e:
            print(f"[Server Sim ERROR] Error processing message on topic {topic}: {e}")

    def handle_registration_request(self, payload):
        mac_address = payload.get("MacAddress")
        hashed_password_device = payload.get("HashedPassword")
        if not mac_address or not hashed_password_device:
            return

        print(f"[Server Sim] Handling registration for {mac_address}...")
        if verify_password(mac_address, hashed_password_device):
            print(f"[Server Sim] Password OK for {mac_address}.")
            self.registered_devices_sim[mac_address] = {"hashed_password": hashed_password_device, "registered_at": datetime.now(timezone.utc)}

            access_token = HARDCODED_ACCESS_TOKEN
            response_payload = {
                "MacAddress": mac_address,
                "AccessToken": access_token,
                "Status": "Success"
            }
            print(f"[INFO] Device {mac_address} registered successfully (Hardcoded Token).")
            self.mqtt_client.publish(SERVER_REGISTER_RESP_TOPIC, json.dumps(response_payload), qos=1)
            print(f"Published registration response (Hardcoded Token) to {SERVER_REGISTER_RESP_TOPIC} for {mac_address}")
        else:
            print(f"[Server Sim WARN] Password verification failed for {mac_address}")

    def handle_sync_request(self, payload):
        mac_address = payload.get("macadddress")
        if not mac_address:
            return

        print(f"[Server Sim] Handling sync request from {mac_address}...")

        all_data = self.get_all_biometric_data_for_device_sim(mac_address)
        if not all_data:
            sync_payload = [{"cmdType": "SYNC_ALL", "bioId": f"SYNC_EMPTY_{time.time()}"}]
        else:
            sync_payload = [{"cmdType": "SYNC_ALL", "bioId": f"SYNC_FULL_{time.time()}"}] + all_data

        target_topic = SERVER_PUSH_BIO_TOPIC_TPL.format(mac_address=mac_address)
        self.mqtt_client.publish(target_topic, json.dumps(sync_payload), qos=1)
        print(f"[Server Sim] Published full sync data ({len(sync_payload)} commands) to {target_topic}")

    def get_all_biometric_data_for_device_sim(self, mac_address):
        data_to_send = []
        device_data = self.device_biometric_data_sim.get(mac_address, {})
        for bio_id, data in device_data.items():
            new_data = data.copy()
            new_data["cmdType"] = "PUSH_NEW_BIO"
            data_to_send.append(new_data)
        print(f"[Server Sim] Found {len(data_to_send)} records in sim DB for {mac_address}")
        return data_to_send

    def initialize_fingerprint_sensor(self):
        if PyFingerprint is None: 
            return
        try:
            print(f"Khởi tạo cảm biến vân tay trên {DEFAULT_FINGERPRINT_PORT}...")
            self.fingerprint_sensor = PyFingerprint(DEFAULT_FINGERPRINT_PORT, DEFAULT_FINGERPRINT_BAUDRATE, 0xFFFFFFFF, 0x00000000)

            if self.fingerprint_sensor.verifyPassword():
                print("Cảm biến vân tay đã sẵn sàng.")
                self.status_label_fp.configure(text="Cảm biến FP: Sẵn sàng", text_color="green")
                self.enroll_finger_button.configure(state="normal")
            else:
                messagebox.showerror("Lỗi Cảm biến FP", "Không thể xác thực với cảm biến vân tay.")
                self.status_label_fp.configure(text="Cảm biến FP: Lỗi xác thực", text_color="red")
                self.fingerprint_sensor = None

        except Exception as e:
            messagebox.showerror("Lỗi Cảm biến FP", f"Không thể khởi tạo cảm biến: {e}")
            self.status_label_fp.configure(text=f"Cảm biến FP: Lỗi", text_color="red")
            self.fingerprint_sensor = None

    def initialize_face_components(self):
        if FaceAnalysis:
            try:
                print("Khởi tạo model InsightFace...")
                self.face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
                self.face_app.prepare(ctx_id=-1)
                print("Model InsightFace đã sẵn sàng.")
                self.status_label_face_model.configure(text="Model Face: Sẵn sàng", text_color="green")
            except Exception as e:
                print(f"Lỗi khởi tạo InsightFace: {e}")
                messagebox.showerror("Lỗi Model Face", f"Không thể khởi tạo model InsightFace: {e}")
                self.status_label_face_model.configure(text="Model Face: Lỗi", text_color="red")
        else:
            self.status_label_face_model.configure(text="Model Face: Chưa cài đặt", text_color="orange")

        try:
            print(f"Mở camera index {CAMERA_INDEX}...")
            self.camera = cv2.VideoCapture(CAMERA_INDEX)
            if not self.camera.isOpened():
                raise ValueError(f"Không thể mở camera index {CAMERA_INDEX}")
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
            print("Camera đã sẵn sàng.")
            self.status_label_cam.configure(text="Camera: Sẵn sàng", text_color="green")
            if self.face_app:
                self.enroll_face_button.configure(state="normal")
        except Exception as e:
            print(f"Lỗi mở camera: {e}")
            messagebox.showerror("Lỗi Camera", f"Không thể mở camera: {e}")
            self.status_label_cam.configure(text="Camera: Lỗi", text_color="red")
            if self.camera:
                self.camera.release()
            self.camera = None

    def setup_ui(self):
        main_frame = ctk.CTkScrollableFrame(self.root)
        main_frame.pack(pady=10, padx=10, fill="both", expand=True)
        row_idx = 0
        ctk.CTkLabel(main_frame, text="Thông tin Người dùng", font=ctk.CTkFont(size=16, weight="bold")).grid(row=row_idx, column=0, columnspan=2, pady=(0, 10), sticky="w")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="BioID (Mã NV/Khách):*").grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
        self.bio_id_entry = ctk.CTkEntry(main_frame, width=300)
        self.bio_id_entry.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="Tên Người dùng:").grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
        self.name_entry = ctk.CTkEntry(main_frame, width=300)
        self.name_entry.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="Số CCCD/ID:").grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
        self.id_number_entry = ctk.CTkEntry(main_frame, width=300)
        self.id_number_entry.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="Lịch trình Hiệu lực", font=ctk.CTkFont(size=16, weight="bold")).grid(row=row_idx, column=0, columnspan=2, pady=(15, 10), sticky="w")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="Từ Ngày (YYYY-MM-DD):").grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
        self.from_date_entry = ctk.CTkEntry(main_frame, placeholder_text="Để trống nếu không giới hạn")
        self.from_date_entry.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="Đến Ngày (YYYY-MM-DD):").grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
        self.to_date_entry = ctk.CTkEntry(main_frame, placeholder_text="Để trống nếu không giới hạn")
        self.to_date_entry.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="Từ Giờ (HH:MM:SS):").grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
        self.from_time_entry = ctk.CTkEntry(main_frame, placeholder_text="00:00:00")
        self.from_time_entry.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="Đến Giờ (HH:MM:SS):").grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
        self.to_time_entry = ctk.CTkEntry(main_frame, placeholder_text="23:59:59")
        self.to_time_entry.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="Ngày hoạt động:").grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
        self.days_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        self.days_frame.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")
        row_idx += 1
        self.day_vars = []
        days = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        for i, day in enumerate(days):
            var = StringVar(value="1")
            chk = ctk.CTkCheckBox(self.days_frame, text=day, variable=var, onvalue="1", offvalue="0")
            chk.pack(side=LEFT, padx=5)
            self.day_vars.append(var)
        ctk.CTkLabel(main_frame, text="Sinh trắc học (* cần ít nhất 1)", font=ctk.CTkFont(size=16, weight="bold")).grid(row=row_idx, column=0, columnspan=2, pady=(15, 5), sticky="w")
        row_idx += 1
        fp_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        fp_frame.grid(row=row_idx, column=0, columnspan=2, sticky="ew", pady=5)
        row_idx += 1
        self.enroll_finger_button = ctk.CTkButton(fp_frame, text="Bắt đầu Đăng ký Vân tay", state="disabled", command=self.start_fingerprint_enrollment_thread)
        self.enroll_finger_button.pack(pady=(0,5))
        self.fingerprint_status_label = ctk.CTkLabel(fp_frame, text="Trạng thái vân tay: Chưa đăng ký", text_color="gray")
        self.fingerprint_status_label.pack(pady=2)
        face_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        face_frame.grid(row=row_idx, column=0, columnspan=2, sticky="ew", pady=10)
        row_idx += 1
        self.camera_feed_label = ctk.CTkLabel(face_frame, text="Camera Feed", width=CAMERA_WIDTH, height=CAMERA_HEIGHT, fg_color="black", text_color="white")
        self.camera_feed_label.pack(pady=5)
        face_buttons_frame = ctk.CTkFrame(face_frame, fg_color="transparent")
        face_buttons_frame.pack(pady=(5,0))
        self.enroll_face_button = ctk.CTkButton(face_buttons_frame, text="Bắt đầu Đăng ký Khuôn mặt", state="disabled", command=self.start_face_enrollment_thread)
        self.enroll_face_button.pack(side=LEFT, padx=5)
        self.capture_face_button = ctk.CTkButton(face_buttons_frame, text="Chụp ảnh", state="disabled", command=self.capture_face_action)
        self.capture_face_button.pack(side=LEFT, padx=5)
        self.face_status_label = ctk.CTkLabel(face_frame, text="Trạng thái khuôn mặt: Chưa đăng ký", text_color="gray")
        self.face_status_label.pack(pady=2)
        ctk.CTkLabel(main_frame, text="Gửi Lệnh Đến Thiết Bị", font=ctk.CTkFont(size=16, weight="bold")).grid(row=row_idx, column=0, columnspan=2, pady=(15, 5), sticky="w")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="MAC Address Thiết bị đích:*").grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
        self.target_mac_entry = ctk.CTkEntry(main_frame, placeholder_text="AA:BB:CC:11:22:33", width=300)
        self.target_mac_entry.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")
        row_idx += 1
        ctk.CTkLabel(main_frame, text="Loại Lệnh:*").grid(row=row_idx, column=0, padx=5, pady=5, sticky="w")
        self.command_type_var = StringVar(value="PUSH_NEW_BIO")
        command_options = ["PUSH_NEW_BIO", "PUSH_UPDATE_BIO", "PUSH_DELETE_BIO", "SYNC_ALL"]
        self.command_type_menu = ctk.CTkOptionMenu(main_frame, variable=self.command_type_var, values=command_options, width=300)
        self.command_type_menu.grid(row=row_idx, column=1, padx=5, pady=5, sticky="ew")
        row_idx += 1
        status_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        status_frame.grid(row=row_idx, column=0, columnspan=2, sticky="ew", pady=10)
        row_idx += 1
        self.status_label_fp = ctk.CTkLabel(status_frame, text="Cảm biến FP: ...", text_color="orange")
        self.status_label_fp.pack(side=LEFT, padx=5)
        self.status_label_face_model = ctk.CTkLabel(status_frame, text="Model Face: ...", text_color="orange")
        self.status_label_face_model.pack(side=LEFT, padx=5)
        self.status_label_cam = ctk.CTkLabel(status_frame, text="Camera: ...", text_color="orange")
        self.status_label_cam.pack(side=LEFT, padx=5)
        self.mqtt_status_label = ctk.CTkLabel(status_frame, text="MQTT: ...", text_color="orange")
        self.mqtt_status_label.pack(side=LEFT, padx=5)
        self.send_button = ctk.CTkButton(main_frame, text="Đóng gói và Gửi Lệnh", command=self.prepare_and_send_data)
        self.send_button.grid(row=row_idx, column=0, columnspan=2, padx=5, pady=20)
        row_idx += 1
        main_frame.grid_columnconfigure(1, weight=1)

    def start_fingerprint_enrollment_thread(self):
        self.fingerprint_template_data = None
        self.update_ui_status(self.fingerprint_status_label, "...")
        self.enroll_finger_button.configure(state="disabled")
        threading.Thread(target=self.enroll_fingerprint_process, daemon=True).start()

    def enroll_fingerprint_process(self):
        if not self.fingerprint_sensor:
            self.update_ui_status(self.fingerprint_status_label, "Lỗi: Cảm biến không sẵn sàng.", "red")
            self.root.after(0, self.enroll_finger_button.configure, {"state": "normal"})
            return
        try:
            self.update_ui_status(self.fingerprint_status_label, "Đặt ngón tay (Lần 1)...", "blue")
            while not self.fingerprint_sensor.readImage():
                time.sleep(0.1)

            self.fingerprint_sensor.convertImage(FINGERPRINT_CHARBUFFER1)
            self.update_ui_status(self.fingerprint_status_label, "Nhấc ngón tay ra.", "orange")
            time.sleep(1)

            self.update_ui_status(self.fingerprint_status_label, "Đặt lại cùng ngón tay (Lần 2)...", "blue")
            while not self.fingerprint_sensor.readImage():
                time.sleep(0.1)

            self.fingerprint_sensor.convertImage(FINGERPRINT_CHARBUFFER2)

            compare_score = self.fingerprint_sensor.compareCharacteristics()
            print(f"[DEBUG] Fingerprint comparison score: {compare_score}")
            if compare_score < 40:
                self.update_ui_status(self.fingerprint_status_label, f"Lỗi: Hai lần quét không khớp (Score: {compare_score}). Thử lại.", "red")
                raise ValueError("Fingerprint scans did not match sufficiently.")

            self.update_ui_status(self.fingerprint_status_label, "Đang tạo mẫu vân tay...", "blue")
            if not self.fingerprint_sensor.createTemplate():
                raise RuntimeError("Failed to create fingerprint template on sensor.")

            self.fingerprint_template_data = self.fingerprint_sensor.downloadCharacteristics(FINGERPRINT_CHARBUFFER1)
            if not self.fingerprint_template_data:
                raise RuntimeError("Failed to download fingerprint template from sensor.")

            self.update_ui_status(self.fingerprint_status_label, "Đăng ký vân tay thành công!", "green")
            print(f"[DEBUG] Fingerprint template captured (type: {type(self.fingerprint_template_data)}, length: {len(self.fingerprint_template_data) if self.fingerprint_template_data else 0})")

        except Exception as e:
            error_msg = f"Lỗi đăng ký vân tay: {e}"
            print(f"[ERROR] {error_msg}")
            self.update_ui_status(self.fingerprint_status_label, error_msg, "red")
            self.fingerprint_template_data = None

        finally:
            self.root.after(0, self.enroll_finger_button.configure, {"state": "normal"})

    def start_face_enrollment_thread(self):
        if not self.camera or not self.camera.isOpened() or not self.face_app:
            messagebox.showerror("Lỗi", "Camera hoặc model nhận diện khuôn mặt chưa sẵn sàng.")
            return
        self.face_embedding = None
        self.face_image_data = None
        self.last_detected_face_info = None
        self.capture_in_progress = True
        self.update_ui_status(self.face_status_label, "Đang mở camera...", "blue")
        self.enroll_face_button.configure(state="disabled")
        self.capture_face_button.configure(state="disabled")
        threading.Thread(target=self.enroll_face_process, daemon=True).start()

    def enroll_face_process(self):
        try:
            while self.capture_in_progress is True:
                if not self.camera or not self.camera.isOpened():
                    self.update_ui_status(self.face_status_label, "Lỗi: Mất kết nối camera.", "red")
                    break
                ret, frame = self.camera.read()
                if not ret:
                    self.update_ui_status(self.face_status_label, "Lỗi: Không đọc được frame.", "red")
                    time.sleep(0.5)
                    continue
                try:
                    faces = self.face_app.get(frame)
                except Exception as face_e:
                    print(f"[ERROR] Lỗi khi gọi face_app.get: {face_e}")
                    faces = []
                display_frame = frame.copy()
                status_text = "Đưa khuôn mặt vào khung"
                status_color = "orange"
                can_capture = False
                if faces and len(faces) == 1:
                    face_info = faces[0]
                    bbox = face_info.bbox.astype(int)
                    cv2.rectangle(display_frame, (bbox[0], bbox[1]), (bbox[2], bbox[3]), (0, 255, 0), 2)
                    status_text = "Phát hiện khuôn mặt. Nhấn 'Chụp ảnh'."
                    status_color = "blue"
                    can_capture = True
                    self.last_detected_face_info = face_info
                    self.last_detected_face_frame = frame
                elif faces and len(faces) > 1:
                    status_text = "Lỗi: Nhiều khuôn mặt!"
                    status_color = "red"
                    can_capture = False
                    self.last_detected_face_info = None
                else:
                    status_text = "Không tìm thấy khuôn mặt"
                    status_color = "gray"
                    can_capture = False
                    self.last_detected_face_info = None
                self.update_ui_status(self.face_status_label, status_text, status_color)
                self.root.after(0, self.capture_face_button.configure, {"state": "normal" if can_capture else "disabled"})
                self.update_camera_feed(display_frame)
                time.sleep(0.05)
        except Exception as e:
            error_msg = f"Lỗi trong luồng camera: {e}"
            print(f"[ERROR] {error_msg}")
            self.update_ui_status(self.face_status_label, error_msg, "red")
        finally:
            print("[INFO] Luồng camera/face enrollment kết thúc.")
            if self.capture_in_progress is not False:
                self.root.after(0, self.capture_face_button.configure, {"state": "disabled"})
                self.root.after(0, self.enroll_face_button.configure, {"state": "normal"})
            self.capture_in_progress = False

    def capture_face_action(self):
        if self.last_detected_face_info and self.last_detected_face_frame is not None:
            print("[INFO] Nút Chụp ảnh được nhấn.")
            self.capture_in_progress = False
            try:
                self.face_embedding = self.last_detected_face_info.embedding.astype(np.float32)
                ret, buf = cv2.imencode('.jpg', self.last_detected_face_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                if not ret:
                    raise ValueError("Không thể mã hóa ảnh JPG.")
                self.face_image_data = buf.tobytes()
                self.update_ui_status(self.face_status_label, "Đã chụp khuôn mặt thành công!", "green")
                print(f"[DEBUG] Face embedding captured")
                print(f"[DEBUG] Face image captured")
                captured_img_pil = Image.open(io.BytesIO(self.face_image_data))
                captured_ctk = ctk.CTkImage(light_image=captured_img_pil, size=(CAMERA_WIDTH, CAMERA_HEIGHT))
                self.root.after(0, self.camera_feed_label.configure, {"image": captured_ctk, "text": ""})
            except Exception as e:
                error_msg = f"Lỗi khi xử lý ảnh chụp: {e}"
                print(f"[ERROR] {error_msg}")
                self.update_ui_status(self.face_status_label, error_msg, "red")
                self.face_embedding = None
                self.face_image_data = None
            finally:
                self.root.after(0, self.enroll_face_button.configure, {"state": "normal"})
                self.root.after(0, self.capture_face_button.configure, {"state": "disabled"})
        else:
            print("[WARN] Nút Chụp ảnh được nhấn nhưng không có dữ liệu khuôn mặt.")
            self.update_ui_status(self.face_status_label, "Lỗi: Không có khuôn mặt để chụp.", "orange")

    def update_camera_feed(self, frame):
        if hasattr(self, 'camera_feed_label') and self.camera_feed_label.winfo_exists():
            try:
                img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img_pil = Image.fromarray(img_rgb)
                ctk_img = ctk.CTkImage(light_image=img_pil, size=(CAMERA_WIDTH, CAMERA_HEIGHT))
                self.root.after(0, self.camera_feed_label.configure, {"image": ctk_img, "text": ""})
            except Exception as e:
                pass

    def update_ui_status(self, label_widget, text, color="gray"):
        if label_widget and label_widget.winfo_exists():
            color_map = {"red": "red", "green": "green", "blue": "#3B8ED0", "orange": "orange", "gray": "gray"}
            text_color = color_map.get(color, "gray")
            self.root.after(0, label_widget.configure, {"text": text, "text_color": text_color})

    def get_active_days_mask(self):
        return "".join(var.get() for var in self.day_vars)

    def prepare_and_send_data(self):
        bio_id = self.bio_id_entry.get().strip()
        person_name = self.name_entry.get().strip()
        id_number = self.id_number_entry.get().strip()
        from_date = self.from_date_entry.get().strip() or None
        to_date = self.to_date_entry.get().strip() or None
        from_time = self.from_time_entry.get().strip() or None
        to_time = self.to_time_entry.get().strip() or None
        active_days = self.get_active_days_mask()
        target_mac = self.target_mac_entry.get().strip()
        command_type = self.command_type_var.get()

        if not bio_id:
            messagebox.showerror("Lỗi", "Vui lòng nhập BioID.")
            return
        if not target_mac:
            messagebox.showerror("Lỗi", "Vui lòng nhập MAC Address đích.")
            return
        if not person_name:
            person_name = bio_id

        bio_datas = []
        if self.fingerprint_template_data:
            try:
                template_bytes = bytes(self.fingerprint_template_data)
                template_b64 = base64.b64encode(template_bytes).decode('utf-8')
                bio_datas.append({"BioType": "FINGER", "Template": template_b64, "Img": None})
            except Exception as e:
                messagebox.showerror("Lỗi Đóng gói", f"Lỗi vân tay: {e}")
                return
        if self.face_embedding is not None and self.face_image_data is not None:
            try:
                face_template_b64 = base64.b64encode(self.face_embedding.tobytes()).decode('utf-8')
                face_image_b64 = base64.b64encode(self.face_image_data).decode('utf-8')
                bio_datas.append({"BioType": "FACE", "Template": face_template_b64, "Img": face_image_b64})
            except Exception as e:
                messagebox.showerror("Lỗi Đóng gói", f"Lỗi khuôn mặt: {e}")
                return

        if command_type in ["PUSH_NEW_BIO", "PUSH_UPDATE_BIO"] and not bio_datas:
            messagebox.showerror("Lỗi", f"Lệnh {command_type} cần Vân tay hoặc Khuôn mặt.")
            return
        if command_type in ["PUSH_DELETE_BIO", "SYNC_ALL"]:
            bio_datas = []

        command_payload = {
            "bioId": bio_id,
            "idNumber": id_number if id_number else None,
            "cmdType": command_type,
            "bioDatas": bio_datas,
            "fromDate": from_date,
            "toDate": to_date,
            "fromTime": from_time,
            "toTime": to_time,
            "activeDays": active_days
        }
        final_payload_list = [command_payload]
        self.update_simulator_data(target_mac, command_payload)
        self.publish_to_device(target_mac, final_payload_list)

    def update_simulator_data(self, mac_address, command_data):
        cmd_type = command_data.get("cmdType")
        bio_id = command_data.get("bioId")
        if mac_address not in self.device_biometric_data_sim:
            self.device_biometric_data_sim[mac_address] = {}
        if bio_id:
            if cmd_type in ["PUSH_NEW_BIO", "PUSH_UPDATE_BIO"]:
                self.device_biometric_data_sim[mac_address][bio_id] = command_data
                print(f"[SIM DB] Updated/Added bioId {bio_id} for {mac_address}")
            elif cmd_type == "PUSH_DELETE_BIO":
                if bio_id in self.device_biometric_data_sim.get(mac_address, {}):
                    del self.device_biometric_data_sim[mac_address][bio_id]
                    print(f"[SIM DB] Deleted bioId {bio_id} for {mac_address}")
        elif cmd_type == "SYNC_ALL":
            self.device_biometric_data_sim[mac_address] = {}
            print(f"[SIM DB] Cleared data for {mac_address}")
            if command_data.get('bioDatas') and bio_id:
                self.device_biometric_data_sim[mac_address][bio_id] = command_data

    def publish_to_device(self, target_mac, payload_list):
        if not self.mqtt_connected:
            messagebox.showerror("Lỗi MQTT", "Chưa kết nối MQTT Broker.")
            return
        if not target_mac:
            messagebox.showerror("Lỗi", "Chưa nhập MAC đích.")
            return
        try:
            target_topic = SERVER_PUSH_BIO_TOPIC_TPL.format(mac_address=target_mac)
            payload_json = json.dumps(payload_list)
            print(f"--- Publishing to Topic: {target_topic} ---")
            print(payload_json)
            print("----------------------------------------------")
            result, mid = self.mqtt_client.publish(target_topic, payload=payload_json, qos=1)
            if result == mqtt.MQTT_ERR_SUCCESS:
                messagebox.showinfo("Thành công", f"Đã gửi lệnh đến {target_mac}!")
            else:
                messagebox.showerror("Lỗi MQTT", f"Gửi lệnh thất bại. Code: {result}")
        except Exception as e:
            messagebox.showerror("Lỗi Gửi Lệnh", f"Lỗi MQTT: {e}")

    def clear_inputs(self):
        self.bio_id_entry.delete(0, "end")
        self.name_entry.delete(0, "end")
        self.id_number_entry.delete(0, "end")
        self.from_date_entry.delete(0, "end")
        self.to_date_entry.delete(0, "end")
        self.from_time_entry.delete(0, "end")
        self.to_time_entry.delete(0, "end")
        for var in self.day_vars:
            var.set("1")
        self.fingerprint_template_data = None
        self.update_ui_status(self.fingerprint_status_label, "Trạng thái vân tay: Chưa đăng ký", "gray")
        self.face_embedding = None
        self.face_image_data = None
        self.last_detected_face_info = None
        self.last_detected_face_frame = None
        self.update_ui_status(self.face_status_label, "Trạng thái khuôn mặt: Chưa đăng ký", "gray")
        if hasattr(self, 'camera_feed_label'):
            self.root.after(0, self.camera_feed_label.configure, {"image": None, "text": "Camera Feed"})

    def cleanup(self):
        print("Đang dọn dẹp tài nguyên...")
        self.is_shutting_down = True
        self.capture_in_progress = False  # Set to False (Boolean) instead of string
        time.sleep(0.2)
        if self.camera and self.camera.isOpened():
            print("Giải phóng camera...")
            self.camera.release()
        if self.mqtt_client:
            print("Ngắt kết nối MQTT...")
            try:
                self.mqtt_client.disconnect()
                self.mqtt_client.loop_stop()
            except Exception as mqtt_e:
                print(f"Error during MQTT cleanup disconnect: {mqtt_e}")
        print("Đóng ứng dụng đăng ký/sim.")
        try:
            if self.root and self.root.winfo_exists():
                self.root.destroy()
        except Exception as tk_e:
            print(f"Error destroying Tkinter root: {tk_e}")

if __name__ == "__main__":
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    app = RegistrationApp(root)
    root.mainloop()