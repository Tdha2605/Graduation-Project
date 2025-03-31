# mqtt.py
import json
import time
import ssl
import socket
import hashlib
import base64
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes

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
    except Exception:
        return False

class MQTTManager:
    def __init__(self, mqtt_config, mac, debug=False):
        self.mqtt_config = mqtt_config
        self.mac = mac
        self.token = None
        self._client = None  # internal client reference
        self.connected = False
        self.debug = debug
        self.on_token_received = None  
        self.on_connection_status_change = None  

    @property
    def client(self):
        return self._client

    @client.setter
    def client(self, value):
        self._client = value

    def disconnect_client(self):
        """Disconnect the current MQTT client if one exists."""
        try:
            if self._client is not None:
                if self.debug:
                    print("[DEBUG] Disconnecting MQTT client...")
                try:
                    self._client.unsubscribe(MQTT_REGISTER_RESPONSE_TOPIC)
                except Exception:
                    pass
                self._client.loop_stop()
                self._client.disconnect()
                if self.debug:
                    print("[DEBUG] MQTT client disconnected.")
                self._client = None
                self.connected = False
        except Exception as e:
            if self.debug:
                print("[DEBUG] Error during disconnect:", e)

    def connect_and_register(self):
        if not is_connected():
            if self.debug:
                print("[DEBUG] No internet connection. Skipping MQTT registration.")
            return False
        try:
            self.connected = False
            self._client = mqtt.Client(protocol=mqtt.MQTTv5)
            self._client.on_connect = self.on_connect
            self._client.on_message = self.on_message
            self._client.on_subscribe = self.on_subscribe
            self._client.on_publish = self.on_publish

            username = self.mqtt_config.get("mqtt_username", "")
            password = self.mqtt_config.get("mqtt_password", "")
            self._client.username_pw_set(username, password)

            if self.mqtt_config.get("port") == 8883:
                self._client.tls_set(cert_reqs=ssl.CERT_NONE)
                self._client.tls_insecure_set(True)

            self._client.connect_async(
                self.mqtt_config.get("broker", ""),
                self.mqtt_config.get("port", 1883),
                60
            )
            self._client.loop_start()
            if self.debug:
                print("[DEBUG] Connecting to MQTT broker for registration...")
            return True
        except Exception as e:
            if self.debug:
                print("[DEBUG] MQTT connection error:", e)
            return False

    def on_connect(self, client, userdata, flags, rc, properties):
        if self.debug:
            print("[DEBUG] on_connect called with rc =", rc)
        if rc == 0:
            self.connected = True
            if self.debug:
                print("[DEBUG] MQTT connection established successfully.")
            client.subscribe(MQTT_REGISTER_RESPONSE_TOPIC)
            if self.token is None:
                props = Properties(PacketTypes.PUBLISH)
                props.UserProperty = [("MacAddress", self.mac)]
                payload = json.dumps({
                    "MacAddress": self.mac,
                    "HashedPassword": generate_hashed_password(self.mac)
                }, separators=(",", ":"))
                if self.debug:
                    print("[DEBUG] Publishing registration payload:", payload)
                client.publish(MQTT_REGISTER_TOPIC, payload=payload, properties=props)
        else:
            self.connected = False
            if self.debug:
                print("[DEBUG] MQTT connection failed with rc =", rc)
            if self.on_connection_status_change:
                self.on_connection_status_change(False)

    def on_subscribe(self, client, userdata, mid, granted_qos, properties=None):
        if self.debug:
            print("[DEBUG] Subscribed: mid =", mid, "granted_qos =", granted_qos)

    def on_publish(self, client, userdata, mid, *args, **kwargs):
        if self.debug:
            print("[DEBUG] Published: mid =", mid)

    def on_message(self, client, userdata, msg):
        payload = msg.payload.decode()
        if self.debug:
            print("[DEBUG] Received message on topic:", msg.topic, "Payload:", payload)
        if msg.topic == MQTT_REGISTER_RESPONSE_TOPIC:
            try:
                data = json.loads(payload)
                if data.get("MacAddress", "").lower() != self.mac.lower():
                    if self.debug:
                        print("[DEBUG] MAC address mismatch in registration response.")
                    return
                token = data.get("AccessToken", None)
                if token:
                    self.token = token
                    if self.debug:
                        print("[DEBUG] Registration successful, received token:", token)
                    if self.on_token_received:
                        self.on_token_received(token)
                    # Stop the registration client.
                    client.loop_stop()
                    client.disconnect()
                    self._client = None
                    self.connected = False
                else:
                    if self.debug:
                        print("[DEBUG] Token not received in registration response.")
            except Exception as e:
                if self.debug:
                    print("[DEBUG] Error parsing registration response:", e)

    def connect_with_token(self):
        try:
            if self._client is not None:
                self.disconnect_client()
            self.connected = False
            self._client = mqtt.Client(protocol=mqtt.MQTTv5)
            self._client.on_connect = self.on_connect_token
            self._client.on_subscribe = self.on_subscribe
            self._client.on_publish = self.on_publish

            self._client.username_pw_set(self.mac, self.token)
            if self.mqtt_config.get("port") == 8883:
                self._client.tls_set(cert_reqs=ssl.CERT_NONE)
                self._client.tls_insecure_set(True)
            if self.debug:
                print("[DEBUG] Reconnecting with token credentials: username =", self.mac, "password =", self.token)
            self._client.connect_async(
                self.mqtt_config.get("broker", ""),
                self.mqtt_config.get("port", 8883),
                60
            )
            self._client.loop_start()
            return True
        except Exception as e:
            if self.debug:
                print("[DEBUG] Error connecting with token:", e)
            return False

    def on_connect_token(self, client, userdata, flags, rc, properties):
        if self.debug:
            print("[DEBUG] on_connect (token) called with rc =", rc)
        if rc == 0:
            self.connected = True
            self._client = client  # Ensure client reference is saved.
            if self.debug:
                print("[DEBUG] MQTT connection with token established successfully.")
            if self.on_connection_status_change:
                self.on_connection_status_change(True)
        else:
            self.connected = False
            if self.debug:
                print("[DEBUG] MQTT token connection failed with rc =", rc)
            if self.on_connection_status_change:
                self.on_connection_status_change(False)

    def send_healthcheck(self):
        if self._client and self.token and self.connected:
            device_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            heartbeat = {"MacAddress": self.mac, "Token": self.token, "DeviceTime": device_time}
            props = Properties(PacketTypes.PUBLISH)
            props.UserProperty = [("MacAddress", self.mac)]
            payload = json.dumps(heartbeat, separators=(",", ":"))
            if self.debug:
                print("[DEBUG] Publishing healthcheck payload:", payload)
            self._client.publish(MQTT_HEALTHCHECK_TOPIC, payload=payload, properties=props)
        else:
            if self.debug:
                print("[DEBUG] Cannot send healthcheck, client not connected or missing token. connected:",
                      self.connected, "client:", self._client, "token:", self.token)
