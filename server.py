# server.py
from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import hashlib
import base64
import uuid
from datetime import datetime, timedelta, timezone
import json
import paho.mqtt.client as mqtt
import threading

app = FastAPI(title="SmartLock API Server")

MQTT_BROKER_HOST = "192.168.0.102"
MQTT_BROKER_PORT = 1883
MQTT_SERVER_CLIENT_ID = f"api_server_{uuid.uuid4().hex[:6]}"
MQTT_SERVER_USERNAME = "20242"
MQTT_SERVER_PASSWORD = "20242"

server_mqtt_client = mqtt.Client(client_id=MQTT_SERVER_CLIENT_ID, protocol=mqtt.MQTTv5)
server_mqtt_connected = False

# --- (Các hàm MQTT, helper functions, Pydantic models giữ nguyên như trước) ---
def on_server_mqtt_connect(client, userdata, flags, rc, properties=None):
    global server_mqtt_connected
    if rc == 0:
        print("[SERVER MQTT] Connected to MQTT Broker successfully.")
        server_mqtt_connected = True
    else:
        print(f"[SERVER MQTT] Failed to connect to MQTT Broker, return code {rc}")
        server_mqtt_connected = False

def on_server_mqtt_disconnect(client, userdata, rc, properties=None):
    global server_mqtt_connected
    print(f"[SERVER MQTT] Disconnected from MQTT Broker, return code {rc}.")
    server_mqtt_connected = False

server_mqtt_client.on_connect = on_server_mqtt_connect
server_mqtt_client.on_disconnect = on_server_mqtt_disconnect

def connect_server_to_mqtt_broker():
    try:
        if MQTT_SERVER_USERNAME and MQTT_SERVER_PASSWORD:
             server_mqtt_client.username_pw_set(MQTT_SERVER_USERNAME, MQTT_SERVER_PASSWORD)
        server_mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        thread = threading.Thread(target=server_mqtt_client.loop_forever)
        thread.daemon = True
        thread.start()
    except Exception as e:
        print(f"[SERVER MQTT] Could not connect to MQTT broker: {e}")

VALID_DEVICE_CREDENTIALS = {
    "DC:A6:32:B8:5B:DB": "mKzGIMkC1sVapQ+mqT4semjDJFeY8tyNbwgdE0kRKKQ=",
    "D8:3A:DD:51:09:02": "SGl0MHRtT2w0ek1iNXRpT2hWUnFlV3V6NHV0WjZXVW5pYWVpM0pNc3E0bz0=",
    "YOUR_ENROLLMENT_STATION_MAC": "YOUR_ENROLLMENT_STATION_HASHED_PASSWORD"
}
MQTT_TOKENS_FOR_DEVICES = {
    "DC:A6:32:B8:5B:DB": "6e83f8f27c9a79c788271459c76794c8515ea337ad86eb6eb5cc88681c40fc28",
    "D8:3A:DD:51:09:02": "49fb5ec0d7e73bf8a3efffb3de9321f92b10fbf0702d8ffa8e9356c2e57cec89",
    "YOUR_ENROLLMENT_STATION_MAC": "SOME_PASSWORD_OR_TOKEN_FOR_ENROLLMENT_STATION_MQTT"
}

VISITOR_BIO_DB = {}
API_ACCESS_TOKENS_DB = {}
TOKEN_EXPIRY_SECONDS = 3600 * 24
SCHEDULE_TO_DOOR_MACS = {
    1001: ["D8:3A:DD:51:09:02"],
    1002: ["D8:3A:DD:51:09:02", "DC:A6:32:B8:5B:DB"]
}

# --- THAY ĐỔI: Hàm tạo ngày tháng động và Khởi tạo SCHEDULE_DB động ---
def get_dynamic_schedule_dates():
    """Trả về ngày bắt đầu (hôm nay) và ngày kết thúc (1 năm sau) ở định dạng ISO."""
    today = datetime.now(timezone.utc)
    one_year_later = today + timedelta(days=365)
    # Format về YYYY-MM-DDTHH:MM:SS (không có Z vì client và server sẽ xử lý múi giờ)
    # Hoặc bạn có thể thêm 'Z' nếu muốn rõ ràng là UTC: .isoformat().replace("+00:00", "Z")
    return today.isoformat(), one_year_later.isoformat()

