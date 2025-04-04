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
import os
from insightface.app import FaceAnalysis
from sklearn.metrics.pairwise import cosine_similarity
import cv2

MQTT_REGISTER_TOPIC = "iot/devices/register_device"
MQTT_REGISTER_RESPONSE_TOPIC = "iot/server/register_device_resp"
MQTT_HEALTHCHECK_TOPIC = "iot/devices/healthcheck"
MQTT_REGISTER_FACE_TOPIC = "iot/devices/register_face"
MQTT_RECOGNITION_FACE_TOPIC = "iot/devices/recognition_face"

#face_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
#face_app.prepare(ctx_id=0)

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
        self._client = None
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
            self._client = mqtt.Client(client_id=self.mac, protocol=mqtt.MQTTv5)
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
                keepalive=3600
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
            client.subscribe(MQTT_REGISTER_RESPONSE_TOPIC, qos=1)
            if self.token is None:
                props = Properties(PacketTypes.PUBLISH)
                props.UserProperty = [("MacAddress", self.mac)]
                payload = json.dumps({
                    "MacAddress": self.mac,
                    "HashedPassword": generate_hashed_password(self.mac)
                }, separators=(",", ":"))
                if self.debug:
                    print("[DEBUG] Publishing registration payload:", payload)
                client.publish(MQTT_REGISTER_TOPIC, payload=payload, properties=props, qos=1)
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
        elif msg.topic == MQTT_REGISTER_FACE_TOPIC:
            try:
                data = json.loads(payload)
                mac = data.get("MAC")
                user_id = data.get("user_ID")
                name_field = data.get("name")
                biometric_type = data.get("biometric_type", "").lower()
                image_base64 = data.get("image_base64")
                start_time_field = data.get("start_time")
                end_time_field = data.get("end_time")
                if not (isinstance(user_id, str) and len(user_id) == 12 and user_id.isdigit()):
                    if self.debug:
                        print("Invalid user_ID:", user_id)
                    return
                if biometric_type == "face" and image_base64 and start_time_field and end_time_field and name_field:
                    embedding = self.process_face_embedding(image_base64)
                    if embedding is not None:
                        filename = f"{name_field}_{user_id}_{start_time_field}_{end_time_field}.npy"
                        output_dir_emb = os.path.join("guest", "embeddings")
                        os.makedirs(output_dir_emb, exist_ok=True)
                        filepath_emb = os.path.join(output_dir_emb, filename)
                        np.save(filepath_emb, embedding)
                        if self.debug:
                            print(f"Saved embedding for user {user_id} at {filepath_emb}")
                        image_data = base64.b64decode(image_base64)
                        output_dir_img = os.path.join("guest", "images")
                        os.makedirs(output_dir_img, exist_ok=True)
                        img_filename = f"{name_field}_{user_id}_{start_time_field}_{end_time_field}.jpg"
                        filepath_img = os.path.join(output_dir_img, img_filename)
                        with open(filepath_img, "wb") as f:
                            f.write(image_data)
                        if self.debug:
                            print(f"Saved image for user {user_id} at {filepath_img}")
                    else:
                        if self.debug:
                            print("Face not detected in registration message")
                else:
                    if self.debug:
                        print("Unsupported biometric type or missing fields:", biometric_type)
            except Exception as e:
                if self.debug:
                    print("Error processing registration message:", e)

    def process_face_embedding(self, image_base64):
        try:
            image_data = base64.b64decode(image_base64)
            nparr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            faces = face_app.get(img)
            if faces:
                return faces[0].embedding
        except Exception as e:
            if self.debug:
                print("Error processing face embedding:", e)
        return None

    def connect_with_token(self):
        try:
            if self._client is not None:
                self.disconnect_client()
            self.connected = False
            self._client = mqtt.Client(client_id=self.mac, protocol=mqtt.MQTTv5)
            self._client.on_connect = self.on_connect_token
            self._client.on_message = self.on_message
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
                keepalive=3600
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
            self._client = client
            if self.debug:
                print("[DEBUG] MQTT connection with token established successfully.")
            client.subscribe(MQTT_REGISTER_FACE_TOPIC, qos=1)
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
            self._client.publish(MQTT_HEALTHCHECK_TOPIC, payload=payload, properties=props, qos=1)
        else:
            if self.debug:
                print("[DEBUG] Cannot send healthcheck, client not connected or missing token.")

    def send_recognition_success(self, name):
        device_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = json.dumps({"MAC": self.mac, "name": name, "DeviceTime": device_time}, separators=(",", ":"))
        if self._client and self.connected:
            self._client.publish(MQTT_RECOGNITION_FACE_TOPIC, payload=payload, qos=1)
            if self.debug:
                print("[DEBUG] Published recognition success:", payload)
        else:
            if self.debug:
                print("[DEBUG] MQTT client not ready to publish recognition success.")

