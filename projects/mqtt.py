import json
import time
import ssl
import requests
import socket
import hashlib
import base64
from datetime import datetime, timezone
import numpy as np
import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
from database import enqueue_outgoing_message, get_pending_outbox, mark_outbox_sent

try:
    from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1, FINGERPRINT_CHARBUFFER2
except ImportError:
    PyFingerprint = None
except Exception:
    PyFingerprint = None

try:
    from insightface.app import FaceAnalysis
    import cv2
    face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
    face_app.prepare(ctx_id=0)
except Exception:
    face_app = None

import database

MQTT_REGISTER_TOPIC = "iot/devices/register_device"
MQTT_REGISTER_RESPONSE_TOPIC = "iot/server/register_device_resp"
MQTT_HEALTHCHECK_TOPIC = "iot/devices/healthcheck"
MQTT_RECOGNITION_FACE_TOPIC = "iot/devices/recognition_face"
MQTT_SYNC_REQUEST_TOPIC = "iot/devices/device_sync_bio"
MQTT_BIO_ACK_TOPIC = "iot/devices/device_received_bio"
MQTT_SOS_ALERT_TOPIC = "iot/devices/sos_alert"
MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE = "iot/server/push_biometric/{mac_address}"


def generate_hashed_password(mac):
    data = (mac + "navis@salt").encode("utf-8")
    hash_bytes = hashlib.sha256(data).digest()
    return base64.b64encode(hash_bytes).decode("utf-8")


def is_connected():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError:
        return False
    except Exception:
        return False