# Khởi tạo SCHEDULE_DB với ngày tháng động
# Lấy thời điểm hiện tại khi module được load (server khởi động)
current_start_date_iso, current_end_date_iso = get_dynamic_schedule_dates()

SCHEDULE_DB = {
    "030081000101": [{
        "scheduleId": 1001,
        "idNumber": "030081000101",
        "scheduleName": "Nguyen Van A - Ca Sáng Hành Chính",
        "departmentName": "Phòng Kỹ Thuật",
        "fromDate": current_start_date_iso, # Ngày bắt đầu động
        "toDate": current_end_date_iso,     # Ngày kết thúc động
        "fromTime": "08:00:00",
        "toTime": "17:00:00",
        "activeDays": "1111111"
    }],
    "030081000102": [{
        "scheduleId": 1002,
        "idNumber": "030081000102",
        "scheduleName": "Tran Thi B - Ca Chiều Bảo Vệ",
        "departmentName": "Bộ Phận An Ninh",
        "fromDate": current_start_date_iso, # Ngày bắt đầu động
        "toDate": current_end_date_iso,     # Ngày kết thúc động
        "fromTime": "14:00:00",
        "toTime": "22:00:00",
        "activeDays": "1111111"
    }]
}
# --- KẾT THÚC THAY ĐỔI ---


def generate_hashed_password_server(mac):
    data = (mac + "navis@salt").encode("utf-8")
    hash_bytes = hashlib.sha256(data).digest()
    return base64.b64encode(hash_bytes).decode("utf-8")
def create_api_access_token(mac_address: str) -> tuple[str, datetime]:
    token = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=TOKEN_EXPIRY_SECONDS)
    API_ACCESS_TOKENS_DB[token] = {"macAddress": mac_address, "expiresAt": expires_at}
    return token, expires_at
def verify_api_access_token(token: str) -> Optional[str]:
    token_data = API_ACCESS_TOKENS_DB.get(token)
    if token_data:
        # print(f"[SERVER DEBUG verify_token] Token found for '{token[:10]}...'. ExpiresAt: {token_data['expiresAt']}, Now_UTC: {datetime.now(timezone.utc)}")
        if token_data["expiresAt"] > datetime.now(timezone.utc):
            # print(f"[SERVER DEBUG verify_token] Token for MAC {token_data['macAddress']} is VALID.")
            return token_data["macAddress"]
        # else:
            # print(f"[SERVER DEBUG verify_token] Token for MAC {token_data['macAddress']} has EXPIRED.")
    # else:
        # print(f"[SERVER DEBUG verify_token] Token '{token[:10]}...' NOT FOUND in DB.")
    if token in API_ACCESS_TOKENS_DB: del API_ACCESS_TOKENS_DB[token]
    return None

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/devicecomm/gettoken")
async def get_current_active_device(token: str = Depends(oauth2_scheme)) -> str:
    mac_address = verify_api_access_token(token)
    if not mac_address: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired API token", headers={"WWW-Authenticate": "Bearer"})
    return mac_address

class TokenRequest(BaseModel): macAddress: str; password: str
class MqttTokenResponseData(BaseModel): token: str; username: str
class ApiTokenResponseData(BaseModel): token: str; username: str; expiresIn: int
class Schedule(BaseModel): scheduleId: int; idNumber: str; scheduleName: str; departmentName: Optional[str] = None; fromDate: str; toDate: str; fromTime: str; toTime: str; activeDays: str
class BioImagePayload(BaseModel): Img: Optional[str] = None; Template: Optional[str] = None
class VisitorBioUploadRequest(BaseModel): idNumber: str; ScheduleId: int; FaceImg: List[BioImagePayload] = Field(default_factory=list); FingerImg: List[BioImagePayload] = Field(default_factory=list); IrisImg: List[BioImagePayload] = Field(default_factory=list); CaptureTime: str; CreatedBy: str
class ApiResponse(BaseModel): code: str; message: str; data: Optional[Any] = None


