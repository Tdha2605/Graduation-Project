import json
import time
import ssl
import requests
import socket
import hashlib
import base64
from datetime import datetime, timezone, timedelta
import paho.mqtt.client as mqtt

MQTT_HEALTHCHECK_TOPIC = "iot/devices/healthcheck"
MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE = "iot/devices/device_info"

GMT_PLUS_7 = timezone(timedelta(hours=7))
ENROLLMENT_STATION_VERSION = 20250601

try:
    from database_enroll import update_discovered_device
except ImportError:
    def update_discovered_device(room_name: str, mac_address: str):
        print(f"[MQTT Fallback DB] update_discovered_device for {room_name}, {mac_address} (No DB op)")

def generate_hashed_password(mac):
    data = (mac + "navis@salt").encode("utf-8")
    hash_bytes = hashlib.sha256(data).digest()
    return base64.b64encode(hash_bytes).decode("utf-8")

def is_connected_to_internet():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError: return False
    except Exception: return False

class MQTTEnrollManager:
    def __init__(self, mqtt_config, enroll_mac, config_file_path, debug=True):
        self.mqtt_config = mqtt_config
        self.mac = enroll_mac
        self.config_file_path = config_file_path
        self.debug = debug
        self.username = self.mqtt_config.get("mqtt_username")
        self.token = self.mqtt_config.get("mqtt_password")
        self._client = None
        self.connected = False
        self.connecting = False
        self.explicit_disconnect = False
        self.on_connection_status_change = None
        self.on_device_info_received = None

    @property
    def client(self): return self._client

    def is_actively_connected(self):
        return self.connected and self._client and self._client.is_connected()

    def _save_config(self):
        try:
            with open(self.config_file_path, "w") as f:
                json.dump(self.mqtt_config, f, indent=2)
            if self.debug: print(f"[Enroll DEBUG] Saved updated config to {self.config_file_path}")
        except Exception as e:
            if self.debug: print(f"[Enroll ERROR] Failed to save config {self.config_file_path}: {e}")

    def _clear_local_credentials(self):
        if self.debug: print("[Enroll INFO] Clearing local (in-memory) authentication credentials.")
        self.token = None
        self.username = None

    def disconnect_client(self, explicit=True):
        if self._client is not None:
            self.explicit_disconnect = explicit
            if self.debug: print(f"[Enroll DEBUG] Disconnecting MQTT client (explicit: {explicit})...")
            try:
                if self._client.is_connected():
                     try: self._client.unsubscribe(MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE)
                     except: pass
                self._client.loop_stop(force=False)
                self._client.disconnect()
                if self.debug: print("[Enroll DEBUG] MQTT client disconnect requested.")
            except Exception as e:
                if self.debug: print(f"[Enroll ERROR] Error during MQTT disconnect: {e}")

            if explicit:
                if self._client: self._client = None
                self.connected = False
                self.connecting = False
                if self.on_connection_status_change:
                    self.on_connection_status_change(False)
    
    def attempt_connection_sequence(self, is_manual_retry=False):
        if self.connecting and not is_manual_retry:
            if self.debug: print(f"[Enroll TRACE] attempt_connection_sequence skipped: connecting={self.connecting}")
            return

        if self.explicit_disconnect and not is_manual_retry:
             if self.debug: print(f"[Enroll TRACE] attempt_connection_sequence skipped: explicit_disconnect={self.explicit_disconnect}")
             return

        self.connecting = True

        if not is_connected_to_internet():
            if self.debug: print("[Enroll WARN] No internet access. Cannot attempt connection sequence.")
            if self.on_connection_status_change: self.on_connection_status_change(False)
            self.connecting = False
            return

        if self.debug: print("[Enroll INFO] Starting connection sequence: Fetching new HTTP token.")
        self._clear_local_credentials()

        if self.retrieve_token_via_http():
            if self.debug: print("[Enroll INFO] HTTP token retrieval SUCCEEDED. Proceeding to MQTT connection.")
            if not self.connect_with_current_token():
                 if self.debug: print("[Enroll WARN] MQTT connection initiation failed in connect_with_current_token.")
                 # self.connecting should be False if connect_with_current_token returned False
        else:
            if self.debug: print("[Enroll ERROR] HTTP token retrieval FAILED. No automatic retry by this sequence.")
            if self.on_connection_status_change:
                self.on_connection_status_change(False)
            self.connecting = False
        
        # If not actively connecting (i.e., connect_async was not successfully called or already finished)
        # and not actually connected, ensure connecting is false.
        # on_connect_token will set self.connecting = False.
        # If connect_with_current_token failed before connect_async, it sets self.connecting = False.
        # If retrieve_token_via_http failed, it sets self.connecting = False.
        # This is a final check.
        is_in_connect_async_state = False
        if self._client and hasattr(self._client, '_state') and hasattr(self._client, 'MQTT_CS_CONNECT_ASYNC'):
            try: # MQTT_CS_CONNECT_ASYNC is an enum/int
                is_in_connect_async_state = (self._client._state == self._client.MQTT_CS_CONNECT_ASYNC)
            except: # In case MQTT_CS_CONNECT_ASYNC is not found on older paho or other issues
                pass 
        
        if not self.is_actively_connected() and not is_in_connect_async_state:
             self.connecting = False


    def retrieve_token_via_http(self) -> bool:
        if self.debug: print("[Enroll DEBUG][HTTP Token] Attempting to retrieve token via HTTP...")
        http_port_str = self.mqtt_config.get('http_port', '8080')
        api_server_host = self.mqtt_config.get('broker')

        if not api_server_host:
             if self.debug: print("[Enroll ERROR][HTTP Token] 'broker' (API server host) not found in MQTT config.")
             return False
        try:
            http_port = int(http_port_str)
        except ValueError:
            if self.debug: print(f"[Enroll ERROR][HTTP Token] Invalid http_port '{http_port_str}'. Using default 8080.")
            http_port = 8080

        base_url = api_server_host.strip().rstrip('/')
        if not base_url.startswith(('http://', 'https://')):
            base_url = f"http://{base_url}"

        url = f"{base_url}:{http_port}/api/devicecomm/getmqtttoken"
        if self.debug: print(f"[Enroll DEBUG][HTTP Token] Requesting token from URL: {url}")
        payload = {"macAddress": self.mac, "password": generate_hashed_password(self.mac)}
        response_text_for_log = "N/A"
        try:
            resp = requests.post(url, json=payload, timeout=10)
            response_text_for_log = resp.text[:200] if resp else "No response object"
            if self.debug: print(f"[Enroll DEBUG][HTTP Token] Server response status: {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            if self.debug: print(f"[Enroll DEBUG][HTTP Token] Server response data (parsed JSON): {str(data)[:300]}...")
        except requests.exceptions.HTTPError as http_err:
            if self.debug: print(f"[Enroll ERROR][HTTP Token] HTTP error: {http_err}. Response: {response_text_for_log}")
            return False
        except requests.exceptions.RequestException as req_err:
            if self.debug: print(f"[Enroll ERROR][HTTP Token] Request exception: {req_err}")
            return False
        except json.JSONDecodeError as json_err:
             if self.debug: print(f"[Enroll ERROR][HTTP Token] Failed to decode JSON. Response: {response_text_for_log}. Error: {json_err}")
             return False
        except Exception as e:
            if self.debug: print(f"[Enroll ERROR][HTTP Token] Unexpected error: {e}")
            return False

        if data.get("code") != "OK" or "data" not in data:
            if self.debug: print(f"[Enroll ERROR][HTTP Token] API error or unexpected structure: {data}")
            return False

        token_data_field = data.get("data", {})
        new_token = token_data_field.get("token")
        new_username = token_data_field.get("username")

        if self.debug:
            token_preview = str(new_token)[:10] + "..." if new_token else "None"
            print(f"[Enroll DEBUG][HTTP Token] Extracted: Username='{new_username}', Token (preview)='{token_preview}'")

        if not new_token or not new_username:
            if self.debug: print(f"[Enroll ERROR][HTTP Token] Token or username missing in API data: {token_data_field}")
            return False

        self.token = new_token
        self.username = new_username
        self.mqtt_config["mqtt_username"] = self.username
        self.mqtt_config["mqtt_password"] = self.token
        if self.debug: print(f"[Enroll INFO][HTTP Token] Successfully retrieved credentials. Username: {self.username}")
        self._save_config()
        return True

    def on_disconnect(self, client, userdata, rc, properties=None):
        reason_code = rc.value if hasattr(rc, 'value') else rc
        if self.debug: print(f"[Enroll DEBUG] MQTT Disconnected. RC: {reason_code}, Explicit: {self.explicit_disconnect}, Connecting: {self.connecting}")
        
        previous_connected_state = self.connected
        self.connected = False
        if client == self._client: self._client = None

        if self.on_connection_status_change:
            self.on_connection_status_change(False)

        if not self.explicit_disconnect and not self.connecting:
            if self.debug: print("[Enroll INFO] Unexpected disconnect. Starting persistent reconnection attempts...")
            
            reconnect_attempt_delay = 1 
            max_delay = 60 
            
            while not self.is_actively_connected():
                if self.explicit_disconnect:
                    if self.debug: print("[Enroll INFO] Persistent reconnection aborted by explicit disconnect.")
                    break
                
                if not is_connected_to_internet():
                    if self.debug: print(f"[Enroll WARN] Network unavailable. Waiting {reconnect_attempt_delay}s...")
                    time.sleep(reconnect_attempt_delay)
                    reconnect_attempt_delay = min(reconnect_attempt_delay * 2, max_delay)
                    continue

                if self.debug: print(f"[Enroll INFO] Attempting to reconnect (delay: {reconnect_attempt_delay}s)...")
                
                self.attempt_connection_sequence() # This will manage self.connecting

                wait_for_connect_cb_time = 0
                max_wait_for_cb = 15 # Max seconds to wait for connect_async to resolve (via on_connect or on_disconnect)
                
                # Wait until self.connecting becomes False (meaning connect attempt finished) or timeout
                while self.connecting and wait_for_connect_cb_time < max_wait_for_cb :
                    if self.debug: print(f"[Enroll TRACE] Waiting for connection attempt to resolve ({wait_for_connect_cb_time+1}s)...")
                    time.sleep(1)
                    wait_for_connect_cb_time +=1
                
                if self.is_actively_connected():
                    if self.debug: print("[Enroll INFO] Reconnection successful!")
                    break 
                else:
                    if self.connecting: # Timed out waiting for connect_async to resolve
                        if self.debug: print(f"[Enroll WARN] Timed out waiting for connect_async to resolve. Assuming failure for this attempt.")
                        self.connecting = False # Force reset if stuck
                    if self.debug: print(f"[Enroll WARN] Reconnection attempt did not succeed.")
                
                if not self.is_actively_connected(): # Check again after waiting
                    actual_sleep_time = reconnect_attempt_delay
                    if self.debug: print(f"[Enroll INFO] Sleeping {actual_sleep_time}s before next reconnect.")
                    time.sleep(actual_sleep_time)
                    reconnect_attempt_delay = min(reconnect_attempt_delay * 2, max_delay)
            
            if self.is_actively_connected():
                 if self.debug: print("[Enroll INFO] Persistent reconnection ended successfully.")
            else:
                 if self.debug: print("[Enroll INFO] Persistent reconnection loop exited.")
                 self.connecting = False # Ensure connecting is false if loop exits without success

        elif self.explicit_disconnect:
            if self.debug: print("[Enroll DEBUG] Explicit disconnect processed. No auto-reconnect.")
            self.explicit_disconnect = False
            self.connecting = False
        else: # self.connecting is True
            if self.debug: print("[Enroll DEBUG] Disconnected, but another connection attempt is in progress.")


    def on_subscribe(self, client, userdata, mid, granted_qos, properties=None):
         if self.debug: print(f"[Enroll DEBUG] Subscribed: mid={mid}, Granted QoS/ReasonCode(s)={granted_qos}")

    def on_publish(self, client, userdata, mid):
         if self.debug and mid != 0 : print(f"[Enroll TRACE] Message MID {mid} published.")

    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload_str = msg.payload.decode('utf-8')
            if self.debug: print(f"[Enroll DEBUG] Received msg on '{topic}': {payload_str[:150]}...")

            if topic == MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE:
                try:
                    device_info = json.loads(payload_str)
                    room = device_info.get("Room")
                    target_mac = device_info.get("MacAddress")
                    if room and target_mac:
                        if self.debug: print(f"[Enroll DEBUG] Processing device info: Room='{room}', MAC='{target_mac}'")
                        update_discovered_device(room, target_mac)
                        if self.on_device_info_received:
                            self.on_device_info_received(room, target_mac)
                    elif self.debug:
                        print(f"[Enroll WARN] Incomplete device info received: {device_info}")
                except json.JSONDecodeError:
                    if self.debug: print(f"[Enroll ERROR] JSON decode error for device info: {payload_str[:150]}")
                except Exception as e:
                    if self.debug: print(f"[Enroll ERROR] Error processing device info: {e}")
        except Exception as e:
            if self.debug: print(f"[Enroll ERROR] Unhandled error in on_message: {e}")

    def connect_with_current_token(self):
        if not self.token or not self.username:
             if self.debug: print("[Enroll ERROR] connect_with_current_token: Username or token is missing.")
             self.connecting = False
             return False
        if self._client:
            if self.debug: print("[Enroll DEBUG] connect_with_current_token: Old client instance found. Cleaning up.")
            try:
                self._client.loop_stop(force=True)
            except: pass
            self._client = None

        broker_address = self.mqtt_config.get("broker")
        broker_port_str = self.mqtt_config.get("port", "1883")
        try:
            broker_port = int(broker_port_str)
            if not (0 < broker_port < 65536): raise ValueError("Port out of range")
        except ValueError:
            if self.debug: print(f"[Enroll ERROR] Invalid MQTT port '{broker_port_str}'. Using 1883.")
            broker_port = 1883
            
        if not broker_address:
            if self.debug: print("[Enroll ERROR] Broker address not configured.")
            self.connecting = False
            return False

        if self.debug: print(f"[Enroll INFO] Attempting MQTT connect to {broker_address}:{broker_port} with user '{self.username}'")
        
        try:
            self._client = mqtt.Client(client_id=self.mac, protocol=mqtt.MQTTv5)
            self._client.on_connect = self.on_connect_token
            self._client.on_disconnect = self.on_disconnect
            self._client.on_message = self.on_message
            self._client.on_subscribe = self.on_subscribe
            self._client.on_publish = self.on_publish
            self._client.username_pw_set(self.username, self.token)

            if broker_port == 8883:
                if self.debug: print("[Enroll INFO] Configuring TLS for MQTT (port 8883).")
                self._client.tls_set(cert_reqs=ssl.CERT_NONE)
                self._client.tls_insecure_set(True)
            
            self._client.connect_async(broker_address, broker_port, keepalive=60)
            self._client.loop_start()
            if self.debug: print("[Enroll DEBUG] MQTT client loop_start() called.")
            # self.connecting is still True here, will be set by on_connect_token
            return True 
        except socket.error as se:
             if self.debug: print(f"[Enroll ERROR] Network error during MQTT connect_async: {se}")
             if self._client: 
                 try: self._client.loop_stop(force=True)
                 except: pass
             self._client = None
             self.connecting = False
             if self.on_connection_status_change: self.on_connection_status_change(False)
             return False
        except Exception as e:
            if self.debug: print(f"[Enroll ERROR] Failed to initiate MQTT connection: {e}")
            if self._client:
                try: self._client.loop_stop(force=True)
                except: pass
            self._client = None
            self.connecting = False
            if self.on_connection_status_change: self.on_connection_status_change(False)
            return False

    def on_connect_token(self, client, userdata, flags, rc, properties=None):
        reason_code = rc.value if hasattr(rc, 'value') else rc
        paho_rc_string = str(rc) if hasattr(rc, 'value') else mqtt.connack_string(reason_code)
        
        self.connecting = False # Always reset connecting flag as attempt is over

        if reason_code == 0:
            self.connected = True
            self.explicit_disconnect = False
            if self.debug: print(f"[Enroll INFO] MQTT connected successfully to broker.")
            try:
                 client.subscribe(MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE, qos=0) 
                 if self.debug: print(f"[Enroll INFO] Subscribed to '{MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE}'.")
            except Exception as e_sub:
                 if self.debug: print(f"[Enroll WARN] Failed to subscribe after connect: {e_sub}")

            if self.on_connection_status_change:
                self.on_connection_status_change(True)
        else:
            self.connected = False
            if self.debug: print(f"[Enroll ERROR] MQTT connection failed. RC: {reason_code} ({paho_rc_string})")
            
            if self._client:
                try: self._client.loop_stop(force=True)
                except: pass
            if client == self._client : self._client = None 
            
            if self.on_connection_status_change:
                self.on_connection_status_change(False)
            
            if self.debug: print(f"[Enroll INFO] MQTT connect failed. Persistent reconnect in on_disconnect will handle.")

    def _publish_message_direct(self, topic, payload_obj, qos=0) -> bool:
        if not self.is_actively_connected():
            if self.debug: print(f"[Enroll WARN] MQTT not connected. Cannot send to {topic} directly.")
            return False
        try:
             payload_str = json.dumps(payload_obj, separators=(",", ":"))
        except TypeError as te_json_dump:
             if self.debug: print(f"[Enroll ERROR] JSON dump error for {topic}: {te_json_dump}. Payload: {str(payload_obj)[:100]}...")
             return False
        
        try:
            publish_info = self._client.publish(topic, payload=payload_str, qos=qos)
            
            if publish_info.rc == mqtt.MQTT_ERR_SUCCESS:
                if self.debug: print(f"[Enroll TRACE] Msg (QoS {qos}) directly published to {topic}.")
                return True
            else:
                if self.debug: print(f"[Enroll WARN] MQTT direct publish to {topic} failed (Paho rc={publish_info.rc}).")
                return False
        except Exception as e_publish_runtime:
            if self.debug: print(f"[Enroll ERROR] Runtime exception during MQTT direct publish to {topic}: {e_publish_runtime}.")
            return False

    def send_healthcheck(self):
        if self.is_actively_connected():
            device_time_gmt7 = datetime.now(GMT_PLUS_7).strftime("%Y-%m-%d %H:%M:%S")
            enroll_station_location = self.mqtt_config.get("enroll_station_room", "EnrollmentDesk")
            heartbeat_payload = {
                "MacAddress": self.mac,
                "DeviceTime": device_time_gmt7,
                "Version": ENROLLMENT_STATION_VERSION, 
                "BioAuthType": {"IsFace": True, "IsFinger": True, "IsIdCard": True, "IsIris": False, "Direction": "IN"},
            }
            success = self._publish_message_direct(MQTT_HEALTHCHECK_TOPIC, heartbeat_payload, qos=0)
            if success and self.debug:
                print(f"[Enroll TRACE] Sent healthcheck directly: {str(heartbeat_payload)[:200]}")
            elif not success and self.debug:
                print(f"[Enroll WARN] Failed to send healthcheck directly.")
        elif self.debug:
            print(f"[Enroll TRACE] MQTT not connected, skipping healthcheck send.")