# mqtt.py
import json
import time
import ssl
import socket
import hashlib
import base64
from datetime import datetime, timezone
import numpy as np
import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes

try:
    from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1, FINGERPRINT_CHARBUFFER2
except ImportError:
    print("[ERROR] PyFingerprint library not found. Fingerprint functionality disabled.")
    PyFingerprint = None
except Exception as e:
    print(f"[ERROR] Failed to import PyFingerprint: {e}. Fingerprint functionality disabled.")
    PyFingerprint = None

try:
    from insightface.app import FaceAnalysis
    import cv2
    face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
    face_app.prepare(ctx_id=0)
    print("[MQTT] InsightFace model initialized.")
except Exception as e:
    print(f"[MQTT WARN] Failed to initialize InsightFace model (may not be needed): {e}")
    face_app = None

import database

MQTT_REGISTER_TOPIC = "iot/devices/register_device"
MQTT_REGISTER_RESPONSE_TOPIC = "iot/server/register_device_resp"
MQTT_HEALTHCHECK_TOPIC = "iot/devices/healthcheck"
MQTT_RECOGNITION_FACE_TOPIC = "iot/devices/recognition_face"
MQTT_SYNC_REQUEST_TOPIC = "iot/devices/request_sync"
MQTT_BIO_ACK_TOPIC = "iot/devices/device_received_bio"

MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE = "iot/server/{mac_address}/push_biometric"


def generate_hashed_password(mac):
    data = (mac + "navis@salt").encode("utf-8")
    hash_bytes = hashlib.sha256(data).digest()
    return base64.b64encode(hash_bytes).decode("utf-8")

def is_connected():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError: return False
    except Exception as e:
        print(f"[WARN] Error checking internet connection: {e}")
        return False