@app.on_event("startup")
async def startup_event_main(): # Đổi tên để tránh trùng với startup_event đã dùng cho MQTT
    print("[FastAPI Startup] Main startup event: Connecting server to MQTT broker...")
    connect_server_to_mqtt_broker()
    # In ra ngày tháng của lịch để kiểm tra
    print(f"[FastAPI Startup] Dynamic schedule dates used: From {current_start_date_iso} To {current_end_date_iso}")


@app.on_event("shutdown")
async def shutdown_event_main(): # Đổi tên để tránh trùng
    if server_mqtt_connected:
        print("[FastAPI Shutdown] Main shutdown event: Disconnecting server from MQTT broker...")
        server_mqtt_client.disconnect()


@app.post("/api/devicecomm/getmqtttoken", response_model=ApiResponse)
async def get_mqtt_token_api(payload: TokenRequest):
    mac_address = payload.macAddress
    if mac_address not in MQTT_TOKENS_FOR_DEVICES: return JSONResponse(status_code=401, content={"code": "UNAUTHORIZED", "message": "MAC address not registered for MQTT"})
    token_data = MqttTokenResponseData(token=MQTT_TOKENS_FOR_DEVICES[mac_address], username=mac_address)
    return ApiResponse(code="OK", message="Success", data=token_data.dict())

@app.post("/api/devicecomm/gettoken", response_model=ApiResponse)
async def get_enrollment_api_token(payload: TokenRequest):
    mac_address = payload.macAddress; client_hashed_password = payload.password
    # print(f"[SERVER DEBUG /gettoken] Received MAC: {mac_address}")
    # print(f"[SERVER DEBUG /gettoken] Received Hashed Password from Client: '{client_hashed_password}'")
    stored_hashed_password = VALID_DEVICE_CREDENTIALS.get(mac_address)
    # print(f"[SERVER DEBUG /gettoken] Stored Hashed Password for MAC {mac_address}: '{stored_hashed_password}'")
    if not stored_hashed_password or client_hashed_password != stored_hashed_password: return JSONResponse(status_code=401, content={"code": "UNAUTHORIZED", "message": "Invalid MAC or password for API token"})
    access_token, expires_at = create_api_access_token(mac_address)
    # print(f"[SERVER DEBUG /gettoken] Created API token '{access_token}' for MAC {mac_address}, expires at {expires_at}")
    # print(f"[SERVER DEBUG /gettoken] Current API_ACCESS_TOKENS_DB keys: {list(API_ACCESS_TOKENS_DB.keys())}")
    token_response_data = ApiTokenResponseData(token=access_token, username=mac_address, expiresIn=TOKEN_EXPIRY_SECONDS)
    return ApiResponse(code="OK", message="API Token granted", data=token_response_data.dict())

@app.get("/api/schedule/getschedule", response_model=ApiResponse)
async def get_schedule_api(idNumber: str, current_device_mac: str = Depends(get_current_active_device)):
    schedules_list = SCHEDULE_DB.get(idNumber)
    if not schedules_list: return ApiResponse(code="OK", message="No schedule found", data=[])
    return ApiResponse(code="OK", message="Schedule retrieved", data=schedules_list)

