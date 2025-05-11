import json
import time
import ssl
import requests
import socket
import hashlib
import base64
from datetime import datetime, timezone, timedelta # Added timedelta
import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
from database_enroll import enqueue_outgoing_message, get_pending_outbox, mark_outbox_sent, update_discovered_device # Added update_discovered_device
import os

MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE = "iot/server/{mac_address}/push_biometric"
# MQTT_REGISTER_TOPIC = "iot/devices/register_device" # Kept as per "not remove anything"
# MQTT_REGISTER_RESPONSE_TOPIC = "iot/server/register_device_resp" # Kept
MQTT_HEALTHCHECK_TOPIC = "iot/devices/healthcheck"
MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE = "iot/devices/device_info" # For dynamic room list

GMT_PLUS_7 = timezone(timedelta(hours=7))
ENROLLMENT_STATION_VERSION = "1.0.2" # Version for this enrollment station software

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
        # print(f"[Enroll WARN] Error checking internet connection: {e}") # Silenced for brevity if needed
        return False

class MQTTEnrollManager:
    def __init__(self, mqtt_config, enroll_mac, config_file_path, debug=True):
        self.mqtt_config = mqtt_config
        self.mac = enroll_mac # MAC of this enrollment station
        self.config_file_path = config_file_path
        self.debug = debug

        self.username = self.mqtt_config.get("mqtt_username")
        self.token = self.mqtt_config.get("mqtt_password") # Using 'mqtt_password' from its own config as token

        self._client = None
        self.connected = False
        self.connecting = False
        # self.on_token_received = None # Kept, if used for MQTT-based token updates for itself
        self.on_connection_status_change = None
        self.on_device_info_received = None # Callback to EnrollmentApp to update UI with rooms

    @property
    def client(self):
        return self._client

    def _save_config(self):
        try:
            with open(self.config_file_path, "w") as f:
                json.dump(self.mqtt_config, f, indent=2)
            if self.debug: print(f"[Enroll DEBUG] Saved updated config to {self.config_file_path}")
        except Exception as e:
            if self.debug: print(f"[Enroll ERROR] Failed to save config file {self.config_file_path}: {e}")

    def disconnect_client(self):
        if self._client is not None:
            if self.debug: print("[Enroll DEBUG] Disconnecting MQTT client...")
            try:
                if self.connected:
                    # Unsubscribe from topics this client was subscribed to
                    try:
                        self._client.unsubscribe(MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE)
                        if self.debug: print(f"[Enroll DEBUG] Unsubscribed from {MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE}")
                    except Exception as e:
                        if self.debug: print(f"[Enroll WARN] Error unsubscribing from {MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE}: {e}")
                    # If it subscribed to MQTT_REGISTER_RESPONSE_TOPIC, unsubscribe here too
                    # if MQTT_REGISTER_RESPONSE_TOPIC: # Check if defined, though it is globally
                    #    try:
                    #        self._client.unsubscribe(MQTT_REGISTER_RESPONSE_TOPIC)
                    #        if self.debug: print(f"[Enroll DEBUG] Unsubscribed from {MQTT_REGISTER_RESPONSE_TOPIC}")
                    #    except Exception as e:
                    #        if self.debug: print(f"[Enroll WARN] Error unsubscribing from {MQTT_REGISTER_RESPONSE_TOPIC}: {e}")


                self._client.loop_stop() # Stop the network loop
                self._client.disconnect() # Send DISCONNECT to broker
                if self.debug: print("[Enroll DEBUG] MQTT client disconnect requested and processed.")
            except Exception as e:
                if self.debug: print(f"[Enroll DEBUG] Error during MQTT disconnect: {e}")
            finally:
                self._client = None # Important to set to None
                self.connected = False
                self.connecting = False
                if self.on_connection_status_change:
                    self.on_connection_status_change(False)

    def initialize_connection(self): # For this enrollment station to connect itself
        if self.token and self.username:
            if self.debug: print("[Enroll INFO] Found existing token in config. Attempting direct connection...")
            return self.connect_with_token()
        else:
            if self.debug: print("[Enroll INFO] No token found in config for enrollment station. Attempting HTTP retrieval...")
            if self.retrieve_token_via_http(): # Gets token for THIS enrollment station
                return self.connect_with_token()
            else:
                if self.debug: print("[Enroll ERROR] Failed to retrieve token via HTTP for enrollment station. Cannot connect.")
                if self.on_connection_status_change:
                    self.on_connection_status_change(False)
                return False

    def retrieve_token_via_http(self) -> bool: # For THIS enrollment station
        # domain = self.mqtt_config.get('domain') # 'domain' key might not be used if 'broker' is API server
        http_port = self.mqtt_config.get('http_port', 8080)
        api_server_host = self.mqtt_config.get('broker') # Assuming 'broker' is the host for the API

        if not api_server_host:
             if self.debug: print("[Enroll ERROR] 'broker' (for API server) not configured for token HTTP request.")
             return False
        
        base = api_server_host.rstrip('/')
        if not base.startswith(('http://', 'https://')): # Add scheme if missing
            base = f"http://{base}" # Default to http for API if not specified

        url = f"{base}:{http_port}/api/devicecomm/getmqtttoken"
        if self.debug: print(f"[Enroll DEBUG] Requesting token for enrollment station from: {url}")

        payload = {"macAddress": self.mac, "password": generate_hashed_password(self.mac)}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if self.debug: print(f"[Enroll DEBUG] HTTP Status Code for token request: {resp.status_code}")
            resp.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            data = resp.json()
        except requests.exceptions.HTTPError as http_err:
            if self.debug: print(f"[Enroll ERROR] HTTP error during token request to {url}: {http_err}. Response: {resp.text[:200]}")
            return False
        except requests.exceptions.RequestException as e:
            if self.debug: print(f"[Enroll ERROR] HTTP token request (network/other) failed to {url}: {e}")
            return False
        # Removed specific SSLError as HTTP is assumed if scheme isn't https
        except json.JSONDecodeError as json_e:
             if self.debug: print(f"[Enroll ERROR] Failed to decode JSON from token API. Response: {resp.text[:200]}. Error: {json_e}")
             return False
        except Exception as e: # Catch any other unexpected errors
            if self.debug: print(f"[Enroll ERROR] Unexpected error during HTTP token request: {e}")
            return False

        if data.get("code") != "OK" or "data" not in data:
            if self.debug: print(f"[Enroll ERROR] Unexpected response structure from token API: {data}")
            return False

        token = data["data"].get("token")
        # Username from server should be this enrollment station's MAC
        username = data["data"].get("username")
        if not token or not username:
            if self.debug: print(f"[Enroll ERROR] Missing token/username in API response data: {data.get('data')}")
            return False

        self.token = token
        self.username = username # Should match self.mac if server follows convention
        self.mqtt_config["mqtt_username"] = self.username
        self.mqtt_config["mqtt_password"] = self.token # Storing token as 'mqtt_password'
        if self.debug: print(f"[Enroll DEBUG] Retrieved token via HTTP for enrollment station. Username={self.username}")
        self._save_config() # Save new credentials
        return True

    def on_disconnect(self, client, userdata, rc, properties=None):
        self.connected = False
        self.connecting = False
        if self.debug: print(f"[Enroll DEBUG] MQTT disconnected. Reason code: {rc if rc is not None else 'None (client-initiated or abrupt)'}")
        if self.on_connection_status_change:
            self.on_connection_status_change(False)

    def on_subscribe(self, client, userdata, mid, granted_qos, properties=None):
         if self.debug: print(f"[Enroll DEBUG] Subscribed: mid={mid}, QoS(s)={granted_qos}")

    def on_publish(self, client, userdata, mid):
         if self.debug and mid != 0 : print(f"[Enroll DEBUG] Message MID {mid} published.") # MID 0 for QoS 0

    def on_message(self, client, userdata, msg): # For THIS enrollment station
        try:
            topic = msg.topic
            payload_str = msg.payload.decode('utf-8')
            if self.debug: print(f"[Enroll DEBUG] Received message on topic '{topic}': {payload_str[:150]}...")

            if topic == MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE: # Messages from Access Control Devices
                try:
                    device_info = json.loads(payload_str)
                    room = device_info.get("Room")
                    target_mac = device_info.get("MacAddress") # MAC of the reporting access control device
                    if room and target_mac:
                        if self.debug: print(f"[Enroll DEBUG] Processing device info: Room='{room}', MAC='{target_mac}'")
                        update_discovered_device(room, target_mac) # Update local DB
                        if self.on_device_info_received: # Callback to EnrollmentApp UI
                            self.on_device_info_received(room, target_mac)
                    elif self.debug:
                        print(f"[Enroll WARN] Incomplete device info received: {device_info}")
                except json.JSONDecodeError:
                    if self.debug: print(f"[Enroll ERROR] JSON decode error for device info message: {payload_str[:150]}")
                except Exception as e:
                    if self.debug: print(f"[Enroll ERROR] Error processing device info message: {e}")
            
            # elif topic == MQTT_REGISTER_RESPONSE_TOPIC: # For its own token, if MQTT registration is used
            #     # This part is from your original code, kept as per "not remove anything"
            #     # It suggests the enrollment station might also get its token via an MQTT-based registration flow
            #     try:
            #         data = json.loads(payload_str)
            #         if data.get("MacAddress", "").lower() != self.mac.lower(): # Check if for THIS station
            #             if self.debug: print("[Enroll DEBUG] MAC mismatch in register response. Ignoring.")
            #             return
            #         token = data.get("AccessToken")
            #         username = data.get("Username")
            #         if token: # If server sends a new token for this enrollment station
            #             self.token = token
            #             self.username = username or self.mac # Use provided username or its own MAC
            #             self.mqtt_config["mqtt_username"] = self.username
            #             self.mqtt_config["mqtt_password"] = self.token # Update stored token
            #             if self.debug: print(f"[Enroll DEBUG] Token for enrollment station updated via MQTT response: {token[:10]}...")
            #             self._save_config()
            #         # else:
            #             # if self.debug: print("[Enroll DEBUG] Register response for self OK, but no AccessToken found.")
            #     except json.JSONDecodeError:
            #         if self.debug: print("[Enroll DEBUG] Failed to decode JSON from its own register response.")
            #     except Exception as e:
            #         if self.debug: print(f"[Enroll DEBUG] Error processing its own register response: {e}")

        except Exception as e:
            if self.debug: print(f"[Enroll ERROR] Unhandled error in on_message: {e}")

    def connect_with_token(self): # For THIS enrollment station
        if self.connecting or self.connected:
            return self.connected
        
        username_to_connect = self.username
        token_to_connect = self.token

        if not token_to_connect or not username_to_connect:
             if self.debug: print("[Enroll ERROR] Missing username or token for MQTT connection.")
             return False

        if not is_connected():
            if self.debug: print("[Enroll WARN] No internet connection for enrollment station.")
            if self.on_connection_status_change: self.on_connection_status_change(False)
            return False

        self.disconnect_client() # Clean previous state

        broker_address = self.mqtt_config.get("broker")
        broker_port = self.mqtt_config.get("port", 1883) # Default MQTT port
        if not broker_address:
            if self.debug: print("[Enroll ERROR] Broker address not configured for enrollment station.")
            return False

        if self.debug: print(f"[Enroll INFO] Attempting MQTT connection to {broker_address}:{broker_port} with user '{username_to_connect}' for enrollment station")
        self.connecting = True
        self.connected = False # Reset before attempt

        try:
            self._client = mqtt.Client(client_id=self.mac, protocol=mqtt.MQTTv5) # Use its own MAC as client ID
            self._client.on_connect = self.on_connect_token
            self._client.on_disconnect = self.on_disconnect
            self._client.on_message = self.on_message
            self._client.on_subscribe = self.on_subscribe
            self._client.on_publish = self.on_publish

            self._client.username_pw_set(username_to_connect, token_to_connect)

            if broker_port == 8883: # Standard secure MQTT port
                if self.debug: print("[Enroll INFO] Configuring TLS for MQTT connection (port 8883).")
                # For simplicity in many environments; for production, use proper CA certs
                self._client.tls_set(cert_reqs=ssl.CERT_NONE) 
                self._client.tls_insecure_set(True) # Allows self-signed certs, NOT FOR PRODUCTION ideally
            
            # Remove tls_insecure from config if not port 8883 (as per your original logic)
            # elif "tls_insecure" in self.mqtt_config:
            #      self.mqtt_config.pop("tls_insecure")
            #      self._save_config()

            self._client.connect_async(broker_address, broker_port, keepalive=60)
            self._client.loop_start()
            if self.debug: print("[Enroll DEBUG] MQTT loop started for enrollment station.")
            return True # Indicates connection attempt initiated

        except Exception as e:
            if self.debug: print(f"[Enroll ERROR] Failed to initiate MQTT connection for enrollment station: {e}")
            self.connecting = False
            self._client = None
            if self.on_connection_status_change: self.on_connection_status_change(False)
            return False

    def on_connect_token(self, client, userdata, flags, rc, properties): # For THIS enrollment station
        self.connecting = False
        if rc == 0:
            self.connected = True
            if self.debug: print(f"[Enroll INFO] Enrollment station MQTT connected successfully to broker.")
            try:
                 # Subscribe to the topic where access control devices publish their info
                 client.subscribe(MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE, qos=0) # QoS 0 is fine for discovery
                 if self.debug: print(f"[Enroll INFO] Subscribed to {MQTT_DEVICE_INFO_TOPIC_SUBSCRIBE} for room discovery.")
                 
                 # If MQTT_REGISTER_RESPONSE_TOPIC is still relevant for this station's own token updates:
                 # client.subscribe(MQTT_REGISTER_RESPONSE_TOPIC, qos=1)
                 # if self.debug: print(f"[Enroll INFO] Subscribed to {MQTT_REGISTER_RESPONSE_TOPIC} for self-registration if used.")

            except Exception as e:
                 if self.debug: print(f"[Enroll WARN] Failed to subscribe after connect: {e}")

            if self.on_connection_status_change:
                self.on_connection_status_change(True)
            self.flush_outbox() # Flush its own queued messages
        else:
            self.connected = False
            if self.debug: print(f"[Enroll ERROR] Enrollment station MQTT connection failed. Return code: {rc}")
            if rc == 5: # Authorization failed for enrollment station itself
                if self.debug: print("[Enroll ERROR] MQTT Authorization Failed (rc=5) for enrollment station. Token might be invalid.")
                self.token = None # Clear invalid token
                self.username = None
                self.mqtt_config.pop("mqtt_username", None)
                self.mqtt_config.pop("mqtt_password", None) # Key used for token
                self._save_config()
                if self.debug: print("[Enroll INFO] Cleared invalid token. Attempting to retrieve a new token via HTTP for enrollment station...")
                time.sleep(3) # Brief pause before retry
                self.initialize_connection() # This will try HTTP token retrieval

            if self.on_connection_status_change:
                self.on_connection_status_change(False)

    def send_healthcheck(self): # For THIS enrollment station
        if self._client and self.token and self.connected:
            device_time_gmt7 = datetime.now(GMT_PLUS_7).isoformat(timespec='seconds')
            # Get the enrollment station's own configured room/location, if any
            enroll_station_location = self.mqtt_config.get("enroll_station_room", "EnrollmentDesk") 

            heartbeat = {
                "MacAddress": self.mac, # Enrollment station's MAC
                "DeviceTime": device_time_gmt7,
                "Version": ENROLLMENT_STATION_VERSION, 
                "Room": enroll_station_location, # Location of this enrollment station
                "BioAuthType": {"IsFace": True, "IsFinger": True, "IsIdCard": False, "IsIris": False}, # Indicates what it CAN enroll
                "Direction": "N/A" # Not applicable for enrollment station in access control sense
            }
            # The original code sent "Token" and "DeviceType", adjust if your server expects that for enrollment station healthcheck
            # If strictly matching access control device healthcheck (which doesn't send its own token in healthcheck):
            # heartbeat.pop("Token", None) # If token not needed in healthcheck
            # heartbeat.pop("DeviceType", None) # If not needed
            
            # Using _publish_or_queue for consistency, though healthchecks are often QoS 0 fire-and-forget
            self._publish_or_queue(MQTT_HEALTHCHECK_TOPIC, heartbeat, qos=0)
            if self.debug: print(f"[Enroll DEBUG] Sent healthcheck: {heartbeat}")

    def publish_enrollment_payload(self, payload_list: list, target_mac: str) -> bool:
        if not isinstance(payload_list, list):
             if self.debug: print("[Enroll ERROR] Invalid enrollment payload: Expected a list.")
             return False # Indicate failure

        target_topic = MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE.format(mac_address=target_mac)
        if self.debug: print(f"[Enroll INFO] Publishing enrollment data to target topic: {target_topic}")
        
        props_list = [("SenderMac", self.mac), ("TargetMac", target_mac)]

        return self._publish_or_queue(
            target_topic,
            payload_list, # Pass the list of dicts (payload_obj for _publish_or_queue)
            qos=1,
            user_properties=props_list
        )

    def _publish_or_queue(self, topic, payload_obj, qos=0, user_properties=None) -> bool:
         try:
             # payload_obj can be a dict (for healthcheck) or list of dicts (for enrollment)
             payload_str = json.dumps(payload_obj, separators=(",", ":"))
         except TypeError as te:
             if self.debug: print(f"[Enroll ERROR] Failed to JSON dump payload for topic {topic}: {te}. Payload: {str(payload_obj)[:100]}")
             return False

         props = None
         if user_properties:
             if isinstance(user_properties, list) and all(isinstance(p, tuple) and len(p) == 2 for p in user_properties):
                  props = Properties(PacketTypes.PUBLISH)
                  props.UserProperty = user_properties
             # else:
                  # if self.debug: print("[Enroll WARN] Invalid user_properties format, ignoring.")

         if self.connected and self._client:
             try:
                 if self._client.is_connected():
                    result_info = self._client.publish(topic, payload=payload_str, qos=qos, properties=props)
                    if result_info.rc == mqtt.MQTT_ERR_SUCCESS:
                        if self.debug: print(f"[Enroll DEBUG] Message (MID {result_info.mid if qos > 0 else 'N/A'}) published to {topic}.")
                        return True # Successfully published
                    # else: # Other publish errors like MQTT_ERR_QUEUE_SIZE
                    #     if self.debug: print(f"[Enroll WARN] MQTT publish failed (rc={result_info.rc}) to {topic}. Queuing.")
                    #     enqueue_outgoing_message(topic, payload_str, qos, user_properties) # Ensure it's str
                    #     return False # Published but failed, now queued
                 else: # Fallback if is_connected became false
                     if self.debug: print(f"[Enroll WARN] Client disconnected before publish to {topic}. Queuing.")
                     enqueue_outgoing_message(topic, payload_str, qos, user_properties)
                     return False
             except Exception as e:
                 if self.debug: print(f"[Enroll ERROR] Exception during MQTT publish to {topic}: {e}. Queuing.")
                 enqueue_outgoing_message(topic, payload_str, qos, user_properties)
                 return False
         else: # Not connected
             if self.debug: print(f"[Enroll DEBUG] MQTT not connected. Queuing message for topic {topic}.")
             enqueue_outgoing_message(topic, payload_str, qos, user_properties)
             return False # Not connected, message queued

    def flush_outbox(self): # For THIS enrollment station's queued messages
        if not self.connected or not self._client:
            # if self.debug: print("[Enroll DEBUG] Cannot flush outbox: MQTT not connected.")
            return

        if self.debug: print("[Enroll DEBUG] Flushing MQTT outbox for enrollment station...")
        items = get_pending_outbox()
        if not items:
            # if self.debug: print("[Enroll DEBUG] Outbox is empty.")
            return

        pending_count = len(items)
        success_count = 0
        for entry_id, topic, payload_str_from_db, qos, props_json in items: # payload is already string here
            if not self.connected or not self._client or not self._client.is_connected():
                 if self.debug: print("[Enroll WARN] MQTT disconnected during outbox flush. Stopping.")
                 break
            props = None
            if props_json:
                try:
                    up = json.loads(props_json)
                    if isinstance(up, list):
                        props = Properties(PacketTypes.PUBLISH); props.UserProperty = up
                except json.JSONDecodeError:
                    if self.debug: print(f"[Enroll WARN] Failed to decode props_json in outbox id {entry_id}")
            try:
                result_info = self._client.publish(topic, payload=payload_str_from_db, qos=qos, properties=props)
                if result_info.rc == mqtt.MQTT_ERR_SUCCESS:
                    mark_outbox_sent(entry_id)
                    success_count +=1
                    if self.debug: print(f"[Enroll DEBUG] Sent queued msg id {entry_id} (MID: {result_info.mid if qos > 0 else 'N/A'}) to {topic}.")
                # else: # Other publish errors like MQTT_ERR_QUEUE_SIZE
                    # if self.debug: print(f"[Enroll WARN] Failed to publish queued msg id {entry_id} (rc={result_info.rc}). Stopping flush.")
                    # break # Stop on first failure to maintain order
            except Exception as e:
                if self.debug: print(f"[Enroll ERROR] Exception publishing queued msg id {entry_id}: {e}. Stopping flush.")
                break
        if self.debug and pending_count > 0:
            print(f"[Enroll INFO] Outbox flush: {success_count}/{pending_count} sent.")