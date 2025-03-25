import os
import base64
import json
import paho.mqtt.client as mqtt

# Thư mục lưu ảnh
SAVE_DIR = "/home/pi/faces"

def on_connect(client, userdata, flags, rc):
    print("Kết nối MQTT thành công")
    client.subscribe("device/pi123/add_user")

def decode_base64_padded(data):
    # Thêm padding nếu thiếu
    missing_padding = len(data) % 4
    if missing_padding:
        data += '=' * (4 - missing_padding)
    return base64.b64decode(data)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        user_id = payload["user_id"]
        images = payload["images"]  # Danh sách ảnh: [{filename, data(base64)}]

        user_folder = os.path.join(SAVE_DIR, user_id)
        os.makedirs(user_folder, exist_ok=True)

        for image in images:
            filename = image["filename"]
            b64_data = image["data"]
            save_path = os.path.join(user_folder, filename)

            with open(save_path, "wb") as f:
                f.write(decode_base64_padded(b64_data))
        
        print(f"[✔] Đã lưu {len(images)} ảnh cho user {user_id}")

    except Exception as e:
        print("[Lỗi xử lý]:", e)

# Cấu hình MQTT client
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect("test.mosquitto.org", 1883)
client.loop_forever()
