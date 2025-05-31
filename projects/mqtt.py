# mqtt.py
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
import database

VERSION = "20250601"
try:
    from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1
except ImportError:
    PyFingerprint = None
    print("[MQTT WARN] pyfingerprint library not found. Fingerprint sensor functionality will be disabled.")
except Exception as e_fp_import:
    PyFingerprint = None
    print(f"[MQTT WARN] Error importing pyfingerprint: {e_fp_import}. Fingerprint sensor disabled.")

MQTT_DEVICE_INFO_TOPIC = "iot/devices/device_info"
MQTT_HEALTHCHECK_TOPIC = "iot/devices/healthcheck"
MQTT_ACCESS_CONTROL = "iot/devices/access"
MQTT_SYNC_REQUEST_TOPIC = "iot/devices/device_sync_bio"
MQTT_BIO_ACK_TOPIC = "iot/devices/device_received_bio"
MQTT_SOS_ALERT_TOPIC = "iot/devices/sos"
MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE = "iot/server/push_biometric/{mac_address}"
MQTT_COMMAND_TOPIC = "iot/server/command/{mac_address}"
MQTT_COMMAND_RESPONSE_TOPIC = "iot/devices/command_resp"
MQTT_PUSH_CONFIG_TOPIC_TEMPLATE = "iot/server/push_config/{mac_address}"

GMT_PLUS_7 = timezone(timedelta(hours=7))
DATETIME_FORMAT_STR = "%Y-%m-%d %H:%M:%S"

def generate_hashed_password(mac):
    data = (mac + "navis@salt").encode("utf-8")
    hash_bytes = hashlib.sha256(data).digest()
    return base64.b64encode(hash_bytes).decode("utf-8")

def is_network_available():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except OSError: return False
    except Exception: return False

