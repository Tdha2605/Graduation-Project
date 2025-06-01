import os
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

import json
import uuid
import customtkinter as ctk
from tkinter import messagebox
from PIL import Image
from customtkinter import CTkImage
from datetime import datetime, timezone, timedelta, time as dt_time, date as dt_date
import io
import base64
import calendar
import requests

import face_enroll
import fingerprint_enroll
from mqtt_enroll import MQTTEnrollManager, generate_hashed_password # Import generate_hashed_password
import database_enroll
import rfid_enroll

try:
    from pyfingerprint.pyfingerprint import PyFingerprint
except ImportError:
    PyFingerprint = None
except Exception as e: 
    if "DEBUG" in globals() and DEBUG: print(f"[Enroll PYFINGERPRINT IMPORT ERROR] {e}")
    PyFingerprint = None

try:
    import board
    import busio
    from adafruit_pn532.i2c import PN532_I2C
except ImportError:
    PN532_I2C = None
    board = None
    busio = None
except Exception as e_pn532_import: 
    if "DEBUG" in globals() and DEBUG: print(f"[Enroll PN532 IMPORT ERROR] {e_pn532_import}")
    PN532_I2C = None
    board = None
    busio = None

DEBUG = True
BG_COLOR = "#F0F0F0"
SCREEN_BG_COLOR = "#E0E0E0"
ACCENT_COLOR = "#007AFF"
BUTTON_FG_TEXT = "#FFFFFF"
SUCCESS_COLOR = "#34C759"
WARNING_COLOR = "#FF9500"
ERROR_COLOR = "#FF3B30"

TITLE_FONT = ("Segoe UI", 26, "bold")
STEP_TITLE_FONT = ("Segoe UI", 22, "bold")
LABEL_FONT = ("Segoe UI", 16)
INPUT_FONT = ("Segoe UI", 16)
BUTTON_FONT = ("Segoe UI", 18, "bold")
SMALL_STATUS_FONT = ("Segoe UI", 13)
OPTION_MENU_FONT = ("Segoe UI", 15)
OPTION_MENU_DROPDOWN_FONT = ("Segoe UI", 14)

LARGE_BUTTON_WIDTH = 250
MEDIUM_BUTTON_WIDTH = 180
LARGE_BUTTON_HEIGHT = 65
MEDIUM_BUTTON_HEIGHT = 50
ENTRY_HEIGHT = 42
OPTION_MENU_HEIGHT = 42
OPTION_MENU_WIDTH_S = 75
OPTION_MENU_WIDTH_M = 100

icon_size_large_button_step2 = (200, 200)
img_size_status = (28, 28)
icon_size_nav_button = (20,20)
icon_size_send_button = (30,30)

PAD_X_MAIN_CONTAINER = 20
PAD_Y_MAIN_CONTAINER = 15
PAD_X_SECTION = 12
PAD_Y_SECTION = 8
PAD_X_WIDGET_HORIZONTAL = 5
PAD_Y_WIDGET_VERTICAL = 4

WINDOW_WIDTH = 1024
WINDOW_HEIGHT = 600

CONFIG_FILE = "mqtt_enroll_config.json"
HEALTHCHECK_INTERVAL_MS = 10000

FINGERPRINT_PORT = '/dev/ttyAMA4'
FINGERPRINT_BAUDRATE = 57600
RFID_RESET_PIN_BCM = None
RFID_IRQ_PIN_BCM = None
GMT_PLUS_7 = timezone(timedelta(hours=7))

def get_hour_values(): return [f"{h:02d}" for h in range(24)]
def get_minute_second_values(): return [f"{m:02d}" for m in range(60)]
def get_year_values(start_offset=-2, end_offset=5):
    current_year = datetime.now().year
    return [str(y) for y in range(current_year + start_offset, current_year + end_offset + 1)]
def get_month_values(): return [f"{m:02d}" for m in range(1, 13)]
def get_day_values(year_str, month_str):
    try:
        year = int(year_str); month = int(month_str)
        num_days = calendar.monthrange(year, month)[1]
        return [f"{d:02d}" for d in range(1, num_days + 1)]
    except (ValueError, TypeError): return [f"{d:02d}" for d in range(1, 32)]

def get_mac_address():
    mac = uuid.getnode()
    return ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))

def load_image_ctk(path, size):
    try:
        full_path = os.path.join(script_dir, path)
        if not os.path.exists(full_path):
            if DEBUG: print(f"[Load Image WARN] Image file not found: {full_path}"); return None
        img = Image.open(full_path)
        if size: img.thumbnail(size, Image.Resampling.LANCZOS)
        return CTkImage(light_image=img, dark_image=img, size=img.size)
    except Exception as e:
        if DEBUG: print(f"[Load Image ERROR] Failed to load {path}: {e}"); return None

