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
    from pyfingerprint.pyfingerprint import PyFingerprint, FINGERPRINT_CHARBUFFER1, FINGERPRINT_CHARBUFFER2
except ImportError:
    PyFingerprint = None
except Exception:
    PyFingerprint = None

MQTT_DEVICE_INFO_TOPIC = "iot/devices/device_info"
MQTT_HEALTHCHECK_TOPIC = "iot/devices/healthcheck"
MQTT_ACCESS_CONTROL = "iot/devices/access"
MQTT_SYNC_REQUEST_TOPIC = "iot/devices/device_sync_bio"
MQTT_BIO_ACK_TOPIC = "iot/devices/device_received_bio"
MQTT_SOS_ALERT_TOPIC = "iot/devices/sos"
MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE = "iot/server/{mac_address}/push_biometric"
MQTT_COMMAND_TOPIC = "iot/server/command/{mac_address}"
MQTT_COMMAND_RESPONSE_TOPIC = "iot/devices/command_resp"

GMT_PLUS_7 = timezone(timedelta(hours=7))

RECONNECT_DELAY_MQTT_DEVICE = 5 
AUTH_FAILURE_RETURN_CODES_MQTT_DEVICE = [2, 4, 5]

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
        self.fingerprint_sensor = fingerprint_sensor
        self.rfid_sensor = rfid_sensor
        self.device_info_sent_this_session = False
        self.explicit_disconnect_flag = False
        self.reconnect_timer_id = None

    @property
    def client(self): return self._client

    def is_connected(self): return self.connected

    def is_actively_connected(self):
        return self.connected and self._client and self._client.is_connected()

    def set_fingerprint_sensor(self, sensor): self.fingerprint_sensor = sensor
    def set_rfid_sensor(self, sensor): self.rfid_sensor = sensor
    def set_door_handler(self, handler): self.door = handler

    def _clear_local_credentials_mqtt(self):
        if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Clearing local in-memory credentials.")
        self.token = None
        self.username = None

    def disconnect_client(self, explicit=True):
        if self._client is not None:
            self.explicit_disconnect_flag = explicit
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Disconnecting MQTT client (explicit: {explicit})...")
            try:
                if self.reconnect_timer_id is not None and hasattr(self.on_connection_status_change, '__self__'):
                    app_instance = self.on_connection_status_change.__self__
                    if hasattr(app_instance, 'root') and app_instance.root:
                        try: app_instance.root.after_cancel(self.reconnect_timer_id)
                        except: pass
                    self.reconnect_timer_id = None

                if self._client.is_connected():
                     try:
                         self._client.unsubscribe(self.push_biometric_topic)
                         self._client.unsubscribe(self.command_topic)
                     except: pass
                self._client.loop_stop(force=False) 
                self._client.disconnect()
                if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) MQTT client disconnect requested.")
            except Exception as e:
                if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Error during MQTT client disconnect: {e}")
            
            if explicit:
                self._client = None 
                self.connected = False
                self.connecting = False
                if self.on_connection_status_change:
                    self.on_connection_status_change(False)

    def connect_and_register(self):
        if self.connecting or self.is_actively_connected():
            if self.debug: print(f"[MQTT TRACE] (MAC: {self.mac}) connect_and_register: Already connecting or connected.")
            return self.is_actively_connected()

        self.connecting = True
        self.explicit_disconnect_flag = False

        if not is_network_available():
            if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) No internet. Cannot connect.")
            if self.on_connection_status_change: self.on_connection_status_change(False)
            self._schedule_reconnect_attempt_mqtt()
            self.connecting = False
            return False

        if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Starting connection sequence: Fetching new HTTP token.")
        self._clear_local_credentials_mqtt()

        if self.retrieve_token_via_http():
            if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) HTTP token retrieval SUCCEEDED. Proceeding to MQTT connect.")
            if not self._connect_with_current_token_mqtt():
                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) MQTT connection failed after new token.")
                self._schedule_reconnect_attempt_mqtt()
        else:
            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) HTTP token retrieval FAILED.")
            self._schedule_reconnect_attempt_mqtt()
        
        if not self._client or (self._client and not self._client.is_connected() and not self.connected):
            self.connecting = False
        return True


    def retrieve_token_via_http(self) -> bool:
        server_address_conf = self.mqtt_config.get('server')
        http_port_conf = self.mqtt_config.get('http_port')

        if not server_address_conf:
            if self.debug: print(f"[MQTT ERROR][HTTP Token] (MAC: {self.mac}) 'server' not configured.")
            return False
        
        http_port = 8080 
        if http_port_conf is not None:
            try: http_port = int(http_port_conf)
            except ValueError:
                if self.debug: print(f"[MQTT ERROR][HTTP Token] (MAC: {self.mac}) Invalid http_port: {http_port_conf}. Using 8080.")
        
        api_base_url = server_address_conf.strip().rstrip('/')
        if not api_base_url.startswith(('http://', 'https://')):
            api_base_url = f"http://{api_base_url}"
        
        url = f"{api_base_url}:{http_port}/api/devicecomm/getmqtttoken"
        payload = {"macAddress": self.mac, "password": generate_hashed_password(self.mac)}
        response_text_for_log = "N/A"
        try:
            if self.debug: print(f"[MQTT DEBUG][HTTP Token] (MAC: {self.mac}) Requesting token from {url}")
            resp = requests.post(url, json=payload, timeout=10)
            response_text_for_log = resp.text[:200] if resp else "No response object"
            if self.debug: print(f"[MQTT DEBUG][HTTP Token] (MAC: {self.mac}) Server response status: {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            if self.debug: print(f"[MQTT DEBUG][HTTP Token] (MAC: {self.mac}) Server response data (parsed): {str(data)[:300]}...")
        except requests.exceptions.HTTPError as e:
            if self.debug: print(f"[MQTT ERROR][HTTP Token] (MAC: {self.mac}) HTTP error: {e}. Response: {response_text_for_log}")
            return False
        except requests.exceptions.RequestException as e:
            if self.debug: print(f"[MQTT ERROR][HTTP Token] (MAC: {self.mac}) Request exception: {e}")
            return False
        except json.JSONDecodeError:
            if self.debug: print(f"[MQTT ERROR][HTTP Token] (MAC: {self.mac}) Failed to decode JSON. Response: {response_text_for_log}")
            return False
        except Exception as e:
            if self.debug: print(f"[MQTT ERROR][HTTP Token] (MAC: {self.mac}) Other HTTP request error: {e}")
            return False

        if data.get("code") != "OK" or "data" not in data:
            if self.debug: print(f"[MQTT ERROR][HTTP Token] (MAC: {self.mac}) Unexpected API response: {data}")
            return False

        api_data_field = data.get("data", {})
        new_token = api_data_field.get("token")
        new_username = api_data_field.get("username")
        token_preview = str(new_token)[:10] + "..." if new_token else "None"
        if self.debug: print(f"[MQTT DEBUG][HTTP Token] (MAC: {self.mac}) Extracted: User='{new_username}', Token='{token_preview}'")

        if not new_token or not new_username:
            if self.debug: print(f"[MQTT ERROR][HTTP Token] (MAC: {self.mac}) Missing token/username in API data: {api_data_field}")
            return False

        self.token = new_token
        self.username = new_username
        self.device_info_sent_this_session = False 

        if self.on_token_received: # This callback is in main.py's App class
            self.on_token_received(new_username, new_token) # App will save to its self.mqtt_config and file
        else:
            if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) on_token_received callback not set. Token updated only in MQTTManager instance.")

        if self.debug: print(f"[MQTT INFO][HTTP Token] (MAC: {self.mac}) Successfully retrieved and stored new credentials. User: {self.username}")
        return True

    def on_disconnect(self, client, userdata, rc, properties=None):
        reason_code = rc
        if hasattr(rc, 'value'): reason_code = rc.value

        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) MQTT disconnected. RC: {reason_code}, Explicit: {self.explicit_disconnect_flag}")

        self.connected = False
        self.connecting = False
        self._client = None 

        if self.on_connection_status_change:
            self.on_connection_status_change(False)

        if not self.explicit_disconnect_flag:
            if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Unexpected MQTT disconnect (rc={reason_code}). Scheduling reconnect sequence.")
            self._schedule_reconnect_attempt_mqtt()
        else:
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Explicit disconnect. No auto-reconnect by this callback.")
            self.explicit_disconnect_flag = False

    def _schedule_reconnect_attempt_mqtt(self):
        if self.connecting or self.explicit_disconnect_flag: return

        if self.reconnect_timer_id is not None and hasattr(self.on_connection_status_change, '__self__'):
            app_instance = self.on_connection_status_change.__self__
            if hasattr(app_instance, 'root') and app_instance.root:
                try: app_instance.root.after_cancel(self.reconnect_timer_id)
                except: pass
        
        delay_ms = RECONNECT_DELAY_MQTT_DEVICE * 1000
        if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Scheduling full reconnect (new token then connect) in {RECONNECT_DELAY_MQTT_DEVICE}s.")
        
        if hasattr(self.on_connection_status_change, '__self__'):
            app_instance = self.on_connection_status_change.__self__
            if hasattr(app_instance, 'root') and app_instance.root and app_instance.root.winfo_exists():
                self.reconnect_timer_id = app_instance.root.after(delay_ms, self.connect_and_register)
            else:
                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Cannot schedule reconnect: App root window unavailable.")
        else:
             if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Cannot schedule reconnect: on_connection_status_change not bound.")


    def on_subscribe(self, client, userdata, mid, granted_qos, properties=None):
        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Subscribed: mid={mid}, QoS/RCs={granted_qos}")

    def on_publish(self, client, userdata, mid, *args, **kwargs):
        if self.debug and mid != 0: print(f"[MQTT TRACE] (MAC: {self.mac}) Message MID {mid} published.")
    
    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload_str = msg.payload.decode('utf-8')
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Received on '{topic}': {payload_str[:150]}{'...' if len(payload_str)>150 else ''}")

            if topic == self.push_biometric_topic:
                if not self.is_actively_connected():
                    if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Biometric push ignored, not actively connected.")
                    return
                try:
                    command_list = json.loads(payload_str)
                    if not isinstance(command_list, list):
                        if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Invalid bio push: Expected list.")
                        return

                    sync_all_processed_delete = False
                    for command_item in command_list:
                        if not isinstance(command_item, dict):
                            if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Skipping invalid item in bio push (not dict).")
                            continue

                        cmd_type = command_item.get("cmdType")
                        bio_id = command_item.get("bioId")
                        processed_ok = False
                        finger_position_for_db = None

                        if cmd_type == "SYNC_ALL":
                            if not sync_all_processed_delete:
                                if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) SYNC_ALL: Clearing sensor and DB.")
                                sensor_cleared = False
                                if self.fingerprint_sensor and PyFingerprint is not None:
                                    try:
                                        if self.fingerprint_sensor.verifyPassword():
                                            if self.fingerprint_sensor.clearDatabase(): sensor_cleared = True
                                            else:
                                                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Failed to clear sensor DB (SYNC_ALL).")
                                        else:
                                            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Sensor pwd verify failed (SYNC_ALL).")
                                    except Exception as e_fp_clear:
                                        if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exc clearing sensor (SYNC_ALL): {e_fp_clear}")
                                else:
                                    sensor_cleared = True 
                                    if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) FP sensor not avail for SYNC_ALL clear.")
                                
                                db_cleared = database.delete_all_biometrics_and_access_for_mac(self.mac)
                                processed_ok = db_cleared and sensor_cleared
                                sync_all_processed_delete = True

                            if 'bioDatas' in command_item and bio_id:
                                if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) SYNC_ALL includes PUSH_NEW for bioId: {bio_id}")
                                cmd_type = "PUSH_NEW_BIO"
                            else:
                                if not bio_id and processed_ok:
                                     if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) SYNC_ALL (clear only) processed.")
                                continue

                        if cmd_type in ["PUSH_NEW_BIO", "PUSH_UPDATE_BIO"]:
                            if not bio_id:
                                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Skipping {cmd_type}: Missing 'bioId'.")
                                continue
                            if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Processing {cmd_type} for bioId: {bio_id}")
                            
                            finger_op_success, face_op_success, idcard_op_success = True, True, True

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
                                                    if actual_position >= 0: finger_position_for_db = actual_position
                                                    else:
                                                        if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Sensor storeTemplate error for {bio_id}: {actual_position}")
                                                        finger_op_success = False
                                                else:
                                                    if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Failed to upload FP for {bio_id}.")
                                                    finger_op_success = False
                                            else:
                                                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Sensor pwd verify failed for {bio_id}.")
                                                finger_op_success = False; break
                                        except Exception as e_fp_enroll:
                                            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exc enrolling FP {bio_id}: {e_fp_enroll}")
                                            finger_op_success = False
                                    else:
                                        if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) FP sensor not avail for {bio_id} FINGER data.")
                                
                                elif bio_data_type == "FACE":
                                     if not template_b64:
                                          if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) FACE template missing for {bio_id}.")
                                          face_op_success = False
                                elif bio_data_type == "IDCARD":
                                     if not template_b64:
                                          if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) IDCARD template missing for {bio_id}.")
                                          idcard_op_success = False
                                else:
                                     if bio_data_type not in ["FINGER", "FACE", "IDCARD"] and bio_data_type:
                                         if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Unknown BioType: {bio_data_type} for {bio_id}")
                            
                            if finger_op_success and face_op_success and idcard_op_success:
                                processed_ok = database.process_biometric_push(command_item, self.mac, finger_position_from_sensor=finger_position_for_db)
                            else:
                                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Skipping DB update for {bio_id} due to bio op failure.")
                                processed_ok = False

                        elif cmd_type == "PUSH_DELETE_BIO":
                            if not bio_id:
                                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Skipping PUSH_DELETE_BIO: Missing 'bioId'.")
                                continue
                            if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Processing PUSH_DELETE_BIO for {bio_id}")
                            
                            position_to_delete = database.get_finger_position_by_bio_id_and_mac(bio_id, self.mac)
                            sensor_delete_successful = True
                            if position_to_delete is not None and self.fingerprint_sensor and PyFingerprint is not None:
                                try:
                                    if self.fingerprint_sensor.verifyPassword():
                                        if not self.fingerprint_sensor.deleteTemplate(position_to_delete):
                                            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Failed to delete from sensor for {bio_id} at pos {position_to_delete}.")
                                            sensor_delete_successful = False
                                        else:
                                             if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Deleted FP from sensor pos {position_to_delete} for {bio_id}.")
                                    else:
                                        if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Sensor pwd verify failed for PUSH_DELETE_BIO of {bio_id}.")
                                        sensor_delete_successful = False
                                except Exception as e_fp_del:
                                    if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exc deleting from sensor for {bio_id}: {e_fp_del}")
                                    sensor_delete_successful = False
                            
                            if sensor_delete_successful:
                                processed_ok = database.delete_biometrics_and_access_for_bio_id(bio_id, self.mac)
                            else: processed_ok = False

                        else:
                            if cmd_type != "SYNC_ALL":
                                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Unknown cmdType: {cmd_type} for {bio_id}")

                        if processed_ok and bio_id: self.send_biometric_ack(bio_id)
                        elif not processed_ok and bio_id:
                            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Failed processing {cmd_type} for {bio_id}. No ACK.")

                except json.JSONDecodeError:
                    if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) JSON decode error in biometric push processing.")
                except Exception as e_bio_proc:
                    if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Error processing biometric push: {e_bio_proc}")

            elif topic == self.command_topic:
                if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Received command on '{topic}': {payload_str[:150]}...")
                try:
                    command = json.loads(payload_str)
                    if not isinstance(command, dict):
                        if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Invalid command: Expected object.")
                        return

                    mac_address, cmd_id, cmd_type = command.get("MacAddress"), command.get("CmdId"), command.get("CmdType")
                    cmd_time_str, cmd_timeout = command.get("CmdTime"), command.get("CmdTimeout", 30)

                    if mac_address != self.mac:
                        if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) MAC mismatch in command. Expected {self.mac}, got {mac_address}.")
                        return

                    if cmd_time_str:
                        try:
                            cmd_time_dt_utc = datetime.fromisoformat(cmd_time_str.replace("Z", "+00:00"))
                            if cmd_timeout > 0 and (datetime.now(timezone.utc) - cmd_time_dt_utc).total_seconds() > cmd_timeout:
                                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Command timeout for ID {cmd_id} (Type: {cmd_type}).")
                                return
                        except ValueError:
                            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Invalid CmdTime '{cmd_time_str}' for ID {cmd_id}.")
                            return
                    
                    action_performed = False
                    if cmd_type == "REMOTE_OPEN":
                        if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Processing REMOTE_OPEN ID {cmd_id}.")
                        if self.door: self.door.open_door(); action_performed = True
                        else: 
                            if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Door handler N/A for REMOTE_OPEN.")
                    elif cmd_type == "REMOTE_CLOSE":
                        if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Processing REMOTE_CLOSE ID {cmd_id}.")
                        if self.door: self.door.close_door(); action_performed = True
                        else: 
                            if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Door handler N/A for REMOTE_CLOSE.")
                    else:
                        if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Unknown command type: {cmd_type}.")

                    if cmd_id and action_performed:
                        response_payload = {"MacAddress": self.mac, "CmdId": cmd_id, "DeviceTime": datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')}
                        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Sending command response: {response_payload}")
                        self._publish_or_queue(MQTT_COMMAND_RESPONSE_TOPIC, response_payload, qos=1) 
                
                except json.JSONDecodeError:
                    if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) JSON decode error in command processing.")
                except KeyError as e_key:
                    if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Missing key in command: {e_key}")
                except Exception as e_cmd_proc:
                    if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Unhandled error processing command: {e_cmd_proc}")
        
        except Exception as e_on_msg_outer:
            if self.debug: print(f"[MQTT CRITICAL] (MAC: {self.mac}) Outer unhandled error in on_message: {e_on_msg_outer}")


    def _connect_with_current_token_mqtt(self):
        if not self.token or not self.username:
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) _connect_with_current_token: Token or username missing.")
            return False
        
        if self._client:
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) _connect_with_current_token: Disconnecting old client instance.")
            self._client.loop_stop(force=True)
            try: self._client.disconnect()
            except: pass
            self._client = None
        
        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Attempting MQTT connect with user: {self.username}")
        try:
            self._client = mqtt.Client(client_id=self.mac, protocol=mqtt.MQTTv5)
            self._client.on_connect = self.on_connect_token
            self._client.on_disconnect = self.on_disconnect
            self._client.on_message = self.on_message
            self._client.on_subscribe = self.on_subscribe
            self._client.on_publish = self.on_publish

            self._client.username_pw_set(self.username, self.token)
            
            mqtt_broker_port_str = self.mqtt_config.get("mqtt_port", "1883")
            try: 
                mqtt_broker_port = int(mqtt_broker_port_str)
                if not (0 < mqtt_broker_port < 65536): raise ValueError("Port out of range")
            except ValueError:
                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Invalid MQTT port: '{mqtt_broker_port_str}'. Using 1883."); mqtt_broker_port = 1883

            if mqtt_broker_port == 8883:
                self._client.tls_set(cert_reqs=ssl.CERT_NONE)
                self._client.tls_insecure_set(True)

            broker_address = self.mqtt_config.get("server", "")
            if not broker_address:
                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) MQTT Broker address not configured.")
                return False

            self._client.connect_async(broker_address, mqtt_broker_port, keepalive=60)
            self._client.loop_start()
            return True
        except socket.error as se:
             if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Network error during MQTT connect_async: {se}")
             if self._client: 
                 try: self._client.loop_stop(force=True)
                 except: pass
             self._client = None
             return False
        except Exception as e:
            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exception during MQTT connection setup: {e}")
            if self._client: 
                try: self._client.loop_stop(force=True)
                except: pass
            self._client = None
            return False

    def on_connect_token(self, client, userdata, flags, rc, properties):
        reason_code = rc
        paho_rc_string = "N/A"
        if hasattr(rc, 'value'): 
            reason_code = rc.value
            paho_rc_string = str(rc)
        elif isinstance(reason_code, int):
            paho_rc_string = mqtt.connack_string(reason_code)

        self.connecting = False
        if reason_code == 0:
            self.connected = True
            if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) MQTT connected successfully. Subscribing...")
            try:
                client.subscribe(self.push_biometric_topic, qos=1)
                client.subscribe(self.command_topic, qos=1)
                if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Subscribed to bio push and command topics.")
            except Exception as e_sub:
                 if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Failed to subscribe after connect: {e_sub}")
            
            if not self.device_info_sent_this_session:
                self.send_device_info() 
                self.device_info_sent_this_session = True

            if self.on_connection_status_change:
                self.on_connection_status_change(True)
            self.flush_outbox()
        else:
            self.connected = False
            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) MQTT connection failed in on_connect_token. RC: {reason_code} ({paho_rc_string})")
            
            if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) MQTT connect failed (rc={reason_code}). Scheduling to fetch new token and retry.")
            
            if self._client:
                try: self._client.loop_stop(force=True)
                except: pass
            self._client = None 
            
            if self.on_connection_status_change:
                self.on_connection_status_change(False)
            
            if not self.explicit_disconnect_flag:
                 self._schedule_reconnect_attempt_mqtt()

    def send_device_info(self): 
        if self.is_actively_connected(): 
            room_name = self.mqtt_config.get("room", "N/A")
            device_version = VERSION
            info_payload = {"MacAddress": self.mac, "Version": device_version, "Room": room_name, "ReportTime": datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')}
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Sending Device Info: {info_payload} to {MQTT_DEVICE_INFO_TOPIC}")
            self._publish_or_queue(MQTT_DEVICE_INFO_TOPIC, info_payload, qos=1, user_properties=[("MacAddress", self.mac)])
        elif self.debug:
            print(f"[MQTT WARN] (MAC: {self.mac}) Cannot send Device Info: Client not actively connected.")

    def send_healthcheck(self): 
        if self.is_actively_connected():
            device_time_gmt7 = datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
            room_name = self.mqtt_config.get("room", "N/A")
            device_version = "20250601"
            bio_auth_support = {"IsFace": True, "IsFinger": bool(self.fingerprint_sensor and PyFingerprint), "IsIdCard": bool(self.rfid_sensor), "IsIris": False, "Direction": "IN"}
            heartbeat = {"MacAddress": self.mac, "DeviceTime": device_time_gmt7, "Version": device_version, "BioAuthType": bio_auth_support }
            self._publish_or_queue(MQTT_HEALTHCHECK_TOPIC, heartbeat, qos=0)
            if self.debug: print(f"[MQTT TRACE] (MAC: {self.mac}) Sent healthcheck: {str(heartbeat)[:200]}")

    def send_recognition_event(self, bio_id, id_number, auth_method, auth_data, status, face_image_b64=None, finger_image_b64=None, iris_image_b64 = '', abnormal = False, direction = "IN"):
        device_time_gmt7 = datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
        person_name_to_send, id_number_to_send = "Unknown", id_number

        if bio_id:
            user_details_row = database.get_user_info_by_bio_id(bio_id) 
            if user_details_row:
                if 'person_name' in user_details_row and user_details_row['person_name']: person_name_to_send = user_details_row['person_name']
                if not id_number_to_send and 'id_number' in user_details_row and user_details_row['id_number']: id_number_to_send = user_details_row['id_number']
                if auth_method.upper() == "FACE" and status.upper() == "SUCCESS" and not face_image_b64 and 'face_image' in user_details_row: face_image_b64 = user_details_row['face_image']
                if auth_method.upper() == "FINGER" and status.upper() == "SUCCESS" and not finger_image_b64 and 'finger_image' in user_details_row: finger_image_b64 = user_details_row['finger_image']

        payload_dict = {"MacAddress": self.mac, "BioId": bio_id, "IdNumber" : id_number_to_send, "AccessTime": device_time_gmt7, "Direction" : direction, "FaceImg" : face_image_b64, "FingerImg" : finger_image_b64, "IrisImg" : iris_image_b64, "Abnormal": abnormal}
        
        if auth_method.upper() == "FACE" and status.upper() == "SUCCESS" and face_image_b64: payload_dict["FaceImg"] = face_image_b64
        if auth_method.upper() == "FINGER" and status.upper() == "SUCCESS" and finger_image_b64: payload_dict["FingerImg"] = finger_image_b64
        if auth_method.upper() == "IRIS" and status.upper() == "SUCCESS" and iris_image_b64: payload_dict["IrisImg"] = iris_image_b64
        
        if self.debug:
            log_payload = {k: (v[:30]+'...' if isinstance(v, str) and k in ["FaceImg", "FingerImg", "IrisImg"] and v and len(v)>30 else v) for k,v in payload_dict.items()}
            print(f"[MQTT DEBUG] (MAC: {self.mac}) Queuing/Publishing Recognition Event to {MQTT_ACCESS_CONTROL}: {log_payload}")
        
        self._publish_or_queue(MQTT_ACCESS_CONTROL, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])

    def send_device_sync_request(self): 
        if self.is_actively_connected():
            payload_dict = {"MacAddress": self.mac, "Token" : self.token} 
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Sending Device Sync Request: {payload_dict}")
            self._publish_or_queue(MQTT_SYNC_REQUEST_TOPIC, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])
        elif self.debug:
            print(f"[MQTT WARN] (MAC: {self.mac}) Cannot send Device Sync Request: MQTT not actively connected.")

    def send_biometric_ack(self, bio_id):
        payload_dict = {"bioId": bio_id, "macAddress": self.mac}
        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) Sending Biometric ACK for bioId {bio_id}: {payload_dict}")
        self._publish_or_queue(MQTT_BIO_ACK_TOPIC, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])

    def send_sos_alert(self):
        device_time_gmt7 = datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
        payload_dict = {"MacAddress": self.mac, "DeviceTime": device_time_gmt7, "AlertType": "SOS"}
        self._publish_or_queue(MQTT_SOS_ALERT_TOPIC, payload_dict, qos=1, user_properties=[("MacAddress", self.mac)])
        if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) SOS alert queued/published to {MQTT_SOS_ALERT_TOPIC}.")

    def _publish_or_queue(self, topic, payload_dict, qos=0, user_properties=None):
        try:
            payload_str = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False)
        except TypeError as te_json:
            if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) JSON dump failed for topic {topic}: {te_json}. Payload: {str(payload_dict)[:200]}")
            return
        
        mqtt_props = None
        user_properties_json_for_db = None
        if user_properties:
            mqtt_props = Properties(PacketTypes.PUBLISH)
            mqtt_props.UserProperty = user_properties
            try: user_properties_json_for_db = json.dumps(user_properties)
            except TypeError: 
                if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Could not serialize user_properties for DB: {user_properties}")

        if self.is_actively_connected():
            try:
                publish_info = self._client.publish(topic, payload=payload_str, qos=qos, properties=mqtt_props)
                if publish_info.rc != mqtt.MQTT_ERR_SUCCESS:
                    if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) MQTT publish to {topic} failed (code {publish_info.rc}). Queuing.")
                    enqueue_outgoing_message(topic, payload_str, qos, user_properties_json_for_db)
            except Exception as e_pub:
                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exception during MQTT publish to {topic}: {e_pub}. Queuing.")
                enqueue_outgoing_message(topic, payload_str, qos, user_properties_json_for_db)
        else:
            if self.debug: print(f"[MQTT DEBUG] (MAC: {self.mac}) MQTT not actively connected. Queuing message for {topic}.")
            enqueue_outgoing_message(topic, payload_str, qos, user_properties_json_for_db)

    def flush_outbox(self):
        if not self.is_actively_connected(): return
            
        pending_messages = get_pending_outbox() 
        if not pending_messages: return
        
        if self.debug: print(f"[MQTT INFO] (MAC: {self.mac}) Found {len(pending_messages)} messages in outbox. Flushing...")
        for entry_id, topic, payload_str, qos, user_props_json_str in pending_messages:
            if not self.is_actively_connected():
                 if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) MQTT disconnected during outbox flush. Stopping.")
                 break
            mqtt_props_for_publish = None
            if user_props_json_str: 
                try:
                    user_props_list_of_tuples = json.loads(user_props_json_str) 
                    if user_props_list_of_tuples and isinstance(user_props_list_of_tuples, list):
                        mqtt_props_for_publish = Properties(PacketTypes.PUBLISH)
                        mqtt_props_for_publish.UserProperty = user_props_list_of_tuples
                except json.JSONDecodeError:
                     if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Failed to decode UserProps JSON for outbox msg ID {entry_id}: '{user_props_json_str}'")
            try:
                if self.debug: print(f"[MQTT TRACE] (MAC: {self.mac}) Publishing outbox msg: ID {entry_id}, Topic {topic}")
                publish_info = self._client.publish(topic, payload=payload_str, qos=qos, properties=mqtt_props_for_publish)
                if publish_info.rc == mqtt.MQTT_ERR_SUCCESS: 
                    mark_outbox_sent(entry_id) 
                    if self.debug: print(f"[MQTT TRACE] (MAC: {self.mac}) Outbox msg ID {entry_id} sent and marked.")
                else: 
                    if self.debug: print(f"[MQTT WARN] (MAC: {self.mac}) Failed to publish outbox msg ID {entry_id} (MQTT Err: {publish_info.rc}). Stopping flush.")
                    break 
            except Exception as e_flush_pub: 
                if self.debug: print(f"[MQTT ERROR] (MAC: {self.mac}) Exception publishing outbox msg ID {entry_id}: {e_flush_pub}. Stopping flush.")
                break 
        if self.debug and pending_messages: print(f"[MQTT DEBUG] (MAC: {self.mac}) Outbox flush finished.")