# mqtt.py (Giữ nguyên payload gốc)
import json
import time
import ssl
import requests
import socket
import hashlib
import base64
from datetime import datetime, timezone, timedelta
import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
from database import enqueue_outgoing_message, get_pending_outbox, mark_outbox_sent
# from door import Door # MQTTManager không nên tự tạo Door, mà nhận từ bên ngoài

try:
    from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1, FINGERPRINT_CHARBUFFER2
except ImportError:
    PyFingerprint = None
except Exception:
    PyFingerprint = None

# InsightFace không dùng trực tiếp trong MQTT logic này
# try:
#     from insightface.app import FaceAnalysis
#     import cv2
#     face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
#     face_app.prepare(ctx_id=0)
# except Exception:
#     face_app = None

import database

# MQTT Topics (giữ nguyên từ file gốc của bạn)
MQTT_DEVICE_INFO_TOPIC = "iot/devices/device_info"
MQTT_HEALTHCHECK_TOPIC = "iot/devices/healthcheck"
MQTT_ACCESS_CONTROL = "iot/devices/access" # Topic cho sự kiện truy cập
MQTT_SYNC_REQUEST_TOPIC = "iot/devices/device_sync_bio"
MQTT_BIO_ACK_TOPIC = "iot/devices/device_received_bio"
MQTT_SOS_ALERT_TOPIC = "iot/devices/sos"
MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE = "iot/server/{mac_address}/push_biometric"
MQTT_COMMAND_TOPIC = "iot/server/command/{mac_address}"
MQTT_COMMAND_RESPONSE_TOPIC = "iot/devices/command_resp"

GMT_PLUS_7 = timezone(timedelta(hours=7))

def generate_hashed_password(mac): # Giữ nguyên
    data = (mac + "navis@salt").encode("utf-8")
    hash_bytes = hashlib.sha256(data).digest()
    return base64.b64encode(hash_bytes).decode("utf-8")

def is_network_available(): # Giữ nguyên
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except OSError:
        return False
    except Exception:
        return False