class EnrollmentApp:
    def __init__(self, root):
        self.root = root
        self.enroll_mac = get_mac_address()
        if DEBUG: print("[Enroll DEBUG] Enrollment Device MAC Address:", self.enroll_mac)

        try: database_enroll.initialize_database()
        except Exception as e_db:
            messagebox.showerror("Database Error", f"Failed to initialize database: {e_db}\nApplication will exit."); root.quit(); return

        self.discovered_rooms_macs = database_enroll.get_all_discovered_devices()
        if DEBUG: print(f"[Enroll DEBUG] Loaded initially discovered rooms/devices: {self.discovered_rooms_macs}")

        self.current_bio_id = None; self.current_id_number = ""; self.current_person_name = ""; self.current_room_name_selected = None
        now = datetime.now()
        self.from_hour_str = "00"; self.from_minute_str = "00"; self.from_second_str = "00"
        self.from_day_str = now.strftime("%d"); self.from_month_str = now.strftime("%m"); self.from_year_str = now.strftime("%Y")
        to_dt = now + timedelta(days=6)
        self.to_hour_str = "23"; self.to_minute_str = "59"; self.to_second_str = "59"
        self.to_day_str = to_dt.strftime("%d"); self.to_month_str = to_dt.strftime("%m"); self.to_year_str = to_dt.strftime("%Y")
        self.active_day_mask_list = [True] * 7

        self.current_face_image_b64 = None; 
        self.current_face_template_b64 = None
        self.current_finger_imgage_b64 = ""
        self.current_finger_template_b64 = None; 
        self.current_rfid_uid_str = None
        self.preview_face_image_ctk = None

        self.fetched_schedule_data = None
        self.current_cccd_for_schedule = ""

        self.http_api_token = None
        self.http_api_token_expiry = None

        self.mqtt_manager = None; self.mqtt_config = {}
        self.config_path = os.path.join(script_dir, CONFIG_FILE)
        self.screen_history = []

        self.fingerprint_sensor = None; self.rfid_sensor = None

        self.connection_status_label = None; self.bg_label = None
        self.main_frame = None; self.config_btn_ref = None
        self.nav_frame = None

        self.connected_image = load_image_ctk("images/connected.jpg", img_size_status)
        self.disconnected_image = load_image_ctk("images/disconnected.jpg", img_size_status)
        self.bg_photo = load_image_ctk("images/background_enroll.jpeg", (WINDOW_WIDTH, WINDOW_HEIGHT))
        self.face_icon_large = load_image_ctk("images/face.png", icon_size_large_button_step2)
        self.fingerprint_icon_large = load_image_ctk("images/fingerprint.png", icon_size_large_button_step2)
        self.rfid_icon_large = load_image_ctk("images/rfid.png", icon_size_large_button_step2)
        self.next_icon = load_image_ctk("images/next_arrow.png", icon_size_nav_button)
        self.back_icon = load_image_ctk("images/back_arrow.png", icon_size_nav_button)
        self.send_icon_large = load_image_ctk("images/send.png", icon_size_send_button)

        self.root.configure(fg_color=BG_COLOR)
        self.show_background()
        self.connection_status_label = ctk.CTkLabel(root, image=self.disconnected_image, text="Chưa kết nối MQTT", font=("Segoe UI", 10), text_color=ERROR_COLOR, compound="left")
        self.connection_status_label.place(relx=0.01, rely=0.98, anchor="sw")
        self.create_config_button()

        self.initialize_fingerprint_sensor()
        self.initialize_rfid_sensor()

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f: self.mqtt_config = json.load(f)
                if not self.mqtt_config.get("broker") or not self.mqtt_config.get("port") or not self.mqtt_config.get("http_port"):
                     if DEBUG: print("[Enroll WARN] MQTT/API config file missing 'broker', 'port', or 'http_port'.")
                     raise ValueError("Config missing broker/port/http_port.")
                self._create_and_init_mqtt_manager()
                self.start_new_enrollment_process()
            except Exception as e_cfg_load:
                if DEBUG: print(f"[Enroll ERROR] Failed to load MQTT/API config or initialize: {e_cfg_load}.")
                if os.path.exists(self.config_path):
                    try: os.remove(self.config_path); self.mqtt_config = {}
                    except OSError as e_rm_cfg: print(f"[Enroll ERROR] Failed removing invalid config file: {e_rm_cfg}")
                self.push_screen("mqtt_config", self.build_mqtt_config_screen)
        else:
            if DEBUG: print(f"[Enroll INFO] MQTT/API config file '{self.config_path}' not found. Navigating to config screen.")
            self.push_screen("mqtt_config", self.build_mqtt_config_screen)

        self.schedule_healthcheck_only()
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup)

    def _fetch_or_refresh_http_api_token(self, force_refresh=False):
        if self.http_api_token and not force_refresh:
            if self.http_api_token_expiry and datetime.now(timezone.utc) < self.http_api_token_expiry:
                if DEBUG: print("[Enroll DEBUG] Using existing valid HTTP API token.")
                return self.http_api_token
            elif not self.http_api_token_expiry:
                 if DEBUG: print("[Enroll DEBUG] Using existing HTTP API token (no expiry info).")
                 return self.http_api_token

        if not self.mqtt_config.get('broker') or not self.mqtt_config.get('http_port'):
            if DEBUG: print("[Enroll ERROR] API server config (broker/http_port) missing for fetching HTTP API token.")
            messagebox.showerror("Lỗi Cấu Hình", "Cấu hình server API (broker, http_port) bị thiếu.", parent=self.root)
            return None

        api_host_base = self.mqtt_config['broker'].strip().rstrip('/')
        if not api_host_base.startswith(('http://', 'https://')):
            api_host_base = f"http://{api_host_base}"
        api_port = self.mqtt_config['http_port']

        token_url = f"{api_host_base}:{api_port}/api/devicecomm/gettoken"
        
        payload = {"macAddress": self.enroll_mac, "password": generate_hashed_password(self.enroll_mac)}
        
        if DEBUG: print(f"[Enroll DEBUG] Fetching HTTP API token from: {token_url} with payload: {payload}")

        try:
            response = requests.post(token_url, json=payload, timeout=10)
            response.raise_for_status()
            token_data_response = response.json()
            if DEBUG: print(f"[Enroll DEBUG] HTTP API token raw response: {token_data_response}")

            if isinstance(token_data_response, dict):
                if token_data_response.get("code") == "OK" and "data" in token_data_response:
                    data_field = token_data_response["data"]
                    self.http_api_token = data_field.get("token")
                    expires_in_seconds = data_field.get("expiresIn")
                    if expires_in_seconds:
                        try:
                            self.http_api_token_expiry = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in_seconds) - 60)
                            if DEBUG: print(f"[Enroll DEBUG] HTTP API token will expire around: {self.http_api_token_expiry}")
                        except ValueError:
                             if DEBUG: print(f"[Enroll WARN] Invalid expiresIn value: {expires_in_seconds}")
                             self.http_api_token_expiry = None
                elif "accessToken" in token_data_response:
                    self.http_api_token = token_data_response.get("accessToken")
                    expires_in_seconds = token_data_response.get("expiresIn")
                    if expires_in_seconds:
                        try:
                            self.http_api_token_expiry = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in_seconds) - 60)
                            if DEBUG: print(f"[Enroll DEBUG] HTTP API token (accessToken) will expire around: {self.http_api_token_expiry}")
                        except ValueError:
                            if DEBUG: print(f"[Enroll WARN] Invalid expiresIn value for accessToken: {expires_in_seconds}")
                            self.http_api_token_expiry = None
            
            if self.http_api_token:
                if DEBUG: print(f"[Enroll DEBUG] Successfully fetched/refreshed HTTP API token: {self.http_api_token[:20]}...")
                return self.http_api_token
            else:
                if DEBUG: print(f"[Enroll ERROR] 'token' or 'accessToken' not found in HTTP API token response from {token_url}. Response: {token_data_response}")
                messagebox.showerror("Lỗi Lấy Token API", f"Không tìm thấy token trong phản hồi từ server: {str(token_data_response)[:200]}", parent=self.root)
                return None

        except requests.exceptions.HTTPError as http_err:
            err_msg = f"Lỗi HTTP {http_err.response.status_code if http_err.response else 'N/A'} khi lấy token API: {http_err}"
            response_text = http_err.response.text[:200] if http_err.response else "N/A"
            if DEBUG: print(f"[Enroll ERROR] {err_msg} - Response: {response_text}")
            messagebox.showerror("Lỗi API Token", f"{err_msg}\nNội dung: {response_text}", parent=self.root)
        except requests.exceptions.RequestException as req_err:
            if DEBUG: print(f"[Enroll ERROR] Request exception fetching HTTP API token: {req_err}")
            messagebox.showerror("Lỗi Mạng API Token", f"Lỗi kết nối khi lấy token API: {req_err}", parent=self.root)
        except json.JSONDecodeError:
            response_text = response.text[:200] if 'response' in locals() and hasattr(response, 'text') else "N/A"
            if DEBUG: print(f"[Enroll ERROR] JSON decode error fetching HTTP API token. Response: {response_text}")
            messagebox.showerror("Lỗi Dữ Liệu API Token", f"Phản hồi lấy token API không phải JSON hợp lệ.\nNội dung: {response_text}", parent=self.root)
        except Exception as e:
            if DEBUG: print(f"[Enroll ERROR] Unexpected error fetching HTTP API token: {e}")
            messagebox.showerror("Lỗi Không Xác Định (API Token)", f"Lỗi khi lấy token API: {e}", parent=self.root)
        
        return None

    def _extract_person_name_from_schedule(self, schedule_name_api):
        if not schedule_name_api:
            return ""
        if " - " in schedule_name_api:
            return schedule_name_api.split(" - ")[0].strip()
        return schedule_name_api.strip()

    def action_fetch_schedule(self):
        cccd = self.cccd_entry_s0.get().strip()
        if not cccd:
            messagebox.showerror("Thiếu thông tin", "Vui lòng nhập số CCCD.", parent=self.main_frame or self.root)
            return

        self.current_cccd_for_schedule = cccd

        if not self.mqtt_config.get('broker') or not self.mqtt_config.get('http_port'):
            messagebox.showerror("Lỗi cấu hình", "Chưa cấu hình API server (trong file mqtt_enroll_config.json).", parent=self.main_frame or self.root)
            return

        api_host_base = self.mqtt_config['broker'].strip().rstrip('/')
        if not api_host_base.startswith(('http://', 'https://')):
            api_host_base = f"http://{api_host_base}"
        api_port = self.mqtt_config['http_port']
        
        access_token = self._fetch_or_refresh_http_api_token()

        if not access_token:
            current_screen_id = self.screen_history[-1][0] if self.screen_history else None
            if hasattr(self, 'fetch_schedule_button_s0') and self.fetch_schedule_button_s0.winfo_exists() and current_screen_id == "step0_id_input":
                 self.fetch_schedule_button_s0.configure(text="KIỂM TRA LỊCH", state="normal")
            return

        headers = {
            "Authorization": f"Bearer {access_token}"
        }
        if DEBUG: print(f"[Enroll DEBUG] Request Headers for getschedule: {headers}")
        
        self.fetch_schedule_button_s0.configure(text="ĐANG TẢI...", state="disabled")
        self.root.update_idletasks()
        
        url = f"{api_host_base}:{api_port}/api/schedule/getschedule?idNumber={cccd}"
        if DEBUG: print(f"[Enroll DEBUG][API Call] Fetching schedule from (GET): {url}")
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            
            if DEBUG: print(f"[Enroll DEBUG][API Response Status] /getschedule: {response.status_code}")
            if response.status_code == 401 and DEBUG:
                print(f"[Enroll DEBUG][API Response 401 Content] /getschedule: {response.text[:500]}")

            response.raise_for_status() 
            
            response_wrapper = response.json()
            if DEBUG: print(f"[Enroll DEBUG][API Response] Full schedule wrapper: {json.dumps(response_wrapper, indent=2)}")

            if isinstance(response_wrapper, dict) and \
               response_wrapper.get("code") == "OK" and \
               "data" in response_wrapper and \
               isinstance(response_wrapper.get("data"), list):
                
                actual_schedule_list = response_wrapper["data"]
                if DEBUG: print(f"[Enroll DEBUG][API Response] Extracted 'data' field (list of schedules): {json.dumps(actual_schedule_list, indent=2)}")

                if not actual_schedule_list:
                    messagebox.showwarning("Không tìm thấy lịch", f"Không có lịch làm việc nào được tìm thấy cho CCCD: {cccd}.", parent=self.main_frame or self.root)
                    self.fetched_schedule_data = None
                else:
                    self.fetched_schedule_data = actual_schedule_list[0]
                    required_fields = ["idNumber", "scheduleId", "fromDate", "toDate", "fromTime", "toTime", "activeDays"]
                    if not all(field in self.fetched_schedule_data for field in required_fields):
                        messagebox.showerror("Lỗi Dữ Liệu Lịch", "Dữ liệu lịch làm việc từ server không đầy đủ. Vui lòng liên hệ quản trị.", parent=self.main_frame or self.root)
                        self.fetched_schedule_data = None
                    else:
                        messagebox.showinfo("Thành công", f"Đã tìm thấy lịch làm việc cho CCCD: {cccd}.\nTên lịch: {self.fetched_schedule_data.get('scheduleName', 'N/A')}", parent=self.main_frame or self.root)
                        self.current_id_number = self.fetched_schedule_data.get("idNumber", cccd)
                        
                        self.current_person_name = self._extract_person_name_from_schedule(self.fetched_schedule_data.get("scheduleName", ""))
                        if DEBUG: print(f"[Enroll DEBUG] Auto-assigned Person Name: {self.current_person_name}")

                        try:
                            from_date_api = self.fetched_schedule_data.get("fromDate", "").split("T")[0]
                            to_date_api = self.fetched_schedule_data.get("toDate", "").split("T")[0]
                            from_time_api = self.fetched_schedule_data.get("fromTime", "00:00:00")
                            to_time_api = self.fetched_schedule_data.get("toTime", "23:59:59")
                            
                            if from_date_api: self.from_year_str, self.from_month_str, self.from_day_str = from_date_api.split("-")
                            if from_time_api: self.from_hour_str, self.from_minute_str, self.from_second_str = from_time_api.split(":")
                            if to_date_api: self.to_year_str, self.to_month_str, self.to_day_str = to_date_api.split("-")
                            if to_time_api: self.to_hour_str, self.to_minute_str, self.to_second_str = to_time_api.split(":")

                            active_days_str_api = self.fetched_schedule_data.get("activeDays", "0000000")
                            if len(active_days_str_api) == 7:
                                self.active_day_mask_list = [char == '1' for char in active_days_str_api]
                            else:
                                self.active_day_mask_list = [False] * 7
                        except Exception as e_auto_assign_dt:
                            if DEBUG: print(f"[Enroll WARN] Could not auto-assign datetime from schedule: {e_auto_assign_dt}")
                        
                        if not self.current_bio_id: self.generate_new_bio_id()
                        
                        self.push_screen("step2_biometrics", self.show_step2_biometric_screen)
                        return 
            else:
                error_msg_structure = "Cấu trúc phản hồi từ API lấy lịch không như mong đợi."
                if isinstance(response_wrapper, dict):
                    error_msg_structure += f" Code: {response_wrapper.get('code')}, Message: {response_wrapper.get('message')}"
                if DEBUG: print(f"[Enroll ERROR] {error_msg_structure}. Response: {response_wrapper}")
                messagebox.showerror("Lỗi Dữ Liệu API", error_msg_structure, parent=self.main_frame or self.root)
                self.fetched_schedule_data = None

        except requests.exceptions.HTTPError as http_err:
            err_msg = f"Lỗi HTTP {http_err.response.status_code if http_err.response else 'N/A'} khi lấy lịch: {http_err}."
            try: 
                err_detail = http_err.response.json()
                err_msg += f"\nChi tiết server: {err_detail.get('message', str(err_detail))}"
            except json.JSONDecodeError:
                err_msg += f"\nNội dung phản hồi (không phải JSON): {http_err.response.text[:200] if http_err.response else ''}"
            except Exception: pass
            messagebox.showerror("Lỗi API", err_msg, parent=self.main_frame or self.root)
            self.fetched_schedule_data = None
        except requests.exceptions.RequestException as req_err: 
            messagebox.showerror("Lỗi Mạng", f"Lỗi kết nối khi lấy lịch: {req_err}", parent=self.main_frame or self.root)
            self.fetched_schedule_data = None
        except json.JSONDecodeError:
            messagebox.showerror("Lỗi Dữ Liệu", "Phản hồi từ API lấy lịch không phải là JSON hợp lệ.", parent=self.main_frame or self.root)
            self.fetched_schedule_data = None
        except Exception as e: 
            messagebox.showerror("Lỗi không xác định", f"Đã xảy ra lỗi khi xử lý lịch làm việc: {e}", parent=self.main_frame or self.root)
            self.fetched_schedule_data = None
        finally:
            current_screen_id = self.screen_history[-1][0] if self.screen_history else None
            if hasattr(self, 'fetch_schedule_button_s0') and self.fetch_schedule_button_s0.winfo_exists() and \
               (current_screen_id == "step0_id_input" and not self.fetched_schedule_data):
                 self.fetch_schedule_button_s0.configure(text="KIỂM TRA LỊCH", state="normal")

    def prepare_and_send_data_http(self):
        parent_for_messages = self.main_frame or self.root

        if not self.fetched_schedule_data:
            messagebox.showerror("Lỗi Dữ Liệu", "Thiếu thông tin lịch làm việc đã lấy từ server. Không thể gửi.", parent=parent_for_messages)
            self._action_goto_step0_from_step3()
            return

        if not self.current_person_name: 
            messagebox.showerror("Lỗi Thiếu Thông Tin", "Tên người dùng không được để trống.", parent=parent_for_messages)
            return


        if not (self.current_face_template_b64 or self.current_finger_template_b64 or self.current_rfid_uid_str):
            messagebox.showwarning("Lỗi Thiếu Sinh Trắc Học", "Cần đăng ký ít nhất một mẫu sinh trắc học (tại Bước 2).", parent=parent_for_messages)
            self._action_goto_step2_from_step3()
            return

        access_token = self._fetch_or_refresh_http_api_token() 

        if not access_token:
            return

        upload_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        if DEBUG: print(f"[Enroll DEBUG] Request Headers for visitorbio/upload: {upload_headers}")
        
        face_img_payload_list = []
        if self.current_face_template_b64 and self.current_face_image_b64:
            face_img_payload_list.append({"Img": self.current_face_image_b64, "Template": self.current_face_template_b64})

        finger_img_payload_list = []
        if self.current_finger_template_b64:
            finger_img_payload_list.append({"Img": self.current_finger_imgage_b64, "Template": self.current_finger_template_b64})
            
        id_card_uid_list = []
        if self.current_rfid_uid_str:
            uid_raw = self.current_rfid_uid_str.encode("utf-8")  
            uid_base64 = base64.b64encode(uid_raw).decode("utf-8")
            id_card_uid_list.append({"Img": "", "Template": uid_base64})

        http_payload = {
            "idNumber": self.fetched_schedule_data.get("idNumber"),
            "ScheduleId": self.fetched_schedule_data.get("scheduleId"),
            "FaceImg": face_img_payload_list,
            "FingerImg": finger_img_payload_list,
            "IrisImg": id_card_uid_list,
            "CaptureTime": datetime.now(GMT_PLUS_7).isoformat(timespec='milliseconds'),
            "CreatedBy": self.enroll_mac
        }

        if not self.mqtt_config.get('broker') or not self.mqtt_config.get('http_port'):
            messagebox.showerror("Lỗi cấu hình", "Chưa cấu hình API server (trong file mqtt_enroll_config.json).", parent=parent_for_messages)
            return

        api_host_base = self.mqtt_config['broker'].strip().rstrip('/')
        if not api_host_base.startswith(('http://', 'https://')):
            api_host_base = f"http://{api_host_base}"
        api_port = self.mqtt_config['http_port']

        upload_url = f"{api_host_base}:{api_port}/api/visitorbio/upload"
        if DEBUG: print(f"[Enroll DEBUG][API Call] Sending bio data to: {upload_url} with payload (first 500 chars): {json.dumps(http_payload)[:500]}...")

        try:
            response = requests.post(upload_url, json=http_payload, headers=upload_headers, timeout=30)
            response.raise_for_status()

            response_data = response.json()
            if DEBUG: print(f"[Enroll DEBUG][API Response] Upload response: {response_data}")

            if response_data.get("code") == "OK":
                messagebox.showinfo("Gửi Thành Công",
                                    f"Đã gửi dữ liệu đăng ký cho '{self.current_person_name}'",
                                    parent=self.root)
                self.start_new_enrollment_process()
            else:
                messagebox.showerror("Lỗi Từ Server",
                                     f"Server báo lỗi khi gửi dữ liệu: {response_data.get('message', 'Không có thông báo lỗi cụ thể.')}\n(Code: {response_data.get('code')})",
                                     parent=self.root)

        except requests.exceptions.HTTPError as http_err:
            err_msg = f"Lỗi HTTP {http_err.response.status_code if http_err.response else 'N/A'} khi gửi dữ liệu: {http_err}."
            try: 
                err_detail = http_err.response.json()
                err_msg += f"\nChi tiết server: {err_detail.get('message', str(err_detail))}"
            except json.JSONDecodeError:
                err_msg += f"\nNội dung phản hồi (không phải JSON): {http_err.response.text[:200] if http_err.response else ''}"
            except Exception: pass
            messagebox.showerror("Lỗi API", err_msg, parent=parent_for_messages)
        except requests.exceptions.RequestException as req_err:
            messagebox.showerror("Lỗi Mạng", f"Lỗi kết nối khi gửi dữ liệu: {req_err}", parent=parent_for_messages)
        except json.JSONDecodeError:
            messagebox.showerror("Lỗi Dữ Liệu", "Phản hồi từ API gửi dữ liệu không phải là JSON hợp lệ.", parent=parent_for_messages)
        except Exception as e:
            messagebox.showerror("Lỗi không xác định", f"Đã xảy ra lỗi khi gửi dữ liệu: {e}", parent=parent_for_messages)

    def _create_and_init_mqtt_manager(self):
        if self.mqtt_config:
            if self.mqtt_manager:
                self.mqtt_manager.disconnect_client(explicit=True)

            if DEBUG: print("[Enroll DEBUG] Creating and initializing MQTTManager with config:", self.mqtt_config)
            self.mqtt_manager = MQTTEnrollManager(
                mqtt_config=self.mqtt_config,
                enroll_mac=self.enroll_mac,
                config_file_path=self.config_path,
                debug=DEBUG
            )
            self.mqtt_manager.on_connection_status_change = self.update_connection_status
            self.mqtt_manager.attempt_connection_sequence()

    def generate_new_bio_id(self):
        self.current_bio_id = uuid.uuid4().hex[:10].upper()
        if DEBUG: print(f"[Enroll DEBUG] New Client-Side Bio ID generated: {self.current_bio_id}")

    def initialize_fingerprint_sensor(self):
        if PyFingerprint is None:
            if DEBUG: print("[Enroll WARN] PyFingerprint library not available. Fingerprint functions disabled.")
            return
        try:
            self.fingerprint_sensor = PyFingerprint(FINGERPRINT_PORT, FINGERPRINT_BAUDRATE, 0xFFFFFFFF, 0x00000000)
            if not self.fingerprint_sensor.verifyPassword():
                if DEBUG: print("[Enroll ERROR] Fingerprint sensor password verification failed. Sensor might not be connected or accessible.")
                self.fingerprint_sensor = None
            elif DEBUG: print("[Enroll INFO] Fingerprint sensor verified successfully.")
        except Exception as e_fp_init:
            if DEBUG: print(f"[Enroll ERROR] Failed to initialize fingerprint sensor: {e_fp_init}")
            self.fingerprint_sensor = None

    def initialize_rfid_sensor(self):
        if PN532_I2C is None or board is None or busio is None:
            if DEBUG: print("[Enroll WARN] PN532 I2C libraries (board, busio, adafruit_pn532) not found. RFID functions disabled.")
            self.rfid_sensor = None; return
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            reset_pin_obj = irq_pin_obj = None
            if RFID_RESET_PIN_BCM is not None:
                import digitalio
                reset_pin_obj = digitalio.DigitalInOut(getattr(board, f"D{RFID_RESET_PIN_BCM}"))
            if RFID_IRQ_PIN_BCM is not None:
                import digitalio
                irq_pin_obj = digitalio.DigitalInOut(getattr(board, f"D{RFID_IRQ_PIN_BCM}"))

            self.rfid_sensor = PN532_I2C(i2c, debug=False, reset=reset_pin_obj, irq=irq_pin_obj)
            self.rfid_sensor.SAM_configuration()
            ic, ver, rev, support = self.rfid_sensor.firmware_version
            if DEBUG: print(f"[Enroll INFO] PN532 I2C sensor initialized. Firmware ver: {ver}.{rev}")
        except Exception as e_rfid_init:
            if DEBUG: print(f"[Enroll ERROR] Failed to initialize RFID I2C sensor: {e_rfid_init}")
            self.rfid_sensor = None

    # def handle_discovered_device_info(self, room_name, mac_address):
    #     if room_name and mac_address:
    #         self.discovered_rooms_macs[room_name] = mac_address
    #         active_screen_id = self.screen_history[-1][0] if self.screen_history else None
    #         if active_screen_id == "step1_basic_info" and hasattr(self, 'room_name_option_menu_s1') and \
    #            self.room_name_option_menu_s1 and self.room_name_option_menu_s1.winfo_exists():
    #             new_room_options = sorted(list(self.discovered_rooms_macs.keys()))
    #             current_selection = self.room_name_var_s1.get()
    #             self.room_name_option_menu_s1.configure(values=new_room_options if new_room_options else ["(Chưa có phòng)"])
    #             if current_selection in new_room_options:
    #                 self.room_name_var_s1.set(current_selection)
    #             elif new_room_options:
    #                 self.room_name_var_s1.set(new_room_options[0])
    #             else:
    #                 self.room_name_var_s1.set("(Chưa có phòng)")
    #     elif DEBUG: print(f"[Enroll WARN] Received incomplete device info from MQTT: room='{room_name}', mac='{mac_address}'")

    def schedule_healthcheck_only(self):
        if self.mqtt_manager and self.mqtt_manager.is_actively_connected():
            self.mqtt_manager.send_healthcheck()
        if self.root and self.root.winfo_exists():
            self.root.after(HEALTHCHECK_INTERVAL_MS, self.schedule_healthcheck_only)

    def update_connection_status(self, is_connected):
        if not (hasattr(self,'connection_status_label') and self.connection_status_label and self.connection_status_label.winfo_exists()): return
        img_to_show = self.connected_image if is_connected else self.disconnected_image
        status_text = "" if is_connected else ""
        text_color = SUCCESS_COLOR if is_connected else ERROR_COLOR
        self.connection_status_label.configure(image=img_to_show, text=status_text, text_color=text_color)

    def show_background(self):
        if hasattr(self,'bg_photo') and self.bg_photo:
            if hasattr(self,'bg_label') and self.bg_label and self.bg_label.winfo_exists(): self.bg_label.destroy()
            self.bg_label = ctk.CTkLabel(self.root, image=self.bg_photo, text=""); self.bg_label.place(x=0, y=0, relwidth=1, relheight=1); self.bg_label.lower()

    def clear_frames(self, keep_background=True):
        if hasattr(self, 'main_frame') and self.main_frame and self.main_frame.winfo_exists():
            self.main_frame.destroy(); self.main_frame = None
        if hasattr(self, 'nav_frame') and self.nav_frame and self.nav_frame.winfo_exists():
            self.nav_frame.destroy(); self.nav_frame = None
        face_enroll.stop_face_capture()
        if keep_background:
            self.show_background()
            if hasattr(self, 'connection_status_label') and self.connection_status_label and self.connection_status_label.winfo_exists():
                 self.connection_status_label.lift()
            self.create_config_button()

    def push_screen(self, screen_id, screen_func, *args):
        if self.screen_history and self.screen_history[-1][0] == screen_id and not screen_id.startswith("step"):
            if DEBUG: print(f"[Enroll DEBUG] Screen '{screen_id}' already at top of history. Skipping push.")
            return
        self.screen_history.append((screen_id, screen_func, args))
        if DEBUG: print(f"[Enroll DEBUG] Pushing screen: {screen_id}. History: {[s[0] for s in self.screen_history]}")
        self.clear_frames()
        self.root.update_idletasks()
        screen_func(*args)

    def go_back(self):
        if len(self.screen_history) > 1:
            self.screen_history.pop()
            prev_screen_id, prev_screen_func, prev_args = self.screen_history[-1]
            if DEBUG: print(f"[Enroll DEBUG] Going back to screen: {prev_screen_id}. History: {[s[0] for s in self.screen_history]}")
            self.clear_frames(); self.root.update_idletasks(); prev_screen_func(*prev_args)
        elif not (hasattr(self, 'main_frame') and self.main_frame and self.main_frame.winfo_exists()):
             self.start_new_enrollment_process()

    def start_new_enrollment_process(self):
        face_enroll.stop_face_capture()
        self.reset_enrollment_state_full()
        self.screen_history = []
        self.push_screen("step0_id_input", self.show_step0_id_input_screen)

    def create_config_button(self):
        if hasattr(self, 'config_btn_ref') and self.config_btn_ref and self.config_btn_ref.winfo_exists():
            self.config_btn_ref.lift(); return
        self.config_btn_ref = ctk.CTkButton(self.root, text="Cài đặt",
                                            command=self.confirm_reconfigure_mqtt,
                                            width=130, height=35,
                                            fg_color="#6c757d", hover_color="#5a6268",
                                            font=("Segoe UI", 12), text_color="white")
        self.config_btn_ref.place(relx=0.99, rely=0.02, anchor="ne")

    def confirm_reconfigure_mqtt(self):
        if messagebox.askyesno("Xác nhận Cấu Hình Lại",
                               "Bạn có chắc muốn cấu hình lại thông tin kết nối MQTT và API Server cho trạm đăng ký này không?\nThao tác này sẽ xóa cấu hình hiện tại.",
                               icon='warning', parent=self.root):
            self.reconfigure_mqtt_station()

    def reconfigure_mqtt_station(self):
        if self.mqtt_manager:
            self.mqtt_manager.disconnect_client(explicit=True)
            self.mqtt_manager = None
            self.update_connection_status(False)
        if os.path.exists(self.config_path):
            try: os.remove(self.config_path)
            except Exception as e_rm_cfg: print(f"[Enroll ERROR] Failed to remove config file during reconfigure: {e_rm_cfg}")
        self.mqtt_config = {}
        self.http_api_token = None
        self.http_api_token_expiry = None
        self.screen_history = []
        self.push_screen("mqtt_config", self.build_mqtt_config_screen)

    def build_mqtt_config_screen(self):
        self.clear_frames(keep_background=False)
        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=10)
        self.main_frame.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.7, relheight=0.65)
        ctk.CTkLabel(self.main_frame, text="CÀI ĐẶT",
                     font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN_CONTAINER + 5, PAD_Y_MAIN_CONTAINER + 5))
        form_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        form_frame.pack(pady=PAD_Y_WIDGET_VERTICAL, padx=PAD_X_SECTION + 10, fill="x")
        def add_cfg_row(parent, label_text, placeholder_text="", default_value=""):
            row_frame = ctk.CTkFrame(parent, fg_color="transparent")
            row_frame.pack(fill="x", pady=PAD_Y_WIDGET_VERTICAL)
            ctk.CTkLabel(row_frame, text=label_text, font=LABEL_FONT, width=170, anchor="w").pack(side="left", padx=(0,5))
            entry_widget = ctk.CTkEntry(row_frame, font=INPUT_FONT, height=ENTRY_HEIGHT, placeholder_text=placeholder_text)
            entry_widget.pack(side="left", expand=True, fill="x")
            if default_value: entry_widget.insert(0, str(default_value))
            return entry_widget
        self.server_entry_cfg = add_cfg_row(form_frame, "MÁY CHỦ", "", self.mqtt_config.get("broker", ""))
        self.port_entry_cfg = add_cfg_row(form_frame, "CỔNG KẾT NỐI MQTT", "", self.mqtt_config.get("port", ""))
        self.http_port_entry_cfg = add_cfg_row(form_frame, "CỔNG KẾT NỐI HTTP", "", self.mqtt_config.get("http_port", ""))
        self.enroll_room_entry_cfg = add_cfg_row(form_frame, "TÒA NHÀ ", "", self.mqtt_config.get("enroll_station_room", ""))
        button_frame_cfg = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        button_frame_cfg.pack(pady=(PAD_Y_MAIN_CONTAINER + 10, PAD_Y_MAIN_CONTAINER), padx=PAD_X_SECTION, fill="x", side="bottom")
        ctk.CTkButton(button_frame_cfg, text="LƯU & KẾT NỐI", width=MEDIUM_BUTTON_WIDTH+30, height=MEDIUM_BUTTON_HEIGHT,
                      font=BUTTON_FONT, command=self.validate_and_save_mqtt_config,
                      fg_color=ACCENT_COLOR, text_color=BUTTON_FG_TEXT).pack(side="right", padx=PAD_X_WIDGET_HORIZONTAL)

    def validate_and_save_mqtt_config(self):
        broker_val=self.server_entry_cfg.get().strip()
        port_str_val=self.port_entry_cfg.get().strip()
        http_port_str_val=self.http_port_entry_cfg.get().strip()
        station_location_val=self.enroll_room_entry_cfg.get().strip()
        if not all([broker_val, port_str_val, http_port_str_val, station_location_val]):
            messagebox.showerror("Thiếu Thông Tin", "Vui lòng điền đầy đủ tất cả các trường cấu hình.", parent=self.main_frame or self.root); return
        try:
            mqtt_port_val = int(port_str_val)
            api_http_port_val = int(http_port_str_val)
            if not (0 < mqtt_port_val < 65536 and 0 < api_http_port_val < 65536):
                raise ValueError("Port number out of valid range (1-65535)")
        except ValueError:
            messagebox.showerror("Lỗi Dữ Liệu", "Cổng MQTT hoặc HTTP không hợp lệ.\nPhải là một số trong khoảng 1-65535.", parent=self.main_frame or self.root); return
        new_mqtt_config = {
            "broker": broker_val, "port": mqtt_port_val,
            "http_port": api_http_port_val, "enroll_station_room": station_location_val
        }
        try:
            with open(self.config_path, "w") as f: json.dump(new_mqtt_config, f, indent=2)
            self.mqtt_config = new_mqtt_config
            self.http_api_token = None; self.http_api_token_expiry = None
            if DEBUG: print("[Enroll DEBUG] Saved new MQTT/API configuration:", self.mqtt_config)
        except Exception as e_save_cfg:
            messagebox.showerror("Lỗi Lưu Trữ", f"Không thể lưu file cấu hình: {e_save_cfg}", parent=self.main_frame or self.root); return
        self.show_connecting_screen_mqtt_station()
        self.root.after(200, self._init_mqtt_after_save_config)

    def _init_mqtt_after_save_config(self):
        self._create_and_init_mqtt_manager()
        self.root.after(3000, self.start_new_enrollment_process)

    def show_connecting_screen_mqtt_station(self):
        self.clear_frames(keep_background=False)
        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=10)
        self.main_frame.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(self.main_frame, text="Đang kết nối đến MQTT Server...",
                     font=STEP_TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=20, padx=40)
        progress_bar = ctk.CTkProgressBar(self.main_frame, width=300, height=18, corner_radius=8, mode="indeterminate")
        progress_bar.pack(pady=(0,20), padx=40); progress_bar.start()

    def show_step0_id_input_screen(self):
        self.clear_frames()
        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=10)
        self.main_frame.place(relx=0.5, rely=0.4, anchor="center", relwidth=0.6, relheight=0.4)
        ctk.CTkLabel(self.main_frame, text="LẤY LỊCH LÀM VIỆC", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN_CONTAINER + 5, PAD_Y_MAIN_CONTAINER))
        ctk.CTkLabel(self.main_frame, text="Nhập số CCCD/Mã định danh:", font=LABEL_FONT).pack(pady=(10,2))
        self.cccd_entry_s0 = ctk.CTkEntry(self.main_frame, font=INPUT_FONT, height=ENTRY_HEIGHT, width=300)
        self.cccd_entry_s0.pack(pady=(0, 20)); self.cccd_entry_s0.insert(0, self.current_cccd_for_schedule)
        self.fetch_schedule_button_s0 = ctk.CTkButton(self.main_frame, text="KIỂM TRA LỊCH", font=BUTTON_FONT,
                                           width=MEDIUM_BUTTON_WIDTH, height=MEDIUM_BUTTON_HEIGHT,
                                           command=self.action_fetch_schedule,
                                           fg_color=ACCENT_COLOR, text_color=BUTTON_FG_TEXT)
        self.fetch_schedule_button_s0.pack(pady=(10, 20))
        if len(self.screen_history) > 1 and self.screen_history[-2][0] == "mqtt_config":
             ctk.CTkButton(self.main_frame, text="QUAY LẠI CẤU HÌNH", font=("Segoe UI", 16),
                           width=MEDIUM_BUTTON_WIDTH, height=MEDIUM_BUTTON_HEIGHT-10,
                           command=self.go_back, fg_color="#A0A0A0").pack(pady=(0,10), side="bottom")

    def show_step1_basic_info_screen(self): 
        self.clear_frames()
        if not self.current_bio_id: self.generate_new_bio_id()
        if not self.fetched_schedule_data:
            messagebox.showwarning("Thiếu Lịch Làm Việc", "Vui lòng thực hiện Bước 0.", parent=self.root)
            self.push_screen("step0_id_input", self.show_step0_id_input_screen); return

        if DEBUG: print("[Enroll DEBUG] show_step1_basic_info_screen was called, but it should be bypassed. Navigating to Step 2.")
        self.push_screen("step2_biometrics", self.show_step2_biometric_screen)


    def go_back_to_step0_from_step1(self): 
        if self.screen_history and self.screen_history[-1][0] == "step1_basic_info":
            self.screen_history.pop()
            if self.screen_history and self.screen_history[-1][0] == "step0_id_input":
                 prev_screen_id, prev_screen_func, prev_args = self.screen_history[-1]
                 if DEBUG: print(f"[Enroll DEBUG] Going back to screen: {prev_screen_id} from Step 1.")
                 self.clear_frames(); self.root.update_idletasks(); prev_screen_func(*prev_args)
                 return
        self.push_screen("step0_id_input", self.show_step0_id_input_screen)

    def _action_goto_step2(self): # This would be called if Step 1 was shown
        # self.current_person_name should be populated from fetched_schedule_data
        if not self.current_person_name: 
            messagebox.showerror("Thiếu Thông Tin", "Không thể xác định tên người dùng từ lịch.", parent=self.main_frame or self.root); return
        self.push_screen("step2_biometrics", self.show_step2_biometric_screen)
    

    def show_step2_biometric_screen(self):
        self.clear_frames()
        if not self.fetched_schedule_data: # Ensure schedule data is present before showing Step 2
            messagebox.showerror("Lỗi Luồng", "Không có dữ liệu lịch. Quay lại Bước 0.", parent=self.root)
            self.push_screen("step0_id_input", self.show_step0_id_input_screen)
            return

        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=10)
        self.main_frame.place(relx=0.5, rely=0.47, anchor="center", relwidth=0.9, relheight=0.75)
        ctk.CTkLabel(self.main_frame, text="ĐĂNG KÝ SINH TRẮC HỌC", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN_CONTAINER, PAD_Y_MAIN_CONTAINER-5))
        
        person_id_for_display = self.fetched_schedule_data.get("idNumber", "N/A")
        # self.current_person_name should have been set in action_fetch_schedule
        person_name_for_display = self.current_person_name or self.fetched_schedule_data.get("scheduleName", "N/A")
        person_info_for_display = f"Đang đăng ký cho: {person_name_for_display[:25]}{'...' if len(person_name_for_display)>25 else ''} (ID: {person_id_for_display})"
        ctk.CTkLabel(self.main_frame, text=person_info_for_display, font=LABEL_FONT).pack(pady=(0, PAD_Y_MAIN_CONTAINER-2))
        
        biometric_buttons_container = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        biometric_buttons_container.pack(expand=True, fill="both", padx=PAD_X_SECTION, pady=0)
        biometric_buttons_container.columnconfigure((0,1,2), weight=1, uniform="bio_button_column_uniform_group") 
        biometric_buttons_container.rowconfigure(0, weight=1)
        button_width_step2 = (WINDOW_WIDTH * 0.94 * 0.3); button_height_step2 = (WINDOW_HEIGHT * 0.81 * 0.55); button_border_spacing_val = 8 
        def create_biometric_button_with_status(parent, grid_column, icon_image, button_text, command_func):
            button_frame = ctk.CTkFrame(parent, fg_color="transparent")
            button_frame.grid(row=0, column=grid_column, padx=PAD_X_WIDGET_HORIZONTAL + 2, pady=0, sticky="nsew")
            actual_button = ctk.CTkButton(button_frame, image=icon_image, text=button_text, font=BUTTON_FONT, compound="top", width=button_width_step2, height=button_height_step2, command=command_func, corner_radius=10, border_spacing=button_border_spacing_val)
            actual_button.pack(expand=True, pady=(5,0))
            status_display_label = ctk.CTkLabel(button_frame, text="", font=SMALL_STATUS_FONT); status_display_label.pack(pady=(2,5))
            return actual_button, status_display_label
        self.face_enroll_btn_s2, self.face_status_label_s2 = create_biometric_button_with_status(biometric_buttons_container, 0, self.face_icon_large, "KHUÔN MẶT", self.start_face_enrollment_s2)
        self.finger_enroll_btn_s2, self.finger_status_label_s2 = create_biometric_button_with_status(biometric_buttons_container, 1, self.fingerprint_icon_large, "VÂN TAY", self.start_fingerprint_enrollment_s2)
        self.rfid_enroll_btn_s2, self.rfid_status_label_s2 = create_biometric_button_with_status(biometric_buttons_container, 2, self.rfid_icon_large, "THẺ TỪ", self.start_rfid_enrollment_s2)
        self._update_biometric_status_s2()
        if hasattr(self, 'nav_frame') and self.nav_frame and self.nav_frame.winfo_exists(): self.nav_frame.destroy()
        self.nav_frame = ctk.CTkFrame(self.root, fg_color=BG_COLOR)
        self.nav_frame.place(relx=0.5, rely=1.0, anchor="s", relwidth=1.0, relheight=0.12)
        ctk.CTkButton(self.nav_frame, text="QUAY LẠI (B0)", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH, height=LARGE_BUTTON_HEIGHT, command=self.go_back, image=self.back_icon, compound="left", corner_radius=8, fg_color="#A0A0A0").pack(side="left", pady=(5,8), padx=PAD_X_MAIN_CONTAINER)
        self.next_step3_button = ctk.CTkButton(self.nav_frame, text="TIẾP TỤC", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH, height=LARGE_BUTTON_HEIGHT, command=self._action_goto_step3, fg_color=ACCENT_COLOR, text_color=BUTTON_FG_TEXT, image=self.next_icon, compound="right", corner_radius=8)
        self.next_step3_button.pack(side="right", pady=(5,8), padx=PAD_X_MAIN_CONTAINER); self._update_next_button_step2_state()

    def _update_biometric_status_s2(self): 
        enrollment_options = [
            (self.face_enroll_btn_s2, self.face_status_label_s2, "current_face_template_b64"),
            (self.finger_enroll_btn_s2, self.finger_status_label_s2, "current_finger_template_b64"),
            (self.rfid_enroll_btn_s2, self.rfid_status_label_s2, "current_rfid_uid_str")]
        for button_widget, status_label_widget, template_attribute_name in enrollment_options:
            if hasattr(status_label_widget,'winfo_exists') and status_label_widget.winfo_exists(): 
                is_data_enrolled = bool(getattr(self, template_attribute_name, None))
                status_label_widget.configure(text="Đã đăng ký" if is_data_enrolled else "Chưa đăng ký", text_color=SUCCESS_COLOR if is_data_enrolled else "grey50")
                if hasattr(button_widget, 'winfo_exists') and button_widget.winfo_exists():
                     button_widget.configure(fg_color=SUCCESS_COLOR if is_data_enrolled else "#606060", hover_color="#2b9e4c" if is_data_enrolled else "#707070")
        self._update_next_button_step2_state()

    def _update_next_button_step2_state(self): 
         if hasattr(self, 'next_step3_button') and self.next_step3_button.winfo_exists():
            can_proceed_to_step3 = bool(self.current_face_template_b64 or self.current_finger_template_b64 or self.current_rfid_uid_str)
            self.next_step3_button.configure(state="normal" if can_proceed_to_step3 else "disabled", fg_color=ACCENT_COLOR if can_proceed_to_step3 else "#A0A0A0")
   
    def start_face_enrollment_s2(self): face_enroll.capture_face_for_enrollment(parent=self.root, on_success_callback=self.handle_face_enroll_success_s2, on_cancel_callback=self.handle_face_enroll_cancel_s2)
    def handle_face_enroll_success_s2(self, captured_image_b64, face_template_b64): self.current_face_image_b64 = captured_image_b64; self.current_face_template_b64 = face_template_b64; self._schedule_return_to_step2()
    def handle_face_enroll_cancel_s2(self): self._schedule_return_to_step2()
    def start_fingerprint_enrollment_s2(self):
        parent_for_messages = self.main_frame or self.root
        if not self.fingerprint_sensor: messagebox.showerror("Lỗi Cảm Biến", "Cảm biến vân tay lỗi hoặc chưa được khởi tạo.", parent=parent_for_messages); return
        try:
            if not self.fingerprint_sensor.verifyPassword(): messagebox.showerror("Lỗi Cảm Biến", "Không thể xác thực với cảm biến Vân tay. Kiểm tra kết nối.", parent=parent_for_messages); return
        except Exception as e_fp_comm: messagebox.showerror("Lỗi Cảm Biến", f"Lỗi giao tiếp với cảm biến Vân tay: {str(e_fp_comm)[:100]}", parent=parent_for_messages); return
        fingerprint_enroll.enroll_fingerprint_template(parent=self.root, sensor=self.fingerprint_sensor, on_success_callback=self.handle_finger_enroll_success_s2, on_failure_callback=self.handle_finger_enroll_failure_s2, on_cancel_callback=self.handle_finger_enroll_cancel_s2)
    def handle_finger_enroll_success_s2(self, finger_template_b64): self.current_finger_template_b64 = finger_template_b64; self._schedule_return_to_step2()
    def handle_finger_enroll_failure_s2(self, failure_reason=""): messagebox.showerror("Lỗi Đăng Ký Vân Tay", f"Đăng ký vân tay không thành công: {failure_reason}", parent=self.root); self._schedule_return_to_step2()
    def handle_finger_enroll_cancel_s2(self): self._schedule_return_to_step2()
    def start_rfid_enrollment_s2(self):
        parent_for_messages = self.main_frame or self.root
        if not self.rfid_sensor: messagebox.showerror("Lỗi Đầu Đọc", "Đầu đọc RFID/IDCard lỗi hoặc chưa được khởi tạo.", parent=parent_for_messages); return
        try: self.rfid_sensor.SAM_configuration()
        except Exception as e_rfid_comm: messagebox.showerror("Lỗi Đầu Đọc", f"Lỗi giao tiếp với đầu đọc RFID/IDCard: {str(e_rfid_comm)[:80]}", parent=parent_for_messages); return
        rfid_enroll.enroll_rfid_card(parent=self.root, sensor_pn532=self.rfid_sensor, on_success_callback=self.handle_rfid_enroll_success_s2, on_failure_callback=self.handle_rfid_enroll_failure_s2, on_cancel_callback=self.handle_rfid_enroll_cancel_s2)
    def handle_rfid_enroll_success_s2(self, rfid_uid_hex_string): self.current_rfid_uid_str = rfid_uid_hex_string; self._schedule_return_to_step2()
    def handle_rfid_enroll_failure_s2(self, failure_reason=""): messagebox.showerror("Lỗi Đăng Ký RFID/IDCard", f"Đăng ký thẻ không thành công: {failure_reason}", parent=self.root); self._schedule_return_to_step2()
    def handle_rfid_enroll_cancel_s2(self): self._schedule_return_to_step2()
    def _schedule_return_to_step2(self): self.root.after(10, lambda: self.push_screen("step2_biometrics", self.show_step2_biometric_screen))
    def _action_goto_step3(self): 
        if not (self.current_face_template_b64 or self.current_finger_template_b64 or self.current_rfid_uid_str):
            messagebox.showwarning("Thiếu Sinh Trắc Học", "Cần đăng ký ít nhất một mẫu sinh trắc học (Khuôn mặt, Vân tay, hoặc Thẻ RFID/IDCard) để tiếp tục.", parent=self.main_frame or self.root); return
        self.push_screen("step3_confirmation", self.show_step3_confirmation_screen)

    def show_step3_confirmation_screen(self):
        self.clear_frames()
        if not self.fetched_schedule_data: # Ensure schedule data is present
            messagebox.showerror("Lỗi Luồng", "Không có dữ liệu lịch. Quay lại Bước 0.", parent=self.root)
            self.push_screen("step0_id_input", self.show_step0_id_input_screen)
            return

        self.main_frame = ctk.CTkFrame(self.root, fg_color=SCREEN_BG_COLOR, corner_radius=10)
        self.main_frame.place(relx=0.5, rely=0.47, anchor="center", relwidth=0.9, relheight=0.75)
        ctk.CTkLabel(self.main_frame, text="XÁC NHẬN THÔNG TIN", font=TITLE_FONT, text_color=ACCENT_COLOR).pack(pady=(PAD_Y_MAIN_CONTAINER, PAD_Y_MAIN_CONTAINER - 5))
        personal_validity_outer_frame = ctk.CTkFrame(self.main_frame, fg_color=BG_COLOR, corner_radius=8)
        personal_validity_outer_frame.pack(fill="x", padx=PAD_X_SECTION, pady=(PAD_Y_SECTION, PAD_Y_SECTION + 3))
        ctk.CTkLabel(personal_validity_outer_frame, text="Thông Tin Chung & Thời Gian Hiệu Lực (từ API)", font=STEP_TITLE_FONT, text_color=ACCENT_COLOR, anchor="w").pack(fill="x", padx=12, pady=(8, 5))
        personal_validity_content_frame = ctk.CTkFrame(personal_validity_outer_frame, fg_color="transparent")
        personal_validity_content_frame.pack(fill="x", padx=12, pady=(0, 8)); personal_validity_content_frame.columnconfigure((0,2), weight=1); personal_validity_content_frame.columnconfigure((1,3), weight=2)
        current_display_row_idx = [0] 
        def add_compact_info_row(parent_widget, current_row_list_ref, col_label_idx, col_value_idx, label_text_val, value_text_val, value_font_override=None, value_wraplength=200, value_columnspan=1):
            effective_value_font = value_font_override if value_font_override else INPUT_FONT
            ctk.CTkLabel(parent_widget, text=f"{label_text_val}:", font=LABEL_FONT, anchor="e").grid(row=current_row_list_ref[0], column=col_label_idx, sticky="e", padx=(0,3), pady=2)
            ctk.CTkLabel(parent_widget, text=str(value_text_val) if value_text_val is not None else "N/A", font=effective_value_font, anchor="w", wraplength=value_wraplength).grid(row=current_row_list_ref[0], column=col_value_idx, sticky="w", pady=2, columnspan=value_columnspan)
        person_name_s3 = self.current_person_name or self.fetched_schedule_data.get("scheduleName", "N/A")
        person_id_s3 = self.fetched_schedule_data.get("idNumber", "N/A")
        schedule_name_s3 = self.fetched_schedule_data.get("scheduleName", "N/A")
        department_name_s3 = self.fetched_schedule_data.get("departmentName", "N/A")
        add_compact_info_row(personal_validity_content_frame, current_display_row_idx, 0, 1, "Họ Tên", person_name_s3)
        add_compact_info_row(personal_validity_content_frame, current_display_row_idx, 2, 3, "CCCD/ID (từ API)", person_id_s3)
        current_display_row_idx[0] += 1
        add_compact_info_row(personal_validity_content_frame, current_display_row_idx, 0, 1, "Tên Lịch LV", schedule_name_s3)
        add_compact_info_row(personal_validity_content_frame, current_display_row_idx, 2, 3, "Phòng Ban", department_name_s3)
        current_display_row_idx[0] += 1
        from_display_str = f"{self.from_day_str}/{self.from_month_str}/{self.from_year_str} {self.from_hour_str}:{self.from_minute_str}"
        add_compact_info_row(personal_validity_content_frame, current_display_row_idx, 0, 1, "Hiệu lực từ", from_display_str)
        to_display_str = f"{self.to_day_str}/{self.to_month_str}/{self.to_year_str} {self.to_hour_str}:{self.to_minute_str}"
        add_compact_info_row(personal_validity_content_frame, current_display_row_idx, 2, 3, "Hiệu lực đến", to_display_str)
        current_display_row_idx[0] += 1
        day_names_map = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ Nhật"]
        active_days_display_list = [day_names_map[i] for i, act in enumerate(self.active_day_mask_list) if act and i < len(day_names_map)]
        active_days_display_str = ", ".join(active_days_display_list) or "Không ngày nào hoạt động"
        add_compact_info_row(personal_validity_content_frame, current_display_row_idx, 0, 1, "Lịch hoạt động", active_days_display_str, value_wraplength=320, value_columnspan=3)
        current_display_row_idx[0] += 1
        bio_outer_frame = ctk.CTkFrame(self.main_frame, fg_color=BG_COLOR, corner_radius=8)
        bio_outer_frame.pack(fill="both", expand=True, padx=PAD_X_SECTION, pady=(PAD_Y_SECTION, PAD_Y_SECTION))
        ctk.CTkLabel(bio_outer_frame, text="Thông Tin Sinh Trắc Học Đã Đăng Ký", font=STEP_TITLE_FONT, text_color=ACCENT_COLOR, anchor="w").pack(fill="x", padx=12, pady=(8, 5))
        bio_content_cols = ctk.CTkFrame(bio_outer_frame, fg_color="transparent")
        bio_content_cols.pack(fill="both", expand=True, padx=5, pady=(0, 8)); bio_content_cols.columnconfigure((0,1,2), weight=1, uniform="bio_info_col_s3_uniform_group"); bio_content_cols.rowconfigure(0, weight=1)
        def create_bio_col_display(parent_widget_for_grid, grid_col_index, bio_type_title, is_enrolled_flag, ctk_image_obj=None, rfid_uid_display_text=None):
            col_content_frame = ctk.CTkFrame(parent_widget_for_grid, fg_color=SCREEN_BG_COLOR, corner_radius=6, border_width=1, border_color="gray70")
            col_content_frame.grid(row=0, column=grid_col_index, sticky="nsew", padx=5, pady=3)
            ctk.CTkLabel(col_content_frame, text=bio_type_title, font=LABEL_FONT, text_color=ACCENT_COLOR).pack(pady=(8,3))
            status_text_val = "ĐÃ ĐĂNG KÝ" if is_enrolled_flag else "CHƯA ĐĂNG KÝ"; status_text_color_val = SUCCESS_COLOR if is_enrolled_flag else WARNING_COLOR
            ctk.CTkLabel(col_content_frame, text=status_text_val, font=INPUT_FONT, text_color=status_text_color_val).pack(pady=(0,8))
            if is_enrolled_flag:
                if bio_type_title == "Khuôn Mặt" and ctk_image_obj: ctk.CTkLabel(col_content_frame, image=ctk_image_obj, text="").pack(pady=(0,8),expand=True,anchor="center")
                elif bio_type_title == "Thẻ RFID/IDCard" and rfid_uid_display_text: ctk.CTkLabel(col_content_frame, text=str(rfid_uid_display_text), font=("Segoe UI", 13, "italic"), text_color="gray20").pack(pady=(0,8))
        self.preview_face_image_ctk_s3_obj = None
        if self.current_face_image_b64:
            try:
                img_bytes = base64.b64decode(self.current_face_image_b64); pil_image_face = Image.open(io.BytesIO(img_bytes))
                preview_dimensions = (70,70); pil_image_face.thumbnail(preview_dimensions, Image.Resampling.LANCZOS)
                final_pil_preview = Image.new("RGBA", preview_dimensions, (0,0,0,0)) 
                paste_x_coord = (preview_dimensions[0] - pil_image_face.width) // 2; paste_y_coord = (preview_dimensions[1] - pil_image_face.height) // 2
                final_pil_preview.paste(pil_image_face, (paste_x_coord, paste_y_coord))
                self.preview_face_image_ctk_s3_obj = CTkImage(light_image=final_pil_preview,dark_image=final_pil_preview,size=preview_dimensions)
            except Exception as e_face_preview: print(f"[Enroll ERROR] Creating face preview for Step 3: {e_face_preview}")
        create_bio_col_display(bio_content_cols, 0, "Khuôn Mặt", bool(self.current_face_template_b64), self.preview_face_image_ctk_s3_obj)
        create_bio_col_display(bio_content_cols, 1, "Vân Tay", bool(self.current_finger_template_b64))
        rfid_uid_text = f"UID: {self.current_rfid_uid_str}" if self.current_rfid_uid_str else None
        create_bio_col_display(bio_content_cols, 2, "Thẻ RFID/IDCard", bool(self.current_rfid_uid_str), rfid_uid_display_text=rfid_uid_text)
        if hasattr(self, 'nav_frame') and self.nav_frame and self.nav_frame.winfo_exists(): self.nav_frame.destroy()
        self.nav_frame = ctk.CTkFrame(self.root, fg_color=BG_COLOR)
        self.nav_frame.place(relx=0.5, rely=1.0, anchor="s", relwidth=1.0, relheight=0.12)
        ctk.CTkButton(self.nav_frame, text="SỬA SINH TRẮC (B2)", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH, height=LARGE_BUTTON_HEIGHT, command=self._action_goto_step2_from_step3, image=self.back_icon, compound="left", corner_radius=8, fg_color="#A0A0A0").pack(side="left", pady=(5,8), padx=PAD_X_MAIN_CONTAINER)
        ctk.CTkButton(self.nav_frame, text="GỬI ĐĂNG KÝ (HTTP)", font=BUTTON_FONT, width=LARGE_BUTTON_WIDTH + 20, height=LARGE_BUTTON_HEIGHT, command=self.prepare_and_send_data_http, fg_color=SUCCESS_COLOR, text_color=BUTTON_FG_TEXT, image=self.send_icon_large, compound="right", corner_radius=8).pack(side="right", pady=(5,8), padx=PAD_X_MAIN_CONTAINER)

    def _action_goto_step2_from_step3(self):
        if self.screen_history and self.screen_history[-1][0] == "step3_confirmation":
            self.screen_history.pop()
            if self.screen_history and self.screen_history[-1][0] == "step2_biometrics":
                prev_screen_id, prev_screen_func, prev_args = self.screen_history[-1]
                self.clear_frames(); self.root.update_idletasks(); prev_screen_func(*prev_args)
                return
        self.push_screen("step2_biometrics", self.show_step2_biometric_screen)

    def _action_goto_step0_from_step3(self):
        self.reset_enrollment_state_full()
        self.screen_history = []
        self.push_screen("step0_id_input", self.show_step0_id_input_screen)

    def generate_active_days_mask_from_list(self):
        mask = ['0'] * 7
        for i, is_active in enumerate(self.active_day_mask_list):
            if i < 7 and is_active: mask[i] = '1'
        return "".join(mask)

    def reset_enrollment_state_full(self):
        self.generate_new_bio_id()
        self.fetched_schedule_data = None
        self.current_cccd_for_schedule = ""
        self.current_id_number = ""; self.current_person_name = ""; self.current_room_name_selected = None
        now_datetime = datetime.now()
        self.from_hour_str = "00"; self.from_minute_str = "00"; self.from_second_str = "00"
        self.from_day_str = now_datetime.strftime("%d"); self.from_month_str = now_datetime.strftime("%m"); self.from_year_str = now_datetime.strftime("%Y")
        to_datetime_default = now_datetime + timedelta(days=6) 
        self.to_hour_str = "23"; self.to_minute_str = "59"; self.to_second_str = "59"
        self.to_day_str = to_datetime_default.strftime("%d"); self.to_month_str = to_datetime_default.strftime("%m"); self.to_year_str = to_datetime_default.strftime("%Y")
        self.active_day_mask_list = [True] * 7
        self.current_face_image_b64 = None; self.current_face_template_b64 = None
        self.current_finger_template_b64 = None; self.current_rfid_uid_str = None
        self.preview_face_image_ctk = None
        if DEBUG: print("[Enroll DEBUG] Enrollment state fully reset for new session.")

    def cleanup(self):
        if DEBUG: print("[Enroll INFO] Application cleanup process started...")
        face_enroll.stop_face_capture()
        if self.mqtt_manager:
            if DEBUG: print("[Enroll INFO] Explicitly disconnecting MQTT client...")
            self.mqtt_manager.disconnect_client(explicit=True)
        if self.root and self.root.winfo_exists(): self.root.destroy()
        if DEBUG: print("[Enroll INFO] Application cleanup finished.")

if __name__ == "__main__":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass
    ctk.set_appearance_mode("Light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
    root.title("Trạm Đăng Ký Sinh Trắc Học - Navis SmartLock")
    app = EnrollmentApp(root)
    root.mainloop()