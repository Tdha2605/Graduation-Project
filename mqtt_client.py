import paho.mqtt.client as mqtt
import json
import config
import uuid
import socket

client = mqtt.Client()
client.connect(config.MQTT_BROKER, config.MQTT_PORT, 60)

def get_ip():
    return socket.gethostbyname(socket.gethostname())

def send_register(mac_address):
    msg = {
        "type": "register",
        "mac_address": mac_address,
        "device_name": "DoorPi",
        "ip": get_ip()
    }
    client.publish("access_control/register", json.dumps(msg))