@app.post("/api/visitorbio/upload", response_model=ApiResponse)
async def upload_visitor_bio_api(payload: VisitorBioUploadRequest, current_device_mac: str = Depends(get_current_active_device)):
    print(f"[API /upload] Bio data for idNumber: {payload.idNumber}, ScheduleId: {payload.ScheduleId} from station: {current_device_mac}")
    if payload.CreatedBy != current_device_mac: print(f"[API /upload] WARN: Token MAC {current_device_mac} != Payload MAC {payload.CreatedBy}")

    registration_key = f"{payload.idNumber}_{payload.ScheduleId}"
    VISITOR_BIO_DB[registration_key] = payload.dict()
    VISITOR_BIO_DB[registration_key]["receivedAt"] = datetime.now(timezone.utc).isoformat()
    print(f"[API /upload] Bio data for {payload.idNumber} stored (simulated).")

    target_door_macs = SCHEDULE_TO_DOOR_MACS.get(payload.ScheduleId, [])
    if not target_door_macs:
        print(f"[API /upload] No door MACs for ScheduleId: {payload.ScheduleId}.")
    else:
        if not server_mqtt_connected:
            print("[API /upload] Server not connected to MQTT Broker. Cannot push to doors.")
        else:
            user_schedule_data = None
            schedules_for_id = SCHEDULE_DB.get(payload.idNumber)
            if schedules_for_id:
                for sched in schedules_for_id:
                    if sched.get("scheduleId") == payload.ScheduleId:
                        user_schedule_data = sched; break
            if not user_schedule_data:
                print(f"[API /upload] CRITICAL: No schedule details for idNumber {payload.idNumber}, ScheduleId {payload.ScheduleId}.")
            else:
                person_name_from_schedule = user_schedule_data.get("scheduleName", "Unknown")
                if " - " in person_name_from_schedule:
                    person_name_from_schedule = person_name_from_schedule.split(" - ")[0].strip()

                face_img_for_mqtt = None
                face_temps_for_mqtt = []
                if payload.FaceImg:
                    face_img_for_mqtt = payload.FaceImg[0].Img
                    face_temps_for_mqtt = [entry.Template for entry in payload.FaceImg if entry.Template is not None]

                finger_temps_for_mqtt = [entry.Template for entry in payload.FingerImg if entry.Template is not None] if payload.FingerImg else []
                iris_temps_for_mqtt = [entry.Template for entry in payload.IrisImg if entry.Template is not None] if payload.IrisImg else []

                mqtt_push_payload = {
                    "DoorId": 0,
                    "BioId": int(payload.idNumber) if payload.idNumber.isdigit() else payload.idNumber,
                    "IdNumber": payload.idNumber,
                    "PersonName": person_name_from_schedule,
                    "CmdType": "PUSH_NEW_BIO",
                    "FromDate": user_schedule_data.get("fromDate", "").split("T")[0],
                    "ToDate": user_schedule_data.get("toDate", "").split("T")[0],
                    "FromTime": user_schedule_data.get("fromTime", "00:00:00"),
                    "ToTime": user_schedule_data.get("toTime", "23:59:59"),
                    "ActiveDays": user_schedule_data.get("activeDays", "0000000"),
                    "FaceImg": face_img_for_mqtt,
                    "FaceTemps": face_temps_for_mqtt,
                    "FingerTemps": finger_temps_for_mqtt,
                    "IrisTemps": iris_temps_for_mqtt
                }
                if not mqtt_push_payload["FaceTemps"]: del mqtt_push_payload["FaceTemps"]
                if mqtt_push_payload["FaceImg"] is None: del mqtt_push_payload["FaceImg"]
                if not mqtt_push_payload["FingerTemps"]: del mqtt_push_payload["FingerTemps"]
                if not mqtt_push_payload["IrisTemps"]: del mqtt_push_payload["IrisTemps"]

                for door_mac in target_door_macs:
                    target_topic = f"iot/server/push_biometric/{door_mac}"
                    try:
                        payload_str = json.dumps(mqtt_push_payload)
                        result = server_mqtt_client.publish(target_topic, payload_str, qos=1)
                        result.wait_for_publish(timeout=5)
                        if result.rc == mqtt.MQTT_ERR_SUCCESS:
                            print(f"[API /upload -> MQTT Push] OK to {target_topic} for MAC {door_mac}")
                        else:
                            print(f"[API /upload -> MQTT Push] FAILED to {target_topic}. RC: {result.rc}")
                    except Exception as e_mqtt_pub:
                        print(f"[API /upload -> MQTT Push] ERROR publishing to {target_topic}: {e_mqtt_pub}")
    return ApiResponse(code="OK", message="Biometric data uploaded and push attempt initiated.")
