import json
import time
import ssl
import requests
import socket
import hashlib
import base64
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
from database_enroll import enqueue_outgoing_message, get_pending_outbox, mark_outbox_sent

MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE = "iot/server/push_biometric/{mac_address}"
MQTT_REGISTER_TOPIC = "iot/devices/register_device"
MQTT_REGISTER_RESPONSE_TOPIC = "iot/server/register_device_resp"
MQTT_HEALTHCHECK_TOPIC = "iot/devices/healthcheck"

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
        print(f"[Enroll WARN] Error checking internet connection: {e}")
        return False

class MQTTEnrollManager:
    def __init__(self, mqtt_config, enroll_mac, debug=True):
        self.mqtt_config = mqtt_config
        self.mac = enroll_mac
        self.username = None
        self.token = None
        self._client = None
        self.connected = False
        self.connecting = False
        self.debug = debug
        self.on_token_received = None
        self.on_connection_status_change = None

    @property
    def client(self):
        return self._client

    def disconnect_client(self):
        if self._client is not None:
            if self.debug: print("[Enroll DEBUG] Disconnecting MQTT client...")
            try:
                if self.connected and MQTT_REGISTER_RESPONSE_TOPIC:
                    try:
                        self._client.unsubscribe(MQTT_REGISTER_RESPONSE_TOPIC)
                        print(f"[Enroll INFO] Unsubscribed from MQTT topics.")
                    except Exception as e:
                        print(f"[Enroll WARN] Error unsubscribing from topics: {e}")

                self._client.loop_stop()
                self._client.disconnect()
                if self.debug: print("[Enroll DEBUG] MQTT client disconnect requested.")
            except Exception as e:
                if self.debug: print("[Enroll DEBUG] Error during MQTT disconnect:", e)
            finally:
                self._client = None
                self.connected = False
                self.connecting = False
                if self.on_connection_status_change:
                    self.on_connection_status_change(False)

    def connect_and_register(self):
        if not self.retrieve_token_via_http():
            print("[Enroll ERROR] Cannot retrieve MQTT token via HTTP.")
            return False
        return self.connect_with_token()

    def retrieve_token_via_http(self) -> bool:
        domain = self.mqtt_config.get('domain')
        http_port = self.mqtt_config.get('http_port', 8080)

        if domain:
            base = domain.rstrip('/')
            if not (domain.startswith("http://") and ':' in domain.split('//')[1]) and \
               not (domain.startswith("https://") and ':' in domain.split('//')[1]):
                if domain.startswith("http://") and http_port != 80:
                    base = f"http://{domain.split('//')[1]}:{http_port}"
                elif domain.startswith("https://") and http_port != 443:
                     base = f"https://{domain.split('//')[1]}:{http_port}"
        else:
            broker = self.mqtt_config.get('broker')
            if not broker:
                print("[Enroll ERROR] Neither 'domain' nor 'broker' configured for token HTTP request.")
                return False
            scheme = "http"
            base = f"{scheme}://{broker}:{http_port}"

        url = f"{base}/api/devicecomm/getmqtttoken"
        print(f"[Enroll DEBUG] Requesting token from: {url}")

        payload = {
            "macAddress": self.mac,
            "password": generate_hashed_password(self.mac)
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            print(f"[Enroll DEBUG] HTTP Status Code: {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.SSLError as ssl_err:
             print(f"[Enroll ERROR] SSL Error connecting to {url}. Check certificate or use http. Error: {ssl_err}")
             return False
        except requests.exceptions.RequestException as e:
            print(f"[Enroll ERROR] HTTP token request failed: {e}")
            return False
        except json.JSONDecodeError as json_e:
             print(f"[Enroll ERROR] Failed to decode JSON response from token API. Response: {resp.text[:500]}. Error: {json_e}")
             return False
        except Exception as e:
            print(f"[Enroll ERROR] Unexpected error during HTTP token request: {e}")
            return False


        if data.get("code") != "OK" or "data" not in data:
            print(f"[Enroll ERROR] Unexpected response structure from token API: {data}")
            return False

        token = data["data"].get("token")
        username = data["data"].get("username")
        if not token or not username:
            print(f"[Enroll ERROR] Missing token/username in API response data: {data.get('data')}")
            return False

        self.token = token
        self.username = username
        self.mqtt_config["mqtt_username"] = username
        self.mqtt_config["mqtt_password"] = token
        if self.debug:
            print(f"[Enroll DEBUG] Retrieved token via HTTP. username={username}, token={token[:10]}...")
        return True

    def on_disconnect(self, client, userdata, rc, properties=None):
        self.connected = False
        self.connecting = False
        if self.debug: print(f"[Enroll DEBUG] MQTT disconnected. Reason code: {rc}")
        if self.on_connection_status_change:
            self.on_connection_status_change(False)

    def on_subscribe(self, client, userdata, mid, granted_qos, properties=None):
         if self.debug: print(f"[Enroll DEBUG] Subscribed: mid={mid}, QoS={granted_qos}")

    def on_publish(self, client, userdata, mid):
         pass

    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            if self.debug: print(f"[Enroll DEBUG] Received message on topic '{topic}': {payload[:100]}...")

            if topic == MQTT_REGISTER_RESPONSE_TOPIC:
                try:
                    data = json.loads(payload)
                    if data.get("MacAddress", "").lower() != self.mac.lower():
                        if self.debug: print("[Enroll DEBUG] MAC mismatch in register response. Ignoring.")
                        return
                    token = data.get("AccessToken")
                    username = data.get("Username")
                    if token:
                        self.token = token
                        self.username = username or self.mac
                        self.mqtt_config["mqtt_username"] = self.username
                        self.mqtt_config["mqtt_password"] = self.token
                        if self.debug: print(f"[Enroll DEBUG] Registration via MQTT successful, received token: {token[:10]}...")
                        if self.on_token_received:
                            self.on_token_received(token)
                        else:
                             print("[Enroll WARN] on_token_received callback not set. Attempting direct reconnect.")
                             self.connect_with_token()
                    else:
                        if self.debug: print("[Enroll DEBUG] Register response OK, but no AccessToken found.")
                except json.JSONDecodeError:
                    if self.debug: print("[Enroll DEBUG] Failed to decode JSON from register response.")
                except Exception as e:
                    if self.debug: print(f"[Enroll DEBUG] Error processing register response: {e}")

        except Exception as e:
            if self.debug: print(f"[Enroll ERROR] Unhandled error in on_message: {e}")

    def connect_with_token(self):
        if self.connecting or self.connected:
            if self.debug: print(f"[Enroll DEBUG] Connection attempt skipped. connecting={self.connecting}, connected={self.connected}")
            return False
        username = self.mqtt_config.get("mqtt_username")
        token = self.mqtt_config.get("mqtt_password")

        if not token or not username:
             print("[Enroll ERROR] Missing username or token for MQTT connection.")
             return False

        if not is_connected():
            print("[Enroll WARN] No internet connection.")
            return False

        self.disconnect_client()

        print(f"[Enroll INFO] Attempting MQTT connection to {self.mqtt_config.get('broker')}:{self.mqtt_config.get('port')} with user '{username}'")
        self.connecting = True
        self.connected = False

        try:
            self._client = mqtt.Client(client_id=self.mac, protocol=mqtt.MQTTv5)
            self._client.on_connect = self.on_connect_token
            self._client.on_disconnect = self.on_disconnect
            self._client.on_message = self.on_message
            self._client.on_subscribe = self.on_subscribe
            self._client.on_publish = self.on_publish

            self._client.username_pw_set(username, token)

            broker_port = self.mqtt_config.get("port", 1883)
            if broker_port == 8883:
                print("[Enroll INFO] Configuring TLS for port 8883.")
                self._client.tls_set(cert_reqs=ssl.CERT_NONE)
                self._client.tls_insecure_set(True)

            broker_address = self.mqtt_config.get("broker", "")
            if not broker_address:
                print("[Enroll ERROR] Broker address not configured.")
                self.connecting = False
                return False

            self._client.connect_async(broker_address, broker_port, keepalive=60)
            self._client.loop_start()
            print("[Enroll DEBUG] MQTT loop started.")
            return True

        except Exception as e:
            print(f"[Enroll ERROR] Failed to initiate MQTT connection: {e}")
            self.connecting = False
            self._client = None
            return False

    def on_connect_token(self, client, userdata, flags, rc, properties):
        self.connecting = False
        if rc == 0:
            self.connected = True
            print(f"[Enroll INFO] MQTT connected successfully to broker.")
            if self.on_connection_status_change:
                self.on_connection_status_change(True)
            self.flush_outbox()
        else:
            self.connected = False
            print(f"[Enroll ERROR] MQTT connection failed with token. Return code: {rc}")
            if rc == 5:
                print("[Enroll ERROR] MQTT Authorization Failed (rc=5). Token might be invalid or expired.")
                self.token = None
                self.mqtt_config.pop("mqtt_username", None)
                self.mqtt_config.pop("mqtt_password", None)
                if self.on_token_received:
                    self.on_token_received(None)
            if self.on_connection_status_change:
                self.on_connection_status_change(False)

    def send_healthcheck(self):
        if self._client and self.token and self.connected:
            device_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            heartbeat = {
                "MacAddress": self.mac,
                "Token": self.token,
                "DeviceTime": device_time,
                "DeviceType": "Enrollment"
            }
            props = Properties(PacketTypes.PUBLISH)
            props.UserProperty = [("MacAddress", self.mac)]

            self._publish_or_queue(
                MQTT_HEALTHCHECK_TOPIC,
                heartbeat, # Send dict directly, _publish_or_queue will dump
                qos=0,
                user_properties=[("MacAddress", self.mac)]
            )

    def publish_enrollment_payload(self, payload_list: list, target_mac: str):
        if not isinstance(payload_list, list):
             print("[Enroll ERROR] Invalid enrollment payload: Expected a list.")
             raise ValueError("Payload must be a list.")

        target_topic = MQTT_PUSH_BIOMETRIC_TOPIC_TEMPLATE.format(mac_address=target_mac)
        print(f"[Enroll INFO] Publishing enrollment data to topic: {target_topic}")

        payload_str = json.dumps(payload_list, separators=(",", ":"))

        props = Properties(PacketTypes.PUBLISH)
        props.UserProperty = [("SenderMac", self.mac), ("TargetMac", target_mac)]

        self._publish_or_queue(
            target_topic,
            payload_str,
            qos=1,
            user_properties=props.UserProperty
        )

    def _publish_or_queue(self, topic, payload, qos=0, user_properties=None):
         # Ensure payload is string before queueing/publishing if it's a dict (like healthcheck)
         if isinstance(payload, dict):
              payload_str = json.dumps(payload, separators=(",", ":"))
         elif isinstance(payload, str):
              payload_str = payload
         else:
              print(f"[Enroll ERROR] Invalid payload type for topic {topic}: {type(payload)}. Must be dict or str.")
              return

         props = None
         if user_properties:
             if isinstance(user_properties, list) and all(isinstance(p, tuple) and len(p) == 2 for p in user_properties):
                  props = Properties(PacketTypes.PUBLISH)
                  props.UserProperty = user_properties
             else:
                  print("[Enroll WARN] Invalid user_properties format, ignoring.")

         if self.connected and self._client:
             try:
                 result_info = self._client.publish(topic, payload=payload_str, qos=qos, properties=props)
                 if result_info.rc == mqtt.MQTT_ERR_SUCCESS:
                     if self.debug and qos > 0: print(f"[Enroll DEBUG] Message MID {result_info.mid} published directly to {topic}.")
                     elif self.debug: print(f"[Enroll DEBUG] Message published directly to {topic}.")
                 else:
                     print(f"[Enroll WARN] MQTT publish failed (rc={result_info.rc}) even when connected. Queuing message.")
                     enqueue_outgoing_message(topic, payload_str, qos, user_properties)

             except Exception as e:
                 print(f"[Enroll ERROR] Exception during MQTT publish: {e}. Queuing message.")
                 enqueue_outgoing_message(topic, payload_str, qos, user_properties)
         else:
             if self.debug: print(f"[Enroll DEBUG] MQTT not connected. Queuing message for topic {topic}.")
             enqueue_outgoing_message(topic, payload_str, qos, user_properties)


    def flush_outbox(self):
        if not self.connected or not self._client:
            return

        if self.debug: print("[Enroll DEBUG] Flushing MQTT outbox...")
        pending_count = 0
        success_count = 0
        fail_count = 0
        items = get_pending_outbox()
        pending_count = len(items)

        for entry_id, topic, payload, qos, props_json in items:
            props = None
            up = None
            if props_json:
                try:
                    up = json.loads(props_json)
                    if isinstance(up, list):
                        props = Properties(PacketTypes.PUBLISH)
                        props.UserProperty = up
                    else:
                        print(f"[Enroll WARN] Invalid props_json format in outbox id {entry_id}, ignoring properties.")
                except json.JSONDecodeError:
                    print(f"[Enroll WARN] Failed to decode props_json in outbox id {entry_id}, ignoring properties.")

            try:
                result_info = self._client.publish(topic, payload=payload, qos=qos, properties=props)

                if result_info.rc == mqtt.MQTT_ERR_SUCCESS:
                    mark_outbox_sent(entry_id)
                    success_count += 1
                    if self.debug: print(f"[Enroll DEBUG] Sent queued message id {entry_id} (MID: {result_info.mid}) to {topic}.")
                else:
                    print(f"[Enroll WARN] Failed to publish queued message id {entry_id} (rc={result_info.rc}). Stopping flush.")
                    fail_count += 1
                    break

            except Exception as e:
                print(f"[Enroll ERROR] Exception publishing queued message id {entry_id}: {e}. Stopping flush.")
                fail_count += 1
                break

        if pending_count > 0:
             print(f"[Enroll INFO] Outbox flush finished. Total: {pending_count}, Sent: {success_count}, Failed/Remaining: {pending_count - success_count}")