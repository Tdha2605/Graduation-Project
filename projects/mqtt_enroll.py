import json
import time
import ssl
import requests # Import requests here
import socket
import hashlib
import base64
from datetime import datetime, timezone, timedelta
import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
from database_enroll import enqueue_outgoing_message, get_pending_outbox, mark_outbox_sent, update_discovered_device
import os

MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE = "iot/server/{mac_address}/push_biometric"
MQTT_HEALTHCHECK_TOPIC = "iot/devices/healthcheck"
MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE = "iot/devices/device_info"

GMT_PLUS_7 = timezone(timedelta(hours=7))
ENROLLMENT_STATION_VERSION = "20250601" # Version increment

# Simplified reconnect: define a fixed delay for retries after fetching a new token
RECONNECT_DELAY_AFTER_TOKEN_FETCH = 5 # seconds

# AUTH_FAILURE_RETURN_CODES can still be useful for specific logging if needed,
# but the primary action on disconnect will be to fetch a new token anyway.
AUTH_FAILURE_RETURN_CODES = [2, 4, 5]

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
        self.connecting = False # True if a connection attempt (MQTT or HTTP) is in progress

        self.on_connection_status_change = None
        self.on_device_info_received = None

        self.explicit_disconnect = False # Flag for intentional disconnects
        self.reconnect_timer = None # To hold the timer object from EnrollmentApp's root.after

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
        """Clears only the local token/username. Config saving is handled after successful HTTP fetch."""
        if self.debug: print("[Enroll INFO] Clearing local (in-memory) authentication credentials.")
        self.token = None
        self.username = None
        # Do NOT remove from self.mqtt_config here, let retrieve_token_via_http update it.

    def disconnect_client(self, explicit=True):
        if self._client is not None:
            self.explicit_disconnect = explicit
            if self.debug: print(f"[Enroll DEBUG] Disconnecting MQTT client (explicit: {explicit})...")
            try:
                # Cancel any pending reconnect timer
                if self.reconnect_timer is not None and hasattr(self.on_connection_status_change, '__self__'): # Check if callback is bound to EnrollmentApp
                    app_instance = self.on_connection_status_change.__self__ # Get EnrollmentApp instance
                    if hasattr(app_instance, 'root') and app_instance.root:
                         try: app_instance.root.after_cancel(self.reconnect_timer)
                         except: pass # Ignore errors if timer ID invalid
                    self.reconnect_timer = None

                if self._client.is_connected():
                     try: self._client.unsubscribe(MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE)
                     except: pass
                self._client.loop_stop(force=False) # Changed from force=True to force=False for graceful stop
                self._client.disconnect()
                if self.debug: print("[Enroll DEBUG] MQTT client disconnect requested.")
            except Exception as e:
                if self.debug: print(f"[Enroll ERROR] Error during MQTT disconnect: {e}")

            if explicit:
                self._client = None # Nullify the client instance
                self.connected = False # Update connection status
                self.connecting = False # Reset connecting flag
                if self.on_connection_status_change:
                    self.on_connection_status_change(False) # Notify UI or other components

    def attempt_connection_sequence(self):
        """
        Manages the sequence of fetching a new token and then connecting to MQTT.
        This is the method that will be called for reconnection attempts.
        """
        if self.connecting or self.explicit_disconnect: # Avoid concurrent attempts or if explicitly stopped
            if self.debug: print(f"[Enroll TRACE] attempt_connection_sequence skipped: connecting={self.connecting}, explicit_disconnect={self.explicit_disconnect}")
            return

        self.connecting = True # Signal that a connection process is starting

        if not is_connected_to_internet():
            if self.debug: print("[Enroll WARN] No internet access. Cannot attempt connection sequence.")
            if self.on_connection_status_change: self.on_connection_status_change(False)
            self._schedule_reconnect_attempt() # Schedule another attempt later
            self.connecting = False # Reset connecting flag for this path
            return

        if self.debug: print("[Enroll INFO] Starting connection sequence: Fetching new HTTP token.")
        self._clear_local_credentials() # Always clear local credentials before fetching new ones

        if self.retrieve_token_via_http(): # This sets self.token and self.username on success
            if self.debug: print("[Enroll INFO] HTTP token retrieval SUCCEEDED. Proceeding to MQTT connection.")
            if not self.connect_with_current_token(): # Try to connect with the newly fetched token
                if self.debug: print("[Enroll WARN] MQTT connection failed even after fetching a new token.")
                self._schedule_reconnect_attempt() # Schedule another full sequence retry
        else: # retrieve_token_via_http FAILED
            if self.debug: print("[Enroll ERROR] HTTP token retrieval FAILED. Will schedule retry.")
            self._schedule_reconnect_attempt() # Schedule another full sequence retry
            self.connecting = False # Reset connecting flag if HTTP token fails outright

        if not self.is_actively_connected() and (not self._client or (self._client and not self._client.is_connected())):
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
            if self.debug: print(f"[Enroll ERROR][HTTP Token] Invalid http_port '{http_port_str}' in config. Using default 8080.")
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
            if self.debug: print(f"[Enroll ERROR][HTTP Token] HTTP error occurred: {http_err}. Response: {response_text_for_log}")
            return False
        except requests.exceptions.RequestException as req_err:
            if self.debug: print(f"[Enroll ERROR][HTTP Token] Request exception (network/timeout): {req_err}")
            return False
        except json.JSONDecodeError as json_err:
             if self.debug: print(f"[Enroll ERROR][HTTP Token] Failed to decode JSON response. Response text: {response_text_for_log}. Error: {json_err}")
             return False
        except Exception as e:
            if self.debug: print(f"[Enroll ERROR][HTTP Token] Unexpected error during HTTP token request: {e}")
            return False

        if data.get("code") != "OK" or "data" not in data:
            if self.debug: print(f"[Enroll ERROR][HTTP Token] API reports error or has unexpected response structure: {data}")
            return False

        token_data_field = data.get("data", {})
        new_token = token_data_field.get("token")
        new_username = token_data_field.get("username")

        if self.debug:
            token_preview = str(new_token)[:10] + "..." if new_token else "None"
            print(f"[Enroll DEBUG][HTTP Token] Extracted from API response: Username='{new_username}', Token (preview)='{token_preview}'")

        if not new_token or not new_username:
            if self.debug: print(f"[Enroll ERROR][HTTP Token] Required 'token' or 'username' missing in API 'data' field: {token_data_field}")
            return False

        self.token = new_token
        self.username = new_username
        self.mqtt_config["mqtt_username"] = self.username
        self.mqtt_config["mqtt_password"] = self.token
        if self.debug: print(f"[Enroll INFO][HTTP Token] Successfully retrieved and stored new credentials. Username: {self.username}")
        self._save_config()
        return True


    def on_disconnect(self, client, userdata, rc, properties=None):
        reason_code = rc
        if hasattr(rc, 'value'):
            reason_code = rc.value

        if self.debug: print(f"[Enroll DEBUG] MQTT disconnected. Reason code: {reason_code}, Explicit: {self.explicit_disconnect}")

        self.connected = False
        self.connecting = False
        # self._client = None # Per Paho docs, client object persists. Nullify if re-creating.

        if self.on_connection_status_change:
            self.on_connection_status_change(False)

        if not self.explicit_disconnect:
            if self.debug: print(f"[Enroll INFO] Unexpected MQTT disconnect (rc={reason_code}). Scheduling attempt to get new token and reconnect.")
            self._schedule_reconnect_attempt()
        else:
            if self.debug: print("[Enroll DEBUG] Explicit disconnect processed. No auto-reconnect scheduled by this callback.")
            self.explicit_disconnect = False

    def _schedule_reconnect_attempt(self):
        """Schedules a call to attempt_connection_sequence after a delay."""
        if self.connecting or self.explicit_disconnect:
            return

        if self.reconnect_timer is not None and hasattr(self.on_connection_status_change, '__self__'):
            app_instance = self.on_connection_status_change.__self__
            if hasattr(app_instance, 'root') and app_instance.root:
                try: app_instance.root.after_cancel(self.reconnect_timer)
                except: pass
        
        delay_ms = RECONNECT_DELAY_AFTER_TOKEN_FETCH * 1000
        if self.debug: print(f"[Enroll INFO] Scheduling full reconnect sequence (fetch new token then connect) in {RECONNECT_DELAY_AFTER_TOKEN_FETCH} seconds.")
        
        if hasattr(self.on_connection_status_change, '__self__'):
            app_instance = self.on_connection_status_change.__self__
            if hasattr(app_instance, 'root') and app_instance.root and app_instance.root.winfo_exists():
                self.reconnect_timer = app_instance.root.after(delay_ms, self.attempt_connection_sequence)
            else:
                if self.debug: print("[Enroll ERROR] Cannot schedule reconnect: EnrollmentApp root window not available or destroyed.")
        else:
             if self.debug: print("[Enroll ERROR] Cannot schedule reconnect: on_connection_status_change not bound or app instance invalid.")


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
                    if self.debug: print(f"[Enroll ERROR] JSON decode error for device info message: {payload_str[:150]}")
                except Exception as e:
                    if self.debug: print(f"[Enroll ERROR] Error processing device info message: {e}")
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
            return True
        except socket.error as se:
             if self.debug: print(f"[Enroll ERROR] Network error during MQTT connect_async: {se}")
             if self._client: 
                 try: self._client.loop_stop(force=True)
                 except: pass
             self._client = None
             self.connecting = False
             return False
        except Exception as e:
            if self.debug: print(f"[Enroll ERROR] Failed to initiate MQTT connection with Paho client: {e}")
            if self._client:
                try: self._client.loop_stop(force=True)
                except: pass
            self._client = None
            self.connecting = False
            return False

    def on_connect_token(self, client, userdata, flags, rc, properties=None):
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
            if self.debug: print(f"[Enroll INFO] MQTT connected successfully to broker.")
            try:
                 client.subscribe(MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE, qos=0) 
                 if self.debug: print(f"[Enroll INFO] Subscribed to '{MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE}'.")
            except Exception as e_sub:
                 if self.debug: print(f"[Enroll WARN] Failed to subscribe after connect: {e_sub}")

            if self.on_connection_status_change:
                self.on_connection_status_change(True)
            self.flush_outbox()
        else:
            self.connected = False
            if self.debug: print(f"[Enroll ERROR] MQTT connection failed in on_connect_token. RC: {reason_code} ({paho_rc_string})")
            
            if self.debug: print(f"[Enroll INFO] MQTT connect failed (rc={reason_code}). Will schedule to fetch new token and retry.")
            
            if self._client:
                try: self._client.loop_stop(force=True)
                except: pass
            self._client = None 
            
            if self.on_connection_status_change:
                self.on_connection_status_change(False)
            
            if not self.explicit_disconnect:
                 self._schedule_reconnect_attempt()

    def send_healthcheck(self):
        if self.is_actively_connected():
            device_time_gmt7 = datetime.now(GMT_PLUS_7).strftime("%Y-%m-%d %H:%M:%S")
            enroll_station_location = self.mqtt_config.get("enroll_station_room", "EnrollmentDesk")
            heartbeat_payload = {
                "MacAddress": self.mac,
                "DeviceTime": device_time_gmt7,
                "Version": ENROLLMENT_STATION_VERSION, 
                "Room": enroll_station_location,
                "BioAuthType": {"IsFace": True, "IsFinger": True, "IsIdCard": True, "IsIris": False, "Direction": "IN"},
            }
            self._publish_or_queue(MQTT_HEALTHCHECK_TOPIC, heartbeat_payload, qos=0)
            if self.debug: print(f"[Enroll TRACE] Sent healthcheck: {str(heartbeat_payload)[:200]}")

    def publish_enrollment_payload(self, list_of_payload_dicts: list, target_device_mac: str) -> bool:
        # THIS METHOD IS LIKELY NOT USED BY THE NEW HTTP-BASED ENROLLMENT FLOW
        # BUT KEPT FOR "DON'T REMOVE ANYTHING" REQUIREMENT.
        if not isinstance(list_of_payload_dicts, list):
             if self.debug: print("[Enroll ERROR] Invalid enrollment payload: Expected a list of dictionaries.")
             return False 
        if not list_of_payload_dicts:
             if self.debug: print("[Enroll WARN] Enrollment payload list is empty. Nothing to publish.")
             return False

        target_topic = MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE.format(mac_address=target_device_mac)
        if self.debug: print(f"[Enroll INFO] Publishing enrollment data ({len(list_of_payload_dicts)} item(s)) to target device {target_device_mac} on topic: {target_topic} (MQTT - POTENTIALLY UNUSED)")
        
        user_props_for_enroll = [("SenderMac", self.mac), ("TargetMac", target_device_mac)]
        return self._publish_or_queue(
            topic=target_topic,
            payload_obj=list_of_payload_dicts, 
            qos=1,
            user_properties=user_props_for_enroll
        )

    def _publish_or_queue(self, topic, payload_obj, qos=0, user_properties=None) -> bool:
        try:
             payload_str = json.dumps(payload_obj, separators=(",", ":"))
        except TypeError as te_json_dump:
             if self.debug: print(f"[Enroll ERROR] Failed to JSON dump payload for topic {topic}: {te_json_dump}. Payload type: {type(payload_obj)}, Content: {str(payload_obj)[:100]}...")
             return False

        mqtt_publish_properties = None
        user_properties_json_for_db = None
        if user_properties:
             if isinstance(user_properties, list) and all(isinstance(p, tuple) and len(p) == 2 for p in user_properties):
                  mqtt_publish_properties = Properties(PacketTypes.PUBLISH)
                  mqtt_publish_properties.UserProperty = user_properties
                  try: user_properties_json_for_db = json.dumps(user_properties)
                  except TypeError: 
                      if self.debug: print(f"[Enroll WARN] Could not serialize user_properties for DB: {user_properties}")
             
        if self.is_actively_connected():
             try:
                publish_info = self._client.publish(topic, payload=payload_str, qos=qos, properties=mqtt_publish_properties)
                
                if publish_info.rc == mqtt.MQTT_ERR_SUCCESS:
                    if self.debug and qos > 0: print(f"[Enroll TRACE] Message (MID {publish_info.mid}) successfully published to {topic}.")
                    elif self.debug and qos == 0: print(f"[Enroll TRACE] Message (QoS 0) published to {topic}.")
                    return True
                else:
                    if self.debug: print(f"[Enroll WARN] MQTT publish to {topic} failed with Paho rc={publish_info.rc}. Queuing message.")
                    enqueue_outgoing_message(topic, payload_str, qos, user_properties_json_for_db)
                    return False
             except Exception as e_publish_runtime:
                 if self.debug: print(f"[Enroll ERROR] Runtime exception during MQTT publish to {topic}: {e_publish_runtime}. Queuing message.")
                 enqueue_outgoing_message(topic, payload_str, qos, user_properties_json_for_db)
                 return False
        else:
             if self.debug: print(f"[Enroll DEBUG] MQTT not actively connected. Queuing message for topic {topic}.")
             enqueue_outgoing_message(topic, payload_str, qos, user_properties_json_for_db)
             return False

    def flush_outbox(self):
        if not self.is_actively_connected():
            return

        if self.debug: print("[Enroll DEBUG] Checking and flushing MQTT outbox for enrollment station...")
        items_to_send = get_pending_outbox()
        
        if not items_to_send:
            return

        pending_count_initial = len(items_to_send)
        if self.debug: print(f"[Enroll INFO] Outbox: Found {pending_count_initial} messages. Attempting to send.")
        success_count = 0
        
        for entry_id, topic, payload_str_from_db, qos, props_json_from_db in items_to_send:
            if not self.is_actively_connected():
                 if self.debug: print("[Enroll WARN] MQTT disconnected during outbox flush. Stopping further sends from outbox.")
                 break
            
            mqtt_publish_props_from_db = None
            if props_json_from_db:
                try:
                    user_props_list = json.loads(props_json_from_db)
                    if isinstance(user_props_list, list):
                        mqtt_publish_props_from_db = Properties(PacketTypes.PUBLISH)
                        mqtt_publish_props_from_db.UserProperty = user_props_list
                except json.JSONDecodeError:
                     if self.debug: print(f"[Enroll WARN] Failed to decode props_json from outbox for msg id {entry_id}: '{props_json_from_db}'")
            
            try:
                result_info = self._client.publish(topic, payload=payload_str_from_db, qos=qos, properties=mqtt_publish_props_from_db)
                
                if result_info.rc == mqtt.MQTT_ERR_SUCCESS:
                    mark_outbox_sent(entry_id)
                    success_count +=1
                    if self.debug: print(f"[Enroll TRACE] Sent queued msg id {entry_id} (MID: {result_info.mid if qos > 0 else 'N/A'}) to {topic} and marked as sent.")
                else:
                    if self.debug: print(f"[Enroll WARN] Failed to publish queued msg id {entry_id} (Paho rc={result_info.rc}). Stopping flush.")
                    break
            except Exception as e_flush_publish:
                if self.debug: print(f"[Enroll ERROR] Exception publishing queued msg id {entry_id}: {e_flush_publish}. Stopping flush.")
                break
        
        if self.debug and pending_count_initial > 0:
            print(f"[Enroll INFO] Outbox flush complete: {success_count} out of {pending_count_initial} messages sent.")