class MQTTManager:
    def __init__(self, mqtt_config, mac, fingerprint_sensor=None, debug=False):
        self.mqtt_config = mqtt_config
        self.mac = mac
        self.token = None
        self._client = None
        self.connected = False
        self.connecting = False
        self.debug = debug
        self.on_token_received = None
        self.on_connection_status_change = None
        self.push_biometric_topic = MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE.format(mac_address=self.mac)
        self.fingerprint_sensor = fingerprint_sensor

    @property
    def client(self):
        return self._client

    def set_fingerprint_sensor(self, sensor):
        self.fingerprint_sensor = sensor
        if self.debug:
            print(f"[DEBUG] Fingerprint sensor object {'set' if sensor else 'unset'} in MQTTManager.")

    def disconnect_client(self):
        if self._client is not None:
            if self.debug: print("[DEBUG] Disconnecting MQTT client...")
            try:
                if self.connected:
                    try:
                        self._client.unsubscribe(MQTT_REGISTER_RESPONSE_TOPIC)
                        self._client.unsubscribe(self.push_biometric_topic)
                        print(f"[INFO] Unsubscribed from MQTT topics.")
                    except Exception as e:
                        print(f"[WARN] Error unsubscribing from topics: {e}")
                self._client.loop_stop()
                self._client.disconnect()
                if self.debug: print("[DEBUG] MQTT client disconnect requested.")
            except Exception as e:
                if self.debug: print("[DEBUG] Error during MQTT disconnect:", e)
            finally:
                 self._client = None
                 self.connected = False
                 self.connecting = False
                 if self.on_connection_status_change:
                     self.on_connection_status_change(False)

    def connect_and_register(self):
        if self.connecting or self.connected:
             if self.debug: print("[DEBUG] Connection attempt already in progress or connected.")
             return False
        if not is_connected():
             if self.debug: print("[DEBUG] No internet connection. Skipping MQTT registration.")
             return False
        try:
             self.disconnect_client()
             self.connecting = True
             self.connected = False
             self._client = mqtt.Client(client_id=self.mac, protocol=mqtt.MQTTv5)
             self._client.on_connect = self.on_connect
             self._client.on_disconnect = self.on_disconnect
             self._client.on_message = self.on_message
             self._client.on_subscribe = self.on_subscribe
             self._client.on_publish = self.on_publish
             username = self.mqtt_config.get("mqtt_username", "")
             password = self.mqtt_config.get("mqtt_password", "")
             if not username or not password:
                 print("[ERROR] MQTT username or password missing in config for registration.")
                 self.connecting = False
                 return False
             self._client.username_pw_set(username, password)
             if self.mqtt_config.get("port") == 8883:
                 try:
                     self._client.tls_set(cert_reqs=ssl.CERT_NONE)
                     self._client.tls_insecure_set(True)
                 except Exception as e:
                     print(f"[ERROR] Failed to set TLS config: {e}")
                     self.connecting = False
                     return False
             broker_address = self.mqtt_config.get("broker", "")
             broker_port = self.mqtt_config.get("port", 1883)
             if not broker_address:
                 print("[ERROR] MQTT broker address missing in config.")
                 self.connecting = False
                 return False
             self._client.connect_async(broker_address, broker_port, keepalive=60)
             self._client.loop_start()
             if self.debug: print(f"[DEBUG] Connecting to MQTT broker {broker_address}:{broker_port} for registration...")
             return True
        except Exception as e:
             if self.debug: print("[DEBUG] MQTT connection setup error:", e)
             self.connecting = False
             self._client = None
             return False


    def on_connect(self, client, userdata, flags, rc, properties):
         self.connecting = False
         if rc == 0:
             if self.debug: print("[DEBUG] MQTT connection established for registration (rc=0).")
             client.subscribe(MQTT_REGISTER_RESPONSE_TOPIC, qos=1)
             if self.token is None:
                 props = Properties(PacketTypes.PUBLISH)
                 props.UserProperty = [("MacAddress", self.mac)]
                 payload = json.dumps({"MacAddress": self.mac, "HashedPassword": generate_hashed_password(self.mac)}, separators=(",", ":"))
                 if self.debug: print("[DEBUG] Publishing registration request:", payload)
                 client.publish(MQTT_REGISTER_TOPIC, payload=payload, properties=props, qos=1)
             elif self.token:
                 print("[WARN] Connected with initial creds but already have a token. Attempting reconnect with token.")
                 if self.on_token_received:
                     self.on_token_received(self.token)
                 else:
                     self.connect_with_token()
         else:
             self.connected = False
             if self.debug: print(f"[DEBUG] MQTT registration connection failed. Return code: {rc}")
             if self.on_connection_status_change: self.on_connection_status_change(False)


    def on_disconnect(self, client, userdata, rc, properties):
        self.connected = False
        self.connecting = False
        if self.debug: print(f"[DEBUG] MQTT disconnected. Reason code: {rc}")
        if self.on_connection_status_change: self.on_connection_status_change(False)


    def on_subscribe(self, client, userdata, mid, granted_qos, properties=None):
        if self.debug: print(f"[DEBUG] Subscribed: mid={mid}, QoS={granted_qos}")


    def on_publish(self, client, userdata, mid, *args, **kwargs):
        pass


    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            if self.debug: print(f"[DEBUG] Received message on topic '{topic}': {payload[:200]}...")

            if topic == MQTT_REGISTER_RESPONSE_TOPIC:
                try:
                    data = json.loads(payload)
                    if data.get("MacAddress", "").lower() != self.mac.lower():
                        if self.debug: print("[DEBUG] MAC address mismatch in registration response. Ignoring.")
                        return
                    token = data.get("AccessToken")
                    if token:
                        self.token = token
                        if self.debug: print(f"[DEBUG] Registration successful, received token: {token[:10]}...")
                        if self.on_token_received:
                             self.on_token_received(token)
                             print("[INFO] Token received. Signaled main application to reconnect.")
                        else:
                             print("[WARN] on_token_received callback not set. Attempting direct reconnect.")
                             self.connect_with_token()
                    else:
                        if self.debug: print("[DEBUG] Registration response received, but no AccessToken found.")
                except json.JSONDecodeError:
                    if self.debug: print("[DEBUG] Failed to decode JSON from registration response.")
                except Exception as e:
                    if self.debug: print(f"[DEBUG] Error processing registration response: {e}")

            elif topic == self.push_biometric_topic:
                 if not self.connected or not self.token:
                     print("[WARN] Received biometric push but not connected with token. Ignoring.")
                     return
                 try:
                     command_list = json.loads(payload)
                     if not isinstance(command_list, list):
                         print("[WARN] Invalid biometric push payload: Expected a JSON list.")
                         return

                     sync_all_processed_delete = False

                     for command_item in command_list:
                         if not isinstance(command_item, dict):
                             print("[WARN] Skipping invalid item in biometric push list: Not a dictionary.")
                             continue

                         cmd_type = command_item.get("cmdType")
                         bio_id = command_item.get("bioId")

                         processed_ok = False
                         finger_position_for_db = None

                         if cmd_type == "SYNC_ALL":
                             if not sync_all_processed_delete:
                                 print("[INFO] Processing SYNC_ALL command: Clearing sensor and DB.")
                                 sensor_cleared = False
                                 if self.fingerprint_sensor and PyFingerprint is not None:
                                     try:
                                         if self.fingerprint_sensor.verifyPassword():
                                             if self.fingerprint_sensor.clearDatabase():
                                                 print("[INFO] Fingerprint sensor database cleared.")
                                                 sensor_cleared = True
                                             else:
                                                 print("[ERROR] Failed to clear fingerprint sensor database.")
                                         else:
                                              print("[ERROR] Fingerprint sensor password verification failed during SYNC_ALL.")
                                     except Exception as e:
                                         print(f"[ERROR] Exception clearing fingerprint sensor: {e}")
                                 else:
                                     print("[WARN] Fingerprint sensor object not available for SYNC_ALL clear.")

                                 db_cleared = database.delete_all_embeddings_for_mac(self.mac)
                                 processed_ok = db_cleared and (sensor_cleared if self.fingerprint_sensor else True)
                                 sync_all_processed_delete = True

                             if 'bioDatas' in command_item and bio_id:
                                 print(f"[INFO] Processing PUSH_NEW data included with SYNC_ALL for bioId: {bio_id}")
                                 cmd_type = "PUSH_NEW_BIO"
                             else:
                                 if not bio_id:
                                     print("[INFO] SYNC_ALL processed (clear only). No specific bioId for ACK.")
                                     continue

                         if cmd_type in ["PUSH_NEW_BIO", "PUSH_UPDATE_BIO"]:
                             if not bio_id:
                                 print(f"[WARN] Skipping {cmd_type}: Missing 'bioId'.")
                                 continue

                             print(f"[INFO] Processing {cmd_type} for bioId: {bio_id}")
                             finger_op_success = True
                             face_op_success = True # Assume success for face unless it fails

                             for bio_data in command_item.get('bioDatas', []):
                                 bio_type = bio_data.get("BioType", "").upper()
                                 template_b64 = bio_data.get("Template")

                                 if bio_type == "FINGER" and template_b64 and self.fingerprint_sensor and PyFingerprint is not None:
                                     try:
                                         padding = '=' * (-len(template_b64) % 4)
                                         template_bytes = base64.b64decode(template_b64.strip() + padding)
                                         template_list = list(template_bytes)

                                         if self.fingerprint_sensor.verifyPassword():
                                             print(f"[INFO] Attempting to store fingerprint for bioId {bio_id} using auto-position...")
                                             if self.fingerprint_sensor.uploadCharacteristics(FINGERPRINT_CHARBUFFER1, template_list):
                                                 actual_position = self.fingerprint_sensor.storeTemplate(FINGERPRINT_CHARBUFFER1)
                                                 
                                                 if actual_position >= 0:
                                                     print(f"[INFO] Fingerprint for bioId {bio_id} stored successfully by sensor at position {actual_position}.")
                                                     finger_position_for_db = actual_position
                                                 else:
                                                     print(f"[ERROR] Failed to store fingerprint template on sensor flash for bioId {bio_id}. Sensor might be full or error occurred. Return code: {actual_position}")
                                                     finger_op_success = False
                                             else:
                                                  print(f"[ERROR] Failed to upload fingerprint characteristics (list) to sensor buffer for bioId {bio_id}.")
                                                  finger_op_success = False
                                         else:
                                             print(f"[ERROR] Fingerprint sensor password verification failed for {cmd_type} (bioId {bio_id}).")
                                             finger_op_success = False
                                             break
                                     except base64.binascii.Error as e:
                                         print(f"[ERROR] Failed to decode FINGER template B64 for sensor enrollment (bioId {bio_id}): {e}")
                                         finger_op_success = False
                                     except Exception as e:
                                         print(f"[ERROR] Exception during fingerprint sensor enrollment for bioId {bio_id}: {e}")
                                         finger_op_success = False
                                 elif bio_type == "FINGER" and not self.fingerprint_sensor:
                                     print(f"[WARN] Fingerprint sensor not available. Cannot process FINGER data for bioId {bio_id}.")
                                     finger_op_success = False # Mark as failed if sensor needed but unavailable

                                 elif bio_type == "FACE":
                                     # Basic check if face data seems okay for DB storage, actual processing happens elsewhere
                                     if not template_b64:
                                         print(f"[WARN] FACE template missing for bioId {bio_id}. Skipping face DB update part.")
                                         face_op_success = False # Consider this a failure for this bio type? Or just skip? Let's just warn for now.


                             if finger_op_success and face_op_success: # Only proceed if relevant ops succeeded
                                 processed_ok = database.process_biometric_push(command_item, self.mac, finger_position=finger_position_for_db)
                             else:
                                 print(f"[WARN] Skipping database update for bioId {bio_id} due to biometric operation failure.")
                                 processed_ok = False

                         elif cmd_type == "PUSH_DELETE_BIO":
                             if not bio_id:
                                 print("[WARN] Skipping PUSH_DELETE_BIO: Missing 'bioId'.")
                                 continue

                             print(f"[INFO] Processing PUSH_DELETE_BIO for bioId: {bio_id}")
                             position_to_delete = database.get_finger_position_by_bio_id(bio_id)
                             sensor_deleted = False
                             if position_to_delete is not None and self.fingerprint_sensor and PyFingerprint is not None:
                                 try:
                                     if self.fingerprint_sensor.verifyPassword():
                                         if self.fingerprint_sensor.deleteTemplate(position_to_delete):
                                             print(f"[INFO] Deleted fingerprint from sensor position {position_to_delete} for bioId {bio_id}.")
                                             sensor_deleted = True
                                         else:
                                             print(f"[ERROR] Failed to delete fingerprint from sensor position {position_to_delete} for bioId {bio_id}.")
                                     else:
                                          print(f"[ERROR] Fingerprint sensor password verification failed during delete for bioId {bio_id}.")
                                 except Exception as e:
                                     print(f"[ERROR] Exception deleting fingerprint from sensor position {position_to_delete}: {e}")
                             elif position_to_delete is None:
                                 sensor_deleted = True
                             elif not self.fingerprint_sensor:
                                 print(f"[WARN] Fingerprint sensor not available. Cannot delete template for bioId {bio_id} from sensor.")
                                 sensor_deleted = False

                             db_deleted = database.delete_embedding_by_bio_id(bio_id)
                             processed_ok = db_deleted

                         else:
                             if cmd_type != "SYNC_ALL":
                                 print(f"[WARN] Unknown cmdType received: {cmd_type}")

                         if processed_ok and bio_id:
                             self.send_biometric_ack(bio_id)
                         elif not processed_ok and bio_id:
                             print(f"[ERROR] Failed to fully process command for bioId {bio_id}. ACK not sent.")

                 except json.JSONDecodeError:
                      if self.debug: print("[DEBUG] Failed to decode JSON from biometric push message.")
                 except Exception as e:
                      if self.debug: print(f"[DEBUG] Error processing biometric push message: {e}")

        except UnicodeDecodeError:
             if self.debug: print(f"[WARN] Could not decode message payload on topic {topic} as UTF-8.")
        except Exception as e:
             if self.debug: print(f"[ERROR] Unhandled error in on_message: {e}")


    def process_face_embedding(self, image_base64):
        if not face_app:
            print("[ERROR] InsightFace model not available for embedding processing.")
            return None
        try:
            image_data = base64.b64decode(image_base64)
            nparr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                 print("[ERROR] Failed to decode image from Base64 data.")
                 return None
            faces = face_app.get(img)
            return faces[0].embedding.astype(np.float32) if faces else None
        except base64.binascii.Error:
             print("[ERROR] Invalid Base64 data received.")
             return None
        except Exception as e:
            if self.debug: print(f"[ERROR] Error processing face embedding: {e}")
            return None

    def connect_with_token(self):
         if self.connecting or self.connected:
             if self.debug: print("[DEBUG] Token connection attempt already in progress or connected.")
             return False
         if not self.token:
             if self.debug: print("[DEBUG] Cannot connect with token: Token not available.")
             return False
         if not is_connected():
             if self.debug: print("[DEBUG] No internet connection. Skipping MQTT token connection.")
             return False
         try:
             self.disconnect_client()
             self.connecting = True
             self.connected = False
             self._client = mqtt.Client(client_id=self.mac, protocol=mqtt.MQTTv5)
             self._client.on_connect = self.on_connect_token
             self._client.on_disconnect = self.on_disconnect
             self._client.on_message = self.on_message
             self._client.on_subscribe = self.on_subscribe
             self._client.on_publish = self.on_publish
             self._client.username_pw_set(self.mac, self.token)
             if self.mqtt_config.get("port") == 8883:
                 try:
                     self._client.tls_set(cert_reqs=ssl.CERT_NONE)
                     self._client.tls_insecure_set(True)
                 except Exception as e:
                      print(f"[ERROR] Failed to set TLS config for token connection: {e}")
                      self.connecting = False
                      return False
             broker_address = self.mqtt_config.get("broker", "")
             broker_port = self.mqtt_config.get("port", 8883)
             if not broker_address:
                 print("[ERROR] MQTT broker address missing in config.")
                 self.connecting = False
                 return False
             if self.debug: print(f"[DEBUG] Reconnecting to {broker_address}:{broker_port} with token credentials...")
             self._client.connect_async(broker_address, broker_port, keepalive=60)
             self._client.loop_start()
             return True
         except Exception as e:
             if self.debug: print("[DEBUG] Error setting up connection with token:", e)
             self.connecting = False
             self._client = None
             return False


    def on_connect_token(self, client, userdata, flags, rc, properties):
        self.connecting = False
        if rc == 0:
            self.connected = True
            if self.debug: print("[DEBUG] MQTT connection with token established successfully (rc=0).")
            client.subscribe(self.push_biometric_topic, qos=1)
            if self.debug: print(f"[DEBUG] Subscribed to {self.push_biometric_topic}")

            if self.on_connection_status_change: self.on_connection_status_change(True)
        else:
            self.connected = False
            if self.debug: print(f"[DEBUG] MQTT token connection failed. Return code: {rc}")
            if rc == 5:
                 print("[ERROR] MQTT connection refused (Not Authorized). Token might be invalid or expired.")
                 self.token = None
                 if self.on_token_received: self.on_token_received(None)
            if self.on_connection_status_change: self.on_connection_status_change(False)


    def send_healthcheck(self):
        if self._client and self.token and self.connected:
            try:
                 device_time = datetime.now(timezone.utc).isoformat(timespec='seconds') + "Z"
                 heartbeat = { "MacAddress": self.mac, "Token": self.token, "DeviceTime": device_time, "Status": "OK" }
                 props = Properties(PacketTypes.PUBLISH)
                 props.UserProperty = [("MacAddress", self.mac)]
                 payload = json.dumps(heartbeat, separators=(",", ":"))
                 result, mid = self._client.publish(MQTT_HEALTHCHECK_TOPIC, payload=payload, properties=props, qos=0)
            except Exception as e: print(f"[ERROR] Exception during send_healthcheck: {e}")


    def send_recognition_success(self, bio_id, person_name=""):
        if self._client and self.connected:
            try:
                 user_id = bio_id
                 name = person_name if person_name else user_id
                 device_time = datetime.now(timezone.utc).isoformat(timespec='seconds') + "Z"
                 payload_dict = {
                     "MacAddress": self.mac,
                     "bioId": user_id,
                     "personName": name,
                     "DeviceTime": device_time,
                     "Status": "Recognized"
                 }
                 payload = json.dumps(payload_dict, separators=(",", ":"))
                 props = Properties(PacketTypes.PUBLISH)
                 props.UserProperty = [("MacAddress", self.mac)]
                 if self.debug: print("[DEBUG] Publishing recognition success:", payload)
                 result, mid = self._client.publish(MQTT_RECOGNITION_FACE_TOPIC, payload=payload, properties=props, qos=1)
                 if result != mqtt.MQTT_ERR_SUCCESS: print(f"[WARN] Failed to publish recognition success (Error code: {result})")
            except Exception as e: print(f"[ERROR] Exception during send_recognition_success: {e}")
        else:
            if self.debug: print("[DEBUG] MQTT client not ready to publish recognition success.")


    def send_device_sync(self):
        if self._client and self.connected:
            try:
                 payload = json.dumps({"MacAddress": self.mac, "Request": "SyncAllData"}, separators=(",", ":"))
                 props = Properties(PacketTypes.PUBLISH)
                 props.UserProperty = [("MacAddress", self.mac)]
                 if self.debug: print("[DEBUG] Publishing sync request:", payload)
                 result, mid = self._client.publish(MQTT_SYNC_REQUEST_TOPIC, payload=payload, properties=props, qos=1)
                 if result != mqtt.MQTT_ERR_SUCCESS: print(f"[WARN] Failed to publish sync request (Error code: {result})")
            except Exception as e: print(f"[ERROR] Exception during send_device_sync: {e}")
        else:
            if self.debug: print("[DEBUG] Cannot send sync request: Client not ready.")


    def send_biometric_ack(self, bio_id):
        if self._client and self.connected:
            try:
                 payload_dict = {"bioId": bio_id, "macAddress": self.mac, "status": "Received"}
                 payload = json.dumps(payload_dict, separators=(",", ":"))
                 props = Properties(PacketTypes.PUBLISH)
                 props.UserProperty = [("MacAddress", self.mac)]
                 if self.debug: print(f"[DEBUG] Publishing ACK for bioId {bio_id}: {payload}")
                 result, mid = self._client.publish(MQTT_BIO_ACK_TOPIC, payload=payload, properties=props, qos=1)
                 if result != mqtt.MQTT_ERR_SUCCESS: print(f"[WARN] Failed to publish biometric ACK for {bio_id} (Error code: {result})")
            except Exception as e: print(f"[ERROR] Exception during send_biometric_ack for {bio_id}: {e}")
        else:
             if self.debug: print(f"[DEBUG] MQTT client not ready to publish ACK for bioId {bio_id}.")