class MQTTManager:
    def __init__(self, mqtt_config, mac, fingerprint_sensor=None, rfid_sensor=None, door_handler=None, debug=True):
        self.mqtt_config = mqtt_config
        self.mac = mac
        self.door = door_handler # Sử dụng Door handler được truyền vào

        self.username = mqtt_config.get("mqtt_username") # Username MQTT lấy từ config (sau khi có token)
        self.token = mqtt_config.get("token")         # Password MQTT (chính là token) lấy từ config
        
        self._client = None
        self.connected = False
        self.connecting = False
        self.debug = debug
        
        self.on_token_received = None # Callback khi nhận token mới từ HTTP
        self.on_connection_status_change = None # Callback cập nhật trạng thái kết nối
        
        self.push_biometric_topic = MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE.format(mac_address=self.mac)
        self.command_topic = MQTT_COMMAND_TOPIC.format(mac_address=self.mac)
        
        self.fingerprint_sensor = fingerprint_sensor
        self.rfid_sensor = rfid_sensor # Thêm RFID sensor

        self.device_info_sent_this_session = False

    @property
    def client(self): # Giữ nguyên
        return self._client

    def is_connected(self): # Giữ nguyên
        return self.connected

    def set_fingerprint_sensor(self, sensor): # Giữ nguyên
        self.fingerprint_sensor = sensor
        
    def set_rfid_sensor(self, sensor): # Giữ nguyên
        self.rfid_sensor = sensor

    def set_door_handler(self, handler): # Giữ nguyên
        self.door = handler

    def disconnect_client(self): # Giữ nguyên, có thể thêm log chi tiết hơn
        if self._client is not None:
            try:
                self.connected = False # Đặt trạng thái ngay lập tức
                self.connecting = False
                if self.on_connection_status_change: # Thông báo trạng thái
                    self.on_connection_status_change(False)
                
                self._client.loop_stop(force=True) 
                self._client.disconnect()
                if self.debug: print(f"[MQTT DEBUG] MQTT client for MAC {self.mac} explicitly disconnected.")
            except Exception as e:
                if self.debug: print(f"[MQTT DEBUG] Error during MQTT client disconnect for MAC {self.mac}: {e}")
            finally:
                self._client = None 
        # else:
            # if self.debug: print(f"[MQTT TRACE] disconnect_client called but no active client for MAC {self.mac}.")


    def connect_and_register(self): # Giữ nguyên logic này
        if self.token and self.username: 
            if self.connect_with_token():
                return True

        if not self.retrieve_token_via_http():
            if self.debug: print(f"[MQTT ERROR] Cannot retrieve MQTT token via HTTP for MAC {self.mac}.")
            return False
        return self.connect_with_token()

    def retrieve_token_via_http(self) -> bool: # Giữ nguyên logic này
        server_address_conf = self.mqtt_config.get('server')
        http_port_conf = self.mqtt_config.get('http_port')

        if not server_address_conf:
            if self.debug: print(f"[MQTT ERROR] 'server' not configured for HTTP token request (MAC: {self.mac}).")
            return False
        
        http_port = 8080 
        if http_port_conf is not None:
            try:
                http_port = int(http_port_conf)
            except ValueError:
                if self.debug: print(f"[MQTT ERROR] Invalid http_port in config: {http_port_conf} (MAC: {self.mac}). Using default 8080.")
        
        api_base_url = server_address_conf.strip().rstrip('/')
        if not api_base_url.startswith(('http://', 'https://')):
            api_base_url = f"http://{api_base_url}"
        
        url = f"{api_base_url}:{http_port}/api/devicecomm/getmqtttoken"
        payload = {"macAddress": self.mac, "password": generate_hashed_password(self.mac)}

        try:
            if self.debug: print(f"[MQTT DEBUG] Requesting token from {url} (MAC: {self.mac})")
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            error_text = e.response.text if e.response is not None else "No response body"
            if self.debug: print(f"[MQTT ERROR] HTTP token request failed status {e.response.status_code if e.response else 'N/A'} for MAC {self.mac}: {error_text}")
            return False
        except requests.exceptions.RequestException as e:
            if self.debug: print(f"[MQTT ERROR] HTTP token request failed (network/timeout) for MAC {self.mac}: {e}")
            return False
        except json.JSONDecodeError:
            if self.debug: print(f"[MQTT ERROR] Failed to decode JSON response from token API (MAC: {self.mac}).")
            return False
        except Exception as e:
            if self.debug: print(f"[MQTT ERROR] HTTP token request failed (other) for MAC {self.mac}: {e}")
            return False

        if data.get("code") != "OK" or "data" not in data:
            if self.debug: print(f"[MQTT ERROR] Unexpected response from token API for MAC {self.mac}: {data}")
            return False

        api_data = data.get("data", {})
        new_token = api_data.get("token")
        new_username = api_data.get("username")

        if not new_token or not new_username:
            if self.debug: print(f"[MQTT ERROR] Missing token/username in API response data for MAC {self.mac}: {api_data}")
            return False

        self.token = new_token
        self.username = new_username
        self.device_info_sent_this_session = False 

        if self.on_token_received:
            self.on_token_received(new_username, new_token) # App (main.py) sẽ lưu vào config
        else:
            if self.debug: print(f"[MQTT WARN] on_token_received callback not set for MAC {self.mac}. Token updated locally.")

        if self.debug: print(f"[MQTT DEBUG] Retrieved token via HTTP for MAC {self.mac}. username={new_username}")
        return True

    def on_disconnect(self, client, userdata, rc, properties=None): # Giữ nguyên
        current_state_was_connected = self.connected # Lưu trạng thái cũ
        self.connected = False
        self.connecting = False
        if self.debug: print(f"[MQTT DEBUG] MQTT disconnected for MAC {self.mac}. Reason code: {rc}")
        if current_state_was_connected and self.on_connection_status_change: # Chỉ gọi nếu trước đó đã connected
            self.on_connection_status_change(False)
        # Cân nhắc logic retry, ví dụ:
        # if rc != 0: # Nếu không phải ngắt kết nối chủ động
        #    time.sleep(5) # Đợi 5s
        #    self.connect_with_token() # Thử kết nối lại

    def on_subscribe(self, client, userdata, mid, granted_qos, properties=None): # Giữ nguyên
        if self.debug: print(f"[MQTT DEBUG] Subscribed (MAC: {self.mac}): mid={mid}, QoS={granted_qos}")

    def on_publish(self, client, userdata, mid, *args, **kwargs): # Giữ nguyên
        pass

    def on_message(self, client, userdata, msg): # CẬP NHẬT CHO RFID
        try:
            topic = msg.topic
            payload_str = msg.payload.decode('utf-8')
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Received on '{topic}': {payload_str[:150]}{'...' if len(payload_str)>150 else ''}")

            if topic == self.push_biometric_topic:
                if not self.connected: # Bỏ qua nếu không kết nối (token sẽ được kiểm tra bởi server)
                    if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Biometric push ignored, not connected.")
                    return
                try:
                    command_list = json.loads(payload_str)
                    if not isinstance(command_list, list):
                        if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Invalid biometric push: Expected list.")
                        return

                    sync_all_processed_delete = False
                    for command_item in command_list:
                        if not isinstance(command_item, dict):
                            if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Skipping invalid item in bio push list (not a dict).")
                            continue

                        cmd_type = command_item.get("cmdType")
                        bio_id = command_item.get("bioId")
                        processed_ok = False # Đổi tên biến này cho rõ ràng hơn
                        finger_position_for_db = None

                        if cmd_type == "SYNC_ALL":
                            if not sync_all_processed_delete:
                                if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) SYNC_ALL: Clearing sensor and DB.")
                                sensor_cleared = False
                                if self.fingerprint_sensor and PyFingerprint is not None:
                                    try:
                                        if self.fingerprint_sensor.verifyPassword():
                                            if self.fingerprint_sensor.clearDatabase():
                                                sensor_cleared = True
                                            else:
                                                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Failed to clear sensor DB.")
                                        else:
                                            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Sensor password verify failed (SYNC_ALL).")
                                    except Exception as e:
                                        if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exception clearing sensor: {e}")
                                else: # Không có sensor, coi như clear thành công
                                    sensor_cleared = True 
                                    if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Fingerprint sensor not available for SYNC_ALL clear.")
                                
                                db_cleared = database.delete_all_biometrics_and_access_for_mac(self.mac) # Cần hàm này
                                processed_ok = db_cleared and sensor_cleared
                                sync_all_processed_delete = True # Đánh dấu đã xóa

                            if 'bioDatas' in command_item and bio_id: # Nếu SYNC_ALL có data đi kèm
                                if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) SYNC_ALL includes PUSH_NEW data for bioId: {bio_id}")
                                cmd_type = "PUSH_NEW_BIO" # Xử lý như PUSH_NEW_BIO
                            else: # Nếu SYNC_ALL chỉ là lệnh xóa
                                if not bio_id and processed_ok: # Kiểm tra processed_ok từ bước xóa
                                     if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) SYNC_ALL (clear only) processed.")
                                # Không gửi ACK cho SYNC_ALL (chỉ xóa), server không mong đợi ACK này.
                                continue # Xong phần SYNC_ALL (chỉ xóa)

                        if cmd_type in ["PUSH_NEW_BIO", "PUSH_UPDATE_BIO"]:
                            if not bio_id:
                                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Skipping {cmd_type}: Missing 'bioId'.")
                                continue
                            if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Processing {cmd_type} for bioId: {bio_id}")
                            
                            finger_op_success = True # Mặc định
                            face_op_success = True   # Mặc định
                            idcard_op_success = True # Mặc định

                            for bio_data in command_item.get('bioDatas', []):
                                bio_data_type = bio_data.get("BioType", "").upper()
                                template_b64 = bio_data.get("Template")

                                if bio_data_type == "FINGER" and template_b64:
                                    if self.fingerprint_sensor and PyFingerprint is not None:
                                        try:
                                            padding = '=' * (-len(template_b64) % 4)
                                            template_bytes = base64.b64decode(template_b64.strip() + padding)
                                            template_list = list(template_bytes)
                                            if self.fingerprint_sensor.verifyPassword():
                                                if self.fingerprint_sensor.uploadCharacteristics(FINGERPRINT_CHARBUFFER1, template_list):
                                                    actual_position = self.fingerprint_sensor.storeTemplate()
                                                    if actual_position >= 0:
                                                        finger_position_for_db = actual_position
                                                    else:
                                                        if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Sensor storeTemplate error for {bio_id}: {actual_position}")
                                                        finger_op_success = False
                                                else:
                                                    if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Failed to upload fingerprint for {bio_id}.")
                                                    finger_op_success = False
                                            else:
                                                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Sensor password verify failed for {bio_id}.")
                                                finger_op_success = False; break
                                        except Exception as e:
                                            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exception enrolling fingerprint {bio_id}: {e}")
                                            finger_op_success = False
                                    else:
                                        if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Fingerprint sensor not available for {bio_id} FINGER data.")
                                        # finger_op_success = False # Coi là lỗi nếu không có sensor mà có data FINGER
                                
                                elif bio_data_type == "FACE":
                                     if not template_b64: # Template (vector) là bắt buộc
                                          if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) FACE template missing for {bio_id}.")
                                          face_op_success = False
                                     # Lưu DB sẽ do process_biometric_push xử lý

                                elif bio_data_type == "IDCARD": # XỬ LÝ CHO THẺ RFID
                                     if not template_b64: # Template (UID thẻ) là bắt buộc
                                          if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) IDCARD template (UID) missing for {bio_id}.")
                                          idcard_op_success = False
                                     # Lưu DB sẽ do process_biometric_push xử lý
                                     # Không có thao tác sensor ở đây vì đây là nhận data.
                                else:
                                     if bio_data_type not in ["FINGER", "FACE", "IDCARD"] and bio_data_type:
                                         if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Unknown BioType: {bio_data_type} for {bio_id}")
                            
                            # Chỉ lưu DB nếu tất cả các thao tác sinh trắc học (sensor, kiểm tra template) đều OK
                            if finger_op_success and face_op_success and idcard_op_success:
                                processed_ok = database.process_biometric_push(command_item, self.mac, finger_position_from_sensor=finger_position_for_db)
                            else:
                                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Skipping DB update for {bio_id} due to FINGER/FACE/IDCARD op failure.")
                                processed_ok = False # Không lưu DB nếu có lỗi

                        elif cmd_type == "PUSH_DELETE_BIO":
                            if not bio_id:
                                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Skipping PUSH_DELETE_BIO: Missing 'bioId'.")
                                continue
                            if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Processing PUSH_DELETE_BIO for {bio_id}")
                            
                            # Xóa khỏi sensor vân tay (nếu có)
                            position_to_delete = database.get_finger_position_by_bio_id_and_mac(bio_id, self.mac) # Cần hàm này
                            sensor_delete_successful = True # Mặc định là true
                            if position_to_delete is not None and self.fingerprint_sensor and PyFingerprint is not None:
                                try:
                                    if self.fingerprint_sensor.verifyPassword():
                                        if not self.fingerprint_sensor.deleteTemplate(position_to_delete):
                                            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Failed to delete from sensor for {bio_id} at pos {position_to_delete}.")
                                            sensor_delete_successful = False
                                        else:
                                             if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Deleted fingerprint from sensor pos {position_to_delete} for {bio_id}.")
                                    else:
                                        if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Sensor password verify failed for PUSH_DELETE_BIO of {bio_id}.")
                                        sensor_delete_successful = False
                                except Exception as e:
                                    if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exception deleting from sensor for {bio_id}: {e}")
                                    sensor_delete_successful = False
                            
                            if sensor_delete_successful: # Chỉ xóa DB nếu xóa sensor thành công (hoặc không cần xóa sensor)
                                processed_ok = database.delete_biometrics_and_access_for_bio_id(bio_id, self.mac) # Cần hàm này
                            else:
                                processed_ok = False # Nếu xóa sensor lỗi, không xóa DB, không ACK

                        else: # cmd_type không phải SYNC_ALL, PUSH_NEW, PUSH_UPDATE, PUSH_DELETE
                            if cmd_type != "SYNC_ALL": # SYNC_ALL (chỉ clear) đã continue ở trên
                                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Unknown cmdType: {cmd_type} for {bio_id}")

                        if processed_ok and bio_id: # Gửi ACK nếu xử lý OK và có bio_id
                            self.send_biometric_ack(bio_id)
                        elif not processed_ok and bio_id: # Log lỗi nếu không OK và có bio_id
                            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Failed processing {cmd_type} for {bio_id}. No ACK sent.")

                except json.JSONDecodeError:
                    if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) JSON decode error in biometric push")
                except Exception as e:
                    if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Error in on_message (biometric push): {e}")

            elif topic == self.command_topic: # Giữ nguyên logic xử lý command
                if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Received command on '{topic}': {payload_str[:150]}...")
                try:
                    command = json.loads(payload_str)
                    if not isinstance(command, dict):
                        if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Invalid command payload: Expected object.")
                        return

                    mac_address = command.get("MacAddress")
                    cmd_id = command.get("CmdId")
                    cmd_type = command.get("CmdType")
                    cmd_time_str = command.get("CmdTime")
                    cmd_timeout = command.get("CmdTimeout", 30) # Mặc định 30s

                    if mac_address != self.mac: # Kiểm tra MAC address
                        if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) MAC mismatch in command. Expected {self.mac}, got {mac_address}. Ignoring.")
                        return

                    # Kiểm tra timeout của lệnh
                    if cmd_time_str:
                        try:
                            cmd_time_dt_utc = datetime.fromisoformat(cmd_time_str.replace("Z", "+00:00"))
                            current_time_utc = datetime.now(timezone.utc)
                            if cmd_timeout > 0 and (current_time_utc - cmd_time_dt_utc).total_seconds() > cmd_timeout:
                                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Command timeout for ID {cmd_id} (Type: {cmd_type}). Ignoring.")
                                return
                        except ValueError:
                            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Invalid CmdTime format '{cmd_time_str}' for ID {cmd_id}. Ignoring.")
                            return
                    
                    action_performed = False
                    if cmd_type == "REMOTE_OPEN":
                        if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Processing REMOTE_OPEN command ID {cmd_id}.")
                        if self.door: 
                            self.door.open_door() # Giả sử door.open_door() không ném lỗi và tự xử lý
                            action_performed = True
                        else:
                             if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Door handler not available for REMOTE_OPEN.")
                    
                    elif cmd_type == "REMOTE_CLOSE":
                        if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Processing REMOTE_CLOSE command ID {cmd_id}.")
                        if self.door:
                            self.door.close_door()
                            action_performed = True
                        else:
                            if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Door handler not available for REMOTE_CLOSE.")
                    else:
                        if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Unknown command type: {cmd_type}. Ignoring.")

                    # Gửi phản hồi lệnh
                    if cmd_id and action_performed: # Chỉ gửi response nếu có cmd_id và đã thực hiện hành động
                        response_payload = {
                            "MacAddress": self.mac,
                            "CmdId": cmd_id,
                            "DeviceTime": datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
                        }
                        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Sending command response: {response_payload}")
                        # Không cần user_properties ở đây vì MAC đã có trong payload
                        self._publish_or_queue(MQTT_COMMAND_RESPONSE_TOPIC, response_payload, qos=1) 
                
                except json.JSONDecodeError:
                    if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) JSON decode error in command processing")
                except KeyError as e:
                    if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Missing key in command payload: {e}")
                except Exception as e_cmd_proc:
                    if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Unhandled error processing command: {e_cmd_proc}")
        
        except Exception as e_on_msg_outer: # Lỗi ngoài cùng của on_message
            if self.debug: print(f"[MQTT CRITICAL] (MAC: {self.mac}) Outer unhandled error in on_message: {e_on_msg_outer}")


    def connect_with_token(self): # Giữ nguyên logic này
        if self.connecting:
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) MQTT connection already in progress.")
            return True 
        if self.connected:
             return True

        if not self.token or not self.username:
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) connect_with_token: Token or username missing.")
            return False # Không thể kết nối nếu thiếu token/username
        
        if not is_network_available():
            if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) No internet, cannot connect MQTT.")
            return False

        self.disconnect_client() # Đảm bảo client cũ đã bị ngắt
        self.connecting = True
        self.connected = False

        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Attempting MQTT connect with user: {self.username}")
        try:
            self._client = mqtt.Client(client_id=self.mac, protocol=mqtt.MQTTv5)
            self._client.on_connect = self.on_connect_token
            self._client.on_disconnect = self.on_disconnect
            self._client.on_message = self.on_message
            self._client.on_subscribe = self.on_subscribe
            # self._client.on_publish = self.on_publish # Bật nếu cần debug

            self._client.username_pw_set(self.username, self.token)
            
            mqtt_broker_port = self.mqtt_config.get("mqtt_port", 1883)
            try:
                mqtt_broker_port = int(mqtt_broker_port) # Đảm bảo port là số
            except ValueError:
                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Invalid MQTT port: {mqtt_broker_port}. Using 1883."); mqtt_broker_port = 1883

            if mqtt_broker_port == 8883: # TLS
                self._client.tls_set(cert_reqs=ssl.CERT_NONE) # Không khuyến nghị cho production
                self._client.tls_insecure_set(True)

            broker_address = self.mqtt_config.get("server", "")
            if not broker_address:
                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) MQTT Broker address (server) not configured.")
                self.connecting = False; return False

            self._client.connect_async(broker_address, mqtt_broker_port, keepalive=60) # Tăng keepalive
            self._client.loop_start()
            return True # Báo là đã bắt đầu kết nối
        except Exception as e:
            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exception during MQTT connect_with_token setup: {e}")
            self.connecting = False; self._client = None; return False


    def on_connect_token(self, client, userdata, flags, rc, properties): # Giữ nguyên logic này
        self.connecting = False # Đã xong quá trình thử kết nối
        if rc == 0:
            self.connected = True
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) MQTT connected with token. Subscribing...")
            client.subscribe(self.push_biometric_topic, qos=1)
            client.subscribe(self.command_topic, qos=1)
            
            if not self.device_info_sent_this_session:
                self.send_device_info() 
                self.device_info_sent_this_session = True

            if self.on_connection_status_change:
                self.on_connection_status_change(True)
            self.flush_outbox() # Gửi tin nhắn chờ
        else:
            self.connected = False
            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) MQTT connection with token failed. RC: {rc}")
            if rc == 5: # Auth error
                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) MQTT Auth failed (RC=5). Token might be invalid/expired.")
                self.token = None # Xóa token
                self.username = None
                self.device_info_sent_this_session = False
                if self.on_token_received: 
                    self.on_token_received(None, None) 
            if self.on_connection_status_change:
                self.on_connection_status_change(False)

    def send_device_info(self): 
        if self._client and self.connected: 
            room_name = self.mqtt_config.get("room", "N/A")
            device_version = "1.0.0" 

            info_payload = {
                "MacAddress": self.mac,
                "Version": device_version,
                "Room": room_name,
                "ReportTime": datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
            }
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Sending Device Info: {info_payload} to {MQTT_DEVICE_INFO_TOPIC}")
            self._publish_or_queue(MQTT_DEVICE_INFO_TOPIC, info_payload, qos=1, user_properties=[("MacAddress", self.mac)])
        elif self.debug:
            print(f"[MQTT WARN] (MAC: {self.mac}) Cannot send Device Info: Client not connected.")

    def send_healthcheck(self): 
        if self._client and self.connected:
            device_time_gmt7 = datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
            room_name = self.mqtt_config.get("room", "N/A")
            device_version = "1.0.0"
            
            bio_auth_support = {
                "IsFace": True, 
                "IsFinger": bool(self.fingerprint_sensor and PyFingerprint),
                "IsIdCard": bool(self.rfid_sensor), 
                "IsIris": False
            }
            heartbeat = {
                "MacAddress": self.mac, 
                "DeviceTime": device_time_gmt7, 
                "Version": device_version, 
                "Room": room_name, 
                "BioAuthType": bio_auth_support, 
                "Direction": "IN" 
            }
            self._publish_or_queue(MQTT_HEALTHCHECK_TOPIC, heartbeat, qos=0) 


    def send_recognition_event(self, bio_id, id_number, auth_method, auth_data, status, face_image_b64=None, finger_image_b64=None, iris_image_b64 = '', abnormal = False, direction = "IN"):
        device_time_gmt7 = datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
        
        person_name_to_send = "Unknown" 
        id_number_to_send = id_number 

        if bio_id:
            user_details_row = database.get_user_info_by_bio_id(bio_id) 
            if user_details_row:
                
                if 'person_name' in user_details_row.keys() and user_details_row['person_name']:
                    person_name_to_send = user_details_row['person_name']
                
                if not id_number_to_send and 'id_number' in user_details_row.keys() and user_details_row['id_number']: # Chỉ lấy từ DB nếu chưa có
                    id_number_to_send = user_details_row['id_number']
                
                if auth_method.upper() == "FACE" and status.upper() == "SUCCESS" and not face_image_b64:
                    if 'face_image' in user_details_row.keys():
                        face_image_b64 = user_details_row['face_image']
                
                if auth_method.upper() == "FINGER" and status.upper() == "SUCCESS" and not finger_image_b64:
                    if 'finger_image' in user_details_row.keys():
                         finger_image_b64 = user_details_row['finger_image']

        payload_dict = {
            "MacAddress": self.mac, 
            "BioId": bio_id, 
            "IdNumber" : id_number_to_send, 
            "AccessTime": device_time_gmt7, 
            "Direction" : direction, 
            "FaceImg"   : face_image_b64,
            "FingerImg" : finger_image_b64,
            "IrisImg"   : iris_image_b64,
            "Abnormal": abnormal
        }
        
        if auth_method.upper() == "FACE" and status.upper() == "SUCCESS" and face_image_b64:
            payload_dict["FaceImg"] = face_image_b64
        if auth_method.upper() == "FINGER" and status.upper() == "SUCCESS" and finger_image_b64:
            payload_dict["FingerImg"] = finger_image_b64
        if auth_method.upper() == "IRIS" and status.upper() == "SUCCESS" and iris_image_b64: 
            payload_dict["IrisImg"] = iris_image_b64
        
        if self.debug:
            log_payload = {k: (v[:30]+'...' if isinstance(v, str) and k in ["FaceImage", "FingerImage", "IrisImage"] and v and len(v)>30 else v) for k,v in payload_dict.items()}
            print(f"[MQTT DEBUG] (MAC: {self.mac}) Queuing/Publishing Recognition Event to {MQTT_ACCESS_CONTROL}: {log_payload}")
        
        self._publish_or_queue(MQTT_ACCESS_CONTROL, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])


    def send_device_sync_request(self): 
        if self._client and self.connected:
            payload_dict = {"MacAddress": self.mac, "Token" : self.token} 
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Sending Device Sync Request: {payload_dict}")
            self._publish_or_queue(MQTT_SYNC_REQUEST_TOPIC, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])
        elif self.debug:
            print(f"[MQTT WARN] (MAC: {self.mac}) Cannot send Device Sync Request: MQTT not connected.")


    def send_biometric_ack(self, bio_id): # Giữ nguyên payload gốc
        payload_dict = {"bioId": bio_id, "macAddress": self.mac}
        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Sending Biometric ACK for bioId {bio_id}: {payload_dict}")
        self._publish_or_queue(MQTT_BIO_ACK_TOPIC, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])


    def send_sos_alert(self): # Giữ nguyên payload gốc
        device_time_gmt7 = datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
        payload_dict = {
            "MacAddress": self.mac, 
            # "Token": self.token, # BỎ TOKEN KHỎI PAYLOAD
            "DeviceTime": device_time_gmt7, 
            "AlertType": "SOS"
        }
        self._publish_or_queue(MQTT_SOS_ALERT_TOPIC, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])
        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) SOS alert queued/published to {MQTT_SOS_ALERT_TOPIC}.")


    def _publish_or_queue(self, topic, payload_dict, qos=0, user_properties=None): # Giữ nguyên
        payload_str = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False)
        
        mqtt_props = None
        if user_properties:
            mqtt_props = Properties(PacketTypes.PUBLISH)
            mqtt_props.UserProperty = user_properties
        
        if self.connected and self._client:
            try:
                publish_info = self._client.publish(topic, payload=payload_str, qos=qos, properties=mqtt_props)
                if publish_info.rc != mqtt.MQTT_ERR_SUCCESS:
                    if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) MQTT publish to {topic} failed (code {publish_info.rc}). Queuing message.")
                    enqueue_outgoing_message(topic, payload_str, qos, json.dumps(user_properties) if user_properties else None)
            except Exception as e_pub:
                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exception during MQTT publish to {topic}: {e_pub}. Queuing message.")
                enqueue_outgoing_message(topic, payload_str, qos, json.dumps(user_properties) if user_properties else None)
        else:
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) MQTT not connected. Queuing message for topic {topic}.")
            enqueue_outgoing_message(topic, payload_str, qos, json.dumps(user_properties) if user_properties else None)

    def flush_outbox(self): # Giữ nguyên
        if not self.connected or not self._client:
            # if self.debug and get_pending_outbox(limit=1): 
            #     print(f"[MQTT DEBUG] (MAC: {self.mac}) Cannot flush outbox: MQTT not connected.")
            return
            
        # if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Attempting to flush outbox...")
        pending_messages = get_pending_outbox() 
        
        if not pending_messages:
            # if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Outbox is empty.")
            return
        
        if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Found {len(pending_messages)} messages in outbox. Flushing...")
        for entry_id, topic, payload_str, qos, user_props_json_str in pending_messages:
            mqtt_props_for_publish = None
            if user_props_json_str: 
                try:
                    user_props_list_of_tuples = json.loads(user_props_json_str) 
                    if user_props_list_of_tuples and isinstance(user_props_list_of_tuples, list):
                        mqtt_props_for_publish = Properties(PacketTypes.PUBLISH)
                        mqtt_props_for_publish.UserProperty = user_props_list_of_tuples
                except json.JSONDecodeError:
                     if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Failed to decode UserProperties JSON for outbox message ID {entry_id}: '{user_props_json_str}'")
            try:
                if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Attempting to publish message from outbox: ID {entry_id}, Topic {topic}")
                publish_info = self._client.publish(topic, payload=payload_str, qos=qos, properties=mqtt_props_for_publish)
                if publish_info.rc == mqtt.MQTT_ERR_SUCCESS: 
                    mark_outbox_sent(entry_id) 
                    if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Outbox message ID {entry_id} successfully sent and marked.")
                else: 
                    if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Failed to publish outbox message ID {entry_id} (MQTT Error Code: {publish_info.rc}). Stopping flush to maintain order.")
                    break 
            except Exception as e_flush_pub: 
                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exception while publishing outbox message ID {entry_id}: {e_flush_pub}. Stopping flush.")
                break 
        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Outbox flush process finished.")