class MQTTManager:
    def __init__(self, mqtt_config, mac, fingerprint_sensor=None, debug=True):
        self.mqtt_config = mqtt_config
        self.mac = mac
        self.username = mqtt_config.get("mqtt_username")
        self.token = mqtt_config.get("token")
        self._client = None
        self.connected = False
        self.connecting = False
        self.debug = debug
        self.on_token_received = None
        self.on_connection_status_change = None
        self.push_biometric_topic = MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE.format(mac_address=self.mac)
        self.fingerprint_sensor = fingerprint_sensor
        if self.debug:
            if self.token:
                print(f"[MQTT DEBUG] MQTTManager initialized with existing token: {self.token[:10]}...")
            else:
                print("[MQTT DEBUG] MQTTManager initialized without an existing token.")

    @property
    def client(self):
        return self._client

    def set_fingerprint_sensor(self, sensor):
        self.fingerprint_sensor = sensor
        if self.debug:
            print(f"[MQTT DEBUG] Fingerprint sensor object {'set' if sensor else 'unset'} in MQTTManager.")

    def disconnect_client(self):
        if self._client is not None:
            if self.debug: print("[MQTT DEBUG] Disconnecting MQTT client...")
            try:
                if self.connected:
                    try:
                        if MQTT_REGISTER_RESPONSE_TOPIC: # Check if topic is defined
                            self._client.unsubscribe(MQTT_REGISTER_RESPONSE_TOPIC)
                        if self.push_biometric_topic: # Check if topic is defined
                            self._client.unsubscribe(self.push_biometric_topic)
                        if self.debug: print(f"[MQTT INFO] Unsubscribed from MQTT topics.")
                    except Exception as e:
                        if self.debug: print(f"[MQTT WARN] Error unsubscribing from topics: {e}")
                self._client.loop_stop(force=False)
                self._client.disconnect()
                if self.debug: print("[MQTT DEBUG] MQTT client disconnect requested.")
            except Exception as e:
                if self.debug: print("[MQTT DEBUG] Error during MQTT disconnect:", e)
            finally:
                self._client = None
                self.connected = False
                self.connecting = False
                if self.on_connection_status_change:
                    self.on_connection_status_change(False)

    def connect_and_register(self):
        if self.token and self.username:
            if self.debug: print("[MQTT DEBUG] Attempting to connect with existing token first.")
            if self.connect_with_token():
                return True
            else:
                if self.debug: print("[MQTT WARN] Connection with existing token failed. Will proceed to HTTP token retrieval if necessary.")

        if not self.retrieve_token_via_http():
            if self.debug: print("[MQTT ERROR] Cannot retrieve MQTT token via HTTP.")
            return False
        return self.connect_with_token()

    def retrieve_token_via_http(self) -> bool:
        domain = self.mqtt_config.get('domain')
        http_port_str = self.mqtt_config.get('http_port', '8080')
        try:
            http_port = int(http_port_str)
        except ValueError:
            if self.debug: print(f"[MQTT ERROR] Invalid http_port in config: {http_port_str}. Using default 8080.")
            http_port = 8080

        if domain:
            base = domain.rstrip('/')
            if not base.startswith(('http://', 'https://')):
                base = f"http://{base}" # Default to http if scheme missing
        else:
            broker = self.mqtt_config.get('broker')
            if not broker:
                if self.debug: print("[MQTT ERROR] Neither 'domain' nor 'broker' configured for token HTTP request.")
                return False
            base = f"http://{broker}:{http_port}"

        url = f"{base}/api/devicecomm/getmqtttoken"
        payload = {"macAddress": self.mac, "password": generate_hashed_password(self.mac)}

        try:
            if self.debug: print(f"[MQTT DEBUG] Requesting token from {url} with payload: {payload}")
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            if self.debug: print(f"[MQTT ERROR] HTTP token request failed with status {e.response.status_code if e.response else 'N/A'}: {e.response.text if e.response else e}")
            return False
        except requests.exceptions.RequestException as e:
            if self.debug: print(f"[MQTT ERROR] HTTP token request failed (network/timeout): {e}")
            return False
        except Exception as e:
            if self.debug: print(f"[MQTT ERROR] HTTP token request failed (other): {e}")
            return False

        if data.get("code") != "OK" or "data" not in data:
            if self.debug: print(f"[MQTT ERROR] Unexpected response from token API: {data}")
            return False

        new_token = data["data"].get("token")
        new_username = data["data"].get("username")
        if not new_token or not new_username:
            if self.debug: print(f"[MQTT ERROR] Missing token/username in API response: {data}")
            return False

        if self.on_token_received:
            self.on_token_received(new_username, new_token)
        else:
            if self.debug: print("[MQTT WARN] on_token_received callback not set. Token may not be saved persistently.")
            self.token = new_token
            self.username = new_username

        if self.debug:
            print(f"[MQTT DEBUG] Retrieved token via HTTP. username={new_username}, token={new_token[:10]}...")
        return True

    def on_connect(self, client, userdata, flags, rc, properties): # For initial registration if no token
        self.connecting = False
        if rc == 0:
            if self.debug: print("[MQTT DEBUG] MQTT connection established for registration (rc=0).")
            client.subscribe(MQTT_REGISTER_RESPONSE_TOPIC, qos=1)
            # This path is less likely now, as we try HTTP first if token is missing
            if self.token is None: # Should ideally not happen if retrieve_token_via_http was called
                props = Properties(PacketTypes.PUBLISH)
                props.UserProperty = [("MacAddress", self.mac)]
                payload_dict = {"MacAddress": self.mac, "HashedPassword": generate_hashed_password(self.mac)}
                payload_str = json.dumps(payload_dict, separators=(",", ":"))
                if self.debug: print("[MQTT DEBUG] Publishing registration request (via on_connect):", payload_str)
                client.publish(MQTT_REGISTER_TOPIC, payload=payload_str, properties=props, qos=1)
            # If somehow connected here but already have a token (e.g. from config), prefer to use it
            elif self.token and self.on_token_received:
                 self.on_token_received(self.username, self.token) # Signal app to use this token
        else:
            self.connected = False
            if self.debug: print(f"[MQTT DEBUG] MQTT registration connection failed. Return code: {rc}")
            if self.on_connection_status_change: self.on_connection_status_change(False)


    def on_disconnect(self, client, userdata, rc, properties=None): # properties added for MQTTv5
        self.connected = False
        self.connecting = False
        if self.debug: print(f"[MQTT DEBUG] MQTT disconnected. Reason code: {rc}")
        if self.on_connection_status_change:
            self.on_connection_status_change(False)

    def on_subscribe(self, client, userdata, mid, granted_qos, properties=None):
        if self.debug: print(f"[MQTT DEBUG] Subscribed: mid={mid}, QoS={granted_qos}")

    def on_publish(self, client, userdata, mid, *args, **kwargs):
        pass

    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload_str = msg.payload.decode('utf-8')
            if self.debug: print(f"[MQTT DEBUG] Received message on topic '{topic}': {payload_str[:200]}...")

            if topic == MQTT_REGISTER_RESPONSE_TOPIC:
                try:
                    data = json.loads(payload_str)
                    if data.get("MacAddress", "").lower() != self.mac.lower():
                        if self.debug: print("[MQTT DEBUG] MAC address mismatch in registration response. Ignoring.")
                        return

                    new_token = data.get("AccessToken")
                    new_username_from_resp = data.get("Username") # Assuming server might send username too

                    if new_token:
                        # Use username from response if available, otherwise keep current one (from HTTP or previous)
                        final_username = new_username_from_resp if new_username_from_resp else self.username
                        if not final_username: # If no username at all, this is an issue
                             if self.debug: print("[MQTT ERROR] No username available for the new token from MQTT_REGISTER_RESPONSE.")
                             return

                        if self.debug: print(f"[MQTT DEBUG] Registration successful via MQTT, received token: {new_token[:10]} for user {final_username}")
                        if self.on_token_received:
                            self.on_token_received(final_username, new_token)
                        else:
                            if self.debug: print("[MQTT WARN] on_token_received callback not set. Token may not be saved.")
                            self.token = new_token
                            self.username = final_username
                            self.connect_with_token()
                    else:
                        if self.debug: print("[MQTT DEBUG] Registration response received, but no AccessToken found.")
                except json.JSONDecodeError:
                    if self.debug: print("[MQTT DEBUG] Failed to decode JSON from registration response.")
                except Exception as e:
                    if self.debug: print(f"[MQTT DEBUG] Error processing registration response: {e}")

            elif topic == self.push_biometric_topic:
                if not self.connected or not self.token:
                    if self.debug: print("[MQTT WARN] Received biometric push but not connected with token. Ignoring.")
                    return
                try:
                    command_list = json.loads(payload_str)
                    if not isinstance(command_list, list):
                        if self.debug: print("[MQTT WARN] Invalid biometric push payload: Expected a JSON list.")
                        return

                    sync_all_processed_delete = False

                    for command_item in command_list:
                        if not isinstance(command_item, dict):
                            if self.debug: print("[MQTT WARN] Skipping invalid item in biometric push list: Not a dictionary.")
                            continue

                        cmd_type = command_item.get("cmdType")
                        bio_id = command_item.get("bioId")
                        processed_ok = False
                        finger_position_for_db = None

                        if cmd_type == "SYNC_ALL":
                            if not sync_all_processed_delete:
                                if self.debug: print("[MQTT INFO] Processing SYNC_ALL command: Clearing sensor and DB.")
                                sensor_cleared = False
                                if self.fingerprint_sensor and PyFingerprint is not None:
                                    try:
                                        if self.fingerprint_sensor.verifyPassword():
                                            if self.fingerprint_sensor.clearDatabase():
                                                if self.debug: print("[MQTT INFO] Fingerprint sensor database cleared.")
                                                sensor_cleared = True
                                            else:
                                                if self.debug: print("[MQTT ERROR] Failed to clear fingerprint sensor database.")
                                        else:
                                            if self.debug: print("[MQTT ERROR] Fingerprint sensor password verification failed during SYNC_ALL.")
                                    except Exception as e:
                                        if self.debug: print(f"[MQTT ERROR] Exception clearing fingerprint sensor: {e}")
                                else:
                                    if self.debug: print("[MQTT WARN] Fingerprint sensor object not available for SYNC_ALL clear.")
                                db_cleared = database.delete_all_embeddings_for_mac(self.mac)
                                processed_ok = db_cleared and (sensor_cleared if self.fingerprint_sensor and PyFingerprint is not None else True)
                                sync_all_processed_delete = True
                            if 'bioDatas' in command_item and bio_id:
                                if self.debug: print(f"[MQTT INFO] Processing PUSH_NEW data included with SYNC_ALL for bioId: {bio_id}")
                                cmd_type = "PUSH_NEW_BIO"
                            else:
                                if not bio_id and processed_ok: # SYNC_ALL clear was successful
                                     if self.debug: print("[MQTT INFO] SYNC_ALL (clear only) processed successfully.")
                                # No specific bioId for ACK for clear-only SYNC_ALL, or handled by subsequent PUSH_NEW
                                continue

                        if cmd_type in ["PUSH_NEW_BIO", "PUSH_UPDATE_BIO"]:
                            if not bio_id:
                                if self.debug: print(f"[MQTT WARN] Skipping {cmd_type}: Missing 'bioId'.")
                                continue
                            if self.debug: print(f"[MQTT INFO] Processing {cmd_type} for bioId: {bio_id}")
                            finger_op_success = True
                            face_op_success = True # Assume success unless face processing fails
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
                                                        if self.debug: print(f"[MQTT INFO] Fingerprint for bioId {bio_id} stored at sensor position {actual_position}.")
                                                        finger_position_for_db = actual_position
                                                    else:
                                                        if self.debug: print(f"[MQTT ERROR] Sensor storeTemplate error for bioId {bio_id}: {actual_position}")
                                                        finger_op_success = False
                                                else:
                                                    if self.debug: print(f"[MQTT ERROR] Failed to upload fingerprint characteristics for bioId {bio_id}.")
                                                    finger_op_success = False
                                            else:
                                                if self.debug: print(f"[MQTT ERROR] Sensor password verification failed for bioId {bio_id}.")
                                                finger_op_success = False; break
                                        except Exception as e:
                                            if self.debug: print(f"[MQTT ERROR] Exception enrolling fingerprint for bioId {bio_id}: {e}")
                                            finger_op_success = False
                                    else: # Sensor not available
                                        if self.debug: print(f"[MQTT WARN] Fingerprint sensor not available for bioId {bio_id}. Skipping FINGER data.")
                                        # finger_op_success can remain true if no finger data was expected or if only face is present
                                elif bio_data_type == "FACE":
                                     if not template_b64:
                                          if self.debug: print(f"[MQTT WARN] FACE template missing for bioId {bio_id}.")
                                          # face_op_success = False # Only if face is mandatory
                            if finger_op_success and face_op_success:
                                processed_ok = database.process_biometric_push(command_item, self.mac, finger_position=finger_position_for_db)
                            else:
                                if self.debug: print(f"[MQTT WARN] Skipping DB update for bioId {bio_id} due to FINGER/FACE op failure.")
                                processed_ok = False

                        elif cmd_type == "PUSH_DELETE_BIO":
                            if not bio_id:
                                if self.debug: print("[MQTT WARN] Skipping PUSH_DELETE_BIO: Missing 'bioId'.")
                                continue
                            if self.debug: print(f"[MQTT INFO] Processing PUSH_DELETE_BIO for bioId: {bio_id}")
                            position_to_delete = database.get_finger_position_by_bio_id(bio_id)
                            if position_to_delete is not None and self.fingerprint_sensor and PyFingerprint is not None:
                                try:
                                    if self.fingerprint_sensor.verifyPassword():
                                        if self.fingerprint_sensor.deleteTemplate(position_to_delete):
                                            if self.debug: print(f"[MQTT INFO] Deleted fingerprint from sensor position {position_to_delete} for bioId {bio_id}.")
                                        else:
                                            if self.debug: print(f"[MQTT ERROR] Failed to delete fingerprint from sensor for bioId {bio_id} at pos {position_to_delete}.")
                                    else:
                                        if self.debug: print(f"[MQTT ERROR] Sensor password verify failed for PUSH_DELETE_BIO of bioId {bio_id}.")
                                except Exception as e:
                                    if self.debug: print(f"[MQTT ERROR] Exception deleting fingerprint from sensor for bioId {bio_id}: {e}")
                            processed_ok = database.delete_embedding_by_bio_id(bio_id)
                        else:
                            if cmd_type != "SYNC_ALL": # SYNC_ALL without bioId might not need ACK here
                                if self.debug: print(f"[MQTT WARN] Unknown cmdType: {cmd_type} for bioId: {bio_id}")

                        if processed_ok and bio_id: # Only send ACK if there's a bio_id and it was processed
                            self.send_biometric_ack(bio_id)
                        elif not processed_ok and bio_id:
                            if self.debug: print(f"[MQTT ERROR] Failed processing {cmd_type} for {bio_id}. No ACK sent.")

                except json.JSONDecodeError:
                    if self.debug: print("[MQTT DEBUG] JSON decode error in biometric push")
                except Exception as e:
                    if self.debug: print(f"[MQTT DEBUG] Error in on_message (biometric push processing): {e}")

        except Exception as e:
            if self.debug: print(f"[MQTT ERROR] Unhandled error in on_message: {e}")

    def process_face_embedding(self, image_base64):
        if not face_app:
            return None
        try:
            image_data = base64.b64decode(image_base64)
            nparr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None: return None
            faces = face_app.get(img)
            return faces[0].embedding.astype(np.float32) if faces else None
        except Exception:
            return None

    def connect_with_token(self):
        if self.connecting:
            if self.debug: print("[MQTT DEBUG] connect_with_token: Already attempting to connect.")
            return True # Indicate an attempt is in progress
        if self.connected:
             if self.debug: print("[MQTT DEBUG] connect_with_token: Already connected.")
             return True

        if not self.token or not self.username:
            if self.debug: print("[MQTT DEBUG] connect_with_token: Token or username missing.")
            return False
        if not is_connected():
            if self.debug: print("[MQTT WARN] No internet connection, cannot connect MQTT.")
            return False

        self.disconnect_client()
        self.connecting = True
        self.connected = False

        if self.debug: print(f"[MQTT DEBUG] Attempting MQTT connect with user: {self.username}, token: {self.token[:10]}...")
        try:
            self._client = mqtt.Client(client_id=self.mac, protocol=mqtt.MQTTv5)
            self._client.on_connect = self.on_connect_token
            self._client.on_disconnect = self.on_disconnect
            self._client.on_message = self.on_message
            self._client.on_subscribe = self.on_subscribe
            # self._client.on_publish = self.on_publish # Can be omitted if not used

            self._client.username_pw_set(self.username, self.token)
            if self.mqtt_config.get("port") == 8883:
                self._client.tls_set(cert_reqs=ssl.CERT_NONE)
                self._client.tls_insecure_set(True)

            broker_address = self.mqtt_config.get("broker", "")
            broker_port = self.mqtt_config.get("port", 1883)
            if not broker_address:
                if self.debug: print("[MQTT ERROR] MQTT Broker address not configured.")
                self.connecting = False
                return False

            self._client.connect_async(broker_address, broker_port, keepalive=30)
            self._client.loop_start()
            return True
        except Exception as e:
            if self.debug: print(f"[MQTT ERROR] Exception during MQTT connect_with_token setup: {e}")
            self.connecting = False
            self._client = None
            return False

    def on_connect_token(self, client, userdata, flags, rc, properties):
        self.connecting = False
        if rc == 0:
            self.connected = True
            if self.debug: print(f"[MQTT DEBUG] MQTT connected with token successfully. Subscribing to {self.push_biometric_topic}")
            client.subscribe(self.push_biometric_topic, qos=1)
            if self.on_connection_status_change:
                self.on_connection_status_change(True)
            self.flush_outbox()
        else:
            self.connected = False
            if self.debug: print(f"[MQTT ERROR] MQTT connection with token failed. Return code: {rc}")
            if rc == 5:
                if self.debug: print("[MQTT WARN] MQTT Authorization failed (RC=5). Token might be invalid or expired.")
                current_token = self.token # Store current token to check if it changes
                self.token = None
                self.username = None
                if self.on_token_received:
                    self.on_token_received(None, None) # Signal app that token is invalid
            if self.on_connection_status_change:
                self.on_connection_status_change(False)

    def send_healthcheck(self):
        if self._client and self.token and self.connected:
            device_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            heartbeat = {"MacAddress": self.mac, "Token": self.token, "DeviceTime": device_time}
            props = Properties(PacketTypes.PUBLISH)
            props.UserProperty = [("MacAddress", self.mac)]
            self.client.publish(MQTT_HEALTHCHECK_TOPIC, payload=json.dumps(heartbeat, separators=(",", ":")), properties=props, qos=0)

    def send_recognition_success(self, bio_id, person_name=""):
        user_id = bio_id
        name = person_name or user_id
        device_time = datetime.now(timezone.utc).isoformat(timespec='seconds') + "Z"
        payload_dict = {"MacAddress": self.mac, "bioId": user_id, "personName": name, "DeviceTime": device_time, "Status": "Recognized"}
        self._publish_or_queue(MQTT_RECOGNITION_FACE_TOPIC, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])

    def send_device_sync(self):
        if self._client and self.connected:
            payload = json.dumps({"MacAddress": self.mac, "Request": "SyncAllData"}, separators=(",", ":"))
            props = Properties(PacketTypes.PUBLISH)
            props.UserProperty = [("MacAddress", self.mac)]
            self.client.publish(MQTT_SYNC_REQUEST_TOPIC, payload=payload, properties=props, qos=1)

    def send_biometric_ack(self, bio_id):
        payload_dict = {"bioId": bio_id, "macAddress": self.mac, "status": "Received"}
        self._publish_or_queue(MQTT_BIO_ACK_TOPIC, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])

    def send_sos_alert(self):
        if self.debug: print("[MQTT DEBUG] Preparing to send SOS alert.")
        if not self.token:
            if self.debug: print("[MQTT WARN] Cannot send SOS alert: Token is missing.")
            return
        device_time = datetime.now(timezone.utc).isoformat(timespec='seconds') + "Z"
        payload_dict = {"MacAddress": self.mac, "Token": self.token, "DeviceTime": device_time, "AlertType": "SOS_ACTIVATED"}
        self._publish_or_queue(MQTT_SOS_ALERT_TOPIC, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])
        if self.debug: print(f"[MQTT DEBUG] SOS alert queued/published to {MQTT_SOS_ALERT_TOPIC}.")

    def _publish_or_queue(self, topic, payload_dict, qos=0, user_properties=None):
        payload = json.dumps(payload_dict, separators=(",", ":"))
        props = None
        if user_properties:
            props = Properties(PacketTypes.PUBLISH)
            props.UserProperty = user_properties
        if self.connected and self._client:
            try:
                result, _ = self._client.publish(topic, payload=payload, qos=qos, properties=props)
                if result != mqtt.MQTT_ERR_SUCCESS:
                    if self.debug: print(f"[MQTT WARN] MQTT publish failed (code {result}) for topic {topic}. Queuing message.")
                    enqueue_outgoing_message(topic, payload, qos, user_properties)
            except Exception as e:
                if self.debug: print(f"[MQTT ERROR] Exception during MQTT publish for topic {topic}: {e}. Queuing message.")
                enqueue_outgoing_message(topic, payload, qos, user_properties)
        else:
            if self.debug: print(f"[MQTT DEBUG] MQTT not connected. Queuing message for topic {topic}.")
            enqueue_outgoing_message(topic, payload, qos, user_properties)

    def flush_outbox(self):
        if not self.connected or not self._client:
            return
        if self.debug: print("[MQTT DEBUG] Flushing outbox...")
        pending_messages = get_pending_outbox()
        if not pending_messages:
            return

        for entry_id, topic, payload_str, qos, props_json in pending_messages:
            props = None
            if props_json:
                try:
                    up_list = json.loads(props_json)
                    if up_list and isinstance(up_list, list):
                        props = Properties(PacketTypes.PUBLISH)
                        props.UserProperty = up_list
                except json.JSONDecodeError:
                    if self.debug: print(f"[MQTT ERROR] Failed to decode properties for outbox message ID {entry_id}")
            try:
                if self.debug: print(f"[MQTT DEBUG] Publishing from outbox: ID {entry_id}, Topic {topic}")
                result, _ = self._client.publish(topic, payload=payload_str, qos=qos, properties=props)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    mark_outbox_sent(entry_id)
                    if self.debug: print(f"[MQTT DEBUG] Outbox message ID {entry_id} sent and marked.")
                else:
                    if self.debug: print(f"[MQTT WARN] Failed to publish outbox message ID {entry_id} (code {result}). Stopping flush.")
                    break
            except Exception as e:
                if self.debug: print(f"[MQTT ERROR] Exception publishing outbox message ID {entry_id}: {e}. Stopping flush.")
                break
        if self.debug: print("[MQTT DEBUG] Outbox flush finished.")