class MQTTManager:
    def __init__(self, mqtt_config, mac, fingerprint_sensor=None, rfid_sensor=None, door_handler=None, debug=True):
        self.mqtt_config = mqtt_config
        self.mac = mac
        self.door = door_handler
        self.username = mqtt_config.get("mqtt_username")
        self.token = mqtt_config.get("token")
        self._client = None
        self.connected = False
        self.connecting = False
        self.debug = debug
        self.on_token_received = None
        self.on_connection_status_change = None
        self.push_biometric_topic = MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE.format(mac_address=self.mac)
        self.command_topic = MQTT_COMMAND_TOPIC.format(mac_address=self.mac)
        self.push_config_topic = MQTT_PUSH_CONFIG_TOPIC_TEMPLATE.format(mac_address=self.mac)
        self.fingerprint_sensor = fingerprint_sensor
        self.rfid_sensor = rfid_sensor
        self.device_info_sent_this_session = False
        self.explicit_disconnect_flag = False
        self.on_device_config_received = None

    @property
    def client(self): return self._client
    def is_connected(self): return self.connected
    def is_actively_connected(self):
        return self.connected and self._client and self._client.is_connected()
    def set_fingerprint_sensor(self, sensor): self.fingerprint_sensor = sensor
    def set_rfid_sensor(self, sensor): self.rfid_sensor = sensor
    def set_door_handler(self, handler): self.door = handler
    def _clear_local_credentials_mqtt(self):
        self.token = None
        self.username = None
    def disconnect_client(self, explicit=True):
        if self._client is not None:
            self.explicit_disconnect_flag = explicit
            try:
                if self._client.is_connected():
                     try:
                         self._client.unsubscribe(self.push_biometric_topic)
                         self._client.unsubscribe(self.command_topic)
                         self._client.unsubscribe(self.push_config_topic)
                     except Exception: pass
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                if self.debug: print(f"[MQTT DEBUG] Error during MQTT client disconnect: {e}")
            if explicit:
                self._client = None
                self.connected = False
                self.connecting = False
                if self.on_connection_status_change:
                    self.on_connection_status_change(False)
    def connect_and_register(self):
        if self.connecting or self.is_actively_connected():
            return self.is_actively_connected()
        self.connecting = True
        self.explicit_disconnect_flag = False
        if not is_network_available():
            if self.on_connection_status_change: self.on_connection_status_change(False)
            self.connecting = False
            return False
        self._clear_local_credentials_mqtt()
        if self.retrieve_token_via_http():
            if not self._connect_with_current_token_mqtt():
                self.connecting = False
                return False
            return True
        else:
            self.connecting = False
            return False
    def retrieve_token_via_http(self) -> bool:
        server_address_conf = self.mqtt_config.get('server')
        http_port_conf = self.mqtt_config.get('http_port')
        if not server_address_conf: return False
        http_port = 8080
        if http_port_conf is not None:
            try: http_port = int(http_port_conf)
            except ValueError: pass
        api_base_url = server_address_conf.strip().rstrip('/')
        if not api_base_url.startswith(('http://', 'https://')):
            api_base_url = f"http://{api_base_url}"
        url = f"{api_base_url}:{http_port}/api/devicecomm/getmqtttoken"
        payload = {"macAddress": self.mac, "password": generate_hashed_password(self.mac)}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            if self.debug: print(f"[MQTT ERROR][HTTP Token] Request error: {e}")
            return False
        if data.get("code") != "OK" or "data" not in data: return False
        api_data_field = data.get("data", {})
        new_token = api_data_field.get("token")
        new_username = api_data_field.get("username")
        if not new_token or not new_username: return False
        self.token = new_token
        self.username = new_username
        self.device_info_sent_this_session = False
        if self.on_token_received: self.on_token_received(new_username, new_token)
        return True
    def on_disconnect(self, client, userdata, rc, properties=None):
        reason_code = rc.value if hasattr(rc, 'value') else rc
        self.connected = False
        if client == self._client: self._client = None
        if self.on_connection_status_change: self.on_connection_status_change(False)
        if not self.explicit_disconnect_flag and not self.connecting:
            time.sleep(1)
            self.connect_and_register()
        else:
            self.explicit_disconnect_flag = False
            self.connecting = False
    def on_subscribe(self, client, userdata, mid, granted_qos, properties=None):
        if self.debug: print(f"[MQTT DEBUG] Subscribed: mid={mid}, QoS/RCs={granted_qos}")
    def on_publish(self, client, userdata, mid, *args, **kwargs):
        rc_value = mid.rc if hasattr(mid, 'rc') else (args[0] if len(args) > 0 and isinstance(args[0], int) else mqtt.MQTT_ERR_SUCCESS)
        mid_value = mid.mid if hasattr(mid, 'mid') else mid
        if rc_value != mqtt.MQTT_ERR_SUCCESS and self.debug:
             print(f"[MQTT WARN] Publish failed for MID {mid_value}, RC: {rc_value}")
    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload_str = msg.payload.decode('utf-8')
            if self.debug: print(f"[MQTT DEBUG] Received on '{topic}': {payload_str[:500]}{'...' if len(payload_str)>500 else ''}")

            if topic == self.push_biometric_topic:
                if not self.is_actively_connected(): return
                try:
                    command_data = json.loads(payload_str)
                    if not isinstance(command_data, dict): return

                    cmd_type = command_data.get("CmdType")
                    bio_id = command_data.get("BioId") 
                    door_id_for_ack = command_data.get("DoorId") 

                    processed_ok = False
                    finger_position_for_db = None

                    if cmd_type == "SYNC_ALL":
                        sensor_cleared = True
                        if self.fingerprint_sensor and PyFingerprint is not None:
                            try:
                                if self.fingerprint_sensor.verifyPassword():
                                    if not self.fingerprint_sensor.clearDatabase(): sensor_cleared = False
                                else: sensor_cleared = False
                            except Exception: sensor_cleared = False
                        
                        db_cleared = database.delete_all_biometrics_and_access_for_mac(self.mac)
                        processed_ok = db_cleared and sensor_cleared
                        
                        if bio_id is not None and (command_data.get("FaceTemps") or command_data.get("FingerTemps") or command_data.get("IrisTemps")):
                            cmd_type = "PUSH_NEW_BIO"
                        elif processed_ok:
                             # self.send_biometric_ack(door_id_for_ack, bio_id_int_for_ack_if_needed, "SYNC_ALL") # ACK for SYNC_ALL if needed
                             return

                    if cmd_type in ["PUSH_NEW_BIO", "PUSH_UPDATE_BIO"]:
                        if bio_id is None: return
                        
                        finger_op_success = True
                        finger_templates_b64 = command_data.get("FingerTemps", [])
                        if finger_templates_b64:
                            if self.fingerprint_sensor and PyFingerprint is not None:
                                first_finger_template_b64 = finger_templates_b64[0]
                                try:
                                    padding = '=' * (-len(first_finger_template_b64) % 4)
                                    template_bytes = base64.b64decode(first_finger_template_b64.strip() + padding)
                                    template_list = list(template_bytes)
                                    if self.fingerprint_sensor.verifyPassword():
                                        target_pos_on_sensor = None
                                        if cmd_type == "PUSH_UPDATE_BIO":
                                           target_pos_on_sensor = database.get_finger_position_by_bio_id_and_mac(bio_id, self.mac)
                                        
                                        if self.fingerprint_sensor.uploadCharacteristics(FINGERPRINT_CHARBUFFER1, template_list):
                                            print({finger_op_success})
                                            if target_pos_on_sensor is not None:
                                                print({finger_op_success})
                                                if self.fingerprint_sensor.storeTemplate(target_pos_on_sensor, FINGERPRINT_CHARBUFFER1):
                                                    print({finger_op_success})
                                                    finger_position_for_db = target_pos_on_sensor
                                                else: finger_op_success = False
                                            else:
                                                print({finger_op_success})
                                                actual_position = self.fingerprint_sensor.storeTemplate()
                                                if actual_position >= 0: finger_position_for_db = actual_position
                                                else: finger_op_success = False
                                        else: finger_op_success = False
                                    else: finger_op_success = False
                                except Exception: finger_op_success = False
                            else: 
                                if finger_templates_b64: finger_op_success = False
                        print({finger_op_success})
                        if finger_op_success:
                            processed_ok = database.process_biometric_push(command_data, self.mac, finger_position_from_sensor=finger_position_for_db)
                        else: processed_ok = False

                    elif cmd_type == "PUSH_DELETE_BIO":
                        if bio_id is None: return
                        position_to_delete = database.get_finger_position_by_bio_id_and_mac(bio_id, self.mac)
                        sensor_delete_successful = True
                        if position_to_delete is not None and self.fingerprint_sensor and PyFingerprint is not None:
                            try:
                                if self.fingerprint_sensor.verifyPassword():
                                    if not self.fingerprint_sensor.deleteTemplate(position_to_delete):
                                        sensor_delete_successful = False
                                else: sensor_delete_successful = False
                            except Exception: sensor_delete_successful = False
                        
                        if sensor_delete_successful:
                            processed_ok = database.delete_biometrics_and_access_for_bio_id(bio_id, self.mac)
                        else: processed_ok = False
                    else:
                        if cmd_type != "SYNC_ALL" and self.debug:
                            print(f"[MQTT WARN] Unknown CmdType: {cmd_type} for BioId {bio_id}")

                    if processed_ok and bio_id is not None:
                        self.send_biometric_ack(door_id_for_ack, bio_id, command_data.get("CmdType"))
                    elif not processed_ok and bio_id is not None and self.debug:
                        print(f"[MQTT ERROR] Failed processing {cmd_type} for BioId {bio_id}. No ACK.")
                except json.JSONDecodeError:
                    if self.debug: print(f"[MQTT ERROR] JSON decode error in biometric push processing.")
                except Exception as e_bio_proc:
                    if self.debug: print(f"[MQTT ERROR] Error processing biometric push: {e_bio_proc}")
                    import traceback; traceback.print_exc()

            elif topic == self.command_topic:
                try:
                    command = json.loads(payload_str)
                    if not isinstance(command, dict): return
                    mac_address_cmd = command.get("MacAddress")
                    cmd_id = command.get("CmdId")
                    cmd_type_cmd = command.get("CmdType")
                    cmd_time_str = command.get("CmdTime")
                    cmd_timeout = command.get("CmdTimeout", 30)
                    if mac_address_cmd != self.mac: return
                    if cmd_time_str:
                        try:
                            parsed_cmd_time_str = cmd_time_str
                            if parsed_cmd_time_str.endswith('Z'):
                                cmd_time_dt_utc = datetime.fromisoformat(parsed_cmd_time_str.replace('Z', '+00:00'))
                            else:
                                try: cmd_time_dt_utc = datetime.fromisoformat(parsed_cmd_time_str)
                                except ValueError: cmd_time_dt_utc = datetime.strptime(parsed_cmd_time_str, '%Y-%m-%d %H:%M:%S')
                            if cmd_time_dt_utc.tzinfo is None:
                                cmd_time_dt_utc = cmd_time_dt_utc.replace(tzinfo=timezone.utc)
                            elif cmd_time_dt_utc.tzinfo != timezone.utc:
                                cmd_time_dt_utc = cmd_time_dt_utc.astimezone(timezone.utc)
                            elapsed = (datetime.now(timezone.utc) - cmd_time_dt_utc).total_seconds()
                            if cmd_timeout > 0 and elapsed > cmd_timeout: return
                            if elapsed < -5: pass # Command from future, process if not timed out
                        except Exception: return
                    action_performed = False
                    if cmd_type_cmd == "REMOTE_OPEN" and self.door:
                        self.door.open_door(); action_performed = True
                    elif cmd_type_cmd == "REMOTE_CLOSE" and self.door:
                        self.door.close_door(); action_performed = True
                    if cmd_id and action_performed:
                        resp_payload = {"MacAddress": self.mac, "CmdId": cmd_id, "DeviceTime": datetime.now(GMT_PLUS_7).strftime(DATETIME_FORMAT_STR)}
                        self._publish_or_queue(MQTT_COMMAND_RESPONSE_TOPIC, resp_payload, qos=1)
                except Exception as e_cmd_proc:
                    if self.debug: print(f"[MQTT ERROR] Error processing command: {e_cmd_proc}")
                    import traceback; traceback.print_exc()
            
            elif topic == self.push_config_topic:
                try:
                    new_config = json.loads(payload_str)
                    if self.on_device_config_received:
                        self.on_device_config_received(new_config)
                except Exception as e_conf_proc:
                    if self.debug: print(f"[MQTT ERROR] Error processing device config: {e_conf_proc}")
        except Exception as e_on_msg_outer:
            if self.debug: print(f"[MQTT CRITICAL] Outer error in on_message: {e_on_msg_outer}")
            import traceback; traceback.print_exc()

    def _connect_with_current_token_mqtt(self):
        if not self.token or not self.username: return False
        if self._client:
            try:
                if self._client.is_connected(): self._client.disconnect()
                self._client.loop_stop()
            except: pass
            self._client = None
        try:
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=self.mac, protocol=mqtt.MQTTv5)
            self._client.on_connect = self.on_connect_token
            self._client.on_disconnect = self.on_disconnect
            self._client.on_message = self.on_message
            self._client.on_subscribe = self.on_subscribe
            self._client.on_publish = self.on_publish
            self._client.username_pw_set(self.username, self.token)
            mqtt_port_str = self.mqtt_config.get("mqtt_port", "1883")
            try: mqtt_port = int(mqtt_port_str)
            except ValueError: mqtt_port = 1883
            if mqtt_port == 8883:
                self._client.tls_set(cert_reqs=ssl.CERT_NONE); self._client.tls_insecure_set(True)
            broker_addr = self.mqtt_config.get("server", "")
            if not broker_addr: return False
            props = Properties(PacketTypes.CONNECT); props.SessionExpiryInterval = 0
            self._client.connect_async(broker_addr, mqtt_port, keepalive=60, properties=props)
            self._client.loop_start()
            return True
        except Exception as e:
            if self.debug: print(f"[MQTT ERROR] MQTT connection setup exception: {e}")
            if self._client: 
                try: self._client.loop_stop()
                except: pass
            self._client = None; return False

    def on_connect_token(self, client, userdata, flags, rc, properties=None):
        reason_code = rc.value if hasattr(rc, 'value') else rc
        self.connecting = False
        if reason_code == 0:
            self.connected = True
            try:
                client.subscribe(self.push_biometric_topic, qos=1)
                client.subscribe(self.command_topic, qos=1)
                client.subscribe(self.push_config_topic, qos=1)
            except Exception as e_sub:
                 if self.debug: print(f"[MQTT WARN] Failed to subscribe: {e_sub}")
            if not self.device_info_sent_this_session:
                self.send_device_info(); self.device_info_sent_this_session = True
            if self.on_connection_status_change: self.on_connection_status_change(True)
            self.flush_outbox()
        else:
            self.connected = False
            if self._client: 
                try: self._client.loop_stop()
                except: pass
            self._client = None
            if self.on_connection_status_change: self.on_connection_status_change(False)
            if not self.explicit_disconnect_flag and not self.connecting:
                time.sleep(1); self.connect_and_register()
            else: self.connecting = False

    def send_device_info(self):
        if self.is_actively_connected():
            payload = {"MacAddress": self.mac, "Version": VERSION, 
                       "Room": self.mqtt_config.get("room", "N/A"), 
                       "ReportTime": datetime.now(GMT_PLUS_7).strftime(DATETIME_FORMAT_STR)}
            self._publish_or_queue(MQTT_DEVICE_INFO_TOPIC, payload, qos=1, user_properties=[("MacAddress", self.mac)])

    def send_healthcheck(self):
        if self.is_actively_connected():
            bio_auth = {"IsFace": True, "IsFinger": bool(self.fingerprint_sensor and PyFingerprint),
                        "IsIdCard": bool(self.rfid_sensor), "IsIris": False, "Direction": "IN"}
            payload = {"MacAddress": self.mac, "DeviceTime": datetime.now(GMT_PLUS_7).strftime(DATETIME_FORMAT_STR),
                       "Version": VERSION, "Room": self.mqtt_config.get("room", "N/A"), "BioAuthType": bio_auth}
            self._publish_or_queue(MQTT_HEALTHCHECK_TOPIC, payload, qos=0)

    def send_recognition_event(self, bio_id, id_number, auth_method, auth_data, status, face_image_b64=None, finger_image_b64=None, iris_image_b64 = '', abnormal = False, direction = "IN"):
        person_name, id_num_send = "Unknown", id_number
        if bio_id is not None: # bio_id is int
            user_details = database.get_user_info_by_bio_id(bio_id)
            if user_details:
                person_name = user_details['person_name'] or person_name
                id_num_send = user_details['id_number'] or id_num_send
                if auth_method.upper() == "FACE" and status.upper() == "SUCCESS" and not face_image_b64:
                    face_image_b64 = user_details['face_image']
        
        payload = {"MacAddress": self.mac, "BioId": bio_id, "IdNumber": id_num_send, "PersonName": person_name,
                   "AccessTime": datetime.now(GMT_PLUS_7).strftime(DATETIME_FORMAT_STR), "Direction": direction,
                   "FaceImg": face_image_b64 or None, "FingerImg": finger_image_b64 or None,
                   "IrisImg": iris_image_b64 or None, "Abnormal": abnormal}
        self._publish_or_queue(MQTT_ACCESS_CONTROL, payload, qos=1, user_properties=[("MacAddress", self.mac)])

    def send_device_sync_request(self):
        if self.is_actively_connected():
            payload = {"MacAddress": self.mac}
            self._publish_or_queue(MQTT_SYNC_REQUEST_TOPIC, payload, qos=1, user_properties=[("MacAddress", self.mac)])

    def send_biometric_ack(self, door_id, bio_id_int, original_cmd_type):
        payload = {"MacAddress": self.mac, "DoorId": door_id, "BioId": bio_id_int,
                   "DeviceTime": datetime.now(GMT_PLUS_7).strftime(DATETIME_FORMAT_STR),
                   "CmdType": "NEW_BIO"} # As per requested ACK format
        if self.debug: print(f"[MQTT DEBUG] Sending Biometric ACK: {payload} for original CmdType {original_cmd_type}")
        self._publish_or_queue(MQTT_BIO_ACK_TOPIC, payload, qos=1, user_properties=[("MacAddress", self.mac)])

    def send_sos_alert(self):
        payload = {"MacAddress": self.mac, "DeviceTime": datetime.now(GMT_PLUS_7).strftime(DATETIME_FORMAT_STR), "AlertType": "SOS"}
        self._publish_or_queue(MQTT_SOS_ALERT_TOPIC, payload, qos=1, user_properties=[("MacAddress", self.mac)])

    def _publish_or_queue(self, topic, payload_dict, qos=0, user_properties=None):
        try:
            payload_str = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False)
        except TypeError: return
        
        mqtt_props = None
        user_props_json_db = None
        if user_properties and isinstance(user_properties, list):
            mqtt_props = Properties(PacketTypes.PUBLISH); mqtt_props.UserProperty = user_properties
            try: user_props_json_db = json.dumps(user_properties)
            except TypeError: pass

        if self.is_actively_connected():
            try:
                pub_info = self._client.publish(topic, payload=payload_str, qos=qos, properties=mqtt_props)
                if pub_info.rc != mqtt.MQTT_ERR_SUCCESS:
                    enqueue_outgoing_message(topic, payload_str, qos, user_props_json_db)
            except Exception:
                enqueue_outgoing_message(topic, payload_str, qos, user_props_json_db)
        else:
            enqueue_outgoing_message(topic, payload_str, qos, user_props_json_db)

    def flush_outbox(self):
        if not self.is_actively_connected(): return
        pending = get_pending_outbox()
        if not pending: return
        for entry_id, topic, payload_str, qos, user_props_json in pending:
            if not self.is_actively_connected(): break
            mqtt_props_pub = None
            if user_props_json:
                try:
                    user_props_list = json.loads(user_props_json)
                    if user_props_list and isinstance(user_props_list, list):
                        valid_props = [tuple(item) for item in user_props_list if isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(s, str) for s in item)]
                        if valid_props:
                            mqtt_props_pub = Properties(PacketTypes.PUBLISH); mqtt_props_pub.UserProperty = valid_props
                except Exception: pass
            try:
                pub_info = self._client.publish(topic, payload=payload_str, qos=qos, properties=mqtt_props_pub)
                if pub_info.rc == mqtt.MQTT_ERR_SUCCESS:
                    mark_outbox_sent(entry_id)
                else: break
            except Exception: break